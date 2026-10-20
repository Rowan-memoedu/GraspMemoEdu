"""Eligibility and original scoring policies across all reproduction readers."""
from contextlib import closing
from datetime import timedelta
import time
import unittest
import uuid
from unittest.mock import patch
from graspmemoedu.judging import judge_answer
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.service import create_learning_service
from graspmemoedu.atomic_api import review_summary
from graspmemoedu.review_cards import eligible_presentations

import test_training_api as bank_tests
import test_review_api as review_tests
import test_review_papers as paper_tests


class BankAssessmentTests(unittest.TestCase):
    setUp = bank_tests.TrainingAPITests.setUp
    call = bank_tests.TrainingAPITests.call
    account = bank_tests.TrainingAPITests.account
    open = bank_tests.TrainingAPITests.open
    payload = paper_tests.PaperTests.payload

    def test_semantic_blank_allowed_exact_text_denied_and_standard_cannot_reveal(self):
        blank = next(q for q in self.app.state.training_catalog.banks[self.bank['id']]['groups'][1]['questions'] if q['id'] == 'blank')
        blank['interaction']['grading'] = 'semantic'
        standard = self.open('blank')
        self.call('POST', '/api/training/reveal', {'attempt_id': standard['attempt_id']}, 403)
        self.account(is_advanced_learner=True)
        state = self.open('blank'); self.assertTrue(state['can_self_rate'])
        shown = self.call('POST', '/api/training/reveal', {'attempt_id': state['attempt_id']})
        self.assertEqual(shown['reference_answer'], '1. 2')
        self.assertIsNone(shown['result'])
        rated = self.call('POST', '/api/training/rate', {'attempt_id': state['attempt_id'], 'rating': 3})
        self.assertEqual(rated['result']['rating'], 3)
        self.assertEqual(self.call('POST', '/api/training/rate', {'attempt_id': state['attempt_id'], 'rating': 3})['result'], rated['result'])
        exact = self.open('exact'); self.assertFalse(exact['can_self_rate'])
        self.call('POST', '/api/training/reveal', {'attempt_id': exact['attempt_id']}, 403)

    def test_atomic_reveal_and_grade_preserve_initial_policy_and_reject_forgery(self):
        self.account(is_advanced_learner=True)
        scope = '?topic_id=atomic-english&subject_id=english'
        for question in self.app.state.catalog.topics['atomic-english']['flashcards']:
            question['interaction']['grading'] = 'semantic'
        state = self.call('GET', '/api/state' + scope)
        state = self.call('POST', '/api/choose' + scope, self.payload(state, choice='learn'))
        while state['phase'] != 'answer':
            state = self.call('POST', '/api/continue' + scope, self.payload(state))
        self.call('POST', '/api/submit' + scope, self.payload(state, answer='自主评分：3', self_rating=3), 403)
        shown = self.call('POST', '/api/reveal' + scope, self.payload(state))
        self.assertTrue(shown['current']['revealed'])
        self.assertEqual(shown['learned_count'], 0)
        data = self.payload(shown, answer='自主评分：3', self_rating=3)
        with patch('graspmemoedu.atomic.judge_answer', side_effect=AssertionError('Self assessment must not invoke a judge')):
            result = self.call('POST', '/api/submit' + scope, data)
            for _ in range(100):
                result = self.call('GET', '/api/submissions/' + data['request_id'] + scope)
                if result['status'] == 'judged': break
                time.sleep(.01)
        self.assertEqual(result['status'], 'judged')
        self.assertTrue(result['correct'])
        self.assertEqual(list(result['state']['current']['ratings'].values()), [None])
        self.assertEqual(result['state']['learned_count'], 1)
        self.assertEqual(self.call('POST', '/api/submit' + scope, data)['state']['revision'], result['state']['revision'])

    def test_atomic_fifth_choice_stops_linked_point_variants_persistently_without_fsrs(self):
        login = self.account(is_advanced_learner=True)
        scope = '?topic_id=atomic-english&subject_id=english'
        topic = self.app.state.catalog.topics['atomic-english']
        for question in topic['flashcards']:
            question['interaction']['grading'] = 'semantic'
        state = self.call('GET', '/api/state' + scope)
        state = self.call('POST', '/api/choose' + scope, self.payload(state, choice='learn'))
        state = self.call('POST', '/api/continue' + scope, self.payload(state))
        self.assertTrue(state['current']['can_stop_reviews'])
        self.call('POST', '/api/submit' + scope, self.payload(state, answer='5', self_rating=5), 403)
        shown = self.call('POST', '/api/reveal' + scope, self.payload(state))
        data = self.payload(shown, answer='自主评分：5', self_rating=5)
        with patch('graspmemoedu.atomic.judge_answer', side_effect=AssertionError('No judge')), \
                patch('graspmemoedu.fsrs_engine.FSRSEngine.schedule', side_effect=AssertionError('No FSRS rating 5')):
            result = self.call('POST', '/api/submit' + scope, data)
            for _ in range(100):
                if result['status'] == 'judged':
                    break
                time.sleep(.01)
                result = self.call('GET', '/api/submissions/' + data['request_id'] + scope)
        self.assertEqual(result['status'], 'judged')
        self.assertTrue(result['state']['current']['stop_requested'])
        self.assertEqual(result['state']['current']['ratings'], {'a': None})
        self.assertEqual(self.call('POST', '/api/submit' + scope, data), result)
        learner = login['access']['learner_id']
        backend = self.app.state.learning_services[(learner, topic['id'])]
        with closing(backend._connect()) as con:
            saved = backend._load(con)
            self.assertTrue(saved['points']['a']['stopped_at'])
            self.assertIsNone(saved['points']['a']['due_at'])
            self.assertIsNone(saved['points']['a']['card_json'])
            self.assertFalse(saved['points']['b'].get('stopped_at'))
        archive = self.root / 'atomic-stop.zip'
        backup_site(self.settings, archive)
        destination = self.root / 'atomic-stop-restored'
        restore_site(archive, destination)
        restored = create_learning_service(topic, destination, learner_id=learner,
                                           clock=lambda: self.when + timedelta(days=365))
        with closing(restored._connect()) as con:
            state = restored._load(con)
        self.assertEqual(state['points'], saved['points'])
        self.assertEqual(review_summary(topic, state, self.when + timedelta(days=365))['due_count'], 0)
        for mode in ('learn', 'review'):
            state['mode'] = mode
            available, _ = restored._eligible(state)
            self.assertNotIn('a', available)
            self.assertTrue(all('a' not in card['point_ids'] for card in
                                eligible_presentations(topic['flashcards'], available, state['card_uses'])))

    def test_standard_account_cannot_forge_atomic_fifth_choice(self):
        self.account()
        scope = '?topic_id=atomic-english&subject_id=english'
        for question in self.app.state.catalog.topics['atomic-english']['flashcards']:
            question['interaction']['grading'] = 'semantic'
        state = self.call('GET', '/api/state' + scope)
        state = self.call('POST', '/api/choose' + scope, self.payload(state, choice='learn'))
        state = self.call('POST', '/api/continue' + scope, self.payload(state))
        self.assertFalse(state['current']['can_stop_reviews'])
        self.call('POST', '/api/submit' + scope, self.payload(state, answer='5', self_rating=5), 403)


