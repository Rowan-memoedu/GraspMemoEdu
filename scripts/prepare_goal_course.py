"""Mechanically package an approved goal course with its original bank exercise.

The original exercise/key are a separate frozen author input, not model output.
Its blind solving and source review use the same isolated Codex Exec transport.
No content or learner state is installed by this command.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
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
from produce_goal_course import (call, points, learner_view, validate_blind, verify_plan,
                                 export_sources, REVIEW, _object)


GOAL_MAPPING = """Build conservative scoring metadata for the unchanged original exercise.
Use the supplied original text/key, source material, approved memory points and
blind solver's answers. Return JSON {blanks:[{id,answers:[string],point_ids:[local
planned point ID],explanation:string}]}. Keep every original key among accepted
answers. Independently judge proposed alternatives; do not accept a blind
solver's suggestion merely because it suggested it. Explain meaningful variants.
The original exercise wording cannot change. Credit only points for which the
blank supplies implicit retrieval evidence. Do not require a verbal explanation
for a form choice, but do not credit incidental vocabulary, extra examples,
unobserved metalinguistic claims, or a specific derivation bypassed by another
accepted answer. Keep the original required knowledge set: it still governs
unlocking. A blank may have no credited points if none of the frozen points is
safe to infer; it still contributes to the exercise's whole-answer result.
Do not invent or rewrite any point. Each explanation is learner-facing Chinese.
"""


def project_goal(source, plan, original, scoring=None):
    """Replace only declared blank spans, retaining the supplied original text."""
    ids = [b['id'] for b in original['blanks']]
    if ids != source['blank_ids']:
        raise ValueError('Original goal blank identity/order changed')
    text = next(s['text'] for s in source['sources'] if s['id'] == original['source_id'])
    for blank in original['blanks']:
        if text.count(blank['source_marker']) != 1 or not blank['answers']:
            raise ValueError('Goal source marker/key is missing or ambiguous')
        text = text.replace(blank['source_marker'], '{{blank:' + blank['id'] + '}}')
    prefix = source['course_id'] + '-'
    linked = [(prefix + t['id'] + '/' + p['id'], p) for t in plan['topics'] for p in points(t)]
    mapping = {ident: [ref for ref, p in linked if ident in p['assessed_by']] for ident in ids}
    blanks = deepcopy(original['blanks'])
    if scoring is not None:
        rows = scoring['blanks']
        if [b['id'] for b in rows] != ids:
            raise ValueError('Scoring changed blank identity/order')
        by_point = {p['id']: (ref, p) for ref, p in linked}
        for given, row in zip(blanks, rows):
            if (not isinstance(row['answers'], list) or not row['answers']
                    or any(not isinstance(a, str) or not a.strip() for a in row['answers'])
                    or not set(given['answers']) <= set(row['answers'])
                    or not row['explanation'].strip() or not isinstance(row['point_ids'], list)
                    or len(row['point_ids']) != len(set(row['point_ids']))
                    or any(p not in by_point or not by_point[p][1]['target_required'] for p in row['point_ids'])):
                raise ValueError('Scoring discarded an original answer or used an unknown prerequisite')
            given['answers'] = row['answers']
            mapping[given['id']] = [by_point[p][0] for p in row['point_ids']]
    elif any(not refs for refs in mapping.values()):
        raise ValueError('Unmapped goal blank')
    mapping = {key: values for key, values in mapping.items() if values}
    assessed = {ref for values in mapping.values() for ref in values}
    if not assessed:
        raise ValueError('No defensible implicit point evidence in the goal')
    explanation = []
    for blank in blanks:
        explanation.append(blank['label'] + '：' + ' / '.join(blank['answers']))
        if scoring:
            explanation.append(next(b['explanation'] for b in scoring['blanks'] if b['id'] == blank['id']))
        else:
            explanation.extend(p['content'] for _, p in linked if blank['id'] in p['assessed_by'])
    q = {'id': original['question_id'], 'title': source['goal_title'],
         'question': original['instruction'] + '\n\n' + text,
         'reference_answer': '\n'.join(b['label'] + ': ' + ' / '.join(b['answers']) for b in blanks),
         'explanation': '\n\n'.join(explanation),
         'point_ids': [ref for ref, _ in linked if ref in assessed],
         'interaction': {'version': 1, 'type': 'fill_blank', 'grading': 'exact',
                         'blanks': [{k: b[k] for k in ('id', 'label', 'answers')} for b in blanks]},
         'assessment': {'version': 1, 'item_points': mapping}}
    prerequisites = [ref for ref, p in linked if p['target_required']]
    return {'schema': 1, 'id': original['bank_id'], 'title': original['bank_title'],
            'description': original.get('description', ''), 'subject_id': source['subject_id'],
            'groups': [{'id': original['group_id'], 'title': source['goal_title'],
                        'unlock_policy': {'point_ids': prerequisites}, 'questions': [q]}]}


def goal_audit(args):
    source, plan = cp.read(args.run / 'source.json'), cp.read(args.run / 'plan.json')
    verify_plan(args.run, plan)
    original = cp.read(args.goal)
    folder = args.run / 'goal'
    folder.mkdir(exist_ok=True)
    cp.save(folder / 'original.json', original)
    candidate = project_goal(source, plan, original)
    q = candidate['groups'][0]['questions'][0]
    view = learner_view({'sections': [{'cards': [q]}]}, 'atomic')
    blind = call(folder, 'blind', cp.LEARNER_REVIEW, view, args.timeout)
    validate_blind(blind, view)
    scoring = call(folder, 'mapping', GOAL_MAPPING,
                   {'source': source, 'plan': plan, 'original': original, 'blind': blind}, args.timeout)
    candidate = project_goal(source, plan, original, scoring)
    if learner_view({'sections': [{'cards': candidate['groups'][0]['questions']}]}, 'atomic') != view:
        raise ValueError('Scoring metadata changed the blind learner view')
    cp.save(folder / 'candidate.json', candidate)
    review = call(folder, 'review', REVIEW, {'source': source, 'plan': plan,
        'candidate': candidate, 'blind': blind, 'original': original, 'scoring': scoring,
        'task': 'Audit the original goal exercise and its exact blank-to-point mapping. '
                'The wording is unchanged; scoring and explanation use separately proposed metadata. '
                'Check answer alternatives, source fidelity and that each mapped point '
                'is actually discriminated by that blank; unrelated prerequisites are not scored.'}, args.timeout)
    if not blind['approved'] or not review['approved'] or review['issues']:
        raise ValueError('Goal exercise did not pass: ' + json.dumps({'blind': blind, 'review': review}, ensure_ascii=False))
    cp.save(folder / 'accepted.json', {'candidate': candidate, 'blind': blind, 'review': review, 'scoring': scoring})
    print('Accepted original goal exercise', flush=True)


def approved_goal(args, source, plan):
    root = args.run / 'goal'
    record = cp.read(root / 'accepted.json')
    original = cp.read(root / 'original.json')
    if project_goal(source, plan, original, record.get('scoring')) != record['candidate']:
        raise ValueError('Goal differs from the original source projection')
    if record.get('scoring'):
        value = _object(cp.read(root / 'mapping.response.json'))
        messages = cp.validate_exec_proof(cp.read(root / 'mapping.exec-proof.json'), value)
        if value != record['scoring'] or json.loads(messages[-1]['content']) != {
                'source': source, 'plan': plan, 'original': original, 'blind': record['blind']}:
            raise ValueError('Goal scoring was not generated from the frozen original and points')
    for phase in ('blind', 'review'):
        value = _object(cp.read(root / (phase + '.response.json')))
        messages = cp.validate_exec_proof(cp.read(root / (phase + '.exec-proof.json')), value)
        request = json.loads(messages[-1]['content'])
        if phase == 'blind':
            expected = learner_view({'sections': [{'cards': record['candidate']['groups'][0]['questions']}]}, 'atomic')
            if request != expected:
                raise ValueError('Goal blind review saw different questions')
        elif any(request.get(k) != v for k, v in {'source': source, 'plan': plan,
                'candidate': record['candidate'], 'blind': record['blind']}.items()):
            raise ValueError('Goal source review used a different candidate or source')
        if value != record[phase] or not value['approved'] or value.get('issues'):
            raise ValueError('Goal lacks independent approval')
    return record['candidate']


def package(args):
    source, plan = cp.read(args.run / 'source.json'), cp.read(args.run / 'plan.json')
    verify_plan(args.run, plan)
    bank = approved_goal(args, source, plan)
    export_sources(SimpleNamespace(output=args.run, source=args.run / 'source.json'))
    export = args.run / 'export'
    args.output.mkdir(parents=True, exist_ok=False)
    atomic = args.output / 'atomic-package'
    course = cp.read(export / 'course.json')
    prepare_atomic(SimpleNamespace(input=export / 'atomic.json', output=atomic,
        subject=source['subject_id'], course_id=course['id'], course_title=course['title'],
        unit_id='foundations', unit_title='语言基础与篇章理解', unit_parent=None,
        topic_prefix='', title_suffix='', node='node', test_material=True))
    prepared = args.output / 'course-package'
    shutil.copytree(atomic, prepared)
    declarations = cp.read(prepared / 'catalog.json')['courses'][0]
    declarations['description'] = plan['description']
    for row in course['paradigms']:
        run = args.output / 'author-packages' / row['id']
        result = prepare_topic(row['source'], row['id'], run)
        if result['status'] not in ('approved', 'mechanically_validated'):
            raise ValueError('Paradigm compilation failed: ' + json.dumps(result))
        manifest, values, topic = read_package(run)
        relative = Path('author-packages') / row['id']
        destination = prepared / relative
        shutil.copytree(run, destination)
        # Portable sidecar paths are the only rewritten package metadata.
        index = cp.read(destination / 'topic.sources.json')
        for key, artifact in [('source_path', 'source'), ('answers_path', 'answers'), ('review_path', 'review')]:
            index[key] = manifest['artifacts'][artifact]
        (destination / 'topic.sources.json').write_bytes(encoded(index))
        portable = cp.read(destination / 'manifest.json')
        portable['files']['topic.sources.json'] = hashlib.sha256((destination / 'topic.sources.json').read_bytes()).hexdigest()
        (destination / 'manifest.json').write_bytes(encoded(portable))
        declarations['topics'].append({'id': topic['id'], 'path': (relative / 'topic.json').as_posix(), 'prerequisites': []})
    by_id = {t['id']: t for t in declarations['topics']}
    declarations['topics'] = [by_id[t['id']] for t in course['topics']]
    for row in course['topics']:
        by_id[row['id']]['prerequisites'] = row['prerequisites']
    declarations['units'].append({'id': 'transfer', 'title': '范式与综合迁移',
                                  'topic_ids': [t['id'] for t in course['topics'] if t['kind'] == 'paradigm']})
    (prepared / 'catalog.json').write_bytes(encoded({'courses': [declarations]}))
    manifest = cp.read(prepared / 'manifest.json')
    manifest['test_materials']['banks'] = [bank['id']]
    manifest['files'] = {p.relative_to(prepared).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(prepared.rglob('*')) if p.is_file() and p != prepared / 'manifest.json'}
    (prepared / 'manifest.json').write_bytes(encoded(manifest))
    bank_source = args.output / 'goal-bank-source.json'
    bank_source.write_bytes(encoded(bank))
    compiled = args.output / 'goal-bank.json'
    subprocess.run(['node', str(ROOT / 'scripts/import_training_bank.mjs'), str(bank_source), str(compiled)],
                   cwd=ROOT, check=True, env={**os.environ, 'GRASPMEMOEDU_PYTHON': sys.executable})
    validate_bank(cp.read(compiled))
    graph = {'schema_version': 3, 'subject_id': source['subject_id'], 'course_id': course['id'], 'title': course['title'],
             'nodes': [{'topic_id': row['id'], 'title': next(t['title'] for t in plan['topics'] if source['course_id'] + '-' + t['id'] == row['id']),
                        'member': True} for row in course['topics']],
             'edges': [{'from': parent, 'to': row['id'], 'type': 'prerequisite'}
                       for row in course['topics'] for parent in row['prerequisites']]}
    graph['nodes'].extend({'bank_id': bank['id'], 'group_id': g['id'], 'title': g['title'], 'member': True} for g in bank['groups'])
    (args.output / 'graph.json').write_bytes(encoded(graph))
    print(json.dumps({'course_package': str(prepared), 'bank': str(compiled), 'topics': len(course['topics'])}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--goal', type=Path)
    parser.add_argument('--audit-goal', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    goal_audit(args) if args.audit_goal else package(args)
