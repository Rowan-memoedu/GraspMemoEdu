"""Bindings contain public problem identities, never sessions or answer keys."""

SUPPORTED = {'choice', 'matching', 'number', 'string', 'fill-blanks', 'sorting', 'parsons', 'table', 'code'}

def validate_presentation(value, kind):
    import json
    if (not isinstance(value, dict) or set(value) != {'schema', 'dataset', 'code_options', 'interaction'}
            or value['schema'] != 1 or not isinstance(value['dataset'], dict)
            or not isinstance(value['code_options'], dict) or len(json.dumps(value)) > 1000000):
        raise ValueError('Invalid prepared Hyperskill presentation')
    fields = {'choice': {'options', 'is_multiple_choice'}, 'matching': {'pairs'},
              'fill-blanks': {'components'}, 'sorting': {'options'}, 'parsons': {'lines'},
              'table': {'rows', 'columns', 'is_checkbox', 'description'}}
    if set(value['dataset']) != fields.get(kind, set()):
        raise ValueError('Unexpected prepared problem fields')
    interaction = value['interaction']
    types = {'fill-blanks': 'hyperskill_fill', **{k: 'hyperskill_' + k for k in ('sorting', 'parsons', 'table', 'code')}}
    if kind in ('number', 'string'):
        if interaction is not None:
            raise ValueError('Unexpected text interaction')
    elif not isinstance(interaction, dict) or interaction.get('type') != types.get(kind, kind):
        raise ValueError('Prepared problem type differs')
    if kind == 'code':
        if set(value['code_options']) != {'code_templates', 'samples', 'limits'}:
            raise ValueError('Unexpected prepared code options')
    elif value['code_options']:
        raise ValueError('Unexpected code data')


def validate_binding(topic):
    binding = topic.get('hyperskill')
    if (topic.get('memory_chunks') != []
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
