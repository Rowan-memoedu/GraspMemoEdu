"""Mechanical adapter for independently stored sections, chunks, points and cards."""
from copy import deepcopy
import base64
import hashlib
from html import escape
from html.parser import HTMLParser
import json
import re
import sys
import xml.etree.ElementTree as ET

from .atomic_content import plain_text, validate_atomic, validate_source_attributions
from .topic_runtime import validate_runtime

FORMAT = 'structured-atomic-v1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class SourceHTML(HTMLParser):
    """Preserve source text/whitespace, remove link wrappers and bind image bytes."""
    allowed = {'p', 'ul', 'ol', 'li', 'pre', 'code', 'strong', 'em', 'b', 'i', 'u', 's', 'span', 'div',
               'blockquote', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'br', 'hr', 'sub', 'sup',
               'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'img'}

    def __init__(self, source, images):
        super().__init__(convert_charrefs=False)
        self.parts, self.images = [], images
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            return
        if tag not in self.allowed:
            raise ValueError('Unsafe source HTML element: ' + tag)
        values = dict(attrs)
        if any(key.lower().startswith('on') for key in values):
            raise ValueError('Source event handlers are forbidden')
        safe = []
        if tag == 'img':
            original = values.get('src')
            if original not in self.images:
                raise ValueError('Source image has no immutable embedded bytes')
            safe = [('src', self.images[original]['data_url']), ('alt', values.get('alt', '')),
                    ('class', 'topicDiagram'), ('style', 'max-width:100%;height:auto')]
        else:
            for key in ('class', 'colspan', 'rowspan', 'start'):
                if key in values:
                    if not re.fullmatch(r'[A-Za-z0-9_ -]+', values[key] or ''):
                        raise ValueError('Unsafe source attribute')
                    safe.append((key, values[key]))
        self.parts.append('<' + tag + ''.join(' ' + k + '="' + escape(v, quote=True) + '"' for k, v in safe) + '>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'a':
            return
        if tag not in self.allowed:
            raise ValueError('Unsafe source HTML element')
        if tag not in ('img', 'br', 'hr'):
            self.parts.append('</' + tag + '>')

    def handle_data(self, value):
        self.parts.append(escape(value, quote=False))

    def handle_entityref(self, name):
        self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        self.parts.append('&#' + name + ';')

    def handle_comment(self, data):
        raise ValueError('Source HTML comments are unsupported')


def validate_source(source):
    if source.get('source_format') != FORMAT:
        raise ValueError('Unknown structured atomic source')
    images = source.get('embedded_images', {})
    for url, record in images.items():
        data_url = record['data_url']
        if not re.fullmatch(r'data:image/(?:png|jpeg|webp|gif|svg\+xml);base64,[A-Za-z0-9+/=]+', data_url):
            raise ValueError('Invalid embedded source image')
        raw = base64.b64decode(data_url.split(',', 1)[1], validate=True)
        if not raw or len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('Embedded source image bytes changed')
        if data_url.startswith('data:image/svg+xml;'):
            if b'<!ENTITY' in raw.upper():
                raise ValueError('SVG entities are forbidden')
            root = ET.fromstring(raw)
            if root.tag.split('}')[-1] != 'svg':
                raise ValueError('Invalid source SVG')
            for node in root.iter():
                if node.tag.split('}')[-1].lower() in ('script', 'foreignobject', 'iframe', 'object', 'embed', 'a'):
                    raise ValueError('Active source SVG is forbidden')
                for key, value in node.attrib.items():
                    name = key.split('}')[-1].lower()
                    if name.startswith('on') or name in ('href', 'src') and not value.startswith('#'):
                        raise ValueError('External source SVG reference')
                if re.search(r'@import|url\(\s*[\'\"]?(?!#)[^)]*://', node.text or '', re.I):
                    raise ValueError('External SVG stylesheet')
    return images


def normalize(source):
    images = validate_source(source)
    topic = {'id': source['id'], 'title': source['title'], 'subject_id': source['subject_id'],
             'training_mode': 'atomic_retrieval', 'topic_format_version': 2, 'topic_kind': 'lesson',
             'introductions': [], 'content_order': [], 'modules': [], 'memory_chunks': [], 'flashcards': []}
    for section in source['sections']:
        if not section.get('import_eligible') or section.get('status') != 'ai_passed':
            raise ValueError('Unapproved source section')
        intro = section['introduction']
        rendered = ''.join(SourceHTML(intro['html'], images).parts)
        if plain_text(rendered) != intro['plain_text']:
            raise ValueError('Introduction text changed while binding source HTML')
        metadata = {}
        if 'source_attributions' in section:
            validate_source_attributions(section['source_attributions'])
            if section['source_attributions']:
                metadata['source_attributions'] = deepcopy(section['source_attributions'])
        topic['introductions'].append({'id': section['id'], 'title': section['section_title'], 'html': rendered, **metadata})
        topic['content_order'].append({'kind': 'introduction', 'id': section['id']})
        assets = {a['id']: a for a in section.get('source_assets', [])}
        chunks = {c['id'] for c in section['memory_chunks']}
        if any(p['chunk_id'] not in chunks for p in section['memory_points']):
            raise ValueError('Unbound source memory point')
        for chunk in section['memory_chunks']:
            mnemonic = chunk.get('mnemonic', {})
            snippets = [assets[a]['markdown'] for a in mnemonic.get('asset_ids', []) if assets[a]['kind'] != 'image']
            snippets.extend(mnemonic.get('source_quotes', []))
            topic['memory_chunks'].append({**deepcopy(chunk), **deepcopy(metadata), 'title': chunk.get('title', '记忆组块 ' + chunk.get('local_id', chunk['id'])),
                'introduction_id': section['id'], 'mnemonic_markdown': '\n\n'.join(snippets),
                'mnemonic_html': ''.join(''.join(SourceHTML(assets[a]['html'], images).parts)
                                        for a in mnemonic.get('asset_ids', []) if assets[a]['kind'] == 'image'),
                'points': [{'id': p['id'], 'text': p['content']} for p in section['memory_points'] if p['chunk_id'] == chunk['id']]})
        for card in section['flashcards']:
            answer = card.get('reference_answer', card.get('answer'))
            topic['flashcards'].append({**deepcopy(card), 'title': card.get('title', '原子卡片'),
                'reference_answer': answer, 'explanation': card.get('explanation', answer),
                'interaction': card.get('interaction', {'version': 1, 'type': 'text', 'grading': 'semantic'}),
                'answer_type': 'text', 'constraints': '依据题干要求和来源知识判断作答是否等价。'})
    topic['introduction'] = topic['introductions'][0]
    if 'reading_materials' in source:
        topic['reading_materials'] = deepcopy(source['reading_materials'])
    topic['content_version'] = fingerprint(source)
    return topic


def validate_projection(topic, source):
    expected = normalize(source)
    for key in ('id', 'title', 'subject_id', 'introductions', 'content_order', 'modules', 'content_version'):
        if topic[key] != expected[key]:
            raise ValueError('Structured source mapping differs: ' + key)
    if topic.get('reading_materials') != expected.get('reading_materials'):
        raise ValueError('Structured reading materials changed')
    for key in ('memory_chunks', 'flashcards'):
        if len(topic[key]) != len(expected[key]):
            raise ValueError('Structured source coverage differs')
        for actual, wanted in zip(topic[key], expected[key]):
            for field, value in wanted.items():
                if field in ('mnemonic_markdown', 'mnemonic_html', 'explanation', 'interaction'):
                    continue
                if actual.get(field) != value:
                    raise ValueError('Structured source field changed: ' + field)
            if key == 'flashcards':
                # Only these display collections gain rendered HTML in the
                # adapter. Composite sections own their HTML in the source;
                # stripping it recursively would discard question content.
                interaction = deepcopy(actual['interaction'])
                for collection in ('left', 'right', 'statements', 'options'):
                    for index, item in enumerate(interaction.get(collection, [])):
                        originals = wanted['interaction'].get(collection, [])
                        if index < len(originals) and 'html' not in originals[index]:
                            item.pop('html', None)
                if interaction != wanted['interaction']:
                    raise ValueError('Structured interaction changed')
    validate_runtime(topic)
    validate_atomic(topic)


def main():
    value = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    if sys.argv[1] == 'normalize':
        result = normalize(value)
    else:
        value['version'] = fingerprint(value)
        validate_runtime(value)
        validate_atomic(value)
        result = value
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    main()
