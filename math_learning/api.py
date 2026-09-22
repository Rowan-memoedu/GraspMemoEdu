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

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .judge import ExistingJudge
from .service import DomainError, LearningService
from .access import AccessStore
from .catalog import Catalog
from .dashboard import DashboardStore
from .subjects import Subjects
from .guide import register_guide_routes
from .guest_reset import register_guest_reset_routes
from .review import ReviewBackend
from .question_types import demo_answer as interaction_demo_answer
from .review_api import register_review_routes, review_route_feature
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
    elapsed_ms: int | None = Field(default=None, strict=True, ge=0, le=86400000)


class Pause(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)


class GuestAccess(BaseModel):
    model_config = ConfigDict(extra='forbid')


class InvitationLogin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    invitation_code: str = Field(min_length=1, max_length=256)


class CourseSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    course_id: str = Field(min_length=1, max_length=128)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)


class CorrectionQuote(BaseModel):
    model_config = ConfigDict(extra='forbid')
    exact: str = Field(min_length=1, max_length=2000)
    prefix: str = Field(max_length=50)
    suffix: str = Field(max_length=50)
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=1, strict=True)


class TextCorrection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content_block_id: str = Field(min_length=1, max_length=300)
    content_version: str = Field(min_length=1, max_length=128)
    quote: CorrectionQuote
    operation: str = Field(pattern='^(replace|delete)$')
    replacement: str = Field(max_length=2000)
    comment: str = Field(default='', max_length=2000)


class Feedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)
    course_id: str | None = Field(default=None, min_length=1, max_length=128)
    topic_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_id: str | None = Field(default=None, min_length=1, max_length=135)
    question_id: str | None = Field(default=None, min_length=1, max_length=256)
    correction: TextCorrection | None = None


class ServiceCache(dict):
    """Tuple keys, with the original default-topic lookup kept for local tools."""
    def __init__(self, default_topic_id):
        super().__init__()
        self.default_topic_id = default_topic_id

    def __getitem__(self, key):
        return super().__getitem__((key, self.default_topic_id) if isinstance(key, str) else key)


