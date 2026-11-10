"""Validate changed mutant-production rules on prior controls before admission."""
import argparse,json
from pathlib import Path
from core import read,save,parse_output,compile_run
from regression import materialize
ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);a=ap.parse_args();root=a.root
inputs={str(x['id']):json.loads(x['input']) for x in (json.loads(s) for s in (root/'production'/'mutant-canary-retry-input.jsonl').read_text(encoding='utf-8').splitlines())}
raws=[json.loads(s) for s in (root/'production'/'mutant-canary-retry-output.jsonl').read_text(encoding='utf-8').splitlines()]
outputs={str(r['id']):parse_output(r) for r in raws};controls=read(root/'regression'/'controls.json');tests={}
for p in sorted((root/'regression').glob('output*.jsonl')):
    for line in p.read_text(encoding='utf-8').splitlines():
        try:
            b=parse_output(json.loads(line));tests[str(b['id'])]=b
        except Exception:pass
checks=[];jdk=read('/mnt/d/Caches/HyperskillCodeTrial/runtime/jdk25.json')['java_home']
for i,item in inputs.items():
    c=outputs[i];same_reference=c.get('official_code')==item['frozen_reference'];different=c.get('mutant_code')!=item['frozen_reference'] and bool(c.get('mutant_code'))
    row={'id':i,'reference_preserved':same_reference,'mutant_distinct':bool(different)}
    if i in controls:
        b=materialize(tests[i],controls[i]['question']);good=compile_run(c['official_code'],b,jdk);bad=compile_run(c['mutant_code'],b,jdk)
        row.update({'correct':good,'mutant':bad,'passed':same_reference and different and good['verdict']=='accepted' and bad['verdict'] in ('wrong_answer','runtime_error')})
    else:row['passed']=same_reference and bool(different)
    checks.append(row);print(json.dumps({k:v for k,v in row.items() if k not in ('correct','mutant')}),flush=True)
result={'passed':all(r['passed'] for r in checks),'checks':checks};save(root/'regression'/'mutant-canary-validation.json',result)
if not result['passed']:raise SystemExit(1)
out=root/'production'/'references-output-zz-mutants.jsonl';assert not out.exists()
out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in raws if str(r['id']) not in controls),encoding='utf-8')
