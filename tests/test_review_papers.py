from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import json
import sqlite3
import time
import unittest
import uuid
from unittest.mock import patch

from graspmemoedu.review_cards import assemble, attach_training_variant, load_paper_types, point_id
from graspmemoedu.review_papers import PaperService
from graspmemoedu.training import TrainingService
import test_training_api as training_api_tests
import test_atomic_api as atomic_api_tests
from graspmemoedu.site_backup import backup_site, restore_site


class PaperTests(unittest.TestCase):
    setUp = training_api_tests.TrainingAPITests.setUp
    call = training_api_tests.TrainingAPITests.call
    account = training_api_tests.TrainingAPITests.account
    payload = atomic_api_tests.AtomicAPITests.payload
    finish = atomic_api_tests.AtomicAPITests.finish

    def service(self):
        access = self.app.state.access_store.resolve(self.headers['X-Learning-Identity'])
        return TrainingService(self.app.state.training_catalog, self.root, access['learner_id'],
                               self.app.state.service.judge, clock=lambda: self.when)

    def prime(self):
        backend = self.service()
        for qid, answer in [('choice', '{"selected":"b"}'), ('blank', '{"a":"0"}')]:
            attempt = backend.open(self.bank['id'], 'group-a', qid, str(uuid.uuid4()))
            sid = backend.submit({'request_id': str(uuid.uuid4()), 'attempt_id': attempt, 'answer': answer})
            backend.process_submission(sid)
            self.assertEqual(backend.state(attempt)['result']['rating'], 1)
        self.when += timedelta(days=2)
        self.app.state.review_paper_types = [
            {'id': 'small', 'title': '隔离小卷', 'subject': 'math', 'enabled': True,
             'requirements': {'choice': 1, 'fill_blank': 1}},
            {'id': 'choice-only', 'title': '隔离单选', 'subject': 'math', 'enabled': True,
             'requirements': {'choice': 1}}]

    def inbox(self):
        return self.call('GET', '/api/review-papers/inbox?subject_id=math')

    def open_paper(self):
        item = next(c for c in self.inbox()['items'] if c['kind'] == 'paper' and c['count'] == 2)
        return self.call('POST', '/api/review-papers/open?subject_id=math',
                         {'candidate_id': item['id'], 'request_id': str(uuid.uuid4())})

    def draft(self, paper, index=None):
        answers = [{'index': i, 'answer': '{"selected":"a"}' if item['interaction']['type'] == 'choice' else '{"a":"2"}',
                    'elapsed_ms': 3000} for i, item in enumerate(paper['items']) if index is None or index == i]
        return self.call('POST', '/api/review-papers/draft', {'paper_id': paper['id'],
                         'expected_revision': paper['revision'], 'answers': answers})

    def submit_paper(self, paper, index=None, expected='done', payload=None):
        payload = payload or {'paper_id': paper['id'], 'request_id': str(uuid.uuid4()),
                              'expected_revision': paper['revision'], **({'index': index} if index is not None else {})}
        result = self.call('POST', '/api/review-papers/submit', payload)
        deadline = time.monotonic() + 10
        while result['job_status'] in ('pending', 'running') and time.monotonic() < deadline:
            time.sleep(.01)
            result = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
        self.assertEqual(result['phase'], expected, result)
        return result, payload

    def memories(self):
        backend = self.service()
        with closing(backend._connect()) as con:
            return [dict(r) for r in con.execute('SELECT * FROM training_memory WHERE learner=? ORDER BY question', (backend.learner_id,))]

    def test_default_whole_paper_overlap_drafts_and_exactly_once_scheduling(self):
        self.prime(); before = self.memories(); choices = self.inbox()
        self.assertEqual(choices['due_count'], 2)
        self.assertEqual(sum(x['kind'] == 'paper' for x in choices['items']), 2)
        paper = self.open_paper(); self.assertEqual(paper['mode'], 'paper')
        self.assertEqual(paper['items'][0]['content_context'], {'topic_id': 'training.' + self.bank['id'],
            'bank_id': self.bank['id'], 'group_id': 'group-a', 'question_id': 'choice'})
        self.assertEqual(self.memories(), before)
        self.assertEqual(self.inbox()['items'], [])
        stale = next(x for x in choices['items'] if x['kind'] == 'paper' and x['count'] == 1)
        self.call('POST', '/api/review-papers/open?subject_id=math', {'candidate_id': stale['id'], 'request_id': str(uuid.uuid4())}, 409)
        paper = self.draft(paper, 0)
        self.call('POST', '/api/review-papers/submit', {'paper_id': paper['id'], 'request_id': str(uuid.uuid4()), 'expected_revision': paper['revision'], 'index': 0}, 403)
        self.call('POST', '/api/review-papers/submit', {'paper_id': paper['id'], 'request_id': str(uuid.uuid4()), 'expected_revision': paper['revision']}, 422)
        self.assertEqual(self.memories(), before)
        self.assertTrue(all(x['explanation_html'] is None and x['result'] is None for x in paper['items']))
        paper = self.draft(paper, 1)
        result, payload = self.submit_paper(paper)
        self.assertTrue(all(x['result']['correct'] for x in result['items']))
        after = self.memories(); self.assertNotEqual(after, before)
        self.assertEqual(self.call('POST', '/api/review-papers/submit', payload)['items'], result['items'])
        self.assertEqual(self.memories(), after)
        self.assertTrue(all(x['explanation_html'] for x in result['items']))

    def test_advanced_setting_frozen_on_new_paper_and_standard_cannot_choose(self):
        self.call('POST', '/api/review-papers/preferences', {'mode': 'question'}, 403)
        self.account(is_advanced_learner=True); self.prime()
        self.assertEqual(self.call('GET', '/api/review-papers/preferences')['mode'], 'paper')
        self.call('POST', '/api/review-papers/preferences', {'mode': 'question'})
        paper = self.open_paper(); self.assertEqual(paper['mode'], 'question')
        self.call('POST', '/api/review-papers/preferences', {'mode': 'paper'})
        self.assertEqual(self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])['mode'], 'question')
        before = self.memories(); paper = self.draft(paper, 0)
        result, _ = self.submit_paper(paper, 0, expected='answer')
        self.assertTrue(result['items'][0]['result']['correct']); self.assertIsNone(result['items'][1]['result'])
        after = self.memories(); self.assertEqual(after[0], before[0]); self.assertNotEqual(after[1], before[1])
        self.call('POST', '/api/review-papers/cancel', {'paper_id': paper['id']})
        self.assertEqual(self.inbox()['due_count'], 1)

    def test_failure_keeps_partial_verdicts_private_and_retry_only_judges_missing(self):
        self.prime(); before = self.memories(); paper = self.draft(self.open_paper())
        with patch('graspmemoedu.review_papers.judge_answer', side_effect=[(True, 'ok'), (None, '临时故障')]) as judge:
            failed, payload = self.submit_paper(paper, expected='grading')
            self.assertEqual(judge.call_count, 2)
        self.assertEqual(failed['job_status'], 'error'); self.assertEqual(self.memories(), before)
        self.assertTrue(all(x['result'] is None for x in failed['items']))
        self.assertEqual(failed['retry_submission'], payload)
        with self.service().transaction() as con:
            con.execute("UPDATE paper_submissions SET status='running' WHERE id=?", (payload['request_id'],))
            PaperService.recover(con)
        restored = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
        self.assertEqual(restored['retry_submission'], payload)
        with patch('graspmemoedu.review_papers.judge_answer', return_value=(True, 'ok')) as judge:
            result, _ = self.submit_paper(paper, payload=restored['retry_submission'])
            self.assertEqual(judge.call_count, 1)
        self.assertTrue(all(x['result']['correct'] for x in result['items']))

    def test_point_created_only_on_completed_answer_and_global_identity_shared(self):
        backend = self.service(); attempt = backend.open(self.bank['id'], 'group-a', 'exact', str(uuid.uuid4()))
        self.inbox()
        with closing(backend._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_points').fetchone()[0], 0)
        sid = backend.submit({'request_id': str(uuid.uuid4()), 'attempt_id': attempt, 'answer': '2'})
        backend.process_submission(sid)
        self.assertEqual(backend.state(attempt)['result']['rating'], 5)
        self.assertEqual(self.inbox()['due_count'], 0)
        self.account(); other = self.service(); attempt = other.open(self.bank['id'], 'group-a', 'exact', str(uuid.uuid4()))
        sid = other.submit({'request_id': str(uuid.uuid4()), 'attempt_id': attempt, 'answer': '0'}); other.process_submission(sid)
        with closing(other._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_points').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(DISTINCT learner) FROM training_memory').fetchone()[0], 2)

    def test_reservation_blocks_original_training_and_expiry_preserves_schedule(self):
        self.prime(); before = self.memories(); paper = self.open_paper()
        with self.assertRaisesRegex(Exception, '复习'):
            self.service().open(self.bank['id'], 'group-a', 'choice', str(uuid.uuid4()))
        self.when += timedelta(days=2)
        self.service().open(self.bank['id'], 'group-a', 'choice', str(uuid.uuid4()))
        self.assertEqual(self.inbox()['due_count'], 2)
        self.assertEqual(self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])['phase'], 'expired')
        self.assertEqual(self.memories(), before)

    def test_atomic_initial_policy_and_shared_paper_preserve_point_ownership(self):
        self.learner_id = self.service().learner_id
        self.scope = '?topic_id=atomic-english&subject_id=english'
        self.finish()
        state = self.call('GET', '/api/state' + self.scope)
        self.assertEqual(state['learned_count'], 3)
        self.assertTrue(all(list(x['ratings'].values()) == [None] for x in state['history']))
        self.when += timedelta(hours=7)
        self.app.state.review_paper_types = [{'id': 'atomic-triple', 'title': '原子三题卷', 'subject': 'english',
            'enabled': True, 'requirements': {'text': 3}}]
        inbox = self.call('GET', '/api/review-papers/inbox?subject_id=english')
        self.assertEqual(inbox['due_count'], 3)
        candidate = next(x for x in inbox['items'] if x['kind'] == 'paper')
        paper = self.call('POST', '/api/review-papers/open?subject_id=english', {'candidate_id': candidate['id'], 'request_id': str(uuid.uuid4())})
        for item in paper['items']:
            answer = next(value for local, value in [('a', '红色'), ('b', '蓝色'), ('c', '圆形')] if local in item['html'])
            paper = self.call('POST', '/api/review-papers/draft', {'paper_id': paper['id'], 'expected_revision': paper['revision'],
                'answers': [{'index': item['index'], 'answer': answer}]})
        result, _ = self.submit_paper(paper)
        self.assertTrue(all(x['result']['correct'] for x in result['items']), result)
        self.assertTrue(all(list(x['result']['ratings'].values()) == [3] for x in result['items']))
        updated = self.call('GET', '/api/state' + self.scope)
        self.assertEqual(len(updated['history']), len(state['history']) + 3)
        self.assertEqual(updated['learned_count'], 3)

    def test_multi_point_card_uses_the_same_paper_grading_history_and_reservations(self):
        from graspmemoedu.question_types import demo_answer
        self.learner_id = self.service().learner_id
        self.scope = '?topic_id=atomic-english&subject_id=english'
        initial, backend = self.finish()
        self.when += timedelta(hours=7)
        self.app.state.review_paper_types = [{'id': 'linked-cards', 'title': '关联卡片卷',
            'subject': 'english', 'enabled': True, 'requirements': {'fill_blank': 1, 'text': 1}}]
        inbox = self.call('GET', '/api/review-papers/inbox?subject_id=english')
        candidate = next(x for x in inbox['items'] if x['kind'] == 'paper')
        self.assertEqual((candidate['count'], candidate['point_count']), (2, 3))
        paper = self.call('POST', '/api/review-papers/open?subject_id=english',
                          {'candidate_id': candidate['id'], 'request_id': str(uuid.uuid4())})
        linked = [p for item in paper['items'] for p in item['point_ids']]
        self.assertEqual(len(linked), len(set(linked)))
        self.assertEqual(self.call('GET', '/api/review-papers/inbox?subject_id=english')['due_count'], 0)
        for item in paper['items']:
            qid = item['content_context']['question_id']
            self.assertIsNone(item['explanation_html'])
            paper = self.call('POST', '/api/review-papers/draft', {'paper_id': paper['id'],
                'expected_revision': paper['revision'], 'answers': [
                    {'index': item['index'], 'answer': demo_answer(backend.questions[qid]), 'elapsed_ms': 1000}]})
        result, payload = self.submit_paper(paper)
        self.assertEqual([len(i['result']['ratings']) for i in result['items']], [2, 1])
        self.assertTrue(all(set(i['result']['ratings']) == set(i['point_ids']) for i in result['items']))
        self.assertTrue(all(set(i['result']['ratings'].values()) == {3} for i in result['items']))
        state = self.call('GET', '/api/state' + self.scope)
        self.assertEqual(len(state['history']), len(initial['history']) + 2)
        self.assertEqual(state['history'][-2]['card_id'], 'ab-bundle')
        self.assertEqual(len(state['history'][-2]['point_ids']), 2)
        replay = self.call('POST', '/api/review-papers/submit', payload)
        self.assertEqual(replay['items'], result['items'])
        self.assertEqual(self.call('GET', '/api/state' + self.scope)['history'], state['history'])
        with closing(backend._connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM review_reservations').fetchone()[0], 0)

    def test_backup_retains_papers_drafts_registry_and_retry_state(self):
        self.prime(); paper = self.draft(self.open_paper(), 0)
        archive = self.root / 'paper-backup.zip'; backup_site(self.settings, archive)
        target = self.root / 'restored'; restore_site(archive, target)
        with closing(sqlite3.connect(target / 'learning.sqlite3')) as con:
            saved = con.execute('SELECT items FROM review_papers WHERE id=?', (paper['id'],)).fetchone()
            self.assertEqual(json.loads(saved[0])[0]['answer'], paper['items'][0]['answer'])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_points').fetchone()[0], 2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_reservations').fetchone()[0], 2)

    def test_variant_rotation_shares_memory_and_source_version(self):
        self.prime(); backend = self.service()
        pid = point_id('training', self.bank['id'], self.bank['version'], 'choice')
        original = self.bank['groups'][1]['questions'][3]
        variant = deepcopy(original); variant.update(id='choice-variant', title='同点变式')
        with backend.transaction() as con:
            identifier = attach_training_variant(con, pid, variant, self.when.isoformat())
            self.assertEqual(attach_training_variant(con, pid, variant, self.when.isoformat()), identifier)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_variants').fetchone()[0], 1)
        first = self.open_paper()
        self.assertEqual(first['items'][0]['title'], '同点变式')
        self.call('POST', '/api/review-papers/cancel', {'paper_id': first['id']})
        original_review = self.open_paper()
        self.assertEqual(original_review['items'][0]['title'], original['title'])
        self.call('POST', '/api/review-papers/cancel', {'paper_id': original_review['id']})
        second = self.open_paper()
        self.assertEqual(second['items'][0]['title'], '同点变式')
        self.assertEqual(second['items'][0]['content_context'], first['items'][0]['content_context'])
        self.assertEqual(second['items'][0]['content_version'], identifier)
        self.assertEqual(first['items'][0]['point_ids'], second['items'][0]['point_ids'])
        before = self.memories(); result, _ = self.submit_paper(self.draft(second))
        self.assertEqual(len(self.memories()), len(before))
        self.assertTrue(result['items'][0]['result']['scheduled'])

    def test_cross_identity_subject_and_downgrade_are_enforced(self):
        self.account(is_advanced_learner=True); self.prime()
        self.call('POST', '/api/review-papers/preferences', {'mode': 'question'})
        paper = self.draft(self.open_paper(), 0)
        self.call('GET', '/api/review-papers/state?subject_id=physics&paper_id=' + paper['id'], status=403)
        learner = self.service().learner_id
        with self.app.state.access_store._transaction() as con:
            con.execute('UPDATE access_principals SET is_advanced_learner=0 WHERE learner_id=?', (learner,))
        self.assertEqual(self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])['mode'], 'paper')
        self.call('POST', '/api/review-papers/submit', {'paper_id': paper['id'], 'request_id': str(uuid.uuid4()),
            'expected_revision': paper['revision'], 'index': 0}, 403)
        self.account()
        self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'], status=404)

    def test_unlearned_notebook_questions_form_papers_without_fsrs_then_initialize_once(self):
        backend = self.service()
        for qid in ('choice', 'blank'):
            attempt = backend.open(self.bank['id'], 'group-a', qid, str(uuid.uuid4()))
            backend.add_to_notebook(attempt)
        self.app.state.review_paper_types = [{'id': 'small', 'title': '待学题卷', 'subject': 'math',
            'enabled': True, 'requirements': {'choice': 1, 'fill_blank': 1}}]
        before = self.memories()
        self.assertTrue(all(m['card_json'] is None and m['revision'] == 0 for m in before))
        self.assertEqual(self.inbox()['due_count'], 2)
        paper = self.open_paper()
        self.assertEqual(self.memories(), before)
        result, payload = self.submit_paper(self.draft(paper))
        self.assertTrue(all(m['card_json'] is not None and m['revision'] == 1 for m in self.memories()))
        self.assertTrue(all(item['result']['rating'] == 3 for item in result['items']))
        saved = self.memories()
        self.call('POST', '/api/review-papers/submit', payload)
        self.assertEqual(saved, self.memories())
        self.assertEqual(self.inbox()['due_count'], 0)

    def test_history_permissions_and_public_boundary(self):
        self.account(features=['learn', 'submit_answer']); self.prime()
        first, _ = self.submit_paper(self.draft(self.open_paper()))
        self.when += timedelta(days=30)
        second = self.open_paper()
        self.call('GET', '/api/review-papers/state?paper_id=' + first['id'], status=403)
        self.assertEqual(self.call('GET', '/api/review-papers/state?paper_id=' + second['id'])['phase'], 'answer')
        response = self.client.get('/api/review-papers/inbox?subject_id=math', headers={'Origin': 'https://site.test'})
        self.assertEqual(response.status_code, 401)
        response = self.client.post('/api/review-papers/preferences', json={'mode': 'paper'},
                                   headers={**self.headers, 'Origin': 'https://other.test'})
        self.assertEqual(response.status_code, 403)
        self.call('POST', '/api/review-papers/import', {}, status=403)


