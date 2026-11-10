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
    if topic.get('training_mode', 'paradigm') not in ('paradigm', 'atomic_retrieval') or topic.get('topic_kind') not in ('lesson', 'introduction'):
        raise ValueError('Invalid Topic v2 kind or training mode')
    if not isinstance(topic.get('content_version'), str) or not topic['content_version']:
        raise ValueError('Missing teaching content version')
    items = topic.get('introductions')
    if not isinstance(items, list) or not (items or topic.get('bank_questions')):
        raise ValueError('Topic v2 requires Introduction sections')
    seen = set()
    from .topic_composition import validate_sections, validate_reference
    sections = validate_sections([{k:v for k,v in s.items() if k != 'title_html'} for s in topic['sections']]) if 'sections' in topic else {}
    if any('title_html' in s and not isinstance(s['title_html'], str) for s in topic.get('sections', [])):
        raise ValueError('Invalid rendered section heading')
    for intro in items:
        if (not isinstance(intro, dict) or not ID.fullmatch(str(intro.get('id', '')))
                or intro['id'] in seen or not isinstance(intro.get('html'), str)
                or not (intro['html'].strip() or intro.get('example') or intro.get('footnotes'))
                or not isinstance(intro.get('title'), str) or not intro['title'].strip()):
            raise ValueError('Invalid or duplicate Introduction')
        seen.add(intro['id'])
        if 'example' in intro and (not isinstance(intro['example'], str) or not intro['example'].strip()):
            raise ValueError('Reading example must contain ungraded HTML')
        if 'footnotes' in intro:
            if (not isinstance(intro['footnotes'], list)
                    or any(not isinstance(n, dict) or set(n) != {'id', 'html'}
                           or not ID.fullmatch(str(n['id'])) or not isinstance(n['html'], str) or not n['html'].strip()
                           for n in intro['footnotes'])):
                raise ValueError('Invalid dedicated footnotes field')
            if len({n['id'] for n in intro['footnotes']}) != len(intro['footnotes']):
                raise ValueError('Duplicate footnote identity')
    bank_questions = topic.get('bank_questions', [])
    if not isinstance(bank_questions, list):
        raise ValueError('Bank references must be ordered')
    refs = set()
    for item in bank_questions:
        if (not isinstance(item, dict) or set(item) - {'id', 'reference', 'section_id'}
                or not ID.fullmatch(str(item.get('id', ''))) or item['id'] in seen):
            raise ValueError('Invalid bank question subblock')
        validate_reference(item.get('reference'))
        key = tuple(item['reference'][k] for k in ('bank_id', 'group_id', 'question_id', 'version'))
        if key in refs:
            raise ValueError('A Topic must not repeat a canonical question reference')
        refs.add(key); seen.add(item['id'])
    if sections and any(item.get('section_id') not in sections for item in [*items, *bank_questions]):
        raise ValueError('Subblock has no authored Topic section')
    if 'reading_lineage' in topic:
        from .topic_composition import validate_reading_lineage
        validate_reading_lineage(topic['reading_lineage'], {i['id'] for i in items})
    modules = topic.get('modules')
    from .topic_structure import has_atomic
    if not isinstance(modules, list) or (is_introduction(topic) != (not modules and not has_atomic(topic))):
        raise ValueError('Topic kind and Example modules disagree')
    module_ids = [m.get('id') for m in modules]
    if sections and any(m.get('section_id') not in sections for m in modules):
        raise ValueError('Example module has no authored Topic section')
    if len(set(module_ids)) != len(module_ids) or seen.intersection(module_ids):
        raise ValueError('Duplicate section ID')
    expected = ({('introduction', i['id']) for i in items} | {('example', mid) for mid in module_ids}
                | {('bank_question', item['id']) for item in bank_questions})
    order = topic.get('content_order')
    if not isinstance(order, list) or any(not isinstance(i, dict) or set(i) != {'kind', 'id'} for i in order):
        raise ValueError('Invalid content order')
    actual = [(i['kind'], i['id']) for i in order]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError('Content order must cover every section once')
    if order[0]['kind'] != 'introduction' and not bank_questions:
        raise ValueError('Every Example must follow its Introduction')
    owners = {('introduction', i['id'], 'html'): i['id'] for i in items}
    owners.update({('introduction', i['id'], field): i['id'] for i in items
                   for field in ('example', 'footnotes') if field in i})
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
    if not topic.get('availability', {}).get('trainable', True):
        return max(1, len(topic.get('modules', [])))
    if is_introduction(topic):
        return 1
    from .topic_structure import has_atomic
    if has_atomic(topic):
        from .atomic_content import memory_points
        return len(memory_points(topic)) + len(topic.get('modules', []))
    return len(topic['modules'])


def reread_pending(topic, state):
    lineage = topic.get('reading_lineage', {})
    if state and lineage.get('content_version') == state.get('content_version'):
        carried = {n for old in state.get('read_introductions', []) for n in lineage['introductions'].get(old, [])}
        if carried == {i['id'] for i in topic['introductions']}:
            return False
    return bool(is_introduction(topic) and state and state.get('completed_at')
                and state.get('read_content_version') != topic['content_version'])
