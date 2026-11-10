"""Regenerate only missing/equal negative candidates, preserving the reference."""
import argparse,json
from pathlib import Path
from core import read
from workflow import jsonl
ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
assert not(a.root/'evaluation-freeze.json').exists()
refs=read(a.root/'candidate-packages.json');missing=read(a.root/'production-validation.json')['missing_mutants'];sources={}
for p in (a.root/'production').glob('references-input-*.jsonl'):
    for line in p.read_text(encoding='utf-8').splitlines():
        r=json.loads(line);sources[str(r['id'])]=r
jobs=[]
for i in missing:
    if i not in refs:continue
    s=sources[i];jobs.append({**s,'input':json.dumps({'original_exercise':json.loads(s['input']),'frozen_reference':refs[i]['official_code']},ensure_ascii=False)})
jsonl(a.output,jobs);print(json.dumps({'mutant_retry_jobs':len(jobs),'ids':[r['id'] for r in jobs]}))
