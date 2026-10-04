from copy import deepcopy
import unittest

from graspmemoedu.idea_presentation import present_question, split_ideas
from graspmemoedu.content_references import ContentReferenceIndex
from tests.test_ordered_learning import ordered_topic, payload, active
from graspmemoedu.service import create_learning_service
from tests.fixtures import temporary_test_directory

IDEA = '<div class="outlineNode"><div id="node-idea"><p><strong>Idea</strong>：</p>\n<ul><li class="outlineNode"><div id="node-ref"><p><a data-target-rem-id="Target">方法引用</a></p></div></li></ul></div></div>'
STEM = '<div class="outlineNode"><div id="node-stem"><p>原题条件 <em>x</em> &gt; 0</p></div></div>'


class IdeaPresentationTests(unittest.TestCase):
    def topic(self):
        topic = ordered_topic()
        q = topic['modules'][0]['example']
        q['html'] = '<div id="node-module">' + IDEA + STEM + '</div>'
        topic['content_nodes'] = [{'id':nid, 'section_id':'module-1', 'parent_id':None,
            'owner':{'kind':'example','id':q['id'],'field':'html'}} for nid in ('idea','ref','stem')]
        topic['references'] = [{'source_node_id':'ref','target_rem_id':'Target','label':'方法引用'}]
        return topic

    def test_field_moves_and_other_markup_stays_byte_for_byte(self):
        original = {'html':'<div>'+IDEA+STEM+'</div>', 'explanation_html':'<p>原解析</p>', 'id':'question'}
        before = deepcopy(original)
        shown = present_question(original)
        self.assertEqual(original, before)
        self.assertEqual(shown['html'], '<div>'+STEM+'</div>')
        self.assertEqual(shown['explanation_html'], IDEA+'<p>原解析</p>')
        self.assertEqual(split_ideas(original['html'])[2], {'idea','ref'})
        self.assertEqual(present_question(shown), shown)

    def test_nested_list_and_multiple_fields_keep_order(self):
        child = '<li class="outlineNode"><div id="node-inner"><p><strong>idea</strong>：提示</p></div></li>'
        front, back, ids = split_ideas('<ul>'+child+'</ul>'+STEM+IDEA)
        self.assertEqual(front, '<ul></ul>'+STEM)
        self.assertEqual(back, '<ul>'+child+'</ul>'+IDEA)
        self.assertEqual(ids, {'inner','idea','ref'})

    def test_prose_and_code_are_not_idea_fields(self):
        for html in ('<div class="outlineNode"><p>正文中的 <strong>idea</strong>：一词</p></div>',
                     '<div class="outlineNode"><p><code>idea：</code></p></div>'):
            self.assertEqual(split_ideas(html), (html,'',frozenset()))

    def test_trick_and_pure_reference_are_answer_fields_but_inline_reference_is_not(self):
        trick = IDEA.replace('Idea', '巧法')
        pure = '<div class="outlineNode"><div id="node-pure"><p><a data-target-rem-id="Target">原方法</a></p></div></div>'
        for field in (trick, pure):
            shown = present_question({'html':field+STEM, 'explanation_html':'原答案'})
            self.assertEqual(shown['html'], STEM)
            self.assertEqual(shown['explanation_html'], field+'原答案')
        for body in ('参见 <a data-target-rem-id="Target">原方法</a>',
                     '<a data-target-rem-id="Target">原方法</a> 是条件。',
                     '<a data-target-rem-id="A">一</a> 与 <a data-target-rem-id="B">二</a>'):
            html = '<div class="outlineNode"><p>'+body+'</p></div>'
            self.assertEqual(split_ideas(html), (html,'',frozenset()))

    def test_frozen_bank_replacement_keeps_source_idea(self):
        original = {'html':IDEA+STEM,'explanation_html':'答案'}
        variant = {**original,'html':'<p>已有题库题面</p>'}
        self.assertEqual(present_question(variant, original), {**variant,'explanation_html':IDEA+'答案'})

    def test_unrevealed_state_and_references_hide_idea(self):
        topic = self.topic()
        before = deepcopy(topic)
        with temporary_test_directory() as folder:
            service = create_learning_service(topic, folder)
            state = service.state()
            self.assertNotIn('方法引用', active(state)['html'])
            self.assertIsNone(active(state)['explanation_html'])
            index = ContentReferenceIndex({topic['id']:topic})
            access = {'topics':[{'id':topic['id']}], 'features':['learn','review_history']}
            view = lambda: index.view(access,topic['id'],{topic['id']:service.state()},lambda _:'math')
            self.assertEqual(view()['outgoing'], [])
            service.choose(payload(service,choice='unmastered'))
            service.advance(payload(service))
            service.advance(payload(service))
            self.assertNotIn('方法引用', active(service.state())['html'])
            self.assertIn('方法引用', active(service.state())['explanation_html'])
            self.assertEqual(view()['outgoing'][0]['source_node_id'], 'ref')
            self.assertEqual(next(n for n in view()['locations'] if n['node_id']=='ref')['field'], 'explanation_html')
        self.assertEqual(topic, before)
