"""Versioned course graphs, independent of Topic content and learner state."""
from collections import deque
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from .catalog import Catalog
from .service import DomainError
from .subjects import Subjects
from .topic_install import encoded, import_lock


MAX_BYTES = 4 * 1024 * 1024


def identifier(value):
    Catalog._identifier(value)
    # Graph IDs become portable Windows file names; canonical lower case also
    # prevents two different identifiers from sharing one case-insensitive file.
    if value != value.lower() or value.endswith('.') or re.fullmatch(
            r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', value):
        raise ValueError('Graph course ID must be a portable lower-case identifier')
    return value


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError('Missing or unknown graph field')


def _source(value):
    value = Catalog._text(value, 1000)
    path = value.split('#', 1)[0]
    if (not path or any(c in value for c in '\\:<>"|?*') or any(ord(c) < 32 for c in value)
            or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts):
        raise ValueError('Graph source must be a portable relative reference')
    return value


def validate_graph(raw):
    """Pure validation; no files, content, permissions or progress are created."""
    _fields(raw, ('schema_version', 'subject_id', 'course_id', 'title', 'nodes', 'edges'))
    if type(raw['schema_version']) is not int or raw['schema_version'] != 1:
        raise ValueError('Unsupported graph schema_version')
    graph = {key: raw[key] for key in ('schema_version', 'subject_id', 'course_id')}
    identifier(graph['course_id'])
    Catalog._identifier(graph['subject_id'])
    graph['title'] = Catalog._text(raw['title'], 200)
    if not isinstance(raw['nodes'], list) or not 1 <= len(raw['nodes']) <= 1000:
        raise ValueError('Graph requires 1–1000 Topic nodes')
    if not isinstance(raw['edges'], list) or len(raw['edges']) > 10000:
        raise ValueError('Graph allows at most 10000 edges')
    nodes, seen = [], set()
    for item in raw['nodes']:
        _fields(item, ('topic_id', 'title'), ('source',))
        topic_id = Catalog._identifier(item['topic_id'])
        if topic_id.casefold() in seen:
            raise ValueError('Duplicate Topic node')
        seen.add(topic_id.casefold())
        node = {'topic_id': topic_id, 'title': Catalog._text(item['title'], 200)}
        if 'source' in item:
            node['source'] = _source(item['source'])
        nodes.append(node)
    ids = {node['topic_id'] for node in nodes}
    edges, pairs = [], set()
    children, indegree = {key: [] for key in ids}, dict.fromkeys(ids, 0)
    for item in raw['edges']:
        _fields(item, ('from', 'to', 'type'), ('reason', 'source'))
        parent, child = Catalog._identifier(item['from']), Catalog._identifier(item['to'])
        if parent not in ids or child not in ids or parent == child:
            raise ValueError('Unknown endpoint or self dependency')
        if item['type'] != 'prerequisite' or (parent, child) in pairs:
            raise ValueError('Unknown or duplicate graph relation')
        pairs.add((parent, child))
        edge = {'from': parent, 'to': child, 'type': 'prerequisite'}
        if 'reason' in item:
            edge['reason'] = Catalog._text(item['reason'], 1000)
        if 'source' in item:
            edge['source'] = _source(item['source'])
        edges.append(edge)
        children[parent].append(child)
        indegree[child] += 1
    ready = deque(key for key in ids if not indegree[key])
    visited = 0
    while ready:
        visited += 1
        for child in children[ready.popleft()]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(ids):
        raise ValueError('Prerequisite cycle')
    graph.update(nodes=sorted(nodes, key=lambda node: node['topic_id']),
                 edges=sorted(edges, key=lambda edge: (edge['from'], edge['to'])))
    if len(encoded(graph)) > MAX_BYTES:
        raise ValueError('Normalized graph file too large')
    return graph


def read_graph(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Graph file too large')
    return validate_graph(json.loads(path.read_text(encoding='utf-8-sig')))


def revision(graph):
    return hashlib.sha256(encoded(graph)).hexdigest()


def graph_diff(old, new):
    result = {}
    for key, identity in (('nodes', lambda item: item['topic_id']),
                          ('edges', lambda item: (item['from'], item['to']))):
        before = {identity(item): item for item in (old or {}).get(key, [])}
        after = {identity(item): item for item in new[key]}
        result[key] = {'added': [after[k] for k in sorted(after.keys() - before.keys())],
                       'removed': [before[k] for k in sorted(before.keys() - after.keys())],
                       'changed': [{'before': before[k], 'after': after[k]} for k in sorted(before.keys() & after.keys())
                                   if before[k] != after[k]]}
    result['metadata_changed'] = [key for key in ('subject_id', 'course_id', 'title')
                                  if (old or {}).get(key) != new[key]]
    return result


class GraphStore:
    def __init__(self, settings, catalog=None, subjects=None):
        self.settings = settings
        self.root = settings.data_dir / 'graphs'
        self.catalog = catalog or Catalog.load(settings)
        self.subjects = subjects or Subjects(settings, self.catalog)

    def path(self, course_id):
        path = self.root / (identifier(course_id) + '.json')
        if not path.resolve().is_relative_to(self.settings.data_dir.resolve()):
            raise ValueError('Graph path leaves data directory')
        return path

    def validate_binding(self, graph):
        self.subjects.require(graph['subject_id'])
        course_id = graph['course_id']
        if course_id in self.catalog.courses:
            self.subjects.authorize_course(graph['subject_id'], course_id)
        for node in graph['nodes']:
            owner = self.catalog.topic_courses.get(node['topic_id'])
            if owner is not None and owner != course_id:
                raise ValueError('Graph Topic belongs to a different course: ' + node['topic_id'])

    def load(self, course_id):
        Catalog._identifier(course_id)
        try:
            identifier(course_id)
        except ValueError:
            return None  # Legacy catalogs allow IDs not usable as graph file names.
        path = self.path(course_id)
        if not path.exists():
            return None
        graph = read_graph(path)
        if graph['course_id'] != course_id:
            raise ValueError('Graph file identity mismatch')
        self.validate_binding(graph)
        return graph

    def all(self):
        graphs = [self.load(path.stem) for path in sorted(self.root.glob('*.json'))]
        owners = {}
        for graph in graphs:
            for node in graph['nodes']:
                if node['topic_id'] in owners:
                    raise ValueError('Topic appears in multiple course graphs')
                owners[node['topic_id']] = graph['course_id']
        return graphs

    def validate_topic_target(self, topic_id, subject_id, course_id):
        for graph in self.all():
            if any(node['topic_id'] == topic_id for node in graph['nodes']):
                if (graph['subject_id'], graph['course_id']) != (subject_id, course_id):
                    raise ValueError('Topic destination conflicts with its existing graph binding')

    def dependencies(self, course_id):
        graph = self.load(course_id)
        if graph is None:
            return {node['id']: node['prerequisites'] for node in self.catalog.course(course_id)['topics']}
        result = {node['topic_id']: [] for node in graph['nodes']}
        for edge in graph['edges']:
            result[edge['to']].append(edge['from'])
        return result

    def public(self, graph, access):
        self.subjects.authorize_subject(access, graph['subject_id'])
        allowed = {topic['id'] for topic in access['topics']}
        inherit = access['role'] == 'account' and access.get('permission_defaults', {}).get('topics', False)
        # Pending Topics are visible only to accounts that inherit future Topics.
        nodes = [{'topic_id': node['topic_id'], 'title': node['title'],
                  'content_status': 'available' if node['topic_id'] in self.catalog.topics else 'not_imported'}
                 for node in graph['nodes'] if inherit or node['topic_id'] in allowed]
        visible = {node['topic_id'] for node in nodes}
        return {'schema_version': 1, 'course_id': graph['course_id'], 'subject_id': graph['subject_id'],
                'title': graph['title'], 'nodes': nodes,
                'edges': [{key: edge[key] for key in ('from', 'to', 'type')}
                          for edge in graph['edges'] if edge['from'] in visible and edge['to'] in visible]}


def import_graph(settings, source, *, apply=False, expected_revision=None):
    graph = read_graph(source)
    def prepare():
        store = GraphStore(settings)
        store.validate_binding(graph)
        prior = store.load(graph['course_id'])
        if prior and prior['subject_id'] != graph['subject_id']:
            raise ValueError('An existing graph cannot move between subjects')
        ids = {node['topic_id'] for node in graph['nodes']}
        for other in store.all():
            if other['course_id'] != graph['course_id'] and ids.intersection(node['topic_id'] for node in other['nodes']):
                raise ValueError('Topic is already bound by another course graph')
        previous = revision(prior) if prior else None
        if expected_revision is not None and expected_revision != (previous or 'none'):
            raise ValueError('Graph changed since preview; preview the latest version')
        return store, prior, {'status': 'unchanged' if prior == graph else 'preview',
                             'course_id': graph['course_id'], 'previous_revision': previous,
                             'revision': revision(graph), 'diff': graph_diff(prior, graph),
                             'pending_topics': [node['topic_id'] for node in graph['nodes']
                                                if node['topic_id'] not in store.catalog.topics],
                             'topic_content_changed': False, 'learning_progress_changed': False}
    if not apply:
        return prepare()[2]
    with import_lock(settings.data_dir):
        store, prior, report = prepare()
        if report['status'] == 'unchanged':
            return report
        path = store.path(graph['course_id'])
        path.parent.mkdir(parents=True, exist_ok=True)
        if prior is not None:
            history = store.root / 'history' / graph['course_id']
            if not history.resolve().is_relative_to(settings.data_dir.resolve()):
                raise ValueError('Graph history leaves data directory')
            history.mkdir(parents=True, exist_ok=True)
            backup = history / (revision(prior) + '.json')
            if backup.exists():
                if backup.read_bytes() != encoded(prior):
                    raise ValueError('Graph history revision conflict')
            else:
                with backup.open('xb') as stream:
                    stream.write(encoded(prior)); stream.flush(); os.fsync(stream.fileno())
            report['backup'] = str(backup)
        descriptor, pending = tempfile.mkstemp(prefix='.graph-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encoded(graph)); stream.flush(); os.fsync(stream.fileno())
            os.replace(pending, path)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
        report.update(status='installed', path=str(path), reload_required=False)
        return report


def preview_html(settings, source, destination):
    """Self-contained local preview using the same renderer as the website."""
    graph = read_graph(source)
    store = GraphStore(settings)
    store.validate_binding(graph)
    public = store.public(graph, {'role': 'account', 'topics': [], 'permission_defaults': {'topics': True}})
    web = Path(__file__).resolve().parents[1] / 'web'
    javascript = (web / 'course-graph.js').read_text(encoding='utf-8').replace('export function ', 'function ')
    javascript = re.sub(r'^import .*?;\n', '', javascript, count=1)
    messages = (web / 'portal-messages.js').read_text(encoding='utf-8').replace('export const ', 'const ')
    javascript = (messages + '\n' + javascript).replace('</script', '<\\/script')
    css = (web / 'course-graph.css').read_text(encoding='utf-8')
    payload = json.dumps(public, ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026')
    document = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>' + html.escape(graph['title']) + '</title><style>body{margin:24px;font-family:system-ui;background:#fafafa;color:#20364a}'
                + css + '</style><h1>' + html.escape(graph['title']) + '</h1><p>本地知识图谱 · 箭头由前置指向后续 · 不含学习进度</p>'
                '<div id="graph"></div><script>' + javascript + '\nrenderCourseGraph(document.getElementById("graph"), '
                + payload + ');</script></html>')
    target = Path(destination)
    if target.suffix.lower() != '.html' or target.resolve() == Path(source).resolve():
        raise ValueError('Preview destination must be a separate .html file')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding='utf-8')
    return {'preview': str(target), 'nodes': len(graph['nodes']), 'edges': len(graph['edges'])}


def validate_graph_archive(blobs):
    """Recognize and validate only current manifests and content-addressed history."""
    recognized = set()
    for name, data in blobs.items():
        if not name.startswith('graphs/'):
            continue
        if len(data) > MAX_BYTES:
            raise ValueError('Archived graph is too large')
        graph = validate_graph(json.loads(data))
        active = f"graphs/{graph['course_id']}.json"
        historical = f"graphs/history/{graph['course_id']}/{revision(graph)}.json"
        if name not in (active, historical):
            raise ValueError('Archived graph path or revision differs from its contents')
        recognized.add(name)
    return recognized


def graph_backup_blobs(settings):
    with import_lock(settings.data_dir):
        store = GraphStore(settings)
        graphs = store.all()
        blobs = {f"graphs/{graph['course_id']}.json": encoded(graph) for graph in graphs}
        for path in sorted((store.root / 'history').glob('*/*.json')):
            if not path.resolve().is_relative_to(settings.data_dir.resolve()):
                raise ValueError('Graph history leaves data directory')
            if path.stat().st_size > MAX_BYTES:
                raise ValueError('Graph history too large')
            blobs[path.relative_to(settings.data_dir).as_posix()] = path.read_bytes()
        validate_graph_archive(blobs)
        return blobs
