"""Independent multi-source responsibility cycles and exact-source boundaries."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from graspmemoedu import context_production as cp
from course_responsibility_adjudication import (ADJUDICATE, adjudication_payload, claim_catalog,
    combine_adjudications, index_payload, partition_adjudication, recorded_observations, unpack_claims,
    validate_adjudication, validate_index)
from course_curriculum_relations import coordination_request_chars
from course_authoring_contract import unit_payload
from course_curriculum_relations import unpack_coordination_payload
from tests.fixtures import temporary_test_directory
import produce_goal_course as workflow
import course_responsibility_adjudication as responsibility


def responsibility_fixture():
    source = {'schema_version': 1, 'role': 'teaching_materials',
        'course': {'id': 'seal-fixture', 'title': 'Independent meanings', 'subject_id': 'english', 'description': 'A held-out reference.'},
        'learning_objectives': [], 'units': []}
    plans = {}
    for owner, closure, animal in (
        ('reference', 'A seal is a barrier fitted at a joint to prevent liquid leakage. Its function is the barrier, not the material used.',
         'A seal is also a marine mammal; this animal meaning is independent of the barrier meaning.'),
        ('containers', 'A container seal prevents liquid leaking at its opening. Apply the barrier definition while checking a container.',
         'A picture label uses seal for the marine mammal; identify that sense from the animal context.'),
        ('pipes', 'A pipe seal prevents liquid leakage at the joint. Apply the barrier definition to the joint location.',
         'A wildlife note uses seal for the marine mammal; this is not a pipe component.')):
        text = 'Two distinct meanings remain separate.\n\n# Barrier\n' + closure + '\n\n# Animal\n' + animal + '\n'
        unit = {'unit_id': owner, 'title': owner.title(), 'role': 'teaching_source', 'text': text,
            'completeness': {'kind': 'complete_chapter', 'boundary': 'Both complete meaning sections.',
                'subunits': [{'id': 'barrier', 'title': 'Barrier'}, {'id': 'animal', 'title': 'Animal'}]},
            'teaching_scope': ['Preserve the barrier meaning and the unrelated animal meaning.']}
        source['units'].append(unit)
        topics = []
        for key, explanation in (('barrier', closure), ('animal', animal)):
            topics.append({'id': key, 'title': owner.title() + ' ' + key, 'kind': 'atomic',
                'boundary_rationale': 'This distinct meaning has a separate explanatory objective.',
                'objectives': [explanation], 'prerequisites': [],
                'coverage': [{'unit_id': owner, 'subunit_ids': [key], 'use': 'primary', 'scope': explanation}],
                'sections': [{'id': key, 'title': key.title(), 'scope': explanation}]})
        plans[owner] = {'title': owner.title(), 'description': 'Both meanings in full.', 'topics': topics}
    conflicts = []
    for consumer, provider in [('containers', 'reference'), ('reference', 'pipes'), ('pipes', 'containers')]:
        conflicts.append({'unit_ids': [consumer], 'affected_topic_refs': [consumer + '/barrier'],
            'topic_refs': [consumer + '/barrier', provider + '/barrier'],
            'issue': 'The same definition of a seal as a liquid-leakage barrier is claimed twice.',
            'repair_instructions': ['Move that shared barrier definition to ' + provider + '/barrier; keep the concrete application.']})
    candidate = {'prerequisites': [], 'reuse': [
        {'consumer': 'containers/barrier', 'provider': 'pipes/barrier', 'knowledge': ['A seal is a liquid-leakage barrier.'],
         'use': 'application', 'reason': 'This old proposal points to an owner also told to hand the definition away.'},
        {'consumer': 'containers/animal', 'provider': 'reference/animal', 'knowledge': ['A seal is a marine mammal.'],
         'use': 'application', 'reason': 'Use the distinct animal meaning in the picture context.'}],
        'conflicts': conflicts, 'reference_resolutions': []}
    index = {'components': [
        {'id': 'barrier-meaning', 'knowledge': 'Seal as a barrier that prevents liquid leakage',
         'claim_ids': ['conflicts:0', 'conflicts:1', 'conflicts:2', 'reuse:0']},
        {'id': 'animal-meaning', 'knowledge': 'Seal as a marine mammal', 'claim_ids': ['reuse:1']}]}
    return source, plans, candidate, index


def consistent_decisions(index, candidate):
    result = []
    for component in index['components']:
        animal = component['id'] == 'animal-meaning'
        kind = 'animal' if animal else 'barrier'
        reuse = ([deepcopy(candidate['reuse'][1])] if animal else [
            {'consumer': owner + '/barrier', 'provider': 'reference/barrier',
             'knowledge': ['A seal is a liquid-leakage barrier.'], 'use': 'application',
             'reason': 'Keep the concrete application and briefly recall the foundation.'}
            for owner in ('containers', 'pipes')])
        result.append({'component_id': component['id'], 'knowledge': component['knowledge'],
            'outcome': 'unique_primary', 'primary_topic_ref': 'reference/' + kind,
            'reason': 'The reference defines this exact sense; the other contexts apply it.',
            'claim_dispositions': [{'claim_id': key, 'disposition': 'retained' if animal else 'replaced',
                'reason': 'Keep this distinct sense.' if animal else 'Replace the circular transfers with the final provider.'}
                for key in component['claim_ids']],
            'prerequisites': [], 'reuse': reuse, 'conflicts': [], 'reference_resolutions': []})
    return {'decisions': result}


def fixture_directions(payload):
    return {'directions': [{'claim_id': row['claim_id'], 'component_id': component['id'],
        'provider': row['record']['topic_refs'][1], 'consumers': row['record']['affected_topic_refs'],
        'instruction_quotes': [{'index': 0, 'quote': row['record']['repair_instructions'][0]}],
        'reason': 'Only extract the explicitly named original destination.'}
        for row in payload['conflicts'] for component in row['components']]}


class ResponsibilityAdjudicationTests(unittest.TestCase):
    def test_evidence_tables_restore_complete_topics_sources_and_repeated_unicode(self):
        source, plans, candidate, index = responsibility_fixture()
        source['units'][0]['text'] += '\n完整自然段落：密封件、海豹；\n不得省略定义。\n'
        payload = adjudication_payload(index['components'], index, candidate, plans, source)
        packed = responsibility.pack_evidence(payload)
        self.assertTrue(packed['value_dictionary'])
        self.assertTrue(packed['object_columns'])
        restored = responsibility.unpack_evidence(packed)
        self.assertEqual(restored, payload)
        self.assertEqual(cp.digest(restored), packed['full_input_sha256'])
        self.assertEqual(restored['complete_topics'], payload['complete_topics'])
        self.assertEqual(restored['teaching_materials'], payload['teaching_materials'])
        self.assertEqual(restored['claims'], payload['claims'])
        bad = deepcopy(packed); bad['full_input_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'input hash mismatch'):
            responsibility.unpack_evidence(bad)
        bad = deepcopy(packed); bad['value_dictionary'][0] = {'value_ref': 0}
        with self.assertRaisesRegex(ValueError, 'forward evidence reference'):
            responsibility.unpack_evidence(bad)
        bad = deepcopy(packed); bad['data'] = {'row': [0]}
        with self.assertRaisesRegex(ValueError, 'Incomplete evidence table record'):
            responsibility.unpack_evidence(bad)

    def test_stage_capacity_reserves_output_by_actual_claims_and_keeps_whole_components(self):
        source, plans, candidate, index = responsibility_fixture()
        profile = {'effective_context_window': 1000000, 'input_tokens_per_char': 1,
            'fixed_input_overhead': 1, 'margin': 1, 'minimum_output_tokens': 1,
            'output_base_tokens': 1, 'output_tokens_per_claim': 1000, 'output_tokens_per_component': 6000}
        single = [adjudication_payload([component], index, candidate, plans, source)
                  for component in index['components']]
        capacities = [responsibility.adjudication_capacity(payload, profile) for payload in single]
        profile['effective_context_window'] = max(row['estimated_total_with_margin'] for row in capacities)
        combined = adjudication_payload(index['components'], index, candidate, plans, source)
        self.assertFalse(responsibility.adjudication_capacity(combined, profile)['fits'])
        batches = partition_adjudication(index, candidate, plans, source, [], [], 1, capacity_profile=profile)
        self.assertEqual([row['components'] for row in batches], [[component] for component in index['components']])
        self.assertTrue(all(responsibility.adjudication_capacity(row, profile)['fits'] for row in batches))
        profile['effective_context_window'] = min(row['estimated_total_with_margin'] for row in capacities) - 1
        with self.assertRaisesRegex(ValueError, 'Complete responsibility component exceeds capacity'):
            partition_adjudication(index, candidate, plans, source, [], [], 1000000, capacity_profile=profile)

    def test_packed_stage_binds_exact_full_input_and_resumes_without_new_calls(self):
        source, plans, candidate, index = responsibility_fixture()
        payload = {'source_reference_findings': [], 'complete_plan_binding': cp.digest(plans)}
        profile = {'effective_context_window': 1000000, 'input_tokens_per_char': 1,
            'fixed_input_overhead': 1, 'margin': 1.15, 'minimum_output_tokens': 8192,
            'output_base_tokens': 4096, 'output_tokens_per_claim': 500, 'output_tokens_per_component': 1100}
        with temporary_test_directory() as directory:
            root = Path(directory) / 'course-coordination/original'
            evidence = root / 'combined.json'; cp.save(evidence, {'candidate': candidate})
            capacity_path = Path(directory) / 'capacity.json'; cp.save(capacity_path, profile)
            calls = []
            def execute(folder, phase, prompt, current, timeout):
                calls.append((prompt, deepcopy(current)))
                if prompt == responsibility.INDEX:
                    result = index
                elif prompt == responsibility.DIRECTIONS:
                    result = fixture_directions(current)
                else:
                    self.assertEqual(prompt, responsibility.PACKED_ADJUDICATE)
                    full = responsibility.unpack_evidence({key: value for key, value in current.items()
                                                           if key != 'previous_issues'})
                    result = consistent_decisions({'components': full['components']}, candidate)
                messages = [{'role': 'system', 'content': prompt},
                            {'role': 'user', 'content': json.dumps(current, ensure_ascii=False)}]
                cp.save(folder / 'generate.response.json', result)
                cp.save(folder / 'generate.exec-proof.json', {'fixture_messages': messages})
                return result
            args = SimpleNamespace(workers=1, attempts=1, timeout=30, coordination_max_chars=50000,
                                   responsibility_capacity=capacity_path)
            with patch.object(workflow, 'read_original_coordination_result', return_value=(candidate, evidence)), \
                 patch.object(workflow, 'call', side_effect=execute), \
                 patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                result, path = workflow.run_responsibility_adjudication(root, payload, plans, source, args)
                self.assertEqual(len(calls), 3)
                record = cp.read(path)
                self.assertEqual(record['batches'][0]['full_input_sha256'], calls[-1][1]['full_input_sha256'])
                self.assertEqual(record['binding']['capacity_profile'], profile)
                self.assertEqual(workflow.run_responsibility_adjudication(root, payload, plans, source, args)[0], result)
                self.assertEqual(len(calls), 3)

    def test_unanimous_structured_relationships_are_inherited_without_adjudication(self):
        source, plans, candidate, index = responsibility_fixture()
        inherited, pending = responsibility.inherited_components(index, candidate)
        self.assertEqual([d['component_id'] for d in inherited['decisions']], ['animal-meaning'])
        self.assertEqual([c['id'] for c in pending], ['barrier-meaning'])
        self.assertEqual(inherited['decisions'][0]['reuse'], [candidate['reuse'][1]])
        payload = adjudication_payload(index['components'][1:], index, candidate, plans, source)
        validate_adjudication(inherited, payload, plans)

    def test_direction_extraction_preserves_cycles_and_rejects_unquoted_owners(self):
        _, _, candidate, index = responsibility_fixture()
        batches = responsibility.direction_inputs(index, candidate, 2)
        self.assertEqual([len(row['conflicts']) for row in batches], [2, 1])
        extracted = []
        for payload in batches:
            result = {'directions': []}
            for row in payload['conflicts']:
                record = row['record']
                result['directions'].append({'claim_id': row['claim_id'], 'component_id': 'barrier-meaning',
                    'provider': record['topic_refs'][1], 'consumers': record['affected_topic_refs'],
                    'instruction_quotes': [{'index': 0, 'quote': record['repair_instructions'][0]}],
                    'reason': 'The complete original instruction explicitly names the destination.'})
            responsibility.validate_directions(result, payload)
            extracted.extend(result['directions'])
            bad = deepcopy(result); bad['directions'][0]['instruction_quotes'][0]['quote'] += ' invented'
            with self.assertRaisesRegex(ValueError, 'quotation is absent'):
                responsibility.validate_directions(bad, payload)
        inherited, pending = responsibility.inherited_components(index, candidate, extracted)
        self.assertEqual([row['id'] for row in pending], ['barrier-meaning'])
        self.assertEqual(inherited['decisions'][0]['primary_topic_ref'], 'reference/animal')

    def test_exact_one_way_conflict_can_be_inherited_but_composite_subject_is_not_guessed(self):
        source, plans, candidate, index = responsibility_fixture()
        original = deepcopy(candidate)
        candidate['conflicts'] = candidate['conflicts'][:1]
        candidate['reuse'] = candidate['reuse'][1:]
        index['components'][0]['claim_ids'] = ['conflicts:0']
        index['components'][1]['claim_ids'] = ['reuse:0']
        payload = responsibility.direction_inputs(index, candidate)[0]
        direction = {'claim_id': 'conflicts:0', 'component_id': 'barrier-meaning',
            'provider': 'reference/barrier', 'consumers': ['containers/barrier'],
            'instruction_quotes': [{'index': 0, 'quote': candidate['conflicts'][0]['repair_instructions'][0]}],
            'reason': 'Explicit original destination and the single affected Topic.'}
        responsibility.validate_directions({'directions': [direction]}, payload)
        inherited, pending = responsibility.inherited_components(index, candidate, [direction])
        self.assertEqual(pending, [])
        current = adjudication_payload(index['components'], index, candidate, plans, source)
        validate_adjudication(inherited, current, plans)
        self.assertEqual(inherited['decisions'][0]['conflicts'][0]['repair_instructions'],
                         original['conflicts'][0]['repair_instructions'])
        payload['conflicts'][0]['record']['affected_topic_refs'].append('pipes/barrier')
        payload['conflicts'][0]['record']['topic_refs'].append('pipes/barrier')
        with self.assertRaisesRegex(ValueError, 'Composite affected Topics'):
            responsibility.validate_directions({'directions': [direction]}, payload)
        direction.update(provider=None, consumers=[], instruction_quotes=[], reason='The precise consumer is not explicit.')
        responsibility.validate_directions({'directions': [direction]}, payload)
        self.assertEqual(len(responsibility.inherited_components(index, candidate, [direction])[1]), 1)

    def test_independent_author_requires_absence_from_all_proposals_and_unaffected_full_unit(self):
        source, plans, candidate, _ = responsibility_fixture()
        unit = deepcopy(source['units'][0]); unit['unit_id'] = 'independent'; source['units'].append(unit)
        plan = deepcopy(plans['reference'])
        for topic in plan['topics']:
            topic['coverage'][0]['unit_id'] = 'independent'
        plans['independent'] = plan
        with temporary_test_directory() as directory:
            run = Path(directory)
            root, payload = workflow.course_relation_stage(run, source, plans)
            evidence = root / 'combined.json'; cp.save(evidence, {'candidate': candidate})
            with patch.object(workflow, 'read_original_coordination_result', return_value=(candidate, evidence)):
                contexts, binding = workflow.independent_author_contexts(run, source, plans, evidence,
                    ['independent/barrier'])
                self.assertFalse(binding['global_coordination_approved'])
                self.assertEqual(contexts['independent/barrier']['providers'], [])
                with self.assertRaisesRegex(ValueError, 'existing coordination proposal'):
                    workflow.independent_author_contexts(run, source, plans, evidence, ['reference/animal'])
                candidate['conflicts'][0]['topic_refs'].append('independent/animal')
                with self.assertRaisesRegex(ValueError, 'complete unit plan'):
                    workflow.independent_author_contexts(run, source, plans, evidence, ['independent/barrier'])

    def test_global_claim_encoding_is_lossless_and_tampering_is_rejected(self):
        _, plans, candidate, _ = responsibility_fixture()
        original = deepcopy(candidate)
        payload = index_payload(candidate, plans)
        self.assertEqual(unpack_claims(payload), claim_catalog(candidate))
        self.assertEqual(candidate, original)
        table = payload['claim_tables'][0]
        table['rows'][0][1 + table['columns'].index('reason')] += ' Changed.'
        with self.assertRaisesRegex(ValueError, 'claim hash'):
            unpack_claims(payload)

    def test_global_index_covers_opposite_claims_and_separates_meanings(self):
        _, plans, candidate, index = responsibility_fixture()
        payload = index_payload(candidate, plans)
        validate_index(index, payload)
        barrier, animal = index['components']
        self.assertTrue({'conflicts:0', 'conflicts:1', 'conflicts:2'} <= set(barrier['claim_ids']))
        self.assertNotIn('reuse:1', barrier['claim_ids'])
        bad = deepcopy(index); bad['components'][0]['claim_ids'].remove('conflicts:2')
        with self.assertRaisesRegex(ValueError, 'omitted original'):
            validate_index(bad, payload)
        bad = deepcopy(index); bad['components'][1]['knowledge'] = barrier['knowledge']
        with self.assertRaisesRegex(ValueError, 'same exact knowledge'):
            validate_index(bad, payload)

    def test_adjudication_receives_complete_related_topics_and_natural_source_sections(self):
        source, plans, candidate, index = responsibility_fixture()
        before = deepcopy((source, plans, candidate))
        payload = adjudication_payload(index['components'][:1], index, candidate, plans, source)
        self.assertEqual(len(payload['complete_topics']), 3)
        for row in payload['complete_topics']:
            owner, local = row['topic_ref'].split('/')
            expected = next(topic for topic in plans[owner]['topics'] if topic['id'] == local)
            self.assertEqual(row['topic'], expected)
            self.assertEqual(row['full_plan_sha256'], cp.digest(plans[owner]))
        for unit in payload['teaching_materials']:
            original = next(row for row in source['units'] if row['unit_id'] == unit['unit_id'])
            self.assertEqual(unit['text'], original['text'].split('# Animal')[0])
            self.assertTrue(unit['text'].startswith('Two distinct meanings remain separate.'))
        self.assertEqual((source, plans, candidate), before)

    def test_final_provider_redirects_old_reuse_and_never_merges_other_sense(self):
        source, plans, candidate, index = responsibility_fixture()
        payload = adjudication_payload(index['components'], index, candidate, plans, source)
        result = consistent_decisions(index, candidate)
        validate_adjudication(result, payload, plans)
        combined = combine_adjudications(index, [payload], [result], plans, source, [])
        providers = {r['consumer']: r['provider'] for r in combined['candidate']['reuse']}
        self.assertEqual(providers['containers/barrier'], 'reference/barrier')
        self.assertEqual(providers['pipes/barrier'], 'reference/barrier')
        self.assertEqual(providers['containers/animal'], 'reference/animal')
        self.assertEqual(candidate['reuse'][0]['provider'], 'pipes/barrier')
        for kind in responsibility.KINDS:
            self.assertEqual({cp.digest(row) for row in combined['candidate'][kind]},
                             set(combined['item_origins'][kind]))
        bad = deepcopy(result); bad['decisions'][0]['reuse'][0]['provider'] = 'pipes/barrier'
        with self.assertRaisesRegex(ValueError, 'different primary'):
            validate_adjudication(bad, payload, plans)

    def test_explicit_disposition_cannot_hide_omission_or_false_retention(self):
        source, plans, candidate, index = responsibility_fixture()
        payload = adjudication_payload(index['components'], index, candidate, plans, source)
        result = consistent_decisions(index, candidate)
        bad = deepcopy(result); bad['decisions'][0]['claim_dispositions'].pop()
        with self.assertRaisesRegex(ValueError, 'explicit disposition'):
            validate_adjudication(bad, payload, plans)
        bad = deepcopy(result); bad['decisions'][0]['claim_dispositions'][0]['disposition'] = 'retained'
        with self.assertRaisesRegex(ValueError, 'Retained original'):
            validate_adjudication(bad, payload, plans)
        bad = deepcopy(result); bad['decisions'][0]['claim_dispositions'][0]['disposition'] = 'partly_represented'
        with self.assertRaisesRegex(ValueError, 'unsplit claim'):
            validate_adjudication(bad, payload, plans)

    def test_capacity_rejects_oversize_complete_component_without_truncation(self):
        source, plans, candidate, index = responsibility_fixture()
        payload = adjudication_payload(index['components'][:1], index, candidate, plans, source)
        limit = coordination_request_chars(payload, ADJUDICATE) - 1
        with self.assertRaisesRegex(ValueError, 'exceeds capacity'):
            partition_adjudication(index, candidate, plans, source, [], [], limit)
        batches = partition_adjudication(index, candidate, plans, source, [], [], 50000)
        self.assertEqual([r['id'] for b in batches for r in b['components']],
                         [r['id'] for r in index['components']])

    def test_external_observation_requires_exact_original_union_and_real_claims(self):
        _, _, candidate, _ = responsibility_fixture()
        original_input = {'complete': 'An independent complete original input.'}
        with temporary_test_directory() as directory:
            directory = Path(directory); original = directory / 'combined.json'
            cp.save(original, {'candidate': candidate})
            report = {'role': 'curriculum_coordination_observation', 'reviewer': 'subagent',
                'candidate_modified': False,
                'inputs': {'combined_path': str(original),
                    'combined_file_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
                    'input_digest': cp.digest(original_input), 'candidate_digest': cp.digest(candidate)},
                'observations': [{'claim_refs': ['conflicts:0', 'conflicts:1', 'conflicts:2'],
                    'finding': 'The barrier proposals transfer the same explanation in a cycle.',
                    'repair_instructions': ['Resolve the whole cycle using the complete records.']}]}
            path = directory / 'observations.json'; cp.save(path, report)
            self.assertEqual(len(recorded_observations([path], original, original_input, candidate)), 1)
            for name, change in [('approval', lambda r: r.update(approved=True)),
                                 ('unknown', lambda r: r['observations'][0]['claim_refs'].append('reuse:999')),
                                 ('changed', lambda r: r['inputs'].update(candidate_digest='0' * 64))]:
                bad = deepcopy(report); change(bad)
                path = directory / (name + '.json'); cp.save(path, bad)
                with self.assertRaises(ValueError):
                    recorded_observations([path], original, original_input, candidate)

    def test_existing_stage_resumes_without_calls_and_authenticates_global_decisions(self):
        source, plans, candidate, index = responsibility_fixture()
        payload = {'source_reference_findings': [], 'complete_plan_binding': cp.digest(plans)}
        with temporary_test_directory() as directory:
            root = Path(directory) / 'course-coordination/original'
            evidence = root / 'combined.json'; cp.save(evidence, {'candidate': candidate})
            original_bytes = evidence.read_bytes()
            calls = []
            def execute(folder, phase, prompt, current, timeout):
                calls.append(prompt)
                result = (index if prompt == responsibility.INDEX else fixture_directions(current)
                    if prompt == responsibility.DIRECTIONS else consistent_decisions({'components': current['components']}, candidate))
                messages = [{'role': 'system', 'content': prompt},
                            {'role': 'user', 'content': json.dumps(current, ensure_ascii=False)}]
                cp.save(folder / 'generate.response.json', result)
                cp.save(folder / 'generate.exec-proof.json', {'fixture_messages': messages})
                return result
            args = SimpleNamespace(workers=1, attempts=1, timeout=30, coordination_max_chars=50000,
                                   responsibility_directions_only=True)
            with patch.object(workflow, 'read_original_coordination_result', return_value=(candidate, evidence)), \
                 patch.object(workflow, 'call', side_effect=execute), \
                 patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                routing, route_path = workflow.run_responsibility_adjudication(root, payload, plans, source, args)
                self.assertEqual(len(calls), 2)
                self.assertFalse(routing['global_coordination_approved'])
                self.assertEqual(len(routing['pending_components']), 1)
                self.assertFalse((root / 'adjudicated.json').exists())
                args.responsibility_directions_only = False
                result, path = workflow.run_responsibility_adjudication(root, payload, plans, source, args)
                self.assertEqual(len(calls), 3)
                self.assertEqual(path.name, 'adjudicated.json')
                self.assertEqual(workflow.run_responsibility_adjudication(root, payload, plans, source, args)[0], result)
                self.assertEqual(len(calls), 3)
                self.assertEqual(evidence.read_bytes(), original_bytes)
                changed = cp.read(path); changed['candidate']['reuse'][0]['provider'] = 'pipes/barrier'
                path.write_text(json.dumps(changed), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'immutable actual Exec'):
                    workflow.read_coordination_result(root, payload, plans, source)

    def test_final_union_checks_cycles_across_individually_valid_components(self):
        source, plans, candidate, index = responsibility_fixture()
        payload = adjudication_payload(index['components'], index, candidate, plans, source)
        result = consistent_decisions(index, candidate)
        for decision in result['decisions']:
            decision['outcome'] = 'distinct_scopes'; decision['primary_topic_ref'] = None
        refs = ('reference/barrier', 'containers/barrier')
        for number, pair in enumerate((refs, tuple(reversed(refs)))):
            result['decisions'][number]['prerequisites'] = [{'from': pair[0], 'to': pair[1],
                'knowledge': 'A precise explanatory dependency.', 'reason': 'This independent claim requires the other concept.'}]
        validate_adjudication(result, payload, plans)
        with self.assertRaisesRegex(ValueError, 'Cyclic course'):
            combine_adjudications(index, [payload], [result], plans, source, [])

    def test_adjudicated_feedback_reuses_authenticated_complete_plans(self):
        source, plans, candidate, index = responsibility_fixture()
        def retain(stage, prompt, payload, value):
            messages = [{'role': 'system', 'content': prompt},
                        {'role': 'user', 'content': json.dumps({**payload, 'previous_issues': []})}]
            cp.save(stage / '1/generate.response.json', value)
            cp.save(stage / '1/generate.exec-proof.json', {'fixture_messages': messages})
            cp.save(stage / 'accepted.json', {'workflow': workflow.WORKFLOW, 'input': payload,
                'candidate': value, 'attempt': 1, 'semantic_review_performed': False,
                'prompt_fingerprints': {'generate': cp.digest(prompt)}})
        with temporary_test_directory() as directory:
            run = Path(directory)
            for ident, plan in plans.items():
                local = run / 'units' / ident
                teaching = unit_payload(source, ident)
                cp.save(local / 'teaching-input.json', teaching)
                retain(local / 'planning', workflow.PLAN, teaching, plan)
            root, payload = workflow.course_relation_stage(run, source, plans)
            cp.save(root / 'complete-coordination-input.json', unpack_coordination_payload(payload))
            raw = deepcopy(candidate)
            for row in raw['conflicts']:
                row.pop('unit_ids')
            retain(root, workflow.RELATIONS, payload, raw)
            originals = {str(path.relative_to(run)): path.read_bytes() for path in run.rglob('accepted.json')}
            def execute(folder, phase, prompt, current, timeout):
                result = (index if prompt == responsibility.INDEX else fixture_directions(current)
                    if prompt == responsibility.DIRECTIONS else consistent_decisions({'components': current['components']}, candidate))
                if prompt == responsibility.ADJUDICATE:
                    result['decisions'][0]['conflicts'] = [{
                        'affected_topic_refs': ['containers/barrier'],
                        'topic_refs': ['containers/barrier', 'reference/barrier'],
                        'issue': 'The original objective still claims the shared foundation.',
                        'repair_instructions': ['Keep the complete container application and reference the primary foundation.']}]
                messages = [{'role': 'system', 'content': prompt},
                            {'role': 'user', 'content': json.dumps(current)}]
                cp.save(folder / 'generate.response.json', result)
                cp.save(folder / 'generate.exec-proof.json', {'fixture_messages': messages})
                return result
            with patch.object(workflow, 'call', side_effect=execute), \
                 patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                args = SimpleNamespace(workers=1, attempts=1, timeout=30, coordination_max_chars=50000)
                result, evidence = workflow.run_responsibility_adjudication(root, payload, plans, source, args)
                feedback, actual_input, prior, binding = workflow.verified_plan_feedback(run, source, evidence)
                self.assertEqual(feedback, result)
                self.assertEqual(actual_input, payload)
                self.assertEqual(prior, plans)
                self.assertEqual(binding['feedback_sha256'], cp.digest(result))
                self.assertEqual({unit for row in feedback['conflicts'] for unit in row['unit_ids']}, {'containers'})
                context = workflow.plan_repair_context('containers', feedback, actual_input, prior)
                self.assertEqual(context['original_complete_plan']['plan'], plans['containers'])
                self.assertEqual({row['topic_ref'] for row in context['related_complete_topics']}, {'reference/barrier'})
                for path, content in originals.items():
                    self.assertEqual((run / path).read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
