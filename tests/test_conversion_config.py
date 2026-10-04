"""Frozen provider settings reach the API without relying on its defaults."""
import json
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from graspmemoedu.author_import import CompatibleClient
from graspmemoedu.conversion_run import ConversionRun, ConversionStopped
from graspmemoedu.completion_stream import read_completion_stream
from graspmemoedu.question_conversion import bounded_call
from tests.fixtures import temporary_test_directory
from tests.test_conversion_budget import raw_result


class ConversionConfigTests(unittest.TestCase):
    @staticmethod
    def stream_data(finish='stop', done=True, usage_only=False):
        rows = [{'id': 'stream-fixture', 'choices': [{'index': 0, 'delta': {'reasoning_content': 'fixture'}}]},
                {'id': 'stream-fixture', 'choices': [{'index': 0, 'delta': {'content': '{"questions":'}}]},
                {'id': 'stream-fixture', 'choices': [{'index': 0, 'delta': {'content': '[]}'}, 'finish_reason': finish}]}]
        if usage_only:
            rows.append({'id': 'stream-fixture', 'choices': []})
        rows[-1]['usage'] = {'prompt_tokens': 5, 'completion_tokens': 6}
        text = ': keep-alive\n\n' + ''.join('data: ' + json.dumps(row) + '\n\n' for row in rows)
        return (text + ('data: [DONE]\n\n' if done else '')).encode()

    def test_stream_payload_usage_and_cache(self):
        for usage_only in (False, True):
            config = {'model': 'deepseek-flash', 'thinking': True, 'reasoning_effort': 'low',
                      'max_output_tokens': 16384, 'stream': True}
            opener = Mock()
            opener.open.return_value = BytesIO(self.stream_data(usage_only=usage_only))
            with temporary_test_directory() as directory:
                root = Path(directory)
                run = ConversionRun(root / 'run.sqlite3', {}, config)
                factory = lambda: CompatibleClient('https://api.deepseek.com', 'synthetic', 'deepseek-flash')
                with patch('graspmemoedu.author_import.urllib.request.build_opener', return_value=opener):
                    result, _ = bounded_call(factory, run, root / 'requests', [{'id': 'q'}])
                self.assertEqual(result, {'questions': []})
                payload = json.loads(opener.open.call_args.args[0].data)
                self.assertEqual(payload['stream_options'], {'include_usage': True})
                self.assertTrue(payload['stream'])
                self.assertEqual(run.summary()['output_tokens'], 6)

    def test_interrupted_stream_is_not_accepted_or_resent(self):
        with self.assertRaises(ConnectionError):
            read_completion_stream(BytesIO(self.stream_data(done=False)))
        self.assertEqual(read_completion_stream(BytesIO(self.stream_data(finish='length')))['choices'][0]['finish_reason'], 'length')
        client = Mock(model='deepseek-flash')
        client.complete_limited.side_effect = ConnectionError('stream interrupted')
        with temporary_test_directory() as directory:
            root = Path(directory)
            run = ConversionRun(root / 'run.sqlite3', {})
            with self.assertRaises(ConnectionError):
                bounded_call(lambda: client, run, root / 'requests', [{'id': 'q'}])
            with self.assertRaises(ConversionStopped):
                run.assert_resumable()
            self.assertEqual(client.complete_limited.call_count, 1)

    def test_explicit_effort_and_output_room_reach_provider_and_cache(self):
        config = {'model': 'deepseek-flash', 'thinking': True,
                  'max_output_tokens': 16384, 'reasoning_effort': 'low'}
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(raw_result({'questions': []})).encode()
        opener = Mock()
        opener.open.return_value = response
        with temporary_test_directory() as directory:
            root = Path(directory)
            run = ConversionRun(root / 'run.sqlite3', {'scope': 'fixture'}, config)
            factory = lambda: CompatibleClient('https://api.deepseek.com', 'synthetic-key', 'deepseek-flash')
            with patch('graspmemoedu.author_import.urllib.request.build_opener', return_value=opener):
                first = bounded_call(factory, run, root / 'requests', [{'id': 'q'}])
                self.assertEqual(first, bounded_call(factory, run, root / 'requests', [{'id': 'q'}]))
            self.assertEqual(opener.open.call_count, 1)
            payload = json.loads(opener.open.call_args.args[0].data)
            self.assertEqual(payload['reasoning_effort'], 'low')
            self.assertEqual(payload['max_tokens'], 16384)
            self.assertNotIn('temperature', payload)
            with self.assertRaises(ConversionStopped):
                ConversionRun(root / 'run.sqlite3', {'scope': 'fixture'}, dict(config, reasoning_effort='high'))

    def test_invalid_combinations_fail_before_network(self):
        client = CompatibleClient('https://api.deepseek.com', 'synthetic-key', 'deepseek-flash')
        for thinking, effort, cap in ((False, 'low', 8192), (False, None, 8193), (True, 'unknown', 8192), (True, 'low', 65537)):
            with temporary_test_directory() as directory:
                with self.assertRaises(ValueError):
                    ConversionRun(Path(directory) / 'run.sqlite3', {}, {
                        'model': 'deepseek-flash', 'thinking': thinking,
                        'reasoning_effort': effort, 'max_output_tokens': cap})
                with self.assertRaises(ValueError):
                    client.complete_limited([], thinking=thinking, reasoning_effort=effort, max_tokens=cap)

    def test_explicit_64k_stream_limit_preserves_cache_freeze_and_stop_rules(self):
        config = {'model': 'deepseek-flash', 'thinking': True, 'reasoning_effort': 'low',
                  'max_output_tokens': 65536, 'stream': True}
        for finish in ('stop', 'length'):
            with self.subTest(finish=finish), temporary_test_directory() as directory:
                root = Path(directory)
                run = ConversionRun(root / 'run.sqlite3', {'scope': 'independent-limit-canary'}, config)
                opener = Mock()
                opener.open.return_value = BytesIO(self.stream_data(finish=finish))
                factory = lambda: CompatibleClient('https://api.deepseek.com', 'synthetic', 'deepseek-flash')
                with patch('graspmemoedu.author_import.urllib.request.build_opener', return_value=opener):
                    if finish == 'stop':
                        result = bounded_call(factory, run, root / 'requests', [{'id': 'q'}])
                        self.assertEqual(result, bounded_call(factory, run, root / 'requests', [{'id': 'q'}]))
                    else:
                        with self.assertRaises(ConversionStopped):
                            bounded_call(factory, run, root / 'requests', [{'id': 'q'}])
                        with self.assertRaises(ConversionStopped):
                            run.assert_resumable()
                payload = json.loads(opener.open.call_args.args[0].data)
                self.assertEqual(payload['max_tokens'], 65536)
                self.assertEqual(payload['reasoning_effort'], 'low')
                self.assertEqual(payload['thinking'], {'type': 'enabled'})
                self.assertTrue(payload['stream'])
                self.assertEqual(opener.open.call_count, 1)
                with self.assertRaises(ConversionStopped):
                    ConversionRun(root / 'run.sqlite3', {'scope': 'independent-limit-canary'},
                                  dict(config, max_output_tokens=16384))
