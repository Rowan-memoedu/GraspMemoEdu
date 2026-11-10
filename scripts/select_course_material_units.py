"""Package complete source units without generating or rewriting teaching text.

The selection config is local task input. The emitted materials manifest is the
shared source-independent course-production contract. Only explicitly configured
files are read; no sibling discovery or goal-derived input is supported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_if_changed(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def write_if_changed(path: Path, payload: bytes) -> None:
    if path.exists() and path.read_bytes() == payload:
        return
    path.write_bytes(payload)


def headings(lines: list[str]) -> list[tuple[int, str]]:
    result = []
    for index, line in enumerate(lines):
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            result.append((index, match.group(1)))
    return result


def partition_markdown(text: str, boundary_pattern: str) -> list[dict[str, Any]]:
    """Return exact, non-overlapping source spans, using one-based line numbers."""
    lines = text.splitlines(keepends=True)
    pattern = re.compile(boundary_pattern)
    starts = [(index, title) for index, title in headings(lines) if pattern.fullmatch(title)]
    result = []
    for position, (index, title) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        result.append({"title": title, "line_start": index + 1, "line_end": end,
                       "text": "".join(lines[index:end])})
    return result


def structural_subunits(text: str, pattern: str, unit_id: str) -> list[dict[str, str]]:
    """Recognize exact structural titles even when OCR omitted Markdown markers."""
    matcher = re.compile(pattern)
    titles = []
    for line in text.splitlines():
        normalized = re.sub(r"^#{1,6}\s+", "", line).strip()
        if matcher.fullmatch(normalized):
            titles.append(normalized)
    return [{"id": f"{unit_id}-section-{index + 1:03d}", "title": title}
            for index, title in enumerate(titles)]


def all_rem_ids(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("remId"), str):
            found.append(node["remId"])
        for key in ("contentStructured", "children"):
            for child in node.get(key, []) or []:
                found.extend(all_rem_ids(child))
    return list(dict.fromkeys(found))


def matching_rem_subtrees(node: dict[str, Any], rem_id: str) -> list[dict[str, Any]]:
    found = [node] if node.get("remId") == rem_id else []
    for key in ("contentStructured", "children"):
        for child in node.get(key, []) or []:
            found.extend(matching_rem_subtrees(child, rem_id))
    return found


def source_unit_id(category: str, rem_id: str) -> str:
    return "rn-" + category.replace("_", "-") + "-" + rem_id.lower()


def inspect_remnote(json_path: Path, markdown_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = read_json(json_path)
    raw_md = markdown_path.read_bytes()
    # Preserve LF/CRLF byte content in packages by decoding bytes directly.
    text = raw_md.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)
    starts = [(i, line[3:].strip()) for i, line in enumerate(lines) if line.startswith("## ")]
    sources = data.get("sources", [])
    if len(starts) != len(sources):
        raise ValueError(f"Heading/source count differs: {json_path.name}: {len(starts)} / {len(sources)}")
    units = []
    for offset, source in enumerate(sources):
        start, title = starts[offset]
        target = source["target"]
        if title.strip() != target["title"].strip():
            raise ValueError(f"Source heading mismatch: {json_path.name}: {title!r}")
        end = starts[offset + 1][0] if offset + 1 < len(starts) else len(lines)
        note = source["note"]
        children = note.get("contentStructured", []) or []
        entries = [{"id": child.get("remId", f"entry-{i + 1}"),
                    "title": child.get("title") or child.get("headline") or "[source media node]"}
                   for i, child in enumerate(children)]
        unit = {"unit_id": source_unit_id(data["category"], target["remId"]),
                "title": data["title"] + "：" + title, "source_title": title,
                "category": data["category"], "source_rem_id": target["remId"],
                "entries": entries, "text": "".join(lines[start:end]),
                "source_refs": [
                    {"path": str(markdown_path), "sha256": sha256(raw_md),
                     "line_start": start + 1, "line_end": end},
                    {"path": str(json_path), "sha256": sha256(json_path.read_bytes()),
                     "rem_ids": all_rem_ids(note)}]}
        units.append(unit)
    return units, {"title": data["title"], "category": data["category"],
                   "path": str(json_path), "sha256": sha256(json_path.read_bytes()),
                   "source_groups": len(units), "direct_entries": sum(len(unit["entries"]) for unit in units),
                   "markdown_bytes": len(raw_md), "json_bytes": json_path.stat().st_size}


def inspect_complete_entries(unit: dict[str, Any], requested_ids: set[str]) -> list[dict[str, Any]]:
    """Select whole direct entries, never a matching meaning inside an entry."""
    structured_ref = next(ref for ref in unit["source_refs"] if "rem_ids" in ref)
    data = read_json(Path(structured_ref["path"]))
    sources = [record for record in data["sources"]
               if record["target"]["remId"] == unit["source_rem_id"]]
    if len(sources) != 1:
        raise ValueError("Complete-entry parent source is not unique")
    children = sources[0]["note"].get("contentStructured", []) or []
    known_ids = {node.get("remId") for node in children}
    if not requested_ids <= known_ids:
        raise ValueError("Requested complete entry is not a direct source child")
    lines = unit["text"].splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.startswith("- ")]
    if len(starts) != len(children):
        raise ValueError("Direct entry/source count differs")
    selected = []
    for index, (start, child) in enumerate(zip(starts, children)):
        title = child.get("headline", child.get("title", "")) or ""
        first_line = title.splitlines()[0] if title else ""
        if lines[start].rstrip("\r\n") != "- " + first_line:
            raise ValueError("Direct entry heading differs from structured source")
        if child.get("remId") not in requested_ids:
            continue
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        raw_ref = unit["source_refs"][0]
        children_of_entry = child.get("children", child.get("contentStructured", [])) or []
        entries = [{"id": node["remId"],
                    "title": node.get("headline", node.get("title", "")) or "[source media node]"}
                   for node in children_of_entry]
        selected.append({
            "unit_id": source_unit_id(unit["category"], child["remId"]),
            "title": data["title"] + "：" + title, "source_title": title,
            "category": unit["category"], "source_rem_id": child["remId"],
            "parent_source_unit_id": unit["unit_id"], "entries": entries,
            "text": "".join(lines[start:end]),
            "source_refs": [
                {"path": raw_ref["path"], "sha256": raw_ref["sha256"],
                 "line_start": raw_ref["line_start"] + start,
                 "line_end": raw_ref["line_start"] + end - 1},
                {"path": structured_ref["path"], "sha256": structured_ref["sha256"],
                 "rem_ids": all_rem_ids(child)}]})
    return selected


def package_unit(output: Path, unit_id: str, title: str, text: str,
                 refs: list[dict[str, Any]], completeness: dict[str, Any],
                 scope: list[str]) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", unit_id):
        raise ValueError(f"Unsafe unit id: {unit_id}")
    relative = Path("units") / (unit_id + ".md")
    payload = text.encode("utf-8")
    destination = output / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_if_changed(destination, payload)
    return {"unit_id": unit_id, "title": title, "role": "teaching_source",
            "source_refs": refs, "content_path": relative.as_posix(),
            "content_sha256": sha256(payload), "completeness": completeness,
            "teaching_scope": scope}


def package_prepared_sources(specification: dict[str, Any], output: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Package reviewed excerpts or declared summaries without reading upstream corpora."""
    manifest_path = Path(specification["manifest_path"])
    manifest_bytes = manifest_path.read_bytes()
    source_manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    external_summary = specification.get("representation") == "external_reference_summary"
    if external_summary and not (
        source_manifest.get("role") == "independent_reference_sources"
        and source_manifest.get("representation") == "source_informed_summary_with_complete_reference_links"
        and source_manifest.get("not_verbatim_original_corpus") is True
        and source_manifest.get("not_full_webpage_transcription") is True
    ):
        raise ValueError("External reference summaries must declare their representation")
    manifest_hash = sha256(manifest_bytes)
    retained = output / "upstream-manifests" / (manifest_hash + ".json")
    retained.parent.mkdir(parents=True, exist_ok=True)
    write_if_changed(retained, manifest_bytes)
    result = []
    provenance = []
    for source in source_manifest["units"]:
        source_path = (manifest_path.parent / source["content_path"]).resolve()
        if not source_path.is_relative_to(manifest_path.parent.resolve()):
            raise ValueError("Prepared source content escapes its directory")
        raw = source_path.read_bytes()
        if sha256(raw) != source["content_sha256"]:
            raise ValueError("Prepared source content hash changed")
        text = raw.decode("utf-8-sig")
        if not text.strip():
            raise ValueError("Prepared source is empty")
        if external_summary:
            declared = source.get("completeness", {})
            if not (source.get("role") == "independent_reference"
                    and declared.get("kind") == "complete_natural_reference_scope"
                    and declared.get("representation") == "paraphrased_reference_note"
                    and declared.get("full_page_copied") is False
                    and declared.get("all_source_examples_reproduced") is False
                    and isinstance(declared.get("boundary"), str) and declared["boundary"].strip()):
                raise ValueError("External summary needs an explicit natural reference scope")
            subunits = [{"id": source["unit_id"] + "-reference-scope",
                         "title": source["title"] + "：全部已声明参考范围"}]
            completeness = {"kind": "external_reference_summary",
                "boundary": "新整理外部参考摘要；非原库全文、非网页逐字转录，也不声称复现全部原例句。声明的完整自然参考范围：" + declared["boundary"],
                "subunits": subunits}
        else:
            subunits = [{"id": source["unit_id"] + "-" + item["key"], "title": item["title"]}
                        for item in specification["subunits"]]
            completeness = {"kind": "complete_topic", "boundary": specification["boundary"],
                            "subunits": subunits}
        refs = [{"path": str(source_path), "sha256": sha256(raw),
                 "line_start": 1, "line_end": len(text.splitlines())},
                {"path": str(retained), "sha256": manifest_hash}]
        result.append(package_unit(output, source["unit_id"], source["title"], text, refs,
            completeness,
            specification["teaching_scope"]))
        provenance.append({"unit_id": source["unit_id"], "prepared_content_sha256": sha256(raw),
                           "upstream_source_refs": source["source_refs"]})
    verification = ("Prepared summary bytes and retained complete natural reference scopes are verified here. Publisher links are preserved; no full-page transcription, original-corpus presence or reproduction of every source example is claimed."
                    if external_summary else
                    "Prepared excerpt bytes and retained upstream provenance are verified here; the isolated source provider verified original source spans. Upstream full corpus is not opened by this selector.")
    return result, {"manifest_path": str(manifest_path), "manifest_sha256": manifest_hash,
                    "retained_manifest_path": str(retained), "source_units": provenance,
                    "verification_boundary": verification}


