"""Persistent, fail-closed limits for the private question conversion workflow."""
from contextlib import closing, contextmanager
from datetime import date
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import sqlite3
import uuid


class BudgetStopped(RuntimeError):
    pass


DEFAULT_LIMITS = {
    'max_requests': 0, 'max_tokens': 0, 'max_output_tokens': 8192,
    'max_concurrency': 2, 'max_cost_rmb': '0',
    'input_rmb_per_million': '0', 'output_rmb_per_million': '0',
    'pricing_checked_on': '',
    'price_source': 'https://api-docs.deepseek.com/quick_start/pricing/',
    'model': 'deepseek-flash', 'thinking': True,
}


def validate_limits(limits):
    if set(limits) != set(DEFAULT_LIMITS):
        raise ValueError('Budget must specify exactly the documented fields')
    for key, low, high in [('max_requests', 0, 24), ('max_tokens', 0, 250000),
                           ('max_output_tokens', 1, 8192), ('max_concurrency', 1, 2)]:
        if type(limits[key]) is not int or not low <= limits[key] <= high:
            raise ValueError('Budget outside frozen limit: ' + key)
    if limits['model'] != 'deepseek-flash' or type(limits['thinking']) is not bool:
        raise ValueError('Unsupported frozen model configuration')
    for key in ('max_cost_rmb', 'input_rmb_per_million', 'output_rmb_per_million'):
        value = Decimal(str(limits[key]))
        if not value.is_finite() or value < 0:
            raise ValueError('Invalid nonnegative price: ' + key)
    if limits['max_requests']:
        if not limits['max_tokens'] or any(Decimal(str(limits[key])) <= 0 for key in
                ('max_cost_rmb', 'input_rmb_per_million', 'output_rmb_per_million')):
            raise ValueError('A paid run requires token, price and monetary limits')
        if limits['price_source'] != DEFAULT_LIMITS['price_source']:
            raise ValueError('Use verified official model pricing')
        age = (date.today() - date.fromisoformat(limits['pricing_checked_on'])).days
        if not 0 <= age <= 1:
            raise ValueError('Verify pricing before starting a new paid run')
    return dict(limits)


def input_token_bound(messages):
    # For plain text, one token cannot encode less than one UTF-8 byte.
    # Reserve extra for message framing; never use a characters/4 estimate.
    return len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 256 * len(messages) + 256


