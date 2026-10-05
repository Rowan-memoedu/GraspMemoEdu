"""Shared point identities and presentation selection; no scheduling formula."""
import hashlib
import json
from pathlib import Path
import re

from .service import DomainError


def point_id(kind, container, version, source_point):
    value = json.dumps([kind, container, version, source_point], ensure_ascii=False)
    return 'point-' + hashlib.sha256(value.encode()).hexdigest()[:40]


def initialize_points(con):
    # execute, not executescript: callers may already be in a learning transaction.
    con.execute('''CREATE TABLE IF NOT EXISTS review_points (
        id TEXT PRIMARY KEY, subject TEXT NOT NULL, kind TEXT NOT NULL,
        container TEXT NOT NULL, version TEXT NOT NULL, source_point TEXT NOT NULL,
        created_at TEXT NOT NULL, UNIQUE(kind,container,version,source_point))''')
    con.execute('''CREATE TABLE IF NOT EXISTS review_variants (
        id TEXT PRIMARY KEY, point TEXT NOT NULL REFERENCES review_points(id),
        snapshot TEXT NOT NULL, created_at TEXT NOT NULL)''')


def register_point(con, kind, container, version, source_point, subject, when):
    initialize_points(con)
    identifier = point_id(kind, container, version, source_point)
    con.execute('INSERT OR IGNORE INTO review_points VALUES (?,?,?,?,?,?,?)',
                (identifier, subject, kind, container, version, source_point, when))
    return identifier


def reserved_points(con, learner, now=None):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_reservations'").fetchone():
        return set()
    timed = 'metadata' in {r[1] for r in con.execute('PRAGMA table_info(review_papers)')}
    limit_clause = " OR json_extract(p.metadata, '$.timing.deadline_at') IS NOT NULL" if timed else ''
    return {r[0] for r in con.execute('''SELECT r.point FROM review_reservations r
        JOIN review_papers p ON p.id=r.paper WHERE r.learner=? AND
        (p.phase='grading' OR (p.phase='answer' AND (? IS NULL OR p.expires_at>?''' + limit_clause + ')))', (learner, now, now))}


def require_unreserved(con, learner, kind, container, version, local, *, now=None):
    if point_id(kind, container, version, local) in reserved_points(con, learner, now):
        raise DomainError('review_point_reserved', '此题正在一份复习中，请先继续或结束那份复习。', 409)


def question_kind(question):
    spec = question.get('interaction') or {}
    kind = spec.get('type', 'text')
    return 'multiple' if kind == 'choice' and spec.get('multiple') else kind


def attach_training_variant(con, point, question, when):
    """Private integration for compiled/reviewed cards; no public generation API."""
    from .training_content import validate, fingerprint
    initialize_points(con)
    row = con.execute('SELECT subject,kind FROM review_points WHERE id=?', (point,)).fetchone()
    if not row or row['kind'] != 'training':
        raise ValueError('Training point must exist before attaching a variant')
    bank = {'schema': 1, 'id': 'variant-validation', 'subject_id': row['subject'], 'title': 'Variant',
            'groups': [{'id': 'variants', 'title': 'Variants', 'questions': [question]}]}
    bank['version'] = fingerprint(bank)
    validate(bank)
    snapshot = json.dumps(question, ensure_ascii=False, sort_keys=True)
    identifier = 'variant-' + hashlib.sha256((point + snapshot).encode()).hexdigest()
    con.execute('INSERT OR IGNORE INTO review_variants VALUES (?,?,?,?)', (identifier, point, snapshot, when))
    return identifier


def eligible_presentations(cards, points, uses, *, singles=False):
    """Used by original atomic learning and unified paper/card reviews."""
    candidates = [c for c in cards if set(c['point_ids']) <= set(points)
                  and (not singles or len(c['point_ids']) == 1)]
    return sorted(candidates, key=lambda c: (-len(c['point_ids']), uses.get(c['id'], 0)))


def assemble(cards, requirements):
    """Match typed slots to distinct points, including points with several types.

    A paper combines single-point presentations. Existing multi-point cards remain
    standalone bundled presentations and retain their original whole-card verdict.
    Augmenting paths avoid a greedy choice using up the only point for a later type.
    """
    options = {}
    for card in cards:
        if len(card['point_ids']) == 1:
            options.setdefault((question_kind(card['question']), card['point_ids'][0]), card)
    slots = [kind for kind, count in requirements.items() for _ in range(count)]
    points_by_type = {kind: [p for k, p in options if k == kind] for kind in slots}
    assigned, occupied = {}, {}

    def match(slot, seen):
        for point in points_by_type[slots[slot]]:
            if point in seen:
                continue
            seen.add(point)
            old = occupied.get(point)
            if old is None or match(old, seen):
                occupied[point] = slot
                assigned[slot] = point
                return True
        return False

    for slot in sorted(range(len(slots)), key=lambda s: len(points_by_type[slots[s]])):
        if not match(slot, set()):
            return None
    return [options[slots[i], assigned[i]] for i in range(len(slots))]


TYPE_LABELS = {'单选': 'choice', '多选': 'multiple', '填空': 'fill_blank',
               '问答': 'text', '判断': 'true_false', '配对': 'matching'}
SUBJECTS = {'math', 'physics', 'english', 'chinese', 'biology', 'chemistry'}


def load_paper_types(path=None):
    path = Path(path or Path(__file__).resolve().parent.parent / 'config' / 'review-paper-types.md')
    text = path.read_text(encoding='utf-8-sig')
    result, current, fields = [], None, set()

    def finish():
        if current is None:
            return
        if (not {'ID', '名称', '限时（分钟）', '学科', '启用'} <= fields or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', current.get('id', ''))
                or current.get('subject') not in SUBJECTS or not current['requirements']
                or not 1 <= sum(current['requirements'].values()) <= 60
                or any(item['id'] == current['id'] for item in result)):
            raise ValueError('Invalid paper type: ' + current['title'])
        result.append(current.copy())

    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith('## '):
            finish()
            current, fields = {'title': line[3:].strip(), 'requirements': {}}, set()
        elif current is not None and line.strip():
            found = re.fullmatch(r'- ([^:]+):\s*(.*?)\s*', line)
            if not found or found[1] in fields:
                raise ValueError(f'Invalid paper type field at line {number}')
            key, value = found.groups()
            fields.add(key)
            if key in TYPE_LABELS:
                if not re.fullmatch(r'\d{1,2}', value):
                    raise ValueError(f'Invalid question count at line {number}')
                if int(value):
                    current['requirements'][TYPE_LABELS[key]] = int(value)
            elif key in ('ID', '学科'):
                current[{'ID': 'id', '学科': 'subject'}[key]] = value
            elif key == '名称' and value and len(value) <= 200:
                current['title'] = value
            elif key == '限时（分钟）' and re.fullmatch(r'\d{1,4}', value) and 1 <= int(value) <= 1440:
                current['time_limit_minutes'] = int(value)
            elif key == '启用' and value in ('是', '否'):
                current['enabled'] = value == '是'
            else:
                raise ValueError(f'Unknown paper type field at line {number}')
    finish()
    if not result:
        raise ValueError('No paper types configured')
    return result
