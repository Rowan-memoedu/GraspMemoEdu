from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import unittest

from graspmemoedu.atomic import AtomicLearningService
from atomic_fixtures import atomic_topic
from tests.fixtures import temporary_test_directory
import test_atomic


class ReadingPointActivationTests(unittest.TestCase):
    action = test_atomic.AtomicTests.action
    advance = test_atomic.AtomicTests.advance
    submit = test_atomic.AtomicTests.submit
    finish_initial = test_atomic.AtomicTests.finish_initial
    raw = test_atomic.AtomicTests.raw

    def setUp(self):
        temporary = temporary_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.when = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)
        self.topic = atomic_topic()
        self.service = AtomicLearningService(self.topic, self.root, clock=lambda: self.when)

    def test_selection_prioritizes_existing_learning_without_mastery_or_schedule(self):
        before = deepcopy(self.raw()['points'])
        result = self.service.request_points(['c'])
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.raw()['points'], before)
        revision = self.raw()['revision']
        self.service.request_points(['c'])
        self.assertEqual(self.raw()['revision'], revision)
        other = AtomicLearningService(self.topic, self.root, learner_id='other', clock=lambda: self.when)
        with closing(other._connect()) as con:
            self.assertNotIn('requested_points', other._load(con))
        self.service.choose(self.action(choice='learn'))
        self.advance()
        self.assertEqual(self.service.state()['current']['point_ids'], ['c'])
        self.assertEqual(self.raw()['points'], before)
        self.submit()
        self.assertTrue(self.raw()['points']['c']['mastered'])
        self.assertFalse(self.raw()['points']['a']['mastered'])
        self.assertIsNotNone(self.raw()['points']['c']['due_at'])

    def test_active_question_and_mastered_schedule_are_preserved(self):
        self.service.choose(self.action(choice='learn'))
        self.advance()
        active = deepcopy(self.raw()['active'])
        self.service.request_points(['c'])
        self.assertEqual(self.raw()['active'], active)
        self.submit()
        before = deepcopy(self.raw()['points']['a'])
        result = self.service.request_points(['a'])
        self.assertEqual(result['status'], 'learned')
        self.assertEqual(self.raw()['points']['a'], before)


if __name__ == '__main__':
    unittest.main()
