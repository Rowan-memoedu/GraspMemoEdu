"""Move source-bound annotation dividers into bank guidance without changing identity."""
from copy import deepcopy
from html.parser import HTMLParser
import re

from .question_bank import digest


class _BalancedFragment(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise ValueError('Guidance must contain complete original HTML blocks')

    def handle_startendtag(self, tag, attrs):
        pass


def split_tail(original, fragment):
    """The only permitted change is moving complete final blocks out of one wrapper."""
    wrapper = re.match(r'<div\b[^>]*>', original)
    if not wrapper or not original.endswith('</div>') or not isinstance(fragment, str) or not fragment.strip():
        raise ValueError('Guidance requires an original wrapped annotation tail')
    body = original[wrapper.end():-6]
    if not body.endswith(fragment) or not body[:-len(fragment)].strip():
        raise ValueError('Guidance must be an exact nonempty original annotation tail')
    prefix = body[:-len(fragment)]
    for part in (prefix, fragment):
        parser = _BalancedFragment()
        parser.feed(part)
        parser.close()
        if parser.stack:
            raise ValueError('Guidance cannot split an original HTML block')
    return wrapper[0] + prefix + '</div>', wrapper[0] + fragment + '</div>'


def apply_guidance(groups, moves):
    if not isinstance(moves, list) or len(moves) > 200:
        raise ValueError('Invalid guidance relocation list')
    groups = deepcopy(groups)
    seen = set()
    for move in moves:
        if not isinstance(move, dict) or set(move) != {'question_id', 'source_sha256', 'before_group', 'fragment', 'group'}:
            raise ValueError('Invalid source-bound guidance relocation')
        ident = move['question_id']
        originals = [(i, q) for i, g in enumerate(groups) for q in g.get('questions', []) if q['id'] == ident]
        before = next((i for i, g in enumerate(groups) if g['id'] == move['before_group']), None)
        if ident in seen or len(originals) != 1 or before is None or before != originals[0][0] + 1:
            raise ValueError('Guidance must precede the next original group')
        seen.add(ident)
        _, original = originals[0]
        if digest(original) != move['source_sha256'] or original.get('multi_step'):
            raise ValueError('Guidance must bind to an unchanged original question')
        remaining, extracted = split_tail(original['explanation_html'], move['fragment'])
        group = deepcopy(move['group'])
        if (set(group) - {'id', 'title', 'parent_id', 'introductions', 'questions', 'content_order'}
                or group.get('questions') != [] or len(group.get('introductions', [])) != 1
                or group.get('parent_id') != groups[before].get('parent_id')
                or group['introductions'][0].get('html') != extracted):
            raise ValueError('Guidance must preserve the original annotation HTML')
        original['explanation_html'] = remaining
        groups.insert(before, group)
    return groups


def validate_guidance(moves, bank):
    from .training_content import fingerprint, validate
    candidate = {**bank, 'groups': apply_guidance(bank['groups'], moves)}
    candidate['version'] = fingerprint(candidate)
    validate(candidate)

