"""Public API contracts for a synthetic Topic containing both training kinds."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import closing
import json
import time
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import create_learning_service
from graspmemoedu.question_types import demo_answer
from tests.fixtures import temporary_test_directory, DeterministicJudge
from tests.test_unified_learning import composed_topic
from tests.test_ordered_learning import active


class UnifiedAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.topic = composed_topic()
        (self.root / 'topic.json').write_text(json.dumps(self.topic), encoding='utf-8')
        (self.root / 'catalog.json').write_text(json.dumps({'courses': [{
            'id': 'composed-course', 'title': 'Synthetic composed course',
            'topics': [{'id': self.topic['id'], 'path': 'topic.json'}]}]}), encoding='utf-8')
        self.when = datetime.now(timezone.utc)
        self.settings = Settings(data_dir=self.root, public_api_origin='https://unified.test',
                                 allowed_origins=('https://site.test',))
        service = create_learning_service(self.topic, self.root, DeterministicJudge(), clock=lambda: self.when)
        self.app = create_app(self.settings, service, atomic_clock=lambda: self.when)
        self.client = TestClient(self.app, base_url='https://unified.test')
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        self.headers = {'Origin': 'https://site.test'}
        guest = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = guest['identity_token']
        self.learner_id = guest['access']['learner_id']
        self.scope = '?topic_id=' + self.topic['id'] + '&subject_id=math'

    def call(self, method, path, body=None, status=200):
        response = self.client.request(method, path, headers=self.headers,
                                       **({'json': body} if body is not None else {}))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def state(self):
        return self.call('GET', '/api/state' + self.scope)

    def action(self, path, **values):
        state = self.state()
        return self.call('POST', '/api/' + path + self.scope, {
            'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
            'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **values})

    def answer(self, answer=None):
        state = self.state()
        if answer is None:
            if state.get('training_mode') == 'atomic_retrieval':
                card = next(card for card in self.topic['flashcards'] if card['id'] == state['current']['card_id'])
                answer = demo_answer(card)
            else:
                answer = 'correct'
        result = self.action('submit', answer=answer)
        deadline = time.monotonic() + 8
        while result['status'] in ('pending', 'running') and time.monotonic() < deadline:
            time.sleep(.01)
            result = self.call('GET', '/api/submissions/' + result['submission_id'] + self.scope)
        self.assertEqual(result['status'], 'judged', result)
        return result['state']

    def first_section(self):
        state = self.action('continue')
        for _ in range(20):
            if state.get('training_mode') != 'atomic_retrieval':
                return state
            if state['phase'] == 'answer':
                state = self.answer()
            else:
                if state['phase'] == 'waiting':
                    self.when = datetime.fromisoformat(state['next_available_at'])
                elif state['phase'] == 'feedback':
                    self.when += timedelta(seconds=180)
                state = self.action('continue')
        self.fail('Atomic initial section did not end')

    def test_public_projection_history_dashboard_and_guest_reset(self):
        state = self.first_section()
        history = [s for s in state['steps'] if s['kind'] == 'atomic_card']
        self.assertEqual(len(history), 2)
        selected = self.action('select', step_id=history[0]['id'])
        self.assertEqual(selected['current_step_id'], history[0]['id'])
        board = self.call('GET', '/api/dashboard?subject_id=math&course_id=composed-course')
        self.assertEqual(board['course']['progress'], 40)
        self.action('select', step_id=state['active_step_id'])
        self.action('choose', choice='mastered')
        self.answer()
        second = self.action('continue')
        self.assertEqual(active(second)['introduction_id'], 'intro-b')
        self.action('continue')
        self.action('continue')
        reset = self.action('guest/reset')
        self.assertEqual(active(reset)['introduction_id'], 'intro-a')
        self.assertEqual(reset['atomic_progress']['completed'], 0)

    def test_paradigm_pause_does_not_block_point_review_paper(self):
        self.first_section()
        self.action('choose', choice='unmastered')
        self.action('continue')
        self.action('continue')
        for _ in range(4):
            state = self.answer('incorrect')
            if state['status'] != 'paused':
                self.action('continue')
        self.assertEqual(state['status'], 'paused')
        self.when += timedelta(hours=7)
        inbox = self.call('GET', '/api/review-papers/inbox?subject_id=math')
        self.assertEqual(inbox['due_count'], 2)
        candidate = next(item for item in inbox['items'] if item['kind'] == 'single')
        paper = self.call('POST', '/api/review-papers/open?subject_id=math',
                          {'candidate_id': candidate['id'], 'request_id': str(uuid.uuid4())})
        self.assertEqual(paper['phase'], 'answer')
        self.assertEqual(self.state()['status'], 'paused')
        card_id = paper['items'][0]['content_context']['question_id']
        answer = demo_answer(next(card for card in self.topic['flashcards'] if card['id'] == card_id))
        paper = self.call('POST', '/api/review-papers/draft', {
            'paper_id': paper['id'], 'expected_revision': paper['revision'],
            'answers': [{'index': 0, 'answer': answer}]})
        paper = self.call('POST', '/api/review-papers/submit', {
            'paper_id': paper['id'], 'expected_revision': paper['revision'], 'request_id': str(uuid.uuid4())})
        deadline = time.monotonic() + 8
        while paper.get('job_status') in ('pending', 'running') and time.monotonic() < deadline:
            time.sleep(.01)
            paper = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
        self.assertEqual(paper['phase'], 'done')
        self.assertEqual(self.state()['status'], 'paused')
        backend = self.app.state.learning_services[(self.learner_id, self.topic['id'])]
        with closing(backend._connect()) as con:
            points = backend._load(con)['atomic']
            self.assertIsNone(points['pause'])
            self.assertEqual(points['points']['a']['last_rating'], 3)

    def test_personal_report_counts_both_components_without_mutating_progress(self):
        store = self.app.state.access_store
        with store._transaction() as con:
            student = store._insert_account(con, 'Synthetic mixed learner', is_student=True)
        login = store.login(student['invitation_code'])
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.learner_id = student['account']['learner_id']
        self.first_section()
        backend = self.app.state.learning_services[(self.learner_id, self.topic['id'])]
        with closing(backend._connect()) as con:
            before = backend._load(con)
        report = self.call('GET', '/api/student-report')
        progress = report['analytics']['topics'][0]
        self.assertEqual((progress['done'], progress['total']), (2, 5))
        self.assertEqual([point['mastered'] for point in progress['points']], [True, True, False])
        self.assertEqual(len(progress['modules']), 2)
        self.assertEqual(report['analytics']['lifetime']['attempts'], 2)
        with closing(backend._connect()) as con:
            self.assertEqual(backend._load(con), before)

    def test_review_paper_reservation_is_respected_by_inline_cards(self):
        self.first_section()
        self.action('choose', choice='unmastered')
        self.action('continue')
        self.when += timedelta(hours=7)
        inbox = self.call('GET', '/api/review-papers/inbox?subject_id=math')
        candidate = next(item for item in inbox['items'] if item['kind'] == 'single')
        paper = self.call('POST', '/api/review-papers/open?subject_id=math',
                          {'candidate_id': candidate['id'], 'request_id': str(uuid.uuid4())})
        card_ids = {item['content_context']['question_id'] for item in paper['items']}
        reserved = {point for card in self.topic['flashcards'] if card['id'] in card_ids
                    for point in card['point_ids']}
        state = self.action('continue')
        if state.get('training_mode') == 'atomic_retrieval':
            self.assertFalse(reserved.intersection(state['current']['point_ids']))
        else:
            self.assertEqual(active(state)['question_id'], 'module-1-practice-1')

    def test_advanced_fifth_choice_uses_nested_point_state_and_preserves_section_flow(self):
        store = self.app.state.access_store
        created = store.create_account('Synthetic advanced learner', is_advanced_learner=True)
        login = store.login(created['invitation_code'])
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.learner_id = login['access']['learner_id']
        for card in self.app.state.catalog.topics[self.topic['id']]['flashcards']:
            card['interaction']['grading'] = 'semantic'
        self.action('continue')
        self.action('continue')
        self.assertTrue(self.state()['current']['can_stop_reviews'])
        self.action('reveal')
        result = self.action('submit', answer='自主评分：5', self_rating=5)
        for _ in range(100):
            if result['status'] == 'judged':
                break
            time.sleep(.01)
            result = self.call('GET', '/api/submissions/' + result['submission_id'] + self.scope)
        self.assertTrue(result['state']['current']['stop_requested'])
        self.assertEqual(active(self.action('continue'))['question_id'], 'module-1-example')
        backend = self.app.state.learning_services[(self.learner_id, self.topic['id'])]
        with closing(backend._connect()) as con:
            saved = backend._load(con)
        self.assertTrue(saved['atomic']['points']['a']['stopped_at'])
        self.assertEqual(saved['atomic']['points']['a']['due_at'], None)


if __name__ == '__main__':
    unittest.main()
