"""Source-neutral course coordination over immutable concept plans."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import combinations, combinations_with_replacement, product
import json

from graspmemoedu.context_production import digest
from course_authoring_contract import (curriculum_units, natural_subunit_spans, reference_only_subunits,
    reject_target_metadata, require, safe_material_path, strings)

RELATIONS = """Coordinate this complete course's existing concept plans before authoring.
You receive teaching plans and neutral source-scope metadata, never a target
exercise. Identify necessary prerequisites and primary teaching ownership across
curriculum units. Source dependency hints are soft topic-area hints, never edges.
A prerequisite is necessary only when the consumer cannot understand or complete
its declared objective without the provider's specific knowledge, and the consumer
does not teach that knowledge itself. Adjacency, shared words, textbook order,
general relevance and a desire for a connected graph are not sufficient.
Name the particular knowledge and its role in the consumer objective. Use existing
Topic references only, preserve valid local prerequisites, avoid redundant
transitive edges, and never make every Topic in one chapter a prerequisite of
every Topic in another. Do not invent compressed bridge Topics or question-format
Topics. Independent learning objectives may correctly remain independent.
Choose one primary home for each shared foundational explanation. Consumers may
apply, contrast or briefly recap that knowledge without duplicating foundational
memory points/cards. New lexical senses, valency, collocation restrictions and new
contrasts retain independent value. Reuse is author context, not automatically a
hard prerequisite and never a cross-Topic point_ids capability.
If two plans both treat the same concept as a primary objective, report a scope
conflict with concrete instructions for replanning the affected original material
units. Do not hide the overlap with an edge or silently rewrite plan candidates.
An unresolved relation that prevents reliable authorship is also a conflict. Keep
unaffected plans reusable. Do not demand arbitrary counts or repeated reviews.
Return exactly {prerequisites:[{from:topic_ref,to:topic_ref,knowledge:string,
reason:string}],reuse:[{consumer:topic_ref,provider:topic_ref,knowledge:[string],
use:'application'|'contrast'|'recap',reason:string}],conflicts:[{unit_ids:[unit_id],
topic_refs:[topic_ref],issue:string,repair_instructions:[string]}]}.
Use full supplied references in the form curriculum_unit_id/local_topic_id.
Return empty conflicts only when teaching responsibilities are consistent.
In each conflict, topic_refs identifies all relevant existing Topics, including
providers that remain unchanged. unit_ids names only the original material units
whose plans actually need revision; do not replan an already valid provider.
"""

RELATIONS += """Some source subunits are only cross-reference headings with no explanatory body.
Their exact source evidence and any independently verified explanatory passages
are supplied separately. Never invent a rule from such a heading or treat its
local primary-coverage claim as proof that the explanation exists. Report a
replanning conflict if a plan still claims primary teaching from that heading;
the revised consumer may reference the real provider instead. Pure transfer
practice does not establish teaching ownership. Resolve every prerequisite-only
reference to a provider Topic whose primary coverage includes the actual detailed
source. If no such provider exists, report the affected source-unit conflict for
replanning. A book cross-reference alone never creates a hard prerequisite edge.
Also return reference_resolutions:[{source:{unit_id,subunit_id},consumer:topic_ref,
provider:topic_ref,provider_source:{unit_id,subunit_ids:[id]},knowledge:string,
reason:string}]. Each resolution must use the supplied source evidence, name the
precise knowledge, and agree with the existing primary coverage. An unresolved
reference must instead appear in conflicts. Do not silently rewrite plans.
"""

RELATIONS += """The complete unit plans use a reversible column-table encoding.
plan_tables.columns declares the fields of each unit, topic, coverage and section
row; nested row arrays retain their original order. All objectives, boundary
reasons, coverage scopes and detailed Introduction section scopes are included
verbatim. This is not a semantic summary. Read every row using its declared
columns, and use the actual unit_id/topic id strings for returned references.
Never infer a missing responsibility from a title. If the supplied full plans
and exact source evidence cannot resolve it, report a concrete conflict.
"""

PLAN_TABLE_COLUMNS = {
    'unit': ['unit_id', 'title', 'source_unit_ids', 'plan_title', 'plan_description', 'topics'],
    'topic': ['id', 'title', 'kind', 'boundary_rationale', 'objectives', 'prerequisites', 'coverage', 'sections'],
    'coverage': ['unit_id', 'subunit_ids', 'use', 'scope'],
    'section': ['id', 'title', 'scope'],
}

COORDINATION_PARTITION_POLICY = 'complete-unit-pairs-v1'
BATCH_RELATIONS = RELATIONS + """This call is one bounded part of course coordination.
batch_focus.unit_pairs assigns the curriculum-unit pairs to this call. Every
possible pair, including a unit with itself, is assigned to exactly one call.
You receive the COMPLETE plans of all units named in batch_focus.unit_ids.
Evaluate relationships only for the assigned pairs. Return prerequisites, reuse
and reference resolutions only when their two Topic owners form an assigned
pair. Report a single-unit conflict only if that unit's self-pair is assigned;
report a cross-unit conflict for one assigned pair at a time. Other calls cover
the remaining pairs; absence of an external unit from this batch is not a gap in
the course. Do not invent its plan or report a missing external provider solely
because it is outside this batch. The final mechanical merge checks the complete
course, real explanatory providers and all cycles before authors are dispatched.
For a reference-only heading, return a provider resolution only when its complete
plan is present; a local false primary claim still requires its assigned self-pair
conflict. Keep all decisions grounded in the detailed supplied scopes. Ordinary
application or a brief contextual reminder is not automatically duplicate primary
teaching; detailed new lexical meanings and specialist extensions retain value.
"""

RELATIONS_BEFORE_TOPIC_OWNERS = RELATIONS
BATCH_RELATIONS_BEFORE_TOPIC_OWNERS = BATCH_RELATIONS


def _explicit_conflict_identity(prompt):
    return prompt.replace('conflicts:[{unit_ids:[unit_id],\ntopic_refs:[topic_ref]',
                          'conflicts:[{affected_topic_refs:[topic_ref],\ntopic_refs:[topic_ref]').replace(
        'providers that remain unchanged. unit_ids names only the original material units\n'
        'whose plans actually need revision; do not replan an already valid provider.',
        'providers that remain unchanged. affected_topic_refs names only the existing\n'
        'Topics whose plans actually need revision; do not select an unchanged provider.') + """
