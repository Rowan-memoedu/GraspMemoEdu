"""Move source book images to the end of their owning field, without redrawing."""
import base64
from copy import deepcopy
import hashlib
from html import escape, unescape
from html.parser import HTMLParser
import re

MARKDOWN_IMAGE = re.compile(r'!\[[^\]]*\]\([^\n)]+\)')
LABEL_LINE = re.compile(r'^[ \t]*((?:[A-L①-⑳](?:[.．、])?\s*)+)$', re.M)
PARAGRAPH = re.compile(r'<p\b[^>]*>([^<>]*)</p>', re.I)
LABEL = re.compile(r'[A-L①-⑳]')


def image_sha256(tag):
    class Image(HTMLParser):
        source = ''
        def handle_starttag(self, name, attrs):
            if name == 'img': self.source = dict(attrs).get('src', '')
        handle_startendtag = handle_starttag
    parser = Image(); parser.feed(tag)
    match = re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=\s]+)', unescape(parser.source))
    return hashlib.sha256(base64.b64decode(match[1], validate=True)).hexdigest() if match else None


def image_tags(html):
    # Only raster <img> assets: inline MathJax SVG and its glyphs stay in place.
    return re.findall(r'<img\b[^>]*>', html, re.I)


def field_layout(html, *, owner, question_id, excluded=(), markdown=None):
    if 'class="bookFigures"' in html:
        return html, markdown  # Already materialized; preserve image numbering.
    tags = image_tags(html)
    if not tags: return html, markdown
    kept = [(i, tag) for i, tag in enumerate(tags) if image_sha256(tag) not in excluded]
    captions, caption_spans, prefix_span = [], [], None
    first_image = list(re.finditer(r'<img\b[^>]*>', html, re.I))[kept[0][0]].start() if kept else len(html)
    for match in PARAGRAPH.finditer(html):
        if match.start() < first_image: continue
        value = unescape(match[1]).strip()
        if value == '图':
            prefix_span = match.span(); continue
        if re.fullmatch(r'(?:[A-L①-⑳][.．、]?\s*)+', value):
            captions.extend(LABEL.findall(value)); caption_spans.append(match.span())
        elif re.fullmatch(r'(?:图\s*)?[（(]\s*\d{1,2}\s*[)）]', value) and (value.startswith('图') or prefix_span):
            captions.append('图' + re.search(r'[（(].*[)）]', value)[0])
            caption_spans.append(match.span())
            if prefix_span: caption_spans.append(prefix_span)
        prefix_span = None
    if len(captions) not in (len(tags), len(kept)) or not captions:
        captions, caption_spans = [], []
    for start, end in sorted(caption_spans, reverse=True): html = html[:start] + html[end:]
    html = re.sub(r'<img\b[^>]*>', '', html, flags=re.I)
    html = re.sub(r'<p\b[^>]*>\s*</p>', '', html, flags=re.I)
    figures = []
    for ordinal, (index, tag) in enumerate(kept, 1):
        label = captions[index if len(captions) == len(tags) else ordinal - 1] if captions else '图' + str(ordinal)
        caption = owner + ' · ' + label
        figures.append('<figure class="bookFigure" data-question-id="' + escape(question_id, quote=True)
                       + '" data-image-sha256="' + (image_sha256(tag) or '')
                       + '" style="margin:16px 0"><figcaption>' + escape(caption)
                       + '</figcaption>' + tag + '</figure>')
    if figures: html += '<div class="bookFigures">' + ''.join(figures) + '</div>'
    if markdown is not None:
        images = list(MARKDOWN_IMAGE.finditer(markdown))
        # The frozen Markdown and compiled HTML must agree before moving any
        # source token. Legacy snapshots can lack Markdown; HTML still works.
        if len(images) == len(tags):
            if captions:
                markdown = LABEL_LINE.sub(lambda m: '' if LABEL.findall(m[1]) == captions else m[0], markdown)
                for caption in captions:
                    if caption.startswith('图'):
                        number = re.search(r'\d+', caption)[0]
                        markdown = re.sub(r'(?m)^\s*图\s*[（(]\s*' + number + r'\s*[)）]\s*$', '', markdown)
                images = list(MARKDOWN_IMAGE.finditer(markdown))
            body = MARKDOWN_IMAGE.sub('', markdown).strip()
            tail = [owner + ' · ' + (captions[i if len(captions) == len(tags) else n - 1] if captions else '图' + str(n)) + '\n\n' + images[i][0]
                    for n, (i, _) in enumerate(kept, 1)]
            markdown = re.sub(r'\n\s*\n+', '\n\n', body) + ('\n\n' + '\n\n'.join(tail) if tail else '')
    return html, markdown


def present_media(question, media=None):
    result = deepcopy(question)
    excluded = (media or {}).get('excluded_images', {})
    owner = question.get('title', question.get('id', '配图'))
    result['html'], result['question'] = field_layout(question['html'], owner=owner,
        question_id=question.get('id', ''), excluded=excluded, markdown=question.get('question'))
    result['explanation_html'], _ = field_layout(question['explanation_html'], owner=owner + ' · 解析',
        question_id=question.get('id', ''), excluded=excluded)
    for index, option in enumerate((result.get('interaction') or {}).get('options', [])):
        option['html'], option['text'] = field_layout(option.get('html', ''),
            owner=owner + ' · 选项' + chr(65 + index), question_id=question.get('id', ''),
            excluded=excluded, markdown=option['text'])
    return result


def validate_media(media, bank, variants):
    if (not isinstance(media, dict) or set(media) != {'excluded_images', 'watermark_policy'}
            or media['watermark_policy'] != 'preserve_embedded'
            or not isinstance(media['excluded_images'], dict)):
        raise ValueError('Invalid book media policy')
    hashes = set()
    for group in bank['groups']:
        for question in group['questions']:
            fields = [question['html'], question['explanation_html']]
            variant = variants.get(question['id'], {})
            fields += [variant.get('html', '')]
            fields += [o.get('html', '') for o in variant.get('interaction', {}).get('options', [])]
            hashes.update(image_sha256(tag) for html in fields for tag in image_tags(html))
    for sha, reason in media['excluded_images'].items():
        if not re.fullmatch(r'[0-9a-f]{64}', sha) or sha not in hashes or reason not in ('qr_code', 'watermark'):
            raise ValueError('Media exclusion must bind to an existing source image')
    return media
