import base64
import json
from pathlib import Path
import unittest
from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from graspmemoedu.subject_registry import DEFAULTS
from tests.fixtures import DeterministicJudge, temporary_test_directory
from tests.catalog_scope_fixture import write_scopes


class CatalogScopeTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        topics = write_scopes(self.root)
        self.app = create_app(Settings(data_dir=self.root),
            LearningService(topics['lesson-math'], self.root, DeterministicJudge()))
        self.client = self.enterContext(TestClient(self.app, base_url='http://127.0.0.1:8767'))
        store = self.app.state.access_store
        account = store.create_account('Scope fixture', is_student=True, purpose='test')
        self.learner = account['account']['learner_id']
        store.set_permissions(self.learner, is_advanced_learner=True)
        self.headers = {'Origin': 'http://127.0.0.1:8767',
                        'X-Learning-Identity': store.login(account['invitation_code'])['identity_token']}
        self.headers['X-Learning-Session'] = self.client.get('/api/session', headers=self.headers).json()['session_token']

    def get(self, path, status=200):
        response = self.client.get('/api/' + path, headers=self.headers)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_selection_scope_and_graph_are_shared_across_subjects(self):
        for subject, *_ in DEFAULTS:
            with self.subTest(subject=subject):
                course, bank = 'course-' + subject, 'bank-' + subject
                for path, field in [('catalog', 'courses'), ('training/catalog', 'banks')]:
                    outline = self.get(f'{path}?subject_id={subject}')[field][0]['outline']
                    language = next(u for u in outline if u['id'] == 'language')
                    self.assertEqual(language['parent_id'], 'languages')
                data = self.get(f'dashboard?subject_id={subject}&course_id={course}&root_id=language')
                self.assertEqual(data['course']['title'], 'Java')
                self.assertEqual(data['course']['id'], course)
                self.assertEqual({t['id'] for t in data['topics']}, {kind + '-' + subject for kind in ('lesson', 'atomic', 'composed', 'academy')})
                self.assertEqual([u['id'] for u in data['pending_hierarchy']], ['basics'])
                self.assertIsNone(data['pending_hierarchy'][0]['parent_id'])
                graph = self.get(f'graphs/{course}?subject_id={subject}&root_id=language')
                self.assertEqual({n['topic_id'] for n in graph['nodes']}, {t['id'] for t in data['topics']})
                data = self.get(f'training/bank?subject_id={subject}&bank_id={bank}&root_id=language')
                self.assertEqual(data['title'], 'Java')
                self.assertEqual({g['id'] for g in data['groups']}, {'language', 'chapter', 'group-a'})
                graph = self.get(f'graphs/{bank}?subject_id={subject}&root_id=language')
                self.assertEqual([n['group_id'] for n in graph['nodes']], ['group-a'])
                self.assertEqual(graph['title'], 'Java')

    def test_leaf_scope_permissions_and_invalid_roots(self):
        data = self.get('dashboard?course_id=course-math&root_id=sibling')
        self.assertEqual([t['id'] for t in data['topics']], ['hyperskill-math'])
        self.assertEqual(data['pending_hierarchy'], [])
        self.app.state.access_store.set_permissions(self.learner, topic_ids=['lesson-math'])
        outline = self.get('catalog?subject_id=math')['courses'][0]['outline']
        self.assertNotIn('sibling', {u['id'] for u in outline})
        self.get('dashboard?course_id=course-math&root_id=sibling', 403)
        self.get('dashboard?course_id=course-math&root_id=unknown', 404)
        self.get('training/bank?bank_id=bank-math&root_id=unknown', 403)

    def test_history_cursor_is_bound_to_selected_subtree(self):
        cursor = base64.urlsafe_b64encode(json.dumps(['course-math:language', self.learner, '', 'x']).encode()).decode()
        self.get(f'dashboard?course_id=course-math&root_id=language&cursor={cursor}')
        self.get(f'dashboard?course_id=course-math&root_id=sibling&cursor={cursor}', 422)
        self.get(f'dashboard?course_id=course-math&cursor={cursor}', 422)

    def test_scoped_bank_retains_external_prerequisites(self):
        from graspmemoedu.course_graph import import_graph
        from tests.test_group_dependencies import mixed_graph
        source = self.root / 'scope-graph.json'
        source.write_text(json.dumps(mixed_graph('cs')), encoding='utf-8')
        import_graph(Settings(data_dir=self.root), source, apply=True)
        graph = self.get('graphs/bank-cs?subject_id=cs&root_id=sibling')
        self.assertEqual({n['id'] for n in graph['nodes']}, {'lesson-cs', 'group:bank-cs:group-a', 'group:bank-cs:group-b'})
        self.assertEqual([n['id'] for n in graph['nodes'] if n['member']], ['group:bank-cs:group-b'])
        data = self.get('training/bank?subject_id=cs&bank_id=bank-cs&root_id=sibling')
        group = next(g for g in data['groups'] if g['id'] == 'group-b')
        self.assertFalse(group['dependency_ready'])
        self.assertIn('group:bank-cs:group-a', {p['id'] for p in group['prerequisites']})
        outline = self.get('training/catalog?subject_id=cs')['banks'][0]['outline']
        self.assertFalse(next(u for u in outline if u['id'] == 'sibling')['dependency_ready'])
