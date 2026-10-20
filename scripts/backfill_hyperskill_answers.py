"""Enrich already accepted website receipts from an authenticated, private API export.

Never changes old rounds, learner answers, or scheduling. New references must
match both the recorded receipt and the exact upstream attempt dataset.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from graspmemoedu.hyperskill_answers import AnswerStore
from graspmemoedu.training_content import TrainingCatalog


def public_dataset(kind, data):
    """Project raw attempts to the same public fields used by the importer."""
    if kind == 'choice':
        return {'options': data['options'], 'is_multiple_choice': bool(data.get('is_multiple_choice'))}
    if kind == 'matching':
        return {'pairs': [{k: pair[k] for k in ('first', 'second')} for pair in data['pairs']]}
    if kind == 'fill-blanks':
        return {'components': [
            {'type': c['type'], 'text': c['text']} if c['type'] == 'text'
            else {'type': c['type'], 'options': c.get('options', [])}
            for c in data['components']]}
    if kind in ('sorting', 'parsons'):
        field = 'options' if kind == 'sorting' else 'lines'
        return {field: data[field]}
    if kind == 'table':
        return {**{k: data[k] for k in ('rows', 'columns')},
                'is_checkbox': bool(data.get('is_checkbox')), 'description': data.get('description') or ''}
    return {}


def backfill(data_dir, evidence):
    catalog = TrainingCatalog(data_dir)
    questions = {q['hyperskill']['step_id']: q for g in catalog.bank('hyperskill-practice')['groups']
                 for q in g['questions'] if q.get('hyperskill')}
    store = AnswerStore(SimpleNamespace(db_path=data_dir / 'learning.sqlite3'))
    submissions = {s['id']: s for s in evidence['submissions']}
    attempts = {a['id']: a for a in evidence['attempts']}
    added = []
    with closing(sqlite3.connect(data_dir / 'learning.sqlite3')) as con:
        receipts = [json.loads(r[0]) for r in con.execute("""SELECT json_extract(e.value,'$.receipt')
            FROM training_external_rounds r, json_each(r.body,'$.events') e
            WHERE json_extract(e.value,'$.receipt.status')='correct'""")]
    for receipt in receipts:
        source = submissions.get(receipt['submission_id'])
        question = questions.get(receipt['step_id'])
        if not source or not question or question['hyperskill']['type'] == 'code':
            continue
        attempt = attempts[source['attempt']]
        reply = json.loads(source['reply']) if isinstance(source['reply'], str) else source['reply']
        if (source['status'] != 'correct' or source['attempt'] != receipt['attempt_id']
                or source['step'] != receipt['step_id'] or source['time'] != receipt['time']
                or reply != receipt['reply'] or attempt['step'] != source['step']):
            raise ValueError('Source submission does not match the recorded website receipt')
        store.record(question, {**receipt, 'dataset': public_dataset(question['hyperskill']['type'], attempt['dataset'])})
        if any(r['submission_id'] == receipt['submission_id'] for r in store.references(question)):
            added.append(receipt['submission_id'])
    return {'verified_reference_submissions': sorted(set(added)), 'learner_history_changed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(backfill(args.data_dir, json.loads(args.evidence.read_bytes()))))
