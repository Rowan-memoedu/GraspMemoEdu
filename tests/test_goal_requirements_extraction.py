"""Boundary regressions for source-blind goal requirement extraction."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.extract_goal_requirements import audit_material_manifest, indexed_source_view, materialize_evidence_references, project_required_course_refs, quote_source_span, source_span_catalog, validate_argument_references, validate_candidate, validate_public, validate_review


class RequirementBoundaries(unittest.TestCase):
    def setUp(self):
        self.packet = {
            'original': {'source_id': 'passage', 'blanks': [{'id': 'slot-a', 'answers': ['who', 'that']}]},
            'sources': [{'id': 'passage', 'text': 'A person ___ teaches.'}],
            'sentences': [{'id': 's001', 'text': 'A person ___ teaches.'}],
        }
        self.candidate = {
            'requirements': [
                {'id': 'relative-role', 'claim': '指人的限定性关系从句可由关系代词充当主语。', 'kind': 'syntax',
                 'role': 'assessed_candidate', 'unlock_required': True,
                 'necessity': 'target_required', 'necessity_reason': '决定关系从句主语的表达。',
                 'evidence': [{'source_id': 'passage', 'quote': 'person ___ teaches'}]},
                {'id': 'understand-verb', 'claim': '认识教学动词的相关含义。', 'kind': 'lexical',
                 'role': 'comprehension_support', 'unlock_required': True,
                 'necessity': 'target_required', 'necessity_reason': '理解主句和从句的行为关系。',
                 'evidence': [{'source_id': 'passage', 'quote': 'teaches'}]},
            ],
            'blank_analysis': [{'id': 'slot-a', 'required_ids': ['relative-role', 'understand-verb'],
                                'assessed_ids': ['relative-role'], 'uncredited_ids': ['understand-verb'],
                                'answer_evidence': [{'answer': 'who', 'supported_ids': ['relative-role'], 'reason': '关系代词作主语'},
                                                    {'answer': 'that', 'supported_ids': ['relative-role'], 'reason': '关系代词作主语'}],
                                'reason': '只记录可观察的句法功能', 'additional_answers': []}],
            'sentence_coverage': [{'sentence_id': 's001', 'requirement_ids': ['relative-role', 'understand-verb'],
                                   'understanding': '人物与行为的关系', 'unscored_reason': '词义没有被单独测查'}],
            'source_cautions': [],
            'nonfinite_role_audit': [],
            'planner_payload': {'role': 'domain_objectives', 'learning_objectives': ['关系从句中的关系代词可以充当主语。']},
            'unresolved_questions': [],
        }

    def test_valid_boundary(self):
        self.assertTrue(validate_candidate(self.candidate, self.packet)['passed'])

    def test_independent_news_instances_share_transferable_point_without_fact_cards(self):
        requirements = [
            {'id': 'news-one', 'claim': 'The photographer opened a museum; she refers to the photographer.',
             'necessity': 'target_required'},
            {'id': 'news-two', 'claim': 'The swimmer won a race; he refers to the swimmer.',
             'necessity': 'target_required'},
            {'id': 'supplied-date', 'claim': 'The race happened on Friday.',
             'necessity': 'provided_in_question'},
        ]
        catalog = {'points': [{'point_ref': 'reference/p1',
                               'content': 'Use agreement, syntax and context to resolve a pronoun antecedent.'}]}
        links = [{'requirement_id': row['id'], 'point_refs': ['reference/p1']} for row in requirements]
        before = deepcopy((requirements, catalog, links))
        result = project_required_course_refs(requirements, catalog, links)
        self.assertEqual(result['required_candidate_count'], 2)
        self.assertEqual(result['unlock_point_refs'], ['reference/p1'])
        self.assertFalse(result['assessment_links_inferred'])
        self.assertTrue(result['semantic_review_required'])
        self.assertNotIn('photographer', json.dumps(result))
        self.assertEqual((requirements, catalog, links), before)

    def test_mapping_cannot_inject_target_facts_or_drop_required_competence(self):
        requirements = [{'id': 'application', 'necessity': 'target_required'}]
        catalog = {'points': [{'point_ref': 'syntax/p1', 'content': 'Find the subject of a finite clause.'}]}
        invalid_links = [
            [{'requirement_id': 'application', 'point_refs': ['private/article-event']}],
            [{'requirement_id': 'application', 'point_refs': ['syntax/p1'], 'content': 'A named person won a race.'}],
            [{'requirement_id': 'application', 'point_refs': []}],
            [],
        ]
        for links in invalid_links:
            with self.subTest(links=links), self.assertRaises(ValueError):
                project_required_course_refs(requirements, catalog, links)

    def test_full_arguments_keep_quantifiers_and_nested_noun_phrases(self):
        # Independent contexts: gathering correspondence and filing a report.
        for full, inner in [('most of the old letters', 'the old letters'),
                            ('the last three pages of the report', 'the report')]:
            with self.subTest(full=full):
                audits = [{'objects': [{'quote': full, 'role': 'direct_object',
                                        'nested_components': [inner]}],
                           'requirement_ids': ['argument-scope']}]
                requirements = [{'id': 'argument-scope', 'argument_references': [
                    {'audit_index': 0, 'object_index': 0, 'quote': full}]}]
                self.assertEqual(validate_argument_references(requirements, audits), 1)
                requirements[0]['argument_references'][0]['quote'] = inner
                with self.assertRaisesRegex(ValueError, 'complete argument'):
                    validate_argument_references(requirements, audits)

    def test_nested_argument_component_cannot_replace_whole_or_escape_span(self):
        for nested in ['several of the sealed boxes', 'the open box']:
            audits = [{'objects': [{'quote': 'several of the sealed boxes',
                                    'role': 'direct_object', 'nested_components': [nested]}],
                       'requirement_ids': []}]
            with self.subTest(nested=nested), self.assertRaisesRegex(ValueError, 'proper subspan'):
                validate_argument_references([], audits)

    def test_multiple_actual_points_per_response_have_no_fixed_cap(self):
        requirement = deepcopy(self.candidate['requirements'][0])
        requirement.update(id='subject-reference', claim='关系从句的主语关系代词指代先行词。')
        self.candidate['requirements'].append(requirement)
        row = self.candidate['blank_analysis'][0]
        row['required_ids'].append('subject-reference')
        row['assessed_ids'].append('subject-reference')
        for answer in row['answer_evidence']:
            answer['supported_ids'].append('subject-reference')
        self.candidate['sentence_coverage'][0]['requirement_ids'].append('subject-reference')
        result = validate_candidate(self.candidate, self.packet)
        self.assertTrue(result['passed'])
        self.assertEqual(result['assessed_requirement_count'], 2)

    def test_foundation_label_cannot_exempt_required_understanding(self):
        self.candidate['requirements'][1]['role'] = 'foundation'
        self.candidate['requirements'][1]['unlock_required'] = False
        with self.assertRaisesRegex(ValueError, 'regardless of role'):
            validate_candidate(self.candidate, self.packet)

    def test_association_projection_keeps_all_necessary_support_and_excludes_supplied_information(self):
        self.packet['sources'][0]['text'] += ' person (人)'
        supplied = deepcopy(self.candidate['requirements'][1])
        supplied.update(id='supplied-person', claim='person 表示人。', necessity='provided_in_question',
                        necessity_reason='释义由题面明确给出。', unlock_required=False,
                        evidence=[{'source_id': 'passage', 'quote': 'person (人)'}])
        self.candidate['requirements'].append(supplied)
        row = self.candidate['blank_analysis'][0]
        row['related_requirement_ids'] = row.pop('required_ids') + ['supplied-person']
        row.pop('uncredited_ids')
        before = deepcopy(self.candidate)
        result = validate_candidate(self.candidate, self.packet)
        partition = result['blank_knowledge_partitions'][0]
        self.assertEqual(partition['prior_required_ids'], ['relative-role', 'understand-verb'])
        self.assertEqual(partition['provided_information_ids'], ['supplied-person'])
        self.assertEqual(partition['uncredited_prior_ids'], ['understand-verb'])
        self.assertEqual(self.candidate, before)
        row['assessed_ids'].append('supplied-person')
        with self.assertRaisesRegex(ValueError, 'Only prior-required'):
            validate_candidate(self.candidate, self.packet)

    def test_association_schema_cannot_reintroduce_freehand_derived_sets(self):
        row = self.candidate['blank_analysis'][0]
        row['related_requirement_ids'] = list(row['required_ids'])
        with self.assertRaisesRegex(ValueError, 'cannot duplicate derived'):
            validate_candidate(self.candidate, self.packet)

    def test_legacy_required_set_cannot_include_supplied_information(self):
        row = self.candidate['requirements'][1]
        row.update(necessity='provided_in_question', unlock_required=False)
        with self.assertRaisesRegex(ValueError, 'cannot be excluded from unlock'):
            validate_candidate(self.candidate, self.packet)

    def test_private_fields_never_pass_allowlist(self):
        public = deepcopy(self.candidate['planner_payload'])
        public['blank_id'] = 'slot-a'
        with self.assertRaisesRegex(ValueError, 'unapproved fields'):
            validate_public(public, self.packet)

    def test_answer_and_identifier_leak(self):
        for text in ['关系词 who 可以作主语。', '第六空考查关系词。', '答案取决于主语。']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_public({'role': 'domain_objectives', 'learning_objectives': [text]}, self.packet)

    def test_support_cannot_receive_implicit_score(self):
        self.candidate['blank_analysis'][0]['assessed_ids'].append('understand-verb')
        self.candidate['blank_analysis'][0]['uncredited_ids'] = []
        with self.assertRaisesRegex(ValueError, 'support/foundation'):
            validate_candidate(self.candidate, self.packet)

    def test_all_original_alternatives_must_support_score(self):
        self.candidate['blank_analysis'][0]['answer_evidence'][1]['supported_ids'] = []
        with self.assertRaisesRegex(ValueError, 'bypassed'):
            validate_candidate(self.candidate, self.packet)

    def test_original_key_cannot_be_replaced(self):
        self.candidate['blank_analysis'][0]['answer_evidence'][0]['answer'] = 'whom'
        with self.assertRaisesRegex(ValueError, 'answers were changed'):
            validate_candidate(self.candidate, self.packet)

    def test_cannot_hide_missing_sentence(self):
        self.candidate['sentence_coverage'] = []
        with self.assertRaisesRegex(ValueError, 'Sentence coverage'):
            validate_candidate(self.candidate, self.packet)

    def test_citation_must_be_exact(self):
        self.candidate['requirements'][0]['evidence'][0]['quote'] = 'person teaches'
        with self.assertRaisesRegex(ValueError, 'Nonverbatim'):
            validate_candidate(self.candidate, self.packet)

    def test_enumerated_evidence_binds_exact_unicode_source_offsets(self):
        packet = {'sources': [{'id': 'entry', 'text': 'Entry: tray\r\n\r\n  Translation: 托盘  \n'}]}
        before = deepcopy(packet)
        catalog = source_span_catalog(packet)
        self.assertEqual(len(catalog['spans']), 2)
        for span in catalog['spans']:
            source = packet['sources'][0]['text']
            self.assertEqual(source[span['raw_start']:span['raw_end']], span['quote'])
            self.assertEqual(span['source_text_sha256'], hashlib.sha256(source.encode('utf-8')).hexdigest())
        view = indexed_source_view(packet, catalog)
        self.assertNotIn('text', view['sources'][0])
        self.assertEqual(view['sources'][0]['content_spans'][1]['text'], '  Translation: 托盘  ')
        self.assertEqual(packet, before)

    def test_evidence_materialization_preserves_model_candidate_and_syntax_spans(self):
        self.packet['sources'][0]['text'] = 'A person ___ teaches to help learners.'
        self.packet['sources'].append({'id': 'entry-teach', 'text': 'Entry: teach\nDefinition: to give instruction to someone.'})
        catalog = source_span_catalog(self.packet)
        lookup = {span['quote']: span['source_span_id'] for span in catalog['spans']}
        raw = deepcopy(self.candidate)
        raw['requirements'][0]['evidence'] = [{'source_span_id': lookup['A person ___ teaches to help learners.']}]
        raw['requirements'][1]['evidence'] = [{'source_span_id': lookup['Definition: to give instruction to someone.']}]
        raw['nonfinite_role_audit'] = [{
            'sentence_id': 's001', 'source_id': 'passage', 'construction_quote': 'to help learners',
            'attachment_target': {'quote': 'teaches', 'kind': 'clause_or_event'},
            'understood_subject': {'status': 'identified', 'candidates': [{'quote': 'A person', 'kind': 'participant'}],
                                   'reason': 'The teaching participant performs the helping.'},
            'objects': [{'quote': 'learners', 'role': 'direct_object'}],
            'semantic_relation': 'purpose', 'requirement_ids': ['relative-role']}]
        raw['requirements'][0]['argument_references'] = [{'audit_index': 0, 'object_index': 0, 'quote': 'learners'}]
        raw['source_cautions'] = [{'source_span_id': lookup['Entry: teach'],
                                   'correction': 'A heading identifies a word, not all of its meanings.',
                                   'supporting_evidence': [{'source_span_id': lookup['Definition: to give instruction to someone.']}]}]
        before = deepcopy(raw)
        result, proof = materialize_evidence_references(raw, self.packet, catalog)
        self.assertEqual(raw, before)
        self.assertEqual(result['nonfinite_role_audit'], raw['nonfinite_role_audit'])
        self.assertEqual(result['requirements'][0]['argument_references'], raw['requirements'][0]['argument_references'])
        self.assertEqual(result['requirements'][1]['evidence'],
                         [{'source_id': 'entry-teach', 'quote': 'Definition: to give instruction to someone.'}])
        self.assertEqual(proof['reference_count'], 4)
        self.assertFalse(proof['fine_grained_syntax_spans_modified'])
        self.assertTrue(validate_candidate(result, self.packet)['passed'])

    def test_correction_and_alternative_support_share_the_same_span_interface(self):
        catalog = source_span_catalog(self.packet)
        known = catalog['spans'][0]['source_span_id']
        raw = deepcopy(self.candidate)
        for row in raw['requirements']:
            row['evidence'] = [{'source_span_id': known}]
        alternative = {'candidate': 'whom', 'decision': 'not_adopted', 'reason': 'Object case does not fill this subject role.',
                       'supporting_evidence': [{'source_span_id': known}]}
        raw['blank_analysis'][0]['additional_answers'] = [alternative]
        materialized, _ = materialize_evidence_references(raw, self.packet, catalog)
        support = materialized['blank_analysis'][0]['additional_answers'][0]
        self.assertEqual(support['evidence_ids'], ['passage'])
        self.assertEqual(support['supporting_evidence'], [{'source_id': 'passage', 'quote': 'A person ___ teaches.'}])
        self.assertTrue(validate_candidate(materialized, self.packet)['passed'])
        alternative['evidence_ids'] = [known]
        with self.assertRaisesRegex(ValueError, 'typed span references'):
            materialize_evidence_references(raw, self.packet, catalog)

    def test_evidence_interface_rejects_labels_freehand_text_and_wrong_source_binding(self):
        catalog = source_span_catalog(self.packet)
        raw = deepcopy(self.candidate)
        known = catalog['spans'][0]['source_span_id']
        for row in raw['requirements']:
            row['evidence'] = [{'source_span_id': known}]
        invalid = [{'source_span_id': 'Definition'}, {'source_id': 'passage'},
                   {'source_span_id': known, 'quote': 'Definition'},
                   {'source_span_id': known, 'source_id': 'another-source'}]
        for reference in invalid:
            broken = deepcopy(raw)
            broken['requirements'][0]['evidence'] = [reference]
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                materialize_evidence_references(broken, self.packet, catalog)
        for field, value in [('quote', 'invented content'), ('raw_start', 1), ('source_id', 'another-source')]:
            broken_catalog = deepcopy(catalog)
            broken_catalog['spans'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'frozen source'):
                materialize_evidence_references(raw, self.packet, broken_catalog)
        other_packet = deepcopy(self.packet)
        other_packet['sources'][0]['text'] += ' New source content.'
        with self.assertRaisesRegex(ValueError, 'frozen source'):
            materialize_evidence_references(raw, other_packet, catalog)

    def test_paired_bold_alignment_preserves_candidate_and_raw_source(self):
        source = 'A **person** ___ teaches.'
        self.packet['sources'][0]['text'] = source
        original = deepcopy(self.candidate)
        result = validate_candidate(self.candidate, self.packet)
        self.assertEqual(self.candidate, original)
        self.assertEqual(len(result['evidence_alignments']), 1)
        alignment = result['evidence_alignments'][0]
        self.assertEqual(alignment['raw_quote'], '**person** ___ teaches')
        self.assertEqual(source[alignment['raw_start']:alignment['raw_end']], alignment['raw_quote'])

    def test_format_alignment_rejects_text_changes_and_ambiguous_spans(self):
        for quote, source in [('person teaches', '**person** ___ teaches'),
                              ('Person ___ teaches', '**person** ___ teaches'),
                              ('person ___ teaches.', '**person** ___ teaches!'),
                              ('person ___ teaches', '**person** ___ teaches; **person** ___ teaches'),
                              ('person ___ teaches', 'person** ___ teaches')]:
            with self.subTest(quote=quote, source=source):
                self.assertIsNone(quote_source_span(quote, source))

    def test_added_or_repositioned_paired_bold_binds_one_unchanged_source_span(self):
        for quote, source, raw in [
                ('**coach** trains runners', 'The coach trains runners daily.', 'coach trains runners'),
                ('coach trains **runners**', 'The **coach** trains runners daily.', '**coach** trains runners')]:
            with self.subTest(quote=quote, source=source):
                original = quote
                alignment = quote_source_span(quote, source)
                self.assertEqual(alignment['raw_quote'], raw)
                self.assertEqual(source[alignment['raw_start']:alignment['raw_end']], raw)
                self.assertEqual(alignment['candidate_quote'], original)

    def test_added_bold_never_permits_content_changes_or_ambiguous_alignment(self):
        for quote, source in [
                ('**coach** trains athletes', 'coach trains runners'),
                ('**coach**  trains runners', 'coach trains runners'),
                ('**Coach** trains runners', 'coach trains runners'),
                ('**coach** trains runners.', 'coach trains runners!'),
                ('**coach** trains runners', 'coach trains runners; coach trains runners'),
                ('**aaa**', 'aaaa'),
                ('coach** trains runners', 'coach trains runners'),
                ('_coach_ trains runners', 'coach trains runners')]:
            with self.subTest(quote=quote, source=source):
                self.assertIsNone(quote_source_span(quote, source))

    def test_event_attachment_and_participant_subject_are_separate(self):
        self.packet['sources'][0]['text'] = 'A person ___ teaches to help learners.'
        self.candidate['nonfinite_role_audit'] = [{
            'sentence_id': 's001', 'source_id': 'passage', 'construction_quote': 'to help learners',
            'attachment_target': {'quote': 'teaches', 'kind': 'clause_or_event'},
            'understood_subject': {'status': 'identified', 'candidates': [{'quote': 'A person', 'kind': 'participant'}],
                                   'reason': '主句人物承担帮助行为。'},
            'objects': [{'quote': 'learners', 'role': 'direct_object'}],
            'semantic_relation': '帮助行为表示教学行为的目的。', 'requirement_ids': ['relative-role'],
        }]
        self.assertTrue(validate_candidate(self.candidate, self.packet)['passed'])
        self.candidate['nonfinite_role_audit'][0]['understood_subject']['candidates'][0] = {'quote': 'teaches', 'kind': 'clause_or_event'}
        with self.assertRaisesRegex(ValueError, 'participant subject'):
            validate_candidate(self.candidate, self.packet)

    def test_review_cannot_omit_necessity_or_nonfinite_decisions(self):
        coverage = {key: True for key in ('all_blanks', 'all_sentences', 'lexical_and_collocation', 'syntax',
                                         'nonfinite_roles', 'necessity_is_complete', 'discourse',
                                         'assessment_is_conservative', 'planner_is_source_independent')}
        review = {'approved': True, 'issues': [], 'coverage': coverage}
        self.assertTrue(validate_review(review))
        del review['coverage']['necessity_is_complete']
        with self.assertRaisesRegex(ValueError, 'required coverage'):
            validate_review(review)

    def test_material_audit_allows_common_words_but_flags_source_sentence(self):
        sentence = 'The new machine gives every visitor a clear view of the sky.'
        self.packet['sources'][0]['text'] = sentence
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ordinary = root / 'ordinary.md'
            copied = root / 'copied.md'
            ordinary.write_text('A machine is useful. Every visitor has a ticket.', encoding='utf-8')
            copied.write_text('Grammar example:\n' + sentence, encoding='utf-8')
            units = [{'unit_id': path.stem, 'content_path': path.name,
                      'content_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                     for path in (ordinary, copied)]
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'units': units}), encoding='utf-8')
            report = audit_material_manifest(manifest, self.packet)
            self.assertEqual([row['unit_id'] for row in report['findings']], ['copied'])
            self.assertEqual(report['findings'][0]['matches'][0]['line'], 2)

    def test_material_audit_checks_full_translation_without_using_grammar_analysis(self):
        translation = '孩子把红色的盒子放在门口以后转身走进了那座安静的旧房子。'
        self.packet['sources'].append({'id': 'goal-analysis', 'text': '常见介词可以描述地点。\n## 文章翻译\n' + translation})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'reading.md'
            path.write_text('常见介词可以描述地点。\n' + translation, encoding='utf-8')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'units': [{'unit_id': 'reading', 'content_path': 'reading.md',
                                  'content_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}), encoding='utf-8')
            matches = audit_material_manifest(manifest, self.packet)['findings'][0]['matches']
            self.assertTrue(all(row['kind'] == 'translated_narrative_overlap' and row['line'] == 2 for row in matches))


if __name__ == '__main__':
    unittest.main()