def create_app(settings: Settings | None = None, service: LearningService | None = None):
    settings = settings or Settings.load()
    catalog = Catalog.load(settings, service.course if service else None)
    if service is None:
        course = catalog.topic(catalog.default_topic_id)
        service = LearningService(course, settings.data_dir, ExistingJudge(settings.judge_project))
    session_token = secrets.token_urlsafe(32)
    # Store only a salted cryptographic hash of the remote pairing credential.
    pairing_file = settings.data_dir / 'pairing.json'
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='answer-judge')
    subjects = Subjects(settings, catalog)
    access_store = AccessStore(settings.data_dir, [{'id': topic['id'], 'title': topic['title']} for topic in catalog.topics.values()])
    dashboard_store = DashboardStore(catalog, access_store, subjects)
    dependency_policy = dashboard_store.dependency_policy
    learning_services = ServiceCache(catalog.default_topic_id)
    learning_services[(service.learner_id, service.topic_id)] = service
    if service.review_backend is None:
        ReviewBackend(service)
    services_lock = threading.Lock()
    public_limits = PublicLimits()
    admission_lock = threading.Lock()
    scheduled_submissions = set()
    reschedule_submissions = set()
    executor_stopping = threading.Event()
    public_host = urlsplit(settings.public_api_origin).netloc.lower() if settings.public_api_origin else None

    def service_for(learner_id, topic_id=None):
        topic_id = topic_id or catalog.default_topic_id
        key = (learner_id, topic_id)
        with services_lock:
            if key not in learning_services:
                learning_services[key] = LearningService(
                    catalog.topic(topic_id), settings.data_dir, service.judge, learner_id=learner_id)
                ReviewBackend(learning_services[key])
            return learning_services[key]

    def requested_topic(request):
        topic_id = request.query_params.get('topic_id', catalog.default_topic_id)
        catalog.topic(topic_id)
        subjects.authorize_topic(request.query_params.get('subject_id'), topic_id)
        return topic_id

    def identity(request, feature=None):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subjects.authorize_subject(access, request.query_params.get('subject_id'))
        topic_id = requested_topic(request)
        access_store.authorize(access, topic_id, feature)
        path = request.url.path
        gated = path in ('/api/choose', '/api/continue', '/api/submit', '/api/demo-answer', '/api/review/begin')
        gated = gated or (path.startswith('/api/review/sessions/') and path.rsplit('/', 1)[-1] in ('submit', 'continue', 'demo-answer'))
        if gated:
            dependency_policy.require(access, topic_id)
        elif path == '/api/state' and not dependency_policy.is_ready(access, topic_id):
            with closing(access_store._connect()) as con:
                previous = con.execute('SELECT 1 FROM topics WHERE learner=? AND topic=?',
                                       (access['learner_id'], topic_id)).fetchone()
            if not previous:
                dependency_policy.require(access, topic_id)
        return access, service_for(access['learner_id'], topic_id)

    def public_state(state, access):
        state['access'] = access
        features = set(access['features'])
        state['dependency_ready'] = dependency_policy.is_ready(access, state['topic_id'])
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
            step['actions'] = [action for action in step['actions']
                               if state['dependency_ready'] and action_feature.get(action) in features]
        return state

    def public_submission(result, access, learner):
        # A POST idempotency replay reads the same saved verdict as GET polling.
        # Both routes must honor a subsequently revoked history permission.
        if 'review_history' not in access['features']:
            with closing(learner._connect()) as con:
                row = con.execute('SELECT step FROM submissions WHERE id=? AND learner=? AND topic=?',
                                  (result['submission_id'], learner.learner_id, learner.topic_id)).fetchone()
            if not row or row['step'] != result['state']['active_step_id']:
                raise DomainError('feature_forbidden', '当前账号没有使用此功能的权限。', 403)
        result['state'] = public_state(result['state'], access)
        return result

    def outstanding_count(con):
        return sum(con.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('pending','running')").fetchone()[0]
                   for table in ('submissions', 'review_submissions'))

    def schedule(learner, submission_id, review=False):
        # Called under admission_lock. A transport retry never adds another queue item.
        key = (review, submission_id)
        if key in scheduled_submissions:
            # A service-error retry may have become pending while its previous
            # worker is still flushing logs. That accepted work must survive.
            reschedule_submissions.add(key)
            return
        with closing(learner._connect()) as con:
            table = 'review_submissions' if review else 'submissions'
            row = con.execute(f'SELECT status FROM {table} WHERE id=?', (submission_id,)).fetchone()
        if not row or row['status'] != 'pending':
            return
        scheduled_submissions.add(key)

        def run():
            try:
                (learner.review_backend if review else learner).process_submission(submission_id)
            finally:
                with admission_lock:
                    scheduled_submissions.discard(key)
                    retry_requested = key in reschedule_submissions
                    reschedule_submissions.discard(key)
                    if retry_requested and not executor_stopping.is_set():
                        schedule(learner, submission_id, review)

        try:
            executor.submit(run)
        except BaseException:
            scheduled_submissions.discard(key)
            reschedule_submissions.discard(key)
            raise

    @asynccontextmanager
    async def lifespan(app):
        with closing(sqlite3.connect(service.db_path)) as con:
            pairs = con.execute('SELECT learner,topic FROM topics').fetchall()
        for learner, topic_id in pairs:
            if topic_id in catalog.topics:
                recovered = service_for(learner, topic_id)
                recovered.recover_interrupted()
                recovered.review_backend.recover_interrupted()
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
    app.state.catalog = catalog
    app.state.dashboard_store = dashboard_store
    app.state.subjects = subjects
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
        public_paths = {'/api/health', '/api/access', '/api/access/guest', '/api/access/login', '/api/access/logout',
                        '/api/state', '/api/demo-answer', '/api/guest/reset', '/api/choose', '/api/continue', '/api/select', '/api/pause', '/api/submit',
                        '/api/subjects', '/api/catalog', '/api/catalog/select', '/api/dashboard', '/api/profile', '/api/feedback', '/api/guide'}
        task_answers_path = path.startswith('/api/tasks/') and path.endswith('/answers') and path.count('/') == 4
        review_feature = review_route_feature(path, request.method)
        review_path = review_feature is not None
        graph_method = request.headers.get('access-control-request-method', '').upper() if request.method == 'OPTIONS' else request.method
        graph_read = graph_method == 'GET' and (path == '/api/graphs' or (path.startswith('/api/graphs/') and path.count('/') == 3))
        if public and path.startswith('/api/') and path not in public_paths and not path.startswith('/api/submissions/') and not task_answers_path and not review_path and not graph_read:
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
            action_features = {'/api/choose': 'learn', '/api/continue': 'learn', '/api/guest/reset': 'learn',
                               '/api/submit': 'submit_answer', '/api/select': 'review_history', '/api/pause': 'pause_topic'}
            if path in action_features or path == '/api/state' or path.startswith('/api/submissions/') or review_path:
                try:
                    access = access_store.resolve(request.headers.get('x-learning-identity'))
                    subjects.authorize_subject(access, request.query_params.get('subject_id'))
                    feature = review_feature if review_path else action_features.get(path)
                    if path.startswith('/api/submissions/'):
                        feature = 'submit_answer'
                    access_store.authorize(access, requested_topic(request), feature)
                except DomainError as exc:
                    return deny(exc.code, exc.message, exc.status_code)
            elif path not in ('/api/access/guest', '/api/access/login'):
                try:
                    access_store.resolve(request.headers.get('x-learning-identity'))
                except DomainError as exc:
                    return deny(exc.code, exc.message, exc.status_code)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; connect-src 'self' http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
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
        return access_store.login(payload.invitation_code, request.headers.get('x-learning-identity'))

    @app.post('/api/access/logout')
    def logout(payload: GuestAccess, request: Request):
        return access_store.logout(request.headers.get('x-learning-identity'))

    @app.get('/api/catalog')
    def course_catalog(request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return dashboard_store.catalog_view(access, request.query_params.get('subject_id'))

    @app.get('/api/subjects')
    def subject_catalog(request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return subjects.public(access)

    @app.post('/api/catalog/select')
    def select_course(payload: CourseSelection, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return dashboard_store.select_course(access, payload.course_id, request.query_params.get('subject_id'))

    @app.get('/api/dashboard')
    def dashboard(request: Request, course_id: str | None = None, cursor: str | None = None,
                  limit: int = Query(default=25, ge=1, le=50)):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return dashboard_store.dashboard(access, course_id, cursor, limit, request.query_params.get('subject_id'))

    @app.get('/api/graphs')
    def graph_catalog(request: Request, subject_id: str = 'math'):
        from .course_graph import GraphStore
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subjects.authorize_subject(access, subject_id)
        try:
            store = GraphStore(settings, catalog, subjects)
            rows = []
            for graph in store.all():
                if graph['subject_id'] != subject_id:
                    continue
                visible = store.public(graph, access)
                if visible['nodes']:
                    rows.append({key: visible[key] for key in ('course_id', 'subject_id', 'title')} | {
                        'node_count': len(visible['nodes']), 'edge_count': len(visible['edges'])})
            return {'graphs': rows}
        except (ValueError, OSError) as exc:
            raise DomainError('invalid_graph', '图谱配置无效，请由管理者检查。', 422) from exc

    @app.get('/api/graphs/{course_id}')
    def course_graph(course_id: str, request: Request, subject_id: str = 'math'):
        from .course_graph import GraphStore
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subjects.authorize_subject(access, subject_id)
        try:
            store = GraphStore(settings, catalog, subjects)
            graph = store.load(course_id)
            if graph is None:
                subjects.authorize_course(subject_id, course_id)
                course = catalog.course(course_id)
                graph = {'schema_version': 1, 'subject_id': subject_id, 'course_id': course_id,
                         'title': course['title'],
                         'nodes': [{'topic_id': item['id'], 'title': catalog.topic(item['id'])['title']}
                                   for item in course['topics']],
                         'edges': [{'from': parent, 'to': item['id'], 'type': 'prerequisite'}
                                   for item in course['topics'] for parent in item['prerequisites']]}
            if graph['subject_id'] != subject_id:
                raise DomainError('unknown_graph', '未找到可访问的课程图谱。', 404)
            result = store.public(graph, access)
            if not result['nodes']:
                raise DomainError('unknown_graph', '未找到可访问的课程图谱。', 404)
            if course_id in catalog.courses and any(node['content_status'] == 'available' for node in result['nodes']):
                states = {topic['id']: topic for topic in dashboard_store.dashboard(access, course_id, subject_id=subject_id)['topics']}
                for node in result['nodes']:
                    state = states.get(node['topic_id'], {})
                    node.update({key: state[key] for key in ('status', 'frontier') if key in state})
            return result
        except (ValueError, OSError) as exc:
            raise DomainError('invalid_graph', '图谱配置无效，请由管理者检查。', 422) from exc

    @app.get('/api/tasks/{task_id}/answers')
    def task_answers(task_id: str, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return dashboard_store.answers(access, task_id, request.query_params.get('subject_id'))

    @app.get('/api/profile')
    def profile(request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return dashboard_store.profile(access)

    @app.post('/api/profile')
    def update_profile(payload: ProfileUpdate, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return dashboard_store.update_profile(access, payload.model_dump(exclude_unset=True))

    @app.post('/api/feedback')
    def feedback(payload: Feedback, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return dashboard_store.save_feedback(access, payload.model_dump())

    @app.get('/api/demo-answer')
    def demo_answer(request: Request):
        access, learner = identity(request, 'submit_answer')
        if access['role'] != 'guest':
            raise DomainError('feature_forbidden', '体验专用浏览版仅供游客使用。', 403)
        state = learner.state()
        step = next((item for item in state['steps'] if item['id'] == state['current_step_id']), None)
        if (state['status'] != 'in_progress' or state['pending_submission_id'] or not step
                or step['id'] != state['active_step_id'] or 'submit' not in step['actions']):
            raise DomainError('demo_answer_unavailable', '当前页面没有可作答的题目。', 409)
        return {'step_id': step['id'], 'question_id': step['question_id'],
                'answer': interaction_demo_answer(learner.questions[step['question_id']])}

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
        data = payload.model_dump(exclude_none=True)
        with admission_lock:
            with closing(learner._connect()) as con:
                old = con.execute('SELECT * FROM submissions WHERE id=?', (data['request_id'],)).fetchone()
                outstanding = outstanding_count(con)
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
        return public_submission(result, access, learner)

    @app.get('/api/submissions/{submission_id}')
    def submission(submission_id: str, request: Request):
        access, learner = identity(request, 'submit_answer')
        result = learner.submission(submission_id)
        return public_submission(result, access, learner)

    register_guide_routes(app, settings, access_store, subjects)
    register_guest_reset_routes(app, identity, public_state, Action)
    register_review_routes(app, identity, admission_lock, public_limits, schedule, outstanding_count, dependency_policy)
    app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent.parent / 'web', html=True), name='web')
    return app
