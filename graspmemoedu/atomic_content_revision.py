"""Revise atomic teaching content without resetting identities or scheduling.

The package pins the previous version. Only an unchanged learning topology can
be revised. Finished answers keep their original question snapshots; current
unanswered cards refresh at a new row revision. Historical events and all FSRS
values remain untouched. Installation runs in the stopped-service release window.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3

from .atomic_content import point_chunks, policy_for, validate_atomic
from .content_bundle import POINTER
from .topic_install import encoded, digest
from .topic_runtime import validate_runtime


def _interaction_shape(spec):
    result = {key: spec.get(key) for key in ('version', 'type', 'grading', 'multiple')}
    if spec.get('type') == 'matching':
        result.update(fixed_side=spec.get('fixed_side'), direction=spec.get('direction','forward'))
    for key in ('blanks', 'controls', 'options', 'statements', 'left', 'right', 'sections'):
        if key in spec:
            result[key] = [{name: item[name] for name in
                            ('id', 'type', 'grading', 'multiple', 'control_ids', 'complete') if name in item}
                           for item in spec[key]]
            if key == 'controls':
                for source, shape in zip(spec[key],result[key]):
                    if 'options' in source:
                        shape['options'] = [{'id': option['id']} for option in source['options']]
    return result


def validate_compatible(previous, incoming):
    """Reject structural changes; this route only revises existing teaching."""
    for topic in (previous, incoming):
        validate_runtime(topic)
        validate_atomic(topic)
        if (topic.get('modules') or topic.get('topic_format_version') != 2
                or topic.get('topic_kind') != 'lesson' or topic.get('academy')
                or topic.get('source_adapter') == 'hyperskill'):
            raise ValueError('Content revision requires a sectioned atomic lesson')
    for key in ('id', 'title', 'subject_id', 'content_order', 'training_mode'):
        if previous.get(key) != incoming.get(key):
            raise ValueError('Content revision changed learning topology: ' + key)
    if policy_for(previous) != policy_for(incoming) or point_chunks(previous) != point_chunks(incoming):
        raise ValueError('Content revision changed memory identities or scheduling policy')
    for key in ('introductions', 'memory_chunks', 'flashcards'):
        if [item['id'] for item in previous[key]] != [item['id'] for item in incoming[key]]:
            raise ValueError('Content revision changed ordered identities: ' + key)
    for old, new in zip(previous['memory_chunks'], incoming['memory_chunks']):
        if (old.get('introduction_id') != new.get('introduction_id')
                or [p['id'] for p in old['points']] != [p['id'] for p in new['points']]):
            raise ValueError('Content revision changed chunk ownership')
    for old, new in zip(previous['flashcards'], incoming['flashcards']):
        for key, default in [('point_ids', []), ('learning_phases', ['learn', 'review']),
                             ('answer_type', None), ('assessment', None)]:
            if old.get(key, default) != new.get(key, default):
                raise ValueError('Content revision changed card ownership or assessment')
        if _interaction_shape(old.get('interaction', {})) != _interaction_shape(new.get('interaction', {})):
            raise ValueError('Content revision changed answer controls')
        # Historical question snapshots resolve media through the current card
        # ID. A text-only revision must not redirect them to a different sound.
        if old.get('audio') != new.get('audio'):
            raise ValueError('Content revision must preserve the complete audio source')
    if previous['version'] == incoming['version']:
        raise ValueError('Changed content requires a new version')


def revised_state(body, previous, incoming):
    state = json.loads(body)
    memory = state.get('atomic', state)
    if (state.get('pending_submission_id') or memory.get('pending_submission_id')
            or set(memory.get('points', {})) != set(point_chunks(previous))):
        raise ValueError('Content revision requires compatible idle grading state')
    questions = {card['id']: card for card in previous['flashcards']}
    for record in [*memory.get('history', []), *([memory['active']] if memory.get('active') else [])]:
        if record['card_id'] not in questions:
            snapshot = record.get('snapshot') or {}
            origin = snapshot.get('training_source') or {}
            if (not origin or snapshot.get('id') != record['card_id']
                    or record['card_id'] != f"bank:{origin.get('bank_id')}:{origin.get('question_id')}"
                    or not record.get('point_ids') or not set(record['point_ids']) <= set(memory['points'])
                    or snapshot.get('point_ids') != record['point_ids']):
                raise ValueError('Saved question identity is outside the pinned content')
            record.setdefault('snapshot_version', previous['version'])
            continue
        if record.get('phase') == 'feedback' or 'answer' in record:
            if 'snapshot' not in record:
                record['snapshot'] = deepcopy(questions[record['card_id']])
            record.setdefault('snapshot_version', previous['version'])
    # Invalidate any old browser's pending action, without advancing a step.
    state['revision'] += 1
    if 'content_version' in state:
        state['content_version'] = incoming['content_version']
    return state


def _rows(con):
    con.row_factory = sqlite3.Row
    names = [row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {name: [dict(row) for row in con.execute('SELECT * FROM "' + name.replace('"', '""') + '"')]
            for name in names}


def verify_transition(before, after, previous, incoming):
    """Independently enforce the narrow allowed row changes at release time."""
    with closing(sqlite3.connect(before)) as con:
        old = _rows(con)
    with closing(sqlite3.connect(after)) as con:
        new = _rows(con)
    if old.keys() != new.keys():
        raise ValueError('Content revision must not change the database schema')
    count = 0
    for table, rows in old.items():
        expected = deepcopy(rows)
        if table == 'topics':
            for row in expected:
                topic_id = row['topic']
                if topic_id in previous:
                    if row['version'] != previous[topic_id]['version']:
                        raise ValueError('Progress does not match the pinned previous version')
                    row['body'] = encoded(revised_state(row['body'], previous[topic_id], incoming[topic_id])).decode()
                    row['version'] = incoming[topic_id]['version']
                    count += 1
        if sorted(map(repr, expected)) != sorted(map(repr, new[table])):
            raise ValueError('Content revision changed records outside its contract: ' + table)
    return {'learning_records_preserved': True, 'fsrs_unchanged': True,
            'unrelated_rows_unchanged': True, 'revised_topic_rows': count}


class AtomicContentRevision:
    def __init__(self, package, data_dir):
        self.package, self.root = Path(package), Path(data_dir)
        manifest = json.loads((self.package / 'manifest.json').read_bytes())
        self.pins = manifest.get('atomic_previous_versions')
        if (not isinstance(self.pins, dict) or not self.pins
                or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in self.pins.items())):
            raise ValueError('Content revision requires explicit previous-version pins')
        self.replacements, self.counts = set(), {}
        self.previous, self.incoming = {}, {}

    def prepare(self, catalog):
        courses = json.loads((self.package / 'catalog.json').read_bytes())['courses']
        seen = set()
        for declaration in (t for c in courses for t in c['topics']):
            incoming = json.loads((self.package / declaration['path']).read_bytes())
            topic_id = incoming['id']
            if topic_id not in self.pins:
                continue
            seen.add(topic_id)
            previous = catalog.topics.get(topic_id)
            if previous is None:
                raise ValueError('Pinned content revision is not installed')
            if previous == incoming:
                continue
            if previous['version'] != self.pins[topic_id]:
                raise ValueError('Pinned content changed before revision')
            validate_compatible(previous, incoming)
            self.replacements.add(topic_id)
            self.previous[topic_id], self.incoming[topic_id] = previous, incoming
        if seen != self.pins.keys():
            raise ValueError('Pinned content is missing from the revision package')

    def unchanged(self):
        return not self.replacements

    def _check_live_work(self, con, tables):
        slots = ','.join('?' for _ in self.replacements)
        ids = sorted(self.replacements)
        if 'submissions' in tables and con.execute(
                f"SELECT 1 FROM submissions WHERE topic IN ({slots}) AND status IN ('pending','running')", ids).fetchone():
            raise ValueError('Finish in-flight answers before revising their content')
        if 'review_papers' in tables:
            for row in con.execute("SELECT items FROM review_papers WHERE phase IN ('answer','grading')"):
                if any(item['source']['kind'] == 'atomic' and item['source']['container'] in self.replacements
                       for item in json.loads(row['items'])):
                    raise ValueError('Finish active review papers before revising their content')
        if 'review_reservations' in tables and 'review_points' in tables and con.execute(
                f"SELECT 1 FROM review_reservations r JOIN review_points p ON p.id=r.point "
                f"WHERE p.kind='atomic' AND p.container IN ({slots})", ids).fetchone():
            raise ValueError('Release reserved review points before revising their content')

    @contextmanager
    def activate(self, old, loaded):
        if not self.replacements:
            yield
            return
        pointer = self.root / POINTER
        previous_pointer = pointer.read_bytes() if pointer.exists() else None
        with closing(sqlite3.connect(self.root / 'learning.sqlite3', timeout=30)) as con:
            con.row_factory = sqlite3.Row
            con.execute('BEGIN IMMEDIATE')
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self._check_live_work(con, tables)
            records, updated = [], []
            if 'topics' in tables:
                for topic_id in sorted(self.replacements):
                    for row in con.execute('SELECT * FROM topics WHERE topic=?', (topic_id,)):
                        if row['version'] != self.pins[topic_id]:
                            raise ValueError('Progress does not match the pinned previous version')
                        records.append(dict(row))
                        body = revised_state(row['body'], self.previous[topic_id], self.incoming[topic_id])
                        updated.append((self.incoming[topic_id]['version'], encoded(body).decode(), row['learner'], topic_id))
            receipt = encoded({'previous_topics': self.previous, 'incoming_topics': self.incoming, 'rows': records})
            folder = self.root / 'migration-backups' / 'atomic-content-revision'
            folder.mkdir(parents=True, exist_ok=True)
            archive = folder / (digest(receipt) + '.json')
            if archive.exists():
                if archive.read_bytes() != receipt:
                    raise ValueError('Content revision recovery archive changed')
            else:
                with os.fdopen(os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as stream:
                    stream.write(receipt); stream.flush(); os.fsync(stream.fileno())
            try:
                con.executemany('UPDATE topics SET version=?,body=? WHERE learner=? AND topic=?', updated)
                self.counts = {'revised_topic_rows': len(updated), 'learning_records_preserved': True, 'fsrs_unchanged': True}
                yield
                con.commit()
            except BaseException:
                con.rollback()
                if previous_pointer is None:
                    pointer.unlink(missing_ok=True)
                else:
                    pending = pointer.with_suffix('.content-revision-rollback')
                    pending.write_bytes(previous_pointer); os.replace(pending, pointer)
                raise
