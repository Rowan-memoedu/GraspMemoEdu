"""Compile synthetic author inputs and run the v2 website in an isolated directory."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu.author_import import prepare_topic
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import create_learning_service
from graspmemoedu.topic_format import dump_json
from tests.fixtures import DeterministicJudge
from tests.topic_format_fixtures import introduction_document, lesson_document, node


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--port', type=int, default=18974)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    intro, lesson = introduction_document(), lesson_document()
    intro['source_rem_id'] = 'LaterDocument'
    def distinct(nodes):
        for item in nodes:
            if 'source_rem_id' in item:
                item['source_rem_id'] = 'Target' + item['source_rem_id']
            distinct(item['children'])
    distinct(intro['nodes'])
    additional_callouts = [
        node('aside-canary', '**旁注**：保留层级。', callout='aside'),
        node('requirement-canary', '**要求**：先阅读条件。', callout='requirement'),
    ]
    present = {item.get('callout') for item in intro['nodes'][0]['children']}
    intro['nodes'][0]['children'].extend(item for item in additional_callouts if item['callout'] not in present)
    entries, compiled = [], {}
    for document in (intro, lesson):
        source = output / (document['id'] + '.json')
        source.write_text(dump_json(document), encoding='utf-8')
        package = output / (document['id'] + '-package')
        prepare_topic(source, document['id'], package)
        topic = json.loads((package / 'topic.json').read_text(encoding='utf-8'))
        compiled[topic['id']] = topic
        entries.append({'id':topic['id'], 'path':str((package / 'topic.json').relative_to(output))})
        print('Compiled ' + topic['id'], flush=True)
    (output / 'catalog.json').write_text(json.dumps({'courses':[{'id':'canary-course','title':'Synthetic course','topics':entries}]}), encoding='utf-8')
    settings = Settings(data_dir=output, port=args.port)
    template = create_learning_service(compiled[intro['id']], output, DeterministicJudge())
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(create_app(settings, template), host='127.0.0.1', port=args.port, log_level='warning'))
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            if not worker.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Canary server did not start')
            time.sleep(.1)
        result = subprocess.run(['node', str(ROOT / 'scripts/topic_runtime_readback.mjs'),
                                 f'http://127.0.0.1:{args.port}', str(output)], cwd=ROOT)
        if result.returncode:
            raise RuntimeError('Frontend readback failed')
    finally:
        server.should_exit = True
        worker.join(20)


if __name__ == '__main__':
    main()
