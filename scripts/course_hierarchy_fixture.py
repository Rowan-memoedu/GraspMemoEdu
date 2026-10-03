"""Loopback-only course grouping fixture; never opens production learning data."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.judge import ExistingJudge
from graspmemoedu.review import ReviewBackend
from graspmemoedu.service import LearningService
from tests.fixtures import synthetic_course

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--port', type=int, default=18849)
args = parser.parse_args()
root = args.output.resolve()
assert not root.exists() and root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve())
root.mkdir(parents=True)
topics = {}
for name in ('ready', 'paused', 'blocked', 'done'):
    topic = synthetic_course(1); topic['id'] = name; topic['title'] = '隔离验收 ' + name
    topics[name] = topic
    (root / (name + '.json')).write_text(json.dumps(topic), encoding='utf-8')
units = [
    {'id': 'a', 'title': 'A 综合章节', 'topic_ids': []},
    {'id': 'b', 'title': 'B 训练单元', 'parent_id': 'a', 'topic_ids': []},
    {'id': 'c', 'title': 'C 浅层分支', 'parent_id': 'b', 'topic_ids': ['ready', 'paused']},
    {'id': 'd', 'title': 'D 深层分支', 'parent_id': 'c', 'topic_ids': ['blocked']},
    {'id': 'done-only', 'title': '已完成分支', 'topic_ids': ['done']},
    {'id': 'empty', 'title': '空分支', 'topic_ids': []}]
(root / 'catalog.json').write_text(json.dumps({'courses': [{'id': 'hierarchy', 'title': '课程层级隔离验收',
    'topics': [{'id': name, 'path': name + '.json', 'prerequisites': ['ready'] if name == 'blocked' else []}
               for name in topics], 'units': units}]}), encoding='utf-8')
judge = ExistingJudge(Path('D:/Projects/MathAnswerJudge'))
app = create_app(Settings(data_dir=root, port=args.port), LearningService(topics['ready'], root, judge))
identities = {}
for label in ('advanced', 'standard'):
    account = app.state.access_store.create_account('隔离验收 ' + label, purpose='test')
    learner = account['account']['learner_id']
    app.state.access_store.set_permissions(learner, is_advanced_learner=label == 'advanced')
    identities[label] = {'learner_id': learner, 'invitation_code': account['invitation_code']}
    access = app.state.access_store.login(account['invitation_code'])['access']
    app.state.dashboard_store.select_course(access, 'hierarchy', 'math')
    learning = LearningService(topics['paused'], root, judge, learner_id=learner)
    state = learning.state()
    learning.pause({'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                    'attempt_id': state['attempt_id'], 'step_id': state['active_step_id']})
    # Seed an isolated completed branch with a due review. This is projection
    # fixture data, not evidence that a synthetic answer passed the real judge.
    learning = LearningService(topics['done'], root, judge, learner_id=learner)
    with learning._transaction() as con:
        state = learning._load(con)
        state['status'] = 'completed'; state['started_at'] = state['completed_at'] = datetime.now(timezone.utc).isoformat()
        learning._save(con, state)
    backend = ReviewBackend(learning)
    backend.record_module_result(module_id='module-1', mastery=1, result_id='fixture-enrollment',
                                 completed_at=datetime.now(timezone.utc) - timedelta(days=30), elapsed_ms=1000)
(root / 'identity.private.json').write_text(json.dumps(identities), encoding='utf-8')
print('READY isolated hierarchy fixture with real judge configured', flush=True)
uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
