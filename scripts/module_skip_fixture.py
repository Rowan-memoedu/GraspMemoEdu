"""Loopback-only module-skip acceptance; creates no formal learner records."""
import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request, HTTPException
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import create_learning_service
from tests.test_ordered_learning import ordered_topic
from tests.fixtures import synthetic_course
from tests.atomic_fixtures import atomic_topic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8783)
    args = parser.parse_args()
    root = args.output.resolve()
    if not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu/module-skip-20261005').resolve()) or root.exists():
        raise ValueError('Use a new isolated artifact directory')
    if args.port in (8765, 8767, 18768): raise ValueError('Formal ports are forbidden')
    with socket.socket() as probe: probe.bind(('127.0.0.1', args.port))
    data = root / 'data'; data.mkdir(parents=True)
    topic = ordered_topic(); topic.update(id='skip-parent', title='隔离跳过验收')
    third = synthetic_course(3)['modules'][2]; topic['modules'].append(third)
    topic['content_order'].append({'kind': 'example', 'id': third['id']})
    for module in topic['modules']:
        module['title'] = '算术模块 ' + module['id'][-1]
        for question in [module['example'], *module['practices']]:
            question.update(question='Calculate 2 + 3.', html='<p>2 + 3 = ?</p>', reference_answer='5',
                            explanation_html='<p>两个数相加等于 5。</p>')
    child = synthetic_course(1); child.update(id='skip-child', title='下游 Topic')
    atomic = atomic_topic('biology', 'skip-atomic')
    for item in (topic, child, atomic):
        (data / (item['id'] + '.json')).write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
    (data / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'skip-course', 'title': '隔离课程', 'topics': [
        {'id': topic['id'], 'path': topic['id'] + '.json'},
        {'id': child['id'], 'path': child['id'] + '.json', 'prerequisites': [topic['id']]}]},
        {'id': 'atomic-course', 'title': '隔离原子课程', 'topics': [{'id': atomic['id'], 'path': atomic['id'] + '.json'}]}]}), encoding='utf-8')
    (data / 'subjects.json').write_text(json.dumps({'subjects': [
        {'id': 'math', 'course_ids': ['skip-course']}, {'id': 'biology', 'course_ids': ['atomic-course']}]}), encoding='utf-8')
    judge = lambda q, r, a: {'status': 'judged', 'correct': a.strip() == r, 'reason': 'Isolated arithmetic fixture'}
    app = create_app(Settings(data_dir=data, port=args.port), create_learning_service(topic, data, judge))
    store = app.state.access_store; identities = {}
    for name, flags in [('student', {'is_student': True}), ('ordinary', {}), ('admin', {'is_admin': True})]:
        with store._transaction() as con:
            account = store._insert_account(con, '隔离 ' + name, purpose='test', **flags)
        identities[name] = account['invitation_code']
        if name == 'admin': app.state.help_requests.set_default_admin(account['account']['learner_id'])
    (root / 'identities.json').write_text(json.dumps(identities), encoding='utf-8')
    @asynccontextmanager
    async def lifespan(_):
        async with app.router.lifespan_context(app): yield
    harness = FastAPI(lifespan=lifespan)
    @harness.post('/__qa/pause')
    async def pause(request: Request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        access = store.resolve(request.headers.get('x-learning-identity'))
        learner = app.state.admin_pauses.service_for(access['learner_id'], topic['id'])
        with learner._transaction() as con:
            state = learner._load(con); learner._pause(con, state, 'practice_error_limit'); learner._save(con, state)
        return learner.state()
    @harness.post('/__qa/review')
    async def review(request: Request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        access = store.resolve(request.headers.get('x-learning-identity'))
        learner = app.state.admin_pauses.service_for(access['learner_id'], topic['id'])
        later = datetime.now(timezone.utc) + timedelta(days=365)
        learner.review_backend.clock = lambda: later
        return learner.review_backend.begin('module-3', request_id='fixture-review')
    harness.mount('/', app)
    print('Loopback fixture ready; formal_data_opened=false', flush=True)
    uvicorn.run(harness, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__': main()
