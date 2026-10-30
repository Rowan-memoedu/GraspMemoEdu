"""Mechanical Math Academy JSON import, independent of course names and IDs."""
from collections import defaultdict
from copy import deepcopy
import argparse
import base64
import hashlib
import html
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import zipfile

from .topic_install import encoded, digest, import_lock

ADAPTER_VERSION = 'mathacademy-json-1'
DEFAULT_COURSES = (82, 74, 66, 72, 140)
SOURCE_FIELDS = {'taskId', 'testMode', 'rendered', 'inputAnswers', 'answer', 'answers', 'complete',
                 'difficultyClass', 'difficultySymbol'}
GAP_LABELS = {'body_missing':'原材料缺少正文', 'example_missing':'原材料缺少例题题面',
              'answer_missing':'原材料缺少已确定的标准答案', 'explanation_missing':'原材料缺少解析',
              'practice_count':'每例储备练习不足 7 道', 'section_missing':'原材料缺少后续分段题面',
              'image_missing':'原材料缺少图片', 'unassigned_question':'题目没有明确的例题归属',
              'interaction_missing':'原材料缺少作答结构'}


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def teaching(value):
    if isinstance(value, dict):
        return {k: teaching(v) for k, v in value.items() if k not in SOURCE_FIELDS}
    if isinstance(value, list):
        return [teaching(v) for v in value]
    return value


def source_fingerprint(source):
    return digest(encoded({'adapter': ADAPTER_VERSION, 'lesson': teaching(source['lesson'])}))


class SourceIndex:
    def __init__(self, topics_zip, graphs_zip):
        self.topics_zip, self.graphs_zip = Path(topics_zip), Path(graphs_zip)
        self.graphs, self.nodes, self.parents, self.edge_sources = {}, {}, defaultdict(set), defaultdict(set)
        with zipfile.ZipFile(graphs_zip) as archive:
            self._validate(archive)
            for name in archive.namelist():
                match = re.fullmatch(r'course-knowledge-graph/course-(\d+)\.json', name)
                if not match:
                    continue
                cid = int(match[1]); graph = json.loads(archive.read(name))
                if not isinstance(graph.get('topics'), dict):
                    raise ValueError('Invalid source course graph: ' + name)
                self.graphs[cid] = graph
                for key, node in graph['topics'].items():
                    tid = int(key)
                    if node.get('id') != tid or node['course']['id'] != cid:
                        raise ValueError('Conflicting source graph identity')
                    self.nodes.setdefault(tid, node)
                    for parent in node.get('prerequisites', []):
                        if type(parent) is not int or parent == tid:
                            raise ValueError('Invalid source dependency')
                        self.parents[tid].add(parent)
                        self.edge_sources[(parent, tid)].add(cid)
        self.archive = zipfile.ZipFile(topics_zip)
        self._validate(self.archive)
        self.names = {int(m[1]): name for name in self.archive.namelist()
                      if (m := re.fullmatch(r'topic-json/topic-(\d+)\.json', name))}

    @staticmethod
    def _validate(archive):
        names = set()
        for info in archive.infolist():
            if info.filename in names or info.flag_bits & 1 or info.file_size > 64 * 1024 * 1024:
                raise ValueError('Invalid, encrypted or oversized ZIP entry')
            names.add(info.filename)

    def scope(self, course_ids):
        if not course_ids or set(course_ids) - self.graphs.keys():
            raise ValueError('Unknown source course IDs')
        targets = {int(tid) for cid in course_ids for tid in self.graphs[cid]['topics']}
        closure, pending = set(targets), list(targets)
        while pending:
            for parent in self.parents[pending.pop()]:
                if parent not in closure:
                    closure.add(parent); pending.append(parent)
        active, done = set(), set()
        def visit(tid):
            if tid in active:
                raise ValueError('Source prerequisite cycle')
            if tid in done:
                return
            active.add(tid)
            for parent in self.parents[tid]:
                visit(parent)
            active.remove(tid); done.add(tid)
        for tid in closure:
            visit(tid)
        return {'course_ids': list(course_ids), 'target_topics': len(targets), 'topic_ids': sorted(closure),
                'missing_topics': sorted(closure - self.names.keys()), 'missing_graph_nodes': sorted(closure - self.nodes.keys()),
                'edges': len([1 for parent, child in self.edge_sources if child in closure])}

    def read(self, tid):
        raw = self.archive.read(self.names[tid])
        source = json.loads(raw)
        if source.get('lesson', {}).get('topic', {}).get('id') != tid:
            raise ValueError('Topic source identity mismatch: ' + str(tid))
        return raw, source

    def graphics(self, cache):
        key = file_hash(self.topics_zip)
        target = cache / ('graphics-' + key + '.json')
        if target.exists():
            return json.loads(target.read_text(encoding='utf-8'))
        result = {}
        for index, tid in enumerate(self.names):
            _, source = self.read(tid)
            for step in source['lesson'].get('steps', []):
                for graphic in (step.get(step.get('type')) or {}).get('graphics', []):
                    identity, tex = graphic.get('fileId'), graphic.get('tex')
                    if not identity or not tex:
                        continue
                    if identity in result and result[identity] != tex:
                        raise ValueError('Conflicting graphic identity: ' + str(identity))
                    result[identity] = tex
            if (index + 1) % 1000 == 0:
                print(f'graphics {index + 1}/{len(self.names)}', flush=True)
        target.write_bytes(encoded(result))
        return result


