"""Independent skip, prerequisite, role and six-hour recovery boundaries."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.module_flow import skip_policy
from graspmemoedu.service import LearningService, DomainError
from tests.fixtures import synthetic_course, DeterministicJudge, temporary_test_directory


class ModuleSkipTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        parent, child = synthetic_course(3), synthetic_course(1)
        parent['id'], child['id'] = 'parent', 'child'
        for topic in (parent, child):
            (self.root / (topic['id'] + '.json')).write_text(json.dumps(topic), encoding='utf-8')
        (self.root / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'course', 'title': 'Course', 'topics': [
            {'id': 'parent', 'path': 'parent.json'}, {'id': 'child', 'path': 'child.json', 'prerequisites': ['parent']}]}]}), encoding='utf-8')
        self.course = parent
        settings = Settings(data_dir=self.root, public_api_origin='https://api.test', allowed_origins=('https://site.test',))
        self.app = create_app(settings, LearningService(parent, self.root, DeterministicJudge()))
        self.client = TestClient(self.app, base_url='https://api.test'); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.store = self.app.state.access_store
        self.student, self.headers = self.identity(is_student=True)
        self.admin, self.admin_headers = self.identity(is_admin=True)
        self.other_admin, self.other_headers = self.identity(is_admin=True)
        self.app.state.help_requests.set_default_admin(self.admin)
        self.service = self.learner(self.student)

    def identity(self, **flags):
        with self.store._transaction() as con:
            account = self.store._insert_account(con, 'Synthetic learner', **flags)
        token = self.store.login(account['invitation_code'])['identity_token']
        return account['account']['learner_id'], {'Origin': 'https://site.test', 'X-Learning-Identity': token}

    def learner(self, learner):
        return self.app.state.admin_pauses.service_for(learner, 'parent')

    def post(self, path, body, headers=None):
        return self.client.post('/api/' + path, json=body, headers=headers or self.headers)

    @staticmethod
    def action(state, **extra):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def finish(self, service=None):
        service = service or self.service
        state = service.choose(self.action(service.state(), choice='mastered'))
        result = service.submit(self.action(state, answer='correct'))
        service.process_submission(result['submission_id'])
        return service.advance(self.action(service.state()))

    def skip(self):
        response = self.post('skip?topic_id=parent', self.action(self.service.state()))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def rows(self, sql, args=()):
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            return con.execute(sql, args).fetchall()

    def unlock(self, record, headers=None):
        return self.post('admin/pauses/unlock', {'learner_id': self.student, 'topic_id': 'parent',
            'module_id': record['module_id'], 'pause_event_id': record['event_id']}, headers or self.admin_headers)

    def test_multiple_skips_keep_null_merge_catch_up_and_restore_full_history(self):
        first = self.skip(); second = self.skip()
        self.assertEqual([m['status'] for m in second['modules']], ['skipped', 'skipped', 'in_progress'])
        self.assertIsNone(first['modules'][0]['mastery']); self.assertIsNone(second['modules'][1]['mastery'])
        records = second['skipped_modules']
        self.finish()
        self.assertEqual([r[0] for r in self.rows('SELECT module FROM review_cards WHERE learner=?', (self.student,))], ['module-3'])
        dashboard = self.app.state.dashboard_store.dashboard(self.store.resolve(self.headers['X-Learning-Identity']), 'course')
        self.assertTrue(next(t for t in dashboard['history'] if t['topic_id'] == 'parent')['half_checked'])
        self.assertFalse(next(t for t in dashboard['tasks'] if t['topic_id'] == 'child')['dependency_ready'])
        inbox = self.client.get('/api/admin/pauses', headers=self.admin_headers).json()
        self.assertEqual(len(inbox['items']), 2)
        self.assertEqual(self.unlock(records[0], self.other_headers).status_code, 404)
        for record in records:
            self.assertEqual(self.unlock(record).status_code, 200)
            self.assertEqual(self.unlock(record).status_code, 200)
        dashboard = self.app.state.dashboard_store.dashboard(self.store.resolve(self.headers['X-Learning-Identity']), 'course')
        self.assertEqual(len([t for t in dashboard['tasks'] if t['topic_id'] == 'parent' and t['type'] == 'Lesson']), 1)
        self.assertEqual(self.service.state()['modules'][2]['status'], 'completed')
        self.finish(); state = self.finish()
        self.assertEqual(state['status'], 'completed'); self.assertEqual(state['skipped_modules'], [])
        self.assertEqual(len(self.rows('SELECT module FROM review_cards WHERE learner=?', (self.student,))), 3)
        self.assertEqual(self.client.get('/api/admin/pauses', headers=self.admin_headers).json()['total'], 0)

    def test_downstream_override_is_owned_exact_and_does_not_bypass_review_due(self):
        self.skip(); self.skip(); self.finish()
        events = [r['event_id'] for r in self.service.state()['skipped_modules']]
        body = {'learner_id': self.student, 'topic_id': 'parent', 'allowed': True, 'expected_events': events}
        self.assertEqual(self.post('admin/pauses/skip-policy', body, self.other_headers).status_code, 404)
        self.assertEqual(self.post('admin/pauses/skip-policy', {**body, 'expected_events': ['wrong']}, self.admin_headers).status_code, 409)
        self.assertEqual(self.post('admin/pauses/skip-policy', body, self.admin_headers).status_code, 200)
        policy = self.app.state.dashboard_store.dependency_policy
        access = self.store.resolve(self.headers['X-Learning-Identity'])
        self.assertTrue(policy.is_ready(access, 'child'))
        with self.service._transaction() as con:
            con.execute("UPDATE review_cards SET due_at='2020-01-01T00:00:00+00:00' WHERE learner=?", (self.student,))
        self.assertFalse(policy.is_ready(access, 'child'))
        self.assertEqual(self.post('admin/pauses/skip-policy', {**body, 'allowed': False}, self.admin_headers).status_code, 200)

    def test_only_students_skip_and_all_manual_pause_apis_are_retired(self):
        for flags in ({}, {'is_advanced_learner': True}, {'is_admin': True}):
            learner, headers = self.identity(**flags)
            response = self.post('skip?topic_id=parent', {}, headers)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(response.json()['detail']['message'], '该功能仅对学员开放')
        for path in ('pause', 'review/sessions/any/pause'):
            response = self.post(path, {})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()['detail']['code'], 'manual_pause_removed')
        guest = self.store.create_guest()
        response = self.post('skip?topic_id=parent', {}, {**self.headers, 'X-Learning-Identity': guest['identity_token']})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['detail']['message'], '该功能仅对学员开放')

    def test_pending_old_judgement_is_discarded_and_skip_is_idempotent(self):
        state = self.service.choose(self.action(self.service.state(), choice='mastered'))
        pending = self.service.submit(self.action(state, answer='correct'))
        payload = self.action(pending['state'])
        state = self.service.skip_module(payload)
        self.service.process_submission(pending['submission_id'])
        self.assertEqual(self.service.state()['attempt_id'], state['attempt_id'])
        self.assertEqual(self.rows('SELECT module FROM review_cards WHERE learner=?', (self.student,)), [])
        self.assertEqual(self.service.skip_module(payload), self.service.state())

    def test_six_hour_initial_recovery_survives_restart_and_student_stays_locked(self):
        learner, _ = self.identity(); service = self.learner(learner)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch('graspmemoedu.module_flow.clock', return_value=start):
            with service._transaction() as con:
                state = service._load(con); service._pause(con, state, 'practice_error_limit'); service._save(con, state)
        with patch('graspmemoedu.module_flow.clock', return_value=start):
            previous = service.state()['attempt_id']
        restarted = LearningService(self.course, self.root, DeterministicJudge(), learner_id=learner)
        from graspmemoedu.review import ReviewBackend
        ReviewBackend(restarted)
        with patch('graspmemoedu.module_flow.clock', return_value=start + timedelta(hours=6, microseconds=-1)):
            self.assertEqual(restarted.state()['status'], 'paused')
        with patch('graspmemoedu.module_flow.clock', return_value=start + timedelta(hours=6)):
            state = restarted.state()
            self.assertEqual(state['status'], 'in_progress'); self.assertNotEqual(state['attempt_id'], previous)
            self.assertEqual(state['modules'][0]['practice_error_count'], 0)
        with self.service._transaction() as con:
            state = self.service._load(con); self.service._pause(con, state, 'practice_error_limit'); self.service._save(con, state)
        with patch('graspmemoedu.module_flow.clock', return_value=start + timedelta(days=1000)):
            self.assertEqual(self.service.state()['status'], 'paused')
            self.assertIsNone(self.service.state()['cooldown_until'])

    def test_review_cooldown_preserves_initial_attempt_fsrs_and_queue(self):
        learner, _ = self.identity(); service = self.learner(learner)
        self.finish(service)
        backend = service.review_backend
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        before = self.rows('SELECT module,card_json,due_at,revision FROM review_cards WHERE learner=?', (learner,))
        original = service.state()['attempt_id']
        with patch('graspmemoedu.module_flow.clock', return_value=start):
            with service._transaction() as con:
                backend._pause_topic(con, 'review_practice_error_limit')
        with patch('graspmemoedu.module_flow.clock', return_value=start + timedelta(hours=5)):
            self.assertEqual(service.state()['attempt_id'], original)
            with service._transaction() as con:
                with self.assertRaises(DomainError): backend._guard_topic(con)
        with patch('graspmemoedu.module_flow.clock', return_value=start + timedelta(hours=6)):
            with service._transaction() as con: backend._guard_topic(con)
            self.assertEqual(service.state()['attempt_id'], original)
        self.assertEqual(self.rows('SELECT module,card_json,due_at,revision FROM review_cards WHERE learner=?', (learner,)), before)

    def test_actual_review_errors_resume_a_fresh_round_without_rewinding_shared_queue(self):
        learner, _ = self.identity(); service = self.learner(learner); self.finish(service)
        backend = service.review_backend; current = datetime.now(timezone.utc)
        backend.clock = lambda: current
        with service._transaction() as con:
            con.execute('UPDATE review_cards SET due_at=? WHERE learner=?', (current.isoformat(), learner))
        before = self.rows('SELECT module,card_json,due_at,revision FROM review_cards WHERE learner=?', (learner,))
        initial_attempt = service.state()['attempt_id']
        state = backend.begin('module-1', request_id=str(uuid.uuid4()))
        for index in range(3):
            payload = {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'], 'question_id': state['practice']['id']}
            result = backend.submit(state['session_id'], {**payload, 'answer': 'wrong'})
            backend.process_submission(result['submission_id']); state = backend.state(state['session_id'])
            if index < 2:
                state = backend.advance(state['session_id'], {'request_id': str(uuid.uuid4()),
                    'expected_revision': state['revision'], 'question_id': state['practice']['id']})
        self.assertEqual(state['status'], 'paused'); self.assertIsNone(state['mastery'])
        old_session = state['session_id']
        queues = self.rows('SELECT body FROM practice_queues WHERE learner=?', (learner,))
        self.assertEqual(service.state()['attempt_id'], initial_attempt)
        current += timedelta(hours=6)
        service.resume_due()
        self.assertEqual(self.rows('SELECT body FROM practice_queues WHERE learner=?', (learner,)), queues)
        self.assertEqual(self.rows('SELECT module,card_json,due_at,revision FROM review_cards WHERE learner=?', (learner,)), before)
        state = backend.begin('module-1', request_id=str(uuid.uuid4()))
        self.assertNotEqual(state['session_id'], old_session)
        self.assertEqual(state['practice_answered_count'], 0); self.assertEqual(state['practice_error_count'], 0)
        self.assertEqual(backend.state(old_session)['status'], 'paused')

    def test_skip_unlock_cannot_thaw_a_different_error_pause(self):
        record = self.skip()['skipped_modules'][0]
        with self.service._transaction() as con:
            state = self.service._load(con); self.service._pause(con, state, 'practice_error_limit'); self.service._save(con, state)
        pause = self.service.state()['pause']
        self.assertEqual(self.unlock(record).status_code, 200)
        state = self.service.state(); self.assertEqual(state['status'], 'paused'); self.assertEqual(state['pause'], pause)
        self.assertTrue(state['skipped_modules'][0]['released'])

    def test_all_skipped_topic_history_is_readable_and_version_is_guarded(self):
        self.skip(); self.skip(); self.skip()
        response = self.client.get('/api/tasks/lesson:parent/answers', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['task']['half_checked'])
        with self.service._transaction() as con:
            con.execute("UPDATE topics SET version='obsolete' WHERE learner=? AND topic='parent'", (self.student,))
        record = self.client.get('/api/admin/pauses', headers=self.admin_headers).json()['items'][0]
        self.assertFalse(record['version_current'])
        self.assertEqual(self.unlock({'event_id': record['pause_event_id'], 'module_id': record['module_id']}).status_code, 409)

    def test_guest_reset_cannot_bypass_cooldown_and_legacy_errors_use_original_event_time(self):
        guest = self.store.create_guest(); service = self.learner(guest['access']['learner_id'])
        start = datetime.now(timezone.utc)
        with patch('graspmemoedu.module_flow.clock', return_value=start):
            with service._transaction() as con:
                state = service._load(con); service._pause(con, state, 'practice_error_limit')
                state.pop('cooldown'); state['pause'].pop('resume_at', None)
                con.execute('UPDATE events SET created_at=? WHERE id=?', (start.isoformat(), state['pause']['event_id']))
                service._save(con, state)
            state = service.state()
            self.assertEqual(datetime.fromisoformat(state['cooldown_until']), start + timedelta(hours=6))
            with self.assertRaises(DomainError) as raised: service.reset_progress(self.action(state))
            self.assertEqual(raised.exception.code, 'topic_cooldown')
        with patch('graspmemoedu.module_flow.clock', return_value=start + timedelta(hours=6)):
            self.assertEqual(service.state()['status'], 'in_progress')


if __name__ == '__main__':
    unittest.main()
