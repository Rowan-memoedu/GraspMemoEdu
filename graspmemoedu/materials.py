"""Content inventory independent of learner progress or due dates.

New training modes register their normal entry providers here. Access policy and
ephemeral storage remain shared; no per-feature administrator grants are needed.
"""
from fastapi import Request, Query
from pydantic import BaseModel, ConfigDict, Field

from .service import DomainError
from .training_content import permission_id
from .question_types import public_interaction
from .question_bank import QuestionBank
from .reference_answers import reference_fields


def topic_blocks(topic):
    """All authored blocks, independent of queues, mastery or spare-practice gates."""
    blocks = []
    def add(kind, item, ident=None, field='html'):
        blocks.append({'id': ident or kind + ':' + item['id'], 'kind': kind,
                       'title': item.get('title', item.get('id', '')), 'html': item.get(field, ''),
                       **({'interaction': public_interaction(item)} if kind == 'question' else {})})
    def question(item):
        add('question', item)
        add('explanation', item, field='explanation_html')
        blocks[-1].update(reference_fields(item, True))
    if topic.get('topic_format_version') == 2:
        introductions = {item['id']: item for item in topic['introductions']}
        modules = {item['id']: item for item in topic.get('modules', [])}
        seen = set()
        for item in topic.get('content_order', []):
            key = (item['kind'], item['id'])
            if key in seen:
                continue
            seen.add(key)
            if item['kind'] == 'introduction' and item['id'] in introductions:
                add('introduction', introductions[item['id']])
            elif item['id'] in modules:
                module = modules[item['id']]
                for q in [module['example'], *module['practices']]:
                    question(q)
        for ident, intro in introductions.items():
            if ('introduction', ident) not in seen:
                add('introduction', intro)
        for ident, module in modules.items():
            if ('example', ident) not in seen:
                for q in [module['example'], *module['practices']]:
                    question(q)
    else:
        add('introduction', topic['introduction'], 'introduction')
        for module in topic.get('modules', []):
            for q in [module['example'], *module['practices']]:
                question(q)
    for chunk in topic.get('memory_chunks', []):
        add('chunk', chunk)
    for card in topic.get('flashcards', []):
        question(card)
    return blocks


def topic_entries(topic):
    return [{'kind': 'topic', 'id': topic['id'], 'title': topic['title']}]


def atomic_entries(topic):
    return topic_entries(topic) + [{'kind': 'atomic_card', 'id': card['id'], 'title': card['title'],
                                    'point_ids': card['point_ids']} for card in topic['flashcards']]


MATERIAL_PROVIDERS = {'paradigm': topic_entries, 'atomic_retrieval': atomic_entries}


def material_inventory(catalog, subjects, access, subject_id=None, topic_id=None):
    if not access.get('is_admin'):
        raise DomainError('feature_forbidden', '此内容入口仅供管理员使用。', 403)
    subjects.authorize_subject(access, subject_id)
    result = []
    for course in catalog.courses.values():
        actual_subject = subjects.course_subjects[course['id']]
        if subject_id and actual_subject != subject_id:
            continue
        for declaration in course['topics']:
            if not subjects.test_materials.graph_topic_visible(course['id'], declaration['id'], access):
                continue
            topic = catalog.topic(declaration['id'])
            if topic_id and topic['id'] != topic_id:
                continue
            provider = MATERIAL_PROVIDERS.get(topic.get('training_mode', 'paradigm'), topic_entries)
            for entry in provider(topic):
                result.append({**entry, 'topic_id': topic['id'], 'topic_title': topic['title'],
                               'subject_id': actual_subject, 'course_id': course['id'], 'course_title': course['title']})
    return result


