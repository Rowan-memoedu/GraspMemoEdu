from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from math_learning.judge import ExistingJudge, independent_settings


class JudgeSettingsTests(unittest.TestCase):
    def test_outsourcing_variables_cannot_supply_missing_judge_configuration(self):
        with patch('math_learning.judge.os.name', 'posix'), patch.dict(
                'os.environ', {'DEEPSEEK_CODEX_API_KEY': 'outsourcing-only',
                               'AI_PRODUCER_BASE_URL': 'https://example.com',
                               'AI_PRODUCER_MODEL': 'external-model'}, clear=True):
            with self.assertRaises(ValueError):
                independent_settings()

    def test_user_variables_override_stale_process_values(self):
        import winreg
        user = {'MATH_JUDGE_API_KEY': 'new-private-key',
                'MATH_JUDGE_BASE_URL': 'https://example.com/v1', 'MATH_JUDGE_MODEL': 'new-model'}
        with patch.dict('os.environ', {'MATH_JUDGE_API_KEY': 'stale-key'}), patch.object(
                winreg, 'QueryValueEx', side_effect=lambda key, name: (user[name], winreg.REG_SZ)):
            result = independent_settings()
        self.assertEqual(result['api_key'], user['MATH_JUDGE_API_KEY'])
        self.assertEqual(result['model'], 'new-model')

    def test_next_call_uses_changed_settings_without_restarting_adapter(self):
        seen = []
        class FakeJudge:
            def __init__(self, config):
                seen.append(config)
            def judge(self, *args):
                return SimpleNamespace(status='judged', correct=True, reason='ok')
        adapter = ExistingJudge(Path('.'))
        adapter._module = SimpleNamespace(AnswerJudge=FakeJudge, JudgeConfig=lambda **values: values)
        with patch('math_learning.judge.independent_settings', side_effect=[
                {'api_key': 'first', 'base_url': 'https://example.com', 'model': 'first-model'},
                {'api_key': 'second', 'base_url': 'https://example.com', 'model': 'second-model'}]):
            self.assertEqual(adapter('question', 'reference', 'answer')['status'], 'judged')
            self.assertEqual(adapter('question', 'reference', 'answer')['status'], 'judged')
        self.assertEqual([row['model'] for row in seen], ['first-model', 'second-model'])
