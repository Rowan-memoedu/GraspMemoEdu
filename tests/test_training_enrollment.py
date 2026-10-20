"""The notebook admission rule is shared by subjects and grading providers."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
import uuid

from graspmemoedu.academy_training import AcademyTraining
from graspmemoedu.hyperskill_training import HyperskillTraining
from graspmemoedu.subject_registry import SUBJECT_IDS
from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import TrainingCatalog, fingerprint, validate
from tests.fixtures import temporary_test_directory
from training_fixtures import training_bank


class TrainingEnrollmentTests(unittest.TestCase):
    def setUp(self):
        temporary = temporary_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'training-banks').mkdir()
        for subject in SUBJECT_IDS:
            bank = training_bank(subject)
            bank['id'] = 'bank-' + subject
            group = bank['groups'][1]
            academy = deepcopy(group['questions'][0])
            academy.update(id='academy', academy={'task_key': 'fixture', 'kind': 'automated', 'version': 'v1'})
            hyperskill = deepcopy(academy)
            hyperskill.pop('academy')
            hyperskill.update(id='hyperskill', answer_type='external', reference_answer='', explanation_html='',
                hyperskill={'topic_id': 101, 'theory_id': 102, 'step_id': 200, 'type': 'choice'})
            group['questions'].extend([academy, hyperskill])
            bank['version'] = fingerprint(bank)
            validate(bank)
            (self.root / 'training-banks' / (bank['id'] + '.json')).write_text(json.dumps(bank), encoding='utf-8')
        self.catalog = TrainingCatalog(self.root)
        self.now = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
        self.receipt = 0

    def backend(self, subject):
        return TrainingService(self.catalog, self.root, 'learner-' + subject, clock=lambda: self.now)

    def open(self, backend, subject, provider):
        qid = 'exact' if provider == 'web' else provider
        return backend.open('bank-' + subject, 'group-a', qid, str(uuid.uuid4()))

    def answer(self, backend, aid, provider, *, failure=False, environment=False):
        if provider == 'web':
            sid = backend.submit({'request_id': str(uuid.uuid4()), 'attempt_id': aid,
                                  'answer': 'wrong' if failure else '2'})
            backend.process_submission(sid)
        else:
            adapter = (AcademyTraining if provider == 'academy' else HyperskillTraining)(backend, aid)
            run = adapter.launch()
            adapter.opened(run['round_id'], run['step_id'])
            categories = (['environment'] if environment else []) + (['wrong'] if failure else []) + ['passed']
            for sequence, category in enumerate(categories, 1):
                event = {'id': str(uuid.uuid4()), 'sequence': sequence, 'category': category,
                         'code_hash': str(sequence) * 64, 'occurred_at': self.now.isoformat()}
                if provider == 'hyperskill':
                    self.receipt += 1
                    event['receipt'] = {'submission_id': self.receipt, 'attempt_id': self.receipt + 100,
                        'step_id': 200, 'status': 'correct' if category == 'passed' else 'wrong',
                        'time': self.now.isoformat(), 'reply': {'choices': [True, False]},
                        'answer_display': 'A', 'feedback': 'Fixture verdict'}
                result = adapter.check_event(run['round_id'], run['step_id'], event)
            self.assertTrue(result['closed'])
            self.assertEqual(result['rating'], backend.state(aid)['result']['rating'])
            self.assertTrue(adapter.check_event(run['round_id'], run['step_id'], event)['duplicate'])
        return backend.state(aid)

    def test_first_correct_has_no_fsrs_or_future_review_for_any_subject_or_provider(self):
        for subject in SUBJECT_IDS:
            backend = self.backend(subject)
            backend.fsrs.schedule = lambda *a: self.fail('First correct must not call FSRS')
            for provider in ('web', 'academy', 'hyperskill'):
                with self.subTest(subject=subject, provider=provider):
                    aid = self.open(backend, subject, provider)
                    state = self.answer(backend, aid, provider)
                    self.assertEqual(state['result']['rating'], 5)
                    self.assertTrue(state['stopped'])
                    self.assertFalse(state['in_error_notebook'])
                    self.assertFalse(state['result']['scheduled'])
                    self.assertIsNone(state['due_at'])
                    with closing(backend._connect()) as con:
                        memory = dict(backend._memory(con, state['bank_id'], state['question_id'], state['version']))
                        self.assertIsNone(memory['card_json'])
                        self.assertEqual(con.execute('SELECT COUNT(*) FROM training_ratings WHERE attempt=?', (aid,)).fetchone()[0], 1)
                    repeat = self.open(backend, subject, provider)
                    self.assertTrue(self.answer(backend, repeat, provider)['result']['practice_only'])
            self.now += timedelta(days=365)
            self.assertEqual(backend.reviews([self.catalog.bank('bank-' + subject)])['items'], [])

    def test_manual_notebook_inclusion_keeps_first_correct_in_review(self):
        for subject in SUBJECT_IDS:
            backend = self.backend(subject)
            for provider in ('web', 'academy', 'hyperskill'):
                with self.subTest(subject=subject, provider=provider):
                    aid = self.open(backend, subject, provider)
                    backend.add_to_notebook(aid)
                    state = self.answer(backend, aid, provider)
                    self.assertEqual(state['result']['rating'], 3)
                    self.assertTrue(state['result']['scheduled'])
                    self.assertFalse(state['stopped'])
                    self.assertIsNotNone(state['due_at'])
                    self.now = datetime.fromisoformat(state['due_at']) + timedelta(seconds=1)
                    review = self.open(backend, subject, provider)
                    reviewed = self.answer(backend, review, provider)
                    self.assertEqual(reviewed['result']['rating'], 3)
                    self.assertTrue(reviewed['result']['scheduled'])

    def test_failure_before_pass_keeps_existing_error_mapping(self):
        for subject in SUBJECT_IDS:
            backend = self.backend(subject)
            for provider in ('web', 'academy', 'hyperskill'):
                with self.subTest(subject=subject, provider=provider):
                    state = self.answer(backend, self.open(backend, subject, provider), provider, failure=True)
                    self.assertEqual(state['result']['rating'], 1 if provider == 'web' else 2)
                    self.assertTrue(state['result']['scheduled'])
                    self.assertTrue(state['in_error_notebook'])
                    self.assertIsNotNone(state['due_at'])

    def test_environment_failures_do_not_turn_first_pass_into_notebook_admission(self):
        backend = self.backend('cs')
        state = self.answer(backend, self.open(backend, 'cs', 'academy'), 'academy', environment=True)
        self.assertEqual(state['result']['rating'], 5)
        self.assertFalse(state['result']['scheduled'])
