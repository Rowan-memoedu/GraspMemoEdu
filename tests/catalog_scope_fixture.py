"""A subject-neutral selection tree, including siblings and direct members."""
import json
from .subject_matrix_fixture import write_matrix
from graspmemoedu.training_content import fingerprint


def write_scopes(root):
    topics = write_matrix(root)
    path = root / 'catalog.json'
    catalog = json.loads(path.read_bytes())
    for course in catalog['courses']:
        ids = [t['id'] for t in course['topics']]
        course['units'] = [
            {'id': 'domain', 'title': 'Computer Science', 'topic_ids': []},
            {'id': 'languages', 'title': 'Programming Languages', 'parent_id': 'domain', 'topic_ids': []},
            {'id': 'language', 'title': 'Java', 'parent_id': 'languages', 'topic_ids': [ids[0]]},
            {'id': 'basics', 'title': 'Basics', 'parent_id': 'language', 'topic_ids': ids[1:-1]},
            {'id': 'sibling', 'title': 'Another branch', 'parent_id': 'domain', 'topic_ids': ids[-1:]}]
    path.write_text(json.dumps(catalog), encoding='utf-8')
    for path in (root / 'training-banks').glob('*.json'):
        bank = json.loads(path.read_bytes())
        bank['groups'][0]['parent_id'] = 'language'
        bank['groups'][-1]['parent_id'] = 'sibling'
        bank['groups'][-1]['questions'][0]['interaction'] = {'version': 1, 'type': 'text', 'grading': 'exact', 'answers': ['2']}
        bank['groups'][:0] = [
            {'id': 'domain', 'title': 'Computer Science', 'parent_id': None, 'questions': []},
            {'id': 'languages', 'title': 'Programming Languages', 'parent_id': 'domain', 'questions': []},
            {'id': 'language', 'title': 'Java', 'parent_id': 'languages', 'questions': []},
            {'id': 'sibling', 'title': 'Another branch', 'parent_id': 'domain', 'questions': []}]
        bank['version'] = fingerprint(bank)
        path.write_text(json.dumps(bank), encoding='utf-8')
    return topics
