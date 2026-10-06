"""Student questions retained privately for each learner's assigned administrator."""
from contextlib import closing
from datetime import datetime, timedelta
import json
import uuid
from typing import Annotated, Literal
from fastapi import Request, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from .service import DomainError, now
from .public_limits import PublicLimitError
from .content_reports import ContentText
from .question_bank import QuestionBank
from .training_content import TrainingCatalog, permission_id
from .feedback_selection import FeedbackContent, content_hash, normalize_selection, validate_selection
from .help_threads import HelpThreads, HelpMessage, HelpThreadRead
from .help_main import MainHelpThreads
from .access import admin_display_name


class HelpContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    topic_id: str = Field(min_length=1, max_length=128)
    question_id: str | None = Field(default=None, max_length=256)
    content_block_id: str = Field(min_length=1, max_length=300)
    content_version: str = Field(min_length=1, max_length=128)
    kind: Literal['help', 'quality'] = 'help'
    bank_id: str | None = Field(default=None, min_length=1, max_length=64)
    group_id: str | None = Field(default=None, min_length=1, max_length=64)


PREVIEW_CATEGORIES = {'factual_error', 'unnatural', 'derivation_steps', 'motivation'}
CATEGORIES = {'quality': {'formatting', 'factual_error', 'unnatural', 'other'},
              'help': {'missing_prerequisite', 'derivation_steps', 'motivation', 'other'}}
Offset = Annotated[StrictInt, Field(ge=0, le=10000000)]


class HelpRequest(HelpContext):
    request_id: str = Field(min_length=1, max_length=128)
    message: str = Field(default='', max_length=4000)
    categories: list[str] = Field(default_factory=list, max_length=4)
    prerequisite: str = Field(default='', max_length=1000)
    selection: list[tuple[Offset, Offset, Offset]] = Field(default_factory=list, max_length=256)
    selection_hash: str = Field(default='', max_length=64, pattern=r'^(?:[0-9a-f]{64})?$')
    selection_version: Literal[1] = 1

    @model_validator(mode='after')
    def validate_details(self):
        if len(set(self.categories)) != len(self.categories) or set(self.categories) - CATEGORIES[self.kind]:
            raise ValueError('Invalid feedback categories')
        self.prerequisite = self.prerequisite.strip()
        if '\x00' in self.prerequisite or (self.prerequisite and 'missing_prerequisite' not in self.categories):
            raise ValueError('Invalid prerequisite detail')
        if self.selection and (not self.selection_hash or not set(self.categories) & PREVIEW_CATEGORIES):
            raise ValueError('Selections require a preview category and content hash')
        if any(end <= start for _, start, end in self.selection):
            raise ValueError('Invalid selection interval')
        self.selection = [tuple(value) for value in normalize_selection(self.selection)]
        if not self.selection:
            self.selection_hash = ''
        return self


class HelpReply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    expected_revision: int = Field(ge=0)


