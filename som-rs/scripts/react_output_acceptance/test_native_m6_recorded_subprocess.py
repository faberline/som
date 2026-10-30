"""GitHub-only finite recorder protocol, with actual Core and Context guard source.

Transfer/admission and Context construction are explicit protocol seams.
Only real nonPython /bin/sh children run. No runtime, tool, API or39/40 gate passes.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import CodeType, FunctionType, ModuleType
import unittest
from unittest import mock

if not (os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
        and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise RuntimeError('GitHub Linux isolated interpreter required')

HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
BASE = 'som-rs/scripts/hosted_readiness/'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
OWNER = 'som-rs/scripts/react_output_acceptance/native_recording_owner.py'
FACADE = 'som-rs/scripts/react_output_acceptance/native_recorded_subprocess.py'
ROLE = 'm6.api.recording.subprocess'
OWNER_ROLE = 'm6.api.recording.owner'
PINS = {
    'som-rs/scripts/hosted_readiness/native_active_controls.py': 'eb0f804a8aa7de0857213d34478adc869fe51f7259c5552439dca109e4e6a038',
    'som-rs/scripts/hosted_readiness/native_qa_authority.py': '024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1',
    CONTEXT: '7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440',
    BASE + 'test_native_active_controls.py': '1a5852fb1693bcb921fcb19e9a20fb8281751e85f6f0ea2805b88323d230a201',
    OWNER: '394290413413a260f85fbcad1989b4f81cab8158dc4896fdff9bee981bd3d8ad',
    FACADE: 'baff542d8ca4eeee0e49483c2aed081ec586754438194f0c9ce5d0b4d3e6ad2a',
}


def raw(path):
    data = (REPO / path).read_bytes()
    if hashlib.sha256(data).hexdigest() != PINS[path]:
        raise AssertionError('exact recorder fixture dependency differs: ' + path)
    return data


protocol_path = BASE + 'test_native_active_controls.py'
protocol = ModuleType('_som_m6_recording_actual_core_protocol')
protocol.__file__, protocol.__package__ = str(REPO / protocol_path), ''
sys.modules[protocol.__name__] = protocol
# Exact same-head fixture import under -I. No sys.path or production loader change.
# PINS is populated in the immutable final source before GitHub executes this file.


def initialize_protocol():
    exec(compile(raw(protocol_path), protocol.__file__, 'exec', dont_inherit=True), protocol.__dict__)


initialize_protocol()


class RecordingTransfer(protocol.ProtocolTransfer):
    def __init__(self, fixture):
        super().__init__(fixture)
        self.revision = fixture.revision
        self.admission.update(helper_revision=self.revision, candidate_revision=self.revision)


class RecordingFixture(protocol.Fixture):
    def source_module(self, name, path):
        return self.synthetic_seed(name, path, raw(path))

    def __init__(self, case):
        self.token = self.facade = self.context_module = self.ctx = self.parent_python = None
        self.original_run, self.original_popen = subprocess.run, subprocess.Popen
        caller = 'def run(controls):\n    return controls.load_module("m6.api.recording.subprocess")\n'
        with mock.patch.object(protocol, 'ProtocolTransfer', RecordingTransfer):
            super().__init__(case, caller)
        self.context_module = self.domain.load_known('runtime.context', self.domain.prepare('runtime.context'), None)
        ctx = object.__new__(self.context_module.Context)
        self.ctx = ctx
        ctx.repo, ctx.helper_revision, ctx.candidate_revision = self.root, self.revision, self.revision
        ctx.qa_mode, ctx.qa_source_authority = 'native-qa-v1', self.transfer.admission
        ctx.work = self.root / 'work'; ctx.work.mkdir()
        self.out = ctx.work / 'recording'; self.out.mkdir()
        ctx.path = self.root / 'context.json'
        ctx.data = {'fixture': 'constructor bypass; no runtime or product authority'}
        ctx.raw = json.dumps(ctx.data).encode(); ctx.path.write_bytes(ctx.raw)
        ctx.sources = self.context_module.SourceBytes(self.root, self.revision)
        ctx.qa_import_controls = self.controls.borrow()
        self.context_module._ACTIVE = ctx
        self.stack.enter_context(mock.patch.dict(os.environ, {self.context_module.ENV: str(ctx.path)}))
        self.parent_python = self.context_module.install_python_controls(ctx)
        ctx.python_controls = self.parent_python
        self.guard = subprocess.Popen
        self.facade = self.run()
        self.token = self.facade._open(ctx, self.out)

    def add_role(self, role, path, body, **kwargs):
        row = super().add_role(role, path, body, **kwargs)
        if role != 'm6.api.producer':
            return row
        # Explicit even though this caller currently performs only load_module.
        self.rows['caller']['bindings'] = {'__som_native_active_controls__': {'kind': 'active-controls'}}
        super().add_role('runtime.context', CONTEXT, raw(CONTEXT), module='_som_m6_recording_actual_context',
            bindings={'__som_native_active_controls__': {'kind': 'active-controls'}})
        super().add_role(OWNER_ROLE, OWNER, raw(OWNER), module='_som_m6_api_recording_owner')
        bindings = {'__som_native_active_controls__': {'kind': 'active-controls'},
            '__som_native_owner_module__': {'kind': 'role-module', 'role': OWNER_ROLE},
            '__som_native_context_module__': {'kind': 'role-module', 'role': 'runtime.context'}}
        for name in ('Recorder', '_ScopeState', '_ChildState', '_RecordingErrors', '_KernelWaitState'):
            alias = {'_ScopeState': 'ScopeState', '_ChildState': 'ChildState',
                     '_RecordingErrors': 'RecordingErrors', '_KernelWaitState': 'KernelWaitState'}.get(name, name)
            bindings['__som_native_' + alias + '__'] = {'kind': 'role-export', 'role': OWNER_ROLE, 'attribute': name}
        for name in ('Context', 'SourceBytes', 'NativePythonControls', 'NativePythonLease'):
            bindings['__som_native_' + name + '__'] = {'kind': 'role-export', 'role': 'runtime.context', 'attribute': name}
        bindings['__som_native_context_getter__'] = {'kind': 'role-export', 'role': 'runtime.context', 'attribute': 'context'}
        super().add_role(ROLE, FACADE, raw(FACADE), module='_som_m6_api_recorded_subprocess',
            bindings=bindings, callers=[{'role': 'caller', 'function': 'run'}])
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        def git(*args):
            return subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
                env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        git('init', '--quiet'); git('add', '--', '.')
        git('-c', 'user.name=Recorder protocol', '-c', 'user.email=fixture@example.invalid',
            '-c', 'core.hooksPath=/dev/null', 'commit', '--quiet', '-m', 'synthetic recorder source')
        self.revision = git('rev-parse', 'HEAD').decode().strip()
        return row

    def close_scope(self, primary=None):
        if self.token is not None and not self.token.closed:
            self.facade._close(self.token, primary)

    def finish(self):
        first = None
        try:
            self.close_scope()
        except BaseException as error:
            first = error
        if self.parent_python is not None and not self.parent_python.closed:
            try:
                self.parent_python.close()
            except BaseException as error:
                first = first or error
        if self.ctx is not None:
            if self.context_module._ACTIVE is self.ctx:
                self.context_module._ACTIVE = None
            lease = getattr(self.ctx, 'qa_import_controls', None)
            if lease is not None and not lease._closed:
                lease.close()
        if hasattr(self, 'domain'):
            super().finish()
        if first is not None:
            raise first


class NativeRecordedSubprocessTests(unittest.TestCase):
    def test_actual_Core_Context_guard_binary_and_text_CP_and_guard_identity(self):
        f = RecordingFixture(self)
        for text in (False, True):
            done = f.facade.run(['/bin/sh', '-c', 'printf alpha; printf beta >&2'], capture_output=True, text=text)
            self.assertIs(type(done), subprocess.CompletedProcess)
            self.assertEqual((done.returncode, done.stdout, done.stderr),
                (0, 'alpha', 'beta') if text else (0, b'alpha', b'beta'))
            self.assertIs(subprocess.Popen, f.guard); self.assertIs(subprocess.run, f.original_run)
        f.close_scope()
        proof = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
        self.assertEqual(len(proof['children']), 2)
        self.assertTrue(all(row['wait_completed'] and row['exit_code'] == 0 for row in proof['children']))
        self.assertTrue(all(row['requested_argv'] == row['actual_argv'] and row['Python_startup_receipt'] is None
                            for row in proof['children']))
        self.assertFalse(f.parent_python.closed); self.assertFalse(f.domain.closed)
        self.assertIs(subprocess.Popen, f.guard)

    def test_actual_nonzero_check_uses_actual_CalledProcessError_and_streams(self):
        f = RecordingFixture(self)
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            f.facade.run(['/bin/sh', '-c', 'printf no; printf bad >&2; exit 7'], check=True, capture_output=True)
        self.assertEqual((caught.exception.returncode, caught.exception.stdout, caught.exception.stderr), (7, b'no', b'bad'))
        f.close_scope(caught.exception)
        self.assertTrue(f.token.closed)

    def test_actual_timeout_partial_output_kill_terminal_wait_and_original_trace(self):
        f = RecordingFixture(self)
        for text in (False, True):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                f.facade.run(['/bin/sh', '-c', 'printf partial; printf err >&2; exec sleep 30'],
                             capture_output=True, text=text, timeout=0.5)
            first = caught.exception
            self.assertIs(type(first.stdout), bytes); self.assertIs(type(first.stderr), bytes)
            self.assertIn(b'partial', first.stdout); self.assertIn(b'err', first.stderr)
            row = f.token.children[-1]
            self.assertTrue(row.wait_completed)
            self.assertEqual(row.exit_code, -signal.SIGKILL)
            self.assertTrue(all(stream is None or stream.closed for name, stream in row.streams))
            self.assertIn('_bounded_run', [item.tb_frame.f_code.co_name for item in traceback_nodes(first.__traceback__)])
        f.close_scope(first)
        self.assertTrue(f.token.closed)

    def test_actual_direct_Popen_STDOUT_wait_and_late_owner_not_overwritten(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'printf x; printf y >&2'],
            stdout=f.facade.PIPE, stderr=f.facade.STDOUT)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual((stdout, stderr), (b'xy', None))
        original = f.token.children[0].wait_original
        later = lambda **kwargs: self.fail('later owner must not run')
        process.wait = later
        primary = RuntimeError('existing target failure')
        f.close_scope(primary)
        self.assertIs(process.wait, later)
        self.assertTrue(any(stage == 'recording-wait-restore' for stage, error, trace in primary.__som_m6_recording_secondary__))
        process.wait = original
        process.stdout.close()

    def test_actual_observation_writer_errors_keep_original_strings_and_live_error_objects(self):
        f = RecordingFixture(self)
        ambient = RuntimeError('unrelated surrounding error')
        try:
            raise ambient
        except RuntimeError:
            f.facade._recording(f.token).errors.append('ToolVersionFailed')
        self.assertEqual(list(f.token.refs['recorder'].errors), ['ToolVersionFailed'])
        self.assertEqual(f.token.observation_errors, [])
        first, second = OSError('first raw save failure'), OSError('second raw save failure')
        write = Path.write_bytes
        def failed(path, data):
            if path.name == 'os-0001.stdout': raise first
            if path.name == 'os-0001.stderr': raise second
            return write(path, data)
        with mock.patch.object(Path, 'write_bytes', new=failed):
            f.token.refs['recorder'].command(['/bin/sh', '-c', 'printf saved'])
        self.assertEqual(list(f.token.refs['recorder'].errors), ['ToolVersionFailed', 'OSError', 'OSError'])
        self.assertIs(f.token.observation_errors[0][0], first)
        self.assertIs(f.token.observation_errors[1][0], second)
        with self.assertRaises(OSError) as caught: f.close_scope()
        self.assertIs(caught.exception, first)
        self.assertIs(first.__som_m6_recording_secondary__[0][1], second)

    def test_close_preserves_active_body_error_and_all_evidence_secondary(self):
        f = RecordingFixture(self)
        f.facade.run(['/bin/sh', '-c', 'exit 0'], capture_output=True)
        first, later = RuntimeError('original body failure'), OSError('native evidence save failed')
        opened = Path.open
        def fail(path, *args, **kwargs):
            if path.name == 'native-recorded-processes.json': raise later
            return opened(path, *args, **kwargs)
        try:
            raise first
        except RuntimeError as actual:
            trace = actual.__traceback__
            with mock.patch.object(Path, 'open', new=fail): f.facade._close(f.token)
            self.assertIs(actual, first); self.assertIs(first.__traceback__, trace)
        self.assertTrue(f.token.closed)
        self.assertIs(first.__som_m6_recording_secondary__[-1][1], later)
        self.assertEqual(f.token.refs, {})

    def test_foreign_Context_method_borrow_binding_thread_and_reentry_refuse_without_hooks(self):
        f = RecordingFixture(self); calls = []
        f.close_scope()
        f.out = f.ctx.work / 'second'; f.out.mkdir()
        class Foreign:
            def __getattribute__(self, name): calls.append(name); raise AssertionError('foreign hook')
        with self.assertRaises(ValueError): f.facade._open(Foreign(), f.out)
        self.assertEqual(calls, [])
        f.token = f.facade._open(f.ctx, f.out)
        f.ctx.python_argv = lambda *args, **kwargs: calls.append('shadow')
        try:
            with self.assertRaisesRegex(ValueError, 'method shadow'): f.facade.run(['/bin/sh', '-c', 'exit 0'])
        finally: del f.ctx.python_argv
        borrow = f.token.refs['borrow']; f.token.refs['borrow'] = Foreign()
        try:
            with self.assertRaises(ValueError): f.facade.run(['/bin/sh', '-c', 'exit 0'])
        finally: f.token.refs['borrow'] = borrow
        self.assertEqual(calls, [])
        f.token.phase = 'run'
        try:
            with self.assertRaisesRegex(ValueError, 'reentry'): f.facade.run(['/bin/sh', '-c', 'exit 0'])
        finally: f.token.phase = None
        errors = []
        def cross_thread():
            try: f.facade.run(['/bin/sh', '-c', 'exit 0'])
            except BaseException as error: errors.append(error)
        thread = threading.Thread(target=cross_thread); thread.start(); thread.join(timeout=10)
        self.assertFalse(thread.is_alive()); self.assertEqual(len(errors), 1)
        self.assertIs(type(errors[0]), ValueError)
        alias = f.facade.__som_native_Recorder__; f.facade.__som_native_Recorder__ = Foreign
        try:
            with self.assertRaises(ValueError): f.facade.run(['/bin/sh', '-c', 'exit 0'])
        finally: f.facade.__som_native_Recorder__ = alias
        self.assertEqual(calls, []); self.assertEqual(f.token.children, [])
        f.close_scope()
        with self.assertRaises(ValueError): f.facade.run(['/bin/sh', '-c', 'exit 0'])

    def test_wrong_actual_argv_refuses_but_owned_cleanup_waits_and_preserves_first_error(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'])
        process.args = ['/wrong/actual/argv']
        with self.assertRaisesRegex(ValueError, 'argv changed'): process.wait(timeout=0)
        first = RuntimeError('original target refusal')
        f.close_scope(first)
        self.assertIsNotNone(process.returncode)
        data = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
        self.assertTrue(data['children'][0]['wait_completed'])
        self.assertEqual(data['children'][0]['actual_argv'], ['/bin/sh', '-c', 'exec sleep 30'])
        self.assertTrue(any(stage == 'recording-child-postcheck' for stage, error, trace in first.__som_m6_recording_secondary__))

    def test_live_public_method_shadows_cannot_block_owned_kill_wait_or_replace_body_first(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'], stdout=f.facade.PIPE)
        row = f.token.children[0]
        calls = []
        def foreign(*args, **kwargs): calls.append('foreign'); raise AssertionError('foreign child hook')
        process.kill = process.wait = process.poll = foreign
        first = RuntimeError('body failed before cleanup')
        try:
            raise first
        except RuntimeError as actual:
            trace = actual.__traceback__
            f.facade._close(f.token)
            self.assertIs(actual, first); self.assertIs(actual.__traceback__, trace)
        self.assertEqual(calls, [])
        self.assertTrue(row.wait_completed); self.assertEqual(row.exit_code, -signal.SIGKILL)
        self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assertIs(process.kill, foreign); self.assertIs(process.wait, foreign)
        self.assertTrue(all(stream is None or stream.closed for name, stream in row.streams))
        stages = [stage for stage, error, trace in first.__som_m6_recording_secondary__]
        self.assertIn('recording-child-postcheck', stages)
        self.assertIn('recording-missing-wait', stages)
        self.assertIn('recording-wait-restore', stages)
        proof = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
        self.assertTrue(proof['children'][0]['wait_completed'])
        self.assertIs(subprocess.Popen, f.guard); self.assertIs(subprocess.run, f.original_run)
        del process.kill, process.wait, process.poll

    def test_tuple_or_foreign_secondary_metadata_keeps_body_first_and_prior_evidence_without_hooks(self):
        f = RecordingFixture(self)
        calls = []
        class Foreign:
            def __getattribute__(self, name): calls.append(name); raise AssertionError('foreign metadata hook')
            def __iter__(self): calls.append('iterate'); raise AssertionError('foreign metadata iterator')
        prior_error = OSError('legitimate earlier evidence')
        prior_row = ('prior-evidence', prior_error, None)
        priors = [(prior_row,), Foreign()]
        for index, previous in enumerate(priors):
            if index:
                f.out = f.ctx.work / 'secondary-case'; f.out.mkdir()
                f.token = f.facade._open(f.ctx, f.out)
            f.facade.run(['/bin/sh', '-c', 'printf actual'], capture_output=True)
            first, later = RuntimeError('actual body first'), OSError('actual evidence save failure')
            first.__som_m6_recording_secondary__ = previous
            previous_record = {'legitimate': 'previous record metadata'}
            first.__som_m6_recording_secondary_record__ = previous_record
            opened = Path.open
            def fail(path, *args, **kwargs):
                if path.name == 'native-recorded-processes.json': raise later
                return opened(path, *args, **kwargs)
            source_borrow = f.token.refs['borrow']; python_borrow = f.token.refs['python_borrow']
            try:
                raise first
            except RuntimeError as actual:
                trace = actual.__traceback__
                with mock.patch.object(Path, 'open', new=fail): f.facade._close(f.token)
                self.assertIs(actual, first); self.assertIs(first.__traceback__, trace)
            rows = first.__som_m6_recording_secondary__
            self.assertIs(type(rows), list)
            self.assertTrue(any(error is later for stage, error, trace in rows))
            record = first.__som_m6_recording_secondary_record__
            self.assertIs(type(record), dict); self.assertIs(record['previous_value'], previous)
            self.assertIs(record['previous_record'], previous_record)
            self.assertIs(record['primary'], first); self.assertIs(record['rows'], rows)
            if index == 0: self.assertIs(rows[0], prior_row)
            self.assertEqual(calls, [])
            self.assertTrue(source_borrow._closed); self.assertTrue(python_borrow.closed)
            self.assertFalse(f.controls._closed); self.assertIs(subprocess.Popen, f.guard)
            self.assertEqual(f.token.refs, {})

    def test_original_callback_and_pre_registration_adoption_failures_still_kill_wait_and_close_pipes(self):
        f = RecordingFixture(self)
        f.close_scope()
        f.out = f.ctx.work / 'callback-failure'; f.out.mkdir()
        qa = {'python': '/bin/sh', 'node': '/no-node', 'pg_bin': '/no-pg', 'lsof': '/no-lsof', 'ps': '/bin/ps'}
        f.token = f.facade._open(f.ctx, f.out, qa)
        first, later = OSError('original Recorder cwd capture failed'), OSError('adoption cwd capture failed')
        with mock.patch.object(Path, 'cwd', side_effect=[first, later]) as cwd:
            with self.assertRaises(OSError) as caught:
                f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'], stdout=f.facade.PIPE, stderr=f.facade.PIPE)
            self.assertEqual(cwd.call_count, 2)
        self.assertIs(caught.exception, first)
        original = f.facade.__som_native_Recorder__.popen
        frame = next(item.tb_frame for item in traceback_nodes(first.__traceback__)
                     if item.tb_frame.f_code is original.__code__ and item.tb_frame.f_globals is original.__globals__)
        process = frame.f_locals['process']
        self.assertIs(type(process), f.guard); self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assertTrue(process.stdout.closed); self.assertTrue(process.stderr.closed)
        self.assertEqual(f.token.children, [])
        self.assertTrue(any(error is later for stage, error, trace in first.__som_m6_recording_secondary__))
        proof = f.token.pre_registration_cleanup[-1]
        self.assertEqual(proof['branch'], 'recorder-callback'); self.assertEqual(proof['pid'], process.pid)
        self.assertTrue(proof['wait_completed']); self.assertEqual(proof['exit_code'], -signal.SIGKILL)
        self.assertEqual(proof['streams_closed'], {'stdout': True, 'stderr': True})
        f.close_scope(first)
        payload = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
        self.assertEqual(payload['children'], [])
        self.assertEqual(payload['pre_registration_cleanup'], [proof])

    def test_successful_original_Recorder_then_pre_registration_failure_closes_all_created_pipes(self):
        f = RecordingFixture(self)
        first = OSError('adoption cwd capture failed after original Recorder returned')
        with mock.patch.object(Path, 'cwd', side_effect=first) as cwd:
            with self.assertRaises(OSError) as caught:
                f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'], stdout=f.facade.PIPE, stderr=f.facade.PIPE)
            self.assertEqual(cwd.call_count, 1)
        self.assertIs(caught.exception, first)
        original = f.facade.Popen
        frame = next(item.tb_frame for item in traceback_nodes(first.__traceback__)
                     if item.tb_frame.f_code is original.__code__ and item.tb_frame.f_globals is original.__globals__)
        process = frame.f_locals['process']
        self.assertIs(type(process), f.guard); self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assertTrue(process.stdout.closed); self.assertTrue(process.stderr.closed)
        self.assertEqual(f.token.children, [])
        proof = f.token.pre_registration_cleanup[-1]
        self.assertEqual(proof['branch'], 'adoption'); self.assertEqual(proof['pid'], process.pid)
        self.assertTrue(proof['wait_completed']); self.assertEqual(proof['exit_code'], -signal.SIGKILL)
        self.assertEqual(proof['streams_closed'], {'stdout': True, 'stderr': True})
        f.close_scope(first)
        payload = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
        self.assertEqual(payload['children'], [])
        self.assertEqual(payload['pre_registration_cleanup'], [proof])

    def test_fully_waited_child_public_pid_mutation_refuses_but_ledger_uses_captured_pid(self):
        f = RecordingFixture(self)
        f.facade.run(['/bin/sh', '-c', 'exit 0'], capture_output=True)
        row = f.token.children[0]; process = row.process; original_pid = row.pid
        self.assertTrue(row.wait_completed); self.assertEqual(row.exit_code, 0)
        process.pid = original_pid + 1000000
        first = RuntimeError('actual target body first')
        try:
            try:
                raise first
            except RuntimeError as actual:
                trace = actual.__traceback__
                with mock.patch.object(os, 'kill', side_effect=AssertionError('waited child must never signal')) as kill:
                    f.facade._close(f.token)
                    kill.assert_not_called()
                self.assertIs(actual, first); self.assertIs(first.__traceback__, trace)
            payload = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
            self.assertEqual(payload['children'][0]['pid'], original_pid)
            self.assertNotEqual(payload['children'][0]['pid'], process.pid)
            self.assertTrue(any(stage == 'recording-child-postcheck' and 'PID changed' in str(error)
                                for stage, error, trace in first.__som_m6_recording_secondary__))
        finally:
            process.pid = original_pid

    def test_actual_Core_binding_postcheck_failure_still_unwinds_intact_nonowning_source_borrow(self):
        f = RecordingFixture(self)
        f.facade.run(['/bin/sh', '-c', 'exit 0'], capture_output=True)
        borrow, python_borrow = f.token.refs['borrow'], f.token.refs['python_borrow']
        original_close = f.token.refs['source_close']
        owner = f.facade.__som_native_owner_module__; original = owner.Recorder
        first = RuntimeError('original body error before source postcheck')
        owner.Recorder = object
        try:
            with self.assertRaises(ValueError): f.controls.verify_installed()
            try:
                raise first
            except RuntimeError as actual:
                trace = actual.__traceback__
                f.facade._close(f.token)
                self.assertIs(actual, first); self.assertIs(first.__traceback__, trace)
            self.assertTrue(borrow._closed); self.assertTrue(python_borrow.closed)
            self.assertFalse(f.controls._closed); self.assertFalse(f.domain.closed)
            self.assertIs(original_close.__self__, borrow)
            self.assertIs(original_close.__func__, type(borrow).close)
            self.assertTrue(any(stage == 'recording-postcheck' for stage, error, trace in first.__som_m6_recording_secondary__))
            self.assertFalse(any(stage == 'recording-source-borrow-owner' for stage, error, trace in first.__som_m6_recording_secondary__))
            self.assertIs(subprocess.Popen, f.guard); self.assertEqual(f.token.refs, {})
        finally:
            owner.Recorder = original
        f.controls.verify_installed()

    def test_changed_global_kill_and_signal_never_run_foreign_hook_but_owned_cleanup_uses_capture(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'])
        row = f.token.children[0]; original_signal = signal.SIGKILL
        calls = []
        def foreign(*args, **kwargs): calls.append((args, kwargs)); raise AssertionError('foreign signal hook')
        first = RuntimeError('original target body before primitive replacement')
        try:
            raise first
        except RuntimeError as actual:
            trace = actual.__traceback__
            with mock.patch.object(os, 'kill', new=foreign), mock.patch.object(signal, 'SIGKILL', signal.SIGTERM):
                f.facade._close(f.token)
            self.assertIs(actual, first); self.assertIs(first.__traceback__, trace)
        self.assertEqual(calls, [])
        self.assertTrue(row.wait_completed); self.assertEqual(row.exit_code, -original_signal)
        self.assertEqual(process.returncode, -original_signal)
        missing = next(error for stage, error, trace in first.__som_m6_recording_secondary__
                       if stage == 'recording-missing-wait')
        self.assertTrue(any(stage == 'recorded-cleanup-primitive-owner'
                            for stage, error, trace in missing.__som_m6_recording_secondary__))
        payload = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
        self.assertTrue(payload['children'][0]['wait_completed'])
        self.assertIs(subprocess.Popen, f.guard); self.assertIs(subprocess.run, f.original_run)

    def test_changed_guard_private_wait_refuses_without_hook_or_false_terminal_wait_then_restores(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'])
        row = f.token.children[0]
        calls = []
        def foreign_wait(*args, **kwargs): calls.append('foreign wait'); return 0
        first = RuntimeError('original target body before private class change')
        type.__setattr__(f.guard, '_wait', foreign_wait)
        try:
            try:
                raise first
            except RuntimeError as actual:
                trace = actual.__traceback__
                f.facade._close(f.token)
                self.assertIs(actual, first); self.assertIs(first.__traceback__, trace)
            self.assertEqual(calls, [])
            self.assertFalse(row.wait_completed); self.assertIsNone(row.exit_code)
            payload = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
            self.assertFalse(payload['children'][0]['wait_completed'])
            self.assertIsNone(payload['children'][0]['exit_code'])
            missing = next(error for stage, error, trace in first.__som_m6_recording_secondary__
                           if stage == 'recording-missing-wait')
            self.assertTrue(any(stage == 'recorded-cleanup-owner' and 'class changed' in str(error)
                                for stage, error, trace in missing.__som_m6_recording_secondary__))
        finally:
            type.__delattr__(f.guard, '_wait')
            self.assertEqual(process.pid, row.pid)
            process.kill()
            self.assertEqual(row.wait_original(timeout=10), -signal.SIGKILL)
        self.assertEqual(calls, [])

    def test_same_Python_close_function_with_foreign_code_is_not_invoked_or_claimed_closed(self):
        f = RecordingFixture(self)
        f.facade.run(['/bin/sh', '-c', 'exit 0'], capture_output=True)
        python_borrow, source_borrow = f.token.refs['python_borrow'], f.token.refs['borrow']
        function = f.context_module.NativePythonLease.close; original_code = function.__code__
        def foreign(self):
            self.__dict__['foreign_close_executed'] = True
            raise AssertionError('foreign Python close must not run')
        first = RuntimeError('actual body first before close code changed')
        function.__code__ = foreign.__code__
        try:
            with self.assertRaises(ValueError): f.controls.verify_installed()
            try:
                raise first
            except RuntimeError as actual:
                trace = actual.__traceback__
                f.facade._close(f.token)
                self.assertIs(actual, first); self.assertIs(first.__traceback__, trace)
            self.assertFalse(python_borrow.closed); self.assertTrue(source_borrow._closed)
            self.assertNotIn('foreign_close_executed', python_borrow.__dict__)
            self.assertTrue(any(stage == 'recording-python-borrow-owner' and 'invocation changed' in str(error)
                                for stage, error, trace in first.__som_m6_recording_secondary__))
        finally:
            function.__code__ = original_code
            f.controls.verify_installed()
            function(python_borrow)
        self.assertTrue(python_borrow.closed)

    def test_live_returncode_claim_requires_actual_kernel_status_and_real_cleanup(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'])
        row = f.token.children[0]
        kernel = f.token.kernel_waits[0]
        self.assertEqual(os.waitpid(row.pid, os.WNOHANG), (0, 0))
        process.returncode = 0
        first = RuntimeError('body first before false public returncode')
        try:
            raise first
        except RuntimeError as actual:
            trace = actual.__traceback__
            f.facade._close(f.token)
            self.assertIs(actual, first); self.assertIs(actual.__traceback__, trace)
        self.assertTrue(kernel.terminal); self.assertFalse(kernel.unknown)
        self.assertEqual(kernel.claims[0]['returncode'], 0)
        self.assertTrue(row.wait_completed); self.assertEqual(row.exit_code, -signal.SIGKILL)
        self.assertEqual(os.waitstatus_to_exitcode(kernel.status), row.exit_code)
        self.assertTrue(any(item.get('state') == 'terminal' and item['pid'] == row.pid
                            and item['status'] == kernel.status for item in kernel.observations))
        with self.assertRaises(ChildProcessError): os.waitpid(row.pid, os.WNOHANG)
        proof = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
        self.assertTrue(proof['children'][0]['wait_completed'])
        self.assertEqual(proof['kernel_waits'][0]['raw_status'], kernel.status)
        self.assertNotIn('_internal_poll', process.__dict__); self.assertNotIn('_try_wait', process.__dict__)

    def test_foreign_global_waitpid_never_runs_and_captured_kernel_proves_cleanup(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'])
        row = f.token.children[0]; kernel = f.token.kernel_waits[0]
        calls = []
        def foreign(*args, **kwargs): calls.append((args, kwargs)); raise AssertionError('foreign waitpid')
        first = RuntimeError('body first before waitpid replacement')
        try:
            raise first
        except RuntimeError as actual:
            trace = actual.__traceback__
            with mock.patch.object(os, 'waitpid', new=foreign): f.facade._close(f.token)
            self.assertIs(actual, first); self.assertIs(actual.__traceback__, trace)
        self.assertEqual(calls, [])
        self.assertTrue(row.wait_completed); self.assertTrue(kernel.terminal)
        self.assertEqual(row.exit_code, -signal.SIGKILL)
        self.assertEqual(os.waitstatus_to_exitcode(kernel.status), row.exit_code)
        missing = next(error for stage, error, trace in first.__som_m6_recording_secondary__
                       if stage == 'recording-missing-wait')
        self.assertTrue(any(stage == 'recorded-cleanup-kernel-primitive-owner'
                            for stage, error, trace in missing.__som_m6_recording_secondary__))
        with self.assertRaises(ChildProcessError): os.waitpid(row.pid, os.WNOHANG)
        self.assertIs(subprocess.Popen, f.guard); self.assertIs(subprocess.run, f.original_run)

    def test_foreign_wait_lock_never_runs_and_unproved_wait_stays_unknown(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'])
        row = f.token.children[0]; kernel = f.token.kernel_waits[0]
        original = process._waitpid_lock; calls = []
        class ForeignLock:
            def __getattribute__(self, name): calls.append(name); raise AssertionError('foreign lock hook')
        foreign = ForeignLock()
        process._waitpid_lock = foreign
        first = RuntimeError('body first before foreign lock')
        try:
            try:
                raise first
            except RuntimeError as actual:
                trace = actual.__traceback__
                f.facade._close(f.token)
                self.assertIs(actual, first); self.assertIs(actual.__traceback__, trace)
            self.assertEqual(calls, [])
            self.assertFalse(row.wait_completed); self.assertIsNone(row.exit_code)
            self.assertFalse(kernel.terminal); self.assertIsNone(kernel.status)
            self.assertEqual(os.waitpid(row.pid, os.WNOHANG), (0, 0))
            proof = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
            self.assertFalse(proof['children'][0]['wait_completed'])
            self.assertIsNone(proof['kernel_waits'][0]['raw_status'])
            self.assertIs(process.__dict__['_waitpid_lock'], foreign)
        finally:
            process._waitpid_lock = original
            process.kill(); self.assertEqual(row.wait_original(timeout=10), -signal.SIGKILL)
        self.assertEqual(calls, [])

    def test_partial_adoption_foreign_stdout_closes_all_captured_actual_pipes_without_foreign_hook(self):
        f = RecordingFixture(self)
        actual_cwd = Path.cwd(); captured = {}; calls = []
        class ForeignStream:
            def __getattribute__(self, name): calls.append(name); raise AssertionError('foreign stream hook')
        foreign = ForeignStream()
        def cwd(cls):
            frame = sys._getframe(1)
            if frame.f_code is f.facade._adopt.__code__ and not captured:
                process = frame.f_locals['process']
                captured.update(process=process, stdout=process.stdout, stderr=process.stderr)
                process.stdout = foreign
            return actual_cwd
        with mock.patch.object(Path, 'cwd', new=classmethod(cwd)):
            with self.assertRaisesRegex(ValueError, 'actual child pipe object required') as caught:
                f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'], stdout=f.facade.PIPE, stderr=f.facade.PIPE)
        first = caught.exception; process = captured['process']
        self.assertEqual(calls, [])
        self.assertEqual(f.token.children, [])
        self.assertTrue(captured['stdout'].closed); self.assertTrue(captured['stderr'].closed)
        self.assertIs(process.__dict__['stdout'], foreign)
        self.assertEqual(process.returncode, -signal.SIGKILL)
        kernel = f.token.kernel_waits[0]
        self.assertTrue(kernel.terminal); self.assertEqual(os.waitstatus_to_exitcode(kernel.status), -signal.SIGKILL)
        f.close_scope(first)
        proof = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
        self.assertEqual(proof['children'], [])
        self.assertTrue(proof['pre_registration_cleanup'][0]['streams_closed']['stderr'])
        self.assertTrue(proof['pre_registration_cleanup'][0]['streams_closed']['stdout'])
        self.assertEqual(calls, [])
        process.stdout = captured['stdout']

    def test_changed_same_Recorder_popen_code_cannot_authorize_matching_foreign_nested_wait(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'exec sleep 30'])
        row = f.token.children[0]; recorder = f.token.refs['recorder']; kernel = f.token.kernel_waits[0]
        method = f.token.refs['recorder_methods']['popen']; original_code = method.__code__
        original_wait = row.wait_original
        def replacement(self, argv, *args, **kwargs):
            process = argv
            original_wait = process.wait
            def observed_wait(*wait_args, **wait_kwargs):
                self.events.append({'foreign_wait_executed': True, 'pid': process.pid})
                return original_wait(*wait_args, **wait_kwargs)
            return observed_wait
        code = next(item for item in replacement.__code__.co_consts if type(item) is CodeType)
        def cell(value): return (lambda: value).__closure__[0]
        values = {'self': recorder, 'process': process, 'original_wait': original_wait}
        nested = FunctionType(code, method.__globals__, closure=tuple(cell(values[name]) for name in code.co_freevars))
        method.__code__ = replacement.__code__
        row.wait_original = nested
        first = RuntimeError('body first before Recorder code mutation')
        try:
            try:
                raise first
            except RuntimeError as actual:
                trace = actual.__traceback__
                f.facade._close(f.token)
                self.assertIs(actual, first); self.assertIs(actual.__traceback__, trace)
            self.assertFalse(any(event.get('foreign_wait_executed') for event in recorder.events))
            self.assertTrue(row.wait_completed); self.assertEqual(row.exit_code, -signal.SIGKILL)
            self.assertTrue(kernel.terminal); self.assertEqual(os.waitstatus_to_exitcode(kernel.status), row.exit_code)
            self.assertIsNot(process.__dict__.get('wait'), nested)
            self.assertTrue(any(stage == 'recording-wait-restore' for stage, error, trace
                                in first.__som_m6_recording_secondary__))
        finally:
            method.__code__ = original_code
            row.wait_original = original_wait
            process.wait = original_wait
            if process.returncode is None:
                process.kill(); original_wait(timeout=10)
        self.assertFalse(any(event.get('foreign_wait_executed') for event in recorder.events))

    def test_exact_contextlib_decorated_method_closure_is_checked_before_source_verification_spawns(self):
        f = RecordingFixture(self)
        record = f.token.refs['process_decorated']['_on_error_fd_closer']
        cell, original = record['cell'], record['underlying']
        calls = []
        def foreign(*args, **kwargs):
            calls.append('foreign fd closer')
            yield []
        cell.cell_contents = foreign
        try:
            with self.assertRaisesRegex(ValueError, 'decorated process closure changed'):
                f.facade.run(['/bin/sh', '-c', 'exit 0'])
            self.assertEqual(calls, []); self.assertEqual(f.token.children, [])
        finally:
            cell.cell_contents = original
        done = f.facade.run(['/bin/sh', '-c', 'exit 0'])
        self.assertEqual(done.returncode, 0)
        self.assertTrue(f.token.kernel_waits[0].terminal)
        self.assertEqual(calls, [])

    def test_external_reap_remains_ECHILD_unknown_and_never_becomes_exit_zero(self):
        f = RecordingFixture(self)
        process = f.facade.Popen(['/bin/sh', '-c', 'exit 0'])
        row = f.token.children[0]; kernel = f.token.kernel_waits[0]
        found, status = os.waitpid(row.pid, 0)
        self.assertEqual(found, row.pid)
        self.assertFalse(kernel.terminal); self.assertIsNone(process.returncode)
        first = RuntimeError('body first after explicit fixture-owned external reap')
        try:
            try:
                raise first
            except RuntimeError as actual:
                trace = actual.__traceback__
                f.facade._close(f.token)
                self.assertIs(actual, first); self.assertIs(actual.__traceback__, trace)
            self.assertFalse(row.wait_completed); self.assertIsNone(row.exit_code)
            self.assertTrue(kernel.unknown); self.assertFalse(kernel.terminal)
            self.assertIsNone(kernel.status); self.assertIsNone(kernel.code)
            self.assertTrue(any(item.get('error_type') == 'ChildProcessError' for item in kernel.observations))
            proof = json.loads((f.out / 'native-recorded-processes.json').read_bytes())
            self.assertFalse(proof['children'][0]['wait_completed'])
            self.assertIsNone(proof['children'][0]['exit_code'])
            self.assertTrue(proof['kernel_waits'][0]['unknown'])
        finally:
            # This external raw status belongs to the fixture, not the helper.
            process.returncode = os.waitstatus_to_exitcode(status)


def traceback_nodes(trace):
    while trace is not None:
        yield trace
        trace = trace.tb_next


if __name__ == '__main__':
    unittest.main()
