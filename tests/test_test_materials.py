from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.atomic import AtomicLearningService
from graspmemoedu.config import Settings
from graspmemoedu.service import DomainError
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.test_materials import TestMaterials
from tests.fixtures import temporary_test_directory
from atomic_fixtures import atomic_topic
from training_fixtures import training_bank
from test_atomic_api import write_catalog
import test_training_api as helpers


class TestMaterialTests(unittest.TestCase):
    call = helpers.TrainingAPITests.call
    account = helpers.TrainingAPITests.account

    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); write_catalog(self.root)
        (self.root / 'training-banks').mkdir()
        self.bank = training_bank()
        (self.root / 'training-banks/isolated-bank.json').write_text(json.dumps(self.bank), encoding='utf-8')
        self.policy = {'schema': 1, 'courses': ['english-course', 'future-test-course'],
                       'topics': ['atomic-chemistry', 'future-test-topic'],
                       'banks': ['isolated-bank'], 'guides': ['math']}
        (self.root / 'test-materials.json').write_text(json.dumps(self.policy), encoding='utf-8')
        (self.root / 'guide').mkdir()
        (self.root / 'guide/guide.md').write_text('# 管理员测试指南\n![图](test.png)', encoding='utf-8')
        (self.root / 'guide/test.png').write_bytes(b'\x89PNG\r\n\x1a\nTEST')
        self.settings = Settings(data_dir=self.root, public_api_origin='https://training.test', allowed_origins=('https://site.test',))
        service = AtomicLearningService(atomic_topic('english', 'atomic-english'), self.root)
        self.app = create_app(self.settings, service)
        self.client = TestClient(self.app, base_url='https://training.test'); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'Origin': 'https://site.test'}
        login = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = login['identity_token']

    def denied(self):
        access = self.call('GET', '/api/access')
        # Existing access endpoint nests the resolved identity in its response.
        topics = access.get('access', access)['topics']
        self.assertFalse({'atomic-english', 'atomic-chemistry', 'training.isolated-bank'} & {x['id'] for x in topics})
        self.assertEqual(self.call('GET', '/api/catalog?subject_id=english')['courses'], [])
        self.assertEqual(self.call('GET', '/api/catalog?subject_id=chemistry')['courses'], [])
        counts = {x['id']: x['course_count'] for x in self.call('GET', '/api/subjects')['subjects']}
        self.assertEqual(counts['english'], 0); self.assertEqual(counts['chemistry'], 0)
        self.call('GET', '/api/state?topic_id=atomic-english&subject_id=english', status=403)
        self.call('GET', '/api/dashboard?course_id=english-course&subject_id=english', status=403)
        self.call('GET', '/api/graphs/english-course?subject_id=english', status=404)
        self.assertEqual(self.call('GET', '/api/training/catalog?subject_id=math')['banks'], [])
        self.call('GET', '/api/training/bank?bank_id=isolated-bank', status=403)
        self.assertEqual(self.call('GET', '/api/review-papers/inbox?subject_id=math')['due_count'], 0)
        self.call('GET', '/api/guide?subject_id=math', status=404)
        self.call('GET', '/api/guide', status=404)
        for path in ['/guide-assets/test.png', '/subject-guide-assets/math/test.png']:
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 404)
        # Content in an unmarked subject continues to work.
        self.assertEqual(len(self.call('GET', '/api/catalog?subject_id=biology')['courses']), 1)

    def test_guests_and_noneligible_account_combinations_cannot_bypass(self):
        self.denied()
        self.account(is_advanced_learner=True, purpose='test', topic_ids=['atomic-english', 'atomic-chemistry',
                     'atomic-biology', 'training.isolated-bank'])
        self.denied()
        self.account()
        self.denied()
        self.account(is_student=True, purpose='test')
        self.denied()

    def test_advanced_students_can_view_but_explicit_permissions_still_apply(self):
        self.account(is_advanced_learner=True, is_student=True, purpose='learner')
        self.denied()
        self.account(is_advanced_learner=True, is_student=True, purpose='test')
        self.assertEqual(len(self.call('GET', '/api/catalog?subject_id=english')['courses']), 1)
        self.assertEqual(len(self.call('GET', '/api/training/catalog')['banks']), 1)
        self.assertEqual(self.call('GET', '/api/guide?subject_id=math')['status'], 'ready')
        self.assertEqual(self.client.get('/guide-assets/test.png', headers=self.headers).status_code, 200)
        self.account(is_advanced_learner=True, is_student=True, purpose='test', topic_ids=[])
        self.call('GET', '/api/state?topic_id=atomic-english&subject_id=english', status=403)
        self.call('GET', '/api/training/bank?bank_id=isolated-bank', status=403)

    def test_admin_sees_original_materials_and_protected_guide_assets(self):
        before = hashlib.sha256((self.root / 'training-banks/isolated-bank.json').read_bytes()).hexdigest()
        self.account(is_admin=True, purpose='test')
        self.assertEqual(len(self.call('GET', '/api/catalog?subject_id=english')['courses']), 1)
        self.assertEqual(len(self.call('GET', '/api/training/catalog')['banks']), 1)
        self.assertEqual(self.call('GET', '/api/state?topic_id=atomic-english')['access']['is_admin'], True)
        graph = self.call('GET', '/api/graphs/english-course?subject_id=english')
        self.assertEqual(graph['nodes'][0]['topic_id'], 'atomic-english')
        self.assertEqual(self.call('GET', '/api/guide?subject_id=math')['status'], 'ready')
        for path in ['/guide-assets/test.png', '/subject-guide-assets/math/test.png']:
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 200)
        self.assertEqual(hashlib.sha256((self.root / 'training-banks/isolated-bank.json').read_bytes()).hexdigest(), before)

    def test_named_formal_account_grant_uses_real_api_without_role_change(self):
        store=self.app.state.access_store
        created=store.create_account('Selected formal learner',purpose='learner',is_student=True,is_advanced_learner=True)
        learner_id=created['account']['learner_id']
        other=store.create_account('Unrelated formal learner',purpose='learner')
        policy=dict(self.policy,account_grants={learner_id:{'courses':['english-course'],'banks':['isolated-bank']}})
        (self.root/'test-materials.json').write_text(json.dumps(policy),encoding='utf-8')
        app=create_app(self.settings,AtomicLearningService(atomic_topic('english','atomic-english'),self.root))
        with TestClient(app,base_url='https://training.test') as client:
            headers={'Origin':'https://site.test'}
            login=client.post('/api/access/login',headers=headers,json={'invitation_code':created['invitation_code']}).json()
            self.assertEqual(login['access']['purpose'],'learner')
            self.assertFalse(login['access'].get('is_admin'))
            headers['X-Learning-Identity']=login['identity_token']
            courses=client.get('/api/catalog?subject_id=english',headers=headers).json()['courses']
            self.assertEqual([c['id'] for c in courses],['english-course'])
            self.assertEqual(client.get('/api/training/bank?bank_id=isolated-bank',headers=headers).status_code,200)
            self.assertEqual(client.get('/api/state?topic_id=atomic-chemistry&subject_id=chemistry',headers=headers).status_code,403)
            self.assertEqual(client.get('/api/guide?subject_id=math',headers=headers).status_code,404)
            _,parameters=app.state.help_requests.visible_scope('',(),login['access'])
            self.assertNotIn('training.isolated-bank',json.loads(parameters[-1]))
            self.assertIn('atomic-chemistry',json.loads(parameters[-1]))
            app.state.access_store.set_permissions(learner_id,topic_ids=[])
            self.assertEqual(client.get('/api/training/bank?bank_id=isolated-bank',headers=headers).status_code,403)
            login=client.post('/api/access/login',headers={'Origin':'https://site.test'},json={'invitation_code':other['invitation_code']}).json()
            headers['X-Learning-Identity']=login['identity_token']
            self.assertEqual(client.get('/api/catalog?subject_id=english',headers=headers).json()['courses'],[])
            self.assertEqual(client.get('/api/training/bank?bank_id=isolated-bank',headers=headers).status_code,403)

    def test_formal_admin_cannot_bypass_material_inventory_or_bank_content(self):
        self.account(is_admin=True, purpose='learner')
        self.denied()
        items = self.call('GET', '/api/materials')['items']
        self.assertFalse({'atomic-english', 'atomic-chemistry'} & {i['topic_id'] for i in items})
        self.assertIn('atomic-biology', {i['topic_id'] for i in items})
        self.call('GET', '/api/materials/content?topic_id=atomic-english', status=403)
        self.call('GET', '/api/materials/content?topic_id=training.isolated-bank&bank_id=isolated-bank&group_id=group-a', status=403)
        self.assertEqual(self.call('GET', '/api/review-papers/inbox?subject_id=english&materials=1')['due_count'], 0)
        self.assertEqual(self.call('GET', '/api/review-papers/inbox?subject_id=math&materials=1')['due_count'], 0)
        self.account(is_admin=True, purpose='test')
        self.assertTrue(self.call('GET', '/api/materials/content?topic_id=training.isolated-bank&bank_id=isolated-bank&group_id=group-a')['blocks'])
        self.assertGreater(self.call('GET', '/api/review-papers/inbox?subject_id=math&materials=1')['due_count'], 0)

    def test_policy_survives_backup_and_does_not_change_source_versions(self):
        archive = self.root / 'backup.zip'; backup_site(self.settings, archive)
        target = self.root / 'restored'; restore_site(archive, target)
        self.assertEqual(json.loads((target / 'test-materials.json').read_text()), self.policy)
        self.assertEqual(json.loads((target / 'training-banks/isolated-bank.json').read_text())['version'], self.bank['version'])
        from graspmemoedu.catalog import Catalog
        policy = TestMaterials(target, Catalog.load(Settings(data_dir=target)))
        self.assertIn('atomic-english', policy.permission_ids)
        self.assertIn('training.isolated-bank', policy.permission_ids)

    def test_bad_policy_is_rejected_and_future_materials_stay_restricted(self):
        policy = self.app.state.access_store.test_materials
        self.assertFalse(policy.graph_topic_visible('future-test-course', 'future-child', {'role': 'account'}))
        self.assertFalse(policy.graph_topic_visible('ordinary-course', 'future-test-topic', {'role': 'account'}))
        (self.root / 'test-materials.json').write_text('{"schema":1,"topics":"all"}', encoding='utf-8')
        with self.assertRaises(DomainError):
            TestMaterials(self.root)

    def test_saved_help_snapshots_obey_policy_without_deleting_history(self):
        access_store = self.app.state.access_store
        with access_store._transaction() as con:
            admin = access_store._insert_account(con, '历史材料管理员', is_admin=True, purpose='test')
        admin_headers = {**self.headers, 'X-Learning-Identity': access_store.login(admin['invitation_code'])['identity_token']}
        self.app.state.help_requests.set_default_admin(admin['account']['learner_id'])
        self.account(is_student=True, purpose='test')
        student_headers = dict(self.headers)
        policy = access_store.test_materials
        policy.permission_ids.remove('training.isolated-bank')
        try:
            saved = self.call('POST', '/api/help-requests', {'request_id': str(uuid.uuid4()), 'message': '测试答疑',
                'topic_id': 'training.isolated-bank', 'bank_id': 'isolated-bank', 'group_id': 'group-a',
                'question_id': 'essay', 'content_block_id': 'question:essay', 'content_version': self.bank['version']})['id']
            visible = self.call('POST', '/api/help-requests', {'request_id': str(uuid.uuid4()), 'message': '普通答疑',
                'topic_id': 'atomic-biology', 'content_block_id': 'introduction', 'content_version': 'atomic-fixture-v1'})['id']
            self.assertEqual(self.call('GET', '/api/help-center')['total'], 2)
        finally:
            policy.permission_ids.add('training.isolated-bank')
        self.headers = admin_headers
        self.call('POST', '/api/help-replies', {'request_id': str(uuid.uuid4()), 'report_id': saved,
                                             'expected_revision': 0, 'message': '保留管理员回复'})
        self.headers = student_headers
        self.assertEqual(self.call('GET', '/api/help-center?summary=true')['total'], 1)
        result = self.call('GET', '/api/help-center?limit=1')
        self.assertEqual([item['id'] for item in result['items']], [visible])
        self.assertIsNone(result['next_offset']); self.assertEqual(result['unread_count'], 0)
        self.call('GET', '/api/help-center?report_id=' + saved, status=404)
        self.call('POST', '/api/help-replies/read', {'report_id': saved, 'revision': 1}, status=404)
        self.headers = admin_headers
        self.assertEqual(self.call('GET', '/api/help-center')['total'], 2)
        kept = self.call('GET', '/api/help-center?report_id=' + saved)['items'][0]
        self.assertEqual(kept['content_html'], self.bank['groups'][1]['questions'][0]['html'])
        self.assertEqual(kept['reply']['message'], '保留管理员回复')
        # Changing only account purpose revokes every administrator snapshot path.
        with access_store._transaction() as con:
            con.execute('UPDATE access_principals SET purpose=? WHERE learner_id=?', ('learner', admin['account']['learner_id']))
        self.assertEqual(self.call('GET', '/api/help-center')['total'], 1)
        self.assertEqual(self.call('GET', '/api/help-requests?limit=1')['total'], 1)
        self.assertIsNone(self.call('GET', '/api/help-requests?limit=1')['next_offset'])
        self.call('GET', '/api/help-requests?report_id=' + saved, status=404)
        self.call('GET', '/api/help-center?report_id=' + saved, status=404)
        self.call('POST', '/api/help-replies', {'request_id': str(uuid.uuid4()), 'report_id': saved,
                                             'expected_revision': 1, 'message': '不得写入'}, status=404)
        with access_store._transaction() as con:
            con.execute('UPDATE access_principals SET purpose=? WHERE learner_id=?', ('test', admin['account']['learner_id']))
        self.assertEqual(self.call('GET', '/api/help-requests')['total'], 2)
        self.assertEqual(self.call('GET', '/api/help-requests?report_id=' + saved)['id'], saved)
        self.headers = student_headers
        learner = access_store.resolve(self.headers['X-Learning-Identity'])['learner_id']
        access_store.set_permissions(learner, is_advanced_learner=True)
        self.assertEqual(self.call('GET', '/api/help-center')['total'], 2)
        self.assertEqual(self.call('GET', '/api/help-center?report_id=' + saved)['items'][0]['id'], saved)
        self.call('POST', '/api/help-replies/read', {'report_id': saved, 'revision': 1})
        access_store.set_permissions(learner, topic_ids=['atomic-biology'])
        self.call('GET', '/api/help-center?report_id=' + saved, status=404)
        self.call('POST', '/api/help-replies/read', {'report_id': saved, 'revision': 1}, status=404)
        access_store.set_permissions(learner, is_advanced_learner=False)
        self.call('GET', '/api/help-center?report_id=' + saved, status=404)

    def test_guide_source_alias_cannot_bypass_admin_only_access(self):
        # Different subject URLs may point at the very same Markdown source.
        service = AtomicLearningService(atomic_topic('english', 'atomic-english'), self.root)
        config = json.loads((self.root / 'subjects.json').read_text(encoding='utf-8'))
        next(s for s in config['subjects'] if s['id'] == 'english')['guide_path'] = 'guide/guide.md'
        (self.root / 'subjects.json').write_text(json.dumps(config), encoding='utf-8')
        aliased = create_app(self.settings, service)
        with TestClient(aliased, base_url='https://training.test') as client:
            for path in ['/api/guide?subject_id=english', '/subject-guide-assets/english/test.png']:
                response = client.get(path, headers=self.headers)
                self.assertEqual(response.status_code, 404, response.text)
            self.account(is_admin=True, purpose='test')
            for path in ['/api/guide?subject_id=english', '/subject-guide-assets/english/test.png']:
                self.assertEqual(client.get(path, headers=self.headers).status_code, 200)


if __name__ == '__main__':
    unittest.main()
