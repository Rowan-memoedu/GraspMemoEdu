"""Source-bound presentation changes for immutable training banks.

Only stems/interactions and empty singleton wrappers can change. IDs, original
answers, versions and attempt snapshots remain owned by the original bank.
"""
from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path
import re

from .question_bank import digest
from .question_types import validate_interaction
from .training_presentation import present_book_question

DIRECTORY = 'training-formats'


def flatten(groups, wrappers):
    groups = deepcopy(groups)
    if not isinstance(wrappers, list) or any(not isinstance(x, str) for x in wrappers) or len(set(wrappers)) != len(wrappers):
        raise ValueError('Invalid singleton wrapper list')
    for ident in wrappers:
        node = next((g for g in groups if g['id'] == ident), None)
        children = [g for g in groups if g.get('parent_id') == ident]
        if not node or not node.get('parent_id') or node.get('questions') or len(children) != 1:
            raise ValueError('Only an empty non-root singleton wrapper can be flattened')
        children[0]['parent_id'] = node['parent_id']
        groups.remove(node)
    return groups


def validate_format(value, bank):
    if (not isinstance(value, dict) or set(value) - {'schema', 'bank_id', 'bank_version', 'questions', 'flatten', 'media'}
            or not {'schema', 'bank_id', 'bank_version', 'questions', 'flatten'} <= set(value)
            or type(value['schema']) is not int or value['schema'] != 1
            or value['bank_id'] != bank['id'] or value['bank_version'] != bank['version']
            or not isinstance(value['questions'], dict)):
        raise ValueError('Invalid training format or source version binding')
    originals = {q['id']: q for g in bank['groups'] for q in g.get('questions', [])}
    for ident, variant in value['questions'].items():
        if (ident not in originals or not isinstance(variant, dict)
                or set(variant) != {'source_sha256', 'question', 'html', 'interaction'}
                or variant['source_sha256'] != digest(originals[ident])
                or not isinstance(variant['question'], str) or not 0 < len(variant['question'].strip()) <= 100000
                or not isinstance(variant['html'], str) or not 0 < len(variant['html'].strip()) <= 1000000
                or not isinstance(variant['interaction'], dict)):
            raise ValueError('Training format must bind to an unchanged source question')
        validate_interaction(variant['interaction'], variant['question'])
    flatten(bank['groups'], value['flatten'])
    if 'media' in value:
        from .training_media import validate_media
        validate_media(value['media'], bank, value['questions'])
    return value


def present(bank, value):
    groups = flatten(bank['groups'], value['flatten'])
    for group in groups:
        group['questions'] = [{**q, **{k: v for k, v in value['questions'].get(q['id'], {}).items()
                                     if k != 'source_sha256'}} for q in group.get('questions', [])]
    for group in groups:
        group['questions'] = [present_book_question(q, value.get('media')) for q in group['questions']]
    return {**bank, 'groups': groups}


def read_formats(data_dir, banks):
    root = Path(data_dir) / DIRECTORY
    if root.is_symlink():
        raise ValueError('Training format directory must not be a symlink')
    values = {}
    for path in sorted(root.glob('*.json')):
        if path.is_symlink() or path.stat().st_size > 32000000 or path.stem not in banks:
            raise ValueError('Invalid training format file')
        values[path.stem] = validate_format(json.loads(path.read_text('utf-8-sig')), banks[path.stem])
    return values


def validate_archive(blobs, banks):
    names = set()
    for name, raw in blobs.items():
        if name.startswith(DIRECTORY + '/'):
            ident = name.removeprefix(DIRECTORY + '/').removesuffix('.json')
            if ident not in banks or name != f'{DIRECTORY}/{ident}.json' or len(raw) > 32000000:
                raise ValueError('Invalid training format archive path')
            validate_format(json.loads(raw), banks[ident])
            names.add(name)
    return names


def install(compiled, data_dir, *, expected_previous=None):
    # Different derived formats require an explicit previous-content hash.
    from .training_content import TrainingCatalog
    raw = Path(compiled).read_bytes()
    if len(raw) > 32000000:
        raise ValueError('Training format exceeds file limit')
    value = json.loads(raw)
    catalog = TrainingCatalog(data_dir)
    ident = value.get('bank_id')
    if not isinstance(ident, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', ident) or ident not in catalog.banks:
        raise ValueError('Unknown format bank')
    validate_format(value, catalog.banks[ident])
    root = Path(data_dir) / DIRECTORY
    root.mkdir(exist_ok=True)
    if root.is_symlink():
        raise ValueError('Training format directory must not be a symlink')
    target = root / (ident + '.json')
    if target.exists():
        if target.is_symlink():
            raise ValueError('An existing format differs')
        previous = target.read_bytes()
        if json.loads(previous) == value:
            return 'already_installed'
        if expected_previous is None or hashlib.sha256(previous).hexdigest() != expected_previous:
            raise ValueError('An existing format differs')
        history = root / 'history'; history.mkdir(exist_ok=True)
        if history.is_symlink():
            raise ValueError('Format history must not be a symlink')
        saved = history / (ident + '-' + expected_previous + '.json')
        if not saved.exists():
            with saved.open('xb') as stream: stream.write(previous)
        temporary = target.with_suffix('.pending')
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')
        os.replace(temporary, target)
        return 'updated'
    with target.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return 'installed'
