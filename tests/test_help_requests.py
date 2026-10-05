"""Student question isolation, exact context, safe retries and durable inbox."""
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.service import DomainError
from graspmemoedu import cli
from tests.fixtures import synthetic_course, DeterministicJudge, temporary_test_directory
from tests.atomic_fixtures import atomic_topic
from graspmemoedu.question_bank import install_bank
from tests.test_question_bank import bank_fixture
from tests.training_fixtures import training_bank
from graspmemoedu.training_content import permission_id


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
        self.app.state.help_requests.set_default_admin(self.admin)

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

    def open_content(self, report_id, headers=None):
        return self.client.get('/api/help-requests?report_id=' + report_id, headers=headers or self.admin_headers)

    def test_exact_block_reader_keeps_submission_html_and_never_opens_learning(self):
        topic = self.app.state.catalog.topic(self.course['id'])
        topic['math_css'] = '.submitted-math {color: navy}'
        q = topic['modules'][0]['practices'][-1]
        for block in ('question', 'explanation'):
            saved = self.send(self.payload(message='', question_id=q['id'], content_block_id=block + ':' + q['id'])).json()
            expected = q['html' if block == 'question' else 'explanation_html']
            q['html' if block == 'question' else 'explanation_html'] = '<p>NEW_CONTENT</p>'
            before = self.client.get('/api/state', headers=self.headers).json()
            self.app.state.catalog.topics.pop(topic['id'])
            try:
                result = self.open_content(saved['id'])
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()['content_html'], expected)
                self.assertEqual(result.json()['math_css'], topic['math_css'])
                self.assertEqual(result.json()['content_fidelity'], 'snapshot')
                self.assertEqual(result.json()['request']['content_block_id'], block + ':' + q['id'])
            finally:
                self.app.state.catalog.topics[topic['id']] = topic
            self.assertEqual(self.client.get('/api/state', headers=self.headers).json(), before)
        self.assertNotIn('content_html', self.client.get('/api/help-requests', headers=self.admin_headers).text)

    def test_exact_block_reader_enforces_current_owner_and_admin_identity(self):
        saved = self.send().json()['id']
        other_admin, other_headers = self.identity(is_admin=True)
        self.assertEqual(self.open_content(saved, self.headers).status_code, 403)
        self.assertEqual(self.open_content(saved, other_headers).status_code, 404)
        self.assertEqual(self.open_content('missing').status_code, 404)
        self.assertEqual(self.client.get('/api/help-requests?report_id=' + saved).status_code, 401)
        self.app.state.help_requests.assign_admin(self.student, other_admin)
        self.assertEqual(self.open_content(saved).status_code, 404)
        self.assertEqual(self.open_content(saved, other_headers).status_code, 200)

    def test_legacy_reader_recovers_only_verified_original_body_without_writing(self):
        saved = self.send().json()['id']
        with self.store._transaction() as con:
            body = json.loads(con.execute('SELECT body FROM help_requests WHERE id=?', (saved,)).fetchone()[0])
            body.pop('content_html'); body.pop('math_css')
            original = json.dumps(body)
            con.execute('UPDATE help_requests SET body=? WHERE id=?', (original, saved))
        self.assertEqual(self.open_content(saved).json()['content_fidelity'], 'verified_source')
        topic = self.app.state.catalog.topic(self.course['id'])
        topic['introduction']['html'] = '<p>NEW_BODY_WITH_SAME_VERSION</p>'
        result = self.open_content(saved).json()
        self.assertEqual(result['content_fidelity'], 'text_snapshot')
        self.assertNotIn('content_html', result)
        self.assertIn('INTRODUCTION_ONLY', result['content_text'])
        topic['version'] = 'new'
        self.assertEqual(self.open_content(saved).json()['content_fidelity'], 'text_snapshot')
        with closing(self.store._connect()) as con:
            self.assertEqual(con.execute('SELECT body FROM help_requests WHERE id=?', (saved,)).fetchone()[0], original)

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

    def test_help_uses_the_presented_bank_revision_without_changing_learning(self):
        revision = install_bank(bank_fixture(self.course), self.course, self.root)
        state = self.client.get('/api/state', headers=self.headers).json()
        step = state['steps'][0]
        self.assertEqual(step['content_version'], revision)
        self.assertNotEqual(revision, self.course['version'])
        for field in ('question', 'explanation'):
            response = self.send(self.payload(message='', question_id=step['question_id'],
                content_block_id=f"{field}:{step['question_id']}", content_version=step['content_version']))
            self.assertEqual(response.status_code, 200, response.text)
            detail = self.open_content(response.json()['id']).json()
            self.assertEqual(detail['request']['content_version'], revision)
            self.assertEqual(detail['content_fidelity'], 'snapshot')
        items = self.client.get('/api/help-requests', headers=self.admin_headers).json()['items']
        question = next(item for item in items if item['request']['content_block_id'].startswith('question:'))
        explanation = next(item for item in items if item['request']['content_block_id'].startswith('explanation:'))
        self.assertIn('Choose one.', question['content_text'])
        self.assertNotIn('Choose one.', explanation['content_text'])
        self.assertTrue(all(item['request']['content_version'] == revision for item in items))
        self.assertEqual(self.client.get('/api/state', headers=self.headers).json(), state)

    def test_pinned_bank_help_survives_active_revision_change_and_rejects_unbound_versions(self):
        bank = bank_fixture(self.course)
        revision = install_bank(bank, self.course, self.root)
        state = self.client.get('/api/state', headers=self.headers).json()
        step = state['steps'][0]
        bank['questions'][step['question_id']]['html'] += '<p>New variant.</p>'
        newer = install_bank(bank, self.course, self.root)
        self.assertNotEqual(newer, revision)
        payload = self.payload(question_id=step['question_id'],
            content_block_id=f"question:{step['question_id']}", content_version=step['content_version'])
        self.assertEqual(self.send(payload).status_code, 200)
        item = self.client.get('/api/help-requests', headers=self.admin_headers).json()['items'][0]
        self.assertIn('Choose one.', item['content_text'])
        self.assertNotIn('New variant.', item['content_text'])
        self.assertEqual(self.send(self.payload(question_id=step['question_id'],
            content_block_id=f"question:{step['question_id']}")).status_code, 200)
        for updates in ({'content_version': 'f' * 64},
                        {'content_version': '../active'},
                        {'question_id': None, 'content_block_id': 'introduction'}):
            response = self.send({**payload, 'request_id': str(uuid.uuid4()), **updates})
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get('/api/state', headers=self.headers).json(), state)

    def test_help_rejects_a_modified_or_wrongly_bound_bank(self):
        bank = bank_fixture(self.course)
        revision = install_bank(bank, self.course, self.root)
        state = self.client.get('/api/state', headers=self.headers).json()
        step = state['steps'][0]
        payload = self.payload(question_id=step['question_id'],
            content_block_id=f"question:{step['question_id']}", content_version=revision)
        path = self.root / 'question-banks' / self.course['id'] / (revision + '.json')
        for field, value in [('course_version', 'other-topic-version'), ('math_css', 'modified')]:
            original = dict(bank)
            original[field] = value
            path.write_text(json.dumps(original), encoding='utf-8')
            self.assertEqual(self.send(payload).status_code, 409)
        self.assertEqual(self.client.get('/api/help-requests', headers=self.admin_headers).json()['total'], 0)

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

    def test_quality_and_help_share_optional_message_storage_and_owner_boundary(self):
        _, other_admin = self.identity(is_admin=True)
        for message in ('', '  Improve this explanation.  '):
            payload = self.payload(kind='quality', message=message)
            response = self.send(payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.send(payload).json(), response.json())
            self.assertEqual(self.send({**payload, 'kind': 'help'}).status_code, 409)
        items = self.client.get('/api/help-requests', headers=self.admin_headers).json()['items']
        self.assertEqual({item['request']['kind'] for item in items}, {'quality'})
        self.assertEqual({item['request']['message'] for item in items}, {'', 'Improve this explanation.'})
        self.assertTrue(all(item['content_block_type'] == 'introduction' for item in items))
        self.assertTrue(all('INTRODUCTION_ONLY' in item['content_text'] for item in items))
        self.assertEqual(self.client.get('/api/help-requests', headers=other_admin).json()['total'], 0)
        self.store.set_permissions(self.student, is_student=False)
        self.assertEqual(self.send(self.payload(kind='quality')).status_code, 403)

    def test_old_help_requests_remain_idempotent_after_default_fields_are_added(self):
        payload = self.payload(message='')
        response = self.send(payload)
        with self.store._transaction() as con:
            row = con.execute('SELECT id,body FROM help_requests').fetchone()
            body = json.loads(row['body'])
            for key in ('kind', 'bank_id', 'group_id'): body['request'].pop(key, None)
            con.execute('UPDATE help_requests SET body=? WHERE id=?', (json.dumps(body), row['id']))
        self.assertEqual(self.send(payload).json(), response.json())
        self.assertEqual(self.send({**payload, 'kind': 'quality'}).status_code, 409)

    def test_training_stems_and_explanations_validate_bank_permissions_and_snapshot(self):
        bank = training_bank()
        directory = self.root / 'training-banks'; directory.mkdir()
        (directory / 'isolated-bank.json').write_text(json.dumps(bank), encoding='utf-8')
        # Reload the application exactly as after a private bank installation.
        app = create_app(self.settings, LearningService(self.course, self.root, DeterministicJudge()))
        with TestClient(app, base_url='http://127.0.0.1:8767') as client:
            headers = {**self.headers, 'X-Learning-Session': client.get('/api/session', headers=self.headers).json()['session_token']}
            admin_headers = {**self.admin_headers, 'X-Learning-Session': headers['X-Learning-Session']}
            payload = self.payload(kind='quality', message='', topic_id=permission_id(bank['id']),
                bank_id=bank['id'], group_id='group-a', question_id='essay',
                content_block_id='question:essay', content_version=bank['version'])
            send = lambda values, suffix='': client.post('/api/help-requests' + suffix, headers=headers, json=values)
            before = client.get('/api/training/history?bank_id=isolated-bank&question_id=essay', headers=headers).json()
            for block in ('question', 'explanation'):
                response = send({**payload, 'request_id': str(uuid.uuid4()), 'content_block_id': block + ':essay'})
                self.assertEqual(response.status_code, 200, response.text)
            items = client.get('/api/help-requests', headers=admin_headers).json()['items']
            self.assertEqual({item['content_block_type'] for item in items}, {'question', 'explanation'})
            self.assertTrue(all(item['course_title'] == bank['title'] and item['topic_title'] == '第一题组' for item in items))
            self.assertTrue(all(item['content_block_title'] == '隔离习题 essay' for item in items))
            self.assertIn('一加一等于二', next(item['content_text'] for item in items if item['content_block_type'] == 'explanation'))
            for update, status in [({'content_version': 'old'}, 409), ({'topic_id': self.course['id']}, 422),
                    ({'content_block_id': 'introduction'}, 422), ({'group_id': 'group-b'}, 404),
                    ({'bank_id': None}, 422)]:
                self.assertEqual(send({**payload, 'request_id': str(uuid.uuid4()), **update}).status_code, status)
            self.assertEqual(send({**payload, 'request_id': str(uuid.uuid4())}, '?subject_id=english').status_code, 403)
            app.state.access_store.set_permissions(self.student, topic_ids=[self.course['id']])
            self.assertEqual(send({**payload, 'request_id': str(uuid.uuid4())}).status_code, 403)
            self.assertEqual(client.get('/api/training/history?bank_id=isolated-bank&question_id=essay', headers=headers).status_code, 403)
            app.state.access_store.set_permissions(self.student, reset_topics=True)
            self.assertEqual(client.get('/api/training/history?bank_id=isolated-bank&question_id=essay', headers=headers).json(), before)

    def test_v2_introduction_reports_point_to_one_section_only(self):
        topic = self.app.state.catalog.topic(self.course['id'])
        topic['topic_format_version'] = 2
        topic['introductions'] = [{'id': 'first', 'title': 'First section', 'html': '<p>FIRST_SECTION</p>'},
                                  {'id': 'second', 'title': 'Second section', 'html': '<p>SECOND_SECTION</p>'}]
        payload = self.payload(kind='quality', message='', content_block_id='introduction:second')
        self.assertEqual(self.send(payload).status_code, 200)
        item = self.client.get('/api/help-requests', headers=self.admin_headers).json()['items'][0]
        self.assertEqual(item['content_text'], 'SECOND_SECTION')
        self.assertEqual(item['content_block_title'], 'Second section')
        detail = self.open_content(item['id']).json()
        self.assertEqual(detail['content_html'], '<p>SECOND_SECTION</p>')
        self.assertEqual(self.send(self.payload(content_block_id='introduction:missing')).status_code, 422)

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
            self.assertEqual(con.execute('SELECT admin_id FROM help_admin_config WHERE id=1').fetchone()[0], self.admin)

    def test_atomic_chunks_cards_and_explanations_use_the_same_request_contract(self):
        topic = atomic_topic()
        atomic_root = self.root / 'atomic'; atomic_root.mkdir()
        (atomic_root / 'atomic.json').write_text(json.dumps(topic), encoding='utf-8')
        (atomic_root / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'english-course', 'title': 'Synthetic atomic', 'topics': [{'id': topic['id'], 'path': 'atomic.json'}]}]}), encoding='utf-8')
        (atomic_root / 'subjects.json').write_text(json.dumps({'subjects': [{'id': 'english', 'course_ids': ['english-course']}]}), encoding='utf-8')
        app = create_app(Settings.load(atomic_root))
        account = app.state.access_store.create_account('Atomic student', is_student=True)['account']
        with app.state.access_store._transaction() as con:
            admin = app.state.access_store._insert_account(con, 'Atomic admin', is_admin=True)['account']
        app.state.help_requests.set_default_admin(admin['learner_id'])
        for block, question in [('chunk:colors', None), ('question:a-1', 'a-1'), ('explanation:a-1', 'a-1')]:
            values = self.payload(topic_id=topic['id'], content_version=topic['version'], content_block_id=block, question_id=question)
            saved = app.state.help_requests.save(account, values, 'english')
            self.assertEqual(saved['status'], 'saved')
            detail = app.state.help_requests.content(admin, saved['id'])
            self.assertEqual(detail['request']['content_block_id'], block)
            self.assertEqual(detail['content_fidelity'], 'snapshot')

    def test_administrator_one_alone_sees_default_students_including_new_students(self):
        other_admin, other_headers = self.identity(is_admin=True)
        _, third_headers = self.identity(is_admin=True)
        self.assertEqual(self.send().status_code, 200)
        future, future_headers = self.identity(is_student=True, is_advanced_learner=True)
        self.assertEqual(self.send(headers=future_headers).status_code, 200)
        owner = self.client.get('/api/help-requests', headers=self.admin_headers).json()
        self.assertEqual(owner['total'], 2)
        self.assertEqual({item['learner_id'] for item in owner['items']}, {self.student, future})
        for headers in (other_headers, third_headers):
            response = self.client.get('/api/help-requests?admin_id=' + self.admin + '&learner_id=' + self.student, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {'items': [], 'total': 0, 'next_offset': None})
        assignments = self.app.state.help_requests.assignments()
        self.assertEqual(assignments['default_admin_id'], self.admin)
        self.assertTrue(all(row['admin_id'] == self.admin and row['uses_default'] for row in assignments['items']))
        self.assertEqual(self.send({**self.payload(), 'admin_id': other_admin}).status_code, 422)

    def test_assignment_filters_records_counts_and_pagination_and_moves_existing_requests(self):
        other_admin, other_headers = self.identity(is_admin=True)
        other_student, other_student_headers = self.identity(is_student=True)
        self.assertEqual(self.send().status_code, 200)
        for _ in range(2): self.assertEqual(self.send(headers=other_student_headers).status_code, 200)
        with closing(sqlite3.connect(self.store.db_path)) as con:
            before = con.execute('SELECT * FROM help_requests ORDER BY id').fetchall()
        self.app.state.help_requests.assign_admin(other_student, other_admin)
        first = self.client.get('/api/help-requests?limit=1', headers=self.admin_headers).json()
        self.assertEqual(first['total'], 1); self.assertIsNone(first['next_offset'])
        self.assertEqual(first['items'][0]['learner_id'], self.student)
        second = self.client.get('/api/help-requests?limit=1', headers=other_headers).json()
        self.assertEqual(second['total'], 2); self.assertEqual(second['next_offset'], 1)
        next_page = self.client.get('/api/help-requests?limit=1&offset=1&admin_id=' + self.admin, headers=other_headers).json()
        self.assertEqual(next_page['total'], 2); self.assertIsNone(next_page['next_offset'])
        self.assertEqual({item['learner_id'] for item in second['items'] + next_page['items']}, {other_student})
        self.app.state.help_requests.assign_admin(other_student)
        self.assertEqual(self.client.get('/api/help-requests', headers=other_headers).json()['total'], 0)
        self.assertEqual(self.client.get('/api/help-requests', headers=self.admin_headers).json()['total'], 3)
        with closing(sqlite3.connect(self.store.db_path)) as con:
            self.assertEqual(con.execute('SELECT * FROM help_requests ORDER BY id').fetchall(), before)

    def test_no_configuration_and_disabled_owner_never_expose_or_reroute_requests(self):
        _, other_headers = self.identity(is_admin=True)
        self.assertEqual(self.send().status_code, 200)
        with self.store._transaction() as con: con.execute('DELETE FROM help_admin_config')
        for headers in (self.admin_headers, other_headers):
            self.assertEqual(self.client.get('/api/help-requests', headers=headers).json()['total'], 0)
        self.assertEqual(self.send().status_code, 503)
        self.app.state.help_requests.set_default_admin(self.admin)
        self.assertEqual(self.client.get('/api/help-requests', headers=self.admin_headers).json()['total'], 1)
        self.store.set_enabled(self.admin, False)
        self.assertEqual(self.send().status_code, 503)
        self.assertEqual(self.client.get('/api/help-requests', headers=other_headers).json()['total'], 0)

    def test_invalid_owners_are_rejected_and_cli_can_assign_and_restore_default(self):
        helper = self.app.state.help_requests
        ordinary, _ = self.identity()
        for admin in (self.student, ordinary, 'missing'):
            with self.assertRaises(DomainError): helper.set_default_admin(admin)
            with self.assertRaises(DomainError): helper.assign_admin(self.student, admin)
        with self.assertRaises(DomainError): helper.assign_admin(ordinary, self.admin)
        self.assertEqual(helper.assignments()['default_admin_id'], self.admin)
        other_admin, _ = self.identity(is_admin=True)
        def invoke(*args):
            stream = io.StringIO()
            with redirect_stdout(stream), patch.object(sys, 'argv', ['graspmemoedu', '--data-dir', str(self.root), *args]):
                cli.main()
            return json.loads(stream.getvalue())
        self.assertEqual(invoke('help-admin-default', '--admin', self.admin)['default_admin_id'], self.admin)
        self.assertEqual(invoke('help-assign', '--learner', self.student, '--admin', other_admin)['admin_id'], other_admin)
        listing = invoke('help-assignments')
        self.assertNotIn('invitation', json.dumps(listing)); self.assertNotIn('identity_token', json.dumps(listing))
        self.assertEqual(listing['items'][0]['admin_id'], other_admin)
        restored = invoke('help-assign', '--learner', self.student, '--default-admin')
        self.assertEqual(restored['admin_id'], self.admin); self.assertTrue(restored['uses_default'])
        helper.assign_admin(self.student, other_admin)
        archive = self.root / 'assigned.zip'; backup_site(self.settings, archive)
        restored_root = self.root / 'assigned-restored'; restore_site(archive, restored_root)
        with closing(sqlite3.connect(restored_root / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT admin_id FROM help_admin_assignments WHERE learner_id=?', (self.student,)).fetchone()[0], other_admin)
