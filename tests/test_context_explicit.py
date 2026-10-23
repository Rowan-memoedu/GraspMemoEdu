from argparse import Namespace
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from graspmemoedu import context_explicit as ce
from graspmemoedu import context_production as cp
from graspmemoedu.structured_atomic import validate_projection
from scripts import produce_explicit_context as cli
from scripts import produce_context as forward


def fixture():
    known = {'subject_id':'physics','points':[{'id':'known-ruler','target':'Measure length with a ruler.'}]}
    raw = {'schema':1,'articles':[{'id':'insulation','title':'Two jars',
        'paragraphs':[{'id':'p1','text':'Two jars contained equally hot water. A thick wool cover slowed heat loss from one jar. The uncovered jar cooled faster.'}]}]}
    document = ce.prepare_input(raw,known)
    source = ce.effective_source(document,document['tasks'][0],[])
    analysis = {'matches':[],'suggestions':[{'target':'Insulation slows heat transfer.',
        'paragraph_id':'p1','quote':'A thick wool cover slowed heat loss from one jar.',
        'reason':'The covered and uncovered jars provide a direct comparison.'}]}
    plan = ce.blueprint(source,analysis)
    point = plan['allocated_targets'][0]['point_id']
    candidate = {'title':'Reducing heat loss','decisions':[{
        'point_id':point,'status':'accepted','target':'Insulation slows heat transfer.',
        'reason':'The wool cover reduced heat loss compared with the uncovered jar.',
        'selected_sense_id':None,'existing_point_ids':[],
        'evidence':[{'paragraph_id':'p1','quote':'A thick wool cover slowed heat loss from one jar.'}]}],
        'sections':[{'point_id':point,'title':'Insulation','introduction':[
            'An insulating layer slows heat transfer; it does not supply heat.',
            'The wool-covered jar loses heat more slowly than the uncovered jar in the source comparison.'],
            'chunk':{'title':'Slower heat transfer','content':'Insulation reduces the rate of heat transfer.','mnemonic':[]},
            'cards':[{'point_ids':[point],'question':'A camper needs to keep soup warm during a walk. She wraps the sealed soup bottle in a blanket. Which effect helps her achieve this?',
                'reference_answer':'A. The blanket slows heat transfer from the soup.',
                'explanation':'The insulating blanket slows heat loss from the warm soup to the cooler air; it does not supply heat.',
                'interaction':{'version':1,'type':'choice','options':[
                    {'id':'a','text':'The blanket slows heat transfer from the soup.'},
                    {'id':'b','text':'The blanket continuously supplies heat to the soup.'}], 'correct_id':'a'}}]}]}
    analysis_review = {'approved':True,'checks':[{'index':1,'approved':True,'issues':[],
                                               'rationale':'The comparison supports the stated rate change.'}]}
    author_review = {'approved':True,'checks':[{'id':ident,'approved':True,'issues':[],
        'rationale':('The application asks how to keep food warm during a walk, rather than repeating the source jar experiment.'
                     if ident.startswith('card:') else 'The source supports the stated teaching claim.')}
        for ident in ce.review_ids(candidate)]}
    return document,source,analysis,plan,candidate,analysis_review,author_review


def learner_review():
    return {'approved':True,'checks':[{'index':1,'answer':'a','approved':True,'issues':[],
        'rationale':'The complete visible food-storage situation supports slower heat transfer.'}]}


