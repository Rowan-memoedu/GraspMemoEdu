import hashlib
import json
import struct
from pathlib import Path
import unittest
import zlib
from unittest.mock import patch

from math_learning.author_import import prepare_topic, resume_topic as _resume_topic
from tests.fixtures import temporary_test_directory
from tests.test_author_import_images import png


def resume_topic(*args, **kwargs):
    return _resume_topic(*args, **kwargs, mode='semantic')


class ResumeFake:
    model = 'fixture-model'

    def __init__(self, reject=False, fail_review=False):
        self.calls, self.reject, self.fail_review = [], reject, fail_review

    def complete(self, messages):
        content = messages[-1]['content']
        text = content[0]['text'] if isinstance(content, list) else content
        rows = json.loads(text.split('待审核数据：\n')[1])
        self.calls.append(rows)
        if 'source' in rows[0]:
            if self.fail_review:
                return 'invalid json'
            return {'questions': [{'id': r['source']['id'], 'approved': not (self.reject and r['source']['id'] == 'q1'),
                'issues': ['Original denial'] if self.reject and r['source']['id'] == 'q1' else [],
                'rationale': 'Independent fixture check'} for r in rows]}
        return {'answers': [{'id': r['id'], 'reference_answer': '2', 'answer_type': 'number', 'constraints': 'Exact'} for r in rows]}


class ImportResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'original.md'
        self.source.write_text(json.dumps({'q1': '1+1', 'q2': '2*1'}), encoding='utf-8')
        self.old, self.new = self.root / 'old', self.root / 'new'
        self.mock_node = patch('math_learning.author_import._node', side_effect=self.node)
        self.mock_validate = patch('math_learning.author_import.Catalog._validate_topic')
        self.mock_node.start(); self.mock_validate.start()
        self.addCleanup(self.mock_node.stop); self.addCleanup(self.mock_validate.stop)

    def node(self, *args):
        if args[0] == '--inspect':
            path = Path(args[1]); questions = json.loads(path.read_text())
            return json.dumps({'title': 'Fixture', 'introduction': 'Calculate',
                'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'modules': [{'id': 'm1', 'title': 'Module'}],
                'questions': [{'id': key, 'module_id': 'm1', 'line': i + 1, 'question': value,
                    'explanation': 'Equal two', 'question_sha256': hashlib.sha256(value.encode()).hexdigest()}
                    for i, (key, value) in enumerate(questions.items())]})
        Path(args[2]).write_text(json.dumps({'id': 'resume-fixture', 'version': 'v1'}))
        Path(args[3]).write_text(json.dumps({'course_version': 'v1'}))
        return '{}'

    def prepare(self, client):
        return prepare_topic(self.source, 'resume-fixture', self.old, client, mode='semantic')

    def test_complete_package_reuses_all_questions_without_api_and_keeps_old_evidence(self):
        self.assertEqual(self.prepare(ResumeFake())['status'], 'approved')
        old_files = {p.name: p.read_bytes() for p in self.old.iterdir()}
        with patch('math_learning.author_import.make_client', side_effect=AssertionError('No API needed')):
            result = resume_topic(self.old, self.new)
        self.assertEqual(result['status'], 'approved', result)
        self.assertEqual(result['reuse_counts']['reviewed_questions'], 2)
        self.assertEqual(old_files, {p.name: p.read_bytes() for p in self.old.iterdir()})
        review = json.loads((self.new / 'review.json').read_text())
        self.assertTrue(all(q['review_origin'] == 'retained_llm_response' for q in review['questions']))
        self.assertEqual(list(self.new.glob('*-request.json')), [])

    def test_missing_review_only_calls_review_and_keeps_extraction(self):
        self.assertEqual(self.prepare(ResumeFake(fail_review=True))['status'], 'failed')
        client = ResumeFake()
        result = resume_topic(self.old, self.new, client=client)
        self.assertEqual(result['status'], 'approved', result)
        self.assertEqual(len(client.calls), 1)
        self.assertIn('source', client.calls[0][0])

    def test_changed_question_only_invalidates_its_own_extraction_and_review(self):
        self.prepare(ResumeFake())
        self.source.write_text(json.dumps({'q1': '4/2', 'q2': '2*1'}))
        client = ResumeFake()
        result = resume_topic(self.old, self.new, source=self.source, client=client)
        self.assertEqual(result['status'], 'approved', result)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual([q['id'] for q in client.calls[0]], ['q1'])
        self.assertEqual([q['source']['id'] for q in client.calls[1]], ['q1'])
        self.assertEqual(result['reuse_counts']['reviewed_questions'], 1)

    def test_explicit_local_resolution_preserves_denial_and_marks_human_origin(self):
        self.assertEqual(self.prepare(ResumeFake(reject=True))['status'], 'failed')
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        resolution = {'schema_version': 1, 'questions': [{'id': 'q1', 'source_sha256': source_hash,
            'question_sha256': hashlib.sha256(b'1+1').hexdigest(), 'assets': [], 'reviewer': 'Codex',
            'decision': 'approved', 'rationale': 'Verified the arithmetic independently.', 'evidence': ['1 + 1 = 2.']}]}
        with patch('math_learning.author_import.make_client', side_effect=AssertionError('No API needed')):
            result = resume_topic(self.old, self.new, resolutions=resolution)
        self.assertEqual(result['status'], 'approved', result)
        q = next(q for q in json.loads((self.new / 'review.json').read_text())['questions'] if q['id'] == 'q1')
        self.assertEqual(q['review_origin'], 'human_resolution')
        self.assertFalse(q['original_review']['approved'])
        self.assertEqual(q['original_review']['issues'], ['Original denial'])
        self.assertEqual(q['resolution'], resolution['questions'][0])
        self.assertEqual(result['reuse_counts']['human_resolutions'], 1)
        third = self.root / 'third'
        with patch('math_learning.author_import.make_client', side_effect=AssertionError('No API needed')):
            continued = resume_topic(self.new, third)
        self.assertEqual(continued['status'], 'approved', continued)
        self.assertEqual(continued['reuse_counts']['human_resolutions'], 1)

    def test_unresolved_denial_never_calls_api_to_overwrite_it(self):
        self.prepare(ResumeFake(reject=True))
        client = ResumeFake()
        result = resume_topic(self.old, self.new, client=client)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(client.calls, [])
        self.assertTrue((self.new / 'review-1-unresolved.json').exists())

    def test_tampered_retained_response_is_rejected(self):
        self.prepare(ResumeFake())
        response = self.old / 'review-1-1-response.json'
        response.write_bytes(response.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            resume_topic(self.old, self.new, client=ResumeFake())
        self.assertFalse(self.new.exists())

    def test_changed_asset_only_invalidates_questions_that_reference_it(self):
        (self.root / 'assets').mkdir()
        image = self.root / 'assets/q1.png'
        image.write_bytes(png())
        self.source.write_text(json.dumps({'q1': '1+1 ![diagram](assets/q1.png)', 'q2': '2*1'}))
        ordinary_node = self.node
        def image_node(*args):
            result = ordinary_node(*args)
            if args[0] != '--inspect':
                return result
            data = json.loads(result)
            raw = (Path(args[1]).parent / 'assets/q1.png').read_bytes()
            data['assets'] = [{'path': 'assets/q1.png', 'sha256': hashlib.sha256(raw).hexdigest(),
                              'size': len(raw), 'mime': 'image/png'}]
            data['questions'][0]['image_paths'] = ['assets/q1.png']
            return json.dumps(data)
        with patch('math_learning.author_import._node', side_effect=image_node):
            self.assertEqual(self.prepare(ResumeFake())['status'], 'approved')
            payload = b'Comment\x00changed'
            chunk = struct.pack('>I', len(payload)) + b'tEXt' + payload + struct.pack('>I', zlib.crc32(b'tEXt' + payload))
            image.write_bytes(png()[:-12] + chunk + png()[-12:])
            client = ResumeFake()
            result = resume_topic(self.old, self.new, source=self.source, client=client)
        self.assertEqual(result['status'], 'approved', result)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual([row['id'] for row in client.calls[0]], ['q1'])
        self.assertEqual([row['source']['id'] for row in client.calls[1]], ['q1'])


if __name__ == '__main__':
    unittest.main()
