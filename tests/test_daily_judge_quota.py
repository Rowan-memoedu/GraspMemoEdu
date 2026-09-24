"""The paid-judge quota survives app restarts and cannot be bypassed by new guests."""
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from math_learning.public_limits import DailyJudgeQuota, PublicLimitError


class DailyJudgeQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.now = datetime(2026, 9, 24, 23, 59, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.quota = DailyJudgeQuota(Path(self.temporary.name), now=lambda: self.now)
        self.quota.global_limit = 3
        self.quota.guest_limit = 2
        self.quota.account_limit = 2

    def test_global_limit_persists_across_guests_and_restart_then_resets_at_china_midnight(self):
        with self.quota.reserve('guest-a', 'guest'):
            pass
        with self.quota.reserve('guest-b', 'guest'):
            pass
        restarted = DailyJudgeQuota(Path(self.temporary.name), now=lambda: self.now)
        restarted.global_limit = 3
        restarted.guest_limit = 2
        with restarted.reserve('guest-c', 'guest'):
            pass
        with self.assertRaises(PublicLimitError) as error:
            with restarted.reserve('guest-d', 'guest'):
                pass
        self.assertEqual(error.exception.code, 'daily_judge_quota')
        self.assertEqual(error.exception.retry_after, 60)
        self.now += timedelta(minutes=1)
        with restarted.reserve('guest-d', 'guest'):
            pass

    def test_individual_limit_rejected_submission_does_not_spend_quota(self):
        with self.assertRaises(ValueError):
            with self.quota.reserve('student-a', 'account'):
                raise ValueError('invalid answer')
        for _ in range(2):
            with self.quota.reserve('student-a', 'account'):
                pass
        with self.assertRaises(PublicLimitError):
            with self.quota.reserve('student-a', 'account'):
                pass
        with self.quota.reserve('student-b', 'account'):
            pass

    def test_invalid_or_zero_configuration_fails_closed(self):
        with patch.dict('os.environ', {'MATH_JUDGE_DAILY_GLOBAL': '0'}):
            with self.assertRaises(ValueError):
                DailyJudgeQuota(Path(self.temporary.name))


if __name__ == '__main__':
    unittest.main()
