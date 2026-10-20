"""Guarded, reversible release step inside the existing server maintenance wrapper."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request


def main():
    stage = Path(sys.argv[1]).resolve()
    if os.geteuid() != 0 or stage.parent != Path('/tmp') or not stage.name.startswith('graspmemoedu-academy-') or stage.is_symlink():
        raise ValueError('Use the configured maintenance stage')
    app, data = Path('/opt/graspmemoedu/app'), Path('/var/lib/graspmemoedu')
    release = json.loads((stage / 'release.json').read_bytes())
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    for name, spec in release['files'].items():
        if not (app / name).resolve().is_relative_to(app) or not (stage / 'source' / name).resolve().is_relative_to(stage / 'source'):
            raise ValueError('Invalid release path')
        if digest(app / name) != spec['before'] or digest(stage / 'source' / name) != spec['after']:
            raise ValueError('Concurrent source change or release corruption: ' + name)
    subprocess.run(['/usr/local/sbin/graspmemoedu-backup'], check=True)
    pointer = data / 'active-content.json'
    previous_pointer = pointer.read_bytes() if pointer.exists() else None
    timer = subprocess.run(['systemctl', 'is-active', '--quiet', 'graspmemoedu-healthcheck.timer']).returncode == 0
    if timer: subprocess.run(['systemctl', 'stop', 'graspmemoedu-healthcheck.timer'], check=True)
    subprocess.run(['systemctl', 'stop', 'graspmemoedu'], check=True)
    def snapshot():
        with sqlite3.connect(f'file:{data / "learning.sqlite3"}?mode=ro', uri=True) as con:
            names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {name: hashlib.sha256(repr(con.execute('SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid').fetchall()).encode()).hexdigest() for name in names}
    changed = []
    def healthy():
        for _ in range(60):
            try:
                with urllib.request.urlopen('http://127.0.0.1:18768/api/health', timeout=3) as response:
                    if response.status == 200: return
            except (OSError, TimeoutError): pass
            time.sleep(1)
        raise RuntimeError('Production health check failed')
    try:
        before_state = snapshot()
        for name, spec in release['files'].items():
            if digest(app / name) != spec['before']:
                raise ValueError('Source changed during release preparation: ' + name)
        for name in release['files']:
            target, rollback = app / name, stage / 'rollback' / name
            if target.exists():
                rollback.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(target, rollback)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stage / 'source' / name, target); changed.append(name)
        if release.get('question_bank_id'):
            operation = ['-m', 'graspmemoedu.bank_placement', '--container-id', release['question_bank_id']]
        else:
            subprocess.run(['chown', '-R', 'graspmemoedu:graspmemoedu', str(stage / 'package')], check=True)
            operation = ['-m', 'graspmemoedu.academy_import', '--package', str(stage / 'package')]
        result = subprocess.run(['runuser', '-u', 'graspmemoedu', '--', '/opt/graspmemoedu/venv/bin/python',
            *operation, '--data-dir', str(data)],
            cwd=app, text=True, capture_output=True, check=True)
        installed = json.loads(result.stdout.strip().splitlines()[-1])
        if snapshot() != before_state: raise RuntimeError('Content installation unexpectedly changed existing database rows')
        subprocess.run(['systemctl', 'start', 'graspmemoedu'], check=True); healthy()
        receipt = {'target': 'production', 'health': 'passed', 'source_commit': release['commit'],
                   'existing_database_rows_unchanged_at_install': True, **installed}
        (data / 'content-activation-history' / (installed.get('generation', 'academy') + '.production.json')).write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt), flush=True)
    except BaseException:
        subprocess.run(['systemctl', 'stop', 'graspmemoedu'], check=True)
        for name in reversed(changed):
            backup = stage / 'rollback' / name
            if backup.exists(): shutil.copy2(backup, app / name)
            else: (app / name).unlink()
        if previous_pointer is None: pointer.unlink(missing_ok=True)
        else:
            pending = pointer.with_suffix('.rollback'); pending.write_bytes(previous_pointer); os.replace(pending, pointer)
        subprocess.run(['systemctl', 'start', 'graspmemoedu'], check=True); healthy()
        raise
    finally:
        if timer: subprocess.run(['systemctl', 'start', 'graspmemoedu-healthcheck.timer'], check=True)


if __name__ == '__main__': main()
