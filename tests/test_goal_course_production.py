"""Held-out role-isolation, native projection, compilation and resume tests."""
from copy import deepcopy
from concurrent.futures import wait as futures_wait
from html import escape
import hashlib
from itertools import combinations_with_replacement
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from graspmemoedu import context_production as cp
from graspmemoedu.atomic_content import plain_text, validate_atomic
from graspmemoedu.topic_runtime import validate_runtime
from course_authoring_contract import (author_payload, domain_objectives, load_materials,
    curriculum_units, point_catalog, project_atomic, project_paradigm, readable_topic, reference_only_subunits, resolve_anchors, unit_payload,
    validate_author, validate_plan)
from produce_goal_course import (accepted_stage, active_planning_root, author_feedback, bounded_results, check_dispatch,
    DispatchStopped, freeze_teaching_catalog, produce, AUTHOR, PLAN, PLAN_REPAIR, PLAN_BEFORE_REFERENCE_RULES, REVIEW, WORKFLOW,
    reuse_plan_from, verify_accepted, verify_failed_start, verify_plan, write_course_plan_preview)
from produce_goal_course import (coordination_batch_stage, course_relation_stage, read_coordination_result,
    prior_coordination_rejection, project_coordination_observations, reuse_compatible_coordination, run_course_coordination,
    plan_repair_context, verified_plan_feedback, verify_coordination_batch)
from course_curriculum_relations import (BATCH_RELATIONS, BATCH_RELATIONS_BEFORE_TOPIC_OWNERS,
    BATCH_RELATIONS_BEFORE_REFERENCE_CASES, coordination_before_reference_cases,
    RELATIONS, author_course_context, combine_coordination,
    coordination_batch_payload, coordination_payload, coordination_request_chars, native_topic_id,
    load_source_findings, pack_coordination_payload, unpack_coordination_payload,
    partition_coordination, project_coordination_response, validate_batch_relations, validate_relations, validate_source_findings)
from prepare_goal_course import compile_sources, final_point_map, validate_bank_references, validate_compiled_points
from retrieve_goal_materials import lexical_entries, vocabulary
from tests.fixtures import temporary_test_directory


