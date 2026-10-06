"""Own-report authorization, private roster names and shared course hierarchy."""
from contextlib import closing
import json
import sqlite3
import unittest

from tests import test_student_reports as reports
from graspmemoedu.admin_names import roster_names, sync_admin_names
from graspmemoedu.access import credential_digest
from graspmemoedu.course_hierarchy import pending_hierarchy, project_hierarchy
from graspmemoedu.service import DomainError


class PersonalProgressTests(unittest.TestCase):
    setUp = reports.StudentReportsTests.setUp
    identity = reports.StudentReportsTests.identity
    snapshot = reports.StudentReportsTests.snapshot
    training = reports.StudentReportsTests.training
    answer = reports.StudentReportsTests.answer
    learner = reports.StudentReportsTests.learner
    action = staticmethod(reports.StudentReportsTests.action)

    def label(self, name='Private roster label'):
        with closing(self.store._connect()) as con:
            digest = con.execute('SELECT invitation_digest FROM access_principals WHERE learner_id=?', (self.student,)).fetchone()[0]
        return sync_admin_names(self.store, {'names': [{'invitation_digest': digest, 'admin_name': name}]})

    def test_self_uses_shared_report_without_admin_names_or_foreign_records(self):
        self.label()
        response = self.client.post('/api/profile', headers=self.headers, json={'display_name': 'My nickname'})
        self.assertEqual(response.status_code, 200)
        self.answer(self.training(), answer='2')
        before = self.snapshot()
        own = self.client.get('/api/student-report', headers=self.headers, params={'learner_id': self.second}).json()
        admin = self.client.get('/api/admin/students/' + self.student, headers=self.admin_headers).json()
        self.assertEqual(own['student']['learner_id'], self.student)
        self.assertEqual(own['student']['display_name'], 'My nickname')
        self.assertEqual(admin['student']['display_name'], 'Private roster label')
        self.assertEqual(own['analytics']['lifetime'], admin['analytics']['lifetime'])
        self.assertNotIn('admin_name', json.dumps(own)); self.assertNotIn('Private roster label', json.dumps(own))
        for suffix, params in [('questions', {'kind': 'notebook'}), ('history', {'kind': 'training', 'container': 'isolated-bank', 'question': 'exact'})]:
            a = self.client.get('/api/student-report/' + suffix, headers=self.headers, params=params)
            b = self.client.get('/api/admin/students/' + self.student + '/' + suffix, headers=self.admin_headers, params=params)
            self.assertEqual(a.status_code, 200); self.assertEqual(a.json(), b.json())
        self.assertEqual(self.snapshot(), before)

    def test_self_routes_do_not_grant_admin_or_nonstudent_access(self):
        for suffix in ['', '/questions?kind=notebook', '/history?kind=topic&container=atoms']:
            self.assertEqual(self.client.get('/api/student-report' + suffix, headers=self.normal_headers).status_code, 403)
            self.assertEqual(self.client.get('/api/student-report' + suffix, headers=self.admin_headers).status_code, 403)
            self.assertEqual(self.client.get('/api/student-report' + suffix, headers={'Origin': 'https://site.test'}).status_code, 401)
        self.assertEqual(self.client.get('/api/admin/students/' + self.second, headers=self.headers).status_code, 403)
        self.assertEqual(self.client.get('/api/student-report/' + self.second, headers=self.headers).status_code, 403)
        with self.assertRaises(DomainError):
            self.app.state.admin_students.detail(self.store.resolve(self.headers['X-Learning-Identity']), self.second, own=True)

    def test_name_import_is_atomic_private_and_does_not_change_nickname(self):
        before = self.snapshot()
        with closing(self.store._connect()) as con:
            digest = con.execute('SELECT invitation_digest FROM access_principals WHERE learner_id=?', (self.student,)).fetchone()[0]
        payload = {'names': [{'invitation_digest': digest, 'admin_name': 'Edited roster name'}]}
        self.assertEqual(sync_admin_names(self.store, payload, True)['changed'], 1)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(DomainError):
            sync_admin_names(self.store, {'names': payload['names'] + [{'invitation_digest': '0' * 64, 'admin_name': 'Unknown'}]})
        self.assertEqual(self.snapshot(), before)
        sync_admin_names(self.store, payload)
        self.assertEqual(self.client.get('/api/access', headers=self.headers).json()['display_name'], 'Student <A>')
        for endpoint in ('access', 'profile', 'student-report'):
            text = self.client.get('/api/' + endpoint, headers=self.headers).text
            self.assertNotIn('Edited roster name', text); self.assertNotIn('admin_name', text)
        search = self.client.get('/api/admin/students', headers=self.admin_headers, params={'q': 'Edited roster'}).json()
        self.assertEqual(search['items'][0]['display_name'], 'Edited roster name')
        self.assertEqual(self.app.state.help_requests._admin_name(self.student), 'Edited roster name')
        self.assertEqual(sync_admin_names(self.store, payload)['changed'], 0)

    def test_roster_parser_hashes_codes_and_rejects_conflicting_duplicates(self):
        code = 'synthetic_invitation_0123456789012345'
        result = roster_names('# Test\n\nFormat: name: code\n姓名：' + code + '\n')
        self.assertEqual(result['names'], [{'invitation_digest': credential_digest('invitation', code), 'admin_name': '姓名'}])
        self.assertNotIn(code, json.dumps(result))
        with self.assertRaises(DomainError): roster_names('A:' + code + '\nB:' + code)
        with self.assertRaises(DomainError): roster_names('No invitation entries')

    def test_report_and_learning_project_the_same_implicit_root_and_real_chapters(self):
        topic = self.course['id']
        course = self.app.state.admin_students.catalog.courses['math']
        for units, expected in [([{'id': 'math', 'title': 'Course', 'parent_id': None, 'topic_ids': [topic]}], []),
                                ([{'id': 'math', 'title': 'Course', 'parent_id': None, 'topic_ids': []},
                                  {'id': 'chapter', 'title': 'Real chapter', 'parent_id': 'math', 'topic_ids': [topic]}], ['chapter'])]:
            course['units'] = units
            tasks = [{'type': 'Lesson', 'course_id': 'math', 'topic_id': topic}]
            projected = pending_hierarchy(units, tasks)
            self.assertEqual(projected, project_hierarchy(units, {topic}, {'math'}))
            for headers, url in [(self.headers, '/api/student-report'), (self.admin_headers, '/api/admin/students/' + self.student)]:
                data = self.client.get(url, headers=headers).json()['analytics']
                report_course = next(c for c in data['courses'] if c['id'] == 'math')
                self.assertEqual(report_course['units'], projected)
                self.assertEqual([u['id'] for u in projected], expected)
                self.assertTrue(any(t['topic_id'] == topic for t in data['topics']))
                if projected: self.assertIsNone(projected[0]['parent_id'])

    def test_atomic_unmastered_history_keeps_existing_learner_lock(self):
        service = self.learner('atoms')
        state = service.choose(self.action(service.state(), choice='learn'))
        state = service.advance(self.action(state))
        submission = service.submit(self.action(state, answer='Wrong', elapsed_ms=10))
        service.process_submission(submission['submission_id'])
        params = {'kind': 'topic', 'container': 'atoms'}
        own = self.client.get('/api/student-report/history', headers=self.headers, params=params).json()['items'][0]
        admin = self.client.get('/api/admin/students/' + self.student + '/history', headers=self.admin_headers, params=params).json()['items'][0]
        self.assertTrue(own['locked']); self.assertFalse(own['content_available'])
        self.assertEqual(own['title'], own['question'])
        self.assertIsNone(own['answer']); self.assertNotIn('html', own); self.assertNotIn('explanation_html', own)
        self.assertTrue(admin['content_available']); self.assertEqual(admin['answer'], 'Wrong')
        detail = self.client.get('/api/student-report', headers=self.headers).json()
        for item in detail['reviews']:
            if item['kind'] == 'atomic_point' and not item['mastered']:
                self.assertEqual(item['title'], item['id'])
        for item in detail['atomic_cards']:
            if not all(point['mastered'] for point in item['points']): self.assertEqual(item['title'], item['id'])
        questions = self.client.get('/api/student-report/questions', headers=self.headers, params=params).json()['items']
        self.assertTrue(all(item['title'] == item['question'] for item in questions))
