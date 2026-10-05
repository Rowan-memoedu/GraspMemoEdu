"""Optional classifications and snapshot-bound selections shared by both viewers."""
from contextlib import closing
import json
import unittest
from graspmemoedu.feedback_selection import FeedbackContent, content_hash, validate_selection
from graspmemoedu.service import DomainError
from tests import test_help_requests as helpers
from tests import test_help_replies as replies
from tests import test_administrator as admins
from tests.question_fixtures import interactions


class FeedbackAnchorTests(unittest.TestCase):
    def test_rich_content_keeps_unicode_entities_and_formulae_atomic(self):
        html = '<p>甲😀&amp;乙 <strong>重复</strong> 重复</p><span data-tex="x^2"><svg><text>x</text></svg></span><img src="data:image/png;base64,AA=="><pre class="mermaid">graph LR; A--&gt;B;</pre>'
        preview = FeedbackContent(html, annotate=True)
        self.assertEqual(preview.tokens, [5, 2, 3, 1, 1, 1])
        self.assertIn('data-feedback-atom="3"', preview.html)
        self.assertIn('<svg><text>x</text></svg>', preview.html)
        selected = [[0, 1, 2], [2, 1, 3], [3, 0, 1], [5, 0, 1]]
        validate_selection(html, selected, content_hash(html))
        marked = FeedbackContent(html, selection=selected).html
        self.assertIn('<mark class="feedbackHighlight">😀</mark>', marked)
        self.assertIn('<strong>重复</strong> <mark class="feedbackHighlight">重复</mark>', marked)
        self.assertEqual(marked.count('data-feedback-selected="true"'), 2)
        self.assertNotIn('data-feedback-text', marked)
        self.assertIn('&amp;', marked)

    def test_hidden_and_raw_elements_do_not_become_selectable_lines(self):
        html = '<style>p > span { color:red }</style><p hidden>secret</p><p aria-hidden="true">hidden</p><p>Visible<br>Next</p>'
        result = FeedbackContent(html, annotate=True)
        self.assertEqual(result.tokens, [7, 4])
        self.assertIn('<style>p > span { color:red }</style>', result.html)
        for selection, digest in [([[0, 0, 2]], '0' * 64), ([[99, 0, 1]], content_hash(html)), ([[0, 0, 999]], content_hash(html))]:
            with self.assertRaises(DomainError): validate_selection(html, selection, digest)