class FakeExec:
    """An isolated transport fixture, never used as actual generation evidence."""
    def __init__(self, responses):
        self.responses=list(responses); self.calls=[]

    def __call__(self,path):
        owner=self
        class Client:
            model='fixture-only'; reasoning_effort='fixture-only'
            def complete_limited(self,messages):
                owner.calls.append({'path':str(path),'messages':deepcopy(messages)})
                value=owner.responses.pop(0)
                if isinstance(value,Exception): raise value
                directory=Path(path)/('fixture-call-'+str(len(owner.calls)))
                directory.mkdir(parents=True)
                cp.save(directory/'messages.json',messages); cp.save(directory/'result.json',value)
                raw=json.dumps({'result_json':json.dumps(value,ensure_ascii=False)},ensure_ascii=False)
                (directory/'final.txt').write_text(raw,encoding='utf-8')
                (directory/'events.jsonl').write_text(json.dumps({'fixture':True,'text':raw}),encoding='utf-8')
                (directory/'prompt.txt').write_text('UNIT FIXTURE ONLY\n'+json.dumps(messages,ensure_ascii=False),encoding='utf-8')
                self.last_receipt={'backend':'codex-exec','exit_code':0,
                    'call_id':directory.name,'call_dir':str(directory),'model':self.model,
                    'status':'success','event_check':{'passed':True},'model_execution_count':1}
                for name,field in (('result.json','result_sha256'),('events.jsonl','events_sha256'),
                                   ('final.txt','final_sha256'),('prompt.txt','input_sha256')):
                    self.last_receipt[field]=hashlib.sha256((directory/name).read_bytes()).hexdigest()
                cp.save(directory/'receipt.json',self.last_receipt)
                return deepcopy(value)
        return Client()


