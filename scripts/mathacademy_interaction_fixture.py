"""Synthetic complete 10-section engine fixture, separate from real incomplete sources."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import create_learning_service
from tests.fixtures import DeterministicJudge, synthetic_course
from tests.test_mathacademy_import import composite

p = argparse.ArgumentParser(); p.add_argument('--output', required=True, type=Path); p.add_argument('--port', type=int, default=18872)
a = p.parse_args(); root = a.output.resolve()
if root.exists() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()):
    raise ValueError('Fixture needs a new isolated artifact directory')
root.mkdir(parents=True)
topic = synthetic_course(1); topic['id'] = 'synthetic-composite'
topic.update(topic_format_version=2, topic_kind='lesson', content_version='synthetic-complete-1',
    introductions=[{'id':'intro', 'title':'Synthetic guidance', 'html':'<p>Synthetic complete input fixture.</p>'},
                   {'id':'tail', 'title':'Synthetic final proof', 'html':'<p>Read this final proof before completing the Topic.</p>'}],
    content_order=[{'kind':'introduction','id':'intro'}, {'kind':'example','id':'module-1'}, {'kind':'introduction','id':'tail'}])
spec = composite()
for section in spec['sections']:
    section['html'] = '<p>Choose Yes for each synthetic statement.</p>' + ''.join('<span data-control="'+cid+'"></span>' for cid in section['control_ids'])
topic['modules'][0]['example']['interaction'] = spec
(root / 'topic.json').write_text(json.dumps(topic), encoding='utf-8')
(root / 'catalog.json').write_text(json.dumps({'courses':[{'id':'synthetic-composite-course', 'title':'Synthetic composite course',
    'topics':[{'id':topic['id'],'path':'topic.json'}]}]}), encoding='utf-8')
(root / 'test-materials.json').write_text(json.dumps({'schema':1,'courses':['synthetic-composite-course']}),encoding='utf-8')
service = create_learning_service(topic, root, DeterministicJudge())
app = create_app(Settings(data_dir=root, port=a.port), service)
account = app.state.access_store.create_account('Synthetic qualified test student', purpose='test', is_student=True, is_advanced_learner=True)
(root / 'identity.private.json').write_text(json.dumps({'invitation_code': account['invitation_code'], 'learner_id':account['account']['learner_id']}), encoding='utf-8')
print('READY isolated complete composite fixture', flush=True)
uvicorn.run(app, host='127.0.0.1', port=a.port, log_level='warning')
