"""Recovery boundaries for the small operational switch, without live shutdown."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import site_availability as site
from tests.fixtures import temporary_test_directory


class SiteAvailabilityTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory(); self.addCleanup(temp.cleanup)
        self.state_path = Path(temp.name) / 'availability.json'
        self.pages = {'source': {'branch': 'main', 'path': '/'}, 'build_type': 'legacy',
                      'https_enforced': True, 'cname': None, 'html_url': site.URL}
        self.gate_state = 'online'; self.monitor = True; self.commit = 'a' * 40
        self.calls = []; self.fail = None
        for target, value in [('STATE', self.state_path), ('github', self.github),
                              ('gate', self.gate), ('status_code', self.code)]:
            patcher = patch.object(site, target, value); patcher.start(); self.addCleanup(patcher.stop)

    def github(self, method, endpoint, body=None, missing=False):
        self.calls.append((method, endpoint, copy.deepcopy(body)))
        if (method, endpoint) == self.fail:
            raise RuntimeError('Synthetic operation failure')
        if endpoint == site.PAGES + '/pages':
            if method == 'GET':
                if not missing and self.pages is None: raise RuntimeError('Pages missing')
                return copy.deepcopy(self.pages)
            if method == 'DELETE': self.pages = None
            elif method == 'POST': self.pages = {**body, 'html_url': site.URL}
            elif method == 'PUT': self.pages.update(body)
        elif endpoint == site.PAGES + '/git/ref/heads/main': return {'object': {'sha': self.commit}}
        elif endpoint == site.PAGES + '/pages/builds': pass
        elif endpoint == site.WORKFLOW: return {'state': 'active' if self.monitor else 'disabled_manually'}
        elif endpoint == site.WORKFLOW + '/disable': self.monitor = False
        elif endpoint == site.WORKFLOW + '/enable': self.monitor = True
        else: raise AssertionError('Unexpected target')

    def gate(self, action):
        if action != 'status': self.gate_state = action
        return self.gate_state

    def code(self, url):
        if url == site.API + '/api/health': return 200
        if url == site.API + '/api/catalog': return 404 if self.gate_state == 'offline' else 401
        return 404 if self.pages is None else 200

    def state(self): return json.loads(self.state_path.read_text())

    def test_prepare_is_read_only_and_full_cycle_restores_settings(self):
        self.assertEqual(site.operate('prepare')['phase'], 'online')
        self.assertTrue(all(method == 'GET' for method, _, _ in self.calls))
        original = copy.deepcopy(self.pages)
        self.assertEqual(site.operate('offline')['phase'], 'offline')
        self.assertIsNone(self.pages); self.assertFalse(self.monitor)
        self.assertEqual(site.operate('offline')['phase'], 'offline')
        self.assertEqual(self.state()['commit'], 'a' * 40)
        self.assertEqual(site.operate('online')['phase'], 'online')
        self.assertEqual(self.pages, original); self.assertTrue(self.monitor)
        self.assertEqual(self.gate_state, 'online')
        self.calls.clear(); self.assertTrue(site.operate('online')['already_online'])
        self.assertTrue(all(method == 'GET' for method, _, _ in self.calls))

    def test_partial_shutdown_keeps_recovery_snapshot_and_can_reopen(self):
        site.operate('prepare'); self.fail = ('DELETE', site.PAGES + '/pages')
        with self.assertRaises(RuntimeError): site.operate('offline')
        self.assertEqual(self.state()['phase'], 'offline_pending')
        self.assertEqual(self.state()['pages']['source'], {'branch': 'main', 'path': '/'})
        self.assertEqual(self.gate_state, 'offline')
        with self.assertRaises(RuntimeError): site.operate('prepare')
        self.fail = None
        self.assertEqual(site.operate('online')['phase'], 'online')
        self.assertTrue(self.monitor); self.assertEqual(self.gate_state, 'online')

    def test_failed_restore_can_retry_without_overwriting_prior_config(self):
        site.operate('prepare'); site.operate('offline')
        self.fail = ('POST', site.PAGES + '/pages')
        with self.assertRaises(RuntimeError): site.operate('online')
        self.assertEqual(self.state()['phase'], 'online_pending')
        self.assertEqual(self.gate_state, 'offline')
        self.fail = None
        self.assertEqual(site.operate('online')['phase'], 'online')

    def test_disabled_monitor_stays_disabled_after_restore(self):
        self.monitor = False
        site.operate('prepare'); site.operate('offline'); site.operate('online')
        self.assertFalse(self.monitor)
        self.assertNotIn(('PUT', site.WORKFLOW + '/enable', None), self.calls)

    def test_branch_drift_and_missing_snapshot_do_not_change_availability(self):
        with self.assertRaises(RuntimeError): site.operate('offline')
        self.assertEqual(self.gate_state, 'online'); self.assertEqual(self.calls, [])
        site.operate('prepare'); site.operate('offline'); self.commit = 'b' * 40
        self.calls.clear()
        with self.assertRaises(AssertionError): site.operate('online')
        self.assertIsNone(self.pages); self.assertEqual(self.gate_state, 'offline')
        self.assertTrue(all(method == 'GET' for method, _, _ in self.calls))

    def test_late_propagation_verify_finishes_restore_and_monitor(self):
        site.operate('prepare'); site.operate('offline')
        # Remote recovery finished, but the original call timed out waiting for Pages.
        self.pages = {'source': {'branch': 'main', 'path': '/'}, 'build_type': 'legacy',
                      'https_enforced': True, 'cname': None, 'html_url': site.URL}
        self.gate_state = 'online'
        state = self.state(); state['phase'] = 'online_pending'; site.save(state)
        self.assertEqual(site.operate('verify')['phase'], 'online')
        self.assertTrue(self.monitor)

    def test_restore_skips_settings_update_when_recreated_site_already_matches(self):
        site.operate('prepare'); original = copy.deepcopy(self.pages); site.operate('offline')
        self.pages = original
        self.pages['https_certificate'] = None
        self.fail = ('PUT', site.PAGES + '/pages')
        self.calls.clear()
        self.assertEqual(site.operate('online')['phase'], 'online')
        self.assertTrue(self.monitor); self.assertEqual(self.gate_state, 'online')
        self.assertFalse(any(method == 'PUT' and endpoint == site.PAGES + '/pages'
                             for method, endpoint, _ in self.calls))

    def test_restore_updates_only_settings_that_differ(self):
        site.operate('prepare'); original = copy.deepcopy(self.pages); site.operate('offline')
        self.pages = original; self.pages['source'] = {'branch': 'main', 'path': '/docs'}
        self.calls.clear()
        self.assertEqual(site.operate('online')['phase'], 'online')
        updates = [body for method, endpoint, body in self.calls
                   if method == 'PUT' and endpoint == site.PAGES + '/pages']
        self.assertEqual(updates, [{'source': {'branch': 'main', 'path': '/'}}])
