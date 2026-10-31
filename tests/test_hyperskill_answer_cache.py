from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import hyperskill_answer_cache_admin as admin
import hyperskill_answer_cache as workflow
from graspmemoedu.hyperskill_answers import AnswerStore, key


def question(step=1):
    return {'id': f'q{step}', 'title': 'Fixture', 'question': '<p>Choose A.</p>', 'html': '<p>Choose A.</p>',
            'reference_answer': '', 'explanation_html': '', 'answer_type': 'external', 'constraints': 'Official grading',
            'hyperskill': {'topic_id': 10, 'theory_id': 20, 'step_id': step, 'type': 'choice'},
            'hyperskill_presentation': {'schema': 1, 'dataset': {'options': ['A', 'B'], 'is_multiple_choice': False},
                'code_options': {}, 'interaction': {'version': 1, 'type': 'choice', 'multiple': False,
                    'options': [{'id': str(i), 'text': t, 'html': t} for i, t in enumerate(['A', 'B'])]}}}


def reference(q):
    return {'source_verified': True, 'problem_key': key(q), 'raw_answer': '{"selected":"0"}',
            'receipt': {'status': 'correct', 'submission_id': 100 + q['hyperskill']['step_id'], 'attempt_id': 300,
                'step_id': q['hyperskill']['step_id'], 'time': '2026-10-31T00:00:00Z',
                'dataset': {'options': ['B', 'A'], 'is_multiple_choice': False},
                'reply': {'choices': [False, True]}, 'answer_display': 'A', 'feedback': ''}}


class CacheWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.q = question()
        self.ref = reference(self.q)
        (self.data / 'training-banks').mkdir()
        workflow.save(self.data / 'training-banks/b.json', {'id': 'b', 'groups': [{'id': 'g', 'questions': [self.q]}]})
        with closing(AnswerStore(SimpleNamespace(db_path=self.data / 'learning.sqlite3')).connect()) as con:
            con.execute('CREATE TABLE learner_history(value TEXT)')
            con.execute("INSERT INTO learner_history VALUES ('preserve')")
            con.commit()

    def test_append_readback_idempotent_and_no_learner_writes(self):
        self.assertEqual(len(admin.inventory(self.data)['pending']), 1)
        result = admin.append_verified(self.data, [self.ref])
        self.assertEqual(result['write_tables'], ['hyperskill_answers'])
        self.assertEqual(len(result['added']), 1)
        self.assertEqual(admin.append_verified(self.data, [self.ref])['added'], [])
        self.assertEqual(admin.inventory(self.data)['pending'], [])
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT * FROM learner_history').fetchall(), [('preserve',)])

    def test_wrong_unverified_stale_and_wrong_input_cannot_be_cached(self):
        for mutation in ('wrong', 'unverified', 'stale', 'raw'):
            item = deepcopy(self.ref)
            if mutation == 'wrong': item['receipt']['status'] = 'wrong'
            elif mutation == 'unverified': item['source_verified'] = False
            elif mutation == 'stale': item['receipt']['dataset']['options'][0] = 'Changed'
            else: item['raw_answer'] = '{"selected":"1"}'
            with self.assertRaises(ValueError): admin.append_verified(self.data, [item])
        self.assertEqual(admin.cache_rows(self.data), [])

    def test_database_authorizer_blocks_trigger_writing_learning_history(self):
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            con.execute("CREATE TRIGGER bad AFTER INSERT ON hyperskill_answers BEGIN UPDATE learner_history SET value='changed'; END")
            con.commit()
        with self.assertRaises(sqlite3.DatabaseError): admin.append_verified(self.data, [self.ref])
        self.assertEqual(admin.cache_rows(self.data), [])

    def test_inventory_spans_all_banks_and_excludes_code(self):
        other, code = question(2), question(3)
        code['hyperskill']['type'] = 'code'
        workflow.save(self.data / 'training-banks/other.json', {'id': 'other', 'groups': [{'id': 'g', 'questions': [other, code]}]})
        self.assertEqual(len(admin.inventory(self.data)['pending']), 2)
        admin.append_verified(self.data, [self.ref])
        self.assertEqual([r['question']['id'] for r in admin.inventory(self.data)['pending']], ['q2'])

    def test_actual_unchanged_local_grader_accepts_verified_permuted_answer(self):
        result = workflow.isolated_accept([{'problem_key': key(self.q), 'question': self.q}], [self.ref], self.data / 'run')
        self.assertEqual(result['correct'], 1)
        self.assertEqual(result['semantic_calls'], 0)

    def test_candidate_generation_only_shares_question_data_and_attaches_images(self):
        q = question()
        q['html'] = '<p>Choose A.</p><img src="data:image/png;base64,AAAA">'
        job = {'problem_key': key(q), 'question': q}
        seen = []
        def raw(messages, **kwargs):
            seen.extend(messages)
            return {'provider': 'codex_exec', 'model': 'fixture', 'choices': [{'message': {'content': json.dumps({
                'answers': [{'problem_key': job['problem_key'][:12], 'answer': {'selected': '0'}}]})}}]}
        result = workflow.generate([job], self.data / 'state', self.data / 'run', SimpleNamespace(identity=['fixture'], raw=raw))
        self.assertEqual(len(result), 1)
        images = [v for v in seen[1]['content'] if v['type'] == 'image_url']
        self.assertEqual(images[0]['image_url']['url'], 'data:image/png;base64,AAAA')

    def test_partial_model_batch_keeps_valid_units_without_guessing_missing_identifiers(self):
        jobs = [{'problem_key': key(q), 'question': q} for q in (question(1), question(2))]
        def raw(messages, **kwargs):
            return {'provider': 'codex_exec', 'model': 'fixture', 'choices': [{'message': {'content': json.dumps({
                'answers': [{'problem_key': jobs[0]['problem_key'][:12], 'answer': {'selected': '0'}},
                            {'problem_key': 'invented', 'answer': {'selected': '0'}}]})}}]}
        result = workflow.generate(jobs, self.data / 'state', self.data / 'run', SimpleNamespace(identity=['fixture'], raw=raw))
        self.assertEqual([row['problem_key'] for row in result], [jobs[0]['problem_key']])
        self.assertFalse((self.data / 'state' / jobs[1]['problem_key'] / 'candidate-0.json').exists())


if __name__ == '__main__': unittest.main()
