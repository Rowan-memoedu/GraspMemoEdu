"""Stable identities for the shared Topic / question-group dependency graph."""
from .catalog import Catalog


def group_key(bank_id, group_id):
    Catalog._identifier(bank_id)
    Catalog._identifier(group_id)
    return f'group:{bank_id}:{group_id}'


def group_parts(node_id):
    parts = node_id.split(':')
    if len(parts) == 3 and parts[0] == 'group':
        group_key(parts[1], parts[2])
        return parts[1], parts[2]
    return None


def node_key(node):
    return group_key(node['bank_id'], node['group_id']) if 'group_id' in node else node['topic_id']


def validate_node_key(value):
    if not isinstance(value, str) or not group_parts(value):
        Catalog._identifier(value)
    return value
