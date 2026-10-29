"""Synthetic six-gate integration tests. Run only on a GitHub runner."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

repo_tests = Path(os.environ['GITHUB_WORKSPACE']) / 'som-rs/tests/react-output-v1'
sys.path.insert(0, str(repo_tests))
spec = importlib.util.spec_from_file_location('som_draft_run_gates', Path(__file__).with_name('run_gates.py'))
gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)

class GateProfileTests(unittest.TestCase):
    def prepare(self):
        root = Path(tempfile.mkdtemp(prefix='som-gates-fixture-', dir=os.environ['RUNNER_TEMP']))
        product, cache, work = root / 'product', root / 'cache', root / 'work'
        product.mkdir()
        cache.mkdir()
        (product / 'package.json').write_bytes(b'{"name":"fixture"}\n')
        (product / 'package-lock.json').write_bytes(b'{"lockfileVersion":3}\n')
        (cache / 'item').write_bytes(b'cache')
        return root, product, cache, work

    def fake_process(self, calls, fail_step=None):
        def run(argv, *, cwd, env, stdout, stderr, timeout, check):
            calls.append((list(argv), dict(env), cwd, timeout, check))
            if len(calls) == 1:
                (cwd / 'node_modules/prettier').mkdir(parents=True)
            if '--outDir' in argv:
                dist = Path(argv[argv.index('--outDir') + 1])
                dist.mkdir()
                (dist / 'output.js').write_bytes(b'identical build')
            stdout.write('synthetic command\n')
            return subprocess.CompletedProcess(argv, 1 if len(calls) == fail_step else 0)
        return run

    def test_linux_runs_all_six_commands_and_reports_profile(self):
        root, product, cache, work = self.prepare()
        profile = root / 'profile.json'
        qa = {'node': '/fixture/node', 'npm_cli': '/fixture/npm-cli.js', 'cache': cache,
              'profile_sha256': 'a' * 64}
        calls = []
        with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(work), '--qa-profile', str(profile)]), \
             mock.patch.object(gates.linux_qa_profile, 'validate_profile', return_value=qa), \
             mock.patch.object(gates.linux_qa_profile, 'recheck') as recheck, \
             mock.patch.object(gates.linux_qa_profile, 'safe_tree', return_value=gates.linux_qa_profile.PRETTIER_TREE), \
             mock.patch.object(gates.subprocess, 'run', side_effect=self.fake_process(calls)):
            self.assertEqual(gates.main(), 0)
        report = json.loads((work / 'gates.json').read_text())
        self.assertEqual([row['step'] for row in report['gates']], ['install', 'typecheck', 'format_check', 'test', 'build-1', 'build-2'])
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[0][0], ['/fixture/node', '/fixture/npm-cli.js', 'ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'])
        self.assertTrue(all(call[0][0] == '/fixture/node' and call[1]['npm_config_offline'] == 'true' for call in calls))
        self.assertTrue(all(call[3:] == (600, False) for call in calls))
        self.assertTrue(report['passed'] and report['builds_identical'] and report['lock_unchanged'] and report['product_unchanged'])
        self.assertTrue(report['qa_profile']['recheck_passed'])
        for row, call in zip(report['gates'], calls):
            self.assertEqual(row['argv'], call[0])
            self.assertEqual(row['cwd'], str(call[2]))
            self.assertEqual(row['environment'], call[1])
            self.assertEqual(row['outcome'], 'exited')
            self.assertEqual(row['wait'], {'state': 'completed', 'exit_code': 0})
            self.assertEqual(row['log_sha256'], hashlib.sha256((work / row['log']).read_bytes()).hexdigest())
        self.assertEqual(report['qa_profile']['prettier_evidence']['path'], 'prettier-evidence')
        recheck.assert_called_once_with(qa)

    def test_macos_default_uses_frozen_tools_and_has_no_profile_report(self):
        root, product, cache, work = self.prepare()
        calls = []
        with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(work), '--cache', str(cache)]), \
             mock.patch.object(gates.subprocess, 'run', side_effect=self.fake_process(calls)):
            self.assertEqual(gates.main(), 0)
        report = json.loads((work / 'gates.json').read_text())
        self.assertEqual(calls[0][0][:2], [gates.NODE, gates.NPM_CLI])
        self.assertEqual(len(calls), 6)
        self.assertNotIn('qa_profile', report)

    def test_profile_failure_and_cache_override_never_pass(self):
        root, product, cache, work = self.prepare()
        profile = root / 'profile.json'
        qa = {'node': '/fixture/node', 'npm_cli': '/fixture/npm-cli.js', 'cache': cache,
              'profile_sha256': 'a' * 64}
        calls = []
        with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(work), '--qa-profile', str(profile)]), \
             mock.patch.object(gates.linux_qa_profile, 'validate_profile', return_value=qa), \
             mock.patch.object(gates.linux_qa_profile, 'recheck', side_effect=gates.linux_qa_profile.ProfileError('changed')), \
             mock.patch.object(gates.subprocess, 'run', side_effect=self.fake_process(calls)):
            self.assertEqual(gates.main(), 1)
        report = json.loads((work / 'gates.json').read_text())
        self.assertFalse(report['passed'])
        self.assertFalse(report['qa_profile']['recheck_passed'])
        with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(root / 'other'), '--qa-profile', str(profile), '--cache', str(cache)]), \
             mock.patch.object(gates.linux_qa_profile, 'validate_profile') as validate:
            with self.assertRaises(SystemExit):
                gates.main()
            validate.assert_not_called()

    def test_nonzero_command_cannot_pass(self):
        _, product, cache, work = self.prepare()
        calls = []
        with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(work), '--cache', str(cache)]), \
             mock.patch.object(gates.subprocess, 'run', side_effect=self.fake_process(calls, fail_step=2)):
            self.assertEqual(gates.main(), 1)
        report = json.loads((work / 'gates.json').read_text())
        self.assertEqual(len(calls), 6)
        self.assertEqual(report['gates'][1]['exit_code'], 1)
        self.assertFalse(report['passed'])

if __name__ == '__main__':
    unittest.main()