class FeedbackSelectionTests(unittest.TestCase):
    setUp = helpers.HelpRequestTests.setUp
    identity = helpers.HelpRequestTests.identity
    payload = helpers.HelpRequestTests.payload
    send = helpers.HelpRequestTests.send
    learning_snapshot = replies.HelpReplyTests.learning_snapshot

    def preview(self, payload=None, headers=None, suffix=''):
        payload = payload or self.payload()
        context = {key: value for key, value in payload.items() if key not in {'request_id', 'message', 'categories', 'selection', 'selection_hash', 'selection_version', 'prerequisite'}}
        return self.client.post('/api/help-requests/preview' + suffix, headers=headers or self.headers, json=context)

    def test_preview_and_saved_markings_are_exact_private_snapshots_without_learning_writes(self):
        topic = self.app.state.catalog.topic(self.course['id'])
        original = '<p>第一行😀</p><p>第二行</p><p>第三行</p>'
        topic['introduction']['html'] = original
        before = self.learning_snapshot()
        preview = self.preview().json()
        self.assertIn('data-feedback-text="2"', preview['content_html'])
        with closing(self.store._connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM help_requests').fetchone()[0], 0)
        payload = self.payload(message='', categories=['missing_prerequisite', 'derivation_steps'], prerequisite='  基础代数  ',
            selection=[[2, 0, 3], [0, 1, 4], [0, 0, 2]], selection_hash=preview['content_hash'])
        saved = self.send(payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        topic['introduction']['html'] = '<p>New version</p>'; topic['version'] = 'new'
        self.assertEqual(self.send(payload).json(), saved.json())
        for headers in (self.headers, self.admin_headers):
            record = self.client.get('/api/help-center', headers=headers).json()['items'][0]
            self.assertEqual(record['request']['prerequisite'], '基础代数')
            self.assertEqual(record['request']['categories'], payload['categories'])
            self.assertEqual(record['request']['selection'], [[0, 0, 4], [2, 0, 3]])
            self.assertEqual(record['content_html'], original)
            self.assertIn('<mark class="feedbackHighlight">第三行</mark>', record['highlighted_html'])
            self.assertNotIn('New version', record['highlighted_html'])
        _, other = self.identity(is_student=True)
        self.assertEqual(self.client.get('/api/help-center', headers=other).json()['items'], [])
        self.assertEqual(self.learning_snapshot(), before)

    def test_default_nothing_checked_and_blank_unmarked_submissions_remain_valid(self):
        for fields in ({}, {'kind': 'quality', 'categories': []}, {'categories': ['other', 'motivation']}):
            result = self.send(self.payload(message='', **fields))
            self.assertEqual(result.status_code, 200, result.text)
            record = self.client.get('/api/help-center?report_id=' + result.json()['id'], headers=self.headers).json()['items'][0]
            self.assertEqual(record['request']['categories'], fields.get('categories', []))
            self.assertEqual(record['request']['selection'], [])
            self.assertEqual(record['request']['message'], '')

    def test_forged_or_cross_kind_fields_are_rejected(self):
        preview = self.preview().json()
        for fields in [
            {'categories': ['factual_error']}, {'kind': 'quality', 'categories': ['missing_prerequisite']},
            {'categories': ['other', 'other']}, {'categories': ['invented']},
            {'prerequisite': 'secret'}, {'categories': ['missing_prerequisite'], 'prerequisite': 'bad\x00text'},
            {'selection': [[0, 0, 1]], 'selection_hash': preview['content_hash']},
            {'categories': ['motivation'], 'selection': [[False, 0, 1]], 'selection_hash': preview['content_hash']},
            {'categories': ['motivation'], 'selection': [[99, 0, 1]], 'selection_hash': preview['content_hash']},
            {'categories': ['motivation'], 'selection': [[0, 0, 1000000]], 'selection_hash': preview['content_hash']},
            {'categories': ['motivation'], 'selection': [[0, 2, 1]], 'selection_hash': preview['content_hash']},
            {'content_html': '<p>Client-provided original</p>'},
        ]:
            with self.subTest(fields=fields): self.assertEqual(self.send(self.payload(**fields)).status_code, 422)
        payload = self.payload(categories=['motivation'], selection=[[0, 0, 1]], selection_hash='0' * 64)
        self.assertEqual(self.send(payload).status_code, 409)
        with closing(self.store._connect()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM help_requests').fetchone()[0], 0)

    def test_preview_checks_permission_subject_and_version(self):
        _, ordinary = self.identity()
        self.assertEqual(self.preview(headers=ordinary).status_code, 403)
        self.assertEqual(self.preview(suffix='?subject_id=physics').status_code, 403)
        self.assertEqual(self.preview(self.payload(content_version='old')).status_code, 409)
        self.assertEqual(self.preview(self.payload(content_block_id='question:missing')).status_code, 422)
        self.store.set_permissions(self.student, topic_ids=[])
        self.assertEqual(self.preview().status_code, 403)

    def test_body_limit_accepts_full_message_with_metadata_but_remains_bounded(self):
        topic = self.app.state.catalog.topic(self.course['id'])
        topic['introduction']['html'] = ''.join('<p>甲</p>' for _ in range(256))
        preview = self.preview().json()
        payload = self.payload(message='甲' * 4000, prerequisite='乙' * 1000,
            categories=['missing_prerequisite', 'derivation_steps'], selection=[[i, 0, 1] for i in range(256)], selection_hash=preview['content_hash'])
        self.assertGreater(len(json.dumps(payload, ensure_ascii=False).encode()), 16384)
        result = self.send(payload); self.assertEqual(result.status_code, 200, result.text)
        too_large = self.payload(message='x' * 70000)
        self.assertEqual(self.send(too_large).status_code, 413)


class AllMaterialBlockTests(unittest.TestCase):
    setUp = admins.AdministratorTests.setUp
    call = admins.AdministratorTests.call
    learning_snapshot = admins.AdministratorTests.learning_snapshot

    def test_all_content_includes_shared_question_presentation_without_answer_keys(self):
        topic = self.app.state.catalog.topic('after')
        for index in (1, 2, 3, 4):
            stem, spec = interactions()[index]
            topic['modules'][0]['practices'][-1].update(html='<p>' + stem + '</p>', interaction=spec)
            result = self.call('GET', 'materials/content?topic_id=after')['blocks'][-2]
            self.assertEqual(result['interaction']['type'], spec['type'])
            if spec['type'] == 'matching':
                self.assertEqual(len(result['interaction']['left']), 3)
                self.assertEqual(result['interaction']['row_count'], 2)
                self.assertNotIn('pairs', result['interaction'])
            elif spec['type'] == 'true_false':
                self.assertEqual(len(result['interaction']['statements']), 2)
                self.assertNotIn('correct', result['interaction']['statements'][0])

    def test_admin_browses_every_spare_practice_explanation_and_atomic_variant_without_writes(self):
        topic = self.call('GET', 'materials/content?topic_id=after&subject_id=math&block=explanation:module-1-practice-15')
        self.assertEqual(len(topic['blocks']), 33)
        self.assertEqual(topic['blocks'][-1]['id'], 'explanation:module-1-practice-15')
        self.assertIn('EXPLANATION::', topic['blocks'][-1]['html'])
        atomic = self.call('GET', 'materials/content?topic_id=atoms&subject_id=english')
        self.assertEqual(len(atomic['blocks']), 17)
        self.assertTrue({'chunk:colors', 'question:ab-bundle', 'explanation:ab-bundle'} <= {b['id'] for b in atomic['blocks']})
        self.call('GET', 'materials/content?topic_id=after&subject_id=english', status=403)
        current = self.call('GET', 'materials/content?topic_id=after&block=introduction&version=old')
        self.assertTrue(current['version_changed'])
        guest = self.call('POST', 'access/guest', {})
        self.call('GET', 'materials/content?topic_id=after', status=403, headers={**self.headers, 'X-Learning-Identity': guest['identity_token']})
        self.assertEqual(self.learning_snapshot(), self.before)

    def test_v2_all_introductions_and_practices_follow_authored_order(self):
        topic = self.app.state.catalog.topic('after')
        topic.update(topic_format_version=2, introductions=[{'id': 'first', 'title': 'One', 'html': '<p>One</p>'}, {'id': 'second', 'title': 'Two', 'html': '<p>Two</p>'}],
            content_order=[{'kind': 'introduction', 'id': 'first'}, {'kind': 'example', 'id': 'module-1'}, {'kind': 'introduction', 'id': 'second'}])
        result = self.call('GET', 'materials/content?topic_id=after')['blocks']
        self.assertEqual(len(result), 34)
        self.assertEqual(result[0]['id'], 'introduction:first')
        self.assertEqual(result[-1]['id'], 'introduction:second')
