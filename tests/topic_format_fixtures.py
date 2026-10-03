"""Synthetic author inputs, never real course data or publication candidates."""


def node(ident, text='', children=(), **metadata):
    return {'id': ident, 'text': text, 'children': list(children), **metadata}


def introduction_document():
    return {'topic_format_version': 2, 'id': 'format-introduction', 'title': 'Synthetic introduction',
            'topic_kind': 'introduction', 'source_rem_id': 'SyntheticDocument', 'nodes': [
                node('intro-a', 'Introduction: 第一节', [
                    node('definition', '**定义**：保留 $x=2$ 与原文。', source_rem_id='DefinitionRem'),
                    node('hint', 'Check the stated conditions.', callout='hint'),
                    node('source', source={'name': 'Synthetic book', 'item': '练习 1.2', 'difficulty': '★'}, callout='source'),
                    node('diagram', '**范式**：\n```mermaid\nflowchart TD\nA["step 1"] --> B["step 2.1"]\nA --> C["step 2.2"]\n```'),
                    node('reference', '参见 [[id:LaterDocument|后续内容]]。'),
                    node('aside', '保留题外话原文。', callout='aside', source_rem_id='AsideRem',
                         source_tags=[{'tag_rem_id': 'AsideTagRem', 'name': '题外话'}]),
                    node('requirement', '保留要求原文。', callout='requirement'),
                ], source_rem_id='IntroRem'),
                node('intro-b', 'Introduction: 第二节', [node('tail', 'Second section, unchanged.')]),
            ]}


def lesson_document():
    document = introduction_document()
    document.update(id='format-lesson', topic_kind='lesson')
    practices = [node(f'practice-{n}', f'Practice {n}', [
        node(f'practice-{n}-question', f'Calculate $2+{n}$.'),
        node(f'practice-{n}-explanation', 'Explanation', [node(f'practice-{n}-answer', f'The supplied answer is ${n+2}$.')]),
    ]) for n in range(1, 8)]
    example = node('example-1', 'Example 1: Synthetic', [
        node('example-question', 'Calculate $2+3$.'),
        node('example-explanation', 'Explanation', [
            node('example-answer', 'The supplied answer is $5$.'),
            node('answer-idea', '[[id:DefinitionRem|定义]]'),
        ]), *practices,
    ])
    document['nodes'].insert(1, example)
    return document
