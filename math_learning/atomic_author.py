"""Finish safe rendered source by binding exact Unicode statement offsets."""
import hashlib
import json
import sys

from .atomic_content import plain_text, validate_atomic


def compile_anchors(topic, statements):
    offset, anchors = 0, {}
    for statement in statements:
        quote = plain_text(statement['html'])
        if not isinstance(statement['id'], str) or statement['id'] in anchors or not quote.strip():
            raise ValueError('Duplicate or empty Introduction statement')
        anchors[statement['id']] = {'start': offset, 'end': offset + len(quote), 'quote': quote}
        offset += len(quote)
    for chunk in topic['memory_chunks']:
        ids = chunk.pop('statement_ids')
        if not isinstance(ids, list) or len(ids) != len(set(ids)):
            raise ValueError('Invalid statement references')
        chunk['anchors'] = [anchors[statement] for statement in ids]
    topic['version'] = hashlib.sha256(json.dumps(topic, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    validate_atomic(topic)
    return topic


if __name__ == '__main__':
    source = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    topic = compile_anchors(source['topic'], source['statements'])
    sys.stdout.buffer.write((json.dumps(topic, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
