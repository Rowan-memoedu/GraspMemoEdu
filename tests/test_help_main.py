"""Global main conversations share the existing message engine and retain ownership."""
from contextlib import closing
import uuid
import unittest
from tests import test_help_threads as helpers


class MainConversationTests(unittest.TestCase):
    setUp = helpers.HelpThreadTests.setUp
    identity = helpers.HelpThreadTests.identity
    send = helpers.HelpThreadTests.send
    payload = helpers.HelpThreadTests.payload
    center = helpers.HelpThreadTests.center
    learning_snapshot = helpers.HelpThreadTests.learning_snapshot
    def message(self, report=None, headers=None, **updates):
        return self.client.post('/api/help-main/messages', headers=headers or self.admin_headers,
            json={'report_id': report or self.student, 'request_id': str(uuid.uuid4()), 'message': '**回答** $x^2$', **updates})

    def read(self, report, sequence, headers=None, version=None):
        return self.client.post('/api/help-main/read', headers=headers or self.headers,
            json={'report_id': report, 'sequence': sequence, 'content_version': version})

    def item(self, report=None, headers=None):
        response = self.center(headers, '?learner_id=' + (report or self.student))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['main_thread']

    def test_main_without_reports_roundtrip_edits_read_and_learning_unchanged(self):
        before = self.learning_snapshot()
        empty = self.item()
        self.assertEqual(empty['messages'], [])
        first = self.message().json()['message']
        follow = self.message(headers=self.headers, reply_to=first['id'], message='追问 \\(x\\)').json()['message']
        self.assertEqual(self.message(message_id=first['id'], expected_revision=1, message='**修改**').status_code, 200)
        self.assertEqual([m['message'] for m in self.item()['messages']], ['**修改**', '追问 \\(x\\)'])
        self.assertEqual(self.item()['messages'][1]['reply_to'], first['id'])
        self.assertTrue(self.item()['message_unread'])
        self.assertEqual(self.read(self.student, self.item()['thread_sequence']).status_code, 200)
        self.assertFalse(self.item()['message_unread'])
        self.assertEqual(self.learning_snapshot(), before)
        with closing(self.store._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM help_requests').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM help_main_versions').fetchone()[0], 3)

    def test_roster_zero_reports_owner_filters_and_reassignment(self):
        other, other_headers = self.identity(is_student=True)
        outsider, outsider_headers = self.identity(is_admin=True)
        _, nonstudent = self.identity()
        roster = self.center(self.admin_headers).json()['students']
        self.assertEqual({r['learner_id'] for r in roster}, {self.student, other})
        self.assertEqual(self.center(outsider_headers).json()['students'], [])
        for headers, expected in [(outsider_headers, 404), (other_headers, 404), (nonstudent, 403)]:
            self.assertEqual(self.message(headers=headers).status_code, expected)
            self.assertEqual(self.center(headers, '?learner_id=' + self.student).status_code, expected)
        self.message()
        self.app.state.help_requests.assign_admin(self.student, outsider)
        self.assertEqual(self.message().status_code, 404)
        self.assertEqual(self.message(headers=outsider_headers).status_code, 200)
        self.assertEqual(len(self.item(headers=outsider_headers)['messages']), 2)
        self.store.set_permissions(self.student, features=['learn'])
        self.assertEqual(self.message(headers=self.headers).status_code, 403)

    def test_namespace_parent_edit_retry_and_read_boundaries(self):
        first = self.message().json()['message']
        ident = str(uuid.uuid4())
        result = self.message(request_id=ident)
        self.assertEqual(self.message(request_id=ident).json(), result.json())
        self.assertEqual(self.message(request_id=ident, message='different').status_code, 409)
        self.assertEqual(self.message(headers=self.headers, message_id=first['id'], expected_revision=1).status_code, 403)
        self.assertEqual(self.message(message_id=first['id'], expected_revision=0).status_code, 409)
        other, _ = self.identity(is_student=True)
        self.assertEqual(self.message(other, reply_to=first['id']).status_code, 404)
        self.assertEqual(self.read(other, self.item()['thread_sequence'], self.admin_headers).status_code, 404)
        self.assertEqual(self.read(self.student, 999999).status_code, 404)
        self.assertEqual(self.read(self.student, 0, version='topic-version').status_code, 422)
        report = self.send().json()['id']
        self.assertEqual(self.client.post('/api/help-messages', headers=self.headers,
            json={'report_id': report, 'request_id': str(uuid.uuid4()), 'message': 'x', 'reply_to': first['id']}).status_code, 404)
        for message in ['', '  ', '\x00', 'x' * 4001]:
            self.assertEqual(self.message(message=message).status_code, 422)

    def test_global_summary_and_selected_history_do_not_mix_learners(self):
        report = self.send().json()['id']
        other, other_headers = self.identity(is_student=True)
        other_report = self.send(headers=other_headers).json()['id']
        self.message()
        self.assertEqual(self.center(suffix='?summary=true').json()['unread_count'], 1)
        selected = self.center(self.admin_headers, '?learner_id=' + self.student).json()
        self.assertEqual([i['id'] for i in selected['items']], [report])
        self.assertEqual(self.center(self.admin_headers, '?learner_id=' + self.student + '&report_id=' + other_report).status_code, 404)

    def test_markdown_indentation_retained_in_main_and_content_threads(self):
        raw = '    indented code\n\n$ x_1 $\n  '
        self.assertEqual(self.message(message=raw).json()['message']['message'], raw)
        self.assertEqual(self.item()['messages'][0]['message'], raw)
        report = self.send().json()['id']
        response = self.client.post('/api/help-messages', headers=self.headers,
            json={'report_id': report, 'request_id': str(uuid.uuid4()), 'message': raw})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['message']['message'], raw)

