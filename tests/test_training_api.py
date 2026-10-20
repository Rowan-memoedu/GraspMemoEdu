from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.atomic import AtomicLearningService
from graspmemoedu.config import Settings
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.training_content import TrainingCatalog
from graspmemoedu.training import TrainingService
from graspmemoedu.service import DomainError
from atomic_fixtures import atomic_topic
from test_atomic_api import write_catalog
from training_fixtures import training_bank
from tests.fixtures import temporary_test_directory


class TrainingAPITests(unittest.TestCase):
    def test_reader_preloads_question_faces_without_creating_attempts_or_revealing_answers(self):
        path = '/api/training/group?subject_id=math&bank_id=isolated-bank&group_id=group-a&reader=1'
        group = self.call('GET', path)
        self.assertEqual(set(group['reader_states']), {q['id'] for q in group['questions']})
        with closing(sqlite3.connect(self.root/'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_attempts').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_memory').fetchone()[0], 0)
        again = self.call('GET', path)
        self.assertEqual(group, again)
        for value in group['reader_states'].values():
            self.assertTrue(value['preview'])
            self.assertIsNone(value['explanation_html'])
            self.assertFalse(value.get('reference_answer'))
            self.assertNotIn('answer_key', value['question']['interaction'] or {})
        preview = group['reader_states']['exact']
        opened = self.call('POST', '/api/training/open', {'request_id': preview['attempt_id'],
            'bank_id': group['bank_id'], 'group_id': group['id'], 'question_id': 'exact'})
        self.assertEqual(preview['question'], opened['question'])
        self.assertNotIn('preview', opened)
        self.answer(opened)
        loaded = self.call('GET', path)
        self.assertEqual(loaded['reader_states']['exact']['phase'], 'done')
        self.assertTrue(loaded['reader_states']['essay']['preview'])
        with closing(sqlite3.connect(self.root/'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_attempts').fetchone()[0], 1)
        self.call('GET', path.replace('subject_id=math', 'subject_id=physics'), status=403)
        login = self.account()
        self.app.state.access_store.set_permissions(login['access']['learner_id'], topic_ids=[])
        self.call('GET', path, status=403)

    def setUp(self):
        temporary = temporary_test_directory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); write_catalog(self.root)
        (self.root / 'training-banks').mkdir()
        self.bank = training_bank(); (self.root / 'training-banks/isolated-bank.json').write_text(json.dumps(self.bank), encoding='utf-8')
        self.when = datetime.now(timezone.utc)
        self.settings = Settings(data_dir=self.root, public_api_origin='https://training.test', allowed_origins=('https://site.test',))
        course = AtomicLearningService(atomic_topic('english', 'atomic-english'), self.root,
                                       judge=lambda *args: {'status': 'judged', 'correct': True, 'reason': 'ok'})
        self.app = create_app(self.settings, course, atomic_clock=lambda: self.when)
        self.client = TestClient(self.app, base_url='https://training.test'); self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        self.headers = {'Origin': 'https://site.test'}
        self.guest = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = self.guest['identity_token']

    def call(self, method, path, body=None, status=200):
        result = self.client.request(method, path, headers=self.headers, **({'json': body} if body is not None else {}))
        self.assertEqual(result.status_code, status, result.text)
        return result.json()

    def open(self, qid='essay', status=200):
        return self.call('POST', '/api/training/open?subject_id=math', {'request_id': str(uuid.uuid4()),
            'bank_id': self.bank['id'], 'group_id': 'group-a', 'question_id': qid}, status)

    def answer(self, state):
        data = {'attempt_id': state['attempt_id'], 'request_id': str(uuid.uuid4()), 'answer': '2', 'elapsed_ms': 100}
        result = self.call('POST', '/api/training/submit', data)
        deadline = time.monotonic() + 10
        while result['submission_status'] in ('pending', 'running') and time.monotonic() < deadline:
            time.sleep(.01)
            result = self.call('GET', '/api/training/submission?submission_id=' + data['request_id'])
        self.assertEqual(result['phase'], 'done', result)
        return result, data

    def account(self, **kwargs):
        if kwargs.get('is_admin'):
            with self.app.state.access_store._transaction() as con:
                created = self.app.state.access_store._insert_account(con, '隔离管理员', **kwargs)
        else:
            created = self.app.state.access_store.create_account('隔离验收', **kwargs)
        login = self.call('POST', '/api/access/login', {'invitation_code': created['invitation_code']})
        self.headers['X-Learning-Identity'] = login['identity_token']
        return login

    def test_catalog_free_order_history_and_fsrs_unified_queue(self):
        self.assertEqual(len(self.call('GET', '/api/training/catalog?subject_id=math')['banks']), 1)
        self.assertEqual(self.call('GET', '/api/training/catalog?subject_id=physics')['banks'], [])
        self.call('GET', '/api/training/bank?subject_id=physics&bank_id=isolated-bank', status=403)
        result, payload = self.answer(self.open('exact'))
        self.assertEqual(result['result']['rating'], 5)
        self.assertTrue(result['stopped']); self.assertTrue(result['can_review_early'])
        self.assertIsNone(result['due_at'])
        replay = self.call('POST', '/api/training/submit', payload); self.assertEqual(replay['result'], result['result'])
        self.when += timedelta(hours=6, seconds=1)
        self.assertEqual(len(self.call('GET', '/api/training/reviews?subject_id=math')['items']), 0)
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM topics WHERE learner=?', (self.guest['access']['learner_id'],)).fetchone()[0], 0)

    def test_account_qualification_preference_and_fixed_question_enforced_server_side(self):
        state = self.open()
        self.call('POST', '/api/training/reveal', {'attempt_id': state['attempt_id']}, status=403)
        self.account(is_advanced_learner=True)
        state = self.open(); self.assertTrue(state['can_self_rate'])
        self.assertTrue(self.call('GET', '/api/training/preferences')['self_rating_enabled'])
        self.call('POST', '/api/training/preferences', {'self_rating_enabled': False})
        self.call('POST', '/api/training/reveal', {'attempt_id': state['attempt_id']}, status=403)
        self.call('POST', '/api/training/preferences', {'self_rating_enabled': True})
        self.call('POST', '/api/training/rate', {'attempt_id': state['attempt_id'], 'rating': 3}, status=422)
        shown = self.call('POST', '/api/training/reveal', {'attempt_id': state['attempt_id']})
        self.assertTrue(shown['explanation_html'])
        self.call('POST', '/api/training/rate', {'attempt_id': state['attempt_id'], 'rating': True}, status=422)
        rated = self.call('POST', '/api/training/rate', {'attempt_id': state['attempt_id'], 'rating': 4})
        self.assertEqual(rated['result']['rating'], 4)
        fixed = self.open('boolean'); self.assertFalse(fixed['can_self_rate'])
        self.call('POST', '/api/training/reveal', {'attempt_id': fixed['attempt_id']}, status=403)

    def test_unlearned_notebook_backup_restores_without_fsrs_or_completed_answers(self):
        state = self.open('exact')
        self.call('POST', '/api/training/notebook', {'attempt_id': state['attempt_id']})
        backup = self.root / 'unlearned.zip'; backup_site(self.settings, backup)
        destination = self.root / 'unlearned-restored'; restore_site(backup, destination)
        backend = TrainingService(TrainingCatalog(destination), destination, self.guest['access']['learner_id'],
                                  clock=lambda: self.when)
        self.assertEqual(len(backend.reviews([self.bank])['items']), 1)
        self.assertTrue(backend.state(state['attempt_id'])['awaiting_first_learning'])
        with closing(backend._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_ratings').fetchone()[0], 0)

    def test_notebook_endpoint_visible_before_first_answer_and_permissions_checked(self):
        state = self.open('exact')
        payload = {'attempt_id': state['attempt_id']}
        added = self.call('POST', '/api/training/notebook?subject_id=math', payload)
        self.assertTrue(added['in_error_notebook']); self.assertTrue(added['awaiting_first_learning'])
        self.assertEqual(added['phase'], 'answer'); self.assertIsNone(added['result'])
        self.assertEqual(len(self.call('GET', '/api/training/reviews?subject_id=math')['items']), 1)
        self.call('POST', '/api/training/notebook?subject_id=physics', payload, 403)
        self.call('POST', '/api/training/notebook', payload)
        result, _ = self.answer(added)
        self.assertEqual(result['result']['rating'], 3); self.assertFalse(result['stopped'])
        self.account(features=['submit_answer'])
        self.call('POST', '/api/training/notebook', payload, 403)

    def test_explicit_permissions_fail_closed_and_revocation_blocks_history(self):
        login = self.account(topic_ids=['atomic-english'])
        self.assertEqual(self.call('GET', '/api/training/catalog')['banks'], [])
        self.open(status=403)
        self.app.state.access_store.set_permissions(login['access']['learner_id'], reset_topics=True)
        old = self.open(); self.answer(old); self.open()
        self.app.state.access_store.set_permissions(login['access']['learner_id'], features=['learn', 'submit_answer'])
        self.call('GET', '/api/training/state?attempt_id=' + old['attempt_id'], status=403)
        self.call('GET', '/api/training/history?bank_id=isolated-bank&question_id=essay', status=403)

    def test_stop_review_authorization_idempotence_and_backup_restore(self):
        state = self.open()
        self.call('POST', '/api/training/stop', {'attempt_id': state['attempt_id']}, status=403)
        login = self.account(is_advanced_learner=True)
        state = self.open(); payload = {'attempt_id': state['attempt_id']}
        self.call('POST', '/api/training/stop', payload, status=422)
        self.call('POST', '/api/training/reveal', payload)
        self.call('POST', '/api/training/rate', {**payload, 'rating': 5}, status=422)
        self.call('POST', '/api/training/preferences', {'self_rating_enabled': False})
        self.call('POST', '/api/training/stop', payload, status=403)
        self.call('POST', '/api/training/preferences', {'self_rating_enabled': True})
        stopped = self.call('POST', '/api/training/stop', payload)
        self.assertTrue(stopped['stopped'])
        self.assertEqual(stopped, self.call('POST', '/api/training/stop', payload))
        self.assertEqual(self.call('GET', '/api/training/bank?bank_id=isolated-bank')['groups'][1]['completed_count'], 1)
        backup = self.root / 'stopped.zip'; backup_site(self.settings, backup)
        destination = self.root / 'stopped-restored'; restore_site(backup, destination)
        restored = TrainingService(TrainingCatalog(destination), destination, login['access']['learner_id'])
        self.assertTrue(restored.state(state['attempt_id'])['stopped'])
        self.assertEqual(restored.reviews([self.bank]), {'items': [], 'next_due_at': None})
        self.account(is_advanced_learner=True)
        self.call('POST', '/api/training/stop', payload, status=404)

    def test_owner_scope_and_backup_restore(self):
        state, _ = self.answer(self.open())
        backup = self.root / 'training.zip'; backup_site(self.settings, backup)
        destination = self.root / 'restored'; restore_site(backup, destination)
        restored = TrainingService(TrainingCatalog(destination), destination, self.guest['access']['learner_id'])
        self.assertEqual(restored.state(state['attempt_id'])['result'], state['result'])
        self.account()
        self.call('GET', '/api/training/state?attempt_id=' + state['attempt_id'], status=404)

    def test_invalid_answer_and_idempotent_replay_do_not_spend_extra_quota(self):
        state = self.open('choice')
        self.call('POST', '/api/training/submit', {'attempt_id': state['attempt_id'], 'request_id': str(uuid.uuid4()),
                  'answer': '{"selected":"unknown"}'}, status=422)
        with closing(sqlite3.connect(self.root / 'judge-daily-quota.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT COALESCE(SUM(count),0) FROM usage').fetchone()[0], 0)
        _, payload = self.answer(self.open('exact'))
        self.call('POST', '/api/training/submit', payload)
        with closing(sqlite3.connect(self.root / 'judge-daily-quota.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT COALESCE(SUM(count),0) FROM usage').fetchone()[0], 2)

    def test_public_boundary_rejects_missing_identity_and_management(self):
        token = self.headers.pop('X-Learning-Identity')
        for route in ('catalog', 'reviews', 'preferences'):
            self.call('GET', '/api/training/' + route, status=401)
        self.headers['X-Learning-Identity'] = token
        self.call('POST', '/api/training/import', {}, status=403)
        self.call('POST', '/api/training/reschedule', {}, status=403)

    def test_review_feature_permission_matches_existing_queue(self):
        self.account(features=['review_history'])
        self.call('GET', '/api/atomic/reviews?subject_id=english', status=403)
        self.call('GET', '/api/training/reviews?subject_id=english', status=403)
        self.open(status=403)

    def test_admin_session_only_and_logout_clears_it(self):
        login = self.account(is_admin=True)
        self.assertEqual(len(self.call('GET', '/api/training/reviews?subject_id=math')['items']), 6)
        with self.assertRaises(DomainError):
            TrainingService(self.app.state.training_catalog, self.root, login['access']['learner_id'])
        state, _ = self.answer(self.open())
        learner = login['access']['learner_id']
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            for table in ('training_attempts', 'training_memory', 'training_submissions', 'training_ratings', 'training_suspensions'):
                self.assertEqual(con.execute(f'SELECT COUNT(*) FROM {table} WHERE learner=?', (learner,)).fetchone()[0], 0)
        self.call('POST', '/api/access/logout', {})
        self.call('GET', '/api/training/state?attempt_id=' + state['attempt_id'], status=401)


if __name__ == '__main__': unittest.main()
