"""Private scoped reset; production execution requires the backend to be stopped."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.test_scenarios import apply_plan, validate_plan, prime_variant_sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = args.data_dir.resolve()
    if not args.data_dir.is_absolute() or not args.plan.is_absolute():
        parser.error('Use absolute paths')
    plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
    with closing(sqlite3.connect(root / 'learning.sqlite3')) as con:
        validate_plan(root, plan, con)
    if not args.apply:
        print(json.dumps({'status': 'validated', 'learners': len(plan['learners']), 'topics': list(plan['topics']), 'banks': list(plan['banks'])}))
        return
    if root == Path('/var/lib/graspmemoedu') and subprocess.run(['systemctl', 'is-active', '--quiet', 'graspmemoedu']).returncode == 0:
        parser.error('Stop graspmemoedu and its healthcheck timer before changing production test presets')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = root / 'admin' / 'test-scenario-backups' / (timestamp + '.sqlite3')
    receipt = apply_plan(root, plan, backup)
    receipt['variant_source_previews'] = prime_variant_sources(root, plan)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
