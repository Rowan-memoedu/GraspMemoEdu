"""Independent, immutable training banks. These never alter course Topics."""
from copy import deepcopy
from .idea_presentation import present_question
import hashlib
import json
from pathlib import Path
import re
import sys

from .catalog import Catalog
from .question_types import validate_interaction
from .service import DomainError
from .subject_registry import SUBJECT_IDS

IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
DIRECTORY = 'training-banks'


def permission_id(bank_id):
    return 'training.' + bank_id


def fingerprint(bank):
    payload = {k: v for k, v in bank.items() if k != 'version'}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate(bank):
    def require(value, message):
        if not value:
            raise ValueError(message)

    def identifier(value):
        return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value))

    def text(value, maximum=100000):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and '\x00' not in value

    require(isinstance(bank, dict) and type(bank.get('schema')) is int and bank['schema'] == 1, 'Invalid training bank schema')
    require(identifier(bank.get('id')) and text(bank.get('title'), 200), 'Invalid bank identity')
    require(bank.get('subject_id') in SUBJECT_IDS, 'Unknown subject')
    require(isinstance(bank.get('description', ''), str) and len(bank.get('description', '')) <= 4000,
            'Invalid bank description')
    require(isinstance(bank.get('math_css', ''), str), 'Invalid math style')
    require(bank.get('version') == fingerprint(bank), 'Training bank version differs from content')
    groups = bank.get('groups')
    require(isinstance(groups, list) and 1 <= len(groups) <= 200, 'Missing question groups')
    ids, questions, introductions = set(), set(), set()
    for group in groups:
        require(isinstance(group, dict) and identifier(group.get('id')) and group['id'] not in ids,
                'Duplicate or invalid group')
        ids.add(group['id'])
        require(text(group.get('title'), 200), 'Missing group title')
        intros = group.get('introductions', [])
        require(isinstance(intros, list) and len(intros) <= 200, 'Invalid group Introductions')
        for intro in intros:
            require(isinstance(intro, dict) and identifier(intro.get('id'))
                    and intro['id'] not in introductions, 'Duplicate or invalid Introduction')
            introductions.add(intro['id'])
            require(text(intro.get('title'), 200) and text(intro.get('text'))
                    and text(intro.get('html'), 1000000), 'Missing Introduction content')
        rows = group.get('questions', [])
        require(isinstance(rows, list) and len(rows) <= 500, 'Too many questions in group')
        for q in rows:
            require(isinstance(q, dict) and identifier(q.get('id')) and q['id'] not in questions,
                    'Question IDs must be unique within the bank')
            questions.add(q['id'])
            external = 'hyperskill' in q
            require(all(text(q.get(key)) for key in ('question', 'answer_type', 'constraints')
                        + (() if external else ('reference_answer',))),
                    'Question needs a stem, reference answer and explanation')
            # Offline SVG formulae and embedded PNGs are larger than source prose.
            # The complete bank still has the existing 32 MB file limit.
            require(all(text(q.get(key), 1000000) for key in (('html',) if external else ('html', 'explanation_html'))),
                    'Missing or oversized rendered question or explanation')
            require(text(q.get('title', q['id']), 200), 'Invalid question title')
            validate_interaction(q.get('interaction'), q['question'])
            if 'academy' in q:
                a = q['academy']
                require(isinstance(a, dict) and set(a) == {'task_key', 'kind', 'version'}
                        and identifier(a['task_key']) and a['kind'] in ('automated', 'manual')
                        and text(a['version'], 128), 'Invalid Academy grading binding')
            if external:
                from .hyperskill_content import SUPPORTED
                h = q['hyperskill']
                require(isinstance(h, dict) and set(h) == {'topic_id', 'theory_id', 'step_id', 'type'}
                        and all(type(h[k]) is int and h[k] > 0 for k in ('topic_id', 'theory_id', 'step_id'))
                        and h['type'] in SUPPORTED and q.get('answer_type') == 'external'
                        and q.get('reference_answer') == '' and q.get('explanation_html') == ''
                        and q.get('interaction') is None and 'academy' not in q,
                        'Invalid Hyperskill grading binding')
        if 'content_order' in group:
            order = group['content_order']
            expected = {('introduction', i['id']) for i in intros} | {('question', q['id']) for q in rows}
            require(isinstance(order, list) and all(isinstance(item, dict) and set(item) == {'type', 'id'}
                    and isinstance(item['type'], str) and isinstance(item['id'], str) for item in order),
                    'Invalid group content order')
            actual = [(item['type'], item['id']) for item in order]
            require(len(actual) == len(set(actual)) and set(actual) == expected, 'Incomplete group content order')
        elif intros:
            raise ValueError('Introductions require explicit source content order')
    require(0 <= len(questions) <= 5000 and (questions or introductions), 'Bank must contain questions or Introductions')
    Catalog._validate_unit_tree(groups)
    return bank


