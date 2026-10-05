"""Public API acceptance, using only temporary guest data and a local judge.

TestClient opens no network connection. No account creation or invitation login
is exercised, and no production configuration, pairing key or course is read.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import threading
import time
import unittest
import uuid

from fastapi.testclient import TestClient

from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from tests.fixtures import synthetic_course, temporary_test_directory


class FixtureClock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class PublicFixtureJudge:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.started = threading.Event()
        self.release = threading.Event()
        self.recoverable_failed = False

    def __call__(self, question, reference_answer, student_answer):
        with self.lock:
            self.calls.append((question, reference_answer, student_answer))
            fail_once = student_answer == "recoverable-error" and not self.recoverable_failed
            if fail_once:
                self.recoverable_failed = True
        if student_answer == "blocked-correct":
            self.started.set()
            if not self.release.wait(30):
                raise RuntimeError("Public API fixture judge was not released")
        if student_answer == "service-error" or fail_once:
            return {"status": "error", "correct": None, "reason": "Synthetic unavailable judge"}
        return {"status": "judged", "correct": student_answer in {"correct", "blocked-correct", "recoverable-error"},
                "reason": "Synthetic public API judgement"}


class PublicAccessAcceptanceTests(unittest.TestCase):
    local_origin = "http://127.0.0.1:8767"
    public_origin = "https://fixture.trycloudflare.com"
    public_host = "fixture.trycloudflare.com"
    github_origin = "https://public-acceptance.github.io"

    def setUp(self):
        self.temporary = temporary_test_directory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        self.judge = PublicFixtureJudge()
        self.service = LearningService(synthetic_course(), self.data_dir, self.judge)
        self.settings = Settings(data_dir=self.data_dir, port=8767,
                                 allowed_origins=(self.github_origin,),
                                 public_api_origin=self.public_origin)
        self.app = create_app(self.settings, self.service)
        self.clock = FixtureClock()
        self.app.state.public_limits.clock = self.clock
        self.client = TestClient(self.app, base_url=self.public_origin)
        self.client.__enter__()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.judge.release.set()
        self.client.__exit__(None, None, None)
        for thread in threading.enumerate():
            if thread.name.startswith("answer-judge"):
                thread.join(timeout=5)

    def _headers(self, identity=None, origin=None, **extra):
        headers = {"Origin": origin or self.github_origin}
        if identity is not None:
            headers["X-Learning-Identity"] = identity
        headers.update(extra)
        return headers

    def _guest(self, origin=None):
        response = self.client.post("/api/access/guest", json={}, headers=self._headers(origin=origin))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["access"]["role"], "guest")
        self.assertEqual(set(result["access"]["features"]), {"learn", "submit_answer", "review_history"})
        self.assertTrue(result["identity_token"])
        return result["identity_token"], result["access"]

    def _state(self, identity):
        response = self.client.get("/api/state", headers=self._headers(identity))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["learner_id"], result["access"]["learner_id"])
        self.assertNotIn("REFERENCE_ONLY", response.text)
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
        self.fail("Public API fixture submission did not finish")

    def _rows(self, query, args=()):
        with closing(sqlite3.connect(self.service.db_path)) as con:
            return con.execute(query, args).fetchall()

    def _assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["detail"]["code"], code)
        self.assertNotIn("REFERENCE_ONLY", response.text)
        self.assertNotIn(self.settings.instance_id, response.text)

    def _assert_rate_error(self, response, code):
        self._assert_error(response, 429, code)
        self.assertTrue(response.headers["Retry-After"].isdigit())
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], self.github_origin)

    def test_configured_public_and_github_origins_bootstrap_guest_without_pairing(self):
        tokens = []
        for origin in (self.github_origin, self.public_origin):
            with self.subTest(origin=origin):
                token, access = self._guest(origin)
                tokens.append(token)
                response = self.client.get("/api/access", headers=self._headers(token, origin))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), access)
                self.assertEqual(self._state(token)["access"], access)
        self.assertNotEqual(tokens[0], tokens[1])
        self.assertFalse((self.data_dir / "pairing.json").exists())
        self.assertEqual(self._rows("SELECT role, COUNT(*) FROM access_principals GROUP BY role"), [("guest", 2)])

    def test_learning_routes_require_valid_identity_after_public_bootstrap(self):
        identity, access = self._guest()
        before = self._state(identity)
        base = self._payload(identity)
        endpoints = [
            ("GET", "/api/access", None), ("GET", "/api/state", None),
            ("GET", "/api/submissions/not-a-submission", None),
            ("POST", "/api/choose", {**base, "choice": "mastered"}),
            ("POST", "/api/continue", base), ("POST", "/api/select", base),
            ("POST", "/api/submit", {**base, "answer": "correct"}),
            ("POST", "/api/pause", {"request_id": str(uuid.uuid4())}),
        ]
        for token, code in [(None, "identity_required"), (identity + "tampered", "invalid_identity"),
                            (access["learner_id"], "invalid_identity")]:
            for method, path, body in endpoints:
                with self.subTest(code=code, path=path):
                    kwargs = {"headers": self._headers(token)}
                    if body is not None:
                        kwargs["json"] = body
                    self._assert_error(self.client.request(method, path, **kwargs), 401, code)
        self.assertEqual(self._state(identity), before)
        self.assertEqual(self.judge.calls, [])

    def test_public_session_never_exposes_local_nonce(self):
        local = self.client.get(self.local_origin + "/api/session", headers={"Origin": self.local_origin})
        self.assertEqual(local.status_code, 200, local.text)
        nonce = local.json()["session_token"]
        identity, _ = self._guest()
        for headers in [
            {}, {"Origin": self.github_origin}, {"Origin": self.public_origin},
            {"Sec-Fetch-Site": "same-origin"},
            self._headers(identity, self.public_origin, **{"X-Learning-Session": nonce}),
        ]:
            with self.subTest(headers=list(headers)):
                response = self.client.get("/api/session", headers=headers)
                self._assert_error(response, 403, "public_session_forbidden")
                self.assertNotIn(nonce, response.text)
                self.assertNotIn("session_token", response.text)

    def test_public_health_is_minimal_while_local_health_keeps_instance_check(self):
        for headers in ({}, self._headers(), self._headers(origin=self.public_origin)):
            response = self.client.get("/api/health", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"status": "ok", "service": "GraspMemoEdu"})
            self.assertNotIn(self.settings.instance_id, response.text)
        local = self.client.get(self.local_origin + "/api/health")
        self.assertEqual(local.status_code, 200, local.text)
        self.assertEqual(local.json()["instance_id"], self.settings.instance_id)
        response = self.client.get("/api/health", headers={"Origin": "https://attacker.invalid"})
        self._assert_error(response, 403, "origin_forbidden")

    def test_deployment_discovery_contains_only_the_public_api_origin(self):
        response = self.client.get("/deployment.json")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"api_origin": self.public_origin})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        local = self.client.get(self.local_origin + "/deployment.json")
        self.assertEqual(local.status_code, 200, local.text)
        self.assertEqual(local.json(), {"api_origin": ""})

    def test_public_learning_rejects_missing_and_nonexact_origins(self):
        identity, _ = self._guest()
        before = self._state(identity)
        bad_origins = [None, "null", "https://attacker.invalid", self.github_origin + ".attacker.invalid",
                       self.github_origin + "/", self.public_origin + ".attacker.invalid",
                       self.public_origin.replace("https:", "http:"), self.local_origin]
        for origin in bad_origins:
            headers = {"X-Learning-Identity": identity}
            if origin is not None:
                headers["Origin"] = origin
            for method, path, body in [("GET", "/api/state", None), ("POST", "/api/access/guest", {})]:
                with self.subTest(origin=origin, path=path):
                    kwargs = {"headers": headers}
                    if body is not None:
                        kwargs["json"] = body
                    response = self.client.request(method, path, **kwargs)
                    self._assert_error(response, 403, "origin_forbidden")
                    self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertEqual(self._state(identity), before)
        self.assertEqual(self._rows("SELECT COUNT(*) FROM access_principals"), [(1,)])

    def test_public_root_same_origin_gets_work_without_an_origin_header(self):
        root = self.client.get("/", headers={"Sec-Fetch-Site": "none"})
        self.assertEqual(root.status_code, 200, root.text)
        self.assertIn("text/html", root.headers["Content-Type"])
        identity, _ = self._guest(self.public_origin)
        before = self._state(identity)
        for referrer in (None, self.public_origin + "/", self.public_origin + "/?view=lesson"):
            headers = {"Sec-Fetch-Site": "same-origin", "X-Learning-Identity": identity}
            if referrer is not None:
                headers["Referer"] = referrer
            with self.subTest(referrer=referrer):
                response = self.client.get("/api/state", headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), before)
                access = self.client.get("/api/access", headers=headers)
                self.assertEqual(access.status_code, 200, access.text)
                self.assertEqual(access.json(), before["access"])
                # The boundary permits HEAD under the same narrow rule. The
                # GET-only route or static fallback can still return 404/405.
                head = self.client.head("/api/state", headers=headers)
                self.assertIn(head.status_code, {200, 404, 405}, head.text)
        response = self.client.get("/api/session", headers={"Sec-Fetch-Site": "same-origin"})
        self._assert_error(response, 403, "public_session_forbidden")

    def test_no_origin_exception_rejects_cross_site_metadata_referrers_and_posts(self):
        identity, _ = self._guest()
        before = self._state(identity)
        denied = [
            {"Sec-Fetch-Site": site, "Referer": self.public_origin + "/"}
            for site in ("cross-site", "same-site", "none")
        ] + [
            {"Sec-Fetch-Site": "same-origin", "Referer": referrer}
            for referrer in ("https://attacker.invalid/", self.github_origin + "/",
                             self.public_origin + ".attacker.invalid/", "not-a-url")
        ]
        for headers in denied:
            headers["X-Learning-Identity"] = identity
            with self.subTest(headers=headers):
                self._assert_error(self.client.get("/api/state", headers=headers), 403, "origin_forbidden")
                self.assertEqual(self.client.head("/api/state", headers=headers).status_code, 403)
        response = self.client.post("/api/access/guest", json={}, headers={
            "Sec-Fetch-Site": "same-origin", "Referer": self.public_origin + "/",
        })
        self._assert_error(response, 403, "origin_forbidden")
        response = self.client.get("/api/state", headers={"Sec-Fetch-Site": "same-origin"})
        self._assert_error(response, 401, "identity_required")
        self.assertEqual(self._state(identity), before)
        self.assertEqual(self._rows("SELECT COUNT(*) FROM access_principals"), [(1,)])

    def test_exact_host_cannot_be_replaced_with_forwarded_headers(self):
        identity, _ = self._guest()
        for host in ["attacker.invalid", self.public_host + ".attacker.invalid", "other.trycloudflare.com",
                     self.public_host + ":443", "127.0.0.1:9999"]:
            with self.subTest(host=host):
                response = self.client.get("/api/state", headers=self._headers(identity, **{
                    "Host": host, "X-Forwarded-Host": self.public_host, "X-Forwarded-Proto": "https",
                    "Forwarded": f"host={self.public_host};proto=https",
                }))
                self._assert_error(response, 400, "invalid_host")
        response = self.client.get("/api/state", headers=self._headers(identity, **{
            "X-Forwarded-Host": "attacker.invalid", "Forwarded": "host=attacker.invalid;proto=http",
        }))
        self.assertEqual(response.status_code, 200, response.text)

    def test_public_mode_does_not_bypass_local_transport_authentication(self):
        identity, _ = self._guest()
        before = self._state(identity)
        for origin in (self.local_origin, self.github_origin):
            response = self.client.get(self.local_origin + "/api/state", headers={
                "Origin": origin, "X-Learning-Identity": identity,
                "X-Forwarded-Host": self.public_host, "X-Forwarded-Proto": "https",
                "Authorization": "Bearer not-a-pairing-credential",
            })
            self._assert_error(response, 401, "authentication_required")
        local = self.client.get(self.local_origin + "/api/session", headers={"Origin": self.local_origin})
        self.assertEqual(local.status_code, 200, local.text)
        response = self.client.get(self.local_origin + "/api/state", headers={
            "Origin": self.local_origin, "X-Learning-Session": local.json()["session_token"],
            "X-Learning-Identity": identity,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), before)

    def test_public_preflight_allows_only_configured_origin_and_known_headers(self):
        response = self.client.options("/api/submit", headers={
            "Origin": self.github_origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-learning-identity",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], self.github_origin)
        self.assertIn("x-learning-identity", response.headers["Access-Control-Allow-Headers"].lower())
        for origin, method, headers, code in [
            ("https://attacker.invalid", "POST", "content-type", "origin_forbidden"),
            (self.github_origin, "DELETE", "content-type", "preflight_forbidden"),
            (self.github_origin, "POST", "x-forwarded-host", "preflight_forbidden"),
        ]:
            with self.subTest(origin=origin, method=method, headers=headers):
                result = self.client.options("/api/submit", headers={
                    "Origin": origin, "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": headers,
                })
                self._assert_error(result, 403, code)

    def test_actual_body_limit_rejects_oversized_and_underreported_streams(self):
        at_limit = b"{}" + b" " * (16384 - 2)
        response = self.client.post("/api/access/guest", content=at_limit,
                                    headers=self._headers(**{"Content-Type": "application/json"}))
        self.assertEqual(response.status_code, 200, response.text)
        before = self._rows("SELECT COUNT(*) FROM access_principals")
        for declared in (None, "2", "16384"):
            with self.subTest(declared=declared):
                headers = self._headers(**{"Content-Type": "application/json"})
                if declared is not None:
                    headers["Content-Length"] = declared
                stream = iter((b"{}", b" " * 8192, b" " * 8191))
                response = self.client.post("/api/access/guest", content=stream, headers=headers)
                self._assert_error(response, 413, "request_too_large")
                self.assertEqual(self._rows("SELECT COUNT(*) FROM access_principals"), before)
        response = self.client.post("/api/access/guest", content=at_limit + b" ",
                                    headers=self._headers(**{"Content-Type": "application/json"}))
        self._assert_error(response, 413, "request_too_large")
        self.assertEqual(self._rows("SELECT COUNT(*) FROM access_principals"), before)

    def test_guest_creation_rate_limit_is_global_and_recovers_after_window(self):
        first = None
        for index in range(20):
            identity, _ = self._guest(self.public_origin if index % 2 else self.github_origin)
            if first is None:
                first = identity
        before = self._state(first)
        response = self.client.post("/api/access/guest", json={}, headers=self._headers())
        self._assert_rate_error(response, "rate_limited")
        self.assertEqual(self._rows("SELECT role, COUNT(*) FROM access_principals GROUP BY role"), [("guest", 20)])
        self.assertEqual(self._state(first), before)
        # Forged client-IP headers do not obtain a separate global quota.
        response = self.client.post("/api/access/guest", json={}, headers=self._headers(**{
            "X-Forwarded-For": "192.0.2.77", "CF-Connecting-IP": "192.0.2.78",
        }))
        self._assert_rate_error(response, "rate_limited")
        self.clock.advance(61)
        self._guest()
        self.assertEqual(self._rows("SELECT COUNT(*) FROM access_principals"), [(21,)])

    def test_submission_rate_limit_is_per_guest_and_rejection_does_not_count_wrong(self):
        first, _ = self._guest()
        second, _ = self._guest()
        self._mutate("choose", first, choice="mastered")
        self._mutate("choose", second, choice="mastered")
        for _ in range(12):
            submitted = self._mutate("submit", first, answer="service-error")
            result = self._wait_for_submission(first, submitted["submission_id"])
            self.assertEqual(result["status"], "error")
            self.assertIsNone(result["correct"])
        before = self._state(first)
        payload = self._payload(first, answer="correct")
        response = self.client.post("/api/submit", json=payload, headers=self._headers(first))
        self._assert_rate_error(response, "rate_limited")
        self.assertEqual(self._state(first), before)
        self.assertEqual(before["modules"][0]["practice_error_count"], 0)
        self.assertIsNone(before["modules"][0]["mastery"])
        self.assertEqual(self._rows("SELECT COUNT(*) FROM submissions WHERE id=?", (payload["request_id"],)), [(0,)])
        other = self._mutate("submit", second, answer="correct")
        self.assertTrue(self._wait_for_submission(second, other["submission_id"])["correct"])
        self.clock.advance(61)
        retried = self.client.post("/api/submit", json=payload, headers=self._headers(first))
        self.assertEqual(retried.status_code, 200, retried.text)
        result = self._wait_for_submission(first, retried.json()["submission_id"])
        self.assertTrue(result["correct"])
        self.assertEqual(result["state"]["modules"][0]["mastery"], 4)

    def test_daily_global_judge_quota_blocks_new_guest_without_changing_progress(self):
        self.app.state.public_limits.daily.global_limit = 1
        first, _ = self._guest()
        second, _ = self._guest()
        self._mutate("choose", first, choice="mastered")
        self._mutate("choose", second, choice="mastered")
        used = self._mutate("submit", first, answer="service-error")
        self.assertEqual(self._wait_for_submission(first, used["submission_id"])["status"], "error")
        before = self._state(second)
        payload = self._payload(second, answer="correct")
        response = self.client.post("/api/submit", json=payload, headers=self._headers(second))
        self._assert_rate_error(response, "daily_judge_quota")
        self.assertEqual(self._state(second), before)
        self.assertEqual(self._rows("SELECT COUNT(*) FROM submissions WHERE id=?", (payload["request_id"],)), [(0,)])

    def test_eight_pending_judgements_are_enforced_under_parallel_requests(self):
        identities, before, payloads = [], {}, {}
        for _ in range(10):
            identity, _ = self._guest()
            identities.append(identity)
            self._mutate("choose", identity, choice="mastered")
            before[identity] = self._state(identity)
            payloads[identity] = self._payload(identity, answer="blocked-correct")
        barrier = threading.Barrier(10)

        def submit(identity):
            barrier.wait(timeout=10)
            return identity, self.client.post("/api/submit", json=payloads[identity], headers=self._headers(identity))

        accepted, rejected = [], []
        try:
            with ThreadPoolExecutor(max_workers=10) as pool:
                responses = list(pool.map(submit, identities))
            for identity, response in responses:
                if response.status_code == 200:
                    self.assertEqual(response.json()["status"], "pending")
                    accepted.append((identity, response.json()["submission_id"]))
                else:
                    self._assert_rate_error(response, "judge_capacity")
                    self.assertEqual(self._state(identity), before[identity])
                    self.assertEqual(self._rows("SELECT COUNT(*) FROM submissions WHERE id=?",
                                               (payloads[identity]["request_id"],)), [(0,)])
                    rejected.append(identity)
            self.assertEqual(len(accepted), 8)
            self.assertEqual(len(rejected), 2)
            self.assertTrue(self.judge.started.wait(2))
            self.assertEqual(self._rows("SELECT COUNT(*) FROM submissions WHERE status IN ('pending','running')"), [(8,)])
            identity, submission_id = accepted[0]
            pending = self._state(identity)
            duplicate = self.client.post("/api/submit", json=payloads[identity], headers=self._headers(identity))
            self.assertEqual(duplicate.status_code, 200, duplicate.text)
            self.assertEqual(duplicate.json()["submission_id"], submission_id)
            self.assertEqual(self._state(identity), pending)
            self.assertEqual(self._rows("SELECT COUNT(*) FROM submissions"), [(8,)])
        finally:
            self.judge.release.set()
        for identity, submission_id in accepted:
            self.assertTrue(self._wait_for_submission(identity, submission_id)["correct"])
        self.assertEqual(len(self.judge.calls), 8)
        identity = rejected[0]
        retried = self.client.post("/api/submit", json=payloads[identity], headers=self._headers(identity))
        self.assertEqual(retried.status_code, 200, retried.text)
        result = self._wait_for_submission(identity, retried.json()["submission_id"])
        self.assertTrue(result["correct"])
        self.assertEqual(result["state"]["modules"][0]["practice_error_count"], 0)
        self.assertEqual(len(self.judge.calls), 9)

    def test_public_guests_cannot_read_or_mutate_each_others_progress(self):
        first, _ = self._guest()
        second, _ = self._guest()
        second_before = self._state(second)
        self._mutate("choose", first, choice="mastered")
        submitted = self._mutate("submit", first, answer="correct")
        result = self._wait_for_submission(first, submitted["submission_id"])
        self.assertTrue(result["correct"])
        first_before = self._state(first)
        response = self.client.get(f"/api/submissions/{submitted['submission_id']}", headers=self._headers(second))
        self._assert_error(response, 404, "unknown_submission")
        response = self.client.post("/api/continue", json=self._payload(first), headers=self._headers(second))
        self.assertIn(response.status_code, {403, 409}, response.text)
        self.assertEqual(self._state(second), second_before)
        self.assertEqual(self._state(first), first_before)
        self._mutate("choose", second, choice="mastered")
        second_before = self._state(second)
        response = self.client.post("/api/submit", json=self._payload(
            second, answer="wrong", request_id=submitted["submission_id"]), headers=self._headers(second))
        self._assert_error(response, 409, "idempotency_conflict")
        self.assertEqual(self._state(second), second_before)
        self.assertEqual(self._state(first), first_before)
        self.assertEqual(len(self.judge.calls), 1)

    def test_error_retry_is_scheduled_while_previous_worker_is_finishing(self):
        identity, access = self._guest()
        self._mutate("choose", identity, choice="mastered")
        learner = self.app.state.learning_services[access["learner_id"]]
        original_flush = learner.flush_logs
        error_saved = threading.Event()
        release_flush = threading.Event()

        def flush_after_error():
            if threading.current_thread().name.startswith("answer-judge") and not error_saved.is_set():
                error_saved.set()
                if not release_flush.wait(10):
                    raise RuntimeError("Public retry fixture flush was not released")
            return original_flush()

        learner.flush_logs = flush_after_error
        payload = self._payload(identity, answer="recoverable-error")
        try:
            submitted = self.client.post("/api/submit", json=payload, headers=self._headers(identity))
            self.assertEqual(submitted.status_code, 200, submitted.text)
            submission_id = submitted.json()["submission_id"]
            self.assertTrue(error_saved.wait(2))
            failed = self._wait_for_submission(identity, submission_id)
            self.assertEqual(failed["status"], "error")
            self.assertIsNone(failed["state"]["pending_submission_id"])
            # The completed error is already visible over HTTP, so its Retry
            # action is valid even while the old worker is still writing logs.
            retried = self.client.post("/api/submit", json=payload, headers=self._headers(identity))
            self.assertEqual(retried.status_code, 200, retried.text)
            self.assertEqual(retried.json()["submission_id"], submission_id)
        finally:
            learner.flush_logs = original_flush
            release_flush.set()
        result = self._wait_for_submission(identity, submission_id)
        self.assertEqual(result["status"], "judged")
        self.assertTrue(result["correct"])
        self.assertEqual(result["state"]["modules"][0]["mastery"], 4)
        self.assertEqual(result["state"]["modules"][0]["practice_error_count"], 0)
        self.assertEqual(len(self.judge.calls), 2)

    def test_public_guest_cannot_manually_pause_even_while_judge_is_running(self):
        identity, _ = self._guest()
        self._mutate("choose", identity, choice="mastered")
        submitted = self._mutate("submit", identity, answer="blocked-correct")
        try:
            self.assertTrue(self.judge.started.wait(2))
            before = self._state(identity)
            response = self.client.post("/api/pause", json={"request_id": str(uuid.uuid4())},
                                        headers=self._headers(identity))
            self._assert_error(response, 403, "feature_forbidden")
            self.assertEqual(response.json()["detail"]["message"], "该功能需注册账号才能使用")
            self.assertEqual(self._state(identity), before)
        finally:
            self.judge.release.set()
        result = self._wait_for_submission(identity, submitted["submission_id"])
        self.assertTrue(result["correct"])
        self.assertEqual(result["state"]["status"], "in_progress")

    def test_graphs_are_read_only_public_routes_with_identity_and_subject_checks(self):
        identity, _ = self._guest()
        headers = self._headers(identity)
        response = self.client.get('/api/graphs?subject_id=math', headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.get('/api/graphs/arithmetic-revisited?subject_id=math', headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['nodes'][0]['topic_id'], self.service.topic_id)
        self.assertEqual(self.client.get('/api/graphs?subject_id=physics', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/api/graphs', headers=self._headers()).status_code, 401)
        for path in ('/api/graphs', '/api/graphs/arithmetic-revisited'):
            self._assert_error(self.client.post(path, json={}, headers=headers), 403, 'public_endpoint_forbidden')
        self.assertNotIn('REFERENCE_ONLY', response.text)

    def test_browser_exposes_no_administration_routes_or_private_files(self):
        # Inspect route registration without invoking any account-login or
        # account-creation behavior, even against the temporary fixture.
        allowed = {"", "/", "/deployment.json", "/api/health", "/api/session", "/api/access", "/api/access/guest",
                   "/api/access/login", "/api/access/logout", "/api/state", "/api/demo-answer", "/api/guest/reset", "/api/choose", "/api/continue",
                   "/api/select", "/api/pause", "/api/submit", "/api/submissions/{submission_id}",
                   "/api/subjects", "/api/catalog", "/api/catalog/select", "/api/dashboard", "/api/profile", "/api/feedback",
                   "/api/atomic/reviews", "/api/help-requests", "/api/admin/pauses", "/api/admin/pauses/unlock",
                   "/api/admin/students", "/api/admin/students/{learner_id}",
                   "/api/materials", "/api/materials/open",
                   "/api/graphs", "/api/graphs/{course_id}",
                   "/api/tasks/{task_id}/answers", "/api/guide", "/guide-assets/{relative:path}",
                   "/subject-guide-assets/{subject_id}/{relative:path}",
                   "/api/review/begin", "/api/review/sessions/{session_id}",
                   "/api/review/sessions/{session_id}/submit", "/api/review/sessions/{session_id}/continue",
                   "/api/review/sessions/{session_id}/pause", "/api/review/sessions/{session_id}/demo-answer",
                   "/api/review/submissions/{submission_id}"}
        allowed.update('/api/training/' + name for name in ('catalog', 'bank', 'group', 'open', 'state', 'submit',
                       'submission', 'reveal', 'rate', 'history', 'reviews', 'preferences', 'stop'))
        self.assertLessEqual({route.path for route in self.app.routes}, allowed)
        atomic_route = next(route for route in self.app.routes if route.path == '/api/atomic/reviews')
        self.assertEqual(atomic_route.methods, {'GET'})
        identity, _ = self._guest()
        before = self._state(identity)
        # These two explicitly authorized admin routes remain inaccessible to learners.
        self._assert_error(self.client.get('/api/admin/pauses', headers=self._headers(identity)), 403, 'feature_forbidden')
        self._assert_error(self.client.post('/api/admin/pauses/unlock', json={}, headers=self._headers(identity)), 403, 'feature_forbidden')
        self._assert_error(self.client.get('/api/admin/students', headers=self._headers(identity)), 403, 'feature_forbidden')
        self._assert_error(self.client.get('/api/admin/students/someone', headers=self._headers(identity)), 403, 'feature_forbidden')
        self._assert_error(self.client.get('/api/atomic/reviews', headers=self._headers()), 401, 'identity_required')
        self.assertEqual(self.client.get('/api/atomic/reviews', headers=self._headers(identity)).status_code, 200)
        self.assertEqual(self.client.post('/api/atomic/reviews', json={}, headers=self._headers(identity)).status_code, 405)
        for path in ["/api/admin/accounts", "/api/unlock", "/api/reset", "/api/access/permissions",
                     "/api/graphs/admin/import", "/learning.sqlite3", "/settings.json", "/pairing.json", "/.env",
                     "/graphs/rigid-constraints.json"]:
            with self.subTest(path=path):
                response = self.client.get(path, headers=self._headers(identity))
                if path.startswith("/api/"):
                    self._assert_error(response, 403, "public_endpoint_forbidden")
                else:
                    self.assertIn(response.status_code, {404, 405}, response.text)
                self.assertNotIn("REFERENCE_ONLY", response.text)
                self.assertNotIn(self.settings.instance_id, response.text)
        self.assertEqual(self._state(identity), before)
        self.assertEqual(self._rows("SELECT role, COUNT(*) FROM access_principals GROUP BY role"), [("guest", 1)])


if __name__ == "__main__":
    unittest.main()