There are two different identity domains in the input. A curriculum plan's
unit_id (the FIRST field of each plan_tables unit row) owns its Topic references.
source_unit_ids (the THIRD field) identifies reference material, not course plans.
For conflicts, return only exact affected_topic_refs and topic_refs from those
existing curriculum plans. affected_topic_refs must be a nonempty subset of
topic_refs. Do not return unit_ids, source IDs, source paths, or guessed aliases
as conflict identities. The workflow mechanically derives affected curriculum
groups from the selected Topic references. Source IDs remain valid only in
source coverage and source-reference evidence fields.
"""


RELATIONS = _explicit_conflict_identity(RELATIONS)
BATCH_RELATIONS = _explicit_conflict_identity(BATCH_RELATIONS)
RELATIONS_BEFORE_REFERENCE_CASES = RELATIONS
BATCH_RELATIONS_BEFORE_REFERENCE_CASES = BATCH_RELATIONS
REFERENCE_RESOLUTION_RULES = """The reference_resolution_cases list is a mechanical inventory of
verified EMPTY-BODY source headings and their CURRENT plan coverage. Only these
source/consumer pairs can appear in reference_resolutions. It is not an inventory
of all prerequisite coverage. Ordinary reuse of material with explanatory text
belongs in reuse, not reference_resolutions. Never return a self-reference.
A case whose current_coverage includes primary needs a real planning conflict;
do not pretend that the consumer has already been revised to prerequisite.
Only disposition=resolve_current_prerequisite permits a resolution. Its listed
provider_candidates have current primary coverage of the independently verified
detailed source; still check their full scopes for the specific knowledge.
An absent provider in this bounded batch is not proof of a course-wide gap.
Do not add a hypothetical resolution alongside a conflict that would first have
to change the consumer's current coverage. Keep the original plans unchanged.
"""
RELATIONS += REFERENCE_RESOLUTION_RULES
BATCH_RELATIONS += REFERENCE_RESOLUTION_RULES


def project_coordination_response(value, plans, *, legacy=False):
    """Derive group ownership from explicit real Topic refs; retain raw evidence."""
    require(isinstance(value, dict) and isinstance(value.get('conflicts'), list),
            'A coordination response needs a conflicts list')
    result = deepcopy(value)
    topics = topic_index(plans)
    for conflict in result['conflicts']:
        if legacy:
            require('affected_topic_refs' not in conflict, 'Legacy coordination conflict contract changed')
            continue
        require(set(conflict) == {'affected_topic_refs', 'topic_refs', 'issue', 'repair_instructions'},
                'Return affected_topic_refs, not freely generated curriculum/source unit_ids')
        affected = strings(conflict['affected_topic_refs'], 'affected Topic references')
        related = strings(conflict['topic_refs'], 'related Topic references')
        require(set(affected) <= set(related) <= set(topics), 'Conflict contains an unknown or unrelated Topic reference')
        conflict['unit_ids'] = sorted({ref.split('/')[0] for ref in affected})
    return result


def coordination_request_chars(payload, prompt=RELATIONS):
    """Measure the serialized request, not an invented model-token count."""
    messages = [{'role': 'system', 'content': prompt},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    return len(json.dumps(messages, ensure_ascii=False))


def _pair(left, right):
    return tuple(sorted((left, right)))


def coordination_batch_payload(full, unit_ids, focus_pairs):
    """Select complete existing plans; never shorten any objective or scope."""
    selected = set(unit_ids)
    plans = [deepcopy(row) for row in full['unit_plans'] if row['unit_id'] in selected]
    require({row['unit_id'] for row in plans} == selected, 'Unknown coordination batch unit')
    source_ids = {ident for row in plans for ident in row['source_unit_ids']}
    selected_keys = {'unit_plans', 'source_scopes', 'reference_only_subunits', 'reference_resolution_cases',
                     'source_reference_findings', 'source_dependency_hints'}
    result = {key: deepcopy(value) for key, value in full.items() if key not in selected_keys}
    result['unit_plans'] = plans
    result['source_scopes'] = [deepcopy(row) for row in full['source_scopes'] if row['unit_id'] in source_ids]
    result['reference_only_subunits'] = [deepcopy(row) for row in full['reference_only_subunits']
                                         if row['unit_id'] in source_ids]
    result['source_reference_findings'] = [deepcopy(row) for row in full['source_reference_findings']
                                           if row['source']['unit_id'] in source_ids]
    result['source_dependency_hints'] = [deepcopy(row) for row in full['source_dependency_hints']
                                         if source_ids.intersection(row['units'])]
    result['batch_focus'] = {'unit_ids': [row['unit_id'] for row in plans],
        'unit_pairs': [list(pair) for pair in sorted(focus_pairs)]}
    result['reference_resolution_cases'] = reference_resolution_cases(result)
    return pack_coordination_payload(result)


def partition_coordination(full, max_request_chars):
    """Cover every unit pair once using bounded unions of complete unit plans.

    Contiguous groups retain the material catalog's natural grouping order.
    Capacity only changes which complete groups share a call, never their text.
    """
    require(type(max_request_chars) is int and max_request_chars > 0, 'Positive coordination capacity required')
    units = [row['unit_id'] for row in full['unit_plans']]
    require(units and len(units) == len(set(units)), 'Unique complete coordination units required')

    def batches(bins):
        rows, internal_done = [], set()
        indices = list(combinations(range(len(bins)), 2)) if len(bins) > 1 else [(0, 0)]
        for a, b in indices:
            focus = {_pair(x, y) for x, y in product(bins[a], bins[b])} if a != b else set()
            for index in (a, b):
                if index not in internal_done:
                    focus.update(_pair(x, y) for x, y in combinations_with_replacement(bins[index], 2))
                    internal_done.add(index)
            selected = [unit for unit in units if unit in set(bins[a] + bins[b])]
            payload = coordination_batch_payload(full, selected, focus)
            rows.append({'bins': (a, b), 'unit_ids': selected, 'focus_pairs': sorted(focus),
                         'input': payload, 'request_chars': coordination_request_chars(payload, BATCH_RELATIONS)})
        return rows

    # Fill in catalog order by measured payload size. Splitting by unit count
    # over-fragments catalogs whose complete source scopes vary substantially.
    bins, current = [], []
    for unit in units:
        candidate = current + [unit]
        focus = {_pair(a, b) for a, b in combinations_with_replacement(candidate, 2)}
        size = coordination_request_chars(coordination_batch_payload(full, candidate, focus), BATCH_RELATIONS)
        if current and size > max_request_chars // 2:
            bins.append(current)
            current = [unit]
        else:
            current = candidate
    if current:
        bins.append(current)
    while True:
        rows = batches(bins)
        oversized = next((row for row in rows if row['request_chars'] > max_request_chars), None)
        if oversized is None:
            break
        candidates = {index for index in oversized['bins'] if len(bins[index]) > 1}
        require(candidates, 'Two complete unit plans exceed coordination capacity; do not truncate them')
        index = max(candidates, key=lambda i: sum(len(json.dumps(row, ensure_ascii=False))
            for row in full['unit_plans'] if row['unit_id'] in bins[i]))
        cut = len(bins[index]) // 2
        bins[index:index + 1] = [bins[index][:cut], bins[index][cut:]]
    seen = set()
    records, inputs = [], {}
    for index, row in enumerate(rows, 1):
        focus = set(row['focus_pairs'])
        require(not seen.intersection(focus), 'A coordination pair has multiple owners')
        seen.update(focus)
        ident = 'batch-' + str(index).zfill(3)
        records.append({'id': ident, 'unit_ids': row['unit_ids'],
            'focus_pairs': [list(pair) for pair in row['focus_pairs']],
            'input_sha256': digest(row['input']), 'request_chars': row['request_chars']})
        inputs[ident] = row['input']
    require(seen == {_pair(a, b) for a, b in combinations_with_replacement(units, 2)},
            'Course coordination omitted a possible unit relationship')
    partition = {'policy': COORDINATION_PARTITION_POLICY, 'full_input_sha256': digest(full),
                 'prompt_sha256': digest(BATCH_RELATIONS), 'max_request_chars': max_request_chars,
                 'batches': records}
    require(coordination_partition_inputs(full, partition) == inputs, 'Generated coordination partition differs')
    return partition, inputs


def coordination_partition_inputs(full, partition):
    """Validate a frozen partition independently of the packing heuristic."""
    require(set(partition) == {'policy', 'full_input_sha256', 'prompt_sha256', 'max_request_chars', 'batches'}
            and partition['policy'] == COORDINATION_PARTITION_POLICY
            and partition['full_input_sha256'] == digest(full)
            and partition['prompt_sha256'] == digest(BATCH_RELATIONS), 'Coordination partition binding changed')
    limit = partition['max_request_chars']
    require(type(limit) is int and limit > 0, 'Positive coordination capacity required')
    units = {row['unit_id'] for row in full['unit_plans']}
    seen, result = set(), {}
    for batch in partition['batches']:
        require(set(batch) == {'id', 'unit_ids', 'focus_pairs', 'input_sha256', 'request_chars'}
                and batch['id'] not in result, 'Invalid or duplicate coordination batch')
        ids = batch['unit_ids']
        require(isinstance(ids, list) and ids and len(ids) == len(set(ids)) and set(ids) <= units,
                'Invalid complete batch units')
        pairs = [tuple(pair) for pair in batch['focus_pairs']]
        require(pairs and all(len(pair) == 2 and set(pair) <= set(ids) and pair == _pair(*pair) for pair in pairs)
                and len(pairs) == len(set(pairs)) and not seen.intersection(pairs),
                'Invalid, duplicate or overlapping coordination pairs')
        seen.update(pairs)
        payload = coordination_batch_payload(full, ids, pairs)
        size = coordination_request_chars(payload, BATCH_RELATIONS)
        require(batch['input_sha256'] == digest(payload) and batch['request_chars'] == size and size <= limit,
                'Coordination batch text or capacity changed')
        result[batch['id']] = payload
    require(seen == {_pair(a, b) for a, b in combinations_with_replacement(sorted(units), 2)},
            'Course coordination omitted a possible unit relationship')
    return result


def validate_batch_relations(value, payload, source=None, *, response_contract=None):
    full = unpack_coordination_payload(payload)
    plans = {row['unit_id']: row['plan'] for row in full['unit_plans']}
    if response_contract is not None:
        value = project_coordination_response(value, plans, legacy=response_contract == 'legacy')
    validate_relations(value, plans)
    focus = {tuple(pair) for pair in full['batch_focus']['unit_pairs']}
    def assigned(left, right):
        require(_pair(left.split('/')[0], right.split('/')[0]) in focus,
                'Relation belongs to another coordination batch')
    for row in value['prerequisites']:
        assigned(row['from'], row['to'])
    for row in value['reuse']:
        assigned(row['consumer'], row['provider'])
    for row in value.get('reference_resolutions', []):
        assigned(row['consumer'], row['provider'])
    for row in value['conflicts']:
        owners = sorted({ref.split('/')[0] for ref in row['topic_refs']})
        require(len(owners) <= 2 and _pair(owners[0], owners[-1]) in focus,
                'Conflict must use one assigned unit pair')
    if source is not None:
        validate_reference_resolutions(value, plans, source, full['source_reference_findings'], focus_pairs=focus)
    return value


def combine_coordination(partition, inputs, results, plans, source, findings):
    """Mechanical union with item provenance; never choose between decisions."""
    rebuilt = coordination_partition_inputs(coordination_payload(source, plans, findings), partition)
    require(inputs == rebuilt, 'Coordination partition or complete plans changed')
    require(set(results) == set(inputs), 'Incomplete coordination results')
    combined = {'prerequisites': [], 'reuse': [], 'conflicts': [], 'reference_resolutions': []}
    origins = {key: {} for key in combined}
    for batch in partition['batches']:
        ident = batch['id']
        value = validate_batch_relations(results[ident], inputs[ident], source)
        for key in combined:
            for row in value.get(key, []):
                row_hash = digest(row)
                if row_hash not in origins[key]:
                    combined[key].append(deepcopy(row))
                    origins[key][row_hash] = []
                origins[key][row_hash].append(ident)
    validate_relations(combined, plans, source, findings)
    return combined, origins


def pack_coordination_payload(payload):
    """Remove repeated field names, never any teaching text or objective."""
    def row(kind, value):
        require(set(value) == set(PLAN_TABLE_COLUMNS[kind]), 'Unexpected coordination table fields')
        return [deepcopy(value[key]) for key in PLAN_TABLE_COLUMNS[kind]]
    units = []
    for item in payload['unit_plans']:
        require(set(item) == {'unit_id', 'title', 'source_unit_ids', 'plan'}, 'Unknown coordinated unit fields')
        plan = item['plan']
        require(set(plan) == {'title', 'description', 'topics'}, 'Unknown complete plan fields')
        topics = []
        for topic in plan['topics']:
            encoded = deepcopy(topic)
            encoded['coverage'] = [row('coverage', c) for c in topic['coverage']]
            encoded['sections'] = [row('section', s) for s in topic['sections']]
            topics.append(row('topic', encoded))
        units.append(row('unit', {'unit_id': item['unit_id'], 'title': item['title'],
            'source_unit_ids': item['source_unit_ids'], 'plan_title': plan['title'],
            'plan_description': plan['description'], 'topics': topics}))
    packed = {key: deepcopy(value) for key, value in payload.items() if key != 'unit_plans'}
    packed.update(full_input_sha256=digest(payload),
                  plan_tables={'columns': deepcopy(PLAN_TABLE_COLUMNS), 'rows': units})
    require(unpack_coordination_payload(packed) == payload, 'Coordination table changed the full input')
    return packed


def unpack_coordination_payload(packed):
    tables = packed['plan_tables']
    require(set(tables) == {'columns', 'rows'} and tables['columns'] == PLAN_TABLE_COLUMNS,
            'Unknown coordination table schema')
    def record(kind, values):
        require(isinstance(values, list) and len(values) == len(PLAN_TABLE_COLUMNS[kind]),
                'Invalid coordination table row')
        return dict(zip(PLAN_TABLE_COLUMNS[kind], deepcopy(values)))
    units = []
    for values in tables['rows']:
        unit = record('unit', values)
        topics = []
        for values in unit['topics']:
            topic = record('topic', values)
            topic['coverage'] = [record('coverage', c) for c in topic['coverage']]
            topic['sections'] = [record('section', s) for s in topic['sections']]
            topics.append(topic)
        units.append({'unit_id': unit['unit_id'], 'title': unit['title'], 'source_unit_ids': unit['source_unit_ids'],
                      'plan': {'title': unit['plan_title'], 'description': unit['plan_description'], 'topics': topics}})
    payload = {key: deepcopy(value) for key, value in packed.items() if key not in ('full_input_sha256', 'plan_tables')}
    payload['unit_plans'] = units
    require(digest(payload) == packed['full_input_sha256'], 'Restored coordination input hash mismatch')
    return payload


def load_source_findings(paths, source):
    """Project independently recorded source observations, never approvals."""
    findings = []
    units = {u['unit_id']: u for u in source['units']}
    for path in paths:
        data = safe_material_path(path).read_bytes()
        report = json.loads(data.decode('utf-8-sig'))
        reject_target_metadata(report)
        for observation in report['required_cross_source_resolution']:
            ref = observation['reference_source']
            detailed = observation['resolved_explanatory_source']
            require(ref['unit_id'] in units and detailed['unit_id'] in units, 'Unknown source evidence unit')
            text = units[detailed['unit_id']]['text'].splitlines()
            findings.append({'source': {'unit_id': ref['unit_id'], 'subunit_id': ref['subunit_id']},
                'reference_quote': ref['quote'], 'reference_source_sha256': ref['content_sha256'],
                'explanation': {'unit_id': detailed['unit_id'], 'subunit_id': detailed['subunit_id'],
                    'line_start': detailed['line_start'], 'line_end': detailed['line_end'],
                    'text': '\n'.join(text[detailed['line_start'] - 1:detailed['line_end']]),
                    'source_sha256': detailed['content_sha256']},
                'report_sha256': hashlib.sha256(data).hexdigest()})
    return validate_source_findings(findings, source)


def validate_source_findings(findings, source):
    reject_target_metadata(findings)
    units = {u['unit_id']: u for u in source['units']}
    references = reference_only_subunits(source)
    seen = set()
    for finding in findings:
        require(set(finding) == {'source', 'reference_quote', 'reference_source_sha256', 'explanation', 'report_sha256'},
                'Unknown source evidence fields')
        require(isinstance(finding['report_sha256'], str) and len(finding['report_sha256']) == 64
                and all(c in '0123456789abcdef' for c in finding['report_sha256']), 'Missing source-observation hash')
        require(set(finding['source']) == {'unit_id', 'subunit_id'}, 'Invalid reference source')
        key = (finding['source']['unit_id'], finding['source']['subunit_id'])
        require(key in references and key not in seen, 'Source evidence is not a unique reference-only subunit')
        seen.add(key)
        require(finding['reference_quote'] == references[key]['quote'], 'Reference quote differs from complete source')
        require(hashlib.sha256(units[key[0]]['text'].encode('utf-8')).hexdigest() == finding['reference_source_sha256'],
                'Reference evidence source changed')
        detailed = finding['explanation']
        require(set(detailed) == {'unit_id', 'subunit_id', 'line_start', 'line_end', 'text', 'source_sha256'}
                and detailed['unit_id'] in units, 'Invalid explanatory source evidence')
        unit = units[detailed['unit_id']]
        span = natural_subunit_spans(unit).get(detailed['subunit_id'])
        require(span and span['line_start'] <= detailed['line_start'] <= detailed['line_end'] <= span['line_end'],
                'Explanatory evidence is outside its complete natural subunit')
        require((detailed['unit_id'], detailed['subunit_id']) not in references
                and len([line for line in detailed['text'].splitlines() if line.strip()]) > 1,
                'A reference-only heading is not an explanatory provider')
        require(detailed['text'] == '\n'.join(unit['text'].splitlines()[detailed['line_start'] - 1:detailed['line_end']])
                and hashlib.sha256(unit['text'].encode('utf-8')).hexdigest() == detailed['source_sha256'],
                'Explanatory evidence differs from complete source')
    return deepcopy(findings)


def topic_index(plans):
    result = {}
    for unit_id, plan in plans.items():
        for topic in plan['topics']:
            ref = unit_id + '/' + topic['id']
            require(ref not in result, 'Duplicate course Topic reference')
            result[ref] = topic
    return result


def reference_resolution_cases(full):
    """List current eligible source references without deciding teaching edges."""
    topics = topic_index({row['unit_id']: row['plan'] for row in full['unit_plans']})
    references = {(row['unit_id'], row['subunit_id']) for row in full['reference_only_subunits']}
    details = {(row['source']['unit_id'], row['source']['subunit_id']):
               (row['explanation']['unit_id'], row['explanation']['subunit_id'])
               for row in full['source_reference_findings']}
    focus = {tuple(pair) for pair in full['batch_focus']['unit_pairs']} if 'batch_focus' in full else None
    cases = []
    for consumer, topic in topics.items():
        for unit, subunit in sorted(references):
            uses = sorted({c['use'] for c in topic['coverage']
                           if c['unit_id'] == unit and subunit in c['subunit_ids']})
            if not uses:
                continue
            detailed = details.get((unit, subunit))
            providers = []
            for provider, other in topics.items():
                primary = {(c['unit_id'], sid) for c in other['coverage'] if c['use'] == 'primary'
                           for sid in c['subunit_ids']}
                if detailed and detailed in primary and provider != consumer and (
                        focus is None or _pair(consumer.split('/')[0], provider.split('/')[0]) in focus):
                    providers.append({'provider': provider,
                        'provider_source': {'unit_id': detailed[0], 'subunit_ids': [detailed[1]]}})
            cases.append({'source': {'unit_id': unit, 'subunit_id': subunit}, 'consumer': consumer,
                'current_coverage': uses,
                'disposition': ('replan_primary_claim' if 'primary' in uses else
                                'resolve_current_prerequisite' if 'prerequisite' in uses else 'not_a_resolution'),
                'verified_provider_source': ({'unit_id': detailed[0], 'subunit_ids': [detailed[1]]}
                                             if detailed else None),
                'provider_candidates': providers})
    return cases


def coordination_before_reference_cases(payload):
    """Recover the exact older input; only remove this deterministic inventory."""
    full = unpack_coordination_payload(payload)
    if 'reference_resolution_cases' in full:
        require(full['reference_resolution_cases'] == reference_resolution_cases(full),
                'Derived reference-resolution inventory changed')
        full.pop('reference_resolution_cases')
    return pack_coordination_payload(full)


def coordination_payload(source, plans, source_findings=None):
    groups = curriculum_units(source)
    require(set(plans) == {g['unit_id'] for g in groups}, 'All material-unit plans are required for course coordination')
    result = {'course': deepcopy(source['course']),
        'source_scopes': [{k: deepcopy(u[k]) for k in ('unit_id', 'title', 'completeness', 'teaching_scope')}
                          for u in source['units']],
        'unit_plans': [{**g, 'plan': deepcopy(plans[g['unit_id']])} for g in groups],
        'source_dependency_hints': deepcopy(source.get('scope_dependencies', [])),
        'reference_only_subunits': [{'unit_id': unit, 'subunit_id': subunit, **span}
            for (unit, subunit), span in reference_only_subunits(source).items()],
        'source_reference_findings': validate_source_findings(source_findings or [], source)}
    result['reference_resolution_cases'] = reference_resolution_cases(result)
    reject_target_metadata(result)
    return result


def validate_relations(value, plans, source=None, source_findings=None):
    reject_target_metadata(value)
    require({'prerequisites', 'reuse', 'conflicts'} <= set(value)
            <= {'prerequisites', 'reuse', 'conflicts', 'reference_resolutions'}, 'Unknown course-relation fields')
    require(all(isinstance(value[k], list) for k in value), 'Course relations must be lists')
    topics = topic_index(plans)
    edges = {(unit_id + '/' + p, unit_id + '/' + topic['id'])
             for unit_id, plan in plans.items() for topic in plan['topics'] for p in topic['prerequisites']}
    declared = set()
    for edge in value['prerequisites']:
        require(set(edge) == {'from', 'to', 'knowledge', 'reason'} and edge['knowledge'].strip()
                and edge['reason'].strip(), 'A course prerequisite needs its concrete knowledge and purpose')
        pair = (edge['from'], edge['to'])
        require(set(pair) <= set(topics) and pair[0] != pair[1], 'Unknown or self-referencing course prerequisite')
        require(pair not in declared, 'Duplicate course prerequisite')
        declared.add(pair); edges.add(pair)
    seen_reuse = set()
    for reuse in value['reuse']:
        require(set(reuse) == {'consumer', 'provider', 'knowledge', 'use', 'reason'}
                and reuse['use'] in ('application', 'contrast', 'recap') and reuse['reason'].strip(),
                'Invalid curriculum reuse record')
        strings(reuse['knowledge'], 'reused foundational knowledge')
        pair = (reuse['consumer'], reuse['provider'])
        identity = (*pair, reuse['use'])
        require(set(pair) <= set(topics) and pair[0] != pair[1] and identity not in seen_reuse,
                'Unknown, duplicate or self-referencing curriculum reuse')
        seen_reuse.add(identity)
    for conflict in value['conflicts']:
        require({'unit_ids', 'topic_refs', 'issue', 'repair_instructions'} <= set(conflict)
                <= {'unit_ids', 'topic_refs', 'affected_topic_refs', 'issue', 'repair_instructions'}
                and conflict['issue'].strip(), 'Invalid teaching responsibility conflict')
        strings(conflict['unit_ids'], 'conflicting units'); strings(conflict['topic_refs'], 'conflicting Topics')
        strings(conflict['repair_instructions'], 'replanning instructions')
        unknown_units = sorted(set(conflict['unit_ids']) - set(plans))
        unknown_topics = sorted(set(conflict['topic_refs']) - set(topics))
        require(not unknown_units and not unknown_topics,
                'Conflict refers to an unknown curriculum unit or Topic: ' + json.dumps({
                    'unknown_curriculum_unit_ids': unknown_units, 'unknown_topic_refs': unknown_topics,
                    'valid_owners_of_supplied_topics': sorted({ref.split('/')[0] for ref in conflict['topic_refs']
                                                             if ref in topics}),
                    'unit_id_field': 'Use the curriculum plan unit ID, not its source_unit_ids.'}, ensure_ascii=False))
        require(set(conflict['unit_ids']) <= {ref.split('/')[0] for ref in conflict['topic_refs']},
                'Conflict omits a Topic from an affected unit')
        if 'affected_topic_refs' in conflict:
            affected = strings(conflict['affected_topic_refs'], 'affected Topic references')
            require(set(affected) <= set(conflict['topic_refs'])
                    and conflict['unit_ids'] == sorted({ref.split('/')[0] for ref in affected}),
                    'Derived curriculum owners differ from affected Topic references')
    parents = {ref: [] for ref in topics}
    for before, after in edges:
        parents[after].append(before)
    done = set()
    def visit(ref, trail):
        require(ref not in trail, 'Cyclic course prerequisite graph')
        if ref not in done:
            for parent in parents[ref]:
                visit(parent, trail | {ref})
            done.add(ref)
    for ref in parents:
        visit(ref, set())
    if source is not None:
        validate_reference_resolutions(value, plans, source, source_findings or [])
    return value


def validate_reference_resolutions(value, plans, source, source_findings, *, focus_pairs=None):
    references = reference_only_subunits(source)
    findings = validate_source_findings(source_findings, source)
    detailed_sources = {(f['source']['unit_id'], f['source']['subunit_id']):
                        (f['explanation']['unit_id'], f['explanation']['subunit_id']) for f in findings}
    topics = topic_index(plans)
    owners = {u: g['unit_id'] for g in curriculum_units(source) for u in g['source_unit_ids']}
    resolved = set()
    for row in value.get('reference_resolutions', []):
        require(set(row) == {'source', 'consumer', 'provider', 'provider_source', 'knowledge', 'reason'}
                and row['knowledge'].strip() and row['reason'].strip(), 'Invalid source-reference resolution')
        require(set(row['source']) == {'unit_id', 'subunit_id'}, 'Invalid reference resolution source')
        key = (row['source']['unit_id'], row['source']['subunit_id'])
        consumer, provider = row['consumer'], row['provider']
        require(key in references,
                'reference_resolutions source ' + '/'.join(key) + ' is not a verified empty-body heading; '
                'ordinary explanatory-source or prerequisite reuse belongs in reuse, not reference_resolutions')
        require(consumer in topics and provider in topics,
                'Unknown reference consumer/provider Topic: ' + str(consumer) + ' / ' + str(provider))
        require(consumer != provider, 'Reference resolutions cannot be self-references: ' + consumer)
        uses = {c['use'] for c in topics[consumer]['coverage']
                if c['unit_id'] == key[0] and key[1] in c['subunit_ids']}
        require('primary' not in uses,
                'Reference consumer currently claims primary coverage: ' + consumer + ' / ' + '/'.join(key)
                + '; report its real planning conflict, not a resolution assuming a future prerequisite revision')
        require(consumer.split('/')[0] == owners[key[0]]
                and 'prerequisite' in uses, 'Reference consumer must have current prerequisite coverage: '
                + consumer + ' / ' + '/'.join(key))
        detail = row['provider_source']
        require(set(detail) == {'unit_id', 'subunit_ids'} and detail['unit_id'] in owners
                and provider.split('/')[0] == owners[detail['unit_id']], 'Wrong explanatory provider source')
        supplied = {(detail['unit_id'], sid) for sid in strings(detail['subunit_ids'], 'provider subunits')}
        primary = {(c['unit_id'], sid) for c in topics[provider]['coverage'] if c['use'] == 'primary'
                   for sid in c['subunit_ids']}
        require(supplied <= primary and not supplied & set(references), 'Provider lacks real primary teaching coverage')
        require(key not in detailed_sources or detailed_sources[key] in supplied, 'Provider does not cover verified explanatory source')
        require((consumer, key) not in resolved, 'Duplicate source-reference resolution')
        resolved.add((consumer, key))
    for consumer, topic in topics.items():
        for coverage in topic['coverage']:
            for sid in coverage['subunit_ids']:
                key = (coverage['unit_id'], sid)
                if key not in references:
                    continue
                conflicted = any(consumer in c['topic_refs'] for c in value['conflicts'])
                owner = consumer.split('/')[0]
                if focus_pairs is None or (owner, owner) in focus_pairs:
                    require(coverage['use'] != 'primary' or conflicted,
                            'Reference-only primary claim needs source-based replanning conflict')
                if coverage['use'] == 'prerequisite':
                    provider_source = detailed_sources.get(key)
                    responsible = (focus_pairs is None or (provider_source is not None and
                                   _pair(owner, owners[provider_source[0]]) in focus_pairs))
                    if responsible:
                        require((consumer, key) in resolved or conflicted,
                                'Unresolved reference needs an actual provider or replanning conflict')


def author_course_context(ref, relations, plans):
    require(not relations['conflicts'], 'Unresolved teaching responsibilities prevent author dispatch')
    topics = topic_index(plans)
    providers = {e['from'] for e in relations['prerequisites'] if e['to'] == ref}
    providers.update(r['provider'] for r in relations['reuse'] if r['consumer'] == ref)
    reference_resolutions = [r for r in relations.get('reference_resolutions', []) if r['consumer'] == ref]
    providers.update(r['provider'] for r in reference_resolutions)
    return {'topic_ref': ref,
        'prerequisites': [deepcopy(e) for e in relations['prerequisites'] if e['to'] == ref],
        'reuse': [deepcopy(r) for r in relations['reuse'] if r['consumer'] == ref],
        'primary_teaching': [deepcopy(r) for r in relations['reuse'] if r['provider'] == ref],
        'source_references': deepcopy(reference_resolutions),
        'providers': [{'topic_ref': p, 'title': topics[p]['title'], 'objectives': deepcopy(topics[p]['objectives']),
                       'sections': deepcopy(topics[p]['sections'])} for p in sorted(providers)]}


def native_topic_id(course_id, ref):
    return course_id + '--' + ref.replace('/', '--')
