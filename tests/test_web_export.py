import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PublicExportTests(unittest.TestCase):
    def export(self, root, origin):
        output = root / 'web'
        result = subprocess.run(
            [sys.executable, '-m', 'graspmemoedu', '--data-dir', str(root / 'data'),
             'export-web', '--output', str(output), '--api-origin', origin],
            capture_output=True, text=True, encoding='utf-8')
        return result, output

    def test_public_export_has_only_static_assets_and_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, output = self.export(Path(temporary), 'https://example.trycloudflare.com')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue({'training.js', 'training.css', 'training-messages.js', 'learning-ui.js'} <= {p.name for p in output.iterdir()})
            self.assertTrue({'review-papers.js', 'review-paper-messages.js'} <= {p.name for p in output.iterdir()})
            self.assertTrue({'review-directory.js', 'task-tree.js', 'catalog-outline.js', 'content-timer.js'} <= {p.name for p in output.iterdir()})
            pwa_files = {'download.html', 'install.css', 'pwa.css', 'pwa.js', 'sw.js', 'manifest.webmanifest',
                         'icon-180.png', 'icon-192.png', 'icon-512.png', 'icon-maskable-512.png'}
            self.assertTrue(pwa_files <= {p.name for p in output.iterdir()})
            self.assertEqual({p.name for p in output.iterdir()} - pwa_files - {'student-dashboard.js', 'presence.js', 'training.js', 'training.css', 'training-messages.js', 'learning-ui.js', 'review-papers.js', 'review-paper-messages.js', 'review-directory.js', 'task-tree.js', 'catalog-outline.js', 'content-timer.js', 'self-assessment.js', 'academy-messages.js', 'hyperskill.js', 'memory-chunk.js', 'point-assessment.js', 'context-library.js', 'context-library.css', 'context-library-messages.js', 'training-audio.js'},
                             {'.nojekyll', 'app.js', 'deployment.json', 'favicon.svg', 'index.html', 'portal.js', 'review.js', 'atomic.js','learning-cache.js', 'response-cache.js', 'question-input.js', 'composite-input.js', 'math-presentation.js', 'content-report.js', 'help-feedback.js', 'catalog-picker.js', 'portal.css', 'subjects.js', 'subjects.css', 'styles.css', 'i18n.js', 'reader-messages.js', 'portal-messages.js', 'static-messages.js', 'course-graph.js', 'course-graph.css', 'topic-content.js', 'topic-content.css', 'topic-callouts.css', 'vendor'})
            self.assertEqual(json.loads((output / 'deployment.json').read_text()),
                             {'api_origin': 'https://example.trycloudflare.com'})
            self.assertEqual((output / 'app.js').read_bytes(),
                             (Path(__file__).resolve().parents[1] / 'web/app.js').read_bytes())

    def test_invalid_or_credential_bearing_origins_fail_before_export(self):
        for origin in ['http://localhost:8767', 'https://name:secret@example.com',
                       'https://example.com/path', 'https://example.com?token=secret']:
            with self.subTest(origin=origin), tempfile.TemporaryDirectory() as temporary:
                result, output = self.export(Path(temporary), origin)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

    def test_local_export_has_empty_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, output = self.export(Path(temporary), '')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((output / 'deployment.json').read_text()), {'api_origin': ''})


if __name__ == '__main__':
    unittest.main()
