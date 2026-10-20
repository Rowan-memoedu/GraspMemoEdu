"""Bindings contain public problem identities, never sessions or answer keys."""

SUPPORTED = {'choice', 'matching', 'number', 'string', 'fill-blanks', 'sorting', 'parsons', 'table', 'code'}


def validate_binding(topic):
    binding = topic.get('hyperskill')
    if (topic.get('subject_id') != 'cs' or topic.get('memory_chunks') != []
            or not isinstance(binding, dict) or set(binding) != {'topic_id', 'theory_id'}
            or any(type(v) is not int or v <= 0 for v in binding.values())):
        raise ValueError('Invalid Hyperskill topic binding')
    seen = set()
    for card in topic['flashcards']:
        source = card.get('hyperskill')
        if (not isinstance(source, dict) or set(source) != {'step_id', 'type'}
                or type(source['step_id']) is not int or source['step_id'] <= 0
                or source['step_id'] in seen or source['type'] not in SUPPORTED
                or len(card['point_ids']) != 1 or card.get('interaction') is not None
                or card.get('reference_answer') != '' or card.get('explanation_html') != ''):
            raise ValueError('Invalid Hyperskill problem binding')
        seen.add(source['step_id'])
