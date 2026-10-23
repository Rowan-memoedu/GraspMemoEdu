"""Retrieve complete lexical entries from a supplied local teaching corpus.

The target text and draft objectives seed retrieval; every matching source entry
is retained, including competing explanations and examples. This is a candidate
pool, not a claim that every retrieved entry belongs in the final curriculum.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


STOP = set('a an the and or but so if when while where which that what who whose how '
           'to of in on at by for from with as into about over through during '
           'be am is are was were been being have has had do does did can could may '
           'might will would shall should must not no only also it its they them '
           'their he him his she her we us our you your i me my this these those '
           'there then than all any some such each every much many more most one '
           'two three first second other another same'.split())


def vocabulary(text):
    words = {w.lower() for w in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)} - STOP
    forms = set(words)
    for word in words:
        if word.endswith('ies'): forms.add(word[:-3] + 'y')
        if word.endswith('s') and len(word) > 3: forms.add(word[:-1])
        if word.endswith('es') and len(word) > 4: forms.add(word[:-2])
        if word.endswith('ed') and len(word) > 4: forms.update((word[:-2], word[:-1]))
        if word.endswith('ing') and len(word) > 5: forms.update((word[:-3], word[:-3] + 'e'))
    return forms - STOP


def lexical_entries(text):
    """Keep an entry and all descendants; never select an isolated example hit."""
    lines = text.splitlines()
    starts = []
    for i, line in enumerate(lines):
        match = re.fullmatch(r'(?:- |#{1,4} )(?:(?:\d+[.)])\s*)?([A-Za-z][A-Za-z -]{0,70})\s*', line)
        contrast = re.fullmatch(r'- [➯→]([A-Za-z][A-Za-z ,/.-]{0,90})\s*', line)
        match = match or contrast
        if match:
            label = match[1].strip()
            if len(label.split()) <= 5:
                starts.append((i, label))
    for k, (start, label) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        # A category heading between entries belongs to the following entry.
        for j in range(start + 1, end):
            if re.match(r'^#{1,4} ', lines[j]):
                end = j
                break
        yield label, start + 1, end, '\n'.join(lines[start:end]).rstrip()


def retrieve(source, draft, corpus):
    goal = source['sources'][0]['text']
    primary = vocabulary(goal)
    objective_text = '\n'.join(p['content'] for t in draft['topics']
                               for s in t['sections'] for c in s['chunks'] for p in c['points'])
    related = vocabulary(objective_text)
    additions, inventory = [], []
    for file in sorted((corpus / 'remnote/materials').glob('*.md')):
        raw = file.read_bytes()
        text = raw.decode('utf-8-sig')
        entries = list(lexical_entries(text))
        hits = []
        for label, start, end, excerpt in entries:
            labels = vocabulary(label)
            if not labels & (primary | related):
                continue
            relative = file.relative_to(corpus).as_posix()
            ident = 'lex-' + hashlib.sha256(f'{relative}:{start}:{end}'.encode()).hexdigest()[:12]
            hits.append({'id': ident, 'title': file.stem + '：' + label, 'relative_path': relative,
                         'line_start': start, 'line_end': end,
                         'file_sha256': hashlib.sha256(raw).hexdigest(), 'text': excerpt,
                         'retrieval_reason': 'goal_vocabulary' if labels & primary else 'supporting_vocabulary'})
        additions.extend(hits)
        inventory.append({'file': file.relative_to(corpus).as_posix(), 'entries': len(entries),
                          'matches': len(hits), 'matched_labels': [h['title'].split('：', 1)[1] for h in hits]})
    return additions, inventory


def main(args):
    source = json.loads(args.source.read_text(encoding='utf-8'))
    draft = json.loads(args.plan.read_text(encoding='utf-8'))
    additions, inventory = retrieve(source, draft, args.corpus)
    args.output.mkdir(parents=True, exist_ok=False)
    expanded = {**source, 'corpus_root': args.corpus.resolve().as_posix(), 'sources': source['sources'] + additions,
                'material_selection': 'Complete matching lexical entries are a candidate pool. Select by teaching relevance; do not import every sense or example indiscriminately.'}
    for name, data in [('sources.json', expanded), ('retrieval.json', inventory)]:
        (args.output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 课程原材料检索', '', '## 已有输入材料', '',
             *['- ' + title for title in dict.fromkeys(s['title'] for s in source['sources'])], '',
             '## 补充检索到的原材料', '',
             '以下条目包含目标文章词汇及现有教学目标涉及的相关词汇，保留整条释义、例句和翻译。检索命中不等于全部选入课程。', '']
    for row in inventory:
        lines.append(f'- {Path(row["file"]).stem}：检索 {row["entries"]} 个完整条目，命中 {row["matches"]} 个。')
    for item in additions:
        path = (args.corpus / item['relative_path']).as_posix()
        lines.extend(['', '### ' + item['title'], '',
                      f'[查看原文](<{path}:{item["line_start"]}>)', '', item['text'], ''])
    (args.output / '选材核对.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'new_entries': len(additions), 'source_characters': sum(len(s['text']) for s in expanded['sources']),
                      'inventory': inventory}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args())
