"""Authenticated shared review inbox, papers and submission-mode preferences."""
from contextlib import closing
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .access import feature_eligible
from .public_limits import PublicLimitError
from .review_cards import load_paper_types
from .review_papers import PaperService
from .review_hierarchy import source_trees
from .materials import material_inventory
from .service import DomainError
from .training import TrainingService
from .training_content import permission_id

PUBLIC_PATHS = {'/api/review-papers/' + part for part in ('inbox', 'open', 'state', 'draft', 'submit', 'cancel', 'preferences', 'activity', 'reveal')}


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class OpenPaper(Model):
    request_id: str = Field(min_length=1, max_length=100)
    candidate_id: str = Field(pattern=r'^[a-f0-9]{64}$')


class Paper(Model):
    paper_id: str = Field(min_length=1, max_length=100)


class Draft(Model):
    index: int = Field(strict=True, ge=0, le=59)
    answer: str = Field(max_length=2000)
    elapsed_ms: int = Field(default=0, strict=True, ge=0, le=86400000)
    self_rating: int | None = Field(default=None, strict=True, ge=1, le=4)


class Reveal(Paper):
    index: int = Field(strict=True, ge=0, le=59)
    expected_revision: int = Field(strict=True, ge=0)


class Activity(Paper):
    session_id: str = Field(min_length=1, max_length=100)
    active: bool = Field(strict=True)


class SaveDraft(Paper):
    expected_revision: int = Field(strict=True, ge=0)
    # Small batches stay inside the existing 16 KiB public request boundary.
    answers: list[Draft] = Field(min_length=1, max_length=2)


class SubmitPaper(Paper):
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(strict=True, ge=0)
    index: int | None = Field(default=None, strict=True, ge=0, le=59)


class Preference(Model):
    mode: str | None = Field(default=None, pattern=r'^(paper|question)$')
    timeout_action: str | None = Field(default=None, pattern=r'^(submit|mark)$')


