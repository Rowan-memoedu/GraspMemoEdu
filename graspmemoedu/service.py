from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from .judging import judge_answer
from .practice import LESSON_POLICY, LEGACY_LESSON_POLICY, PracticePolicy, apply_practice_result
from .practice_queue import PersistentPracticeQueue
from .question_types import validate_interaction, public_interaction, parse_answer, display_answer
from .question_bank import QuestionBank
from .idea_presentation import present_question


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class DomainError(Exception):
    def __init__(self, code, message, status_code=409):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


STORAGE_SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS topics (learner TEXT NOT NULL, topic TEXT NOT NULL, version TEXT NOT NULL,
    body TEXT NOT NULL, PRIMARY KEY(learner, topic));
CREATE TABLE IF NOT EXISTS actions (learner TEXT NOT NULL, topic TEXT NOT NULL, id TEXT NOT NULL,
    fingerprint TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(learner, topic, id));
CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY, learner TEXT NOT NULL, topic TEXT NOT NULL,
    attempt TEXT NOT NULL, step TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
    fingerprint TEXT NOT NULL, status TEXT NOT NULL, correct INTEGER, reason TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_answer ON submissions(learner, topic, attempt, question)
    WHERE status IN ('pending','running','judged');
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, learner TEXT NOT NULL, topic TEXT NOT NULL,
    created_at TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unlocks (pause_event TEXT PRIMARY KEY, learner TEXT NOT NULL,
    topic TEXT NOT NULL, module TEXT NOT NULL, attempt TEXT);
