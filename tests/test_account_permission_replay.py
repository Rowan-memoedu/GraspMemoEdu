"""A bounded review-history authorization regression using only account fixtures."""

import unittest

from tests import test_accounts


class AccountPermissionReplayTests(unittest.TestCase):
    def test_old_submission_replay_respects_revoked_history_permission(self):
        fixture = test_accounts.AccountAcceptanceTests(
            'test_account_login_has_stable_identity_but_independent_device_tokens')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        account = fixture._account()
        token = fixture._login(account)['identity_token']
        fixture._action(token, 'choose', choice='mastered')
        payload = fixture._payload(token, answer='correct')
        submitted = fixture._request('POST', '/api/submit?topic_id=account-topic-a', token, payload)
        fixture._poll(token, submitted['submission_id'])
        fixture._action(token, 'continue')
        fixture.store.set_permissions(account['account']['learner_id'],
                                      features=['learn', 'submit_answer', 'pause_topic'])
        fixture._request('GET', '/api/submissions/' + submitted['submission_id'] + '?topic_id=account-topic-a',
                         token, status=403, code='feature_forbidden')
        before = fixture._state(token)
        replay = fixture.client.post('/api/submit?topic_id=account-topic-a', json=payload,
                                     headers=fixture._headers(token))
        self.assertEqual(fixture._state(token)['revision'], before['revision'])
        self.assertEqual(len(fixture.judge.calls), 1)
        self.assertEqual(replay.status_code, 403,
                         'POST replay returned a historical verdict although GET for the same submission is forbidden')
        self.assertEqual(replay.json()['detail']['code'], 'feature_forbidden')


if __name__ == '__main__':
    unittest.main()
