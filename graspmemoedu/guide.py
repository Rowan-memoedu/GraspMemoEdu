"""One-way local Markdown guide and a narrowly scoped public asset reader."""
from datetime import datetime, timezone
from collections import OrderedDict
import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path
import shutil
import subprocess
import threading
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import FileResponse

from .service import DomainError
from .subjects import Subjects
from .access import feature_eligible


GUIDE_FEATURES = ('course_hierarchy', 'training_self_rating', 'review_question_mode')
GUIDE_MARKERS = ('student', 'advanced', *GUIDE_FEATURES, 'review_timeout')


def guide_visibility(access=None):
    """The two qualifications are independent; grants alone never qualify."""
    access = access or {}
    if access.get('role') == 'guest' or (access.get('role') == 'account' and access.get('is_admin')):
        return dict.fromkeys(GUIDE_MARKERS, True)
    account = access.get('role') == 'account'
    granted = access.get('features') or ()
    effective = lambda name: account and name in granted and feature_eligible(access, name)
    features = {name: bool(effective(name)) for name in GUIDE_FEATURES}
    timeout = bool(account and access.get('is_advanced_learner'))
    return {'student': bool(access.get('is_student') and effective('request_help')),
            'advanced': bool(account and access.get('is_advanced_learner') and (timeout or any(features.values()))),
            'review_timeout': timeout, **features}


