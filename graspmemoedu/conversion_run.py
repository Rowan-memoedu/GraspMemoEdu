"""Frozen conversion run and request receipts; no price or budget gate."""
from contextlib import closing
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
from pathlib import Path
import sqlite3
import uuid

class ConversionStopped(RuntimeError):
    pass


DEFAULT_CONFIG = {'model': 'deepseek-flash', 'thinking': False, 'max_output_tokens': 8192}


class ConversionRun:
    def __init__(self, path, plan, config=None):
        self.path = Path(path)
        self.concurrency = plan.get('concurrency', 1)
        if type(self.concurrency) is not int or not 1 <= self.concurrency <= 64:
            raise ValueError('Use one to 64 concurrent generation requests')
        self.limits = dict(DEFAULT_CONFIG if config is None else config)
        if (not set(DEFAULT_CONFIG) <= set(self.limits)
                or set(self.limits) - set(DEFAULT_CONFIG) - {'reasoning_effort', 'stream'}
                or self.limits['model'] != 'deepseek-flash'
                or type(self.limits['thinking']) is not bool
                or type(self.limits['max_output_tokens']) is not int
                or not 1 <= self.limits['max_output_tokens'] <= (65536 if self.limits['thinking'] else 8192)
                or ('stream' in self.limits and type(self.limits['stream']) is not bool)
                or ('reasoning_effort' in self.limits and (not self.limits['thinking']
                    or self.limits['reasoning_effort'] not in ('low', 'high', 'max')))):
            raise ValueError('Invalid frozen generation configuration')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({'plan': plan, 'config': self.limits}, sort_keys=True, ensure_ascii=False)
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, body TEXT, stopped TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, request_key TEXT UNIQUE, status TEXT, provider_id TEXT, usage TEXT, error TEXT)')
            existing = con.execute('SELECT body FROM config WHERE id=1').fetchone()
            if existing is None:
                con.execute('INSERT INTO config VALUES (1,?,NULL)', (body,))
            elif existing[0] != body:
                raise ConversionStopped('Input or configuration changed after the run was frozen')

    def assert_resumable(self):
        with closing(sqlite3.connect(self.path)) as con:
            stopped = con.execute('SELECT stopped FROM config WHERE id=1').fetchone()[0]
            if stopped:
                raise ConversionStopped(stopped)
            if con.execute("SELECT 1 FROM requests WHERE status != 'complete' LIMIT 1").fetchone():
                raise ConversionStopped('An earlier request has an uncertain outcome; do not resend automatically')

    def reserve(self, request_key, messages):
        del messages
        with closing(sqlite3.connect(self.path, timeout=30)) as con, con:
            con.execute('BEGIN IMMEDIATE')
            stopped = con.execute('SELECT stopped FROM config WHERE id=1').fetchone()[0]
            if stopped:
                raise ConversionStopped(stopped)
            if con.execute('SELECT 1 FROM requests WHERE request_key=?', (request_key,)).fetchone():
                raise ConversionStopped('Request already sent; reuse its saved response')
            if con.execute("SELECT 1 FROM requests WHERE status NOT IN ('sent','complete') LIMIT 1").fetchone():
                raise ConversionStopped('An earlier request has an uncertain outcome')
            active = con.execute("SELECT COUNT(*) FROM requests WHERE status='sent'").fetchone()[0]
            if active >= self.concurrency:
                raise ConversionStopped('Frozen concurrent request limit reached')
            request_id = uuid.uuid4().hex
            con.execute('INSERT INTO requests VALUES (?,?,?,NULL,NULL,NULL)', (request_id, request_key, 'sent'))
            return request_id

    def settle(self, request_id, response):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("UPDATE requests SET status='complete',provider_id=?,usage=? WHERE id=?",
                        (response.get('id'), json.dumps(response.get('usage')), request_id))

    def stop(self, reason):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute('UPDATE config SET stopped=COALESCE(stopped,?) WHERE id=1', (reason,))

    def uncertain(self, request_id, reason):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("UPDATE requests SET status='unknown',error=? WHERE id=? AND status='sent'", (reason, request_id))
        self.stop('Request outcome uncertain: ' + reason)

    def summary(self):
        with closing(sqlite3.connect(self.path)) as con:
            rows = con.execute('SELECT status,usage FROM requests').fetchall()
            stopped = con.execute('SELECT stopped FROM config WHERE id=1').fetchone()[0]
        usages = [json.loads(usage) for _, usage in rows if usage and json.loads(usage) is not None]
        return {'requests': len(rows), 'completed': sum(status == 'complete' for status, _ in rows),
                'input_tokens': sum(u.get('prompt_tokens', 0) for u in usages),
                'output_tokens': sum(u.get('completion_tokens', 0) for u in usages),
                'reasoning_tokens': sum(u.get('completion_tokens_details', {}).get('reasoning_tokens', 0) for u in usages),
                'missing_usage': len(rows) - len(usages), 'stopped': stopped}


def run_batches(groups, action, run):
    """Bound dispatch; on failure stop new requests and drain existing responses."""
    def guarded(group):
        try:
            return action(group)
        except BaseException as exc:
            run.stop('Generation batch failed: ' + type(exc).__name__)
            raise

    if run.concurrency == 1:
        for group in groups:
            guarded(group)
        return
    groups = iter(groups)
    with ThreadPoolExecutor(max_workers=run.concurrency, thread_name_prefix='question-conversion') as pool:
        active = set()
        try:
            for _ in range(run.concurrency):
                group = next(groups, None)
                if group is not None:
                    active.add(pool.submit(guarded, group))
            while active:
                completed, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in completed:
                    future.result()
                active.difference_update(completed)
                for _ in completed:
                    group = next(groups, None)
                    if group is not None:
                        active.add(pool.submit(guarded, group))
        except BaseException as exc:
            run.stop('Parallel dispatch stopped: ' + type(exc).__name__)
            for future in active:
                future.cancel()
            # Context-manager shutdown waits for already sent requests to finish;
            # each successful result is saved by the existing acceptance callback.
            raise
