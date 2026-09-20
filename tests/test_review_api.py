"""Website review acceptance using disposable synthetic data only."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
import threading
import unittest
import uuid

from fastapi.testclient import TestClient
from math_learning.api import create_app
from math_learning.config import Settings
from math_learning.service import LearningService
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class ReviewAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.course = synthetic_course()
        self.judge = DeterministicJudge()
        self.app = create_app(Settings(data_dir=self.root, public_api_origin='https://review.test',
                                       allowed_origins=('https://site.test',)),
                              LearningService(self.course, self.root, self.judge))
        self.client = TestClient(self.app, base_url='https://review.test')
        self.client.__enter__()
        self.addCleanup(self.close)
        self.headers = {'Origin': 'https://site.test'}
        guest = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = guest['identity_token']
        self.call('GET', '/api/state')
        self.learner = self.app.state.learning_services[guest['access']['learner_id']]
        self.backend = self.learner.review_backend
        self.time = datetime.now(timezone.utc)
        self.backend.clock = lambda: self.time

    def close(self):
        self.client.__exit__(None, None, None)
        for thread in threading.enumerate():
            if thread.name.startswith('answer-judge'):
                thread.join(5)

    def call(self, method, path, payload=None, status=200):
        response = self.client.request(method, path, headers=self.headers, **({'json': payload} if payload is not None else {}))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def enroll(self):
        item = self.backend.record_module_result(module_id='module-1', mastery=4,
                    result_id=str(uuid.uuid4()), completed_at=self.time)
        self.time = datetime.fromisoformat(item['due_at']) + timedelta(seconds=1)
        return self.call('POST', '/api/review/begin', {'module_id': 'module-1', 'request_id': str(uuid.uuid4())})

    def payload(self, state, **extra):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'question_id': state['practice']['id'], **extra}

    def answer(self, state, correct):
        data = self.payload(state, answer='correct' if correct else 'wrong')
        path = '/api/review/sessions/' + state['session_id'] + '/submit'
        result = self.call('POST', path, data)
        for _ in range(200):
            result = self.call('GET', '/api/review/submissions/' + result['submission_id'])
            if result['status'] != 'pending':
                break
            time.sleep(.01)
        self.assertEqual(result['status'], 'judged')
        self.assertEqual(self.call('POST', path, data)['state']['revision'], result['state']['revision'])
        return result['state'], result['submission_id']

    def test_scores_and_automatic_pause(self):
        for sequence, rating in (([True], 4), ([False, True, True], 2),
                                 ([False, False, True, True, True], 1), ([False, False, False], None)):
            with self.subTest(sequence=sequence):
                state = self.enroll()
                for index, correct in enumerate(sequence):
                    state, _ = self.answer(state, correct)
                    if index < len(sequence) - 1:
                        state = self.call('POST', '/api/review/sessions/' + state['session_id'] + '/continue', self.payload(state))
                self.assertEqual(state['mastery'], rating)
                self.assertEqual(state['status'], 'paused' if rating is None else 'completed')
                self.assertEqual(len(state['answers']), len(sequence))
                self.assertIn('explanation_html', state['answers'][0])
                self.assertNotIn('REFERENCE_ONLY', str(state))
                self.assertNotIn('INTRODUCTION_ONLY', str(state))

    def test_guest_pause_demo_and_identity_boundaries(self):
        state = self.enroll()
        base = '/api/review/sessions/' + state['session_id']
        self.call('POST', base + '/pause', {'request_id': 'pause'}, 403)
        demo = self.call('GET', base + '/demo-answer')
        self.assertEqual(demo['question_id'], state['practice']['id'])
        state, submission = self.answer(state, True)
        self.call('GET', base + '/demo-answer', status=409)
        other = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = other['identity_token']
        self.call('GET', base, status=404)
        self.call('GET', '/api/review/submissions/' + submission, status=404)
        self.call('POST', '/api/review/configure', {}, 403)
        self.call('POST', base + '/unlock', {}, 403)

    def test_shared_pending_limit_blocks_both_directions(self):
        state = self.enroll()
        self.app.state.public_limits.max_pending = 1
        lesson = self.learner.state()
        choice = {'request_id': 'choose', 'expected_revision': lesson['revision'], 'attempt_id': lesson['attempt_id'],
                  'step_id': lesson['active_step_id'], 'choice': 'mastered'}
        lesson = self.learner.choose(choice)
        data = {**choice, 'request_id': 'lesson-pending', 'expected_revision': lesson['revision'], 'answer': 'service-error'}
        data.pop('choice')
        self.learner.submit(data)
        result = self.call('POST', '/api/review/sessions/' + state['session_id'] + '/submit', self.payload(state, answer='correct'), 429)
        self.assertEqual(result['detail']['code'], 'judge_capacity')
        self.learner.process_submission('lesson-pending')
        self.backend.submit(state['session_id'], self.payload(state, answer='correct'))
        data.update(request_id='new-lesson-attempt')
        self.call('POST', '/api/submit', data, 429)

    def test_lesson_completion_enrolls_without_history_backfill(self):
        with closing(self.learner._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_cards').fetchone()[0], 0)
        state = self.learner.state()
        data = {'request_id': 'choice', 'expected_revision': state['revision'], 'attempt_id': state['attempt_id'],
                'step_id': state['active_step_id'], 'choice': 'mastered'}
        state = self.call('POST', '/api/choose', data)
        data.pop('choice')
        data.update(request_id='lesson-answer', expected_revision=state['revision'], answer='correct')
        item = self.call('POST', '/api/submit', data)
        for _ in range(200):
            result = self.call('GET', '/api/submissions/' + item['submission_id'])
            if result['status'] != 'pending':
                break
            time.sleep(.01)
        self.assertEqual(result['status'], 'judged')
        self.assertIsNotNone(self.backend.next_due_at())

    def test_revoked_history_and_topic_authorization(self):
        account = self.app.state.access_store.create_account('Review test', purpose='test')
        login = self.call('POST', '/api/access/login', {'invitation_code': account['invitation_code']})
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.call('GET', '/api/state')
        self.learner = self.app.state.learning_services[login['access']['learner_id']]
        self.backend = self.learner.review_backend
        self.backend.clock = lambda: self.time
        state = self.enroll()
        state, old_submission = self.answer(state, False)
        self.app.state.access_store.set_permissions(login['access']['learner_id'], features=['learn', 'submit_answer', 'pause_topic'])
        state = self.call('POST', '/api/review/sessions/' + state['session_id'] + '/continue', self.payload(state))
        self.assertEqual(state['answers'], [])
        self.call('GET', '/api/review/submissions/' + old_submission, status=403)
        self.app.state.access_store.set_permissions(login['access']['learner_id'], topic_ids=[])
        self.call('GET', '/api/review/sessions/' + state['session_id'], status=403)

    def test_account_manual_pause_is_unscored_and_previous_round_is_history(self):
        account = self.app.state.access_store.create_account('Review pause test', purpose='test')
        login = self.call('POST', '/api/access/login', {'invitation_code': account['invitation_code']})
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.call('GET', '/api/state')
        self.learner = self.app.state.learning_services[login['access']['learner_id']]
        self.backend = self.learner.review_backend
        self.backend.clock = lambda: self.time
        state = self.enroll()
        state, old_submission = self.answer(state, True)
        old_session = state['session_id']
        state = self.enroll()
        self.app.state.access_store.set_permissions(login['access']['learner_id'], features=['learn', 'submit_answer', 'pause_topic'])
        self.call('GET', '/api/review/sessions/' + old_session, status=403)
        self.call('GET', '/api/review/submissions/' + old_submission, status=403)
        with closing(self.learner._connect()) as con:
            before = con.execute('SELECT COUNT(*) FROM review_ratings').fetchone()[0]
        state = self.call('POST', '/api/review/sessions/' + state['session_id'] + '/pause', {'request_id': 'pause-current'})
        self.assertEqual(state['status'], 'paused')
        self.assertIsNone(state['mastery'])
        with closing(self.learner._connect()) as con:
            self.assertEqual(before, con.execute('SELECT COUNT(*) FROM review_ratings').fetchone()[0])

    def test_startup_recovers_interrupted_review_before_accepting_work(self):
        state = self.enroll()
        item = self.backend.submit(state['session_id'], self.payload(state, answer='correct'))
        with self.learner._transaction() as con:
            con.execute("UPDATE review_submissions SET status='running',run_token='dead-worker' WHERE id=?", (item['submission_id'],))
        replacement = create_app(Settings(data_dir=self.root), LearningService(self.course, self.root, self.judge))
        with TestClient(replacement, base_url='http://127.0.0.1:8767'):
            recovered = replacement.state.learning_services[self.learner.learner_id].review_backend
            result = recovered.submission(item['submission_id'])
            self.assertEqual(result['status'], 'error')
            self.assertIsNone(result['state']['pending_submission_id'])
            self.assertEqual(result['state']['practice_error_count'], 0)


if __name__ == '__main__':
    unittest.main()
