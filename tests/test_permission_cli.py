import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from math_learning import cli
from math_learning.config import Settings
from tests.fixtures import synthetic_course, temporary_test_directory


class PermissionCliTests(unittest.TestCase):
    def setUp(self):
        temporary = temporary_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        path = self.root / 'courses/digits-to-letters.json'
        path.parent.mkdir()
        path.write_text(json.dumps(synthetic_course(1)), encoding='utf-8')
        self.store = cli.make_access_store(Settings(data_dir=self.root))
        self.account = self.store.create_account('Fixture', topic_ids=[], features=[])['account']

    def command(self, *args):
        output = io.StringIO()
        with patch('sys.argv', ['math_learning', '--data-dir', str(self.root), 'account-permissions',
                                '--learner', self.account['learner_id'], *args]), contextlib.redirect_stdout(output):
            cli.main()
        return json.loads(output.getvalue())

    def test_reset_flags_restore_dynamic_defaults(self):
        result = self.command('--default-topics', '--default-features')
        self.assertEqual(result['permission_defaults'], {'topics': True, 'features': True})
        self.assertTrue(result['topics'])
        self.assertTrue(result['features'])
        result = self.command('--features', '')
        self.assertEqual(result['features'], [])
        self.assertEqual(result['permission_defaults'], {'topics': True, 'features': False})

    def test_conflicting_flags_leave_account_unchanged(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.command('--topics', '', '--default-topics')
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.store.list_principals(accounts_only=True), [self.account])
