from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch

from graspmemoedu.review_cards import point_id
from graspmemoedu.variant_production import approved, validate_pair
from graspmemoedu.variant_worker import VariantWorker
import test_review_papers as paper_tests
import test_training_api as training_tests


class Producer:
    def __init__(self):
        self.calls = []
        self.accept = True

    def generate(self, source, existing):
        self.calls.append('generate')
        q = source['question']
        offset = len(existing)
        rows = []
        for n in (3 + offset, 4 + offset):
            spec = deepcopy(q.get('interaction'))
            if spec and spec['type'] == 'fill_blank':
                spec['blanks'][0]['answers'] = [str(n)]
                stem = f'{n - 1}+1 = {{{{blank:a}}}}'
            else:
                stem = f'{n - 1}+1 的值是？'
                if spec and spec['type'] == 'choice':
                    for i, option in enumerate(spec['options']):
                        option.pop('html', None)
                        option['text'] = str(n + i)
            rows.append({'question': stem, 'explanation': f'计算得 {n}。',
                         'reference_answer': str(n), 'interaction': spec})
        return {'result': {'variants': rows}, 'usage': {'total_tokens': 100}}

    def review(self, source, existing, rows):
        self.calls.append('review')
        return {'result': {'approved': self.accept, 'checks': [
            {'index': i, 'approved': self.accept, 'reason': '独立计算核对'} for i in (1, 2)]}}


def compiler(rows, source, job_id, data_dir):
    return [{**deepcopy(q), 'id': 'p-' + job_id[:24] + '-' + str(i), 'title': '同组练习',
             'html': '<p>' + q['question'] + '</p>', 'explanation_html': '<p>解析</p>',
             'answer_type': source['question']['answer_type'], 'constraints': source['question']['constraints']}
            for i, q in enumerate(rows)]


