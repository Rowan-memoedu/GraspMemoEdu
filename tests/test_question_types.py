import copy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import unittest
from unittest.mock import Mock, patch
import uuid

from math_learning.author_import import prepare_topic
from math_learning.judging import judge_answer
from math_learning.question_types import (validate_interaction, public_interaction, parse_answer,
    deterministic_verdict, semantic_arguments, display_answer, demo_answer)
from math_learning.review import ReviewBackend
from math_learning.service import LearningService, DomainError
from tests.fixtures import temporary_test_directory
from tests.question_fixtures import interactions, interaction_course, author_source


class QuestionTypeTests(unittest.TestCase):
    def question(self, index):
        return interaction_course(index)['modules'][0]['example']

    def test_all_supported_specs_validate_and_roundtrip(self):
        for index in range(7):
            with self.subTest(index=index):
                question = self.question(index)
                validate_interaction(question['interaction'], question['question'])
                parse_answer(question, demo_answer(question))
                if index < 5:
                    self.assertTrue(deterministic_verdict(question, demo_answer(question))[0])

    def test_no_private_keys_in_public_metadata(self):
        for index in range(7):
            raw = json.dumps(public_interaction(self.question(index)))
            for secret in ('"correct"', '"answers"', '"pairs"', '"extract_pattern"'):
                self.assertNotIn(secret, raw)

    def test_exact_ignores_whitespace_width_but_preserves_math_and_case(self):
        question = self.question(0)
        judge = Mock(side_effect=AssertionError('Deterministic answer must not call LLM'))
        self.assertTrue(judge_answer(judge, question, ' ４ ２\n')[0])
        for answer in ('-42', '4.2', '4²', '42 or 41', 'forty two'):
            self.assertFalse(judge_answer(judge, question, answer)[0])
        question['interaction']['answers'] = ['Na']
        self.assertFalse(judge_answer(judge, question, 'na')[0])
        judge.assert_not_called()

    def test_regex_extracts_only_authored_complete_format(self):
        question = self.question(0)
        question['interaction']['extract_pattern'] = r'(?:answer=)?\s*([0-9 ]+)\s*'
        validate_interaction(question['interaction'])
        self.assertTrue(deterministic_verdict(question, 'answer= 4 2')[0])
        self.assertFalse(deterministic_verdict(question, 'wrong answer=42')[0])
        question['interaction']['extract_pattern'] = '('
        with self.assertRaises(ValueError):
            validate_interaction(question['interaction'])

    def test_regex_timeout_is_retryable_not_wrong(self):
        question = self.question(0)
        with patch('math_learning.question_types.exact', side_effect=TimeoutError):
            self.assertIsNone(judge_answer(None, question, '42')[0])

    def test_one_false_item_makes_whole_question_wrong(self):
        for index, answer in [(1, '{"a":true,"b":true}'), (2, '{"a":"东","b":"东"}')]:
            correct, reason = deterministic_verdict(self.question(index), answer)
            self.assertFalse(correct)
            self.assertEqual(reason, '1 ✓ · 2 ✗')

    def test_invalid_structured_answers_are_not_counted_as_wrong(self):
        for answer in ('{}', '{"a":true,"b":null}', '{"a":1,"b":false}', '{"a":true,"a":false,"b":false}', 'true'):
            with self.assertRaises(ValueError):
                parse_answer(self.question(1), answer)
        with self.assertRaises(ValueError):
            parse_answer(self.question(2), '{"a":"东","b":" "}')

    def test_matching_relation_is_independent_of_row_order(self):
        question = self.question(3)
        pairs = question['interaction']['pairs']
        self.assertTrue(deterministic_verdict(question, json.dumps(pairs[::-1]))[0])
        wrong = [{'left': 'l2', 'right': 'r9'}, {'left': 'lx', 'right': 'r4'}]
        self.assertFalse(deterministic_verdict(question, json.dumps(wrong))[0])
        self.assertIn('↔', display_answer(question, json.dumps(pairs)))

    def test_fixed_sides_unknown_ids_duplicates_and_counts(self):
        for side in ('left', 'right'):
            question = self.question(3)
            question['interaction']['fixed_side'] = side
            pairs = question['interaction']['pairs']
            self.assertEqual(public_interaction(question)['fixed'], [p[side] for p in pairs])
            with self.assertRaises(ValueError):
                parse_answer(question, json.dumps(pairs[::-1]))
        question = self.question(3)
        for pairs in ([], [question['interaction']['pairs'][0]] * 2,
                      [{'left': 'outside', 'right': 'r9'}, {'left': 'l2', 'right': 'r4'}]):
            with self.assertRaises(ValueError):
                parse_answer(question, json.dumps(pairs))

    def test_semantic_blanks_pass_context_and_all_item_rule_to_llm(self):
        question = self.question(6)
        judge = Mock(return_value={'status': 'judged', 'correct': True, 'reason': 'Both describe the phase change.'})
        self.assertTrue(judge_answer(judge, question, '{"a":"liquid","b":"solid"}')[0])
        stem, reference, answer = judge.call_args.args
        self.assertIn('所有空都语义正确', stem)
        self.assertEqual(json.loads(reference), {'a': ['液态'], 'b': ['固态']})
        self.assertEqual(json.loads(answer)['a'], 'liquid')
        self.assertIsNone(judge_answer(None, question, '{"a":"液态","b":"固态"}')[0])

    def test_legacy_question_contract_is_unchanged(self):
        question = self.question(0)
        del question['interaction']
        self.assertIsNone(deterministic_verdict(question, 'free text'))
        self.assertEqual(semantic_arguments(question, 'free text'),
                         (question['question'] + '\n作答约束：' + question['constraints'], question['reference_answer'], 'free text'))

    def test_invalid_schema_and_blank_markers(self):
        stem, original = interactions()[2]
        for bad_stem in ('missing markers', stem + ' {{blank:a}}', stem + ' {{blank:bad!}}', '$' + stem + '$'):
            with self.assertRaises(ValueError):
                validate_interaction(original, bad_stem)
        for spec in ({'version': True, 'type': 'text'}, {'version': 1, 'type': []}, {'version': 1, 'type': 'unknown'}):
            with self.assertRaises(ValueError):
                validate_interaction(spec)
        matching = copy.deepcopy(interactions()[3][1])
        matching['pairs'][1]['right'] = 'r4'
        with self.assertRaises(ValueError):
            validate_interaction(matching)


class QuestionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.course = interaction_course(1)
        self.judge = Mock(side_effect=AssertionError('LLM should not be used'))
        self.learning = LearningService(self.course, self.root, self.judge)

    def payload(self, **extra):
        state = self.learning.state()
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def test_lesson_structural_rejection_then_all_items_one_error_and_idempotency(self):
        self.learning.choose(self.payload(choice='mastered'))
        before = self.learning.state()
        with self.assertRaises(DomainError) as caught:
            self.learning.submit(self.payload(answer='{}'))
        self.assertEqual(caught.exception.code, 'invalid_answer')
        self.assertEqual(before, self.learning.state())
        payload = self.payload(answer='{"a":false,"b":true}')
        result = self.learning.submit(payload)
        self.learning.process_submission(result['submission_id'])
        state = self.learning.submission(result['submission_id'])['state']
        self.assertFalse(state['steps'][0]['feedback']['correct'])
        self.assertEqual(self.learning.submit(payload)['submission_id'], result['submission_id'])
        self.judge.assert_not_called()
        with closing(self.learning._connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM submissions').fetchone()[0], 1)
        restored = LearningService(self.course, self.root, self.judge).state()
        self.assertEqual(state['steps'][0]['answer_display'], restored['steps'][0]['answer_display'])

    def test_review_partial_counts_once_and_invalid_does_not_advance(self):
        time = datetime.now(timezone.utc)
        backend = ReviewBackend(self.learning, clock=lambda: time, rng=random.Random(4))
        backend.record_module_result(module_id='module-1', mastery=4, result_id='synthetic-completion', completed_at=time)
        time += timedelta(days=365)
        state = backend.begin('module-1', request_id=str(uuid.uuid4()))
        base = {'expected_revision': state['revision'], 'question_id': state['practice']['id']}
        with self.assertRaises(DomainError):
            backend.submit(state['session_id'], {**base, 'request_id': str(uuid.uuid4()), 'answer': '{}'})
        payload = {**base, 'request_id': str(uuid.uuid4()), 'answer': '{"a":false,"b":true}'}
        result = backend.submit(state['session_id'], payload)
        backend.process_submission(result['submission_id'])
        state = backend.submission(result['submission_id'])['state']
        self.assertEqual(state['practice_error_count'], 1)
        self.assertEqual(state['practice_answered_count'], 1)
        self.assertEqual(state['practice_target_count'], 3)
        self.assertEqual(backend.submit(state['session_id'], payload)['submission_id'], result['submission_id'])
        self.judge.assert_not_called()

    def test_author_import_roundtrip_all_types_safe_rendering_and_no_answer_leak(self):
        source = self.root / 'source.md'
        source.write_text(author_source(), encoding='utf-8')
        with patch('math_learning.author_import.make_client', side_effect=AssertionError('Mechanical import only')):
            result = prepare_topic(source, 'interaction-fixture', self.root / 'imported')
        self.assertEqual(result['status'], 'approved', result)
        topic = json.loads((self.root / 'imported/topic.json').read_text(encoding='utf-8'))
        service = LearningService(topic, self.root / 'imported-data', None)
        self.assertEqual(len(topic['modules']), 7)
        for module in topic['modules']:
            for question in [module['example'], *module['practices']]:
                self.assertNotIn('"answers"', question['html'])
                self.assertNotIn('Interaction', question['html'])
                validate_interaction(question['interaction'], question['question'])
        self.assertIn('<svg', topic['modules'][3]['example']['interaction']['left'][0]['html'])
        self.assertNotIn('"answers"', json.dumps(service.state()))


if __name__ == '__main__':
    unittest.main()
