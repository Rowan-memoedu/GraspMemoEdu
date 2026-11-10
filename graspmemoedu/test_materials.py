"""Retained test content with qualified testers and explicit account grants."""
import json
from pathlib import Path
import re

from .service import DomainError
from .subject_registry import SUBJECT_IDS

FILENAME = 'test-materials.json'
KINDS = ('courses', 'topics', 'banks', 'guides')
GRANT_KINDS = ('courses', 'topics', 'banks')
MAX_BYTES = 1024 * 1024


def validate(raw):
    if (not isinstance(raw, dict) or type(raw.get('schema')) is not int or raw['schema'] != 1
            or set(raw) - {'schema', *KINDS, 'account_grants'}):
        raise ValueError('Invalid test material policy')
    for key in KINDS:
        values = raw.get(key, [])
        if (not isinstance(values, list) or len(values) > 10000
                or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', v) for v in values)
                or len(values) != len(set(values))):
            raise ValueError('Invalid test material IDs')
        if key == 'guides' and set(values) - SUBJECT_IDS:
            raise ValueError('Unknown test guide subject')
    grants = raw.get('account_grants', {})
    if not isinstance(grants, dict) or len(grants) > 10000:
        raise ValueError('Invalid account material grants')
    for learner_id, grant in grants.items():
        if (not isinstance(learner_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', learner_id)
                or not isinstance(grant, dict) or set(grant) - set(GRANT_KINDS)):
            raise ValueError('Invalid account material grant')
        validate({'schema': 1, **grant})
    return raw


class TestMaterials:
    def __init__(self, data_dir, catalog=None):
        from .content_bundle import content_root
        path = getattr(catalog, 'content_dir', None) or content_root(data_dir)
        path = path / FILENAME
        try:
            if path.exists() or path.is_symlink():
                if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
                    raise ValueError('Invalid test material policy file')
                raw = validate(json.loads(path.read_text(encoding='utf-8-sig')))
            else:
                raw = {'schema': 1}
        except (ValueError, OSError) as error:
            raise DomainError('invalid_test_materials', '测试材料配置无效，请由管理员检查；未开放这些内容。', 422) from error
        self.courses, self.topics, self.banks, self.guides = (set(raw.get(k, [])) for k in KINDS)
        if catalog:
            for course in catalog.courses.values():
                if course['id'] in self.courses:
                    self.topics.update(t['id'] for t in course['topics'])
        self.permission_ids = self.topics | {'training.' + bank for bank in self.banks}
        self.account_courses = {}
        self.account_permissions = {}
        for learner_id, grant in raw.get('account_grants', {}).items():
            courses = set(grant.get('courses', []))
            permissions = set(grant.get('topics', [])) | {'training.' + bank for bank in grant.get('banks', [])}
            if catalog:
                for course in catalog.courses.values():
                    if course['id'] in courses:
                        permissions.update(t['id'] for t in course['topics'])
            self.account_courses[learner_id] = courses
            self.account_permissions[learner_id] = permissions

    def _granted(self, topic_id, access):
        return bool(access and access.get('role') == 'account' and
                    topic_id in self.account_permissions.get(access.get('learner_id'), ()))

    @staticmethod
    def allowed(access):
        return bool(access and access.get('role') == 'account' and access.get('purpose') == 'test' and
                    (access.get('is_admin') or (access.get('is_advanced_learner') and access.get('is_student'))))

    def topic_visible(self, topic_id, access):
        return self.allowed(access) or topic_id not in self.permission_ids or self._granted(topic_id, access)

    def snapshot_visible(self, topic_id, access):
        return topic_id not in self.permission_ids or ((self.allowed(access) or self._granted(topic_id, access)) and
            (access.get('is_admin') or topic_id in {t['id'] for t in access.get('topics', [])}))

    def course_visible(self, course, access):
        granted_course = bool(access and access.get('role') == 'account' and
            course['id'] in self.account_courses.get(access.get('learner_id'), ()))
        return self.allowed(access) or granted_course or any(self._granted(t['id'], access) for t in course['topics']) or (
            course['id'] not in self.courses and (not course['topics'] or any(t['id'] not in self.topics for t in course['topics'])))

    def graph_topic_visible(self, course_id, topic_id, access):
        return self.allowed(access) or self._granted(topic_id, access) or (course_id not in self.courses and topic_id not in self.topics)

    def require_guide(self, subject_id, access):
        if subject_id in self.guides and not self.allowed(access):
            raise DomainError('guide_unavailable', '该学科指南暂未开放。', 404)
