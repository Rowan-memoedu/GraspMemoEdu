"""Loopback-only synthetic website fixture; never opens formal learning data.

Run: python scripts/review_web_fixture.py --output D:/Artifacts/MathLearningWeb/review-web/run-unique
Then: node scripts/review_web_acceptance.mjs http://127.0.0.1:8779 OUTPUT
Pass --real-judge to reuse MathAnswerJudge instead of the explicit arithmetic oracle.
"""
import argparse
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request, HTTPException
import uvicorn
from math_learning.api import create_app
from math_learning.config import Settings
from math_learning.judge import ExistingJudge
from math_learning.service import LearningService


def make_course():
    def question(name, index):
        return {'id': name, 'title': '算术练习', 'question': f'Calculate {index} + {42-index}.',
                'html': f'<p>{index} + {42-index} = ?</p>', 'reference_answer': '42',
                'answer_type': 'number', 'constraints': 'Give one number.',
                'explanation_html': '<p>两个数相加，结果为 42。</p>'}
    course = {'id': 'review-web-canary', 'title': '隔离算术验收',
              'introduction': {'html': '<p>INTRODUCTION_SENTINEL</p>'},
              'modules': [{'id': f'module-{m}', 'title': f'算术模块 {m}',
                           'example': {**question(f'example-{m}', 1), 'html': '<p>EXAMPLE_SENTINEL</p>'},
                           'practices': [question(f'practice-{m}-{i}', i) for i in range(1, 12)]}
                          for m in range(1, 3)]}
    course['version'] = hashlib.sha256(json.dumps(course, sort_keys=True).encode()).hexdigest()
    return course


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8779)
    parser.add_argument('--real-judge', action='store_true')
    args = parser.parse_args()
    root = args.output.resolve()
    artifact_root = Path('D:/Artifacts/MathLearningWeb/review-web').resolve()
    if not args.output.is_absolute() or not root.is_relative_to(artifact_root) or root.exists():
        raise ValueError('Use a NEW directory below D:/Artifacts/MathLearningWeb/review-web')
    if args.port in (8765, 8767):
        raise ValueError('Never use a formal service port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    root.mkdir(parents=True)
    course = make_course()
    data = root / 'data'
    data.mkdir()
    source = data / 'course.json'
    source.write_text(json.dumps(course, ensure_ascii=False), encoding='utf-8')
    external = ExistingJudge(Path('D:/Projects/MathAnswerJudge')) if args.real_judge else None
    def judge(question, reference, answer):
        time.sleep(0.35)  # Allows refresh/pending checks without mocking HTTP.
        return external(question, reference, answer) if external else {
            'status': 'judged', 'correct': answer.strip() == reference,
            'reason': 'Isolated synthetic arithmetic oracle'}
    app = create_app(Settings(data_dir=data, course_path=source, port=args.port),
                     LearningService(course, data, judge))
    @asynccontextmanager
    async def lifespan(_):
        async with app.router.lifespan_context(app):
            yield
    harness = FastAPI(lifespan=lifespan)

    def qa_learner(request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        access = app.state.access_store.resolve(request.headers.get('x-learning-identity'))
        return app.state.learning_services[(access['learner_id'], course['id'])]

    @harness.get('/__qa/audit')
    async def audit(request: Request):
        learner = qa_learner(request)
        with closing(learner._connect()) as con:
            rows = con.execute('SELECT source,mastery,due_at FROM review_ratings WHERE learner=? AND topic=? ORDER BY rowid',
                               (learner.learner_id, learner.topic_id)).fetchall()
            return {'ratings': [dict(row) for row in rows], 'integrity': con.execute('PRAGMA integrity_check').fetchone()[0]}

    @harness.post('/__qa/seed')
    async def seed(request: Request):
        # This endpoint exists only in this separately launched fixture process.
        learner = qa_learner(request)
        body = await request.json()
        delay = float(body.get('due_in_seconds', -10))
        if not -60 <= delay <= 60:
            raise HTTPException(422)
        module = body.get('module_id', 'module-1')
        # A new Again card has the upstream one-minute learning step. Derive
        # the completion time; no database edits and no system-clock changes.
        result = learner.review_backend.record_module_result(
            module_id=module, mastery=1, result_id=f'fixture-{module}',
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=60-delay))
        return {'due_at': result['due_at'], 'real_judge': args.real_judge}

    harness.mount('/', app)
    (root / 'fixture.json').write_text(json.dumps({'base': f'http://127.0.0.1:{args.port}',
        'topic_id': course['id'], 'real_judge': args.real_judge, 'formal_data_opened': False}), encoding='utf-8')
    uvicorn.run(harness, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
