"""Source-bound display relocation preserves installed banks and active attempts."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from graspmemoedu.question_bank import digest
from graspmemoedu.training_content import TrainingCatalog, fingerprint
from graspmemoedu.training_format import install, present, validate_format
from graspmemoedu.training_guidance import split_tail
from tests.fixtures import temporary_test_directory
from training_fixtures import training_bank


def fixture():
    bank = training_bank()
    bank['groups'] = [g for g in bank['groups'] if g.get('questions')]
    for group in bank['groups']:
        group.pop('parent_id', None)
    first = bank['groups'][0]
    q = first['questions'][0]
    fragment = '<p>Next exercises</p>\n<p>Compare the two methods.</p>\n'
    q['explanation_html'] = '<div class="outlineNode"><p>Original answer.</p>\n'+fragment+'</div>'
    bank['groups'].append({'id': 'later', 'title': 'Later', 'questions': []})
    bank['version'] = fingerprint(bank)
    target = bank['groups'][1]
    group = {'id': 'guidance', 'title': 'Next exercises', 'questions': [],
             'introductions': [{'id': 'guidance', 'title': 'Next exercises', 'text': 'Next exercises\n\nCompare the two methods.',
                               'html': '<div class="outlineNode">'+fragment+'</div>'}],
             'content_order': [{'type': 'introduction', 'id': 'guidance'}]}
    if target.get('parent_id'):
        group['parent_id'] = target['parent_id']
    overlay = {'schema': 1, 'bank_id': bank['id'], 'bank_version': bank['version'], 'questions': {}, 'flatten': [],
               'guidance': [{'question_id': q['id'], 'source_sha256': digest(q), 'before_group': target['id'],
                             'fragment': fragment, 'group': group}]}
    return bank, overlay


class GuidanceFormatTests(unittest.TestCase):
    def test_exact_original_html_moves_without_changing_identity_or_answers(self):
        bank, overlay = fixture()
        original = deepcopy(bank)
        validate_format(overlay, bank)
        shown = present(bank, overlay)
        self.assertEqual(shown['version'], bank['version'])
        self.assertEqual(shown['groups'][1]['id'], 'guidance')
        old = bank['groups'][0]['questions'][0]
        new = shown['groups'][0]['questions'][0]
        self.assertEqual({k:v for k,v in new.items() if k!='explanation_html'},
                         {k:v for k,v in old.items() if k!='explanation_html'})
        self.assertNotIn('Compare the two methods', new['explanation_html'])
        self.assertEqual(bank, original)

    def test_invalid_source_html_position_or_id_is_rejected(self):
        for mutate in (
            lambda m: m.update(source_sha256='0'*64),
            lambda m: m.update(fragment='<p>Invented text</p>'),
            lambda m: m.update(before_group='missing'),
            lambda m: m['group'].update(id='later'),
            lambda m: m['group']['introductions'][0].update(html='<p>Rewritten</p>'),
        ):
            bank, overlay = fixture()
            mutate(overlay['guidance'][0])
            with self.assertRaises(ValueError): validate_format(overlay, bank)
        with self.assertRaises(ValueError): split_tail('<div><p>ab</p></div>', 'b</p>')

    def test_additive_format_install_is_idempotent_and_bank_bytes_are_unchanged(self):
        bank, overlay = fixture()
        with temporary_test_directory() as temp:
            root = Path(temp); (root/'training-banks').mkdir()
            source = root/'training-banks'/ (bank['id']+'.json')
            source.write_text(json.dumps(bank), encoding='utf8')
            before = source.read_bytes()
            target = root/'format.json'; target.write_text(json.dumps(overlay), encoding='utf8')
            self.assertEqual(install(target, root), 'installed')
            self.assertEqual(install(target, root), 'already_installed')
            self.assertEqual(source.read_bytes(), before)
            catalog = TrainingCatalog(root)
            self.assertEqual(catalog.banks[bank['id']], bank)
            self.assertEqual(catalog.bank(bank['id'])['groups'][1]['id'], 'guidance')

