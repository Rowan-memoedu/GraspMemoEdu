"""Serve an approved import in isolated data with the real configured judge."""
import argparse
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.topic_install import install_topic, read_package
from tests.fixtures import synthetic_course


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8779)
    args = parser.parse_args()
    root = args.output.resolve()
    allowed = Path('D:/Artifacts/GraspMemoEdu/author-import/qa').resolve()
    if not args.output.is_absolute() or not root.is_relative_to(allowed) or root == allowed or root.exists():
        raise ValueError('Use a new absolute child of D:/Artifacts/GraspMemoEdu/author-import/qa')
    if args.port in (8765, 8767) or not 1024 <= args.port <= 65535:
        raise ValueError('Use an isolated local port; formal service ports are forbidden')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    _, _, topic = read_package(args.package)
    if topic['id'] != 'physics-uniform-speed-import-test':
        raise ValueError('This fixture accepts the declared physics import canary only')
    data = root / 'data'
    data.mkdir(parents=True)
    math_topic = synthetic_course(module_count=1)
    math_path = data / 'math-base.json'
    write_json(math_path, math_topic)
    write_json(data / 'catalog.json', {'courses': [
        {'id': 'math-fixture-course', 'title': '隔离数学测试',
         'topics': [{'id': math_topic['id'], 'path': str(math_path), 'prerequisites': []}]},
        {'id': 'test-physics-course', 'title': '【测试】物理导入验收', 'topics': []},
    ]})
    write_json(data / 'subjects.json', {'subjects': [
        {'id': 'math', 'course_ids': ['math-fixture-course']},
        {'id': 'physics', 'course_ids': ['test-physics-course']},
    ]})
    write_json(data / 'settings.json', {'course_path': str(math_path), 'port': args.port})
    settings = Settings(data_dir=data, course_path=math_path, port=args.port)
    installed = install_topic(settings, args.package, subject_id='physics', course_id='test-physics-course')
    assert installed['status'] == 'installed', installed
    app = create_app(settings)
    created = app.state.access_store.create_account(
        '作者导入器隔离测试', topic_ids=[topic['id']], purpose='test')
    write_json(root / 'account.private.json', {'accounts': [
        {**created['account'], 'invitation_code': created['invitation_code']}
    ]})
    fixture = {'base': f'http://127.0.0.1:{args.port}', 'topic_id': topic['id'],
               'course_id': 'test-physics-course', 'real_judge': True, 'formal_data_opened': False,
               'topic_path': str(data / 'imported-topics' / topic['id'] / 'topic.json'),
               'credentials_file': str(root / 'account.private.json')}
    write_json(root / 'fixture.json', fixture)
    print(json.dumps({'status': 'fixture_ready', 'base': fixture['base'],
                      'fixture_file': str(root / 'fixture.json')}, ensure_ascii=False), flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
