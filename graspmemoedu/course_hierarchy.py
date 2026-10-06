"""Iterative course containment projection, independent of prerequisite edges."""


def pending_hierarchy(units, tasks):
    pending = {task['topic_id'] for task in tasks if task['type'] == 'Lesson'}
    course_ids = {task.get('course_id') for task in tasks}
    return project_hierarchy(units, pending, course_ids)


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
