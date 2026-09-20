"""One-way local Markdown guide and a narrowly scoped public asset reader."""
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import threading
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import FileResponse

from .service import DomainError
from .subjects import Subjects


class GuideReader:
    MAX_SOURCE_BYTES = 1024 * 1024
    MAX_ASSET_BYTES = 20 * 1024 * 1024
    ASSET_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.pdf', '.txt', '.csv'}

    def __init__(self, source):
        self.source = Path(source)
        self._lock = threading.RLock()
        self._source_digest = None
        self._rendered = None
        self._resources = set()

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

    def read(self, asset_base):
        with self._lock:
            try:
                if not self.source.is_file() or self.source.stat().st_size > self.MAX_SOURCE_BYTES:
                    raise OSError('Missing or oversized guide')
                raw = self.source.read_bytes()
                if len(raw) > self.MAX_SOURCE_BYTES:
                    raise ValueError('Guide too large')
                text = raw.decode('utf-8-sig')
                digest = hashlib.sha256(raw).hexdigest()
                if digest != self._source_digest:
                    rendered = self._compile(text) if text.strip() else {'html': '', 'math_css': '', 'resources': []}
                    self._rendered, self._source_digest = rendered, digest
                    self._resources = set(rendered['resources'])
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
                return {'status': 'ready' if text.strip() else 'empty', 'html': html,
                        'math_css': self._rendered.get('math_css', ''), 'version': version,
                        'modified_at': datetime.fromtimestamp(self.source.stat().st_mtime, timezone.utc).isoformat(),
                        'message': '' if text.strip() else '指南暂未填写。'}
            except (OSError, ValueError, subprocess.SubprocessError):
                self._resources = set()
                self._source_digest = None
                self._rendered = None
                return {'status': 'unavailable', 'html': '', 'math_css': '', 'version': None,
                        'modified_at': None, 'message': '指南暂时无法读取，请稍后重试。'}

    def asset(self, relative):
        with self._lock:
            # Recheck the source so a removed reference stops being exposed.
            self.read('https://guide-asset.invalid')
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
        access_store.resolve(request.headers.get('x-learning-identity'))
        origin = settings.public_api_origin if request.state.public_transport else f"http://{request.headers['host']}"
        subject_id = request.query_params.get('subject_id')
        if subject_id is not None and subjects:
            selected = subject_reader(subject_id)
            if selected is None or not selected.source.is_file():
                return {'status': 'empty', 'html': '', 'math_css': '', 'version': None,
                        'modified_at': None, 'message': '该学科指南暂未填写。'}
            return selected.read(origin + '/subject-guide-assets/' + subject_id)
        return reader.read(origin + '/guide-assets')

    @app.get('/subject-guide-assets/{subject_id}/{relative:path}')
    def subject_guide_asset(subject_id: str, relative: str):
        selected = subject_reader(subject_id) if subjects else None
        if selected is None:
            raise DomainError('guide_asset_not_found', '未找到指南资源。', 404)
        return asset_response(selected.asset(relative))

    def asset_response(resource):
        media_type = mimetypes.guess_type(resource.name)[0] or 'application/octet-stream'
        disposition = 'inline' if media_type.startswith('image/') else 'attachment'
        return FileResponse(resource, media_type=media_type,
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                     'Content-Disposition': f"{disposition}; filename*=UTF-8''{quote(resource.name)}"})

    @app.get('/guide-assets/{relative:path}')
    def guide_asset(relative: str):
        resource = reader.asset(relative)
        return asset_response(resource)
