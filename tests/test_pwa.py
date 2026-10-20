"""Publication contract: path-scoped PWA without offline learning mutations."""
import json
import unittest
from pathlib import Path
from urllib.parse import urljoin

WEB = Path(__file__).resolve().parents[1] / 'web'


class PwaTests(unittest.TestCase):
    def test_manifest_under_pages_subdirectory(self):
        manifest = json.loads((WEB / 'manifest.webmanifest').read_text(encoding='utf-8'))
        base = 'https://example.test/GraspMemoEdu-Pages/manifest.webmanifest'
        self.assertEqual(urljoin(base, manifest['id']), 'https://example.test/GraspMemoEdu-Pages/')
        self.assertEqual(manifest['start_url'], './')
        self.assertEqual(manifest['scope'], './')
        self.assertEqual(manifest['display'], 'standalone')
        for icon in manifest['icons']:
            path = WEB / icon['src']
            raw = path.read_bytes()
            self.assertEqual(raw[:8], b'\x89PNG\r\n\x1a\n')
            self.assertEqual(int.from_bytes(raw[16:20], 'big'), int(icon['sizes'].split('x')[0]))
        self.assertTrue(any(icon['purpose'] == 'maskable' for icon in manifest['icons']))

    def test_entry_and_install_page_are_wired(self):
        for name in ('index.html', 'download.html'):
            text = (WEB / name).read_text(encoding='utf-8')
            self.assertIn('./manifest.webmanifest', text)
            self.assertIn('./pwa.js', text)
            self.assertIn('./icon-180.png', text)
        page = (WEB / 'download.html').read_text(encoding='utf-8')
        for target in ('ios', 'android', 'windows'):
            self.assertIn(f'id="{target}"', page)

