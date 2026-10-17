"""Restore source-provided inline TeX without reconverting teaching content."""
import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil

from .mathacademy_import import Assets, file_hash, production_install, source_fingerprint
from .topic_install import digest, encoded, import_lock

IMAGE = re.compile(r'<img\b[^>]*>', re.I)
SRC = re.compile(r'\bsrc=["\']([^"\']*)["\']', re.I)
KIND = 'mathacademy-inline-graphics'


def fields(topic, source):
    """Yield paired source and existing rendered fields by stable teaching identity."""
    intros = {v['id']: i for i, v in enumerate(topic['introductions'])}
    examples, questions = {}, {}
    for i, module in enumerate(topic['modules']):
        examples[module['example']['id']] = ['modules', i, 'example']
        for j, q in enumerate(module['practices']):
            questions[q['id']] = ['modules', i, 'practices', j]
    for i, q in enumerate(topic.get('unassigned_questions', [])):
        questions[q['id']] = ['unassigned_questions', i]
    for step in source['lesson']['steps']:
        kind = step['type']; body = step.get(kind) or {}
        if kind == 'tutorial':
            path = ['introductions', intros[f'ma-tutorial-{body["id"]}']]
            yield path + ['html'], body.get('content', ''), body.get('html', '')
        elif kind in ('example', 'question'):
            path = (examples if kind == 'example' else questions)[f'ma-{kind}-{body["id"]}']
            yield path + ['html'], body.get('question' if kind == 'example' else 'text', ''), body.get('questionHtml', '')
            yield path + ['explanation_html'], body.get('explanation', ''), body.get('explanationHtml', '')


def get(value, path):
    for key in path:
        value = value[key]
    return value


def put(value, path, replacement):
    get(value, path[:-1])[path[-1]] = replacement


def remove_gaps(topic, restored):
    pending = Counter(restored)
    gaps = []
    for gap in topic['_source_gaps']:
        ref = gap.get('reference')
        if gap['code'] == 'image_missing' and pending[ref]:
            pending[ref] -= 1
        else:
            gaps.append(gap)
    if any(pending.values()):
        raise ValueError('Restored images do not match recorded missing references')
    topic['_source_gaps'] = gaps
    remaining = sum(g['code'] == 'image_missing' for g in gaps)
    reasons = []
    for reason in topic['availability']['reasons']:
        if reason['code'] != 'image_missing':
            reasons.append(reason)
        elif remaining:
            reasons.append({**reason, 'count': remaining})
    topic['availability']['reasons'] = reasons


def commented_gaps(topic, source):
    """HTML comments are omitted by the renderer and cannot be missing visible images."""
    missing = Counter(g.get('reference') for g in topic['_source_gaps'] if g['code'] == 'image_missing')
    ignored = []
    for _path, _raw, source_html in fields(topic, source):
        for comment in re.findall(r'<!--[\s\S]*?-->', source_html or ''):
            for image in IMAGE.finditer(comment):
                ref = SRC.search(image[0])
                if ref and missing[ref[1]]:
                    ignored.append(ref[1]); missing[ref[1]] -= 1
    return ignored


