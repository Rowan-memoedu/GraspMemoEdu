"""Composed Topic transitions use synthetic content and an injected clock."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import unittest

from graspmemoedu.service import create_learning_service, DomainError
from graspmemoedu.atomic_content import validate_atomic
from graspmemoedu.question_types import demo_answer
from tests.fixtures import temporary_test_directory, DeterministicJudge
from tests.test_ordered_learning import ordered_topic, payload, active
from tests.atomic_fixtures import atomic_topic


def composed_topic():
    topic = ordered_topic()
    source = atomic_topic()
    topic.update(subject_id='math', memory_chunks=deepcopy(source['memory_chunks']),
                 flashcards=deepcopy(source['flashcards']))
    for chunk, intro in zip(topic['memory_chunks'], topic['introductions']):
        chunk['introduction_id'] = intro['id']
        intro['html'] = source['introduction']['html']
    return topic


class UnifiedLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.when = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.topic = composed_topic()
        self.service = self.make()

    def make(self):
        return create_learning_service(self.topic, self.temp.name, DeterministicJudge(), clock=lambda: self.when)

    def raw(self):
        with closing(self.service._connect()) as con:
            return self.service._load(con)

    def answer(self, answer=None):
        state = self.service.state()
        if answer is None:
            answer = demo_answer(self.service.atomic.questions[state['current']['card_id']]) if state.get('training_mode') == 'atomic_retrieval' else 'correct'
        data = payload(self.service, answer=answer)
        result = self.service.submit(data)
        self.service.process_submission(result['submission_id'])
        return data, self.service.submission(result['submission_id'])

    def advance(self):
        return self.service.advance(payload(self.service))

    def first_section(self):
        self.advance()
        for _ in range(20):
            state = self.service.state()
            if state.get('training_mode') != 'atomic_retrieval':
                return state
            if state['phase'] == 'answer':
                self.answer()
            elif state['phase'] == 'waiting':
                self.when = datetime.fromisoformat(state['next_available_at'])
                self.advance()
            else:
                if state['phase'] == 'feedback':
                    self.when += timedelta(seconds=180)
                self.advance()
        self.fail('First section did not finish')

    def test_intro_atomic_example_sequence_and_section_local_completion(self):
        self.assertEqual(active(self.service.state())['introduction_id'], 'intro-a')
        state = self.first_section()
        self.assertEqual(active(state)['question_id'], 'module-1-example')
        self.assertEqual(state['atomic_progress'], {'completed': 2, 'total': 3})
        self.assertFalse(self.raw()['atomic']['points']['c']['mastered'])
        self.assertIn('intro-a', [s.get('introduction_id') for s in state['steps'] if s['unlocked']])

    def test_due_cards_wait_for_continue_preserve_cursor_and_relock_guidance(self):
        self.first_section()
        self.service.choose(payload(self.service, choice='unmastered'))
        self.advance()
        before = self.service.state()
        self.assertEqual(active(before)['phase'], 'reading')
        self.when += timedelta(hours=7)
        self.assertEqual(self.service.state(), before)  # a GET never inserts cards
        state = self.advance()
        self.assertEqual(state['training_mode'], 'atomic_retrieval')
        self.assertIsNone(state['introduction'])
        self.assertNotIn('INTRODUCTION', str(state.get('unified_history')))
        intro_step = next(s for s in self.raw()['steps'] if s.get('introduction_id') == 'intro-a')
        with self.assertRaises(DomainError):
            self.service.select({**payload(self.service), 'step_id': intro_step['id']})
        self.answer('{"a":"wrong","b":"wrong"}')
        self.when += timedelta(seconds=60)
        state = self.advance()
        # The remaining point has a three-minute gap: show the next Practice.
        self.assertEqual(active(state)['question_id'], 'module-1-practice-1')
        self.assertEqual(active(state)['phase'], 'answer')
        self.assertEqual(len([s for s in self.raw()['steps'] if s['kind'] == 'practice']), 1)

    def test_restart_and_duplicate_continue_do_not_draw_extra_practice(self):
        self.first_section()
        self.service.choose(payload(self.service, choice='unmastered'))
        self.advance()
        self.when += timedelta(hours=7)
        data = payload(self.service)
        first = self.service.advance(data)
        self.assertEqual(self.service.advance(data), first)
        self.service = self.make()
        self.assertEqual(self.service.state(), first)
        self.answer()
        state = self.advance()
        self.assertEqual(active(state)['question_id'], 'module-1-practice-1')

    def test_competing_tabs_insert_one_card_and_do_not_consume_practices(self):
        self.first_section()
        self.service.choose(payload(self.service, choice='unmastered'))
        self.advance()
        self.when += timedelta(hours=7)
        first, second = payload(self.service), payload(self.service)

        def advance(data):
            try:
                return self.service.advance(data)
            except DomainError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(advance, (first, second)))
        self.assertEqual(sum(isinstance(result, DomainError) for result in results), 1)
        self.assertEqual(self.service.state()['training_mode'], 'atomic_retrieval')
        self.assertEqual(len([s for s in self.raw()['steps'] if s['kind'] == 'practice']), 0)
        self.answer()
        self.assertEqual(active(self.advance())['question_id'], 'module-1-practice-1')

    def test_previous_section_cards_exit_inline_queue_and_guidance_stays_open(self):
        self.first_section()
        self.service.choose(payload(self.service, choice='mastered'))
        self.answer()
        state = self.advance()
        self.assertEqual(active(state)['introduction_id'], 'intro-b')
        self.when += timedelta(hours=7)
        self.advance()
        state = self.advance()
        self.assertEqual(state['current']['point_ids'], ['c'])
        self.assertEqual([s['id'] for s in state['unified_history']],
                         [s['id'] for s in self.raw()['steps'] if s.get('introduction_id') == 'intro-a'])

    def test_atomic_state_survives_paradigm_pause(self):
        self.first_section()
        self.service.choose(payload(self.service, choice='unmastered'))
        self.advance()
        self.advance()
        for _ in range(4):
            self.answer('incorrect')
            if self.service.state()['status'] != 'paused':
                self.advance()
        raw = self.raw()
        self.assertEqual(raw['status'], 'paused')
        self.assertIsNone(raw['atomic']['pause'])
        self.assertTrue(raw['atomic']['points']['a']['mastered'])

    def test_contract_requires_unambiguous_section_ownership(self):
        validate_atomic(self.topic)
        broken = deepcopy(self.topic)
        broken['memory_chunks'][0].pop('introduction_id')
        with self.assertRaises(ValueError):
            validate_atomic(broken)

    def test_catch_up_module_keeps_finished_sections_and_point_history(self):
        self.first_section()
        state = self.service.skip_module(payload(self.service))
        skipped = state['skipped_modules'][0]
        self.assertEqual(active(state)['introduction_id'], 'intro-b')
        self.advance()
        self.advance()
        self.answer()
        self.advance()
        self.service.choose(payload(self.service, choice='mastered'))
        self.answer()
        self.assertEqual(self.advance()['status'], 'completed')
        points = deepcopy(self.raw()['atomic']['points'])
        state = self.service.unlock(self.topic['id'], skipped['module_id'], skipped['event_id'])
        self.assertEqual(active(state)['question_id'], 'module-1-example')
        self.service.choose(payload(self.service, choice='mastered'))
        self.answer()
        state = self.advance()
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['skipped_modules'], [])
        self.assertEqual(len([s for s in self.raw()['steps'] if s['kind'] == 'introduction']), 2)
        self.assertEqual(self.raw()['atomic']['points'], points)

    def test_multiple_introductions_without_examples_finish_and_omit_module_stats(self):
        self.topic['modules'] = []
        self.topic['content_order'] = [item for item in self.topic['content_order'] if item['kind'] == 'introduction']
        self.topic['id'] = 'composed-atoms-only'
        self.service = self.make()
        state = self.first_section()
        self.assertEqual(active(state)['introduction_id'], 'intro-b')
        self.advance()
        self.advance()
        self.answer()
        state = self.advance()
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['modules'], [])
        self.assertEqual(state['atomic_progress'], {'completed': 3, 'total': 3})

    def test_section_without_points_passes_directly_to_its_example(self):
        self.topic['memory_chunks'] = self.topic['memory_chunks'][1:]
        self.topic['flashcards'] = [card for card in self.topic['flashcards'] if card['point_ids'] == ['c']]
        self.topic['id'] = 'composed-empty-section'
        self.service = self.make()
        state = self.advance()
        self.assertEqual(active(state)['question_id'], 'module-1-example')
        self.assertEqual(state['atomic_progress'], {'completed': 0, 'total': 1})

    def test_unavailable_initial_cards_do_not_block_examples_or_next_introduction(self):
        self.advance()
        self.advance()
        self.answer('wrong')
        points = deepcopy(self.raw()['atomic']['points'])
        state = self.advance()
        self.assertEqual(active(state)['question_id'], 'module-1-example')
        self.assertEqual(state['atomic_progress']['completed'], 0)
        self.service.choose(payload(self.service, choice='mastered'))
        self.answer()
        data = payload(self.service)
        state = self.service.advance(data)
        self.assertEqual(active(state)['introduction_id'], 'intro-b')
        self.assertEqual(self.service.advance(data), state)
        self.assertEqual(self.raw()['atomic']['points'], points)
        self.service = self.make()
        self.assertEqual(self.service.state(), state)

    def test_atoms_only_advance_then_resume_unfinished_learning_without_false_completion(self):
        self.topic['modules'] = []
        self.topic['content_order'] = [item for item in self.topic['content_order'] if item['kind'] == 'introduction']
        self.topic['id'] = 'pending-atoms'
        self.service = self.make()
        self.advance()
        self.advance()
        self.answer('wrong')
        self.assertEqual(active(self.advance())['introduction_id'], 'intro-b')
        self.advance()
        self.advance()
        self.answer()
        state = self.advance()
        self.assertEqual(state['phase'], 'waiting')
        self.assertEqual(state['status'], 'in_progress')
        self.assertIsNone(state['completed_at'])
        self.assertTrue(self.raw()['atomic_catch_up'])
        self.service = self.make()
        self.assertEqual(self.service.state(), state)
        for _ in range(10):
            state = self.service.state()
            if state.get('training_mode') != 'atomic_retrieval':
                break
            if state['phase'] == 'answer':
                self.answer()
            else:
                self.when += timedelta(minutes=5)
                self.advance()
        state = self.service.state()
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['atomic_progress'], {'completed': 3, 'total': 3})
        self.assertEqual(len([s for s in self.raw()['steps'] if s['kind'] == 'introduction']), 2)

    def test_chunk_reading_exposes_guidance_but_card_does_not(self):
        state = self.advance()
        self.assertEqual(state['phase'], 'chunk')
        self.assertEqual(state['introduction']['id'], 'intro-a')
        state = self.advance()
        self.assertIsNone(state['introduction'])
        self.assertIsNone(state['chunk'])
        self.answer()
        state = self.advance()
        self.assertTrue(next(s for s in state['steps'] if s['kind'] == 'introduction')['unlocked'])

    def test_already_waiting_session_advances_on_continue(self):
        from graspmemoedu.atomic import AtomicLearningService
        from graspmemoedu.unified_learning import PointState
        self.advance()
        self.advance()
        self.answer('wrong')
        with self.service._transaction() as con:
            state = self.service._load(con)
            points = PointState(state)
            AtomicLearningService._next(self.service.atomic, points)
            self.assertEqual(points['phase'], 'waiting')
            state['atomic'] = dict(points)
            self.service._save(con, state)
        self.service = self.make()
        self.assertEqual(active(self.advance())['question_id'], 'module-1-example')

    def test_due_review_is_checked_even_when_section_initial_learning_is_unfinished(self):
        self.advance()
        self.advance()
        self.answer()
        self.advance()
        self.service.choose(payload(self.service, choice='unmastered'))
        self.advance()
        before = self.service.state()
        self.when += timedelta(hours=7)
        self.assertEqual(self.service.state(), before)
        state = self.advance()
        self.assertEqual(state['current']['point_ids'], ['a'])
        self.assertEqual(state['mode'], 'review')
        self.assertFalse(self.raw()['atomic']['points']['b']['mastered'])

    def test_single_card_preview_finishes_without_opening_another_module(self):
        self.advance()
        self.advance()
        self.answer()
        with self.service._transaction() as con:
            state = self.service._load(con)
            state['atomic'].update(preview_single_card=True, mode='review')
            self.service._save(con, state)
        state = self.advance()
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['mode'], 'review')
        self.assertEqual(len(self.raw()['attempts']), 1)


if __name__ == '__main__':
    unittest.main()
