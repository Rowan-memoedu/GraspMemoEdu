import json
from pathlib import Path
from core import read,save,derive_fixture_sources,parse_output
ROOT=Path(r'D:\Artifacts\GraspMemoEdu\code-100-independent-20261101\regression')
controls=read(ROOT/'controls.json');validation=read(ROOT/'validation.json');failed={str(r['id']) for r in validation['rows'] if not r['passed']}
i='-910';q={'id':int(i),'title':'Count public generic parameters','question':'Implement Inspector.report(Probe object, String name) to print the number of type parameters on the named no-argument method. The example input describes the named method.','template':'class Inspector {static void report(Probe object, String name) throws Exception {/*fill*/}}','existing_samples':[['<X> void alpha','1'],['<A, B, C> void beta','3']]}
controls[i]={'question':q,'good':'class Inspector {static void report(Probe object,String name)throws Exception {System.out.println(object.getClass().getDeclaredMethod(name).getTypeParameters().length);}}','bad':'class Inspector {static void report(Probe object,String name)throws Exception {System.out.println(object.getClass().getDeclaredMethod(name).getTypeParameters().length+1);}}','bad_must_fail':True};failed.add(i);save(ROOT/'controls.json',controls)
rows=[]
for i in sorted(failed,key=int):
 q=controls[i]['question'];source={k:q[k] for k in ('id','title','question','template','existing_samples')};source['fixture_sources']=derive_fixture_sources(q)
 rows.append({'id':i,'input':json.dumps(source,ensure_ascii=False)})
p=ROOT/'repair-input.jsonl';assert not p.exists();p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
print(json.dumps({'source_only_rebuild':[r['id'] for r in rows]}))
