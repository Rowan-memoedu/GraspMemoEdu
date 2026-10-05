"""Private administrator replies, student delivery, retries and unread revisions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import unittest
import uuid
from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import DomainError, LearningService
from graspmemoedu.site_backup import backup_site, restore_site
from tests import test_help_requests as helpers
from tests.fixtures import DeterministicJudge


class HelpReplyTests(unittest.TestCase):
    setUp = helpers.HelpRequestTests.setUp
    identity = helpers.HelpRequestTests.identity
    payload = helpers.HelpRequestTests.payload
    send = helpers.HelpRequestTests.send

    def center(self, headers=None, suffix=''):
        return self.client.get('/api/help-center' + suffix, headers=headers or self.headers)

    def reply_payload(self, report, **updates):
        return {'report_id': report, 'request_id': str(uuid.uuid4()), 'message': '  A useful answer.\nSecond line.  ',
                'expected_revision': 0, **updates}

    def reply(self, payload, headers=None):
        return self.client.post('/api/help-replies', headers=headers or self.admin_headers, json=payload)

    def seen(self, report, revision=1, headers=None):
        return self.client.post('/api/help-replies/read', headers=headers or self.headers,
                                json={'report_id': report, 'revision': revision})

    def learning_snapshot(self):
        with closing(self.store._connect()) as con:
            tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                      if not row[0].startswith(('help_', 'access_', 'sqlite_'))]
            return {table: sorted(repr(tuple(row)) for row in con.execute('SELECT * FROM "' + table + '"')) for table in tables}

    def test_reply_delivery_read_and_updates_preserve_content_and_learning(self):
        self.client.get('/api/state', headers=self.headers)
        report = self.send(self.payload(message='My question')).json()['id']
        before = self.learning_snapshot()
        pending = self.center().json()
        self.assertEqual(pending['viewer'], 'student')
        self.assertEqual(pending['pending_count'], 1)
        self.assertIsNone(pending['items'][0]['reply'])
        self.assertIn('INTRODUCTION_ONLY', pending['items'][0]['content_html'])
        payload = self.reply_payload(report)
        response = self.reply(payload)
        self.assertEqual(response.status_code, 200, response.text)
        first = self.center().json()
        self.assertEqual(first['unread_count'], 1)
        self.assertTrue(first['items'][0]['unread'])
        self.assertEqual(first['items'][0]['reply']['message'], payload['message'].strip())
        self.assertEqual(first['items'][0]['request']['message'], 'My question')
        self.assertEqual(self.seen(report).status_code, 200)
        self.assertEqual(self.center().json()['unread_count'], 0)
        self.assertEqual(self.reply(self.reply_payload(report, expected_revision=1, message='Updated answer')).status_code, 200)
        self.assertEqual(self.seen(report, 1).status_code, 200)
        self.assertEqual(self.center().json()['unread_count'], 1)
        self.assertEqual(self.seen(report, 3).status_code, 404)
        self.assertEqual(self.seen(report, 2).status_code, 200)
        self.assertEqual(self.center().json()['unread_count'], 0)
        self.assertEqual(self.learning_snapshot(), before)

    def test_owner_student_and_qualification_boundaries_include_counts_and_detail(self):
        report = self.send().json()['id']
        other_admin, other_admin_headers = self.identity(is_admin=True)
        _, other_student = self.identity(is_student=True)
        _, nonstudent = self.identity()
        payload = self.reply_payload(report)
        self.assertEqual(self.reply(payload, self.headers).status_code, 403)
        self.assertEqual(self.reply(payload, other_admin_headers).status_code, 404)
        self.assertEqual(self.center(other_student).json()['total'], 0)
        self.assertEqual(self.center(other_admin_headers).json()['total'], 0)
        self.assertEqual(self.center(other_student, '?report_id=' + report).status_code, 404)
        self.assertEqual(self.center(nonstudent).status_code, 403)
        self.assertEqual(self.client.get('/api/help-center').status_code, 401)
        self.assertEqual(self.reply(payload).status_code, 200)
        self.assertEqual(self.seen(report, headers=other_student).status_code, 404)
        self.assertEqual(self.seen(report, headers=self.admin_headers).status_code, 403)
        self.assertEqual(self.reply({**payload, 'learner_id': self.student}).status_code, 422)
        self.app.state.help_requests.assign_admin(self.student, other_admin)
        self.assertEqual(self.center(self.admin_headers).json()['total'], 0)
        self.assertEqual(self.center(other_admin_headers).json()['total'], 1)
        self.assertEqual(self.reply(self.reply_payload(report, expected_revision=1)).status_code, 404)
        self.assertEqual(self.reply(self.reply_payload(report, expected_revision=1), other_admin_headers).status_code, 200)
        self.store.set_permissions(self.student, features=['learn'])
        self.assertEqual(self.center().status_code, 403)
        self.assertEqual(self.seen(report, 2).status_code, 403)

    def test_retries_are_idempotent_and_stale_or_blank_replies_are_rejected(self):
        report = self.send().json()['id']
        payload = self.reply_payload(report)
        first = self.reply(payload)
        self.assertEqual(self.reply(payload).json(), first.json())
        self.assertEqual(self.reply({**payload, 'message': 'Different text'}).status_code, 409)
        self.assertEqual(self.reply(self.reply_payload(report)).status_code, 409)
        second = self.reply(self.reply_payload(report, expected_revision=1, message='Second answer'))
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.reply(payload).json(), first.json())
        self.assertEqual(self.center().json()['items'][0]['reply']['message'], 'Second answer')
        for message in ('', ' \n ', '\x00', 'x' * 4001):
            self.assertEqual(self.reply(self.reply_payload(report, expected_revision=2, message=message)).status_code, 422)
        with closing(self.store._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM help_replies').fetchone()[0], 2)

    def test_concurrent_reply_submissions_cannot_silently_overwrite(self):
        report = self.send().json()['id']
        access = self.store.resolve(self.admin_headers['X-Learning-Identity'])
        def submit(index):
            try:
                self.app.state.help_requests.reply(access, self.reply_payload(report, message='Reply ' + str(index)))
                return 'saved'
            except DomainError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, [1, 2]))
        self.assertCountEqual(outcomes, ['saved', 'help_reply_conflict'])

    def test_read_receipts_do_not_reorder_pagination_or_expose_another_student(self):
        reports = [self.send(self.payload(message=str(i))).json()['id'] for i in range(3)]
        for report in reports: self.assertEqual(self.reply(self.reply_payload(report)).status_code, 200)
        first = self.center(suffix='?limit=1').json()
        summary = self.center(suffix='?summary=true').json()
        self.assertNotIn('items', summary)
        self.assertEqual(summary['unread_count'], 3)
        self.seen(first['items'][0]['id'])
        second = self.center(suffix='?limit=1&offset=1').json()
        third = self.center(suffix='?limit=1&offset=2').json()
        self.assertEqual({i['id'] for result in (first, second, third) for i in result['items']}, set(reports))
        self.assertIsNone(third['next_offset'])

    def test_student_keeps_submitted_snapshot_after_topic_changes_and_legacy_fallback(self):
        report = self.send(self.payload(kind='quality', message='')).json()['id']
        self.reply(self.reply_payload(report, message='<script>Literal reply</script>'))
        topic = self.app.state.catalog.topic(self.course['id'])
        topic['version'] = 'new'; topic['introduction']['html'] = '<p>CHANGED</p>'
        item = self.center(suffix='?report_id=' + report).json()['items'][0]
        self.assertIn('INTRODUCTION_ONLY', item['content_html'])
        self.assertEqual(item['reply']['message'], '<script>Literal reply</script>')
        with self.store._transaction() as con:
            body = json.loads(con.execute('SELECT body FROM help_requests WHERE id=?', (report,)).fetchone()[0])
            body.pop('content_html'); body.pop('math_css')
            con.execute('UPDATE help_requests SET body=? WHERE id=?', (json.dumps(body), report))
        item = self.center(suffix='?report_id=' + report).json()['items'][0]
        self.assertEqual(item['content_fidelity'], 'text_snapshot')
        self.assertIn('INTRODUCTION_ONLY', item['content_text'])

    def test_replies_and_read_revisions_survive_portable_backup_restore(self):
        report = self.send().json()['id']
        self.reply(self.reply_payload(report)); self.seen(report)
        self.reply(self.reply_payload(report, expected_revision=1, message='Updated'))
        archive = self.root / 'replies.zip'; backup_site(self.settings, archive)
        restored = self.root / 'restored'; restore_site(archive, restored)
        with closing(sqlite3.connect(restored / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT revision FROM help_replies ORDER BY revision').fetchall(), [(1,), (2,)])
            self.assertEqual(con.execute('SELECT revision FROM help_reply_reads').fetchone()[0], 1)

    def test_public_routes_keep_origin_and_role_checks(self):
        report = self.send().json()['id']
        settings = Settings(data_dir=self.root, course_path=self.root / 'course.json',
            public_api_origin='https://qa.example', allowed_origins=('https://frontend.example',))
        app = create_app(settings, LearningService(self.course, self.root, DeterministicJudge()))
        with TestClient(app, base_url='https://qa.example') as client:
            student = {**self.headers, 'Origin': 'https://frontend.example'}
            admin = {**self.admin_headers, 'Origin': 'https://frontend.example'}
            self.assertEqual(client.get('/api/help-center?summary=true', headers=student).status_code, 200)
            self.assertEqual(client.post('/api/help-replies', headers=admin, json=self.reply_payload(report)).status_code, 200)
            self.assertEqual(client.post('/api/help-replies/read', headers=student, json={'report_id': report, 'revision': 1}).status_code, 200)
            self.assertEqual(client.post('/api/help-replies', headers=student, json=self.reply_payload(report)).status_code, 403)
            self.assertEqual(client.get('/api/help-center', headers={**admin, 'Origin': 'https://other.example'}).status_code, 403)
            for path in ('help-center', 'help-replies', 'help-replies/read'):
                response = client.options('/api/' + path, headers={'Origin': 'https://frontend.example',
                    'Access-Control-Request-Method': 'POST' if path != 'help-center' else 'GET',
                    'Access-Control-Request-Headers': 'content-type,x-learning-identity'})
                self.assertEqual(response.status_code, 200, response.text)
