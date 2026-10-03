"""No live provider: financial limits and the bounded production path."""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from datetime import date
import json
import multiprocessing
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from graspmemoedu.conversion_budget import ConversionBudget, BudgetStopped, DEFAULT_LIMITS
from graspmemoedu.question_conversion import bounded_call, generate_batch, run_conversion, production_plan
from graspmemoedu.author_import import CompatibleClient
from tests.fixtures import synthetic_course, temporary_test_directory
from tests.test_question_bank import bank_fixture


def limits(**changes):
    return {**DEFAULT_LIMITS, 'max_requests': 24, 'max_tokens': 250000,
            'max_cost_rmb': '10', 'input_rmb_per_million': '1', 'output_rmb_per_million': '2',
            'pricing_checked_on': date.today().isoformat(), **changes}


def raw_result(content, *, response_id='response-one', **changes):
    return {'id': response_id, 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200,
                      'completion_tokens_details': {'reasoning_tokens': 50}}, **changes}


def contender(arguments):
    path, key = arguments
    try:
        budget = ConversionBudget(path, {'fixed': True}, limits(max_requests=1))
        budget.reserve(key, [{'role': 'user', 'content': 'small input'}])
        return True
    except BudgetStopped:
        return False


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_test_directory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def budget(self, **changes):
        return ConversionBudget(self.root / 'budget.sqlite3', {'fixed': True}, limits(**changes))

    def test_default_zero_blocks_before_constructing_client(self):
        budget = ConversionBudget(self.root / 'budget.sqlite3', {})
        factory = Mock(side_effect=AssertionError('No credentials or API'))
        with self.assertRaises(BudgetStopped):
            bounded_call(factory, budget, self.root / 'requests', [{'id': 'x'}])
        factory.assert_not_called()
        self.assertEqual(budget.summary()['requests'], 0)

    def test_atomic_limits_across_independent_processes(self):
        path = str(self.root / 'budget.sqlite3')
        with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context('spawn')) as pool:
            results = list(pool.map(contender, [(path, str(i)) for i in range(8)]))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.budget(max_requests=1).summary()['requests'], 1)

    def test_tokens_money_and_concurrency_reserved_before_network(self):
        for boundary in ({'max_tokens': 8192}, {'max_cost_rmb': '0.000001'}):
            with temporary_test_directory() as directory:
                budget = ConversionBudget(Path(directory) / 'budget.sqlite3', {}, limits(**boundary))
                with self.assertRaises(BudgetStopped):
                    budget.reserve('first', [{'role': 'user', 'content': 'input'}])
                self.assertEqual(budget.summary()['requests'], 0)
        budget = self.budget()
        budget.reserve('one', [])
        budget.reserve('two', [])
        with self.assertRaises(BudgetStopped):
            budget.reserve('three', [])

    def test_unknown_usage_cannot_be_released_by_restart_or_resend(self):
        budget = self.budget()
        request_id = budget.reserve('one', [])
        before = budget.summary()['tokens_used_or_reserved']
        with self.assertRaises(BudgetStopped):
            budget.settle(request_id, {'id': 'missing-usage'})
        restored = self.budget()
        self.assertEqual(restored.summary()['tokens_used_or_reserved'], before)
        with self.assertRaises(BudgetStopped):
            restored.reserve('two', [])
        with self.assertRaises(BudgetStopped):
            restored.assert_resumable()

    def test_timeout_is_not_automatically_retried(self):
        client = Mock(model='deepseek-flash')
        client.complete_limited.side_effect = TimeoutError('synthetic timeout')
        budget = self.budget()
        with self.assertRaises(TimeoutError):
            generate_batch(lambda: client, budget, self.root / 'requests', [{'id': 'q', 'reference_answer': '5'}])
        self.assertEqual(client.complete_limited.call_count, 1)
        self.assertEqual(budget.summary()['requests'], 1)
        self.assertIsNotNone(budget.summary()['stopped'])

    def test_frozen_plan_cannot_be_changed_to_reset_or_enlarge_budget(self):
        self.budget()
        with self.assertRaises(BudgetStopped):
            self.budget(max_requests=23)
        with self.assertRaises(BudgetStopped):
            ConversionBudget(self.root / 'budget.sqlite3', {'changed': True}, limits())
        for change in ({'max_requests': 25}, {'max_tokens': 250001}, {'max_output_tokens': 8193}, {'max_concurrency': 3}):
            with self.assertRaises(ValueError):
                self.budget(**change)

    def test_complete_response_and_cache_do_not_double_bill(self):
        client = Mock(model='deepseek-flash')
        client.complete_limited.return_value = raw_result({'questions': []})
        budget = self.budget()
        a = bounded_call(lambda: client, budget, self.root / 'requests', [{'id': 'q'}])
        b = bounded_call(lambda: client, budget, self.root / 'requests', [{'id': 'q'}])
        self.assertEqual(a, b)
        self.assertEqual(budget.summary()['tokens_used_or_reserved'], 200)
        self.assertEqual(client.complete_limited.call_count, 1)
        self.assertEqual(client.complete_limited.call_args.kwargs['max_tokens'], 8192)

    def test_output_cap_reaches_provider_payload(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(raw_result({})).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch('graspmemoedu.author_import.urllib.request.build_opener', return_value=opener):
            CompatibleClient('https://api.deepseek.com', 'synthetic-key', 'deepseek-flash').complete_limited([], max_tokens=4096, thinking=True)
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload['max_tokens'], 4096)
        self.assertEqual(payload['thinking']['type'], 'enabled')

    def test_one_repair_only_resends_failed_items(self):
        rows = lambda q: {'id': q, 'distractors': [{'value': str(n)} for n in (1, 2, 3, 4, 6)]}
        good, bad = rows('a'), rows('b')
        bad['distractors'][0]['value'] = '5'
        client = Mock(model='deepseek-flash')
        client.complete_limited.side_effect = [raw_result({'questions': [good, bad]}),
                                               raw_result({'questions': [rows('b')]}, response_id='response-two')]
        result = generate_batch(lambda: client, self.budget(), self.root / 'requests',
                                [{'id': 'a', 'reference_answer': '5'}, {'id': 'b', 'reference_answer': '5'}])
        self.assertEqual(set(result), {'a', 'b'})
        sent = json.loads(client.complete_limited.call_args.args[0][-1]['content'].split('输入数据：\n')[1])
        self.assertEqual([q['id'] for q in sent], ['b'])
        self.assertEqual(client.complete_limited.call_count, 2)

    def test_second_hard_failure_stops_without_nested_review(self):
        client = Mock(model='deepseek-flash')
        client.complete_limited.side_effect = [raw_result({}), raw_result({}, response_id='response-two')]
        budget = self.budget()
        with self.assertRaises(BudgetStopped):
            generate_batch(lambda: client, budget, self.root / 'requests', [{'id': 'q', 'reference_answer': '5'}])
        self.assertEqual(client.complete_limited.call_count, 2)
        self.assertIsNotNone(budget.summary()['stopped'])

    def test_sample_stops_before_full_production_and_uses_frozen_plan(self):
        course = synthetic_course(6)
        for module in course['modules']:
            for q in [module['example'], *module['practices']]:
                q['reference_answer'] = '5'
        client, counter = Mock(model='deepseek-flash'), [0]
        def generate(messages, **kwargs):
            counter[0] += 1
            sources = json.loads(messages[-1]['content'].split('输入数据：\n')[1])
            return raw_result({'questions': [{'id': s['id'], 'distractors': [{'value': str(n)} for n in (1,2,3,4,6)]}
                                             for s in sources]}, response_id='sample-' + str(counter[0]))
        client.complete_limited.side_effect = generate
        result = run_conversion(course, self.root, [4], client_factory=lambda: client)
        self.assertEqual(result['status'], 'sample_review_required')
        self.assertEqual(counter[0], 2)
        self.assertFalse((self.root / 'bank.json').exists())
        sample = json.loads((self.root / 'sample.json').read_text())
        self.assertEqual(len(sample), 8)
        self.assertEqual(len({item['source']['id'].split('-example')[0] for item in sample[:5]}), 5)
        run_conversion(course, self.root, [4], client_factory=lambda: client)
        self.assertEqual(counter[0], 2)
        changed = deepcopy(course); changed['title'] += ' changed'
        with self.assertRaises(BudgetStopped):
            run_conversion(changed, self.root, [4], client_factory=lambda: client)

    def test_existing_bank_readback_never_builds_a_client(self):
        course = synthetic_course(1)
        for q in [course['modules'][0]['example'], *course['modules'][0]['practices']]:
            q['reference_answer'] = '5'
        bank = bank_fixture(course)
        for qid, v in bank['questions'].items():
            original = next(q for q in [course['modules'][0]['example'], *course['modules'][0]['practices']] if q['id'] == qid)
            v['question'] = original['question'] + '\n\n请选择一个正确答案。'
            for o in v['interaction']['options']:
                o['html'] = '<span data-tex="' + o['text'].strip('$') + '"><svg></svg></span>'
        (self.root / 'bank.json').write_text(json.dumps(bank))
        client = Mock(side_effect=AssertionError('Existing bank must not call API'))
        result = run_conversion(course, self.root, [], client_factory=client)
        self.assertEqual(result['status'], 'reused')
        self.assertEqual(result['api_requests'], 0)
        client.assert_not_called()
