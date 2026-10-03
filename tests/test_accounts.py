"""Account lifecycle acceptance in disposable databases with a local fixture judge.

No production invitation, account, data directory or external judge is used.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
import unittest
import uuid

from fastapi.testclient import TestClient

from graspmemoedu.access import AccessStore, FEATURES, STANDARD_FEATURES, credential_digest
from graspmemoedu.api import create_app
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.service import DomainError, LearningService
from graspmemoedu.site_backup import backup_site, restore_site
from tests.fixtures import synthetic_course, temporary_test_directory


class FixtureAccountJudge:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, question, reference, answer):
        self.calls.append((question, reference, answer))
        if answer == 'blocked-correct':
            self.started.set()
            if not self.release.wait(8):
                raise RuntimeError('Fixture judge release missing')
        return {'status': 'judged', 'correct': answer in {'correct', 'blocked-correct'},
                'reason': 'Local account fixture judgement'}


class AccountAcceptanceTests(unittest.TestCase):
    origin = 'http://127.0.0.1:8767'

    def setUp(self):
        self.temporary = temporary_test_directory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.topics = {}
        self.catalog_data = {'courses': []}
        for suffix in ('a', 'b'):
            self._write_topic(suffix)
        self._save_catalog()
        self.settings = Settings(data_dir=self.root)
        self.judge = FixtureAccountJudge()
        self.clients = []
        self.addCleanup(self._close)
        self._open()

    def _write_topic(self, suffix):
        topic = synthetic_course(1)
        topic.update(id='account-topic-' + suffix, title='Account fixture topic ' + suffix)
        self.topics[topic['id']] = topic
        filename = topic['id'] + '.json'
        (self.root / filename).write_text(json.dumps(topic), encoding='utf-8')
        self.catalog_data['courses'].append({'id': 'account-course-' + suffix, 'title': 'Fixture course ' + suffix,
                                              'description': '', 'topics': [{'id': topic['id'], 'path': filename}]})

    def _save_catalog(self):
        (self.root / 'catalog.json').write_text(json.dumps(self.catalog_data), encoding='utf-8')

    def _open(self):
        service = LearningService(self.topics['account-topic-a'], self.root, self.judge)
        self.app = create_app(self.settings, service)
        self.store = self.app.state.access_store
        self.client = TestClient(self.app, base_url=self.origin)
        self.client.__enter__()
        self.clients.append(self.client)
        self.transport = self.client.get('/api/session', headers={'Origin': self.origin}).json()['session_token']

    def _restart(self):
        self.client.__exit__(None, None, None)
        self.clients.remove(self.client)
        self._open()

    def _close(self):
        self.judge.release.set()
        for client in reversed(self.clients):
            client.__exit__(None, None, None)
        for thread in threading.enumerate():
            if thread.name.startswith('answer-judge'):
                thread.join(5)

    def _headers(self, token=None):
        result = {'Origin': self.origin, 'X-Learning-Session': self.transport}
        if token:
            result['X-Learning-Identity'] = token
        return result

    def _request(self, method, path, token=None, payload=None, status=200, code=None):
        options = {'headers': self._headers(token)}
        if payload is not None:
            options['json'] = payload
        response = self.client.request(method, path, **options)
        self.assertEqual(response.status_code, status, response.text)
        result = response.json()
        if code:
            self.assertEqual(result['detail']['code'], code, result)
        return result

    def _account(self, name='Fixture account', topics=None, features=None, **extra):
        return self.store.create_account(name, topic_ids=topics, features=features,
                                         **{'purpose': 'test', **extra})

    def _login(self, account, current=None):
        return self._request('POST', '/api/access/login', current, {'invitation_code': account['invitation_code']})

    def _guest(self):
        return self._request('POST', '/api/access/guest', payload={})

    def _state(self, token, topic='account-topic-a'):
        return self._request('GET', '/api/state?topic_id=' + topic, token)

    def _payload(self, token, topic='account-topic-a', **extra):
        state = self._state(token, topic)
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def _action(self, token, endpoint, topic='account-topic-a', **extra):
        return self._request('POST', '/api/' + endpoint + '?topic_id=' + topic, token,
                             self._payload(token, topic, **extra))

    def _poll(self, token, submission_id, topic='account-topic-a'):
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            result = self._request('GET', '/api/submissions/' + submission_id + '?topic_id=' + topic, token)
            if result['status'] != 'pending':
                return result
            time.sleep(.01)
        self.fail('Local fixture submission did not finish')

    def _complete(self, token, topic='account-topic-a'):
        self._action(token, 'choose', topic, choice='mastered')
        submitted = self._action(token, 'submit', topic, answer='correct', elapsed_ms=2500)
        self.assertTrue(self._poll(token, submitted['submission_id'], topic)['correct'])
        self._action(token, 'continue', topic)
        return submitted['submission_id']

    def _assert_invalid(self, token, code='invalid_identity'):
        self._request('GET', '/api/access', token, status=401, code=code)

    def test_account_login_has_stable_identity_but_independent_device_tokens(self):
        account = self._account()
        first, second = self._login(account), self._login(account)
        self.assertNotEqual(first['identity_token'], second['identity_token'])
        self.assertEqual(first['access']['learner_id'], account['account']['learner_id'])
        self.assertEqual(first['access']['learner_id'], second['access']['learner_id'])
        self.assertEqual(first['access']['role'], 'account')
        self.assertEqual(first['access']['purpose'], 'test')
        self.assertTrue(first['access']['enabled'])
        self.assertEqual(set(first['access']['features']), set(STANDARD_FEATURES))
        changed = self._action(first['identity_token'], 'choose', choice='mastered')
        shared = self._state(second['identity_token'])
        self.assertEqual(shared['revision'], changed['revision'])
        self.assertEqual(shared['active_step_id'], changed['active_step_id'])

    def test_invitation_and_identity_plaintext_never_enter_database_or_public_records(self):
        account = self._account(batch_id='fixture-private-batch')
        login = self._login(account)
        with closing(self.store._connect()) as con:
            dump = '\n'.join(con.iterdump())
        self.assertNotIn(account['invitation_code'], dump)
        self.assertNotIn(login['identity_token'], dump)
        public = self._request('GET', '/api/access', login['identity_token'])
        for key in ('invitation_code', 'invitation_digest', 'token_digest', 'identity_token'):
            self.assertNotIn(key, public)
            self.assertNotIn(key, self.store.list_principals(accounts_only=True)[0])

    def test_fifteen_fixture_codes_are_unique_and_five_are_marked_for_testing(self):
        created = [self._account(name=f'Fixture batch {index}', purpose='learner' if index < 10 else 'test',
                                 batch_id='fixture-batch-15') for index in range(15)]
        self.assertEqual(len({item['invitation_code'] for item in created}), 15)
        self.assertEqual(sum(item['account']['purpose'] == 'test' for item in created), 5)
        self.assertTrue(all(item['account']['enabled'] for item in created))
        with closing(self.store._connect()) as con:
            rows = con.execute('SELECT purpose,batch_id FROM access_principals WHERE role="account"').fetchall()
        self.assertEqual(len(rows), 15)
        self.assertEqual({row['batch_id'] for row in rows}, {'fixture-batch-15'})
        with self.assertRaises(DomainError) as caught:
            self._account(purpose='administrator')
        self.assertEqual(caught.exception.status_code, 422)

    def test_invalid_invitation_does_not_revoke_current_valid_session(self):
        account = self._account()
        current = self._login(account)['identity_token']
        self._request('POST', '/api/access/login', current, {'invitation_code': 'invalid-fixture-code'}, 401, 'invalid_invitation')
        self.assertEqual(self._request('GET', '/api/access', current)['learner_id'], account['account']['learner_id'])

    def test_successful_account_switch_revokes_only_current_account_token(self):
        first, second = self._account('First'), self._account('Second')
        active = self._login(first)['identity_token']
        other_device = self._login(first)['identity_token']
        guest = self._guest()['identity_token']
        switched = self._login(second, active)
        self._assert_invalid(active)
        self.assertEqual(self._request('GET', '/api/access', other_device)['learner_id'], first['account']['learner_id'])
        self.assertEqual(switched['access']['learner_id'], second['account']['learner_id'])
        self._login(first, guest)
        self.assertEqual(self._request('GET', '/api/access', guest)['role'], 'guest')

    def test_login_can_replace_expired_or_invalid_current_token_after_valid_code(self):
        account = self._account()
        expired = self._login(account)['identity_token']
        with self.store._transaction() as con:
            con.execute('UPDATE access_identities SET expires_at=? WHERE token_digest=?',
                        ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), credential_digest('identity', expired)))
        self._assert_invalid(expired, 'identity_expired')
        replacement = self._login(account, expired)
        self.assertEqual(replacement['access']['learner_id'], account['account']['learner_id'])
        self.assertEqual(self._login(account, 'forged-current-identity')['access']['role'], 'account')

    def test_logout_revokes_only_current_account_session_and_guest_logout_is_noop(self):
        account = self._account()
        first, other = self._login(account)['identity_token'], self._login(account)['identity_token']
        result = self._request('POST', '/api/access/logout', first, {})
        self.assertEqual(result['status'], 'logged_out')
        self._assert_invalid(first)
        self.assertEqual(self._request('GET', '/api/access', other)['role'], 'account')
        guest = self._guest()['identity_token']
        self.assertEqual(self._request('POST', '/api/access/logout', guest, {})['status'], 'logged_out')
        self.assertEqual(self._request('GET', '/api/access', guest)['role'], 'guest')

    def test_disable_revokes_all_devices_and_enable_does_not_revive_old_sessions(self):
        account = self._account()
        tokens = [self._login(account)['identity_token'] for _ in range(2)]
        state = self._action(tokens[0], 'choose', choice='mastered')
        self.store.set_enabled(account['account']['learner_id'], False)
        for token in tokens:
            self._assert_invalid(token)
        disabled = self._request('POST', '/api/access/login', payload={'invitation_code': account['invitation_code']}, status=401)
        unknown = self._request('POST', '/api/access/login', payload={'invitation_code': 'wrong-invitation'}, status=401)
        self.assertEqual(disabled['detail'], unknown['detail'])
        self.assertEqual(disabled['detail']['code'], 'invalid_invitation')
        self.store.set_enabled(account['account']['learner_id'], True)
        for token in tokens:
            self._assert_invalid(token)
        replacement = self._login(account)['identity_token']
        self.assertEqual(self._state(replacement)['revision'], state['revision'])

    def test_rotation_changes_code_revokes_sessions_and_preserves_identity_permissions_progress(self):
        account = self._account(topics=['account-topic-a'], features=['learn', 'submit_answer'], batch_id='rotation-fixture')
        first, other = self._login(account)['identity_token'], self._login(account)['identity_token']
        before = self._action(first, 'choose', choice='mastered')
        rotated = self.store.rotate_invitation(account['account']['learner_id'])
        self.assertNotEqual(rotated['invitation_code'], account['invitation_code'])
        self._assert_invalid(first)
        self._assert_invalid(other)
        self._request('POST', '/api/access/login', payload={'invitation_code': account['invitation_code']}, status=401, code='invalid_invitation')
        after = self._login(rotated)
        self.assertEqual(after['access']['learner_id'], account['account']['learner_id'])
        self.assertEqual(after['access']['features'], ['learn', 'submit_answer'])
        self.assertEqual([topic['id'] for topic in after['access']['topics']], ['account-topic-a'])
        self.assertEqual(after['access']['purpose'], 'test')
        self.assertEqual(self._state(after['identity_token'])['revision'], before['revision'])
        with closing(self.store._connect()) as con:
            self.assertEqual(con.execute('SELECT batch_id FROM access_principals WHERE learner_id=?',
                                         (account['account']['learner_id'],)).fetchone()[0], 'rotation-fixture')

    def test_expiry_is_thirty_days_while_guest_has_no_expiry_and_expired_mutation_does_nothing(self):
        account = self._account()
        token = self._login(account)['identity_token']
        guest = self._guest()['identity_token']
        payload = self._payload(token, choice='mastered')
        before = self._state(token)
        with self.store._transaction() as con:
            row = con.execute('SELECT created_at,expires_at FROM access_identities WHERE token_digest=?',
                              (credential_digest('identity', token),)).fetchone()
            duration = datetime.fromisoformat(row['expires_at']) - datetime.fromisoformat(row['created_at'])
            self.assertEqual(duration, timedelta(days=30))
            self.assertIsNone(con.execute('SELECT expires_at FROM access_identities WHERE token_digest=?',
                                          (credential_digest('identity', guest),)).fetchone()[0])
            con.execute('UPDATE access_identities SET expires_at=? WHERE token_digest=?',
                        ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), credential_digest('identity', token)))
        self._request('POST', '/api/choose?topic_id=account-topic-a', token, payload, 401, 'identity_expired')
        learner = self.app.state.learning_services[(account['account']['learner_id'], 'account-topic-a')]
        self.assertEqual(learner.state()['revision'], before['revision'])
        self.assertEqual(self._request('GET', '/api/access', guest)['role'], 'guest')

    def test_revoke_sessions_preserves_invitation_and_does_not_affect_other_accounts(self):
        first, second = self._account('First'), self._account('Second')
        tokens = [self._login(first)['identity_token'] for _ in range(2)]
        second_token = self._login(second)['identity_token']
        self.store.revoke_sessions(first['account']['learner_id'])
        for token in tokens:
            self._assert_invalid(token)
        self.assertEqual(self._request('GET', '/api/access', second_token)['learner_id'], second['account']['learner_id'])
        self.assertEqual(self._login(first)['access']['learner_id'], first['account']['learner_id'])

    def test_account_and_guest_progress_profiles_selections_and_submissions_are_isolated(self):
        first, second = self._account('First'), self._account('Second')
        one, two, guest = self._login(first)['identity_token'], self._login(second)['identity_token'], self._guest()['identity_token']
        submission_id = self._complete(one)
        self._request('POST', '/api/catalog/select', one, {'course_id': 'account-course-b'})
        self._request('POST', '/api/profile', one, {'display_name': 'Changed first', 'timezone': 'Asia/Shanghai'})
        for token in (two, guest):
            self.assertEqual(self._state(token)['revision'], 0)
            self.assertIsNone(self._request('GET', '/api/catalog', token)['selected_course_id'])
            self.assertNotEqual(self._request('GET', '/api/profile', token)['display_name'], 'Changed first')
            self._request('GET', '/api/submissions/' + submission_id + '?topic_id=account-topic-a', token, status=404, code='unknown_submission')
            self._request('GET', '/api/tasks/lesson:account-topic-a/answers', token, status=404)
        self.assertEqual(self._state(one)['status'], 'completed')

    def test_topic_revocation_immediately_blocks_learning_history_feedback_and_course_selection(self):
        account = self._account()
        token = self._login(account)['identity_token']
        submission_id = self._complete(token)
        self.store.set_permissions(account['account']['learner_id'], topic_ids=['account-topic-b'])
        for path in ('/api/state?topic_id=account-topic-a', '/api/submissions/' + submission_id + '?topic_id=account-topic-a',
                     '/api/tasks/lesson:account-topic-a/answers'):
            self._request('GET', path, token, status=403, code='topic_forbidden')
        self._request('GET', '/api/dashboard?course_id=account-course-a', token, status=403, code='course_forbidden')
        self._request('POST', '/api/catalog/select', token, {'course_id': 'account-course-a'}, 403, 'course_forbidden')
        self._request('POST', '/api/feedback', token, {'message': 'Should be denied', 'topic_id': 'account-topic-a'}, 403, 'topic_forbidden')
        access = self._request('GET', '/api/access', token)
        self.assertEqual([topic['id'] for topic in access['topics']], ['account-topic-b'])
        self.store.set_permissions(account['account']['learner_id'], topic_ids=['account-topic-a', 'account-topic-b'])
        self.assertEqual(self._state(token)['status'], 'completed')

    def test_feature_revocation_is_enforced_by_api_and_active_action_list(self):
        account = self._account()
        token = self._login(account)['identity_token']
        learner_id = account['account']['learner_id']
        payload = self._payload(token, choice='mastered')
        self.store.set_permissions(learner_id, features=['submit_answer', 'review_history', 'pause_topic'])
        self._request('POST', '/api/choose?topic_id=account-topic-a', token, payload, 403, 'feature_forbidden')
        self.assertEqual(self._state(token)['steps'][0]['actions'], [])
        self.store.set_permissions(learner_id, features=list(FEATURES))
        self._action(token, 'choose', choice='mastered')
        answer_payload = self._payload(token, answer='correct')
        self.store.set_permissions(learner_id, features=['learn', 'review_history'])
        self._request('POST', '/api/submit?topic_id=account-topic-a', token, answer_payload, 403, 'feature_forbidden')
        self._request('POST', '/api/pause?topic_id=account-topic-a', token, {'request_id': str(uuid.uuid4())}, 403, 'feature_forbidden')
        self.store.set_permissions(learner_id, features=list(FEATURES))
        submitted = self._request('POST', '/api/submit?topic_id=account-topic-a', token, answer_payload)
        self._poll(token, submitted['submission_id'])
        continue_payload = self._payload(token)
        self.store.set_permissions(learner_id, features=['submit_answer', 'review_history', 'pause_topic'])
        self._request('POST', '/api/continue?topic_id=account-topic-a', token, continue_payload, 403, 'feature_forbidden')
        self.assertEqual(self._state(token)['status'], 'in_progress')

    def test_review_history_revocation_hides_old_content_and_completed_answers(self):
        account = self._account()
        token = self._login(account)['identity_token']
        submission_id = self._complete(token)
        before = self._state(token)
        previous = next(step for step in before['steps'] if step['question_id'])
        self.store.set_permissions(account['account']['learner_id'], features=['learn', 'submit_answer', 'pause_topic'])
        current = self._state(token)
        self.assertEqual(len(current['steps']), 1)
        self.assertEqual(current['steps'][0]['id'], current['active_step_id'])
        self.assertNotIn(previous['id'], [step['id'] for step in current['steps']])
        select = self._payload(token, step_id=previous['id'])
        self._request('POST', '/api/select?topic_id=account-topic-a', token, select, 403, 'feature_forbidden')
        self._request('GET', '/api/tasks/lesson:account-topic-a/answers', token, status=403, code='feature_forbidden')
        self._request('GET', '/api/submissions/' + submission_id + '?topic_id=account-topic-a', token, status=403, code='feature_forbidden')
        self.assertEqual(self._request('GET', '/api/dashboard?course_id=account-course-a', token)['history'], [])

    def test_explicit_topic_override_does_not_grant_future_topics(self):
        account = self._account(topics=list(self.store.topics))
        token = self._login(account)['identity_token']
        guest = self._guest()['identity_token']
        self._write_topic('future')
        self._save_catalog()
        self._restart()
        self._request('GET', '/api/state?topic_id=account-topic-future', token, status=403, code='topic_forbidden')
        self.assertEqual(self._state(guest, 'account-topic-future')['topic_id'], 'account-topic-future')
        self.store.set_permissions(account['account']['learner_id'], topic_ids=['account-topic-future'])
        self.assertEqual(self._state(token, 'account-topic-future')['revision'], 0)
        self._request('GET', '/api/state?topic_id=account-topic-a', token, status=403, code='topic_forbidden')

    def test_account_cannot_fetch_future_question_or_reference_answers_before_unlock(self):
        account = self._account(topics=['account-topic-a'])
        token = self._login(account)['identity_token']
        state = self._state(token)
        body = json.dumps(state)
        self.assertNotIn('REFERENCE_ONLY', body)
        self.assertNotIn('EXPLANATION::', body)
        self.assertNotIn('practice-1', body)
        self._request('POST', '/api/select?topic_id=account-topic-a', token,
                      self._payload(token, step_id='forged-future-practice'), 403, 'locked_step')
        self._request('GET', '/api/tasks/lesson:account-topic-a/answers', token, status=404)

    def test_account_manual_pause_and_precise_local_unlock_are_idempotent(self):
        account = self._account()
        token = self._login(account)['identity_token']
        before = self._state(token)
        payload = {'request_id': str(uuid.uuid4())}
        paused = self._request('POST', '/api/pause?topic_id=account-topic-a', token, payload)
        replay = self._request('POST', '/api/pause?topic_id=account-topic-a', token, payload)
        self.assertEqual(paused['status'], 'paused')
        self.assertEqual(replay['revision'], paused['revision'])
        pause = paused['pause']
        learner = self.app.state.learning_services[(account['account']['learner_id'], 'account-topic-a')]
        for topic, module, event in [('wrong-topic', pause['module_id'], pause['event_id']),
                                     ('account-topic-a', 'wrong-module', pause['event_id']),
                                     ('account-topic-a', pause['module_id'], 'wrong-event')]:
            with self.assertRaises(DomainError):
                learner.unlock(topic, module, event)
        self.assertEqual(learner.state()['status'], 'paused')
        other = self._account('Other learner')
        other_service = LearningService(self.topics['account-topic-a'], self.root, self.judge, other['account']['learner_id'])
        with self.assertRaises(DomainError):
            other_service.unlock('account-topic-a', pause['module_id'], pause['event_id'])
        unlocked = learner.unlock('account-topic-a', pause['module_id'], pause['event_id'])
        self.assertEqual(unlocked['status'], 'in_progress')
        self.assertNotEqual(unlocked['attempt_id'], before['attempt_id'])
        self.assertEqual(learner.unlock('account-topic-a', pause['module_id'], pause['event_id'])['revision'], unlocked['revision'])
        response = self.client.post('/api/unlock', json={}, headers=self._headers(token))
        self.assertIn(response.status_code, {404, 405})
        self.assertEqual(learner.state()['revision'], unlocked['revision'])

    def test_account_pause_during_judgement_discards_late_result_and_preserves_permission_boundary(self):
        account = self._account()
        token = self._login(account)['identity_token']
        self._action(token, 'choose', choice='mastered')
        submitted = self._action(token, 'submit', answer='blocked-correct')
        self.assertTrue(self.judge.started.wait(3))
        paused = self._request('POST', '/api/pause?topic_id=account-topic-a', token, {'request_id': str(uuid.uuid4())})
        self.judge.release.set()
        settled = self._poll(token, submitted['submission_id'])
        self.assertEqual(settled['status'], 'discarded')
        self.assertEqual(settled['state']['status'], 'paused')
        self.assertIsNone(settled['state']['modules'][0]['mastery'])
        self.assertEqual(settled['state']['pause']['event_id'], paused['pause']['event_id'])

    def test_course_choice_and_progress_are_shared_across_devices_but_separate_between_courses(self):
        account = self._account()
        one, two = self._login(account)['identity_token'], self._login(account)['identity_token']
        self._request('POST', '/api/catalog/select', one, {'course_id': 'account-course-a'})
        state_a = self._action(one, 'choose', choice='mastered')
        self._request('POST', '/api/catalog/select', two, {'course_id': 'account-course-b'})
        self._action(two, 'choose', 'account-topic-b', choice='unmastered')
        self.assertEqual(self._request('GET', '/api/catalog', one)['selected_course_id'], 'account-course-b')
        self._restart()
        self.assertEqual(self._request('GET', '/api/catalog', one)['selected_course_id'], 'account-course-b')
        self.assertEqual(self._state(two)['active_step_id'], state_a['active_step_id'])
        self.assertEqual(self._state(one, 'account-topic-b')['steps'][0]['phase'], 'unmastered')

    def test_web_profile_cannot_change_account_purpose_batch_enabled_or_permissions(self):
        account = self._account(batch_id='immutable-fixture-label')
        token = self._login(account)['identity_token']
        for payload in ({'purpose': 'learner'}, {'batch_id': 'forged'}, {'enabled': False},
                        {'role': 'administrator'}, {'features': []}, {'topic_ids': []}):
            self._request('POST', '/api/profile', token, payload, status=422)
        access = self._request('GET', '/api/access', token)
        self.assertEqual(access['purpose'], 'test')
        self.assertTrue(access['enabled'])
        self.assertEqual(set(access['features']), set(STANDARD_FEATURES))

    def test_parallel_duplicate_submission_from_two_devices_judges_and_scores_once(self):
        account = self._account()
        one, two = self._login(account)['identity_token'], self._login(account)['identity_token']
        self._action(one, 'choose', choice='mastered')
        payload = self._payload(one, answer='blocked-correct', elapsed_ms=500)
        def submit(token):
            return self.client.post('/api/submit?topic_id=account-topic-a', json=payload, headers=self._headers(token))
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(submit, (one, two)))
        self.assertTrue(all(response.status_code == 200 for response in responses), [response.text for response in responses])
        self.assertEqual({response.json()['submission_id'] for response in responses}, {payload['request_id']})
        self.assertTrue(self.judge.started.wait(3))
        self.judge.release.set()
        result = self._poll(two, payload['request_id'])
        self.assertEqual(len(self.judge.calls), 1)
        self.assertEqual(result['state']['modules'][0]['mastery'], 4)
        with closing(self.store._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM submissions WHERE learner=?',
                                         (account['account']['learner_id'],)).fetchone()[0], 1)

    def test_concurrent_stale_devices_cannot_both_advance_the_same_revision(self):
        account = self._account()
        one, two = self._login(account)['identity_token'], self._login(account)['identity_token']
        first = self._payload(one, choice='mastered')
        second = {**first, 'request_id': str(uuid.uuid4()), 'choice': 'unmastered'}
        def choose(pair):
            token, payload = pair
            return self.client.post('/api/choose?topic_id=account-topic-a', json=payload, headers=self._headers(token))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(choose, [(one, first), (two, second)]))
        self.assertEqual(sorted(result.status_code for result in results), [200, 409])
        failed = next(result for result in results if result.status_code == 409)
        self.assertEqual(failed.json()['detail']['code'], 'stale_revision')
        self.assertEqual(self._state(one)['revision'], 1)

    def test_public_login_and_logout_keep_origin_transport_and_no_admin_access(self):
        account = self._account()
        self.settings.public_api_origin = 'https://accounts-fixture.trycloudflare.com'
        self.settings.allowed_origins = ('https://accounts-fixture.github.io',)
        self._restart()
        with TestClient(self.app, base_url=self.settings.public_api_origin) as client:
            origin = {'Origin': self.settings.allowed_origins[0]}
            bad = client.post('/api/access/login', json={'invitation_code': account['invitation_code']}, headers={'Origin': 'https://attacker.invalid'})
            self.assertEqual(bad.status_code, 403)
            login = client.post('/api/access/login', json={'invitation_code': account['invitation_code']}, headers=origin)
            self.assertEqual(login.status_code, 200, login.text)
            token = login.json()['identity_token']
            headers = {**origin, 'X-Learning-Identity': token}
            self.assertEqual(client.get('/api/state?topic_id=account-topic-a', headers=headers).status_code, 200)
            admin = client.post('/api/access/permissions', json={}, headers=headers)
            self.assertEqual(admin.status_code, 403)
            logout = client.post('/api/access/logout', json={}, headers=headers)
            self.assertEqual(logout.status_code, 200, logout.text)
            self.assertEqual(logout.headers['Access-Control-Allow-Origin'], origin['Origin'])
            self.assertEqual(client.get('/api/access', headers=headers).json()['detail']['code'], 'invalid_identity')

    def test_full_backup_restore_keeps_account_sessions_permissions_labels_and_learning_history(self):
        account = self._account(topics=['account-topic-a'], batch_id='backup-fixture')
        token = self._login(account)['identity_token']
        self._complete(token)
        self._request('POST', '/api/profile', token, {'timezone': 'Asia/Shanghai'})
        self._request('POST', '/api/catalog/select', token, {'course_id': 'account-course-a'})
        archive = self.root / 'accounts-backup.zip'
        backup_site(self.settings, archive)
        restored = self.root / 'restored-accounts'
        restore_site(archive, restored)
        settings = Settings.load(restored)
        catalog = Catalog.load(settings)
        store = AccessStore(restored, [{'id': topic['id'], 'title': topic['title']} for topic in catalog.topics.values()])
        access = store.resolve(token)
        self.assertEqual(access['learner_id'], account['account']['learner_id'])
        self.assertEqual(access['purpose'], 'test')
        self.assertTrue(access['enabled'])
        self.assertEqual([topic['id'] for topic in access['topics']], ['account-topic-a'])
        renewed = store.login(account['invitation_code'])
        self.assertEqual(renewed['access']['learner_id'], access['learner_id'])
        service = LearningService(catalog.topic('account-topic-a'), restored, learner_id=access['learner_id'])
        self.assertEqual(service.state()['status'], 'completed')
        with closing(store._connect()) as con:
            self.assertEqual(con.execute('SELECT batch_id FROM access_principals WHERE learner_id=?', (access['learner_id'],)).fetchone()[0], 'backup-fixture')
            self.assertEqual(con.execute('SELECT timezone,selected_course_id FROM learner_preferences WHERE learner_id=?',
                                         (access['learner_id'],)).fetchone()[:], ('Asia/Shanghai', 'account-course-a'))


class AccountMigrationAcceptanceTests(unittest.TestCase):
    def test_old_account_and_guest_records_migrate_without_renewing_or_losing_identity(self):
        with temporary_test_directory() as folder:
            root = Path(folder)
            topic = synthetic_course(1)
            LearningService(topic, root)
            created = datetime.now(timezone.utc) - timedelta(days=3)
            expired_created = datetime.now(timezone.utc) - timedelta(days=31)
            tokens = {'active-account': 'legacy-active-secret', 'expired-account': 'legacy-expired-secret', 'guest': 'legacy-guest-secret'}
            invitations = {'active-account': 'legacy-active-invitation', 'expired-account': 'legacy-expired-invitation'}
            with closing(sqlite3.connect(root / 'learning.sqlite3')) as con:
                con.executescript('''CREATE TABLE access_principals (
                    learner_id TEXT PRIMARY KEY,role TEXT NOT NULL,display_name TEXT NOT NULL,
                    features TEXT NOT NULL,topic_ids TEXT NOT NULL,invitation_digest TEXT UNIQUE,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                    CREATE TABLE access_identities(token_digest TEXT PRIMARY KEY,learner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL);''')
                for learner, token in tokens.items():
                    role = 'guest' if learner == 'guest' else 'account'
                    moment = expired_created if learner == 'expired-account' else created
                    invitation = credential_digest('invitation', invitations[learner]) if learner in invitations else None
                    con.execute('INSERT INTO access_principals VALUES (?,?,?,?,?,?,?,?)',
                                (learner, role, learner, json.dumps(list(FEATURES)), json.dumps([topic['id']]), invitation,
                                 moment.isoformat(), moment.isoformat()))
                    con.execute('INSERT INTO access_identities VALUES (?,?,?)',
                                (credential_digest('identity', token), learner, moment.isoformat()))
                con.commit()
            store = AccessStore(root, [{'id': topic['id'], 'title': topic['title']}])
            access = store.resolve(tokens['active-account'])
            self.assertEqual(access['learner_id'], 'active-account')
            self.assertEqual(access['purpose'], 'learner')
            self.assertTrue(access['enabled'])
            with self.assertRaises(DomainError) as caught:
                store.resolve(tokens['expired-account'])
            self.assertEqual(caught.exception.code, 'identity_expired')
            self.assertEqual(store.resolve(tokens['guest'])['role'], 'guest')
            with closing(store._connect()) as con:
                account_row = con.execute('SELECT expires_at FROM access_identities WHERE learner_id=?', ('active-account',)).fetchone()
                self.assertEqual(datetime.fromisoformat(account_row['expires_at']), created + timedelta(days=30))
                self.assertIsNone(con.execute('SELECT expires_at FROM access_identities WHERE learner_id=?', ('guest',)).fetchone()[0])
                self.assertIsNone(con.execute('SELECT batch_id FROM access_principals WHERE learner_id=?', ('active-account',)).fetchone()[0])
            reopened = AccessStore(root, [{'id': topic['id'], 'title': topic['title']}])
            self.assertEqual(reopened.resolve(tokens['active-account'])['learner_id'], 'active-account')
            self.assertEqual(reopened.login(invitations['active-account'])['access']['learner_id'], 'active-account')


if __name__ == '__main__':
    unittest.main()