class MaterialOpen(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    kind: str
    id: str = Field(min_length=1, max_length=128)


def register_material_routes(app, catalog, access_store, subjects, identity, public_state):
    @app.get('/api/materials/content')
    def read_content(request: Request, topic_id: str = Query(min_length=1, max_length=128),
                     block: str | None = Query(default=None, max_length=300),
                     version: str | None = Query(default=None, max_length=128),
                     bank_id: str | None = Query(default=None, max_length=64),
                     group_id: str | None = Query(default=None, max_length=64)):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        if not access.get('is_admin'):
            raise DomainError('feature_forbidden', '此内容入口仅供管理员使用。', 403)
        subject_id = request.query_params.get('subject_id')
        subjects.authorize_subject(access, subject_id)
        helper = app.state.help_requests
        if bank_id:
            access_store.authorize(access, permission_id(bank_id))
            bank = helper.training.bank(bank_id)
            if topic_id != permission_id(bank_id) or (subject_id and subject_id != bank['subject_id']):
                raise DomainError('invalid_help_context', '未找到此答疑请求对应的内容。', 422)
            group = next((g for g in bank['groups'] if g['id'] == group_id), None)
            if not group:
                raise DomainError('unknown_material', '未找到此内容。', 404)
            blocks = []
            for raw in group.get('questions', []):
                _, _, q = helper.training.question(bank_id, group_id, raw['id'])
                blocks.extend({'id': kind + ':' + q['id'], 'kind': kind, 'title': q.get('title', q['id']), 'html': q[field],
                               **({'interaction': public_interaction(q)} if kind == 'question' else {})}
                              for kind, field in [('question', 'html'), ('explanation', 'explanation_html')])
                blocks[-1].update(reference_fields(q, True))
            result = {'title': group['title'], 'course_title': bank['title'], 'version': bank['version'],
                      'math_css': bank.get('math_css', ''), 'blocks': blocks}
        else:
            access_store.authorize(access, topic_id)
            subjects.authorize_topic(subject_id, topic_id)
            topic = catalog.topic(topic_id)
            course = catalog.courses[catalog.topic_courses[topic_id]]
            result = {'title': topic['title'], 'course_title': course['title'], 'version': topic['version'],
                      'math_css': topic.get('math_css', ''), 'blocks': topic_blocks(topic)}
        result['version_changed'] = bool(version and version != result['version'])
        # A converted practice can be pinned to an immutable question-bank revision.
        if block and result['version_changed']:
            question_id = block.split(':', 1)[1] if block.startswith(('question:', 'explanation:')) else None
            try:
                html, context = helper._content({'topic_id': topic_id, 'bank_id': bank_id, 'group_id': group_id,
                    'question_id': question_id, 'content_block_id': block, 'content_version': version})
                target = next((item for item in result['blocks'] if item['id'] == block), None)
                if target is not None:
                    target['html'] = html
                    target['version'] = version
                    if question_id and not bank_id and target['kind'] == 'question':
                        target['interaction'] = public_interaction(QuestionBank(catalog.topic(topic_id), access_store.data_dir).question(question_id, version))
                    result['math_css'] += '\n' + context.get('math_css', '')
                    result['version_changed'] = False
            except DomainError:
                pass  # The Q&A record retains the original snapshot; do not pretend current text is that version.
        return result

    @app.get('/api/materials')
    def inventory(request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        return {'items': material_inventory(catalog, subjects, access, request.query_params.get('subject_id'),
                                            request.query_params.get('topic_id'))}

    @app.post('/api/materials/open')
    def open_material(payload: MaterialOpen, request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        if not access.get('is_admin'):
            raise DomainError('feature_forbidden', '此内容入口仅供管理员使用。', 403)
        access, learner = identity(request, 'learn')
        entries = material_inventory(catalog, subjects, access, request.query_params.get('subject_id'), learner.topic_id)
        if not any(e['kind'] == payload.kind and e['id'] == payload.id for e in entries):
            raise DomainError('unknown_material', '未找到此内容。', 404)
        if payload.kind == 'atomic_card':
            return public_state(learner.preview_card(payload.id, payload.request_id), access, learner)
        if payload.kind == 'topic':
            return public_state(learner.state(), access, learner)
        raise DomainError('unknown_material', '未找到此内容的学习入口。', 404)
