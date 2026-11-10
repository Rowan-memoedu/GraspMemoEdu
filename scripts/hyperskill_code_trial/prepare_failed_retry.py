import argparse,json
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True);ap.add_argument('--failed',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
ids={str(json.loads(s)['id']) for s in a.failed.read_text(encoding='utf-8').splitlines() if s.strip()}
rows=[json.loads(s) for s in a.input.read_text(encoding='utf-8').splitlines()];rows=[r for r in rows if str(r['id']) in ids]
assert not a.output.exists();a.output.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8');print({'retry_ids':[r['id'] for r in rows]})
