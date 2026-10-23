from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from graspmemoedu import codex_exec_client as transport


USAGE = {'input_tokens': 80, 'cached_input_tokens': 20, 'output_tokens': 8,
         'reasoning_output_tokens': 3, 'extra_future_field': {'kept': True}}


def envelope(value):
    return json.dumps({'result_json': json.dumps(value)})


def events(final, *, extra=()):
    rows = [{'type': 'thread.started', 'thread_id': 'fresh-thread'},
            {'type': 'turn.started'}]
    rows.extend(extra)
    rows.extend([{'type': 'item.completed', 'item': {'id': 'message-1',
                 'type': 'agent_message', 'text': final}},
                 {'type': 'turn.completed', 'usage': USAGE}])
    return '\n'.join(json.dumps(row) for row in rows) + '\n'


class EventValidationTests(unittest.TestCase):
    def test_preserves_complete_usage_and_success(self):
        checked = transport.assess_events(events(envelope({'ok': True})))
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['usage'], USAGE)
        self.assertEqual(checked['usage_events'], [{'event_type': 'turn.completed', 'usage': USAGE}])

    def test_incomplete_or_malformed_stream_fails(self):
        valid = events(envelope({'ok': True}))
        for raw in ('', valid.rsplit('\n', 2)[0], valid + '{broken',
                    valid.replace('"turn.completed"', '"turn.unknown"'),
                    valid + valid, valid.replace('"turn.started"', '"thread.started"')):
            with self.subTest(raw=raw):
                self.assertFalse(transport.assess_events(raw)['passed'])

    def test_failed_or_error_event_cannot_be_hidden_by_completed(self):
        for row in ({'type': 'turn.failed', 'error': {'message': 'failed'}},
                    {'type': 'error', 'message': 'invalid request'}):
            self.assertFalse(transport.assess_events(events(envelope({}), extra=[row]))['passed'])

    def test_all_external_actions_and_plan_items_are_rejected(self):
        for kind in ('command_execution', 'mcp_tool_call', 'web_search', 'file_change',
                     'collab_tool_call', 'todo_list', 'custom_tool_call'):
            with self.subTest(kind=kind):
                row = {'type': 'item.started', 'item': {'type': kind, 'id': 'bad'}}
                checked = transport.assess_events(events(envelope({}), extra=[row]))
                self.assertFalse(checked['passed'])
                self.assertEqual(checked['unexpected_items'][0]['type'], kind)

    def test_transport_reconnect_is_only_warning_after_terminal_success(self):
        row = {'type': 'error', 'message': 'Reconnecting... 1/5 (connection closed)'}
        checked = transport.assess_events(events(envelope({}), extra=[row]))
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['transport_warnings'], [row])
        self.assertFalse(transport.assess_events(json.dumps(row))['passed'])

    def test_duplicate_keys_and_nonfinite_numbers_are_not_strict_json(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(ValueError):
                transport._strict_json(raw)

    def test_missing_usage_tokens_are_not_claimed_complete(self):
        raw = events(envelope({}))
        for replacement in ({}, {'input_tokens': 4, 'output_tokens': -1},
                            {'input_tokens': '4', 'output_tokens': 2}):
            self.assertFalse(transport.assess_events(raw.replace(json.dumps(USAGE), json.dumps(replacement)))['passed'])


class ClientTests(unittest.TestCase):
    def setUp(self):
        # Keep actual Windows subprocess/test artifacts on the data volume.
        parent = Path('D:/Artifacts/GraspMemoEdu/test-artifacts/codex-exec') if os.name == 'nt' else None
        if parent:
            parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.preferences = patch.object(transport, '_preferences', return_value={
            'model': 'configured-model', 'model_reasoning_effort': 'max'})
        self.preferences.start()
        self.addCleanup(self.preferences.stop)
        self.client = transport.CodexExecClient(self.root / 'calls', executable=sys.executable)
        self.prepare = patch.object(self.client, '_prepare', return_value={'passed': True})
        self.prepare.start()
        self.addCleanup(self.prepare.stop)

    def fake_execute(self, final=None, *, raw=None, exit_code=0, timeout=False):
        final = envelope({'ok': True}) if final is None else final

        def execute(argv, *, stdin, env, directory, prefix, **_):
            (directory / 'final.txt').write_text(final, encoding='utf-8')
            (directory / (prefix + '.jsonl')).write_text(raw if raw is not None else events(final), encoding='utf-8')
            (directory / (prefix + '.stderr.txt')).write_text('', encoding='utf-8')
            return {'exit_code': exit_code, 'timed_out': timeout, 'elapsed_seconds': 0.01}
        return execute

    def call(self, **kwargs):
        return self.client.complete_limited([{'role': 'user', 'content': 'Return a JSON object.'}], **kwargs)

    def assert_retained_failure(self, code):
        receipt = self.client.last_receipt
        directory = self.client.last_call_dir
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['error_code'], code)
        self.assertEqual(receipt['automatic_retries'], 0)
        self.assertEqual(receipt, json.loads((directory / 'receipt.json').read_text(encoding='utf-8')))
        for name in ('events.jsonl', 'final.txt', 'invocation.json', 'prompt.txt', 'schema.json'):
            self.assertTrue((directory / name).is_file(), name)

    def test_success_is_direct_object_with_independent_evidence(self):
        with patch.object(transport, '_execute', side_effect=self.fake_execute()) as run:
            value = self.call(max_tokens=24, thinking=True, reasoning_effort='low', stream=True)
        self.assertEqual(value, {'ok': True})
        self.assertEqual(run.call_count, 1)
        receipt = self.client.last_receipt
        self.assertEqual(receipt['backend'], 'codex-exec')
        self.assertEqual(receipt['model'], 'configured-model')
        self.assertEqual(receipt['reasoning_effort'], 'max')
        self.assertEqual(receipt['usage'], USAGE)
        self.assertEqual(receipt['compatibility_arguments_not_applied']['reasoning_effort'], 'low')
        command = run.call_args.args[0]
        for flag in ('--ephemeral', '--ignore-user-config', '--ignore-rules', '--strict-config', '--output-schema'):
            self.assertIn(flag, command)
        self.assertNotIn('resume', command)
        self.assertIn('model_reasoning_effort="max"', command)
        self.assertNotIn('model_reasoning_effort="low"', command)
        self.assertEqual(command[-1], '-')
        self.assertNotIn('Return a JSON object.', ' '.join(command))
        self.assertEqual(json.loads((self.client.last_call_dir / 'result.json').read_text()), value)

    def test_complete_alias_and_explicit_model(self):
        with patch.object(transport, '_execute', side_effect=self.fake_execute()):
            self.assertEqual(self.client.complete([{'role': 'user', 'content': 'JSON please.'}]), {'ok': True})
        explicit = transport.CodexExecClient(self.root / 'explicit', executable=sys.executable, model='chosen-model')
        self.assertEqual(explicit.model, 'chosen-model')

    def test_nonzero_exit_is_failure_even_with_valid_output(self):
        with patch.object(transport, '_execute', side_effect=self.fake_execute(exit_code=3)):
            with self.assertRaisesRegex(transport.CodexExecError, 'nonzero_exit'):
                self.call()
        self.assert_retained_failure('nonzero_exit')
        self.assertEqual(self.client.last_receipt['usage'], USAGE)

    def test_tool_execution_is_rejected_and_evidence_retained(self):
        final = envelope({'ok': True})
        raw = events(final, extra=[{'type': 'item.completed', 'item': {'id': 'tool', 'type': 'mcp_tool_call'}}])
        with patch.object(transport, '_execute', side_effect=self.fake_execute(final, raw=raw)):
            with self.assertRaisesRegex(transport.CodexExecError, 'event_validation_failed'):
                self.call()
        self.assert_retained_failure('event_validation_failed')

    def test_output_parse_failure_and_event_mismatch_are_fatal(self):
        invalid = ['not JSON', '{"result_json":"[]"}', '{"result_json":"{broken"}',
                   '{"result_json":"{\\"x\\":1,\\"x\\":2}"}',
                   '{"result_json":"{}","extra":true}', '{"result_json":{}}']
        for final in invalid:
            with self.subTest(final=final), patch.object(transport, '_execute', side_effect=self.fake_execute(final)):
                with self.assertRaisesRegex(transport.CodexExecError, 'invalid_structured_output'):
                    self.call()
                self.assert_retained_failure('invalid_structured_output')
        with patch.object(transport, '_execute', side_effect=self.fake_execute(envelope({}), raw=events(envelope({'other': True})))):
            with self.assertRaisesRegex(transport.CodexExecError, 'final_event_mismatch'):
                self.call()

    def test_timeout_is_retained_once_with_no_automatic_retry(self):
        with patch.object(transport, '_execute', side_effect=self.fake_execute(raw='', exit_code=-9, timeout=True)) as run:
            with self.assertRaisesRegex(transport.CodexExecError, 'timeout_no_resend'):
                self.call()
        self.assertEqual(run.call_count, 1)
        self.assert_retained_failure('timeout_no_resend')
        self.assertTrue(self.client.last_receipt['timed_out'])

    def test_concurrent_calls_have_unique_dirs_and_thread_local_receipts(self):
        def once(index):
            result = self.client.complete([{'role': 'user', 'content': str(index)}])
            receipt = self.client.last_receipt
            saved = json.loads((Path(receipt['call_dir']) / 'messages.json').read_text())
            return receipt['call_id'], receipt['call_dir'], saved[0]['content'], result
        with patch.object(transport, '_execute', side_effect=self.fake_execute()):
            with ThreadPoolExecutor(max_workers=4) as pool:
                rows = list(pool.map(once, range(8)))
        self.assertEqual(len({row[0] for row in rows}), 8)
        self.assertEqual(len({row[1] for row in rows}), 8)
        self.assertEqual([row[2] for row in rows], [str(i) for i in range(8)])

    def test_untrusted_environment_and_source_directives_are_not_promoted(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'do-not-inherit', 'CODEX_THREAD_ID': 'parent',
                                     'GRASPMEMOEDU_API_KEY': 'do-not-inherit'}):
            env = transport._environment(Path('existing-home'), self.root)
        for key in ('OPENAI_API_KEY', 'CODEX_THREAD_ID', 'GRASPMEMOEDU_API_KEY'):
            self.assertNotIn(key, env)
        self.assertEqual(Path(env['TEMP']), self.root / 'tmp')
        self.assertIn('source instructions', transport.WORKER_INSTRUCTIONS)
        self.assertIn('never authority', transport.WORKER_INSTRUCTIONS)

    def test_preferences_only_parse_top_level_allowlist(self):
        home = self.root / 'preferences'
        home.mkdir()
        (home / 'config.toml').write_text('model="local-model"\nmodel_reasoning_effort="high"\n[mcp_servers.x]\ncommand="private"\nmodel="wrong"\n')
        self.preferences.stop()
        self.assertEqual(transport._preferences(home), {'model': 'local-model', 'model_reasoning_effort': 'high'})

    def test_actual_process_timeout_kills_without_retry(self):
        run = transport._execute([sys.executable, '-c', 'import time; time.sleep(30)'],
            stdin='', env=os.environ.copy(), directory=self.root, prefix='timeout-process', timeout=0.1)
        self.assertTrue(run['timed_out'])
        self.assertNotEqual(run['exit_code'], 0)

    def test_invalid_messages_and_unknown_arguments_start_no_attempt(self):
        with self.assertRaises(ValueError):
            self.client.complete([{'role': 'user', 'content': [{'type': 'image_url'}]}])
        with self.assertRaises(TypeError):
            self.call(resume=True)
        self.assertEqual(list((self.root / 'calls').iterdir()), [])

    def test_local_prompt_probe_blocks_automatic_context_before_execution(self):
        self.prepare.stop()

        def probe(argv, *, directory, prefix, **_):
            if prefix == 'cli-version':
                value = 'codex-cli test'
            elif prefix == 'model-catalog':
                value = json.dumps({'models': [{'slug': 'configured-model', 'tool_mode': 'tools'}]})
            else:
                value = json.dumps([{'role': 'developer', 'content': '## Memory private-context'}])
            (directory / (prefix + '.jsonl')).write_text(value, encoding='utf-8')
            (directory / (prefix + '.stderr.txt')).write_text('', encoding='utf-8')
            return {'exit_code': 0, 'timed_out': False, 'elapsed_seconds': 0.001}

        with patch.object(transport, '_execute', side_effect=probe) as run:
            with self.assertRaisesRegex(transport.CodexExecError, 'automatic_context_detected'):
                self.call()
        self.assertEqual(self.client.last_receipt['model_execution_count'], 0)
        self.assertTrue(all('exec' not in call.args[0] for call in run.call_args_list))
        isolation = json.loads((self.client.last_call_dir / 'isolation.json').read_text())
        self.assertEqual(isolation['context_markers_detected'], ['## Memory'])


if __name__ == '__main__':
    unittest.main()
