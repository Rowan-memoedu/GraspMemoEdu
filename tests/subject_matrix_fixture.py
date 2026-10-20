"""Identical synthetic feature inventory in every registered subject."""
import base64
from copy import deepcopy
from pathlib import Path

from graspmemoedu.subject_registry import DEFAULTS
from graspmemoedu.topic_install import encoded
from graspmemoedu.training_content import fingerprint
from tests.fixtures import synthetic_course
from tests.atomic_fixtures import atomic_topic
from tests.training_fixtures import training_bank
from tests.test_academy import exercise
from tests.test_hyperskill import topic as hyperskill_topic
from tests.test_unified_learning import composed_topic


def write_matrix(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'training-banks').mkdir(exist_ok=True)
    courses, subjects, topics = [], [], {}
    for subject, *_ in DEFAULTS:
        declarations = []
        for kind, value in (
            ('lesson', synthetic_course(1)), ('atomic', atomic_topic()),
            ('composed', composed_topic()), ('academy', exercise()), ('hyperskill', hyperskill_topic()),
        ):
            value = deepcopy(value)
            value.update(id=kind + '-' + subject, subject_id=subject, title='Matrix ' + kind)
            if value.get('academy'):
                value['academy']['task_key'] = 'matrix-' + subject
            topics[value['id']] = value
            path = value['id'] + '.json'
            (root / path).write_bytes(encoded(value))
            declarations.append({'id': value['id'], 'path': path})
        courses.append({'id': 'course-' + subject, 'title': 'Matrix course', 'topics': declarations,
                        'units': [{'id': 'chapter', 'title': 'Matrix chapter',
                                   'topic_ids': [t['id'] for t in declarations]}]})
        folder = root / 'guides' / subject
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'guide.md').write_text('# Matrix guide\n\nShared text.\n\n![pixel](pixel.png)', encoding='utf-8')
        (folder / 'pixel.png').write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/mWQAAAAASUVORK5CYII='))
        subjects.append({'id': subject, 'course_ids': ['course-' + subject],
                         'guide_path': 'guides/' + subject + '/guide.md'})
        bank = training_bank(subject)
        bank.update(id='bank-' + subject, title='Matrix bank')
        bank['version'] = fingerprint(bank)
        (root / 'training-banks' / (bank['id'] + '.json')).write_bytes(encoded(bank))
    (root / 'catalog.json').write_bytes(encoded({'courses': courses}))
    (root / 'subjects.json').write_bytes(encoded({'subjects': subjects}))
    return topics
