"""Source-role isolation and mechanical native-source projections.

Only teaching manifests cross the author boundary. Target extraction and final
point mapping are independent consumers, never inputs to this module.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from html import escape
import json
from pathlib import Path
import re

from graspmemoedu.atomic_content import plain_text
from graspmemoedu.point_assessment import validate_assessment
from graspmemoedu.question_types import validate_interaction
from graspmemoedu.structured_atomic import SourceHTML
from graspmemoedu.topic_format import normalize_document, validate_field_placement

SCHEMA_VERSION = 1
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
FORBIDDEN_KEYS = frozenset({
    'goal', 'goals', 'goal_text', 'goal_title', 'original', 'original_goal',
    'target', 'target_text', 'target_passage', 'target_required', 'assessed_by',
    'planned_assessed_by', 'blank_ids', 'blank_id', 'answer_key', 'answers',
    'source_marker', 'private_goal', 'material_selection',
})
FORBIDDEN_PATHS = frozenset({'private-goal', 'material-grounded-course',
                            'original-goal.json', 'sources.json'})
STRUCTURAL_SECTION_NAMES = re.compile(
    r'^(?:Introduction|Examples?|Explanations?|Practices?|Flashcards?|Memory[ -]?(?:chunks?|points?)|'
    r'Atomic[ -]?(?:memory[ -]?)?points?|导入|引入|指导|记忆组块|知识组块|原子知识点|原子点|记忆点|'
    r'统一闪卡|统一Flashcards?|闪卡|例题|示例|讲解|练习|练习与解析)$', re.I)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(value):
    require(isinstance(value, str) and ID.fullmatch(value), 'Invalid stable identifier')
    return value


def strings(value, label, *, empty=False):
    require(isinstance(value, list) and (empty or value)
            and all(isinstance(s, str) and s.strip() for s in value), 'Invalid ' + label)
    return value


def reject_target_metadata(value):
    """Reject contamination before filtering; silently dropping it hides leaks."""
    if isinstance(value, dict):
        for key, item in value.items():
            require(key not in FORBIDDEN_KEYS, 'Target data cannot enter teaching input: ' + key)
            if key == 'role':
                require(item in ('teaching_materials', 'teaching_source', 'domain_objectives'),
                        'Only explicitly typed teaching roles may enter author input')
            reject_target_metadata(item)
    elif isinstance(value, list):
        for item in value:
            reject_target_metadata(item)


def safe_material_path(path):
    path = Path(path).resolve()
    parts = [part.casefold() for part in path.parts]
    require(not FORBIDDEN_PATHS.intersection(parts)
            and not any(a == 'goal-course' and b == 'run' for a, b in zip(parts, parts[1:])),
            'Private target or retired goal-centered input is forbidden')
    return path


def domain_objectives(value):
    reject_target_metadata(value)
    require(set(value) == {'role', 'learning_objectives'} and value['role'] == 'domain_objectives',
            'Domain objectives must use the dedicated allowlisted contract')
    return deepcopy(strings(value['learning_objectives'], 'domain objectives', empty=True))


def load_materials(path, objectives_path=None):
    path = safe_material_path(path)
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    reject_target_metadata(raw)
    require(raw.get('schema_version') == SCHEMA_VERSION and raw.get('role') == 'teaching_materials',
            'A typed teaching-materials manifest is required; legacy goal sources are refused')
    require(set(raw) <= {'schema_version', 'role', 'course', 'learning_objectives', 'units', 'curriculum_units', 'scope_dependencies'},
            'Unknown teaching manifest field')
    course = raw['course']
    require(set(course) <= {'id', 'title', 'subject_id', 'description'}, 'Unknown course field')
    identifier(course['id']); identifier(course['subject_id'])
    require(bool(course['title'].strip()), 'Missing course title')
    objectives = strings(raw.get('learning_objectives', []), 'learning objectives', empty=True)
    if objectives_path:
        objectives += domain_objectives(json.loads(safe_material_path(objectives_path).read_text(encoding='utf-8-sig')))
    units, seen = [], set()
    require(isinstance(raw['units'], list) and raw['units'], 'Teaching units are required')
    for unit in raw['units']:
        require(set(unit) <= {'unit_id', 'title', 'role', 'source_refs', 'content_path',
                             'content_sha256', 'text', 'completeness', 'teaching_scope'},
                'Unknown teaching unit field')
        ident = identifier(unit['unit_id'])
        require(ident not in seen and unit.get('role') == 'teaching_source', 'Duplicate unit or non-teaching role')
        seen.add(ident)
        completeness = unit['completeness']
        require(set(completeness) == {'kind', 'boundary', 'subunits'} and completeness['kind'] in
                ('complete_chapter', 'complete_topic', 'complete_reference_entry', 'external_reference_summary'),
                'A complete material boundary is required')
        require(bool(completeness['boundary'].strip()), 'Missing completeness boundary')
        subunits = completeness['subunits']
        require(isinstance(subunits, list) and subunits, 'Declare all natural material subunits')
        subids = [identifier(s['id']) for s in subunits]
        require(len(subids) == len(set(subids)) and all(set(s) == {'id', 'title'} and s['title'].strip() for s in subunits),
                'Invalid material subunit identity')
        require(isinstance(unit['source_refs'], list) and unit['source_refs'], 'Missing source provenance')
        for ref in unit['source_refs']:
            require(set(ref) <= {'path', 'sha256', 'line_start', 'line_end', 'rem_ids', 'excluded_line_ranges'}, 'Unknown provenance field')
            safe_material_path(ref['path'])
            require(re.fullmatch(r'[a-f0-9]{64}', ref['sha256']), 'Missing source hash')
            if 'line_start' in ref or 'line_end' in ref:
                require(0 < ref['line_start'] <= ref['line_end'], 'Invalid material line range')
            for span in ref.get('excluded_line_ranges', []):
                require(set(span) == {'line_start', 'line_end'} and 0 < span['line_start'] <= span['line_end'],
                        'Invalid excluded source line range')
        if 'text' in unit:
            require('content_path' not in unit, 'Use text or a frozen content file, not both')
            text = unit['text']
        else:
            relative = Path(unit['content_path'])
            require(not relative.is_absolute(), 'Content path must be relative to its manifest')
            content = safe_material_path(path.parent / relative)
            require(content.is_relative_to(path.parent.resolve()), 'Material path escapes its package')
            data = content.read_bytes()
            require(hashlib.sha256(data).hexdigest() == unit['content_sha256'], 'Teaching material bytes changed')
            text = data.decode('utf-8-sig')
        require(isinstance(text, str) and text.strip(), 'Empty teaching material')
        # Provenance remains in the manifest. Authors receive no paths, target
        # identities, answer mappings, or legacy retrieval/selection metadata.
        units.append({'unit_id': ident, 'title': unit['title'], 'role': 'teaching_source',
                      'text': text, 'completeness': deepcopy(completeness),
                      'teaching_scope': deepcopy(strings(unit['teaching_scope'], 'teaching scope'))})
    result = {'schema_version': SCHEMA_VERSION, 'role': 'teaching_materials',
              'course': deepcopy(course), 'learning_objectives': list(dict.fromkeys(objectives)), 'units': units}
    if 'curriculum_units' in raw:
        result['curriculum_units'] = deepcopy(raw['curriculum_units'])
    if 'scope_dependencies' in raw:
        for hint in raw['scope_dependencies']:
            require(set(hint) == {'units', 'prerequisites'}
                    and set(strings(hint['units'], 'dependent source scopes')) <= seen
                    and set(strings(hint['prerequisites'], 'prerequisite source scopes')) <= seen,
                    'Source dependency hints refer to unknown complete material units')
        result['scope_dependencies'] = deepcopy(raw['scope_dependencies'])
    curriculum_units(result)
    return result


def curriculum_units(source):
    groups = source.get('curriculum_units')
    if groups is None:
        return [{'unit_id': u['unit_id'], 'title': u['title'], 'source_unit_ids': [u['unit_id']]}
                for u in source['units']]
    require(isinstance(groups, list) and groups, 'Empty curriculum grouping')
    group_ids, ownership = [], []
    for group in groups:
        require(set(group) == {'unit_id', 'title', 'source_unit_ids'} and group['title'].strip(), 'Unknown curriculum unit field')
        group_ids.append(identifier(group['unit_id']))
        ownership.extend(strings(group['source_unit_ids'], 'curriculum source units'))
    require(len(group_ids) == len(set(group_ids)), 'Duplicate curriculum unit')
    require(len(ownership) == len(set(ownership)) and set(ownership) == {u['unit_id'] for u in source['units']},
            'Every complete source unit must belong to exactly one curriculum unit')
    return deepcopy(groups)


def validate_teaching_payload(source):
    reject_target_metadata(source)
    require({'schema_version', 'role', 'course', 'learning_objectives', 'units'} <= set(source)
            <= {'schema_version', 'role', 'course', 'learning_objectives', 'units', 'curriculum_units', 'scope_dependencies'}
            and source['role'] == 'teaching_materials' and source['schema_version'] == SCHEMA_VERSION,
            'Unexpected author input contract')
    for unit in source['units']:
        require(set(unit) == {'unit_id', 'title', 'role', 'text', 'completeness', 'teaching_scope'}
                and unit['role'] == 'teaching_source', 'Author payload contains non-teaching source metadata')


def unit_payload(source, unit_id):
    validate_teaching_payload(source)
    groups = [g for g in curriculum_units(source) if g['unit_id'] == unit_id]
    require(len(groups) == 1, 'Unknown teaching unit')
    selected = [u for u in source['units'] if u['unit_id'] in groups[0]['source_unit_ids']]
    return {**{k: deepcopy(v) for k, v in source.items() if k not in ('curriculum_units', 'scope_dependencies')}, 'units': deepcopy(selected)}


def substantive_section(title):
    require(not STRUCTURAL_SECTION_NAMES.fullmatch(title.strip()),
            'Introduction must name a substantive concept, not a native structure type')


def natural_subunit_spans(unit):
    """Locate declared headings without guessing ambiguous source boundaries."""
    lines = unit['text'].splitlines(keepends=True)
    normalized = [re.sub(r'^\s*#{1,6}\s*', '', line).strip() for line in lines]
    located = []
    for subunit in unit['completeness']['subunits']:
        matches = [i for i, line in enumerate(normalized) if line == subunit['title'].strip()]
        if len(matches) != 1:
            return {}
        located.append((subunit['id'], matches[0]))
    if (len({start for _, start in located}) != len(located)
            or [start for _, start in located] != sorted(start for _, start in located)):
        return {}
    return {ident: {'line_start': start + 1,
                    'line_end': located[i + 1][1] if i + 1 < len(located) else len(lines),
                    'text': ''.join(lines[start:located[i + 1][1] if i + 1 < len(located) else len(lines)])}
            for i, (ident, start) in enumerate(located)}


def reference_only_subunits(source):
    """A heading with an explicit reference and no body cannot teach its rules.

    Ambiguous headings and substantive bodies never receive this exception.
    The course coordinator must still resolve the actual explanatory provider.
    """
    result = {}
    pointer = re.compile(r'[（(]\s*(?:参看|参见|参阅|详见|见|see\b|refer\s+to\b)[^）)]+[）)]', re.I)
    for unit in source['units']:
        for ident, span in natural_subunit_spans(unit).items():
            nonempty = [line.strip() for line in span['text'].splitlines() if line.strip()]
            if len(nonempty) == 1 and pointer.search(nonempty[0]):
                result[(unit['unit_id'], ident)] = {**span, 'quote': nonempty[0]}
    return result


def validate_plan(plan, source, *, historical=False):
    validate_teaching_payload(source)
    reject_target_metadata(plan)
    require(set(plan) == {'title', 'description', 'topics'}, 'Unexpected curriculum plan fields')
    topics = plan['topics']
    require(isinstance(topics, list) and topics, 'Empty material-derived plan')
    ids = [identifier(t['id']) for t in topics]
    require(len(ids) == len(set(ids)), 'Duplicate Topic identity')
    wanted = {(u['unit_id'], s['id']) for u in source['units'] for s in u['completeness']['subunits']}
    covered, primary, referenced = set(), set(), set()
    objective_sets = set()
    for topic in topics:
        keys = {'id', 'title', 'kind', 'objectives', 'prerequisites', 'coverage', 'sections'}
        if not historical:
            keys.add('boundary_rationale')
        require(set(topic) == keys,
                'Plan cannot contain frozen goal points or hidden author input')
        if not historical:
            require(isinstance(topic['boundary_rationale'], str) and topic['boundary_rationale'].strip(),
                    'Each Topic needs a substantive reusable learning boundary')
        require(topic['kind'] in ('atomic', 'paradigm'), 'Use an existing native author source kind')
        strings(topic['objectives'], 'Topic objectives')
        objective_set = frozenset(text.strip().casefold() for text in topic['objectives'])
        if not historical:
            require(objective_set not in objective_sets,
                    'The same learning objectives cannot be split by training mode or exercise format')
            objective_sets.add(objective_set)
        require(set(topic['prerequisites']) <= set(ids) and topic['id'] not in topic['prerequisites'], 'Unknown prerequisite')
        require(topic['coverage'], 'Missing material scope')
        for row in topic['coverage']:
            coverage_keys = {'unit_id', 'subunit_ids', 'scope'} | (set() if historical else {'use'})
            require(set(row) == coverage_keys and row['scope'].strip(), 'Invalid material coverage')
            if not historical:
                require(row['use'] in ('primary', 'transfer', 'prerequisite'), 'Invalid material ownership role')
            refs = {(row['unit_id'], sid) for sid in strings(row['subunit_ids'], 'material subunits')}
            require(refs <= wanted, 'Coverage refers to an absent material subunit')
            covered.update(refs)
            if not historical and row['use'] == 'primary':
                primary.update(refs)
            elif not historical and row['use'] == 'prerequisite':
                referenced.update(refs)
        sections = topic['sections']
        require(sections and len({s['id'] for s in sections}) == len(sections), 'Missing or duplicate Introduction sections')
        for section in sections:
            identifier(section['id'])
            require(set(section) == {'id', 'title', 'scope'} and section['scope'].strip(), 'Invalid teaching section')
            if not historical:
                substantive_section(section['title'])
    require(covered == wanted, 'Plan omitted a natural material subunit')
    reference_only = set(reference_only_subunits(source))
    require(historical or primary | (referenced & reference_only) == wanted,
            'Each material subunit needs primary teaching ownership; only a proven reference-only heading may use prerequisite coverage')
    parents = {t['id']: t['prerequisites'] for t in topics}
    done = set()
    def visit(ident, path):
        require(ident not in path, 'Cyclic curriculum prerequisites')
        if ident not in done:
            for parent in parents[ident]:
                visit(parent, path | {ident})
            done.add(ident)
    for ident in ids:
        visit(ident, set())


def author_material_selection(source, topic):
    """Keep complete natural sections, their preamble and a lossless source ledger.

    Planning always sees the full source. Selection uses every declared coverage
    role, never sentences or retrieval hits. An uncertain boundary retains the
    entire source unit instead. Whole-unit inputs keep their existing identity.
    """
    wanted = {}
    for row in topic['coverage']:
        wanted.setdefault(row['unit_id'], set()).update(row['subunit_ids'])
    selected, ledger, reduced = [], [], False
    for unit in source['units']:
        if unit['unit_id'] not in wanted:
            continue
        current = deepcopy(unit)
        needed = wanted[unit['unit_id']]
        subunits = unit['completeness']['subunits']
        all_ids = {s['id'] for s in subunits}
        require(needed <= all_ids, 'Author selection refers to an absent natural section')
        lines = unit['text'].splitlines(keepends=True)
        ranges = [{'line_start': 1, 'line_end': len(lines), 'kind': 'complete_source'}]
        mode = 'complete_source'
        if needed != all_ids:
            spans = natural_subunit_spans(unit)
            if set(spans) == all_ids:
                ranges = []
                first = min(span['line_start'] for span in spans.values())
                if first > 1:
                    ranges.append({'line_start': 1, 'line_end': first - 1, 'kind': 'preamble'})
                for subunit in subunits:
                    if subunit['id'] in needed:
                        span = spans[subunit['id']]
                        ranges.append({'line_start': span['line_start'], 'line_end': span['line_end'],
                                       'kind': 'complete_natural_subunit', 'subunit_id': subunit['id']})
                current['text'] = ''.join(''.join(lines[r['line_start'] - 1:r['line_end']]) for r in ranges)
                original = unit['completeness']
                current['completeness'] = {
                    'kind': ('external_reference_summary' if original['kind'] == 'external_reference_summary'
                             else 'complete_topic'),
                    'boundary': ('Complete declared natural sections with the original preamble, selected from '
                                 + original['kind'] + ': ' + original['boundary']),
                    'subunits': [deepcopy(s) for s in subunits if s['id'] in needed]}
                reduced = True
                mode = 'complete_natural_subunits'
            else:
                mode = 'complete_source_boundary_fallback'
        selected.append(current)
        ledger.append({'unit_id': unit['unit_id'], 'mode': mode,
                       'full_completeness': deepcopy(unit['completeness']),
                       'full_source_sha256': hashlib.sha256(unit['text'].encode('utf-8')).hexdigest(),
                       'selected_text_sha256': hashlib.sha256(current['text'].encode('utf-8')).hexdigest(),
                       'coverage_subunit_ids': [s['id'] for s in subunits if s['id'] in needed],
                       'line_ranges': ranges})
    return selected, ledger if reduced else None


def author_payload(source, plan, topic, *, historical=False):
    validate_plan(plan, source, historical=historical)
    require(topic in plan['topics'], 'Topic does not belong to the material-derived plan')
    if historical:
        material_ids = {c['unit_id'] for c in topic['coverage']}
        materials = [deepcopy(u) for u in source['units'] if u['unit_id'] in material_ids]
        selection = None
    else:
        materials, selection = author_material_selection(source, topic)
    result = {'teaching': {**deepcopy(source), 'units': materials}, 'topic': deepcopy(topic)}
    if not historical:
        result['curriculum_plan'] = deepcopy(plan)
    if selection is not None:
        result['source_selection'] = selection
    reject_target_metadata(result)
    return result


def resolve_anchors(introduction, anchors):
    """Resolve exact author quotations; never append text to make a match."""
    require(isinstance(anchors, list) and anchors, 'Missing real Introduction anchor quotations')
    result = []
    for anchor in anchors:
        require(set(anchor) <= {'quote', 'occurrence', 'start', 'end'}, 'Unknown anchor field')
        quote = anchor['quote']
        require(isinstance(quote, str) and quote, 'Empty Introduction anchor')
        matches = [m.start() for m in re.finditer(re.escape(quote), introduction)]
        require(matches, 'Anchor quotation is absent from the original Introduction')
        if 'start' in anchor or 'end' in anchor:
            start, end = anchor['start'], anchor['end']
            require(isinstance(start, int) and isinstance(end, int) and start >= 0
                    and introduction[start:end] == quote and end - start == len(quote), 'Invalid original anchor offsets')
        else:
            occurrence = anchor.get('occurrence', 1)
            require(type(occurrence) is int and 1 <= occurrence <= len(matches)
                    and (len(matches) == 1 or 'occurrence' in anchor), 'Ambiguous Introduction anchor needs an explicit occurrence')
            start = matches[occurrence - 1]; end = start + len(quote)
        result.append({'start': start, 'end': end, 'quote': quote})
    return result


def project_atomic(candidate, ident, *, approved=False):
    projected = deepcopy(candidate)
    projected['id'] = identifier(ident)
    for section in projected['sections']:
        intro = section['introduction']
        text = plain_text(intro['html'])
        require('plain_text' not in intro or intro['plain_text'] == text, 'Introduction plain text differs from authored HTML')
        intro['plain_text'] = text
        for chunk in section['memory_chunks']:
            chunk['anchors'] = resolve_anchors(text, chunk['anchors'])
        # Approval is metadata added only by the exporting caller after proof
        # verification; it never alters the authored Introduction or entities.
        if approved:
            section.update(status='ai_passed', import_eligible=True)
    return projected


def project_paradigm(candidate, ident):
    document = {**deepcopy(candidate), 'id': identifier(ident)}
    normalized, ignored = normalize_document(document)
    require(not ignored, 'Native source projection would discard authored nodes')
    validate_field_placement(normalized)
    return normalized


def validate_author(candidate, topic, *, historical=False):
    require(candidate['id'] == topic['id'] and candidate['title'].strip(), 'Changed Topic identity or empty title')
    wanted = [s['id'] for s in topic['sections']]
    if topic['kind'] == 'paradigm':
        normalized = project_paradigm(candidate, candidate['id'])
        intros = [n['id'] for n in normalized['nodes'] if n['text'].split(':', 1)[0].strip() == 'Introduction']
        require(intros == wanted, 'Native Introduction order or identity changed')
        if not historical:
            for node in normalized['nodes']:
                if node['id'] in wanted:
                    substantive_section(node['text'].split(':', 1)[-1])
        examples = [n for n in normalized['nodes'] if n['text'].startswith('Example ')]
        require(examples, 'Paradigm Topic has no Example')
        for example in examples:
            practices = [n for n in example['children'] if n['text'].startswith('Practice ')]
            require(len(practices) >= 7, 'Existing adaptive queue requires at least seven Practice per Example')
        return
    require(set(candidate) == {'id', 'title', 'sections'}, 'Unexpected atomic author fields')
    require([s['id'] for s in candidate['sections']] == wanted, 'Native Introduction order or identity changed')
    projected = project_atomic(candidate, candidate['id'])
    points, chunks, cards, singles = set(), set(), set(), set()
    for section in projected['sections']:
        if not historical:
            substantive_section(section['section_title'])
        require(set(section) <= {'id', 'section_title', 'introduction', 'memory_chunks', 'memory_points',
                                 'flashcards', 'status', 'import_eligible'}, 'Unknown native section field')
        require(section['section_title'].strip() and section['introduction']['plain_text'].strip(), 'Empty Introduction')
        rendered = ''.join(SourceHTML(section['introduction']['html'], {}).parts)
        require(plain_text(rendered) == section['introduction']['plain_text'], 'Introduction text changed in native sanitizer')
        local_chunks = {identifier(c['id']) for c in section['memory_chunks']}
        require(len(local_chunks) == len(section['memory_chunks']) and not chunks & local_chunks, 'Duplicate memory chunk')
        chunks.update(local_chunks)
        local_points = {identifier(p['id']) for p in section['memory_points']}
        require(len(local_points) == len(section['memory_points']) and not points & local_points, 'Duplicate memory point')
        points.update(local_points)
        for chunk in section['memory_chunks']:
            require(chunk['content'].strip() and any(p['chunk_id'] == chunk['id'] for p in section['memory_points']), 'Empty memory chunk')
        for point in section['memory_points']:
            require(set(point) == {'id', 'chunk_id', 'content'} and point['chunk_id'] in local_chunks
                    and point['content'].strip(), 'Unbound memory point')
        for card in section['flashcards']:
            require(identifier(card['id']) not in cards and card['point_ids']
                    and len(set(card['point_ids'])) == len(card['point_ids'])
                    and set(card['point_ids']) <= local_points, 'Invalid Flashcard identity or membership')
            cards.add(card['id'])
            require(all(card[k].strip() for k in ('question', 'reference_answer', 'explanation')), 'Incomplete Flashcard')
            validate_interaction(card['interaction'], card['question']); validate_assessment(card)
            phases = card.get('learning_phases', ['learn', 'review'])
            require(phases and len(phases) == len(set(phases)) and set(phases) <= {'learn', 'review'}, 'Invalid Flashcard phases')
            if len(card['point_ids']) == 1 and 'learn' in phases:
                singles.update(card['point_ids'])
    require(points and singles == points, 'Every point needs an initial single-point Flashcard')


def point_catalog(candidate, topic_id):
    return [{'point_ref': topic_id + '/' + point['id'], 'topic_id': topic_id,
             'topic_title': candidate['title'], 'introduction_id': section['id'],
             'chunk_id': point['chunk_id'], 'content': point['content']}
            for section in candidate.get('sections', []) for point in section['memory_points']]


def readable_topic(candidate, kind):
    """Type-preserving review view, explicitly separate from native JSON source."""
    lines = ['# ' + candidate['title'], '']
    if kind == 'paradigm':
        def visit(nodes, depth):
            for node in nodes:
                lines.append('  ' * depth + '- ' + node['text'].replace('\n', '\n' + '  ' * (depth + 1)))
                visit(node['children'], depth + 1)
        visit(candidate['nodes'], 0)
        return '\n'.join(lines) + '\n'
    for section in candidate['sections']:
        lines.extend(['## Introduction · ' + section['section_title'], '', section['introduction']['html'], ''])
        by_point = {p['id']: p for p in section['memory_points']}
        for chunk in section['memory_chunks']:
            lines.extend(['### 记忆组块 · ' + chunk.get('title', chunk['content']), '', chunk['content'], ''])
            mnemonic = chunk.get('mnemonic', {}).get('source_quotes', [])
            if mnemonic:
                lines.extend(['**助记**', '', *mnemonic, ''])
            lines.extend(['**Introduction 对应原文**', '', *['> ' + a['quote'].replace('\n', '\n> ') for a in chunk['anchors']], ''])
            for point in section['memory_points']:
                if point['chunk_id'] == chunk['id']:
                    lines.extend(['#### 记忆点', '', point['content'], ''])
                    for card in section['flashcards']:
                        if card['point_ids'] == [point['id']]:
                            lines.extend(_readable_card(card, 5))
        for card in section['flashcards']:
            if len(card['point_ids']) > 1:
                lines.extend(['### 打包 Flashcard', '', '**关联记忆点**', '',
                              *['- ' + by_point[p]['content'] for p in card['point_ids']], ''])
                lines.extend(_readable_card(card, 4))
    return '\n'.join(lines) + '\n'


def _readable_card(card, depth):
    return ['#' * depth + ' Flashcard · ' + card.get('title', '复现练习'), '',
            card['question'], '', '**答案**', '', card['reference_answer'], '',
            '**Explanation**', '', card['explanation'], '']
