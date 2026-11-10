import json
from pathlib import Path
from core import read,derive_fixture_sources
root=Path(r'D:\Artifacts\GraspMemoEdu\code-100-independent-20261101\regression');controls=read(root/'controls.json');rows=[]
for i in ('16536','-903'):
 q=controls[i]['question'];source={k:q[k] for k in ('id','title','question','template','existing_samples')};source['fixture_sources']=derive_fixture_sources(q);rows.append({'id':i,'input':json.dumps(source,ensure_ascii=False)})
p=root/'state-input.jsonl';assert not p.exists();p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
