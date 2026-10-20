"""Read an Academy student course and its author templates, preserving source order.

PyYAML is a preparation-only dependency. No learner files or status are changed.
Theory tasks require an explicit classification file; they are never silently made cards.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.atomic_content import plain_text
from graspmemoedu.catalog import Catalog
from graspmemoedu.topic_install import encoded


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def within(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Academy path leaves source root')
    return path


def template(raw, placeholders):
    """Academy offsets are JVM UTF-16 code units, not Python code points."""
    data = raw.decode('utf-8-sig').encode('utf-16-le')
    patches, metadata, shift, previous = [], [], 0, 0
    for item in sorted(placeholders, key=lambda p: p['offset']):
        start, end = item['offset'], item['offset'] + item['length']
        if start < previous or end * 2 > len(data):
            raise ValueError('Invalid/overlapping Academy placeholder')
        value = item['placeholder_text'].encode('utf-16-le')
        patches.append((start, end, value))
        metadata.append({'offset': start + shift, 'length': len(value) // 2})
        shift += len(value) // 2 - (end - start)
        previous = end
    for start, end, value in reversed(patches):
        data = data[:start * 2] + value + data[end * 2:]
    return data.decode('utf-16-le').encode('utf-8'), metadata


def prepare(student, author, course_id, classifications, output, local_manifest):
    student, author, output = Path(student).resolve(), Path(author).resolve(), Path(output).resolve()
    Catalog._identifier(course_id)
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'manifest.json').exists(): raise ValueError('Prepared package already exists; do not overwrite it')
    if any(p.name not in ('render-input.json', 'rendered.json', 'topics') for p in output.iterdir()):
        raise ValueError('Output contains unrelated files')
    baseline, tasks, units, bindings = {}, [], [], {}
    def read(path):
        raw = path.read_bytes(); baseline[path] = digest(raw); return raw
    def metadata(path):
        return yaml.safe_load(read(path).decode('utf-8-sig'))
    course_meta = metadata(student / 'course-info.yaml')
    course = {'id': course_id, 'title': course_meta['title'], 'topics': [], 'units': units}
    def traverse(relative='', parent=None):
        folder = within(student, relative)
        config = next((folder / f'{kind}-info.yaml' for kind in ('course', 'section', 'lesson')
                       if (folder / f'{kind}-info.yaml').is_file()), None)
        if config is None: raise ValueError('Missing Academy directory metadata: ' + relative)
        meta = metadata(config)
        for name in meta.get('content', []):
            rel = '/'.join(filter(None, [relative, name]))
            path = within(student, rel)
            task_config = path / 'task-info.yaml'
            if not task_config.exists():
                child = metadata(next(p for p in [path / 'section-info.yaml', path / 'lesson-info.yaml'] if p.exists()))
                uid = course_id + '-unit-' + digest(rel.encode())[:16]
                units.append({'id': uid, 'title': child.get('custom_name') or name,
                              'parent_id': parent, 'topic_ids': []})
                traverse(rel, uid)
                continue
            data = metadata(task_config)
            if data['type'] == 'edu': kind = 'automated'
            elif data['type'] == 'theory':
                kind = classifications.get(rel)
                if kind not in ('manual', 'reading'): raise ValueError('Classify theory task explicitly: ' + rel)
            else: raise ValueError('Unsupported Academy task type: ' + data['type'])
            task_key = course_id + '-task-' + digest(rel.encode())[:20]
            title = data.get('custom_name') or name
            source = path / 'task.md'
            if not source.exists(): raise ValueError('Expected source Markdown: ' + rel)
            markdown = read(source).decode('utf-8-sig')
            heading = re.search(r'^#\s+(.+)$', markdown, re.M)
            if heading: title = heading[1]
            tasks.append({'id': task_key, 'title': title, 'source': str(source), 'markdown': markdown, 'kind': kind})
            course['topics'].append({'id': task_key, 'path': 'topics/' + task_key + '.json', 'prerequisites': []})
            if parent is None:
                uid = course_id + '-root'
                if not any(u['id'] == uid for u in units): units.append({'id': uid, 'title': course['title'], 'topic_ids': []})
                next(u for u in units if u['id'] == uid)['topic_ids'].append(task_key)
            else: next(u for u in units if u['id'] == parent)['topic_ids'].append(task_key)
            if kind == 'reading': continue
            original = within(author, rel)
            original_meta = metadata(original / 'task-info.yaml')
            files = []
            for item in original_meta.get('files', []):
                if not item.get('visible', True) or item.get('is_binary'): continue
                name = item['name']
                raw, placeholders = template(read(within(original, name)), item.get('placeholders', []))
                if not within(path, name).is_file(): raise ValueError('Student template file missing: ' + name)
                files.append({'path': name, 'template': base64.b64encode(raw).decode('ascii'),
                              'sha256': digest(raw), 'placeholders': placeholders})
            bindings[task_key] = {'project_root': str(student), 'task_path': rel, 'kind': kind, 'files': files}
    traverse()
    source_json = output / 'render-input.json'
    source_json.write_bytes(encoded(tasks))
    subprocess.run(['node', str(Path(__file__).with_name('render_academy.mjs')), str(source_json), str(output / 'rendered.json')], check=True)
    rendered = json.loads((output / 'rendered.json').read_text(encoding='utf-8'))
    for item, presentation in zip(tasks, rendered, strict=True):
        tid, html = item['id'], presentation['html']
        source_hash = digest(item['markdown'].encode('utf-8'))
        topic = {'id': tid, 'title': item['title'], 'subject_id': 'cs', 'source_sha256': source_hash,
                 'math_css': presentation['css'], 'modules': [], 'academy_source': {'course_id': course_id,
                 'task_key': tid, 'markdown': item['markdown'], 'kind': item['kind']}}
        if item['kind'] == 'reading':
            topic.update(topic_format_version=2, topic_kind='introduction', training_mode='paradigm',
                         content_version=source_hash, introductions=[{'id': 'instructions', 'title': item['title'], 'html': html}],
                         content_order=[{'kind': 'introduction', 'id': 'instructions'}])
        else:
            quote = plain_text(html)
            note = '按题目要求在 IDEA 完成练习。' if item['kind'] == 'automated' else '完成题目中的操作与解释要求后自主评分。'
            topic.update(training_mode='atomic_retrieval', academy={'task_key': tid, 'kind': item['kind']},
                         introduction={'html': html},
                         memory_chunks=[{'id': 'exercise', 'title': item['title'], 'html': html,
                             'anchors': [{'start': 0, 'end': len(quote), 'quote': quote}],
                             'points': [{'id': 'task', 'text': item['title']}]}],
                         flashcards=[{'id': 'task', 'title': item['title'], 'question': item['markdown'], 'html': html,
                             'point_ids': ['task'], 'reference_answer': note, 'explanation_html': '<p>' + note + '</p>',
                             'answer_type': 'text', 'constraints': note}])
        topic['version'] = digest(encoded(topic))
        Catalog._validate_topic(topic)
        destination = output / 'topics' / (tid + '.json'); destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(encoded(topic))
        if tid in bindings: bindings[tid]['version'] = topic['version']
    for path, expected in baseline.items():
        if digest(path.read_bytes()) != expected: raise ValueError('Source changed during preparation; retry: ' + str(path))
    # The public package has no project paths, previous solutions or encrypted answers.
    report = {'course_id': course_id, 'topics': len(tasks), 'automated': sum(t['kind'] == 'automated' for t in tasks),
              'manual': sum(t['kind'] == 'manual' for t in tasks), 'reading': sum(t['kind'] == 'reading' for t in tasks),
              'source_files_verified': len(baseline), 'hierarchy_preserved': True, 'learner_progress_imported': False}
    (output / 'catalog.json').write_bytes(encoded({'courses': [course]}))
    (output / 'report.json').write_bytes(encoded(report))
    names = ['catalog.json', 'report.json', *['topics/' + t['id'] + '.json' for t in tasks]]
    (output / 'manifest.json').write_bytes(encoded({'schema': 1, 'adapter': 'jetbrains-academy',
        'files': {name: digest((output / name).read_bytes()) for name in names}}))
    local_manifest = Path(local_manifest); local_manifest.parent.mkdir(parents=True, exist_ok=True)
    if local_manifest.exists(): raise ValueError('Local manifest exists; inspect before updating')
    local_manifest.write_bytes(encoded({'schema': 1, 'tasks': bindings}))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('student', 'author', 'course-id', 'classification', 'output', 'local-manifest'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    prepare(a.student, a.author, a.course_id, json.loads(Path(a.classification).read_text(encoding='utf-8')),
            a.output, a.local_manifest)
