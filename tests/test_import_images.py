"""Local PNG import uses synthetic files only, with no network access."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
import zlib

from scripts.verify_content import collect_questions, parse_outline, sha256
from tests.fixtures import temporary_test_directory


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/import_course.mjs'


def png(width=2, height=2):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff\x00\x00' * 2) * 2)) + chunk(b'IEND', b''))


class ImportImagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = temporary_test_directory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'assets').mkdir()
        (self.root / 'assets/diagram.png').write_bytes(png())
        self.source = self.root / 'source.md'
        self.original = '\n'.join(['- Image fixture', '  - Introduction', '    - ![Intro](assets/diagram.png)',
            '  - Example 1: Image', '    - Read the diagram.', '      <!-- topic-diagrams:' + 'a' * 64 + ' -->',
            '    - ![A "safe" diagram](assets/diagram.png)', '    - Explanation', '      - A red dot.'] +
            [f'    - Practice {i}\n      - Name a colour.\n      - Explanation\n        - Red.' for i in range(1, 8)]) + '\n'
        self.source.write_text(self.original, encoding='utf-8')

    def run_node(self, *args):
        return subprocess.run(['node', str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                              encoding='utf-8', env={**os.environ, 'MATH_LEARNING_PYTHON': sys.executable})

    def test_inspect_hashes_paths_and_render_embedded_image_without_source_mutation(self):
        result = self.run_node('--inspect', self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        inspected = json.loads(result.stdout)
        self.assertEqual(inspected['introduction_image_paths'], ['assets/diagram.png'])
        self.assertEqual(inspected['questions'][0]['image_paths'], ['assets/diagram.png'])
        self.assertEqual(inspected['assets'][0]['occurrences'], 2)
        questions = collect_questions(parse_outline(self.original))
        sidecar = {'course_id': 'image-fixture', 'source_sha256': sha256(self.source.read_bytes()),
                   'expected_module_count': 1, 'expected_question_count': 8, 'expected_practices_per_module': 7,
                   'answers': {q['id']: {'question_sha256': sha256(q['question']), 'reference_answer': 'Red',
                                       'answer_type': 'text', 'constraints': 'Accept red.'} for q in questions}}
        answers = self.root / 'answers.json'
        answers.write_text(json.dumps(sidecar), encoding='utf-8')
        review = self.root / 'review.json'
        review.write_text(json.dumps({'schema_version': 1, 'status': 'approved', 'source_sha256': sidecar['source_sha256'],
            'sidecar_sha256': sha256(answers.read_bytes()), 'assets': inspected['assets'], 'questions': [{'id': q['id'],
            'question_sha256': sha256(q['question']), 'approved': True, 'issues': []} for q in questions]}), encoding='utf-8')
        mixed = json.loads(review.read_text(encoding='utf-8'))
        mixed['questions'][0]['review_origin'] = 'human_resolution'
        review.write_text(json.dumps(mixed), encoding='utf-8')
        output, audit = self.root / 'topic.json', self.root / 'audit.json'
        result = self.run_node(self.source, answers, output, audit, review)
        self.assertEqual(result.returncode, 0, result.stderr)
        course = json.loads(output.read_text(encoding='utf-8'))
        report = json.loads(audit.read_text(encoding='utf-8'))
        self.assertEqual(report['method'], 'llm-reviewed-with-local-resolution')
        self.assertEqual(report['human_resolution_count'], 1)
        self.assertEqual(report['records'][0]['review_origin'], 'human_resolution')
        self.assertEqual(report['records'][1]['method'], 'llm-reviewed')
        html = course['modules'][0]['example']['html']
        self.assertIn('src="data:image/png;base64,', html)
        self.assertIn('max-width:100%;height:auto', html)
        self.assertNotIn('<!--', html)
        self.assertEqual(course['modules'][0]['example']['question'], questions[0]['question'])
        self.assertEqual(self.source.read_text(encoding='utf-8'), self.original)
        changed_review = json.loads(review.read_text(encoding='utf-8'))
        changed_review['assets'][0]['sha256'] = '0' * 64
        review.write_text(json.dumps(changed_review), encoding='utf-8')
        rejected = self.run_node(self.source, answers, self.root / 'rejected.json', self.root / 'rejected-audit.json', review)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse((self.root / 'rejected.json').exists())

    def test_external_absolute_traversal_and_non_png_rejected(self):
        for path in ['https://example.com/a.png', '/absolute.png', '../a.png', 'assets/../a.png',
                     'C:/secret.png', 'assets\\diagram.png', 'assets/a.svg', 'assets/%2e%2e/a.png']:
            with self.subTest(path=path):
                self.source.write_text(self.original.replace('assets/diagram.png', path), encoding='utf-8')
                self.assertNotEqual(self.run_node('--inspect', self.source).returncode, 0)

    def test_corrupt_and_oversized_png_rejected(self):
        for data in [b'not a PNG', png(10001, 2), png()[:-1], png()[:45] + b'changed']:
            with self.subTest(length=len(data)):
                (self.root / 'assets/diagram.png').write_bytes(data)
                self.assertNotEqual(self.run_node('--inspect', self.source).returncode, 0)

    def test_symlink_escape_rejected_when_supported(self):
        target = self.root / 'outside.png'
        target.write_bytes(png())
        try:
            (self.root / 'assets/link.png').symlink_to(target)
        except OSError as exc:
            self.skipTest(str(exc))
        self.source.write_text(self.original.replace('assets/diagram.png', 'assets/link.png'), encoding='utf-8')
        self.assertNotEqual(self.run_node('--inspect', self.source).returncode, 0)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_windows_junction_escape_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'diagram.png').write_bytes(png())
        link = self.root / 'assets/junction'
        created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                                 capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(created.returncode, 0)
        self.source.write_text(self.original.replace('assets/diagram.png', 'assets/junction/diagram.png'), encoding='utf-8')
        self.assertNotEqual(self.run_node('--inspect', self.source).returncode, 0)