class TrainingCatalog:
    def __init__(self, data_dir):
        self.root = Path(data_dir) / DIRECTORY
        self.banks = {}
        if self.root.is_symlink():
            raise ValueError('Training bank directory must not be a symlink')
        for path in sorted(self.root.glob('*.json')):
            if path.is_symlink() or path.stat().st_size > 32000000:
                raise ValueError('Invalid training bank file')
            bank = validate(json.loads(path.read_text(encoding='utf-8-sig')))
            if path.name != bank['id'] + '.json' or bank['id'] in self.banks:
                raise ValueError('Training bank filename differs from its ID')
            self.banks[bank['id']] = bank
        from .training_format import read_formats, present
        self.formats = read_formats(data_dir, self.banks)
        self.presented = {ident: present(self.banks[ident], value) for ident, value in self.formats.items()}
        self.topic_redirects = {}
        for bank in self.banks.values():
            for group in bank['groups']:
                for kind, rows in (('question', group.get('questions', [])), ('introduction', group.get('introductions', []))):
                    for item in rows:
                        tid = item.get('legacy_topic_id')
                        if tid:
                            if not isinstance(tid, str) or not IDENTIFIER.fullmatch(tid) or tid in self.topic_redirects:
                                raise ValueError('Invalid or duplicate legacy Topic mapping')
                            self.topic_redirects[tid] = {'bank_id': bank['id'], 'group_id': group['id'],
                                                         kind + '_id': item['id']}

    def permissions(self, existing=()):
        result = [{'id': permission_id(b['id']), 'title': b['title']} for b in self.banks.values()]
        if set(existing) & {row['id'] for row in result}:
            raise ValueError('Training permissions collide with existing Topic IDs')
        return result

    def validate_ownership(self, catalog, subjects):
        """A bank is a native bank, never a second UI over course Topics."""
        for tid, target in self.topic_redirects.items():
            course = catalog.courses.get(target['bank_id'])
            if (not course or course.get('placement') != 'question_bank'
                    or tid not in {t['id'] for t in course['topics']}
                    or catalog.topic_courses.get(tid) != {course['id']}):
                raise DomainError('ambiguous_content_owner', '同一内容不能同时由课程和题库两套流程管理。', 422)
        for cid, course in catalog.courses.items():
            if course.get('placement') != 'question_bank':
                if cid in self.banks:
                    raise DomainError('ambiguous_content_owner', '课程和题库不能共用同一个容器标识。', 422)
                continue
            bank = self.banks.get(cid)
            if bank is None:
                raise DomainError('bank_conversion_required',
                    '题库归属需要完整题库转换，不能仅修改目录位置。请先完成内容及学习记录迁移。', 422)
            if bank['subject_id'] != subjects.course_subjects[cid]:
                raise DomainError('subject_bank_mismatch', '题库与原内容的学科归属不一致。', 422)
            members = {t['id'] for t in course['topics']}
            mapped = {tid for tid, target in self.topic_redirects.items() if target['bank_id'] == cid}
            if members != mapped:
                raise DomainError('incomplete_bank_conversion', '题库转换必须完整保留原内容及链接对应关系。', 422)

    def bank(self, bank_id):
        if bank_id not in self.banks:
            raise DomainError('unknown_bank', '未找到此题库。', 404)
        return self.presented.get(bank_id, self.banks[bank_id])

    def question(self, bank_id, group_id, question_id):
        bank = self.bank(bank_id)
        for group in bank['groups']:
            if group['id'] == group_id:
                for question in group.get('questions', []):
                    if question['id'] == question_id:
                        return bank, group, present_question(deepcopy(question))
        raise DomainError('unknown_question', '未找到此习题。', 404)

    def archive_blobs(self):
        from .training_format import DIRECTORY as FORMAT_DIRECTORY
        return {**{f'{DIRECTORY}/{b["id"]}.json': json.dumps(b, ensure_ascii=False, indent=2).encode()
                   for b in self.banks.values()},
                **{f'{FORMAT_DIRECTORY}/{ident}.json': json.dumps(v, ensure_ascii=False, indent=2).encode()
                   for ident, v in self.formats.items()}}


def validate_archive(blobs):
    names, banks = set(), {}
    for name, blob in blobs.items():
        if name.startswith(DIRECTORY + '/'):
            bank = validate(json.loads(blob))
            if name != f'{DIRECTORY}/{bank["id"]}.json':
                raise ValueError('Invalid training bank archive path')
            names.add(name)
            banks[bank['id']] = bank
    from .training_format import validate_archive as validate_formats
    names.update(validate_formats(blobs, banks))
    return names


def validate_saved(con, catalog):
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    identities = {(bank['id'], q['id'], bank['version']) for bank in catalog.banks.values()
                  for group in bank['groups'] for q in group.get('questions', [])}
    for table in ('training_memory', 'training_attempts', 'training_ratings', 'training_suspensions'):
        if table in tables:
            for row in con.execute(f'SELECT DISTINCT bank,question,version FROM {table}'):
                if tuple(row) not in identities:
                    raise ValueError('Saved training records require the original bank version')


def install(compiled, data_dir):
    bank = validate(json.loads(Path(compiled).read_text(encoding='utf-8-sig')))
    root = Path(data_dir) / DIRECTORY
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError('Training bank directory must not be a symlink')
    target = root / (bank['id'] + '.json')
    # Immutable IDs: an import must never overwrite content backing learner cards.
    with target.open('x', encoding='utf-8') as handle:
        json.dump(bank, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return target


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'install':
        print(install(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) == 1:
        bank = json.loads(sys.stdin.buffer.read().decode('utf-8'))
        bank['version'] = fingerprint(bank)
        validate(bank)
        sys.stdout.buffer.write((json.dumps(bank, ensure_ascii=False, indent=2) + '\n').encode())
    else:
        raise SystemExit('Usage: python -m graspmemoedu.training_content install COMPILED_JSON DATA_DIR')
