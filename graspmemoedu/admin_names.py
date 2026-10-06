"""Private roster names, separate from learner-editable profile names."""
import re

from .access import credential_digest
from .service import DomainError


def roster_names(text):
    names = {}
    for line in text.splitlines():
        match = re.fullmatch(r'\s*(?:[-*]\s+)?(.+?)\s*[:：]\s*([A-Za-z0-9_-]{24,128})\s*', line)
        if not match: continue
        name, code = match.groups(); name = name.strip()
        digest = credential_digest('invitation', code)
        if not name or len(name) > 100 or '\x00' in name or digest in names and names[digest] != name:
            raise DomainError('invalid_admin_names', '邀请码清单中的名称无效或重复冲突。', 422)
        names[digest] = name
    if not names:
        raise DomainError('invalid_admin_names', '清单中未找到有效的名称与邀请码。', 422)
    return {'names': [{'invitation_digest': digest, 'admin_name': name} for digest, name in names.items()]}


def sync_admin_names(store, payload, dry_run=False):
    if not isinstance(payload, dict) or set(payload) != {'names'} or not isinstance(payload['names'], list) or not 1 <= len(payload['names']) <= 10000:
        raise DomainError('invalid_admin_names', '管理员名称清单格式无效。', 422)
    seen, updates = set(), []
    with store._transaction() as con:
        for item in payload['names']:
            if not isinstance(item, dict) or set(item) != {'invitation_digest', 'admin_name'}:
                raise DomainError('invalid_admin_names', '管理员名称条目格式无效。', 422)
            digest, name = item['invitation_digest'], item['admin_name']
            if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest) or digest in seen or not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 or '\x00' in name:
                raise DomainError('invalid_admin_names', '管理员名称条目无效或重复。', 422)
            seen.add(digest)
            row = con.execute("SELECT learner_id,admin_name FROM access_principals WHERE invitation_digest=? AND role='account'", (digest,)).fetchone()
            if not row:
                raise DomainError('unknown_roster_account', '清单含无效或已更换的邀请码；全部名称均未修改。', 422)
            if row['admin_name'] != name.strip(): updates.append((name.strip(), row['learner_id']))
        if not dry_run:
            con.executemany('UPDATE access_principals SET admin_name=? WHERE learner_id=?', updates)
    return {'status': 'validated' if dry_run else 'updated', 'matched': len(seen), 'changed': len(updates),
            'profile_names_changed': False, 'learning_records_changed': False}
