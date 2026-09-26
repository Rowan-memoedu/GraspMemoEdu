"""Read-only course audit with exact arithmetic; no course data is embedded here.

Supported arithmetic is deliberately small. Unknown TeX is rejected, never
executed. Ellipses require explicitly validated consecutive sequences.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from fractions import Fraction
import hashlib
import json
from math import isqrt
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.question_types import validate_interaction


def sha256(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


@dataclass
class Node:
    head: str
    indent: int
    line: int
    lines: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)


def parse_outline(source: str) -> Node:
    root = Node("", -2, 0)
    stack = [root]
    for number, line in enumerate(source.lstrip("\ufeff").splitlines(), 1):
        match = re.match(r"^( *)- (.*)$", line)
        if match:
            indent = len(match[1])
            while stack[-1].indent >= indent:
                stack.pop()
            if indent != stack[-1].indent + 2:
                raise ValueError(f"Invalid outline indentation at line {number}")
            node = Node(match[2], indent, number)
            stack[-1].children.append(node)
            stack.append(node)
        else:
            node = stack[-1]
            if line.strip() and len(line) - len(line.lstrip()) < node.indent + 2:
                raise ValueError(f"Invalid continuation at line {number}")
            node.lines.append(line[node.indent + 2:])
    if len(root.children) != 1:
        raise ValueError("Expected one topic root")
    return root.children[0]


def node_text(node: Node) -> str:
    text = "\n".join([node.head, *node.lines]).strip()
    return text + ("\n" + "\n".join(node_text(n) for n in node.children) if node.children else "")


PRACTICE_HEAD = re.compile(r"^Practice\s+(\d+)(?:\.(\d+))?$", re.I)


def practice_nodes(module: Node) -> list[Node]:
    nodes = [n for n in module.children if re.match(r"^Practice(?:\s|$)", n.head, re.I)]
    matches = [PRACTICE_HEAD.fullmatch(n.head) for n in nodes]
    if any(m is None for m in matches):
        raise ValueError(f"Invalid Practice heading in {module.head}")
    if any(m[2] is not None for m in matches):
        numbers = [(int(m[1]), int(m[2]) if m[2] is not None else None) for m in matches]
        expected = [(group, member) for group in range(1, 6) for member in range(1, 4)]
        if numbers != expected:
            raise ValueError(f"Grouped Practice numbers must be consecutive 1.1 through 5.3 in {module.head}")
    elif [int(m[1]) for m in matches] != list(range(1, len(nodes) + 1)):
        raise ValueError(f"Practice numbers must be consecutive in {module.head}")
    return nodes


def collect_questions(topic: Node) -> list[dict]:
    result = []
    intro = [n for n in topic.children if n.head.lower() == "introduction"]
    if len(intro) != 1:
        raise ValueError("Expected exactly one Introduction")
    module_numbers = []
    for module in topic.children:
        if module in intro:
            continue
        found = re.match(r"^Example\s+(\d+)(?:[：:]|$)", module.head, re.I)
        if not found:
            raise ValueError(f"Unexpected section {module.head}")
        number = int(found[1])
        module_numbers.append(number)
        module_id = f"example-{number}"
        practices = practice_nodes(module)
        for ordinal, node in enumerate([module, *practices]):
            question_id = f"{module_id}-question" if ordinal == 0 else f"{module_id}-practice-{ordinal}"
            explanation = [n for n in node.children if n.head.lower() == "explanation"]
            configurations = [n for n in node.children if n.head.lower() == 'interaction']
            if len(configurations) > 1:
                raise ValueError('Duplicate Interaction')
            interaction = None
            if configurations:
                config = configurations[0]
                if any(line.strip() for line in config.lines) or len(config.children) != 1:
                    raise ValueError('Interaction needs one JSON child')
                interaction = json.loads(node_text(config.children[0]))
                if not isinstance(interaction, dict):
                    raise ValueError('Interaction must be a JSON object')
            question = [n for n in node.children if n.head.lower() not in ('explanation', 'interaction') and n not in practices]
            if len(explanation) != 1 or not question:
                raise ValueError(f"Missing question or Explanation in {question_id}")
            record = {"id": question_id, "module_id": module_id, "line": node.line,
                           "question": "\n".join(node_text(n) for n in question),
                           "explanation": "\n".join(node_text(n) for n in explanation[0].children)}
            validate_interaction(interaction, record['question'])
            if interaction is not None:
                record['interaction'] = interaction
            match = PRACTICE_HEAD.fullmatch(node.head) if ordinal else None
            if match and match[2] is not None:
                record["source_practice"] = {"label": f"{match[1]}.{match[2]}",
                                             "group": int(match[1]), "member": int(match[2])}
            result.append(record)
    if module_numbers != list(range(1, len(module_numbers) + 1)):
        raise ValueError("Example numbers must be consecutive")
    return result


def normalize_tex(tex: str) -> str:
    tex = re.sub(r"\\(?:begin|end)\{aligned\}", "", tex)
    tex = re.sub(r"\\(?:left|right|quad|qquad)", "", tex)
    return re.sub(r"\s+", "", tex.replace("&", "").replace("\\\\", "")).rstrip(".")


def expand_sequences(tex: str) -> tuple[str, list[dict]]:
    checks = []
    reciprocal = r"(?:1|\\frac1(?:\{\d+\}|\d))"
    harmonic = re.compile(f"({reciprocal})\\+({reciprocal})\\+" + r"\\cdots\+(" + reciprocal + ")")

    def denominator(term: str) -> int:
        if term == "1":
            return 1
        return int(term.removeprefix(r"\frac1").strip("{}"))

    def harmonic_replacement(match: re.Match) -> str:
        first, second, last = map(denominator, match.groups())
        if second != first + 1 or not 1 <= first < last <= 5000:
            raise ValueError("Unsupported harmonic ellipsis bounds")
        value = sum((Fraction(1, n) for n in range(first, last + 1)), Fraction())
        checks.append({"kind": "harmonic_sum", "first": first, "last": last,
                       "terms": last - first + 1, "exact": str(value)})
        return r"\frac{" + str(value.numerator) + "}{" + str(value.denominator) + "}"

    tex = harmonic.sub(harmonic_replacement, tex)
    factor = r"\(1-\\frac1\{(\d+)\^2\}\)"
    telescoping = re.compile(factor + factor + r"\\cdots" + factor)

    def product_replacement(match: re.Match) -> str:
        first, second, last = map(int, match.groups())
        if second != first + 1 or not 2 <= first < last <= 5000:
            raise ValueError("Unsupported product ellipsis bounds")
        direct = Fraction(1)
        for n in range(first, last + 1):
            direct *= 1 - Fraction(1, n * n)
        # Independent cancellation of the two product streams.
        cancelled = Fraction(first - 1, last) * Fraction(last + 1, first)
        if direct != cancelled:
            raise ValueError("Product expansion disagrees with cancellation")
        checks.append({"kind": "consecutive_product", "first": first, "last": last,
                       "terms": last - first + 1, "direct": str(direct), "cancelled": str(cancelled)})
        return r"\frac{" + str(direct.numerator) + "}{" + str(direct.denominator) + "}"

    tex = telescoping.sub(product_replacement, tex)
    if r"\cdots" in tex or r"\ldots" in tex:
        raise ValueError("Unrecognized ellipsis; manual mathematical review is required")
    return tex, checks


class ExactTex:
    """A non-executing TeX arithmetic parser over fractions and exact roots."""

    def __init__(self, tex: str):
        self.text, self.sequence_checks = expand_sequences(normalize_tex(tex))
        self.position = 0

    def take(self, value: str) -> bool:
        if self.text.startswith(value, self.position):
            self.position += len(value)
            return True
        return False

    def expression(self) -> Fraction:
        value = self.product()
        while self.position < len(self.text):
            if self.take("+"):
                value += self.product()
            elif self.take("-"):
                value -= self.product()
            else:
                break
        return value

    def product(self) -> Fraction:
        value = self.power()
        while self.position < len(self.text):
            if self.take(r"\times") or self.take(r"\cdot") or self.take("*"):
                value *= self.power()
            elif self.take(r"\div") or self.take("/"):
                value /= self.power()
            elif self.text[self.position] in "({[" or self.text.startswith((r"\frac", r"\sqrt"), self.position):
                value *= self.power()
            else:
                break
        return value

    def power(self) -> Fraction:
        sign = -1 if self.take("-") else 1
        if sign == 1:
            self.take("+")
        value = self.atom()
        if self.take("^"):
            exponent = self.argument()
            if exponent.denominator != 1 or abs(exponent.numerator) > 100:
                raise ValueError("Only bounded integer powers are supported")
            value **= exponent.numerator
        return sign * value

    def argument(self) -> Fraction:
        if self.position >= len(self.text):
            raise ValueError("Missing TeX argument")
        if self.text[self.position] == "{":
            return self.atom()
        if self.text[self.position].isdigit():
            # Unbraced TeX arguments consume one token, not the whole number.
            result = Fraction(int(self.text[self.position]))
            self.position += 1
            return result
        raise ValueError(f"Unsupported TeX argument at {self.position}")

    def atom(self) -> Fraction:
        if self.position >= len(self.text):
            raise ValueError("Unexpected end of expression")
        if self.take(r"\frac"):
            numerator = self.argument()
            denominator = self.argument()
            return numerator / denominator
        if self.take(r"\sqrt"):
            value = self.argument()
            if value < 0:
                raise ValueError("Negative radicand")
            numerator, denominator = isqrt(value.numerator), isqrt(value.denominator)
            if numerator * numerator != value.numerator or denominator * denominator != value.denominator:
                raise ValueError("Non-rational square root requires a separate verifier")
            return Fraction(numerator, denominator)
        opener = self.text[self.position]
        if opener in "({[":
            self.position += 1
            value = self.expression()
            if not self.take({"(": ")", "{": "}", "[": "]"}[opener]):
                raise ValueError(f"Unclosed group at {self.position}")
            return value
        match = re.match(r"\d+(?:\.\d+)?", self.text[self.position:])
        if not match:
            raise ValueError(f"Unsupported arithmetic token at {self.position}: {self.text[self.position:self.position + 16]}")
        self.position += len(match[0])
        return Fraction(match[0])

    def evaluate(self) -> Fraction:
        value = self.expression()
        if self.position != len(self.text):
            raise ValueError(f"Unparsed arithmetic at {self.position}: {self.text[self.position:self.position + 16]}")
        return value


def verify(source_path: Path, sidecar_path: Path, review_path: Path | None = None) -> dict:
    raw = source_path.read_bytes()
    source = raw.decode("utf-8-sig")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8-sig"))
    if sha256(raw) != sidecar["source_sha256"]:
        raise ValueError("Source hash changed; sidecar must be reviewed again")
    questions = collect_questions(parse_outline(source))
    by_id = {q["id"]: q for q in questions}
    mechanical = False
    if set(by_id) != set(sidecar["answers"]):
        raise ValueError("Sidecar question IDs do not exactly cover the source")
    if len(questions) != sidecar["expected_question_count"]:
        raise ValueError("Question count changed")
    if review_path is not None:
        module_ids = {q["module_id"] for q in questions}
        if not module_ids or len(module_ids) != sidecar.get("expected_module_count"):
            raise ValueError("Module count changed")
        if not isinstance(sidecar.get("course_id"), str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", sidecar["course_id"]):
            raise ValueError("Invalid course_id")
        expected = sidecar.get("expected_practices_per_module")
        for module_id in module_ids:
            count = sum(q["module_id"] == module_id for q in questions) - 1
            if count < 7 or (expected is not None and count != expected):
                raise ValueError(f"Unexpected Practice count in {module_id}; at least seven required")
        review = json.loads(review_path.read_text(encoding="utf-8-sig"))
        mechanical = review.get("mode") == "mechanical"
        expected_status = "mechanically_validated" if mechanical else "approved"
        if (review.get("schema_version") != 1 or review.get("status") != expected_status
                or (mechanical and review.get("method") != "mechanical-import")):
            raise ValueError("LLM review is not approved")
        if review.get("source_sha256") != sha256(raw) or review.get("sidecar_sha256") != sha256(sidecar_path.read_bytes()):
            raise ValueError("LLM review hash mismatch; review again")
        reviewed = review.get("questions")
        if not isinstance(reviewed, list) or any(not isinstance(q, dict) for q in reviewed):
            raise ValueError("Invalid LLM review questions")
        ids = [q.get("id") for q in reviewed]
        if any(not isinstance(qid, str) for qid in ids) or len(set(ids)) != len(ids) or set(ids) != set(by_id):
            raise ValueError("LLM review must uniquely cover all questions")
        for record in reviewed:
            if mechanical:
                if record.get("structurally_valid") is not True or record.get("field_mapping") != "reference_answer=explanation":
                    raise ValueError(f"Invalid mechanical field mapping: {record['id']}")
            elif record.get("approved") is not True or record.get("issues") != []:
                raise ValueError(f"LLM review rejected: {record['id']}")
            if record.get("question_sha256") != sha256(by_id[record["id"]]["question"]):
                raise ValueError(f"LLM review question hash mismatch: {record['id']}")
    records = []
    for question in questions:
        answer = sidecar["answers"][question["id"]]
        if sha256(question["question"]) != answer["question_sha256"]:
            raise ValueError(f"Question hash mismatch: {question['id']}")
        if review_path is not None:
            if any(not isinstance(answer.get(key), str) or not answer[key].strip()
                   for key in ("reference_answer", "answer_type", "constraints")):
                raise ValueError(f"Missing answer metadata: {question['id']}")
            if not question["explanation"].strip():
                raise ValueError(f"Missing explanation: {question['id']}")
            if mechanical and answer["reference_answer"] != question["explanation"]:
                raise ValueError(f"Mechanical explanation mapping mismatch: {question['id']}")
            records.append({"id": question["id"], "source_line": question["line"],
                            "question_sha256": answer["question_sha256"],
                            "reference_answer": answer["reference_answer"], "passed": True,
                            "method": "mechanical-import" if mechanical else "llm-reviewed"})
            continue
        if answer["answer_type"] not in {"integer", "rational"} or not answer["constraints"].strip():
            raise ValueError(f"Missing answer metadata: {question['id']}")
        formulas = re.findall(r"\$\$([\s\S]*?)\$\$", question["question"])
        if len(formulas) != 1:
            raise ValueError(f"Expected one arithmetic question formula: {question['id']}")
        evaluator = ExactTex(formulas[0])
        computed = evaluator.evaluate()
        expected = Fraction(answer["reference_answer"])
        if computed != expected:
            raise ValueError(f"Independent arithmetic mismatch: {question['id']}: {computed} != {expected}")
        if answer["answer_type"] == "integer" and computed.denominator != 1:
            raise ValueError(f"Integer answer type mismatch: {question['id']}")
        records.append({"id": question["id"], "source_line": question["line"],
                        "question_sha256": answer["question_sha256"],
                        "reference_answer": answer["reference_answer"],
                        "independent_exact_result": str(computed), "passed": True,
                        "method": "direct question TeX evaluation with Python Fraction and integer square root",
                        "sequence_checks": evaluator.sequence_checks})
    corrections = []
    if mechanical and sidecar.get("errata"):
        raise ValueError("Mechanical import does not rewrite source explanations")
    for correction in sidecar.get("errata", []):
        question = by_id[correction["question_id"]]
        if correction["field"] != "explanation" or question["explanation"].count(correction["original"]) != 1:
            raise ValueError(f"Erratum is not a unique explanation match: {correction['question_id']}")
        if not correction["reason"] or not correction["replacement"]:
            raise ValueError("Errata require a reason and replacement")
        corrections.append({**correction, "source_line": question["line"], "unique_match": True})
    return {"status": "mechanically_validated" if mechanical else "verified", "source_sha256": sha256(raw), "source_path": str(source_path),
            "sidecar_sha256": sha256(sidecar_path.read_bytes()), "source_unchanged": sha256(source_path.read_bytes()) == sha256(raw),
            "question_count": len(questions), "verified_answer_count": 0 if mechanical else len(records),
            "records": records, "errata": corrections, "content_review_notes": sidecar.get("content_review_notes", []),
            **({"method": "mechanical-import" if mechanical else "llm-reviewed", "review_sha256": sha256(review_path.read_bytes()),
                "review_model": review.get("model")} if review_path is not None else {}),
            **({"semantic_review_performed": False, "mathematical_correctness_checked": False,
                "mechanically_mapped_answer_count": len(records)} if mechanical else {})}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    result = verify(args.source, args.sidecar, review_path=args.review)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
