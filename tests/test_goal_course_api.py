"""Shared graph, dashboard, bank and grading contract across every subject."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from fsrs import Card, State
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.course_graph import import_graph
from graspmemoedu.service import LearningService
from graspmemoedu.subject_registry import SUBJECT_IDS
from graspmemoedu.training_content import fingerprint
from tests.fixtures import DeterministicJudge, temporary_test_directory
from tests.subject_matrix_fixture import write_matrix


class GoalCourseApiTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.topics = write_matrix(self.root)
        self.when = datetime(2026, 10, 23, tzinfo=timezone.utc)
        for subject in SUBJECT_IDS:
            path = self.root / 'training-banks' / ('bank-' + subject + '.json')
            bank = json.loads(path.read_text(encoding='utf-8'))
            group = next(g for g in bank['groups'] if g['id'] == 'group-a')
            q = next(q for q in group['questions'] if q['id'] == 'exact')
            q['point_ids'] = ['atomic-' + subject + '/a']
            group['questions'] = [q]
            group['unlock_policy'] = {'point_ids': ['atomic-' + subject + '/' + p for p in ('a', 'b')]}
            bank['version'] = fingerprint(bank)
            path.write_text(json.dumps(bank), encoding='utf-8')
        self.settings = Settings(data_dir=self.root)
        self.app = create_app(self.settings, LearningService(self.topics['lesson-math'], self.root, DeterministicJudge()),
                              atomic_clock=lambda: self.when)
        self.client = self.enterContext(TestClient(self.app, base_url='http://127.0.0.1:8767'))
        self.headers = {'Origin': 'http://127.0.0.1:8767'}
        self.headers['X-Learning-Session'] = self.client.get('/api/session', headers=self.headers).json()['session_token']
        login = self.client.post('/api/access/guest', headers=self.headers, json={}).json()
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.learner = login['access']['learner_id']

    def call(self, path, body=None, status=200):
        response = self.client.request('GET' if body is None else 'POST', '/api/' + path,
                                       headers=self.headers, **({'json': body} if body is not None else {}))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_course_and_bank_share_live_unlock_and_source_progress_for_all_subjects(self):
        for subject in SUBJECT_IDS:
            with self.subTest(subject=subject):
                tid, bid, cid = 'atomic-' + subject, 'bank-' + subject, 'course-' + subject
                graph = {'schema_version': 3, 'subject_id': subject, 'course_id': cid, 'title': 'Shared course',
                    'nodes': [{'topic_id': tid, 'title': 'Atomic', 'member': True},
                              {'bank_id': bid, 'group_id': 'group-a', 'title': 'Goal', 'member': True}], 'edges': []}
                source = self.root / ('graph-' + subject + '.json')
                source.write_text(json.dumps(graph), encoding='utf-8')
                import_graph(self.settings, source, apply=True)
                def dashboard_group():
                    return next(t for t in self.call('dashboard?subject_id=' + subject + '&course_id=' + cid)['tasks'] if t.get('bank_id') == bid)
                def bank_group():
                    return next(g for g in self.call('training/bank?subject_id=' + subject + '&bank_id=' + bid)['groups'] if g['id'] == 'group-a')
                self.assertFalse(dashboard_group()['unlocked'])
                self.assertFalse(bank_group()['unlocked'])
                projected = self.call('graphs/' + cid + '?subject_id=' + subject)
                self.assertTrue(any(e['from'] == tid and e.get('scope') == 'memory_points' for e in projected['edges']))
                self.call('training/group?reader=1&bank_id=' + bid + '&group_id=group-a&subject_id=' + subject, status=409)
                self.call('state?subject_id=' + subject + '&topic_id=' + tid)
                backend = next(s for s in self.app.state.learning_services.values()
                               if s.learner_id == self.learner and s.topic_id == tid)
                with backend._transaction() as con:
                    state = backend._load(con)
                    state.update(active=None, mode='review', phase='complete', status='in_progress', completed_at=None)
                    for p, memory in state['points'].items():
                        card = Card(state=State.Review, stability=10, difficulty=5,
                                    last_review=self.when - timedelta(days=1), due=self.when + timedelta(days=9))
                        memory.update(mastered=True, card_json=card.to_json(), due_at=card.due.isoformat(),
                                      first_passed_at=(self.when - timedelta(days=4)).isoformat(), last_rating=3)
                        state['history'].append({'mode': 'review', 'ratings': {p: 3}, 'answered_at': (self.when - timedelta(days=1)).isoformat()})
                    backend._save(con, state)
                self.assertTrue(dashboard_group()['unlocked'])
                self.assertTrue(bank_group()['unlocked'])
                opened = self.call('training/open?subject_id=' + subject, {'bank_id': bid, 'group_id': 'group-a',
                                  'question_id': 'exact', 'request_id': str(uuid4())})
                self.assertTrue(opened['can_submit'])
                self.when += timedelta(days=20)
                self.assertFalse(dashboard_group()['unlocked'])
                self.assertFalse(bank_group()['unlocked'])
                state = self.call('training/state?subject_id=' + subject + '&attempt_id=' + opened['attempt_id'])
                self.assertFalse(state['can_submit'])
                self.call('training/submit?subject_id=' + subject,
                    {'attempt_id': opened['attempt_id'], 'request_id': str(uuid4()), 'answer': '2'}, status=409)

    def test_admin_preview_uses_same_ephemeral_unlock_without_writing_progress(self):
        from contextlib import closing
        store = self.app.state.access_store
        with store._transaction() as con:
            account = store._insert_account(con, 'Goal preview', is_admin=True)
        login = store.login(account['invitation_code'])
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.learner = login['access']['learner_id']
        self.test_course_and_bank_share_live_unlock_and_source_progress_for_all_subjects()
        with closing(store._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM topics WHERE learner=?', (self.learner,)).fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_attempts WHERE learner=?', (self.learner,)).fetchone()[0], 0)

    def test_point_prerequisite_cannot_create_a_hidden_graph_cycle(self):
        from graspmemoedu.dependency_nodes import group_key
        source = self.root / 'cycle.json'
        source.write_text(json.dumps({'schema_version': 3, 'subject_id': 'english',
            'course_id': 'course-english', 'title': 'Cycle',
            'nodes': [{'topic_id': 'atomic-english', 'title': 'Atomic', 'member': True},
                      {'bank_id': 'bank-english', 'group_id': 'group-a', 'title': 'Goal', 'member': True}],
            'edges': [{'from': group_key('bank-english', 'group-a'), 'to': 'atomic-english',
                       'type': 'prerequisite'}]}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'cycle'):
            import_graph(self.settings, source, apply=True)

    def test_bank_install_rejects_unknown_point_before_activation(self):
        from graspmemoedu.training_content import install
        bank = json.loads((self.root / 'training-banks/bank-english.json').read_text(encoding='utf-8'))
        bank['id'] = 'unbound-bank'
        group = next(g for g in bank['groups'] if g['id'] == 'group-a')
        group['unlock_policy'] = {'point_ids': ['missing-topic/point']}
        group['questions'][0]['point_ids'] = ['missing-topic/point']
        bank['version'] = fingerprint(bank)
        source = self.root / 'unbound-source.json'
        source.write_text(json.dumps(bank), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unknown point Topic'):
            install(source, self.root)
        self.assertFalse((self.root / 'training-banks/unbound-bank.json').exists())


if __name__ == '__main__':
    unittest.main()
