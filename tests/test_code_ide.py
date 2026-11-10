from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import json
import hashlib
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch
from tests.fixtures import temporary_test_directory

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu import code_judges
from graspmemoedu.hyperskill_training import HyperskillCodeTraining, HyperskillTraining, external_training
from graspmemoedu.service import DomainError
from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import TrainingCatalog, fingerprint, validate
import test_hyperskill_training as fixtures


class LocalCodeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HyperskillBankTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.fixture.native()
        self.data, self.now = self.fixture.data, self.fixture.now
        self.bank = deepcopy(self.fixture.bank)
        self.question = self.bank['groups'][1]['questions'][0]
        self.question['hyperskill']['type'] = 'code'
        self.bank['version'] = fingerprint(self.bank); validate(self.bank)
        (self.data / 'training-banks/hyperskill-practice.json').write_text(json.dumps(self.bank), encoding='utf-8')
        self.training = TrainingService(TrainingCatalog(self.data), self.data, 'learner', clock=lambda: self.now)
        self.definition = {'engine': code_judges.ENGINE, 'language': 'java25',
                           'source_sha256': code_judges.source_signature(self.question), 'package_sha256': 'a' * 64}

    def register(self, definition=None, revoked=None):
        value = {'schema': 1, 'bindings': {self.bank['id'] + '/' + self.question['id']: definition or self.definition},
                 'revoked': revoked or []}
        (self.data / 'code-judges.json').write_text(json.dumps(value), encoding='utf-8')

    def begin(self):
        aid = self.training.open(self.bank['id'], self.fixture.old['id'], self.question['id'], str(uuid.uuid4()))
        adapter = external_training(self.training, aid); run = adapter.launch()
        adapter.opened(run['round_id'], aid)
        return adapter, run

    def event(self, number=1, category='passed', definition=None):
        return {'id': str(uuid.uuid4()), 'sequence': number, 'category': category,
                'code_hash': str(number % 10) * 64, 'occurred_at': self.now.isoformat(),
                'local_code': {**(definition or self.definition), 'feedback': 'check result'}}

    def test_native_check_uses_original_attempt_card_and_idempotent_scoring(self):
        self.register(); adapter, run = self.begin()
        self.assertIsInstance(adapter, HyperskillCodeTraining)
        self.training.add_to_notebook(adapter.attempt_id)
        claim = adapter.claim(run['round_id'])
        self.assertEqual(claim['task_key'], self.question['id'])
        self.assertEqual(claim['version'], self.definition['source_sha256'])
        failed = self.event(category='wrong'); adapter.check_event(run['round_id'], run['step_id'], failed)
        self.assertTrue(adapter.check_event(run['round_id'], run['step_id'], failed)['duplicate'])
        passed = self.event(2); result = adapter.check_event(run['round_id'], run['step_id'], passed)
        self.assertEqual(result['rating'], 2)
        self.assertTrue(adapter.check_event(run['round_id'], run['step_id'], passed)['duplicate'])
        state = self.training.state(adapter.attempt_id)
        self.assertEqual(state['hyperskill']['local_ide']['checks'], 2)
        self.assertEqual(state['hyperskill']['submissions'], [])
        with closing(self.training._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_memory').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_ratings').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT question FROM training_memory').fetchone()[0], self.question['id'])

    def test_old_official_round_and_existing_card_survive_registration(self):
        old, run = self.begin(); self.training.add_to_notebook(old.attempt_id)
        self.register()
        self.assertIs(type(external_training(self.training, old.attempt_id)), HyperskillTraining)
        self.assertNotIn('local_ide', self.training.state(old.attempt_id)['hyperskill'])
        event = self.fixture.event(); event['receipt']['reply'] = {'language': 'java25', 'code': 'class Main {}'}
        old.check_event(run['round_id'], run['step_id'], event)
        self.now += timedelta(days=30)
        new, next_run = self.begin()
        self.assertIsInstance(new, HyperskillCodeTraining)
        new.check_event(next_run['round_id'], next_run['step_id'], self.event())
        with closing(self.training._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_memory').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_ratings').fetchone()[0], 2)

    def test_pin_test_version_reject_wrong_package_and_revocation(self):
        self.register(); adapter, run = self.begin()
        changed = {**self.definition, 'package_sha256': 'b' * 64}; self.register(changed)
        self.assertEqual(adapter.claim(run['round_id'])['code_judge'], self.definition)
        with self.assertRaises(DomainError):
            adapter.check_event(run['round_id'], run['step_id'], self.event(definition=changed))
        self.register(changed, ['a' * 64])
        with self.assertRaises(DomainError):
            adapter.check_event(run['round_id'], run['step_id'], self.event())
        with closing(self.training._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_ratings').fetchone()[0], 0)

    def test_environment_event_never_becomes_a_failed_answer(self):
        self.register(); adapter, run = self.begin()
        adapter.check_event(run['round_id'], run['step_id'], self.event(category='environment'))
        with self.assertRaises(DomainError): adapter.finish()
        result = adapter.check_event(run['round_id'], run['step_id'], self.event(2))
        self.assertEqual(result['failures'], 0)
        self.assertEqual(self.training.state(adapter.attempt_id)['result']['rating'], 5)

    def test_native_launch_reuses_same_capability_api_and_source_version(self):
        self.register()
        app = create_app(Settings(data_dir=self.data, public_api_origin='https://local-code.test'), atomic_clock=lambda: self.now)
        with TestClient(app, base_url='https://local-code.test') as client:
            headers = {'origin': 'https://local-code.test'}
            identity = client.post('/api/access/guest', json={}, headers=headers).json()
            headers['x-learning-identity'] = identity['identity_token']
            response = client.post('/api/training/open?subject_id=cs', headers=headers, json={
                'request_id': str(uuid.uuid4()), 'bank_id': self.bank['id'], 'group_id': self.fixture.old['id'],
                'question_id': self.question['id']})
            self.assertEqual(response.status_code, 200, response.text)
            state = response.json(); self.assertIn('local_ide', state['hyperskill'])
            grant = client.post('/api/academy/training/launch', headers=headers, json={'attempt_id': state['attempt_id']}).json()
            self.assertNotIn('ticket', grant)
            self.assertTrue(grant['url'].startswith('graspmemoedu://academy/open?ticket='))
            plugin = {'origin': headers['origin'], 'authorization': 'Bearer ' + grant['url'].split('ticket=')[1]}
            claim = client.post('/api/academy/claim', json={}, headers=plugin)
            self.assertEqual(claim.status_code, 200, claim.text)
            self.assertEqual(claim.json()['code_judge'], self.definition)
            client.post('/api/academy/event', headers=plugin, json={'kind': 'opened'}).raise_for_status()
            checked = client.post('/api/academy/event', headers=plugin, json={'kind': 'check', 'event': self.event()})
            self.assertEqual(checked.status_code, 200, checked.text)
            client.post('/api/access/logout', headers=headers, json={})
            self.assertEqual(client.post('/api/academy/claim', headers=plugin, json={}).status_code, 401)

    def test_registry_in_portable_backup_and_restore(self):
        from graspmemoedu.site_backup import backup_site, restore_site
        from graspmemoedu.catalog import Catalog
        from graspmemoedu.service import create_learning_service
        self.register()
        settings = Settings.load(self.data)
        catalog = Catalog.load(settings)
        create_learning_service(catalog.topic(catalog.default_topic_id), self.data).state()
        output = self.fixture.root / 'code-backup.zip'
        backup_site(settings, output)
        restored = self.fixture.root / 'code-restored'
        restore_site(output, restored)
        self.assertEqual(code_judges.registry(restored), code_judges.registry(self.data))


class LocalCodeRuntimeContractTests(unittest.TestCase):
    def test_immutable_package_and_runner_checked_before_executing_code(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        import code_check
        with temporary_test_directory() as folder:
            root = Path(folder); project = root / 'course'; project.mkdir()
            package = {'schema': 1, 'approved': True, 'source_sha256': 'b' * 64,
                       'engine': code_judges.ENGINE, 'language': 'java25', 'bundle': {},
                       'runner_sha256': code_check.sha((code_check.ROOT / 'scripts/hyperskill_code_trial/core.py').read_bytes())}
            raw = json.dumps(package).encode(); digest = hashlib.sha256(raw).hexdigest()
            definition = {**{k: package[k] for k in ('source_sha256', 'engine', 'language')}, 'package_sha256': digest}
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'tasks': {'q': {'project_root': str(project), 'task_path': 'Practice/task', 'code_judge': definition}}}))
            config = root / 'config.json'
            config.write_text(json.dumps({'manifest': str(manifest), 'code_checker': {'packages': str(root)}}))
            immutable = root / (digest + '.json'); immutable.write_bytes(raw)
            request = {'project_root': str(project), 'task_path': 'Practice/task', 'code': 'learner code'}
            with patch.object(code_check, 'execute', return_value={'verdict': 'accepted'}) as run:
                self.assertEqual(code_check.check(config, request)['status'], 'Solved')
                run.assert_called_once()
                run.reset_mock()
                immutable.write_bytes(raw + b' ')
                with self.assertRaisesRegex(ValueError, 'checksum'): code_check.check(config, request)
                run.assert_not_called()
                immutable.write_bytes(raw)
                with patch.object(code_check, 'sha', side_effect=[digest, 'c' * 64]):
                    with self.assertRaisesRegex(ValueError, 'current runtime'): code_check.check(config, request)
                run.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'not unique'):
                    code_check.check(config, {**request, 'task_path': 'Practice/other'})
                run.assert_not_called()

    def test_runtime_failure_mapping(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        from code_check import verdict
        self.assertEqual(verdict({'verdict': 'test_error'})[0], 'Unchecked')
        self.assertEqual(verdict({'verdict': 'compile_error'})[:2], ('Failed', '编译错误。'))
        self.assertEqual(verdict({'verdict': 'accepted'})[0], 'Solved')
        self.assertEqual(verdict({'verdict': 'runtime_error', 'tests': [{'passed': False, 'timed_out': True}]})[1], '运行超时。')


if __name__ == '__main__': unittest.main()
