import argparse,json
from pathlib import Path
from core import parse_output
ap=argparse.ArgumentParser();ap.add_argument('--drafts',type=Path,required=True);ap.add_argument('--sources',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
sources={str(r['id']):r for r in (json.loads(s) for s in a.sources.read_text(encoding='utf-8').splitlines())};rows=[]
for line in a.drafts.read_text(encoding='utf-8').splitlines():
 r=json.loads(line);source=sources[str(r['id'])]
 try:draft=parse_output(r)
 except Exception:draft={'invalid_raw_output':r.get('output','')}
 rows.append({**source,'input':json.dumps({'original_exercise':json.loads(source['input']),'draft_test_bundle':draft},ensure_ascii=False)})
assert not a.output.exists();a.output.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8');print(json.dumps({'review_jobs':len(rows)}))
