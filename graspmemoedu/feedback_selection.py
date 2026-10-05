"""Stable, snapshot-bound anchors for optional feedback line selections.

Text offsets count Unicode code points. Formulae, diagrams and images are single
tokens; their internal SVG/MathJax text is never mistaken for reading lines.
"""
from hashlib import sha256
from html import escape
from html.parser import HTMLParser

from .service import DomainError


def content_hash(html):
    return sha256(html.encode('utf-8')).hexdigest()


def normalize_selection(selection):
    result = []
    for token, start, end in sorted(set(tuple(value) for value in selection)):
        if result and result[-1][0] == token and start <= result[-1][2]:
            result[-1][2] = max(result[-1][2], end)
        else:
            result.append([token, start, end])
    return result


class FeedbackContent(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html, *, annotate=False, selection=()):
        super().__init__(convert_charrefs=True)
        self.annotate = annotate
        self.selection = {}
        for token, start, end in normalize_selection(selection):
            self.selection.setdefault(token, []).append((start, end))
        self.tokens, self.parts, self.stack = [], [], []
        self.feed(html)
        self.close()
        self.html = ''.join(self.parts)

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, True)

    def _start(self, tag, attrs, closed):
        values = dict(attrs)
        mode = self.stack[-1][1] if self.stack else ''
        raw = self.get_starttag_text()
        if not mode:
            if tag in {'script', 'style', 'template'} or 'hidden' in values or values.get('aria-hidden') == 'true':
                mode = 'skip'
            elif 'data-tex' in values or tag in {'svg', 'img', 'mjx-container', 'math'} or (tag == 'pre' and 'mermaid' in values.get('class', '').split()):
                mode = 'atom'
                token = len(self.tokens)
                self.tokens.append(1)
                extra = f' data-feedback-atom="{token}"' if self.annotate else ''
                if token in self.selection:
                    extra += ' data-feedback-selected="true"'
                position = raw.rfind('/>') if closed else -1
                raw = raw[:position] + extra + raw[position:]
        self.parts.append(raw)
        if not closed and tag not in self.VOID:
            self.stack.append((tag, mode))

    def handle_endtag(self, tag):
        self.parts.append(f'</{tag}>')
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] in {'script', 'style'}:
            self.parts.append(data)
            return
        if (self.stack and self.stack[-1][1]) or not data.strip():
            self.parts.append(escape(data, quote=False))
            return
        token = len(self.tokens)
        self.tokens.append(len(data))
        cursor, parts = 0, []
        for start, end in self.selection.get(token, []):
            parts.extend((escape(data[cursor:start], quote=False), '<mark class="feedbackHighlight">',
                          escape(data[start:end], quote=False), '</mark>'))
            cursor = end
        parts.append(escape(data[cursor:], quote=False))
        rendered = ''.join(parts)
        self.parts.append(f'<span data-feedback-text="{token}">{rendered}</span>' if self.annotate else rendered)

    def handle_comment(self, data):
        self.parts.append('<!--' + data + '-->')

    def handle_decl(self, decl):
        self.parts.append('<!' + decl + '>')


def validate_selection(html, selection, expected_hash):
    if not selection:
        return
    if expected_hash != content_hash(html):
        raise DomainError('feedback_content_changed', '标注的内容已变化，请重新打开预览后选择。', 409)
    tokens = FeedbackContent(html).tokens
    if any(token >= len(tokens) or start < 0 or end <= start or end > tokens[token]
           for token, start, end in selection):
        raise DomainError('invalid_feedback_selection', '所选内容位置无效，请重新选择。', 422)
