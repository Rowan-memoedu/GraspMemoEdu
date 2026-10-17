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
from .question_types import public_interaction, display_answer
from .reference_answers import reference_fields
from .content_reports import validate_correction
from .question_bank import QuestionBank
from .course_hierarchy import pending_hierarchy
from .topic_runtime import is_introduction, learning_units, reread_pending


class DashboardStore:
    def __init__(self, catalog, access_store, subjects=None, *, atomic_clock=None):
        self.atomic_clock = atomic_clock
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
                CREATE TABLE IF NOT EXISTS feedback_corrections (
                    feedback_id TEXT PRIMARY KEY REFERENCES feedback(id),
                    content_block_id TEXT NOT NULL, content_version TEXT NOT NULL,
                    body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS learner_subject_preferences (
                    learner_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    subject_id TEXT NOT NULL, selected_course_id TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(learner_id,subject_id));
            ''')
            if 'course_hierarchy_enabled' not in {row['name'] for row in con.execute('PRAGMA table_info(learner_preferences)')}:
                con.execute('ALTER TABLE learner_preferences ADD COLUMN course_hierarchy_enabled INTEGER NOT NULL DEFAULT 1 CHECK(course_hierarchy_enabled IN (0,1))')
            con.commit()

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
                'selected_course_id': selected,
                'course_hierarchy_enabled': 'course_hierarchy' in access['features'] and (not row or bool(row['course_hierarchy_enabled']))}

    def update_profile(self, access, values):
        if 'course_hierarchy_enabled' in values:
            self._feature(access, 'course_hierarchy')
            if type(values['course_hierarchy_enabled']) is not bool:
                raise DomainError('invalid_profile', '课程层级设置需要布尔值。', 422)
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
            if 'course_hierarchy_enabled' in values:
                con.execute('UPDATE learner_preferences SET course_hierarchy_enabled=? WHERE learner_id=?',
                            (int(values['course_hierarchy_enabled']), access['learner_id']))
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
                             'topic_count': sum(self.access_store.test_materials.topic_visible(t['id'], access) for t in course['topics'])}
                            for course in self.catalog.courses.values() if course['id'] in ids
                            and self.access_store.test_materials.course_visible(course, access)],
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
        if not topic.get('availability', {}).get('trainable', True):
            return {'state': None, 'status': 'read_only', 'started_at': None, 'completed_at': None,
                    'mastery': None, 'completed_modules': 0, 'module_count': max(1, len(topic['modules'])),
                    'progress': 0, 'availability': topic['availability']}
        row = con.execute('SELECT body,version FROM topics WHERE learner=? AND topic=?', (learner_id, topic_id)).fetchone()
        state = json.loads(row['body']) if row else None
        if row and row['version'] != topic['version'] and not is_introduction(topic):
            raise DomainError('course_version_mismatch', '课程版本已变化，原进度已保留。请先由管理者处理版本迁移。')
        if is_introduction(topic):
            if state and state.get('topic_kind') != 'introduction':
                raise DomainError('course_version_mismatch', 'Topic 类型已变化，原进度已保留。')
            done = bool(state and state.get('completed_at'))
            status = 'completed' if done else ('paused' if state and state.get('pause') else
                     'in_progress' if state and state.get('started_at') else 'not_started')
            current = bool(state and state.get('content_version') == topic['content_version'])
            read = len(state.get('read_introductions', [])) if current else 0
            return {'state': state, 'status': status, 'started_at': (state or {}).get('started_at'),
                    'completed_at': (state or {}).get('completed_at'), 'mastery': None,
                    'completed_modules': int(done), 'module_count': 1, 'progress': 100 if done else 0,
                    'reread_pending': reread_pending(topic, state),
                    'reading_progress': round(100 * read / len(topic['introductions']), 2)}
        if topic.get('training_mode') == 'atomic_retrieval':
            count = sum(len(c['points']) for c in topic['memory_chunks'])
            completed = sum(p['mastered'] for p in state['points'].values()) if state else 0
            return {'state': state, 'status': state['status'] if state and state['started_at'] else 'not_started',
                    'started_at': state['started_at'] if state else None,
                    'completed_at': state['completed_at'] if state else None,
                    'mastery': self.dependency_policy.mastery(con, learner_id, topic_id, state),
                    'completed_modules': completed, 'module_count': count, 'progress': round(100 * completed / count, 2)}
        attempts = state['attempts'] if state else []
        module_ids = {module['id'] for module in topic['modules']}
        completed = {attempt['module_id'] for attempt in attempts if attempt['status'] == 'completed' and attempt['module_id'] in module_ids}
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
        if state and state.get('initial_completed_at') and (status != 'completed' or state.get('skipped_modules')):
            completed_at = state['initial_completed_at']
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
        state = snapshot['state'] or {}
        if state.get('initial_completed_at') and snapshot['status'] != 'completed':
            task['retake'] = True
        task.update(skipped_modules=list(state.get('skipped_modules', {})),
                    half_checked=bool(state.get('skipped_modules')),
                    cooldown_until=(state.get('cooldown') or {}).get('resume_at'),
                    locked=bool(state.get('pause')))
        if is_introduction(self.catalog.topic(topic_id)):
            task.update(topic_kind='introduction', reread_pending=snapshot.get('reread_pending', False))
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
        if not self._has_reviews(con) and not any(self.catalog.topic(t).get('training_mode') == 'atomic_retrieval' for t in snapshots):
            return tasks, history, None, paused
        current_time = now()
        for topic_id, snapshot in snapshots.items():
            topic = self.catalog.topic(topic_id)
            if topic.get('training_mode') == 'atomic_retrieval':
                from .atomic_api import review_summary
                state = snapshot['state']
                if state and state['pause']:
                    paused.add(topic_id)
                summary = review_summary(topic, state, self.atomic_clock() if self.atomic_clock else None)
                if summary['next_due_at']:
                    wakeups.append(summary['next_due_at'])
                if summary['due_count'] or summary['active']:
                    tasks.append({'id': 'atomic-review:' + topic_id, 'type': 'Review', 'topic_id': topic_id,
                                  'module_id': 'atomic', 'title': topic['title'], 'course_id': course['id'],
                                  'course_title': course['title'], 'started': summary['active'], 'started_at': None,
                                  'status': 'in_progress' if summary['active'] else 'not_started', 'progress': 0,
                                  'due_at': summary['next_due_at'], 'prerequisites': [], 'maintenance': False,
                                  'start_href': '#/review/' + quote(topic_id, safe='') + '/atomic'})
                continue
            if not self._has_reviews(con):
                continue
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
                cooldown = ((snapshot['state'] or {}).get('cooldown') or {}).get('resume_at')
                if cooldown:
                    wakeups.append(cooldown)
                if snapshot['status'] == 'completed':
                    held = self._task(course, topic_id, snapshot, self._allowed_topics(access))
                    held.update(id='paused:' + topic_id, status='paused', locked=True, result=None, completed_at=None)
                    tasks.append(held)
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
            missing = {topic_id: self.dependency_policy.missing_count(con, access, topic_id, dependencies, now())
                       for topic_id in snapshots}
            eligibility = {topic_id: count == 0 for topic_id, count in missing.items()}
            parent_snapshots = dict(snapshots)
            for parent in {p for tid in snapshots for p in dependencies.get(tid, [])} & allowed:
                if parent not in parent_snapshots:
                    parent_snapshots[parent] = self._snapshot(con, access['learner_id'], parent)
        def details(topic_id):
            return [{'id': parent, 'title': self.catalog.topics[parent]['title'],
                     'progress': parent_snapshots[parent]['progress'], 'mastery': parent_snapshots[parent]['mastery']}
                    for parent in dependencies.get(topic_id, []) if parent in parent_snapshots]
        for task in [*review_tasks, *review_history]:
            task['prerequisites'] = [parent for parent in dependencies.get(task['topic_id'], []) if parent in snapshots]
            task['prerequisite_details'] = details(task['topic_id'])
            task['dependency_ready'] = eligibility[task['topic_id']]
            task['missing_prerequisite_count'] = missing[task['topic_id']]
        def topic_units(topic_id):
            topic = self.catalog.topic(topic_id)
            return learning_units(topic)
        counted = [t for t in course['topics'] if self.access_store.test_materials.topic_visible(t['id'], access)]
        total = sum(topic_units(topic['id']) for topic in counted)
        completed = sum(item['completed_modules'] for item in snapshots.values())
        progress = round(100 * completed / total, 2) if total else 0
        started = [item['started_at'] for item in snapshots.values() if item['started_at']]
        finished = [item['completed_at'] for item in snapshots.values() if item['completed_at']]
        all_complete = bool(visible) and len(finished) == len(counted) and not any(
            (item['state'] or {}).get('skipped_modules') for item in snapshots.values())
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
            task.update(prerequisite_details=details(topic_id), dependency_ready=eligibility[topic_id],
                        missing_prerequisite_count=missing[topic_id], mastery=snapshot['mastery'])
            if topic_id in review_paused and snapshot['status'] != 'completed':
                task.update(status='paused', locked=True)
            if snapshot['status'] == 'paused' and task.get('cooldown_until'):
                next_review_due_at = min(filter(None, [next_review_due_at, task['cooldown_until']]))
            if snapshot['status'] != 'completed' and (snapshot['state'] or {}).get('initial_completed_at'):
                history.append({**task, 'status': 'completed', 'result': 'completed',
                                'completed_at': snapshot['completed_at'], 'locked': False})
            if snapshot['status'] == 'completed':
                history.append(task)
                if snapshot.get('reread_pending'):
                    paused = bool((snapshot['state'] or {}).get('pause'))
                    tasks.append({**task, 'id': 'introduction-update:' + topic_id,
                                  'status': 'paused' if paused else 'in_progress' if snapshot['reading_progress'] else 'not_started',
                                  'progress': snapshot['reading_progress'], 'content_update': True,
                                  'completed_at': None, 'result': None,
                                  'started': snapshot['reading_progress'] > 0})
            elif snapshot['status'] != 'read_only':
                tasks.append(task)
            topics.append({'id': topic_id, 'title': task['title'], 'status': snapshot['status'], 'progress': snapshot['progress'],
                           **({'availability': snapshot['availability']} if 'availability' in snapshot else {}),
                           'prerequisites': task['prerequisites'],
                           'prerequisite_details': task['prerequisite_details'], 'mastery': snapshot['mastery'],
                           'dependency_ready': eligibility[topic_id],
                           'unit_id': next(unit['id'] for unit in units if topic_id in unit['topic_ids']),
                           'frontier': snapshot['status'] not in ('completed', 'read_only') and topic_id not in review_paused and eligibility[topic_id]})
        # Stable ordering preserves existing Review/Lesson priority for ready tasks
        # and catalog/due order for equally blocked tasks.
        tasks.sort(key=lambda task: (not task['dependency_ready'], task['missing_prerequisite_count']))
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
        hierarchy_enabled = self.profile(access)['course_hierarchy_enabled']
        return {'course': overview, 'units': units, 'topics': topics, 'tasks': tasks, 'history': page,
                'course_hierarchy_enabled': hierarchy_enabled,
                'pending_hierarchy': pending_hierarchy(course['units'], tasks) if hierarchy_enabled else [],
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
        course = self.catalog.course_context(topic_id)
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            snapshot = self._snapshot(con, access['learner_id'], topic_id)
            rows = con.execute('''SELECT * FROM submissions WHERE learner=? AND topic=? AND status='judged'
                                ORDER BY created_at,id''', (access['learner_id'], topic_id)).fetchall()
            if topic.get('training_mode') == 'atomic_retrieval':
                state = snapshot['state']
                if not state or not state['completed_at']:
                    raise DomainError('unknown_task_answers', '此身份尚无该任务的已完成作答记录。', 404)
                questions = {q['id']: q for q in topic['flashcards']}
                groups = []
                for chunk in topic['memory_chunks']:
                    points = {p['id'] for p in chunk['points']}
                    records = [r for r in state['history'] if r['mode'] == 'learn' and set(r['point_ids']) <= points]
                    answers = [{'id': r['id'], 'question_id': r['card_id'], 'html': questions[r['card_id']]['html'],
                                'interaction': public_interaction(questions[r['card_id']]), 'content_version': topic['version'],
                                'explanation_html': questions[r['card_id']]['explanation_html'], 'answer': r['answer'],
                                **reference_fields(questions[r['card_id']], True),
                                'answer_display': display_answer(questions[r['card_id']], r['answer']),
                                'correct': r['correct'], 'reason': r['reason'],
                                'result': 'correct' if r['correct'] else 'incorrect', 'answered_at': r['answered_at'],
                                'elapsed_seconds': None, 'difficulty': None} for r in records]
                    groups.append({'id': chunk['id'], 'title': chunk['title'], 'answers': answers})
                return {'task': self._task(course, topic_id, snapshot, self._allowed_topics(access)),
                        'groups': groups, 'math_css': topic.get('math_css', '')}
            current_attempts = {attempt['id'] for attempt in snapshot['state']['attempts']} if snapshot['state'] else set()
            rows = [row for row in rows if row['attempt'] in current_attempts]
        task = self._task(course, topic_id, snapshot, self._allowed_topics(access))
        initially_done = bool((snapshot['state'] or {}).get('initial_completed_at'))
        if (snapshot['status'] != 'completed' and not initially_done) or (not rows and not task.get('skipped_modules')):
            raise DomainError('unknown_task_answers', '此身份尚无该任务的已完成作答记录。', 404)
        if initially_done:
            task.update(status='completed', result='completed', completed_at=snapshot['completed_at'])
        groups = []
        bank = QuestionBank(topic, self.access_store.data_dir)
        steps = {s['id']: s for s in snapshot['state']['steps']}
        for module in topic['modules']:
            questions = {question['id']: question for question in [module['example'], *module['practices']]}
            answers = []
            for row in rows:
                if row['question'] not in questions:
                    continue
                step = steps[row['step']]
                question = bank.question(row['question'], step.get('question_bank_revision'), step['id'])
                # Explanation is available only with that learner's actual
                # judged answer. Locked questions never leave.
                answers.append({'id': row['id'], 'question_id': row['question'], 'html': question['html'],
                                'interaction': public_interaction(question), 'content_version': step.get('question_bank_revision') or topic['version'],
                                'answer_display': display_answer(question, row['answer']), 'reason': row['reason'],
                                'explanation_html': question['explanation_html'], 'answer': row['answer'],
                                **reference_fields(question, True),
                                'correct': bool(row['correct']), 'result': 'correct' if row['correct'] else 'incorrect',
                                'answered_at': row['created_at'],
                                'elapsed_seconds': row['elapsed_ms'] / 1000 if row['elapsed_ms'] is not None else None,
                                'difficulty': None})
            if answers:
                groups.append({'id': module['id'], 'title': module['title'], 'answers': answers})
        return {'task': task, 'groups': groups, 'math_css': topic.get('math_css', '') + bank.css(s.get('question_bank_revision') for s in steps.values())}

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
        bank = QuestionBank(topic, self.access_store.data_dir)
        revision = state.get('question_bank_revision')
        questions = {q['id']: bank.question(q['id'], revision, state['session_id'] + ':' + q['id']) for q in module['practices']}
        answers = [{'id': answer['id'], 'question_id': answer['question'], 'html': questions[answer['question']]['html'],
                    'interaction': public_interaction(questions[answer['question']]), 'content_version': revision or topic['version'],
                    'answer_display': display_answer(questions[answer['question']], answer['answer']), 'reason': answer['reason'],
                    'explanation_html': questions[answer['question']]['explanation_html'], 'answer': answer['answer'],
                    **reference_fields(questions[answer['question']], True),
                    'correct': bool(answer['correct']), 'result': 'correct' if answer['correct'] else 'incorrect',
                    'answered_at': answer['created_at'], 'difficulty': None,
                    'elapsed_seconds': answer['elapsed_ms'] / 1000 if answer['elapsed_ms'] is not None else None}
                   for answer in rows if answer['question'] in questions]
        course = self.catalog.course_context(topic_id)
        return {'task': self._review_task(course, topic_id, module['id'], state=state),
                'groups': [{'id': module['id'], 'title': module['title'], 'answers': answers}], 'math_css': topic.get('math_css', '') + bank.css([revision])}

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
            actual_courses = self.catalog.topic_courses[topic_id]
            if course_id and course_id not in actual_courses:
                raise DomainError('invalid_feedback_context', '反馈中的课程和 Topic 不一致。', 422)
            course_id = course_id or self.catalog.course_context(topic_id)['id']
        if course_id:
            self._course(access, course_id)
        if question_id and not topic_id:
            raise DomainError('invalid_feedback_context', '题目反馈需要指定对应 Topic。', 422)
        correction = values.get('correction')
        if correction:
            if not topic_id:
                raise DomainError('invalid_feedback_context', '文字纠错需要指定对应 Topic。', 422)
            if not question_id:
                validate_correction(self.catalog.topic(topic_id), question_id, correction)
        created_at = now()
        cutoff = (datetime.fromisoformat(created_at) - timedelta(seconds=60)).isoformat()
        feedback_id = str(uuid.uuid4())
        with self.access_store._transaction() as con:
            if question_id:
                snapshot = self._snapshot(con, access['learner_id'], topic_id)
                saved = snapshot['state']
                if saved and saved.get('training_mode') == 'atomic_retrieval':
                    records = saved['history'] + ([saved['active']] if saved['active'] else [])
                    visited = {record['card_id'] for record in records}
                else:
                    visited = {step['question_id'] for step in saved['steps']} if saved else set()
                if task_id and task_id.startswith('review:'):
                    row = con.execute('SELECT body FROM review_sessions WHERE id=? AND learner=? AND topic=?',
                                      (task_id[7:], access['learner_id'], topic_id)).fetchone()
                    state = json.loads(row['body']) if row else {}
                    visited = set(state.get('order', [])[:state.get('index', -1) + 1])
                if question_id not in visited:
                    raise DomainError('locked_step', '只能反馈已访问的题目。', 403)
                if correction:
                    topic = self.catalog.topic(topic_id)
                    revision = correction['content_version']
                    if saved and saved.get('training_mode') == 'atomic_retrieval':
                        validate_correction(topic, question_id, correction)
                    else:
                        allowed = ({state.get('question_bank_revision') or topic['version']}
                                   if task_id and task_id.startswith('review:') else
                                   {s.get('question_bank_revision') or topic['version'] for s in saved['steps'] if s['question_id'] == question_id})
                        if revision not in allowed:
                            raise DomainError('invalid_correction', '只能纠错本人已访问的题目版本。', 409)
                        question = QuestionBank(topic, self.access_store.data_dir).question(
                            question_id, revision if revision != topic['version'] else None)
                        validate_correction(topic, question_id, correction, question=question, content_version=revision)
            recent = con.execute('SELECT created_at FROM feedback WHERE learner_id=? AND created_at>? ORDER BY created_at',
                                 (access['learner_id'], cutoff)).fetchall()
            if len(recent) >= 5:
                retry = math.ceil((datetime.fromisoformat(recent[0]['created_at']) + timedelta(seconds=60)
                                   - datetime.fromisoformat(created_at)).total_seconds())
                raise PublicLimitError('feedback_rate_limited', '反馈过于频繁，请稍后重试。', retry)
            con.execute('INSERT INTO feedback VALUES (?,?,?,?,?,?,?,?)',
                        (feedback_id, access['learner_id'], message, course_id, topic_id, task_id, question_id, created_at))
            if correction:
                con.execute('INSERT INTO feedback_corrections VALUES (?,?,?,?)',
                            (feedback_id, correction['content_block_id'], correction['content_version'],
                             json.dumps(correction, ensure_ascii=False)))
        return {'id': feedback_id, 'status': 'saved', 'created_at': created_at}
