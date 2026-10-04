"""Independent mechanical/repair canaries; no external requests or course data."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from graspmemoedu.conversion_recovery import recover_candidates, reviewed_reuse, verify_recovery_evidence
from graspmemoedu.conversion_run import ConversionRun, ConversionStopped
from graspmemoedu.mechanical_templates import expand_candidate
from graspmemoedu.physics_question_conversion import PhysicsAdapter, production_plan, run_conversion
from graspmemoedu.question_conversion import generate_batch, write_json
from graspmemoedu.question_bank import digest
from graspmemoedu.conversion_response import decode_questions
from tests.fixtures import temporary_test_directory
from tests.test_conversion_budget import raw_result
from tests.test_physics_conversion import course_fixture


def candidate(offsets=(1, 2, 3, 4, 5)):
    return {'distractors': [{'value': [{'expr': f'answer+{n}'}], 'reason': 'Synthetic independent offset'} for n in offsets]}


class ConversionRecoveryTests(unittest.TestCase):
    def test_thinking_configuration_uses_small_frozen_batches(self):
        courses = [course_fixture('independent-' + str(n)) for n in range(3)]
        for course in courses:
            course['modules'][0]['practices'] = []
        client, sizes = Mock(model='deepseek-flash'), []
        def generate(messages, **kwargs):
            self.assertIs(kwargs['thinking'], True)
            payload = json.loads(messages[1]['content'][0]['text'].split('输入数据：\n')[1])
            sizes.append(len(payload['questions']))
            return raw_result({'questions': [{'id': s['id'], **candidate()} for s in payload['questions']]}, response_id=str(len(sizes)))
        client.complete_limited.side_effect = generate
        with temporary_test_directory() as directory:
            result = run_conversion(courses, Path(directory), config={'model': 'deepseek-flash', 'thinking': True, 'max_output_tokens': 8192}, client_factory=lambda: client)
            self.assertEqual(result['status'], 'sample_review_required')
            self.assertEqual(sizes, [2, 1])

    def test_malformed_neighbor_does_not_change_or_discard_intact_question(self):
        first = {'id': 'independent-a', **candidate()}
        last = {'id': 'independent-c', **candidate((2,3,4,5,6))}
        malformed = '{"questions":[' + json.dumps(first) + ',{"id":"bad","distractors":[{"value":,"reason":"bad"}]},' + json.dumps(last) + ']}'
        self.assertEqual(decode_questions(malformed), {'questions': [first, last]})
        # A quoted JSON-looking string is not a separate question.
        quote = json.dumps({'id': 'quoted', 'distractors': []})
        broken = '{"questions":[{"bad":' + json.dumps(quote) + ',"value":},' + json.dumps(last) + ']}'
        self.assertEqual(decode_questions(broken), {'questions': [last]})

    def test_structured_parts_keep_arithmetic_separate_from_display(self):
        value = {'distractors': [{'value': ['长度为 $', {'expr': 'p1/2'}, '$。'], 'reason': 'Synthetic'}]}
        self.assertEqual(expand_candidate(value, {'p1': '7'})['distractors'][0]['value'], r'长度为 $\frac{7}{2}$。')
        for params, expected in [({'p1': '8'}, '4'), ({'p1': '12'}, '6')]:
            value['distractors'][0]['value'] = [{'expr': 'p1/2'}]
            self.assertEqual(expand_candidate(value, params)['distractors'][0]['value'], expected)
        for parts in (['2', {'expr': 'p1'}], [{'expr': 'p1'}, '2'], [{'expr': 'p1'}, {'expr': 'p1'}],
                      ['[[p1]]'], [{'expr': 'p1+'}], [{'call': 'p1'}]):
            value['distractors'][0]['value'] = parts
            with self.assertRaises(ValueError):
                expand_candidate(value, {'p1': '7'})

    def test_repair_contains_candidate_and_specific_equivalence_evidence(self):
        _, sources = production_plan([course_fixture()])
        bad = candidate((0, 2, 3, 4, 5))
        with self.assertRaises(ValueError) as caught:
            PhysicsAdapter.validate(sources[0], bad)
        detail = PhysicsAdapter.repair_details(sources[0], bad, caught.exception)
        self.assertEqual(detail['previous_candidate'], bad)
        self.assertEqual(detail['failures'][0]['distractor'], 1)
        self.assertEqual(detail['failures'][0]['error'], 'Equivalent to correct answer')
        self.assertTrue(detail['failures'][0]['parameters'])

    def test_batch_preserves_good_results_and_repairs_only_failed_sources(self):
        _, sources = production_plan([course_fixture('fixture-a'), course_fixture('fixture-b')])
        saved, payloads = [], []
        client = Mock(model='deepseek-flash')
        def generate(messages, **kwargs):
            payload = json.loads(messages[1]['content'][0]['text'].split('输入数据：\n')[1])
            payloads.append(payload)
            return raw_result({'questions': [{'id': s['id'], **(candidate() if s['topic_id'] == 'fixture-a' else candidate((0,2,3,4,5)))}
                                            for s in payload['questions']]}, response_id=str(len(payloads)))
        client.complete_limited.side_effect = generate
        with temporary_test_directory() as directory:
            root = Path(directory)
            run = ConversionRun(root / 'run.sqlite3', {'scope': 'independent-canary'})
            with self.assertRaises(ConversionStopped):
                generate_batch(lambda: client, run, root / 'requests', sources, adapter=PhysicsAdapter,
                               on_accepted=lambda s, a: saved.append(s['id']))
            self.assertEqual(saved, [sources[0]['id']])
            self.assertEqual(client.complete_limited.call_count, 2)
            self.assertEqual(len(payloads[1]['questions']), 1)
            self.assertIn('previous_candidate', payloads[1]['questions'][0]['local_validation_error'])
            recovered = recover_candidates([course_fixture('fixture-a'), course_fixture('fixture-b')], [root])
            reuse = reviewed_reuse(recovered, saved)
            verify_recovery_evidence(reuse['items'][0])
            edited = deepcopy(reuse['items'][0])
            edited['artifact']['candidate']['distractors'][0]['value'] = ['999']
            with self.assertRaises(ValueError):
                verify_recovery_evidence(edited)
            changed = course_fixture('fixture-a')
            changed['modules'][0]['example']['reference_answer'] = '$v=999\\,\\mathrm{m/s}$'
            rejected = recover_candidates([changed], [root])
            self.assertFalse(rejected['items'])

    def test_reviewed_original_response_is_reused_without_provider_call(self):
        course = course_fixture()
        client = Mock(model='deepseek-flash')
        def generate(messages, **kwargs):
            payload = json.loads(messages[1]['content'][0]['text'].split('输入数据：\n')[1])
            return raw_result({'questions': [{'id': s['id'], **candidate()} for s in payload['questions']]})
        client.complete_limited.side_effect = generate
        with temporary_test_directory() as directory:
            root = Path(directory)
            run_conversion([course], root / 'first', client_factory=lambda: client)
            recovery = recover_candidates([course], [root / 'first'])
            reuse = reviewed_reuse(recovery, [item['id'] for item in recovery['items']])
            write_json(root / 'reuse.json', reuse)
            factory = Mock(side_effect=AssertionError('Reused content must not call a provider'))
            result = run_conversion([course], root / 'second', client_factory=factory, reuse_reviewed=root / 'reuse.json')
            self.assertEqual(result['status'], 'sample_review_required')
            self.assertEqual(result['usage']['requests'], 0)
            sample = json.loads((root / 'first/sample.json').read_text(encoding='utf-8'))
            write_json(root / 'continuation.json', {'validated_cache_directory': str(root / 'first'),
                                                  'sample_reviewed': True, 'sample_sha256': digest(sample)})
            resumed = run_conversion([course], root / 'continued', client_factory=factory, reuse_reviewed=root / 'continuation.json')
            self.assertEqual(resumed['usage']['requests'], 0)
            continuation = json.loads((root / 'continuation.json').read_text(encoding='utf-8'))
            continuation['quality_rejections'] = {sample[0]['source']['id']: 'Synthetic review rejects this saved item'}
            write_json(root / 'rejected.json', continuation)
            run_conversion([course], root / 'regenerated', client_factory=lambda: client, reuse_reviewed=root / 'rejected.json')
            self.assertEqual(client.complete_limited.call_count, 2)


if __name__ == '__main__':
    unittest.main()
