"""Private local account administration in isolated databases only.

Never print or include generated invitation values in assertion diagnostics.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from math_learning.access import AccessStore, FEATURES
from math_learning.account_admin import AccountAdmin, _private_permissions
from math_learning import cli
from math_learning.service import DomainError
from tests.fixtures import synthetic_course, temporary_test_directory


class CommitFailure(sqlite3.Connection):
    def commit(self):
        raise sqlite3.OperationalError('Synthetic database commit failure')


class AccountAdminAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = temporary_test_directory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = AccessStore(self.root, [{'id': 'synthetic-course', 'title': 'Synthetic course'}])
        self.admin = AccountAdmin(self.store)
        self.output = self.root / 'admin' / 'initial.json'

    def rows(self, query, params=()):
        with closing(sqlite3.connect(self.store.db_path)) as con:
            return con.execute(query, params).fetchall()

    def manifest(self, path=None):
        return json.loads((path or self.output).read_text(encoding='utf-8'))

    def provision(self, **options):
        return self.admin.provision('initial-20260920', output=self.output, **options)

    def assert_safe_receipt(self, receipt):
        text = json.dumps(receipt)
        self.assertTrue('MLW-' not in text, 'Receipt exposed an invitation')
        self.assertTrue('invitation_code' not in text, 'Receipt exposed an invitation field')

    def assert_private(self, path):
        if os.name != 'nt':
            expected = 0o700 if path.is_dir() else 0o600
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected)
            return
        script = r'''
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$acl = Get-Acl -LiteralPath $p.path
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
@{protected=$acl.AreAccessRulesProtected; user=$sid; rules=@($acl.Access | ForEach-Object { @{sid=$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value; access=[string]$_.AccessControlType; rights=[string]$_.FileSystemRights} })} | ConvertTo-Json -Depth 5 -Compress
'''
        result = subprocess.run(['pwsh', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                                input=json.dumps({'path': str(path)}), capture_output=True, text=True,
                                encoding='utf-8', timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, 'Could not inspect private ACL')
        acl = json.loads(result.stdout)
        self.assertTrue(acl['protected'])
        self.assertEqual({rule['sid'] for rule in acl['rules']}, {acl['user'], 'S-1-5-18'})
        self.assertTrue(all(rule['access'] == 'Allow' and rule['rights'] == 'FullControl' for rule in acl['rules']))

    def test_initial_batch_is_ten_learners_five_tests_and_private(self):
        receipt = self.provision()
        self.assert_safe_receipt(receipt)
        self.assertEqual((receipt['account_count'], receipt['learners'], receipt['tests']), (15, 10, 5))
        self.assertFalse(receipt['already_exists'])
        manifest = self.manifest()
        self.assertEqual(manifest['batch_id'], 'initial-20260920')
        self.assertEqual(manifest['database'], str(self.store.db_path.resolve()))
        self.assertEqual(len(manifest['accounts']), 15)
        self.assertEqual(len({a['learner_id'] for a in manifest['accounts']}), 15)
        self.assertTrue(len({a['invitation_code'] for a in manifest['accounts']}) == 15, 'Invitations were not unique')
        self.assertTrue(all(re.fullmatch(r'MLW-[A-Za-z0-9_-]{32}', a['invitation_code']) for a in manifest['accounts']), 'Malformed invitation')
        self.assertTrue(all(a['topic_ids'] == ['synthetic-course'] and a['features'] == list(FEATURES) for a in manifest['accounts']))
        self.assertEqual(self.rows('SELECT purpose, COUNT(*) FROM access_principals GROUP BY purpose'), [('learner', 10), ('test', 5)])
        self.assert_private(self.output.parent)
        self.assert_private(self.output)
        self.assertEqual(self.rows('SELECT manifest_sha256 FROM account_admin_operations'),
                         [(hashlib.sha256(self.output.read_bytes()).hexdigest(),)])
        with closing(sqlite3.connect(self.store.db_path)) as con:
            dump = '\n'.join(con.iterdump())
        self.assertTrue(all(a['invitation_code'] not in dump for a in manifest['accounts']), 'Database contained a raw invitation')
        self.assertFalse((self.root / 'logs').exists())
        self.assertEqual(self.rows('SELECT DISTINCT topics_default, features_default FROM access_principals'), [(1, 1)])

    def test_default_create_follows_new_topics_but_explicit_all_is_distinct_request(self):
        self.admin.create('Dynamic', output=self.output)
        account = self.manifest()['accounts'][0]
        self.assertTrue(account['topics_default'] and account['features_default'])
        with self.assertRaises(DomainError) as caught:
            self.admin.create('Dynamic', ['synthetic-course'], list(FEATURES), output=self.output)
        self.assertEqual(caught.exception.code, 'account_batch_conflict')
        expanded = AccessStore(self.root, [{'id': 'synthetic-course', 'title': 'First'}, {'id': 'new-topic', 'title': 'New'}])
        expanded_admin = AccountAdmin(expanded)
        self.assertTrue(expanded_admin.create('Dynamic', output=self.output)['already_exists'])
        access = expanded.login(account['invitation_code'])['access']
        self.assertEqual({topic['id'] for topic in access['topics']}, {'synthetic-course', 'new-topic'})
        rotation = self.root / 'admin/rotate-default.json'
        expanded_admin.rotate(account['learner_id'], output=rotation)
        rotated = self.manifest(rotation)['accounts'][0]
        self.assertTrue(rotated['topics_default'] and rotated['features_default'])
        self.assertEqual(self.rows('SELECT topics_default, features_default FROM access_principals'), [(1, 1)])

    def test_explicit_create_empty_permissions_and_rotation_preserve_overrides(self):
        self.admin.create('Restricted', [], [], output=self.output)
        account = self.manifest()['accounts'][0]
        self.assertFalse(account['topics_default'] or account['features_default'])
        rotation = self.root / 'admin/rotate-explicit.json'
        self.admin.rotate(account['learner_id'], output=rotation)
        self.assertEqual(self.rows('SELECT topic_ids, features, topics_default, features_default FROM access_principals'),
                         [('[]', '[]', 0, 0)])

    def test_legacy_pending_manifest_retries_without_granting_dynamic_permissions(self):
        with patch.object(self.store, '_insert_account', side_effect=sqlite3.OperationalError('Interrupted')):
            with self.assertRaises(DomainError):
                self.provision(learners=1, tests=0)
        document = self.manifest()
        for record in [document['request'], *document['accounts']]:
            record.pop('topics_default')
            record.pop('features_default')
        self.output.write_text(json.dumps(document), encoding='utf-8')
        self.assertFalse(self.provision(learners=1, tests=0)['already_exists'])
        self.assertEqual(self.rows('SELECT topics_default, features_default FROM access_principals'), [(0, 0)])
        self.assertTrue(self.provision(learners=1, tests=0)['already_exists'])

    def test_same_batch_and_concurrent_requests_create_exactly_one_set(self):
        barrier = threading.Barrier(4)
        def execute(_):
            barrier.wait(timeout=10)
            return self.provision(learners=2, tests=1)
        with ThreadPoolExecutor(max_workers=4) as workers:
            receipts = list(workers.map(execute, range(4)))
        self.assertEqual(sum(not receipt['already_exists'] for receipt in receipts), 1)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(3,)])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM account_admin_operations'), [(1,)])
        for receipt in receipts:
            self.assert_safe_receipt(receipt)
        original = hashlib.sha256(self.output.read_bytes()).hexdigest()
        retry = self.provision(learners=2, tests=1)
        self.assertTrue(retry['already_exists'])
        self.assertEqual(hashlib.sha256(self.output.read_bytes()).hexdigest(), original)

    def test_file_permission_failure_precedes_every_credential_write(self):
        real_permissions = _private_permissions
        def deny_file(path, *, apply=False):
            if path == self.output:
                self.assertEqual(path.stat().st_size, 0)
                raise DomainError('account_export_permissions', 'Synthetic permission failure', 422)
            return real_permissions(path, apply=apply)
        with patch('math_learning.account_admin._private_permissions', side_effect=deny_file):
            with self.assertRaises(DomainError) as caught:
                self.provision(learners=2, tests=1)
        self.assertEqual(caught.exception.code, 'account_export_permissions')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(0,)])
        self.assertFalse(self.output.exists())

    def test_partial_write_and_fsync_failures_leave_no_accounts_or_incomplete_file(self):
        def partial_write(descriptor, payload):
            os.write(descriptor, payload[:500])
            raise OSError('Synthetic export failure')
        for target, effect in [('persist', partial_write), ('fsync', OSError('Synthetic flush failure'))]:
            with self.subTest(target=target):
                manager = (patch.object(self.admin, '_persist', side_effect=effect) if target == 'persist'
                           else patch('math_learning.account_admin.os.fsync', side_effect=effect))
                with manager, self.assertRaises(DomainError) as caught:
                    self.provision(learners=2, tests=1)
                self.assertEqual(caught.exception.code, 'account_export_failed')
                self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(0,)])
                self.assertFalse(self.output.exists())
        self.assertEqual(self.provision(learners=2, tests=1)['account_count'], 3)

    def test_database_commit_failure_keeps_private_manifest_and_retries_same_codes(self):
        def connection():
            con = sqlite3.connect(self.store.db_path, isolation_level=None, factory=CommitFailure)
            con.row_factory = sqlite3.Row
            return con
        with patch.object(self.store, '_connect', side_effect=connection), self.assertRaises(DomainError) as caught:
            self.provision(learners=2, tests=1)
        self.assertEqual(caught.exception.code, 'account_export_failed')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(0,)])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM account_admin_operations'), [(0,)])
        self.assertTrue(self.output.is_file())
        original = hashlib.sha256(self.output.read_bytes()).hexdigest()
        self.assert_private(self.output)
        receipt = self.provision(learners=2, tests=1)
        self.assertFalse(receipt['already_exists'])
        self.assertEqual(hashlib.sha256(self.output.read_bytes()).hexdigest(), original)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(3,)])
        manifest = self.manifest()
        test_account = next(account for account in manifest['accounts'] if account['purpose'] == 'test')
        logged_in = self.store.login(test_account['invitation_code'])
        self.assertEqual(logged_in['access']['learner_id'], test_account['learner_id'])

    def test_mid_batch_insertion_failure_rolls_back_every_account(self):
        insert = self.store._insert_account
        calls = 0
        def fail_third(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise sqlite3.OperationalError('Synthetic insert failure')
            return insert(*args, **kwargs)
        with patch.object(self.store, '_insert_account', side_effect=fail_third), self.assertRaises(DomainError):
            self.provision(learners=2, tests=1)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(0,)])
        self.assertTrue(self.output.exists())
        self.assertEqual(self.provision(learners=2, tests=1)['account_count'], 3)

    def test_existing_manifest_is_strictly_validated_without_overwrite(self):
        with patch.object(self.store, '_insert_account', side_effect=sqlite3.OperationalError('Synthetic interruption')):
            with self.assertRaises(DomainError):
                self.provision(learners=2, tests=1)
        pristine = self.output.read_bytes()
        def change_account(field, value):
            return lambda document: document['accounts'][0].__setitem__(field, value)
        changes = [
            lambda document: document.__setitem__('batch_id', 'wrong-batch'),
            lambda document: document.__setitem__('database', str(self.root / 'wrong.sqlite3')),
            change_account('display_name', 'Wrong name'), change_account('purpose', 'test'),
            lambda document: document['accounts'][0].__setitem__('learner_id', document['accounts'][1]['learner_id']),
            lambda document: document['accounts'][0].__setitem__('invitation_code', document['accounts'][1]['invitation_code']),
            lambda document: document['accounts'].pop(), change_account('topic_ids', []),
            change_account('features', []), lambda document: document.__setitem__('format', True),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                document = json.loads(pristine)
                change(document)
                self.output.write_text(json.dumps(document), encoding='utf-8')
                saved_hash = hashlib.sha256(self.output.read_bytes()).hexdigest()
                with self.assertRaises(DomainError) as caught:
                    self.provision(learners=2, tests=1)
                self.assertEqual(caught.exception.code, 'account_manifest_invalid')
                self.assertEqual(hashlib.sha256(self.output.read_bytes()).hexdigest(), saved_hash)
                self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(0,)])
        self.output.write_bytes(pristine)
        self.assertEqual(self.provision(learners=2, tests=1)['account_count'], 3)

    def test_completed_batch_does_not_recreate_accounts_after_rotation_or_missing_manifest(self):
        self.provision(learners=2, tests=1)
        account = self.manifest()['accounts'][-1]
        rotation = self.root / 'admin' / 'rotation.json'
        receipt = self.admin.rotate(account['learner_id'], output=rotation)
        self.assert_safe_receipt(receipt)
        self.assertTrue(self.provision(learners=2, tests=1)['already_exists'])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(3,)])
        self.output.unlink()
        retry = self.provision(learners=2, tests=1)
        self.assertTrue(retry['already_exists'])
        self.assertFalse(retry['manifest_available'])
        self.assertFalse(self.output.exists())
        with self.assertRaises(DomainError) as caught:
            self.provision(learners=3, tests=1)
        self.assertEqual(caught.exception.code, 'account_batch_conflict')

    def test_rotation_failure_preserves_old_code_and_sessions_until_private_export_commits(self):
        self.admin.create('Test account', purpose='test', output=self.output)
        account = self.manifest()['accounts'][0]
        old_identity = self.store.login(account['invitation_code'])['identity_token']
        destination = self.root / 'admin' / 'rotated.json'
        def connection():
            con = sqlite3.connect(self.store.db_path, isolation_level=None, factory=CommitFailure)
            con.row_factory = sqlite3.Row
            return con
        with patch.object(self.store, '_connect', side_effect=connection), self.assertRaises(DomainError):
            self.admin.rotate(account['learner_id'], output=destination)
        self.assertEqual(self.store.resolve(old_identity)['learner_id'], account['learner_id'])
        self.assertEqual(self.store.login(account['invitation_code'])['access']['learner_id'], account['learner_id'])
        pending = self.manifest(destination)['accounts'][0]
        with self.assertRaises(DomainError):
            self.store.login(pending['invitation_code'])
        receipt = self.admin.rotate(account['learner_id'], output=destination)
        self.assertFalse(receipt['already_exists'])
        with self.assertRaises(DomainError):
            self.store.resolve(old_identity)
        with self.assertRaises(DomainError):
            self.store.login(account['invitation_code'])
        renewed = self.store.login(pending['invitation_code'])['identity_token']
        self.assertTrue(self.admin.rotate(account['learner_id'], output=destination)['already_exists'])
        self.assertEqual(self.store.resolve(renewed)['learner_id'], account['learner_id'])

    def test_export_rejects_outside_relative_traversal_and_non_json_paths(self):
        for path in [Path('relative.json'), self.root / 'website' / 'invites.json',
                     self.root / 'admin' / '..' / 'outside.json', self.root / 'admin' / 'invites.md']:
            with self.subTest(path=str(path)):
                with self.assertRaises(DomainError) as caught:
                    self.admin.provision('safe-batch', learners=1, tests=0, output=path)
                self.assertEqual(caught.exception.code, 'account_export_path')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(0,)])

    def test_export_rejects_symbolic_link_or_junction_escape(self):
        (self.root / 'admin').mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'admin' / 'redirected'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                                    capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0)
        with self.assertRaises(DomainError) as caught:
            self.admin.provision('safe-batch', learners=1, tests=0, output=link / 'invites.json')
        self.assertEqual(caught.exception.code, 'account_export_path')
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(0,)])

    def _cli(self, *arguments):
        course_path = self.root / 'courses' / 'digits-to-letters.json'
        course_path.parent.mkdir(exist_ok=True)
        course_path.write_text(json.dumps(synthetic_course()), encoding='utf-8')
        stdout, stderr = io.StringIO(), io.StringIO()
        status = 0
        with patch.object(sys, 'argv', ['math-learning', '--data-dir', str(self.root), *arguments]), redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                cli.main()
            except SystemExit as exc:
                status = exc.code
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cli_provision_management_and_optional_single_create_export_never_print_codes(self):
        status, stdout, stderr = self._cli('account-provision', '--batch', 'cli-batch', '--learners', '1', '--tests', '1', '--output', str(self.output))
        self.assertEqual(status, 0)
        self.assertTrue('MLW-' not in stdout + stderr, 'Provision CLI exposed invitation')
        self.assertEqual(json.loads(stdout)['account_count'], 2)
        account = self.manifest()['accounts'][1]
        for command in ('account-disable', 'account-enable', 'account-revoke-sessions'):
            status, stdout, stderr = self._cli(command, '--learner', account['learner_id'])
            self.assertEqual(status, 0)
            self.assertTrue('MLW-' not in stdout + stderr, 'Management CLI exposed invitation')
        status, stdout, stderr = self._cli('account-list')
        self.assertEqual(status, 0)
        listed = json.loads(stdout)['accounts']
        self.assertTrue(all({'purpose', 'enabled', 'batch_id'} <= item.keys() for item in listed))
        self.assertTrue('MLW-' not in stdout + stderr and 'invitation_code' not in stdout, 'Account list exposed invitation')
        single = self.root / 'admin' / 'single.json'
        status, stdout, stderr = self._cli('account-create', '--name', 'Exported test', '--purpose', 'test', '--output', str(single))
        self.assertEqual(status, 0)
        self.assertTrue('MLW-' not in stdout + stderr, 'Create CLI exposed invitation')
        rotated = self.root / 'admin' / 'rotated.json'
        status, stdout, stderr = self._cli('account-rotate', '--learner', account['learner_id'], '--output', str(rotated))
        self.assertEqual(status, 0)
        self.assertTrue('MLW-' not in stdout + stderr, 'Rotation CLI exposed invitation')

    def test_legacy_create_stdout_and_sanitized_export_error(self):
        status, stdout, stderr = self._cli('account-create', '--name', 'Legacy test', '--purpose', 'test')
        self.assertEqual(status, 0)
        legacy = json.loads(stdout)
        self.assertTrue(bool(re.fullmatch(r'MLW-[A-Za-z0-9_-]{32}', legacy['invitation_code'])), 'Legacy one-time output missing')
        with patch.object(AccountAdmin, '_persist', side_effect=OSError('Sensitive internal error ' + legacy['invitation_code'])):
            status, stdout, stderr = self._cli('account-create', '--name', 'Private test', '--purpose', 'test', '--output', str(self.output))
        self.assertEqual(status, 2)
        self.assertTrue(legacy['invitation_code'] not in stdout + stderr, 'Error leaked invitation')
        self.assertTrue('Traceback' not in stderr)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access_principals'), [(1,)])


if __name__ == '__main__':
    unittest.main()