def repair_topic(topic, source, assets, failures=None):
    if topic.get('source_adapter') != 'mathacademy' or topic['version'] != source_fingerprint(source):
        raise ValueError('Source identity or teaching version changed')
    result, changes, restored = deepcopy(topic), [], []
    for path, raw, source_html in fields(topic, source):
        drawing = assets.inline_graphics(raw, source_html)
        if not drawing:
            continue
        original = get(topic, path)
        source_images = list(IMAGE.finditer(Assets.visible_markup(source_html)))
        rendered_images = list(IMAGE.finditer(original))
        if len(source_images) != len(rendered_images):
            raise ValueError('Rendered image sequence differs from its source field')
        replacements, proofs = {}, []
        for i, (source_image, rendered_image) in enumerate(zip(source_images, rendered_images)):
            ref = SRC.search(source_image[0])
            current = SRC.search(rendered_image[0])
            if not ref or ref[1] not in drawing or (current and current[1]):
                continue
            reference = ref[1]; tex = drawing[reference]
            try:
                url = assets.compile_inline(tex)
            except RuntimeError as error:
                if failures is None or not str(error).startswith('TeX graphic conversion failed:'):
                    raise
                failures.append({'topic_id': topic['id'], 'path': path, 'reference': reference,
                                 'tex_sha256': digest(tex.encode()), 'error': str(error)})
                continue
            tag = SRC.sub('', rendered_image[0]).replace('alt="原材料图片缺失"', 'alt="原图"')
            tag = re.sub(r'\s*/?>$', ' src="' + url + '" />', tag)
            replacements[i] = tag
            proofs.append({'image_index': i, 'reference': reference, 'tex_sha256': digest(tex.encode()),
                           'png_sha256': digest(base64.b64decode(url.split(',', 1)[1], validate=True))})
            restored.append(reference)
        if replacements:
            counter = iter(range(len(rendered_images)))
            fixed = IMAGE.sub(lambda m: replacements.get(next(counter), m[0]), original)
            put(result, path, fixed)
            changes.append({'path': path, 'images': proofs})
    remove_gaps(result, restored + commented_gaps(topic, source))
    validate_patch(topic, result, source, changes)
    return result, changes


def validate_patch(before, after, source, changes):
    """Only empty image tags and their exact missing-image counters may change."""
    if before.get('source_adapter') != 'mathacademy' or before['version'] != source_fingerprint(source):
        raise ValueError('Source differs from installed teaching content')
    expected = deepcopy(before); restored = []
    originals = {tuple(path): (raw, rendered) for path, raw, rendered in fields(before, source)}
    seen = set()
    for change in changes:
        path = change['path']; key = tuple(path)
        if key in seen or key not in originals:
            raise ValueError('Duplicate or unknown graphics field')
        seen.add(key)
        old, new = get(before, path), get(after, path)
        old_images, new_images = list(IMAGE.finditer(old)), list(IMAGE.finditer(new))
        raw, rendered = originals[key]
        source_images = list(IMAGE.finditer(Assets.visible_markup(rendered)))
        drawings = Assets.inline_graphics(raw, rendered)
        if len(old_images) != len(new_images) or len(source_images) != len(old_images):
            raise ValueError('Image sequence changed')
        replacements = {}
        for proof in change['images']:
            i = proof['image_index']
            if type(i) is not int or i < 0 or i >= len(old_images) or i in replacements:
                raise ValueError('Invalid image position')
            ref = SRC.search(source_images[i][0])
            old_src, new_src = SRC.search(old_images[i][0]), SRC.search(new_images[i][0])
            if not ref or ref[1] != proof['reference'] or ref[1] not in drawings or (old_src and old_src[1]):
                raise ValueError('Image is not an empty source-provided inline drawing')
            if digest(drawings[ref[1]].encode()) != proof['tex_sha256']:
                raise ValueError('Drawing source changed')
            if not new_src or not new_src[1].startswith('data:image/png;base64,'):
                raise ValueError('Restored drawing must be a local PNG')
            png = base64.b64decode(new_src[1].split(',', 1)[1], validate=True)
            if not png.startswith(b'\x89PNG\r\n\x1a\n') or digest(png) != proof['png_sha256']:
                raise ValueError('Restored PNG hash mismatch')
            expected_tag = SRC.sub('', old_images[i][0]).replace('alt="原材料图片缺失"', 'alt="原图"')
            expected_tag = re.sub(r'\s*/?>$', ' src="' + new_src[1] + '" />', expected_tag)
            if new_images[i][0] != expected_tag:
                raise ValueError('Unexpected image attributes changed')
            replacements[i] = expected_tag; restored.append(ref[1])
        counter = iter(range(len(old_images)))
        put(expected, path, IMAGE.sub(lambda m: replacements.get(next(counter), m[0]), old))
    remove_gaps(expected, restored + commented_gaps(before, source))
    if expected != after:
        raise ValueError('Graphics repair changed unrelated content or learning identity')


