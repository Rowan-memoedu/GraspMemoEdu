"""Read public exercise attachments as data, never execute or extract them."""
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import PurePosixPath
import re
import shutil
import subprocess
from threading import Lock
from urllib.parse import urlsplit
from zipfile import ZipFile

LOCK = Lock()
HOSTS = ('cogniterra.org', 'stepik.org', 'hyperskill.org')
MAX_DOWNLOAD = 32 * 1024 * 1024
NUM = re.compile(r'[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?')


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            url = dict(attrs).get('href', '')
            if urlsplit(url).path.lower().endswith(('.zip', '.txt', '.csv', '.json', '.xml')):
                self.urls.append(url)


def allowed(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and not parts.username and not parts.password and
            any(parts.hostname == h or (parts.hostname or '').endswith('.' + h) for h in HOSTS))


def numeric_summary(text, question):
    rows = []
    for line in text.splitlines():
        tokens = line.split()
        if tokens and all(NUM.fullmatch(token) for token in tokens):
            rows.append([Decimal(token.replace(',', '')) for token in tokens])
    if not rows:
        return None
    thresholds = sorted({Decimal(v.replace(',', '')) for v in NUM.findall(question)})
    if len(thresholds) > 40:
        thresholds = []
    def summarize(values, labels):
        stats = {'count': len(values), 'sum': str(sum(values)), 'min': str(min(values)), 'max': str(max(values)),
                 'threshold_counts': {str(n): {'equal': sum(v == n for v in values),
                    'greater': sum(v > n for v in values), 'greater_or_equal': sum(v >= n for v in values)} for n in thresholds}}
        if all(v == v.to_integral_value() for v in values):
            stats['even_count'] = sum(v % 2 == 0 for v in values)
            prefix = values[:values.index(Decimal(0))] if 0 in values else values
            stats['before_first_zero'] = {'count': len(prefix), 'sum': str(sum(prefix)),
                                         'even_count': sum(v % 2 == 0 for v in prefix)}
        if len(values) > 1:
            deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
            high, low = max(deltas), min(deltas)
            stats['adjacent_differences'] = {'max': str(high), 'min': str(low),
                'max_rows': [[str(v) for v in labels[i + 1]] for i, delta in enumerate(deltas) if delta == high],
                'min_rows': [[str(v) for v in labels[i + 1]] for i, delta in enumerate(deltas) if delta == low]}
        return stats
    sequence = [v for row in rows for v in row]
    # A long whitespace-separated line is a sequence, not thousands of columns.
    columns = ([summarize([row[i] for row in rows], rows) for i in range(len(rows[0]))]
               if len({len(row) for row in rows}) == 1 and len(rows[0]) <= 32 else [])
    return {'numeric_rows': len(rows), 'sequence_in_file_order': summarize(sequence, [[v] for v in sequence]),
            'columns_in_source_order': columns}


def zip_summary(data):
    files, directories = set(), set()
    with ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 100000 or sum(i.file_size for i in entries) > 64 * 1024 * 1024:
            raise ValueError('Attachment archive exceeds metadata bounds')
        for entry in entries:
            name = entry.filename.replace('\\', '/')
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or ':' in name:
                raise ValueError('Unsafe archive path')
            if entry.is_dir():
                directories.add(str(path))
            else:
                files.add(str(path))
            for parent in path.parents:
                if str(parent) != '.': directories.add(str(parent))
    children, counts = defaultdict(set), Counter()
    for name in files | directories:
        children[str(PurePosixPath(name).parent)].add(name)
    for name in files:
        counts[str(PurePosixPath(name).parent)] += 1
    all_names = files | directories
    depths = Counter(len(PurePosixPath(name).parts) for name in all_names)
    deepest = max(depths, default=0)
    empty = sorted(name for name in directories if not children[name])
    largest = max((counts[name] for name in directories), default=0)
    return {'file_count': len(files), 'directory_count': len(directories),
            'nodes_by_depth': dict(sorted(depths.items())), 'maximum_depth': deepest,
            'deepest_entries': sorted(name for name in all_names if len(PurePosixPath(name).parts) == deepest),
            'empty_directory_count': len(empty), 'empty_directories': empty,
            'maximum_direct_files': largest,
            'directories_with_most_files': sorted(name for name in directories if counts[name] == largest),
            'entries': [{'path': name, 'type': 'file' if name in files else 'directory',
                         'depth': len(PurePosixPath(name).parts)} for name in sorted(files | directories)],
            'directories': [{'path': name, 'direct_files': counts[name], 'direct_children': len(children[name])}
                            for name in sorted(directories)]}


def attachments(question, output):
    parser = Links()
    parser.feed(question['question'])
    result = []
    for url in dict.fromkeys(parser.urls):
        if not allowed(url):
            raise ValueError('Attachment is outside public Hyperskill source hosts')
        ident = hashlib.sha256(url.encode()).hexdigest()
        folder = output / 'attachments'
        file = folder / ident
        with LOCK:
            folder.mkdir(parents=True, exist_ok=True)
            if file.exists():
                data = file.read_bytes()
            else:
                # Use the native public HTTP client; no cookies or browser session.
                fetched = subprocess.run([shutil.which('curl.exe') or 'curl', '--fail', '--silent', '--show-error',
                    '--proto', '=https', '--max-time', '60', '--max-filesize', str(MAX_DOWNLOAD),
                    '--write-out', '%{stderr}%{http_code}', url], capture_output=True, timeout=75)
                if fetched.returncode or fetched.stderr.strip() != b'200':
                    raise ValueError('Public attachment download failed; no partial input accepted')
                data = fetched.stdout
                if len(data) > MAX_DOWNLOAD:
                    raise ValueError('Attachment exceeds input size bound')
                temporary = file.with_suffix('.tmp')
                temporary.write_bytes(data)
                temporary.replace(file)
        item = {'url': url, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        if urlsplit(url).path.lower().endswith('.zip'):
            full = zip_summary(data)
            with LOCK:
                manifest = file.with_suffix('.manifest.json')
                temporary = manifest.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(full, ensure_ascii=False), encoding='utf-8')
                temporary.replace(manifest)
            item['archive'] = dict(full)
            # Statistics cover every entry. Large raw lists stay in the audited
            # manifest; omission is explicit rather than silently cutting a list.
            for field, limit in (('entries', 2000), ('directories', 1000),
                                 ('empty_directories', 200), ('deepest_entries', 200),
                                 ('directories_with_most_files', 200)):
                if len(full[field]) > limit:
                    item['archive'].pop(field)
                    item['archive'][field + '_omitted_count'] = len(full[field])
        else:
            item['text'] = data.decode('utf-8-sig')
            item['numeric_summary'] = (numeric_summary(item['text'], question['question'])
                                       if urlsplit(url).path.lower().endswith('.txt') else None)
        if len(json.dumps(item)) > 500000:
            raise ValueError('Attachment representation exceeds complete context bound')
        result.append(item)
    return result
