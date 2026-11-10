"""Source subquestions with server-owned progression and whole-question scheduling."""
from copy import deepcopy
import json


def validate(question):
    spec = question.get('multi_step')
    if spec is None:
        return
    from .question_types import validate_interaction
    if (not isinstance(spec, dict) or set(spec) != {'schema', 'context', 'context_html', 'steps', 'review_policy'}
            or spec['schema'] != 1 or spec['review_policy'] != 'whole'
            or not isinstance(spec['context'], str) or not isinstance(spec['context_html'], str)
            or not isinstance(spec['steps'], list) or not 2 <= len(spec['steps']) <= 32):
        raise ValueError('Invalid multi-step question')
    ids = set()
    for step in spec['steps']:
        if (not isinstance(step, dict) or set(step) != {'id', 'label', 'question', 'html', 'reference_answer', 'explanation_html', 'interaction'}
                or not isinstance(step['id'], str) or not step['id'].isascii() or not step['id'].replace('-', '').isalnum()
                or step['id'] in ids or any(not isinstance(step[k], str) or not step[k].strip() or len(step[k]) > 1000000
                    for k in ('label', 'question', 'html', 'reference_answer', 'explanation_html'))
                or not isinstance(step['interaction'], dict) or step['interaction'].get('type') not in ('text', 'fill_blank')):
            raise ValueError('Invalid multi-step subquestion')
        ids.add(step['id'])
        validate_interaction(step['interaction'], step['question'])


def step_question(question, index, progress=()):
    spec = question['multi_step']
    step = spec['steps'][index]
    context = spec['context']
    # A self-rated step has no written answer; its already revealed reference is
    # context for the next judge. Future questions/keys never enter this context.
    for row in progress:
        if row['correct']:
            previous = spec['steps'][row['index']]
            answer = previous['reference_answer'] if row.get('self_rating') else row['answer_display']
            context += '\n' + previous['question'] + '\n已完成的作答：' + answer
    return {**step, 'question': context + '\n' + step['question'],
            'constraints': question.get('constraints', '按题意作答。'), 'answer_type': 'text'}


def require_step(question, progress, step_id):
    from .service import DomainError
    if complete(question, progress) or step_id != question['multi_step']['steps'][len(progress)]['id']:
        raise DomainError('stale_step', '当前小问已变化，请刷新后继续。', 409)
    return step_question(question, len(progress), progress)


def append_result(question, progress, index, answer, correct, reason, elapsed_ms=0, *, self_rating=None):
    from .question_types import display_answer
    if index != len(progress) or any(not p['correct'] for p in progress):
        raise ValueError('Multi-step position changed')
    step = question['multi_step']['steps'][index]
    return [*progress, {'index': index, 'step_id': step['id'], 'answer': answer,
        'answer_display': ('自主判定：做对' if correct else '自主判定：做错') if self_rating is not None else display_answer(step, answer),
        'correct': correct, 'reason': reason, 'elapsed_ms': elapsed_ms or 0,
        **({'self_rating': self_rating} if self_rating is not None else {})}]


def completion_rating(progress):
    """A completed whole question is either wrong (1) or fully mastered (5)."""
    return 1 if any(not p['correct'] for p in progress) else 5


def complete(question, progress):
    return bool(progress and (not progress[-1]['correct'] or len(progress) == len(question['multi_step']['steps'])))


def review_interface(question, progress):
    return {'version': 1, 'mode': 'whole', 'start_step': 0, 'context_step_ids': [],
            'first_failed_step_id': next((p['step_id'] for p in progress if not p['correct']), None),
            'step_ids': [s['id'] for s in question['multi_step']['steps']]}


def public_state(question, progress=(), *, revealed=False):
    from .question_types import public_interaction
    from .reference_answers import reference_fields
    spec = question['multi_step']
    index = min(len(progress), len(spec['steps']) - 1)
    finished = complete(question, progress)
    if finished:
        index = progress[-1]['index']
    shown = len(spec['steps']) if revealed or finished else index + 1
    steps = []
    for position, step in enumerate(spec['steps'][:shown]):
        result = progress[position] if position < len(progress) else None
        steps.append({'id': step['id'], 'label': step['label'], 'html': step['html'],
                      'interaction': public_interaction(step), 'result': deepcopy(result)})
        if result and result.get('self_rating') is not None:
            steps[-1].update(reference_fields(step, True), explanation_html=step['explanation_html'])
    current = spec['steps'][index]
    return {'version': 1, 'context_html': spec['context_html'], 'steps': steps,
            'current_step_id': None if revealed or finished else current['id'],
            'current_index': index, 'total': len(spec['steps']), 'finished': revealed or finished,
            'review': review_interface(question, progress)}


def progress_from(con, attempt):
    row = con.execute('SELECT body FROM training_multi_step WHERE attempt=?', (attempt,)).fetchone()
    return json.loads(row[0]) if row else []


def source_block_html(question, block):
    """Exact source blocks for selection-based help; never an answer projection."""
    spec = question.get('multi_step')
    if not spec:
        return None
    if block == 'context:' + question['id']:
        return spec['context_html']
    return next((s['html'] for s in spec['steps'] if block == f"step:{s['id']}:{question['id']}"), None)


def save_progress(con, attempt, progress):
    con.execute('INSERT INTO training_multi_step VALUES (?,?) ON CONFLICT(attempt) DO UPDATE SET body=excluded.body',
                (attempt, json.dumps(progress, ensure_ascii=False)))
