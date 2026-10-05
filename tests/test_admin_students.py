"""Read-only student overview, full future schedules and shared atomic point dates."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from graspmemoedu.training import TrainingService
from tests.fixtures import synthetic_course, temporary_test_directory, DeterministicJudge
from tests.atomic_fixtures import atomic_topic
from tests.training_fixtures import training_bank


class AdminStudentsTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.course = synthetic_course(2)
        self.atoms = atomic_topic('english', 'atoms')
        for topic in (self.course, self.atoms):
            (self.root / (topic['id'] + '.json')).write_text(json.dumps(topic), encoding='utf-8')
        (self.root / 'catalog.json').write_text(json.dumps({'courses': [
            {'id': 'math', 'title': 'Math fixture', 'topics': [{'id': self.course['id'], 'path': self.course['id'] + '.json'}]},
            {'id': 'english', 'title': 'English fixture', 'topics': [{'id': 'atoms', 'path': 'atoms.json'}]}]}), encoding='utf-8')
        (self.root / 'subjects.json').write_text(json.dumps({'subjects': [
            {'id': 'math', 'course_ids': ['math']}, {'id': 'english', 'course_ids': ['english']}]}), encoding='utf-8')
        bank = training_bank(); (self.root / 'training-banks').mkdir()
        (self.root / 'training-banks' / (bank['id'] + '.json')).write_text(json.dumps(bank), encoding='utf-8')
        self.time = datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.app = create_app(Settings(data_dir=self.root, public_api_origin='https://api.test', allowed_origins=('https://site.test',)),
            LearningService(self.course, self.root, DeterministicJudge()), atomic_clock=lambda: self.time)
        self.client = TestClient(self.app, base_url='https://api.test'); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.store = self.app.state.access_store
        self.admin, self.admin_headers = self.identity('Admin', is_admin=True)
        self.other, self.other_headers = self.identity('Other admin', is_admin=True)
        self.student, self.headers = self.identity('Student <A>', is_student=True)
        self.second, _ = self.identity('Student B', is_student=True)
        self.normal, self.normal_headers = self.identity('Not a student')
        self.app.state.help_requests.set_default_admin(self.admin)
        self.app.state.help_requests.assign_admin(self.second, self.other)

    def identity(self, name, **flags):
        with self.store._transaction() as con:
            item = self.store._insert_account(con, name, **flags)
        login = self.store.login(item['invitation_code'])
        return item['account']['learner_id'], {'Origin': 'https://site.test', 'X-Learning-Identity': login['identity_token']}

    def detail(self, learner=None, headers=None):
        return self.client.get('/api/admin/students/' + (learner or self.student), headers=headers or self.admin_headers)

    def snapshot(self):
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {name: con.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in names}

    def learner(self, topic=None):
        return self.app.state.admin_pauses.service_for(self.student, topic or self.course['id'])

    @staticmethod
    def action(state, **values):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
            'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **values}

    def test_all_student_scope_pagination_search_and_strict_roles(self):
        self.store.set_enabled(self.second, False)
        before = self.snapshot()
        services = list(self.app.state.learning_services)
        for headers in (self.admin_headers, self.other_headers):
            r = self.client.get('/api/admin/students?limit=1', headers=headers)
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json(); self.assertEqual(data['total'], 2); self.assertEqual(data['next_offset'], 1)
            tail = self.client.get('/api/admin/students?limit=1&offset=1', headers=headers).json()
            self.assertIsNone(tail['next_offset']); self.assertFalse(tail['items'][0]['enabled'])
            self.assertEqual(self.detail(self.second, headers).status_code, 200)
        self.assertEqual(self.client.get('/api/admin/students?q=%25', headers=self.admin_headers).json()['total'], 0)
        self.assertEqual(self.client.get('/api/admin/students?q=Student', headers=self.admin_headers).json()['total'], 2)
        for learner in (self.normal, self.admin, 'missing'):
            self.assertEqual(self.detail(learner).status_code, 404)
        for headers in (self.headers, self.normal_headers):
            self.assertEqual(self.detail(headers=headers).status_code, 403)
            self.assertEqual(self.client.get('/api/admin/students', headers=headers).status_code, 403)
        self.assertEqual(self.client.get('/api/admin/students', headers={'Origin': 'https://site.test'}).status_code, 401)
        self.assertEqual(self.client.post('/api/admin/students', json={}, headers=self.admin_headers).status_code, 405)
        self.assertEqual(self.client.get('/api/admin/students?limit=101', headers=self.admin_headers).status_code, 422)
        self.assertEqual(self.client.options('/api/admin/students', headers={'Origin': 'https://evil.test', 'Access-Control-Request-Method': 'GET'}).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.app.state.learning_services), services)

    def test_future_modules_active_reviews_and_old_version_are_read_only(self):
        learner = self.learner(); backend = learner.review_backend; backend.clock = lambda: self.time
        self.assertEqual(self.detail().json()['learned'], [])  # Merely reading a Topic is not learning.
        state = learner.choose(self.action(learner.state(), choice='mastered'))
        result = learner.submit(self.action(state, answer='correct')); learner.process_submission(result['submission_id'])
        scheduled = backend.record_module_result(module_id='module-2', mastery=4,
            result_id='future-module', completed_at=self.time + timedelta(days=10))
        first = next(r for r in self.detail().json()['reviews'] if r['id'] == 'module-1')
        self.time = datetime.fromisoformat(first['due_at']) + timedelta(seconds=1)
        backend.begin('module-1', request_id='active-review')
        before = self.snapshot(); services = list(self.app.state.learning_services)
        response = self.detail(); self.assertEqual(response.status_code, 200, response.text)
        data = response.json(); self.assertEqual(len(data['learned'][0]['modules']), 1)
        self.assertEqual(data['learned'][0]['modules'][0]['status'], 'completed')
        self.assertEqual(len(data['reviews']), 2)
        future = next(r for r in data['reviews'] if r['id'] == 'module-2')
        self.assertEqual(future['due_at'], scheduled['due_at']); self.assertFalse(future['overdue'])
        self.assertIn('another_review_active', future['blockers'])
        self.assertTrue(next(r for r in data['reviews'] if r['id'] == 'module-1')['active'])
        self.assertEqual(self.snapshot(), before); self.assertEqual(list(self.app.state.learning_services), services)
        self.assertNotIn('REFERENCE_ONLY', response.text); self.assertNotIn('invitation_digest', response.text)
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            con.execute('UPDATE topics SET version=? WHERE learner=?', ('older-version', self.student)); con.commit()
            con.execute('UPDATE review_cards SET version=? WHERE learner=?', ('older-version', self.student)); con.commit()
        before = self.snapshot(); data = self.detail().json()
        self.assertFalse(data['learned'][0]['version_current'])
        self.assertTrue(all('old_version' in r['blockers'] for r in data['reviews']))
        self.assertEqual(self.snapshot(), before)

    def test_atomic_variants_bundles_unlearned_and_interleaving_share_point_dates(self):
        learner = self.learner('atoms')
        dates = {'a': self.time + timedelta(hours=6), 'b': self.time + timedelta(hours=8)}
        # Fixture schedule snapshot: two mastered points and one unlearned point.
        with learner._transaction() as con:
            state = learner._load(con)
            state.update(started_at=self.time.isoformat(), completed_at=self.time.isoformat(), status='completed')
            for pid, date in dates.items():
                state['points'][pid].update(mastered=True, first_passed_at=self.time.isoformat(), due_at=date.isoformat())
            state['chunk_last_seen']['colors'] = (dates['a'] - timedelta(seconds=60)).isoformat()
            learner._save(con, state)
        before = self.snapshot(); data = self.detail().json()
        self.assertEqual(len(data['reviews']), 2); self.assertEqual(len(data['atomic_cards']), 7)
        cards = {c['id']: c for c in data['atomic_cards']}
        effective_a = dates['a'] + timedelta(seconds=120)
        self.assertEqual(cards['a-1']['due_at'], effective_a.isoformat())
        self.assertEqual(cards['a-2']['due_at'], cards['a-1']['due_at'])
        self.assertEqual(cards['ab-bundle']['due_at'], dates['b'].isoformat())
        self.assertIsNone(cards['c-1']['due_at']); self.assertIsNone(cards['c-2']['due_at'])
        self.assertEqual(self.snapshot(), before)
        learner.pause(self.action(learner.state()))
        data = self.detail().json(); self.assertTrue(all('paused' in r['blockers'] for r in data['reviews']))
        self.assertEqual({r['id']: r['due_at'] for r in data['reviews']}['a'], effective_a.isoformat())

    def test_permission_removed_and_disabled_account_keep_visible_schedule(self):
        learner = self.learner(); learner.review_backend.clock = lambda: self.time
        learner.review_backend.record_module_result(module_id='module-1', mastery=4, result_id='future', completed_at=self.time)
        self.store.set_permissions(self.student, [], ['learn'])
        self.store.set_enabled(self.student, False)
        before = self.snapshot(); data = self.detail().json()
        self.assertEqual(len(data['reviews']), 1)
        for block in ('topic_forbidden', 'feature_forbidden', 'account_disabled'):
            self.assertIn(block, data['reviews'][0]['blockers'])
        self.assertEqual(self.snapshot(), before)

    def test_training_future_and_stop_push_are_included_without_rescheduling(self):
        service = TrainingService(self.app.state.training_catalog, self.root, self.student, clock=lambda: self.time)
        first = service.open('isolated-bank', 'group-a', 'essay', 'fixture-initial')
        service.reveal(first); service.rate(first, 4)
        attempt = service.open('isolated-bank', 'group-a', 'essay', 'fixture-stop')
        service.reveal(attempt)
        service.stop_reviews(attempt)
        before = self.snapshot(); data = self.detail().json()
        review = next(r for r in data['reviews'] if r['kind'] == 'training')
        self.assertFalse(review['overdue']); self.assertIn('stopped', review['blockers'])
        self.assertFalse(review['active'])
        self.assertEqual(len(data['training_learned']), 1)
        self.assertEqual(self.snapshot(), before)

    def test_formal_admin_projection_hides_test_points_and_bank_history_without_deleting(self):
        learner = self.learner('atoms')
        with learner._transaction() as con:
            state = learner._load(con)
            state.update(started_at=self.time.isoformat(), completed_at=self.time.isoformat(), status='completed')
            state['points']['a'].update(mastered=True, due_at=self.time.isoformat())
            learner._save(con, state)
        self.learner().review_backend.record_module_result(module_id='module-1', mastery=4,
            result_id='formal-schedule', completed_at=self.time)
        service = TrainingService(self.app.state.training_catalog, self.root, self.student, clock=lambda: self.time)
        attempt = service.open('isolated-bank', 'group-a', 'essay', 'test-history')
        service.reveal(attempt); service.rate(attempt, 4)
        self.store.test_materials.permission_ids.update({'atoms', 'training.isolated-bank'})
        before = self.snapshot()
        hidden = self.detail().json()
        self.assertFalse(hidden['atomic_cards']); self.assertFalse(hidden['training_learned'])
        self.assertFalse(any(r.get('bank_id') or r.get('topic_id') == 'atoms' for r in hidden['reviews']))
        self.assertTrue(any(r['topic_id'] == self.course['id'] for r in hidden['reviews']))
        self.assertFalse(any(r['topic_id'] == 'atoms' for r in hidden['learned']))
        listing = self.client.get('/api/admin/students', headers=self.admin_headers).json()
        self.assertEqual(next(r for r in listing['items'] if r['learner_id'] == self.student)['started_topics'], 0)
        self.assertEqual(self.snapshot(), before)
        test_admin, test_headers = self.identity('Test admin', is_admin=True, purpose='test')
        before = self.snapshot()
        visible = self.detail(headers=test_headers).json()
        self.assertTrue(visible['atomic_cards']); self.assertTrue(visible['training_learned'])
        self.assertTrue(any(r['topic_id'] == 'atoms' for r in visible['learned']))
        listing = self.client.get('/api/admin/students', headers=test_headers).json()
        self.assertEqual(next(r for r in listing['items'] if r['learner_id'] == self.student)['started_topics'], 1)
        self.assertEqual(self.snapshot(), before)
