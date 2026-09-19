"""Guest-only HTTP acceptance in isolated temporary databases.

No account, invitation, login or production-data operation is performed here.
All judgments are local deterministic callables; TestClient opens no network port.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import threading
import time
import unittest
import uuid

from fastapi.testclient import TestClient

from math_learning.api import create_app
from math_learning.config import Settings
from math_learning.service import LearningService
from tests.fixtures import synthetic_course, temporary_test_directory


class GuestJudge:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, question, reference_answer, student_answer):
        self.calls.append((question, reference_answer, student_answer))
        if student_answer == "blocked-correct":
            self.started.set()
            if not self.release.wait(10):
                raise RuntimeError("Synthetic guest judge was not released")
        return {"status": "judged", "correct": student_answer in {"correct", "blocked-correct"},
                "reason": "Synthetic guest-only judgement"}


class GuestAccessAcceptanceTests(unittest.TestCase):
    origin = "http://127.0.0.1:8767"
    remote_origin = "https://guest-only-acceptance.github.io"
    registered_only_message = "该功能需注册账号才能使用"

    def setUp(self):
        self.temporary = temporary_test_directory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        self.course = synthetic_course(3)
        self.judge = GuestJudge()
        self.settings = Settings(data_dir=self.data_dir, port=8767,
                                 allowed_origins=(self.remote_origin,))
        self._clients = []
        self.addCleanup(self._shutdown_clients)
        self._open_app()

    def _open_app(self):
        self.template = LearningService(self.course, self.data_dir, self.judge)
        self.app = create_app(self.settings, self.template)
        self.client = TestClient(self.app, base_url=self.origin)
        self.client.__enter__()
        self._clients.append(self.client)
        response = self.client.get("/api/session", headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 200, response.text)
        self.transport_token = response.json()["session_token"]

    def _restart_app(self):
        self.client.__exit__(None, None, None)
        self._clients.remove(self.client)
        self._open_app()

    def _shutdown_clients(self):
        self.judge.release.set()
        for client in reversed(self._clients):
            client.__exit__(None, None, None)
        self._clients.clear()
        # The production executor permits graceful asynchronous shutdown. Join
        # only this test process's judge threads before removing its database.
        for thread in threading.enumerate():
            if thread.name.startswith("answer-judge"):
                thread.join(timeout=5)

    def _headers(self, identity=None, transport=None):
        result = {"Origin": self.origin,
                  "X-Learning-Session": self.transport_token if transport is None else transport}
        if identity is not None:
            result["X-Learning-Identity"] = identity
        return result

    def _assert_guest_access(self, access):
        self.assertEqual(access["role"], "guest")
        self.assertTrue({"learn", "submit_answer", "review_history"} <= set(access["features"]))
        self.assertNotIn("pause_topic", access["features"])
        self.assertIsInstance(access["learner_id"], str)
        self.assertTrue(access["learner_id"].strip())

    def _guest(self):
        response = self.client.post("/api/access/guest", json={}, headers=self._headers())
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertIsInstance(result["identity_token"], str)
        self.assertTrue(result["identity_token"].strip())
        self._assert_guest_access(result["access"])
        return result["identity_token"], result["access"]

    def _state(self, identity, client=None):
        response = (client or self.client).get("/api/state", headers=self._headers(identity))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self._assert_guest_access(result["access"])
        self.assertEqual(result["learner_id"], result["access"]["learner_id"])
        return result

    @staticmethod
    def _active(state):
        return next(step for step in state["steps"] if step["id"] == state["active_step_id"])

    def _payload(self, identity, **extra):
        state = self._state(identity)
        return {"request_id": str(uuid.uuid4()), "expected_revision": state["revision"],
                "attempt_id": state["attempt_id"], "step_id": state["active_step_id"], **extra}

    def _mutate(self, endpoint, identity, **extra):
        response = self.client.post(f"/api/{endpoint}", json=self._payload(identity, **extra),
                                    headers=self._headers(identity))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _wait_for_submission(self, identity, submission_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/submissions/{submission_id}", headers=self._headers(identity))
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            if result["status"] != "pending":
                return result
            time.sleep(0.01)
        self.fail("Synthetic guest submission did not finish")

    def _answer(self, identity, answer):
        submitted = self._mutate("submit", identity, answer=answer)
        result = self._wait_for_submission(identity, submitted["submission_id"])
        self.assertEqual(result["status"], "judged", result)
        self.assertEqual(result["correct"], answer == "correct")
        self._assert_guest_access(result["state"]["access"])
        return result

    def _study(self, identity):
        self._mutate("choose", identity, choice="unmastered")
        state = self._mutate("continue", identity)
        if self._active(state)["kind"] == "introduction":
            state = self._mutate("continue", identity)
        self.assertEqual(self._active(state)["phase"], "reading")
        state = self._mutate("continue", identity)
        self.assertEqual(self._active(state)["kind"], "practice")
        return state

    def _assert_manual_pause_forbidden(self, identity, extra_headers=None):
        before = self._state(identity)
        response = self.client.post("/api/pause", json={"request_id": str(uuid.uuid4())},
                                    headers={**self._headers(identity), **(extra_headers or {})})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], {
            "code": "feature_forbidden", "message": self.registered_only_message,
        })
        self.assertEqual(self._state(identity), before, "Rejected guest pause changed learning progress")
        return before

    def test_guest_access_and_state_expose_only_guest_features(self):
        identity, access = self._guest()
        response = self.client.get("/api/access", headers=self._headers(identity))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), access)
        state = self._state(identity)
        self.assertEqual(state["access"], access)
        self.assertEqual(self._active(state)["phase"], "choice")
        self.assertNotIn("REFERENCE_ONLY", response.text)

    def test_transport_session_cannot_be_replaced_by_guest_identity(self):
        identity, _ = self._guest()
        before = self._state(identity)
        for transport in ["", "forged-session"]:
            with self.subTest(transport=transport):
                response = self.client.get("/api/state", headers=self._headers(identity, transport))
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.json()["detail"]["code"], "authentication_required")
                self.assertNotIn("QUESTION::", response.text)
        response = self.client.post("/api/access/guest", json={}, headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self._state(identity), before)

    def test_every_learning_endpoint_rejects_missing_and_forged_identity(self):
        identity, access = self._guest()
        before = self._state(identity)
        base = self._payload(identity)
        requests = [
            ("GET", "/api/access", None), ("GET", "/api/state", None),
            ("GET", "/api/submissions/nonexistent-submission", None),
            ("POST", "/api/choose", {**base, "choice": "mastered"}),
            ("POST", "/api/submit", {**base, "answer": "correct"}),
            ("POST", "/api/continue", base), ("POST", "/api/select", base),
            ("POST", "/api/pause", {"request_id": str(uuid.uuid4())}),
        ]
        for candidate, code in [(None, "identity_required"), (identity + "tampered", "invalid_identity"),
                                (access["learner_id"], "invalid_identity")]:
            for method, path, body in requests:
                with self.subTest(identity=candidate is None, method=method, path=path):
                    kwargs = {"headers": self._headers(candidate)}
                    if body is not None:
                        kwargs["json"] = body
                    response = self.client.request(method, path, **kwargs)
                    self.assertEqual(response.status_code, 401, response.text)
                    self.assertEqual(response.json()["detail"]["code"], code)
                    self.assertNotIn("QUESTION::", response.text)
        self.assertEqual(self._state(identity), before)
        self.assertEqual(self.judge.calls, [])

    def test_two_guests_have_independent_progress_and_submission_access(self):
        first, first_access = self._guest()
        second, second_access = self._guest()
        self.assertNotEqual(first, second)
        self.assertNotEqual(first_access["learner_id"], second_access["learner_id"])
        second_before = self._state(second)
        self._mutate("choose", first, choice="mastered")
        judged = self._answer(first, "correct")
        first_after = self._state(first)
        self.assertEqual(first_after["modules"][0]["mastery"], 4)
        self.assertEqual(self._state(second), second_before)
        response = self.client.get(f"/api/submissions/{judged['submission_id']}", headers=self._headers(second))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "unknown_submission")
        response = self.client.post("/api/continue", json=self._payload(first), headers=self._headers(second))
        self.assertIn(response.status_code, {403, 409}, response.text)
        self.assertEqual(self._state(second), second_before)
        self._mutate("choose", second, choice="unmastered")
        self._mutate("continue", second)
        self.assertEqual(self._state(first), first_after)

    def test_guest_can_choose_submit_continue_and_review_visited_history(self):
        identity, _ = self._guest()
        initial = self._study(identity)
        first_practice = initial["active_step_id"]
        self._answer(identity, "correct")
        second = self._mutate("continue", identity)
        active_step = second["active_step_id"]
        counters = second["modules"][0].copy()
        review = self._mutate("select", identity, step_id=first_practice)
        self.assertEqual(review["current_step_id"], first_practice)
        self.assertEqual(review["active_step_id"], active_step)
        self.assertEqual(review["modules"][0], counters)
        self.assertEqual(next(step for step in review["steps"] if step["current"])["actions"], [])
        self._mutate("select", identity, step_id=active_step)
        result = self._answer(identity, "correct")
        self.assertEqual(result["state"]["modules"][0]["mastery"], 4)
        self.assertEqual(result["state"]["modules"][0]["practice_answered_count"], 2)
        advanced = self._mutate("continue", identity)
        self.assertEqual(self._active(advanced)["module_id"], "module-2")
        self.assertEqual(self._active(advanced)["phase"], "choice")

    def test_same_request_id_cannot_mix_two_guests_actions_or_submissions(self):
        first, first_access = self._guest()
        second, _ = self._guest()
        shared_choice = str(uuid.uuid4())
        for identity in (first, second):
            payload = self._payload(identity, choice="mastered", request_id=shared_choice)
            response = self.client.post("/api/choose", json=payload, headers=self._headers(identity))
            self.assertEqual(response.status_code, 200, response.text)
            chosen = self._state(identity)
            self.assertEqual(self._active(chosen)["phase"], "answer")
            retried = self.client.post("/api/choose", json=payload, headers=self._headers(identity))
            self.assertEqual(retried.status_code, 200, retried.text)
            self.assertEqual(self._state(identity), chosen)

        shared_submission = str(uuid.uuid4())
        submitted = self._mutate("submit", first, answer="correct", request_id=shared_submission)
        first_result = self._wait_for_submission(first, submitted["submission_id"])
        self.assertTrue(first_result["correct"])
        first_before = self._state(first)
        second_before = self._state(second)
        response = self.client.post("/api/submit", json=self._payload(
            second, answer="wrong", request_id=shared_submission), headers=self._headers(second))
        # Submission identifiers remain globally unique. A collision is rejected;
        # it must not replay the other learner's answer or change either state.
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "idempotency_conflict")
        self.assertNotIn(first_access["learner_id"], response.text)
        self.assertNotIn("REFERENCE_ONLY", response.text)
        self.assertEqual(self._state(first), first_before)
        self.assertEqual(self._state(second), second_before)
        response = self.client.get(f"/api/submissions/{submitted['submission_id']}",
                                   headers=self._headers(second))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "unknown_submission")
        second_result = self._answer(second, "wrong")
        response = self.client.get(f"/api/submissions/{second_result['submission_id']}",
                                   headers=self._headers(first))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self._state(first), first_before)
        self.assertEqual(len(self.judge.calls), 2)

        shared_continue = str(uuid.uuid4())
        first_advanced = self._mutate("continue", first, request_id=shared_continue)
        second_advanced = self._mutate("continue", second, request_id=shared_continue)
        self.assertEqual(self._active(first_advanced)["module_id"], "module-2")
        self.assertEqual(self._active(first_advanced)["phase"], "choice")
        self.assertEqual(self._active(second_advanced)["kind"], "introduction")
        self.assertEqual(self._state(first), first_advanced)

    def test_snapshot_restore_preserves_guest_token_and_matching_progress(self):
        identity, access = self._guest()
        self._study(identity)
        result = self._answer(identity, "wrong")
        snapshotted = self._state(identity)
        snapshot = self.data_dir / "guest-snapshot.sqlite3"
        self.app.state.learning_services[access["learner_id"]].backup(snapshot)
        self.assertNotIn(identity.encode("utf-8"), snapshot.read_bytes())
        self._mutate("continue", identity)
        self.assertNotEqual(self._state(identity), snapshotted)

        # Restore into a fresh temporary data directory. The original temporary
        # source stays intact; no installed application or account is accessed.
        restored_dir = self.data_dir / "restored-guest"
        restored_dir.mkdir()
        shutil.copyfile(snapshot, restored_dir / "learning.sqlite3")
        self.data_dir = restored_dir
        self.settings = Settings(data_dir=self.data_dir, port=8767,
                                 allowed_origins=(self.remote_origin,))
        self._restart_app()
        response = self.client.get("/api/access", headers=self._headers(identity))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), access)
        self.assertEqual(self._state(identity), snapshotted)
        restored_result = self._wait_for_submission(identity, result["submission_id"])
        self.assertEqual(restored_result, result)
        self._assert_manual_pause_forbidden(identity)
        continued = self._mutate("continue", identity)
        self.assertEqual(self._active(continued)["phase"], "answer")

    def test_refresh_reuses_guest_identity_and_progress(self):
        identity, access = self._guest()
        self._study(identity)
        self._answer(identity, "wrong")
        before = self._state(identity)
        # A fresh browser client reuses the saved identity without minting a new
        # guest or entering an account flow. The application keeps running.
        refresh = TestClient(self.app, base_url=self.origin)
        try:
            response = refresh.get("/api/session", headers={"Origin": self.origin})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["session_token"], self.transport_token)
            response = refresh.get("/api/access", headers=self._headers(identity))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), access)
            self.assertEqual(self._state(identity, client=refresh), before)
        finally:
            refresh.close()

    def test_recreating_app_recovers_same_guest_identity_and_committed_progress(self):
        identity, access = self._guest()
        self._study(identity)
        self._answer(identity, "wrong")
        before = self._state(identity)
        old_transport = self.transport_token
        self._restart_app()
        self.assertNotEqual(self.transport_token, old_transport)
        stale_transport = self.client.get("/api/state", headers=self._headers(identity, old_transport))
        self.assertEqual(stale_transport.status_code, 401, stale_transport.text)
        response = self.client.get("/api/access", headers=self._headers(identity))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), access)
        self.assertEqual(self._state(identity), before)
        continued = self._mutate("continue", identity)
        self.assertEqual(continued["learner_id"], access["learner_id"])
        self.assertEqual(self._active(continued)["phase"], "answer")

    def test_interrupted_guest_submission_is_recovered_after_app_restart(self):
        identity, access = self._guest()
        self._mutate("choose", identity, choice="mastered")
        payload = self._payload(identity, answer="correct")
        # Model a process exiting between transactional reservation and worker
        # scheduling. No real or mock judge runs before the simulated restart.
        guest_service = self.app.state.learning_services[access["learner_id"]]
        pending = guest_service.submit(payload)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(self._state(identity)["pending_submission_id"], pending["submission_id"])
        self._restart_app()
        restored = self._state(identity)
        self.assertEqual(restored["learner_id"], access["learner_id"])
        self.assertIsNone(restored["pending_submission_id"])
        self.assertEqual(self._active(restored)["phase"], "answer")
        self.assertIsNone(restored["modules"][0]["mastery"])
        self.assertEqual(restored["modules"][0]["practice_error_count"], 0)
        response = self.client.get(f"/api/submissions/{pending['submission_id']}", headers=self._headers(identity))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "error")
        self.assertIsNone(response.json()["correct"])
        retried = self.client.post("/api/submit", json=payload, headers=self._headers(identity))
        self.assertEqual(retried.status_code, 200, retried.text)
        judged = self._wait_for_submission(identity, pending["submission_id"])
        self.assertEqual(judged["status"], "judged")
        self.assertEqual(judged["state"]["modules"][0]["mastery"], 4)
        self.assertEqual(len(self.judge.calls), 1)

    def test_guest_manual_pause_is_forbidden_in_every_available_phase(self):
        identity, _ = self._guest()
        self._assert_manual_pause_forbidden(identity)
        self._mutate("choose", identity, choice="unmastered")
        self._assert_manual_pause_forbidden(identity)
        self._mutate("continue", identity)
        self._assert_manual_pause_forbidden(identity)
        self._mutate("continue", identity)
        self._assert_manual_pause_forbidden(identity)
        self._mutate("continue", identity)
        self._assert_manual_pause_forbidden(identity)
        self._answer(identity, "wrong")
        self._assert_manual_pause_forbidden(identity)
        self._assert_manual_pause_forbidden(identity, {"X-Learning-Role": "registered", "X-Learning-Features": "pause_topic"})
        other, _ = self._guest()
        self._mutate("choose", other, choice="mastered")
        self._assert_manual_pause_forbidden(other)
        self._answer(other, "correct")
        settled = self._assert_manual_pause_forbidden(other)
        self.assertEqual(settled["modules"][0]["mastery"], 4)

    def test_guest_cannot_pause_in_flight_judgement_or_discard_its_result(self):
        identity, _ = self._guest()
        self._mutate("choose", identity, choice="mastered")
        submitted = self._mutate("submit", identity, answer="blocked-correct")
        try:
            self.assertTrue(self.judge.started.wait(2), "Synthetic judge never started")
            pending = self._state(identity)
            self.assertEqual(pending["pending_submission_id"], submitted["submission_id"])
            began = time.monotonic()
            self._assert_manual_pause_forbidden(identity)
            self.assertLess(time.monotonic() - began, 2, "Guest permission refusal waited for judgment")
            self.assertEqual(self._state(identity), pending)
        finally:
            self.judge.release.set()
        result = self._wait_for_submission(identity, submitted["submission_id"])
        self.assertEqual(result["status"], "judged")
        self.assertTrue(result["correct"])
        self.assertEqual(result["state"]["status"], "in_progress")
        self.assertEqual(result["state"]["modules"][0]["mastery"], 4)
        self.assertIsNone(result["state"]["pause"])

    def test_fourth_wrong_practice_still_automatically_pauses_guest(self):
        identity, _ = self._guest()
        self._study(identity)
        for count in range(1, 5):
            result = self._answer(identity, "wrong")
            state = result["state"]
            self.assertEqual(state["modules"][0]["practice_error_count"], count)
            if count < 4:
                self.assertEqual(state["status"], "in_progress")
                self._mutate("continue", identity)
        self.assertEqual(state["status"], "paused")
        self.assertEqual(state["pause"]["reason"], "practice_error_limit")
        self.assertEqual(state["pause"]["message"], "检测到状态不佳，已暂停作答")
        self.assertIsNone(state["modules"][0]["mastery"])
        self.assertIsNone(state["pending_submission_id"])
        self.assertTrue(all(not step["actions"] for step in state["steps"]))
        response = self.client.post("/api/continue", json=self._payload(identity), headers=self._headers(identity))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "topic_paused")
        self._assert_manual_pause_forbidden(identity)
        self._restart_app()
        self.assertEqual(self._state(identity), state)

    def test_remote_guest_identity_header_is_allowed_in_cors_preflight(self):
        response = self.client.options("/api/submit", headers={
            "Origin": self.remote_origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type, x-learning-identity",
            "Access-Control-Request-Private-Network": "true",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["access-control-allow-origin"], self.remote_origin)
        allowed_headers = {item.strip().lower() for item in response.headers["access-control-allow-headers"].split(",")}
        self.assertIn("x-learning-identity", allowed_headers)


if __name__ == "__main__":
    unittest.main()
