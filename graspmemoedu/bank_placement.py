"""Move a catalog container to the question bank without changing topics or memories."""
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


def place_in_question_bank(data_dir, container_id):
    data_dir = Path(data_dir).resolve()
    if (data_dir / 'cloud-authoritative.json').exists():
        raise ValueError('Do not modify the production rollback snapshot')
    with import_lock(data_dir):
        settings = Settings.load(data_dir)
        old = Catalog.load(settings)
        container = old.course(container_id)
        if container.get('placement') == 'question_bank':
            return {'status': 'unchanged', 'bank_id': container_id, 'learning_records_changed': False}
        pointer = (data_dir / POINTER).read_bytes() if (data_dir / POINTER).exists() else None
        old_root = settings.content_dir
        baseline = {p: p.read_bytes() for p in (settings.catalog_path, old_root / 'subjects.json')}
        extras = {p.relative_to(old_root): p.read_bytes() for p in
                  [old_root / 'test-materials.json', *sorted((old_root / 'graphs').glob('*.json'))] if p.exists()}
        topics_before = {tid: hashlib.sha256(p.read_bytes()).hexdigest() for tid, p in old.topic_paths.items()}
        courses = deepcopy(list(old.courses.values()))
        for course in courses:
            if course['id'] == container_id:
                course['placement'] = 'question_bank'
            for topic in course['topics']:
                topic['path'] = str(old.topic_paths[topic['id']])
        body = encoded({'courses': courses})
        generation = hashlib.sha256(body + (pointer or b'')).hexdigest()
        batch = data_dir / 'content-batches' / generation
        batch.mkdir(parents=True, exist_ok=False)
        (batch / 'catalog.json').write_bytes(body)
        (batch / 'subjects.json').write_bytes(baseline[old_root / 'subjects.json'])
        for name, raw in extras.items():
            (batch / name).parent.mkdir(parents=True, exist_ok=True)
            (batch / name).write_bytes(raw)
        if (old_root / 'mathacademy-sources').exists():
            shutil.copytree(old_root / 'mathacademy-sources', batch / 'mathacademy-sources')
        staged = replace(settings, catalog_path=batch / 'catalog.json')
        staged.content_dir = batch
        loaded = Catalog.load(staged)
        subjects = Subjects(staged, loaded)
        from .course_graph import GraphStore
        GraphStore(staged, loaded, subjects).all()
        if (any(p.read_bytes() != raw for p, raw in baseline.items())
                or any((old_root / p).read_bytes() != raw for p, raw in extras.items())
                or topics_before != {tid: hashlib.sha256(p.read_bytes()).hexdigest() for tid, p in loaded.topic_paths.items()}
                or ((data_dir / POINTER).read_bytes() if (data_dir / POINTER).exists() else None) != pointer):
            raise ValueError('Content changed concurrently; placement not activated')
        history = data_dir / 'content-activation-history'
        history.mkdir(exist_ok=True)
        (history / (generation + '.json')).write_bytes(encoded(
            {'previous': json.loads(pointer) if pointer else None, 'generation': generation, 'bank_id': container_id}))
        pending = data_dir / (POINTER + '.bank-pending')
        with pending.open('xb') as stream:
            stream.write(encoded({'schema': 1, 'generation': generation}))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, data_dir / POINTER)
        return {'status': 'placed_in_question_bank', 'bank_id': container_id, 'generation': generation,
                'topic_contents_unchanged': len(topics_before), 'learning_records_changed': False}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--container-id', required=True)
    args = parser.parse_args()
    print(json.dumps(place_in_question_bank(args.data_dir, args.container_id)))