class VariantWorkerTests(unittest.TestCase):
    setUp = training_tests.TrainingAPITests.setUp
    call = training_tests.TrainingAPITests.call
    account = training_tests.TrainingAPITests.account
    service = paper_tests.PaperTests.service
    prime = paper_tests.PaperTests.prime
    memories = paper_tests.PaperTests.memories

    def worker(self):
        self.producer = Producer()
        return VariantWorker(self.root, self.producer, compiler)

    def query(self, sql, args=()):
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            return con.execute(sql, args).fetchall()

    def change(self, sql, args=()):
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con, con:
            con.execute(sql, args)

    def test_no_eager_generation_and_only_two_without_schedule_changes(self):
        worker = self.worker()
        self.assertIsNone(worker.run_one())
        self.prime(); before = self.memories()
        result = worker.run_one()
        self.assertEqual(result['state'], 'installed', result)
        self.assertEqual(len(result['variant_ids']), 2)
        self.assertEqual(self.producer.calls, ['generate', 'review'])
        self.assertEqual(self.memories(), before)
        # The other learned point gets its own two; the entire bank does not.
        self.assertEqual(worker.run_one()['state'], 'installed')
        self.assertIsNone(worker.run_one())
        self.assertEqual(self.query('SELECT COUNT(*) FROM review_variants')[0][0], 4)

    def test_one_unseen_does_not_refill_zero_unseen_refills_exactly_two(self):
        self.prime(); worker = self.worker()
        result = worker.run_one(); worker.run_one()
        learner = self.service().learner_id
        self.change('INSERT INTO review_card_uses VALUES (?,?,1)', (learner, result['variant_ids'][0]))
        self.assertIsNone(worker.run_one())
        self.change('INSERT INTO review_card_uses VALUES (?,?,1)', (learner, result['variant_ids'][1]))
        self.assertEqual(worker.run_one()['state'], 'installed')
        self.assertEqual(self.query('SELECT COUNT(*) FROM review_variants')[0][0], 6)
        self.assertIsNone(worker.run_one())

    def test_seen_state_is_per_learner_and_variants_are_shared(self):
        self.prime(); worker = self.worker()
        worker.run_one(); worker.run_one()
        self.account(); self.prime()
        self.assertIsNone(worker.run_one())
        self.assertEqual(self.query('SELECT COUNT(*) FROM review_variants')[0][0], 4)

    def test_permissions_stops_admin_and_other_subjects_do_not_generate(self):
        self.prime(); worker = self.worker()
        for statement in (
            'UPDATE access_principals SET enabled=0',
            'UPDATE access_principals SET enabled=1,is_admin=1',
            "UPDATE access_principals SET is_admin=0,role='account',topics_default=0,topic_ids='[]'",
            "UPDATE access_principals SET role='guest'; UPDATE review_points SET subject='physics'",
        ):
            for sql in statement.split(';'): self.change(sql)
            self.assertIsNone(worker.run_one())
        self.change("UPDATE review_points SET subject='math'")
        self.change('INSERT INTO training_suspensions SELECT learner,bank,question,version,? FROM training_memory', (self.when.isoformat(),))
        self.assertIsNone(worker.run_one())

    def test_test_material_policy_is_respected(self):
        self.prime(); worker = self.worker()
        (self.root / 'test-materials.json').write_text(json.dumps({'schema': 1, 'banks': [self.bank['id']]}))
        self.assertIsNone(worker.run_one())
        self.account(is_advanced_learner=True, is_student=True); self.prime()
        self.assertEqual(worker.run_one()['state'], 'installed')

    def test_rejected_or_malformed_output_is_never_installed_or_repeated(self):
        self.prime(); worker = self.worker(); self.producer.accept = False
        self.assertEqual(worker.run_one()['state'], 'rejected')
        with patch.object(self.producer, 'generate', return_value={'result': {'variants': []}}):
            self.assertEqual(worker.run_one()['state'], 'failed')
        self.assertIsNone(worker.run_one())
        self.assertEqual(self.query('SELECT COUNT(*) FROM review_variants')[0][0], 0)

    def test_network_runs_without_learning_write_lock(self):
        self.prime(); worker = self.worker()
        original = self.producer.generate
        def call(*args):
            self.change("UPDATE access_principals SET display_name=display_name")
            return original(*args)
        with patch.object(self.producer, 'generate', side_effect=call):
            self.assertEqual(worker.run_one()['state'], 'installed')

    def test_revoked_demand_during_review_defers_install_and_reuses_approval(self):
        self.prime(); worker = self.worker()
        original = self.producer.review
        def revoke(*args):
            self.change('UPDATE access_principals SET enabled=0')
            return original(*args)
        with patch.object(self.producer, 'review', side_effect=revoke):
            self.assertEqual(worker.run_one()['state'], 'approved')
        self.assertEqual(self.query('SELECT COUNT(*) FROM review_variants')[0][0], 0)
        self.change('UPDATE access_principals SET enabled=1')
        self.assertEqual(worker.run_one()['state'], 'installed')
        self.assertEqual(self.producer.calls, ['generate', 'review'])

    def test_two_workers_cannot_claim_same_call(self):
        self.prime(); worker = self.worker(); worker.discover()
        other = VariantWorker(self.root, Producer(), compiler)
        ident = self.query('SELECT id FROM review_variant_jobs ORDER BY id')[0][0]
        self.assertTrue(worker.transition(ident, 'pending', 'generating'))
        self.assertFalse(other.transition(ident, 'pending', 'generating'))

    def test_uncertain_interruption_is_not_reissued(self):
        self.prime(); worker = self.worker(); worker.discover()
        self.change("UPDATE review_variant_jobs SET state='generating',updated_at='2000-01-01T00:00:00+00:00'")
        self.assertIsNone(worker.run_one())
        self.assertTrue(all(r[0] == 'failed' for r in self.query('SELECT state FROM review_variant_jobs')))
        self.assertEqual(self.producer.calls, [])

    def test_compiler_failure_is_atomic(self):
        self.prime(); worker = self.worker()
        worker.compiler = lambda *args: [dict(compiler(*args)[0]), {'broken': True}]
        self.assertEqual(worker.run_one()['state'], 'failed')
        self.assertEqual(self.query('SELECT COUNT(*) FROM review_variants')[0][0], 0)

    def test_changed_source_version_rejects_install(self):
        from graspmemoedu.training_content import fingerprint
        self.prime(); worker = self.worker()
        def change_source(*args):
            altered = deepcopy(self.bank); altered['title'] = '另一个版本'
            altered['version'] = fingerprint(altered)
            (self.root / 'training-banks' / (self.bank['id'] + '.json')).write_text(json.dumps(altered))
            return compiler(*args)
        worker.compiler = change_source
        self.assertEqual(worker.run_one()['state'], 'failed')
        self.assertEqual(self.query('SELECT COUNT(*) FROM review_variants')[0][0], 0)

    def test_new_variants_are_preferred_over_already_seen_source(self):
        from graspmemoedu.review_papers import PaperService
        self.prime(); worker = self.worker(); worker.run_one(); worker.run_one()
        inbox = self.call('GET', '/api/review-papers/inbox?subject_id=math')
        candidate = next(i for i in inbox['items'] if i['kind'] == 'paper' and i['count'] == 2)
        self.call('POST', '/api/review-papers/open?subject_id=math',
                  {'candidate_id': candidate['id'], 'request_id': 'b20fc5d3-fdc3-443b-88e0-60c1c8b4b997'})
        self.assertTrue(all(r[0].startswith('variant-') for r in self.query('SELECT card FROM review_card_uses')))

    def test_backups_include_jobs_and_compiled_variants(self):
        from graspmemoedu.site_backup import backup_site, restore_site
        self.prime(); self.worker().run_one()
        target = self.root / 'variants-backup.zip'; backup_site(self.settings, target)
        restored = self.root / 'restored'; restore_site(target, restored)
        with closing(sqlite3.connect(restored / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM review_variants').fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT count(*) FROM review_variant_jobs WHERE state='installed'").fetchone()[0], 1)


class PairValidationTests(unittest.TestCase):
    def test_inequalities_are_math_and_html_is_rejected(self):
        source = {'question': {'question': '原题', 'reference_answer': '原解', 'interaction': None}}
        rows = [{'question': f'求 ${i}<x<9$ 的区间', 'explanation': '$2<x<9$，即 $x>2$。',
                 'reference_answer': '$2<x<9$', 'interaction': None} for i in (2, 3)]
        validate_pair({'variants': rows}, source, [])
        rows[0]['question'] = '<script>alert(1)</script>'
        with self.assertRaisesRegex(ValueError, 'markup'):
            validate_pair({'variants': rows}, source, [])

    def test_review_requires_two_explicit_approvals(self):
        self.assertFalse(approved({'approved': True, 'checks': []}))
        self.assertFalse(approved({'approved': True, 'checks': [{'index': i, 'approved': True, 'reason': ''} for i in (1, 2)]}))

    def test_pair_rejects_original_and_changed_interaction(self):
        source = {'question': {'question': '2+1 = {{blank:a}}', 'reference_answer': '3',
            'interaction': {'version': 1, 'type': 'fill_blank', 'grading': 'exact', 'blanks': [{'id': 'a', 'answers': ['3']}]}}}
        producer = Producer(); result = producer.generate(source, [])['result']
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            validate_pair(result, source, [])
        result['variants'][0]['question'] = '计算 1+2 = {{blank:a}}'
        result['variants'][1]['interaction']['grading'] = 'semantic'
        with self.assertRaisesRegex(ValueError, 'type changed'):
            validate_pair(result, source, [])
