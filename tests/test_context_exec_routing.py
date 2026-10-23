"""Engine provenance and resumability for the existing local context workflow."""
from argparse import Namespace
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from graspmemoedu import context_production as cp
from scripts import produce_context as runner


def source_fixture():
    return {'schema': 1, 'subject_id': 'english',
            'points': [{'id': 'request', 'target': 'Ask for a needed object.'}],
            'tasks': [{'id': 'new-context', 'kind': 'cards', 'topic_id': 'context-test',
                       'point_ids': ['request'], 'format': 'cloze', 'count': 1,
                       'requirements': 'A natural request for help.'}]}


def candidate_fixture():
    return {'cards': [{'point_ids': ['request'], 'question': 'They asked {{blank:a}} help.',
                      'reference_answer': 'for', 'explanation': 'Ask for introduces what is requested.',
                      'interaction': {'version': 1, 'type': 'fill_blank', 'grading': 'exact',
                                      'blanks': [{'id': 'a', 'answers': ['for']}]}}]}


def review_fixture():
    return {'approved': True, 'checks': [{'index': 1, 'approved': True, 'issues': [],
                                        'rationale': 'The object requested is help: ask for help.'}]}


def learner_fixture():
    return {'approved':True,'checks':[{'index':1,'answer':'for','approved':True,'issues':[],
        'rationale':'The visible request asks for help; no external article is needed.'}]}


