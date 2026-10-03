"""Validate text-correction anchors against the exact published content version."""
from html.parser import HTMLParser

from .service import DomainError


class ContentText(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(html)

    def handle_data(self, data):
        self.parts.append(data)


def validate_correction(topic, question_id, correction, *, question=None, content_version=None):
    def require(condition, message='所选文字与正文不一致，请重新选择。', status=422):
        if not condition:
            raise DomainError('invalid_correction', message, status)

    require(correction['content_version'] == (content_version or topic['version']), '正文版本已变化，请刷新后重新选择文字。', 409)
    block = correction['content_block_id']
    if question_id:
        questions = (topic['flashcards'] if topic.get('training_mode') == 'atomic_retrieval'
                     else [q for m in topic['modules'] for q in [m['example'], *m['practices']]])
        question = question or next((q for q in questions if q['id'] == question_id), None)
        require(question is not None)
        fields = {f'question:{question_id}': 'html', f'explanation:{question_id}': 'explanation_html'}
        require(block in fields)
        html = question[fields[block]]
    else:
        if topic.get('topic_format_version') == 2:
            intro = next((item for item in topic['introductions'] if block == 'introduction:' + item['id']), None)
            require(intro is not None)
            html = intro['html']
        else:
            require(block == 'introduction')
            html = topic['introduction']['html']
    text = ''.join(ContentText(html).parts)
    quote = correction['quote']
    start, end = quote['start'], quote['end']
    require(0 <= start < end <= len(text) and text[start:end] == quote['exact'])
    require(text[max(0, start - 50):start] == quote['prefix'] and text[end:end + 50] == quote['suffix'])
    require(correction['operation'] in ('replace', 'delete'))
    require((correction['operation'] == 'delete') == (correction['replacement'] == ''))
    require(not any('\x00' in v for v in [quote['exact'], correction['replacement'], correction['comment']]))
    return correction
