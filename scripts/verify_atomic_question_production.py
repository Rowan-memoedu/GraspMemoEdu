"""Read back local question production without importing or invoking a model."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import atomic_question_trial as trial
from scripts.produce_atomic_questions import assemble, source_tasks
from graspmemoedu.author_import import _object


def response(path, model):
    raw = trial.read(path)
    if (raw.get('provider') != 'codex_exec' or raw.get('model') != model
            or not raw.get('transport', {}).get('completed_event')):
        raise ValueError('Missing completed official Codex response: ' + str(path))
    return _object(raw)


def verify(root):
    root = Path(root).resolve()
    manifest = trial.read(root/'run.json')
    original = trial.read(root/'source-snapshot.json')
    tasks = source_tasks(original, manifest['subject'], manifest['count'])
    if tasks != trial.read(root/'tasks.json'):
        raise ValueError('Frozen tasks differ from frozen upstream')
    for filename, key in [('generation-prompt.md','generation_sha256'), ('review-prompt.md','review_sha256')]:
        if trial.digest((root/filename).read_text(encoding='utf-8')) != manifest[key]:
            raise ValueError('Frozen prompt changed')
    results = []
    for task in tasks:
        folder = root/task['point']['id']
        result = trial.read(folder/'result.json')
        if result['status'] != 'accepted' or result['source_sha256'] != trial.digest(task):
            raise ValueError('Unaccepted or changed point: ' + task['point']['id'])
        work = folder/f'attempt-{result["attempt"]:02d}'
        receipt = work/('recovered-result.json' if (work/'recovered-result.json').exists() else 'result.json')
        attempt = trial.read(receipt)
        origin = work/'generation.response.json'
        if (work/'generation-reused.json').exists():
            reuse = trial.read(work/'generation-reused.json')
            origin = Path(reuse['path']).resolve()
            if not origin.is_relative_to(root) or hashlib.sha256(origin.read_bytes()).hexdigest() != reuse['sha256']:
                raise ValueError('Reused generation evidence changed')
        candidate = response(origin, manifest['model'])
        trial.validate_pair(candidate, task)
        if candidate['variants'] != result['variants'] or candidate != attempt['candidate']:
            raise ValueError('Candidate differs from exact producer response')
        request = trial.read(origin.with_name('generation.request.json'))
        payload = trial.json.loads(request['messages'][-1]['content'])
        submitted = dict(payload['task']); submitted.pop('retry_feedback', None)
        if submitted != task:
            raise ValueError('Producer input differs from frozen task')
        phase = attempt.get('review_phase', 'review')
        verdict = response(work/(phase+'.response.json'), manifest['model'])
        trial.validate_review(verdict, task['point']['id'], manifest['count'])
        if not verdict['approved'] or verdict != attempt['review']:
            raise ValueError('Missing exact independent approval')
        review_request = trial.read(work/(phase+'.request.json'))
        review_payload = trial.json.loads(review_request['messages'][-1]['content'])
        if review_payload['source'] != task or review_payload['candidate'] != candidate:
            raise ValueError('Reviewer did not see exact source and candidate')
        results.append(result)
    expected = assemble(original, tasks, results,
                        replace_representatives=manifest.get('replace_representatives',False))
    exported = trial.read(root/'structured-topics.enriched.json')
    if exported != expected:
        raise ValueError('Export differs from exact accepted points and untouched upstream')
    cards = [c for t in exported['topics'] for s in t['sections'] for c in s['flashcards']]
    if any('{{asset:' in trial.json.dumps(c, ensure_ascii=False) for c in cards):
        raise ValueError('Unresolved asset reference in Flashcard')
    for before, after in zip(original['topics'], exported['topics']):
        for old, new in zip(before['sections'], after['sections']):
            if {k:v for k,v in old.items() if k!='flashcards'} != {k:v for k,v in new.items() if k!='flashcards'}:
                raise ValueError('An upstream field changed')
            for point in new['memory_points']:
                linked = [c for c in new['flashcards'] if c['point_ids']==[point['id']]]
                if manifest.get('replace_representatives') and (
                        len(linked)!=manifest['count'] or
                        sum('learn' in c['learning_phases'] for c in linked)!=1):
                    raise ValueError('Missing or duplicate initial representative')
    return {'verified':True, 'topics':len(exported['topics']),
            'sections':sum(len(t['sections']) for t in exported['topics']),
            'memory_chunks':sum(len(s['memory_chunks']) for t in exported['topics'] for s in t['sections']),
            'memory_points':len(tasks), 'flashcards':len(cards), 'upstream_unchanged':True,
            'independent_approvals':len(results), 'interaction_summary':trial.interaction_summary(cards),
            'export_sha256':hashlib.sha256((root/'structured-topics.enriched.json').read_bytes()).hexdigest(),
            'imported':False}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    args=parser.parse_args()
    value=verify(args.run_root)
    output=args.run_root/'verification.json'
    if output.exists():
        if trial.read(output)!=value: raise ValueError('Existing verification differs')
    else:
        trial.save(output,value)
    print(trial.json.dumps(value,ensure_ascii=False))
