from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from graspmemoedu.academy_import import install
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.topic_install import encoded
from test_academy import exercise


class AcademyImportTests(unittest.TestCase):
    def test_atomic_install_preserves_course_hierarchy_and_does_not_write_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); data = root / 'data'; package = root / 'package'
            data.mkdir(); (package / 'topics').mkdir(parents=True)
            original = exercise(); original['id'] = 'existing'; original.pop('academy')
            (data / 'existing.json').write_bytes(encoded(original))
            (data / 'catalog.json').write_bytes(encoded({'courses': [{'id': 'old', 'title': 'Existing',
                'topics': [{'id': 'existing', 'path': 'existing.json'}]}]}))
            (data / 'subjects.json').write_bytes(encoded({'subjects': [{'id': 'cs', 'course_ids': ['old']}]}))
            topic = exercise()
            (package / 'topics/exercise.json').write_bytes(encoded(topic))
            course = {'id': 'new', 'title': 'New', 'topics': [{'id': topic['id'], 'path': 'topics/exercise.json', 'prerequisites': []}],
                      'units': [{'id': 'section', 'title': 'Section', 'topic_ids': [], 'parent_id': None},
                                {'id': 'lesson', 'title': 'Lesson', 'topic_ids': [topic['id']], 'parent_id': 'section'}]}
            (package / 'catalog.json').write_bytes(encoded({'courses': [course]}))
            (package / 'report.json').write_bytes(encoded({'topics': 1}))
            (package / 'manifest.json').write_bytes(encoded({'schema': 1, 'adapter': 'jetbrains-academy', 'files': {
                name: hashlib.sha256((package / name).read_bytes()).hexdigest()
                for name in ('catalog.json', 'report.json', 'topics/exercise.json')}}))
            before = (data / 'existing.json').read_bytes()
            result = install(package, data)
            self.assertEqual(result['status'], 'installed')
            catalog = Catalog.load(Settings.load(data))
            self.assertEqual(catalog.courses['new']['units'], course['units'])
            self.assertEqual((data / 'existing.json').read_bytes(), before)
            self.assertFalse(list(data.glob('*.sqlite*')))
            self.assertEqual(install(package, data)['status'], 'unchanged')
            (package / 'topics/exercise.json').write_text('{}')
            with self.assertRaises(ValueError): install(package, data)

    def test_author_placeholders_use_utf16_offsets(self):
        from scripts.prepare_academy import template
        raw, positions = template('😀答案尾巴'.encode(), [{'offset': 2, 'length': 2, 'placeholder_text': 'TODO'}])
        self.assertEqual(raw.decode(), '😀TODO尾巴')
        self.assertEqual(positions, [{'offset': 2, 'length': 4}])