class ContextExplicitTests(unittest.TestCase):
    def _legacy_standalone_record(self, root, generation_prompt=None):
        """The immediately preceding quality policy, with immutable old prompts."""
        document,source,analysis,plan,candidate,ar,review=fixture()
        fake=FakeExec([analysis,ar,candidate,learner_review(),review])
        run=root/'legacy'/source['task']['id']; library=root/'library'
        analyzed=ce._analysis_record(source,run,library,fake,1)
        stage=ce._stage(run/'author',generation_prompt or ce.LEGACY_AUTHOR,ce.LEGACY_AUTHOR_REVIEW,plan,
            lambda c:ce.validate_author(c,plan,cp.QUALITY_POLICY),ce.validate_author_review,
            lambda c:{'plan':plan,'candidate':c,'required_check_ids':ce.review_ids(c)},fake,1,
            learner_cards=ce.author_cards)
        record=ce.accepted_record(source,analyzed,candidate,review,stage['evidence'])
        cp.save(run/'result.json',record)
        cp.save(library/'explicit'/(record['record_sha256']+'.json'),record)
        return document,source,record

    def _roundtrip_fixture(self, root):
        _,source,analysis,_,candidate,ar,review=fixture()
        reverse_fake=FakeExec([analysis,ar,candidate,learner_review(),review])
        record,_=ce.run_source(source,root/'reverse'/source['task']['id'],root/'library',reverse_fake)
        topic=ce.record_topic(record)
        task={'id':'transfer','kind':'cards','topic_id':topic['id'],
              'point_ids':[p['id'] for s in topic['sections'] for p in s['memory_points']],
              'format':'choice','count':1,'requirements':'A different context for the same target.'}
        document=ce.exported_points(topic,source['subject_id']); document['tasks']=[task]
        transfer=deepcopy(candidate['sections'][0]['cards'][0])
        transfer.update(question='A vendor transports ice cream through warm streets in an insulated box. How does the insulating layer help prevent melting?',
            reference_answer='A. It slows heat transfer from the warm air into the ice cream.',
            explanation='Insulation slows heat transfer into a colder object, as well as heat loss from a warmer object.')
        transfer['interaction']['options']=[
            {'id':'a','text':'It slows heat transfer from the warm air into the ice cream.'},
            {'id':'b','text':'It continuously removes heat using an internal power source.'}]
        result_review={'approved':True,'checks':[{'index':1,'approved':True,'issues':[],
                       'rationale':'The layer slows heat flow into the cold food during transport; no active refrigeration is described.'}]}
        blind=learner_review()
        blind['checks'][0]['rationale']='The visible transport situation is about reducing heat flow into cold food.'
        forward_fake=FakeExec([{'cards':[transfer]},blind,result_review])
        with patch.object(forward,'make_client',side_effect=lambda config,work,backend,options:forward_fake(work/'exec')):
            result=forward.run_task(cp.task_source(document,task),root/'forward',root/'library',None,1,backend='codex-exec')
        self.assertEqual(result['status'],'accepted')
        cp.save(root/'source.json',{'schema':2,'topics':[topic]}); cp.save(root/'tasks.json',document)
        args=Namespace(source=root/'source.json',tasks=root/'tasks.json',runs=[root/'forward'],
            explicit_runs=[root/'reverse'],library=root/'library',output=root/'prepared',compile=False,
            test_material=True,node='node',course_id='roundtrip-fixture',course_title='Roundtrip fixture',
            unit_id='unit',unit_title='Unit')
        return args,record,reverse_fake,forward_fake

    def test_forward_reuses_complete_explicit_review_without_reauditing_initial_cards(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); args,record,reverse_fake,forward_fake=self._roundtrip_fixture(root)
            args.compile=True
            with (patch.object(forward,'make_client',side_effect=AssertionError('No new audit calls')),
                  patch.object(ce,'exec_call',side_effect=AssertionError('No new explicit calls'))):
                forward.assemble(args)
            self.assertEqual(len(reverse_fake.calls),5); self.assertEqual(len(forward_fake.calls),3)
            reused=cp.read(args.output/'explicit-audits.json')
            self.assertEqual(reused[0]['record_sha256'],record['record_sha256'])
            original=cp.read(args.source)['topics'][0]
            self.assertEqual(reused[0]['source_topic_sha256'],cp.digest(original))
            topic_id=original['id']
            topic=cp.read(args.output/'bundle/topics'/(topic_id+'.json'))
            compiled_source=cp.read(args.output/'bundle/atomic-sources'/(topic_id+'.json'))
            validate_projection(topic,compiled_source)
            self.assertEqual([card['learning_phases'] for card in topic['flashcards']],[['learn'],['review']])
            self.assertEqual(len(list((root/'library').glob('*.json'))),1)

    def test_forward_explicit_review_reuse_rejects_any_topic_source_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); args,_,_,_=self._roundtrip_fixture(root)
            original=cp.read(args.source)
            mutations={
                'introduction':lambda t:t['sections'][0]['introduction'].update(html='<p>A changed introduction.</p>'),
                'card':lambda t:t['sections'][0]['flashcards'][0].update(question='A changed question.'),
                'binding':lambda t:t['sections'][0]['flashcards'][0].update(point_ids=['other-point']),
                'original_text':lambda t:t['reading_materials'][0]['paragraphs'][0].update(text='A changed original paragraph.'),
                'extra_field':lambda t:t.update(unaudited_addition='New material'),
            }
            for name,mutate in mutations.items():
                with self.subTest(change=name):
                    changed=deepcopy(original); mutate(changed['topics'][0])
                    path=root/(name+'.json'); cp.save(path,changed); args.source=path
                    with self.assertRaisesRegex(ValueError,'differs from its complete independent review'):
                        forward.assemble(args)
                    self.assertFalse(args.output.exists())

    def test_forward_explicit_review_reuse_requires_actual_exec_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); args,record,_,_=self._roundtrip_fixture(root)
            directory=Path(record['evidence']['review']['receipt']['call_dir'])
            with (directory/'events.jsonl').open('a',encoding='utf-8') as file: file.write('\nchanged')
            with self.assertRaisesRegex(ValueError,'raw artifact hash'):
                forward.assemble(args)
            self.assertFalse(args.output.exists())

    def test_original_source_is_preserved_and_later_sources_see_prior_points(self):
        document,source,analysis,plan,candidate,_,_=fixture()
        topic=ce.build_topic(candidate,plan,{})
        points=ce.exported_points(topic,'physics')['points']
        following=deepcopy(document['tasks'][0]); following.update(id='next',topic_id='next-topic')
        effective=ce.effective_source(document,following,points)
        self.assertEqual(effective['points'][-1],points[0])
        self.assertIn(points[0]['id'],effective['task']['point_ids'])
        self.assertEqual(topic['reading_materials'][0]['paragraphs'],source['task']['article']['paragraphs'])
        self.assertEqual(topic['source_provenance']['source'],source)
        self.assertEqual(topic['sections'][0]['flashcards'][0]['learning_phases'],['learn'])

    def test_allocated_points_and_exact_evidence_cannot_be_invented(self):
        _,source,analysis,plan,candidate,_,_=fixture()
        self.assertEqual(plan,ce.blueprint(source,analysis))
        changed=deepcopy(candidate); changed['decisions'][0]['point_id']='made-up'
        with self.assertRaises(ValueError): ce.validate_author(changed,plan)
        changed=deepcopy(candidate); changed['decisions'][0]['evidence'][0]['quote']='An invented assertion.'
        with self.assertRaisesRegex(ValueError,'exactly once'): ce.validate_author(changed,plan)
        changed=deepcopy(candidate); changed['decisions'][0]['selected_sense_id']='fake-dictionary-sense'
        with self.assertRaisesRegex(ValueError,'not supplied'): ce.validate_author(changed,plan)

    def test_each_new_target_needs_safe_complete_teaching_and_initial_card(self):
        _,_,_,plan,candidate,_,_=fixture()
        changed=deepcopy(candidate); changed['sections'][0]['cards']=[]
        with self.assertRaisesRegex(ValueError,'initial cards'): ce.validate_author(changed,plan)
        changed=deepcopy(candidate); changed['sections'][0]['introduction']=['<img src=x>']
        with self.assertRaisesRegex(ValueError,'raw HTML'): ce.validate_author(changed,plan)
        changed=deepcopy(candidate); changed['sections'][0]['cards'][0]['point_ids']=['known-ruler']
        with self.assertRaisesRegex(ValueError,'bindings'): ce.validate_author(changed,plan)
        changed=deepcopy(candidate); changed['decisions'][0]['target']='Measure length with a ruler.'
        with self.assertRaisesRegex(ValueError,'duplicates known'): ce.validate_author(changed,plan)

    def test_deferred_and_existing_targets_never_become_new_learning_points(self):
        _,_,_,plan,candidate,_,_=fixture()
        for status in ('deferred','existing'):
            changed=deepcopy(candidate); changed['sections']=[]
            changed['decisions'][0].update(status=status,reason='The supplied evidence needs additional confirmation.',
                existing_point_ids=['known-ruler'] if status=='existing' else [])
            ce.validate_author(changed,plan)
            self.assertIsNone(ce.build_topic(changed,plan,{}))

    def test_independent_review_must_cover_teaching_and_every_initial_card(self):
        _,_,_,_,candidate,_,review=fixture()
        ce.validate_author_review(review,candidate)
        changed=deepcopy(review); changed['checks'].pop(1)
        with self.assertRaisesRegex(ValueError,'every decision'): ce.validate_author_review(changed,candidate)
        changed=deepcopy(review); changed['checks'][0]['issues']=['Incorrect source claim']
        with self.assertRaisesRegex(ValueError,'Inconsistent'): ce.validate_author_review(changed,candidate)

    def test_current_quality_neutralizes_titles_and_keeps_source_metadata_outside_teaching(self):
        _,_,_,plan,candidate,_,_=fixture()
        candidate['sections'][0]['title']='The wool slowed heat transfer.'
        candidate['decisions'][0]['reason'] += ' Source license: CC BY 4.0.'
        ce.validate_author(candidate,plan,cp.QUALITY_POLICY)
        topic=ce.build_topic(candidate,plan,{},cp.QUALITY_POLICY)
        self.assertEqual(topic['sections'][0]['flashcards'][0]['title'],'练习 1')
        legacy=ce.build_topic(candidate,plan,{})
        self.assertEqual(legacy['sections'][0]['flashcards'][0]['title'],candidate['sections'][0]['title'])
        for bad in ('自编教学例句，非原文事实：','CC BY 4.0','本内容由人工智能生成。'):
            with self.subTest(bad=bad):
                changed=deepcopy(candidate); changed['sections'][0]['introduction'].append(bad)
                with self.assertRaisesRegex(ValueError,'outside learner-facing'):
                    ce.validate_author(changed,plan,cp.QUALITY_POLICY)

    def test_learner_review_has_no_source_targets_answers_or_explanations(self):
        _,source,analysis,_,candidate,ar,review=fixture()
        fake=FakeExec([analysis,ar,candidate,learner_review(),review])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); record,_=ce.run_source(source,root/'run',root/'library',fake)
            request=json.loads(fake.calls[3]['messages'][1]['content'])
            self.assertEqual(request,cp.learner_payload(cp.learner_view(ce.author_cards(candidate))))
            self.assertEqual(fake.calls[3]['messages'][0]['content'],cp.LEARNER_REVIEW)
            def check(value):
                if isinstance(value,dict):
                    self.assertFalse(set(value)&{'source','plan','point_ids','reference_answer','explanation','answers','correct_id'})
                    for item in value.values(): check(item)
                elif isinstance(value,list):
                    for item in value: check(item)
            check(request)
            final_request=json.loads(fake.calls[4]['messages'][1]['content'])
            self.assertEqual(final_request['learner_review'],learner_review())
            ce.require_quality(record)

    def test_failed_blind_review_never_reaches_source_review(self):
        _,source,analysis,_,candidate,ar,_=fixture()
        candidate['sections'][0]['cards'][0]['question']='Why did the jar in the earlier exercise cool slowly?'
        rejected={'approved':False,'checks':[{'index':1,'answer':'Cannot determine the missing setup.',
            'approved':False,'issues':['The earlier exercise is not in the visible question.'],
            'rationale':'No information about the jars or cover is visible.'}]}
        fake=FakeExec([analysis,ar,candidate,rejected])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); result,_=ce.run_source(source,root/'run',root/'library',fake,attempts=1)
            self.assertEqual(result['status'],'rejected'); self.assertEqual(len(fake.calls),4)
            self.assertFalse((root/'run/author/attempt-01/review').exists())
            self.assertFalse((root/'library/explicit').exists())

    def test_new_policy_reuses_approved_analysis_but_not_old_explicit_approval(self):
        document,source,analysis,plan,candidate,ar,review=fixture()
        fake=FakeExec([analysis,ar,candidate,review])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); library=root/'library'
            analysis_record=ce._analysis_record(source,root/'old',library,fake,1)
            _,generation=ce.exec_call(root/'old/author-generation',ce.LEGACY_AUTHOR,plan,fake)
            _,audit=ce.exec_call(root/'old/author-review',ce.LEGACY_AUTHOR_REVIEW,
                {'plan':plan,'candidate':candidate,'required_check_ids':ce.review_ids(candidate)},fake)
            old=ce.accepted_record(source,analysis_record,candidate,review,
                {'backend':ce.BACKEND,'generation':generation,'review':audit})
            cp.save(library/'explicit'/(old['record_sha256']+'.json'),old)
            ce.validate_record(old)
            self.assertNotEqual(ce.workflow_key(source),ce.workflow_key(source,cp.QUALITY_POLICY))
            self.assertIsNone(ce.find_record(library,source))
            with self.assertRaisesRegex(ValueError,'Historical explicit approval'): ce.require_quality(old)
            cp.save(root/'old-run'/source['task']['id']/'result.json',old)
            cp.save(root/'input.json',document)
            with self.assertRaisesRegex(ValueError,'Historical explicit approval'):
                cli.prepare(Namespace(input=root/'input.json',runs=[root/'old-run'],library=library,output=root/'bad'))
            next_fake=FakeExec([candidate,learner_review(),review])
            record,_=ce.run_source(source,root/'new-run',library,next_fake,1)
            self.assertEqual(record['analysis_record'],analysis_record)
            self.assertEqual(len(next_fake.calls),3)
            self.assertTrue(all('/author/' in call['path'].replace('\\','/') for call in next_fake.calls))
            ce.require_quality(record)

    def test_initial_transfer_policy_preserves_old_quality_reading_but_blocks_current_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); document,source,old=self._legacy_standalone_record(root)
            ce.validate_record(old)
            old_topic=ce.record_topic(old)
            self.assertEqual(old_topic,ce.build_topic(old['candidate'],ce.blueprint(source,old['analysis_record']['candidate']),
                {'analysis_record_sha256':old['analysis_record']['record_sha256'],'author':old['evidence']},cp.QUALITY_POLICY))
            self.assertEqual(old['workflow_key'],ce.workflow_key(source,cp.QUALITY_POLICY))
            self.assertNotEqual(old['workflow_key'],ce.workflow_key(source,cp.QUALITY_POLICY,ce.AUTHOR_POLICY))
            self.assertIsNone(ce.find_record(root/'library',source))
            with self.assertRaisesRegex(ValueError,'initial-transfer'): ce.require_quality(old)
            cp.save(root/'input.json',document)
            with self.assertRaisesRegex(ValueError,'initial-transfer'):
                cli.prepare(Namespace(input=root/'input.json',runs=[root/'legacy'],library=root/'library',output=root/'bad'))
            with self.assertRaisesRegex(ValueError,'initial-transfer'):
                forward.explicit_audits({'schema':2,'topics':[old_topic]},[root/'legacy'])
            self.assertFalse((root/'bad').exists())
            fake=FakeExec([old['candidate'],learner_review(),old['review']])
            record,origin=ce.run_source(source,root/'new',root/'library',fake,1)
            self.assertEqual(origin,'generated'); self.assertEqual(len(fake.calls),3)
            self.assertEqual(record['analysis_record'],old['analysis_record'])
            self.assertEqual(record['evidence']['author_policy'],ce.AUTHOR_POLICY)
            self.assertEqual(cp.read(root/'new/workflow.json')['author_policy'],ce.AUTHOR_POLICY)
            self.assertEqual(cp.read(root/'new/author/stage.json')['author_policy'],ce.AUTHOR_POLICY)
            self.assertEqual(fake.calls[0]['messages'][0]['content'],ce.AUTHOR)
            self.assertEqual(fake.calls[1]['messages'][0]['content'],cp.LEARNER_REVIEW)
            self.assertEqual(fake.calls[2]['messages'][0]['content'],ce.AUTHOR_REVIEW)
            ce.require_quality(record)

    def test_initial_transfer_label_cannot_upgrade_old_actual_prompt_proof(self):
        for prompt,expected in ((ce.LEGACY_AUTHOR,'Author generation'),(ce.AUTHOR,'Source review did not verify')):
            with self.subTest(prompt=expected), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); _,source,old=self._legacy_standalone_record(root,prompt)
                evidence=deepcopy(old['evidence']); evidence['author_policy']=ce.AUTHOR_POLICY
                with self.assertRaisesRegex(ValueError,expected):
                    ce.accepted_record(source,old['analysis_record'],old['candidate'],old['review'],evidence)

    def test_source_review_can_reject_repeated_worked_example_after_blind_approval(self):
        _,source,analysis,_,candidate,ar,review=fixture()
        card=candidate['sections'][0]['cards'][0]
        card['question']='Two jars contain equally hot water. One has a wool cover; the other has no cover. Why does the covered jar cool more slowly?'
        card['reference_answer']='A. The wool slows heat transfer.'
        card['interaction']['options'][0]['text']='The wool slows heat transfer.'
        card['interaction']['options'][1]['text']='The wool continuously supplies heat.'
        review['approved']=False
        review['checks'][-1].update(approved=False,issues=['The application repeats the solved source jar comparison.'],
            rationale='The learner has just read this exact setup and explanation; no new practical situation is posed.')
        fake=FakeExec([analysis,ar,candidate,learner_review(),review])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); result,_=ce.run_source(source,root/'run',root/'library',fake,1)
            self.assertEqual(result['status'],'rejected'); self.assertEqual(len(fake.calls),5)
            self.assertFalse((root/'library/explicit').exists())
            reviewed=json.loads(fake.calls[-1]['messages'][1]['content'])
            self.assertEqual(reviewed['plan']['source'],source)
            self.assertEqual(reviewed['candidate'],candidate)
            self.assertTrue(reviewed['learner_review']['approved'])
            self.assertIn('merely substitute names or nouns',fake.calls[2]['messages'][0]['content'])
            self.assertIn('every worked example',fake.calls[-1]['messages'][0]['content'])
            self.assertIn('general concept question',fake.calls[-1]['messages'][0]['content'])

    def test_no_new_target_requires_no_author_calls_and_records_current_policy(self):
        _,source,_,_,_,_,_=fixture()
        analysis={'matches':[],'suggestions':[]}
        review={'approved':True,'checks':[{'index':1,'approved':True,'issues':[],
            'rationale':'No genuinely new learning target was identified.'}]}
        fake=FakeExec([analysis,review])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); record,_=ce.run_source(source,root/'run',root/'library',fake,1)
            self.assertEqual(len(fake.calls),2)
            self.assertEqual(record['evidence'],{'backend':ce.BACKEND,'author_call_required':False,
                'quality_policy':cp.QUALITY_POLICY,'author_policy':ce.AUTHOR_POLICY})
            ce.require_quality(record); self.assertIsNone(ce.record_topic(record))

    def test_invalid_learner_output_retries_only_learner_then_source(self):
        _,source,analysis,_,candidate,ar,review=fixture()
        fake=FakeExec([analysis,ar,candidate,{'approved':True,'checks':[]},learner_review(),review])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); record,_=ce.run_source(source,root/'run',root/'library',fake)
            self.assertEqual(record['candidate'],candidate); self.assertEqual(len(fake.calls),6)
            self.assertFalse((root/'run/author/attempt-02/generation').exists())
            self.assertTrue((root/'run/author/attempt-02/learner').exists())

    def test_unknown_blind_review_outcome_is_not_retried_or_sent_to_source_reviewer(self):
        _,source,analysis,_,candidate,ar,_=fixture()
        fake=FakeExec([analysis,ar,candidate,TimeoutError('No terminal learner result')])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); result,_=ce.run_source(source,root/'run',root/'library',fake)
            self.assertEqual(result['status'],'outcome_unknown'); self.assertEqual(len(fake.calls),4)
            self.assertFalse((root/'run/author/attempt-02').exists())

    def test_five_fresh_exec_sessions_and_offline_reuse_no_legacy_api(self):
        _,source,analysis,_,candidate,ar,review=fixture()
        fake=FakeExec([analysis,ar,candidate,learner_review(),review])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            record,origin=ce.run_source(source,root/'run',root/'library',fake)
            self.assertEqual(origin,'generated'); self.assertEqual(len(fake.calls),5)
            self.assertEqual(len({c['path'] for c in fake.calls}),5)
            with patch.object(ce,'exec_call',side_effect=AssertionError('Approved reuse must be offline')):
                repeated,origin=ce.run_source(source,root/'again',root/'library',None)
            self.assertEqual(origin,'library'); self.assertEqual(repeated,record)
            changed=deepcopy(record); changed['backend']='api'
            with self.assertRaises(ValueError): ce.validate_record(changed)
            changed=deepcopy(record); changed['candidate']['sections'][0]['cards'][0]['reference_answer']='B'
            with self.assertRaises(ValueError): ce.validate_record(changed)
            changed=deepcopy(record['evidence']); changed['review']['receipt']['call_id']=changed['generation']['receipt']['call_id']
            with self.assertRaisesRegex(ValueError,'independent'): ce.validate_exec_pair(changed)

    def test_invalid_review_retries_only_review_and_retains_original_generation(self):
        _,source,analysis,_,candidate,ar,review=fixture()
        fake=FakeExec([analysis,ar,candidate,learner_review(),{'approved':True,'checks':[]},review])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            record,_=ce.run_source(source,root/'run',root/'library',fake)
            self.assertEqual(record['candidate'],candidate)
            self.assertEqual(len(fake.calls),6)
            self.assertIn('author/attempt-02/review',fake.calls[-1]['path'].replace('\\','/'))
            self.assertFalse((root/'run/author/attempt-02/generation').exists())
            self.assertFalse((root/'run/author/attempt-02/learner').exists())

    def test_explicit_generation_and_review_recheck_actual_retained_exec_files(self):
        _,source,analysis,_,candidate,ar,review=fixture()
        for role in ('generation','learner','review'):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); fake=FakeExec([analysis,ar,candidate,learner_review(),review])
                record,_=ce.run_source(source,root/'run',root/'library',fake)
                proof=record['evidence'][role]['proof'] if role=='learner' else record['evidence'][role]
                directory=Path(proof['receipt']['call_dir'])
                with (directory/'events.jsonl').open('a',encoding='utf-8') as file: file.write('\nchanged')
                with self.assertRaisesRegex(ValueError,'raw artifact hash'):
                    ce.validate_record(record)

    def test_explicit_author_record_cannot_borrow_a_different_response(self):
        _,source,analysis,_,candidate,ar,review=fixture()
        fake=FakeExec([analysis,ar,candidate,learner_review(),review])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); record,_=ce.run_source(source,root/'run',root/'library',fake)
            evidence=deepcopy(record['evidence'])
            evidence['generation']=deepcopy(record['analysis_record']['evidence']['generation'])
            evidence['generation']['response_sha256']=cp.digest(candidate)
            with self.assertRaisesRegex(ValueError,'actual Codex Exec response'):
                ce.accepted_record(source,record['analysis_record'],candidate,review,evidence)

    def test_unknown_exec_outcome_is_not_blindly_retried(self):
        _,source,_,_,_,_,_=fixture()
        fake=FakeExec([TimeoutError('uncertain result')])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            result,_=ce.run_source(source,root/'run',root/'library',fake)
            self.assertEqual(result['status'],'outcome_unknown')
            repeated,_=ce.run_source(source,root/'run',root/'library',fake)
            self.assertEqual(repeated['status'],'outcome_unknown')
            self.assertEqual(len(fake.calls),1)

    def test_complete_schema2_compiles_through_existing_importer(self):
        document,_,analysis,_,candidate,ar,review=fixture()
        fake=FakeExec([analysis,ar,candidate,learner_review(),review])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); cp.save(root/'input.json',document)
            args=Namespace(input=root/'input.json',run_root=root/'run',library=root/'library',attempts=3)
            self.assertEqual(cli.run(args,fake),0)
            cli.prepare(Namespace(input=args.input,runs=[args.run_root],library=args.library,
                output=root/'prepared',compile=True,test_material=True,node='node',
                course_id='explicit-fixture',course_title='Explicit fixture',unit_id='unit',unit_title='Unit'))
            package=root/'prepared/bundle'; ident=document['tasks'][0]['topic_id']
            topic=cp.read(package/'topics'/(ident+'.json'))
            source=cp.read(package/'atomic-sources'/(ident+'.json'))
            validate_projection(topic,source)
            self.assertEqual(len(topic['flashcards']),1)
            self.assertEqual(topic['flashcards'][0]['learning_phases'],['learn'])
            points=ce.load_known(root/'prepared/known-points')
            self.assertEqual(points['points'][0]['id'],candidate['decisions'][0]['point_id'])
            self.assertEqual(cp.read(package/'manifest.json')['test_materials'],{'courses':['explicit-fixture']})


if __name__=='__main__': unittest.main()
