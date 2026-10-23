from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid

from fsrs import Card, State
from graspmemoedu.atomic import AtomicLearningService
from graspmemoedu.atomic_review import apply_review
from graspmemoedu.dependencies import DependencyPolicy
from graspmemoedu.linked_points import snapshot, eligibility, validate_unlock
from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import TrainingCatalog, fingerprint, validate
from graspmemoedu.service import DomainError
from graspmemoedu.subject_registry import SUBJECT_IDS
from atomic_fixtures import atomic_topic
from tests.fixtures import temporary_test_directory


class LinkedPointsTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.when = datetime(2026, 10, 23, tzinfo=timezone.utc)
        self.topics = {tid: atomic_topic('english', tid) for tid in ('first', 'second')}
        self.catalog = SimpleNamespace(topics=self.topics, topic=lambda tid: self.topics[tid],
            metadata=lambda tid: {'prerequisites': []})
        self.atoms = {tid: AtomicLearningService(topic, self.root, learner_id='student', clock=lambda: self.when)
                      for tid, topic in self.topics.items()}
        self.refs = ['first/a', 'second/b']
        question = deepcopy(self.topics['first']['flashcards'][-1])
        question.update(id='goal', point_ids=self.refs,
            assessment={'version': 1, 'item_points': {'a': ['first/a'], 'b': ['second/b']}})
        self.bank = {'schema': 1, 'id': 'linked-bank', 'subject_id': 'english', 'title': 'Synthetic bank',
            'groups': [{'id': 'goal-group', 'title': 'Goal', 'questions': [question],
                        'unlock_policy': {'point_ids': [*self.refs, 'first/c']}},
                       {'id': 'ordinary', 'title': 'Ordinary', 'questions': [{**deepcopy(question), 'id': 'ordinary'}]}]}
        self.bank['version'] = fingerprint(self.bank)
        validate(self.bank)
        (self.root / 'training-banks').mkdir()
        (self.root / 'training-banks/linked-bank.json').write_text(json.dumps(self.bank), encoding='utf-8')
        self.banks = TrainingCatalog(self.root)
        self.access = {'learner_id': 'student', 'is_admin': False,
                       'topics': [{'id': tid} for tid in [*self.topics, 'training.linked-bank']]}
        self.dependencies = DependencyPolicy(self.catalog, None, training=self.banks)
        self.training = TrainingService(self.banks, self.root, 'student', clock=lambda: self.when,
            dependencies=self.dependencies, access=self.access, atomic_for=self.atoms.__getitem__)

    def seed(self, stability=10):
        for backend in self.atoms.values():
            with backend._transaction() as con:
                state = backend._load(con)
                state.update(status='completed', completed_at=(self.when - timedelta(days=1)).isoformat(),
                             mode='review', phase='complete', active=None)
                for local, memory in state['points'].items():
                    card = Card(state=State.Review, stability=stability, difficulty=5,
                                last_review=self.when - timedelta(days=1), due=self.when + timedelta(days=9))
                    memory.update(mastered=True, first_passed_at=(self.when - timedelta(days=5)).isoformat(),
                                  card_json=card.to_json(), due_at=card.due.isoformat(), last_rating=3)
                    state['history'].append({'id': 'seed-' + local, 'card_id': local + '-1', 'point_ids': [local],
                                             'mode': 'review', 'ratings': {local: 3},
                                             'answered_at': (self.when - timedelta(days=1)).isoformat()})
                backend._save(con, state)

    def status(self):
        with closing(self.training._connect()) as con:
            return self.training.group_status(con, 'linked-bank', 'goal-group')

    def answer(self, question='goal', group='goal-group', values=None):
        attempt = self.training.open('linked-bank', group, question, str(uuid.uuid4()))
        submission = self.training.submit({'request_id': str(uuid.uuid4()), 'attempt_id': attempt,
            'answer': json.dumps(values or {'a': '红色', 'b': '蓝色'}), 'elapsed_ms': 8000})
        self.training.process_submission(submission)
        return self.training.state(attempt)

    def test_dynamic_unlock_including_time_decay_and_never_learned(self):
        self.assertFalse(self.status()['unlocked'])
        with self.assertRaises(DomainError):
            self.training.open('linked-bank', 'goal-group', 'goal', 'blocked')
        self.seed()
        self.assertTrue(self.status()['unlocked'])
        self.when += timedelta(days=20)
        status = self.status()
        self.assertFalse(status['unlocked'])
        self.assertTrue(all(p['reason'] == 'retrievability_low' for p in status['point_prerequisites']))

    def test_first_learning_or_low_stability_does_not_unlock(self):
        self.seed(stability=6.99)
        self.assertFalse(self.status()['unlocked'])
        self.seed()
        with self.atoms['first']._transaction() as con:
            state = self.atoms['first']._load(con)
            state['history'] = []
            self.atoms['first']._save(con, state)
        self.assertEqual(self.status()['point_prerequisites'][0]['reason'], 'needs_spaced_review')

    def test_partial_answer_updates_only_assessed_original_points_and_relocks(self):
        self.seed()
        with closing(self.training._connect()) as con:
            before = snapshot(con, self.catalog, 'student', [*self.refs, 'first/c'])
        result = self.answer(values={'a': 'wrong', 'b': '蓝色'})['result']
        self.assertEqual(result['ratings'], {'first/a': 1, 'second/b': 3})
        self.assertTrue(result['scheduled'])
        self.assertFalse(self.status()['unlocked'])
        with closing(self.training._connect()) as con:
            after = snapshot(con, self.catalog, 'student', [*self.refs, 'first/c'])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_memory').fetchone()[0], 0)
        self.assertEqual(before[2]['memory'], after[2]['memory'])
        self.assertNotEqual(before[0]['memory'], after[0]['memory'])
        self.assertNotEqual(before[1]['memory'], after[1]['memory'])

    def test_repeat_before_due_is_practice_and_does_not_farm_stability(self):
        self.seed()
        self.assertTrue(self.answer()['result']['scheduled'])
        result = self.answer()['result']
        self.assertTrue(result['practice_only'])
        self.assertEqual(result['ratings'], {})

    def test_stale_result_does_not_overwrite_newer_atomic_memory(self):
        self.seed()
        attempt = self.training.open('linked-bank', 'goal-group', 'goal', 'stale')
        submission = self.training.submit({'request_id': 'stale-answer', 'attempt_id': attempt,
            'answer': json.dumps({'a': '红色', 'b': '蓝色'}), 'elapsed_ms': 100})
        with self.atoms['first']._transaction() as con:
            state = self.atoms['first']._load(con)
            state['points']['a']['last_rating'] = 1
            self.atoms['first']._save(con, state)
        self.training.process_submission(submission)
        result = self.training.state(attempt)['result']
        self.assertTrue(result['state_changed'])
        self.assertFalse(result['scheduled'])

    def test_ordinary_bundle_uses_same_atomic_ratings_when_due(self):
        self.seed()
        early = self.answer('ordinary', 'ordinary')['result']
        self.assertTrue(early['practice_only'])
        for backend in self.atoms.values():
            with backend._transaction() as con:
                state = backend._load(con)
                for memory in state['points'].values():
                    memory['due_at'] = (self.when - timedelta(seconds=1)).isoformat()
                backend._save(con, state)
        result = self.answer('ordinary', 'ordinary', {'a': 'wrong', 'b': '蓝色'})['result']
        self.assertEqual(result['ratings'], {'first/a': 1, 'second/b': 3})

    def test_permissions_and_all_subjects_share_thresholds(self):
        self.seed()
        self.access['topics'] = [t for t in self.access['topics'] if t['id'] != 'second']
        self.assertFalse(self.status()['unlocked'])
        self.assertEqual(self.status()['point_prerequisites'][1]['text'], '')
        self.access['topics'].append({'id': 'second'})
        for subject in SUBJECT_IDS:
            with self.subTest(subject=subject):
                for topic in self.topics.values():
                    topic['subject_id'] = subject
                self.assertTrue(self.status()['unlocked'])

    def test_invalid_contract_is_rejected(self):
        for policy in ({'point_ids': ['first/a'], 'min_retrievability': 2},
                       {'point_ids': ['first/a'], 'min_successful_reviews': True},
                       {'point_ids': ['first/a', 'first/a']}):
            with self.assertRaises(ValueError):
                validate_unlock(policy)

    def test_report_counts_one_bank_answer_even_when_several_topics_are_updated(self):
        from graspmemoedu.student_reports import StudentReport
        self.seed()
        self.answer()
        owner = SimpleNamespace(catalog=self.catalog, training=self.banks,
            access=SimpleNamespace(test_materials=SimpleNamespace(topic_visible=lambda *args: True)))
        with closing(self.training._connect()) as con:
            rows = {r['topic']: r for r in con.execute('SELECT * FROM topics WHERE learner=?', ('student',))}
            tables = self.dependencies.tables(con)
            report = StudentReport(owner, con, self.access, {'learner_id': 'student'}, rows, tables, self.when)
        self.assertEqual(len(report.events), 1)
        self.assertEqual(report.events[0]['kind'], 'training')
        self.assertEqual(report.events[0]['elapsed_ms'], 8000)

    def test_content_revision_preserves_external_bank_snapshot(self):
        from graspmemoedu.atomic_content_revision import revised_state
        self.seed()
        self.answer()
        backend = self.atoms['first']
        with closing(backend._connect()) as con:
            body = con.execute('SELECT body FROM topics WHERE learner=? AND topic=?', ('student', 'first')).fetchone()[0]
        revised = revised_state(body, self.topics['first'], self.topics['first'])
        self.assertEqual(revised['points'], json.loads(body)['points'])
        self.assertEqual(revised['history'][-1]['snapshot']['training_source']['bank_id'], 'linked-bank')

    def test_bank_history_is_not_exposed_through_a_topic(self):
        self.seed()
        self.answer()
        backend = self.atoms['first']
        with closing(backend._connect()) as con:
            state = backend._load(con)
        external = [r for r in state['history'] if (r.get('snapshot') or {}).get('training_source')]
        self.assertTrue(external)
        state['history'] = external
        self.assertEqual(backend._public(state)['history'], [])

    def test_backup_restore_preserves_original_points_and_one_bank_attempt(self):
        from graspmemoedu.site_backup import backup_site, restore_site
        from graspmemoedu.config import Settings
        import sqlite3
        for tid, topic in self.topics.items():
            (self.root / (tid + '.json')).write_text(json.dumps(topic), encoding='utf-8')
        (self.root / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'course', 'title': 'Fixture',
            'topics': [{'id': tid, 'path': tid + '.json'} for tid in self.topics]}]}), encoding='utf-8')
        (self.root / 'subjects.json').write_text(json.dumps({'subjects': [{'id': 'english', 'course_ids': ['course']}]}), encoding='utf-8')
        self.seed(); self.answer()
        archive, restored = self.root / 'backup.zip', self.root / 'restored'
        backup_site(Settings(data_dir=self.root), archive)
        restore_site(archive, restored)
        with closing(self.training._connect()) as original, closing(sqlite3.connect(restored / 'learning.sqlite3')) as copied:
            for table in ('topics', 'training_attempts', 'training_ratings'):
                self.assertEqual([tuple(r) for r in original.execute('SELECT * FROM ' + table + ' ORDER BY rowid')],
                                 copied.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall())

    def test_due_bank_bundle_is_a_shared_paper_presentation_with_atomic_reservations(self):
        from graspmemoedu.review_papers import PaperService
        self.seed()
        self.access.update(features=['learn', 'submit_answer', 'review_history'], role='account')
        for backend in self.atoms.values():
            with backend._transaction() as con:
                state = backend._load(con)
                for memory in state['points'].values():
                    memory['due_at'] = (self.when - timedelta(seconds=1)).isoformat()
                backend._save(con, state)
        papers = PaperService(self.training, self.catalog, self.dependencies, self.access, [], self.atoms.__getitem__)
        with self.training.transaction() as con:
            cards, _ = papers.sources.inventory(con, 'english', self.when)
            item = next(c for c in cards if c['source'].get('local_card') == 'ordinary')
            self.assertEqual(len(item['point_ids']), 2)
            self.assertTrue(all(p.startswith('point-') for p in item['point_ids']))
            paper = {'id': 'synthetic-paper', 'created_at': self.when.isoformat()}
            con.execute('''INSERT INTO review_papers (id,learner,subject,candidate,title,mode,phase,revision,
                created_at,expires_at,items) VALUES (?,?,?,?,?,?,'grading',0,?,?,?)''',
                (paper['id'], 'student', 'english', 'test', 'test', 'paper', paper['created_at'],
                 (self.when + timedelta(hours=1)).isoformat(), '[]'))
            for p in item['point_ids']:
                con.execute('INSERT INTO review_reservations VALUES (?,?,?)', ('student', p, paper['id']))
            papers.sources.begin(con, [item], 'english', paper['id'], paper['created_at'])
            self.assertTrue(papers.sources.unchanged(con, item))
            item['answer'] = json.dumps({'a': 'wrong', 'b': '蓝色'})
            result = papers.sources.apply(con, paper, 0, item, (False, 'partial'), paper['created_at'])
            self.assertEqual(list(result['ratings'].values()), [1, 3])
            self.assertTrue(result['scheduled'])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_memory').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
