"""Synthetic product gate evidence tests; run on GitHub-hosted runners only."""
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

import test_run_gates_profile as fixtures
gates = fixtures.gates


class RawEvidenceTests(unittest.TestCase):
    def prepare(self):
        return fixtures.GateProfileTests.prepare(self)

    def test_timeout_process_error_and_missing_exit_stop_and_record_raw_log(self):
        cases = ('timeout', 'error', 'missing')
        for kind in cases:
            with self.subTest(kind=kind):
                root, product, cache, work = self.prepare()
                calls = []
                def run(argv, *, cwd, env, stdout, stderr, timeout, check):
                    calls.append(list(argv))
                    stdout.write('partial raw output\n')
                    if kind == 'timeout':
                        raise subprocess.TimeoutExpired(argv, timeout)
                    if kind == 'error':
                        raise OSError('fixture process error')
                    return subprocess.CompletedProcess(argv, None)
                with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(work), '--cache', str(cache)]), \
                     mock.patch.object(gates.subprocess, 'run', side_effect=run):
                    self.assertEqual(gates.main(), 1)
                report = json.loads((work / 'gates.json').read_text())
                first, *remaining = report['gates']
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(report['gates']), 6)
                self.assertEqual(first['outcome'], {'timeout': 'timed-out', 'error': 'process-error', 'missing': 'unverified'}[kind])
                self.assertEqual(first['argv'], calls[0])
                self.assertEqual(first['wait'], {'state': 'unverified', 'exit_code': None})
                self.assertEqual(first['exit_code'], None)
                raw = (work / first['log']).read_bytes()
                self.assertEqual(raw, b'partial raw output\n')
                self.assertEqual(first['log_sha256'], hashlib.sha256(raw).hexdigest())
                self.assertTrue(all(row['outcome'] == 'not-run' and row['argv'] is None and row['log'] is None
                                    and row['exit_code'] is None for row in remaining))
                self.assertFalse(report['passed'])

    def test_real_short_child_nonzero_exit_and_wait(self):
        _, product, cache, work = self.prepare()
        child = [sys.executable, '-c', 'import sys; print("real fixture"); sys.exit(7)']
        with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(work), '--cache', str(cache)]), \
             mock.patch.object(gates, 'argv', return_value=child):
            self.assertEqual(gates.main(), 1)
        report = json.loads((work / 'gates.json').read_text())
        self.assertEqual(len(report['gates']), 6)
        for row in report['gates']:
            self.assertEqual(row['argv'], child)
            self.assertEqual(row['outcome'], 'exited')
            self.assertEqual(row['exit_code'], 7)
            self.assertEqual(row['wait'], {'state': 'completed', 'exit_code': 7})
            raw = (work / row['log']).read_bytes()
            self.assertEqual(raw, b'real fixture\n')
            self.assertEqual(row['log_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertFalse(report['passed'])

    def test_real_short_child_timeout_keeps_wait_unverified(self):
        _, product, cache, work = self.prepare()
        child = [sys.executable, '-c', 'import time; print("fixture start", flush=True); time.sleep(10)']
        original = subprocess.run
        def short_timeout(argv, *, cwd, env, stdout, stderr, timeout, check):
            self.assertEqual(timeout, 600)
            return original(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=1, check=check)
        with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(work), '--cache', str(cache)]), \
             mock.patch.object(gates, 'argv', return_value=child), \
             mock.patch.object(gates.subprocess, 'run', side_effect=short_timeout):
            self.assertEqual(gates.main(), 1)
        report = json.loads((work / 'gates.json').read_text())
        first, *remaining = report['gates']
        self.assertEqual(first['argv'], child)
        self.assertEqual(first['outcome'], 'timed-out')
        self.assertEqual(first['wait'], {'state': 'unverified', 'exit_code': None})
        self.assertEqual((work / first['log']).read_bytes(), b'fixture start\n')
        self.assertEqual(first['log_sha256'], hashlib.sha256((work / first['log']).read_bytes()).hexdigest())
        self.assertTrue(all(row['outcome'] == 'not-run' for row in remaining))
        self.assertFalse(report['passed'])

    def test_retain_prettier_preserves_raw_files_and_rejects_links(self):
        root = Path(tempfile.mkdtemp(prefix='som-prettier-evidence-', dir=os.environ['RUNNER_TEMP']))
        product, work = root / 'product', root / 'work'
        prettier = product / 'node_modules/prettier'
        prettier.mkdir(parents=True)
        (prettier / 'bin').mkdir()
        (prettier / 'bin/prettier.cjs').write_bytes(b'fixture prettier\n')
        (prettier / 'package.json').write_bytes(b'{"name":"prettier"}\n')
        work.mkdir()
        expected = gates.linux_qa_profile.safe_tree(prettier, False)
        with mock.patch.object(gates.linux_qa_profile, 'PRETTIER_TREE', expected):
            receipt = gates.retain_prettier(product, work)
        retained = work / receipt['path']
        self.assertEqual((retained / 'bin/prettier.cjs').read_bytes(), b'fixture prettier\n')
        self.assertEqual((retained / 'package.json').read_bytes(), b'{"name":"prettier"}\n')
        self.assertEqual(gates.linux_qa_profile.safe_tree(retained, False), expected)
        self.assertEqual(receipt, {'path': 'prettier-evidence', 'sha256': expected[0], 'files': expected[1], 'symlinks': 0})
        other = Path(tempfile.mkdtemp(prefix='som-prettier-link-', dir=os.environ['RUNNER_TEMP']))
        bad = other / 'product/node_modules/prettier'
        bad.mkdir(parents=True)
        (bad / 'target').write_bytes(b'x')
        (bad / 'link').symlink_to('target')
        (other / 'work').mkdir()
        with self.assertRaises(gates.linux_qa_profile.ProfileError):
            gates.retain_prettier(other / 'product', other / 'work')
        self.assertFalse((other / 'work/prettier-evidence').exists())

    def test_bad_or_missing_retained_prettier_cannot_pass(self):
        root, product, cache, work = self.prepare()
        qa = {'node': '/fixture/node', 'npm_cli': '/fixture/npm-cli.js', 'cache': cache, 'profile_sha256': 'a' * 64}
        calls = []
        fake = fixtures.GateProfileTests.fake_process(self, calls)
        with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(work), '--qa-profile', str(root / 'profile')]), \
             mock.patch.object(gates.linux_qa_profile, 'validate_profile', return_value=qa), \
             mock.patch.object(gates.linux_qa_profile, 'recheck'), \
             mock.patch.object(gates.linux_qa_profile, 'safe_tree', return_value=gates.linux_qa_profile.PRETTIER_TREE), \
             mock.patch.object(gates, 'retain_prettier', side_effect=gates.linux_qa_profile.ProfileError('tampered')), \
             mock.patch.object(gates.subprocess, 'run', side_effect=fake):
            self.assertEqual(gates.main(), 1)
        report = json.loads((work / 'gates.json').read_text())
        self.assertFalse(report['passed'])
        self.assertTrue(report['qa_profile']['recheck_passed'])
        self.assertIn('prettier evidence', report['qa_profile']['failure_reason'])
        self.assertIsNone(report['qa_profile']['prettier_evidence'])
        self.assertEqual(len(report['gates']), 6)

    def test_missing_lock_after_child_keeps_failure_evidence(self):
        _, product, cache, work = self.prepare()
        calls = []
        fake = fixtures.GateProfileTests.fake_process(self, calls)
        def mutate(argv, *, cwd, env, stdout, stderr, timeout, check):
            result = fake(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=timeout, check=check)
            if len(calls) == 1:
                (cwd / 'package-lock.json').rename(cwd / 'relocated-lock')
            return result
        with mock.patch.object(sys, 'argv', ['run_gates.py', str(product), str(work), '--cache', str(cache)]), \
             mock.patch.object(gates.subprocess, 'run', side_effect=mutate):
            self.assertEqual(gates.main(), 1)
        report = json.loads((work / 'gates.json').read_text())
        self.assertFalse(report['passed'])
        self.assertFalse(report['lock_unchanged'])
        self.assertIsNone(report['lock_sha256_after'])
        self.assertTrue(any('lock or manifest:' in reason for reason in report['postcheck_errors']))
        self.assertEqual(len(report['gates']), 6)


if __name__ == '__main__':
    unittest.main()
