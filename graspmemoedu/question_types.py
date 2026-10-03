"""Versioned question interactions. Answer keys never enter public metadata."""
import json
import re
import unicodedata

import regex


TYPES = {'text', 'true_false', 'fill_blank', 'matching', 'choice'}
ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,39}\Z')


def _require(condition, message='Invalid question interaction'):
    if not condition:
        raise ValueError(message)


def _string(value, maximum=1000):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and '\x00' not in value


def _keys(value, required, optional=()):
    _require(isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional))


def _items(rows, required, optional=(), maximum=20):
    _require(isinstance(rows, list) and 1 <= len(rows) <= maximum)
    ids = set()
    for item in rows:
        _keys(item, {'id', *required}, optional)
        _require(isinstance(item['id'], str) and ID.fullmatch(item['id']) and item['id'] not in ids)
        ids.add(item['id'])
    return ids


def _answer_spec(item):
    _require(isinstance(item['answers'], list) and 1 <= len(item['answers']) <= 20
             and all(_string(a) for a in item['answers']))
    if 'extract_pattern' in item:
        _require(_string(item['extract_pattern'], 500))
        try:
            compiled = regex.compile(item['extract_pattern'])
        except regex.error as exc:
            raise ValueError('Invalid extraction pattern') from exc
        _require(compiled.groups == 1, 'Extraction pattern must have exactly one capture group')
        # A full match permits authored wrappers without accepting arbitrary
        # extra text containing a correct answer. Bound pathological patterns.
        for answer in item['answers']:
            compiled.fullmatch(answer, timeout=.05)


def validate_interaction(spec, stem=''):
    if spec is None:
        return
    _require(isinstance(spec, dict) and spec.get('version') == 1 and type(spec['version']) is int)
    kind = spec.get('type')
    _require(isinstance(kind, str) and kind in TYPES)
    common = {'version', 'type'}
    if kind == 'text':
        _keys(spec, common | {'grading'}, {'answers', 'extract_pattern'})
        _require(spec['grading'] in ('exact', 'semantic'))
        if spec['grading'] == 'exact':
            _require('answers' in spec)
            _answer_spec(spec)
        else:
            _require('extract_pattern' not in spec)
            if 'answers' in spec:
                _answer_spec(spec)
    elif kind == 'choice':
        _keys(spec, common | {'options', 'correct_id'})
        ids = _items(spec['options'], {'text'}, {'html'}, maximum=12)
        _require(len(ids) >= 2 and all(_string(s['text']) for s in spec['options']))
        _require(len({s['text'].strip() for s in spec['options']}) == len(ids), 'Choice options must differ')
        _require(isinstance(spec['correct_id'], str) and spec['correct_id'] in ids)
    elif kind == 'true_false':
        _keys(spec, common | {'statements'})
        _items(spec['statements'], {'text', 'correct'}, {'html'})
        _require(all(_string(s['text']) and type(s['correct']) is bool for s in spec['statements']))
    elif kind == 'fill_blank':
        _keys(spec, common | {'grading', 'blanks'})
        _require(spec['grading'] in ('exact', 'semantic'))
        ids = _items(spec['blanks'], {'answers'}, {'label', 'extract_pattern'})
        markers = re.findall(r'\{\{blank:([A-Za-z][A-Za-z0-9_-]{0,39})\}\}', stem)
        _require(len(markers) == len(ids) and set(markers) == ids,
                 'Each blank needs exactly one {{blank:id}} marker in the question')
        _require(stem.count('{{blank:') == len(markers), 'Invalid blank marker')
        _require(not any('{{blank:' in m.group(0) for m in re.finditer(r'\$\$[\s\S]*?\$\$|\$[^$\n]+\$', stem)),
                 'Blank markers must be outside math delimiters')
        for item in spec['blanks']:
            _answer_spec(item)
            _require('label' not in item or _string(item['label']))
            _require(spec['grading'] == 'exact' or 'extract_pattern' not in item)
    else:
        _keys(spec, common | {'left', 'right', 'pairs'}, {'direction', 'fixed_side'})
        left = _items(spec['left'], {'text'}, {'html'}, maximum=40)
        right = _items(spec['right'], {'text'}, {'html'}, maximum=40)
        _require(all(_string(x['text']) for x in spec['left'] + spec['right']))
        _require(spec.get('direction', 'forward') in ('forward', 'both'))
        _require(spec.get('fixed_side') in (None, 'left', 'right'))
        pairs = spec['pairs']
        _require(isinstance(pairs, list) and 1 <= len(pairs) <= min(20, len(left), len(right)))
        for pair in pairs:
            _keys(pair, {'left', 'right'})
            _require(isinstance(pair['left'], str) and isinstance(pair['right'], str)
                     and pair['left'] in left and pair['right'] in right)
        _require(len({p['left'] for p in pairs}) == len(pairs) == len({p['right'] for p in pairs}),
                 'Matching pairs must be one-to-one')


