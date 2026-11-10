"""Bank question references share existing attempts and never clone scheduling."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import unittest
import uuid

from graspmemoedu.author_import import prepare_topic
from graspmemoedu.service import create_learning_service, DomainError
from graspmemoedu.topic_format import normalize_document, dump_markdown, parse_source, semantic_payload
from graspmemoedu.topic_runtime import validate_runtime
from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import TrainingCatalog
from graspmemoedu.materials import topic_blocks
from tests.training_fixtures import training_bank
from tests.test_ordered_learning import ordered_topic, payload, active
from tests.fixtures import temporary_test_directory


def reference(bank):
    return {'bank_id': bank['id'], 'group_id': 'group-a', 'question_id': 'exact', 'version': bank['version']}


def composed_topic(bank):
    value = ordered_topic(True)
    value['sections'] = [{'id': 'section-a', 'title': '一、小标题', 'level': 1, 'parent_id': None}]
    for intro in value['introductions']:
        intro['section_id'] = 'section-a'
    value['introductions'][0].update(example='<p>纯说明的例子</p>', footnotes=[{'id': 'note-1', 'html': '<p>原脚注</p>'}])
    value['bank_questions'] = [{'id': 'worked-example', 'section_id': 'section-a', 'reference': reference(bank)}]
    value['content_order'].insert(1, {'kind': 'bank_question', 'id': 'worked-example'})
    return value


class TopicCompositionTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bank = training_bank()
        (self.root/'training-banks').mkdir()
        (self.root/'training-banks/isolated-bank.json').write_text(json.dumps(self.bank), encoding='utf-8')
        self.catalog = TrainingCatalog(self.root)
        self.training = TrainingService(self.catalog, self.root, 'learner')
        self.topic = composed_topic(self.bank)

    def service(self, topic=None, learner='learner'):
        return create_learning_service(deepcopy(topic or self.topic), self.root, learner_id=learner)

    def solve(self, training=None):
        training = training or self.training
        attempt = training.open(self.bank['id'], 'group-a', 'exact', str(uuid.uuid4()))
        submission = training.submit({'attempt_id': attempt, 'request_id': str(uuid.uuid4()), 'answer': '2'})
        training.process_submission(submission)
        self.assertEqual(training.state(attempt)['phase'], 'done')
        return attempt

    def test_required_question_reuses_bank_completion_and_one_record(self):
        service = self.service()
        first = active(service.state())
        self.assertIn('纯说明', first['example']); self.assertEqual(first['footnotes'][0]['id'], 'note-1')
        self.assertEqual(first['section']['title'], '一、小标题')
        state = service.advance(payload(service)); self.assertEqual(active(state)['kind'], 'bank_question')
        self.assertNotIn('continue', active(state)['actions'])
        with self.assertRaises(DomainError) as raised:
            service.advance(payload(service))
        self.assertEqual(raised.exception.code, 'bank_question_required')
        with self.assertRaises(DomainError) as skipped:
            service.skip_module(payload(service))
        self.assertEqual(skipped.exception.code, 'skip_unavailable')
        self.solve()
        self.assertIn('continue', active(service.state())['actions'])
        request = payload(service)
        service.advance(request); service.advance(request)
        state = service.advance(payload(service)); self.assertEqual(state['status'], 'completed')
        with closing(service._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM training_attempts').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM submissions').fetchone()[0], 0)
        self.assertEqual(state['reading_progress'], 100)

    def test_different_learner_and_pinned_version_do_not_complete_reference(self):
        self.solve()
        other = self.service(learner='someone-else'); other.advance(payload(other))
        self.assertFalse(active(other.state())['bank_completed'])
        stale = deepcopy(self.topic); stale['bank_questions'][0]['reference']['version'] = 'f'*64
        learner = self.service(stale, 'stale'); learner.advance(payload(learner))
        self.assertFalse(active(learner.state())['bank_completed'])

    def test_bank_first_and_bank_only_topic(self):
        value = deepcopy(self.topic); value['introductions'] = []
        value['content_order'] = [value['content_order'][1]]
        service = self.service(value)
        self.assertEqual(active(service.state())['kind'], 'bank_question')
        self.solve(); self.assertEqual(service.advance(payload(service))['status'], 'completed')

    def test_catalog_material_projection_uses_canonical_question(self):
        blocks = topic_blocks(self.topic, self.catalog)
        self.assertEqual([b['kind'] for b in blocks], ['introduction', 'question', 'explanation', 'introduction'])
        self.assertEqual(blocks[1]['html'], self.catalog.question(self.bank['id'], 'group-a', 'exact')[2]['html'])
        self.assertEqual(blocks[1]['bank_reference'], reference(self.bank))

    def test_old_reading_progress_is_carried_but_new_question_is_required(self):
        old = self.service(ordered_topic(True))
        old.advance(payload(old)); old.advance(payload(old))
        completed_at = old.state()['completed_at']
        new = deepcopy(self.topic); new.update(version='composition-artifact', content_version='composition-content')
        new['reading_lineage'] = {'content_version': 'content-one', 'introductions': {'intro-a': ['intro-a'], 'intro-b': ['intro-b']}}
        service = self.service(new)
        state = service.state()
        self.assertEqual(state['status'], 'in_progress'); self.assertIsNone(state['completed_at'])
        self.assertEqual(active(state)['kind'], 'bank_question')
        with closing(service._connect()) as con:
            saved = service._load(con)
        self.assertEqual(saved['content_history'][0]['completed_at'], completed_at)
        self.assertEqual(set(saved['read_introductions']), {'intro-a', 'intro-b'})
        self.solve(); self.assertEqual(service.advance(payload(service))['status'], 'completed')

    def test_atomic_and_bank_questions_share_the_ordered_topic(self):
        from tests.test_unified_learning import composed_topic as mixed_topic
        from graspmemoedu.question_types import demo_answer
        from datetime import datetime, timedelta, timezone
        topic = mixed_topic(); topic['modules'] = []
        topic['introductions'][0].update(example='<p>Hidden example</p>', footnotes=[{'id': 'hidden-note', 'html': '<p>Hidden note</p>'}])
        topic['bank_questions'] = self.topic['bank_questions']
        topic['content_order'] = [topic['content_order'][0], {'kind': 'bank_question', 'id': 'worked-example'}, topic['content_order'][2]]
        clock = [datetime(2026, 10, 29, tzinfo=timezone.utc)]
        service = create_learning_service(topic, self.root, learner_id='learner', clock=lambda: clock[0])
        service.advance(payload(service))
        for _ in range(40):
            state = service.state()
            if state.get('training_mode') != 'atomic_retrieval': break
            if state['phase'] == 'answer':
                self.assertIsNone(state.get('introduction'))
                self.assertNotIn('Hidden note', json.dumps(state))
            if state['phase'] == 'answer':
                q = service.atomic.questions[state['current']['card_id']]
                submission = service.submit(payload(service, answer=demo_answer(q)))
                service.process_submission(submission['submission_id'])
            else:
                clock[0] += timedelta(minutes=3)
                service.advance(payload(service))
        self.assertEqual(active(service.state())['kind'], 'bank_question')
        with self.assertRaises(DomainError): service.advance(payload(service))
        self.solve(); state = service.advance(payload(service))
        self.assertNotEqual(state['status'], 'completed')
        self.assertEqual(active(state)['introduction_id'], 'intro-b')

    def test_duplicate_refs_and_orphan_sections_fail(self):
        bad = deepcopy(self.topic); bad['bank_questions'].append({**bad['bank_questions'][0], 'id': 'duplicate'})
        with self.assertRaises(ValueError): validate_runtime(bad)
        bad = deepcopy(self.topic); bad['introductions'][0]['section_id'] = 'missing'
        with self.assertRaises(ValueError): validate_runtime(bad)

    def test_mechanical_compiler_keeps_reading_fields_and_reference(self):
        document = {'topic_format_version': 2, 'id': 'composition', 'title': '组合', 'topic_kind': 'introduction',
            'sections': self.topic['sections'], 'nodes': [
                {'id': 'intro', 'section_id': 'section-a', 'text': 'Introduction', 'children': [
                    {'id': 'body', 'text': '原正文 $x+1$', 'children': []}],
                 'example': [{'id': 'illustration', 'text': '说明性例子 $y+2$', 'children': []}],
                 'footnotes': [{'id': 'note', 'text': '脚注 $z+3$', 'children': []}]},
                {'id': 'question-ref', 'kind': 'bank_question', 'section_id': 'section-a', 'text': '', 'children': [],
                 'reference': reference(self.bank)}]}
        normalized, _ = normalize_document(document)
        reparsed, _ = parse_source(dump_markdown(normalized))
        self.assertEqual(semantic_payload(normalized), semantic_payload(reparsed))
        source = self.root/'source.json'; source.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        receipt = prepare_topic(source, 'composition', self.root/'package')
        self.assertEqual(receipt['status'], 'approved', receipt.get('error'))
        compiled = json.loads((self.root/'package/topic.json').read_text(encoding='utf-8'))
        self.assertIn('y+2', compiled['introductions'][0]['example'])
        self.assertIn('z+3', compiled['introductions'][0]['footnotes'][0]['html'])
        self.assertNotIn('脚注', compiled['introductions'][0]['html'])
        self.assertEqual(compiled['bank_questions'][0]['reference'], reference(self.bank))
        validate_runtime(compiled)


class TopicCompositionAPITests(unittest.TestCase):
    def test_referenced_bank_keeps_permissions_and_admin_preview_is_ephemeral(self):
        from fastapi.testclient import TestClient
        from graspmemoedu.api import create_app
        from graspmemoedu.config import Settings
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); (self.root/'training-banks').mkdir()
        bank = training_bank(); topic = composed_topic(bank)
        for name, value in [('topic.json', topic), ('training-banks/isolated-bank.json', bank),
            ('catalog.json', {'courses': [{'id': 'course', 'title': 'Course', 'topics': [{'id': topic['id'], 'path': 'topic.json'}]}]}),
            ('subjects.json', {'subjects': [{'id': 'math', 'course_ids': ['course']}]}),
            ('test-materials.json', {'schema': 1, 'banks': [bank['id']]})]:
            (self.root/name).write_text(json.dumps(value), encoding='utf-8')
        self.app = create_app(Settings(data_dir=self.root, public_api_origin='https://training.test',
            allowed_origins=('https://site.test',)), create_learning_service(topic, self.root))
        self.client = TestClient(self.app, base_url='https://training.test'); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'Origin': 'https://site.test'}
        def call(method, path, body=None, status=200):
            response = self.client.request(method, path, headers=self.headers, **({'json': body} if body is not None else {}))
            self.assertEqual(response.status_code, status, response.text)
            return response.json()
        self.call = call
        def account(**kwargs):
            with self.app.state.access_store._transaction() as con:
                created = self.app.state.access_store._insert_account(con, '隔离验收', **kwargs)
            login = call('POST', '/api/access/login', {'invitation_code': created['invitation_code']})
            self.headers['X-Learning-Identity'] = login['identity_token']
            return login
        path = '/api/state?topic_id='+topic['id']
        login = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.call('GET', path, status=403)
        for args in ({'is_admin': True, 'purpose': 'learner'},
                     {'is_advanced_learner': True, 'is_student': True, 'purpose': 'learner'},
                     {'is_student': True, 'purpose': 'test'},
                     {'is_advanced_learner': True, 'purpose': 'test'}):
            account(**args); self.call('GET', path, status=403)
        account(is_advanced_learner=True, is_student=True, purpose='test', topic_ids=[topic['id']])
        self.call('GET', path, status=403)
        account(is_advanced_learner=True, is_student=True, purpose='test')
        self.assertIn('steps', self.call('GET', path))
        admin = account(is_admin=True, purpose='test')
        self.assertTrue(self.call('GET', path)['access']['is_admin'])
        with closing(self.app.state.access_store._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM topics WHERE learner=?', (admin['access']['learner_id'],)).fetchone()[0], 0)
