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
from graspmemoedu.author_import import _object
from scripts.atomic_question_trial import CompatibleClient
from scripts.atomic_question_recovery import checkpoint, dispatch, recover_response, run_lock


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
                    'point': {'id': ident, 'target': '保持原始单点卡实际所问的完整目标与提取方向。'
                              + ('明确提取目标：' + point['retrieval_target'] + '。' if point.get('retrieval_target') else '')
                              + '知识正文：' + point['content']},
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


def review_candidate(client, work, prompt, source, candidate):
    """Repair an inconsistent review protocol without rewriting the candidate."""
    payload = {'source': source, 'candidate': candidate}
    for ordinal in range(1, 4):
        phase = 'review' if ordinal == 1 else f'review-{ordinal:02d}'
        verdict = _object(trial.call(client, work, phase, prompt, payload))
        try:
            trial.validate_review(verdict, source['point']['id'], source.get('variant_count', 2))
            return verdict, phase
        except ValueError as error:
            if ordinal == 3:
                raise
            payload = {'source': source, 'candidate': candidate,
                       'protocol_feedback': {'error': str(error), 'previous_verdict': verdict,
                         'instruction': '固定候选，只纠正审核格式与结论不一致。逐项 approved 与 issues、总 approved 必须一致；不能为了通过协议检查而改变标准。'}}


def run_point(source, root, config, generation, review, examples, attempts, retry_incomplete=False):
    ident = source['point']['id']
    folder = root / ident
    final = folder / 'result.json'
    if final.exists():
        result = trial.read(final)
        if result['source_sha256'] != trial.digest(source):
            raise ValueError('Saved point input changed')
        if result['status'] == 'accepted':
            return result
    result = {'id': ident, 'source_sha256': trial.digest(source), 'status': 'failed'}
    feedback = None
    reusable = None
    for ordinal in range(1, attempts + 1):
        work = folder / f'attempt-{ordinal:02d}'
        receipt = work / 'result.json'
        if receipt.exists():
            attempt = trial.read(receipt)
            if attempt['status'] == 'outcome_unknown' and retry_incomplete:
                client = CompatibleClient(*config, timeout=300)
                for phase in ('generation', 'review'):
                    recover_response(work, phase, client)
                saved_generation = work / 'generation.response.json'
                if saved_generation.exists():
                    try:
                        candidate = _object(trial.read(saved_generation))
                        trial.validate_pair(candidate, source)
                        if candidate['status'] == 'ok':
                            saved_review = work / 'review.response.json'
                            if saved_review.exists():
                                verdict = _object(trial.read(saved_review))
                                trial.validate_review(verdict, ident, source.get('variant_count', 2))
                                attempt = {**attempt, 'candidate': candidate, 'review': verdict,
                                           'status': 'accepted' if verdict['approved'] else 'rejected'}
                                recovered = work / 'recovered-result.json'
                                if not recovered.exists(): trial.save(recovered, attempt)
                            else:
                                reusable = (candidate, saved_generation)
                    except ValueError as error:
                        attempt = {**attempt, 'status': 'error', 'error': str(error)}
        else:
            attempt = {'attempt': ordinal, 'status': 'error'}
            request_source = deepcopy(source)
            if feedback:
                request_source['retry_feedback'] = feedback
            try:
                client = CompatibleClient(*config, timeout=300)
                if reusable:
                    candidate, origin = reusable
                    trial.save(work / 'generation-reused.json', {'path': str(origin),
                               'sha256': hashlib.sha256(origin.read_bytes()).hexdigest()})
                    reusable = None
                else:
                    print(f'{ident} attempt={ordinal} generating', flush=True)
                    candidate = _object(trial.call(client, work, 'generation', generation,
                                       trial.generation_payload(request_source, examples)))
                attempt['candidate'] = candidate
                trial.validate_pair(candidate, source)
                if candidate['status'] == 'blocked':
                    attempt.update(status='blocked', error=candidate['reason'])
                else:
                    print(f'{ident} attempt={ordinal} reviewing', flush=True)
                    verdict, phase = review_candidate(client, work, review, source, candidate)
                    attempt.update(review=verdict, review_phase=phase,
                                   status='accepted' if verdict['approved'] else 'rejected')
            except Exception as error:
                attempt.update(error_type=type(error).__name__,
                               error=str(error) if isinstance(error, ValueError) else type(error).__name__)
                if any(not path.with_name(path.name.replace('.started.json', '.response.json')).exists()
                       for path in work.glob('*.started.json')):
                    attempt['status'] = 'outcome_unknown'
                elif not isinstance(error, ValueError):
                    attempt['status'] = 'internal_error'
            trial.save(receipt, attempt)
        if attempt['status'] == 'accepted':
            result.update(status='accepted', variants=attempt['candidate']['variants'], attempt=ordinal)
            break
        feedback = {'issues': attempt.get('review', {}).get('checks') or attempt.get('error', 'Candidate rejected')}
        if attempt.get('candidate'):
            feedback['previous_candidate'] = attempt['candidate']
        result.update(status=attempt['status'], attempt=ordinal, error=feedback)
        retryable = retry_incomplete and (attempt['status'] == 'outcome_unknown' or
                    attempt['status'] == 'internal_error' and attempt.get('error_type') in ('OSError', 'RuntimeError'))
        if retryable:
            replay = work / 'retry-decision.json'
            if not replay.exists():
                trial.save(replay, {'next_attempt': ordinal + 1, 'previous_status': attempt['status'],
                    'policy': 'bounded_retry_of_pure_text_after_prior_run_or_call_ended',
                    'generation_reused': bool(reusable), 'remote_mutations': False})
        if attempt['status'] in ('outcome_unknown', 'internal_error') and not retryable:
            break
    checkpoint(final, result)
    print(f'{ident} {result["status"]}', flush=True)
    return result


