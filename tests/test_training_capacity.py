from copy import deepcopy
import json
from pathlib import Path
import unittest

from graspmemoedu.subject_registry import SUBJECT_IDS
from graspmemoedu.question_bank import digest
from graspmemoedu.training_content import (MAX_GROUPS, TrainingCatalog, fingerprint,
                                          install, validate, validate_archive)
from graspmemoedu.training_format import install as install_format
from tests.fixtures import temporary_test_directory
from tests.training_fixtures import training_bank


class TrainingCapacityTests(unittest.TestCase):
    def test_bank_and_format_over_32mb_install_read_and_archive(self):
        with temporary_test_directory() as folder:
            root = Path(folder)
            bank = training_bank()
            template = bank['groups'][1]['questions'][0]
            bank['groups'][1]['questions'] = [
                {**template, 'id': f'large-{index}', 'html': '<p>' + 'x' * 800000 + '</p>'}
                for index in range(40)]
            bank['version'] = fingerprint(bank)
            candidate = root / 'candidate.json'
            candidate.write_text(json.dumps(bank, ensure_ascii=False), encoding='utf-8')
            self.assertGreater(candidate.stat().st_size, 32000000)
            installed = install(candidate, root)
            self.assertLess(installed.stat().st_size, 64000000)
            self.assertEqual(TrainingCatalog(root).banks[bank['id']], bank)
            value = {'schema': 1, 'bank_id': bank['id'], 'bank_version': bank['version'],
                     'flatten': [], 'questions': {q['id']: {
                         'source_sha256': digest(q), 'question': q['question'], 'html': q['html'],
                         'interaction': {'version': 1, 'type': 'text', 'grading': 'exact', 'answers': ['2']}}
                         for q in bank['groups'][1]['questions']}}
            candidate.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            self.assertGreater(candidate.stat().st_size, 32000000)
            self.assertEqual(install_format(candidate, root), 'installed')
            catalog = TrainingCatalog(root)
            self.assertEqual(catalog.formats[bank['id']], value)
            blobs = catalog.archive_blobs()
            self.assertEqual(validate_archive(blobs), set(blobs))

    def test_file_limit_accepts_64mb_and_rejects_one_extra_byte(self):
        with temporary_test_directory() as folder:
            root = Path(folder)
            bank = training_bank()
            bank_raw = json.dumps(bank, ensure_ascii=False).encode().ljust(64000000, b' ')
            candidate = root / 'candidate.json'
            candidate.write_bytes(bank_raw)
            installed = install(candidate, root)
            installed.write_bytes(bank_raw)
            self.assertEqual(TrainingCatalog(root).banks[bank['id']], bank)
            self.assertEqual(validate_archive({'training-banks/' + installed.name: bank_raw}),
                             {'training-banks/' + installed.name})
            candidate.write_bytes(bank_raw + b' ')
            with self.assertRaisesRegex(ValueError, 'file limit'):
                install(candidate, root)
            with self.assertRaisesRegex(ValueError, 'file limit'):
                validate_archive({'training-banks/' + installed.name: bank_raw + b' '})
            installed.write_bytes(bank_raw + b' ')
            with self.assertRaisesRegex(ValueError, 'Invalid training bank file'):
                TrainingCatalog(root)
            installed.write_text(json.dumps(bank, ensure_ascii=False), encoding='utf-8')
            value = {'schema': 1, 'bank_id': bank['id'], 'bank_version': bank['version'],
                     'flatten': [], 'questions': {}}
            format_raw = json.dumps(value).encode().ljust(64000000, b' ')
            candidate.write_bytes(format_raw)
            self.assertEqual(install_format(candidate, root), 'installed')
            format_path = root / 'training-formats' / installed.name
            format_path.write_bytes(format_raw)
            self.assertEqual(TrainingCatalog(root).formats[bank['id']], value)
            blobs = {'training-banks/' + installed.name: installed.read_bytes(),
                     'training-formats/' + installed.name: format_raw}
            self.assertEqual(validate_archive(blobs), set(blobs))
            candidate.write_bytes(format_raw + b' ')
            with self.assertRaisesRegex(ValueError, 'file limit'):
                install_format(candidate, root)
            blobs['training-formats/' + installed.name] += b' '
            with self.assertRaisesRegex(ValueError, 'archive path'):
                validate_archive(blobs)
            format_path.write_bytes(format_raw + b' ')
            with self.assertRaisesRegex(ValueError, 'Invalid training format file'):
                TrainingCatalog(root)

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
