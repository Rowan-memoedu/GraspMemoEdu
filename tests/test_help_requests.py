"""Student question isolation, exact context, safe retries and durable inbox."""
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
from graspmemoedu.site_backup import backup_site, restore_site
from tests.fixtures import synthetic_course, DeterministicJudge, temporary_test_directory
from tests.atomic_fixtures import atomic_topic


class HelpRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.course = synthetic_course(1)
        (self.root / 'course.json').write_text(json.dumps(self.course), encoding='utf-8')
        (self.root / 'settings.json').write_text(json.dumps({'course_path': str(self.root / 'course.json')}), encoding='utf-8')
        self.settings = Settings.load(self.root)
        self.app = create_app(self.settings, LearningService(self.course, self.root, DeterministicJudge()))
        self.store = self.app.state.access_store
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8767'); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.student, self.headers = self.identity(is_student=True)
        self.admin, self.admin_headers = self.identity(is_admin=True)

    def identity(self, **flags):
        with self.store._transaction() as con:
            account = self.store._insert_account(con, 'Synthetic <student>', **flags)
        token = self.store.login(account['invitation_code'])['identity_token']
        headers = {'Origin': 'http://127.0.0.1:8767', 'X-Learning-Identity': token}
        headers['X-Learning-Session'] = self.client.get('/api/session', headers=headers).json()['session_token']
        return account['account']['learner_id'], headers

    def payload(self, **values):
        return {'request_id': str(uuid.uuid4()), 'message': ' Please explain <script>question</script>. ',
                'topic_id': self.course['id'], 'content_block_id': 'introduction',
                'content_version': self.course['version'], **values}

    def send(self, payload=None, headers=None, suffix=''):
        return self.client.post('/api/help-requests' + suffix, headers=headers or self.headers, json=payload or self.payload())

    def test_crossed_qualifications_and_explicit_grants_are_checked_server_side(self):
        for advanced, student in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(advanced=advanced, student=student):
                self.store.set_permissions(self.student, is_advanced_learner=advanced, is_student=student)
                response = self.send()
                self.assertEqual(response.status_code, 200 if student else 403)
                self.assertEqual('request_help' in self.client.get('/api/access', headers=self.headers).json()['features'], student)
        self.store.set_permissions(self.student, features=['learn'])
        self.assertEqual(self.send().status_code, 403)
        self.store.set_permissions(self.student, reset_features=True, topic_ids=[])
        self.assertEqual(self.send().status_code, 403)
        guest = self.store.create_guest()
        headers = {**self.headers, 'X-Learning-Identity': guest['identity_token']}
        self.assertEqual(self.send(headers=headers).status_code, 403)
        self.assertEqual(self.send(headers=self.admin_headers).status_code, 200)

    def test_private_inbox_and_retry_preserve_snapshot_without_learning_writes(self):
        before = self.client.get('/api/state', headers=self.headers).json()
        payload = self.payload()
        response = self.send(payload); self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.send(payload).json(), response.json())
        self.assertEqual(self.send({**payload, 'message': 'changed'}).status_code, 409)
        self.assertEqual(self.client.get('/api/help-requests', headers=self.headers).status_code, 403)
        _, other = self.identity(is_student=True)
        self.assertEqual(self.client.get('/api/help-requests', headers=other).status_code, 403)
        self.assertEqual(self.client.get('/api/help-requests').status_code, 401)
        inbox = self.client.get('/api/help-requests', headers=self.admin_headers)
        self.assertEqual(inbox.status_code, 200, inbox.text)
        items = inbox.json()['items']; self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['learner_id'], self.student)
        self.assertEqual(items[0]['request']['message'], payload['message'].strip())
        self.assertIn('INTRODUCTION_ONLY', items[0]['content_text'])
        self.assertEqual(items[0]['course_id'], self.app.state.catalog.topic_courses[self.course['id']])
        self.assertNotIn('identity_token', inbox.text); self.assertNotIn('invitation', inbox.text)
        self.assertNotIn('REFERENCE_ONLY', inbox.text)
        self.assertEqual(self.client.get('/api/state', headers=self.headers).json(), before)
        self.assertEqual(self.client.post('/api/help-requests', headers=self.headers, json={**payload, 'is_student': True}).status_code, 422)

    def test_version_subject_and_content_position_are_validated_before_storage(self):
        for updates, status in [({'content_version': 'old'}, 409),
                ({'message': 'bad\x00'}, 422), ({'content_block_id': 'chunk:unknown'}, 422),
                ({'question_id': 'unknown', 'content_block_id': 'question:unknown'}, 422),
                ({'question_id': 'module-1-example', 'content_block_id': 'introduction'}, 422)]:
            with self.subTest(updates=updates): self.assertEqual(self.send(self.payload(**updates)).status_code, status)
        self.assertEqual(self.send(suffix='?subject_id=english').status_code, 403)
        for block in ('question', 'explanation'):
            response = self.send(self.payload(question_id='module-1-example', content_block_id=block + ':module-1-example'))
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/api/help-requests', headers=self.admin_headers).json()['total'], 2)

    def test_empty_whitespace_and_omitted_messages_save_context_and_retry_once(self):
        for message in ('', ' \n\t ', None):
            payload = self.payload(message=message)
            if message is None: payload.pop('message')
            response = self.send(payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.send(payload).json(), response.json())
        inbox = self.client.get('/api/help-requests', headers=self.admin_headers).json()
        self.assertEqual(inbox['total'], 3)
        for item in inbox['items']:
            self.assertEqual(item['request']['message'], '')
            self.assertEqual(item['request']['content_block_id'], 'introduction')
            self.assertEqual(item['request']['content_version'], self.course['version'])
            self.assertIn('INTRODUCTION_ONLY', item['content_text'])
            self.assertEqual(item['learner_id'], self.student)

    def test_rate_limit_does_not_block_replays_and_pagination_and_backup_survive(self):
        payloads = [self.payload(message='Synthetic ' + str(index)) for index in range(3)]
        for payload in payloads: self.assertEqual(self.send(payload).status_code, 200)
        self.assertEqual(self.send().status_code, 429)
        self.assertEqual(self.send(payloads[0]).status_code, 200)
        first = self.client.get('/api/help-requests?limit=2', headers=self.admin_headers).json()
        second = self.client.get('/api/help-requests?limit=2&offset=2', headers=self.admin_headers).json()
        self.assertEqual(first['total'], 3); self.assertEqual(first['next_offset'], 2)
        self.assertIsNone(second['next_offset'])
        self.assertEqual(len({item['id'] for item in first['items'] + second['items']}), 3)
        archive = self.root / 'backup.zip'; backup_site(self.settings, archive)
        restored = self.root / 'restored'; restore_site(archive, restored)
        with closing(sqlite3.connect(restored / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM help_requests').fetchone()[0], 3)

    def test_atomic_chunks_cards_and_explanations_use_the_same_request_contract(self):
        topic = atomic_topic()
        atomic_root = self.root / 'atomic'; atomic_root.mkdir()
        (atomic_root / 'atomic.json').write_text(json.dumps(topic), encoding='utf-8')
        (atomic_root / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'english-course', 'title': 'Synthetic atomic', 'topics': [{'id': topic['id'], 'path': 'atomic.json'}]}]}), encoding='utf-8')
        (atomic_root / 'subjects.json').write_text(json.dumps({'subjects': [{'id': 'english', 'course_ids': ['english-course']}]}), encoding='utf-8')
        app = create_app(Settings.load(atomic_root))
        account = app.state.access_store.create_account('Atomic student', is_student=True)['account']
        for block, question in [('chunk:colors', None), ('question:a-1', 'a-1'), ('explanation:a-1', 'a-1')]:
            values = self.payload(topic_id=topic['id'], content_version=topic['version'], content_block_id=block, question_id=question)
            self.assertEqual(app.state.help_requests.save(account, values, 'english')['status'], 'saved')
