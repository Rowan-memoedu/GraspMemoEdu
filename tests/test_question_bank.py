"""Synthetic regressions for new choice questions and immutable presentations."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import unittest
from unittest.mock import Mock, patch
import uuid

from graspmemoedu.question_bank import QuestionBank, digest, install_bank, validate_bank, validate_archive
from graspmemoedu.question_types import validate_interaction, public_interaction, parse_answer, demo_answer, display_answer
from graspmemoedu.judging import judge_answer
from graspmemoedu.review import ReviewBackend
from graspmemoedu.service import DomainError, LearningService
from graspmemoedu.question_conversion import validate_candidates, source_text, build_variant, accepted_blind, accepted_review, select_candidates, within_obvious_bounds, call, review_pool
from tests.fixtures import synthetic_course, temporary_test_directory, DeterministicJudge
from graspmemoedu.author_import import prepare_topic


def choice_spec():
    return {'version': 1, 'type': 'choice', 'correct_id': 'owl', 'options': [
        {'id': key, 'text': text} for key, text in zip(('ash', 'elm', 'owl', 'bay', 'fir', 'oak'),
                                                    ('$2$', '$8$', '$5$', '$7$', '$12$', '$15$'))]}


def bank_fixture(course):
    return {'schema': 1, 'topic_id': course['id'], 'course_version': course['version'], 'math_css': '',
            'provenance': {'synthetic': True}, 'questions': {
                q['id']: {'source_sha256': digest(q), 'question': q['question'] + ' Choose one.',
                          'html': q['html'] + '<p>Choose one.</p>', 'interaction': choice_spec()}
                for m in course['modules'] for q in [m['example'], *m['practices']]}}


class ChoiceContractTests(unittest.TestCase):
    def test_denominator_and_sign_variants_with_multiple_centers_are_allowed(self):
        for denominator in (23, 47):
            correct = f'1/{denominator}'
            values = [f'{sign}1/{n}' for sign in ('', '-')
                      for n in (denominator - 1, denominator, denominator + 1)]
            rows = [{'value': value, 'family': 'endpoint', 'error_expression': value,
                     'reason': 'Nearby denominators with two equally plausible sign centers.'}
                    for value in values if value != correct]
            selected, _ = select_candidates({'reference_answer': correct}, {'distractors': rows})
            self.assertEqual(selected['distractors'], rows)
            self.assertEqual(validate_candidates({'reference_answer': correct}, selected)['families'], ['endpoint'])
            accepted_blind({'cue_detected': False, 'suspect_value': '',
                            'reason': 'The positive and negative groups have equally plausible centers.'}, correct)
            with self.assertRaises(ValueError):
                accepted_blind({'cue_detected': True, 'suspect_value': correct,
                                'reason': 'A different set exposes this as its only conspicuous center.',
                                'suspect_value_note': 'Extra descriptive metadata does not override the verdict.'}, correct)

    def test_near_zero_candidates_are_not_rejected_only_for_a_large_ratio(self):
        question = {'reference_answer': '1/500'}
        rows = [{'value': value, 'family': family, 'error_expression': value,
                 'reason': 'A concrete hypothetical source-related operation for numeric screening.'}
                for value, family in [('1/250000', 'formula'), ('-1/500', 'sign'),
                                      ('1/499', 'endpoint'), ('1/249500', 'cancellation'), ('0', 'structure')]]
        self.assertEqual(len(validate_candidates(question, {'distractors': rows})['values']), 6)
        far = deepcopy(rows); far[0]['value'] = far[0]['error_expression'] = '2000'
        with self.assertRaises(ValueError):
            validate_candidates(question, {'distractors': far})

    def test_rejected_candidate_is_removed_without_editing_and_new_set_is_reviewed(self):
        source = {'id': 'independent-synthetic', 'question': 'A numeric exercise.', 'reference_answer': '10'}
        rows = [{'value': str(v), 'family': ('structure', 'endpoint', 'formula')[i % 3],
                 'reason': 'A sufficiently concrete synthetic error explanation.', 'error_expression': str(v)}
                for i, v in enumerate((8, 9, 11, 12, 14, 15, 17, 19, 21, 23, 25, 27))]
        original = deepcopy(rows)
        rejected = []
        def reviewer(_client, _folder, _stage, _instruction, data, **_kwargs):
            if not rejected:
                rejected.append(data['candidate']['distractors'][0]['value'])
                return {'approved': False, 'issues': ['One candidate has an invalid error mechanism.'],
                        'rejected_values': rejected, 'rationale': 'The first selected candidate does not follow its claimed operation.'}
            self.assertFalse(set(rejected) & {row['value'] for row in data['candidate']['distractors']})
            return {'approved': True, 'issues': [], 'rejected_values': [],
                    'rationale': 'Each of the replacement set candidates has now been independently reviewed.'}
        with temporary_test_directory() as directory, patch('graspmemoedu.question_conversion.call', side_effect=reviewer) as api:
            candidate, _, review = review_pool(Mock(), Path(directory), source, {'distractors': rows})
        self.assertEqual(api.call_count, 2)
        self.assertEqual(rows, original)
        self.assertTrue(all(row in original for row in candidate['distractors']))
        accepted_review(review)

    def test_response_metadata_does_not_discard_valid_reviews_or_bypass_rejections(self):
        review = {'approved': True, 'issues': [], 'rationale': 'A sufficiently detailed independent mathematical review.', 'type': 'json_object'}
        accepted_review(review)
        for bad in ({**review, 'issues': ['Wrong calculation']}, {**review, 'approved': 'true'}, {**review, 'unknown': True}):
            with self.assertRaises(ValueError):
                accepted_review(bad)

    def test_interrupted_stage_reuses_exact_raw_response_without_calling_api(self):
        with temporary_test_directory() as directory:
            root = Path(directory)
            first, second = root / 'binding-one' / 'attempt-1', root / 'binding-two' / 'attempt-1'
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            client = Mock(model='test-model')
            client.review_complete.return_value = {'approved': True}
            self.assertEqual(call(client, first, 'review', 'fixed instruction', {'source': 'same'}, thinking=True), {'approved': True})
            client.review_complete.side_effect = AssertionError('Identical request must use cached raw response')
            self.assertEqual(call(client, first, 'review', 'fixed instruction', {'source': 'same'}, thinking=True), {'approved': True})
            self.assertEqual(call(client, second, 'review', 'fixed instruction', {'source': 'same'}, thinking=True), {'approved': True})
            self.assertEqual(client.review_complete.call_count, 1)

    def test_visible_bounds_are_source_neutral_and_do_not_assume_the_answer(self):
        from fractions import Fraction
        for start, last in [(2, 9), (3, 27)]:
            question = {'question': r'Calculate $$\left(1-\frac1{' + str(start) + r'^2}\right)\left(1-\frac1{' + str(start + 1) + r'^2}\right)\cdots\left(1-\frac1{' + str(last) + r'^2}\right).$$'}
            within_obvious_bounds(question, Fraction(1, 2))
            for value in (Fraction(-1), Fraction(0), Fraction(1), Fraction(9, 2)):
                with self.assertRaises(ValueError):
                    within_obvious_bounds(question, value)
        with self.assertRaises(ValueError):
            within_obvious_bounds({'question': r'Compute $\sqrt{7^2+24^2}$.'}, Fraction(-25))
        within_obvious_bounds({'question': r'Compute $\sqrt{7^2+24^2}-{30}$.'}, Fraction(-5))

    def test_choice_authoring_uses_existing_safe_math_renderer(self):
        with temporary_test_directory() as directory:
            root = Path(directory)
            lines = ['- Choice fixture', '  - Introduction', '    - Synthetic course.', '  - Example 1']
            for label, prefix in [('Example', '    '), *((f'Practice {i}', '      ') for i in range(1, 16))]:
                if label != 'Example':
                    lines.append('    - ' + label)
                lines += [prefix + '- Compute $2+3$.', prefix + '- Interaction',
                          prefix + '  - ' + json.dumps(choice_spec()), prefix + '- Explanation', prefix + '  - $2+3=5$.']
            source = root / 'source.md'
            source.write_text('\n'.join(lines), encoding='utf-8')
            result = prepare_topic(source, 'choice-fixture', root / 'imported')
            self.assertEqual(result['status'], 'approved', result)
            topic = json.loads((root / 'imported/topic.json').read_text(encoding='utf-8'))
            question = topic['modules'][0]['example']
            self.assertIn('<svg', question['interaction']['options'][0]['html'])
            self.assertNotIn('correct_id', json.dumps(public_interaction(question)))

    def test_schema_submission_unique_key_and_no_answer_leak(self):
        question = {'interaction': choice_spec()}
        validate_interaction(question['interaction'])
        self.assertNotIn('correct_id', json.dumps(public_interaction(question)))
        judge = Mock(side_effect=AssertionError('No LLM for choice'))
        self.assertTrue(judge_answer(judge, question, demo_answer(question))[0])
        self.assertFalse(judge_answer(judge, question, '{"selected":"ash"}')[0])
        self.assertEqual(display_answer(question, demo_answer(question)), '$5$')
        for answer in ('{}', '[]', '{"selected":null}', '{"selected":["owl"]}',
                       '{"selected":"unknown"}', '{"selected":"owl","selected":"ash"}',
                       '{"selected":"owl","extra":true}'):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                parse_answer(question, answer)
        for mutate in (lambda s: s.update(correct_id='unknown'), lambda s: s.update(options=s['options'][:1]),
                       lambda s: s['options'].append(s['options'][0]), lambda s: s.update(correct_id=['owl'])):
            spec = choice_spec()
            mutate(spec)
            with self.assertRaises(ValueError):
                validate_interaction(spec)

    def test_independent_numeric_validator_catches_equivalence_and_invented_calculations(self):
        source = {'reference_answer': '5'}
        candidate = {'distractors': [
            {'value': value, 'error_expression': expression, 'family': family,
             'reason': 'Synthetic misconception using an explicitly different operation.'}
            for value, expression, family in [('2', '7-5', 'structure'), ('8', '4*2', 'coefficient'),
                ('12', '3*4', 'scope'), ('15', '3*5', 'formula'), ('3', '9/3', 'cancellation')]]}
        validate_candidates(source, candidate)
        for key, value in [('value', '5'), ('value', '4/2'), ('error_expression', '8*8')]:
            bad = deepcopy(candidate)
            bad['distractors'][0][key] = value
            with self.assertRaises(ValueError):
                validate_candidates(source, bad)
        with self.assertRaises(ValueError):
            accepted_blind({'cue_detected': True, 'suspect_value': '5', 'reason': 'The options form a visible pattern.'}, '5')
        accepted_blind({'cue_detected': True, 'suspect_value': '8', 'reason': 'A blind clue points to a distractor, not the key.'}, '5')

    def test_pool_rejects_bad_calculations_without_editing_candidates(self):
        pool = {'distractors': [{'value': str(n), 'error_expression': str(n), 'family': ('scope', 'formula', 'coefficient')[i % 3],
                                'reason': 'Synthetic source-bound misconception for set selection.'}
                               for i, n in enumerate((2, 3, 7, 8, 12, 13, 14, 15, 25, 35, 45, 55))]}
        pool['distractors'][0]['error_expression'] = '2+2'
        before = deepcopy(pool)
        candidate, selection = select_candidates({'reference_answer': '5'}, pool)
        self.assertEqual(pool, before)
        self.assertEqual(len(selection['rejected_candidates']), 1)
        self.assertEqual(len(candidate['distractors']), 5)
        self.assertNotIn('2', [row['value'] for row in candidate['distractors']])
        validate_candidates({'reference_answer': '5'}, candidate)

    def test_original_math_and_equivalent_answer_contract_survive_blank_conversion(self):
        q = synthetic_course(1)['modules'][0]['example']
        q['reference_answer'] = '5'
        variant = build_variant(q)
        self.assertTrue(variant['question'].startswith(q['question']))
        self.assertEqual(variant['interaction']['grading'], 'semantic')
        self.assertEqual(variant['interaction']['blanks'][0]['answers'], ['5'])
        self.assertEqual(source_text('<p>Use <span data-tex="a+b"><svg><g><text>wrong pixels</text></g></svg></span>.</p>'), 'Use $a+b$.')
        with self.assertRaises(ValueError):
            source_text('<p>See <img src="diagram.png"></p>')


class BankPresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.course = synthetic_course(2)
        self.bank = bank_fixture(self.course)

    def payload(self, learning, **extra):
        state = learning.state()
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def test_install_does_not_rewrite_progress_and_pins_new_steps(self):
        learning = LearningService(self.course, self.root, DeterministicJudge())
        before = learning.state()
        with closing(learning._connect()) as con:
            body_before = con.execute('SELECT body FROM topics').fetchone()[0]
        revision = install_bank(self.bank, self.course, self.root)
        with closing(learning._connect()) as con:
            self.assertEqual(body_before, con.execute('SELECT body FROM topics').fetchone()[0])
        self.assertEqual(before, learning.state())
        learning.choose(self.payload(learning, choice='mastered'))
        result = learning.submit(self.payload(learning, answer='correct'))
        learning.process_submission(result['submission_id'])
        learning.advance(self.payload(learning))
        state = learning.state()
        new = state['steps'][-1]
        self.assertEqual(new['question_bank_revision'], revision)
        self.assertEqual(new['interaction']['type'], 'choice')
        self.assertNotIn('interaction', state['steps'][0])
        self.assertEqual(new['interaction'], LearningService(self.course, self.root).state()['steps'][-1]['interaction'])
        learning.choose(self.payload(learning, choice='mastered'))
        ready = learning.state()
        with self.assertRaises(DomainError):
            learning.submit(self.payload(learning, answer='correct'))
        self.assertEqual(ready, learning.state())
        payload = self.payload(learning, answer='{"selected":"owl"}')
        result = learning.submit(payload)
        learning.process_submission(result['submission_id'])
        self.assertTrue(learning.submission(result['submission_id'])['correct'])
        self.assertEqual(result['submission_id'], learning.submit(payload)['submission_id'])

    def test_old_and_new_review_rounds_and_immutable_revisions(self):
        learning = LearningService(self.course, self.root, DeterministicJudge())
        now = datetime.now(timezone.utc)
        backend = ReviewBackend(learning, clock=lambda: now, rng=random.Random(2))
        backend.record_module_result(module_id='module-1', mastery=4, result_id='fixture', completed_at=now)
        now += timedelta(days=365)
        old = backend.begin('module-1', request_id=str(uuid.uuid4()))
        revision = install_bank(self.bank, self.course, self.root)
        self.assertEqual(old, backend.state(old['session_id']))
        backend.record_module_result(module_id='module-2', mastery=4, result_id='fixture2', completed_at=now)
        for _ in range(2):
            state = backend.state(old['session_id'])
            result = backend.submit(state['session_id'], {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'question_id': state['practice']['id'], 'answer': 'correct'})
            backend.process_submission(result['submission_id'])
            state = backend.submission(result['submission_id'])['state']
            if state['status'] == 'in_progress':
                backend.advance(state['session_id'], {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                                                              'question_id': state['practice']['id']})
            else:
                break
        now += timedelta(days=365)
        new = backend.begin('module-2', request_id=str(uuid.uuid4()))
        self.assertEqual(new['question_bank_revision'], revision)
        self.assertEqual(new['practice']['interaction']['type'], 'choice')
        changed = deepcopy(self.bank)
        changed['provenance']['iteration'] = 2
        for variant in changed['questions'].values():
            variant['interaction']['correct_id'] += 'new'
            for option in variant['interaction']['options']:
                option['id'] += 'new'
        install_bank(changed, self.course, self.root)
        self.assertEqual(new, backend.state(new['session_id']))
        self.assertEqual(QuestionBank(self.course, self.root).load(revision), self.bank)
        result = backend.submit(new['session_id'], {'request_id': str(uuid.uuid4()), 'expected_revision': new['revision'],
            'question_id': new['practice']['id'], 'answer': '{"selected":"ash"}'})
        backend.process_submission(result['submission_id'])
        state = backend.submission(result['submission_id'])['state']
        self.assertEqual(state['practice_error_count'], 1)
        self.assertEqual(state['practice_answered_count'], 1)

    def test_pending_choice_and_history_remain_bound_when_active_bank_changes(self):
        first = install_bank(self.bank, self.course, self.root)
        learning = LearningService(self.course, self.root, Mock(side_effect=AssertionError('No LLM')))
        learning.choose(self.payload(learning, choice='mastered'))
        submitted = learning.submit(self.payload(learning, answer='{"selected":"owl"}'))
        changed = deepcopy(self.bank)
        changed['questions']['module-1-example']['interaction']['correct_id'] = 'ash'
        install_bank(changed, self.course, self.root)
        learning.process_submission(submitted['submission_id'])
        self.assertTrue(learning.submission(submitted['submission_id'])['correct'])
        self.assertEqual(learning.state()['steps'][0]['question_bank_revision'], first)
        path = QuestionBank(self.course, self.root).root / (first + '.json')
        altered = json.loads(path.read_text(encoding='utf-8'))
        altered['provenance']['tampered'] = True
        path.write_text(json.dumps(altered), encoding='utf-8')
        with self.assertRaises(ValueError):
            LearningService(self.course, self.root).state()

    def test_different_presentations_shuffle_without_changing_key_or_source(self):
        revision = install_bank(self.bank, self.course, self.root)
        bank = QuestionBank(self.course, self.root)
        qid = self.course['modules'][0]['example']['id']
        orders = {tuple(o['id'] for o in bank.question(qid, revision, str(i))['interaction']['options']) for i in range(20)}
        self.assertGreater(len(orders), 10)
        self.assertEqual(bank.question(qid, revision, 'same'), bank.question(qid, revision, 'same'))
        self.assertEqual(bank.question(qid), self.course['modules'][0]['example'])
        bad = deepcopy(self.bank)
        bad['questions'][qid]['source_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            validate_bank(bad, self.course)
        blobs = {f'question-banks/{self.course["id"]}/{revision}.json': json.dumps(self.bank).encode(),
                 f'question-banks/{self.course["id"]}/active.json': json.dumps({'revision': revision}).encode()}
        self.assertEqual(set(blobs), validate_archive(blobs, {self.course['id']: self.course}))
        del blobs[f'question-banks/{self.course["id"]}/{revision}.json']
        with self.assertRaises(ValueError):
            validate_archive(blobs, {self.course['id']: self.course})


if __name__ == '__main__':
    unittest.main()
