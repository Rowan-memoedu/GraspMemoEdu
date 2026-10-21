"""Serve synthetic selection trees for browser acceptance, with no live data."""
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
from tests.catalog_scope_fixture import write_scopes
from tests.fixtures import DeterministicJudge


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18876)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or args.port in (8765, 18768):
        raise ValueError('Use a fresh isolated artifact path and port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    topics = write_scopes(root / 'data')
    app = create_app(Settings(data_dir=root / 'data', port=args.port),
                     LearningService(topics['lesson-math'], root / 'data', DeterministicJudge()))
    account = app.state.access_store.create_account('Catalog scope fixture', is_student=True, purpose='test')
    app.state.access_store.set_permissions(account['account']['learner_id'], is_advanced_learner=True)
    (root / 'accounts.private.json').write_text(json.dumps([account]), encoding='utf-8')
    print('READY synthetic catalog scope fixture', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
