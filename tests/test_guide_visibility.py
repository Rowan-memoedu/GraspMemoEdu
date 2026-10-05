"""Guide permission projections against real Node and real isolated HTTP APIs."""
import unittest
from unittest.mock import patch

from graspmemoedu.guide import GUIDE_MARKERS, guide_visibility, project_guide
from graspmemoedu.service import DomainError
from tests.test_guide import GuideFixture, GuideHTTPAcceptanceTests


SOURCE = '''# PUBLIC_BODY
![public](public.png)

<!-- guide:student -->
STUDENT_BODY
![student](student.png)
<!-- /guide:student -->

<!-- guide:advanced -->
ADVANCED_BODY
<!-- guide:course_hierarchy -->
HIERARCHY_BODY
![hierarchy](hierarchy.png)
<!-- /guide:course_hierarchy -->
<!-- guide:training_self_rating -->
RATING_BODY
![rating](rating.png)
<!-- /guide:training_self_rating -->
<!-- guide:review_question_mode -->
MODE_BODY
![mode](mode.png)
<!-- /guide:review_question_mode -->
<!-- guide:review_timeout -->
TIMEOUT_BODY
![timeout](timeout.png)
<!-- /guide:review_timeout -->
<!-- /guide:advanced -->
'''


class GuideVisibilityTests(GuideFixture):
    def setUp(self):
        super().setUp()
        self.source.write_text(SOURCE, encoding='utf-8')
        for name in ('public', 'student', 'hierarchy', 'rating', 'mode', 'timeout'):
            self._asset_file(name + '.png')

    def test_cross_qualification_and_feature_matrix_real_renderer(self):
        advanced = ['course_hierarchy', 'training_self_rating', 'review_question_mode']
        cases = [
            (None, []),
            ({'role': 'guest'}, ['STUDENT', 'ADVANCED', 'HIERARCHY', 'RATING', 'MODE']),
            ({'role': 'account', 'is_admin': True}, ['STUDENT', 'ADVANCED', 'HIERARCHY', 'RATING', 'MODE']),
            ({'role': 'account', 'features': ['request_help', *advanced]}, []),
            ({'role': 'account', 'is_student': True, 'features': ['request_help', *advanced]}, ['STUDENT']),
            ({'role': 'account', 'is_advanced_learner': True, 'features': ['request_help', *advanced]}, ['ADVANCED', 'HIERARCHY', 'RATING', 'MODE']),
            ({'role': 'account', 'is_student': True, 'is_advanced_learner': True, 'features': ['request_help', 'course_hierarchy']}, ['STUDENT', 'ADVANCED', 'HIERARCHY']),
            ({'role': 'account', 'is_student': True, 'is_advanced_learner': True, 'features': []}, ['ADVANCED']),
            ({'role': 'account', 'is_advanced_learner': True, 'features': []}, ['ADVANCED']),
            ({'role': 'account', 'is_student': True, 'features': []}, []),
        ]
        for access, expected in cases:
            with self.subTest(access=access):
                result = self.reader.read(self.asset_base, access)
                self.assertEqual(result['status'], 'ready')
                self.assertIn('PUBLIC_BODY', result['html'])
                for label in ('STUDENT', 'ADVANCED', 'HIERARCHY', 'RATING', 'MODE'):
                    self.assertEqual(label + '_BODY' in result['html'], label in expected)
                self.assertEqual('TIMEOUT_BODY' in result['html'], 'ADVANCED' in expected)
                self.assertNotIn('guide:student', result['html'])
                self.assertNotIn('<!--', result['html'])

    def test_assets_recheck_projection_not_prior_privileged_cache(self):
        guest = {'role': 'guest'}
        student = {'role': 'account', 'is_student': True, 'features': ['request_help']}
        self.reader.read(self.asset_base, guest)
        self.assertTrue(self.reader.asset('student.png', student).is_file())
        for access in (None, {'role': 'account'}, student):
            with self.assertRaises(DomainError):
                self.reader.asset('hierarchy.png', access)
        with self.assertRaises(DomainError):
            self.reader.asset('student.png')
        self.assertTrue(self.reader.asset('public.png').is_file())

    def test_version_changes_on_revoke_and_restores_without_source_change(self):
        access = {'role': 'account', 'is_student': True, 'features': ['request_help']}
        before = self.reader.read(self.asset_base, access)
        access['features'] = []
        after = self.reader.read(self.asset_base, access)
        self.assertNotEqual(before['version'], after['version'])
        self.assertNotIn('student.png', after['html'])
        access['features'] = ['request_help']
        restored = self.reader.read(self.asset_base, access)
        self.assertEqual(before['version'], restored['version'])
        self.assertEqual(before['html'], restored['html'])

    def test_invalid_markers_fail_closed_even_in_hidden_blocks(self):
        for text in ('<!-- guide:unknown -->\nsecret\n<!-- /guide:unknown -->',
                     '<!-- guide:student -->\nsecret', '<!-- /guide:advanced -->',
                     '<!-- guide:student -->\n<!-- /guide:advanced -->',
                     'text <!-- guide:student -->', '<!-- guide:student -->\n<!-- guide:BAD -->\n<!-- /guide:student -->'):
            with self.subTest(text=text):
                self.source.write_text(text, encoding='utf-8')
                result = self.reader.read(self.asset_base, {'role': 'guest'})
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['html'], '')
                with self.assertRaises(DomainError):
                    self.reader.asset('public.png', {'role': 'guest'})

    def test_fenced_literal_markers_do_not_control_visibility(self):
        for fence in ('```', '~~~~'):
            text = f'{fence}text\n<!-- guide:unknown -->\n<!-- /guide:student -->\n{fence}\nPUBLIC_END'
            self.source.write_text(text, encoding='utf-8')
            result = self.reader.read(self.asset_base)
            self.assertEqual(result['status'], 'ready')
            self.assertIn('&lt;!-- guide:unknown --&gt;', result['html'])
            self.assertIn('PUBLIC_END', result['html'])
        text = '<!-- guide:student -->\n```\n<!-- /guide:student -->\n```\nPRIVATE\n<!-- /guide:student -->\nPUBLIC'
        self.assertEqual(project_guide(text, guide_visibility()), '\n\nPUBLIC')

    def test_bounded_view_cache_reuses_real_renderings_and_invalidates_source(self):
        guest = {'role': 'guest'}
        student = {'role': 'account', 'is_student': True, 'features': ['request_help']}
        advanced = {'role': 'account', 'is_advanced_learner': True, 'features': []}
        # Spy only: every cache miss still invokes the real Node renderer.
        with patch.object(self.reader, '_compile', wraps=self.reader._compile) as compile_spy:
            for access in (guest, student, advanced, guest, student, advanced):
                self.assertEqual(self.reader.read(self.asset_base, access)['status'], 'ready')
            self.assertEqual(compile_spy.call_count, 3)
            self.assertTrue(self.reader.asset('timeout.png', advanced).is_file())
            with self.assertRaises(DomainError):
                self.reader.asset('timeout.png', student)
            self.assertEqual(compile_spy.call_count, 3)
            self.source.write_text(SOURCE.replace('TIMEOUT_BODY', 'UPDATED_TIMEOUT'), encoding='utf-8')
            self.assertIn('UPDATED_TIMEOUT', self.reader.read(self.asset_base, advanced)['html'])
            self.assertEqual(compile_spy.call_count, 4)
            self.assertEqual(len(self.reader._views), 1)
            self.reader.MAX_CACHED_VIEWS = 2
            for access in (guest, student, advanced):
                self.reader.read(self.asset_base, access)
            self.assertEqual(len(self.reader._views), 2)
            previous = compile_spy.call_count
            self.reader.read(self.asset_base, guest)
            self.assertEqual(compile_spy.call_count, previous + 1)


