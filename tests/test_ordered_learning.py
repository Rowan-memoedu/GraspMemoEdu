"""Public-service checks for source ordering and version-aware Introduction reading."""
from copy import deepcopy
from contextlib import closing
import unittest
import uuid

from graspmemoedu.service import create_learning_service, DomainError
from graspmemoedu.topic_runtime import validate_runtime
from tests.fixtures import synthetic_course, temporary_test_directory, DeterministicJudge


def ordered_topic(intro_only=False):
    topic = synthetic_course(0 if intro_only else 2)
    topic.update(topic_format_version=2, topic_kind='introduction' if intro_only else 'lesson',
                 version='artifact-one', content_version='content-one', content_nodes=[], references=[],
                 introductions=[{'id':'intro-a', 'title':'第一段指导', 'html':'<p>先读 A</p>'},
                                {'id':'intro-b', 'title':'第二段指导', 'html':'<p>再读 B</p>'}])
    topic['content_order'] = [{'kind':'introduction', 'id':'intro-a'}]
    if not intro_only:
        topic['content_order'].append({'kind':'example', 'id':'module-1'})
    topic['content_order'].append({'kind':'introduction', 'id':'intro-b'})
    if not intro_only:
        topic['content_order'].append({'kind':'example', 'id':'module-2'})
    return topic


def payload(service, **extra):
    state = service.state()
    return dict(request_id=str(uuid.uuid4()), expected_revision=state['revision'],
                attempt_id=state['attempt_id'], step_id=state['active_step_id'], **extra)


def active(state):
    return next(s for s in state['steps'] if s['id'] == state['active_step_id'])


class OrderedLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.judge = DeterministicJudge()

    def service(self, topic):
        return create_learning_service(deepcopy(topic), self.temp.name, self.judge)

    def finish_reading(self, service):
        while active(service.state())['kind'] == 'introduction':
            service.advance(payload(service))

    def test_source_order_does_not_reveal_later_sections_and_module_rules_are_preserved(self):
        service = self.service(ordered_topic())
        state = service.state()
        self.assertEqual(active(state)['introduction_id'], 'intro-a')
        self.assertEqual(len(state['steps']), 1)
        state = service.advance(payload(service))
        self.assertEqual(active(state)['question_id'], 'module-1-example')
        service.choose(payload(service, choice='unmastered'))
        state = service.advance(payload(service))
        self.assertEqual(active(state)['kind'], 'example')
        self.assertIsNotNone(active(state)['explanation_html'])
        service.advance(payload(service))
        for index in range(2):
            result = service.submit(payload(service, answer='correct'))
            service.process_submission(result['submission_id'])
            service.advance(payload(service))
        state = service.state()
        self.assertEqual(active(state)['introduction_id'], 'intro-b')
        self.assertEqual(state['modules'][0]['status'], 'completed')
        self.assertEqual(state['modules'][1]['status'], 'locked')
        self.assertNotIn('module-2-example', [s['question_id'] for s in state['steps']])
        self.assertEqual(active(service.advance(payload(service)))['question_id'], 'module-2-example')

    def test_introduction_has_completion_without_judging_or_review_cards(self):
        service = self.service(ordered_topic(True))
        first = payload(service)
        service.advance(first)
        revision = service.state()['revision']
        service.advance(first)
        self.assertEqual(service.state()['revision'], revision)
        self.finish_reading(service)
        state = service.state()
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['reading_progress'], 100)
        self.assertEqual(state['modules'], [])
        self.assertEqual(self.judge.calls, [])
        with closing(service._connect()) as con:
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='review_cards'").fetchone())

    def test_updates_preserve_completion_and_only_changed_teaching_content_requires_rereading(self):
        topic = ordered_topic(True)
        service = self.service(topic)
        self.finish_reading(service)
        done = service.state()['completed_at']
        topic['version'] = 'style-only'
        same = self.service(topic)
        self.assertFalse(same.state()['reread_pending'])
        self.assertEqual(same.state()['completed_at'], done)
        topic['version'], topic['content_version'] = 'artifact-two', 'content-two'
        topic['introductions'][0]['html'] = '<p>教学内容已更新</p>'
        changed = self.service(topic)
        state = changed.state()
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['completed_at'], done)
        self.assertTrue(state['reread_pending'])
        self.assertIn('continue', active(state)['actions'])
        with self.assertRaises(DomainError):
            same.advance(payload(changed))
        self.finish_reading(changed)
        self.assertFalse(changed.state()['reread_pending'])
        self.assertEqual(changed.state()['completed_at'], done)
        reloaded = self.service(topic)
        self.assertFalse(reloaded.state()['reread_pending'])

    def test_unfinished_update_is_still_one_initial_task_and_keeps_manual_pause(self):
        topic = ordered_topic(True)
        service = self.service(topic)
        service.pause({'request_id':str(uuid.uuid4())})
        event = service.state()['pause']
        topic['version'], topic['content_version'] = 'new-artifact', 'new-content'
        service = self.service(topic)
        self.assertFalse(service.state()['reread_pending'])
        self.assertEqual(service.state()['pause']['event_id'], event['event_id'])
        self.assertEqual(active(service.state())['actions'], [])
        service.unlock(topic['id'], event['module_id'], event['event_id'])
        self.finish_reading(service)
        self.assertEqual(service.state()['status'], 'completed')

    def test_duplicate_order_and_ordinary_version_overwrite_are_rejected(self):
        topic = ordered_topic()
        self.service(topic)
        topic['version'] = 'new-ordinary-version'
        with self.assertRaises(DomainError):
            self.service(topic)
        topic['content_order'].append(topic['content_order'][0])
        with self.assertRaises(ValueError):
            validate_runtime(topic)
