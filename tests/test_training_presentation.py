import re
import unittest

from graspmemoedu.training_presentation import prose_layout, html_layout, present_book_question


class TrainingPresentationTests(unittest.TestCase):
    def test_paragraphs_preserve_tex_images_and_equation_references(self):
        text = '题目(1)求 $f(1)$；(2)由(1)式得到结论。![](part(2).png)'
        result = prose_layout(text)
        self.assertIn('题目\n\n(1)求 $f(1)$；\n\n(2)', result)
        self.assertIn('由(1)式', result)
        self.assertEqual(re.sub(r'\s', '', result), re.sub(r'\s', '', text))
        self.assertEqual(prose_layout(result), result)

    def test_html_layout_preserves_compiled_math_and_existing_paragraph_breaks(self):
        svg = '<svg><defs><path id="a" d="(1)"></path></defs><text>(2)</text></svg>'
        html = '<div><p>题目(1)计算' + svg + '；(2)证明。</p><p>(3)比较。</p></div>'
        result = html_layout(html)
        self.assertIn(svg, result); self.assertEqual(result.count('bookSubnumberBreak'), 4)
        self.assertIn('<p>(3)', result); self.assertEqual(html_layout(result), result)
        self.assertEqual(re.sub(r'\s', '', re.sub(r'<[^>]*>', '', result)), re.sub(r'\s', '', re.sub(r'<[^>]*>', '', html)))

    def test_single_source_heading_list_loses_indent_but_keeps_its_number(self):
        result = html_layout('<ol start="4"><li>解析(1)求值。</li></ol><p>(2)证明。</p>')
        self.assertIn('4. 解析', result); self.assertNotIn('<li>', result)
        self.assertIn('(1) 求值', result); self.assertIn('(2) 证明', result)

    def test_field_removes_only_choice_labels_and_keeps_reference_answer(self):
        q = {'question': '1.（多选题）判断。', 'html': '<p>1.（多选题）判断。</p>',
             'reference_answer': '(1)原答案(2)原答案', 'explanation_html': '<p>(1)原答案(2)原答案</p>',
             'interaction': {'type': 'choice', 'multiple': True}}
        result = present_book_question(q)
        self.assertNotIn('多选题', result['question']); self.assertNotIn('多选题', result['html'])
        self.assertEqual(result['reference_answer'], q['reference_answer'])
        self.assertIn('多选题', q['html'])


if __name__ == '__main__': unittest.main()
