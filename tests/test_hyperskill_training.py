from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from graspmemoedu.academy_import import install
from graspmemoedu.api import create_app
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.hyperskill import HyperskillLearningService
from graspmemoedu.hyperskill_training import HyperskillTraining
from graspmemoedu.service import DomainError, create_learning_service
from graspmemoedu.topic_install import encoded
from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import TrainingCatalog, fingerprint, validate
from test_hyperskill import topic


class HyperskillBankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'; self.data.mkdir()
        self.now = datetime.now(timezone.utc)
        self.old = topic()
        self.old['math_css'] = ''
        for q in self.old['flashcards']:
            q['answer_type'] = 'external'
        self.course = {'id': 'hyperskill', 'title': 'Hyperskill', 'description': '',
            'topics': [{'id': self.old['id'], 'path': 'topic.json', 'prerequisites': []}],
            'units': [{'id': 'java', 'title': 'Java', 'topic_ids': [self.old['id']]}]}
        (self.data / 'topic.json').write_bytes(encoded(self.old))
        (self.data / 'catalog.json').write_bytes(encoded({'courses': [self.course]}))
        (self.data / 'subjects.json').write_bytes(encoded({'subjects': [{'id': 'cs', 'course_ids': ['hyperskill']}]}))
        self.package, self.new, self.bank = self.prepare(self.old, 'package')

    def prepare(self, source, name):
        package = self.root / name; package.mkdir()
        new = {k: deepcopy(source[k]) for k in ('id', 'title', 'subject_id', 'source_adapter', 'hyperskill', 'math_css')}
        new.update(topic_format_version=2, topic_kind='introduction', training_mode='paradigm',
            introductions=[{'id': 'intro-' + source['id'], 'title': '引言', 'html': '<p>Source theory</p>'}],
            modules=[], memory_chunks=[], memory_points=[], flashcards=[], content_version='content')
        new['content_order'] = [{'kind': 'introduction', 'id': new['introductions'][0]['id']}]
        new['version'] = fingerprint(new)
        questions = []
        for card in source['flashcards']:
            q = deepcopy(card); q.pop('point_ids')
            q['hyperskill'] = {**source['hyperskill'], **card['hyperskill']}
            questions.append(q)
        bank = {'schema': 1, 'id': 'hyperskill-practice', 'title': 'Hyperskill', 'description': '', 'subject_id': 'cs',
            'math_css': source['math_css'], 'groups': [
                {'id': 'java', 'title': 'Java', 'questions': []},
                {'id': source['id'], 'title': source['title'], 'parent_id': 'java', 'questions': questions}]}
        bank['version'] = fingerprint(bank); validate(bank)
        course = deepcopy(self.course)
        course['topics'] = [{'id': source['id'], 'path': 'topic.json', 'prerequisites': []}]
        course['units'][0]['topic_ids'] = [source['id']]
        files = {'topic.json': encoded(new), 'catalog.json': encoded({'courses': [course]}),
                 'training-banks/hyperskill-practice.json': encoded(bank)}
        for name, raw in files.items():
            (package / name).parent.mkdir(exist_ok=True)
            (package / name).write_bytes(raw)
        (package / 'manifest.json').write_bytes(encoded({'schema': 1, 'adapter': 'hyperskill', 'topic_format_version': 2,
            'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}))
        return package, new, bank

    def native(self):
        install(self.package, self.data, extend_course=True)
        return TrainingService(TrainingCatalog(self.data), self.data, 'learner', clock=lambda: self.now)

    def begin(self, backend, q=None):
        aid = backend.open('hyperskill-practice', self.old['id'], q or self.old['flashcards'][0]['id'], str(uuid.uuid4()))
        adapter = HyperskillTraining(backend, aid)
        run = adapter.launch(); adapter.opened(run['round_id'], run['step_id'])
        return adapter, run

    def event(self, number=1, sequence=1, correct=True):
        when = self.now.isoformat()
        return {'id': str(uuid.uuid4()), 'sequence': sequence, 'category': 'passed' if correct else 'wrong',
            'code_hash': str(sequence % 10) * 64, 'occurred_at': when,
            'receipt': {'submission_id': number, 'attempt_id': number + 100,
                'step_id': 200, 'status': 'correct' if correct else 'wrong', 'time': when,
                'reply': {'choices': [True, False]}, 'answer_display': 'A', 'feedback': 'source feedback'}}

    def test_verdict_fsrs_and_global_receipt_replay(self):
        backend = self.native(); adapter, run = self.begin(backend)
        backend.add_to_notebook(adapter.attempt_id)
        event = self.event()
        self.assertEqual(adapter.check_event(run['round_id'], run['step_id'], event)['rating'], 3)
        self.assertTrue(adapter.check_event(run['round_id'], run['step_id'], event)['duplicate'])
        state = backend.state(adapter.attempt_id)
        self.assertEqual(state['hyperskill']['submissions'][0]['feedback'], 'source feedback')
        self.assertTrue(state['result']['scheduled']); self.assertEqual(state['answer_display'], 'A')
        again, arun = self.begin(backend)
        with self.assertRaises(DomainError):
            again.check_event(arun['round_id'], arun['step_id'], event)
        with closing(backend._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_ratings').fetchone()[0], 1)
            original = dict(con.execute('SELECT * FROM training_memory').fetchone())
        self.now = datetime.fromisoformat(state['due_at']) + timedelta(seconds=1)
        review, rrun = self.begin(backend)
        review.check_event(rrun['round_id'], rrun['step_id'], self.event(2, correct=False))
        review.check_event(rrun['round_id'], rrun['step_id'], self.event(3, sequence=2))
        self.assertEqual(backend.state(review.attempt_id)['result']['rating'], 2)
        with closing(backend._connect()) as con:
            self.assertEqual(con.execute('SELECT revision FROM training_memory').fetchone()[0], original['revision'] + 1)

    def test_reject_fake_grading_wrong_problem_and_unconfirmed_finish(self):
        backend = self.native(); adapter, run = self.begin(backend)
        for operation in (lambda: backend.submit({'attempt_id': adapter.attempt_id}),
                          lambda: backend.reveal(adapter.attempt_id), lambda: adapter.finish()):
            with self.assertRaises(DomainError): operation()
        event = self.event(); event['receipt']['step_id'] = 201
        with self.assertRaises(DomainError): adapter.check_event(run['round_id'], run['step_id'], event)
        event = self.event(correct=False)
        adapter.check_event(run['round_id'], run['step_id'], event); adapter.finish(); adapter.finish()
        self.assertEqual(backend.state(adapter.attempt_id)['result']['rating'], 1)

    def test_legacy_card_history_and_open_round_preserved(self):
        legacy = HyperskillLearningService(self.old, self.data, learner_id='learner', clock=lambda: self.now)
        def action(**kw):
            s = legacy.state()
            return dict(request_id=str(uuid.uuid4()), expected_revision=s['revision'],
                attempt_id=s['attempt_id'], step_id=s['active_step_id'], **kw)
        legacy.choose(action(choice='learn')); run = legacy.launch(action())
        legacy.opened(run['round_id'], run['step_id'])
        event = self.event()
        event['receipt']['step_id'] = legacy.state()['current']['hyperskill']['step_id']
        legacy.check_event(run['round_id'], run['step_id'], event)
        legacy.advance(action())
        next_run = legacy.launch(action()); legacy.opened(next_run['round_id'], next_run['step_id'])
        with closing(legacy._connect()) as con:
            original = con.execute('SELECT body FROM topics WHERE learner=?', ('learner',)).fetchone()[0]
        backend = self.native()
        catalog = Catalog.load(Settings.load(self.data))
        with closing(backend._connect()) as con:
            self.assertEqual(con.execute('SELECT body FROM hyperskill_topic_archives').fetchone()[0], original)
            point = next(p for p in json.loads(original)['points'].values() if p['card_json'])
            migrated = con.execute('SELECT * FROM training_memory').fetchone()
            self.assertEqual((migrated['card_json'], migrated['due_at']), (point['card_json'], point['due_at']))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_attempts').fetchone()[0], 2)
        reader = create_learning_service(catalog.topics[self.old['id']], self.data, learner_id='learner')
        self.assertEqual(reader.state()['topic_kind'], 'introduction')
        self.assertEqual(install(self.package, self.data, extend_course=True)['status'], 'unchanged')
        self.assertEqual(TrainingCatalog(self.data).topic_redirects, {})

    def test_failed_activation_rolls_back_data_bank_and_pointer(self):
        HyperskillLearningService(self.old, self.data, learner_id='learner')
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            original = con.execute('SELECT body FROM topics').fetchone()[0]
        from graspmemoedu.hyperskill_import import HyperskillImport
        migrate = HyperskillImport._migrate
        def failure(self, con, old, loaded):
            migrate(self, con, old, loaded)
            raise ValueError('injected activation failure')
        with patch.object(HyperskillImport, '_migrate', failure), self.assertRaises(ValueError):
            self.native()
        self.assertFalse((self.data / 'active-content.json').exists())
        self.assertFalse((self.data / 'training-banks/hyperskill-practice.json').exists())
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT body FROM topics').fetchone()[0], original)

    def test_future_append_keeps_existing_memory_and_question_contents(self):
        backend = self.native(); adapter, run = self.begin(backend)
        adapter.check_event(run['round_id'], run['step_id'], self.event())
        with closing(backend._connect()) as con:
            before = dict(con.execute('SELECT * FROM training_memory').fetchone())
        other = deepcopy(self.old); other['id'] = 'other'; other['hyperskill']['topic_id'] = 303
        for q in other['flashcards']:
            q['id'] = 'other-' + q['id']; q['hyperskill']['step_id'] += 500
        package, _, _ = self.prepare(other, 'more')
        install(package, self.data, extend_course=True)
        with closing(backend._connect()) as con:
            after = dict(con.execute('SELECT * FROM training_memory').fetchone())
        self.assertNotEqual(after.pop('version'), before.pop('version'))
        self.assertEqual(after, before)
        self.assertEqual(len(Catalog.load(Settings.load(self.data)).topics), 2)
        self.assertEqual(install(package, self.data, extend_course=True)['status'], 'unchanged')

    def test_api_uses_bank_capability_and_keeps_theory_route(self):
        self.native()
        app = create_app(Settings(data_dir=self.data, public_api_origin='https://hyperskill.test'), atomic_clock=lambda:self.now)
        with TestClient(app, base_url='https://hyperskill.test') as client:
            headers = {'origin': 'https://hyperskill.test'}
            def call(method, path, data=None, auth=None, status=200):
                response = client.request(method, '/api/' + path, headers=auth or headers,
                    **({'json': data} if data is not None else {}))
                self.assertEqual(response.status_code, status, response.text)
                return response.json()
            identity = call('POST', 'access/guest', {})
            headers['x-learning-identity'] = identity['identity_token']
            theory = call('GET', 'state?topic_id=' + self.old['id'])
            self.assertEqual(theory['topic_kind'], 'introduction'); self.assertNotIn('training_target', theory)
            state = call('POST', 'training/open?subject_id=cs', {'request_id':str(uuid.uuid4()),
                'bank_id':'hyperskill-practice','group_id':self.old['id'],'question_id':self.old['flashcards'][0]['id']})
            call('POST', 'training/notebook', {'attempt_id':state['attempt_id']})
            grant = call('POST', 'academy/training/launch', {'attempt_id':state['attempt_id']})
            plugin = {'origin':headers['origin'], 'authorization':'Bearer ' + grant['ticket']}
            binding = call('POST', 'academy/claim', {}, plugin)
            self.assertEqual(binding['provider'], 'hyperskill'); self.assertEqual(binding['step_id'], 200)
            call('POST', 'academy/event', {'kind':'opened'}, plugin)
            event = self.event()
            call('POST', 'academy/event', {'kind':'check','event':event}, plugin)
            done = call('GET', 'training/state?attempt_id=' + state['attempt_id'])
            self.now = datetime.fromisoformat(done['due_at']) + timedelta(seconds=1)
            reviews = call('GET', 'review-papers/inbox?subject_id=cs')
            self.assertEqual(reviews['due_count'], 1)
            self.assertTrue(reviews['legacy'][0]['external'])
            call('POST', 'access/logout', {})
            call('POST', 'academy/claim', {}, plugin, 401)

    def test_release_guard_and_portable_restore(self):
        from graspmemoedu.hyperskill_import import verify_database_transition
        from graspmemoedu.site_backup import backup_site, restore_site
        HyperskillLearningService(self.old, self.data, learner_id='learner')
        before = self.root / 'before.sqlite3'
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as source, closing(sqlite3.connect(before)) as destination:
            source.backup(destination)
        backend = self.native()
        self.assertTrue(verify_database_transition(before, backend.db_path, {self.old['id']}, self.bank)['unrelated_rows_unchanged'])
        adapter, run = self.begin(backend)
        adapter.check_event(run['round_id'], run['step_id'], self.event())
        saved = backend.state(adapter.attempt_id)
        archive = self.root / 'site.zip'
        backup_site(Settings.load(self.data), archive)
        restored = self.root / 'restored'; restore_site(archive, restored)
        other = TrainingService(TrainingCatalog(restored), restored, backend.learner_id, clock=lambda:self.now)
        self.assertEqual(other.state(adapter.attempt_id), saved)
        with closing(other._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM hyperskill_topic_archives').fetchone()[0], 1)
        with closing(backend._connect()) as con:
            con.execute('INSERT INTO training_preferences VALUES (?,1)', ('unrelated',))
        with self.assertRaisesRegex(ValueError, 'Unexpected database row'):
            verify_database_transition(before, backend.db_path, {self.old['id']}, self.bank)

if __name__ == '__main__':
    unittest.main()
