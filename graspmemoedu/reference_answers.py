"""Project existing structured grading answers only when the answer side is visible."""
from functools import lru_cache
from html import escape
from pathlib import Path
import re

from .question_types import demo_answer, display_answer


@lru_cache(maxsize=256)
def _render(text):
    if re.search(r'\$[^$]+\$', text):
        # Mechanical typography through the existing offline renderer, no judge
        # or content generation. Imported option HTML is reused below instead.
        from .guide import GuideReader
        value = GuideReader(Path())._compile(text)
        return value['html'], value['math_css']
    return '<p>' + escape(text).replace('\n', '<br>') + '</p>', ''


def reference_fields(question, visible=False):
    spec = question.get('interaction') or {}
    if not visible or spec.get('type', 'text') == 'text':
        return {}
    answer = display_answer(question, demo_answer(question))
    if spec['type'] == 'choice':
        ids = set(spec['correct_ids']) if spec.get('multiple') else {spec['correct_id']}
        rows = [item for item in spec['options'] if item['id'] in ids]
        html = ''.join(item.get('html') or _render(item['text'])[0] for item in rows)
        css = '\n'.join(_render(item['text'])[1] for item in rows if not item.get('html'))
    elif spec['type'] == 'matching':
        labels = {side: {item['id']: item for item in spec[side]} for side in ('left', 'right')}
        arrow = '↔' if spec.get('direction') == 'both' else '→'
        html, styles = [], []
        for pair in spec['pairs']:
            parts = []
            for side in ('left', 'right'):
                item = labels[side][pair[side]]
                rendered, style = (item['html'], '') if item.get('html') else _render(item['text'])
                parts.append('<div>' + rendered + '</div>'); styles.append(style)
            html.append('<div class="referencePair">' + parts[0] + '<span>' + arrow + '</span>' + parts[1] + '</div>')
        html, css = ''.join(html), '\n'.join(styles)
    else:
        html, css = _render(answer)
    return {'reference_answer': answer, 'reference_answer_html': html, 'reference_math_css': css}
