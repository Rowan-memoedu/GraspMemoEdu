"""Author contract for atomic retrieval topics, independent of Example modules."""
from html.parser import HTMLParser
import re

from .question_types import validate_interaction

MODE = 'atomic_retrieval'
SUBJECTS = {'english', 'chinese', 'biology', 'chemistry'}
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
    return topic.get('training_mode') == MODE


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

    require(is_atomic(topic) and topic.get('subject_id') in SUBJECTS, 'Atomic retrieval is limited to four subjects')
    require(identifier(topic.get('id')) and text(topic.get('title')) and text(topic.get('version')), 'Missing topic identity')
    require(not topic.get('modules'), 'Atomic topics cannot contain Example modules')
    intro = topic.get('introduction', {}).get('html')
    require(text(intro), 'Missing Introduction')
    intro_text = plain_text(intro)
    policy_for(topic)
    chunks = topic.get('memory_chunks')
    require(isinstance(chunks, list) and 1 <= len(chunks) <= 200, 'Missing memory chunks')
    chunk_ids, point_ids = set(), set()
    for chunk in chunks:
        require(identifier(chunk.get('id')) and chunk['id'] not in chunk_ids, 'Duplicate memory chunk')
        chunk_ids.add(chunk['id'])
        require(text(chunk.get('title')) and text(chunk.get('html')), 'Missing memory chunk content')
        anchors = chunk.get('anchors')
        require(isinstance(anchors, list) and 1 <= len(anchors) <= 30, 'Missing Introduction anchors')
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
    cards = topic.get('flashcards')
    require(isinstance(cards, list) and 1 <= len(cards) <= 5000, 'Missing flashcards')
    questions, singles = {}, set()
    for card in cards:
        require(identifier(card.get('id')) and card['id'] not in questions, 'Duplicate flashcard')
        require(all(text(card.get(field)) for field in
                    ('title', 'question', 'html', 'reference_answer', 'explanation_html', 'answer_type', 'constraints')),
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
    return questions
