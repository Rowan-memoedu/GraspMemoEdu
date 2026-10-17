"""Retained test content, visible only to qualified test accounts."""
import json
from pathlib import Path
import re

from .service import DomainError

FILENAME = 'test-materials.json'
KINDS = ('courses', 'topics', 'banks', 'guides')
MAX_BYTES = 1024 * 1024


def validate(raw):
    if (not isinstance(raw, dict) or type(raw.get('schema')) is not int or raw['schema'] != 1
            or set(raw) - {'schema', *KINDS}):
        raise ValueError('Invalid test material policy')
    for key in KINDS:
        values = raw.get(key, [])
        if (not isinstance(values, list) or len(values) > 10000
                or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', v) for v in values)
                or len(values) != len(set(values))):
            raise ValueError('Invalid test material IDs')
        if key == 'guides' and set(values) - {'math', 'physics', 'english', 'chinese', 'biology', 'chemistry'}:
            raise ValueError('Unknown test guide subject')
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

    @staticmethod
    def allowed(access):
        return bool(access and access.get('role') == 'account' and access.get('purpose') == 'test' and
                    (access.get('is_admin') or (access.get('is_advanced_learner') and access.get('is_student'))))

    def topic_visible(self, topic_id, access):
        return self.allowed(access) or topic_id not in self.permission_ids

    def snapshot_visible(self, topic_id, access):
        return topic_id not in self.permission_ids or (self.allowed(access) and
            (access.get('is_admin') or topic_id in {t['id'] for t in access.get('topics', [])}))

    def course_visible(self, course, access):
        return self.allowed(access) or (course['id'] not in self.courses
            and (not course['topics'] or any(t['id'] not in self.topics for t in course['topics'])))

    def graph_topic_visible(self, course_id, topic_id, access):
        return self.allowed(access) or (course_id not in self.courses and topic_id not in self.topics)

    def require_guide(self, subject_id, access):
        if subject_id in self.guides and not self.allowed(access):
            raise DomainError('guide_unavailable', '该学科指南暂未开放。', 404)
