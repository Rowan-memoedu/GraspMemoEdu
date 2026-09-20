"""Lesson integration boundaries for persistent grouped Practice queues."""

from contextlib import closing
from pathlib import Path
import random
import unittest
import uuid

from math_learning.service import LearningService
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class GroupedLessonTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.course = synthetic_course()
        self.restart()
        self.service.practice_queue.rng = random.Random(618)

    def restart(self, learner='local'):
        self.service = LearningService(self.course, self.path, DeterministicJudge(), learner_id=learner)

    def payload(self, **extra):
        state = self.service.state()
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def active(self, state=None):
        state = state or self.service.state()
        return next(step for step in state['steps'] if step['id'] == state['active_step_id'])

    def queue(self):
        with closing(self.service._connect()) as con:
            return self.service.practice_queue.snapshot(con, self.course['modules'][0])

    def advance(self):
        return self.service.advance(self.payload())

    def study(self):
        state = self.service.choose(self.payload(choice='unmastered'))
        while self.active(state)['kind'] != 'practice':
            state = self.advance()
        return state

    def answer(self, correct):
        item = self.service.submit(self.payload(answer='correct' if correct else 'wrong'))
        self.service.process_submission(item['submission_id'])
        return self.service.submission(item['submission_id'])['state']

    def test_legacy_attempt_keeps_sequential_questions_and_never_creates_queue(self):
        with self.service._transaction() as con:
            state = self.service._load(con)
            self.service._attempt(state).pop('practice_queue_version')
            self.service._save(con, state)
        self.restart()
        state = self.study()
        self.assertEqual(self.active(state)['question_id'], 'module-1-practice-1')
        self.answer(False)
        self.restart()
        state = self.advance()
        self.assertEqual(self.active(state)['question_id'], 'module-1-practice-2')
        self.assertIsNone(self.queue())

    def test_refresh_and_replayed_advance_do_not_consume_extra_questions(self):
        self.study()
        before = self.queue()
        self.assertEqual(before['sequence'], 1)
        self.service.state()
        self.restart()
        self.assertEqual(self.queue(), before)
        self.answer(True)
        payload = self.payload()
        state = self.service.advance(payload)
        after = self.queue()
        self.assertEqual(after['sequence'], 2)
        self.assertEqual(self.service.advance(payload), state)
        self.service.state()
        self.assertEqual(self.queue(), after)

    def test_group_expansion_preserves_lesson_targets_scores_and_fourth_error_pause(self):
        cases = [([True, True], 4), ([False, True, True], 3),
                 ([False, False, True, True, True], 2),
                 ([False, False, False, True, True, True, True], 1),
                 ([False, False, False, False], None)]
        for case_index, (answers, mastery) in enumerate(cases):
            with self.subTest(mastery=mastery):
                self.restart(learner=f'case-{case_index}')
                self.study()
                for index, correct in enumerate(answers):
                    if index:
                        self.advance()
                    state = self.answer(correct)
                    errors = answers[:index + 1].count(False)
                    module = state['modules'][0]
                    self.assertEqual(module['practice_answered_count'], index + 1)
                    self.assertEqual(module['practice_target_count'], (2, 3, 5, 7)[min(errors, 3)])
                    self.assertEqual(len(self.queue()['expanded_groups']), errors)
                self.assertEqual(module['mastery'], mastery)
                self.assertEqual(module['status'], 'paused' if mastery is None else 'completed')
                self.assertEqual(self.queue()['sequence'], len(answers))

    def test_reset_during_judgement_cannot_recreate_deleted_queue(self):
        self.study()
        item = self.service.submit(self.payload(answer='wrong'))
        self.assertIsNotNone(self.queue())

        def reset_while_judging(*_args):
            self.service.reset_progress(self.payload())
            return {'status': 'judged', 'correct': False, 'reason': 'late result'}

        self.service.judge = reset_while_judging
        self.service.process_submission(item['submission_id'])
        self.assertIsNone(self.queue())
        self.assertEqual(self.service.submission(item['submission_id'])['status'], 'discarded')
        self.assertEqual(self.service.state()['modules'][0]['practice_error_count'], 0)

    def test_unlock_new_attempt_continues_remaining_queue_and_group_flags(self):
        self.study()
        for index in range(4):
            if index:
                self.advance()
            state = self.answer(False)
        before = self.queue()
        old_attempt = state['attempt_id']
        state = self.service.unlock(self.course['id'], 'module-1', state['pause']['event_id'])
        self.assertNotEqual(state['attempt_id'], old_attempt)
        self.restart()
        self.assertEqual(self.queue(), before)
        state = self.study()
        self.assertEqual(self.active(state)['question_id'], before['pending'][0])
        self.assertEqual(self.queue()['expanded_groups'], before['expanded_groups'])
        self.assertEqual(self.queue()['sequence'], before['sequence'] + 1)


if __name__ == '__main__':
    unittest.main()
