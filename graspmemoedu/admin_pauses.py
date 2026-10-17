"""Assigned administrators can inspect pauses and invoke existing exact-event unlocks."""
from contextlib import closing
import json

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .service import DomainError


PUBLIC_PATHS = {'/api/admin/pauses', '/api/admin/pauses/unlock', '/api/admin/pauses/skip-policy'}


class UnlockPause(BaseModel):
    model_config = ConfigDict(extra='forbid')
    learner_id: str = Field(min_length=1, max_length=128)
    topic_id: str = Field(min_length=1, max_length=128)
    pause_event_id: str = Field(min_length=1, max_length=128)
    module_id: str | None = Field(default=None, max_length=128)


class SkipPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    learner_id: str = Field(min_length=1, max_length=128)
    topic_id: str = Field(min_length=1, max_length=128)
    allowed: bool = Field(strict=True)
    expected_events: list[str] = Field(min_length=1, max_length=1000)


class AdminPauses:
    def __init__(self, catalog, access_store, subjects, service_for):
        self.catalog, self.access_store, self.subjects, self.service_for = catalog, access_store, subjects, service_for

    @staticmethod
    def require_admin(access):
        if not access.get('is_admin'):
            raise DomainError('feature_forbidden', '此页面仅供管理员使用。', 403)

    def _owned(self, con, admin_id, learner_id=None):
        scope = '''SELECT p.learner_id,COALESCE(NULLIF(p.admin_name,''),p.display_name) AS display_name,t.topic,t.version,t.body
            FROM access_principals p JOIN topics t ON t.learner=p.learner_id
            LEFT JOIN help_admin_assignments a ON a.learner_id=p.learner_id
            LEFT JOIN help_admin_config c ON c.id=1
            WHERE p.role='account' AND p.is_student=1 AND p.is_admin=0 AND p.enabled=1
            AND COALESCE(a.admin_id,c.admin_id)=?'''
        args = [admin_id]
        if learner_id is not None:
            scope += ' AND p.learner_id=?'; args.append(learner_id)
        return con.execute(scope + ' ORDER BY p.learner_id,t.topic', args).fetchall()

    @staticmethod
    def _reviews(con):
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_topic_pauses'").fetchone():
            return {}
        return {(r['learner'], r['topic']): dict(r) for r in con.execute('SELECT * FROM review_topic_pauses')}

    def inbox(self, access, limit=25, offset=0):
        self.require_admin(access)
        items = []
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            reviews = self._reviews(con)
            for row in self._owned(con, access['learner_id']):
                if not self.access_store.test_materials.topic_visible(row['topic'], access):
                    continue
                state = json.loads(row['body'])
                if row['topic'] in self.catalog.topics:
                    topic = self.catalog.topic(row['topic'])
                    course = self.catalog.course_context(row['topic'])
                    skipped = state.get('skipped_modules', {})
                    for module_id, record in skipped.items():
                        module = next((m for m in topic['modules'] if m['id'] == module_id), None)
                        items.append({'learner_id': row['learner_id'], 'display_name': row['display_name'],
                            'topic_id': row['topic'], 'topic_title': topic['title'], 'course_title': course['title'],
                            'subject_id': self.subjects.topic_subject(row['topic']), 'module_id': module_id,
                            'module_title': module.get('title', module_id) if module else module_id,
                            'pause_event_id': record['event_id'], 'reason': 'module_skipped',
                            'paused_at': record['created_at'], 'released': record['released'],
                            'downstream_allowed': bool(state.get('skip_downstream_allowed')),
                            'skip_events': [r['event_id'] for r in skipped.values()],
                            'version_current': row['version'] == topic['version']})
                pause = state.get('pause') or reviews.get((row['learner_id'], row['topic']))
                if not pause or row['topic'] not in self.catalog.topics:
                    continue
                topic = self.catalog.topic(row['topic'])
                course = self.catalog.course_context(row['topic'])
                event = con.execute('SELECT created_at FROM events WHERE id=?', (pause['event_id'],)).fetchone()
                module_id = pause.get('module_id')
                module = next((m for m in topic.get('modules', []) if m['id'] == module_id), None)
                items.append({'learner_id': row['learner_id'], 'display_name': row['display_name'],
                    'topic_id': row['topic'], 'topic_title': topic['title'], 'course_title': course['title'],
                    'subject_id': self.subjects.topic_subject(row['topic']), 'module_id': module_id,
                    'module_title': module.get('title', module_id) if module else module_id,
                    'pause_event_id': pause['event_id'], 'reason': pause['reason'],
                    'paused_at': pause.get('created_at') or (event['created_at'] if event else None),
                    'review_only': not bool(state.get('pause')),
                    'version_current': row['version'] == topic['version']})
        items.sort(key=lambda item: (item['paused_at'] or '', item['pause_event_id']), reverse=True)
        return {'items': items[offset:offset + limit], 'total': len(items),
                'next_offset': offset + limit if offset + limit < len(items) else None}

    def unlock(self, access, values):
        self.require_admin(access)
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            row = next((r for r in self._owned(con, access['learner_id'], values['learner_id'])
                        if r['topic'] == values['topic_id']), None)
            if not row or not self.access_store.test_materials.topic_visible(row['topic'], access):
                raise DomainError('pause_not_found', '未找到可由您管理的暂停记录。', 404)
            topic = self.catalog.topic(values['topic_id'])
            if row['version'] != topic['version']:
                raise DomainError('course_version_mismatch', '该暂停对应旧版内容，请联系维护者处理。', 409)
            state = json.loads(row['body'])
            pause = state.get('pause')
            if pause and pause['event_id'] == values['pause_event_id'] and values['module_id'] != pause['module_id']:
                raise DomainError('unlock_mismatch', '暂停模块不匹配，请刷新后重试。', 409)
            reviews = self._reviews(con)
            review_pause = reviews.get((values['learner_id'], values['topic_id']))
            has_review_unlock = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_unlocks'").fetchone()
            review_replay = has_review_unlock and con.execute(
                'SELECT 1 FROM review_unlocks WHERE learner=? AND topic=? AND event_id=?',
                (values['learner_id'], values['topic_id'], values['pause_event_id'])).fetchone()
        learner = self.service_for(values['learner_id'], values['topic_id'])
        skip_record = state.get('skipped_modules', {}).get(values['module_id'])
        skip_event = skip_record and skip_record['event_id'] == values['pause_event_id']
        if not skip_event and topic.get('training_mode') != 'atomic_retrieval' and (review_pause or review_replay):
            learner.review_backend.unlock_topic(pause_event_id=values['pause_event_id'])
        else:
            learner.unlock(values['topic_id'], values['module_id'], values['pause_event_id'])
        return {'unlocked': True, 'pause_event_id': values['pause_event_id']}

    def skip_policy(self, access, values):
        self.require_admin(access)
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            row = next((r for r in self._owned(con, access['learner_id'], values['learner_id'])
                        if r['topic'] == values['topic_id']), None)
            if not row or not self.access_store.test_materials.topic_visible(row['topic'], access):
                raise DomainError('pause_not_found', '未找到可由您管理的跳过记录。', 404)
            if row['version'] != self.catalog.topic(row['topic'])['version']:
                raise DomainError('course_version_mismatch', '该跳过记录对应旧版内容。', 409)
        from .module_flow import skip_policy
        return skip_policy(self.service_for(values['learner_id'], values['topic_id']),
                           values['allowed'], values['expected_events'])


def register_admin_pause_routes(app, catalog, access_store, subjects, service_for):
    store = AdminPauses(catalog, access_store, subjects, service_for)
    app.state.admin_pauses = store

    @app.get('/api/admin/pauses')
    def inbox(request: Request, limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000)):
        return store.inbox(access_store.resolve(request.headers.get('x-learning-identity')), limit, offset)

    @app.post('/api/admin/pauses/unlock')
    def unlock(payload: UnlockPause, request: Request):
        return store.unlock(access_store.resolve(request.headers.get('x-learning-identity')), payload.model_dump())

    @app.post('/api/admin/pauses/skip-policy')
    def policy(payload: SkipPolicy, request: Request):
        return store.skip_policy(access_store.resolve(request.headers.get('x-learning-identity')), payload.model_dump())
