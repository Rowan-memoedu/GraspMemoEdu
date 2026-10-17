"""Serve an already installed isolated batch for browser acceptance."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
p=argparse.ArgumentParser(); p.add_argument('data',type=Path); p.add_argument('--port',type=int,default=18876)
a=p.parse_args(); root=a.data.resolve()
if not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()): raise ValueError('Isolated artifacts only')
app=create_app(Settings(data_dir=root,port=a.port))
identity=root/'reading-identity.private.json'
if not identity.exists():
    account=app.state.access_store.create_account('Source reading acceptance',purpose='test',is_student=True,is_advanced_learner=True)
    identity.write_text(json.dumps({'invitation_code':account['invitation_code']}),encoding='utf-8')
print('READY isolated source reader',flush=True)
uvicorn.run(app,host='127.0.0.1',port=a.port,log_level='warning')
