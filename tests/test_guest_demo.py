"""Opt-in guest answer access; no real accounts, judge calls or production data."""
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from math_learning.api import create_app
from math_learning.config import Settings
from math_learning.service import LearningService
from tests.fixtures import synthetic_course, temporary_test_directory


class GuestDemoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = temporary_test_directory()
        self.addCleanup(self.temporary.cleanup)
        self.course = synthetic_course(2)
        root = Path(self.temporary.name)
        self.calls = []
        def judge(question, reference, answer):
            self.calls.append(answer)
            return {'status': 'judged', 'correct': answer == reference, 'reason': 'fixture'}
        self.app = create_app(Settings(data_dir=root, public_api_origin='https://demo.example'),
                              LearningService(self.course, root, judge))
        self.client = TestClient(self.app, base_url='https://demo.example')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'Origin': 'https://demo.example'}
        self.guest()

    def guest(self):
        result = self.client.post('/api/access/guest', json={}, headers=self.headers).json()
        self.headers['X-Learning-Identity'] = result['identity_token']
        self.access = result['access']

    def state(self):
        return self.client.get('/api/state', headers=self.headers).json()

    def action(self, name, **extra):
        state = self.state()
        response = self.client.post('/api/' + name, headers=self.headers, json={
            'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
            'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_only_current_answer_phase_and_no_state_leak_or_mutation(self):
        self.assertEqual(self.client.get('/api/demo-answer', headers=self.headers).status_code, 409)
        self.action('choose', choice='mastered')
        before = self.state()
        result = self.client.get('/api/demo-answer', headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {'step_id': before['active_step_id'],
            'question_id': self.course['modules'][0]['example']['id'],
            'answer': self.course['modules'][0]['example']['reference_answer']})
        self.assertEqual(self.state(), before)
        self.assertNotIn('REFERENCE_ONLY', str(before))
        self.assertEqual(self.calls, [])

    def test_missing_identity_and_account_role_are_denied(self):
        self.assertEqual(self.client.get('/api/demo-answer', headers={'Origin': 'https://demo.example'}).status_code, 401)
        # Authorization-only stub: no account creation or login is performed.
        with patch.object(self.app.state.access_store, 'resolve', return_value={**self.access, 'role': 'account'}):
            self.assertEqual(self.client.get('/api/demo-answer', headers=self.headers).status_code, 403)

    def test_prefilled_and_edited_wrong_answers_use_normal_judging(self):
        for correct in (True, False):
            self.guest()
            self.action('choose', choice='mastered')
            answer = self.client.get('/api/demo-answer', headers=self.headers).json()['answer']
            submitted = self.action('submit', answer=answer if correct else 'deliberately wrong')
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                result = self.client.get('/api/submissions/' + submitted['submission_id'], headers=self.headers).json()
                if result['status'] != 'pending': break
                time.sleep(.01)
            self.assertEqual(result['status'], 'judged')
            self.assertEqual(result['correct'], correct)
            self.assertEqual(self.client.get('/api/demo-answer', headers=self.headers).status_code, 409)
        self.assertEqual(len(self.calls), 2)