def public_interaction(question):
    spec = question.get('interaction')
    if spec is None:
        return None
    result = {k: spec[k] for k in ('version', 'type', 'grading') if k in spec}
    if spec['type'] == 'choice':
        result['options'] = [{k: row[k] for k in ('id', 'text', 'html') if k in row} for row in spec['options']]
    elif spec['type'] == 'true_false':
        result['statements'] = [{k: row[k] for k in ('id', 'text', 'html') if k in row} for row in spec['statements']]
    elif spec['type'] == 'fill_blank':
        result['blanks'] = [{k: row[k] for k in ('id', 'label') if k in row} for row in spec['blanks']]
    elif spec['type'] == 'matching':
        result.update(left=spec['left'], right=spec['right'], row_count=len(spec['pairs']),
                      direction=spec.get('direction', 'forward'), fixed_side=spec.get('fixed_side'))
        side = result['fixed_side']
        if side:
            result['fixed'] = [p[side] for p in spec['pairs']]
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'Duplicate answer field')
        result[key] = value
    return result


def parse_answer(question, answer):
    _require(isinstance(answer, str) and bool(answer.strip()) and len(answer) <= 2000,
             'Answer must contain 1 to 2000 characters')
    spec = question.get('interaction')
    if spec is None or spec['type'] == 'text':
        return answer
    result = json.loads(answer, object_pairs_hook=_unique_object)
    kind = spec['type']
    if kind == 'choice':
        _keys(result, {'selected'})
        _require(isinstance(result['selected'], str) and result['selected'] in {s['id'] for s in spec['options']})
    elif kind in ('true_false', 'fill_blank'):
        rows = spec['statements'] if kind == 'true_false' else spec['blanks']
        _require(isinstance(result, dict) and set(result) == {s['id'] for s in rows})
        _require(all(type(v) is bool if kind == 'true_false' else _string(v, 2000) for v in result.values()))
    else:
        _require(isinstance(result, list) and len(result) == len(spec['pairs']))
        for index, pair in enumerate(result):
            _keys(pair, {'left', 'right'})
            for side in ('left', 'right'):
                _require(isinstance(pair[side], str) and pair[side] in {x['id'] for x in spec[side]})
                if spec.get('fixed_side') == side:
                    _require(pair[side] == spec['pairs'][index][side], 'Prefilled slots cannot change')
        _require(len({p['left'] for p in result}) == len(result) == len({p['right'] for p in result}),
                 'Each matching option can be used once')
    return result


def normalize(value):
    # NFC preserves mathematical distinctions such as superscript 2 versus 2.
    value = unicodedata.normalize('NFC', value)
    value = ''.join(chr(ord(c) - 0xFEE0) if '\uff01' <= c <= '\uff5e' else c for c in value)
    return ''.join(c for c in value if not c.isspace())


