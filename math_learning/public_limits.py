"""Small, process-local admission limits for the public testing deployment."""

from collections import deque
import math
import threading
import time

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

    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self._events = {}
        self._lock = threading.Lock()

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
