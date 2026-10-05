"""Isolated synthetic paper fixture, with the existing real semantic judge."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.judge import ExistingJudge
from graspmemoedu.service import LearningService
from graspmemoedu.training import TrainingService
from graspmemoedu.training_content import fingerprint, validate
from tests.fixtures import synthetic_course
from tests.training_fixtures import training_bank


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18855)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or args.port in (8765, 18768):
        raise ValueError('Use a fresh isolated artifact directory and port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    root.mkdir(parents=True)
    data = root / 'data'; (data / 'training-banks').mkdir(parents=True)
    bank = training_bank(); base = bank['groups'][1]['questions']; questions = []
    for index in range(8):
        q = deepcopy(base[3]); q.update(id=f'choice-{index}', title=f'单选 {index + 1}'); questions.append(q)
    for index in range(3):
        q = deepcopy(base[3]); q.update(id=f'multiple-{index}', title=f'多选 {index + 1}',
            question='选出等于二的表达式。', html='<p>选出等于二的表达式。</p>')
        q['interaction'] = {'version': 1, 'type': 'choice', 'multiple': True,
            'options': [{'id': 'a', 'text': '1+1'}, {'id': 'b', 'text': '4÷2'}, {'id': 'c', 'text': '3'}], 'correct_ids': ['a', 'b']}
        questions.append(q)
    for index in range(3):
        q = deepcopy(base[4]); q.update(id=f'blank-{index}', title=f'填空 {index + 1}'); questions.append(q)
    essay = deepcopy(base[0]); essay.update(id='semantic', title='真实语义判题'); questions.append(essay)
    bank['groups'] = [{'id': 'review-group', 'title': '隔离复习题组', 'questions': questions}]
    bank['version'] = fingerprint(bank); validate(bank)
    (data / 'training-banks/isolated-bank.json').write_text(json.dumps(bank, ensure_ascii=False), encoding='utf-8')
    course = synthetic_course(1); course_path = data / 'topic.json'; course_path.write_text(json.dumps(course), encoding='utf-8')
    (data / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'legacy-course', 'title': '隔离基线',
        'units': [{'id': 'chapter', 'title': '基线', 'topic_ids': [course['id']]}],
        'topics': [{'id': course['id'], 'path': 'topic.json'}]}]}), encoding='utf-8')
    when = [datetime.now(timezone.utc)]
    judge = ExistingJudge(Path('D:/Projects/MathAnswerJudge'))
    app = create_app(Settings(data_dir=data, course_path=course_path, port=args.port),
        LearningService(course, data, judge), atomic_clock=lambda: when[0])
    store = app.state.access_store; accounts = {}
    for name, advanced in [('standard', False), ('advanced', True)]:
        created = store.create_account('组卷隔离 ' + name, purpose='test', is_advanced_learner=advanced, is_student=True)
        accounts[name] = created['invitation_code']
        backend = TrainingService(app.state.training_catalog, data, created['account']['learner_id'], judge, clock=lambda: when[0])
        for q in questions:
            attempt = backend.open(bank['id'], 'review-group', q['id'], str(uuid.uuid4()))
            kind = q.get('interaction', {}).get('type', 'text')
            wrong = ('{"selected":["c"]}' if q.get('interaction', {}).get('multiple') else '{"selected":"b"}') if kind == 'choice' else '{"a":"0"}' if kind == 'fill_blank' else '九'
            sid = backend.submit({'request_id': str(uuid.uuid4()), 'attempt_id': attempt, 'answer': wrong})
            backend.process_submission(sid)
            if backend.state(attempt)['result'] is None or backend.state(attempt)['result']['rating'] != 1:
                raise RuntimeError('Isolated initial-answer setup failed')
    when[0] += timedelta(days=2)
    (root / 'identities.json').write_text(json.dumps(accounts), encoding='utf-8')
    print('READY isolated paper fixture', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
