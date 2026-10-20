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

    def test_extension_keeps_existing_topics_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); data = root / 'data'; package = root / 'package'
            data.mkdir(); package.mkdir()
            original = exercise()
            (data / 'original.json').write_bytes(encoded(original))
            course = {'id': 'existing', 'title': 'Existing', 'description': '',
                      'topics': [{'id': original['id'], 'path': 'original.json', 'prerequisites': []}],
                      'units': [{'id': 'unit', 'title': 'Unit', 'parent_id': None, 'topic_ids': [original['id']]}]}
            (data / 'catalog.json').write_bytes(encoded({'courses': [course]}))
            (data / 'subjects.json').write_bytes(encoded({'subjects': [{'id': 'cs', 'course_ids': ['existing']}]}))
            marker = data / 'learning.sqlite3'; marker.write_bytes(b'unchanged learning records')
            added = deepcopy(original); added['id'] = 'added'
            (package / 'original.json').write_bytes(encoded(original))
            (package / 'added.json').write_bytes(encoded(added))
            candidate = deepcopy(course)
            candidate['topics'].append({'id': 'added', 'path': 'added.json', 'prerequisites': []})
            candidate['units'][0]['topic_ids'] = ['added', original['id']]
            (package / 'catalog.json').write_bytes(encoded({'courses': [candidate]}))
            def manifest():
                (package / 'manifest.json').write_bytes(encoded({'schema': 1, 'adapter': 'hyperskill',
                    'files': {n: hashlib.sha256((package / n).read_bytes()).hexdigest()
                              for n in ('catalog.json', 'original.json', 'added.json')}}))
            manifest()
            with self.assertRaisesRegex(ValueError, 'existing course'):
                install(package, data)
            result = install(package, data, extend_course=True)
            self.assertEqual(result['status'], 'installed')
            catalog = Catalog.load(Settings.load(data))
            self.assertEqual(catalog.topics[original['id']], original)
            self.assertEqual(catalog.courses['existing']['units'][0]['topic_ids'], ['added', original['id']])
            self.assertEqual(marker.read_bytes(), b'unchanged learning records')
            self.assertEqual(install(package, data, extend_course=True)['status'], 'unchanged')
            changed = deepcopy(original); changed['title'] = 'Overwritten'
            (package / 'original.json').write_bytes(encoded(changed)); manifest()
            pointer = (data / 'active-content.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'existing Topic'):
                install(package, data, extend_course=True)
            self.assertEqual((data / 'active-content.json').read_bytes(), pointer)
