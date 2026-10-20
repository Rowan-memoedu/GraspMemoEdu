"""Hyperskill receipts use the established Academy round and atomic FSRS state."""
import json

from .academy import AcademyLearningService
from .service import DomainError, encode


class HyperskillLearningService(AcademyLearningService):
    event_fields = AcademyLearningService.event_fields | {'receipt'}

    def binding(self, active=None):
        if not active:
            raise DomainError('invalid_step', '当前没有可作答的题目。', 409)
        card = self.questions[active['card_id']]
        return {'task_key': card['id'], 'kind': 'automated',
                'provider': 'hyperskill', **self.course['hyperskill'], **card['hyperskill']}

    def _question(self, active, *, feedback=False):
        result = super()._question(active, feedback=feedback)
        result['hyperskill'] = result.pop('academy')
        result.pop('self_assessment', None)
        run = active.get('academy_round') or {}
        receipts = [event['receipt'] for event in run.get('events', [])]
        result['hyperskill']['submissions'] = [
            {k: r[k] for k in ('submission_id', 'attempt_id', 'status', 'time', 'feedback')}
            for r in receipts]
        if receipts:
            result['hyperskill']['last_answer'] = receipts[-1]['answer_display']
        return result

    def answer_summary(self, run, result):
        return run['events'][-1]['receipt']['answer_display']

    def check_event(self, round_id, step_id, event):
        receipt = event.get('receipt') if isinstance(event, dict) else None
        fields = {'submission_id', 'attempt_id', 'step_id', 'status', 'time',
                  'reply', 'answer_display', 'feedback'}
        if (not isinstance(receipt, dict) or set(receipt) != fields
                or any(type(receipt[k]) is not int or receipt[k] <= 0
                       for k in ('submission_id', 'attempt_id', 'step_id'))
                or receipt['status'] not in ('correct', 'wrong')
                or receipt['time'] != event.get('occurred_at')
                or event.get('category') != ('passed' if receipt['status'] == 'correct' else 'wrong')
                or not isinstance(receipt['reply'], dict)
                or not isinstance(receipt['answer_display'], str)
                or not 1 <= len(receipt['answer_display']) <= 8000
                or not isinstance(receipt['feedback'], str) or len(receipt['feedback']) > 12000
                or len(encode(receipt)) > 40000):
            raise DomainError('invalid_hyperskill_receipt', 'Hyperskill 判题回执格式不正确。', 422)
        return super().check_event(round_id, step_id, event)

    def validate_check(self, con, state, round_id, step_id, event):
        receipt = event['receipt']
        # Check upstream identity inside the same write transaction as FSRS.
        records = con.execute("""SELECT body FROM events WHERE learner=? AND topic=?
            AND json_extract(body,'$.type')='academy_check'
            AND json_extract(body,'$.check.receipt.submission_id')=?""",
            (*self.key, receipt['submission_id'])).fetchall()
        if records:
            prior = json.loads(records[0]['body'])
            if prior['round_id'] != round_id or prior['step_id'] != step_id or prior['check'] != event:
                raise DomainError('idempotency_conflict', '该 Hyperskill 提交已属于另一条记录。', 409)
        elif self.binding(state.get('active'))['step_id'] != receipt['step_id']:
            raise DomainError('wrong_hyperskill_problem', '判题结果不属于当前题目。', 409)
