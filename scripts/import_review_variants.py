"""Install a compiled, reviewed Practice batch through the private server CLI."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.review_variants import install_variants


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--input', required=True, type=Path)
    args = parser.parse_args()
    if not args.data_dir.is_absolute() or not args.input.is_absolute():
        parser.error('Use absolute data and input paths')
    print(json.dumps(install_variants(args.data_dir, args.input)))


if __name__ == '__main__':
    main()
