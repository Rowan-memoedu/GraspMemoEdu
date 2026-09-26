"""Independent boundary checks against isolated storage and real graph imports."""
from contextlib import closing
import json
from pathlib import Path
import unittest

from graspmemoedu.access import AccessStore
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.course_graph import import_graph
from graspmemoedu.dashboard import DashboardStore
from graspmemoedu.review import ReviewBackend
from graspmemoedu.service import DomainError, LearningService
from graspmemoedu.subjects import Subjects
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class DependencyPolicyBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for topic_id in ('parent', 'child'):
            topic = synthetic_course(1)
            topic.update(id=topic_id, title='Private ' + topic_id)
            self.write(self.root / (topic_id + '.json'), topic)
        self.write(self.root / 'catalog.json', {'courses': [{
            'id': 'course', 'title': 'Course', 'topics': [
                {'id': 'parent', 'path': 'parent.json'},
                {'id': 'child', 'path': 'child.json', 'prerequisites': ['parent']}]}]})
        self.settings = Settings(data_dir=self.root)
        self.catalog = Catalog.load(self.settings)
        self.subjects = Subjects(self.settings, self.catalog)
        self.access_store = AccessStore(self.root, [{'id': item['id'], 'title': item['title']}
                                                  for item in self.catalog.topics.values()])
        guest = self.access_store.create_guest()
        self.access = self.access_store.resolve(guest['identity_token'])
        self.parent = LearningService(self.catalog.topic('parent'), self.root, DeterministicJudge(),
                                      learner_id=self.access['learner_id'])
        ReviewBackend(self.parent)
        self.dashboard = DashboardStore(self.catalog, self.access_store, self.subjects)
        self.policy = self.dashboard.dependency_policy

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def graph(self, prerequisites, pending=False):
        nodes = [{'topic_id': topic_id, 'title': 'Secret ' + topic_id} for topic_id in ('parent', 'child')]
        if pending:
            nodes.append({'topic_id': 'unimported', 'title': 'Private unimported title'})
        source = self.root / 'graph-source.json'
        self.write(source, {'schema_version': 1, 'subject_id': 'math', 'course_id': 'course', 'title': 'Course',
                            'nodes': nodes, 'edges': [{'from': parent, 'to': 'child', 'type': 'prerequisite'}
                                                     for parent in prerequisites]})
        import_graph(self.settings, source, apply=True)

    def completed_parent(self):
        with self.parent._transaction() as con:
            state = self.parent._load(con)
            state['status'] = 'completed'
            state['completed_at'] = '2026-01-01T00:00:00+00:00'
            state['attempts'][0].update(status='completed', mastery=1, completed_at=state['completed_at'])
            self.parent._save(con, state)

    def test_graph_is_authoritative_and_changes_without_policy_recreation(self):
        self.assertFalse(self.policy.is_ready(self.access, 'child'))
        before = (self.root / 'catalog.json').read_bytes()
        self.graph([])
        self.assertTrue(self.policy.is_ready(self.access, 'child'))
        self.graph(['parent'])
        self.assertFalse(self.policy.is_ready(self.access, 'child'))
        self.completed_parent()
        self.assertTrue(self.policy.is_ready(self.access, 'child'))
        self.assertEqual((self.root / 'catalog.json').read_bytes(), before)

    def test_unimported_parent_blocks_and_does_not_expose_graph_private_metadata(self):
        self.graph(['unimported'], pending=True)
        self.assertFalse(self.policy.is_ready(self.access, 'child'))
        result = self.dashboard.dashboard(self.access, 'course')
        child = next(topic for topic in result['topics'] if topic['id'] == 'child')
        self.assertEqual(child['prerequisite_details'], [])
        task = next(task for task in result['tasks'] if task['id'] == 'lesson:child')
        self.assertFalse(task['dependency_ready'])
        self.assertEqual(task['missing_prerequisite_count'], 1)
        self.assertNotIn('unimported', json.dumps(result))

    def test_unauthorized_completed_parent_blocks_without_exposing_progress_or_title(self):
        self.completed_parent()
        limited = {**self.access, 'topics': [topic for topic in self.access['topics'] if topic['id'] == 'child']}
        self.assertFalse(self.policy.is_ready(limited, 'child'))
        result = self.dashboard.dashboard(limited, 'course')
        self.assertEqual(result['topics'][0]['prerequisite_details'], [])
        self.assertEqual(result['topics'][0]['prerequisites'], [])
        self.assertNotIn('Private parent', json.dumps(result))
        with self.assertRaises(DomainError) as caught:
            self.policy.require(limited, 'child')
        self.assertEqual(caught.exception.code, 'prerequisites_not_ready')
        self.assertNotIn('parent', str(caught.exception))

    def test_only_current_version_due_cards_and_active_reviews_block(self):
        self.completed_parent()
        version = self.parent.course['version']
        key = (self.access['learner_id'], 'parent')
        with closing(self.access_store._connect()) as con:
            con.execute('INSERT INTO review_cards VALUES(?,?,?,?,?,?,1,NULL)',
                        (*key, 'module-1', 'retired-version', '{}', '2000-01-01T00:00:00+00:00'))
            con.execute('INSERT INTO review_sessions VALUES(?,?,?,?,?,?,?)',
                        ('old-review', *key, 'module-1', 'retired-version', 'in_progress', '{}'))
            con.commit()
        self.assertTrue(self.policy.is_ready(self.access, 'child'))
        with closing(self.access_store._connect()) as con:
            con.execute('UPDATE review_cards SET version=?', (version,)); con.commit()
        self.assertFalse(self.policy.is_ready(self.access, 'child'))
        with closing(self.access_store._connect()) as con:
            con.execute("UPDATE review_cards SET due_at='2999-01-01T00:00:00+00:00'")
            con.execute('UPDATE review_sessions SET version=?', (version,)); con.commit()
        self.assertFalse(self.policy.is_ready(self.access, 'child'))
        with closing(self.access_store._connect()) as con:
            con.execute("UPDATE review_sessions SET status='completed'"); con.commit()
        self.assertTrue(self.policy.is_ready(self.access, 'child'))


if __name__ == '__main__':
    unittest.main()
