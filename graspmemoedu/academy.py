"""Academy attempts feed the existing atomic memory, never a second scheduler."""
from datetime import timedelta
import hashlib
import json
import re
import uuid

from .atomic import AtomicLearningService
from .fsrs_engine import utc_time
from .review_cards import register_point
from .service import DomainError, encode

POLICY = 'academy-errors-v1'
WEIGHTS = {'compile': .25, 'runtime': .5, 'wrong': 1, 'limit': 1}


def outcome(events, *, finished=False):
    """First pass closes the round. Re-checking identical failures adds no burden."""
    seen, burden, failures = set(), 0, 0
    for event in events:
        category = event['category']
        if category == 'passed':
            return {'rating': 3 if burden == 0 else 2 if burden < 3 else 1,
                    'passed': True, 'burden': burden, 'failures': failures}
        key = (event['code_hash'], category)
        if category in WEIGHTS and key not in seen:
            seen.add(key)
            burden += WEIGHTS[category]
            failures += 1
    return {'rating': 1 if finished and failures else None, 'passed': False,
            'burden': burden, 'failures': failures}


class AcademyLearningService(AtomicLearningService):
    def _next(self, state):
        # The exercise is itself the card; there is no extra memorisation screen.
        state['seen_chunks'] = list(self.chunks)
        state['chunk_last_seen'] = {}
        super()._next(state)
        state['chunk_last_seen'] = {}

    def _question(self, active, *, feedback=False):
        result = super()._question(active, feedback=feedback)
        run = active.get('academy_round')
        result['academy'] = {**self.course['academy'], 'round_id': run['id'] if run else None,
                             'checks': len(run['events']) if run else 0,
                             'opened': bool(run and run.get('opened_at')),
                             'policy': POLICY}
        return result

    def submit(self, payload):
        raise DomainError('academy_check_required', '请在 IDEA 中检查此题，或使用本题的自主评分。', 409)

    def reveal(self, payload):
        raise DomainError('academy_check_required', '请在 IDEA 中查看题目和代码。', 409)

    def launch(self, payload):
        with self._transaction() as con:
            state = self._load(con)
            self._guard_atomic(state, payload)
            active = state.get('active')
            if state['phase'] != 'answer' or not active:
                raise DomainError('invalid_step', '当前没有可打开的练习。', 409)
            run = active.get('academy_round')
            if not run:
                run = {'id': str(uuid.uuid4()), 'started_at': self._time().isoformat(),
                       'events': [], 'policy': POLICY}
                active['academy_round'] = run
                self._save(con, state)
            return {'round_id': run['id'], 'step_id': active['id'], 'mode': state['mode'],
                    'version': self.course['version'], **self.course['academy']}

    def _run(self, state, round_id, step_id):
        active = state.get('active') or {}
        run = active.get('academy_round') or {}
        if (state.get('pause') or state.get('pending_submission_id') or active.get('id') != step_id
                or run.get('id') != round_id):
            raise DomainError('stale_academy_round', '此练习轮次已结束或更新，请从网页重新打开。', 409)
        return run

    def opened(self, round_id, step_id):
        with self._transaction() as con:
            state = self._load(con)
            run = self._run(state, round_id, step_id)
            if state['phase'] != 'answer':
                raise DomainError('academy_round_closed', '此练习已完成，请回到网页继续。', 409)
            if not run.get('opened_at'):
                run['opened_at'] = self._time().isoformat()
                self._save(con, state)
            return {'round_id': round_id, 'next_sequence': len(run['events']) + 1}

    def check_event(self, round_id, step_id, event):
        if (not isinstance(event, dict) or set(event) != {'id', 'sequence', 'category', 'code_hash', 'occurred_at'}
                or not isinstance(event['id'], str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', event['id'])
                or type(event['sequence']) is not int or event['sequence'] < 1
                or event['category'] not in {*WEIGHTS, 'passed', 'environment'}
                or not isinstance(event['code_hash'], str) or not re.fullmatch('[0-9a-f]{64}', event['code_hash'])):
            raise DomainError('invalid_academy_event', '检查结果格式不正确。', 422)
        when = utc_time(event['occurred_at'])
        with self._transaction() as con:
            state = self._load(con)
            # An acknowledgement can be lost after the browser advances to another
            # step. Deduplication must outlive the active presentation.
            prior = con.execute("""SELECT body FROM events WHERE learner=? AND topic=?
                AND json_extract(body,'$.type')='academy_check'
                AND json_extract(body,'$.round_id')=? AND json_extract(body,'$.check.id')=?""",
                (*self.key, round_id, event['id'])).fetchone()
            if prior:
                saved = json.loads(prior['body'])
                if saved['check'] != event or saved['step_id'] != step_id or saved['course_version'] != self.course['version']:
                    raise DomainError('idempotency_conflict', '检查结果标识已用于其他内容。', 409)
                return {'accepted': True, 'duplicate': True, 'closed': state['phase'] != 'answer' or state['active_step_id'] != step_id}
            run = self._run(state, round_id, step_id)
            old = next((e for e in run['events'] if e['id'] == event['id']), None)
            if old:
                if old != event:
                    raise DomainError('idempotency_conflict', '检查结果标识已用于其他内容。', 409)
                return {'accepted': True, 'duplicate': True, 'closed': state['phase'] != 'answer'}
            if state['phase'] != 'answer' or self.course['academy']['kind'] != 'automated':
                raise DomainError('academy_round_closed', '此题不接受新的自动检查结果。', 409)
            if not run.get('opened_at') or event['sequence'] != len(run['events']) + 1:
                raise DomainError('academy_event_order', '请按检查顺序重传结果。', 409)
            if len(run['events']) >= 1000 or when < utc_time(run['started_at']) - timedelta(minutes=5) or when > self._time() + timedelta(minutes=5):
                raise DomainError('invalid_academy_time', '检查时间或数量超出此轮范围。', 422)
            if run['events'] and when < utc_time(run['events'][-1]['occurred_at']):
                raise DomainError('academy_event_order', '检查时间不能倒退。', 409)
            run['events'].append(event)
            result = outcome(run['events'])
            self._event(con, state, 'academy_check', round_id=run['id'], check=event, policy=POLICY)
            if result['rating'] is not None:
                self._complete(con, state, run, result)
            self._save(con, state)
            return {'accepted': True, 'closed': state['phase'] != 'answer', **result}

    def finish(self, payload):
        with self._transaction() as con:
            state = self._load(con)
            old, fingerprint = self._request(con, 'academy_finish', payload)
            if old:
                return self._public(state)
            self._guard_atomic(state, payload)
            active = state.get('active') or {}
            run = active.get('academy_round')
            if state['phase'] != 'answer' or not run or not run.get('opened_at'):
                raise DomainError('academy_not_opened', '请先打开 IDEA 中的本题。', 409)
            if self.course['academy']['kind'] == 'manual':
                rating = payload.get('rating')
                if type(rating) is not int or rating not in (1, 2, 3, 4):
                    raise DomainError('invalid_rating', '请选择 1–4 评分。', 422)
                result = {'rating': rating, 'passed': rating > 1, 'burden': None, 'failures': 0}
            else:
                if payload.get('rating') is not None:
                    raise DomainError('invalid_rating', '自动检查题由检查记录计算评分。', 422)
                result = outcome(run['events'], finished=True)
            if result['rating'] is not None:
                self._complete(con, state, run, result)
            else:
                # No evidence is not a failure. Preserve the code and resume this round.
                raise DomainError('no_scored_attempt', '尚无有效检查结果；本轮未评分，可继续在 IDEA 作答。', 409)
            self._record_action(con, 'academy_finish', payload, fingerprint)
            self._save(con, state)
            return self._public(state)

    def _complete(self, con, state, run, result):
        active, now = state['active'], self._time()
        if result['passed'] and run['events']:
            # Offline delivery keeps the actual first-pass time; bounded clock
            # skew must not move a review before the website-created round.
            now = max(utc_time(run['started_at']), min(now, utc_time(run['events'][-1]['occurred_at'])))
        rating, passed = result['rating'], result['passed']
        labels = {1: 'Again', 2: 'Hard', 3: 'Good', 4: 'Easy'}
        reason = ('自主评分' if self.course['academy']['kind'] == 'manual'
                  else f"首次通过前的错误负担：{result['burden']:g}") + ' · ' + labels[rating]
        ratings = {}
        for pid in active['point_ids']:
            register_point(con, 'atomic', self.topic_id, self.course['version'], pid, 'cs', now.isoformat())
            memory = state['points'][pid]
            scheduled = self.engine.schedule(memory['card_json'], rating, now, None)
            memory.update(card_json=scheduled['card_json'], due_at=scheduled['due_at'], last_rating=rating, mastered=True)
            if passed and not memory['first_passed_at']:
                memory['first_passed_at'] = now.isoformat()
            ratings[pid] = rating
            self._event(con, state, 'atomic_rating', point_id=pid, card_id=active['card_id'],
                        submission_id=run['id'], rating=rating, review_log=scheduled['review_log_json'],
                        algorithm=scheduled['algorithm'], scheduler=scheduled['scheduler_json'], mode=state['mode'],
                        policy=POLICY, error_burden=result['burden'])
        answer = f"IDEA：{len(run['events'])} 次检查" if self.course['academy']['kind'] == 'automated' else '自主评分：' + str(rating)
        con.execute('INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (run['id'], *self.key, active['id'], active['id'], active['card_id'], answer,
                     hashlib.sha256(encode(run).encode()).hexdigest(), 'judged', int(passed), reason,
                     run['started_at'], now.isoformat(), None))
        active.update(phase='feedback', answer=answer, correct=passed, reason=reason,
                      ratings=ratings, answered_at=now.isoformat())
        state['history'].append(json.loads(encode(active)))
        state.update(phase='feedback', status='completed', completed_at=state.get('completed_at') or now.isoformat())
