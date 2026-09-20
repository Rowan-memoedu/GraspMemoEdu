"""Grouped author labels flatten without changing question content or legacy IDs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from scripts.verify_content import collect_questions, parse_outline, sha256
from tests.fixtures import temporary_test_directory


ROOT = Path(__file__).resolve().parents[1]
LABELS = [f"{group}.{member}" for group in range(1, 6) for member in range(1, 4)]


def outline(labels, modules=1):
    lines = ["- Synthetic grouped arithmetic", "  - Introduction", "    - Add two numbers."]
    for module in range(1, modules + 1):
        for ordinal, label in enumerate([None, *labels]):
            value = module * 40 + ordinal
            indent = "    " if ordinal else "  "
            head = f"Practice {label}" if ordinal else f"Example {module}: Addition"
            lines += [f"{indent}- {head}", f"{indent}  - Calculate:", "",
                      f"{indent}    $$", f"{indent}    {value}+2", f"{indent}    $$",
                      f"{indent}  - Explanation", f"{indent}    - ${value}+2={value + 2}$."]
    return "\n".join(lines) + "\n"


class GroupedPracticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.md"

    def node(self, *args):
        return subprocess.run(["node", str(ROOT / "scripts/import_course.mjs"), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8", timeout=120,
                              env={**os.environ, "MATH_LEARNING_PYTHON": sys.executable})

    def test_grouped_source_imports_with_flat_titles_and_preserved_source(self):
        original = outline(LABELS, modules=2)
        self.source.write_text(original, encoding="utf-8")
        questions = collect_questions(parse_outline(original))
        inspected = self.node("--inspect", self.source)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        inspection = json.loads(inspected.stdout)
        self.assertEqual(len(questions), 32)
        self.assertEqual([q["id"] for q in inspection["questions"]], [q["id"] for q in questions])
        for actual, expected in zip(inspection["questions"], questions):
            self.assertEqual(actual["question"], expected["question"])
            self.assertEqual(actual.get("source_practice"), expected.get("source_practice"))
            self.assertNotIn("Practice", actual["question"])

        answers = {"course_id": "synthetic-grouped-arithmetic", "source_sha256": sha256(self.source.read_bytes()),
                   "expected_module_count": 2, "expected_question_count": 32,
                   "expected_practices_per_module": 15, "errata": [], "answers": {}}
        for index, question in enumerate(questions):
            module, ordinal = divmod(index, 16)
            answers["answers"][question["id"]] = {
                "question_sha256": sha256(question["question"]),
                "reference_answer": str((module + 1) * 40 + ordinal + 2),
                "answer_type": "integer", "constraints": "Give the exact sum."}
        sidecar, output, report = [self.root / name for name in ("answers.json", "course.json", "report.json")]
        sidecar.write_text(json.dumps(answers), encoding="utf-8")
        imported = self.node(self.source, sidecar, output, report)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        course = json.loads(output.read_text(encoding="utf-8"))
        for number, module in enumerate(course["modules"], 1):
            self.assertEqual([q["title"] for q in module["practices"]], [f"Practice {i}" for i in range(1, 16)])
            self.assertEqual([q["id"] for q in module["practices"]], [f"example-{number}-practice-{i}" for i in range(1, 16)])
            self.assertEqual([q["source_practice"]["label"] for q in module["practices"]], LABELS)
            self.assertEqual(module["practices"][3]["source_practice"], {"label": "2.1", "group": 2, "member": 1})
            self.assertTrue(all("Practice" not in q["html"] for q in module["practices"]))
        audited = subprocess.run([sys.executable, str(ROOT / "scripts/audit_course.py"),
                                  str(self.source), str(sidecar), str(output)], capture_output=True,
                                 text=True, encoding="utf-8", timeout=60)
        self.assertEqual(audited.returncode, 0, audited.stderr)
        self.assertEqual(json.loads(audited.stdout)["questions_verified"], 32)
        self.assertEqual(self.source.read_text(encoding="utf-8"), original)

    def test_invalid_group_labels_fail_in_both_parsers(self):
        cases = {
            "missing": LABELS[:4] + LABELS[5:],
            "duplicate": [LABELS[0], LABELS[0], *LABELS[2:]],
            "out_of_order": [LABELS[1], LABELS[0], *LABELS[2:]],
            "mixed": ["1", *LABELS[1:]],
            "bad_member": ["1.4", *LABELS[1:]],
            "bad_group": [*LABELS[:-1], "6.3"],
            "trailing_junk": ["1.1 extra", *LABELS[1:]],
            "incomplete": LABELS[:-3],
        }
        for name, labels in cases.items():
            with self.subTest(case=name):
                source = outline(labels)
                with self.assertRaisesRegex(ValueError, "Practice"):
                    collect_questions(parse_outline(source))
                self.source.write_text(source, encoding="utf-8")
                result = self.node("--inspect", self.source)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Practice", result.stderr)

    def test_legacy_integer_numbering_retains_titles_ids_and_no_group_metadata(self):
        self.source.write_text(outline([str(i) for i in range(1, 9)]), encoding="utf-8")
        questions = collect_questions(parse_outline(self.source.read_text(encoding="utf-8")))
        result = self.node("--inspect", self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        inspected = json.loads(result.stdout)["questions"]
        self.assertEqual(len(inspected), 9)
        self.assertEqual([q["id"] for q in inspected], [q["id"] for q in questions])
        self.assertEqual([q["title"] for q in inspected[1:]], [f"Practice {i}" for i in range(1, 9)])
        self.assertTrue(all("source_practice" not in q for q in inspected + questions))


if __name__ == "__main__":
    unittest.main()
