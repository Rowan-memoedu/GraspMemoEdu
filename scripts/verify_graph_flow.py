"""Re-run a graph source through CLI import, HTTP, shared UI and update recovery.

Uses a private disposable data directory, never the production learning database.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.course_graph import read_graph, revision
from graspmemoedu.service import LearningService
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


def run(source, output):
    graph = read_graph(source)
    output.mkdir(parents=True, exist_ok=True)
    report = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'nodes': len(graph['nodes']), 'edges': len(graph['edges']), 'formal_data_opened': False,
              'http_mocked': False, 'checks': []}
    with temporary_test_directory() as temporary:
        data = Path(temporary)
        topic = synthetic_course(1)
        topic['id'] = 'graph-flow-fixture-topic'
        (data / 'topic.json').write_text(json.dumps(topic), encoding='utf-8')
        (data / 'catalog.json').write_text(json.dumps({'courses': [{
            'id': 'graph-flow-fixture-course', 'title': 'Isolated fixture',
            'topics': [{'id': topic['id'], 'path': 'topic.json'}]}]}), encoding='utf-8')
        before = {name: (data / name).read_bytes() for name in ('catalog.json', 'topic.json')}
        def command(path, *args):
            result = subprocess.run([sys.executable, '-m', 'graspmemoedu', '--data-dir', str(data),
                                     'graph-import', '--source', str(path), *args],
                                    cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
            if result.returncode:
                raise AssertionError(result.stderr)
            return json.loads(result.stdout)
        assert command(source)['status'] == 'preview'
        assert not (data / 'graphs').exists()
        first = command(source, '--apply', '--preview-html', str(output / 'preview.html'))
        assert first['status'] == 'installed' and len(first['pending_topics']) == len(graph['nodes'])
        assert revision(read_graph(first['path'])) == revision(graph)
        assert command(source, '--apply')['status'] == 'unchanged'
        report['checks'].append('Source → CLI preview → independent import → readback → idempotent reimport')
        update = copy.deepcopy(graph)
        update['title'] += ' [update canary]'
        removed = update['edges'].pop() if update['edges'] else None
        candidate = data / 'update-source.json'
        candidate.write_text(json.dumps(update), encoding='utf-8')
        result = command(candidate, '--apply')
        if removed:
            assert result['diff']['edges']['removed'] == [removed]
        assert read_graph(first['path']) == read_graph(candidate)
        assert command(result['backup'], '--apply')['revision'] == first['revision']
        for name, raw in before.items():
            assert (data / name).read_bytes() == raw
        assert not (data / 'learning.sqlite3').exists()
        report['checks'].append('Full replacement removes edges; history restores source; content and progress untouched')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        assert port not in (8765, 8767)
        settings = Settings(data_dir=data, port=port)
        service = LearningService(topic, data, DeterministicJudge())
        app = create_app(settings, service)
        account = app.state.access_store.create_account('Graph acceptance', purpose='test')
        credentials = data / 'fixture-auth.json'
        credentials.write_text(json.dumps({'invitation_code': account['invitation_code']}), encoding='utf-8')
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started, 'Isolated HTTP server failed to start'
            child = subprocess.run(['node', str(ROOT / 'scripts/verify_graph_ui.mjs'),
                f'http://127.0.0.1:{port}', str(output), str(credentials), graph['subject_id'], graph['course_id']],
                cwd=ROOT, text=True, encoding='utf-8', capture_output=True, timeout=120)
            if child.returncode:
                raise AssertionError(child.stderr + '\n' + child.stdout)
            report['browser'] = json.loads((output / 'browser.json').read_text(encoding='utf-8'))
            report['checks'].append('Real HTTP + isolated Chrome: graph-only course, rendering, search, zoom, upstream selection, mobile')
        finally:
            server.should_exit = True
            worker.join(15)
            assert not worker.is_alive(), 'Fixture server did not stop'
    report['status'] = 'passed'
    (output / 'flow.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.output.resolve())
