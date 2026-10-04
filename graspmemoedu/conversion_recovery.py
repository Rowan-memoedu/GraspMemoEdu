"""Read original conversion responses for review/reuse, without model calls."""
import json
from pathlib import Path

from .physics_question_conversion import PhysicsAdapter, production_plan
from .question_bank import digest
from .conversion_response import decode_questions


def recover_candidates(courses, directories):
    _, sources = production_plan(courses)
    current = {source['id']: source for source in sources}
    recovered, rejected, history = {}, [], []
    fields = ('id', 'topic_id', 'question', 'reference_answer', 'constraints',
              'explanation', 'correct_option', 'option_kind', 'choice_count', 'scalar', 'mechanical')
    for directory in map(Path, directories):
        for response_path in sorted((directory / 'requests').glob('*.response.json'), key=lambda p: p.stat().st_mtime_ns):
            request_path = response_path.with_name(response_path.name.replace('.response.', '.request.'))
            request = json.loads(request_path.read_text(encoding='utf-8'))
            response = json.loads(response_path.read_text(encoding='utf-8'))
            if response.get('choices', [{}])[0].get('finish_reason') != 'stop':
                continue
            try:
                payload = json.loads(request['messages'][1]['content'][0]['text'].split('输入数据：\n', 1)[1])
                requested = {row['id']: row for row in payload['questions']}
                rows = decode_questions(response)['questions']
            except (ValueError, KeyError, TypeError, IndexError):
                continue
            for row in rows:
                source = current.get(row.get('id'))
                old = requested.get(row.get('id'))
                if not source or not old:
                    continue
                if (any(old.get(key) != source[key] for key in fields)
                        or payload['contexts'].get(source['topic_id']) != source['context']
                        or old.get('image_ids') != [image['image_id'] for image in source['images']]):
                    rejected.append({'id': row['id'], 'reason': 'Source or mechanical group changed', 'path': str(response_path)})
                    continue
                candidate = {'distractors': row.get('distractors')}
                evidence = {'response_path': str(response_path.resolve()), 'response_sha256': digest(response),
                            'request_sha256': digest(request)}
                item = {'id': source['id'], 'source_sha256': digest(source), 'evidence': evidence,
                        'artifact': {'source_sha256': digest(source), 'candidate': candidate,
                                     'request_key': response_path.name.split('.')[0],
                                     'prompt_version': request.get('prompt_version')}}
                try:
                    item['artifact']['checks'] = PhysicsAdapter.validate(source, candidate)
                    item['hard_validation_passed'] = True
                    item['quality_approved'] = False
                except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                    item['hard_validation_passed'] = False
                    item['repair_details'] = PhysicsAdapter.repair_details(source, candidate, exc)
                recovered[source['id']] = item
                history.append(item)
    return {'items': list(recovered.values()), 'history': history, 'rejected': rejected}


def reviewed_reuse(recovery, approved_ids, quality_failures=None):
    items, failed = [], {}
    for item in recovery['items']:
        if item['id'] in approved_ids:
            if not item['hard_validation_passed']:
                raise ValueError('Cannot approve a candidate that failed hard validation')
            items.append({**item, 'quality_approved': True})
        elif not item['hard_validation_passed']:
            failed[item['id']] = item['repair_details']
        elif quality_failures and item['id'] in quality_failures:
            failed[item['id']] = {'message': quality_failures[item['id']],
                                 'previous_candidate': item['artifact']['candidate'], 'failures': []}
    if set(approved_ids) != {item['id'] for item in items}:
        raise ValueError('Unknown reviewed candidate')
    return {'items': items, 'failed': failed}


def verify_recovery_evidence(item):
    """Ensure the reviewed candidate is an unchanged original provider result."""
    response_path = Path(item['evidence']['response_path'])
    response = json.loads(response_path.read_text(encoding='utf-8'))
    request_path = response_path.with_name(response_path.name.replace('.response.', '.request.'))
    request = json.loads(request_path.read_text(encoding='utf-8'))
    if (digest(response) != item['evidence']['response_sha256']
            or digest(request) != item['evidence']['request_sha256']):
        raise ValueError('Recovery evidence changed')
    original = next(row for row in decode_questions(response)['questions']
                    if row['id'] == item['id'])
    if item['artifact']['candidate'] != {'distractors': original['distractors']}:
        raise ValueError('Recovered candidate was manually changed')


def verify_cached_response(question_id, artifact, directory):
    if artifact.get('recovery_evidence'):
        verify_recovery_evidence({'id': question_id, 'artifact': artifact, 'evidence': artifact['recovery_evidence']})
        return
    source_directory = Path(artifact.get('reused_from', directory))
    # A second continuation still points to the immutable original response.
    if artifact.get('previous_cache'):
        source_directory = Path(artifact['previous_cache']).parent.parent
        original = json.loads(Path(artifact['previous_cache']).read_text(encoding='utf-8'))
        if original['candidate'] != artifact['candidate']:
            raise ValueError('Cached candidate changed during continuation')
        return verify_cached_response(question_id, original, source_directory)
    response = json.loads((source_directory / 'requests' / (artifact['request_key'] + '.response.json')).read_text(encoding='utf-8'))
    row = next(row for row in decode_questions(response)['questions'] if row['id'] == question_id)
    if artifact['candidate'] != {'distractors': row['distractors']}:
        raise ValueError('Cached candidate was manually changed')
