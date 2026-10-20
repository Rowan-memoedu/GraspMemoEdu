from contextlib import closing
import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.bank_placement import place_in_question_bank
from graspmemoedu.academy_training_import import install as migrate_bank
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.service import DomainError
from graspmemoedu.topic_install import encoded
from test_academy import exercise
import test_training_api as helpers


class AtomicBankTests(unittest.TestCase):
    account = helpers.TrainingAPITests.account
    call = helpers.TrainingAPITests.call

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        a, b = exercise(), exercise('manual')
        b['id'] = 'second-exercise'
        reading = {'id': 'reading', 'title': 'Instructions', 'subject_id': 'cs', 'version': 'v1',
                   'topic_format_version': 2, 'topic_kind': 'introduction', 'content_version': 'v1',
                   'training_mode': 'paradigm', 'modules': [], 'math_css': '',
                   'introductions': [{'id': 'intro', 'title': 'Read me', 'html': '<p>Read me</p>'}],
                   'content_order': [{'kind': 'introduction', 'id': 'intro'}]}
        self.contents = {t['id']: t for t in (a, b, reading)}
        for tid, topic in self.contents.items():
            (self.root / (tid + '.json')).write_bytes(encoded(topic))
        self.units = [{'id': 'section', 'title': 'Section', 'parent_id': None, 'topic_ids': []},
                      {'id': 'lesson', 'title': 'Lesson', 'parent_id': 'section', 'topic_ids': list(self.contents)}]
        self.course = {'id': 'practice', 'title': 'Practice', 'topics': [
            {'id': tid, 'path': tid + '.json'} for tid in self.contents], 'units': self.units}
        (self.root / 'catalog.json').write_bytes(encoded({'courses': [self.course]}))
        (self.root / 'subjects.json').write_bytes(encoded({'subjects': [{'id': 'cs', 'course_ids': ['practice']}]}))
        self.headers = {'Origin': 'https://bank.test'}
        self.clients = []
        self.addCleanup(lambda: [c.__exit__(None, None, None) for c in self.clients])
        self.open_app()
        login = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.learner = login['access']['learner_id']

    def open_app(self):
        self.settings = Settings.load(self.root)
        self.settings.public_api_origin = 'https://bank.test'
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, base_url='https://bank.test')
        self.client.__enter__()
        self.clients.append(self.client)

    def rows(self):
        with closing(self.app.state.access_store._connect()) as con:
            return {name: [tuple(r) for r in con.execute('SELECT * FROM ' + name + ' ORDER BY rowid')]
                    for name in ('topics', 'submissions', 'events', 'training_memory')}

    def legacy_placement(self):
        """Reproduce an already-published legacy projection, not a new import."""
        old = copy.deepcopy(self.course)
        old['placement'] = 'question_bank'
        (self.root / 'catalog.json').write_bytes(encoded({'courses': [old]}))

    def test_placement_only_rejected_and_explicit_migration_preserves_evidence(self):
        self.call('POST', '/api/catalog/select?subject_id=cs', {'course_id': 'practice'})
        self.call('GET', '/api/state?topic_id=academy-fixture&subject_id=cs')
        backend = self.app.state.learning_services[(self.learner, 'academy-fixture')]
        def payload(**extra):
            state = backend.state()
            return dict(request_id=str(uuid.uuid4()), expected_revision=state['revision'],
                        attempt_id=state['attempt_id'], step_id=state['active_step_id'], **extra)
        backend.choose(payload(choice='learn'))
        run = backend.launch(payload())
        backend.opened(run['round_id'], run['step_id'])
        backend.check_event(run['round_id'], run['step_id'], {
            'id': str(uuid.uuid4()), 'sequence': 1, 'category': 'passed', 'code_hash': 'a' * 64,
            'occurred_at': backend._time().isoformat()})
        before = self.rows()
        with self.assertRaisesRegex(DomainError, '完整题库转换'):
            place_in_question_bank(self.root, 'practice')
        self.assertEqual(before, self.rows())
        self.assertFalse((self.root / 'active-content.json').exists())
        self.legacy_placement()
        with self.assertRaisesRegex(DomainError, '完整题库转换'):
            create_app(Settings.load(self.root))
        self.assertEqual(before, self.rows())
        migrate_bank(self.root, 'practice')
        loaded = Catalog.load(Settings.load(self.root))
        self.assertEqual(loaded.topics, self.contents)
        self.assertEqual(loaded.courses['practice']['units'], self.units)
        self.assertEqual(place_in_question_bank(self.root, 'practice')['status'], 'unchanged')
        self.open_app()
        catalog = self.call('GET', '/api/catalog?subject_id=cs')
        self.assertEqual(catalog, {'courses': [], 'selected_course_id': None})
        self.call('GET', '/api/dashboard?course_id=practice&subject_id=cs', status=404)
        banks = self.call('GET', '/api/training/catalog?subject_id=cs')['banks']
        self.assertEqual([b['id'] for b in banks], ['practice'])
        bank = self.call('GET', '/api/training/bank?bank_id=practice&subject_id=cs')
        self.assertEqual([(g['id'], g.get('parent_id')) for g in bank['groups']],
                         [('section', None), ('lesson', 'section')])
        self.assertEqual(sum(g['question_count'] for g in bank['groups']), 2)
        self.assertEqual(sum(g['introduction_count'] for g in bank['groups']), 1)
        self.assertEqual(sum(g['completed_count'] for g in bank['groups']), 1)
        after_rows = self.rows()
        for name in ('topics', 'submissions', 'events'):
            self.assertEqual(before[name], after_rows[name])
        self.assertEqual(len(after_rows['training_memory']), 1)
        after = self.call('GET', '/api/state?topic_id=academy-fixture&subject_id=cs')
        self.assertEqual(after['training_target']['bank_id'], 'practice')
        self.assertIn('question_id', after['training_target'])

    def test_permissions_subject_and_test_materials_are_not_bypassed(self):
        self.legacy_placement()
        migrate_bank(self.root, 'practice')
        self.open_app()
        self.call('GET', '/api/training/bank?bank_id=practice&subject_id=math', status=403)
        self.assertEqual(self.call('GET', '/api/training/catalog?subject_id=math')['banks'], [])
        self.account(topic_ids=['training.practice'], is_student=True)
        bank = self.call('GET', '/api/training/bank?bank_id=practice&subject_id=cs')
        self.assertEqual(sum(g['question_count'] for g in bank['groups']), 2)
        self.account(topic_ids=[])
        self.assertEqual(self.call('GET', '/api/training/catalog?subject_id=cs')['banks'], [])
        self.call('GET', '/api/training/bank?bank_id=practice', status=403)
        active = Settings.load(self.root).content_dir
        (active / 'test-materials.json').write_bytes(encoded({'schema': 1, 'courses': ['practice'], 'banks': ['practice']}))
        self.open_app()
        self.account(is_admin=True, purpose='learner')
        self.assertEqual(self.call('GET', '/api/training/catalog')['banks'], [])
        self.account(is_admin=True, purpose='test')
        self.assertEqual(len(self.call('GET', '/api/training/catalog')['banks']), 1)

    def test_invalid_bank_cannot_activate(self):
        raw = copy.deepcopy(self.course)
        raw['topics'][1]['prerequisites'] = ['academy-fixture']
        (self.root / 'catalog.json').write_bytes(encoded({'courses': [raw]}))
        before = (self.root / 'catalog.json').read_bytes()
        with self.assertRaises(DomainError):
            place_in_question_bank(self.root, 'practice')
        self.assertEqual((self.root / 'catalog.json').read_bytes(), before)
        self.assertFalse((self.root / 'active-content.json').exists())

    def test_incomplete_mapping_and_dual_ownership_fail_before_state_initialization(self):
        self.legacy_placement()
        migrate_bank(self.root, 'practice')
        from graspmemoedu.training_content import fingerprint
        bank_path = self.root / 'training-banks/practice.json'
        bank_bytes = bank_path.read_bytes()
        before = self.rows()
        broken = json.loads(bank_bytes)
        question = next(q for group in broken['groups'] for q in group.get('questions', []))
        del question['legacy_topic_id']
        broken['version'] = fingerprint(broken)
        bank_path.write_bytes(encoded(broken))
        with self.assertRaisesRegex(DomainError, '完整保留'):
            create_app(Settings.load(self.root))
        self.assertEqual(before, self.rows())
        bank_path.write_bytes(bank_bytes)
        catalog_path = Settings.load(self.root).content_dir / 'catalog.json'
        catalog = json.loads(catalog_path.read_bytes())
        extra = copy.deepcopy(catalog['courses'][0])
        extra['id'] = 'another-course'
        extra.pop('placement')
        catalog['courses'].append(extra)
        catalog_path.write_bytes(encoded(catalog))
        subjects_path = Settings.load(self.root).content_dir / 'subjects.json'
        subjects = json.loads(subjects_path.read_bytes())
        next(s for s in subjects['subjects'] if s['id'] == 'cs')['course_ids'].append(extra['id'])
        subjects_path.write_bytes(encoded(subjects))
        with self.assertRaisesRegex(DomainError, '两套流程'):
            create_app(Settings.load(self.root))
        self.assertEqual(before, self.rows())
