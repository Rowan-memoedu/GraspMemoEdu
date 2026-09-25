"""The paid-judge quota survives app restarts and cannot be bypassed by new guests."""
from datetime import datetime, timedelta
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from math_learning.access import AccessStore
from math_learning.cli import main
from math_learning.public_limits import DailyJudgeQuota, PublicLimitError, judge_usage_report


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

    def test_unlimited_account_still_counts_toward_global_limit_and_private_report(self):
        root = Path(self.temporary.name)
        store = AccessStore(root, [])
        account = store.create_account('Student A')['account']
        unused = store.create_account('Student B')['account']
        guest = store.create_guest()['access']
        with patch.dict('os.environ', {'MATH_JUDGE_DAILY_GLOBAL': '5',
                                    'MATH_JUDGE_DAILY_ACCOUNT': '0',
                                    'MATH_JUDGE_DAILY_GUEST': '100'}):
            quota = DailyJudgeQuota(root, now=lambda: self.now)
        self.assertIsNone(quota.account_limit)
        for _ in range(4):
            with quota.reserve(account['learner_id'], 'account'):
                pass
        with quota.reserve(guest['learner_id'], 'guest'):
            pass
        with self.assertRaises(PublicLimitError):
            with quota.reserve(account['learner_id'], 'account'):
                pass
        report = judge_usage_report(root, day='2026-09-24')
        self.assertEqual(report['global_used'], 5)
        self.assertEqual({row['learner_id']: row['used'] for row in report['identities']},
                         {account['learner_id']: 4, unused['learner_id']: 0, guest['learner_id']: 1})
        self.assertEqual(report['identities'][0]['display_name'], 'Student A')
        self.assertNotIn('invitation_code', json.dumps(report))
        self.assertEqual([row['used'] for row in judge_usage_report(root, day='2026-09-24', role='guest')['identities']], [1])
        self.assertEqual(len(judge_usage_report(root, day='2026-09-24', learner_id=unused['learner_id'])['identities']), 1)
        self.assertEqual(sum(row['used'] for row in judge_usage_report(root, day='2026-09-25')['identities']), 0)
        with patch('sys.argv', ['math_learning', '--data-dir', str(root), 'judge-usage-report',
                                '--day', '2026-09-24', '--role', 'account']):
            stream = io.StringIO()
            with redirect_stdout(stream):
                main()
        command_report = json.loads(stream.getvalue())
        self.assertEqual(command_report['global_used'], 5)
        self.assertEqual({row['learner_id'] for row in command_report['identities']},
                         {account['learner_id'], unused['learner_id']})

    def test_guest_limit_and_invalid_day(self):
        self.quota.global_limit = 200
        self.quota.guest_limit = 100
        for _ in range(100):
            with self.quota.reserve('guest-a', 'guest'):
                pass
        with self.assertRaises(PublicLimitError):
            with self.quota.reserve('guest-a', 'guest'):
                pass
        with self.assertRaises(ValueError):
            judge_usage_report(self.temporary.name, day='2026-02-30')

    def test_missing_history_is_not_reported_as_zero_usage(self):
        old_restore = Path(self.temporary.name) / 'old-restore'
        AccessStore(old_restore, []).create_guest()
        with self.assertRaises(FileNotFoundError):
            judge_usage_report(old_restore, day='2026-09-24')


if __name__ == '__main__':
    unittest.main()