class SelectionTests(unittest.TestCase):
    def test_linked_set_matching_agrees_with_exhaustive_small_catalogs(self):
        from collections import Counter
        from itertools import combinations
        import random
        rng = random.Random(413)
        for _ in range(150):
            cards = [{'id': str(i), 'point_ids': rng.sample(list('abcdef'), rng.randint(1, 3)),
                      'question': {'interaction': {'type': rng.choice(['text', 'fill_blank'])}}}
                     for i in range(9)]
            needed = {'text': rng.randint(1, 2), 'fill_blank': rng.randint(1, 2)}
            def fits(group):
                points = [p for c in group for p in c['point_ids']]
                return (Counter(c['question']['interaction']['type'] for c in group) == needed
                        and len(points) == len(set(points)))
            possible = any(fits(group) for group in combinations(cards, sum(needed.values())))
            chosen = assemble(cards, needed)
            self.assertEqual(chosen is not None, possible, (cards, needed))
            if chosen is not None:
                self.assertTrue(fits(chosen))
        dense = [{'id': f'{a}-{b}', 'point_ids': [str(a), str(b)],
                  'question': {'interaction': {'type': 'text'}}}
                 for a in range(20) for b in range(a + 1, 20)]
        self.assertIsNone(assemble(dense, {'text': 60}))

    def test_card_identity_and_origin_do_not_decide_single_or_multiple_points(self):
        from graspmemoedu.review_cards import eligible_presentations
        card = {'id': 'unchanged-card', 'title': '打包变式', 'origin': 'generated',
                'point_ids': ['a', 'b'], 'question': {'interaction': {'type': 'text'}}}
        self.assertIsNone(assemble([card], {'text': 2}))
        self.assertEqual(assemble([card], {'text': 1}), [card])
        self.assertEqual(eligible_presentations([card], {'a'}, {}), [])
        card['point_ids'] = ['a']
        self.assertEqual(assemble([card], {'text': 1}), [card])
        self.assertEqual(eligible_presentations([card], {'a'}, {}, singles=True), [card])
        card['point_ids'].append('b')
        self.assertEqual(eligible_presentations([card], {'a', 'b'}, {}), [card])
        self.assertEqual(eligible_presentations([card], {'a', 'b'}, {}, singles=True), [])

    def test_multi_point_matching_backtracks_and_never_reuses_any_linked_point(self):
        def card(ident, points, kind):
            return {'id': ident, 'point_ids': points, 'question': {'interaction': {'type': kind}}}
        cards = [card('ab', ['a', 'b'], 'text'), card('bc', ['b', 'c'], 'text'),
                 card('ad', ['a', 'd'], 'choice')]
        selected = assemble(cards, {'text': 1, 'choice': 1})
        self.assertEqual([c['id'] for c in selected], ['bc', 'ad'])
        self.assertIsNone(assemble(cards[:1] + cards[2:], {'text': 1, 'choice': 1}))

    def test_templates_and_matching_do_not_count_variants_as_extra_points(self):
        self.assertEqual([sum(t['requirements'].values()) for t in load_paper_types()], [8, 3, 3, 11, 14])
        def card(pid, kind):
            return {'id': pid + kind, 'point_ids': [pid], 'question': {'interaction': {'type': kind}}}
        cards = [card('a', 'choice'), card('a', 'fill_blank'), card('b', 'choice')]
        selected = assemble(cards, {'choice': 1, 'fill_blank': 1})
        self.assertEqual([x['point_ids'] for x in selected], [['b'], ['a']])
        self.assertIsNone(assemble(cards[:2], {'choice': 1, 'fill_blank': 1}))
        self.assertEqual(point_id('training', 'bank', 'v1', 'q'), point_id('training', 'bank', 'v1', 'q'))
