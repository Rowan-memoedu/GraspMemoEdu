"""Course-level projections and personal preferences in the learning database."""

from __future__ import annotations

import base64
import hashlib
from contextlib import closing
from datetime import datetime, timedelta
import json
import math
import uuid
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .public_limits import PublicLimitError
from .service import DomainError, now
from .dependencies import DependencyPolicy


class DashboardStore:
    def __init__(self, catalog, access_store, subjects=None):
        self.catalog, self.access_store = catalog, access_store
        self.subjects = subjects
        self.dependency_policy = DependencyPolicy(catalog, access_store, subjects)
        with closing(access_store._connect()) as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS learner_preferences (
                    learner_id TEXT PRIMARY KEY REFERENCES access_principals(learner_id),
                    selected_course_id TEXT, timezone TEXT, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    learner_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    message TEXT NOT NULL, course_id TEXT, topic_id TEXT,
                    task_id TEXT, question_id TEXT, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS feedback_learner_created
                    ON feedback(learner_id, created_at);
                CREATE TABLE IF NOT EXISTS learner_subject_preferences (
                    learner_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    subject_id TEXT NOT NULL, selected_course_id TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(learner_id,subject_id));
            ''')

    def _allowed_topics(self, access):
        return {topic['id'] for topic in access['topics']}

    @staticmethod
    def _feature(access, feature):
        if feature not in access['features']:
            raise DomainError('feature_forbidden', '当前账号没有使用此功能的权限。', 403)

    def _course(self, access, course_id, subject_id=None):
        if self.subjects:
            self.subjects.authorize_subject(access, subject_id)
            self.subjects.authorize_subject(access, self.subjects.course_subjects.get(course_id))
            self.subjects.authorize_course(subject_id, course_id)
        course = self.catalog.course(course_id)
        if not self._allowed_topics(access).intersection(topic['id'] for topic in course['topics']):
            raise DomainError('course_forbidden', '当前身份没有访问此课程的权限。', 403)
        return course

    def profile(self, access):
        with closing(self.access_store._connect()) as con:
            row = con.execute('SELECT * FROM learner_preferences WHERE learner_id=?', (access['learner_id'],)).fetchone()
        selected = row['selected_course_id'] if row else None
        if selected not in self.catalog.courses or not self._allowed_topics(access).intersection(
                topic['id'] for topic in self.catalog.courses[selected]['topics']):
            selected = None
        return {'display_name': access['display_name'], 'timezone': row['timezone'] if row else None,
                'selected_course_id': selected}

    def update_profile(self, access, values):
        if 'display_name' in values:
            name = values['display_name']
            if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 100
                    or any(ord(character) < 32 for character in name)):
                raise DomainError('invalid_profile', '显示名称需要 1 至 100 个可显示字符。', 422)
            values['display_name'] = name.strip()
        if 'timezone' in values:
            zone = values['timezone']
            try:
                if not isinstance(zone, str) or not 1 <= len(zone) <= 100:
                    raise ValueError('Invalid timezone')
                ZoneInfo(zone)
            except (ValueError, ZoneInfoNotFoundError):
                raise DomainError('invalid_timezone', '请选择有效的 IANA 时区。', 422)
        with self.access_store._transaction() as con:
            if 'display_name' in values:
                con.execute('UPDATE access_principals SET display_name=?,updated_at=? WHERE learner_id=?',
                            (values['display_name'], now(), access['learner_id']))
            con.execute('''INSERT INTO learner_preferences (learner_id,timezone,updated_at) VALUES (?,?,?)
                        ON CONFLICT(learner_id) DO UPDATE SET
                        timezone=CASE WHEN ? THEN excluded.timezone ELSE learner_preferences.timezone END,
                        updated_at=excluded.updated_at''',
                        (access['learner_id'], values.get('timezone'), now(), 'timezone' in values))
        return self.profile({**access, 'display_name': values.get('display_name', access['display_name'])})

    def _selection(self, access, subject_id=None):
        if subject_id is None or not self.subjects:
            return self.profile(access)['selected_course_id']
        ids = self.subjects.course_ids(subject_id)
        with closing(self.access_store._connect()) as con:
            row = con.execute('SELECT selected_course_id FROM learner_subject_preferences WHERE learner_id=? AND subject_id=?',
                              (access['learner_id'], subject_id)).fetchone()
        selected = row['selected_course_id'] if row else self.profile(access)['selected_course_id'] if subject_id == 'math' else None
        if selected not in ids or not self._allowed_topics(access).intersection(
                topic['id'] for topic in self.catalog.courses[selected]['topics']):
            return None
        return selected

    def catalog_view(self, access, subject_id=None):
        allowed = self._allowed_topics(access)
        if self.subjects:
            self.subjects.authorize_subject(access, subject_id)
        scope = 'math' if self.subjects and subject_id is None and access['role'] == 'guest' else subject_id
        ids = self.subjects.course_ids(scope) if scope is not None and self.subjects else set(self.catalog.courses)
        return {'courses': [{'id': course['id'], 'title': course['title'], 'description': course['description'],
                             'available': bool(allowed.intersection(topic['id'] for topic in course['topics'])),
                             'topic_count': len(course['topics'])} for course in self.catalog.courses.values() if course['id'] in ids],
                'selected_course_id': self._selection(access, subject_id)}

    def select_course(self, access, course_id, subject_id=None):
        self._course(access, course_id, subject_id)
        with self.access_store._transaction() as con:
            if subject_id is not None and self.subjects:
                con.execute('''INSERT INTO learner_subject_preferences VALUES (?,?,?,?)
                            ON CONFLICT(learner_id,subject_id) DO UPDATE SET selected_course_id=excluded.selected_course_id,
                            updated_at=excluded.updated_at''', (access['learner_id'], subject_id, course_id, now()))
            else:
                con.execute('''INSERT INTO learner_preferences (learner_id,selected_course_id,updated_at) VALUES (?,?,?)
                        ON CONFLICT(learner_id) DO UPDATE SET selected_course_id=excluded.selected_course_id,
                        updated_at=excluded.updated_at''', (access['learner_id'], course_id, now()))
        return {'selected_course_id': course_id}

    def _snapshot(self, con, learner_id, topic_id):
        topic = self.catalog.topic(topic_id)
        row = con.execute('SELECT body,version FROM topics WHERE learner=? AND topic=?', (learner_id, topic_id)).fetchone()
        state = json.loads(row['body']) if row else None
        if row and row['version'] != topic['version']:
            raise DomainError('course_version_mismatch', '课程版本已变化，原进度已保留。请先由管理者处理版本迁移。')
        attempts = state['attempts'] if state else []
        completed = {attempt['module_id'] for attempt in attempts if attempt['status'] == 'completed'}
        started_at = state.get('started_at') if state else None
        if state and not state.get('reset_at'):
            # Reading an initial example is not a start. Old records have the
            # genuine first choice in the existing transactional event log.
            for event in con.execute('SELECT body,created_at FROM events WHERE learner=? AND topic=? ORDER BY created_at', (learner_id, topic_id)):
                if json.loads(event['body'])['type'] == 'example_choice':
                    started_at = min(started_at, event['created_at']) if started_at else event['created_at']
                    break
        status = state['status'] if state and (started_at or state['status'] != 'in_progress') else 'not_started'
        completed_at = None
        if status == 'completed':
            completed_at = state.get('completed_at') or max(
                (attempt['completed_at'] for attempt in attempts if attempt.get('completed_at')), default=None)
        module_count = len(topic['modules'])
        return {'state': state, 'status': status, 'started_at': started_at, 'completed_at': completed_at,
                'mastery': self.dependency_policy.mastery(con, learner_id, topic_id, state),
                'completed_modules': len(completed), 'module_count': module_count,
                'progress': round(100 * len(completed) / module_count, 2)}

    def _task(self, course, topic_id, snapshot, allowed, dependencies=None):
        if dependencies is None:
            dependencies = self._dependencies(course['id'])
        task = {'id': 'lesson:' + topic_id, 'type': 'Lesson', 'topic_id': topic_id,
                'course_id': course['id'], 'course_title': course['title'],
                'title': self.catalog.topics[topic_id]['title'], 'progress': snapshot['progress'],
                'started': snapshot['started_at'] is not None, 'started_at': snapshot['started_at'],
                'status': snapshot['status'], 'prerequisites': [item for item in dependencies.get(topic_id, []) if item in allowed],
                'maintenance': False}
        if snapshot['status'] == 'completed':
            task.update(completed_at=snapshot['completed_at'], result='completed')
        return task

    def _dependencies(self, course_id):
        return self.dependency_policy.dependencies(course_id)

    @staticmethod
    def _has_reviews(con):
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_sessions'").fetchone() is not None

    def _review_task(self, course, topic_id, module_id, *, state=None, due_at=None):
        module = next(item for item in self.catalog.topic(topic_id)['modules'] if item['id'] == module_id)
        state = state or {}
        session_id = state.get('session_id')
        task = {'id': 'review:' + session_id if session_id else 'review-due:' + topic_id + ':' + module_id,
                'type': 'Review', 'topic_id': topic_id, 'module_id': module_id,
                'course_id': course['id'], 'course_title': course['title'],
                'title': self.catalog.topics[topic_id]['title'] + ' · ' + module['title'],
                'module_title': module['title'], 'session_id': session_id,
                'started': bool(session_id), 'started_at': state.get('created_at'),
                'status': state.get('status', 'not_started'), 'due_at': due_at or state.get('due_at'),
                'progress': round(100 * state.get('practice_answered_count', 0) /
                                  max(1, state.get('practice_target_count', 1)), 2),
                'prerequisites': [], 'maintenance': False,
                'start_href': '#/review/' + quote(topic_id, safe='') + '/' + quote(module_id, safe='')}
        if session_id:
            task['start_href'] += '?session=' + quote(session_id, safe='')
        if state.get('status') == 'completed':
            task.update(completed_at=state['completed_at'], mastery=state['mastery'], result='completed', progress=100)
        return task

    def _reviews(self, con, access, course, snapshots):
        tasks, history, wakeups, paused = [], [], [], set()
        if not self._has_reviews(con):
            return tasks, history, None, paused
        current_time = now()
        for topic_id, snapshot in snapshots.items():
            topic = self.catalog.topic(topic_id)
            key = (access['learner_id'], topic_id)
            is_paused = snapshot['status'] == 'paused' or con.execute(
                'SELECT 1 FROM review_topic_pauses WHERE learner=? AND topic=?', key).fetchone()
            if is_paused:
                paused.add(topic_id)
            rows = con.execute('SELECT * FROM review_sessions WHERE learner=? AND topic=? AND version=?',
                               (*key, topic['version'])).fetchall()
            active = None
            for row in rows:
                state = json.loads(row['body'])
                if row['status'] == 'completed':
                    history.append(self._review_task(course, topic_id, row['module'], state=state))
                elif row['status'] == 'in_progress':
                    active = self._review_task(course, topic_id, row['module'], state=state)
            if is_paused:
                continue
            if active:
                tasks.append(active)
                continue
            cards = con.execute('''SELECT * FROM review_cards WHERE learner=? AND topic=? AND version=?
                                   AND active_session IS NULL ORDER BY due_at,module''', (*key, topic['version'])).fetchall()
            for card in cards:
                wakeups.append(card['due_at'])
                if datetime.fromisoformat(card['due_at']) <= datetime.fromisoformat(current_time):
                    tasks.append(self._review_task(course, topic_id, card['module'], due_at=card['due_at']))
        tasks.sort(key=lambda task: (task['due_at'] or '', task['id']))
        return tasks, history, min(wakeups, default=None), paused

    @staticmethod
    def _decode_cursor(cursor, course_id, learner_id):
        if not cursor:
            return None
        try:
            if len(cursor) > 1024:
                raise ValueError('Cursor too long')
            value = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            if (not isinstance(value, list) or len(value) != 4 or value[0] != course_id
                    or value[1] != learner_id or not isinstance(value[2], str) or not isinstance(value[3], str)):
                raise ValueError('Wrong cursor scope')
            return value[2], value[3]
        except (ValueError, TypeError, UnicodeDecodeError):
            raise DomainError('invalid_cursor', '历史分页位置无效，请重新加载。', 422)

    def dashboard(self, access, course_id=None, cursor=None, limit=25, subject_id=None):
        if subject_id is not None and self.subjects:
            self.subjects.authorize_subject(access, subject_id)
        course_id = course_id or self._selection(access, subject_id)
        if not course_id:
            raise DomainError('course_required', '请先选择课程。', 409)
        course = self._course(access, course_id, subject_id)
        allowed = self._allowed_topics(access)
        visible = [topic for topic in course['topics'] if topic['id'] in allowed]
        dependencies = self._dependencies(course_id)
        boundary = self._decode_cursor(cursor, course_id, access['learner_id'])
        with closing(self.access_store._connect()) as con:
            # One consistent SQLite snapshot for progress, tasks and history.
            con.execute('BEGIN')
            snapshots = {topic['id']: self._snapshot(con, access['learner_id'], topic['id']) for topic in visible}
            review_tasks, review_history, next_review_due_at, review_paused = self._reviews(con, access, course, snapshots)
            eligibility = {topic_id: self.dependency_policy.ready(con, access, topic_id, dependencies, now())
                           for topic_id in snapshots}
        def details(topic_id):
            return [{'id': parent, 'title': self.catalog.topics[parent]['title'],
                     'progress': snapshots[parent]['progress'], 'mastery': snapshots[parent]['mastery']}
                    for parent in dependencies.get(topic_id, []) if parent in snapshots]
        review_tasks = [task for task in review_tasks if eligibility[task['topic_id']]]
        for task in [*review_tasks, *review_history]:
            task['prerequisites'] = [parent for parent in dependencies.get(task['topic_id'], []) if parent in snapshots]
            task['prerequisite_details'] = details(task['topic_id'])
            task['dependency_ready'] = eligibility[task['topic_id']]
        total = sum(len(self.catalog.topics[topic['id']]['modules']) for topic in course['topics'])
        completed = sum(item['completed_modules'] for item in snapshots.values())
        progress = round(100 * completed / total, 2) if total else 0
        started = [item['started_at'] for item in snapshots.values() if item['started_at']]
        finished = [item['completed_at'] for item in snapshots.values() if item['completed_at']]
        all_complete = bool(visible) and len(finished) == len(course['topics'])
        overview = {'id': course['id'], 'title': course['title'], 'description': course['description'],
                    'progress': progress, 'start_date': min(started) if started else None,
                    'end_date': max(finished) if all_complete else None, 'estimated_completion': None}
        tasks, history, topics, units = review_tasks, review_history, [], []
        for unit in course['units']:
            members = [topic_id for topic_id in unit['topic_ids'] if topic_id in snapshots]
            if not members:
                continue
            unit_total = sum(snapshots[topic_id]['module_count'] for topic_id in members)
            counts = {status: sum(snapshots[topic_id]['status'] == status for topic_id in members)
                      for status in ('not_started', 'in_progress', 'paused', 'completed')}
            units.append({'id': unit['id'], 'title': unit['title'], 'topic_count': len(members), 'topic_ids': members,
                          'progress': round(100 * sum(snapshots[item]['completed_modules'] for item in members) / unit_total, 2),
                          'status_counts': counts})
        for metadata in visible:
            topic_id, snapshot = metadata['id'], snapshots[metadata['id']]
            task = self._task(course, topic_id, snapshot, allowed, dependencies)
            task.update(prerequisite_details=details(topic_id), dependency_ready=eligibility[topic_id], mastery=snapshot['mastery'])
            if topic_id in review_paused and snapshot['status'] != 'completed':
                task['status'] = 'paused'
            if snapshot['status'] == 'completed':
                history.append(task)
            elif eligibility[topic_id]:
                tasks.append(task)
            topics.append({'id': topic_id, 'title': task['title'], 'status': snapshot['status'], 'progress': snapshot['progress'],
                           'prerequisites': task['prerequisites'],
                           'prerequisite_details': task['prerequisite_details'], 'mastery': snapshot['mastery'],
                           'dependency_ready': eligibility[topic_id],
                           'unit_id': next(unit['id'] for unit in units if topic_id in unit['topic_ids']),
                           'frontier': snapshot['status'] != 'completed' and topic_id not in review_paused and eligibility[topic_id]})
        history.sort(key=lambda task: (task['completed_at'] or '', task['id']), reverse=True)
        if 'review_history' not in access['features']:
            history = []
        # A reset or permission change can remove even a previously loaded older
        # page. Let clients invalidate that cache, regardless of cursor position.
        history_revision = hashlib.sha256(json.dumps(
            [(task['id'], task['completed_at']) for task in history]).encode()).hexdigest()
        if boundary:
            history = [task for task in history if (task['completed_at'] or '', task['id']) < boundary]
        has_more = len(history) > limit
        page = history[:limit]
        next_cursor = None
        if has_more and page:
            next_cursor = base64.urlsafe_b64encode(json.dumps(
                [course_id, access['learner_id'], page[-1]['completed_at'] or '', page[-1]['id']]).encode()).decode().rstrip('=')
        return {'course': overview, 'units': units, 'topics': topics, 'tasks': tasks, 'history': page,
                'history_cursor': next_cursor, 'has_more': has_more, 'history_revision': history_revision,
                'next_review_due_at': next_review_due_at}

    def _task_topic(self, access, task_id):
        if task_id.startswith('review:'):
            with closing(self.access_store._connect()) as con:
                row = con.execute('SELECT topic,version FROM review_sessions WHERE id=? AND learner=?',
                                  (task_id[7:], access['learner_id'])).fetchone() if self._has_reviews(con) else None
            if not row or row['topic'] not in self.catalog.topics:
                raise DomainError('unknown_task', '未找到此学习任务。', 404)
            self.access_store.authorize(access, row['topic'])
            if row['version'] != self.catalog.topic(row['topic'])['version']:
                raise DomainError('course_version_mismatch', '课程版本已变化，原记录已保留。')
            return row['topic']
        if not task_id.startswith('lesson:') or task_id[7:] not in self.catalog.topics:
            raise DomainError('unknown_task', '未找到此学习任务。', 404)
        topic_id = task_id[7:]
        self.access_store.authorize(access, topic_id)
        return topic_id

    def answers(self, access, task_id, subject_id=None):
        self._feature(access, 'review_history')
        if self.subjects:
            self.subjects.authorize_subject(access, subject_id)
        topic_id = self._task_topic(access, task_id)
        if self.subjects:
            self.subjects.authorize_topic(subject_id, topic_id)
        if task_id.startswith('review:'):
            return self._review_answers(access, task_id, topic_id)
        topic = self.catalog.topic(topic_id)
        course = self.catalog.courses[self.catalog.topic_courses[topic_id]]
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            snapshot = self._snapshot(con, access['learner_id'], topic_id)
            rows = con.execute('''SELECT * FROM submissions WHERE learner=? AND topic=? AND status='judged'
                                ORDER BY created_at,id''', (access['learner_id'], topic_id)).fetchall()
            current_attempts = {attempt['id'] for attempt in snapshot['state']['attempts']} if snapshot['state'] else set()
            rows = [row for row in rows if row['attempt'] in current_attempts]
        task = self._task(course, topic_id, snapshot, self._allowed_topics(access))
        if snapshot['status'] != 'completed' or not rows:
            raise DomainError('unknown_task_answers', '此身份尚无该任务的已完成作答记录。', 404)
        groups = []
        for module in topic['modules']:
            questions = {question['id']: question for question in [module['example'], *module['practices']]}
            answers = []
            for row in rows:
                if row['question'] not in questions:
                    continue
                question = questions[row['question']]
                # Explanation is available only with that learner's actual
                # judged answer. Reference answers and locked questions never leave.
                answers.append({'id': row['id'], 'question_id': row['question'], 'html': question['html'],
                                'explanation_html': question['explanation_html'], 'answer': row['answer'],
                                'correct': bool(row['correct']), 'result': 'correct' if row['correct'] else 'incorrect',
                                'answered_at': row['created_at'],
                                'elapsed_seconds': row['elapsed_ms'] / 1000 if row['elapsed_ms'] is not None else None,
                                'difficulty': None})
            if answers:
                groups.append({'id': module['id'], 'title': module['title'], 'answers': answers})
        return {'task': task, 'groups': groups, 'math_css': topic.get('math_css', '')}

    def _review_answers(self, access, task_id, topic_id):
        topic = self.catalog.topic(topic_id)
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            row = con.execute("SELECT * FROM review_sessions WHERE id=? AND learner=? AND topic=? AND status='completed'",
                              (task_id[7:], access['learner_id'], topic_id)).fetchone()
            if not row or row['version'] != topic['version']:
                raise DomainError('unknown_task_answers', '此身份尚无该任务的已完成作答记录。', 404)
            state = json.loads(row['body'])
            rows = con.execute("SELECT * FROM review_submissions WHERE session=? AND learner=? AND topic=? AND status='judged' ORDER BY created_at,id",
                               (row['id'], access['learner_id'], topic_id)).fetchall()
        module = next(item for item in topic['modules'] if item['id'] == row['module'])
        questions = {question['id']: question for question in module['practices']}
        answers = [{'id': answer['id'], 'question_id': answer['question'], 'html': questions[answer['question']]['html'],
                    'explanation_html': questions[answer['question']]['explanation_html'], 'answer': answer['answer'],
                    'correct': bool(answer['correct']), 'result': 'correct' if answer['correct'] else 'incorrect',
                    'answered_at': answer['created_at'], 'difficulty': None,
                    'elapsed_seconds': answer['elapsed_ms'] / 1000 if answer['elapsed_ms'] is not None else None}
                   for answer in rows if answer['question'] in questions]
        course = self.catalog.courses[self.catalog.topic_courses[topic_id]]
        return {'task': self._review_task(course, topic_id, module['id'], state=state),
                'groups': [{'id': module['id'], 'title': module['title'], 'answers': answers}], 'math_css': topic.get('math_css', '')}

    def save_feedback(self, access, values):
        message = values['message'].strip()
        if not message or '\x00' in message:
            raise DomainError('invalid_feedback', '请填写反馈内容。', 422)
        course_id, topic_id, task_id, question_id = (values.get(key) for key in ('course_id', 'topic_id', 'task_id', 'question_id'))
        if task_id:
            task_topic = self._task_topic(access, task_id)
            if topic_id and topic_id != task_topic:
                raise DomainError('invalid_feedback_context', '反馈中的任务和 Topic 不一致。', 422)
            topic_id = task_topic
        if topic_id:
            self.catalog.topic(topic_id)
            self.access_store.authorize(access, topic_id)
            actual_course = self.catalog.topic_courses[topic_id]
            if course_id and course_id != actual_course:
                raise DomainError('invalid_feedback_context', '反馈中的课程和 Topic 不一致。', 422)
            course_id = actual_course
        if course_id:
            self._course(access, course_id)
        if question_id and not topic_id:
            raise DomainError('invalid_feedback_context', '题目反馈需要指定对应 Topic。', 422)
        created_at = now()
        cutoff = (datetime.fromisoformat(created_at) - timedelta(seconds=60)).isoformat()
        feedback_id = str(uuid.uuid4())
        with self.access_store._transaction() as con:
            if question_id:
                snapshot = self._snapshot(con, access['learner_id'], topic_id)
                visited = {step['question_id'] for step in snapshot['state']['steps']} if snapshot['state'] else set()
                if task_id and task_id.startswith('review:'):
                    row = con.execute('SELECT body FROM review_sessions WHERE id=? AND learner=? AND topic=?',
                                      (task_id[7:], access['learner_id'], topic_id)).fetchone()
                    state = json.loads(row['body']) if row else {}
                    visited = set(state.get('order', [])[:state.get('index', -1) + 1])
                if question_id not in visited:
                    raise DomainError('locked_step', '只能反馈已访问的题目。', 403)
            recent = con.execute('SELECT created_at FROM feedback WHERE learner_id=? AND created_at>? ORDER BY created_at',
                                 (access['learner_id'], cutoff)).fetchall()
            if len(recent) >= 5:
                retry = math.ceil((datetime.fromisoformat(recent[0]['created_at']) + timedelta(seconds=60)
                                   - datetime.fromisoformat(created_at)).total_seconds())
                raise PublicLimitError('feedback_rate_limited', '反馈过于频繁，请稍后重试。', retry)
            con.execute('INSERT INTO feedback VALUES (?,?,?,?,?,?,?,?)',
                        (feedback_id, access['learner_id'], message, course_id, topic_id, task_id, question_id, created_at))
        return {'id': feedback_id, 'status': 'saved', 'created_at': created_at}
