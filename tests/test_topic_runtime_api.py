"""Read, update, permission and graph integration over an isolated catalog."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import create_learning_service
from tests.fixtures import DeterministicJudge, temporary_test_directory
from tests.test_ordered_learning import ordered_topic


class TopicRuntimeApiTests(unittest.TestCase):
    origin = 'http://127.0.0.1:8767'

    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.intro, self.lesson = ordered_topic(True), ordered_topic()
        self.intro.update(id='intro', source_rem_id='IntroRem')
        self.lesson.update(id='lesson', source_rem_id='LessonRem')
        self.intro['content_nodes'] = [{'id':'intro-node', 'section_id':'intro-a', 'parent_id':None,
                                      'owner':{'kind':'introduction','id':'intro-a','field':'html'}}]
        self.intro['references'] = [{'source_node_id':'intro-node','target_rem_id':'LessonRem','label':'lesson'}]
        self.intro['introductions'][0]['html'] = '<div id="node-intro-node"><p>reading text</p></div>'
        (self.root / 'catalog.json').write_text(json.dumps({'courses':[{
            'id':'course', 'title':'Fixture course', 'topics':[
                {'id':'intro','path':'intro.json'}, {'id':'lesson','path':'lesson.json'}]}]}), encoding='utf-8')
        self.client = None
        self.addCleanup(self.close)
        self.open()
        response = self.client.post('/api/access/guest', json={}, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.token = response.json()['identity_token']

    def close(self):
        if self.client:
            self.client.__exit__(None, None, None)
            self.client = None

    def open(self):
        for topic in (self.intro, self.lesson):
            (self.root / (topic['id']+'.json')).write_text(json.dumps(topic), encoding='utf-8')
        template = create_learning_service(deepcopy(self.intro), self.root, DeterministicJudge())
        self.app = create_app(Settings(data_dir=self.root), template)
        self.client = TestClient(self.app, base_url=self.origin)
        self.client.__enter__()
        self.transport = self.client.get('/api/session', headers={'Origin':self.origin}).json()['session_token']

    def headers(self):
        return {'Origin':self.origin, 'X-Learning-Session':self.transport,
                **({'X-Learning-Identity':self.token} if hasattr(self,'token') else {})}

    def get(self, path):
        response = self.client.get(path, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def advance(self):
        state = self.get('/api/state?topic_id=intro')
        response = self.client.post('/api/continue?topic_id=intro', headers=self.headers(), json={
            'request_id':str(uuid.uuid4()), 'expected_revision':state['revision'],
            'attempt_id':state['attempt_id'], 'step_id':state['active_step_id']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_updated_intro_keeps_course_completion_and_only_one_reread_task(self):
        initial = self.get('/api/dashboard?course_id=course')
        self.assertEqual([t['topic_id'] for t in initial['tasks']], ['intro','lesson'])
        self.advance()
        done = self.advance()
        before = self.get('/api/dashboard?course_id=course')
        self.assertEqual(before['course']['progress'], round(100 / 3, 2))
        self.close()
        self.intro.update(version='artifact-two', content_version='content-two')
        self.intro['introductions'][0]['html'] += '<p>Updated teaching.</p>'
        self.open()
        after = self.get('/api/dashboard?course_id=course')
        rereads = [t for t in after['tasks'] if t.get('content_update')]
        self.assertEqual(len(rereads), 1)
        self.assertEqual(after['course']['progress'], before['course']['progress'])
        self.assertEqual(after['history'][0]['completed_at'], done['completed_at'])
        self.assertTrue(self.get('/api/state?topic_id=intro')['reread_pending'])
        self.advance()
        self.advance()
        self.assertFalse(any(t.get('content_update') for t in self.get('/api/dashboard?course_id=course')['tasks']))
        denied = self.client.get('/api/review/sessions/missing?topic_id=intro', headers=self.headers())
        self.assertEqual(denied.status_code, 422, denied.text)
        with closing(self.app.state.access_store._connect()) as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'review_cards' in tables:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM review_cards WHERE topic='intro'").fetchone()[0], 0)

    def test_references_are_derived_and_intro_is_absent_from_graph(self):
        state = self.get('/api/state?topic_id=intro')
        reference = state['content_references']['outgoing'][0]
        self.assertEqual(reference['target']['topic_id'], 'lesson')
        self.assertEqual(reference['target']['subject_id'], 'math')
        graph = self.get('/api/graphs/course')
        self.assertEqual([node['topic_id'] for node in graph['nodes']], ['lesson'])
        lesson = self.get('/api/state?topic_id=lesson')
        self.assertEqual([item['topic_id'] for item in lesson['content_references']['backlinks']], ['intro'])
        self.assertEqual(lesson['content_references']['backlinks'][0]['node_id'], 'intro-node')
