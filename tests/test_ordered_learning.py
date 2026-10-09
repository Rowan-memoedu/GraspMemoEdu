"""Example-first learning, Introduction ownership and pure Introduction reading."""
from copy import deepcopy
from contextlib import closing
import unittest
import uuid

from graspmemoedu.service import create_learning_service, DomainError, encode
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

    def test_unmastered_opens_its_guidance_then_existing_example_and_practice_flow(self):
        service = self.service(ordered_topic())
        state = service.state()
        self.assertEqual(active(state)['question_id'], 'module-1-example')
        self.assertEqual(active(state)['actions'], ['mastered', 'unmastered'])
        self.assertEqual(len(state['steps']), 1)
        service.choose(payload(service, choice='unmastered'))
        state = service.advance(payload(service))
        self.assertEqual(active(state)['introduction_id'], 'intro-a')
        self.assertNotIn('intro-b', [s.get('introduction_id') for s in state['steps']])
        state = service.advance(payload(service))
        self.assertEqual(active(state)['kind'], 'example')
        self.assertIsNotNone(active(state)['explanation_html'])
        service.advance(payload(service))
        for index in range(2):
            result = service.submit(payload(service, answer='correct'))
            service.process_submission(result['submission_id'])
            service.advance(payload(service))
        state = service.state()
        self.assertEqual(active(state)['question_id'], 'module-2-example')
        self.assertEqual(active(state)['actions'], ['mastered', 'unmastered'])
        self.assertEqual(state['modules'][0]['status'], 'completed')
        service.choose(payload(service, choice='unmastered'))
        self.assertEqual(active(service.advance(payload(service)))['introduction_id'], 'intro-b')

    def test_skips_follow_example_order_and_unlocked_module_restores_its_own_guidance(self):
        service = self.service(ordered_topic())
        first = service.skip_module(payload(service))
        record = first['skipped_modules'][0]
        self.assertEqual(active(first)['question_id'], 'module-2-example')
        state = service.skip_module(payload(service))
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['reading_status'], 'completed')
        state = service.unlock(service.topic_id, record['module_id'], record['event_id'])
        self.assertEqual(active(state)['question_id'], 'module-1-example')
        self.assertEqual(state['reading_status'], 'in_progress')
        service.choose(payload(service, choice='unmastered'))
        self.assertEqual(active(service.advance(payload(service)))['introduction_id'], 'intro-a')

    def test_mastered_answers_directly_and_wrong_answer_returns_to_its_guidance(self):
        service = self.service(ordered_topic())
        service.choose(payload(service, choice='mastered'))
        self.assertEqual(active(service.state())['phase'], 'answer')
        self.assertFalse(any(s['kind'] == 'introduction' for s in service.state()['steps']))
        result = service.submit(payload(service, answer='wrong'))
        service.process_submission(result['submission_id'])
        self.assertEqual(active(service.advance(payload(service)))['introduction_id'], 'intro-a')

    def test_mastered_correct_advances_between_groups_without_mandatory_guidance(self):
        service = self.service(ordered_topic())
        for question_id in ('module-1-example', 'module-2-example'):
            self.assertEqual(active(service.state())['question_id'], question_id)
            service.choose(payload(service, choice='mastered'))
            result = service.submit(payload(service, answer='correct'))
            service.process_submission(result['submission_id'])
            service.advance(payload(service))
        self.assertEqual(service.state()['status'], 'completed')
        self.assertFalse(any(s['kind'] == 'introduction' for s in service.state()['steps']))

    def test_several_examples_share_preceding_guidance_without_repeating_it(self):
        topic = ordered_topic()
        # Two Examples belong to A; B has no Example and must not become a forced step.
        topic['content_order'] = [topic['content_order'][i] for i in (0, 1, 3, 2)]
        service = self.service(topic)
        service.choose(payload(service, choice='unmastered'))
        self.assertEqual(active(service.advance(payload(service)))['introduction_id'], 'intro-a')
        service.advance(payload(service))
        service.advance(payload(service))
        for _ in range(2):
            result = service.submit(payload(service, answer='correct'))
            service.process_submission(result['submission_id'])
            service.advance(payload(service))
        service.choose(payload(service, choice='unmastered'))
        state = service.advance(payload(service))
        self.assertEqual(active(state)['kind'], 'example')
        self.assertIsNotNone(active(state)['explanation_html'])
        self.assertEqual([s.get('introduction_id') for s in state['steps'] if s['kind'] == 'introduction'], ['intro-a'])

    def test_mastered_first_example_does_not_mark_its_shared_guidance_read(self):
        topic = ordered_topic()
        topic['content_order'] = [topic['content_order'][i] for i in (0, 1, 3, 2)]
        service = self.service(topic)
        service.choose(payload(service, choice='mastered'))
        result = service.submit(payload(service, answer='correct'))
        service.process_submission(result['submission_id'])
        service.advance(payload(service))
        service.choose(payload(service, choice='unmastered'))
        self.assertEqual(active(service.advance(payload(service)))['introduction_id'], 'intro-a')

    def test_late_shared_guidance_exposes_source_order_without_rewriting_progress(self):
        topic = ordered_topic()
        topic['content_order'] = [topic['content_order'][i] for i in (0, 1, 3, 2)]
        service = self.service(topic)
        service.choose(payload(service, choice='mastered'))
        result = service.submit(payload(service, answer='correct'))
        service.process_submission(result['submission_id'])
        service.advance(payload(service))
        service.choose(payload(service, choice='unmastered'))
        state = service.advance(payload(service))
        self.assertEqual([s['kind'] for s in state['steps']], ['example', 'example', 'introduction'])
        self.assertEqual(active(state)['introduction_id'], 'intro-a')
        self.assertEqual(active(state)['module_id'], 'module-2')
        self.assertEqual(state['content_order'], topic['content_order'])
        self.assertFalse(any(s.get('introduction_id') == 'intro-b' for s in state['steps']))
        with service._transaction() as con:
            saved = service._load(con)
            before = encode(saved)
            projected = service._public(saved)
            projected['content_order'].reverse()
            projected['content_order'][0]['id'] = 'not-a-course-section'
            self.assertEqual(encode(saved), before)
            self.assertEqual(service.course['content_order'], topic['content_order'])
            self.assertEqual(encode(service._load(con)), before)
        self.assertEqual(service.state()['content_order'], topic['content_order'])

    def test_pure_introduction_exposes_order_without_creating_unread_steps(self):
        topic = ordered_topic(intro_only=True)
        service = self.service(topic)
        state = service.state()
        self.assertEqual(state['content_order'], topic['content_order'])
        self.assertEqual([s['introduction_id'] for s in state['steps']], ['intro-a'])
        self.assertTrue(all(set(section) == {'kind', 'id'} for section in state['content_order']))

    def legacy_reading(self, service, index=0):
        # A persisted state produced by the released standalone-guidance reader.
        with service._transaction() as con:
            state = service._load(con)
            state.update(attempts=[], steps=[], order_index=index, module_index=-1,
                         attempt_id='old-intro-attempt', active_step_id='old-intro-step', current_step_id='old-intro-step')
            state['attempts'] = [{'id':'old-intro-attempt', 'kind':'introduction', 'module_id':'intro-a',
                'module_index':-1, 'status':'in_progress', 'mastery':None, 'learning_path':'reading',
                'practice_error_count':0, 'practice_answered_count':0, 'practice_target_count':0,
                'example_result':None, 'created_at':'old-created', 'completed_at':None,
                'paused_at':None, 'pause_reason':None}]
            state['steps'] = [{'id':'old-intro-step', 'kind':'introduction', 'phase':'reading',
                'introduction_id':'intro-a', 'module_id':'intro-a', 'attempt_id':'old-intro-attempt',
                'question_id':None, 'feedback':None, 'answer':None}]
            service._save(con, state)
        return state

    def test_legacy_unread_guidance_returns_to_choice_and_keeps_audit_and_stale_guard(self):
        topic = ordered_topic()
        service = self.service(topic)
        old = self.legacy_reading(service)
        old_payload = payload(service)
        service = self.service(topic)
        state = service.state()
        self.assertEqual(active(state)['phase'], 'choice')
        self.assertEqual(len(state['steps']), 1)
        with service._transaction() as con:
            raw = service._load(con)
        self.assertEqual(raw['flow_history'][0]['steps'], old['steps'])
        self.assertEqual(raw['read_introductions'], [])
        with self.assertRaises(DomainError):
            service.advance(old_payload)
        self.assertEqual(self.service(topic).state(), state)

    def test_existing_answered_progress_is_byte_for_byte_unchanged_on_reopen(self):
        topic = ordered_topic()
        service = self.service(topic)
        service.choose(payload(service, choice='mastered'))
        result = service.submit(payload(service, answer='correct'))
        service.process_submission(result['submission_id'])
        with service._transaction() as con:
            before = encode(service._load(con))
        service = self.service(topic)
        with service._transaction() as con:
            self.assertEqual(encode(service._load(con)), before)

    def test_legacy_pause_is_preserved_until_exact_event_unlock(self):
        topic = ordered_topic()
        service = self.service(topic)
        self.legacy_reading(service)
        service.pause({'request_id':str(uuid.uuid4())})
        pause = service.state()['pause']
        service = self.service(topic)
        self.assertEqual(service.state()['pause'], pause)
        service.unlock(topic['id'], pause['module_id'], pause['event_id'])
        self.assertEqual(active(service.state())['phase'], 'choice')
        self.assertFalse(any(s['kind'] == 'introduction' for s in service.state()['steps']))

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

    def test_example_without_preceding_introduction_is_rejected(self):
        topic = ordered_topic()
        topic['content_order'][0:2] = reversed(topic['content_order'][0:2])
        with self.assertRaisesRegex(ValueError, 'follow its Introduction'):
            validate_runtime(topic)
