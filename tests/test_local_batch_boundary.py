import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from graspmemoedu import author_import
from scripts import atomic_question_trial as trial
from scripts import produce_context


class LocalBatchBoundaryTests(unittest.TestCase):
    def test_local_factory_keeps_the_website_api_client_intact(self):
        original=author_import.CompatibleClient
        fake=SimpleNamespace(model='fixture-codex',identity=['codex_exec','fixture-codex'])
        with patch.dict('sys.modules',{'batch_transport':SimpleNamespace(BatchClient=lambda **kwargs:fake)}):
            self.assertIs(trial.CompatibleClient('unused','unused-key','fixture-codex'),fake)
        self.assertIs(author_import.CompatibleClient,original)

    def test_prior_api_response_is_preserved_without_relabel_or_rebill(self):
        with tempfile.TemporaryDirectory(dir='D:/Caches/ProgressiveReading') as temp:
            folder=Path(temp)
            path=folder/'generation.response.json'
            old={'model':'deepseek-flash','choices':[{'message':{'content':'old response'}}]}
            path.write_text(json.dumps(old),encoding='utf-8')
            client=SimpleNamespace(identity=['codex_exec','fixture-codex'])
            with self.assertRaisesRegex(ValueError,'Prior API response retained'):
                trial.call(client,folder,'generation','prompt',{})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),old)
            self.assertFalse((folder/'generation.started.json').exists())

    def test_legacy_context_api_option_fails_before_a_website_client_is_created(self):
        with patch.object(produce_context,'CompatibleClient',side_effect=AssertionError('software API invoked')):
            with self.assertRaisesRegex(ValueError,'requires --backend codex-exec'):
                produce_context.make_client(('old','old-key','old-model'),Path('unused'),'api',{})


if __name__=='__main__':
    unittest.main()
