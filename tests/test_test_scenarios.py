from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import unittest

from graspmemoedu.access import AccessStore
from graspmemoedu.test_scenarios import apply_plan, validate_plan
from tests.fixtures import temporary_test_directory
from training_fixtures import training_bank
from test_atomic_api import write_catalog


class TestScenarioTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); write_catalog(self.root)
        bank = training_bank()
        (self.root / 'training-banks').mkdir()
        (self.root / 'training-banks/isolated-bank.json').write_text(json.dumps(bank), encoding='utf-8')
        (self.root / 'test-materials.json').write_text(json.dumps({'schema': 1, 'topics': ['atomic-english', 'atomic-chemistry'], 'banks': ['isolated-bank']}), encoding='utf-8')
        self.store = AccessStore(self.root, [])
        self.test = self.store.create_account('Student', purpose='test', is_advanced_learner=True, is_student=True)['account']['learner_id']
        self.formal = self.store.create_account('Formal', is_advanced_learner=True, is_student=True)['account']['learner_id']
        self.plan = {'schema': 1, 'learners': [self.test], 'topics': {'atomic-english': 'due', 'atomic-chemistry': 'paused'},
                     'banks': {'isolated-bank': {'due': ['choice', 'blank'], 'stopped': ['exact'], 'manual_stop': ['essay']}}}

    def test_presets_are_repeatable_and_preserve_outside_records(self):
        from graspmemoedu.atomic import AtomicLearningService
        from graspmemoedu.catalog import Catalog
        from graspmemoedu.config import Settings
        course = Catalog.load(Settings.load(self.root)).topics['atomic-english']
        formal = AtomicLearningService(course, self.root, learner_id=self.formal)
        def stored():
            with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
                return con.execute('SELECT body FROM topics WHERE learner=? AND topic=?', (self.formal, 'atomic-english')).fetchone()[0]
        before = stored()
        first = apply_plan(self.root, self.plan, self.root / 'admin/first.sqlite3')
        self.assertEqual(first['status'], 'test_scenarios_ready')
        self.assertEqual(before, stored())
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            state = json.loads(con.execute('SELECT body FROM topics WHERE learner=? AND topic=?', (self.test, 'atomic-english')).fetchone()[0])
            self.assertTrue(all(p['mastered'] and p['card_json'] is None and datetime.fromisoformat(p['due_at']) < datetime.now(timezone.utc) for p in state['points'].values()))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_suspensions WHERE learner=?', (self.test,)).fetchone()[0], 2)
            old_count = con.execute('SELECT COUNT(*) FROM training_attempts WHERE learner=?', (self.test,)).fetchone()[0]
        apply_plan(self.root, self.plan, self.root / 'admin/second.sqlite3')
        self.assertEqual(before, stored())
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_attempts WHERE learner=?', (self.test,)).fetchone()[0], old_count)

    def test_real_accounts_and_unflagged_materials_are_rejected_without_writes(self):
        for plan in [{**self.plan, 'learners': [self.formal]},
                     {**self.plan, 'topics': {'atomic-biology': 'due'}},
                     {**self.plan, 'banks': {'not-a-test': {}}}]:
            backup = self.root / 'admin/rejected.sqlite3'
            with self.assertRaises(ValueError):
                apply_plan(self.root, plan, backup)
            self.assertFalse(backup.exists())


if __name__ == '__main__':
    unittest.main()
