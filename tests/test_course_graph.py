"""Independent graph imports, permissions, live refresh and portable recovery."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from math_learning.api import create_app
from math_learning.catalog import Catalog
from math_learning.config import Settings
from math_learning.course_graph import GraphStore, import_graph, preview_html, read_graph, revision, validate_graph
from math_learning.service import LearningService
from math_learning.site_backup import backup_site, restore_site
from tests.fixtures import synthetic_course, DeterministicJudge, temporary_test_directory


class CourseGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.topic = synthetic_course(1)
        self.topic['id'] = 'topic-a'
        self.write(self.data / 'topic.json', self.topic)
        self.write(self.data / 'catalog.json', {'courses': [{'id': 'course-a', 'title': 'Existing course',
            'topics': [{'id': 'topic-a', 'path': 'topic.json'}]}]})
        self.settings = Settings(data_dir=self.data)
        self.source = self.root / 'course-graph.json'
        self.graph = {'schema_version': 1, 'subject_id': 'physics', 'course_id': 'rigid', 'title': 'Graph only',
                      'nodes': [{'topic_id': 'pending-a', 'title': 'First'}, {'topic_id': 'pending-b', 'title': 'Second'}],
                      'edges': [{'from': 'pending-a', 'to': 'pending-b', 'type': 'prerequisite'}]}
        self.write(self.source, self.graph)

    def write(self, path, data):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def test_preview_replace_idempotency_history_and_restore_without_content_changes(self):
        before = {path.name: path.read_bytes() for path in self.data.iterdir()}
        self.assertEqual(import_graph(self.settings, self.source)['status'], 'preview')
        self.assertFalse((self.data / 'graphs').exists())
        first = import_graph(self.settings, self.source, apply=True, expected_revision='none')
        active = Path(first['path'])
        stamp = active.stat().st_mtime_ns
        self.assertEqual(import_graph(self.settings, self.source, apply=True)['status'], 'unchanged')
        self.assertEqual(active.stat().st_mtime_ns, stamp)
        self.graph['edges'] = []
        self.graph['nodes'][0]['title'] = 'New title'
        self.write(self.source, self.graph)
        update = import_graph(self.settings, self.source, apply=True, expected_revision=first['revision'])
        self.assertEqual(len(update['diff']['edges']['removed']), 1)
        self.assertEqual(len(update['diff']['nodes']['changed']), 1)
        self.assertEqual(read_graph(active)['edges'], [])
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            import_graph(self.settings, self.source, apply=True, expected_revision=first['revision'])
        restored = import_graph(self.settings, update['backup'], apply=True)
        self.assertEqual(restored['revision'], first['revision'])
        for name, raw in before.items():
            self.assertEqual((self.data / name).read_bytes(), raw)
        self.assertFalse((self.data / 'learning.sqlite3').exists())

    def test_failed_commit_retains_active_graph_and_can_retry(self):
        first = import_graph(self.settings, self.source, apply=True)
        self.graph['edges'] = []
        self.write(self.source, self.graph)
        with patch('math_learning.course_graph.os.replace', side_effect=OSError('commit failed')):
            with self.assertRaises(OSError):
                import_graph(self.settings, self.source, apply=True)
        self.assertEqual(revision(read_graph(first['path'])), first['revision'])
        self.assertFalse((self.data / '.author-import.lock').exists())
        self.assertEqual(import_graph(self.settings, self.source, apply=True)['status'], 'installed')

    def test_invalid_graphs_fail_before_mutation(self):
        variants = []
        unknown = copy.deepcopy(self.graph); unknown['edges'][0]['from'] = 'missing'; variants.append(unknown)
        cycle = copy.deepcopy(self.graph); cycle['edges'].append({'from': 'pending-b', 'to': 'pending-a', 'type': 'prerequisite'}); variants.append(cycle)
        duplicate = copy.deepcopy(self.graph); duplicate['nodes'].append(duplicate['nodes'][0]); variants.append(duplicate)
        external = copy.deepcopy(self.graph); external['nodes'][0]['kind'] = 'assumed_prerequisite'; variants.append(external)
        unsafe = copy.deepcopy(self.graph); unsafe['course_id'] = '../outside'; variants.append(unsafe)
        path = copy.deepcopy(self.graph); path['nodes'][0]['source'] = 'D:/private/source.md'; variants.append(path)
        unknown_version = copy.deepcopy(self.graph); unknown_version['schema_version'] = 2; variants.append(unknown_version)
        for graph in variants:
            with self.subTest(graph=graph):
                self.write(self.source, graph)
                with self.assertRaises(ValueError):
                    import_graph(self.settings, self.source, apply=True)
        self.assertFalse((self.data / 'graphs').exists())

    def test_cross_course_conflict_and_long_dag(self):
        import_graph(self.settings, self.source, apply=True)
        self.graph['course_id'] = 'another'
        self.write(self.source, self.graph)
        with self.assertRaisesRegex(ValueError, 'another course'):
            import_graph(self.settings, self.source)
        self.graph['nodes'] = [{'topic_id': 'topic-a', 'title': 'Wrong owner'}]; self.graph['edges'] = []
        self.write(self.source, self.graph)
        with self.assertRaisesRegex(ValueError, 'different course'):
            import_graph(self.settings, self.source)
        self.graph['nodes'] = [{'topic_id': f't-{i}', 'title': str(i)} for i in range(1000)]
        self.graph['edges'] = [{'from': f't-{i}', 'to': f't-{i+1}', 'type': 'prerequisite'} for i in range(999)]
        self.assertEqual(len(validate_graph(self.graph)['nodes']), 1000)

    def test_local_preview_is_self_contained_and_escapes_source_text(self):
        self.graph['nodes'][0]['title'] = '</script><script>alert(1)</script>'
        self.write(self.source, self.graph)
        preview = self.root / 'preview.html'
        preview_html(self.settings, self.source, preview)
        text = preview.read_text(encoding='utf-8')
        self.assertNotIn('<script>alert(1)', text)
        self.assertIn('\\u003c/script>', text)
        self.assertIn('renderCourseGraph', text)
        self.assertNotIn('source.md', text)

    def test_api_live_update_subject_and_topic_permissions(self):
        service = LearningService(self.topic, self.data, DeterministicJudge())
        app = create_app(self.settings, service)
        origin = 'http://127.0.0.1:8767'
        with TestClient(app, base_url=origin) as client:
            transport = client.get('/api/session', headers={'Origin': origin}).json()['session_token']
            headers = {'Origin': origin, 'X-Learning-Session': transport}
            guest = client.post('/api/access/guest', json={}, headers=headers).json()
            headers['X-Learning-Identity'] = guest['identity_token']
            self.assertEqual(client.get('/api/graphs?subject_id=physics', headers=headers).status_code, 200)
            account = app.state.access_store.create_account('Graph test')
            login = client.post('/api/access/login', json={'invitation_code': account['invitation_code']}, headers=headers).json()
            headers['X-Learning-Identity'] = login['identity_token']
            import_graph(self.settings, self.source, apply=True)
            url = '/api/graphs/rigid?subject_id=physics'
            response = client.get(url, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({node['content_status'] for node in response.json()['nodes']}, {'not_imported'})
            self.assertEqual(len(response.json()['edges']), 1)
            self.graph['edges'] = []; self.write(self.source, self.graph)
            import_graph(self.settings, self.source, apply=True)
            self.assertEqual(client.get(url, headers=headers).json()['edges'], [])
            self.assertEqual(client.get('/api/graphs/rigid?subject_id=math', headers=headers).status_code, 404)
            restricted = app.state.access_store.create_account('Restricted', topic_ids=[])
            identity = client.post('/api/access/login', json={'invitation_code': restricted['invitation_code']}, headers=headers).json()
            headers['X-Learning-Identity'] = identity['identity_token']
            self.assertEqual(client.get('/api/graphs?subject_id=physics', headers=headers).json()['graphs'], [])
            self.assertEqual(client.get(url, headers=headers).status_code, 404)

    def test_backup_roundtrip_includes_graph_history(self):
        LearningService(self.topic, self.data, DeterministicJudge())
        first = import_graph(self.settings, self.source, apply=True)
        self.graph['edges'] = []; self.write(self.source, self.graph)
        update = import_graph(self.settings, self.source, apply=True)
        backup = backup_site(self.settings, self.root / 'backup.zip')
        restored = self.root / 'restored'
        restore_site(backup['backup'], restored)
        self.assertEqual(GraphStore(Settings(data_dir=restored)).load('rigid'), read_graph(self.source))
        old = restored / 'graphs/history/rigid' / (first['revision'] + '.json')
        self.assertEqual(old.read_bytes(), Path(update['backup']).read_bytes())

    def test_dashboard_uses_live_graph_without_rewriting_legacy_catalog(self):
        self.graph.update(subject_id='math', course_id='course-a')
        self.graph['nodes'][0]['topic_id'] = 'topic-a'
        self.graph['edges'] = [{'from': 'pending-b', 'to': 'topic-a', 'type': 'prerequisite'}]
        self.write(self.source, self.graph)
        import_graph(self.settings, self.source, apply=True)
        before = (self.data / 'catalog.json').read_bytes()
        service = LearningService(self.topic, self.data, DeterministicJudge())
        app = create_app(self.settings, service)
        guest = app.state.access_store.create_guest()
        access = app.state.access_store.resolve(guest['identity_token'])
        first = app.state.dashboard_store.dashboard(access, 'course-a')
        self.assertFalse(first['topics'][0]['frontier'])
        self.graph['edges'] = []; self.write(self.source, self.graph)
        import_graph(self.settings, self.source, apply=True)
        second = app.state.dashboard_store.dashboard(access, 'course-a')
        self.assertTrue(second['topics'][0]['frontier'])
        self.assertEqual((self.data / 'catalog.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
