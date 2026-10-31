#!/usr/bin/env python3
"""Isolated source-body controls only. Native Core/source authority is NOT tested.

The control loader compiles exact definitions from the selected source as data.
Its new CodeTypes are NOT the Core-owned original CodeTypes. No control result
qualifies constructor/caller/native origin, wheel payload, process waits or M12.
No original test method or host ProcessTracker body is changed.
"""
from __future__ import annotations

import ast
import argparse
import contextlib
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
import tempfile

REPO = Path(__file__).resolve().parents[3]
TS = 'som-rs/behavior/typescript/host.py'
BASE = 'som-rs/behavior/hosts.py'


def control_definitions(relative, names, values):
    source = (REPO / relative).read_bytes()
    tree = ast.parse(source)
    nodes = [node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == '__future__']
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    if len(selected) != len(names):
        raise AssertionError('exact source definitions missing')
    nodes.extend(selected)
    namespace = {'__name__': 'synthetic_host_body_controls', '__file__': str(REPO / relative), **values}
    # Only a future selected GitHub test run executes this. It is not origin proof.
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(REPO / relative), 'exec'), namespace)
    return namespace


class ControlProcess:
    pid = 321
    returncode = None
    def __init__(self, code):
        self.returncode, self.waits = code, 0
    def poll(self):
        return self.returncode
    def wait(self, **kwargs):
        self.waits += 1
        raise AssertionError('unexpected control wait')


class ControlProcPath:
    raw = ''
    owner = 1000
    reads = 0
    def __init__(self, value):
        self.value = str(value)
    def __truediv__(self, value):
        return type(self)(self.value + '/' + str(value))
    def stat(self):
        return SimpleNamespace(st_uid=type(self).owner)
    def read_text(self, **kwargs):
        type(self).reads += 1
        return type(self).raw


