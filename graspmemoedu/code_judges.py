"""Versioned local-check bindings for existing questions, not another question bank."""
import hashlib
import json
from pathlib import Path
import re

from .service import DomainError

ENGINE = 'java-sandbox-v1'
FIELDS = {'source_sha256', 'package_sha256', 'language', 'engine'}


def source_signature(question):
    value = {k: question.get(k) for k in ('id', 'question', 'html', 'hyperskill', 'hyperskill_presentation')}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def valid_definition(value):
    return (isinstance(value, dict) and set(value) == FIELDS and value['engine'] == ENGINE
            and value['language'] == 'java25'
            and all(isinstance(value[k], str) and re.fullmatch('[0-9a-f]{64}', value[k])
                    for k in ('source_sha256', 'package_sha256')))


def registry(data_dir):
    path = Path(data_dir) / 'code-judges.json'
    if not path.exists():
        return {'schema': 1, 'bindings': {}, 'revoked': []}
    if path.stat().st_size > 4 * 1024 * 1024:
        raise DomainError('code_judge_config', '本地判题配置超出大小限制。', 503)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not valid_registry(value):
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise DomainError('code_judge_config', '本地判题配置无效，当前作答未评分。', 503)


def valid_registry(value):
    return (isinstance(value, dict) and value.get('schema') == 1 and isinstance(value.get('bindings'), dict)
            and all(isinstance(k, str) and re.fullmatch(r'[\w.-]+/[\w.-]+', k) and valid_definition(v)
                    for k, v in value['bindings'].items())
            and isinstance(value.get('revoked', []), list)
            and all(isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in value.get('revoked', [])))


def selected(training, bank_id, question, run=None):
    if question.get('hyperskill', {}).get('type') != 'code':
        return None
    # Existing rounds retain their original judge, including the original upstream route.
    if run is not None:
        return run.get('code_judge')
    value = registry(training.db_path.parent)
    definition = value['bindings'].get(bank_id + '/' + question['id'])
    if (definition and definition['source_sha256'] == source_signature(question)
            and definition['package_sha256'] not in value.get('revoked', [])):
        return definition
    return None


def require_active(training, question, definition):
    if not valid_definition(definition) or definition['source_sha256'] != source_signature(question):
        raise DomainError('stale_code_judge', '本地测试与当前题目版本不匹配，当前作答未评分。', 409)
    if definition['package_sha256'] in registry(training.db_path.parent).get('revoked', []):
        raise DomainError('revoked_code_judge', '此测试包已停用，当前作答未评分。', 409)


def local_binding(question, definition, run=None):
    return {'task_key': question['id'], 'version': definition['source_sha256'], 'kind': 'automated',
            'code_judge': definition, 'round_id': run['id'] if run else None,
            'checks': len((run or {}).get('events', [])), 'opened': bool((run or {}).get('opened_at'))}


def install(data_dir, incoming):
    from .training_content import TrainingCatalog
    catalog = TrainingCatalog(data_dir)
    patch = json.loads(Path(incoming).read_text(encoding='utf-8'))
    if not valid_registry(patch):
        raise ValueError('Invalid judge registry')
    for key, value in patch['bindings'].items():
        bank, question_id = key.split('/')
        questions = [q for g in catalog.bank(bank)['groups'] for q in g['questions'] if q['id'] == question_id]
        if len(questions) != 1 or source_signature(questions[0]) != value['source_sha256']:
            raise ValueError('Question source differs from admitted package')
    current = registry(data_dir)
    merged = {'schema': 1, 'bindings': {**current['bindings'], **patch['bindings']},
              'revoked': sorted(set(current.get('revoked', [])) | set(patch.get('revoked', [])))}
    path = Path(data_dir) / 'code-judges.json'
    if path.exists():
        backup = path.with_name('code-judges.previous.json')
        backup.write_bytes(path.read_bytes())
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    pending.replace(path)
    return {'registered': len(patch['bindings']), 'banks_unchanged': True, 'learning_records_unchanged': True}


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--data-dir', required=True); p.add_argument('--input', required=True)
    a = p.parse_args(); print(json.dumps(install(a.data_dir, a.input)))
