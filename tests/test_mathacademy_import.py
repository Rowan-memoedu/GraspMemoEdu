"""Independent synthetic sources: shared identity, atomic install and safe reading."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
import zipfile

from fastapi.testclient import TestClient
from graspmemoedu.mathacademy_import import SourceIndex, Assets, Renderer, adapt, prepare, install, source_fingerprint
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.course_graph import GraphStore
from graspmemoedu.api import create_app
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.question_types import validate_interaction, public_interaction, parse_answer, deterministic_verdict
from graspmemoedu.service import create_learning_service
from tests.fixtures import temporary_test_directory, DeterministicJudge
from tests.test_ordered_learning import ordered_topic, payload, active


def source(tid):
    return {'lesson': {'topic': {'id': tid, 'name': 'Synthetic ' + str(tid)}, 'taskId': 123,
        'steps': [{'id': 10, 'number': 1, 'type': 'tutorial', 'contentId': 100 + tid,
                   'tutorial': {'id': 100 + tid, 'title': 'Synthetic guidance', 'content': '> ```python\n> if x < 4:\n>     x += 1\n> ```', 'html': ''}},
                  {'id': 11, 'number': 2, 'type': 'example', 'contentId': 200 + tid,
                   'example': {'id': 200 + tid, 'title': 'Synthetic example', 'question': 'Read $x^2$.', 'explanation': '', 'calculator': 'None'}},
                  {'id': 12, 'number': 3, 'type': 'question', 'contentId': 300 + tid,
                   'question': {'id': 300 + tid, 'exampleId': 200 + tid, 'text': 'Fill the number of faces.', 'format': 'FR',
                                'freeResponse': {'segments': [{'textbox': {'answer': 'PRIVATE-CANDIDATE', 'type': 'number'}}, {'text': ' face(s)'}]}}}]}}


def composite():
    return {'version': 2, 'type': 'composite', 'grading': 'exact',
        'controls': [{'id': f'c{i}', 'type': 'select', 'options': [{'id': 'yes', 'text': 'Yes'}, {'id': 'no', 'text': 'No'}], 'answers': ['yes']} for i in range(23)],
        'sections': [{'id': f's{i}', 'html': '<p>Section</p>', 'complete': True,
                      'control_ids': [f'c{n}' for n in range(23) if n % 10 == i]} for i in range(10)]}


class MathAcademyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_test_directory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.data = self.root / 'site'; self.cache = self.root / 'cache'; self.package = self.root / 'package'
        self.topics = self.root / 'topics.zip'; self.graphs = self.root / 'graphs.zip'
        with zipfile.ZipFile(self.topics, 'w') as z:
            for tid in (1, 2, 3):
                z.writestr(f'topic-json/topic-{tid}.json', json.dumps(source(tid)))
        with zipfile.ZipFile(self.graphs, 'w') as z:
            for cid, tids in [(10, [1, 2]), (20, [2, 3])]:
                nodes = {str(tid): {'id': tid, 'name': 'Synthetic ' + str(tid),
                    'course': {'id': cid, 'name': 'Course ' + str(cid)}, 'unit': {'id': 1, 'number': 1, 'name': 'Unit'},
                    'module': {'id': 1, 'number': 1, 'name': 'Module'}, 'prerequisites': [1] if tid == 2 else []} for tid in tids}
                z.writestr(f'course-knowledge-graph/course-{cid}.json', json.dumps({'topics': nodes}))
        self.index = SourceIndex(self.topics, self.graphs)
        self.addCleanup(self.index.archive.close)

    def prepared(self):
        prepare(self.index, self.index.scope([20]), self.cache, self.package)

    def test_scope_is_explicit_graph_closure_and_fingerprint_ignores_capture_state(self):
        self.assertEqual(self.index.scope([20])['topic_ids'], [1, 2, 3])
        a, b = source(1), source(1); b['lesson']['taskId'] = 999
        b['lesson']['steps'][2]['question']['rendered'] = True
        self.assertEqual(source_fingerprint(a), source_fingerprint(b))
        b['lesson']['steps'][0]['tutorial']['content'] = 'Changed teaching'
        self.assertNotEqual(source_fingerprint(a), source_fingerprint(b))

    def test_atomic_activation_idempotency_shared_identity_and_backup(self):
        self.prepared()
        with self.assertRaisesRegex(RuntimeError, 'pre-activation'):
            install(self.package, self.data, fail_at='activate')
        self.assertFalse((self.data / 'active-content.json').exists())
        result = install(self.package, self.data)
        self.assertEqual(result['status'], 'installed_with_source_gaps')
        self.assertEqual(result['trainable'], 0)
        before = (self.data / 'active-content.json').read_bytes()
        self.assertEqual(install(self.package, self.data)['status'], 'unchanged')
        self.assertEqual(before, (self.data / 'active-content.json').read_bytes())
        settings = Settings(data_dir=self.data); catalog = Catalog.load(settings)
        self.assertEqual(catalog.topic_courses['ma-topic-2'], {'ma-course-10', 'ma-course-20'})
        self.assertIsNone(catalog.course_context('ma-topic-2')['id'])
        self.assertEqual(GraphStore(settings, catalog).dependencies()['ma-topic-2'], ['ma-topic-1'])
        reader = create_learning_service(catalog.topic('ma-topic-2'), self.data)
        self.assertTrue(reader.state()['read_only'])
        self.assertNotIn('PRIVATE-CANDIDATE', json.dumps(reader.state()))
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM topics').fetchone()[0], 0)
        result = backup_site(settings, self.root / 'backup.zip')
        restore_site(self.root / 'backup.zip', self.root / 'restored')
        restored = Catalog.load(Settings(data_dir=self.root / 'restored'))
        self.assertEqual(restored.topics, catalog.topics)
        self.assertEqual(restored.topic_courses, catalog.topic_courses)

    def test_reader_and_catalog_enforce_test_purpose_and_qualification(self):
        self.prepared(); install(self.package, self.data)
        settings = Settings(data_dir=self.data, public_api_origin='https://source.test', allowed_origins=('https://site.test',))
        app = create_app(settings)
        with TestClient(app, base_url='https://source.test') as client:
            headers = {'Origin': 'https://site.test'}
            guest = client.post('/api/access/guest', headers=headers, json={}).json()
            headers['X-Learning-Identity'] = guest['identity_token']
            self.assertEqual(client.get('/api/catalog', headers=headers).json()['courses'], [])
            for flags, allowed in [({'purpose': 'test'}, False), ({'purpose': 'learner', 'is_advanced_learner': True, 'is_student': True}, False),
                                   ({'purpose': 'test', 'is_advanced_learner': True, 'is_student': True}, True),
                                   ({'purpose': 'learner', 'is_admin': True}, False), ({'purpose': 'test', 'is_admin': True}, True)]:
                with app.state.access_store._transaction() as con:
                    account = app.state.access_store._insert_account(con, 'Synthetic', **flags)
                login = client.post('/api/access/login', headers=headers, json={'invitation_code': account['invitation_code']})
                self.assertEqual(login.status_code, 200, login.text)
                headers['X-Learning-Identity'] = login.json()['identity_token']
                response = client.get('/api/state?topic_id=ma-topic-2&subject_id=math', headers=headers)
                self.assertEqual(response.status_code, 200 if allowed else 403, response.text[:200])
                if allowed:
                    self.assertTrue(response.json()['read_only'])
                    self.assertEqual(response.json()['learner_id'], login.json()['access']['learner_id'])
                    dashboard = client.get('/api/dashboard?course_id=ma-course-20&subject_id=math', headers=headers)
                    self.assertEqual(dashboard.status_code, 200, dashboard.text[:300])
                    self.assertEqual(dashboard.json()['tasks'], [])
                    self.assertEqual({t['status'] for t in dashboard.json()['topics']}, {'read_only'})

    def test_composite_submission_requires_all_ten_sections_and_hides_keys(self):
        spec = composite(); validate_interaction(spec)
        question = {'interaction': spec}; public = public_interaction(question)
        self.assertNotIn('answers', json.dumps(public))
        answer = {'section': 9, 'values': {f'c{i}': 'yes' for i in range(23)}}
        self.assertTrue(deterministic_verdict(question, json.dumps(answer))[0])
        answer['section'] = 8
        with self.assertRaises(ValueError):
            parse_answer(question, json.dumps(answer))
        spec['sections'][9]['complete'] = False
        with self.assertRaises(ValueError):
            validate_interaction(spec)

    def test_trailing_guidance_survives_mastered_path_refresh_and_replay(self):
        topic = ordered_topic()
        topic['introductions'].append({'id': 'tail', 'title': 'Final proof', 'html': '<p>Proof</p>'})
        topic['content_order'].append({'kind': 'introduction', 'id': 'tail'})
        service = create_learning_service(topic, self.data, DeterministicJudge())
        for _ in topic['modules']:
            service.choose(payload(service, choice='mastered'))
            result = service.submit(payload(service, answer='correct')); service.process_submission(result['submission_id'])
            service.advance(payload(service))
        self.assertEqual(service.state()['status'], 'in_progress')
        self.assertEqual(active(service.state())['introduction_id'], 'tail')
        service = create_learning_service(topic, self.data, DeterministicJudge())
        self.assertEqual(active(service.state())['introduction_id'], 'tail')
        action = payload(service); service.advance(action); service.advance(action)
        self.assertEqual(service.state()['status'], 'completed')
        with closing(service._connect()) as con:
            state = json.loads(con.execute('SELECT body FROM topics').fetchone()[0])
            self.assertEqual(state['read_introductions'].count('tail'), 1)


if __name__ == '__main__':
    unittest.main()
