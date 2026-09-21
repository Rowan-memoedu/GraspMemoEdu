"""Install approved packages as one new course; retain old courses and identities."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from math_learning.catalog import Catalog
from math_learning.config import Settings
from math_learning.course_graph import GraphStore
from math_learning.subjects import Subjects
from math_learning.topic_install import encoded, import_lock, install_topic, read_package


def install_course(settings, plan):
    course_id = Catalog._identifier(plan['course_id'])
    subject_id, title = plan['subject_id'], Catalog._text(plan['title'], 200)
    catalog = Catalog.load(settings)
    subjects = Subjects(settings, catalog)
    subjects.require(subject_id)
    if course_id in catalog.courses:
        raise ValueError('Course already exists; this command only adds a new course')
    if not plan['topics']:
        raise ValueError('Course needs reviewed Topics')
    topic_ids = []
    for entry in plan['topics']:
        _, _, topic = read_package(entry['package'])
        if topic['id'] != entry['topic_id'] or topic['id'] in catalog.topics or topic['id'] in topic_ids:
            raise ValueError('Conflicting Topic identity')
        GraphStore(settings, catalog, subjects).validate_topic_target(topic['id'], subject_id, course_id)
        topic_ids.append(topic['id'])
    subjects_path = settings.data_dir / 'subjects.json'
    before_catalog, before_subjects = settings.catalog_path.read_bytes(), subjects_path.read_bytes()
    proposed = json.loads(before_catalog)
    for course in proposed['courses']:
        for topic in course['topics']:
            topic['path'] = str(catalog.topic_paths[topic['id']])
    proposed['courses'].append({'id': course_id, 'title': title, 'description': '', 'topics': []})
    scope = json.loads(before_subjects)
    next(s for s in scope['subjects'] if s['id'] == subject_id)['course_ids'].append(course_id)
    with tempfile.TemporaryDirectory(prefix='.course-stage-', dir=settings.data_dir) as temporary:
        stage = Path(temporary)
        (stage / 'catalog.json').write_bytes(encoded(proposed))
        (stage / 'subjects.json').write_bytes(encoded(scope))
        staged = replace(settings, data_dir=stage, catalog_path=stage / 'catalog.json')
        for entry in plan['topics']:
            install_topic(staged, entry['package'], subject_id=subject_id, course_id=course_id)
            print(json.dumps({'validated_topic': entry['topic_id']}, ensure_ascii=False), flush=True)
        installed = json.loads((stage / 'catalog.json').read_bytes())['courses'][-1]
        # Retain exact existing declarations, including relative paths.
        final_catalog = json.loads(before_catalog)
        final_catalog['courses'].append(installed)
        with import_lock(settings.data_dir):
            if settings.catalog_path.read_bytes() != before_catalog or subjects_path.read_bytes() != before_subjects:
                raise ValueError('Configuration changed while preparing the course')
            for topic_id in topic_ids:
                GraphStore(settings, catalog, subjects).validate_topic_target(topic_id, subject_id, course_id)
            target = settings.data_dir / 'imported-topics'
            target.mkdir(exist_ok=True)
            if target.is_symlink() or not target.resolve().is_relative_to(settings.data_dir.resolve()):
                raise ValueError('Invalid permanent import destination')
            if any((target / topic_id).exists() for topic_id in topic_ids):
                raise ValueError('An import directory already exists; inspect it without overwriting')
            backup = settings.data_dir / 'course-imports' / course_id
            backup.mkdir(parents=True, exist_ok=False)
            (backup / 'catalog-before.json').write_bytes(before_catalog)
            (backup / 'subjects-before.json').write_bytes(before_subjects)
            for topic in installed['topics']:
                origin = stage / 'imported-topics' / topic['id']
                destination = target / topic['id']
                index = json.loads((origin / 'topic.sources.json').read_bytes())
                for key in ('source_path', 'answers_path', 'review_path'):
                    if key in index:
                        index[key] = str(destination / Path(index[key]).name)
                (origin / 'topic.sources.json').write_bytes(encoded(index))
                os.replace(origin, destination)
                topic['path'] = str(destination / 'topic.json')
            pending = settings.catalog_path.with_name(settings.catalog_path.name + '.course-pending')
            subject_pending = subjects_path.with_name(subjects_path.name + '.course-pending')
            # Exclusive staging prevents overwriting another pending operation.
            created = []
            try:
                for path, value in ((pending, final_catalog), (subject_pending, scope)):
                    with path.open('xb') as stream:
                        created.append(path)
                        stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
                pending_catalog = Catalog.load(replace(settings, catalog_path=pending))
                Subjects(settings, pending_catalog, scope).authorize_course(subject_id, course_id)
                try:
                    os.replace(pending, settings.catalog_path)
                    os.replace(subject_pending, subjects_path)
                    Subjects(settings, Catalog.load(settings)).authorize_course(subject_id, course_id)
                except BaseException:
                    settings.catalog_path.write_bytes(before_catalog)
                    subjects_path.write_bytes(before_subjects)
                    raise
            finally:
                for path in created:
                    if path.exists():
                        path.unlink()
    return {'status': 'installed', 'course_id': course_id, 'title': title, 'topics': len(topic_ids),
            'reload_required': True, 'learning_records_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True, help='JSON course metadata and topics with package paths')
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(install_course(Settings.load(args.data_dir), json.loads(args.plan.read_text(encoding='utf-8'))),
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
