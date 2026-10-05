"""Administrator-only projections over persisted student progress and schedules.

No learner services are created and no scheduler or progress migration is run.
Atomic card variants project their points' shared schedules, not separate cards.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import re

from fastapi import Query, Request

from .atomic_content import GAP_SECONDS
from .service import DomainError
from .training_content import permission_id


def public_route(path):
    return bool(re.fullmatch(r'/api/admin/students(?:/[A-Za-z0-9_-]{1,128})?', path))


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
    def student(row):
        return {key: row[key] for key in ('learner_id', 'display_name', 'purpose')} | {
            'enabled': bool(row['enabled']), 'is_advanced_learner': bool(row['is_advanced_learner'])}

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
                where += ' AND instr(lower(display_name),lower(?))>0'; args.append(query)
            total = con.execute('SELECT COUNT(*) FROM access_principals WHERE ' + where, args).fetchone()[0]
            rows = con.execute('SELECT * FROM access_principals WHERE ' + where +
                               ' ORDER BY display_name,learner_id LIMIT ? OFFSET ?', (*args, limit, offset)).fetchall()
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

    def detail(self, access, learner_id):
        self.require_admin(access)
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
        reviews.sort(key=lambda r: (r['due_at'] or '', r['kind'], r['id']))
        learned.sort(key=lambda r: (r['started_at'] or '', r['topic_id']), reverse=True)
        return {'student': self.student(row), 'generated_at': current.isoformat(), 'learned': learned,
                'reviews': reviews, 'atomic_cards': cards, 'training_learned': training_learned}

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

    @app.get('/api/admin/students')
    def students(request: Request, limit: int = Query(default=25, ge=1, le=100),
                 offset: int = Query(default=0, ge=0, le=1000000), q: str = Query(default='', max_length=100)):
        return store.students(access_store.resolve(request.headers.get('x-learning-identity')), limit, offset, q)

    @app.get('/api/admin/students/{learner_id}')
    def detail(learner_id: str, request: Request):
        return store.detail(access_store.resolve(request.headers.get('x-learning-identity')), learner_id)
