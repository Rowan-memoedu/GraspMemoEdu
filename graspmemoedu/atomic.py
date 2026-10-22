"""Transactional point-level learning, interleaving and FSRS review.

The shared topics/actions/submissions/events tables remain the source of truth.
Question variants are presentations; only memory points own scheduling state.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import uuid

from fsrs import Card

from .atomic_content import GAP_SECONDS, MODE, policy_for, point_chunks, chunk_presentation, card_chunks, learning_presentation
from .fsrs_engine import FSRSEngine, utc_time
from .judging import judge_answer
from .question_types import public_interaction, parse_answer, display_answer
from .service import DomainError, LearningService, encode
from .review_cards import eligible_presentations, register_point, reserved_points, point_id as global_point_id


class AtomicLearningService(LearningService):
    def __init__(self, course, data_dir, judge=None, learner_id='local', *, clock=None, engine=None, runtime=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.engine = engine or FSRSEngine()
        self.chunks = {c['id']: c for c in course['memory_chunks']}
        self.point_chunks = point_chunks(course)
        super().__init__(course, data_dir, judge, learner_id, runtime=runtime)

    def _time(self):
        return utc_time(self.clock())

    def _new_attempt(self, state, index):
        state.clear()
        state.update(training_mode=MODE, revision=0, status='in_progress', started_at=None,
                     completed_at=None, pause=None, pending_submission_id=None,
                     attempt_id=str(uuid.uuid4()), active_step_id='introduction',
                     mode='learn', phase='introduction', active=None, seen_chunks=[], chunk_last_seen={},
                     pending_chunk=None, history=[], card_uses={},
                     policy=policy_for(self.course), points={p: {'mastered': False, 'card_json': None,
                     'due_at': None, 'first_passed_at': None} for p in self.point_chunks})

    def _event(self, con, state, event_type, **extra):
        event = {'event_id': str(uuid.uuid4()), 'time': self._time().isoformat(), 'type': event_type,
                 'learner_id': self.learner_id, 'topic_id': self.topic_id,
                 'course_version': self.course['version'], 'training_mode': MODE,
                 'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}
        con.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                    (event['event_id'], *self.key, event['time'], encode(event)))
        return event['event_id']

    def _guard_atomic(self, state, payload):
        if state['pause']:
            raise DomainError('topic_paused', '当前 Topic 已暂停。')
        if (payload.get('expected_revision') != state['revision']
                or payload.get('attempt_id') != state['attempt_id']
                or payload.get('step_id') != state['active_step_id']):
            raise DomainError('stale_revision', '学习状态已更新，请重新同步。')
        if state['pending_submission_id']:
            raise DomainError('submission_pending', '答案正在判定，请稍候。')

    def _eligible(self, state):
        current = self._time()
        candidates, wakeups = set(), []
        with closing(self._connect()) as con:
            reserved = reserved_points(con, self.learner_id, current.isoformat())
        for point_id, memory in state['points'].items():
            if memory.get('stopped_at'):
                continue
            if global_point_id('atomic', self.topic_id, self.course['version'], point_id) in reserved:
                continue
            if (state['mode'] == 'learn') == memory['mastered']:
                continue
            due = utc_time(memory['due_at']) if memory['due_at'] else current
            last = state['chunk_last_seen'].get(self.point_chunks[point_id])
            if last:
                due = max(due, utc_time(last) + timedelta(seconds=GAP_SECONDS))
            wakeups.append(due.isoformat())
            if due <= current:
                candidates.add(point_id)
        return candidates, min(wakeups, default=None)

    def _next(self, state):
        state['active'] = None
        state['active_step_id'] = str(uuid.uuid4())
        state['pending_chunk'] = None
        if state.get('preview_single_card'):
            state.update(phase='completed', status='completed')
            return
        if state['mode'] == 'learn' and all(p['mastered'] for p in state['points'].values()):
            state.update(status='completed', phase='completed', completed_at=self._time().isoformat())
            return
        eligible, _ = self._eligible(state)
        cards = eligible_presentations(self.questions.values(), eligible, state['card_uses'],
                                       singles=state['mode'] != 'review', phase=state['mode'])
        if not cards:
            state['phase'] = 'waiting'
            return
        # Prefer an available bundle; otherwise rotate least-used variants.
        # The stable author order also preserves the order of new chunks.
        card = cards[0]
        if state['mode'] == 'learn' and state.get('requested_points'):
            order = {point: index for index, point in enumerate(state['requested_points'])}
            card = min(cards, key=lambda candidate: min(order.get(p, len(order)) for p in candidate['point_ids']))
        chunk_ids = list(dict.fromkeys(self.point_chunks[p] for p in card['point_ids']))
        unseen = next((c for c in chunk_ids if c not in state['seen_chunks']), None)
        if state['mode'] == 'learn' and unseen:
            state.update(phase='chunk', pending_chunk=unseen)
            return
        shown = self._time().isoformat()
        state['active'] = {'id': state['active_step_id'], 'card_id': card['id'], 'point_ids': card['point_ids'],
                           'mode': state['mode'], 'shown_at': shown, 'phase': 'answer'}
        state['phase'] = 'answer'
        for chunk in chunk_ids:
            state['chunk_last_seen'][chunk] = shown
        state['card_uses'][card['id']] = state['card_uses'].get(card['id'], 0) + 1

    def _question(self, active, *, feedback=False):
        card = active.get('snapshot') or self.questions[active['card_id']]
        result = {'id': active['id'], 'card_id': card['id'], 'title': card['title'], 'html': card['html'],
                  'point_ids': card['point_ids'], 'phase': active['phase'], 'shown_at': active['shown_at'],
                  'interaction': public_interaction(card)}
        from .self_assessment import presentation
        result.update(presentation(card, active, visible=feedback))
        if card.get('audio'):
            from .training_audio import public_audio
            result['audio'] = public_audio(card, self.topic_id, feedback=feedback)
        if feedback:
            result.update(answer=active.get('answer'), correct=active.get('correct'), reason=active.get('reason'),
                          answer_display=display_answer(card, active.get('answer')),
                          explanation_html=card['explanation_html'], ratings=active.get('ratings', {}))
            if active.get('assessment_result'):
                result['assessment_result'] = active['assessment_result']
            if active.get('stop_requested'):
                result['stop_requested'] = True
        return result

    def _public(self, state):
        learned = bool(state['completed_at'])
        history = []
        for record in state['history']:
            mastered = (all(state['points'][p]['mastered'] for p in record['point_ids'])
                        if record['mode'] == 'learn' else record['correct'])
            item = {'id': record['id'], 'point_ids': record['point_ids'], 'mode': record['mode'],
                    'mastered': mastered, 'viewable': mastered}
            if mastered:
                item.update(self._question(record, feedback=True))
            history.append(item)
        _, next_at = self._eligible(state)
        result = {'training_mode': MODE, 'topic_id': self.topic_id, 'course_version': self.course['version'],
                  'learner_id': self.learner_id,
                  'title': self.course['title'], 'math_css': self.course.get('math_css', ''),
                  'revision': state['revision'], 'status': state['status'], 'mode': state['mode'],
                  'phase': state['phase'], 'attempt_id': state['attempt_id'],
                  'active_step_id': state['active_step_id'], 'pending_submission_id': state['pending_submission_id'],
                  'pause': state['pause'], 'history': history, 'next_available_at': next_at,
                  'server_time': self._time().isoformat(), 'completed_at': state['completed_at'],
                  'learned_count': sum(p['mastered'] for p in state['points'].values()),
                  'point_count': len(state['points']), 'current': None, 'chunk': None,
                  'introduction': self.course['introduction'] if learned or not state['started_at']
                  or state['phase'] == 'chunk' or state.get('preview_single_card') else None}
        if state['active']:
            result['current'] = self._question(state['active'], feedback=state['active']['phase'] == 'feedback')
            result['current']['memory_chunks'] = card_chunks(self.course, result['current'])
        if state['phase'] == 'chunk' and not state['pause']:
            chunk = self.chunks[state['pending_chunk']]
            result['chunk'] = chunk_presentation(self.course, chunk)
        from .topic_structure import sections
        result.update(learning_presentation(self.course, state,
                      [s['id'] for s in sections(self.course)] if state['started_at'] or learned else []))
        return result

    def preview_card(self, card_id, request_id):
        if self.runtime is None:
            raise DomainError('feature_forbidden', '仅管理员临时会话可直接进入卡片。', 403)
        card = self.questions.get(card_id)
        if card is None:
            raise DomainError('unknown_card', '未找到此卡片。', 404)
        with self._transaction() as con:
            state = self._load(con)
            payload = {'request_id': request_id, 'card_id': card_id}
            replay, fingerprint = self._request(con, 'preview_card', payload)
            if replay:
                return self._public(state)
            if state['pending_submission_id']:
                raise DomainError('submission_pending', '答案正在判定，请稍候。')
            revision = state['revision']
            self._new_attempt(state, 0)
            shown = self._time().isoformat()
            state.update(revision=revision, preview_single_card=True, mode='review', phase='answer',
                         started_at=shown, active_step_id=str(uuid.uuid4()))
            state['active'] = {'id': state['active_step_id'], 'card_id': card_id, 'point_ids': card['point_ids'],
                               'mode': 'review', 'shown_at': shown, 'phase': 'answer'}
            self._save(con, state)
            self._record_action(con, 'preview_card', payload, fingerprint)
        return self._public(state)

    def request_points(self, point_ids):
        """Prioritize existing points without inventing mastery or FSRS events.

        The current question and Introduction remain in place. TopicPoints uses
        the same method, with its existing Introduction scope applied at selection.
        """
        if (not isinstance(point_ids, list) or not 1 <= len(point_ids) <= 20
                or any(not isinstance(p, str) or p not in self.point_chunks for p in point_ids)
                or len(set(point_ids)) != len(point_ids)):
            raise DomainError('invalid_memory_points', '所选记忆点不属于当前 Topic。', 422)
        with self._transaction() as con:
            state = self._load(con)
            pending = [p for p in point_ids if not state['points'][p]['mastered']]
            requested = state.get('requested_points', [])
            additions = [p for p in pending if p not in requested]
            if additions:
                if state.get('pending_submission_id'):
                    raise DomainError('submission_pending', '答案正在判定，请完成后再加入记忆。')
                state['requested_points'] = [*requested, *additions]
                self._save(con, state)
        return {'status': 'pending' if pending else 'learned', 'point_ids': point_ids,
                'pending_point_ids': pending,
                'message': '已加入待学；完成当前步骤后优先学习，作答后按原规则安排复习。' if pending
                           else '已关联原记忆点，保留当前复习进度。'}

    def choose(self, payload):
        with self._transaction() as con:
            replay, fingerprint = self._request(con, 'atomic_begin', payload)
            state = self._load(con)
            if not replay:
                self._guard_atomic(state, payload)
                mode = payload.get('choice')
                if mode not in ('learn', 'review'):
                    raise DomainError('invalid_choice', '请选择学习或复习。', 422)
                if mode == 'learn' and state['phase'] != 'introduction':
                    raise DomainError('already_started', '本 Topic 已开始训练。')
                if mode == 'review' and not state['completed_at']:
                    raise DomainError('learning_incomplete', '请先完成本 Topic 的初次学习。')
                if mode == 'review' and state['mode'] == 'review' and state['active']:
                    raise DomainError('review_in_progress', '请先完成当前复习卡片。')
                state['mode'] = mode
                state['started_at'] = state['started_at'] or self._time().isoformat()
                self._next(state)
                self._record_action(con, 'atomic_begin', payload, fingerprint)
                self._event(con, state, 'atomic_started', mode=mode)
                self._save(con, state)
        return self._public(state)

    def advance(self, payload):
        with self._transaction() as con:
            replay, fingerprint = self._request(con, 'atomic_continue', payload)
            state = self._load(con)
            if not replay:
                self._guard_atomic(state, payload)
                if state['phase'] not in ('chunk', 'feedback', 'waiting'):
                    raise DomainError('invalid_step', '当前步骤还不能继续。')
                if state['phase'] == 'chunk':
                    state['seen_chunks'].append(state['pending_chunk'])
                self._next(state)
                self._record_action(con, 'atomic_continue', payload, fingerprint)
                self._save(con, state)
        return self._public(state)

    def _grade(self, con, state, item, correct, reason):
        active, when = state['active'], self._time()
        ratings = {}
        assessment = active.get('self_assessment') or {}
        stop = assessment.get('submission_id') == item['id'] and assessment.get('rating') == 5
        from .point_assessment import assess_points
        card = active.get('snapshot') or self.questions[active['card_id']]
        point_correct, details = assess_points(card, item['answer'], correct, self_assessed=bool(assessment))
        if details:
            from .atomic_content import memory_points
            labels = {p['id']: p['text'] for p in memory_points(self.course)}
            for point in details['points']:
                point['text'] = labels[point['id']]
            active['assessment_result'] = details
        for point_id in active['point_ids']:
            register_point(con, 'atomic', self.topic_id, self.course['version'], point_id,
                           self.course['subject_id'], when.isoformat())
            memory = state['points'][point_id]
            if stop:
                # The fifth control retires the memory point; it is not an
                # FSRS rating. Retain any existing card state for audit/history.
                memory.update(mastered=True, first_passed_at=memory.get('first_passed_at') or when.isoformat(),
                              stopped_at=when.isoformat(), due_at=None)
                ratings[point_id] = None
                self._event(con, state, 'atomic_reviews_stopped', point_id=point_id, card_id=active['card_id'],
                            submission_id=item['id'])
                continue
            passed = point_correct[point_id]
            rating = (None if passed else 1) if state['mode'] == 'learn' else (state['policy']['correct_rating'] if passed else 1)
            ratings[point_id] = rating
            if rating is not None:
                scheduled = self.engine.schedule(memory['card_json'], rating, when, item['elapsed_ms'])
                memory.update(card_json=scheduled['card_json'], due_at=scheduled['due_at'], last_rating=rating)
                self._event(con, state, 'atomic_rating', point_id=point_id, card_id=active['card_id'],
                            submission_id=item['id'], rating=rating, review_log=scheduled['review_log_json'],
                            algorithm=scheduled['algorithm'], scheduler=scheduled['scheduler_json'], mode=state['mode'])
            if state['mode'] == 'learn' and passed:
                memory.update(mastered=True, first_passed_at=when.isoformat())
                first_due = when + timedelta(seconds=state['policy']['first_review_seconds'])
                memory['due_at'] = first_due.isoformat()
                if memory['card_json'] is not None:
                    card = Card.from_json(memory['card_json'])
                    card.due = first_due
                    memory['card_json'] = card.to_json()
        active.update(phase='feedback', answer=item['answer'], correct=correct, reason=reason,
                      ratings=ratings, answered_at=when.isoformat())
        if stop:
            active['stop_requested'] = True
        state['history'].append(dict(active))
        state['phase'] = 'feedback'

    def submit(self, payload):
        request_id = payload.get('request_id')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise DomainError('invalid_request', '请求缺少有效标识。', 422)
        fingerprint = self._fingerprint('submit', payload)
        with self._transaction() as con:
            old = con.execute('SELECT * FROM submissions WHERE id=?', (request_id,)).fetchone()
            if old and (old['learner'], old['topic'], old['fingerprint']) != (*self.key, fingerprint):
                raise DomainError('idempotency_conflict', '请求标识已用于其他操作。')
            state = self._load(con)
            if old and old['status'] != 'error':
                return self._submission_public(old, state)
            self._guard_atomic(state, payload)
            active = state['active']
            if not active or state['phase'] != 'answer':
                raise DomainError('invalid_step', '当前没有可提交的卡片。')
            answer = payload.get('answer')
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 16384:
                raise DomainError('invalid_answer', '请填写完整答案。', 422)
            from .self_assessment import answer as assessed_answer
            try:
                assessed_answer(self.questions[active['card_id']], active, payload, allow_stop=True)
                if payload.get('self_rating') is None:
                    parse_answer(self.questions[active['card_id']], answer)
            except (ValueError, TypeError) as exc:
                raise DomainError('invalid_answer', '请填写完整且有效的答案。', 422) from exc
            if con.execute('SELECT 1 FROM actions WHERE learner=? AND topic=? AND id=?', (*self.key, request_id)).fetchone():
                raise DomainError('idempotency_conflict', '请求标识已用于其他操作。')
            timestamp = self._time().isoformat()
            if old:
                con.execute("UPDATE submissions SET status='pending',reason=NULL,updated_at=? WHERE id=?", (timestamp, request_id))
            else:
                con.execute('INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (request_id, *self.key, active['id'], active['id'], active['card_id'], answer,
                             fingerprint, 'pending', None, None, timestamp, timestamp, payload.get('elapsed_ms')))
            state['pending_submission_id'] = request_id
            self._save(con, state)
            row = con.execute('SELECT * FROM submissions WHERE id=?', (request_id,)).fetchone()
            return self._submission_public(row, state)

    def reveal(self, payload):
        from .self_assessment import reveal
        with self._transaction() as con:
            state = self._load(con)
            replay, fingerprint = self._request(con, 'atomic_reveal', payload)
            if not replay:
                self._guard_atomic(state, payload)
                if state['phase'] != 'answer' or not state['active']:
                    raise DomainError('invalid_step', '当前没有可显示答案的卡片。', 409)
                reveal(self.questions[state['active']['card_id']], state['active'])
                self._record_action(con, 'atomic_reveal', payload, fingerprint)
                self._save(con, state)
        return self._public(state)

    def _submission_public(self, row, state):
        record = next((r for r in state['history'] if r['id'] == row['step']), None)
        if record and record['id'] != (state['active'] or {}).get('id'):
            viewable = (all(state['points'][p]['mastered'] for p in record['point_ids'])
                        if record['mode'] == 'learn' else record['correct'])
            if not viewable:
                raise DomainError('locked_step', '未掌握的卡片暂不可查看。', 403)
        if row['step'] != (state['active'] or {}).get('id') and not record and row['status'] == 'judged':
            raise DomainError('locked_step', '该内容尚未解锁。', 403)
        if row['status'] == 'discarded':
            return {'submission_id': row['id'], 'status': 'discarded', 'correct': None,
                    'reason': '作答轮次已变化，本次结果未应用。', 'state': self._public(state)}
        return {'submission_id': row['id'], 'status': row['status'], 'correct': bool(row['correct']) if row['correct'] is not None else None,
                'reason': row['reason'], 'state': self._public(state)}

    def submission(self, submission_id):
        with closing(self._connect()) as con:
            row = con.execute('SELECT * FROM submissions WHERE id=? AND learner=? AND topic=?', (submission_id, *self.key)).fetchone()
            if not row:
                raise DomainError('unknown_submission', '未找到此提交。', 404)
            state = self._load(con)
            # An unsuccessful card that will recur may only be inspected while active.
            active_id = (state['active'] or {}).get('id')
            record = next((r for r in state['history'] if r['id'] == row['step']), None)
            if record and record['id'] != active_id and not all(state['points'][p]['mastered'] for p in record['point_ids']):
                raise DomainError('locked_step', '未掌握的卡片暂不可查看。', 403)
            return self._submission_public(row, state)

    def instant_submission(self, submission_id):
        from .judging import instant_question
        with closing(self._connect()) as con:
            row = con.execute('SELECT question FROM submissions WHERE id=? AND learner=? AND topic=?',
                              (submission_id, *self.key)).fetchone()
            return bool(row and instant_question(self.questions[row['question']]))

    def process_submission(self, submission_id):
        with self._transaction() as con:
            row = con.execute("SELECT * FROM submissions WHERE id=? AND learner=? AND topic=? AND status='pending'", (submission_id, *self.key)).fetchone()
            if not row:
                return
            item = dict(row)
            target = self._load(con).get('active') or {}
            con.execute("UPDATE submissions SET status='running' WHERE id=?", (submission_id,))
        try:
            from .self_assessment import verdict as self_verdict
            correct, reason = self_verdict(target, submission_id) or judge_answer(self.judge, self.questions[item['question']], item['answer'])
            if correct is not None and type(correct) is not bool:
                correct, reason = None, '判题结果无效，请重试；本次不计错。'
        except Exception:
            correct, reason = None, '判题暂不可用，请重试；本次不计错。'
        with self._transaction() as con:
            state = self._load(con)
            applies = (not state['pause'] and state['pending_submission_id'] == submission_id
                       and state['active'] and state['active']['id'] == item['step'])
            status = 'discarded' if not applies else 'error' if correct is None else 'judged'
            con.execute('UPDATE submissions SET status=?,correct=?,reason=?,updated_at=? WHERE id=?',
                        (status, correct, reason, self._time().isoformat(), submission_id))
            if applies:
                state['pending_submission_id'] = None
                if correct is not None:
                    self._grade(con, state, item, correct, reason)
                self._save(con, state)
        self.flush_logs()

    def _pause_atomic(self, con, state, reason):
        event_id = self._event(con, state, 'atomic_paused', reason=reason)
        state['pause'] = {'event_id': event_id, 'module_id': 'atomic', 'reason': reason,
                          'created_at': self._time().isoformat()}
        state['status'] = 'completed' if state['completed_at'] else 'paused'
        state['pending_submission_id'] = None

    def pause(self, payload):
        with self._transaction() as con:
            replay, fingerprint = self._request(con, 'atomic_pause', payload)
            state = self._load(con)
            if not replay and not state['pause']:
                self._pause_atomic(con, state, 'manual')
                self._record_action(con, 'atomic_pause', payload, fingerprint)
                self._save(con, state)
        return self._public(state)

    def unlock(self, topic_id, module_id, pause_event_id):
        with self._transaction() as con:
            state = self._load(con)
            if topic_id != self.topic_id or module_id != 'atomic':
                raise DomainError('unlock_mismatch', '必须指定当前 Topic 和 atomic 训练。')
            prior = con.execute('SELECT * FROM unlocks WHERE pause_event=?', (pause_event_id,)).fetchone()
            if prior:
                if (prior['learner'], prior['topic'], prior['module']) != (*self.key, module_id):
                    raise DomainError('unlock_mismatch', '解锁对象不匹配。')
                return self._public(state)
            if not state['pause'] or state['pause']['event_id'] != pause_event_id:
                raise DomainError('unlock_mismatch', '必须指定当前暂停事件。')
            state.update(status='completed' if state['completed_at'] else 'in_progress', pause=None,
                         attempt_id=str(uuid.uuid4()), pending_submission_id=None)
            if not state['started_at']:
                state.update(phase='introduction', active=None, active_step_id='introduction')
            else:
                self._next(state)
            con.execute('INSERT INTO unlocks VALUES (?,?,?,?,?)', (pause_event_id, *self.key, module_id, state['attempt_id']))
            self._event(con, state, 'atomic_unlocked', pause_event_id=pause_event_id)
            self._save(con, state)
        return self._public(state)

    def select(self, payload):
        raise DomainError('locked_step', '原子卡学习记录仅允许查看已掌握内容。', 403)

    def reset_progress(self, payload):
        with self._transaction() as con:
            replay, fingerprint = self._request(con, 'atomic_reset', payload)
            state = self._load(con)
            if not replay:
                self._guard_atomic(state, payload)
                revision = state['revision']
                self._new_attempt(state, 0)
                state['revision'] = revision
                self._record_action(con, 'atomic_reset', payload, fingerprint)
                self._event(con, state, 'atomic_reset')
                self._save(con, state)
        return self._public(state)
