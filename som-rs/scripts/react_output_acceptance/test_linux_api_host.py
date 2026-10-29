"""Synthetic adapter and real child fixtures; GitHub-hosted runners only."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import linux_api_host

spec = importlib.util.spec_from_file_location('api_recorder_fixture', Path(__file__).with_name('record_api.py'))
recorder_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder_module)


class LinuxHostFixtures(unittest.TestCase):
    def test_live_listener_query_retains_original_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
            qa = {'python': sys.executable, 'node': '/qa/node', 'pg_bin': '/qa/pg', 'lsof': '/usr/bin/lsof'}
            result = subprocess.CompletedProcess([], 0, '123\n', '')
            recorder = recorder_module.Recorder(Path(directory), mock.Mock(return_value=result), qa=qa)
            argv = ['/usr/bin/lsof', '-nP', '-a', '-p', '123', '-iTCP:18919', '-sTCP:LISTEN', '-t']
            self.assertIs(recorder.run(argv, capture_output=True, text=True), result)
            event = recorder.events[-1]
            self.assertEqual(event['kind'], 'listener')
            self.assertEqual(event['argv'], argv)
            self.assertEqual((Path(directory) / event['stdout']['file']).read_bytes(), b'123\n')

    def test_tools_and_owner_checks_are_scoped_and_restored(self):
        host = SimpleNamespace(NODE_BIN='/mac/node', PG_BIN_DIR=Path('/mac/pg'),
                               verify_process_cwd=lambda *_: ('old', None), _python_path=lambda _: 'old',
                               forbidden_pids=lambda: {99},
                               check_port_allowed=lambda port: (_ for _ in ()).throw(ValueError()) if port in (8765, 18765) else None,
                               base_hosts=SimpleNamespace(listening=lambda *_: 'old'))
        qa = {'node': '/qa/node', 'pg_bin': '/qa/pg', 'lsof': '/usr/bin/lsof'}
        fake = subprocess.CompletedProcess([], 0, 'p123\nn/tmp/owned\n', '')
        with mock.patch.object(linux_api_host.subprocess, 'run', return_value=fake) as command:
            with linux_api_host.configured_host(host, qa):
                self.assertEqual(host.NODE_BIN, '/qa/node')
                self.assertEqual(host.PG_BIN_DIR, Path('/qa/pg'))
                self.assertEqual(host._python_path(Path('/qa/product')), '/qa/product')
                self.assertEqual(host.verify_process_cwd(99, Path('/tmp/owned')), (False, None))
                self.assertEqual(host.verify_process_cwd(123, Path('/tmp/owned')), (True, '/tmp/owned'))
                self.assertEqual(command.call_args.args[0], ['/usr/bin/lsof', '-a', '-p', '123', '-d', 'cwd', '-Fn'])
                with self.assertRaises(ValueError):
                    host.base_hosts.listening(123, 8765)
                with self.assertRaises(ValueError):
                    host.base_hosts.listening(123, 18765)
            self.assertEqual(host.NODE_BIN, '/mac/node')
            self.assertEqual(host.PG_BIN_DIR, Path('/mac/pg'))
            self.assertEqual(host._python_path(Path('/qa/product')), 'old')

    def test_real_child_exit_and_timeout_wait_are_recorded(self):
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as temporary:
            qa = {'python': sys.executable, 'node': '/qa/node', 'pg_bin': '/qa/pg', 'lsof': '/usr/bin/lsof'}
            recorder = recorder_module.Recorder(Path(temporary), subprocess.run, qa=qa)
            child = recorder.popen([sys.executable, '-c', 'raise SystemExit(7)'])
            self.assertEqual(child.wait(timeout=10), 7)
            starts = [event for event in recorder.events if event['kind'] == 'child-start']
            waits = [event for event in recorder.events if event['kind'] == 'child-wait']
            self.assertEqual(starts[-1]['pid'], child.pid)
            self.assertEqual(waits[-1], {'kind': 'child-wait', 'pid': child.pid, 'state': 'completed',
                                         'error_type': None, 'exit_code': 7})
            child = recorder.popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    child.wait(timeout=0.1)
            finally:
                child.terminate()
                child.wait(timeout=10)
            waits = [event for event in recorder.events if event['kind'] == 'child-wait' and event['pid'] == child.pid]
            self.assertEqual(waits[0]['state'], 'unverified')
            self.assertEqual(waits[0]['error_type'], 'TimeoutExpired')
            self.assertEqual(waits[-1]['state'], 'completed')


if __name__ == '__main__':
    unittest.main()
