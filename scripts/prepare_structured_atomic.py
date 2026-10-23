"""Prepare a hashed structured atomic package for the existing content importer."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu.structured_atomic import FORMAT, fingerprint, validate_projection
from graspmemoedu.site_backup import _validate_review
from graspmemoedu.topic_install import encoded
from graspmemoedu.training_audio import audio_bytes


class Images(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.urls = []; self.feed(html)
    def handle_starttag(self, tag, attrs):
        if tag == 'img': self.urls.append(dict(attrs)['src'])


def copy_audio_asset(source_path, destination_root, card):
    """Use the same path, hash, MIME and size contract as the serving endpoint."""
    data = audio_bytes(source_path, card)
    destination = destination_root / card['audio']['asset']
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError('Conflicting audio asset')
    if not destination.exists(): destination.write_bytes(data)
    return destination


def prepare(args):
    raw = args.input.read_bytes(); document = json.loads(raw)
    if document.get('schema') != 2:
        raise ValueError('Structured Topic schema 2 is required')
    pins_path = getattr(args, 'preserve_atomic_progress', None)
    pins = json.loads(Path(pins_path).read_bytes()) if pins_path else None
    if pins is not None:
        if (getattr(args, 'reset_reading_progress', False) or getattr(args, 'replace_atomic_and_reset_progress', False)
                or not isinstance(pins, dict) or not pins
                or any(not isinstance(key, str) or not isinstance(value, str) or not value for key, value in pins.items())
                or not set(pins) <= {args.topic_prefix + str(topic['id']) for topic in document['topics']}):
            raise ValueError('Content revision requires matching previous-version pins and no reset option')
    urls = sorted({url for t in document['topics'] for s in t['sections'] for url in Images(s['introduction']['html']).urls})
    args.output.mkdir(parents=True, exist_ok=False)
    def download(url):
        import httpx
        if not url.startswith('https://'):
            raise ValueError('Only explicit HTTPS source images can be archived')
        with httpx.Client(timeout=90, follow_redirects=True, trust_env=False) as client:
            response = client.get(url); response.raise_for_status()
        data = response.content
        if data.startswith(b'\x89PNG'): mime = 'image/png'
        elif data.startswith(b'\xff\xd8\xff'): mime = 'image/jpeg'
        elif data.startswith((b'GIF87a', b'GIF89a')): mime = 'image/gif'
        elif data.startswith(b'RIFF') and data[8:12] == b'WEBP': mime = 'image/webp'
        elif b'<svg' in data[:1000]: mime = 'image/svg+xml'
        else: raise ValueError('Unsupported original source image')
        return url, {'data_url': 'data:' + mime + ';base64,' + base64.b64encode(data).decode(),
                     'sha256': hashlib.sha256(data).hexdigest()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        images = dict(pool.map(download, urls))
    declarations, report = [], []
    sources = args.output / 'atomic-sources'; sources.mkdir()
    topics = args.output / 'topics'; topics.mkdir()
    for original in document['topics']:
        ident = args.topic_prefix + str(original['id'])
        own_urls = {url for section in original['sections'] for url in Images(section['introduction']['html']).urls}
        source = {**deepcopy(original), 'source_format': FORMAT, 'id': ident, 'source_topic_id': original['id'],
                  'subject_id': args.subject, 'structured_source_sha256': hashlib.sha256(raw).hexdigest(),
                  'embedded_images': {url: images[url] for url in own_urls}}
        if args.title_suffix:
            source['title'] += args.title_suffix
        source_path = sources / (ident + '.json'); source_path.write_bytes(encoded(source))
        topic_path = topics / (ident + '.json')
        completed = subprocess.run([args.node, str(ROOT / 'scripts/import_atomic_topic.mjs'), str(source_path), str(topic_path)],
                                  cwd=ROOT, env={**os.environ, 'GRASPMEMOEDU_PYTHON': sys.executable},
                                  capture_output=True, text=True, encoding='utf-8', timeout=300)
        if completed.returncode:
            raise ValueError(completed.stderr[-4000:])
        topic = json.loads(topic_path.read_bytes()); validate_projection(topic, source)
        for card in topic['flashcards']:
            if card.get('audio_source'):
                raise ValueError('Unbound audio source cannot enter an installable package')
            audio = card.get('audio')
            if audio:
                copy_audio_asset(args.input, topics, card)
        answers = {'source_sha256': topic['source_sha256'], 'answers': {card['id']: {
            key: card[key] for key in ('reference_answer', 'answer_type', 'constraints')} for card in topic['flashcards']}}
        answer_bytes = encoded(answers)
        review = {'schema_version': 1, 'mode': 'mechanical', 'status': 'mechanically_validated', 'method': 'mechanical-import',
                  'semantic_review_performed': False, 'mathematical_correctness_checked': False,
                  'source_format': FORMAT, 'source_sha256': topic['source_sha256'],
                  'sidecar_sha256': hashlib.sha256(answer_bytes).hexdigest(), 'topic_fingerprint': fingerprint(topic), 'assets': []}
        review_bytes = encoded(review)
        _validate_review(topic, source_path.read_bytes(), answer_bytes, review_bytes)
        (sources / (ident + '.answers.json')).write_bytes(answer_bytes)
        (sources / (ident + '.review.json')).write_bytes(review_bytes)
        (topics / (ident + '.sources.json')).write_bytes(encoded({
            'source_path': '../atomic-sources/' + ident + '.json',
            'answers_path': '../atomic-sources/' + ident + '.answers.json',
            'review_path': '../atomic-sources/' + ident + '.review.json',
            'review_sha256': hashlib.sha256(review_bytes).hexdigest()}))
        declarations.append({'id': ident, 'path': 'topics/' + ident + '.json', 'prerequisites': []})
        report.append({'id': ident, 'chunks': len(topic['memory_chunks']), 'points': sum(len(c['points']) for c in topic['memory_chunks']),
                       'cards': len(topic['flashcards']), 'review_only': sum(c.get('learning_phases') == ['review'] for c in topic['flashcards'])})
        print(json.dumps(report[-1]), flush=True)
    unit = {'id': args.unit_id, 'title': args.unit_title, 'topic_ids': [row['id'] for row in declarations]}
    if args.unit_parent: unit['parent_id'] = args.unit_parent
    (args.output / 'catalog.json').write_bytes(encoded({'courses': [{'id': args.course_id, 'title': args.course_title,
        'description': '', 'topics': declarations, 'units': [unit]}]}))
    (args.output / 'report.json').write_bytes(encoded({'topics': report, 'structured_source_sha256': hashlib.sha256(raw).hexdigest()}))
    files = {p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(args.output.rglob('*')) if p.is_file()}
    manifest = {'schema': 1, 'adapter': 'structured-atomic',
        'subject_id': args.subject, 'reset_reading_progress': getattr(args, 'reset_reading_progress', False),
        'replace_atomic_and_reset_progress': getattr(args, 'replace_atomic_and_reset_progress', False), 'files': files}
    if getattr(args, 'test_material', False):
        manifest['test_materials'] = {'courses': [args.course_id]}
    if pins is not None:
        manifest.update(preserve_atomic_progress=True, atomic_previous_versions=pins)
    (args.output / 'manifest.json').write_bytes(encoded(manifest))
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--subject', required=True); p.add_argument('--course-id', required=True); p.add_argument('--course-title', required=True)
    p.add_argument('--unit-id', required=True); p.add_argument('--unit-title', required=True); p.add_argument('--unit-parent')
    p.add_argument('--topic-prefix', default='atomic-topic-'); p.add_argument('--title-suffix', default='')
    p.add_argument('--node', default='node')
    p.add_argument('--reset-reading-progress', action='store_true', help='Explicitly allow same-ID reading-only upgrades')
    p.add_argument('--replace-atomic-and-reset-progress', action='store_true', help='Explicitly replace atomic Topics and reset their progress')
    p.add_argument('--preserve-atomic-progress', type=Path, metavar='PREVIOUS_VERSIONS_JSON',
                   help='Revise same-identity atomic content with pinned old versions, preserving learning records')
    p.add_argument('--test-material', action='store_true', help='Mark this course as test material before content activation')
    prepare(p.parse_args())


if __name__ == '__main__': main()
