from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.mathacademy_import import Assets, SourceIndex, prepare, install, source_fingerprint
from graspmemoedu.mathacademy_graphics import prepare_repairs, install_repairs, validate_patch, repair_topic
from graspmemoedu.site_backup import backup_site, restore_site
from tests.test_mathacademy_import import source

TEX = '\\documentclass{article}\n\\begin{document}one\\end{document}'
PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF9sAAAAASUVORK5CYII='


class MathAcademyGraphicsTests(unittest.TestCase):
    def setUp(self):
        from tests.test_mathacademy_import import MathAcademyTests
        MathAcademyTests.setUp(self)

    def test_inline_drawings_follow_source_field_positions_and_explicit_images(self):
        raw = TEX + '\n<img src="/graphics/keep" />\n' + TEX.replace('one', 'two')
        rendered = '<img src="/graphics/first"><img src="/graphics/keep"><img src="/graphics/last">'
        mapping = Assets.inline_graphics(raw, rendered)
        self.assertEqual(mapping, {'/graphics/first': TEX, '/graphics/last': TEX.replace('one', 'two')})
        self.assertEqual(Assets.inline_graphics(raw, rendered.replace('/graphics/keep', '/graphics/other')), {})
        self.assertEqual(Assets.inline_graphics(raw, rendered + '<img src="/graphics/extra">'), {})
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            Assets.inline_graphics(TEX + TEX.replace('one', 'two'), '<img src="/graphics/a"><img src="/graphics/a">')

    def test_new_import_restores_only_source_provided_drawings(self):
        assets = Assets(self.cache, {}); gaps = []
        with patch.object(assets, 'compile_inline', return_value=PNG) as compiler:
            result = assets.render(TEX, '<p>Original <strong>layout</strong></p><img src="/graphics/a">', gaps)
        self.assertIn('<p>Original <strong>layout</strong></p>', result['_html'])
        self.assertIn(PNG, result['_html']); self.assertEqual(gaps, [])
        compiler.assert_called_once_with(TEX)
        assets.render('Unrelated source', '<img src="/graphics/q-1-a-2">', gaps)
        self.assertEqual(gaps, [{'code': 'image_missing', 'reference': '/graphics/q-1-a-2'}])

    def old_package(self):
        import zipfile
        self.index.archive.close()
        with zipfile.ZipFile(self.topics, 'w') as archive:
            for tid in (1, 2, 3):
                item = source(tid)
                item['lesson']['steps'][0]['tutorial'].update(content='Caption\n\n' + TEX,
                    html='<p>Caption</p><img src="/graphics/source-' + str(tid) + '">')
                archive.writestr(f'topic-json/topic-{tid}.json', json.dumps(item))
        self.index = SourceIndex(self.topics, self.graphs); self.addCleanup(self.index.archive.close)
        with patch.object(Assets, 'inline_graphics', return_value={}):
            prepare(self.index, self.index.scope([20]), self.cache, self.package)
        install(self.package, self.data)
        return Catalog.load(Settings.load(self.data))

    def test_repairs_are_scoped_atomic_idempotent_and_survive_backup(self):
        old = self.old_package(); pointer = self.data / 'active-content.json'; previous = pointer.read_bytes()
        repairs = self.root / 'repairs'
        with patch.object(Assets, 'compile_inline', return_value=PNG):
            report = prepare_repairs(self.package, [10], self.cache, repairs)
        self.assertEqual(report['topics_changed'], 2)
        with self.assertRaisesRegex(RuntimeError, 'pre-activation'):
            install_repairs(repairs, self.data, fail_at='activate')
        self.assertEqual(pointer.read_bytes(), previous)
        result = install_repairs(repairs, self.data)
        self.assertEqual(result['restored_occurrences'], 2)
        loaded = Catalog.load(Settings.load(self.data))
        self.assertEqual(old.topics['ma-topic-3'], loaded.topics['ma-topic-3'])
        self.assertEqual(old.courses, loaded.courses)
        self.assertEqual(old.topics['ma-topic-1']['version'], loaded.topics['ma-topic-1']['version'])
        self.assertIn(PNG, loaded.topics['ma-topic-2']['introductions'][0]['html'])
        self.assertEqual(install_repairs(repairs, self.data)['status'], 'unchanged')
        backup_site(Settings.load(self.data), self.root / 'repaired.zip')
        restore_site(self.root / 'repaired.zip', self.root / 'restored')
        self.assertEqual(loaded.topics, Catalog.load(Settings.load(self.root / 'restored')).topics)

    def test_repair_validation_rejects_body_changes_and_wrong_source(self):
        old = self.old_package(); repairs = self.root / 'repairs'
        with patch.object(Assets, 'compile_inline', return_value=PNG):
            prepare_repairs(self.package, [10], self.cache, repairs)
        entry = json.loads((repairs / 'manifest.json').read_text(encoding='utf-8'))['topics'][0]
        after = json.loads((repairs / entry['path']).read_text(encoding='utf-8'))
        raw = json.loads((self.package / 'mathacademy-sources/topics' / (entry['topic_id'] + '.json')).read_text(encoding='utf-8'))
        altered = deepcopy(after); altered['introductions'][0]['html'] = altered['introductions'][0]['html'].replace('Caption', 'Changed')
        with self.assertRaisesRegex(ValueError, 'unrelated'):
            validate_patch(old.topics[entry['topic_id']], altered, raw, entry['changes'])
        raw['lesson']['steps'][0]['tutorial']['content'] += 'changed'
        with self.assertRaisesRegex(ValueError, 'Source differs'):
            validate_patch(old.topics[entry['topic_id']], after, raw, entry['changes'])

    def test_uncompilable_source_is_retained_without_inventing_a_drawing(self):
        old = self.old_package(); tid = 'ma-topic-1'
        raw = json.loads((self.package / 'mathacademy-sources/topics' / (tid + '.json')).read_text(encoding='utf-8'))
        failures = []
        with patch.object(Assets, 'compile_inline', side_effect=RuntimeError('TeX graphic conversion failed: source syntax')):
            after, changes = repair_topic(old.topics[tid], raw, Assets(self.cache, {}), failures)
        self.assertEqual(after, old.topics[tid]); self.assertEqual(changes, [])
        self.assertEqual(len(failures), 1)
        with patch.object(Assets, 'compile_inline', side_effect=RuntimeError('Tectonic compiler is required')):
            with self.assertRaisesRegex(RuntimeError, 'compiler is required'):
                repair_topic(old.topics[tid], raw, Assets(self.cache, {}), [])

    def test_commented_out_drawings_stay_hidden_and_do_not_count_as_missing(self):
        old = self.old_package(); tid = 'ma-topic-1'; before = deepcopy(old.topics[tid])
        raw = json.loads((self.package / 'mathacademy-sources/topics' / (tid + '.json')).read_text(encoding='utf-8'))
        body = raw['lesson']['steps'][0]['tutorial']
        body['content'] += '<!--' + TEX.replace('one', 'hidden') + '-->'
        body['html'] += '<!--<img src="/graphics/hidden">-->'
        before['version'] = before['content_version'] = source_fingerprint(raw)
        before['_source_gaps'].append({'code': 'image_missing', 'reference': '/graphics/hidden'})
        next(r for r in before['availability']['reasons'] if r['code'] == 'image_missing')['count'] += 1
        with patch.object(Assets, 'compile_inline', return_value=PNG) as compiler:
            after, changes = repair_topic(before, raw, Assets(self.cache, {}), [])
        compiler.assert_called_once_with(TEX)
        self.assertEqual(len(changes[0]['images']), 1)
        self.assertNotIn('hidden', after['introductions'][0]['html'])
        self.assertFalse(any(g['code'] == 'image_missing' for g in after['_source_gaps']))

    def test_paper_margin_removal_preserves_every_nonwhite_pixel(self):
        import base64
        import io
        from PIL import Image, ImageChops
        page = Image.new('RGB', (100, 160), 'white')
        for x in range(40, 60):
            for y in range(70, 90):
                page.putpixel((x, y), (x, y, 220))
        raw = io.BytesIO(); page.save(raw, format='PNG')
        assets = Assets(self.cache, {})
        with patch.object(assets, 'compile', return_value='data:image/png;base64,' + base64.b64encode(raw.getvalue()).decode()):
            result = assets.compile_inline(TEX)
        with Image.open(io.BytesIO(base64.b64decode(result.split(',', 1)[1]))) as image:
            self.assertEqual(image.size, (30, 30))
            self.assertIsNone(ImageChops.difference(image, page.crop((35, 65, 65, 95))).getbbox())


if __name__ == '__main__':
    unittest.main()
