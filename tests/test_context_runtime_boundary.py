"""Audio must pass the complete application's two body-limit boundaries."""
import base64
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient

from graspmemoedu.api import create_app
from graspmemoedu.atomic import AtomicLearningService
from graspmemoedu.config import Settings
from tests.atomic_fixtures import atomic_topic
from tests.fixtures import temporary_test_directory


class StubTranscriber:
    configured = True

    def __init__(self):
        self.calls = []

    def __call__(self, data):
        self.calls.append(data)
        return {'text': 'Synthetic recording.', 'provider': 'test-stub', 'segments': []}


class ContextRuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.topic = atomic_topic('english', 'audio-boundary')
        self.card = self.topic['flashcards'][0]
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(16000)
            recording.writeframes(b'\x00\x00' * 16000)
        self.wav = stream.getvalue()
        self.card['audio'] = {'version': 1, 'asset': 'audio/' + hashlib.sha256(self.wav).hexdigest() + '.wav',
                              'mime_type': 'audio/wav', 'mode': 'read_aloud', 'transcript': 'Synthetic reference.'}
        (self.root / 'audio').mkdir()
        (self.root / self.card['audio']['asset']).write_bytes(self.wav)
        (self.root / 'topic.json').write_text(json.dumps(self.topic), encoding='utf-8')
        (self.root / 'catalog.json').write_text(json.dumps({'courses': [{
            'id': 'audio-course', 'title': 'Synthetic course',
            'topics': [{'id': self.topic['id'], 'path': 'topic.json'}],
        }]}), encoding='utf-8')
        (self.root / 'subjects.json').write_text(json.dumps({'subjects': [
            {'id': 'english', 'course_ids': ['audio-course']},
        ]}), encoding='utf-8')
        (self.root / 'test-materials.json').write_text(json.dumps({
            'schema': 1, 'topics': [self.topic['id']],
        }), encoding='utf-8')
        self.transcriber = StubTranscriber()
        with patch('graspmemoedu.context_library_api.FunASRTranscriber', return_value=self.transcriber):
            self.app = create_app(Settings(data_dir=self.root), AtomicLearningService(self.topic, self.root))
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8767')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.base_headers = {'Origin': 'http://127.0.0.1:8767'}
        self.base_headers['X-Learning-Session'] = self.client.get('/api/session', headers=self.base_headers).json()['session_token']
        self.headers = self.account()
        self.url = ('/api/training-audio/transcribe?topic_id=' + self.topic['id']
                    + '&subject_id=english&question_id=' + self.card['id'])
        self.payload = {'content_version': self.topic['version'], 'mime_type': 'audio/wav',
                        'data_base64': base64.b64encode(self.wav).decode('ascii')}

    def account(self, features=None):
        store = self.app.state.access_store
        account = store.create_account('Synthetic audio learner', features=features, purpose='test',
                                       is_advanced_learner=True, is_student=True)
        token = store.login(account['invitation_code'])['identity_token']
        return {**self.base_headers, 'X-Learning-Identity': token}

    def test_real_one_second_wave_crosses_full_app_boundary_without_external_asr(self):
        self.assertGreater(len(json.dumps(self.payload)), 16384)
        response = self.client.post(self.url, headers=self.headers, json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['text'], 'Synthetic recording.')
        self.assertEqual(self.transcriber.calls, [self.wav])

    def test_ordinary_requests_still_have_the_original_small_limit(self):
        response = self.client.post('/api/feedback', headers=self.headers, json={'message': 'x' * 20000})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.transcriber.calls, [])

    def test_oversized_audio_is_rejected_even_with_underreported_content_length(self):
        content = json.dumps({'data_base64': 'A' * (6 * 1024 * 1024)})
        for extra in ({}, {'Content-Length': '1'}):
            with self.subTest(headers=extra):
                response = self.client.post(self.url, headers={**self.headers, 'Content-Type': 'application/json', **extra},
                                            content=content)
                self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.transcriber.calls, [])

    def test_large_valid_recording_still_requires_submission_permission(self):
        response = self.client.post(self.url, headers=self.account(features=['learn']), json=self.payload)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()['detail']['code'], 'feature_forbidden')
        self.assertEqual(self.transcriber.calls, [])


if __name__ == '__main__':
    unittest.main()
