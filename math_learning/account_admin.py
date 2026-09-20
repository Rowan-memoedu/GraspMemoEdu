"""Local-only, transactional account provisioning with private credential files."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import uuid

from .access import FEATURES
from .service import DomainError


_ACL_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$item = Get-Item -LiteralPath $request.path -Force
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$system = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
if ($request.apply) {
    # Use the DACL-only Win32 API. Set-Acl can also request SACL access for the
    # descriptor Python 3.14 creates for a Windows mode-0700 directory.
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AccountPrivateDacl {
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string value, uint revision, out IntPtr descriptor, IntPtr size);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool GetSecurityDescriptorDacl(IntPtr descriptor, out bool present, out IntPtr dacl, out bool defaulted);
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode)]
    public static extern uint SetNamedSecurityInfo(string name, uint type, uint info, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl);
    [DllImport("kernel32.dll")]
    public static extern IntPtr LocalFree(IntPtr handle);
}
'@
    $inherit = if ($item.PSIsContainer) { 'OICI' } else { '' }
    $sddl = "D:P(A;$inherit;FA;;;S-1-5-18)(A;$inherit;FA;;;$($user.Value))"
    $descriptor = [IntPtr]::Zero
    if (-not [AccountPrivateDacl]::ConvertStringSecurityDescriptorToSecurityDescriptor($sddl, 1, [ref]$descriptor, [IntPtr]::Zero)) { throw 'Private descriptor failed' }
    try {
        $present = $false
        $defaulted = $false
        $dacl = [IntPtr]::Zero
        if (-not [AccountPrivateDacl]::GetSecurityDescriptorDacl($descriptor, [ref]$present, [ref]$dacl, [ref]$defaulted) -or -not $present) { throw 'Private DACL missing' }
        $status = [AccountPrivateDacl]::SetNamedSecurityInfo($request.path, 1, [uint32]2147483652, [IntPtr]::Zero, [IntPtr]::Zero, $dacl, [IntPtr]::Zero)
        if ($status -ne 0) { throw 'Private DACL failed' }
    } finally {
        [void][AccountPrivateDacl]::LocalFree($descriptor)
    }
}
$actual = Get-Acl -LiteralPath $request.path
if (-not $actual.AreAccessRulesProtected) { throw 'Private ACL required' }
$found = @{}
foreach ($rule in $actual.Access) {
    $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    if ($sid -notin @($user.Value, $system.Value) -or $rule.AccessControlType -ne 'Allow' -or $rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl) { throw 'Private ACL required' }
    $found[$sid] = $true
}
if (-not $found.ContainsKey($user.Value) -or -not $found.ContainsKey($system.Value)) { throw 'Private ACL required' }
'''


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _private_permissions(path, *, apply=False):
    """No credential is passed to the ACL process or written before this succeeds."""
    try:
        if os.name == 'nt':
            executable = shutil.which('pwsh')
            if not executable:
                raise OSError('PowerShell unavailable')
            result = subprocess.run([executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', _ACL_SCRIPT],
                                    input=json.dumps({'path': str(path), 'apply': apply}),
                                    capture_output=True, text=True, encoding='utf-8', timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise OSError('Private ACL rejected')
        else:
            expected = 0o700 if path.is_dir() else 0o600
            if apply:
                path.chmod(expected)
            current = path.stat()
            if stat.S_IMODE(current.st_mode) != expected or current.st_uid != os.getuid():
                raise OSError('Private permissions required')
    except (OSError, subprocess.SubprocessError):
        raise DomainError('account_export_permissions', '无法确认邀请码文件仅当前用户可访问；未写入邀请码。', 422) from None


def _link(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate manifest key')
        result[key] = value
    return result


class AccountAdmin:
    MAX_MANIFEST_BYTES = 2 * 1024 * 1024

    def __init__(self, store):
        self.store = store
        self.database = str(store.db_path.resolve())
        self.private_root = store.data_dir.resolve() / 'admin'
        with closing(store._connect()) as con:
            con.execute('''CREATE TABLE IF NOT EXISTS account_admin_operations (
                operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                request_json TEXT NOT NULL, output_path TEXT NOT NULL UNIQUE,
                manifest_sha256 TEXT NOT NULL, public_accounts TEXT NOT NULL,
                batch_id TEXT, created_at TEXT NOT NULL)''')

    def _output(self, output):
        path = Path(output)
        if (not path.is_absolute() or '..' in path.parts or path.suffix.lower() != '.json'
                or not path.resolve().is_relative_to(self.private_root)):
            raise DomainError('account_export_path', '邀请码只能导出到数据目录 admin 下的绝对 JSON 文件路径。', 422)
        # Reject reparse points before resolving them; admin itself must never
        # redirect a private export into the website, source tree or another root.
        try:
            relative = path.relative_to(self.private_root)
        except ValueError:
            raise DomainError('account_export_path', '邀请码导出路径不在数据目录 admin 内。', 422) from None
        current = self.private_root
        for part in (None, *relative.parts[:-1]):
            if part is not None:
                current = current / part
            if _link(current) or (current.exists() and not current.is_dir()):
                raise DomainError('account_export_path', '邀请码目录不能使用符号链接或其他文件。', 422)
            current.mkdir(mode=0o700, exist_ok=True)
            _private_permissions(current, apply=True)
        if _link(path) or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
            raise DomainError('account_export_path', '邀请码文件不能使用链接或覆盖目录。', 422)
        return path

    @staticmethod
    def _persist(descriptor, payload):
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError('Incomplete credential write')
            remaining = remaining[written:]
        os.fsync(descriptor)

    def _write(self, path, payload):
        descriptor = None
        own_identity = None
        complete = False
        try:
            # Keep the persisted bytes identical to the audited digest on
            # Windows, where a text-mode descriptor otherwise expands LF.
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
            created = os.fstat(descriptor)
            own_identity = (created.st_dev, created.st_ino)
            _private_permissions(path, apply=True)
            self._persist(descriptor, payload)
            complete = True
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if not complete and own_identity is not None:
                # Remove only our newly created, incomplete file. A caller's
                # pre-existing file is never opened for write or overwritten.
                try:
                    current = path.stat(follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == own_identity:
                        path.unlink()
                except OSError:
                    pass
        if complete and os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def _read(self, path):
        _private_permissions(path)
        with path.open('rb') as stream:
            blob = stream.read(self.MAX_MANIFEST_BYTES + 1)
        if len(blob) > self.MAX_MANIFEST_BYTES:
            raise ValueError('Manifest too large')
        return json.loads(blob.decode('utf-8'), object_pairs_hook=_unique_object), blob

    def _validate(self, manifest, operation_id, kind, batch_id, request, templates, fixed_ids=False):
        if (not isinstance(manifest, dict) or set(manifest) != {
                'format', 'operation', 'operation_id', 'batch_id', 'database', 'created_at', 'request', 'accounts'}
                or type(manifest['format']) is not int or manifest['format'] != 1
                or manifest['operation'] != kind or manifest['operation_id'] != operation_id
                or manifest['batch_id'] != batch_id or manifest['database'] != self.database
                or manifest['request'] != request or not isinstance(manifest['accounts'], list)
                or len(manifest['accounts']) != len(templates)):
            raise ValueError('Manifest does not match request')
        created = datetime.fromisoformat(manifest['created_at'])
        if created.tzinfo is None or created.utcoffset().total_seconds() != 0:
            raise ValueError('Manifest timestamp must be UTC')
        ids, codes = set(), set()
        for account, expected in zip(manifest['accounts'], templates):
            if not isinstance(account, dict) or set(account) != {
                    'learner_id', 'display_name', 'purpose', 'topic_ids', 'features', 'invitation_code'}:
                raise ValueError('Invalid manifest account')
            for field in ('display_name', 'purpose', 'topic_ids', 'features'):
                if account[field] != expected[field]:
                    raise ValueError('Manifest account differs')
            learner_id, code = account['learner_id'], account['invitation_code']
            if (not isinstance(learner_id, str) or not re.fullmatch(r'account-[a-f0-9]{32}', learner_id)
                    or not isinstance(code, str) or not re.fullmatch(r'MLW-[A-Za-z0-9_-]{32}', code)
                    or learner_id in ids or code in codes
                    or ((fixed_ids or 'learner_id' in expected) and learner_id != expected['learner_id'])):
                raise ValueError('Invalid or duplicate account identifier')
            ids.add(learner_id)
            codes.add(code)

    @staticmethod
    def _receipt(kind, batch_id, database, path, accounts, already_exists):
        return {'operation': kind, 'batch_id': batch_id, 'database': database, 'output': str(path),
                'account_count': len(accounts), 'learners': sum(a['purpose'] == 'learner' for a in accounts),
                'tests': sum(a['purpose'] == 'test' for a in accounts),
                'already_exists': already_exists, 'manifest_available': path.is_file()}

    def _run(self, kind, operation_id, batch_id, request, output, templates):
        try:
            with self.store._transaction() as con:
                # Serialize directory creation/ACL changes with the credential
                # operation, including callers in separate local processes.
                path = self._output(output)
                completed = con.execute('SELECT * FROM account_admin_operations WHERE operation_id=?', (operation_id,)).fetchone()
                if completed:
                    if (completed['kind'] != kind or completed['request_json'] != _canonical(request)
                            or completed['output_path'] != str(path)):
                        raise DomainError('account_batch_conflict', '此批次或导出操作已完成，但参数或输出路径不同；未新增账号。', 409)
                    public = json.loads(completed['public_accounts'])
                    if path.exists():
                        manifest, blob = self._read(path)
                        self._validate(manifest, operation_id, kind, batch_id, request, public, fixed_ids=True)
                        if hashlib.sha256(blob).hexdigest() != completed['manifest_sha256']:
                            raise ValueError('Completed manifest was modified')
                    return self._receipt(kind, batch_id, self.database, path, public, True)
                expected = templates(con) if callable(templates) else templates
                if path.exists():
                    manifest, blob = self._read(path)
                    self._validate(manifest, operation_id, kind, batch_id, request, expected)
                else:
                    accounts = [{**entry, 'learner_id': entry.get('learner_id') or 'account-' + uuid.uuid4().hex,
                                 'invitation_code': 'MLW-' + secrets.token_urlsafe(24)} for entry in expected]
                    manifest = {'format': 1, 'operation': kind, 'operation_id': operation_id, 'batch_id': batch_id,
                                'database': self.database, 'created_at': datetime.now(timezone.utc).isoformat(),
                                'request': request, 'accounts': accounts}
                    self._validate(manifest, operation_id, kind, batch_id, request, expected)
                    blob = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
                    self._write(path, blob)
                for account in manifest['accounts']:
                    if kind == 'rotate':
                        self.store._rotate_invitation(con, account['learner_id'], invitation=account['invitation_code'])
                    else:
                        self.store._insert_account(con, account['display_name'], account['topic_ids'], account['features'],
                                                   purpose=account['purpose'], batch_id=batch_id,
                                                   learner_id=account['learner_id'], invitation=account['invitation_code'])
                public = [{key: value for key, value in account.items() if key != 'invitation_code'} for account in manifest['accounts']]
                con.execute('INSERT INTO account_admin_operations VALUES (?,?,?,?,?,?,?,?)',
                            (operation_id, kind, _canonical(request), str(path), hashlib.sha256(blob).hexdigest(),
                             _canonical(public), batch_id, manifest['created_at']))
            return self._receipt(kind, batch_id, self.database, path, public, False)
        except DomainError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise DomainError('account_manifest_invalid', '邀请码清单与当前操作不匹配或已损坏；未覆盖原文件，未改变账号。', 422) from None
        except (OSError, sqlite3.Error, subprocess.SubprocessError):
            raise DomainError('account_export_failed', '私密导出或数据库提交失败；本次账号变更已撤销，可用相同参数重试。', 422) from None

    def provision(self, batch_id, *, learners=10, tests=5, output):
        if not isinstance(batch_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', batch_id):
            raise DomainError('invalid_batch', '批次名需要 1 至 100 个英文字母、数字、点、下划线或连字符。', 422)
        if any(type(count) is not int or count < 0 for count in (learners, tests)) or not 1 <= learners + tests <= 1000:
            raise DomainError('invalid_account_count', '每批账号总数需要为 1 至 1000，且各用途数量不能为负。', 422)
        topics, features = list(self.store.topics), list(FEATURES)
        request = {'learners': learners, 'tests': tests, 'topic_ids': topics, 'features': features}
        entries = [{'display_name': f'{label} {index:02d}', 'purpose': purpose, 'topic_ids': topics, 'features': features}
                   for purpose, label, count in [('learner', '学习者', learners), ('test', '测试账号', tests)]
                   for index in range(1, count + 1)]
        return self._run('provision', 'batch:' + batch_id, batch_id, request, output, entries)

    def create(self, display_name, topic_ids=None, features=None, *, purpose='learner', output):
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
            raise DomainError('invalid_account_name', '账号显示名称需要 1 至 100 个字符。', 422)
        if purpose not in ('learner', 'test'):
            raise DomainError('invalid_account_purpose', '账号用途只能是 learner 或 test。', 422)
        topics, features = self.store._validate_permissions(list(self.store.topics) if topic_ids is None else topic_ids,
                                                          list(FEATURES) if features is None else features)
        request = {'display_name': display_name.strip(), 'purpose': purpose, 'topic_ids': topics, 'features': features}
        operation_id = 'create:' + hashlib.sha256(str(Path(output).resolve()).encode()).hexdigest()
        return self._run('create', operation_id, None, request, output, [request])

    def rotate(self, learner_id, *, output):
        def template(con):
            row = self.store._account(con, learner_id)
            return [{'learner_id': learner_id, 'display_name': row['display_name'], 'purpose': row['purpose'],
                     'topic_ids': json.loads(row['topic_ids']), 'features': json.loads(row['features'])}]
        operation_id = 'rotate:' + hashlib.sha256(str(Path(output).resolve()).encode()).hexdigest()
        return self._run('rotate', operation_id, None, {'learner_id': learner_id}, output, template)
