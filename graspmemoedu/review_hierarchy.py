"""Review projections over the existing course and bank directory metadata."""
from .course_hierarchy import pending_hierarchy


def source_trees(catalog, bank_catalog, entries):
    """Keep original parent links and order, adding one source root per directory."""
    trees = []
    for course in catalog.courses.values():
        topics = {t['id'] for t in course['topics']}
        selected = [e for e in entries if e.get('source', {}).get('kind') == 'atomic'
                    and e['source']['container'] in topics]
        if not selected:
            continue
        prefix = 'course:' + course['id']
        topic_ids = {e['source']['container'] for e in selected}
        projected = pending_hierarchy(course['units'], [
            {'type': 'Lesson', 'topic_id': tid, 'course_id': course['id']} for tid in topic_ids])
        units = [{'id': prefix, 'title': course['title'], 'parent_id': None, 'topic_ids': []}]
        parents = {}
        for unit in projected:
            uid = prefix + ':unit:' + unit['id']
            units.append({'id': uid, 'title': unit['title'],
                          'parent_id': prefix + ':unit:' + unit['parent_id'] if unit.get('parent_id') else prefix,
                          'topic_ids': []})
            parents.update({tid: uid for tid in unit['topic_ids']})
        for topic in course['topics']:
            tid = topic['id']
            if tid in topic_ids:
                units.append({'id': prefix + ':topic:' + tid, 'title': catalog.topics[tid]['title'],
                              'parent_id': parents.get(tid, prefix),
                              'topic_ids': [e['id'] for e in selected if e['source']['container'] == tid]})
        trees.append({'kind': 'atomic', 'course': {'id': prefix, 'title': course['title']},
                      'pending_hierarchy': units, 'tasks': selected,
                      'source_order': [u['id'] for u in units] + [e['id'] for e in selected]})
    for bank in bank_catalog.banks.values():
        selected = [e for e in entries if e.get('source', {}).get('kind') == 'training'
                    and e['source']['container'] == bank['id']]
        if not selected:
            continue
        prefix = 'bank:' + bank['id']
        groups = {g['id']: g for g in bank['groups']}
        retained = set()
        for entry in selected:
            gid = entry['source']['group_id']
            while gid and gid not in retained and gid in groups:
                retained.add(gid)
                gid = groups[gid].get('parent_id')
        units = [{'id': prefix, 'title': bank['title'], 'parent_id': None, 'topic_ids': []}]
        for group in bank['groups']:
            if group['id'] in retained:
                units.append({'id': prefix + ':group:' + group['id'], 'title': group['title'],
                              'parent_id': prefix + ':group:' + group['parent_id'] if group.get('parent_id') else prefix,
                              'topic_ids': [e['id'] for e in selected if e['source']['group_id'] == group['id']]})
        trees.append({'kind': 'training', 'course': {'id': prefix, 'title': bank['title']},
                      'pending_hierarchy': units, 'tasks': selected,
                      'source_order': [u['id'] for u in units] + [e['id'] for e in selected]})
    return trees


def public_source(card):
    source = card['source']
    return {key: source[key] for key in ('kind', 'container', 'group_id') if key in source}
