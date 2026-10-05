"""Test-purpose role creation must preserve private exports and safe retries."""
import json
from pathlib import Path
import unittest

from graspmemoedu.access import AccessStore
from graspmemoedu.account_admin import AccountAdmin
from graspmemoedu.service import DomainError
from tests.fixtures import temporary_test_directory


class TestAccountRoles(unittest.TestCase):
    def test_admin_and_advanced_student_are_test_accounts_with_idempotent_exports(self):
        with temporary_test_directory() as directory:
            root = Path(directory)
            store = AccessStore(root, [])
            admin = AccountAdmin(store)
            for role, flags in [('admin', {'is_admin': True}),
                                ('student', {'is_advanced_learner': True, 'is_student': True})]:
                output = root / 'admin' / (role + '.json')
                first = admin.create(role, purpose='test', output=output, **flags)
                original = output.read_bytes()
                retry = admin.create(role, purpose='test', output=output, **flags)
                self.assertFalse(first['already_exists'])
                self.assertTrue(retry['already_exists'])
                self.assertEqual(first['tests'], 1)
                self.assertNotIn('MLW-', json.dumps(first))
                self.assertEqual(original, output.read_bytes())
                account = json.loads(original)['accounts'][0]
                identity = store.login(account['invitation_code'])['access']
                self.assertEqual(identity['purpose'], 'test')
                for key, value in flags.items():
                    self.assertEqual(identity[key], value)
                with self.assertRaises(DomainError):
                    admin.create(role, purpose='learner', output=output, **flags)
                if role == 'admin':
                    self.assertEqual(identity['learning_policy']['persistence'], 'memory')
                    with self.assertRaises(DomainError):
                        admin.create(role, purpose='test', output=output)


if __name__ == '__main__':
    unittest.main()
