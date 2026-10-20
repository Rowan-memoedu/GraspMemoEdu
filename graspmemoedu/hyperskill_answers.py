"""Shared verified answer references; learner histories and FSRS remain private."""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
import sqlite3

from .service import DomainError


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def enrich(training, bank_id, question):
    """Add display-only data to old attempt snapshots without rewriting history."""
    if question.get('hyperskill') and not question.get('hyperskill_presentation'):
        for group in training.catalog.bank(bank_id)['groups']:
            for candidate in group['questions']:
                if (candidate['id'] == question['id'] and candidate.get('hyperskill_presentation')
                        and all(candidate.get(k) == question.get(k) for k in
                                ('hyperskill', 'question', 'html', 'reference_answer', 'explanation_html'))):
                    question = {**question, 'hyperskill_presentation': deepcopy(candidate['hyperskill_presentation'])}
    return question


def signature(kind, dataset):
    data = deepcopy(dataset)
    if kind in ('choice', 'sorting'):
        data['options'] = sorted(data['options'])
    elif kind == 'matching':
        data = {'left': sorted(p['first'] for p in data['pairs']), 'right': sorted(p['second'] for p in data['pairs'])}
    elif kind == 'parsons':
        data['lines'] = sorted(data['lines'])
    elif kind == 'table':
        data['rows'], data['columns'] = sorted(data['rows']), sorted(data['columns'])
    elif kind == 'fill-blanks':
        for component in data['components']:
            if component['type'] == 'select':
                component['options'] = sorted(component['options'])
    return data


def key(question):
    binding, prepared = question.get('hyperskill'), question.get('hyperskill_presentation')
    if not binding or not prepared or binding['type'] == 'code':
        return None
    payload = {'step': binding['step_id'], 'type': binding['type'], 'question': question['question'],
               'dataset': signature(binding['type'], prepared['dataset'])}
    return hashlib.sha256(packed(payload).encode()).hexdigest()


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing answer')
    return html.unescape(value).replace('\r\n', '\n').strip()


def numeric(value):
    value = Decimal(text(value))
    if not value.is_finite():
        raise ValueError('Invalid number')
    return str(value.normalize())


def canonical(kind, dataset, reply):
    if kind == 'number':
        return numeric(reply['number'])
    if kind == 'string':
        return text(reply['text'])
    if kind == 'fill-blanks':
        blanks, fields = reply['blanks'], dataset['components'][1:]
        if not isinstance(blanks, list) or len(blanks) != len(fields):
            raise ValueError('Incomplete blanks')
        for value, field in zip(blanks, fields):
            if field['type'] == 'select' and value not in field['options']:
                raise ValueError('Unknown option')
        return [text(v) for v in blanks]
    if kind == 'choice':
        choices, options = reply['choices'], dataset['options']
        if (not isinstance(choices, list) or len(choices) != len(options) or any(type(v) is not bool for v in choices)
                or not any(choices) or not dataset['is_multiple_choice'] and sum(choices) != 1):
            raise ValueError('Invalid choices')
        return sorted(o for o, selected in zip(options, choices) if selected)
    if kind in ('matching', 'sorting'):
        order = reply['ordering']
        values = dataset['pairs'] if kind == 'matching' else dataset['options']
        if not isinstance(order, list) or any(type(v) is not int for v in order) or sorted(order) != list(range(len(values))):
            raise ValueError('Invalid ordering')
        return (sorted([[p['first'], values[order[i]]['second']] for i, p in enumerate(values)])
                if kind == 'matching' else [values[i] for i in order])
    if kind == 'parsons':
        lines = reply['lines']
        if (not isinstance(lines, list) or sorted(v['line_number'] for v in lines) != list(range(len(dataset['lines'])))
                or any(type(v['line_number']) is not int or type(v['level']) is not int or not 0 <= v['level'] <= 10 for v in lines)):
            raise ValueError('Invalid code lines')
        return [[dataset['lines'][v['line_number']], v['level']] for v in lines]
    if kind == 'table':
        rows = reply['choices']
        if len(rows) != len(dataset['rows']) or sorted(v['name_row'] for v in rows) != sorted(dataset['rows']):
            raise ValueError('Invalid table rows')
        result = []
        for row in rows:
            cols = row['columns']
            if (len(cols) != len(dataset['columns']) or sorted(c['name'] for c in cols) != sorted(dataset['columns'])
                    or any(type(c['answer']) is not bool for c in cols)
                    or not dataset['is_checkbox'] and sum(c['answer'] for c in cols) != 1):
                raise ValueError('Invalid table columns')
            result.append([row['name_row'], sorted(c['name'] for c in cols if c['answer'])])
        return sorted(result)
    raise ValueError('Unsupported local grading')


