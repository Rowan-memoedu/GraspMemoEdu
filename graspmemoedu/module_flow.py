"""Module holds and timed automatic recovery. Stored with the Topic generation."""
from datetime import datetime, timedelta, timezone


def clock(service):
    backend = service.review_backend
    return backend._now() if backend else datetime.now(timezone.utc)


def student(service, con):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='access_principals'").fetchone():
        return True  # Trusted standalone integrations retain administrator unlocks.
    row = con.execute('SELECT is_student FROM access_principals WHERE learner_id=?', service.key[:1]).fetchone()
    return bool(row and row['is_student'])


def set_cooldown(service, state, event_id, kind):
    state['cooldown'] = {'event_id': event_id, 'kind': kind,
                         'resume_at': (clock(service) + timedelta(hours=6)).isoformat()}
    if state.get('pause'):
        state['pause']['resume_at'] = state['cooldown']['resume_at']


def resume_due(service, con):
    state = service._load(con)
    cooldown = state.get('cooldown')
    if not cooldown and not student(service, con):
        pause = state.get('pause')
        if not pause and service.review_backend is not None:
            row = service.review_backend._paused(con)
            pause = dict(row) if row else None
        if pause and pause.get('reason') in ('practice_error_limit', 'review_practice_error_limit'):
            event = con.execute('SELECT created_at FROM events WHERE id=? AND learner=? AND topic=?',
                                (pause['event_id'], *service.key)).fetchone()
            if event:
                cooldown = {'event_id': pause['event_id'], 'kind': 'lesson' if state.get('pause') else 'review',
                            'resume_at': (datetime.fromisoformat(event['created_at']) + timedelta(hours=6)).isoformat()}
                state['cooldown'] = cooldown
                if state.get('pause'): state['pause']['resume_at'] = cooldown['resume_at']
                service._save(con, state)
    if not cooldown or clock(service) < datetime.fromisoformat(cooldown['resume_at']):
        return False
    event_id = cooldown['event_id']
    pause = state.get('pause')
    if cooldown['kind'] == 'lesson' and pause and pause['event_id'] == event_id:
        previous = service._attempt(state)
        state.update(status='in_progress', pause=None, pending_submission_id=None)
        if previous['status'] == 'completed':
            service._next_module(state)
        else:
            service._new_attempt(state, previous['module_index'])
    if service.review_backend is not None:
        paused = service.review_backend._paused(con)
        if paused and paused['event_id'] == event_id:
            service.review_backend.on_topic_unlocked(con, event_id)
    state.pop('cooldown', None)
    service._event(con, state, 'cooldown_resumed', pause_event_id=event_id)
    service._save(con, state)
    return True


def skip(service, payload):
    from .service import DomainError, now
    from .topic_runtime import is_introduction
    with service._transaction() as con:
        if not student(service, con):
            raise DomainError('student_required', '该功能仅对学员开放', 403)
        if is_introduction(service.course) or service.course.get('training_mode') == 'atomic_retrieval':
            raise DomainError('skip_unavailable', '当前内容没有可跳过的初学模块。', 409)
        state = service._load(con)
        replay, fingerprint = service._request(con, 'skip', payload)
        if not replay:
            # Cancelling a pending judgement is safe: its old attempt can no longer apply.
            service._guard({**state, 'pending_submission_id': None}, payload)
            attempt = service._attempt(state)
            if attempt['status'] == 'completed':
                raise DomainError('module_completed', '已完成的模块不能跳过。')
            attempt.update(status='skipped', mastery=None)
            event_id = service._event(con, state, 'module_skipped')
            state.setdefault('skipped_modules', {})[attempt['module_id']] = {
                'event_id': event_id, 'module_id': attempt['module_id'], 'attempt_id': attempt['id'],
                'created_at': now(), 'released': False}
            state['skip_downstream_allowed'] = False
            state.setdefault('started_at', now())
            state['pending_submission_id'] = None
            service._next_module(state)
            service._record_action(con, 'skip', payload, fingerprint)
            service._save(con, state)
    service.flush_logs()
    return service._public(state)


def unlock_skip(service, con, state, module_id, event_id):
    from .service import DomainError
    record = state.get('skipped_modules', {}).get(module_id)
    if not record or record['event_id'] != event_id:
        raise DomainError('unlock_mismatch', '必须指定当前跳过模块及对应事件。')
    record['released'] = True
    if state['status'] == 'completed':
        state['status'] = 'in_progress'
        service._next_module(state)
    service._event(con, state, 'skipped_module_unlocked', module_id=module_id, skip_event_id=event_id)
    con.execute('INSERT INTO unlocks VALUES (?,?,?,?,?)', (event_id, *service.key, module_id, state['attempt_id']))


def skip_policy(service, allowed, expected_events):
    from .service import DomainError
    with service._transaction() as con:
        state = service._load(con)
        records = state.get('skipped_modules', {})
        if not records or sorted(expected_events) != sorted(r['event_id'] for r in records.values()):
            raise DomainError('stale_skip_policy', '跳过记录已更新，请刷新后重试。')
        state['skip_downstream_allowed'] = allowed
        service._event(con, state, 'skip_downstream_policy', allowed=allowed, skip_events=expected_events)
        service._save(con, state)
    return {'allowed': allowed}
