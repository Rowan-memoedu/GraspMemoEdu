"""Iterative course containment projection, independent of prerequisite edges."""


def pending_hierarchy(units, tasks):
    pending = {task['topic_id'] for task in tasks if task['type'] == 'Lesson'}
    members = {unit['id']: [item for item in unit['topic_ids'] if item in pending] for unit in units}
    parents = {unit['id']: unit.get('parent_id') for unit in units}
    retained = set()
    for unit in units:
        current = unit['id'] if members[unit['id']] else None
        while current is not None and current not in retained:
            retained.add(current)
            current = parents[current]
    # Flat adjacency avoids recursion limits and deep JSON nesting. The browser
    # attaches child containers iteratively; catalog order remains the order.
    return [{'id': unit['id'], 'title': unit['title'], 'parent_id': unit.get('parent_id'),
             'topic_ids': members[unit['id']]} for unit in units if unit['id'] in retained]
