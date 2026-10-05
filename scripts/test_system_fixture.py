"""Isolated test-system acceptance using a retained material archive and real judge."""
import argparse
from contextlib import closing, asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request, HTTPException
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.account_admin import _private_permissions
from graspmemoedu.config import Settings
from graspmemoedu.judge import ExistingJudge
from graspmemoedu.question_types import demo_answer
from graspmemoedu.service import LearningService
from graspmemoedu.test_scenarios import apply_plan, prime_variant_sources
from graspmemoedu.review_variants import install_variants
from scripts.seed_test_help import seed
from tests.fixtures import synthetic_course


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--materials', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18858)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or args.port in (8765, 18768):
        raise ValueError('Use a fresh isolated fixture root and port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    data = root / 'data'; data.mkdir(parents=True)
    with zipfile.ZipFile(args.materials) as z:
        manifest = json.loads(z.read('manifest.json'))
        for name, sha in manifest['files'].items():
            target = data / name
            assert target.resolve().is_relative_to(data.resolve())
            raw = z.read(name); assert hashlib.sha256(raw).hexdigest() == sha
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
    course = synthetic_course(1)
    (data / 'formal.json').write_text(json.dumps(course), encoding='utf-8')
    formal = {'id': 'formal-course', 'title': '普通内容对照', 'topics': [{'id': course['id'], 'path': 'formal.json'}]}
    (data / 'catalog.json').write_text(json.dumps({'courses': [formal, *manifest['courses']]}), encoding='utf-8')
    subjects = [{'id': sid, 'course_ids': ids} for sid, ids in manifest['subject_courses'].items()]
    subjects.append({'id': 'math', 'course_ids': ['formal-course']})
    (data / 'subjects.json').write_text(json.dumps({'subjects': subjects}), encoding='utf-8')
    (data / 'test-materials.json').write_text(json.dumps(manifest['policy']), encoding='utf-8')
    when = [datetime.now(timezone.utc)]
    judge = ExistingJudge(Path('D:/Projects/MathAnswerJudge'))
    app = create_app(Settings(data_dir=data, course_path=data / 'formal.json', port=args.port),
                     LearningService(course, data, judge), atomic_clock=lambda: when[0])
    store, identities, learners = app.state.access_store, {}, []
    for role, flags in [('standard', {}), ('advanced_nonstudent', {'is_advanced_learner': True}),
                        ('ordinary_student', {'is_student': True}),
                        ('student1', {'is_advanced_learner': True, 'is_student': True}),
                        ('student2', {'is_advanced_learner': True, 'is_student': True}),
                        ('admin1', {'is_admin': True}), ('admin2', {'is_admin': True})]:
        with store._transaction() as con:
            created = store._insert_account(con, role, purpose='test', **flags)
        identities[role] = {'code': created['invitation_code'], 'id': created['account']['learner_id']}
        if role.startswith('student'):
            learners.append(created['account']['learner_id'])
    for index in (1, 2):
        app.state.help_requests.assign_admin(identities['student' + str(index)]['id'], identities['admin' + str(index)]['id'])
    plan = {'schema': 1, 'learners': learners, 'topics': {'atomic-english': 'due', 'atomic-chinese': 'initial',
        'atomic-biology': 'future', 'atomic-chemistry': 'paused', 'test-english-topic': 'initial'},
        'banks': {'test-review-math': {'due': [q['id'] for g in app.state.training_catalog.bank('test-review-math')['groups'][:3] for q in g['questions']],
                                    'stopped': ['stopped'], 'manual_stop': ['paused']}}}
    (data / 'admin/test-scenarios.json').write_text(json.dumps(plan), encoding='utf-8')
    apply_plan(data, plan, data / 'admin/preset-before.sqlite3', now=when[0])
    install_variants(data, data / 'admin/testing-materials/practice-variants.json')
    prime_variant_sources(data, plan)
    seed(data, plan)
    private = root / 'identities.json'; private.touch(); _private_permissions(private, apply=True)
    private.write_text(json.dumps(identities), encoding='utf-8')
    @asynccontextmanager
    async def lifespan(_):
        async with app.router.lifespan_context(app):
            yield
    harness = FastAPI(lifespan=lifespan)
    def authorize(request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        return store.resolve(request.headers.get('x-learning-identity'))
    @harness.get('/__qa/answers')
    def answers(request: Request, paper: str):
        access = authorize(request)
        with closing(store._connect()) as con:
            row = con.execute('SELECT items FROM review_papers WHERE id=? AND learner=?', (paper, access['learner_id'])).fetchone()
        if not row:
            raise HTTPException(404)
        return {'items': [{'answer': demo_answer(i['question']), 'card_id': i['id']} for i in json.loads(row[0])]}
    @harness.post('/__qa/advance')
    async def advance(request: Request):
        authorize(request)
        seconds = (await request.json())['seconds']
        if type(seconds) is not int or not 0 <= seconds <= 86400 * 7:
            raise HTTPException(422)
        when[0] += timedelta(seconds=seconds)
        return {'now': when[0].isoformat()}
    print('READY isolated test system with real semantic judge', flush=True)
    harness.mount('/', app)
    uvicorn.run(harness, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
