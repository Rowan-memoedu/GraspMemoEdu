"""Sync edited private invitation-list labels through the existing server CLI."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.admin_names import roster_names
from graspmemoedu.service import DomainError

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--list', type=Path, default=Path('D:/AppData/GraspMemoEdu/admin/邀请码清单.md'))
parser.add_argument('--host', default='ubuntu@124.221.144.155')
parser.add_argument('--dry-run', action='store_true')
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_.@-]+', args.host): parser.error('Invalid SSH host')
try:
    payload = roster_names(args.list.read_text(encoding='utf-8-sig'))
    command = 'cd /opt/graspmemoedu/app && sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu account-sync-admin-names'
    if args.dry_run: command += ' --dry-run'
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', args.host, command],
                            input=json.dumps(payload, ensure_ascii=False), encoding='utf-8', check=False)
    sys.exit(result.returncode)
except DomainError as error:
    print(error.message, file=sys.stderr); sys.exit(1)
