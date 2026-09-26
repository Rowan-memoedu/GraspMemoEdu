"""Isolated administrator browser fixture; never installs production content."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from graspmemoedu.judge import ExistingJudge
from graspmemoedu.account_admin import AccountAdmin
from tests.fixtures import synthetic_course
from tests.atomic_fixtures import atomic_topic

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = args.output.resolve()
assert not root.exists() and root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve())
root.mkdir(parents=True)
topics = {}
for name in ('before', 'after'):
    topic = synthetic_course(1); topic['id'] = name
    for module in topic['modules']:
        for question in [module['example'], *module['practices']]:
            question['reference_answer'] = '5'
    topics[name] = topic
topics['atoms'] = atomic_topic('english', 'atoms')
topics['atoms']['flashcards'][4]['interaction'] = {'version': 1, 'type': 'text', 'grading': 'semantic'}
for name, topic in topics.items():
    (root / (name + '.json')).write_text(json.dumps(topic, ensure_ascii=False), encoding='utf-8')
(root / 'catalog.json').write_text(json.dumps({'courses': [
    {'id': 'math-course', 'title': '管理员隔离验收', 'topics': [
        {'id': 'before', 'path': 'before.json'}, {'id': 'after', 'path': 'after.json', 'prerequisites': ['before']}]},
    {'id': 'english-course', 'title': '原子隔离验收', 'topics': [{'id': 'atoms', 'path': 'atoms.json'}]}]}), encoding='utf-8')
(root / 'subjects.json').write_text(json.dumps({'subjects': [
    {'id': 'math', 'course_ids': ['math-course']}, {'id': 'english', 'course_ids': ['english-course']}]}), encoding='utf-8')
app = create_app(Settings(data_dir=root, port=18844),
                 LearningService(topics['before'], root, ExistingJudge(Path('D:/Projects/MathAnswerJudge'))))
AccountAdmin(app.state.access_store).provision('synthetic-admins', learners=0, tests=0, admins=3, output=root / 'admin/invitations.json')
print('READY isolated administrator fixture with real judge', flush=True)
uvicorn.run(app, host='127.0.0.1', port=18844, log_level='warning')
