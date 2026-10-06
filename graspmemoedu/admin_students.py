"""Administrator-only projections over persisted student progress and schedules.

No learner services are created and no scheduler or progress migration is run.
Atomic card variants project their points' shared schedules, not separate cards.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Literal

from fastapi import Query, Request

from .atomic_content import GAP_SECONDS
from .service import DomainError
from .training_content import permission_id
from .student_reports import StudentReport, time_zone
from .access import admin_display_name


def public_route(path):
    return bool(re.fullmatch(r'/api/(?:admin/students(?:/[A-Za-z0-9_-]{1,128}(?:/(?:questions|history))?)?|student-report(?:/(?:questions|history))?)', path))


class AdminStudents:
    def __init__(self, catalog, access_store, subjects, dashboard, training, clock=None):
        self.catalog, self.access, self.subjects = catalog, access_store, subjects
        self.dashboard, self.training = dashboard, training
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def require_admin(access):
        if not access.get('is_admin'):
            raise DomainError('feature_forbidden', '此页面仅供管理员使用。', 403)

    @staticmethod
    def student(row, own=False):
        return {key: row[key] for key in ('learner_id', 'purpose')} | {
            'display_name': row['display_name'] if own else admin_display_name(row),
            'enabled': bool(row['enabled']), 'is_advanced_learner': bool(row['is_advanced_learner'])}

    def require_viewer(self, access, learner_id, own=False):
        if not own:
            self.require_admin(access)
        elif (access.get('role') != 'account' or not access.get('is_student') or access.get('is_admin')
              or learner_id != access.get('learner_id')):
            raise DomainError('feature_forbidden', '个人学习情况仅供学员查看本人记录。', 403)

    @staticmethod
    def started(state):
        return bool(state.get('started_at') or state.get('completed_at') or state.get('introduction_read')
                    or state.get('read_introductions') or state.get('seen_chunks') or state.get('history')
                    or any(a.get('learning_path') or a.get('example_result') or a['status'] == 'completed' for a in state.get('attempts', [])))

    def students(self, access, limit=25, offset=0, query=''):
        self.require_admin(access)
        with closing(self.access._connect()) as con:
            con.execute('PRAGMA query_only=ON')
            con.execute('BEGIN')
            # Search is literal: LIKE metacharacters in a name do not broaden it.
            where = "role='account' AND is_student=1 AND is_admin=0"
            args = []
            if query:
                where += " AND instr(lower(COALESCE(NULLIF(admin_name,''),display_name)),lower(?))>0"; args.append(query)
            total = con.execute('SELECT COUNT(*) FROM access_principals WHERE ' + where, args).fetchone()[0]
            rows = con.execute('SELECT * FROM access_principals WHERE ' + where +
                               " ORDER BY COALESCE(NULLIF(admin_name,''),display_name),learner_id LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
            items = []
            for row in rows:
                item = self.student(row)
                states = [json.loads(r['body']) for r in con.execute('SELECT topic,body FROM topics WHERE learner=?', (row['learner_id'],))
                          if self.access.test_materials.topic_visible(r['topic'], access)]
                item.update(started_topics=sum(self.started(s) for s in states),
                            completed_topics=sum(s.get('status') == 'completed' for s in states))
                items.append(item)
        return {'items': items, 'total': total, 'next_offset': offset + limit if offset + limit < total else None}

    def context(self, topic_id):
        if topic_id not in self.catalog.topics:
            return {'topic_id': topic_id, 'topic_title': topic_id, 'course_title': '', 'subject_id': None}
        topic = self.catalog.topic(topic_id)
        course = self.catalog.courses[self.catalog.topic_courses[topic_id]]
        return {'topic_id': topic_id, 'topic_title': topic['title'], 'course_title': course['title'],
                'subject_id': self.subjects.course_subjects[course['id']]}

    def blockers(self, con, student, topic_id, state, version, tables, current):
        result = []
        allowed = {t['id'] for t in student['topics']}
        if not student['enabled']: result.append('account_disabled')
        if topic_id not in allowed: result.append('topic_forbidden')
        if not {'learn', 'submit_answer'} <= set(student['features']): result.append('feature_forbidden')
        topic = self.catalog.topics.get(topic_id)
        if not topic or version != topic['version']:
            result.append('old_version')
        else:
            if (state or {}).get('pause') or ('review_topic_pauses' in tables and con.execute(
                'SELECT 1 FROM review_topic_pauses WHERE learner=? AND topic=?', (student['learner_id'], topic_id)).fetchone()):
                result.append('paused')
            if not self.dashboard.dependency_policy.ready(con, student, topic_id, current_time=current.isoformat()):
                result.append('prerequisites')
        return result

    def learned_topic(self, topic_id, row):
        state = json.loads(row['body'])
        topic = self.catalog.topics.get(topic_id, {})
        current = bool(topic and topic['version'] == row['version'])
        item = self.context(topic_id) | {'status': state.get('status'), 'started_at': state.get('started_at'),
            'completed_at': state.get('completed_at'), 'version_current': current, 'paused': bool(state.get('pause')),
            'guidance_read': bool(state.get('introduction_read') or state.get('read_introductions') or state.get('seen_chunks')),
            'guidance_titles': [], 'modules': [], 'points': []}
        for intro in topic.get('introductions', [topic.get('introduction', {})]):
            if intro and (intro.get('id') in state.get('read_introductions', []) or state.get('introduction_read')):
                item['guidance_titles'].append(intro.get('title', 'Introduction'))
        definitions = {m['id']: m for m in topic.get('modules', [])}
        by_module = {}
        for attempt in state.get('attempts', []):
            if not attempt.get('learning_path') and not attempt.get('example_result') and attempt['status'] == 'in_progress':
                continue
            module_id = attempt['module_id']
            record = {'id': module_id, 'title': definitions.get(module_id, {}).get('title', module_id),
                'status': attempt['status'], 'mastery': attempt.get('mastery'), 'completed_at': attempt.get('completed_at')}
            # Completed knowledge stays completed even if later practice is active.
            if module_id not in by_module or attempt['status'] == 'completed' or by_module[module_id]['status'] != 'completed':
                by_module[module_id] = record
        item['modules'] = list(by_module.values())
        known_points = {p['id']: p for c in topic.get('memory_chunks', []) for p in c['points']}
        for point_id, memory in state.get('points', {}).items():
            if memory.get('mastered') or memory.get('card_json') or memory.get('first_passed_at'):
                item['points'].append({'id': point_id, 'text': known_points.get(point_id, {}).get('text', point_id),
                    'mastered': bool(memory.get('mastered')), 'first_passed_at': memory.get('first_passed_at')})
        if not item['started_at'] and state.get('attempts'):
            item['started_at'] = min((a.get('created_at') or a.get('started_at') or '' for a in state['attempts']), default=None) or None
        return item

    def detail(self, access, learner_id, days=30, timezone='UTC', own=False):
        self.require_viewer(access, learner_id, own)
        time_zone(timezone)
        current = self.clock()
        with closing(self.access._connect()) as con:
            con.execute('PRAGMA query_only=ON')
            con.execute('BEGIN')
            row = con.execute("SELECT * FROM access_principals WHERE learner_id=? AND role='account' AND is_student=1 AND is_admin=0", (learner_id,)).fetchone()
            if not row:
                raise DomainError('student_not_found', '未找到该学员。', 404)
            student = self.access._public(row)
            tables = self.dashboard.dependency_policy.tables(con)
            rows = {r['topic']: r for r in con.execute('SELECT topic,version,body FROM topics WHERE learner=?', (learner_id,))
                    if self.access.test_materials.topic_visible(r['topic'], access)}
            states = {key: json.loads(r['body']) for key, r in rows.items()}
            learned = [self.learned_topic(key, r) for key, r in rows.items()
                       if self.started(states[key])]
            reviews, cards = [], []
            if 'review_cards' in tables:
                for card in con.execute('SELECT * FROM review_cards WHERE learner=? ORDER BY due_at,topic,module', (learner_id,)):
                    if not self.access.test_materials.topic_visible(card['topic'], access):
                        continue
                    context = self.context(card['topic'])
                    topic = self.catalog.topics.get(card['topic'], {})
                    title = next((m['title'] for m in topic.get('modules', []) if m['id'] == card['module']), card['module'])
                    blocked = self.blockers(con, student, card['topic'], states.get(card['topic']), card['version'], tables, current)
                    active = None
                    if 'review_sessions' in tables:
                        active = con.execute("SELECT id,module FROM review_sessions WHERE learner=? AND topic=? AND status='in_progress'", (learner_id, card['topic'])).fetchone()
                    if active and active['id'] != card['active_session']: blocked.append('another_review_active')
                    reviews.append(context | {'kind': 'module', 'id': card['module'], 'title': title,
                        'due_at': card['due_at'], 'stored_due_at': card['due_at'], 'active': bool(card['active_session']),
                        'blockers': blocked, 'overdue': datetime.fromisoformat(card['due_at']) <= current})
            for topic_id, topic in self.catalog.topics.items():
                if not self.access.test_materials.topic_visible(topic_id, access):
                    continue
                if topic.get('training_mode') != 'atomic_retrieval' or (topic_id not in rows and topic_id not in {t['id'] for t in student['topics']}):
                    continue
                state = states.get(topic_id, {})
                points = state.get('points', {})
                blocked = self.blockers(con, student, topic_id, state, rows[topic_id]['version'] if topic_id in rows else topic['version'], tables, current)
                if not state.get('completed_at'): blocked.append('initial_learning')
                dates = {}
                for chunk in topic['memory_chunks']:
                    last = state.get('chunk_last_seen', {}).get(chunk['id'])
                    for point in chunk['points']:
                        memory = points.get(point['id'], {})
                        stored = memory.get('due_at')
                        due = datetime.fromisoformat(stored) if stored else None
                        if due and last: due = max(due, datetime.fromisoformat(last) + timedelta(seconds=GAP_SECONDS))
                        dates[point['id']] = due.isoformat() if due else None
                        if stored:
                            active = state.get('mode') == 'review' and point['id'] in (state.get('active') or {}).get('point_ids', [])
                            reviews.append(self.context(topic_id) | {'kind': 'atomic_point', 'id': point['id'],
                                'title': point['text'], 'chunk_title': chunk['title'], 'due_at': dates[point['id']],
                                'stored_due_at': stored, 'active': active, 'mastered': bool(memory.get('mastered')),
                                'blockers': blocked[:], 'overdue': due <= current})
                for flashcard in topic['flashcards']:
                    point_dates = [{'id': pid, 'due_at': dates.get(pid), 'mastered': bool(points.get(pid, {}).get('mastered'))} for pid in flashcard['point_ids']]
                    all_scheduled = all(p['due_at'] and p['mastered'] for p in point_dates)
                    cards.append(self.context(topic_id) | {'id': flashcard['id'], 'title': flashcard['title'],
                        'point_ids': flashcard['point_ids'], 'points': point_dates,
                        'due_at': max((p['due_at'] for p in point_dates), key=datetime.fromisoformat, default=None) if all_scheduled else None,
                        'blockers': blocked[:], 'bundle': len(point_dates) > 1})
            training_learned, training_reviews = self.training_records(con, student, tables, current, access)
            reviews.extend(training_reviews)
            analytics = StudentReport(self, con, access, student, rows, tables, current, own).build(reviews, days, timezone)
            if own:
                for item in learned:
                    for point in item['points']:
                        if not point['mastered']: point['text'] = point['id']
                for item in reviews:
                    if item['kind'] == 'atomic_point' and not item['mastered']: item['title'] = item['id']
                for item in cards:
                    if not all(point['mastered'] for point in item['points']): item['title'] = item['id']
        reviews.sort(key=lambda r: (r['due_at'] or '', r['kind'], r['id']))
        learned.sort(key=lambda r: (r['started_at'] or '', r['topic_id']), reverse=True)
        return {'student': self.student(row, own), 'generated_at': current.isoformat(), 'learned': learned,
                'reviews': reviews, 'atomic_cards': cards, 'training_learned': training_learned, 'analytics': analytics}

    def report(self, access, learner_id, view, *, own=False, **options):
        self.require_viewer(access, learner_id, own)
        with closing(self.access._connect()) as con:
            con.execute('PRAGMA query_only=ON')
            con.execute('BEGIN')
            row = con.execute("SELECT * FROM access_principals WHERE learner_id=? AND role='account' AND is_student=1 AND is_admin=0", (learner_id,)).fetchone()
            if not row:
                raise DomainError('student_not_found', '未找到该学员。', 404)
            rows = {r['topic']: r for r in con.execute('SELECT topic,version,body FROM topics WHERE learner=?', (learner_id,))
                    if self.access.test_materials.topic_visible(r['topic'], access)}
            report = StudentReport(self, con, access, self.access._public(row), rows,
                                   self.dashboard.dependency_policy.tables(con), self.clock(), own)
            kind, container = options['kind'], options.get('container')
            if container and not report.visible('training' if kind == 'notebook' else kind, container):
                raise DomainError('unknown_material', '未找到此内容。', 404)
            if view == 'history':
                return report.history(**options)
            entries = report.question_rows(None if kind == 'notebook' else kind, container, notebook=kind == 'notebook')
            state, query = options['state'], options['q'].casefold()
            entries = [e for e in entries if (not query or query in (e['title'] + ' ' + e['group_title'] + ' ' + e['container_title']).casefold())
                       and (not options.get('group') or e['group_id'] == options['group'])
                       and (state == 'all' or state == 'due' and e['due'] or state == 'stopped' and e['stopped']
                            or state == 'unanswered' and not e['stats']['attempts']
                            or state == 'wrong' and (e['stats']['wrong'] or e['stats']['self_rated'] > e['stats']['self_passed']))]
            offset, limit = options['offset'], options['limit']
            return {'items': entries[offset:offset + limit], 'total': len(entries),
                    'next_offset': offset + limit if offset + limit < len(entries) else None}

    def training_records(self, con, student, tables, current, access):
        learned, reviews = [], []
        learner_id = student['learner_id']
        if 'training_attempts' in tables:
            for row in con.execute('''SELECT bank,question,version,MAX(completed_at) AS completed_at,COUNT(*) AS attempts
                FROM training_attempts WHERE learner=? AND completed_at IS NOT NULL GROUP BY bank,question,version''', (learner_id,)):
                if not self.access.test_materials.topic_visible(permission_id(row['bank']), access):
                    continue
                bank, title = self.training_title(row['bank'], row['question'])
                learned.append({'bank_id': row['bank'], 'bank_title': bank.get('title', row['bank']),
                    'subject_id': bank.get('subject_id'), 'id': row['question'], 'title': title,
                    'completed_at': row['completed_at'], 'attempts': row['attempts']})
        if 'training_memory' in tables:
            for row in con.execute('SELECT * FROM training_memory WHERE learner=? ORDER BY due_at,bank,question', (learner_id,)):
                if not self.access.test_materials.topic_visible(permission_id(row['bank']), access):
                    continue
                bank, title = self.training_title(row['bank'], row['question'])
                blocked = []
                if not student['enabled']: blocked.append('account_disabled')
                if permission_id(row['bank']) not in {t['id'] for t in student['topics']}: blocked.append('topic_forbidden')
                if not {'learn', 'submit_answer'} <= set(student['features']): blocked.append('feature_forbidden')
                if not bank or row['version'] != bank['version']: blocked.append('old_version')
                if 'training_suspensions' in tables and con.execute('SELECT 1 FROM training_suspensions WHERE learner=? AND bank=? AND question=? AND version=?',
                    (learner_id, row['bank'], row['question'], row['version'])).fetchone(): blocked.append('stopped')
                active = con.execute("SELECT 1 FROM training_attempts WHERE learner=? AND bank=? AND question=? AND version=? AND mode='review' AND phase NOT IN ('done','completed')",
                    (learner_id, row['bank'], row['question'], row['version'])).fetchone() if 'training_attempts' in tables else None
                reviews.append({'kind': 'training', 'id': row['question'], 'title': title,
                    'bank_id': row['bank'], 'course_title': bank.get('title', row['bank']), 'topic_title': '',
                    'subject_id': bank.get('subject_id'), 'due_at': row['due_at'], 'stored_due_at': row['due_at'],
                    'mastered': bool(row['mastered']), 'active': bool(active), 'blockers': blocked,
                    'overdue': datetime.fromisoformat(row['due_at']) <= current})
        return learned, reviews

    def training_title(self, bank_id, question_id):
        bank = self.training.banks.get(bank_id, {})
        title = next((q.get('title', q['id']) for group in bank.get('groups', [])
                      for q in group.get('questions', []) if q['id'] == question_id), question_id)
        return bank, title


def register_admin_student_routes(app, catalog, access_store, subjects, dashboard, training, clock=None):
    store = AdminStudents(catalog, access_store, subjects, dashboard, training, clock)
    app.state.admin_students = store

    @app.get('/api/student-report')
    def personal_detail(request: Request, days: int = Query(default=30, ge=1, le=366),
                        timezone: str = Query(default='UTC', max_length=100)):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return store.detail(access, access['learner_id'], days, timezone, own=True)

    @app.get('/api/student-report/questions')
    def personal_questions(request: Request, kind: Literal['topic', 'training', 'notebook'],
                           container: str | None = Query(default=None, max_length=128), group: str | None = Query(default=None, max_length=128),
                           state: Literal['all', 'due', 'stopped', 'unanswered', 'wrong'] = 'all',
                           q: str = Query(default='', max_length=100), offset: int = Query(default=0, ge=0, le=1000000),
                           limit: int = Query(default=30, ge=1, le=100)):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return store.report(access, access['learner_id'], 'questions', own=True,
                            kind=kind, container=container, group=group, state=state, q=q, offset=offset, limit=limit)

    @app.get('/api/student-report/history')
    def personal_history(request: Request, kind: Literal['topic', 'training'],
                         container: str = Query(min_length=1, max_length=128), question: str | None = Query(default=None, max_length=128),
                         version: str | None = Query(default=None, max_length=128), offset: int = Query(default=0, ge=0, le=1000000),
                         point: str | None = Query(default=None, max_length=128), limit: int = Query(default=10, ge=1, le=30)):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return store.report(access, access['learner_id'], 'history', own=True,
                            kind=kind, container=container, question=question, version=version, point=point, offset=offset, limit=limit)

    @app.get('/api/admin/students')
    def students(request: Request, limit: int = Query(default=25, ge=1, le=100),
                 offset: int = Query(default=0, ge=0, le=1000000), q: str = Query(default='', max_length=100)):
        return store.students(access_store.resolve(request.headers.get('x-learning-identity')), limit, offset, q)

    @app.get('/api/admin/students/{learner_id}')
    def detail(learner_id: str, request: Request, days: int = Query(default=30, ge=1, le=366),
               timezone: str = Query(default='UTC', max_length=100)):
        return store.detail(access_store.resolve(request.headers.get('x-learning-identity')), learner_id, days, timezone)

    @app.get('/api/admin/students/{learner_id}/questions')
    def questions(learner_id: str, request: Request, kind: Literal['topic', 'training', 'notebook'],
                  container: str | None = Query(default=None, max_length=128), group: str | None = Query(default=None, max_length=128),
                  state: Literal['all', 'due', 'stopped', 'unanswered', 'wrong'] = 'all',
                  q: str = Query(default='', max_length=100), offset: int = Query(default=0, ge=0, le=1000000),
                  limit: int = Query(default=30, ge=1, le=100)):
        return store.report(access_store.resolve(request.headers.get('x-learning-identity')), learner_id, 'questions',
                            kind=kind, container=container, group=group, state=state, q=q, offset=offset, limit=limit)

    @app.get('/api/admin/students/{learner_id}/history')
    def history(learner_id: str, request: Request, kind: Literal['topic', 'training'],
                container: str = Query(min_length=1, max_length=128), question: str | None = Query(default=None, max_length=128),
                version: str | None = Query(default=None, max_length=128), offset: int = Query(default=0, ge=0, le=1000000),
                point: str | None = Query(default=None, max_length=128),
                limit: int = Query(default=10, ge=1, le=30)):
        return store.report(access_store.resolve(request.headers.get('x-learning-identity')), learner_id, 'history',
                            kind=kind, container=container, question=question, version=version, point=point, offset=offset, limit=limit)
