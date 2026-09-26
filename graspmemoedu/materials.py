"""Content inventory independent of learner progress or due dates.

New training modes register their normal entry providers here. Access policy and
ephemeral storage remain shared; no per-feature administrator grants are needed.
"""
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .service import DomainError


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
            return public_state(learner.preview_card(payload.id, payload.request_id), access)
        if payload.kind == 'topic':
            return public_state(learner.state(), access)
        raise DomainError('unknown_material', '未找到此内容的学习入口。', 404)
