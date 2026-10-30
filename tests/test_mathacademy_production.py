"""Exercise content activation when a health monitor starts the old service."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tarfile
import unittest
from unittest.mock import patch

from scripts import install_mathacademy_production as release
from tests.fixtures import temporary_test_directory


class ProductionActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_test_directory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / 'graspmemoedu-ma-race'
        self.stage.mkdir()
        self.data = self.root / 'data'
        (self.data / 'content-activation-history').mkdir(parents=True)
        self.pointer = self.data / 'active-content.json'
        self.old, self.new = 'a' * 64, 'b' * 64
        self.activate(self.old)
        source = self.root / 'source'
        source.mkdir()
        (source / 'manifest.json').write_text('{}', encoding='utf-8')
        with tarfile.open(self.stage / 'package.tar.gz', 'w:gz') as archive:
            archive.add(source, arcname='package')
        self.state = {'active': True, 'generation': self.old, 'stops': 0}

    def activate(self, generation):
        self.pointer.write_text(json.dumps({'schema': 1, 'generation': generation}), encoding='utf-8')

    def generation(self):
        return json.loads(self.pointer.read_text(encoding='utf-8'))['generation']

    def execute(self, *, monitor_during_install=False, fail_new_health=False, monitor_during_rollback=False):
        def mapped_path(value):
            return {'/tmp': self.root, '/var/lib/graspmemoedu': self.data}.get(str(value), Path(value))

        def start():
            self.state.update(active=True, generation=self.generation())

        def command(args, **kwargs):
            if args[0] == 'runuser':
                if monitor_during_install:
                    start()  # An independent monitor loads the previous pointer.
                self.activate(self.new)
                return SimpleNamespace(returncode=0, stdout=json.dumps({'generation': self.new}))
            if args[:2] == ['systemctl', 'is-active']:
                return SimpleNamespace(returncode=3)
            if args[:1] == ['systemctl'] and args[-1] == 'graspmemoedu':
                if args[1] == 'stop':
                    self.state['active'] = False
                    self.state['stops'] += 1
                    if monitor_during_rollback and self.state['stops'] > 1:
                        start()  # Before the previous pointer is restored.
                elif args[1] == 'restart' or (args[1] == 'start' and not self.state['active']):
                    start()
            return SimpleNamespace(returncode=0)

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        def health(*args, **kwargs):
            if not self.state['active'] or (fail_new_health and self.state['generation'] == self.new):
                raise OSError('Synthetic unavailable generation')
            return Response()

        with patch.object(release, 'Path', mapped_path), \
             patch.object(release.sys, 'argv', ['installer', str(self.stage)]), \
             patch.object(release, 'os', SimpleNamespace(geteuid=lambda: 0, replace=os.replace)), \
             patch.object(release.subprocess, 'run', side_effect=command), \
             patch.object(release.urllib.request, 'urlopen', side_effect=health), \
             patch.object(release.time, 'sleep'), redirect_stdout(io.StringIO()):
            release.main()

    def test_activation_loads_new_content_even_if_a_monitor_started_the_old_service(self):
        self.execute(monitor_during_install=True)
        self.assertEqual(self.generation(), self.new)
        self.assertTrue(self.state['active'])
        self.assertEqual(self.state['generation'], self.new)
        self.assertTrue((self.data / 'content-activation-history' / (self.new + '.production.json')).exists())

    def test_rollback_reloads_the_restored_pointer_even_if_a_monitor_started_the_failed_generation(self):
        with self.assertRaisesRegex(RuntimeError, 'health check'):
            self.execute(fail_new_health=True, monitor_during_rollback=True)
        self.assertEqual(self.generation(), self.old)
        self.assertTrue(self.state['active'])
        self.assertEqual(self.state['generation'], self.old)
        self.assertFalse((self.data / 'content-activation-history' / (self.new + '.production.json')).exists())


if __name__ == '__main__':
    unittest.main()
