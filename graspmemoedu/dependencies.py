"""Read-only, shared prerequisite eligibility for lesson and review delivery."""
from contextlib import closing
from datetime import datetime
import json

from .service import DomainError, now
from .topic_runtime import is_introduction


class DependencyPolicy:
    def __init__(self, catalog, access_store, subjects=None):
        self.catalog, self.access_store, self.subjects = catalog, access_store, subjects

    def dependencies(self, course_id=None):
        if self.subjects is not None:
            from .course_graph import GraphStore
            root = self.subjects.settings.content_dir / 'graphs'
            signature = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(root.glob('*.json')))
            if getattr(self, '_graph_signature', None) != signature:
                self._graph_dependencies = GraphStore(self.subjects.settings, self.catalog, self.subjects).dependencies(course_id)
                self._graph_signature = signature
            dependencies = self._graph_dependencies
        else:
            dependencies = {tid: self.catalog.metadata(tid)['prerequisites'] for tid in self.catalog.topics}
        return dependencies

    def scope_members(self, topic_id):
        topic = self.catalog.topic(topic_id)
        scope = topic.get('prerequisite_scope_course_id')
        return (set(topic.get('prerequisite_scope_topic_ids',
                    [row['id'] for row in self.catalog.courses[scope]['topics']])) if scope else None)

    def required_parents(self, topic_id, dependencies=None):
        dependencies = dependencies if dependencies is not None else self.dependencies()
        members = self.scope_members(topic_id)
        return [parent for parent in dependencies.get(topic_id, []) if members is None or parent in members]

    @staticmethod
    def tables(con):
        return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def ready(self, con, access, topic_id, dependencies=None, current_time=None):
        return self.missing_count(con, access, topic_id, dependencies, current_time) == 0

    def missing_count(self, con, access, topic_id, dependencies=None, current_time=None):
        if is_introduction(self.catalog.topic(topic_id)):
            return 0
        if access.get('is_admin'):
            return 0
        dependencies = dependencies if dependencies is not None else self.dependencies()
        # Scope belongs to the requested root. Filtering the cached graph would
        # also grant this exception to ordinary downstream Topics.
        members = self.scope_members(topic_id)
        if members is not None:
            dependencies = {node: [parent for parent in parents if parent in members]
                            for node, parents in dependencies.items()}
        allowed = {topic['id'] for topic in access['topics']}
        tables, checked, visiting = self.tables(con), {}, set()
        current = datetime.fromisoformat(current_time or now())

        def healthy(node):
            if node in checked:
                return checked[node]
            if (node in visiting or node not in allowed or node not in self.catalog.topics
                    or not self.catalog.topic(node).get('availability', {}).get('trainable', True)):
                return False
            visiting.add(node)
            key = (access['learner_id'], node)
            version = self.catalog.topic(node)['version']
            row = con.execute('SELECT body,version FROM topics WHERE learner=? AND topic=?', key).fetchone()
            valid = bool(row and row['version'] == version)
            state = json.loads(row['body']) if valid else {}
            skipped = state.get('skipped_modules', {})
            skip_allowed = bool(skipped and state.get('skip_downstream_allowed'))
            valid = valid and (state.get('status') == 'completed' or skip_allowed and state.get('initial_completed_at'))
            valid = valid and not state.get('pause') and (not skipped or skip_allowed)
            if valid and state.get('training_mode') == 'atomic_retrieval':
                valid = not state.get('pause') and not any(p.get('due_at') and datetime.fromisoformat(p['due_at']) <= current
                                                         for p in state['points'].values())
            if valid and 'review_topic_pauses' in tables:
                valid = con.execute('SELECT 1 FROM review_topic_pauses WHERE learner=? AND topic=?', key).fetchone() is None
            if valid and 'review_sessions' in tables:
                # Paused historical rounds remain after unlocking; the pause table
                # above is authoritative for current pauses.
                valid = con.execute("SELECT 1 FROM review_sessions WHERE learner=? AND topic=? AND version=? AND status='in_progress'",
                                    (*key, version)).fetchone() is None
            if valid and 'review_cards' in tables:
                valid = not any(datetime.fromisoformat(card['due_at']) <= current for card in con.execute(
                    'SELECT due_at FROM review_cards WHERE learner=? AND topic=? AND version=?', (*key, version)))
            valid = valid and all(healthy(parent) for parent in dependencies.get(node, []))
            visiting.remove(node)
            checked[node] = bool(valid)
            return bool(valid)

        return sum(not healthy(parent) for parent in set(dependencies.get(topic_id, [])))

    def is_ready(self, access, topic_id):
        with closing(self.access_store._connect()) as con:
            con.execute('BEGIN')
            return self.ready(con, access, topic_id)

    def require(self, access, topic_id):
        if not self.is_ready(access, topic_id):
            raise DomainError('prerequisites_not_ready',
                              '请先完成前置知识的学习和待复习内容，并解除前置知识的暂停状态。', 409)

    def mastery(self, con, learner_id, topic_id, state):
        topic = self.catalog.topic(topic_id)
        if topic.get('training_mode') == 'atomic_retrieval':
            ratings = [p['last_rating'] for p in (state or {}).get('points', {}).values() if p.get('last_rating') is not None]
            return round(sum(ratings) / len(ratings), 2) if ratings else None
        modules = {module['id'] for module in topic['modules']}
        scores = {}
        def record(module, score, completed_at):
            if module in modules and score is not None and (module not in scores or completed_at >= scores[module][0]):
                scores[module] = (completed_at, score)
        for attempt in (state or {}).get('attempts', []):
            if attempt['status'] == 'completed':
                record(attempt['module_id'], attempt.get('mastery'), attempt.get('completed_at') or '')
        if 'review_ratings' in self.tables(con):
            for row in con.execute('SELECT module,mastery,completed_at FROM review_ratings WHERE learner=? AND topic=? AND version=? ORDER BY completed_at,rowid',
                                   (learner_id, topic_id, topic['version'])):
                record(row['module'], row['mastery'], row['completed_at'])
        return round(sum(item[1] for item in scores.values()) / len(scores), 2) if scores else None
