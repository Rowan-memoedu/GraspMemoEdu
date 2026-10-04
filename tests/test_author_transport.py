"""Offline proof of the transport retry boundary: never repeat a sent request."""
import http.client
import io
import json
import ssl
import unittest
import urllib.error
from unittest.mock import Mock, patch

from graspmemoedu.author_import import CompatibleClient, _PreconnectedHTTPSConnection, _RequestNotSent


class AuthorTransportTests(unittest.TestCase):
    def test_tls_failure_is_marked_unsent_before_application_request(self):
        connection = _PreconnectedHTTPSConnection('api.deepseek.com')
        with patch.object(connection, 'connect', side_effect=ssl.SSLEOFError('synthetic')):
            with patch.object(http.client.HTTPSConnection, 'request') as send:
                with self.assertRaises(_RequestNotSent):
                    connection.request('POST', '/chat/completions', b'synthetic')
                send.assert_not_called()

    def test_post_connect_send_failure_is_not_marked_unsent(self):
        connection = _PreconnectedHTTPSConnection('api.deepseek.com')
        with patch.object(connection, 'connect'):
            with patch.object(http.client.HTTPSConnection, 'request', side_effect=ssl.SSLEOFError('synthetic')):
                with self.assertRaises(ssl.SSLEOFError):
                    connection.request('POST', '/chat/completions', b'synthetic')

    def test_one_unsent_retry_reuses_the_identical_request_and_records_it(self):
        result = {'id': 'synthetic', 'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}
        opener = Mock()
        opener.open.side_effect = [urllib.error.URLError(_RequestNotSent('SSLEOFError')),
                                  io.BytesIO(json.dumps(result).encode())]
        client = CompatibleClient('https://api.deepseek.com', 'synthetic-key', 'deepseek-flash')
        with patch('urllib.request.build_opener', return_value=opener):
            response = client.complete([{'role': 'user', 'content': 'Synthetic'}])
        self.assertEqual(opener.open.call_count, 2)
        self.assertIs(opener.open.call_args_list[0].args[0], opener.open.call_args_list[1].args[0])
        self.assertEqual(response['_client_transport']['pre_request_connection_retries'], 1)

    def test_repeated_unsent_failure_stops_after_one_connection_retry(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError(_RequestNotSent('SSLEOFError'))
        client = CompatibleClient('https://api.deepseek.com', 'synthetic-key', 'deepseek-flash')
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(urllib.error.URLError):
                client.complete([{'role': 'user', 'content': 'Synthetic'}])
        self.assertEqual(opener.open.call_count, 2)

    def test_certificate_and_unclassified_errors_are_never_retried(self):
        for reason in (ssl.SSLCertVerificationError('synthetic'), ssl.SSLEOFError('synthetic'), TimeoutError()):
            opener = Mock()
            opener.open.side_effect = urllib.error.URLError(reason)
            client = CompatibleClient('https://api.deepseek.com', 'synthetic-key', 'deepseek-flash')
            with self.subTest(reason=type(reason).__name__), patch('urllib.request.build_opener', return_value=opener):
                with self.assertRaises(urllib.error.URLError):
                    client.complete([{'role': 'user', 'content': 'Synthetic'}])
                self.assertEqual(opener.open.call_count, 1)

    def test_incomplete_stream_is_never_retried(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'data: {"id":"synthetic","choices":[]}\n\n')
        client = CompatibleClient('https://api.deepseek.com', 'synthetic-key', 'deepseek-flash')
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ConnectionError):
                client.complete_limited([{'role': 'user', 'content': 'Synthetic'}], max_tokens=16384, thinking=True, stream=True)
        self.assertEqual(opener.open.call_count, 1)