def apply_author_projections(manifest: dict[str, Any], root: Path,
                             exclusions: dict[str, list[list[int]]]) -> list[dict[str, Any]]:
    """Omit supplied complete source records from author input, retaining originals.

    Only numeric record boundaries are accepted; the script neither discovers nor
    interprets the content responsible for an exclusion.
    """
    units = {unit["unit_id"]: unit for unit in manifest["units"]}
    records = []
    for unit_id, ranges in exclusions.items():
        if unit_id not in units:
            raise ValueError("Exclusion refers to unknown material unit")
        unit = units[unit_id]
        original_path = unit["content_path"]
        original_bytes = (root / original_path).read_bytes()
        lines = original_bytes.decode("utf-8").splitlines(keepends=True)
        blocked: set[int] = set()
        for start, end in ranges:
            if not (1 <= start <= end <= len(lines)):
                raise ValueError("Exclusion line range invalid")
            indexes = set(range(start, end + 1))
            if indexes & blocked:
                raise ValueError("Overlapping exclusion ranges")
            blocked.update(indexes)
        text = "".join(line for index, line in enumerate(lines, 1) if index not in blocked)
        relative = Path("units-teaching") / (unit_id + ".md")
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        payload = text.encode("utf-8")
        write_if_changed(root / relative, payload)
        unit["content_path"] = relative.as_posix()
        unit["content_sha256"] = sha256(payload)
        primary = unit["source_refs"][0]
        if "line_start" not in primary:
            raise ValueError("Source projection requires original line provenance")
        primary["excluded_line_ranges"] = [
            {"line_start": primary["line_start"] + start - 1,
             "line_end": primary["line_start"] + end - 1}
            for start, end in ranges]
        records.append({"unit_id": unit_id, "original_path": original_path,
                        "original_sha256": sha256(original_bytes),
                        "teaching_path": relative.as_posix(), "teaching_sha256": sha256(payload),
                        "excluded_relative_lines": ranges, "excluded_line_count": len(blocked)})
    return records


