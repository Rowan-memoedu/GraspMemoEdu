"""Optional item-to-point evidence; one scheduling decision per point/attempt.

The containing card retains its whole-answer verdict. More specific item evidence
wins for mapped points; several items mapped to one point must all pass. Old cards
without this optional contract keep their existing whole-card policy.
"""
import json

from .question_types import exact, _unique_object


def item_keys(question):
    spec = question.get('interaction') or {}
    if spec.get('grading') in ('semantic', 'unavailable') or not spec:
        return set()
    kind = spec.get('type')
    field = {'fill_blank': 'blanks', 'true_false': 'statements', 'composite': 'controls'}.get(kind)
    if field:
        return {row['id'] for row in spec[field]}
    if kind == 'matching':
        return {row['left'] for row in spec['pairs']}
    return {'answer'} if kind in ('text', 'choice') else set()


def validate_assessment(card):
    mapping = card.get('assessment')
    if mapping is None:
        return
    if (not isinstance(mapping, dict) or set(mapping) != {'version', 'item_points'}
            or type(mapping['version']) is not int or mapping['version'] != 1
            or not isinstance(mapping['item_points'], dict) or not mapping['item_points']):
        raise ValueError('Invalid item assessment contract')
    keys, points = item_keys(card), set(card['point_ids'])
    for key, targets in mapping['item_points'].items():
        if (key not in keys or not isinstance(targets, list) or not targets
                or any(not isinstance(p, str) for p in targets)
                or len(set(targets)) != len(targets) or not set(targets) <= points):
            raise ValueError('Item assessment needs known deterministic items and card points')


def item_results(question, answer):
    """Read submitted evidence, including incomplete drafts at a paper deadline.

    Request validation remains owned by the existing input adapters. Missing
    values are false; answered items in an incomplete timed paper stay useful.
    """
    spec = question['interaction']
    kind = spec['type']
    try:
        value = (answer if kind == 'text' else
                 json.loads(answer, object_pairs_hook=_unique_object))
    except (ValueError, TypeError):
        value = {}
    if kind == 'text':
        return {'answer': isinstance(value, str) and bool(value.strip()) and exact(spec, value)}
    if kind == 'matching':
        pairs = value if isinstance(value, list) else []
        left, right = {r['id'] for r in spec['left']}, {r['id'] for r in spec['right']}
        valid = (len(pairs) <= len(spec['pairs']) and all(
            isinstance(pair, dict) and set(pair) == {'left', 'right'}
            and isinstance(pair['left'], str) and pair['left'] in left
            and isinstance(pair['right'], str) and pair['right'] in right for pair in pairs))
        if valid:
            valid = len({p['left'] for p in pairs}) == len({p['right'] for p in pairs}) == len(pairs)
        return {p['left']: valid and p in pairs for p in spec['pairs']}
    if not isinstance(value, dict):
        value = {}
    if kind == 'choice':
        selected = value.get('selected')
        correct = (isinstance(selected, list) and all(isinstance(v, str) for v in selected)
                   and len(selected) == len(set(selected)) and set(selected) == set(spec['correct_ids'])
                   if spec.get('multiple') else selected == spec['correct_id'])
        return {'answer': correct}
    if kind == 'true_false':
        return {row['id']: type(value.get(row['id'])) is bool and value[row['id']] == row['correct']
                for row in spec['statements']}
    rows = spec['controls'] if kind == 'composite' else spec['blanks']
    if kind == 'composite':
        value = value.get('values', {})
        if not isinstance(value, dict):
            value = {}
    return {row['id']: isinstance(value.get(row['id']), str) and bool(value[row['id']].strip())
            and exact(row, value[row['id']]) for row in rows}


def assess_points(card, answer, correct, *, self_assessed=False):
    decisions = {point: correct for point in card['point_ids']}
    if not card.get('assessment') or self_assessed:
        return decisions, None
    results = item_results(card, answer)
    mapping = card['assessment']['item_points']
    evidence = {point: [key for key, points in mapping.items() if point in points]
                for point in card['point_ids']}
    for point, keys in evidence.items():
        if keys:
            decisions[point] = all(results[key] for key in keys)
    return decisions, {'items': [{'id': key, 'correct': results[key], 'point_ids': points}
                                for key, points in mapping.items()],
                       'points': [{'id': point, 'correct': decisions[point],
                                   'basis': 'items' if keys else 'whole', 'item_ids': keys}
                                  for point, keys in evidence.items()]}
