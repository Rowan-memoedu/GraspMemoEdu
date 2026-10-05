"""Optional wall-clock limits for content blocks; callers own submission policy."""
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
    remaining = (utc_time(limit['deadline_at']) - reference).total_seconds()
    action = limit['timeout_action'] if can_mark else 'submit'
    return {**limit, 'timeout_action': action, 'server_now': current.isoformat(),
            'remaining_seconds': max(0, ceil(remaining)), 'timed_out': remaining <= 0,
            'finished_at': finished_at}
