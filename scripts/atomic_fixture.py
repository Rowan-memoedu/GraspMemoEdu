"""Loopback-only browser acceptance with synthetic compiled topics and a test clock."""
import argparse
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException, Request
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.judge import ExistingJudge
from graspmemoedu.service import create_learning_service
from tests.atomic_fixtures import atomic_author_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18843)
    parser.add_argument('--real-judge', action='store_true')
    args = parser.parse_args()
    root = args.output.resolve()
    if (not args.output.is_absolute() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve())
            or root.exists() or args.port in (8765, 8767, 18768)):
        raise ValueError('Use a new artifact directory and an isolated port')
    with socket.socket() as probe: probe.bind(('127.0.0.1', args.port))
    root.mkdir(parents=True)
    data = root / 'data'; data.mkdir()
    courses, subjects = [], []
    for subject in ('english', 'chinese', 'biology', 'chemistry'):
        source = atomic_author_source(subject, 'atomic-' + subject)
        if args.real_judge:
            source['flashcards'][4]['interaction'] = {'version': 1, 'type': 'text', 'grading': 'semantic'}
        source_file = root / f'{subject}.source.json'
        source_file.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
        output = data / f'{subject}.json'
        subprocess.run(['node', 'scripts/import_atomic_topic.mjs', str(source_file), str(output)],
                       check=True, env={**os.environ, 'GRASPMEMOEDU_PYTHON': sys.executable})
        courses.append({'id': subject + '-course', 'title': '原子训练隔离验收',
                        'topics': [{'id': source['id'], 'path': output.name}]})
        subjects.append({'id': subject, 'course_ids': [subject + '-course']})
    (data / 'catalog.json').write_text(json.dumps({'courses': courses}), encoding='utf-8')
    (data / 'subjects.json').write_text(json.dumps({'subjects': subjects}), encoding='utf-8')
    when = datetime.now(timezone.utc)
    judge = ExistingJudge(Path('D:/Projects/MathAnswerJudge')) if args.real_judge else None
    topic = json.loads((data / 'english.json').read_text(encoding='utf-8'))
    app = create_app(Settings(data_dir=data, course_path=data / 'english.json', port=args.port),
                     create_learning_service(topic, data, judge), atomic_clock=lambda: when)

    @asynccontextmanager
    async def lifespan(_):
        async with app.router.lifespan_context(app): yield
    harness = FastAPI(lifespan=lifespan)

    def learner(request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        access = app.state.access_store.resolve(request.headers.get('x-learning-identity'))
        return app.state.learning_services[(access['learner_id'], 'atomic-english')]

    @harness.post('/__qa/advance')
    async def advance(request: Request):
        nonlocal when
        learner(request)
        seconds = (await request.json())['seconds']
        if type(seconds) is not int or not 0 <= seconds <= 86400: raise HTTPException(422)
        when += timedelta(seconds=seconds)
        return {'server_time': when.isoformat()}

    @harness.get('/__qa/audit')
    def audit(request: Request):
        service = learner(request)
        with closing(service._connect()) as con:
            state = service._load(con)
            return {'points': state['points'], 'history': state['history'], 'pause': state['pause'],
                    'integrity': con.execute('PRAGMA integrity_check').fetchone()[0], 'real_judge': args.real_judge}

    harness.mount('/', app)
    print('READY atomic fixture; synthetic content only', flush=True)
    uvicorn.run(harness, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__': main()
