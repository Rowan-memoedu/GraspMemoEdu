"""Hyperskill verdicts over native question-bank attempts and independent FSRS."""
import json
from contextlib import closing
import hashlib
import re

from .academy_training import AcademyTraining
from .hyperskill import validate_receipt
from .service import DomainError
from .hyperskill_answers import AnswerStore, enrich, parse_local, packed, display_local
from decimal import InvalidOperation
from .fsrs_engine import utc_time


class HyperskillTraining(AcademyTraining):
    provider = 'hyperskill'
    event_fields = AcademyTraining.event_fields | {'receipt'}

    def __init__(self, training, attempt_id):
        super().__init__(training, attempt_id)
        self.question = enrich(training, self.bank_id, self.question)

    def claim(self, round_id):
        with self.training.transaction() as con:
            attempt, run = self._read(con, round_id)
            if attempt['phase'] != 'answer':
                raise DomainError('academy_round_closed', '本次作答已经完成。', 409)
        return {**self.question['hyperskill'], 'provider': 'hyperskill', 'kind': 'automated',
                **({'presentation': self.question['hyperskill_presentation']} if self.question.get('hyperskill_presentation') else {}),
                'task_key': self.question['id'], 'version': self.version, 'round_id': round_id,
                'next_sequence': len(run['events']) + 1, 'mode': attempt['mode']}

    def answer_summary(self, run, rating):
        event = run['events'][-1]
        return event.get('receipt', event.get('local', {}))['answer_display']

    def check_event(self, round_id, step_id, event):
        validate_receipt(event)
        if 'dataset' in event['receipt'] and self.question['hyperskill']['type'] != 'code':
            from .hyperskill_answers import canonical
            try:
                canonical(self.question['hyperskill']['type'], event['receipt']['dataset'], event['receipt']['reply'])
            except (ValueError, TypeError, KeyError, IndexError, InvalidOperation):
                raise DomainError('invalid_hyperskill_receipt', 'Hyperskill 判题回执的题面或答案不完整。', 422)
        result = super().check_event(round_id, step_id, event)
        # The original upstream receipt is committed first; replay can repair cache writes.
        AnswerStore(self.training).record(self.question, event['receipt'])
        return result

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


def public_binding(question, run, training=None):
    events = (run or {}).get('events', [])
    receipts = [e['receipt'] for e in events if 'receipt' in e]
    local = [e['local'] for e in events if 'local' in e]
    prepared = question.get('hyperskill_presentation')
    return {**question['hyperskill'], 'round_id': run['id'] if run else None,
            'prepared': bool(prepared), 'interaction': prepared['interaction'] if prepared else None,
            'local_ready': bool(training and AnswerStore(training).references(question)),
            'opened': bool(run and run.get('opened_at')), 'checks': len(receipts),
            'submissions': [{k: r[k] for k in ('submission_id', 'attempt_id', 'status', 'time', 'feedback')}
                            for r in receipts],
            'last_answer': receipts[-1]['answer_display'] if receipts else '',
            'local_checks': len(local),
            'local_last': local[-1] if local and 'local' in events[-1] else None}


class HyperskillLocalTraining(HyperskillTraining):
    """Server-only path. Local verdicts never impersonate an upstream receipt."""
    event_fields = AcademyTraining.event_fields | {'local'}

    def validate_check(self, con, run, event):
        pass

    def submit_local(self, request_id, raw):
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', request_id):
            raise DomainError('invalid_request_id', '提交标识无效。', 422)
        references = AnswerStore(self.training).references(self.question)
        if not references:
            raise DomainError('hyperskill_reference_missing', '此题尚无已验证答案，需要连接 Hyperskill 完成首次判题。', 409)
        value = parse_local(self.question, raw)
        digest = hashlib.sha256(packed(value).encode()).hexdigest()
        with closing(self.training._connect()) as con:
            attempt, saved = self._read(con)
            prior = next((e for e in (saved or {}).get('events', []) if e['id'] == request_id), None)
            if prior:
                if prior['code_hash'] != digest or 'local' not in prior:
                    raise DomainError('idempotency_conflict', '本次提交标识已用于其他答案。', 409)
                return
            if attempt['phase'] != 'answer':
                raise DomainError('hyperskill_round_closed', '本次作答已经结束。', 409)
        correct = any(json.loads(r['answer']) == value for r in references)
        reason = '依据 Hyperskill 已验证答案判题。'
        # Text and numerical problems can accept equivalent expressions. Reuse the
        # platform's existing semantic judge instead of assuming one string is exhaustive.
        kind = self.question['hyperskill']['type']
        display = display_local(kind, value)
        if not correct and kind in ('string', 'number', 'fill-blanks'):
            from .judging import judge_answer
            question = {**self.question, 'interaction': None, 'answer_type': 'reproduction',
                        'question': self.question['question'] + '\n题内结构：' + packed(self.question['hyperskill_presentation']['dataset']),
                        'reference_answer': '\n\n'.join(r['answer_display'] for r in references),
                        'constraints': '依据原题要求和已被 Hyperskill 验证通过的答案判题；接受语义等价表达，保留代码符号和大小写要求。'}
            correct, reason = judge_answer(self.training.judge, question, display)
            if correct is None:
                raise DomainError('judge_unavailable', reason, 503)
        launched = self.launch()
        self.opened(launched['round_id'], self.attempt_id)
        with closing(self.training._connect()) as con:
            _, run = self._read(con, launched['round_id'])
        event = {'id': request_id, 'sequence': len(run['events']) + 1,
                 'category': 'passed' if correct else 'wrong', 'code_hash': digest,
                 'occurred_at': utc_time(self.training.clock()).isoformat(),
                 'local': {'status': 'correct' if correct else 'wrong', 'answer_display': display,
                           'reference_submission_id': references[0]['submission_id'], 'feedback': reason}}
        AcademyTraining.check_event(self, launched['round_id'], self.attempt_id, event)


def external_training(training, attempt_id):
    with training.transaction() as con:
        question = json.loads(training._attempt(con, attempt_id)['snapshot'])
    adapter = HyperskillTraining if question.get('hyperskill') else AcademyTraining
    return adapter(training, attempt_id)
