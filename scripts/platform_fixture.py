"""Isolated synthetic multi-subject website, no formal data or external judge."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from math_learning.api import create_app
from math_learning.config import Settings
from math_learning.service import LearningService
from review_web_fixture import make_course


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8779)
    args = parser.parse_args()
    root = args.output.resolve()
    allowed = Path('D:/Artifacts/MathLearningWeb/platform').resolve()
    if not args.output.is_absolute() or not root.is_relative_to(allowed) or root.exists():
        raise ValueError('Use a new absolute directory below D:/Artifacts/MathLearningWeb/platform')
    if args.port in (8765, 8767):
        raise ValueError('Formal service ports are forbidden')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    data = root / 'data'
    data.mkdir(parents=True)
    course = make_course()
    course.update(id='digits-to-letters', title='平台隔离数学 Topic')
    course['version'] = hashlib.sha256(json.dumps(course, sort_keys=True).encode()).hexdigest()
    source = data / 'course.json'
    source.write_text(json.dumps(course, ensure_ascii=False), encoding='utf-8')
    guide = data / 'guide' / 'guide.md'
    guide.parent.mkdir()
    guide.write_text('# 数学指南\n\n## 学习流程\n\nMATH_GUIDE_SENTINEL\n', encoding='utf-8')
    def judge(question, reference, answer):
        return {'status': 'judged', 'correct': answer.strip() == reference,
                'reason': 'Synthetic platform fixture oracle'}
    app = create_app(Settings(data_dir=data, course_path=source, guide_path=guide, port=args.port),
                     LearningService(course, data, judge))
    test_account = app.state.access_store.create_account('平台隔离测试账号', purpose='test')
    (root / 'account.private.json').write_text(json.dumps(test_account, ensure_ascii=False), encoding='utf-8')
    (root / 'fixture.json').write_text(json.dumps({'base': f'http://127.0.0.1:{args.port}',
        'topic_id': course['id'], 'real_judge': False, 'formal_data_opened': False}), encoding='utf-8')
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
