"""Read-only teaching projections for current cards and bundled review cards."""
from copy import deepcopy
import unittest

from graspmemoedu.atomic_content import card_chunks, chunk_presentation, learning_presentation
from tests.atomic_fixtures import atomic_topic
from tests import test_unified_learning as unified


class ChunkPresentationTests(unittest.TestCase):
    def test_separate_mnemonic_and_full_introduction_preserve_source(self):
        topic = atomic_topic()
        chunk = topic['memory_chunks'][0]
        chunk.update(content_html='<p>Core.</p>', mnemonic_html='<p>Memory aid.</p>',
                     html='<p>Core.</p><p>Memory aid.</p>')
        before = deepcopy(topic)
        public = chunk_presentation(topic, chunk)
        self.assertEqual(public['content_html'], '<p>Core.</p>')
        self.assertEqual(public['mnemonic_html'], '<p>Memory aid.</p>')
        self.assertEqual(public['introduction']['html'], topic['introduction']['html'])
        self.assertEqual(public['anchors'], chunk['anchors'])
        self.assertEqual(topic, before)

    def test_legacy_mnemonic_boundary_is_exposed_without_inventing_fields(self):
        topic = atomic_topic()
        chunk = topic['memory_chunks'][0]
        chunk['mnemonic'] = {'source_quotes': ['Existing aid'], 'asset_ids': []}
        public = chunk_presentation(topic, chunk)
        self.assertTrue(public['has_mnemonic'])
        self.assertNotIn('content_html', public)
        self.assertEqual(public['html'], chunk['html'])
        self.assertFalse(chunk_presentation(topic, topic['memory_chunks'][1])['has_mnemonic'])

    def test_bundle_projects_only_its_chunks_with_exact_introduction_owners(self):
        topic = unified.composed_topic()
        topic['introductions'][1]['html'] += '<p>Second section only.</p>'
        chunks = card_chunks(topic, {'point_ids': ['a', 'c']})
        self.assertEqual([c['id'] for c in chunks], ['colors', 'shapes'])
        self.assertEqual([c['introduction']['id'] for c in chunks], ['intro-a', 'intro-b'])
        self.assertNotIn('Second section only.', chunks[0]['introduction']['html'])
        self.assertIn('Second section only.', chunks[1]['introduction']['html'])
        self.assertEqual(card_chunks(topic, {'point_ids': ['standalone']}), [])


class ActiveChunkTests(unittest.TestCase):
    setUp = unified.UnifiedLearningTests.setUp
    make = unified.UnifiedLearningTests.make
    raw = unified.UnifiedLearningTests.raw
    advance = unified.UnifiedLearningTests.advance
    answer = unified.UnifiedLearningTests.answer
    first_section = unified.UnifiedLearningTests.first_section

    def test_reading_and_card_projection_do_not_change_learning_or_expose_other_chunks(self):
        reading = self.advance()
        self.assertEqual(reading['chunk']['introduction']['html'], self.topic['introductions'][0]['html'])
        current = self.advance()
        before = self.raw()
        again = self.service.state()
        self.assertEqual([c['id'] for c in again['current']['memory_chunks']], ['colors'])
        self.assertIsNone(again['introduction'])
        self.assertEqual(current['active_step_id'], again['active_step_id'])
        self.assertEqual(self.raw(), before)

    def test_whole_topic_counts_and_hierarchy_survive_engine_and_service_changes(self):
        initial = self.service.state()
        self.assertEqual(initial['topic_progress'], {
            'introductions': {'completed': 0, 'total': 2},
            'chunks': {'completed': 0, 'total': 2}, 'points': {'completed': 0, 'total': 3}})
        reading = self.advance()
        self.assertEqual(reading['topic_progress']['introductions'], {'completed': 1, 'total': 2})
        self.assertEqual(reading['topic_progress']['chunks'], {'completed': 0, 'total': 2})
        active = self.advance()
        self.assertEqual(active['topic_progress']['chunks'], {'completed': 1, 'total': 2})
        self.assertEqual(active['learning_sections'][0]['chunks'][0]['point_ids'], ['a', 'b'])
        self.assertEqual(active['learning_sections'][1]['chunks'][0]['point_ids'], ['c'])
        self.answer('{"a":"wrong","b":"wrong"}')
        record = self.service.state()['history'][0]
        self.assertFalse(record['viewable'])
        self.assertNotIn('html', record)
        self.assertNotIn('answer', record)
        self.assertEqual(self.make().state()['topic_progress'], self.service.state()['topic_progress'])

    def test_counts_do_not_reset_at_paradigm_stage_and_empty_chunks_stay_optional(self):
        state = self.first_section()
        self.assertEqual(state['topic_progress']['points'], {'completed': 2, 'total': 3})
        self.assertEqual(state['topic_progress']['chunks'], {'completed': 1, 'total': 2})
        self.assertEqual(state['topic_progress']['introductions'], {'completed': 1, 'total': 2})
        self.assertTrue(state['atomic_history'])
        self.assertEqual(self.make().state()['learning_sections'], state['learning_sections'])
        topic = deepcopy(self.topic)
        topic['memory_chunks'] = []
        projected = learning_presentation(topic, {'seen_chunks': [], 'points': {}}, [])
        self.assertEqual(projected['topic_progress']['chunks'], {'completed': 0, 'total': 0})
