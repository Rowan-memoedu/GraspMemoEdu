"""Bounded Hyperskill conversion: theory IDs stay Topics; problems become bank cards.

Run installation during the ordinary stopped-service release window. The shared
importer owns the activation pointer. This hook journals old rows and copies
existing cards without grading them, with transaction and file rollback on failure.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .content_bundle import POINTER
from .ordered_learning import OrderedLearningService
from .topic_install import encoded
from .training import TrainingService, packed
from .training_content import TrainingCatalog, fingerprint, validate, permission_id
from .review_cards import register_point


def merge_bank(previous, incoming):
    if previous is None:
        return deepcopy(incoming)
    for key in ('id', 'title', 'subject_id', 'description', 'schema'):
        if previous.get(key) != incoming.get(key):
            raise ValueError('Existing Hyperskill bank metadata must be preserved')
    result = deepcopy(previous)
    groups = {g['id']: g for g in result['groups']}
    for group in incoming['groups']:
        old = groups.get(group['id'])
        if old is None:
            result['groups'].append(deepcopy(group))
            continue
        if {k: v for k, v in old.items() if k != 'questions'} != {k: v for k, v in group.items() if k != 'questions'}:
            raise ValueError('Existing Hyperskill group hierarchy must be preserved')
        known = {q['id']: q for q in old['questions']}
        for q in group['questions']:
            if q['id'] in known and known[q['id']] != q:
                raise ValueError('An existing problem cannot be overwritten by an import')
            if q['id'] not in known:
                old['questions'].append(deepcopy(q))
    result['math_css'] = '\n'.join(dict.fromkeys([previous.get('math_css', ''), incoming.get('math_css', '')]))
    result['version'] = fingerprint(result)
    return validate(result)


def verify_database_transition(before_path, after_path, topic_ids, bank):
    """Release check: existing unrelated rows must remain byte-for-byte identical."""
    def read(path):
        with closing(sqlite3.connect(path)) as con:
            con.row_factory = sqlite3.Row
            return {name: [dict(r) for r in con.execute('SELECT * FROM "' + name.replace('"', '""') + '"')]
                    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}
    before, after = read(before_path), read(after_path)
    archive = {(r['learner'], r['topic'], r['version']): r['body']
               for r in after.get('hyperskill_topic_archives', [])}
    attempts = {r['id']: r for r in after.get('training_attempts', [])}
    rounds = {r['attempt']: json.loads(r['body']) for r in after.get('training_external_rounds', [])}
    allowed_new = {'hyperskill_topic_archives', 'training_memory', 'training_attempts', 'training_submissions',
                   'training_ratings', 'training_suspensions', 'training_external_rounds',
                   'training_preferences', 'review_points', 'review_variants'}
    if set(after) - set(before) - allowed_new or set(before) - set(after):
        raise ValueError('Unexpected database schema change during Hyperskill migration')
    for table, rows in before.items():
        remaining = list(after[table])
        for row in rows:
            if row in remaining:
                remaining.remove(row)
                continue
            changed = None
            if table == 'topics' and row['topic'] in topic_ids:
                if archive.get((row['learner'], row['topic'], row['version'])) != row['body']:
                    raise ValueError('Original Topic row was not preserved verbatim')
                changed = next((r for r in remaining if (r['learner'], r['topic']) == (row['learner'], row['topic'])), None)
            elif table == 'access_principals':
                permitted = {**row, 'topic_ids': packed(sorted(set(json.loads(row['topic_ids'])) | {permission_id(bank['id'])}))}
                changed = next((r for r in remaining if r == permitted), None)
            elif table == 'academy_capabilities':
                for r in remaining:
                    if (r['topic'] == permission_id(bank['id']) and r['version'] == bank['version']
                            and r['step'] in attempts and attempts[r['step']]['learner'] == row['learner']
                            and rounds.get(r['step'], {}).get('id') == row['round']
                            and all(r[k] == v for k, v in row.items() if k not in ('topic', 'version', 'step'))):
                        changed = r; break
            elif table in ('training_memory', 'training_attempts', 'training_ratings', 'training_suspensions') and row['bank'] == bank['id']:
                changed = next((r for r in remaining if r == {**row, 'version': bank['version']}), None)
            if changed is None:
                raise ValueError('Unrelated existing database row changed: ' + table)
            remaining.remove(changed)
        after[table] = remaining
    for table, rows in after.items():
        for row in rows:
            permitted = False
            if table == 'hyperskill_topic_archives':
                permitted = row in before.get('topics', []) and row['topic'] in topic_ids
            elif table in ('training_memory', 'training_attempts', 'training_ratings', 'training_suspensions'):
                permitted = row['bank'] == bank['id'] and row['version'] == bank['version']
            elif table in ('training_submissions', 'training_external_rounds'):
                permitted = attempts.get(row['attempt'], {}).get('bank') == bank['id']
            elif table == 'review_points':
                permitted = row['kind'] == 'training' and row['container'] == bank['id'] and row['version'] == bank['version']
            if not permitted:
                raise ValueError('Unexpected database row added during migration: ' + table)
    return {'unrelated_rows_unchanged': True, 'original_topic_rows_archived': len(archive)}


class HyperskillImport:
    def __init__(self, package, data_dir):
        self.package, self.root = Path(package).resolve(), Path(data_dir).resolve()
        manifest = json.loads((self.package / 'manifest.json').read_bytes())
        if 'training-banks/hyperskill-practice.json' not in manifest.get('files', {}):
            raise ValueError('The practice bank must be covered by the source manifest')
        self.incoming = validate(json.loads((self.package / 'training-banks/hyperskill-practice.json').read_bytes()))
        if self.incoming['id'] != 'hyperskill-practice':
            raise ValueError('Unexpected Hyperskill bank identity')
        self.target = self.root / 'training-banks/hyperskill-practice.json'
        self.replacements, self.counts = set(), {}

    def prepare(self, catalog):
        self.before = self.target.read_bytes() if self.target.exists() else None
        self.previous = json.loads(self.before) if self.before else None
        self.bank = merge_bank(self.previous, self.incoming)
        course = json.loads((self.package / 'catalog.json').read_bytes())['courses']
        questions = {q['id']: q for g in self.incoming['groups'] for q in g['questions']}
        for declaration in (t for c in course for t in c['topics']):
            topic = json.loads((self.package / declaration['path']).read_bytes())
            if (topic.get('source_adapter') != 'hyperskill' or topic.get('topic_format_version') != 2
                    or topic.get('topic_kind') != 'introduction'
                    or any(topic.get(k) for k in ('modules', 'memory_chunks', 'memory_points', 'flashcards'))):
                raise ValueError('Hyperskill theory import must contain only ordered Introductions')
            old = catalog.topics.get(topic['id'])
            if old is None or old == topic:
                continue
            if (old.get('source_adapter') != 'hyperskill' or old.get('topic_format_version') == 2
                    or old.get('hyperskill') != topic.get('hyperskill')):
                raise ValueError('Only the original Hyperskill atomic format can be converted')
            for card in old['flashcards']:
                q = questions.get(card['id'])
                if (q is None or any(q.get(k) != card.get(k) for k in
                        ('question', 'html', 'reference_answer', 'explanation_html', 'answer_type', 'constraints'))
                        or any(q['hyperskill'].get(k) != v for k, v in card['hyperskill'].items())
                        or len(card['point_ids']) != 1):
                    raise ValueError('Legacy problem content or identity would change during conversion')
            self.replacements.add(topic['id'])

    def unchanged(self):
        return self.previous == self.bank

    def _expand_version(self, con):
        if not self.previous or self.previous['version'] == self.bank['version']:
            return
        old, new, bank = self.previous['version'], self.bank['version'], self.bank['id']
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'review_reservations' in tables and con.execute("""SELECT 1 FROM review_reservations r
                JOIN review_points p ON p.id=r.point WHERE p.kind='training' AND p.container=? LIMIT 1""", (bank,)).fetchone():
            raise ValueError('Finish reserved bank reviews before extending content')
        if (self.root / 'training-formats' / (bank + '.json')).exists():
            raise ValueError('Preserve the existing bank formatting before extending content')
        for table in ('training_memory', 'training_attempts', 'training_ratings', 'training_suspensions'):
            con.execute(f'UPDATE {table} SET version=? WHERE bank=? AND version=?', (new, bank, old))
        if 'academy_capabilities' in tables:
            con.execute('UPDATE academy_capabilities SET version=? WHERE topic=? AND version=?',
                        (new, permission_id(bank), old))
        for row in con.execute('SELECT question,due_at FROM training_memory WHERE bank=? AND version=?', (bank, new)):
            register_point(con, 'training', bank, new, row['question'], self.bank['subject_id'], row['due_at'])
        self.counts['bank_version_extended'] = True

    def _migrate(self, con, old, loaded):
        con.execute('''CREATE TABLE IF NOT EXISTS hyperskill_topic_archives (
            learner TEXT NOT NULL, topic TEXT NOT NULL, version TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY(learner,topic,version))''')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ids = {g['id'] for g in self.bank['groups'] if g['questions']}
        if 'access_principals' in tables:
            for row in con.execute('SELECT learner_id,topic_ids,topics_default FROM access_principals').fetchall():
                allowed = set(json.loads(row['topic_ids']))
                if not row['topics_default'] and allowed & ids:
                    if not ids <= allowed:
                        raise ValueError('Partial Topic permissions need a per-bank access decision')
                    con.execute('UPDATE access_principals SET topic_ids=? WHERE learner_id=?',
                                (packed(sorted(allowed | {permission_id(self.bank['id'])})), row['learner_id']))
        for tid in sorted(self.replacements):
            if 'topics' not in tables:
                continue
            group = next(g for g in self.bank['groups'] if g['id'] == tid)
            mapping = {c['id']: c['point_ids'][0] for c in old.topics[tid]['flashcards']}
            for row in con.execute('SELECT * FROM topics WHERE topic=?', (tid,)).fetchall():
                if row['version'] != old.topics[tid]['version']:
                    raise ValueError('Restore the original Hyperskill version before migration')
                state, learner = json.loads(row['body']), row['learner']
                if state.get('pause') or state.get('pending_submission_id'):
                    raise ValueError('Resolve the active pause or pending judgment before migration')
                con.execute('INSERT INTO hyperskill_topic_archives VALUES (?,?,?,?)',
                            (learner, tid, row['version'], row['body']))
                history = {h['id']: h for h in state.get('history', [])}
                if state.get('active'):
                    history.setdefault(state['active']['id'], state['active'])
                for q in group['questions']:
                    point = state['points'].get(mapping.get(q['id']))
                    if point is None:
                        continue
                    presentations = [h for h in history.values() if h['card_id'] == q['id']]
                    revision = max(1, sum(h.get('phase') == 'feedback' for h in presentations)) if point.get('card_json') else 0
                    if point.get('card_json'):
                        con.execute('INSERT INTO training_memory VALUES (?,?,?,?,?,?,?,?)',
                            (learner, self.bank['id'], q['id'], self.bank['version'], int(point['mastered']),
                             revision, point['card_json'], point['due_at']))
                        register_point(con, 'training', self.bank['id'], self.bank['version'], q['id'],
                                       self.bank['subject_id'], point['due_at'])
                        self.counts['memories'] = self.counts.get('memories', 0) + 1
                    for active in presentations:
                        aid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'hyperskill-bank:{learner}:{tid}:{active["id"]}'))
                        run = active.get('academy_round')
                        done = active.get('phase') == 'feedback'
                        created = (run or {}).get('started_at') or active.get('shown_at') or state.get('started_at')
                        result, rating = None, None
                        if done:
                            rating = active['ratings'][mapping[q['id']]]
                            result = {'correct': active.get('correct'), 'reason': active.get('reason', ''),
                                'rating': rating, 'self_rating': None, 'scheduled': True,
                                'due_at': point.get('due_at'), 'stopped': False, 'stop_requested': False, 'practice_only': False}
                        con.execute('INSERT INTO training_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (aid, learner, self.bank['id'], group['id'], q['id'], self.bank['version'], packed(q),
                             'review' if active.get('mode') == 'review' else 'initial', revision,
                             'done' if done else 'answer', created, active.get('answered_at') if done else None,
                             packed(result) if done else None, run['id'] if done and run else None))
                        if done:
                            if not run:
                                raise ValueError('Completed Hyperskill evidence has no source round')
                            con.execute('INSERT INTO training_submissions VALUES (?,?,?,?,?,NULL,?,?)',
                                (run['id'], learner, aid, fingerprint(run), active.get('answer', ''), 'judged', active.get('reason', '')))
                            con.execute('INSERT INTO training_ratings VALUES (?,?,?,?,?,?,?,NULL)',
                                (aid, learner, self.bank['id'], q['id'], self.bank['version'], rating, active['answered_at']))
                        if run:
                            con.execute('INSERT INTO training_external_rounds VALUES (?,?)', (aid, packed(run)))
                            if 'academy_capabilities' in tables:
                                con.execute('UPDATE academy_capabilities SET topic=?,version=?,step=? WHERE learner=? AND topic=? AND round=?',
                                    (permission_id(self.bank['id']), self.bank['version'], aid, learner, tid, run['id']))
                        self.counts['attempts'] = self.counts.get('attempts', 0) + 1
                # Reading gets its own state. The original body remains verbatim in
                # the journal; seeing practice is not fabricated section completion.
                reader = object.__new__(OrderedLearningService)
                reader.course = loaded.topics[tid]
                fresh = {'revision': state['revision'] + 1, 'status': 'in_progress', 'introduction_read': False,
                         'attempts': [], 'steps': [], 'module_index': 0, 'pause': None, 'pending_submission_id': None}
                reader._new_attempt(fresh, 0)
                if state.get('started_at'):
                    fresh['started_at'] = state['started_at']
                con.execute('UPDATE topics SET version=?,body=? WHERE learner=? AND topic=?',
                            (reader.course['version'], packed(fresh), learner, tid))
                self.counts['archived_topic_rows'] = self.counts.get('archived_topic_rows', 0) + 1

    @contextmanager
    def activate(self, old, loaded):
        db = self.root / 'learning.sqlite3'
        pointer = self.root / POINTER
        previous_pointer = pointer.read_bytes() if pointer.exists() else None
        backup = self.root / 'migration-backups' / ('hyperskill-' + self.bank['version'][:16] + '.sqlite3')
        backup.parent.mkdir(exist_ok=True)
        if db.exists() and not backup.exists():
            with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
        backend = TrainingService(TrainingCatalog(self.root), self.root, 'hyperskill-migration')
        self.target.parent.mkdir(exist_ok=True)
        if self.target.is_symlink() or not self.target.resolve().is_relative_to(self.root):
            raise ValueError('Training bank destination leaves data directory')
        try:
            with backend.transaction() as con:
                self._expand_version(con)
                self._migrate(con, old, loaded)
                temporary = self.target.with_suffix('.pending')
                with temporary.open('xb') as stream:
                    stream.write(encoded(self.bank)); stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary, self.target)
                yield
        except BaseException:
            if self.before is None:
                self.target.unlink(missing_ok=True)
            else:
                self.target.write_bytes(self.before)
            if previous_pointer is None:
                pointer.unlink(missing_ok=True)
            else:
                pointer.write_bytes(previous_pointer)
            raise
