import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'atomic_question_trial', Path(__file__).resolve().parents[1] / 'scripts/atomic_question_trial.py')
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def example(subject, ident='example-01'):
    source = trial.source_for({'id': ident, 'subject_id': subject, 'chunk': 'A term denotes a fact.',
                               'target': 'Recall the fact.', 'question': 'What does the term denote?',
                               'answer': 'The fact.'})
    cards = [{'point_ids': [ident], 'question': question, 'reference_answer': 'The fact.',
              'explanation': 'The term denotes this fact.',
              'interaction': {'version': 1, 'type': 'text', 'grading': 'semantic'}}
             for question in ('Explain what the term denotes.', 'Which fact does this term denote?')]
    return {'source': source, 'output': {'point_id': ident, 'status': 'ok', 'reason': '', 'variants': cards}}


class AtomicFewShotRoutingTests(unittest.TestCase):
    def setUp(self):
        self.first, self.second = list(trial.SUBJECTS)[:2]
        self.first_example, self.second_example = example(self.first), example(self.second)
        self.source = copy.deepcopy(self.first_example['source'])
        self.source['point']['id'] = 'new-point'
        self.source['seed_card']['point_ids'] = ['new-point']
        self.source['seed_card']['question'] = 'A new source question?'

    def load(self, subjects):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps({'version': 1, 'subjects': subjects}), encoding='utf-8')
            return trial.load_few_shots(path)

    def test_subject_metadata_selects_only_same_subject_examples(self):
        catalog = self.load({self.first: [self.first_example], self.second: [self.second_example]})
        payload = trial.generation_payload(self.source, catalog)
        self.assertEqual(payload, {'few_shot_examples': [self.first_example], 'task': self.source})

    def test_current_point_is_excluded_even_if_question_changed(self):
        candidate = copy.deepcopy(self.first_example)
        candidate['source']['point']['id'] = self.source['point']['id']
        with self.assertRaisesRegex(ValueError, 'No approved same-subject'):
            trial.generation_payload(self.source, {self.first: [candidate]})

    def test_same_source_question_is_excluded_when_point_id_differs(self):
        source = copy.deepcopy(self.source)
        source['seed_card']['question'] = self.first_example['source']['seed_card']['question']
        with self.assertRaisesRegex(ValueError, 'No approved same-subject'):
            trial.generation_payload(source, {self.first: [self.first_example]})

    def test_missing_subject_never_falls_back_to_another_subject(self):
        with self.assertRaisesRegex(ValueError, 'No approved same-subject'):
            trial.generation_payload(self.source, {self.second: [self.second_example]})

    def test_catalog_rejects_mislabeled_subject(self):
        with self.assertRaisesRegex(ValueError, 'subject mismatch'):
            self.load({self.first: [self.second_example]})

    def test_catalog_rejects_duplicate_and_incomplete_examples(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.load({self.first: [self.first_example, self.first_example]})
        invalid = copy.deepcopy(self.first_example)
        invalid['output']['variants'].pop()
        with self.assertRaisesRegex(ValueError, 'Exactly two'):
            self.load({self.first: [invalid]})

    def test_selected_examples_do_not_mutate_task_or_catalog(self):
        catalog = {self.first: [self.first_example]}
        before = copy.deepcopy((self.source, catalog))
        payload = trial.generation_payload(self.source, catalog)
        self.assertEqual((self.source, catalog), before)
        self.assertEqual(len(payload['few_shot_examples']), 1)

    def test_missing_examples_fail_before_credentials_or_provider_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_file = root / 'input.json'
            source_file.write_text(json.dumps({'cases': [{'id': 'test-01', 'subject_id': self.first,
                'chunk': 'a fact', 'target': 'recall it', 'question': 'new question', 'answer': 'a fact'}]}), encoding='utf-8')
            catalog_file = root / 'catalog.json'
            catalog_file.write_text(json.dumps({'version': 1, 'subjects': {self.second: [self.second_example]}}), encoding='utf-8')
            args = SimpleNamespace(input=source_file, few_shots=catalog_file, only=None,
                                   run_root=root / 'never-created', workers=1)
            with patch.object(trial, 'configuration') as configure:
                with self.assertRaisesRegex(ValueError, 'No approved same-subject'):
                    trial.run(args)
                configure.assert_not_called()
                self.assertFalse(args.run_root.exists())


if __name__ == '__main__':
    unittest.main()
