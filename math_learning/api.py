from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from contextlib import closing
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .judge import ExistingJudge
from .service import DomainError, LearningService
from .access import AccessStore
from .public_limits import BodyLimitMiddleware, PublicLimits, PublicLimitError


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


class GuestAccess(BaseModel):
    model_config = ConfigDict(extra='forbid')


class InvitationLogin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    invitation_code: str = Field(min_length=1, max_length=256)


def create_app(settings: Settings | None = None, service: LearningService | None = None):
    settings = settings or Settings.load()
    if service is None:
        course = json.loads(settings.course_path.read_text(encoding='utf-8'))
        service = LearningService(course, settings.data_dir, ExistingJudge(settings.judge_project))
    session_token = secrets.token_urlsafe(32)
    # Store only a salted cryptographic hash of the remote pairing credential.
    pairing_file = settings.data_dir / 'pairing.json'
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='answer-judge')
    access_store = AccessStore(settings.data_dir, [{'id': service.topic_id, 'title': service.course['title']}])
    learning_services = {service.learner_id: service}
    services_lock = threading.Lock()
    public_limits = PublicLimits()
    admission_lock = threading.Lock()
    scheduled_submissions = set()
    reschedule_submissions = set()
    executor_stopping = threading.Event()
    public_host = urlsplit(settings.public_api_origin).netloc.lower() if settings.public_api_origin else None

    def service_for(learner_id):
        with services_lock:
            if learner_id not in learning_services:
                learning_services[learner_id] = LearningService(
                    service.course, settings.data_dir, service.judge, learner_id=learner_id)
            return learning_services[learner_id]

    def identity(request, feature=None):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        access_store.authorize(access, service.topic_id, feature)
        return access, service_for(access['learner_id'])

    def public_state(state, access):
        state['access'] = access
        features = set(access['features'])
        action_feature = {'mastered': 'learn', 'unmastered': 'learn', 'continue': 'learn', 'submit': 'submit_answer'}
        if 'review_history' not in features:
            state['steps'] = [step for step in state['steps'] if step['id'] == state['active_step_id']]
            state['current_step_id'] = state['active_step_id']
            for step in state['steps']:
                step['current'] = True
                if state['status'] == 'in_progress' and not state['pending_submission_id']:
                    step['actions'] = (['mastered', 'unmastered'] if step['phase'] == 'choice'
                                       else ['submit'] if step['phase'] == 'answer' else ['continue'])
        for step in state['steps']:
            step['actions'] = [action for action in step['actions'] if action_feature.get(action) in features]
        return state

    def public_submission(result, access):
        result['state'] = public_state(result['state'], access)
        return result

    def schedule(learner, submission_id):
        # Called under admission_lock. A transport retry never adds another queue item.
        if submission_id in scheduled_submissions:
            # A service-error retry may have become pending while its previous
            # worker is still flushing logs. That accepted work must survive.
            reschedule_submissions.add(submission_id)
            return
        with closing(learner._connect()) as con:
            row = con.execute('SELECT status FROM submissions WHERE id=?', (submission_id,)).fetchone()
        if not row or row['status'] != 'pending':
            return
        scheduled_submissions.add(submission_id)

        def run():
            try:
                learner.process_submission(submission_id)
            finally:
                with admission_lock:
                    scheduled_submissions.discard(submission_id)
                    retry_requested = submission_id in reschedule_submissions
                    reschedule_submissions.discard(submission_id)
                    if retry_requested and not executor_stopping.is_set():
                        schedule(learner, submission_id)

        try:
            executor.submit(run)
        except BaseException:
            scheduled_submissions.discard(submission_id)
            reschedule_submissions.discard(submission_id)
            raise

    @asynccontextmanager
    async def lifespan(app):
        with closing(sqlite3.connect(service.db_path)) as con:
            learners = [row[0] for row in con.execute('SELECT learner FROM topics WHERE topic=?', (service.topic_id,))]
        for learner in learners:
            service_for(learner).recover_interrupted()
        yield
        with admission_lock:
            executor_stopping.set()
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title='MathLearningWeb', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # The subsequently registered boundary middleware wraps this body limiter.
    app.add_middleware(BodyLimitMiddleware, maximum=16384)
    app.state.service = service
    app.state.settings = settings
    app.state.access_store = access_store
    app.state.learning_services = learning_services
    app.state.public_limits = public_limits

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
        public = bool(public_host and host == public_host and host not in exact_hosts)
        request.state.public_transport = public
        if not public and host not in exact_hosts:
            return error('invalid_host', '无效的本机服务地址。', 400)
        origin = request.headers.get('origin')
        path = request.url.path
        # Do not regard localhost and 127.0.0.1 as interchangeable origins.
        own_origin = settings.public_api_origin if public else f'http://{host}'
        permitted_origins = {*settings.allowed_origins, own_origin}
        is_own = origin == own_origin
        remote = bool(origin and origin != own_origin)
        fetch_site = request.headers.get('sec-fetch-site')
        same_origin_read = (public and not origin and request.method in ('GET', 'HEAD')
                            and fetch_site == 'same-origin')
        if same_origin_read and request.headers.get('referer'):
            try:
                referring = urlsplit(request.headers['referer'])
                same_origin_read = f'{referring.scheme}://{referring.netloc}' == own_origin
            except ValueError:
                same_origin_read = False
        if public and path == '/api/session':
            response = error('public_session_forbidden', '公网不提供本机会话。')
            response.headers['Cache-Control'] = 'no-store'
            if origin in permitted_origins:
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Vary'] = 'Origin'
            return response
        if origin and origin not in permitted_origins:
            return error('origin_forbidden', '此网页来源未获授权。')
        if public and path.startswith('/api/') and path != '/api/health' and not origin and not same_origin_read:
            return error('origin_forbidden', '公网学习请求需要已配置的网页来源。')
        if not public and not origin and fetch_site in ('cross-site', 'same-site'):
            return error('origin_forbidden', '跨来源请求被拒绝。')
        def deny(code, message, status=403):
            response = error(code, message, status)
            response.headers['Cache-Control'] = 'no-store'
            if remote and origin in permitted_origins:
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Vary'] = 'Origin'
            return response
        public_paths = {'/api/health', '/api/access', '/api/access/guest', '/api/access/login',
                        '/api/state', '/api/choose', '/api/continue', '/api/select', '/api/pause', '/api/submit'}
        if public and path.startswith('/api/') and path not in public_paths and not path.startswith('/api/submissions/'):
            return deny('public_endpoint_forbidden', '公网不开放管理操作。')
        if request.method == 'OPTIONS':
            if not origin or origin not in permitted_origins:
                return error('origin_forbidden', '此网页来源未获授权。')
            requested_method = request.headers.get('access-control-request-method', '').upper()
            requested_headers = {h.strip().lower() for h in request.headers.get('access-control-request-headers', '').split(',') if h.strip()}
            if requested_method not in ('GET', 'POST') or requested_headers - {'authorization', 'content-type', 'x-learning-session', 'x-learning-identity'}:
                return deny('preflight_forbidden', '此请求方式未获授权。')
            response = JSONResponse({})
            response.headers.update({'Access-Control-Allow-Origin': origin,
                                     'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                                     'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-Learning-Session, X-Learning-Identity',
                                     'Access-Control-Max-Age': '300', 'Vary': 'Origin'})
            if not public and request.headers.get('access-control-request-private-network') == 'true':
                response.headers['Access-Control-Allow-Private-Network'] = 'true'
            return response
        if path == '/api/session':
            if remote or not (is_own or fetch_site == 'same-origin'):
                return deny('session_forbidden', '仅本机同源页面可建立会话。')
        elif path.startswith('/api/') and path != '/api/health':
            if not public:
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
            # Enforce permissions before parsing a mutation body; hiding controls is not authorization.
            action_features = {'/api/choose': 'learn', '/api/continue': 'learn',
                               '/api/submit': 'submit_answer', '/api/select': 'review_history', '/api/pause': 'pause_topic'}
            if path in action_features or path == '/api/state' or path.startswith('/api/submissions/'):
                try:
                    access = access_store.resolve(request.headers.get('x-learning-identity'))
                    feature = action_features.get(path)
                    if path.startswith('/api/submissions/'):
                        feature = 'submit_answer'
                    access_store.authorize(access, service.topic_id, feature)
                except DomainError as exc:
                    return deny(exc.code, exc.message, exc.status_code)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if remote:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Expose-Headers'] = 'Retry-After'
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        response = error(exc.code, exc.message, exc.status_code)
        if isinstance(exc, PublicLimitError):
            response.headers['Retry-After'] = str(exc.retry_after)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error('invalid_request', '请求字段不完整或格式错误，请刷新后重试。', 422)

    @app.get('/api/health')
    def health(request: Request):
        if request.state.public_transport:
            return {'status': 'ok', 'service': 'MathLearningWeb'}
        return {'status': 'ok', 'service': 'MathLearningWeb', 'course_ready': True, 'instance_id': settings.instance_id}

    @app.get('/api/session')
    def session():
        return {'session_token': session_token}

    @app.get('/deployment.json')
    def deployment(request: Request):
        return {'api_origin': settings.public_api_origin if request.state.public_transport else ''}

    @app.get('/api/access')
    def access(request: Request):
        return access_store.resolve(request.headers.get('x-learning-identity'))

    @app.post('/api/access/guest')
    def guest(payload: GuestAccess, request: Request):
        if request.state.public_transport:
            public_limits.consume('guest', public_limits.guest_per_minute)
        return access_store.create_guest()

    @app.post('/api/access/login')
    def login(payload: InvitationLogin, request: Request):
        if request.state.public_transport:
            public_limits.consume('login', public_limits.login_per_minute)
        return access_store.login(payload.invitation_code)

    @app.get('/api/state')
    def state(request: Request):
        access, learner = identity(request)
        return public_state(learner.state(), access)

    @app.post('/api/choose')
    def choose(payload: Choice, request: Request):
        access, learner = identity(request, 'learn')
        return public_state(learner.choose(payload.model_dump()), access)

    @app.post('/api/continue')
    def advance(payload: Action, request: Request):
        access, learner = identity(request, 'learn')
        return public_state(learner.advance(payload.model_dump()), access)

    @app.post('/api/select')
    def select(payload: Action, request: Request):
        access, learner = identity(request, 'review_history')
        return public_state(learner.select(payload.model_dump()), access)

    @app.post('/api/pause')
    def pause(payload: Pause, request: Request):
        access, learner = identity(request, 'pause_topic')
        return public_state(learner.pause(payload.model_dump()), access)

    @app.post('/api/submit')
    def submit(payload: Answer, request: Request):
        access, learner = identity(request, 'submit_answer')
        data = payload.model_dump()
        with admission_lock:
            with closing(learner._connect()) as con:
                old = con.execute('SELECT * FROM submissions WHERE id=?', (data['request_id'],)).fetchone()
                outstanding = con.execute("SELECT COUNT(*) FROM submissions WHERE status IN ('pending','running')").fetchone()[0]
            replay_or_conflict = old and (old['status'] != 'error' or old['learner'] != learner.learner_id
                                         or old['topic'] != learner.topic_id or old['fingerprint'] != learner._fingerprint('submit', data))
            if replay_or_conflict:
                result = learner.submit(data)
            else:
                if outstanding >= public_limits.max_pending:
                    raise PublicLimitError('judge_capacity', '当前判题队列已满，请稍后重试；本次未计入作答。', 2)
                quota_key = 'submit:' + learner.learner_id
                if request.state.public_transport:
                    public_limits.check(quota_key, public_limits.submissions_per_minute)
                result = learner.submit(data)
                if request.state.public_transport:
                    public_limits.record(quota_key)
            if result['status'] == 'pending':
                schedule(learner, result['submission_id'])
        return public_submission(result, access)

    @app.get('/api/submissions/{submission_id}')
    def submission(submission_id: str, request: Request):
        access, learner = identity(request, 'submit_answer')
        result = learner.submission(submission_id)
        if 'review_history' not in access['features']:
            with closing(learner._connect()) as con:
                row = con.execute('SELECT step FROM submissions WHERE id=? AND learner=? AND topic=?',
                                  (submission_id, learner.learner_id, learner.topic_id)).fetchone()
            if not row or row['step'] != result['state']['active_step_id']:
                raise DomainError('feature_forbidden', '当前账号没有使用此功能的权限。', 403)
        return public_submission(result, access)

    app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent.parent / 'web', html=True), name='web')
    return app
