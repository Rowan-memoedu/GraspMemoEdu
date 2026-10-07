"""Q&A image delivery, privacy, actual decoding and portable backups."""
import base64
import io
import tempfile
from pathlib import Path
import unittest
from PIL import Image
from graspmemoedu.service import DomainError
from graspmemoedu.site_backup import backup_site, restore_site
from tests import test_help_main as helpers


class HelpImageTests(unittest.TestCase):
    setUp = helpers.MainConversationTests.setUp
    identity = helpers.MainConversationTests.identity
    send = helpers.MainConversationTests.send
    payload = helpers.MainConversationTests.payload
    center = helpers.MainConversationTests.center
    message = helpers.MainConversationTests.message
    learning_snapshot = helpers.MainConversationTests.learning_snapshot

    def raw(self):
        image = Image.new('RGB', (12, 8), 'red')
        output = io.BytesIO(); image.save(output, format='PNG')
        return output.getvalue()

    def upload(self, headers=None, report=None, is_main=True, raw=None):
        return self.client.post('/api/help-images', headers=headers or self.headers,
            json={'report_id': report or self.student, 'is_main': is_main,
                  'data': base64.b64encode(self.raw() if raw is None else raw).decode()})

    def get(self, ident, headers=None):
        return self.client.get('/api/help-images?image_id=' + ident, headers=headers or self.headers)

    def test_roundtrip_both_participants_image_only_edit_quote_and_progress_unchanged(self):
        before = self.learning_snapshot()
        image = self.upload().json()
        response = self.message(headers=self.headers, message=image['markdown'])
        self.assertEqual(response.status_code, 200, response.text)
        message = response.json()['message']
        for headers in (self.headers, self.admin_headers):
            raw = base64.b64decode(self.get(image['id'], headers).json()['data_url'].split(',')[1])
            self.assertEqual(Image.open(io.BytesIO(raw)).size, (12, 8))
        self.assertEqual(self.message(reply_to=message['id'], message='**已查看图片** $x^2$').status_code, 200)
        self.assertEqual(self.message(headers=self.headers, message_id=message['id'], expected_revision=1,
            message='修改 ' + image['markdown']).status_code, 200)
        report = self.send().json()['id']
        content_image = self.upload(self.admin_headers, report, False).json()
        self.assertEqual(self.client.post('/api/help-messages', headers=self.admin_headers,
            json={'report_id': report, 'request_id': 'image-content', 'message': content_image['markdown']}).status_code, 200)
        self.assertEqual(self.get(content_image['id']).status_code, 200)
        self.assertEqual(self.learning_snapshot(), before)

    def test_access_cross_thread_refs_and_reassignment(self):
        image = self.upload().json()
        other, other_headers = self.identity(is_student=True)
        admin, admin_headers = self.identity(is_admin=True)
        for headers in (other_headers, admin_headers):
            self.assertEqual(self.get(image['id'], headers).status_code, 404)
            self.assertEqual(self.upload(headers).status_code, 404)
        report = self.send().json()['id']
        self.assertEqual(self.client.post('/api/help-messages', headers=self.headers,
            json={'report_id': report, 'request_id': 'cross-thread', 'message': image['markdown']}).status_code, 422)
        self.assertEqual(self.message(other, message=image['markdown']).status_code, 422)
        self.assertEqual(self.client.get('/api/help-images?image_id=' + image['id']).status_code, 401)
        self.app.state.help_requests.assign_admin(self.student, admin)
        self.assertEqual(self.get(image['id'], self.admin_headers).status_code, 404)
        self.assertEqual(self.get(image['id'], admin_headers).status_code, 200)
        self.store.set_permissions(self.student, features=['learn'])
        self.assertEqual(self.get(image['id']).status_code, 403)

    def test_actual_image_validation_size_and_metadata_removal(self):
        for raw in (b'<svg onload="alert(1)"></svg>', b'not an image', self.raw()[:20], b'x' * (2 * 1024 * 1024 + 1)):
            self.assertEqual(self.upload(raw=raw).status_code, 422)
        payload = {'report_id': self.student, 'is_main': True, 'data': 'not base64!'}
        self.assertEqual(self.client.post('/api/help-images', headers=self.headers, json=payload).status_code, 422)
        raw = self.raw() + b'<script>private metadata</script>'
        image = self.upload(raw=raw).json()
        stored = base64.b64decode(self.get(image['id']).json()['data_url'].split(',')[1])
        self.assertNotIn(b'<script>', stored)
        self.assertEqual(self.client.get('/api/help-images?image_id=../../private', headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post('/api/help-images', headers={**self.headers, 'Content-Type': 'application/json'},
            content=b'x' * (3 * 1024 * 1024 + 1)).status_code, 413)

    def test_backup_restores_image_and_rejects_missing_or_changed_attachment(self):
        image = self.upload().json()
        self.message(headers=self.headers, message=image['markdown'])
        with tempfile.TemporaryDirectory(dir=self.settings.data_dir.parent) as folder:
            folder = Path(folder)
            archive = folder / 'site.zip'
            backup_site(self.app.state.settings, archive)
            restored = folder / 'restored'
            restore_site(archive, restored)
            self.assertEqual((restored / 'help-images' / (image['id'] + '.png')).read_bytes(),
                (self.settings.data_dir / 'help-images' / (image['id'] + '.png')).read_bytes())
        path = self.settings.data_dir / 'help-images' / (image['id'] + '.png')
        path.write_bytes(b'corrupt')
        self.assertEqual(self.get(image['id']).status_code, 409)
        with self.assertRaises((ValueError, DomainError)):
            backup_site(self.app.state.settings, self.settings.data_dir / 'broken.zip')
