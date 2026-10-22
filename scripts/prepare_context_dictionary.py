"""Prepare fixed OEWN data for offline lookup; never touch learning records."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu.context_dictionary import LEXICON, MANIFEST, SOURCE, WN_VERSION, lookup_word

LICENSE_URLS = {
    'OEWN-LICENSE.md': 'https://raw.githubusercontent.com/globalwordnet/english-wordnet/2025-edition/LICENSE.md',
    'WNDB_License.txt': 'https://raw.githubusercontent.com/globalwordnet/english-wordnet/2025-edition/WNDB_License.txt',
}


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def progress_handler(base):
    """Use Wn's supported progress interface without one line per record."""
    class SparseProgress(base):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.last_report = time.monotonic()

        def update(self, n=1, force=False):
            super().update(n, force=force)
            now = time.monotonic()
            if now - self.last_report >= 5:
                self.last_report = now
                print(f"{self.kwargs['status'] or self.kwargs['message']} "
                      f"{self.kwargs['count']}/{self.kwargs['total']}", flush=True)

        def flash(self, message):
            print(message, flush=True)

    return SparseProgress


def prepare(args):
    if not args.output.is_absolute():
        raise ValueError('--output must be an absolute path')
    output = args.output.resolve()
    if output == Path(output.anchor) or output == ROOT or ROOT in output.parents:
        raise ValueError('Use a dedicated data/artifact directory outside the source repository')
    if not args.download and (args.source is None or args.licenses is None):
        raise ValueError('Offline preparation requires both --source and --licenses')
    wn = importlib.import_module('wn')
    if wn.__version__ != WN_VERSION:
        raise ValueError(f'Preparation requires wn=={WN_VERSION}')
    started = time.monotonic()
    marker = output / 'preparation.json'
    if output.exists() and any(output.iterdir()):
        if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')).get('lexicon') != LEXICON:
            raise ValueError('Refusing to reuse a non-dictionary output directory')
    output.mkdir(parents=True, exist_ok=True)
    write_json(marker, {'schema_version': 1, 'lexicon': LEXICON, 'wn_version': WN_VERSION})
    original = wn.config.database_path.parent
    db = importlib.import_module('wn._db')
    progress = progress_handler(importlib.import_module('wn.util').ProgressHandler)
    wn.config.data_directory = output
    try:
        print('Preparing license notices', flush=True)
        licenses = output / 'licenses'
        licenses.mkdir(exist_ok=True)
        for name, url in LICENSE_URLS.items():
            target = licenses / name
            if args.licenses:
                source_license = args.licenses / name
                if not source_license.is_file():
                    raise ValueError(f'Missing license notice: {name}')
                if target.resolve() != source_license.resolve():
                    shutil.copy2(source_license, target)
            elif not target.exists():
                with urlopen(url, timeout=30) as response:
                    data = response.read(131073)
                if not data or len(data) > 131072:
                    raise ValueError('Unexpected license notice size')
                target.write_bytes(data)
        if args.source:
            if not args.source.is_file():
                raise ValueError('The provided source file does not exist')
            source = output / 'source' / args.source.name
            source.parent.mkdir(exist_ok=True)
            if source.resolve() != args.source.resolve():
                if source.exists() and digest(source) != digest(args.source):
                    raise ValueError('A different source already exists in this preparation directory')
                if not source.exists():
                    shutil.copy2(args.source, source)
        else:
            print(f'Downloading or reusing {LEXICON}', flush=True)
            source = wn.download(LEXICON, add=False, progress_handler=progress)
        source_hash = digest(source)
        if args.sha256 and source_hash != args.sha256.lower():
            raise ValueError('Source SHA-256 does not match --sha256')
        previous_manifest = output / MANIFEST
        if previous_manifest.exists():
            previous = json.loads(previous_manifest.read_text(encoding='utf-8'))
            if previous.get('source_sha256') != source_hash:
                raise ValueError('Prepared source changed; use a new output directory')
        installed = {f'{item.id}:{item.version}' for item in wn.lexicons()}
        if installed - {LEXICON}:
            raise ValueError('The output contains an unrelated lexicon')
        if LEXICON not in installed:
            print(f'Indexing {LEXICON}', flush=True)
            # Wn decompresses LMF with tempfile. Keep this potentially large
            # intermediate in the explicitly selected data directory too.
            previous_tempdir = tempfile.tempdir
            with tempfile.TemporaryDirectory(prefix='indexing-', dir=output) as tempdir:
                try:
                    tempfile.tempdir = tempdir
                    wn.add(source, progress_handler=progress)
                finally:
                    tempfile.tempdir = previous_tempdir
        dictionary = wn.Wordnet(LEXICON, expand='')
        counts = {'words': len(dictionary.words()), 'senses': len(dictionary.senses()),
                  'synsets': len(dictionary.synsets())}
    finally:
        db.clear_connections()
        wn.config.data_directory = original
    database = output / 'wn.db'
    manifest = {
        'schema_version': 1, 'lexicon': LEXICON, 'wn_version': WN_VERSION,
        'prepared_at': datetime.now(timezone.utc).isoformat(), 'source': dict(SOURCE),
        'source_url': 'https://en-word.net/static/english-wordnet-2025.xml.gz',
        'source_file': source.relative_to(output).as_posix(),
        'source_sha256': source_hash, 'source_bytes': source.stat().st_size,
        'database': {'file': database.name, 'bytes': database.stat().st_size,
                     'sha256': digest(database)}, 'counts': counts,
        'licenses': [{'file': f'licenses/{name}', 'url': url,
                      'sha256': digest(licenses / name)} for name, url in LICENSE_URLS.items()],
    }
    write_json(output / MANIFEST, manifest)
    print('Querying the independently prepared dictionary', flush=True)
    results = [lookup_word(word, data_dir=output, limit=20) for word in args.words]
    if any(result['status'] != 'available' for result in results):
        raise ValueError('Canary lookup failed: ' + ', '.join(
            f"{r['surface']}={r['status']}:{r.get('reason', '')}" for r in results if r['status'] != 'available'))
    report = {'lexicon': LEXICON, 'wn_version': WN_VERSION, 'counts': counts,
              'database_bytes': database.stat().st_size,
              'directory_bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
              'elapsed_seconds': round(time.monotonic() - started, 3), 'queries': results,
              'database_unchanged_by_queries': digest(database) == manifest['database']['sha256']}
    if not report['database_unchanged_by_queries']:
        raise ValueError('Lookup unexpectedly changed the prepared database')
    write_json(output / 'verification.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'queries'}, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--download', action='store_true', help='Allow downloading the fixed lexicon and license notices')
    parser.add_argument('--source', type=Path, help='Existing downloaded LMF or compressed LMF')
    parser.add_argument('--licenses', type=Path, help='Directory containing OEWN-LICENSE.md and WNDB_License.txt')
    parser.add_argument('--sha256', help='Expected source SHA-256, useful for offline reproduction')
    parser.add_argument('--words', nargs='+', default=['design', 'sustain', 'reuse', 'designed'])
    args = parser.parse_args()
    try:
        prepare(args)
    except (ValueError, ImportError) as exc:
        parser.exit(1, f'Preparation failed: {exc}\n')


if __name__ == '__main__':
    main()
