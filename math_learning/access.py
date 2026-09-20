"""Local identity and permission records; no raw credentials are persisted."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import uuid

from .service import DomainError


FEATURES = ('learn', 'submit_answer', 'review_history', 'pause_topic')
GUEST_FEATURES = FEATURES[:3]
REGISTER_MESSAGE = '该功能需注册账号才能使用'
ACCOUNT_SESSION_DAYS = 30
ACCOUNT_PURPOSES = ('learner', 'test')


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def credential_digest(kind, value):
    return hashlib.sha256((kind + '\0' + value).encode('utf-8')).hexdigest()


class AccessStore:
    """Identity tables share the learning DB so backups preserve their mapping."""

    def __init__(self, data_dir: Path, topics: list[dict], *, guest_topic_ids=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / 'learning.sqlite3'
        self.topics = {topic['id']: {'id': topic['id'], 'title': topic['title']} for topic in topics}
        self.guest_topic_ids = set(self.topics) if guest_topic_ids is None else set(guest_topic_ids)
        if self.guest_topic_ids - set(self.topics):
            raise ValueError('Guest Topic permissions must refer to configured Topics')
        with closing(self._connect()) as con:
            con.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS access_principals (
                    learner_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('guest','account')),
                    display_name TEXT NOT NULL,
                    features TEXT NOT NULL,
                    topic_ids TEXT NOT NULL,
                    invitation_digest TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS access_identities (
                    token_digest TEXT PRIMARY KEY,
                    learner_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS access_identity_learner
                    ON access_identities(learner_id);
            ''')
        # Existing guest identities and learning records retain their IDs.  Run
        # additive migrations under the same writer lock as account operations.
        with self._transaction() as con:
            columns = {row['name'] for row in con.execute('PRAGMA table_info(access_principals)')}
            if 'purpose' not in columns:
                con.execute("ALTER TABLE access_principals ADD COLUMN purpose TEXT NOT NULL DEFAULT 'learner' CHECK(purpose IN ('learner','test'))")
            if 'enabled' not in columns:
                con.execute('ALTER TABLE access_principals ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))')
            if 'batch_id' not in columns:
                con.execute('ALTER TABLE access_principals ADD COLUMN batch_id TEXT')
            # Legacy lists were explicit snapshots. Never infer that an old
            # all-current-topics list authorized future content or features.
            for name in ('topics_default', 'features_default'):
                if name not in columns:
                    con.execute(f'ALTER TABLE access_principals ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0 CHECK({name} IN (0,1))')
            columns = {row['name'] for row in con.execute('PRAGMA table_info(access_identities)')}
            if 'expires_at' not in columns:
                con.execute('ALTER TABLE access_identities ADD COLUMN expires_at TEXT')
                rows = con.execute("""SELECT i.token_digest,i.created_at FROM access_identities i
                    JOIN access_principals p ON p.learner_id=i.learner_id WHERE p.role='account'""").fetchall()
                for row in rows:
                    # Migration does not grant old accounts another 30 days.
                    try:
                        created = datetime.fromisoformat(row['created_at'])
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        expires = created + timedelta(days=ACCOUNT_SESSION_DAYS)
                    except (ValueError, TypeError):
                        expires = datetime(1970, 1, 1, tzinfo=timezone.utc)
                    con.execute('UPDATE access_identities SET expires_at=? WHERE token_digest=?',
                                (expires.isoformat(), row['token_digest']))
            con.execute('CREATE INDEX IF NOT EXISTS access_principal_batch ON access_principals(batch_id)')

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA synchronous=FULL')
        return con

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as con:
            con.execute('BEGIN IMMEDIATE')
            try:
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise

    def _public(self, row):
        guest = row['role'] == 'guest'
        features = list(GUEST_FEATURES) if guest else (list(FEATURES) if row['features_default'] else json.loads(row['features']))
        topic_ids = ([topic for topic in self.topics if topic in self.guest_topic_ids] if guest else
                     (list(self.topics) if row['topics_default'] else json.loads(row['topic_ids'])))
        result = {
            'role': row['role'], 'learner_id': row['learner_id'],
            'display_name': row['display_name'],
            'features': [feature for feature in FEATURES if feature in features],
            'topics': [self.topics[topic_id] for topic_id in topic_ids if topic_id in self.topics],
        }
        if not guest:
            result.update(purpose=row['purpose'], enabled=bool(row['enabled']), batch_id=row['batch_id'],
                          permission_defaults={'topics': bool(row['topics_default']), 'features': bool(row['features_default'])})
        return result

    def _new_identity(self, con, learner_id, *, account=False):
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=ACCOUNT_SESSION_DAYS)).isoformat() if account else None
        con.execute('INSERT INTO access_identities(token_digest,learner_id,created_at,expires_at) VALUES (?,?,?,?)',
                    (credential_digest('identity', token), learner_id, now.isoformat(), expires))
        return token

    def create_guest(self):
        learner_id = 'guest-' + uuid.uuid4().hex
        with self._transaction() as con:
            now = timestamp()
            con.execute('''INSERT INTO access_principals
                        (learner_id,role,display_name,features,topic_ids,invitation_digest,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?)''',
                        (learner_id, 'guest', '游客', json.dumps(GUEST_FEATURES), '[]', None, now, now))
            token = self._new_identity(con, learner_id)
            row = con.execute('SELECT * FROM access_principals WHERE learner_id=?', (learner_id,)).fetchone()
        return {'identity_token': token, 'access': self._public(row)}

    def resolve(self, token):
        if not token:
            raise DomainError('identity_required', '请先选择游客访问或使用邀请码登录。', 401)
        if not isinstance(token, str) or len(token) > 256:
            raise DomainError('invalid_identity', '访问身份已失效，请重新选择访问方式。', 401)
        with closing(self._connect()) as con:
            row = con.execute('''SELECT p.*,i.expires_at FROM access_principals p
                JOIN access_identities i ON i.learner_id=p.learner_id WHERE i.token_digest=?''',
                (credential_digest('identity', token),)).fetchone()
        if not row or not row['enabled']:
            raise DomainError('invalid_identity', '访问身份已失效，请重新选择访问方式。', 401)
        if row['role'] == 'account':
            try:
                expires = datetime.fromisoformat(row['expires_at'])
                if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
                    raise ValueError('expired')
            except (TypeError, ValueError):
                raise DomainError('identity_expired', '账号登录已到期，请重新输入邀请码登录。', 401)
        return self._public(row)

    def authorize(self, access, topic_id, feature=None):
        if access['role'] == 'guest' and topic_id not in self.guest_topic_ids:
            raise DomainError('topic_forbidden', REGISTER_MESSAGE, 403)
        if topic_id not in {topic['id'] for topic in access['topics']}:
            raise DomainError('topic_forbidden', '当前身份没有访问此 Topic 的权限。', 403)
        if feature and feature not in access['features']:
            message = REGISTER_MESSAGE if access['role'] == 'guest' and feature == 'pause_topic' else '当前账号没有使用此功能的权限。'
            raise DomainError('feature_forbidden', message, 403)

    def login(self, invitation_code, current_token=None):
        if not isinstance(invitation_code, str) or not invitation_code.strip() or len(invitation_code) > 256:
            raise DomainError('invalid_invitation', '邀请码无效，请联系管理者。', 401)
        with self._transaction() as con:
            row = con.execute("SELECT * FROM access_principals WHERE role='account' AND invitation_digest=?",
                              (credential_digest('invitation', invitation_code.strip()),)).fetchone()
            if not row or not row['enabled']:
                raise DomainError('invalid_invitation', '邀请码无效，请联系管理者。', 401)
            token = self._new_identity(con, row['learner_id'], account=True)
            # Switch only this browser's account session after authenticating
            # the new account.  Other devices and the guest entry stay valid.
            if isinstance(current_token, str) and len(current_token) <= 256:
                self._revoke_identity(con, current_token)
        return {'identity_token': token, 'access': self._public(row)}

    @staticmethod
    def _revoke_identity(con, token):
        return con.execute("""DELETE FROM access_identities WHERE token_digest=?
            AND learner_id IN (SELECT learner_id FROM access_principals WHERE role='account')""",
            (credential_digest('identity', token),)).rowcount

    def logout(self, token):
        if isinstance(token, str) and len(token) <= 256:
            with self._transaction() as con:
                self._revoke_identity(con, token)
        return {'status': 'logged_out'}

    def _validate_permissions(self, topic_ids, features):
        if not isinstance(topic_ids, (tuple, list)) or any(topic not in self.topics for topic in topic_ids):
            raise DomainError('invalid_topics', 'Topic 权限必须使用当前已配置的 Topic 标识。', 422)
        if not isinstance(features, (tuple, list)) or any(feature not in FEATURES for feature in features):
            raise DomainError('invalid_features', '功能权限包含未支持的功能标识。', 422)
        return list(dict.fromkeys(topic_ids)), [feature for feature in FEATURES if feature in features]

    def _insert_account(self, con, display_name, topic_ids=None, features=None,
                        purpose='learner', batch_id=None, learner_id=None, invitation=None):
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
            raise DomainError('invalid_account_name', '账号显示名称需要 1 至 100 个字符。', 422)
        if purpose not in ACCOUNT_PURPOSES:
            raise DomainError('invalid_account_purpose', '账号用途只能是 learner 或 test。', 422)
        if batch_id is not None and (not isinstance(batch_id, str) or not batch_id.strip() or len(batch_id) > 100):
            raise DomainError('invalid_batch', '批次标识需要 1 至 100 个字符。', 422)
        topics_default, features_default = topic_ids is None, features is None
        topic_ids, features = self._validate_permissions(
            list(self.topics) if topic_ids is None else topic_ids,
            list(FEATURES) if features is None else features)
        learner_id = learner_id or 'account-' + uuid.uuid4().hex
        invitation = invitation or 'MLW-' + secrets.token_urlsafe(24)
        now = timestamp()
        con.execute('''INSERT INTO access_principals
            (learner_id,role,display_name,features,topic_ids,invitation_digest,created_at,updated_at,purpose,batch_id,topics_default,features_default)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (learner_id, 'account', display_name.strip(), json.dumps(features), json.dumps(topic_ids),
             credential_digest('invitation', invitation), now, now, purpose, batch_id, int(topics_default), int(features_default)))
        row = con.execute('SELECT * FROM access_principals WHERE learner_id=?', (learner_id,)).fetchone()
        return {'account': self._public(row), 'invitation_code': invitation,
                'notice': '邀请码可重复登录同一账号，请通过可信方式交付给对应学习者。'}

    def create_account(self, display_name, topic_ids=None, features=None, *, purpose='learner', batch_id=None):
        with self._transaction() as con:
            return self._insert_account(con, display_name, topic_ids, features, purpose, batch_id)

    @staticmethod
    def _account(con, learner_id):
        row = con.execute("SELECT * FROM access_principals WHERE learner_id=? AND role='account'", (learner_id,)).fetchone()
        if not row:
            raise DomainError('account_not_found', '未找到指定账号；游客权限为固定规则。', 404)
        return row

    def set_enabled(self, learner_id, enabled):
        if not isinstance(enabled, bool):
            raise DomainError('invalid_account_status', '启用状态必须为 true 或 false。', 422)
        with self._transaction() as con:
            self._account(con, learner_id)
            con.execute('UPDATE access_principals SET enabled=?,updated_at=? WHERE learner_id=?',
                        (int(enabled), timestamp(), learner_id))
            if not enabled:
                con.execute('DELETE FROM access_identities WHERE learner_id=?', (learner_id,))
            return self._public(self._account(con, learner_id))

    def _rotate_invitation(self, con, learner_id, invitation=None):
        invitation = invitation or 'MLW-' + secrets.token_urlsafe(24)
        self._account(con, learner_id)
        con.execute('UPDATE access_principals SET invitation_digest=?,updated_at=? WHERE learner_id=?',
                    (credential_digest('invitation', invitation), timestamp(), learner_id))
        con.execute('DELETE FROM access_identities WHERE learner_id=?', (learner_id,))
        return {'account': self._public(self._account(con, learner_id)), 'invitation_code': invitation,
                'notice': '旧邀请码和全部账号会话已失效，学习记录保留。'}

    def rotate_invitation(self, learner_id):
        with self._transaction() as con:
            return self._rotate_invitation(con, learner_id)

    def revoke_sessions(self, learner_id):
        with self._transaction() as con:
            self._account(con, learner_id)
            count = con.execute('DELETE FROM access_identities WHERE learner_id=?', (learner_id,)).rowcount
        return {'learner_id': learner_id, 'revoked_sessions': count}

    def set_permissions(self, learner_id, topic_ids=None, features=None, *, reset_topics=False, reset_features=False):
        if type(reset_topics) is not bool or type(reset_features) is not bool:
            raise DomainError('invalid_permission_reset', '恢复默认权限的选项必须为 true 或 false。', 422)
        if (reset_topics and topic_ids is not None) or (reset_features and features is not None):
            raise DomainError('conflicting_permissions', '同一权限不能同时指定列表和恢复默认。', 422)
        validated_topics, validated_features = self._validate_permissions(
            [] if topic_ids is None else topic_ids, [] if features is None else features)
        with self._transaction() as con:
            row = con.execute("SELECT * FROM access_principals WHERE learner_id=? AND role='account'", (learner_id,)).fetchone()
            if not row:
                raise DomainError('account_not_found', '未找到指定账号；游客权限为固定规则。', 404)
            topics_default = 1 if reset_topics else (0 if topic_ids is not None else row['topics_default'])
            features_default = 1 if reset_features else (0 if features is not None else row['features_default'])
            topic_json = '[]' if reset_topics else (json.dumps(validated_topics) if topic_ids is not None else row['topic_ids'])
            feature_json = '[]' if reset_features else (json.dumps(validated_features) if features is not None else row['features'])
            con.execute('UPDATE access_principals SET topic_ids=?,features=?,topics_default=?,features_default=?,updated_at=? WHERE learner_id=?',
                        (topic_json, feature_json, topics_default, features_default, timestamp(), learner_id))
            row = con.execute('SELECT * FROM access_principals WHERE learner_id=?', (learner_id,)).fetchone()
        return self._public(row)

    def list_principals(self, *, accounts_only=False):
        with closing(self._connect()) as con:
            query = 'SELECT * FROM access_principals'
            if accounts_only:
                query += " WHERE role='account'"
            rows = con.execute(query + ' ORDER BY created_at').fetchall()
        return [self._public(row) for row in rows]