'''


class LearningService:
    """Transactional learning state. No network call holds a database transaction."""

    def __init__(self, course: dict, data_dir: Path, judge=None, learner_id='local', *, runtime=None):
        self.course, self.data_dir, self.judge = course, Path(data_dir), judge
        self.runtime = runtime
        if runtime is None:
            from .learning_storage import administrator
            if administrator(data_dir, learner_id):
                raise DomainError('admin_persistence_forbidden', '管理员学习必须使用临时会话。', 403)
        # Attached by the website or trusted review integration.
        self.review_backend = None
        self.learner_id, self.topic_id = learner_id, course['id']
        if runtime is None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / 'learning.sqlite3'
        self.log_path = self.data_dir / 'logs/events.jsonl'
        self._log_lock = threading.Lock()
        self.questions = {}
        self._validate_course()
        self.question_bank = QuestionBank(course, data_dir)
        self.practice_queue = PersistentPracticeQueue(self)
        for module in self.course.get('modules', []):
            self.practice_queue.supported(module)
        with closing(self._connect()) as con:
            con.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS topics (
                    learner TEXT NOT NULL, topic TEXT NOT NULL, version TEXT NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(learner, topic));
                CREATE TABLE IF NOT EXISTS actions (
                    learner TEXT NOT NULL, topic TEXT NOT NULL, id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, kind TEXT NOT NULL,
                    PRIMARY KEY(learner, topic, id));
                CREATE TABLE IF NOT EXISTS submissions (
                    id TEXT PRIMARY KEY, learner TEXT NOT NULL, topic TEXT NOT NULL,
                    attempt TEXT NOT NULL, step TEXT NOT NULL, question TEXT NOT NULL,
                    answer TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL, correct INTEGER, reason TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_live_answer
                    ON submissions(learner, topic, attempt, question)
                    WHERE status IN ('pending','running','judged');
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, learner TEXT NOT NULL, topic TEXT NOT NULL,
                    created_at TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS unlocks (
                    pause_event TEXT PRIMARY KEY, learner TEXT NOT NULL,
                    topic TEXT NOT NULL, module TEXT NOT NULL, attempt TEXT);
            ''')
            self.practice_queue.initialize_schema(con)
        with self._transaction() as con:
            columns = {item['name'] for item in con.execute('PRAGMA table_info(submissions)')}
            if 'elapsed_ms' not in columns:
                con.execute('ALTER TABLE submissions ADD COLUMN elapsed_ms INTEGER')
            row = con.execute('SELECT version,body FROM topics WHERE learner=? AND topic=?', self.key).fetchone()
            if row and row['version'] != course['version']:
                self._reconcile_version(con, row)
            if not row:
                state = {'revision': 0, 'status': 'in_progress', 'introduction_read': False,
                         'attempts': [], 'steps': [], 'module_index': 0, 'pause': None,
                         'pending_submission_id': None}
                self._new_attempt(state, 0)
                con.execute('INSERT INTO topics VALUES (?,?,?,?)', (*self.key, course['version'], encode(state)))
        self.flush_logs()

    @property
    def key(self):
        return self.learner_id, self.topic_id

    def _validate_course(self):
        from .topic_runtime import validate_runtime, is_v2, is_introduction
        try:
            validate_runtime(self.course)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise DomainError('invalid_course', 'Topic 章节、正文身份或引用结构无效。', 422) from exc
        from .topic_structure import has_atomic
        if has_atomic(self.course) or self.course.get('training_mode') == 'atomic_retrieval':
            from .atomic_content import validate_atomic
            try:
                self.questions = validate_atomic(self.course)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise DomainError('invalid_course', '原子训练结构、记忆点或批注位置无效。', 422) from exc
            if not self.course.get('modules'):
                return
        if self.course.get('training_mode', 'paradigm') not in ('paradigm', 'atomic_retrieval'):
            raise DomainError('invalid_course', '未知的 Topic 训练模式。', 422)
        if not self.course.get('version') or not (is_v2(self.course) or self.course.get('introduction', {}).get('html')):
            raise DomainError('invalid_course', '课程缺少版本或 Introduction。', 422)
        if is_introduction(self.course):
            return
        modules = self.course.get('modules', [])
        if not modules:
            raise DomainError('invalid_course', '课程没有 Example 模块。', 422)
        ids = set()
        for module in modules:
            if not module.get('id') or module['id'] in ids or len(module.get('practices', [])) < 7:
                raise DomainError('invalid_course', '模块标识重复或储备练习不足 7 道。', 422)
            ids.add(module['id'])
            for question in [module['example'], *module['practices']]:
                if (question.get('id') in self.questions or not question.get('id')
                        or any(not isinstance(question.get(k), str) or not question[k].strip() for k in
                               ['question', 'reference_answer', 'html', 'explanation_html', 'answer_type', 'constraints'])):
                    raise DomainError('invalid_course', '题目缺少独立标准答案、题型、约束或解析。', 422)
                self.questions[question['id']] = present_question(question)
                try:
                    validate_interaction(question.get('interaction'), question['question'])
                except (ValueError, TypeError, KeyError, TimeoutError) as exc:
                    raise DomainError('invalid_course', '题型配置或标准答案无效。', 422) from exc

    def _reconcile_version(self, con, row):
        raise DomainError('course_version_mismatch', '课程版本已变化，原进度已保留。请先由管理者处理版本迁移。')

    def _connect(self):
        if self.runtime is not None:
            return self.runtime.connect()
        con = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA synchronous=FULL')
        return con

    @contextmanager
    def _transaction(self):
        con = self._connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _load(self, con):
        row = con.execute('SELECT body,version FROM topics WHERE learner=? AND topic=?', self.key).fetchone()
        if row['version'] != self.course['version']:
            raise DomainError('course_version_mismatch', '课程版本与进度不一致。')
        return json.loads(row['body'])

    def _save(self, con, state):
        state['revision'] += 1
        con.execute('UPDATE topics SET body=? WHERE learner=? AND topic=?', (encode(state), *self.key))

    def _attempt(self, state):
        return next(a for a in state['attempts'] if a['id'] == state['attempt_id'])

    def _step(self, state, step_id=None):
        target = step_id or state['active_step_id']
        try:
            return next(s for s in state['steps'] if s['id'] == target)
        except StopIteration:
            raise DomainError('locked_step', '该内容尚未解锁。', 403)

    def _new_attempt(self, state, index):
        module = self.course['modules'][index]
        grouped = self.practice_queue.supported(module)
        policy = LESSON_POLICY if grouped else LEGACY_LESSON_POLICY
        attempt = {'id': str(uuid.uuid4()), 'module_id': module['id'], 'module_index': index,
                   'status': 'in_progress', 'mastery': None, 'learning_path': None,
                   'practice_error_count': 0, 'practice_answered_count': 0,
                   'practice_target_count': policy.targets[0], 'practice_policy': policy.to_dict(), 'example_result': None,
                   'created_at': now(), 'completed_at': None, 'paused_at': None, 'pause_reason': None}
        if grouped:
            attempt['practice_queue_version'] = self.practice_queue.VERSION
        state['attempts'].append(attempt)
        state['attempt_id'] = attempt['id']
        state['module_index'] = index
        self._add_step(state, kind='example', question=module['example'], phase='choice')

    def _add_step(self, state, *, kind, question=None, phase='reading'):
        attempt = self._attempt(state)
        question_id = question['id'] if question else None
        step = {'id': f"{attempt['id']}:{question_id or kind}", 'kind': kind, 'phase': phase,
                'module_id': attempt['module_id'], 'attempt_id': attempt['id'],
                'question_id': question_id, 'feedback': None, 'answer': None}
        if question_id:
            revision = self.question_bank.active_revision(question_id)
            if revision:
                step['question_bank_revision'] = revision
        state['steps'].append(step)
        state['active_step_id'] = state['current_step_id'] = step['id']
        return step

    def question_for_step(self, step):
        return self.question_bank.question(step['question_id'], step.get('question_bank_revision'), step['id'])

    def _fingerprint(self, kind, payload):
        # Absent timing has the same fingerprint as every pre-timing client.
        if kind == 'submit' and payload.get('elapsed_ms') is None:
            payload = {key: value for key, value in payload.items() if key != 'elapsed_ms'}
        return hashlib.sha256(encode({'kind': kind, 'payload': payload}).encode()).hexdigest()

    def _request(self, con, kind, payload):
        request_id = payload.get('request_id')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise DomainError('invalid_request', '请求缺少有效标识。', 422)
        fingerprint = self._fingerprint(kind, payload)
        previous = con.execute('SELECT * FROM actions WHERE learner=? AND topic=? AND id=?', (*self.key, request_id)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint or previous['kind'] != kind:
                raise DomainError('idempotency_conflict', '同一请求标识不能对应不同操作。')
            return True, fingerprint
        if con.execute('SELECT 1 FROM submissions WHERE id=?', (request_id,)).fetchone():
            raise DomainError('idempotency_conflict', '请求标识已用于答案提交。')
        return False, fingerprint

    def _record_action(self, con, kind, payload, fingerprint):
        con.execute('INSERT INTO actions VALUES (?,?,?,?,?)', (*self.key, payload['request_id'], fingerprint, kind))

    def _guard(self, state, payload, *, active=True, revision=True):
        if self.review_backend is not None:
            with closing(self._connect()) as con:
                if self.review_backend._paused(con):
                    raise DomainError('topic_paused', '当前 Topic 已暂停。')
        if state['status'] != 'in_progress':
            raise DomainError('topic_paused' if state['status'] == 'paused' else 'topic_completed', '当前 Topic 已暂停或完成。')
        if revision and payload.get('expected_revision') != state['revision']:
            raise DomainError('stale_revision', '学习状态已更新，已为你重新同步。')
        if payload.get('attempt_id') != state['attempt_id']:
            raise DomainError('stale_attempt', '作答轮次已变化，请刷新。')
        if active and payload.get('step_id') != state['active_step_id']:
            raise DomainError('locked_step', '只能操作当前活动步骤。', 403)
        if state['pending_submission_id']:
            raise DomainError('submission_pending', '答案正在判定，请稍候。')

    def _event(self, con, state, event_type, **extra):
        attempt = self._attempt(state)
        event = {'event_id': str(uuid.uuid4()), 'time': now(), 'type': event_type,
                 'learner_id': self.learner_id, 'topic_id': self.topic_id,
                 'module_id': attempt['module_id'], 'course_version': self.course['version'],
                 'attempt_id': attempt['id'], 'step_id': state['active_step_id'],
                 'practice_error_count': attempt['practice_error_count'],
                 'mastery': attempt['mastery'], **extra}
        con.execute('INSERT INTO events VALUES (?,?,?,?,?)', (event['event_id'], *self.key, event['time'], encode(event)))
        return event['event_id']

    def state(self):
        self.resume_due()
        self.flush_logs()
        with closing(self._connect()) as con:
            return self._public(self._load(con))

    def _public(self, state):
        attempt = self._attempt(state)
        active = self._step(state)
        modules = []
        for module in self.course['modules']:
            rounds = [a for a in state['attempts'] if a['module_id'] == module['id']]
            last = rounds[-1] if rounds else None
            summary = {k: last[k] if last else None for k in
                       ['status', 'mastery', 'learning_path', 'practice_error_count',
                        'practice_answered_count', 'practice_target_count', 'example_result', 'completed_at', 'paused_at', 'pause_reason']}
            if not last:
                summary.update(status='locked', practice_error_count=0, practice_answered_count=0,
                               practice_target_count=LESSON_POLICY.targets[0] if self.practice_queue.supported(module) else LEGACY_LESSON_POLICY.targets[0])
            summary.update(id=module['id'], title=module['title'], attempt_id=last['id'] if last else None)
            modules.append(summary)
        steps = []
        for saved in state['steps']:
            step = dict(saved)
            question = self.question_for_step(saved) if saved['question_id'] else None
            if question:
                step.update(title=question['title'], html=question['html'], explanation_html=None)
                from .topic_composition import section_for
                module = next((m for m in self.course['modules'] if m['id'] == saved['module_id']), {})
                if section := section_for(self.course, module):
                    step['section'] = section
                from .self_assessment import presentation
                step.update(presentation(question, saved, visible=saved['phase'] == 'reading'
                    or saved['kind'] == 'practice' and saved['phase'] == 'feedback'))
                step['content_version'] = saved.get('question_bank_revision') or self.course['version']
                if question.get('interaction'):
                    step['interaction'] = public_interaction(question)
                    step['answer_display'] = display_answer(question, saved.get('answer'))
                if saved['phase'] == 'reading' or (saved['kind'] == 'practice' and saved['phase'] == 'feedback'):
                    step['explanation_html'] = question['explanation_html']
            elif saved['kind'] == 'introduction':
                intro = (next(i for i in self.course['introductions'] if i['id'] == saved['introduction_id'])
                         if saved.get('introduction_id') else self.course['introduction'])
                step.update(title=intro.get('title', 'Introduction'), html=intro['html'], explanation_html=None)
                for field in ('example', 'footnotes'):
                    if field in intro:
                        step[field] = intro[field]
                from .topic_composition import section_for
                section = section_for(self.course, intro)
                if section:
                    step['section'] = section
                from .atomic_content import public_source_attributions
                attributions = public_source_attributions(intro.get('source_attributions'))
                if attributions:
                    step['source_attributions'] = attributions
            else:
                step.update(title='Topic 完成', html='<p>本 Topic 的所有模块已完成。</p>', explanation_html=None)
            is_active = saved['id'] == state['active_step_id']
            is_current = saved['id'] == state['current_step_id']
            visible = not (active['kind'] == 'introduction' and saved['module_id'] == active['module_id'] and saved['kind'] != 'introduction')
            step.update(visited=True, unlocked=True, current=is_current, active=is_active, visible=visible, actions=[])
            if is_active and is_current and state['status'] == 'in_progress' and not state['pending_submission_id']:
                if saved['phase'] == 'choice':
                    step['actions'] = ['mastered', 'unmastered']
                elif saved['phase'] == 'answer':
                    step['actions'] = ['submit']
                else:
                    step['actions'] = ['continue']
            steps.append(step)
        return {'topic_id': self.topic_id, 'title': self.course['title'], 'course_version': self.course['version'],
                'math_css': self.course.get('math_css', '') + self.question_bank.css(s.get('question_bank_revision') for s in state['steps']),
                'learner_id': self.learner_id, 'revision': state['revision'], 'status': state['status'],
                'introduction_read': state['introduction_read'], 'active_step_id': state['active_step_id'],
                'current_step_id': state['current_step_id'], 'attempt_id': attempt['id'], 'pause': state['pause'],
                'pending_submission_id': state['pending_submission_id'], 'modules': modules, 'steps': steps,
                'skipped_modules': list(state.get('skipped_modules', {}).values()),
                'cooldown_until': (state.get('cooldown') or {}).get('resume_at')}

    def skip_module(self, payload):
        from .module_flow import skip
        return skip(self, payload)

    def resume_due(self):
        from .module_flow import resume_due
        with self._transaction() as con:
            return resume_due(self, con)

    def reset_progress(self, payload):
        """Start a fresh Topic generation, retaining the previous state for audit."""
        with self._transaction() as con:
            state = self._load(con)
            replay, fingerprint = self._request(con, 'guest_reset', payload)
            if not replay:
                if state.get('cooldown'):
                    raise DomainError('topic_cooldown', '6 小时后可再次学习。', 409)
                # Reset is also allowed after completion, pause or while judging.
                if payload.get('expected_revision') != state['revision']:
                    raise DomainError('stale_revision', '学习状态已更新，请刷新后重置。')
                attempt_id, step_id = self._reset_identity(state)
                if payload.get('attempt_id') != attempt_id:
                    raise DomainError('stale_attempt', '作答轮次已变化，请刷新。')
                if payload.get('step_id') != step_id:
                    raise DomainError('locked_step', '学习步骤已变化，请刷新。', 403)
                review_tables = {row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'review_%'")}
                if 'review_submissions' in review_tables and con.execute(
                        "SELECT 1 FROM review_submissions WHERE learner=? AND topic=? AND status IN ('pending','running')",
                        self.key).fetchone():
                    raise DomainError('review_submission_pending', '复习答案正在判定，请等待完成后重置。')
                # Keep scheduling preferences, but remove this identity's entire
                # review generation in the same transaction as its Lesson reset.
                for table in ('review_cards', 'review_ratings', 'review_sessions', 'review_actions',
                              'review_submissions', 'review_topic_pauses', 'review_unlocks', 'review_optimizations'):
                    if table in review_tables:
                        con.execute(f'DELETE FROM {table} WHERE learner=? AND topic=?', self.key)
                con.execute('DELETE FROM practice_queues WHERE learner=? AND topic=?', self.key)
                previous = state
                state = {'revision': previous['revision'], 'status': 'in_progress',
                         'introduction_read': False, 'attempts': [], 'steps': [],
                         'module_index': 0, 'pause': None, 'pending_submission_id': None,
                         'reset_at': now()}
                self._new_attempt(state, 0)
                con.execute("UPDATE submissions SET status='discarded',reason=?,updated_at=? "
                            "WHERE learner=? AND topic=? AND status IN ('pending','running')",
                            ('学习进度已重置，本次作答不再计入。', now(), *self.key))
                self._event(con, state, 'guest_progress_reset', previous_state=previous)
                self._record_action(con, 'guest_reset', payload, fingerprint)
                self._save(con, state)
        self.flush_logs()
        return self._public(state)

    def _reset_identity(self, state):
        return state['attempt_id'], state['active_step_id']

    def choose(self, payload):
        with self._transaction() as con:
            state = self._load(con)
            replay, fingerprint = self._request(con, 'choose', payload)
            if not replay:
                self._guard(state, payload)
                step, attempt = self._step(state), self._attempt(state)
                if step['kind'] != 'example' or step['phase'] != 'choice':
                    raise DomainError('invalid_step', '此步骤不接受掌握选择。')
                choice = payload.get('choice')
                if choice not in ('mastered', 'unmastered'):
                    raise DomainError('invalid_choice', '请选择已掌握或未掌握。', 422)
                attempt['example_result'] = {'choice': choice, 'correct': None}
                state.setdefault('started_at', now())
                if choice == 'mastered':
                    step['phase'] = 'answer'
                else:
                    step['phase'] = 'unmastered'
                    attempt['learning_path'] = 'unmastered'
                self._event(con, state, 'example_choice', choice=choice)
                self._record_action(con, 'choose', payload, fingerprint)
                self._save(con, state)
        self.flush_logs()
        return self._public(state)

    def _learn(self, state):
        example = next(s for s in state['steps'] if s['attempt_id'] == state['attempt_id'] and s['kind'] == 'example')
        if not state['introduction_read']:
            prior = next((s for s in state['steps'] if s['kind'] == 'introduction'), None)
            if prior:
                prior['phase'] = 'reading'
                prior['module_id'] = self._attempt(state)['module_id']
                state['active_step_id'] = state['current_step_id'] = prior['id']
            else:
                self._add_step(state, kind='introduction')
        else:
            example['phase'] = 'reading'
            state['active_step_id'] = state['current_step_id'] = example['id']

    def _module_order(self):
        return list(range(len(self.course['modules'])))

    def _next_module(self, state):
        completed = {a['module_id'] for a in state['attempts'] if a['status'] == 'completed'}
        skipped = state.get('skipped_modules', {})
        index = next((i for i in self._module_order() if self.course['modules'][i]['id'] not in completed
                      and self.course['modules'][i]['id'] not in skipped), None)
        if index is None:
            state.setdefault('initial_completed_at', now())
            index = next((i for i in self._module_order() if self.course['modules'][i]['id'] in skipped
                          and skipped[self.course['modules'][i]['id']]['released']
                          and self.course['modules'][i]['id'] not in completed), None)
        if index is None:
            state['status'] = 'completed'
            state.setdefault('completed_at', now())
            if not skipped:
                state['completed_at'] = now()
            self._add_step(state, kind='completion', phase='completed')
        else:
            self._new_attempt(state, index)

    def advance(self, payload):
        with self._transaction() as con:
            state = self._load(con)
            replay, fingerprint = self._request(con, 'continue', payload)
            if not replay:
                self._guard(state, payload)
                self._advance_step(con, state)
                self._record_action(con, 'continue', payload, fingerprint)
                self._save(con, state)
        self.flush_logs()
        return self._public(state)

    def _advance_step(self, con, state):
        """Advance only when the next normal page is actually being shown."""
        step, attempt = self._step(state), self._attempt(state)
        if step['phase'] in ('choice', 'answer'):
            raise DomainError('answer_required', '请先完成当前步骤。')
        if step['kind'] == 'introduction':
            state['introduction_read'] = True
            step['phase'] = 'completed'
            self._learn(state)
        elif attempt['status'] == 'completed':
            self._next_module(state)
        elif step['kind'] == 'example' and step['phase'] in ('unmastered', 'feedback'):
            self._learn(state)
        elif step['kind'] == 'example' and step['phase'] == 'reading':
            question = self._practice_question(con, state, attempt)
            self._add_step(state, kind='practice', question=question, phase='answer')
        elif step['kind'] == 'practice' and step['phase'] == 'feedback':
            question = self._practice_question(con, state, attempt)
            self._add_step(state, kind='practice', question=question, phase='answer')
        else:
            raise DomainError('invalid_step', '无法推进当前步骤。')

    def _practice_question(self, con, state, attempt):
        module = self.course['modules'][attempt['module_index']]
        if attempt.get('practice_queue_version') == self.practice_queue.VERSION:
            shown = [step['question_id'] for step in state['steps']
                     if step['attempt_id'] == attempt['id'] and step['kind'] == 'practice']
            options = {}
            if attempt.get('practice_policy', {}).get('version') == LESSON_POLICY.version:
                seeds = [group[0] for group in self.practice_queue._groups(module)[:3]]
                options['initial'] = seeds
                if len(shown) < len(seeds):
                    options['selected'] = seeds[len(shown)]
            question_id = self.practice_queue.draw(con, module, exclude=shown, **options)
            return self.questions[question_id]
        # A round started before this upgrade keeps its original question order.
        return module['practices'][attempt['practice_answered_count']]

    def select(self, payload):
        with self._transaction() as con:
            state = self._load(con)
            replay, fingerprint = self._request(con, 'select', payload)
            if not replay:
                if payload.get('expected_revision') != state['revision']:
                    raise DomainError('stale_revision', '学习状态已变化，请重新同步。')
                self._step(state, payload.get('step_id'))
                state['current_step_id'] = payload['step_id']
                self._record_action(con, 'select', payload, fingerprint)
                self._save(con, state)
        return self._public(state)

    def _pause(self, con, state, reason, *, submission=None):
        attempt = self._attempt(state)
        if attempt['status'] != 'completed':
            attempt.update(status='paused', mastery=None, paused_at=now(), pause_reason=reason)
        step = self._step(state)
        if submission is None:
            related_id = (step.get('feedback') or {}).get('submission_id') or state.get('pending_submission_id')
            if related_id:
                row = con.execute('SELECT * FROM submissions WHERE id=?', (related_id,)).fetchone()
                if row:
                    submission = dict(row)
        extra = {'pause_reason': reason, 'mastery': None, 'question_id': None,
                 'student_answer': None, 'judgement': None, 'submission_id': None,
                 'step_phase': step['phase']}
        if submission:
            extra.update(question_id=submission['question'], student_answer=submission['answer'],
                         judgement={'correct': bool(submission['correct']), 'reason': submission['reason']} if submission['correct'] is not None else None,
                         submission_id=submission['id'])
        event_id = self._event(con, state, 'topic_paused', **extra)
        state['status'] = 'paused'
        state['pause'] = {'event_id': event_id, 'reason': reason, 'module_id': attempt['module_id'],
                          'attempt_id': attempt['id'], 'message': '检测到状态不佳，已暂停作答' if reason in ('practice_error_limit', 'review_practice_error_limit')
                          else '已手动暂停学习，请等待管理者解锁。'}
        state['pending_submission_id'] = None

        if self.review_backend is not None:
            self.review_backend.on_topic_paused(con, event_id, reason)
        from .module_flow import student, set_cooldown
        if reason in ('practice_error_limit', 'review_practice_error_limit') and not student(self, con):
            set_cooldown(self, state, event_id, 'lesson')

    def pause(self, payload):
        with self._transaction() as con:
            state = self._load(con)
            replay, fingerprint = self._request(con, 'pause', payload)
            if not replay:
                if state['status'] == 'in_progress':
                    self._pause(con, state, 'manual_difficulty')
                    self._save(con, state)
                self._record_action(con, 'pause', payload, fingerprint)
        self.flush_logs()
        return self._public(state)

    def submit(self, payload):
        submission_id = payload.get('request_id')
        if not isinstance(submission_id, str) or not submission_id.strip() or len(submission_id) > 128:
            raise DomainError('invalid_request', '缺少有效提交标识。', 422)
        elapsed_ms = payload.get('elapsed_ms')
        if elapsed_ms is not None and (type(elapsed_ms) is not int or not 0 <= elapsed_ms <= 86400000):
            raise DomainError('invalid_elapsed_time', '作答用时必须为 0 至 86400000 毫秒的整数。', 422)
        fingerprint = self._fingerprint('submit', payload)
        with self._transaction() as con:
            state = self._load(con)
            old = con.execute('SELECT * FROM submissions WHERE id=?', (submission_id,)).fetchone()
            if old:
                if old['learner'] != self.learner_id or old['topic'] != self.topic_id or old['fingerprint'] != fingerprint:
                    raise DomainError('idempotency_conflict', '同一提交标识不能用于不同答案。')
                if old['status'] != 'error':
                    return self._submission_public(old, state)
            elif con.execute('SELECT 1 FROM actions WHERE learner=? AND topic=? AND id=?', (*self.key, submission_id)).fetchone():
                raise DomainError('idempotency_conflict', '请求标识已用于其他动作。')
            self._guard(state, payload, revision=not bool(old))
            step = self._step(state)
            if step['phase'] != 'answer' or step['kind'] not in ('example', 'practice'):
                raise DomainError('already_answered', '当前题目不接受再次作答。')
            answer = payload.get('answer')
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 16384:
                raise DomainError('invalid_answer', '请输入有效答案（最多 2000 字）。', 422)
            from .self_assessment import answer as assessed_answer
            try:
                assessed_answer(self.question_for_step(step), step, payload)
                if payload.get('self_rating') is None:
                    parse_answer(self.question_for_step(step), answer)
            except (ValueError, TypeError, KeyError) as exc:
                raise DomainError('invalid_answer', '请完成全部作答，且不要重复使用配对选项。', 422) from exc
            if old:
                con.execute("UPDATE submissions SET status='pending',correct=NULL,reason=NULL,updated_at=? WHERE id=?", (now(), submission_id))
            else:
                con.execute('''INSERT INTO submissions
                            (id,learner,topic,attempt,step,question,answer,fingerprint,status,correct,reason,created_at,updated_at,elapsed_ms)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (submission_id, *self.key, state['attempt_id'], step['id'], step['question_id'],
                             answer, fingerprint, 'pending', None, None, now(), now(), elapsed_ms))
            state['pending_submission_id'] = submission_id
            self._save(con, state)
            row = con.execute('SELECT * FROM submissions WHERE id=?', (submission_id,)).fetchone()
        return self._submission_public(row, state)

    def reveal(self, payload):
        from .self_assessment import reveal
        with self._transaction() as con:
            state = self._load(con)
            replay, fingerprint = self._request(con, 'reveal', payload)
            if not replay:
                self._guard(state, payload)
                step = self._step(state)
                if step['phase'] != 'answer' or step['kind'] not in ('example', 'practice'):
                    raise DomainError('invalid_step', '当前没有可显示答案的题目。', 409)
                reveal(self.question_for_step(step), step)
                self._record_action(con, 'reveal', payload, fingerprint)
                self._save(con, state)
        return self._public(state)

    def _submission_public(self, row, state):
        return {'submission_id': row['id'], 'status': 'pending' if row['status'] == 'running' else row['status'],
                'correct': None if row['correct'] is None else bool(row['correct']),
                'reason': row['reason'], 'state': self._public(state)}

    def submission(self, submission_id):
        with closing(self._connect()) as con:
            row = con.execute('SELECT * FROM submissions WHERE id=? AND learner=? AND topic=?', (submission_id, *self.key)).fetchone()
            if not row:
                raise DomainError('unknown_submission', '未找到此提交。', 404)
            return self._submission_public(row, self._load(con))

    def instant_submission(self, submission_id):
        from .judging import instant_question
        with closing(self._connect()) as con:
            row = con.execute('SELECT step FROM submissions WHERE id=? AND learner=? AND topic=?',
                              (submission_id, *self.key)).fetchone()
            return bool(row and instant_question(self.question_for_step(self._step(self._load(con), row['step']))))

    def process_submission(self, submission_id):
        with self._transaction() as con:
            row = con.execute('SELECT * FROM submissions WHERE id=? AND learner=? AND topic=?', (submission_id, *self.key)).fetchone()
            if not row or row['status'] != 'pending':
                return
            con.execute("UPDATE submissions SET status='running',updated_at=? WHERE id=?", (now(), submission_id))
            item = dict(row)
            state = self._load(con)
            saved = next((s for s in state['steps'] if s['id'] == item['step']), None)
            if saved is None:
                con.execute("UPDATE submissions SET status='discarded',updated_at=? WHERE id=?", (now(), submission_id))
                return
            question = self.question_for_step(saved)
        from .self_assessment import verdict as self_verdict
        correct, reason = self_verdict(saved, submission_id) or judge_answer(self.judge, question, item['answer'])
        valid = correct is not None
        with self._transaction() as con:
            state = self._load(con)
            latest = con.execute('SELECT status FROM submissions WHERE id=?', (submission_id,)).fetchone()
            if latest['status'] != 'running':
                return
            applies = (state['status'] == 'in_progress' and state['attempt_id'] == item['attempt']
                       and state['active_step_id'] == item['step'] and state['pending_submission_id'] == submission_id)
            status = ('judged' if valid else 'error') if applies else 'discarded'
            con.execute('UPDATE submissions SET status=?,correct=?,reason=?,updated_at=? WHERE id=?',
                        (status, correct, reason, now(), submission_id))
            if not applies:
                self._event(con, state, 'late_judgement_discarded', submission_id=submission_id,
                            original_attempt_id=item['attempt'], correct=correct)
            else:
                state['pending_submission_id'] = None
                if valid:
                    step, attempt = self._step(state), self._attempt(state)
                    step.update(phase='feedback', answer=item['answer'], feedback={
                        'correct': correct, 'reason': reason, 'submission_id': submission_id})
                    if step['kind'] == 'example':
                        attempt['example_result'].update(correct=correct, submission_id=submission_id)
                        attempt['learning_path'] = 'direct_example' if correct else 'example_incorrect'
                        if correct:
                            self._complete(con, state, 4)
                    else:
                        policy = PracticePolicy.from_dict(attempt['practice_policy']) if 'practice_policy' in attempt else LEGACY_LESSON_POLICY
                        must_pause, mastery = apply_practice_result(attempt, correct, policy)
                        if (attempt.get('practice_queue_version') == self.practice_queue.VERSION
                                and (not must_pause or policy.version == LEGACY_LESSON_POLICY.version)):
                            self.practice_queue.record_result(con, self.course['modules'][attempt['module_index']],
                                                              item['question'], correct)
                        if must_pause:
                            self._pause(con, state, 'practice_error_limit', submission={**item, 'correct': correct, 'reason': reason})
                        elif mastery is not None:
                            self._complete(con, state, mastery)
                    self._event(con, state, 'answer_judged', submission_id=submission_id,
                                question_id=item['question'], correct=correct)
                else:
                    self._event(con, state, 'judge_error', submission_id=submission_id, mastery=None)
                self._save(con, state)
        self.flush_logs()

    def _complete(self, con, state, mastery):
        attempt = self._attempt(state)
        attempt.update(status='completed', mastery=mastery, completed_at=now())
        state.get('skipped_modules', {}).pop(attempt['module_id'], None)
        self._event(con, state, 'module_completed')
        if self.review_backend is not None:
            self.review_backend.on_lesson_completed(con, attempt)

    def recover_interrupted(self):
        """Called by the one server process on startup, never by an admin client."""
        with self._transaction() as con:
            state = self._load(con)
            con.execute("UPDATE submissions SET status='error',correct=NULL,reason=?,updated_at=? WHERE learner=? AND topic=? AND status IN ('pending','running')",
                        ('服务已重启，本次判题未完成，请重新提交；不计错。', now(), *self.key))
            if state['pending_submission_id']:
                state['pending_submission_id'] = None
                self._save(con, state)
        self.flush_logs()

    def unlock(self, topic_id, module_id, pause_event_id):
        if topic_id != self.topic_id:
            raise DomainError('wrong_topic', '解锁 Topic 不匹配。', 404)
        with self._transaction() as con:
            state = self._load(con)
            prior = con.execute('SELECT * FROM unlocks WHERE pause_event=?', (pause_event_id,)).fetchone()
            if prior:
                if prior['learner'] != self.learner_id or prior['topic'] != topic_id or prior['module'] != module_id:
                    raise DomainError('unlock_mismatch', '解锁对象不匹配。')
                return self._public(state)
            if module_id in state.get('skipped_modules', {}):
                from .module_flow import unlock_skip
                unlock_skip(self, con, state, module_id, pause_event_id)
                self._save(con, state)
                return self._public(state)
            pause = state['pause']
            if state['status'] != 'paused' or not pause or pause['event_id'] != pause_event_id or pause['module_id'] != module_id:
                raise DomainError('unlock_mismatch', '必须指定当前暂停的学习者、Topic、模块及暂停事件。')
            previous = self._attempt(state)
            state.update(status='in_progress', pause=None, pending_submission_id=None)
            state.pop('cooldown', None)
            if previous['status'] == 'completed':
                self._next_module(state)
            else:
                self._new_attempt(state, previous['module_index'])
            self._event(con, state, 'admin_unlocked', pause_event_id=pause_event_id,
                        old_attempt_id=previous['id'], new_attempt_id=state['attempt_id'])
            if self.review_backend is not None:
                self.review_backend.on_topic_unlocked(con, pause_event_id)
            con.execute('INSERT INTO unlocks VALUES (?,?,?,?,?)', (pause_event_id, *self.key, module_id, state['attempt_id']))
            self._save(con, state)
        self.flush_logs()
        return self._public(state)

    def flush_logs(self):
        """Rebuild the readable outbox under an interprocess lock, atomically.

        SQLite is authoritative. A failed log write never rolls back learning;
        the next read/startup retries all events, using stable IDs exactly once.
        """
        if self.runtime is not None:
            return True
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_lock, (self.log_path.parent / '.events.lock').open('a+b') as lock:
                lock.seek(0, 2)
                if lock.tell() == 0:
                    lock.write(b'0')
                    lock.flush()
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    with closing(self._connect()) as con:
                        rows = con.execute('SELECT body FROM events ORDER BY rowid').fetchall()
                    temporary = self.log_path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
                    with temporary.open('w', encoding='utf-8', newline='\n') as out:
                        for row in rows:
                            out.write(row['body'] + '\n')
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(temporary, self.log_path)
                finally:
                    lock.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return True
        except OSError:
            return False

    def backup(self, path):
        if self.runtime is not None:
            raise DomainError('admin_persistence_forbidden', '管理员临时学习不生成备份。', 403)
        path = Path(path)
        if path.resolve() == self.db_path.resolve() or path.exists():
            raise DomainError('backup_exists', '备份目标必须是新的独立文件。', 422)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
            result = target.execute('PRAGMA integrity_check').fetchone()[0]
            if result != 'ok':
                raise DomainError('backup_failed', '备份完整性校验失败。', 500)
        return path


def create_learning_service(course, data_dir, judge=None, learner_id='local', **options):
    from .source_content import trainable, SourceReader
    if not trainable(course):
        return SourceReader(course, data_dir, judge, learner_id, **options)
    from .topic_structure import sectioned_training, has_atomic
    if sectioned_training(course):
        from .unified_learning import UnifiedLearningService
        return UnifiedLearningService(course, data_dir, judge, learner_id, **options)
    if has_atomic(course) or course.get('training_mode') == 'atomic_retrieval':
        if course.get('source_adapter') == 'hyperskill':
            from .hyperskill import HyperskillLearningService
            return HyperskillLearningService(course, data_dir, judge, learner_id, **options)
        if course.get('academy'):
            from .academy import AcademyLearningService
            return AcademyLearningService(course, data_dir, judge, learner_id, **options)
        from .atomic import AtomicLearningService
        return AtomicLearningService(course, data_dir, judge, learner_id, **options)
    if course.get('topic_format_version') == 2:
        from .ordered_learning import OrderedLearningService
        return OrderedLearningService(course, data_dir, judge, learner_id, **options)
    return LearningService(course, data_dir, judge, learner_id, **options)
