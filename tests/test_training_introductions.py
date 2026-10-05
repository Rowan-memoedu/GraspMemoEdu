"""Bank guidance is readable without creating attempts, cards or forced introductions."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest

from tests.fixtures import temporary_test_directory
from training_fixtures import training_bank
from graspmemoedu.training_content import fingerprint, validate, TrainingCatalog
from graspmemoedu.training import TrainingService


class TrainingIntroductionTests(unittest.TestCase):
    def bank(self):
        bank = training_bank()
        intro = {'id': 'guide-a', 'title': 'Introduction', 'text': '原书知识', 'html': '<p>原书知识</p>'}
        group = bank['groups'][1]
        group['introductions'] = [intro]
        group['content_order'] = [{'type': 'introduction', 'id': intro['id']}] + [
            {'type': 'question', 'id': q['id']} for q in group['questions']]
        bank['version'] = fingerprint(bank)
        return bank

    def test_mixed_no_intro_and_intro_only_groups_are_read_only(self):
        bank = self.bank()
        only = deepcopy(bank['groups'][1])
        only.update(id='only-guidance', questions=[], parent_id='chapter')
        only['introductions'][0]['id'] = 'guide-only'
        only['content_order'] = [{'type': 'introduction', 'id': 'guide-only'}]
        bank['groups'].append(only)
        bank['version'] = fingerprint(bank); validate(bank)
        with temporary_test_directory() as folder:
            root = Path(folder); (root / 'training-banks').mkdir()
            (root / 'training-banks/isolated-bank.json').write_text(json.dumps(bank), encoding='utf-8')
            service = TrainingService(TrainingCatalog(root), root, 'isolated-learner')
            self.assertEqual(service.group(bank['id'], 'only-guidance')['introductions'], only['introductions'])
            self.assertEqual(service.group(bank['id'], 'group-b')['questions'][0]['id'], 'essay-b')
            self.assertEqual(service.bank(bank['id'])['groups'][-1]['introduction_count'], 1)
            with closing(sqlite3.connect(root / 'learning.sqlite3')) as con:
                for table in ('training_attempts', 'training_memory', 'training_ratings'):
                    self.assertEqual(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    def test_whole_bank_can_be_introduction_only(self):
        bank = self.bank(); bank['groups'] = bank['groups'][1:2]
        bank['groups'][0].update(parent_id=None, questions=[], content_order=[{'type': 'introduction', 'id': 'guide-a'}])
        bank['version'] = fingerprint(bank); validate(bank)

    def test_order_must_cover_each_content_once(self):
        bank = self.bank(); bank['groups'][1]['content_order'].append(bank['groups'][1]['content_order'][0])
        bank['version'] = fingerprint(bank)
        with self.assertRaisesRegex(ValueError, 'content order'):
            validate(bank)

    def test_introduction_ids_are_global_and_content_required(self):
        bank = self.bank(); bank['groups'][1]['introductions'][0]['html'] = ''
        bank['version'] = fingerprint(bank)
        with self.assertRaisesRegex(ValueError, 'Introduction content'):
            validate(bank)


if __name__ == '__main__':
    unittest.main()
