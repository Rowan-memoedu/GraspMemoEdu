"""Read back every public static asset and compare the exact exported bytes."""
import argparse
import concurrent.futures
import hashlib
import json
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urljoin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path)
    parser.add_argument('url')
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    files = {path.relative_to(args.export).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in args.export.rglob('*') if path.is_file()}
    pending, passed = dict(files), {}
    def check(item):
        name, expected = item
        url = urljoin(args.url, quote(name)) + '?v=' + args.version
        try:
            request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(request, timeout=25) as response:
                actual = hashlib.sha256(response.read()).hexdigest()
                if actual != expected: return name, {'error': 'hash mismatch'}
                return name, {'sha256': actual, 'status': response.status,
                              'content_type': response.headers.get('Content-Type')}
        except Exception as exc:
            return name, {'error': type(exc).__name__}
    errors = {}
    for attempt in range(20):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for name, result in pool.map(check, list(pending.items())):
                if 'error' not in result:
                    passed[name] = result
                    pending.pop(name, None)
                else: errors[name] = result
        print(f'Public asset readback {len(passed)}/{len(files)}, attempt {attempt + 1}', flush=True)
        if not pending: break
        time.sleep(15)
    result = {'status': 'passed' if not pending else 'failed', 'verified': len(passed), 'total': len(files),
              'assets': passed, 'pending': {name: errors.get(name) for name in pending}}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if pending: raise SystemExit('Public asset bytes have not converged to the release')


if __name__ == '__main__': main()
