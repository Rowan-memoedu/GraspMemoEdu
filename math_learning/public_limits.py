"""Small, process-local admission limits for the public testing deployment."""

from collections import deque
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
import math
import os
from pathlib import Path
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
    def configured_limit(name, default):
        value = int(os.environ.get(name, default))
        if value <= 0:
            raise ValueError(f'{name} must be a positive integer')
        return value

    def __init__(self, data_dir, now=None):
        self.path = Path(data_dir) / 'judge-daily-quota.sqlite3'
        self.now = now or (lambda: datetime.now(self.zone))
        self.global_limit = self.configured_limit('MATH_JUDGE_DAILY_GLOBAL', 600)
        self.guest_limit = self.configured_limit('MATH_JUDGE_DAILY_GUEST', 30)
        self.account_limit = self.configured_limit('MATH_JUDGE_DAILY_ACCOUNT', 300)
        with closing(self._connect()) as con:
            con.execute('''CREATE TABLE IF NOT EXISTS usage (
                day TEXT NOT NULL, scope TEXT NOT NULL, count INTEGER NOT NULL,
                PRIMARY KEY(day, scope))''')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    @contextmanager
    def reserve(self, learner_id, role):
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
                    if used and used[0] >= limit:
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
