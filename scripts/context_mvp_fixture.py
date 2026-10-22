"""Run the real English MVP bundle on loopback with entirely isolated accounts."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException, Request
import uvicorn
from graspmemoedu.account_admin import _private_permissions
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.judge import ExistingJudge
from graspmemoedu.access import credential_digest
from graspmemoedu.service import create_learning_service
from tests.fixtures import synthetic_course


def configure_asr():
    """Read existing configuration only into this fixture process; never persist it."""
    value = os.environ.get('DASHSCOPE_API_KEY')
    if not value and sys.platform == 'win32':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                value = winreg.QueryValueEx(key, 'DASHSCOPE_API_KEY')[0]
        except OSError:
            pass
    if value:
        os.environ['GRASPMEMOEDU_ASR_API_KEY'] = value
    return bool(value)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--dictionary', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18861)
    args = parser.parse_args()
    root = args.output.resolve()
    if (not args.output.is_absolute() or root.exists()
            or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve())
            or args.port in (8765, 8767, 18768)):
        raise ValueError('Use a fresh D: artifact directory and an isolated port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    bundle = args.bundle.resolve()
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('adapter') != 'structured-atomic' or not manifest.get('test_materials'):
        raise ValueError('Expected a compiled bundle with a test-material policy')
    verified = {}
    for name, sha in manifest['files'].items():
        source = (bundle / name).resolve()
        if not source.is_relative_to(bundle):
            raise ValueError('Bundle path escaped its root')
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError('Bundle hash mismatch: ' + name)
        verified[name] = raw
    data = root / 'data'
    data.mkdir(parents=True)
    # Policy exists before any material is loaded by the application.
    write_json(data / 'test-materials.json', {'schema': 1, **manifest['test_materials']})
    for name, raw in verified.items():
        target = data / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    catalog = json.loads((data / 'catalog.json').read_text(encoding='utf-8'))
    english_courses = [course['id'] for course in catalog['courses']]
    formal = synthetic_course(1)
    write_json(data / 'formal.json', formal)
    catalog['courses'].insert(0, {'id': 'fixture-formal', 'title': '普通内容对照',
        'topics': [{'id': formal['id'], 'path': 'formal.json'}]})
    write_json(data / 'catalog.json', catalog)
    write_json(data / 'subjects.json', {'subjects': [
        {'id': 'math', 'course_ids': ['fixture-formal']},
        {'id': 'english', 'course_ids': english_courses}]})
    dictionary = args.dictionary.resolve()
    (data / 'context-dictionary').mkdir()
    # Same-volume hard links reuse the prepared, read-only 97 MB index.
    for name in ('wn.db', 'dictionary-manifest.json'):
        os.link(dictionary / name, data / 'context-dictionary' / name)
    asr_configured = configure_asr()
    when = [datetime.now(timezone.utc)]
    judge = ExistingJudge(Path('D:/Projects/MathAnswerJudge'))
    settings = Settings(data_dir=data, course_path=data / 'formal.json', port=args.port)
    app = create_app(settings, create_learning_service(formal, data, judge), atomic_clock=lambda: when[0])
    store, identities = app.state.access_store, {}
    roles = [('standard', 'test', {}), ('advanced_nonstudent', 'test', {'is_advanced_learner': True}),
        ('ordinary_student', 'test', {'is_student': True}),
        ('student1', 'test', {'is_advanced_learner': True, 'is_student': True}),
        ('student2', 'test', {'is_advanced_learner': True, 'is_student': True}),
        ('admin1', 'test', {'is_admin': True}), ('admin2', 'test', {'is_admin': True}),
        ('formal_student', 'learner', {'is_advanced_learner': True, 'is_student': True}),
        ('formal_admin', 'learner', {'is_admin': True})]
    for role, purpose, flags in roles:
        with store._transaction() as con:
            created = store._insert_account(con, role, purpose=purpose, **flags)
        identities[role] = {'code': created['invitation_code'], 'id': created['account']['learner_id']}
    for index in (1, 2):
        app.state.help_requests.assign_admin(identities[f'student{index}']['id'], identities[f'admin{index}']['id'])
    private = root / 'identities.json'
    private.touch()
    _private_permissions(private, apply=True)
    write_json(private, identities)
    write_json(root / 'fixture.json', {'port': args.port, 'instance_id': settings.instance_id,
        'pid': os.getpid(), 'created_at': when[0].isoformat(), 'bundle': str(bundle),
        'bundle_manifest_sha256': hashlib.sha256((bundle / 'manifest.json').read_bytes()).hexdigest(),
        'verified_files': len(verified), 'real_judge': True, 'asr_configured': asr_configured,
        'dictionary': str(dictionary), 'production_data': False})

    @asynccontextmanager
    async def lifespan(_):
        async with app.router.lifespan_context(app):
            yield

    harness = FastAPI(lifespan=lifespan)

    def authorize(request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        return store.resolve(request.headers.get('x-learning-identity'))

    @harness.post('/__qa/advance')
    async def advance(request: Request):
        authorize(request)
        seconds = (await request.json()).get('seconds')
        if type(seconds) is not int or not 0 <= seconds <= 86400:
            raise HTTPException(422)
        when[0] += timedelta(seconds=seconds)
        return {'server_time': when[0].isoformat()}

    @harness.get('/__qa/audit')
    def audit(request: Request, topic_id: str):
        access = authorize(request)
        key = (access['learner_id'], topic_id)
        if access.get('is_admin'):
            key += (credential_digest('preview', request.headers.get('x-learning-identity') or ''),)
        service = app.state.learning_services.get(key)
        state = None
        if service:
            backend = getattr(service, 'atomic', service)
            with closing(backend._connect()) as con:
                state = backend._load(con)
        with closing(store._connect()) as con:
            counts = {}
            for table in ('topics', 'atomic_states', 'reading_contexts'):
                if con.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone():
                    counts[table] = con.execute(f'SELECT COUNT(*) FROM {table} WHERE learner=?', (access['learner_id'],)).fetchone()[0]
            integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
        return {'state': state, 'persistent_rows': counts, 'integrity': integrity, 'real_judge': True}

    harness.mount('/', app)
    print('READY English MVP: verified source bundle, real judge, isolated identities', flush=True)
    uvicorn.run(harness, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
