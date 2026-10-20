"""Install a complete branch in an isolated copy and verify source fidelity."""
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sys

from graspmemoedu.academy_import import install
from graspmemoedu.atomic_content import validate_atomic
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.test_materials import TestMaterials


class Content(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.codes, self.images, self.headings = [], [], []
        self.pre = self.heading = None
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        if tag == 'pre': self.pre = ''
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'): self.heading = [tag, '']
        if tag == 'img': self.images.append(dict(attrs).get('src', ''))

    def handle_data(self, value):
        if self.pre is not None: self.pre += value
        if self.heading is not None: self.heading[1] += value

    def handle_endtag(self, tag):
        if tag == 'pre' and self.pre is not None:
            self.codes.append(self.pre); self.pre = None
        if self.heading is not None and tag == self.heading[0]:
            self.headings.append(self.heading); self.heading = None


package, previous, target, translated = map(Path, sys.argv[1:])
shutil.copytree(previous, target)
before = Catalog.load(Settings.load(target))
original = before.topics.copy()
result = install(package, target, extend_course=True)
catalog = Catalog.load(Settings.load(target))
manifest = json.loads((package / 'manifest.json').read_bytes())
ids = manifest['test_materials']['topics']
policy = TestMaterials(target, catalog)
cards = images = code_blocks = 0
for ident in ids:
    topic = catalog.topics[ident]; validate_atomic(topic)
    theory = topic['hyperskill']['theory_id']
    source = json.loads((translated / f'translated.step-{theory}.json').read_bytes())
    expected, actual = Content(source['step']['block']['text']), Content(topic['introduction']['html'])
    assert actual.codes == expected.codes, ident + ' changed code'
    assert actual.headings == expected.headings, ident + ' changed heading hierarchy'
    assert len(actual.images) == len(expected.images), ident + ' lost image'
    assert all(url.startswith('data:image/') for url in actual.images), ident + ' unresolved image'
    assert topic['memory_chunks'] == []
    assert len(topic['flashcards']) == len(topic['memory_points'])
    assert not policy.topic_visible(ident, None)
    assert policy.topic_visible(ident, {'role': 'account', 'purpose': 'test', 'is_advanced_learner': True, 'is_student': True})
    cards += len(topic['flashcards']); images += len(actual.images); code_blocks += len(actual.codes)
assert len(ids) == 33 and cards == 488
assert all(catalog.topics[k] == value for k, value in original.items())
assert install(package, target, extend_course=True)['status'] == 'unchanged'
assert not list(target.glob('*.sqlite*'))
print(json.dumps({**result, 'verified_topics': len(ids), 'cards': cards, 'theory_images': images,
    'theory_code_blocks': code_blocks, 'existing_topics_unchanged': True, 'idempotent': True,
    'learning_database_created': False, 'test_visibility': True}))
