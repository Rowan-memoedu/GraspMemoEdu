"""Concurrent generation keeps exact request bounds, original caches and stops."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from threading import Barrier, Event, Lock
import unittest
from unittest.mock import Mock, patch

from graspmemoedu.conversion_run import ConversionRun, ConversionStopped, run_batches
from graspmemoedu.physics_question_conversion import run_conversion
from graspmemoedu.physics_question_conversion import PhysicsAdapter, production_plan
from graspmemoedu.question_conversion import generate_batch
from tests.fixtures import temporary_test_directory
from tests.test_conversion_budget import raw_result
from tests.test_physics_conversion import course_fixture


def question_payload(messages):
    return json.loads(messages[1]['content'][0]['text'].split('输入数据：\n')[1])['questions'][0]


def accepted_response(source):
    return raw_result({'questions': [{'id': source['id'], 'distractors': [
        {'value': [{'expr': 'answer+' + str(n)}], 'reason': 'Synthetic'}
        for n in (1, 2, 3, 4, 5)]}]})


class ConversionParallelTests(unittest.TestCase):
    def test_four_requests_overlap_and_cache_reuse_sends_nothing_again(self):
        courses = [course_fixture('parallel-' + str(i)) for i in range(4)]
        rendezvous, lock = Barrier(4), Lock()
        active, peak, seen = 0, 0, []
        client = Mock(model='deepseek-flash')

        def generate(messages, **kwargs):
            nonlocal active, peak
            source = question_payload(messages)
            with lock:
                active += 1
                peak = max(peak, active)
                seen.append(source['id'])
            rendezvous.wait(timeout=15)
            response = accepted_response(source)
            with lock:
                active -= 1
            return response

        client.complete_limited.side_effect = generate
        with temporary_test_directory() as directory:
            result = run_conversion(courses, directory, client_factory=lambda: client, batch_size=1, concurrency=4)
            self.assertEqual(result['status'], 'sample_review_required')
            self.assertEqual(peak, 4)
            self.assertEqual(len(set(seen)), 4)
            self.assertEqual(result['usage']['requests'], 4)
            self.assertEqual(result['usage']['completed'], 4)
            run_conversion(courses, directory, client_factory=lambda: client, batch_size=1, concurrency=4)
            self.assertEqual(client.complete_limited.call_count, 4)
            with self.assertRaises(ConversionStopped):
                run_conversion(courses, directory, client_factory=lambda: client, batch_size=1, concurrency=2)

    def test_failure_stops_dispatch_but_inflight_success_is_saved(self):
        for failure in ('truncated', 'unknown'):
            with self.subTest(failure=failure), temporary_test_directory() as directory:
                courses = [course_fixture('failure-' + str(i)) for i in range(4)]
                rendezvous, stopped = Barrier(2), Event()
                original_stop = ConversionRun.stop
                client = Mock(model='deepseek-flash')

                def record_stop(run, reason):
                    original_stop(run, reason)
                    stopped.set()

                def generate(messages, **kwargs):
                    source = question_payload(messages)
                    rendezvous.wait(timeout=15)
                    if source['topic_id'] == 'failure-0':
                        if failure == 'unknown':
                            raise ConnectionError('Synthetic interrupted response')
                        response = accepted_response(source)
                        response['choices'][0]['finish_reason'] = 'length'
                        return response
                    self.assertTrue(stopped.wait(timeout=15))
                    return accepted_response(source)

                client.complete_limited.side_effect = generate
                with patch.object(ConversionRun, 'stop', autospec=True, side_effect=record_stop):
                    with self.assertRaises((ConversionStopped, ConnectionError)):
                        run_conversion(courses, directory, client_factory=lambda: client, batch_size=1, concurrency=2)
                self.assertEqual(client.complete_limited.call_count, 2)
                saved = list((Path(directory) / 'validated-items').glob('*.json'))
                self.assertEqual(len(saved), 1)
                with closing(sqlite3.connect(Path(directory) / 'run.sqlite3')) as con:
                    statuses = [row[0] for row in con.execute('SELECT status FROM requests')]
                    self.assertTrue(con.execute('SELECT stopped FROM config WHERE id=1').fetchone()[0])
                self.assertEqual(statuses.count('complete'), 2 if failure == 'truncated' else 1)
                self.assertEqual(statuses.count('sent'), 0)
                factory = Mock(side_effect=AssertionError('Stopped run must not retry'))
                with self.assertRaises(ConversionStopped):
                    run_conversion(courses, directory, client_factory=factory, batch_size=1, concurrency=2)
                factory.assert_not_called()

    def test_ledger_capacity_duplicate_keys_and_uncertainty_remain_enforced(self):
        with temporary_test_directory() as directory:
            run = ConversionRun(Path(directory) / 'run.sqlite3', {'concurrency': 4})
            requests = [run.reserve(str(i), []) for i in range(4)]
            with self.assertRaises(ConversionStopped):
                run.reserve('overflow', [])
            with self.assertRaises(ConversionStopped):
                run.reserve('0', [])
            run.settle(requests[0], {'id': 'done'})
            extra = run.reserve('next', [])
            run.uncertain(requests[1], 'Synthetic')
            with self.assertRaises(ConversionStopped):
                run.reserve('after-unknown', [])
            for request in requests[2:] + [extra]:
                run.settle(request, {'id': 'inflight-finished'})
            self.assertEqual(run.summary()['completed'], 4)
            with self.assertRaises(ConversionStopped):
                run.assert_resumable()

    def test_invalid_concurrency_fails_before_any_provider_call(self):
        for concurrency in (0, 65, True, '4'):
            with self.subTest(concurrency=concurrency), temporary_test_directory() as directory:
                factory = Mock(side_effect=AssertionError('Invalid setting must not call a provider'))
                with self.assertRaises(ValueError):
                    run_conversion([course_fixture()], directory, client_factory=factory, concurrency=concurrency)
                factory.assert_not_called()
                with self.assertRaises(ValueError):
                    ConversionRun(Path(directory) / 'run.sqlite3', {'concurrency': concurrency})

    def test_32_requests_are_dispatched_without_a_hidden_four_request_ceiling(self):
        rendezvous, lock = Barrier(32), Lock()
        active, peak = 0, 0
        with temporary_test_directory() as directory:
            run = ConversionRun(Path(directory) / 'run.sqlite3', {'concurrency': 32})
            def action(item):
                nonlocal active, peak
                reservation = run.reserve(str(item), [])
                with lock:
                    active += 1
                    peak = max(peak, active)
                rendezvous.wait(timeout=20)
                run.settle(reservation, {'id': 'fixture-' + str(item)})
                with lock:
                    active -= 1
            run_batches(range(32), action, run)
            self.assertEqual(peak, 32)
            self.assertEqual(run.summary()['requests'], 32)
            self.assertEqual(run.summary()['completed'], 32)
            run.assert_resumable()

    def test_continuation_does_not_reset_an_existing_hard_error_repair(self):
        _, sources = production_plan([course_fixture('prior-hard-error')])
        source = sources[0]
        bad = {'distractors': [{'value': [{'expr': 'answer'}], 'reason': 'Synthetic'}] * 5}
        client = Mock(model='deepseek-flash')
        client.complete_limited.return_value = raw_result({'questions': [{'id': source['id'], **bad}]})
        with temporary_test_directory() as directory:
            root = Path(directory)
            run = ConversionRun(root / 'run.sqlite3', {})
            with self.assertRaises(ConversionStopped):
                generate_batch(lambda: client, run, root / 'requests', sources, adapter=PhysicsAdapter,
                               initial_errors={source['id']: {'message': 'Equivalent to correct answer',
                                                             'previous_candidate': bad, 'failures': []}})
            self.assertEqual(client.complete_limited.call_count, 1)
            self.assertEqual(run.summary()['requests'], 1)
            request = json.loads(next((root / 'requests').glob('*.request.json')).read_text(encoding='utf-8'))
            self.assertTrue(request['repair_attempt'])
            self.assertIn('local_validation_error', question_payload(request['messages']))
