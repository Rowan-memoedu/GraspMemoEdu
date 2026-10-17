"""A single atomic pointer selects one complete set of content configuration."""
import json
from pathlib import Path
import re

POINTER = 'active-content.json'


def content_root(data_dir):
    root = Path(data_dir)
    pointer = root / POINTER
    if not pointer.exists():
        return root
    value = json.loads(pointer.read_text(encoding='utf-8'))
    if set(value) != {'schema', 'generation'} or value['schema'] != 1 or not re.fullmatch(r'[a-f0-9]{64}', value['generation']):
        raise ValueError('Invalid active content pointer')
    target = root / 'content-batches' / value['generation']
    if not target.resolve().is_relative_to(root.resolve()) or not target.is_dir():
        raise ValueError('Active content batch is missing')
    return target
