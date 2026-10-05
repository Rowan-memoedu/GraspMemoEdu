"""Visible structured answers reuse authored judging keys, never text explanations."""
from copy import deepcopy
import unittest
from graspmemoedu.reference_answers import reference_fields
from graspmemoedu.self_assessment import presentation, protect
from tests.training_fixtures import training_bank


class ReferenceAnswerTests(unittest.TestCase):
    def questions(self):
        rows = {q['id']: q for q in training_bank()['groups'][1]['questions']}
        rows['match'] = {**deepcopy(rows['essay']), 'interaction': {
            'version': 1, 'type': 'matching', 'left': [{'id': 'a', 'text': '1'}],
            'right': [{'id': 'b', 'text': '一'}], 'pairs': [{'left': 'a', 'right': 'b'}]}}
        return rows

    def test_text_and_unrevealed_questions_never_have_reference_fields(self):
        for q in self.questions().values():
            self.assertEqual(reference_fields(q), {})
            if (q.get('interaction') or {}).get('type', 'text') == 'text':
                shown = presentation(q, {'revealed': True})
                self.assertNotIn('reference_answer', shown)
                self.assertNotIn('reference_answer_html', shown)
                self.assertEqual(shown['explanation_html'], q['explanation_html'])

    def test_all_four_structured_types_reuse_existing_answer_data_without_mutation(self):
        rows = self.questions()
        for ident, expected in [('choice', '2'), ('blank', '1. 2'), ('boolean', '1. ✓'), ('match', '1 → 一')]:
            q = rows[ident]; original = deepcopy(q)
            fields = reference_fields(q, True)
            self.assertEqual(fields['reference_answer'], expected)
            self.assertTrue(fields['reference_answer_html'])
            self.assertEqual(q, original)

    def test_imported_math_option_html_is_reused(self):
        q = self.questions()['choice']
        q['interaction']['options'][0]['html'] = '<p><span class="mathInline"><svg><path d="M0 0"/></svg></span></p>'
        fields = reference_fields(q, True)
        self.assertEqual(fields['reference_answer_html'], q['interaction']['options'][0]['html'])

    def test_fill_blank_reference_latex_uses_existing_offline_renderer(self):
        q = self.questions()['blank']; q['interaction']['grading'] = 'semantic'
        q['interaction']['blanks'][0]['answers'] = ['$\\frac{1}{2}$']
        fields = reference_fields(q, True)
        self.assertEqual(fields['reference_answer'], '1. $\\frac{1}{2}$')
        self.assertIn('<svg', fields['reference_answer_html'])
        self.assertNotIn('$\\frac', fields['reference_answer_html'])

    def test_revocation_hides_revealed_answer_but_retains_allowed_feedback(self):
        q = self.questions()['blank']; q['interaction']['grading'] = 'semantic'
        item = {'phase': 'answer', **presentation(q, {'revealed': True})}
        protect(item, False)
        self.assertNotIn('reference_answer', item)
        self.assertNotIn('reference_answer_html', item)
        item = {'phase': 'feedback', **presentation(q, {'revealed': True}, visible=True)}
        protect(item, False)
        self.assertEqual(item['reference_answer'], '1. 2')
