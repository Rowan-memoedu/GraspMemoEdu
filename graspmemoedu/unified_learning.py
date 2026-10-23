"""Section ordering over the existing transactional Example and point engines.

Example state keeps its original shape. Point state is nested under ``atomic``
in the same learner/Topic row. There is one visible page and one row revision;
the two scheduling states, pause states and question identities stay separate.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import uuid

from .atomic import AtomicLearningService
from .atomic_content import point_chunks, learning_presentation
from .fsrs_engine import FSRSEngine
from .ordered_learning import OrderedLearningService
from .service import DomainError, LearningService, now
from .topic_structure import sections


class PointState(dict):
    def __init__(self, root):
        super().__init__(root['atomic'])
        self.root = root
        self['revision'] = root['revision']


class TopicPoints(AtomicLearningService):
    """Storage adapter; grading and FSRS continue to use AtomicLearningService."""

    def __init__(self, owner):
        self.owner = owner
        self.questions = {card['id']: card for card in owner.course['flashcards']}
        self.review_backend = None

    def __getattr__(self, name):
        return getattr(self.owner, name)

    def _load(self, con):
        return PointState(self.owner._load(con))

    def _save(self, con, state):
        if state.get('preview_single_card'):
            state.root['display'] = 'atomic'
            card = state.get('active')
            if card:
                state.root['section_index'] = next(i for i, s in enumerate(self.owner.sections)
                                                   if card['point_ids'][0] in s['point_ids'])
        state.root['atomic'] = dict(state)
        self.owner._save(con, state.root)
        state['revision'] = state.root['revision']

    def _public(self, state):
        state.root['atomic'] = dict(state)
        return self.owner._public(state.root)

    def _eligible(self, state):
        candidates, wakeup = super()._eligible(state)
        scope = state.get('scope_introduction')
        if scope:
            candidates &= set(self.owner.section_by_id[scope]['point_ids'])
            # The waiting page must not wake for another Introduction.
            scoped = PointState(state.root)
            scoped.update(state)
            scoped['points'] = {key: memory for key, memory in state['points'].items()
                                if key in self.owner.section_by_id[scope]['point_ids']}
            _, wakeup = AtomicLearningService._eligible(self, scoped)
        return candidates, wakeup

    def _guard_atomic(self, state, payload):
        if state.root.get('display') != 'atomic':
            raise DomainError('locked_step', '只能操作当前活动步骤。', 403)
        super()._guard_atomic(state, payload)


class UnifiedLearningService(OrderedLearningService):
    def __init__(self, course, data_dir, judge=None, learner_id='local', *, clock=None, engine=None, runtime=None):
        self.course = deepcopy(course)
        self.sections = sections(course)
        self.section_by_id = {section['id']: section for section in self.sections}
        self.module_sections = {module['id']: section['id'] for section in self.sections for module in section['modules']}
        if self.course.get('topic_format_version') != 2:
            self.course.update(
                topic_format_version=2, topic_kind='lesson', content_version=course['version'],
                introductions=[s['introduction'] for s in self.sections],
                content_order=[entry for s in self.sections for entry in [
                    {'kind': 'introduction', 'id': s['id']},
                    *[{'kind': 'example', 'id': m['id']} for m in s['modules']]]])
        self.course.setdefault('modules', [])
        self.course.setdefault('introduction', self.sections[0]['introduction'])
        for intro in self.course['introductions']:
            intro.setdefault('title', 'Introduction')
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.engine = engine or FSRSEngine()
        self.chunks = {c['id']: c for c in course['memory_chunks']}
        self.point_chunks = point_chunks(course)
        self.atomic = TopicPoints(self)
        # Do not run the legacy v2 migration from mandatory guidance to Example
        # choice. Guidance is deliberately first in a composed Topic.
        LearningService.__init__(self, self.course, data_dir, judge, learner_id, runtime=runtime)

    def _new_attempt(self, state, index):
        if 'atomic' not in state:
            points = {}
            AtomicLearningService._new_attempt(self.atomic, points, 0)
            state.update(atomic=points, display='paradigm', section_index=0,
                         topic_kind='lesson', read_introductions=[], sections_finished=[],
                         content_version=self.course['content_version'], reading_status='in_progress',
                         read_content_version=None, reread_pending=False)
            self._start_section(state, 0)
            return
        module = self.course['modules'][index]
        section_id = self.module_sections[module['id']]
        state['section_index'] = next(i for i, s in enumerate(self.sections) if s['id'] == section_id)
        state['order_index'] = next(i for i, entry in enumerate(self.course['content_order'])
                                   if entry == {'kind': 'example', 'id': module['id']})
        state['display'] = 'paradigm'
        LearningService._new_attempt(self, state, index)

    def _start_section(self, state, index):
        section = self.sections[index]
        state.update(section_index=index, display='paradigm')
        state['order_index'] = next(i for i, item in enumerate(self.course['content_order'])
                                   if item == {'kind': 'introduction', 'id': section['id']})
        attempt = {'id': str(uuid.uuid4()), 'kind': 'introduction', 'module_id': section['id'],
                   'module_index': -1, 'status': 'in_progress', 'mastery': None, 'learning_path': 'reading',
                   'practice_error_count': 0, 'practice_answered_count': 0, 'practice_target_count': 0,
                   'example_result': None, 'created_at': now(), 'completed_at': None,
                   'paused_at': None, 'pause_reason': None}
        state['attempts'].append(attempt)
        state.update(attempt_id=attempt['id'], module_index=-1)
        self._add_step(state, kind='introduction')['introduction_id'] = section['id']
        state['atomic'].update(active=None, phase='idle', scope_introduction=section['id'])

    def _current_section(self, state):
        return self.sections[state['section_index']]

    def _atomic_next(self, state):
        """Select without advancing or consuming any normal page."""
        points = PointState(state)
        if points.get('preview_single_card'):
            AtomicLearningService._next(self.atomic, points)
            state.update(atomic=dict(points), display='atomic')
            return True
        catch_up = state.get('atomic_catch_up', False)
        points['scope_introduction'] = None if catch_up else self._current_section(state)['id']
        if all(memory['mastered'] for memory in points['points'].values()):
            points['completed_at'] = points['completed_at'] or self.atomic._time().isoformat()
        points['started_at'] = points['started_at'] or self.atomic._time().isoformat()
        # A future retry (or chunk spacing) must not hold later content hostage.
        # Check due review cards first, then available initial learning cards.
        for mode in (('learn',) if catch_up else ('review', 'learn')):
            points['mode'] = mode
            AtomicLearningService._next(self.atomic, points)
            if points['phase'] in ('chunk', 'answer'):
                break
        state['atomic'] = dict(points)
        if points['phase'] == 'completed' or points['phase'] == 'waiting' and not catch_up:
            points.update(active=None, phase='idle')
            state['atomic'] = dict(points)
            state['display'] = 'paradigm'
            return False
        if catch_up and points['phase'] in ('chunk', 'answer'):
            point_id = (self.chunks[points['pending_chunk']]['points'][0]['id'] if points['phase'] == 'chunk'
                        else points['active']['point_ids'][0])
            state['section_index'] = next(i for i, section in enumerate(self.sections)
                                          if point_id in section['point_ids'])
        state['display'] = 'atomic'
        return True

    def _next_module(self, state):
        section = self._current_section(state)
        completed = {a['module_id'] for a in state['attempts'] if a['status'] == 'completed'}
        skipped = state.get('skipped_modules', {})
        pending = next((m for m in section['modules'] if m['id'] not in completed and m['id'] not in skipped), None)
        if pending:
            self._new_attempt(state, next(i for i, m in enumerate(self.course['modules']) if m['id'] == pending['id']))
            return
        if section['id'] not in state['sections_finished']:
            state['sections_finished'].append(section['id'])
        following = next((i for i in range(state['section_index'] + 1, len(self.sections))
                          if self.sections[i]['id'] not in state['sections_finished']), None)
        if following is not None:
            self._start_section(state, following)
            return
        released = next((m for m in self.course['modules'] if m['id'] not in completed
                         and skipped.get(m['id'], {}).get('released')), None)
        if released:
            self._new_attempt(state, next(i for i, m in enumerate(self.course['modules']) if m['id'] == released['id']))
            return
        # Once all later content has been visited, resume unfinished initial
        # learning across sections. Do not discard it or mark it mastered.
        if not all(memory['mastered'] for memory in state['atomic']['points'].values()):
            state['atomic_catch_up'] = True
            if self._atomic_next(state):
                return
        state.pop('atomic_catch_up', None)
        state.setdefault('initial_completed_at', now())
        state.update(status='completed', reading_status='completed', display='paradigm',
                     read_content_version=self.course['content_version'])
        state.setdefault('completed_at', now())
        self._add_step(state, kind='completion', phase='completed')

    def _advance_step(self, con, state):
        step = self._step(state)
        if step['kind'] == 'introduction':
            step['phase'] = 'completed'
            self._attempt(state).update(status='completed', completed_at=now())
            ident = step['introduction_id']
            if ident not in state['read_introductions']:
                state['read_introductions'].append(ident)
            state['introduction_read'] = len(state['read_introductions']) == len(self.sections)
            state.setdefault('started_at', now())
            self._event(con, state, 'introduction_read', introduction_id=ident)
            if not self._atomic_next(state):
                self._next_module(state)
        else:
            LearningService._advance_step(self, con, state)

    def advance(self, payload):
        with self._transaction() as con:
            state = self._load(con)
            replay, fingerprint = self._request(con, 'continue', payload)
            if not replay:
                if state['display'] == 'atomic':
                    points = PointState(state)
                    self.atomic._guard_atomic(points, payload)
                    if points['phase'] not in ('chunk', 'feedback', 'waiting'):
                        raise DomainError('invalid_step', '当前步骤还不能继续。')
                    if points['phase'] == 'chunk':
                        points['seen_chunks'].append(points['pending_chunk'])
                    state['atomic'] = dict(points)
                    if not self._atomic_next(state):
                        if state['status'] == 'paused':
                            state.pop('deferred_advance', None)
                        elif state.pop('deferred_advance', False):
                            self._advance_step(con, state)
                        else:
                            self._next_module(state)
                else:
                    self._guard(state, payload)
                    step = self._step(state)
                    if step['phase'] in ('choice', 'answer'):
                        raise DomainError('answer_required', '请先完成当前步骤。')
                    if step['kind'] != 'introduction' and self._atomic_next(state):
                        state['deferred_advance'] = True
                    else:
                        self._advance_step(con, state)
                self._record_action(con, 'continue', payload, fingerprint)
                self._save(con, state)
        self.flush_logs()
        return self._public(state)

    def _guard(self, state, payload, **options):
        if state.get('display') == 'atomic':
            raise DomainError('locked_step', '请先完成当前原子卡。', 403)
        super()._guard(state, payload, **options)

    def _dispatch(self, method, payload):
        with closing(self._connect()) as con:
            state = self._load(con)
            atomic = state['display'] == 'atomic'
            # Idempotent answer retries must return the original verdict even
            # after the visible page has moved to the other training component.
            if method == 'submit':
                row = con.execute('SELECT question FROM submissions WHERE id=? AND learner=? AND topic=?',
                                  (payload.get('request_id'), *self.key)).fetchone()
                if row:
                    atomic = row['question'] in self.atomic.questions
        target = self.atomic if atomic else super()
        return getattr(target, method)(payload)

    def choose(self, payload):
        return self._dispatch('choose', payload)

    def submit(self, payload):
        return self._dispatch('submit', payload)

    def reveal(self, payload):
        return self._dispatch('reveal', payload)

    def _reset_identity(self, state):
        current = state['atomic'] if state['display'] == 'atomic' else state
        return current['attempt_id'], current['active_step_id']

    def preview_card(self, card_id, request_id):
        return self.atomic.preview_card(card_id, request_id)

    def _submission_public(self, row, state):
        if row['question'] in self.atomic.questions:
            return self.atomic._submission_public(row, PointState(state))
        return super()._submission_public(row, state)

    def _submission_target(self, submission_id):
        with closing(self._connect()) as con:
            row = con.execute('SELECT question FROM submissions WHERE id=? AND learner=? AND topic=?',
                              (submission_id, *self.key)).fetchone()
            return self.atomic if row and row['question'] in self.atomic.questions else super()

    def instant_submission(self, submission_id):
        return self._submission_target(submission_id).instant_submission(submission_id)

    def process_submission(self, submission_id):
        return self._submission_target(submission_id).process_submission(submission_id)

    def _intro_open(self, state, ident):
        section = self._current_section(state)
        if ident != section['id']:
            return ident in state['read_introductions']
        if state['display'] == 'atomic':
            return state['atomic']['active'] is None
        return True

    def _point_history(self, state, records=None):
        if records is None:
            records = AtomicLearningService._public(self.atomic, PointState(state))['history']
        result = []
        for record in records:
            if not record['viewable']:
                continue
            section_id = next(s['id'] for s in self.sections if record['point_ids'][0] in s['point_ids'])
            result.append({**record, 'kind': 'atomic_card', 'question_id': record['card_id'],
                           'introduction_id': section_id, 'module_id': None, 'attempt_id': None,
                           'content_version': record.get('content_version', self.course['version']), 'visited': True, 'unlocked': True,
                           'active': False, 'current': record['id'] == state['current_step_id'],
                           'visible': True, 'actions': [], 'feedback': {
                               'correct': record['correct'], 'reason': record['reason']}})
        return result

    def _step(self, state, step_id=None):
        if step_id and not any(s['id'] == step_id for s in state['steps']):
            record = next((s for s in self._point_history(state) if s['id'] == step_id), None)
            if record:
                return record
        return super()._step(state, step_id)

    def select(self, payload):
        with closing(self._connect()) as con:
            state = self._load(con)
            step = self._step(state, payload.get('step_id'))
            if step['kind'] == 'introduction' and not self._intro_open(state, step['introduction_id']):
                raise DomainError('locked_step', '当前原子训练期间不能回看本节指导。', 403)
        return super().select(payload)

    def _public(self, state):
        result = OrderedLearningService._public(self, state)
        section = self._current_section(state)
        for step in result['steps']:
            ident = step.get('introduction_id')
            if step['kind'] == 'introduction' and not self._intro_open(state, ident):
                step.update(html='', unlocked=False, visible=False, actions=[])
        atomic = AtomicLearningService._public(self.atomic, PointState(state))
        result['steps'].extend(self._point_history(state, atomic['history']))
        result['atomic_history'] = atomic['history']
        if state['display'] == 'atomic':
            atomic['introduction'] = section['introduction'] if self._intro_open(state, section['id']) else None
            atomic['unified_history'] = [
                {**{key: step.get(key) for key in ('id', 'introduction_id', 'title', 'html', 'explanation_html')},
                 **({'source_attributions': step['source_attributions']} if step.get('source_attributions') else {})}
                for step in result['steps'] if step['kind'] == 'introduction' and step['unlocked']
                and step['introduction_id'] != section['id']]
            result = atomic
        result.update(learning_presentation(self.course, state['atomic'], state['read_introductions']))
        result.update(unified_topic=True, introduction_id=section['id'],
                      atomic_progress={'completed': sum(p['mastered'] for p in state['atomic']['points'].values()),
                                       'total': len(state['atomic']['points'])},
                      paradigm_paused=bool(state.get('pause')))
        return result

    def recover_interrupted(self):
        super().recover_interrupted()
        with self._transaction() as con:
            state = self._load(con)
            if state['atomic'].get('pending_submission_id'):
                state['atomic']['pending_submission_id'] = None
                self._save(con, state)
