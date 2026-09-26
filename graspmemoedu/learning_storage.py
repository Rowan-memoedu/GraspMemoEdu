"""One storage boundary for normal learning and unrecorded administrator sessions."""
from contextlib import closing
from pathlib import Path
import sqlite3
import threading
import uuid


def administrator(data_dir, learner_id):
    database = Path(data_dir) / 'learning.sqlite3'
    if not database.exists():
        return False
    with closing(sqlite3.connect(database)) as con:
        columns = {r[1] for r in con.execute('PRAGMA table_info(access_principals)')}
        if 'is_admin' not in columns:
            return False
        row = con.execute('SELECT is_admin FROM access_principals WHERE learner_id=?', (learner_id,)).fetchone()
        return bool(row and row[0])


class ConnectionLease:
    def __init__(self, connection, lock):
        self.connection, self.lock, self.closed = connection, lock, False

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.connection.close()
            finally:
                self.lock.release()


class MemoryLearningStore:
    """Shared in-memory SQLite preserves the existing transactional state machines.

    No temporary files, SQLite journals, outbox files or backup exports are created.
    Closing a login session destroys the database after outstanding readers finish.
    """
    def __init__(self):
        self.uri = 'file:admin-learning-' + uuid.uuid4().hex + '?mode=memory&cache=shared'
        self.lock = threading.RLock()
        self.closed = False
        self.workers, self.close_requested = 0, False
        self.keeper = sqlite3.connect(self.uri, uri=True, check_same_thread=False)

    def connect(self):
        self.lock.acquire()
        try:
            if self.closed:
                from .service import DomainError
                raise DomainError('preview_expired', '临时学习会话已结束，请重新进入内容。', 409)
            con = sqlite3.connect(self.uri, uri=True, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('PRAGMA temp_store=MEMORY')
            return ConnectionLease(con, self.lock)
        except BaseException:
            self.lock.release()
            raise

    def close(self):
        with self.lock:
            self.close_requested = True
            if not self.workers and not self.closed:
                self.closed = True
                self.keeper.close()

    def retain_job(self):
        with self.lock:
            if self.closed:
                raise RuntimeError('Closed preview session')
            self.workers += 1

    def release_job(self):
        with self.lock:
            self.workers -= 1
            if self.close_requested:
                self.close()
