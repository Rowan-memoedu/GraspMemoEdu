"""Explicit source-only recovery, with no real model calls in these fixtures."""
from argparse import Namespace
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from graspmemoedu import context_production as cp
from graspmemoedu.codex_exec_client import CodexExecError, assess_events
from scripts import produce_context as runner
from test_context_exec_routing import FakeClient, source_fixture, candidate_fixture, learner_fixture, review_fixture


def overwrite(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


class CapacityClient(FakeClient):
    def complete_limited(self, messages, **kwargs):
        if len(self.calls) != 2:
            return super().complete_limited(messages, **kwargs)
        self.calls.append(deepcopy(messages))
        directory = self.root / ('fixture-capacity-' + cp.digest(str(self.root))[:10])
        directory.mkdir(parents=True)
        cp.save(directory/'messages.json', messages)
        (directory/'prompt.txt').write_text('UNIT FIXTURE ONLY\n'+json.dumps(messages,ensure_ascii=False),encoding='utf-8')
        (directory/'final.txt').write_bytes(b'')
        rows = [{'type':'thread.started','thread_id':'unit-fixture'}, {'type':'turn.started'},
                {'type':'error','message':'Selected model is at capacity.'},
                {'type':'turn.failed','error':{'message':'Selected model is at capacity.'}}]
        raw = '\n'.join(json.dumps(row) for row in rows)+'\n'
        (directory/'events.jsonl').write_text(raw,encoding='utf-8')
        checked=assess_events(raw); checked.pop('final_message')
        self.last_receipt={'schema':1,'backend':'codex-exec','call_id':directory.name,
            'call_dir':str(directory),'model':self.model,'reasoning_effort':self.reasoning_effort,
            'status':'failed','exit_code':1,'model_execution_count':1,'automatic_retries':0,
            'timed_out':False,'error_code':'nonzero_exit','isolation':{'passed':True},
            'started_at':'2026-10-23T00:00:00+00:00','finished_at':'2026-10-23T00:00:01+00:00',
            'usage':None,'usage_events':[],'event_check':checked,
            'input_sha256':runner.file_sha(directory/'prompt.txt')}
        cp.save(directory/'receipt.json',self.last_receipt)
        raise CodexExecError('nonzero_exit',directory)


def args_for(root, document=None):
    if not (root/'input.json').exists(): cp.save(root/'input.json',document or source_fixture())
    return Namespace(input=root/'input.json',library=root/'library',run_root=root/'old',
                     workers=1,attempts=1,only=None,candidate_runs=[],backend='codex-exec',
                     reuse_learner_runs=[])


def failed_fixture(root):
    args=args_for(root)
    clients=[]
    def factory(config,work,backend,options):
        client=CapacityClient(work/'codex-exec')
        clients.append(client); return client
    with patch.object(runner,'make_client',side_effect=factory):
        assert runner.run(args)==2
    source=cp.task_source(source_fixture(),source_fixture()['tasks'][0])
    work=args.run_root/source['task']['id']/'attempt-01'
    return args,source,work,Path(clients[0].last_receipt['call_dir'])


class ContextSourceRecoveryTests(unittest.TestCase):
    def test_resume_calls_only_source_and_preserves_old_files_and_role_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); args,source,work,failed=failed_fixture(root)
            old={str(p.relative_to(args.run_root)):p.read_bytes() for p in args.run_root.rglob('*') if p.is_file()}
            retained=runner.retained_learner(source,[args.run_root])
            args.reuse_learner_runs=[args.run_root]; args.run_root=root/'resume'
            clients=[]
            def factory(config,work,backend,options):
                client=FakeClient(work/'codex-exec',[review_fixture()]); clients.append(client); return client
            with patch.object(runner,'make_client',side_effect=factory):
                self.assertEqual(runner.run(args),0)
            self.assertEqual(len(clients),1); self.assertEqual(len(clients[0].calls),1)
            self.assertEqual(clients[0].calls[0][0]['content'],cp.REVIEW)
            new=args.run_root/source['task']['id']/'attempt-01'
            for name,data in retained['files'].items(): self.assertEqual((new/name).read_bytes(),data)
            for name,data in old.items(): self.assertEqual((root/'old'/name).read_bytes(),data)
            result=cp.read(args.run_root/source['task']['id']/'result.json')
            record=cp.read(args.library/(result['record_sha256']+'.json')); cp.require_quality(record)
            self.assertEqual(record['candidate'],candidate_fixture())
            self.assertEqual(record['evidence']['learner_reuse'],retained['manifest'])
            with patch.object(runner,'make_client',side_effect=AssertionError('No second request')):
                self.assertEqual(runner.run(args),0)

    def test_recovery_rejects_unknown_partial_running_and_rejected_source_states_without_calls(self):
        mutations={
            'timeout':lambda w,d,r:r.update(timed_out=True),
            'running':lambda w,d,r:r.update(status='running',finished_at=None),
            'unknown_exit':lambda w,d,r:r.update(exit_code=None),
            'successful_exit':lambda w,d,r:r.update(exit_code=0),
            'retry':lambda w,d,r:r.update(automatic_retries=1),
            'usage':lambda w,d,r:r.update(usage={'input_tokens':1,'output_tokens':0}),
            'usage_event':lambda w,d,r:r.update(usage_events=[{'event_type':'turn.completed'}]),
            'partial_final':lambda w,d,r:(d/'final.txt').write_text('{',encoding='utf-8'),
            'result_output':lambda w,d,r:cp.save(d/'result.json',{'approved':False}),
            'review_response':lambda w,d,r:cp.save(w/'review.response.json',review_fixture()),
            'successful_rejection':lambda w,d,r:overwrite(w.parent/'result.json',
                {**cp.read(w.parent/'result.json'),'status':'rejected'}),
            'truncated_stream':lambda w,d,r:(d/'events.jsonl').write_text('{',encoding='utf-8'),
            'missing_terminal':lambda w,d,r:(d/'events.jsonl').write_text(
                '{"type":"thread.started"}\n{"type":"turn.started"}\n',encoding='utf-8'),
            'another_unfinished_call':lambda w,d,r:(w/'codex-exec'/'unfinished').mkdir(),
            'later_attempt':lambda w,d,r:(w.parent/'attempt-02').mkdir(),
        }
        for name,change in mutations.items():
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);args,source,work,failed=failed_fixture(root)
                receipt=cp.read(failed/'receipt.json'); change(work,failed,receipt);overwrite(failed/'receipt.json',receipt)
                args.reuse_learner_runs=[args.run_root];args.run_root=root/'resume'
                with patch.object(runner,'make_client',side_effect=AssertionError('Must authenticate before invocation')):
                    with self.assertRaises((ValueError,FileNotFoundError)):
                        runner.run(args)
                self.assertFalse(args.run_root.exists())

    def test_source_candidate_prompt_proof_and_public_view_are_exactly_bound(self):
        for name in ('source','candidate','generation_proof','learner_result','learner_prompt','public_view','source_prompt'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);args,source,work,failed=failed_fixture(root)
                attempt=cp.read(work/'result.json')
                if name=='source': source['task']['requirements']+=' Different task.'
                elif name=='candidate': attempt['candidate']['cards'][0]['explanation']+=' Changed.'
                elif name=='generation_proof': attempt['generation_evidence']['response_sha256']='0'*64
                elif name=='learner_result':
                    attempt['learner_evidence']['result']['checks'][0]['rationale']='Changed reasoning.'
                elif name in ('learner_prompt','public_view'):
                    proof=attempt['learner_evidence']['proof'];directory=Path(proof['receipt']['call_dir'])
                    messages=cp.read(directory/'messages.json')
                    if name=='learner_prompt':messages[0]['content']+=' '
                    else: messages[1]['content']=json.dumps({'quality_policy':cp.QUALITY_POLICY,'learner_view':{'cards':[]}})
                    overwrite(directory/'messages.json',messages)
                elif name=='source_prompt':
                    messages=cp.read(failed/'messages.json');messages[0]['content']+=' '
                    overwrite(failed/'messages.json',messages)
                overwrite(work/'result.json',attempt)
                with self.assertRaises(ValueError): runner.retained_learner(source,[args.run_root])

    def test_real_content_rejection_after_recovery_generates_new_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args,source,work,failed=failed_fixture(root)
            args.reuse_learner_runs=[args.run_root];args.run_root=root/'resume';args.attempts=2
            rejected=review_fixture();rejected['approved']=False
            rejected['checks'][0].update(approved=False,issues=['The context does not support the keyed answer.'])
            clients=[]
            def factory(config,work,backend,options):
                client=FakeClient(work/'codex-exec',[rejected] if not clients else None)
                clients.append(client);return client
            with patch.object(runner,'make_client',side_effect=factory):self.assertEqual(runner.run(args),0)
            self.assertEqual([len(c.calls) for c in clients],[1,3])
            self.assertEqual(clients[1].calls[0][0]['content'],cp.GENERATION)
            self.assertIn('retry_feedback',json.loads(clients[1].calls[0][1]['content']))

    def test_another_empty_source_failure_stops_and_requires_another_explicit_new_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args,source,work,failed=failed_fixture(root)
            args.reuse_learner_runs=[args.run_root];args.run_root=root/'resume';args.attempts=3
            clients=[]
            def factory(config,work,backend,options):
                client=CapacityClient(work/'codex-exec')
                client.calls=[[],[]]  # This fixture fails its next call; these are not execution receipts.
                clients.append(client);return client
            with patch.object(runner,'make_client',side_effect=factory):self.assertEqual(runner.run(args),2)
            self.assertEqual(len(clients),1);self.assertEqual(len(clients[0].calls),3)
            self.assertFalse((args.run_root/source['task']['id']/'attempt-02').exists())
            retained=runner.retained_learner(source,[args.run_root])
            self.assertEqual(retained['candidate'],candidate_fixture())
            self.assertNotEqual(retained['manifest']['failed_source']['call_id'],failed.name)

    def test_exact_approved_legacy_blind_prompt_can_be_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(cp,'LEARNER_REVIEW',cp.LEGACY_LEARNER_REVIEW):
                root=Path(temp);args,source,work,failed=failed_fixture(root)
            retained=runner.retained_learner(source,[args.run_root])
            messages=cp.validate_exec_proof(retained['learner_evidence']['proof'],retained['learner_evidence']['result'])
            self.assertEqual(messages[0]['content'],cp.LEGACY_LEARNER_REVIEW)

    def test_same_root_api_provided_cards_or_combined_reuse_are_rejected_before_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args,source,work,failed=failed_fixture(root)
            for backend,candidates,newroot in [('api',[],root/'new'),('codex-exec',[root/'old'],root/'new'),
                                                ('codex-exec',[],root/'old')]:
                with self.subTest(backend=backend,candidates=candidates,newroot=newroot):
                    with patch.object(runner,'make_client',side_effect=AssertionError('No invocation')):
                        with self.assertRaises(ValueError):
                            runner.run_task(source,newroot,args.library,None,1,candidates,backend,
                                            reuse_learner_runs=[root/'old'])
            supplied=deepcopy(source);supplied['task']['provided_cards']=candidate_fixture()['cards']
            with self.assertRaises(ValueError):runner.retained_learner(supplied,[root/'old'])


if __name__=='__main__':unittest.main()
