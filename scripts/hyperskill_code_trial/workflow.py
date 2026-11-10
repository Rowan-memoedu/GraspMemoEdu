"""Frozen, disjoint code-question experiment preparation and local evaluation."""
import argparse,collections,hashlib,json,re,shlex,sys,time
from pathlib import Path
from core import VERSION,read,save,digest,parse_output,derive_fixture_sources,validate_bundle,compile_run
from regression import materialize
HERE=Path(__file__).parent
SEED='hyperskill-code-independent-100-20261101-r2'
QUOTAS={'basics_io':20,'strings_arrays':18,'collections_generics':16,'functions_streams':12,'oop_state':18,'exceptions_reflection':8,'concurrency':4,'cli_args':4}
def category(q):
    g=q['group'].lower()
    if 'command-line' in g:return 'cli_args'
    if any(x in g for x in ['thread','concurrent','atomic','executor','future','semaphore','lock','latch','timer','interruption','reactive','copyonwrite']):return 'concurrency'
    if any(x in g for x in ['exception','error','reflection','annotation','class loader']):return 'exceptions_reflection'
    if any(x in g for x in ['lambda','functional','function composition','currying','collector','reduction','taking elements','method reference','optional','map and flatmap']) or ('stream' in g and not any(x in g for x in ['input','output'])):return 'functions_streams'
    if any(x in g for x in ['tostring','hashcode','default method','keyword "super"','primitive and reference']):return 'oop_state'
    if any(x in g for x in ['collection','arraylist','arraydeque','list interface','map','set','queue','stack','generic','wildcard','bounds','type erasure','reification','comparable','comparator']):return 'collections_generics'
    if any(x in g for x in ['string','character','array']):return 'strings_arrays'
    if any(x in g for x in ['class','interface','inherit','polymorph','overrid','constructor','enum','factory','builder','prototype','singleton','adapter','bridge','composite','decorator','facade','flyweight','proxy','chain','command','interpreter','iterator','mediator','memento','observer','strategy','template method','visitor','encapsulat','field','access modifier','object','getter','setter','static member']) or re.search(r'\bstate\b',g):return 'oop_state'
    return 'basics_io'
def jsonl(path,rows):
    text=''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows)
    if path.exists():assert path.read_text(encoding='utf-8')==text,'Frozen JSONL changed: '+str(path)
    else:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
def init(root,base):
    regression=read(root/'regression'/'validation.json');assert regression and regression['passed'],'Regression must pass before sampling'
    assert regression['workflow_core_sha256']==digest((HERE/'core.py').read_text(encoding='utf-8')),'Core changed after regression'
    bank=read(base/'bank-code-snapshot.json');old=read(base/'sample-v2.json');excluded={q['id'] for q in old['questions']}
    signature=lambda q:digest(re.sub(r'\s+',' ',q.get('question','')).strip()+'\n'+re.sub(r'\s+',' ',json.dumps(q.get('options',{}).get('code_templates',{}),sort_keys=True)).strip())
    old_signatures={signature(q) for q in old['questions']};content_duplicates=[]
    buckets=collections.defaultdict(list)
    for q in bank['code_questions']:
        if q['id'] not in excluded:
            if signature(q) in old_signatures:content_duplicates.append(q['id'])
            else:buckets[category(q)].append(q)
    order=lambda value:hashlib.sha256((SEED+':'+str(value)).encode()).hexdigest()
    quotas={cat:min(n,len(buckets[cat])) for cat,n in QUOTAS.items()}
    while sum(quotas.values())<sum(QUOTAS.values()):
        available=[cat for cat in QUOTAS if len(buckets[cat])>quotas[cat]]
        assert available,'Insufficient distinct remaining questions'
        cat=max(available,key=lambda cat:(len(buckets[cat])-quotas[cat],cat))
        quotas[cat]+=1
    selected=[]
    for cat,n in quotas.items():
        if not n:continue
        topics=collections.defaultdict(list)
        for raw in buckets[cat]:
            q=dict(raw);q['stratum']=cat;topics[q['group']].append(q)
        for values in topics.values():values.sort(key=lambda q:order(q['id']))
        names=sorted(topics,key=order);chosen=[]
        while len(chosen)<n:
            for name in names:
                if topics[name]:chosen.append(topics[name].pop(0))
                if len(chosen)==n:break
        selected.extend(chosen)
    for index,q in enumerate(selected,1):q['sample_index']=index
    assert len(selected)==100 and len({q['id'] for q in selected})==100 and not({q['id'] for q in selected}&excluded)
    sample={'schema':2,'seed':SEED,'sampling':'eight planned quota strata capped by remaining availability; deficits assigned to largest remaining stratum; topic round robin; hash order; excludes all first-cohort IDs and identical source content; before solution matching','population':len(bank['code_questions']),'eligible_population':sum(map(len,buckets.values())),'bank_sha256':bank['bank_sha256'],'planned_quotas':QUOTAS,'quotas':quotas,'availability':{cat:len(buckets[cat]) for cat in QUOTAS},'excluded_ids':sorted(excluded),'excluded_duplicate_content_ids':content_duplicates,'questions':selected}
    assert not (root/'sample.json').exists();save(root/'sample.json',sample)
    save(root/'protocol.json',{'version':VERSION,'test_policy':'reuse complete executable tests; public examples are preserved and supplemented','independence':'source-only test construction and review; freeze before official outcomes; no held-out semantic tuning','seed':SEED,'source_sha256':{p.name:digest(p.read_text(encoding='utf-8')) for p in HERE.iterdir() if p.suffix in ('.py','.mjs','.txt')},'prior_regression_passed':True,'selected_at':time.time()})
    print(json.dumps({'selected':100,'overlap':0,'quotas':quotas,'eligible':sample['eligible_population']}))