class NativeApiHostBodyControls(unittest.TestCase):
    def test_proc_identity_parser_reaches_live_owner_and_rejects_zombie_wrong_uid_and_short_data(self):
        namespace = control_definitions(TS, {'_native_process_identity'}, {
            'sys': SimpleNamespace(platform='linux'), 'pathlib': SimpleNamespace(Path=ControlProcPath),
            'os': SimpleNamespace(geteuid=lambda: 1000)})
        target = namespace['_native_process_identity']
        fields = ['S', '10', '321'] + ['0'] * 16 + ['9001']
        with mock.patch.object(ControlProcPath, 'raw', '321 (owned worker) ' + ' '.join(fields)), \
                mock.patch.object(ControlProcPath, 'owner', 1000), mock.patch.object(ControlProcPath, 'reads', 0):
            self.assertEqual(target(321), (9001, 10, 321, 1000))
            self.assertEqual(ControlProcPath.reads, 1)
        for raw, owner in [('321 (owned) Z ' + ' '.join(fields[1:]), 1000),
                           ('321 (owned) ' + ' '.join(fields), 1001), ('321 (short) S 1', 1000)]:
            with self.subTest(raw=raw, owner=owner), mock.patch.object(ControlProcPath, 'raw', raw), \
                    mock.patch.object(ControlProcPath, 'owner', owner):
                self.assertIsNone(target(321))
        self.assertIsNone(target(True))
        self.assertIsNone(target(1))

    def cwd_subject(self, root, *, identity_after=None, code=0, owner='321', observed=None):
        identities = [(9001, 10, 321, os.geteuid()), identity_after or (9001, 10, 321, os.geteuid())]
        calls, identity_calls = [], []
        def identity(pid):
            identity_calls.append(pid)
            return identities[len(identity_calls) - 1]
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=code, stdout='p' + owner + '\nfcwd\nn' + str(observed or root) + '\n')
        namespace = control_definitions(TS, {'_native_verify_process_cwd'}, {
            'pathlib': __import__('pathlib'), 'os': SimpleNamespace(readlink=lambda path: str(root)),
            '_native_process_identity': identity, 'subprocess': SimpleNamespace(run=run),
            '_NATIVE_API_TOOLS': {'lsof': '/usr/bin/lsof'}})
        return namespace['_native_verify_process_cwd'], calls, identity_calls

    def test_linux_cwd_control_reaches_fixed_lsof_and_both_process_identity_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target, calls, identity_calls = self.cwd_subject(root)
            self.assertEqual(target(321, root), (True, str(root)))
            self.assertEqual(identity_calls, [321, 321])
            self.assertEqual(calls, [(['/usr/bin/lsof', '-a', '-p', '321', '-d', 'cwd', '-Fn'],
                                      {'capture_output': True, 'text': True, 'check': False})])

    def test_linux_cwd_control_rejects_changed_process_foreign_lsof_owner_and_nonzero_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for options, expected_reads in [({'identity_after': (9002, 10, 321, os.geteuid())}, 2),
                                             ({'owner': '322'}, 1), ({'code': 1}, 1)]:
                with self.subTest(options=options):
                    target, calls, identity_calls = self.cwd_subject(root, **options)
                    self.assertFalse(target(321, root)[0])
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(len(identity_calls), expected_reads)

    def test_linux_listening_control_requires_only_requested_pid_and_unchanged_identity(self):
        for output, last, expected in [('321\n', (1, 2, 3, 4), True),
                                      ('321\n999\n', (1, 2, 3, 4), False),
                                      ('321\n', (9, 2, 3, 4), False), ('', (1, 2, 3, 4), False)]:
            with self.subTest(output=output, identity_after=last):
                calls = []
                identities = iter([(1, 2, 3, 4), last])
                def run(argv, **kwargs):
                    calls.append((argv, kwargs))
                    return SimpleNamespace(returncode=0, stdout=output)
                namespace = control_definitions(BASE, {'_native_listening'}, {
                    'QA_PORTS': range(18919, 19000), 'FORBIDDEN_PORTS': frozenset((8765, 18765)),
                    '_native_process_identity': lambda pid: next(identities),
                    '_NATIVE_API_TOOLS': {'lsof': '/usr/bin/lsof'}, 'subprocess': SimpleNamespace(run=run)})
                self.assertIs(namespace['_native_listening'](321, 18929), expected)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0], ['/usr/bin/lsof', '-nP', '-a', '-p', '321', '-iTCP:18929', '-sTCP:LISTEN', '-t'])
                calls.clear()
                self.assertFalse(namespace['_native_listening'](321, 8765))
                self.assertEqual(calls, [])

    def tracker_subject(self, verified):
        signals, cwd_checks = [], []
        def verify(pid, cwd):
            cwd_checks.append((pid, cwd))
            return verified, '/control/cwd'
        namespace = control_definitions(TS, {'ProcessTracker'}, {
            'forbidden_pids': lambda: set(), 'verify_process_cwd': verify,
            'os': SimpleNamespace(killpg=lambda *args: signals.append(args)),
            'signal': signal, 'contextlib': contextlib,
            'subprocess': SimpleNamespace(TimeoutExpired=TimeoutError)})
        return namespace['ProcessTracker'](), signals, cwd_checks

    def test_original_tracker_never_signals_or_waits_after_known_reap(self):
        tracker, signals, checks = self.tracker_subject(False)
        process, entry = ControlProcess(0), {'role': 'control', 'stopped': False}
        self.assertEqual(tracker.stop(process, Path('/control/cwd'), entry), 0)
        self.assertEqual(signals, [])
        self.assertEqual(checks, [])
        self.assertEqual(process.waits, 0)
        self.assertTrue(entry['stopped'])

    def test_original_tracker_refuses_signal_when_actual_cwd_check_fails(self):
        tracker, signals, checks = self.tracker_subject(False)
        process, entry = ControlProcess(None), {'role': 'control', 'stopped': False}
        with self.assertRaisesRegex(RuntimeError, 'refusing to signal PID'):
            tracker.stop(process, Path('/control/cwd'), entry)
        self.assertEqual(len(checks), 1)
        self.assertEqual(signals, [])
        self.assertEqual(process.waits, 0)
        self.assertFalse(entry['stopped'])

    def test_native_python_path_uses_only_held_actual_payload_site(self):
        namespace = control_definitions(TS, {'_python_path'}, {
            '_NATIVE_API': True, '_NATIVE_API_TOOLS': {'site': '/control/api-venv/site-packages'}})
        self.assertEqual(namespace['_python_path'](Path('/control/product')),
                         '/control/product:/control/api-venv/site-packages')

    def test_native_gate_cache_refusal_reaches_exact_guard_before_output_or_child_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            effects = []
            namespace = control_definitions('som-rs/tests/react-output-v1/run_gates.py', {'main'}, {
                'argparse': argparse, 'pathlib': __import__('pathlib'), 'REPO': root,
                '_NATIVE_API': True, '_NATIVE_CACHE': root / 'actual-qualified-cache',
                'shutil': SimpleNamespace(copytree=lambda *args, **kwargs: effects.append('copy'))})
            work = root / 'must-stay-absent'
            with mock.patch.object(sys, 'argv', ['control', str(root / 'product'), str(work),
                                                 '--cache', str(root / 'different-cache')]):
                with self.assertRaisesRegex(ValueError, 'native gate cache differs from actual fixed tool source'):
                    namespace['main']()
            self.assertFalse(work.exists())
            self.assertEqual(effects, [])


if __name__ == '__main__':
    unittest.main()
