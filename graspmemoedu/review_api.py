"""Permission-aware website adapter for the existing review state machine."""
from contextlib import closing
import re

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .service import DomainError
from .public_limits import PublicLimitError
from .question_types import public_interaction, display_answer, demo_answer


def review_route_feature(path, method):
    routes = ((r'/api/review/begin', 'POST', 'learn'),
              (r'/api/review/sessions/[^/]+', 'GET', 'learn'),
              (r'/api/review/sessions/[^/]+/submit', 'POST', 'submit_answer'),
              (r'/api/review/sessions/[^/]+/continue', 'POST', 'learn'),
              (r'/api/review/sessions/[^/]+/pause', 'POST', 'pause_topic'),
              (r'/api/review/sessions/[^/]+/demo-answer', 'GET', 'submit_answer'),
              (r'/api/review/submissions/[^/]+', 'GET', 'submit_answer'))
    return next((feature for pattern, verb, feature in routes
                 if method in (verb, 'OPTIONS') and re.fullmatch(pattern, path)), None)


class ReviewBegin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    module_id: str = Field(min_length=1, max_length=256)


class ReviewPause(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)


class ReviewAction(ReviewPause):
    expected_revision: int = Field(ge=0, strict=True)
    question_id: str = Field(min_length=1, max_length=256)


class ReviewAnswer(ReviewAction):
    answer: str = Field(min_length=1, max_length=2000)
    elapsed_ms: int | None = Field(default=None, strict=True, ge=0, le=86400000)


