"""IDE grading adapter for the shared, subject-independent TrainingService."""
from datetime import timedelta
import hashlib
import json
import re
import uuid

from .academy import outcome, POLICY, WEIGHTS
from .fsrs_engine import utc_time
from .service import DomainError
from .training import packed
from .review_cards import require_unreserved


class AcademyTraining:
    provider = 'academy'
    event_fields = {'id', 'sequence', 'category', 'code_hash', 'occurred_at'}

    def __init__(self, training, attempt_id):
        self.training, self.attempt_id = training, attempt_id
        self.learner_id = training.learner_id
        with training.transaction() as con:
            row = training._attempt(con, attempt_id)
            self.question = json.loads(row['snapshot'])
            if not self.question.get(self.provider):
                raise DomainError('not_academy', '本题未绑定 JetBrains Academy。', 422)
            self.bank_id, self.version = row['bank'], row['version']

    def _read(self, con, round_id=None):
        attempt = self.training._attempt(con, self.attempt_id)
        row = con.execute('SELECT body FROM training_external_rounds WHERE attempt=?',
                          (self.attempt_id,)).fetchone()
        run = json.loads(row['body']) if row else None
        if round_id is not None and (not run or run['id'] != round_id):
            raise DomainError('stale_academy_round', '此练习轮次已更新，请从题库重新打开。', 409)
        return attempt, run

    def _save(self, con, run):
        con.execute('INSERT INTO training_external_rounds VALUES (?,?) '
                    'ON CONFLICT(attempt) DO UPDATE SET body=excluded.body',
                    (self.attempt_id, packed(run)))

    def launch(self):
        with self.training.transaction() as con:
            attempt, run = self._read(con)
            if attempt['phase'] != 'answer':
                raise DomainError('academy_round_closed', '本次作答已完成，请开始新一次训练。', 409)
            if run is None:
                run = {'id': str(uuid.uuid4()), 'started_at': utc_time(self.training.clock()).isoformat(),
                       'events': [], 'policy': POLICY, **self.round_metadata()}
                self._save(con, run)
        return {'round_id': run['id'], 'step_id': self.attempt_id, 'version': self.version}

    def round_metadata(self):
        return {}

    def claim(self, round_id):
        with self.training.transaction() as con:
            attempt, run = self._read(con, round_id)
            if attempt['phase'] != 'answer':
                raise DomainError('academy_round_closed', '本次作答已经完成。', 409)
        # The installed IDE manifest is bound to source content, not the bank envelope.
        return {**self.question['academy'], 'round_id': round_id, 'next_sequence': len(run['events']) + 1,
                'mode': attempt['mode'], 'reset_policy': 'archive_then_template'}

    def opened(self, round_id, step_id):
        if step_id != self.attempt_id:
            raise DomainError('stale_academy_round', '作答标识不匹配。', 409)
        with self.training.transaction() as con:
            attempt, run = self._read(con, round_id)
            if attempt['phase'] != 'answer':
                raise DomainError('academy_round_closed', '本次作答已完成。', 409)
            if not run.get('opened_at'):
                run['opened_at'] = utc_time(self.training.clock()).isoformat()
                self._save(con, run)
            return {'round_id': round_id, 'next_sequence': len(run['events']) + 1}

    def _complete(self, con, attempt, run, result):
        rating = result['rating']
        manual = self.manual
        reason = ('自主评分' if manual else f"首次通过前的错误负担：{result['burden']:g}")
        reason += ' · ' + {1: 'Again', 2: 'Hard', 3: 'Good', 4: 'Easy'}[rating]
        now = utc_time(self.training.clock())
        if result['passed'] and run['events']:
            now = max(utc_time(run['started_at']), min(now, utc_time(run['events'][-1]['occurred_at'])))
        answer = self.answer_summary(run, rating)
        con.execute('INSERT INTO training_submissions VALUES (?,?,?,?,?,NULL,?,?)',
                    (run['id'], self.learner_id, self.attempt_id, hashlib.sha256(packed(run).encode()).hexdigest(),
                     answer, 'judged', reason))
        con.execute('UPDATE training_attempts SET submission_id=? WHERE id=?', (run['id'], self.attempt_id))
        require_unreserved(con, self.learner_id, 'training', attempt['bank'], attempt['version'],
                           attempt['question'], now=utc_time(self.training.clock()).isoformat())
        return self.training._complete(con, attempt, correct=result['passed'], reason=reason, rating=rating,
            self_rating=manual, completed_at=now, enroll=True,
            first_try_correct=not manual and result['passed'] and result['failures'] == 0)

    @property
    def manual(self):
        return self.question[self.provider].get('kind') == 'manual'

    def answer_summary(self, run, rating):
        return '自主评分：' + str(rating) if self.manual else f"IDEA：{len(run['events'])} 次检查"

    def validate_check(self, con, run, event):
        pass

    def check_event(self, round_id, step_id, event):
        if (step_id != self.attempt_id or not isinstance(event, dict)
                or set(event) != self.event_fields
                or not isinstance(event['id'], str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', event['id'])
                or type(event['sequence']) is not int or event['sequence'] < 1
                or not isinstance(event['category'], str)
                or event['category'] not in {*WEIGHTS, 'passed', 'environment'}
                or not isinstance(event['code_hash'], str) or not re.fullmatch('[0-9a-f]{64}', event['code_hash'])):
            raise DomainError('invalid_academy_event', '检查结果格式不正确。', 422)
        try:
            when = utc_time(event['occurred_at'])
        except (ValueError, TypeError, AttributeError):
            raise DomainError('invalid_academy_time', '检查时间格式不正确。', 422)
        with self.training.transaction() as con:
            attempt, run = self._read(con, round_id)
            prior = next((e for e in run['events'] if e['id'] == event['id']), None)
            if prior:
                if prior != event:
                    raise DomainError('idempotency_conflict', '检查结果标识已用于其他内容。', 409)
                return {'accepted': True, 'duplicate': True, 'closed': attempt['phase'] != 'answer'}
            if attempt['phase'] != 'answer' or self.manual:
                raise DomainError('academy_round_closed', '此题不接受新的自动检查结果。', 409)
            if not run.get('opened_at') or event['sequence'] != len(run['events']) + 1:
                raise DomainError('academy_event_order', '请按检查顺序重传结果。', 409)
            if (len(run['events']) >= 1000 or when < utc_time(run['started_at']) - timedelta(minutes=5)
                    or when > utc_time(self.training.clock()) + timedelta(minutes=5)
                    or run['events'] and when < utc_time(run['events'][-1]['occurred_at'])):
                raise DomainError('invalid_academy_time', '检查时间或数量超出此轮范围。', 422)
            self.validate_check(con, run, event)
            run['events'].append(event)
            result = outcome(run['events'])
            closed = result['rating'] is not None
            if closed:
                completed = self._complete(con, attempt, run, result)
                result['rating'] = completed['rating']
            self._save(con, run)
            return {'accepted': True, 'closed': closed, **result}

    def finish(self, rating=None):
        with self.training.transaction() as con:
            attempt, run = self._read(con)
            manual = self.manual
            if attempt['phase'] == 'done':
                saved = json.loads(attempt['result'])
                if saved.get('self_rating') != rating:
                    raise DomainError('rating_conflict', '本次作答已记录另一项评分。', 409)
                return
            if not run or not run.get('opened_at'):
                raise DomainError('academy_not_opened', '请先打开 IDEA 中的本题。', 409)
            if manual:
                if type(rating) is not int or rating not in (1, 2, 3, 4):
                    raise DomainError('invalid_rating', '请选择 1–4 评分。', 422)
                result = {'rating': rating, 'passed': rating > 1, 'burden': None, 'failures': 0}
            else:
                if rating is not None:
                    raise DomainError('invalid_rating', '自动检查题由检查记录计算评分。', 422)
                result = outcome(run['events'], finished=True)
            if result['rating'] is None:
                raise DomainError('no_scored_attempt', '尚无有效检查结果；本轮未评分，可继续在 IDEA 作答。', 409)
            self._complete(con, attempt, run, result)
