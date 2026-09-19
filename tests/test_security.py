"""HTTP boundary and API acceptance with no network or real pairing credentials."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from math_learning.api import create_app
from math_learning.config import Settings
from math_learning.service import LearningService

from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class APISecurityAcceptanceTests(unittest.TestCase):
    origin = "http://127.0.0.1:8765"
    allowed_origin = "https://synthetic-course.github.io"
    credential = "SYNTHETIC_PAIRING_CREDENTIAL_ONLY"

    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.service = LearningService(synthetic_course(), self.data_dir, DeterministicJudge())
        salt = "synthetic-acceptance-salt"
        (self.data_dir / "pairing.json").write_text(json.dumps({
            "salt": salt,
            "digest": hashlib.sha256((salt + self.credential).encode()).hexdigest(),
        }), encoding="utf-8")
        self.settings = Settings(data_dir=self.data_dir, port=8765, allowed_origins=(self.allowed_origin,))
        self.app = create_app(self.settings, self.service)
        self.client = TestClient(self.app, base_url=self.origin)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        response = self.client.get("/api/session", headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 200)
        self.token = response.json()["session_token"]
        response = self.client.post("/api/access/guest", json={}, headers={
            "Origin": self.origin, "X-Learning-Session": self.token,
        })
        self.assertEqual(response.status_code, 200)
        self.identity_token = response.json()["identity_token"]

    @property
    def local_headers(self):
        return {"Origin": self.origin, "X-Learning-Session": self.token,
                "X-Learning-Identity": self.identity_token}

    def state(self):
        response = self.client.get("/api/state", headers=self.local_headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def payload(self, **extra):
        state = self.state()
        data = {
            "request_id": str(uuid.uuid4()),
            "expected_revision": state["revision"],
            "attempt_id": state["attempt_id"],
            "step_id": state["active_step_id"],
        }
        data.update(extra)
        return data

    def test_bootstrap_requires_exact_same_origin_or_fetch_metadata(self):
        for headers in [
            {},
            {"Origin": "https://attacker.invalid"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Origin": "http://localhost:8765"},
            {"Origin": self.allowed_origin, "Authorization": f"Bearer {self.credential}"},
        ]:
            with self.subTest(headers=headers):
                response = self.client.get("/api/session", headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("session_token", response.text)
        metadata = self.client.get("/api/session", headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(metadata.json()["session_token"], self.token)
        self.assertGreaterEqual(len(self.token), 32)

    def test_local_learning_requires_valid_nonce(self):
        for token in [None, "wrong-session"]:
            headers = {"Origin": self.origin}
            if token is not None:
                headers["X-Learning-Session"] = token
            response = self.client.get("/api/state", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("QUESTION::", response.text)
        self.assertEqual(self.state()["topic_id"], "synthetic-course")

    def test_host_rebinding_is_rejected_even_with_valid_credentials(self):
        for hostname in [
            "attacker.invalid:8765", "127.0.0.1.attacker.invalid:8765", "127.0.0.1:80",
            "127.0.0.1", "0.0.0.0:8765",
        ]:
            with self.subTest(host=hostname):
                response = self.client.get("/api/state", headers={**self.local_headers, "Host": hostname})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"]["code"], "invalid_host")

    def test_disallowed_origin_is_rejected_before_nonce_or_pairing(self):
        for credentials in [
            {"X-Learning-Session": self.token},
            {"Authorization": f"Bearer {self.credential}"},
        ]:
            response = self.client.get("/api/state", headers={"Origin": "https://attacker.invalid", **credentials})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"]["code"], "origin_forbidden")
            self.assertNotIn("access-control-allow-origin", response.headers)

    def test_allowed_remote_origin_requires_pairing_and_never_receives_credentials(self):
        for credentials in [
            {}, {"X-Learning-Session": self.token}, {"Authorization": "Bearer wrong"},
        ]:
            response = self.client.get("/api/state", headers={"Origin": self.allowed_origin, **credentials})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers.get("access-control-allow-origin"), self.allowed_origin,
                             "An allowed frontend must be able to read an authentication error")
            self.assertNotIn(self.credential, response.text)
            self.assertNotIn(self.token, response.text)
        response = self.client.get("/api/state", headers={
            "Origin": self.allowed_origin, "Authorization": f"Bearer {self.credential}",
            "X-Learning-Identity": self.identity_token,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], self.allowed_origin)
        self.assertNotIn(self.credential, response.text)
        self.assertNotIn(self.token, response.text)
        self.assertNotIn("REFERENCE_ONLY", response.text)

    def test_cors_preflight_is_scoped_to_configured_origin_and_methods(self):
        headers = {
            "Origin": self.allowed_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
            "Access-Control-Request-Private-Network": "true",
        }
        allowed = self.client.options("/api/submit", headers=headers)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers["access-control-allow-origin"], self.allowed_origin)
        self.assertEqual(allowed.headers["access-control-allow-private-network"], "true")
        for changed in [
            {"Origin": "https://attacker.invalid"},
            {"Access-Control-Request-Method": "DELETE"},
            {"Access-Control-Request-Headers": "x-unapproved-header"},
        ]:
            response = self.client.options("/api/submit", headers={**headers, **changed})
            self.assertEqual(response.status_code, 403)

    def test_json_and_body_size_boundary_apply_before_mutation(self):
        baseline = self.state()
        response = self.client.post("/api/pause", headers=self.local_headers, content="request_id=test")
        self.assertEqual(response.status_code, 415)
        response = self.client.post("/api/pause", headers={
            **self.local_headers, "Content-Type": "application/json",
        }, content='{"request_id":"' + "x" * 17000 + '"}')
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.state(), baseline)

    def test_unknown_client_correctness_and_reference_fields_are_rejected(self):
        choose = self.client.post("/api/choose", json=self.payload(choice="mastered"), headers=self.local_headers)
        self.assertEqual(choose.status_code, 200)
        baseline = self.state()
        response = self.client.post("/api/submit", json=self.payload(
            answer="wrong", correct=True, reference_answer="attacker answer",
        ), headers=self.local_headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.state(), baseline)

    def test_validation_errors_follow_documented_error_shape(self):
        response = self.client.post("/api/choose", json={"request_id": "missing-action-fields"}, headers=self.local_headers)
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, dict)
        self.assertIsInstance(detail.get("code"), str)
        self.assertIsInstance(detail.get("message"), str)

    def test_stale_revision_is_structured_conflict_and_does_not_advance(self):
        request = self.payload(choice="unmastered")
        response = self.client.post("/api/choose", json=request, headers=self.local_headers)
        self.assertEqual(response.status_code, 200)
        stale = {key: value for key, value in request.items() if key != "choice"}
        stale["request_id"] = str(uuid.uuid4())
        response = self.client.post("/api/continue", json=stale, headers=self.local_headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "stale_revision")

    def test_http_has_no_unlock_endpoint(self):
        response = self.client.post("/api/pause", json={"request_id": str(uuid.uuid4())}, headers=self.local_headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["message"], "该功能需注册账号才能使用")
        baseline = self.state()
        for path in ["/api/unlock", "/api/admin/unlock", "/api/resume"]:
            with self.subTest(path=path):
                response = self.client.post(path, json={"request_id": str(uuid.uuid4())}, headers=self.local_headers)
                self.assertIn(response.status_code, {404, 405})
        self.assertEqual(self.state(), baseline)

    def test_api_judging_keeps_guest_pause_denial_responsive(self):
        started, release = threading.Event(), threading.Event()

        def blocking_judge(*args):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Synthetic judge timed out waiting for release")
            return {"status": "judged", "correct": True, "reason": "Synthetic late verdict"}

        self.service.judge = blocking_judge
        response = self.client.post("/api/choose", json=self.payload(choice="mastered"), headers=self.local_headers)
        self.assertEqual(response.status_code, 200)
        try:
            response = self.client.post("/api/submit", json=self.payload(answer="correct"), headers=self.local_headers)
            self.assertEqual(response.status_code, 200)
            submitted = response.json()
            self.assertEqual(submitted["status"], "pending")
            self.assertTrue(started.wait(2))
            self.assertEqual(self.state()["pending_submission_id"], submitted["submission_id"])
            began = time.monotonic()
            response = self.client.post("/api/pause", json={"request_id": str(uuid.uuid4())}, headers=self.local_headers)
            self.assertLess(time.monotonic() - began, 2)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"]["message"], "该功能需注册账号才能使用")
            self.assertEqual(self.state()["pending_submission_id"], submitted["submission_id"])
        finally:
            release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = self.client.get(f"/api/submissions/{submitted['submission_id']}", headers=self.local_headers).json()
            if result["status"] != "pending":
                break
            time.sleep(0.02)
        self.assertEqual(result["status"], "judged")
        self.assertEqual(self.state()["status"], "in_progress")
        self.assertEqual(self.state()["modules"][0]["mastery"], 4)

    def test_static_page_has_security_headers_and_no_course_or_pairing_secrets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertNotIn(self.credential, response.text)
        self.assertNotIn(self.token, response.text)
        self.assertNotIn("QUESTION::", response.text)
        self.assertNotIn("REFERENCE_ONLY", response.text)
        for path in ["/pairing.json", "/learning.sqlite3", "/logs/events.jsonl", "/courses/digits-to-letters.json"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_allowed_origins_cannot_be_wildcard_non_https_or_path(self):
        for origin in ["*", "http://attacker.invalid", "https://attacker.invalid/path", "https://*.invalid", "https://user:secret@attacker.invalid"]:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                Settings(data_dir=self.data_dir, allowed_origins=(origin,))


if __name__ == "__main__":
    unittest.main()
