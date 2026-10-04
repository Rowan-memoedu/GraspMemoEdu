"""Public role/ownership boundary and existing Lesson/Review/atomic unlock behavior."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from tests.fixtures import temporary_test_directory, synthetic_course, DeterministicJudge
from tests.atomic_fixtures import atomic_topic
from graspmemoedu.atomic import AtomicLearningService


class AdminPauseTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.course = synthetic_course(2)
        (self.root / 'course.json').write_text(json.dumps(self.course), encoding='utf-8')
        self.settings = Settings(data_dir=self.root, course_path=self.root / 'course.json',
                                 public_api_origin='https://api.test', allowed_origins=('https://site.test',))
        self.app = create_app(self.settings, LearningService(self.course, self.root, DeterministicJudge()))
        self.client = TestClient(self.app, base_url='https://api.test'); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.store = self.app.state.access_store
        self.admin, self.admin_headers = self.identity(is_admin=True)
        self.other_admin, self.other_headers = self.identity(is_admin=True)
        self.student, self.headers = self.identity(is_student=True)
        self.app.state.help_requests.set_default_admin(self.admin)

    def identity(self, **flags):
        with self.store._transaction() as con:
            account = self.store._insert_account(con, 'Synthetic <student>', **flags)
        token = self.store.login(account['invitation_code'])['identity_token']
        return account['account']['learner_id'], {'Origin': 'https://site.test', 'X-Learning-Identity': token}

    def learner(self, learner_id=None):
        return self.app.state.admin_pauses.service_for(learner_id or self.student, self.course['id'])

    @staticmethod
    def action(state, **values):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **values}

    def pause(self, learner_id=None):
        learner = self.learner(learner_id)
        state = learner.pause(self.action(learner.state()))
        return {'learner_id': learner_id or self.student, 'topic_id': self.course['id'],
                'module_id': state['pause']['module_id'], 'pause_event_id': state['pause']['event_id']}

    def inbox(self, headers=None, suffix=''):
        return self.client.get('/api/admin/pauses' + suffix, headers=headers or self.admin_headers)

    def unlock(self, body, headers=None):
        return self.client.post('/api/admin/pauses/unlock', json=body, headers=headers or self.admin_headers)

    def test_public_roles_ownership_counts_and_read_only_listing(self):
        first = self.pause()
        second, _ = self.identity(is_student=True); self.pause(second)
        self.app.state.help_requests.assign_admin(second, self.other_admin)
        before = self.learner().state()
        result = self.inbox(suffix='?limit=1').json()
        self.assertEqual(result['total'], 1); self.assertIsNone(result['next_offset'])
        self.assertEqual(result['items'][0]['pause_event_id'], first['pause_event_id'])
        self.assertIsNotNone(result['items'][0]['paused_at'])
        self.assertEqual(self.inbox(self.other_headers).json()['items'][0]['learner_id'], second)
        self.assertEqual(self.learner().state(), before)
        for headers in (self.headers, self.identity()[1], {'Origin': 'https://site.test'}):
            self.assertIn(self.inbox(headers).status_code, (401, 403))
            self.assertIn(self.unlock(first, headers).status_code, (401, 403))
        self.assertEqual(self.unlock(first, self.other_headers).status_code, 404)
        text = json.dumps(result)
        for secret in ('reference_answer', 'invitation', 'identity_token', 'REFERENCE_ONLY'):
            self.assertNotIn(secret, text)
        self.assertEqual(self.client.post('/api/unlock', headers=self.admin_headers, json=first).status_code, 403)
        self.assertEqual(self.client.options('/api/admin/pauses/unlock', headers={
            'Origin': 'https://evil.test', 'Access-Control-Request-Method': 'POST'}).status_code, 403)

    def test_unlock_preserves_completed_work_is_idempotent_and_cannot_unlock_new_pause(self):
        learner = self.learner()
        state = learner.choose(self.action(learner.state(), choice='mastered'))
        result = learner.submit(self.action(state, answer='correct'))
        learner.process_submission(result['submission_id'])
        state = learner.submission(result['submission_id'])['state']
        state = learner.advance(self.action(state))
        completed = [m for m in state['modules'] if m['status'] == 'completed']
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            review_cards = con.execute('SELECT * FROM review_cards WHERE learner=?', (self.student,)).fetchall()
        payload = self.pause(); old_attempt = learner.state()['attempt_id']
        self.assertEqual(self.unlock({**payload, 'module_id': 'wrong'}).status_code, 409)
        self.assertEqual(self.unlock(payload).status_code, 200)
        state = learner.state(); self.assertEqual(state['status'], 'in_progress'); self.assertIsNone(state['pause'])
        self.assertNotEqual(state['attempt_id'], old_attempt)
        self.assertEqual([m for m in state['modules'] if m['status'] == 'completed'], completed)
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT * FROM review_cards WHERE learner=?', (self.student,)).fetchall(), review_cards)
        self.assertEqual(self.unlock(payload).status_code, 200)
        self.assertEqual(learner.state(), state)
        new_pause = self.pause()
        self.assertEqual(self.unlock(payload).status_code, 200)
        self.assertEqual(learner.state()['pause']['event_id'], new_pause['pause_event_id'])
        self.assertEqual(self.unlock({**new_pause, 'pause_event_id': 'stale'}).status_code, 409)

    def test_reassignment_and_disabled_students_take_effect_before_unlock(self):
        payload = self.pause()
        self.app.state.help_requests.assign_admin(self.student, self.other_admin)
        self.assertEqual(self.inbox().json()['total'], 0)
        self.assertEqual(self.unlock(payload).status_code, 404)
        self.assertEqual(self.unlock(payload, self.other_headers).status_code, 200)
        payload = self.pause()
        self.store.set_enabled(self.student, False)
        self.assertEqual(self.inbox(self.other_headers).json()['total'], 0)
        self.assertEqual(self.unlock(payload, self.other_headers).status_code, 404)

    def test_old_content_version_does_not_reconcile_or_change_progress(self):
        payload = self.pause()
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            con.execute('UPDATE topics SET version=? WHERE learner=? AND topic=?',
                        ('old', self.student, self.course['id'])); con.commit()
            before = con.execute('SELECT version,body FROM topics WHERE learner=?', (self.student,)).fetchone()
        self.assertFalse(self.inbox().json()['items'][0]['version_current'])
        self.assertEqual(self.unlock(payload).status_code, 409)
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT version,body FROM topics WHERE learner=?', (self.student,)).fetchone(), before)

    def test_review_only_pause_uses_existing_topic_unlock(self):
        learner = self.learner(); backend = learner.review_backend
        with learner._transaction() as con:
            event = backend._event(con, 'review_topic_paused', pause_reason='review_manual_difficulty', mastery=None)
            backend.on_topic_paused(con, event, 'review_manual_difficulty')
        result = self.inbox().json()['items'][0]; self.assertTrue(result['review_only'])
        payload = {k: result[k] for k in ('learner_id', 'topic_id', 'module_id', 'pause_event_id')}
        self.assertEqual(self.unlock(payload).status_code, 200)
        self.assertEqual(self.inbox().json()['total'], 0)
        self.assertEqual(self.unlock(payload).status_code, 200)

    def test_atomic_pause_is_listed_and_unlocked_even_after_initial_completion(self):
        topic = atomic_topic()
        self.app.state.catalog.topics[topic['id']] = topic
        self.app.state.catalog.topic_courses[topic['id']] = next(iter(self.app.state.catalog.courses))
        learner = AtomicLearningService(topic, self.root, learner_id=self.student)
        with learner._transaction() as con:
            state = learner._load(con); state['completed_at'] = '2026-10-04T00:00:00+00:00'
            learner._save(con, state)
        state = learner.pause(self.action(learner.state()))
        self.assertEqual(state['status'], 'completed')
        service = self.app.state.admin_pauses
        original = service.service_for
        service.service_for = lambda learner_id, topic_id: learner if topic_id == topic['id'] else original(learner_id, topic_id)
        result = self.inbox().json()['items'][0]
        payload = {k: result[k] for k in ('learner_id', 'topic_id', 'module_id', 'pause_event_id')}
        self.assertEqual(self.unlock(payload).status_code, 200)
        self.assertEqual(learner.state()['status'], 'completed'); self.assertIsNone(learner.state()['pause'])
