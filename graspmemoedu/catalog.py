"""External course metadata; topic content and versions stay in their source JSON."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .service import DomainError, LearningService


IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


class Catalog:
    def __init__(self, courses, topics, default_topic_id, topic_paths):
        self.courses = {course['id']: course for course in courses}
        self.topics = topics
        self.default_topic_id = default_topic_id
        self.topic_paths = topic_paths
        self.topic_courses = {tid: frozenset(c['id'] for c in courses
                             if any(t['id'] == tid for t in c['topics'])) for tid in topics}
        for tid, topic in topics.items():
            scope = topic.get('prerequisite_scope_course_id')
            if scope is not None and (not isinstance(scope, str) or scope not in self.topic_courses[tid]):
                raise ValueError('Prerequisite scope must be a course containing the Topic')
            if 'prerequisite_scope_topic_ids' in topic:
                members = topic['prerequisite_scope_topic_ids']
                if (not scope or not isinstance(members, list) or not members
                        or any(not isinstance(value, str) for value in members)
                        or len(set(members)) != len(members) or tid not in members
                        or not set(members) <= {row['id'] for row in self.courses[scope]['topics']}):
                    raise ValueError('Prerequisite Topic scope must belong to the selected course')

    @classmethod
    def load(cls, settings, fallback_topic=None):
        path = settings.catalog_path
        if not path.exists():
            topic = fallback_topic or json.loads(settings.course_path.read_text(encoding='utf-8'))
            cls._validate_topic(topic)
            course = {'id': 'arithmetic-revisited', 'title': '再探小学算术',
                      'description': '从熟悉的算术出发，用字母表达规律，重新理解计算。' if topic['id'] == 'digits-to-letters' else '',
                      'topics': [{'id': topic['id'], 'prerequisites': []}],
                      'units': [{'id': 'arithmetic-revisited', 'title': '再探小学算术', 'topic_ids': [topic['id']]}]}
            return cls([course], {topic['id']: topic}, topic['id'], {topic['id']: settings.course_path.resolve()})
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            courses, topics, topic_paths = [], {}, {}
            if not isinstance(raw, dict) or set(raw) != {'courses'} or not isinstance(raw['courses'], list) or not raw['courses']:
                raise ValueError('Catalog requires courses')
            course_ids = set()
            for value in raw['courses']:
                if not isinstance(value, dict) or set(value) - {'id', 'title', 'description', 'topics', 'units'}:
                    raise ValueError('Unknown course field')
                course_id = cls._identifier(value.get('id'))
                if course_id in course_ids:
                    raise ValueError('Duplicate course')
                course_ids.add(course_id)
                title = cls._text(value.get('title'), 200)
                description = value.get('description', '')
                if not isinstance(description, str) or len(description) > 2000:
                    raise ValueError('Invalid description')
                declarations = value.get('topics')
                if not isinstance(declarations, list):
                    raise ValueError('Topics must be a list')
                metadata = []
                for declaration in declarations:
                    if not isinstance(declaration, dict) or set(declaration) - {'id', 'path', 'prerequisites'}:
                        raise ValueError('Unknown topic field')
                    topic_id = cls._identifier(declaration.get('id'))
                    if any(t['id'] == topic_id for t in metadata):
                        raise ValueError('Duplicate course member')
                    source = Path(cls._text(declaration.get('path'), 2000))
                    if not source.is_absolute():
                        source = path.parent / source
                    if topic_id in topics:
                        if topic_paths[topic_id] != source.resolve():
                            raise ValueError('Shared Topic must reference the same source')
                        topic = topics[topic_id]
                    else:
                        topic = json.loads(source.read_text(encoding='utf-8'))
                        cls._validate_topic(topic)
                    if not isinstance(topic, dict) or topic.get('id') != topic_id:
                        raise ValueError('Topic source ID does not match catalog')
                    if topic_id in topics and (topics[topic_id] != topic or topic_paths[topic_id] != source.resolve()):
                        raise ValueError('Shared Topic must reference the same source')
                    prerequisites = declaration.get('prerequisites', [])
                    if not isinstance(prerequisites, list) or any(cls._identifier(item) == topic_id for item in prerequisites):
                        raise ValueError('Invalid prerequisites')
                    topics[topic_id] = topic
                    topic_paths[topic_id] = source.resolve()
                    metadata.append({'id': topic_id, 'prerequisites': list(dict.fromkeys(prerequisites))})
                ids = {topic['id'] for topic in metadata}
                units = value.get('units', [{'id': course_id, 'title': title, 'topic_ids': list(topic['id'] for topic in metadata)}])
                if not isinstance(units, list):
                    raise ValueError('Units must be a list')
                covered, unit_ids, validated_units = set(), set(), []
                for unit in units:
                    if (not isinstance(unit, dict) or set(unit) - {'id', 'title', 'topic_ids', 'parent_id'}
                            or not {'id', 'title', 'topic_ids'} <= set(unit)):
                        raise ValueError('Invalid unit')
                    unit_id = cls._identifier(unit['id'])
                    members = unit['topic_ids']
                    if (unit_id in unit_ids or not isinstance(members, list) or any(not isinstance(item, str) for item in members)
                            or len(members) != len(set(members)) or set(members) - ids or covered.intersection(members)):
                        raise ValueError('Duplicate or unknown unit topic')
                    unit_ids.add(unit_id)
                    covered.update(members)
                    validated_units.append({'id': unit_id, 'title': cls._text(unit['title'], 200), 'topic_ids': members})
                    if 'parent_id' in unit:
                        validated_units[-1]['parent_id'] = cls._identifier(unit['parent_id']) if unit['parent_id'] is not None else None
                if covered != ids:
                    raise ValueError('Units must cover each course topic exactly once')
                cls._validate_unit_tree(validated_units)
                courses.append({'id': course_id, 'title': title, 'description': description,
                                'topics': metadata, 'units': validated_units})
            if not topics:
                raise ValueError('Catalog has no topic content')
            for course in courses:
                ids = {topic['id'] for topic in course['topics']}
                if any(set(topic['prerequisites']) - topics.keys() for topic in course['topics']):
                    raise ValueError('Catalog prerequisites must reference imported topics')
                from .topic_runtime import is_introduction
                reading_ids = {tid for tid in ids if is_introduction(topics[tid])}
                if any((topic['id'] in reading_ids and topic['prerequisites'])
                       or reading_ids.intersection(topic['prerequisites']) for topic in course['topics']):
                    raise ValueError('Introduction Topics cannot participate in prerequisite relationships')
                graph = {tid: set() for tid in topics}
                for c in courses:
                    for item in c['topics']:
                        graph[item['id']].update(item['prerequisites'])
                visited, pending = set(), set()
                def visit(topic_id):
                    if topic_id in pending:
                        raise ValueError('Prerequisite cycle')
                    if topic_id in visited:
                        return
                    pending.add(topic_id)
                    for prerequisite in graph[topic_id]:
                        visit(prerequisite)
                    pending.remove(topic_id)
                    visited.add(topic_id)
                for topic_id in graph:
                    visit(topic_id)
            preferred = fallback_topic['id'] if fallback_topic else 'digits-to-letters'
            result = cls(courses, topics, preferred if preferred in topics else next(iter(topics)), topic_paths)
            result.content_dir = path.parent
            return result
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise DomainError('invalid_catalog', '课程目录格式或来源无效，请由管理者检查本地目录配置。', 422) from exc

    @staticmethod
    def _validate_unit_tree(units):
        parents = {unit['id']: unit.get('parent_id') for unit in units}
        visited = set()
        for unit_id in parents:
            path = set()
            current = unit_id
            while current is not None and current not in visited:
                if current not in parents:
                    raise ValueError('Unknown unit parent')
                if current in path:
                    raise ValueError('Unit hierarchy cycle')
                path.add(current)
                current = parents[current]
            visited.update(path)

    @staticmethod
    def _validate_topic(topic):
        Catalog._identifier(topic.get('id'))
        Catalog._text(topic.get('title'), 200)
        # Reuse the learning engine's pure content validation without creating
        # any learner progress merely because a catalog was opened.
        if topic.get('source_adapter') == 'mathacademy':
            from .source_content import validate_source_topic
            validate_source_topic(topic)
            if not topic['availability']['trainable']:
                return
        validator = object.__new__(LearningService)
        validator.course, validator.questions = topic, {}
        validator._validate_course()

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise ValueError('Invalid stable identifier')
        return value

    @staticmethod
    def _text(value, maximum):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError('Invalid text')
        return value.strip()

    def topic(self, topic_id):
        if topic_id not in self.topics:
            raise DomainError('unknown_topic', '未找到此 Topic。', 404)
        return self.topics[topic_id]

    def course(self, course_id):
        if course_id not in self.courses:
            raise DomainError('unknown_course', '未找到此课程。', 404)
        return self.courses[course_id]

    def course_context(self, topic_id, course_id=None):
        members = self.topic_courses[topic_id]
        if course_id is not None:
            if course_id not in members:
                raise DomainError('topic_course_mismatch', 'Topic 不属于当前课程。', 422)
            return self.courses[course_id]
        if len(members) == 1:
            return self.courses[next(iter(members))]
        return {'id': None, 'title': ' · '.join(self.courses[c]['title'] for c in sorted(members)),
                'course_ids': sorted(members)}

    def metadata(self, topic_id, course_id=None):
        members = self.topic_courses[topic_id]
        if course_id is not None:
            course = self.course_context(topic_id, course_id)
            return next(t for t in course['topics'] if t['id'] == topic_id)
        return {'id': topic_id, 'course_ids': sorted(members), 'prerequisites': sorted({p
                for cid in members for t in self.courses[cid]['topics'] if t['id'] == topic_id
                for p in t['prerequisites']})}
