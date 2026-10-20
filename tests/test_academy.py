from contextlib import closing
from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient
from fsrs import Card
from graspmemoedu.academy import AcademyLearningService, outcome
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import DomainError
from atomic_fixtures import atomic_topic


def exercise(kind='automated'):
    topic = atomic_topic('cs', 'academy-fixture')
    topic['memory_chunks'] = topic['memory_chunks'][:1]
    topic['memory_chunks'][0]['points'] = topic['memory_chunks'][0]['points'][:1]
    topic['flashcards'] = [c for c in topic['flashcards'] if c['point_ids'] == ['a']][:1]
    topic['academy'] = {'task_key': 'synthetic-exercise', 'kind': kind}
    return topic


class AcademyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.when = datetime.now(timezone.utc)
        self.backend = AcademyLearningService(exercise(), self.root, clock=lambda: self.when)

    def payload(self, **extra):
        state = self.backend.state()
        return dict(request_id=str(uuid.uuid4()), expected_revision=state['revision'],
                    attempt_id=state['attempt_id'], step_id=state['active_step_id'], **extra)

    def launch(self, mode='learn'):
        self.backend.choose(self.payload(choice=mode))
        run = self.backend.launch(self.payload())
        self.backend.opened(run['round_id'], run['step_id'])
        return run

    def event(self, run, category, seq, code=None):
        return {'id': str(uuid.uuid4()), 'sequence': seq, 'category': category,
                'code_hash': (code or str(seq % 10)) * 64, 'occurred_at': self.when.isoformat()}

    def send(self, run, category, seq, code=None):
        event = self.event(run, category, seq, code)
        return self.backend.check_event(run['round_id'], run['step_id'], event)

    def test_weighted_mapping_and_same_failure_dedup(self):
        events = [{'category': 'compile', 'code_hash': 'a'}] * 8
        events += [{'category': 'runtime', 'code_hash': 'b'}, {'category': 'passed', 'code_hash': 'c'}]
        self.assertEqual(outcome(events)['burden'], .75)
        self.assertEqual(outcome(events)['rating'], 2)
        self.assertEqual(outcome([{'category': 'passed', 'code_hash': 'a'}])['rating'], 3)
        self.assertEqual(outcome([{'category': 'environment', 'code_hash': 'a'}], finished=True)['rating'], None)
        self.assertEqual(outcome([{'category': 'wrong', 'code_hash': str(i)} for i in range(3)]
                                 + [{'category': 'passed', 'code_hash': 'x'}])['rating'], 1)

    def test_real_fsrs_identity_survives_failed_and_passed_rounds(self):
        run = self.launch()
        self.send(run, 'wrong', 1)
        state = self.backend.finish(self.payload())
        self.assertEqual(state['current']['ratings'], {'a': 1})
        with closing(self.backend._connect()) as con:
            before = self.backend._load(con)['points']['a']
        self.assertIsNone(before['first_passed_at'])
        self.backend.advance(self.payload())
        self.when = datetime.fromisoformat(before['due_at']) + timedelta(seconds=1)
        run = self.launch('review')
        event = self.event(run, 'passed', 1)
        result = self.backend.check_event(run['round_id'], run['step_id'], event)
        self.assertEqual(result['rating'], 3)
        self.assertTrue(self.backend.check_event(run['round_id'], run['step_id'], event)['duplicate'])
        with closing(self.backend._connect()) as con:
            after = self.backend._load(con)['points']['a']
            self.assertEqual(con.execute('SELECT COUNT(*) FROM submissions').fetchone()[0], 2)
        self.assertEqual(Card.from_json(before['card_json']).card_id, Card.from_json(after['card_json']).card_id)
        self.assertIsNotNone(after['first_passed_at'])
        with self.assertRaises(DomainError): self.send(run, 'passed', 2)
        self.backend.advance(self.payload())
        self.assertTrue(self.backend.check_event(run['round_id'], run['step_id'], event)['duplicate'])

    def test_round_order_mismatch_and_no_evidence_do_not_score(self):
        run = self.launch()
        with self.assertRaises(DomainError): self.send(run, 'passed', 2)
        with self.assertRaises(DomainError): self.backend.finish(self.payload())
        with self.assertRaises(DomainError): self.backend.submit(self.payload(answer='passed'))
        with self.assertRaises(DomainError): self.backend.opened(str(uuid.uuid4()), run['step_id'])
        self.send(run, 'environment', 1)
        with self.assertRaises(DomainError): self.backend.finish(self.payload())
        with closing(self.backend._connect()) as con:
            self.assertIsNone(self.backend._load(con)['points']['a']['card_json'])

    def test_manual_all_four_ratings_and_retry(self):
        self.backend = AcademyLearningService(exercise('manual'), self.root / 'manual', clock=lambda: self.when)
        for rating in (4, 2, 1, 3):
            run = self.launch('learn' if rating == 4 else 'review')
            payload = self.payload(rating=rating)
            state = self.backend.finish(payload)
            self.assertEqual(state['current']['ratings'], {'a': rating})
            self.assertEqual(self.backend.finish(payload)['revision'], state['revision'])
            self.backend.advance(self.payload())
            with closing(self.backend._connect()) as con:
                due = self.backend._load(con)['points']['a']['due_at']
            self.when = datetime.fromisoformat(due) + timedelta(seconds=1)

    def test_claim_callback_auth_logout_and_review_directory(self):
        topic = exercise()
        (self.root / 'topic.json').write_text(json.dumps(topic), encoding='utf-8')
        (self.root / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'cs-fixture', 'title': 'CS fixture',
            'topics': [{'id': topic['id'], 'path': 'topic.json'}]}]}), encoding='utf-8')
        (self.root / 'subjects.json').write_text(json.dumps({'subjects': [{'id': 'cs', 'course_ids': ['cs-fixture']}]}), encoding='utf-8')
        settings = Settings(data_dir=self.root, public_api_origin='https://cs.test')
        app = create_app(settings, atomic_clock=lambda: self.when)
        with TestClient(app, base_url='https://cs.test') as client:
            headers = {'origin': 'https://cs.test'}
            def post(path, body, h=headers):
                response = client.post('/api/' + path, json=body, headers=h)
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()
            guest = post('access/guest', {})
            headers['x-learning-identity'] = guest['identity_token']
            self.backend = app.state.learning_services[(guest['access']['learner_id'], topic['id'])] if (guest['access']['learner_id'], topic['id']) in app.state.learning_services else None
            scope = '?topic_id=academy-fixture&subject_id=cs'
            state = client.get('/api/state' + scope, headers=headers).json()
            self.backend = app.state.learning_services[(guest['access']['learner_id'], topic['id'])]
            post('choose' + scope, self.payload(choice='learn'))
            opened = post('academy/launch' + scope, self.payload())
            ticket = opened['url'].split('ticket=')[1]
            plugin_headers = {'origin': 'https://cs.test', 'authorization': 'Bearer ' + ticket}
            self.assertEqual(client.post('/api/academy/claim', json={}, headers={'origin': 'https://cs.test'}).status_code, 401)
            run = post('academy/claim', {}, plugin_headers)
            post('academy/event', {'kind': 'opened'}, plugin_headers)
            result = post('academy/event', {'kind': 'check', 'event': self.event(run, 'passed', 1)}, plugin_headers)
            self.assertEqual(result['rating'], 3)
            post('continue' + scope, self.payload())
            self.when += timedelta(days=1)
            inbox = client.get('/api/review-papers/inbox?subject_id=cs', headers=headers)
            self.assertEqual(inbox.status_code, 200, inbox.text)
            self.assertTrue(inbox.json()['legacy'][0]['academy'])
            self.assertEqual(inbox.json()['items'], [])
            post('access/logout', {})
            response = client.post('/api/academy/claim', json={}, headers=plugin_headers)
            self.assertEqual(response.status_code, 401, response.text)
