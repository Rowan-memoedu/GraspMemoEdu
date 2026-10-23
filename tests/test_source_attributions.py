"""Public source metadata remains separate from teaching prose and private state."""
from copy import deepcopy
import unittest

from graspmemoedu.atomic_content import chunk_presentation, public_source_attributions, validate_source_attributions
from graspmemoedu.materials import topic_blocks
from graspmemoedu.source_content import reading_blocks
from graspmemoedu.service import create_learning_service
from graspmemoedu.structured_atomic import normalize
from tests.atomic_fixtures import atomic_topic
from tests.fixtures import temporary_test_directory, DeterministicJudge
from tests.test_ordered_learning import payload, active, ordered_topic
from tests.test_unified_learning import composed_topic


ATTRIBUTIONS = [{'text': 'Example lexicon <not markup>', 'url': 'https://example.org/lexicon',
                 'license': 'CC BY 4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
                 'changes': 'Teaching explanation adapted.'}]


class SourceAttributionTests(unittest.TestCase):
    def test_structured_projection_keeps_metadata_outside_html_and_preserves_input(self):
        source = {'source_format': 'structured-atomic-v1', 'id': 'neutral', 'title': 'Fact', 'subject_id': 'english',
                  'sections': [{'id': 'section', 'section_title': 'Statement', 'status': 'ai_passed', 'import_eligible': True,
                    'introduction': {'html': '<p>A label.</p>', 'plain_text': 'A label.'},
                    'source_attributions': deepcopy(ATTRIBUTIONS),
                    'memory_chunks': [{'id': 'c', 'content': 'A label.', 'anchors': [{'start': 0, 'end': 8, 'quote': 'A label.'}]}],
                    'memory_points': [{'id': 'p', 'chunk_id': 'c', 'content': 'A label.'}],
                    'flashcards': [{'id': 'q', 'point_ids': ['p'], 'question': 'Recall the label.', 'answer': 'A label.'}]}]}
        before = deepcopy(source)
        topic = normalize(source)
        self.assertEqual(source, before)
        self.assertEqual(topic['introductions'][0]['source_attributions'], ATTRIBUTIONS)
        self.assertEqual(topic['memory_chunks'][0]['source_attributions'], ATTRIBUTIONS)
        self.assertEqual(topic['introductions'][0]['html'], '<p>A label.</p>')
        topic['memory_chunks'][0]['source_attributions'][0]['text'] = 'Changed'
        self.assertEqual(topic['introductions'][0]['source_attributions'], ATTRIBUTIONS)
        self.assertEqual(source, before)

    def test_guidance_chunk_and_admin_content_preserve_public_fields_without_learning_changes(self):
        topic = composed_topic()
        for introduction in topic['introductions']:
            introduction['source_attributions'] = deepcopy(ATTRIBUTIONS)
        for chunk in topic['memory_chunks']:
            chunk['source_attributions'] = deepcopy(ATTRIBUTIONS)
        with temporary_test_directory() as directory:
            service = create_learning_service(topic, directory, DeterministicJudge())
            state = service.state()
            self.assertEqual(active(state)['source_attributions'], ATTRIBUTIONS)
            chunk_state = service.advance(payload(service))
            self.assertEqual(chunk_state['chunk']['source_attributions'], ATTRIBUTIONS)
            self.assertEqual(chunk_state['chunk']['introduction']['source_attributions'], ATTRIBUTIONS)
            self.assertEqual(chunk_state['atomic_progress']['completed'], 0)
        for block in topic_blocks(topic):
            if block['kind'] in ('introduction', 'chunk'):
                self.assertEqual(block['source_attributions'], ATTRIBUTIONS)
                self.assertNotIn('CC BY 4.0', block['html'])

    def test_unsafe_links_private_fields_and_unbounded_records_are_rejected(self):
        for url in ('javascript:alert(1)', 'file:///D:/private.txt', '//example.org/path',
                    'https://user:password@example.org', 'https://example.org/a b', 'https://example.org\\private'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_source_attributions([{'text': 'Source', 'url': url}])
        for value in (None, {}, [{'text': 'Source', 'private_path': 'D:/private/source.json'}],
                      [{'text': 'Source', 'status': 'ai_passed'}], [{'text': 'x' * 4001}], ATTRIBUTIONS * 31):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                validate_source_attributions(value)
        self.assertEqual(public_source_attributions([{'text': 'Source', 'private_path': 'D:/private',
            'status': 'ai_passed', 'url': 'javascript:alert(1)'}]), [{'text': 'Source'}])

    def test_legacy_chunk_projection_has_no_new_empty_ui_field(self):
        topic = atomic_topic()
        projection = chunk_presentation(topic, topic['memory_chunks'][0])
        self.assertNotIn('source_attributions', projection)
        self.assertNotIn('source_attributions', projection['introduction'])

    def test_read_only_reader_retains_sources_when_grouping_consecutive_guidance(self):
        topic = ordered_topic(intro_only=True)
        topic['introductions'][0]['source_attributions'] = deepcopy(ATTRIBUTIONS)
        topic['introductions'][1]['source_attributions'] = [*deepcopy(ATTRIBUTIONS),
            {'text': 'Second source.', 'url': 'https://example.org/second', 'private_path': 'D:/private'}]
        before = deepcopy(topic)
        blocks = reading_blocks(topic)
        self.assertEqual(topic, before)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['introduction_ids'], ['intro-a', 'intro-b'])
        self.assertEqual(blocks[0]['source_attributions'], [*ATTRIBUTIONS,
            {'text': 'Second source.', 'url': 'https://example.org/second'}])
        self.assertNotIn('CC BY 4.0', blocks[0]['html'])


if __name__ == '__main__':
    unittest.main()
