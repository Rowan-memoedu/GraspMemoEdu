"""Iterative course containment projection, independent of prerequisite edges."""


def select_subtree(units, root_id=None):
    """Select a view root without changing stored IDs, ownership or dependencies."""
    if not root_id:
        return list(units)
    from .service import DomainError
    if root_id not in {unit['id'] for unit in units}:
        raise DomainError('unknown_catalog_root', '未找到此目录层级，请重新选择。', 404)
    children = {}
    for unit in units:
        children.setdefault(unit.get('parent_id'), []).append(unit['id'])
    retained, pending = set(), [root_id]
    while pending:
        ident = pending.pop()
        if ident in retained:
            continue
        retained.add(ident)
        pending.extend(children.get(ident, []))
    return [{**unit, 'parent_id': None if unit['id'] == root_id else unit.get('parent_id')}
            for unit in units if unit['id'] in retained]


def course_scope(course, root_id=None):
    if not root_id:
        return course
    units = select_subtree(course['units'], root_id)
    members = {tid for unit in units for tid in unit['topic_ids']}
    root = next(unit for unit in units if unit['id'] == root_id)
    return {**course, 'title': root['title'], 'root_id': root_id, 'units': units,
            'topics': [topic for topic in course['topics'] if topic['id'] in members]}


def pending_hierarchy(units, tasks, root_id=None):
    pending = {task['topic_id'] for task in tasks if task['type'] == 'Lesson'}
    course_ids = {task.get('course_id') for task in tasks}
    return project_hierarchy(units, pending, course_ids | {root_id})


def project_hierarchy(units, topic_ids, course_ids):
    """One course-tree projection for learning, reports and other course views."""
    # The selected course already contains this page. Legacy catalogs use its
    # ID for a storage root; showing that root repeats the course as a chapter.
    course_roots = {unit['id'] for unit in units
                    if unit.get('parent_id') is None and unit['id'] in course_ids}
    members = {unit['id']: [item for item in unit['topic_ids'] if item in topic_ids] for unit in units}
    parents = {unit['id']: unit.get('parent_id') for unit in units}
    retained = set()
    for unit in units:
        current = unit['id'] if members[unit['id']] else None
        while current is not None and current not in retained:
            retained.add(current)
            current = parents[current]
    # Flat adjacency avoids recursion limits and deep JSON nesting. The browser
    # attaches child containers iteratively; catalog order remains the order.
    return [{'id': unit['id'], 'title': unit['title'],
             'parent_id': None if unit.get('parent_id') in course_roots else unit.get('parent_id'),
             'topic_ids': members[unit['id']]} for unit in units if unit['id'] in retained
            and unit['id'] not in course_roots]
