"""Loopback-only, synthetic choice/blank/browser acceptance; no formal data."""
import argparse
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
import json
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
from graspmemoedu.question_bank import install_bank
from graspmemoedu.question_conversion import build_variant
from graspmemoedu.service import LearningService
from tests.fixtures import synthetic_course
from tests.test_question_bank import bank_fixture
from scripts.verify_content import ExactTex


class OfflineNumericJudge:
    """Fixture-only exact arithmetic; no provider credentials or HTTP calls."""
    def __call__(self, question, reference, answer):
        try:
            if reference.lstrip().startswith('{'):
                references, values = json.loads(reference), json.loads(answer)
                correct = (set(references) == set(values) and all(
                    any(ExactTex(value).evaluate() == ExactTex(values[key]).evaluate() for value in allowed)
                    for key, allowed in references.items()))
            else:
                correct = ExactTex(reference).evaluate() == ExactTex(answer).evaluate()
            return {'status': 'judged', 'correct': correct, 'reason': 'Offline numeric fixture judgement'}
        except (ValueError, ArithmeticError, TypeError, KeyError):
            return {'status': 'judged', 'correct': False, 'reason': 'Not an exact numeric fixture expression'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18847)
    parser.add_argument('--real-judge', action='store_true')
    parser.add_argument('--course', type=Path, help='Optional read-only source copy for full-bank acceptance')
    parser.add_argument('--bank', type=Path, help='Validated full bank; never a pilot')
    args = parser.parse_args()
    root = args.output.resolve()
    if not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or root.exists() or args.port in (8765, 8767, 18768):
        raise ValueError('Use a new isolated artifact directory and a non-production port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    root.mkdir(parents=True)
    if bool(args.course) != bool(args.bank):
        raise ValueError('Specify both a read-only course copy and its full validated bank')
    if args.course:
        course = json.loads(args.course.read_text(encoding='utf-8-sig'))
        bank = json.loads(args.bank.read_text(encoding='utf-8'))
    else:
        course = synthetic_course(2)
        for module in course['modules']:
            for q in [module['example'], *module['practices']]:
                q.update(question='计算 $2+3$。', html='<p>计算 2+3。</p>', reference_answer='5',
                         constraints='接受数学上等值的精确表达式。', explanation_html='<p>将两个数相加，结果是 5。</p>')
        bank = bank_fixture(course)
        for q in [course['modules'][1]['example'], *course['modules'][1]['practices']]:
            bank['questions'][q['id']] = build_variant(q)
        source = root / 'bank-source.json'
        source.write_text(json.dumps(bank, ensure_ascii=False), encoding='utf-8')
        compiled = root / 'bank.json'
        subprocess.run(['node', str(Path(__file__).with_name('compile_question_bank.mjs')), str(source), str(compiled)], check=True)
        bank = json.loads(compiled.read_text(encoding='utf-8'))
    data = root / 'data'
    data.mkdir()
    path = data / 'topic.json'
    path.write_text(json.dumps(course, ensure_ascii=False), encoding='utf-8')
    (data / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'bank-course', 'title': '题库隔离验收',
        'topics': [{'id': course['id'], 'path': 'topic.json'}]}]}), encoding='utf-8')
    (data / 'subjects.json').write_text(json.dumps({'subjects': [{'id': 'math', 'course_ids': ['bank-course']}]}), encoding='utf-8')
    install_bank(bank, course, data)
    judge = ExistingJudge(Path('D:/Projects/MathAnswerJudge')) if args.real_judge else OfflineNumericJudge()
    app = create_app(Settings(data_dir=data, course_path=path, port=args.port), LearningService(course, data, judge))

    @asynccontextmanager
    async def lifespan(_):
        async with app.router.lifespan_context(app):
            yield

    harness = FastAPI(lifespan=lifespan)

    @harness.post('/__qa/seed-review')
    async def seed(request: Request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        access = app.state.access_store.resolve(request.headers.get('x-learning-identity'))
        learning = app.state.learning_services[(access['learner_id'], course['id'])]
        module_id = (await request.json())['module_id']
        backend = learning.review_backend
        with closing(learning._connect()) as con:
            card = backend._card(con, module_id)
        if not card:
            backend.record_module_result(module_id=module_id, mastery=4, result_id='fixture-' + module_id, completed_at=datetime.now(timezone.utc))
            with closing(learning._connect()) as con:
                card = backend._card(con, module_id)
        backend.clock = lambda: datetime.fromisoformat(card['due_at']) + timedelta(seconds=1)
        return {'ready': True}

    harness.mount('/', app)
    (root / 'fixture.json').write_text(json.dumps({'base': f'http://127.0.0.1:{args.port}', 'real_judge': args.real_judge,
        'synthetic_course': not bool(args.course), 'formal_progress_opened': False}), encoding='utf-8')
    print('READY question bank fixture', flush=True)
    uvicorn.run(harness, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