class GuideVisibilityHTTPTests(GuideHTTPAcceptanceTests):
    def setUp(self):
        super().setUp()
        self.source.write_text(SOURCE, encoding='utf-8')
        for name in ('public', 'student', 'hierarchy', 'rating', 'mode', 'timeout'):
            self._asset_file(name + '.png')

    # Only this subclass's targeted methods are collected below; inherited
    # historical guide methods retain their original fixtures in test_guide.
    def test_live_account_permissions_and_asset_revocation(self):
        store = self.app.state.access_store
        made = store.create_account('Isolated guide test', is_student=True, is_advanced_learner=True,
                                    features=['request_help', 'course_hierarchy'])
        logged = store.login(made['invitation_code'])
        token = logged['identity_token']
        headers = self._headers(token)
        first = self.client.get('/api/guide', headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertIn('STUDENT_BODY', first.json()['html'])
        self.assertIn('HIERARCHY_BODY', first.json()['html'])
        self.assertNotIn('RATING_BODY', first.json()['html'])
        self.assertEqual(self.client.get('/guide-assets/student.png', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/guide-assets/hierarchy.png', headers=headers).status_code, 200)
        store.set_permissions(made['account']['learner_id'], features=['request_help'])
        second = self.client.get('/api/guide', headers=headers)
        self.assertNotEqual(first.json()['version'], second.json()['version'])
        self.assertIn('ADVANCED_BODY', second.json()['html'])
        self.assertIn('TIMEOUT_BODY', second.json()['html'])
        self.assertNotIn('HIERARCHY_BODY', second.json()['html'])
        self.assertEqual(self.client.get('/guide-assets/hierarchy.png?v='+first.json()['version'], headers=headers).status_code, 404)
        self.assertEqual(self.client.get('/guide-assets/student.png', headers=headers).status_code, 200)
        store.set_permissions(made['account']['learner_id'], is_student=False)
        self.assertEqual(self.client.get('/guide-assets/student.png', headers=headers).status_code, 404)
        self.assertNotIn('STUDENT_BODY', self.client.get('/api/guide', headers=headers).json()['html'])
        self.assertEqual(self.client.get('/guide-assets/timeout.png', headers=headers).status_code, 200)
        before_qualification_revoke = self.client.get('/api/guide', headers=headers).json()
        store.set_permissions(made['account']['learner_id'], is_advanced_learner=False)
        self.assertEqual(self.client.get('/guide-assets/timeout.png', headers=headers).status_code, 404)
        after_qualification_revoke = self.client.get('/api/guide', headers=headers).json()
        self.assertNotIn('ADVANCED_BODY', after_qualification_revoke['html'])
        self.assertNotEqual(before_qualification_revoke['version'], after_qualification_revoke['version'])
        store.set_enabled(made['account']['learner_id'], False)
        self.assertEqual(self.client.get('/guide-assets/student.png', headers=headers).status_code, 401)

    def test_anonymous_assets_only_public_guest_sees_all_no_identity_creation(self):
        before = self._rows('SELECT COUNT(*) FROM access_principals')
        for asset, status in [('public.png', 200), ('student.png', 404), ('hierarchy.png', 404)]:
            self.assertEqual(self.client.get('/guide-assets/'+asset).status_code, status)
        self.assertEqual(self._rows('SELECT COUNT(*) FROM access_principals'), before)
        guest = self._guest()
        result = self.client.get('/api/guide', headers=self._headers(guest))
        self.assertIn('STUDENT_BODY', result.json()['html'])
        self.assertIn('MODE_BODY', result.json()['html'])
        self.assertEqual(self.client.get('/guide-assets/mode.png', headers=self._headers(guest)).status_code, 200)
        self.assertEqual(self.client.get('/guide-assets/mode.png').status_code, 404)
        self.assertEqual(result.headers['cache-control'], 'no-store')

    def test_http_cross_roles_and_subject_asset_route(self):
        store = self.app.state.access_store
        cases = [
            ({}, []),
            ({'is_student': True}, ['STUDENT']),
            ({'is_advanced_learner': True}, ['ADVANCED', 'HIERARCHY', 'RATING', 'MODE']),
            ({'is_student': True, 'is_advanced_learner': True}, ['STUDENT', 'ADVANCED', 'HIERARCHY', 'RATING', 'MODE']),
            ({'is_admin': True}, ['STUDENT', 'ADVANCED', 'HIERARCHY', 'RATING', 'MODE']),
            ({'is_advanced_learner': True, 'features': []}, ['ADVANCED']),
        ]
        for flags, expected in cases:
            with self.subTest(flags=flags):
                if flags.get('is_admin'):
                    with store._transaction() as con:
                        made = store._insert_account(con, 'Isolated admin', is_admin=True)
                else:
                    made = store.create_account('Isolated qualification', **flags)
                token = store.login(made['invitation_code'])['identity_token']
                headers = self._headers(token)
                result = self.client.get('/api/guide?subject_id=math', headers=headers)
                self.assertEqual(result.status_code, 200)
                for label in ('STUDENT', 'ADVANCED', 'HIERARCHY', 'RATING', 'MODE'):
                    self.assertEqual(label+'_BODY' in result.json()['html'], label in expected)
                self.assertEqual('TIMEOUT_BODY' in result.json()['html'], 'ADVANCED' in expected)
                for asset, label in [('student', 'STUDENT'), ('hierarchy', 'HIERARCHY'), ('timeout', 'ADVANCED')]:
                    response = self.client.get('/subject-guide-assets/math/'+asset+'.png', headers=headers)
                    self.assertEqual(response.status_code, 200 if label in expected else 404)
        self.assertEqual(self.client.get('/subject-guide-assets/math/public.png').status_code, 200)
        self.assertEqual(self.client.get('/subject-guide-assets/math/student.png').status_code, 404)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(GuideVisibilityTests))
    suite.addTests(GuideVisibilityHTTPTests(name) for name in (
        'test_live_account_permissions_and_asset_revocation',
        'test_anonymous_assets_only_public_guest_sees_all_no_identity_creation',
        'test_http_cross_roles_and_subject_asset_route'))
    return suite


if __name__ == '__main__':
    unittest.main()