def prepare(root,base):
    sample=read(root/'sample.json');index=read(base/'reference-index.json');refs=[];tests=[];source_matches=0;test_candidates=[]
    for q in sample['questions']:
        live=read(root/'public-steps'/f"{q['id']}.json")['step'];assert live['id']==q['id'] and live['block']['name']=='code'
        options=live['block']['options'];q['language']=max((x for x in options['code_templates'] if x.startswith('java')),key=lambda x:int(re.search(r'\d+',x)[0]))
        q['template']=options['code_templates'][q['language']];q['question']=live['block']['text'];q['existing_samples']=options.get('samples') or []
        q['fixture_sources']=derive_fixture_sources(q)
        source={k:q[k] for k in ('id','title','group','question','template','language','existing_samples','fixture_sources')}
        matches=index.get(str(q['id']),[]);source_matches+=bool(matches)
        public=[{k:m[k] for k in ('source_url','repository','commit','java_files')} for m in matches[:3]]
        for m in matches:
            if m.get('tests'):test_candidates.append({'id':q['id'],'source_url':m['source_url'],'tests':m['tests']})
        images=re.findall(r'<img[^>]+src="([^"]+)"',q['question']);media={'requires_visual':True,'images':[{'url':u,'detail':'original'} for u in images]} if images else {}
        refs.append({'id':str(q['id']),'input':json.dumps({**source,'public_candidates':public},ensure_ascii=False),**media})
        tests.append({'id':str(q['id']),'input':json.dumps(source,ensure_ascii=False),**media})
    assert not test_candidates,'Retrieved test files require completeness inspection before generation'
    save(root/'trial-input.json',sample);save(root/'retrieval.json',{'source_matches':source_matches,'full_test_files_found':test_candidates,'index_source':str(base/'reference-index.json')})
    for lane in range(2):jsonl(root/'production'/f'references-input-{lane}.jsonl',refs[lane::2]);jsonl(root/'production'/f'tests-input-{lane}.jsonl',tests[lane::2])
    print(json.dumps({'reference_jobs':len(refs),'test_jobs':len(tests),'source_matches':source_matches,'public_sample_questions':sum(bool(q['existing_samples']) for q in sample['questions']),'image_questions':sum(bool(r.get('images')) for r in tests)}))
def collect(root):
    questions={str(q['id']):q for q in read(root/'trial-input.json')['questions']};refs={};bundles={};errors=[]
    for kind,pattern,target in [('reference','references-output*.jsonl',refs),('tests','tests-reviewed*.jsonl',bundles)]:
        for path in sorted((root/'production').glob(pattern)):
            if '.failed.' in path.name:continue
            for line in path.read_text(encoding='utf-8').splitlines():
                raw=json.loads(line)
                try:
                    obj=parse_output(raw);i=str(obj['id']);assert i in questions
                    if kind=='reference':
                        assert obj.get('status')=='ready' and obj.get('official_code','').strip()
                        if obj.get('mutant_code')==obj['official_code']:obj['mutant_code']=''
                        if i in target and target[i].get('official_code'):
                            if not target[i].get('mutant_code') and obj.get('mutant_code'):
                                assert obj['official_code']==target[i]['official_code'],'Mutant retry changed the frozen reference'
                                target[i]['mutant_code']=obj['mutant_code'];target[i]['mutant_rationale']=obj.get('mutant_rationale','')
                        else:target[i]=obj
                    else:target[i]=materialize(obj,questions[i])
                except Exception as e:errors.append({'kind':kind,'id':raw.get('id'),'file':path.name,'error':str(e)})
    candidates=[]
    for i,q in questions.items():
        c=refs.get(i,{})
        for variant,field in [('reference','official_code'),('mutant','mutant_code')]:
            if c.get(field):candidates.append({'step_id':q['id'],'variant':variant,'language':q['language'],'code':c[field]})
    save(root/'candidate-packages.json',refs);save(root/'test-packages.json',bundles);save(root/'official-candidates.json',candidates)
    state={'references':len(refs),'test_packages':len(bundles),'candidate_programs':len(candidates),'errors':errors,'missing_references':[i for i in questions if i not in refs],'missing_mutants':[i for i in questions if not refs.get(i,{}).get('mutant_code')],'missing_tests':[i for i in questions if i not in bundles],'invalid_tests':{i:validate_bundle(b) for i,b in bundles.items() if validate_bundle(b)}}
    save(root/'production-validation.json',state);print(json.dumps(state,ensure_ascii=False))
