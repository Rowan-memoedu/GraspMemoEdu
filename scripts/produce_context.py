"""Plan/run local context tasks, independently review, reuse, and prepare imports."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu import context_production as cp
from graspmemoedu.author_import import CompatibleClient, _object
from scripts import atomic_question_trial as transport


def review_response(client, folder, payload):
    """Use shared API transport with enough output budget for multi-item review."""
    response = folder / 'review.response.json'
    if response.exists():
        return cp.read(response)
    started = folder / 'review.started.json'
    if started.exists():
        raise ValueError('Previous review outcome unknown; do not blindly rebill')
    messages = [{'role':'system','content':cp.REVIEW},
                {'role':'user','content':transport.json.dumps(payload,ensure_ascii=False)}]
    cp.save(folder / 'review.request.json', {'model':client.model,'messages':messages,
            'thinking':True,'reasoning_effort':'low','max_tokens':24576})
    cp.save(started, {'started_at':datetime.now(timezone.utc).isoformat()})
    value = client.complete_limited(messages, max_tokens=24576, thinking=True, reasoning_effort='low', stream=True)
    cp.save(response, value)
    return value


def codex_response(client, folder, phase, prompt, payload):
    messages = [{'role':'system','content':prompt},
                {'role':'user','content':transport.json.dumps(payload,ensure_ascii=False)}]
    response = folder/(phase+'.response.json')
    proof_path = folder/(phase+'.exec-proof.json')
    if response.exists():
        value,proof = cp.read(response),cp.read(proof_path)
        if cp.validate_exec_proof(proof,_object(value)) != messages:
            raise ValueError('Saved Codex Exec response belongs to a different request')
        return value
    started = folder/(phase+'.started.json')
    if started.exists():
        raise ValueError('Previous Codex Exec outcome unknown; inspect retained events before retrying')
    cp.save(folder/(phase+'.request.json'),{'backend':'codex-exec','model':client.model,
        'reasoning_effort':getattr(client,'reasoning_effort',None),'messages':messages})
    cp.save(started,{'started_at':datetime.now(timezone.utc).isoformat()})
    value = client.complete_limited(messages)
    proof = {'backend':'codex-exec','model':client.model,
        'reasoning_effort':getattr(client,'reasoning_effort',None),
        'request_sha256':cp.digest({'backend':'codex-exec','messages':messages}),
        'response_sha256':cp.digest(_object(value)),'receipt':deepcopy(client.last_receipt)}
    cp.validate_exec_proof(proof,_object(value))
    cp.save(response,value); cp.save(proof_path,proof)
    return value


def quality_policy(source, backend):
    return cp.QUALITY_POLICY if backend == 'codex-exec' and source['task']['kind'] == 'cards' else None


def plan(document, library, backend='api'):
    cp.validate_document(document)
    return [{'id': t['id'], 'kind': t['kind'], 'topic_id': t['topic_id'],
             'point_ids': t['point_ids'], 'reuse': cp.find_material(library, cp.task_source(document, t), backend,
                quality_policy(cp.task_source(document,t),backend)) is not None,
             'generation_required': t['kind'] == 'article' or 'provided_cards' not in t}
            for t in document['tasks']]


def retained_candidate(source, candidate_runs, backend='api'):
    """Reuse an intact generated response when only downstream review was fixed."""
    for root in candidate_runs:
        metadata = cp.read(root / 'run.json')
        if metadata.get('backend', 'api') != backend:
            continue
        if metadata['generation_prompt_sha256'] != cp.digest(cp.GENERATION):
            continue
        previous = cp.read(root / 'input-snapshot.json')
        tasks = [t for t in previous['tasks'] if t['id'] == source['task']['id']]
        if not tasks or cp.digest(cp.task_source(previous, tasks[0])) != cp.digest(source):
            continue
        for response in sorted((root / source['task']['id']).glob('attempt-*/generation.response.json'), reverse=True):
            candidate = _object(cp.read(response))
            try:
                cp.validate_candidate(candidate, source)
                if backend == 'codex-exec':
                    proof_path = response.with_name('generation.exec-proof.json')
                    if not proof_path.exists():
                        continue
                    messages = cp.validate_exec_proof(cp.read(proof_path),candidate)
                    payload = transport.json.loads(messages[-1]['content'])
                    if not isinstance(payload,dict):
                        continue
                    payload.pop('retry_feedback',None)
                    if messages[0]['content'] != cp.GENERATION or payload != source:
                        continue
            except ValueError:
                continue
            return candidate, str(response)
    return None


def make_client(config, work, backend, codex_options):
    if backend == 'api':
        return CompatibleClient(*config, timeout=300)
    if backend != 'codex-exec':
        raise ValueError('Unknown generation backend')
    from graspmemoedu.codex_exec_client import CodexExecClient
    return CodexExecClient(work / 'codex-exec', **(codex_options or {}))


def run_task(source, root, library, config, attempts, candidate_runs=(), backend='api', codex_options=None):
    task = source['task']
    policy = quality_policy(source,backend)
    folder = root / task['id']
    final = folder / 'result.json'
    if final.exists():
        result = cp.read(final)
        if result['source_sha256'] != cp.digest(source):
            raise ValueError('Saved task source changed')
        if result.get('backend', 'api') != backend:
            raise ValueError('Saved task execution backend differs')
        if result.get('status') == 'accepted' and backend == 'codex-exec':
            record = cp.read(library/(result['record_sha256']+'.json'))
            cp.validate_record(record)
            if (record['evidence'].get('backend','api') != backend
                    or cp.task_key(record['source']) != cp.task_key(source) or result.get('source') != source
                    or record['candidate'] != result['candidate'] or record['review'] != result['review']):
                raise ValueError('Saved result differs from its approved Exec record')
            if policy: cp.require_quality(record)
        return result
    existing = cp.find_material(library, source, backend, policy)
    result = {'id': task['id'], 'source_sha256': cp.digest(source), 'source': source, 'backend': backend}
    if existing:
        result.update(status='accepted', origin='library', candidate=existing['candidate'],
                      review=existing['review'], record_sha256=existing['record_sha256'])
        cp.save(final, result)
        print(task['id'] + ' reused', flush=True)
        return result
    feedback, retry_retained = None, None
    for ordinal in range(1, attempts + 1):
        work = folder / f'attempt-{ordinal:02d}'
        receipt = work / 'result.json'
        if receipt.exists():
            attempt = cp.read(receipt)
        else:
            attempt = {'attempt': ordinal, 'status': 'error'}
            phase = 'generation'
            try:
                generation_proof = None
                client = make_client(config, work, backend, codex_options)
                if retry_retained:
                    candidate = retry_retained['candidate']
                    generation_proof = retry_retained.get('generation_evidence')
                elif 'provided_cards' in task:
                    candidate = {'cards': deepcopy(task['provided_cards'])}
                    cp.save(work / 'provided-candidate.json', candidate)
                elif ordinal == 1 and (retained := retained_candidate(source, candidate_runs, backend)):
                    candidate, previous_path = retained
                    cp.save(work / 'retained-generation.json', {'path': previous_path, 'candidate_sha256': cp.digest(candidate)})
                    if backend == 'codex-exec':
                        generation_proof = cp.read(Path(previous_path).with_name('generation.exec-proof.json'))
                    print(task['id'] + ' retaining generated response', flush=True)
                else:
                    payload = deepcopy(source)
                    if feedback:
                        payload['retry_feedback'] = feedback
                    print(f'{task["id"]} attempt={ordinal} generating', flush=True)
                    candidate = _object(codex_response(client,work,'generation',cp.GENERATION,payload)
                        if backend == 'codex-exec' else transport.call(client, work, 'generation', cp.GENERATION, payload))
                    if backend == 'codex-exec':
                        generation_proof = cp.read(work/'generation.exec-proof.json')
                attempt['candidate'] = candidate
                cp.validate_candidate(candidate, source)
                if generation_proof: attempt['generation_evidence'] = generation_proof
                learner, view = None, None
                if policy:
                    cp.validate_learning_text(candidate)
                    view = cp.learner_view(candidate['cards'])
                    phase = 'learner'
                    learner = retry_retained.get('learner_evidence') if retry_retained else None
                    if learner is None:
                        print(f'{task["id"]} attempt={ordinal} checking learner view', flush=True)
                        blind = _object(codex_response(client,work,'learner',cp.LEARNER_REVIEW,cp.learner_payload(view)))
                        learner = {'result':blind,'proof':cp.read(work/'learner.exec-proof.json')}
                    attempt['learner_evidence'] = learner
                    cp.validate_learner_review(learner['result'],view)
                if learner and not learner['result']['approved']:
                    attempt.update(status='rejected',learner_rejected=True)
                else:
                    phase = 'review'
                    print(f'{task["id"]} attempt={ordinal} reviewing source', flush=True)
                    review_payload = {'source':source,'candidate':candidate}
                    if policy: review_payload = cp.quality_review_payload(review_payload,view,learner['result'])
                    review = _object(codex_response(client,work,'review',cp.REVIEW,review_payload)
                        if backend == 'codex-exec' else review_response(client, work, review_payload))
                    attempt['review'] = review
                    cp.validate_review(review, candidate, task['kind'])
                    if backend == 'codex-exec':
                        evidence = {'backend':'codex-exec','review':cp.read(work/'review.exec-proof.json')}
                        if generation_proof: evidence['generation'] = generation_proof
                        if policy: evidence.update(quality_policy=policy,learner=learner)
                        attempt['exec_evidence'] = evidence
                        cp.validate_exec_evidence(source,candidate,review,evidence)
                    attempt['status'] = 'accepted' if review['approved'] else 'rejected'
            except Exception as error:
                attempt.update(error_type=type(error).__name__,
                               error=str(error) if isinstance(error, ValueError) else type(error).__name__)
                if any((work / (p + '.started.json')).exists() and not (work / (p + '.response.json')).exists()
                       for p in ('generation', 'learner', 'review')):
                    attempt['status'] = 'outcome_unknown'
                elif phase in ('learner','review') and (work/(phase+'.response.json')).exists():
                    attempt['status'] = phase+'_invalid'
                elif not isinstance(error, ValueError):
                    attempt['status'] = 'internal_error'
            cp.save(receipt, attempt)
        result.update(status=attempt['status'], attempt=ordinal)
        if attempt['status'] == 'accepted':
            evidence = {'run':str(work),'backend':backend,'generation_prompt_sha256':cp.digest(cp.GENERATION),
                        'review_prompt_sha256':cp.digest(cp.REVIEW),**attempt.get('exec_evidence',{})}
            record = cp.accepted_record(source, attempt['candidate'], attempt['review'],
                                        evidence)
            cp.save(library / (record['record_sha256'] + '.json'), record)
            result.update(origin='authored' if 'provided_cards' in task else 'generated',
                          candidate=attempt['candidate'], review=attempt['review'], record_sha256=record['record_sha256'])
            if task['kind'] == 'article':
                result['located_analysis'] = cp.evidence_locations(attempt['candidate'], source)
            break
        feedback = (attempt.get('learner_evidence',{}).get('result',{}).get('checks')
                    if attempt.get('learner_rejected') else attempt.get('review', {}).get('checks'))
        feedback = feedback or attempt.get('error', 'Candidate rejected')
        retry_retained = None
        if attempt['status'] in ('learner_invalid','review_invalid'):
            retry_retained = {k:attempt[k] for k in ('candidate','generation_evidence') if k in attempt}
            if attempt['status'] == 'review_invalid' and attempt.get('learner_evidence'):
                retry_retained['learner_evidence'] = attempt['learner_evidence']
        result['error'] = feedback
        if ('provided_cards' in task and attempt['status'] == 'rejected') or attempt['status'] in ('outcome_unknown', 'internal_error'):
            break
    cp.save(final, result)
    print(task['id'] + ' ' + result['status'], flush=True)
    return result


def run(args):
    document = cp.read(args.input)
    cp.validate_document(document)
    if 'json' not in cp.GENERATION.lower() or 'json' not in cp.REVIEW.lower():
        raise ValueError('JSON-mode prompts must explicitly request JSON')
    selected = set(args.only or [t['id'] for t in document['tasks']])
    if selected - {t['id'] for t in document['tasks']}:
        raise ValueError('Unknown selected task')
    backend = getattr(args, 'backend', 'api')
    codex_options = {'model': getattr(args, 'codex_model', None),
                     'executable': getattr(args, 'codex_executable', None),
                     'timeout': getattr(args, 'codex_timeout', 900)}
    metadata = {'schema': 1, 'input_sha256': cp.digest(document),
                'generation_prompt_sha256': cp.digest(cp.GENERATION), 'review_prompt_sha256': cp.digest(cp.REVIEW)}
    # Preserve legacy API run manifests; Codex runs explicitly bind their engine.
    if backend != 'api':
        metadata.update(backend=backend,codex_options=codex_options,quality_policy=cp.QUALITY_POLICY,
                        learner_prompt_sha256=cp.digest(cp.LEARNER_REVIEW))
    cp.save(args.run_root / 'run.json', metadata)
    cp.save(args.run_root / 'input-snapshot.json', document)
    prompts = {'generation':cp.GENERATION,'review':cp.REVIEW}
    if backend == 'codex-exec': prompts.update(learner_review=cp.LEARNER_REVIEW,quality_policy=cp.QUALITY_POLICY)
    cp.save(args.run_root / 'prompts.json', prompts)
    tasks = [cp.task_source(document, t) for t in document['tasks'] if t['id'] in selected]
    needs_api = backend == 'api' and any(not (args.run_root / s['task']['id'] / 'result.json').exists()
                    and not cp.find_material(args.library, s, backend) for s in tasks)
    config = transport.configuration() if needs_api else None
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_task, s, args.run_root, args.library, config, args.attempts,
                               args.candidate_runs, backend, codex_options) for s in tasks]
        for future in as_completed(futures):
            future.result()
    results = [cp.read(p) for t in document['tasks']
               if (p := args.run_root / t['id'] / 'result.json').exists()]
    report = {'total': len(document['tasks']), 'finished': len(results),
              'accepted': sum(r['status'] == 'accepted' for r in results),
              'reused': sum(r.get('origin') == 'library' for r in results),
              'complete': len(results) == len(document['tasks']) and all(r['status'] == 'accepted' for r in results)}
    # A new report name preserves evidence when --only incrementally finishes a run.
    cp.save(args.run_root / ('report-' + cp.digest(report)[:12] + '.json'), report)
    print(transport.json.dumps(report), flush=True)
    return 0 if report['complete'] else 2


def explicit_audits(source, roots):
    """Reuse complete explicit reviews only for their identical source Topics."""
    if not roots:
        return set(), set(), []
    from graspmemoedu import context_explicit as ce
    topics = {topic['id']: topic for topic in source['topics']}
    if len(topics) != len(source['topics']):
        raise ValueError('Duplicate source Topic identity')
    points, cards, records = set(), set(), {}
    for root in roots:
        for path in sorted(Path(root).glob('*/result.json')):
            record = cp.read(path)
            ce.require_quality(record)
            topic = ce.record_topic(record)
            if topic is None or topic['id'] not in topics:
                continue
            if topic != topics[topic['id']]:
                raise ValueError('Explicit source Topic differs from its complete independent review: '+topic['id'])
            previous = records.get(topic['id'])
            if previous and previous['record_sha256'] != record['record_sha256']:
                raise ValueError('Conflicting explicit reviews for the same source Topic')
            point_ids = [point['id'] for section in topic['sections'] for point in section['memory_points']]
            initial_cards = [card['id'] for section in topic['sections'] for card in section['flashcards']]
            points.update(point_ids)
            cards.update(cp.digest(cp.normalize_card(card)) for section in record['candidate']['sections']
                         for card in section['cards'])
            records[topic['id']] = {'kind':'explicit-review-reuse','topic_id':topic['id'],
                'quality_policy':cp.QUALITY_POLICY,
                'record_sha256':record['record_sha256'],'record_path':str(path),
                'source_topic_sha256':cp.digest(topic),'point_ids':point_ids,'initial_card_ids':initial_cards}
    return points, cards, list(records.values())


def assemble(args):
    """Attach accepted review cards to authored structured sources, then compile."""
    from scripts.prepare_structured_atomic import prepare
    source = cp.read(args.source)
    if source.get('schema') != 2:
        raise ValueError('Structured Topic schema 2 source required')
    document = deepcopy(source)
    topics = {t['id']: t for t in document['topics']}
    receipts = {}
    for root in args.runs:
        for path in sorted(root.glob('*/result.json')):
            result = cp.read(path)
            if result['status'] != 'accepted':
                continue
            record = cp.read(args.library / (result['record_sha256'] + '.json'))
            cp.validate_record(record)
            if quality_policy(record['source'],record['evidence'].get('backend','api')):
                cp.require_quality(record)
            if (cp.task_key(record['source']) != cp.task_key(result['source'])
                    or record['evidence'].get('backend','api') != result.get('backend','api')
                    or record['candidate'] != result['candidate'] or record['review'] != result['review']):
                raise ValueError('Accepted result differs from reviewed library record')
            cp.validate_candidate(result['candidate'], result['source'])
            cp.validate_review(result['review'], result['candidate'], result['source']['task']['kind'])
            if not result['review']['approved']:
                raise ValueError('Rejected result marked accepted')
            ident = result['source']['task']['id']
            if ident in receipts and receipts[ident]['candidate'] != result['candidate']:
                raise ValueError('Conflicting accepted candidates across runs')
            receipts[ident] = result
    tasks = cp.read(args.tasks)
    cp.validate_document(tasks)
    if set(receipts) != {t['id'] for t in tasks['tasks']}:
        raise ValueError('Every task must have one accepted result before preparing')
    audits, audited_cards, reused_explicit = explicit_audits(source, getattr(args,'explicit_runs',[]))
    coverage, current_card_titles = [], {}
    for task in tasks['tasks']:
        result = receipts[task['id']]
        if cp.digest(cp.task_source(tasks, task)) != result['source_sha256']:
            raise ValueError('Task input changed after review')
        if task['kind'] == 'article':
            continue
        if task['topic_id'] not in topics:
            raise ValueError('Unknown destination Topic')
        topic = topics[task['topic_id']]
        if 'provided_cards' in task:
            audits.update(task['point_ids'])
            audited_cards.update(cp.digest(cp.normalize_card(c)) for c in result['candidate']['cards'])
            if quality_policy(result['source'],result.get('backend','api')):
                for index,card in enumerate(result['candidate']['cards'],1):
                    current_card_titles[cp.digest(cp.normalize_card(card))] = cp.neutral_title(index)
            continue
        for ordinal, raw in enumerate(result['candidate']['cards'], 1):
            card = cp.normalize_card(raw)
            script = card.pop('audio_script', None)
            ident = 'context-' + cp.digest({'task': task['id'], 'card': raw})[:24]
            card.update(id=ident, title=cp.neutral_title(ordinal), learning_phases=['review'])
            if script:
                # Bind real audio in a later, separately verified production step.
                card['audio_source'] = {'mode': task.get('audio_mode'), 'transcript': script}
            topic['sections'][0]['flashcards'].append(card)
            coverage.append({'task_id': task['id'], 'topic_id': topic['id'], 'card_id': ident,
                             'format': task['format'], 'point_ids': card['point_ids'],
                             'interaction': card['interaction']['type'], 'record_sha256': result['record_sha256']})
    for topic in document['topics']:
        for section in topic['sections']:
            point_ids = {p['id'] for p in section['memory_points']}
            if not point_ids <= audits:
                raise ValueError('Initial source cards need independent audit for every point')
            section.update(import_eligible=True, status='ai_passed')
    for topic in source['topics']:
        for section in topic['sections']:
            for card in section['flashcards']:
                audited = {k: v for k, v in card.items() if k in {
                    'point_ids', 'question', 'reference_answer', 'explanation', 'interaction', 'assessment'}}
                if card.get('audio_source'):
                    audited['audio_script'] = card['audio_source']['transcript']
                spec = audited.get('interaction',{})
                if spec.get('version') == 2 and spec.get('type') == 'composite' and all(
                        'text' in section for section in spec.get('sections',[])):
                    audited = cp.normalize_card(audited)
                if cp.digest(audited) not in audited_cards:
                    raise ValueError('An original source card differs from its independent audit')
                # A provided card's arbitrary author title is not part of its
                # blind review. Project the exact neutral reviewed title.
                title = current_card_titles.get(cp.digest(audited))
                if title is not None:
                    destination = next(c for s in topics[topic['id']]['sections'] for c in s['flashcards'] if c['id']==card['id'])
                    destination['title'] = title
    if args.compile and any('audio_source' in c for t in document['topics'] for s in t['sections'] for c in s['flashcards']):
        raise ValueError('Bind and verify real audio before compiling media content')
    args.output.mkdir(parents=True, exist_ok=False)
    source_path = args.output / 'structured-topics.json'
    cp.save(source_path, document)
    cp.save(args.output / 'coverage.json', coverage)
    if reused_explicit:
        cp.save(args.output / 'explicit-audits.json', reused_explicit)
    cp.save(args.output / 'article-analyses.json', [
        {'task_id': task['id'], 'analysis': cp.evidence_locations(receipts[task['id']]['candidate'], cp.task_source(tasks, task))}
        for task in tasks['tasks'] if task['kind'] == 'article'])
    if not args.compile:
        return
    prepare(argparse.Namespace(input=source_path, output=args.output / 'bundle', subject=tasks['subject_id'],
        course_id=args.course_id, course_title=args.course_title, unit_id=args.unit_id, unit_title=args.unit_title,
        unit_parent=None, topic_prefix='', title_suffix='', node=args.node,
        reset_reading_progress=False, replace_atomic_and_reset_progress=False,
        test_material=getattr(args,'test_material',False)))


def from_reading(args):
    if args.points.is_dir():
        known={'points':[]}; points={}
        for path in sorted(args.points.glob('*.json')):
            value=cp.read(path)
            if 'points' not in value or 'subject_id' not in value: continue
            if 'subject_id' in known and known['subject_id'] != value['subject_id']:
                raise ValueError('Known point directory contains different subjects')
            known['subject_id']=value['subject_id']
            for point in value['points']:
                if point['id'] in points and points[point['id']] != point:
                    raise ValueError('Conflicting known point definition')
                points[point['id']]=point
        known['points']=list(points.values())
    else:
        known=cp.read(args.points)
    exported=cp.read(args.input)
    value=cp.from_reading_export(exported,known,args.candidates_only)
    cp.save(args.output,value)
    cp.save(args.output.with_suffix('.export.json'),exported)
    print(transport.json.dumps({'tasks':len(value['tasks']),'deferred':len(value['deferred_records']),
                               'output':str(args.output)},ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'run'):
        p = sub.add_parser(name)
        p.add_argument('--input', type=Path, required=True)
        p.add_argument('--library', type=Path, required=True)
        p.add_argument('--backend', choices=('api', 'codex-exec'), default='api',
                       help='Keep API compatibility, or run every new generation/review through Codex Exec')
        if name == 'run':
            p.add_argument('--run-root', type=Path, required=True)
            p.add_argument('--workers', type=int, choices=range(1, 9), default=2)
            p.add_argument('--attempts', type=int, choices=range(1, 7), default=3)
            p.add_argument('--only', nargs='+')
            p.add_argument('--candidate-runs', type=Path, nargs='*', default=[])
            p.add_argument('--codex-model', help='Optional explicit override; default uses the current Codex model')
            p.add_argument('--codex-executable')
            p.add_argument('--codex-timeout', type=int, default=900)
    p = sub.add_parser('prepare')
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--tasks', type=Path, required=True)
    p.add_argument('--runs', type=Path, nargs='+', required=True)
    p.add_argument('--explicit-runs', type=Path, nargs='+', default=[],
                   help='Reuse accepted explicit Topic reviews when the complete source Topic is unchanged')
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--compile', action='store_true')
    p.add_argument('--test-material', action='store_true')
    p.add_argument('--node', default='node')
    p.add_argument('--course-id', required=True)
    p.add_argument('--course-title', required=True)
    p.add_argument('--unit-id', required=True)
    p.add_argument('--unit-title', required=True)
    p=sub.add_parser('from-reading',help='Prepare article-analysis tasks from a private reading export')
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--points',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--candidates-only',action='store_true',help='Analyze only unlinked records marked for learning')
    args = parser.parse_args()
    if args.command == 'plan':
        print(transport.json.dumps(plan(cp.read(args.input), args.library, args.backend), ensure_ascii=False, indent=2))
    elif args.command == 'run':
        return run(args)
    elif args.command == 'prepare':
        assemble(args)
    else:
        from_reading(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
