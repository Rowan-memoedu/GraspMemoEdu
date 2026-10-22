"""Source-bound reading annotations and private context records; no scheduling state."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

from .atomic_content import memory_points
from .service import DomainError

IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


def _text(value, maximum, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum or '\x00' in value:
        raise ValueError('Invalid reading text')
    return value


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('Invalid reading identifier')
    return value


def validate_reading_materials(topic):
    """Optional Topic capability. All offsets count Unicode code points."""
    values = topic.get('reading_materials', [])
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError('Invalid reading materials')
    point_ids = {p['id'] for p in memory_points(topic)}
    seen = set()
    for material in values:
        if (not isinstance(material, dict) or set(material) - {'id', 'title', 'paragraphs', 'annotations', 'source'}
                or not {'id', 'title', 'paragraphs', 'annotations'} <= material.keys()):
            raise ValueError('Invalid reading material')
        if _id(material['id']) in seen:
            raise ValueError('Duplicate reading material')
        seen.add(material['id']); _text(material['title'], 200)
        if 'source' in material:
            _text(material['source'], 2000)
        paragraphs = material['paragraphs']
        if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 200:
            raise ValueError('Invalid reading paragraphs')
        texts = {}
        for paragraph in paragraphs:
            if not isinstance(paragraph, dict) or set(paragraph) != {'id', 'text'}:
                raise ValueError('Invalid reading paragraph')
            if _id(paragraph['id']) in texts:
                raise ValueError('Duplicate reading paragraph')
            texts[paragraph['id']] = _text(paragraph['text'], 10000)
        annotations = material['annotations']
        if not isinstance(annotations, list) or len(annotations) > 1000:
            raise ValueError('Invalid reading annotations')
        annotation_ids, occupied = set(), {}
        for item in annotations:
            required = {'id', 'paragraph_id', 'start', 'end', 'meaning', 'point_ids'}
            if not isinstance(item, dict) or not required <= item.keys() or set(item) - required - {'lemma', 'note', 'relations'}:
                raise ValueError('Invalid reading annotation')
            if (_id(item['id']) in annotation_ids or not isinstance(item['paragraph_id'], str)
                    or item['paragraph_id'] not in texts):
                raise ValueError('Invalid reading annotation identity')
            annotation_ids.add(item['id'])
            start, end = item['start'], item['end']
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(texts[item['paragraph_id']]):
                raise ValueError('Invalid reading annotation span')
            if end - start > 300 or not texts[item['paragraph_id']][start:end].strip():
                raise ValueError('Invalid reading annotation span')
            intervals = occupied.setdefault(item['paragraph_id'], [])
            if any(start < previous_end and end > previous_start for previous_start, previous_end in intervals):
                raise ValueError('Reading annotations cannot overlap')
            intervals.append((start, end)); _text(item['meaning'], 4000)
            points = item['point_ids']
            if (not isinstance(points, list) or len(points) > 20 or any(not isinstance(p, str) for p in points)
                    or len(points) != len(set(points)) or set(points) - point_ids):
                raise ValueError('Reading annotation points must belong to its Topic')
            for field, maximum in (('lemma', 300), ('note', 2000)):
                if field in item:
                    _text(item[field], maximum)
            relations = item.get('relations', [])
            if not isinstance(relations, list) or len(relations) > 30:
                raise ValueError('Invalid word relations')
            for relation in relations:
                if not isinstance(relation, dict) or set(relation) != {'kind', 'text'}:
                    raise ValueError('Invalid word relation')
                _text(relation['kind'], 100); _text(relation['text'], 1000)
    return values


def find_material(topic, material_id):
    material = next((m for m in topic.get('reading_materials', []) if m['id'] == material_id), None)
    if material is None:
        raise DomainError('reading_not_found', '未找到这篇阅读材料。', 404)
    return material


def validate_anchor(topic, material, values):
    """Use the correction tool's version + exact/prefix/suffix quote contract."""
    if values.get('content_version') != topic['version']:
        raise DomainError('reading_version_changed', '正文版本已变化，请刷新后重新选择。', 409)
    paragraph = next((p for p in material['paragraphs'] if p['id'] == values.get('paragraph_id')), None)
    quote = values.get('quote', {})
    valid = isinstance(quote, dict) and set(quote) == {'exact', 'prefix', 'suffix', 'start', 'end'}
    start, end = quote.get('start'), quote.get('end')
    valid = valid and paragraph is not None and type(start) is int and type(end) is int
    if valid:
        text = paragraph['text']
        valid = (0 <= start < end <= len(text) and end - start <= 2000 and
                 text[start:end] == quote['exact'] and bool(quote['exact'].strip()) and
                 text[max(0, start - 50):start] == quote['prefix'] and text[end:end + 50] == quote['suffix'])
    if not valid:
        raise DomainError('invalid_reading_anchor', '所选文字与正文不一致，请重新选择。', 422)
    return paragraph


