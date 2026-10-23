"""Plan/run local context tasks, independently review, reuse, and prepare imports."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
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


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def failed_source_evidence(work, messages, successful_proofs):
    """Authenticate a terminal empty failure, never infer failure from elapsed time."""
    from graspmemoedu.codex_exec_client import assess_events
    successful = {p['receipt']['call_id']: Path(p['receipt']['call_dir']).resolve()
                  for p in successful_proofs}
    extras = []
    for directory in sorted((work / 'codex-exec').glob('*')):
        if not directory.is_dir():
            continue
        receipt = cp.read(directory / 'receipt.json')
        if receipt.get('call_id') in successful:
            if directory.resolve() != successful[receipt['call_id']]:
                raise ValueError('Successful call identity points to a different directory')
        else:
            extras.append((directory.resolve(), receipt))
    if len(extras) != 1:
        raise ValueError('Source-only recovery requires exactly one retained failed source call')
    directory, receipt = extras[0]
    if (receipt.get('schema') != 1 or receipt.get('backend') != 'codex-exec'
            or receipt.get('status') != 'failed' or type(receipt.get('exit_code')) is not int
            or receipt['exit_code'] == 0 or receipt.get('model_execution_count') != 1
            or receipt.get('automatic_retries') != 0 or receipt.get('timed_out') is not False
            or receipt.get('error_code') != 'nonzero_exit'
            or receipt.get('isolation', {}).get('passed') is not True
            or receipt.get('call_id') != directory.name
            or Path(receipt.get('call_dir', '')).resolve() != directory
            or not receipt.get('finished_at') or not receipt.get('model')
            or not receipt.get('reasoning_effort')
            or receipt.get('usage') is not None or receipt.get('usage_events') != []):
        raise ValueError('Source call is not a confirmed terminal empty execution failure')
    if ((directory / 'result.json').exists() or not (directory / 'final.txt').is_file()
            or (directory / 'final.txt').read_bytes() != b''):
        raise ValueError('Source-only recovery refuses partial or ambiguous output')
    request = cp.read(work / 'review.request.json')
    if (request.get('backend') != 'codex-exec' or request.get('messages') != messages
            or request.get('model') != receipt['model']
            or request.get('reasoning_effort') != receipt['reasoning_effort']
            or cp.read(directory / 'messages.json') != messages
            or file_sha(directory / 'prompt.txt') != receipt.get('input_sha256')
            or not (directory / 'prompt.txt').read_text(encoding='utf-8').endswith(
                transport.json.dumps(messages, ensure_ascii=False, allow_nan=False))):
        raise ValueError('Failed source call has different policy, input or executed prompt')
    raw = (directory / 'events.jsonl').read_text(encoding='utf-8')
    rows = [transport.json.loads(line) for line in raw.splitlines() if line.strip()]
    assessed = assess_events(raw)
    final_message = assessed.pop('final_message')
    if ([row.get('type') for row in rows] != ['thread.started', 'turn.started', 'error', 'turn.failed']
            or not isinstance(rows[2].get('message'), str) or not rows[2]['message'].strip()
            or rows[3].get('error', {}).get('message') != rows[2]['message']
            or final_message is not None or assessed != receipt.get('event_check')
            or assessed['usage'] is not None or assessed['usage_events'] != []
            or assessed['unexpected_items'] != []):
        raise ValueError('Source failure is not an explicit empty failed turn')
    return {'call_dir': str(directory), 'call_id': receipt['call_id'],
            'model': receipt['model'], 'reasoning_effort': receipt['reasoning_effort'],
            'files': {name: file_sha(directory / name) for name in
                      ('receipt.json', 'messages.json', 'prompt.txt', 'events.jsonl', 'final.txt')}}


def retained_learner(source, roots):
    """Explicitly reuse two proven stages after one confirmed empty source failure."""
    if source['task']['kind'] != 'cards' or 'provided_cards' in source['task']:
        raise ValueError('Source-only recovery requires generated cards and their generation proof')
    matches = []
    for root in roots:
        root = Path(root).resolve()
        snapshot = cp.read(root / 'input-snapshot.json')
        tasks = [t for t in snapshot['tasks'] if t['id'] == source['task']['id']]
        if not tasks:
            continue
        cp.validate_document(snapshot)
        metadata, prompts = cp.read(root / 'run.json'), cp.read(root / 'prompts.json')
        if (cp.task_source(snapshot, tasks[0]) != source
                or metadata.get('input_sha256') != cp.digest(snapshot)
                or metadata.get('backend') != 'codex-exec'
                or metadata.get('quality_policy') != cp.QUALITY_POLICY
                or metadata.get('generation_prompt_sha256') != cp.digest(cp.GENERATION)
                or metadata.get('review_prompt_sha256') != cp.digest(cp.REVIEW)
                or prompts.get('generation') != cp.GENERATION or prompts.get('review') != cp.REVIEW
                or prompts.get('quality_policy') != cp.QUALITY_POLICY
                or not cp.is_learner_review_prompt(prompts.get('learner_review'))
                or metadata.get('learner_prompt_sha256') != cp.digest(prompts['learner_review'])):
            raise ValueError('Retained run has different source, backend or policy')
        folder = root / source['task']['id']
        result = cp.read(folder / 'result.json')
        ordinal = result.get('attempt')
        if (result.get('id') != source['task']['id'] or result.get('source') != source
                or result.get('source_sha256') != cp.digest(source)
                or result.get('backend') != 'codex-exec' or result.get('status') != 'outcome_unknown'
                or type(ordinal) is not int or ordinal < 1):
            raise ValueError('Source-only recovery requires an unfinished task with a retained terminal failure')
        work = folder / f'attempt-{ordinal:02d}'
        if any(p.name > work.name for p in folder.glob('attempt-*')):
            raise ValueError('A later attempt exists; do not replay an earlier failure')
        attempt = cp.read(work / 'result.json')
        if (attempt.get('attempt') != ordinal or attempt.get('status') != 'outcome_unknown'
                or 'review' in attempt or 'exec_evidence' in attempt
                or any((work / name).exists() for name in ('review.response.json', 'review.exec-proof.json'))
                or not (work / 'review.started.json').is_file()
                or not {'candidate', 'generation_evidence', 'learner_evidence'} <= set(attempt)):
            raise ValueError('Source-only recovery refuses completed, rejected or partially recorded reviews')
        candidate = attempt['candidate']
        cp.validate_candidate(candidate, source); cp.validate_learning_text(candidate)
        generation, learner = attempt['generation_evidence'], attempt['learner_evidence']
        generated = cp.validate_exec_proof(generation, candidate)
        payload = transport.json.loads(generated[1]['content'])
        if not isinstance(payload, dict):
            raise ValueError('Retained generation input is not an object')
        payload.pop('retry_feedback', None)
        if generated[0]['content'] != cp.GENERATION or payload != source:
            raise ValueError('Generation proof belongs to different input or prompt')
        view = cp.learner_view(candidate['cards'])
        cp.validate_learner_review(learner['result'], view)
        blinded = cp.validate_exec_proof(learner['proof'], learner['result'])
        if (not learner['result']['approved'] or not cp.is_learner_review_prompt(blinded[0]['content'])
                or transport.json.loads(blinded[1]['content']) != cp.learner_payload(view)
                or generation['receipt']['call_id'] == learner['proof']['receipt']['call_id']):
            raise ValueError('Learner proof is unapproved, not independent or has a different public view')
        files = {}
        for role, response, proof, messages in (
                ('generation', candidate, generation, generated),
                ('learner', learner['result'], learner['proof'], blinded)):
            for suffix, value in (('response', response), ('exec-proof', proof)):
                path = work / f'{role}.{suffix}.json'
                if path.exists() and cp.read(path) != value:
                    raise ValueError('Retained role artifact differs from its actual proof')
                files[path.name] = (path.read_bytes() if path.exists() else
                    (transport.json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8'))
            request_path = work / f'{role}.request.json'
            if request_path.exists():
                if cp.read(request_path).get('messages') != messages:
                    raise ValueError('Retained role request differs from its executed messages')
                files[request_path.name] = request_path.read_bytes()
        review_payload = cp.quality_review_payload({'source': source, 'candidate': candidate}, view, learner['result'])
        messages = [{'role': 'system', 'content': cp.REVIEW}, {'role': 'user',
                    'content': transport.json.dumps(review_payload, ensure_ascii=False)}]
        failure = failed_source_evidence(work, messages, [generation, learner['proof']])
        manifest = {'run': str(root), 'attempt': str(work), 'source_sha256': cp.digest(source),
                    'candidate_sha256': cp.digest(candidate), 'learner_view_sha256': cp.digest(view),
                    'successful_call_ids': [generation['receipt']['call_id'], learner['proof']['receipt']['call_id']],
                    'failed_source': failure,
                    'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                    'bindings': {str(path): file_sha(path) for path in
                                 (root/'run.json', root/'input-snapshot.json', root/'prompts.json',
                                  folder/'result.json', work/'result.json', work/'review.request.json',
                                  work/'review.started.json')}}
        matches.append({'candidate': candidate, 'generation_evidence': generation,
                        'learner_evidence': learner, 'manifest': manifest, 'files': files})
    if len(matches) != 1:
        raise ValueError('Select exactly one eligible retained source failure for each requested task')
    return matches[0]


def copy_retained_learner(record, work):
    """Keep original role bytes and failure evidence; do not fabricate new calls."""
    cp.save(work / 'reused-learner.json', record['manifest'])
    files = dict(record['files'])
    failure = record['manifest']['failed_source']
    for name, sha in failure['files'].items():
        data = (Path(failure['call_dir']) / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError('Failed source evidence changed after authentication')
        files['reused-source-failure/' + name] = data
    for name, data in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError('Retained role bytes changed in the new run')
        else:
            with path.open('xb') as stream:
                stream.write(data)


def run_task(source, root, library, config, attempts, candidate_runs=(), backend='api', codex_options=None,
             reuse_learner_runs=()):
    task = source['task']
    policy = quality_policy(source,backend)
    folder = root / task['id']
    final = folder / 'result.json'
    retained = None
    if reuse_learner_runs:
        if backend != 'codex-exec' or candidate_runs or Path(root).resolve() in map(lambda p: Path(p).resolve(), reuse_learner_runs):
            raise ValueError('Source-only recovery requires Codex Exec, a new run root and no candidate-runs')
        retained = retained_learner(source, reuse_learner_runs)
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
    feedback, retry_retained = None, retained
    for ordinal in range(1, attempts + 1):
        work = folder / f'attempt-{ordinal:02d}'
        receipt = work / 'result.json'
        if receipt.exists():
            attempt = cp.read(receipt)
        else:
            attempt = {'attempt': ordinal, 'status': 'error'}
            phase = 'generation'
            if ordinal == 1 and retained:
                copy_retained_learner(retained, work)
                attempt['learner_reuse'] = retained['manifest']
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
                        if attempt.get('learner_reuse'):
                            if evidence['review']['receipt']['call_id'] in (
                                    attempt['learner_reuse']['successful_call_ids'] +
                                    [attempt['learner_reuse']['failed_source']['call_id']]):
                                raise ValueError('Source-only recovery requires a fresh independent source call')
                            evidence['learner_reuse'] = attempt['learner_reuse']
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
    reuse_learner_runs = getattr(args, 'reuse_learner_runs', ())
    tasks = [cp.task_source(document, t) for t in document['tasks'] if t['id'] in selected]
    if reuse_learner_runs:
        if (backend != 'codex-exec' or args.candidate_runs
                or args.run_root.resolve() in {Path(p).resolve() for p in reuse_learner_runs}):
            raise ValueError('Source-only recovery requires Codex Exec, a new run root and no candidate-runs')
        for source in tasks:
            retained_learner(source, reuse_learner_runs)  # Validate every task before any model invocation.
    codex_options = {'model': getattr(args, 'codex_model', None),
                     'executable': getattr(args, 'codex_executable', None),
                     'timeout': getattr(args, 'codex_timeout', 900)}
    metadata = {'schema': 1, 'input_sha256': cp.digest(document),
                'generation_prompt_sha256': cp.digest(cp.GENERATION), 'review_prompt_sha256': cp.digest(cp.REVIEW)}
    # Preserve legacy API run manifests; Codex runs explicitly bind their engine.
    if backend != 'api':
        metadata.update(backend=backend,codex_options=codex_options,quality_policy=cp.QUALITY_POLICY,
                        learner_prompt_sha256=cp.digest(cp.LEARNER_REVIEW))
    if reuse_learner_runs:
        metadata['reuse_learner_runs'] = [str(Path(p).resolve()) for p in reuse_learner_runs]
    cp.save(args.run_root / 'run.json', metadata)
    cp.save(args.run_root / 'input-snapshot.json', document)
    prompts = {'generation':cp.GENERATION,'review':cp.REVIEW}
    if backend == 'codex-exec': prompts.update(learner_review=cp.LEARNER_REVIEW,quality_policy=cp.QUALITY_POLICY)
    cp.save(args.run_root / 'prompts.json', prompts)
    needs_api = backend == 'api' and any(not (args.run_root / s['task']['id'] / 'result.json').exists()
                    and not cp.find_material(args.library, s, backend) for s in tasks)
    config = transport.configuration() if needs_api else None
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_task, s, args.run_root, args.library, config, args.attempts,
                               args.candidate_runs, backend, codex_options, reuse_learner_runs) for s in tasks]
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
            p.add_argument('--reuse-learner-runs', type=Path, nargs='+', default=[],
                           help='Explicitly reuse proven generation/blind review after a terminal empty source-call failure')
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
