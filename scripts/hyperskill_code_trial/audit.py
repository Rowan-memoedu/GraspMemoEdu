"""Read-only provenance and frozen-evaluation audit; no candidate repair."""
import argparse,collections,json
from pathlib import Path
from core import read,save,digest,parse_output
from regression import materialize

def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]

def audit(root):
    sample=read(root/'sample.json');questions={str(q['id']):q for q in read(root/'trial-input.json')['questions']}
    freeze=read(root/'evaluation-freeze.json',{});bundles=read(root/'test-packages.json',{})
    checks={
        '100_unique_questions':len(questions)==100,
        'no_prior_question_ids':not(set(map(int,questions))&set(sample['excluded_ids'])),
        'regression_passed':bool(read(root/'regression'/'validation.json',{}).get('passed')),
        'regression_runner_matches_freeze':read(root/'regression'/'validation.json',{}).get('workflow_core_sha256')==freeze.get('source_sha256',{}).get('core.py'),
        'mutant_production_canaries_passed':bool(read(root/'regression'/'mutant-canary-validation.json',{}).get('passed')),
        'capability_canaries_passed':bool(read(root/'regression'/'capability-canary-validation-fifth.json',{}).get('passed')),
        'frozen_before_official_results':freeze.get('current_cohort_official_results_seen')==0,
        'frozen_before_local_semantic_results':freeze.get('current_cohort_local_semantic_results_seen')==0,
        'frozen_inputs_unchanged':bool(freeze) and all(digest((root/n).read_text(encoding='utf-8'))==h for n,h in freeze.get('file_sha256',{}).items()),
        'runner_unchanged_since_freeze':freeze.get('source_sha256',{}).get('core.py')==digest(Path(__file__).with_name('core.py').read_text(encoding='utf-8')),
    }
    source_fields=('id','title','group','question','template','language','existing_samples','fixture_sources')
    source_jobs={}
    for p in (root/'production').glob('tests-input-*.jsonl'):
        for r in rows(p):source_jobs[str(r['id'])]=json.loads(r['input'])
    checks['source_only_test_inputs']=set(source_jobs)==set(questions) and all(source_jobs[i]=={k:q[k] for k in source_fields} for i,q in questions.items())
    produced={};attempts=collections.Counter();models=collections.Counter();invalid=[]
    for p in sorted((root/'production').glob('tests-reviewed*.jsonl')):
        if '.failed.' in p.name:continue
        for r in rows(p):
            attempts[str(r['id'])]+=1;models[(r.get('provider','unknown'),r.get('model','unknown'))]+=1
            try:
                obj=parse_output(r);produced[str(obj['id'])]=materialize(obj,questions[str(obj['id'])])
            except Exception as e:invalid.append({'id':r.get('id'),'file':p.name,'error':str(e)})
    checks['test_packages_match_raw_production']=set(bundles)==set(questions) and all(produced.get(i)==b for i,b in bundles.items())
    checks['original_io_samples_preserved']=all([(c['input'],c['output']) for c in b.get('cases',[]) if c.get('purpose')=='original_public_sample']==[tuple(x) for x in questions[i].get('existing_samples',[])] for i,b in bundles.items() if b.get('mode')=='io' and b.get('invocation')!='argv')
    candidates=read(root/'official-candidates.json',[]);official=read(root/'official-state.json',{'entries':{}})['entries'];receipts=[]
    for c in candidates:
        key=f"{c['step_id']}:{c['variant']}:{digest(c['code'])}";o=official.get(key,{})
        receipts.append(o.get('phase')=='complete' and o.get('code_readback_verified') and o.get('account_matches_config') and o.get('code_sha256')==digest(c['code']))
    checks['200_exact_official_readbacks']=len(receipts)==200 and all(receipts)
    timestamps=[o.get('started_at','') for o in official.values()]
    from datetime import datetime
    checks['official_started_after_freeze']=bool(timestamps) and all(datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()>=freeze.get('frozen_at',float('inf')) for t in timestamps)
    local=read(root/'local-results.json',{'entries':{}})['entries']
    checks['local_runs_started_after_freeze']=len(local)==200 and all(x.get('started_at',0)>=freeze.get('frozen_at',float('inf')) for x in local.values())
    metrics_path=root/'official-request-metrics.jsonl';metrics=rows(metrics_path) if metrics_path.exists() else []
    report={'checks':checks,'all_checks_passed':all(checks.values()),'official_receipts':sum(bool(x) for x in receipts),'test_review_attempts':dict(attempts),'test_review_models':[{'provider':p,'model':m,'records':n} for (p,m),n in models.items()],'invalid_production_records':invalid,'official_http_statuses':dict(collections.Counter(str(x['http_status']) for x in metrics)),'official_request_counts':read(root/'official-request-counts.json',{}),'sampling_quotas':sample['quotas'],'note':'Audit checks provenance and exact readback; it does not assert semantic correctness.'}
    save(root/'audit.json',report);print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);a=ap.parse_args();audit(a.root)
