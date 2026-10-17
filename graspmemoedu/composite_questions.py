"""Version 2 source interactions; a section transition is never a scored attempt."""
import json
import re

MAX_ANSWER = 16384
ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,79}\Z')


def validate(spec):
    if spec.get('type') == 'choice':
        if set(spec) - {'version', 'type', 'grading', 'options', 'correct_id', 'shuffle'}:
            raise ValueError('Unknown choice field')
        rows = spec.get('options')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
            raise ValueError('Invalid choice options')
        ids = [x['id'] for x in rows]
        if len(set(ids)) != len(ids) or any(not ID.fullmatch(x) for x in ids):
            raise ValueError('Invalid option IDs')
        if spec.get('grading') not in ('unavailable', 'exact'):
            raise ValueError('Invalid choice grading')
        if spec['grading'] == 'exact' and spec.get('correct_id') not in ids:
            raise ValueError('Missing correct option')
        return
    if (spec.get('type') != 'composite' or set(spec) - {'version', 'type', 'grading', 'controls', 'sections'}
            or spec.get('grading') not in ('unavailable', 'exact', 'semantic')):
        raise ValueError('Invalid composite interaction')
    controls, sections = spec.get('controls'), spec.get('sections')
    if not isinstance(controls, list) or not 1 <= len(controls) <= 128 or not isinstance(sections, list) or not 1 <= len(sections) <= 64:
        raise ValueError('Composite size exceeds limits')
    ids = set()
    for control in controls:
        if (set(control) - {'id', 'type', 'label', 'options', 'answers', 'width', 'input_type', 'context'}
                or not ID.fullmatch(control.get('id', '')) or control['id'] in ids
                or control.get('type') not in ('select', 'text')):
            raise ValueError('Invalid composite control')
        ids.add(control['id'])
        if control['type'] == 'select':
            options = control.get('options')
            if not isinstance(options, list) or not 1 <= len(options) <= 64:
                raise ValueError('Invalid select options')
            option_ids = [o['id'] for o in options]
            if len(set(option_ids)) != len(option_ids) or any(not ID.fullmatch(x) for x in option_ids):
                raise ValueError('Invalid select option identity')
        if spec['grading'] == 'exact':
            if not isinstance(control.get('answers'), list) or not control['answers']:
                raise ValueError('Missing control answer')
    covered = []
    section_ids = set()
    for section in sections:
        if (set(section) != {'id', 'html', 'control_ids', 'complete'} or not ID.fullmatch(section.get('id', ''))
                or section['id'] in section_ids or not isinstance(section['html'], str)
                or type(section['complete']) is not bool or not isinstance(section['control_ids'], list)
                or set(section['control_ids']) - ids):
            raise ValueError('Invalid section')
        section_ids.add(section['id'])
        covered.extend(section['control_ids'])
    if set(covered) != ids or len(set(covered)) != len(covered):
        raise ValueError('Every control belongs to exactly one section')
    if spec['grading'] != 'unavailable' and not all(s['complete'] for s in sections):
        raise ValueError('Incomplete sections cannot be graded')


def public(spec):
    if spec['type'] == 'choice':
        return {k: spec[k] for k in ('version', 'type', 'options', 'grading')}
    return {'version': 2, 'type': 'composite', 'grading': spec['grading'],
            'sections': spec['sections'], 'controls': [{k: v for k, v in c.items() if k != 'answers'} for c in spec['controls']]}


def parse(spec, answer, object_pairs_hook):
    if not isinstance(answer, str) or not 1 <= len(answer) <= MAX_ANSWER:
        raise ValueError('Invalid composite answer size')
    value = json.loads(answer, object_pairs_hook=object_pairs_hook)
    if spec['type'] == 'choice':
        if not isinstance(value, dict) or set(value) != {'selected'} or value['selected'] not in {o['id'] for o in spec['options']}:
            raise ValueError('Invalid choice')
        return value
    if (not isinstance(value, dict) or set(value) != {'section', 'values'} or type(value['section']) is not int
            or value['section'] != len(spec['sections']) - 1 or not all(s['complete'] for s in spec['sections'])):
        raise ValueError('Only the complete final section may submit the whole question')
    values = value['values']
    if not isinstance(values, dict) or set(values) != {c['id'] for c in spec['controls']}:
        raise ValueError('Missing or extra control answers')
    for c in spec['controls']:
        v = values[c['id']]
        if not isinstance(v, str) or not v.strip() or len(v) > 2000:
            raise ValueError('Empty control answer')
        if c['type'] == 'select' and v not in {o['id'] for o in c['options']}:
            raise ValueError('Unknown select option')
    return value
