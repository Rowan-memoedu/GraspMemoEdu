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
from .service import DomainError, LearningService, create_learning_service
from .atomic_content import is_atomic
from .topic_structure import has_atomic
from .atomic_api import register_atomic_routes
from .access import AccessStore
from .access import credential_digest
from .admin_pauses import register_admin_pause_routes, PUBLIC_PATHS as ADMIN_PAUSE_PATHS
from .admin_students import register_admin_student_routes, public_route as admin_student_route
from .learning_storage import MemoryLearningStore, administrator
from .catalog import Catalog
from .dashboard import DashboardStore
from .subjects import Subjects
from .guide import register_guide_routes
from .guest_reset import register_guest_reset_routes
from .review import ReviewBackend
from .question_types import demo_answer as interaction_demo_answer
from .review_api import register_review_routes, review_route_feature
from .public_limits import BodyLimitMiddleware, PublicLimits, PublicLimitError
from .training_content import TrainingCatalog
from .training_api import register_training_routes, PUBLIC_PATHS as TRAINING_PATHS
from .training import TrainingService
from .review_paper_api import register_paper_routes, PUBLIC_PATHS as PAPER_PATHS
from .review_papers import PaperService
from .content_references import ContentReferenceIndex
from .self_assessment import enabled as self_rating_enabled, protect as protect_assessment
from .response_content import compact_response
from .context_library_api import register_context_library_routes, PUBLIC_PATHS as CONTEXT_PATHS


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    attempt_id: str = Field(min_length=1, max_length=128)
    step_id: str = Field(min_length=1, max_length=256)


class Choice(Action):
    choice: str


class Answer(Action):
    answer: str = Field(min_length=1, max_length=16384)
    elapsed_ms: int | None = Field(default=None, strict=True, ge=0, le=86400000)
    self_rating: int | None = Field(default=None, strict=True, ge=1, le=5)


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
    course_hierarchy_enabled: bool = Field(default=True, strict=True)


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


