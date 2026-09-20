"""End-to-end backend review behavior on isolated synthetic course data."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import random
import threading
import unittest
from unittest import mock
import uuid

from fsrs import Card, Rating, Scheduler

from math_learning.fsrs_engine import FSRSEngine, utc_time
from math_learning.practice import PracticePolicy
from math_learning.review import ReviewBackend
from math_learning.service import DomainError, LearningService
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class ReviewBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.course = synthetic_course()
        self.time = datetime.now(timezone.utc)
        self.learning = LearningService(self.course, self.path, DeterministicJudge())
        self.backend = ReviewBackend(self.learning, clock=lambda: self.time, rng=random.Random(942),
                                     engine=FSRSEngine(Scheduler(enable_fuzzing=False)))

    def restart(self):
        self.learning = LearningService(self.course, self.path, self.learning.judge)
        self.backend = ReviewBackend(self.learning, clock=lambda: self.time)

    def count(self, table):
        with closing(self.learning._connect()) as con:
            return con.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def card(self, module_id='module-1'):
        with closing(self.learning._connect()) as con:
            return dict(self.backend._card(con, module_id))

    def enroll(self, module_id='module-1', mastery=4):
        return self.backend.record_module_result(module_id=module_id, mastery=mastery,
                    result_id='enrollment-' + module_id, completed_at=self.time, elapsed_ms=1234)

    def begin(self, module_id='module-1'):
        self.time = max(self.time, utc_time(self.card(module_id)['due_at']))
        return self.backend.begin(module_id, request_id=str(uuid.uuid4()))

    def payload(self, state, **extra):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'question_id': state['practice']['id'], **extra}

    def answer(self, state, correct):
        result = self.backend.submit(state['session_id'], self.payload(state, answer='correct' if correct else 'wrong', elapsed_ms=1400))
        self.backend.process_submission(result['submission_id'])
        return self.backend.submission(result['submission_id'])['state']

    def advance(self, state):
        return self.backend.advance(state['session_id'], self.payload(state))

    def round(self, sequence, expected):
        self.enroll()
        state = self.begin()
        questions = []
        for index, correct in enumerate(sequence):
            questions.append(state['practice']['id'])
            state = self.answer(state, correct)
            self.assertEqual(state['practice_error_count'], sequence[:index + 1].count(False))
            if index < len(sequence) - 1:
                self.assertIsNone(state['mastery'])
                state = self.advance(state)
        self.assertEqual(len(questions), len(set(questions)))
        self.assertEqual(state['mastery'], expected)
        return state

    def lesson_payload(self, **extra):
        state = self.learning.state()
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def complete_lesson_example(self):
        self.learning.choose(self.lesson_payload(choice='mastered'))
        result = self.learning.submit(self.lesson_payload(answer='correct', elapsed_ms=2000))
        self.learning.process_submission(result['submission_id'])
        return result

    def test_library_is_opt_in_and_does_not_backfill_old_lesson_results(self):
        other = LearningService(self.course, self.path, DeterministicJudge(), learner_id='other')
        state = other.state()
        payload = {'request_id': 'choose', 'expected_revision': state['revision'],
                   'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], 'choice': 'mastered'}
        state = other.choose(payload)
        payload.update(request_id='answer', expected_revision=state['revision'], answer='correct')
        payload.pop('choice')
        item = other.submit(payload)
        other.process_submission(item['submission_id'])
        review = ReviewBackend(other)
        self.assertIsNone(review.next_due_at())
        self.assertEqual(other.state()['modules'][0]['mastery'], 4)

    def test_lesson_result_atomically_enrolls_and_replay_does_not_reschedule(self):
        item = self.complete_lesson_example()
        first = self.card()
        self.assertEqual(self.count('review_ratings'), 1)
        self.assertEqual(self.learning.state()['modules'][0]['mastery'], 4)
        self.learning.process_submission(item['submission_id'])
        self.assertEqual(self.card(), first)
        self.assertEqual(self.count('review_ratings'), 1)
        self.assertEqual(self.backend.due(), [])

    def test_fsrs_failure_cannot_commit_lesson_score_without_schedule(self):
        self.learning.choose(self.lesson_payload(choice='mastered'))
        item = self.learning.submit(self.lesson_payload(answer='correct'))
        with mock.patch.object(self.backend.engine, 'schedule', side_effect=RuntimeError('synthetic engine failure')):
            with self.assertRaises(RuntimeError):
                self.learning.process_submission(item['submission_id'])
        self.assertIsNone(self.learning.state()['modules'][0]['mastery'])
        self.assertEqual(self.count('review_ratings'), 0)
        self.learning.recover_interrupted()
        self.assertEqual(self.learning.submission(item['submission_id'])['status'], 'error')

    def test_all_four_ratings_match_unmodified_upstream_scheduler(self):
        for mastery in (1, 2, 3, 4):
            with self.subTest(mastery=mastery):
                scheduled = self.backend.engine.schedule(None, mastery, self.time)
                actual = Card.from_json(scheduled['card_json'])
                expected, _ = Scheduler(enable_fuzzing=False).review_card(
                    Card(card_id=actual.card_id, due=self.time), Rating(mastery), self.time)
                self.assertEqual(actual.to_dict(), expected.to_dict())

    def test_due_boundary_restart_and_claim_are_persistent(self):
        enrolled = self.enroll()
        self.time = utc_time(enrolled['due_at']) - timedelta(microseconds=1)
        self.assertEqual(self.backend.due(), [])
        self.restart()
        self.time += timedelta(microseconds=1)
        tasks = self.backend.due()
        self.assertEqual([task['module_id'] for task in tasks], ['module-1'])
        state = self.backend.begin('module-1', request_id='start-once')
        self.assertEqual(self.backend.due(), [])
        self.assertEqual(self.backend.begin('module-1', request_id='start-once'), state)
        self.assertEqual(self.backend.begin('module-1', request_id='same-round')['session_id'], state['session_id'])
        self.restart()
        self.assertEqual(self.backend.state(state['session_id']), state)
        self.assertEqual(self.count('review_sessions'), 1)

    def test_first_correct_finishes_as_four_and_schedules_once(self):
        state = self.round([True], 4)
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['practice_answered_count'], 1)
        self.assertEqual(self.count('review_ratings'), 2)
        self.assertGreater(utc_time(state['due_at']), self.time)
        self.assertEqual(state['actions'], [])

    def test_one_wrong_adds_two_and_finishes_as_two(self):
        state = self.round([False, True, True], 2)
        self.assertEqual(state['practice_target_count'], 3)
        self.assertEqual(state['status'], 'completed')

    def test_two_wrong_add_four_and_finish_as_one(self):
        state = self.round([False, True, False, True, True], 1)
        self.assertEqual(state['practice_target_count'], 5)
        self.assertEqual(state['status'], 'completed')

    def test_third_wrong_on_last_question_pauses_before_completion(self):
        state = self.round([False, False, True, True, False], None)
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(self.count('review_ratings'), 1)
        self.assertEqual(self.learning.state()['status'], 'paused')
        self.assertEqual(self.backend.due(), [])
        self.assertIsNone(self.backend.next_due_at())
        self.assertEqual(state['actions'], [])

    def test_no_example_intro_reference_or_unvisited_practices_in_response(self):
        self.enroll()
        state = self.begin()
        body = json.dumps(state)
        self.assertNotIn('REFERENCE_ONLY', body)
        self.assertNotIn('INTRODUCTION_ONLY', body)
        self.assertNotIn('module-1-example', body)
        self.assertNotIn('order', state)
        self.assertIsNone(state['practice']['explanation_html'])
        self.assertTrue(state['practice']['id'].startswith('module-1-practice-'))

    def test_random_selection_varies_between_rounds_without_repeated_questions_within_round(self):
        self.enroll()
        first_questions = []
        for _ in range(8):
            state = self.begin()
            first_questions.append(state['practice']['id'])
            self.answer(state, True)
        self.assertGreater(len(set(first_questions)), 1)

    def test_random_order_and_policy_are_frozen_for_started_round(self):
        self.enroll()
        state = self.begin()
        self.backend.policy = PracticePolicy('review-v2', (1, 2), (4, 3))
        state = self.answer(state, False)
        self.assertEqual(state['practice_target_count'], 3)
        self.restart()
        state = self.backend.state(state['session_id'])
        self.assertEqual(state['policy_version'], 'review-v1')
        state = self.answer(self.advance(state), True)
        state = self.answer(self.advance(state), True)
        self.assertEqual(state['mastery'], 2)
        self.backend.policy = PracticePolicy('review-v2', (1, 2), (4, 3))
        state = self.begin()
        state = self.answer(state, False)
        self.assertEqual(state['practice_target_count'], 2)
        state = self.answer(self.advance(state), True)
        self.assertEqual(state['mastery'], 3)

    def test_duplicate_answers_and_continue_do_not_expand_twice(self):
        self.enroll()
        state = self.begin()
        payload = self.payload(state, answer='wrong')
        item = self.backend.submit(state['session_id'], payload)
        self.backend.submit(state['session_id'], payload)
        self.backend.process_submission(item['submission_id'])
        self.backend.process_submission(item['submission_id'])
        state = self.backend.submit(state['session_id'], payload)['state']
        self.assertEqual(state['practice_error_count'], 1)
        self.assertEqual(state['practice_target_count'], 3)
        with self.assertRaises(DomainError):
            self.backend.submit(state['session_id'], {**payload, 'answer': 'correct'})
        advance = self.payload(state)
        next_state = self.backend.advance(state['session_id'], advance)
        self.assertEqual(self.backend.advance(state['session_id'], advance), next_state)
        with self.assertRaises(DomainError):
            self.backend.submit(state['session_id'], self.payload(state, answer='correct'))

    def test_judge_failure_does_not_count_and_same_request_can_retry(self):
        self.enroll()
        state = self.begin()
        payload = self.payload(state, answer='correct')
        self.learning.judge = lambda *_: {'status': 'error', 'correct': None}
        item = self.backend.submit(state['session_id'], payload)
        self.backend.process_submission(item['submission_id'])
        failed = self.backend.submission(item['submission_id'])
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['state']['practice_answered_count'], 0)
        self.assertEqual(self.count('review_ratings'), 1)
        self.learning.judge = DeterministicJudge()
        self.backend.submit(state['session_id'], payload)
        self.backend.process_submission(item['submission_id'])
        self.assertEqual(self.backend.submission(item['submission_id'])['state']['mastery'], 4)
        self.assertEqual(self.count('review_submissions'), 1)

    def test_restart_recovers_pending_without_changing_random_question(self):
        self.enroll()
        state = self.begin()
        payload = self.payload(state, answer='correct')
        item = self.backend.submit(state['session_id'], payload)
        self.restart()
        self.backend.recover_interrupted()
        self.assertEqual(self.backend.submission(item['submission_id'])['status'], 'error')
        restored = self.backend.state(state['session_id'])
        self.assertEqual(restored['practice']['id'], state['practice']['id'])
        self.assertEqual(restored['practice_error_count'], 0)
        self.backend.submit(state['session_id'], payload)
        self.backend.process_submission(item['submission_id'])
        self.assertEqual(self.backend.state(state['session_id'])['mastery'], 4)

    def test_pause_freezes_whole_topic_without_rating_and_unlock_starts_new_round(self):
        self.enroll()
        self.enroll('module-2')
        state = self.begin()
        original_card = self.card()['card_json']
        paused = self.backend.pause(state['session_id'], request_id='pause-once')
        self.assertIsNone(paused['mastery'])
        self.assertEqual(self.backend.due(), [])
        self.assertEqual(self.learning.state()['status'], 'paused')
        with self.assertRaises(DomainError):
            self.learning.choose(self.lesson_payload(choice='mastered'))
        with self.assertRaises(DomainError):
            self.backend.unlock_topic(pause_event_id='wrong')
        self.backend.unlock_topic(pause_event_id=paused['pause_event_id'])
        self.backend.unlock_topic(pause_event_id=paused['pause_event_id'])
        self.assertEqual(self.card()['card_json'], original_card)
        self.assertEqual(self.learning.state()['status'], 'in_progress')
        new_round = self.begin()
        self.assertNotEqual(new_round['session_id'], state['session_id'])
        self.assertEqual(self.backend.pause(state['session_id'], request_id='pause-once')['status'], 'paused')
        self.assertEqual(self.backend.state(new_round['session_id'])['status'], 'in_progress')
        self.assertEqual(self.backend.state(new_round['session_id'])['practice_error_count'], 0)

    def test_lesson_pause_also_stops_review_and_lesson_unlock_releases_it(self):
        self.enroll()
        state = self.begin()
        paused = self.learning.pause({'request_id': 'pause-lesson'})
        self.assertEqual(self.backend.state(state['session_id'])['status'], 'paused')
        self.learning.unlock(self.course['id'], paused['pause']['module_id'], paused['pause']['event_id'])
        self.assertEqual(len(self.backend.due()), 1)

    def test_pause_after_completed_lesson_keeps_lesson_scores(self):
        for _ in range(3):
            self.complete_lesson_example()
            self.learning.advance(self.lesson_payload())
        self.assertEqual(self.learning.state()['status'], 'completed')
        state = self.begin()
        paused = self.backend.pause(state['session_id'], request_id='completed-topic-pause')
        self.assertEqual(self.learning.state()['status'], 'completed')
        self.assertEqual([m['mastery'] for m in self.learning.state()['modules']], [4, 4, 4])
        self.assertEqual(self.backend.due(), [])
        self.backend.unlock_topic(pause_event_id=paused['pause_event_id'])
        self.assertTrue(self.backend.due())

    def test_pause_during_judging_discards_old_result_even_after_new_round(self):
        self.enroll()
        state = self.begin()
        entered, release = threading.Event(), threading.Event()
        def blocked_judge(*_):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test did not release judge')
            return {'status': 'judged', 'correct': True, 'reason': 'late test result'}
        self.learning.judge = blocked_judge
        item = self.backend.submit(state['session_id'], self.payload(state, answer='correct'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.backend.process_submission, item['submission_id'])
            self.assertTrue(entered.wait(3))
            try:
                paused = self.backend.pause(state['session_id'], request_id='pause-in-flight')
                self.backend.unlock_topic(pause_event_id=paused['pause_event_id'])
                new_round = self.begin()
            finally:
                release.set()
            future.result(timeout=5)
        self.assertEqual(self.backend.submission(item['submission_id'])['status'], 'discarded')
        self.assertEqual(self.backend.state(new_round['session_id'])['practice_answered_count'], 0)
        self.assertEqual(self.count('review_ratings'), 1)

    def test_two_competing_starts_claim_one_session(self):
        self.enroll()
        self.time = utc_time(self.card()['due_at'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.backend.begin, 'module-1', request_id=f'concurrent-{i}') for i in range(2)]
            states = [future.result(timeout=5) for future in futures]
        self.assertEqual(states[0]['session_id'], states[1]['session_id'])
        self.assertEqual(self.count('review_sessions'), 1)

    def test_identity_isolation_and_unknown_module(self):
        self.enroll()
        state = self.begin()
        other = ReviewBackend(LearningService(self.course, self.path, DeterministicJudge(), learner_id='other'))
        self.assertEqual(other.due(), [])
        with self.assertRaises(DomainError) as raised:
            other.state(state['session_id'])
        self.assertEqual(raised.exception.status_code, 404)
        with self.assertRaises(DomainError):
            self.backend.begin('unknown', request_id='unknown')

    def test_result_idempotency_strict_ratings_timezone_and_chronology(self):
        kwargs = dict(module_id='module-1', mastery=3, result_id='result-1', completed_at=self.time)
        first = self.backend.record_module_result(**kwargs)
        self.assertEqual(self.backend.record_module_result(**kwargs), first)
        with self.assertRaises(DomainError):
            self.backend.record_module_result(**{**kwargs, 'mastery': 4})
        for value in (None, 0, 5, True, '4'):
            with self.subTest(value=value), self.assertRaises(DomainError):
                self.backend.record_module_result(**{**kwargs, 'result_id': str(uuid.uuid4()), 'mastery': value})
        with self.assertRaises(DomainError):
            self.backend.record_module_result(**{**kwargs, 'result_id': 'old', 'completed_at': self.time - timedelta(days=1)})
        with self.assertRaises(DomainError):
            self.backend.record_module_result(**{**kwargs, 'result_id': 'naive', 'completed_at': self.time.replace(tzinfo=None)})
        self.assertEqual(self.count('review_ratings'), 1)

    def test_retention_persists_and_rescheduling_preserves_original_logs(self):
        self.enroll()
        before = self.card()
        settings = self.backend.configure_scheduler(desired_retention=0.97)
        self.assertEqual(settings['rescheduled_cards'], 0)
        self.assertEqual(self.card(), before)
        self.restart()
        self.assertEqual(self.backend.scheduler_settings()['desired_retention'], 0.97)
        result = self.backend.configure_scheduler(reschedule_existing=True)
        self.assertEqual(result['rescheduled_cards'], 1)
        self.assertLess(utc_time(self.card()['due_at']), utc_time(before['due_at']))
        self.assertEqual(self.count('review_ratings'), 1)
        with closing(self.learning._connect()) as con:
            log = con.execute('SELECT scheduler_json FROM review_ratings').fetchone()[0]
        self.assertEqual(Scheduler.from_json(log).desired_retention, 0.9)

    def test_invalid_settings_and_active_rescheduling_cannot_partially_write(self):
        original = self.backend.scheduler_settings()
        for value in (0, 1, -0.1, 2, True, float('nan'), '0.9'):
            with self.subTest(value=value), self.assertRaises(DomainError):
                self.backend.configure_scheduler(desired_retention=value)
        for parameters in ([], [0.1], [float('nan')] * 21, [True] * 21):
            with self.assertRaises(DomainError):
                self.backend.configure_scheduler(parameters=parameters)
        self.assertEqual(self.backend.scheduler_settings(), original)
        self.enroll()
        self.begin()
        with self.assertRaises(DomainError):
            self.backend.configure_scheduler(desired_retention=0.95, reschedule_existing=True)
        self.assertEqual(self.backend.scheduler_settings(), original)

    def test_optimization_without_cross_day_data_reports_shortage(self):
        self.enroll()
        with self.assertRaises(DomainError) as error:
            self.backend.optimize_parameters()
        self.assertEqual(error.exception.code, 'insufficient_optimization_data')

    def test_optimizer_reports_small_history_instead_of_returning_defaults(self):
        for i in range(8):
            self.backend.record_module_result(module_id='module-1', mastery=4, result_id=f'small-{i}',
                                               completed_at=self.time + timedelta(days=i))
        status = self.backend.optimization_status()
        self.assertEqual(status['cross_day_review_count'], 7)
        self.assertFalse(status['enough_data'])
        with self.assertRaises(DomainError) as error:
            self.backend.optimize_parameters()
        self.assertEqual(error.exception.code, 'insufficient_optimization_data')
        self.assertEqual(self.count('review_optimizations'), 0)

    def test_optimizer_can_use_other_topics_but_never_other_learners(self):
        self.enroll()
        self.backend.record_module_result(module_id='module-1', mastery=3, result_id='second',
                                           completed_at=self.time + timedelta(days=2))
        other_course = synthetic_course()
        other_course['id'] = 'other-topic'
        other_topic = ReviewBackend(LearningService(other_course, self.path, DeterministicJudge()))
        other_topic.record_module_result(module_id='module-1', mastery=4, result_id='other-topic-first', completed_at=self.time)
        other_topic.record_module_result(module_id='module-1', mastery=4, result_id='other-topic-next', completed_at=self.time + timedelta(days=2))
        other_person = ReviewBackend(LearningService(self.course, self.path, DeterministicJudge(), learner_id='other-learner'))
        other_person.record_module_result(module_id='module-1', mastery=4, result_id='other-person-first', completed_at=self.time)
        self.assertEqual(self.backend.optimization_status(scope='topic')['log_count'], 2)
        self.assertEqual(self.backend.optimization_status(scope='learner')['log_count'], 4)
        self.assertEqual(self.backend.optimization_status()['cross_day_review_count'], 2)

    def test_sqlite_backup_preserves_review_settings_queue_and_pause(self):
        self.enroll()
        state = self.begin()
        paused = self.backend.pause(state['session_id'], request_id='backup-paused')
        self.backend.configure_scheduler(desired_retention=0.94)
        restored_dir = self.path / 'restored'
        restored_dir.mkdir()
        self.learning.backup(restored_dir / 'learning.sqlite3')
        restored = ReviewBackend(LearningService(self.course, restored_dir, DeterministicJudge()), clock=lambda: self.time)
        self.assertEqual(restored.state(state['session_id']), paused)
        self.assertEqual(restored.scheduler_settings()['desired_retention'], 0.94)
        self.assertEqual(restored.due(), [])
        restored.unlock_topic(pause_event_id=paused['pause_event_id'])
        self.assertEqual(len(restored.due()), 1)

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'optional optimizer runtime is not installed')
    def test_real_optimizer_candidate_apply_and_restart(self):
        # 10 independent cards, 53 logs each => 520 eligible cross-day reviews.
        # Below 512 the real upstream optimizer returns defaults without training.
        training_course = synthetic_course(module_count=10)
        training_dir = self.path / 'optimizer'
        trainer = ReviewBackend(LearningService(training_course, training_dir, DeterministicJudge()))
        for module_index in range(1, 11):
            for index in range(53):
                rating = [4, 3, 2, 4, 1, 4, 3, 4][(index + module_index) % 8]
                trainer.record_module_result(module_id=f'module-{module_index}', mastery=rating,
                    result_id=f'optimizer-fixture-{module_index}-{index}',
                    completed_at=self.time + timedelta(days=3 * index), elapsed_ms=1800)
        original = trainer.scheduler_settings()
        with self.assertRaises(DomainError) as error:
            self.backend.optimize_parameters(include_optimal_retention=True)
        self.assertEqual(error.exception.code, 'insufficient_retention_data')
        result = trainer.optimize_parameters()
        self.assertEqual(len(result['parameters']), 21)
        self.assertEqual(result['log_count'], 530)
        self.assertEqual(result['cross_day_review_count'], 520)
        self.assertFalse(result['applied'])
        self.assertNotEqual(result['parameters'], original['parameters'], 'Must run real optimization, not return default weights')
        self.assertEqual(trainer.scheduler_settings(), original)
        applied = trainer.apply_optimization(result['optimization_id'], reschedule_existing=True)
        self.assertTrue(applied['applied'])
        self.assertEqual(applied['parameters'], result['parameters'])
        self.assertEqual(applied['rescheduled_cards'], 10)
        restored = ReviewBackend(LearningService(training_course, training_dir, DeterministicJudge()))
        self.assertEqual(restored.scheduler_settings()['parameters'], result['parameters'])
        with closing(restored.learning._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_ratings').fetchone()[0], 530)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_optimizations').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
