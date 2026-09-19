"""Compare rendered course content and hierarchy against the original outline."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from verify_content import collect_questions, parse_outline, sha256, verify

MATH = re.compile(r"\$\$([\s\S]*?)\$\$|\$([^$\n]+)\$")


def prose(value: str) -> str:
    value = MATH.sub("", value)
    value = re.sub(r"^\s*\d+\.\s*", "", value, flags=re.M)
    return re.sub(r"\s+", "", value)


def normalize_formula(value: str) -> str:
    return re.sub(r"^ +", "", value, flags=re.M).strip()


class CourseHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.records = {}
        self.formulas = []
        self.error_nodes = 0
        self.active_content = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        line = int(attrs["data-source-line"]) if "data-source-line" in attrs else None
        ignored = bool(set(attrs.get("class", "").split()) & {"mathInline", "mathDisplay", "contentErratum"})
        if "data-tex" in attrs:
            self.formulas.append(normalize_formula(attrs["data-tex"]))
        if attrs.get("data-mml-node") == "merror" or "data-mjx-error" in attrs:
            self.error_nodes += 1
        if tag in {"script", "iframe", "img", "link", "object", "embed", "form"} or any(key.startswith("on") for key in attrs):
            self.active_content.append(tag)
        if any(key in {"href", "src", "xlink:href"} and re.match(r"https?:|//|javascript:|data:", value, re.I) for key, value in attributes if value):
            self.active_content.append(tag)
        if line is not None:
            if line in self.records:
                raise ValueError(f"Duplicate rendered source line {line}")
            parent = next((entry[1] for entry in reversed(self.stack) if entry[1] is not None), None)
            self.records[line] = {"parent": parent, "text": ""}
        if tag not in {"br", "hr", "meta", "input", "img", "link", "wbr"}:
            self.stack.append((tag, line, ignored))

    def handle_startendtag(self, tag, attributes):
        self.handle_starttag(tag, attributes)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if any(entry[2] for entry in self.stack):
            return
        line = next((entry[1] for entry in reversed(self.stack) if entry[1] is not None), None)
        if line is not None:
            self.records[line]["text"] += data


def audit(source_path: Path, sidecar_path: Path, course_path: Path) -> dict:
    arithmetic = verify(source_path, sidecar_path)
    source = source_path.read_text(encoding="utf-8-sig")
    topic = parse_outline(source)
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    course = json.loads(course_path.read_text(encoding="utf-8"))
    if course["source_sha256"] != arithmetic["source_sha256"]:
        raise ValueError("Rendered course has a different source hash")
    questions = collect_questions(topic)
    imported_questions = [q for module in course["modules"] for q in [module["example"], *module["practices"]]]
    if [q["id"] for q in questions] != [q["id"] for q in imported_questions]:
        raise ValueError("Rendered question IDs differ from source order")
    for question, imported in zip(questions, imported_questions):
        if question["question"] != imported["question"]:
            raise ValueError(f"Question text changed: {question['id']}")
        for key in ["reference_answer", "answer_type", "constraints"]:
            if imported[key] != sidecar["answers"][question["id"]][key]:
                raise ValueError(f"Answer metadata changed: {question['id']}/{key}")
    expected = {}
    expected_formulas = []

    def visit(nodes, corrections=(), parent=None):
        for node in nodes:
            body = "\n".join([node.head, *node.lines]).strip()
            for correction in corrections:
                body = body.replace(correction["original"], correction["replacement"])
            expected[node.line] = {"parent": parent, "text": prose(body)}
            expected_formulas.extend(normalize_formula(match[1] if match[1] is not None else match[2]) for match in MATH.finditer(body))
            visit(node.children, corrections, node.line)

    introduction = next(node for node in topic.children if node.head == "Introduction")
    visit(introduction.children)
    for module in [node for node in topic.children if node.head.startswith("Example ")]:
        module_number = re.search(r"\d+", module.head)[0]
        practices = [node for node in module.children if re.match(r"Practice\s+\d+$", node.head)]
        for index, section in enumerate([module, *practices]):
            question_id = f"example-{module_number}-question" if index == 0 else f"example-{module_number}-practice-{index}"
            visit([node for node in section.children if node.head != "Explanation" and not re.match(r"Practice\s+\d+$", node.head)])
            explanation = next(node for node in section.children if node.head == "Explanation")
            visit(explanation.children, [entry for entry in sidecar.get("errata", []) if entry["question_id"] == question_id])
    rendered = CourseHTML()
    rendered.feed(course["introduction"]["html"])
    for question in imported_questions:
        rendered.feed(question["html"])
        rendered.feed(question["explanation_html"])
    if rendered.stack:
        raise ValueError("Rendered HTML contains unclosed elements")
    if set(expected) != set(rendered.records):
        raise ValueError("Rendered outline nodes differ from source nodes")
    for line, item in expected.items():
        actual = rendered.records[line]
        if item["parent"] != actual["parent"] or item["text"] != re.sub(r"\s+", "", actual["text"]):
            raise ValueError(f"Prose or hierarchy changed at source line {line}")
    if expected_formulas != rendered.formulas:
        raise ValueError("Rendered mathematical formulas differ from reviewed source")
    if rendered.error_nodes or rendered.active_content:
        raise ValueError("Rendered content has math errors or active/remote content")
    stable_content = {key: value for key, value in course.items() if key != "version"}
    calculated_version = sha256(json.dumps(stable_content, ensure_ascii=False, separators=(",", ":")))
    if calculated_version != course["version"]:
        raise ValueError("Course version does not cover its full contents")
    return {"status": "verified", "source_sha256": arithmetic["source_sha256"], "course_version": course["version"],
            "questions_verified": len(questions), "prose_and_hierarchy_nodes_verified": len(expected),
            "formulas_verified": len(expected_formulas), "math_errors": rendered.error_nodes,
            "active_or_remote_content": rendered.active_content, "reviewed_errata": len(sidecar.get("errata", [])),
            "content_review_notes": sidecar.get("content_review_notes", [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("course", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit(args.source, args.sidecar, args.course)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
