"""Resumable local production over structured Topics, using the approved trial workflow."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import atomic_question_trial as trial
from graspmemoedu.author_import import CompatibleClient, _object


def source_tasks(document, subject, count):
    if document.get('schema') != 2 or subject not in trial.SUBJECTS or not 1 <= count <= 10:
        raise ValueError('Expected structured Topic schema 2, registered subject and count 1..10')
    tasks, identities = [], set()
    for topic in document['topics']:
        for section in topic['sections']:
            if not section.get('import_eligible') or section.get('status') != 'ai_passed':
                raise ValueError('Every section must be ready for import')
            chunks = {chunk['id']: chunk for chunk in section['memory_chunks']}
            for point in section['memory_points']:
                ident = point['id']
                if ident in identities or not trial.re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', ident):
                    raise ValueError('Duplicate or unsafe memory point')
                identities.add(ident)
                cards = [card for card in section['flashcards'] if card['point_ids'] == [ident]]
                seeds = [card for card in cards if 'learn' in card.get('learning_phases', ['learn', 'review'])]
                if not seeds:
                    raise ValueError('Every point needs an original single-point initial card')
                seed = seeds[0]
                answer = seed.get('reference_answer', seed.get('answer'))
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError('The original card needs an answer')
                source = {'subject_id': subject, 'variant_count': count,
                    'topic': {'id': str(topic['id']), 'version': trial.digest(topic)},
                    'memory_chunk': {'id': point['chunk_id'], 'content': chunks[point['chunk_id']]['content']},
                    'point': {'id': ident, 'target': '保持原始单点卡实际所问的完整目标与提取方向。知识正文：' + point['content']},
                    'seed_card': {'point_ids': [ident], 'question': seed['question'], 'reference_answer': answer,
                                  'interaction': seed.get('interaction', {'version': 1, 'type': 'text', 'grading': 'semantic'})},
                    'existing_variants': [{**card, 'reference_answer': card.get('reference_answer', card.get('answer'))}
                                          for card in cards if card['id'] != seed['id']]}
                tasks.append(source)
    if not tasks:
        raise ValueError('No memory points')
    return tasks


def refill_required(cards, seen, *, mastered):
    """A local demand simulation; no learner identities or website connection."""
    return mastered and not any(card['id'] not in seen for card in cards
                               if 'review' in card.get('learning_phases', ['learn', 'review']))


def run_point(source, root, config, generation, review, examples, attempts):
    ident = source['point']['id']
    folder = root / ident
    final = folder / 'result.json'
    if final.exists():
        result = trial.read(final)
        if result['source_sha256'] != trial.digest(source):
            raise ValueError('Saved point input changed')
        return result
    result = {'id': ident, 'source_sha256': trial.digest(source), 'status': 'failed'}
    feedback = None
    for ordinal in range(1, attempts + 1):
        work = folder / f'attempt-{ordinal:02d}'
        receipt = work / 'result.json'
        if receipt.exists():
            attempt = trial.read(receipt)
        else:
            attempt = {'attempt': ordinal, 'status': 'error'}
            request_source = deepcopy(source)
            if feedback:
                request_source['retry_feedback'] = feedback
            try:
                client = CompatibleClient(*config, timeout=300)
                print(f'{ident} attempt={ordinal} generating', flush=True)
                candidate = _object(trial.call(client, work, 'generation', generation,
                                   trial.generation_payload(request_source, examples)))
                attempt['candidate'] = candidate
                trial.validate_pair(candidate, source)
                if candidate['status'] == 'blocked':
                    attempt.update(status='blocked', error=candidate['reason'])
                else:
                    print(f'{ident} attempt={ordinal} reviewing', flush=True)
                    verdict = _object(trial.call(client, work, 'review', review,
                                      {'source': source, 'candidate': candidate}))
                    trial.validate_review(verdict, ident, source.get('variant_count', 2))
                    attempt.update(review=verdict, status='accepted' if verdict['approved'] else 'rejected')
            except Exception as error:
                attempt.update(error_type=type(error).__name__,
                               error=str(error) if isinstance(error, ValueError) else type(error).__name__)
                if any((work / f'{phase}.started.json').exists() and not (work / f'{phase}.response.json').exists()
                       for phase in ('generation', 'review')):
                    attempt['status'] = 'outcome_unknown'
                elif not isinstance(error, ValueError):
                    attempt['status'] = 'internal_error'
            trial.save(receipt, attempt)
        if attempt['status'] == 'accepted':
            result.update(status='accepted', variants=attempt['candidate']['variants'], attempt=ordinal)
            break
        feedback = attempt.get('review', {}).get('checks') or attempt.get('error', 'Candidate rejected')
        result.update(status=attempt['status'], attempt=ordinal, error=feedback)
        if attempt['status'] in ('outcome_unknown', 'internal_error'):
            break
    trial.save(final, result)
    print(f'{ident} {result["status"]}', flush=True)
    return result


def assemble(document, tasks, results):
    accepted = {result['id']: result for result in results if result['status'] == 'accepted'}
    if set(accepted) != {task['point']['id'] for task in tasks}:
        raise ValueError('Incomplete production; do not export or import a partial batch')
    value = deepcopy(document)
    added = 0
    for topic in value['topics']:
        for section in topic['sections']:
            for point in section['memory_points']:
                for variant in accepted[point['id']]['variants']:
                    ident = point['id'] + '-v-' + trial.digest(variant)[:16]
                    if any(card['id'] == ident for card in section['flashcards']):
                        raise ValueError('Duplicate generated identity')
                    section['flashcards'].append({'id': ident, **variant, 'learning_phases': ['review']})
                    added += 1
    value['summary'] = {**value.get('summary', {}), 'flashcards': sum(len(s['flashcards']) for t in value['topics'] for s in t['sections']),
                        'added_review_flashcards': added,
                        'new_card_interactions': trial.interaction_summary(
                            card for result in accepted.values() for card in result['variants']),
                        'source_unchanged': True, 'imported': False}
    return value


def run(args):
    document = trial.read(args.input)
    tasks = source_tasks(document, args.subject, args.count)
    examples = trial.load_few_shots(args.few_shots)
    for task in tasks:
        trial.generation_payload(task, examples)
    generation, review = trial.GEN.read_text(encoding='utf-8'), trial.REVIEW.read_text(encoding='utf-8')
    metadata = {'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(), 'subject': args.subject,
                'count': args.count, 'generation_sha256': trial.digest(generation), 'review_sha256': trial.digest(review),
                'few_shots_sha256': hashlib.sha256(args.few_shots.read_bytes()).hexdigest(), 'points': len(tasks)}
    manifest = args.run_root / 'run.json'
    if manifest.exists():
        if trial.read(manifest) != metadata:
            raise ValueError('Immutable run inputs changed; use a fresh run root')
    else:
        trial.save(manifest, metadata)
        trial.save(args.run_root / 'source-snapshot.json', document)
        trial.save(args.run_root / 'tasks.json', tasks)
        trial.save(args.run_root / 'few-shots-snapshot.json', {'version': 1, 'subjects': examples})
        (args.run_root / 'generation-prompt.md').write_text(generation, encoding='utf-8')
        (args.run_root / 'review-prompt.md').write_text(review, encoding='utf-8')
    selected = set(args.only or [task['point']['id'] for task in tasks])
    if selected - {task['point']['id'] for task in tasks}:
        raise ValueError('Unknown selected point')
    config = trial.configuration()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_point, task, args.run_root, config, generation, review, examples, args.attempts)
                   for task in tasks if task['point']['id'] in selected]
        for future in as_completed(futures):
            future.result()
    results = [trial.read(path) for task in tasks
               if (path := args.run_root / task['point']['id'] / 'result.json').exists()]
    status = {'points': len(tasks), 'counts': dict(Counter(result['status'] for result in results)),
              'complete': len(results) == len(tasks) and all(result['status'] == 'accepted' for result in results)}
    print(json.dumps(status), flush=True)
    if status['complete']:
        exported = assemble(document, tasks, results)
        output = args.run_root / 'structured-topics.enriched.json'
        if output.exists():
            if trial.read(output) != exported:
                raise ValueError('Existing export differs')
        else:
            trial.save(output, exported)
        print(str(output), flush=True)
    return 0 if status['complete'] or args.only else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--subject', required=True)
    parser.add_argument('--few-shots', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--count', type=int, default=2)
    parser.add_argument('--workers', type=int, choices=range(1, 33), default=4)
    parser.add_argument('--attempts', type=int, choices=range(1, 7), default=3)
    parser.add_argument('--only', nargs='+')
    return run(parser.parse_args())


if __name__ == '__main__':
    sys.exit(main())