class CourseAssessmentTests(unittest.TestCase):
    setUp = review_tests.ReviewAPITests.setUp
    close = review_tests.ReviewAPITests.close
    call = review_tests.ReviewAPITests.call
    enroll = review_tests.ReviewAPITests.enroll
    payload = review_tests.ReviewAPITests.payload

    def advanced(self):
        created = self.app.state.access_store.create_account('self-assessment fixture', is_advanced_learner=True)
        login = self.call('POST', '/api/access/login', {'invitation_code': created['invitation_code']})
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.call('GET', '/api/state')
        self.learner = self.app.state.learning_services[login['access']['learner_id']]
        self.backend = self.learner.review_backend; self.backend.clock = lambda: self.time

    def test_lesson_retains_module_policy_and_reveal_is_not_completion(self):
        self.advanced(); state = self.call('GET', '/api/state')
        def data(state, **extra):
            return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                    'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}
        state = self.call('POST', '/api/choose', data(state, choice='mastered'))
        shown = self.call('POST', '/api/reveal', data(state))
        self.assertEqual(shown['modules'][0]['status'], 'in_progress')
        self.assertNotIn('reference_answer', next(s for s in shown['steps'] if s['active']))
        request = data(shown, answer='自主评分：2', self_rating=2)
        with patch('graspmemoedu.service.judge_answer', side_effect=AssertionError('No judge')):
            result = self.call('POST', '/api/submit', request)
            for _ in range(100):
                result = self.call('GET', '/api/submissions/' + request['request_id'])
                if result['status'] == 'judged': break
                time.sleep(.01)
        self.assertTrue(result['correct'])
        self.assertEqual(result['state']['modules'][0]['mastery'], 4)

    def test_review_wrong_then_next_requires_fresh_reveal_and_preserves_practice_rule(self):
        self.advanced(); state = self.enroll()
        base = '/api/review/sessions/' + state['session_id']
        shown = self.call('POST', base + '/reveal', self.payload(state))
        request = self.payload(shown, answer='自主评分：1', self_rating=1)
        result = self.call('POST', base + '/submit', request)
        for _ in range(100):
            result = self.call('GET', '/api/review/submissions/' + request['request_id'])
            if result['status'] == 'judged': break
            time.sleep(.01)
        self.assertFalse(result['correct'])
        self.assertEqual(result['state']['practice_error_count'], 1)
        next_state = self.call('POST', base + '/continue', self.payload(result['state']))
        self.assertFalse(next_state['practice']['revealed'])
        self.assertIsNone(next_state['practice']['explanation_html'])
        self.call('POST', base + '/submit', self.payload(next_state, answer='自主评分：3', self_rating=3), 403)


