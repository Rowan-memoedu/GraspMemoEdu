from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch
import tempfile
import threading
import unittest
import uuid

from fsrs import Card
from math_learning.atomic import AtomicLearningService
from math_learning.atomic_content import validate_atomic
from math_learning.question_types import demo_answer
from math_learning.service import DomainError
from atomic_fixtures import atomic_topic


class AtomicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.when = datetime(2026, 9, 22, 9, tzinfo=timezone.utc)
        self.topic = atomic_topic()
        self.service = AtomicLearningService(self.topic, Path(self.temp.name), clock=lambda: self.when)

    def action(self, **values):
        state = self.service.state()
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **values}

    def advance(self):
        return self.service.advance(self.action())

    def submit(self, answer=None):
        state = self.service.state()
        if answer is None:
            answer = demo_answer(self.service.questions[state['current']['card_id']])
        payload = self.action(answer=answer)
        result = self.service.submit(payload)
        self.service.process_submission(result['submission_id'])
        return payload, self.service.submission(result['submission_id'])

    def finish_initial(self):
        self.service.choose(self.action(choice='learn'))
        for _ in range(30):
            state = self.service.state()
            if state['status'] == 'completed':
                return
            if state['phase'] == 'answer':
                self.submit()
            else:
                if state['phase'] == 'waiting':
                    self.when = datetime.fromisoformat(state['next_available_at'])
                self.advance()
        self.fail('Initial learning did not complete')

    def raw(self):
        with closing(self.service._connect()) as con:
            return self.service._load(con)

    def test_contract_rejects_wrong_subject_anchor_and_missing_single_card(self):
        validate_atomic(self.topic)
        for mutate in (lambda t: t.update(subject_id='math'),
                       lambda t: t['memory_chunks'][0]['anchors'][0].update(quote='wrong'),
                       lambda t: t.update(flashcards=[c for c in t['flashcards'] if c['point_ids'] != ['a']])):
            topic = atomic_topic(); mutate(topic)
            with self.assertRaises(ValueError):
                validate_atomic(topic)

    def test_initial_correct_is_unrated_and_memory_only_starts_on_review(self):
        self.finish_initial()
        state = self.raw()
        self.assertTrue(all(p['card_json'] is None for p in state['points'].values()))
        self.assertTrue(all(p['mastered'] for p in state['points'].values()))
        self.assertTrue(all(r['ratings'] == {r['point_ids'][0]: None} for r in state['history']))
        self.assertIsNotNone(self.service.state()['introduction'])
        for memory in state['points'].values():
            self.assertEqual(datetime.fromisoformat(memory['due_at']) - datetime.fromisoformat(memory['first_passed_at']), timedelta(hours=6))
        self.when += timedelta(hours=6, minutes=1)
        self.service.choose(self.action(choice='review'))
        self.assertEqual(self.service.state()['current']['card_id'], 'ab-bundle')
        payload, result = self.submit()
        self.assertEqual(result['state']['current']['ratings'], {'a': 3, 'b': 3})
        memories = self.raw()['points']
        a, b = Card.from_json(memories['a']['card_json']), Card.from_json(memories['b']['card_json'])
        self.assertIsNotNone(a.stability); self.assertIsNotNone(b.difficulty)
        self.assertNotEqual(a.card_id, b.card_id)
        self.assertIsNone(memories['c']['card_json'])
        self.assertEqual(self.service.submit(payload)['status'], 'judged')
        with closing(self.service._connect()) as con:
            logs = [json.loads(r[0]) for r in con.execute('SELECT body FROM events')]
        self.assertEqual(sum(e['type'] == 'atomic_rating' for e in logs), 2)

    def test_interleaving_and_history_are_enforced_after_reload(self):
        self.assertIsNotNone(self.service.state()['introduction'])
        self.service.choose(self.action(choice='learn')); self.advance()
        self.assertIsNone(self.service.state()['introduction'])
        old, _ = self.submit('错误标签'); self.advance()
        self.assertFalse(self.service.state()['history'][0]['viewable'])
        self.assertNotIn('html', self.service.state()['history'][0])
        with self.assertRaises(DomainError):
            self.service.submit(old)
        self.assertEqual(self.service.state()['chunk']['id'], 'shapes')
        self.advance(); self.submit(); self.advance()
        self.assertEqual(self.service.state()['phase'], 'waiting')
        self.when += timedelta(seconds=179)
        self.advance(); self.assertEqual(self.service.state()['phase'], 'waiting')
        self.service = AtomicLearningService(self.topic, Path(self.temp.name), clock=lambda: self.when)
        self.when += timedelta(seconds=1); self.advance()
        self.assertEqual(self.service.state()['phase'], 'answer')
        history = self.raw()['history']
        self.assertEqual(history[0]['ratings'], {'a': 1})
        self.assertIsNotNone(self.raw()['points']['a']['card_json'])

    def test_variants_rotate_and_bundle_requires_all_points_due(self):
        self.finish_initial()
        state = self.raw()
        first_due = min(datetime.fromisoformat(p['due_at']) for p in state['points'].values())
        self.when = first_due
        self.service.choose(self.action(choice='review'))
        self.assertEqual(self.service.state()['current']['card_id'], 'a-2')
        self.assertEqual(self.service.state()['current']['point_ids'], ['a'])
        self.submit()
        self.assertIsNone(self.raw()['points']['b']['card_json'])

    def test_repeated_errors_never_auto_pause_and_manual_pause_has_exact_unlock(self):
        self.service.choose(self.action(choice='learn')); self.advance()
        for _ in range(12):
            state = self.service.state()
            if state['pause']:
                break
            if state['phase'] == 'answer':
                self.submit('错误标签')
            else:
                if state['phase'] == 'waiting':
                    self.when = datetime.fromisoformat(state['next_available_at'])
                self.advance()
        # Far beyond the paradigm mode's error thresholds, atom training continues.
        for _ in range(40):
            state = self.service.state()
            if state['pause']:
                break
            if state['phase'] == 'answer': self.submit('错误标签')
            else:
                if state['phase'] == 'waiting': self.when = datetime.fromisoformat(state['next_available_at'])
                self.advance()
        self.assertIsNone(state['pause'])
        self.assertGreaterEqual(len(self.raw()['history']), 12)
        state = self.service.pause({'request_id': str(uuid.uuid4())})
        self.assertIsNotNone(state['pause'])
        with self.assertRaises(DomainError): self.service.unlock(self.topic['id'], 'atomic', 'wrong')
        result = self.service.unlock(self.topic['id'], 'atomic', state['pause']['event_id'])
        self.assertIsNone(result['pause'])
        self.assertEqual(self.service.unlock(self.topic['id'], 'atomic', state['pause']['event_id'])['revision'], result['revision'])

    def test_stale_answer_does_not_change_memory(self):
        self.service.choose(self.action(choice='learn')); self.advance()
        stale = self.action(answer='红色')
        self.submit('错误标签')
        with self.assertRaises(DomainError): self.service.submit(stale)
        before = self.raw()['points']
        self.service.pause({'request_id': str(uuid.uuid4())})
        self.assertEqual(self.raw()['points'], before)

    def test_judge_failure_recovery_and_pause_during_judging(self):
        self.service.choose(self.action(choice='learn')); self.advance()
        original = self.raw()['points']
        with patch('math_learning.atomic.judge_answer', return_value=(None, 'retry')):
            _, result = self.submit('红色')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(self.raw()['points'], original)
        self.assertIsNone(self.service.state()['pending_submission_id'])
        entered, finish = threading.Event(), threading.Event()
        def wait_for_judge(*args):
            entered.set(); finish.wait(10); return True, 'correct'
        payload = self.action(answer='红色')
        self.service.submit(payload)
        with patch('math_learning.atomic.judge_answer', side_effect=wait_for_judge):
            worker = threading.Thread(target=self.service.process_submission, args=(payload['request_id'],))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                paused = self.service.pause({'request_id': str(uuid.uuid4())})
                self.assertIsNotNone(paused['pause'])
            finally:
                finish.set(); worker.join(10)
        result = self.service.submission(payload['request_id'])
        self.assertEqual(result['status'], 'discarded'); self.assertIsNone(result['correct'])
        self.assertEqual(self.raw()['points'], original)
        self.service.unlock(self.topic['id'], 'atomic', paused['pause']['event_id'])
        state = self.service.state()
        if state['phase'] == 'chunk': self.advance()
        if self.service.state()['phase'] == 'waiting':
            self.when = datetime.fromisoformat(self.service.state()['next_available_at']); self.advance()
        pending = self.service.submit(self.action(answer='红色'))
        restarted = AtomicLearningService(self.topic, Path(self.temp.name), clock=lambda: self.when)
        restarted.recover_interrupted()
        self.assertEqual(restarted.submission(pending['submission_id'])['status'], 'error')
        self.assertEqual(self.raw()['points'], original)

    def test_repeated_review_errors_do_not_auto_pause(self):
        self.finish_initial(); self.when += timedelta(hours=6, minutes=1)
        self.service.choose(self.action(choice='review'))
        failures = 0
        for _ in range(40):
            state = self.service.state()
            self.assertIsNone(state['pause'])
            if state['phase'] == 'answer':
                self.submit('错误' if state['current']['card_id'] != 'ab-bundle' else '{"a":"错误","b":"错误"}')
                failures += 1
                if failures >= 8: break
            else:
                if state['phase'] == 'waiting': self.when = datetime.fromisoformat(state['next_available_at'])
                self.advance()
        self.assertEqual(failures, 8); self.assertIsNone(self.service.state()['pause'])

    def test_concurrent_duplicate_submission_is_judged_and_scheduled_once(self):
        self.finish_initial(); self.when += timedelta(hours=6, minutes=1)
        self.service.choose(self.action(choice='review'))
        payload = self.action(answer='{"a":"红色","b":"蓝色"}')
        with patch('math_learning.atomic.judge_answer', return_value=(True, 'correct')) as judge:
            workers = [threading.Thread(target=lambda: (self.service.submit(payload), self.service.process_submission(payload['request_id']))) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join(10)
            self.assertEqual(judge.call_count, 1)
        self.assertEqual(sum(r['mode'] == 'review' for r in self.raw()['history']), 1)

    def test_version_change_preserves_progress(self):
        self.finish_initial()
        changed = atomic_topic(); changed['version'] = 'v2'
        with self.assertRaises(DomainError):
            AtomicLearningService(changed, Path(self.temp.name))
        self.assertTrue(all(p['mastered'] for p in self.raw()['points'].values()))
