"""Learner tiers gate future capabilities without changing existing learning."""
from contextlib import closing, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest.mock import patch
import uuid
from fastapi.testclient import TestClient
from graspmemoedu import access as policy, cli
from graspmemoedu.access import AccessStore, FEATURES, STANDARD_FEATURES
from graspmemoedu.account_admin import AccountAdmin
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService, DomainError
from graspmemoedu.site_backup import backup_site, restore_site
from tests.fixtures import synthetic_course, DeterministicJudge, temporary_test_directory


class AdvancedLearnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.course = synthetic_course(1)
        (self.root / 'course.json').write_text(json.dumps(self.course), encoding='utf-8')
        (self.root / 'settings.json').write_text(json.dumps({'course_path': str(self.root / 'course.json')}), encoding='utf-8')
        self.settings = Settings.load(self.root)
        self.app = create_app(self.settings, LearningService(self.course, self.root, DeterministicJudge()))
        self.store = self.app.state.access_store
        account = self.store.create_account('Synthetic learner', purpose='test')
        self.learner = account['account']['learner_id']
        self.token = self.store.login(account['invitation_code'])['identity_token']
        self.headers = {'Origin': 'http://127.0.0.1:8767', 'X-Learning-Identity': self.token}

    def test_existing_accounts_default_standard_and_migration_preserves_records(self):
        with closing(sqlite3.connect(self.store.db_path)) as con:
            before = con.execute('SELECT learner_id,features,topic_ids,invitation_digest FROM access_principals').fetchall()
            con.execute('ALTER TABLE access_principals DROP COLUMN is_advanced_learner')
        upgraded = AccessStore(self.root, [{'id': self.course['id'], 'title': self.course['title']}])
        self.assertFalse(upgraded.resolve(self.token)['is_advanced_learner'])
        self.assertFalse(upgraded.resolve(self.token)['is_student'])
        self.assertEqual(upgraded.resolve(self.token)['features'], list(STANDARD_FEATURES))
        with closing(sqlite3.connect(self.store.db_path)) as con:
            self.assertTrue(con.execute('SELECT learner_id,features,topic_ids,invitation_digest FROM access_principals').fetchall() == before)

    def test_future_feature_requires_both_qualification_and_grant(self):
        with patch.dict(policy.FEATURE_REQUIREMENTS, {'synthetic_advanced': {'advanced_learner': True}}), patch.object(policy, 'FEATURES', (*FEATURES, 'synthetic_advanced')):
            standard = self.store.resolve(self.token)
            self.assertNotIn('synthetic_advanced', standard['features'])
            with self.assertRaises(DomainError) as denied:
                self.store.authorize({**standard, 'features': [*standard['features'], 'synthetic_advanced']}, self.course['id'], 'synthetic_advanced')
            self.assertEqual(denied.exception.code, 'advanced_learner_required')
            advanced = self.store.set_permissions(self.learner, is_advanced_learner=True)
            self.assertIn('synthetic_advanced', advanced['features'])
            self.store.authorize(self.store.resolve(self.token), self.course['id'], 'synthetic_advanced')
            self.store.set_permissions(self.learner, features=['learn'])
            self.assertNotIn('synthetic_advanced', self.store.resolve(self.token)['features'])
            self.store.set_permissions(self.learner, reset_features=True)
            self.store.set_permissions(self.learner, is_advanced_learner=False)
            self.assertNotIn('synthetic_advanced', self.store.resolve(self.token)['features'])
            guest = self.store.create_guest()['access']
            self.assertFalse(policy.feature_eligible({**guest, 'is_advanced_learner': True}, 'synthetic_advanced'))
            with self.store._transaction() as con:
                admin = self.store._insert_account(con, 'Synthetic admin', is_admin=True)['account']
            self.assertIn('synthetic_advanced', admin['features'])

    def test_cli_changes_only_tier_and_rejects_conflicting_flags(self):
        self.store.set_permissions(self.learner, topic_ids=[], features=['learn'])
        def invoke(*args):
            stream = io.StringIO()
            with redirect_stdout(stream), redirect_stderr(stream), patch.object(sys, 'argv', ['graspmemoedu', '--data-dir', str(self.root), 'account-permissions', '--learner', self.learner, *args]):
                cli.main()
            return json.loads(stream.getvalue())
        result = invoke('--advanced-learner')
        self.assertTrue(result['is_advanced_learner']); self.assertEqual(result['topics'], [])
        self.assertEqual(result['features'], ['learn'])
        self.assertFalse(invoke('--standard-learner')['is_advanced_learner'])
        result = invoke('--advanced-learner', '--student')
        self.assertTrue(result['is_advanced_learner']); self.assertTrue(result['is_student'])
        result = invoke('--non-student')
        self.assertTrue(result['is_advanced_learner']); self.assertFalse(result['is_student'])
        result = invoke('--standard-learner', '--student')
        self.assertFalse(result['is_advanced_learner']); self.assertTrue(result['is_student'])
        with redirect_stderr(io.StringIO()), patch.object(sys, 'argv', ['graspmemoedu', '--data-dir', str(self.root), 'account-permissions', '--learner', self.learner, '--advanced-learner', '--standard-learner']), self.assertRaises(SystemExit):
            cli.main()
        with self.assertRaises(DomainError): self.store.set_permissions(self.learner, is_advanced_learner='true')
        with self.assertRaises(DomainError): self.store.set_permissions(self.learner, is_student=1)

    def test_web_cannot_promote_and_advanced_learning_is_persisted_normally(self):
        self.store.set_permissions(self.learner, is_advanced_learner=True, is_student=True)
        with TestClient(self.app, base_url='http://127.0.0.1:8767') as client:
            self.headers['X-Learning-Session'] = client.get('/api/session', headers=self.headers).json()['session_token']
            self.assertTrue(client.get('/api/access', headers=self.headers).json()['is_advanced_learner'])
            response = client.post('/api/profile', headers=self.headers, json={'is_advanced_learner': True, 'is_student': True})
            self.assertEqual(response.status_code, 422)
            response = client.post('/api/access/login', headers=self.headers, json={'invitation_code': 'invalid', 'is_advanced_learner': True})
            self.assertEqual(response.status_code, 422)
            state = client.get('/api/state', headers=self.headers).json()
            def body(state, **extra):
                return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                        'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}
            state = client.post('/api/choose', headers=self.headers, json=body(state, choice='mastered')).json()
            submitted = client.post('/api/submit', headers=self.headers, json=body(state, answer='correct')).json()
            for _ in range(200):
                result = client.get('/api/submissions/' + submitted['submission_id'], headers=self.headers).json()
                if result['status'] == 'judged': break
                time.sleep(.01)
            self.assertTrue(result['correct'])
            self.assertIsNone(self.app.state.learning_services[self.learner].runtime)
        with closing(sqlite3.connect(self.store.db_path)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM topics WHERE learner=?', (self.learner,)).fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM submissions WHERE learner=?', (self.learner,)).fetchone()[0], 1)

    def test_tier_survives_rotation_restart_and_backup_restore(self):
        self.store.set_permissions(self.learner, is_advanced_learner=True, is_student=True)
        AccountAdmin(self.store).rotate(self.learner, output=self.root / 'admin/rotated.json')
        reopened = AccessStore(self.root, [{'id': self.course['id'], 'title': self.course['title']}])
        self.assertTrue(next(a for a in reopened.list_principals(accounts_only=True) if a['learner_id'] == self.learner)['is_advanced_learner'])
        backup = self.root / 'backup.zip'; backup_site(self.settings, backup)
        restored = self.root / 'restored'; restore_site(backup, restored)
        with closing(sqlite3.connect(restored / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT is_advanced_learner FROM access_principals WHERE learner_id=?', (self.learner,)).fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT is_student FROM access_principals WHERE learner_id=?', (self.learner,)).fetchone()[0], 1)

    def test_student_and_advanced_requirements_are_independent_and_intersect(self):
        rules = {'synthetic_advanced': {'advanced_learner': True}, 'synthetic_student': {'student': True},
                 'synthetic_both': {'advanced_learner': True, 'student': True}}
        with patch.dict(policy.FEATURE_REQUIREMENTS, rules), patch.object(policy, 'FEATURES', (*FEATURES, *rules)):
            for advanced, student in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(advanced=advanced, student=student):
                    self.store.set_permissions(self.learner, is_advanced_learner=advanced, is_student=student)
                    current = self.store.resolve(self.token)
                    for feature, eligible in [('synthetic_advanced', advanced), ('synthetic_student', student), ('synthetic_both', advanced and student)]:
                        self.assertEqual(feature in current['features'], eligible)
                        if eligible:
                            self.store.authorize(current, self.course['id'], feature)
                        else:
                            with self.assertRaises(DomainError): self.store.authorize(current, self.course['id'], feature)
                    self.assertEqual([f for f in current['features'] if f in STANDARD_FEATURES], list(STANDARD_FEATURES))
                    self.assertEqual('request_help' in current['features'], student)
            self.store.set_permissions(self.learner, features=[])
            self.assertEqual(self.store.resolve(self.token)['features'], [])

    def test_four_private_batches_create_exactly_five_each_and_retry_safely(self):
        manager = AccountAdmin(self.store)
        for advanced, student in ((False, False), (True, False), (False, True), (True, True)):
            batch = f'qualification-{int(advanced)}-{int(student)}'
            options = dict(learners=5, tests=0, output=self.root / 'admin' / (batch + '.json'),
                           is_advanced_learner=advanced, is_student=student)
            result = manager.provision(batch, **options)
            self.assertEqual(result['account_count'], 5)
            self.assertTrue(manager.provision(batch, **options)['already_exists'])
            accounts = [a for a in self.store.list_principals(accounts_only=True) if a['batch_id'] == batch]
            self.assertEqual(len(accounts), 5)
            for account in accounts:
                self.assertEqual(account['is_advanced_learner'], advanced)
                self.assertEqual(account['is_student'], student)
            manifest = json.loads(options['output'].read_text(encoding='utf-8'))
            self.assertNotIn('invitation_code', json.dumps(result))
            for account in manifest['accounts']:
                self.assertTrue(account['invitation_code'] not in json.dumps(result))
        with self.assertRaises(DomainError):
            manager.provision('qualification-1-1', **{**options, 'is_student': False})
