"""Local CLI for evidence-preserving, question-level import recovery."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from math_learning.author_import import resume_topic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--source')
    parser.add_argument('--resolutions')
    parser.add_argument('--mode', choices=('mechanical', 'semantic'), default='mechanical')
    args = parser.parse_args()
    try:
        result = resume_topic(args.package, args.run_dir, source=args.source, resolutions=args.resolutions, mode=args.mode)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'approved' else 1


if __name__ == '__main__':
    raise SystemExit(main())
