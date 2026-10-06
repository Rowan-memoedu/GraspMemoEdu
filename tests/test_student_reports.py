"""Student reports: exact records, no double counting, no writes, strict access."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import unittest
import uuid

from tests import test_admin_students as admin_tests
from graspmemoedu.training import TrainingService
from graspmemoedu.review_papers import PaperService


class StudentReportsTests(unittest.TestCase):
    setUp = admin_tests.AdminStudentsTests.setUp
    identity = admin_tests.AdminStudentsTests.identity
    detail = admin_tests.AdminStudentsTests.detail
    snapshot = admin_tests.AdminStudentsTests.snapshot
    learner = admin_tests.AdminStudentsTests.learner
    action = staticmethod(admin_tests.AdminStudentsTests.action)

    def get(self, path='', **params):
        return self.client.get('/api/admin/students/' + self.student + path,
                               params=params, headers=self.admin_headers)

    def training(self):
        return TrainingService(self.app.state.training_catalog, self.root, self.student, clock=lambda: self.time)

    def answer(self, service, qid='exact', answer='0', elapsed=1500):
        attempt = service.open('isolated-bank', 'group-a', qid, str(uuid.uuid4()))
        submission = service.submit({'request_id': str(uuid.uuid4()), 'attempt_id': attempt, 'answer': answer, 'elapsed_ms': elapsed})
        service.process_submission(submission)
        return attempt

    def test_complete_catalog_unstarted_empty_metrics_and_read_only(self):
        before = self.snapshot(); services = list(self.app.state.learning_services)
        response = self.get(); self.assertEqual(response.status_code, 200, response.text)
        data = response.json()['analytics']
        self.assertEqual(len(data['topics']), 2)
        self.assertTrue(all(t['status'] == 'not_started' and t['done'] == 0 for t in data['topics']))
        self.assertEqual({t['topic_id']: t['total'] for t in data['topics']}, {self.course['id']: 2, 'atoms': 3})
        self.assertEqual(data['banks'][0]['total'], 6); self.assertEqual(data['banks'][0]['answered'], 0)
        self.assertIsNone(data['period']['accuracy']); self.assertEqual(data['period']['timed_attempts'], 0)
        self.assertEqual(data['period']['active_days'], 0); self.assertEqual(len(data['activity']), 30)
        self.assertEqual(self.get('/questions', kind='topic', container=self.course['id']).json()['total'], 32)
        self.assertEqual(self.get('/questions', kind='notebook').json()['total'], 0)
        self.assertEqual(self.snapshot(), before); self.assertEqual(list(self.app.state.learning_services), services)

    def test_training_metrics_notebook_enrollment_and_full_history(self):
        service = self.training()
        self.answer(service, answer='0'); self.time += timedelta(seconds=2); self.answer(service, answer='2')
        essay = service.open('isolated-bank', 'group-a', 'essay', 'self-rating'); service.reveal(essay); service.rate(essay, 2)
        manual = service.open('isolated-bank', 'group-a', 'blank', 'manual-enrollment'); service.add_to_notebook(manual)
        before = self.snapshot(); data = self.get().json()['analytics']
        stats = data['lifetime']; self.assertEqual(stats['attempts'], 3)
        self.assertEqual((stats['judged'], stats['correct'], stats['wrong'], stats['self_rated']), (2, 1, 1, 1))
        self.assertEqual(stats['accuracy'], .5); self.assertEqual(stats['first_accuracy'], 0)
        self.assertEqual((stats['elapsed_ms'], stats['timed_attempts']), (3000, 2))
        self.assertEqual(data['banks'][0]['answered'], 2); self.assertEqual(data['notebook']['total'], 3)
        self.assertEqual(data['notebook']['unanswered'], 1)
        history = self.get('/history', kind='training', container='isolated-bank', question='exact', limit=1).json()
        self.assertEqual(history['total'], 2); self.assertEqual(history['next_offset'], 1)
        item = history['items'][0]; self.assertEqual(item['answer'], '2'); self.assertTrue(item['correct'])
        self.assertIn('一加一', item['html']); self.assertIn('等于二', item['explanation_html'])
        tail = self.get('/history', kind='training', container='isolated-bank', question='exact', offset=1).json()
        self.assertEqual(tail['items'][0]['answer'], '0'); self.assertIsNone(tail['next_offset'])
        self.assertEqual(self.get('/questions', kind='notebook', state='unanswered').json()['total'], 1)
        self.assertEqual(self.get('/questions', kind='training', state='wrong').json()['total'], 1)
        self.assertEqual(self.snapshot(), before)

    def test_stopped_opened_and_failed_attempts_do_not_inflate_activity(self):
        service = self.training()
        self.answer(service, answer='2')  # Score 5 stops subsequent delivery.
        service.open('isolated-bank', 'group-a', 'blank', 'only-opened')
        essay = service.open('isolated-bank', 'group-a', 'essay', 'judge-failure')
        sub = service.submit({'request_id': 'failed-submission', 'attempt_id': essay, 'answer': 'private draft'})
        service.process_submission(sub)  # No semantic judge configured; not a valid answer.
        data = self.get().json()['analytics']
        self.assertEqual(data['lifetime']['attempts'], 1)
        self.assertEqual(data['notebook']['stopped'], 1); self.assertEqual(data['notebook']['due'], 0)
        response = self.get('/history', kind='training', container='isolated-bank')
        self.assertNotIn('private draft', response.text); self.assertEqual(response.json()['total'], 1)

    def test_timezone_window_and_calendar_are_explicit(self):
        self.time = datetime(2026, 10, 5, 23, 30, tzinfo=timezone.utc)
        self.answer(self.training(), answer='0')
        response = self.get(days=1, timezone='Asia/Shanghai'); data = response.json()['analytics']
        self.assertEqual(data['activity'][0]['date'], '2026-10-06'); self.assertEqual(data['activity'][0]['attempts'], 1)
        self.assertEqual(data['calendar'][0]['date'], '2026-10-06')
        self.assertEqual(self.get(timezone='not/a-zone').status_code, 422)
        self.assertEqual(self.get(days=367).status_code, 422)

    def test_hidden_test_materials_never_leak_through_new_drilldowns(self):
        self.answer(self.training(), answer='0')
        self.store.test_materials.permission_ids.update({'training.isolated-bank', 'atoms'})
        before = self.snapshot(); data = self.get().json()['analytics']
        self.assertEqual(data['banks'], []); self.assertEqual(data['lifetime']['attempts'], 0)
        self.assertEqual(len(data['topics']), 1)
        self.assertEqual(self.get('/questions', kind='notebook').json()['total'], 0)
        for path in ['/questions', '/history']:
            self.assertEqual(self.get(path, kind='training', container='isolated-bank').status_code, 404)
        self.assertEqual(self.get('/questions', kind='notebook', container='isolated-bank').status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_all_new_routes_enforce_roles_and_target_identity(self):
        self.answer(self.training(), answer='0')
        before = self.snapshot()
        for path in ['/questions?kind=training&container=isolated-bank', '/history?kind=training&container=isolated-bank']:
            base = '/api/admin/students/' + self.student + path
            for headers in [self.headers, self.normal_headers]:
                self.assertEqual(self.client.get(base, headers=headers).status_code, 403)
            self.assertEqual(self.client.get(base, headers={'Origin': 'https://site.test'}).status_code, 401)
            self.assertEqual(self.client.post(base, headers=self.admin_headers, json={}).status_code, 405)
            self.assertEqual(self.client.get('/api/admin/students/' + self.second + path, headers=self.admin_headers).json()['total'], 6 if path.startswith('/questions') else 0)
        self.assertEqual(self.client.get('/api/admin/students/missing/history?kind=training&container=x', headers=self.admin_headers).status_code, 404)
        self.assertEqual(self.get('/questions', kind='invalid').status_code, 422)
        self.assertEqual(self.get('/history', kind='training', container='isolated-bank', limit=31).status_code, 422)
        self.assertEqual(self.snapshot(), before)

    def test_lesson_history_missing_old_content_and_skipped_progress(self):
        learner = self.learner()
        state = learner.choose(self.action(learner.state(), choice='mastered'))
        result = learner.submit(self.action(state, answer='correct', elapsed_ms=2300)); learner.process_submission(result['submission_id'])
        data = self.get().json()['analytics']; topic = next(t for t in data['topics'] if t['topic_id'] == self.course['id'])
        self.assertEqual(topic['done'], 1); self.assertEqual(topic['stats']['attempts'], 1)
        history = self.get('/history', kind='topic', container=self.course['id']).json()
        self.assertTrue(history['items'][0]['content_available']); self.assertIn('QUESTION::', history['items'][0]['html'])
        self.assertNotIn('REFERENCE_ONLY', json.dumps(history))
        with closing(sqlite3.connect(self.root / 'learning.sqlite3')) as con:
            con.execute('UPDATE topics SET version=? WHERE learner=?', ('older-version', self.student)); con.commit()
        before = self.snapshot()
        old = self.get('/history', kind='topic', container=self.course['id']).json()['items'][0]
        self.assertFalse(old['content_available']); self.assertNotIn('html', old); self.assertEqual(old['answer'], 'correct')
        topic = next(t for t in self.get().json()['analytics']['topics'] if t['topic_id'] == self.course['id'])
        self.assertEqual(topic['done'], 0); self.assertFalse(topic['version_current']); self.assertEqual(self.snapshot(), before)

    def test_skipped_modules_are_not_completed_topics_and_questions_keep_author_order(self):
        learner = self.learner()
        with learner._transaction() as con:
            state = learner._load(con)
            state.update(status='completed', completed_at=self.time.isoformat(), skipped_modules={'module-1': {}, 'module-2': {}})
            learner._save(con, state)
        before = self.snapshot()
        topic = next(t for t in self.get().json()['analytics']['topics'] if t['topic_id'] == self.course['id'])
        self.assertEqual(topic['status'], 'in_progress'); self.assertEqual(topic['done'], 0)
        self.assertTrue(all(m['status'] == 'skipped' for m in topic['modules']))
        questions = self.get('/questions', kind='topic', container=self.course['id'], limit=100).json()['items']
        self.assertEqual([q['question'] for q in questions[:4]], ['module-1-example', 'module-1-practice-1', 'module-1-practice-2', 'module-1-practice-3'])
        self.assertEqual(self.snapshot(), before)

    def test_paper_training_results_are_counted_once_and_keep_original_snapshot(self):
        service = self.training(); self.answer(service, qid='choice', answer='{"selected":"b"}')
        self.time += timedelta(days=2)
        access = self.store.resolve(self.headers['X-Learning-Identity'])
        papers = PaperService(service, self.app.state.catalog, self.app.state.admin_students.dashboard.dependency_policy,
            access, [], lambda tid: self.learner(tid))
        candidate = next(c for c in papers.inbox('math')['items'] if c['kind'] == 'single')
        pid = papers.open(candidate['id'], 'paper-once', 'math')
        state = papers.state(pid)
        papers.save(pid, state['revision'], [{'index': 0, 'answer': '{"selected":"a"}', 'elapsed_ms': 9000}])
        state = papers.state(pid)
        sid = papers.submit({'paper_id': pid, 'request_id': 'grade-once', 'expected_revision': state['revision']})
        papers.process_submission(sid); self.assertEqual(papers.state(pid)['phase'], 'done')
        before = self.snapshot(); data = self.get().json()['analytics']
        self.assertEqual(data['lifetime']['attempts'], 2); self.assertEqual(data['lifetime']['elapsed_ms'], 10500)
        history = self.get('/history', kind='training', container='isolated-bank', question='choice').json()
        self.assertEqual(history['total'], 2); self.assertEqual(history['items'][0]['interaction']['type'], 'choice')
        self.assertEqual(self.snapshot(), before)

    def test_atomic_history_deduplicates_submissions_and_combines_variants_by_point(self):
        learner = self.learner('atoms')
        state = learner.choose(self.action(learner.state(), choice='learn'))
        state = learner.advance(self.action(state))
        result = learner.submit(self.action(state, answer='错误', elapsed_ms=1000)); learner.process_submission(result['submission_id'])
        with learner._transaction() as con:
            saved = learner._load(con)
            original = saved['history'][0]
            saved['history'].append({**original, 'id': 'paper:0', 'card_id': 'a-2', 'mode': 'review', 'snapshot': self.atoms['flashcards'][1]})
            learner._save(con, saved)
        before = self.snapshot(); data = self.get().json()['analytics']
        self.assertEqual(data['lifetime']['attempts'], 2); self.assertEqual(data['lifetime']['timed_attempts'], 1)
        self.assertEqual(data['weak_points'][0]['point'], 'a'); self.assertEqual(data['weak_points'][0]['failures'], 2)
        history = self.get('/history', kind='topic', container='atoms', point='a').json()
        self.assertEqual(history['total'], 2); self.assertTrue(all(i['content_available'] for i in history['items']))
        self.assertEqual(self.snapshot(), before)
