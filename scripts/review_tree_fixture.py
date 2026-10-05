"""Disposable real-clock fixture for shared trees and timed review acceptance."""
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
    parser.add_argument('--port', type=int, default=18859)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or args.port in (8765, 18768):
        raise ValueError('Use a new isolated artifact directory and port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    root.mkdir(parents=True)
    data = root / 'data'; (data / 'training-banks').mkdir(parents=True)
    bank = training_bank(); base = bank['groups'][1]['questions']; questions = []
    for index in range(16):
        q = deepcopy(base[3]); q.update(id=f'choice-{index}', title=f'单选 {index + 1}'); questions.append(q)
    essay = deepcopy(base[0]); essay.update(id='semantic', title='真实语义判题'); questions.append(essay)
    bank.update(id='tree-bank-a', title='隔离题库甲', groups=[
        {'id': 'chapter', 'title': '第一章', 'questions': []},
        {'id': 'section', 'title': '第一节', 'parent_id': 'chapter', 'questions': questions}])
    banks = [bank]
    other = deepcopy(bank); other.update(id='tree-bank-b', title='隔离题库乙')
    other['groups'][1]['questions'] = other['groups'][1]['questions'][:1]
    banks.append(other)
    for item in banks:
        item['version'] = fingerprint(item); validate(item)
        (data / 'training-banks' / (item['id'] + '.json')).write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
    course = synthetic_course(1); course_path = data / 'topic.json'; course_path.write_text(json.dumps(course), encoding='utf-8')
    (data / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'tree-course', 'title': '隔离课程',
        'units': [{'id': 'chapter', 'title': '第一章', 'topic_ids': [course['id']]}],
        'topics': [{'id': course['id'], 'path': 'topic.json'}]}]}), encoding='utf-8')
    offset = [timedelta(0)]
    clock = lambda: datetime.now(timezone.utc) + offset[0]
    judge = ExistingJudge(Path('D:/Projects/MathAnswerJudge'))
    app = create_app(Settings(data_dir=data, course_path=course_path, port=args.port),
        LearningService(course, data, judge), atomic_clock=clock)
    app.state.review_paper_types = [{'id': 'timed-choice', 'title': '隔离限时卷', 'subject': 'math',
        'enabled': True, 'requirements': {'choice': 8}, 'time_limit_minutes': 1}]
    accounts = {}
    for name, advanced in [('standard', False), ('advanced', True)]:
        created = app.state.access_store.create_account('树形限时隔离 ' + name, purpose='test', is_advanced_learner=advanced)
        accounts[name] = created['invitation_code']
        backend = TrainingService(app.state.training_catalog, data, created['account']['learner_id'], judge, clock=clock)
        for item in banks:
            for q in item['groups'][1]['questions']:
                attempt = backend.open(item['id'], 'section', q['id'], str(uuid.uuid4()))
                sid = backend.submit({'request_id': str(uuid.uuid4()), 'attempt_id': attempt,
                                      'answer': '九' if q['id'] == 'semantic' else '{"selected":"b"}'})
                backend.process_submission(sid)
                if backend.state(attempt)['result']['rating'] != 1:
                    raise RuntimeError('Fixture setup did not produce an initial wrong answer')
        print('READY isolated account ' + name, flush=True)
    with app.state.access_store._transaction() as con:
        admin = app.state.access_store._insert_account(con, '树形隔离管理员', is_admin=True)
        accounts['admin'] = admin['invitation_code']
    offset[0] += timedelta(days=2)
    (root / 'identities.json').write_text(json.dumps(accounts), encoding='utf-8')
    print('READY isolated shared-tree timed fixture', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
