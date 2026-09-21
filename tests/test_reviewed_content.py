"""Generic importer checks use isolated synthetic physics prose, no API calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.verify_content import collect_questions, parse_outline, sha256, verify


ROOT = Path(__file__).resolve().parents[1]


class ReviewedContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        lines = ["- Synthetic physics", "  - Introduction", "    - Speed measures distance per time."]
        for module, count in [(1, 7), (2, 8)]:
            lines += [f"  - Example {module}: Speed", "    - Define speed.", "    - Explanation", "      - Speed is distance divided by time."]
            for number in range(1, count + 1):
                lines += [f"    - Practice {number}", "      - Name the unit of speed.", "      - Explanation", "        - The SI unit is metres per second."]
        self.source = self.root / "source.md"
        self.source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.questions = collect_questions(parse_outline(self.source.read_text(encoding="utf-8")))
        self.sidecar = self.root / "answers.json"
        self.metadata = {"course_id": "synthetic-physics", "source_sha256": sha256(self.source.read_bytes()),
                         "expected_module_count": 2, "expected_question_count": 17,
                         "expected_practices_per_module": None, "errata": [],
                         "answers": {q["id"]: {"question_sha256": sha256(q["question"]),
                                    "reference_answer": "distance divided by time" if q["id"].endswith("question") else "m/s",
                                    "answer_type": "text", "constraints": "Equivalent words and SI notation accepted."}
                                     for q in self.questions}}
        self.review = self.root / "review.json"
        self.save_sidecar()
        self.receipt = {"schema_version": 1, "status": "approved", "source_sha256": self.metadata["source_sha256"],
                        "sidecar_sha256": sha256(self.sidecar.read_bytes()), "model": "test-fixture",
                        "questions": [{"id": q["id"], "question_sha256": sha256(q["question"]),
                                       "approved": True, "issues": []} for q in self.questions]}
        self.save_review()

    def save_sidecar(self):
        self.sidecar.write_text(json.dumps(self.metadata), encoding="utf-8")

    def save_review(self):
        self.review.write_text(json.dumps(self.receipt), encoding="utf-8")

    def audit(self):
        return verify(self.source, self.sidecar, review_path=self.review)

    def test_generic_prose_with_variable_practice_counts(self):
        report = self.audit()
        self.assertEqual(report["verified_answer_count"], 17)
        self.assertEqual(report["method"], "llm-reviewed")
        self.assertNotIn("independent_exact_result", report["records"][0])

    def test_mechanical_mapping_is_exact_without_claiming_semantic_review(self):
        for question in self.questions:
            self.metadata['answers'][question['id']]['reference_answer'] = question['explanation']
        self.save_sidecar()
        self.receipt.update(mode='mechanical', method='mechanical-import', status='mechanically_validated',
                            sidecar_sha256=sha256(self.sidecar.read_bytes()), model=None)
        self.receipt['questions'] = [{'id': q['id'], 'question_sha256': sha256(q['question']),
                                     'structurally_valid': True, 'field_mapping': 'reference_answer=explanation'}
                                    for q in self.questions]
        self.save_review()
        report = self.audit()
        self.assertEqual(report['method'], 'mechanical-import')
        self.assertEqual(report['verified_answer_count'], 0)
        self.assertEqual(report['mechanically_mapped_answer_count'], 17)
        self.assertFalse(report['semantic_review_performed'])
        self.metadata['answers'][self.questions[0]['id']]['reference_answer'] = 'A silently rewritten explanation.'
        self.save_sidecar()
        self.receipt['sidecar_sha256'] = sha256(self.sidecar.read_bytes())
        self.save_review()
        with self.assertRaisesRegex(ValueError, 'mapping mismatch'):
            self.audit()

    def test_mechanical_receipt_cannot_omit_or_relabel_a_question(self):
        self.receipt.update(mode='mechanical', method='mechanical-import', status='mechanically_validated')
        self.receipt['questions'].pop()
        self.save_review()
        with self.assertRaisesRegex(ValueError, 'uniquely cover'):
            self.audit()

    def test_rejected_incomplete_duplicate_and_issue_reviews_fail(self):
        original = json.loads(json.dumps(self.receipt))
        for mutation in [lambda r: r.update(status="rejected"),
                         lambda r: r["questions"].pop(),
                         lambda r: r["questions"].append(r["questions"][0]),
                         lambda r: r["questions"][0].update(approved=False),
                         lambda r: r["questions"][0].update(issues=["Ambiguous"]),
                         lambda r: r["questions"][0].update(question_sha256="0" * 64)]:
            self.receipt = json.loads(json.dumps(original))
            mutation(self.receipt)
            self.save_review()
            with self.assertRaises(ValueError):
                self.audit()

    def test_review_binds_sidecar_bytes_even_for_whitespace_change(self):
        self.sidecar.write_bytes(self.sidecar.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "review hash mismatch"):
            self.audit()

    def test_missing_review_does_not_enable_generic_mode(self):
        with self.assertRaises(ValueError):
            verify(self.source, self.sidecar)
        with self.assertRaises(FileNotFoundError):
            verify(self.source, self.sidecar, review_path=self.root / "absent.json")

    def test_missing_metadata_and_wrong_module_count_fail(self):
        self.metadata["answers"][self.questions[0]["id"]]["constraints"] = " "
        self.save_sidecar()
        self.receipt["sidecar_sha256"] = sha256(self.sidecar.read_bytes())
        self.save_review()
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.audit()
        self.metadata["expected_module_count"] = 3
        self.save_sidecar()
        with self.assertRaisesRegex(ValueError, "Module count"):
            self.audit()

    def test_less_than_seven_practices_is_rejected(self):
        text = self.source.read_text(encoding="utf-8")
        start = text.index("    - Practice 7")
        end = text.index("  - Example 2")
        self.source.write_text(text[:start] + text[end:], encoding="utf-8")
        self.metadata["source_sha256"] = sha256(self.source.read_bytes())
        self.metadata["expected_question_count"] = 16
        self.metadata["answers"].pop("example-1-practice-7")
        self.save_sidecar()
        with self.assertRaisesRegex(ValueError, "at least seven"):
            self.audit()

    def test_inspection_and_rendering_preserve_fields_and_source(self):
        script = ROOT / "scripts/import_course.mjs"
        inspected = subprocess.run(["node", str(script), "--inspect", str(self.source)],
                                   capture_output=True, text=True, encoding="utf-8", check=True)
        document = json.loads(inspected.stdout)
        self.assertEqual(document["source_sha256"], self.metadata["source_sha256"])
        self.assertEqual(len(document["modules"][1]["question_ids"]), 9)
        self.assertEqual(document["questions"][0]["explanation"], self.questions[0]["explanation"])
        output, audit = self.root / "course.json", self.root / "audit.json"
        import sys
        result = subprocess.run(["node", str(script), str(self.source), str(self.sidecar), str(output), str(audit), str(self.review)],
                                capture_output=True, text=True, encoding="utf-8",
                                env={**os.environ, "MATH_LEARNING_PYTHON": sys.executable})
        self.assertEqual(result.returncode, 0, result.stderr)
        course = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(course["id"], "synthetic-physics")
        self.assertEqual([len(m["practices"]) for m in course["modules"]], [7, 8])
        self.assertEqual(course["modules"][1]["practices"][0]["reference_answer"], "m/s")
        self.assertIn("metres per second", course["modules"][1]["practices"][0]["explanation_html"])
        self.assertEqual(sha256(self.source.read_bytes()), self.metadata["source_sha256"])
        self.assertEqual(json.loads(audit.read_text())["method"], "llm-reviewed")


if __name__ == "__main__":
    unittest.main()
