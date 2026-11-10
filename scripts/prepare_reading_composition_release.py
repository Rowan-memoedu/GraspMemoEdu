"""Capture exact deployed sources before reviewing a bounded reading update."""
import argparse
import base64
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

FILES = ['graspmemoedu/'+name+'.py' for name in (
    'topic_composition', 'topic_format', 'topic_runtime', 'topic_structure', 'service',
    'ordered_learning', 'unified_learning', 'training_content', 'api', 'help_requests',
    'materials', 'content_references', 'dashboard', 'atomic_content', 'academy_import',
    'reading_book_import')]+['scripts/import_course.mjs', 'scripts/verify_content.py']


def capture(output):
    root = Path(__file__).resolve().parents[1]
    remote = ('import pathlib,json,base64; root=pathlib.Path("/opt/graspmemoedu/app"); '
        'names='+repr(FILES)+'; print(json.dumps({n:base64.b64encode((root/n).read_bytes()).decode() '
        'if (root/n).exists() else None for n in names}))')
    run = subprocess.run(['ssh', '-o', 'BatchMode=yes', 'ubuntu@124.221.144.155', 'python3', '-'],
                         input=remote, capture_output=True, text=True, check=True, timeout=90)
    values = json.loads(run.stdout); changes = []
    for name, value in values.items():
        raw = base64.b64decode(value) if value else b''
        target = output/'production-baseline'/name; target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != raw: raise ValueError('Captured baseline changed')
        target.write_bytes(raw)
        local = (root/name).read_bytes()
        diff = ''.join(difflib.unified_diff(raw.decode().splitlines(True), local.decode().splitlines(True),
                                          fromfile='deployed/'+name, tofile='local/'+name))
        report = output/'source-diffs'/(name+'.diff'); report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(diff, encoding='utf-8')
        changes.append({'path': name, 'before': hashlib.sha256(raw).hexdigest() if value else None,
                        'after': hashlib.sha256(local).hexdigest(), 'diff_lines': len(diff.splitlines())})
    (output/'baseline.json').write_text(json.dumps(changes, indent=2), encoding='utf-8')
    print(json.dumps(changes, indent=2))


def compiler(output):
    """Keep unrelated local renderer work outside this bounded production update."""
    root = Path(__file__).resolve().parents[1]
    old = (output/'production-baseline/scripts/import_course.mjs').read_text(encoding='utf-8')
    current = (root/'scripts/import_course.mjs').read_text(encoding='utf-8')
    start = 'export async function renderer('
    stop = '\nexport async function inspectCourse('
    # The only renderer change required by composition is rendering source headings.
    old_renderer = old[old.index(start):old.index(stop)]
    old_return = 'return {nodes, wrap, withoutMermaid,'
    if old_renderer.count(old_return) != 1: raise ValueError('Renderer baseline changed')
    new_renderer = old_renderer.replace(old_return,
        "return {nodes, wrap, heading:text => body({head:text, lines:[], children:[], meta:{}}).replace(/<\\/?p>/g, ''), withoutMermaid,")
    value = current[:current.index(start)] + new_renderer + current[current.index(stop):]
    target = output/'release-compiler.mjs'
    target.write_text(value, encoding='utf-8')
    (output/'release-compiler.diff').write_text(''.join(difflib.unified_diff(old.splitlines(True), value.splitlines(True))), encoding='utf-8')
    print(str(target))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('output', type=Path)
    p.add_argument('--compiler', action='store_true'); args = p.parse_args()
    (compiler if args.compiler else capture)(args.output)
