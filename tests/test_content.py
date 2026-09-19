"""Arithmetic and import-contract tests use synthetic, public arithmetic only."""
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest

from scripts.verify_content import ExactTex, collect_questions, parse_outline, sha256, verify


ARITHMETIC_CASES = [
    (r"\frac12+\frac{12}{25}", Fraction(49, 50)),
    (r"\sqrt{8\times9\times10\times11+1}", Fraction(89)),
    (r"[\frac12-\frac13]\div[\frac17-\frac18]", Fraction(28, 3)),
    (r"123\times456456-456\times123123", Fraction(0)),
    (r"-2^2+5", Fraction(1)),
    (r"(\frac13+\frac14+\cdots+\frac1{7})", sum((Fraction(1, n) for n in range(3, 8)), Fraction())),
    (r"(1-\frac1{3^2})(1-\frac1{4^2})\cdots(1-\frac1{9^2})", Fraction(20, 27)),
]


INVALID_CASES = [
    r"\frac1{0}", r"\sqrt{2}", r"\frac6\cdot\frac23",
    r"(1-\frac1{3^2})(1-\frac1{5^2})\cdots(1-\frac1{9^2})",
    r"\frac13+\frac15+\cdots+\frac1{9}", "__import__('os')", r"\href{https://example.com}{1}",
]


FIXTURE = """- Synthetic arithmetic
  - Introduction
    - A common idea
      - A nested detail
  - Example 1: Addition
    - Evaluate:

      $$2+3.$$
    - Explanation
      - Deliberately wrong prose answer: 999.
    - Practice 1
      - Evaluate:

        $$3+4.$$
      - Explanation
        - Deliberately wrong prose answer: 888.
"""


def fixture_files(tmp_path: Path):
    source = tmp_path / "source.md"
    source.write_text(FIXTURE, encoding="utf-8")
    questions = collect_questions(parse_outline(FIXTURE))
    metadata = {"source_sha256": sha256(source.read_bytes()), "expected_question_count": 2,
                "answers": {q["id"]: {"question_sha256": sha256(q["question"]), "reference_answer": str(value),
                                      "answer_type": "integer", "constraints": "Give the exact value."}
                            for q, value in zip(questions, [5, 7])}}
    sidecar = tmp_path / "answers.json"
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    return source, sidecar, metadata


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tmp_path = Path(self.temporary.name)

    def test_exact_arithmetic(self):
        for tex, expected in ARITHMETIC_CASES:
            with self.subTest(tex=tex):
                self.assertEqual(ExactTex(tex).evaluate(), expected)

    def test_unsupported_or_invalid_arithmetic_is_rejected(self):
        for tex in INVALID_CASES:
            with self.subTest(tex=tex), self.assertRaises((ValueError, ZeroDivisionError)):
                ExactTex(tex).evaluate()

    def test_answer_verification_ignores_explanation_answers(self):
        source, sidecar, _ = fixture_files(self.tmp_path)
        report = verify(source, sidecar)
        self.assertEqual(report["verified_answer_count"], 2)
        self.assertEqual([r["independent_exact_result"] for r in report["records"]], ["5", "7"])

    def test_sidecar_cannot_override_computed_answer(self):
        source, sidecar, metadata = fixture_files(self.tmp_path)
        metadata["answers"]["example-1-question"]["reference_answer"] = "999"
        sidecar.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Independent arithmetic mismatch"):
            verify(source, sidecar)

    def test_source_change_requires_fresh_review(self):
        source, sidecar, _ = fixture_files(self.tmp_path)
        source.write_text(FIXTURE.replace("2+3", "2+4"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Source hash changed"):
            verify(source, sidecar)

    def test_outline_hierarchy_and_ids_are_preserved(self):
        topic = parse_outline(FIXTURE)
        self.assertEqual(topic.children[0].children[0].children[0].head, "A nested detail")
        self.assertEqual([q["id"] for q in collect_questions(topic)], ["example-1-question", "example-1-practice-1"])

    def test_skipped_practice_number_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "consecutive"):
            collect_questions(parse_outline(FIXTURE.replace("Practice 1", "Practice 2")))

    def test_erratum_must_be_uniquely_scoped(self):
        source, sidecar, metadata = fixture_files(self.tmp_path)
        metadata["errata"] = [{"question_id": "example-1-question", "field": "explanation", "original": "absent", "replacement": "present", "reason": "Synthetic"}]
        sidecar.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unique explanation match"):
            verify(source, sidecar)


if __name__ == "__main__":
    unittest.main()
