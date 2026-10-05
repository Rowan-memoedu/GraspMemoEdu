"""Optional limits for content blocks; callers own activity and submission policy."""
from datetime import timedelta
from math import ceil

from .fsrs_engine import utc_time


def start_limit(minutes, now, action='submit'):
    if minutes is None:
        return None
    if type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValueError('Time limit must be an integer between 1 and 1440 minutes')
    if action not in ('submit', 'mark'):
        raise ValueError('Unknown timeout action')
    started = utc_time(now)
    return {'limit_minutes': minutes, 'started_at': started.isoformat(),
            'deadline_at': (started + timedelta(minutes=minutes)).isoformat(),
            'timeout_action': action}


def limit_state(limit, now, *, can_mark=False, finished_at=None):
    if not limit:
        return None
    current = utc_time(now)
    reference = utc_time(finished_at) if finished_at else current
    remaining = remaining_time(limit, reference) if limit.get('clock') == 'active' else (utc_time(limit['deadline_at']) - reference).total_seconds()
    action = limit['timeout_action'] if can_mark else 'submit'
    paused = limit.get('paused', False) or bool(limit.get('clock') == 'active' and limit.get('active_until') and utc_time(limit['active_until']) <= reference)
    return {**limit, 'paused': paused, 'timeout_action': action, 'server_now': current.isoformat(),
            'remaining_seconds': max(0, ceil(remaining)), 'timed_out': remaining <= 0,
            'finished_at': finished_at}


ACTIVITY_LEASE_SECONDS = 15


def active_limit(minutes, now, action='submit'):
    limit = start_limit(minutes, now, action)
    if limit:
        limit.update(clock='active', remaining_seconds=minutes * 60, active_since=None,
                     active_until=None, session_id=None, deadline_at=None, paused=True)
    return limit


def remaining_time(limit, now):
    elapsed = 0
    if limit.get('active_since'):
        end = min(utc_time(now), utc_time(limit['active_until']))
        elapsed = max(0, (end - utc_time(limit['active_since'])).total_seconds())
    return max(0, limit['remaining_seconds'] - elapsed)


def update_activity(limit, now, session_id, active):
    """Count the union of active intervals, never the time between visits."""
    current = utc_time(now)
    if limit.get('clock') != 'active':
        # Legacy drafts have no reliable exit record. Freeze their last known
        # allowance once; future absence must not inherit a wall-clock deadline.
        remaining = max(0, (utc_time(limit['deadline_at']) - current).total_seconds())
        limit.update(clock='active', remaining_seconds=remaining, active_since=None,
                     active_until=None, session_id=None, paused=True)
    owner = limit.get('session_id')
    if owner and owner != session_id and limit.get('active_until') and utc_time(limit['active_until']) > current:
        return False
    remaining = remaining_time(limit, current)
    limit['remaining_seconds'] = remaining
    if active and remaining > 0:
        limit.update(active_since=current.isoformat(),
                     active_until=(current + timedelta(seconds=ACTIVITY_LEASE_SECONDS)).isoformat(),
                     session_id=session_id, paused=False,
                     deadline_at=(current + timedelta(seconds=remaining)).isoformat())
    else:
        limit.update(active_since=None, active_until=None, session_id=None, paused=True)
        if remaining > 0:
            limit['deadline_at'] = None
    return True
