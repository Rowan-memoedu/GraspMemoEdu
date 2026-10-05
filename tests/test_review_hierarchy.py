from types import SimpleNamespace
import unittest

from graspmemoedu.review_hierarchy import source_trees


class ReviewHierarchyTests(unittest.TestCase):
    def test_preserves_course_internal_tree_and_adds_exactly_one_course_root(self):
        catalog = SimpleNamespace(courses={'course': {'id': 'course', 'title': '课程名称',
            'topics': [{'id': 'topic'}], 'units': [
                {'id': 'course', 'title': '重复的存储根', 'topic_ids': []},
                {'id': 'chapter', 'title': '第一章', 'parent_id': 'course', 'topic_ids': []},
                {'id': 'section', 'title': '第一节', 'parent_id': 'chapter', 'topic_ids': ['topic']}]}},
            topics={'topic': {'title': '原子主题'}})
        entry = {'id': 'card', 'source': {'kind': 'atomic', 'container': 'topic'}}
        tree = source_trees(catalog, SimpleNamespace(banks={}), [entry])[0]
        nodes = tree['pending_hierarchy']
        self.assertEqual([n['title'] for n in nodes], ['课程名称', '第一章', '第一节', '原子主题'])
        self.assertEqual([n['parent_id'] for n in nodes], [None, 'course:course', 'course:course:unit:chapter', 'course:course:unit:section'])
        self.assertEqual(nodes[-1]['topic_ids'], ['card'])

    def test_multiple_banks_keep_original_group_order_and_isolate_colliding_ids(self):
        groups = [{'id': 'chapter', 'title': '章'}, {'id': 'section', 'title': '节', 'parent_id': 'chapter'},
                  {'id': 'hidden', 'title': '没有复习内容的分支'}]
        banks = SimpleNamespace(banks={bid: {'id': bid, 'title': bid, 'groups': groups} for bid in ['bank-a', 'bank-b']})
        entries = [{'id': bid + '-card', 'source': {'kind': 'training', 'container': bid, 'group_id': 'section'}} for bid in banks.banks]
        trees = source_trees(SimpleNamespace(courses={}), banks, entries)
        self.assertEqual(len(trees), 2)
        for tree in trees:
            self.assertEqual([g['title'] for g in tree['pending_hierarchy']][1:], ['章', '节'])
        self.assertFalse({g['id'] for g in trees[0]['pending_hierarchy']} & {g['id'] for g in trees[1]['pending_hierarchy']})
