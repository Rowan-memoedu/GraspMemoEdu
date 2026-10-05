from datetime import datetime, timedelta, timezone
import unittest

from graspmemoedu.content_timing import limit_state, start_limit


class ContentTimingTests(unittest.TestCase):
    def test_disabled_and_frozen_wall_clock_deadline(self):
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.assertIsNone(start_limit(None, now))
        limit = start_limit(12, now)
        self.assertEqual(limit_state(limit, now + timedelta(minutes=5))['remaining_seconds'], 420)
        self.assertTrue(limit_state(limit, now + timedelta(minutes=12))['timed_out'])
        finished = (now + timedelta(minutes=3)).isoformat()
        result = limit_state(limit, now + timedelta(days=1), finished_at=finished)
        self.assertEqual(result['remaining_seconds'], 540)
        self.assertFalse(result['timed_out'])

    def test_mark_requires_current_qualification(self):
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        limit = start_limit(1, now, 'mark')
        self.assertEqual(limit_state(limit, now, can_mark=True)['timeout_action'], 'mark')
        self.assertEqual(limit_state(limit, now)['timeout_action'], 'submit')
        for invalid in (0, -1, True, '5', 1.5, 1441):
            with self.assertRaises(ValueError):
                start_limit(invalid, now)