def assemble(document, tasks, results, *, replace_representatives=False):
    accepted = {result['id']: result for result in results if result['status'] == 'accepted'}
    if set(accepted) != {task['point']['id'] for task in tasks}:
        raise ValueError('Incomplete production; do not export or import a partial batch')
    value = deepcopy(document)
    added = 0
    for topic in value['topics']:
        for section in topic['sections']:
            for point in section['memory_points']:
                if replace_representatives:
                    section['flashcards'] = [c for c in section['flashcards']
                        if not (c['point_ids'] == [point['id']] and 'learn' in c.get('learning_phases', ['learn','review']))]
                variants = accepted[point['id']]['variants']
                for index,variant in enumerate(variants):
                    ident = point['id'] + '-v-' + trial.digest(variant)[:16]
                    if any(card['id'] == ident for card in section['flashcards']):
                        raise ValueError('Duplicate generated identity')
                    phases = ['learn','review'] if replace_representatives and index == 0 else ['review']
                    section['flashcards'].append({'id': ident, **variant, 'learning_phases': phases})
                    added += 1
    value['summary'] = {**value.get('summary', {}), 'flashcards': sum(len(s['flashcards']) for t in value['topics'] for s in t['sections']),
                        'added_review_flashcards': added,
                        'new_card_interactions': trial.interaction_summary(
                            card for result in accepted.values() for card in result['variants']),
                        'source_unchanged': True, 'imported': False,
                        **({'representatives_regenerated':len(tasks), 'upstream_unchanged':True,
                            'added_review_flashcards':added-len(tasks)} if replace_representatives else {})}
    return value


