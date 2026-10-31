"""SSH-only inventory and append operations for verified Hyperskill answers.

No learner services are initialized. The write connection permits INSERT only
into hyperskill_answers; grading, learning records and FSRS are untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu.hyperskill_answers import AnswerStore, canonical, key, packed, parse_local, signature

TYPES = {'choice', 'matching', 'number', 'string', 'fill-blanks', 'sorting', 'parsons', 'table'}


def source_questions(data_dir):
    for file in sorted((Path(data_dir) / 'training-banks').glob('*.json')):
        bank = json.loads(file.read_bytes())
        for group in bank['groups']:
            for question in group['questions']:
                if question.get('hyperskill'):
                    yield bank['id'], group['id'], question


def cache_rows(data_dir):
    path = Path(data_dir) / 'learning.sqlite3'
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hyperskill_answers'").fetchone():
            raise ValueError('Existing answer cache schema is required')
        return [dict(r) for r in con.execute('SELECT * FROM hyperskill_answers ORDER BY problem_key,submission_id')]


def inventory(data_dir):
    cached = cache_rows(data_dir)
    keys = {r['problem_key'] for r in cached}
    jobs, blocked, counts = {}, [], Counter()
    for bank, group, q in source_questions(data_dir):
        kind = q['hyperskill']['type']
        counts[kind] += 1
        if kind == 'code':
            continue
        if kind not in TYPES or not q.get('hyperskill_presentation'):
            blocked.append({'bank_id': bank, 'question_id': q['id'], 'step_id': q['hyperskill']['step_id'],
                            'reason': 'unsupported_type' if kind not in TYPES else 'missing_presentation'})
            continue
        ident = key(q)
        if ident not in keys:
            jobs.setdefault(ident, {'problem_key': ident, 'bank_id': bank, 'group_id': group, 'question': q})
    jobs = sorted(jobs.values(), key=lambda r: (r['question']['hyperskill']['type'], r['question']['hyperskill']['step_id']))
    return {'schema': 1, 'by_type': dict(counts), 'noncode_questions': sum(n for k, n in counts.items() if k != 'code'),
            'cached_problem_keys': sorted(keys), 'cache_rows': cached, 'pending': jobs, 'blocked': blocked,
            'pending_by_type': dict(Counter(r['question']['hyperskill']['type'] for r in jobs)),
            'grading_sha256': hashlib.sha256((ROOT / 'graspmemoedu/hyperskill_answers.py').read_bytes()).hexdigest()}


def validate_reference(question, item):
    receipt = item['receipt']
    if (item.get('source_verified') is not True or receipt.get('status') != 'correct'
            or question['hyperskill']['type'] not in TYPES or item['problem_key'] != key(question)
            or receipt.get('step_id') != question['hyperskill']['step_id']):
        raise ValueError('Reference is not bound to a verified current non-code problem')
    for field in ('submission_id', 'attempt_id'):
        if type(receipt.get(field)) is not int or receipt[field] <= 0:
            raise ValueError('Invalid source receipt identity')
    if not isinstance(receipt.get('time'), str) or not receipt['time']:
        raise ValueError('Missing source timestamp')
    kind = question['hyperskill']['type']
    if signature(kind, receipt['dataset']) != signature(kind, question['hyperskill_presentation']['dataset']):
        raise ValueError('Source dataset changed')
    if parse_local(question, item['raw_answer']) != canonical(kind, receipt['dataset'], receipt['reply']):
        raise ValueError('Website input differs from the official correct answer')


def append_verified(data_dir, items):
    if not isinstance(items, list) or not items or len(items) > 100:
        raise ValueError('Expected 1..100 verified references')
    questions = {key(q): q for _, _, q in source_questions(data_dir) if q.get('hyperskill_presentation') and q['hyperskill']['type'] in TYPES}
    for item in items:
        if item['problem_key'] not in questions:
            raise ValueError('Question content is no longer installed')
        validate_reference(questions[item['problem_key']], item)
    before = cache_rows(data_dir)
    before_by_id = {(r['problem_key'], r['submission_id']): r for r in before}
    writes = set()

    def authorizer(action, first, second, database, trigger):
        if action == sqlite3.SQLITE_INSERT and first == 'hyperskill_answers':
            writes.add(first)
            return sqlite3.SQLITE_OK
        if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION,
                      sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    class GuardedStore(AnswerStore):
        def connect(self):
            con = sqlite3.connect(self.path, timeout=30)
            con.row_factory = sqlite3.Row
            con.set_authorizer(authorizer)
            return con

    store = GuardedStore(SimpleNamespace(db_path=Path(data_dir) / 'learning.sqlite3'))
    for item in items:
        q, receipt = questions[item['problem_key']], item['receipt']
        # A conflicting old receipt is never overwritten or silently trusted.
        old = before_by_id.get((item['problem_key'], receipt['submission_id']))
        if old and json.loads(old['answer']) != parse_local(q, item['raw_answer']):
            raise ValueError('Existing reference conflicts with this source receipt')
        store.record(q, receipt)
    after = cache_rows(data_dir)
    by_id = {(r['problem_key'], r['submission_id']): r for r in after}
    if any(by_id.get(ident) != old for ident, old in before_by_id.items()):
        raise ValueError('Existing cached row changed')
    for item in items:
        row = by_id[(item['problem_key'], item['receipt']['submission_id'])]
        if json.loads(row['answer']) != parse_local(questions[item['problem_key']], item['raw_answer']):
            raise ValueError('Cache readback mismatch')
    added = [{'problem_key': item['problem_key'], 'submission_id': item['receipt']['submission_id']}
             for item in items if (item['problem_key'], item['receipt']['submission_id']) not in before_by_id]
    return {'verified': len(items), 'added': added, 'write_tables': sorted(writes),
            'cache_rows_before': len(before), 'cache_rows_after': len(after), 'learner_history_written': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inventory', 'append'])
    parser.add_argument('--data-dir', type=Path, default=Path('/var/lib/graspmemoedu'))
    args = parser.parse_args()
    result = inventory(args.data_dir) if args.action == 'inventory' else append_verified(args.data_dir, json.load(sys.stdin)['references'])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
