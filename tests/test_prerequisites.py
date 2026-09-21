"""Prerequisite delivery acceptance using isolated synthetic learners only."""
from contextlib import closing
import json
import unittest
import uuid

from tests import test_dashboard as fixtures


class PrerequisiteTests(unittest.TestCase):
    origin = fixtures.DashboardAcceptanceTests.origin
    for _name in ('setUp', '_open', '_close', '_restart', '_headers', '_guest',
                  '_get', '_post', '_action', '_complete'):
        locals()[_name] = getattr(fixtures.DashboardAcceptanceTests, _name)

    def dashboard(self):
        return self._get('/api/dashboard?course_id=course-a')

    def due(self, topic, value):
        with closing(self.app.state.service._connect()) as con:
            con.execute('UPDATE review_cards SET due_at=? WHERE learner=? AND topic=?',
                        (value, self.guest['access']['learner_id'], topic))
            con.commit()

    def test_initial_and_partial_learning_are_blocked_without_creating_progress(self):
        self.assertEqual([x['topic_id'] for x in self.dashboard()['tasks']], ['topic-a'])
        self._get('/api/state?topic_id=topic-b', status=409)
        with closing(self.app.state.service._connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM topics WHERE topic=?', ('topic-b',)).fetchone()[0], 0)
        self._action('topic-a', 'choose', choice='mastered')
        self._action('topic-a', 'submit', answer='correct')
        self._action('topic-a', 'continue')
        topic = self.dashboard()['topics'][0]
        self.assertEqual(topic['progress'], 50)
        self._get('/api/state?topic_id=topic-b', status=409)

    def test_due_prerequisite_blocks_existing_lesson_actions_and_restores(self):
        self._complete('topic-a')
        state = self._get('/api/state?topic_id=topic-b')
        self.due('topic-a', '2000-01-01T00:00:00+00:00')
        self.assertNotIn('topic-b', [x['topic_id'] for x in self.dashboard()['tasks']])
        blocked = self._get('/api/state?topic_id=topic-b')
        self.assertFalse(blocked['dependency_ready'])
        self.assertTrue(all(not x['actions'] for x in blocked['steps']))
        self._post('/api/choose?topic_id=topic-b', {
            'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
            'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'],
            'choice': 'mastered'}, status=409)
        self.due('topic-a', '2099-01-01T00:00:00+00:00')
        self.assertTrue(self._get('/api/state?topic_id=topic-b')['dependency_ready'])
        self.assertIn('topic-b', [x['topic_id'] for x in self.dashboard()['tasks']])

    def test_review_begin_and_resume_require_healthy_prerequisites(self):
        self._complete('topic-a')
        self._complete('topic-b')
        self.due('topic-b', '2000-01-01T00:00:00+00:00')
        self.due('topic-a', '2000-01-01T00:00:00+00:00')
        payload = {'request_id': str(uuid.uuid4()), 'module_id': 'module-1'}
        self._post('/api/review/begin?topic_id=topic-b', payload, status=409)
        self.due('topic-a', '2099-01-01T00:00:00+00:00')
        state = self._post('/api/review/begin?topic_id=topic-b', payload)
        self.due('topic-a', '2000-01-01T00:00:00+00:00')
        path = '/api/review/sessions/' + state['session_id']
        read = self._get(path + '?topic_id=topic-b')
        self.assertEqual(read['actions'], [])
        self._post(path + '/submit?topic_id=topic-b', {
            'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
            'question_id': state['practice']['id'], 'answer': 'correct'}, status=409)
        self.assertIn('lesson:topic-b', [x['id'] for x in self.dashboard()['history']])

    def test_active_review_and_pause_block_even_with_future_due(self):
        self._complete('topic-a')
        self.due('topic-a', '2000-01-01T00:00:00+00:00')
        state = self._post('/api/review/begin?topic_id=topic-a', {
            'request_id': str(uuid.uuid4()), 'module_id': 'module-1'})
        self.due('topic-a', '2099-01-01T00:00:00+00:00')
        self._get('/api/state?topic_id=topic-b', status=409)
        learner = self.app.state.learning_services[(self.guest['access']['learner_id'], 'topic-a')]
        learner.review_backend.pause(state['session_id'], request_id=str(uuid.uuid4()))
        self._get('/api/state?topic_id=topic-b', status=409)

    def test_mastery_is_latest_module_average_and_not_an_unlock_threshold(self):
        self._complete('topic-a')
        learner = self.app.state.learning_services[(self.guest['access']['learner_id'], 'topic-a')]
        from datetime import datetime, timezone
        for module, score in [('module-1', 1), ('module-2', 4)]:
            learner.review_backend.record_module_result(module_id=module, mastery=score,
                result_id=str(uuid.uuid4()), completed_at=datetime.now(timezone.utc))
        dashboard = self.dashboard()
        task = next(x for x in dashboard['tasks'] if x['topic_id'] == 'topic-b')
        detail = task['prerequisite_details'][0]
        self.assertEqual(detail['progress'], 100)
        self.assertEqual(detail['mastery'], 2.5)
        self._get('/api/state?topic_id=topic-b')
        other = self._guest()
        self._get('/api/state?topic_id=topic-b', guest=other, status=409)

    def test_transitive_prerequisites_fail_closed(self):
        self._complete('topic-a')
        self._complete('topic-b')
        # Move C into the same synthetic course and chain it after B.
        self.catalog_data['courses'][0]['topics'].append(
            {'id': 'topic-c', 'path': 'topic-c.json', 'prerequisites': ['topic-b']})
        self.catalog_data['courses'][0].pop('units')
        self.catalog_data['courses'].pop()
        (self.root / 'catalog.json').write_text(json.dumps(self.catalog_data), encoding='utf-8')
        self._restart()
        self.due('topic-a', '2000-01-01T00:00:00+00:00')
        self._get('/api/state?topic_id=topic-c', status=409)
        self.due('topic-a', '2099-01-01T00:00:00+00:00')
        self._get('/api/state?topic_id=topic-c')
