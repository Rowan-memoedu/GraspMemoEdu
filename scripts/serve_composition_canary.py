"""Actual HTTP learning API for an explicitly isolated browser acceptance run."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import create_learning_service
from tests.test_topic_composition import composed_topic
from tests.training_fixtures import training_bank


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('data', type=Path); p.add_argument('port', type=int)
    args = p.parse_args(); data = args.data.resolve()
    if not data.is_relative_to(Path('D:/Artifacts').resolve()) or data.exists():
        raise ValueError('Use a new isolated D:/Artifacts directory')
    data.mkdir(parents=True); (data/'training-banks').mkdir()
    bank = training_bank(); topic = composed_topic(bank); topic['id'] = 'composition-ui'
    topic['introductions'][0]['html'] = '<p>原正文和题库作答共用一个 Topic。</p>'
    topic['introductions'][1]['html'] = '<p>完成例题后才能阅读这一段。</p>'
    for name, value in [('topic.json', topic), ('training-banks/isolated-bank.json', bank),
        ('catalog.json', {'courses': [{'id': 'composition-course', 'title': '共享题目验收', 'topics': [{'id': topic['id'], 'path': 'topic.json'}]}]}),
        ('subjects.json', {'subjects': [{'id': 'math', 'course_ids': ['composition-course']} ]})]:
        (data/name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    service = create_learning_service(topic, data)
    app = create_app(Settings(data_dir=data, port=args.port), service)
    from fastapi.responses import FileResponse, JSONResponse
    @app.get('/deployment.json')
    def deployment(): return JSONResponse({'api_origin': ''})
    @app.get('/{path:path}')
    def frontend(path: str):
        file = (ROOT/'web'/(path or 'index.html')).resolve()
        if not file.is_relative_to(ROOT/'web'): raise ValueError('Invalid web path')
        return FileResponse(file)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
