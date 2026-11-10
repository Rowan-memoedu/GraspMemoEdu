"""Apply a hash-checked backup-capacity repair and prove native restore before release."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    stage=Path(sys.argv[1]).resolve()
    if os.geteuid()!=0 or stage.parent!=Path('/tmp') or not stage.name.startswith('graspmemoedu-academy-backup-'):
        raise ValueError('Use the existing maintenance stage')
    target=Path('/opt/graspmemoedu/app/graspmemoedu/site_backup.py')
    candidate=stage/'candidate.py';guard=json.loads((stage/'guard.json').read_bytes())
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    if digest(target)!=guard['before'] or digest(candidate)!=guard['after']:
        raise ValueError('Backup source changed concurrently')
    compile(candidate.read_text(),str(target),'exec')
    shutil.copy2(target,stage/'original.py')
    try:
        shutil.copyfile(candidate,target)
        subprocess.run(['/usr/local/sbin/graspmemoedu-backup'],check=True)
    except BaseException:
        shutil.copy2(stage/'original.py',target)
        raise
    print(json.dumps({'status':'backup_and_native_restore_verified','source_sha256':digest(target),
                      'service_restarted':False,'learning_records_modified':False}),flush=True)


if __name__=='__main__':main()
