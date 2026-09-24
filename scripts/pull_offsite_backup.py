"""Pull the latest verified cloud archive to the owner's Windows PC when online."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import zipfile


HOST = 'ubuntu@124.221.144.155'
REMOTE = '/var/backups/graspmemoedu'
NAME = re.compile(r'site-\d{8}T\d{6}Z\.zip\Z')
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'PreferredAuthentications=publickey',
       '-o', 'PasswordAuthentication=no', '-o', 'StrictHostKeyChecking=yes',
       '-o', 'ConnectTimeout=10', HOST]


def remote(command):
    return subprocess.run([*SSH, command], capture_output=True, check=True, timeout=50).stdout.decode('utf-8')


def digest(path):
    hashed = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hashed.update(chunk)
    return hashed.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    names = remote(f"sudo -n find {REMOTE} -maxdepth 1 -type f -name 'site-????????T??????Z.zip' -printf '%f\\n'").splitlines()
    names = sorted(name for name in names if NAME.fullmatch(name))
    if not names:
        raise RuntimeError('No verified server backup found')
    name = names[-1]
    expected = remote(f'sudo -n sha256sum {REMOTE}/{name}').split()[0].lower()
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise RuntimeError('Invalid server backup checksum')
    args.destination.mkdir(parents=True, exist_ok=True)
    target = args.destination / name
    if target.exists():
        if digest(target) != expected:
            raise RuntimeError('Existing offsite copy differs from server backup')
        print(f'PASS existing offsite copy {name} SHA-256 verified')
        return
    temporary = args.destination / (name + '.part')
    if temporary.exists():
        temporary.unlink()  # Only this script's incomplete transfer file.
    with temporary.open('wb') as output:
        process = subprocess.run([*SSH, f'sudo -n cat {REMOTE}/{name}'], stdout=output,
                                 stderr=subprocess.PIPE, timeout=1200)
    if process.returncode != 0 or digest(temporary) != expected:
        raise RuntimeError('Offsite transfer or checksum verification failed')
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Offsite archive CRC verification failed')
    temporary.replace(target)
    print(f'PASS offsite backup {name} SHA-256 and ZIP CRC verified')


if __name__ == '__main__':
    try:
        main()
    except (OSError, subprocess.SubprocessError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f'FAIL offsite backup: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
