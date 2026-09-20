"""Thin adapter over upstream py-fsrs; no copied or modified FSRS formulas."""

from datetime import datetime, timezone
from importlib.metadata import version
import uuid

from fsrs import Card, Rating, Scheduler

from .service import DomainError


def utc_time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise DomainError('invalid_review_time', '复习时间必须包含时区。', 422)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainError('invalid_review_time', '复习时间必须包含时区。', 422)
    return value.astimezone(timezone.utc)


class FSRSEngine:
    def __init__(self, scheduler=None):
        self.scheduler = scheduler or Scheduler()
        self.algorithm = 'py-fsrs/' + version('fsrs')

    def schedule(self, card_json, mastery, completed_at, duration_ms=None):
        if type(mastery) is not int or mastery not in (1, 2, 3, 4):
            raise DomainError('invalid_mastery', '只有完整模块的 1、2、3、4 评分可提交给 FSRS。', 422)
        when = utc_time(completed_at)
        card = (Card.from_json(card_json) if card_json else
                Card(card_id=uuid.uuid4().int >> 65, due=when))
        if card.last_review is not None and when < card.last_review:
            raise DomainError('out_of_order_review', '旧的模块结果不能覆盖更新的复习状态。')
        card, log = self.scheduler.review_card(card, Rating(mastery), when, duration_ms)
        return {'card_json': card.to_json(), 'review_log_json': log.to_json(),
                'due_at': card.due.isoformat(), 'scheduler_json': self.scheduler.to_json(),
                'algorithm': self.algorithm}
