"""Local subject presentation and course ownership, independent of permissions."""
import json
from pathlib import Path, PurePosixPath
import re

from .service import DomainError


DEFAULTS = (
    ('math', '数学', 'Mathematics', '理解规律，建立数学直觉。', 'Understand patterns and build mathematical intuition.', '#785744', '∑'),
    ('physics', '物理', 'Physics', '从现象出发，探索世界的规律。', 'Explore the laws behind the world around us.', '#456781', '⚛'),
    ('english', '英语', 'English', '用语言连接知识与世界。', 'Connect with knowledge and the world through language.', '#55766c', 'Aa'),
    ('chinese', '语文', 'Chinese', '在阅读与表达中理解与思考。', 'Read, reflect and express ideas with clarity.', '#915847', '文'),
    ('biology', '生物', 'Biology', '认识生命的结构与变化。', 'Discover the structures and processes of life.', '#647746', '❧'),
    ('chemistry', '化学', 'Chemistry', '探索物质的性质与变化。', 'Explore matter, its properties and transformations.', '#796080', '⚗'),
    ('cs', '计算机科学', 'Computer Science', '理解计算，构建解决问题的方法。', 'Understand computation and build ways to solve problems.', '#087f83', 'CS'),
)
PUBLIC_FIELDS = ('id', 'title', 'title_en', 'description', 'description_en', 'accent', 'emblem')


class Subjects:
    def __init__(self, settings, catalog, raw=None):
        self.settings, self.catalog = settings, catalog
        from .test_materials import TestMaterials
        self.test_materials = TestMaterials(settings.data_dir, catalog)
        self.subjects = {row[0]: dict(zip(PUBLIC_FIELDS, row)) for row in DEFAULTS}
        self.guide_paths = {'math': settings.guide_path}
        self.course_subjects = {}
        self.explicit_guides = set()
        if raw is None:
            source = settings.content_dir / 'subjects.json'
            try:
                if source.exists():
                    if source.stat().st_size > 1024 * 1024:
                        raise ValueError('Subject configuration too large')
                    raw = json.loads(source.read_text(encoding='utf-8-sig'))
                else:
                    raw = {'subjects': []}
            except (ValueError, OSError) as exc:
                raise DomainError('invalid_subjects', '学科配置格式不正确。', 422) from exc
        try:
            if not isinstance(raw, dict) or set(raw) != {'subjects'} or not isinstance(raw['subjects'], list):
                raise ValueError('Invalid subject configuration')
            seen = set()
            for item in raw['subjects']:
                if not isinstance(item, dict) or set(item) - {*PUBLIC_FIELDS, 'course_ids', 'guide_path'}:
                    raise ValueError('Unknown subject field')
                subject_id = item.get('id')
                if subject_id not in self.subjects or subject_id in seen:
                    raise ValueError('Unknown or duplicate subject')
                seen.add(subject_id)
                for key in PUBLIC_FIELDS[1:]:
                    if key in item:
                        value = item[key]
                        if not isinstance(value, str) or not value.strip() or len(value) > 2000 or any(ord(c) < 32 for c in value):
                            raise ValueError('Invalid subject presentation')
                        if key == 'accent' and not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
                            raise ValueError('Invalid subject color')
                        self.subjects[subject_id][key] = value
                ids = item.get('course_ids', [])
                if not isinstance(ids, list):
                    raise ValueError('Invalid course assignment')
                for course_id in ids:
                    if not isinstance(course_id, str) or course_id not in catalog.courses or course_id in self.course_subjects:
                        raise ValueError('Unknown or multiply assigned course')
                    self.course_subjects[course_id] = subject_id
                if 'guide_path' in item:
                    self.guide_paths[subject_id] = self.guide_location(settings.data_dir, item['guide_path'])
                    self.explicit_guides.add(subject_id)
            for course_id in catalog.courses:
                self.course_subjects.setdefault(course_id, 'math')
            for topic_id, topic in getattr(catalog, 'topics', {}).items():
                if topic.get('training_mode') == 'atomic_retrieval':
                    actual = self.topic_subject(topic_id)
                    if actual not in ('english', 'chinese', 'biology', 'chemistry') or topic.get('subject_id') != actual:
                        raise ValueError('Atomic topic does not match its permitted subject')
        except (ValueError, TypeError) as exc:
            raise DomainError('invalid_subjects', '学科配置中的课程、字段或指南路径不正确。', 422) from exc

    @staticmethod
    def guide_location(root, relative):
        if (not isinstance(relative, str) or not relative or len(relative) > 2000
                or any(c in relative for c in '\\:<>"|?*') or any(ord(c) < 32 for c in relative)
                or PurePosixPath(relative).is_absolute() or str(PurePosixPath(relative)) != relative
                or any(part in ('.', '..') for part in PurePosixPath(relative).parts)
                or PurePosixPath(relative).suffix.lower() != '.md'):
            raise ValueError('Guide must be a relative Markdown path')
        reserved = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$)(\.|$)', re.IGNORECASE)
        if any(part != part.rstrip(' .') or reserved.match(part) for part in PurePosixPath(relative).parts):
            raise ValueError('Invalid guide path component')
        candidate = Path(root) / relative
        if not candidate.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('Guide leaves data directory')
        return candidate

    def require(self, subject_id):
        if subject_id not in self.subjects:
            raise DomainError('unknown_subject', '未找到此学科。', 404)
        return self.subjects[subject_id]

    def course_ids(self, subject_id):
        self.require(subject_id)
        return {course for course, subject in self.course_subjects.items() if subject == subject_id}

    def authorize_course(self, subject_id, course_id):
        if subject_id is not None and course_id not in self.course_ids(subject_id):
            raise DomainError('subject_course_mismatch', '此课程不属于当前学科。', 403)

    def authorize_topic(self, subject_id, topic_id):
        if subject_id is not None and subject_id not in {self.course_subjects[c] for c in self.catalog.topic_courses[topic_id]}:
            raise DomainError('subject_course_mismatch', '此 Topic 不属于当前学科。', 403)

    def topic_subject(self, topic_id):
        subjects = {self.course_subjects[c] for c in self.catalog.topic_courses[topic_id]}
        if len(subjects) != 1:
            raise ValueError('Topic requires explicit subject context')
        return next(iter(subjects))

    def authorize_subject(self, access, subject_id):
        if subject_id is None:
            return
        self.require(subject_id)

    def public(self, access=None):
        return {'subjects': [{**subject, 'course_count': sum(self.test_materials.course_visible(self.catalog.courses[cid], access)
                                                            for cid in self.course_ids(subject_id)),
                             'available': True,
                             'requires_account': False}
                             for subject_id, subject in self.subjects.items()],
                'default_subject_id': 'math', 'brand': 'GraspMemoEdu'}
