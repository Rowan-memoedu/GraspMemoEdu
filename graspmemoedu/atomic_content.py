"""Author contract for point/card content, optionally alongside Example modules."""
from html.parser import HTMLParser
import re

from .question_types import validate_interaction

MODE = 'atomic_retrieval'
SUBJECTS = {'math', 'physics', 'english', 'chinese', 'biology', 'chemistry', 'cs'}
DEFAULT_POLICY = {'version': 'atomic-v1', 'first_review_seconds': 21600, 'correct_rating': 3}
GAP_SECONDS = 180
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


class PlainText(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(html)

    def handle_data(self, text):
        self.parts.append(text)


def plain_text(html):
    return ''.join(PlainText(html).parts)


def is_atomic(topic):
    from .topic_structure import has_atomic, has_paradigm
    return not has_paradigm(topic) and (has_atomic(topic) or topic.get('training_mode') == MODE)


def memory_points(topic):
    return [p for c in topic.get('memory_chunks', []) for p in c['points']] + topic.get('memory_points', [])


def point_chunks(topic):
    return {**{p['id']: c['id'] for c in topic.get('memory_chunks', []) for p in c['points']},
            **{p['id']: None for p in topic.get('memory_points', [])}}


def point_groups(topic):
    """Report grouping does not create teaching chunks or additional memories."""
    return topic.get('memory_chunks', []) + (
        [{'id': 'problems', 'title': topic['title'], 'points': topic['memory_points']}]
        if topic.get('memory_points') else [])


def policy_for(topic):
    policy = {**DEFAULT_POLICY, **topic.get('atomic_policy', {})}
    if (set(policy) != set(DEFAULT_POLICY) or not isinstance(policy['version'], str)
            or not policy['version'] or len(policy['version']) > 100
            or type(policy['first_review_seconds']) is not int
            or not GAP_SECONDS <= policy['first_review_seconds'] <= 30 * 86400
            or type(policy['correct_rating']) is not int or policy['correct_rating'] not in (3, 4)):
        raise ValueError('Invalid atomic scheduling policy')
    return policy


def validate_atomic(topic):
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def identifier(value):
        return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value))

    def text(value):
        return isinstance(value, str) and bool(value.strip()) and '\x00' not in value

    from .topic_structure import has_atomic, sections
    require(has_atomic(topic) and topic.get('subject_id') in SUBJECTS, 'Unsupported atomic retrieval subject')
    require(identifier(topic.get('id')) and text(topic.get('title')) and text(topic.get('version')), 'Missing topic identity')
    groups = sections(topic)
    require(all(text(s['introduction'].get('html')) for s in groups), 'Missing Introduction')
    chunk_sources = {c['id']: plain_text(s['introduction']['html']) for s in groups for c in s['chunks']}
    policy_for(topic)
    chunks = topic.get('memory_chunks')
    external = topic.get('source_adapter') == 'hyperskill'
    require(isinstance(chunks, list) and (0 if external else 1) <= len(chunks) <= 200, 'Missing memory chunks')
    chunk_ids, point_ids = set(), set()
    for chunk in chunks:
        require(identifier(chunk.get('id')) and chunk['id'] not in chunk_ids, 'Duplicate memory chunk')
        chunk_ids.add(chunk['id'])
        require(text(chunk.get('title')) and text(chunk.get('html')), 'Missing memory chunk content')
        anchors = chunk.get('anchors')
        require(isinstance(anchors, list) and 1 <= len(anchors) <= 30, 'Missing Introduction anchors')
        intro_text = chunk_sources[chunk['id']]
        for anchor in anchors:
            start, end = anchor.get('start'), anchor.get('end')
            require(type(start) is int and type(end) is int and 0 <= start < end <= len(intro_text)
                    and intro_text[start:end] == anchor.get('quote'), 'Introduction anchor does not match source')
        points = chunk.get('points')
        require(isinstance(points, list) and 1 <= len(points) <= 100, 'Missing atomic memory points')
        for point in points:
            require(identifier(point.get('id')) and point['id'] not in point_ids and text(point.get('text')),
                    'Duplicate or incomplete memory point')
            point_ids.add(point['id'])
    loose = topic.get('memory_points', [])
    require(isinstance(loose, list) and (external or not loose), 'Standalone points require an external exercise provider')
    for point in loose:
        require(identifier(point.get('id')) and point['id'] not in point_ids and text(point.get('text')),
                'Duplicate or incomplete memory point')
        point_ids.add(point['id'])
    require(bool(point_ids), 'Missing atomic memory points')
    cards = topic.get('flashcards')
    require(isinstance(cards, list) and 1 <= len(cards) <= 5000, 'Missing flashcards')
    questions, singles = {}, set()
    for card in cards:
        require(identifier(card.get('id')) and card['id'] not in questions, 'Duplicate flashcard')
        required_fields = ('title', 'question', 'html', 'answer_type', 'constraints')
        if not external:
            required_fields += ('reference_answer', 'explanation_html')
        require(all(text(card.get(field)) for field in required_fields),
                'Flashcard is missing its question, answer or explanation')
        targets = card.get('point_ids')
        require(isinstance(targets, list) and 1 <= len(targets) <= 20
                and all(isinstance(p, str) for p in targets)
                and len(set(targets)) == len(targets) and set(targets) <= point_ids, 'Unknown or repeated memory point')
        if len(targets) == 1:
            singles.add(targets[0])
        validate_interaction(card.get('interaction'), card['question'])
        questions[card['id']] = card
    require(singles == point_ids, 'Each memory point requires an individual flashcard for initial learning')
    if external:
        from .hyperskill_content import validate_binding
        validate_binding(topic)
    if 'academy' in topic:
        binding = topic['academy']
        require(topic['subject_id'] == 'cs' and len(cards) == len(point_ids) == 1,
                'An Academy exercise owns exactly one CS card and memory point')
        require(isinstance(binding, dict) and set(binding) == {'task_key', 'kind'}
                and identifier(binding.get('task_key')) and binding.get('kind') in ('automated', 'manual'),
                'Invalid Academy binding; local paths must stay on the device')
    return questions
