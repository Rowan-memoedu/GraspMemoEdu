"""Mixed Topic/group dependencies share eligibility, graph storage and API gates."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.course_graph import GraphStore, import_graph, validate_graph
from graspmemoedu.dependency_nodes import group_key
from graspmemoedu.service import LearningService
from graspmemoedu.question_types import demo_answer
from graspmemoedu.subject_registry import DEFAULTS
from tests.fixtures import DeterministicJudge, temporary_test_directory
from tests.subject_matrix_fixture import write_matrix


def mixed_graph(subject):
    bank = 'bank-' + subject
    return {'schema_version': 3, 'subject_id': subject, 'course_id': bank, 'title': 'Mixed graph',
            'nodes': [{'topic_id': 'lesson-' + subject, 'title': 'Prerequisite Topic', 'member': False},
                      {'topic_id': 'atomic-' + subject, 'title': 'Downstream Topic', 'member': False},
                      {'bank_id': bank, 'group_id': 'group-a', 'title': 'First group', 'member': True},
                      {'bank_id': bank, 'group_id': 'group-b', 'title': 'Second group', 'member': True}],
            'edges': [{'from': 'lesson-' + subject, 'to': group_key(bank, 'group-a'), 'type': 'prerequisite'},
                      {'from': group_key(bank, 'group-a'), 'to': group_key(bank, 'group-b'), 'type': 'prerequisite'},
                      {'from': group_key(bank, 'group-b'), 'to': 'atomic-' + subject, 'type': 'prerequisite'}]}


class GroupDependencyTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.topics = write_matrix(self.root)
        self.settings = Settings(data_dir=self.root)
        self.app = create_app(self.settings, LearningService(self.topics['lesson-math'], self.root, DeterministicJudge()))
        self.client = self.enterContext(TestClient(self.app, base_url='http://127.0.0.1:8767'))
        self.headers = {'Origin': 'http://127.0.0.1:8767'}
        self.headers['X-Learning-Session'] = self.client.get('/api/session', headers=self.headers).json()['session_token']
        login = self.client.post('/api/access/guest', headers=self.headers, json={}).json()
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.access = self.app.state.access_store.resolve(login['identity_token'])
        self.backend = self.app.state.training_backend_for(self.access)
        self.backend.judge = lambda stem, reference, answer: {'status': 'judged', 'correct': answer == '2', 'reason': 'Synthetic exact answer'}
        self.policy = self.backend.dependencies

    def install(self, graph):
        path = self.root / 'graph-source.json'
        path.write_text(json.dumps(graph), encoding='utf-8')
        return import_graph(self.settings, path, apply=True)

    def complete_topic(self, subject):
        service = LearningService(self.topics['lesson-' + subject], self.root, DeterministicJudge(),
                                  learner_id=self.access['learner_id'])
        with service._transaction() as con:
            state = service._load(con)
            state['status'] = 'completed'
            state['completed_at'] = datetime.now(timezone.utc).isoformat()
            state['attempts'][0].update(status='completed', mastery=5, completed_at=state['completed_at'])
            service._save(con, state)
        return service

    def finish_group(self, subject, group_id):
        bank = self.backend.catalog.bank('bank-' + subject)
        group = next(g for g in bank['groups'] if g['id'] == group_id)
        for question in group['questions']:
            attempt = self.backend.open(bank['id'], group_id, question['id'], str(uuid4()))
            self.backend.add_to_notebook(attempt)
            with closing(self.backend._connect()) as con:
                snapshot = json.loads(self.backend._attempt(con, attempt)['snapshot'])
            submission = self.backend.submit({'attempt_id': attempt, 'request_id': str(uuid4()), 'answer': demo_answer(snapshot)})
            self.backend.process_submission(submission)
            self.assertEqual(self.backend.state(attempt)['phase'], 'done')

    def call(self, method, path, body=None, status=200):
        result = self.client.request(method, '/api/' + path, headers=self.headers, **({'json': body} if body else {}))
        self.assertEqual(result.status_code, status, result.text)
        return result.json()

    def test_all_subjects_topic_to_group_to_topic_use_one_policy(self):
        for subject, *_ in DEFAULTS:
            with self.subTest(subject=subject):
                self.install(mixed_graph(subject))
                bank = 'bank-' + subject
                self.assertFalse(self.policy.is_ready(self.access, group_key(bank, 'group-a')))
                self.complete_topic(subject)
                self.assertTrue(self.policy.is_ready(self.access, group_key(bank, 'group-a')))
                self.assertFalse(self.policy.is_ready(self.access, group_key(bank, 'group-b')))
                self.finish_group(subject, 'group-a')
                self.assertTrue(self.policy.is_ready(self.access, group_key(bank, 'group-b')))
                self.assertFalse(self.policy.is_ready(self.access, 'atomic-' + subject))
                self.finish_group(subject, 'group-b')
                self.assertTrue(self.policy.is_ready(self.access, 'atomic-' + subject))
                dashboard = self.call('GET', 'dashboard?course_id=course-' + subject + '&subject_id=' + subject)
                topic = next(t for t in dashboard['topics'] if t['id'] == 'atomic-' + subject)
                self.assertTrue(topic['dependency_ready'])
                self.assertEqual(topic['prerequisite_details'][0]['kind'], 'group')

    def test_api_preview_open_submission_and_graph_follow_live_changes(self):
        graph = mixed_graph('cs'); self.install(graph)
        bank = self.call('GET', 'training/bank?bank_id=bank-cs&subject_id=cs')
        group = next(g for g in bank['groups'] if g['id'] == 'group-a')
        self.assertFalse(group['dependency_ready'])
        self.assertEqual(group['prerequisites'][0]['href'], '/subjects/cs/topic/lesson-cs')
        body = {'request_id': str(uuid4()), 'bank_id': 'bank-cs', 'group_id': 'group-a', 'question_id': 'exact'}
        self.call('POST', 'training/open?subject_id=cs', body, 409)
        self.call('GET', 'training/group?bank_id=bank-cs&group_id=group-a&reader=1&subject_id=cs', status=409)
        self.complete_topic('cs')
        opened = self.call('POST', 'training/open?subject_id=cs', body)
        graph['nodes'].append({'topic_id': 'pending', 'title': 'Do not leak', 'member': False})
        graph['edges'].append({'from': 'pending', 'to': group_key('bank-cs', 'group-a'), 'type': 'prerequisite'})
        self.install(graph)
        state = self.call('GET', 'training/state?attempt_id=' + opened['attempt_id'] + '&subject_id=cs')
        self.assertFalse(state['can_submit'])
        self.call('POST', 'training/submit?subject_id=cs', {'request_id': str(uuid4()), 'attempt_id': opened['attempt_id'], 'answer': '2'}, 409)
        public = self.call('GET', 'graphs/bank-cs?subject_id=cs')
        self.assertNotIn('Do not leak', json.dumps(public))
        self.assertEqual(next(n for n in public['nodes'] if n.get('group_id') == 'group-a')['href'], '/subjects/cs/banks/bank-cs/group-a')
        self.assertFalse(self.call('GET', 'training/group?bank_id=bank-cs&group_id=group-a&reader=1&subject_id=cs')['dependency_ready'])
        with closing(self.backend._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_submissions').fetchone()[0], 0)

    def test_due_group_blocks_downstream_but_stopped_questions_do_not(self):
        self.install(mixed_graph('math')); self.complete_topic('math'); self.finish_group('math', 'group-a')
        self.assertTrue(self.policy.is_ready(self.access, group_key('bank-math', 'group-b')))
        with self.backend.transaction() as con:
            con.execute('UPDATE training_memory SET due_at=? WHERE bank=?',
                        ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), 'bank-math'))
        self.assertFalse(self.policy.is_ready(self.access, group_key('bank-math', 'group-b')))
        with self.backend.transaction() as con:
            con.execute('''INSERT INTO training_suspensions SELECT learner,bank,question,version,?
                FROM training_memory WHERE bank=?''', (datetime.now(timezone.utc).isoformat(), 'bank-math'))
        self.assertTrue(self.policy.is_ready(self.access, group_key('bank-math', 'group-b')))

    def test_permissions_missing_nodes_and_mixed_cycles_are_not_bypassed(self):
        graph = mixed_graph('math'); self.install(graph); self.complete_topic('math')
        limited = {**self.access, 'topics': [{'id': 'training.bank-math'}]}
        self.assertFalse(self.policy.is_ready(limited, group_key('bank-math', 'group-a')))
        with closing(self.backend._connect()) as con:
            self.assertEqual(self.policy.group_status(con, limited, 'bank-math', 'group-a')['prerequisites'], [])
        cyclic = deepcopy(graph)
        cyclic['edges'].append({'from': 'atomic-math', 'to': 'lesson-math', 'type': 'prerequisite'})
        with self.assertRaisesRegex(ValueError, 'cycle'):
            validate_graph(cyclic)
        separate = deepcopy(graph)
        separate['course_id'] = 'course-math'
        for node in separate['nodes']:
            node['member'] = 'topic_id' in node
        separate['edges'] = [{'from': 'atomic-math', 'to': 'lesson-math', 'type': 'prerequisite'}]
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.install(separate)
        self.assertEqual(self.install(graph)['status'], 'unchanged')

    def test_superseded_review_round_does_not_keep_downstream_locked(self):
        self.install(mixed_graph('math')); self.complete_topic('math'); self.finish_group('math', 'group-a')
        with self.backend.transaction() as con:
            con.execute("UPDATE training_memory SET due_at=? WHERE bank='bank-math' AND question='exact'",
                        ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
        stale = self.backend.open('bank-math', 'group-a', 'exact', str(uuid4()))
        fresh = self.backend.open('bank-math', 'group-a', 'exact', str(uuid4()))
        self.assertFalse(self.policy.is_ready(self.access, group_key('bank-math', 'group-b')))
        job = self.backend.submit({'attempt_id': fresh, 'request_id': str(uuid4()), 'answer': '2'})
        self.backend.process_submission(job)
        self.assertEqual(self.backend.state(stale)['phase'], 'answer')
        self.assertTrue(self.policy.is_ready(self.access, group_key('bank-math', 'group-b')))

    def test_incorrect_initial_answer_is_not_a_completed_prerequisite(self):
        self.install(mixed_graph('cs')); self.complete_topic('cs'); self.finish_group('cs', 'group-a')
        attempt = self.backend.open('bank-cs', 'group-b', 'essay-b', str(uuid4()))
        job = self.backend.submit({'attempt_id': attempt, 'request_id': str(uuid4()), 'answer': 'wrong'})
        self.backend.process_submission(job)
        self.assertEqual(self.backend.state(attempt)['phase'], 'done')
        self.assertFalse(self.policy.is_ready(self.access, 'atomic-cs'))

    def test_completion_boolean_is_available_without_attempt_history(self):
        self.complete_topic('cs'); self.finish_group('cs', 'group-b')
        group = self.backend.group('bank-cs', 'group-b', history=False)
        self.assertTrue(group['questions'][0]['completed'])
        self.assertIsNone(group['questions'][0]['completed_count'])
        self.assertEqual(GraphStore(self.settings).public(validate_graph(mixed_graph('cs')), self.access)['schema_version'], 3)
