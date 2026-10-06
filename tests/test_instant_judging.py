"""Mechanical feedback must return on POST even when both AI workers are busy."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from graspmemoedu.question_types import demo_answer
from tests.fixtures import synthetic_course, temporary_test_directory
from tests.question_fixtures import interaction_course
import test_atomic_api as atomic_tests
import test_training_api as training_tests
import test_review_papers as paper_tests


class InstantJudgingTests(unittest.TestCase):
    def start(self, course, judge):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.app = create_app(Settings(data_dir=self.root, public_api_origin='https://instant.test',
                                       allowed_origins=('https://site.test',)),
                              LearningService(course, self.root, judge))
        self.client = TestClient(self.app, base_url='https://instant.test'); self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        self.headers = {'Origin': 'https://site.test'}
        self.guest()

    def call(self, method, path, data=None, status=200):
        response = self.client.request(method, path, headers=self.headers, **({'json': data} if data is not None else {}))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def guest(self):
        access = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = access['identity_token']
        self.learner_id = access['access']['learner_id']

    def payload(self, state, **extra):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def choose(self, state=None):
        state = state or self.call('GET', '/api/state')
        return self.call('POST', '/api/choose', self.payload(state, choice='mastered'))

    def test_exact_post_does_not_wait_for_busy_ai_workers_and_semantic_stays_async(self):
        release = threading.Event(); entered = threading.Event(); lock = threading.Lock(); calls = []
        def judge(*args):
            with lock:
                calls.append(args)
                if len(calls) == 2: entered.set()
            release.wait(20)
            return {'status': 'judged', 'correct': True, 'reason': 'Synthetic semantic answer'}
        course = synthetic_course(2)
        course['modules'][0]['example']['interaction'] = {'version': 1, 'type': 'text', 'grading': 'exact', 'answers': ['correct']}
        self.start(course, judge)
        futures = []; original_submit = ThreadPoolExecutor.submit
        def track(pool, function, *args, **kwargs):
            future = original_submit(pool, function, *args, **kwargs)
            futures.append(future)
            return future
        patcher = patch('graspmemoedu.api.ThreadPoolExecutor.submit', new=track)
        patcher.start(); self.addCleanup(patcher.stop)
        def finish_workers():
            release.set()
            for future in futures: future.result(timeout=5)
        self.addCleanup(finish_workers)
        for _ in range(2):
            state = self.choose()
            result = self.call('POST', '/api/submit', self.payload(state, answer='correct'))
            self.assertEqual(result['status'], 'judged')
            state = self.call('POST', '/api/continue', self.payload(result['state']))
            state = self.choose(state)
            result = self.call('POST', '/api/submit', self.payload(state, answer='semantic answer'))
            self.assertEqual(result['status'], 'pending')
            self.guest()
        self.assertTrue(entered.wait(2), 'Both AI workers must be occupied')
        state = self.choose(); payload = self.payload(state, answer='wrong')
        started = time.monotonic()
        result = self.call('POST', '/api/submit', payload)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(result['status'], 'judged'); self.assertFalse(result['correct'])
        self.assertIsNone(result['state']['pending_submission_id'])
        self.assertEqual(self.call('POST', '/api/submit', payload), result)
        self.assertEqual(len(calls), 2)

    def test_every_mechanical_type_returns_feedback_without_executor_or_poll(self):
        for kind in (0, 1, 2, 3, 4, 5):
            with self.subTest(kind=kind):
                judge = Mock(side_effect=AssertionError('Mechanical questions must not call AI'))
                course = interaction_course(kind if kind < 5 else 0)
                if kind == 5:
                    course['modules'][0]['example']['interaction'] = {'version': 1, 'type': 'choice',
                        'options': [{'id': 'a', 'text': '42'}, {'id': 'b', 'text': '43'}], 'correct_id': 'a'}
                self.start(course, judge)
                state = self.choose()
                payload = self.payload(state, answer=demo_answer(course['modules'][0]['example']))
                with patch('graspmemoedu.api.ThreadPoolExecutor.submit', side_effect=AssertionError('Must not enqueue')):
                    result = self.call('POST', '/api/submit', payload)
                self.assertEqual(result['status'], 'judged'); self.assertTrue(result['correct'])
                judge.assert_not_called()
                # Close each disposable app before the next subtest.
                self.doCleanups()

    def test_review_exact_post_is_judged_and_fsrs_is_applied_only_once(self):
        course = interaction_course(2)
        self.start(course, Mock(side_effect=AssertionError('No AI for exact blanks')))
        self.call('GET', '/api/state'); learner = self.app.state.learning_services[self.learner_id]
        backend = learner.review_backend; when = datetime.now(timezone.utc); backend.clock = lambda: when
        card = backend.record_module_result(module_id='module-1', mastery=4, result_id='synthetic', completed_at=when)
        when = datetime.fromisoformat(card['due_at']) + timedelta(seconds=1)
        state = self.call('POST', '/api/review/begin', {'module_id': 'module-1', 'request_id': str(uuid.uuid4())})
        question = backend.question_for_session(state, state['practice']['id'])
        payload = {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                   'question_id': state['practice']['id'], 'answer': demo_answer(question)}
        path = '/api/review/sessions/' + state['session_id'] + '/submit'
        with patch('graspmemoedu.api.ThreadPoolExecutor.submit', side_effect=AssertionError('Must not enqueue')):
            result = self.call('POST', path, payload)
        self.assertEqual(result['status'], 'judged'); self.assertTrue(result['correct'])
        self.assertEqual(self.call('POST', path, payload), result)


class InstantOtherReadersTests(unittest.TestCase):
    def helper(self, cls):
        helper = cls(methodName='runTest'); helper.setUp(); self.addCleanup(helper.doCleanups)
        return helper

    def test_atomic_exact_returns_feedback_on_submit(self):
        fixture = self.helper(atomic_tests.AtomicAPITests)
        state = fixture.call('GET', '/api/state' + fixture.scope)
        state = fixture.call('POST', '/api/choose' + fixture.scope, fixture.payload(state, choice='learn'))
        for _ in range(10):
            if state['phase'] == 'answer': break
            state = fixture.call('POST', '/api/continue' + fixture.scope, fixture.payload(state))
        backend = fixture.app.state.learning_services[(fixture.learner_id, 'atomic-english')]
        payload = fixture.payload(state, answer=demo_answer(backend.questions[state['current']['card_id']]))
        with patch('graspmemoedu.api.ThreadPoolExecutor.submit', side_effect=AssertionError('Must not enqueue')):
            result = fixture.call('POST', '/api/submit' + fixture.scope, payload)
        self.assertEqual(result['status'], 'judged'); self.assertTrue(result['correct'])

    def test_training_exact_returns_done_on_submit(self):
        fixture = self.helper(training_tests.TrainingAPITests)
        state = fixture.open('exact')
        payload = {'attempt_id': state['attempt_id'], 'request_id': str(uuid.uuid4()), 'answer': '2'}
        with patch('graspmemoedu.api.ThreadPoolExecutor.submit', side_effect=AssertionError('Must not enqueue')):
            result = fixture.call('POST', '/api/training/submit', payload)
        self.assertEqual(result['submission_status'], 'judged'); self.assertEqual(result['phase'], 'done')
        self.assertTrue(result['result']['correct'])
        self.assertEqual(fixture.call('POST', '/api/training/submit', payload)['result'], result['result'])

    def test_mechanical_paper_returns_done_and_mixed_paper_needs_ai(self):
        fixture = self.helper(paper_tests.PaperTests); fixture.prime()
        paper = fixture.draft(fixture.open_paper())
        payload = {'paper_id': paper['id'], 'request_id': str(uuid.uuid4()), 'expected_revision': paper['revision']}
        with patch('graspmemoedu.api.ThreadPoolExecutor.submit', side_effect=AssertionError('Must not enqueue')):
            result = fixture.call('POST', '/api/review-papers/submit', payload)
        self.assertEqual(result['job_status'], 'judged'); self.assertEqual(result['phase'], 'done')
        self.assertEqual(fixture.call('POST', '/api/review-papers/submit', payload)['items'], result['items'])
        fixture.when += timedelta(days=20)
        paper = fixture.draft(fixture.open_paper())
        # A fresh pending job over the pinned snapshots classifies the whole submission.
        backend = paper_tests.PaperService(fixture.service(), fixture.app.state.catalog,
            None, fixture.app.state.access_store.resolve(fixture.headers['X-Learning-Identity']),
            fixture.app.state.review_paper_types, lambda tid: None)
        sid = backend.submit({'paper_id': paper['id'], 'request_id': str(uuid.uuid4()), 'expected_revision': paper['revision']}, 'math')
        self.assertTrue(backend.instant_submission(sid))
        with backend.transaction() as con:
            items = backend._row(con, paper['id'])['items']
            items[0]['question']['interaction'] = {'version': 1, 'type': 'text', 'grading': 'semantic'}
            con.execute('UPDATE review_papers SET items=? WHERE id=?', (json.dumps(items), paper['id']))
        self.assertFalse(backend.instant_submission(sid))
