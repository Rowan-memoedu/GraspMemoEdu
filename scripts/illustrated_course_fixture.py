"""Install an approved illustrated course into fresh isolated real-judge QA data."""
import argparse
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.course_graph import import_graph
from scripts.install_author_course import install_course
from tests.fixtures import synthetic_course


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--graph', type=Path)
    parser.add_argument('--port', type=int, default=8779)
    args = parser.parse_args()
    root = args.output.resolve()
    allowed = Path('D:/Artifacts/GraspMemoEdu/illustrated-import-20260921/qa').resolve()
    if not args.output.is_absolute() or not root.is_relative_to(allowed) or root == allowed or root.exists():
        raise ValueError('Use a new absolute child of the illustrated-import QA directory')
    if args.port in (8765, 8767) or not 1024 <= args.port <= 65535:
        raise ValueError('Formal service ports forbidden')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
    data = root / 'data'
    data.mkdir(parents=True)
    math = synthetic_course(1)
    math_path = data / 'math.json'
    write(math_path, math)
    write(data / 'catalog.json', {'courses': [{'id': 'qa-math', 'title': '隔离数学',
          'topics': [{'id': math['id'], 'path': str(math_path)}]}]})
    write(data / 'subjects.json', {'subjects': [{'id': 'math', 'course_ids': ['qa-math']},
                                             {'id': plan['subject_id'], 'course_ids': []}]})
    write(data / 'settings.json', {'course_path': str(math_path), 'port': args.port})
    settings = Settings.load(data)
    if args.graph:
        import_graph(settings, args.graph, apply=True)
    install_course(settings, plan)
    app = create_app(settings)
    account = app.state.access_store.create_account('配图课程隔离验收', purpose='test')
    write(root / 'account.private.json', {'accounts': [{**account['account'], 'invitation_code': account['invitation_code']}]})
    write(root / 'fixture.json', {'base': f'http://127.0.0.1:{args.port}', 'data_dir': str(data),
          'plan': str(args.plan.resolve()), 'credentials_file': str(root / 'account.private.json'),
          'real_judge': True, 'formal_data_opened': False})
    print(json.dumps({'status': 'fixture_ready', 'fixture': str(root / 'fixture.json')}), flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
