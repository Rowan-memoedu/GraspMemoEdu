"""Student questions retained privately for each learner's assigned administrator."""
from contextlib import closing
from datetime import datetime, timedelta
import json
import uuid
from typing import Literal
from fastapi import Request, Query
from pydantic import BaseModel, ConfigDict, Field
from .service import DomainError, now
from .public_limits import PublicLimitError
from .content_reports import ContentText
from .question_bank import QuestionBank
from .training_content import TrainingCatalog, permission_id


class HelpRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    message: str = Field(default='', max_length=4000)
    topic_id: str = Field(min_length=1, max_length=128)
    question_id: str | None = Field(default=None, max_length=256)
    content_block_id: str = Field(min_length=1, max_length=300)
    content_version: str = Field(min_length=1, max_length=128)
    kind: Literal['help', 'quality'] = 'help'
    bank_id: str | None = Field(default=None, min_length=1, max_length=64)
    group_id: str | None = Field(default=None, min_length=1, max_length=64)


class HelpRequestStore:
    def __init__(self, catalog, access_store, subjects):
        self.catalog, self.access_store, self.subjects = catalog, access_store, subjects
        self.training = TrainingCatalog(access_store.data_dir)
        with closing(access_store._connect()) as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS help_requests (
                id TEXT PRIMARY KEY, learner_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                request_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(learner_id,request_id));
                CREATE INDEX IF NOT EXISTS help_request_created ON help_requests(created_at,id);
                CREATE TABLE IF NOT EXISTS help_admin_config (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    admin_id TEXT NOT NULL REFERENCES access_principals(learner_id));
                CREATE TABLE IF NOT EXISTS help_admin_assignments (
                    learner_id TEXT PRIMARY KEY REFERENCES access_principals(learner_id),
                    admin_id TEXT NOT NULL REFERENCES access_principals(learner_id));''')

    @staticmethod
    def _require_admin(con, admin_id):
        row = con.execute('SELECT role,is_admin,enabled FROM access_principals WHERE learner_id=?', (admin_id,)).fetchone()
        if not row or row['role'] != 'account' or not row['is_admin'] or not row['enabled']:
            raise DomainError('invalid_help_admin', '答疑归属必须指定有效的管理员账号。', 422)

    @staticmethod
    def _owner(con, learner_id):
        row = con.execute('''SELECT COALESCE(
            (SELECT admin_id FROM help_admin_assignments WHERE learner_id=?),
            (SELECT admin_id FROM help_admin_config WHERE id=1))''', (learner_id,)).fetchone()
        return row[0]

    def set_default_admin(self, admin_id):
        with self.access_store._transaction() as con:
            self._require_admin(con, admin_id)
            con.execute('INSERT INTO help_admin_config VALUES (1,?) ON CONFLICT(id) DO UPDATE SET admin_id=excluded.admin_id', (admin_id,))
        return {'default_admin_id': admin_id}

    def assign_admin(self, learner_id, admin_id=None):
        with self.access_store._transaction() as con:
            learner = con.execute('SELECT role,is_student FROM access_principals WHERE learner_id=?', (learner_id,)).fetchone()
            if not learner or learner['role'] != 'account' or not learner['is_student']:
                raise DomainError('invalid_help_learner', '答疑归属只能设置给学员账号。', 422)
            if admin_id is None:
                con.execute('DELETE FROM help_admin_assignments WHERE learner_id=?', (learner_id,))
            else:
                self._require_admin(con, admin_id)
                con.execute('INSERT INTO help_admin_assignments VALUES (?,?) ON CONFLICT(learner_id) DO UPDATE SET admin_id=excluded.admin_id', (learner_id, admin_id))
            owner = self._owner(con, learner_id)
        return {'learner_id': learner_id, 'admin_id': owner, 'uses_default': admin_id is None}

    def assignments(self):
        with closing(self.access_store._connect()) as con:
            default = con.execute('SELECT admin_id FROM help_admin_config WHERE id=1').fetchone()
            rows = con.execute('''SELECT p.learner_id,p.display_name,COALESCE(a.admin_id,c.admin_id) AS admin_id,
                a.admin_id IS NULL AS uses_default FROM access_principals p
                LEFT JOIN help_admin_assignments a ON a.learner_id=p.learner_id
                LEFT JOIN help_admin_config c ON c.id=1
                WHERE p.role='account' AND p.is_student=1 ORDER BY p.learner_id''').fetchall()
        return {'default_admin_id': default[0] if default else None,
                'items': [{**dict(row), 'uses_default': bool(row['uses_default'])} for row in rows]}

    def save(self, access, values, subject_id=None):
        values = HelpRequest.model_validate(values).model_dump()
        topic_id = values['topic_id']
        bank_id = values['bank_id']
        if bank_id:
            if topic_id != permission_id(bank_id) or not values['group_id'] or not values['question_id']:
                raise DomainError('invalid_help_context', '未找到此答疑请求对应的内容。', 422)
            bank = self.training.bank(bank_id)
            if subject_id is not None and subject_id != bank['subject_id']:
                raise DomainError('subject_bank_mismatch', '此题库不属于当前学科。', 403)
        elif values['group_id'] or topic_id not in self.catalog.topics:
            raise DomainError('invalid_help_context', '未找到此答疑请求对应的内容。', 422)
        self.access_store.authorize(access, topic_id, 'request_help')
        if not bank_id:
            self.subjects.authorize_topic(subject_id, topic_id)
        message = values['message'].strip()
        if '\x00' in message:
            raise DomainError('invalid_help_request', '请求字段不完整或格式错误，请刷新后重试。', 422)
        payload = {**values, 'message': message}
        with self.access_store._transaction() as con:
            existing = con.execute('SELECT id,body,created_at FROM help_requests WHERE learner_id=? AND request_id=?',
                                   (access['learner_id'], payload['request_id'])).fetchone()
            if existing:
                if HelpRequest.model_validate(json.loads(existing['body'])['request']).model_dump() != payload:
                    raise DomainError('request_conflict', '同一请求标识不能用于不同内容。', 409)
                return {'id': existing['id'], 'status': 'saved', 'created_at': existing['created_at']}
            owner = self._owner(con, access['learner_id'])
            if owner is None:
                raise DomainError('help_admin_unavailable', '答疑管理员尚未配置，请稍后重试。', 503)
            try:
                self._require_admin(con, owner)
            except DomainError:
                raise DomainError('help_admin_unavailable', '答疑管理员暂不可用，请稍后重试。', 503) from None
            html, context = self._content(payload)
            created = now()
            cutoff = (datetime.fromisoformat(created) - timedelta(seconds=60)).isoformat()
            if con.execute('SELECT COUNT(*) FROM help_requests WHERE learner_id=? AND created_at>?', (access['learner_id'], cutoff)).fetchone()[0] >= 3:
                raise PublicLimitError('help_rate_limited', '答疑请求过于频繁，请稍后重试。', 60)
            body = {'request': payload, 'display_name': access['display_name'], **context,
                    'content_text': ''.join(ContentText(html).parts)}
            request_id = str(uuid.uuid4())
            con.execute('INSERT INTO help_requests VALUES (?,?,?,?,?)',
                        (request_id, access['learner_id'], payload['request_id'], json.dumps(body, ensure_ascii=False), created))
        return {'id': request_id, 'status': 'saved', 'created_at': created}

    def _content(self, payload):
        topic_id = payload['topic_id']
        block, question_id = payload['content_block_id'], payload.get('question_id')
        revision = payload['content_version']
        block_type = block.split(':')[0]
        if payload['bank_id']:
            bank, group, question = self.training.question(payload['bank_id'], payload['group_id'], question_id)
            if revision != bank['version']:
                raise DomainError('content_version_mismatch', '正文版本已变化，请刷新后重新提交答疑请求。', 409)
            field = {f'question:{question_id}': 'html', f'explanation:{question_id}': 'explanation_html'}.get(block)
            if field is None:
                raise DomainError('invalid_help_context', '未找到此答疑请求对应的内容。', 422)
            return question[field], {'course_id': bank['id'], 'course_title': bank['title'],
                'topic_title': group['title'], 'subject_id': bank['subject_id'],
                'content_block_type': block_type, 'content_block_title': question.get('title', question_id)}
        topic = self.catalog.topic(topic_id)
        bank_question = None
        if revision != topic['version']:
            # Converted questions pin an immutable bank revision rather than
            # the source Topic version, including in history and reviews.
            try:
                if not question_id or topic.get('training_mode') == 'atomic_retrieval':
                    raise ValueError('A bank revision requires a paradigm question')
                bank = QuestionBank(topic, self.access_store.data_dir)
                if question_id not in bank.load(revision)['questions']:
                    raise ValueError('The revision does not contain this question')
                bank_question = bank.question(question_id, revision)
            except (ValueError, KeyError, OSError):
                raise DomainError('content_version_mismatch', '正文版本已变化，请刷新后重新提交答疑请求。', 409) from None
        html = None
        block_title = ''
        if question_id:
            questions = topic['flashcards'] if topic.get('training_mode') == 'atomic_retrieval' else [q for m in topic['modules'] for q in [m['example'], *m['practices']]]
            question = bank_question or next((q for q in questions if q['id'] == question_id), None)
            field = {f'question:{question_id}': 'html', f'explanation:{question_id}': 'explanation_html'}.get(block)
            if question and field:
                html = question[field]
                block_title = question.get('title', question_id)
        elif block == 'introduction':
            html = topic['introduction']['html']
            block_title = topic['introduction'].get('title', '')
        elif block.startswith('introduction:') and topic.get('topic_format_version') == 2:
            intro = next((i for i in topic['introductions'] if 'introduction:' + i['id'] == block), None)
            if intro:
                html, block_title = intro['html'], intro.get('title', intro['id'])
        elif block.startswith('chunk:') and topic.get('training_mode') == 'atomic_retrieval':
            chunk = next((c for c in topic['memory_chunks'] if 'chunk:' + c['id'] == block), None)
            if chunk:
                html, block_title = chunk['html'], chunk.get('title', chunk['id'])
        if html is None:
            raise DomainError('invalid_help_context', '未找到此答疑请求对应的内容。', 422)
        course = self.catalog.courses[self.catalog.topic_courses[topic_id]]
        return html, {'course_id': course['id'], 'course_title': course['title'], 'topic_title': topic['title'],
                'subject_id': self.subjects.course_subjects[course['id']],
                'content_block_type': block_type, 'content_block_title': block_title}

    def inbox(self, access, limit=25, offset=0):
        if not access.get('is_admin'):
            raise DomainError('feature_forbidden', '此页面仅供管理员查看。', 403)
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            # No administrator bypass: records and counts use the same owner filter.
            scope = '''FROM help_requests r
                LEFT JOIN help_admin_assignments a ON a.learner_id=r.learner_id
                LEFT JOIN help_admin_config c ON c.id=1
                WHERE COALESCE(a.admin_id,c.admin_id)=?'''
            rows = con.execute('SELECT r.id,r.learner_id,r.body,r.created_at ' + scope +
                               ' ORDER BY r.created_at DESC,r.id DESC LIMIT ? OFFSET ?', (access['learner_id'], limit + 1, offset)).fetchall()
            total = con.execute('SELECT COUNT(*) ' + scope, (access['learner_id'],)).fetchone()[0]
        return {'items': [{**json.loads(row['body']), 'id': row['id'], 'learner_id': row['learner_id'],
                           'created_at': row['created_at']} for row in rows[:limit]],
                'total': total, 'next_offset': offset + limit if len(rows) > limit else None}


def register_help_routes(app, catalog, access_store, subjects):
    store = HelpRequestStore(catalog, access_store, subjects)
    app.state.help_requests = store
    @app.post('/api/help-requests')
    def submit_help(payload: HelpRequest, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return store.save(access, payload.model_dump(), request.query_params.get('subject_id'))

    @app.get('/api/help-requests')
    def help_inbox(request: Request, limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000)):
        return store.inbox(access_store.resolve(request.headers.get('x-learning-identity')), limit, offset)