def render_reading(document, root):
    """Render the derived cards, with their actual controls, for local review."""
    folder=root/'topics'
    folder.mkdir(parents=True,exist_ok=True)
    index=['# 原子学习材料','']
    for topic in document['topics']:
        lines=['# '+topic['title'],'']
        for section in topic['sections']:
            lines += ['## '+section.get('section_title',section.get('id','')),'',
                      section.get('introduction',{}).get('markdown',''),'']
            if section['memory_chunks']:
                lines += ['### 记忆组块','']
            assets={a['id']:a for a in section.get('source_assets',[])}
            for chunk in section['memory_chunks']:
                lines += ['#### '+chunk.get('local_id',chunk['id']),'',chunk['content'],'']
                mnemonic=chunk.get('mnemonic',{})
                aids=mnemonic.get('source_quotes',[])+[assets[i]['markdown'] for i in mnemonic.get('asset_ids',[])]
                if aids: lines += ['助记：','',*aids,'']
            if section['memory_points']:
                lines += ['### 原子记忆点与设问','']
            for point in section['memory_points']:
                lines += ['#### '+point.get('local_id',point['id']),'',point['content'],'']
                if point.get('retrieval_target'): lines += ['提取目标：'+point['retrieval_target'],'']
                for card in section['flashcards']:
                    if point['id'] not in card['point_ids']: continue
                    title='初始设问' if 'learn' in card.get('learning_phases',['learn','review']) else '复习设问'
                    lines += ['##### '+title,'']+trial.question_lines({**card,
                        'reference_answer':card.get('reference_answer',card.get('answer')),
                        'explanation':card.get('explanation',''),
                        'interaction':card.get('interaction',{'version':1,'type':'text','grading':'semantic'})})
        name=str(topic['id'])+'.md'
        (folder/name).write_text('\n'.join(lines)+'\n',encoding='utf-8')
        index += ['- ['+topic['title']+'](topics/'+name+')']
    (root/'阅读入口.md').write_text('\n'.join(index)+'\n',encoding='utf-8')


def run(args):
    document = trial.read(args.input)
    tasks = source_tasks(document, args.subject, args.count)
    examples = trial.load_few_shots(args.few_shots)
    for task in tasks:
        trial.generation_payload(task, examples)
    generation, review = trial.GEN.read_text(encoding='utf-8'), trial.REVIEW.read_text(encoding='utf-8')
    config = trial.configuration()
    replace_representatives = getattr(args,'replace_representatives',False)
    metadata = {'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(), 'subject': args.subject,
                'count': args.count, 'generation_sha256': trial.digest(generation), 'review_sha256': trial.digest(review),
                'few_shots_sha256': hashlib.sha256(args.few_shots.read_bytes()).hexdigest(), 'points': len(tasks),
                'backend':'codex_exec', 'model':config[2]}
    if replace_representatives:
        metadata['replace_representatives'] = True
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
    dispatch([task for task in tasks if task['point']['id'] in selected],
             lambda task: run_point(task, args.run_root, config, generation, review, examples,
                                    args.attempts, getattr(args, 'retry_incomplete', False)), args.workers)
    results = [trial.read(path) for task in tasks
               if (path := args.run_root / task['point']['id'] / 'result.json').exists()]
    status = {'points': len(tasks), 'counts': dict(Counter(result['status'] for result in results)),
              'complete': len(results) == len(tasks) and all(result['status'] == 'accepted' for result in results)}
    print(json.dumps(status), flush=True)
    if status['complete']:
        exported = assemble(document, tasks, results,replace_representatives=replace_representatives)
        output = args.run_root / 'structured-topics.enriched.json'
        if output.exists():
            if trial.read(output) != exported:
                raise ValueError('Existing export differs')
        else:
            trial.save(output, exported)
        render_reading(exported,args.run_root)
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
    parser.add_argument('--replace-representatives',action='store_true',
                        help='Use the first independently accepted new card as the initial representative in the derived local export; preserve upstream and source artifacts')
    parser.add_argument('--retry-incomplete', action='store_true',
                        help='Recover retained completed calls first, then retry failed pure-text calls within the attempt limit; preserve every prior receipt')
    args = parser.parse_args()
    with run_lock(args.run_root):
        return run(args)


if __name__ == '__main__':
    sys.exit(main())