def exact(item, value):
    if 'extract_pattern' in item:
        match = regex.fullmatch(item['extract_pattern'], value.strip(), timeout=.05)
        if match is None or match[1] is None:
            return False
        value = match[1]
    return normalize(value) in {normalize(a) for a in item['answers']}


def _summary(results):
    return ' · '.join(f'{index + 1} {"✓" if correct else "✗"}' for index, correct in enumerate(results))


def deterministic_verdict(question, answer):
    """Return (correct, reason), or None when this question needs the LLM."""
    spec = question.get('interaction')
    if spec is None:
        return None
    value = parse_answer(question, answer)
    kind = spec['type']
    if kind in ('text', 'fill_blank') and spec['grading'] == 'semantic':
        return None
    if kind == 'text':
        result = exact(spec, value)
        return result, '✓' if result else '✗'
    if kind == 'choice':
        result = value['selected'] == spec['correct_id']
        return result, '✓' if result else '✗'
    if kind == 'true_false':
        results = [value[s['id']] == s['correct'] for s in spec['statements']]
    elif kind == 'fill_blank':
        results = [exact(s, value[s['id']]) for s in spec['blanks']]
    else:
        expected = {(p['left'], p['right']) for p in spec['pairs']}
        results = [(p['left'], p['right']) in expected for p in value]
    return all(results), _summary(results)


def semantic_arguments(question, answer):
    spec = question.get('interaction')
    stem = question['question'] + '\n作答约束：' + question['constraints']
    reference = question['reference_answer']
    if spec and spec['type'] == 'fill_blank':
        values = parse_answer(question, answer)
        reference = json.dumps({b['id']: b['answers'] for b in spec['blanks']}, ensure_ascii=False)
        answer = json.dumps(values, ensure_ascii=False)
        stem += '\n这是题干挖空题。JSON 键对应题干中的 blank 标记。所有空都语义正确才判整题正确；请在原因中逐空标明对错。答案列表表示允许的参考表达。'
    elif spec and spec.get('answers'):
        reference = '\n'.join(spec['answers'])
    return stem, reference, answer


def demo_answer(question):
    spec = question.get('interaction')
    if not spec:
        return question['reference_answer']
    kind = spec['type']
    if kind == 'text':
        # A wrapper regex need not accept the bare canonical answer. Explicit
        # demo support for such a question is optional; never invent a wrapper.
        candidates = spec.get('answers', [question['reference_answer']])
        if spec['grading'] == 'exact':
            return next((a for a in candidates if exact(spec, a)), '')
        return candidates[0]
    if kind == 'choice':
        answer = {'selected': spec['correct_id']}
    elif kind == 'true_false':
        answer = {s['id']: s['correct'] for s in spec['statements']}
    elif kind == 'fill_blank':
        answer = {s['id']: next((a for a in s['answers'] if spec['grading'] == 'semantic' or exact(s, a)), '') for s in spec['blanks']}
        if any(not a for a in answer.values()):
            return ''
    else:
        answer = spec['pairs']
    return json.dumps(answer, ensure_ascii=False, separators=(',', ':'))


def display_answer(question, answer):
    if not answer or not question.get('interaction') or question['interaction']['type'] == 'text':
        return answer
    try:
        value, spec = parse_answer(question, answer), question['interaction']
        if spec['type'] == 'choice':
            return next(s['text'] for s in spec['options'] if s['id'] == value['selected'])
        if spec['type'] == 'matching':
            labels = {side: {x['id']: x['text'] for x in spec[side]} for side in ('left', 'right')}
            arrow = '↔' if spec.get('direction') == 'both' else '→'
            return '\n'.join(f"{labels['left'][p['left']]} {arrow} {labels['right'][p['right']]}" for p in value)
        rows = spec['statements'] if spec['type'] == 'true_false' else spec['blanks']
        return '\n'.join(f"{i + 1}. {('✓' if value[s['id']] else '✗') if spec['type'] == 'true_false' else value[s['id']]}" for i, s in enumerate(rows))
    except (ValueError, TypeError, KeyError):
        return answer
