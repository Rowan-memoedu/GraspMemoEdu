from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient
from fsrs import Card
from graspmemoedu.academy import AcademyLearningService
from graspmemoedu.academy_training import AcademyTraining
from graspmemoedu.academy_training_import import install, convert
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import DomainError
from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import TrainingCatalog
from graspmemoedu.topic_install import encoded
from test_academy import exercise


class NativeAcademyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime.now(timezone.utc)
        self.topic = exercise()
        self.manual = exercise('manual'); self.manual['id'] = 'manual'
        self.reading = {'id': 'reading', 'title': 'Read me', 'subject_id': 'cs', 'version': 'v1',
            'topic_format_version': 2, 'topic_kind': 'introduction', 'content_version': 'v1',
            'training_mode': 'paradigm', 'modules': [], 'math_css': '',
            'introductions': [{'id': 'intro', 'title': 'Read me', 'html': '<p>Read me</p>'}],
            'content_order': [{'kind': 'introduction', 'id': 'intro'}]}
        self.topics = {t['id']: t for t in (self.topic, self.manual, self.reading)}
        for tid, topic in self.topics.items():
            (self.root / (tid + '.json')).write_bytes(encoded(topic))
        self.course = {'id': 'academy-native', 'title': 'Practice', 'placement': 'question_bank',
            'topics': [{'id': tid, 'path': tid + '.json'} for tid in self.topics],
            'units': [{'id': 'section', 'title': 'Section', 'topic_ids': []},
                      {'id': 'lesson', 'title': 'Lesson', 'parent_id': 'section', 'topic_ids': list(self.topics)}]}
        (self.root / 'catalog.json').write_bytes(encoded({'courses': [self.course]}))
        (self.root / 'subjects.json').write_bytes(encoded({'subjects': [{'id': 'cs', 'course_ids': [self.course['id']]}]}))

    def native(self, learner='learner'):
        install(self.root, self.course['id'])
        catalog = TrainingCatalog(self.root)
        return TrainingService(catalog, self.root, learner, clock=lambda: self.now)

    def test_content_import_requires_explicit_legacy_migration(self):
        legacy = AcademyLearningService(self.topic, self.root, learner_id='learner', clock=lambda: self.now)
        legacy.state()
        before = (self.root / 'learning.sqlite3').read_bytes()
        with self.assertRaisesRegex(ValueError, 'explicit academy_training_import'):
            install(self.root, self.course['id'], migrate_existing=False)
        self.assertEqual((self.root / 'learning.sqlite3').read_bytes(), before)
        self.assertFalse((self.root / 'training-banks' / (self.course['id'] + '.json')).exists())
        self.assertEqual(install(self.root, self.course['id'])['status'], 'migrated')
        before = (self.root / 'learning.sqlite3').read_bytes()
        self.assertEqual(install(self.root, self.course['id'], migrate_existing=False)['status'], 'unchanged')
        self.assertEqual((self.root / 'learning.sqlite3').read_bytes(), before)

    def open(self, backend, question=None):
        return AcademyTraining(backend, backend.open(self.course['id'], 'lesson',
            question or self.topic['id'], str(uuid.uuid4())))

    def event(self, n, category, code=None):
        return {'id': str(uuid.uuid4()), 'sequence': n, 'category': category,
                'code_hash': (code or str(n % 10)) * 64, 'occurred_at': self.now.isoformat()}

    def launch(self, adapter):
        run = adapter.launch()
        adapter.opened(run['round_id'], run['step_id'])
        return run

    def send(self, adapter, run, n, category, code=None):
        return adapter.check_event(run['round_id'], run['step_id'], self.event(n, category, code))

    def test_native_hierarchy_and_source_fidelity(self):
        backend = self.native()
        bank = backend.catalog.bank(self.course['id'])
        self.assertEqual(len(bank['groups']), 2)
        self.assertEqual(bank['groups'][1]['parent_id'], 'section')
        group = bank['groups'][1]
        self.assertEqual(group['content_order'], [{'type': 'question', 'id': self.topic['id']},
            {'type': 'question', 'id': 'manual'}, {'type': 'introduction', 'id': 'reading'}])
        self.assertEqual(group['questions'][0]['html'], self.topic['flashcards'][0]['html'])
        # Conversion is not a CS-specific page or hierarchy implementation.
        self.assertEqual(convert(self.course, self.topics, 'math')['groups'], bank['groups'])
        state = backend.state(self.open(backend).attempt_id)
        self.assertEqual(state['phase'], 'answer')
        self.assertNotIn('introduction', state)
        self.assertEqual(state['academy']['version'], self.topic['version'])

    def test_error_mapping_dedup_and_one_fsrs_state(self):
        backend = self.native(); adapter = self.open(backend); run = self.launch(adapter)
        self.send(adapter, run, 1, 'compile', 'a')
        self.send(adapter, run, 2, 'compile', 'a')
        self.send(adapter, run, 3, 'environment', 'b')
        event = self.event(4, 'passed')
        result = adapter.check_event(run['round_id'], run['step_id'], event)
        self.assertEqual((result['rating'], result['burden']), (2, .25))
        self.assertTrue(adapter.check_event(run['round_id'], run['step_id'], event)['duplicate'])
        with closing(backend._connect()) as con:
            before = dict(con.execute('SELECT * FROM training_memory').fetchone())
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_ratings').fetchone()[0], 1)
        state = backend.state(adapter.attempt_id)
        self.assertEqual(state['phase'], 'done'); self.assertIsNone(state['result']['self_rating'])
        early = self.open(backend); erun = self.launch(early)
        self.send(early, erun, 1, 'passed')
        self.assertTrue(backend.state(early.attempt_id)['result']['practice_only'])
        with closing(backend._connect()) as con:
            self.assertEqual(dict(con.execute('SELECT * FROM training_memory').fetchone()), before)
        self.now = datetime.fromisoformat(before['due_at']) + timedelta(seconds=1)
        review = self.open(backend); rrun = self.launch(review)
        self.send(review, rrun, 1, 'passed')
        with closing(backend._connect()) as con:
            after = con.execute('SELECT * FROM training_memory').fetchone()
            self.assertEqual(Card.from_json(before['card_json']).card_id, Card.from_json(after['card_json']).card_id)
            self.assertEqual(after['revision'], 2)

    def test_no_evidence_and_generic_judging_cannot_score(self):
        backend = self.native(); adapter = self.open(backend); run = self.launch(adapter)
        self.send(adapter, run, 1, 'environment')
        with self.assertRaises(DomainError): adapter.finish()
        with self.assertRaises(DomainError): backend.submit({'attempt_id': adapter.attempt_id,
            'request_id': str(uuid.uuid4()), 'answer': 'passed'})
        with self.assertRaises(DomainError): backend.reveal(adapter.attempt_id)
        with self.assertRaises(DomainError): self.send(adapter, run, 3, 'passed')
        with self.assertRaises(DomainError): self.send(adapter, run, 2, [])
        bad = self.event(2, 'passed'); bad['occurred_at'] = 'bad'
        with self.assertRaises(DomainError): adapter.check_event(run['round_id'], run['step_id'], bad)
        with closing(backend._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_memory').fetchone()[0], 0)
        self.send(adapter, run, 2, 'wrong')
        adapter.finish()
        self.assertEqual(backend.state(adapter.attempt_id)['result']['rating'], 1)

    def test_manual_four_ratings_and_automatic_again(self):
        backend = self.native()
        for rating in (4, 1, 2, 3):
            adapter = self.open(backend, 'manual'); self.launch(adapter)
            adapter.finish(rating); adapter.finish(rating)
            state = backend.state(adapter.attempt_id)
            self.assertEqual(state['result']['rating'], rating)
            self.now = datetime.fromisoformat(state['due_at']) + timedelta(seconds=1)
        adapter = self.open(backend); run = self.launch(adapter)
        for i in range(1, 4): self.send(adapter, run, i, 'wrong')
        self.assertEqual(self.send(adapter, run, 4, 'passed')['rating'], 1)

    def test_migration_exact_card_history_active_round_and_idempotency(self):
        legacy = AcademyLearningService(self.topic, self.root, learner_id='learner', clock=lambda: self.now)
        def action(**kw):
            s = legacy.state()
            return dict(request_id=str(uuid.uuid4()), expected_revision=s['revision'],
                        attempt_id=s['attempt_id'], step_id=s['active_step_id'], **kw)
        legacy.choose(action(choice='learn'))
        first = legacy.launch(action()); legacy.opened(first['round_id'], first['step_id'])
        legacy.check_event(first['round_id'], first['step_id'], self.event(1, 'passed'))
        legacy.advance(action())
        with closing(legacy._connect()) as con:
            saved = legacy._load(con)['points']['a']
        self.now = datetime.fromisoformat(saved['due_at']) + timedelta(seconds=1)
        legacy.choose(action(choice='review'))
        run = legacy.launch(action()); legacy.opened(run['round_id'], run['step_id'])
        legacy.check_event(run['round_id'], run['step_id'], self.event(1, 'wrong'))
        with closing(legacy._connect()) as con:
            original = con.execute('SELECT body FROM topics WHERE learner=?', ('learner',)).fetchone()[0]
            con.execute('CREATE TABLE academy_capabilities (learner TEXT,topic TEXT,version TEXT,step TEXT,round TEXT)')
            con.execute('INSERT INTO academy_capabilities VALUES (?,?,?,?,?)',
                ('learner', self.topic['id'], self.topic['version'], run['step_id'], run['round_id']))
        backend = self.native()
        with closing(backend._connect()) as con:
            migrated = con.execute('SELECT * FROM training_memory').fetchone()
            self.assertEqual((migrated['card_json'], migrated['due_at']), (saved['card_json'], saved['due_at']))
            capability = con.execute('SELECT * FROM academy_capabilities').fetchone()
            self.assertEqual(capability['topic'], 'training.' + self.course['id'])
            self.assertEqual(con.execute('SELECT body FROM topics WHERE learner=?', ('learner',)).fetchone()[0], original)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_attempts').fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT mode FROM training_attempts WHERE phase='done'").fetchone()[0], 'initial')
            self.assertEqual(con.execute("SELECT mode FROM training_attempts WHERE phase='answer'").fetchone()[0], 'review')
        adapter = AcademyTraining(backend, capability['step'])
        self.assertEqual(adapter.claim(run['round_id'])['next_sequence'], 2)
        self.assertEqual(adapter.claim(run['round_id'])['version'], self.topic['version'])
        self.assertEqual(self.send(adapter, {'round_id': run['round_id'], 'step_id': adapter.attempt_id}, 2, 'passed')['rating'], 2)
        self.assertEqual(install(self.root, self.course['id'])['status'], 'unchanged')

    def test_shared_api_round_and_review_routing(self):
        self.native()
        settings = Settings(data_dir=self.root, public_api_origin='https://native.test')
        app = create_app(settings, atomic_clock=lambda: self.now)
        with TestClient(app, base_url='https://native.test') as client:
            headers = {'origin': 'https://native.test'}
            def call(method, path, data=None, h=None, status=200):
                response = client.request(method, '/api/' + path, headers=h or headers,
                    **({'json': data} if data is not None else {}))
                self.assertEqual(response.status_code, status, response.text)
                return response.json()
            identity = call('POST', 'access/guest', {})
            headers['x-learning-identity'] = identity['identity_token']
            banks = call('GET', 'training/catalog?subject_id=cs')['banks']
            self.assertEqual([b['id'] for b in banks], [self.course['id']])
            self.assertNotIn('kind', banks[0])
            redirect = call('GET', 'state?topic_id=' + self.topic['id'])
            self.assertEqual(redirect['training_target']['bank_id'], self.course['id'])
            call('GET', 'training/bank?subject_id=math&bank_id=' + self.course['id'], status=403)
            state = call('POST', 'training/open?subject_id=cs', {'request_id': str(uuid.uuid4()),
                'bank_id': self.course['id'], 'group_id': 'lesson', 'question_id': self.topic['id']})
            self.assertEqual(state['phase'], 'answer')
            launch = call('POST', 'academy/training/launch?subject_id=cs', {'attempt_id': state['attempt_id']})
            plugin = {'origin': 'https://native.test', 'authorization': 'Bearer ' + launch['url'].split('ticket=')[1]}
            claim = call('POST', 'academy/claim', {}, plugin)
            self.assertEqual(claim['version'], self.topic['version'])
            call('POST', 'academy/event', {'kind': 'opened'}, plugin)
            event = self.event(1, 'passed')
            self.assertEqual(call('POST', 'academy/event', {'kind': 'check', 'event': event}, plugin)['rating'], 3)
            self.assertTrue(call('POST', 'academy/event', {'kind': 'check', 'event': event}, plugin)['duplicate'])
            done = call('GET', 'training/state?attempt_id=' + state['attempt_id'])
            self.now = datetime.fromisoformat(done['due_at']) + timedelta(seconds=1)
            legacy = AcademyLearningService(self.topic, self.root, learner_id=identity['access']['learner_id'])
            with legacy._transaction() as con:
                archived = legacy._load(con)
                archived['completed_at'] = (self.now - timedelta(days=1)).isoformat()
                for point in archived['points'].values():
                    point.update(mastered=True, due_at=archived['completed_at'])
                legacy._save(con, archived)
            self.assertEqual(call('GET', 'atomic/reviews?subject_id=cs')['items'], [])
            inbox = call('GET', 'review-papers/inbox?subject_id=cs')
            self.assertEqual(inbox['due_count'], 1)
            self.assertEqual(inbox['items'], [])
            self.assertTrue(inbox['legacy'][0]['external'])
            self.assertEqual(inbox['trees'][0]['kind'], 'training')
            call('POST', 'academy/launch?topic_id=' + self.topic['id'], {'request_id': str(uuid.uuid4()),
                'expected_revision': 0, 'attempt_id': 'old', 'step_id': 'old'}, status=409)
            call('POST', 'access/logout', {})
            call('POST', 'academy/claim', {}, plugin, status=401)

    def test_partial_permissions_abort_without_publishing_native_bank(self):
        from graspmemoedu.access import AccessStore
        store = AccessStore(self.root, [{'id': t['id'], 'title': t['title']} for t in self.topics.values()])
        store.create_account('Subset', topic_ids=[self.topic['id']])
        with self.assertRaisesRegex(ValueError, 'Partial legacy'):
            install(self.root, self.course['id'])
        self.assertEqual(TrainingCatalog(self.root).banks, {})

    def test_test_material_marker_precedes_native_visibility(self):
        from graspmemoedu.test_materials import TestMaterials
        (self.root / 'test-materials.json').write_bytes(encoded({'schema': 1, 'courses': [self.course['id']]}))
        self.native()
        policy = TestMaterials(self.root)
        self.assertIn(self.course['id'], policy.banks)
        self.assertFalse(policy.topic_visible('training.' + self.course['id'], {'role': 'guest'}))

    def test_portable_backup_keeps_active_external_round_and_scheduled_card(self):
        from graspmemoedu.site_backup import backup_site, restore_site
        AcademyLearningService(self.topic, self.root, learner_id='archive-source')
        backend = self.native()
        adapter = self.open(backend); run = self.launch(adapter)
        self.send(adapter, run, 1, 'passed')
        pending = self.open(backend, 'manual'); active = self.launch(pending)
        saved = backend.state(adapter.attempt_id)
        archive = self.root / 'site.zip'
        backup_site(Settings.load(self.root), archive)
        restored = self.root / 'restored'; restore_site(archive, restored)
        other = TrainingService(TrainingCatalog(restored), restored, backend.learner_id, clock=lambda: self.now)
        self.assertEqual(other.state(adapter.attempt_id), saved)
        self.assertEqual(AcademyTraining(other, pending.attempt_id).claim(active['round_id'])['next_sequence'], 1)
        AcademyTraining(other, pending.attempt_id).finish(3)
        self.assertEqual(other.state(pending.attempt_id)['result']['rating'], 3)


if __name__ == '__main__':
    unittest.main()
