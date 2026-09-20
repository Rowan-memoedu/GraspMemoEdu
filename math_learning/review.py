"""Opt-in, persistent Example review backend for trusted server-side callers.

Construct with an existing LearningService to attach its completion/pause hooks.
No HTTP routes, website startup, background notifications, or old-score import
are enabled by this module. All state shares the Lesson SQLite transaction.
"""

from contextlib import closing
from datetime import datetime, timezone
import json
import hashlib
import importlib.util
import math
import random
import uuid

from fsrs import Card, ReviewLog, Scheduler

from .fsrs_engine import FSRSEngine, utc_time
from .judging import judge_answer
from .practice import PracticePolicy, REVIEW_POLICY, apply_practice_result
from .service import DomainError, encode


class ReviewBackend:
    def __init__(self, learning, *, engine=None, policy=REVIEW_POLICY, clock=None, rng=None):
        self.learning = learning
        self.engine = engine or FSRSEngine()
        self.policy = policy
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rng = rng or random.SystemRandom()
        self.modules = {module['id']: module for module in learning.course['modules']}
        self.version = learning.course['version']
        if learning.review_backend is not None:
            raise ValueError('This LearningService already has a review backend')
        with closing(learning._connect()) as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS review_cards (
                    learner TEXT NOT NULL, topic TEXT NOT NULL, module TEXT NOT NULL,
                    version TEXT NOT NULL, card_json TEXT NOT NULL, due_at TEXT NOT NULL,
                    revision INTEGER NOT NULL, active_session TEXT,
                    PRIMARY KEY(learner, topic, module));
                CREATE INDEX IF NOT EXISTS review_due
                    ON review_cards(learner, topic, due_at) WHERE active_session IS NULL;
                CREATE TABLE IF NOT EXISTS review_ratings (
                    learner TEXT NOT NULL, topic TEXT NOT NULL, source TEXT NOT NULL,
                    source_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    module TEXT NOT NULL, version TEXT NOT NULL,
                    mastery INTEGER NOT NULL CHECK(mastery BETWEEN 1 AND 4),
                    completed_at TEXT NOT NULL, before_card_json TEXT,
                    after_card_json TEXT NOT NULL, review_log_json TEXT NOT NULL,
                    scheduler_json TEXT NOT NULL, algorithm TEXT NOT NULL,
                    policy_version TEXT, due_at TEXT NOT NULL,
                    PRIMARY KEY(learner, topic, source, source_id));
                CREATE TABLE IF NOT EXISTS review_sessions (
                    id TEXT PRIMARY KEY, learner TEXT NOT NULL, topic TEXT NOT NULL,
                    module TEXT NOT NULL, version TEXT NOT NULL,
                    status TEXT NOT NULL, body TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_review_per_topic
                    ON review_sessions(learner, topic) WHERE status='in_progress';
                CREATE TABLE IF NOT EXISTS review_actions (
                    learner TEXT NOT NULL, topic TEXT NOT NULL, id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, session TEXT NOT NULL,
                    PRIMARY KEY(learner, topic, id));
                CREATE TABLE IF NOT EXISTS review_submissions (
                    id TEXT PRIMARY KEY, learner TEXT NOT NULL, topic TEXT NOT NULL,
                    session TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                    correct INTEGER, reason TEXT, elapsed_ms INTEGER,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, run_token TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS one_live_review_answer
                    ON review_submissions(session, question)
                    WHERE status IN ('pending','running','judged');
                CREATE TABLE IF NOT EXISTS review_topic_pauses (
                    learner TEXT NOT NULL, topic TEXT NOT NULL, event_id TEXT NOT NULL,
                    reason TEXT NOT NULL, PRIMARY KEY(learner, topic));
                CREATE TABLE IF NOT EXISTS review_unlocks (
                    learner TEXT NOT NULL, topic TEXT NOT NULL, event_id TEXT NOT NULL,
                    PRIMARY KEY(learner, topic, event_id));
                CREATE TABLE IF NOT EXISTS review_settings (
                    learner TEXT NOT NULL, topic TEXT NOT NULL,
                    scheduler_json TEXT NOT NULL, revision INTEGER NOT NULL,
                    PRIMARY KEY(learner, topic));
                CREATE TABLE IF NOT EXISTS review_optimizations (
                    id TEXT PRIMARY KEY, learner TEXT NOT NULL, topic TEXT NOT NULL,
                    created_at TEXT NOT NULL, body TEXT NOT NULL);
            ''')
        with learning._transaction() as con:
            con.execute('INSERT OR IGNORE INTO review_settings VALUES (?,?,?,1)',
                        (*self.key, self.engine.scheduler.to_json()))
            self.engine.scheduler = Scheduler.from_json(self._settings_row(con)['scheduler_json'])
            if con.execute('SELECT 1 FROM review_cards WHERE learner=? AND topic=? AND version<>?',
                           (*self.key, self.version)).fetchone():
                raise DomainError('course_version_mismatch', '复习卡片的课程版本已变化，旧记录已保留。')
            lesson = learning._load(con)
            if lesson['status'] == 'paused':
                self.on_topic_paused(con, lesson['pause']['event_id'], lesson['pause']['reason'])
        learning.review_backend = self

    def _settings_row(self, con):
        return con.execute('SELECT * FROM review_settings WHERE learner=? AND topic=?', self.key).fetchone()

    def scheduler_settings(self):
        with closing(self.learning._connect()) as con:
            row = self._settings_row(con)
            return {**Scheduler.from_json(row['scheduler_json']).to_dict(),
                    'revision': row['revision'], 'algorithm': self.engine.algorithm}

    def configure_scheduler(self, *, desired_retention=None, parameters=None, reschedule_existing=False,
                            _optimization_id=None):
        """Persist settings for this learner/Topic; optional atomic rescheduling."""
        if type(reschedule_existing) is not bool:
            raise DomainError('invalid_scheduler_settings', '重排选项必须为布尔值。', 422)
        if desired_retention is not None and (type(desired_retention) not in (int, float)
                or not math.isfinite(desired_retention) or not 0 < desired_retention < 1):
            raise DomainError('invalid_retention', '目标保留度必须在 0 与 1 之间。', 422)
        if parameters is not None and (not isinstance(parameters, (tuple, list))
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in parameters)):
            raise DomainError('invalid_parameters', 'FSRS 参数必须为有效数值列表。', 422)
        with self.learning._transaction() as con:
            previous = self._settings_row(con)
            settings = Scheduler.from_json(previous['scheduler_json']).to_dict()
            if desired_retention is not None:
                settings['desired_retention'] = desired_retention
            if parameters is not None:
                settings['parameters'] = list(parameters)
            try:
                scheduler = Scheduler.from_dict(settings)
            except (ValueError, TypeError, IndexError, AssertionError) as exc:
                raise DomainError('invalid_parameters', 'FSRS 参数未通过调度器校验。', 422) from exc
            count = 0
            if reschedule_existing:
                if con.execute("SELECT 1 FROM review_sessions WHERE learner=? AND topic=? AND status='in_progress'", self.key).fetchone():
                    raise DomainError('review_in_progress', '请完成或暂停当前复习后再重算到期时间。')
                cards = con.execute('SELECT * FROM review_cards WHERE learner=? AND topic=? AND version=?',
                                    (*self.key, self.version)).fetchall()
                for card in cards:
                    logs = [ReviewLog.from_json(row[0]) for row in con.execute('''SELECT review_log_json
                        FROM review_ratings WHERE learner=? AND topic=? AND module=? AND version=?
                        ORDER BY completed_at,rowid''', (*self.key, card['module'], self.version))]
                    updated = scheduler.reschedule_card(Card.from_json(card['card_json']), logs)
                    con.execute('''UPDATE review_cards SET card_json=?,due_at=?,revision=revision+1
                        WHERE learner=? AND topic=? AND module=?''',
                                (updated.to_json(), updated.due.isoformat(), *self.key, card['module']))
                    count += 1
            con.execute('UPDATE review_settings SET scheduler_json=?,revision=revision+1 WHERE learner=? AND topic=?',
                        (scheduler.to_json(), *self.key))
            self._event(con, 'review_scheduler_configured', previous_scheduler_json=previous['scheduler_json'],
                        scheduler_json=scheduler.to_json(), rescheduled_cards=count, optimization_id=_optimization_id)
        self.learning.flush_logs()
        return {**self.scheduler_settings(), 'rescheduled_cards': count}

    def _optimization_snapshot(self, scope):
        if scope not in ('topic', 'learner'):
            raise DomainError('invalid_optimization_scope', '优化范围须为 topic 或 learner。', 422)
        with closing(self.learning._connect()) as con:
            rows = con.execute('''SELECT r.review_log_json,r.policy_version,r.topic
                FROM review_ratings r JOIN review_cards c ON
                c.learner=r.learner AND c.topic=r.topic AND c.module=r.module AND c.version=r.version
                WHERE r.learner=? ''' + ('AND r.topic=? ' if scope == 'topic' else '') +
                'ORDER BY r.completed_at,r.rowid', self.key if scope == 'topic' else (self.key[0],)).fetchall()
        logs = [ReviewLog.from_json(row['review_log_json']) for row in rows]
        last, per_card_count = {}, {}
        eligible = 0
        # py-fsrs 6.3.2 uses the first 64 logs/card and requires a batch of 512
        # reviews separated by >=24 hours; otherwise it silently returns defaults.
        for log in logs:
            per_card_count[log.card_id] = per_card_count.get(log.card_id, 0) + 1
            if per_card_count[log.card_id] <= 64:
                if log.card_id in last and (log.review_datetime - last[log.card_id]).days > 0:
                    eligible += 1
                last[log.card_id] = log.review_datetime
        summary = {'scope': scope, 'log_count': len(logs), 'cross_day_review_count': eligible,
                   'card_count': len(last), 'minimum_cross_day_reviews': 512, 'maximum_logs_per_card': 64,
                   'enough_data': eligible >= 512,
                   'can_optimize_retention': len(logs) >= 512 and all(log.review_duration is not None for log in logs),
                   'topic_ids': sorted({row['topic'] for row in rows})}
        return rows, logs, summary

    def optimization_status(self, *, scope='learner'):
        _, _, summary = self._optimization_snapshot(scope)
        return {**summary, 'runtime_installed': all(importlib.util.find_spec(name) is not None
                                                    for name in ('torch', 'numpy', 'pandas', 'tqdm'))}

    def optimize_parameters(self, *, include_optimal_retention=False, scope='learner'):
        """Run upstream optimization locally and persist a candidate, without applying it.

        Training reads this learner's logs (optionally limited to this Topic),
        outside any write transaction. The upstream minimum training batch is
        512 eligible reviews, limited to the first 64 logs/card. Retention search
        additionally needs 512 logs with measured duration.
        """
        if type(include_optimal_retention) is not bool:
            raise DomainError('invalid_optimization_request', '保留度优化选项必须为布尔值。', 422)
        rows, logs, summary = self._optimization_snapshot(scope)
        if include_optimal_retention and not summary['can_optimize_retention']:
            raise DomainError('insufficient_retention_data', '自动建议保留度需要至少 512 条均含作答用时的记录。', 422)
        if not summary['enough_data']:
            raise DomainError('insufficient_optimization_data',
                              f"有效跨日复习记录不足 512 条（当前 {summary['cross_day_review_count']} 条，每卡最多取前 64 次记录）；未进行优化。", 422)
        try:
            from fsrs import Optimizer
            optimizer = Optimizer(logs)
        except ImportError as exc:
            raise DomainError('optimizer_unavailable', '请在后端安装 requirements-optimizer.txt 中的本地优化依赖。', 503) from exc
        parameters = optimizer.compute_optimal_parameters()
        # Validate before preserving a candidate. Invalid output never changes settings.
        Scheduler(parameters=parameters)
        result = {'optimization_id': str(uuid.uuid4()), 'parameters': list(parameters),
                  'recommended_retention': optimizer.compute_optimal_retention(parameters) if include_optimal_retention else None,
                  **summary,
                  'policy_versions': sorted({row['policy_version'] for row in rows if row['policy_version']}),
                  'data_sha256': hashlib.sha256(encode([row['review_log_json'] for row in rows]).encode()).hexdigest(),
                  'algorithm': self.engine.algorithm, 'created_at': self._now().isoformat(), 'applied': False}
        with self.learning._transaction() as con:
            con.execute('INSERT INTO review_optimizations VALUES (?,?,?,?,?)',
                        (result['optimization_id'], *self.key, result['created_at'], encode(result)))
        return result

    def apply_optimization(self, optimization_id, *, reschedule_existing=False, use_recommended_retention=False):
        """Explicit administrator action; generating a candidate never auto-applies it."""
        if type(use_recommended_retention) is not bool:
            raise DomainError('invalid_optimization_request', '保留度应用选项必须为布尔值。', 422)
        with closing(self.learning._connect()) as con:
            row = con.execute('SELECT body FROM review_optimizations WHERE id=? AND learner=? AND topic=?',
                              (optimization_id, *self.key)).fetchone()
        if not row:
            raise DomainError('unknown_optimization', '未找到此优化候选。', 404)
        candidate = json.loads(row['body'])
        if use_recommended_retention and candidate['recommended_retention'] is None:
            raise DomainError('missing_retention_candidate', '此候选没有自动建议保留度。', 422)
        result = self.configure_scheduler(parameters=candidate['parameters'], reschedule_existing=reschedule_existing,
                                          desired_retention=candidate['recommended_retention'] if use_recommended_retention else None,
                                          _optimization_id=optimization_id)
        return {**result, 'optimization_id': optimization_id, 'applied': True}

    @property
    def key(self):
        return self.learning.key

    def _now(self):
        return utc_time(self.clock())

    @staticmethod
    def _request_id(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise DomainError('invalid_request', '缺少有效请求标识。', 422)
        return value

    def _module(self, module_id):
        if module_id not in self.modules:
            raise DomainError('unknown_module', '未找到此 Example 模块。', 404)
        return self.modules[module_id]

    def _event(self, con, event_type, **extra):
        event = {'event_id': str(uuid.uuid4()), 'time': self._now().isoformat(),
                 'type': event_type, 'learner_id': self.key[0], 'topic_id': self.key[1],
                 'course_version': self.version, **extra}
        con.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                    (event['event_id'], *self.key, event['time'], encode(event)))
        return event['event_id']

    def _card(self, con, module_id):
        row = con.execute('SELECT * FROM review_cards WHERE learner=? AND topic=? AND module=?',
                          (*self.key, module_id)).fetchone()
        if row and row['version'] != self.version:
            raise DomainError('course_version_mismatch', '复习内容版本不匹配，旧记录已保留。')
        return row

    def _paused(self, con):
        return con.execute('SELECT * FROM review_topic_pauses WHERE learner=? AND topic=?', self.key).fetchone()

    def _guard_topic(self, con):
        if self._paused(con) or self.learning._load(con)['status'] == 'paused':
            raise DomainError('topic_paused', '当前 Topic 已暂停，等待管理员解锁。')

    def _record_rating(self, con, *, module_id, mastery, source, source_id,
                       completed_at, policy_version=None, duration_ms=None):
        self._module(module_id)
        self._request_id(source_id)
        when = utc_time(completed_at)
        fingerprint = self.learning._fingerprint('review_rating', {
            'module_id': module_id, 'version': self.version, 'mastery': mastery,
            'completed_at': when.isoformat(), 'policy_version': policy_version,
            'duration_ms': duration_ms})
        previous = con.execute('''SELECT * FROM review_ratings
            WHERE learner=? AND topic=? AND source=? AND source_id=?''',
            (*self.key, source, source_id)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise DomainError('idempotency_conflict', '同一模块结果不能对应不同评分或时间。')
            return self._rating_public(previous)
        self._guard_topic(con)
        old = self._card(con, module_id)
        if old and old['active_session'] and (source != 'review' or old['active_session'] != source_id):
            raise DomainError('review_in_progress', '当前 Example 正在复习，不能覆盖调度状态。')
        self.engine.scheduler = Scheduler.from_json(self._settings_row(con)['scheduler_json'])
        scheduled = self.engine.schedule(old['card_json'] if old else None, mastery, when, duration_ms)
        con.execute('''INSERT INTO review_ratings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (*self.key, source, source_id, fingerprint, module_id, self.version, mastery,
                     when.isoformat(), old['card_json'] if old else None, scheduled['card_json'],
                     scheduled['review_log_json'], scheduled['scheduler_json'], scheduled['algorithm'],
                     policy_version, scheduled['due_at']))
        con.execute('''INSERT INTO review_cards VALUES (?,?,?,?,?,?,1,NULL)
            ON CONFLICT(learner,topic,module) DO UPDATE SET card_json=excluded.card_json,
            due_at=excluded.due_at, revision=review_cards.revision+1, active_session=NULL''',
                    (*self.key, module_id, self.version, scheduled['card_json'], scheduled['due_at']))
        self._event(con, 'review_scheduled', module_id=module_id, mastery=mastery,
                    source=source, source_id=source_id, due_at=scheduled['due_at'],
                    algorithm=scheduled['algorithm'], policy_version=policy_version)
        return {'module_id': module_id, 'mastery': mastery, 'completed_at': when.isoformat(),
                'due_at': scheduled['due_at'], 'source': source, 'source_id': source_id,
                'algorithm': scheduled['algorithm']}

    @staticmethod
    def _rating_public(row):
        return {key: row[column] for key, column in (
            ('module_id', 'module'), ('mastery', 'mastery'), ('completed_at', 'completed_at'),
            ('due_at', 'due_at'), ('source', 'source'), ('source_id', 'source_id'), ('algorithm', 'algorithm'))}

    def record_module_result(self, *, module_id, mastery, result_id, completed_at, elapsed_ms=None):
        """Trusted backend integration for an already finalized 1..4 module result.

        This is NOT a browser endpoint. Callers must supply a stable result ID
        and the original completion time. Pauses have no numeric result.
        """
        if elapsed_ms is not None and (type(elapsed_ms) is not int or not 0 <= elapsed_ms <= 86400000):
            raise DomainError('invalid_elapsed_time', '模块作答用时须为有效毫秒数。', 422)
        with self.learning._transaction() as con:
            result = self._record_rating(con, module_id=module_id, mastery=mastery,
                                         source='external', source_id=result_id, completed_at=completed_at, duration_ms=elapsed_ms)
        self.learning.flush_logs()
        return result

    def on_lesson_completed(self, con, attempt):
        # Same transaction as the Lesson result: neither side can commit alone.
        durations = [row[0] for row in con.execute('''SELECT elapsed_ms FROM submissions
            WHERE learner=? AND topic=? AND attempt=? AND status='judged' ''', (*self.key, attempt['id']))]
        self._record_rating(con, module_id=attempt['module_id'], mastery=attempt['mastery'],
                            source='lesson', source_id=attempt['id'],
                            completed_at=attempt['completed_at'], policy_version='lesson-v1',
                            duration_ms=sum(durations) if durations and all(n is not None for n in durations) else None)

    def due(self, *, limit=50):
        """Durable in-site dispatch queue; overdue tasks survive outages/restarts."""
        if type(limit) is not int or not 1 <= limit <= 500:
            raise DomainError('invalid_limit', '到期任务数量须在 1 至 500 之间。', 422)
        with closing(self.learning._connect()) as con:
            if self._paused(con) or self.learning._load(con)['status'] == 'paused':
                return []
            rows = con.execute('''SELECT * FROM review_cards
                WHERE learner=? AND topic=? AND version=? AND due_at<=? AND active_session IS NULL
                ORDER BY due_at,module LIMIT ?''', (*self.key, self.version, self._now().isoformat(), limit)).fetchall()
        return [{'task_type': 'review', 'learner_id': row['learner'], 'topic_id': row['topic'],
                 'module_id': row['module'], 'course_version': row['version'],
                 'due_at': row['due_at'], 'schedule_revision': row['revision']} for row in rows]

    def next_due_at(self):
        with closing(self.learning._connect()) as con:
            if self._paused(con) or self.learning._load(con)['status'] == 'paused':
                return None
            return con.execute('''SELECT MIN(due_at) FROM review_cards
                WHERE learner=? AND topic=? AND version=? AND active_session IS NULL''',
                               (*self.key, self.version)).fetchone()[0]

    def _session(self, con, session_id):
        row = con.execute('SELECT * FROM review_sessions WHERE id=? AND learner=? AND topic=?',
                          (session_id, *self.key)).fetchone()
        if not row:
            raise DomainError('unknown_review', '未找到此复习轮次。', 404)
        if row['version'] != self.version:
            raise DomainError('course_version_mismatch', '复习轮次的课程版本不匹配。')
        return json.loads(row['body'])

    def _save_session(self, con, state):
        state['revision'] += 1
        con.execute('UPDATE review_sessions SET status=?,body=? WHERE id=? AND learner=? AND topic=?',
                    (state['status'], encode(state), state['session_id'], *self.key))

    def _public(self, state):
        result = {key: state.get(key) for key in (
            'session_id', 'module_id', 'course_version', 'revision', 'status', 'mastery',
            'practice_error_count', 'practice_answered_count', 'practice_target_count',
            'phase', 'pending_submission_id', 'created_at', 'completed_at', 'paused_at',
            'pause_reason', 'pause_event_id', 'due_at')}
        result.update(learner_id=self.key[0], topic_id=self.key[1], policy_version=state['policy']['version'])
        question = self.learning.questions[state['order'][state['index']]]
        result['practice'] = {'id': question['id'], 'title': question['title'], 'html': question['html'],
                              'kind': 'practice', 'explanation_html':
                              question['explanation_html'] if state['phase'] == 'feedback' else None}
        result['feedback'] = state.get('feedback')
        result['answers'] = state['answers']
        result['actions'] = ([] if state['status'] != 'in_progress' or state['pending_submission_id']
                             else ['submit'] if state['phase'] == 'answer' else ['continue'])
        return result

    def state(self, session_id):
        self.learning.flush_logs()
        with closing(self.learning._connect()) as con:
            return self._public(self._session(con, session_id))

    def _action(self, con, kind, payload):
        request_id = self._request_id(payload.get('request_id'))
        fingerprint = self.learning._fingerprint('review_' + kind, payload)
        row = con.execute('SELECT * FROM review_actions WHERE learner=? AND topic=? AND id=?',
                          (*self.key, request_id)).fetchone()
        if row and row['fingerprint'] != fingerprint:
            raise DomainError('idempotency_conflict', '同一请求标识不能用于不同操作。')
        if con.execute('SELECT 1 FROM review_submissions WHERE id=?', (request_id,)).fetchone():
            raise DomainError('idempotency_conflict', '请求标识已用于答案提交。')
        return row, fingerprint

    def _record_action(self, con, payload, fingerprint, session_id):
        con.execute('INSERT INTO review_actions VALUES (?,?,?,?,?)',
                    (*self.key, payload['request_id'], fingerprint, session_id))

    def begin(self, module_id, *, request_id):
        module = self._module(module_id)
        payload = {'module_id': module_id, 'request_id': request_id}
        with self.learning._transaction() as con:
            previous, fingerprint = self._action(con, 'begin', payload)
            if previous:
                return self._public(self._session(con, previous['session']))
            self._guard_topic(con)
            card = self._card(con, module_id)
            if not card:
                raise DomainError('review_not_enrolled', '此 Example 尚未收到已完成的模块评分。')
            active = con.execute("SELECT id,module FROM review_sessions WHERE learner=? AND topic=? AND status='in_progress'", self.key).fetchone()
            if active:
                if active['module'] != module_id:
                    raise DomainError('review_in_progress', '请先完成本 Topic 当前的复习轮次。')
                state = self._session(con, active['id'])
            else:
                if utc_time(card['due_at']) > self._now():
                    raise DomainError('review_not_due', '此 Example 尚未到复习时间。')
                if len(module['practices']) < self.policy.targets[-1]:
                    raise DomainError('insufficient_practices', '当前练习库不足以完成这套复习规则。', 422)
                order = self.rng.sample([question['id'] for question in module['practices']], self.policy.targets[-1])
                state = {'session_id': str(uuid.uuid4()), 'module_id': module_id,
                         'course_version': self.version, 'revision': 0, 'status': 'in_progress',
                         'mastery': None, 'policy': self.policy.to_dict(), 'order': order, 'index': 0,
                         'phase': 'answer', 'feedback': None, 'answers': [],
                         'practice_error_count': 0, 'practice_answered_count': 0,
                         'practice_target_count': self.policy.targets[0], 'pending_submission_id': None,
                         'created_at': self._now().isoformat(), 'completed_at': None,
                         'paused_at': None, 'pause_reason': None, 'pause_event_id': None, 'due_at': card['due_at']}
                con.execute('INSERT INTO review_sessions VALUES (?,?,?,?,?,?,?)',
                            (state['session_id'], *self.key, module_id, self.version, state['status'], encode(state)))
                con.execute('UPDATE review_cards SET active_session=? WHERE learner=? AND topic=? AND module=?',
                            (state['session_id'], *self.key, module_id))
                self._event(con, 'review_started', module_id=module_id, session_id=state['session_id'],
                            policy_version=self.policy.version)
            self._record_action(con, payload, fingerprint, state['session_id'])
        self.learning.flush_logs()
        return self._public(state)

    def _guard_session(self, con, state, payload, *, revision=True):
        self._guard_topic(con)
        if state['status'] != 'in_progress':
            raise DomainError('review_inactive', '此复习轮次已完成或暂停。')
        if revision and (type(payload.get('expected_revision')) is not int
                         or payload['expected_revision'] != state['revision']):
            raise DomainError('stale_revision', '复习状态已更新，请重新读取。')
        if payload.get('question_id') != state['order'][state['index']]:
            raise DomainError('locked_question', '只能操作当前复习题目。', 403)
        if state['pending_submission_id']:
            raise DomainError('submission_pending', '答案正在判定，请稍候。')

    def advance(self, session_id, payload):
        data = {**payload, 'session_id': session_id}
        with self.learning._transaction() as con:
            state = self._session(con, session_id)
            previous, fingerprint = self._action(con, 'advance', data)
            if not previous:
                self._guard_session(con, state, data)
                if state['phase'] != 'feedback':
                    raise DomainError('answer_required', '请先完成当前题目。')
                state.update(index=state['index'] + 1, phase='answer', feedback=None)
                self._save_session(con, state)
                self._record_action(con, data, fingerprint, session_id)
        return self._public(state)

    def _submission(self, con, submission_id):
        row = con.execute('SELECT * FROM review_submissions WHERE id=? AND learner=? AND topic=?',
                          (submission_id, *self.key)).fetchone()
        if not row:
            raise DomainError('unknown_submission', '未找到此复习提交。', 404)
        return row

    def _submission_public(self, row, state):
        return {'submission_id': row['id'], 'status': 'pending' if row['status'] == 'running' else row['status'],
                'correct': None if row['correct'] is None else bool(row['correct']),
                'reason': row['reason'], 'state': self._public(state)}

    def submission(self, submission_id):
        with closing(self.learning._connect()) as con:
            row = self._submission(con, submission_id)
            return self._submission_public(row, self._session(con, row['session']))

    def submit(self, session_id, payload):
        submission_id = self._request_id(payload.get('request_id'))
        answer = payload.get('answer')
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
            raise DomainError('invalid_answer', '请输入有效答案（最多 2000 字）。', 422)
        elapsed = payload.get('elapsed_ms')
        if elapsed is not None and (type(elapsed) is not int or not 0 <= elapsed <= 86400000):
            raise DomainError('invalid_elapsed_time', '作答用时必须为 0 至 86400000 毫秒的整数。', 422)
        data = {**payload, 'session_id': session_id}
        fingerprint = self.learning._fingerprint('review_submit', data)
        with self.learning._transaction() as con:
            state = self._session(con, session_id)
            old = con.execute('SELECT * FROM review_submissions WHERE id=?', (submission_id,)).fetchone()
            if old:
                if (old['learner'], old['topic'], old['session'], old['fingerprint']) != (*self.key, session_id, fingerprint):
                    raise DomainError('idempotency_conflict', '同一提交标识不能用于不同答案。')
                if old['status'] != 'error':
                    return self._submission_public(old, state)
            if con.execute('SELECT 1 FROM review_actions WHERE learner=? AND topic=? AND id=?', (*self.key, submission_id)).fetchone():
                raise DomainError('idempotency_conflict', '请求标识已用于其他操作。')
            self._guard_session(con, state, data, revision=not bool(old))
            if state['phase'] != 'answer':
                raise DomainError('already_answered', '当前题目已经获得判定。')
            stamp = self._now().isoformat()
            if old:
                con.execute("UPDATE review_submissions SET status='pending',correct=NULL,reason=NULL,updated_at=?,run_token=NULL WHERE id=?", (stamp, submission_id))
            else:
                con.execute('INSERT INTO review_submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)',
                            (submission_id, *self.key, session_id, data['question_id'], answer,
                             fingerprint, 'pending', None, None, elapsed, stamp, stamp))
            state['pending_submission_id'] = submission_id
            self._save_session(con, state)
            row = self._submission(con, submission_id)
        return self._submission_public(row, state)

    def process_submission(self, submission_id):
        """Worker entry point, sharing the exact Lesson judging contract."""
        with self.learning._transaction() as con:
            row = self._submission(con, submission_id)
            if row['status'] != 'pending':
                return
            item = dict(row)
            run_token = uuid.uuid4().hex
            con.execute("UPDATE review_submissions SET status='running',run_token=?,updated_at=? WHERE id=?",
                        (run_token, self._now().isoformat(), submission_id))
        correct, reason = judge_answer(self.learning.judge, self.learning.questions[item['question']], item['answer'])
        with self.learning._transaction() as con:
            latest = self._submission(con, submission_id)
            if latest['status'] != 'running' or latest['run_token'] != run_token:
                return
            state = self._session(con, item['session'])
            applies = (not self._paused(con) and self.learning._load(con)['status'] != 'paused'
                       and state['status'] == 'in_progress' and state['phase'] == 'answer'
                       and state['pending_submission_id'] == submission_id
                       and state['order'][state['index']] == item['question'])
            status = ('judged' if correct is not None else 'error') if applies else 'discarded'
            con.execute('UPDATE review_submissions SET status=?,correct=?,reason=?,updated_at=? WHERE id=?',
                        (status, correct, reason, self._now().isoformat(), submission_id))
            if applies:
                state['pending_submission_id'] = None
                if correct is not None:
                    state.update(phase='feedback', feedback={'correct': correct, 'reason': reason, 'submission_id': submission_id})
                    state['answers'].append({'question_id': item['question'], 'answer': item['answer'],
                                             'correct': correct, 'elapsed_ms': item['elapsed_ms']})
                    policy = PracticePolicy.from_dict(state['policy'])
                    must_pause, mastery = apply_practice_result(state, correct, policy)
                    if mastery is not None:
                        completed_at = self._now().isoformat()
                        durations = [answer['elapsed_ms'] for answer in state['answers']]
                        result = self._record_rating(con, module_id=state['module_id'], mastery=mastery,
                                                     source='review', source_id=state['session_id'],
                                                     completed_at=completed_at, policy_version=policy.version,
                                                     duration_ms=sum(durations) if all(n is not None for n in durations) else None)
                        state.update(status='completed', mastery=mastery, completed_at=completed_at, due_at=result['due_at'])
                    self._save_session(con, state)
                    self._event(con, 'review_answer_judged', session_id=state['session_id'], module_id=state['module_id'],
                                submission_id=submission_id, correct=correct, mastery=state['mastery'])
                    if must_pause:
                        self._pause_topic(con, 'review_practice_error_limit')
                else:
                    self._save_session(con, state)
                    self._event(con, 'review_judge_error', session_id=state['session_id'], submission_id=submission_id)
            else:
                self._event(con, 'review_late_judgement_discarded', session_id=state['session_id'], submission_id=submission_id)
        self.learning.flush_logs()

    def on_topic_paused(self, con, event_id, reason):
        prior = self._paused(con)
        if prior:
            event_id, reason = prior['event_id'], prior['reason']
        else:
            con.execute('INSERT INTO review_topic_pauses VALUES (?,?,?,?)', (*self.key, event_id, reason))
        active = con.execute("SELECT id FROM review_sessions WHERE learner=? AND topic=? AND status='in_progress'", self.key).fetchall()
        for row in active:
            state = self._session(con, row['id'])
            state.update(status='paused', mastery=None, paused_at=self._now().isoformat(), pause_reason=reason,
                         pause_event_id=event_id, pending_submission_id=None)
            self._save_session(con, state)
            con.execute('UPDATE review_cards SET active_session=NULL WHERE learner=? AND topic=? AND module=?',
                        (*self.key, state['module_id']))
            self._event(con, 'review_paused', session_id=state['session_id'], module_id=state['module_id'],
                        pause_event_id=event_id, pause_reason=reason, mastery=None,
                        practice_error_count=state['practice_error_count'])

    def _pause_topic(self, con, reason):
        lesson = self.learning._load(con)
        if lesson['status'] == 'in_progress':
            self.learning._pause(con, lesson, reason)
            self.learning._save(con, lesson)
        elif lesson['status'] == 'paused':
            self.on_topic_paused(con, lesson['pause']['event_id'], lesson['pause']['reason'])
        else:
            event_id = self._event(con, 'review_topic_paused', pause_reason=reason, mastery=None)
            self.on_topic_paused(con, event_id, reason)

    def pause(self, session_id, *, request_id):
        payload = {'session_id': session_id, 'request_id': request_id}
        with self.learning._transaction() as con:
            state = self._session(con, session_id)
            prior, fingerprint = self._action(con, 'pause', payload)
            if not prior:
                if state['status'] == 'in_progress':
                    self._pause_topic(con, 'review_manual_difficulty')
                self._record_action(con, payload, fingerprint, session_id)
            state = self._session(con, session_id)
        self.learning.flush_logs()
        return self._public(state)

    def on_topic_unlocked(self, con, event_id):
        paused = self._paused(con)
        if paused and paused['event_id'] != event_id:
            raise DomainError('unlock_mismatch', '复习暂停事件不匹配。')
        con.execute('DELETE FROM review_topic_pauses WHERE learner=? AND topic=?', self.key)
        con.execute('INSERT OR IGNORE INTO review_unlocks VALUES (?,?,?)', (*self.key, event_id))
        if paused:
            self._event(con, 'review_topic_unlocked', pause_event_id=event_id)

    def unlock_topic(self, *, pause_event_id):
        """Local administrator integration only; never expose as learner API."""
        with self.learning._transaction() as con:
            prior = con.execute('SELECT 1 FROM review_unlocks WHERE learner=? AND topic=? AND event_id=?',
                                (*self.key, pause_event_id)).fetchone()
            if prior:
                return {'unlocked': True, 'pause_event_id': pause_event_id}
            paused = self._paused(con)
            if not paused or paused['event_id'] != pause_event_id:
                raise DomainError('unlock_mismatch', '必须指定当前 Topic 的暂停事件。')
            lesson = self.learning._load(con)
            if lesson['status'] != 'paused':
                self.on_topic_unlocked(con, pause_event_id)
                lesson_pause = None
            else:
                lesson_pause = lesson['pause']
        if lesson_pause:
            self.learning.unlock(self.key[1], lesson_pause['module_id'], pause_event_id)
        self.learning.flush_logs()
        return {'unlocked': True, 'pause_event_id': pause_event_id}

    def recover_interrupted(self):
        """Call once during backend startup, before starting any judge workers."""
        with self.learning._transaction() as con:
            con.execute("""UPDATE review_submissions SET status='error',correct=NULL,reason=?,run_token=NULL,
                updated_at=? WHERE learner=? AND topic=? AND status IN ('pending','running')""",
                        ('服务已重启，请重新提交；本次未计错。', self._now().isoformat(), *self.key))
            rows = con.execute("SELECT id FROM review_sessions WHERE learner=? AND topic=? AND status='in_progress'", self.key).fetchall()
            for row in rows:
                state = self._session(con, row['id'])
                if state['pending_submission_id']:
                    state['pending_submission_id'] = None
                    self._save_session(con, state)
