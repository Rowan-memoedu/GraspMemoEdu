import importlib.util
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('version_web_assets', ROOT / 'scripts/version_web_assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class StartupAssetTests(unittest.TestCase):
    def test_static_closure_deduplicates_cycles_and_keeps_optional_engines_lazy(self):
        sources = {assets.WEB / name: value for name, value in {
            'app.js': 'import {a} from "./shared.js?v=abc";\nimport("./optional.js");',
            'portal.js': 'import {a} from "./shared.js";',
            'pwa.js': '',
            'shared.js': 'import "./app.js";\nimport {x} from "./vendor/entry.js";',
        }.items()}
        self.assertEqual(assets.startup_modules(sources),
                         ['app.js', 'portal.js', 'pwa.js', 'shared.js', 'vendor/entry.js'])

    def test_published_hints_match_current_module_urls_without_extra_fetches(self):
        web = ROOT / 'web'
        index = (web / 'index.html').read_text(encoding='utf-8')
        hints = re.findall(r'<link rel="modulepreload" href="([^"]+)">', index)
        self.assertEqual(len(hints), len(set(hints)))
        self.assertGreater(len(hints), 10)
        scripts = {p: p.read_text(encoding='utf-8') for p in web.glob('*.js')}
        self.assertEqual({url.split('?')[0][2:] for url in hints}, set(assets.startup_modules(scripts)))
        referenced = set(re.findall(r'''["'](\./[^"']+\.(?:m?js)\?v=[a-f0-9]+)["']''', '\n'.join(scripts.values())))
        referenced.update(re.findall(r'<script type="module" src="([^"]+)">', index))
        for url in hints:
            self.assertIn(url, referenced, f'Preload must reuse the exact module URL: {url}')
            self.assertTrue((web / url.split('?')[0]).is_file())
        self.assertFalse(any('/chat/' in url or 'mermaid.core' in url for url in hints))


if __name__ == '__main__':
    unittest.main()
