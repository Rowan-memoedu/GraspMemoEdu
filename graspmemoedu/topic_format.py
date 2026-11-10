"""Versioned, source-preserving Topic author format (no I/O or model calls).

Markdown and JSON enter the same normalizer. The CLI is a stdin/stdout bridge
for the existing JavaScript compiler; it does not create a second importer.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys


VERSION = 2
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
REM_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
HEADER = re.compile(r'<!-- topic-format: (\{.*\}) -->\s*\Z')
ANNOTATION = re.compile(r'^(.*) <!-- topic-node: (\{.*\}) -->\s*$')
REFERENCE = re.compile(r'\[\[id:([A-Za-z0-9_-]{1,128})\|([^\]\n|]+)\]\]')
IMAGE = re.compile(r'!\[([^\]\n]*)\]\(([^)\n]+)\)')
EXTERNAL = re.compile(r'(?:https?://|ftp://|mailto:|www\.)', re.I)
PURE_EXTERNAL = re.compile(r'(?:\[[^\]\n]*\]\((?:https?://|ftp://|mailto:)[^\s)]+\)|<?(?:https?://|ftp://|mailto:|www\.)[^\s<>]+>?)\Z', re.I)
INTRODUCTION = re.compile(r'^Introduction(?:\s*[:：]\s*(.+))?$', re.I)
EXAMPLE = re.compile(r'^Example\s+([1-9][0-9]*)(?:\s*[:：].*)?$', re.I)
PRACTICE = re.compile(r'^Practice\s+([1-9][0-9]*)(?:\.([1-9][0-9]*))?$', re.I)
CALLOUTS = {'hint': 'hint', 'aside': '题外话', 'requirement': '要求', 'source': '题源'}
CALLOUT_ALIASES = {**{k: k for k in CALLOUTS}, **{v: k for k, v in CALLOUTS.items()}}
SOURCE_FIELDS = ('name', 'year', 'edition', 'volume', 'page', 'item', 'location', 'adaptation')
NODE_FIELDS = {'id', 'text', 'children', 'source_rem_id', 'source_tags', 'callout', 'source', 'body_layout', 'presentation',
               'kind', 'reference', 'section_id', 'example', 'footnotes'}
ROOT_FIELDS = {'topic_format_version', 'id', 'title', 'topic_kind', 'source_rem_id', 'nodes', 'sections', 'reading_lineage'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(value, label='node ID'):
    require(isinstance(value, str) and IDENTIFIER.fullmatch(value), 'Invalid ' + label)
    return value


def _rem_id(value):
    require(isinstance(value, str) and REM_ID.fullmatch(value), 'Invalid source/target Rem ID')
    return value


def is_v2_source(source):
    value = source.lstrip('\ufeff \r\n\t')
    if value.startswith('<!-- topic-format:'):
        return True
    if value.startswith('{'):
        try:
            document = json.loads(value)
        except (ValueError, RecursionError):
            return False
        return isinstance(document, dict) and 'topic_format_version' in document
    return False


def _without_fences(text):
    """Exclude literal code from link/idea classification, including Mermaid."""
    result, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence and not marker[2].strip():
                fence = None
            continue
        if fence is None:
            result.append(line)
    require(fence is None, 'Unclosed code fence')
    return re.sub(r'(`+)([^\n]*?)\1', '', '\n'.join(result))


def references_in_text(text):
    prose = _without_fences(text)
    # Nested brackets in a formula are mathematical notation, not Rem links.
    prose = re.sub(r'\$\$[\s\S]*?\$\$|\$[^$\n]+\$', '', prose)
    rows = [{'target_rem_id': m[1], 'label': m[2]} for m in REFERENCE.finditer(prose)]
    require('[[' not in REFERENCE.sub('', prose) and ']]' not in REFERENCE.sub('', prose),
            'Internal references require [[id:<exact Rem ID>|display text]]; titles are not identities')
    return rows


def source_label(source):
    """No inferred fields, empty placeholders, URL, or difficulty in student text."""
    return '｜'.join(source[key] for key in SOURCE_FIELDS if source.get(key))


def _markdown_document(source):
    lines = source.lstrip('\ufeff').splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    match = HEADER.fullmatch(lines[first].strip()) if first is not None else None
    require(match is not None, 'v2 Markdown needs one topic-format header')
    metadata = json.loads(match[1])
    require(isinstance(metadata, dict) and not set(metadata) - (ROOT_FIELDS - {'title', 'nodes'}),
            'Unknown v2 Markdown header fields')
    roots, stack, fence = [], [], None
    for number, line in enumerate(lines[first + 1:], first + 2):
        found = re.match(r'^( *)- (.*)$', line) if fence is None else None
        if found:
            indent, head = len(found[1]), found[2]
            while stack and stack[-1]['_indent'] >= indent:
                stack.pop()
            require(indent == (stack[-1]['_indent'] + 2 if stack else 0),
                    f'Invalid outline indentation at line {number}')
            annotation = ANNOTATION.fullmatch(head)
            node_meta = json.loads(annotation[2]) if annotation else {}
            require(isinstance(node_meta, dict) and not set(node_meta) - (NODE_FIELDS - {'text', 'children'}),
                    f'Unknown topic-node metadata at line {number}')
            node = {**node_meta, 'text': annotation[1] if annotation else head, 'children': [],
                    '_indent': indent, '_line': number}
            (stack[-1]['children'] if stack else roots).append(node)
            stack.append(node)
            logical = node['text']
        else:
            if not line.strip():
                if stack:
                    stack[-1]['text'] += '\n'
                continue
            require(bool(stack), f'Unexpected content at line {number}')
            indent = stack[-1]['_indent'] + 2
            require(len(line) - len(line.lstrip(' ')) >= indent,
                    f'Invalid continuation at line {number}')
            logical = line[indent:]
            stack[-1]['text'] += '\n' + logical
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', logical)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence and not marker[2].strip():
                fence = None
    require(fence is None, 'Unclosed code fence')
    require(len(roots) == 1 and set(roots[0]) == {'text', 'children', '_indent', '_line'},
            'Expected one plain Topic root; root identity belongs in topic-format metadata')
    root = roots[0]
    require('\n' not in root['text'].strip(), 'Topic root title must be one line')

    def clean(node):
        result = {k: v for k, v in node.items() if k not in {'_indent', '_line', 'children'}}
        # Empty separator lines do not belong to paragraph prose.
        result['text'] = result['text'].rstrip()
        value = result['text'].split('\n')
        callout = re.fullmatch(r'> \[!([^\]]+)\]', value[0])
        if callout:
            kind = CALLOUT_ALIASES.get(callout[1].lower())
            require(kind is not None, 'Unknown Callout type')
            require(result.get('callout', kind) == kind, 'Callout marker conflicts with metadata')
            require(all(part == '>' or part.startswith('> ') for part in value[1:]),
                    'Callout continuation lines must start with >')
            result['callout'] = kind
            result['text'] = '\n'.join(part[2:] if part.startswith('> ') else '' for part in value[1:])
        result['children'] = [clean(child) for child in node['children']]
        result['_line'] = node['_line']
        return result

    return {**metadata, 'title': root['text'].strip(), 'nodes': [clean(n) for n in root['children']]}


def normalize_document(value, *, markdown=False):
    require(isinstance(value, dict) and not set(value) - ROOT_FIELDS, 'Unknown or invalid Topic author fields')
    require(value.get('topic_format_version') == VERSION, 'Unsupported Topic format version')
    identifier(value.get('id'), 'Topic ID')
    require(isinstance(value.get('title'), str) and value['title'].strip() and len(value['title']) <= 200 and '\n' not in value['title'],
            'Topic needs a nonempty one-line title')
    require(value.get('topic_kind') in {'lesson', 'introduction'}, 'Invalid Topic kind')
    require(isinstance(value.get('nodes'), list) and value['nodes'], 'Topic needs ordered sections')
    document = {k: value[k] for k in ROOT_FIELDS - {'nodes'} if k in value}
    document['title'] = document['title'].strip()
    from .topic_composition import validate_sections, validate_reference
    sections = validate_sections(value['sections']) if 'sections' in value else {}
    if 'source_rem_id' in value:
        _rem_id(value['source_rem_id'])
    seen, source_ids, ignored = set(), set(), []
    if value.get('source_rem_id'):
        source_ids.add(value['source_rem_id'])

    def normalize(node, depth=1, answer=False):
        require(depth <= 50 and len(seen) < 10000, 'Topic outline is too deep or too large')
        allowed = NODE_FIELDS | ({'_line'} if markdown else set())
        require(isinstance(node, dict) and not set(node) - allowed, 'Unknown node field; refusing to discard it')
        ident = identifier(node.get('id'))
        require(ident not in seen, 'Duplicate node ID: ' + ident)
        seen.add(ident)
        require(isinstance(node.get('children', []), list), 'Node children must be an ordered array')
        text = node.get('text', '')
        require(isinstance(text, str) and len(text) <= 1000000 and '\r' not in text, 'Invalid node text; use LF newlines')
        text = text.rstrip()
        result = {k: copy.deepcopy(v) for k, v in node.items() if k not in {'children', '_line'}}
        result['text'] = text
        if 'section_id' in node:
            require(depth == 1 and node['section_id'] in sections, 'Unknown containing Topic section')
        if 'kind' in node or 'reference' in node:
            require(depth == 1 and node.get('kind') == 'bank_question' and not text.strip()
                    and not node.get('children') and not node.get('example') and not node.get('footnotes'),
                    'Bank questions must reference their source without copied content')
            validate_reference(node.get('reference'))
        if 'source_rem_id' in node:
            rem = _rem_id(node['source_rem_id'])
            require(rem not in source_ids, 'Ambiguous duplicate source Rem ID: ' + rem)
            source_ids.add(rem)
        tags = node.get('source_tags', [])
        require(isinstance(tags, list), 'source_tags must be an array')
        tag_ids = set()
        for tag in tags:
            require(isinstance(tag, dict) and set(tag) == {'tag_rem_id', 'name'}, 'Invalid source tag metadata')
            _rem_id(tag['tag_rem_id'])
            require(isinstance(tag['name'], str) and tag['name'].strip(), 'Source tag name is empty')
            require(tag['tag_rem_id'] not in tag_ids, 'Duplicate source tag')
            tag_ids.add(tag['tag_rem_id'])
        classified = {CALLOUT_ALIASES[tag['name'].lower()] for tag in tags if tag['name'].lower() in CALLOUT_ALIASES}
        require(len(classified) <= 1, 'Conflicting Callout tags require discussion: ' + ident)
        if classified:
            kind = next(iter(classified))
            require(node.get('callout', kind) == kind, 'Callout conflicts with actual source tags: ' + ident)
            result['callout'] = kind
        if 'callout' in result:
            require(result['callout'] in CALLOUTS, 'Unknown Callout type: ' + ident)
            require(not node.get('source_rem_id') or bool(classified),
                    'RemNote Callout needs its real identifying source tag: ' + ident)
        if 'body_layout' in result:
            require(result['body_layout'] == 'prose', 'Unknown body layout: ' + ident)
        if 'presentation' in result:
            style = result['presentation']
            require(isinstance(style, dict) and not set(style) - {'role', 'level', 'lead', 'indent', 'figure_group'}, 'Invalid source presentation')
            require(style.get('role', 'paragraph') in {'paragraph', 'heading', 'run-in', 'list', 'footnote'}, 'Invalid source role')
            require(style.get('indent', 0) in (0, 2), 'Invalid source indent')
            if 'level' in style:
                require(style.get('role') == 'heading' and type(style['level']) is int and 2 <= style['level'] <= 6, 'Invalid source heading level')
            if 'lead' in style:
                lead = style['lead']
                require(style.get('role') == 'run-in' and isinstance(lead, str) and 0 < len(lead) <= 140
                        and text.startswith(lead) and not re.search(r'[$`*\n<>\[\]{}\\]', lead), 'Invalid literal source lead')
            require(style.get('role') != 'run-in' or bool(style.get('lead')), 'Source lead is required')
            if 'figure_group' in style:
                identifier(style['figure_group'], 'source figure group')
        if text.strip().rstrip('：:') in {'待转化参考资料', '**待转化参考资料**'}:
            ignored.append({'node_id': ident, 'reason': 'pending-reference-subtree'})
            return None
        if 'source' in node:
            source = node['source']
            require(result.get('callout') == 'source' and isinstance(source, dict)
                    and not set(source) - set(SOURCE_FIELDS) - {'difficulty', 'url'}, 'Invalid 题源 metadata')
            require(all(isinstance(v, str) for v in source.values()), '题源 fields must be text when present')
            source = {k: v.strip() for k, v in source.items() if v.strip()}
            for key in SOURCE_FIELDS:
                require(not EXTERNAL.search(source.get(key, '')), 'External link mixed with 题源 text requires discussion: ' + ident)
            label = source_label(source)
            require(not text.strip() or text == label, '题源 text differs from its explicit fields: ' + ident)
            result['text'], result['source'] = label, source
            if not label:
                require(not node.get('children'), 'Empty 题源 has other content; discussion required: ' + ident)
                ignored.append({'node_id': ident, 'reason': 'source-without-display-information'})
                return None
        prose = _without_fences(result['text'])
        # PNG assets are a separate validated attachment channel, never links to discard.
        require(not any(EXTERNAL.search(m[2]) for m in IMAGE.finditer(result['text'])),
                'Remote images must be supplied through the local attachment contract')
        if PURE_EXTERNAL.fullmatch(prose.strip()) and prose.strip() == result['text'].strip():
            require(not node.get('children'), 'External reference has other child content; discussion required: ' + ident)
            ignored.append({'node_id': ident, 'reason': 'external-reference'})
            return None
        require(not EXTERNAL.search(REFERENCE.sub('', prose)),
                'External link mixed with other body text requires discussion: ' + ident)
        references_in_text(result['text'])
        answer = answer or text.strip().lower() == 'explanation'
        if answer and REFERENCE.fullmatch(result['text'].strip()):
            result['text'] = '**idea**：' + result['text'].strip()
        result['children'] = [converted for child in node.get('children', [])
                              if (converted := normalize(child, depth + 1, answer)) is not None]
        for field in ('example', 'footnotes'):
            if field in node:
                require(depth == 1 and INTRODUCTION.fullmatch(text) and isinstance(node[field], list),
                        'Reading example and footnotes belong to an Introduction subblock')
                result[field] = [converted for child in node[field]
                                 if (converted := normalize(child, depth + 1, answer)) is not None]
        require(result['text'].strip() or result['children'] or node.get('kind') == 'bank_question', 'Empty content node: ' + ident)
        if markdown:
            result['_line'] = node.get('_line', 1)
        return result

    document['nodes'] = [result for node in value['nodes'] if (result := normalize(node)) is not None]
    if sections:
        require(all(n.get('section_id') in sections for n in document['nodes']), 'Every subblock needs its authored Topic section')
    introductions, modules = [], []
    for node in document['nodes']:
        if node.get('kind') == 'bank_question':
            continue
        require('\n' not in node['text'] and not node.get('callout'), 'Top-level section must be Introduction or Example')
        if INTRODUCTION.fullmatch(node['text']):
            introductions.append(node)
            require(node['children'] or node.get('example') or node.get('footnotes'), 'Empty Introduction: ' + node['id'])
        else:
            found = EXAMPLE.fullmatch(node['text'])
            require(found and int(found[1]) == len(modules) + 1, 'Example numbers must be consecutive')
            require(introductions, 'Every Example must follow its Introduction')
            require(node['id'] == f'example-{found[1]}', 'Example section ID must equal its module ID')
            modules.append(node)
    require(introductions or any(n.get('kind') == 'bank_question' for n in document['nodes']), 'Topic needs reading or a bank question')
    require((bool(modules) and document['topic_kind'] == 'lesson')
            or (not modules and document['topic_kind'] == 'introduction'), 'Topic kind disagrees with its sections')
    if 'reading_lineage' in document:
        from .topic_composition import validate_reading_lineage
        validate_reading_lineage(document['reading_lineage'], {n['id'] for n in introductions})
    return document, ignored


def parse_source(source):
    require(isinstance(source, str) and len(source.encode('utf-8')) <= 32 * 1024 * 1024, 'Source too large')
    raw = source.lstrip('\ufeff \r\n\t')
    markdown = not raw.startswith('{')
    value = _markdown_document(source) if markdown else json.loads(raw)
    return normalize_document(value, markdown=markdown)


def validate_field_placement(document):
    """Validate new author output without changing immutable legacy source packages."""
    destinations = {**{name: 'introduction' for name in ('定义', '定理', '性质', '识别特征', '范式')},
                    '要求': 'question', '题源': 'question', '巧法': 'answer', 'idea': 'answer'}

    def walk(node, area, in_idea=False):
        text = node['text']
        if INTRODUCTION.fullmatch(text):
            area = 'introduction'
        elif EXAMPLE.fullmatch(text) or PRACTICE.fullmatch(text):
            area = 'question'
        elif text.casefold() == 'explanation':
            area = 'answer'
        elif text.casefold() == 'interaction':
            return
        head = re.match(r'^\*\*([^*]+)\*\*\s*[:：]', text)
        field = head[1].casefold() if head else None
        field = {'requirement': '要求', 'source': '题源'}.get(node.get('callout'), field)
        if REFERENCE.fullmatch(text.strip()) and not in_idea:
            field = 'idea'
        if field in destinations:
            require(area == destinations[field], f"Field {field} belongs to {destinations[field]}: {node['id']}")
        for key in ('children', 'example', 'footnotes'):
            for child in node.get(key, []):
                walk(child, area, in_idea or field == 'idea')

    for section in document['nodes']:
        walk(section, None)


def _metadata(node):
    return {key: value for key, value in node.items() if key not in {'text', 'children', '_line'}}


def to_outline(document):
    def convert(node, indent):
        parts = node['text'].split('\n')
        # Structural headings are kept compatible with existing question collection.
        head = 'Introduction' if INTRODUCTION.fullmatch(parts[0]) else parts[0]
        meta = _metadata(node)
        for field in ('example', 'footnotes'):
            if field in node:
                meta[field] = [convert(n, indent + 2) for n in node[field]]
        return {'head': head, 'lines': parts[1:], 'line': node.get('_line', 1), 'indent': indent,
                'meta': meta, 'children': [convert(n, indent + 2) for n in node['children']]}
    return {'head': document['title'], 'lines': [], 'line': 1, 'indent': 0,
            'meta': {k: v for k, v in document.items() if k not in {'title', 'nodes'}},
            'children': [convert(n, 2) for n in document['nodes']]}


def source_text(source):
    """Text/asset scanners must consume parsed JSON values, not JSON escapes."""
    if not is_v2_source(source):
        return source
    document, _ = parse_source(source)
    def visit(node):
        return [node['text'], *(text for field in ('children', 'example', 'footnotes')
                               for child in node.get(field, []) for text in visit(child))]
    return '\n'.join([*(s['title'] for s in document.get('sections', [])),
                      *(text for node in document['nodes'] for text in visit(node))])


def content_metadata(document):
    nodes, references, order = [], [], []

    def add(node, section, parent, owner, structural=False):
        record = {'id': node['id'], 'section_id': section, 'parent_id': parent, 'owner': dict(owner)}
        for key in ('source_rem_id', 'source_tags', 'callout'):
            if key in node:
                record[key] = copy.deepcopy(node[key])
        if 'source' in node:
            record['source'] = {k: v for k, v in node['source'].items() if k != 'url'}
        nodes.append(record)
        if not structural:
            references.extend({'source_node_id': node['id'], **ref} for ref in references_in_text(node['text']))
            for child in node['children']:
                add(child, section, node['id'], owner)

    for section in document['nodes']:
        section_id = section['id']
        if section.get('kind') == 'bank_question':
            order.append({'kind': 'bank_question', 'id': section_id})
            continue
        if INTRODUCTION.fullmatch(section['text']):
            order.append({'kind': 'introduction', 'id': section_id})
            owner = {'kind': 'introduction', 'id': section_id, 'field': 'html'}
            add(section, section_id, None, owner, True)
            for child in section['children']:
                add(child, section_id, section_id, owner)
            for field in ('example', 'footnotes'):
                for child in section.get(field, []):
                    add(child, section_id, section_id, {**owner, 'field': field})
            continue
        order.append({'kind': 'example', 'id': section_id})
        practices = [n for n in section['children'] if PRACTICE.fullmatch(n['text'])]
        for index, question in enumerate([section, *practices]):
            qid = f'{section_id}-question' if index == 0 else f'{section_id}-practice-{index}'
            owner = {'kind': 'example' if index == 0 else 'practice', 'id': qid, 'field': 'html'}
            add(question, section_id, None if index == 0 else section_id, owner, True)
            for child in question['children']:
                if child in practices or child['text'].lower() == 'interaction':
                    continue
                if child['text'].lower() == 'explanation':
                    explanation_owner = {**owner, 'field': 'explanation_html'}
                    add(child, section_id, question['id'], explanation_owner, True)
                    for body in child['children']:
                        add(body, section_id, child['id'], explanation_owner)
                else:
                    add(child, section_id, question['id'], owner)
    return {'content_order': order, 'content_nodes': nodes, 'references': references}


def semantic_payload(document, assets=()):
    by_path = {a['path']: a['sha256'] for a in assets}
    def node_payload(node):
        text = node['text']
        if by_path:
            text = IMAGE.sub(lambda m: f'![{m[1]}](sha256:{by_path[m[2]]})', text)
        result = {'id': node['id'], 'text': text, 'callout': node.get('callout'),
                  'children': [node_payload(child) for child in node['children']]}
        for field in ('example', 'footnotes'):
            if field in node:
                result[field] = [node_payload(child) for child in node[field]]
        for key in ('kind', 'reference', 'section_id'):
            if key in node:
                result[key] = node[key]
        return result
    return {**({'sections': document['sections']} if 'sections' in document else {}),
            'topic_kind': document['topic_kind'], 'title': document['title'],
            'nodes': [node_payload(node) for node in document['nodes']]}


def content_version(document, assets=()):
    raw = json.dumps(semantic_payload(document, assets), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def dump_json(document):
    def clean(node):
        return {**{k: v for k, v in node.items() if k not in {'_line', 'children'}},
                'children': [clean(c) for c in node['children']]}
    return json.dumps({**document, 'nodes': [clean(n) for n in document['nodes']]}, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def dump_markdown(document):
    compact = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    meta = {k: v for k, v in document.items() if k not in {'title', 'nodes'}}
    lines = ['<!-- topic-format: ' + compact(meta) + ' -->', '- ' + document['title']]
    def emit(node, indent):
        metadata = _metadata(node)
        annotation = ' <!-- topic-node: ' + compact(metadata) + ' -->'
        prefix = ' ' * indent
        parts = node['text'].split('\n')
        if node.get('callout'):
            lines.append(prefix + '- > [!' + node['callout'] + ']' + annotation)
            lines.extend(prefix + '  > ' + part for part in parts)
        else:
            lines.append(prefix + '- ' + parts[0] + annotation)
            lines.extend(prefix + '  ' + part for part in parts[1:])
        for child in node['children']:
            emit(child, indent + 2)
    for node in document['nodes']:
        emit(node, 2)
    return '\n'.join(lines) + '\n'


def main():
    payload = json.load(sys.stdin)
    document, ignored = parse_source(payload['source'])
    if payload.get('validate_field_placement'):
        validate_field_placement(document)
    result = {'document': document, 'outline': to_outline(document), 'ignored_nodes': ignored,
              'source_text': source_text(payload['source']),
              **content_metadata(document), 'semantic_payload': semantic_payload(document, payload.get('assets', [])),
              'content_version': content_version(document, payload.get('assets', []))}
    if payload.get('serialize'):
        result.update(markdown=dump_markdown(document), json=dump_json(document))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