class PaperAssessmentTests(unittest.TestCase):
    setUp = paper_tests.PaperTests.setUp
    call = paper_tests.PaperTests.call
    payload = paper_tests.PaperTests.payload
    account = paper_tests.PaperTests.account
    service = paper_tests.PaperTests.service
    prime = paper_tests.PaperTests.prime
    inbox = paper_tests.PaperTests.inbox
    open_paper = paper_tests.PaperTests.open_paper
    draft = paper_tests.PaperTests.draft
    submit_paper = paper_tests.PaperTests.submit_paper
    memories = paper_tests.PaperTests.memories

    def test_atomic_review_fifth_choice_stops_all_points_in_linked_card(self):
        self.account(is_advanced_learner=True)
        self.learner_id = self.service().learner_id
        self.scope = '?topic_id=atomic-english&subject_id=english'
        topic = self.app.state.catalog.topics['atomic-english']
        for question in topic['flashcards']:
            question['interaction']['grading'] = 'semantic'
        _, backend = paper_tests.PaperTests.finish(self)
        self.when += timedelta(hours=7)
        inbox = self.call('GET', '/api/review-papers/inbox?subject_id=english')
        candidate = next(item for item in inbox['items'] if item['kind'] == 'single' and item['point_count'] == 2)
        paper = self.call('POST', '/api/review-papers/open?subject_id=english',
                          {'candidate_id': candidate['id'], 'request_id': str(uuid.uuid4())})
        self.assertTrue(paper['items'][0]['can_stop_reviews'])
        paper = self.call('POST', '/api/review-papers/reveal', {
            'paper_id': paper['id'], 'index': 0, 'expected_revision': paper['revision']})
        paper = self.call('POST', '/api/review-papers/draft', {
            'paper_id': paper['id'], 'expected_revision': paper['revision'],
            'answers': [{'index': 0, 'answer': '自主评分：5', 'self_rating': 5}]})
        with closing(backend._connect()) as con:
            self.assertFalse(backend._load(con)['points']['a'].get('stopped_at'))
        with patch('graspmemoedu.fsrs_engine.FSRSEngine.schedule', side_effect=AssertionError('No FSRS rating 5')):
            result, _ = self.submit_paper(paper)
        self.assertTrue(result['items'][0]['result']['stop_requested'])
        self.assertFalse(result['items'][0]['result']['scheduled'])
        with closing(backend._connect()) as con:
            points = backend._load(con)['points']
        self.assertTrue(points['a']['stopped_at'])
        self.assertTrue(points['b']['stopped_at'])
        self.assertFalse(points['c'].get('stopped_at'))
        self.when += timedelta(days=365)
        inbox = self.call('GET', '/api/review-papers/inbox?subject_id=english')
        self.assertEqual(inbox['due_count'], 1)

    def test_whole_paper_assessment_stays_draft_until_submit_and_exact_denied(self):
        self.account(is_advanced_learner=True); self.prime()
        blank = next(q for q in self.app.state.training_catalog.banks[self.bank['id']]['groups'][1]['questions'] if q['id'] == 'blank')
        blank['interaction']['grading'] = 'semantic'
        paper = self.open_paper(); before = self.memories()
        index = next(i['index'] for i in paper['items'] if i['interaction']['type'] == 'fill_blank')
        paper = self.call('POST', '/api/review-papers/reveal', {'paper_id': paper['id'], 'index': index, 'expected_revision': paper['revision']})
        self.assertEqual(paper['items'][index]['reference_answer'], '1. 2')
        self.assertEqual(self.memories(), before)
        paper = self.call('POST', '/api/review-papers/draft', {'paper_id': paper['id'], 'expected_revision': paper['revision'],
            'answers': [{'index': index, 'answer': '自主评分：3', 'self_rating': 3}]})
        self.assertIsNone(paper['items'][index]['result']); self.assertEqual(self.memories(), before)
        paper = self.draft(paper, 1 - index)
        with patch('graspmemoedu.review_papers.judge_answer', wraps=judge_answer) as judge:
            result, request = self.submit_paper(paper)
            self.assertEqual(judge.call_count, 1)
        self.assertTrue(result['items'][index]['result']['correct'])
        self.assertTrue(result['items'][index]['result']['reason'].startswith('自主评分'))
        after = self.memories()
        self.call('POST', '/api/review-papers/submit', request)
        self.assertEqual(self.memories(), after)
