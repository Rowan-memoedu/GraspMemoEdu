"""Add a prepared Academy course through the existing atomic content pointer."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil

from .catalog import Catalog
from .config import Settings
from .content_bundle import POINTER
from .subjects import Subjects
from .topic_install import encoded, import_lock


def contains_extension(old, incoming, package):
    current = old.courses.get(incoming['id'])
    if not current or any(current.get(k, '') != incoming.get(k, '') for k in ('title', 'description')):
        return False
    topics = {t['id']: t for t in current['topics']}
    for t in incoming['topics']:
        if (t['id'] not in topics or topics[t['id']] != {k: v for k, v in t.items() if k != 'path'}
                or old.topics[t['id']] != json.loads((package / t['path']).read_bytes())):
            return False
    units = {u['id']: u for u in current.get('units', [])}
    for u in incoming.get('units', []):
        previous = units.get(u['id'])
        if (previous is None or {k: v for k, v in previous.items() if k != 'topic_ids'} !=
                {k: v for k, v in u.items() if k != 'topic_ids'}
                or not set(u.get('topic_ids', [])) <= set(previous.get('topic_ids', []))):
            return False
    return True


def install(package, data_dir, *, extend_course=False):
    package, data_dir = Path(package).resolve(), Path(data_dir).resolve()
    if (data_dir / 'cloud-authoritative.json').exists():
        raise ValueError('Do not install into the local production rollback snapshot')
    manifest = json.loads((package / 'manifest.json').read_bytes())
    if manifest.get('adapter') not in ('jetbrains-academy', 'hyperskill') or manifest.get('schema') != 1:
        raise ValueError('Invalid Academy package')
    for name, expected in manifest['files'].items():
        path = package / name
        if (path.is_symlink() or not path.resolve().is_relative_to(package)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError('Package hash/path mismatch')
    candidate = json.loads((package / 'catalog.json').read_bytes())
    with import_lock(data_dir):
        settings = Settings.load(data_dir)
        old = Catalog.load(settings)
        old_root = settings.content_dir
        pointer = (data_dir / POINTER).read_bytes() if (data_dir / POINTER).exists() else None
        baseline = {p: p.read_bytes() for p in [settings.catalog_path, old_root / 'subjects.json']}
        extra_config = {p: p.read_bytes() for p in [old_root / 'test-materials.json', *sorted((old_root / 'graphs').glob('*.json'))] if p.exists()}
        existing = {c['id'] for c in candidate['courses']} & old.courses.keys()
        if extend_course and all(contains_extension(old, c, package) for c in candidate['courses']):
            return {'status': 'unchanged', 'learning_records_changed': False}
        if existing:
            if all(c['id'] in existing and old.courses[c['id']] == {**c, 'description': c.get('description', ''),
                    'topics': [{k: v for k, v in t.items() if k != 'path'} for t in c['topics']]}
                   and all(old.topics[t['id']] == json.loads((package / t['path']).read_bytes()) for t in c['topics'])
                   for c in candidate['courses']):
                return {'status': 'unchanged', 'learning_records_changed': False}
            if not extend_course:
                raise ValueError('An existing course must not be replaced by the importer')
        if not extend_course and any(t['id'] in old.topics for c in candidate['courses'] for t in c['topics']):
            raise ValueError('Topic identity collision')
        generation = hashlib.sha256(encoded(manifest) + (pointer or b'') + b''.join(baseline.values())).hexdigest()
        batch = data_dir / 'content-batches' / generation
        batch.mkdir(parents=True, exist_ok=False)
        courses = deepcopy(list(old.courses.values()))
        for course in courses:
            for topic in course['topics']: topic['path'] = str(old.topic_paths[topic['id']])
        for name in manifest['files']:
            if name in ('catalog.json', 'report.json'): continue
            target = batch / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package / name, target)
        for incoming in candidate['courses']:
            current = next((c for c in courses if c['id'] == incoming['id']), None)
            if current is None:
                if any(t['id'] in old.topics for t in incoming['topics']):
                    raise ValueError('Topic identity collision')
                courses.append(incoming)
                continue
            if not extend_course or any(current.get(k, '') != incoming.get(k, '') for k in ('title', 'description')):
                raise ValueError('Existing course metadata must be preserved')
            known_topics = {t['id']: t for t in current['topics']}
            for declaration in incoming['topics']:
                if declaration['id'] in old.topics:
                    previous = known_topics.get(declaration['id'])
                    if (previous is None or old.topics[declaration['id']] != json.loads((package / declaration['path']).read_bytes())
                            or {k: v for k, v in previous.items() if k != 'path'} !=
                            {k: v for k, v in declaration.items() if k != 'path'}):
                        raise ValueError('An existing Topic cannot be changed by a course extension')
                else:
                    current['topics'].append(declaration)
            known_units = {u['id']: u for u in current.get('units', [])}
            for unit in incoming.get('units', []):
                previous = known_units.get(unit['id'])
                if previous is None:
                    current.setdefault('units', []).append(unit)
                else:
                    if ({k: v for k, v in previous.items() if k != 'topic_ids'} !=
                            {k: v for k, v in unit.items() if k != 'topic_ids'}):
                        raise ValueError('Existing source hierarchy must be preserved')
                    previous['topic_ids'] = list(dict.fromkeys(unit.get('topic_ids', []) + previous.get('topic_ids', [])))
        (batch / 'catalog.json').write_bytes(encoded({'courses': courses}))
        subjects = json.loads(baseline[old_root / 'subjects.json'])
        cs = next((s for s in subjects['subjects'] if s['id'] == 'cs'), None)
        if cs is None: cs = {'id': 'cs', 'course_ids': []}; subjects['subjects'].append(cs)
        cs.setdefault('course_ids', []).extend(c['id'] for c in candidate['courses'] if c['id'] not in cs['course_ids'])
        (batch / 'subjects.json').write_bytes(encoded(subjects))
        policy_path = old_root / 'test-materials.json'
        policy = json.loads(policy_path.read_bytes()) if policy_path.exists() else {'schema': 1}
        for kind, ids in manifest.get('test_materials', {}).items():
            if kind not in ('courses', 'topics'):
                raise ValueError('Unsupported package test-material kind')
            policy[kind] = list(dict.fromkeys(policy.get(kind, []) + ids))
        if policy_path.exists() or manifest.get('test_materials'):
            from .test_materials import validate
            (batch / 'test-materials.json').write_bytes(encoded(validate(policy)))
        for folder in ('graphs', 'mathacademy-sources'):
            if (old_root / folder).exists(): shutil.copytree(old_root / folder, batch / folder)
        staged = replace(settings, catalog_path=batch / 'catalog.json'); staged.content_dir = batch
        loaded = Catalog.load(staged); Subjects(staged, loaded)
        from .course_graph import GraphStore
        GraphStore(staged, loaded, Subjects(staged, loaded)).all()
        current_extra = {p: p.read_bytes() for p in [old_root / 'test-materials.json', *sorted((old_root / 'graphs').glob('*.json'))] if p.exists()}
        if any(p.read_bytes() != value for p, value in baseline.items()) or current_extra != extra_config or ((data_dir / POINTER).read_bytes() if (data_dir / POINTER).exists() else None) != pointer:
            raise ValueError('Content changed concurrently; no activation performed')
        history = data_dir / 'content-activation-history'; history.mkdir(exist_ok=True)
        (history / (generation + '.json')).write_bytes(encoded({'previous': json.loads(pointer) if pointer else None, 'generation': generation}))
        pending = data_dir / (POINTER + '.academy-pending')
        with pending.open('xb') as stream:
            stream.write(encoded({'schema': 1, 'generation': generation})); stream.flush(); os.fsync(stream.fileno())
        os.replace(pending, data_dir / POINTER)
        final = Catalog.load(Settings.load(data_dir))
        assert all(final.topics[t['id']] == loaded.topics[t['id']] for c in candidate['courses'] for t in c['topics'])
        return {'status': 'installed', 'courses': [c['id'] for c in candidate['courses']],
                'topics': sum(len(c['topics']) for c in candidate['courses']), 'generation': generation, 'learning_records_changed': False}


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--package', required=True); p.add_argument('--data-dir', required=True)
    p.add_argument('--extend-course', action='store_true')
    args = p.parse_args()
    print(json.dumps(install(args.package, args.data_dir, extend_course=args.extend_course), ensure_ascii=False))
