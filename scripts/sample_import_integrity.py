"""Explicit optional API sampling for mechanical damage across a whole import batch."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.author_import import sample_batch_integrity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', nargs='+', required=True, help='All package directories in this batch')
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-questions', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    result = sample_batch_integrity(args.package, args.output, max_questions=args.max_questions)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'no_damage_detected' else 1


if __name__ == '__main__':
    raise SystemExit(main())
