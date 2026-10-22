from copy import deepcopy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import unittest
import uuid

from graspmemoedu.atomic import AtomicLearningService
from graspmemoedu.atomic_content import validate_atomic
from graspmemoedu.point_assessment import assess_points, item_results
from atomic_fixtures import atomic_topic
from tests.fixtures import temporary_test_directory
import test_atomic
import test_shared_architecture
from graspmemoedu.subject_registry import SUBJECT_IDS


class ItemContractTests(unittest.TestCase):
    def card(self):
        card = deepcopy(atomic_topic()['flashcards'][-1])
        card['point_ids'].append('c')
        card['assessment'] = {'version': 1, 'item_points': {'a': ['a'], 'b': ['b']}}
        return card

    def test_specific_items_win_and_unmapped_point_uses_whole(self):
        values, evidence = assess_points(self.card(), '{"a":"红色","b":"错"}', False)
        self.assertEqual(values, {'a': True, 'b': False, 'c': False})
        self.assertEqual(evidence['points'][-1]['basis'], 'whole')

    def test_several_items_on_one_point_are_aggregated_once(self):
        card = self.card()
        card['assessment']['item_points']['b'].append('a')
        values, evidence = assess_points(card, '{"a":"红色","b":"错"}', False)
        self.assertFalse(values['a'])
        self.assertEqual([p['id'] for p in evidence['points']].count('a'), 1)

    def test_incomplete_timed_submission_keeps_valid_evidence(self):
        self.assertEqual(assess_points(self.card(), '{"a":"红色","b":""}', False)[0],
                         {'a': True, 'b': False, 'c': False})
        self.assertEqual(assess_points(self.card(), '', False)[0], {'a': False, 'b': False, 'c': False})

    def test_malformed_deadline_choice_draft_cannot_break_settlement(self):
        card = {'interaction': {'type': 'choice', 'multiple': True, 'correct_ids': ['a', 'b']}}
        for answer in ('{"selected":[{}]}', '{"selected":["a","a","b"]}', '[]'):
            self.assertEqual(item_results(card, answer), {'answer': False})

    def test_deadline_matching_requires_unambiguous_pairs_but_keeps_partial_evidence(self):
        pairs = [{'left': 'l1', 'right': 'r1'}, {'left': 'l2', 'right': 'r2'}]
        card = {'point_ids': ['a', 'b'], 'interaction': {'type': 'matching', 'pairs': pairs,
                'left': [{'id': 'l1'}, {'id': 'l2'}], 'right': [{'id': 'r1'}, {'id': 'r2'}]},
                'assessment': {'version': 1, 'item_points': {'l1': ['a'], 'l2': ['b']}}}
        self.assertEqual(assess_points(card, json.dumps(pairs[:1]), False)[0], {'a': True, 'b': False})
        for submitted in (pairs + [{'left': 'l1', 'right': 'r2'}], [pairs[0], pairs[0]],
                          [pairs[0], {'left': 'l2', 'right': 'r1'}],
                          [pairs[0], {'left': {}, 'right': 'r2'}],
                          [pairs[0], {'left': 'missing', 'right': 'r2'}]):
            with self.subTest(answer=submitted):
                self.assertEqual(assess_points(card, json.dumps(submitted), False)[0], {'a': False, 'b': False})

    def test_legacy_and_self_assessment_are_unchanged(self):
        card = self.card()
        self.assertEqual(assess_points(card, '', True, self_assessed=True),
                         ({'a': True, 'b': True, 'c': True}, None))
        del card['assessment']
        self.assertEqual(assess_points(card, '{"a":"红色","b":"错"}', False),
                         ({'a': False, 'b': False, 'c': False}, None))

    def test_contract_rejects_dangling_items_points_and_semantic_items(self):
        for mapping, semantic in [({'missing': ['a']}, False), ({'a': ['missing']}, False),
                                  ({'a': ['a', 'a']}, False), ({'a': ['a']}, True)]:
            topic = atomic_topic()
            card = topic['flashcards'][-1]
            card['assessment'] = {'version': 1, 'item_points': mapping}
            if semantic:
                card['interaction']['grading'] = 'semantic'
            with self.assertRaises(ValueError):
                validate_atomic(topic)


class ItemSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.when = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)
        self.topic = atomic_topic()
        self.service = AtomicLearningService(self.topic, Path(self.temp.name), clock=lambda: self.when)
    action = test_atomic.AtomicTests.action
    advance = test_atomic.AtomicTests.advance
    submit = test_atomic.AtomicTests.submit
    finish_initial = test_atomic.AtomicTests.finish_initial
    raw = test_atomic.AtomicTests.raw

    def test_review_updates_each_point_once_and_survives_restart_and_retry(self):
        card = self.topic['flashcards'][-1]
        card['assessment'] = {'version': 1, 'item_points': {'a': ['a'], 'b': ['b']}}
        self.service = AtomicLearningService(self.topic, self.service.data_dir, clock=lambda: self.when)
        self.finish_initial()
        self.when += timedelta(hours=6, minutes=1)
        self.service.choose(self.action(choice='review'))
        self.assertNotIn('assessment_result', self.service.state()['current'])
        payload, result = self.submit('{"a":"红色","b":"错误"}')
        current = result['state']['current']
        self.assertFalse(current['correct'])
        self.assertEqual(current['ratings'], {'a': 3, 'b': 1})
        self.assertEqual(len(current['assessment_result']['points']), 2)
        before = self.raw()['points']
        self.service.submit(payload)
        self.assertEqual(self.raw()['points'], before)
        self.service = AtomicLearningService(self.topic, self.service.data_dir, clock=lambda: self.when)
        self.assertEqual(self.raw()['points'], before)
        with closing(self.service._connect()) as con:
            events = [json.loads(row[0]) for row in con.execute('SELECT body FROM events')]
        self.assertEqual(sum(e['type'] == 'atomic_rating' for e in events), 2)


class ItemPaperTests(unittest.TestCase):
    setUp = test_shared_architecture.SharedArchitectureTests.setUp
    call = test_shared_architecture.SharedArchitectureTests.call

    def test_shared_paper_applies_specific_and_whole_evidence_once_in_every_subject(self):
        for subject in SUBJECT_IDS:
            with self.subTest(subject=subject):
                tid = 'atomic-' + subject
                topic = self.app.state.catalog.topic(tid)
                card = next(c for c in topic['flashcards'] if len(c['point_ids']) == 2)
                card['point_ids'] = ['a', 'b', 'c']
                card['assessment'] = {'version': 1, 'item_points': {'a': ['a'], 'b': ['b']}}
                self.call('GET', '/api/state?subject_id=' + subject + '&topic_id=' + tid)
                backend = self.app.state.learning_services[self.learner, tid]
                with backend._transaction() as con:
                    state = backend._load(con)
                    state.update(completed_at=self.when.isoformat(), status='completed', phase='completed')
                    for memory in state['points'].values():
                        memory.update(mastered=True, first_passed_at=self.when.isoformat(), due_at=self.when.isoformat())
                    backend._save(con, state)
                self.app.state.review_paper_types = [{'id': 'item-' + subject, 'title': 'Item evidence',
                    'subject': subject, 'enabled': True, 'requirements': {'fill_blank': 1}}]
                inbox = self.call('GET', '/api/review-papers/inbox?subject_id=' + subject)
                candidate = next(c for c in inbox['items'] if c['kind'] == 'paper')
                paper = self.call('POST', '/api/review-papers/open?subject_id=' + subject,
                    {'candidate_id': candidate['id'], 'request_id': str(uuid.uuid4())})
                self.assertNotIn('assessment_result', paper['items'][0])
                paper = self.call('POST', '/api/review-papers/draft', {'paper_id': paper['id'],
                    'expected_revision': paper['revision'], 'answers': [{'index': 0, 'answer': '{"a":"红色","b":"wrong"}'}]})
                payload = {'paper_id': paper['id'], 'expected_revision': paper['revision'], 'request_id': str(uuid.uuid4())}
                self.call('POST', '/api/review-papers/submit', payload)
                for _ in range(200):
                    paper = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
                    if paper['job_status'] not in ('pending', 'running'):
                        break
                    time.sleep(.01)
                self.assertEqual(paper['phase'], 'done', paper)
                result = paper['items'][0]['result']
                self.assertFalse(result['correct'])
                points = result['assessment_result']['points']
                self.assertEqual({p['id']: p['correct'] for p in points}, {'a': True, 'b': False, 'c': False})
                self.assertEqual(points[-1]['basis'], 'whole')
                with closing(backend._connect()) as con:
                    before = deepcopy(backend._load(con)['points'])
                self.assertEqual({p: v['last_rating'] for p, v in before.items()}, {'a': 3, 'b': 1, 'c': 1})
                self.call('POST', '/api/review-papers/submit', payload)
                with closing(backend._connect()) as con:
                    self.assertEqual(backend._load(con)['points'], before)
                    events = [json.loads(r[0]) for r in con.execute('SELECT body FROM events WHERE topic=?', (tid,))]
                    self.assertEqual(sum(e['type'] == 'atomic_rating' for e in events), 3)


if __name__ == '__main__':
    unittest.main()
