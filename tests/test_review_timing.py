from copy import deepcopy
from datetime import timedelta
import time
import unittest
import uuid

import test_review_papers as paper_tests
from graspmemoedu.review_cards import attach_training_variant, point_id, load_paper_types


class TimedPaperTests(unittest.TestCase):
    setUp = paper_tests.PaperTests.setUp
    call = paper_tests.PaperTests.call
    account = paper_tests.PaperTests.account
    service = paper_tests.PaperTests.service
    prime = paper_tests.PaperTests.prime
    inbox = paper_tests.PaperTests.inbox
    open_paper = paper_tests.PaperTests.open_paper
    draft = paper_tests.PaperTests.draft
    submit_paper = paper_tests.PaperTests.submit_paper
    memories = paper_tests.PaperTests.memories
    finish = paper_tests.PaperTests.finish
    payload = paper_tests.PaperTests.payload

    def timed(self):
        self.prime()
        for template in self.app.state.review_paper_types:
            template['time_limit_minutes'] = 1

    def settled(self, paper):
        for _ in range(200):
            state = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
            if state['phase'] == 'done' or state['job_status'] == 'error':
                return state
            time.sleep(.01)
        self.fail('Timed paper did not settle')

    def test_deadline_uses_saved_answers_marks_empty_wrong_and_is_idempotent(self):
        self.timed()
        paper = self.draft(self.open_paper(), 0)
        deadline = paper['timing']['deadline_at']
        self.when += timedelta(seconds=60)
        state = self.settled(paper)
        self.assertEqual(state['phase'], 'done', state)
        self.assertEqual([i['result']['correct'] for i in state['items']], [True, False])
        self.assertEqual(state['timing']['deadline_at'], deadline)
        self.assertTrue(state['timing']['timed_out'])
        before = self.memories()
        self.assertEqual(self.settled(paper)['items'], state['items'])
        self.assertEqual(self.memories(), before)

    def test_late_draft_cannot_change_answer_or_extend_deadline(self):
        self.timed(); paper = self.open_paper()
        self.when += timedelta(minutes=2)
        late = self.draft(paper)
        self.assertTrue(all(not i['answer'] for i in late['items']))
        state = self.settled(paper)
        self.assertTrue(all(not i['result']['correct'] for i in state['items']))

    def test_mark_only_is_advanced_and_frozen_for_existing_papers(self):
        self.call('POST', '/api/review-papers/preferences', {'timeout_action': 'mark'}, 403)
        self.account(is_advanced_learner=True); self.timed()
        settings = self.call('POST', '/api/review-papers/preferences', {'timeout_action': 'mark'})
        self.assertTrue(settings['can_choose_timeout'])
        paper = self.open_paper()
        self.call('POST', '/api/review-papers/preferences', {'timeout_action': 'submit'})
        self.when += timedelta(minutes=2)
        late = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
        self.assertEqual(late['phase'], 'answer')
        self.assertTrue(late['can_edit']); self.assertTrue(late['timing']['timed_out'])
        self.assertEqual(late['timing']['timeout_action'], 'mark')
        result, _ = self.submit_paper(self.draft(late))
        self.assertTrue(all(i['result']['correct'] for i in result['items']))

    def test_timeout_finishes_remaining_questions_after_individual_submission(self):
        self.account(is_advanced_learner=True); self.timed()
        self.call('POST', '/api/review-papers/preferences', {'mode': 'question'})
        paper = self.draft(self.open_paper(), 0)
        first, _ = self.submit_paper(paper, 0, expected='answer')
        self.when += timedelta(minutes=2)
        done = self.settled(first)
        self.assertEqual(done['items'][0]['result'], first['items'][0]['result'])
        self.assertFalse(done['items'][1]['result']['correct'])

    def test_single_card_stays_untimed_and_limit_is_frozen_after_config_change(self):
        self.timed(); data = self.inbox()
        single = next(i for i in data['items'] if i['kind'] == 'single')
        opened = self.call('POST', '/api/review-papers/open?subject_id=math', {
            'request_id': str(uuid.uuid4()), 'candidate_id': single['id']})
        self.assertIsNone(opened['timing'])
        self.call('POST', '/api/review-papers/cancel', {'paper_id': opened['id']})
        paper = self.open_paper()
        self.app.state.review_paper_types[0]['time_limit_minutes'] = 60
        restored = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
        self.assertEqual(restored['timing']['limit_minutes'], 1)

    def test_same_type_has_disjoint_numbered_papers_and_preserves_active_number(self):
        self.timed(); backend = self.service()
        q = deepcopy(next(q for g in self.bank['groups'] for q in g.get('questions', []) if q['id'] == 'choice'))
        q['id'] = 'blank-choice-variant'
        pid = point_id('training', self.bank['id'], self.bank['version'], 'blank')
        with backend.transaction() as con:
            attach_training_variant(con, pid, q, self.when.isoformat())
        choices = [c for c in self.inbox()['items'] if c.get('paper_type_id') == 'choice-only']
        self.assertEqual([c['paper_number'] for c in choices], [1, 2])
        opened = self.call('POST', '/api/review-papers/open?subject_id=math', {
            'request_id': str(uuid.uuid4()), 'candidate_id': choices[0]['id']})
        remaining = [c for c in self.inbox()['items'] if c.get('paper_type_id') == 'choice-only']
        self.assertEqual([c['paper_number'] for c in remaining], [2])
        self.assertEqual(opened['paper_number'], 1)

    def test_default_names_and_minutes_are_explicit(self):
        templates = load_paper_types()
        self.assertEqual([t['time_limit_minutes'] for t in templates], [20, 12, 12, 32, 45])
        self.assertEqual(templates[0]['title'], '单选专项卷')

    def test_revoked_advanced_qualification_cannot_keep_mark_only(self):
        self.account(is_advanced_learner=True); self.timed()
        self.call('POST', '/api/review-papers/preferences', {'timeout_action': 'mark'})
        paper = self.open_paper()
        learner = self.service().learner_id
        with self.app.state.access_store._transaction() as con:
            con.execute('UPDATE access_principals SET is_advanced_learner=0 WHERE learner_id=?', (learner,))
        self.when += timedelta(minutes=2)
        state = self.settled(paper)
        self.assertEqual(state['timing']['timeout_action'], 'submit')
        self.assertEqual(state['phase'], 'done')

    def test_atomic_paper_timeout_updates_each_point_once(self):
        self.learner_id = self.service().learner_id
        self.scope = '?topic_id=atomic-english&subject_id=english'
        self.finish(); self.when += timedelta(hours=7)
        self.app.state.review_paper_types = [{'id': 'atomic-timed', 'title': '原子限时卷',
            'subject': 'english', 'enabled': True, 'requirements': {'text': 3}, 'time_limit_minutes': 1}]
        inbox = self.call('GET', '/api/review-papers/inbox?subject_id=english')
        self.assertEqual(inbox['trees'][0]['kind'], 'atomic')
        candidate = next(i for i in inbox['items'] if i['kind'] == 'paper')
        paper = self.call('POST', '/api/review-papers/open?subject_id=english', {
            'candidate_id': candidate['id'], 'request_id': str(uuid.uuid4())})
        self.when += timedelta(minutes=2)
        state = self.settled(paper)
        self.assertEqual(state['phase'], 'done', state)
        self.assertEqual([list(i['result']['ratings'].values()) for i in state['items']], [[1], [1], [1]])

    def test_revoked_source_does_not_block_the_rest_of_the_review_directory(self):
        self.account(); self.timed(); paper = self.open_paper()
        self.app.state.access_store.set_permissions(self.service().learner_id, topic_ids=['atomic-english'])
        self.when += timedelta(minutes=2)
        self.assertEqual(self.inbox()['active'], [])
        self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'], status=403)