def register_paper_routes(app, training, catalog, dependencies, access_store, subjects,
                          service_for, admission_lock, limits, schedule, outstanding_count, clock=None):
    app.state.review_paper_types = load_paper_types()

    def context(request, feature=None):
        token = request.headers.get('x-learning-identity')
        access = access_store.resolve(token)
        subject = request.query_params.get('subject_id')
        subjects.authorize_subject(access, subject)
        if feature and (feature not in access['features'] or not feature_eligible(access, feature)):
            raise DomainError('feature_forbidden', '当前账号没有使用此功能的权限。', 403)
        runtime = service_for(access['learner_id'], session_scope=token).runtime if access.get('is_admin') else None
        backend = TrainingService(training, app.state.settings.data_dir, access['learner_id'],
                                  app.state.service.judge, runtime=runtime, clock=clock)
        papers = PaperService(backend, catalog, dependencies, access, app.state.review_paper_types,
                              lambda tid: service_for(access['learner_id'], tid, token),
                              refresh_access=lambda: access_store.resolve(token))
        return papers, subject

    def require_subject(subject):
        if not subject:
            raise DomainError('subject_required', '请先选择学科。', 422)

    def admit(request, backend, data, subject):
        with admission_lock:
            size = backend.submission_size(data)
            with closing(backend._connect()) as con:
                outstanding = outstanding_count(con)
            if size and outstanding >= limits.max_pending:
                raise PublicLimitError('judge_capacity', '当前判题队列已满，请稍后重试；本次未计入作答。', 2)
            key = 'submit:' + backend.learner_id
            if size and request.state.public_transport:
                limits.check(key, limits.submissions_per_minute)
            if size:
                with limits.daily.reserve(backend.learner_id, backend.access['role'], count=size):
                    submission_id = backend.submit(data, subject)
                if request.state.public_transport:
                    limits.record(key)
            else:
                submission_id = backend.submit(data, subject)
            schedule(backend, submission_id)

    def enforce_timeout(request, backend, paper_id, subject):
        if 'submit_answer' not in backend.access['features']:
            return None
        data = backend.timeout_submission(paper_id, subject)
        if data:
            try:
                admit(request, backend, data, subject)
            except PublicLimitError as error:
                # The deadline still locks edits. Capacity/quota never turns into a wrong answer.
                return error.message
            except DomainError as error:
                if error.code not in ('stale_revision', 'request_conflict'):
                    raise
        return None

    def current_state(request, backend, paper_id, subject):
        pending = enforce_timeout(request, backend, paper_id, subject)
        state = backend.state(paper_id, subject)
        if pending:
            state['timeout_pending'] = pending
        return state

    @app.get('/api/review-papers/inbox')
    def inbox(request: Request):
        backend, subject = context(request, 'learn')
        require_subject(subject)
        if request.query_params.get('materials') == '1':
            # The existing administrator preview uses the same source projection.
            # material_inventory enforces administrator access before any data is returned.
            entries = [{'id': 'atomic:' + row['topic_id'] + ':' + row['id'], 'kind': 'single',
                        'title': row['title'], 'count': 1, 'material': True,
                        'topic_id': row['topic_id'], 'card_id': row['id'],
                        'source': {'kind': 'atomic', 'container': row['topic_id']}}
                       for row in material_inventory(catalog, subjects, backend.access, subject)
                       if row['kind'] == 'atomic_card']
            allowed = {topic['id'] for topic in backend.access['topics']}
            banks = [bank for bank in training.banks.values()
                     if bank['subject_id'] == subject and permission_id(bank['id']) in allowed]
            entries.extend({'id': 'training:' + row['bank_id'] + ':' + row['question_id'],
                            'kind': 'single', 'title': row['title'], 'count': 1, 'material': True,
                            'bank_id': row['bank_id'], 'group_id': row['group_id'], 'question_id': row['question_id'],
                            'source': {'kind': 'training', 'container': row['bank_id'], 'group_id': row['group_id']}}
                           for row in backend.training.reviews(banks, all_materials=True)['items'])
            return {'all_materials': True, 'items': [], 'active': [], 'due_count': len(entries),
                    'trees': source_trees(catalog, training, entries), 'next_due_at': None}
        for paper_id in backend.timed_papers(subject):
            try:
                enforce_timeout(request, backend, paper_id, subject)
            except DomainError as error:
                # A revoked source must not hide other authorized review entries.
                if error.code != 'feature_forbidden':
                    raise
        return backend.inbox(subject)

    @app.post('/api/review-papers/open')
    def open_paper(payload: OpenPaper, request: Request):
        backend, subject = context(request, 'learn')
        require_subject(subject)
        paper_id = backend.open(payload.candidate_id, payload.request_id, subject)
        return current_state(request, backend, paper_id, subject)

    @app.get('/api/review-papers/state')
    def state(request: Request, paper_id: str):
        backend, subject = context(request, 'learn')
        return current_state(request, backend, paper_id, subject)

    @app.post('/api/review-papers/draft')
    def draft(payload: SaveDraft, request: Request):
        backend, subject = context(request, 'submit_answer')
        if backend.timeout_submission(payload.paper_id, subject):
            return current_state(request, backend, payload.paper_id, subject)
        backend.save(payload.paper_id, payload.expected_revision, [a.model_dump() for a in payload.answers], subject)
        return current_state(request, backend, payload.paper_id, subject)

    @app.post('/api/review-papers/activity')
    def activity(payload: Activity, request: Request):
        backend, subject = context(request, 'learn')
        backend.activity(payload.paper_id, payload.session_id, payload.active, subject)
        return current_state(request, backend, payload.paper_id, subject)

    @app.post('/api/review-papers/reveal')
    def reveal(payload: Reveal, request: Request):
        backend, subject = context(request, 'submit_answer')
        backend.reveal(payload.paper_id, payload.index, payload.expected_revision, subject)
        return current_state(request, backend, payload.paper_id, subject)

    @app.post('/api/review-papers/submit')
    def submit(payload: SubmitPaper, request: Request):
        backend, subject = context(request, 'submit_answer')
        data = payload.model_dump(exclude_none=True)
        admit(request, backend, data, subject)
        return current_state(request, backend, payload.paper_id, subject)

    @app.post('/api/review-papers/cancel')
    def cancel(payload: Paper, request: Request):
        backend, subject = context(request, 'learn')
        if backend.timeout_submission(payload.paper_id, subject):
            return current_state(request, backend, payload.paper_id, subject)
        backend.cancel(payload.paper_id, subject)
        return backend.state(payload.paper_id, subject)

    @app.get('/api/review-papers/preferences')
    def preferences(request: Request):
        backend, _ = context(request)
        return backend.preferences()

    @app.post('/api/review-papers/preferences')
    def update_preferences(payload: Preference, request: Request):
        backend, _ = context(request)
        return backend.preferences(payload.mode, payload.timeout_action)