def prepare_repairs(source_package, course_ids, cache, output):
    from .catalog import Catalog
    root, output = Path(source_package), Path(output)
    catalog = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
    selected = {f'ma-course-{cid}' for cid in course_ids}
    if not selected <= {c['id'] for c in catalog['courses']}:
        raise ValueError('Unknown selected course')
    tids = sorted({t['id'] for c in catalog['courses'] if c['id'] in selected for t in c['topics']})
    output.mkdir(parents=True, exist_ok=True); (output / 'topics').mkdir(exist_ok=True)
    assets = Assets(Path(cache) / 'graphics', {})
    entries, occurrence_count, references, failures = [], 0, set(), []
    def convert(tid):
        before_path = root / 'topics' / (tid + '.json')
        source_path = root / 'mathacademy-sources/topics' / (tid + '.json')
        before = json.loads(before_path.read_text(encoding='utf-8'))
        source = json.loads(source_path.read_text(encoding='utf-8'))
        local_failures = []
        after, changes = repair_topic(before, source, assets, local_failures)
        return tid, before_path, source_path, after, changes, local_failures, after != before
    with ThreadPoolExecutor(max_workers=4) as executor:
        for position, (tid, before_path, source_path, after, changes, local_failures, changed) in enumerate(executor.map(convert, tids)):
            failures.extend(local_failures)
            if changed:
                Catalog._validate_topic(after)
                path = 'topics/' + tid + '.json'; (output / path).write_bytes(encoded(after))
                entries.append({'topic_id': tid, 'path': path, 'before_sha256': file_hash(before_path),
                    'after_sha256': file_hash(output / path), 'source_sha256': file_hash(source_path), 'changes': changes})
                for change in changes:
                    occurrence_count += len(change['images'])
                    references.update(p['reference'] for p in change['images'])
            print(f'graphics repair {position + 1}/{len(tids)} changed={len(entries)} restored={occurrence_count} unrenderable={len(failures)}', flush=True)
    manifest = {'schema': 1, 'kind': KIND, 'courses': sorted(selected), 'topics_checked': len(tids),
                'topics': entries, 'restored_occurrences': occurrence_count, 'restored_references': len(references),
                'unrenderable_drawings': failures}
    (output / 'manifest.json').write_bytes(encoded(manifest))
    return {k: v for k, v in manifest.items() if k not in ('topics', 'unrenderable_drawings')} | {
        'topics_changed': len(entries), 'unrenderable_occurrences': len(failures)}


