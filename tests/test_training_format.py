from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
import uuid

from graspmemoedu.question_bank import digest
from graspmemoedu.question_types import (validate_interaction, public_interaction, deterministic_verdict,
                                       parse_answer, demo_answer, display_answer)
from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import TrainingCatalog, validate_archive, validate_saved, fingerprint
from graspmemoedu.training_format import flatten, install, validate_format
from tests.fixtures import temporary_test_directory
from training_fixtures import training_bank


def format_fixture(bank):
    q = bank['groups'][1]['questions'][0]
    spec = {'version': 1, 'type': 'choice', 'multiple': True, 'shuffle': False,
            'options': [{'id': 'a', 'text': '甲'}, {'id': 'b', 'text': '乙'}, {'id': 'c', 'text': '丙'}],
            'correct_ids': ['a', 'c']}
    return {'schema': 1, 'bank_id': bank['id'], 'bank_version': bank['version'], 'flatten': [],
            'questions': {q['id']: {'source_sha256': digest(q), 'question': q['question'],
                                    'html': q['html'], 'interaction': spec}}}


class TrainingFormatTests(unittest.TestCase):
    def setUp(self):
        temporary = temporary_test_directory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'training-banks').mkdir()
        self.bank = training_bank()
        (self.root / 'training-banks/isolated-bank.json').write_text(json.dumps(self.bank), encoding='utf-8')
        self.value = format_fixture(self.bank)

    def save(self):
        path = self.root / 'format.json'
        path.write_text(json.dumps(self.value), encoding='utf-8')
        return install(path, self.root)

    def test_format_update_requires_exact_previous_hash_and_keeps_previous_bytes(self):
        self.save()
        target = self.root / 'training-formats' / (self.bank['id'] + '.json')
        previous = target.read_bytes(); previous_hash = hashlib.sha256(previous).hexdigest()
        self.value['questions']['essay']['html'] = '<p>同一道题的排版修正。</p>'
        candidate = self.root / 'format-update.json'
        candidate.write_text(json.dumps(self.value), encoding='utf-8')
        for guard in (None, '0' * 64):
            with self.assertRaises(ValueError): install(candidate, self.root, expected_previous=guard)
        self.assertEqual(target.read_bytes(), previous)
        self.assertEqual(install(candidate, self.root, expected_previous=previous_hash), 'updated')
        self.assertEqual((target.parent / 'history' / (self.bank['id'] + '-' + previous_hash + '.json')).read_bytes(), previous)
        self.assertEqual(TrainingCatalog(self.root).banks[self.bank['id']], self.bank)

    def test_multiple_selection_all_or_nothing_roundtrip_and_secrets(self):
        q = self.value['questions']['essay']; spec = q['interaction']
        validate_interaction(spec, q['question'])
        self.assertTrue(deterministic_verdict(q, '{"selected":["c","a"]}')[0])
        for selection in (['a'], ['a', 'b', 'c'], ['b']):
            self.assertFalse(deterministic_verdict(q, json.dumps({'selected': selection}))[0])
        for selection in ([], ['a', 'a'], ['bad'], 'a', [None], [True]):
            with self.assertRaises(ValueError):
                parse_answer(q, json.dumps({'selected': selection}))
        self.assertEqual(parse_answer(q, demo_answer(q)), {'selected': ['a', 'c']})
        self.assertEqual(display_answer(q, demo_answer(q)), '甲\n丙')
        self.assertEqual(set(public_interaction(q)), {'version', 'type', 'options', 'multiple'})
        for mutation in ({'correct_ids': []}, {'correct_ids': ['a', 'a']}, {'multiple': 'true'}, {'shuffle': 0}, {'correct_id': 'a'}):
            with self.assertRaises(ValueError):
                validate_interaction({**spec, **mutation})

    def test_install_preserves_originals_progress_and_in_flight_snapshot(self):
        catalog = TrainingCatalog(self.root)
        service = TrainingService(catalog, self.root, 'fixture-learner')
        attempt = service.open(self.bank['id'], 'group-a', 'essay', str(uuid.uuid4()))
        service.reveal(attempt); service.rate(attempt, 3)
        pending = service.open(self.bank['id'], 'group-a', 'essay', str(uuid.uuid4()))
        before = service.state(pending)
        with closing(service._connect()) as con:
            memory = tuple(con.execute('SELECT * FROM training_memory').fetchone())
        self.assertEqual(self.save(), 'installed')
        self.assertEqual(self.save(), 'already_installed')
        updated = TrainingCatalog(self.root)
        self.assertEqual(updated.banks, catalog.banks)
        service = TrainingService(updated, self.root, 'fixture-learner')
        self.assertEqual(service.state(pending), before)
        new = service.open(self.bank['id'], 'group-a', 'essay', str(uuid.uuid4()))
        self.assertEqual(service.state(new)['version'], self.bank['version'])
        self.assertTrue(service.state(new)['question']['interaction']['multiple'])
        self.assertEqual([o['id'] for o in service.state(new)['question']['interaction']['options']], ['a', 'b', 'c'])
        self.assertFalse(service.state(new)['can_self_rate'])
        with closing(service._connect()) as con:
            self.assertEqual(tuple(con.execute('SELECT * FROM training_memory').fetchone()), memory)
            validate_saved(con, updated)

    def test_source_binding_answer_edits_and_invalid_flatten_are_rejected(self):
        for mutate in (lambda v: v.update(bank_version='0' * 64),
                       lambda v: v['questions']['essay'].update(source_sha256='0' * 64),
                       lambda v: v['questions']['essay'].update(reference_answer='changed'),
                       lambda v: v.update(flatten=['chapter']),
                       lambda v: v.update(flatten=['group-a'])):
            value = deepcopy(self.value); mutate(value)
            with self.assertRaises(ValueError): validate_format(value, self.bank)
        self.save(); self.value['questions']['essay']['question'] = 'changed'
        with self.assertRaises(ValueError): self.save()

    def test_archive_contains_format_and_requires_original_source(self):
        self.save()
        blobs = TrainingCatalog(self.root).archive_blobs()
        self.assertEqual(validate_archive(blobs), set(blobs))
        with self.assertRaises(ValueError):
            validate_archive({k: v for k, v in blobs.items() if k.startswith('training-formats/')})

    def test_media_repair_cleans_old_display_without_rewriting_attempt_snapshot(self):
        q = self.bank['groups'][1]['questions'][0]
        q['html'] += '<img src="data:image/png;base64,YWJj"><p>题目续文。</p>'
        self.bank['version'] = fingerprint(self.bank)
        original_path = self.root / 'training-banks/isolated-bank.json'
        original_path.write_text(json.dumps(self.bank), encoding='utf-8')
        original_bytes = original_path.read_bytes()
        service = TrainingService(TrainingCatalog(self.root), self.root, 'fixture-learner')
        attempt = service.open(self.bank['id'], 'group-a', 'essay', str(uuid.uuid4()))
        with closing(service._connect()) as con:
            previous = tuple(con.execute('SELECT * FROM training_attempts').fetchone())
        self.value = {'schema': 1, 'bank_id': self.bank['id'], 'bank_version': self.bank['version'],
                      'questions': {}, 'flatten': [], 'media': {'watermark_policy': 'preserve_embedded',
                      'excluded_images': {hashlib.sha256(b'abc').hexdigest(): 'qr_code'}}}
        self.save()
        updated = TrainingCatalog(self.root)
        service = TrainingService(updated, self.root, 'fixture-learner')
        self.assertNotIn('YWJj', service.state(attempt)['question']['html'])
        self.assertIn('题目续文', service.state(attempt)['question']['html'])
        with closing(service._connect()) as con:
            self.assertEqual(tuple(con.execute('SELECT * FROM training_attempts').fetchone()), previous)
            validate_saved(con, updated)
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(validate_archive(updated.archive_blobs()), set(updated.archive_blobs()))

    def test_singleton_flatten_preserves_order_titles_and_leaf_ids(self):
        groups = [{'id': 'root', 'parent_id': None, 'questions': []},
                  {'id': 'wrapper', 'parent_id': 'root', 'questions': []},
                  {'id': 'leaf', 'parent_id': 'wrapper', 'questions': [{'id': 'q'}]},
                  {'id': 'other', 'parent_id': 'root', 'questions': []}]
        result = flatten(groups, ['wrapper'])
        self.assertEqual([g['id'] for g in result], ['root', 'leaf', 'other'])
        self.assertEqual(result[1]['parent_id'], 'root')
        self.assertEqual(groups[2]['parent_id'], 'wrapper')


if __name__ == '__main__':
    unittest.main()
