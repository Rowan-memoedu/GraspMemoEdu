import argparse,json
from pathlib import Path
from core import read,save,parse_output,compile_run
from regression import materialize
ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--production-file',default='capability-review-output.jsonl');ap.add_argument('--overlay-production-file');ap.add_argument('--validation-file',default='capability-canary-validation.json');a=ap.parse_args();root=a.root
controls=read(root/'regression'/'capability-controls.json');raws=[json.loads(s) for s in (root/'production'/a.production_file).read_text(encoding='utf-8').splitlines()];outputs={str(r['id']):parse_output(r) for r in raws}
if a.overlay_production_file:
    overlays=[json.loads(s) for s in (root/'production'/a.overlay_production_file).read_text(encoding='utf-8').splitlines()];raws+=overlays
    outputs.update({str(r['id']):parse_output(r) for r in overlays})
jdk=read('/mnt/d/Caches/HyperskillCodeTrial/runtime/jdk25.json')['java_home'];results=[]
for i,c in controls.items():
    b=materialize(outputs[i],c['question']);good=compile_run(c['good'],b,jdk);bad=compile_run(c['bad'],b,jdk)
    r={'id':i,'correct':good,'mutant':bad,'passed':good['verdict']=='accepted' and bad['verdict'] in ('wrong_answer','runtime_error')};results.append(r);print(json.dumps({'id':i,'passed':r['passed'],'good':good['verdict'],'bad':bad['verdict']}),flush=True)
result={'passed':all(r['passed'] for r in results),'checks':results};save(root/'regression'/a.validation_file,result)
if not result['passed']:raise SystemExit(1)
out=root/'production'/'tests-reviewed-zz-capability.jsonl';assert not out.exists();out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in raws if str(r['id']) not in controls),encoding='utf-8')
