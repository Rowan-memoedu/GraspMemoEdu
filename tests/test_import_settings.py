import os
import unittest
from unittest.mock import patch
from graspmemoedu import import_settings as config
from graspmemoedu.author_import import make_client


class ImportSettingsTests(unittest.TestCase):
    def test_client_factory_uses_import_configuration(self):
        with patch('graspmemoedu.author_import.import_settings', return_value={
                'api_key':'dedicated-import-fixture', 'base_url':'https://api.deepseek.com',
                'model':'deepseek-flash'}):
            client = make_client()
        self.assertEqual(client._api_key, 'dedicated-import-fixture')
        self.assertEqual(client.model, 'deepseek-flash')

    def test_other_workflow_keys_cannot_authorize_import(self):
        with patch.object(config.os, 'name', 'posix'), patch.dict(os.environ, {
            'DEEPSEEK_CODEX_API_KEY': 'codex-only',
            'GRASPMEMOEDU_JUDGE_API_KEY': 'judge-only',
            'AI_PRODUCER_API_KEY': 'legacy-only',
        }, clear=True):
            with self.assertRaisesRegex(ValueError, 'GRASPMEMOEDU_IMPORT_API_KEY'):
                config.settings()

    def test_import_key_ignores_foreign_endpoint_and_model(self):
        with patch.object(config.os, 'name', 'posix'), patch.dict(os.environ, {
            'GRASPMEMOEDU_IMPORT_API_KEY': 'import-only',
            'AI_PRODUCER_BASE_URL': 'https://example.com',
            'AI_PRODUCER_MODEL': 'foreign',
            'AUTHOR_IMPORT_BASE_URL': 'https://example.com',
        }, clear=True):
            result = config.settings()
        self.assertEqual(result, dict(api_key='import-only',
                                     base_url='https://api.deepseek.com', model='deepseek-flash'))

    @unittest.skipUnless(os.name == 'nt', 'Windows user environment')
    def test_live_user_edit_overrides_stale_process_and_empty_disables(self):
        import winreg
        def query(key, name):
            if name == 'GRASPMEMOEDU_IMPORT_API_KEY':
                return current[0], winreg.REG_SZ
            raise FileNotFoundError(name)
        current = ['new-import-key']
        with patch.dict(os.environ, {'GRASPMEMOEDU_IMPORT_API_KEY': 'stale-import-key'},
                        clear=True), patch.object(winreg, 'QueryValueEx', side_effect=query):
            self.assertEqual(config.settings()['api_key'], 'new-import-key')
            current[0] = ''
            with self.assertRaises(ValueError):
                config.settings()

    def test_nonofficial_endpoint_is_rejected(self):
        with patch.object(config.os, 'name', 'posix'), patch.dict(os.environ, {
            'GRASPMEMOEDU_IMPORT_API_KEY': 'import-only',
            'GRASPMEMOEDU_IMPORT_BASE_URL': 'https://example.com',
        }, clear=True):
            with self.assertRaisesRegex(ValueError, 'official DeepSeek'):
                config.settings()
