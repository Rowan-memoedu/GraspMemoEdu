"""Self-contained installation checks with the real Markdown/MathJax converter."""
from contextlib import closing
import base64
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.service import DomainError
from graspmemoedu.topic_install import install_topic
from scripts.verify_content import collect_questions, parse_outline, sha256
from tests.fixtures import synthetic_course, temporary_test_directory


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class TopicInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = temporary_test_directory()
        cls.addClassCleanup(cls.shared.cleanup)
        cls.package = Path(cls.shared.name) / 'approved'
        cls.package.mkdir()
        source = cls.package / 'source.md'
        lines = ['- Synthetic physics', '  - Introduction', '    - Speed is distance divided by time.',
                 '  - Example 1: Speed', '    - What is the SI unit of speed?',
                 '    - Explanation', '      - The SI unit is metres per second.']
        assets = []
        if getattr(cls, 'with_image', False):
            raw = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC')
            (cls.package / 'assets').mkdir()
            (cls.package / 'assets/diagram.png').write_bytes(raw)
            assets = [{'path': 'assets/diagram.png', 'sha256': sha256(raw), 'size': len(raw), 'mime': 'image/png'}]
            lines.insert(3, '    - ![diagram](assets/diagram.png)')
        for number in range(1, 8):
            lines += [f'    - Practice {number}', '      - What is the SI unit of speed?',
                      '      - Explanation', '        - The SI unit is metres per second.']
        source.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        questions = collect_questions(parse_outline(source.read_text(encoding='utf-8')))
        answers = {'course_id': 'imported-physics', 'source_sha256': sha256(source.read_bytes()),
            'expected_module_count': 1, 'expected_question_count': 8,
            'expected_practices_per_module': None, 'errata': [],
            'answers': {q['id']: {'question_sha256': sha256(q['question']),
                'reference_answer': 'm/s', 'answer_type': 'text', 'constraints': 'Equivalent SI notation accepted.'}
                for q in questions}}
        if getattr(cls, 'mechanical', False):
            for q in questions:
                answers['answers'][q['id']]['reference_answer'] = q['explanation']
        write(cls.package / 'answers.json', answers)
        review = {'schema_version': 1, 'status': 'approved', 'source_sha256': answers['source_sha256'],
            'sidecar_sha256': sha256((cls.package / 'answers.json').read_bytes()), 'model': 'test-fixture',
            'questions': [{'id': q['id'], 'question_sha256': sha256(q['question']),
                           'approved': True, 'issues': []} for q in questions]}
        if assets:
            review['assets'] = assets
        if getattr(cls, 'mechanical', False):
            review.update(mode='mechanical', method='mechanical-import', status='mechanically_validated',
                          semantic_review_performed=False, mathematical_correctness_checked=False)
            review['questions'] = [{'id': q['id'], 'question_sha256': sha256(q['question']),
                                    'structurally_valid': True, 'field_mapping': 'reference_answer=explanation'} for q in questions]
        write(cls.package / 'review.json', review)
        converter = Path(__file__).resolve().parents[1] / 'scripts/import_course.mjs'
        result = subprocess.run(['node', str(converter), str(source), str(cls.package / 'answers.json'),
            str(cls.package / 'topic.json'), str(cls.package / 'audit.json'), str(cls.package / 'review.json')],
            capture_output=True, text=True, encoding='utf-8', timeout=180,
            env={**os.environ, 'GRASPMEMOEDU_PYTHON': sys.executable})
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.manifest = {'schema_version': 1, 'status': 'approved', 'topic_id': 'imported-physics',
            'artifacts': {'source': 'source.md', 'answers': 'answers.json', 'review': 'review.json',
                          'topic': 'topic.json', 'audit': 'audit.json'},
            'files': {p.relative_to(cls.package).as_posix(): sha256(p.read_bytes()) for p in cls.package.rglob('*') if p.is_file()}}
        if assets:
            cls.manifest['assets'] = assets
        write(cls.package / 'manifest.json', cls.manifest)

    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.local_package = self.root / 'package'
        shutil.copytree(self.package, self.local_package)
        topic = synthetic_course(1)
        topic['id'] = 'existing-math'
        write(self.data / 'math.json', topic)
        self.original_catalog = {'courses': [
            {'id': 'math-course', 'title': 'Original mathematics',
             'topics': [{'id': 'existing-math', 'path': 'math.json'}]},
            {'id': 'physics-course', 'title': 'Physics', 'topics': []}]}
        write(self.data / 'catalog.json', self.original_catalog)
        write(self.data / 'subjects.json', {'subjects': [{'id': 'physics', 'course_ids': ['physics-course']}]})
        database = self.data / 'learning.sqlite3'
        with closing(sqlite3.connect(database)) as con:
            con.execute('CREATE TABLE existing_progress (learner TEXT, value TEXT)')
            con.execute("INSERT INTO existing_progress VALUES ('existing-user', 'untouched')")
            con.commit()
        self.database_before = database.read_bytes()
        self.catalog_before = (self.data / 'catalog.json').read_bytes()
        self.settings = Settings(data_dir=self.data)

    def install(self, **kwargs):
        return install_topic(self.settings, self.local_package, subject_id=kwargs.get('subject_id', 'physics'),
                             course_id=kwargs.get('course_id', 'physics-course'))

    def assert_not_activated(self):
        self.assertEqual((self.data / 'catalog.json').read_bytes(), self.catalog_before)
        self.assertEqual((self.data / 'learning.sqlite3').read_bytes(), self.database_before)
        self.assertNotIn('imported-physics', Catalog.load(self.settings).topics)
        self.assertFalse((self.data / '.author-import.lock').exists())

    def test_install_preserves_existing_catalog_and_database_and_is_idempotent(self):
        result = self.install()
        self.assertEqual(result['status'], 'installed')
        self.assertFalse(result['permissions_changed'])
        current = json.loads((self.data / 'catalog.json').read_text(encoding='utf-8'))
        self.assertEqual(current['courses'][0], self.original_catalog['courses'][0])
        self.assertEqual(len(current['courses'][1]['topics']), 1)
        self.assertEqual((self.data / 'learning.sqlite3').read_bytes(), self.database_before)
        self.assertEqual(Path(result['catalog_backup']).read_bytes(), self.catalog_before)
        destination = Path(result['topic_path']).parent
        self.assertEqual((destination / 'topic.json').read_bytes(), (self.local_package / 'topic.json').read_bytes())
        sources = json.loads((destination / 'topic.sources.json').read_text())
        self.assertEqual(Path(sources['source_path']), destination / 'source.md')
        self.assertEqual(Path(sources['review_path']), destination / 'review.json')
        self.assertEqual(Catalog.load(self.settings).topic_courses['imported-physics'], 'physics-course')
        installed_catalog = (self.data / 'catalog.json').read_bytes()
        self.assertEqual(self.install()['status'], 'already_installed')
        self.assertEqual((self.data / 'catalog.json').read_bytes(), installed_catalog)

    def test_changed_artifact_is_rejected_before_activation(self):
        source = self.local_package / 'source.md'
        source.write_bytes(source.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'changed after validation'):
            self.install()
        self.assert_not_activated()

    def test_rehashed_but_unreviewed_answer_is_rejected_by_real_converter(self):
        answers_path = self.local_package / 'answers.json'
        answers = json.loads(answers_path.read_text())
        answers['answers']['example-1-question']['reference_answer'] = 'wrong'
        write(answers_path, answers)
        manifest = json.loads((self.local_package / 'manifest.json').read_text())
        manifest['files']['answers.json'] = sha256(answers_path.read_bytes())
        write(self.local_package / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'independent conversion'):
            self.install()
        self.assert_not_activated()

    def test_existing_topic_id_cannot_be_replaced(self):
        existing = json.loads((self.local_package / 'topic.json').read_text(encoding='utf-8'))
        existing['title'] = 'Existing protected content'
        write(self.data / 'existing-physics.json', existing)
        catalog = self.original_catalog
        catalog['courses'][1]['topics'].append({'id': 'imported-physics', 'path': 'existing-physics.json'})
        write(self.data / 'catalog.json', catalog)
        before = (self.data / 'catalog.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.install()
        self.assertEqual((self.data / 'catalog.json').read_bytes(), before)
        self.assertEqual(json.loads((self.data / 'existing-physics.json').read_text())['title'], 'Existing protected content')

    def test_wrong_subject_does_not_activate(self):
        with self.assertRaises(DomainError):
            self.install(subject_id='math')
        self.assert_not_activated()

    def test_graph_first_then_real_topic_import_preserves_graph_and_binds_content(self):
        from graspmemoedu.course_graph import GraphStore, import_graph
        source = self.root / 'graph.json'
        graph = {'schema_version': 1, 'subject_id': 'physics', 'course_id': 'physics-course', 'title': 'Physics',
                 'nodes': [{'topic_id': 'imported-physics', 'title': 'Speed'}, {'topic_id': 'pending', 'title': 'Later'}],
                 'edges': [{'from': 'imported-physics', 'to': 'pending', 'type': 'prerequisite'}]}
        write(source, graph)
        result = import_graph(self.settings, source, apply=True)
        before = Path(result['path']).read_bytes()
        self.install()
        self.assertEqual(Path(result['path']).read_bytes(), before)
        store = GraphStore(self.settings)
        visible = store.public(store.load('physics-course'), {'role': 'account', 'topics': [], 'permission_defaults': {'topics': True}})
        self.assertEqual({node['topic_id']: node['content_status'] for node in visible['nodes']},
                         {'imported-physics': 'available', 'pending': 'not_imported'})
        self.assertEqual((self.data / 'learning.sqlite3').read_bytes(), self.database_before)

    def test_topic_first_then_graph_updates_do_not_change_topic_or_progress(self):
        from graspmemoedu.course_graph import import_graph
        installed = self.install()
        topic_path = Path(installed['topic_path'])
        before = topic_path.read_bytes()
        catalog = (self.data / 'catalog.json').read_bytes()
        source = self.root / 'graph.json'
        graph = {'schema_version': 1, 'subject_id': 'physics', 'course_id': 'physics-course', 'title': 'Physics',
                 'nodes': [{'topic_id': 'imported-physics', 'title': 'Speed'}], 'edges': []}
        write(source, graph); import_graph(self.settings, source, apply=True)
        graph['title'] = 'New graph title'
        write(source, graph); import_graph(self.settings, source, apply=True)
        self.assertEqual(topic_path.read_bytes(), before)
        self.assertEqual((self.data / 'catalog.json').read_bytes(), catalog)
        self.assertEqual((self.data / 'learning.sqlite3').read_bytes(), self.database_before)

    def test_commit_failure_leaves_catalog_inactive_and_allows_recovery(self):
        with patch('graspmemoedu.topic_install.os.replace', side_effect=OSError('simulated commit failure')):
            with self.assertRaisesRegex(OSError, 'simulated commit failure'):
                self.install()
        self.assert_not_activated()
        self.assertFalse((self.data / 'catalog.json.author-pending').exists())
        self.assertTrue((self.data / 'imported-topics/imported-physics/topic.json').is_file())
        self.assertEqual(self.install()['status'], 'installed')


class ImageTopicInstallTests(unittest.TestCase):
    with_image = True
    setUpClass = classmethod(TopicInstallTests.setUpClass.__func__)
    setUp = TopicInstallTests.setUp
    install = TopicInstallTests.install
    assert_not_activated = TopicInstallTests.assert_not_activated

    def test_image_package_installs_original_and_embedded_image(self):
        result = self.install()
        destination = Path(result['topic_path']).parent
        self.assertEqual((destination / 'assets/diagram.png').read_bytes(), (self.package / 'assets/diagram.png').read_bytes())
        self.assertIn('data:image/png;base64,', (destination / 'topic.json').read_text())
        sources = json.loads((destination / 'topic.sources.json').read_text())
        self.assertEqual(sources['assets'], self.manifest['assets'])
        self.assertEqual(self.install()['status'], 'already_installed')
        self.assertEqual((self.data / 'learning.sqlite3').read_bytes(), self.database_before)

    def test_altered_asset_cannot_be_installed(self):
        (self.local_package / 'assets/diagram.png').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.install()
        self.assert_not_activated()

    def test_review_cannot_omit_asset_binding(self):
        review_path = self.local_package / 'review.json'
        review = json.loads(review_path.read_text())
        review['assets'] = []
        write(review_path, review)
        manifest_path = self.local_package / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files']['review.json'] = sha256(review_path.read_bytes())
        write(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, 'Review assets'):
            self.install()
        self.assert_not_activated()

    def test_restored_image_source_reimports_through_real_converter(self):
        from graspmemoedu.site_backup import backup_site, restore_site
        from graspmemoedu.service import LearningService
        self.install()
        catalog = Catalog.load(self.settings)
        LearningService(catalog.topic(catalog.default_topic_id), self.data)
        archive = self.root / 'images.zip'
        backup_site(self.settings, archive)
        restored = self.root / 'restored'
        restore_site(archive, restored)
        index = json.loads((restored / 'courses/imported-physics.sources.json').read_text())
        output = self.root / 'reimported.json'
        converter = Path(__file__).resolve().parents[1] / 'scripts/import_course.mjs'
        result = subprocess.run(['node', str(converter), index['source_path'], index['answers_path'],
                                 str(output), str(self.root / 'reimport-audit.json'), index['review_path']],
                                capture_output=True, text=True, encoding='utf-8', timeout=180,
                                env={**os.environ, 'GRASPMEMOEDU_PYTHON': sys.executable})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text()), catalog.topic('imported-physics'))


class MechanicalImageTopicInstallTests(ImageTopicInstallTests):
    mechanical = True

    def test_review_cannot_omit_asset_binding(self):
        # Mechanical validation reaches the same binding check in its verifier.
        review_path = self.local_package / 'review.json'
        review = json.loads(review_path.read_text())
        review['assets'] = []
        write(review_path, review)
        manifest_path = self.local_package / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files']['review.json'] = sha256(review_path.read_bytes())
        write(manifest_path, manifest)
        with self.assertRaises(ValueError):
            self.install()
        self.assert_not_activated()

    def test_mechanical_evidence_cannot_claim_semantic_approval(self):
        review_path = self.local_package / 'review.json'
        review = json.loads(review_path.read_text())
        review['semantic_review_performed'] = True
        write(review_path, review)
        manifest_path = self.local_package / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files']['review.json'] = sha256(review_path.read_bytes())
        write(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, 'semantic approval'):
            self.install()
        self.assert_not_activated()


if __name__ == '__main__':
    unittest.main()
