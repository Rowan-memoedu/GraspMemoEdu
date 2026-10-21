"""Isolated mixed-graph fixture for the actual SPA; never loads production data."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys
from uuid import uuid4

repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo), str(repo / 'tests')]
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.course_graph import import_graph
from graspmemoedu.service import LearningService
from graspmemoedu.training_content import fingerprint
from tests.fixtures import DeterministicJudge
from tests.subject_matrix_fixture import write_matrix
from tests.test_group_dependencies import mixed_graph


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18874)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or args.port in (8765, 18768):
        raise ValueError('Use a new isolated artifact directory and port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    topics = write_matrix(root / 'data')
    for path in (root / 'data/training-banks').glob('*.json'):
        bank = json.loads(path.read_bytes())
        base = bank['groups'][1]['questions'][1]
        for i in range(14):
            question = deepcopy(base); question['id'] = f'earlier-{i}'
            bank['groups'].append({'id': f'earlier-{i}', 'title': f'Earlier group {i}', 'parent_id': None, 'questions': [question]})
        bank['groups'].extend([
            {'id': 'tail', 'title': 'Final chapter', 'parent_id': None, 'questions': []},
            {'id': 'branch', 'title': 'Nested section', 'parent_id': 'tail', 'questions': []},
            {'id': 'group-ready', 'title': 'Ready group', 'parent_id': 'branch',
             'questions': [{**deepcopy(base), 'id': 'first'}, {**deepcopy(base), 'id': 'last'}]},
        ])
        bank['version'] = fingerprint(bank)
        path.write_text(json.dumps(bank), encoding='utf-8')
        graph = root / (bank['subject_id'] + '-graph.json')
        graph.write_text(json.dumps(mixed_graph(bank['subject_id'])), encoding='utf-8')
        import_graph(Settings(data_dir=root / 'data'), graph, apply=True)
    app = create_app(Settings(data_dir=root / 'data', port=args.port),
                     LearningService(topics['lesson-math'], root / 'data', DeterministicJudge()))
    account = app.state.access_store.create_account('Mixed dependency fixture', is_student=True, purpose='test')
    login = app.state.access_store.login(account['invitation_code'])
    access = app.state.access_store.resolve(login['identity_token'])
    backend = app.state.training_backend_for(access)
    for bank in backend.catalog.banks.values():
        attempt = backend.open(bank['id'], 'group-ready', 'first', str(uuid4()))
        job = backend.submit({'attempt_id': attempt, 'request_id': str(uuid4()), 'answer': '2'})
        backend.process_submission(job)
        assert backend.state(attempt)['phase'] == 'done'
    (root / 'accounts.private.json').write_text(json.dumps([account]), encoding='utf-8')
    print('READY mixed dependency fixture; synthetic content and deterministic grading', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
