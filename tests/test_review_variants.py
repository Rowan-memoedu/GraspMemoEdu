from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import unittest
import uuid

from graspmemoedu.review_cards import point_id, register_point
from graspmemoedu.review_variants import install_variants, read_variant
from graspmemoedu.service import DomainError
import test_training_api as helpers


class VariantTests(unittest.TestCase):
    setUp = helpers.TrainingAPITests.setUp
    call = helpers.TrainingAPITests.call
    account = helpers.TrainingAPITests.account

    def batch(self):
        question = deepcopy(self.bank['groups'][1]['questions'][3])
        question.update(id='practice-choice', title='Practice', question='Practice question', html='<p>VARIANT QUESTION</p>',
                        explanation_html='<p>VARIANT EXPLANATION</p>')
        data = {'schema': 1, 'bank_id': self.bank['id'], 'bank_version': self.bank['version'],
                'variants': [{'source_question_id': 'choice', 'question': question}]}
        path = self.root / 'variants.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path, data

    def register(self):
        with closing(self.app.state.access_store._connect()) as con:
            register_point(con, 'training', self.bank['id'], self.bank['version'], 'choice', 'math', self.when.isoformat())

    def test_import_requires_existing_point_and_is_atomic_and_idempotent(self):
        path, data = self.batch()
        with self.assertRaises(ValueError):
            install_variants(self.root, path)
        self.register()
        first = install_variants(self.root, path)
        self.assertEqual(first, install_variants(self.root, path))
        ident = first['variant_ids'][0]
        self.assertEqual(read_variant(self.root, ident, self.bank, 'choice')['html'], '<p>VARIANT QUESTION</p>')
        with self.assertRaises(DomainError):
            read_variant(self.root, ident, self.bank, 'blank')
        with closing(self.app.state.access_store._connect()) as con:
            before = con.execute('SELECT COUNT(*) FROM review_variants').fetchone()[0]
        data['variants'][0]['question']['html'] = '<p>A different valid candidate</p>'
        data['variants'].append({'source_question_id': 'missing', 'question': data['variants'][0]['question']})
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            install_variants(self.root, path)
        with closing(self.app.state.access_store._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_variants').fetchone()[0], before)

    def test_help_pins_variant_snapshot_and_rejects_wrong_source(self):
        self.account(is_student=True, is_advanced_learner=True)
        store = self.app.state.access_store
        with store._transaction() as con:
            admin = store._insert_account(con, 'Variant administrator', is_admin=True)
        self.app.state.help_requests.set_default_admin(admin['account']['learner_id'])
        self.register(); path, _ = self.batch()
        variant = install_variants(self.root, path)['variant_ids'][0]
        payload = {'request_id': str(uuid.uuid4()), 'message': 'Ask about this Practice',
                   'topic_id': 'training.' + self.bank['id'], 'bank_id': self.bank['id'], 'group_id': 'group-a',
                   'question_id': 'choice', 'content_block_id': 'question:choice', 'content_version': variant}
        saved = self.call('POST', '/api/help-requests', payload)['id']
        item = self.call('GET', '/api/help-center?report_id=' + saved)['items'][0]
        self.assertEqual(item['content_html'], '<p>VARIANT QUESTION</p>')
        self.call('POST', '/api/help-requests', {**payload, 'request_id': str(uuid.uuid4()),
            'question_id': 'blank', 'content_block_id': 'question:blank'}, status=409)
        self.call('POST', '/api/help-requests', {**payload, 'request_id': str(uuid.uuid4()),
            'content_version': 'variant-' + '0' * 64}, status=409)


if __name__ == '__main__':
    unittest.main()
