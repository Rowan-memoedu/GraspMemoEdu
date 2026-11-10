import argparse,json,re,sys
from pathlib import Path
from core import read,save,parse_output,compile_run,validate_bundle,normalize,CHECKS,digest,derive_fixture_sources

def prepare(root,baseline):
    sample=read(baseline/'trial-input.json')['questions'];refs=read(baseline/'candidate-packages.json')
    ids={11184,4329,2497,16536,8120,9897};jobs=[];controls={}
    for q in sample:
        if q['id'] not in ids:continue
        source={k:q[k] for k in ('id','title','question','template','existing_samples')};source['regression_scope']='Test adapter and contract checks; no reference implementations supplied.'
        jobs.append({'id':str(q['id']),'input':json.dumps(source,ensure_ascii=False)})
        controls[str(q['id'])]={'question':q,'good':refs[str(q['id'])]['official_code'],'bad':refs[str(q['id'])]['mutant_code'],'bad_must_fail':q['id'] in (11184,4329,8120,9897)}
    fixtures=[
      (-903,'Independent saved counters','Implement Counter.save() returning an independent state snapshot and Counter.restore(State). value() returns the current count.','class Counter {int n; void increment(){n++;} int value(){return n;} static class State{/*fill*/} State save(){return null;} void restore(State s){/*fill*/}}',
       'class Counter {int n; void increment(){n++;} int value(){return n;} static class State{final int n;State(int n){this.n=n;}} State save(){return new State(n);} void restore(State s){n=s.n;}}',
       'class Counter {int n; void increment(){n++;} int value(){return n;} static class State{final int n;State(int n){this.n=n;}} State save(){return new State(n);} void restore(State s){n=0;}}'),
      (-904,'Unordered distinct names','Implement Names.collect(String[] values), returning each distinct input string exactly once. Result order is unspecified. Inputs are non-null arrays of non-null strings.','class Names {static String[] collect(String[] values){return null;}}',
       'class Names {static String[] collect(String[] a){return java.util.Arrays.stream(a).distinct().sorted(java.util.Comparator.reverseOrder()).toArray(String[]::new);}}',
       'class Names {static String[] collect(String[] a){return a.clone();}}'),
      (-905,'Integer box ordering','Implement BoxOrder to order Box objects by ascending value. Do not read or print in compare.','class Box {final int value;Box(int v){value=v;}} class BoxOrder implements java.util.Comparator<Box>{public int compare(Box a,Box b){return 0;}}',
       'class Box {final int value;Box(int v){value=v;}} class BoxOrder implements java.util.Comparator<Box>{public int compare(Box a,Box b){return Integer.compare(a.value,b.value);}}',
       'class Box {final int value;Box(int v){value=v;}} class BoxOrder implements java.util.Comparator<Box>{public int compare(Box a,Box b){return 0;}}'),
      (-908,'Arbitrary precision conversion','Implement Converter.numbers(String[] input), returning a java.math.BigInteger array with exactly the same decimal integer values and order. Inputs are valid signed decimal integer strings of arbitrary size. Empty arrays are permitted.','class Converter {static java.math.BigInteger[] numbers(String[] input){return null;}}',
       'class Converter {static java.math.BigInteger[] numbers(String[] a){return java.util.Arrays.stream(a).map(java.math.BigInteger::new).toArray(java.math.BigInteger[]::new);}}',
       'class Converter {static java.math.BigInteger[] numbers(String[] a){return java.util.Arrays.stream(a).map(x->java.math.BigInteger.valueOf(Long.parseLong(x))).toArray(java.math.BigInteger[]::new);}}')]
    for i,title,question,template,good,bad in fixtures:
        q={'id':i,'title':title,'question':question,'template':template,'existing_samples':[]}
        jobs.append({'id':str(i),'input':json.dumps(q)});controls[str(i)]={'question':q,'good':good,'bad':bad,'bad_must_fail':True}
    root.mkdir(parents=True,exist_ok=True);save(root/'controls.json',controls)
    for lane in range(2):
        p=root/f'input-{lane}.jsonl';assert not p.exists();p.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in jobs[lane::2]),encoding='utf-8')
    print(json.dumps({'regressions':len(jobs),'ids':[x['id'] for x in jobs]}))

def materialize(obj,q):
    b=dict(obj)
    from core import declares_main
    b['template_has_main']=declares_main(q.get('template',''))
    fixed=re.search(r'\bString\s+toString\s*\(\s*\)\s*\{([\s\S]*?)\n\s*\}',q.get('template',''))
    b['fixed_to_string']=bool(fixed and not re.search(r'TODO|write your|implement|your code',fixed.group(1),re.I))
    supplied=derive_fixture_sources(q)
    defined='\n'.join([b.get('harness_code') or '',*[f['code'] for f in b.get('support_files',[])]])
    b['support_files']=b.get('support_files',[])+[f for f in supplied if not re.search(r'\bclass\s+'+re.escape(f['name'][:-5])+r'\b',defined)]
    if b.get('mode')=='io':
        cases=[]
        for stdin,stdout in q.get('existing_samples',[]):
            import shlex
            cases.append({'input':'' if b.get('invocation')=='argv' else stdin,'output':stdout,'argv':shlex.split(stdin) if b.get('invocation')=='argv' else [],'purpose':'original_public_sample'})
        b['cases']=cases+b.get('cases',[])
    return b

def validate(root,jdk):
    print(json.dumps({'stage':'regression_start','root':str(root),'python':sys.executable}),flush=True)
    controls=read(root/'controls.json');outputs={};parse_errors=[]
    for path in sorted(root.glob('output*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            raw=json.loads(line)
            try:outputs[str(raw['id'])]=parse_output(raw)
            except Exception as e:parse_errors.append({'id':raw['id'],'error':str(e)})
    results=[]
    for i,c in controls.items():
        if i not in outputs:results.append({'id':i,'passed':False,'error':'output_missing'});continue
        b=materialize(outputs[i],c['question']);good=compile_run(c['good'],b,jdk);bad=compile_run(c['bad'],b,jdk)
        passed=good['verdict']=='accepted' and (not c['bad_must_fail'] or bad['verdict'] in ('wrong_answer','runtime_error'))
        row={'id':i,'passed':passed,'bundle_sha256':digest(b),'correct':good,'incorrect':bad,'bad_must_fail':c['bad_must_fail']};results.append(row)
        print(json.dumps({'id':i,'passed':passed,'good':good['verdict'],'bad':bad['verdict']}),flush=True)
        save(root/'validation.json',{'passed':False,'rows':results,'parse_errors':parse_errors})
    ok=len(results)==len(controls) and all(r['passed'] for r in results)
    save(root/'validation.json',{'passed':ok,'rows':results,'parse_errors':parse_errors,'workflow_core_sha256':digest(Path(__file__).with_name('core.py').read_text(encoding='utf-8'))})
    if not ok:sys.exit(1)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['prepare','validate']);ap.add_argument('--root',type=Path,required=True);ap.add_argument('--baseline',type=Path);ap.add_argument('--jdk');a=ap.parse_args()
    if a.mode=='prepare':prepare(a.root,a.baseline)
    else:validate(a.root,a.jdk or read('/mnt/d/Caches/HyperskillCodeTrial/runtime/jdk25.json')['java_home'])
