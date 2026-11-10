"""Exercise an installed native Topic through isolated real application APIs.

The complete section traversal uses an explicitly labelled scheduling fixture.
Only the small real-judge sample is evidence of language judgement.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from fastapi.testclient import TestClient
from graspmemoedu.api import create_app
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.dependencies import DependencyPolicy
from graspmemoedu.judge import ExistingJudge, independent_settings
from graspmemoedu.question_types import demo_answer
from graspmemoedu.service import LearningService
from preview_source_course import main as prepare_preview


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


class Exercise:
    def __init__(self, root, topic, mode):
        self.root, self.topic, self.mode = root, topic, mode
        self.when = datetime.now(timezone.utc)
        self.calls, self.trace = [], []
        self.settings = Settings(data_dir=root / 'data', public_api_origin='https://native.test',
                                 allowed_origins=('https://native.test',))
        self.real_judge = ExistingJudge(self.settings.judge_project)
        catalog = Catalog.load(self.settings)
        self.app = create_app(self.settings, LearningService(catalog.topic('lesson-math'), root / 'data', self.judge),
            atomic_clock=lambda: self.when if self.mode == 'sequence_fixture' else datetime.now(timezone.utc))
        self.client = TestClient(self.app, base_url='https://native.test')
        self.headers = {'Origin': 'https://native.test'}
        self.available_topics = {topic['id']: topic}
        self.prerequisite_runs = []
        self.select_topic(topic)

    def select_topic(self, topic):
        self.topic = topic
        self.scope = '?subject_id=english&topic_id=' + topic['id']
        self.cards = {card['id']: card for card in topic.get('flashcards', [])}
        self.questions = {question['id']: question for module in topic.get('modules', [])
                          for question in [module['example'], *module['practices']]}
        self.point_sections = {point['id']: chunk['introduction_id']
                               for chunk in topic.get('memory_chunks', []) for point in chunk['points']}
        self.chunk_sections = {chunk['id']: chunk['introduction_id'] for chunk in topic.get('memory_chunks', [])}
        self.intro_order = [item['id'] for item in topic['content_order'] if item['kind'] == 'introduction']

    def prerequisite_order(self):
        policy = DependencyPolicy(self.app.state.catalog, self.app.state.access_store, self.app.state.subjects)
        dependencies = policy.dependencies()
        order, seen = [], set()
        def visit(ident, trail):
            assert ident not in trail, 'A prerequisite cycle cannot be bypassed for acceptance'
            assert ident in self.available_topics, 'The compiled preview is missing a real prerequisite: ' + ident
            if ident in seen:
                return
            for parent in policy.required_parents(ident, dependencies):
                visit(parent, trail | {ident})
            seen.add(ident); order.append(ident)
        visit(self.topic['id'], set())
        return order[:-1]

    def prepare_prerequisites(self):
        """Complete the actual closure through APIs in the same test identity.

        This setup is explicitly a scheduling fixture, never real-judge evidence.
        No database completion state or prerequisite edge is edited.
        """
        order = self.prerequisite_order()
        original = self.topic, self.root, self.mode, self.calls, self.trace
        try:
            for ident in order:
                self.select_topic(self.available_topics[ident])
                self.mode, self.calls, self.trace = 'sequence_fixture', [], []
                self.root = (original[1] / 'prerequisite-fixtures' / hashlib.sha256(self.learner_id.encode()).hexdigest()[:12]
                             / hashlib.sha256(ident.encode()).hexdigest()[:8])
                self.root.mkdir(parents=True, exist_ok=False)
                result = self.sequence(login=False)
                self.prerequisite_runs.append({'topic_id': ident, 'learner_id': self.learner_id,
                    'evidence': str(self.root), 'result': result, 'progress_written_through_normal_api': True})
        finally:
            topic, self.root, self.mode, self.calls, self.trace = original
            self.select_topic(topic)
        save(self.root / (self.mode + '-prerequisite-fixture-report.json'), {
            'real_judge': False, 'original_dependencies_retained': True,
            'completion_rows_fabricated': False, 'runs': self.prerequisite_runs})

    def judge(self, question, reference, answer):
        started = datetime.now(timezone.utc).isoformat()
        result = (self.real_judge(question, reference, answer) if self.mode == 'real_judge'
                  else {'status': 'judged', 'correct': True, 'reason': 'Scheduling fixture; semantic correctness not evaluated.'})
        self.calls.append({'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
            'question_sha256': hashlib.sha256(question.encode()).hexdigest(),
            'reference_sha256': hashlib.sha256(reference.encode()).hexdigest(), 'answer': answer, 'result': result})
        save(self.root / (self.mode + '-judge-calls.json'), self.calls)
        return result

    def call(self, method, route, body=None):
        for retry in range(3):
            response = self.client.request(method, '/api/' + route, headers=self.headers,
                                           **({'json': body} if body is not None else {}))
            if response.status_code != 429 or response.json().get('detail', {}).get('code') != 'rate_limited':
                break
            delay = int(response.headers['Retry-After'])
            assert 0 < delay <= 60
            self.trace.append({'kind': 'admission_wait', 'route': route, 'retry_after_seconds': delay})
            # Preserve application admission limits; the learning clock fixture
            # does not bypass the real per-minute request limit.
            for pause in range(0, delay, 10):
                print(self.mode + ': respecting admission Retry-After (' + str(delay - pause) + 's)', flush=True)
                time.sleep(min(10, delay - pause))
        assert response.status_code == 200, (route, response.status_code, response.text)
        return response.json()

    def login(self, *, admin=False):
        store = self.app.state.access_store
        with store._transaction() as con:
            account = store._insert_account(con, 'Isolated native course ' + self.mode,
                purpose='test', is_admin=admin, is_advanced_learner=not admin, is_student=not admin)
        result = self.call('POST', 'access/login', {'invitation_code': account['invitation_code']})
        self.headers['X-Learning-Identity'] = result['identity_token']
        self.learner_id = result['access']['learner_id']
        assert result['access']['purpose'] == 'test'
        return result['access']

    def state(self):
        return self.call('GET', 'state' + self.scope)

    def action(self, route, **fields):
        state = self.state()
        return self.call('POST', route + self.scope, {'request_id': str(uuid.uuid4()),
            'expected_revision': state['revision'], 'attempt_id': state['attempt_id'],
            'step_id': state['active_step_id'], **fields})

    def answer(self, answer, expected):
        before = self.state()
        call_count = len(self.calls)
        result = self.action('submit', answer=answer)
        deadline = time.monotonic() + 180
        while result['status'] in ('pending', 'running') and time.monotonic() < deadline:
            time.sleep(.1)
            result = self.call('GET', 'submissions/' + result['submission_id'] + self.scope)
        assert result['status'] == 'judged', result
        assert result['correct'] is expected, result
        current = result['state'].get('current')
        if current:
            assert current['correct'] is expected
        else:
            step = self.active(result['state'])
            assert step['phase'] == 'feedback'
            assert step['feedback']['correct'] is expected
            assert step['feedback']['submission_id'] == result['submission_id']
        if self.mode == 'real_judge':
            assert len(self.calls) > call_count, 'This sample must reach the real configured judge'
            assert all(call['result']['status'] == 'judged' for call in self.calls[call_count:])
        identity = ({'card_id': before['current']['card_id'], 'point_ids': before['current']['point_ids']}
                    if before.get('current') else {'question_id': self.active(before)['question_id'],
                                                   'module_id': self.active(before)['module_id']})
        item = {'kind': 'submission', **identity, 'submission_id': result['submission_id'],
            'answer': answer, 'expected_correct': expected, 'result': result}
        self.trace.append(item)
        save(self.root / (self.mode + '-trace.json'), self.trace)
        print(self.mode + ': judged ' + (item.get('card_id') or item['question_id']) + ' / ' + str(expected), flush=True)
        return result['state']

    @staticmethod
    def active(state):
        return next(step for step in state['steps'] if step['id'] == state['active_step_id'])

    def saved(self):
        backend = self.app.state.learning_services[(self.learner_id, self.topic['id'])]
        with closing(backend._connect()) as con:
            assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            return backend._load(con)

    def sequence(self, *, login=True):
        if login:
            self.login()
            self.prepare_prerequisites()
        if self.questions and not self.cards:
            return self.paradigm_sequence()
        visited, shown = [], set()
        for index in range(400):
            state, saved = self.state(), self.saved()
            read = saved['read_introductions']
            assert read == self.intro_order[:len(read)]
            active = next((step for step in state.get('steps', []) if step['id'] == state['active_step_id']), {})
            if saved['display'] == 'paradigm' and active.get('kind') == 'introduction':
                ident = active['introduction_id']
                if ident not in visited:
                    visited.append(ident)
                    assert visited == self.intro_order[:len(visited)]
            catch_up = bool(saved.get('atomic_catch_up'))
            if catch_up:
                assert read == self.intro_order
            owners = set()
            if state.get('chunk'):
                owners.add(self.chunk_sections[state['chunk']['id']])
            if state.get('current'):
                owners.update(self.point_sections[p] for p in state['current']['point_ids'])
                if state['phase'] == 'answer':
                    shown.add(state['current']['card_id'])
            assert owners <= set(read), 'Cards and chunks must follow their owning Introduction'
            if not catch_up and owners:
                assert owners == {self.intro_order[saved['section_index']]}
            self.trace.append({'kind': 'state', 'index': index, 'state': state,
                'read_introductions': read, 'section_index': saved['section_index'], 'catch_up': catch_up})
            save(self.root / (self.mode + '-trace.json'), self.trace)
            if state['status'] == 'completed':
                points = saved['atomic']['points']
                assert set(points) == set(self.point_sections)
                assert all(point['mastered'] for point in points.values())
                assert visited == self.intro_order
                save(self.root / 'sequence-fixture-final-state.json', saved)
                return {'status': 'passed', 'real_judge': False, 'clock_advanced_by_fixture': True,
                    'introduction_order': visited, 'points_mastered': len(points), 'cards_seen': sorted(shown),
                    'normal_learning_sequence': True, 'administrator_previews_used': False,
                    'real_elapsed_learning_time_verified': False}
            if state.get('phase') == 'answer':
                self.answer(demo_answer(self.cards[state['current']['card_id']]), True)
            else:
                if state.get('phase') == 'waiting':
                    self.when = datetime.fromisoformat(state['next_available_at']) + timedelta(seconds=1)
                elif state.get('phase') == 'feedback':
                    self.when += timedelta(seconds=180)
                self.action('continue')
        raise AssertionError('Learning sequence did not reach completion')

    def paradigm_sequence(self):
        """Exercise the existing unmastered path; the judge remains a fixture."""
        owners, assigned, pending, latest = {}, [], [], None
        for entry in self.topic['content_order']:
            if entry['kind'] == 'introduction':
                pending.append(entry['id']); latest = entry['id']
            else:
                assert latest is not None
                owners[entry['id']] = latest
                assigned.extend(pending); pending = []
        visited, answered = [], []
        for index in range(max(100, len(owners) * 60)):
            state, saved = self.state(), self.saved()
            step = self.active(state)
            if step['kind'] == 'introduction' and step['introduction_id'] not in visited:
                visited.append(step['introduction_id'])
                assert visited == assigned[:len(visited)]
            if step['kind'] == 'practice' or step['kind'] == 'example' and step['phase'] == 'reading':
                assert owners[step['module_id']] in saved['read_introductions']
            self.trace.append({'kind': 'state', 'index': index, 'state': state,
                               'read_introductions': saved['read_introductions']})
            save(self.root / (self.mode + '-trace.json'), self.trace)
            if state['status'] == 'completed':
                assert all(module['status'] == 'completed' for module in state['modules'])
                assert visited == assigned
                completed = saved['attempts']
                assert {attempt['module_id'] for attempt in completed} == set(owners)
                assert all(attempt['practice_error_count'] == 0
                           and attempt['practice_answered_count'] == attempt['practice_target_count']
                           == attempt['practice_policy']['targets'][0] for attempt in completed)
                assert len(answered) == sum(attempt['practice_answered_count'] for attempt in completed)
                save(self.root / 'sequence-fixture-final-state.json', saved)
                return {'status': 'passed', 'real_judge': False, 'training_kind': 'paradigm',
                    'normal_learning_sequence': True, 'administrator_previews_used': False,
                    'module_ids': list(owners), 'introduction_order': visited,
                    'unassigned_guidance_ids': pending, 'practice_ids_answered': answered,
                    'practice_counts_match_saved_policy': True,
                    'all_assigned_guidance_visited': True, 'real_elapsed_learning_time_verified': False}
            if step['phase'] == 'choice':
                self.action('choose', choice='unmastered')
            elif step['phase'] == 'answer':
                assert step['kind'] == 'practice'
                self.answer(demo_answer(self.questions[step['question_id']]), True)
                answered.append(step['question_id'])
            else:
                self.action('continue')
        raise AssertionError('Paradigm sequence did not reach completion')

    def real_samples(self):
        if self.questions and not self.cards:
            return self.paradigm_real_samples()
        independent_settings()  # Validate dedicated configuration without printing it.
        self.login()
        self.prepare_prerequisites()
        samples = []
        for index in range(2):
            for _ in range(30):
                state = self.state()
                if state.get('phase') == 'answer':
                    break
                assert state.get('phase') != 'waiting', 'Real sampling must not alter the clock'
                self.action('continue')
            card = self.cards[state['current']['card_id']]
            assert len(card['point_ids']) == 1
            answer = demo_answer(card) if index == 0 else 'I do not know.'
            self.answer(answer, index == 0)
            samples.append({'card_id': card['id'], 'point_ids': card['point_ids'],
                'expected_correct': index == 0, 'route': 'normal_initial_learning'})
        student_final = self.saved()
        save(self.root / 'real-judge-student-final-state.json', student_final)
        self.call('POST', 'access/logout', {})
        self.login(admin=True)
        bundle = next(card for card in self.cards.values() if len(card['point_ids']) > 1
                      and card.get('interaction', {}).get('grading') == 'semantic')
        opened = self.call('POST', 'materials/open' + self.scope,
            {'kind': 'atomic_card', 'id': bundle['id'], 'request_id': str(uuid.uuid4())})
        assert opened['current']['card_id'] == bundle['id']
        self.answer(demo_answer(bundle), True)
        samples.append({'card_id': bundle['id'], 'point_ids': bundle['point_ids'], 'expected_correct': True,
                        'route': 'administrator_single_card_preview'})
        self.call('POST', 'access/logout', {})
        return {'status': 'passed', 'real_judge': True, 'samples': samples, 'judge_calls': len(self.calls),
            'clock_advanced_by_fixture': False, 'complete_learning_verified': False,
            'bundle_preview_is_not_normal_learning_sequence': True}

    def paradigm_real_samples(self):
        """One correct and one incorrect real answer in separate test identities."""
        independent_settings()
        samples = []
        for expected in (True, False):
            self.login()
            self.prepare_prerequisites()
            before = self.state(); step = self.active(before)
            assert step['kind'] == 'example' and step['phase'] == 'choice'
            question = self.questions[step['question_id']]
            assert question.get('interaction', {}).get('grading', 'semantic') == 'semantic'
            self.action('choose', choice='mastered')
            self.answer(demo_answer(question) if expected else 'I do not know.', expected)
            advanced = self.action('continue')
            if not expected:
                assert self.active(advanced)['kind'] == 'introduction'
            samples.append({'question_id': question['id'], 'expected_correct': expected,
                'route': 'normal_example_mastery_choice', 'wrong_answer_returned_to_guidance': not expected,
                'separate_test_identity': True})
            save(self.root / ('real-judge-paradigm-' + ('correct' if expected else 'incorrect') + '-final-state.json'), self.saved())
            self.call('POST', 'access/logout', {})
        return {'status': 'passed', 'real_judge': True, 'training_kind': 'paradigm',
            'samples': samples, 'judge_calls': len(self.calls), 'clock_advanced_by_fixture': False,
            'complete_learning_verified': False, 'administrator_previews_used': False}


def main(args):
    prepare_preview(SimpleNamespace(output=args.output, bundle=args.bundle, prepare=True, subject='english'))
    metadata = json.loads((args.output / 'preview-catalog.json').read_text(encoding='utf-8'))
    topics = json.loads((args.output / 'preview-native.json').read_text(encoding='utf-8'))['topics']
    target = getattr(args, 'topic', None)
    assert target or len(topics) == 1, 'Select one Topic; its actual prerequisite closure remains installed'
    chosen = next((topic for topic in topics if topic['id'] == target), None) if target else topics[0]
    assert chosen is not None, 'Unknown selected Topic'
    report = {'status': 'running', 'production_opened': False, 'source_binding': metadata['source_binding'],
              'sampled_topic_id': chosen['id'], 'prerequisite_progress_is_scheduling_fixture': True}
    try:
        for mode in ('sequence_fixture', 'real_judge'):
            exercise = Exercise(args.output, chosen, mode)
            exercise.available_topics = {topic['id']: topic for topic in topics}
            with exercise.client:
                report[mode] = exercise.sequence() if mode == 'sequence_fixture' else exercise.real_samples()
            save(args.output / 'learning-validation.json', report)
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        save(args.output / 'learning-validation.json', report)
        raise
    save(args.output / 'learning-validation.json', report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--topic', help='One compiled Topic to sample while retaining all actual prerequisites')
    main(parser.parse_args())
