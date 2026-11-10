"""Lossless numbered-subquestion extraction shared by all import adapters."""
import re

MATH = re.compile(r'\$\$[\s\S]*?\$\$|\$[^$]*\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|```[\s\S]*?```')
MARKER = re.compile(r'(?:^|[\n；;。])\s*(?P<label>[（(](?P<key>\d{1,2}|[a-hA-H]|[iIvVxX]{1,4})[）)])\s*')


def split_numbered(text):
    masked = MATH.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text)
    found = list(MARKER.finditer(masked))
    if len(found) < 2:
        return None
    keys = [m['key'] for m in found]
    candidates = [[str(i) for i in range(1, len(keys) + 1)],
                  [chr(97+i) for i in range(len(keys))], [chr(65+i) for i in range(len(keys))],
                  ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII'][:len(keys)],
                  ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii'][:len(keys)]]
    if keys not in candidates:
        return None
    parts = []
    for i, match in enumerate(found):
        end = found[i+1].start('label') if i+1 < len(found) else len(text)
        parts.append({'id': f'step-{i+1}', 'key': match['key'], 'label': text[match.start('label'):match.end('label')],
                      'question': text[match.end('label'):end].strip()})
    return {'context': text[:found[0].start('label')].strip(), 'parts': parts}


def derive(question, alignment=None):
    """No solving/rewording. Unaligned references require source-quoted evidence."""
    if question.get('multi_step') or question.get('academy') or question.get('hyperskill'):
        return question.get('multi_step')
    if (question.get('interaction') or {}).get('type', 'text') not in ('text', 'fill_blank'):
        return None
    split = split_numbered(question['question'])
    if not split:
        return None
    reference = question.get('reference_answer') or question.get('explanation', '')
    answers = split_numbered(reference)
    aligned = answers and [p['key'] for p in answers['parts']] == [p['key'] for p in split['parts']]
    rows = []
    for i, part in enumerate(split['parts']):
        if aligned:
            answer = '\n'.join(v for v in (answers['context'], answers['parts'][i]['question']) if v)
        else:
            row = next((r for r in (alignment or {}).get('steps', []) if r['id'] == part['id']), None)
            if not row or not row.get('answer_quotes'):
                raise ValueError('Subquestion answers need source alignment: ' + question['id'])
            for quote in row['answer_quotes']:
                if not quote.strip() or not any(quote in value for value in (reference, question.get('explanation', ''))):
                    raise ValueError('Step answer must quote the existing source exactly')
            answer = '\n'.join(row['answer_quotes'])
        prose = MATH.sub(' ', part['question'])
        extended = bool(re.search(r'证明|说明|解释|论证|讨论|作图|画出|绘出|绘制|作出.{0,40}图|prove|show\s+that|explain|justify|\b(?:draw|sketch|plot)\b', prose, re.I))
        text = part['question']
        interaction = {'version': 1, 'type': 'text', 'grading': 'semantic'}
        if not extended and len(answer) <= 1000:
            text += '\n\n{{blank:result}}'
            interaction = {'version': 1, 'type': 'fill_blank', 'grading': 'semantic',
                           'blanks': [{'id': 'result', 'answers': [answer]}]}
        rows.append({'id': part['id'], 'label': part['label'], 'question': text,
                     'reference_answer': answer, 'explanation': answer, 'interaction': interaction})
    return {'schema': 1, 'context': split['context'], 'steps': rows, 'review_policy': 'whole'}


if __name__ == '__main__':
    import json
    import sys
    bank = json.load(sys.stdin)
    rows = {}
    for group in bank['groups']:
        for question in group.get('questions', []):
            value = derive(question)
            if value:
                rows[question['id']] = {'multi_step': value}
    print(json.dumps({'questions': rows}, ensure_ascii=False))
