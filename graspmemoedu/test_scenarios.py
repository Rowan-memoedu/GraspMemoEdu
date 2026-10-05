"""Private, explicitly scoped test presets. Never reachable from public routes."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .access import AccessStore
from .atomic import AtomicLearningService
from .catalog import Catalog
from .config import Settings
from .question_types import demo_answer
from .service import DomainError
from .test_materials import TestMaterials
from .training import TrainingService
from .training_content import TrainingCatalog


def prime_variant_sources(data_dir, plan):
    """Record a real preview/cancel so the next review presents an unused Practice."""
    from .cli import make_access_store
    from .dependencies import DependencyPolicy
    from .review_cards import load_paper_types
    from .review_papers import PaperService
    from .subjects import Subjects
    settings = Settings.load(data_dir)
    catalog, training = Catalog.load(settings), TrainingCatalog(data_dir)
    store = make_access_store(settings)
    subjects = Subjects(settings, catalog)
    dependencies = DependencyPolicy(catalog, store, subjects)
    count = 0
    for learner in plan['learners']:
        with closing(store._connect()) as con:
            validate_plan(data_dir, plan, con)
            access = store._public(store._account(con, learner))
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'review_variants' not in tables:
                continue
            targets = [(bank, question) for bank, question in con.execute('''SELECT DISTINCT p.container,p.source_point
                FROM review_variants v JOIN review_points p ON p.id=v.point WHERE p.kind='training' ''') if bank in plan['banks']]
        backend = TrainingService(training, data_dir, learner)
        papers = PaperService(backend, catalog, dependencies, access, load_paper_types(),
            lambda tid: AtomicLearningService(catalog.topics[tid], data_dir, learner_id=learner))
        for bank, question in targets:
            with papers.transaction() as con:
                choices, cards, _, _ = papers._choices(con, training.bank(bank)['subject_id'])
                candidate = next((item['id'] for item in choices if item['kind'] == 'single' and
                    cards[item['id']][0]['id'].startswith('training:') and cards[item['id']][0]['source']['container'] == bank
                    and cards[item['id']][0]['source']['local_points'] == [question]), None)
            if candidate:
                pid = papers.open(candidate, str(uuid.uuid4()), training.bank(bank)['subject_id'])
                papers.cancel(pid)
                count += 1
    return count


def _payload(state, **extra):
    return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
            'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}


def validate_plan(data_dir, plan, con):
    if (not isinstance(plan, dict) or set(plan) != {'schema', 'learners', 'topics', 'banks'} or plan['schema'] != 1
            or not isinstance(plan['learners'], list) or not 1 <= len(plan['learners']) <= 20
            or len(set(plan['learners'])) != len(plan['learners'])
            or not isinstance(plan['topics'], dict) or not isinstance(plan['banks'], dict)
            or not (plan['topics'] or plan['banks'])):
        raise ValueError('Invalid test scenario plan')
    catalog = Catalog.load(Settings.load(data_dir))
    policy, training = TestMaterials(data_dir, catalog), TrainingCatalog(data_dir)
    for learner in plan['learners']:
        row = con.execute('SELECT role,purpose,is_admin,is_advanced_learner,is_student,enabled FROM access_principals WHERE learner_id=?', (learner,)).fetchone()
        if not row or tuple(row) != ('account', 'test', 0, 1, 1, 1):
            raise ValueError('Presets require enabled advanced student test accounts')
    for topic, mode in plan['topics'].items():
        if topic not in policy.topics or topic not in catalog.topics or mode not in ('initial', 'due', 'future', 'paused'):
            raise ValueError('Topic must be explicitly restricted test material')
        if mode != 'initial' and catalog.topics[topic].get('training_mode') != 'atomic_retrieval':
            raise ValueError('Only atomic topics support scheduled presets')
    for bank_id, groups in plan['banks'].items():
        if bank_id not in policy.banks or not isinstance(groups, dict) or set(groups) - {'due', 'stopped', 'manual_stop'}:
            raise ValueError('Bank must be explicitly restricted test material')
        bank = training.bank(bank_id)
        known = {q['id'] for g in bank['groups'] for q in g.get('questions', [])}
        selected = []
        for items in groups.values():
            if not isinstance(items, list) or any(not isinstance(q, str) or q not in known for q in items):
                raise ValueError('Unknown test question')
            selected += items
        if len(selected) != len(set(selected)):
            raise ValueError('Conflicting test question presets')
    # A paper may contain other materials: reject mixed papers before any deletion.
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for learner in plan['learners']:
        for table in ('submissions', 'review_submissions', 'training_submissions', 'paper_submissions'):
            if table in tables and con.execute('SELECT 1 FROM ' + table + " WHERE learner=? AND status IN ('pending','running')", (learner,)).fetchone():
                raise ValueError('A test account has pending judgments')
        if 'review_papers' in tables:
            for (items,) in con.execute('SELECT items FROM review_papers WHERE learner=?', (learner,)):
                owned = [item['source']['container'] in plan['topics' if item['source']['kind'] == 'atomic' else 'banks'] for item in json.loads(items)]
                if any(owned) and not all(owned):
                    raise ValueError('A paper mixes selected tests with other materials')
    return catalog, training


def _clear_selected(con, plan):
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for learner in plan['learners']:
        if 'review_papers' in tables:
            papers = [pid for pid, raw in con.execute('SELECT id,items FROM review_papers WHERE learner=?', (learner,))
                      if any(i['source']['container'] in plan['topics' if i['source']['kind'] == 'atomic' else 'banks'] for i in json.loads(raw))]
            for pid in papers:
                for table, column in [('review_reservations', 'paper'), ('paper_submissions', 'paper'), ('review_papers', 'id')]:
                    if table in tables:
                        con.execute('DELETE FROM ' + table + ' WHERE ' + column + '=? AND learner=?', (pid, learner))
        for topic in plan['topics']:
            for table in tables:
                if table not in {'topics', 'actions', 'submissions', 'events', 'unlocks', 'practice_queues',
                                 'review_cards', 'review_ratings', 'review_sessions', 'review_actions',
                                 'review_submissions', 'review_topic_pauses', 'review_unlocks', 'review_optimizations'}:
                    continue
                columns = {r[1] for r in con.execute('PRAGMA table_info(' + table + ')')}
                if {'learner', 'topic'} <= columns:
                    con.execute('DELETE FROM ' + table + ' WHERE learner=? AND topic=?', (learner, topic))
        for bank in plan['banks']:
            if 'training_submissions' in tables:
                con.execute('''DELETE FROM training_submissions WHERE learner=? AND attempt IN
                    (SELECT id FROM training_attempts WHERE learner=? AND bank=?)''', (learner, learner, bank))
            for table in ('training_ratings', 'training_attempts', 'training_memory', 'training_suspensions'):
                if table in tables:
                    con.execute('DELETE FROM ' + table + ' WHERE learner=? AND bank=?', (learner, bank))
        if 'review_card_uses' in tables:
            for (card,) in con.execute('SELECT card FROM review_card_uses WHERE learner=?', (learner,)).fetchall():
                source = next((tid for tid in plan['topics'] if card.startswith('atomic:' + tid + ':')), None)
                source = source or next((bid for bid in plan['banks'] if card.startswith('training:' + bid + ':')), None)
                if card.startswith('variant-'):
                    row = con.execute('SELECT p.container FROM review_variants v JOIN review_points p ON p.id=v.point WHERE v.id=?', (card,)).fetchone()
                    source = bool(row and row[0] in plan['banks'])
                if source:
                    con.execute('DELETE FROM review_card_uses WHERE learner=? AND card=?', (learner, card))


class _PresetJudge:
    def __call__(self, *args, **kwargs):
        # Used only to construct explicitly synthetic initial-learning records.
        # This is not an acceptance result for the real semantic judge.
        return {'status': 'judged', 'correct': True, 'reason': '测试场景预置：按参考答案完成初学。'}


def _seed_atomic(topic, root, learner, mode, now):
    when = [now if mode == 'future' else now - timedelta(days=2)]
    backend = AtomicLearningService(topic, root, _PresetJudge(), learner, clock=lambda: when[0])
    if mode == 'initial':
        return
    state = backend.choose(_payload(backend.state(), choice='learn'))
    for _ in range(len(backend.point_chunks) * 8 + 10):
        if state['status'] == 'completed':
            break
        if state['phase'] == 'answer':
            answer = demo_answer(backend.questions[state['current']['card_id']])
            result = backend.submit(_payload(state, answer=answer))
            backend.process_submission(result['submission_id'])
            state = backend.state()
        else:
            if state['phase'] == 'waiting':
                when[0] = datetime.fromisoformat(state['next_available_at'])
            state = backend.advance(_payload(state))
    if state['status'] != 'completed':
        raise ValueError('Atomic preset did not complete')
    if mode == 'paused':
        backend.pause({'request_id': str(uuid.uuid4())})


def _wrong(question):
    spec = question.get('interaction', {})
    if spec.get('type') == 'choice':
        good = spec.get('correct_ids', [spec.get('correct_id')])
        bad = next(o['id'] for o in spec['options'] if o['id'] not in good)
        return json.dumps({'selected': [bad] if spec.get('multiple') else bad})
    if spec.get('type') == 'fill_blank' and spec.get('grading') == 'exact':
        return json.dumps({b['id']: '__测试错误答案__' for b in spec['blanks']})
    if spec.get('type') == 'text' and spec.get('grading') == 'exact':
        return '__测试错误答案__'
    raise ValueError('Due presets require a deterministic choice, blank or text question')


def apply_plan(data_dir, plan, backup, *, now=None):
    root, backup = Path(data_dir).resolve(), Path(backup).resolve()
    now = now or datetime.now(timezone.utc)
    if not backup.is_relative_to(root / 'admin') or backup.exists() or backup.suffix != '.sqlite3':
        raise ValueError('Use a new backup under the private data/admin directory')
    with closing(sqlite3.connect(root / 'learning.sqlite3')) as con:
        catalog, training = validate_plan(root, plan, con)
        backup.parent.mkdir(parents=True, exist_ok=True)
        from .account_admin import _private_permissions
        _private_permissions(backup.parent, apply=True)
        backup.touch(mode=0o600, exist_ok=False); _private_permissions(backup, apply=True)
        with closing(sqlite3.connect(backup)) as target:
            con.backup(target)
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Test preset backup failed')
        with con:
            con.execute('BEGIN IMMEDIATE')
            validate_plan(root, plan, con)
            _clear_selected(con, plan)
    for learner in plan['learners']:
        for topic, mode in plan['topics'].items():
            if catalog.topics[topic].get('training_mode') == 'atomic_retrieval':
                _seed_atomic(catalog.topics[topic], root, learner, mode, now)
        backend = TrainingService(training, root, learner, clock=lambda: now - timedelta(days=7))
        for bank_id, groups in plan['banks'].items():
            bank = training.bank(bank_id)
            questions = {q['id']: g['id'] for g in bank['groups'] for q in g.get('questions', [])}
            for mode, identifiers in groups.items():
                for qid in identifiers:
                    attempt = backend.open(bank_id, questions[qid], qid, str(uuid.uuid4()))
                    with closing(backend._connect()) as con:
                        question = json.loads(con.execute('SELECT snapshot FROM training_attempts WHERE id=?', (attempt,)).fetchone()[0])
                    if mode == 'manual_stop':
                        backend.reveal(attempt); backend.stop_reviews(attempt)
                    else:
                        answer = demo_answer(question) if mode == 'stopped' else _wrong(question)
                        sid = backend.submit({'request_id': str(uuid.uuid4()), 'attempt_id': attempt, 'answer': answer})
                        backend.process_submission(sid)
                        result = backend.state(attempt)['result']
                        if not result or result['rating'] != (5 if mode == 'stopped' else 1):
                            raise ValueError('Bank preset did not produce the expected schedule')
    return {'status': 'test_scenarios_ready', 'learners': len(plan['learners']), 'topics': len(plan['topics']),
            'banks': len(plan['banks']), 'backup': str(backup), 'synthetic_preset': True}
