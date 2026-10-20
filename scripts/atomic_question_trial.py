"""Independent, local API trials for point-bound Flashcards; no site writes."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu.author_import import CompatibleClient, _object
from graspmemoedu.question_types import validate_interaction
from graspmemoedu.subject_registry import DEFAULTS

SUBJECTS = {row[0]: row[1] for row in DEFAULTS}
GEN = ROOT / 'prompts/atomic-question-generation.md'
REVIEW = ROOT / 'prompts/atomic-question-review.md'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write('\n')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def cases_at(path):
    cases = read(path)['cases']
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Source cases need unique identities')
    for case in cases:
        if not re.fullmatch(r'[a-z]+-[0-9]{2}', case['id']):
            raise ValueError('Unsafe case identity')
        if case['subject_id'] not in SUBJECTS or not all(
                isinstance(case[k], str) and case[k].strip()
                for k in ('chunk', 'target', 'question', 'answer')):
            raise ValueError('Incomplete source case')
    return cases


def source_for(case):
    return {'subject_id': case['subject_id'],
            'topic': {'id': 'local-trial-' + case['subject_id'], 'version': 'fixture-1'},
            'memory_chunk': {'id': 'chunk-' + case['id'], 'content': case['chunk']},
            'point': {'id': case['id'], 'target': case['target']},
            'seed_card': {'point_ids': [case['id']], 'question': case['question'],
                          'reference_answer': case['answer'],
                          'interaction': {'version': 1, 'type': 'text', 'grading': 'semantic'}},
            'existing_variants': case.get('existing_variants', [])}


def load_few_shots(path):
    catalog = read(path)
    if catalog.get('version') != 1 or not isinstance(catalog.get('subjects'), dict):
        raise ValueError('Invalid few-shot catalog')
    for subject, examples in catalog['subjects'].items():
        if subject not in SUBJECTS or not isinstance(examples, list) or not examples:
            raise ValueError('Unknown or empty few-shot subject')
        identities = set()
        for example in examples:
            if set(example) != {'source', 'output'} or example['source']['subject_id'] != subject:
                raise ValueError('Few-shot subject mismatch')
            ident = example['source']['point']['id']
            if ident in identities:
                raise ValueError('Duplicate few-shot point')
            identities.add(ident)
            validate_pair(example['output'], example['source'])
            if example['output']['status'] != 'ok':
                raise ValueError('Few-shot example is not a complete pair')
    return catalog['subjects']


def generation_payload(source, catalog):
    examples = [example for example in catalog.get(source['subject_id'], [])
                if example['source']['point']['id'] != source['point']['id']
                and example['source']['seed_card']['question'].strip() != source['seed_card']['question'].strip()]
    if not examples:
        raise ValueError('No approved same-subject few-shot examples for this point')
    return {'few_shot_examples': examples, 'task': source}


def validate_pair(candidate, source):
    if set(candidate) != {'point_id', 'status', 'reason', 'variants'}:
        raise ValueError('Unexpected response fields')
    if candidate['point_id'] != source['point']['id']:
        raise ValueError('Response changed the target point')
    if candidate['status'] == 'blocked':
        if candidate['variants'] != [] or not isinstance(candidate['reason'], str) or not candidate['reason'].strip():
            raise ValueError('Blocked result must explain itself and contain no cards')
        return
    if candidate['status'] != 'ok' or candidate['reason'] != '':
        raise ValueError('Invalid generation status')
    rows = candidate['variants']
    count = source.get('variant_count', 2)
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError('Exactly two Flashcards are required' if count == 2 else f'Exactly {count} Flashcards are required')
    seen = {digest({'question': re.sub(r'\s+', '', q['question']), 'interaction': q.get('interaction')})
            for q in [source['seed_card'], *source['existing_variants']]}
    for card in rows:
        if set(card) != {'point_ids', 'question', 'reference_answer', 'explanation', 'interaction'}:
            raise ValueError('Unexpected Flashcard fields')
        if card['point_ids'] != [source['point']['id']]:
            raise ValueError('Flashcard changed its point bindings')
        for key in ('question', 'reference_answer', 'explanation'):
            value = card[key]
            if not isinstance(value, str) or not value.strip() or len(value) > 15000 or '\x00' in value:
                raise ValueError('Missing or oversized Flashcard text')
            if re.search(r'''https?://|<!--|<\s*/?\s*[A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z_:][-A-Za-z0-9_:.]*(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s<>]+))?)*\s*/?>''',
                         re.sub(r'\$\$[\s\S]*?\$\$|\$[^$\n]+\$', '', value)):
                raise ValueError('Untrusted active markup or link')
        validate_interaction(card['interaction'], card['question'])
        if card['interaction'] is None:
            raise ValueError('Explicit interaction required')
        if card['interaction'].get('type') == 'true_false' and len(card['interaction']['statements']) != 1:
            raise ValueError('Only one judgment per point-bound card')
        if card['interaction'].get('type') == 'matching':
            spec = card['interaction']
            pairs = {pair['left']: pair['right'] for pair in spec['pairs']}
            aligned = [pairs[item['id']] for item in spec['left'] if item['id'] in pairs]
            if len(aligned) >= 2 and aligned == [item['id'] for item in spec['right']]:
                raise ValueError('Matching candidates initially reveal the full answer alignment')
        signature = digest({'question': re.sub(r'\s+', '', card['question']), 'interaction': card['interaction']})
        if signature in seen:
            raise ValueError('Duplicate Flashcard')
        seen.add(signature)


def validate_review(value, ident, count=2):
    if set(value) != {'point_id', 'approved', 'checks'} or value['point_id'] != ident:
        raise ValueError('Invalid review identity')
    checks = value['checks']
    if (type(value['approved']) is not bool or not isinstance(checks, list) or len(checks) != count):
        raise ValueError('Incomplete review')
    for index, row in enumerate(checks, 1):
        if (set(row) != {'index', 'approved', 'issues', 'rationale'} or row['index'] != index
                or type(row['approved']) is not bool or not isinstance(row['issues'], list)
                or not all(isinstance(s, str) and s.strip() for s in row['issues'])
                or not isinstance(row['rationale'], str) or not row['rationale'].strip()
                or row['approved'] != (not row['issues'])):
            raise ValueError('Inconsistent review verdict')
    if value['approved'] != all(row['approved'] for row in checks):
        raise ValueError('Inconsistent pair approval')


def configuration():
    path = Path.home() / '.codex/skills/ai-production-supervisor/scripts/run_ai_batch.py'
    spec = importlib.util.spec_from_file_location('configured_ai_producer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.load_env_file()
    key = os.environ.get('DEEPSEEK_CODEX_API_KEY')
    base = os.environ.get('AI_PRODUCER_BASE_URL', 'https://api.deepseek.com')
    model = os.environ.get('AI_PRODUCER_MODEL', 'deepseek-flash')
    if not key or model != 'deepseek-flash':
        raise ValueError('Configure the Codex production credential and deepseek-flash')
    return base, key, model


def call(client, folder, phase, prompt, payload):
    response_path = folder / (phase + '.response.json')
    if response_path.exists():
        return read(response_path)
    started = folder / (phase + '.started.json')
    if started.exists():
        raise ValueError('Previous request outcome unknown; do not blindly rebill')
    messages = [{'role': 'system', 'content': prompt},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    save(folder / (phase + '.request.json'), {'model': client.model, 'messages': messages,
         'thinking': True, 'reasoning_effort': 'low'})
    save(started, {'started_at': datetime.now(timezone.utc).isoformat()})
    response = client.complete_limited(messages, max_tokens=16000 if phase == 'generation' else 12000,
                                       thinking=True, reasoning_effort='low', stream=True)
    save(response_path, response)
    return response


def run_one(case, run_root, config, generation, review, catalog):
    folder = run_root / case['id']
    result_path = folder / 'result.json'
    if result_path.exists():
        return read(result_path)
    source = source_for(case)
    client = CompatibleClient(*config, timeout=300)
    result = {'id': case['id'], 'subject_id': case['subject_id'], 'source': source}
    try:
        print(case['id'] + ' generating', flush=True)
        candidate = _object(call(client, folder, 'generation', generation, generation_payload(source, catalog)))
        result['candidate'] = candidate
        validate_pair(candidate, source)
        if candidate['status'] == 'blocked':
            result['status'] = 'blocked'
        else:
            print(case['id'] + ' reviewing', flush=True)
            verdict = _object(call(client, folder, 'review', review, {'source': source, 'candidate': candidate}))
            validate_review(verdict, case['id'])
            result.update(review=verdict, status='accepted' if verdict['approved'] else 'rejected')
    except Exception as error:
        result.update(status='error', error_type=type(error).__name__,
                      error=str(error) if isinstance(error, ValueError) else type(error).__name__)
    save(result_path, result)
    print(case['id'] + ' ' + result['status'], flush=True)
    return result


def run(args):
    cases = cases_at(args.input)
    if args.only:
        selected = set(args.only)
        if selected - {case['id'] for case in cases}:
            raise ValueError('Unknown selected case')
        cases = [case for case in cases if case['id'] in selected]
    generation, review = GEN.read_text(encoding='utf-8'), REVIEW.read_text(encoding='utf-8')
    catalog = load_few_shots(args.few_shots)
    for case in cases:
        generation_payload(source_for(case), catalog)
    metadata = {'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
                'generation_sha256': hashlib.sha256(generation.encode()).hexdigest(),
                'review_sha256': hashlib.sha256(review.encode()).hexdigest(),
                'few_shots_sha256': hashlib.sha256(args.few_shots.read_bytes()).hexdigest(),
                'ids': [case['id'] for case in cases], 'messages_per_request': 2,
                'chat_history_included': False}
    path = args.run_root / 'run.json'
    if path.exists():
        if read(path) != metadata:
            raise ValueError('Run inputs or prompts changed; choose a fresh evidence directory')
    else:
        save(path, metadata)
        save(args.run_root / 'input-snapshot.json', {'cases': cases})
        save(args.run_root / 'few-shots-snapshot.json', {'version': 1, 'subjects': catalog})
        (args.run_root / 'generation-prompt.md').write_text(generation, encoding='utf-8')
        (args.run_root / 'review-prompt.md').write_text(review, encoding='utf-8')
    config = configuration()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_one, case, args.run_root, config, generation, review, catalog) for case in cases]
        results = [future.result() for future in as_completed(futures)]
    print(json.dumps(dict(Counter(result['status'] for result in results))), flush=True)


def question_lines(card):
    lines = [re.sub(r'\{\{blank:[A-Za-z][A-Za-z0-9_-]*\}\}', '____', card['question']), '']
    spec = card['interaction']
    for option in spec.get('options', []):
        lines.append('- ' + option['id'].upper() + '. ' + option['text'])
    for statement in spec.get('statements', []):
        lines.append('> ' + statement['text'])
    for side, title in [('left', '待配对位置或线索'), ('right', '全部候选内容')]:
        if side in spec:
            lines += ['', title + '：', '']
            lines += ['- ' + item['text'] for item in spec[side]]
    lines += ['', '**答案**：' + card['reference_answer'], '', '**解析**：' + card['explanation'], '']
    return lines


def render(args):
    cases = cases_at(args.input)
    results = {}
    for run_root in args.runs:
        for case in cases:
            path = run_root / case['id'] / 'result.json'
            if path.exists():
                results[case['id']] = read(path)
    args.output.mkdir(parents=True, exist_ok=True)
    index = ['# 跨学科可变设问', '', '每例包含新编记忆组块、目标原子记忆点、基准卡和两张由 API 生成的可变设问。', '']
    totals = Counter()
    for subject, name in SUBJECTS.items():
        subject_cases = [case for case in cases if case['subject_id'] == subject]
        if not subject_cases:
            continue
        lines = ['# ' + name + '：可变设问', '']
        accepted = 0
        for ordinal, case in enumerate(subject_cases, 1):
            result = results.get(case['id'], {'status': 'not_run'})
            totals[result['status']] += 1
            if result['status'] != 'accepted':
                continue
            accepted += 1
            lines += ['## ' + str(ordinal), '', '**记忆组块**：' + case['chunk'], '',
                      '**目标原子记忆点**：' + case['target'], '',
                      '**基准卡**：' + case['question'] + ' → ' + case['answer'], '']
            for letter, card in zip('AB', result['candidate']['variants']):
                lines += ['### 可变设问 ' + letter, ''] + question_lines(card)
            if case.get('citation'):
                lines += ['知识依据：[参考资料](' + case['citation'] + ')。', '']
        (args.output / (name + '.md')).write_text('\n'.join(lines), encoding='utf-8')
        index += ['- [' + name + '](' + name + '.md)：' + str(accepted) + ' 例，' + str(accepted * 2) + ' 张可变设问。']
    (args.output / 'README.md').write_text('\n'.join(index) + '\n', encoding='utf-8')
    report = {'counts': dict(totals), 'results': [{'id': case['id'], 'status': results.get(case['id'], {}).get('status', 'not_run')}
                                               for case in cases]}
    (args.output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['counts']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    produce = commands.add_parser('run')
    produce.add_argument('--input', type=Path, required=True)
    produce.add_argument('--run-root', type=Path, required=True)
    produce.add_argument('--few-shots', type=Path, required=True)
    produce.add_argument('--workers', type=int, choices=range(1, 5), default=1)
    produce.add_argument('--only', nargs='+')
    output = commands.add_parser('render')
    output.add_argument('--input', type=Path, required=True)
    output.add_argument('--runs', type=Path, nargs='+', required=True)
    output.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args) if args.command == 'run' else render(args)


if __name__ == '__main__':
    main()