class Assets:
    def __init__(self, root, graphics):
        self.root, self.graphics = root, graphics
        self._compile_locks = defaultdict(threading.Lock)
        root.mkdir(parents=True, exist_ok=True)

    def compile(self, source):
        # Only transport math delimiters are normalized; drawing content stays intact.
        source = re.sub(r'\[math\]([\s\S]*?)\[/math\]', lambda m: '$' + m[1] + '$', source)
        # tkz-euclide 4 loads all objects itself; the old loader no longer exists.
        if '\\usepackage{tkz-euclide}' in source:
            source = source.replace('\\usetkzobj{all}', '')
            source = re.sub(r'\\tkzDrawVector(s?)\s*(?:\[([^\]]*)\])?',
                lambda m: '\\tkzDrawSegment' + m[1] + '[->' + (',' + m[2] if m[2] else '') + ']', source)
        key = digest((ADAPTER_VERSION + ':150dpi:' + source).encode())
        with self._compile_locks[key]:
            return self._compile_png(source, key)

    def _compile_png(self, source, key):
        png = self.root / (key + '.png')
        if not png.exists():
            tectonic = os.environ.get('CODEX_TECTONIC_PATH') or shutil.which('tectonic')
            if not tectonic:
                raise RuntimeError('Tectonic compiler is required for source graphics')
            # Preserve captured CRLF: Windows newline translation would otherwise
            # write CRCRLF, creating spurious paragraphs inside TeX arguments.
            tex = self.root / (key + '.tex'); tex.write_text(source, encoding='utf-8', newline='')
            print('render graphic ' + key[:12], flush=True)
            result = subprocess.run([tectonic, '--untrusted', '--keep-logs', '--outdir', str(self.root), str(tex)],
                                    capture_output=True, text=True, timeout=180)
            (self.root / (key + '.build.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
            if result.returncode:
                raise RuntimeError('TeX graphic conversion failed: ' + key + ': ' + result.stderr[-1000:])
            subprocess.run(['pdftoppm', '-png', '-singlefile', '-r', '150', str(tex.with_suffix('.pdf')), str(png.with_suffix(''))],
                           check=True, capture_output=True, timeout=120)
        return 'data:image/png;base64,' + base64.b64encode(png.read_bytes()).decode('ascii')

    def compile_inline(self, source):
        """Keep source-drawn pixels intact and omit the TeX paper's white margins."""
        from PIL import Image, ImageChops
        original = base64.b64decode(self.compile(source).split(',', 1)[1], validate=True)
        target = self.root / ('inline-' + digest(original) + '-tight-v1.png')
        with self._compile_locks[str(target)]:
            if not target.exists():
                with Image.open(io.BytesIO(original)) as rendered:
                    rendered.load()
                    bounds = ImageChops.difference(rendered.convert('RGB'), Image.new('RGB', rendered.size, 'white')).getbbox()
                    if bounds:
                        left, top, right, bottom = bounds
                        bounds = (max(0, left - 5), max(0, top - 5), min(rendered.width, right + 5), min(rendered.height, bottom + 5))
                        rendered = rendered.crop(bounds)
                    pending = target.with_suffix('.tmp')
                    rendered.save(pending, format='PNG'); os.replace(pending, target)
        return 'data:image/png;base64,' + base64.b64encode(target.read_bytes()).decode('ascii')

    @staticmethod
    def visible_markup(source):
        return re.sub(r'<!--[\s\S]*?-->', '', source or '')

    @staticmethod
    def inline_graphics(source, source_html):
        """Pair inline drawings in two representations of the same source field.

        This uses document positions, never the unrelated graphics attachment order.
        Every explicit image must retain its identity and the complete image sequence
        must agree before any inline TeX can be associated with rendered HTML.
        """
        pattern = (r'\\documentclass[\s\S]*?\\end\{document\}'
                   r'|<img\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>'
                   r'|!\[[^\]]*\]\(([^)]+)\)')
        original = list(re.finditer(pattern, Assets.visible_markup(source), re.I))
        rendered = re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', Assets.visible_markup(source_html), re.I)
        if len(original) != len(rendered):
            return {}
        result = {}
        for event, reference in zip(original, rendered):
            if event[0].startswith('\\documentclass'):
                if not re.fullmatch(r'/graphics/[A-Za-z0-9_-]+', reference):
                    return {}
                if reference in result and result[reference] != event[0]:
                    raise ValueError('Conflicting inline graphic identity: ' + reference)
                result[reference] = event[0]
            elif html.unescape(event[1] or event[2]) != html.unescape(reference):
                return {}
        return result

    def text(self, source, gaps, inline_graphics=None):
        source = self.visible_markup(str(source or ''))
        source = re.sub(r'\\documentclass[\s\S]*?\\end\{document\}',
                        lambda m: '<img alt="Source diagram" src="' + self.compile(m[0]) + '">', source)
        def image(url):
            if url.startswith('data:image/'):
                return url
            # Source controls may use a bare fileId; HTML uses /graphics/<fileId>.
            # A question image is not the image of an option sharing its prefix.
            identity = urlsplit(html.unescape(url)).path.removeprefix('/graphics/')
            if identity in self.graphics:
                return self.compile(self.graphics[identity])
            if inline_graphics and url in inline_graphics:
                return self.compile_inline(inline_graphics[url])
            gaps.append({'code': 'image_missing', 'reference': url})
            return ''
        source = re.sub(r'(<img\b[^>]*\bsrc=["\'])([^"\']+)(["\'])', lambda m: m[1] + image(m[2]) + m[3], source, flags=re.I)
        source = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', lambda m: '<img alt="' + html.escape(m[1], quote=True) + '" src="' + image(m[2]) + '">', source)
        return source

    def render(self, source, source_html, gaps):
        if source_html and source_html.strip():
            return {'_render': source or '', '_html': self.text(source_html, gaps, self.inline_graphics(source, source_html))}
        return {'_render': self.text(source, gaps)}


def controls(question, assets, gaps):
    fmt, qid = question.get('format'), question['id']
    if fmt is None:
        gaps.append({'code': 'interaction_missing', 'question': qid}); return None
    if fmt == 'MC':
        options = [{'id': f'o{i}', 'text': question['choice' + str(i)],
                    'html': assets.render(question['choice' + str(i)], None, gaps)} for i in range(1, 6)
                   if str(question.get('choice' + str(i)) or '').strip()]
        if not options:
            gaps.append({'code': 'interaction_missing', 'question': qid}); return None
        return {'version': 2, 'type': 'choice', 'grading': 'unavailable', 'shuffle': False, 'options': options}
    if fmt not in ('FR', 'SS', 'PR', 'DS', 'CO'):
        raise ValueError('Unsupported source question format: ' + str(fmt))
    data = question.get('freeResponse' if fmt == 'FR' else 'body') or {}
    segments = data.get('segments')
    if segments is None and fmt == 'FR':
        segments = template_segments(question.get('freeAnswerChoice', ''))
    if not segments:
        gaps.append({'code': 'interaction_missing', 'question': qid}); return None
    section_count = len(question.get('sections') or []) if fmt in ('PR', 'DS') else 1
    divided = [[]]
    for segment in segments:
        if segment.get('sectionDivider'):
            divided.append([])
        else:
            divided[-1].append(segment)
    if len(divided) > 1:
        if section_count not in (0, len(divided)):
            raise ValueError('Source section boundaries conflict')
        section_count = len(divided)
    section_count = max(1, section_count)
    rows, sections, identities = [], [], {}
    def register(control, section, position, kind):
        identity = (kind, control.get('id'), control.get('index'), section)
        if (control.get('id') is not None or control.get('index') is not None) and identity in identities:
            return identities[identity]
        cid = f'c{qid}s{section}p{position}'
        row = {'id': cid, 'type': kind, 'label': str(len(rows) + 1)}
        if kind == 'select':
            row['options'] = [{'id': f'o{i}', 'text': str(option), 'html': assets.render(str(option), None, gaps)}
                              for i, option in enumerate(control.get('options', []))]
        else:
            if control.get('type') in ('number', 'text', 'code'):
                row['input_type'] = control['type']
            if isinstance(control.get('width'), (int, float)):
                row['width'] = min(800, max(16, control['width']))
        if control.get('context') in ('mathJax', 'text'):
            row['context'] = control['context']
        rows.append(row); identities[identity] = cid
        return cid
    for section in range(section_count):
        declared = (question.get('sections') or [{}] * section_count)[section]
        body = (declared.get('body') or {}).get('segments', declared.get('segments'))
        if body is None and len(divided) > 1:
            body = divided[section]
        if body is None and section == 0:
            body = segments
        parts, ids, floating = [], [], []
        for position, segment in enumerate(body or []):
            if 'select' in segment or 'textbox' in segment:
                kind = 'select' if 'select' in segment else 'text'
                control = segment.get('select', segment.get('textbox'))
                cid = register(control, section, position, kind); ids.append(cid)
                if control.get('context') == 'mathJax' or (sum(p.count('\\[') - p.count('\\]') for p in parts) > 0):
                    number = next(r['label'] for r in rows if r['id'] == cid)
                    parts.append('\\boxed{\\text{' + number + '}}'); floating.append(cid)
                else:
                    parts.append('{{control:' + cid + '}}')
            elif segment.get('newline'):
                parts.append('<br>')
            elif segment.get('newParagraph'):
                parts.append('\n\n')
            elif 'mathJax' in segment or 'text' in segment:
                parts.append(str(segment.get('mathJax', segment.get('text', ''))))
            elif segment.get('image') or segment.get('graphic'):
                image = segment.get('image', segment.get('graphic'))
                ref = image if isinstance(image, str) else str(image.get('fileId', image.get('src', '')))
                parts.append('<img src="' + html.escape(ref, quote=True) + '">')
            else:
                raise ValueError('Unknown source segment: ' + str(segment.keys()))
        # Missing later bodies retain the original controls and section identity.
        if body is None:
            gaps.append({'code': 'section_missing', 'question': qid, 'section': section})
            for position, control in enumerate(question.get('selects', [])):
                if control.get('sectionIndex') == section and control.get('options'):
                    ids.append(register(control, section, position, 'select'))
        text = ''.join(parts) + ''.join('\n\n{{control:' + cid + '}}' for cid in floating)
        sections.append({'id': f's{section}', 'html': assets.render(text, None, gaps), 'control_ids': ids, 'complete': body is not None})
    if not rows:
        gaps.append({'code': 'interaction_missing', 'question': qid}); return None
    return {'version': 2, 'type': 'composite', 'grading': 'unavailable', 'controls': rows, 'sections': sections}


def template_segments(template):
    """Parse the documented FR bracket grammar only when structural segments are absent."""
    pattern = re.compile(r'\[\s*type:\s*(number|text|code)\s*;([\s\S]*?)\]')
    segments, position = [], 0
    for match in pattern.finditer(template or ''):
        if match.start() > position:
            segments.append({'text': template[position:match.start()].replace('\\newline', '<br>')})
        box = {'type': match[1]}
        width = re.search(r'width:\s*(\d+)', match[2])
        if width:
            box['width'] = int(width[1])
        segments.append({'textbox': box}); position = match.end()
    if position and position < len(template):
        segments.append({'text': template[position:]})
    return segments if position else None


def adapt(source, assets):
    lesson = source['lesson']; tid = lesson['topic']['id']; gaps = []
    topic = {'id': f'ma-topic-{tid}', 'title': lesson['topic']['name'], 'topic_format_version': 2,
             'topic_kind': 'lesson', 'source_adapter': 'mathacademy', 'training_mode': 'paradigm',
             'introductions': [], 'modules': [], 'content_order': [], 'unassigned_questions': [],
             'version': source_fingerprint(source), 'content_version': source_fingerprint(source)}
    modules, questions, step_numbers = {}, [], []
    for position, step in enumerate(lesson.get('steps', [])):
        if type(step.get('number')) is not int:
            raise ValueError('Invalid source step number')
        step_numbers.append(step['number'])
        kind, payload = step['type'], step.get(step['type']) or {}
        if kind not in ('tutorial', 'example', 'question'):
            raise ValueError('Unknown source lesson step')
        private = {'step_id': step.get('id'), 'content_id': step.get('contentId'), 'position': position, 'source_id': payload.get('id')}
        if kind == 'tutorial':
            ident = f'ma-tutorial-{payload["id"]}'
            topic['introductions'].append({'id': ident, 'title': payload.get('title') or '指导',
                'html': assets.render(payload.get('content'), payload.get('html'), gaps), '_source': private})
            topic['content_order'].append({'kind': 'introduction', 'id': ident})
        elif kind == 'example':
            ident = f'ma-example-{payload["id"]}'
            q = question_model(payload, assets, gaps, example=True)
            q['_source'] = private
            module = {'id': ident, 'title': payload.get('title') or ident, 'example': q, 'practices': [],
                      '_source': private, 'key_prerequisites': payload.get('keyPrerequisites', [])}
            if payload['id'] in modules:
                raise ValueError('Duplicate source Example')
            modules[payload['id']] = module; topic['modules'].append(module)
            topic['content_order'].append({'kind': 'example', 'id': ident})
        else:
            q = question_model(payload, assets, gaps); q['_source'] = private
            questions.append((payload.get('exampleId'), q))
    if step_numbers != sorted(step_numbers) or len(set(step_numbers)) != len(step_numbers):
        raise ValueError('Source step order is inconsistent')
    for eid, q in questions:
        if eid in modules:
            modules[eid]['practices'].append(q)
        else:
            topic['unassigned_questions'].append(q); gaps.append({'code': 'unassigned_question', 'question': q['id']})
    for module in topic['modules']:
        if len(module['practices']) < 7:
            gaps.append({'code': 'practice_count', 'module': module['id'], 'count': len(module['practices'])})
    if not lesson.get('steps'):
        gaps.append({'code': 'body_missing'})
    topic['_source_gaps'] = gaps
    topic['availability'] = {'saved': True, 'readable': bool(lesson.get('steps')), 'trainable': False,
        'reasons': [{'code': code, 'message': GAP_LABELS[code], 'count': sum(g['code'] == code for g in gaps)} for code in dict.fromkeys(g['code'] for g in gaps)]}
    return topic


def question_model(q, assets, gaps, example=False):
    qid = q['id']; text = q.get('question' if example else 'text') or ''
    explanation = q.get('explanation') or ''
    if not text.strip():
        gaps.append({'code': 'example_missing' if example else 'body_missing', 'question': qid})
    gaps.append({'code': 'answer_missing', 'question': qid})
    if not explanation.strip():
        gaps.append({'code': 'explanation_missing', 'question': qid})
    constraints = q.get('calculator') if example else (q.get('example') or {}).get('calculator')
    requirement = ('<aside class="inline-document-callout callout" data-callout="requirement"><strong>要求</strong><p>Calculator: '
                   + html.escape(str(constraints)) + '</p></aside>') if constraints is not None else ''
    source_html = q.get('questionHtml') or ''
    render_text = text + requirement
    # Captured Practice stems can say "the graph above" while the diagram is a
    # separate q-<id> attachment. Choices have their own q-<id>-a-<n> identities.
    if not example and q.get('graphic') and not re.search(
            r'<img\b|!\[[^\]]*\]\(|\\documentclass',
            assets.visible_markup(text) + assets.visible_markup(source_html), re.I):
        image = '<img alt="Source diagram" src="/graphics/q-' + str(qid) + '">'
        render_text = image + '\n\n' + render_text
        if source_html:
            source_html = re.sub(r'<body\b[^>]*>', lambda m: m[0] + image, source_html, count=1, flags=re.I) if re.search(r'<body\b', source_html, re.I) else image + source_html
    if source_html and requirement:
        source_html = re.sub(r'</body>', lambda m: requirement + m[0], source_html, count=1, flags=re.I) if re.search(r'</body>', source_html, re.I) else source_html + requirement
    result = {'id': f'ma-{"example" if example else "question"}-{qid}', 'title': q.get('title') or f'Practice {q.get("number", qid)}',
              'question': text, 'html': assets.render(render_text, source_html, gaps),
              'explanation_html': assets.render(explanation, q.get('explanationHtml'), gaps),
              'reference_answer': '', 'answer_type': 'source', 'constraints': str(constraints or ''),
              'source_format': 'example' if example else q.get('format'), 'difficulty': q.get('difficulty')}
    if not example:
        interaction = controls(q, assets, gaps)
        if interaction:
            result['interaction'] = interaction
    return result


class Renderer:
    def __init__(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/render_mathacademy.mjs'
        self.process = subprocess.Popen(['node', str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        text=True, encoding='utf-8', bufsize=1)

    def render(self, topic):
        self.process.stdin.write(json.dumps(topic, ensure_ascii=False) + '\n'); self.process.stdin.flush()
        response = self.process.stdout.readline()
        if not response:
            raise RuntimeError('Source renderer exited unexpectedly')
        result = json.loads(response)
        if not result['ok']:
            raise RuntimeError(topic['id'] + ': ' + result['error'])
        return result['topic']

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=15)
        self.process.stdout.close()


def course_documents(index, scope):
    closure = set(scope['topic_ids']); courses, graphs = [], []
    for cid in list(scope['course_ids']) + sorted(set(index.graphs) - set(scope['course_ids'])):
        source = index.graphs[cid]
        members = [node for tid, node in source['topics'].items() if int(tid) in closure and int(tid) in index.names]
        if not members:
            continue
        course_id = f'ma-course-{cid}'; title = members[0]['course']['name']
        units, unit_order, module_order = {}, {}, {}
        for position, node in enumerate(members):
            unit, module = node['unit'], node['module']
            uid, mid = f'{course_id}-unit-{unit["id"]}', f'{course_id}-module-{module["id"]}'
            units.setdefault(uid, {'id': uid, 'title': unit['name'], 'topic_ids': []})
            units.setdefault(mid, {'id': mid, 'title': module['name'], 'parent_id': uid, 'topic_ids': []})['topic_ids'].append(f'ma-topic-{node["id"]}')
            unit_order.setdefault(uid, (unit['number'], position))
            module_order.setdefault(mid, (module['number'], position))
        ordered = []
        for uid in sorted(unit_order, key=unit_order.get):
            ordered.append(units[uid])
            ordered.extend(units[mid] for mid in sorted(module_order, key=module_order.get) if units[mid]['parent_id'] == uid)
        course = {'id': course_id, 'title': title, 'description': '' if cid in scope['course_ids'] else '仅含本次课程所需的前置内容。',
                  'topics': [{'id': f'ma-topic-{n["id"]}', 'path': f'topics/ma-topic-{n["id"]}.json', 'prerequisites': []} for n in members], 'units': ordered}
        courses.append(course)
        selected = {n['id'] for n in members}
        edges = [{'from': f'ma-topic-{p}', 'to': f'ma-topic-{n["id"]}', 'type': 'prerequisite',
                  'source': f'course-{cid}.json'} for n in members for p in sorted(set(n.get('prerequisites', [])))]
        ids = selected | {p for n in members for p in n.get('prerequisites', [])}
        graph = {'schema_version': 2, 'subject_id': 'math', 'course_id': course_id, 'title': title,
                 'nodes': [{'topic_id': f'ma-topic-{tid}', 'title': index.nodes.get(tid, {}).get('name', f'Topic {tid}'),
                            'member': tid in selected} for tid in sorted(ids)], 'edges': edges}
        from .course_graph import validate_graph
        graphs.append(validate_graph(graph))
    return courses, graphs


def prepare(index, scope, cache, output, *, fail_at=None):
    cache.mkdir(parents=True, exist_ok=True); output.mkdir(parents=True, exist_ok=True)
    graphics = index.graphics(cache)
    assets = Assets(cache / 'graphics', graphics)
    # Code changes invalidate derived caches, never private input snapshots.
    root = Path(__file__).resolve().parents[1]
    conversion = '\n'.join(inspect.getsource(value) for value in (teaching, source_fingerprint, Assets, controls, template_segments, adapt, question_model))
    config_hash = digest((repr((ADAPTER_VERSION, sorted(SOURCE_FIELDS), GAP_LABELS)) + conversion).encode()
                         + (root / 'scripts/render_mathacademy.mjs').read_bytes())
    renderer = Renderer(); reused = 0
    topics_dir, raw_dir = output / 'topics', output / 'mathacademy-sources/topics'
    topics_dir.mkdir(exist_ok=True); raw_dir.mkdir(parents=True, exist_ok=True)
    reports, failures = [], []
    try:
        for position, tid in enumerate(scope['topic_ids']):
            if tid not in index.names:
                continue
            raw, source = index.read(tid)
            key = digest((source_fingerprint(source) + config_hash).encode())
            cached = cache / (key + '.json')
            if cached.exists():
                topic = json.loads(cached.read_text(encoding='utf-8')); reused += 1
            else:
                try:
                    topic = renderer.render(adapt(source, assets))
                    from .catalog import Catalog
                    Catalog._validate_topic(topic)
                    cached.write_bytes(encoded(topic))
                except (ValueError, RuntimeError, subprocess.SubprocessError) as error:
                    failures.append({'topic_id': tid, 'error': str(error)})
                    print(f'conversion_failed {tid}: {str(error)[:400]}', flush=True)
                    continue
            (topics_dir / (topic['id'] + '.json')).write_bytes(encoded(topic))
            (raw_dir / (topic['id'] + '.json')).write_bytes(raw)
            reports.append({'topic_id': topic['id'], **topic['availability'], 'source_gaps': topic['_source_gaps']})
            if (position + 1) % 10 == 0:
                print(f'content {position + 1}/{len(scope["topic_ids"])} reused={reused}', flush=True)
            if fail_at == 'prepare':
                raise RuntimeError('Injected preparation failure')
    finally:
        renderer.close()
    (output / 'conversion-failures.json').write_bytes(encoded(failures))
    if failures:
        raise RuntimeError(f'{len(failures)} conversion failures; batch was not activated. See conversion-failures.json')
    courses, graphs = course_documents(index, scope)
    (output / 'catalog.json').write_bytes(encoded({'courses': courses}))
    (output / 'graphs').mkdir(exist_ok=True)
    graph_sources = output / 'mathacademy-sources/graphs'; graph_sources.mkdir(parents=True, exist_ok=True)
    for graph in graphs:
        (output / 'graphs' / (graph['course_id'] + '.json')).write_bytes(encoded(graph))
        cid = int(graph['course_id'].removeprefix('ma-course-'))
        (graph_sources / f'course-{cid}.json').write_bytes(encoded(index.graphs[cid]))
    report = {'scope': scope, 'topics': reports, 'reused_conversion': reused,
              'readable': sum(t['readable'] for t in reports), 'trainable': sum(t['trainable'] for t in reports),
              'untrainable': sum(not t['trainable'] for t in reports), 'failed': 0}
    (output / 'report.json').write_bytes(encoded(report))
    manifest = {'schema': 1, 'adapter': ADAPTER_VERSION, 'topic_zip_sha256': file_hash(index.topics_zip),
                'graph_zip_sha256': file_hash(index.graphs_zip), 'course_ids': scope['course_ids'],
                'files': {p.relative_to(output).as_posix(): file_hash(p) for p in sorted(output.rglob('*')) if p.is_file() and p.name != 'manifest.json'}}
    (output / 'manifest.json').write_bytes(encoded(manifest))
    return report


def install(package, data_dir, *, fail_at=None):
    """Prepare immutable files, validate the merged website, then swap one pointer."""
    from .catalog import Catalog
    from .config import Settings
    from .subjects import Subjects
    from .course_graph import GraphStore
    from .content_bundle import POINTER
    package, data_dir = Path(package).resolve(), Path(data_dir).resolve()
    if (data_dir / 'cloud-authoritative.json').exists():
        raise ValueError('The local rollback snapshot cannot receive an installation')
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict) or not {'catalog.json', 'report.json'} <= manifest['files'].keys():
        raise ValueError('Invalid prepared package manifest')
    for name, expected in manifest['files'].items():
        path = package / name
        if not path.resolve().is_relative_to(package) or path.is_symlink() or file_hash(path) != expected:
            raise ValueError('Prepared package hash mismatch: ' + name)
    candidate = json.loads((package / 'catalog.json').read_text(encoding='utf-8'))
    report = json.loads((package / 'report.json').read_text(encoding='utf-8'))
    with import_lock(data_dir):
        settings = Settings.load(data_dir)
        before_pointer = (data_dir / POINTER).read_bytes() if (data_dir / POINTER).exists() else None
        old_root = settings.content_dir
        def configuration():
            return {p: file_hash(p) if p.exists() else None for p in [settings.catalog_path, old_root / 'subjects.json',
                old_root / 'test-materials.json', *sorted((old_root / 'graphs').glob('*.json'))]}
        baseline = configuration()
        old = Catalog.load(settings) if settings.catalog_path.exists() else None
        package_hash = file_hash(package / 'manifest.json')
        previous_install = old_root / 'installation.json'
        if previous_install.exists():
            prior = json.loads(previous_install.read_text(encoding='utf-8'))
            if prior.get('package_sha256') == package_hash:
                return {**prior, 'status': 'unchanged', 'added': 0,
                        'reused': len({t['id'] for c in candidate['courses'] for t in c['topics']})}
        added, reused = [], []
        for tid in {t['id'] for c in candidate['courses'] for t in c['topics']}:
            topic = json.loads((package / 'topics' / (tid + '.json')).read_text(encoding='utf-8'))
            if old and tid in old.topics:
                if old.topics[tid] != topic:
                    raise ValueError('Existing ordinary Topic cannot be overwritten: ' + tid)
                reused.append(tid)
            else:
                added.append(tid)
        generation = digest(encoded({'package': manifest, 'baseline': {str(p.relative_to(data_dir)): h for p, h in baseline.items()}}))
        batch = data_dir / 'content-batches' / generation
        batch.mkdir(parents=True, exist_ok=True)
        # Only write content-addressed derived files; old content and learner DB are untouched.
        for name in manifest['files']:
            if name in ('catalog.json', 'report.json'):
                continue
            target = batch / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package / name, target)
        courses = []
        incoming = {c['id']: c for c in candidate['courses']}
        if old:
            for course in old.courses.values():
                c = deepcopy(course)
                for t in c['topics']:
                    t['path'] = os.path.relpath(old.topic_paths[t['id']], batch).replace('\\', '/')
                if c['id'] in incoming:
                    new = incoming.pop(c['id'])
                    by_id = {t['id']: t for t in c['topics']}
                    for t in new['topics']:
                        by_id.setdefault(t['id'], t)
                    c['topics'] = list(by_id.values())
                    by_unit = {u['id']: u for u in c['units']}
                    for u in new['units']:
                        if u['id'] in by_unit:
                            previous = by_unit[u['id']]
                            previous['topic_ids'] = list(dict.fromkeys(previous['topic_ids'] + u['topic_ids']))
                        else:
                            by_unit[u['id']] = u
                    c['units'] = list(by_unit.values())
                    if not new['description']:
                        c['description'] = ''
                courses.append(c)
        courses.extend(incoming.values())
        # Shared IDs always reference one actual file, regardless of course entry.
        canonical = {}
        for c in courses:
            for t in c['topics']:
                canonical.setdefault(t['id'], t['path']); t['path'] = canonical[t['id']]
        (batch / 'catalog.json').write_bytes(encoded({'courses': courses}))
        subject_raw = json.loads((old_root / 'subjects.json').read_text(encoding='utf-8')) if (old_root / 'subjects.json').exists() else {'subjects': []}
        math = next((s for s in subject_raw['subjects'] if s['id'] == 'math'), None)
        if math is None:
            math = {'id': 'math', 'course_ids': []}; subject_raw['subjects'].append(math)
        math['course_ids'] = list(dict.fromkeys(math.get('course_ids', []) + [c['id'] for c in candidate['courses']]))
        (batch / 'subjects.json').write_bytes(encoded(subject_raw))
        policy = json.loads((old_root / 'test-materials.json').read_text(encoding='utf-8')) if (old_root / 'test-materials.json').exists() else {'schema': 1}
        policy['courses'] = sorted(set(policy.get('courses', [])) | {c['id'] for c in candidate['courses']})
        policy['topics'] = sorted(set(policy.get('topics', [])) | {t['id'] for c in candidate['courses'] for t in c['topics']})
        (batch / 'test-materials.json').write_bytes(encoded(policy))
        for folder in ('graphs', 'mathacademy-sources'):
            for p in (old_root / folder).rglob('*'):
                if p.is_file():
                    target = batch / folder / p.relative_to(old_root / folder)
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(p, target)
        staged = Settings(data_dir=data_dir, catalog_path=batch / 'catalog.json'); staged.content_dir = batch
        catalog = Catalog.load(staged); subjects = Subjects(staged, catalog)
        GraphStore(staged, catalog, subjects).all()
        from .test_materials import TestMaterials
        test = TestMaterials(data_dir, catalog)
        if not set(added + reused) <= test.topics:
            raise ValueError('Imported content must be hidden before activation')
        # Idempotency compares the effective site, not a generation's relative file names.
        same = old is not None and old.courses == catalog.courses and old.topics == catalog.topics
        same = same and all((old_root / n).exists() and (old_root / n).read_bytes() == (batch / n).read_bytes() for n in ('subjects.json', 'test-materials.json'))
        same = same and {p.name: file_hash(p) for p in (old_root / 'graphs').glob('*.json')} == {p.name: file_hash(p) for p in (batch / 'graphs').glob('*.json')}
        if not same:
            if configuration() != baseline or ((data_dir / POINTER).read_bytes() if (data_dir / POINTER).exists() else None) != before_pointer:
                raise ValueError('Content configuration changed concurrently; batch was not activated')
            history = data_dir / 'content-activation-history'; history.mkdir(exist_ok=True)
            (history / (generation + '.json')).write_bytes(encoded({'previous': json.loads(before_pointer) if before_pointer else None, 'generation': generation, 'baseline': {str(p.relative_to(data_dir)): h for p, h in baseline.items()}}))
            if fail_at == 'activate':
                raise RuntimeError('Injected pre-activation failure')
            temp = data_dir / (POINTER + '.tmp')
            temp.write_bytes(encoded({'schema': 1, 'generation': generation})); os.replace(temp, data_dir / POINTER)
        loaded = Catalog.load(Settings.load(data_dir))
        if any(loaded.topics[t] != catalog.topics[t] for t in added + reused):
            raise RuntimeError('Installed content readback mismatch')
        result = {'status': 'unchanged' if same else 'installed_with_source_gaps' if report['untrainable'] else 'installed',
                  'added': len(added), 'reused': len(reused), 'readable': report['readable'], 'trainable': report['trainable'],
                  'untrainable': report['untrainable'], 'failed': 0, 'courses': [c['id'] for c in candidate['courses']],
                  'generation': generation, 'test_only': True, 'subject_id': 'math'}
        result['package_sha256'] = package_hash
        (batch / 'installation.json').write_bytes(encoded(result))
        return result


def production_install(package, host='ubuntu@124.221.144.155'):
    import tarfile
    import shlex
    package = Path(package).resolve()
    archive = package.parent / (package.name + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(package, arcname='package')
    stage = '/tmp/graspmemoedu-ma-' + file_hash(archive)[:16]
    subprocess.run(['ssh', '-o', 'BatchMode=yes', host, 'mkdir -p ' + shlex.quote(stage)], check=True)
    subprocess.run(['scp', str(archive), host + ':' + stage + '/package.tar.gz'], check=True)
    result = subprocess.run(['ssh', host, 'sudo -n python3 /usr/local/lib/tencent-cloud-maintenance/host.py run-stage --path '
        + shlex.quote(stage) + ' -- /opt/graspmemoedu/venv/bin/python /opt/graspmemoedu/app/scripts/install_mathacademy_production.py '
        + shlex.quote(stage)], check=True, text=True, capture_output=True)
    for line in result.stdout.splitlines():
        try:
            receipt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if receipt.get('target') == 'production' and receipt.get('health') == 'passed':
            return receipt
    raise RuntimeError('Production wrapper returned no verified installation receipt')


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--topics-zip'); p.add_argument('--graphs-zip')
    p.add_argument('--course-ids', nargs='+', type=int, default=DEFAULT_COURSES)
    p.add_argument('--target', choices=['local', 'production'], default='local')
    p.add_argument('--data-dir'); p.add_argument('--plan-only', action='store_true')
    p.add_argument('--cache', default='D:/Caches/GraspMemoEdu/mathacademy' if os.name == 'nt' else '/var/cache/graspmemoedu/mathacademy')
    p.add_argument('--output', default='D:/Artifacts/GraspMemoEdu/mathacademy-course-import/prepared')
    p.add_argument('--install-package'); p.add_argument('--fail-at', choices=['prepare', 'activate'])
    args = p.parse_args(argv)
    if args.target == 'local' and not args.data_dir and not args.plan_only:
        p.error('--data-dir must explicitly name an isolated local target')
    if args.install_package:
        result = production_install(args.install_package) if args.target == 'production' else install(args.install_package, args.data_dir, fail_at=args.fail_at)
    else:
        index = SourceIndex(args.topics_zip, args.graphs_zip); scope = index.scope(args.course_ids)
        if args.plan_only:
            print(json.dumps(scope, ensure_ascii=False)); return
        print(json.dumps({k: v for k, v in scope.items() if k != 'topic_ids'}), flush=True)
        output = Path(args.output); prepare(index, scope, Path(args.cache), output, fail_at=args.fail_at)
        result = production_install(output) if args.target == 'production' else install(output, args.data_dir, fail_at=args.fail_at)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
