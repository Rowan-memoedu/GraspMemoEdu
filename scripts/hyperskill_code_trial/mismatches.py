"""Export evidence for disagreement analysis without changing the frozen trial."""
import argparse,difflib,json
from pathlib import Path
from core import read,save,digest
ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);a=ap.parse_args();root=a.root
summary=read(root/'summary.json');questions={str(q['id']):q for q in read(root/'trial-input.json')['questions']};refs=read(root/'candidate-packages.json');bundles=read(root/'test-packages.json');local=read(root/'local-results.json')['entries'];official=read(root/'official-state.json')['entries'];runner=digest(Path(__file__).with_name('core.py').read_text(encoding='utf-8'));result=[]
for row in summary['records']:
    if row['classification'] not in ('false_accept','false_reject','unresolved'):continue
    i=str(row['id']);c=refs[i];b=bundles[i];code=c['official_code' if row['variant']=='reference' else 'mutant_code'];key=f"{i}:{row['variant']}:{digest(code)}";l=local.get(key+':'+digest(b)+':'+runner,{})
    result.append({**row,'question':questions[i]['question'],'template':questions[i]['template'],'existing_samples':questions[i].get('existing_samples',[]),'reference_code':c['official_code'],'mutant_diff':'\n'.join(difflib.unified_diff(c['official_code'].splitlines(),c['mutant_code'].splitlines(),fromfile='reference',tofile='mutant',lineterm='')),'tests':b,'local_result':l,'official_feedback':{k:official.get(key,{}).get(k) for k in ('status','feedback','hint','code_readback_verified')}})
save(root/'mismatches.json',result);print(json.dumps([{'id':r['id'],'variant':r['variant'],'classification':r['classification']} for r in result]))
