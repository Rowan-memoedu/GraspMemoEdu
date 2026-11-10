"""Check a scoped production compiler against representative prepared Topics."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def verify(root):
    site = Path(__file__).resolve().parents[1]
    stage = root/'compiler-check'; scripts = stage/'scripts'; scripts.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root/'release-compiler.mjs', scripts/'import_course.mjs')
    for name in ('verify_content.py', 'topic-presentation.mjs'):
        shutil.copyfile(site/'scripts'/name, scripts/name)
    shutil.copytree(site/'scripts/generated', scripts/'generated', dirs_exist_ok=True)
    env = {**os.environ, 'PYTHONPATH': str(site), 'GRASPMEMOEDU_PYTHON': sys.executable}
    report = []
    for book in ('gaoxian', 'liwenwei'):
        plan = json.loads((root/book/'course-install-plan.json').read_bytes())
        candidates = [(r, json.loads((Path(r['package'])/'topic.json').read_bytes())) for r in plan['topics']]
        chosen = {}
        for condition in (lambda t: t.get('bank_questions'), lambda t: any(i.get('footnotes') for i in t['introductions']),
                          lambda t: any(i.get('example') for i in t['introductions'])):
            row, _ = next((r,t) for r,t in candidates if condition(t)); chosen[row['topic_id']] = row
        for ident, row in chosen.items():
            package = Path(row['package']); out = stage/ident; out.mkdir(exist_ok=True)
            result = subprocess.run(['node', str(scripts/'import_course.mjs'), str(package/'source.json'),
                str(package/'answers.json'), str(out/'topic.json'), str(out/'audit.json'), str(package/'review.json')],
                capture_output=True, text=True, encoding='utf-8', env=env, timeout=180)
            if result.returncode: raise ValueError(result.stderr[-3000:])
            actual = json.loads((out/'topic.json').read_bytes()); expected = json.loads((package/'topic.json').read_bytes())
            if actual != expected: raise ValueError('Scoped compiler changed prepared content: '+ident)
            report.append(ident); print('Compiler equivalence: '+ident, flush=True)
    (root/'compiler-verification.json').write_text(json.dumps({'passed': True, 'topics': report}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('root', type=Path); verify(p.parse_args().root)
