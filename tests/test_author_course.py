"""Batch course installation and preparation use isolated synthetic packages."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from math_learning.catalog import Catalog
from math_learning.course_graph import import_graph
from math_learning.subjects import Subjects
from scripts.install_author_course import install_course
from scripts import prepare_topic_batch
from tests import test_topic_install as topic_fixture

write = topic_fixture.write


class AuthorCourseTests(unittest.TestCase):
    setUpClass = classmethod(topic_fixture.TopicInstallTests.setUpClass.__func__)
    setUp = topic_fixture.TopicInstallTests.setUp

    def plan(self):
        return {'course_id': 'new-physics', 'subject_id': 'physics', 'title': 'New Physics',
                'topics': [{'topic_id': 'imported-physics', 'package': str(self.local_package)}]}

    def assert_unchanged(self, subjects):
        self.assertEqual((self.data / 'catalog.json').read_bytes(), self.catalog_before)
        self.assertEqual((self.data / 'subjects.json').read_bytes(), subjects)
        self.assertEqual((self.data / 'learning.sqlite3').read_bytes(), self.database_before)

    def test_new_course_keeps_original_catalog_and_database(self):
        result = install_course(self.settings, self.plan())
        self.assertEqual(result['topics'], 1)
        catalog = Catalog.load(self.settings)
        self.assertEqual(catalog.topic_courses['imported-physics'], 'new-physics')
        Subjects(self.settings, catalog).authorize_course('physics', 'new-physics')
        current = json.loads((self.data / 'catalog.json').read_bytes())
        self.assertEqual(current['courses'][:2], self.original_catalog['courses'])
        self.assertEqual((self.data / 'learning.sqlite3').read_bytes(), self.database_before)
        sources = json.loads((self.data / 'imported-topics/imported-physics/topic.sources.json').read_bytes())
        self.assertTrue(Path(sources['source_path']).is_file())
        before = (self.data / 'catalog.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            install_course(self.settings, self.plan())
        self.assertEqual((self.data / 'catalog.json').read_bytes(), before)

    def test_duplicates_and_mismatched_identity_do_not_activate(self):
        original = (self.data / 'subjects.json').read_bytes()
        for mode in ('duplicate', 'mismatch'):
            plan = self.plan()
            if mode == 'duplicate':
                plan['topics'].append(dict(plan['topics'][0]))
            else:
                plan['topics'][0]['topic_id'] = 'wrong-id'
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'identity'):
                install_course(self.settings, plan)
            self.assert_unchanged(original)

    def test_existing_graph_binding_cannot_move_topic_to_new_course(self):
        graph = self.root / 'graph.json'
        write(graph, {'schema_version': 1, 'subject_id': 'physics', 'course_id': 'physics-course',
                      'title': 'Bound physics', 'nodes': [{'topic_id': 'imported-physics', 'title': 'Speed'}], 'edges': []})
        import_graph(self.settings, graph, apply=True)
        original = (self.data / 'subjects.json').read_bytes()
        with self.assertRaises(ValueError):
            install_course(self.settings, self.plan())
        self.assert_unchanged(original)

    def test_activation_failure_rolls_back_catalog_and_subjects(self):
        import scripts.install_author_course as installer
        original = (self.data / 'subjects.json').read_bytes()
        replace = installer.os.replace
        def fail_subject(source, destination):
            if Path(source).name == 'subjects.json.course-pending':
                raise OSError('Synthetic activation failure')
            return replace(source, destination)
        with patch.object(installer.os, 'replace', side_effect=fail_subject), self.assertRaises(OSError):
            install_course(self.settings, self.plan())
        self.assert_unchanged(original)

    def test_post_activation_authorization_failure_rolls_back(self):
        original = (self.data / 'subjects.json').read_bytes()
        authorize = Subjects.authorize_course
        def fail_after_activation(instance, subject_id, course_id):
            if (course_id == 'new-physics' and (self.data / 'catalog.json').read_bytes() != self.catalog_before):
                raise ValueError('Synthetic final authorization failure')
            return authorize(instance, subject_id, course_id)
        with patch.object(Subjects, 'authorize_course', fail_after_activation), self.assertRaisesRegex(ValueError, 'final authorization'):
            install_course(self.settings, self.plan())
        self.assert_unchanged(original)

    def batch(self, plan, run, producer):
        path = self.root / 'plan.json'
        write(path, plan)
        with patch.object(sys, 'argv', ['prepare-topic-batch', '--plan', str(path), '--run-root', str(run)]), \
                patch.object(prepare_topic_batch, 'prepare_topic', producer):
            return prepare_topic_batch.main()

    def test_batch_summary_conflict_fails_before_api_and_hash_mismatch_fails_result(self):
        from scripts.verify_content import sha256
        source = self.local_package / 'source.md'
        plan = {'topics': [{'topic_id': 'planned', 'source': str(source), 'source_sha256': sha256(source.read_bytes())}]}
        run = self.root / 'batch'
        run.mkdir()
        write(run / 'batch-all.json', {'unchanged': True})
        def forbidden(*args):
            raise AssertionError('Must not call API on conflict')
        with self.assertRaisesRegex(ValueError, 'summary already exists'):
            self.batch(plan, run, forbidden)
        wrong = self.root / 'mismatched'
        self.assertEqual(self.batch(plan, wrong, lambda *args: {'status': 'approved', 'source_sha256': '0' * 64}), 1)
        report = json.loads((wrong / 'batch-all.json').read_bytes())
        self.assertEqual(report['results'][0]['status'], 'failed')
