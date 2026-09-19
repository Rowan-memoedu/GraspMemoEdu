from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .judge import ExistingJudge
from .service import DomainError, LearningService


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    attempt_id: str = Field(min_length=1, max_length=128)
    step_id: str = Field(min_length=1, max_length=256)


class Choice(Action):
    choice: str


class Answer(Action):
    answer: str = Field(min_length=1, max_length=2000)


class Pause(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)


def create_app(settings: Settings | None = None, service: LearningService | None = None):
    settings = settings or Settings.load()
    if service is None:
        course = json.loads(settings.course_path.read_text(encoding='utf-8'))
        service = LearningService(course, settings.data_dir, ExistingJudge(settings.judge_project))
    session_token = secrets.token_urlsafe(32)
    # Store only a salted cryptographic hash of the remote pairing credential.
    pairing_file = settings.data_dir / 'pairing.json'
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='answer-judge')

    @asynccontextmanager
    async def lifespan(app):
        service.recover_interrupted()
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title='MathLearningWeb', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.state.settings = settings

    def error(code, message, status=403):
        return JSONResponse({'detail': {'code': code, 'message': message}}, status_code=status)

    def paired(header):
        if not header.startswith('Bearer ') or not pairing_file.exists():
            return False
        try:
            import hashlib
            saved = json.loads(pairing_file.read_text(encoding='utf-8'))
            supplied = hashlib.sha256((saved['salt'] + header[7:]).encode()).hexdigest()
            return hmac.compare_digest(saved['digest'], supplied)
        except (OSError, ValueError, KeyError):
            return False

    @app.middleware('http')
    async def boundaries(request: Request, call_next):
        host = request.headers.get('host', '').lower()
        exact_hosts = {f'127.0.0.1:{settings.port}', f'localhost:{settings.port}'}
        if host not in exact_hosts:
            return error('invalid_host', '无效的本机服务地址。', 400)
        origin = request.headers.get('origin')
        # Do not regard localhost and 127.0.0.1 as interchangeable origins.
        own_origin = f'http://{host}'
        is_own = origin == own_origin
        remote = bool(origin and origin != own_origin)
        fetch_site = request.headers.get('sec-fetch-site')
        if origin and origin != own_origin and origin not in settings.allowed_origins:
            return error('origin_forbidden', '此网页来源未获授权。')
        if not origin and fetch_site in ('cross-site', 'same-site'):
            return error('origin_forbidden', '跨来源请求被拒绝。')
        def deny(code, message, status=403):
            response = error(code, message, status)
            response.headers['Cache-Control'] = 'no-store'
            if remote and origin in settings.allowed_origins:
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Vary'] = 'Origin'
            return response
        if request.method == 'OPTIONS':
            if not origin or (not is_own and origin not in settings.allowed_origins):
                return error('origin_forbidden', '此网页来源未获授权。')
            requested_method = request.headers.get('access-control-request-method', '').upper()
            requested_headers = {h.strip().lower() for h in request.headers.get('access-control-request-headers', '').split(',') if h.strip()}
            if requested_method not in ('GET', 'POST') or requested_headers - {'authorization', 'content-type', 'x-learning-session'}:
                return deny('preflight_forbidden', '此请求方式未获授权。')
            response = JSONResponse({})
            response.headers.update({'Access-Control-Allow-Origin': origin,
                                     'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                                     'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-Learning-Session',
                                     'Access-Control-Max-Age': '300', 'Vary': 'Origin'})
            if request.headers.get('access-control-request-private-network') == 'true':
                response.headers['Access-Control-Allow-Private-Network'] = 'true'
            return response
        path = request.url.path
        if path == '/api/session':
            if remote or not (is_own or fetch_site == 'same-origin'):
                return deny('session_forbidden', '仅本机同源页面可建立会话。')
        elif path.startswith('/api/') and path != '/api/health':
            supplied = request.headers.get('x-learning-session', '')
            authorized = paired(request.headers.get('authorization', '')) if remote else (
                hmac.compare_digest(supplied, session_token) or paired(request.headers.get('authorization', '')))
            if not authorized:
                return deny('authentication_required', '请连接本机服务并完成配对。', 401)
            if request.method == 'POST' and not request.headers.get('content-type', '').lower().startswith('application/json'):
                return deny('json_required', '需要 JSON 请求。', 415)
            size = request.headers.get('content-length', '0')
            if not size.isdigit() or int(size) > 16384:
                return deny('request_too_large', '请求过大。', 413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if remote:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return error(exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error('invalid_request', '请求字段不完整或格式错误，请刷新后重试。', 422)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'service': 'MathLearningWeb', 'course_ready': True, 'instance_id': settings.instance_id}

    @app.get('/api/session')
    def session():
        return {'session_token': session_token}

    @app.get('/api/state')
    def state():
        return service.state()

    @app.post('/api/choose')
    def choose(payload: Choice):
        return service.choose(payload.model_dump())

    @app.post('/api/continue')
    def advance(payload: Action):
        return service.advance(payload.model_dump())

    @app.post('/api/select')
    def select(payload: Action):
        return service.select(payload.model_dump())

    @app.post('/api/pause')
    def pause(payload: Pause):
        return service.pause(payload.model_dump())

    @app.post('/api/submit')
    def submit(payload: Answer):
        result = service.submit(payload.model_dump())
        if result['status'] == 'pending':
            executor.submit(service.process_submission, result['submission_id'])
        return result

    @app.get('/api/submissions/{submission_id}')
    def submission(submission_id: str):
        return service.submission(submission_id)

    app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent.parent / 'web', html=True), name='web')
    return app
