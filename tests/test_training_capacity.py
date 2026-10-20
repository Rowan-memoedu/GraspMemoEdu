from copy import deepcopy
import unittest

from graspmemoedu.subject_registry import SUBJECT_IDS
from graspmemoedu.training_content import MAX_GROUPS, fingerprint, validate
from tests.training_fixtures import training_bank


class TrainingCapacityTests(unittest.TestCase):
    def test_large_source_hierarchy_uses_the_same_contract_for_every_subject(self):
        for subject in SUBJECT_IDS:
            with self.subTest(subject=subject):
                bank = training_bank(subject)
                bank['groups'].extend(
                    {'id': f'chapter-{index}', 'title': f'Chapter {index}',
                     'parent_id': 'chapter', 'questions': []}
                    for index in range(254))
                bank['version'] = fingerprint(bank)
                self.assertEqual(len(validate(bank)['groups']), 257)
                broken = deepcopy(bank)
                broken['groups'][-1]['id'] = broken['groups'][-2]['id']
                broken['version'] = fingerprint(broken)
                with self.assertRaisesRegex(ValueError, 'Duplicate or invalid group'):
                    validate(broken)

    def test_group_and_total_question_bounds_remain_enforced(self):
        bank = training_bank()
        bank['groups'].extend(
            {'id': f'chapter-{index}', 'title': f'Chapter {index}',
             'parent_id': 'chapter', 'questions': []}
            for index in range(MAX_GROUPS - len(bank['groups']) + 1))
        bank['version'] = fingerprint(bank)
        with self.assertRaisesRegex(ValueError, 'Question group count'):
            validate(bank)
        bank = training_bank()
        question = bank['groups'][1]['questions'][0]
        bank['groups'] = []
        for offset in range(0, 5001, 500):
            bank['groups'].append(
                {'id': f'group-{offset}', 'title': f'Group {offset}', 'parent_id': None,
                 'questions': [{**question, 'id': f'question-{index}'}
                               for index in range(offset, min(offset + 500, 5001))]})
        bank['version'] = fingerprint(bank)
        with self.assertRaisesRegex(ValueError, 'Bank must contain'):
            validate(bank)


if __name__ == '__main__':
    unittest.main()