def register_review_routes(app, identity, admission_lock, limits, schedule, outstanding_count, dependency_policy=None, references_for=None):
    def public_state(state, access, learner):
        if 'review_history' not in access['features']:
            with closing(learner._connect()) as con:
                latest = con.execute('SELECT id FROM review_sessions WHERE learner=? AND topic=? ORDER BY rowid DESC LIMIT 1',
                                     (learner.learner_id, learner.topic_id)).fetchone()
            if not latest or latest['id'] != state['session_id']:
                raise DomainError('feature_forbidden', '当前账号没有使用此功能的权限。', 403)
            state['answers'] = [answer for answer in state['answers'] if answer['question_id'] == state['practice']['id']]
        module = learner.review_backend.modules[state['module_id']]
        with closing(learner._connect()) as con:
            reasons = {row['question']: row['reason'] for row in con.execute(
                "SELECT question,reason FROM review_submissions WHERE session=? AND learner=? AND topic=? AND status='judged'",
                (state['session_id'], learner.learner_id, learner.topic_id))}
        questions = {answer['question_id']: learner.review_backend.question_for_session(state, answer['question_id'])
                     for answer in state['answers']}
        state['answers'] = [{**answer, 'title': questions[answer['question_id']]['title'],
                             'interaction': public_interaction(questions[answer['question_id']]),
                             'content_version': state.get('question_bank_revision') or state['course_version'],
                             'answer_display': display_answer(questions[answer['question_id']], answer['answer']),
                             'html': questions[answer['question_id']]['html'],
                             'explanation_html': questions[answer['question_id']]['explanation_html'],
                             'feedback': {'correct': answer['correct'], 'reason': reasons.get(answer['question_id'])}}
                            for answer in state['answers']]
        state.update(topic_title=learner.course['title'], module_title=module.get('title', state['module_id']),
                     math_css=learner.course.get('math_css', '') + learner.question_bank.css([state.get('question_bank_revision')]), access=access)
        action_features = {'submit': 'submit_answer', 'continue': 'learn'}
        state['dependency_ready'] = dependency_policy is None or dependency_policy.is_ready(access, learner.topic_id)
        state['actions'] = [a for a in state['actions'] if state['dependency_ready'] and action_features.get(a) in access['features']]
        if references_for and learner.course.get('topic_format_version') == 2:
            state['content_references'] = references_for(access, learner)
        return state

    def public_submission(result, access, learner):
        if 'review_history' not in access['features']:
            with closing(learner._connect()) as con:
                row = con.execute('SELECT question FROM review_submissions WHERE id=? AND learner=? AND topic=?',
                                  (result['submission_id'], learner.learner_id, learner.topic_id)).fetchone()
            if not row or row['question'] != result['state']['practice']['id']:
                raise DomainError('feature_forbidden', '当前账号没有使用此功能的权限。', 403)
        result['state'] = public_state(result['state'], access, learner)
        return result

    @app.post('/api/review/begin')
    def begin(payload: ReviewBegin, request: Request):
        access, learner = identity(request, 'learn')
        return public_state(learner.review_backend.begin(payload.module_id, request_id=payload.request_id), access, learner)

    @app.get('/api/review/sessions/{session_id}')
    def state(session_id: str, request: Request):
        access, learner = identity(request, 'learn')
        return public_state(learner.review_backend.state(session_id), access, learner)

    @app.post('/api/review/sessions/{session_id}/continue')
    def advance(session_id: str, payload: ReviewAction, request: Request):
        access, learner = identity(request, 'learn')
        return public_state(learner.review_backend.advance(session_id, payload.model_dump()), access, learner)

    @app.post('/api/review/sessions/{session_id}/pause')
    def pause(session_id: str, payload: ReviewPause, request: Request):
        access, learner = identity(request, 'pause_topic')
        return public_state(learner.review_backend.pause(session_id, request_id=payload.request_id), access, learner)

    @app.get('/api/review/sessions/{session_id}/demo-answer')
    def demo(session_id: str, request: Request):
        access, learner = identity(request, 'submit_answer')
        if access['role'] != 'guest':
            raise DomainError('feature_forbidden', '体验专用浏览版仅供游客使用。', 403)
        state = learner.review_backend.state(session_id)
        if state['status'] != 'in_progress' or state['pending_submission_id'] or 'submit' not in state['actions']:
            raise DomainError('demo_answer_unavailable', '当前页面没有可作答的题目。', 409)
        return {'session_id': session_id, 'question_id': state['practice']['id'],
                'answer': demo_answer(learner.review_backend.question_for_session(state, state['practice']['id']))}

    @app.post('/api/review/sessions/{session_id}/submit')
    def submit(session_id: str, payload: ReviewAnswer, request: Request):
        access, learner = identity(request, 'submit_answer')
        data = payload.model_dump(exclude_none=True)
        backend = learner.review_backend
        with admission_lock:
            with closing(learner._connect()) as con:
                old = con.execute('SELECT * FROM review_submissions WHERE id=?', (data['request_id'],)).fetchone()
                outstanding = outstanding_count(con)
            fingerprint = learner._fingerprint('review_submit', {**data, 'session_id': session_id})
            replay_or_conflict = old and (old['status'] != 'error' or
                (old['learner'], old['topic'], old['session'], old['fingerprint']) !=
                (learner.learner_id, learner.topic_id, session_id, fingerprint))
            if replay_or_conflict:
                result = backend.submit(session_id, data)
            else:
                if outstanding >= limits.max_pending:
                    raise PublicLimitError('judge_capacity', '当前判题队列已满，请稍后重试；本次未计入作答。', 2)
                quota_key = 'submit:' + learner.learner_id
                if request.state.public_transport:
                    limits.check(quota_key, limits.submissions_per_minute)
                with limits.daily.reserve(learner.learner_id, access['role']):
                    result = backend.submit(session_id, data)
                if request.state.public_transport:
                    limits.record(quota_key)
            if result['status'] == 'pending':
                schedule(learner, result['submission_id'], review=True)
        return public_submission(result, access, learner)

    @app.get('/api/review/submissions/{submission_id}')
    def submission(submission_id: str, request: Request):
        access, learner = identity(request, 'submit_answer')
        return public_submission(learner.review_backend.submission(submission_id), access, learner)
