"""Production count, provenance and phase boundaries using independent synthetic input."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import atomic_question_trial as trial
from scripts.produce_atomic_questions import assemble, refill_required, run_point, source_tasks


def document():
    return {'schema': 2, 'topics': [{'id': 'independent', 'title': 'Labels', 'sections': [{
        'status': 'ai_passed', 'import_eligible': True,
        'memory_chunks': [{'id': 'c', 'content': 'The label is amber.'}],
        'memory_points': [{'id': 'p', 'chunk_id': 'c', 'content': 'The label is amber.'}],
        'flashcards': [{'id': 'seed', 'point_ids': ['p'], 'question': 'What is the label?', 'answer': 'amber'}]}]}]}


def candidate(count):
    return {'point_id': 'p', 'status': 'ok', 'reason': '', 'variants': [
        {'point_ids': ['p'], 'question': f'Independent presentation {i}?', 'reference_answer': 'amber',
         'explanation': 'The label is amber.', 'interaction': {'version': 1, 'type': 'text', 'grading': 'semantic'}}
        for i in range(count)]}


class AtomicQuestionProductionTests(unittest.TestCase):
    def test_count_override_and_default_pair_are_both_enforced(self):
        source = source_tasks(document(), 'english', 3)[0]
        trial.validate_pair(candidate(3), source)
        with self.assertRaisesRegex(ValueError, 'Exactly 3'):
            trial.validate_pair(candidate(2), source)
        source.pop('variant_count')
        trial.validate_pair(candidate(2), source)
        with self.assertRaisesRegex(ValueError, 'Exactly two'):
            trial.validate_pair(candidate(3), source)

    def test_all_registered_subjects_use_the_same_source_contract(self):
        for subject in trial.SUBJECTS:
            source = source_tasks(document(), subject, 3)[0]
            self.assertEqual(source['subject_id'], subject)
            self.assertEqual(source['seed_card']['reference_answer'], 'amber')

    def test_export_preserves_original_and_appends_review_only_cards(self):
        original = document()
        before = deepcopy(original)
        tasks = source_tasks(original, 'english', 3)
        enriched = assemble(original, tasks, [{'id': 'p', 'status': 'accepted', 'variants': candidate(3)['variants']}])
        self.assertEqual(original, before)
        section = enriched['topics'][0]['sections'][0]
        self.assertEqual(section['flashcards'][0], before['topics'][0]['sections'][0]['flashcards'][0])
        self.assertEqual(len(section['flashcards']), 4)
        self.assertTrue(all(c['learning_phases'] == ['review'] for c in section['flashcards'][1:]))

    def test_partial_or_rejected_batch_cannot_be_exported(self):
        for results in ([], [{'id': 'p', 'status': 'rejected'}]):
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                assemble(document(), source_tasks(document(), 'english', 3), results)

    def test_local_demand_waits_for_mastery_and_zero_unseen(self):
        cards = [{'id': 'seed'}, {'id': 'a', 'learning_phases': ['review']}, {'id': 'b', 'learning_phases': ['review']}]
        self.assertFalse(refill_required(cards, {'seed', 'a', 'b'}, mastered=False))
        self.assertFalse(refill_required(cards, {'seed', 'a'}, mastered=True))
        self.assertTrue(refill_required(cards, {'seed', 'a', 'b'}, mastered=True))

    def test_review_requires_exact_count_and_consistent_verdicts(self):
        value = {'point_id': 'p', 'approved': True, 'checks': [
            {'index': i, 'approved': True, 'issues': [], 'rationale': 'The same fact is tested.'} for i in range(1, 4)]}
        trial.validate_review(value, 'p', 3)
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            trial.validate_review(value, 'p')

    def test_receipt_distinguishes_structured_semantic_from_mechanical_grading(self):
        cards = [{'interaction': {'type': 'text', 'grading': 'semantic'}},
                 {'interaction': {'type': 'fill_blank', 'grading': 'semantic'}},
                 {'interaction': {'type': 'fill_blank', 'grading': 'exact'}},
                 {'interaction': {'type': 'choice'}}, {'interaction': {'type': 'matching'}},
                 {'interaction': {'type': 'true_false'}}]
        summary = trial.interaction_summary(cards)
        self.assertEqual(summary['semantic_graded'], 2)
        self.assertEqual(summary['mechanically_graded'], 4)
        self.assertEqual(summary['by_type']['fill_blank'], 2)

    def test_a_blank_stem_cannot_silently_fall_back_to_a_text_box(self):
        value = candidate(2)
        value['variants'][0]['question'] = 'The label is {{blank:b1}}.'
        with self.assertRaisesRegex(ValueError, 'Blank markers require fill_blank'):
            trial.validate_pair(value, source_tasks(document(), 'english', 2)[0])

    def test_default_two_card_source_also_reaches_review_without_rebilling(self):
        source = source_tasks(document(), 'english', 2)[0]
        source.pop('variant_count')
        review = {'point_id': 'p', 'approved': True, 'checks': [
            {'index': i, 'approved': True, 'issues': [], 'rationale': 'The target is preserved.'} for i in (1, 2)]}
        with tempfile.TemporaryDirectory() as temporary, \
                patch('scripts.produce_atomic_questions.CompatibleClient'), \
                patch.object(trial, 'generation_payload', return_value={}), \
                patch.object(trial, 'call', return_value={}) as call, \
                patch('scripts.produce_atomic_questions._object', side_effect=[candidate(2), review]):
            result = run_point(source, Path(temporary), (), 'generate', 'review', {}, 4)
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(call.call_count, 2)

    def test_internal_program_error_stops_instead_of_repeating_provider_calls(self):
        source = source_tasks(document(), 'english', 2)[0]
        with tempfile.TemporaryDirectory() as temporary, \
                patch('scripts.produce_atomic_questions.CompatibleClient', side_effect=KeyError('internal')) as client:
            result = run_point(source, Path(temporary), (), 'generate', 'review', {}, 4)
            self.assertEqual(result['status'], 'internal_error')
            self.assertEqual(client.call_count, 1)


if __name__ == '__main__':
    unittest.main()