def install_repairs(package, data_dir, *, fail_at=None):
    from .catalog import Catalog
    from .config import Settings
    from .content_bundle import POINTER
    from .course_graph import GraphStore
    from .subjects import Subjects
    from .test_materials import TestMaterials
    package, data = Path(package).resolve(), Path(data_dir).resolve()
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or manifest.get('kind') != KIND or not manifest.get('topics'):
        raise ValueError('Invalid source graphics repair package')
    if (data / 'cloud-authoritative.json').exists():
        raise ValueError('The local rollback snapshot cannot receive repairs')
    with import_lock(data):
        settings = Settings.load(data); root = settings.content_dir
        pointer = data / POINTER; previous = pointer.read_bytes()
        old = Catalog.load(settings); policy = TestMaterials(data, old)
        selected = {t['id'] for cid in manifest['courses'] for t in old.course(cid)['topics']}
        entries = {e['topic_id']: e for e in manifest['topics']}
        if len(entries) != len(manifest['topics']) or not set(entries) <= selected or not set(entries) <= policy.topics:
            raise ValueError('Repair exceeds selected test course material')
        if all(file_hash(old.topic_paths[tid]) == e['after_sha256'] for tid, e in entries.items()):
            return {'status': 'unchanged', 'generation': json.loads(previous)['generation'], 'topics_changed': 0}
        for tid, entry in entries.items():
            path = package / entry['path']
            if entry['path'] != 'topics/' + tid + '.json' or path.is_symlink() or not path.resolve().is_relative_to(package):
                raise ValueError('Invalid graphics repair path')
            if file_hash(path) != entry['after_sha256'] or file_hash(old.topic_paths[tid]) != entry['before_sha256']:
                raise ValueError('Prepared or installed content changed: ' + tid)
            source_path = root / 'mathacademy-sources/topics' / (tid + '.json')
            if file_hash(source_path) != entry['source_sha256']:
                raise ValueError('Installed original source changed: ' + tid)
            validate_patch(old.topics[tid], json.loads(path.read_text(encoding='utf-8')),
                           json.loads(source_path.read_text(encoding='utf-8')), entry['changes'])
        generation = digest(previous + encoded(manifest)); batch = data / 'content-batches' / generation
        batch.mkdir(parents=True, exist_ok=True)
        for folder in ('graphs', 'mathacademy-sources'):
            for source_path in (root / folder).rglob('*'):
                if source_path.is_file():
                    target = batch / source_path.relative_to(root); target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        try:
                            os.link(source_path, target)
                        except OSError:
                            shutil.copyfile(source_path, target)
        for name in ('subjects.json', 'test-materials.json'):
            shutil.copyfile(root / name, batch / name)
        for entry in entries.values():
            target = batch / entry['path']; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package / entry['path'], target)
        catalog = json.loads(settings.catalog_path.read_text(encoding='utf-8'))
        for course in catalog['courses']:
            for topic in course['topics']:
                tid = topic['id']
                topic['path'] = entries[tid]['path'] if tid in entries else os.path.relpath(old.topic_paths[tid], batch).replace('\\', '/')
        (batch / 'catalog.json').write_bytes(encoded(catalog))
        (batch / 'graphics-repair.json').write_bytes(encoded(manifest))
        staged = Settings(data_dir=data, catalog_path=batch / 'catalog.json'); staged.content_dir = batch
        candidate = Catalog.load(staged); subjects = Subjects(staged, candidate)
        GraphStore(staged, candidate, subjects).all()
        if old.courses != candidate.courses or any(old.topics[tid] != candidate.topics[tid] for tid in old.topics if tid not in entries):
            raise ValueError('Repair changed unrelated courses or topics')
        if pointer.read_bytes() != previous:
            raise ValueError('Active content changed concurrently')
        history = data / 'content-activation-history'; history.mkdir(exist_ok=True)
        (history / (generation + '.json')).write_bytes(encoded({'previous': json.loads(previous), 'generation': generation, 'kind': KIND}))
        if fail_at == 'activate':
            raise RuntimeError('Injected pre-activation failure')
        pending = data / (POINTER + '.tmp')
        pending.write_bytes(encoded({'schema': 1, 'generation': generation})); os.replace(pending, pointer)
        try:
            loaded = Catalog.load(Settings.load(data))
            if any(file_hash(loaded.topic_paths[tid]) != entry['after_sha256'] for tid, entry in entries.items()):
                raise RuntimeError('Graphics repair readback mismatch')
        except Exception:
            pending.write_bytes(previous); os.replace(pending, pointer)
            raise
        receipt = {'status': 'graphics_restored', 'generation': generation, 'kind': KIND,
                   'topics_changed': len(entries), 'restored_occurrences': manifest['restored_occurrences'],
                   'restored_references': manifest['restored_references'], 'test_only': True,
                   'package_sha256': file_hash(package / 'manifest.json')}
        (batch / 'installation.json').write_bytes(encoded(receipt))
        return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-package'); parser.add_argument('--course-ids', nargs='+', type=int)
    parser.add_argument('--cache', default='D:/Caches/GraspMemoEdu/mathacademy')
    parser.add_argument('--output'); parser.add_argument('--install-package')
    parser.add_argument('--data-dir'); parser.add_argument('--target', choices=['local', 'production'], default='local')
    args = parser.parse_args()
    if args.install_package:
        if args.target == 'local' and not args.data_dir:
            parser.error('--data-dir is required for isolated local installation')
        result = production_install(args.install_package) if args.target == 'production' else install_repairs(args.install_package, args.data_dir)
    else:
        if not args.source_package or not args.course_ids or not args.output:
            parser.error('--source-package, --course-ids and --output are required')
        result = prepare_repairs(args.source_package, args.course_ids, args.cache, args.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
