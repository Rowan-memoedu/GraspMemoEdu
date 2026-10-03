import copy
import json
import unittest

from graspmemoedu.topic_format import (content_metadata, content_version, dump_json, dump_markdown,
                                     normalize_document, parse_source, source_text)
from tests.topic_format_fixtures import introduction_document, lesson_document, node


class TopicFormatTests(unittest.TestCase):
    def test_separator_whitespace_normalizes_equally_without_losing_internal_linebreaks(self):
        value = introduction_document()
        value['title'] = '  Synthetic whitespace  '
        value['nodes'][1]['children'][0]['text'] = '第一行。  \n第二行。\n\n'
        document, _ = normalize_document(value)
        markdown, _ = parse_source(dump_markdown(document))
        self.assertEqual(dump_json(document), dump_json(markdown))
        self.assertEqual(document['nodes'][1]['children'][0]['text'], '第一行。  \n第二行。')
        value['title'] = '  '
        with self.assertRaisesRegex(ValueError, 'one-line title'):
            normalize_document(value)

    def test_literal_code_does_not_become_an_authored_reference(self):
        document = introduction_document()
        document['nodes'][0]['children'].append(node('literal',
            '`[[id:InlineLiteral|语法样例]]`\n```text\n[[id:FencedLiteral|语法样例]]\n```'))
        document, _ = normalize_document(document)
        targets = {r['target_rem_id'] for r in content_metadata(document)['references']}
        self.assertNotIn('InlineLiteral', targets)
        self.assertNotIn('FencedLiteral', targets)

    def test_two_inputs_preserve_order_identity_body_and_reference_ownership(self):
        document, _ = normalize_document(lesson_document())
        from_json, _ = parse_source(dump_json(document))
        from_markdown, _ = parse_source(dump_markdown(document))
        self.assertEqual(dump_json(from_json), dump_json(from_markdown))
        self.assertEqual(content_version(from_json), content_version(from_markdown))
        metadata = content_metadata(from_markdown)
        self.assertEqual(metadata['content_order'], [{'kind': 'introduction', 'id': 'intro-a'},
            {'kind': 'example', 'id': 'example-1'}, {'kind': 'introduction', 'id': 'intro-b'}])
        idea = next(n for n in metadata['content_nodes'] if n['id'] == 'answer-idea')
        self.assertEqual(idea['owner'], {'kind': 'example', 'id': 'example-1-question', 'field': 'explanation_html'})
        self.assertIn({'source_node_id': 'answer-idea', 'target_rem_id': 'DefinitionRem', 'label': '定义'}, metadata['references'])
        self.assertIn('**idea**：[[id:DefinitionRem|定义]]', source_text(dump_json(document)))

    def test_source_omits_missing_values_and_metadata_from_visible_text(self):
        document, _ = normalize_document(introduction_document())
        source = document['nodes'][0]['children'][2]
        self.assertEqual(source['text'], 'Synthetic book｜练习 1.2')
        self.assertEqual(source['source']['difficulty'], '★')
        self.assertNotIn('未知', source['text'])
        altered = copy.deepcopy(document)
        altered['nodes'][0]['children'][2]['source']['difficulty'] = '★★'
        altered['nodes'][0]['children'][2]['source']['url'] = 'https://example.test/book'
        self.assertEqual(content_version(document), content_version(altered))

    def test_actual_tags_identify_callouts_but_plain_words_do_not(self):
        value = introduction_document()
        leaf = value['nodes'][0]['children'][1]
        leaf.update(source_rem_id='HintRem', source_tags=[{'tag_rem_id': 'HintTag', 'name': 'hint'}])
        del leaf['callout']
        value['nodes'][1]['children'][0]['text'] = 'The word hint alone is ordinary prose.'
        document, _ = normalize_document(value)
        self.assertEqual(document['nodes'][0]['children'][1]['callout'], 'hint')
        self.assertNotIn('callout', document['nodes'][1]['children'][0])
        leaf['source_tags'].append({'tag_rem_id': 'RequirementTag', 'name': '要求'})
        with self.assertRaisesRegex(ValueError, 'Conflicting Callout'):
            normalize_document(value)

    def test_external_reference_exclusion_never_swallows_mixed_body(self):
        value = introduction_document()
        value['nodes'][0]['children'].append(node('external', '[Pending](https://example.test)'))
        document, ignored = normalize_document(value)
        self.assertNotIn('external', [n['id'] for n in document['nodes'][0]['children']])
        self.assertEqual(ignored, [{'node_id': 'external', 'reason': 'external-reference'}])
        value['nodes'][0]['children'][-1]['text'] = 'Keep this theorem; see [Pending](https://example.test).'
        before = copy.deepcopy(value)
        with self.assertRaisesRegex(ValueError, 'mixed with other body'):
            normalize_document(value)
        self.assertEqual(value, before)

    def test_reference_target_and_label_change_content_version_but_provenance_does_not(self):
        document, _ = normalize_document(introduction_document())
        changed = copy.deepcopy(document)
        changed['nodes'][0]['children'][0]['source_rem_id'] = 'AnotherRem'
        self.assertEqual(content_version(document), content_version(changed))
        changed['nodes'][0]['children'][-1]['text'] = '参见 [[id:OtherDocument|后续内容]]。'
        self.assertNotEqual(content_version(document), content_version(changed))

    def test_image_bytes_affect_content_version_but_relative_rename_does_not(self):
        document, _ = normalize_document(introduction_document())
        document['nodes'][1]['children'][0]['text'] = '![label](assets/a.png)'
        renamed = copy.deepcopy(document)
        renamed['nodes'][1]['children'][0]['text'] = '![label](assets/b.png)'
        a = [{'path': 'assets/a.png', 'sha256': 'a' * 64}]
        b = [{'path': 'assets/b.png', 'sha256': 'a' * 64}]
        self.assertEqual(content_version(document, a), content_version(renamed, b))
        b[0]['sha256'] = 'b' * 64
        self.assertNotEqual(content_version(document, a), content_version(renamed, b))

    def test_unsupported_fields_duplicate_ids_and_ambiguous_rem_ids_fail(self):
        for mutation, error in [
            (lambda d: d['nodes'][1].update(id='intro-a'), 'Duplicate node ID'),
            (lambda d: d['nodes'][1].update(source_rem_id='IntroRem'), 'duplicate source Rem'),
            (lambda d: d['nodes'][1].update(unrecognized='must not disappear'), 'Unknown node field'),
            (lambda d: d.update(topic_kind='lesson'), 'Topic kind'),
        ]:
            value = introduction_document()
            mutation(value)
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                normalize_document(value)

    def test_remnote_callout_requires_provenance_and_source_text_conflict_fails(self):
        value = introduction_document()
        value['nodes'][0]['children'][1]['source_rem_id'] = 'UnverifiedHintRem'
        with self.assertRaisesRegex(ValueError, 'real identifying source tag'):
            normalize_document(value)
        value = introduction_document()
        value['nodes'][0]['children'][2]['text'] = 'Different citation'
        with self.assertRaisesRegex(ValueError, '题源 text differs'):
            normalize_document(value)

    def test_literal_mermaid_lines_roundtrip_without_outline_reinterpretation(self):
        value = introduction_document()
        value['nodes'][0]['children'][3]['text'] = '**范式**：\n```mermaid\nflowchart TD\n  - literal\nA["$x$"] --> B\n```'
        document, _ = normalize_document(value)
        parsed, _ = parse_source(dump_markdown(document))
        self.assertEqual(dump_json(document), dump_json(parsed))


if __name__ == '__main__':
    unittest.main()
