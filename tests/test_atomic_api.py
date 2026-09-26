from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.atomic import AtomicLearningService
from graspmemoedu.config import Settings
from graspmemoedu.question_types import demo_answer
from graspmemoedu.site_backup import backup_site, restore_site
from atomic_fixtures import atomic_topic


def write_catalog(root):
    courses, subjects = [], []
    for subject in ('english', 'chinese', 'biology', 'chemistry'):
        topic = atomic_topic(subject, 'atomic-' + subject)
        (root / f'{subject}.json').write_text(json.dumps(topic, ensure_ascii=False), encoding='utf-8')
        courses.append({'id': subject + '-course', 'title': '隔离原子训练',
                        'topics': [{'id': topic['id'], 'path': subject + '.json'}]})
        subjects.append({'id': subject, 'course_ids': [subject + '-course']})
    (root / 'catalog.json').write_text(json.dumps({'courses': courses}), encoding='utf-8')
    (root / 'subjects.json').write_text(json.dumps({'subjects': subjects}), encoding='utf-8')


class AtomicAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); write_catalog(self.root)
        self.settings = Settings(data_dir=self.root, public_api_origin='https://atomic.test', allowed_origins=('https://site.test',))
        self.when = datetime.now(timezone.utc)
        topic = atomic_topic('english', 'atomic-english')
        self.app = create_app(self.settings, AtomicLearningService(topic, self.root), atomic_clock=lambda: self.when)
        self.client = TestClient(self.app, base_url='https://atomic.test'); self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        self.headers = {'Origin': 'https://site.test'}
        guest = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = guest['identity_token']
        self.learner_id = guest['access']['learner_id']
        self.scope = '?topic_id=atomic-english&subject_id=english'

    def call(self, method, path, body=None, status=200):
        response = self.client.request(method, path, headers=self.headers, **({'json': body} if body is not None else {}))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def payload(self, state, **extra):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def finish(self):
        state = self.call('GET', '/api/state' + self.scope)
        backend = self.app.state.learning_services[(self.learner_id, 'atomic-english')]
        backend.clock = lambda: self.when
        state = self.call('POST', '/api/choose' + self.scope, self.payload(state, choice='learn'))
        for _ in range(40):
            if state['status'] == 'completed': return state, backend
            if state['phase'] == 'answer':
                answer = demo_answer(backend.questions[state['current']['card_id']])
                result = self.call('POST', '/api/submit' + self.scope, self.payload(state, answer=answer))
                for _ in range(100):
                    result = self.call('GET', '/api/submissions/' + result['submission_id'] + self.scope)
                    if result['status'] not in ('pending', 'running'): break
                    time.sleep(.01)
                self.assertEqual(result['status'], 'judged'); state = result['state']
            else:
                if state['phase'] == 'waiting': self.when = datetime.fromisoformat(state['next_available_at'])
                state = self.call('POST', '/api/continue' + self.scope, self.payload(state))
        self.fail('API learning did not complete')

    def test_four_subjects_dashboard_queue_history_permissions_and_backup(self):
        for subject in ('english', 'chinese', 'biology', 'chemistry'):
            state = self.call('GET', f'/api/state?topic_id=atomic-{subject}&subject_id={subject}')
            self.assertEqual(state['training_mode'], 'atomic_retrieval')
            board = self.call('GET', f'/api/dashboard?subject_id={subject}&course_id={subject}-course')
            self.assertEqual(board['topics'][0]['progress'], 0)
        self.call('GET', '/api/state?topic_id=atomic-english&subject_id=math', status=403)
        self.call('POST', '/api/pause' + self.scope, {'request_id': 'guest-pause'}, status=403)
        state, backend = self.finish()
        board = self.call('GET', '/api/dashboard?subject_id=english&course_id=english-course')
        self.assertEqual(board['course']['progress'], 100)
        history = self.call('GET', '/api/tasks/lesson:atomic-english/answers?subject_id=english')
        self.assertEqual(sum(len(g['answers']) for g in history['groups']), 3)
        self.assertEqual(self.call('GET', '/api/atomic/reviews?subject_id=english')['items'], [])
        # Only this isolated app's injected clock advances; system time does not.
        self.when += timedelta(hours=6, minutes=1)
        self.assertEqual(self.call('GET', '/api/atomic/reviews?subject_id=english')['items'][0]['due_count'], 3)
        state = self.call('POST', '/api/choose' + self.scope, self.payload(state, choice='review'))
        self.assertEqual(state['current']['card_id'], 'ab-bundle')
        self.assertTrue(self.call('GET', '/api/atomic/reviews?subject_id=english')['items'][0]['active'])
        self.call('POST', '/api/review/begin' + self.scope, {'request_id': str(uuid.uuid4()), 'module_id': 'atomic'}, status=422)
        # A separate identity cannot read an existing submission/history.
        other = self.call('POST', '/api/access/guest', {})
        original = self.headers['X-Learning-Identity']; self.headers['X-Learning-Identity'] = other['identity_token']
        self.call('GET', '/api/tasks/lesson:atomic-english/answers?subject_id=english', status=404)
        self.headers['X-Learning-Identity'] = original
        archive = self.root / 'snapshot.zip'
        backup_site(self.settings, archive)
        restored = self.root / 'restored'; restore_site(archive, restored)
        reopened = AtomicLearningService(atomic_topic('english', 'atomic-english'), restored, learner_id=self.learner_id)
        self.assertEqual(reopened.state()['current']['card_id'], 'ab-bundle')
