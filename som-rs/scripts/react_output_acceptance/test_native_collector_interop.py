#!/usr/bin/env python3
"""Finite synthetic collector controls. These tests grant no native authority.

They exercise actual source functions with explicit test resource/Context doubles.
They do not qualify Core origin, the API39/40 payload, native primitives, product
children, twelve-step completion, policy, receipts, SourceSeal or M12 acceptance.
The old collector/entry/callback/startup fixtures are preserved byte-for-byte.
"""
from __future__ import annotations

import contextlib
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import FunctionType, ModuleType
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load_control_source(name, filename):
    specification = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class ControlLease:
    events = None
    group = None
    failure = None
    def borrow(self):
        type(self).events.append('borrow')
        return type(self)()
    def load_private_group(self, name):
        type(self).events.append('group-return')
        if name != 'm6.api.payload':
            raise AssertionError(name)
        return type(self).group
    def close(self):
        type(self).events.append('borrow-close')
        if type(self).failure is not None:
            raise type(self).failure


class ControlGroup:
    events = None
    failure = None
    member_failure = None
    def __getitem__(self, key):
        type(self).events.append('member:' + key)
        raise type(self).member_failure
    def close(self):
        type(self).events.append('group-close')
        if type(self).failure is not None:
            raise type(self).failure


class ControlContext:
    pass


class ControlStep:
    """A dispatch counter, never a source/native owner substitute."""
    def __init__(self, report):
        self.report, self.effects, self.authorizations, self.finishes = report, 0, 0, 0
    def authorize_step(self, step, out, repo):
        self.authorizations += 1
    def log_primitives(self):
        return (os.open, os.close, os.dup, os.dup2, os.fstat, os.stat, os.fsync, os.fdopen, os.geteuid, os.pread, hashlib.sha256)
    def execute_step(self, step):
        self.effects += 1
        print('python-stdout')
        print('python-stderr', file=sys.stderr)
        os.write(1, b'fd1-stdout\n')
        os.write(2, b'fd2-stderr\n')
        print('1 passed, 0 failed')
        return 0
    def finish_step(self, step, receipt):
        self.finishes += 1


class ControlDriverSourceLease:
    events = None
    root = None
    helper_revision = candidate_revision = 'a' * 40
    role_sources = {'entry.m6.collector': 'som-rs/scripts/react_output_acceptance/run_acceptance.py'}
    entry_error = None
    finalized = False
    def execute_entry(self, role, arguments):
        type(self).events.append('entry')
        if type(self).entry_error is not None:
            raise type(self).entry_error
        return 0
    def close(self):
        if not type(self).finalized:
            raise AssertionError('source lease closed before provider')
        type(self).events.append('source-close')


class ControlDriverPythonLease:
    def close(self):
        if not ControlDriverSourceLease.finalized:
            raise AssertionError('Python lease closed before provider')
        ControlDriverSourceLease.events.append('python-close')


class ControlDriverSources:
    root = None
    pins = {}
    def read(self, name):
        return b'explicit synthetic control source data'
    def verify(self):
        if ControlDriverSourceLease.finalized:
            raise AssertionError('source verify after provider release')
        ControlDriverSourceLease.events.append('source-verify')


class ControlDriverContext:
    verify_error = None
    activate_error = None
    def __init__(self, path):
        ControlDriverSourceLease.events.append('Context-construct')
        self.sources = ControlDriverSources()
        self.qa_import_controls = ControlDriverSourceLease()
        self.python_controls = ControlDriverPythonLease()
        self.qa_source_authority = {'source_authority_active': True,
            'admitted_repository_python_sources': ['som-rs/scripts/react_output_acceptance/run_acceptance.py']}
    def activate_python_paths(self, executable):
        ControlDriverSourceLease.events.append('activate')
        if type(self).activate_error is not None:
            raise type(self).activate_error
    def verify(self):
        if ControlDriverSourceLease.finalized:
            raise AssertionError('Context verify after provider release')
        ControlDriverSourceLease.events.append('Context-verify')
        if type(self).verify_error is not None:
            raise type(self).verify_error


