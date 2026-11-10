"""Progression, scheduling, visibility and legacy attempt preservation."""
from copy import deepcopy
from datetime import datetime, timedelta
from contextlib import closing
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_training as training_tests
import test_review_papers as paper_tests
import test_review_timing as timing_tests
import test_linked_points as linked_tests
from graspmemoedu import multi_step
from graspmemoedu.multi_step_import import split_numbered, derive
from graspmemoedu.reference_answers import reference_fields
from graspmemoedu.service import DomainError


def fixture(reproduction=False):
    value = {'schema': 1, 'context': '共同条件', 'context_html': '<p>共同条件</p>', 'review_policy': 'whole',
        'steps': [{'id': f'step-{i}', 'label': f'({i})', 'question': f'求第{i}个值。',
                   'html': f'<p>只在第{i}步出现的题面</p>', 'reference_answer': '2', 'explanation_html': '<p>原解答</p>',
                   'interaction': {'version': 1, 'type': 'text', 'grading': 'exact', 'answers': ['2']}}
                  for i in range(1, 4)]}
    if reproduction:
        for i, step in enumerate(value['steps'][:2], 1):
            step['interaction'] = {'version': 1, 'type': 'text', 'grading': 'semantic'}
            step['reference_answer'] = f'第{i}步参考答案：2'
            step['explanation_html'] = f'<p>仅第{i}步的解析</p>'
        first = value['steps'][0]
        first['question'] += '{{blank:value}}'
        first['html'] = '<p>第一步：{{blank:value}}</p>'
        first['interaction'] = {'version': 1, 'type': 'fill_blank', 'grading': 'semantic',
                                'blanks': [{'id': 'value', 'answers': ['第1步参考答案：2']}]}
    multi_step.validate({'multi_step': value})
    return value


