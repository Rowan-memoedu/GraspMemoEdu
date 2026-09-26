"""Default import is deterministic field mapping, never an AI subject audit."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from graspmemoedu.author_import import prepare_topic, resume_topic, sample_batch_integrity
from tests.fixtures import temporary_test_directory


class MechanicalImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'input.md'
        lines = ['- Mechanical fixture', '  - Introduction', '    - Retain original author text.',
                 '  - Example 1: Field mapping', '    - State the author-provided answer.',
                 '    - Explanation', '      - Original answer, with all reasoning retained.',
                 '      - Second paragraph with $x=2$ and units $\\mathrm{m/s}$.']
        for i in range(1, 8):
            lines += [f'    - Practice {i}', '      - Read the source answer.',
                      '      - Explanation', f'        - Original complete explanation {i}.']
        self.source.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def test_default_mechanical_mapping_has_no_api_and_exact_explanation_answers(self):
        before = self.source.read_bytes()
        with patch('graspmemoedu.author_import.make_client', side_effect=AssertionError('API forbidden')):
            result = prepare_topic(self.source, 'mechanical-fixture', self.root / 'run')
        self.assertEqual(result['status'], 'approved', result)
        self.assertEqual(result['review_mode'], 'mechanical')
        self.assertFalse(result['semantic_review_performed'])
        self.assertIsNone(result['model'])
        run = self.root / 'run'
        inspection = json.loads((run / 'inspection.json').read_text(encoding='utf-8'))
        answers = json.loads((run / 'answers.json').read_text(encoding='utf-8'))
        for question in inspection['questions']:
            self.assertEqual(answers['answers'][question['id']]['reference_answer'], question['explanation'])
        review = json.loads((run / 'review.json').read_text(encoding='utf-8'))
        self.assertEqual(review['status'], 'mechanically_validated')
        self.assertTrue(all('approved' not in q for q in review['questions']))
        audit = json.loads((run / 'audit.json').read_text(encoding='utf-8'))
        self.assertEqual(audit['method'], 'mechanical-import')
        self.assertFalse(audit['semantic_review_performed'])
        self.assertEqual(list(run.glob('*-request.json')), [])
        self.assertEqual(self.source.read_bytes(), before)

    def test_injected_client_cannot_implicitly_enable_semantic_review(self):
        class ForbiddenClient:
            def complete(self, messages):
                raise AssertionError('API forbidden')
        result = prepare_topic(self.source, 'mechanical-fixture', self.root / 'run', client=ForbiddenClient())
        self.assertEqual(result['status'], 'approved', result)

    def test_missing_explanation_fails_structure_without_calling_api(self):
        self.source.write_text(self.source.read_text().replace('    - Explanation', '    - Missing field', 1))
        with patch('graspmemoedu.author_import.make_client', side_effect=AssertionError('API forbidden')):
            result = prepare_topic(self.source, 'mechanical-fixture', self.root / 'run')
        self.assertEqual(result['status'], 'failed')
        self.assertFalse((self.root / 'run/topic.json').exists())

    def test_resume_defaults_to_zero_api_despite_old_semantic_rejection(self):
        old = self.root / 'old'
        self.assertEqual(prepare_topic(self.source, 'mechanical-fixture', old)['status'], 'approved')
        manifest = json.loads((old / 'manifest.json').read_text())
        manifest.update(status='failed', error='Old semantic rejection')
        (old / 'manifest.json').write_text(json.dumps(manifest))
        old_manifest = (old / 'manifest.json').read_bytes()
        with patch('graspmemoedu.author_import.make_client', side_effect=AssertionError('API forbidden')):
            result = resume_topic(old, self.root / 'resumed')
        self.assertEqual(result['status'], 'approved', result)
        self.assertFalse(result['semantic_review_performed'])
        self.assertEqual((old / 'manifest.json').read_bytes(), old_manifest)

    def test_explicit_sample_limits_entire_batch_to_two_questions(self):
        first, second = self.root / 'first', self.root / 'second'
        prepare_topic(self.source, 'first', first)
        prepare_topic(self.source, 'second', second)
        class SampleClient:
            model = 'sample-fixture'
            def __init__(self):
                self.calls = []
            def complete(self, messages):
                self.calls.append(messages)
                rows = json.loads(messages[-1]['content'].split('待审核数据：\n')[1])
                return {'samples': [{'id': row['id'], 'serious_damage': False, 'issues': []} for row in rows]}
        client = SampleClient()
        result = sample_batch_integrity([first, second], self.root / 'sample', client=client)
        self.assertEqual(result['status'], 'no_damage_detected', result)
        self.assertEqual(result['sample_count'], 2)
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(result['semantic_review_performed'])
        self.assertTrue(result['samples'][0]['id'].startswith('first:'))
        self.assertTrue(result['samples'][1]['id'].startswith('second:'))
        with self.assertRaises(ValueError):
            sample_batch_integrity([first, second], self.root / 'too-many', client=client, max_questions=3)


if __name__ == '__main__':
    unittest.main()
