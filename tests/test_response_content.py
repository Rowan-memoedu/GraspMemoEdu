import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.response_content import pack_content
from graspmemoedu.service import LearningService
from tests.fixtures import synthetic_course, DeterministicJudge, temporary_test_directory


def restore(value, texts):
    if isinstance(value, dict):
        if set(value) == {"$gme_text"}:
            return texts[value["$gme_text"]]
        return {key: restore(child, texts) for key, child in value.items()}
    if isinstance(value, list):
        return [restore(child, texts) for child in value]
    return value


class ResponseContentTests(unittest.TestCase):
    def test_roundtrip_content_changes_and_no_early_answer(self):
        raw = {"steps": [{"html": "<p>question</p>" * 50, "explanation_html": None}],
               "answer": "learner answer", "server_time": "now", "revision": 1}
        first = pack_content(raw, "1")
        self.assertEqual(restore(first["state"], first["texts"]), raw)
        changed_progress = {**raw, "server_time": "later", "revision": 2}
        delta = pack_content(changed_progress, first["revision"])
        self.assertIsNone(delta["texts"])
        self.assertEqual(restore(delta["state"], first["texts"]), changed_progress)
        self.assertNotIn("learner answer", first["texts"])
        revealed = {**raw, "steps": [{"html": raw["steps"][0]["html"], "explanation_html": "revealed" * 50}]}
        second = pack_content(revealed, first["revision"])
        self.assertNotEqual(second["revision"], first["revision"])
        self.assertEqual(second["texts"][0], {"$gme_previous": 0})
        self.assertEqual(second["texts"][1], "revealed" * 50)
        new_texts = [first["texts"][v["$gme_previous"]] if isinstance(v, dict) else v for v in second["texts"]]
        self.assertEqual(restore(second["state"], new_texts), revealed)
        hidden = pack_content(raw, second["revision"])
        hidden_texts = [new_texts[v["$gme_previous"]] if isinstance(v, dict) else v for v in hidden["texts"]]
        self.assertEqual(restore(hidden["state"], hidden_texts), raw)

    def test_live_endpoint_rechecks_identity_and_preserves_old_client_contract(self):
        with temporary_test_directory() as folder:
            course = synthetic_course()
            course["introduction"]["html"] = "<p>large authorized introduction</p>" * 4000
            course["modules"][0]["example"]["html"] = "<p>large authorized question</p>" * 4000
            settings = Settings(data_dir=Path(folder), public_api_origin="https://cache.test")
            app = create_app(settings, LearningService(course, Path(folder), DeterministicJudge()))
            with TestClient(app, base_url="https://cache.test") as client:
                headers = {"Origin": "https://cache.test"}
                guest = client.post("/api/access/guest", headers=headers, json={}).json()
                headers["X-Learning-Identity"] = guest["identity_token"]
                first = client.get("/api/state?content_revision=1", headers=headers)
                self.assertEqual(first.status_code, 200, first.text)
                packed = first.json()
                self.assertEqual(packed["$gme_content"], 1)
                path = "/api/state?content_revision=" + packed["revision"]
                second = client.get(path, headers=headers)
                self.assertIsNone(second.json()["texts"])
                self.assertLess(len(second.content), len(first.content) / 10)
                self.assertEqual(second.headers["cache-control"], "no-store")
                plain = client.get("/api/state", headers=headers).json()
                rebuilt = restore(second.json()["state"], packed["texts"])
                plain.pop("server_time", None); rebuilt.pop("server_time", None)
                self.assertEqual(plain, rebuilt)
                denied = client.get(path, headers={"Origin": "https://cache.test"})
                self.assertEqual(denied.status_code, 401)
                self.assertNotIn("$gme_content", denied.text)
                payload = {"request_id": "compact-choice", "expected_revision": rebuilt["revision"],
                           "attempt_id": rebuilt["attempt_id"], "step_id": rebuilt["active_step_id"],
                           "choice": "unmastered"}
                changed = client.post("/api/choose?content_revision=" + packed["revision"],
                                      headers=headers, json=payload)
                self.assertEqual(changed.status_code, 200, changed.text)
                delta = changed.json()
                self.assertEqual(delta["$gme_content"], 1)
                texts = packed["texts"] if delta["texts"] is None else [
                    packed["texts"][text["$gme_previous"]] if isinstance(text, dict) else text
                    for text in delta["texts"]]
                updated = restore(delta["state"], texts)
                self.assertGreater(updated["revision"], rebuilt["revision"])
                retried = client.post("/api/choose", headers=headers, json=payload).json()
                self.assertEqual(retried["revision"], updated["revision"])
                account = app.state.access_store.create_account("Cache authorization test")
                auth = client.post("/api/access/login", headers={"Origin": "https://cache.test"},
                                   json={"invitation_code": account["invitation_code"]}).json()
                restricted_headers = {**headers, "X-Learning-Identity": auth["identity_token"]}
                initial = client.get("/api/state?content_revision=1", headers=restricted_headers)
                self.assertEqual(initial.status_code, 200)
                app.state.access_store.set_permissions(auth["access"]["learner_id"], topic_ids=[])
                denied = client.get("/api/state?content_revision=" + initial.json()["revision"],
                                    headers=restricted_headers)
                self.assertEqual(denied.status_code, 403, denied.text)
                self.assertNotIn("large authorized", denied.text)


if __name__ == "__main__":
    unittest.main()
