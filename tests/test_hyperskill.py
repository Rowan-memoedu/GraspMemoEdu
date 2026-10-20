from contextlib import closing
from datetime import datetime, timezone
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from graspmemoedu.hyperskill import HyperskillLearningService
from graspmemoedu.atomic_content import validate_atomic
from graspmemoedu.service import DomainError, create_learning_service
from test_academy import exercise
from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings


def topic():
    value = exercise()
    value.pop('academy')
    value.update(source_adapter='hyperskill', hyperskill={'topic_id': 101, 'theory_id': 102},
                 memory_chunks=[], memory_points=[{'id': str(i), 'text': 'Problem ' + str(i)} for i in range(8)])
    original = value['flashcards'][0]
    value['flashcards'] = [{**original, 'id': 'p' + str(i), 'point_ids': [str(i)],
                           'reference_answer': '', 'explanation_html': '', 'interaction': None,
                           'hyperskill': {'step_id': 200 + i, 'type': 'choice'}} for i in range(8)]
    return value


class HyperskillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime.now(timezone.utc)
        self.backend = HyperskillLearningService(topic(), Path(self.temp.name), clock=lambda: self.now)

    def payload(self, **extra):
        state = self.backend.state()
        return dict(request_id=str(uuid.uuid4()), expected_revision=state['revision'],
                    attempt_id=state['attempt_id'], step_id=state['active_step_id'], **extra)

    def start(self):
        self.backend.choose(self.payload(choice='learn'))

    def launch(self):
        run = self.backend.launch(self.payload())
        self.backend.opened(run['round_id'], run['step_id'])
        return run

    def event(self, run, number, sequence=1, correct=True):
        when = self.now.isoformat()
        return {'id': str(uuid.uuid4()), 'sequence': sequence, 'category': 'passed' if correct else 'wrong',
                'code_hash': str(sequence % 10) * 64, 'occurred_at': when,
                'receipt': {'submission_id': number, 'attempt_id': number + 100,
                            'step_id': self.backend.state()['current']['hyperskill']['step_id'],
                            'status': 'correct' if correct else 'wrong', 'time': when,
                            'reply': {'choices': [True, False]}, 'answer_display': 'A', 'feedback': ''}}

    def test_eight_independent_memories_without_chunks_and_complete_only_at_end(self):
        from graspmemoedu.topic_runtime import learning_units
        validate_atomic(topic())
        self.assertEqual(learning_units(topic()), 8)
        self.assertIsInstance(create_learning_service(topic(), Path(self.temp.name)), HyperskillLearningService)
        self.assertEqual(self.backend.state()['point_count'], 8)
        self.start()
        for i in range(8):
            state = self.backend.state()
            self.assertIsNone(state['chunk'])
            run = self.launch()
            event = self.event(run, i + 1)
            self.backend.check_event(run['round_id'], run['step_id'], event)
            state = self.backend.state()
            self.assertEqual(state['learned_count'], i + 1)
            self.assertEqual(state['status'], 'completed' if i == 7 else 'in_progress')
            self.backend.advance(self.payload())
            self.assertTrue(self.backend.check_event(run['round_id'], run['step_id'], event)['duplicate'])
        with closing(self.backend._connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM events WHERE json_extract(body,'$.type')='atomic_rating'").fetchone()[0], 8)
        self.assertEqual(self.backend.state()['phase'], 'completed')

    def test_wrong_then_pass_and_receipt_identity(self):
        self.start(); run = self.launch()
        bad = self.event(run, 1, correct=False)
        self.backend.check_event(run['round_id'], run['step_id'], bad)
        self.assertEqual(self.backend.state()['learned_count'], 0)
        good = self.event(run, 2, sequence=2)
        self.assertEqual(self.backend.check_event(run['round_id'], run['step_id'], good)['rating'], 2)
        receipt = self.backend.state()['current']['hyperskill']['submissions']
        self.assertEqual([r['submission_id'] for r in receipt], [1, 2])
        self.backend.advance(self.payload())
        new_run = self.launch()
        stolen = self.event(new_run, 2)
        with self.assertRaises(DomainError):
            self.backend.check_event(new_run['round_id'], new_run['step_id'], stolen)

    def test_mismatched_problem_and_ungraded_receipt_do_not_schedule(self):
        self.start(); run = self.launch()
        for mutation in ('step', 'status', 'category'):
            event = self.event(run, 1)
            if mutation == 'step': event['receipt']['step_id'] += 1
            if mutation == 'status': event['receipt']['status'] = 'evaluation'
            if mutation == 'category': event['category'] = 'wrong'
            with self.assertRaises(DomainError):
                self.backend.check_event(run['round_id'], run['step_id'], event)
        self.assertEqual(self.backend.state()['learned_count'], 0)
        broken = copy.deepcopy(topic())
        broken['flashcards'][0]['reference_answer'] = 'invented'
        with self.assertRaises(ValueError): validate_atomic(broken)

    def test_capability_binds_internal_round_and_external_problem_separately(self):
        root = Path(self.temp.name) / 'api'; root.mkdir()
        value = topic()
        (root / 'topic.json').write_text(json.dumps(value), encoding='utf-8')
        (root / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'cs', 'title': 'CS',
            'topics': [{'id': value['id'], 'path': 'topic.json'}]}]}), encoding='utf-8')
        (root / 'subjects.json').write_text(json.dumps({'subjects': [{'id': 'cs', 'course_ids': ['cs']}]}), encoding='utf-8')
        app = create_app(Settings(data_dir=root, public_api_origin='https://cs.test'), atomic_clock=lambda: self.now)
        with TestClient(app, base_url='https://cs.test') as client:
            headers = {'origin': 'https://cs.test'}
            def post(route, body, auth=headers):
                result = client.post('/api/' + route, json=body, headers=auth)
                self.assertEqual(result.status_code, 200, result.text)
                return result.json()
            guest = post('access/guest', {})
            headers['x-learning-identity'] = guest['identity_token']
            scope = '?topic_id=' + value['id'] + '&subject_id=cs'
            client.get('/api/state' + scope, headers=headers)
            self.backend = app.state.learning_services[(guest['access']['learner_id'], value['id'])]
            post('choose' + scope, self.payload(choice='learn'))
            grant = post('academy/launch' + scope, self.payload())
            self.assertNotIn('url', grant)
            local = {'origin': 'https://cs.test', 'authorization': 'Bearer ' + grant['ticket']}
            binding = post('academy/claim', {}, local)
            self.assertEqual(binding['provider'], 'hyperskill')
            self.assertEqual(binding['step_id'], 200)
            post('academy/event', {'kind': 'opened'}, local)
            event = self.event(binding, 77)
            post('academy/event', {'kind': 'check', 'event': event}, local)
            state = client.get('/api/state' + scope, headers=headers).json()
            self.assertEqual(state['learned_count'], 1)
            self.assertEqual(state['current']['hyperskill']['submissions'][0]['submission_id'], 77)
