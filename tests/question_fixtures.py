"""Public synthetic inputs shared by question-type and browser acceptance tests."""
import copy
import json

from tests.fixtures import synthetic_course


def interactions():
    matching = {'version': 1, 'type': 'matching', 'direction': 'both',
                'left': [{'id': 'l2', 'text': '$2$'}, {'id': 'l3', 'text': '$3$'}, {'id': 'lx', 'text': '左侧干扰项'}],
                'right': [{'id': 'rx', 'text': '右侧干扰项'}, {'id': 'r9', 'text': '$9$'}, {'id': 'r4', 'text': '$4$'}],
                'pairs': [{'left': 'l2', 'right': 'r4'}, {'left': 'l3', 'right': 'r9'}]}
    return [
        ('写出六乘七的结果。', {'version': 1, 'type': 'text', 'grading': 'exact', 'answers': ['42']}),
        ('判断下列说法。', {'version': 1, 'type': 'true_false', 'statements': [
            {'id': 'a', 'text': '$2+2=4$', 'correct': True}, {'id': 'b', 'text': '$3+3=7$', 'correct': False}]}),
        ('太阳从 {{blank:a}} 方升起，从 {{blank:b}} 方落下。', {'version': 1, 'type': 'fill_blank', 'grading': 'exact',
           'blanks': [{'id': 'a', 'answers': ['东']}, {'id': 'b', 'answers': ['西']}]}),
        ('将数与它的平方配对。', matching),
        ('左侧已给定，将数与它的平方配对。', {**copy.deepcopy(matching), 'fixed_side': 'left', 'direction': 'forward'}),
        ('水结冰时由什么状态变为什么状态？', {'version': 1, 'type': 'text', 'grading': 'semantic', 'answers': ['液态变成固态']}),
        ('水结冰时由 {{blank:a}} 变成 {{blank:b}}。', {'version': 1, 'type': 'fill_blank', 'grading': 'semantic',
            'blanks': [{'id': 'a', 'answers': ['液态']}, {'id': 'b', 'answers': ['固态']}]}),
    ]


def interaction_course(spec_index=1):
    course = synthetic_course(1)
    stem, spec = interactions()[spec_index]
    for question in [course['modules'][0]['example'], *course['modules'][0]['practices']]:
        question.update(question=stem, html=f'<p>{stem}</p>', interaction=copy.deepcopy(spec))
    return course


def author_source():
    lines = ['- Shared interaction acceptance', '  - Introduction', '    - 这是独立验收课程，不能发布到正式课程目录。']
    for i, (stem, spec) in enumerate(interactions(), 1):
        lines += [f'  - Example {i}', f'    - {stem}', '    - Interaction',
                  '      - ' + json.dumps(spec, ensure_ascii=False), '    - Explanation', '      - 合成题目的参考解析。']
        for j in range(1, 16):
            lines += [f'    - Practice {j}', f'      - {stem}', '      - Interaction',
                      '        - ' + json.dumps(spec, ensure_ascii=False), '      - Explanation', '        - 合成题目的参考解析。']
    return '\n'.join(lines) + '\n'
