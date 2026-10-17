"""Readable imported content is not a learning attempt or a completed Topic."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import html
import sqlite3

from .question_types import public_interaction
from .service import DomainError


def trainable(topic):
    return topic.get('availability', {}).get('trainable', True)


def validate_source_topic(topic):
    from .catalog import Catalog
    if not topic.get('version') or topic.get('topic_format_version') != 2 or topic.get('topic_kind') != 'lesson':
        raise ValueError('Invalid source content identity')
    availability = topic['availability']
    if (type(availability.get('readable')) is not bool or type(availability.get('trainable')) is not bool
            or not isinstance(availability.get('reasons'), list)):
        raise ValueError('Invalid content availability')
    ids = set()
    for item in [*topic['introductions'], *topic['modules']]:
        Catalog._identifier(item['id'])
        if item['id'] in ids:
            raise ValueError('Duplicate source section')
        ids.add(item['id'])
    expected = {('introduction', i['id']) for i in topic['introductions']} | {('example', m['id']) for m in topic['modules']}
    order = [(i['kind'], i['id']) for i in topic['content_order']]
    if set(order) != expected or len(order) != len(expected):
        raise ValueError('Source order does not cover all sections')
    questions = set()
    from .question_types import validate_interaction
    for q in [q for m in topic['modules'] for q in [m['example'], *m['practices']]] + topic.get('unassigned_questions', []):
        Catalog._identifier(q['id'])
        if q['id'] in questions:
            raise ValueError('Duplicate source question')
        questions.add(q['id'])
        if q.get('interaction', {}).get('version') == 2:
            validate_interaction(q['interaction'], q.get('question', ''))


def reading_blocks(topic):
    intros = {i['id']: i for i in topic['introductions']}
    modules = {m['id']: m for m in topic['modules']}
    result = []
    def question(q):
        result.append({'id': q['id'], 'title': q.get('title', ''), 'kind': 'question', 'html': q['html'],
                       'interaction': public_interaction(q), 'actions': [],
                       'explanation_html': q.get('explanation_html', '')})
    for section in topic['content_order']:
        if section['kind'] == 'introduction':
            item = intros[section['id']]
            fragment = '<h3>' + html.escape(item['title']) + '</h3>' + item['html']
            if result and result[-1]['kind'] == 'introduction':
                result[-1]['html'] += fragment
                result[-1]['introduction_ids'].append(item['id'])
            else:
                result.append({'id': item['id'], 'title': '', 'html': fragment, 'kind': 'introduction',
                               'introduction_ids': [item['id']], 'actions': []})
        else:
            module = modules[section['id']]
            question(module['example'])
            for q in module['practices']:
                question(q)
    for q in topic.get('unassigned_questions', []):
        question(q)
    return result


class SourceReader:
    """Stateless reader. No LearningService, progress row, FSRS card or completion."""
    def __init__(self, course, data_dir, judge=None, learner_id='local', **_options):
        self.course, self.data_dir, self.judge = course, Path(data_dir), judge
        self.learner_id, self.topic_id = learner_id, course['id']
        self.db_path = self.data_dir / 'learning.sqlite3'
        self.review_backend = None
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # The normal storage schema is shared, without inserting a learner row.
        from .service import STORAGE_SCHEMA
        with closing(self._connect()) as con:
            con.executescript(STORAGE_SCHEMA)

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def resume_due(self):
        return None

    def flush_logs(self):
        return None

    def state(self):
        return {'topic_id': self.topic_id, 'title': self.course['title'], 'course_version': self.course['version'],
                'learner_id': self.learner_id,
                'status': 'read_only', 'read_only': True, 'availability': deepcopy(self.course['availability']),
                'blocks': reading_blocks(self.course), 'math_css': self.course.get('math_css', ''),
                'references': deepcopy(self.course.get('external_references', [])), 'revision': 0,
                'steps': [], 'modules': [], 'pending_submission_id': None, 'pause': None}

    def unavailable(self, *_args, **_kwargs):
        raise DomainError('content_not_trainable', '原材料尚不满足训练条件，可阅读已保存内容。', 409)

    choose = advance = select = pause = skip = submit = reveal = reset = unavailable
