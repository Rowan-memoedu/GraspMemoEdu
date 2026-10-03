"""Independent, immutable training banks. These never alter course Topics."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

from .catalog import Catalog
from .question_types import validate_interaction
from .service import DomainError
from .subjects import DEFAULTS

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
    require(bank.get('subject_id') in {row[0] for row in DEFAULTS}, 'Unknown subject')
    require(isinstance(bank.get('description', ''), str) and len(bank.get('description', '')) <= 4000,
            'Invalid bank description')
    require(isinstance(bank.get('math_css', ''), str), 'Invalid math style')
    require(bank.get('version') == fingerprint(bank), 'Training bank version differs from content')
    groups = bank.get('groups')
    require(isinstance(groups, list) and 1 <= len(groups) <= 200, 'Missing question groups')
    ids, questions = set(), set()
    for group in groups:
        require(isinstance(group, dict) and identifier(group.get('id')) and group['id'] not in ids,
                'Duplicate or invalid group')
        ids.add(group['id'])
        require(text(group.get('title'), 200), 'Missing group title')
        rows = group.get('questions', [])
        require(isinstance(rows, list) and len(rows) <= 500, 'Too many questions in group')
        for q in rows:
            require(isinstance(q, dict) and identifier(q.get('id')) and q['id'] not in questions,
                    'Question IDs must be unique within the bank')
            questions.add(q['id'])
            require(all(text(q.get(key)) for key in ('question', 'html', 'reference_answer',
                                                    'explanation_html', 'answer_type', 'constraints')),
                    'Question needs a stem, reference answer and explanation')
            require(text(q.get('title', q['id']), 200), 'Invalid question title')
            validate_interaction(q.get('interaction'), q['question'])
    require(0 < len(questions) <= 5000, 'Bank must contain 1 to 5000 questions')
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

    def permissions(self, existing=()):
        result = [{'id': permission_id(b['id']), 'title': b['title']} for b in self.banks.values()]
        if set(existing) & {row['id'] for row in result}:
            raise ValueError('Training permissions collide with existing Topic IDs')
        return result

    def bank(self, bank_id):
        if bank_id not in self.banks:
            raise DomainError('unknown_bank', '未找到此题库。', 404)
        return self.banks[bank_id]

    def question(self, bank_id, group_id, question_id):
        bank = self.bank(bank_id)
        for group in bank['groups']:
            if group['id'] == group_id:
                for question in group.get('questions', []):
                    if question['id'] == question_id:
                        return bank, group, deepcopy(question)
        raise DomainError('unknown_question', '未找到此习题。', 404)

    def archive_blobs(self):
        return {f'{DIRECTORY}/{b["id"]}.json': json.dumps(b, ensure_ascii=False, indent=2).encode()
                for b in self.banks.values()}


def validate_archive(blobs):
    names = set()
    for name, blob in blobs.items():
        if name.startswith(DIRECTORY + '/'):
            bank = validate(json.loads(blob))
            if name != f'{DIRECTORY}/{bank["id"]}.json':
                raise ValueError('Invalid training bank archive path')
            names.add(name)
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
