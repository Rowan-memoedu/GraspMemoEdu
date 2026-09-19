"""Small synthetic course and predictable judge, unrelated to production content."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def synthetic_course(module_count: int = 3) -> dict:
    def question(question_id: str, label: str) -> dict:
        return {
            "id": question_id,
            "title": label,
            "question": f"Synthetic exercise {question_id}: calculate 2 + 3.",
            "html": f"<p>QUESTION::{question_id} — calculate 2 + 3.</p>",
            "explanation_html": f"<p>EXPLANATION::{question_id} — add the quantities.</p>",
            "reference_answer": f"REFERENCE_ONLY::{question_id}",
            "answer_type": "expression",
            "constraints": "Return one expression.",
        }

    course = {
        "id": "synthetic-course",
        "title": "Synthetic acceptance course",
        "source_sha256": "d" * 64,
        "introduction": {
            "id": "synthetic-introduction",
            "title": "Introduction",
            "html": "<h3>Introduction</h3><p>INTRODUCTION_ONLY::read once</p>",
        },
        "modules": [],
    }
    for index in range(1, module_count + 1):
        module_id = f"module-{index}"
        course["modules"].append({
            "id": module_id,
            "title": f"Example {index}",
            "example": question(f"{module_id}-example", f"Example {index}"),
            "practices": [
                question(f"{module_id}-practice-{item}", f"Practice {item}")
                for item in range(1, 16)
            ],
        })
    course["version"] = hashlib.sha256(
        json.dumps(course, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return course


class DeterministicJudge:
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, question: str, reference: str, answer: str) -> dict:
        self.calls.append((question, reference, answer))
        if answer == "service-error":
            return {"status": "error", "correct": None, "reason": "Synthetic unavailable judge"}
        if answer == "raise-error":
            raise RuntimeError("Synthetic transport failure")
        return {"status": "judged", "correct": answer == "correct", "reason": "Synthetic judgement"}


def temporary_test_directory():
    # The Windows development host keeps disposable run data on D:.
    root = os.environ.get("MLW_TEST_TEMP_ROOT")
    if root is None and os.name == "nt" and Path("D:/").exists():
        root = "D:/Artifacts/MathLearningWeb/test-runs"
    if root:
        Path(root).mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="math-learning-", dir=root)
