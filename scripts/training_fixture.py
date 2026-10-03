"""Disposable loopback training-bank acceptance fixture; never touches live data."""
import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import hashlib
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
from graspmemoedu.service import LearningService
from graspmemoedu.training_content import install
from tests.fixtures import synthetic_course


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--port', type=int, default=18849)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or args.port in (8765, 8767, 18768):
        raise ValueError('Use a new isolated artifact directory and a non-production port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    root.mkdir(parents=True)
    data = root / 'data'; data.mkdir()
    source = {'id': 'training-acceptance', 'subject_id': 'math', 'title': '隔离题库验收', 'description': '合成内容，绝不发布到正式网站。',
        'groups': [{'id': 'chapter', 'title': '基础概念', 'questions': []},
                   {'id': 'group-a', 'parent_id': 'chapter', 'title': '加法题组', 'questions': [
                       {'id': 'essay', 'title': '说明加法', 'question': '计算 $2+3$，可以说明你的思路。',
                        'explanation': '将两个数相加，得到 $2+3=5$。', 'reference_answer': '5', 'constraints': '接受等值表达。'},
                       {'id': 'choice', 'title': '选择结果', 'question': '选择 $2+3$ 的结果。', 'explanation': '$2+3=5$。',
                        'interaction': {'version': 1, 'type': 'choice', 'options': [{'id': 'a', 'text': '$5$'}, {'id': 'b', 'text': '$6$'}], 'correct_id': 'a'}},
                       {'id': 'blank', 'title': '填写结果', 'question': '$2+3=$ {{blank:a}}。', 'explanation': '$2+3=5$。',
                        'interaction': {'version': 1, 'type': 'fill_blank', 'grading': 'exact', 'blanks': [{'id': 'a', 'answers': ['5']}]}},
                       {'id': 'boolean', 'title': '判断等式', 'question': '判断下列等式。', 'explanation': '$2+3=5$。',
                        'interaction': {'version': 1, 'type': 'true_false', 'statements': [{'id': 'a', 'text': '$2+3=5$', 'correct': True}]}}]},
                   {'id': 'group-b', 'parent_id': 'chapter', 'title': '另一题组', 'questions': [
                       {'id': 'other', 'title': '认识数字', 'question': '写出数字一。', 'explanation': '数字一写作 $1$。', 'reference_answer': '1'}]}]}
    source_path = root / 'training-source.json'; source_path.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    compiled = root / 'training-bank.json'
    subprocess.run(['node', str(Path(__file__).with_name('import_training_bank.mjs')), str(source_path), str(compiled)], check=True)
    install(compiled, data)
    course = synthetic_course(1)
    for module in course['modules']:
        for question in [module['example'], *module['practices']]:
            question['reference_answer'] = '5'
    course['version'] = hashlib.sha256(json.dumps(course, sort_keys=True).encode()).hexdigest()
    path = data / 'topic.json'; path.write_text(json.dumps(course), encoding='utf-8')
    (data / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'legacy-course', 'title': '原课程隔离基线',
        'units': [{'id': 'chapter', 'title': '基础概念', 'topic_ids': [course['id']]}],
        'topics': [{'id': course['id'], 'path': 'topic.json'}]}]}), encoding='utf-8')
    when = [datetime.now(timezone.utc)]
    app = create_app(Settings(data_dir=data, course_path=path, port=args.port),
                     LearningService(course, data, ExistingJudge(Path('D:/Projects/MathAnswerJudge'))), atomic_clock=lambda: when[0])
    store = app.state.access_store
    accounts = {}
    with store._transaction() as con:
        for name, properties in [('standard', {}), ('advanced', {'is_advanced_learner': True}), ('admin', {'is_admin': True})]:
            accounts[name] = store._insert_account(con, '隔离 ' + name, purpose='test', **properties)['invitation_code']
    # Only synthetic credentials for this disposable loopback fixture.
    (root / 'identities.json').write_text(json.dumps(accounts), encoding='utf-8')

    @asynccontextmanager
    async def lifespan(_):
        async with app.router.lifespan_context(app):
            yield

    harness = FastAPI(lifespan=lifespan)

    @harness.post('/__qa/advance')
    async def advance(request: Request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        when[0] += timedelta(hours=7)
        return {'clock_advanced': True}

    harness.mount('/', app)
    print('READY isolated training fixture', flush=True)
    uvicorn.run(harness, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__': main()
