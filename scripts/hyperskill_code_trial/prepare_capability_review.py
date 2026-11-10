"""Recheck unsupported capability claims using source-only inputs and controls."""
import argparse,json
from pathlib import Path
from core import read,save,derive_fixture_sources
from workflow import jsonl
ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);a=ap.parse_args();root=a.root
assert not(root/'evaluation-freeze.json').exists()
old=read(root/'regression'/'controls.json');controls={i:old[i] for i in ('-903','-910')}
q={'id':-914,'title':'Observable distinct words','group':'independent pipeline control','question':'Read a positive integer n followed by n non-null case-sensitive words. Implement distinct(words), returning the number of distinct words. The driver computes internal object labels but prints only the returned distinct count.','template':'import java.util.*; class Main { static int distinct(List<String> words) { /* implement */ return 0; } public static void main(String[] args) {Scanner s=new Scanner(System.in);int n=s.nextInt();List<String> words=new ArrayList<>();Map<String,Integer> internalLabels=new HashMap<>();for(int i=0;i<n;i++){String w=s.next();words.add(w);internalLabels.computeIfAbsent(w,k->System.identityHashCode(new Object()));}System.out.println(distinct(words));}}','language':'java25','existing_samples':[['3 red blue red','2']],'fixture_sources':[]}
good=q['template'].replace('/* implement */ return 0;','return new HashSet<>(words).size();');bad=q['template'].replace('/* implement */ return 0;','return words.size();')
controls['-914']={'question':q,'good':good,'bad':bad,'bad_must_fail':True};save(root/'regression'/'capability-controls.json',controls)
jobs=[]
for i,c in controls.items():
    source={**c['question'],'fixture_sources':derive_fixture_sources(c['question'])}
    jobs.append({'id':i,'input':json.dumps({'original_exercise':source,'draft_test_bundle':{'id':int(i),'status':'unsupported','reason':'Capability recheck requested; generate from the original contract.'}},ensure_ascii=False)})
bundles=read(root/'test-packages.json');sources={}
for p in (root/'production').glob('tests-input-*.jsonl'):
    for line in p.read_text(encoding='utf-8').splitlines():
        r=json.loads(line);sources[str(r['id'])]=r
for i,b in bundles.items():
    if b.get('status')=='unsupported':
        s=sources[i];jobs.append({**s,'input':json.dumps({'original_exercise':json.loads(s['input']),'draft_test_bundle':b},ensure_ascii=False)})
jsonl(root/'production'/'capability-review-input.jsonl',jobs);print(json.dumps({'jobs':len(jobs),'ids':[r['id'] for r in jobs]}))
