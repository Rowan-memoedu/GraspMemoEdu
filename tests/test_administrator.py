"""Administrator access uses real endpoints and disposable in-memory learning."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
import threading
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.access import FEATURES
from graspmemoedu.account_admin import AccountAdmin
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService, DomainError
from graspmemoedu.site_backup import backup_site, restore_site
from tests.fixtures import synthetic_course, DeterministicJudge, temporary_test_directory
from tests.atomic_fixtures import atomic_topic


class AdministratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.topics = {}
        for topic_id in ('before', 'after'):
            topic = synthetic_course(1); topic['id'] = topic_id
            self.topics[topic_id] = topic
        self.topics['atoms'] = atomic_topic('english', 'atoms')
        for name, topic in self.topics.items():
            (self.root / (name + '.json')).write_text(json.dumps(topic), encoding='utf-8')
        catalog = {'courses': [
            {'id': 'math-course', 'title': 'Synthetic', 'topics': [
                {'id': 'before', 'path': 'before.json'}, {'id': 'after', 'path': 'after.json', 'prerequisites': ['before']}]},
            {'id': 'english-course', 'title': 'Atomic synthetic', 'topics': [{'id': 'atoms', 'path': 'atoms.json'}]}]}
        (self.root / 'catalog.json').write_text(json.dumps(catalog), encoding='utf-8')
        (self.root / 'subjects.json').write_text(json.dumps({'subjects': [
            {'id': 'math', 'course_ids': ['math-course']}, {'id': 'english', 'course_ids': ['english-course']}]}), encoding='utf-8')
        self.settings = Settings(data_dir=self.root, public_api_origin='https://admin.test', allowed_origins=('https://site.test',))
        self.app = create_app(self.settings, LearningService(self.topics['before'], self.root, DeterministicJudge()))
        self.client = TestClient(self.app, base_url='https://admin.test'); self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        self.store = self.app.state.access_store
        with self.store._transaction() as con:
            self.admin = self.store._insert_account(con, 'Synthetic administrator', is_admin=True)
        self.login = self.store.login(self.admin['invitation_code'])
        self.learner = self.login['access']['learner_id']
        self.headers = {'Origin': 'https://site.test', 'X-Learning-Identity': self.login['identity_token']}
        self.before = self.learning_snapshot()

    def call(self, method, route, body=None, status=200, headers=None):
        result = self.client.request(method, '/api/' + route, headers=headers or self.headers,
                                     **({'json': body} if body is not None else {}))
        self.assertEqual(result.status_code, status, result.text)
        return result.json() if result.content else None

    def learning_snapshot(self):
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {table: con.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall() for table in tables
                    if not table.startswith(('access_', 'account_admin_', 'sqlite_'))}

    def payload(self, state, **extra):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def submit(self, topic, state, answer):
        result = self.call('POST', 'submit?topic_id=' + topic, self.payload(state, answer=answer))
        for _ in range(150):
            result = self.call('GET', 'submissions/' + result['submission_id'] + '?topic_id=' + topic)
            if result['status'] not in ('pending', 'running'): break
            time.sleep(.01)
        self.assertEqual(result['status'], 'judged')
        return result

    def test_unrestricted_dynamic_access_and_normal_accounts_remain_locked(self):
        self.store.set_permissions(self.learner, [], [])
        access = self.call('GET', 'access')
        self.assertTrue(access['is_admin']); self.assertEqual(access['features'], [f for f in FEATURES if f != 'skip_module'])
        self.assertEqual(len(access['topics']), 3)
        self.store.topics['future'] = {'id': 'future', 'title': 'Added later'}
        self.assertIn('future', {t['id'] for t in self.call('GET', 'access')['topics']})
        state = self.call('GET', 'state?topic_id=after')
        self.assertTrue(state['dependency_ready'])
        guest = self.call('POST', 'access/guest', {})
        headers = {**self.headers, 'X-Learning-Identity': guest['identity_token']}
        self.call('GET', 'state?topic_id=after', status=409, headers=headers)
        self.call('GET', 'materials', status=403, headers=headers)
        self.call('POST', 'materials/open?topic_id=atoms', {'request_id': 'denied', 'kind': 'atomic_card', 'id': 'a-1'}, status=403, headers=headers)
        self.assertEqual(self.learning_snapshot(), self.before)

    def test_normal_lesson_judgement_never_writes_learning_or_outbox(self):
        log = self.root / 'logs/events.jsonl'
        prior_log = log.read_bytes() if log.exists() else None
        state = self.call('GET', 'state?topic_id=after')
        self.assertEqual(state['steps'][0]['actions'], ['mastered', 'unmastered'])
        state = self.call('POST', 'choose?topic_id=after', self.payload(state, choice='mastered'))
        result = self.submit('after', state, 'correct')
        self.assertTrue(result['correct']); self.assertEqual(result['state']['modules'][0]['mastery'], 4)
        self.assertEqual(self.learning_snapshot(), self.before)
        self.assertEqual(log.read_bytes() if log.exists() else None, prior_log)
        with self.assertRaises(DomainError):
            LearningService(self.topics['after'], self.root, learner_id=self.learner)
        service = next(s for s in self.app.state.learning_services.values() if s.learner_id == self.learner)
        with self.assertRaises(DomainError): service.backup(self.root / 'forbidden.sqlite3')
        self.assertFalse((self.root / 'forbidden.sqlite3').exists())

    def test_demo_lesson_and_review_answers_preserve_ephemeral_storage(self):
        from datetime import datetime, timedelta, timezone

        self.call('GET', 'demo-answer?topic_id=after', status=409)
        state = self.call('GET', 'state?topic_id=after')
        state = self.call('POST', 'choose?topic_id=after', self.payload(state, choice='mastered'))
        demo = self.call('GET', 'demo-answer?topic_id=after')
        self.assertEqual(demo['step_id'], state['active_step_id'])
        self.assertEqual(demo['answer'], self.topics['after']['modules'][0]['example']['reference_answer'])
        self.assertEqual(self.call('GET', 'state?topic_id=after'), state)
        service = next(s for s in self.app.state.learning_services.values()
                       if s.learner_id == self.learner and s.topic_id == 'after')
        service.judge = lambda question, reference, answer: {
            'status': 'judged', 'correct': answer == reference, 'reason': 'Exact fixture judge'}
        result = self.submit('after', state, demo['answer'])
        self.assertTrue(result['correct'])
        self.call('GET', 'demo-answer?topic_id=after', status=409)

        service.review_backend.clock = lambda: datetime.now(timezone.utc) + timedelta(days=365)
        review = self.call('POST', 'review/begin?topic_id=after',
                           {'request_id': str(uuid.uuid4()), 'module_id': 'module-1'})
        route = 'review/sessions/' + review['session_id'] + '?topic_id=after'
        demo = self.call('GET', route.replace('?topic_id=', '/demo-answer?topic_id='))
        self.assertEqual(demo['question_id'], review['practice']['id'])
        self.assertEqual(demo['answer'], service.review_backend.question_for_session(
            review, review['practice']['id'])['reference_answer'])
        self.assertEqual(self.call('GET', route), review)
        self.assertEqual(self.learning_snapshot(), self.before)

    def test_demo_atomic_answer_uses_same_administrator_preview(self):
        state = self.call('POST', 'materials/open?topic_id=atoms',
                          {'request_id': 'demo-bundle', 'kind': 'atomic_card', 'id': 'ab-bundle'})
        demo = self.call('GET', 'demo-answer?topic_id=atoms')
        self.assertEqual(demo['question_id'], state['current']['card_id'])
        self.assertEqual(json.loads(demo['answer']), {'a': '红色', 'b': '蓝色'})
        after = self.call('GET', 'state?topic_id=atoms')
        self.assertEqual({k: v for k, v in after.items() if k != 'server_time'},
                         {k: v for k, v in state.items() if k != 'server_time'})
        self.assertTrue(self.submit('atoms', state, demo['answer'])['correct'])
        self.assertEqual(self.learning_snapshot(), self.before)

    def test_all_unlearned_variants_and_bundle_use_normal_card_flow_without_delivery(self):
        inventory = self.call('GET', 'atomic/reviews?subject_id=english')
        self.assertTrue(inventory['all_materials']); self.assertIsNone(inventory['next_due_at'])
        self.assertEqual(len(inventory['items']), 7)
        self.assertNotIn('reference_answer', json.dumps(inventory))
        body = {'request_id': 'open-bundle', 'kind': 'atomic_card', 'id': 'ab-bundle'}
        state = self.call('POST', 'materials/open?topic_id=atoms&subject_id=english', body)
        self.assertEqual(state['current']['interaction']['type'], 'fill_blank')
        self.assertEqual(self.call('POST', 'materials/open?topic_id=atoms', body)['revision'], state['revision'])
        result = self.submit('atoms', state, '{"a":"红色","b":"蓝色"}')
        self.assertTrue(result['correct']); self.assertEqual(result['state']['phase'], 'feedback')
        state = self.call('POST', 'continue?topic_id=atoms', self.payload(result['state']))
        self.assertEqual(state['phase'], 'completed'); self.assertIsNone(state['current'])
        self.assertIsNone(self.call('GET', 'atomic/reviews?subject_id=english')['next_due_at'])
        self.assertEqual(self.learning_snapshot(), self.before)

    def test_login_sessions_are_isolated_and_logout_discards_memory(self):
        state = self.call('GET', 'state?topic_id=before')
        state = self.call('POST', 'choose?topic_id=before', self.payload(state, choice='mastered'))
        result = self.submit('before', state, 'correct')
        other = self.store.login(self.admin['invitation_code'])
        second = {**self.headers, 'X-Learning-Identity': other['identity_token']}
        fresh = self.call('GET', 'state?topic_id=before', headers=second)
        self.assertEqual(fresh['steps'][0]['phase'], 'choice')
        self.call('GET', 'submissions/' + result['submission_id'] + '?topic_id=before', status=404, headers=second)
        old_services = [s for s in self.app.state.learning_services.values() if s.learner_id == self.learner]
        self.call('POST', 'access/logout', {})
        self.call('GET', 'state?topic_id=before', status=401)
        self.assertEqual(sum(s.runtime.closed for s in old_services), 1)
        self.assertEqual(self.call('GET', 'state?topic_id=before', headers=second)['steps'][0]['phase'], 'choice')
        self.assertEqual(self.learning_snapshot(), self.before)

    def test_private_three_admin_provision_is_atomic_idempotent_and_backup_preserves_roles(self):
        output = self.root / 'admin/three.json'
        manager = AccountAdmin(self.store)
        result = manager.provision('three-admins', learners=0, tests=0, admins=3, output=output)
        self.assertEqual(result['admins'], 3); self.assertEqual(result['learners'], 0)
        private = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(len(private['accounts']), 3)
        for account in private['accounts']:
            self.assertTrue(self.store.login(account['invitation_code'])['access']['is_admin'])
            self.assertNotIn(account['invitation_code'], json.dumps(result))
        self.assertTrue(manager.provision('three-admins', learners=0, tests=0, admins=3, output=output)['already_exists'])
        archive = self.root / 'backup.zip'; backup_site(self.settings, archive)
        destination = self.root / 'restored'; restore_site(archive, destination)
        with closing(sqlite3.connect(destination / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM access_principals WHERE is_admin=1').fetchone()[0], 4)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM topics WHERE learner=?', (self.learner,)).fetchone()[0], 0)

    def test_logout_during_judgement_discards_late_result_without_persistence(self):
        entered, finish = threading.Event(), threading.Event()
        state = self.call('GET', 'state?topic_id=before')
        service = next(s for s in self.app.state.learning_services.values() if s.learner_id == self.learner)
        def blocked_judge(*args):
            entered.set()
            finish.wait(5)
            return {'status': 'judged', 'correct': True, 'reason': 'Late result'}
        service.judge = blocked_judge
        state = self.call('POST', 'choose?topic_id=before', self.payload(state, choice='mastered'))
        self.call('POST', 'submit?topic_id=before', self.payload(state, answer='correct'))
        try:
            self.assertTrue(entered.wait(3))
            self.call('POST', 'access/logout', {})
            self.assertTrue(service.runtime.close_requested)
        finally:
            finish.set()
        for _ in range(200):
            if service.runtime.closed: break
            time.sleep(.01)
        self.assertTrue(service.runtime.closed)
        self.assertEqual(service.runtime.workers, 0)
        self.assertEqual(self.learning_snapshot(), self.before)
