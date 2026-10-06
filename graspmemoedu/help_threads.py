"""Versioned two-party Q&A, with content updates independent of learning state."""
import hashlib
import json
import uuid
from pydantic import BaseModel, ConfigDict, Field
from .service import DomainError, now


class HelpMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    message_id: str | None = Field(default=None, min_length=1, max_length=128)
    reply_to: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int = Field(default=0, ge=0)


class HelpThreadRead(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    content_version: str | None = Field(default=None, min_length=1, max_length=128)


class HelpThreads:
    def __init__(self, store):
        self.store = store
        with store.access_store._transaction() as con:
            for sql in (
                '''CREATE TABLE IF NOT EXISTS help_messages (
                    id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES help_requests(id),
                    author_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    author_role TEXT NOT NULL CHECK(author_role IN ('student','admin')),
                    reply_to TEXT REFERENCES help_messages(id), created_at TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS help_message_versions (
                    message_id TEXT NOT NULL REFERENCES help_messages(id), revision INTEGER NOT NULL CHECK(revision>0),
                    author_id TEXT NOT NULL, request_id TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
                    operation TEXT NOT NULL, PRIMARY KEY(message_id,revision), UNIQUE(author_id,request_id))''',
                '''CREATE TABLE IF NOT EXISTS help_thread_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, report_id TEXT NOT NULL REFERENCES help_requests(id),
                    author_id TEXT NOT NULL, message_id TEXT NOT NULL REFERENCES help_messages(id),
                    revision INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(message_id,revision))''',
                'CREATE INDEX IF NOT EXISTS help_thread_event_report ON help_thread_events(report_id,sequence)',
                '''CREATE TABLE IF NOT EXISTS help_thread_reads (
                    report_id TEXT NOT NULL REFERENCES help_requests(id), viewer_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, PRIMARY KEY(report_id,viewer_id))''',
                '''CREATE TABLE IF NOT EXISTS help_content_baselines (
                    report_id TEXT PRIMARY KEY REFERENCES help_requests(id), version TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS help_content_reads (
                    report_id TEXT NOT NULL REFERENCES help_requests(id), viewer_id TEXT NOT NULL,
                    version TEXT NOT NULL, PRIMARY KEY(report_id,viewer_id))'''):
                con.execute(sql)
            for row in con.execute('SELECT id,body FROM help_requests').fetchall():
                self.baseline(con, row['id'], json.loads(row['body']))
                self.sync_legacy(con, row['id'])

    def current_version(self, body):
        request = body['request']
        try:
            source = self.store.training.bank(request['bank_id']) if request.get('bank_id') else self.store.catalog.topic(request['topic_id'])
            return source['version']
        except (DomainError, ValueError, KeyError, OSError):
            return None

    def baseline(self, con, report_id, body):
        version = body.get('source_version') or body['request']['content_version']
        # A converted question/variable prompt uses a separate immutable bank
        # revision, which must not be mistaken for a Topic update on upgrade.
        if not body.get('source_version'):
            request = body['request']
            current = self.current_version(body)
            if request.get('bank_id') and version.startswith('variant-'):
                version = current or version
            elif not request.get('bank_id') and version != current and request.get('question_id'):
                try:
                    from .question_bank import QuestionBank
                    QuestionBank(self.store.catalog.topic(request['topic_id']), self.store.access_store.data_dir).load(version)
                    version = current or version
                except (DomainError, ValueError, KeyError, OSError):
                    pass
        con.execute('INSERT OR IGNORE INTO help_content_baselines VALUES (?,?)', (report_id, version))

    def sync_legacy(self, con, report_id):
        rows = con.execute('SELECT * FROM help_replies WHERE report_id=? ORDER BY revision', (report_id,)).fetchall()
        if not rows:
            return
        ident = 'legacy-' + report_id
        con.execute('INSERT OR IGNORE INTO help_messages VALUES (?,?,?,?,?,?)',
                    (ident, report_id, rows[-1]['admin_id'], 'admin', None, rows[0]['created_at']))
        # The most recent administrator owns the legacy answer after reassignment.
        con.execute('UPDATE help_messages SET author_id=? WHERE id=?', (rows[-1]['admin_id'], ident))
        for row in rows:
            operation = json.dumps({'legacy': row['revision']}, sort_keys=True)
            con.execute('INSERT OR IGNORE INTO help_message_versions VALUES (?,?,?,?,?,?,?)',
                        (ident, row['revision'], row['admin_id'], 'legacy-' + row['request_id'], row['message'], row['created_at'], operation))
            con.execute('INSERT OR IGNORE INTO help_thread_events(report_id,author_id,message_id,revision,created_at) VALUES (?,?,?,?,?)',
                        (report_id, row['admin_id'], ident, row['revision'], row['created_at']))

    def authorized(self, con, access, report_id):
        viewer = self.store._viewer(access)
        if viewer == 'admin':
            self.store._require_admin(con, access['learner_id'])
        row = con.execute('SELECT * FROM help_requests WHERE id=?', (report_id,)).fetchone()
        if (not row or (viewer == 'student' and row['learner_id'] != access['learner_id']) or
                (viewer == 'admin' and self.store._owner(con, row['learner_id']) != access['learner_id']) or
                not self.store.access_store.test_materials.snapshot_visible(json.loads(row['body'])['request']['topic_id'], access)):
            raise DomainError('unknown_help_request', '未找到此提交记录。', 404)
        return row, viewer

    def state(self, con, row, access, include_messages=True):
        body = json.loads(row['body'])
        report, viewer = row['id'], access['learner_id']
        baseline = con.execute('SELECT version FROM help_content_baselines WHERE report_id=?', (report,)).fetchone()
        current = self.current_version(body)
        updated = bool(baseline and current and current != baseline['version'])
        seen = con.execute('SELECT version FROM help_content_reads WHERE report_id=? AND viewer_id=?', (report, viewer)).fetchone()
        update_unread = updated and (not seen or seen['version'] != current)
        events = con.execute('''SELECT COALESCE(MAX(sequence),0) sequence, MAX(created_at) latest_at,
            COALESCE(SUM(author_id<>? AND sequence>COALESCE(
                (SELECT sequence FROM help_thread_reads WHERE report_id=? AND viewer_id=?),0)
                AND NOT(message_id=? AND revision<=COALESCE(
                    (SELECT revision FROM help_reply_reads WHERE report_id=? AND learner_id=?),0))),0) unread
            FROM help_thread_events WHERE report_id=?''', (viewer, report, viewer, 'legacy-' + report, report, viewer, report)).fetchone()
        result = {'thread_sequence': events['sequence'], 'latest_activity_at': events['latest_at'] or row['created_at'],
                  'content_updated': updated, 'current_content_version': current,
                  'content_update_unread': bool(update_unread), 'message_unread': bool(events['unread']),
                  'unread': bool(events['unread'] or update_unread)}
        if include_messages:
            messages = con.execute('''SELECT m.*,v.message,v.revision,v.created_at updated_at,e.sequence event_sequence
                FROM help_messages m JOIN help_message_versions v ON v.message_id=m.id
                    AND v.revision=(SELECT MAX(revision) FROM help_message_versions WHERE message_id=m.id)
                JOIN help_thread_events e ON e.message_id=m.id AND e.revision=v.revision
                WHERE m.report_id=? ORDER BY (SELECT MIN(sequence) FROM help_thread_events WHERE message_id=m.id),m.id''', (report,)).fetchall()
            result['messages'] = [{**dict(m), 'editable': m['author_id'] == viewer} for m in messages]
        return result

    def send(self, access, values):
        values = HelpMessage.model_validate(values).model_dump()
        message = values['message'].strip()
        if not message or '\x00' in message:
            raise DomainError('invalid_help_reply', '请填写回复内容。', 422)
        values['message'] = message
        operation = json.dumps(values, ensure_ascii=False, sort_keys=True)
        with self.store.access_store._transaction() as con:
            row, viewer = self.authorized(con, access, values['report_id'])
            existing = con.execute('''SELECT v.*,m.report_id FROM help_message_versions v JOIN help_messages m ON m.id=v.message_id
                WHERE v.author_id=? AND v.request_id=?''', (access['learner_id'], values['request_id'])).fetchone()
            if existing:
                if existing['operation'] != operation:
                    raise DomainError('request_conflict', '同一请求标识不能用于不同内容。', 409)
                return {'status': 'saved', 'message': self.result(existing)}
            ident, revision = values['message_id'], values['expected_revision']
            created = now()
            if ident:
                original = con.execute('SELECT * FROM help_messages WHERE id=? AND report_id=?', (ident, row['id'])).fetchone()
                if not original or original['author_id'] != access['learner_id']:
                    raise DomainError('help_message_forbidden', '只能修改自己发出的回复。', 403)
                current = con.execute('SELECT MAX(revision) FROM help_message_versions WHERE message_id=?', (ident,)).fetchone()[0]
                if revision != current or values['reply_to'] is not None:
                    raise DomainError('help_reply_conflict', '该回复已在其他页面更新，请重新载入后再提交。', 409)
            else:
                if revision:
                    raise DomainError('help_reply_conflict', '该回复已在其他页面更新，请重新载入后再提交。', 409)
                parent = values['reply_to']
                if parent and not con.execute('SELECT 1 FROM help_messages WHERE id=? AND report_id=?', (parent, row['id'])).fetchone():
                    raise DomainError('unknown_help_request', '未找到此提交记录。', 404)
                ident = str(uuid.uuid4())
                con.execute('INSERT INTO help_messages VALUES (?,?,?,?,?,?)', (ident, row['id'], access['learner_id'], viewer, parent, created))
            con.execute('INSERT INTO help_message_versions VALUES (?,?,?,?,?,?,?)',
                        (ident, revision + 1, access['learner_id'], values['request_id'], message, created, operation))
            con.execute('INSERT INTO help_thread_events(report_id,author_id,message_id,revision,created_at) VALUES (?,?,?,?,?)',
                        (row['id'], access['learner_id'], ident, revision + 1, created))
        return {'status': 'saved', 'message': {'id': ident, 'message': message, 'revision': revision + 1, 'updated_at': created}}

    @staticmethod
    def result(row):
        return {'id': row['message_id'], 'message': row['message'], 'revision': row['revision'], 'updated_at': row['created_at']}

    def read(self, access, values):
        values = HelpThreadRead.model_validate(values).model_dump()
        with self.store.access_store._transaction() as con:
            row, _ = self.authorized(con, access, values['report_id'])
            sequence = values['sequence']
            if sequence and not con.execute('SELECT 1 FROM help_thread_events WHERE report_id=? AND sequence=?', (row['id'], sequence)).fetchone():
                raise DomainError('unknown_help_request', '未找到此答疑回复。', 404)
            con.execute('''INSERT INTO help_thread_reads VALUES (?,?,?) ON CONFLICT(report_id,viewer_id)
                DO UPDATE SET sequence=MAX(sequence,excluded.sequence)''', (row['id'], access['learner_id'], sequence))
            version = values['content_version']
            # Stale receipts must never replace the current version's receipt.
            if version and version == self.current_version(json.loads(row['body'])):
                con.execute('''INSERT INTO help_content_reads VALUES (?,?,?) ON CONFLICT(report_id,viewer_id)
                    DO UPDATE SET version=excluded.version''', (row['id'], access['learner_id'], version))
        return {'status': 'read', 'sequence': sequence}

    @staticmethod
    def activity_key(items):
        return hashlib.sha256(json.dumps(sorted((i['id'], i['thread_sequence'], i['current_content_version']) for i in items)).encode()).hexdigest()
