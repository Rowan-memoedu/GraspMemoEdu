"""Production count, provenance and phase boundaries using independent synthetic input."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import atomic_question_trial as trial
from scripts.produce_atomic_questions import assemble, refill_required, run_point, source_tasks, render_reading, review_candidate
from scripts.atomic_question_recovery import checkpoint, recover_response, worker_capacity
from types import SimpleNamespace
import hashlib
from scripts.verify_atomic_question_production import verify


def document():
    return {'schema': 2, 'topics': [{'id': 'independent', 'title': 'Labels', 'sections': [{
        'status': 'ai_passed', 'import_eligible': True,
        'memory_chunks': [{'id': 'c', 'content': 'The label is amber.'}],
        'memory_points': [{'id': 'p', 'chunk_id': 'c', 'content': 'The label is amber.'}],
        'flashcards': [{'id': 'seed', 'point_ids': ['p'], 'question': 'What is the label?', 'answer': 'amber'}]}]}]}


def candidate(count):
    return {'point_id': 'p', 'status': 'ok', 'reason': '', 'variants': [
        {'point_ids': ['p'], 'question': f'Independent presentation {i}?', 'reference_answer': 'amber',
         'explanation': 'The label is amber.', 'interaction': {'version': 1, 'type': 'text', 'grading': 'semantic'}}
        for i in range(count)]}


class AtomicQuestionProductionTests(unittest.TestCase):
    def test_final_verifier_checks_raw_calls_and_unchanged_upstream(self):
        original=document(); tasks=source_tasks(original,'english',2); task=tasks[0]
        value=candidate(2)
        verdict={'point_id':'p','approved':True,'checks':[
            {'index':i,'approved':True,'issues':[],'rationale':'Same target.'} for i in (1,2)]}
        result={'id':'p','status':'accepted','source_sha256':trial.digest(task),'attempt':1,'variants':value['variants']}
        exported=assemble(original,tasks,[result],replace_representatives=True)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); work=root/'p'/'attempt-01'
            trial.save(root/'run.json',{'subject':'english','count':2,'model':'fixture','replace_representatives':True,
                'generation_sha256':trial.digest('generate'),'review_sha256':trial.digest('review')})
            trial.save(root/'source-snapshot.json',original); trial.save(root/'tasks.json',tasks)
            (root/'generation-prompt.md').write_text('generate',encoding='utf-8')
            (root/'review-prompt.md').write_text('review',encoding='utf-8')
            trial.save(root/'p'/'result.json',result)
            trial.save(work/'result.json',{'candidate':value,'review':verdict,'status':'accepted'})
            for phase,payload,answer in [('generation',{'task':task},value),('review',{'source':task,'candidate':value},verdict)]:
                trial.save(work/(phase+'.request.json'),{'messages':[{'role':'user','content':trial.json.dumps(payload)}]})
                trial.save(work/(phase+'.response.json'),{'provider':'codex_exec','model':'fixture',
                    'transport':{'completed_event':True},'choices':[{'finish_reason':'stop','message':{'content':trial.json.dumps(answer)}}]})
            trial.save(root/'structured-topics.enriched.json',exported)
            self.assertTrue(verify(root)['verified'])
            exported['topics'][0]['sections'][0]['memory_points'][0]['content']='tampered'
            (root/'structured-topics.enriched.json').write_text(trial.json.dumps(exported),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Export differs'):
                verify(root)

    def test_inconsistent_review_retries_review_only_with_fixed_candidate(self):
        source=source_tasks(document(),'english',2)[0]
        good={'point_id':'p','approved':True,'checks':[
            {'index':i,'approved':True,'issues':[],'rationale':'Same target.'} for i in (1,2)]}
        bad={**good,'approved':False}
        value=candidate(2)
        with patch.object(trial,'call',return_value={}) as call, \
                patch('scripts.produce_atomic_questions._object',side_effect=[bad,good]):
            verdict,phase=review_candidate(None,Path('unused'),'review',source,value)
        self.assertEqual(phase,'review-02')
        self.assertTrue(verdict['approved'])
        self.assertEqual([c.args[2] for c in call.call_args_list],['review','review-02'])
        self.assertIs(call.call_args_list[1].args[4]['candidate'],value)

    def test_memory_pressure_bounds_startup_without_changing_requested_concurrency(self):
        self.assertEqual(worker_capacity(32, 4096), 2)
        self.assertEqual(worker_capacity(32, 2048), 0)
        self.assertEqual(worker_capacity(8, 16000), 8)

    def test_completed_exit_failure_can_be_recovered_but_incomplete_turn_cannot(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); work=root/'point'; diagnostics=root/'calls'/'call-fixture'
            trial.save(work/'generation.request.json',{'messages':[
                {'role':'system','content':'instructions'}, {'role':'user','content':'isolated input'}]})
            trial.save(work/'generation.started.json',{'started_at':'2026-01-01T00:00:00+00:00'})
            diagnostics.mkdir(parents=True)
            (diagnostics/'instructions.md').write_text('producer\n\ninstructions',encoding='utf-8')
            (diagnostics/'response.txt').write_text('{"fixture":true}',encoding='utf-8')
            failure={'error':'producer_codex_exit_1','failed_at':'2026-01-01T00:01:00Z','model':'fixture',
                     'user_payload_sha256':hashlib.sha256(b'isolated input').hexdigest(),
                     'event_summary':[{'type':'turn.started'}]}
            trial.save(diagnostics/'failure.json',failure)
            client=SimpleNamespace(model='fixture',identity=['codex_exec','fixture'])
            recover_response(work,'generation',client,root/'calls')
            self.assertFalse((work/'generation.response.json').exists())
            failure['event_summary'].append({'type':'turn.completed'})
            (diagnostics/'failure.json').write_text(trial.json.dumps(failure),encoding='utf-8')
            recover_response(work,'generation',client,root/'calls')
            value=trial.read(work/'generation.response.json')
            self.assertEqual(value['choices'][0]['message']['content'],'{"fixture":true}')
            self.assertTrue(value['transport']['completed_event'])

    def test_retry_retains_original_receipt_and_reuses_accepted_result(self):
        source=source_tasks(document(),'english',2)[0]
        review={'point_id':'p','approved':True,'checks':[
            {'index':i,'approved':True,'issues':[],'rationale':'Same target.'} for i in (1,2)]}
        with tempfile.TemporaryDirectory() as d, patch('scripts.produce_atomic_questions.CompatibleClient'), \
                patch.object(trial,'generation_payload',return_value={}), \
                patch.object(trial,'call',return_value={}) as call, \
                patch('scripts.produce_atomic_questions._object',side_effect=[candidate(2),review]):
            root=Path(d); original={'id':'p','source_sha256':trial.digest(source),'status':'internal_error','attempt':1}
            trial.save(root/'p'/'result.json',original)
            trial.save(root/'p'/'attempt-01'/'result.json',{'status':'internal_error','error_type':'OSError'})
            result=run_point(source,root,(),'generate','review',{},3,True)
            self.assertEqual(result['status'],'accepted')
            self.assertEqual(result['attempt'],2)
            self.assertEqual(call.call_count,2)
            self.assertEqual(trial.read(next((root/'p'/'result-history').glob('*.json'))),original)
            self.assertEqual(run_point(source,root,(),'generate','review',{},3,True),result)
            self.assertEqual(call.call_count,2)

    def test_local_reading_includes_options_and_preserves_answer_code_fences(self):
        value=candidate(2)
        value['variants'][0].update(reference_answer='```text\namber\n```',
            interaction={'version':1,'type':'choice','options':[{'id':'a','text':'amber'},{'id':'b','text':'blue'}],'correct_id':'a'})
        original=document()
        refreshed=assemble(original,source_tasks(original,'english',2),
            [{'id':'p','status':'accepted','variants':value['variants']}],replace_representatives=True)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); render_reading(refreshed,root)
            text=(root/'topics'/'independent.md').read_text(encoding='utf-8')
            self.assertIn('- B. blue',text)
            self.assertIn('**答案**：\n\n```text\namber\n```',text)
            self.assertIn('[Labels](topics/independent.md)',(root/'阅读入口.md').read_text(encoding='utf-8'))

    def test_optional_explanation_does_not_require_repeating_the_answer(self):
        value=candidate(2)
        value['variants'][0]['explanation']=''
        trial.validate_pair(value,source_tasks(document(),'english',2)[0])
        self.assertFalse(any('解析' in line for line in trial.question_lines(value['variants'][0])))
        value['variants'][0]['reference_answer']=''
        with self.assertRaises(ValueError): trial.validate_pair(value,source_tasks(document(),'english',2)[0])

    def test_count_override_and_default_pair_are_both_enforced(self):
        source = source_tasks(document(), 'english', 3)[0]
        trial.validate_pair(candidate(3), source)
        with self.assertRaisesRegex(ValueError, 'Exactly 3'):
            trial.validate_pair(candidate(2), source)
        source.pop('variant_count')
        trial.validate_pair(candidate(2), source)
        with self.assertRaisesRegex(ValueError, 'Exactly two'):
            trial.validate_pair(candidate(3), source)

    def test_all_registered_subjects_use_the_same_source_contract(self):
        for subject in trial.SUBJECTS:
            source = source_tasks(document(), subject, 3)[0]
            self.assertEqual(source['subject_id'], subject)
            self.assertEqual(source['seed_card']['reference_answer'], 'amber')

    def test_export_preserves_original_and_appends_review_only_cards(self):
        original = document()
        before = deepcopy(original)
        tasks = source_tasks(original, 'english', 3)
        enriched = assemble(original, tasks, [{'id': 'p', 'status': 'accepted', 'variants': candidate(3)['variants']}])
        self.assertEqual(original, before)
        section = enriched['topics'][0]['sections'][0]
        self.assertEqual(section['flashcards'][0], before['topics'][0]['sections'][0]['flashcards'][0])
        self.assertEqual(len(section['flashcards']), 4)
        self.assertTrue(all(c['learning_phases'] == ['review'] for c in section['flashcards'][1:]))

    def test_partial_or_rejected_batch_cannot_be_exported(self):
        for results in ([], [{'id': 'p', 'status': 'rejected'}]):
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                assemble(document(), source_tasks(document(), 'english', 3), results)

    def test_representative_refresh_changes_only_cards_in_a_derived_export(self):
        original=document()
        section=original['topics'][0]['sections'][0]
        section['memory_points'][0]['retrieval_target']='Given the label role, retrieve its color.'
        before=deepcopy(original)
        tasks=source_tasks(original,'english',2)
        self.assertIn('Given the label role',tasks[0]['point']['target'])
        refreshed=assemble(original,tasks,[{'id':'p','status':'accepted','variants':candidate(2)['variants']}],
                           replace_representatives=True)
        after=refreshed['topics'][0]['sections'][0]
        self.assertEqual(original,before)
        self.assertEqual({k:v for k,v in after.items() if k!='flashcards'},
                         {k:v for k,v in section.items() if k!='flashcards'})
        self.assertEqual(len(after['flashcards']),2)
        self.assertEqual(after['flashcards'][0]['learning_phases'],['learn','review'])
        self.assertEqual(after['flashcards'][1]['learning_phases'],['review'])
        self.assertNotIn('seed',[c['id'] for c in after['flashcards']])

    def test_local_demand_waits_for_mastery_and_zero_unseen(self):
        cards = [{'id': 'seed'}, {'id': 'a', 'learning_phases': ['review']}, {'id': 'b', 'learning_phases': ['review']}]
        self.assertFalse(refill_required(cards, {'seed', 'a', 'b'}, mastered=False))
        self.assertFalse(refill_required(cards, {'seed', 'a'}, mastered=True))
        self.assertTrue(refill_required(cards, {'seed', 'a', 'b'}, mastered=True))

    def test_review_requires_exact_count_and_consistent_verdicts(self):
        value = {'point_id': 'p', 'approved': True, 'checks': [
            {'index': i, 'approved': True, 'issues': [], 'rationale': 'The same fact is tested.'} for i in range(1, 4)]}
        trial.validate_review(value, 'p', 3)
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            trial.validate_review(value, 'p')

    def test_receipt_distinguishes_structured_semantic_from_mechanical_grading(self):
        cards = [{'interaction': {'type': 'text', 'grading': 'semantic'}},
                 {'interaction': {'type': 'fill_blank', 'grading': 'semantic'}},
                 {'interaction': {'type': 'fill_blank', 'grading': 'exact'}},
                 {'interaction': {'type': 'choice'}}, {'interaction': {'type': 'matching'}},
                 {'interaction': {'type': 'true_false'}}]
        summary = trial.interaction_summary(cards)
        self.assertEqual(summary['semantic_graded'], 2)
        self.assertEqual(summary['mechanically_graded'], 4)
        self.assertEqual(summary['by_type']['fill_blank'], 2)

    def test_a_blank_stem_cannot_silently_fall_back_to_a_text_box(self):
        value = candidate(2)
        value['variants'][0]['question'] = 'The label is {{blank:b1}}.'
        with self.assertRaisesRegex(ValueError, 'Blank markers require fill_blank'):
            trial.validate_pair(value, source_tasks(document(), 'english', 2)[0])

    def test_default_two_card_source_also_reaches_review_without_rebilling(self):
        source = source_tasks(document(), 'english', 2)[0]
        source.pop('variant_count')
        review = {'point_id': 'p', 'approved': True, 'checks': [
            {'index': i, 'approved': True, 'issues': [], 'rationale': 'The target is preserved.'} for i in (1, 2)]}
        with tempfile.TemporaryDirectory() as temporary, \
                patch('scripts.produce_atomic_questions.CompatibleClient'), \
                patch.object(trial, 'generation_payload', return_value={}), \
                patch.object(trial, 'call', return_value={}) as call, \
                patch('scripts.produce_atomic_questions._object', side_effect=[candidate(2), review]):
            result = run_point(source, Path(temporary), (), 'generate', 'review', {}, 4)
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(call.call_count, 2)

    def test_internal_program_error_stops_instead_of_repeating_provider_calls(self):
        source = source_tasks(document(), 'english', 2)[0]
        with tempfile.TemporaryDirectory() as temporary, \
                patch('scripts.produce_atomic_questions.CompatibleClient', side_effect=KeyError('internal')) as client:
            result = run_point(source, Path(temporary), (), 'generate', 'review', {}, 4)
            self.assertEqual(result['status'], 'internal_error')
            self.assertEqual(client.call_count, 1)


if __name__ == '__main__':
    unittest.main()
