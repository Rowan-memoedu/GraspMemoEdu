"""Pure validation and projections for the shared Topic v2 runtime format."""
import re

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
REM_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


def is_v2(topic):
    return topic.get('topic_format_version') == 2


def is_introduction(topic):
    return is_v2(topic) and topic.get('topic_kind') == 'introduction'


def introductions(topic):
    if is_v2(topic):
        return topic['introductions']
    return [topic['introduction']] if topic.get('introduction') else []


def validate_runtime(topic):
    if not is_v2(topic):
        if 'topic_format_version' in topic:
            raise ValueError('Unknown Topic format version')
        return
    if topic.get('training_mode', 'paradigm') != 'paradigm' or topic.get('topic_kind') not in ('lesson', 'introduction'):
        raise ValueError('Invalid Topic v2 kind or training mode')
    if not isinstance(topic.get('content_version'), str) or not topic['content_version']:
        raise ValueError('Missing teaching content version')
    items = topic.get('introductions')
    if not isinstance(items, list) or not items:
        raise ValueError('Topic v2 requires Introduction sections')
    seen = set()
    for intro in items:
        if (not isinstance(intro, dict) or not ID.fullmatch(str(intro.get('id', '')))
                or intro['id'] in seen or not isinstance(intro.get('html'), str) or not intro['html'].strip()
                or not isinstance(intro.get('title'), str) or not intro['title'].strip()):
            raise ValueError('Invalid or duplicate Introduction')
        seen.add(intro['id'])
    modules = topic.get('modules')
    if not isinstance(modules, list) or (is_introduction(topic) != (not modules)):
        raise ValueError('Topic kind and Example modules disagree')
    module_ids = [m.get('id') for m in modules]
    if len(set(module_ids)) != len(module_ids) or seen.intersection(module_ids):
        raise ValueError('Duplicate section ID')
    expected = {('introduction', i['id']) for i in items} | {('example', mid) for mid in module_ids}
    order = topic.get('content_order')
    if not isinstance(order, list) or any(not isinstance(i, dict) or set(i) != {'kind', 'id'} for i in order):
        raise ValueError('Invalid content order')
    actual = [(i['kind'], i['id']) for i in order]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError('Content order must cover every section once')
    if order[0]['kind'] != 'introduction':
        raise ValueError('Every Example must follow its Introduction')
    owners = {('introduction', i['id'], 'html'): i['id'] for i in items}
    for module in modules:
        for kind, question in [('example', module['example']), *[('practice', q) for q in module['practices']]]:
            for field in ('html', 'explanation_html'):
                owners[(kind, question['id'], field)] = module['id']
    nodes = topic.get('content_nodes', [])
    if not isinstance(nodes, list):
        raise ValueError('Invalid content node index')
    indexed = {}
    for node in nodes:
        if not isinstance(node, dict) or not ID.fullmatch(str(node.get('id', ''))) or node['id'] in indexed:
            raise ValueError('Invalid or duplicate content node')
        owner = node.get('owner', {})
        key = (owner.get('kind'), owner.get('id'), owner.get('field'))
        if owners.get(key) != node.get('section_id') or key not in owners:
            raise ValueError('Content node owner does not match a section')
        source = node.get('source_rem_id')
        if source is not None and (not isinstance(source, str) or not REM_ID.fullmatch(source)):
            raise ValueError('Invalid source Rem ID')
        indexed[node['id']] = node
    for node in nodes:
        parent = node.get('parent_id')
        visited = {node['id']}
        while parent is not None:
            if parent in visited or parent not in indexed or indexed[parent]['section_id'] != node['section_id']:
                raise ValueError('Invalid content node parent')
            visited.add(parent)
            parent = indexed[parent].get('parent_id')
    if not isinstance(topic.get('references', []), list):
        raise ValueError('Invalid reference list')
    for ref in topic.get('references', []):
        if (not isinstance(ref, dict) or ref.get('source_node_id') not in indexed
                or not isinstance(ref.get('target_rem_id'), str) or not REM_ID.fullmatch(ref['target_rem_id'])
                or not isinstance(ref.get('label'), str) or not ref['label'].strip()):
            raise ValueError('Invalid authored reference')
    source = topic.get('source_rem_id')
    if source is not None and (not isinstance(source, str) or not REM_ID.fullmatch(source)):
        raise ValueError('Invalid Topic source Rem ID')


def completed_modules(topic, state):
    if is_introduction(topic):
        return 1 if (state or {}).get('completed_at') else 0
    known = {m['id'] for m in topic.get('modules', [])}
    return len({a['module_id'] for a in (state or {}).get('attempts', [])
                if a.get('status') == 'completed' and a.get('module_id') in known})


def learning_units(topic):
    if is_introduction(topic):
        return 1
    if topic.get('training_mode') == 'atomic_retrieval':
        return sum(len(c['points']) for c in topic['memory_chunks'])
    return len(topic['modules'])


def reread_pending(topic, state):
    return bool(is_introduction(topic) and state and state.get('completed_at')
                and state.get('read_content_version') != topic['content_version'])
