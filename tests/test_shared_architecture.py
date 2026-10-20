"""Cross-subject behavior contracts, not copies of subject-specific implementations."""
from contextlib import closing
import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
import uuid

from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from graspmemoedu.review_cards import attach_training_variant, load_paper_types, point_id
from graspmemoedu.subject_registry import DEFAULTS, SUBJECT_IDS
from graspmemoedu.test_materials import validate as validate_test_policy
from graspmemoedu.training import TrainingService
from tests.fixtures import DeterministicJudge, temporary_test_directory
from tests.subject_matrix_fixture import write_matrix


class SharedArchitectureTests(unittest.TestCase):
    def setUp(self):
        temporary = temporary_test_directory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.topics = write_matrix(self.root)
        self.when = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.settings = Settings(data_dir=self.root, public_api_origin='https://matrix.test')
        self.app = create_app(self.settings,
            LearningService(self.topics['lesson-math'], self.root, DeterministicJudge()),
            atomic_clock=lambda: self.when)
        self.client = TestClient(self.app, base_url='https://matrix.test')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'Origin': 'https://matrix.test'}
        login = self.call('POST', '/api/access/guest', {})
        self.headers['X-Learning-Identity'] = login['identity_token']
        self.learner = login['access']['learner_id']

    def call(self, method, path, body=None, status=200):
        response = self.client.request(method, path, headers=self.headers,
                                       **({'json': body} if body is not None else {}))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_same_pages_and_learning_runtimes_for_every_subject(self):
        shapes, runtimes = {}, {}
        for subject, *_ in DEFAULTS:
            with self.subTest(subject=subject):
                course = 'course-' + subject
                catalog = self.call('GET', '/api/catalog?subject_id=' + subject)
                self.assertEqual([c['id'] for c in catalog['courses']], [course])
                self.call('POST', '/api/catalog/select?subject_id=' + subject, {'course_id': course})
                for endpoint in ('dashboard', 'training/catalog', 'review-papers/inbox', 'guide'):
                    data = self.call('GET', '/api/' + endpoint + '?subject_id=' + subject)
                    self.assertEqual(set(data), shapes.setdefault(endpoint, set(data)))
                reader = self.call('GET', '/api/training/group?reader=1&subject_id=' + subject +
                                   '&bank_id=bank-' + subject + '&group_id=group-a')
                self.assertEqual(set(reader['reader_states']), {'essay', 'exact', 'boolean', 'choice', 'blank'})
                self.assertTrue(all(value['preview'] and value['explanation_html'] is None
                                    for value in reader['reader_states'].values()))
                for kind in ('lesson', 'atomic', 'composed', 'academy', 'hyperskill'):
                    tid = kind + '-' + subject
                    state = self.call('GET', '/api/state?subject_id=' + subject + '&topic_id=' + tid)
                    service = self.app.state.learning_services[self.learner, tid]
                    self.assertEqual(type(service), runtimes.setdefault(kind, type(service)))
                    shape = (set(state), state.get('phase'), state.get('training_mode'))
                    self.assertEqual(shape, shapes.setdefault(kind, shape))
                wrong = 'physics' if subject == 'math' else 'math'
                self.call('GET', '/api/state?subject_id=' + wrong + '&topic_id=lesson-' + subject, status=403)
                self.call('GET', '/api/training/bank?subject_id=' + wrong + '&bank_id=bank-' + subject, status=403)
                self.assertEqual(self.client.get('/subject-guide-assets/' + subject + '/pixel.png',
                                               headers=self.headers).status_code, 200)
                self.assertEqual(self.client.get('/subject-guide-assets/' + subject + '/pixel.png',
                                               headers={'Origin': self.headers['Origin']}).status_code, 401)

    def test_linked_cards_share_paper_selection_and_point_scoring_in_every_subject(self):
        import time
        from graspmemoedu.question_types import demo_answer
        for subject in SUBJECT_IDS:
            with self.subTest(subject=subject):
                tid = 'atomic-' + subject
                self.call('GET', '/api/state?subject_id=' + subject + '&topic_id=' + tid)
                backend = self.app.state.learning_services[self.learner, tid]
                # Prime only the isolated fixture's learned/due state.
                with backend._transaction() as con:
                    state = backend._load(con)
                    state.update(completed_at=self.when.isoformat(), status='completed', phase='completed')
                    for memory in state['points'].values():
                        memory.update(mastered=True, first_passed_at=self.when.isoformat(), due_at=self.when.isoformat())
                    backend._save(con, state)
                self.app.state.review_paper_types = [{'id': 'linked-' + subject, 'title': 'Linked cards',
                    'subject': subject, 'enabled': True, 'requirements': {'fill_blank': 1, 'text': 1}}]
                inbox = self.call('GET', '/api/review-papers/inbox?subject_id=' + subject)
                candidate = next(c for c in inbox['items'] if c['kind'] == 'paper')
                self.assertEqual((candidate['count'], candidate['point_count']), (2, 3))
                paper = self.call('POST', '/api/review-papers/open?subject_id=' + subject,
                    {'candidate_id': candidate['id'], 'request_id': str(uuid.uuid4())})
                answers = []
                for item in paper['items']:
                    question = backend.questions[item['content_context']['question_id']]
                    answer = ('{"a":"wrong","b":"wrong"}' if len(item['point_ids']) > 1 else demo_answer(question))
                    answers.append({'index': item['index'], 'answer': answer})
                paper = self.call('POST', '/api/review-papers/draft',
                    {'paper_id': paper['id'], 'expected_revision': paper['revision'], 'answers': answers})
                self.call('POST', '/api/review-papers/submit', {'paper_id': paper['id'],
                    'expected_revision': paper['revision'], 'request_id': str(uuid.uuid4())})
                for _ in range(100):
                    paper = self.call('GET', '/api/review-papers/state?paper_id=' + paper['id'])
                    if paper['job_status'] not in ('pending', 'running'):
                        break
                    time.sleep(.01)
                self.assertEqual(paper['phase'], 'done', paper)
                for item in paper['items']:
                    expected = 1 if len(item['point_ids']) > 1 else 3
                    self.assertEqual(set(item['result']['ratings']), set(item['point_ids']))
                    self.assertEqual(set(item['result']['ratings'].values()), {expected})

    def test_bank_scoring_review_selection_and_fsrs_are_subject_independent(self):
        states, schedules = [], []
        for subject, *_ in DEFAULTS:
            backend = TrainingService(self.app.state.training_catalog, self.root, self.learner,
                                      DeterministicJudge(), clock=lambda: self.when)
            aid = backend.open('bank-' + subject, 'group-a', 'exact', str(uuid.uuid4()))
            sid = backend.submit({'attempt_id': aid, 'request_id': str(uuid.uuid4()), 'answer': 'wrong'})
            backend.process_submission(sid)
            state = backend.state(aid)
            self.assertEqual(state['result']['rating'], 1)
            states.append(state['result'])
            with closing(backend._connect()) as con, con:
                row = con.execute('SELECT card_json,due_at FROM training_memory WHERE learner=? AND bank=?',
                                  (self.learner, 'bank-' + subject)).fetchone()
                card = json.loads(row['card_json'])
                schedules.append((card['state'], card['stability'], card['difficulty'], row['due_at']))
                bank = backend.catalog.bank('bank-' + subject)
                pid = point_id('training', bank['id'], bank['version'], 'exact')
                variant = dict(backend.catalog.question(bank['id'], 'group-a', 'exact')[2])
                variant.update(id='unseen-variant', title='Unseen')
                attach_training_variant(con, pid, variant, self.when.isoformat())
        self.assertTrue(all(s == schedules[0] for s in schedules))
        self.when += timedelta(days=2)
        for subject, *_ in DEFAULTS:
            with self.subTest(subject=subject):
                inbox = self.call('GET', '/api/review-papers/inbox?subject_id=' + subject)
                self.assertEqual(inbox['due_count'], 1)
                item = next(c for c in inbox['items'] if c['kind'] == 'single')
                opened = self.call('POST', '/api/review-papers/open?subject_id=' + subject,
                                   {'candidate_id': item['id'], 'request_id': str(uuid.uuid4())})
                self.assertEqual(opened['items'][0]['title'], 'Unseen')
                self.call('POST', '/api/review-papers/cancel', {'paper_id': opened['id']})

    def test_source_adapter_registers_actual_subject_in_shared_memory_index(self):
        for subject, *_ in DEFAULTS:
            tid = 'academy-' + subject
            self.call('GET', '/api/state?subject_id=' + subject + '&topic_id=' + tid)
            backend = self.app.state.learning_services[self.learner, tid]
            state = backend.state()
            payload = lambda s: dict(request_id=str(uuid.uuid4()), expected_revision=s['revision'],
                attempt_id=s['attempt_id'], step_id=s['active_step_id'])
            backend.choose({**payload(state), 'choice': 'learn'})
            run = backend.launch(payload(backend.state()))
            backend.opened(run['round_id'], run['step_id'])
            backend.check_event(run['round_id'], run['step_id'], {'id': str(uuid.uuid4()), 'sequence': 1,
                'category': 'passed', 'code_hash': 'a' * 64, 'occurred_at': self.when.isoformat()})
            with closing(backend._connect()) as con:
                row = con.execute('SELECT subject FROM review_points WHERE container=?', (tid,)).fetchone()
                self.assertEqual(row['subject'], subject)

    def test_every_registered_subject_supports_paper_configuration_and_test_guides(self):
        validate_test_policy({'schema': 1, 'guides': list(SUBJECT_IDS)})
        source = self.root / 'papers.md'
        source.write_text('\n\n'.join(
            f'## {s}\n- ID: {s}-paper\n- 名称: Shared\n- 限时（分钟）: 10\n- 学科: {s}\n- 启用: 是\n- 问答: 1'
            for s, *_ in DEFAULTS), encoding='utf-8')
        self.assertEqual({p['subject'] for p in load_paper_types(source)}, SUBJECT_IDS)
        with self.assertRaises(ValueError):
            validate_test_policy({'schema': 1, 'guides': ['unknown']})

    def test_roles_and_test_material_visibility_are_identical_for_all_subjects(self):
        policy = {'schema': 1, 'courses': ['course-' + s for s in SUBJECT_IDS],
                  'banks': ['bank-' + s for s in SUBJECT_IDS], 'guides': sorted(SUBJECT_IDS)}
        (self.root / 'test-materials.json').write_text(json.dumps(policy), encoding='utf-8')
        self.app = create_app(self.settings,
            LearningService(self.topics['lesson-math'], self.root, DeterministicJudge()))
        self.client = TestClient(self.app, base_url='https://matrix.test')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        for attributes, allowed in (
            (None, False),
            ({'is_admin': True, 'purpose': 'learner'}, False),
            ({'is_student': True, 'purpose': 'test'}, False),
            ({'is_advanced_learner': True, 'purpose': 'test'}, False),
            ({'is_student': True, 'is_advanced_learner': True, 'purpose': 'test'}, True),
            ({'is_admin': True, 'purpose': 'test'}, True),
        ):
            if attributes is None:
                login = self.call('POST', '/api/access/guest', {})
            else:
                with self.app.state.access_store._transaction() as con:
                    account = self.app.state.access_store._insert_account(con, 'Matrix role', **attributes)
                login = self.call('POST', '/api/access/login', {'invitation_code': account['invitation_code']})
            self.headers['X-Learning-Identity'] = login['identity_token']
            for subject, *_ in DEFAULTS:
                with self.subTest(subject=subject, role=attributes):
                    self.assertEqual(len(self.call('GET', '/api/catalog?subject_id=' + subject)['courses']), int(allowed))
                    self.assertEqual(len(self.call('GET', '/api/training/catalog?subject_id=' + subject)['banks']), int(allowed))
                    self.call('GET', '/api/state?topic_id=lesson-' + subject + '&subject_id=' + subject,
                              status=200 if allowed else 403)
                    self.call('GET', '/api/training/bank?bank_id=bank-' + subject + '&subject_id=' + subject,
                              status=200 if allowed else 403)
                    self.call('GET', '/api/guide?subject_id=' + subject, status=200 if allowed else 404)
                    self.assertEqual(self.client.get('/subject-guide-assets/' + subject + '/pixel.png',
                        headers=self.headers).status_code, 200 if allowed else 404)

    def test_subject_configuration_cannot_override_common_mechanisms(self):
        from graspmemoedu.subjects import Subjects
        from graspmemoedu.service import DomainError
        for subject, *_ in DEFAULTS:
            for key in ('renderer', 'rating', 'scheduler', 'features', 'permissions'):
                with self.subTest(subject=subject, key=key), self.assertRaises(DomainError):
                    Subjects(self.settings, self.app.state.catalog, {'subjects': [{'id': subject, key: 'different'}]})


