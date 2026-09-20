"""Behavioral acceptance of the agreed topic and module rules.

These tests drive the public service interface and do not fabricate database state.
The synthetic judge controls correctness only; mastery is computed by the service.
"""

from __future__ import annotations

import copy
from contextlib import closing
import json
import shutil
import sqlite3
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from math_learning.service import DomainError, LearningService

from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class LearningAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.course = synthetic_course()
        self.judge = DeterministicJudge()
        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, self.judge)

    def active(self, state=None):
        state = state or self.service.state()
        return next(step for step in state["steps"] if step["id"] == state["active_step_id"])

    def current(self, state=None):
        state = state or self.service.state()
        return next(step for step in state["steps"] if step["id"] == state["current_step_id"])

    def payload(self, state=None, **extra):
        state = state or self.service.state()
        result = {
            "request_id": str(uuid.uuid4()),
            "expected_revision": state["revision"],
            "attempt_id": state["attempt_id"],
            "step_id": state["active_step_id"],
        }
        result.update(extra)
        return result

    def choose(self, choice):
        return self.service.choose(self.payload(choice=choice))

    def advance(self):
        return self.service.advance(self.payload())

    def answer(self, answer):
        reserved = self.service.submit(self.payload(answer=answer))
        self.service.process_submission(reserved["submission_id"])
        return self.service.submission(reserved["submission_id"])

    def study(self):
        before_calls = len(self.judge.calls)
        state = self.choose("unmastered")
        self.assertEqual(self.active(state)["phase"], "unmastered")
        self.assertIn("continue", self.active(state)["actions"])
        self.assertEqual(len(self.judge.calls), before_calls)
        state = self.advance()
        if self.active(state)["kind"] == "introduction":
            state = self.advance()
        self.assertEqual(self.active(state)["kind"], "example")
        self.assertIsNotNone(self.active(state)["explanation_html"])
        self.assertNotIn("mastered", self.active(state)["actions"])
        state = self.advance()
        self.assertEqual(self.active(state)["kind"], "practice")
        return state

    def module(self, index=0, state=None):
        return (state or self.service.state())["modules"][index]

    def practice_sequence(self, correctness, expected_mastery):
        self.study()
        for number, correct in enumerate(correctness, start=1):
            result = self.answer("correct" if correct else "wrong")
            state = result["state"]
            self.assertEqual(result["correct"], correct)
            self.assertEqual(self.active(state)["phase"], "feedback")
            self.assertEqual(self.module(state=state)["practice_answered_count"], number)
            error_count = correctness[:number].count(False)
            self.assertEqual(self.module(state=state)["practice_error_count"], error_count)
            self.assertEqual(self.module(state=state)["practice_target_count"], [2, 3, 5, 7][error_count])
            self.assertIsNotNone(self.active(state)["explanation_html"])
            if number < len(correctness):
                self.assertIsNone(self.module(state=state)["mastery"])
                self.advance()
        module = self.module()
        self.assertEqual(module["status"], "completed")
        self.assertEqual(module["mastery"], expected_mastery)
        self.assertEqual(module["practice_target_count"], len(correctness))
        self.assertEqual(module["practice_error_count"], correctness.count(False))
        self.assertEqual(self.active()["kind"], "practice", "Feedback must wait for Continue")
        self.advance()
        self.assertEqual(self.active()["module_id"], "module-2")
        self.assertEqual(self.active()["phase"], "choice")

    def test_example_correct_scores_four_and_waits_for_continue(self):
        state = self.service.state()
        self.assertEqual(self.active(state)["actions"], ["mastered", "unmastered"])
        self.assertIsNone(self.active(state)["explanation_html"])
        with self.assertRaises(DomainError):
            self.service.submit(self.payload(answer="correct"))
        self.choose("mastered")
        result = self.answer("correct")
        self.assertEqual(result["status"], "judged")
        self.assertEqual(self.module()["mastery"], 4)
        self.assertEqual(self.module()["practice_answered_count"], 0)
        self.assertFalse(self.service.state()["introduction_read"])
        self.assertEqual(self.active()["phase"], "feedback")
        self.assertEqual(self.active()["actions"], ["continue"])
        self.advance()
        self.assertEqual(self.active()["module_id"], "module-2")

    def test_first_unmastered_requires_continue_and_introduction_is_once(self):
        example_id = self.service.state()["active_step_id"]
        self.choose("unmastered")
        self.assertEqual(self.active()["kind"], "example")
        self.assertEqual(self.judge.calls, [])
        state = self.advance()
        self.assertEqual(self.active(state)["kind"], "introduction")
        withdrawn = next(step for step in state["steps"] if step["id"] == example_id)
        self.assertFalse(withdrawn["visible"])
        self.assertTrue(withdrawn["visited"])
        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, self.judge)
        self.assertEqual(self.active()["kind"], "introduction")
        self.advance()
        self.assertTrue(self.service.state()["introduction_read"])
        self.assertEqual(self.active()["id"], example_id)
        self.assertIsNotNone(self.active()["explanation_html"])
        self.assertNotIn("mastered", self.active()["actions"])
        self.advance()
        self.answer("correct")
        self.advance()
        self.answer("correct")
        self.advance()
        self.choose("unmastered")
        self.advance()
        self.assertEqual(self.active()["kind"], "example")
        self.assertEqual(self.active()["module_id"], "module-2")
        self.assertEqual(sum(step["kind"] == "introduction" for step in self.service.state()["steps"]), 1)

    def test_example_wrong_feedback_precedes_introduction_and_is_not_practice_error(self):
        self.choose("mastered")
        self.answer("wrong")
        self.assertEqual(self.active()["phase"], "feedback")
        self.assertEqual(self.active()["kind"], "example")
        self.assertEqual(self.module()["practice_error_count"], 0)
        self.assertFalse(any(step["kind"] == "introduction" for step in self.service.state()["steps"]))
        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, self.judge)
        self.assertFalse(self.active()["feedback"]["correct"])
        self.advance()
        self.assertEqual(self.active()["kind"], "introduction")
        self.advance()
        self.assertEqual(self.active()["kind"], "example")
        self.assertNotIn("submit", self.active()["actions"])
        self.assertEqual(self.module()["practice_error_count"], 0)

    def test_two_correct_practices_score_four(self):
        self.practice_sequence([True, True], 4)
        self.assertNotEqual(self.module()["learning_path"], None)

    def test_one_wrong_requires_three_practices_and_scores_three(self):
        self.practice_sequence([False, True, True], 3)

    def test_two_wrong_require_five_practices_and_score_two(self):
        self.practice_sequence([False, False, True, True, True], 2)

    def test_three_wrong_require_seven_practices_and_score_one(self):
        self.practice_sequence([False, False, False, True, True, True, True], 1)

    def test_wrong_last_initial_question_extends_before_completion(self):
        self.practice_sequence([True, False, True], 3)

    def test_fourth_error_pauses_whole_topic_with_no_mastery(self):
        self.study()
        for position in range(4):
            self.answer("wrong")
            if position < 3:
                self.advance()
        state = self.service.state()
        self.assertEqual(state["status"], "paused")
        self.assertEqual(state["pause"]["reason"], "practice_error_limit")
        self.assertEqual(state["pause"]["message"], "检测到状态不佳，已暂停作答")
        self.assertIsNone(self.module()["mastery"])
        self.assertEqual(self.module()["practice_error_count"], 4)
        self.assertEqual(self.module()["practice_answered_count"], 4)
        self.assertTrue(all(not step["actions"] for step in state["steps"]))
        self.assertFalse(self.active()["feedback"]["correct"])
        self.assertIsNotNone(self.active()["explanation_html"])
        with self.assertRaises(DomainError):
            self.advance()
        with self.assertRaises(DomainError):
            self.answer("correct")
        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, self.judge)
        self.assertEqual(self.service.state()["status"], "paused")

    def test_fourth_error_on_seventh_question_overrides_completion(self):
        self.study()
        for index, correct in enumerate([False, False, False, True, True, True, False]):
            self.answer("correct" if correct else "wrong")
            if index < 6:
                self.advance()
        self.assertEqual(self.service.state()["status"], "paused")
        self.assertEqual(self.module()["practice_answered_count"], 7)
        self.assertEqual(self.module()["practice_target_count"], 7)
        self.assertIsNone(self.module()["mastery"])

    def test_duplicate_submission_counts_once_and_uses_trusted_answer(self):
        self.study()
        question_id = self.active()["question_id"]
        request = self.payload(answer="wrong", reference_answer="attacker", correct=True)
        first = self.service.submit(request)
        again = self.service.submit(copy.deepcopy(request))
        self.assertEqual(first["submission_id"], again["submission_id"])
        self.service.process_submission(first["submission_id"])
        self.service.process_submission(first["submission_id"])
        replay = self.service.submit(copy.deepcopy(request))
        self.assertEqual(replay["submission_id"], first["submission_id"])
        self.assertEqual(len(self.judge.calls), 1)
        self.assertEqual(self.judge.calls[0][1], "REFERENCE_ONLY::" + question_id)
        self.assertEqual(self.module()["practice_error_count"], 1)
        self.assertEqual(self.module()["practice_target_count"], 3)
        self.assertEqual(self.module()["practice_answered_count"], 1)
        with self.assertRaises(DomainError):
            self.service.submit(self.payload(answer="correct"))

    def test_duplicate_continue_and_stale_window_do_not_skip_steps(self):
        state = self.choose("unmastered")
        request = self.payload(state)
        self.service.advance(request)
        self.service.advance(copy.deepcopy(request))
        self.assertEqual(self.active()["kind"], "introduction")
        stale_request = self.payload(state)
        with self.assertRaises(DomainError) as caught:
            self.service.advance(stale_request)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.active()["kind"], "introduction")

    def test_reusing_request_id_for_different_payload_is_rejected(self):
        request = self.payload(choice="mastered")
        self.service.choose(request)
        altered = dict(request, choice="unmastered")
        with self.assertRaises(DomainError):
            self.service.choose(altered)
        self.assertEqual(self.active()["phase"], "answer")

    def test_judge_errors_allow_retry_without_counting_answer_or_error(self):
        self.study()
        for failing_answer in ["service-error", "raise-error"]:
            result = self.answer(failing_answer)
            self.assertEqual(result["status"], "error")
            self.assertIsNone(result["correct"])
            self.assertEqual(self.module()["practice_error_count"], 0)
            self.assertEqual(self.module()["practice_answered_count"], 0)
            self.assertEqual(self.module()["practice_target_count"], 2)
            self.assertIn("submit", self.active()["actions"])
        self.answer("correct")
        self.assertEqual(self.module()["practice_answered_count"], 1)

    def test_same_submission_id_can_retry_error_without_duplicate_score(self):
        self.study()
        outcomes = iter([
            {"status": "error", "correct": None, "reason": "Transient"},
            {"status": "judged", "correct": False, "reason": "Recovered"},
        ])
        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, lambda *args: next(outcomes))
        request = self.payload(answer="wrong")
        reserved = self.service.submit(request)
        self.service.process_submission(reserved["submission_id"])
        self.assertEqual(self.service.submission(reserved["submission_id"])["status"], "error")
        retry = self.service.submit(copy.deepcopy(request))
        self.assertEqual(retry["submission_id"], reserved["submission_id"])
        self.service.process_submission(retry["submission_id"])
        self.assertEqual(self.service.submission(retry["submission_id"])["status"], "judged")
        self.assertEqual(self.module()["practice_error_count"], 1)
        self.assertEqual(self.module()["practice_answered_count"], 1)
        self.assertEqual(self.module()["practice_target_count"], 3)

    def test_invalid_judge_output_cannot_be_misclassified_as_wrong(self):
        invalid_results = [
            {"status": "judged", "correct": None, "reason": "Missing boolean"},
            {"status": "judged", "correct": "true", "reason": "String boolean"},
            {"status": "error", "correct": False, "reason": "Error is not incorrect"},
            {},
        ]
        for index, invalid in enumerate(invalid_results):
            with self.subTest(invalid=invalid):
                self.service = LearningService(
                    copy.deepcopy(self.course), self.data_dir / f"invalid-{index}",
                    lambda *args, output=invalid: output,
                )
                self.study()
                result = self.answer("wrong")
                self.assertEqual(result["status"], "error")
                self.assertIsNone(result["correct"])
                self.assertEqual(self.module()["practice_error_count"], 0)
                self.assertEqual(self.module()["practice_answered_count"], 0)
                self.assertEqual(self.module()["practice_target_count"], 2)

    def test_blank_answer_is_rejected_without_judge_call(self):
        self.choose("mastered")
        for answer in ["", "   ", "\n\t"]:
            with self.subTest(answer=answer), self.assertRaises(DomainError):
                self.service.submit(self.payload(answer=answer))
        self.assertEqual(self.judge.calls, [])
        self.assertEqual(self.active()["phase"], "answer")

    def test_recover_interrupted_submission_is_retryable_and_not_an_error_count(self):
        self.study()
        request = self.payload(answer="wrong")
        reserved = self.service.submit(request)
        self.assertEqual(self.service.state()["pending_submission_id"], reserved["submission_id"])
        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, self.judge)
        self.service.recover_interrupted()
        result = self.service.submission(reserved["submission_id"])
        self.assertEqual(result["status"], "error")
        self.assertIsNone(self.service.state()["pending_submission_id"])
        self.assertEqual(self.module()["practice_answered_count"], 0)
        self.assertEqual(self.module()["practice_error_count"], 0)
        retry = self.service.submit(copy.deepcopy(request))
        self.service.process_submission(retry["submission_id"])
        self.assertEqual(self.module()["practice_answered_count"], 1)
        self.assertEqual(self.module()["practice_error_count"], 1)

    def test_manual_pause_is_available_in_every_learning_phase(self):
        def at_choice():
            pass

        def at_answer():
            self.choose("mastered")

        def at_unmastered():
            self.choose("unmastered")

        def at_intro():
            at_unmastered()
            self.advance()

        def at_explanation():
            at_intro()
            self.advance()

        def at_practice():
            self.study()

        def at_feedback():
            self.study()
            self.answer("wrong")

        for index, prepare in enumerate([at_choice, at_answer, at_unmastered, at_intro, at_explanation, at_practice, at_feedback]):
            with self.subTest(phase=prepare.__name__):
                self.service = LearningService(copy.deepcopy(self.course), self.data_dir / str(index), self.judge)
                prepare()
                before = self.module().copy()
                state = self.service.pause({"request_id": str(uuid.uuid4())})
                self.assertEqual(state["status"], "paused")
                self.assertEqual(state["pause"]["reason"], "manual_difficulty")
                self.assertEqual(self.module()["practice_error_count"], before["practice_error_count"])
                self.assertIsNone(self.module()["mastery"])
                self.assertTrue(all(not step["actions"] for step in state["steps"]))
                with self.assertRaises(DomainError):
                    self.advance()

    def test_manual_pause_during_judge_discards_late_result_even_after_unlock(self):
        started, release = threading.Event(), threading.Event()
        errors = []

        def blocking_judge(question, reference, answer):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test judge was not released")
            return {"status": "judged", "correct": True, "reason": "Late result"}

        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, blocking_judge)
        self.choose("mastered")
        submission = self.service.submit(self.payload(answer="correct"))
        old_attempt = self.service.state()["attempt_id"]

        def run_judge():
            try:
                self.service.process_submission(submission["submission_id"])
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=run_judge, daemon=True)
        worker.start()
        self.assertTrue(started.wait(3), "Judge did not start")
        try:
            start_pause = time.monotonic()
            state = self.service.pause({"request_id": str(uuid.uuid4())})
            self.assertLess(time.monotonic() - start_pause, 2, "Pause waited for the running judge")
            self.assertEqual(state["status"], "paused")
            pause = state["pause"]
            self.service.unlock(state["topic_id"], pause["module_id"], pause["event_id"])
            fresh_attempt = self.service.state()["attempt_id"]
            self.assertNotEqual(fresh_attempt, old_attempt)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.service.submission(submission["submission_id"])["status"], "discarded")
        self.assertEqual(self.service.state()["attempt_id"], fresh_attempt)
        self.assertEqual(self.active()["phase"], "choice")
        self.assertEqual(self.module()["status"], "in_progress")
        self.assertIsNone(self.module()["mastery"])
        self.assertEqual(self.module()["practice_answered_count"], 0)

    def test_pause_replay_cannot_pause_an_already_unlocked_new_attempt(self):
        pause_request = {"request_id": str(uuid.uuid4())}
        state = self.service.pause(pause_request)
        pause = state["pause"]
        self.service.unlock(state["topic_id"], pause["module_id"], pause["event_id"])
        fresh_attempt = self.service.state()["attempt_id"]
        self.service.pause(copy.deepcopy(pause_request))
        self.assertEqual(self.service.state()["status"], "in_progress")
        self.assertEqual(self.service.state()["attempt_id"], fresh_attempt)

    def test_unlock_new_attempt_is_idempotent_and_preserves_intro_history(self):
        self.study()
        self.answer("wrong")
        old_attempt = self.service.state()["attempt_id"]
        old_steps = {step["id"] for step in self.service.state()["steps"]}
        state = self.service.pause({"request_id": str(uuid.uuid4())})
        pause = state["pause"]
        with self.assertRaises(DomainError):
            self.service.unlock(state["topic_id"], "module-2", pause["event_id"])
        self.service.unlock(state["topic_id"], pause["module_id"], pause["event_id"])
        unlocked = self.service.state()
        self.assertNotEqual(unlocked["attempt_id"], old_attempt)
        new_attempt = unlocked["attempt_id"]
        self.assertEqual(self.active()["phase"], "choice")
        self.assertEqual(self.module()["practice_error_count"], 0)
        self.assertEqual(self.module()["practice_answered_count"], 0)
        self.assertEqual(self.module()["practice_target_count"], 2)
        self.assertTrue(unlocked["introduction_read"])
        self.assertTrue(old_steps.issubset({step["id"] for step in unlocked["steps"]}))
        self.service.unlock(state["topic_id"], pause["module_id"], pause["event_id"])
        self.assertEqual(self.service.state()["attempt_id"], new_attempt)
        self.choose("unmastered")
        self.advance()
        self.assertEqual(self.active()["kind"], "example")

    def test_pause_after_settlement_keeps_score_and_unlocks_next_module(self):
        self.choose("mastered")
        self.answer("correct")
        state = self.service.pause({"request_id": str(uuid.uuid4())})
        self.assertEqual(self.module()["status"], "completed")
        self.assertEqual(self.module()["mastery"], 4)
        with self.assertRaises(DomainError):
            self.advance()
        pause = state["pause"]
        self.service.unlock(state["topic_id"], pause["module_id"], pause["event_id"])
        self.assertEqual(self.active()["module_id"], "module-2")
        self.assertEqual(self.module()["mastery"], 4)

    def test_last_settled_module_can_pause_and_unlock_completes_topic(self):
        course = synthetic_course(module_count=1)
        self.service = LearningService(course, self.data_dir / "single", self.judge)
        self.choose("mastered")
        self.answer("correct")
        state = self.service.pause({"request_id": str(uuid.uuid4())})
        self.assertEqual(state["status"], "paused")
        self.assertEqual(self.module()["mastery"], 4)
        pause = state["pause"]
        self.service.unlock(state["topic_id"], pause["module_id"], pause["event_id"])
        self.assertEqual(self.service.state()["status"], "completed")

    def test_all_examples_correct_complete_without_introduction_or_practices(self):
        for module_index in range(3):
            self.choose("mastered")
            self.answer("correct")
            self.assertEqual(self.module(module_index)["mastery"], 4)
            self.advance()
        state = self.service.state()
        self.assertEqual(state["status"], "completed")
        self.assertFalse(state["introduction_read"])
        self.assertFalse(any(step["kind"] in {"introduction", "practice"} for step in state["steps"]))
        self.assertNotIn("mastery", state, "Topic must not invent an aggregate mastery")
        try:
            self.service.pause({"request_id": str(uuid.uuid4())})
        except DomainError:
            pass
        self.assertEqual(self.service.state()["status"], "completed")

    def test_history_review_does_not_rejudge_or_advance(self):
        self.study()
        first_practice = self.service.state()["active_step_id"]
        self.answer("wrong")
        self.advance()
        active_step = self.service.state()["active_step_id"]
        before = self.module().copy()
        state = self.service.select(self.payload(step_id=first_practice))
        self.assertEqual(state["active_step_id"], active_step)
        self.assertEqual(state["current_step_id"], first_practice)
        self.assertEqual(self.current()["actions"], [])
        with self.assertRaises(DomainError):
            self.service.submit(self.payload(step_id=first_practice, answer="correct"))
        self.assertEqual(self.module(), before)
        self.assertEqual(len(self.judge.calls), 1)
        self.service.select(self.payload(step_id=active_step))
        self.assertIn("submit", self.current()["actions"])

    def test_state_never_leaks_reference_answers_or_locked_content(self):
        def verify():
            serialized = json.dumps(self.service.state())
            self.assertNotIn("REFERENCE_ONLY", serialized)
            self.assertNotIn("reference_answer", serialized)
            self.assertNotIn("QUESTION::module-2-example", serialized)
            self.assertNotIn("EXPLANATION::module-2-example", serialized)
            shown = {step.get("question_id") for step in self.service.state()["steps"]}
            for question in self.course["modules"][0]["practices"]:
                if question["id"] not in shown:
                    self.assertNotIn(json.dumps(question["html"]), serialized)

        verify()
        self.assertNotIn("INTRODUCTION_ONLY", json.dumps(self.service.state()))
        self.assertNotIn("EXPLANATION::module-1-example", json.dumps(self.service.state()))
        with self.assertRaises(DomainError):
            self.service.select(self.payload(step_id="module-2-example"))
        self.study()
        verify()
        self.answer("wrong")
        verify()

    def test_feedback_and_manual_pause_survive_restarting_service(self):
        self.study()
        result = self.answer("wrong")
        old_state = self.service.state()
        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, self.judge)
        restored = self.service.state()
        self.assertEqual(restored["active_step_id"], old_state["active_step_id"])
        self.assertEqual(self.active()["phase"], "feedback")
        self.assertEqual(self.active()["feedback"]["submission_id"], result["submission_id"])
        self.assertEqual(self.module()["practice_target_count"], 3)
        paused = self.service.pause({"request_id": str(uuid.uuid4())})
        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, self.judge)
        self.assertEqual(self.service.state()["pause"], paused["pause"])
        self.assertEqual(self.service.state()["status"], "paused")
        self.assertTrue(all(not step["actions"] for step in self.service.state()["steps"]))

    def test_pause_log_contains_real_trigger_and_stable_event_id(self):
        self.study()
        last_result = None
        for index in range(4):
            last_question_id = self.active()["question_id"]
            last_result = self.answer("wrong")
            if index < 3:
                self.advance()
        state = self.service.state()
        records = [json.loads(line) for line in self.service.log_path.read_text(encoding="utf-8").splitlines()]
        pauses = [event for event in records if event.get("type") == "topic_paused"]
        self.assertEqual(len(pauses), 1)
        event = pauses[0]
        self.assertEqual(event["event_id"], state["pause"]["event_id"])
        self.assertEqual(event["learner_id"], "local")
        self.assertEqual(event["topic_id"], self.course["id"])
        self.assertEqual(event["course_version"], self.course["version"])
        self.assertEqual(event["attempt_id"], state["attempt_id"])
        self.assertEqual(event["practice_error_count"], 4)
        self.assertEqual(event["pause_reason"], "practice_error_limit")
        self.assertEqual(event["question_id"], last_question_id)
        self.assertEqual(event["student_answer"], "wrong")
        self.assertFalse(event["judgement"]["correct"])
        self.assertEqual(event["submission_id"], last_result["submission_id"])
        self.assertIsNone(event["mastery"])
        self.assertTrue(event["time"])
        self.assertTrue(event["step_id"])
        self.service.flush_logs()
        repeated = [json.loads(line) for line in self.service.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records, repeated)
        self.assertEqual(len({record["event_id"] for record in records}), len(records))

    def test_failed_log_write_preserves_pause_and_recovers_exactly_once(self):
        self.choose("unmastered")
        self.advance()
        prior_log = self.service.log_path.read_text(encoding="utf-8")
        with mock.patch("math_learning.service.os.replace", side_effect=OSError("Synthetic log disk unavailable")):
            state = self.service.pause({"request_id": str(uuid.uuid4())})
            self.assertEqual(state["status"], "paused")
            self.assertFalse(self.service.flush_logs())
            self.assertEqual(self.service.log_path.read_text(encoding="utf-8"), prior_log)
        # Reconstructing the service must rebuild the readable file from the committed DB.
        self.service = LearningService(copy.deepcopy(self.course), self.data_dir, self.judge)
        recovered = self.service.state()
        self.assertEqual(recovered["pause"], state["pause"])
        rows = [json.loads(line) for line in self.service.log_path.read_text(encoding="utf-8").splitlines()]
        pause_rows = [row for row in rows if row["event_id"] == state["pause"]["event_id"]]
        self.assertEqual(len(pause_rows), 1)
        self.assertIsNone(pause_rows[0]["question_id"])
        self.assertIsNone(pause_rows[0]["student_answer"])
        self.assertIsNone(pause_rows[0]["judgement"])
        self.assertIsNone(pause_rows[0]["mastery"])
        pause = state["pause"]
        self.service.unlock(state["topic_id"], pause["module_id"], pause["event_id"])
        self.service.unlock(state["topic_id"], pause["module_id"], pause["event_id"])
        self.service.flush_logs()
        rows = [json.loads(line) for line in self.service.log_path.read_text(encoding="utf-8").splitlines()]
        unlocks = [row for row in rows if row.get("type") == "admin_unlocked"]
        self.assertEqual(len(unlocks), 1)
        self.assertEqual(unlocks[0]["pause_event_id"], pause["event_id"])
        self.assertNotEqual(unlocks[0]["old_attempt_id"], unlocks[0]["new_attempt_id"])

    def test_manual_pause_in_feedback_logs_existing_answer_and_judgement(self):
        self.study()
        question_id = self.active()["question_id"]
        submission = self.answer("wrong")
        state = self.service.pause({"request_id": str(uuid.uuid4())})
        records = [json.loads(line) for line in self.service.log_path.read_text(encoding="utf-8").splitlines()]
        event = next(row for row in records if row["event_id"] == state["pause"]["event_id"])
        self.assertEqual(event["pause_reason"], "manual_difficulty")
        self.assertEqual(event["practice_error_count"], 1)
        self.assertIsNone(event["mastery"])
        self.assertEqual(event["question_id"], question_id)
        self.assertEqual(event["student_answer"], "wrong")
        self.assertEqual(event["submission_id"], submission["submission_id"])
        self.assertEqual(event["judgement"]["correct"], False)

    def test_manual_pause_with_pending_answer_logs_submission_without_fabricated_verdict(self):
        self.choose("mastered")
        pending = self.service.submit(self.payload(answer="correct"))
        state = self.service.pause({"request_id": str(uuid.uuid4())})
        records = [json.loads(line) for line in self.service.log_path.read_text(encoding="utf-8").splitlines()]
        event = next(row for row in records if row["event_id"] == state["pause"]["event_id"])
        self.assertEqual(event["pause_reason"], "manual_difficulty")
        self.assertEqual(event["practice_error_count"], 0)
        self.assertIsNone(event["mastery"])
        self.assertEqual(event["question_id"], "module-1-example")
        self.assertEqual(event["student_answer"], "correct")
        self.assertEqual(event["submission_id"], pending["submission_id"])
        self.assertIsNone(event["judgement"])

    def test_sqlite_backup_restores_pause_answers_history_and_logs(self):
        self.study()
        self.answer("wrong")
        expected = self.service.pause({"request_id": str(uuid.uuid4())})
        expected_log = self.service.log_path.read_text(encoding="utf-8")
        backup = self.data_dir / "backups" / "acceptance.sqlite3"
        self.service.backup(backup)
        with closing(sqlite3.connect(backup)) as con:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with self.assertRaises(DomainError):
            self.service.backup(backup)
        with self.assertRaises(DomainError):
            self.service.backup(self.service.db_path)
        pause = expected["pause"]
        self.service.unlock(expected["topic_id"], pause["module_id"], pause["event_id"])
        self.assertEqual(self.service.state()["status"], "in_progress")
        restored_dir = self.data_dir / "restored"
        restored_dir.mkdir()
        shutil.copyfile(backup, restored_dir / "learning.sqlite3")
        restored = LearningService(copy.deepcopy(self.course), restored_dir, self.judge)
        self.assertEqual(restored.state(), expected)
        self.assertEqual(restored.log_path.read_text(encoding="utf-8"), expected_log)

    def test_content_version_mismatch_cannot_overwrite_existing_progress(self):
        self.study()
        before = self.service.state()
        changed = copy.deepcopy(self.course)
        changed["version"] = "e" * 64
        with self.assertRaises(DomainError) as caught:
            LearningService(changed, self.data_dir, self.judge)
        self.assertEqual(caught.exception.code, "course_version_mismatch")
        self.assertEqual(self.service.state(), before)

    def test_missing_questions_answers_or_constraints_are_preparation_errors(self):
        def too_few_practices(course):
            course["modules"][0]["practices"] = course["modules"][0]["practices"][:6]

        def missing_answer(course):
            course["modules"][0]["example"]["reference_answer"] = ""

        def null_answer(course):
            course["modules"][0]["example"]["reference_answer"] = None

        def wrong_constraints_type(course):
            course["modules"][0]["example"]["constraints"] = ["Return one expression"]

        def duplicate_question_id(course):
            course["modules"][0]["practices"][1]["id"] = course["modules"][0]["practices"][0]["id"]

        for mutation in [too_few_practices, missing_answer, null_answer, wrong_constraints_type, duplicate_question_id]:
            with self.subTest(mutation=mutation.__name__):
                invalid = copy.deepcopy(self.course)
                mutation(invalid)
                with self.assertRaises(DomainError) as caught:
                    LearningService(invalid, self.data_dir / mutation.__name__, self.judge)
                self.assertEqual(caught.exception.code, "invalid_course")


if __name__ == "__main__":
    unittest.main()
