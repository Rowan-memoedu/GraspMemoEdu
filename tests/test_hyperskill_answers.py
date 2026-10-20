from contextlib import closing
from copy import deepcopy
import hashlib
import json
import unittest
import uuid

import test_hyperskill_training as fixtures
from graspmemoedu.academy_import import install
from graspmemoedu.hyperskill_answers import AnswerStore, canonical, key
from graspmemoedu.hyperskill_training import HyperskillLocalTraining
from graspmemoedu.service import DomainError
from graspmemoedu.topic_install import encoded
from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import TrainingCatalog, fingerprint
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from fastapi.testclient import TestClient


class SharedHyperskillAnswersTests(unittest.TestCase):
    setUp = fixtures.HyperskillBankTests.setUp
    prepare = fixtures.HyperskillBankTests.prepare
    native = fixtures.HyperskillBankTests.native
    begin = fixtures.HyperskillBankTests.begin
    event = fixtures.HyperskillBankTests.event

    def prepared(self, kind='choice'):
        q = self.bank['groups'][-1]['questions'][0]
        q['hyperskill']['type'] = kind
        q['hyperskill_presentation'] = {'schema': 1, 'dataset': {'options': ['A', 'B'], 'is_multiple_choice': False},
            'code_options': {}, 'interaction': {'version': 1, 'type': 'choice', 'multiple': False,
                'options': [{'id': str(i), 'text': v, 'html': v} for i, v in enumerate(['A', 'B'])]}}
        if kind == 'string':
            q['hyperskill_presentation'].update(dataset={}, interaction=None)
            self.old['flashcards'][0]['hyperskill']['type'] = kind
            (self.data / 'topic.json').write_bytes(encoded(self.old))
        if kind == 'fill-blanks':
            q['hyperskill_presentation'].update(
                dataset={'components': [{'type': 'text', 'text': 'Use ▭'},
                                        {'type': 'select', 'options': ['A', 'B']}]},
                interaction={'version': 1, 'type': 'hyperskill_fill', 'template': 'Use ▭',
                             'fields': [{'type': 'select', 'options': ['A', 'B']}]})
            self.old['flashcards'][0]['hyperskill']['type'] = kind
            (self.data / 'topic.json').write_bytes(encoded(self.old))
        self.bank['version'] = fingerprint(self.bank)
        name = 'training-banks/hyperskill-practice.json'
        raw = encoded(self.bank); (self.package / name).write_bytes(raw)
        manifest = json.loads((self.package / 'manifest.json').read_bytes())
        manifest['files'][name] = hashlib.sha256(raw).hexdigest()
        (self.package / 'manifest.json').write_bytes(encoded(manifest))
        return q

    def accepted(self, backend):
        adapter, run = self.begin(backend)
        event = self.event()
        event['receipt'].update(dataset={'options': ['B', 'A'], 'is_multiple_choice': False}, reply={'choices': [False, True]})
        adapter.check_event(run['round_id'], run['step_id'], event)
        return adapter

    def test_two_users_share_answer_but_not_history_or_fsrs_and_replay_is_safe(self):
        self.prepared(); backend = self.native(); source = self.accepted(backend)
        other = TrainingService(TrainingCatalog(self.data), self.data, 'second', clock=lambda: self.now)
        aid = other.open(self.bank['id'], self.old['id'], source.question['id'], str(uuid.uuid4()))
        state = other.state(aid)
        self.assertTrue(state['hyperskill']['prepared']); self.assertTrue(state['hyperskill']['local_ready'])
        self.assertEqual(state['hyperskill']['submissions'], [])
        self.assertNotIn('answer_display', state['hyperskill'])
        self.assertNotIn('reference_submission_id', json.dumps(state))
        adapter = HyperskillLocalTraining(other, aid)
        wrong = str(uuid.uuid4()); correct = str(uuid.uuid4())
        adapter.submit_local(wrong, '{"selected":"1"}')
        adapter.submit_local(wrong, '{"selected":"1"}')
        self.assertEqual(other.state(aid)['hyperskill']['local_checks'], 1)
        adapter.submit_local(correct, '{"selected":"0"}')
        adapter.submit_local(correct, '{"selected":"0"}')
        state = other.state(aid)
        self.assertEqual(state['result']['rating'], 2); self.assertTrue(state['result']['correct'])
        self.assertTrue(state['result']['scheduled'])
        with self.assertRaises(DomainError):
            adapter.submit_local(correct, '{"selected":"1"}')
        with closing(other._connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM training_ratings WHERE learner='second'").fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM hyperskill_answers').fetchone()[0], 1)

    def test_shared_reference_api_requires_attempt_owner_and_records_one_score(self):
        q = self.prepared(); backend = self.native(); self.accepted(backend)
        app = create_app(Settings(data_dir=self.data, public_api_origin='https://hyperskill.test'),
                         atomic_clock=lambda:self.now)
        with TestClient(app, base_url='https://hyperskill.test') as client:
            def identity():
                headers = {'origin': 'https://hyperskill.test'}
                response = client.post('/api/access/guest', json={}, headers=headers)
                self.assertEqual(response.status_code, 200)
                headers['x-learning-identity'] = response.json()['identity_token']
                return headers
            owner, other = identity(), identity()
            response = client.post('/api/training/open?subject_id=cs', headers=owner, json={
                'request_id': str(uuid.uuid4()), 'bank_id': self.bank['id'],
                'group_id': self.old['id'], 'question_id': q['id']})
            self.assertEqual(response.status_code, 200)
            state = response.json(); self.assertTrue(state['hyperskill']['local_ready'])
            payload = {'attempt_id': state['attempt_id'], 'request_id': str(uuid.uuid4()),
                       'answer': '{"selected":"0"}'}
            self.assertEqual(client.post('/api/training/hyperskill-submit', headers=other,
                                         json=payload).status_code, 404)
            result = client.post('/api/training/hyperskill-submit', headers=owner, json=payload)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()['result']['correct'])
            self.assertEqual(result.json()['hyperskill']['local_checks'], 1)
            replay = client.post('/api/training/hyperskill-submit', headers=owner, json=payload)
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.json()['hyperskill']['local_checks'], 1)

    def test_unverified_wrong_and_code_do_not_unlock_shared_grading(self):
        q = self.prepared(); backend = self.native()
        store = AnswerStore(backend)
        wrong = self.event(correct=False)['receipt']
        wrong['dataset'] = q['hyperskill_presentation']['dataset']
        store.record(q, wrong); self.assertFalse(store.references(q))
        old = self.event()['receipt']
        store.record(q, old); self.assertFalse(store.references(q))
        code = deepcopy(q); code['hyperskill']['type'] = 'code'
        store.record(code, self.event()['receipt']); self.assertIsNone(key(code))
        aid = backend.open(self.bank['id'], self.old['id'], q['id'], str(uuid.uuid4()))
        with self.assertRaises(DomainError):
            HyperskillLocalTraining(backend, aid).submit_local(str(uuid.uuid4()), '{"selected":"0"}')
        self.assertIsNone(backend.state(aid)['result'])

    def test_content_change_invalidates_reference_but_option_shuffle_does_not(self):
        q = self.prepared(); backend = self.native(); self.accepted(backend)
        shuffled = deepcopy(q); shuffled['hyperskill_presentation']['dataset']['options'].reverse()
        self.assertTrue(AnswerStore(backend).references(shuffled))
        changed = deepcopy(q); changed['question'] += ' Additional requirement'
        self.assertFalse(AnswerStore(backend).references(changed))
        changed = deepcopy(q); changed['hyperskill_presentation']['dataset']['options'][0] = 'New option'
        self.assertFalse(AnswerStore(backend).references(changed))

    def test_backfill_only_matching_old_receipt_preserves_rounds_and_ratings(self):
        from scripts.backfill_hyperskill_answers import backfill
        q = self.prepared(); backend = self.native()
        adapter, run = self.begin(backend)
        event = self.event()
        event['receipt']['reply'] = {'choices': [True, False]}
        adapter.check_event(run['round_id'], run['step_id'], event)
        with closing(backend._connect()) as con:
            before = {t: con.execute('SELECT * FROM ' + t).fetchall()
                      for t in ('training_external_rounds', 'training_ratings', 'training_memory')}
        r = event['receipt']
        evidence = {'submissions': [{'id': r['submission_id'], 'attempt': r['attempt_id'],
                    'step': r['step_id'], 'time': r['time'], 'status': 'correct', 'reply': r['reply']}],
                    'attempts': [{'id': r['attempt_id'], 'step': r['step_id'],
                                 'dataset': {**q['hyperskill_presentation']['dataset'], 'unused_api_field': ''}}]}
        self.assertEqual(backfill(self.data, evidence)['verified_reference_submissions'], [r['submission_id']])
        self.assertEqual(len(AnswerStore(backend).references(q)), 1)
        backfill(self.data, evidence)
        with closing(backend._connect()) as con:
            for table, rows in before.items():
                self.assertEqual(con.execute('SELECT * FROM ' + table).fetchall(), rows)
        evidence['submissions'][0]['reply'] = {'choices': [False, True]}
        with self.assertRaises(ValueError):
            backfill(self.data, evidence)

    def test_backfill_raw_fill_components_ignore_empty_fields_but_reject_changed_question(self):
        from scripts.backfill_hyperskill_answers import backfill
        q = self.prepared('fill-blanks'); backend = self.native()
        adapter, run = self.begin(backend)
        event = self.event(); event['receipt']['reply'] = {'blanks': ['A']}
        adapter.check_event(run['round_id'], run['step_id'], event)
        r = event['receipt']
        raw = {'components': [{'type': 'text', 'text': 'Use ▭', 'options': []},
                              {'type': 'select', 'text': '', 'options': ['B', 'A']}]}
        evidence = {'submissions': [{'id': r['submission_id'], 'attempt': r['attempt_id'],
                    'step': r['step_id'], 'time': r['time'], 'status': 'correct', 'reply': r['reply']}],
                    'attempts': [{'id': r['attempt_id'], 'step': r['step_id'], 'dataset': raw}]}
        raw['components'][0]['text'] = 'Changed requirement ▭'
        self.assertEqual(backfill(self.data, evidence)['verified_reference_submissions'], [])
        self.assertEqual(AnswerStore(backend).references(q), [])
        raw['components'][0]['text'] = 'Use ▭'
        self.assertEqual(backfill(self.data, evidence)['verified_reference_submissions'], [r['submission_id']])
        self.assertEqual(len(AnswerStore(backend).references(q)), 1)

    def test_semantic_equivalent_text_uses_existing_judge_and_never_becomes_source_key(self):
        q = self.prepared('string'); backend = self.native()
        receipt = self.event()['receipt']; receipt.update(dataset={}, reply={'text': 'known expression'}, answer_display='known expression')
        AnswerStore(backend).record(q, receipt)
        calls = []
        def judge(*args):
            calls.append(args); return {'status': 'judged', 'correct': True, 'reason': 'Equivalent'}
        other = TrainingService(TrainingCatalog(self.data), self.data, 'second', judge, clock=lambda:self.now)
        aid = other.open(self.bank['id'], self.old['id'], q['id'], str(uuid.uuid4()))
        other.add_to_notebook(aid)
        HyperskillLocalTraining(other, aid).submit_local(str(uuid.uuid4()), 'another expression')
        self.assertTrue(other.state(aid)['result']['correct']); self.assertEqual(len(calls), 1)
        self.assertIn('known expression', calls[0][1])
        self.assertEqual(len(AnswerStore(other).references(q)), 1)

    def test_display_enrichment_preserves_existing_attempt_and_learning_rows(self):
        backend = self.native(); original, run = self.begin(backend)
        with closing(backend._connect()) as con:
            snapshot = con.execute('SELECT snapshot FROM training_attempts WHERE id=?', (original.attempt_id,)).fetchone()[0]
        self.prepared(); install(self.package, self.data, extend_course=True)
        new = TrainingService(TrainingCatalog(self.data), self.data, 'learner', clock=lambda:self.now)
        self.assertTrue(new.state(original.attempt_id)['hyperskill']['prepared'])
        with closing(new._connect()) as con:
            self.assertEqual(con.execute('SELECT snapshot FROM training_attempts WHERE id=?', (original.attempt_id,)).fetchone()[0], snapshot)

    def test_all_closed_answer_types_are_bound_to_content(self):
        self.assertEqual(canonical('number', {}, {'number': '1.00'}), canonical('number', {}, {'number': '1e0'}))
        self.assertEqual(canonical('matching', {'pairs': [{'first': 'a', 'second': '2'}, {'first': 'b', 'second': '1'}]},
                                   {'ordering': [1, 0]}), [['a', '1'], ['b', '2']])
        self.assertEqual(canonical('sorting', {'options': ['b', 'a']}, {'ordering': [1, 0]}), ['a', 'b'])
        self.assertEqual(canonical('parsons', {'lines': ['inner', 'outer']},
                                   {'lines': [{'line_number': 1, 'level': 0}, {'line_number': 0, 'level': 1}]}), [['outer', 0], ['inner', 1]])
        self.assertEqual(canonical('fill-blanks', {'components': [{'type': 'text', 'text': '▭'},
            {'type': 'select', 'options': ['x', 'y']}]}, {'blanks': ['x']}), ['x'])
        self.assertEqual(canonical('table', {'rows': ['r'], 'columns': ['a', 'b'], 'is_checkbox': False},
            {'choices': [{'name_row': 'r', 'columns': [{'name': 'a', 'answer': True}, {'name': 'b', 'answer': False}]}]}), [['r', ['a']]])
