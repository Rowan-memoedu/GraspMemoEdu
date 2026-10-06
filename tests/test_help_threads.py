"""Two-party conversations, revisions, unread races and Topic update delivery."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import unittest
import uuid
from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import DomainError, LearningService
from graspmemoedu.site_backup import backup_site, restore_site
from tests import test_help_replies as helpers
from tests.fixtures import DeterministicJudge


class HelpThreadTests(unittest.TestCase):
    setUp = helpers.HelpReplyTests.setUp
    identity = helpers.HelpReplyTests.identity
    payload = helpers.HelpReplyTests.payload
    send = helpers.HelpReplyTests.send
    center = helpers.HelpReplyTests.center
    learning_snapshot = helpers.HelpReplyTests.learning_snapshot

    def message(self, report, headers=None, **updates):
        return self.client.post('/api/help-messages', headers=headers or self.admin_headers,
            json={'report_id': report, 'request_id': str(uuid.uuid4()), 'message': 'A reply', **updates})

    def read(self, report, sequence, headers=None, version=None):
        return self.client.post('/api/help-messages/read', headers=headers or self.headers,
            json={'report_id': report, 'sequence': sequence, 'content_version': version})

    def item(self, report, headers=None):
        response = self.center(headers, '?report_id=' + report)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['items'][0]

    def test_multi_round_conversation_and_individual_edits_preserve_learning(self):
        self.client.get('/api/state', headers=self.headers)
        before = self.learning_snapshot()
        report = self.send().json()['id']
        first = self.message(report, message='First answer').json()['message']
        second = self.message(report, self.headers, reply_to=first['id'], message='Follow-up question').json()['message']
        third = self.message(report, reply_to=second['id'], message='Second answer').json()['message']
        self.assertEqual(self.message(report, self.headers, message_id=second['id'], expected_revision=1,
            message='Edited follow-up').status_code, 200)
        self.assertEqual(self.message(report, message_id=first['id'], expected_revision=1,
            message='Edited first answer').status_code, 200)
        item = self.item(report)
        self.assertEqual([m['message'] for m in item['messages']], ['Edited first answer', 'Edited follow-up', 'Second answer'])
        self.assertEqual([m['editable'] for m in item['messages']], [False, True, False])
        self.assertEqual(item['messages'][1]['reply_to'], first['id'])
        self.assertEqual(item['messages'][2]['id'], third['id'])
        self.assertEqual(self.learning_snapshot(), before)
        with closing(self.store._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM help_message_versions').fetchone()[0], 5)

    def test_author_report_assignment_feature_and_subject_boundaries(self):
        report = self.send().json()['id']
        message = self.message(report).json()['message']
        self.assertEqual(self.message(report, self.headers, message_id=message['id'], expected_revision=1).status_code, 403)
        other_admin, admin_headers = self.identity(is_admin=True)
        _, student_headers = self.identity(is_student=True)
        _, nonstudent = self.identity()
        for headers, status in [(admin_headers, 404), (student_headers, 404), (nonstudent, 403)]:
            self.assertEqual(self.message(report, headers).status_code, status)
            self.assertEqual(self.read(report, 1, headers).status_code, status)
            self.assertEqual(self.client.get('/api/help-content?report_id=' + report, headers=headers).status_code, status)
        self.assertEqual(self.client.post('/api/help-messages', headers=self.headers,
            json={'report_id': report, 'request_id': str(uuid.uuid4()), 'message': 'wrong subject'},
            params={'subject_id': 'physics'}).status_code, 404)
        self.app.state.help_requests.assign_admin(self.student, other_admin)
        self.assertEqual(self.message(report).status_code, 404)
        self.assertEqual(self.message(report, admin_headers).status_code, 200)
        self.store.set_permissions(self.student, features=['learn'])
        self.assertEqual(self.message(report, self.headers).status_code, 403)

    def test_retry_conflict_invalid_parents_and_empty_messages(self):
        report = self.send().json()['id']
        request = str(uuid.uuid4())
        first = self.message(report, request_id=request)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.message(report, request_id=request).json(), first.json())
        self.assertEqual(self.message(report, request_id=request, message='Different').status_code, 409)
        ident = first.json()['message']['id']
        edited = self.message(report, message_id=ident, expected_revision=1, message='Edited')
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(self.message(report, message_id=ident, expected_revision=1).status_code, 409)
        self.assertEqual(self.message(report, request_id=request).json(), first.json())
        other = self.send().json()['id']
        self.assertEqual(self.message(other, reply_to=ident).status_code, 404)
        for text in ('', '  ', '\x00', 'x' * 4001):
            self.assertEqual(self.message(report, message=text).status_code, 422)

    def test_concurrent_edits_conflict_and_appends_remain_distinct(self):
        report = self.send().json()['id']
        first = self.message(report).json()['message']
        access = self.store.resolve(self.admin_headers['X-Learning-Identity'])
        def edit(index):
            try:
                self.app.state.help_requests.threads.send(access, {'report_id': report, 'request_id': str(uuid.uuid4()),
                    'message_id': first['id'], 'message': str(index), 'expected_revision': 1})
                return 'saved'
            except DomainError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(edit, [1, 2])), ['saved', 'help_reply_conflict'])
        def append(index):
            return self.app.state.help_requests.threads.send(access, {'report_id': report, 'request_id': str(uuid.uuid4()), 'message': str(index)})
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(len({r['message']['id'] for r in pool.map(append, [3, 4])}), 2)

    def test_unread_both_sides_and_stale_reads_do_not_swallow_new_events(self):
        report = self.send().json()['id']
        first = self.message(report).json()['message']
        sequence = self.item(report)['thread_sequence']
        self.assertEqual(self.center().json()['unread_count'], 1)
        self.assertEqual(self.center(self.admin_headers).json()['unread_count'], 0)
        self.assertEqual(self.read(report, sequence).status_code, 200)
        self.assertEqual(self.center().json()['unread_count'], 0)
        self.message(report, self.headers, reply_to=first['id'])
        admin = self.item(report, self.admin_headers)
        self.assertTrue(admin['unread'])
        self.read(report, admin['thread_sequence'], self.admin_headers)
        self.message(report, message_id=first['id'], expected_revision=1, message='Modified')
        self.read(report, sequence)
        self.assertEqual(self.center().json()['unread_count'], 1)
        self.assertEqual(self.read(report, 99999).status_code, 404)
        second_report = self.send().json()['id']
        self.assertEqual(self.read(second_report, sequence).status_code, 404)

    def test_topic_update_badge_original_snapshot_preview_and_stale_receipts(self):
        report = self.send(self.payload(kind='quality')).json()['id']
        initial = self.center(suffix='?summary=true').json()['activity_key']
        self.assertFalse(self.item(report)['content_updated'])
        topic = self.app.state.catalog.topic(self.course['id'])
        topic['version'] = 'updated-one'; topic['introduction']['html'] = '<p>UPDATED EXACT BLOCK</p>'
        item = self.item(report)
        self.assertTrue(item['content_updated']); self.assertTrue(item['content_update_unread'])
        self.assertIn('INTRODUCTION_ONLY', item['content_html'])
        self.assertNotEqual(initial, self.center(suffix='?summary=true').json()['activity_key'])
        current = self.client.get('/api/help-content?report_id=' + report, headers=self.headers)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()['content_html'], '<p>UPDATED EXACT BLOCK</p>')
        self.read(report, 0, version='updated-one')
        self.assertFalse(self.item(report)['unread']); self.assertTrue(self.item(report)['content_updated'])
        topic['version'] = 'updated-two'
        self.read(report, 0, version='updated-one')
        self.assertTrue(self.item(report)['content_update_unread'])
        self.read(report, 0, version='updated-two'); self.read(report, 0, version='updated-one')
        self.assertFalse(self.item(report)['content_update_unread'])
        self.store.set_permissions(self.student, topic_ids=[])
        self.assertEqual(self.client.get('/api/help-content?report_id=' + report, headers=self.headers).status_code, 403)
        self.assertEqual(self.center().status_code, 200)

    def test_old_reply_upgrade_preserves_versions_read_receipts_and_is_idempotent(self):
        report = self.send().json()['id']
        helpers.HelpReplyTests.reply(self, helpers.HelpReplyTests.reply_payload(self, report))
        helpers.HelpReplyTests.seen(self, report)
        helpers.HelpReplyTests.reply(self, helpers.HelpReplyTests.reply_payload(self, report, expected_revision=1, message='Updated legacy'))
        self.message(report, self.headers, reply_to='legacy-' + report, message='New follow-up')
        app = create_app(self.settings, LearningService(self.course, self.root, DeterministicJudge()))
        app2 = create_app(self.settings, LearningService(self.course, self.root, DeterministicJudge()))
        with closing(self.store._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM help_messages').fetchone()[0], 2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM help_message_versions').fetchone()[0], 3)
        self.assertTrue(app2.state.help_requests.center(self.store.resolve(self.headers['X-Learning-Identity']))['items'][0]['unread'])
        # An old read receipt acknowledges only the legacy answer, not new messages.
        self.message(report, message='Another answer')
        helpers.HelpReplyTests.seen(self, report, 2)
        self.assertTrue(self.item(report)['unread'])
        self.assertIsNotNone(app.state.help_requests)

    def test_messages_and_update_receipts_survive_backup_restore(self):
        report = self.send().json()['id']
        message = self.message(report).json()['message']
        self.message(report, self.headers, reply_to=message['id'])
        self.read(report, self.item(report)['thread_sequence'])
        archive = self.root / 'threads.zip'; backup_site(self.settings, archive)
        restored = self.root / 'restored'; restore_site(archive, restored)
        settings = Settings.load(restored)
        app = create_app(settings, LearningService(self.course, restored, DeterministicJudge()))
        access = app.state.access_store.resolve(self.headers['X-Learning-Identity'])
        result = app.state.help_requests.center(access)
        self.assertEqual(len(result['items'][0]['messages']), 2)
        self.assertFalse(result['items'][0]['unread'])

    def test_new_routes_public_origin_and_identity_boundary(self):
        report = self.send().json()['id']
        settings = Settings(data_dir=self.root, course_path=self.root / 'course.json',
            public_api_origin='https://qa.example', allowed_origins=('https://frontend.example',))
        app = create_app(settings, LearningService(self.course, self.root, DeterministicJudge()))
        with TestClient(app, base_url='https://qa.example') as client:
            headers = {**self.headers, 'Origin': 'https://frontend.example'}
            for path in ('help-messages', 'help-messages/read', 'help-content'):
                self.assertEqual(client.options('/api/' + path, headers={'Origin': 'https://frontend.example',
                    'Access-Control-Request-Method': 'GET' if path == 'help-content' else 'POST',
                    'Access-Control-Request-Headers': 'content-type,x-learning-identity'}).status_code, 200)
            self.assertEqual(client.post('/api/help-messages', headers=headers,
                json={'report_id': report, 'request_id': str(uuid.uuid4()), 'message': 'Student follow-up'}).status_code, 200)
            self.assertEqual(client.get('/api/help-content', params={'report_id': report}, headers=headers).status_code, 200)
            self.assertEqual(client.get('/api/help-content', params={'report_id': report}, headers={'Origin': 'https://frontend.example'}).status_code, 401)
            self.assertEqual(client.post('/api/help-messages', headers={**headers, 'Origin': 'https://wrong.example'},
                json={'report_id': report, 'request_id': str(uuid.uuid4()), 'message': 'Denied'}).status_code, 403)
