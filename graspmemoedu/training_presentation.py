"""Mechanical book typography over source text and already compiled HTML."""
from html.parser import HTMLParser
import re

TYPE_LABEL = re.compile(r'[（(【]?\s*(?:多项选择题|单项选择题|多选题|单选题)\s*[）)】]?')
SUBNUMBER = re.compile(r'[（(]\s*\d{1,2}\s*[）)](?!\s*(?:式|得|可知|和|、|，|。|的))|[①-⑳]')
PROTECTED = re.compile(r'\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|!\[[^\]]*\]\([^\n)]+\)')


def prose_layout(text, *, choice=False):
    def plain(value):
        if choice:
            value = TYPE_LABEL.sub('', value)
        return SUBNUMBER.sub(lambda m: '\n\n' + m[0], value)
    pieces, start = [], 0
    for match in PROTECTED.finditer(text):
        pieces.extend((plain(text[start:match.start()]), match[0]))
        start = match.end()
    pieces.append(plain(text[start:]))
    return re.sub(r'[ \t]*\n(?:[ \t]*\n)+', '\n\n', ''.join(pieces)).strip()


def html_layout(html, *, choice=False):
    # OCR answer headings such as "4. 解析" can become a one-item Markdown
    # ordered list. Unwrap that artificial indent while retaining the number.
    def source_paragraph(match):
        body = match['body']
        if re.search(r'<(?:ol|li)\b', body):
            return match[0]
        start = re.search(r'\bstart=["\'](\d+)["\']', match['attrs'])
        return '<div class="bookSourceParagraph">' + (start[1] if start else '1') + '. ' + body + '</div>'
    html = re.sub(r'<ol\b(?P<attrs>[^>]*)>\s*<li>(?P<body>[\s\S]*?)</li>\s*</ol>', source_paragraph, html)
    class Layout(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.parts, self.protected, self.block_start = [], 0, True

        def handle_starttag(self, tag, attrs):
            self.parts.append(self.get_starttag_text())
            if tag in ('svg', 'math', 'pre', 'code'):
                self.protected += 1
            if not self.protected and tag in ('p', 'div', 'li', 'br'):
                self.block_start = True

        def handle_startendtag(self, tag, attrs):
            self.parts.append(self.get_starttag_text())

        def handle_endtag(self, tag):
            self.parts.append('</' + tag + '>')
            if tag in ('svg', 'math', 'pre', 'code'):
                self.protected -= 1
                self.block_start = False
            elif not self.protected and tag in ('p', 'div', 'li'):
                self.block_start = True

        def handle_data(self, text):
            if self.protected:
                self.parts.append(text)
                return
            if choice:
                text = TYPE_LABEL.sub('', text)
            cursor = 0
            for match in SUBNUMBER.finditer(text):
                prefix = text[cursor:match.start()]
                if prefix.strip():
                    self.block_start = False
                self.parts.append(prefix)
                if not self.block_start:
                    self.parts.append('<br class="bookSubnumberBreak"><br class="bookSubnumberBreak">')
                self.parts.append(match[0] + ('' if text[match.end():match.end() + 1].isspace() else ' '))
                self.block_start, cursor = False, match.end()
            self.parts.append(text[cursor:])
            if text[cursor:].strip():
                self.block_start = False

        def handle_entityref(self, name):
            self.parts.append('&' + name + ';')

        def handle_charref(self, name):
            self.parts.append('&#' + name + ';')

        def handle_comment(self, data):
            self.parts.append('<!--' + data + '-->')

    parser = Layout(); parser.feed(html); parser.close()
    return ''.join(parser.parts)


def present_book_question(question):
    choice = (question.get('interaction') or {}).get('type') == 'choice'
    return {**question, 'question': prose_layout(question['question'], choice=choice),
            'html': html_layout(question['html'], choice=choice),
            'explanation_html': html_layout(question['explanation_html'])}
