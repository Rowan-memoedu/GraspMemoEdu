"""Convert Academy exercises to the native bank contract, preserving source bindings."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .catalog import Catalog
from .config import Settings
from .subjects import Subjects
from .topic_install import encoded, import_lock
from .training import TrainingService, packed
from .training_content import TrainingCatalog, fingerprint, validate, permission_id


def convert(course, topics, subject_id):
    bank = {'schema': 1, 'id': course['id'], 'title': course['title'], 'subject_id': subject_id,
            'description': course.get('description', ''), 'groups': [], 'math_css': ''}
    styles, seen = [], set()
    for unit in course['units']:
        group = {k: unit[k] for k in ('id', 'title', 'parent_id') if k in unit}
        group.update(questions=[], introductions=[], content_order=[])
        for tid in unit['topic_ids']:
            if tid in seen:
                raise ValueError('An Academy task must occur in exactly one source group')
            seen.add(tid)
            topic = topics[tid]
            if topic.get('math_css') not in styles:
                styles.append(topic.get('math_css', ''))
            if topic.get('topic_kind') == 'introduction':
                intros = topic['introductions']
                group['introductions'].append({'id': tid, 'title': topic['title'],
                    'text': topic.get('academy_source', {}).get('markdown') or topic['title'],
                    'html': '\n'.join(i['html'] for i in intros), 'legacy_topic_id': tid})
                kind = 'introduction'
            else:
                if not topic.get('academy') or len(topic.get('flashcards', [])) != 1:
                    raise ValueError('Native Academy banks require exactly one source exercise per task')
                q = deepcopy(topic['flashcards'][0])
                q.pop('point_ids', None)
                q.update(id=tid, title=topic['title'], legacy_topic_id=tid,
                         academy={**topic['academy'], 'version': topic['version']})
                group['questions'].append(q)
                kind = 'question'
            group['content_order'].append({'type': kind, 'id': tid})
        bank['groups'].append(group)
    if seen != {t['id'] for t in course['topics']}:
        raise ValueError('Source hierarchy does not cover the course')
    bank['math_css'] = '\n'.join(styles)
    bank['version'] = fingerprint(bank)
    return validate(bank)


def migrate_records(con, bank, topics):
    """Copy exact cards and evidence. Never grade or regenerate a migrated card."""
    con.execute('''CREATE TABLE IF NOT EXISTS training_topic_migrations
                   (bank TEXT PRIMARY KEY, version TEXT NOT NULL, completed_at TEXT NOT NULL)''')
    old = con.execute('SELECT version FROM training_topic_migrations WHERE bank=?', (bank['id'],)).fetchone()
    if old:
        if old[0] != bank['version']:
            raise ValueError('Migration already refers to a different bank version')
        return {'status': 'unchanged'}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts = dict(memories=0, attempts=0, rounds=0, capabilities=0)
    ids = {x['legacy_topic_id'] for g in bank['groups'] for x in g['questions'] + g['introductions']}
    if 'access_principals' in tables:
        for row in con.execute('SELECT learner_id,topic_ids,topics_default FROM access_principals'):
            permissions = set(json.loads(row['topic_ids']))
            if not row['topics_default'] and ids & permissions and not ids <= permissions:
                raise ValueError('Partial legacy permissions require an explicit per-bank access decision')
            if not row['topics_default'] and ids <= permissions and permission_id(bank['id']) not in permissions:
                con.execute('UPDATE access_principals SET topic_ids=? WHERE learner_id=?',
                            (packed(sorted(permissions | {permission_id(bank['id'])})), row['learner_id']))
    for group in bank['groups']:
        for q in group['questions']:
            tid = q['legacy_topic_id']
            rows = con.execute('SELECT learner,version,body FROM topics WHERE topic=?', (tid,)).fetchall() if 'topics' in tables else []
            for row in rows:
                if row['version'] != topics[tid]['version']:
                    raise ValueError('Restore the original Topic version before migrating its records')
                state, learner = json.loads(row['body']), row['learner']
                if len(state['points']) != 1:
                    raise ValueError('Expected one memory state per source exercise')
                point = next(iter(state['points'].values()))
                history = state.get('history', [])
                revision = max(1, len(history)) if point.get('card_json') else 0
                if point.get('card_json'):
                    con.execute('INSERT INTO training_memory VALUES (?,?,?,?,?,?,?,?)',
                        (learner, bank['id'], q['id'], bank['version'], int(point['mastered']),
                         revision, point['card_json'], point['due_at']))
                    counts['memories'] += 1
                presentations = {h['id']: h for h in history}
                if state.get('active'):
                    presentations.setdefault(state['active']['id'], state['active'])
                for source_id, active in presentations.items():
                    aid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'academy-bank:{bank["id"]}:{learner}:{tid}:{source_id}'))
                    run = active.get('academy_round')
                    done = active.get('phase') == 'feedback'
                    created = (run or {}).get('started_at') or active.get('shown_at') or state.get('created_at') or datetime.now(timezone.utc).isoformat()
                    result, rating = None, None
                    if done:
                        rating = next(iter(active['ratings'].values()))
                        result = {'correct': active.get('correct'), 'reason': active.get('reason', ''),
                                  'rating': rating, 'self_rating': rating if q['academy']['kind'] == 'manual' else None,
                                  'scheduled': True, 'due_at': point.get('due_at'), 'stopped': False,
                                  'stop_requested': False, 'practice_only': False}
                    con.execute('INSERT INTO training_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (aid, learner, bank['id'], group['id'], q['id'], bank['version'], packed(q),
                         'review' if revision else 'initial', revision, 'done' if done else 'answer',
                         created, active.get('answered_at') if done else None,
                         packed(result) if done else None, run['id'] if done and run else None))
                    counts['attempts'] += 1
                    if done:
                        if not run:
                            raise ValueError('Completed Academy evidence has no source round')
                        con.execute('INSERT INTO training_submissions VALUES (?,?,?,?,?,NULL,?,?)',
                            (run['id'], learner, aid, fingerprint(run), active.get('answer', ''), 'judged', active.get('reason', '')))
                        con.execute('INSERT INTO training_ratings VALUES (?,?,?,?,?,?,?,NULL)',
                            (aid, learner, bank['id'], q['id'], bank['version'], rating, active['answered_at']))
                    if run:
                        con.execute('INSERT INTO training_external_rounds VALUES (?,?)', (aid, packed(run)))
                        counts['rounds'] += 1
                        if 'academy_capabilities' in tables:
                            cur = con.execute('UPDATE academy_capabilities SET topic=?,version=?,step=? WHERE learner=? AND topic=? AND round=?',
                                (permission_id(bank['id']), bank['version'], aid, learner, tid, run['id']))
                            counts['capabilities'] += cur.rowcount
    con.execute('INSERT INTO training_topic_migrations VALUES (?,?,?)',
                (bank['id'], bank['version'], datetime.now(timezone.utc).isoformat()))
    return {'status': 'migrated', **counts}


def install(data_dir, course_id):
    data_dir = Path(data_dir).resolve()
    if (data_dir / 'cloud-authoritative.json').exists():
        raise ValueError('Do not migrate the local production rollback snapshot')
    with import_lock(data_dir):
        settings = Settings.load(data_dir)
        catalog = Catalog.load(settings)
        subjects = Subjects(settings, catalog)
        course = catalog.course(course_id)
        if course.get('placement') != 'question_bank':
            raise ValueError('Only an explicitly designated question bank may be converted')
        bank = convert(course, catalog.topics, subjects.course_subjects[course_id])
        # Reuse the test visibility boundary before exposing any native bank.
        policy = subjects.test_materials
        policy_path = settings.content_dir / 'test-materials.json'
        policy_before = policy_path.read_bytes() if policy_path.exists() else None
        policy_after = policy_before
        if course_id in policy.courses or any(t['id'] in policy.topics for t in course['topics']):
            data = json.loads(policy_before) if policy_before else {'schema': 1}
            if bank['id'] not in data.get('banks', []):
                data['banks'] = data.get('banks', []) + [bank['id']]
                policy_after = encoded(data)
        target = data_dir / 'training-banks' / (bank['id'] + '.json')
        target.parent.mkdir(exist_ok=True)
        if target.exists() and json.loads(target.read_bytes()) != bank:
            raise ValueError('Existing native bank must not be overwritten')
        db = data_dir / 'learning.sqlite3'
        if db.exists():
            backup = data_dir / 'migration-backups' / ('academy-bank-' + bank['version'][:16] + '.sqlite3')
            backup.parent.mkdir(exist_ok=True)
            if not backup.exists():
                with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(backup)) as destination:
                    source.backup(destination)
        created = False
        def publish():
            nonlocal created
            if policy_after != policy_before:
                policy_path.write_bytes(policy_after)
            if not target.exists():
                with target.open('xb') as handle:
                    created = True
                    handle.write(encoded(bank))
        try:
            if not db.exists():
                publish()
                return {'status': 'installed', 'bank_id': bank['id'], 'records': 'none'}
            backend = TrainingService(TrainingCatalog(data_dir), data_dir, 'bank-migration')
            with backend.transaction() as con:
                result = migrate_records(con, bank, catalog.topics)
                publish()
        except BaseException:
            if created:
                target.unlink()
            if policy_after != policy_before:
                if policy_before is None:
                    policy_path.unlink(missing_ok=True)
                else:
                    policy_path.write_bytes(policy_before)
            raise
        return {**result, 'bank_id': bank['id'], 'version': bank['version']}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--course-id', required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.data_dir, args.course_id), ensure_ascii=False))
