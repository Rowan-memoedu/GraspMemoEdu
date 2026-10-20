"""Explicit, reversible replacement of reading-only Topics by atomic lessons."""
from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import sqlite3

from .content_bundle import POINTER
from .topic_runtime import is_introduction
from .topic_install import encoded, digest


def verify_transition(before, after, topic_ids):
    def rows(path):
        with closing(sqlite3.connect(path)) as con:
            con.row_factory = sqlite3.Row
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {t: [dict(r) for r in con.execute('SELECT * FROM "' + t.replace('"', '""') + '"')] for t in tables}
    old, new = rows(before), rows(after)
    if old.keys() != new.keys():
        raise ValueError('Reading upgrade must not change the database schema')
    removed = 0
    for table in old:
        expected = old[table]
        if table == 'topics':
            removed = sum(r['topic'] in topic_ids for r in expected)
            expected = [r for r in expected if r['topic'] not in topic_ids]
        if sorted(map(repr, expected)) != sorted(map(repr, new[table])):
            raise ValueError('Unrelated database records changed: ' + table)
    return {'unrelated_rows_unchanged': True, 'cleared_reading_rows': removed}


class AtomicReadingUpgrade:
    def __init__(self, package, data_dir):
        self.package, self.root = Path(package), Path(data_dir)
        self.replacements, self.counts = set(), {}

    def prepare(self, catalog):
        courses = json.loads((self.package / 'catalog.json').read_bytes())['courses']
        for declaration in (t for c in courses for t in c['topics']):
            incoming = json.loads((self.package / declaration['path']).read_bytes())
            previous = catalog.topics.get(incoming['id'])
            if previous is None or previous == incoming:
                continue
            if (not is_introduction(previous) or any(previous.get(k) for k in ('modules', 'flashcards', 'memory_points', 'memory_chunks'))
                    or not incoming.get('flashcards') or incoming.get('title') != previous['title']):
                raise ValueError('Only an explicit, same-title reading-only Topic can be upgraded')
            self.replacements.add(incoming['id'])
        self.previous = {tid: catalog.topics[tid]['version'] for tid in self.replacements}

    def unchanged(self):
        return not self.replacements

    @contextmanager
    def activate(self, old, loaded):
        if not self.replacements:
            yield
            return
        pointer = self.root / POINTER
        before_pointer = pointer.read_bytes() if pointer.exists() else None
        with closing(sqlite3.connect(self.root / 'learning.sqlite3', timeout=30)) as con:
            con.row_factory = sqlite3.Row
            con.execute('BEGIN IMMEDIATE')
            records = []
            for tid in sorted(self.replacements):
                for row in con.execute('SELECT * FROM topics WHERE topic=?', (tid,)).fetchall():
                    state = json.loads(row['body'])
                    if (row['version'] != self.previous[tid] or state.get('atomic') or state.get('points')
                            or state.get('pause') or state.get('pending_submission_id')):
                        raise ValueError('Only pure, idle reading progress can be cleared')
                    records.append(dict(row))
            history = self.root / 'migration-backups' / 'atomic-reading'; history.mkdir(parents=True, exist_ok=True)
            receipt = encoded({'topics': sorted(self.replacements), 'rows': records})
            target = history / (digest(receipt) + '.json')
            if target.exists():
                if target.read_bytes() != receipt:
                    raise ValueError('Reading recovery archive changed')
            else:
                descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(receipt); stream.flush(); os.fsync(stream.fileno())
            try:
                for tid in sorted(self.replacements):
                    con.execute('DELETE FROM topics WHERE topic=? AND version=?', (tid, self.previous[tid]))
                self.counts = {'cleared_reading_rows': len(records)}
                yield
                con.commit()
            except BaseException:
                con.rollback()
                if before_pointer is None:
                    pointer.unlink(missing_ok=True)
                else:
                    pending = pointer.with_suffix('.atomic-reading-rollback')
                    pending.write_bytes(before_pointer); os.replace(pending, pointer)
                raise
