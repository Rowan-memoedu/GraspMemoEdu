"""Explicit atomic content replacement and scoped progress reset."""
from contextlib import closing, contextmanager
import json
import os
import sqlite3

from .atomic_reading_upgrade import AtomicReadingUpgrade
from .content_bundle import POINTER
from .topic_install import encoded, digest
from .topic_runtime import is_introduction

PROGRESS = {'topics', 'actions', 'submissions', 'events', 'unlocks', 'practice_queues',
            'review_cards', 'review_ratings', 'review_sessions', 'review_actions',
            'review_submissions', 'review_topic_pauses', 'review_unlocks', 'review_optimizations'}


def snapshot(con):
    con.row_factory = sqlite3.Row
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {n: [dict(r) for r in con.execute('SELECT * FROM "' + n + '"')] for n in names}


def clear_progress(con, ids):
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    slots = ','.join('?' for _ in ids)
    for table in PROGRESS & tables:
        columns = {r[1] for r in con.execute('PRAGMA table_info(' + table + ')')}
        if 'topic' not in columns:
            continue
        if table in {'submissions', 'review_submissions'} and con.execute(
                f"SELECT 1 FROM {table} WHERE topic IN ({slots}) AND status IN ('pending','running')", ids).fetchone():
            raise ValueError('Selected Topic has in-flight judgments')
    papers = []
    if 'review_papers' in tables:
        for pid, raw in con.execute('SELECT id,items FROM review_papers'):
            items = json.loads(raw)
            selected = [i['source']['kind'] == 'atomic' and i['source']['container'] in ids for i in items]
            if any(selected):
                if not all(selected):
                    raise ValueError('A review paper mixes selected Topics with other materials')
                if 'paper_submissions' in tables and con.execute(
                        "SELECT 1 FROM paper_submissions WHERE paper=? AND status IN ('pending','running')", (pid,)).fetchone():
                    raise ValueError('Selected paper has in-flight judgments')
                papers.append(pid)
    for pid in papers:
        for table, column in [('review_reservations','paper'), ('paper_submissions','paper'), ('review_papers','id')]:
            if table in tables:
                con.execute(f'DELETE FROM {table} WHERE {column}=?', (pid,))
    for table in PROGRESS & tables:
        if 'topic' in {r[1] for r in con.execute('PRAGMA table_info(' + table + ')')}:
            con.execute(f'DELETE FROM {table} WHERE topic IN ({slots})', ids)
    if 'review_card_uses' in tables:
        for learner, card in con.execute('SELECT learner,card FROM review_card_uses').fetchall():
            if any(card.startswith('atomic:' + tid + ':') for tid in ids):
                con.execute('DELETE FROM review_card_uses WHERE learner=? AND card=?', (learner,card))
    if 'review_points' in tables:
        points = [r[0] for r in con.execute(f"SELECT id FROM review_points WHERE kind='atomic' AND container IN ({slots})", ids)]
        for point in points:
            if 'review_reservations' in tables:
                con.execute('DELETE FROM review_reservations WHERE point=?', (point,))
            if 'review_variants' in tables:
                variants = [r[0] for r in con.execute('SELECT id FROM review_variants WHERE point=?', (point,))]
                for vid in variants:
                    if 'review_card_uses' in tables:
                        con.execute('DELETE FROM review_card_uses WHERE card=?', (vid,))
                con.execute('DELETE FROM review_variants WHERE point=?', (point,))
            con.execute('DELETE FROM review_points WHERE id=?', (point,))


def verify_transition(before, after, topic_ids):
    with closing(sqlite3.connect(before)) as source, closing(sqlite3.connect(':memory:')) as expected:
        source.backup(expected)
        clear_progress(expected, sorted(topic_ids))
        wanted = snapshot(expected)
    with closing(sqlite3.connect(after)) as con:
        actual = snapshot(con)
    if wanted.keys() != actual.keys() or any(sorted(map(repr,wanted[t])) != sorted(map(repr,actual[t])) for t in wanted):
        raise ValueError('Replacement changed records outside its explicit reset scope')
    return {'unrelated_rows_unchanged':True, 'selected_topic_progress_reset':True}


class AtomicTopicReplacement(AtomicReadingUpgrade):
    def prepare(self, catalog):
        courses = json.loads((self.package/'catalog.json').read_bytes())['courses']
        for d in (t for c in courses for t in c['topics']):
            incoming = json.loads((self.package/d['path']).read_bytes())
            old = catalog.topics.get(incoming['id'])
            if old is None or old == incoming:
                continue
            if old.get('modules') or not (old.get('flashcards') or is_introduction(old)) or not incoming.get('flashcards') or old['title'] != incoming['title']:
                raise ValueError('Replacement requires same-title atomic or reading-only Topics without paradigm modules')
            self.replacements.add(incoming['id'])
        self.previous = {tid:catalog.topics[tid]['version'] for tid in self.replacements}

    @contextmanager
    def activate(self, old, loaded):
        if not self.replacements:
            yield
            return
        pointer=self.root/POINTER; previous=pointer.read_bytes() if pointer.exists() else None
        with closing(sqlite3.connect(self.root/'learning.sqlite3',timeout=30)) as con:
            con.execute('BEGIN IMMEDIATE')
            before=snapshot(con)
            try:
                clear_progress(con,sorted(self.replacements))
                after=snapshot(con)
                removed={t:[r for r in before[t] if r not in after[t]] for t in before}
                removed={t:rows for t,rows in removed.items() if rows}
                receipt=encoded({'topics':sorted(self.replacements),'previous_versions':self.previous,'removed':removed})
                folder=self.root/'migration-backups/atomic-replacement'; folder.mkdir(parents=True,exist_ok=True)
                path=folder/(digest(receipt)+'.json')
                if not path.exists():
                    with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as stream:
                        stream.write(receipt); stream.flush(); os.fsync(stream.fileno())
                self.counts={'reset_topics':len(self.replacements),'removed_rows':{t:len(rs) for t,rs in removed.items()}}
                yield
                con.commit()
            except BaseException:
                con.rollback()
                if previous is None:
                    pointer.unlink(missing_ok=True)
                else:
                    temp=pointer.with_suffix('.replacement-rollback'); temp.write_bytes(previous); os.replace(temp,pointer)
                raise
