"""Held-out structural fixtures for the source-neutral course producer."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from produce_goal_course import (validate_plan, validate_author, validate_blind, learner_view,
                                 apply_evidence_repairs, project_atomic, project_paradigm)
from prepare_goal_course import project_goal
from retrieve_goal_materials import lexical_entries, vocabulary


class GoalCourseProductionTests(unittest.TestCase):
    def test_retrieval_preserves_whole_entries_and_contrast_notes(self):
        text = '# Material\n\n- light\n  - A light bag.\n    - Not heavy.\n- flight\n  - A long flight.\n## Batch\n- ➯trip, journey, travel\n- A three-day trip.\n'
        rows = list(lexical_entries(text))
        self.assertEqual([row[0] for row in rows], ['Material', 'light', 'flight', 'Batch', 'trip, journey, travel'])
        self.assertIn('Not heavy.', rows[1][3])
        self.assertNotIn('flight', rows[1][3])
        self.assertIn('A three-day trip.', rows[-1][3])
        self.assertIn('close', vocabulary('The doors stayed closed.'))
        self.assertNotIn('light', vocabulary('flights'))

    def fixture(self, prefix, fact_a, fact_b):
        source = {'blank_ids': ['b1'], 'sources': [{'id': 'source', 'text': fact_a + ' ' + fact_b}]}
        points = [{'id': prefix + str(i), 'content': fact, 'target_required': i == 0,
                   'assessed_by': ['b1'] if i == 0 else [], 'evidence': [{'source_id': 'source', 'quote': fact}]}
                  for i, fact in enumerate((fact_a, fact_b))]
        topic = {'id': prefix, 'title': 'Scope', 'kind': 'atomic', 'objectives': ['Recall'],
            'source_ids': ['source'], 'prerequisites': [],
            'sections': [{'id': 'intro', 'title': 'Introduction', 'scope': 'Complete system',
                'chunks': [{'id': 'chunk', 'title': 'Two facts', 'points': points}]}]}
        cards = [{'id': 'card-' + p['id'], 'point_ids': [p['id']], 'question': 'Recall the named fact.',
                  'reference_answer': p['content'], 'explanation': p['content'],
                  'interaction': {'version': 1, 'type': 'text', 'grading': 'semantic'},
                  'learning_phases': ['learn', 'review']} for p in points]
        cards.append({**deepcopy(cards[0]), 'id': 'bundle', 'point_ids': [p['id'] for p in points],
                      'question': 'State both facts.', 'learning_phases': ['review']})
        candidate = {'id': prefix, 'title': 'Scope', 'sections': [{'id': 'intro', 'title': 'Introduction',
            'paragraphs': [fact_a, fact_b], 'chunks': [{'id': 'chunk', 'title': 'Two facts', 'mnemonic': 'Pair',
                'points': [{'id': p['id'], 'content': p['content']} for p in points]}], 'cards': cards}]}
        return source, {'topics': [topic]}, candidate

    def test_two_independent_sources_use_the_same_contract(self):
        for args in [('color', 'Red is a colour.', 'Blue is a colour.'),
                     ('parity', 'Two is even.', 'Three is odd.')]:
            with self.subTest(source=args[0]):
                source, plan, candidate = self.fixture(*args)
                validate_plan(plan, source)
                validate_author(candidate, plan['topics'][0])
                view = learner_view(candidate, 'atomic')
                self.assertNotIn('reference_answer', str(view))
                self.assertNotIn('point_ids', str(view))
                blind = {'approved': True, 'checks': [{'index': i, 'approved': True, 'issues': []} for i in (1, 2, 3)]}
                validate_blind(blind, view)

    def test_missing_goal_evidence_cycle_and_dropped_point_fail(self):
        source, plan, candidate = self.fixture('fixture', 'First fact.', 'Second fact.')
        bad = deepcopy(plan); bad['topics'][0]['sections'][0]['chunks'][0]['points'][0]['evidence'][0]['quote'] = 'invented'
        with self.assertRaises(ValueError): validate_plan(bad, source)
        bad = deepcopy(plan); bad['topics'][0]['prerequisites'] = ['fixture']
        with self.assertRaises(ValueError): validate_plan(bad, source)
        bad = deepcopy(candidate); bad['sections'][0]['cards'] = bad['sections'][0]['cards'][1:]
        with self.assertRaises(ValueError): validate_author(bad, plan['topics'][0])

    def test_incomplete_blind_audit_cannot_claim_pass(self):
        _, _, candidate = self.fixture('fixture', 'First fact.', 'Second fact.')
        view = learner_view(candidate, 'atomic')
        with self.assertRaises(ValueError): validate_blind({'approved': True, 'checks': []}, view)
        with self.assertRaises(ValueError):
            validate_blind({'approved': True, 'checks': [{'index': i, 'approved': True, 'issues': ['missing']} for i in (1, 2, 3)]}, view)

    def test_projection_preserves_prose_points_cards_and_anchor_offsets(self):
        _, _, candidate = self.fixture('fixture', 'A < B & C.', 'A second fact.')
        projected = project_atomic(candidate, 'course-fixture')
        section = projected['sections'][0]
        anchor = section['memory_chunks'][0]['anchors'][0]
        self.assertEqual(section['introduction']['plain_text'][anchor['start']:anchor['end']], anchor['quote'])
        self.assertEqual(section['flashcards'], candidate['sections'][0]['cards'])
        self.assertIn('A &lt; B &amp; C.', section['introduction']['html'])
        paradigm = {'title': 'A method', 'introduction': ['First reason. Then check.'],
                    'examples': [{'question': 'Explain a method.', 'explanation': 'A full solution.',
                                  'practices': [{'question': f'Case {i}', 'explanation': f'Solution {i}'} for i in range(7)]}]}
        document = project_paradigm(paradigm, 'course-paradigm')
        from graspmemoedu.topic_format import normalize_document
        normalized, ignored = normalize_document(document)
        self.assertFalse(ignored)
        self.assertEqual(normalized['nodes'][1]['id'], 'example-1')
        self.assertEqual(len(document['nodes'][1]['children']), 9)
        self.assertEqual(document['nodes'][0]['children'][0]['text'], paradigm['introduction'][0])

    def test_evidence_repair_is_metadata_only_and_source_neutral(self):
        for prefix, a, b in [('color', 'Red is a colour.', 'Blue is a colour.'),
                             ('parity', 'Two is even.', 'Three is odd.')]:
            source, plan, _ = self.fixture(prefix, a, b)
            damaged = deepcopy(plan)
            damaged['topics'][0]['sections'][0]['chunks'][0]['points'][0]['evidence'][0]['quote'] = 'Wrong quote.'
            repairs = {'repairs': [{'point_id': prefix + '0', 'evidence': [{'source_id': 'source', 'quote': a}]}]}
            self.assertEqual(apply_evidence_repairs(damaged, repairs, source), plan)
            repairs['repairs'][0]['point_id'] = prefix + '1'
            with self.assertRaises(ValueError): apply_evidence_repairs(damaged, repairs, source)

    def test_goal_projection_preserves_original_and_separates_support_from_assessment(self):
        source, plan, _ = self.fixture('fixture', 'This is ____ (1).', 'A supporting fact.')
        source.update(course_id='held-out', subject_id='english', goal_title='Original')
        plan['topics'][0]['sections'][0]['chunks'][0]['points'][1]['target_required'] = True
        original = {'source_id': 'source', 'bank_id': 'bank', 'bank_title': 'Bank', 'group_id': 'group',
                    'question_id': 'question', 'instruction': 'Complete the text.',
                    'blanks': [{'id': 'b1', 'label': '1', 'answers': ['one'], 'source_marker': '____ (1)'}]}
        bank = project_goal(source, plan, original)
        group, = bank['groups']; q, = group['questions']
        self.assertEqual(q['question'], 'Complete the text.\n\nThis is {{blank:b1}}. A supporting fact.')
        self.assertEqual(q['point_ids'], ['held-out-fixture/fixture0'])
        self.assertEqual(group['unlock_policy']['point_ids'], ['held-out-fixture/fixture0', 'held-out-fixture/fixture1'])
        scoring = {'blanks': [{'id': 'b1', 'answers': ['one', 'a single item'],
                              'point_ids': ['fixture0'], 'explanation': 'Both variants express one item.'}]}
        revised = project_goal(source, plan, original, scoring)['groups'][0]
        self.assertEqual(revised['unlock_policy'], group['unlock_policy'])
        self.assertEqual(revised['questions'][0]['question'], q['question'])
        self.assertEqual(revised['questions'][0]['point_ids'], q['point_ids'])
        scoring['blanks'][0]['answers'] = ['replacement']
        with self.assertRaises(ValueError): project_goal(source, plan, original, scoring)
        original['blanks'][0]['source_marker'] = 'absent'
        with self.assertRaises(ValueError): project_goal(source, plan, original)


if __name__ == '__main__':
    unittest.main()
