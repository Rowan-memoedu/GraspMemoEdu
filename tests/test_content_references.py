from copy import deepcopy
import unittest

from graspmemoedu.content_references import ContentReferenceIndex
from tests.test_ordered_learning import ordered_topic


class ReferenceTests(unittest.TestCase):
    def fixture(self):
        a, b = ordered_topic(), ordered_topic(True)
        a.update(id='a', title='A', source_rem_id='RemA')
        b.update(id='b', title='B', source_rem_id='RemB')
        a['content_nodes'] = [
            {'id':'statement', 'section_id':'intro-a', 'parent_id':None, 'source_rem_id':'RemStatement',
             'owner':{'kind':'introduction', 'id':'intro-a', 'field':'html'}},
            {'id':'answer', 'section_id':'module-1', 'parent_id':None, 'source_rem_id':'RemAnswer',
             'owner':{'kind':'example', 'id':'module-1-example', 'field':'explanation_html'}}]
        a['references'] = [{'source_node_id':'statement', 'target_rem_id':'RemB', 'label':'B'},
                           {'source_node_id':'answer', 'target_rem_id':'RemB', 'label':'hidden answer'}]
        access = {'topics':[{'id':'a'},{'id':'b'}], 'features':['learn','review_history']}
        state = {'a': {'_course_version':a['version'], 'active_step_id':'sa', 'steps':[
            {'id':'sa', 'kind':'introduction', 'introduction_id':'intro-a', 'phase':'completed'},
            {'id':'qa', 'kind':'example', 'question_id':'module-1-example', 'phase':'choice'}]}}
        return a, b, access, state

    def test_pending_target_resolves_on_rebuild_and_reimport_removes_stale_edges(self):
        a, b, access, state = self.fixture()
        first = ContentReferenceIndex({'a':a}).view(access, 'a', state, lambda _:'math')
        self.assertEqual(first['outgoing'][0]['status'], 'unresolved')
        index = ContentReferenceIndex({'a':a,'b':b})
        self.assertEqual(index.view(access, 'a', state, lambda _:'math')['outgoing'][0]['target']['topic_id'], 'b')
        self.assertEqual(len(index.view(access, 'b', state, lambda _:'math')['backlinks']), 1)
        a['references'] = []
        self.assertEqual(ContentReferenceIndex({'a':a,'b':b}).view(access,'b',state,lambda _:'math')['backlinks'], [])

    def test_locked_answer_and_inaccessible_topics_do_not_leak_through_backlinks(self):
        a,b,access,state = self.fixture()
        index = ContentReferenceIndex({'a':a,'b':b})
        result = index.view(access,'b',state,lambda _:'math')
        self.assertEqual([r['node_id'] for r in result['backlinks']], ['statement'])
        state['a']['steps'][1]['phase'] = 'reading'
        self.assertEqual(len(index.view(access,'b',state,lambda _:'math')['backlinks']), 2)
        access['topics'] = [{'id':'b'}]
        self.assertEqual(index.view(access,'b',state,lambda _:'math')['backlinks'], [])

    def test_history_revocation_and_ambiguous_rem_identity_are_respected(self):
        a,b,access,state = self.fixture()
        access['features'] = ['learn']
        state['a']['active_step_id'] = 'qa'
        index = ContentReferenceIndex({'a':a,'b':b})
        self.assertEqual(index.view(access,'b',state,lambda _:'math')['backlinks'], [])
        access['features'].append('review_history')
        c = deepcopy(b)
        c.update(id='c',title='C')
        access['topics'].append({'id':'c'})
        index = ContentReferenceIndex({'a':a,'b':b,'c':c})
        self.assertEqual(index.view(access,'a',state,lambda _:'math')['outgoing'][0]['status'], 'unresolved')

    def test_guidance_reference_remains_visible_during_chunk_reading(self):
        a, b, access, state = self.fixture()
        state['a'].update(display='atomic', section_index=0, atomic={'active': None, 'phase': 'chunk'})
        index = ContentReferenceIndex({'a': a, 'b': b})
        self.assertEqual(len(index.view(access, 'b', state, lambda _: 'math')['backlinks']), 1)
        state['a']['atomic'].update(active={'card_id': 'a'}, phase='answer')
        self.assertEqual(index.view(access, 'b', state, lambda _: 'math')['backlinks'], [])