class HelpReplyRead(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)


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
                    admin_id TEXT NOT NULL REFERENCES access_principals(learner_id));
                CREATE TABLE IF NOT EXISTS help_replies (
                    id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES help_requests(id),
                    admin_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    request_id TEXT NOT NULL, message TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision>0), created_at TEXT NOT NULL,
                    UNIQUE(report_id,revision), UNIQUE(admin_id,request_id));
                CREATE TABLE IF NOT EXISTS help_reply_reads (
                    report_id TEXT PRIMARY KEY REFERENCES help_requests(id),
                    learner_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                    revision INTEGER NOT NULL CHECK(revision>0), read_at TEXT NOT NULL);''')
        self.threads = HelpThreads(self)
        self.main_threads = MainHelpThreads(self)

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
            rows = con.execute('''SELECT p.learner_id,COALESCE(NULLIF(p.admin_name,''),p.display_name) AS display_name,COALESCE(a.admin_id,c.admin_id) AS admin_id,
                a.admin_id IS NULL AS uses_default FROM access_principals p
                LEFT JOIN help_admin_assignments a ON a.learner_id=p.learner_id
                LEFT JOIN help_admin_config c ON c.id=1
                WHERE p.role='account' AND p.is_student=1 ORDER BY p.learner_id''').fetchall()
        return {'default_admin_id': default[0] if default else None,
                'items': [{**dict(row), 'uses_default': bool(row['uses_default'])} for row in rows]}

    def _authorize_context(self, access, values, subject_id):
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

    def preview(self, access, values, subject_id=None):
        payload = HelpContext.model_validate(values).model_dump()
        self._authorize_context(access, payload, subject_id)
        html, context = self._content(payload)
        return {'content_html': FeedbackContent(html, annotate=True).html,
                'content_hash': content_hash(html), 'math_css': context.get('math_css', '')}

    def save(self, access, values, subject_id=None):
        values = HelpRequest.model_validate(values).model_dump()
        self._authorize_context(access, values, subject_id)
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
            validate_selection(html, payload['selection'], payload['selection_hash'])
            created = now()
            cutoff = (datetime.fromisoformat(created) - timedelta(seconds=60)).isoformat()
            if con.execute('SELECT COUNT(*) FROM help_requests WHERE learner_id=? AND created_at>?', (access['learner_id'], cutoff)).fetchone()[0] >= 3:
                raise PublicLimitError('help_rate_limited', '答疑请求过于频繁，请稍后重试。', 60)
            body = {'request': payload, 'display_name': access['display_name'], **context,
                    'content_text': ''.join(ContentText(html).parts), 'content_html': html}
            body['source_version'] = self.threads.current_version(body)
            request_id = str(uuid.uuid4())
            con.execute('INSERT INTO help_requests VALUES (?,?,?,?,?)',
                        (request_id, access['learner_id'], payload['request_id'], json.dumps(body, ensure_ascii=False), created))
            self.threads.baseline(con, request_id, body)
        return {'id': request_id, 'status': 'saved', 'created_at': created}

    def _content(self, payload):
        topic_id = payload['topic_id']
        block, question_id = payload['content_block_id'], payload.get('question_id')
        revision = payload['content_version']
        block_type = block.split(':')[0]
        if payload['bank_id']:
            bank, group, question = self.training.question(payload['bank_id'], payload['group_id'], question_id)
            if revision.startswith('variant-'):
                from .review_variants import read_variant
                question = read_variant(self.access_store.data_dir, revision, bank, question_id)
            elif revision != bank['version']:
                raise DomainError('content_version_mismatch', '正文版本已变化，请刷新后重新提交答疑请求。', 409)
            field = {f'question:{question_id}': 'html', f'explanation:{question_id}': 'explanation_html'}.get(block)
            if field is None:
                raise DomainError('invalid_help_context', '未找到此答疑请求对应的内容。', 422)
            return question[field], {'course_id': bank['id'], 'course_title': bank['title'],
                'topic_title': group['title'], 'subject_id': bank['subject_id'],
                'math_css': bank.get('math_css', ''),
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
                'math_css': topic.get('math_css', '') + ('\n' + bank.load(revision)['math_css'] if bank_question else ''),
                'subject_id': self.subjects.course_subjects[course['id']],
                'content_block_type': block_type, 'content_block_title': block_title}

    def visible_scope(self, scope, parameters, access):
        restricted = self.access_store.test_materials.permission_ids
        if self.access_store.test_materials.allowed(access):
            restricted = restricted - {t['id'] for t in access['topics']}
        if restricted:
            scope += ''' AND NOT EXISTS (SELECT 1 FROM json_each(?) blocked
                WHERE blocked.value=json_extract(r.body,'$.request.topic_id'))'''
            parameters += (json.dumps(sorted(restricted)),)
        return scope, parameters

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
            scope, parameters = self.visible_scope(scope, (access['learner_id'],), access)
            rows = con.execute('SELECT r.id,r.learner_id,r.body,r.created_at ' + scope +
                               ' ORDER BY r.created_at DESC,r.id DESC LIMIT ? OFFSET ?', (*parameters, limit + 1, offset)).fetchall()
            total = con.execute('SELECT COUNT(*) ' + scope, parameters).fetchone()[0]
        return {'items': [{**{key: value for key, value in json.loads(row['body']).items()
                              if key not in ('content_html', 'math_css')}, 'id': row['id'], 'learner_id': row['learner_id'],
                           'created_at': row['created_at'], 'display_name': self._admin_name(row['learner_id'])} for row in rows[:limit]],
                'total': total, 'next_offset': offset + limit if len(rows) > limit else None}

    def content(self, access, report_id):
        """Read one submitted block without opening a learning service or attempt."""
        if not access.get('is_admin'):
            raise DomainError('feature_forbidden', '此页面仅供管理员查看。', 403)
        with closing(self.access_store._connect()) as con:
            row = con.execute('''SELECT r.id,r.learner_id,r.body,r.created_at FROM help_requests r
                LEFT JOIN help_admin_assignments a ON a.learner_id=r.learner_id
                LEFT JOIN help_admin_config c ON c.id=1
                WHERE r.id=? AND COALESCE(a.admin_id,c.admin_id)=?''',
                (report_id, access['learner_id'])).fetchone()
        if not row or not self.access_store.test_materials.snapshot_visible(json.loads(row['body'])['request']['topic_id'], access):
            raise DomainError('unknown_help_request', '未找到此提交记录。', 404)
        return {**self._render_record(row), 'display_name': self._admin_name(row['learner_id'])}

    def _admin_name(self, learner_id):
        with closing(self.access_store._connect()) as con:
            row = con.execute('SELECT display_name,admin_name FROM access_principals WHERE learner_id=?', (learner_id,)).fetchone()
        return admin_display_name(row) if row else learner_id

    def _render_record(self, row):
        body = json.loads(row['body'])
        fidelity = 'snapshot'
        if 'content_html' not in body:
            # Older records saved plain text only. Resolve the exact revision,
            # and never substitute a changed current block for the saved text.
            fidelity = 'text_snapshot'
            try:
                html, context = self._content(HelpRequest.model_validate(body['request']).model_dump())
                if ''.join(ContentText(html).parts) == body['content_text']:
                    body.update(content_html=html, math_css=context.get('math_css', ''))
                    fidelity = 'verified_source'
            except (DomainError, ValueError, KeyError, OSError):
                pass
        if body.get('content_html') and body['request'].get('selection'):
            body['highlighted_html'] = FeedbackContent(body['content_html'], selection=body['request']['selection']).html
        return {**body, 'id': row['id'], 'learner_id': row['learner_id'],
                'created_at': row['created_at'], 'content_fidelity': fidelity}

    @staticmethod
    def _viewer(access):
        if access.get('is_admin'):
            return 'admin'
        if access.get('role') == 'account' and access.get('is_student') and 'request_help' in access.get('features', []):
            return 'student'
        raise DomainError('feature_forbidden', '答疑页面仅供学员和管理员使用。', 403)

    @staticmethod
    def _center_scope(viewer):
        return '''FROM help_requests r
            LEFT JOIN help_admin_assignments a ON a.learner_id=r.learner_id
            LEFT JOIN help_admin_config c ON c.id=1
            LEFT JOIN help_replies reply ON reply.report_id=r.id AND reply.revision=
                (SELECT MAX(revision) FROM help_replies WHERE report_id=r.id)
            LEFT JOIN help_reply_reads seen ON seen.report_id=r.id AND seen.learner_id=r.learner_id
            WHERE ''' + ('COALESCE(a.admin_id,c.admin_id)=?' if viewer == 'admin' else 'r.learner_id=?')

    def center(self, access, limit=10, offset=0, summary=False, report_id=None, subject_id=None, learner_id=None):
        viewer = self._viewer(access)
        self.subjects.authorize_subject(access, subject_id)
        scope = self._center_scope(viewer)
        parameters = (access['learner_id'],)
        scope, parameters = self.visible_scope(scope, parameters, access)
        if subject_id:
            scope += " AND json_extract(r.body,'$.subject_id')=?"
            parameters += (subject_id,)
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            if learner_id:
                self.main_threads.authorized(con, access, learner_id)
                scope += ' AND r.learner_id=?'
                parameters += (learner_id,)
            fields = '''SELECT r.id,r.learner_id,r.body,r.created_at,reply.message reply_message,
                reply.revision,reply.created_at replied_at,COALESCE(seen.revision,0) seen_revision '''
            all_rows = con.execute(fields + scope, parameters).fetchall()
            states = {row['id']: self.threads.state(con, row, access, include_messages=False) for row in all_rows}
            result = {'viewer': viewer, 'total': len(all_rows),
                      'pending_count': sum(not con.execute("SELECT 1 FROM help_messages WHERE report_id=? AND author_role='admin'", (row['id'],)).fetchone() for row in all_rows),
                      'unread_count': sum(state['unread'] for state in states.values()),
                      'latest_reply_at': max((state['latest_activity_at'] for state in states.values()), default=None),
                      'activity_key': self.threads.activity_key([{'id': ident, **state} for ident, state in states.items()])}
            roster = self.main_threads.roster(con, access)
            main_states = {r['id']: self.main_threads.state(con, r, access, False) for r in roster}
            # Main-conversation notifications are independent of subject filters.
            result['unread_count'] += sum(s['unread'] for s in main_states.values())
            result['activity_key'] = self.threads.activity_key(
                [{'id': ident, **state} for ident, state in states.items()] +
                [{**state, 'id': 'main:' + ident} for ident, state in main_states.items()])
            if summary:
                return result
            if viewer == 'admin':
                result['students'] = [{'learner_id': r['id'], 'display_name': self._admin_name(r['id']),
                                       'main_unread': main_states[r['id']]['unread']} for r in roster]
            selected = learner_id or (access['learner_id'] if viewer == 'student' else None)
            if selected:
                row, _ = self.main_threads.authorized(con, access, selected)
                result['main_thread'] = self.main_threads.state(con, row, access)
            if report_id:
                rows = [row for row in all_rows if row['id'] == report_id]
                if not rows:
                    raise DomainError('unknown_help_request', '未找到此提交记录。', 404)
            else:
                rows = sorted(all_rows, key=lambda row: (states[row['id']]['latest_activity_at'], row['id']), reverse=True)[offset:offset + limit + 1]
            items = []
            for row in rows[:limit]:
                reply = ({'message': row['reply_message'], 'revision': row['revision'], 'created_at': row['replied_at']}
                         if row['revision'] is not None else None)
                items.append({**self._render_record(row), **({'display_name': self._admin_name(row['learner_id'])} if viewer == 'admin' else {}),
                              'reply': reply, **self.threads.state(con, row, access)})
        return {**result, 'items': items, 'next_offset': offset + limit if not report_id and len(rows) > limit else None}

    def reply(self, access, values):
        if self._viewer(access) != 'admin':
            raise DomainError('feature_forbidden', '仅管理员可以回复答疑。', 403)
        values = HelpReply.model_validate(values).model_dump()
        message = values['message'].strip()
        if not message or '\x00' in message:
            raise DomainError('invalid_help_reply', '请填写回复内容。', 422)
        with self.access_store._transaction() as con:
            self._require_admin(con, access['learner_id'])
            row = con.execute('SELECT learner_id,body FROM help_requests WHERE id=?', (values['report_id'],)).fetchone()
            if (not row or self._owner(con, row['learner_id']) != access['learner_id'] or
                    not self.access_store.test_materials.snapshot_visible(json.loads(row['body'])['request']['topic_id'], access)):
                raise DomainError('unknown_help_request', '未找到此提交记录。', 404)
            existing = con.execute('SELECT * FROM help_replies WHERE admin_id=? AND request_id=?',
                (access['learner_id'], values['request_id'])).fetchone()
            if existing:
                if (existing['report_id'], existing['message'], existing['revision'] - 1) != (
                        values['report_id'], message, values['expected_revision']):
                    raise DomainError('request_conflict', '同一请求标识不能用于不同内容。', 409)
                return {'status': 'saved', 'reply': {key: existing[key] for key in ('message', 'revision', 'created_at')}}
            revision = con.execute('SELECT COALESCE(MAX(revision),0) FROM help_replies WHERE report_id=?',
                (values['report_id'],)).fetchone()[0]
            thread_revision = con.execute('SELECT MAX(revision) FROM help_message_versions WHERE message_id=?',
                                          ('legacy-' + values['report_id'],)).fetchone()[0]
            if values['expected_revision'] != revision or (thread_revision and thread_revision != revision):
                raise DomainError('help_reply_conflict', '该回复已在其他页面更新，请重新载入后再提交。', 409)
            created = now()
            con.execute('INSERT INTO help_replies VALUES (?,?,?,?,?,?,?)',
                (str(uuid.uuid4()), values['report_id'], access['learner_id'], values['request_id'], message, revision + 1, created))
            self.threads.sync_legacy(con, values['report_id'])
        return {'status': 'saved', 'reply': {'message': message, 'revision': revision + 1, 'created_at': created}}

    def mark_read(self, access, values):
        if self._viewer(access) != 'student':
            raise DomainError('feature_forbidden', '仅学员可以标记自己的答疑回复。', 403)
        values = HelpReplyRead.model_validate(values).model_dump()
        with self.access_store._transaction() as con:
            row = con.execute('''SELECT reply.revision,r.body FROM help_replies reply
                JOIN help_requests r ON r.id=reply.report_id
                WHERE r.id=? AND r.learner_id=? AND reply.revision=?''',
                (values['report_id'], access['learner_id'], values['revision'])).fetchone()
            if not row or not self.access_store.test_materials.snapshot_visible(json.loads(row['body'])['request']['topic_id'], access):
                raise DomainError('unknown_help_request', '未找到此答疑回复。', 404)
            con.execute('''INSERT INTO help_reply_reads VALUES (?,?,?,?) ON CONFLICT(report_id) DO UPDATE SET
                revision=MAX(help_reply_reads.revision,excluded.revision),read_at=excluded.read_at''',
                (values['report_id'], access['learner_id'], values['revision'], now()))
        return {'status': 'read', 'revision': values['revision']}

    def current_content(self, access, report_id, subject_id=None):
        # Only the submitted block is exposed; no learner-progress bypass for
        # arbitrary blocks, hidden answers or a different Topic.
        with closing(self.access_store._connect()) as con:
            row, _ = self.threads.authorized(con, access, report_id)
            body = json.loads(row['body'])
        self.subjects.authorize_subject(access, subject_id)
        if subject_id and body['subject_id'] != subject_id:
            raise DomainError('unknown_help_request', '未找到此提交记录。', 404)
        payload = HelpContext.model_validate({key: body['request'][key] for key in HelpContext.model_fields if key in body['request']}).model_dump()
        self._authorize_context(access, payload, subject_id)
        version = self.threads.current_version(body)
        if not version:
            raise DomainError('unknown_help_request', '未找到此提交记录。', 404)
        payload['content_version'] = version
        html, context = self._content(payload)
        return {**context, 'content_html': html, 'content_version': version, 'content_block_id': payload['content_block_id']}


def register_help_routes(app, catalog, access_store, subjects):
    store = HelpRequestStore(catalog, access_store, subjects)
    app.state.help_requests = store
    @app.post('/api/help-requests/preview')
    def preview_help(payload: HelpContext, request: Request):
        return store.preview(access_store.resolve(request.headers.get('x-learning-identity')),
                             payload.model_dump(), request.query_params.get('subject_id'))

    @app.post('/api/help-requests')
    def submit_help(payload: HelpRequest, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return store.save(access, payload.model_dump(), request.query_params.get('subject_id'))

    @app.get('/api/help-requests')
    def help_inbox(request: Request, limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000),
                   report_id: str | None = Query(default=None, min_length=1, max_length=128)):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return store.content(access, report_id) if report_id else store.inbox(access, limit, offset)

    @app.get('/api/help-center')
    def help_center(request: Request, limit: int = Query(default=10, ge=1, le=25),
                    offset: int = Query(default=0, ge=0, le=1000000), summary: bool = False,
                    report_id: str | None = Query(default=None, min_length=1, max_length=128),
                    learner_id: str | None = Query(default=None, min_length=1, max_length=128)):
        return store.center(access_store.resolve(request.headers.get('x-learning-identity')), limit, offset, summary, report_id,
                            request.query_params.get('subject_id'), learner_id)

    @app.post('/api/help-main/messages')
    def send_main_message(payload: HelpMessage, request: Request):
        return store.main_threads.send(access_store.resolve(request.headers.get('x-learning-identity')), payload.model_dump())

    @app.post('/api/help-main/read')
    def read_main_messages(payload: HelpThreadRead, request: Request):
        if payload.content_version:
            raise DomainError('invalid_help_reply', '主对话不包含内容版本。', 422)
        return store.main_threads.read(access_store.resolve(request.headers.get('x-learning-identity')), payload.model_dump())

    @app.post('/api/help-replies')
    def reply_help(payload: HelpReply, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subject = request.query_params.get('subject_id')
        if subject:
            store.center(access, report_id=payload.report_id, subject_id=subject)
        return store.reply(access, payload.model_dump())

    @app.post('/api/help-replies/read')
    def read_reply(payload: HelpReplyRead, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subject = request.query_params.get('subject_id')
        if subject:
            store.center(access, report_id=payload.report_id, subject_id=subject)
        return store.mark_read(access, payload.model_dump())

    @app.post('/api/help-messages')
    def send_message(payload: HelpMessage, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subject = request.query_params.get('subject_id')
        if subject:
            store.center(access, report_id=payload.report_id, subject_id=subject)
        return store.threads.send(access, payload.model_dump())

    @app.post('/api/help-messages/read')
    def read_messages(payload: HelpThreadRead, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subject = request.query_params.get('subject_id')
        if subject:
            store.center(access, report_id=payload.report_id, subject_id=subject)
        return store.threads.read(access, payload.model_dump())

    @app.get('/api/help-content')
    def current_help_content(request: Request, report_id: str = Query(min_length=1, max_length=128)):
        return store.current_content(access_store.resolve(request.headers.get('x-learning-identity')),
                                     report_id, request.query_params.get('subject_id'))
