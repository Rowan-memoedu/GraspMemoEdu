"""Persistent grouped Practice source tests, isolated from website/formal data."""

import copy
import random
import sqlite3
from types import SimpleNamespace
import unittest

from graspmemoedu.practice_queue import PersistentPracticeQueue, VERSION
from graspmemoedu.service import DomainError
from tests.fixtures import synthetic_course


class PersistentPracticeQueueTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:', isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.course = synthetic_course(2)
        self.module = self.course['modules'][0]
        self.learning = SimpleNamespace(key=('learner-a', self.course['id']), course=self.course)
        self.queue = PersistentPracticeQueue(self.learning, random.Random(42))
        self.queue.initialize_schema(self.con)

    def snapshot(self):
        return self.queue.snapshot(self.con, self.module)

    def draw(self, exclude=()):
        return self.queue.draw(self.con, self.module, exclude)

    def group(self, question):
        return next(index // 3 + 1 for index, item in enumerate(self.module['practices'])
                    if item['id'] == question)

    def test_lazy_five_seeds_each_group_in_source_order(self):
        self.assertIsNone(self.snapshot())
        first = self.draw()
        state = self.snapshot()
        seeds = [first, *state['pending']]
        self.assertEqual([self.group(item) for item in seeds], [1, 2, 3, 4, 5])
        self.assertEqual(state['presented'], [first])
        self.assertEqual(state['last_presented'], {first: 1})
        self.assertEqual(state['sequence'], 1)
        self.assertEqual(state['queue_version'], VERSION)

    def test_first_error_appends_remaining_two_once_forever(self):
        first = self.draw()
        initial = self.snapshot()['pending']
        siblings = [item['id'] for item in self.module['practices'][:3] if item['id'] != first]
        self.queue.record_result(self.con, self.module, first, False)
        expected = self.snapshot()
        self.assertEqual(expected['pending'], initial + siblings)
        self.assertEqual(expected['expanded_groups'], [1])
        self.queue.record_result(self.con, self.module, first, False)
        self.assertEqual(self.snapshot(), expected)
        # Consume into the same group's siblings in a later round/restarted owner.
        for unused in range(5):
            current = self.draw()
        self.assertIn(current, siblings)
        self.queue = PersistentPracticeQueue(self.learning, random.Random(19))
        before = self.snapshot()
        self.queue.record_result(self.con, self.module, current, False)
        self.assertEqual(self.snapshot(), before)

    def test_rounds_resume_remaining_queue_and_refresh_never_advances(self):
        first = self.draw()
        second = self.draw([first])
        after_lesson = self.snapshot()
        self.assertEqual(len(after_lesson['pending']), 3)
        self.queue = PersistentPracticeQueue(self.learning, random.Random(4))
        self.assertEqual(self.snapshot(), after_lesson)
        self.assertEqual(self.snapshot(), after_lesson)
        review_first = self.draw()
        self.assertEqual(review_first, after_lesson['pending'][0])
        self.assertNotIn(review_first, (first, second))
        self.assertEqual(self.snapshot()['sequence'], 3)

    def test_unseen_fill_then_least_recently_presented(self):
        initial = [self.draw() for unused in range(15)]
        self.assertEqual(len(set(initial)), 15)
        self.assertEqual(self.snapshot()['pending'], [])
        self.assertEqual(self.draw(), initial[0])
        self.assertEqual(self.draw(), initial[1])
        self.assertEqual(self.snapshot()['last_presented'][initial[0]], 16)
        # Excluding this round's oldest candidates skips them without changing their ages.
        self.assertEqual(self.draw(initial[2:5]), initial[5])
        self.assertEqual(self.draw(), initial[2])

    def test_excluded_pending_is_preserved_and_error_does_not_duplicate_it(self):
        self.draw()
        pending = self.snapshot()['pending'][:]
        self.draw(pending)
        self.assertEqual(self.snapshot()['pending'], pending)
        # Find an unseen sibling of a pending seed, then judge its first group error.
        candidate = None
        excluded = list(pending)
        for unused in range(10):
            question = self.draw(excluded)
            excluded.append(question)
            if self.group(question) in [self.group(item) for item in pending]:
                candidate = question
                break
        self.assertIsNotNone(candidate)
        self.queue.record_result(self.con, self.module, candidate, False)
        state = self.snapshot()
        self.assertEqual(len(state['pending']), len(set(state['pending'])))
        self.assertEqual(state['pending'][:len(pending)], pending)
        self.assertNotIn(candidate, state['pending'])
        self.assertEqual(self.draw(), pending[0])

    def test_same_round_never_repeats_and_exhaustion_does_not_mutate(self):
        seen = []
        for unused in range(15):
            question = self.draw(seen)
            self.assertNotIn(question, seen)
            seen.append(question)
        before = self.snapshot()
        with self.assertRaises(DomainError) as error:
            self.draw(seen)
        self.assertEqual(error.exception.code, 'practice_queue_exhausted')
        self.assertEqual(self.snapshot(), before)

    def test_correct_and_judge_failure_do_not_expand(self):
        question = self.draw()
        before = self.snapshot()
        self.queue.record_result(self.con, self.module, question, True)
        self.assertEqual(self.snapshot(), before)
        for invalid in (None, 0, 1, 'false'):
            with self.assertRaises(ValueError):
                self.queue.record_result(self.con, self.module, question, invalid)
        self.assertEqual(self.snapshot(), before)

    def test_question_must_belong_to_module_and_have_been_presented(self):
        self.draw()
        unseen = self.snapshot()['pending'][0]
        for question, code in ((unseen, 'practice_not_presented'), ('other', 'invalid_practice_question')):
            with self.assertRaises(DomainError) as error:
                self.queue.record_result(self.con, self.module, question, False)
            self.assertEqual(error.exception.code, code)

    def test_metadata_defines_group_members_even_when_input_reordered(self):
        module = copy.deepcopy(self.module)
        for index, question in enumerate(module['practices']):
            question['source_practice'] = {'group': index // 3 + 1, 'member': index % 3 + 1}
        module['practices'].reverse()
        first = self.queue.draw(self.con, module)
        seeds = [first, *self.queue.snapshot(self.con, module)['pending']]
        self.assertEqual([self.group(item) for item in seeds], [1, 2, 3, 4, 5])

    def test_partial_duplicate_or_invalid_metadata_rejected_legacy_small_supported_false(self):
        legacy = copy.deepcopy(self.module)
        legacy['practices'] = legacy['practices'][:7]
        self.assertFalse(self.queue.supported(legacy))
        self.assertTrue(self.queue.supported(self.module))
        malformed = []
        partial = copy.deepcopy(self.module)
        partial['practices'][0]['source_practice'] = {'group': 1, 'member': 1}
        malformed.append(partial)
        for value in (None, {'group': True, 'member': 1}, {'group': 1, 'member': 0},
                      {'group': 1, 'member': 1}, {'group': 6, 'member': 1}):
            module = copy.deepcopy(self.module)
            for item in module['practices']:
                item['source_practice'] = value
            malformed.append(module)
        for module in malformed:
            with self.subTest(module=module['practices'][0].get('source_practice')):
                with self.assertRaises(DomainError) as error:
                    self.queue.supported(module)
                self.assertEqual(error.exception.code, 'invalid_course')

    def test_identity_topic_module_and_course_version_boundaries(self):
        self.draw()
        before = self.snapshot()
        other = SimpleNamespace(key=('learner-b', self.course['id']), course=self.course)
        queue = PersistentPracticeQueue(other)
        self.assertIsNone(queue.snapshot(self.con, self.module))
        queue.draw(self.con, self.module)
        other_topic = SimpleNamespace(key=('learner-a', 'other-topic'), course=self.course)
        self.assertIsNone(PersistentPracticeQueue(other_topic).snapshot(self.con, self.module))
        self.assertIsNone(self.queue.snapshot(self.con, self.course['modules'][1]))
        self.assertEqual(self.snapshot(), before)
        changed = SimpleNamespace(key=self.learning.key, course={**self.course, 'version': 'changed'})
        with self.assertRaises(DomainError) as error:
            PersistentPracticeQueue(changed).draw(self.con, self.module)
        self.assertEqual(error.exception.code, 'course_version_mismatch')
        self.assertEqual(self.snapshot(), before)

    def test_callers_transaction_rolls_back_draw_and_group_expansion(self):
        self.con.execute('BEGIN IMMEDIATE')
        self.queue.initialize_schema(self.con)
        question = self.draw()
        self.queue.record_result(self.con, self.module, question, False)
        self.con.rollback()
        self.assertIsNone(self.snapshot())
        question = self.draw()
        before = self.snapshot()
        self.con.execute('BEGIN IMMEDIATE')
        self.queue.record_result(self.con, self.module, question, False)
        self.draw()
        self.con.rollback()
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
