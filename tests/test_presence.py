"""Online time is a union of foreground intervals, never answer duration."""
from datetime import datetime, timedelta, timezone
import json
import unittest

from tests import test_admin_students as fixtures


class PresenceTests(unittest.TestCase):
    setUp = fixtures.AdminStudentsTests.setUp
    identity = fixtures.AdminStudentsTests.identity
    snapshot = fixtures.AdminStudentsTests.snapshot
    detail = fixtures.AdminStudentsTests.detail

    def pulse(self, seq, stream='a' * 32, visible=True, topics=None, headers=None):
        return self.client.post('/api/presence', headers=headers or self.headers, json={
            'stream_id': stream, 'sequence': seq, 'visible': visible,
            'topic_ids': topics if topics is not None else [self.course['id']]})

    def report(self):
        response = self.detail()
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['presence']

    def test_foreground_overlap_duplicate_and_readonly_learning(self):
        before = {k: v for k, v in self.snapshot().items() if not k.startswith('presence_')}
        services = list(self.app.state.learning_services)
        self.assertEqual(self.report()['online_ms'], 0)
        self.assertEqual(self.pulse(1).status_code, 200)
        self.time += timedelta(seconds=10)
        self.pulse(1, stream='b' * 32)
        self.time += timedelta(seconds=20)
        self.pulse(2)
        self.time += timedelta(seconds=10)
        self.pulse(2, stream='b' * 32)
        self.assertEqual(self.report()['online_ms'], 40000)  # 0–30 union 10–40
        frozen = self.snapshot()
        self.assertFalse(self.pulse(1).json()['recorded'])
        self.assertEqual(self.snapshot(), frozen)
        data = self.report()
        self.assertEqual([x['topic_id'] for x in data['topics']], [self.course['id']])
        self.assertEqual(self.client.get('/api/student-report', headers=self.headers).json()['presence'], data)
        self.assertEqual(self.snapshot(), frozen)
        self.assertEqual({k: v for k, v in self.snapshot().items() if not k.startswith('presence_')}, before)
        self.assertEqual(list(self.app.state.learning_services), services)

    def test_background_disconnect_resume_and_navigation(self):
        # This case measures one day's sessions. A real start near Beijing
        # midnight would clip earlier intervals; midnight has a separate case.
        self.time = self.time.replace(hour=4, minute=0, second=0, microsecond=0)
        self.pulse(1)
        self.time += timedelta(seconds=12)
        self.pulse(2, visible=False)
        self.time += timedelta(minutes=10)
        self.pulse(3, topics=['atoms'])
        self.time += timedelta(seconds=30)
        self.pulse(4, topics=['atoms'])
        self.time += timedelta(minutes=10)
        self.pulse(5, topics=[])
        self.time += timedelta(seconds=30)
        self.pulse(6, topics=[])
        self.assertEqual(self.report()['online_ms'], 72000)
        self.assertEqual({x['topic_id'] for x in self.report()['topics']}, {self.course['id'], 'atoms'})

    def test_midnight_uses_beijing_time_and_keeps_visits_separate(self):
        self.time = datetime(2026, 10, 5, 15, 59, 50, tzinfo=timezone.utc)
        self.pulse(1)
        self.time += timedelta(seconds=20)
        self.pulse(2)
        data = self.report()
        self.assertEqual(data['date'], '2026-10-06')
        self.assertEqual(data['online_ms'], 10000)
        self.assertEqual(data['topics'][0]['first_at'], self.time.isoformat())
        self.time += timedelta(days=1)
        data = self.report()
        self.assertEqual(data['online_ms'], 0)
        self.assertEqual(data['topics'], [])

    def test_identity_roles_permissions_validation_and_test_visibility(self):
        before = self.snapshot()
        guest = self.store.create_guest()['identity_token']
        for headers in (self.admin_headers, self.normal_headers, {**self.headers, 'X-Learning-Identity': guest}):
            response = self.pulse(1, headers=headers)
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.pulse(1, headers={'Origin': 'https://site.test'}).status_code, 401)
        self.assertEqual(self.pulse(0).status_code, 422)
        self.assertEqual(self.pulse(1, stream='bad').status_code, 422)
        self.assertEqual(self.pulse(1, topics=['unknown-topic']).status_code, 404)
        self.store.set_permissions(self.student, topic_ids=['atoms'])
        frozen = self.snapshot()
        self.assertEqual(self.pulse(1).status_code, 403)
        self.assertEqual(self.snapshot(), frozen)
        self.assertEqual(self.pulse(1, topics=['atoms']).status_code, 200)
        self.assertEqual(len(self.report()['topics']), 1)
        # A later classification as test material must also hide old visit rows.
        policy = self.store.test_materials
        policy.topics.add('atoms')
        policy.permission_ids.add('atoms')
        self.assertEqual(self.report()['topics'], [])
        self.assertEqual(before['topics'], self.snapshot()['topics'])


if __name__ == '__main__':
    unittest.main()
