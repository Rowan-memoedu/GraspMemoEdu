"""Offline conversion canaries, with independent parameters and a fake provider."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from graspmemoedu.conversion_run import ConversionRun, ConversionStopped
from graspmemoedu.mechanical_templates import group_sources, expand_candidate, expression_value
from graspmemoedu.physics_question_conversion import (
    integer_blank, build_variant, canonical_math, PhysicsAdapter, production_plan,
    run_conversion, audit_bank, make_source, local_choices, numeric_display, choice_count)
from graspmemoedu.question_types import deterministic_verdict, demo_answer
from tests.fixtures import synthetic_course, temporary_test_directory
from tests.test_conversion_budget import raw_result


def course_fixture(topic_id='physics-fixture'):
    course = synthetic_course(1)
    course['id'] = topic_id
    for i, q in enumerate([course['modules'][0]['example'], *course['modules'][0]['practices']]):
        n = 3 + i
        q.update(question=f'固定条件下，长度为 ${n}\\,\\mathrm m$，求速度。',
                 html=f'<p>固定条件下，长度为 {n} m，求速度。</p>',
                 reference_answer=rf'$v=\frac{{{2*n+1}}}{{2}}\,\mathrm{{m/s}}$',
                 constraints='保留原单位', explanation_html='<p>给定关系的结果。</p>')
    return course


class PhysicsConversionTests(unittest.TestCase):
    def test_finite_judgments_are_enumerated_without_extra_reasons(self):
        q = {'question': '位置 $A$ 与 $B$ 中，哪些满足约束？',
             'reference_answer': '$A$ 满足约束，$B$ 不满足约束。'}
        self.assertEqual(len(set(local_choices(q))), 4)
        self.assertEqual(local_choices(q)[0], q['reference_answer'])
        q = {'question': '给定一个关系。这能否写成完整约束？',
             'reference_answer': '能写成完整约束。后面是完整推导。'}
        self.assertEqual(local_choices(q), ['能写成完整约束。', '不能写成完整约束。'])
        q['question'] += '并写出其位置方程。'
        self.assertIsNone(local_choices(q))

    def test_template_arithmetic_and_uniform_numeric_display(self):
        for value in ('2[[p1]]', '[[p1]]2', '[[p1]][[p2]]'):
            with self.assertRaises(ValueError):
                expand_candidate({'distractors': [{'value': value, 'reason': 'Synthetic'}]}, {'p1': '3', 'p2': '4'})
        self.assertEqual(numeric_display('-3*2'), '-6')
        self.assertEqual(numeric_display(r'-\frac{2}{2}'), '-1')
        self.assertEqual(numeric_display('0.5'), r'\frac{1}{2}')

    def test_only_whole_integer_quantities_become_blanks(self):
        for reference, expected in [(r'$v=-12\,\mathrm{km/h}$', '-12'),
                                    (r'\ddot y=0\,\mathrm{m/s^2}', '0'),
                                    (r'x=\frac{12}{3}\,\mathrm m', '4'), ('17', '17')]:
            q = {'reference_answer': reference, 'question': 'Source', 'id': 'q'}
            self.assertEqual(integer_blank(q)['answer'], expected)
            variant = build_variant(q)
            self.assertEqual(variant['interaction']['grading'], 'exact')
            self.assertTrue(deterministic_verdict(variant, demo_answer(variant))[0])
        for reference in ('y=0', r'$v=\frac{3}{2}\,\mathrm{m/s}$',
                          '坐标为 (3,4) m；不需要其他参数。', r'$x^2+y^2=25\,\mathrm{m^2}$'):
            self.assertIsNone(integer_blank({'reference_answer': reference}))

    def test_supported_algebra_detects_equivalent_distractors(self):
        self.assertEqual(canonical_math(r'2x+2y=0'), canonical_math('x+y=0'))
        self.assertEqual(canonical_math(r'-2\sqrt3'), canonical_math(r'-\sqrt{12}'))
        source = {'option_kind': 'scalar', 'scalar': {'value': r'\frac{1}{2}'}, 'correct_option': '', 'choice_count': 6}
        rows = [{'value': v, 'reason': 'Synthetic'} for v in ('0.5', '2', '3', '4', '5')]
        with self.assertRaises(ValueError):
            PhysicsAdapter.validate(source, {'distractors': rows})

    def test_mechanical_grouping_preserves_conditions_and_qualified_ids(self):
        a, b = course_fixture('topic-a'), course_fixture('topic-b')
        sources = [make_source(c, q) for c in (a, b) for q in [c['modules'][0]['example'], *c['modules'][0]['practices']]]
        changed = deepcopy(sources[1]); changed['id'] += '-condition'; changed['question'] += '仅沿负方向运动。'
        groups = group_sources([*sources, changed])
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0]['mechanical']['size'], 16)
        self.assertTrue(any(role['field'] == 'question' for roles in groups[0]['parameter_roles'].values() for role in roles))
        self.assertTrue(any(role['field'] == 'correct_option' for roles in groups[0]['parameter_roles'].values() for role in roles))
        content = PhysicsAdapter.messages(groups[:1])[1]['content'][0]['text']
        self.assertNotIn('"_members"', content)
        self.assertIn('parameter_sets', content)

    def test_expansion_checks_every_member_and_rejects_code_execution(self):
        course = course_fixture()
        source = group_sources([make_source(course, q) for q in [course['modules'][0]['example'], *course['modules'][0]['practices']]])[0]
        candidate = {'distractors': [{'value': '[[answer+' + str(n) + ']]', 'reason': 'Synthetic'} for n in (1,2,3,4,5)]}
        self.assertEqual(PhysicsAdapter.validate(source, candidate)['mechanical_members'], 16)
        # A bad template may be distinct for the representative but correct for
        # another member; all mechanical expansions must be checked locally.
        candidate['distractors'][0]['value'] = r'\frac{9}{2}'
        with self.assertRaises(ValueError):
            PhysicsAdapter.validate(source, candidate)
        for text in ('__import__("os").system("echo unsafe")', 'p1.__class__', '2**1000', '1/0'):
            with self.assertRaises((ValueError, SyntaxError)):
                expression_value(text, {'p1': '3'})

    def test_sample_cache_then_full_banks_with_zero_individual_member_calls(self):
        course = course_fixture()
        with temporary_test_directory() as directory:
            root = Path(directory)
            client = Mock(model='deepseek-flash')
            def generate(messages, **kwargs):
                payload = json.loads(messages[1]['content'][0]['text'].split('输入数据：\n')[1])
                rows = [{'id': s['id'], 'distractors': [{'value': '[[answer+'+str(n)+']]', 'reason': 'Synthetic'} for n in (1,2,3,4,5)]}
                        for s in payload['questions']]
                return raw_result({'questions': rows})
            client.complete_limited.side_effect = generate
            result = run_conversion([course], root, client_factory=lambda: client)
            self.assertEqual(result['status'], 'sample_review_required')
            self.assertEqual(client.complete_limited.call_count, 1)
            self.assertFalse((root / course['id'] / 'bank.json').exists())
            run_conversion([course], root, client_factory=lambda: client)
            result = run_conversion([course], root, sample_reviewed=True, client_factory=lambda: client)
            self.assertEqual(client.complete_limited.call_count, 1)
            self.assertEqual(result['topics'][0]['choice'], 16)
            bank = json.loads((root / course['id'] / 'bank.json').read_text())
            self.assertEqual(audit_bank(bank, course)['questions'], 16)

    def test_no_financial_gate_and_uncertain_request_is_not_resent(self):
        with temporary_test_directory() as directory:
            root = Path(directory)
            run = ConversionRun(root / 'run.sqlite3', {'scope': 'fixed'})
            for i in range(30):
                request = run.reserve(str(i), [])
                run.settle(request, {'id': str(i)})
            self.assertEqual(run.summary()['requests'], 30)
            request = run.reserve('timeout', [])
            run.uncertain(request, 'TimeoutError')
            with self.assertRaises(ConversionStopped):
                ConversionRun(root / 'run.sqlite3', {'scope': 'fixed'}).assert_resumable()


if __name__ == '__main__':
    unittest.main()
