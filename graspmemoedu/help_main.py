"""One subject-independent conversation per learner; no fabricated content report."""
from .help_threads import HelpThreads
from .service import DomainError


class MainHelpThreads(HelpThreads):
    is_main = True
    tables = {'messages': 'help_main_messages', 'versions': 'help_main_versions',
              'events': 'help_main_events', 'reads': 'help_main_reads'}

    def __init__(self, store):
        self.store = store
        with store.access_store._transaction() as con:
            for sql in (
                '''CREATE TABLE IF NOT EXISTS help_main_messages (
                    id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    author_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    author_role TEXT NOT NULL CHECK(author_role IN ('student','admin')),
                    reply_to TEXT REFERENCES help_main_messages(id), created_at TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS help_main_versions (
                    message_id TEXT NOT NULL REFERENCES help_main_messages(id), revision INTEGER NOT NULL CHECK(revision>0),
                    author_id TEXT NOT NULL, request_id TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
                    operation TEXT NOT NULL, PRIMARY KEY(message_id,revision), UNIQUE(author_id,request_id))''',
                '''CREATE TABLE IF NOT EXISTS help_main_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, report_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    author_id TEXT NOT NULL, message_id TEXT NOT NULL REFERENCES help_main_messages(id),
                    revision INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(message_id,revision))''',
                'CREATE INDEX IF NOT EXISTS help_main_event_report ON help_main_events(report_id,sequence)',
                '''CREATE TABLE IF NOT EXISTS help_main_reads (
                    report_id TEXT NOT NULL REFERENCES access_principals(learner_id), viewer_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, PRIMARY KEY(report_id,viewer_id))'''):
                con.execute(sql)

    def authorized(self, con, access, learner_id):
        viewer = self.store._viewer(access)
        if viewer == 'admin':
            self.store._require_admin(con, access['learner_id'])
        row = con.execute("SELECT * FROM access_principals WHERE learner_id=? AND role='account' AND is_student=1 AND is_admin=0", (learner_id,)).fetchone()
        if not row or (viewer == 'student' and learner_id != access['learner_id']) or (
                viewer == 'admin' and self.store._owner(con, learner_id) != access['learner_id']):
            raise DomainError('unknown_help_request', '未找到此学员的对话。', 404)
        return {'id': learner_id, 'learner_id': learner_id, 'created_at': row['created_at']}, viewer

    def state(self, con, row, access, include_messages=True):
        report, viewer = row['id'], access['learner_id']
        events = self.query(con, '''SELECT COALESCE(MAX(sequence),0) sequence, MAX(created_at) latest_at,
            COALESCE(SUM(author_id<>? AND sequence>COALESCE(
                (SELECT sequence FROM {reads} WHERE report_id=? AND viewer_id=?),0)),0) unread
            FROM {events} WHERE report_id=?''', (viewer, report, viewer, report)).fetchone()
        result = {'id': report, 'learner_id': report, 'is_main': True,
                  'thread_sequence': events['sequence'], 'latest_activity_at': events['latest_at'] or row['created_at'],
                  'message_unread': bool(events['unread']), 'unread': bool(events['unread']),
                  'content_updated': False, 'content_update_unread': False, 'current_content_version': None}
        if include_messages:
            result['messages'] = self.messages(con, report, viewer)
        return result

    def roster(self, con, access):
        viewer = self.store._viewer(access)
        if viewer == 'student':
            ids = [access['learner_id']]
        else:
            self.store._require_admin(con, access['learner_id'])
            ids = [r[0] for r in con.execute("SELECT learner_id FROM access_principals WHERE role='account' AND is_student=1 AND is_admin=0 ORDER BY learner_id")
                   if self.store._owner(con, r[0]) == access['learner_id']]
        return [self.authorized(con, access, ident)[0] for ident in ids]