class GoalCourseProductionTests(unittest.TestCase):
    def fixture(self, a='Red is a colour.', b='Blue is a colour.'):
        source = {'schema_version': 1, 'role': 'teaching_materials',
            'course': {'id': 'held-out', 'title': 'Independent material', 'subject_id': 'english', 'description': 'A complete teaching system.'},
            'learning_objectives': [], 'units': [{'unit_id': 'complete-unit', 'title': 'A complete unit',
                'role': 'teaching_source', 'text': a + '\n\n' + b,
                'completeness': {'kind': 'complete_topic', 'boundary': 'Both complete sections.',
                                 'subunits': [{'id': 'first', 'title': 'First'}, {'id': 'second', 'title': 'Second'}]},
                'teaching_scope': ['Explain both facts and their relation.']}]}
        topic = {'id': 'facts', 'title': 'Independent facts', 'kind': 'atomic', 'objectives': ['Recall both facts'],
            'boundary_rationale': 'The two related facts form a reusable conceptual system.',
            'prerequisites': [], 'coverage': [{'unit_id': 'complete-unit', 'subunit_ids': ['first', 'second'], 'use': 'primary', 'scope': 'Both facts in context.'}],
            'sections': [{'id': 'intro-a', 'title': 'First', 'scope': 'Explain the first fact.'},
                         {'id': 'intro-b', 'title': 'Second', 'scope': 'Explain the second fact.'}]}
        candidate = {'id': 'facts', 'title': 'Independent facts', 'sections': []}
        for i, fact in enumerate((a, b)):
            suffix = 'a' if i == 0 else 'b'
            candidate['sections'].append({'id': 'intro-' + suffix, 'section_title': 'Section ' + suffix,
                'introduction': {'html': '<p>' + escape(fact) + '</p><p>This explanation belongs to this section.</p>'},
                'memory_chunks': [{'id': 'chunk-' + suffix, 'title': 'Fact ' + suffix, 'content': fact,
                                   'anchors': [{'quote': fact}], 'mnemonic': {'source_quotes': ['A useful cue.']}}],
                'memory_points': [{'id': 'point-' + suffix, 'chunk_id': 'chunk-' + suffix, 'content': fact}],
                'flashcards': [{'id': 'card-' + suffix, 'title': 'Recall', 'point_ids': ['point-' + suffix],
                    'question': 'State the named fact.', 'reference_answer': fact, 'explanation': fact,
                    'interaction': {'version': 1, 'type': 'text', 'grading': 'semantic'}, 'learning_phases': ['learn', 'review']}]})
        return source, {'title': 'Material course', 'description': 'A complete system.', 'topics': [topic]}, candidate

    def paradigm(self):
        def node(ident, text, children=None):
            return {'id': ident, 'text': text, 'children': children or []}
        children = [node('q', 'Compute 2 + 3.'), node('explanation', 'Explanation', [node('answer', '2 + 3 = 5.')])]
        children += [node('practice-' + str(i), 'Practice ' + str(i), [node('pq-' + str(i), 'Compute 3 + ' + str(i) + '.'),
                     node('pe-' + str(i), 'Explanation', [node('pa-' + str(i), 'The result is ' + str(3 + i) + '.')])]) for i in range(1, 8)]
        return {'topic_format_version': 2, 'id': 'method', 'title': 'Addition method', 'topic_kind': 'lesson',
            'nodes': [node('method-intro', 'Introduction: Addition', [node('prose', 'Add the two whole numbers in order.')]),
                      node('example-1', 'Example 1', children)]}

    def test_legacy_target_sources_and_nested_mapping_are_rejected_before_authoring(self):
        source, plan, _ = self.fixture()
        for mutation in ('role', 'blank_ids', 'target_required', 'assessed_by', 'answer_key'):
            bad = deepcopy(source)
            if mutation == 'role':
                bad['units'][0]['role'] = 'target_source'
            else:
                bad['units'][0][mutation] = ['private']
            with self.subTest(field=mutation), self.assertRaises(ValueError):
                author_payload(bad, plan, plan['topics'][0])
        bad = deepcopy(plan); bad['topics'][0]['target_required'] = True
        with self.assertRaises(ValueError): author_payload(source, bad, bad['topics'][0])
        with self.assertRaises(ValueError): domain_objectives({'role': 'domain_objectives', 'learning_objectives': [], 'blank_ids': ['b1']})
        with temporary_test_directory() as directory:
            path = Path(directory) / 'legacy.json'
            cp.save(path, {'blank_ids': ['b1'], 'sources': [{'id': 'goal', 'text': 'Unrelated target.'}]})
            with self.assertRaises(ValueError): load_materials(path)

    def test_complete_scope_and_whole_materials_survive_author_boundary(self):
        for a, b in [('Red is a colour.', 'Blue is a colour.'), ('Two is even.', 'Three is odd.')]:
            source, plan, candidate = self.fixture(a, b)
            validate_plan(plan, source); validate_author(candidate, plan['topics'][0])
            payload = author_payload(source, plan, plan['topics'][0])
            self.assertEqual(payload['teaching']['units'][0]['text'], a + '\n\n' + b)
            self.assertNotIn('source_refs', str(payload))
            bad = deepcopy(plan); bad['topics'][0]['coverage'][0]['subunit_ids'] = ['first']
            with self.assertRaisesRegex(ValueError, 'omitted'): validate_plan(bad, source)

    def section_selection_fixture(self):
        source, plan, _ = self.fixture()
        unit = source['units'][0]
        unit['text'] = ('A shared introduction.\r\nKeep this context.\r\n\r\n'
                        '# First\r\nRed is a colour.\r\nIts full example remains.\r\n\r\n'
                        '# Second\r\nBlue is a colour.\r\n\r\n'
                        '# Third\r\nYellow is a colour.\r\n\r\n'
                        '# Fourth\r\nGreen is a colour.\r\n')
        unit['completeness'] = {'kind': 'complete_chapter', 'boundary': 'Four full colour sections.',
            'subunits': [{'id': key, 'title': key.title()} for key in ('first', 'second', 'third', 'fourth')]}
        topic = plan['topics'][0]
        topic['coverage'] = [{'unit_id': 'complete-unit', 'subunit_ids': [key], 'use': use, 'scope': key}
            for key, use in [('first', 'primary'), ('second', 'transfer'), ('third', 'prerequisite'), ('second', 'transfer')]]
        other = deepcopy(topic)
        other.update(id='mixtures', title='Colour mixtures', objectives=['Explain complete colour mixtures'])
        other['coverage'] = [{'unit_id': 'complete-unit', 'subunit_ids': ['second', 'third', 'fourth'],
                              'use': 'primary', 'scope': 'Teach these complete colour sections.'}]
        plan['topics'].append(other)
        return source, plan

    def test_author_selection_keeps_preamble_all_roles_and_whole_sections_once(self):
        source, plan = self.section_selection_fixture()
        before = deepcopy((source, plan))
        payload = author_payload(source, plan, plan['topics'][0])
        actual = payload['teaching']['units'][0]
        self.assertEqual(actual['text'], source['units'][0]['text'].split('# Fourth')[0])
        self.assertIn('Its full example remains.\r\n', actual['text'])
        self.assertEqual(actual['text'].count('# Second'), 1)
        self.assertEqual(payload['curriculum_plan'], plan)
        self.assertEqual(actual['completeness']['kind'], 'complete_topic')
        self.assertEqual([s['id'] for s in actual['completeness']['subunits']], ['first', 'second', 'third'])
        selection = payload['source_selection'][0]
        self.assertEqual(selection['coverage_subunit_ids'], ['first', 'second', 'third'])
        lines = source['units'][0]['text'].splitlines(keepends=True)
        self.assertEqual(''.join(''.join(lines[r['line_start'] - 1:r['line_end']])
                                for r in selection['line_ranges']), actual['text'])
        self.assertEqual(selection['line_ranges'][0]['kind'], 'preamble')
        self.assertEqual(selection['full_source_sha256'], hashlib.sha256(source['units'][0]['text'].encode()).hexdigest())
        self.assertEqual(selection['selected_text_sha256'], hashlib.sha256(actual['text'].encode()).hexdigest())
        self.assertEqual((source, plan), before)

    def test_author_selection_falls_back_for_ambiguous_missing_or_unordered_boundaries(self):
        for error in ('duplicate', 'missing', 'order', 'same-title'):
            source, plan = self.section_selection_fixture()
            unit = source['units'][0]
            if error == 'duplicate': unit['text'] += '# Second\nAnother section.\n'
            elif error == 'missing': unit['text'] = unit['text'].replace('# Second', '# Another title')
            elif error == 'order': unit['completeness']['subunits'].reverse()
            else: unit['completeness']['subunits'][1]['title'] = 'First'
            with self.subTest(error=error):
                payload = author_payload(source, plan, plan['topics'][0])
                self.assertEqual(payload['teaching']['units'], source['units'])
                self.assertNotIn('source_selection', payload)

    def test_whole_source_author_input_identity_is_unchanged(self):
        source, plan, _ = self.fixture()
        self.assertEqual(author_payload(source, plan, plan['topics'][0]),
                         {'teaching': source, 'topic': plan['topics'][0], 'curriculum_plan': plan})

    def test_selected_reference_summary_remains_explicitly_a_summary(self):
        source, plan = self.section_selection_fixture()
        unit = source['units'][0]
        unit['completeness'].update(kind='external_reference_summary',
            boundary='A new reference summary, not original text or every example.')
        payload = author_payload(source, plan, plan['topics'][0])
        actual = payload['teaching']['units'][0]
        self.assertEqual(actual['completeness']['kind'], 'external_reference_summary')
        self.assertIn(unit['completeness']['boundary'], actual['completeness']['boundary'])
        self.assertEqual(payload['source_selection'][0]['full_completeness'], unit['completeness'])

    def test_reference_summary_keeps_its_explicit_boundary_and_full_text(self):
        source, plan, _ = self.fixture()
        unit = source['units'][0]
        unit['completeness'].update(kind='external_reference_summary',
            boundary='A newly prepared summary of both whole reference entries; not original text or every example.')
        unit['source_refs'] = [{'path': str(Path('D:/Artifacts/reference-summary.md')), 'sha256': 'a' * 64}]
        with temporary_test_directory() as directory:
            path = Path(directory) / 'materials.json'; cp.save(path, source)
            payload = author_payload(load_materials(path), plan, plan['topics'][0])
        actual = payload['teaching']['units'][0]
        self.assertEqual(actual['completeness'], unit['completeness'])
        self.assertEqual(actual['text'], unit['text'])
        self.assertNotIn('source_refs', actual)

    def test_grouping_preserves_complete_sources_and_has_one_owner(self):
        source, plan, _ = self.fixture()
        second = deepcopy(source['units'][0]); second['unit_id'] = 'complement'
        source['units'].append(second)
        source['curriculum_units'] = [{'unit_id': 'shared-concept', 'title': 'One teaching system',
                                      'source_unit_ids': ['complete-unit', 'complement']}]
        self.assertEqual(len(curriculum_units(source)), 1)
        payload = unit_payload(source, 'shared-concept')
        self.assertEqual([u['text'] for u in payload['units']], [u['text'] for u in source['units']])
        self.assertNotIn('curriculum_units', payload)
        bad = deepcopy(source); bad['curriculum_units'][0]['source_unit_ids'].append('complement')
        with self.assertRaisesRegex(ValueError, 'exactly one'): curriculum_units(bad)
        bad = deepcopy(plan); bad['topics'][0]['coverage'][0]['use'] = 'transfer'
        with self.assertRaisesRegex(ValueError, 'primary teaching'):
            validate_plan(bad, self.fixture()[0])

    def reference_fixture(self):
        source, consumer, _ = self.fixture()
        reference = source['units'][0]
        reference['completeness']['subunits'] = [{'id': 'first', 'title': 'Spectrum (see Colour foundations)'},
                                               {'id': 'second', 'title': 'Mixtures'}]
        reference['text'] = 'Spectrum (see Colour foundations)\n\nMixtures\nA mixture combines two colours.\n'
        consumer['topics'][0]['coverage'] = [
            {'unit_id': 'complete-unit', 'subunit_ids': ['first'], 'use': 'prerequisite', 'scope': 'Reference to the foundations.'},
            {'unit_id': 'complete-unit', 'subunit_ids': ['second'], 'use': 'primary', 'scope': 'Explain mixtures.'}]
        detailed = deepcopy(reference)
        detailed.update(unit_id='colour-details', title='Colour foundations', text='Colour foundations\nRed is a colour.\nBlue is a colour.\n')
        detailed['completeness']['subunits'] = [{'id': 'foundations', 'title': 'Colour foundations'}]
        source['units'].append(detailed)
        provider = deepcopy(consumer)
        provider['topics'][0]['coverage'] = [{'unit_id': 'colour-details', 'subunit_ids': ['foundations'],
            'use': 'primary', 'scope': 'Teach the complete colour foundations.'}]
        plans = {'complete-unit': consumer, 'colour-details': provider}
        finding = {'source': {'unit_id': 'complete-unit', 'subunit_id': 'first'},
            'reference_quote': reference['text'].splitlines()[0],
            'reference_source_sha256': hashlib.sha256(reference['text'].encode()).hexdigest(),
            'explanation': {'unit_id': 'colour-details', 'subunit_id': 'foundations', 'line_start': 1, 'line_end': 3,
                'text': detailed['text'].rstrip('\n'), 'source_sha256': hashlib.sha256(detailed['text'].encode()).hexdigest()},
            'report_sha256': 'e' * 64}
        relation = {'prerequisites': [], 'reuse': [], 'conflicts': [], 'reference_resolutions': [{
            'source': deepcopy(finding['source']), 'consumer': 'complete-unit/facts', 'provider': 'colour-details/facts',
            'provider_source': {'unit_id': 'colour-details', 'subunit_ids': ['foundations']},
            'knowledge': 'Colour foundations', 'reason': 'The original heading explicitly refers to the complete explanation.'}]}
        return source, plans, finding, relation

    def test_only_empty_reference_heading_can_replace_local_primary_coverage(self):
        source, plans, _, _ = self.reference_fixture()
        payload = unit_payload(source, 'complete-unit')
        self.assertEqual(set(reference_only_subunits(payload)), {('complete-unit', 'first')})
        validate_plan(plans['complete-unit'], payload)
        substantive = deepcopy(payload)
        substantive['units'][0]['text'] = substantive['units'][0]['text'].replace('\n\nMixtures', '\nThe spectrum contains many colours.\n\nMixtures')
        self.assertEqual(reference_only_subunits(substantive), {})
        with self.assertRaisesRegex(ValueError, 'primary teaching'):
            validate_plan(plans['complete-unit'], substantive)
        transfer = deepcopy(plans['complete-unit']); transfer['topics'][0]['coverage'][0]['use'] = 'transfer'
        with self.assertRaisesRegex(ValueError, 'primary teaching'):
            validate_plan(transfer, payload)

    def test_reference_resolution_requires_actual_detailed_primary_provider(self):
        source, plans, finding, relation = self.reference_fixture()
        before = deepcopy(plans)
        validate_relations(relation, plans, source, [finding])
        self.assertEqual(relation['prerequisites'], [])
        context = author_course_context('complete-unit/facts', relation, plans)
        self.assertEqual(context['providers'][0]['topic_ref'], 'colour-details/facts')
        missing = deepcopy(relation); missing['reference_resolutions'] = []
        with self.assertRaisesRegex(ValueError, 'Unresolved reference'):
            validate_relations(missing, plans, source, [finding])
        wrong = deepcopy(relation); wrong['reference_resolutions'][0]['provider_source']['unit_id'] = 'complete-unit'
        with self.assertRaisesRegex(ValueError, 'Wrong explanatory provider'):
            validate_relations(wrong, plans, source, [finding])
        wrong = deepcopy(plans); wrong['colour-details']['topics'][0]['coverage'][0]['use'] = 'transfer'
        with self.assertRaisesRegex(ValueError, 'real primary teaching'):
            validate_relations(relation, wrong, source, [finding])
        missing['conflicts'] = [{'unit_ids': ['complete-unit'], 'topic_refs': ['complete-unit/facts'],
            'issue': 'The reference has no established provider.', 'repair_instructions': ['Replan from the complete sources.']}]
        validate_relations(missing, plans, source, [finding])
        original = deepcopy(plans); original['complete-unit']['topics'][0]['coverage'][0]['use'] = 'primary'
        with self.assertRaisesRegex(ValueError, 'primary claim'):
            validate_relations({'prerequisites': [], 'reuse': [], 'conflicts': []}, original, source, [finding])
        validate_relations(missing, original, source, [finding])
        self.assertEqual(plans, before)

    def test_reference_inventory_excludes_explanatory_reuse_and_preserves_current_primary_conflict(self):
        source, plans, finding, relation = self.reference_fixture()
        payload = coordination_payload(source, plans, [finding])
        cases = payload['reference_resolution_cases']
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['disposition'], 'resolve_current_prerequisite')
        self.assertEqual(cases[0]['provider_candidates'], [{'provider': 'colour-details/facts',
            'provider_source': {'unit_id': 'colour-details', 'subunit_ids': ['foundations']}}])
        primary = deepcopy(plans)
        primary['complete-unit']['topics'][0]['coverage'][0]['use'] = 'primary'
        primary_payload = coordination_payload(source, primary, [finding])
        self.assertEqual(primary_payload['reference_resolution_cases'][0]['disposition'], 'replan_primary_claim')
        with self.assertRaisesRegex(ValueError, 'currently claims primary.*planning conflict'):
            validate_relations(relation, primary, source, [finding])
        ordinary = deepcopy(relation)
        ordinary['reference_resolutions'][0]['source']['subunit_id'] = 'second'
        with self.assertRaisesRegex(ValueError, 'not a verified empty-body heading.*belongs in reuse'):
            validate_relations(ordinary, plans, source, [finding])
        self_ref = deepcopy(relation)
        self_ref['reference_resolutions'][0]['provider'] = self_ref['reference_resolutions'][0]['consumer']
        with self.assertRaisesRegex(ValueError, 'cannot be self-references'):
            validate_relations(self_ref, plans, source, [finding])
        legacy = deepcopy(payload); legacy.pop('reference_resolution_cases')
        self.assertEqual(coordination_before_reference_cases(pack_coordination_payload(payload)),
                         pack_coordination_payload(legacy))
        changed = deepcopy(payload); changed['reference_resolution_cases'][0]['current_coverage'] = ['primary']
        with self.assertRaisesRegex(ValueError, 'inventory changed'):
            coordination_before_reference_cases(pack_coordination_payload(changed))

    def test_reference_contract_reuses_exact_old_plans_without_rewriting_response(self):
        source, plans, finding, relation = self.reference_fixture()
        full = coordination_payload(source, plans, [finding])
        payload = coordination_batch_payload(full, list(plans), list(combinations_with_replacement(sorted(plans), 2)))
        old_input = coordination_before_reference_cases(payload)
        messages = [{'role': 'system', 'content': BATCH_RELATIONS_BEFORE_REFERENCE_CASES},
                    {'role': 'user', 'content': json.dumps(old_input)}]
        with temporary_test_directory() as directory:
            root = Path(directory) / 'course-coordination/new-contract'
            old = root.parent / 'calls' / cp.digest({'prompt': BATCH_RELATIONS_BEFORE_REFERENCE_CASES,
                                                    'input': old_input})[:16]
            cp.save(old / '1/generate.response.json', relation)
            cp.save(old / '1/generate.exec-proof.json', {'fixture_messages': messages})
            cp.save(old / 'accepted.json', {'workflow': WORKFLOW, 'input': old_input, 'candidate': relation,
                'attempt': 1, 'prompt_fingerprints': {'generate': cp.digest(BATCH_RELATIONS_BEFORE_REFERENCE_CASES)}})
            original = (old / 'accepted.json').read_bytes()
            with patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                self.assertTrue(reuse_compatible_coordination(root, payload, source))
                stage = coordination_batch_stage(root, payload)
                self.assertEqual(verify_coordination_batch(stage, payload, source), relation)
                self.assertEqual((stage / 'accepted.json').read_bytes(), original)
                changed = deepcopy(full)
                changed['unit_plans'][0]['plan']['topics'][0]['sections'][0]['scope'] += ' A real new distinction.'
                other = coordination_batch_payload(changed, list(plans),
                    list(combinations_with_replacement(sorted(plans), 2)))
                self.assertFalse(reuse_compatible_coordination(root, other, source))
            self.assertEqual((old / 'accepted.json').read_bytes(), original)

    def test_recorded_source_evidence_is_dynamic_and_bound_to_complete_text(self):
        source, plans, finding, _ = self.reference_fixture()
        report = {'required_cross_source_resolution': [{'reference_source': {
            **finding['source'], 'quote': finding['reference_quote'], 'content_sha256': finding['reference_source_sha256']},
            'resolved_explanatory_source': {**finding['explanation'], 'content_sha256': finding['explanation']['source_sha256']}}]}
        with temporary_test_directory() as directory:
            path = Path(directory) / 'source-observation.json'; cp.save(path, report)
            projected = load_source_findings([path], source)
        self.assertEqual(projected[0]['explanation'], finding['explanation'])
        payload = coordination_payload(source, plans, projected)
        self.assertEqual(payload['source_reference_findings'], projected)
        tampered = deepcopy(projected); tampered[0]['explanation']['text'] += ' Invented detail.'
        with self.assertRaisesRegex(ValueError, 'differs from complete source'):
            validate_source_findings(tampered, source)

    def test_previous_plan_prompt_gets_mechanical_revalidation_without_rewriting_receipt(self):
        source, plan, _ = self.fixture()
        messages = [{'role': 'system', 'content': PLAN_BEFORE_REFERENCE_RULES},
            {'role': 'user', 'content': json.dumps({**source, 'previous_issues': []})}]
        with temporary_test_directory() as directory:
            root = Path(directory); stage = root / 'planning'; attempt = stage / '1'
            cp.save(root / 'teaching-input.json', source)
            cp.save(attempt / 'generate.response.json', {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(plan)}}]})
            cp.save(attempt / 'generate.exec-proof.json', {'fixture_messages': messages})
            cp.save(stage / 'accepted.json', {'workflow': WORKFLOW, 'input': source, 'candidate': plan,
                'attempt': 1, 'semantic_review_performed': False,
                'prompt_fingerprints': {'generate': cp.digest(PLAN_BEFORE_REFERENCE_RULES)}})
            original = (stage / 'accepted.json').read_bytes()
            with patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                verify_plan(root, plan)
            self.assertEqual((stage / 'accepted.json').read_bytes(), original)
            checks = list((root / 'mechanical-revalidations').glob('*.json'))
            self.assertEqual(len(checks), 1)
            self.assertFalse(cp.read(checks[0])['semantic_review_performed'])
            self.assertEqual(cp.read(checks[0])['current_prompt_sha256'], cp.digest(PLAN))

    def test_unplanned_material_growth_preserves_frozen_unit_inputs(self):
        source, _, _ = self.fixture()
        with temporary_test_directory() as directory:
            root = Path(directory)
            freeze_teaching_catalog(root, source)
            frozen = root / 'units' / 'complete-unit' / 'teaching-input.json'
            frozen.parent.mkdir(parents=True); cp.save(frozen, unit_payload(source, 'complete-unit'))
            original = frozen.read_bytes()
            updated = deepcopy(source)
            extra = deepcopy(source['units'][0]); extra['unit_id'] = 'additional-complete-unit'
            extra['text'] = 'A separate complete reference.'
            updated['units'].append(extra)
            freeze_teaching_catalog(root, updated)
            self.assertEqual(frozen.read_bytes(), original)
            self.assertEqual(cp.read(root / 'teaching-input.json'), updated)
            self.assertEqual(cp.read(root / 'input-history' / (cp.digest(source) + '.json')), source)
            self.assertEqual(unit_payload(updated, 'complete-unit'), cp.read(frozen))
            changed = deepcopy(updated); changed['units'][0]['text'] += ' Changed.'
            with self.assertRaisesRegex(ValueError, 'Previously frozen material unit changed'):
                freeze_teaching_catalog(root, changed)
            self.assertEqual(cp.read(root / 'teaching-input.json'), updated)

    def test_new_material_revision_reuses_only_exact_verified_unit_plan(self):
        source, plan, _ = self.fixture()
        payload = unit_payload(source, 'complete-unit')
        messages = [{'role': 'system', 'content': PLAN},
                    {'role': 'user', 'content': json.dumps(payload)}]
        with temporary_test_directory() as directory:
            base = Path(directory)
            prior = base / 'original'
            old = prior / 'units/complete-unit'
            stage = old / 'planning'
            cp.save(old / 'teaching-input.json', payload)
            cp.save(stage / '1/generate.response.json', {'choices': [{'finish_reason': 'stop',
                'message': {'content': json.dumps(plan)}}]})
            cp.save(stage / '1/generate.exec-proof.json', {'fixture_messages': messages})
            cp.save(stage / 'accepted.json', {'workflow': WORKFLOW, 'input': payload,
                'candidate': plan, 'attempt': 1, 'prompt_fingerprints': {'generate': cp.digest(PLAN)}})
            old_bytes = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob('*') if p.is_file()}
            copied = base / 'current/units/complete-unit'
            cp.save(copied / 'teaching-input.json', payload)
            with patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                self.assertTrue(reuse_plan_from(prior, copied, payload))
                verify_plan(copied, plan)
                self.assertFalse(reuse_plan_from(prior, copied, payload))
            self.assertEqual({str(p.relative_to(old)): p.read_bytes() for p in old.rglob('*') if p.is_file()}, old_bytes)
            binding = cp.read(copied / 'planning/source-reuse.json')
            self.assertFalse(binding['new_generation_performed'])
            self.assertEqual(binding['input_sha256'], cp.digest(payload))
            changed = deepcopy(payload)
            changed['units'][0]['text'] += ' A newly supplied independent source detail.'
            other = base / 'changed/units/complete-unit'
            cp.save(other / 'teaching-input.json', changed)
            self.assertFalse(reuse_plan_from(prior, other, changed))
            self.assertFalse((other / 'planning').exists())
            changed_goals = deepcopy(payload)
            changed_goals['learning_objectives'].append('A new independent objective.')
            self.assertFalse(reuse_plan_from(prior, base / 'changed-goals/units/complete-unit', changed_goals))
            pending = old / 'planning-revisions/1'
            pending.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'unfinished revision'):
                reuse_plan_from(prior, base / 'unfinished/units/complete-unit', payload)
            pending.rmdir()
            proof_path = stage / '1/generate.exec-proof.json'
            original_proof = proof_path.read_bytes()
            changed_messages = deepcopy(messages)
            changed_messages[0]['content'] = 'A different production policy.'
            proof_path.write_text(json.dumps({'fixture_messages': changed_messages}), encoding='utf-8')
            with patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                with self.assertRaisesRegex(ValueError, 'older production prompt'):
                    reuse_plan_from(prior, base / 'changed-prompt/units/complete-unit', payload)
            proof_path.write_bytes(original_proof)
            tampered = cp.read(stage / 'accepted.json')
            tampered['candidate']['title'] = 'Changed without execution'
            (stage / 'accepted.json').write_text(json.dumps(tampered), encoding='utf-8')
            untouched = base / 'tampered/units/complete-unit'
            cp.save(untouched / 'teaching-input.json', payload)
            with self.assertRaisesRegex(ValueError, 'immutable response'):
                reuse_plan_from(prior, untouched, payload)
            self.assertFalse((untouched / 'planning').exists())

    def test_course_relations_preserve_local_edges_and_reject_cross_unit_cycles(self):
        _, plan, _ = self.fixture()
        plans = {'foundation': deepcopy(plan), 'application': deepcopy(plan)}
        extra = deepcopy(plan['topics'][0]); extra.update(id='contrast', prerequisites=['facts'])
        plans['application']['topics'].append(extra)
        relation = {'prerequisites': [{'from': 'foundation/facts', 'to': 'application/facts',
            'knowledge': 'Basic distinctions', 'reason': 'Needed to understand the new contrast.'}],
            'reuse': [], 'conflicts': []}
        validate_relations(relation, plans)
        self.assertEqual(native_topic_id('course', relation['prerequisites'][0]['from']), 'course--foundation--facts')
        bad = deepcopy(relation); bad['prerequisites'].append({'from': 'application/contrast', 'to': 'foundation/facts',
            'knowledge': 'Reverse knowledge', 'reason': 'This would form a cycle with local and cross-unit edges.'})
        with self.assertRaisesRegex(ValueError, 'Cyclic'): validate_relations(bad, plans)
        bad = deepcopy(relation); bad['prerequisites'][0]['to'] = 'missing/facts'
        with self.assertRaisesRegex(ValueError, 'Unknown'): validate_relations(bad, plans)

    def test_course_reuse_is_context_and_scope_conflicts_prevent_authoring(self):
        _, plan, _ = self.fixture()
        plans = {'foundation': deepcopy(plan), 'application': deepcopy(plan)}
        before = deepcopy(plans)
        relation = {'prerequisites': [], 'reuse': [{'consumer': 'application/facts', 'provider': 'foundation/facts',
            'knowledge': ['Shared basic distinction'], 'use': 'recap', 'reason': 'One brief reminder is sufficient.'}], 'conflicts': []}
        validate_relations(relation, plans)
        context = author_course_context('application/facts', relation, plans)
        self.assertEqual(context['prerequisites'], [])
        self.assertEqual(context['providers'][0]['topic_ref'], 'foundation/facts')
        self.assertEqual(context['primary_teaching'], [])
        provider = author_course_context('foundation/facts', relation, plans)
        self.assertEqual(provider['primary_teaching'], relation['reuse'])
        self.assertEqual(provider['prerequisites'], [])
        changed = deepcopy(relation); changed['reuse'][0]['knowledge'].append('Another shared foundation')
        self.assertNotEqual(cp.digest(context), cp.digest(author_course_context('application/facts', changed, plans)))
        relation['conflicts'] = [{'unit_ids': ['application'],
            'topic_refs': ['foundation/facts', 'application/facts'], 'issue': 'Both teach the same foundation.',
            'repair_instructions': ['Choose the primary teaching home and replan the consumer scope.']}]
        validate_relations(relation, plans)
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            author_course_context('application/facts', relation, plans)
        invalid = deepcopy(relation); invalid['conflicts'][0]['unit_ids'] = ['textbook-source']
        with self.assertRaisesRegex(ValueError, 'valid_owners_of_supplied_topics.*application.*foundation'):
            validate_relations(invalid, plans)
        self.assertEqual(plans, before)

    def test_course_coordination_requires_complete_plans_and_does_not_invent_hint_edges(self):
        source, plan, _ = self.fixture()
        second = deepcopy(source['units'][0]); second['unit_id'] = 'other-unit'; source['units'].append(second)
        source['scope_dependencies'] = [{'units': ['other-unit'], 'prerequisites': ['complete-unit']}]
        plans = {'complete-unit': deepcopy(plan), 'other-unit': deepcopy(plan)}
        payload = coordination_payload(source, plans)
        self.assertEqual(payload['source_dependency_hints'], source['scope_dependencies'])
        self.assertNotIn('text', payload['source_scopes'][0])
        empty = {'prerequisites': [], 'reuse': [], 'conflicts': []}
        self.assertEqual(validate_relations(empty, plans)['prerequisites'], [])
        with self.assertRaisesRegex(ValueError, 'All material-unit plans'):
            coordination_payload(source, {'complete-unit': plan})
        with temporary_test_directory() as directory:
            root = Path(directory)
            self.assertEqual(active_planning_root(root), root / 'planning')
            (root / 'planning-revisions/1').mkdir(parents=True)
            cp.save(root / 'planning-revisions/1/accepted.json', {'candidate': plan})
            (root / 'planning-revisions/2').mkdir()
            self.assertEqual(active_planning_root(root), root / 'planning-revisions/1')

    def test_partial_curriculum_repair_resumes_only_unfinished_unit_with_complete_context(self):
        source, original, _ = self.fixture()
        template = source['units'][0]
        source['units'], plans = [], {}
        for ident in ('foundation', 'consumer-a', 'consumer-b'):
            unit = deepcopy(template); unit['unit_id'] = ident
            source['units'].append(unit)
            plan = deepcopy(original)
            plan['topics'][0]['coverage'][0]['unit_id'] = ident
            plans[ident] = plan
        feedback = {'prerequisites': [], 'reuse': [], 'conflicts': [
            {'affected_topic_refs': [ident + '/facts'], 'topic_refs': ['foundation/facts', ident + '/facts'],
             'issue': 'The shared definition has an established primary home.',
             'repair_instructions': ['Keep the complete mixture distinctions and reuse the shared definition.']}
            for ident in ('consumer-a', 'consumer-b')]}
        calls = []
        def save_phase(folder, prompt, current, candidate):
            messages = [{'role': 'system', 'content': prompt},
                        {'role': 'user', 'content': json.dumps(current, ensure_ascii=False)}]
            cp.save(folder / 'generate.request.json', {'messages': messages})
            cp.save(folder / 'generate.response.json', candidate)
            cp.save(folder / 'generate.exec-proof.json', {'fixture_messages': messages})
        def execute(folder, phase, prompt, current, timeout):
            self.assertEqual((phase, prompt), ('generate', PLAN_REPAIR))
            ident = current['curriculum_coordination']['unit_id']; calls.append(ident)
            self.assertEqual(current['teaching'], unit_payload(source, ident))
            context = current['curriculum_coordination']
            self.assertEqual(context['original_complete_plan']['plan'], plans[ident])
            rows = context['related_complete_topics']
            self.assertEqual({row['topic_ref']: row['topic'] for row in rows},
                             {'foundation/facts': plans['foundation']['topics'][0]})
            self.assertEqual(rows[0]['full_plan_sha256'], cp.digest(plans['foundation']))
            external = current['curriculum_coordination'].get('external_observations', [])
            if ident == 'consumer-a':
                self.assertEqual(external[0]['observations'][0]['finding'],
                                 'One complete colour source is present, not two.')
                self.assertNotIn('accepted_path', external[0]['evidence'])
            else:
                self.assertEqual(external, [])
            candidate = deepcopy(plans[ident]); candidate['title'] += ' with distinct mixture scope'
            if ident == 'consumer-b' and calls.count(ident) == 1:
                candidate['topics'][0]['coverage'][0]['subunit_ids'] = ['first']
            save_phase(folder, prompt, current, candidate)
            return candidate
        with temporary_test_directory() as directory:
            run = Path(directory)
            for ident, plan in plans.items():
                root = run / 'units' / ident; payload = unit_payload(source, ident)
                cp.save(root / 'teaching-input.json', payload)
                save_phase(root / 'planning/1', PLAN, payload, plan)
                cp.save(root / 'planning/accepted.json', {'workflow': WORKFLOW, 'input': payload,
                    'candidate': plan, 'attempt': 1, 'prompt_fingerprints': {'generate': cp.digest(PLAN)}})
            root, payload = course_relation_stage(run, source, plans)
            cp.save(root / 'complete-coordination-input.json', unpack_coordination_payload(payload))
            save_phase(root / '1', RELATIONS, payload, feedback)
            cp.save(root / 'accepted.json', {'workflow': WORKFLOW, 'input': payload,
                'candidate': feedback, 'attempt': 1, 'prompt_fingerprints': {'generate': cp.digest(RELATIONS)}})
            evidence = root / 'accepted.json'
            evidence_bytes = evidence.read_bytes()
            report = {'role': 'curriculum_coordination_observation', 'reviewer': 'subagent',
                'candidate_modified': False, 'inputs': {'accepted_path': str(evidence),
                    'accepted_file_sha256': hashlib.sha256(evidence_bytes).hexdigest(),
                    'input_digest': cp.digest(payload), 'candidate_digest': cp.digest(feedback)},
                'observations': [{'affected_topic_refs': ['consumer-a/facts'],
                    'finding': 'One complete colour source is present, not two.',
                    'repair_instructions': ['Preserve all mixture uses and correct only the shared foundation.']}]}
            observation_path = run / 'independent-observation.json'
            cp.save(observation_path, report)
            old_bytes = {ident: (run / 'units' / ident / 'planning/accepted.json').read_bytes() for ident in plans}
            args = SimpleNamespace(source=run / 'unused.json', objectives=None, output=run,
                unit=None, topic=None, workers=1, attempts=1, timeout=30, plan_only=True,
                plan_feedback=root / 'accepted.json', stop_file=None,
                coordination_observations=[observation_path])
            with patch('produce_goal_course.load_materials', return_value=source), \
                 patch('produce_goal_course.call', side_effect=execute), \
                 patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                projected = project_coordination_observations([observation_path], evidence, payload, plans, source)
                self.assertEqual(projected[0]['observations'], report['observations'])
                for label, mutate in (
                    ('candidate', lambda value: value['inputs'].update(candidate_digest='not-the-candidate')),
                    ('file', lambda value: value['inputs'].update(accepted_file_sha256='not-the-file')),
                    ('provider', lambda value: value['observations'][0].update(affected_topic_refs=['foundation/facts'])),
                    ('unknown', lambda value: value['observations'][0].update(affected_topic_refs=['unknown/facts'])),
                    ('approval', lambda value: value.update(approved=True)),
                ):
                    bad = deepcopy(report); mutate(bad)
                    bad_path = run / ('invalid-observation-' + label + '.json')
                    cp.save(bad_path, bad)
                    with self.subTest(binding=label), self.assertRaises(ValueError):
                        project_coordination_observations([bad_path], evidence, payload, plans, source)
                with self.assertRaisesRegex(ValueError, 'did not pass'):
                    produce(args)
                first = run / 'units/consumer-a/planning-revisions/1/accepted.json'
                completed_bytes = first.read_bytes()
                produce(args)
                produce(args)
                self.assertEqual(calls, ['consumer-a', 'consumer-b', 'consumer-b'])
                self.assertEqual(first.read_bytes(), completed_bytes)
                self.assertFalse((run / 'units/consumer-a/planning-revisions/2').exists())
                self.assertFalse((run / 'units/foundation/planning-revisions').exists())
                self.assertEqual(evidence.read_bytes(), evidence_bytes)
                context = run / 'units/consumer-a/planning-revisions/1/curriculum-context.json'
                tampered = cp.read(context); tampered['conflicts'][0]['issue'] = 'A different claim'
                context.write_text(json.dumps(tampered), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'context changed'):
                    verified_plan_feedback(run, source, args.plan_feedback)
            for ident in plans:
                self.assertEqual((run / 'units' / ident / 'planning/accepted.json').read_bytes(), old_bytes[ident])

    def test_repair_projection_keeps_whole_local_plan_and_complete_referenced_external_topics(self):
        source, plans, finding, _ = self.reference_fixture()
        sibling = deepcopy(plans['colour-details']['topics'][0])
        sibling.update(id='unrelated-shapes', title='A separate shape explanation')
        plans['colour-details']['topics'].append(sibling)
        before = deepcopy(plans)
        payload = pack_coordination_payload(coordination_payload(source, plans, [finding]))
        feedback = {'prerequisites': [], 'reuse': [], 'conflicts': [{
            'unit_ids': ['complete-unit'], 'affected_topic_refs': ['complete-unit/facts'],
            'topic_refs': ['complete-unit/facts', 'colour-details/facts'],
            'issue': 'A shared explanation requires one primary home.',
            'repair_instructions': ['Keep all mixture content; reuse the colour foundation.']}]}
        context = plan_repair_context('complete-unit', feedback, payload, plans)
        self.assertEqual(context['original_complete_plan']['plan'], plans['complete-unit'])
        self.assertEqual(len(context['related_complete_topics']), 1)
        record = context['related_complete_topics'][0]
        self.assertEqual(record['topic_ref'], 'colour-details/facts')
        self.assertEqual(record['topic'], plans['colour-details']['topics'][0])
        self.assertEqual(record['full_plan_sha256'], cp.digest(plans['colour-details']))
        self.assertEqual(record['topic_sha256'], cp.digest(record['topic']))
        self.assertNotIn('unrelated-shapes', json.dumps(context))
        self.assertEqual(plans, before)
        changed = deepcopy(plans); changed['colour-details']['topics'][0]['sections'][0]['scope'] += ' New detail.'
        with self.assertRaisesRegex(ValueError, 'authenticated complete plans'):
            plan_repair_context('complete-unit', feedback, payload, changed)

    def test_coordination_tables_restore_every_scope_and_bind_full_plan_changes(self):
        source, plans, finding, _ = self.reference_fixture()
        plans['complete-unit']['topics'][0]['sections'][0]['scope'] = 'A complete detail: "red" → 红色。\nKeep both examples.'
        payload = coordination_payload(source, plans, [finding])
        before = deepcopy(payload)
        packed = pack_coordination_payload(payload)
        self.assertEqual(unpack_coordination_payload(packed), payload)
        self.assertEqual(packed['source_reference_findings'], [finding])
        self.assertEqual(packed['full_input_sha256'], cp.digest(payload))
        self.assertEqual(payload, before)
        changed = deepcopy(plans)
        changed['complete-unit']['topics'][0]['sections'][0]['scope'] += ' An additional distinction.'
        updated = pack_coordination_payload(coordination_payload(source, changed, [finding]))
        self.assertNotEqual(cp.digest(updated), cp.digest(packed))
        tampered = deepcopy(packed)
        tampered['plan_tables']['rows'][0][5][0][7][0][2] = 'A silently shortened scope.'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            unpack_coordination_payload(tampered)
        unknown = deepcopy(payload)
        unknown['unit_plans'][0]['plan']['topics'][0]['hidden_field'] = 'Cannot be silently dropped.'
        with self.assertRaisesRegex(ValueError, 'Unexpected coordination table'):
            pack_coordination_payload(unknown)

    def coordination_batch_fixture(self):
        source, plan, _ = self.fixture()
        original = source['units'][0]
        source['units'], plans = [], {}
        for index in range(4):
            ident = 'colour-' + str(index)
            unit = deepcopy(original); unit.update(unit_id=ident, title='Complete colour chapter ' + str(index))
            source['units'].append(unit)
            current = deepcopy(plan)
            current['topics'][0]['coverage'][0]['unit_id'] = ident
            current['topics'][0]['sections'][0]['scope'] = ('An exact, complete distinction and its full contrasting example. ' * 70) + ident
            plans[ident] = current
        full = coordination_payload(source, plans)
        ids = list(plans)[:2]
        pairs = list(combinations_with_replacement(ids, 2))
        limit = coordination_request_chars(coordination_batch_payload(full, ids, pairs), BATCH_RELATIONS) + 100
        partition, inputs = partition_coordination(full, limit)
        return source, plans, full, partition, inputs

    def test_bounded_coordination_keeps_full_plans_and_assigns_every_pair_once(self):
        source, plans, full, partition, inputs = self.coordination_batch_fixture()
        expected = set(combinations_with_replacement(sorted(plans), 2))
        seen = []
        for batch in partition['batches']:
            self.assertLessEqual(batch['request_chars'], partition['max_request_chars'])
            restored = unpack_coordination_payload(inputs[batch['id']])
            self.assertEqual([row['unit_id'] for row in restored['unit_plans']], batch['unit_ids'])
            for row in restored['unit_plans']:
                self.assertEqual(row['plan'], plans[row['unit_id']])
            seen.extend(tuple(pair) for pair in batch['focus_pairs'])
        self.assertEqual(set(seen), expected); self.assertEqual(len(seen), len(expected))
        self.assertEqual(partition_coordination(full, partition['max_request_chars']), (partition, inputs))
        with self.assertRaisesRegex(ValueError, 'do not truncate'):
            partition_coordination(full, 1)
        self.assertEqual(full, coordination_payload(source, plans))
        long_root = Path('D:/Artifacts') / ('independent-' * 6) / 'course-coordination' / ('a' * 64)
        stage = coordination_batch_stage(long_root, next(iter(inputs.values())))
        self.assertLess(len(str(stage / '1/generate-exec' / ('20260101T000000-' + 'b' * 32) /
                                'restricted-model-catalog.json')), 250)
        changed = deepcopy(full)
        changed['unit_plans'][-1]['plan']['topics'][0]['sections'][0]['scope'] += ' A new independent detail.'
        unaffected = next(batch for batch in partition['batches'] if 'colour-3' not in batch['unit_ids'])
        self.assertEqual(coordination_batch_payload(changed, unaffected['unit_ids'], unaffected['focus_pairs']),
                         inputs[unaffected['id']])

    def test_coordination_union_rejects_omission_unassigned_pairs_and_global_cycles(self):
        source, plans, full, partition, inputs = self.coordination_batch_fixture()
        empty = {'prerequisites': [], 'reuse': [], 'conflicts': [], 'reference_resolutions': []}
        results = {ident: deepcopy(empty) for ident in inputs}
        def edge(left, right):
            return {'from': left + '/facts', 'to': right + '/facts',
                    'knowledge': 'A required distinction', 'reason': 'The consumer requires this particular foundation.'}
        for left, right in [('colour-0', 'colour-1'), ('colour-1', 'colour-2'), ('colour-2', 'colour-0')]:
            pair = sorted((left, right))
            owner = next(batch['id'] for batch in partition['batches'] if pair in batch['focus_pairs'])
            results[owner]['prerequisites'].append(edge(left, right))
        for ident, value in results.items():
            validate_batch_relations(value, inputs[ident])
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            combine_coordination(partition, inputs, results, plans, source, [])
        missing = dict(results); missing.pop(next(iter(missing)))
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            combine_coordination(partition, inputs, missing, plans, source, [])
        batch = next(b for b in partition['batches'] if any([u, u] not in b['focus_pairs'] for u in b['unit_ids']))
        ident = next(u for u in batch['unit_ids'] if [u, u] not in batch['focus_pairs'])
        wrong = deepcopy(empty)
        wrong['conflicts'] = [{'unit_ids': [ident], 'topic_refs': [ident + '/facts'],
            'issue': 'A duplicate explanation', 'repair_instructions': ['Keep one primary explanation.']}]
        with self.assertRaisesRegex(ValueError, 'assigned unit pair'):
            validate_batch_relations(wrong, inputs[batch['id']])

    def test_coordination_union_preserves_conflicts_and_authenticates_every_batch_on_resume(self):
        source, plans, full, partition, inputs = self.coordination_batch_fixture()
        payload = pack_coordination_payload(full)
        empty = {'prerequisites': [], 'reuse': [], 'conflicts': [], 'reference_resolutions': []}
        calls = []
        def execute(folder, phase, prompt, current, timeout):
            self.assertEqual(phase, 'generate'); self.assertEqual(prompt, BATCH_RELATIONS)
            calls.append(folder)
            value = deepcopy(empty)
            if ['colour-0', 'colour-1'] in current['batch_focus']['unit_pairs']:
                value['conflicts'] = [{'affected_topic_refs': ['colour-0/facts', 'colour-1/facts'],
                    'topic_refs': ['colour-0/facts', 'colour-1/facts'], 'issue': 'Both claim the same primary explanation.',
                    'repair_instructions': ['Replan the consumer from its original material; retain the primary home.']}]
            messages = [{'role': 'system', 'content': prompt},
                        {'role': 'user', 'content': json.dumps(current, ensure_ascii=False)}]
            cp.save(folder / 'generate.response.json', value)
            cp.save(folder / 'generate.exec-proof.json', {'fixture_messages': messages})
            return value
        with temporary_test_directory() as directory:
            root = Path(directory) / 'course-coordination/whole'
            args = SimpleNamespace(workers=2, attempts=1, timeout=30,
                                   coordination_max_chars=partition['max_request_chars'])
            with patch('produce_goal_course.call', side_effect=execute), \
                 patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                candidate, evidence = run_course_coordination(root, payload, plans, source, args)
                self.assertEqual(len(candidate['conflicts']), 1)
                self.assertEqual(evidence.name, 'combined.json')
                self.assertFalse((root / 'accepted.json').exists())
                self.assertEqual(len(calls), len(partition['batches']))
                self.assertEqual(run_course_coordination(root, payload, plans, source, args), (candidate, evidence))
                self.assertEqual(len(calls), len(partition['batches']))
                record = cp.read(evidence)
                self.assertEqual(record['kind'], 'mechanical-coordination-union')
                self.assertEqual(len(record['item_origins']['conflicts']), 1)
                record['candidate']['conflicts'] = []
                evidence.write_text(json.dumps(record), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'immutable batch'):
                    read_coordination_result(root, payload, plans, source)

    def test_conflict_owners_are_derived_from_affected_topics_without_replanning_provider(self):
        _, plan, _ = self.fixture()
        plans = {'provider': plan, 'consumer': deepcopy(plan)}
        raw = {'prerequisites': [], 'reuse': [], 'conflicts': [{
            'affected_topic_refs': ['consumer/facts'], 'topic_refs': ['provider/facts', 'consumer/facts'],
            'issue': 'The consumer duplicates the established definition.',
            'repair_instructions': ['Keep its distinct application and reuse the primary definition.']}]}
        before = deepcopy(raw)
        actual = project_coordination_response(raw, plans)
        self.assertEqual(actual['conflicts'][0]['unit_ids'], ['consumer'])
        self.assertEqual(actual['conflicts'][0]['affected_topic_refs'], ['consumer/facts'])
        self.assertEqual(raw, before)
        validate_relations(actual, plans)
        bad = deepcopy(raw); bad['conflicts'][0]['unit_ids'] = ['a-source-unit']
        with self.assertRaisesRegex(ValueError, 'not freely generated'):
            project_coordination_response(bad, plans)
        bad = deepcopy(raw); bad['conflicts'][0]['affected_topic_refs'] = ['a-source-unit/facts']
        with self.assertRaisesRegex(ValueError, 'unknown or unrelated'):
            project_coordination_response(bad, plans)
        bad = deepcopy(actual); bad['conflicts'][0]['unit_ids'].append('provider')
        with self.assertRaisesRegex(ValueError, 'Derived curriculum owners'):
            validate_relations(bad, plans)
        legacy = deepcopy(raw)
        legacy['conflicts'][0].pop('affected_topic_refs')
        legacy['conflicts'][0]['unit_ids'] = ['consumer']
        self.assertEqual(project_coordination_response(legacy, plans, legacy=True), legacy)
        self.assertNotIn('affected_topic_refs', project_coordination_response(legacy, plans, legacy=True)['conflicts'][0])

    def test_exact_accepted_coordination_can_be_reused_without_changing_old_contract_evidence(self):
        source, _, _, _, inputs = self.coordination_batch_fixture()
        payload = next(iter(inputs.values()))
        value = {'prerequisites': [], 'reuse': [], 'conflicts': [], 'reference_resolutions': []}
        messages = [{'role': 'system', 'content': BATCH_RELATIONS_BEFORE_TOPIC_OWNERS},
                    {'role': 'user', 'content': json.dumps(payload)}]
        with temporary_test_directory() as directory:
            root = Path(directory) / 'course-coordination/new-contract'
            old = root.parent / 'calls' / cp.digest({'prompt': BATCH_RELATIONS_BEFORE_TOPIC_OWNERS, 'input': payload})[:16]
            cp.save(old / '1/generate.response.json', value)
            cp.save(old / '1/generate.exec-proof.json', {'fixture_messages': messages})
            # An output without its real acceptance must not cross the boundary.
            self.assertFalse(reuse_compatible_coordination(root, payload, source))
            cp.save(old / 'accepted.json', {'workflow': WORKFLOW, 'input': payload, 'candidate': value,
                'attempt': 1, 'prompt_fingerprints': {'generate': cp.digest(BATCH_RELATIONS_BEFORE_TOPIC_OWNERS)}})
            original = (old / 'accepted.json').read_bytes()
            with patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                self.assertTrue(reuse_compatible_coordination(root, payload, source))
                stage = coordination_batch_stage(root, payload)
                self.assertEqual(verify_coordination_batch(stage, payload, source), value)
                self.assertEqual((stage / 'accepted.json').read_bytes(), original)
                self.assertFalse(cp.read(stage / 'contract-compatibility.json')['new_generation_performed'])
                self.assertFalse(reuse_compatible_coordination(root, payload, source))
                self.assertEqual((old / 'accepted.json').read_bytes(), original)

    def test_old_contract_rejection_is_authenticated_feedback_not_edited_acceptance(self):
        source, _, _, _, inputs = self.coordination_batch_fixture()
        payload = next(iter(inputs.values()))
        owner = unpack_coordination_payload(payload)['unit_plans'][0]['unit_id']
        value = {'prerequisites': [], 'reuse': [], 'reference_resolutions': [], 'conflicts': [{
            'unit_ids': ['source-document-not-a-curriculum-owner'], 'topic_refs': [owner + '/facts'],
            'issue': 'Duplicate foundational explanation.', 'repair_instructions': ['Keep one primary explanation.']}]}
        messages = [{'role': 'system', 'content': BATCH_RELATIONS_BEFORE_TOPIC_OWNERS},
                    {'role': 'user', 'content': json.dumps({**payload, 'previous_issues': []})}]
        with temporary_test_directory() as directory:
            root = Path(directory) / 'course-coordination/new-contract'
            old = root.parent / 'calls' / cp.digest({'prompt': BATCH_RELATIONS_BEFORE_TOPIC_OWNERS, 'input': payload})[:16]
            cp.save(old / '1/generate.request.json', {'messages': messages})
            cp.save(old / '1/generate.response.json', value)
            cp.save(old / '1/generate.exec-proof.json', {'fixture_messages': messages})
            self.assertEqual(prior_coordination_rejection(root, payload, source), [])
            cp.save(old / '1/rejection.json', {'issues': ['Unknown curriculum owner']})
            original = (old / '1/generate.response.json').read_bytes()
            with patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                feedback = prior_coordination_rejection(root, payload, source)
                self.assertIn('Unknown curriculum owner', feedback[0])
                stage = coordination_batch_stage(root, payload)
                self.assertFalse(cp.read(stage / 'prior-contract-rejection.json')['accepted'])
                self.assertFalse((stage / 'accepted.json').exists())
                self.assertEqual((old / '1/generate.response.json').read_bytes(), original)
                changed = deepcopy(messages); changed[-1]['content'] = json.dumps({'other': 'input'})
                (old / '1/generate.request.json').write_text(json.dumps({'messages': changed}), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'different request'):
                    prior_coordination_rejection(root, payload, source)

    def test_readable_course_directory_projects_actual_order_and_only_real_prerequisites(self):
        source, plan, _ = self.fixture()
        extra = deepcopy(source['units'][0]); extra.update(unit_id='application', title='Complete mixture chapter')
        source['units'].append(extra)
        basic = deepcopy(plan); basic['topics'][0]['title'] = 'Colour foundations'
        applied = deepcopy(plan); applied['topics'][0].update(title='Colour mixtures', objectives=['Explain mixtures'])
        applied['topics'][0]['coverage'][0]['unit_id'] = 'application'
        follow = deepcopy(applied['topics'][0]); follow.update(id='contrast', title='Mixture contrasts',
            objectives=['Distinguish mixture outcomes'], prerequisites=['facts'])
        applied['topics'].append(follow)
        plans = {'application': applied, 'complete-unit': basic}
        relations = {'prerequisites': [{'from': 'complete-unit/facts', 'to': 'application/facts',
            'knowledge': 'Colour foundations', 'reason': 'Required for understanding mixtures.'}],
            'reuse': [{'consumer': 'application/contrast', 'provider': 'complete-unit/facts',
                'knowledge': ['Basic colours'], 'use': 'recap', 'reason': 'Brief context only.'}], 'conflicts': []}
        before = deepcopy((source, plans, relations))
        with temporary_test_directory() as directory:
            path = write_course_plan_preview(Path(directory), source, plans, relations)
            text = path.read_text(encoding='utf-8')
            self.assertLess(text.index('## Colour foundations'), text.index('## Colour mixtures'))
            self.assertLess(text.index('## Colour mixtures'), text.index('## Mixture contrasts'))
            self.assertIn('必要先修：Colour foundations。', text)
            contrast = text.split('## Mixture contrasts')[1]
            self.assertIn('必要先修：Colour mixtures。', contrast)
            self.assertNotIn('必要先修：Colour foundations', contrast)
            self.assertIn('- First\n', text)
            self.assertIn('来源：Complete mixture chapter。', text)
            self.assertNotIn('application/facts', text)
            self.assertNotIn('sha256', text)
            self.assertNotIn('D:/', text)
            conflicting = deepcopy(relations)
            conflicting['conflicts'] = [{'unit_ids': ['application'], 'topic_refs': ['application/facts'],
                'issue': 'Responsibilities overlap.', 'repair_instructions': ['Replan the affected unit.']}]
            with self.assertRaisesRegex(ValueError, 'Unresolved'):
                write_course_plan_preview(Path(directory), source, plans, conflicting)
            self.assertEqual(path.read_text(encoding='utf-8'), text)
        self.assertEqual((source, plans, relations), before)

    def test_terminal_empty_start_can_resume_without_reclassifying_as_teaching_failure(self):
        payload = {'teaching': 'Independent material'}
        messages = [{'role': 'system', 'content': 'frozen prompt'},
                    {'role': 'user', 'content': json.dumps({**payload, 'previous_issues': []}, ensure_ascii=False)}]
        with temporary_test_directory() as directory:
            root = Path(directory); attempt = root / '1'; call_dir = attempt / 'generate-exec' / 'failed-call'
            call_dir.mkdir(parents=True)
            cp.save(attempt / 'generate.request.json', {'messages': messages})
            cp.save(call_dir / 'messages.json', messages)
            (call_dir / 'prompt.txt').write_text('immutable transport input', encoding='utf-8')
            (call_dir / 'events.jsonl').write_text('', encoding='utf-8')
            (call_dir / 'final.txt').write_text('', encoding='utf-8')
            receipt = {'status': 'failed', 'finished_at': '2026-01-01T00:00:00Z', 'exit_code': 1,
                'event_check': {'event_count': 0}, 'usage': None, 'call_dir': str(call_dir),
                'input_sha256': hashlib.sha256((call_dir / 'prompt.txt').read_bytes()).hexdigest()}
            cp.save(call_dir / 'receipt.json', receipt)
            args = SimpleNamespace(attempts=1, timeout=30, retry_failed_start=True)
            with patch('produce_goal_course.call', return_value={'ok': True}) as caller:
                self.assertEqual(accepted_stage(root, 'frozen prompt', payload, lambda _: None, args,
                    review_required=False), {'ok': True})
                self.assertEqual(caller.call_args.args[0], root / '2')
                self.assertEqual(caller.call_args.args[3]['previous_issues'], [])
            self.assertFalse(cp.read(attempt / 'failed-start-resume.json')['semantic_rejection'])
            with self.assertRaisesRegex(ValueError, 'request changed'):
                verify_failed_start(attempt, 'changed prompt', {**payload, 'previous_issues': []})
            receipt['status'] = 'running'
            (call_dir / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'unknown outcome'):
                verify_failed_start(attempt, 'frozen prompt', {**payload, 'previous_issues': []})
            receipt['status'] = 'failed'; receipt['event_check']['event_count'] = 1
            (call_dir / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'model output'):
                verify_failed_start(attempt, 'frozen prompt', {**payload, 'previous_issues': []})

    def test_failed_review_resumes_same_candidate_and_retains_original_proof(self):
        payload, candidate = {'teaching': 'Frozen unrelated facts'}, {'id': 'unchanged'}
        review_payload = {'source': payload, 'candidate': candidate}
        review_messages = [{'role': 'system', 'content': REVIEW},
                           {'role': 'user', 'content': json.dumps(review_payload, ensure_ascii=False)}]
        generate_messages = [{'role': 'system', 'content': 'author prompt'},
            {'role': 'user', 'content': json.dumps({**payload, 'previous_issues': []}, ensure_ascii=False)}]
        approved = {'approved': True, 'issues': []}
        def response(value):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}
        with temporary_test_directory() as directory:
            root = Path(directory); attempt = root / '1'; call_dir = attempt / 'review-exec' / 'failed-call'
            call_dir.mkdir(parents=True)
            cp.save(attempt / 'generate.request.json', {'messages': generate_messages})
            cp.save(attempt / 'generate.response.json', response(candidate))
            cp.save(attempt / 'generate.exec-proof.json', {'fixture_messages': generate_messages})
            original_hash = hashlib.sha256((attempt / 'generate.response.json').read_bytes()).hexdigest()
            cp.save(attempt / 'review.request.json', {'messages': review_messages})
            cp.save(attempt / 'review.started.json', {'started_at': '2026-01-01T00:00:00Z'})
            cp.save(call_dir / 'messages.json', review_messages)
            (call_dir / 'prompt.txt').write_text('same review transport', encoding='utf-8')
            (call_dir / 'events.jsonl').write_text('', encoding='utf-8')
            (call_dir / 'final.txt').write_text('', encoding='utf-8')
            cp.save(call_dir / 'receipt.json', {'status': 'failed', 'finished_at': '2026-01-01T00:00:01Z',
                'exit_code': 1, 'event_check': {'event_count': 0}, 'call_dir': str(call_dir),
                'input_sha256': hashlib.sha256((call_dir / 'prompt.txt').read_bytes()).hexdigest()})
            args = SimpleNamespace(attempts=1, timeout=30, retry_failed_start=True, review_only=True)
            def retained_or_review(folder, phase, prompt, current, timeout):
                if phase == 'generate':
                    self.assertEqual(folder, attempt)
                    self.assertTrue((folder / 'generate.response.json').exists())
                    return candidate
                self.assertEqual(folder, attempt / 'review-retries' / '1')
                self.assertEqual(current, review_payload)
                cp.save(folder / 'review.response.json', response(approved))
                cp.save(folder / 'review.exec-proof.json', {'fixture_messages': review_messages})
                return approved
            with patch('produce_goal_course.call', side_effect=retained_or_review), \
                 patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                self.assertEqual(accepted_stage(root, 'author prompt', payload, lambda _: None, args), candidate)
                record = cp.read(root / 'accepted.json')
                self.assertEqual(record['attempt'], 1); self.assertEqual(record['review_retry'], 1)
                self.assertEqual(verify_accepted(root, record, expected_input=payload, prompt='author prompt'), candidate)
            self.assertEqual(hashlib.sha256((attempt / 'generate.response.json').read_bytes()).hexdigest(), original_hash)
            self.assertFalse((attempt / 'rejection.json').exists()); self.assertFalse((root / '2').exists())
            self.assertFalse(cp.read(attempt / 'review-failed-start-resume.json')['semantic_rejection'])

    def test_unknown_review_outcome_never_becomes_candidate_rejection(self):
        with temporary_test_directory() as directory:
            root = Path(directory)
            args = SimpleNamespace(attempts=3, timeout=30)
            with patch('produce_goal_course.call', side_effect=[{'id': 'retained'},
                    ValueError('Previous Codex Exec outcome unknown')]) as caller:
                with self.assertRaisesRegex(ValueError, 'outcome unknown'):
                    accepted_stage(root, 'prompt', {'teaching': 'Independent facts'}, lambda _: None, args)
                self.assertEqual(caller.call_count, 2)
            self.assertFalse((root / '1/rejection.json').exists())
            self.assertFalse((root / '2').exists())

    def test_review_only_refuses_missing_candidate_without_any_generation(self):
        with temporary_test_directory() as directory:
            root = Path(directory)
            args = SimpleNamespace(attempts=3, timeout=30, review_only=True)
            with patch('produce_goal_course.call', side_effect=AssertionError('Generation is forbidden')):
                with self.assertRaisesRegex(ValueError, 'generation is forbidden'):
                    accepted_stage(root, 'prompt', {'teaching': 'Whole source'}, lambda _: None, args)

    def test_bounded_dispatch_stops_after_failure_and_keeps_running_result(self):
        started, retained = [], []
        both_started = threading.Barrier(2)
        failure_observed = threading.Event()
        def task(index):
            started.append(index)
            both_started.wait(timeout=3)
            if index == 0:
                raise RuntimeError('Terminal infrastructure failure')
            self.assertTrue(failure_observed.wait(timeout=3))
            retained.append(index)
            return index
        def wait_for_failure(*args, **kwargs):
            result = futures_wait(*args, **kwargs)
            if any(future.exception() is not None for future in result[0]):
                failure_observed.set()
            return result
        args = SimpleNamespace(workers=2)
        with patch('produce_goal_course.wait', side_effect=wait_for_failure):
            with self.assertRaisesRegex(RuntimeError, 'infrastructure'):
                list(bounded_results(task, range(20), args))
        self.assertEqual(sorted(started), [0, 1])
        self.assertEqual(retained, [1])

    def test_bounded_dispatch_stop_marker_prevents_next_topic(self):
        with temporary_test_directory() as directory:
            marker = Path(directory) / 'STOP-DISPATCH'
            started = []
            def task(index):
                started.append(index)
                marker.write_text('Keep the in-flight result.', encoding='utf-8')
                return index
            args = SimpleNamespace(workers=1, stop_file=marker)
            self.assertEqual(list(bounded_results(task, range(20), args)), [0])
            self.assertEqual(started, [0])

    def test_bounded_dispatch_observes_failure_while_consumer_is_paused(self):
        started, waiting = [], []
        both_started = threading.Barrier(2)
        release_failure = threading.Event()
        def task(index):
            started.append(index)
            both_started.wait(timeout=3)
            if index == 0:
                return index
            self.assertTrue(release_failure.wait(timeout=3))
            raise RuntimeError('Failure during consumer pause')
        def observe_wait(*args, **kwargs):
            result = futures_wait(*args, **kwargs)
            waiting.extend(result[1])
            return result
        with patch('produce_goal_course.wait', side_effect=observe_wait):
            iterator = bounded_results(task, range(20), SimpleNamespace(workers=2))
            self.assertEqual(next(iterator), 0)
            release_failure.set()
            self.assertEqual(len(waiting), 1)
            with self.assertRaisesRegex(RuntimeError, 'consumer pause'):
                waiting[0].result(timeout=3)
            with self.assertRaisesRegex(RuntimeError, 'consumer pause'):
                next(iterator)
        self.assertEqual(sorted(started), [0, 1])

    def feedback_fixture(self, directory):
        source, plan, candidate = self.fixture()
        root = Path(directory); unit = root / 'units/complete-unit'; topic_root = unit / 'topics/facts'
        payload = author_payload(source, plan, plan['topics'][0])
        def saved_phase(folder, phase, prompt, current, value):
            messages = [{'role': 'system', 'content': prompt},
                        {'role': 'user', 'content': json.dumps(current, ensure_ascii=False)}]
            cp.save(folder / (phase + '.request.json'), {'messages': messages})
            cp.save(folder / (phase + '.response.json'), {'choices': [{'finish_reason': 'stop',
                    'message': {'content': json.dumps(value, ensure_ascii=False)}}]})
            cp.save(folder / (phase + '.exec-proof.json'), {'fixture_messages': messages})
        saved_phase(unit / 'planning/1', 'generate', PLAN, {**source, 'previous_issues': []}, plan)
        cp.save(unit / 'planning/accepted.json', {'workflow': WORKFLOW, 'input': source, 'candidate': plan,
                                               'attempt': 1, 'semantic_review_performed': False})
        cp.save(unit / 'plan.json', plan)
        saved_phase(topic_root / '2', 'generate', 'historical author policy',
                    {**payload, 'previous_issues': ['Preserve valid alternatives.']}, candidate)
        response_path = topic_root / '2/generate.response.json'
        report = {'role': 'independent_candidate_teaching_review', 'reviewer': 'subagent',
            'reviewer_id': '/fixture/reviewer', 'accepted': False, 'candidate_modified': False,
            'replaces_official_exec_review': False, 'review_inputs': {
                'candidate': str(response_path), 'candidate_sha256': hashlib.sha256(response_path.read_bytes()).hexdigest(),
                'plan': str(unit / 'plan.json'), 'plan_sha256': hashlib.sha256((unit / 'plan.json').read_bytes()).hexdigest()},
            'findings': [{'id': 'unresolved-reference', 'impact': 'An answer label has no visible options.',
                          'general_rule': 'All learner-facing references must be resolvable.'}]}
        report_path = root / 'external-review.json'; cp.save(report_path, report)
        args = SimpleNamespace(source=root / 'unused-manifest.json', output=root, unit=['complete-unit'], topic=['facts'],
            workers=1, attempts=1, timeout=30, plan_only=False, author_feedback=report_path, stop_file=None)
        return source, plan, candidate, payload, topic_root, args, saved_phase

    def test_external_findings_regenerate_from_original_source_without_replanning_or_fake_approval(self):
        with temporary_test_directory() as directory:
            source, plan, candidate, payload, topic_root, args, saved_phase = self.feedback_fixture(directory)
            old_bytes = (topic_root / '2/generate.response.json').read_bytes()
            plan_bytes = (topic_root.parent.parent / 'planning/accepted.json').read_bytes()
            generation_calls, review_calls = [], []
            def execute(folder, phase, prompt, current, timeout):
                self.assertEqual(folder, topic_root / '3')
                if phase == 'generate':
                    self.assertEqual(prompt, AUTHOR)
                    self.assertEqual({k: v for k, v in current.items() if k != 'previous_issues'}, payload)
                    self.assertEqual(len(current['previous_issues']), 2)
                    if not (folder / 'generate.response.json').exists():
                        generation_calls.append(folder)
                        saved_phase(folder, phase, prompt, current, candidate)
                    return candidate
                review_calls.append(folder)
                self.assertEqual(prompt, REVIEW)
                if len(review_calls) == 1:
                    raise RuntimeError('Official review still pending')
                approved = {'approved': True, 'issues': []}
                saved_phase(folder, phase, prompt, current, approved)
                return approved
            with patch('produce_goal_course.load_materials', return_value=source), \
                 patch('produce_goal_course.call', side_effect=execute), \
                 patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                with self.assertRaisesRegex(RuntimeError, 'still pending'):
                    produce(args)
                self.assertFalse((topic_root / 'accepted.json').exists())
                self.assertFalse((topic_root / '2/rejection.json').exists())
                self.assertFalse((topic_root / '3/review.response.json').exists())
                produce(args)
            self.assertEqual(len(generation_calls), 1); self.assertEqual(len(review_calls), 2)
            self.assertEqual((topic_root / '2/generate.response.json').read_bytes(), old_bytes)
            self.assertEqual((topic_root.parent.parent / 'planning/accepted.json').read_bytes(), plan_bytes)
            record = cp.read(topic_root / 'accepted.json')
            self.assertEqual(record['attempt'], 3); self.assertTrue(record['review']['approved'])
            self.assertFalse(cp.read(topic_root / '3/external-feedback.json')['replaces_official_exec_review'])

    def test_coordinate_only_never_dispatches_a_single_unit_author(self):
        with temporary_test_directory() as directory:
            source, _, _, _, _, args, _ = self.feedback_fixture(directory)
            args.author_feedback = None
            args.coordinate_only = True
            with patch('produce_goal_course.load_materials', return_value=source), \
                 patch('produce_goal_course.call', side_effect=AssertionError('Coordination-only dispatched an author')), \
                 patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                produce(args)

    def test_external_feedback_rejects_tampered_evidence_and_unknown_prior_review(self):
        with temporary_test_directory() as directory:
            source, plan, candidate, payload, root, args, _ = self.feedback_fixture(directory)
            with patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                self.assertEqual(author_feedback(args.author_feedback, root, payload, plan)['origin_attempt'], 2)
                changed = cp.read(args.author_feedback); changed['review_inputs']['candidate_sha256'] = '0' * 64
                wrong = Path(directory) / 'wrong-review.json'; cp.save(wrong, changed)
                with self.assertRaisesRegex(ValueError, 'hash changed'):
                    author_feedback(wrong, root, payload, plan)
                changed = cp.read(args.author_feedback); changed['accepted'] = True
                approved = Path(directory) / 'false-approval.json'; cp.save(approved, changed)
                with self.assertRaisesRegex(ValueError, 'unapproved'):
                    author_feedback(approved, root, payload, plan)
                cp.save(root / '2/review.started.json', {'started_at': '2026-01-01T00:00:00Z'})
                with self.assertRaisesRegex(ValueError, 'outcome unknown'):
                    author_feedback(args.author_feedback, root, payload, plan)

    def test_native_projection_keeps_original_intro_and_real_ownership(self):
        _, plan, candidate = self.fixture('A < B & C.', 'A second fact.')
        before = deepcopy(candidate); native = project_atomic(candidate, 'native-facts')
        self.assertEqual(candidate, before)
        for original, section in zip(candidate['sections'], native['sections']):
            self.assertNotIn('status', section)
            self.assertNotIn('import_eligible', section)
            self.assertEqual(section['introduction']['html'], original['introduction']['html'])
            self.assertEqual(section['flashcards'], original['flashcards'])
            self.assertEqual(section['memory_points'], original['memory_points'])
            anchor = section['memory_chunks'][0]['anchors'][0]
            self.assertEqual(section['introduction']['plain_text'][anchor['start']:anchor['end']], anchor['quote'])
        bad = deepcopy(candidate); bad['sections'][0]['memory_chunks'][0]['anchors'][0]['quote'] = 'Synthetic appended fact.'
        with self.assertRaisesRegex(ValueError, 'absent'): validate_author(bad, plan['topics'][0])
        self.assertEqual(bad['sections'][0]['introduction']['html'], candidate['sections'][0]['introduction']['html'])
        bad = deepcopy(candidate); bad['sections'][0]['flashcards'][0]['point_ids'] = ['point-b']
        with self.assertRaises(ValueError): validate_author(bad, plan['topics'][0])
        with self.assertRaisesRegex(ValueError, 'Ambiguous'): resolve_anchors('Same. Same.', [{'quote': 'Same.'}])
        self.assertEqual(resolve_anchors('Same. Same.', [{'quote': 'Same.', 'occurrence': 2}])[0]['start'], 6)
        readable = readable_topic(native, 'atomic')
        self.assertIn('## Introduction', readable); self.assertIn('### 记忆组块', readable)
        self.assertIn('#### 记忆点', readable); self.assertIn('##### Flashcard', readable)

    def test_structural_types_cannot_masquerade_as_introduction_sections(self):
        source, plan, candidate = self.fixture()
        for label in ('记忆组块', '原子知识点', '统一闪卡', 'Example', 'Explanation', 'Practice'):
            bad = deepcopy(plan); bad['topics'][0]['sections'][0]['title'] = label
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, 'structure type'):
                validate_plan(bad, source)
            altered = deepcopy(candidate); altered['sections'][0]['section_title'] = label
            with self.assertRaisesRegex(ValueError, 'structure type'):
                validate_author(altered, plan['topics'][0])
        duplicate = deepcopy(plan['topics'][0]); duplicate.update(id='application', kind='paradigm')
        bad = deepcopy(plan); bad['topics'].append(duplicate)
        with self.assertRaisesRegex(ValueError, 'training mode or exercise format'): validate_plan(bad, source)

    def test_native_sources_compile_with_existing_importers_without_semantic_calls(self):
        source, _, candidate = self.fixture()
        with temporary_test_directory() as directory:
            root = Path(directory); export = root / 'export'; export.mkdir()
            atomic = project_atomic(candidate, 'native-facts', approved=True); paradigm = project_paradigm(self.paradigm(), 'native-method')
            atomic_path = export / 'facts.atomic.json'; paradigm_path = export / 'method.v2.json'
            cp.save(atomic_path, {'schema': 2, 'topics': [atomic]}); cp.save(paradigm_path, paradigm)
            cp.save(export / 'atomic.json', {'schema': 2, 'topics': [atomic]})
            rows = [{'id': 'native-facts', 'title': candidate['title'], 'kind': 'atomic', 'prerequisites': [], 'source': str(atomic_path)},
                    {'id': 'native-method', 'title': paradigm['title'], 'kind': 'paradigm', 'prerequisites': ['native-facts'], 'source': str(paradigm_path)}]
            cp.save(export / 'course.json', {**source['course'], 'topics': rows,
                'units': [{'id': 'complete-unit', 'title': 'Whole unit', 'topic_ids': [r['id'] for r in rows]}]})
            cp.save(export / 'point-catalog.json', {'schema_version': 1, 'course_id': source['course']['id'],
                'mapping_status': 'unmapped', 'points': point_catalog(candidate, 'native-facts')})
            with patch('produce_goal_course.call', side_effect=AssertionError('Mechanical import called semantic production')):
                report = compile_sources(export, root / 'compiled')
            self.assertEqual(report['status'], 'mechanically_validated'); self.assertFalse(report['semantic_review_performed'])
            compiled = cp.read(root / 'compiled/course-package/topics/native-facts.json')
            validate_atomic(compiled); validate_runtime(compiled)
            self.assertEqual([c['introduction_id'] for c in compiled['memory_chunks']], ['intro-a', 'intro-b'])
            self.assertEqual([c['points'][0]['text'] for c in compiled['memory_chunks']], ['Red is a colour.', 'Blue is a colour.'])
            v2 = cp.read(root / 'compiled/course-package/author-packages/native-method/topic.json')
            validate_runtime(v2)
            self.assertEqual(v2['content_order'][0]['id'], 'method-intro')
            self.assertEqual(len(v2['modules'][0]['practices']), 7)
            # A separate synthetic API fixture checks the reusable paradigm
            # traversal helper. It does not claim real language judgement.
            from validate_source_course import Exercise
            from tests.subject_matrix_fixture import write_matrix
            fixture_root = root / 'paradigm-api'; data = fixture_root / 'data'
            write_matrix(data)
            cp.save(data / 'test-materials.json', {'schema': 1, 'courses': ['fixture-paradigm']})
            cp.save(data / 'native-facts.json', compiled)
            cp.save(data / 'native-method.json', v2)
            fixture_catalog = cp.read(data / 'catalog.json')
            fixture_catalog['courses'].append({'id': 'fixture-paradigm', 'title': 'Independent addition fixture',
                'topics': [{'id': compiled['id'], 'path': 'native-facts.json', 'prerequisites': []},
                           {'id': v2['id'], 'path': 'native-method.json', 'prerequisites': [compiled['id']]}]})
            (data / 'catalog.json').write_text(json.dumps(fixture_catalog), encoding='utf-8')
            subjects = cp.read(data / 'subjects.json')
            next(s for s in subjects['subjects'] if s['id'] == 'english')['course_ids'].append('fixture-paradigm')
            (data / 'subjects.json').write_text(json.dumps(subjects), encoding='utf-8')
            exercise = Exercise(fixture_root, v2, 'sequence_fixture')
            with self.assertRaisesRegex(AssertionError, 'missing a real prerequisite'):
                exercise.prerequisite_order()
            exercise.available_topics[compiled['id']] = compiled
            with exercise.client:
                exercise.login()
                locked = exercise.client.get('/api/state' + exercise.scope, headers=exercise.headers)
                self.assertEqual(locked.status_code, 409)
                self.assertEqual(locked.json()['detail']['code'], 'prerequisites_not_ready')
                exercise.prepare_prerequisites()
                sequence = exercise.sequence(login=False)
            self.assertEqual([row['topic_id'] for row in exercise.prerequisite_runs], [compiled['id']])
            self.assertTrue(exercise.prerequisite_runs[0]['progress_written_through_normal_api'])
            self.assertEqual(exercise.app.state.catalog.metadata(v2['id'])['prerequisites'], [compiled['id']])
            self.assertFalse(sequence['real_judge'])
            self.assertTrue(sequence['all_assigned_guidance_visited'])
            self.assertEqual(sequence['introduction_order'], ['method-intro'])
            from graspmemoedu.practice import LEGACY_LESSON_POLICY
            self.assertTrue(sequence['practice_counts_match_saved_policy'])
            self.assertEqual(len(sequence['practice_ids_answered']), LEGACY_LESSON_POLICY.targets[0])

    def test_accepted_stage_resumes_without_generation_and_rejects_tampering(self):
        payload = {'teaching': 'Independent frozen fixture'}; candidate = {'id': 'fixture'}
        review = {'approved': True, 'issues': []}
        with temporary_test_directory() as directory:
            root = Path(directory); attempt = root / '1'; attempt.mkdir()
            for phase, value in [('generate', candidate), ('review', review)]:
                request = {**payload, 'previous_issues': []} if phase == 'generate' else {'source': payload, 'candidate': candidate}
                cp.save(attempt / (phase + '.response.json'), {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]})
                cp.save(attempt / (phase + '.exec-proof.json'), {'fixture_messages': [{'role': 'system', 'content': 'prompt' if phase == 'generate' else REVIEW},
                    {'role': 'user', 'content': json.dumps(request)}]})
            cp.save(root / 'accepted.json', {'workflow': WORKFLOW, 'input': payload, 'candidate': candidate, 'review': review, 'attempt': 1})
            args = SimpleNamespace(attempts=1, timeout=30)
            with patch('produce_goal_course.call', side_effect=AssertionError('Accepted unit regenerated')), \
                 patch.object(cp, 'validate_exec_proof', side_effect=lambda proof, _: proof['fixture_messages']):
                self.assertEqual(accepted_stage(root, 'prompt', payload, lambda _: None, args), candidate)
                with self.assertRaisesRegex(ValueError, 'older production prompt'):
                    accepted_stage(root, 'revised policy prompt', payload, lambda _: None, args)
                bad = cp.read(root / 'accepted.json'); bad['candidate']['id'] = 'changed'
                (root / 'accepted.json').write_text(json.dumps(bad), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'immutable'): accepted_stage(root, 'prompt', payload, lambda _: None, args)

    def test_planning_has_no_semantic_review_gate_and_stop_prevents_new_dispatch(self):
        source, plan, _ = self.fixture()
        with temporary_test_directory() as directory:
            root = Path(directory); marker = root / 'STOP-DISPATCH'
            args = SimpleNamespace(attempts=1, timeout=30, stop_file=marker)
            with patch('produce_goal_course.call', return_value=plan) as caller:
                self.assertEqual(accepted_stage(root / 'plan', 'fixture', source,
                    lambda p: validate_plan(p, source), args, review_required=False), plan)
                self.assertEqual(caller.call_count, 1)
                record = cp.read(root / 'plan/accepted.json')
                self.assertFalse(record['semantic_review_performed']); self.assertNotIn('review', record)
            marker.write_text('Stop after in-flight calls finish.', encoding='utf-8')
            with self.assertRaises(DispatchStopped): check_dispatch(args)
            with patch('produce_goal_course.call', side_effect=AssertionError('New call dispatched after stop')):
                with self.assertRaises(DispatchStopped):
                    accepted_stage(root / 'new-stage', 'fixture', source, lambda _: None, args)

    def test_post_course_mapping_keeps_unrelated_points_and_rejects_unknown_refs(self):
        _, _, candidate = self.fixture()
        catalog = {'points': point_catalog(candidate, 'native-facts')}
        bank = {'id': 'independent-bank', 'groups': [{'id': 'group', 'unlock_policy': {'point_ids': ['native-facts/point-a']},
            'questions': [{'id': 'question', 'point_ids': ['native-facts/point-a'],
                           'assessment': {'item_points': {'item': ['native-facts/point-a']}}}]}]}
        result = final_point_map(catalog, bank)
        self.assertEqual(len(result['points']), 2)
        self.assertEqual(result['points'][1]['unlock_targets'], [])
        self.assertEqual(result['points'][1]['assessments'], [])
        bank['groups'][0]['unlock_policy']['point_ids'] = ['unknown/point']
        with self.assertRaises(ValueError): validate_bank_references(bank, catalog)

    def test_point_catalog_must_match_actual_compiled_ids(self):
        with temporary_test_directory() as directory:
            root = Path(directory)
            cp.save(root / 'native.json', {'id': 'native', 'memory_chunks': [
                {'id': 'chunk', 'points': [{'id': 'compiled-point', 'text': 'A complete fact.'}]}]})
            declarations = {'topics': [{'id': 'native', 'path': 'native.json'}]}
            catalog = {'points': [{'point_ref': 'native/compiled-point'}]}
            self.assertEqual(validate_compiled_points(root, declarations, catalog), 1)
            catalog['points'][0]['point_ref'] = 'native/source-only-point'
            with self.assertRaisesRegex(ValueError, 'actual compiled'):
                validate_compiled_points(root, declarations, catalog)

    def test_retrieval_preserves_whole_entries_and_contrast_notes(self):
        text = '# Material\n\n- light\n  - A light bag.\n    - Not heavy.\n- flight\n  - A long flight.\n## Batch\n- ➯trip, journey, travel\n- A three-day trip.\n'
        rows = list(lexical_entries(text))
        self.assertEqual([row[0] for row in rows], ['Material', 'light', 'flight', 'Batch', 'trip, journey, travel'])
        self.assertIn('Not heavy.', rows[1][3]); self.assertNotIn('flight', rows[1][3])
        self.assertIn('A three-day trip.', rows[-1][3]); self.assertIn('close', vocabulary('The doors stayed closed.'))
        self.assertNotIn('light', vocabulary('flights'))


if __name__ == '__main__':
    unittest.main()
