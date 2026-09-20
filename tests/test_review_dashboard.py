"""Read-only review projections and guest reset on isolated synthetic data."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import unittest
from unittest.mock import patch
import uuid

from math_learning.access import AccessStore
from math_learning.catalog import Catalog
from math_learning.config import Settings
from math_learning.dashboard import DashboardStore
from math_learning.review import ReviewBackend
from math_learning.service import DomainError, LearningService
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class ReviewDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.topic = synthetic_course(2)
        self.catalog = Catalog.load(Settings(data_dir=self.root), self.topic)
        self.access_store = AccessStore(self.root, [self.topic])
        self.access = self.access_store.create_guest()['access']
        self.other = self.access_store.create_guest()['access']
        self.learning = LearningService(self.topic, self.root, DeterministicJudge(), learner_id=self.access['learner_id'])
        self.store = DashboardStore(self.catalog, self.access_store)
        self.time = datetime.now(timezone.utc)
        self.backend = None

    def reviews(self):
        if self.backend is None:
            self.backend = ReviewBackend(self.learning, clock=lambda: self.time)
        return self.backend

    def enroll(self, module='module-1'):
        result = self.reviews().record_module_result(module_id=module, mastery=4,
                  result_id=str(uuid.uuid4()), completed_at=self.time)
        return datetime.fromisoformat(result['due_at'])

    def dashboard(self, access=None, **kwargs):
        with patch('math_learning.dashboard.now', return_value=self.time.isoformat()):
            return self.store.dashboard(access or self.access, 'arithmetic-revisited', **kwargs)

    def begin(self, module='module-1'):
        return self.backend.begin(module, request_id=str(uuid.uuid4()))

    def submit(self, state, process=True):
        item = self.backend.submit(state['session_id'], {'request_id': str(uuid.uuid4()),
            'expected_revision': state['revision'], 'question_id': state['practice']['id'],
            'answer': 'correct', 'elapsed_ms': 1234})
        if process:
            self.backend.process_submission(item['submission_id'])
        return item

    def reset(self):
        state = self.learning.state()
        return self.learning.reset_progress({'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
            'attempt_id': state['attempt_id'], 'step_id': state['active_step_id']})

    def test_legacy_database_read_does_not_create_progress_or_review_tables(self):
        result = self.dashboard(self.other)
        self.assertIsNone(result['next_review_due_at'])
        self.assertEqual([t['type'] for t in result['tasks']], ['Lesson'])
        with closing(self.learning._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM topics WHERE learner=?', (self.other['learner_id'],)).fetchone()[0], 0)
            self.assertFalse(self.store._has_reviews(con))

    def test_due_boundary_priority_no_content_and_resume_hides_sibling(self):
        due = self.enroll()
        self.enroll('module-2')
        before = self.dashboard()
        self.assertTrue(before['next_review_due_at'])
        self.assertEqual([t['type'] for t in before['tasks']], ['Lesson'])
        self.time = due + timedelta(days=90)
        result = self.dashboard()
        self.assertEqual([t['type'] for t in result['tasks']], ['Review', 'Review', 'Lesson'])
        self.assertNotIn('QUESTION::', json.dumps(result))
        self.assertEqual(result['course']['progress'], 0)
        self.assertIsNone(result['course']['start_date'])
        state = self.begin()
        resumed = DashboardStore(self.catalog, self.access_store)
        with patch('math_learning.dashboard.now', return_value=self.time.isoformat()):
            result = resumed.dashboard(self.access, 'arithmetic-revisited')
        tasks = [task for task in result['tasks'] if task['type'] == 'Review']
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['id'], 'review:' + state['session_id'])
        self.assertIn('?session=', tasks[0]['start_href'])
        self.assertTrue(tasks[0]['started'])
        self.assertIsNone(result['next_review_due_at'])

    def test_due_instant_and_obsolete_content_are_not_early_or_reused(self):
        due = self.enroll()
        self.time = due - timedelta(microseconds=1)
        self.assertEqual([task['type'] for task in self.dashboard()['tasks']], ['Lesson'])
        self.time = due
        self.assertEqual([task['type'] for task in self.dashboard()['tasks']], ['Review', 'Lesson'])
        with self.learning._transaction() as con:
            con.execute('UPDATE review_cards SET version=? WHERE learner=?', ('old-version', self.access['learner_id']))
        result = self.dashboard()
        self.assertEqual([task['type'] for task in result['tasks']], ['Lesson'])
        self.assertIsNone(result['next_review_due_at'])

    def test_history_answers_feedback_and_scoped_pagination(self):
        self.time = self.enroll() + timedelta(days=90)
        first = self.begin()
        self.submit(first)
        self.time = datetime.fromisoformat(self.backend.state(first['session_id'])['due_at'])
        second = self.begin()
        self.submit(second)
        result = self.dashboard(limit=1)
        self.assertTrue(result['has_more'])
        task = result['history'][0]
        self.assertEqual(task['id'], 'review:' + second['session_id'])
        self.assertEqual(task['mastery'], 4)
        next_page = self.dashboard(cursor=result['history_cursor'], limit=1)
        self.assertEqual(next_page['history_revision'], result['history_revision'])
        self.assertEqual(next_page['history'][0]['id'], 'review:' + first['session_id'])
        with self.assertRaises(DomainError):
            self.dashboard(self.other, cursor=result['history_cursor'])
        answers = self.store.answers(self.access, task['id'])
        encoded = json.dumps(answers)
        self.assertNotIn('REFERENCE_ONLY', encoded)
        self.assertNotIn('module-1-example', encoded)
        self.assertEqual(len(answers['groups'][0]['answers']), 1)
        self.store.save_feedback(self.access, {'message': 'question feedback', 'task_id': task['id'],
                                              'question_id': second['practice']['id']})
        with self.assertRaises(DomainError):
            self.store.save_feedback(self.access, {'message': 'locked', 'task_id': task['id'],
                                                  'question_id': 'module-1-example'})
        with self.assertRaises(DomainError):
            self.store.answers(self.other, task['id'])
        with self.assertRaises(DomainError):
            self.store.answers({**self.access, 'topics': []}, task['id'])
        self.assertEqual(self.dashboard(self.other)['history'], [])
        self.assertEqual(self.dashboard({**self.access, 'features': ['learn']})['history'], [])

    def test_paused_topic_stops_push_and_guest_reset_isolated(self):
        self.time = self.enroll() + timedelta(days=90)
        state = self.begin()
        other_learning = LearningService(self.topic, self.root, DeterministicJudge(), learner_id=self.other['learner_id'])
        other_reviews = ReviewBackend(other_learning)
        other_reviews.record_module_result(module_id='module-1', mastery=4, result_id='other', completed_at=self.time)
        self.backend.pause(state['session_id'], request_id=str(uuid.uuid4()))
        result = self.dashboard()
        self.assertEqual([t['type'] for t in result['tasks']], ['Lesson'])
        self.assertEqual(result['tasks'][0]['status'], 'paused')
        self.assertIsNone(result['next_review_due_at'])
        self.reset()
        with closing(self.learning._connect()) as con:
            for table in ('review_cards', 'review_ratings', 'review_sessions', 'review_actions', 'review_submissions',
                          'review_topic_pauses', 'review_unlocks', 'review_optimizations'):
                self.assertEqual(con.execute(f'SELECT COUNT(*) FROM {table} WHERE learner=?', (self.access['learner_id'],)).fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_cards WHERE learner=?', (self.other['learner_id'],)).fetchone()[0], 1)
        self.assertEqual(self.dashboard()['tasks'][0]['status'], 'not_started')
        # Cached backend remains usable after reset without a settings-row gap.
        self.enroll()

    def test_reset_waits_for_pending_review_and_never_loses_the_round(self):
        self.time = self.enroll() + timedelta(days=90)
        state = self.begin()
        item = self.submit(state, process=False)
        with self.assertRaises(DomainError) as error:
            self.reset()
        self.assertEqual(error.exception.code, 'review_submission_pending')
        self.backend.process_submission(item['submission_id'])
        self.assertEqual(self.backend.state(state['session_id'])['status'], 'completed')
        previous_revision = self.dashboard()['history_revision']
        self.reset()
        self.assertEqual(self.dashboard()['history'], [])
        self.assertNotEqual(self.dashboard()['history_revision'], previous_revision)


if __name__ == '__main__':
    unittest.main()
