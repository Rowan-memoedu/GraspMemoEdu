"""Synthetic labels only. Never installed in a production catalog."""
from copy import deepcopy


def atomic_topic(subject='english', topic_id='atomic-fixture'):
    intro = '甲标签是红色，乙标签是蓝色。丙标签是圆形。'
    points = [('a', '红色'), ('b', '蓝色'), ('c', '圆形')]
    cards = []
    for point, answer in points:
        for variant in (1, 2):
            cards.append({'id': f'{point}-{variant}', 'title': f'测试原子卡 {point} / {variant}',
                          'question': f'回忆测试标签 {point}。', 'html': f'<p>回忆测试标签 {point}。</p>',
                          'reference_answer': answer, 'explanation_html': f'<p>{answer}</p>',
                          'answer_type': 'text', 'constraints': '按标签精确回答。', 'point_ids': [point],
                          'interaction': {'version': 1, 'type': 'text', 'grading': 'exact', 'answers': [answer]}})
    bundle = deepcopy(cards[0])
    bundle.update(id='ab-bundle', title='测试打包卡', question='甲为 {{blank:a}}，乙为 {{blank:b}}。',
                  html='<p>甲为 {{blank:a}}，乙为 {{blank:b}}。</p>', point_ids=['a', 'b'],
                  reference_answer='红色；蓝色', explanation_html='<p>甲红色，乙蓝色。</p>',
                  interaction={'version': 1, 'type': 'fill_blank', 'grading': 'exact',
                               'blanks': [{'id': 'a', 'answers': ['红色']}, {'id': 'b', 'answers': ['蓝色']}]})
    cards.append(bundle)
    return {'id': topic_id, 'version': 'atomic-fixture-v1', 'title': '原子训练隔离验收',
            'training_mode': 'atomic_retrieval', 'subject_id': subject, 'introduction': {'html': f'<p>{intro}</p>'},
            'memory_chunks': [
                {'id': 'colors', 'title': '测试颜色组块', 'html': '<p>甲：红色；乙：蓝色。</p>',
                 'anchors': [{'start': 0, 'end': 14, 'quote': intro[:14]}],
                 'points': [{'id': point, 'text': answer} for point, answer in points[:2]]},
                {'id': 'shapes', 'title': '测试形状组块', 'html': '<p>丙：圆形。</p>',
                 'anchors': [{'start': 14, 'end': len(intro), 'quote': intro[14:]}],
                 'points': [{'id': 'c', 'text': '圆形'}]}], 'flashcards': cards}


def atomic_author_source(subject='english', topic_id='atomic-english'):
    topic = atomic_topic(subject, topic_id)
    return {'id': topic_id, 'title': topic['title'], 'subject_id': subject,
            'introduction': [{'id': 'colors', 'text': '甲标签是红色，乙标签是蓝色。'}, {'id': 'shapes', 'text': '丙标签是圆形。'}],
            'memory_chunks': [{key: value for key, value in chunk.items() if key not in ('html', 'anchors')}
                              | {'content': chunk['html'][3:-4], 'statement_ids': [chunk['id']]}
                              for chunk in topic['memory_chunks']],
            'flashcards': [{key: value for key, value in card.items() if key not in ('html', 'explanation_html')}
                          | {'explanation': card['reference_answer']} for card in topic['flashcards']]}