def lookup_context(topic, material, values, dictionary=None):
    paragraph = validate_anchor(topic, material, values)
    quote = values['quote']
    annotations = [deepcopy(a) for a in material['annotations']
                   if a['paragraph_id'] == paragraph['id'] and quote['start'] <= a['start'] and a['end'] <= quote['end']]
    points = list(dict.fromkeys(p for a in annotations for p in a['point_ids']))
    definitions = [{'term': paragraph['text'][a['start']:a['end']], 'meaning': a['meaning'],
                    'source': 'material', **{k: deepcopy(a[k]) for k in ('lemma', 'note', 'relations') if k in a}}
                   for a in annotations]
    lexical = None
    if not definitions and dictionary is not None:
        lexical = dictionary(quote['exact']) or {}
        if isinstance(lexical, dict):
            definitions = [{'term': quote['exact'], 'meaning': sense['definition'], 'source': 'dictionary',
                            **{k: deepcopy(sense[k]) for k in ('pos', 'lemmas', 'examples', 'relations') if k in sense}}
                           for sense in lexical.get('senses', [])]
        else:
            definitions = lexical
    # A whole paragraph is retained, including punctuation and original spacing.
    return {'quote': deepcopy(quote), 'paragraph_id': paragraph['id'], 'context': paragraph['text'],
            'point_ids': points, 'definitions': definitions, 'candidate': not bool(points),
            **({'dictionary': lexical} if isinstance(lexical, dict) else {})}


class ContextLibraryStore:
    """An independent context ledger referencing the existing atomic point owner."""
    def __init__(self, connect):
        self.connect = connect
        with closing(connect()) as con:
            con.execute('''CREATE TABLE IF NOT EXISTS reading_contexts (
                id TEXT PRIMARY KEY, learner TEXT NOT NULL, topic TEXT NOT NULL,
                version TEXT NOT NULL, material TEXT NOT NULL, body TEXT NOT NULL,
                updated_at TEXT NOT NULL)''')
            con.execute('CREATE INDEX IF NOT EXISTS reading_contexts_owner ON reading_contexts(learner,topic,version,material)')
            con.commit()

    def records(self, learner, topic, material):
        with closing(self.connect()) as con:
            rows = con.execute('SELECT body,version FROM reading_contexts WHERE learner=? AND topic=? AND material=? ORDER BY updated_at,id',
                               (learner, topic['id'], material['id'])).fetchall()
        return [{**json.loads(row[0]), 'content_version': row[1], 'stale': row[1] != topic['version']} for row in rows]

    def save(self, access, topic, material, values, *, activate_points=None, dictionary=None):
        context = lookup_context(topic, material, values, dictionary)
        action, note = values.get('action'), values.get('note', '')
        if action not in ('mark', 'unmark', 'memory'):
            raise DomainError('invalid_reading_action', '请选择标记疑惑或加入记忆。', 422)
        try:
            _text(note, 2000, empty=True)
        except ValueError:
            raise DomainError('invalid_reading_note', '阅读笔记最多 2000 字。', 422) from None
        key = json.dumps([access['learner_id'], topic['id'], topic['version'], material['id'],
                          context['paragraph_id'], context['quote']['start'], context['quote']['end']], ensure_ascii=False)
        record_id = hashlib.sha256(key.encode()).hexdigest()
        activation = None
        if action == 'memory' and context['point_ids']:
            if activate_points is None:
                raise DomainError('reading_memory_unavailable', '记忆入口暂不可用；可先标记疑惑并保留原文。', 503)
            activation = activate_points(access, topic, context['point_ids'], context)
            if not isinstance(activation, dict) or activation.get('status') not in ('pending', 'learned'):
                raise RuntimeError('Invalid existing-point activation response')
        with closing(self.connect()) as con:
            con.execute('BEGIN IMMEDIATE')
            previous = con.execute('SELECT body FROM reading_contexts WHERE id=?', (record_id,)).fetchone()
            record = json.loads(previous[0]) if previous else {'id': record_id, **context, 'marked': False, 'memory_status': None}
            record.update(content_version=topic['version'], stale=False)
            record['note'] = note
            if action in ('mark', 'unmark'):
                record['marked'] = action == 'mark'
            else:
                record['memory_status'] = activation['status'] if activation else 'candidate'
                if activation:
                    record['memory'] = activation
            stamp = datetime.now(timezone.utc).isoformat()
            record['updated_at'] = stamp
            con.execute('''INSERT INTO reading_contexts VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at''',
                        (record_id, access['learner_id'], topic['id'], topic['version'], material['id'],
                         json.dumps(record, ensure_ascii=False), stamp))
            con.commit()
        return record
