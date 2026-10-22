"""Private, content-bound audio and bounded WAV transcription; no pronunciation score."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import threading
import urllib.error
import urllib.request
from urllib.parse import urlencode
import wave

from .service import DomainError

ASSET = re.compile(r'audio/([0-9a-f]{64})\.(mp3|wav)\Z')
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_RECORDING_BYTES = 4 * 1024 * 1024
MAX_RECORDING_SECONDS = 120
MIME = {'mp3': 'audio/mpeg', 'wav': 'audio/wav'}
MODES = {'listen', 'dictation', 'read_aloud', 'retell'}


def validate_audio(question):
    audio = question.get('audio')
    if audio is None:
        return None
    if (not isinstance(audio, dict) or not {'version', 'asset', 'mime_type', 'mode'} <= audio.keys()
            or set(audio) - {'version', 'asset', 'mime_type', 'mode', 'transcript'}
            or type(audio['version']) is not int or audio['version'] != 1
            or not isinstance(audio['asset'], str) or not ASSET.fullmatch(audio['asset'])
            or not isinstance(audio['mode'], str) or audio['mode'] not in MODES):
        raise ValueError('Invalid question audio')
    match = ASSET.fullmatch(audio['asset'])
    if audio['mime_type'] != MIME[match[2]]:
        raise ValueError('Audio MIME does not match the asset type')
    if 'transcript' in audio and (not isinstance(audio['transcript'], str) or not audio['transcript'].strip()
                                or len(audio['transcript']) > 20000 or '\x00' in audio['transcript']):
        raise ValueError('Invalid private audio transcript')
    return audio


def public_audio(question, topic_id, *, feedback=False):
    audio = validate_audio(question)
    if audio is None:
        return None
    result = {key: audio[key] for key in ('version', 'mime_type', 'mode')}
    result['url'] = 'training-audio?' + urlencode({'topic_id': topic_id, 'question_id': question['id']})
    if feedback and audio.get('transcript'):
        result['transcript'] = audio['transcript']
    return result


def audio_question(topic, question_id):
    questions = topic.get('flashcards', []) + [q for m in topic.get('modules', []) for q in [m['example'], *m['practices']]]
    question = next((q for q in questions if q['id'] == question_id and q.get('audio')), None)
    if question is None:
        raise DomainError('audio_not_found', '这道题没有可播放的音频。', 404)
    validate_audio(question)
    return question


def validate_audio_bytes(question, data):
    audio = validate_audio(question)
    if not isinstance(data, bytes) or not 12 <= len(data) <= MAX_AUDIO_BYTES:
        raise DomainError('invalid_audio', '音频资源大小不符合要求。', 422)
    match = ASSET.fullmatch(audio['asset'])
    if hashlib.sha256(data).hexdigest() != match[1]:
        raise DomainError('invalid_audio', '音频资源校验未通过。', 422)
    if match[2] == 'wav':
        valid = data[:4] == b'RIFF' and data[8:12] == b'WAVE'
    else:
        valid = data[:3] == b'ID3' or (data[0] == 255 and data[1] & 224 == 224)
    if not valid:
        raise DomainError('invalid_audio', '音频资源格式不符合要求。', 422)
    return data


def audio_bytes(topic_path, question):
    audio = validate_audio(question)
    base = Path(topic_path).resolve().parent
    path = (base / audio['asset']).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise DomainError('audio_not_found', '音频资源暂不可用。', 404)
    if not 12 <= path.stat().st_size <= MAX_AUDIO_BYTES:
        raise DomainError('invalid_audio', '音频资源大小不符合要求。', 422)
    return validate_audio_bytes(question, path.read_bytes())


def read_audio(topic_path, question):
    audio = validate_audio(question)
    data = audio_bytes(topic_path, question)
    return {'mime_type': audio['mime_type'], 'data_base64': base64.b64encode(data).decode('ascii')}


def _audio_questions(topic):
    return [question for question in topic.get('flashcards', []) +
            [q for m in topic.get('modules', []) for q in [m['example'], *m['practices']]] if question.get('audio')]


def audio_backup_blobs(catalog):
    result = {}
    for topic_id, topic in catalog.topics.items():
        for question in _audio_questions(topic):
            data = audio_bytes(catalog.topic_paths[topic_id], question)
            key = 'courses/' + question['audio']['asset']
            if key in result and result[key] != data:
                raise ValueError('Conflicting archived audio asset')
            result[key] = data
    return result


def validate_audio_archive(blobs, topics):
    expected = set()
    for topic in topics.values() if isinstance(topics, dict) else topics:
        for question in _audio_questions(topic):
            validate_audio(question)
            key = 'courses/' + question['audio']['asset']
            if key not in blobs:
                raise ValueError('Missing referenced audio asset')
            validate_audio_bytes(question, blobs[key]); expected.add(key)
    return expected


def decode_recording(encoded):
    if not isinstance(encoded, str) or not encoded or len(encoded) > (MAX_RECORDING_BYTES + 2) // 3 * 4:
        raise DomainError('invalid_recording', '录音过大；每次最多录制 120 秒。', 422)
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_RECORDING_BYTES or data[:4] != b'RIFF' or data[8:12] != b'WAVE' or struct.unpack('<I', data[4:8])[0] + 8 != len(data):
            raise ValueError('Invalid WAV container')
        with wave.open(io.BytesIO(data), 'rb') as wav:
            frames, rate = wav.getnframes(), wav.getframerate()
            if (wav.getcomptype() != 'NONE' or wav.getnchannels() != 1 or wav.getsampwidth() != 2
                    or rate not in (8000, 16000) or not 0 < frames / rate <= MAX_RECORDING_SECONDS
                    or len(wav.readframes(frames + 1)) != frames * 2):
                raise ValueError('Invalid recording format or duration')
    except (ValueError, struct.error, wave.Error, EOFError):
        raise DomainError('invalid_recording', '请使用不超过 120 秒的单声道 PCM WAV 录音。', 422) from None
    return data


def _sentences(response):
    """Only final SSE sentences survive; partial-only output is not a result."""
    data, total, final, pending = [], 0, {}, set()
    for raw in response:
        total += len(raw)
        if total > 2 * 1024 * 1024 or len(raw) > 256 * 1024:
            raise ValueError('ASR response exceeds bound')
        line = raw.decode('utf-8').rstrip('\r\n')
        if line.startswith('data:'):
            data.append(line[5:].lstrip())
        elif not line and data:
            payload = '\n'.join(data); data = []
            if payload == '[DONE]':
                continue
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError('Invalid ASR event')
            if value.get('code'):
                raise ValueError('ASR service error')
            output = value.get('output', {})
            if not isinstance(output, dict):
                raise ValueError('Invalid ASR output')
            sentence = output.get('sentence')
            if not sentence:
                continue
            if not isinstance(sentence, dict):
                raise ValueError('Invalid ASR sentence')
            key = (sentence.get('channel_id', 0), sentence.get('sentence_id'))
            if any(type(part) is not int or part < 0 for part in key):
                raise ValueError('Invalid ASR sentence identity')
            text = sentence.get('text', '')
            if not isinstance(text, str):
                raise ValueError('Invalid ASR text')
            if sentence.get('sentence_end') is True:
                pending.discard(key)
                if text.strip():
                    start, end = sentence.get('begin_time'), sentence.get('end_time')
                    if type(start) is not int or type(end) is not int or not 0 <= start < end:
                        raise ValueError('Invalid ASR timestamps')
                    final[key] = {'text': text.strip(), 'start_ms': start, 'end_ms': end}
            elif text.strip() and key not in final:
                pending.add(key)
    if data or pending or not final:
        raise ValueError('ASR stream incomplete or empty')
    ordered = sorted(final.values(), key=lambda x: (x['start_ms'], x['end_ms']))
    text = '\n'.join(x['text'] for x in ordered)
    if len(text) > 16000:
        raise ValueError('ASR transcript too large')
    return {'text': text, 'segments': ordered, 'provider': 'fun-asr-realtime'}


class FunASRTranscriber:
    endpoint = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'

    def __init__(self, *, key=None, opener=None):
        self.key = key if key is not None else os.environ.get('GRASPMEMOEDU_ASR_API_KEY', '')
        self.opener = opener or urllib.request.urlopen
        self.slots = threading.BoundedSemaphore(2)

    @property
    def configured(self):
        return bool(self.key and self.key != 'PENDING_LOCAL_ENTRY')

    def __call__(self, data):
        if not self.configured:
            raise DomainError('transcription_unavailable', '语音转写尚未配置；可直接填写文字答案。', 503)
        if not self.slots.acquire(blocking=False):
            raise DomainError('transcription_busy', '语音转写正在处理其他录音，请稍后重试。', 429)
        try:
            payload = {'model': 'fun-asr-realtime', 'input': {'messages': [{'role': 'user', 'content': [
                {'audio': 'data:audio/wav;base64,' + base64.b64encode(data).decode('ascii')}]}]},
                'parameters': {'format': 'wav'}, 'resources': []}
            request = urllib.request.Request(self.endpoint, method='POST', data=json.dumps(payload).encode(),
                headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json', 'X-DashScope-SSE': 'enable'})
            with self.opener(request, timeout=90) as response:
                if 'text/event-stream' not in response.headers.get('Content-Type', ''):
                    raise ValueError('ASR did not provide SSE')
                return _sentences(response)
        except (OSError, ValueError, urllib.error.URLError):
            raise DomainError('transcription_failed', '转写未完成，请重试或直接填写答案；本次未计入作答。', 502) from None
        finally:
            self.slots.release()
