"""Stable cross-content references to existing atomic memory, with no shadow state."""
from datetime import timedelta
import hashlib
import json
import math
import re

from fsrs import Card, Scheduler

from .atomic_content import memory_points
from .fsrs_engine import utc_time
from .review_cards import point_id, require_unreserved
from .service import DomainError
from .topic_structure import atomic_state

REFERENCE = re.compile(r'([A-Za-z0-9][A-Za-z0-9_.-]{0,127})/([A-Za-z0-9][A-Za-z0-9_.-]{0,127})\Z')
DEFAULT_UNLOCK = {'min_successful_reviews': 1, 'min_stability_days': 7, 'min_retrievability': .90}


def split_reference(value):
    match = REFERENCE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError('Point reference must be topic_id/point_id')
    return match.groups()


def validate_references(values):
    if not isinstance(values, list) or not values or len(values) > 2000:
        raise ValueError('A nonempty point reference list is required')
    for value in values:
        split_reference(value)
    if len(set(values)) != len(values):
        raise ValueError('Duplicate point reference')


def validate_unlock(policy):
    if not isinstance(policy, dict) or set(policy) - {'point_ids', *DEFAULT_UNLOCK}:
        raise ValueError('Invalid mastery unlock policy')
    validate_references(policy.get('point_ids'))
    for key, default in DEFAULT_UNLOCK.items():
        value = policy.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not value > 0:
            raise ValueError('Invalid mastery threshold')
    if (type(policy.get('min_successful_reviews', 1)) is not int
            or policy.get('min_retrievability', .90) > 1):
        raise ValueError('Invalid mastery threshold')


def fingerprint(memory):
    return hashlib.sha256(json.dumps(memory, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def resolve(catalog, reference):
    tid, local = split_reference(reference)
    if tid not in catalog.topics:
        raise ValueError('Unknown point Topic: ' + tid)
    topic = catalog.topic(tid)
    point = next((p for p in memory_points(topic) if p['id'] == local), None)
    if point is None:
        raise ValueError('Unknown atomic memory point: ' + reference)
    return topic, point


def snapshot(con, catalog, learner, references, access=None):
    """Read only: absent/stale learning records never create or migrate progress."""
    allowed = {t['id'] for t in access['topics']} if access is not None else None
    states, rows = {}, []
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for reference in references:
        topic, point = resolve(catalog, reference)
        tid, local = split_reference(reference)
        if tid not in states:
            row = con.execute('SELECT version,body FROM topics WHERE learner=? AND topic=?', (learner, tid)).fetchone() if 'topics' in tables else None
            body = json.loads(row['body']) if row and row['version'] == topic['version'] else {}
            state = atomic_state(body)
            states[tid] = state
        state = states[tid]
        memory = state.get('points', {}).get(local, {})
        permitted = allowed is None or tid in allowed
        rows.append({'reference': reference, 'topic_id': tid, 'local_id': local, 'version': topic['version'],
            'point_id': point_id('atomic', tid, topic['version'], local), 'text': point['text'] if permitted else '',
            'memory': memory, 'fingerprint': fingerprint(memory), 'state': state,
            'learned': bool(permitted and memory.get('mastered') and memory.get('first_passed_at')),
            'available': bool(permitted and not state.get('pause'))})
    return rows


def eligibility(rows, current, policy=None):
    current = utc_time(current)
    thresholds = {**DEFAULT_UNLOCK, **(policy or {})}
    scheduler = Scheduler()
    result = []
    for row in rows:
        memory, state = row['memory'], row['state']
        reason = None if row['learned'] and row['available'] else 'not_learned'
        stability, retrievability, successful = None, None, 0
        if policy is not None and reason is None:
            if memory.get('card_json'):
                card = Card.from_json(memory['card_json'])
                stability = card.stability
                retrievability = scheduler.get_card_retrievability(card, current)
            first_due = utc_time(memory['first_passed_at']) + timedelta(seconds=state['policy']['first_review_seconds'])
            successful = sum(h.get('mode') == 'review' and (h.get('ratings', {}).get(row['local_id']) or 0) >= 3
                and utc_time(h['answered_at']) >= first_due for h in state.get('history', []) if h.get('answered_at'))
            if successful < thresholds['min_successful_reviews']:
                reason = 'needs_spaced_review'
            elif (memory.get('last_rating') or 0) < 3:
                reason = 'last_review_failed'
            elif stability is None or stability < thresholds['min_stability_days']:
                reason = 'stability_low'
            elif retrievability is None or retrievability < thresholds['min_retrievability']:
                reason = 'retrievability_low'
        result.append({'id': row['reference'], 'topic_id': row['topic_id'], 'text': row['text'],
            'ready': reason is None, 'reason': reason, 'stability_days': stability,
            'retrievability': retrievability, 'successful_reviews': successful})
    return {'unlocked': all(r['ready'] for r in result), 'points': result,
            'missing_point_count': sum(not r['ready'] for r in result)}


def require_available(con, rows, learner, current, reservation_owner=None):
    for row in rows:
        if not row['learned'] or not row['available']:
            raise DomainError('prerequisites_not_ready', '请先学习题目关联的记忆点。', 409)
        state = row['state']
        if state.get('pending_submission_id') or state.get('active'):
            raise DomainError('linked_point_busy', '关联记忆点正在另一轮训练中，请先完成该轮。', 409)
        owned = reservation_owner and con.execute('SELECT 1 FROM review_reservations WHERE learner=? AND point=? AND paper=?',
                                                  (learner, row['point_id'], reservation_owner)).fetchone()
        if not owned:
            require_unreserved(con, learner, 'atomic', row['topic_id'], row['version'], row['local_id'], now=current)


def pinned(rows):
    return [{k: r[k] for k in ('reference', 'topic_id', 'local_id', 'version', 'point_id', 'fingerprint')} for r in rows]


def unchanged(rows, pin):
    return pinned(rows) == pin and all(r['available'] and r['learned'] and not r['state'].get('active')
        and not r['state'].get('pending_submission_id') for r in rows)
