"""Shared reading/audio routes using existing identity, Topic grants and quota."""
from copy import deepcopy
import threading
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .access import credential_digest
from .context_library import ContextLibraryStore, find_material, lookup_context, validate_reading_materials
from .learning_storage import MemoryLearningStore
from .service import DomainError
from .training_audio import FunASRTranscriber, audio_question, decode_recording, read_audio

PUBLIC_PATHS = {'/api/context-library', '/api/context-library/material', '/api/context-library/lookup',
                '/api/context-library/marks', '/api/training-audio', '/api/training-audio/status',
                '/api/training-audio/transcribe'}


class ReadingQuote(BaseModel):
    model_config = ConfigDict(extra='forbid')
    exact: str = Field(min_length=1, max_length=2000)
    prefix: str = Field(max_length=50)
    suffix: str = Field(max_length=50)
    start: StrictInt = Field(ge=0, le=10000)
    end: StrictInt = Field(ge=1, le=10000)


class ReadingSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content_version: str = Field(min_length=1, max_length=128)
    paragraph_id: str = Field(min_length=1, max_length=128)
    quote: ReadingQuote


class ReadingMark(ReadingSelection):
    action: Literal['mark', 'unmark', 'memory']
    note: str = Field(default='', max_length=2000)


class AudioRecording(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content_version: str = Field(min_length=1, max_length=128)
    mime_type: Literal['audio/wav']
    data_base64: str = Field(min_length=1, max_length=5592408)


def register_context_library_routes(app, catalog, access_store, subjects, *, activate_points=None,
                                    transcriber=None, dictionary=None, connect_for=None):
    """connect_for(access, request) may reuse the host's administrator preview DB.

    activate_points(access, topic, point_ids, context, request) queues existing points for
    normal learning. It must never create a second FSRS record or mark mastery.
    """
    for topic in catalog.topics.values():
        validate_reading_materials(topic)
    normal = ContextLibraryStore(access_store._connect)
    preview, preview_lock = {}, threading.Lock()
    transcriber = transcriber if transcriber is not None else FunASRTranscriber()
    app.state.context_library = normal

    def close_previews():
        for runtime, _ in preview.values():
            if runtime is not None:
                runtime.close()
    app.state.close_context_previews = close_previews
    if not connect_for:
        # The host injects and owns its preview runtime. Standalone consumers
        # can explicitly close this fallback; newer FastAPI has no event API.
        from contextlib import asynccontextmanager
        previous_lifespan = app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            async with previous_lifespan(application):
                try:
                    yield
                finally:
                    close_previews()
        app.router.lifespan_context = lifespan

    def store_for(access, request):
        if not access.get('is_admin'):
            return normal
        scope = credential_digest('reading-preview', request.headers.get('x-learning-identity') or '')
        with preview_lock:
            if scope not in preview:
                if connect_for:
                    runtime, connection = None, connect_for(access, request)
                else:
                    runtime = MemoryLearningStore(); connection = runtime.connect
                preview[scope] = (runtime, ContextLibraryStore(connection))
            return preview[scope][1]

    def identity(request, *, feature='learn'):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subject_id = request.query_params.get('subject_id')
        subjects.authorize_subject(access, subject_id)
        topic_id = request.query_params.get('topic_id')
        topic = catalog.topic(topic_id)
        subjects.authorize_topic(subject_id, topic_id)
        access_store.authorize(access, topic_id, feature)
        return access, topic

    def selection_context(request):
        access, topic = identity(request)
        return access, topic, find_material(topic, request.query_params.get('material_id'))

    @app.get('/api/context-library')
    def materials(request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        subject_id, topic_id = request.query_params.get('subject_id'), request.query_params.get('topic_id')
        subjects.authorize_subject(access, subject_id)
        if 'learn' not in access['features']:
            raise DomainError('feature_forbidden', '当前账号没有阅读权限。', 403)
        if topic_id:
            identity(request)
        allowed = {t['id'] for t in access['topics']}
        return {'items': [{'topic_id': topic['id'], 'topic_title': topic['title'], 'content_version': topic['version'],
                           'subject_id': topic['subject_id'], 'id': material['id'], 'title': material['title']}
                          for topic in catalog.topics.values() if topic['id'] in allowed
                          and (not topic_id or topic_id == topic['id'])
                          and (not subject_id or subject_id == topic['subject_id'])
                          for material in topic.get('reading_materials', [])]}

    @app.get('/api/context-library/material')
    def material(request: Request):
        access, topic, value = selection_context(request)
        return {'topic_id': topic['id'], 'content_version': topic['version'], 'material': deepcopy(value),
                'records': store_for(access, request).records(access['learner_id'], topic, value),
                'preview': bool(access.get('is_admin'))}

    @app.post('/api/context-library/lookup')
    def lookup(request: Request, payload: ReadingSelection):
        _, topic, value = selection_context(request)
        return lookup_context(topic, value, payload.model_dump(), dictionary)

    @app.post('/api/context-library/marks')
    def mark(request: Request, payload: ReadingMark):
        access, topic, value = selection_context(request)
        return store_for(access, request).save(access, topic, value, payload.model_dump(),
                    activate_points=(lambda a, t, p, c: activate_points(a, t, p, c, request)) if activate_points else None,
                    dictionary=dictionary)

    @app.get('/api/training-audio')
    def audio(request: Request):
        _, topic = identity(request)
        question = audio_question(topic, request.query_params.get('question_id'))
        return read_audio(catalog.topic_paths[topic['id']], question)

    @app.get('/api/training-audio/status')
    def audio_status(request: Request):
        access, topic = identity(request)
        question = audio_question(topic, request.query_params.get('question_id'))
        available = (access.get('role') == 'account' and 'submit_answer' in access['features']
                     and question['audio']['mode'] in ('read_aloud', 'retell') and bool(getattr(transcriber, 'configured', True)))
        return {'transcription_available': available, 'max_seconds': 120,
                'message': '' if available else '语音转写暂不可用，可直接填写文字答案。'}

    @app.post('/api/training-audio/transcribe')
    def transcribe(request: Request, payload: AudioRecording):
        access, topic = identity(request, feature='submit_answer')
        if access.get('role') != 'account':
            raise DomainError('transcription_account_required', '请登录账号后使用语音转写；也可直接填写文字答案。', 403)
        question = audio_question(topic, request.query_params.get('question_id'))
        if question['audio']['mode'] not in ('read_aloud', 'retell'):
            raise DomainError('transcription_not_applicable', '此题使用文字作答。', 422)
        if payload.content_version != topic['version']:
            raise DomainError('audio_version_changed', '题目版本已变化，请刷新后重新录音。', 409)
        if not bool(getattr(transcriber, 'configured', True)):
            raise DomainError('transcription_unavailable', '语音转写尚未配置；可直接填写文字答案。', 503)
        data = decode_recording(payload.data_base64)
        limits = app.state.public_limits
        limits.consume(('audio-transcription', access['learner_id']), limits.submissions_per_minute)
        # Reserve before the external call, outside the SQLite write lock.
        # Reuse the existing daily cost budget; transcription is not a grade.
        if limits.daily:
            with limits.daily.reserve(access['learner_id'], access['role']):
                pass
        result = transcriber(data)
        if not isinstance(result, dict) or not isinstance(result.get('text'), str) or not result['text'].strip():
            raise DomainError('transcription_failed', '未获得有效转写，请重试或直接填写答案。', 502)
        return {'text': result['text'], 'provider': result.get('provider', 'configured'),
                'segments': result.get('segments', []), 'editable': True}
