from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import TrainingCatalog, fingerprint, validate, install, validate_saved
from graspmemoedu.service import DomainError
from graspmemoedu.learning_storage import MemoryLearningStore
from training_fixtures import training_bank
from tests.fixtures import temporary_test_directory


class TrainingTests(unittest.TestCase):
    def setUp(self):
        temporary = temporary_test_directory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); (self.root / 'training-banks').mkdir()
        self.bank = training_bank()
        (self.root / 'training-banks/isolated-bank.json').write_text(json.dumps(self.bank), encoding='utf-8')
        self.catalog = TrainingCatalog(self.root)
        self.now = datetime(2026, 10, 3, 10, tzinfo=timezone.utc)
        self.calls = []
        def judge(stem, reference, answer):
            self.calls.append(answer)
            return {'status': 'judged', 'correct': answer == '2', 'reason': '隔离判题'}
        self.service = TrainingService(self.catalog, self.root, 'learner', judge, clock=lambda: self.now)

    def open(self, qid='essay', service=None):
        service = service or self.service
        return service.open(self.bank['id'], 'group-b' if qid == 'essay-b' else 'group-a', qid, str(uuid.uuid4()))

    def answer(self, attempt, answer='2', service=None, request=None):
        service = service or self.service
        payload = {'request_id': request or str(uuid.uuid4()), 'attempt_id': attempt, 'answer': answer, 'elapsed_ms': 850}
        submission = service.submit(payload); service.process_submission(submission)
        return service.state(attempt), payload

    def memory(self, qid='essay'):
        with closing(self.service._connect()) as con:
            row = self.service._memory(con, self.bank['id'], qid, self.bank['version'])
            return dict(row) if row else None

    def test_initial_correct_five_never_schedules_and_manual_repeat_preserves_memory(self):
        self.assertFalse(self.service.state(self.open())['can_review_early'])
        self.service.fsrs.schedule = lambda *args: self.fail('Score 5 must not enter FSRS')
        state, _ = self.answer(self.open())
        self.assertEqual(state['result']['rating'], 5)
        self.assertIsNone(state['due_at']); self.assertTrue(state['stopped'])
        self.assertFalse(state['result']['scheduled']); self.assertFalse(state['result']['practice_only'])
        self.assertTrue(state['can_review_early'])
        saved = self.memory(); self.assertIsNone(saved['card_json'])
        self.now += timedelta(days=365)
        self.assertEqual(self.service.reviews([self.bank]), {'items': [], 'next_due_at': None})
        for answer in ('wrong', '2', 'wrong'):
            state, _ = self.answer(self.open(), answer)
            self.assertEqual(state['mode'], 'practice'); self.assertTrue(state['result']['practice_only'])
            self.assertEqual(saved, self.memory())
        self.assertEqual(len(self.service.history(self.bank['id'], 'essay')['items']), 5)

    def test_wrong_then_correct_rates_three_and_due_review_binary_rating(self):
        state, _ = self.answer(self.open(), 'wrong')
        self.assertEqual(state['result']['rating'], 1)
        card = self.memory()['card_json']; self.assertIsNotNone(card)
        state, _ = self.answer(self.open())
        self.assertEqual(state['result']['rating'], 3); self.assertNotEqual(self.memory()['card_json'], card)
        self.assertTrue(state['result']['scheduled']); self.assertFalse(state['stopped'])
        self.now = datetime.fromisoformat(state['due_at']) + timedelta(seconds=1)
        self.assertEqual(len(self.service.reviews([self.bank])['items']), 1)
        state, _ = self.answer(self.open())
        self.assertEqual(state['mode'], 'review'); self.assertEqual(state['result']['rating'], 3)
        self.now = datetime.fromisoformat(state['due_at']) + timedelta(seconds=1)
        state, _ = self.answer(self.open(), 'wrong')
        self.assertEqual(state['result']['rating'], 1)

    def test_reveal_and_self_ratings_idempotence(self):
        for rating in (1, 2, 3, 4):
            other = TrainingService(self.catalog, self.root, 'self-' + str(rating), clock=lambda: self.now)
            attempt = self.open(service=other)
            self.assertIsNone(other.state(attempt)['explanation_html'])
            other.reveal(attempt)
            self.assertTrue(other.state(attempt)['explanation_html'])
            with closing(other._connect()) as con:
                self.assertIsNone(other._memory(con, self.bank['id'], 'essay', self.bank['version']))
            other.rate(attempt, rating, 300)
            other.rate(attempt, rating, 300)
            self.assertEqual(other.state(attempt)['result']['rating'], rating)
            with self.assertRaises(DomainError): other.rate(attempt, 2 if rating == 1 else 1)
        self.assertEqual(self.calls, [])

    def test_early_self_rating_also_leaves_schedule_unchanged(self):
        self.answer(self.open(), 'wrong'); self.answer(self.open()); memory = self.memory()
        attempt = self.open(); self.service.reveal(attempt); self.service.rate(attempt, 1)
        self.assertEqual(memory, self.memory()); self.assertTrue(self.service.state(attempt)['result']['practice_only'])

    def test_stop_reviews_preserves_card_and_survives_restart_and_manual_practice(self):
        self.answer(self.open(), 'wrong'); self.answer(self.open()); memory = self.memory()
        # Stopping also works before the next due date, without FSRS rating 5.
        attempt = self.open(); self.service.reveal(attempt)
        self.service.fsrs.schedule = lambda *args: self.fail('Stop must never call FSRS')
        self.service.stop_reviews(attempt); self.service.stop_reviews(attempt)
        state = self.service.state(attempt)
        self.assertTrue(state['stopped']); self.assertTrue(state['result']['stop_requested'])
        self.assertIsNone(state['due_at']); self.assertIsNone(state['result']['rating'])
        self.assertEqual(memory, self.memory())
        with self.assertRaises(DomainError): self.service.rate(attempt, 4)
        self.now += timedelta(days=365)
        self.service = TrainingService(self.catalog, self.root, 'learner', self.service.judge, clock=lambda: self.now)
        self.assertEqual(self.service.reviews([self.bank]), {'items': [], 'next_due_at': None})
        state, _ = self.answer(self.open(), 'wrong')
        self.assertEqual(state['mode'], 'practice'); self.assertTrue(state['stopped'])
        self.assertEqual(memory, self.memory())
        attempt = self.open(); self.service.reveal(attempt); self.service.rate(attempt, 3)
        self.assertEqual(memory, self.memory()); self.assertTrue(self.service.state(attempt)['stopped'])

    def test_stop_before_first_rating_blocks_inflight_and_other_open_attempts(self):
        pending, shown = self.open(), self.open()
        request = self.service.submit({'request_id': str(uuid.uuid4()), 'attempt_id': pending, 'answer': '2'})
        self.service.reveal(shown); self.service.stop_reviews(shown)
        self.service.process_submission(request)
        self.assertIsNone(self.memory()); self.assertTrue(self.service.state(pending)['stopped'])
        with closing(self.service._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_suspensions').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_ratings WHERE rating=5').fetchone()[0], 0)
            validate_saved(con, self.catalog)
            self.catalog.banks.clear()
            with self.assertRaises(ValueError): validate_saved(con, self.catalog)

    def test_stop_requires_revealed_answer_and_is_owner_scoped(self):
        attempt = self.open()
        with self.assertRaises(DomainError): self.service.stop_reviews(attempt)
        self.service.reveal(attempt); self.service.stop_reviews(attempt)
        other = TrainingService(self.catalog, self.root, 'other', self.service.judge, clock=lambda: self.now)
        with self.assertRaises(DomainError): other.stop_reviews(attempt)
        result, _ = self.answer(self.open(service=other), service=other)
        self.assertTrue(result['stopped']); self.assertEqual(result['result']['rating'], 5)
        self.assertFalse(result['result']['scheduled'])

    def test_review_again_does_not_return_card_to_initial_learning(self):
        self.answer(self.open(), 'wrong'); self.answer(self.open())
        self.now = datetime.fromisoformat(self.memory()['due_at']) + timedelta(seconds=1)
        attempt = self.open(); self.service.reveal(attempt); self.service.rate(attempt, 1)
        self.now = datetime.fromisoformat(self.memory()['due_at']) + timedelta(seconds=1)
        state, _ = self.answer(self.open())
        self.assertEqual(state['mode'], 'review'); self.assertEqual(state['result']['rating'], 3)

    def test_permission_namespace_cannot_alias_an_existing_course_topic(self):
        with self.assertRaises(ValueError): self.catalog.permissions(['training.isolated-bank'])

    def test_fixed_interactions_no_self_rating_or_llm(self):
        for qid, answer in [('boolean', '{"a":true}'), ('choice', '{"selected":"a"}'), ('blank', '{"a":"2"}')]:
            attempt = self.open(qid)
            with self.assertRaises(DomainError): self.service.reveal(attempt)
            state, _ = self.answer(attempt, answer)
            self.assertTrue(state['result']['correct'])
        self.assertEqual(self.calls, [])

    def test_question_keys_do_not_leak_before_answer(self):
        for qid in ('essay', 'choice', 'boolean', 'blank'):
            state = self.service.state(self.open(qid), self_rating=True)
            public = json.dumps(state)
            for key in ('correct_id', 'reference_answer', 'answers', 'constraints'):
                self.assertNotIn('"' + key + '"', public)
            self.assertIsNone(state['explanation_html'])

    def test_submission_replay_and_concurrent_attempt_cannot_reschedule_twice(self):
        first, second = self.open(), self.open()
        state, payload = self.answer(first)
        self.answer(first, request=payload['request_id'])
        memory = self.memory(); self.answer(second, 'wrong')
        self.assertEqual(self.memory(), memory); self.assertEqual(len(self.calls), 2)
        with self.assertRaises(DomainError): self.answer(first, 'changed', request=payload['request_id'])

    def test_concurrent_first_correct_cannot_override_an_earlier_wrong(self):
        first, second = self.open(), self.open()
        self.answer(first, 'wrong'); saved = self.memory()
        state, _ = self.answer(second)
        self.assertTrue(state['result']['practice_only']); self.assertFalse(state['stopped'])
        self.assertEqual(self.memory(), saved)
        state, _ = self.answer(self.open())
        self.assertEqual(state['result']['rating'], 3); self.assertFalse(state['stopped'])

    def test_reveal_only_is_not_learned_and_error_retry_can_still_score_five(self):
        attempt = self.open(); self.service.reveal(attempt)
        self.assertFalse(self.service.state(attempt)['can_review_early'])
        self.service.judge = lambda *args: None
        attempt = self.open(); state, payload = self.answer(attempt)
        self.assertFalse(state['can_review_early'])
        self.service.judge = lambda *args: {'status': 'judged', 'correct': True, 'reason': 'ok'}
        self.service.submit(payload); self.service.process_submission(payload['request_id'])
        self.assertEqual(self.service.state(attempt)['result']['rating'], 5)

    def test_judge_error_retry_and_recovery_never_counts_wrong(self):
        self.service.judge = lambda *args: None
        attempt = self.open(); state, payload = self.answer(attempt)
        self.assertEqual(state['submission_status'], 'error'); self.assertIsNone(self.memory())
        self.service.judge = lambda *args: {'status': 'judged', 'correct': True, 'reason': 'ok'}
        self.service.submit(payload); self.service.recover_interrupted()
        self.assertEqual(self.service.state(attempt)['submission_status'], 'error')
        self.service.submit(payload); self.service.process_submission(payload['request_id'])
        self.assertEqual(self.service.state(attempt)['phase'], 'done')

    def test_order_history_pagination_and_cross_identity(self):
        self.answer(self.open('essay-b'))
        for _ in range(52): self.open('essay')
        page = self.service.history(self.bank['id'], 'essay')
        self.assertEqual(page['next_offset'], 50)
        self.assertEqual(len(self.service.history(self.bank['id'], 'essay', 50)['items']), 2)
        latest = page['items'][0]['id']; older = page['items'][1]['id']
        self.service.state(latest, history=False)
        with self.assertRaises(DomainError): self.service.state(older, history=False)
        other = TrainingService(self.catalog, self.root, 'other')
        with self.assertRaises(DomainError): other.state(latest)

    def test_admin_memory_does_not_enter_database(self):
        runtime = MemoryLearningStore(); self.addCleanup(runtime.close)
        preview = TrainingService(self.catalog, self.root, 'admin', self.service.judge, runtime=runtime, clock=lambda: self.now)
        state, _ = self.answer(self.open(service=preview), service=preview)
        self.assertEqual(state['phase'], 'done')
        stopped = self.open(service=preview); preview.reveal(stopped); preview.stop_reviews(stopped)
        self.assertTrue(preview.state(stopped)['stopped'])
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            for table in ('training_attempts', 'training_memory', 'training_submissions', 'training_ratings', 'training_suspensions'):
                self.assertEqual(con.execute(f'SELECT COUNT(*) FROM {table} WHERE learner=?', ('admin',)).fetchone()[0], 0)

    def test_content_tree_identity_immutability_and_backup_version_validation(self):
        validate(self.bank)
        for change in ('cycle', 'duplicate', 'subject', 'hash'):
            bank = deepcopy(self.bank)
            if change == 'cycle': bank['groups'][0]['parent_id'] = 'group-a'
            if change == 'duplicate': bank['groups'][2]['questions'][0]['id'] = 'essay'
            if change == 'subject': bank['subject_id'] = 'unknown'
            if change != 'hash': bank['version'] = fingerprint(bank)
            else: bank['title'] = 'changed'
            with self.assertRaises(ValueError): validate(bank)
        with self.assertRaises(FileExistsError): install(self.root / 'training-banks/isolated-bank.json', self.root)
        self.answer(self.open())
        with closing(self.service._connect()) as con:
            validate_saved(con, self.catalog)
            self.catalog.banks.clear()
            with self.assertRaises(ValueError): validate_saved(con, self.catalog)

    def test_rendered_markup_limit_does_not_expand_source_prose_limit(self):
        for field, maximum in (('html', 1000000), ('explanation_html', 1000000),
                               ('question', 100000), ('reference_answer', 100000)):
            bank = deepcopy(self.bank)
            question = next(q for group in bank['groups'] for q in group['questions'])
            question[field] = 'x' * maximum
            bank['version'] = fingerprint(bank)
            validate(bank)
            question[field] += 'x'
            bank['version'] = fingerprint(bank)
            with self.assertRaises(ValueError): validate(bank)


if __name__ == '__main__': unittest.main()
