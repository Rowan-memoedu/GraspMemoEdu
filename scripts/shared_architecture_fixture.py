"""Local-only seven-subject fixture. No production content or external judging."""
import argparse
import json
from pathlib import Path
import socket
import sys

repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo), str(repo / 'tests')]
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from tests.fixtures import DeterministicJudge
from tests.subject_matrix_fixture import write_matrix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18871)
    args = parser.parse_args()
    root = args.output.resolve()
    if not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or root.exists() or args.port in (8765, 18768):
        raise ValueError('Use a new isolated artifact directory and port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    topics = write_matrix(root / 'data')
    app = create_app(Settings(data_dir=root / 'data', port=args.port),
        LearningService(topics['lesson-math'], root / 'data', DeterministicJudge()))
    account = app.state.access_store.create_account('Architecture fixture', is_student=True, purpose='test')
    (root / 'account.private.json').write_text(json.dumps(account), encoding='utf-8')
    print('READY shared architecture fixture; synthetic content only', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