class MultiStepTests(unittest.TestCase):
    setUp = training_tests.TrainingTests.setUp
    open = training_tests.TrainingTests.open
    memory = training_tests.TrainingTests.memory
    def multi(self, reproduction=False):
        bank = self.catalog.banks[self.bank['id']]
        q = next(q for g in bank['groups'] for q in g['questions'] if q['id'] == 'essay')
        q['multi_step'] = fixture(reproduction)
        return self.open()

    def test_show_only_current_reproduction_step_then_require_a_fresh_reveal(self):
        attempt = self.multi(reproduction=True)
        self.assertTrue(self.service.state(attempt, self_rating=True)['can_self_rate'])
        self.service.reveal(attempt, 'step-1')
        shown = self.service.state(attempt, self_rating=True)
        self.assertEqual(shown['phase'], 'shown')
        self.assertEqual(shown['multi_step']['current_step_id'], 'step-1')
        self.assertIn('第1步参考答案：2', shown['reference_answer'])
        self.assertEqual(shown['explanation_html'], '<p>仅第1步的解析</p>')
        self.assertNotIn('第2步', json.dumps(shown, ensure_ascii=False))
        self.assertFalse(shown['multi_step']['finished'])
        self.assertIsNone(self.service.state(attempt, self_rating=False)['explanation_html'])
        self.assertNotIn('reference_answer', self.service.state(attempt, self_rating=False))
        self.service.reveal(attempt, 'step-1')
        self.assertEqual(self.service.state(attempt, self_rating=True), shown)
        with self.assertRaises(DomainError): self.service.stop_reviews(attempt)
        self.service.rate(attempt, 5, 1200, 'step-1')
        next_step = self.service.state(attempt, self_rating=True)
        self.assertEqual(next_step['phase'], 'answer')
        self.assertEqual(next_step['multi_step']['current_step_id'], 'step-2')
        self.assertIsNone(next_step['explanation_html'])
        self.assertNotIn('reference_answer', next_step)
        self.assertIsNone(self.memory())
        self.service.rate(attempt, 5, 1200, 'step-1')
        self.assertEqual(self.service.state(attempt, self_rating=True), next_step)
        with self.assertRaises(DomainError): self.service.rate(attempt, 2, step_id='step-1')
        with self.assertRaises(DomainError): self.service.reveal(attempt, 'step-1')
        with self.assertRaises(DomainError): self.service.rate(attempt, 5, step_id='step-2')

    def test_mixed_self_rating_and_judging_retires_whole_once(self):
        attempt = self.multi(reproduction=True)
        for i, rating in ((1, 5), (2, 5)):
            self.service.reveal(attempt, f'step-{i}')
            self.service.rate(attempt, rating, 1500, f'step-{i}')
        self.assertFalse(self.service.state(attempt, self_rating=True)['can_self_rate'])
        with self.assertRaises(DomainError): self.service.reveal(attempt, 'step-3')
        with patch('graspmemoedu.training.judge_answer', return_value=(True, '正确')) as judge:
            state, _ = self.step(attempt, 3)
        context = judge.call_args.args[1]['question']
        self.assertIn('第1步参考答案：2', context)
        self.assertIn('第2步参考答案：2', context)
        self.assertEqual(state['phase'], 'done')
        self.assertTrue(state['result']['correct'])
        self.assertEqual(state['result']['self_rating'], 5)
        self.assertEqual(state['result']['rating'], 5)
        self.assertFalse(state['result']['scheduled']); self.assertTrue(state['result']['stopped'])
        self.assertEqual(self.memory()['revision'], 1)
        self.service.rate(attempt, 5, 1500, 'step-2')
        with closing(self.service._connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM training_ratings WHERE attempt=?', (attempt,)).fetchone()[0], 1)

    def test_self_rated_failure_reveals_whole_and_reviews_from_first_step(self):
        attempt = self.multi(reproduction=True)
        self.service.reveal(attempt, 'step-1')
        self.service.rate(attempt, 5, step_id='step-1')
        self.service.reveal(attempt, 'step-2')
        self.service.rate(attempt, 1, step_id='step-2')
        state = self.service.state(attempt, self_rating=True)
        self.assertEqual(state['phase'], 'done')
        self.assertFalse(state['result']['correct'])
        self.assertEqual(state['result']['rating'], 1)
        self.assertEqual(len(state['multi_step']['steps']), 3)
        self.assertEqual(state['result']['multi_step']['first_failed_step_id'], 'step-2')
        self.assertTrue(state['explanation_html'])
        self.assertEqual(self.service.state(self.open())['multi_step']['current_step_id'], 'step-1')

    def test_judged_failure_after_self_rating_overrides_successful_step_rating(self):
        attempt = self.multi(reproduction=True)
        self.service.reveal(attempt, 'step-1')
        self.service.rate(attempt, 5, step_id='step-1')
        state, _ = self.step(attempt, 2, 'wrong')
        self.assertFalse(state['result']['correct'])
        self.assertEqual(state['result']['self_rating'], 1)
        self.assertEqual(state['result']['rating'], 1)

    def test_step_identity_required_and_pending_judge_cannot_be_self_rated(self):
        attempt = self.multi(reproduction=True)
        for step_id in (None, 'step-2', 'unknown'):
            with self.assertRaises(DomainError): self.service.reveal(attempt, step_id)
        ident = self.service.submit({'attempt_id': attempt, 'request_id': 'pending', 'step_id': 'step-1',
                                     'answer': '{"value":"2"}'})
        with self.assertRaises(DomainError): self.service.reveal(attempt, 'step-1')
        with self.assertRaises(DomainError): self.service.rate(attempt, 5, step_id='step-1')
        self.service.process_submission(ident)

    def test_binary_feedback_and_completed_reference_survive_reload(self):
        attempt = self.multi(reproduction=True)
        self.service.reveal(attempt, 'step-1')
        for value in (2, 3, 4, True):
            with self.assertRaises(DomainError): self.service.rate(attempt, value, step_id='step-1')
        self.service.rate(attempt, 5, step_id='step-1')
        state = self.service.state(attempt, self_rating=True)
        first = state['multi_step']['steps'][0]
        self.assertIn('第1步参考答案：2', first['reference_answer'])
        self.assertEqual(first['result']['answer_display'], '自主判定：做对')
        self.assertNotIn('第2步参考答案', json.dumps(state, ensure_ascii=False))
        self.assertIsNone(state['result']); self.assertIsNone(self.memory())

    def test_all_judged_steps_retire_existing_review_and_do_not_count_as_self_rating(self):
        attempt = self.multi(); self.service.add_to_notebook(attempt)
        self.step(attempt, 1, 'wrong')
        self.now = datetime.fromisoformat(self.memory()['due_at']) + timedelta(seconds=1)
        attempt = self.open()
        self.assertEqual(self.service.state(attempt)['mode'], 'review')
        for index in (1, 2, 3): state, _ = self.step(attempt, index)
        self.assertEqual(state['result']['rating'], 5)
        self.assertIsNone(state['result']['self_rating'])
        self.assertFalse(state['result']['scheduled']); self.assertTrue(state['stopped'])
        self.assertEqual(self.service.reviews([self.bank])['items'], [])

    def test_all_correct_early_practice_retires_but_wrong_practice_keeps_normal_policy(self):
        attempt = self.multi(); self.service.add_to_notebook(attempt)
        self.step(attempt, 1, 'wrong')
        saved = self.memory()
        early = self.open(); self.assertEqual(self.service.state(early)['mode'], 'practice')
        failed, _ = self.step(early, 1, 'wrong')
        self.assertFalse(failed['result']['correct']); self.assertEqual(self.memory(), saved)
        early = self.open()
        for index in (1, 2, 3): state, _ = self.step(early, index)
        self.assertEqual(state['result']['rating'], 5); self.assertTrue(state['stopped'])
        self.assertEqual(self.memory()['revision'], saved['revision'] + 1)
        saved = self.memory()
        repeated = self.open()
        for index in (1, 2, 3): state, _ = self.step(repeated, index)
        self.assertEqual(state['result']['rating'], 5); self.assertEqual(self.memory(), saved)

    def test_late_success_does_not_overwrite_newer_wrong_result(self):
        attempt = self.multi()
        self.step(attempt, 1); self.step(attempt, 2)
        with self.service.transaction() as con:
            row = self.service._attempt(con, attempt)
            # A concurrent round changes the memory revision while this judge is in flight.
            con.execute('INSERT INTO training_memory VALUES (?,?,?,?,?,?,?,?)',
                (self.service.learner_id, row['bank'], row['question'], row['version'], 0, 1, None, self.now.isoformat()))
        saved = self.memory()
        state, _ = self.step(attempt, 3)
        self.assertFalse(state['stopped']); self.assertTrue(state['result']['practice_only'])
        self.assertIsNone(state['result']['rating']); self.assertEqual(self.memory(), saved)

    def step(self, attempt, index, answer='2', request=None):
        data = {'attempt_id': attempt, 'request_id': request or f'{attempt}-{index}',
                'step_id': f'step-{index}', 'answer': answer, 'elapsed_ms': 1000}
        ident = self.service.submit(data); self.service.process_submission(ident)
        return self.service.state(attempt), data

    def test_unlocks_only_after_server_verdict_and_schedules_once(self):
        attempt = self.multi()
        state = self.service.state(attempt)
        self.assertEqual(len(state['multi_step']['steps']), 1)
        self.assertNotIn('第2步', json.dumps(state, ensure_ascii=False))
        self.assertNotIn('answers', json.dumps(state))
        with self.assertRaises(DomainError): self.step(attempt, 2)
        state, data = self.step(attempt, 1)
        self.assertEqual(state['phase'], 'answer'); self.assertIsNone(state['submission_id'])
        self.assertEqual(state['multi_step']['current_step_id'], 'step-2')
        self.assertIsNone(self.memory())
        self.service.submit(data)  # Replay after advancing does not grade the next step.
        self.assertEqual(self.service.state(attempt)['multi_step']['current_step_id'], 'step-2')
        self.step(attempt, 2)
        state, _ = self.step(attempt, 3)
        self.assertEqual(state['phase'], 'done'); self.assertTrue(state['result']['correct'])
        self.assertEqual(state['result']['rating'], 5)
        with closing(self.service._connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM training_ratings WHERE attempt=?',(attempt,)).fetchone()[0], 1)

    def test_first_error_reveals_all_and_records_failed_step_but_reviews_whole(self):
        attempt = self.multi(); self.step(attempt, 1)
        state, data = self.step(attempt, 2, 'wrong')
        self.assertEqual(len(state['multi_step']['steps']), 3)
        self.assertTrue(state['explanation_html']); self.assertEqual(state['phase'], 'done')
        self.assertFalse(state['result']['correct']); self.assertEqual(state['result']['rating'], 1)
        self.assertEqual(state['result']['multi_step']['first_failed_step_id'], 'step-2')
        self.assertEqual(state['result']['multi_step']['start_step'], 0)
        self.assertEqual(self.service.state(self.open())['multi_step']['current_index'], 0)
        with self.assertRaises(DomainError): self.step(attempt, 3)

    def test_judge_error_does_not_advance_and_frozen_old_attempt_stays_whole(self):
        old = self.open(); attempt = self.multi()
        self.assertNotIn('multi_step', self.service.state(old))
        with patch('graspmemoedu.training.judge_answer', return_value=(None, '暂时失败')):
            state, data = self.step(attempt, 1)
        self.assertEqual(state['multi_step']['current_step_id'], 'step-1')
        self.assertEqual(state['submission_status'], 'error'); self.assertIsNone(self.memory())
        self.service.submit(data); self.service.process_submission(data['request_id'])
        self.assertEqual(self.service.state(attempt)['multi_step']['current_step_id'], 'step-2')


class MultiStepLinkedTests(unittest.TestCase):
    setUp = linked_tests.LinkedPointsTests.setUp
    seed = linked_tests.LinkedPointsTests.seed

    def test_judged_linked_success_retires_points_without_becoming_self_assessment(self):
        self.seed()
        question = self.banks.banks['linked-bank']['groups'][0]['questions'][0]
        question['multi_step'] = fixture()
        question.pop('assessment', None)
        question['interaction'] = {'version':1, 'type':'text', 'grading':'semantic'}
        attempt = self.training.open('linked-bank', 'goal-group', 'goal', 'multi-linked')
        with patch('graspmemoedu.training.judge_answer', return_value=(True, '正确')):
            for index in (1, 2, 3):
                ident = self.training.submit({'attempt_id':attempt, 'request_id':f'multi-{index}',
                    'step_id':f'step-{index}', 'answer':'2'})
                self.training.process_submission(ident)
        result = self.training.state(attempt)['result']
        self.assertEqual(result['rating'], 5); self.assertTrue(result['stopped'])
        self.assertIsNone(result['self_rating']); self.assertFalse(result['stop_requested'])
        self.assertTrue(all(r == 5 for r in result['ratings'].values()))
        for tid, backend in self.atoms.items():
            with backend._transaction() as con:
                state = backend._load(con)
                row = next(r for r in state['history'] if r['id'] == attempt)
                self.assertNotIn('self_assessment', row)
                self.assertTrue(row['stopped'])


class MultiStepPaperTests(unittest.TestCase):
    setUp = paper_tests.PaperTests.setUp
    call = paper_tests.PaperTests.call
    account = paper_tests.PaperTests.account
    service = paper_tests.PaperTests.service
    prime = paper_tests.PaperTests.prime
    inbox = paper_tests.PaperTests.inbox
    open_paper = paper_tests.PaperTests.open_paper
    submit_paper = paper_tests.PaperTests.submit_paper
    memories = paper_tests.PaperTests.memories
    draft = paper_tests.PaperTests.draft

    advance_active = timing_tests.TimedPaperTests.advance_active
    settled = timing_tests.TimedPaperTests.settled

    def open_multi(self, timed=False, reproduction=False):
        self.prime()
        if timed:
            for template in self.app.state.review_paper_types: template['time_limit_minutes'] = 1
        for group in self.app.state.training_catalog.banks[self.bank['id']]['groups']:
            for question in group['questions']:
                if question['id'] == 'blank': question['multi_step'] = fixture(reproduction)
        return self.open_paper()

    def reveal_step(self, paper, step, status=200):
        item = next(i for i in paper['items'] if i.get('multi_step'))
        return self.call('POST', '/api/review-papers/reveal', {'paper_id': paper['id'], 'index': item['index'],
            'expected_revision': paper['revision'], 'step_id': f'step-{step}'}, status)

    def rate_step(self, paper, step, rating):
        import uuid
        item = next(i for i in paper['items'] if i.get('multi_step'))
        paper = self.call('POST', '/api/review-papers/draft', {'paper_id': paper['id'],
            'expected_revision': paper['revision'], 'answers': [{'index': item['index'], 'step_id': f'step-{step}',
            'answer': f'自主评分：{rating}', 'self_rating': rating, 'elapsed_ms': step * 2000}]})
        return self.submit_paper(paper, expected='answer', payload={'paper_id': paper['id'],
            'expected_revision': paper['revision'], 'request_id': str(uuid.uuid4()),
            'index': item['index'], 'step_id': f'step-{step}'})

    def test_step_reveal_in_paper_mode_and_mixed_completion(self):
        self.account(is_advanced_learner=True)
        paper = self.open_multi(reproduction=True); before = self.memories()
        item = next(i for i in paper['items'] if i.get('multi_step')); index = item['index']
        self.assertTrue(item['can_self_rate'])
        paper = self.reveal_step(paper, 1)
        item = paper['items'][index]
        self.assertEqual(item['explanation_html'], '<p>仅第1步的解析</p>')
        self.assertIn('第1步参考答案：2', item['reference_answer'])
        self.assertNotIn('第2步', json.dumps(item, ensure_ascii=False))
        self.assertFalse(item['can_stop_reviews'])
        resumed = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
        self.assertEqual(resumed['items'][index], item)
        paper, replay = self.rate_step(resumed, 1, 5)
        item = paper['items'][index]
        self.assertEqual(item['multi_step']['current_step_id'], 'step-2')
        self.assertFalse(item['revealed']); self.assertIsNone(item['self_rating'])
        self.assertNotIn('reference_answer', item); self.assertIsNone(item['explanation_html'])
        self.assertEqual(self.memories(), before)
        self.assertEqual(self.call('POST', '/api/review-papers/submit', replay)['items'], paper['items'])
        self.reveal_step(paper, 1, 409)
        paper = self.reveal_step(paper, 2)
        paper, _ = self.rate_step(paper, 2, 5)
        self.reveal_step(paper, 3, 403)
        paper, _ = self.submit_step(paper, 3)
        result = paper['items'][index]['result']
        self.assertTrue(result['correct']); self.assertEqual(result['self_rating'], 5)
        self.assertEqual(result['rating'], 5)
        self.assertEqual(result['multi_step']['mode'], 'whole')
        self.assertEqual(sum(after['revision'] - old['revision'] for after, old in zip(self.memories(), before)), 1)
        native = self.service().state(f'{paper["id"]}:{index}')
        self.assertEqual(native['result']['self_rating'], 5)
        self.assertEqual(native['result']['multi_step']['mode'], 'whole')

    def test_question_mode_self_rating_failure_reveals_all(self):
        self.account(is_advanced_learner=True)
        self.call('POST', '/api/review-papers/preferences', {'mode': 'question'})
        paper = self.open_multi(reproduction=True)
        paper = self.reveal_step(paper, 1)
        paper, _ = self.rate_step(paper, 1, 1)
        item = next(i for i in paper['items'] if i.get('multi_step'))
        self.assertEqual(len(item['multi_step']['steps']), 3)
        self.assertFalse(item['result']['correct'])
        self.assertEqual(item['result']['multi_step']['first_failed_step_id'], 'step-1')
        self.assertTrue(item['explanation_html'])

    def test_reveal_permissions_and_ratings_cannot_bypass_current_step(self):
        paper = self.open_multi(reproduction=True)
        self.reveal_step(paper, 1, 403)
        for group in self.app.state.training_catalog.banks[self.bank['id']]['groups']:
            for question in group['questions']: question.pop('multi_step', None)
        self.account(is_advanced_learner=True)
        paper = self.open_multi(reproduction=True)
        item = next(i for i in paper['items'] if i.get('multi_step')); index = item['index']
        self.call('POST', '/api/review-papers/draft', {'paper_id': paper['id'], 'expected_revision': paper['revision'],
            'answers': [{'index': index, 'step_id': 'step-1', 'answer': '自主评分：3', 'self_rating': 3}]}, 403)
        paper = self.reveal_step(paper, 1)
        for step_id, rating, status in (('step-2', 5, 409), ('step-1', 3, 403)):
            self.call('POST', '/api/review-papers/draft', {'paper_id': paper['id'], 'expected_revision': paper['revision'],
                'answers': [{'index': index, 'step_id': step_id, 'answer': '自主评分', 'self_rating': rating}]}, status)
        self.call('POST', '/api/training/preferences', {'self_rating_enabled': False})
        hidden = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])['items'][index]
        self.assertFalse(hidden['revealed']); self.assertNotIn('reference_answer', hidden)
        self.assertIsNone(hidden['explanation_html'])
        self.reveal_step(paper, 1, 403)

    def test_timeout_after_reveal_records_unfinished_step_as_wrong(self):
        self.account(is_advanced_learner=True)
        paper = self.open_multi(timed=True, reproduction=True)
        self.active_paper = paper['id']
        paper = self.call('POST', '/api/review-papers/activity', {'paper_id': paper['id'], 'session_id':'timing-test', 'active':True})
        paper = self.reveal_step(paper, 1)
        paper, _ = self.rate_step(paper, 1, 5)
        paper = self.reveal_step(paper, 2)
        self.advance_active(60)
        final = self.settled(paper)
        item = next(i for i in final['items'] if i.get('multi_step'))
        self.assertEqual(final['phase'], 'done'); self.assertFalse(item['result']['correct'])
        self.assertEqual(item['result']['rating'], 1)
        self.assertEqual(item['result']['multi_step']['first_failed_step_id'], 'step-2')

    def test_training_api_reproduction_preview_and_step_identity(self):
        import uuid
        self.account(is_advanced_learner=True)
        question = next(q for g in self.app.state.training_catalog.banks[self.bank['id']]['groups'] for q in g['questions'] if q['id'] == 'essay')
        question['multi_step'] = fixture(reproduction=True)
        reader = self.call('GET', f'/api/training/group?bank_id={self.bank["id"]}&group_id=group-a&reader=true')
        preview = reader['reader_states']['essay']
        self.assertTrue(preview['can_self_rate']); self.assertTrue(preview['preview'])
        with closing(self.service()._connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM training_attempts').fetchone()[0], 0)
        state = self.call('POST', '/api/training/open', {'bank_id': self.bank['id'], 'group_id': 'group-a',
            'question_id': 'essay', 'request_id': str(uuid.uuid4())})
        action = {'attempt_id': state['attempt_id'], 'step_id': 'step-1'}
        self.call('POST', '/api/training/reveal', {'attempt_id': state['attempt_id']}, 409)
        self.call('POST', '/api/training/rate', {**action, 'rating': 5}, 422)
        shown = self.call('POST', '/api/training/reveal', action)
        self.assertIn('第1步参考答案：2', shown['reference_answer'])
        advanced = self.call('POST', '/api/training/rate', {**action, 'rating': 5})
        self.assertEqual(advanced['multi_step']['current_step_id'], 'step-2')
        self.assertEqual(self.call('POST', '/api/training/rate', {**action, 'rating': 5}), advanced)
        self.call('POST', '/api/training/preferences', {'self_rating_enabled': False})
        self.call('POST', '/api/training/reveal', {**action, 'step_id': 'step-2'}, 403)

    def submit_step(self, paper, number, answer='2'):
        item = next(i for i in paper['items'] if i.get('multi_step'))
        paper = self.call('POST', '/api/review-papers/draft', {'paper_id': paper['id'],
            'expected_revision': paper['revision'], 'answers': [{'index': item['index'],
            'step_id': f'step-{number}', 'answer': answer, 'elapsed_ms': number * 1000}]})
        import uuid
        return self.submit_paper(paper, expected='answer', payload={'paper_id': paper['id'],
            'expected_revision': paper['revision'], 'request_id': str(uuid.uuid4()),
            'index': item['index'], 'step_id': f'step-{number}'})

    def test_whole_paper_supports_step_progress_and_one_final_schedule(self):
        paper = self.open_multi(); before = self.memories()
        item = next(i for i in paper['items'] if i.get('multi_step'))
        self.assertEqual(len(item['multi_step']['steps']), 1)
        self.assertNotIn('第2步', json.dumps(item, ensure_ascii=False))
        paper, payload = self.submit_step(paper, 1)
        self.assertEqual(self.memories(), before)
        self.assertEqual(self.call('POST', '/api/review-papers/submit', payload)['items'], paper['items'])
        resumed = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
        self.assertEqual(resumed['items'][item['index']]['multi_step']['current_step_id'], 'step-2')
        paper, _ = self.submit_step(resumed, 2)
        self.assertEqual(self.memories(), before)
        paper, _ = self.submit_step(paper, 3)
        complete = paper['items'][item['index']]
        self.assertTrue(complete['result']['correct']); self.assertTrue(complete['explanation_html'])
        self.assertEqual(complete['result']['multi_step']['mode'], 'whole')
        self.assertNotEqual(self.memories(), before)
        paper = self.draft(paper, 0)
        final, _ = self.submit_paper(paper)
        self.assertTrue(all(i['result']['correct'] for i in final['items']))

    def test_wrong_step_reveals_remaining_questions_and_keeps_other_question_private(self):
        paper = self.open_multi()
        paper, _ = self.submit_step(paper, 1)
        paper, _ = self.submit_step(paper, 2, 'wrong')
        item = next(i for i in paper['items'] if i.get('multi_step'))
        self.assertFalse(item['result']['correct'])
        self.assertEqual(len(item['multi_step']['steps']), 3)
        self.assertEqual(item['result']['multi_step']['first_failed_step_id'], 'step-2')
        self.assertIsNone(paper['items'][0]['explanation_html'])

    def test_timeout_records_current_failed_step_and_finishes_whole_paper(self):
        paper = self.open_multi(timed=True)
        self.active_paper = paper['id']
        paper = self.call('POST', '/api/review-papers/activity', {'paper_id': paper['id'], 'session_id':'timing-test', 'active':True})
        paper, _ = self.submit_step(paper, 1)
        self.advance_active(60)
        result = self.settled(paper)
        self.assertEqual(result['phase'], 'done')
        item = next(i for i in result['items'] if i.get('multi_step'))
        self.assertEqual(item['result']['multi_step']['first_failed_step_id'], 'step-2')
        self.assertEqual(item['multi_step']['current_index'], 1)
        self.assertEqual(len(item['multi_step']['steps']), 3)

    def test_step_help_resolves_the_exact_original_subquestion(self):
        paper = self.open_multi()
        question = next(q for g in self.app.state.training_catalog.banks[self.bank['id']]['groups'] for q in g['questions'] if q['id']=='blank')
        block = multi_step.source_block_html(question, 'step:step-2:blank')
        self.assertEqual(block, fixture()['steps'][1]['html'])
        self.assertNotIn('第3步', block)


class ImportAndFeedbackTests(unittest.TestCase):
    def test_numbered_math_protection_and_proof_fill_mix(self):
        q = {'id':'fixture', 'question':'矩阵 $A=(1)$。\n(a) 求维数。\n(b) 证明封闭。',
             'reference_answer':'(a) $2$。\n(b) 由原有论证得封闭。', 'explanation':'原完整解答'}
        result = derive(q)
        self.assertEqual([s['interaction']['type'] for s in result['steps']], ['fill_blank','text'])
        self.assertEqual(result['context'], '矩阵 $A=(1)$。')
        self.assertIsNone(split_numbered('函数 $f(x)$ 与 $g(y)$，矩阵 $(1)$、$(2)$。'))

    def test_graphing_stays_text_while_graph_interval_answers_are_fill(self):
        for instruction in ('作出 $f(x)$ 的简略图。', '画出函数图像。', '绘出函数图像。', 'Sketch its graph.'):
            with self.subTest(instruction=instruction):
                q = {'id': 'graph', 'question': '(1) 求图像上凹的区间。\n(2) ' + instruction,
                     'reference_answer': '(1) $(0,1)$。\n(2) 图像经过原点，向右递增。'}
                steps = derive(q)['steps']
                self.assertEqual([s['interaction']['type'] for s in steps], ['fill_blank', 'text'])
                self.assertEqual(steps[1]['question'], instruction)

    def test_unaligned_answers_require_exact_source_quotes(self):
        q = {'id':'fixture', 'question':'(1) 求值。\n(2) 证明。', 'reference_answer':'结果为 2。证明如下。', 'explanation':'原论证。'}
        with self.assertRaises(ValueError): derive(q)
        with self.assertRaises(ValueError): derive(q, {'steps':[{'id':'step-1','answer_quotes':['结果为 3。']}]})

    def test_negative_choice_colours_are_answer_side_only(self):
        q = {'question':'下列说法错误的是？', 'reference_answer':'二', 'interaction':{'version':1,'type':'choice',
             'options':[{'id':'a','text':'一'},{'id':'b','text':'二'}], 'correct_id':'b'}}
        self.assertEqual(reference_fields(q), {})
        self.assertEqual(reference_fields(q, True)['choice_feedback'], {'correct_ids': ['b']})
        q['question'] = '以下说法正确的是？'
        self.assertEqual(reference_fields(q, True)['choice_feedback'], {'correct_ids': ['b']})