class ConversionBudget:
    def __init__(self, path, plan, limits=None):
        self.path = Path(path)
        self.limits = validate_limits(dict(DEFAULT_LIMITS if limits is None else limits))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = json.dumps({'plan': plan, 'limits': self.limits}, sort_keys=True, ensure_ascii=False)
        with self.transaction() as con:
            con.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT, stopped TEXT)')
            con.execute('''CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE, status TEXT,
                input_bound INTEGER, output_bound INTEGER, tokens INTEGER, cost_micros INTEGER,
                provider_id TEXT UNIQUE, usage TEXT, error TEXT)''')
            row = con.execute('SELECT body FROM config WHERE id=1').fetchone()
            if row is None:
                con.execute('INSERT INTO config VALUES (1,?,NULL)', (config,))
            elif row[0] != config:
                raise BudgetStopped('The frozen plan/budget cannot change on resume')

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as con:
            con.execute('BEGIN IMMEDIATE')
            try:
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise

    def cost(self, inputs, outputs):
        value = inputs * Decimal(str(self.limits['input_rmb_per_million']))
        value += outputs * Decimal(str(self.limits['output_rmb_per_million']))
        return int(value.to_integral_value(rounding=ROUND_CEILING))

    def stop(self, reason):
        with self.transaction() as con:
            con.execute('UPDATE config SET stopped=COALESCE(stopped,?) WHERE id=1', (reason,))

    def assert_resumable(self):
        with self.transaction() as con:
            if con.execute("SELECT 1 FROM requests WHERE status IN ('reserved','unknown') LIMIT 1").fetchone():
                raise BudgetStopped('Unsettled request exists; no automatic resend or new work')

    def reserve(self, request_key, messages):
        inputs, outputs = input_token_bound(messages), self.limits['max_output_tokens']
        reserved_tokens, cost = inputs + outputs, self.cost(inputs, outputs)
        with self.transaction() as con:
            stopped = con.execute('SELECT stopped FROM config WHERE id=1').fetchone()[0]
            if stopped:
                raise BudgetStopped(stopped)
            if con.execute('SELECT 1 FROM requests WHERE request_key=?', (request_key,)).fetchone():
                raise BudgetStopped('Request already sent; use its saved response, never resend')
            count, tokens, spent = con.execute('SELECT COUNT(*),COALESCE(SUM(tokens),0),COALESCE(SUM(cost_micros),0) FROM requests').fetchone()
            active = con.execute("SELECT COUNT(*) FROM requests WHERE status IN ('reserved','unknown')").fetchone()[0]
            money_limit = int(Decimal(str(self.limits['max_cost_rmb'])) * 1000000)
            if (count + 1 > self.limits['max_requests'] or tokens + reserved_tokens > self.limits['max_tokens']
                    or spent + cost > money_limit or active >= self.limits['max_concurrency']):
                raise BudgetStopped('Budget exhausted or concurrency limit reached; request not sent')
            request_id = uuid.uuid4().hex
            con.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (request_id, request_key, 'reserved', inputs, outputs, reserved_tokens, cost, None, None, None))
        return request_id

    def uncertain(self, request_id, reason):
        # Retain the full reservation: a timeout/cancellation is not free.
        with self.transaction() as con:
            con.execute("UPDATE requests SET status='unknown',error=? WHERE id=? AND status='reserved'", (reason, request_id))
            con.execute('UPDATE config SET stopped=COALESCE(stopped,?) WHERE id=1', ('Unsettled API request: ' + reason,))

    def settle(self, request_id, response):
        try:
            usage = response['usage']
            provider_id = response['id']
            if not isinstance(provider_id, str) or not provider_id:
                raise ValueError('Missing provider response ID')
            inputs, outputs, total = (usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
            if any(type(v) is not int or v < 0 for v in (inputs, outputs, total)) or inputs + outputs != total:
                raise ValueError('Invalid API token accounting')
            reasoning = usage.get('completion_tokens_details', {}).get('reasoning_tokens', 0)
            if type(reasoning) is not int or not 0 <= reasoning <= outputs:
                raise ValueError('Invalid reasoning token accounting')
            with self.transaction() as con:
                row = con.execute('SELECT status,input_bound,output_bound FROM requests WHERE id=?', (request_id,)).fetchone()
                if row is None or row[0] != 'reserved' or inputs > row[1] or outputs > row[2]:
                    raise ValueError('Response exceeded its reservation or was already settled')
                con.execute("UPDATE requests SET status='complete',tokens=?,cost_micros=?,provider_id=?,usage=? WHERE id=?",
                            (total, self.cost(inputs, outputs), provider_id, json.dumps(usage), request_id))
        except (KeyError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
            self.uncertain(request_id, type(exc).__name__)
            raise BudgetStopped('Incomplete or inconsistent accounting; further API calls stopped') from exc

    def summary(self):
        with self.transaction() as con:
            count, tokens, cost = con.execute('SELECT COUNT(*),COALESCE(SUM(tokens),0),COALESCE(SUM(cost_micros),0) FROM requests').fetchone()
            stopped = con.execute('SELECT stopped FROM config WHERE id=1').fetchone()[0]
        return {'requests': count, 'tokens_used_or_reserved': tokens, 'cost_rmb_upper_bound': str(Decimal(cost) / 1000000),
                'requests_remaining': self.limits['max_requests'] - count,
                'tokens_remaining': self.limits['max_tokens'] - tokens, 'stopped': stopped}