def control_driver_finalize(ctx):
    ctx.qa_import_controls.events.append('provider-release')
    type(ctx.qa_import_controls).finalized = True
    return {'private4_closed': True, 'borrow_closed': True, 'product_acceptance_passed': False}


class NativeCollectorInteropControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collector = load_control_source('som_collector_control_subject', 'run_acceptance.py')
        cls.provider = load_control_source('som_provider_control_subject', 'react_api_python_payload.py')
        cls.driver = load_control_source('som_driver_control_subject',
            HERE.parent / 'hosted_readiness/run_full_products.py')

    def test_native_profile_flags_parse_as_paths_and_ordinary_cli_retains_refusal(self):
        arguments = ['--candidate', '/control/candidate', '--out', '/control/out',
                     '--qa-profile', '/control/qa.json', '--api-profile', '/control/api.json']
        with mock.patch.object(self.collector, '_native_route', return_value=True):
            args = self.collector.parse_args(arguments)
        self.assertEqual(args.qa_profile, Path('/control/qa.json'))
        self.assertEqual(args.api_profile, Path('/control/api.json'))
        with mock.patch.object(self.collector, '_native_route', return_value=False), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                self.collector.parse_args(arguments)
        self.assertEqual(caught.exception.code, 2)

    def provider_scope(self, *, group_error=None, borrow_error=None):
        stack = contextlib.ExitStack()
        events = []
        for kind in (ControlLease, ControlGroup):
            stack.enter_context(mock.patch.object(kind, 'events', events))
        stack.enter_context(mock.patch.object(ControlGroup, 'failure', group_error))
        stack.enter_context(mock.patch.object(ControlLease, 'failure', borrow_error))
        member_error = RuntimeError('synthetic member acquisition failure')
        stack.enter_context(mock.patch.object(ControlGroup, 'member_failure', member_error))
        stack.enter_context(mock.patch.object(ControlLease, 'group', ControlGroup()))
        ctx, controls = ControlContext(), ControlLease()
        context_module = ModuleType('synthetic_collector_context')
        context_module._ACTIVE = ctx
        for name, value in (('_NATIVE_OWNER', None), ('_NATIVE_FAILURE', None),
                            ('__som_native_Group__', ControlGroup), ('__som_native_Context__', ControlContext),
                            ('__som_native_context_module__', context_module)):
            stack.enter_context(mock.patch.object(self.provider, name, value, create=True))
        stack.enter_context(mock.patch.object(self.provider, '_native_controls', return_value=controls))
        stack.enter_context(mock.patch.object(self.provider, '_native_profile', return_value=({}, '0' * 64)))
        return stack, ctx, controls, events, member_error

    def test_partial_group_member_failure_closes_group_then_borrow_once_and_preserves_error(self):
        stack, ctx, controls, events, original = self.provider_scope()
        with stack:
            with self.assertRaises(RuntimeError) as caught:
                self.provider._native_acquire(ctx)
            self.assertIs(caught.exception, original)
            self.assertEqual(events, ['borrow', 'group-return', 'member:retained', 'group-close', 'borrow-close'])
            owner = self.provider._NATIVE_OWNER
            self.assertTrue(owner.failed)
            self.assertTrue(owner.closed)
            self.assertEqual(owner.phase, 'closed')
            self.assertEqual([row['name'] for row in owner.cleanup], ['borrow', 'private4'])
            self.assertTrue(all(row['attempted'] and row['closed'] for row in owner.cleanup))
            with mock.patch.object(self.provider, '_native_controls', side_effect=AssertionError('poison gate used')):
                result = self.provider._native_collector_finalize(ctx)
            self.assertTrue(result['private4_closed'])
            self.assertTrue(result['borrow_closed'])
            self.assertEqual(events.count('group-close'), 1)
            self.assertEqual(events.count('borrow-close'), 1)
            self.assertIs(self.provider._NATIVE_OWNER, owner)

    def test_failed_rollback_closes_each_resource_once_and_keeps_both_secondary_errors(self):
        group_error, borrow_error = OSError('group-close'), OSError('borrow-close')
        stack, ctx, controls, events, original = self.provider_scope(
            group_error=group_error, borrow_error=borrow_error)
        with stack:
            with self.assertRaises(RuntimeError) as caught:
                self.provider._native_acquire(ctx)
            self.assertIs(caught.exception, original)
            self.assertEqual(original.__som_native_payload_cleanup_secondary__, (group_error, borrow_error))
            self.assertEqual(events[-2:], ['group-close', 'borrow-close'])
            status = self.provider._native_collector_finalize(ctx)
            self.assertFalse(status['private4_closed'])
            self.assertFalse(status['borrow_closed'])
            self.assertEqual(events.count('group-close'), 1)
            self.assertEqual(events.count('borrow-close'), 1)
            self.assertEqual([row[1] for row in self.provider._NATIVE_OWNER.cleanup_errors],
                             [group_error, borrow_error])

    def test_live_owner_finalizer_uses_actual_context_and_reports_first_close_error(self):
        group_error, borrow_error = OSError('group-close'), OSError('borrow-close')
        stack, ctx, controls, events, original = self.provider_scope(
            group_error=group_error, borrow_error=borrow_error)
        with stack:
            lease, group = ControlLease(), ControlGroup()
            owner = self.provider._NativeOwner(ctx, controls, lease, group, {}, '0' * 64)
            self.provider._NATIVE_OWNER = owner
            self.provider._native_owned_close(owner, 'borrow', lease, ControlLease)
            self.provider._native_owned_close(owner, 'private4', group, ControlGroup)
            owner.phase = 'live'
            with self.assertRaises(ValueError):
                self.provider._native_collector_finalize(ControlContext())
            self.assertEqual(events, [])
            with self.assertRaises(OSError) as caught:
                self.provider._native_collector_finalize(ctx)
            self.assertIs(caught.exception, group_error)
            self.assertEqual(group_error.__som_native_payload_cleanup_secondary__, (borrow_error,))
            self.assertEqual(events, ['group-close', 'borrow-close'])
            self.provider._native_collector_finalize(ctx)
            self.assertEqual(events, ['group-close', 'borrow-close'])

    def log_scope(self, root):
        logs = root / 'logs'; logs.mkdir(mode=0o700)
        receipts = root / 'receipts'; receipts.mkdir(mode=0o700)
        report = root / 'report.json'
        step = {'index': 1, 'step': 'contract', 'argv': [sys.executable, 'control.py'],
                'kind': 'contract', 'report': report}
        return step, ControlStep(report), logs

    def test_native_normal_capture_owns_both_0600_logs_and_captures_python_and_fd_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            step, scope, logs = self.log_scope(root)
            originals = (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__)
            with mock.patch.object(self.collector.subprocess, 'run', side_effect=AssertionError('Python child fallback')):
                receipt = self.collector._native_run_single_step(step, root, root, scope)
            self.assertEqual(scope.effects, 1)
            self.assertEqual((scope.authorizations, scope.finishes), (1, 1))
            self.assertEqual((sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__), originals)
            output, errors = logs / '01-contract.stdout', logs / '01-contract.stderr'
            self.assertIn(b'python-stdout\n', output.read_bytes())
            self.assertIn(b'fd1-stdout\n', output.read_bytes())
            self.assertIn(b'python-stderr\n', errors.read_bytes())
            self.assertIn(b'fd2-stderr\n', errors.read_bytes())
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(errors.stat().st_mode), 0o600)
            self.assertTrue(receipt['passed'])
            self.assertEqual(receipt['exit_code'], 0)
            self.assertEqual(receipt['execution'], 'same-Core-inprocess-entry')
            self.assertFalse(receipt['child_wait_claimed'])

    def test_missing_manifest_owns_empty_logs_and_never_dispatches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            step, scope, logs = self.log_scope(root)
            step['requires_manifest'] = root / 'absent.json'
            receipt = self.collector._native_run_single_step(step, root, root, scope)
            self.assertEqual(scope.effects, 0)
            self.assertEqual((scope.authorizations, scope.finishes), (1, 1))
            self.assertEqual(receipt['judgment'], 'not_run')
            self.assertIsNone(receipt['exit_code'])
            self.assertFalse(receipt['passed'])
            for name in ('01-contract.stdout', '01-contract.stderr'):
                self.assertEqual((logs / name).read_bytes(), b'')
                self.assertEqual(stat.S_IMODE((logs / name).stat().st_mode), 0o600)

    def test_existing_file_and_symlink_refuse_before_dispatch_without_overwrite(self):
        for linked in (False, True):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                step, scope, logs = self.log_scope(root)
                existing = root / 'existing'; existing.write_bytes(b'existing-private-control-canary')
                output = logs / '01-contract.stdout'
                if linked:
                    output.symlink_to(existing)
                else:
                    output.write_bytes(b'existing-private-control-canary')
                with mock.patch.object(self.collector, 'sha_file', side_effect=AssertionError('unowned stream read')):
                    with self.assertRaises(FileExistsError):
                        self.collector._native_run_single_step(step, root, root, scope)
                self.assertEqual(scope.effects, 0)
                self.assertEqual(existing.read_bytes(), b'existing-private-control-canary')
                self.assertEqual(output.read_bytes(), b'existing-private-control-canary')
                self.assertFalse((root / 'receipts/01-contract.json').exists())

    def test_second_log_acquisition_failure_closes_first_fd_and_does_not_replay_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); logs = root / 'logs'; logs.mkdir(mode=0o700)
            first, second = logs / 'x.stdout', logs / 'x.stderr'
            second.write_bytes(b'keep')
            capture = self.collector._NativeStepLogs(first, second)
            with self.assertRaises(FileExistsError):
                with capture:
                    self.fail('capture entered after second open refusal')
            self.assertEqual(capture.files, [])
            self.assertIsNone(capture.directory)
            self.assertEqual(first.read_bytes(), b'')
            self.assertEqual(second.read_bytes(), b'keep')
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)

    def test_body_first_error_and_traceback_survive_stream_restoration_and_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); logs = root / 'logs'; logs.mkdir(mode=0o700)
            originals = (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__)
            original = RuntimeError('synthetic-first-body-error')
            original_tb = None
            try:
                with self.collector._NativeStepLogs(logs / 'x.stdout', logs / 'x.stderr') as capture:
                    capture.capture()
                    try:
                        raise original
                    except RuntimeError:
                        original_tb = sys.exc_info()[2]
                        raise
            except RuntimeError as caught:
                self.assertIs(caught, original)
                trace = caught.__traceback__
                while trace is not None and trace is not original_tb:
                    trace = trace.tb_next
                self.assertIs(trace, original_tb)
            else:
                self.fail('body error was swallowed')
            self.assertEqual((sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__), originals)
            self.assertEqual(capture.files, [])
            self.assertIsNone(capture.directory)


    def driver_scope(self, root, *, entry_error=None, verify_error=None, activate_error=None):
        stack, events = contextlib.ExitStack(), []
        for name, value in (('events', events), ('root', root), ('entry_error', entry_error), ('finalized', False)):
            stack.enter_context(mock.patch.object(ControlDriverSourceLease, name, value))
        stack.enter_context(mock.patch.object(ControlDriverSources, 'root', root))
        stack.enter_context(mock.patch.object(ControlDriverContext, 'verify_error', verify_error))
        stack.enter_context(mock.patch.object(ControlDriverContext, 'activate_error', activate_error))
        controls = ControlDriverSourceLease()
        module = ModuleType('synthetic_driver_context')
        module.Context, module.NativePythonLease = ControlDriverContext, ControlDriverPythonLease
        module.__som_native_source_authority__ = {'source_authority_active': True,
            'admitted_repository_python_sources': [self.driver.COLLECTOR]}
        provider = ModuleType('synthetic_driver_provider')
        provider.__file__ = str(root / self.driver.PROVIDER)
        # This is explicitly a synthetic control function, never native origin.
        finalize = FunctionType(control_driver_finalize.__code__, provider.__dict__)
        provider._native_collector_finalize = finalize
        stack.enter_context(mock.patch.object(self.driver, '_native_controls', return_value=controls))
        stack.enter_context(mock.patch.object(self.driver, 'load_context_module', return_value=module))
        stack.enter_context(mock.patch.object(self.driver, '__som_native_provider__', provider, create=True))
        stack.enter_context(mock.patch.object(self.driver, '__som_native_provider_collector_finalize__', finalize, create=True))
        data = {'repo': str(root), 'helper_revision': 'a' * 40, 'candidate_revision': 'a' * 40,
                'stage': 'control-only', 'shard': 'control-only'}
        return stack, data, module, events

    def test_driver_one_context_last_full_verify_then_provider_python_source_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack, data, module, events = self.driver_scope(root)
            with stack:
                code = self.driver._native_execute_collector([self.driver.COLLECTOR], root / 'context.json', data, root)
                self.assertEqual(code, 0)
                self.assertEqual(events, ['Context-construct', 'activate', 'entry', 'source-verify',
                                         'Context-verify', 'provider-release', 'python-close', 'source-close'])
                proof = json.loads(next((root / 'checker-postchecks').glob('*.json')).read_bytes())
                self.assertTrue(proof['source_runtime_tools_inputs_unchanged'])
                self.assertFalse(proof['product_acceptance_passed'])
                self.assertEqual(proof['entry_exit_code'], 0)
                self.assertTrue(all(row['completed'] for row in proof['cleanup'].values()))

    def test_driver_first_entry_error_survives_postverify_error_and_all_release_actions(self):
        first, later = RuntimeError('first-entry'), OSError('last-verify')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack, data, module, events = self.driver_scope(root, entry_error=first, verify_error=later)
            with stack:
                with self.assertRaises(RuntimeError) as caught:
                    try:
                        self.driver._native_execute_collector([self.driver.COLLECTOR], root / 'context.json', data, root)
                    except RuntimeError as actual:
                        raised_trace = actual.__traceback__
                        raise
                self.assertIs(caught.exception, first)
                self.assertEqual(first.__som_native_collector_secondary__, (later,))
                self.assertEqual(events[-3:], ['provider-release', 'python-close', 'source-close'])
                self.assertEqual(events.count('Context-construct'), 1)
                self.assertFalse(first.__som_native_collector_postcheck__['source_runtime_tools_inputs_unchanged'])
                trace = raised_trace
                while trace is not None and trace.tb_frame.f_code is not ControlDriverSourceLease.execute_entry.__code__:
                    trace = trace.tb_next
                self.assertIsNotNone(trace)

    def test_driver_activation_error_still_releases_held_provider_before_both_context_leases(self):
        first = RuntimeError('activation-before-session')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack, data, module, events = self.driver_scope(root, activate_error=first)
            with stack:
                with self.assertRaises(RuntimeError) as caught:
                    self.driver._native_execute_collector([self.driver.COLLECTOR], root / 'context.json', data, root)
                self.assertIs(caught.exception, first)
                self.assertNotIn('entry', events)
                self.assertEqual(events[-3:], ['provider-release', 'python-close', 'source-close'])
                self.assertEqual(events.count('Context-construct'), 1)
                self.assertFalse(first.__som_native_collector_postcheck__['entry_executed'])


if __name__ == '__main__':
    unittest.main()
