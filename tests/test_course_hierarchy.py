import json
import unittest
from contextlib import closing

from graspmemoedu.catalog import Catalog
from graspmemoedu.service import DomainError
from graspmemoedu.site_backup import backup_site, restore_site
from tests import test_dashboard as fixture


class CourseHierarchyTests(unittest.TestCase):
    setUp = fixture.DashboardAcceptanceTests.setUp
    _open = fixture.DashboardAcceptanceTests._open
    _close = fixture.DashboardAcceptanceTests._close
    _restart = fixture.DashboardAcceptanceTests._restart
    _headers = fixture.DashboardAcceptanceTests._headers
    _guest = fixture.DashboardAcceptanceTests._guest
    _get = fixture.DashboardAcceptanceTests._get
    _post = fixture.DashboardAcceptanceTests._post
    _action = fixture.DashboardAcceptanceTests._action
    _complete = fixture.DashboardAcceptanceTests._complete
    origin = fixture.DashboardAcceptanceTests.origin

    def _hierarchy(self):
        self.catalog_data['courses'][0]['units'] = [
            {'id': 'a', 'title': 'A', 'topic_ids': []},
            {'id': 'b', 'title': 'B', 'parent_id': 'a', 'topic_ids': []},
            {'id': 'c', 'title': 'C', 'parent_id': 'b', 'topic_ids': ['topic-a']},
            {'id': 'd', 'title': 'D', 'parent_id': 'c', 'topic_ids': ['topic-b']},
            {'id': 'empty', 'title': 'Empty', 'topic_ids': []}]
        self._write()
        self._restart()

    def _write(self):
        (self.root / 'catalog.json').write_text(json.dumps(self.catalog_data), encoding='utf-8')

    def _account(self, advanced=True):
        store = self.app.state.access_store
        created = store.create_account('Hierarchy QA', purpose='test')
        account = created['account']
        store.set_permissions(account['learner_id'], is_advanced_learner=advanced)
        token = store.login(created['invitation_code'])['identity_token']
        return {'identity_token': token, 'access': store.resolve(token), 'invitation_code': created['invitation_code']}

    def test_uneven_depth_and_only_pending_authorized_descendants(self):
        self._hierarchy()
        identity = self._account()
        data = self._get('/api/dashboard?course_id=course-a', identity)
        self.assertTrue(data['course_hierarchy_enabled'])
        self.assertEqual([unit['id'] for unit in data['pending_hierarchy']], ['a', 'b', 'c', 'd'])
        blocked = next(task for task in data['tasks'] if task['topic_id'] == 'topic-b')
        self.assertFalse(blocked['dependency_ready'])
        store = self.app.state.access_store
        store.set_permissions(identity['access']['learner_id'], topic_ids=['topic-a'])
        restricted = self._get('/api/dashboard?course_id=course-a', identity)
        self.assertEqual([unit['id'] for unit in restricted['pending_hierarchy']], ['a', 'b', 'c'])
        self.assertNotIn('topic-b', json.dumps(restricted['pending_hierarchy']))

    def test_legacy_course_container_is_not_a_visible_chapter(self):
        high = self._account()
        data = self._get('/api/dashboard?course_id=course-b', high)
        self.assertTrue(data['course_hierarchy_enabled'])
        self.assertEqual(data['pending_hierarchy'], [])
        self.assertEqual([task['topic_id'] for task in data['tasks']], ['topic-c'])
        # Genuine course-internal chapters retain their grouping, even when
        # there is only one chapter in a course.
        self.catalog_data['courses'][1]['units'] = [
            {'id': 'chapter-c', 'title': 'Chapter C', 'topic_ids': ['topic-c']}]
        self._write(); self._restart()
        grouped = self._get('/api/dashboard?course_id=course-b', high)
        self.assertEqual([unit['id'] for unit in grouped['pending_hierarchy']], ['chapter-c'])

    def test_course_root_children_are_promoted_without_losing_direct_topics(self):
        self.catalog_data['courses'][0]['units'] = [
            {'id': 'course-a', 'title': 'Course A', 'topic_ids': ['topic-a']},
            {'id': 'chapter', 'title': 'Chapter', 'parent_id': 'course-a', 'topic_ids': []},
            {'id': 'section', 'title': 'Section', 'parent_id': 'chapter', 'topic_ids': ['topic-b']}]
        self._write(); self._restart()
        high = self._account()
        data = self._get('/api/dashboard?course_id=course-a', high)
        self.assertEqual([unit['id'] for unit in data['pending_hierarchy']], ['chapter', 'section'])
        self.assertIsNone(data['pending_hierarchy'][0]['parent_id'])
        self.assertEqual(data['pending_hierarchy'][1]['parent_id'], 'chapter')
        self.assertEqual({task['topic_id'] for task in data['tasks']}, {'topic-a', 'topic-b'})

    def test_default_off_for_standard_and_guest_and_cross_device_preference(self):
        self._hierarchy()
        ordinary = self._account(False)
        for identity in (ordinary, self.guest):
            self.assertFalse(self._get('/api/profile', identity)['course_hierarchy_enabled'])
            self.assertEqual(self._get('/api/dashboard?course_id=course-a', identity)['pending_hierarchy'], [])
            self._post('/api/profile', {'course_hierarchy_enabled': True}, identity, status=403)
        high = self._account()
        self.assertTrue(self._get('/api/profile', high)['course_hierarchy_enabled'])
        self._post('/api/profile', {'course_hierarchy_enabled': False}, high)
        self._post('/api/profile', {'display_name': 'Still disabled'}, high)
        self._restart()
        other_device = {'identity_token': self.app.state.access_store.login(high['invitation_code'])['identity_token']}
        self.assertFalse(self._get('/api/profile', other_device)['course_hierarchy_enabled'])
        self.assertFalse(self._get('/api/profile', high)['course_hierarchy_enabled'])
        self.assertEqual(self._get('/api/dashboard?course_id=course-a', high)['pending_hierarchy'], [])
        self._post('/api/profile', {'course_hierarchy_enabled': True}, high)
        store = self.app.state.access_store
        store.set_permissions(high['access']['learner_id'], is_advanced_learner=False)
        self.assertFalse(self._get('/api/profile', high)['course_hierarchy_enabled'])
        store.set_permissions(high['access']['learner_id'], is_advanced_learner=True)
        self.assertTrue(self._get('/api/profile', high)['course_hierarchy_enabled'])
        for invalid in (1, 'true', None):
            self._post('/api/profile', {'course_hierarchy_enabled': invalid}, high, status=422)

    def test_complete_branches_disappear_and_reviews_never_keep_them_visible(self):
        from graspmemoedu.course_hierarchy import pending_hierarchy
        self._hierarchy()
        units = self.app.state.catalog.course('course-a')['units']
        self.assertEqual(pending_hierarchy(units, [{'type': 'Review', 'topic_id': 'topic-b'}]), [])
        result = pending_hierarchy(units, [{'type': 'Lesson', 'topic_id': 'topic-a', 'status': 'paused'}])
        self.assertEqual([item['id'] for item in result], ['a', 'b', 'c'])
        high = self._account()
        self.guest = high
        self._complete('topic-a'); self._complete('topic-b')
        self.assertEqual(self._get('/api/dashboard?course_id=course-a', high)['pending_hierarchy'], [])

    def test_deep_tree_and_invalid_graphs(self):
        self._hierarchy()
        units = [{'id': f'n{i}', 'title': str(i), 'topic_ids': [],
                  'parent_id': f'n{i-1}' if i else None} for i in range(3000)]
        units[0]['topic_ids'] = ['topic-a']; units[-1]['topic_ids'] = ['topic-b']
        self.catalog_data['courses'][0]['units'] = units; self._write()
        catalog = Catalog.load(self.settings)
        from graspmemoedu.course_hierarchy import pending_hierarchy
        self.assertEqual(len(pending_hierarchy(catalog.course('course-a')['units'],
                         [{'type': 'Lesson', 'topic_id': 'topic-b'}])), 3000)
        for parent in ('n2999', 'missing'):
            units[0]['parent_id'] = parent; self._write()
            with self.assertRaises(DomainError): Catalog.load(self.settings)
        units[0]['parent_id'] = None; units[-1]['topic_ids'].append('topic-a'); self._write()
        with self.assertRaises(DomainError): Catalog.load(self.settings)

    def test_backup_preserves_structure_and_disabled_preference(self):
        self._hierarchy(); high = self._account()
        self._post('/api/profile', {'course_hierarchy_enabled': False}, high)
        archive = backup_site(self.settings, self.root / 'hierarchy.zip')
        target = self.root / 'restored'
        restore_site(self.root / 'hierarchy.zip', target)
        from graspmemoedu.config import Settings
        settings = Settings.load(target)
        self.assertEqual(Catalog.load(settings).course('course-a')['units'], self.app.state.catalog.course('course-a')['units'])
        import sqlite3
        with closing(sqlite3.connect(target / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT course_hierarchy_enabled FROM learner_preferences WHERE learner_id=?',
                                        (high['access']['learner_id'],)).fetchone()[0], 0)
