import base64
import hashlib
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
import zlib

from math_learning.author_import import prepare_topic, _batches
from math_learning.topic_assets import validate_assets, read_local_asset
from tests.fixtures import temporary_test_directory


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b'')


class VisionFake:
    model = 'test-vision'

    def __init__(self, reject=False):
        self.messages, self.reject = [], reject

    def complete(self, messages):
        self.messages.append(messages)
        content = messages[-1]['content']
        rows = json.loads(content[0]['text'].split('待审核数据：\n')[1])
        if 'source' in rows[0]:
            return {'questions': [{'id': q['source']['id'], 'approved': not self.reject,
                'issues': ['Unreadable diagram'] if self.reject else [], 'rationale': 'Read the supplied figure.'} for q in rows]}
        return {'answers': [{'id': q['id'], 'reference_answer': 'red', 'answer_type': 'text', 'constraints': 'red pixel'} for q in rows]}


class AuthorImportImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.md'
        (self.root / 'assets').mkdir()
        self.records = []
        for name in ['intro', *(f'q{i}' for i in range(9))]:
            path = f'assets/{name}.png'
            (self.root / path).write_bytes(png())
            self.records.append({'path': path, 'sha256': hashlib.sha256(png()).hexdigest(), 'size': len(png()), 'mime': 'image/png'})
        self.source.write_text('\n'.join('![figure](' + a['path'] + ')' for a in self.records), encoding='utf-8')
        (self.root / 'assets/unreferenced.png').write_bytes(png())
        self.folder = self.root / 'run'

    def node(self, *args):
        if args[0] == '--inspect':
            return json.dumps({'title': 'Visual task', 'introduction': 'See intro figure.',
                'source_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
                'assets': self.records, 'introduction_image_paths': ['assets/intro.png'],
                'modules': [{'id': 'm1', 'title': 'Module'}],
                'questions': [{'id': f'q{i}', 'module_id': 'm1', 'question': 'Read figure', 'explanation': 'Red',
                    'question_sha256': hashlib.sha256(str(i).encode()).hexdigest(), 'image_paths': [f'assets/q{i}.png']} for i in range(9)]})
        Path(args[2]).write_text(json.dumps({'id': 'visual-topic', 'version': 'v1'}))
        Path(args[3]).write_text(json.dumps({'course_version': 'v1'}))
        return '{}'

    def run_prepare(self, client):
        with patch('math_learning.author_import._node', side_effect=self.node), patch('math_learning.author_import.Catalog._validate_topic'):
            return prepare_topic(self.source, 'visual-topic', self.folder, client, mode='semantic')

    def test_real_image_parts_split_batches_bind_hashes_and_snapshot_only_references(self):
        client = VisionFake()
        manifest = self.run_prepare(client)
        self.assertEqual(manifest['status'], 'approved', manifest)
        self.assertEqual(len(client.messages), 4)
        for messages in client.messages:
            parts = messages[-1]['content']
            images = [p for p in parts if p['type'] == 'image_url']
            self.assertLessEqual(len(images), 8)
            self.assertNotIn('data:image', parts[0]['text'])
            self.assertEqual(base64.b64decode(images[0]['image_url']['url'].split(',')[1]), png())
            self.assertTrue(any(p.get('text', '').endswith('assets/intro.png') for p in parts))
        self.assertFalse((self.folder / 'assets/unreferenced.png').exists())
        for record in self.records:
            self.assertEqual(manifest['files'][record['path']], record['sha256'])
        review = json.loads((self.folder / 'review.json').read_text())
        self.assertEqual(review['assets'], self.records)
        self.assertEqual(manifest['assets'], self.records)

    def test_semantic_image_rejection_keeps_failure_and_never_converts(self):
        client = VisionFake(reject=True)
        result = self.run_prepare(client)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(client.messages), 2)
        self.assertFalse((self.folder / 'topic.json').exists())
        self.assertTrue((self.folder / 'review-rejection.json').exists())

    def test_changed_image_fails_before_api(self):
        (self.root / 'assets/q0.png').write_bytes(png() + b'x')
        client = VisionFake()
        self.assertEqual(self.run_prepare(client)['status'], 'failed')
        self.assertEqual(client.messages, [])

    def test_asset_path_hash_crc_and_reference_inventory_reject_unsafe_inputs(self):
        good = self.records[0]
        for path in ('../outside.png', '/root.png', 'CON.png', 'assets\\x.png', 'assets/x.png:stream', 'assets./x.png'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_assets([{**good, 'path': path}], lambda name: png())
        broken = bytearray(png()); broken[25] ^= 1
        with self.assertRaises(ValueError):
            validate_assets([good], lambda name: bytes(broken))
        with self.assertRaises(ValueError):
            validate_assets([good], lambda name: png(), source='No image reference')
        with self.assertRaises(ValueError):
            validate_assets([{**good, 'sha256': '0' * 64}], lambda name: png())

    def test_too_many_images_for_single_question_never_silently_drops_context(self):
        with self.assertRaises(ValueError):
            list(_batches([{'image_paths': [f'{i}.png' for i in range(9)]}], []))


if __name__ == '__main__':
    unittest.main()
