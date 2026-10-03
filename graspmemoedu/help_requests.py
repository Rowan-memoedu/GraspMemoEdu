"""Student questions retained privately and readable only by administrators."""
from contextlib import closing
from datetime import datetime, timedelta
import json
import uuid
from fastapi import Request, Query
from pydantic import BaseModel, ConfigDict, Field
from .service import DomainError, now
from .public_limits import PublicLimitError
from .content_reports import ContentText


class HelpRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    message: str = Field(default='', max_length=4000)
    topic_id: str = Field(min_length=1, max_length=128)
    question_id: str | None = Field(default=None, max_length=256)
    content_block_id: str = Field(min_length=1, max_length=300)
    content_version: str = Field(min_length=1, max_length=128)


class HelpRequestStore:
    def __init__(self, catalog, access_store, subjects):
        self.catalog, self.access_store, self.subjects = catalog, access_store, subjects
        with closing(access_store._connect()) as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS help_requests (
                id TEXT PRIMARY KEY, learner_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                request_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(learner_id,request_id));
                CREATE INDEX IF NOT EXISTS help_request_created ON help_requests(created_at,id);''')

    def save(self, access, values, subject_id=None):
        topic_id = values['topic_id']
        self.access_store.authorize(access, topic_id, 'request_help')
        self.subjects.authorize_topic(subject_id, topic_id)
        message = values['message'].strip()
        if '\x00' in message:
            raise DomainError('invalid_help_request', '请求字段不完整或格式错误，请刷新后重试。', 422)
        payload = {**values, 'message': message}
        with self.access_store._transaction() as con:
            existing = con.execute('SELECT id,body,created_at FROM help_requests WHERE learner_id=? AND request_id=?',
                                   (access['learner_id'], payload['request_id'])).fetchone()
            if existing:
                if json.loads(existing['body'])['request'] != payload:
                    raise DomainError('request_conflict', '同一请求标识不能用于不同内容。', 409)
                return {'id': existing['id'], 'status': 'saved', 'created_at': existing['created_at']}
            topic = self.catalog.topic(topic_id)
            if payload['content_version'] != topic['version']:
                raise DomainError('content_version_mismatch', '正文版本已变化，请刷新后重新提交答疑请求。', 409)
            block, question_id = payload['content_block_id'], payload.get('question_id')
            html = None
            if question_id:
                questions = topic['flashcards'] if topic.get('training_mode') == 'atomic_retrieval' else [q for m in topic['modules'] for q in [m['example'], *m['practices']]]
                question = next((q for q in questions if q['id'] == question_id), None)
                field = {f'question:{question_id}': 'html', f'explanation:{question_id}': 'explanation_html'}.get(block)
                if question and field: html = question[field]
            elif block == 'introduction':
                html = topic['introduction']['html']
            elif block.startswith('chunk:') and topic.get('training_mode') == 'atomic_retrieval':
                chunk = next((c for c in topic['memory_chunks'] if 'chunk:' + c['id'] == block), None)
                if chunk: html = chunk['html']
            if html is None:
                raise DomainError('invalid_help_context', '未找到此答疑请求对应的内容。', 422)
            created = now()
            cutoff = (datetime.fromisoformat(created) - timedelta(seconds=60)).isoformat()
            if con.execute('SELECT COUNT(*) FROM help_requests WHERE learner_id=? AND created_at>?', (access['learner_id'], cutoff)).fetchone()[0] >= 3:
                raise PublicLimitError('help_rate_limited', '答疑请求过于频繁，请稍后重试。', 60)
            course = self.catalog.courses[self.catalog.topic_courses[topic_id]]
            body = {'request': payload, 'display_name': access['display_name'],
                    'course_id': course['id'], 'course_title': course['title'], 'topic_title': topic['title'],
                    'subject_id': self.subjects.course_subjects[course['id']],
                    'content_text': ''.join(ContentText(html).parts)}
            request_id = str(uuid.uuid4())
            con.execute('INSERT INTO help_requests VALUES (?,?,?,?,?)',
                        (request_id, access['learner_id'], payload['request_id'], json.dumps(body, ensure_ascii=False), created))
        return {'id': request_id, 'status': 'saved', 'created_at': created}

    def inbox(self, access, limit=25, offset=0):
        if not access.get('is_admin'):
            raise DomainError('feature_forbidden', '此页面仅供管理员查看。', 403)
        with closing(self.access_store._connect()) as con:
            rows = con.execute('SELECT id,learner_id,body,created_at FROM help_requests ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (limit + 1, offset)).fetchall()
            total = con.execute('SELECT COUNT(*) FROM help_requests').fetchone()[0]
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
