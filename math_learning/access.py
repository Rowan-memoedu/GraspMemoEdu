"""Local identity and permission records; no raw credentials are persisted."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
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


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def credential_digest(kind, value):
    return hashlib.sha256((kind + '\0' + value).encode('utf-8')).hexdigest()


class AccessStore:
    """Identity tables share the learning DB so backups preserve their mapping."""

    def __init__(self, data_dir: Path, topics: list[dict]):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / 'learning.sqlite3'
        self.topics = {topic['id']: {'id': topic['id'], 'title': topic['title']} for topic in topics}
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
        features = list(GUEST_FEATURES) if guest else json.loads(row['features'])
        topic_ids = list(self.topics) if guest else json.loads(row['topic_ids'])
        return {
            'role': row['role'], 'learner_id': row['learner_id'],
            'display_name': row['display_name'],
            'features': [feature for feature in FEATURES if feature in features],
            'topics': [self.topics[topic_id] for topic_id in topic_ids if topic_id in self.topics],
        }

    def _new_identity(self, con, learner_id):
        token = secrets.token_urlsafe(32)
        con.execute('INSERT INTO access_identities VALUES (?,?,?)',
                    (credential_digest('identity', token), learner_id, timestamp()))
        return token

    def create_guest(self):
        learner_id = 'guest-' + uuid.uuid4().hex
        with self._transaction() as con:
            now = timestamp()
            con.execute('INSERT INTO access_principals VALUES (?,?,?,?,?,?,?,?)',
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
            row = con.execute('''SELECT p.* FROM access_principals p
                JOIN access_identities i ON i.learner_id=p.learner_id WHERE i.token_digest=?''',
                (credential_digest('identity', token),)).fetchone()
        if not row:
            raise DomainError('invalid_identity', '访问身份已失效，请重新选择访问方式。', 401)
        return self._public(row)

    def authorize(self, access, topic_id, feature=None):
        if topic_id not in {topic['id'] for topic in access['topics']}:
            raise DomainError('topic_forbidden', '当前身份没有访问此 Topic 的权限。', 403)
        if feature and feature not in access['features']:
            message = REGISTER_MESSAGE if access['role'] == 'guest' and feature == 'pause_topic' else '当前账号没有使用此功能的权限。'
            raise DomainError('feature_forbidden', message, 403)

    def login(self, invitation_code):
        if not isinstance(invitation_code, str) or not invitation_code.strip() or len(invitation_code) > 256:
            raise DomainError('invalid_invitation', '邀请码无效，请联系管理者。', 401)
        with self._transaction() as con:
            row = con.execute("SELECT * FROM access_principals WHERE role='account' AND invitation_digest=?",
                              (credential_digest('invitation', invitation_code.strip()),)).fetchone()
            if not row:
                raise DomainError('invalid_invitation', '邀请码无效，请联系管理者。', 401)
            token = self._new_identity(con, row['learner_id'])
        return {'identity_token': token, 'access': self._public(row)}

    def _validate_permissions(self, topic_ids, features):
        if not isinstance(topic_ids, (tuple, list)) or any(topic not in self.topics for topic in topic_ids):
            raise DomainError('invalid_topics', 'Topic 权限必须使用当前已配置的 Topic 标识。', 422)
        if not isinstance(features, (tuple, list)) or any(feature not in FEATURES for feature in features):
            raise DomainError('invalid_features', '功能权限包含未支持的功能标识。', 422)
        return list(dict.fromkeys(topic_ids)), [feature for feature in FEATURES if feature in features]

    def create_account(self, display_name, topic_ids=None, features=None):
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
            raise DomainError('invalid_account_name', '账号显示名称需要 1 至 100 个字符。', 422)
        topic_ids, features = self._validate_permissions(
            list(self.topics) if topic_ids is None else topic_ids,
            list(FEATURES) if features is None else features)
        learner_id = 'account-' + uuid.uuid4().hex
        invitation = 'MLW-' + secrets.token_urlsafe(24)
        with self._transaction() as con:
            now = timestamp()
            con.execute('INSERT INTO access_principals VALUES (?,?,?,?,?,?,?,?)',
                        (learner_id, 'account', display_name.strip(), json.dumps(features), json.dumps(topic_ids),
                         credential_digest('invitation', invitation), now, now))
            row = con.execute('SELECT * FROM access_principals WHERE learner_id=?', (learner_id,)).fetchone()
        return {'account': self._public(row), 'invitation_code': invitation,
                'notice': '邀请码仅本次显示，请通过可信方式交付给对应学习者。'}

    def set_permissions(self, learner_id, topic_ids=None, features=None):
        with self._transaction() as con:
            row = con.execute("SELECT * FROM access_principals WHERE learner_id=? AND role='account'", (learner_id,)).fetchone()
            if not row:
                raise DomainError('account_not_found', '未找到指定账号；游客权限为固定规则。', 404)
            topic_ids, features = self._validate_permissions(
                json.loads(row['topic_ids']) if topic_ids is None else topic_ids,
                json.loads(row['features']) if features is None else features)
            con.execute('UPDATE access_principals SET topic_ids=?,features=?,updated_at=? WHERE learner_id=?',
                        (json.dumps(topic_ids), json.dumps(features), timestamp(), learner_id))
            row = con.execute('SELECT * FROM access_principals WHERE learner_id=?', (learner_id,)).fetchone()
        return self._public(row)

    def list_principals(self, *, accounts_only=False):
        with closing(self._connect()) as con:
            query = 'SELECT * FROM access_principals'
            if accounts_only:
                query += " WHERE role='account'"
            rows = con.execute(query + ' ORDER BY created_at').fetchall()
        return [self._public(row) for row in rows]
