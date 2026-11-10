"""Short-lived, exercise-scoped delegation from the web identity to the IDE."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .access import credential_digest
from .academy import AcademyLearningService
from .academy_training import AcademyTraining
from .hyperskill_training import external_training
from .training_api import AttemptAction
from .training_content import permission_id
from .service import DomainError

CAPABILITY_PATHS = {'/api/academy/claim', '/api/academy/event'}
PUBLIC_PATHS = CAPABILITY_PATHS | {'/api/academy/launch', '/api/academy/finish',
                                 '/api/academy/training/launch', '/api/academy/training/finish'}


class Finish(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str
    expected_revision: int
    attempt_id: str
    step_id: str
    rating: int | None = None


class Event(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str
    event: dict | None = None


def register_academy_routes(app, settings, access_store, service_for, identity, public_state, Action, dependencies, clock=None):
    now = clock or (lambda: datetime.now(timezone.utc))
    with closing(access_store._connect()) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS academy_capabilities (
            digest TEXT PRIMARY KEY, identity_digest TEXT NOT NULL, preview_scope TEXT NOT NULL,
            learner TEXT NOT NULL, topic TEXT NOT NULL, version TEXT NOT NULL,
            step TEXT NOT NULL, round TEXT NOT NULL, expires TEXT NOT NULL, claimed INTEGER NOT NULL DEFAULT 0)''')
        con.commit()

    def require_service(backend):
        if not isinstance(backend, AcademyLearningService):
            raise DomainError('not_academy', '本题未绑定 JetBrains Academy。', 422)

    def capability(request, *, claim=False):
        auth = request.headers.get('authorization', '')
        if not auth.startswith('Bearer ') or not 40 <= len(auth) <= 150:
            raise DomainError('invalid_academy_ticket', 'IDEA 连接授权无效，请从网页重新打开。', 401)
        digest = hashlib.sha256(auth[7:].encode()).hexdigest()
        with closing(access_store._connect()) as con:
            row = con.execute('SELECT * FROM academy_capabilities WHERE digest=?', (digest,)).fetchone()
        if not row or row['expires'] < now().isoformat() or not claim and not row['claimed']:
            raise DomainError('expired_academy_ticket', 'IDEA 连接授权已到期，请从网页重新打开。', 401)
        access = access_store.resolve_digest(row['identity_digest'])
        access_store.authorize(access, row['topic'], 'submit_answer')
        if row['topic'].startswith('training.'):
            backend = external_training(app.state.training_backend_for(access, row['preview_scope']), row['step'])
            if (access['learner_id'] != row['learner'] or backend.version != row['version']
                    or permission_id(backend.bank_id) != row['topic']):
                raise DomainError('stale_academy_ticket', '题库已更新，请从网页重新打开。', 409)
            with closing(backend.training._connect()) as con:
                attempt = backend.training._attempt(con, row['step'])
                backend.training.require_group(backend.bank_id, attempt['group_id'], con=con)
            return row, backend
        if row['topic'] in app.state.training_catalog.topic_redirects:
            raise DomainError('stale_academy_ticket', '本题已移入题库，请从题库重新打开。', 409)
        dependencies.require(access, row['topic'])
        backend = service_for(access['learner_id'], row['topic'], scope_digest=row['preview_scope'])
        require_service(backend)
        if access['learner_id'] != row['learner'] or backend.course['version'] != row['version']:
            raise DomainError('stale_academy_ticket', '课程已更新，请从网页重新打开。', 409)
        return row, backend

    def grant(request, access, container, run):
        token = secrets.token_urlsafe(32)
        identity_token = request.headers.get('x-learning-identity')
        with closing(access_store._connect()) as con:
            con.execute('DELETE FROM academy_capabilities WHERE expires<?', (now().isoformat(),))
            con.execute('INSERT INTO academy_capabilities VALUES (?,?,?,?,?,?,?,?,?,0)',
                        (hashlib.sha256(token.encode()).hexdigest(), credential_digest('identity', identity_token),
                         credential_digest('preview', identity_token), access['learner_id'], container,
                         run['version'], run['step_id'], run['round_id'], (now() + timedelta(minutes=10)).isoformat()))
            con.commit()
        return token

    @app.post('/api/academy/training/launch')
    def training_launch(payload: AttemptAction, request: Request):
        access, training = app.state.training_attempt_context(request, payload.attempt_id, 'submit_answer')
        backend = external_training(training, payload.attempt_id)
        token = grant(request, access, permission_id(backend.bank_id), backend.launch())
        return {**({'ticket': token} if backend.provider == 'hyperskill' and not getattr(backend, 'native_ide', False)
                   else {'url': 'graspmemoedu://academy/open?ticket=' + token}),
                'state': app.state.training_public_state(request, access, training, payload.attempt_id)}

    class TrainingFinish(AttemptAction):
        rating: int | None = Field(default=None, strict=True, ge=1, le=4)

    @app.post('/api/academy/training/finish')
    def training_finish(payload: TrainingFinish, request: Request):
        access, training = app.state.training_attempt_context(request, payload.attempt_id, 'submit_answer')
        external_training(training, payload.attempt_id).finish(payload.rating)
        return app.state.training_public_state(request, access, training, payload.attempt_id)

    @app.post('/api/academy/launch')
    def launch(payload: Action, request: Request):
        access, backend = identity(request, 'submit_answer')
        require_service(backend)
        dependencies.require(access, backend.topic_id)
        run = backend.launch(payload.model_dump())
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        identity_token = request.headers.get('x-learning-identity')
        with closing(access_store._connect()) as con:
            con.execute('DELETE FROM academy_capabilities WHERE expires<?', (now().isoformat(),))
            con.execute('INSERT INTO academy_capabilities VALUES (?,?,?,?,?,?,?,?,?,0)',
                        (digest, credential_digest('identity', identity_token), credential_digest('preview', identity_token),
                         access['learner_id'], backend.topic_id, run['version'], run['step_id'], run['round_id'],
                         (now() + timedelta(minutes=10)).isoformat()))
            con.commit()
        result = {'state': public_state(backend.state(), access, backend)}
        if backend.course.get('source_adapter') == 'hyperskill':
            result['ticket'] = token
        else:
            result['url'] = 'graspmemoedu://academy/open?ticket=' + token
        return result

    @app.post('/api/academy/claim')
    def claim(request: Request):
        row, backend = capability(request, claim=True)
        if isinstance(backend, AcademyTraining):
            binding = backend.claim(row['round'])
        else:
            with closing(backend._connect()) as con:
                state = backend._load(con)
                run = backend._run(state, row['round'], row['step'])
                if state['phase'] != 'answer':
                    raise DomainError('academy_round_closed', '此轮已经完成，请回到网页继续。', 409)
            binding = {**backend.binding(state['active']), 'version': backend.course['version'], 'round_id': row['round'],
                       'next_sequence': len(run['events']) + 1, 'mode': state['mode'], 'reset_policy': 'archive_then_template'}
        with closing(access_store._connect()) as con:
            con.execute('UPDATE academy_capabilities SET claimed=1,expires=? WHERE digest=? AND claimed=0',
                        ((now() + timedelta(days=7)).isoformat(), row['digest']))
            con.commit()
        return binding

    @app.post('/api/academy/event')
    def event(payload: Event, request: Request):
        row, backend = capability(request)
        if payload.kind == 'opened' and payload.event is None:
            return backend.opened(row['round'], row['step'])
        if payload.kind == 'check' and payload.event is not None:
            return backend.check_event(row['round'], row['step'], payload.event)
        raise DomainError('invalid_academy_event', '未知的 IDEA 回传类型。', 422)

    @app.post('/api/academy/finish')
    def finish(payload: Finish, request: Request):
        access, backend = identity(request, 'submit_answer')
        require_service(backend)
        dependencies.require(access, backend.topic_id)
        return public_state(backend.finish(payload.model_dump(exclude_none=True)), access, backend)