class SharedArchitectureBoundaryTests(unittest.TestCase):
    def test_business_code_does_not_dispatch_by_subject_name(self):
        root = Path(__file__).resolve().parents[1]
        # Content production chooses its destination; it cannot change runtime rules.
        producers = {'academy_import.py', 'mathacademy_import.py', 'variant_worker.py'}
        # Only old unscoped data/URLs keep their historical default owner.
        compatibility = {'dashboard.py': {'_selection'}, 'guide.py': {'authorize_test_guide'},
                         'site_backup.py': {'_validate_payload', 'backup_site'}}
        failures = []
        for path in (root / 'graspmemoedu').glob('*.py'):
            if path.name in producers:
                continue
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                    ids = {v.value for v in node.elts if isinstance(v, ast.Constant) and isinstance(v.value, str)}
                    if len(ids & SUBJECT_IDS) > 1:
                        failures.append(f'{path.name}:{node.lineno}: duplicate subject registry')
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                owner = parents.get(node)
                while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owner = parents.get(owner)
                literal_subjects = {n.value for n in ast.walk(node)
                                    if isinstance(n, ast.Constant) and isinstance(n.value, str)} & SUBJECT_IDS
                if literal_subjects and 'subject' in ast.unparse(node).lower():
                    if getattr(owner, 'name', '') not in compatibility.get(path.name, set()):
                        failures.append(f'{path.name}:{node.lineno}: subject selects business behavior')
        self.assertEqual(failures, [])