def apply_image_transcriptions(manifest: dict[str, Any], root: Path,
                               records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bind inspected source pixels and replace image markers with exact transcripts."""
    by_id = {unit["unit_id"]: unit for unit in manifest["units"]}
    audits = []
    for record in records:
        unit = by_id[record["unit_id"]]
        source_image = (Path(unit["source_refs"][0]["path"]).parent / record["reference"]).resolve()
        source_root = Path(unit["source_refs"][0]["path"]).parent.resolve()
        if not source_image.is_relative_to(source_root):
            raise ValueError("Image escapes source document directory")
        raw_image = source_image.read_bytes()
        image_hash = sha256(raw_image)
        image_copy = root / "source-images" / (image_hash + source_image.suffix)
        image_copy.parent.mkdir(parents=True, exist_ok=True)
        write_if_changed(image_copy, raw_image)
        transcript = record.get("transcription", "")
        if record["classification"] != "decorative" and not transcript.strip():
            raise ValueError("Instructional source image has no transcription")
        transcript_path = root / "source-transcripts" / (image_hash + ".md")
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_bytes = (transcript + "\n").encode("utf-8") if transcript else b""
        write_if_changed(transcript_path, transcript_bytes)
        content = (root / unit["content_path"]).read_bytes().decode("utf-8")
        pattern = re.compile(r"!\[[^\]]*\]\(" + re.escape(record["reference"]) + r"\)")
        matches = pattern.findall(content)
        if len(matches) != 1:
            raise ValueError("Image marker must match one exact source position")
        replacement = transcript_bytes.decode("utf-8").rstrip("\n")
        content = pattern.sub(lambda match: replacement, content)
        relative = Path("units-teaching") / (unit["unit_id"] + ".md")
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        write_if_changed(root / relative, content.encode("utf-8"))
        unit["content_path"] = relative.as_posix()
        unit["content_sha256"] = sha256(content.encode("utf-8"))
        unit["source_refs"].extend([
            {"path": str(image_copy), "sha256": image_hash},
            {"path": str(transcript_path), "sha256": sha256(transcript_bytes)}])
        audits.append({"unit_id": unit["unit_id"], "reference": record["reference"],
                       "source_image": str(source_image), "image_copy": str(image_copy),
                       "image_sha256": image_hash, "classification": record["classification"],
                       "transcript_path": str(transcript_path), "transcript_sha256": sha256(transcript_bytes),
                       "original_marker": matches[0], "visual_review": record.get("visual_review", {})})
    return audits


def build_curriculum_groups(manifest: dict[str, Any], group_index: list[dict[str, Any]],
                            recipe: dict[str, Any]) -> list[dict[str, Any]]:
    """Group complete sources; this never specifies the number of resulting Topics."""
    selected = {unit["unit_id"]: unit for unit in manifest["units"]}
    groups = list(recipe.get("fixed_groups", []))
    used = {source_id for group in groups for source_id in group["source_unit_ids"]}
    selected_notes = [item for item in group_index if item["unit_id"] in selected]
    collocations = [item for item in selected_notes if item["category"] == "collocations"]
    phrasal = [item for item in selected_notes if item["category"] == "phrasal_verbs"]
    components: list[dict[str, Any]] = []
    for source in collocations:
        words = {item["title"].strip().casefold() for item in source["entries"]}
        connected = [part for part in components if part["words"] & words]
        component = {"words": set(words), "sources": [source]}
        for part in connected:
            component["words"].update(part["words"])
            component["sources"].extend(part["sources"])
            components.remove(part)
        components.append(component)
    for number, component in enumerate(components, 1):
        sources = component["sources"]
        joined = [item for item in phrasal if item["source_title"].casefold() in component["words"]]
        ids = [item["unit_id"] for item in sources + joined]
        labels = list(dict.fromkeys(entry["title"] for source in sources for entry in source["entries"]))
        groups.append({"unit_id": f"curr-collocations-{number:02d}",
                       "title": "动词搭配与多义：" + "、".join(labels), "source_unit_ids": ids})
        used.update(ids)
    for item in recipe.get("remaining_phrasal_groups", []):
        names = set(item["source_titles"])
        ids = [source["unit_id"] for source in phrasal if source["source_title"] in names]
        if len(ids) != len(names) or set(ids) & used:
            raise ValueError("Residual phrasal group is missing or repeated")
        groups.append({"unit_id": item["unit_id"], "title": item["title"], "source_unit_ids": ids})
        used.update(ids)
    for item in recipe.get("remaining_category_groups", []):
        ids = [source["unit_id"] for source in selected_notes
               if source["category"] in item["categories"] and source["unit_id"] not in used]
        if not ids:
            raise ValueError("Curriculum category group has no complete sources")
        if item.get("attach_to"):
            targets = [group for group in groups if group["unit_id"] == item["attach_to"]]
            if len(targets) != 1:
                raise ValueError("Curriculum category target is unknown")
            targets[0]["source_unit_ids"].extend(ids)
        else:
            groups.append({"unit_id": item["unit_id"], "title": item["title"], "source_unit_ids": ids})
        used.update(ids)
    for item in recipe.get("supplementary_sources", []):
        targets = [group for group in groups if group["unit_id"] == item["attach_to"]]
        ids = item["source_unit_ids"]
        if len(targets) != 1 or not ids or any(source_id not in selected for source_id in ids):
            raise ValueError("Supplementary sources require a known group and selected sources")
        if len(ids) != len(set(ids)) or set(ids) & used:
            raise ValueError("Supplementary source is already assigned")
        targets[0]["source_unit_ids"].extend(ids)
        used.update(ids)
    for source in selected_notes:
        if source["unit_id"] not in used:
            groups.append({"unit_id": "curr-" + source["unit_id"], "title": source["title"],
                           "source_unit_ids": [source["unit_id"]]})
            used.add(source["unit_id"])
    referenced = [source_id for group in groups for source_id in group["source_unit_ids"]]
    if len(referenced) != len(set(referenced)) or set(referenced) != set(selected):
        raise ValueError("Curriculum groups must cover every complete source exactly once")
    return groups


def validate_package(manifest: dict[str, Any], root: Path,
                     image_transcriptions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Independently read packaged bytes and compare every exact source span."""
    root = root.resolve()
    cache: dict[str, bytes] = {}
    checked_spans = 0
    checked_rem_trees = 0
    for unit in manifest["units"]:
        content_path = (root / unit["content_path"]).resolve()
        if not content_path.is_relative_to(root):
            raise ValueError("Packaged content escapes material directory")
        payload = content_path.read_bytes()
        if sha256(payload) != unit["content_sha256"]:
            raise ValueError("Packaged content hash changed: " + unit["unit_id"])
        for reference in unit["source_refs"]:
            source_path = reference["path"]
            if source_path not in cache:
                cache[source_path] = Path(source_path).read_bytes()
            raw = cache[source_path]
            if sha256(raw) != reference["sha256"]:
                raise ValueError("Source hash changed: " + source_path)
            if "line_start" in reference:
                lines = raw.decode("utf-8-sig").splitlines(keepends=True)
                start, end = reference["line_start"], reference["line_end"]
                if not (1 <= start <= end <= len(lines)):
                    raise ValueError("Source line range invalid: " + unit["unit_id"])
                excluded: set[int] = set()
                for span in reference.get("excluded_line_ranges", []):
                    if not (start <= span["line_start"] <= span["line_end"] <= end):
                        raise ValueError("Excluded source lines escape original span")
                    excluded.update(range(span["line_start"], span["line_end"] + 1))
                expected_text = "".join(line for index, line in enumerate(lines[start - 1:end], start)
                                        if index not in excluded)
                for image_record in image_transcriptions or []:
                    if image_record["unit_id"] == unit["unit_id"]:
                        transcript_bytes = Path(image_record["transcript_path"]).read_bytes()
                        if sha256(transcript_bytes) != image_record["transcript_sha256"]:
                            raise ValueError("Image transcript hash changed")
                        expected_text = expected_text.replace(image_record["original_marker"],
                            transcript_bytes.decode("utf-8").rstrip("\n"))
                expected = expected_text.encode("utf-8")
                if payload != expected:
                    raise ValueError("Packaged text differs from complete source span: " + unit["unit_id"])
                checked_spans += 1
            if "rem_ids" in reference:
                expected_ids = reference["rem_ids"]
                source = json.loads(raw.decode("utf-8-sig"))
                candidates = [node for record in source["sources"]
                              for node in matching_rem_subtrees(record["note"], expected_ids[0])]
                if len(candidates) != 1 or all_rem_ids(candidates[0]) != expected_ids:
                    raise ValueError("RemNote subtree provenance changed: " + unit["unit_id"])
                checked_rem_trees += 1
    return {"status": "source_bytes_verified", "unit_count": len(manifest["units"]),
            "exact_source_spans": checked_spans, "complete_remnote_subtrees": checked_rem_trees,
            "unique_source_files": len(cache)}


def build(config: dict[str, Any], output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"schema_version": 1, "role": "teaching_materials",
                               "course": config["course"],
                               "learning_objectives": config.get("learning_objectives", []), "units": []}
    inventory: dict[str, Any] = {"schema_version": 1, "books": [], "remnote_categories": [],
                               "media_gaps": [], "selection": [], "not_selected": []}
    canaries = []
    for book in config.get("books", []):
        path = Path(book["path"])
        raw = path.read_bytes()
        original_hash = sha256(raw)
        text = raw.decode("utf-8-sig")
        parts = partition_markdown(text, book["boundary_pattern"])
        selected = {item["source_title"]: item for item in book["selected_units"]}
        seen = set()
        available = []
        for part in parts:
            specification = selected.get(part["title"])
            available.append({"title": part["title"], "line_start": part["line_start"],
                              "line_end": part["line_end"], "characters": len(part["text"]),
                              "selected": specification is not None})
            if specification is None:
                inventory["not_selected"].append({"source": book["source_id"], "title": part["title"]})
                continue
            seen.add(part["title"])
            unit_id = specification["unit_id"]
            subunits = structural_subunits(part["text"],
                specification.get("subunit_pattern", book.get("subunit_pattern", r"(?!)")), unit_id)
            if not subunits:
                subunits = [{"id": unit_id + "-whole", "title": part["title"]}]
            refs = [{"path": str(path), "sha256": original_hash,
                     "line_start": part["line_start"], "line_end": part["line_end"]}]
            unit = package_unit(output, unit_id, specification.get("title", part["title"]), part["text"], refs,
                                {"kind": specification.get("kind", "complete_chapter"),
                                 "boundary": f"{book['title']} / {part['title']}，从本标题到下一同级来源单元前，保留全文。",
                                 "subunits": subunits},
                                specification.get("teaching_scope", [item["title"] for item in subunits]))
            manifest["units"].append(unit)
            inventory["selection"].append({"unit_id": unit_id, "characters": len(part["text"]),
                                           "source_lines": part["line_end"] - part["line_start"] + 1,
                                           "subunits": len(subunits)})
            images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", part["text"])
            if images:
                inventory["media_gaps"].append({"unit_id": unit_id, "kind": "source_image_references",
                                               "count": len(images), "references": images,
                                               "state": "text_package_preserves_reference_only"})
        if seen != set(selected):
            raise ValueError(f"Selected headings missing from {path.name}: {sorted(set(selected) - seen)}")
        inventory["books"].append({"source_id": book["source_id"], "title": book["title"],
                                   "path": str(path), "sha256": original_hash,
                                   "bytes": len(raw), "lines": len(text.splitlines()), "units": available})
        for canary in book.get("canaries", []):
            matches = partition_markdown(text, canary["boundary_pattern"])
            matches = [part for part in matches if part["title"] == canary["source_title"]]
            if len(matches) != 1:
                raise ValueError("Canary heading must occur exactly once")
            part = matches[0]
            canaries.append(package_unit(output, canary["unit_id"], canary["title"], part["text"],
                [{"path": str(path), "sha256": original_hash,
                  "line_start": part["line_start"], "line_end": part["line_end"]}],
                {"kind": "complete_topic", "boundary": canary["boundary"],
                 "subunits": [{"id": canary["unit_id"] + "-whole", "title": canary["source_title"]}]},
                canary["teaching_scope"]))
    group_index = []
    selected_extra = set(config.get("include_remnote_unit_ids", []))
    if config.get("complete_groups_path"):
        selected_extra.update(read_json(Path(config["complete_groups_path"]))["unit_ids"])
    reference_groups = set(config.get("reference_remnote_unit_ids", []))
    if config.get("reference_groups_path"):
        reference_groups.update(read_json(Path(config["reference_groups_path"]))["unit_ids"])
    found_references = set()
    inventory["reference_containers"] = []
    requested_entries = config.get("complete_entries", [])
    entry_manifests = list(config.get("complete_entries_paths", []))
    if config.get("complete_entries_path"):
        entry_manifests.append(config["complete_entries_path"])
    for entry_manifest in entry_manifests:
        requested_entries = [*requested_entries,
                             *read_json(Path(entry_manifest))["entries"]]
    if any(set(item) != {"source_unit_id", "rem_id"} for item in requested_entries):
        raise ValueError("Complete-entry requests must contain only source and entry IDs")
    entry_pairs = {(item["source_unit_id"], item["rem_id"]) for item in requested_entries}
    if len(entry_pairs) != len(requested_entries):
        raise ValueError("Repeated complete-entry request")
    found_entries = set()
    found_extra = set()
    for source in config.get("remnote_sources", []):
        units, details = inspect_remnote(Path(source["json_path"]), Path(source["markdown_path"]))
        inventory["remnote_categories"].append(details)
        for unit in units:
            group_index.append({key: value for key, value in unit.items() if key != "text"})
            selected = source.get("include_all", False) or unit["unit_id"] in selected_extra
            if unit["unit_id"] in reference_groups:
                found_references.add(unit["unit_id"])
                if not selected:
                    retained = package_unit(output, unit["unit_id"], unit["title"], unit["text"],
                        unit["source_refs"], {"kind": "complete_topic",
                            "boundary": "完整历史来源容器，仅作来源参考，不按容器指定必教范围。",
                            "subunits": unit["entries"]}, [])
                    retained["role"] = "reference_only"
                    inventory["reference_containers"].append(retained)
            entry_ids = {entry_id for source_id, entry_id in entry_pairs
                         if source_id == unit["unit_id"]}
            if selected and entry_ids:
                raise ValueError("Complete group and contained entry cannot both be selected")
            for entry in inspect_complete_entries(unit, entry_ids) if entry_ids else []:
                subunits = entry["entries"] or [{"id": entry["source_rem_id"], "title": entry["source_title"]}]
                manifest["units"].append(package_unit(output, entry["unit_id"], entry["title"], entry["text"],
                    entry["source_refs"], {"kind": "complete_reference_entry",
                        "boundary": "完整词头或词族条目：" + entry["source_title"] + "；保留其全部义项、例句和子节点。",
                        "subunits": subunits}, source.get("teaching_scope", [details["title"]])))
                group_index.append({key: value for key, value in entry.items() if key != "text"})
                inventory["selection"].append({"unit_id": entry["unit_id"], "characters": len(entry["text"]),
                                               "subunits": len(subunits)})
                found_entries.add((unit["unit_id"], entry["source_rem_id"]))
                if "[image]" in entry["text"]:
                    inventory["media_gaps"].append({"unit_id": entry["unit_id"], "kind": "unresolved_source_image",
                                                   "count": entry["text"].count("[image]")})
            if unit["unit_id"] in selected_extra:
                found_extra.add(unit["unit_id"])
            if not selected:
                continue
            subunits = unit["entries"] or [{"id": unit["source_rem_id"], "title": unit["source_title"]}]
            manifest["units"].append(package_unit(output, unit["unit_id"], unit["title"], unit["text"],
                unit["source_refs"], {"kind": "complete_topic",
                    "boundary": "完整原始来源组：" + unit["source_title"] + "；保留其全部条目、释义、例句与层级。",
                    "subunits": subunits}, source.get("teaching_scope", [details["title"]])))
            inventory["selection"].append({"unit_id": unit["unit_id"], "characters": len(unit["text"]),
                                           "subunits": len(subunits)})
            if "[image]" in unit["text"]:
                inventory["media_gaps"].append({"unit_id": unit["unit_id"], "kind": "unresolved_source_image",
                                               "count": unit["text"].count("[image]")})
    if selected_extra != found_extra:
        raise ValueError(f"Unknown complete RemNote groups: {sorted(selected_extra - found_extra)}")
    if reference_groups != found_references:
        raise ValueError(f"Unknown reference RemNote containers: {sorted(reference_groups - found_references)}")
    if entry_pairs != found_entries:
        raise ValueError(f"Unknown complete RemNote entries: {sorted(entry_pairs - found_entries)}")
    inventory["prepared_source_collections"] = []
    for specification in config.get("prepared_source_manifests", []):
        prepared, provenance = package_prepared_sources(specification, output)
        manifest["units"].extend(prepared)
        inventory["prepared_source_collections"].append(provenance)
        for unit in prepared:
            text = (output / unit["content_path"]).read_bytes().decode("utf-8")
            inventory["selection"].append({"unit_id": unit["unit_id"], "characters": len(text),
                                           "subunits": len(unit["completeness"]["subunits"])})
            if "[image]" in text or re.search(r"!\[[^\]]*\]\([^)]*\)", text):
                inventory["media_gaps"].append({"unit_id": unit["unit_id"], "kind": "unresolved_prepared_source_image"})
    ids = [unit["unit_id"] for unit in manifest["units"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Repeated unit ID")
    inventory["source_projections"] = apply_author_projections(
        manifest, output, config.get("excluded_relative_lines", {}))
    image_transcriptions = []
    if config.get("image_transcriptions_path"):
        image_transcriptions = apply_image_transcriptions(manifest, output,
            read_json(Path(config["image_transcriptions_path"]))["images"])
    inventory["image_transcriptions"] = image_transcriptions
    resolved_media = {record["unit_id"] for record in image_transcriptions}
    for gap in inventory["media_gaps"]:
        if gap["unit_id"] in resolved_media and gap["kind"] == "source_image_references":
            gap["state"] = "pixels_reviewed_and_teaching_projection_resolved"
    if config.get("curriculum_recipe_path"):
        manifest["curriculum_units"] = build_curriculum_groups(
            manifest, group_index, read_json(Path(config["curriculum_recipe_path"])))
    if config.get("scope_dependencies"):
        hints = config["scope_dependencies"]
        selected_ids = {unit["unit_id"] for unit in manifest["units"]}
        for hint in hints:
            if set(hint) != {"units", "prerequisites"} or any(
                not isinstance(values, list) or not values for values in hint.values()
            ):
                raise ValueError("Scope dependencies require source-unit lists")
            mentioned = [*hint["units"], *hint["prerequisites"]]
            if any(not isinstance(unit_id, str) or unit_id not in selected_ids for unit_id in mentioned):
                raise ValueError("Scope dependency references an unselected source")
        manifest["scope_dependencies"] = hints
    inventory["totals"] = {"selected_units": len(manifest["units"]),
                            "characters": sum(item["characters"] for item in inventory["selection"]),
                            "content_bytes": sum((output / item["content_path"]).stat().st_size for item in manifest["units"]),
                            "teaching_characters": sum(len((output / item["content_path"]).read_bytes().decode("utf-8")) for item in manifest["units"]),
                            "natural_subunits": sum(item["subunits"] for item in inventory["selection"])}
    if manifest.get("curriculum_units"):
        inventory["totals"]["curriculum_groups"] = len(manifest["curriculum_units"])
    write_json(output / "materials-manifest.json", manifest)
    write_json(output / "source-inventory.json", inventory)
    write_json(output / "remnote-group-index.json", {"schema_version": 1, "groups": group_index})
    if canaries:
        write_json(output / "canary-materials-manifest.json",
                   {**{key:value for key,value in manifest.items()
                       if key not in {"curriculum_units", "scope_dependencies"}}, "units": canaries})
    validation = {"main": validate_package(manifest, output, image_transcriptions),
                  "images": {"status": "pixels_reviewed_and_hash_bound",
                             "source_images": len(image_transcriptions),
                             "instructional_transcripts": sum(record["classification"] != "decorative" for record in image_transcriptions),
                             "decorative_images": sum(record["classification"] == "decorative" for record in image_transcriptions)}}
    if inventory["reference_containers"]:
        validation["reference_only"] = validate_package({"units": inventory["reference_containers"]}, output)
    if canaries:
        validation["canary"] = validate_package({**manifest, "units": canaries}, output)
    write_json(output / "source-validation.json", validation)
    return inventory["totals"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(read_json(args.config), args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
