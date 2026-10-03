"""Authenticated training endpoints; content import remains a private CLI operation."""
from contextlib import closing

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .public_limits import PublicLimitError
from .service import DomainError
from .training import TrainingService
from .training_content import permission_id
from .access import feature_eligible

PUBLIC_PATHS = {'/api/training/' + name for name in (
    'catalog', 'bank', 'group', 'open', 'state', 'submit', 'submission', 'reveal', 'rate',
    'history', 'reviews', 'preferences', 'stop')}


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    bank_id: str = Field(min_length=1, max_length=64)
    group_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)


class AttemptAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    attempt_id: str = Field(min_length=1, max_length=128)


class Answer(AttemptAction):
    request_id: str = Field(min_length=1, max_length=128)
    answer: str = Field(min_length=1, max_length=2000)
    elapsed_ms: int | None = Field(default=None, strict=True, ge=0, le=86400000)


class Rating(AttemptAction):
    rating: int = Field(strict=True, ge=1, le=4)
    elapsed_ms: int | None = Field(default=None, strict=True, ge=0, le=86400000)


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    self_rating_enabled: bool = Field(strict=True)


def register_training_routes(app, training, access_store, subjects, service_for,
                             admission_lock, limits, schedule, outstanding_count, clock=None):
    settings = app.state.settings

    def context(request, feature=None):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subjects.authorize_subject(access, request.query_params.get('subject_id'))
        if feature and (feature not in access['features'] or not feature_eligible(access, feature)):
            raise DomainError('feature_forbidden', '当前账号没有使用此功能的权限。', 403)
        runtime = service_for(access['learner_id'], session_scope=request.headers.get('x-learning-identity')).runtime if access.get('is_admin') else None
        backend = TrainingService(training, settings.data_dir, access['learner_id'], app.state.service.judge,
                                  runtime=runtime, clock=clock)
        return access, backend

    def authorize_bank(request, access, bank_id, feature=None):
        bank = training.bank(bank_id)
        subject = request.query_params.get('subject_id')
        if subject is not None and subject != bank['subject_id']:
            raise DomainError('subject_bank_mismatch', '此题库不属于当前学科。', 403)
        access_store.authorize(access, permission_id(bank_id), feature)
        return bank

    def visible(request, access):
        return [bank for bank in training.banks.values()
                if permission_id(bank['id']) in {topic['id'] for topic in access['topics']}
                and (not request.query_params.get('subject_id') or bank['subject_id'] == request.query_params['subject_id'])]

    def self_enabled(access, backend):
        return 'training_self_rating' in access['features'] and backend.preferences()['self_rating_enabled']

    def state(request, access, backend, attempt_id):
        with closing(backend._connect()) as con:
            row = backend._attempt(con, attempt_id)
        authorize_bank(request, access, row['bank'])
        result = backend.state(attempt_id, history='review_history' in access['features'],
                               self_rating=self_enabled(access, backend))
        result['can_submit'] = 'submit_answer' in access['features']
        result['can_learn'] = 'learn' in access['features']
        return result

    def attempt_context(request, attempt_id, feature):
        access, backend = context(request, feature)
        state(request, access, backend, attempt_id)
        return access, backend

    @app.get('/api/training/catalog')
    def catalog(request: Request):
        access, _ = context(request)
        return {'banks': [{k: bank.get(k, '') for k in ('id', 'title', 'subject_id', 'description', 'version')}
                          for bank in visible(request, access)]}

    @app.get('/api/training/bank')
    def bank(request: Request, bank_id: str):
        access, backend = context(request)
        authorize_bank(request, access, bank_id)
        return backend.bank(bank_id, history='review_history' in access['features'])

    @app.get('/api/training/group')
    def group(request: Request, bank_id: str, group_id: str):
        access, backend = context(request, 'learn')
        authorize_bank(request, access, bank_id)
        return backend.group(bank_id, group_id, history='review_history' in access['features'])

    @app.post('/api/training/open')
    def open_question(payload: OpenQuestion, request: Request):
        access, backend = context(request, 'learn')
        authorize_bank(request, access, payload.bank_id)
        attempt = backend.open(payload.bank_id, payload.group_id, payload.question_id, payload.request_id)
        return state(request, access, backend, attempt)

    @app.get('/api/training/state')
    def get_state(request: Request, attempt_id: str):
        access, backend = context(request)
        return state(request, access, backend, attempt_id)

    @app.post('/api/training/submit')
    def submit(payload: Answer, request: Request):
        access, backend = attempt_context(request, payload.attempt_id, 'submit_answer')
        data = payload.model_dump(exclude_none=True)
        with admission_lock:
            with closing(backend._connect()) as con:
                old = con.execute('SELECT * FROM training_submissions WHERE id=?', (payload.request_id,)).fetchone()
                outstanding = outstanding_count(con)
            replay = old and (old['status'] != 'error' or old['learner'] != backend.learner_id
                              or old['fingerprint'] != backend.submission_fingerprint(data))
            if replay:
                submission_id = backend.submit(data)
            else:
                if outstanding >= limits.max_pending:
                    raise PublicLimitError('judge_capacity', '当前判题队列已满，请稍后重试；本次未计入作答。', 2)
                key = 'submit:' + backend.learner_id
                if request.state.public_transport:
                    limits.check(key, limits.submissions_per_minute)
                with limits.daily.reserve(backend.learner_id, access['role']):
                    submission_id = backend.submit(data)
                if request.state.public_transport:
                    limits.record(key)
            schedule(backend, submission_id)
        return state(request, access, backend, payload.attempt_id)

    @app.get('/api/training/submission')
    def submission(request: Request, submission_id: str):
        access, backend = context(request, 'submit_answer')
        return state(request, access, backend, backend.submission_attempt(submission_id))

    def self_context(request, attempt_id):
        access, backend = attempt_context(request, attempt_id, 'submit_answer')
        if not self_enabled(access, backend):
            raise DomainError('self_rating_forbidden', '此账号未开启高阶学习者作答。', 403)
        return access, backend

    @app.post('/api/training/reveal')
    def reveal(payload: AttemptAction, request: Request):
        access, backend = self_context(request, payload.attempt_id)
        backend.reveal(payload.attempt_id)
        return state(request, access, backend, payload.attempt_id)

    @app.post('/api/training/rate')
    def rate(payload: Rating, request: Request):
        access, backend = self_context(request, payload.attempt_id)
        backend.rate(payload.attempt_id, payload.rating, payload.elapsed_ms)
        return state(request, access, backend, payload.attempt_id)

    @app.post('/api/training/stop')
    def stop_reviews(payload: AttemptAction, request: Request):
        access, backend = self_context(request, payload.attempt_id)
        backend.stop_reviews(payload.attempt_id)
        return state(request, access, backend, payload.attempt_id)

    @app.get('/api/training/history')
    def history(request: Request, bank_id: str, question_id: str, offset: int = 0):
        access, backend = context(request, 'review_history')
        authorize_bank(request, access, bank_id)
        if offset < 0 or offset > 10000000:
            raise DomainError('invalid_offset', '无效的分页位置。', 422)
        return backend.history(bank_id, question_id, offset)

    @app.get('/api/training/reviews')
    def reviews(request: Request):
        access, backend = context(request, 'learn')
        return backend.reviews(visible(request, access), all_materials=bool(access.get('is_admin')))

    @app.get('/api/training/preferences')
    def preferences(request: Request):
        access, backend = context(request)
        return {**backend.preferences(), 'eligible': 'training_self_rating' in access['features']}

    @app.post('/api/training/preferences')
    def update_preferences(payload: Preferences, request: Request):
        access, backend = context(request, 'training_self_rating')
        return {**backend.preferences(payload.self_rating_enabled), 'eligible': True}

    app.state.training_catalog = training
