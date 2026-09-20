import json
from pathlib import Path
import unittest
from unittest import mock
import os

from math_learning.catalog import Catalog
from math_learning.config import Settings
from math_learning.subjects import Subjects
from scripts.initialize_subject_test_courses import initialize, SUBJECTS, source_bundle
from scripts.verify_content import collect_questions, parse_outline
from tests.fixtures import synthetic_course, temporary_test_directory


class SubjectInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = self.root / 'courses/digits-to-letters.json'
        self.original.parent.mkdir()
        self.original.write_text(json.dumps(synthetic_course()), encoding='utf-8')

    def test_deterministic_source_uses_standard_outline_and_full_practice_pools(self):
        for subject, name in SUBJECTS.items():
            first = source_bundle(subject, name)
            self.assertEqual(first, source_bundle(subject, name))
            self.assertEqual(len(collect_questions(parse_outline(first[0].decode()))), 32)

    def test_real_import_is_additive_idempotent_and_preserves_math_and_overrides(self):
        original = self.original.read_bytes()
        overrides = {'subjects': [{'id': 'physics', 'title': '自定义物理', 'guide_path': 'custom/physics.md'},
                                  {'id': 'english', 'guide_path': 'custom/english.md'},
                                  {'id': 'math', 'title': '已有数学'}]}
        (self.root / 'custom').mkdir()
        (self.root / 'custom/english.md').write_bytes(b'KEEP EXISTING GUIDE')
        (self.root / 'subjects.json').write_text(json.dumps(overrides), encoding='utf-8')
        dry = initialize(self.root)
        self.assertFalse(dry['applied'])
        self.assertFalse((self.root / 'catalog.json').exists())
        self.assertFalse((self.root / 'test-content').exists())
        initialize(self.root, apply=True)
        after = initialize(self.root, apply=True)
        self.assertTrue(all(not item['changed'] for item in after['files']))
        self.assertEqual(self.original.read_bytes(), original)
        catalog = Catalog.load(Settings(data_dir=self.root))
        subjects = Subjects(Settings(data_dir=self.root), catalog)
        self.assertEqual(len(catalog.courses), 6)
        self.assertNotIn('test-math-course', catalog.courses)
        self.assertEqual(subjects.subjects['physics']['title'], '自定义物理')
        self.assertEqual(subjects.guide_paths['physics'], self.root / 'custom/physics.md')
        self.assertIn('【测试】', (self.root / 'custom/physics.md').read_text(encoding='utf-8'))
        self.assertEqual((self.root / 'custom/english.md').read_bytes(), b'KEEP EXISTING GUIDE')
        for subject in SUBJECTS:
            self.assertEqual(subjects.course_ids(subject), {f'test-{subject}-course'})
            topic = catalog.topic(f'test-{subject}-topic')
            self.assertEqual(len(topic['modules']), 2)
            self.assertTrue(all(len(module['practices']) == 15 for module in topic['modules']))

    def test_changed_generated_file_aborts_without_config_write(self):
        initialize(self.root, apply=True)
        config = (self.root / 'catalog.json').read_bytes()
        topic = self.root / 'test-content/physics/topic.json'
        topic.write_bytes(topic.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'overwrite changed'):
            initialize(self.root, apply=True)
        self.assertEqual((self.root / 'catalog.json').read_bytes(), config)

    def test_config_replace_failure_rolls_back_this_run(self):
        subject_path = self.root / 'subjects.json'
        original = b'{"subjects": [{"id": "math", "title": "existing"}]}'
        subject_path.write_bytes(original)
        replace = os.replace
        def fail_subject(source, destination):
            if Path(destination) == subject_path:
                raise OSError('injected configuration failure')
            return replace(source, destination)
        with mock.patch('scripts.initialize_subject_test_courses.os.replace', side_effect=fail_subject):
            with self.assertRaisesRegex(OSError, 'injected'):
                initialize(self.root, apply=True)
        self.assertEqual(subject_path.read_bytes(), original)
        self.assertFalse((self.root / 'catalog.json').exists())
        self.assertFalse(list((self.root / 'test-content').rglob('*.json')))


if __name__ == '__main__':
    unittest.main()
