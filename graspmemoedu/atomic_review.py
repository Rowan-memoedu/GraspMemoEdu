"""One atomic grading path for Topics, papers and linked bank Flashcards."""
from copy import deepcopy


def apply_review(con, backend, question, point_ids, request_id, answer, correct, reason,
                 shown_at, elapsed_ms=None, self_rating=None, *, learning_points=(), whole_rating=None):
    state = backend._load(con)
    saved = {key: state[key] for key in ('active', 'mode', 'phase')}
    state['mode'] = 'review'
    state['active'] = {'id': request_id, 'card_id': question['id'], 'point_ids': point_ids,
        'mode': 'review', 'shown_at': shown_at, 'phase': 'answer', 'snapshot': deepcopy(question)}
    if learning_points:
        state['active']['learning_point_ids'] = list(learning_points)
    if self_rating is not None:
        state['active']['self_assessment'] = {'submission_id': request_id, 'rating': self_rating}
    if whole_rating is not None:
        if not question.get('multi_step') or whole_rating not in (1, 5) or (whole_rating == 5) != bool(correct):
            raise ValueError('Invalid completed multi-step rating')
        state['active']['whole_question_rating'] = whole_rating
    backend._grade(con, state, {'id': request_id, 'answer': answer, 'elapsed_ms': elapsed_ms}, correct, reason)
    active = dict(state['active'])
    state.update(saved)
    backend._save(con, state)
    stopped = bool(active.get('stop_requested') or active.get('stopped'))
    return {'correct': correct, 'reason': reason, 'scheduled': not stopped,
        **({'assessment_result': active['assessment_result']} if active.get('assessment_result') else {}),
        **({'stop_requested': True, 'stopped': True} if stopped else {}),
        'ratings': active['ratings'],
        'due_at': None if stopped else min((state['points'][p]['due_at'] for p in point_ids
                                          if state['points'][p]['due_at']), default=None)}
