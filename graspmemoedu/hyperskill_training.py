"""Hyperskill verdicts over native question-bank attempts and independent FSRS."""
import json

from .academy_training import AcademyTraining
from .hyperskill import validate_receipt
from .service import DomainError


class HyperskillTraining(AcademyTraining):
    provider = 'hyperskill'
    event_fields = AcademyTraining.event_fields | {'receipt'}

    def claim(self, round_id):
        with self.training.transaction() as con:
            attempt, run = self._read(con, round_id)
            if attempt['phase'] != 'answer':
                raise DomainError('academy_round_closed', '本次作答已经完成。', 409)
        return {**self.question['hyperskill'], 'provider': 'hyperskill', 'kind': 'automated',
                'task_key': self.question['id'], 'version': self.version, 'round_id': round_id,
                'next_sequence': len(run['events']) + 1, 'mode': attempt['mode']}

    def answer_summary(self, run, rating):
        return run['events'][-1]['receipt']['answer_display']

    def check_event(self, round_id, step_id, event):
        validate_receipt(event)
        return super().check_event(round_id, step_id, event)

    def validate_check(self, con, run, event):
        receipt = event['receipt']
        if receipt['step_id'] != self.question['hyperskill']['step_id']:
            raise DomainError('wrong_hyperskill_problem', '判题结果不属于当前题目。', 409)
        # An upstream submission can score exactly one bank attempt for this learner.
        # The scan includes closed rounds and operates inside the scoring transaction.
        prior = con.execute("""SELECT 1 FROM training_external_rounds r
            JOIN training_attempts a ON a.id=r.attempt, json_each(r.body, '$.events') e
            WHERE a.learner=? AND json_extract(e.value,'$.receipt.submission_id')=? LIMIT 1""",
            (self.learner_id, receipt['submission_id'])).fetchone()
        if prior:
            raise DomainError('idempotency_conflict', '该 Hyperskill 提交已属于另一条记录。', 409)


def public_binding(question, run):
    receipts = [e['receipt'] for e in (run or {}).get('events', [])]
    return {**question['hyperskill'], 'round_id': run['id'] if run else None,
            'opened': bool(run and run.get('opened_at')), 'checks': len(receipts),
            'submissions': [{k: r[k] for k in ('submission_id', 'attempt_id', 'status', 'time', 'feedback')}
                            for r in receipts],
            'last_answer': receipts[-1]['answer_display'] if receipts else ''}


def external_training(training, attempt_id):
    with training.transaction() as con:
        question = json.loads(training._attempt(con, attempt_id)['snapshot'])
    adapter = HyperskillTraining if question.get('hyperskill') else AcademyTraining
    return adapter(training, attempt_id)
