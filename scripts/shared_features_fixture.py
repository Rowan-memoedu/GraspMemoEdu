"""Loopback-only acceptance server for shared interactions, reports and Catalog."""
import argparse
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, Request
import uvicorn
from math_learning.api import create_app
from math_learning.author_import import prepare_topic
from math_learning.config import Settings
from math_learning.judge import ExistingJudge
from math_learning.service import LearningService
from tests.question_fixtures import author_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18842)
    parser.add_argument('--real-judge', action='store_true')
    args = parser.parse_args()
    root = args.output.resolve()
    if (not args.output.is_absolute() or not root.is_relative_to(Path('D:/Artifacts/MathLearningWeb/shared-features').resolve())
            or root.exists() or args.port in (8765, 8767, 18768)):
        raise ValueError('Use a new isolated artifact directory and non-production port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    root.mkdir(parents=True)
    raw = root / 'source.md'
    raw.write_text(author_source(), encoding='utf-8')
    result = prepare_topic(raw, 'shared-types', root / 'imported')
    if result['status'] != 'approved':
        raise ValueError('Synthetic import failed: ' + str(result))
    course = json.loads((root / 'imported/topic.json').read_text(encoding='utf-8'))
    data = root / 'data'
    data.mkdir()
    (data / 'math.json').write_text(json.dumps(course, ensure_ascii=False), encoding='utf-8')
    physics = copy.deepcopy(course)
    physics.update(id='physics-types', title='隔离物理课程')
    (data / 'physics.json').write_text(json.dumps(physics, ensure_ascii=False), encoding='utf-8')
    (data / 'catalog.json').write_text(json.dumps({'courses': [
        {'id': 'math-course', 'title': '共享功能验收', 'topics': [{'id': course['id'], 'path': 'math.json'}]},
        {'id': 'physics-course', 'title': '共享功能验收', 'topics': [{'id': physics['id'], 'path': 'physics.json'}]},
    ]}), encoding='utf-8')
    (data / 'subjects.json').write_text(json.dumps({'subjects': [
        {'id': 'math', 'course_ids': ['math-course']}, {'id': 'physics', 'course_ids': ['physics-course']},
    ]}), encoding='utf-8')
    judge = ExistingJudge(Path('D:/Projects/MathAnswerJudge')) if args.real_judge else lambda q, r, a: {
        'status': 'judged', 'correct': a == r, 'reason': 'Synthetic fixture; no semantic verification.'}
    app = create_app(Settings(data_dir=data, course_path=data / 'math.json', port=args.port), LearningService(course, data, judge))

    @asynccontextmanager
    async def lifespan(_):
        async with app.router.lifespan_context(app):
            yield

    harness = FastAPI(lifespan=lifespan)

    def learner(request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        access = app.state.access_store.resolve(request.headers.get('x-learning-identity'))
        return app.state.learning_services[(access['learner_id'], course['id'])]

    @harness.post('/__qa/seed-review')
    async def seed(request: Request):
        learning = learner(request)
        body = await request.json()
        module = body['module_id']
        backend = learning.review_backend
        with closing(learning._connect()) as con:
            card = backend._card(con, module)
        if card is None:
            backend.record_module_result(module_id=module, mastery=1, result_id=f'isolated-{module}',
                                         completed_at=datetime.now(timezone.utc))
            with closing(learning._connect()) as con:
                card = backend._card(con, module)
        # Only this fixture learner's injected clock advances. Preserve the
        # actual Lesson result, card and system time; never backdate a rating.
        when = datetime.fromisoformat(card['due_at']) + timedelta(seconds=1)
        backend.clock = lambda: when
        return {'due_at': card['due_at']}

    @harness.get('/__qa/audit')
    async def audit(request: Request):
        learning = learner(request)
        with closing(learning._connect()) as con:
            reports = con.execute('''SELECT f.id,c.body FROM feedback f JOIN feedback_corrections c ON c.feedback_id=f.id
                                     WHERE learner_id=?''', (learning.learner_id,)).fetchall()
            return {'corrections': [json.loads(row['body']) for row in reports],
                    'integrity': con.execute('PRAGMA integrity_check').fetchone()[0], 'real_judge': args.real_judge}

    harness.mount('/', app)
    (root / 'fixture.json').write_text(json.dumps({'base': f'http://127.0.0.1:{args.port}', 'topic_id': course['id'],
        'real_judge': args.real_judge, 'formal_data_opened': False}), encoding='utf-8')
    print('READY isolated shared feature fixture', flush=True)
    uvicorn.run(harness, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
