"""Version first-party asset URLs as one module graph; --patch emits an apply_patch patch."""
from pathlib import Path
import argparse
import difflib
import hashlib
import re

WEB = Path(__file__).resolve().parents[1] / 'web'
URL = re.compile(r'''(\./(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.(?:m?js|css|svg))(?:\?v=[a-f0-9]+)?(?=["'])''')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patch', action='store_true')
    args = parser.parse_args()
    originals = {p: p.read_text(encoding='utf-8') for p in sorted(WEB.iterdir())
                 if p.suffix in {'.html', '.js', '.css', '.svg'}}
    canonical = {p: URL.sub(r'\1', text) for p, text in originals.items()}
    digest = hashlib.sha256()
    for p, text in canonical.items():
        digest.update(p.name.encode() + b'\0' + text.encode() + b'\0')
    # Vendor chunks carry content hashes in their names; version the stable
    # entrypoint URLs when the pinned dependency build changes as well.
    for p in sorted((WEB / 'vendor').rglob('*')):
        if p.is_file():
            digest.update(p.relative_to(WEB).as_posix().encode() + b'\0' + p.read_bytes() + b'\0')
    version = digest.hexdigest()[:16]
    changed = {p: URL.sub(lambda m: m[1] + '?v=' + version, text)
               for p, text in canonical.items()}
    changed = {p: text for p, text in changed.items() if text != originals[p]}
    if args.patch:
        print('*** Begin Patch')
        for p, text in changed.items():
            print('*** Update File: ' + p.as_posix())
            diff = list(difflib.unified_diff(originals[p].splitlines(), text.splitlines(), lineterm=''))
            for line in diff[2:]:
                print('@@' if line.startswith('@@') else line)
        print('*** End Patch')
    elif changed:
        raise SystemExit('Static asset versions are stale. Apply the patch from scripts/version_web_assets.py --patch before export.')
    else:
        print('PASS static asset version ' + version)


if __name__ == '__main__':
    main()
