"""Mechanically compile accepted native course sources, optionally with a bank.

No semantic generation/review or production writes occur here. Goal extraction,
answer adjudication and post-course mapping are independent upstream workflows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu import context_production as cp
from graspmemoedu.author_import import prepare_topic
from graspmemoedu.topic_install import encoded, read_package
from graspmemoedu.training_content import validate as validate_bank
from prepare_structured_atomic import prepare as prepare_atomic
from produce_goal_course import export_sources
from course_authoring_contract import require


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_bank_references(bank, catalog):
    """Mechanical referential integrity only; no judgment of scoring meaning."""
    known = {row['point_ref'] for row in catalog['points']}
    for group in bank['groups']:
        required = group.get('unlock_policy', {}).get('point_ids', [])
        require(set(required) <= known, 'Goal unlock references unknown course points')
        for question in group['questions']:
            refs = question.get('point_ids', [])
            require(set(refs) <= known, 'Goal scoring references unknown course points')
            for items in question.get('assessment', {}).get('item_points', {}).values():
                require(set(items) <= set(refs), 'Goal item mapping is outside its question point set')


def final_point_map(catalog, bank):
    """Retain every course point, including all points irrelevant to a target."""
    validate_bank_references(bank, catalog)
    rows = []
    for point in catalog['points']:
        ref = point['point_ref']; unlock, assessments = [], []
        for group in bank['groups']:
            if ref in group.get('unlock_policy', {}).get('point_ids', []):
                unlock.append({'bank_id': bank['id'], 'group_id': group['id']})
            for question in group['questions']:
                if ref in question.get('point_ids', []):
                    assessments.append({'bank_id': bank['id'], 'group_id': group['id'], 'question_id': question['id'],
                        'item_ids': [key for key, refs in question.get('assessment', {}).get('item_points', {}).items() if ref in refs]})
        rows.append({**point, 'unlock_targets': unlock, 'assessments': assessments})
    return {'schema_version': 1, 'mapping_status': 'mechanically_validated', 'points': rows}


def validate_compiled_points(prepared, declarations, catalog):
    """Bind the mapping catalog to actual installed-format IDs, not just source IDs."""
    actual = set()
    for row in declarations['topics']:
        path = (prepared / row['path']).resolve()
        require(path.is_relative_to(prepared.resolve()), 'Compiled Topic path escapes its package')
        topic = cp.read(path)
        require(topic['id'] == row['id'], 'Compiled Topic identity changed')
        for chunk in topic.get('memory_chunks', []):
            for point in chunk['points']:
                ref = topic['id'] + '/' + point['id']
                require(ref not in actual, 'Compiled memory-point identity is duplicated')
                actual.add(ref)
    expected = [point['point_ref'] for point in catalog['points']]
    require(len(expected) == len(set(expected)) and actual == set(expected),
            'Point catalog differs from actual compiled memory-point IDs')
    return len(actual)


def compile_sources(export, output, *, goal_bank_source=None, node='node'):
    """Use only existing native compilers; exported reading Markdown is not input."""
    course = cp.read(export / 'course.json')
    point_catalog = cp.read(export / 'point-catalog.json')
    topic_ids = {row['id'] for row in course['topics']}
    require(len(topic_ids) == len(course['topics']) and all(set(row['prerequisites']) <= topic_ids
            for row in course['topics']), 'Export must include the actual prerequisite closure without duplicate Topic IDs')
    output.mkdir(parents=True, exist_ok=False)
    prepared = output / 'course-package'
    atomic = cp.read(export / 'atomic.json')
    if atomic['topics']:
        prepare_atomic(SimpleNamespace(input=export / 'atomic.json', output=prepared,
            subject=course['subject_id'], course_id=course['id'], course_title=course['title'],
            unit_id='source-units', unit_title=course['title'], unit_parent=None,
            topic_prefix='', title_suffix='', node=node, test_material=True))
        declarations = cp.read(prepared / 'catalog.json')['courses'][0]
    else:
        prepared.mkdir()
        declarations = {'id': course['id'], 'title': course['title'], 'description': '', 'topics': [], 'units': []}
    declarations['description'] = course.get('description', '')
    for row in course['topics']:
        if row['kind'] != 'paradigm':
            continue
        run = output / 'author-packages' / row['id']
        result = prepare_topic(row['source'], row['id'], run)
        require(result['status'] in ('approved', 'mechanically_validated')
                and result.get('semantic_review_performed') is False
                and result.get('method') == 'mechanical-import',
                'Native v2 mechanical compilation did not pass: ' + json.dumps(result))
        manifest, _, topic = read_package(run)
        relative = Path('author-packages') / row['id']; destination = prepared / relative
        shutil.copytree(run, destination)
        # Relocate only package path metadata, then rebind its hash inventory.
        index = cp.read(destination / 'topic.sources.json')
        for key, artifact in [('source_path', 'source'), ('answers_path', 'answers'), ('review_path', 'review')]:
            index[key] = manifest['artifacts'][artifact]
        (destination / 'topic.sources.json').write_bytes(encoded(index))
        portable = cp.read(destination / 'manifest.json')
        portable['files']['topic.sources.json'] = file_hash(destination / 'topic.sources.json')
        (destination / 'manifest.json').write_bytes(encoded(portable))
        declarations['topics'].append({'id': topic['id'], 'path': (relative / 'topic.json').as_posix(), 'prerequisites': []})
    by_id = {t['id']: t for t in declarations['topics']}
    require(set(by_id) == {t['id'] for t in course['topics']}, 'Native compilation lost or duplicated a Topic')
    declarations['topics'] = [by_id[t['id']] for t in course['topics']]
    declarations['units'] = course['units']
    for row in course['topics']:
        by_id[row['id']]['prerequisites'] = row['prerequisites']
    validate_compiled_points(prepared, declarations, point_catalog)
    (prepared / 'catalog.json').write_bytes(encoded({'courses': [declarations]}))
    manifest = {'schema': 1, 'adapter': 'structured-atomic', 'subject_id': course['subject_id'],
                'reset_reading_progress': False, 'replace_atomic_and_reset_progress': False,
                'test_materials': {'courses': [course['id']]}}
    graph = {'schema_version': 3, 'subject_id': course['subject_id'], 'course_id': course['id'], 'title': course['title'],
        'nodes': [{'topic_id': row['id'], 'title': row['title'], 'member': True} for row in course['topics']],
        'edges': [{'from': parent, 'to': row['id'], 'type': 'prerequisite'}
                  for row in course['topics'] for parent in row['prerequisites']]}
    if goal_bank_source:
        # This optional boundary is invoked only after independent private-goal
        # mapping. Teaching export and course compilation do not wait for it.
        bank = cp.read(goal_bank_source)
        require(bank['subject_id'] == course['subject_id'], 'Bank belongs to another subject')
        validate_bank_references(bank, point_catalog)
        bank_source = output / 'goal-bank-source.json'; bank_source.write_bytes(encoded(bank))
        compiled = output / 'goal-bank.json'
        subprocess.run([node, str(ROOT / 'scripts/import_training_bank.mjs'), str(bank_source), str(compiled)],
                       cwd=ROOT, check=True, env={**os.environ, 'GRASPMEMOEDU_PYTHON': sys.executable})
        validate_bank(cp.read(compiled)); validate_bank_references(cp.read(compiled), point_catalog)
        cp.save(output / 'point-map.json', final_point_map(point_catalog, bank))
        manifest['test_materials']['banks'] = [bank['id']]
        graph['nodes'].extend({'bank_id': bank['id'], 'group_id': g['id'], 'title': g['title'], 'member': True} for g in bank['groups'])
    cp.save(output / 'point-catalog.json', point_catalog)
    manifest['files'] = {p.relative_to(prepared).as_posix(): file_hash(p)
                         for p in sorted(prepared.rglob('*')) if p.is_file() and p != prepared / 'manifest.json'}
    (prepared / 'manifest.json').write_bytes(encoded(manifest))
    (output / 'graph.json').write_bytes(encoded(graph))
    report = {'status': 'mechanically_validated', 'semantic_review_performed': False,
        'course_package': str(prepared), 'topics': len(course['topics']), 'points': len(point_catalog['points']),
        'goal_bank_included': bool(goal_bank_source), 'installed': False,
        'compiled_point_references_verified': True,
        'native_sources': [{'topic_id': t['id'], 'path': t['source'], 'sha256': file_hash(Path(t['source']))} for t in course['topics']]}
    cp.save(output / 'compilation-report.json', report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def package(args):
    export = export_sources(SimpleNamespace(output=args.run, unit=getattr(args, 'unit', None),
        topic=getattr(args, 'topic', None), export_dir=getattr(args, 'export_dir', None)))
    return compile_sources(export, args.output, goal_bank_source=getattr(args, 'goal_bank_source', None),
                           node=getattr(args, 'node', 'node'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--unit', action='append')
    parser.add_argument('--topic', action='append', help='Compile selected accepted Topics for an isolated partial preview')
    parser.add_argument('--export-dir', type=Path, help='Separate native export directory for a partial preview')
    parser.add_argument('--goal-bank-source', type=Path, help='Optional independently adjudicated native bank source')
    parser.add_argument('--node', default='node')
    package(parser.parse_args())
