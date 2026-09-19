"""Course-level projections and personal preferences in the learning database."""

from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timedelta
import json
import math
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .public_limits import PublicLimitError
from .service import DomainError, now


class DashboardStore:
    def __init__(self, catalog, access_store):
        self.catalog, self.access_store = catalog, access_store
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
            ''')

    def _allowed_topics(self, access):
        return {topic['id'] for topic in access['topics']}

    @staticmethod
    def _feature(access, feature):
        if feature not in access['features']:
            raise DomainError('feature_forbidden', '当前账号没有使用此功能的权限。', 403)

    def _course(self, access, course_id):
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

    def catalog_view(self, access):
        allowed = self._allowed_topics(access)
        return {'courses': [{'id': course['id'], 'title': course['title'], 'description': course['description'],
                             'available': bool(allowed.intersection(topic['id'] for topic in course['topics'])),
                             'topic_count': len(course['topics'])} for course in self.catalog.courses.values()],
                'selected_course_id': self.profile(access)['selected_course_id']}

    def select_course(self, access, course_id):
        self._course(access, course_id)
        with self.access_store._transaction() as con:
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
        if state:
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
                'completed_modules': len(completed), 'module_count': module_count,
                'progress': round(100 * len(completed) / module_count, 2)}

    def _task(self, course, topic_id, snapshot, allowed):
        metadata = self.catalog.metadata(topic_id)
        task = {'id': 'lesson:' + topic_id, 'type': 'Lesson', 'topic_id': topic_id,
                'course_id': course['id'], 'course_title': course['title'],
                'title': self.catalog.topics[topic_id]['title'], 'progress': snapshot['progress'],
                'started': snapshot['started_at'] is not None, 'started_at': snapshot['started_at'],
                'status': snapshot['status'], 'prerequisites': [item for item in metadata['prerequisites'] if item in allowed],
                'maintenance': False}
        if snapshot['status'] == 'completed':
            task.update(completed_at=snapshot['completed_at'], result='completed')
        return task

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

    def dashboard(self, access, course_id=None, cursor=None, limit=25):
        course_id = course_id or self.profile(access)['selected_course_id']
        if not course_id:
            raise DomainError('course_required', '请先选择课程。', 409)
        course = self._course(access, course_id)
        allowed = self._allowed_topics(access)
        visible = [topic for topic in course['topics'] if topic['id'] in allowed]
        boundary = self._decode_cursor(cursor, course_id, access['learner_id'])
        with closing(self.access_store._connect()) as con:
            # One consistent SQLite snapshot for progress, tasks and history.
            con.execute('BEGIN')
            snapshots = {topic['id']: self._snapshot(con, access['learner_id'], topic['id']) for topic in visible}
        total = sum(len(self.catalog.topics[topic['id']]['modules']) for topic in course['topics'])
        completed = sum(item['completed_modules'] for item in snapshots.values())
        progress = round(100 * completed / total, 2) if total else 0
        started = [item['started_at'] for item in snapshots.values() if item['started_at']]
        finished = [item['completed_at'] for item in snapshots.values() if item['completed_at']]
        all_complete = bool(visible) and len(finished) == len(course['topics'])
        overview = {'id': course['id'], 'title': course['title'], 'description': course['description'],
                    'progress': progress, 'start_date': min(started) if started else None,
                    'end_date': max(finished) if all_complete else None, 'estimated_completion': None}
        tasks, history, topics, units = [], [], [], []
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
            task = self._task(course, topic_id, snapshot, allowed)
            (history if snapshot['status'] == 'completed' else tasks).append(task)
            topics.append({'id': topic_id, 'title': task['title'], 'status': snapshot['status'], 'progress': snapshot['progress'],
                           'prerequisites': task['prerequisites'],
                           'unit_id': next(unit['id'] for unit in units if topic_id in unit['topic_ids']),
                           'frontier': snapshot['status'] != 'completed' and all(
                               item in snapshots and snapshots[item]['status'] == 'completed' for item in metadata['prerequisites'])})
        history.sort(key=lambda task: (task['completed_at'] or '', task['id']), reverse=True)
        if 'review_history' not in access['features']:
            history = []
        if boundary:
            history = [task for task in history if (task['completed_at'] or '', task['id']) < boundary]
        has_more = len(history) > limit
        page = history[:limit]
        next_cursor = None
        if has_more and page:
            next_cursor = base64.urlsafe_b64encode(json.dumps(
                [course_id, access['learner_id'], page[-1]['completed_at'] or '', page[-1]['id']]).encode()).decode().rstrip('=')
        return {'course': overview, 'units': units, 'topics': topics, 'tasks': tasks, 'history': page,
                'history_cursor': next_cursor, 'has_more': has_more}

    def _task_topic(self, access, task_id):
        if not task_id.startswith('lesson:') or task_id[7:] not in self.catalog.topics:
            raise DomainError('unknown_task', '未找到此学习任务。', 404)
        topic_id = task_id[7:]
        self.access_store.authorize(access, topic_id)
        return topic_id

    def answers(self, access, task_id):
        self._feature(access, 'review_history')
        topic_id = self._task_topic(access, task_id)
        topic = self.catalog.topic(topic_id)
        course = self.catalog.courses[self.catalog.topic_courses[topic_id]]
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            snapshot = self._snapshot(con, access['learner_id'], topic_id)
            rows = con.execute('''SELECT * FROM submissions WHERE learner=? AND topic=? AND status='judged'
                                ORDER BY created_at,id''', (access['learner_id'], topic_id)).fetchall()
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
