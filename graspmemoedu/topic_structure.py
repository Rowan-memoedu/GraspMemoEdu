"""One content model with optional retrieval points and Example modules.

Legacy training_mode is accepted at the boundary, not used as ownership of
content. Projections keep authored identities and never migrate learner rows.
"""


def has_atomic(topic):
    return bool(topic.get('flashcards') or topic.get('memory_chunks') or topic.get('memory_points'))


def has_paradigm(topic):
    return bool(topic.get('modules'))


def sectioned_training(topic):
    return has_atomic(topic) and (has_paradigm(topic) or topic.get('topic_format_version') == 2)


def atomic_state(state):
    return (state or {}).get('atomic', state or {})


def atomic_backend(service):
    return getattr(service, 'atomic', service)


def sections(topic):
    """Group both kinds of training under their exact Introduction identity."""
    introductions = topic.get('introductions') or [
        {'id': 'introduction', **topic.get('introduction', {})}]
    intros = {intro['id']: intro for intro in introductions}
    modules = {module['id']: module for module in topic.get('modules', [])}
    order = topic.get('content_order') or [
        {'kind': 'introduction', 'id': introductions[0]['id']},
        *[{'kind': 'example', 'id': ident} for ident in modules]]
    result = []
    for entry in order:
        if entry['kind'] == 'introduction':
            result.append({'id': entry['id'], 'introduction': intros[entry['id']],
                           'modules': [], 'chunks': [], 'point_ids': []})
        elif not result:
            raise ValueError('Every Example must follow an Introduction')
        else:
            result[-1]['modules'].append(modules[entry['id']])
    by_id = {section['id']: section for section in result}
    default = result[0]['id'] if len(result) == 1 else None
    for chunk in topic.get('memory_chunks', []):
        intro_id = chunk.get('introduction_id', default)
        if intro_id not in by_id:
            raise ValueError('Every memory chunk must identify its Introduction')
        by_id[intro_id]['chunks'].append(chunk)
        by_id[intro_id]['point_ids'].extend(p['id'] for p in chunk['points'])
    for point in topic.get('memory_points', []):
        intro_id = point.get('introduction_id', default)
        if intro_id not in by_id:
            raise ValueError('Every standalone point must identify its Introduction')
        by_id[intro_id]['point_ids'].append(point['id'])
    return result


def point_introductions(topic):
    return {point: section['id'] for section in sections(topic) for point in section['point_ids']}
