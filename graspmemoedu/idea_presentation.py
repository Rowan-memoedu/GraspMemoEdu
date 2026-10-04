"""Put authored Idea fields on the answer side without rewriting stored Topics."""
from functools import lru_cache
from html.parser import HTMLParser
import re

ANSWER_HEAD = re.compile(r'^\s*(?:<div\b[^>]*\bid="node-[^"]*"[^>]*>\s*)?'
                         r'(?:<p>\s*)?(?:<strong>\s*(?:idea|巧法)\s*</strong>\s*[:：]'
                         r'|<a\b[^>]*\bdata-target-rem-id="[^"]+"[^>]*>(?:(?!</?a\b).)*</a>\s*</p>)', re.I | re.S)
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


class _OutlineSpans(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source, self.stack, self.spans = source, [], []
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer('\n', source))

    def source_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            start = self.source_offset()
            self.stack.append((tag, start, start + len(self.get_starttag_text()),
                               'outlineNode' in dict(attrs).get('class', '').split()))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                _, start, inner, outline = self.stack[index]
                del self.stack[index:]
                if outline and ANSWER_HEAD.match(self.source[inner:self.source_offset()]):
                    self.spans.append((start, self.source.index('>', self.source_offset()) + 1, tag))
                break


@lru_cache(maxsize=256)
def split_ideas(html):
    """Return unchanged stem bytes, answer blocks and stable IDs moved with them."""
    if not re.search(r'<strong>\s*(?:idea|巧法)\s*</strong>\s*[:：]|data-target-rem-id=', html, re.I):
        return html, '', frozenset()
    parser = _OutlineSpans(html)
    parser.feed(html)
    spans = []
    for span in sorted(parser.spans, key=lambda s: (s[0], -s[1])):
        if not spans or span[0] >= spans[-1][1]:
            spans.append(span)
    if not spans:
        return html, '', frozenset()
    blocks, ids, stem, cursor = [], set(), [], 0
    for start, end, tag in spans:
        fragment = html[start:end]
        blocks.append('<ul>' + fragment + '</ul>' if tag == 'li' else fragment)
        ids.update(re.findall(r'\bid="node-([A-Za-z0-9_.-]+)"', fragment))
        stem.append(html[cursor:start])
        cursor = end
    stem.append(html[cursor:])
    return ''.join(stem), ''.join(blocks), frozenset(ids)


def present_question(question, original=None):
    stem, ideas, _ = split_ideas(question['html'])
    # A frozen bank may have replaced the stem; source answer fields still belong
    # to its unchanged answer side. Never modify hashes, keys or the source.
    source_ideas = split_ideas(original['html'])[1] if original else ''
    ideas = source_ideas or ideas
    if not ideas:
        return question
    return {**question, 'html': stem, 'explanation_html': ideas + question['explanation_html']}
