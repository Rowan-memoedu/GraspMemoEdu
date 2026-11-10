"""Prepare bounded technical repairs without exposing reference or verdicts."""
import argparse,json,re
from pathlib import Path
from core import read,digest
from workflow import jsonl
ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
root=a.root
assert not(root/'evaluation-freeze.json').exists(),'No test repair after independent evaluation freeze'
sources={}
for p in (root/'production').glob('tests-input-*.jsonl'):
    for line in p.read_text(encoding='utf-8').splitlines():
        r=json.loads(line);sources[str(r['id'])]=r
bundles=read(root/'test-packages.json');checks=read(root/'compile-preflight.json',{'entries':{}})['entries'];issues={}
for r in checks.values():
    i=str(r['step_id'])
    stderr=r.get('compile',{}).get('stderr','');source=json.loads(sources[i]['input'])
    missing_types=re.findall(r'symbol:\s+class\s+(\w+)',stderr)
    missing_fixture=r['verdict']=='compile_error' and any(re.search(r'\b'+re.escape(name)+r'\b',source['template']) for name in missing_types)
    if r.get('test_sha256')==digest(bundles[i]) and (r['verdict']=='test_error' or missing_fixture):
        # Preserve compiler messages but never leak candidate source lines to test production.
        diagnostics='\n'.join(line for line in stderr.splitlines() if re.search(r'^\S+\.java:\d+: error:|^\s*(symbol:|location:)|^\d+ errors?$',line))
        errors=r.get('errors',[]) or [diagnostics]
        issues.setdefault(i,set()).update(errors)
for i,errors in read(root/'production-validation.json',{}).get('invalid_tests',{}).items():
    if bundles[i].get('status')=='ready':issues.setdefault(i,set()).update(errors)
jobs=[]
for i,errors in issues.items():
    s=sources[i]
    # Original public examples are prepended by materialization, not producer output.
    draft={**bundles[i],'cases':[c for c in bundles[i].get('cases',[]) if c.get('purpose')!='original_public_sample']}
    jobs.append({**s,'input':json.dumps({'original_exercise':json.loads(s['input']),'draft_test_bundle':draft,'technical_diagnostics':sorted(errors),'repair_scope':'Fix invocation, fixture, type, syntax and assertion wiring from the original contract. No reference code, program output or official outcome was supplied.'},ensure_ascii=False)})
jsonl(a.output,jobs);print(json.dumps({'technical_review_jobs':len(jobs),'ids':[r['id'] for r in jobs]}))
