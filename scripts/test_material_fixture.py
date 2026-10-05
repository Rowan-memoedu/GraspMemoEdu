"""Retained, isolated fixtures for administrator-only material acceptance."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from tests.fixtures import synthetic_course, DeterministicJudge
from tests.training_fixtures import training_bank


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--port', default=18856, type=int)
    args = parser.parse_args(); root = args.output.resolve()
    if root.exists() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or args.port in (8765, 18768):
        raise ValueError('Use a new isolated fixture directory and port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    data = root / 'data'; data.mkdir(parents=True)
    courses = []
    for kind, title in [('public', '普通课程'), ('test', '管理员测试课程')]:
        topic = deepcopy(synthetic_course(1)); topic.update(id=kind + '-topic', title=title)
        (data / (kind + '.json')).write_text(json.dumps(topic), encoding='utf-8')
        courses.append({'id': kind + '-course', 'title': title, 'topics': [{'id': topic['id'], 'path': kind + '.json'}]})
    (data / 'catalog.json').write_text(json.dumps({'courses': courses}), encoding='utf-8')
    (data / 'training-banks').mkdir(); bank = training_bank()
    (data / 'training-banks/isolated-bank.json').write_text(json.dumps(bank), encoding='utf-8')
    (data / 'test-materials.json').write_text(json.dumps({'schema': 1, 'courses': ['test-course'],
        'banks': ['isolated-bank'], 'guides': ['math']}), encoding='utf-8')
    (data / 'guide').mkdir()
    (data / 'guide/guide.md').write_text('# 管理员测试指南\n[测试附件](test.txt)', encoding='utf-8')
    (data / 'guide/test.txt').write_text('ADMIN TEST MATERIAL', encoding='utf-8')
    app = create_app(Settings(data_dir=data, course_path=data / 'public.json', port=args.port),
        LearningService(json.loads((data / 'public.json').read_text()), data, DeterministicJudge()))
    store = app.state.access_store; identities = {}
    identities['standard'] = store.create_account('普通验收', purpose='test')['invitation_code']
    with store._transaction() as con:
        identities['admin'] = store._insert_account(con, '管理员验收', is_admin=True)['invitation_code']
    (root / 'identities.json').write_text(json.dumps(identities), encoding='utf-8')
    print('READY isolated administrator-material fixture', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