def project_guide(text, visibility):
    """Validate the whole source, then expose only permitted Markdown lines.

    Fenced code is literal, including apparent markers inside a hidden block.
    An invalid marker anywhere fails closed, even in an otherwise hidden block.
    """
    output, stack = [], []
    fence = None
    marker = re.compile(r'\s*<!--\s*(/?)guide:([a-z_]+)\s*-->\s*')
    for line in text.splitlines(keepends=True):
        visible = all(visibility[name] for name in stack)
        if fence:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(fence[1]) + r',}\s*', line):
                fence = None
            if visible:
                output.append(line)
            continue
        opening = re.match(r' {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if opening and (opening[1][0] != '`' or '`' not in opening[2]):
            fence = (opening[1][0], len(opening[1]))
            if visible:
                output.append(line)
            continue
        match = marker.fullmatch(line)
        if match:
            closing, name = match.groups()
            if name not in GUIDE_MARKERS:
                raise ValueError('Unknown guide visibility marker')
            if closing:
                if not stack or stack.pop() != name:
                    raise ValueError('Unbalanced guide visibility marker')
            else:
                stack.append(name)
            # Keep paragraph boundaries without emitting permission syntax.
            output.append('\n')
        elif re.search(r'<!--\s*/?guide\s*:', line):
            raise ValueError('Guide markers must occupy their own line')
        elif visible:
            output.append(line)
    if stack:
        raise ValueError('Unclosed guide visibility marker')
    return ''.join(output)


class GuideReader:
    MAX_CACHED_VIEWS = 8
    MAX_SOURCE_BYTES = 1024 * 1024
    MAX_ASSET_BYTES = 20 * 1024 * 1024
    ASSET_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.pdf', '.txt', '.csv'}

    def __init__(self, source):
        self.source = Path(source)
        self._lock = threading.RLock()
        self._source_digest = None
        self._rendered = None
        self._resources = set()
        self._views = OrderedDict()

    def _resource(self, relative):
        if not isinstance(relative, str) or not relative or any(c in relative for c in '\\:\x00'):
            raise ValueError('Invalid guide resource')
        candidate = (self.source.parent / relative).resolve()
        root = self.source.parent.resolve()
        if (candidate == self.source.resolve() or not candidate.is_relative_to(root)
                or candidate.suffix.lower() not in self.ASSET_SUFFIXES):
            raise ValueError('Invalid guide resource')
        if not candidate.is_file() or candidate.stat().st_size > self.MAX_ASSET_BYTES:
            raise ValueError('Invalid guide resource')
        return candidate

    def _compile(self, text):
        node = shutil.which('node')
        if not node:
            raise ValueError('Guide renderer unavailable')
        result = subprocess.run(
            [node, str(Path(__file__).resolve().parents[1] / 'scripts/render_guide.mjs')],
            input=json.dumps({'markdown': text}, ensure_ascii=False),
            capture_output=True, text=True, encoding='utf-8', timeout=25,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            env={**os.environ, 'NODE_NO_WARNINGS': '1'})
        if result.returncode:
            raise ValueError('Guide renderer failed')
        result = json.loads(result.stdout)
        if not isinstance(result.get('html'), str) or not isinstance(result.get('resources'), list):
            raise ValueError('Invalid guide result')
        return result

    def read(self, asset_base, access=None):
        with self._lock:
            try:
                if not self.source.is_file() or self.source.stat().st_size > self.MAX_SOURCE_BYTES:
                    raise OSError('Missing or oversized guide')
                raw = self.source.read_bytes()
                if len(raw) > self.MAX_SOURCE_BYTES:
                    raise ValueError('Guide too large')
                visibility = guide_visibility(access)
                source_digest = hashlib.sha256(raw).hexdigest()
                if source_digest != self._source_digest:
                    self._views.clear()
                    self._source_digest = source_digest
                key = (source_digest, tuple(visibility[name] for name in GUIDE_MARKERS))
                cached = self._views.get(key)
                if cached is None:
                    text = project_guide(raw.decode('utf-8-sig'), visibility)
                    digest = hashlib.sha256((json.dumps(visibility, sort_keys=True) + '\0' + text).encode()).hexdigest()
                    rendered = self._compile(text) if text.strip() else {'html': '', 'math_css': '', 'resources': []}
                    cached = (rendered, digest, bool(text.strip()))
                    self._views[key] = cached
                    while len(self._views) > self.MAX_CACHED_VIEWS:
                        self._views.popitem(last=False)
                self._views.move_to_end(key)
                self._rendered, digest, has_text = cached
                # Always select resources from this request's current authority,
                # including cache hits after a grant or qualification revocation.
                self._resources = set(self._rendered['resources'])
                asset_states = []
                for relative in sorted(self._resources):
                    try:
                        resource = self._resource(relative)
                        asset_states.append((relative, hashlib.sha256(resource.read_bytes()).hexdigest()))
                    except (OSError, ValueError):
                        asset_states.append((relative, 'missing'))
                version = hashlib.sha256((digest + json.dumps(asset_states)).encode()).hexdigest()
                html = self._rendered['html'].replace('https://guide-asset.invalid/', asset_base.rstrip('/') + '/')
                html = html.replace('GUIDEASSETVERSION', version)
                return {'status': 'ready' if has_text else 'empty', 'html': html,
                        'math_css': self._rendered.get('math_css', ''), 'version': version,
                        'modified_at': datetime.fromtimestamp(self.source.stat().st_mtime, timezone.utc).isoformat(),
                        'message': '' if has_text else '指南暂未填写。'}
            except (OSError, ValueError, subprocess.SubprocessError):
                self._resources = set()
                self._source_digest = None
                self._rendered = None
                self._views.clear()
                return {'status': 'unavailable', 'html': '', 'math_css': '', 'version': None,
                        'modified_at': None, 'message': '指南暂时无法读取，请稍后重试。'}

    def asset(self, relative, access=None):
        with self._lock:
            # Recheck the source so a removed reference stops being exposed.
            self.read('https://guide-asset.invalid', access)
            if relative not in self._resources:
                raise DomainError('guide_asset_not_found', '未找到指南资源。', 404)
            try:
                return self._resource(relative)
            except (OSError, ValueError):
                raise DomainError('guide_asset_not_found', '未找到指南资源。', 404)


def register_guide_routes(app, settings, access_store, subjects=None):
    reader = GuideReader(settings.guide_path)
    app.state.guide = reader
    subject_readers = {subject_id: GuideReader(path) for subject_id, path in subjects.guide_paths.items()} if subjects else {}

    def authorize_test_guide(request, subject_id, selected):
        protected = set()
        if subjects:
            protected = {sid for sid in subjects.test_materials.guides
                         if sid == subject_id or (selected and sid in subject_readers
                             and subject_readers[sid].source.resolve() == selected.source.resolve())}
            if selected and 'math' in subjects.test_materials.guides and selected.source.resolve() == reader.source.resolve():
                protected.add('math')
        if protected:
            access = access_store.resolve(request.headers.get('x-learning-identity'))
            subjects.test_materials.require_guide(next(iter(protected)), access)

    def subject_reader(subject_id):
        subjects.require(subject_id)
        configured = subjects.guide_paths.get(subject_id)
        if subject_id in subjects.explicit_guides:
            try:
                Subjects.guide_location(settings.data_dir, configured.relative_to(settings.data_dir).as_posix())
            except (ValueError, OSError):
                raise DomainError('guide_unavailable', '指南路径无效。', 404)
        return subject_readers.get(subject_id)

    @app.get('/api/guide')
    def guide(request: Request):
        access = access_store.resolve(request.headers.get('x-learning-identity'))
        origin = settings.public_api_origin if request.state.public_transport else f"http://{request.headers['host']}"
        subject_id = request.query_params.get('subject_id')
        if subject_id is not None and subjects:
            subjects.authorize_subject(access, subject_id)
            selected = subject_reader(subject_id)
            authorize_test_guide(request, subject_id, selected)
            if selected is None or not selected.source.is_file():
                return {'status': 'empty', 'html': '', 'math_css': '', 'version': None,
                        'modified_at': None, 'message': '该学科指南暂未填写。'}
            return selected.read(origin + '/subject-guide-assets/' + subject_id, access)
        authorize_test_guide(request, 'math', reader)
        return reader.read(origin + '/guide-assets', access)

    def asset_access(request):
        # Images may be fetched anonymously; they only see unmarked content.
        # Resolve supplied tokens on every request, including revocations.
        token = request.headers.get('x-learning-identity')
        return access_store.resolve(token) if token else None

    @app.get('/subject-guide-assets/{subject_id}/{relative:path}')
    def subject_guide_asset(subject_id: str, relative: str, request: Request):
        access = asset_access(request)
        if subjects:
            subjects.require(subject_id)
            if access is None:
                # Every subject-scoped endpoint uses the same identity boundary.
                # Anonymous compatibility remains on the legacy /guide-assets URL.
                access = access_store.resolve(None)
            if access is not None:
                subjects.authorize_subject(access, subject_id)
        selected = subject_reader(subject_id) if subjects else None
        authorize_test_guide(request, subject_id, selected)
        if selected is None:
            raise DomainError('guide_asset_not_found', '未找到指南资源。', 404)
        return asset_response(selected.asset(relative, access))

    def asset_response(resource):
        media_type = mimetypes.guess_type(resource.name)[0] or 'application/octet-stream'
        disposition = 'inline' if media_type.startswith('image/') else 'attachment'
        return FileResponse(resource, media_type=media_type,
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                     'Content-Disposition': f"{disposition}; filename*=UTF-8''{quote(resource.name)}"})

    @app.get('/guide-assets/{relative:path}')
    def guide_asset(relative: str, request: Request):
        authorize_test_guide(request, 'math', reader)
        resource = reader.asset(relative, asset_access(request))
        return asset_response(resource)
