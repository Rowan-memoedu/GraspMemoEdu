"""Server-only, demand-driven math-bank replenishment; never schedules learning."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import time

from .access import AccessStore
from .review_cards import attach_training_variant, initialize_points
from .training_content import TrainingCatalog, permission_id
from .variant_production import Producer, WORKFLOW_VERSION, approved, compile_pair, digest, validate_pair


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class VariantWorker:
    def __init__(self, data_dir, producer=None, compiler=compile_pair):
        self.root = Path(data_dir).resolve()
        if not (self.root / 'learning.sqlite3').is_file():
            raise ValueError('An existing learning database is required')
        self.producer, self.compiler = producer, compiler
        with self.transaction() as con:
            initialize_points(con)
            con.execute('CREATE INDEX IF NOT EXISTS review_variant_point ON review_variants(point)')
            con.execute('''CREATE TABLE IF NOT EXISTS review_card_uses (
                learner TEXT NOT NULL, card TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(learner,card))''')
            con.execute('''CREATE TABLE IF NOT EXISTS review_variant_jobs (
                id TEXT PRIMARY KEY, point TEXT NOT NULL REFERENCES review_points(id),
                state TEXT NOT NULL, source TEXT NOT NULL, existing TEXT NOT NULL,
                generation TEXT, review TEXT, compiled TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.root / 'learning.sqlite3', timeout=30)) as con:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('BEGIN IMMEDIATE')
            try:
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise

    def context(self):
        catalog = TrainingCatalog(self.root)
        return catalog, AccessStore(self.root, catalog.permissions())

    @staticmethod
    def demand(con, catalog, access):
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'training_memory', 'training_suspensions'} <= tables:
            return []
        rows = con.execute('''SELECT DISTINCT p.* FROM review_points p
            JOIN training_memory m ON m.bank=p.container AND m.version=p.version AND m.question=p.source_point
            JOIN access_principals a ON a.learner_id=m.learner
            WHERE p.kind='training' AND p.subject='math' AND m.due_at IS NOT NULL
            AND a.enabled=1 AND a.is_admin=0
            AND NOT EXISTS (SELECT 1 FROM training_suspensions s WHERE s.learner=m.learner
                AND s.bank=m.bank AND s.question=m.question AND s.version=m.version)
            AND NOT EXISTS (SELECT 1 FROM review_variants v WHERE v.point=p.id AND NOT EXISTS (
                SELECT 1 FROM review_card_uses u WHERE u.learner=m.learner AND u.card=v.id AND u.count>0))
            AND a.learner_id IS NOT NULL
            ORDER BY p.created_at,p.id''').fetchall()
        result = []
        for row in rows:
            bank = catalog.bank(row['container']) if row['container'] in catalog.banks else None
            if not bank or bank['subject_id'] != 'math' or bank['version'] != row['version']:
                continue
            learners = con.execute('''SELECT a.* FROM training_memory m JOIN access_principals a ON a.learner_id=m.learner
                WHERE m.bank=? AND m.version=? AND m.question=? AND m.due_at IS NOT NULL AND a.enabled=1 AND a.is_admin=0
                AND NOT EXISTS (SELECT 1 FROM training_suspensions s WHERE s.learner=m.learner AND s.bank=m.bank
                    AND s.version=m.version AND s.question=m.question)
                AND NOT EXISTS (SELECT 1 FROM review_variants v WHERE v.point=? AND NOT EXISTS (
                    SELECT 1 FROM review_card_uses u WHERE u.learner=m.learner AND u.card=v.id AND u.count>0))''',
                (row['container'], row['version'], row['source_point'], row['id'])).fetchall()
            for learner in learners:
                public = access._public(learner)
                if (permission_id(bank['id']) in {t['id'] for t in public['topics']}
                        and {'learn', 'submit_answer'} <= set(public['features'])):
                    result.append(dict(row)); break
        return result

    def discover(self):
        catalog, access = self.context()
        with self.transaction() as con:
            # Unknown network outcomes are retained for inspection, never blindly billed again.
            stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            con.execute("""UPDATE review_variant_jobs SET state='failed',error='Interrupted API call; outcome unknown',
                updated_at=? WHERE state IN ('generating','reviewing') AND updated_at<?""", (now(), stale))
            con.execute("UPDATE review_variant_jobs SET state='approved',updated_at=? WHERE state='compiling' AND updated_at<?",
                        (now(), stale))
            for point in self.demand(con, catalog, access):
                bank = catalog.bank(point['container'])
                question = next((q for g in bank['groups'] for q in g.get('questions', []) if q['id'] == point['source_point']), None)
                if question is None:
                    continue
                variants = con.execute('SELECT id,snapshot FROM review_variants WHERE point=? ORDER BY id', (point['id'],)).fetchall()
                source = {'bank_id': bank['id'], 'version': bank['version'], 'question': question}
                job_id = digest([WORKFLOW_VERSION, point['id'], digest(source), [v['id'] for v in variants]])
                con.execute('''INSERT OR IGNORE INTO review_variant_jobs
                    (id,point,state,source,existing,created_at,updated_at) VALUES (?,?,'pending',?,?,?,?)''',
                    (job_id, point['id'], encode(source), encode([json.loads(v['snapshot']) for v in variants]), now(), now()))

    def transition(self, job_id, old, new, **fields):
        if set(fields) - {'generation', 'review', 'compiled', 'error'}:
            raise ValueError('Invalid job fields')
        with self.transaction() as con:
            return con.execute('UPDATE review_variant_jobs SET state=?,updated_at=?' +
                ''.join(',' + key + '=?' for key in fields) + ' WHERE id=? AND state=?',
                (new, now(), *fields.values(), job_id, old)).rowcount == 1

    def run_one(self):
        self.discover()
        catalog, access = self.context()
        with self.transaction() as con:
            eligible = {p['id'] for p in self.demand(con, catalog, access)}
            jobs = con.execute("SELECT * FROM review_variant_jobs WHERE state IN ('pending','generated','approved') ORDER BY created_at,id").fetchall()
            job = next((dict(r) for r in jobs if r['point'] in eligible), None)
        if job is None:
            return None
        ident = job['id']; state = job['state']
        source, existing = json.loads(job['source']), json.loads(job['existing'])
        try:
            if state == 'pending':
                if not self.transition(ident, state, 'generating'): return None
                state = 'generating'
                self.producer = self.producer or Producer()
                generation = self.producer.generate(source, existing)
                # Save evidence before schema checking so failures remain inspectable.
                self.transition(ident, state, 'generated', generation=encode(generation))
                state = 'generated'; job['generation'] = encode(generation)
            rows = validate_pair(json.loads(job['generation'])['result'], source, existing)
            if state == 'generated':
                if not self.transition(ident, state, 'reviewing'): return None
                state = 'reviewing'
                self.producer = self.producer or Producer()
                review = self.producer.review(source, existing, rows)
                accepted = approved(review['result'])
                self.transition(ident, state, 'approved' if accepted else 'rejected', review=encode(review))
                if not accepted: return {'job': ident, 'state': 'rejected'}
                state = 'approved'
            if not self.transition(ident, state, 'compiling'): return None
            state = 'compiling'
            compiled = self.compiler(rows, source, ident, self.root)
            if not isinstance(compiled, list) or len(compiled) != 2:
                raise ValueError('Compiler must return two Practices')
            catalog, access = self.context()
            bank = catalog.bank(source['bank_id'])
            current = next((q for g in bank['groups'] for q in g.get('questions', []) if q['id'] == source['question']['id']), None)
            if bank['version'] != source['version'] or digest(current) != digest(source['question']):
                raise ValueError('Source changed during production')
            with self.transaction() as con:
                active = con.execute('SELECT state FROM review_variant_jobs WHERE id=?', (ident,)).fetchone()
                if active['state'] != state: return None
                if job['point'] not in {p['id'] for p in self.demand(con, catalog, access)}:
                    con.execute("UPDATE review_variant_jobs SET state='approved',updated_at=? WHERE id=?", (now(), ident))
                    return {'job': ident, 'state': 'approved'}
                latest = [json.loads(r[0]) for r in con.execute('SELECT snapshot FROM review_variants WHERE point=?', (job['point'],))]
                validate_pair({'variants': rows}, source, latest)
                ids = [attach_training_variant(con, job['point'], q, now()) for q in compiled]
                if len(set(ids)) != 2:
                    raise ValueError('Duplicate compiled Practices')
                con.execute("UPDATE review_variant_jobs SET state='installed',compiled=?,updated_at=? WHERE id=?",
                            (encode(compiled), now(), ident))
            return {'job': ident, 'state': 'installed', 'variant_ids': ids}
        except Exception as error:
            # Do not log request text, API response bodies, credentials or learner identity.
            message = str(error) if isinstance(error, ValueError) else type(error).__name__
            self.transition(ident, state, 'failed', error=message[:200])
            return {'job': ident, 'state': 'failed', 'error': message[:200]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    worker = VariantWorker(args.data_dir)
    if args.status:
        with worker.transaction() as con:
            print(encode([dict(r) for r in con.execute(
                'SELECT id,point,state,error,updated_at FROM review_variant_jobs ORDER BY created_at')]))
        return
    while True:
        try:
            result = worker.run_one()
            if result: print(encode(result), flush=True)
        except Exception as error:
            print(encode({'state': 'worker_error', 'error': type(error).__name__}), flush=True)
            if not args.watch: raise
            result = None
        if not args.watch: break
        time.sleep(2 if result else 30)


if __name__ == '__main__':
    main()
