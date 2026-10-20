"""Shared reproduction eligibility and audited self-assessment metadata.

Each caller retains its own grading/scheduling policy and transaction boundary.
"""
from .access import feature_eligible
from .question_types import is_reproduction
from .reference_answers import reference_fields


def enabled(access, con):
    if 'training_self_rating' not in access.get('features', []) or not feature_eligible(access, 'training_self_rating'):
        return False
    table = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_preferences'").fetchone()
    if not table:
        return True
    row = con.execute('SELECT self_rating_enabled FROM training_preferences WHERE learner=?', (access['learner_id'],)).fetchone()
    return not row or bool(row[0])


def reveal(question, target):
    from .service import DomainError
    if not is_reproduction(question):
        raise DomainError('self_rating_forbidden', '只有复现题支持高阶学习者自主评分。', 403)
    target['revealed'] = True


def presentation(question, target, *, visible=False):
    result = {'reproduction': is_reproduction(question), 'revealed': bool(target.get('revealed'))}
    if result['revealed']:
        result.update(explanation_html=question.get('explanation_html', ''))
    result.update(reference_fields(question, visible or result['revealed']))
    return result


def protect(item, allowed):
    item['can_self_rate'] = bool(allowed and item.pop('reproduction', False))
    if item.get('revealed') and not item['can_self_rate']:
        if item.get('phase', 'answer') == 'answer':
            for key in ('reference_answer', 'reference_answer_html', 'reference_math_css'):
                item.pop(key, None)
        if item.get('phase', 'answer') == 'answer':
            item['explanation_html'] = None
        item['revealed'] = False


def answer(question, target, payload, *, allow_stop=False):
    from .service import DomainError
    rating = payload.get('self_rating')
    if rating is None:
        if target.get('revealed'):
            raise DomainError('invalid_rating', '显示答案后请选择自主评分。', 422)
        return payload['answer']
    choices = (1, 2, 3, 4, 5) if allow_stop else (1, 2, 3, 4)
    if type(rating) is not int or rating not in choices or not target.get('revealed') or not is_reproduction(question):
        raise DomainError('self_rating_forbidden', '请先显示复现题答案，再选择自主评分。', 403)
    target['self_assessment'] = {'submission_id': payload['request_id'], 'rating': rating}
    return payload['answer']


def verdict(target, submission_id):
    saved = target.get('self_assessment') or {}
    if saved.get('submission_id') != submission_id:
        return None
    return saved['rating'] > 1, ('已停止后续推送' if saved['rating'] == 5 else '自主评分：' + str(saved['rating']))
