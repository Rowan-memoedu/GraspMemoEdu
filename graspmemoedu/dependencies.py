"""Read-only, shared prerequisite eligibility for lesson and review delivery."""
from contextlib import closing
from datetime import datetime
import json

from .service import DomainError, now
from .topic_runtime import is_introduction
from .topic_structure import atomic_state, has_atomic
from .dependency_nodes import group_key, group_parts
from .training_content import TrainingCatalog, permission_id


class DependencyPolicy:
    def __init__(self, catalog, access_store, subjects=None, training=None):
        self.catalog, self.access_store, self.subjects = catalog, access_store, subjects
        self.training = training or (TrainingCatalog(subjects.settings.data_dir) if subjects else None)

    def dependencies(self, course_id=None):
        if self.subjects is not None:
            from .course_graph import GraphStore
            root = self.subjects.settings.content_dir / 'graphs'
            signature = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(root.glob('*.json')))
            if getattr(self, '_graph_signature', None) != signature:
                self._graph_dependencies = GraphStore(self.subjects.settings, self.catalog, self.subjects, self.training).dependencies(course_id)
                self._graph_signature = signature
            dependencies = self._graph_dependencies
        else:
            dependencies = {tid: self.catalog.metadata(tid)['prerequisites'] for tid in self.catalog.topics}
        return dependencies

    def scope_members(self, topic_id):
        if group_parts(topic_id):
            return None
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
        if topic_id in self.catalog.topics and is_introduction(self.catalog.topic(topic_id)):
            return 0
        point_gate = self.group_point_status(con, access, topic_id, current_time)
        if access.get('is_admin'):
            return point_gate['missing_point_count']
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
            if group_parts(node):
                if node in visiting:
                    return False
                visiting.add(node)
                valid = (self.group_completed(con, access, node, current, tables)
                         and self.group_point_status(con, access, node, current.isoformat())['unlocked'])
                valid = valid and all(healthy(parent) for parent in dependencies.get(node, []))
                visiting.remove(node)
                checked[node] = bool(valid)
                return bool(valid)
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
            if valid and has_atomic(self.catalog.topic(node)):
                points = atomic_state(state)
                valid = not points.get('pause') and not any(p.get('due_at') and datetime.fromisoformat(p['due_at']) <= current
                                                           for p in points.get('points', {}).values())
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

        return point_gate['missing_point_count'] + sum(not healthy(parent) for parent in set(dependencies.get(topic_id, [])))

    def group_point_status(self, con, access, node_id, current_time=None):
        from .linked_points import snapshot, eligibility, bypass_mastery
        bank, group = self.group_content(node_id)
        if not group:
            return {'unlocked': True, 'points': [], 'missing_point_count': 0}
        policy = group.get('unlock_policy')
        refs = policy['point_ids'] if policy else list(dict.fromkeys(
            p for q in group.get('questions', []) for p in q.get('point_ids', [])))
        rows = snapshot(con, self.catalog, access['learner_id'], refs, access)
        return eligibility(rows, current_time or now(), policy, allow_unlearned=bypass_mastery(access))

    def group_content(self, node_id):
        parts = group_parts(node_id)
        bank = self.training.banks.get(parts[0]) if parts and self.training else None
        group = next((g for g in bank['groups'] if g['id'] == parts[1]), None) if bank else None
        return bank, group

    def group_completed(self, con, access, node_id, current, tables):
        bank, group = self.group_content(node_id)
        if (not group or not group.get('questions') or 'training_attempts' not in tables
                or permission_id(bank['id']) not in {t['id'] for t in access['topics']}):
            return False
        key = (access['learner_id'], bank['id'], bank['version'])
        questions = {q['id'] for q in group['questions']}
        completed = {row['question'] for row in con.execute('''SELECT DISTINCT question FROM training_attempts
            WHERE learner=? AND bank=? AND version=? AND group_id=? AND phase='done' ''', (*key, group['id']))}
        if not questions <= completed:
            return False
        if 'training_memory' in tables:
            stopped = {r['question'] for r in con.execute('''SELECT question FROM training_suspensions
                WHERE learner=? AND bank=? AND version=?''', key)} if 'training_suspensions' in tables else set()
            # Older unfinished rounds cannot block after a newer round updated the
            # same FSRS card. Match the current revision, as submission does.
            active = {r['question'] for r in con.execute('''SELECT a.question FROM training_attempts a
                JOIN training_memory m ON m.learner=a.learner AND m.bank=a.bank AND m.question=a.question
                    AND m.version=a.version AND m.revision=a.memory_revision
                WHERE a.learner=? AND a.bank=? AND a.version=? AND a.group_id=?
                    AND a.mode='review' AND a.phase!='done' ''', (*key, group['id']))}
            if active - stopped:
                return False
            for row in con.execute('SELECT question,mastered,due_at FROM training_memory WHERE learner=? AND bank=? AND version=?', key):
                if row['question'] in questions - stopped and (not row['mastered'] or datetime.fromisoformat(row['due_at']) <= current):
                    return False
        return True

    def describe(self, con, access, node_id):
        """Permission-filtered links for prerequisites, independent of their kind."""
        from urllib.parse import quote
        allowed = {t['id'] for t in access['topics']}
        if group_parts(node_id):
            bank, group = self.group_content(node_id)
            if not group or permission_id(bank['id']) not in allowed:
                return None
            return {'id': node_id, 'title': group['title'], 'kind': 'group',
                    'href': f"/subjects/{quote(bank['subject_id'])}/banks/{quote(bank['id'])}?groupId={quote(group['id'])}"}
        if node_id not in self.catalog.topics or node_id not in allowed:
            return None
        subject = self.subjects.topic_subject(node_id) if self.subjects else 'math'
        return {'id': node_id, 'title': self.catalog.topic(node_id)['title'], 'kind': 'topic',
                'href': f'/subjects/{quote(subject)}/topic/{quote(node_id)}'}

    def group_status(self, con, access, bank_id, group_id, current_time=None):
        node_id = group_key(bank_id, group_id)
        dependencies = self.dependencies()
        missing = self.missing_count(con, access, node_id, dependencies, current_time)
        details = [item for parent in dependencies.get(node_id, [])
                   if (item := self.describe(con, access, parent)) is not None]
        point_status = self.group_point_status(con, access, node_id, current_time)
        return {'dependency_ready': missing == 0, 'missing_prerequisite_count': missing,
                'unlocked': missing == 0, 'point_prerequisites': point_status['points'],
                'missing_point_count': point_status['missing_point_count'],
                'prerequisites': details, 'prerequisite_details': details}

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
        if has_atomic(topic) and not topic.get('modules'):
            ratings = [p['last_rating'] for p in atomic_state(state).get('points', {}).values() if p.get('last_rating') is not None]
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
