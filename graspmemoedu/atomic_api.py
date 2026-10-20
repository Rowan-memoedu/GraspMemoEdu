"""Read-only review inbox; atomic answers use the existing protected submit API."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json

from fastapi import Request

from .atomic_content import GAP_SECONDS, is_atomic, point_chunks
from .service import DomainError
from .topic_structure import atomic_state, has_atomic, sectioned_training


def review_summary(topic, state, current=None):
    current = current or datetime.now(timezone.utc)
    state = atomic_state(state)
    if not state or (not sectioned_training(topic) and not state.get('completed_at')) or state.get('pause'):
        return {'due_count': 0, 'next_due_at': None, 'active': False}
    chunks = point_chunks(topic)
    dates = []
    for point_id, memory in state['points'].items():
        if not memory['mastered'] or not memory['due_at']:
            continue
        due = datetime.fromisoformat(memory['due_at'])
        last = state['chunk_last_seen'].get(chunks[point_id])
        if last:
            due = max(due, datetime.fromisoformat(last) + timedelta(seconds=GAP_SECONDS))
        dates.append(due)
    return {'due_count': sum(due <= current for due in dates),
            'next_due_at': min(dates).isoformat() if dates else None,
            'active': state['mode'] == 'review' and state['active'] is not None}


def register_atomic_routes(app, catalog, access_store, subjects, dependencies, *, clock=None):
    @app.get('/api/atomic/reviews')
    def reviews(request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subject_id = request.query_params.get('subject_id')
        subjects.authorize_subject(access, subject_id)
        if 'learn' not in access['features']:
            raise DomainError('feature_forbidden', '当前账号没有使用此功能的权限。', 403)
        allowed = {t['id'] for t in access['topics']}
        redirected = app.state.training_catalog.topic_redirects
        if access.get('is_admin'):
            from .materials import material_inventory
            return {'items': [entry for entry in material_inventory(catalog, subjects, access, subject_id)
                              if entry['kind'] == 'atomic_card' and entry['topic_id'] not in redirected],
                    'next_due_at': None, 'all_materials': True}
        items, next_dates = [], []
        with closing(access_store._connect()) as con:
            con.execute('BEGIN')
            for topic_id, topic in catalog.topics.items():
                if topic_id in redirected:
                    continue
                if not has_atomic(topic) or topic_id not in allowed or (subject_id and topic['subject_id'] != subject_id):
                    continue
                row = con.execute('SELECT version,body FROM topics WHERE learner=? AND topic=?', (access['learner_id'], topic_id)).fetchone()
                if not row or row['version'] != topic['version']:
                    continue
                state = json.loads(row['body'])
                summary = review_summary(topic, state, clock() if clock else None)
                if summary['next_due_at']:
                    next_dates.append(summary['next_due_at'])
                point_state = atomic_state(state)
                if summary['due_count'] or summary['active'] or point_state['pause']:
                    items.append({'topic_id': topic_id, 'title': topic['title'], 'subject_id': topic['subject_id'],
                                  'paused': bool(point_state['pause']), 'dependency_ready': dependencies.ready(con, access, topic_id), **summary})
        return {'items': items, 'next_due_at': min(next_dates, default=None)}
