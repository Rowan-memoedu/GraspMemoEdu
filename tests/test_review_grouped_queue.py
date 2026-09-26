"""Cross-round practice delivery using isolated learner data and an injected clock."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import random
import unittest
import uuid

from graspmemoedu.fsrs_engine import utc_time
from graspmemoedu.review import ReviewBackend
from graspmemoedu.service import LearningService, encode
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class GroupedReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.course = synthetic_course()
        self.time = datetime.now(timezone.utc)
        self.restart()
        self.learning.practice_queue.rng = random.Random(902)
        self.backend.record_module_result(module_id='module-1', mastery=4, result_id='enroll', completed_at=self.time)

    def restart(self):
        self.learning = LearningService(self.course, self.path, DeterministicJudge())
        self.backend = ReviewBackend(self.learning, clock=lambda: self.time)

    def begin(self):
        self.time = utc_time(self.backend.next_due_at())
        return self.backend.begin('module-1', request_id=str(uuid.uuid4()))

    def payload(self, state, **kwargs):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'question_id': state['practice']['id'], **kwargs}

    def answer(self, state, answer='correct'):
        result = self.backend.submit(state['session_id'], self.payload(state, answer=answer))
        self.backend.process_submission(result['submission_id'])
        return self.backend.submission(result['submission_id'])['state']

    def advance(self, state):
        return self.backend.advance(state['session_id'], self.payload(state))

    def stored(self, state):
        with closing(self.learning._connect()) as con:
            return self.backend._session(con, state['session_id'])

    def queue(self):
        with closing(self.learning._connect()) as con:
            return self.learning.practice_queue.snapshot(con, self.course['modules'][0])

    @staticmethod
    def group(question_id):
        return (int(question_id.rsplit('-', 1)[1]) - 1) // 3

    def test_first_five_rounds_use_one_from_each_group_and_only_draw_current(self):
        questions = []
        for _ in range(5):
            state = self.begin()
            questions.append(state['practice']['id'])
            stored = self.stored(state)
            self.assertEqual(stored['practice_queue_version'], 'grouped-v1')
            self.assertEqual(stored['order'], [state['practice']['id']])
            state = self.answer(state)
            self.assertEqual(state['mastery'], 4)
            self.restart()
        self.assertEqual(len({self.group(q) for q in questions}), 5)

    def test_lesson_and_review_consume_one_shared_remaining_queue(self):
        def lesson_payload(**extra):
            state = self.learning.state()
            return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                    'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

        self.learning.choose(lesson_payload(choice='unmastered'))
        for _ in range(3):
            self.learning.advance(lesson_payload())
        for index in range(2):
            if index:
                self.learning.advance(lesson_payload())
            item = self.learning.submit(lesson_payload(answer='correct'))
            self.learning.process_submission(item['submission_id'])
        before = self.queue()
        self.assertEqual(before['sequence'], 2)
        self.assertEqual(len(before['pending']), 3)
        self.restart()
        state = self.begin()
        self.assertEqual(state['practice']['id'], before['pending'][0])
        self.assertEqual(self.queue()['sequence'], 3)
        self.assertEqual(self.queue()['pending'], before['pending'][1:])

    def test_wrong_group_siblings_are_appended_after_initial_five(self):
        state = self.begin()
        questions = [state['practice']['id']]
        state = self.answer(state, 'wrong')
        for _ in range(2):
            state = self.advance(state)
            questions.append(state['practice']['id'])
            state = self.answer(state)
        self.assertEqual(state['mastery'], 2)
        for _ in range(4):
            state = self.begin()
            questions.append(state['practice']['id'])
            self.answer(state)
        self.assertEqual(len({self.group(q) for q in questions[:5]}), 5)
        self.assertTrue(all(self.group(q) == self.group(questions[0]) for q in questions[5:]))
        self.assertEqual(len(set(questions)), 7)

    def test_all_unseen_are_delivered_before_oldest_practices_repeat(self):
        questions = []
        for _ in range(18):
            state = self.begin()
            questions.append(state['practice']['id'])
            self.answer(state)
        self.assertEqual(len(set(questions[:15])), 15)
        self.assertEqual(questions[15:], questions[:3])

    def test_refresh_replay_and_judge_error_do_not_draw_or_expand(self):
        state = self.begin()
        sid = state['session_id']
        state = self.answer(state, 'service-error')
        self.assertEqual(state['practice_answered_count'], 0)
        self.assertEqual(self.stored(state)['order'], [state['practice']['id']])
        self.assertEqual(self.queue()['expanded_groups'], [])
        self.restart()
        self.assertEqual(self.backend.state(sid), state)
        payload = self.payload(state, answer='wrong')
        result = self.backend.submit(sid, payload)
        self.backend.process_submission(result['submission_id'])
        state = self.backend.submission(result['submission_id'])['state']
        self.backend.process_submission(result['submission_id'])
        self.assertEqual(self.backend.submit(sid, payload)['state'], state)
        advance = self.payload(state)
        next_state = self.backend.advance(sid, advance)
        self.assertEqual(self.backend.advance(sid, advance), next_state)
        self.assertEqual(len(self.stored(next_state)['order']), 2)
        self.assertEqual(next_state['practice_error_count'], 1)

    def test_third_wrong_expands_before_pause_and_unlock_keeps_remaining_queue(self):
        state = self.begin()
        for index in range(3):
            if index:
                state = self.advance(state)
            state = self.answer(state, 'wrong')
        self.assertEqual(state['status'], 'paused')
        self.assertIsNone(state['mastery'])
        pending = self.queue()
        self.assertEqual(len(pending['expanded_groups']), 3)
        self.assertEqual(len(pending['pending']), 8)
        self.backend.unlock_topic(pause_event_id=state['pause_event_id'])
        self.restart()
        self.assertEqual(self.queue(), pending)
        state = self.begin()
        self.assertEqual(state['practice']['id'], pending['pending'][0])

    def test_second_error_in_same_group_does_not_expand_again(self):
        state = self.begin()
        state = self.answer(state, 'wrong')
        state = self.answer(self.advance(state))
        self.answer(self.advance(state))
        for _ in range(2):
            self.answer(self.begin())
        state = self.begin()
        before = self.queue()
        self.assertIn(self.group(state['practice']['id']) + 1, before['expanded_groups'])
        state = self.answer(state, 'wrong')
        self.assertEqual(self.queue(), before)
        self.assertEqual(state['practice_target_count'], 3)

    def test_late_judgement_after_pause_cannot_expand_queue(self):
        state = self.begin()
        item = self.backend.submit(state['session_id'], self.payload(state, answer='wrong'))
        before = self.queue()
        self.backend.pause(state['session_id'], request_id='pause-before-worker')
        self.backend.process_submission(item['submission_id'])
        self.assertEqual(self.backend.submission(item['submission_id'])['status'], 'discarded')
        self.assertEqual(self.queue(), before)

    def test_existing_round_without_queue_marker_keeps_its_frozen_order(self):
        state = self.begin()
        first = state['practice']['id']
        legacy_order = [first] + [q['id'] for q in self.course['modules'][0]['practices'] if q['id'] != first][:4]
        with self.learning._transaction() as con:
            stored = self.backend._session(con, state['session_id'])
            stored.pop('practice_queue_version')
            stored['order'] = legacy_order
            con.execute('UPDATE review_sessions SET body=? WHERE id=?', (encode(stored), state['session_id']))
        self.restart()
        state = self.answer(self.backend.state(state['session_id']), 'wrong')
        state = self.advance(state)
        self.assertEqual(state['practice']['id'], legacy_order[1])
        self.assertEqual(self.stored(state)['order'], legacy_order)


if __name__ == '__main__':
    unittest.main()
