"""Append deterministic test courses to five non-math subjects through the real importer.

Default is a dry run. Use --data-dir <absolute directory> --apply to install.
Existing content and subject overrides are never replaced; conflicting files abort.
"""
from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.subjects import Subjects
from scripts.verify_content import collect_questions, parse_outline, sha256

SUBJECTS = {'physics': '物理', 'english': '英语', 'chinese': '语文', 'biology': '生物', 'chemistry': '化学'}
REPO = Path(__file__).resolve().parents[1]


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def source_bundle(subject, name):
    lines = [f'- 【测试】{name}基础练习', '  - Introduction',
             '    - 本内容只用于测试学习、补题与复习流程，不代表正式学科课程。']
    for module in range(1, 3):
        lines += [f'  - Example {module}: {name}测试模块', '    - 计算：', '', '      $$20+22$$',
                  '    - Explanation', '      - 两个数相加，结果是 42。']
        for practice in range(1, 16):
            lines += [f'    - Practice {practice}', '      - 计算：', '', f'        $${practice}+{42-practice}$$',
                      '      - Explanation', '        - 两个数相加，结果是 42。']
    source = '\n'.join(lines) + '\n'
    answers = {q['id']: {'question_sha256': sha256(q['question']), 'reference_answer': '42',
                        'answer_type': 'integer', 'constraints': '只填写计算结果。'}
               for q in collect_questions(parse_outline(source))}
    return source.encode('utf-8'), encoded({'course_id': f'test-{subject}-topic',
        'source_sha256': sha256(source.encode('utf-8')), 'expected_module_count': 2,
        'expected_question_count': 32, 'expected_practices_per_module': 15, 'answers': answers, 'errata': []})