class FakeClient:
    """Synthetic transport files for unit tests, never actual production proof."""
    model = 'configured-codex-model'
    reasoning_effort = 'configured-effort'

    def __init__(self, root, responses=None):
        self.root = Path(root)
        self.calls = []
        self.responses = list(responses if responses is not None else [candidate_fixture(), learner_fixture(), review_fixture()])

    def complete_limited(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        value = deepcopy(self.responses.pop(0))
        if isinstance(value,Exception): raise value
        directory = self.root / ('fixture-call-' + str(len(self.calls)))
        directory.mkdir(parents=True)
        cp.save(directory / 'messages.json', messages)
        cp.save(directory / 'result.json', value)
        raw = json.dumps({'result_json': json.dumps(value, ensure_ascii=False)}, ensure_ascii=False)
        (directory / 'final.txt').write_text(raw, encoding='utf-8')
        (directory / 'events.jsonl').write_text(json.dumps({'fixture': True, 'text': raw}), encoding='utf-8')
        (directory / 'prompt.txt').write_text('UNIT FIXTURE ONLY\n' + json.dumps(messages, ensure_ascii=False), encoding='utf-8')
        self.last_receipt = {'backend': 'codex-exec', 'call_id': directory.name,
            'call_dir': str(directory), 'model': self.model, 'reasoning_effort': self.reasoning_effort,
            'status': 'success', 'exit_code': 0, 'model_execution_count': 1, 'event_check': {'passed': True}}
        for name, field in (('result.json', 'result_sha256'), ('events.jsonl', 'events_sha256'),
                            ('final.txt', 'final_sha256'), ('prompt.txt', 'input_sha256')):
            self.last_receipt[field] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        cp.save(directory / 'receipt.json', self.last_receipt)
        return value


class ContextExecRoutingTests(unittest.TestCase):
    def test_codex_run_does_not_read_api_credentials_or_reuse_api_records(self):
        doc = source_fixture()
        source = cp.task_source(doc, doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = root / 'library'
            old = cp.accepted_record(source, candidate_fixture(), review_fixture(), {'run': 'legacy-api'})
            cp.save(library / (old['record_sha256'] + '.json'), old)
            cp.save(root / 'input.json', doc)
            args = Namespace(input=root / 'input.json', library=library, run_root=root / 'first',
                             workers=1, attempts=1, only=None, candidate_runs=[], backend='codex-exec')
            client = FakeClient(root / 'transport')
            with patch.object(runner, 'make_client', return_value=client) as make, \
                 patch.object(runner.transport, 'configuration', side_effect=AssertionError('API config forbidden')):
                self.assertEqual(runner.run(args), 0)
            self.assertEqual(len(client.calls), 3)
            self.assertEqual(make.call_args.args[2], 'codex-exec')
            self.assertEqual(client.calls[0][0]['content'], cp.GENERATION)
            self.assertEqual(client.calls[1][0]['content'], cp.LEARNER_REVIEW)
            self.assertEqual(client.calls[2][0]['content'], cp.REVIEW)
            self.assertNotIn('messages', client.calls[2][1]['content'])
            result = cp.read(args.run_root / 'new-context/result.json')
            self.assertEqual(result['backend'], 'codex-exec')
            self.assertEqual(result['origin'], 'generated')
            self.assertEqual(cp.find_material(library, source, 'api')['record_sha256'], old['record_sha256'])
            self.assertIsNone(cp.find_material(library,source,'codex-exec'))
            self.assertEqual(cp.find_material(library,source,'codex-exec',cp.QUALITY_POLICY)['record_sha256'],result['record_sha256'])
            record = cp.read(library / (result['record_sha256'] + '.json'))
            evidence = record['evidence']
            self.assertNotEqual(evidence['generation']['receipt']['call_id'], evidence['review']['receipt']['call_id'])
            cp.validate_exec_evidence(source, candidate_fixture(), review_fixture(), evidence)
            args.run_root = root / 'repeat'
            with patch.object(runner, 'make_client', side_effect=AssertionError('Accepted record must be reused')), \
                 patch.object(runner.transport, 'configuration', side_effect=AssertionError('No API config')):
                self.assertEqual(runner.run(args), 0)
            self.assertEqual(cp.read(args.run_root / 'new-context/result.json')['origin'], 'library')

    def test_provided_cards_require_blind_and_source_review_without_generation(self):
        doc = source_fixture(); doc['tasks'][0]['provided_cards'] = candidate_fixture()['cards']
        source = cp.task_source(doc, doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); client = FakeClient(root / 'transport', [learner_fixture(),review_fixture()])
            with patch.object(runner, 'make_client', return_value=client):
                result = runner.run_task(source, root / 'run', root / 'library', None, 1, backend='codex-exec')
            self.assertEqual(result['status'], 'accepted'); self.assertEqual(result['origin'], 'authored')
            self.assertEqual(len(client.calls), 2); self.assertEqual(client.calls[0][0]['content'], cp.LEARNER_REVIEW)
            self.assertEqual(client.calls[1][0]['content'],cp.REVIEW)
            record = cp.read(root / 'library' / (result['record_sha256'] + '.json'))
            self.assertNotIn('generation', record['evidence'])
            cp.validate_record(record)

    def test_proof_binds_success_receipt_response_prompt_and_original_source(self):
        doc = source_fixture(); source = cp.task_source(doc, doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); client = FakeClient(root / 'transport',[candidate_fixture(),review_fixture()])
            runner.codex_response(client, root / 'run', 'generation', cp.GENERATION, source)
            runner.codex_response(client, root / 'run', 'review', cp.REVIEW,
                                  {'source': source, 'candidate': candidate_fixture()})
            evidence = {'backend': 'codex-exec', 'generation': cp.read(root / 'run/generation.exec-proof.json'),
                        'review': cp.read(root / 'run/review.exec-proof.json')}
            cp.accepted_record(source, candidate_fixture(), review_fixture(), evidence)
            changed = deepcopy(evidence); changed['generation']['receipt']['status'] = 'failed'
            with self.assertRaisesRegex(ValueError, 'Successful'):
                cp.validate_exec_evidence(source, candidate_fixture(), review_fixture(), changed)
            changed = candidate_fixture(); changed['cards'][0]['question'] = 'We asked {{blank:a}} help.'
            proof = deepcopy(evidence['generation']); proof['response_sha256'] = cp.digest(changed)
            with self.assertRaisesRegex(ValueError, 'actual Codex Exec response'):
                cp.validate_exec_proof(proof, changed)
            changed = deepcopy(source); changed['task']['requirements'] = 'Different task.'
            with self.assertRaisesRegex(ValueError, 'different source/candidate'):
                cp.validate_exec_evidence(changed, candidate_fixture(), review_fixture(), evidence)
            directory = Path(evidence['generation']['receipt']['call_dir'])
            messages = cp.read(directory / 'messages.json'); messages[1]['content'] = '{}'
            (directory / 'messages.json').write_text(json.dumps(messages), encoding='utf-8')
            proof = deepcopy(evidence['generation']); proof['request_sha256'] = cp.digest({'backend': 'codex-exec', 'messages': messages})
            with self.assertRaisesRegex(ValueError, 'executed prompt'):
                cp.validate_exec_proof(proof, candidate_fixture())

    def test_cached_response_cannot_be_used_for_a_different_request_or_changed_raw_event(self):
        doc = source_fixture(); source = cp.task_source(doc, doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); client = FakeClient(root / 'transport')
            runner.codex_response(client, root / 'run', 'generation', cp.GENERATION, source)
            with self.assertRaisesRegex(ValueError, 'different request'):
                runner.codex_response(client, root / 'run', 'generation', cp.GENERATION, {'changed': True})
            directory = Path(client.last_receipt['call_dir'])
            with (directory / 'events.jsonl').open('a', encoding='utf-8') as file: file.write('\nchanged')
            with self.assertRaisesRegex(ValueError, 'raw artifact hash'):
                runner.codex_response(client, root / 'run', 'generation', cp.GENERATION, source)
            self.assertEqual(len(client.calls), 1)

    def test_saved_codex_result_cannot_point_at_legacy_api_record(self):
        doc = source_fixture(); source = cp.task_source(doc, doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); library = root / 'library'
            record = cp.accepted_record(source, candidate_fixture(), review_fixture(), {'run': 'legacy'})
            cp.save(library / (record['record_sha256'] + '.json'), record)
            cp.save(root / 'new-context/result.json', {'source_sha256': cp.digest(source), 'source': source,
                'status': 'accepted', 'backend': 'codex-exec', 'record_sha256': record['record_sha256'],
                'candidate': candidate_fixture(), 'review': review_fixture()})
            with self.assertRaisesRegex(ValueError, 'approved Exec record'):
                runner.run_task(source, root, library, None, 1, backend='codex-exec')

    def test_saved_result_cannot_change_execution_engine_on_resume(self):
        doc = source_fixture(); source = cp.task_source(doc, doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cp.save(root / 'new-context/result.json', {'source_sha256': cp.digest(source), 'status': 'accepted'})
            with self.assertRaisesRegex(ValueError, 'backend differs'):
                runner.run_task(source, root, root / 'library', None, 1, backend='codex-exec')

    def test_retained_generation_requires_the_same_backend(self):
        doc = source_fixture(); source = cp.task_source(doc, doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cp.save(root / 'run.json', {'generation_prompt_sha256': cp.digest(cp.GENERATION)})
            cp.save(root / 'input-snapshot.json', doc)
            cp.save(root / 'new-context/attempt-01/generation.response.json', candidate_fixture())
            self.assertIsNone(runner.retained_candidate(source, [root], 'codex-exec'))
            self.assertIsNotNone(runner.retained_candidate(source, [root], 'api'))

    def test_retained_generation_needs_intact_actual_exec_proof(self):
        doc = source_fixture(); source = cp.task_source(doc, doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cp.save(root / 'run.json', {'backend': 'codex-exec', 'generation_prompt_sha256': cp.digest(cp.GENERATION)})
            cp.save(root / 'input-snapshot.json', doc)
            work = root / 'new-context/attempt-01'
            runner.codex_response(FakeClient(root / 'transport'), work, 'generation', cp.GENERATION, source)
            self.assertEqual(runner.retained_candidate(source, [root], 'codex-exec')[0], candidate_fixture())
            changed = candidate_fixture(); changed['cards'][0]['reference_answer'] = 'about'
            (work / 'generation.response.json').write_text(json.dumps(changed), encoding='utf-8')
            self.assertIsNone(runner.retained_candidate(source, [root], 'codex-exec'))
            (work / 'generation.response.json').write_text(json.dumps(candidate_fixture()), encoding='utf-8')
            (work / 'generation.exec-proof.json').unlink()
            self.assertIsNone(runner.retained_candidate(source, [root], 'codex-exec'))

    def test_raw_composite_audit_matches_only_the_exact_safe_rendered_source(self):
        raw = candidate_fixture()['cards'][0]
        raw['interaction'] = {'version': 2, 'type': 'composite', 'grading': 'exact',
            'controls': [{'id': 'a', 'type': 'text', 'answers': ['for']}],
            'sections': [{'id': 's', 'text': 'Read A & B.\nComplete the gap.', 'control_ids': ['a'], 'complete': True}]}
        doc = source_fixture(); doc['tasks'][0]['provided_cards'] = [raw]
        source = cp.task_source(doc, doc['tasks'][0]); candidate = {'cards': [raw]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); client = FakeClient(root / 'transport', [learner_fixture(),review_fixture()])
            with patch.object(runner, 'make_client', return_value=client):
                result = runner.run_task(source, root / 'run', root / 'library', None, 1, backend='codex-exec')
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(json.loads(client.calls[1][1]['content'])['candidate'], candidate)
            with self.assertRaisesRegex(ValueError, 'HTML fields'):
                cp.validate_candidate({'cards': [cp.normalize_card(raw)]}, source)
            cp.save(root / 'tasks.json', doc)
            for style in ('plain', 'rendered', 'changed'):
                seed = deepcopy(raw) if style == 'plain' else cp.normalize_card(raw)
                if style == 'changed': seed['interaction']['sections'][0]['html'] = '<p>A different teaching passage.</p>'
                structured = {'schema': 2, 'topics': [{'id': 'context-test', 'sections': [{
                    'memory_points': [{'id': 'request'}], 'flashcards': [{'id': 'seed', 'title':'The answer is for', **seed}]}]}]}
                path = root / (style + '.json'); cp.save(path, structured)
                args = Namespace(source=path, tasks=root / 'tasks.json', runs=[root / 'run'],
                    library=root / 'library', output=root / ('prepared-' + style), compile=False)
                if style == 'changed':
                    with self.assertRaisesRegex(ValueError, 'differs from its independent audit'): runner.assemble(args)
                else:
                    runner.assemble(args)
                    self.assertTrue((args.output / 'structured-topics.json').is_file())
                    prepared=cp.read(args.output/'structured-topics.json')
                    self.assertEqual(prepared['topics'][0]['sections'][0]['flashcards'][0]['title'],'练习 1')

    def test_quality_cache_excludes_old_exec_approval_but_historical_record_stays_readable(self):
        doc=source_fixture(); source=cp.task_source(doc,doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); client=FakeClient(root/'old-transport',[candidate_fixture(),review_fixture()])
            runner.codex_response(client,root/'old','generation',cp.GENERATION,source)
            runner.codex_response(client,root/'old','review',cp.REVIEW,{'source':source,'candidate':candidate_fixture()})
            old=cp.accepted_record(source,candidate_fixture(),review_fixture(),{'backend':'codex-exec',
                'generation':cp.read(root/'old/generation.exec-proof.json'),'review':cp.read(root/'old/review.exec-proof.json')})
            cp.save(root/'library'/(old['record_sha256']+'.json'),old)
            cp.validate_record(old)
            with self.assertRaisesRegex(ValueError,'Historical approval'): cp.require_quality(old)
            self.assertIsNone(cp.find_material(root/'library',source,'codex-exec',cp.QUALITY_POLICY))
            self.assertNotEqual(cp.task_key(source),cp.task_key(source,cp.QUALITY_POLICY))
            new=FakeClient(root/'new-transport')
            with patch.object(runner,'make_client',return_value=new):
                result=runner.run_task(source,root/'new',root/'library',None,1,backend='codex-exec')
            self.assertEqual(result['status'],'accepted'); self.assertEqual(len(new.calls),3)
            self.assertNotEqual(result['record_sha256'],old['record_sha256'])

    def test_learner_proof_rejects_hidden_source_in_its_actual_exec_request(self):
        doc=source_fixture(); source=cp.task_source(doc,doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); client=FakeClient(root/'transport')
            runner.codex_response(client,root,'generation',cp.GENERATION,source)
            view=cp.learner_view(candidate_fixture()['cards'])
            runner.codex_response(client,root,'learner',cp.LEARNER_REVIEW,{**cp.learner_payload(view),'source':source})
            payload=cp.quality_review_payload({'source':source,'candidate':candidate_fixture()},view,learner_fixture())
            runner.codex_response(client,root,'review',cp.REVIEW,payload)
            evidence={'backend':'codex-exec','quality_policy':cp.QUALITY_POLICY,
                'generation':cp.read(root/'generation.exec-proof.json'),'review':cp.read(root/'review.exec-proof.json'),
                'learner':{'result':learner_fixture(),'proof':cp.read(root/'learner.exec-proof.json')}}
            with self.assertRaisesRegex(ValueError,'hidden data'):
                cp.accepted_record(source,candidate_fixture(),review_fixture(),evidence)

    def test_failed_learner_review_stops_before_answer_key_audit(self):
        doc=source_fixture(); source=cp.task_source(doc,doc['tasks'][0])
        rejected={'approved':False,'checks':[{'index':1,'answer':'Missing necessary context.',
            'approved':False,'issues':['Unseen earlier exercise is required.'],'rationale':'Cannot answer from this surface.'}]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); client=FakeClient(root/'transport',[candidate_fixture(),rejected])
            with patch.object(runner,'make_client',return_value=client):
                result=runner.run_task(source,root/'run',root/'library',None,1,backend='codex-exec')
            self.assertEqual(result['status'],'rejected'); self.assertEqual(len(client.calls),2)
            self.assertFalse((root/'run/new-context/attempt-01/review.started.json').exists())

    def test_invalid_source_audit_retries_only_that_role(self):
        doc=source_fixture(); source=cp.task_source(doc,doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); client=FakeClient(root/'transport',[
                candidate_fixture(),learner_fixture(),{'approved':True,'checks':[]},review_fixture()])
            with patch.object(runner,'make_client',return_value=client):
                result=runner.run_task(source,root/'run',root/'library',None,2,backend='codex-exec')
            self.assertEqual(result['status'],'accepted'); self.assertEqual(len(client.calls),4)
            self.assertFalse((root/'run/new-context/attempt-02/generation.started.json').exists())
            self.assertFalse((root/'run/new-context/attempt-02/learner.started.json').exists())

    def test_unknown_learner_outcome_stops_without_another_request(self):
        doc=source_fixture(); source=cp.task_source(doc,doc['tasks'][0])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); client=FakeClient(root/'transport',[candidate_fixture(),TimeoutError('No terminal result')])
            with patch.object(runner,'make_client',return_value=client):
                result=runner.run_task(source,root/'run',root/'library',None,3,backend='codex-exec')
            self.assertEqual(result['status'],'outcome_unknown'); self.assertEqual(len(client.calls),2)
            self.assertFalse((root/'run/new-context/attempt-02').exists())

    def test_unknown_outcome_stops_without_another_model_request(self):
        doc = source_fixture(); source = cp.task_source(doc, doc['tasks'][0])
        class InterruptedClient:
            model = 'configured-codex-model'
            def complete_limited(self, *args, **kwargs):
                raise TimeoutError('No terminal response')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(runner, 'make_client', return_value=InterruptedClient()) as make:
                result = runner.run_task(source, root, root / 'library', None, 3, backend='codex-exec')
            self.assertEqual(result['status'], 'outcome_unknown')
            self.assertEqual(make.call_count, 1)
            self.assertFalse((root / 'new-context/attempt-02').exists())

    def test_prepare_without_explicit_runs_preserves_legacy_provided_card_audit(self):
        doc=source_fixture(); doc['tasks'][0]['provided_cards']=candidate_fixture()['cards']
        source=cp.task_source(doc,doc['tasks'][0])
        record=cp.accepted_record(source,candidate_fixture(),review_fixture(),{'run':'legacy-api'})
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            cp.save(root/'library'/(record['record_sha256']+'.json'),record)
            cp.save(root/'run/new-context/result.json',{'status':'accepted','source':source,
                'source_sha256':cp.digest(source),'candidate':candidate_fixture(),'review':review_fixture(),
                'record_sha256':record['record_sha256']})
            cp.save(root/'tasks.json',doc)
            cp.save(root/'source.json',{'schema':2,'topics':[{'id':'context-test','sections':[{
                'memory_points':[{'id':'request'}],
                'flashcards':[{'id':'seed',**candidate_fixture()['cards'][0]}]}]}]})
            args=Namespace(source=root/'source.json',tasks=root/'tasks.json',runs=[root/'run'],
                library=root/'library',output=root/'prepared',compile=False)
            with patch.object(runner,'make_client',side_effect=AssertionError('Offline preparation')):
                runner.assemble(args)
            self.assertTrue((args.output/'structured-topics.json').exists())
            self.assertFalse((args.output/'explicit-audits.json').exists())


if __name__ == '__main__':
    unittest.main()
