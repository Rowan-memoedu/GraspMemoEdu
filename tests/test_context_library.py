from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import unittest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from graspmemoedu.access import AccessStore
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.context_library import ContextLibraryStore, lookup_context, validate_anchor, validate_reading_materials
from graspmemoedu.context_library_api import register_context_library_routes
from graspmemoedu.public_limits import PublicLimits
from graspmemoedu.service import DomainError
from graspmemoedu.subjects import Subjects
from tests.atomic_fixtures import atomic_topic
from tests.fixtures import temporary_test_directory


def reading_fixture():
    topic = atomic_topic()
    topic['reading_materials'] = [{'id': 'article', 'title': 'A shared context',
        'paragraphs': [{'id': 'p1', 'text': '🌱 They used blue paper. Unknown words remain.'}],
        'annotations': [{'id': 'word-blue', 'paragraph_id': 'p1', 'start': 12, 'end': 16,
                         'meaning': 'a colour', 'point_ids': ['b']}]}]
    return topic


def selection(topic, start=12, end=16):
    text = topic['reading_materials'][0]['paragraphs'][0]['text']
    return {'content_version': topic['version'], 'paragraph_id': 'p1',
            'quote': {'exact': text[start:end], 'prefix': text[max(0, start - 50):start],
                      'suffix': text[end:end + 50], 'start': start, 'end': end}}


class ContextLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.topic = reading_fixture(); self.material = self.topic['reading_materials'][0]
        def connect():
            con = sqlite3.connect(self.root / 'contexts.sqlite3'); con.row_factory = sqlite3.Row; return con
        self.connect = connect; self.store = ContextLibraryStore(connect)
        self.access = {'learner_id': 'reader-a'}

    def test_non_bmp_anchor_and_known_point_derive_from_source(self):
        validate_reading_materials(self.topic)
        value = lookup_context(self.topic, self.material, selection(self.topic))
        self.assertEqual(value['quote']['exact'], 'blue')
        self.assertEqual(value['point_ids'], ['b'])
        self.assertEqual(value['context'], self.material['paragraphs'][0]['text'])
        self.assertEqual(value['definitions'][0]['meaning'], 'a colour')

    def test_stale_or_forged_quote_is_rejected(self):
        for key, value in [('content_version', 'stale'), ('quote', {**selection(self.topic)['quote'], 'exact': 'wrong'}),
                           ('quote', {**selection(self.topic)['quote'], 'prefix': ''})]:
            with self.subTest(key=key), self.assertRaises(DomainError):
                validate_anchor(self.topic, self.material, {**selection(self.topic), key: value})

    def test_empty_point_mapping_is_candidate_but_unknown_mapping_rejected(self):
        self.material['annotations'][0]['point_ids'] = []
        validate_reading_materials(self.topic)
        self.assertTrue(lookup_context(self.topic, self.material, selection(self.topic))['candidate'])
        self.material['annotations'][0]['point_ids'] = ['does-not-exist']
        with self.assertRaises(ValueError): validate_reading_materials(self.topic)

    def test_unknown_word_dictionary_preserves_senses_without_automatic_link(self):
        calls = []
        def dictionary(text):
            calls.append(text)
            return {'status': 'available', 'senses': [{'definition': 'not previously known', 'pos': 'a'}], 'license': 'fixture'}
        value = lookup_context(self.topic, self.material, selection(self.topic, 24, 31), dictionary)
        self.assertEqual(calls, ['Unknown']); self.assertEqual(value['point_ids'], [])
        self.assertEqual(value['definitions'][0]['source'], 'dictionary')
        self.assertEqual(value['dictionary']['license'], 'fixture')

    def test_private_records_survive_restart_and_do_not_create_scheduling(self):
        body = {**selection(self.topic, 24, 31), 'action': 'memory', 'note': 'Clarify in this sentence.'}
        saved = self.store.save(self.access, self.topic, self.material, body)
        self.assertEqual(saved['memory_status'], 'candidate')
        restored = ContextLibraryStore(self.connect).records('reader-a', self.topic, self.material)
        self.assertEqual(restored[0]['context'], self.material['paragraphs'][0]['text'])
        self.assertEqual(self.store.records('reader-b', self.topic, self.material), [])
        with closing(self.connect()) as con:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {'reading_contexts'})

    def test_memory_callback_is_idempotent_owner_bridge_and_marks_are_independent(self):
        calls = []
        def activate(access, topic, points, context):
            calls.append((access['learner_id'], topic['id'], points, context['quote']['exact']))
            return {'status': 'pending', 'point_ids': points, 'learning_url': '#/subjects/english/learn'}
        values = {**selection(self.topic), 'action': 'memory'}
        first = self.store.save(self.access, self.topic, self.material, values, activate_points=activate)
        second = self.store.save(self.access, self.topic, self.material, values, activate_points=activate)
        self.assertEqual(first['id'], second['id']); self.assertEqual(len(self.store.records('reader-a', self.topic, self.material)), 1)
        self.assertEqual(calls[0][2], ['b'])
        marked = self.store.save(self.access, self.topic, self.material, {**values, 'action': 'mark'})
        self.assertTrue(marked['marked']); self.assertEqual(marked['memory_status'], 'pending')
        self.assertEqual(len(calls), 2)

    def test_old_version_context_remains_visible_without_reanchoring(self):
        before = deepcopy(self.topic)
        saved = self.store.save(self.access, before, before['reading_materials'][0],
                                {**selection(before), 'action': 'mark', 'note': 'Keep this original context.'})
        self.topic['version'] = 'reading-v2'
        self.material['paragraphs'][0]['text'] = 'An edited paragraph has different offsets.'
        records = self.store.records('reader-a', self.topic, self.material)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]['stale']); self.assertEqual(records[0]['content_version'], before['version'])
        self.assertEqual(records[0]['quote'], saved['quote']); self.assertEqual(records[0]['context'], saved['context'])
        self.assertEqual(records[0]['note'], 'Keep this original context.')
        with self.assertRaises(DomainError):
            validate_anchor(self.topic, self.material, selection(before))


class ContextLibraryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.topic = reading_fixture()
        (self.root / 'test-materials.json').write_text(json.dumps({'schema': 1, 'topics': [self.topic['id']]}), encoding='utf-8')
        self.catalog = Catalog([{'id': 'reading-course', 'title': 'Reading', 'topics': [{'id': self.topic['id'], 'prerequisites': []}]}],
                               {self.topic['id']: self.topic}, self.topic['id'], {self.topic['id']: self.root / 'topic.json'})
        self.subjects = Subjects(Settings(data_dir=self.root), self.catalog,
                                 raw={'subjects': [{'id': 'english', 'course_ids': ['reading-course']}]})
        self.access = AccessStore(self.root, [{'id': self.topic['id'], 'title': self.topic['title']}], test_materials=self.subjects.test_materials)
        self.app = FastAPI(); self.app.state.public_limits = PublicLimits(data_dir=self.root)
        @self.app.exception_handler(DomainError)
        async def domain_error(request, error):
            return JSONResponse({'code': error.code, 'message': error.message}, status_code=error.status_code)
        self.activations = []
        def activate(access, topic, points, context, request):
            self.activations.append((access['learner_id'], points, request.headers.get('x-learning-identity')))
            return {'status': 'pending', 'point_ids': points}
        register_context_library_routes(self.app, self.catalog, self.access, self.subjects, activate_points=activate,
                                        transcriber=getattr(self, 'transcriber', None))
        self.client = TestClient(self.app); self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)
        self.learner, self.headers = self.account(purpose='test', is_advanced_learner=True, is_student=True)
        self.query = '?topic_id=' + self.topic['id'] + '&material_id=article'

    def account(self, **flags):
        with self.access._transaction() as con: account = self.access._insert_account(con, 'Synthetic Reader', **flags)
        login = self.access.login(account['invitation_code'])
        return account['account']['learner_id'], {'X-Learning-Identity': login['identity_token']}

    def test_test_material_access_and_subject_checks_cover_all_endpoints(self):
        for flags in ({}, {'is_admin': True}, {'purpose': 'test'}, {'purpose': 'test', 'is_advanced_learner': True}):
            _, headers = self.account(**flags)
            self.assertEqual(self.client.get('/api/context-library', headers=headers).json()['items'], [])
            self.assertEqual(self.client.get('/api/context-library/material' + self.query, headers=headers).status_code, 403)
            self.assertEqual(self.client.post('/api/context-library/lookup' + self.query, headers=headers, json=selection(self.topic)).status_code, 403)
        good = self.client.get('/api/context-library/material' + self.query, headers=self.headers)
        self.assertEqual(good.status_code, 200, good.text)
        wrong = self.client.get('/api/context-library/material' + self.query + '&subject_id=physics', headers=self.headers)
        self.assertEqual(wrong.status_code, 403)

    def test_owner_isolation_and_exact_request_scope_callback(self):
        body = {**selection(self.topic), 'action': 'memory'}
        saved = self.client.post('/api/context-library/marks' + self.query, headers=self.headers, json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.activations[0], (self.learner, ['b'], self.headers['X-Learning-Identity']))
        _, other = self.account(purpose='test', is_advanced_learner=True, is_student=True)
        self.assertEqual(self.client.get('/api/context-library/material' + self.query, headers=other).json()['records'], [])
        self.assertEqual(len(self.client.get('/api/context-library/material' + self.query, headers=self.headers).json()['records']), 1)

    def test_admin_marks_stay_in_memory_and_separate_sessions(self):
        admin, headers = self.account(purpose='test', is_admin=True)
        body = {**selection(self.topic), 'action': 'mark'}
        response = self.client.post('/api/context-library/marks' + self.query, headers=headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        material = self.client.get('/api/context-library/material' + self.query, headers=headers).json()
        self.assertTrue(material['preview']); self.assertEqual(len(material['records']), 1)
        with closing(self.access._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM reading_contexts WHERE learner=?', (admin,)).fetchone()[0], 0)

    def test_extra_client_point_ids_or_learner_are_rejected(self):
        for field, value in [('point_ids', ['a']), ('learner_id', 'someone-else')]:
            response = self.client.post('/api/context-library/marks' + self.query, headers=self.headers,
                                        json={**selection(self.topic), 'action': 'memory', field: value})
            self.assertEqual(response.status_code, 422)


if __name__ == '__main__': unittest.main()