def initialize(data_dir, *, apply=False, node='node'):
    root = Path(data_dir).resolve()
    if not Path(data_dir).is_absolute() or not root.is_dir():
        raise ValueError('An existing absolute data directory is required')
    settings = Settings.load(root)
    current = Catalog.load(settings)
    Subjects(settings, current)  # Validate old configuration before planning.
    catalog_path, subjects_path = settings.catalog_path, root / 'subjects.json'
    old_catalog = catalog_path.read_bytes() if catalog_path.exists() else None
    old_subjects = subjects_path.read_bytes() if subjects_path.exists() else None
    if old_catalog:
        catalog = json.loads(old_catalog)
    else:
        catalog = {'courses': [dict(course, topics=[dict(topic, path=str(current.topic_paths[topic['id']]))
                              for topic in course['topics']]) for course in current.courses.values()]}
    subjects = json.loads(old_subjects) if old_subjects else {'subjects': []}
    files = {}
    cache = Path('D:/Artifacts/GraspMemoEdu/test-runs') if os.name == 'nt' else Path(tempfile.gettempdir())
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='subject-import-', dir=cache) as temporary:
        stage = Path(temporary)
        for subject, name in SUBJECTS.items():
            topic_id, course_id = f'test-{subject}-topic', f'test-{subject}-course'
            relative = Path('test-content') / subject
            source, answers = source_bundle(subject, name)
            stage_dir = stage / relative
            stage_dir.mkdir(parents=True)
            (stage_dir / 'source.md').write_bytes(source)
            (stage_dir / 'answers.json').write_bytes(answers)
            env = dict(os.environ, GRASPMEMOEDU_PYTHON=sys.executable)
            process = subprocess.run([node, str(REPO / 'scripts/import_course.mjs'), str(stage_dir / 'source.md'),
                str(stage_dir / 'answers.json'), str(stage_dir / 'topic.json'), str(stage_dir / 'audit.json')],
                env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
            if process.returncode:
                raise ValueError('Content import failed: ' + process.stderr[-3000:])
            topic = json.loads((stage_dir / 'topic.json').read_bytes())
            Catalog._validate_topic(topic)
            if topic['id'] != topic_id or len(topic['modules']) != 2 or any(len(m['practices']) != 15 for m in topic['modules']):
                raise ValueError('Imported topic does not satisfy the declared fixture shape')
            # The generated course is untouched. Only the import receipt and
            # provenance file get their durable destination paths.
            for filename in ('topic.sources.json', 'audit.json'):
                metadata = json.loads((stage_dir / filename).read_bytes())
                for key in ('source_path', 'answers_path', 'course_path'):
                    if key in metadata:
                        metadata[key] = str(root / relative / Path(metadata[key]).name)
                (stage_dir / filename).write_bytes(encoded(metadata))
            for filename in ('source.md', 'answers.json', 'topic.json', 'topic.sources.json', 'audit.json'):
                files[root / relative / filename] = (stage_dir / filename).read_bytes()
            declaration = {'id': course_id, 'title': f'【测试】{name}流程体验',
                'description': '仅供测试现有 Lesson、补题与 FSRS 复习机制。',
                'topics': [{'id': topic_id, 'path': str(root / relative / 'topic.json'), 'prerequisites': []}]}
            existing = next((c for c in catalog['courses'] if c['id'] == course_id), None)
            if existing is not None and existing != declaration:
                raise ValueError('Existing test course was changed: ' + course_id)
            if existing is None:
                if topic_id in current.topics:
                    raise ValueError('Stable test Topic ID is already owned elsewhere: ' + topic_id)
                catalog['courses'].append(declaration)
            for item in subjects['subjects']:
                if course_id in item.get('course_ids', []) and item['id'] != subject:
                    raise ValueError('Test course assigned to a different subject: ' + course_id)
            item = next((s for s in subjects['subjects'] if s['id'] == subject), None)
            if item is None:
                item = {'id': subject}
                subjects['subjects'].append(item)
            if course_id not in item.setdefault('course_ids', []):
                item['course_ids'].append(course_id)
            if not item.get('guide_path'):
                item['guide_path'] = (relative / 'guide.md').as_posix()
            guide = Subjects.guide_location(root, item['guide_path'])
            if not guide.exists() or item['guide_path'] == (relative / 'guide.md').as_posix():
                files[guide] = f'# 【测试】{name}指南\n\n选择测试课程，完成 Example 与 Practice；到期后进入复习。\n\n题目仅用于机制测试。\n'.encode('utf-8')
        for filename, content in files.items():
            if not filename.resolve().is_relative_to(root):
                raise ValueError('Test content path leaves the data directory: ' + str(filename))
            if filename.exists() and filename.read_bytes() != content:
                raise ValueError('Refusing to overwrite changed test content: ' + str(filename))
        # Validate the full prospective catalog and subject ownership using
        # temporary references, without publishing partial configuration.
        staged_catalog = deepcopy(catalog)
        for c in staged_catalog['courses']:
            for t in c['topics']:
                target = Path(t['path'])
                if target in files:
                    t['path'] = str(stage / target.relative_to(root))
                elif not target.is_absolute():
                    t['path'] = str(catalog_path.parent / target)
        (stage / 'catalog.json').write_bytes(encoded(staged_catalog))
        validation = Settings(data_dir=root, catalog_path=stage / 'catalog.json', course_path=settings.course_path)
        validated = Catalog.load(validation)
        Subjects(settings, validated, raw=subjects)
        configs = {catalog_path: encoded(catalog), subjects_path: encoded(subjects)}
        changes = {p: b for p, b in {**files, **configs}.items() if not p.exists() or p.read_bytes() != b}
        if apply:
            # Refuse racing administrator edits discovered after import.
            for p, before in ((catalog_path, old_catalog), (subjects_path, old_subjects)):
                if (p.read_bytes() if p.exists() else None) != before:
                    raise ValueError('Configuration changed during initialization: ' + str(p))
            snapshots = {p: p.read_bytes() if p.exists() else None for p in changes}
            pending, installed = {}, []
            try:
                for p, content in changes.items():
                    p.parent.mkdir(parents=True, exist_ok=True)
                    temporary_path = p.with_name(p.name + '.initialize.tmp')
                    with temporary_path.open('xb') as stream:
                        stream.write(content)
                    pending[p] = temporary_path
                for p in changes:
                    os.replace(pending[p], p)
                    installed.append(p)
                final_catalog = Catalog.load(settings)
                Subjects(settings, final_catalog)
            except Exception:
                for p in reversed(installed):
                    if snapshots[p] is None:
                        p.unlink()
                    else:
                        rollback = p.with_name(p.name + '.rollback.tmp')
                        rollback.write_bytes(snapshots[p])
                        os.replace(rollback, p)
                raise
            finally:
                for temporary_path in pending.values():
                    temporary_path.unlink(missing_ok=True)
        return {'applied': apply, 'subject_ids': list(SUBJECTS), 'math_unchanged': True,
                'files': [{'path': str(p), 'sha256': hashlib.sha256(b).hexdigest(), 'changed': p in changes}
                          for p, b in {**files, **configs}.items()]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--node', default='node')
    args = parser.parse_args()
    print(json.dumps(initialize(args.data_dir, apply=args.apply, node=args.node), ensure_ascii=False, indent=2))
