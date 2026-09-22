from contextlib import closing
import copy
import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from math_learning.api import create_app
from math_learning.config import Settings
from math_learning.service import LearningService
from tests.fixtures import synthetic_course, temporary_test_directory


class ContentReportTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        course = synthetic_course(1)
        self.question = course['modules'][0]['example']
        self.question['html'] = '<p>same same 🌟 and &amp; safe.</p>'
        self.learning = LearningService(course, self.root, None)
        self.app = create_app(Settings(data_dir=self.root), self.learning)
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8767')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'Origin': 'http://127.0.0.1:8767'}
        self.headers['X-Learning-Session'] = self.client.get('/api/session', headers=self.headers).json()['session_token']
        guest = self.client.post('/api/access/guest', headers=self.headers, json={}).json()
        self.headers['X-Learning-Identity'] = guest['identity_token']
        self.learner_id = guest['access']['learner_id'] if 'access' in guest else guest['learner_id']
        self.state = self.client.get('/api/state', headers=self.headers).json()
        self.body = {'message': 'Report text', 'topic_id': self.state['topic_id'], 'question_id': self.question['id'],
                     'correction': {'content_block_id': 'question:' + self.question['id'], 'content_version': course['version'],
                         'quote': {'exact': 'same', 'prefix': 'same ', 'suffix': ' 🌟 and & safe.', 'start': 5, 'end': 9},
                         'operation': 'replace', 'replacement': 'different', 'comment': ''}}

    def post(self, body, expected=200):
        response = self.client.post('/api/feedback', headers=self.headers, json=body)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def test_precise_repeated_quote_saved_without_changing_content_or_progress(self):
        result = self.post(self.body)
        self.assertEqual(result['status'], 'saved')
        with closing(self.learning._connect()) as con:
            row = con.execute('SELECT * FROM feedback_corrections WHERE feedback_id=?', (result['id'],)).fetchone()
            self.assertEqual(json.loads(row['body']), self.body['correction'])
        self.assertEqual(self.state, self.client.get('/api/state', headers=self.headers).json())
        self.assertEqual(self.question['html'], '<p>same same 🌟 and &amp; safe.</p>')

    def test_explicit_delete_allows_empty_replacement(self):
        self.body['correction'].update(operation='delete', replacement='')
        self.assertEqual(self.post(self.body)['status'], 'saved')
        self.body['correction']['operation'] = 'replace'
        self.post(self.body, 422)

    def test_stale_version_bad_anchor_and_unvisited_question_are_rejected(self):
        version = copy.deepcopy(self.body)
        version['correction']['content_version'] = 'old-version'
        self.post(version, 409)
        for field, value in [('start', 0), ('prefix', 'wrong'), ('exact', 'unknown')]:
            body = copy.deepcopy(self.body)
            body['correction']['quote'][field] = value
            self.post(body, 422)
        body = copy.deepcopy(self.body)
        body['question_id'] = self.learning.course['modules'][0]['practices'][0]['id']
        body.pop('correction')
        self.post(body, 403)
        with closing(self.learning._connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM feedback_corrections').fetchone()[0], 0)

    def test_plain_feedback_compatibility_and_size_limits(self):
        self.assertEqual(self.post({'message': 'General feedback'})['status'], 'saved')
        self.body['correction']['replacement'] = 'x' * 2001
        self.post(self.body, 422)


if __name__ == '__main__':
    unittest.main()