def create_app(settings: Settings | None = None, service: LearningService | None = None, *, atomic_clock=None):
    settings = settings or Settings.load()
    catalog = Catalog.load(settings, service.course if service else None)
    subjects = Subjects(settings, catalog)
    training = TrainingCatalog(settings.data_dir)
    training.validate_ownership(catalog, subjects)
    if service is None:
        course = catalog.topic(catalog.default_topic_id)
        service = create_learning_service(course, settings.data_dir, ExistingJudge(settings.judge_project),
                                          **({'clock': atomic_clock} if has_atomic(course) and atomic_clock else {}))
    if has_atomic(service.course) and atomic_clock:
        service.clock = atomic_clock
    session_token = secrets.token_urlsafe(32)
    # Store only a salted cryptographic hash of the remote pairing credential.
    pairing_file = settings.data_dir / 'pairing.json'
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='answer-judge')
    access_store = AccessStore(settings.data_dir, [{'id': topic['id'], 'title': topic['title']} for topic in catalog.topics.values()] + training.permissions(catalog.topics),
                               test_materials=subjects.test_materials)
    dashboard_store = DashboardStore(catalog, access_store, subjects, atomic_clock=atomic_clock, training=training)
    content_references = ContentReferenceIndex(catalog.topics)
    from .help_requests import register_help_routes
    dependency_policy = dashboard_store.dependency_policy
    learning_services = ServiceCache(catalog.default_topic_id)
    learning_services[(service.learner_id, service.topic_id)] = service
    from .topic_runtime import is_introduction
    from .source_content import trainable
    if trainable(service.course) and service.review_backend is None and not is_atomic(service.course) and not is_introduction(service.course):
        ReviewBackend(service)
    services_lock = threading.Lock()
    public_limits = PublicLimits(data_dir=settings.data_dir)
    admission_lock = threading.Lock()
    scheduled_submissions = set()
    reschedule_submissions = set()
    executor_stopping = threading.Event()
    public_host = urlsplit(settings.public_api_origin).netloc.lower() if settings.public_api_origin else None

    preview_stores = {}

    def forget_preview(token):
        scope = credential_digest('preview', token or '')
        with closing(access_store._connect()) as con:
            con.execute('DELETE FROM academy_capabilities WHERE identity_digest=?', (credential_digest('identity', token or ''),))
            con.commit()
        with services_lock:
            for key in [key for key in learning_services if len(key) == 3 and key[2] == scope]:
                learning_services.pop(key)
            for key in [key for key in preview_stores if key[1] == scope]:
                preview_stores.pop(key).close()

    def service_for(learner_id, topic_id=None, session_scope=None, *, scope_digest=None):
        topic_id = topic_id or catalog.default_topic_id
        admin = administrator(settings.data_dir, learner_id)
        scope = scope_digest or credential_digest('preview', session_scope or '')
        key = (learner_id, topic_id, scope) if admin else (learner_id, topic_id)
        with services_lock:
            if key not in learning_services:
                runtime = None
                if admin:
                    store_key = (learner_id, scope)
                    if store_key not in preview_stores:
                        preview_stores[store_key] = MemoryLearningStore()
                    runtime = preview_stores[store_key]
                learning_services[key] = create_learning_service(
                    catalog.topic(topic_id), settings.data_dir, service.judge, learner_id=learner_id,
                    **({'runtime': runtime} if runtime else {}),
                    **({'clock': atomic_clock} if has_atomic(catalog.topic(topic_id)) and atomic_clock else {}))
                if trainable(learning_services[key].course) and not is_atomic(learning_services[key].course) and not is_introduction(learning_services[key].course):
                    ReviewBackend(learning_services[key])
            learner = learning_services[key]
        learner.resume_due()
        return learner

    def recover_cooldowns(access):
        if access.get('is_admin'):
            return
        allowed = {item['id'] for item in access['topics']}
        with closing(access_store._connect()) as con:
            rows = con.execute('SELECT topic,version,body FROM topics WHERE learner=?', (access['learner_id'],)).fetchall()
            has_reviews = con.execute("SELECT 1 FROM sqlite_master WHERE name='review_topic_pauses'").fetchone()
            legacy_review_topics = {row['topic'] for row in con.execute(
                'SELECT topic FROM review_topic_pauses WHERE learner=?', (access['learner_id'],))} if has_reviews and not access.get('is_student') else set()
        for row in rows:
            state = json.loads(row['body'])
            if (row['topic'] in allowed and row['version'] == catalog.topic(row['topic'])['version']
                    and (state.get('cooldown') or state.get('pause') or row['topic'] in legacy_review_topics)):
                service_for(access['learner_id'], row['topic'])

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
        if topic_id in training.topic_redirects and request.method != 'GET':
            raise DomainError('topic_moved_to_bank', '本题已移入题库，请从题库继续训练。', 409)
        if not trainable(catalog.topic(topic_id)) and request.url.path != '/api/state':
            raise DomainError('content_not_trainable', '原材料尚不满足训练条件，可阅读已保存内容。', 409)
        recover_cooldowns(access)
        path = request.url.path
        gated = path in ('/api/choose', '/api/continue', '/api/skip', '/api/submit', '/api/demo-answer', '/api/review/begin')
        gated = gated or (path.startswith('/api/review/sessions/') and path.rsplit('/', 1)[-1] in ('submit', 'continue', 'demo-answer'))
        if gated:
            dependency_policy.require(access, topic_id)
        elif path == '/api/state' and trainable(catalog.topic(topic_id)) and not dependency_policy.is_ready(access, topic_id):
            with closing(access_store._connect()) as con:
                previous = con.execute('SELECT 1 FROM topics WHERE learner=? AND topic=?',
                                       (access['learner_id'], topic_id)).fetchone()
            if not previous:
                dependency_policy.require(access, topic_id)
        learner = service_for(access['learner_id'], topic_id, request.headers.get('x-learning-identity'))
        if is_introduction(learner.course) and path.startswith('/api/review/'):
            raise DomainError('no_scheduled_review', '引论不安排定时复习。', 422)
        if is_atomic(learner.course) and path.startswith('/api/review/'):
            raise DomainError('wrong_training_mode', '原子训练请使用记忆点复习页面。', 422)
        return access, learner

    def references_for(access, learner):
        # Follow the learner's storage scope, including temporary admin preview.
        with closing(learner._connect()) as con:
            snapshots = {row['topic']: {**json.loads(row['body']), '_course_version': row['version']}
                         for row in con.execute('SELECT topic,version,body FROM topics WHERE learner=?',
                                                (access['learner_id'],))}
        return content_references.view(
            access, learner.topic_id, snapshots,
            lambda tid: subjects.topic_subject(tid),
            lambda tid: dependency_policy.is_ready(access, tid))

    def public_state(state, access, learner):
        state['access'] = access
        if learner.topic_id in training.topic_redirects:
            state['training_target'] = training.topic_redirects[learner.topic_id]
        if state.get('read_only'):
            return state
        features = set(access['features'])
        state['dependency_ready'] = dependency_policy.is_ready(access, state['topic_id'])
        if learner.review_backend is not None:
            with closing(learner._connect()) as con:
                pause = learner.review_backend._paused(con)
                state['topic_locked'] = bool(pause)
                state['topic_pause'] = dict(pause) if pause else None
        else:
            state['topic_locked'] = bool(state.get('pause'))
        with closing(learner._connect()) as con:
            self_rating = self_rating_enabled(access, con) and state['dependency_ready'] and 'submit_answer' in features
        if state.get('training_mode') == 'atomic_retrieval':
            # A paradigm pause cannot freeze the independent point engine.
            if state.get('unified_topic'):
                state['topic_locked'] = bool(state.get('pause'))
                state['topic_pause'] = state.get('pause')
            if state.get('current'):
                protect_assessment(state['current'], self_rating)
                state['current']['can_stop_reviews'] = state['current']['can_self_rate']
            for item in state.get('history', []):
                protect_assessment(item, self_rating)
            if 'review_history' not in features:
                state['history'] = []
                state.pop('unified_history', None)
                if state['phase'] != 'introduction':
                    state['introduction'] = None
            state['can_learn'] = 'learn' in features and state['dependency_ready']
            state['can_submit'] = 'submit_answer' in features and state['dependency_ready']
            return state
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
            protect_assessment(step, self_rating)
            step['actions'] = [action for action in step['actions']
                               if state['dependency_ready'] and not state['topic_locked'] and action_feature.get(action) in features]
        if state.get('topic_format_version') == 2:
            state['content_references'] = references_for(access, learner)
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
        result['state'] = public_state(result['state'], access, learner)
        return result

    def outstanding_count(con):
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return max(len(scheduled_submissions), sum(con.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('pending','running')").fetchone()[0]
                   for table in ('submissions', 'review_submissions', 'training_submissions', 'paper_submissions') if table in tables))

    def schedule(learner, submission_id, review=False):
        # Called under admission_lock. A transport retry never adds another queue item.
        table = 'review_submissions' if review else getattr(learner, 'submission_table', 'submissions')
        key = (id(learner.runtime) if learner.runtime is not None else None, table, submission_id)
        if key in scheduled_submissions:
            # A service-error retry may have become pending while its previous
            # worker is still flushing logs. That accepted work must survive.
            reschedule_submissions.add(key)
            return
        with closing(learner._connect()) as con:
            row = con.execute(f'SELECT status FROM {table} WHERE id=?', (submission_id,)).fetchone()
        if not row or row['status'] != 'pending':
            return
        backend = learner.review_backend if review else learner
        if backend.instant_submission(submission_id):
            # Mechanical answers never wait behind an AI worker or a browser poll.
            backend.process_submission(submission_id)
            return
        scheduled_submissions.add(key)

        def run():
            try:
                (learner.review_backend if review else learner).process_submission(submission_id)
            finally:
                if learner.runtime is not None:
                    learner.runtime.release_job()
                with admission_lock:
                    scheduled_submissions.discard(key)
                    retry_requested = key in reschedule_submissions
                    reschedule_submissions.discard(key)
                    if retry_requested and not executor_stopping.is_set():
                        schedule(learner, submission_id, review)

        retained = False
        try:
            if learner.runtime is not None:
                learner.runtime.retain_job()
                retained = True
            future = executor.submit(run)
            if learner.runtime is not None:
                future.add_done_callback(lambda finished: learner.runtime.release_job() if finished.cancelled() else None)
        except BaseException:
            if retained:
                learner.runtime.release_job()
            scheduled_submissions.discard(key)
            reschedule_submissions.discard(key)
            raise

    @asynccontextmanager
    async def lifespan(app):
        TrainingService(training, settings.data_dir, 'recovery').recover_interrupted()
        with closing(sqlite3.connect(service.db_path)) as con:
            PaperService.recover(con)
            con.commit()
            pairs = con.execute('SELECT learner,topic FROM topics').fetchall()
        for learner, topic_id in pairs:
            if topic_id in catalog.topics:
                recovered = service_for(learner, topic_id)
                recovered.recover_interrupted()
                if recovered.review_backend is not None:
                    recovered.review_backend.recover_interrupted()
        yield
        with admission_lock:
            executor_stopping.set()
        executor.shutdown(wait=False, cancel_futures=True)
        for runtime in preview_stores.values():
            runtime.close()

    app = FastAPI(title='GraspMemoEdu', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # The subsequently registered boundary middleware wraps this body limiter.
    body_limits = {'/api/help-requests': 65536, '/api/help-images': 3 * 1024 * 1024,
                   '/api/training-audio/transcribe': 6 * 1024 * 1024}
    app.add_middleware(BodyLimitMiddleware, maximum=16384, path_limits=body_limits)
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
        from .academy_api import CAPABILITY_PATHS, PUBLIC_PATHS as ACADEMY_PATHS
        academy_callback = path in CAPABILITY_PATHS
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
                        '/api/help-center', '/api/help-replies', '/api/help-replies/read', '/api/help-requests/preview',
                        '/api/help-messages', '/api/help-messages/read', '/api/help-content',
                        '/api/help-main/messages', '/api/help-main/read',
                        '/api/help-images', '/api/presence',
                        '/api/materials', '/api/materials/open', '/api/materials/content',
                        '/api/state', '/api/demo-answer', '/api/guest/reset', '/api/choose', '/api/continue', '/api/select', '/api/pause', '/api/skip', '/api/submit', '/api/reveal',
                        '/api/subjects', '/api/catalog', '/api/catalog/select', '/api/dashboard', '/api/profile', '/api/feedback', '/api/help-requests', '/api/guide', '/api/atomic/reviews'}
        public_paths.update(TRAINING_PATHS)
        public_paths.update(ACADEMY_PATHS)
        public_paths.update(PAPER_PATHS)
        public_paths.update(ADMIN_PAUSE_PATHS)
        public_paths.update(CONTEXT_PATHS)
        student_read = admin_student_route(path)
        task_answers_path = path.startswith('/api/tasks/') and path.endswith('/answers') and path.count('/') == 4
        review_feature = review_route_feature(path, request.method)
        review_path = review_feature is not None
        graph_method = request.headers.get('access-control-request-method', '').upper() if request.method == 'OPTIONS' else request.method
        graph_read = graph_method == 'GET' and (path == '/api/graphs' or (path.startswith('/api/graphs/') and path.count('/') == 3))
        if public and path.startswith('/api/') and path not in public_paths and not path.startswith('/api/submissions/') and not task_answers_path and not review_path and not graph_read and not student_read:
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
            if not public and not academy_callback:
                supplied = request.headers.get('x-learning-session', '')
                authorized = paired(request.headers.get('authorization', '')) if remote else (
                    hmac.compare_digest(supplied, session_token) or paired(request.headers.get('authorization', '')))
                if not authorized:
                    return deny('authentication_required', '请连接本机服务并完成配对。', 401)
            if request.method == 'POST' and not request.headers.get('content-type', '').lower().startswith('application/json'):
                return deny('json_required', '需要 JSON 请求。', 415)
            size = request.headers.get('content-length', '0')
            if not size.isdigit() or int(size) > body_limits.get(path, 16384):
                return deny('request_too_large', '请求过大。', 413)
            # Enforce permissions before parsing a mutation body; hiding controls is not authorization.
            if request.method == 'POST' and (path == '/api/pause' or path.startswith('/api/review/sessions/') and path.endswith('/pause')):
                try:
                    access_store.resolve(request.headers.get('x-learning-identity'))
                except DomainError as exc:
                    return deny(exc.code, exc.message, exc.status_code)
                return deny('manual_pause_removed', '主动暂停功能已移除。', 403)
            action_features = {'/api/choose': 'learn', '/api/continue': 'learn', '/api/guest/reset': 'learn',
                               '/api/submit': 'submit_answer', '/api/select': 'review_history', '/api/skip': 'skip_module'}
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
            elif path not in ('/api/access/guest', '/api/access/login') and not academy_callback:
                try:
                    access = access_store.resolve(request.headers.get('x-learning-identity'))
                    if (path in ADMIN_PAUSE_PATHS or student_read and path.startswith('/api/admin/')) and not access.get('is_admin'):
                        return deny('feature_forbidden', '此页面仅供管理员使用。')
                except DomainError as exc:
                    return deny(exc.code, exc.message, exc.status_code)
        response = await call_next(request)
        response = await compact_response(request, response)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; media-src 'self' blob:; connect-src 'self' http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
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
            return {'status': 'ok', 'service': 'GraspMemoEdu'}
        return {'status': 'ok', 'service': 'GraspMemoEdu', 'course_ready': True, 'instance_id': settings.instance_id}

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
        previous = request.headers.get('x-learning-identity')
        result = access_store.login(payload.invitation_code, previous)
        forget_preview(previous)
        return result

    @app.post('/api/access/logout')
    def logout(payload: GuestAccess, request: Request):
        token = request.headers.get('x-learning-identity')
        result = access_store.logout(token)
        forget_preview(token)
        return result

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
        recover_cooldowns(access)
        return dashboard_store.dashboard(access, course_id, cursor, limit, request.query_params.get('subject_id'),
                                         request.query_params.get('root_id'))

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
            if graph is None and course_id in training.banks:
                bank = training.bank(course_id)
                access_store.authorize(access, 'training.' + course_id)
                graph = {'schema_version': 3, 'subject_id': bank['subject_id'], 'course_id': bank['id'],
                         'title': bank['title'], 'nodes': [
                             {'bank_id': bank['id'], 'group_id': g['id'], 'title': g['title'], 'member': True}
                             for g in bank['groups'] if g.get('questions')], 'edges': []}
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
            root_id = request.query_params.get('root_id')
            if root_id:
                from .course_hierarchy import course_scope, select_subtree
                from .dependency_nodes import node_key
                if course_id in training.banks:
                    scoped = select_subtree(training.bank(course_id)['groups'], root_id)
                    group_ids = {g['id'] for g in scoped}
                    member_ids = {node_key(n) for n in result['nodes'] if n.get('bank_id') == course_id and n.get('group_id') in group_ids}
                    result['title'] = next(g['title'] for g in scoped if g['id'] == root_id)
                else:
                    scoped = course_scope(catalog.course(course_id), root_id)
                    topic_ids = {t['id'] for t in scoped['topics']}
                    member_ids = {node_key(n) for n in result['nodes'] if n.get('topic_id') in topic_ids}
                    result['title'] = scoped['title']
                # A view root hides unrelated branches, but must not erase the
                # prerequisites that explain why a member is still locked.
                parents = {}
                for edge in result['edges']:
                    parents.setdefault(edge['to'], []).append(edge['from'])
                retained, pending = set(member_ids), list(member_ids)
                while pending:
                    for parent in parents.get(pending.pop(), []):
                        if parent not in retained:
                            retained.add(parent); pending.append(parent)
                result['nodes'] = [{**n, 'member': node_key(n) in member_ids} for n in result['nodes'] if node_key(n) in retained]
                result['edges'] = [e for e in result['edges'] if e['from'] in retained and e['to'] in retained]
            if not result['nodes']:
                raise DomainError('unknown_graph', '未找到可访问的课程图谱。', 404)
            # A migrated bank may retain a catalog entry only for old links.
            # Its graph must not re-enter the retired course dashboard.
            if course_id in catalog.courses and course_id not in training.banks and any(
                    node['content_status'] == 'available' for node in result['nodes']):
                states = {topic['id']: topic for topic in dashboard_store.dashboard(access, course_id, subject_id=subject_id)['topics']}
                for node in result['nodes']:
                    state = states.get(node.get('topic_id'), {})
                    node.update({key: state[key] for key in ('status', 'frontier') if key in state})
            return result
        except (ValueError, OSError) as exc:
            raise DomainError('invalid_graph', '图谱配置无效，请由管理者检查。', 422) from exc

    @app.get('/api/tasks/{task_id}/answers')
    def task_answers(task_id: str, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        result = dashboard_store.answers(access, task_id, request.query_params.get('subject_id'))
        topic_id = result['task']['topic_id']
        if catalog.topic(topic_id).get('topic_format_version') == 2:
            result['content_references'] = references_for(access, service_for(access['learner_id'], topic_id, request.headers.get('x-learning-identity')))
        return result

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
        if access['role'] != 'guest' and not access.get('is_admin'):
            raise DomainError('feature_forbidden', '体验专用浏览版仅供游客和管理员使用。', 403)
        state = learner.state()
        if state.get('training_mode') == 'atomic_retrieval':
            current = state['current']
            if not current or state['phase'] != 'answer' or state['pause'] or state['pending_submission_id']:
                raise DomainError('demo_answer_unavailable', '当前页面没有可作答的题目。', 409)
            return {'step_id': current['id'], 'question_id': current['card_id'],
                    'answer': interaction_demo_answer(learner.questions[current['card_id']])}
        step = next((item for item in state['steps'] if item['id'] == state['current_step_id']), None)
        if (state['status'] != 'in_progress' or state['pending_submission_id'] or not step
                or step['id'] != state['active_step_id'] or 'submit' not in step['actions']):
            raise DomainError('demo_answer_unavailable', '当前页面没有可作答的题目。', 409)
        return {'step_id': step['id'], 'question_id': step['question_id'],
                'answer': interaction_demo_answer(learner.question_for_step(step))}

    @app.get('/api/state')
    def state(request: Request):
        target = training.topic_redirects.get(request.query_params.get('topic_id'))
        if target:
            access = access_store.resolve(request.headers.get('x-learning-identity'))
            subjects.authorize_subject(access, request.query_params.get('subject_id'))
            requested_topic(request)
            access_store.authorize(access, 'training.' + target['bank_id'])
            # A bookmark redirect must not initialize or advance the retired Topic.
            return {'training_target': target, 'access': access}
        access, learner = identity(request)
        return public_state(learner.state(), access, learner)

    @app.post('/api/choose')
    def choose(payload: Choice, request: Request):
        access, learner = identity(request, 'learn')
        return public_state(learner.choose(payload.model_dump()), access, learner)

    @app.post('/api/continue')
    def advance(payload: Action, request: Request):
        access, learner = identity(request, 'learn')
        return public_state(learner.advance(payload.model_dump()), access, learner)

    @app.post('/api/select')
    def select(payload: Action, request: Request):
        access, learner = identity(request, 'review_history')
        return public_state(learner.select(payload.model_dump()), access, learner)

    @app.post('/api/pause')
    def pause(payload: Pause, request: Request):
        raise DomainError('manual_pause_removed', '主动暂停功能已移除。', 403)

    @app.post('/api/skip')
    def skip_module(payload: Action, request: Request):
        access, learner = identity(request, 'skip_module')
        return public_state(learner.skip_module(payload.model_dump()), access, learner)

    @app.post('/api/submit')
    def submit(payload: Answer, request: Request):
        access, learner = identity(request, 'submit_answer')
        data = payload.model_dump(exclude_none=True)
        if data.get('self_rating') is not None:
            with closing(learner._connect()) as con:
                if not self_rating_enabled(access, con):
                    raise DomainError('feature_forbidden', '自主评分仅对高阶学习者开放。', 403)
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
                with public_limits.daily.reserve(learner.learner_id, access['role']):
                    result = learner.submit(data)
                if request.state.public_transport:
                    public_limits.record(quota_key)
            if result['status'] == 'pending':
                schedule(learner, result['submission_id'])
                result = learner.submission(result['submission_id'])
        return public_submission(result, access, learner)

    @app.post('/api/reveal')
    def reveal(payload: Action, request: Request):
        access, learner = identity(request, 'submit_answer')
        with closing(learner._connect()) as con:
            if not self_rating_enabled(access, con):
                raise DomainError('feature_forbidden', 'Show Answer 仅对高阶学习者开放。', 403)
        return public_state(learner.reveal(payload.model_dump()), access, learner)

    @app.get('/api/submissions/{submission_id}')
    def submission(submission_id: str, request: Request):
        access, learner = identity(request, 'submit_answer')
        result = learner.submission(submission_id)
        return public_submission(result, access, learner)

    from .materials import register_material_routes
    register_help_routes(app, catalog, access_store, subjects)
    register_admin_pause_routes(app, catalog, access_store, subjects, service_for)
    from .presence import register_presence_route
    register_presence_route(app, access_store, catalog, atomic_clock)
    register_admin_student_routes(app, catalog, access_store, subjects, dashboard_store, training, atomic_clock)
    register_material_routes(app, catalog, access_store, subjects, identity, public_state)
    register_atomic_routes(app, catalog, access_store, subjects, dependency_policy, clock=atomic_clock)
    from .academy_api import register_academy_routes
    register_academy_routes(app, settings, access_store, service_for, identity, public_state, Action, dependency_policy, atomic_clock)
    register_training_routes(app, training, access_store, subjects, service_for,
                             admission_lock, public_limits, schedule, outstanding_count, clock=atomic_clock,
                             dependencies=dependency_policy)
    register_paper_routes(app, training, catalog, dependency_policy, access_store, subjects, service_for,
                         admission_lock, public_limits, schedule, outstanding_count, clock=atomic_clock)
    register_guide_routes(app, settings, access_store, subjects)
    register_guest_reset_routes(app, identity, public_state, Action)
    register_review_routes(app, identity, admission_lock, public_limits, schedule, outstanding_count, dependency_policy, references_for)
    def reading_service(access, request):
        return service_for(access['learner_id'], requested_topic(request), request.headers.get('x-learning-identity'))

    def activate_reading_points(access, topic, point_ids, context, request):
        dependency_policy.require(access, topic['id'])
        learner = reading_service(access, request)
        backend = getattr(learner, 'atomic', learner)
        if not hasattr(backend, 'request_points'):
            raise DomainError('reading_memory_unavailable', '此材料暂未关联可学习的记忆点。', 422)
        result = backend.request_points(point_ids)
        return {**result, 'learning_url': f"#/subjects/{topic['subject_id']}/topic/{topic['id']}"}

    from .context_dictionary import lookup_word
    register_context_library_routes(app, catalog, access_store, subjects,
        activate_points=activate_reading_points,
        dictionary=lambda word: lookup_word(word, data_dir=settings.data_dir / 'context-dictionary'),
        connect_for=lambda access, request: reading_service(access, request)._connect)
    app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent.parent / 'web', html=True), name='web')
    return app
