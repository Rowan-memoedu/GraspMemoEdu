"""Install an approved author package into an existing course, without replacing Topics."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .catalog import Catalog
from .subjects import Subjects


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


@contextmanager
def import_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / '.author-import.lock'
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('Another import is active. If it crashed, inspect .author-import.lock before removing it.')
    try:
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink()


def read_package(package):
    root = Path(package).resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') not in ('approved', 'mechanically_validated') or not isinstance(manifest.get('files'), dict):
        raise ValueError('Only a validated, hashed author package can be installed')
    artifacts = manifest.get('artifacts', {})
    values = {}
    for key in ('source', 'answers', 'review', 'topic', 'audit'):
        name = artifacts.get(key)
        if not isinstance(name, str) or Path(name).name != name or any(c in name for c in '\\/:'):
            raise ValueError('Invalid package artifact path')
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Artifact leaves package directory')
        raw = path.read_bytes()
        if len(raw) > 32 * 1024 * 1024 or digest(raw) != manifest['files'].get(name):
            raise ValueError('Package artifact changed after validation: ' + key)
        values[key] = raw
    topic = json.loads(values['topic'])
    topic_id = Catalog._identifier(topic.get('id'))
    if topic_id != manifest.get('topic_id') or topic.get('source_sha256') != digest(values['source']):
        raise ValueError('Package identity or source differs from the reviewed Topic')
    Catalog._validate_topic(topic)
    from .topic_assets import validate_assets, read_local_asset
    assets = manifest.get('assets', [])
    review = json.loads(values['review'])
    if manifest.get('status') == 'mechanically_validated' or manifest.get('review_mode') == 'mechanical' or review.get('mode') == 'mechanical':
        if review.get('status') != 'mechanically_validated' or review.get('mode') != 'mechanical':
            raise ValueError('Mechanical package must preserve mechanical validation evidence')
        from .site_backup import _validate_review
        _validate_review(topic, values['source'], values['answers'], values['review'], assets)
        audit = json.loads(values['audit'])
        count = sum(1 + len(module['practices']) for module in topic['modules'])
        if (audit.get('status') != 'mechanically_validated' or audit.get('method') != 'mechanical-import'
                or audit.get('verified_answer_count') != 0
                or audit.get('mechanically_mapped_answer_count') != count
                or audit.get('semantic_review_performed') is not False
                or audit.get('mathematical_correctness_checked') is not False):
            raise ValueError('Mechanical audit must not claim semantic or mathematical verification')
    if review.get('assets', []) != assets:
        raise ValueError('Review assets differ from the approved package')
    values['assets'] = validate_assets(assets, lambda name: read_local_asset(root, name), source=values['source'].decode('utf-8-sig'))
    for name, raw in values['assets'].items():
        if manifest['files'].get(name) != digest(raw):
            raise ValueError('Asset is not covered by the package manifest')
    return manifest, values, topic


def install_topic(settings, package, *, subject_id, course_id, unit_id=None):
    """The final catalog replace is the only activation point; no DB or permission writes."""
    manifest, values, topic = read_package(package)
    topic_id = topic['id']
    with import_lock(settings.data_dir):
        catalog = Catalog.load(settings)
        subjects = Subjects(settings, catalog)
        subjects.authorize_course(subject_id, course_id)
        catalog.course(course_id)
        from .course_graph import GraphStore
        GraphStore(settings, catalog, subjects).validate_topic_target(topic_id, subject_id, course_id)
        if topic_id in catalog.topics:
            if catalog.topics[topic_id] == topic and catalog.topic_courses[topic_id] == course_id:
                return {'status': 'already_installed', 'topic_id': topic_id, 'course_id': course_id,
                        'subject_id': subject_id, 'reload_required': True, 'permissions_changed': False}
            raise ValueError('Topic ID already exists; existing progress/content will not be overwritten')
        if not settings.catalog_path.is_file():
            raise ValueError('Create an explicit catalog.json before installing author content')
        before = settings.catalog_path.read_bytes()
        raw_catalog = json.loads(before)
        course = next(item for item in raw_catalog['courses'] if item['id'] == course_id)
        if 'units' in course:
            matches = [unit for unit in course['units'] if unit['id'] == unit_id] if unit_id else course['units']
            if len(matches) != 1:
                raise ValueError('Specify --unit for a course with multiple units')
            matches[0]['topic_ids'].append(topic_id)
        elif unit_id is not None:
            raise ValueError('This course has no explicitly configured units')
        imports = settings.data_dir / 'imported-topics'
        imports.mkdir(exist_ok=True)
        if not imports.resolve().is_relative_to(settings.data_dir.resolve()):
            raise ValueError('Import destination leaves data directory')
        destination = imports / topic_id
        if destination.is_symlink():
            raise ValueError('Import destination must not be a symbolic link')
        record = {'topic_id': topic_id, 'course_id': course_id, 'subject_id': subject_id,
                  'source_sha256': topic['source_sha256'], 'course_version': topic['version']}
        with tempfile.TemporaryDirectory(prefix='.author-stage-', dir=imports) as temporary:
            stage = Path(temporary)
            names = {'source': 'source.md', 'answers': 'answers.json', 'review': 'review.json',
                     'audit': 'prepared-audit.json'}
            for key, name in names.items():
                (stage / name).write_bytes(values[key])
            for name, raw in values['assets'].items():
                asset = stage / name
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.write_bytes(raw)
            converter = Path(__file__).resolve().parents[1] / 'scripts/import_course.mjs'
            env = {**os.environ, 'GRASPMEMOEDU_PYTHON': sys.executable}
            result = subprocess.run(['node', str(converter), str(stage / 'source.md'), str(stage / 'answers.json'),
                                     str(stage / 'topic.json'), str(stage / 'audit.json'), str(stage / 'review.json')],
                                    capture_output=True, text=True, encoding='utf-8', timeout=180, env=env,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('Package failed independent conversion at installation: ' + result.stderr[-1500:])
            if json.loads((stage / 'topic.json').read_text(encoding='utf-8')) != topic:
                raise ValueError('Installed fields differ from the reviewed source conversion')
            (stage / 'topic.sources.json').write_bytes(encoded({
                'source_path': str(destination / 'source.md'), 'answers_path': str(destination / 'answers.json'),
                'review_path': str(destination / 'review.json'), 'review_sha256': digest(values['review']),
                'source_sha256': topic['source_sha256'], **({'assets': manifest['assets']} if values['assets'] else {})}))
            (stage / 'import.json').write_bytes(encoded(record))
            (stage / 'catalog-before.json').write_bytes(before)
            if destination.exists():
                # A process may have stopped after placing files but before activating the catalog.
                prior = json.loads((destination / 'import.json').read_text(encoding='utf-8'))
                if prior != record or any((destination / name).read_bytes() != (stage / name).read_bytes()
                                          for name in ('source.md', 'answers.json', 'review.json', 'topic.json', 'topic.sources.json', *values['assets'])):
                    raise ValueError('Unactivated import directory conflicts; inspect it without overwriting')
            else:
                # Copy to a permanent directory; it remains unreachable until catalog commit.
                shutil.copytree(stage, destination)
        course['topics'].append({'id': topic_id, 'path': str(destination / 'topic.json'), 'prerequisites': []})
        pending = settings.catalog_path.with_name(settings.catalog_path.name + '.author-pending')
        created_pending = False
        try:
            with pending.open('xb') as stream:
                created_pending = True
                stream.write(encoded(raw_catalog)); stream.flush(); os.fsync(stream.fileno())
            from dataclasses import replace
            staged = replace(settings, catalog_path=pending)
            loaded = Catalog.load(staged)
            Subjects(staged, loaded).authorize_topic(subject_id, topic_id)
            if settings.catalog_path.read_bytes() != before:
                raise ValueError('Catalog changed during import; retry after reviewing concurrent edits')
            os.replace(pending, settings.catalog_path)
        finally:
            if created_pending and pending.exists():
                pending.unlink()
        return {'status': 'installed', **record, 'topic_path': str(destination / 'topic.json'),
                'reload_required': True, 'permissions_changed': False,
                'catalog_backup': str(destination / 'catalog-before.json')}
