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
        practices = [n for n in module.children if re.match(r"^Practice\s+\d+$", n.head, re.I)]
        practice_numbers = [int(re.search(r"\d+", n.head)[0]) for n in practices]
        if practice_numbers != list(range(1, len(practices) + 1)):
            raise ValueError(f"Practice numbers must be consecutive in {module_id}")
        for node in [module, *practices]:
            practice_number = re.search(r"\d+", node.head)[0]
            question_id = f"{module_id}-question" if node is module else f"{module_id}-practice-{practice_number}"
            explanation = [n for n in node.children if n.head.lower() == "explanation"]
            question = [n for n in node.children if n.head.lower() != "explanation" and not re.match(r"^Practice\s+\d+$", n.head, re.I)]
            if len(explanation) != 1 or not question:
                raise ValueError(f"Missing question or Explanation in {question_id}")
            result.append({"id": question_id, "module_id": module_id, "line": node.line,
                           "question": "\n".join(node_text(n) for n in question),
                           "explanation": "\n".join(node_text(n) for n in explanation[0].children)})
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


def verify(source_path: Path, sidecar_path: Path) -> dict:
    raw = source_path.read_bytes()
    source = raw.decode("utf-8-sig")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8-sig"))
    if sha256(raw) != sidecar["source_sha256"]:
        raise ValueError("Source hash changed; sidecar must be reviewed again")
    questions = collect_questions(parse_outline(source))
    by_id = {q["id"]: q for q in questions}
    if set(by_id) != set(sidecar["answers"]):
        raise ValueError("Sidecar question IDs do not exactly cover the source")
    if len(questions) != sidecar["expected_question_count"]:
        raise ValueError("Question count changed")
    records = []
    for question in questions:
        answer = sidecar["answers"][question["id"]]
        if sha256(question["question"]) != answer["question_sha256"]:
            raise ValueError(f"Question hash mismatch: {question['id']}")
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
    for correction in sidecar.get("errata", []):
        question = by_id[correction["question_id"]]
        if correction["field"] != "explanation" or question["explanation"].count(correction["original"]) != 1:
            raise ValueError(f"Erratum is not a unique explanation match: {correction['question_id']}")
        if not correction["reason"] or not correction["replacement"]:
            raise ValueError("Errata require a reason and replacement")
        corrections.append({**correction, "source_line": question["line"], "unique_match": True})
    return {"status": "verified", "source_sha256": sha256(raw), "source_path": str(source_path),
            "sidecar_sha256": sha256(sidecar_path.read_bytes()), "source_unchanged": sha256(source_path.read_bytes()) == sha256(raw),
            "question_count": len(questions), "verified_answer_count": len(records),
            "records": records, "errata": corrections, "content_review_notes": sidecar.get("content_review_notes", [])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.source, args.sidecar)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
