import json
from pathlib import Path
from core import read,save
root=Path(r'D:\Artifacts\GraspMemoEdu\code-100-independent-20261101\regression');base=Path(r'D:\Artifacts\GraspMemoEdu\code-100-trial-20261101');controls=read(root/'controls.json')
q=next(q for q in read(base/'trial-input.json')['questions'] if q['id']==12811);c=read(base/'candidate-packages.json')['12811'];controls['12811']={'question':q,'good':c['official_code'],'bad':c['mutant_code'],'bad_must_fail':True}
q={'id':-911,'title':'Source retained method marker','question':'Define annotation Tag with a String value parameter and annotate Handler.process with it. Any retention policy is allowed. Do not change the method signature.','template':'@interface Tag {/*fill*/}\nclass Handler {void process(){}}','existing_samples':[]}
controls['-911']={'question':q,'good':'@java.lang.annotation.Retention(java.lang.annotation.RetentionPolicy.SOURCE) @interface Tag {String value();} class Handler {@Tag("alpha") void process(){}}','bad':'@java.lang.annotation.Retention(java.lang.annotation.RetentionPolicy.SOURCE) @interface Tag {String value();} class Handler {void process(){}}','bad_must_fail':True};save(root/'controls.json',controls)
rows=[{'id':i,'input':json.dumps({k:controls[i]['question'][k] for k in ('id','title','question','template','existing_samples')},ensure_ascii=False)} for i in ('12811','-911')]
p=root/'annotation-input.jsonl';assert not p.exists();p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
