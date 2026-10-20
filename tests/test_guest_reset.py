"""Guest reset isolation, concurrency and retained audit regression checks."""
from contextlib import closing
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from graspmemoedu.api import Action
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.dashboard import DashboardStore
from graspmemoedu.guest_reset import register_guest_reset_routes
from graspmemoedu.service import DomainError, LearningService
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class GuestResetTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.course = synthetic_course(1)
        self.service = LearningService(self.course, self.root, DeterministicJudge(), 'guest-one')

    def payload(self, service=None, **extra):
        state = (service or self.service).state()
        return dict(request_id=str(uuid.uuid4()), expected_revision=state['revision'],
                    attempt_id=state['attempt_id'], step_id=state['active_step_id'], **extra)

    def complete(self):
        self.service.choose(self.payload(choice='mastered'))
        submission = self.service.submit(self.payload(answer='correct'))
        self.service.process_submission(submission['submission_id'])
        self.service.advance(self.payload())

    def test_reset_completed_is_idempotent_scoped_and_audited(self):
        other = LearningService(self.course, self.root, learner_id='guest-two')
        other_topic = LearningService({**self.course, 'id': 'other-topic'}, self.root, learner_id='guest-one')
        untouched = [other.state(), other_topic.state()]
        self.complete()
        before = self.service.state()
        self.assertEqual(before['status'], 'completed')
        payload = self.payload()
        reset = self.service.reset_progress(payload)
        self.assertEqual(reset['revision'], before['revision'] + 1)
        self.assertNotEqual(reset['attempt_id'], before['attempt_id'])
        self.assertEqual(len(reset['steps']), 1)
        self.assertFalse(reset['introduction_read'])
        self.assertEqual(reset['steps'][0]['phase'], 'choice')
        self.assertEqual(self.service.reset_progress(payload), reset)
        self.assertEqual([other.state(), other_topic.state()], untouched)
        with closing(self.service._connect()) as con:
            events = [json.loads(row['body']) for row in con.execute('SELECT body FROM events')]
            self.assertEqual(len([event for event in events if event['type'] == 'guest_progress_reset']), 1)
            archived = next(event['previous_state'] for event in events if event['type'] == 'guest_progress_reset')
            self.assertEqual(archived['status'], 'completed')
            self.assertEqual(con.execute("SELECT COUNT(*) FROM submissions WHERE status='judged'").fetchone()[0], 1)
        with self.assertRaises(DomainError):
            self.service.choose({**payload, 'request_id': str(uuid.uuid4()), 'choice': 'mastered'})
        with self.assertRaises(DomainError):
            self.service.reset_progress({**payload, 'expected_revision': reset['revision']})

    def test_reset_during_judging_cannot_contaminate_new_generation(self):
        entered, release = threading.Event(), threading.Event()
        def judge(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return {'status': 'judged', 'correct': True, 'reason': 'fixture'}
        self.service.judge = judge
        self.service.choose(self.payload(choice='mastered'))
        submitted = self.service.submit(self.payload(answer='correct'))
        worker = threading.Thread(target=self.service.process_submission, args=(submitted['submission_id'],))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            reset = self.service.reset_progress(self.payload())
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.service.state(), reset)
        self.assertEqual(self.service.submission(submitted['submission_id'])['status'], 'discarded')

    def test_pending_and_paused_resets(self):
        self.service.choose(self.payload(choice='mastered'))
        submission = self.service.submit(self.payload(answer='correct'))
        reset = self.service.reset_progress(self.payload())
        self.service.process_submission(submission['submission_id'])
        self.assertEqual(self.service.state(), reset)
        self.assertEqual(self.service.submission(submission['submission_id'])['status'], 'discarded')
        self.service.pause({'request_id': str(uuid.uuid4())})
        self.assertEqual(self.service.reset_progress(self.payload())['status'], 'in_progress')

    def test_dashboard_old_choices_and_answers_do_not_reappear(self):
        dashboard = DashboardStore(
            Catalog.load(Settings(data_dir=self.root), self.course),
            SimpleNamespace(_connect=self.service._connect, data_dir=self.root, authorize=lambda *args: None))
        access = {'learner_id': 'guest-one', 'features': ['review_history'], 'topics': [{'id': self.course['id']}]}
        self.complete()
        original = dashboard.answers(access, 'lesson:' + self.course['id'])['groups'][0]['answers'][0]['id']
        self.service.reset_progress(self.payload())
        with closing(self.service._connect()) as con:
            snapshot = dashboard._snapshot(con, 'guest-one', self.course['id'])
        self.assertEqual(snapshot['status'], 'not_started')
        self.assertIsNone(snapshot['started_at'])
        self.complete()
        answers = dashboard.answers(access, 'lesson:' + self.course['id'])['groups'][0]['answers']
        self.assertEqual(len(answers), 1)
        self.assertNotEqual(answers[0]['id'], original)

    def test_route_requires_guest_and_learn_permission(self):
        app = FastAPI()
        role, allowed = ['guest'], [True]
        def identity(request, feature):
            self.assertEqual(feature, 'learn')
            if not allowed[0]:
                raise DomainError('feature_forbidden', 'denied', 403)
            return {'role': role[0]}, self.service
        @app.exception_handler(DomainError)
        async def domain_error(request, error):
            return JSONResponse({'code': error.code}, status_code=error.status_code)
        def public_state(state, access, learner):
            self.assertIs(learner, self.service)
            self.assertEqual(access['role'], 'guest')
            return state
        register_guest_reset_routes(app, identity, public_state, Action)
        with TestClient(app) as client:
            role[0] = 'account'
            self.assertEqual(client.post('/api/guest/reset', json=self.payload()).status_code, 403)
            role[0], allowed[0] = 'guest', False
            self.assertEqual(client.post('/api/guest/reset', json=self.payload()).status_code, 403)
            allowed[0] = True
            self.assertEqual(client.post('/api/guest/reset', json=self.payload()).status_code, 200)