def learner_reply(question, raw):
    kind, data = question['hyperskill']['type'], question['hyperskill_presentation']['dataset']
    if kind == 'number':
        return {'number': raw}
    if kind == 'string':
        return {'text': raw}
    value = json.loads(raw)
    if kind == 'choice':
        selected = value['selected'] if data['is_multiple_choice'] else [value['selected']]
        if (not isinstance(selected, list) or len(set(selected)) != len(selected)
                or any(v not in [str(i) for i in range(len(data['options']))] for v in selected)):
            raise ValueError('Unknown choice')
        return {'choices': [str(i) in selected for i in range(len(data['options']))]}
    if kind == 'matching':
        if (not isinstance(value, list) or len(value) != len(data['pairs'])
                or sorted(p['left'] for p in value) != sorted(str(i) for i in range(len(data['pairs'])))):
            raise ValueError('Incomplete matching')
        return {'ordering': [int(next(p['right'] for p in value if p['left'] == str(i))) for i in range(len(data['pairs']))]}
    if kind == 'table':
        if not isinstance(value, list) or len(value) != len(data['rows']) or any(len(r) != len(data['columns']) for r in value):
            raise ValueError('Incomplete table')
        return {'choices': [{'name_row': name, 'columns': [{'name': col, 'answer': value[i][j]}
                for j, col in enumerate(data['columns'])]} for i, name in enumerate(data['rows'])]}
    return {{'fill-blanks': 'blanks', 'sorting': 'ordering', 'parsons': 'lines'}[kind]: value}


class AnswerStore:
    def __init__(self, training):
        self.path = training.db_path

    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute('''CREATE TABLE IF NOT EXISTS hyperskill_answers (
            problem_key TEXT NOT NULL, submission_id INTEGER NOT NULL, step INTEGER NOT NULL,
            answer TEXT NOT NULL, answer_display TEXT NOT NULL, verified_at TEXT NOT NULL,
            PRIMARY KEY(problem_key,submission_id))''')
        con.commit()
        return con

    def references(self, question):
        ident = key(question)
        if ident is None:
            return []
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute('SELECT * FROM hyperskill_answers WHERE problem_key=? ORDER BY submission_id', (ident,))]

    def record(self, question, receipt):
        ident = key(question)
        if ident is None or receipt['status'] != 'correct' or 'dataset' not in receipt:
            return
        kind = question['hyperskill']['type']
        if signature(kind, receipt['dataset']) != signature(kind, question['hyperskill_presentation']['dataset']):
            return
        value = canonical(kind, receipt['dataset'], receipt['reply'])
        with closing(self.connect()) as con:
            con.execute('INSERT OR IGNORE INTO hyperskill_answers VALUES (?,?,?,?,?,?)',
                        (ident, receipt['submission_id'], receipt['step_id'], packed(value),
                         receipt['answer_display'], receipt['time']))
            con.commit()


def parse_local(question, raw):
    try:
        reply = learner_reply(question, raw)
        value = canonical(question['hyperskill']['type'], question['hyperskill_presentation']['dataset'], reply)
        return value
    except (ValueError, TypeError, KeyError, IndexError, StopIteration, InvalidOperation):
        raise DomainError('invalid_answer', '请完整填写此题的答案。', 422)


def display_local(kind, value):
    from .atomic_content import plain_text
    if kind in ('number', 'string'):
        return value
    if kind == 'fill-blanks':
        return '\n'.join(f'{i + 1}. {v}' for i, v in enumerate(value))
    if kind in ('choice', 'sorting'):
        return '\n'.join(plain_text(v) for v in value)
    if kind == 'matching':
        return '\n'.join(f'{plain_text(left)} → {plain_text(right)}' for left, right in value)
    if kind == 'parsons':
        return '\n'.join('  ' * level + plain_text(line) for line, level in value)
    return '\n'.join(f'{plain_text(row)} → {", ".join(plain_text(v) for v in columns)}' for row, columns in value)
