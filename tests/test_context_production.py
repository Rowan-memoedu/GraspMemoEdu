from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hashlib
from argparse import Namespace

from graspmemoedu import context_production as cp
from scripts import produce_context as runner
from scripts.prepare_structured_atomic import copy_audio_asset
from graspmemoedu.service import DomainError


def fixture():
    return {'schema':1, 'subject_id':'english',
            'points':[{'id':'p-one','target':'Use a preposition to ask for an object.'}],
            'tasks':[{'id':'one','kind':'cards','topic_id':'topic-one','point_ids':['p-one'],
                      'format':'cloze','count':1,'requirements':'One context gap.'}]}


def card():
    return {'cards':[{'point_ids':['p-one'], 'question':'Ask {{blank:a}} help.',
                      'reference_answer':'for', 'explanation':'ask for requests help.',
                      'interaction':{'version':1,'type':'fill_blank','grading':'exact',
                                     'blanks':[{'id':'a','answers':['for']}]},
                      'assessment':{'version':1,'item_points':{'a':['p-one']}}}]}


def review():
    return {'approved':True,'checks':[{'index':1,'approved':True,'issues':[],
                                     'rationale':'The requested object is help, so for is correct.'}]}


class ContextProductionTests(unittest.TestCase):
    def test_source_and_item_bindings_cannot_be_invented(self):
        source = cp.task_source(fixture(), fixture()['tasks'][0])
        cp.validate_candidate(card(), source)
        bad = card(); bad['cards'][0]['point_ids'] = ['another-point']
        with self.assertRaises(ValueError): cp.validate_candidate(bad, source)
        bad = card(); bad['cards'][0]['assessment']['item_points'] = {'missing':['p-one']}
        with self.assertRaises(ValueError): cp.validate_candidate(bad, source)
        bad = card(); bad['cards'][0]['interaction']['grading'] = 'semantic'
        with self.assertRaises(ValueError): cp.validate_candidate(bad, source)

    def test_article_evidence_is_exact_and_suggestions_are_not_new_ids(self):
        doc = fixture()
        doc['tasks'][0].update(kind='article', article={'paragraphs':[{'id':'p','text':'They ask for help.'}]})
        source = cp.task_source(doc, doc['tasks'][0])
        value = {'matches':[{'point_id':'p-one','paragraph_id':'p','quote':'ask for help','reason':'Requests an object.'}],
                 'suggestions':[{'target':'Plural pronoun they','paragraph_id':'p','quote':'They','reason':'A possible additional target.'}]}
        cp.validate_candidate(value, source)
        located = cp.evidence_locations(value, source)
        self.assertEqual((located['matches'][0]['start'],located['matches'][0]['end']), (5,17))
        self.assertNotIn('point_id', located['suggestions'][0])
        value['matches'][0]['quote'] = 'ask about help'
        with self.assertRaises(ValueError): cp.validate_candidate(value, source)

    def test_model_html_and_false_review_are_rejected(self):
        source = cp.task_source(fixture(), fixture()['tasks'][0])
        value = card(); value['cards'][0]['question'] = '<img src=x onerror=alert(1)>'
        with self.assertRaises(ValueError): cp.validate_candidate(value, source)
        value = review(); value['checks'][0]['issues'] = ['wrong key']
        with self.assertRaises(ValueError): cp.validate_review(value, card(), 'cards')
        self.assertIn('json', cp.GENERATION.lower())
        self.assertIn('json', cp.REVIEW.lower())

    def test_composite_source_is_escaped_and_validated(self):
        value = card()['cards'][0]
        value['interaction'] = {'version':2,'type':'composite','grading':'exact',
            'controls':[{'id':'a','type':'text','answers':['for']}],
            'sections':[{'id':'s','text':'Read A & B.','control_ids':['a'],'complete':True}]}
        source = cp.task_source(fixture(), fixture()['tasks'][0])
        cp.validate_candidate({'cards':[value]},source)
        normalized = cp.normalize_card(value)
        self.assertEqual(normalized['interaction']['sections'][0]['html'],'<p>Read A &amp; B.</p>')
        self.assertIn('text',value['interaction']['sections'][0])

    def test_reuse_requires_same_targets_and_untampered_approved_record(self):
        source = cp.task_source(fixture(), fixture()['tasks'][0])
        record = cp.accepted_record(source,card(),review(),{'run':'test'})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); library = root / 'library'
            cp.save(library / (record['record_sha256']+'.json'),record)
            renamed = deepcopy(source); renamed['task']['id'] = 'another-run-id'
            self.assertIsNotNone(cp.find_material(library,renamed))
            altered = deepcopy(source); altered['task']['requirements'] = 'Two context gaps.'
            self.assertIsNone(cp.find_material(library,altered))
            with patch.object(runner, 'CompatibleClient', side_effect=AssertionError('Reuse must make no API call')):
                result = runner.run_task(source,root/'run',library,None,1)
            self.assertEqual(result['origin'],'library')
            changed = deepcopy(record); changed['candidate']['cards'][0]['reference_answer'] = 'about'
            with self.assertRaises(ValueError): cp.validate_record(changed)

    def test_review_only_fix_reuses_unchanged_generation_response(self):
        doc = fixture(); source = cp.task_source(doc,doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cp.save(root/'run.json',{'generation_prompt_sha256':cp.digest(cp.GENERATION)})
            cp.save(root/'input-snapshot.json',doc)
            cp.save(root/'one/attempt-01/generation.response.json',card())
            candidate, path = runner.retained_candidate(source,[root])
            self.assertEqual(candidate,card())
            self.assertTrue(path.endswith('generation.response.json'))
            changed = deepcopy(source); changed['points'][0]['target'] = 'A different objective.'
            self.assertIsNone(runner.retained_candidate(changed,[root]))

    def test_media_packaging_reuses_runtime_validation_and_keeps_content_hash(self):
        data = b'ID3' + b'\0' * 32
        asset = 'audio/' + hashlib.sha256(data).hexdigest() + '.mp3'
        question = {'audio':{'version':1,'asset':asset,'mime_type':'audio/mpeg','mode':'listen'}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'source/input.json'; source.parent.mkdir()
            (source.parent/'audio').mkdir(); (source.parent/asset).write_bytes(data)
            path = copy_audio_asset(source,root/'package/topics',question)
            self.assertEqual(path.read_bytes(),data)
            self.assertEqual(path.relative_to(root/'package').as_posix(),'topics/'+asset)
            (source.parent/asset).write_bytes(data+b'changed')
            with self.assertRaises(DomainError): copy_audio_asset(source,root/'package/topics',question)

    def test_prepare_cannot_substitute_a_different_source_card_after_audit(self):
        doc = fixture(); doc['tasks'][0]['provided_cards'] = card()['cards']
        source = cp.task_source(doc,doc['tasks'][0])
        record = cp.accepted_record(source,card(),review(),{'run':'test'})
        changed = deepcopy(card()['cards'][0]); changed['question'] = 'A changed question.'
        structured = {'schema':2,'topics':[{'id':'topic-one','sections':[{
            'memory_points':[{'id':'p-one'}],'flashcards':[{'id':'seed',**changed}]}]}]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); library=root/'library'; run=root/'run'
            cp.save(library/(record['record_sha256']+'.json'),record)
            cp.save(root/'source.json',structured); cp.save(root/'tasks.json',doc)
            cp.save(run/'one/result.json',{'status':'accepted','source':source,
                'source_sha256':cp.digest(source),'candidate':card(),'review':review(),
                'record_sha256':record['record_sha256']})
            args=Namespace(source=root/'source.json',tasks=root/'tasks.json',runs=[run],library=library,
                           output=root/'prepared',compile=False)
            with self.assertRaisesRegex(ValueError,'differs from its independent audit'):
                runner.assemble(args)
            self.assertFalse(args.output.exists())


    def test_reading_bridge_preserves_context_definitions_and_deduplicates(self):
        row={'topic_id':'topic-one','material_id':'reader','content_version':'version-one',
             'quote':'durable','paragraph_id':'para','context':'These durable tools last for years.',
             'note':'Which sense fits?','definitions':[{'definition':'long-lasting'},{'definition':'consumer goods'}],
             'point_ids':[],'memory_status':'candidate','stale':False}
        exported={'schema':1,'kind':'reading-context-export','records':[row,{**row,'browser_record_id':999}]}
        original=deepcopy(exported)
        value=cp.from_reading_export(exported,fixture())
        self.assertEqual(exported,original)
        self.assertEqual(len(value['tasks']),1)
        task=value['tasks'][0]
        self.assertEqual(task['article']['paragraphs'][0]['text'],row['context'])
        self.assertEqual(task['reading_source']['definitions'],row['definitions'])
        self.assertEqual(task['reading_source']['point_ids'],[])
        self.assertEqual(task['point_ids'],['p-one'])
        row['stale']=True
        stale=cp.from_reading_export({'schema':1,'kind':'reading-context-export','records':[row]},fixture())
        self.assertEqual(stale['tasks'],[])
        self.assertEqual(stale['deferred_records'][0]['reason'],'source_version_changed')
        row['context']='A different paragraph.'
        with self.assertRaisesRegex(ValueError,'original context'):
            cp.from_reading_export({'schema':1,'kind':'reading-context-export','records':[row]},fixture())

    def test_reading_bridge_preserves_browser_unicode_quote_anchor_and_unselected_senses(self):
        row={'topic_id':'topic-one','material_id':'reader','content_version':'version-one',
             'quote':{'exact':'durable','start':8,'end':15,'prefix':'🛠 These ','suffix':' tools.'},
             'paragraph_id':'para','context':'🛠 These durable tools.',
             'note':'Which sense fits?','definitions':[{'meaning':'long-lasting'},{'meaning':'consumer goods'}],
             'point_ids':[],'memory_status':'candidate','stale':False,
             'selected_sense_id':None,'contextual_sense_selected':False,'account_id':'private',
             'dictionary':{'senses':[{'sense_id':'local-durable','definition':'long-lasting'}],
                           'source':{'name':'Fixture dictionary','version':'1','license':'CC0'}}}
        exported={'schema':1,'kind':'reading-context-export','records':[row]}
        task=cp.from_reading_export(exported,fixture())['tasks'][0]
        self.assertEqual(task['article']['title'],'durable')
        self.assertEqual(task['reading_source']['quote'],row['quote'])
        self.assertIsNone(task['reading_source']['selected_sense_id'])
        self.assertFalse(task['reading_source']['contextual_sense_selected'])
        self.assertEqual(task['reading_source']['dictionary'],row['dictionary'])
        self.assertNotIn('account_id',task['reading_source'])
        known=deepcopy(row); known['point_ids']=['p-one']; known['memory_status']='pending'
        filtered=cp.from_reading_export({**exported,'records':[row,known]},fixture(),True)
        self.assertEqual(len(filtered['tasks']),1)
        self.assertEqual(filtered['deferred_records'][0]['reason'],'outside_unknown_candidate_scope')
        candidate={'matches':[],'suggestions':[{'target':'durable tools','paragraph_id':'para',
                    'quote':'durable','reason':'In context it means long-lasting.'}]}
        cp.validate_candidate(candidate,{'task':task})
        row['quote']['start']=9
        with self.assertRaisesRegex(ValueError,'original context'):
            cp.from_reading_export(exported,fixture())


if __name__ == '__main__': unittest.main()
