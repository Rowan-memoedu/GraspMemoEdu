"""Prepare independent author packages from an explicit local plan; never install."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from math_learning.author_import import prepare_topic
from math_learning.catalog import Catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=1, choices=range(1, 5))
    parser.add_argument('--topic', action='append', help='Prepare only these Topic IDs')
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    entries = plan['topics']
    ids = [Catalog._identifier(item['topic_id']) for item in entries]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError('The plan must contain distinct Topic IDs')
    if args.topic and set(args.topic) - set(ids):
        raise ValueError('Unknown selected Topic')
    selected = [item for item in entries if not args.topic or item['topic_id'] in args.topic]
    args.run_root.mkdir(parents=True, exist_ok=True)
    summary = args.run_root / ('batch-' + ('selected' if args.topic else 'all') + '.json')
    if summary.exists():
        raise ValueError('Batch summary already exists; use a new run root')
    # A run directory is immutable. Retry failures under a new run root.
    for item in selected:
        source = Path(item['source'])
        if not source.is_absolute() or hashlib.sha256(source.read_bytes()).hexdigest() != item['source_sha256']:
            raise ValueError('Source differs from the import plan: ' + item['topic_id'])
        if (args.run_root / item['topic_id']).exists():
            raise ValueError('Run directory already exists: ' + item['topic_id'])
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(prepare_topic, Path(item['source']), item['topic_id'],
                               args.run_root / item['topic_id']): item for item in selected}
        for future in as_completed(futures):
            item = futures[future]
            try:
                manifest = future.result()
                if manifest.get('status') == 'approved' and manifest.get('source_sha256') != item['source_sha256']:
                    raise ValueError('Prepared source differs from the import plan')
                result = {'topic_id': item['topic_id'], 'status': manifest['status'],
                          'package': str(args.run_root / item['topic_id']),
                          'stage': manifest.get('stage'), 'error': manifest.get('error')}
            except Exception as exc:
                result = {'topic_id': item['topic_id'], 'status': 'failed', 'error_type': type(exc).__name__}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    with summary.open('x', encoding='utf-8') as stream:
        json.dump({'results': results}, stream, ensure_ascii=False, indent=2)
    return 0 if all(item['status'] == 'approved' for item in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
