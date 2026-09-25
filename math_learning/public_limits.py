"""Small, process-local admission limits for the public testing deployment."""

from collections import deque
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from starlette.responses import JSONResponse

from .service import DomainError


class PublicLimitError(DomainError):
    def __init__(self, code, message, retry_after):
        super().__init__(code, message, 429)
        self.retry_after = max(1, int(retry_after))


class PublicLimits:
    guest_per_minute = 20
    login_per_minute = 10
    submissions_per_minute = 12
    max_pending = 8

    def __init__(self, clock=None, data_dir=None):
        self.clock = clock or time.monotonic
        self._events = {}
        self._lock = threading.Lock()
        self.daily = DailyJudgeQuota(Path(data_dir)) if data_dir is not None else None

    def _recent(self, key, now):
        events = self._events.setdefault(key, deque())
        while events and events[0] <= now - 60:
            events.popleft()
        return events

    def _check(self, key, limit, now):
        events = self._recent(key, now)
        if len(events) >= limit:
            raise PublicLimitError('rate_limited', '操作过于频繁，请稍后重试。', math.ceil(events[0] + 60 - now))

    def consume(self, key, limit):
        with self._lock:
            now = self.clock()
            self._check(key, limit, now)
            self._recent(key, now).append(now)

    def check(self, key, limit):
        with self._lock:
            self._check(key, limit, self.clock())

    def record(self, key):
        with self._lock:
            now = self.clock()
            self._recent(key, now).append(now)


class DailyJudgeQuota:
    """Durable daily reservations shared by Lesson and Review, including retries."""

    zone = ZoneInfo('Asia/Shanghai')

    @staticmethod
    def configured_limit(name, default, *, allow_unlimited=False):
        value = int(os.environ.get(name, default))
        if allow_unlimited and value == 0:
            return None
        if value <= 0:
            raise ValueError(f'{name} must be a positive integer')
        return value

    def __init__(self, data_dir, now=None):
        self.path = Path(data_dir) / 'judge-daily-quota.sqlite3'
        self.now = now or (lambda: datetime.now(self.zone))
        self.global_limit = self.configured_limit('MATH_JUDGE_DAILY_GLOBAL', 2000)
        self.guest_limit = self.configured_limit('MATH_JUDGE_DAILY_GUEST', 100)
        self.account_limit = self.configured_limit('MATH_JUDGE_DAILY_ACCOUNT', 0, allow_unlimited=True)
        with closing(self._connect()) as con:
            con.execute('''CREATE TABLE IF NOT EXISTS usage (
                day TEXT NOT NULL, scope TEXT NOT NULL, count INTEGER NOT NULL,
                PRIMARY KEY(day, scope))''')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    @contextmanager
    def reserve(self, learner_id, role):
        if role not in ('guest', 'account'):
            raise ValueError('Unknown learner role')
        now = self.now().astimezone(self.zone)
        day = now.date().isoformat()
        midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), self.zone)
        retry_after = math.ceil((midnight - now).total_seconds())
        own_scope = role + ':' + learner_id
        own_limit = self.guest_limit if role == 'guest' else self.account_limit
        with closing(self._connect()) as con:
            con.execute('BEGIN IMMEDIATE')
            try:
                for scope, limit in (('global', self.global_limit), (own_scope, own_limit)):
                    used = con.execute('SELECT count FROM usage WHERE day=? AND scope=?', (day, scope)).fetchone()
                    if limit is not None and used and used[0] >= limit:
                        raise PublicLimitError('daily_judge_quota',
                                               '今日判题额度已用完，请明日再试；本次未计入作答。', retry_after)
                yield
                for scope in ('global', own_scope):
                    con.execute('''INSERT INTO usage(day,scope,count) VALUES (?,?,1)
                        ON CONFLICT(day,scope) DO UPDATE SET count=count+1''', (day, scope))
                con.commit()
            except BaseException:
                con.rollback()
                raise


def judge_usage_report(data_dir, *, day=None, role='all', learner_id=None):
    """Read-only, private per-identity usage for one China-local calendar day."""
    if role not in ('all', 'account', 'guest'):
        raise ValueError('Unknown report role')
    day = day or datetime.now(DailyJudgeQuota.zone).date().isoformat()
    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise ValueError('Day must be YYYY-MM-DD')
    date.fromisoformat(day)
    root = Path(data_dir)
    principals_path = root / 'learning.sqlite3'
    with closing(sqlite3.connect(principals_path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        con.row_factory = sqlite3.Row
        principals = con.execute('''SELECT learner_id,role,display_name,purpose,enabled,is_admin
            FROM access_principals WHERE role IN ('account','guest')''').fetchall()
    entries = {(row['role'], row['learner_id']): {
        'role': row['role'], 'learner_id': row['learner_id'],
        'display_name': row['display_name'], 'used': 0, 'identity_exists': True,
        **({'purpose': row['purpose'], 'enabled': bool(row['enabled']),
            'is_admin': bool(row['is_admin'])} if row['role'] == 'account' else {})
    } for row in principals}
    quota_path = root / 'judge-daily-quota.sqlite3'
    if not quota_path.is_file():
        raise FileNotFoundError('判题用量数据库不存在，无法生成可信报表。')
    with closing(sqlite3.connect(quota_path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        usage = con.execute('SELECT scope,count FROM usage WHERE day=?', (day,)).fetchall()
    global_used = 0
    for scope, count in usage:
        if scope == 'global':
            global_used = count
            continue
        kind, separator, identity = scope.partition(':')
        if not separator or kind not in ('account', 'guest'):
            raise ValueError('Unknown judge usage scope')
        entry = entries.setdefault((kind, identity), {
            'role': kind, 'learner_id': identity, 'display_name': None,
            'used': 0, 'identity_exists': False})
        entry['used'] = count
    selected = [entry for (kind, identity), entry in entries.items()
                if (role == 'all' or kind == role) and (learner_id is None or identity == learner_id)]
    selected.sort(key=lambda entry: (entry['role'], -entry['used'], entry['display_name'] or '', entry['learner_id']))
    return {'day': day, 'timezone': 'Asia/Shanghai', 'global_used': global_used,
            'identities': selected}


class BodyLimitMiddleware:
    """Read at most one bounded POST body, then replay it through the ASGI API.

    Register inside the origin/authentication boundary so even rejection responses
    receive the normal scoped CORS and cache headers. Content-Length is not trusted.
    """

    def __init__(self, app, maximum=16384):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST':
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > self.maximum:
                response = JSONResponse({'detail': {'code': 'request_too_large', 'message': '请求过大。'}}, status_code=413)
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get('more_body', False):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
