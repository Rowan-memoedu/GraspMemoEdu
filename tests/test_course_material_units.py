"""Tests use independent synthetic sources, never target exercise content."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.select_course_material_units import build, partition_markdown, inspect_remnote, inspect_complete_entries, sha256, structural_subunits, validate_package, apply_author_projections, apply_image_transcriptions, build_curriculum_groups


class MaterialUnitTests(unittest.TestCase):
    def test_complete_entry_preserves_all_meanings_and_rejects_partial_meaning(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / 'source.json'
            markdown = base / 'source.md'
            source.write_text(json.dumps({'category':'reference','title':'Reference','sources':[
                {'target':{'remId':'Container','title':'Container'}, 'note':{'remId':'Container','contentStructured':[
                    {'remId':'WordA','title':'Alpha','children':[
                        {'remId':'MeaningA1','title':'Meaning one','children':[{'remId':'ExampleA','title':'Example'}]},
                        {'remId':'MeaningA2','title':'Meaning two'}]},
                    {'remId':'WordB','title':'Beta','children':[{'remId':'MeaningB','title':'Other meaning'}]}
                ]}}
            ]}), encoding='utf-8')
            markdown.write_bytes(b'# Reference\r\n## Container\r\n\r\n- Alpha\r\n  - Meaning one\r\n    - Example\r\n  - Meaning two\r\n- Beta\r\n  - Other meaning\r\n')
            units, _ = inspect_remnote(source, markdown)
            selected = inspect_complete_entries(units[0], {'WordA'})[0]
            self.assertEqual(selected['source_refs'][0]['line_start'], 4)
            self.assertEqual(selected['source_refs'][0]['line_end'], 7)
            self.assertEqual(selected['source_refs'][1]['rem_ids'], ['WordA','MeaningA1','ExampleA','MeaningA2'])
            self.assertEqual(selected['text'], '- Alpha\r\n  - Meaning one\r\n    - Example\r\n  - Meaning two\r\n')
            with self.assertRaisesRegex(ValueError, 'not a direct source child'):
                inspect_complete_entries(units[0], {'MeaningA1'})
            config = {'course':{}, 'remnote_sources':[{'json_path':str(source),'markdown_path':str(markdown)}],
                      'complete_entries':[{'source_unit_id':units[0]['unit_id'],'rem_id':'WordA'}],
                      'reference_remnote_unit_ids':[units[0]['unit_id']]}
            output = base / 'materials'
            build(config, output)
            manifest = json.loads((output / 'materials-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['units'][0]['completeness']['kind'], 'complete_reference_entry')
            self.assertEqual(validate_package(manifest, output)['complete_remnote_subtrees'], 1)
            inventory = json.loads((output / 'source-inventory.json').read_text(encoding='utf-8'))
            reference = inventory['reference_containers'][0]
            self.assertEqual(reference['role'], 'reference_only')
            self.assertIn('Beta', (output / reference['content_path']).read_text(encoding='utf-8'))
            self.assertEqual(len(manifest['units']), 1)
            config['include_remnote_unit_ids'] = [units[0]['unit_id']]
            with self.assertRaisesRegex(ValueError, 'cannot both be selected'):
                build(config, output)
            del config['include_remnote_unit_ids']
            extra = base / 'extra-entries.json'
            extra.write_text(json.dumps({'entries':[{'source_unit_id':units[0]['unit_id'],'rem_id':'WordB'}]}), encoding='utf-8')
            config['complete_entries_paths'] = [str(extra)]
            build(config, output)
            expanded = json.loads((output / 'materials-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(len(expanded['units']), 2)
            self.assertEqual(validate_package(expanded, output)['complete_remnote_subtrees'], 2)

    def test_image_transcription_preserves_raw_source_and_binds_reviewed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            original = base / 'original'
            (original / 'assets').mkdir(parents=True)
            (original / 'assets/diagram.jpg').write_bytes(b'synthetic image fixture')
            text = b'# Unit\n![](assets/diagram.jpg)\nSource text.\n'
            (original / 'source.md').write_bytes(text)
            root = base / 'materials'
            (root / 'units').mkdir(parents=True)
            (root / 'units/u.md').write_bytes(text)
            manifest = {'units':[{'unit_id':'u','content_path':'units/u.md','content_sha256':sha256(text),
                                 'source_refs':[{'path':str(original / 'source.md'),'sha256':sha256(text),
                                                 'line_start':1,'line_end':3}]}]}
            audit = apply_image_transcriptions(manifest, root, [{'unit_id':'u','reference':'assets/diagram.jpg',
                'classification':'instructional_diagram','transcription':'A -> B'}])
            self.assertEqual((root / 'units/u.md').read_bytes(), text)
            self.assertIn(b'A -> B', (root / manifest['units'][0]['content_path']).read_bytes())
            self.assertEqual(validate_package(manifest, root, audit)['exact_source_spans'], 1)
            Path(audit[0]['transcript_path']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'transcript hash changed'):
                validate_package(manifest, root, audit)

    def test_shared_source_heads_merge_and_historical_batches_share_semantic_group(self):
        manifest = {'units':[{'unit_id':ident} for ident in ('c1','c2','p','w1','w2')]}
        index = [
            {'unit_id':'c1','category':'collocations','entries':[{'title':'take'}]},
            {'unit_id':'c2','category':'collocations','entries':[{'title':'take'},{'title':'give'}]},
            {'unit_id':'p','category':'phrasal_verbs','source_title':'take'},
            {'unit_id':'w1','category':'word_forms','title':'batch1'},
            {'unit_id':'w2','category':'word_forms','title':'batch2'}]
        groups = build_curriculum_groups(manifest, index, {'remaining_category_groups':[
            {'unit_id':'word-families','title':'Word families','categories':['word_forms']}]})
        self.assertEqual(len(groups), 2)
        self.assertEqual(set(groups[0]['source_unit_ids']), {'c1','c2','p'})
        self.assertEqual(groups[1]['source_unit_ids'], ['w1','w2'])

    def test_reference_summary_attaches_to_existing_semantic_group_once(self):
        manifest = {'units': [{'unit_id': ident} for ident in ('head', 'summary')]}
        index = [{'unit_id': 'head', 'category': 'word_senses', 'title': 'Head'}]
        recipe = {
            'remaining_category_groups': [
                {'unit_id': 'senses', 'title': 'Word senses', 'categories': ['word_senses']}],
            'supplementary_sources': [{'attach_to': 'senses', 'source_unit_ids': ['summary']}]}
        groups = build_curriculum_groups(manifest, index, recipe)
        self.assertEqual(groups, [{'unit_id': 'senses', 'title': 'Word senses',
                                  'source_unit_ids': ['head', 'summary']}])
        recipe['supplementary_sources'][0]['source_unit_ids'] = ['head']
        with self.assertRaisesRegex(ValueError, 'already assigned'):
            build_curriculum_groups(manifest, index, recipe)
        recipe['supplementary_sources'][0]['source_unit_ids'] = ['missing']
        with self.assertRaisesRegex(ValueError, 'selected sources'):
            build_curriculum_groups(manifest, index, recipe)

    def test_author_exclusion_preserves_original_and_uses_exact_numeric_spans(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'units').mkdir()
            original = b'# Unit\nkeep\nomit record\nkeep too\n'
            (root / 'units/a.md').write_bytes(original)
            source = root / 'source.md'
            source.write_bytes(b'front\n' + original)
            manifest = {'units':[{'unit_id':'a','content_path':'units/a.md','content_sha256':sha256(original),
                                  'source_refs':[{'path':str(source),'sha256':sha256(source.read_bytes()),
                                                  'line_start':2,'line_end':5}]}]}
            records = apply_author_projections(manifest, root, {'a':[[3,3]]})
            self.assertEqual((root / 'units/a.md').read_bytes(), original)
            self.assertEqual((root / 'units-teaching/a.md').read_bytes(), b'# Unit\nkeep\nkeep too\n')
            self.assertEqual(records[0]['excluded_line_count'], 1)
            self.assertEqual(validate_package(manifest, root)['exact_source_spans'], 1)

    def test_ocr_plain_structural_heading_is_not_lost(self):
        text = '# Unit\nPart 1 First\nbody\n## Part 2 Second\n'
        found = structural_subunits(text, r'Part \d+ .+', 'unit')
        self.assertEqual([item['title'] for item in found], ['Part 1 First', 'Part 2 Second'])

    def test_chapter_partitions_preserve_complete_contiguous_source(self):
        text = "front matter\n# Unit A\nintro\n## Section A\nbody\n# Unit B\nlast\n"
        parts = partition_markdown(text, r"Unit [A-Z]")
        self.assertEqual([(p['line_start'], p['line_end']) for p in parts], [(2, 5), (6, 7)])
        self.assertEqual(''.join(p['text'] for p in parts), ''.join(text.splitlines(keepends=True)[1:]))

    def test_remnote_source_preserves_all_children_and_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.json'
            markdown = root / 'source.md'
            source.write_text(json.dumps({'category':'verbs','title':'Verbs','sources':[
                {'target':{'remId':'RootA','title':'Alpha'}, 'note':{'remId':'RootA','contentStructured':[
                    {'remId':'EntryA','title':'First','children':[{'remId':'MeaningA','title':'Meaning'}]},
                    {'remId':'EntryB','title':'Second'}]}},
                {'target':{'remId':'RootB','title':'Beta'}, 'note':{'remId':'RootB','contentStructured':[]}}
            ]}), encoding='utf-8')
            markdown.write_bytes(b'# Verbs\r\n\r\n## Alpha\r\n- First\r\n  - Meaning\r\n- Second\r\n\r\n## Beta\r\n')
            units, inventory = inspect_remnote(source, markdown)
            self.assertEqual(inventory['direct_entries'], 2)
            self.assertEqual(units[0]['source_refs'][1]['rem_ids'], ['RootA','EntryA','MeaningA','EntryB'])
            self.assertIn('\r\n', units[0]['text'])
            self.assertEqual(len(units[0]['entries']), 2)

    def test_manifest_has_valid_hashes_and_no_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            book = root / 'book.md'
            book.write_bytes(b'# Unit A\n## Part 1\nExact source.\n# Unit B\nNot selected.\n')
            config = {'course':{'id':'fixture','title':'Fixture','subject_id':'english','description':''},
                      'books':[{'source_id':'book','path':str(book),'title':'Fixture book',
                                'boundary_pattern':r'Unit [A-Z]','subunit_pattern':r'Part \d+',
                                'selected_units':[{'source_title':'Unit A','unit_id':'unit-a'}]}]}
            output = root / 'materials'
            total = build(config, output)
            manifest = json.loads((output / 'materials-manifest.json').read_text(encoding='utf-8'))
            unit = manifest['units'][0]
            payload = (output / unit['content_path']).read_bytes()
            self.assertEqual(payload.decode(), '# Unit A\n## Part 1\nExact source.\n')
            self.assertEqual(unit['content_sha256'], sha256(payload))
            self.assertEqual(total['selected_units'], 1)
            self.assertEqual(unit['completeness']['subunits'], [{'id':'unit-a-section-001','title':'Part 1'}])
            self.assertEqual(validate_package(manifest, output)['exact_source_spans'], 1)
            (output / unit['content_path']).write_bytes(payload + b'changed')
            with self.assertRaisesRegex(ValueError, 'Packaged content hash changed'):
                validate_package(manifest, output)

    def test_unknown_selected_group_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'Unknown complete RemNote groups'):
                build({'course':{},'include_remnote_unit_ids':['missing']}, Path(temporary))

    def test_scope_hints_bind_sources_without_leaking_into_canary(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            book = base / 'book.md'
            book.write_bytes(b'# Unit A\n## Part 1\nAlpha\n# Unit B\n## Part 2\nBeta\n')
            config = {'course':{}, 'books':[{'source_id':'fixture','path':str(book),'title':'Fixture',
                'boundary_pattern':r'Unit [A-Z]','subunit_pattern':r'Part \d+',
                'selected_units':[{'source_title':'Unit A','unit_id':'a'},{'source_title':'Unit B','unit_id':'b'}],
                'canaries':[{'unit_id':'canary','source_title':'Part 1','title':'Part 1',
                            'boundary_pattern':r'Part \d+|Unit [A-Z]','boundary':'Complete part','teaching_scope':[]}]}],
                'scope_dependencies':[{'units':['b'],'prerequisites':['a']}]}
            output = base / 'materials'
            build(config, output)
            main = json.loads((output / 'materials-manifest.json').read_text(encoding='utf-8'))
            canary = json.loads((output / 'canary-materials-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(main['scope_dependencies'], config['scope_dependencies'])
            self.assertNotIn('scope_dependencies', canary)
            config['scope_dependencies'][0]['prerequisites'] = ['not-selected']
            with self.assertRaisesRegex(ValueError, 'unselected source'):
                build(config, output)

    def test_prepared_sources_preserve_provenance_without_reading_upstream_corpus(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            prepared = base / 'reviewed'
            prepared.mkdir()
            text = b'Complete article.\r\nOriginal analysis.\r\nTranslation.\r\n'
            (prepared / 'article.md').write_bytes(text)
            upstream = {'path':str(base / 'unavailable-upstream.md'),'sha256':'a' * 64,
                        'line_start':10,'line_end':12}
            manifest = {'units':[{'unit_id':'article','title':'Complete article','content_path':'article.md',
                                 'content_sha256':sha256(text),'source_refs':[upstream]}]}
            (prepared / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            config = {'course':{}, 'prepared_source_manifests':[{'manifest_path':str(prepared / 'manifest.json'),
                'boundary':'Complete article, analysis and translation','teaching_scope':['Discourse'],
                'subunits':[{'key':'article','title':'Article'},{'key':'analysis','title':'Analysis'},
                            {'key':'translation','title':'Translation'}]}]}
            output = base / 'materials'
            build(config, output)
            packaged = json.loads((output / 'materials-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual((output / packaged['units'][0]['content_path']).read_bytes(), text)
            self.assertEqual(validate_package(packaged, output)['exact_source_spans'], 1)
            retained = Path(packaged['units'][0]['source_refs'][1]['path'])
            self.assertEqual(json.loads(retained.read_text(encoding='utf-8'))['units'][0]['source_refs'], [upstream])

    def test_external_summary_keeps_scope_and_does_not_claim_full_source_transcription(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            prepared = base / 'references'
            prepared.mkdir()
            text = b'# Alpha\nNoun and verb meaning groups.\n[Reference](https://publisher.example/alpha)\n'
            (prepared / 'alpha.md').write_bytes(text)
            manifest = {'role':'independent_reference_sources',
                'representation':'source_informed_summary_with_complete_reference_links',
                'not_verbatim_original_corpus':True, 'not_full_webpage_transcription':True,
                'units':[{'unit_id':'ref-alpha','title':'Alpha','role':'independent_reference',
                    'content_path':'alpha.md','content_sha256':sha256(text),
                    'source_refs':[{'url':'https://publisher.example/alpha','publisher':'Fixture publisher'}],
                    'completeness':{'kind':'complete_natural_reference_scope',
                        'boundary':'Whole Alpha headword, noun and verb.',
                        'representation':'paraphrased_reference_note',
                        'full_page_copied':False,'all_source_examples_reproduced':False}}]}
            path = prepared / 'manifest.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            config = {'course':{}, 'prepared_source_manifests':[{'manifest_path':str(path),
                'representation':'external_reference_summary','teaching_scope':['Whole declared reference scope']} ]}
            output = base / 'materials'
            build(config, output)
            packaged = json.loads((output / 'materials-manifest.json').read_text(encoding='utf-8'))
            unit = packaged['units'][0]
            self.assertEqual(unit['completeness']['kind'], 'external_reference_summary')
            self.assertIn('Whole Alpha headword, noun and verb.', unit['completeness']['boundary'])
            self.assertEqual((output / unit['content_path']).read_bytes(), text)
            self.assertEqual(validate_package(packaged, output)['exact_source_spans'], 1)
            retained = json.loads(Path(unit['source_refs'][1]['path']).read_text(encoding='utf-8'))
            self.assertEqual(retained, manifest)
            manifest['not_full_webpage_transcription'] = False
            path.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'declare their representation'):
                build(config, output)
            manifest['not_full_webpage_transcription'] = True
            manifest['units'][0]['completeness']['all_source_examples_reproduced'] = True
            path.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'explicit natural reference scope'):
                build(config, output)


if __name__ == '__main__':
    unittest.main()
