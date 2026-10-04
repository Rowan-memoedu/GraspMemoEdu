"""Introduction ownership over the existing Example-first learning engine."""
from copy import deepcopy
import json
import uuid

from .service import DomainError, LearningService, encode, now
from .topic_runtime import is_introduction, reread_pending


class OrderedLearningService(LearningService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not is_introduction(self.course):
            with self._transaction() as con:
                state = self._load(con)
                # The first v2 reader created mandatory standalone guidance steps.
                # Resume those at the Example choice without changing answered work.
                if state['status'] == 'in_progress' and self._attempt(state).get('kind') == 'introduction':
                    self._start_order(state, state['order_index'])
                    self._save(con, state)

    def _reconcile_version(self, con, row):
        if not is_introduction(self.course):
            return super()._reconcile_version(con, row)
        state = json.loads(row['body'])
        if state.get('topic_kind') != 'introduction':
            return super()._reconcile_version(con, row)
        if state.get('content_version') != self.course['content_version']:
            archived = {k: deepcopy(state.get(k)) for k in
                        ('content_version', 'read_content_version', 'status', 'completed_at', 'started_at',
                         'steps', 'attempts', 'read_introductions', 'reading_status', 'pause')}
            state.setdefault('content_history', []).append(archived)
            paused = bool(state.get('pause'))
            state.update(content_version=self.course['content_version'], attempts=[], steps=[],
                         read_introductions=[], introduction_read=False, pending_submission_id=None,
                         reading_status='paused' if paused else 'in_progress',
                         status='completed' if state.get('completed_at') else 'paused' if paused else 'in_progress')
            state['reread_pending'] = reread_pending(self.course, state)
            self._start_order(state, 0)
        state['revision'] += 1
        con.execute('UPDATE topics SET version=?,body=? WHERE learner=? AND topic=?',
                    (self.course['version'], encode(state), *self.key))

    def _new_attempt(self, state, index):
        if 'order_index' not in state:
            state.update(topic_kind=self.course['topic_kind'], content_version=self.course['content_version'],
                         read_introductions=[], read_content_version=None, reading_status='in_progress', reread_pending=False)
            self._start_order(state, 0)
        elif self.course['content_order'][state['order_index']]['kind'] == 'introduction':
            self._start_order(state, state['order_index'])
        else:
            super()._new_attempt(state, index)

    def _start_order(self, state, index):
        order = self.course['content_order']
        if not is_introduction(self.course):
            if state.get('attempts'):
                previous = self._attempt(state)
                if previous.get('kind') == 'introduction' and previous['status'] != 'completed':
                    obsolete = [s for s in state['steps'] if s['attempt_id'] == previous['id']]
                    state.setdefault('flow_history', []).append(
                        {'reason': 'restore_example_first', 'attempt': deepcopy(previous), 'steps': deepcopy(obsolete)})
                    previous['status'] = 'superseded'
                    state['steps'] = [s for s in state['steps'] if s['attempt_id'] != previous['id']]
            while index < len(order) and order[index]['kind'] == 'introduction':
                index += 1
        if index >= len(order):
            state.update(status='completed', reading_status='completed', reread_pending=False,
                         read_content_version=self.course['content_version'])
            state.setdefault('completed_at', now())
            state.setdefault('read_versions', {})[self.course['content_version']] = now()
            self._add_step(state, kind='completion', phase='completed')
            return
        state['order_index'] = index
        section = order[index]
        if section['kind'] == 'example':
            module_index = next(i for i, m in enumerate(self.course['modules']) if m['id'] == section['id'])
            super()._new_attempt(state, module_index)
            return
        attempt = {'id': str(uuid.uuid4()), 'kind': 'introduction', 'module_id': section['id'],
                   'module_index': -1, 'status': 'in_progress', 'mastery': None, 'learning_path': 'reading',
                   'practice_error_count': 0, 'practice_answered_count': 0, 'practice_target_count': 0,
                   'example_result': None, 'created_at': now(), 'completed_at': None, 'paused_at': None, 'pause_reason': None}
        state['attempts'].append(attempt)
        state['attempt_id'], state['module_index'] = attempt['id'], -1
        self._add_step(state, kind='introduction')['introduction_id'] = section['id']

    def _learn(self, state):
        example = next(s for s in state['steps'] if s['attempt_id'] == state['attempt_id'] and s['kind'] == 'example')
        introduction_id = next(section['id'] for section in
                               reversed(self.course['content_order'][:state['order_index']])
                               if section['kind'] == 'introduction')
        if introduction_id not in state['read_introductions']:
            prior = next((s for s in state['steps'] if s.get('introduction_id') == introduction_id), None)
            if prior:
                prior.update(phase='reading', module_id=self._attempt(state)['module_id'])
                state['active_step_id'] = state['current_step_id'] = prior['id']
            else:
                self._add_step(state, kind='introduction')['introduction_id'] = introduction_id
        else:
            example['phase'] = 'reading'
            state['active_step_id'] = state['current_step_id'] = example['id']

    def _next_module(self, state):
        self._start_order(state, state['order_index'] + 1)

    def _guard(self, state, payload, **options):
        effective = state
        if state.get('reread_pending'):
            effective = {**state, 'status': state.get('reading_status', 'in_progress')}
        return super()._guard(effective, payload, **options)

    def advance(self, payload):
        with self._transaction() as con:
            state = self._load(con)
            step = self._step(state)
            if step['kind'] != 'introduction':
                ordinary = True
            else:
                ordinary = False
                replay, fingerprint = self._request(con, 'continue', payload)
                if not replay:
                    self._guard(state, payload)
                    step['phase'] = 'completed'
                    if step['introduction_id'] not in state['read_introductions']:
                        state['read_introductions'].append(step['introduction_id'])
                    state['introduction_read'] = len(state['read_introductions']) == len(self.course['introductions'])
                    state.setdefault('started_at', now())
                    self._event(con, state, 'introduction_read', introduction_id=step['introduction_id'])
                    if is_introduction(self.course):
                        self._attempt(state).update(status='completed', completed_at=now())
                        self._start_order(state, state['order_index'] + 1)
                    else:
                        self._learn(state)
                    self._record_action(con, 'continue', payload, fingerprint)
                    self._save(con, state)
        if ordinary:
            return super().advance(payload)
        self.flush_logs()
        return self._public(state)

    def _public(self, state):
        result = super()._public(state)
        result.update(topic_kind=self.course['topic_kind'], topic_format_version=2,
                      content_version=self.course['content_version'], reading_status=state.get('reading_status'),
                      reread_pending=state.get('reread_pending', False), completed_at=state.get('completed_at'))
        if state.get('reread_pending') and not state.get('pause'):
            for step in result['steps']:
                if step['active'] and step['current'] and step['kind'] == 'introduction':
                    step['actions'] = ['continue']
        if is_introduction(self.course):
            count = len(state.get('read_introductions', []))
            result['reading_progress'] = round(100 * count / len(self.course['introductions']), 2)
        return result

    def pause(self, payload):
        if not is_introduction(self.course):
            return super().pause(payload)
        with self._transaction() as con:
            state = self._load(con)
            replay, fingerprint = self._request(con, 'pause', payload)
            if not replay:
                if state.get('reading_status') == 'in_progress':
                    self._pause(con, state, 'manual_difficulty')
                    state['reading_status'] = 'paused'
                    if state.get('completed_at'):
                        state['status'] = 'completed'
                    self._save(con, state)
                self._record_action(con, 'pause', payload, fingerprint)
        self.flush_logs()
        return self._public(state)

    def unlock(self, topic_id, module_id, pause_event_id):
        if not is_introduction(self.course):
            return super().unlock(topic_id, module_id, pause_event_id)
        if topic_id != self.topic_id:
            raise DomainError('wrong_topic', '解锁 Topic 不匹配。', 404)
        with self._transaction() as con:
            state = self._load(con)
            previous = con.execute('SELECT * FROM unlocks WHERE pause_event=?', (pause_event_id,)).fetchone()
            if previous:
                if (previous['learner'], previous['topic'], previous['module']) != (*self.key, module_id):
                    raise DomainError('unlock_mismatch', '解锁对象不匹配。')
                return self._public(state)
            pause = state.get('pause')
            if not pause or pause['event_id'] != pause_event_id or pause['module_id'] != module_id:
                raise DomainError('unlock_mismatch', '必须指定当前引论的暂停事件。')
            state.update(pause=None, reading_status='in_progress', status='completed' if state.get('completed_at') else 'in_progress')
            self._attempt(state)['status'] = 'in_progress'
            self._event(con, state, 'admin_unlocked', pause_event_id=pause_event_id)
            con.execute('INSERT INTO unlocks VALUES (?,?,?,?,?)', (pause_event_id, *self.key, module_id, state['attempt_id']))
            self._save(con, state)
        return self._public(state)
