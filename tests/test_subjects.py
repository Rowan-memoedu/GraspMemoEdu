"""Subject scopes are organizational boundaries and never expand Topic access."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
import zipfile

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.access import AccessStore
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService, DomainError
from graspmemoedu.subjects import Subjects
from graspmemoedu.site_backup import backup_site, restore_site
from tests.fixtures import synthetic_course, temporary_test_directory, DeterministicJudge


class SubjectAcceptanceTests(unittest.TestCase):
    origin = 'https://subjects.test'

    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'active'
        self.data.mkdir()
        courses = []
        self.topics = {}
        for suffix in ('a', 'b', 'c'):
            topic = synthetic_course(1)
            topic['id'] = 'topic-' + suffix
            self.topics[topic['id']] = topic
            path = self.data / (topic['id'] + '.json')
            path.write_text(json.dumps(topic), encoding='utf-8')
            courses.append({'id': 'course-' + suffix, 'title': 'Course ' + suffix,
                            'topics': [{'id': topic['id'], 'path': path.name}]})
        (self.data / 'catalog.json').write_text(json.dumps({'courses': courses}), encoding='utf-8')
        self.config = {'subjects': [{'id': 'physics', 'course_ids': ['course-b'], 'guide_path': 'physics/guide.md'}]}
        self.write_config()
        for subject, folder in [('math', 'guide'), ('physics', 'physics')]:
            directory = self.data / folder
            directory.mkdir()
            (directory / 'guide.md').write_text('# ' + subject + '\n![figure](plot.png)', encoding='utf-8')
            (directory / 'plot.png').write_bytes(b'\x89PNG\r\n\x1a\n' + subject.encode())
            (directory / 'unreferenced.txt').write_text('PRIVATE_UNREFERENCED', encoding='utf-8')
        self.settings = Settings(data_dir=self.data, public_api_origin=self.origin)
        self.app = create_app(self.settings, LearningService(self.topics['topic-a'], self.data, DeterministicJudge()))
        self.client = TestClient(self.app, base_url=self.origin)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'Origin': self.origin}
        self.guest = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = self.guest['identity_token']

    def write_config(self):
        (self.data / 'subjects.json').write_text(json.dumps(self.config), encoding='utf-8')

    def call(self, method, path, payload=None, status=200):
        response = self.client.request(method, path, headers=self.headers, **({'json': payload} if payload is not None else {}))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def login_account(self, topics=None):
        account = self.app.state.access_store.create_account('Subject fixture account', topic_ids=topics, purpose='test')
        login = self.call('POST', '/api/access/login', {'invitation_code': account['invitation_code']})
        self.headers['X-Learning-Identity'] = login['identity_token']
        return login

    def test_defaults_empty_shell_and_no_private_configuration(self):
        result = self.call('GET', '/api/subjects')
        self.assertEqual(result['brand'], 'GraspMemoEdu')
        self.assertEqual([s['id'] for s in result['subjects']], ['math', 'physics', 'english', 'chinese', 'biology', 'chemistry', 'cs'])
        self.assertEqual([s['course_count'] for s in result['subjects']], [2, 1, 0, 0, 0, 0, 0])
        self.assertNotIn('guide_path', json.dumps(result))
        self.assertNotIn(str(self.data), json.dumps(result))
        self.assertTrue(all(s['available'] for s in result['subjects']))
        self.assertFalse(any(s['requires_account'] for s in result['subjects']))
        for subject in ('english', 'chinese', 'biology', 'chemistry', 'cs'):
            self.assertEqual(self.call('GET', '/api/catalog?subject_id=' + subject)['courses'], [])
            self.call('GET', '/api/dashboard?subject_id=' + subject, status=409)
            self.assertEqual(self.call('GET', '/api/guide?subject_id=' + subject)['status'], 'empty')
            self.assertEqual(self.call('GET', '/api/training/catalog?subject_id=' + subject)['banks'], [])
            self.assertEqual(self.call('GET', '/api/review-papers/inbox?subject_id=' + subject)['due_count'], 0)
        self.login_account([])
        self.assertTrue(all(s['available'] for s in self.call('GET', '/api/subjects')['subjects']))
        self.assertEqual(self.call('GET', '/api/catalog?subject_id=english'), {'courses': [], 'selected_course_id': None})
        self.call('GET', '/api/dashboard?subject_id=english', status=409)
        self.assertEqual(self.call('GET', '/api/guide?subject_id=english')['status'], 'empty')
        self.call('GET', '/api/catalog?subject_id=unknown', status=404)

    def test_subject_selection_preserves_legacy_row_and_identity_progress(self):
        self.login_account()
        self.call('POST', '/api/catalog/select', {'course_id': 'course-a'})
        before = self.call('GET', '/api/state?topic_id=topic-a')
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            legacy = con.execute('SELECT * FROM learner_preferences').fetchall()
        self.assertEqual(self.call('GET', '/api/catalog?subject_id=math')['selected_course_id'], 'course-a')
        self.call('POST', '/api/catalog/select?subject_id=physics', {'course_id': 'course-b'})
        self.call('POST', '/api/catalog/select?subject_id=math', {'course_id': 'course-c'})
        self.assertEqual(self.call('GET', '/api/catalog')['selected_course_id'], 'course-a')
        self.assertEqual(self.call('GET', '/api/dashboard?subject_id=physics')['course']['id'], 'course-b')
        self.assertEqual(self.call('GET', '/api/dashboard?subject_id=math')['course']['id'], 'course-c')
        self.assertEqual(before, self.call('GET', '/api/state?topic_id=topic-a&subject_id=math'))
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT * FROM learner_preferences').fetchall(), legacy)
        other = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = other['identity_token']
        self.assertIsNone(self.call('GET', '/api/catalog?subject_id=physics')['selected_course_id'])
        self.assertIsNone(self.call('GET', '/api/catalog?subject_id=math')['selected_course_id'])

    def test_cross_subject_requests_reject_and_permissions_do_not_expand(self):
        self.call('GET', '/api/state?topic_id=topic-a&subject_id=physics', status=403)
        self.call('POST', '/api/catalog/select?subject_id=physics', {'course_id': 'course-a'}, status=403)
        self.call('GET', '/api/dashboard?course_id=course-a&subject_id=physics', status=403)
        self.call('GET', '/api/tasks/lesson:topic-a/answers?subject_id=physics', status=403)
        self.call('POST', '/api/review/begin?topic_id=topic-a&subject_id=physics', {}, status=403)
        account = self.app.state.access_store.create_account('Scoped fixture', topic_ids=['topic-a'], purpose='test')
        login = self.call('POST', '/api/access/login', {'invitation_code': account['invitation_code']})
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.assertFalse(self.call('GET', '/api/catalog?subject_id=physics')['courses'][0]['available'])
        self.call('POST', '/api/catalog/select?subject_id=physics', {'course_id': 'course-b'}, status=403)
        self.call('GET', '/api/state?topic_id=topic-b&subject_id=physics', status=403)

    def test_subject_guide_assets_are_distinct_and_scoped(self):
        self.login_account()
        math = self.call('GET', '/api/guide?subject_id=math')
        physics = self.call('GET', '/api/guide?subject_id=physics')
        self.assertIn('/subject-guide-assets/math/plot.png', math['html'])
        self.assertIn('/subject-guide-assets/physics/plot.png', physics['html'])
        self.assertNotEqual(math['version'], physics['version'])
        for subject in ('math', 'physics'):
            response = self.client.get('/subject-guide-assets/' + subject + '/plot.png', headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertIn(subject.encode(), response.content)
            self.assertEqual(self.client.get('/subject-guide-assets/' + subject + '/unreferenced.txt', headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get('/subject-guide-assets/english/plot.png', headers=self.headers).status_code, 404)
        self.assertIn('/guide-assets/plot.png', self.call('GET', '/api/guide')['html'])

    def test_invalid_subject_config_rejected(self):
        catalog = Catalog.load(self.settings)
        cases = [ {'subjects': [{'id': 'unknown'}]},
                  {'subjects': [{'id': 'physics', 'course_ids': ['missing']}]},
                  {'subjects': [{'id': 'math', 'course_ids': ['course-a']}, {'id': 'physics', 'course_ids': ['course-a']}]},
                  {'subjects': [{'id': 'physics', 'guide_path': '../private.md'}]},
                  {'subjects': [{'id': 'physics', 'guide_path': 'D:/private.md'}]},
                  {'subjects': [{'id': 'physics', 'prompt': 'not implemented'}]},
                  {'subjects': [{'id': 'physics', 'accent': 'url(https://external.invalid)'}]} ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(DomainError):
                Subjects(self.settings, catalog, raw)

    def test_backup_roundtrip_retains_subject_guides_and_preferences(self):
        self.login_account()
        self.call('POST', '/api/catalog/select', {'course_id': 'course-a'})
        self.call('POST', '/api/catalog/select?subject_id=physics', {'course_id': 'course-b'})
        backup = self.root / 'subjects.zip'
        backup_site(self.settings, backup)
        with zipfile.ZipFile(backup) as archive:
            names = archive.namelist()
            self.assertIn('subjects.json', names)
            self.assertIn('subject-guides/physics/plot.png', names)
            self.assertFalse(any('unreferenced' in name for name in names))
            raw = archive.read('subjects.json').decode()
            self.assertNotIn(str(self.data), raw)
        restored = self.root / 'restored'
        restore_site(backup, restored)
        settings = Settings.load(restored)
        subjects = Subjects(settings, Catalog.load(settings))
        self.assertEqual(subjects.course_ids('physics'), {'course-b'})
        self.assertEqual(subjects.guide_paths['physics'].read_text(), (self.data / 'physics/guide.md').read_text())
        with closing(sqlite3.connect(restored / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT subject_id,selected_course_id FROM learner_subject_preferences').fetchall(), [('physics', 'course-b')])
            self.assertEqual(con.execute('SELECT selected_course_id FROM learner_preferences').fetchone()[0], 'course-a')

    def test_backup_rejects_forged_subject_path_before_extraction(self):
        original = self.root / 'original.zip'
        backup_site(self.settings, original)
        with zipfile.ZipFile(original) as archive:
            blobs = {name: archive.read(name) for name in archive.namelist() if name != 'manifest.json'}
            manifest = json.loads(archive.read('manifest.json'))
        raw = json.loads(blobs['subjects.json'])
        raw['subjects'][1]['guide_path'] = '../private.md'
        blobs['subjects.json'] = json.dumps(raw).encode()
        manifest['sha256'] = {name: hashlib.sha256(data).hexdigest() for name, data in blobs.items()}
        forged = self.root / 'forged.zip'
        with zipfile.ZipFile(forged, 'w') as archive:
            for name, data in blobs.items():
                archive.writestr(name, data)
            archive.writestr('manifest.json', json.dumps(manifest))
        destination = self.root / 'rejected'
        with self.assertRaises(DomainError):
            restore_site(forged, destination)
        self.assertFalse(destination.exists())

    def test_absent_config_keeps_every_course_in_math(self):
        subjects = Subjects(self.settings, Catalog.load(self.settings), {'subjects': []})
        self.assertEqual(subjects.course_ids('math'), {'course-a', 'course-b', 'course-c'})
        self.assertEqual(subjects.course_ids('physics'), set())

    def test_guests_can_learn_nonmath_with_or_without_subject_scope(self):
        self.assertEqual({t['id'] for t in self.call('GET', '/api/access')['topics']}, set(self.topics))
        self.assertEqual({c['id'] for c in self.call('GET', '/api/catalog?subject_id=physics')['courses']}, {'course-b'})
        self.call('POST', '/api/catalog/select?subject_id=physics', {'course_id': 'course-b'})
        self.assertEqual(self.call('GET', '/api/dashboard?subject_id=physics')['course']['id'], 'course-b')
        state = self.call('GET', '/api/state?topic_id=topic-b&subject_id=physics')
        self.assertEqual(state, self.call('GET', '/api/state?topic_id=topic-b'))
        chosen = self.call('POST', '/api/choose?topic_id=topic-b&subject_id=physics', {
            'request_id': 'physics-choice', 'expected_revision': state['revision'],
            'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], 'choice': 'mastered'})
        self.assertEqual(chosen['topic_id'], 'topic-b')
        denied = self.call('POST', '/api/pause?topic_id=topic-b&subject_id=physics', {'request_id': 'physics-pause'}, status=403)
        self.assertEqual(denied['detail']['code'], 'manual_pause_removed')
        self.assertEqual(chosen, self.call('GET', '/api/state?topic_id=topic-b'))

    def test_nonmath_assets_accept_guests_and_still_require_valid_identity(self):
        path = '/subject-guide-assets/physics/plot.png'
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers=self.headers).status_code, 200)
        self.assertIn('/subject-guide-assets/physics/plot.png', self.call('GET', '/api/guide?subject_id=physics')['html'])
        self.assertEqual(self.client.get('/subject-guide-assets/math/plot.png').status_code, 200)
        preflight = self.client.options(path, headers={'Origin': self.origin,
            'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'X-Learning-Identity,X-Learning-Session'})
        self.assertEqual(preflight.status_code, 200)
        login = self.login_account([])
        self.assertEqual(self.client.get(path, headers=self.headers).status_code, 200)
        self.app.state.access_store.set_enabled(login['access']['learner_id'], False)
        self.assertEqual(self.client.get(path, headers=self.headers).status_code, 401)

    def test_guest_scope_is_derived_without_rewriting_principals_or_accounts(self):
        from graspmemoedu.cli import make_access_store
        store = AccessStore(self.data, list(self.topics.values()))
        old_guest = store.create_guest()
        account = store.create_account('Existing math only', topic_ids=['topic-a'], purpose='test')
        with closing(sqlite3.connect(store.db_path)) as con:
            before = con.execute('SELECT * FROM access_principals ORDER BY learner_id').fetchall()
        scoped = AccessStore(self.data, list(self.topics.values()), guest_topic_ids=['topic-a', 'topic-c'])
        self.assertEqual({t['id'] for t in scoped.resolve(old_guest['identity_token'])['topics']}, {'topic-a', 'topic-c'})
        self.assertEqual({t['id'] for t in make_access_store(self.settings).resolve(old_guest['identity_token'])['topics']}, set(self.topics))
        login = scoped.login(account['invitation_code'])
        self.assertEqual([t['id'] for t in login['access']['topics']], ['topic-a'])
        with closing(sqlite3.connect(store.db_path)) as con:
            self.assertEqual(con.execute('SELECT * FROM access_principals ORDER BY learner_id').fetchall(), before)


if __name__ == '__main__':
    unittest.main()
