"""Root-only existing-host release step; invoked through managed run-stage."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tarfile
import time
import urllib.request


def main():
    stage = Path(sys.argv[1]).resolve()
    if os.geteuid() != 0 or stage.parent != Path('/tmp') or not stage.name.startswith('graspmemoedu-ma-') or stage.is_symlink():
        raise ValueError('Use the configured root release wrapper and exact staging path')
    data = Path('/var/lib/graspmemoedu')
    pointer = data / 'active-content.json'
    before = pointer.read_bytes() if pointer.exists() else None
    with tarfile.open(stage / 'package.tar.gz', 'r:gz') as archive:
        for member in archive.getmembers():
            target = (stage / member.name).resolve()
            if not target.is_relative_to(stage / 'package') or not (member.isfile() or member.isdir()):
                raise ValueError('Invalid prepared package member')
        archive.extractall(stage, filter='data')
    subprocess.run(['chown', '-R', 'graspmemoedu:graspmemoedu', str(stage / 'package')], check=True)
    manifest = json.loads((stage / 'package/manifest.json').read_text(encoding='utf-8'))
    module = 'graspmemoedu.mathacademy_graphics' if manifest.get('kind') == 'mathacademy-inline-graphics' else 'graspmemoedu.mathacademy_import'
    subprocess.run(['/usr/local/sbin/graspmemoedu-backup'], check=True)
    timer = subprocess.run(['systemctl', 'is-active', '--quiet', 'graspmemoedu-healthcheck.timer']).returncode == 0
    if timer:
        subprocess.run(['systemctl', 'stop', 'graspmemoedu-healthcheck.timer'], check=True)
    subprocess.run(['systemctl', 'stop', 'graspmemoedu'], check=True)
    def database_state():
        with sqlite3.connect('file:' + str(data / 'learning.sqlite3') + '?mode=ro', uri=True) as con:
            con.execute('BEGIN')
            names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {name: hashlib.sha256(repr(con.execute('SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid').fetchall()).encode()).hexdigest()
                    for name in names}
    def healthy():
        for attempt in range(90):
            try:
                with urllib.request.urlopen('http://127.0.0.1:18768/api/health', timeout=3) as response:
                    if response.status == 200:
                        return
            except (OSError, TimeoutError):
                pass
            time.sleep(1)
        raise RuntimeError('New content failed the running service health check')
    try:
        before_database = database_state() if module == 'graspmemoedu.mathacademy_graphics' else None
        result = subprocess.run(['runuser', '-u', 'graspmemoedu', '--', '/opt/graspmemoedu/venv/bin/python',
            '-m', module, '--install-package', str(stage / 'package'), '--data-dir', str(data)],
            cwd='/opt/graspmemoedu/app', text=True, capture_output=True, check=True)
        receipt = json.loads(result.stdout.strip().splitlines()[-1])
        if before_database is not None:
            if database_state() != before_database:
                raise RuntimeError('Graphics repair changed learning database rows')
            receipt['learning_database_unchanged'] = True
        # A separate host monitor may have started the service before activation.
        # Reload the selected generation even when that process is already active.
        subprocess.run(['systemctl', 'restart', 'graspmemoedu'], check=True)
        healthy()
        receipt.update(target='production', health='passed', pointer_sha256=hashlib.sha256(pointer.read_bytes()).hexdigest())
        record = data / 'content-activation-history' / (receipt['generation'] + '.production.json')
        record.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps(receipt, ensure_ascii=False), flush=True)
    except Exception:
        subprocess.run(['systemctl', 'stop', 'graspmemoedu'], check=True)
        if before is None:
            pointer.unlink(missing_ok=True)
        else:
            pending = pointer.with_suffix('.rollback'); pending.write_bytes(before); os.replace(pending, pointer)
        subprocess.run(['systemctl', 'restart', 'graspmemoedu'], check=True)
        healthy()
        raise
    finally:
        if timer:
            subprocess.run(['systemctl', 'start', 'graspmemoedu-healthcheck.timer'], check=True)


if __name__ == '__main__':
    main()
