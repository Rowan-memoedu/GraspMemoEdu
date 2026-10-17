"""A scoped lesson retains internal prerequisites without external blockers."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import unittest

from graspmemoedu.access import AccessStore
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.dependencies import DependencyPolicy
from graspmemoedu.service import LearningService
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class ScopedPrerequisitesTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        topics = {}
        for tid in ('outside', 'first', 'second', 'ordinary'):
            topics[tid] = synthetic_course(1)
            topics[tid].update(id=tid, title=tid)
        topics['first']['prerequisite_scope_course_id'] = 'inside'
        topics['second']['prerequisite_scope_course_id'] = 'inside'
        inside = {'id': 'inside', 'title': 'Inside', 'topics': [
            {'id': 'first', 'prerequisites': ['outside']},
            {'id': 'second', 'prerequisites': ['first', 'outside']},
            {'id': 'ordinary', 'prerequisites': ['outside']}]}
        outside = {'id': 'external', 'title': 'External', 'topics': [{'id': 'outside', 'prerequisites': []}]}
        self.catalog = Catalog([inside, outside], topics, 'first', {})
        self.store = AccessStore(self.root, [{'id': tid, 'title': tid} for tid in topics])
        self.access = self.store.resolve(self.store.create_guest()['identity_token'])
        self.policy = DependencyPolicy(self.catalog, self.store)
        # Metadata is supplied by the isolated in-memory catalog.
        self.catalog.metadata = lambda tid: next(row for c in self.catalog.courses.values()
                                                for row in c['topics'] if row['id'] == tid)
        self.first = LearningService(topics['first'], self.root, DeterministicJudge(), learner_id=self.access['learner_id'])

    def test_external_is_ignored_internal_is_required_and_other_topics_unchanged(self):
        dependencies = self.policy.dependencies()
        self.assertEqual(dependencies['first'], ['outside'])
        self.assertEqual(dependencies['second'], ['first', 'outside'])
        self.assertEqual(self.policy.required_parents('first', dependencies), [])
        self.assertEqual(self.policy.required_parents('second', dependencies), ['first'])
        self.assertEqual(dependencies['ordinary'], ['outside'])
        self.assertTrue(self.policy.is_ready(self.access, 'first'))
        self.assertFalse(self.policy.is_ready(self.access, 'second'))
        self.assertFalse(self.policy.is_ready(self.access, 'ordinary'))
        with self.first._transaction() as con:
            state = self.first._load(con)
            state.update(status='completed', completed_at='2026-01-01T00:00:00+00:00')
            self.first._save(con, state)
        self.assertTrue(self.policy.is_ready(self.access, 'second'))
        self.assertEqual(self.catalog.metadata('first')['prerequisites'], ['outside'])
        # A caller supplying the full source graph cannot restore external gates.
        with closing(self.store._connect()) as con:
            self.assertTrue(self.policy.ready(con, self.access, 'second', {
                'first': ['outside'], 'second': ['first', 'outside']}))

    def test_scope_is_validated_against_actual_course_membership(self):
        for scope in ('missing', 'external', 72, ''):
            topics = deepcopy(self.catalog.topics)
            topics['first']['prerequisite_scope_course_id'] = scope
            with self.assertRaises(ValueError):
                Catalog(list(self.catalog.courses.values()), topics, 'first', {})

    def test_partial_course_scope_excludes_unconverted_topics_and_preserves_raw_graph(self):
        for tid in ('first', 'second'):
            self.catalog.topics[tid]['prerequisite_scope_topic_ids'] = ['first', 'second']
        self.catalog.metadata = lambda tid: {'prerequisites': {
            'outside': [], 'first': ['ordinary'], 'second': ['first', 'ordinary'],
            'ordinary': ['outside']}[tid]}
        dependencies = self.policy.dependencies()
        self.assertEqual(dependencies['first'], ['ordinary'])
        self.assertEqual(dependencies['second'], ['first', 'ordinary'])
        self.assertEqual(self.policy.required_parents('first', dependencies), [])
        self.assertEqual(self.policy.required_parents('second', dependencies), ['first'])
        self.assertEqual(dependencies['ordinary'], ['outside'])
        self.assertTrue(self.policy.is_ready(self.access, 'first'))
        self.assertFalse(self.policy.is_ready(self.access, 'second'))
        self.assertEqual(self.catalog.metadata('second')['prerequisites'], ['first', 'ordinary'])
        for members in ([], ['first', 'outside'], ['second'], ['first', 'first'], 'first'):
            topics = deepcopy(self.catalog.topics)
            topics['first']['prerequisite_scope_topic_ids'] = members
            with self.assertRaises(ValueError):
                Catalog(list(self.catalog.courses.values()), topics, 'first', {})

    def test_unscoped_downstream_does_not_inherit_ancestor_exception(self):
        self.catalog.metadata = lambda tid: {'prerequisites': {
            'outside': [], 'first': ['outside'], 'second': ['first'], 'ordinary': ['first']}[tid]}
        with self.first._transaction() as con:
            state = self.first._load(con)
            state.update(status='completed', completed_at='2026-01-01T00:00:00+00:00')
            self.first._save(con, state)
        self.assertTrue(self.policy.is_ready(self.access, 'first'))
        self.assertTrue(self.policy.is_ready(self.access, 'second'))
        self.assertFalse(self.policy.is_ready(self.access, 'ordinary'))
        self.assertEqual(self.policy.dependencies()['first'], ['outside'])

    def test_internal_ancestor_keeps_health_checks_but_ignores_external_ancestors(self):
        self.catalog.topics['first'].pop('prerequisite_scope_course_id')
        with self.first._transaction() as con:
            state = self.first._load(con)
            state.update(status='completed', completed_at='2026-01-01T00:00:00+00:00')
            self.first._save(con, state)
        self.assertFalse(self.policy.is_ready(self.access, 'first'))
        self.assertTrue(self.policy.is_ready(self.access, 'second'))
        with self.first._transaction() as con:
            state = self.first._load(con)
            state['pause'] = {'reason': 'test'}
            self.first._save(con, state)
        self.assertFalse(self.policy.is_ready(self.access, 'second'))