def missing_source_fixtures(template,diagnostics):
    defined=set(re.findall(r'\b(?:class|interface|enum|record)\s+(\w+)',template))
    imported=set(re.findall(r'\bimport\s+[\w.]+\.(\w+)\s*;',template))
    names=set(re.findall(r'symbol:\s+class\s+(\w+)',diagnostics))
    return sorted(n for n in names-defined-imported if re.search(r'\b'+re.escape(n)+r'\b',template))

def evaluate(root,jdk,compile_only=False,shard=None):
    if not compile_only:
        frozen=read(root/'evaluation-freeze.json');assert frozen,'Freeze before semantic evaluation'
        assert frozen['source_sha256']['core.py']==digest((HERE/'core.py').read_text(encoding='utf-8')),'Frozen runner changed'
        assert all(digest((root/n).read_text(encoding='utf-8'))==h for n,h in frozen['file_sha256'].items()),'Frozen evaluation input changed'
    candidates=read(root/'official-candidates.json');bundles=read(root/'test-packages.json');questions={q['id']:q for q in read(root/'trial-input.json')['questions']};filename='compile-preflight.json' if compile_only else 'local-results.json'
    if shard is not None:
        assert shard in (0,1);candidates=candidates[shard::2];filename=filename.replace('.json',f'-{shard}.json')
    state=read(root/filename,{'schema':2,'entries':{}})
    for c in candidates:
        p=bundles.get(str(c['step_id']))
        if not p:continue
        key=f"{c['step_id']}:{c['variant']}:{digest(c['code'])}:{digest(p)}:{digest((HERE/'core.py').read_text(encoding='utf-8'))}"
        if key in state['entries']:continue
        started_at=time.time()
        if p.get('status')!='ready':r={'verdict':'unsupported','reason':p.get('reason')}
        else:
            try:r=compile_run(c['code'],p,jdk,compile_only=compile_only)
            except Exception as e:r={'verdict':'environment_error','error':str(e)}
        if r['verdict']=='compile_error':
            missing=missing_source_fixtures(questions[c['step_id']]['template'],r.get('compile',{}).get('stderr',''))
            if missing:r={**r,'verdict':'test_error','missing_source_fixture_types':missing,'reason':'Original template depends on unavailable, undefined and unimported input fixture types; not a learner verdict.'}
        row={'step_id':c['step_id'],'variant':c['variant'],'started_at':started_at,'code_sha256':digest(c['code']),'test_sha256':digest(p),'runner_sha256':digest((HERE/'core.py').read_text(encoding='utf-8')),**r};state['entries'][key]=row;save(root/filename,state)
        print(json.dumps({'id':c['step_id'],'variant':c['variant'],'verdict':r['verdict']}),flush=True)
    print(json.dumps({'runs':len(state['entries']),'mode':'compile' if compile_only else 'execute'}))

def merge_local(root):
    merged={}
    for shard in (0,1):
        entries=read(root/f'local-results-{shard}.json',{'entries':{}})['entries']
        assert len(entries)==100 and not(set(entries)&set(merged)),'Incomplete or overlapping local shards'
        merged.update(entries)
    assert len(merged)==200;save(root/'local-results.json',{'schema':2,'entries':merged});print(json.dumps({'local_runs':len(merged)}))
def freeze(root):
    v=read(root/'production-validation.json');assert v and v['references']==100 and v['candidate_programs']==200 and v['test_packages']==100
    assert not(read(root/'official-state.json',{}).get('entries')),'Already has official observations'
    assert not any(read(p,{}).get('entries') for p in root.glob('local-results*.json')),'Already has local semantic observations'
    names=['trial-input.json','candidate-packages.json','test-packages.json','official-candidates.json','protocol.json']
    hashes={n:digest((root/n).read_text(encoding='utf-8')) for n in names}
    f={'frozen_at':time.time(),'file_sha256':hashes,'source_sha256':{p.name:digest(p.read_text(encoding='utf-8')) for p in HERE.iterdir() if p.suffix in ('.py','.mjs','.txt')},'current_cohort_official_results_seen':0,'current_cohort_local_semantic_results_seen':0,'candidate_programs':200,'overlap_with_first_cohort':0}
    assert not (root/'evaluation-freeze.json').exists();save(root/'evaluation-freeze.json',f);print(json.dumps({'evaluation_frozen':True,'candidates':200}))
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['init','prepare','collect','compile','local','merge-local','freeze']);ap.add_argument('--root',type=Path,required=True);ap.add_argument('--baseline',type=Path);ap.add_argument('--jdk');ap.add_argument('--shard',type=int,choices=(0,1));a=ap.parse_args()
    if a.mode=='init':init(a.root,a.baseline)
    elif a.mode=='prepare':prepare(a.root,a.baseline)
    elif a.mode=='collect':collect(a.root)
    elif a.mode=='freeze':freeze(a.root)
    elif a.mode=='merge-local':merge_local(a.root)
    else:evaluate(a.root,a.jdk or read('/mnt/d/Caches/HyperskillCodeTrial/runtime/jdk25.json')['java_home'],a.mode=='compile',a.shard)
