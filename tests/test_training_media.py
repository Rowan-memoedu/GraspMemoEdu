from copy import deepcopy
import hashlib
import unittest

from graspmemoedu.training_media import field_layout, present_media, validate_media
from graspmemoedu.training_presentation import present_book_question


class TrainingMediaTests(unittest.TestCase):
    image = '<img src="data:image/png;base64,YWJj" width="10">'
    second = '<img src="data:image/png;base64,ZGVm" width="10">'

    def question(self):
        return {'id': 'q', 'title': '第12题', 'question': '正文\n![](a.png)\n后续(1)求值。',
                'html': '<p>正文</p><p>' + self.image + '</p><p>后续(1)求值。</p>',
                'reference_answer': '原始答案', 'explanation_html': '<p>解析</p>' + self.second + '<p>结论。</p>'}

    def test_images_follow_all_text_and_keep_source_bytes_and_ownership(self):
        question = self.question(); original = deepcopy(question)
        result = present_book_question(question)
        self.assertGreater(result['html'].index('<img'), result['html'].index('求值'))
        self.assertGreater(result['explanation_html'].index('<img'), result['explanation_html'].index('结论'))
        self.assertIn('第12题 · 图1', result['html'])
        self.assertIn('data-question-id="q"', result['html'])
        self.assertIn(self.image, result['html']); self.assertIn(self.second, result['explanation_html'])
        self.assertEqual(result['reference_answer'], original['reference_answer'])
        self.assertEqual(question, original)
        self.assertEqual(present_book_question(result), result)

    def test_parallel_printed_labels_move_with_their_images(self):
        html = '<p>看下图。</p><p>' + self.image + '</p><p>' + self.second + '</p><p>① ②</p><p>比较。</p>'
        result, text = field_layout(html, owner='第3题', question_id='q',
            markdown='看下图。\n![](a.png)\n![](b.png)\n\n①\n\n②\n\n比较。')
        self.assertNotIn('<p>①', result)
        self.assertIn('第3题 · ①', result); self.assertIn('第3题 · ②', result)
        self.assertGreater(text.index('![](a.png)'), text.index('比较'))
        self.assertEqual(text.count('①'), 1)
        self.assertLess(result.index('YWJj'), result.index('ZGVm'))

    def test_hash_exclusion_drops_only_selected_noise_and_keeps_math(self):
        math = '<svg><defs><path id="glyph"></path></defs><use href="#glyph"></use></svg>'
        html, _ = field_layout('<p>题目' + math + self.image + self.second + '</p>',
            owner='第1题', question_id='q', excluded={hashlib.sha256(b'abc').hexdigest(): 'qr_code'})
        self.assertNotIn('YWJj', html); self.assertIn(self.second, html); self.assertIn(math, html)

    def test_legacy_split_figure_captions_ignore_excluded_qr_images(self):
        noise = '<img src="data:image/png;base64,Z2hp">'
        html = '<p>题目。</p>' + noise + self.image + '<p>图</p><p>(1)</p>' + self.second + '<p>图</p><p>(2)</p>'
        result, _ = field_layout(html, owner='第8题', question_id='q',
            excluded={hashlib.sha256(b'ghi').hexdigest(): 'qr_code'})
        self.assertIn('第8题 · 图(1)', result); self.assertIn('第8题 · 图(2)', result)
        self.assertNotIn('<p>图</p>', result); self.assertNotIn('<p>(1)</p>', result)

    def test_split_circle_caption_paragraphs_are_associated_in_printed_order(self):
        html = self.image + self.second + '<p>①</p>\n<p>②</p>'
        result, _ = field_layout(html, owner='第8题', question_id='q')
        self.assertIn('第8题 · ①', result); self.assertIn('第8题 · ②', result)
        self.assertNotIn('<p>①</p>', result)

    def test_image_choice_option_identity_answer_and_order_do_not_change(self):
        q = self.question(); q['interaction'] = {'type': 'choice', 'shuffle': False, 'correct_id': 'b',
            'options': [{'id': 'a', 'text': '![](a.png)描述', 'html': self.image + '<p>描述</p>'},
                        {'id': 'b', 'text': '乙', 'html': '<p>乙</p>'}]}
        result = present_media(q)
        self.assertEqual(result['interaction']['correct_id'], 'b')
        self.assertEqual([o['id'] for o in result['interaction']['options']], ['a', 'b'])
        self.assertIn('选项A', result['interaction']['options'][0]['html'])
        self.assertGreater(result['interaction']['options'][0]['html'].index('<img'),
                           result['interaction']['options'][0]['html'].index('描述'))

    def test_media_policy_rejects_unbound_image_hash_and_unknown_operations(self):
        bank = {'groups': [{'questions': [self.question()]}]}
        policy = {'watermark_policy': 'preserve_embedded',
                  'excluded_images': {hashlib.sha256(b'abc').hexdigest(): 'qr_code'}}
        validate_media(policy, bank, {})
        for exclusions in ({'0' * 64: 'qr_code'}, {hashlib.sha256(b'abc').hexdigest(): 'rewrite'}):
            with self.assertRaises(ValueError): validate_media({**policy, 'excluded_images': exclusions}, bank, {})
