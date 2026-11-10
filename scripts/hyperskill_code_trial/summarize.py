import argparse,collections,csv,json
from pathlib import Path
from core import read,save,digest
ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);a=ap.parse_args();root=a.root
questions=read(root/'trial-input.json')['questions'];refs=read(root/'candidate-packages.json');bundles=read(root/'test-packages.json');official=read(root/'official-state.json',{'entries':{}})['entries'];local=read(root/'local-results.json',{'entries':{}})['entries'];core_hash=digest(Path(__file__).with_name('core.py').read_text(encoding='utf-8'))
rows=[];records=[];totals=collections.Counter();groups={};ablation=collections.Counter();semantic=collections.Counter()
for q in questions:
 i=str(q['id']);c=refs.get(i,{});p=bundles.get(i,{})
 row={'index':q['sample_index'],'id':q['id'],'title':q['title'],'stratum':q['stratum'],'reference_origin':c.get('origin','unavailable'),'test_mode':p.get('mode','unavailable'),'public_examples':len(q.get('existing_samples',[])),'reference_official':'unavailable','reference_local':'unavailable','mutant_official':'unavailable','mutant_local':'unavailable','new_io_cases':sum(x.get('purpose')!='original_public_sample' for x in p.get('cases',[])),'source_url':q['source_url']}
 for variant,field in [('reference','official_code'),('mutant','mutant_code')]:
  code=c.get(field,'');key=f"{i}:{variant}:{digest(code)}";o=official.get(key,{})
  l=local.get(key+':'+digest(p)+':'+core_hash,{})
  os=o.get('status','unavailable');ls=l.get('verdict','unsupported' if p.get('status')=='unsupported' else 'unavailable')
  record={'id':q['id'],'title':q['title'],'stratum':q['stratum'],'variant':variant,'official':os,'local':ls,'readback_verified':o.get('code_readback_verified',False),'classification':'unresolved'}
  if os in ('correct','wrong') and ls in ('accepted','wrong_answer','runtime_error','compile_error'):
   record['classification']=('correct_accepted' if ls=='accepted' else 'false_reject') if os=='correct' else ('false_accept' if ls=='accepted' else 'wrong_rejected')
  totals[record['classification']]+=1;totals['official_'+os]+=1;totals['local_'+ls]+=1;records.append(record);row[variant+'_official']=os;row[variant+'_local']=ls
  if 'compile_elapsed_ms' in l and record['classification']!='unresolved':
   semantic['programs']+=1;semantic['official_'+os]+=1;semantic[record['classification']]+=1
  sample_indices={n for n,x in enumerate(p.get('cases',[])) if x.get('purpose')=='original_public_sample'}
  runs=l.get('tests',[])
  if p.get('mode')=='io' and sample_indices and len(runs)==len(p.get('cases',[])) and os in ('correct','wrong'):
   original_pass=all(r['passed'] for r in runs if r['case_index'] in sample_indices)
   ablation['programs']+=1;ablation['official_'+os]+=1
   if os=='wrong':
    ablation['examples_only_false_accept']+=original_pass
    ablation['supplemented_false_accept']+=ls=='accepted'
    ablation['caught_by_new_cases']+=original_pass and ls!='accepted'
   else:
    ablation['examples_only_false_reject']+=not original_pass
    ablation['supplemented_false_reject']+=ls!='accepted'
    ablation['new_case_only_false_reject']+=original_pass and ls!='accepted'
 if row['reference_official']=='correct' and row['reference_local']=='accepted':totals['reference_covered_questions']+=1
 if row['reference_local'] in ('accepted','wrong_answer','runtime_error','compile_error'):totals['locally_judged_questions']+=1
 if row['reference_official']=='correct' and row['reference_local']=='accepted' and row['mutant_official']=='wrong' and row['mutant_local'] in ('wrong_answer','runtime_error'):totals['discriminating_questions']+=1
 rows.append(row)
for cat in dict.fromkeys(q['stratum'] for q in questions):
 qs=[r for r in rows if r['stratum']==cat];rs=[r for r in records if r['stratum']==cat];groups[cat]={'selected':len(qs),'reference_correct':sum(r['reference_official']=='correct' for r in qs),'accepted_both':sum(r['reference_official']=='correct' and r['reference_local']=='accepted' for r in qs),'false_accept':sum(r['classification']=='false_accept' for r in rs),'false_reject':sum(r['classification']=='false_reject' for r in rs)}
result={'questions':len(rows),'totals':dict(totals),'strata':groups,'rows':rows,'records':records,'reference_origins':dict(collections.Counter(r['reference_origin'] for r in rows)),'test_modes':dict(collections.Counter(r['test_mode'] for r in rows)),'compiled_programs_only_comparison':dict(semantic),'io_public_examples_ablation':dict(ablation),'protocol':'Independent question IDs and source-only tests; official agreement is not an absolute semantic oracle.'}
save(root/'summary.json',result)
with (root/'逐题结果.csv').open('w',encoding='utf-8-sig',newline='') as f:
 writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
print(json.dumps({k:v for k,v in result.items() if k not in ('rows','records')},ensure_ascii=False))
