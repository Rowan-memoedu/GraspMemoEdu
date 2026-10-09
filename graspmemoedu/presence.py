"""Foreground presence, independent of grading, learning state and scheduling."""
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .service import DomainError

DAY_ZONE = ZoneInfo('Asia/Shanghai')
MAX_GAP_SECONDS = 45


class PresencePulse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stream_id: str = Field(pattern=r'^[a-f0-9-]{32,36}$')
    sequence: int = Field(ge=1, le=2147483647)
    visible: bool
    topic_ids: list[str] = Field(default_factory=list, max_length=100)


class Presence:
    def __init__(self, access_store, catalog, clock=None):
        self.access, self.catalog = access_store, catalog
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with closing(self.access._connect()) as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS presence_config (id INTEGER PRIMARY KEY CHECK(id=1), started_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS presence_intervals (
                    id INTEGER PRIMARY KEY, learner TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS presence_intervals_learner ON presence_intervals(learner,end_at);
                CREATE TABLE IF NOT EXISTS presence_sessions (
                    learner TEXT NOT NULL, stream TEXT NOT NULL, sequence INTEGER NOT NULL,
                    last_at TEXT NOT NULL, active INTEGER NOT NULL, interval_id INTEGER,
                    PRIMARY KEY(learner,stream));
                CREATE TABLE IF NOT EXISTS presence_visits (
                    learner TEXT NOT NULL, day TEXT NOT NULL, topic TEXT NOT NULL,
                    first_at TEXT NOT NULL, last_at TEXT NOT NULL, PRIMARY KEY(learner,day,topic));
            ''')
            con.execute('INSERT OR IGNORE INTO presence_config VALUES(1,?)', (self.clock().isoformat(),))
            con.commit()

    def pulse(self, access, payload):
        if access.get('role') != 'account' or not access.get('is_student') or access.get('is_admin'):
            raise DomainError('feature_forbidden', '访问计时仅适用于学员。', 403)
        topics = set(payload.topic_ids)
        for topic in topics:
            self.catalog.topic(topic)
            self.access.authorize(access, topic)
        now = self.clock()
        at, learner = now.isoformat(), access['learner_id']
        with self.access._transaction() as con:
            previous = con.execute('SELECT * FROM presence_sessions WHERE learner=? AND stream=?',
                                   (learner, payload.stream_id)).fetchone()
            if previous and previous['sequence'] >= payload.sequence:
                return {'recorded': False}
            interval = None
            gap = (now - datetime.fromisoformat(previous['last_at'])).total_seconds() if previous else None
            if previous and previous['active'] and 0 <= gap <= MAX_GAP_SECONDS:
                interval = previous['interval_id']
                if interval:
                    con.execute('UPDATE presence_intervals SET end_at=? WHERE id=? AND learner=?', (at, interval, learner))
                elif gap > 0:
                    interval = con.execute('INSERT INTO presence_intervals(learner,start_at,end_at) VALUES(?,?,?)',
                                           (learner, previous['last_at'], at)).lastrowid
            con.execute('INSERT INTO presence_sessions VALUES(?,?,?,?,?,?) ON CONFLICT(learner,stream) DO UPDATE SET '
                        'sequence=excluded.sequence,last_at=excluded.last_at,active=excluded.active,interval_id=excluded.interval_id',
                        (learner, payload.stream_id, payload.sequence, at, int(payload.visible), interval if payload.visible else None))
            if payload.visible:
                day = now.astimezone(DAY_ZONE).date().isoformat()
                for topic in topics:
                    con.execute('INSERT INTO presence_visits VALUES(?,?,?,?,?) ON CONFLICT(learner,day,topic) '
                                'DO UPDATE SET last_at=excluded.last_at', (learner, day, topic, at, at))
        return {'recorded': True}


def today_presence(con, learner, current, access, context, test_materials):
    """Read one snapshot, clip at midnight and union overlapping tabs/devices."""
    day = current.astimezone(DAY_ZONE).date()
    start = datetime.combine(day, time(), DAY_ZONE).astimezone(timezone.utc)
    end = min(current, start + timedelta(days=1))
    intervals = []
    for row in con.execute('SELECT start_at,end_at FROM presence_intervals WHERE learner=? AND end_at>=? AND start_at<?',
                           (learner, start.isoformat(), end.isoformat())):
        left, right = max(start, datetime.fromisoformat(row['start_at'])), min(end, datetime.fromisoformat(row['end_at']))
        if right > left:
            intervals.append((left, right))
    merged = []
    for left, right in sorted(intervals):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    visits = [context(row['topic']) | {'first_at': row['first_at'], 'last_at': row['last_at']}
              for row in con.execute('SELECT * FROM presence_visits WHERE learner=? AND day=? ORDER BY last_at DESC,topic',
                                     (learner, day.isoformat())) if test_materials.topic_visible(row['topic'], access)]
    return {'date': day.isoformat(), 'timezone': 'Asia/Shanghai',
            'online_ms': round(sum((right - left).total_seconds() for left, right in merged) * 1000),
            'topics': visits, 'recording_since': con.execute('SELECT started_at FROM presence_config WHERE id=1').fetchone()[0]}


def register_presence_route(app, access_store, catalog, clock=None):
    app.state.presence = Presence(access_store, catalog, clock)

    @app.post('/api/presence')
    def pulse(payload: PresencePulse, request: Request):
        return app.state.presence.pulse(access_store.resolve(request.headers.get('x-learning-identity')), payload)
