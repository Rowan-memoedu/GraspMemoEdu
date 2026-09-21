"""Isolated guest acceptance for real course projections; no accounts or network."""

import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time
import unittest
import uuid

from fastapi.testclient import TestClient

from math_learning.api import create_app
from math_learning.catalog import Catalog
from math_learning.config import Settings
from math_learning.service import DomainError, LearningService
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class DashboardAcceptanceTests(unittest.TestCase):
    origin = 'http://127.0.0.1:8767'

    def setUp(self):
        self.temporary = temporary_test_directory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.courses = {}
        for topic_id, modules in [('topic-a', 2), ('topic-b', 1), ('topic-c', 1)]:
            topic = synthetic_course(modules)
            topic.update(id=topic_id, title='Fixture ' + topic_id)
            self.courses[topic_id] = topic
            (self.root / (topic_id + '.json')).write_text(json.dumps(topic), encoding='utf-8')
        self.catalog_data = {'courses': [
            {'id': 'course-a', 'title': 'Course A', 'description': 'Two actual topics',
             'topics': [{'id': 'topic-a', 'path': 'topic-a.json'},
                        {'id': 'topic-b', 'path': 'topic-b.json', 'prerequisites': ['topic-a']}],
             'units': [{'id': 'unit-a', 'title': 'Unit A', 'topic_ids': ['topic-a']},
                       {'id': 'unit-b', 'title': 'Unit B', 'topic_ids': ['topic-b']}]},
            {'id': 'course-b', 'title': 'Course B', 'description': '',
             'topics': [{'id': 'topic-c', 'path': 'topic-c.json'}]}]}
        (self.root / 'catalog.json').write_text(json.dumps(self.catalog_data), encoding='utf-8')
        self.settings = Settings(data_dir=self.root)
        self.judge = DeterministicJudge()
        self.clients = []
        self.addCleanup(self._close)
        self._open()
        self.guest = self._guest()

    def _open(self):
        template = LearningService(self.courses['topic-a'], self.root, self.judge)
        self.app = create_app(self.settings, template)
        self.client = TestClient(self.app, base_url=self.origin)
        self.client.__enter__()
        self.clients.append(self.client)
        self.transport = self.client.get('/api/session', headers={'Origin': self.origin}).json()['session_token']

    def _close(self):
        for client in reversed(self.clients):
            client.__exit__(None, None, None)
        for thread in threading.enumerate():
            if thread.name.startswith('answer-judge'):
                thread.join(5)

    def _restart(self):
        self.client.__exit__(None, None, None)
        self.clients.remove(self.client)
        self._open()

    def _headers(self, guest=None):
        result = {'Origin': self.origin, 'X-Learning-Session': self.transport}
        if guest is not None:
            result['X-Learning-Identity'] = guest['identity_token']
        return result

    def _guest(self):
        response = self.client.post('/api/access/guest', json={}, headers=self._headers())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _get(self, path, guest=None, status=200):
        response = self.client.get(path, headers=self._headers(guest or self.guest))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def _post(self, path, payload, guest=None, status=200):
        response = self.client.post(path, json=payload, headers=self._headers(guest or self.guest))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def _action(self, topic_id, kind, guest=None, **extra):
        state = self._get('/api/state?topic_id=' + topic_id, guest)
        payload = {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                   'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}
        result = self._post('/api/' + kind + '?topic_id=' + topic_id, payload, guest)
        if kind == 'submit':
            deadline = time.monotonic() + 5
            while result['status'] == 'pending' and time.monotonic() < deadline:
                result = self._get('/api/submissions/' + payload['request_id'] + '?topic_id=' + topic_id, guest)
                time.sleep(.005)
            self.assertEqual(result['status'], 'judged')
        return result, payload

    def _complete(self, topic_id, elapsed_ms=None):
        for _ in self.courses[topic_id]['modules']:
            self._action(topic_id, 'choose', choice='mastered')
            extras = {'elapsed_ms': elapsed_ms} if elapsed_ms is not None else {}
            self._action(topic_id, 'submit', answer='correct', **extras)
            self._action(topic_id, 'continue')

    def test_catalog_read_and_selection_do_not_create_learning_state(self):
        catalog = self._get('/api/catalog')
        self.assertIsNone(catalog['selected_course_id'])
        self.assertEqual([course['topic_count'] for course in catalog['courses']], [2, 1])
        before = self._get('/api/dashboard?course_id=course-a')
        self.assertEqual(before['course']['progress'], 0)
        self.assertTrue(all(not task['started'] and task['status'] == 'not_started' for task in before['tasks']))
        self._post('/api/catalog/select', {'course_id': 'course-a'})
        self._post('/api/catalog/select', {'course_id': 'course-a'})
        with closing(self.app.state.service._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM topics WHERE learner=?',
                                        (self.guest['access']['learner_id'],)).fetchone()[0], 0)
        self.assertEqual(self._get('/api/catalog')['selected_course_id'], 'course-a')

    def test_switching_courses_preserves_progress_and_selection_after_restart(self):
        self._post('/api/catalog/select', {'course_id': 'course-a'})
        state, _ = self._action('topic-a', 'choose', choice='mastered')
        self._post('/api/catalog/select', {'course_id': 'course-b'})
        self._action('topic-c', 'choose', choice='unmastered')
        self._restart()
        self.assertEqual(self._get('/api/catalog')['selected_course_id'], 'course-b')
        restored = self._get('/api/state?topic_id=topic-a')
        self.assertEqual(restored['revision'], state['revision'])
        self.assertEqual(restored['active_step_id'], state['active_step_id'])
        self.assertEqual(self._get('/api/dashboard')['course']['id'], 'course-b')

    def test_dashboard_has_real_weighted_progress_units_graph_and_no_question_content(self):
        self._complete('topic-a')
        dashboard = self._get('/api/dashboard?course_id=course-a')
        self.assertEqual(dashboard['course']['progress'], 66.67)
        self.assertIsNotNone(dashboard['course']['start_date'])
        self.assertIsNone(dashboard['course']['end_date'])
        self.assertIsNone(dashboard['course']['estimated_completion'])
        self.assertEqual([unit['progress'] for unit in dashboard['units']], [100, 0])
        self.assertEqual(dashboard['units'][1]['topic_ids'], ['topic-b'])
        self.assertEqual(dashboard['topics'][1]['prerequisites'], ['topic-a'])
        self.assertTrue(dashboard['topics'][1]['frontier'])
        self.assertEqual(dashboard['topics'][1]['unit_id'], 'unit-b')
        self.assertEqual([task['id'] for task in dashboard['history']], ['lesson:topic-a'])
        encoded = json.dumps(dashboard)
        self.assertNotIn('QUESTION::', encoded)
        self.assertNotIn('REFERENCE_ONLY::', encoded)

    def test_zero_progress_after_choice_is_started_and_paused_task_stays_pending(self):
        self._action('topic-a', 'choose', choice='mastered')
        learner = self.app.state.learning_services[(self.guest['access']['learner_id'], 'topic-a')]
        learner.pause({'request_id': str(uuid.uuid4())})
        task = self._get('/api/dashboard?course_id=course-a')['tasks'][0]
        self.assertEqual(task['progress'], 0)
        self.assertTrue(task['started'])
        self.assertEqual(task['status'], 'paused')
        self.assertIsNotNone(task['started_at'])

    def test_history_pagination_is_stable_and_scoped_to_learner_and_course(self):
        self._complete('topic-a')
        self._complete('topic-b')
        first = self._get('/api/dashboard?course_id=course-a&limit=1')
        self.assertEqual(first['course']['progress'], 100)
        self.assertIsNotNone(first['course']['end_date'])
        self.assertTrue(first['has_more'])
        cursor = first['history_cursor']
        second = self._get('/api/dashboard?course_id=course-a&limit=1&cursor=' + cursor)
        self.assertFalse(second['has_more'])
        self.assertIsNone(second['history_cursor'])
        self.assertEqual(len({first['history'][0]['id'], second['history'][0]['id']}), 2)
        self._get('/api/dashboard?course_id=course-b&cursor=' + cursor, status=422)
        self._get('/api/dashboard?course_id=course-a&cursor=' + cursor, self._guest(), status=422)
        self._get('/api/dashboard?course_id=course-a&cursor=not-valid', status=422)

    def test_answers_only_own_completed_judged_content_and_actual_time(self):
        self._complete('topic-a', elapsed_ms=12345)
        result = self._get('/api/tasks/lesson:topic-a/answers')
        self.assertEqual(len(result['groups']), 2)
        self.assertEqual(result['groups'][0]['answers'][0]['elapsed_seconds'], 12.345)
        self.assertIsNone(result['groups'][0]['answers'][0]['difficulty'])
        self.assertTrue(result['groups'][0]['answers'][0]['correct'])
        self.assertIsNotNone(result['groups'][0]['answers'][0]['answered_at'])
        encoded = json.dumps(result)
        self.assertNotIn('REFERENCE_ONLY::', encoded)
        self.assertNotIn('practice-1', encoded)
        self._get('/api/tasks/lesson:topic-a/answers', self._guest(), status=404)
        self._get('/api/tasks/lesson:topic-b/answers', status=404)

    def test_old_submission_time_is_null_and_timing_does_not_break_replay(self):
        self._complete('topic-a')
        self._action('topic-b', 'choose', choice='mastered')
        result, payload = self._action('topic-b', 'submit', answer='correct')
        replay = self._post('/api/submit?topic_id=topic-b', {**payload, 'elapsed_ms': None})
        self.assertEqual(replay['submission_id'], result['submission_id'])
        self._post('/api/submit?topic_id=topic-b', {**payload, 'elapsed_ms': 2}, status=409)
        self._action('topic-b', 'continue')
        answer = self._get('/api/tasks/lesson:topic-b/answers')['groups'][0]['answers'][0]
        self.assertIsNone(answer['elapsed_seconds'])
        self.assertIsNone(answer['difficulty'])

    def test_elapsed_time_rejects_negative_boolean_fraction_and_oversize(self):
        self._complete('topic-a')
        state, _ = self._action('topic-b', 'choose', choice='mastered')
        for elapsed in [-1, True, 1.5, 86400001, '25']:
            payload = {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                       'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'],
                       'answer': 'correct', 'elapsed_ms': elapsed}
            self._post('/api/submit?topic_id=topic-b', payload, status=422)
        self.assertEqual(self._get('/api/state?topic_id=topic-b')['revision'], state['revision'])

    def test_profile_strict_timezone_and_no_permission_edit(self):
        self.assertIsNone(self._get('/api/profile')['timezone'])
        profile = self._post('/api/profile', {'display_name': '测试访客', 'timezone': 'Asia/Shanghai'})
        self.assertEqual(profile['display_name'], '测试访客')
        self.assertEqual(self._get('/api/access')['display_name'], '测试访客')
        for timezone in ['Mars/Olympus', '../UTC', '', None]:
            self._post('/api/profile', {'timezone': timezone}, status=422)
        self._post('/api/profile', {'features': ['pause_topic']}, status=422)
        self._restart()
        self.assertEqual(self._get('/api/profile')['timezone'], 'Asia/Shanghai')
        self.assertEqual(self._get('/api/profile', self._guest())['display_name'], '游客')

    def test_feedback_is_real_persistent_context_checked_and_rate_limited(self):
        self._complete('topic-a')
        state = self._get('/api/state?topic_id=topic-b')
        question_id = state['steps'][0]['question_id']
        payload = {'message': 'A synthetic correction', 'course_id': 'course-a',
                   'task_id': 'lesson:topic-b', 'question_id': question_id}
        self._post('/api/feedback', {**payload, 'course_id': 'course-b'}, status=422)
        self._post('/api/feedback', {**payload, 'question_id': 'module-1-practice-9'}, status=403)
        saved = [self._post('/api/feedback', payload) for _ in range(5)]
        response = self.client.post('/api/feedback', json=payload, headers=self._headers(self.guest))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['detail']['code'], 'feedback_rate_limited')
        self.assertIn('Retry-After', response.headers)
        with closing(self.app.state.service._connect()) as con:
            rows = con.execute('SELECT * FROM feedback WHERE learner_id=?', (self.guest['access']['learner_id'],)).fetchall()
        self.assertEqual({row['id'] for row in rows}, {item['id'] for item in saved})
        self.assertTrue(all(row['topic_id'] == 'topic-b' for row in rows))
        self._restart()
        self._post('/api/feedback', payload, status=429)
        self._post('/api/feedback', {'message': 'Separate guest'}, self._guest())

    def test_new_endpoints_retain_authentication_and_reject_management_routes(self):
        for path in ['/api/catalog', '/api/dashboard?course_id=course-a', '/api/profile', '/api/tasks/lesson:topic-a/answers']:
            response = self.client.get(path, headers=self._headers())
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()['detail']['code'], 'identity_required')
        self._get('/api/state?topic_id=unknown', status=404)
        self._get('/api/catalog/manage', status=404)

    def test_restart_recovers_pending_work_for_nondefault_topic(self):
        state, _ = self._action('topic-c', 'choose', choice='mastered')
        learner = self.app.state.learning_services[(self.guest['access']['learner_id'], 'topic-c')]
        request_id = str(uuid.uuid4())
        learner.submit({'request_id': request_id, 'expected_revision': state['revision'],
                        'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], 'answer': 'correct'})
        self._restart()
        result = self._get('/api/submissions/' + request_id + '?topic_id=topic-c')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['state']['modules'][0]['practice_error_count'], 0)

    def test_version_change_preserves_all_saved_progress(self):
        self._action('topic-c', 'choose', choice='mastered')
        changed = copy.deepcopy(self.courses['topic-c'])
        changed['version'] = 'changed-version'
        (self.root / 'topic-c.json').write_text(json.dumps(changed), encoding='utf-8')
        with closing(self.app.state.service._connect()) as con:
            before = con.execute('SELECT body FROM topics WHERE learner=? AND topic=?',
                                 (self.guest['access']['learner_id'], 'topic-c')).fetchone()[0]
        with self.assertRaises(DomainError) as caught:
            LearningService(changed, self.root, self.judge, self.guest['access']['learner_id'])
        self.assertEqual(caught.exception.code, 'course_version_mismatch')
        with closing(self.app.state.service._connect()) as con:
            after = con.execute('SELECT body FROM topics WHERE learner=? AND topic=?',
                                (self.guest['access']['learner_id'], 'topic-c')).fetchone()[0]
        self.assertEqual(before, after)

    def test_catalog_rejects_duplicate_topics_unknown_prerequisites_and_cycles(self):
        for mutation in ('duplicate', 'unknown', 'cycle'):
            raw = copy.deepcopy(self.catalog_data)
            if mutation == 'duplicate':
                raw['courses'][1]['topics'] = [raw['courses'][0]['topics'][0]]
            elif mutation == 'unknown':
                raw['courses'][0]['topics'][0]['prerequisites'] = ['absent-topic']
            else:
                raw['courses'][0]['topics'][0]['prerequisites'] = ['topic-b']
            (self.root / 'catalog.json').write_text(json.dumps(raw), encoding='utf-8')
            with self.assertRaises(DomainError) as caught:
                Catalog.load(self.settings)
            self.assertEqual(caught.exception.code, 'invalid_catalog')

    def test_sqlite_backup_includes_profile_selection_feedback_and_timing(self):
        self._complete('topic-a')
        self._post('/api/catalog/select', {'course_id': 'course-a'})
        self._post('/api/profile', {'timezone': 'Asia/Shanghai'})
        self._post('/api/feedback', {'message': 'Backed up message'})
        self._complete('topic-b', elapsed_ms=4200)
        snapshot = self.root / 'snapshot.sqlite3'
        self.app.state.service.backup(snapshot)
        with closing(sqlite3.connect(snapshot)) as con:
            prefs = con.execute('SELECT selected_course_id,timezone FROM learner_preferences WHERE learner_id=?',
                                (self.guest['access']['learner_id'],)).fetchone()
            self.assertEqual(prefs, ('course-a', 'Asia/Shanghai'))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM feedback').fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT elapsed_ms FROM submissions WHERE topic='topic-b'").fetchone()[0], 4200)

    def test_old_database_adds_timing_column_without_changing_saved_payload(self):
        self._complete('topic-a')
        self._action('topic-b', 'choose', choice='mastered')
        result, payload = self._action('topic-b', 'submit', answer='correct')
        with closing(self.app.state.service._connect()) as con:
            original = con.execute('SELECT fingerprint FROM submissions WHERE id=?', (result['submission_id'],)).fetchone()[0]
            con.execute('ALTER TABLE submissions DROP COLUMN elapsed_ms')
        self._restart()
        replay = self._post('/api/submit?topic_id=topic-b', payload)
        self.assertEqual(replay['status'], 'judged')
        with closing(self.app.state.service._connect()) as con:
            row = con.execute('SELECT fingerprint,elapsed_ms FROM submissions WHERE id=?', (result['submission_id'],)).fetchone()
        self.assertEqual(row['fingerprint'], original)
        self.assertIsNone(row['elapsed_ms'])

    def test_public_new_routes_keep_exact_origin_and_identity_boundaries(self):
        public_origin = 'https://dashboard-fixture.trycloudflare.com'
        website_origin = 'https://dashboard-fixture.github.io'
        self.settings.public_api_origin = public_origin
        self.settings.allowed_origins = (website_origin,)
        self._restart()
        with TestClient(self.app, base_url=public_origin) as public_client:
            created = public_client.post('/api/access/guest', json={}, headers={'Origin': website_origin})
            self.assertEqual(created.status_code, 200, created.text)
            headers = {'Origin': website_origin, 'X-Learning-Identity': created.json()['identity_token']}
            for path in ['/api/catalog', '/api/dashboard?course_id=course-a', '/api/profile']:
                response = public_client.get(path, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], website_origin)
                self.assertEqual(public_client.get(path, headers={'Origin': website_origin}).status_code, 401)
                self.assertEqual(public_client.get(path, headers={**headers, 'Origin': 'https://attacker.invalid'}).status_code, 403)
            selected = public_client.post('/api/catalog/select', json={'course_id': 'course-b'}, headers=headers)
            self.assertEqual(selected.status_code, 200, selected.text)
            feedback = public_client.post('/api/feedback', json={'message': 'Public fixture'}, headers=headers)
            self.assertEqual(feedback.status_code, 200, feedback.text)
            answers = public_client.get('/api/tasks/lesson:topic-a/answers', headers=headers)
            self.assertEqual(answers.status_code, 404, answers.text)
            forbidden = public_client.post('/api/feedback/manage', json={}, headers=headers)
            self.assertEqual(forbidden.status_code, 403)
            self.assertEqual(forbidden.json()['detail']['code'], 'public_endpoint_forbidden')

    def test_existing_start_event_precedes_new_timestamp_and_unfinished_answers_stay_hidden(self):
        self._action('topic-a', 'choose', choice='mastered')
        self._action('topic-a', 'submit', answer='correct')
        self._get('/api/tasks/lesson:topic-a/answers', status=404)
        learner = self.app.state.learning_services[(self.guest['access']['learner_id'], 'topic-a')]
        with learner._transaction() as con:
            body = learner._load(con)
            first_started = body['started_at']
            body['started_at'] = '2099-01-01T00:00:00+00:00'
            learner._save(con, body)
        course = self._get('/api/dashboard?course_id=course-a')['course']
        self.assertLess(course['start_date'], '2099')
        self.assertGreaterEqual(course['start_date'], first_started)


if __name__ == '__main__':
    unittest.main()
