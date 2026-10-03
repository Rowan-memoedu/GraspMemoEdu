"""Default account access grows with the catalog; explicit grants never do."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from graspmemoedu.access import AccessStore, FEATURES, STANDARD_FEATURES, GUEST_FEATURES, FEATURE_REQUIREMENTS
from graspmemoedu.service import DomainError
from tests.fixtures import temporary_test_directory


class PermissionDefaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.topics = [{'id': 'math', 'title': 'Math'}, {'id': 'physics', 'title': 'Physics'}]
        self.store = AccessStore(self.temp.name, self.topics, guest_topic_ids=['math'])

    def account(self, **permissions):
        created = self.store.create_account('Fixture', **permissions)
        self.learner_id = created['account']['learner_id']
        self.token = self.store.login(created['invitation_code'])['identity_token']
        return created['account']

    def expanded(self):
        return AccessStore(self.temp.name, self.topics + [{'id': 'chemistry', 'title': 'Chemistry'}], guest_topic_ids=['math'])

    def test_new_defaults_include_future_topics_and_features_for_existing_session(self):
        account = self.account()
        self.assertEqual(account['permission_defaults'], {'topics': True, 'features': True})
        store = self.expanded()
        self.assertEqual({t['id'] for t in store.resolve(self.token)['topics']}, {'math', 'physics', 'chemistry'})
        with patch('graspmemoedu.access.FEATURES', (*FEATURES, 'future_feature')), patch.dict(FEATURE_REQUIREMENTS, {'future_feature': {}}):
            self.assertIn('future_feature', store.resolve(self.token)['features'])

    def test_explicit_empty_and_subset_do_not_expand(self):
        for topics, features in (([], []), (['math'], ['learn'])):
            with self.subTest(topics=topics):
                account = self.account(topic_ids=topics, features=features)
                self.assertEqual(account['permission_defaults'], {'topics': False, 'features': False})
                resolved = self.expanded().resolve(self.token)
                self.assertEqual([t['id'] for t in resolved['topics']], topics)
                self.assertEqual(resolved['features'], features)

    def test_single_dimension_update_preserves_other_mode_and_existing_session(self):
        self.account()
        self.store.set_permissions(self.learner_id, features=[])
        current = self.store.resolve(self.token)
        self.assertEqual(current['permission_defaults'], {'topics': True, 'features': False})
        self.assertEqual(current['features'], [])
        self.store.set_permissions(self.learner_id, topic_ids=['physics'])
        current = self.store.resolve(self.token)
        self.assertEqual(current['permission_defaults'], {'topics': False, 'features': False})
        self.assertEqual([t['id'] for t in current['topics']], ['physics'])
        self.assertEqual(current['features'], [])
        self.store.set_permissions(self.learner_id, reset_features=True)
        current = self.store.resolve(self.token)
        self.assertEqual(current['permission_defaults'], {'topics': False, 'features': True})
        self.assertEqual([t['id'] for t in current['topics']], ['physics'])
        self.assertEqual(current['features'], list(STANDARD_FEATURES))
        self.store.set_permissions(self.learner_id, reset_topics=True)
        self.assertEqual(self.expanded().resolve(self.token)['permission_defaults'], {'topics': True, 'features': True})
        self.assertEqual(len(self.expanded().resolve(self.token)['topics']), 3)

    def test_omitted_arguments_keep_modes_and_removed_topic_override(self):
        self.account(topic_ids=['physics'])
        store = AccessStore(self.temp.name, [self.topics[0]], guest_topic_ids=['math'])
        result = store.set_permissions(self.learner_id, features=['learn'])
        self.assertEqual(result['topics'], [])
        with closing(sqlite3.connect(store.db_path)) as con:
            self.assertEqual(json.loads(con.execute('SELECT topic_ids FROM access_principals WHERE learner_id=?',
                (self.learner_id,)).fetchone()[0]), ['physics'])
        self.assertEqual(store.set_permissions(self.learner_id)['permission_defaults'], {'topics': False, 'features': False})

    def test_conflicting_or_invalid_reset_is_atomic(self):
        self.account()
        before = self.store.resolve(self.token)
        for kwargs in ({'topic_ids': [], 'reset_topics': True}, {'features': [], 'reset_features': True},
                       {'reset_topics': 'true'}, {'reset_features': 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(DomainError):
                self.store.set_permissions(self.learner_id, **kwargs)
            self.assertEqual(self.store.resolve(self.token), before)

    def test_legacy_snapshot_migration_does_not_infer_default_grant(self):
        self.account(topic_ids=['math', 'physics'], features=list(FEATURES))
        with closing(sqlite3.connect(self.store.db_path)) as con:
            con.execute('ALTER TABLE access_principals DROP COLUMN topics_default')
            con.execute('ALTER TABLE access_principals DROP COLUMN features_default')
            con.commit()
        current = self.expanded().resolve(self.token)
        self.assertEqual(current['permission_defaults'], {'topics': False, 'features': False})
        self.assertEqual([t['id'] for t in current['topics']], ['math', 'physics'])

    def test_guest_restrictions_and_disabled_account_remain_effective(self):
        guest = self.store.create_guest()
        current = self.expanded().resolve(guest['identity_token'])
        self.assertEqual([t['id'] for t in current['topics']], ['math'])
        self.assertEqual(current['features'], list(GUEST_FEATURES))
        self.assertNotIn('permission_defaults', current)
        self.account()
        self.store.set_enabled(self.learner_id, False)
        with self.assertRaises(DomainError):
            self.expanded().resolve(self.token)


if __name__ == '__main__':
    unittest.main()
