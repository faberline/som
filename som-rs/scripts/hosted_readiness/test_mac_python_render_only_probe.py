"""Whole real-Mac diagnostic fixture. Run only in its GitHub Mac 3.12.11 job.

The class setup is a mandatory real source/build/HTTP run. Controlled children
below exercise only the selected private process gates. They cannot replace
that positive run, establish SourceSeal, or grant product acceptance.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock

# Isolated Python removes the script directory from sys.path. Load the one
# fixed sibling with the normal source loader. The publishing workflow pins
# both files; no search path, alternate source, caller module, or fallback exists.
_LIBRARY = Path(__file__).resolve(strict=True).with_name('mac_python_render_only_probe.py')
_SPEC = importlib.util.spec_from_file_location('mac_python_render_only_probe', _LIBRARY)
if _SPEC is None or _SPEC.loader is None or _SPEC.name in sys.modules:
    raise RuntimeError('fresh fixed sibling source loader required')
probe = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = probe
try:
    _SPEC.loader.exec_module(probe)
except BaseException:
    if sys.modules.get(_SPEC.name) is probe:
        del sys.modules[_SPEC.name]
    raise


# Independent cleanup keeps the actual creator PID and native functions. Subject
# patches and a changed public Popen.pid must not disable fixture cleanup.
import functools
import traceback

_WAITPID = os.waitpid
_KILLPG = os.killpg
_KILL = os.kill
_GETPGID = os.getpgid
_GETSID = os.getsid
_CLOCK = time.monotonic
_SLEEP = time.sleep
_CONTROL = (
    "import os,signal,socket,sys;"
    "mode=sys.argv[1];"
    "signal.signal(signal.SIGTERM,signal.SIG_IGN) if mode=='ignore-term' else None;"
    "s=socket.socket() if mode=='listener' else None;"
    "s.bind(('127.0.0.1',19063)) if s else None;"
    "s.listen(1) if s else None;"
    "print('READY',flush=True);"
    "line=sys.stdin.readline();"
    "os._exit(int(line) if line.strip() else 0)"
)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _select_control_executable(case):
    """Join this run's qualified Framework program before a control Popen.

    Normalized bytes/SHA come from the current passed provision. Unix mode is
    a fresh physical observation, with frozen extract/restore source provenance;
    the provision schema has no per-program mode or historical hash contract.
    """
    keg = Path('/opt/homebrew/Cellar/python@3.12/3.12.11_1')
    member = 'Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python'
    public_launcher = Path('/opt/homebrew/opt/python@3.12/bin/python3.12')
    launcher = public_launcher.resolve(strict=True)
    probe._need(launcher == keg / 'Frameworks/Python.framework/Versions/3.12/bin/python3.12'
                and Path(sys.executable).resolve(strict=True) == launcher
                and sys.version_info[:3] == (3, 12, 11), 'fixed qualified control launcher differs')
    report_path = Path(os.environ['RUNNER_TEMP']).resolve(strict=True) / 'm6-provision/report.json'
    report_raw = probe._read(report_path)
    report_sha256 = hashlib.sha256(report_raw).hexdigest()
    probe._need(report_sha256 == case.result['tools']['provision_report_sha256'],
                'current control provision differs from genuine qualified setup')
    report = probe._json(report_raw)
    probe._need(report['version'] == 'som.react-output-macos-provision.v1'
                and report['status'] == 'passed' and report['stage'] == 'complete'
                and report['python']['status'] == 'passed'
                and report['python']['binary'] == str(public_launcher)
                and report['python']['expected_version'] == 'Python 3.12.11',
                'current qualified control Python provision differs')
    packages = [row for row in report['packages'] if row['formula'] == 'python@3.12']
    probe._need(len(packages) == 1, 'one qualified control Python package required')
    package = packages[0]
    bottle_sha256 = '1443684f05205bf2aca7b050fdea79c93fa4f52fdbf2f085c037aef09b19b0a0'
    probe._need(package['status'] == 'passed' and package['version'] == '3.12.11'
                and type(package['revision']) is int and package['revision'] == 1
                and package['expected_keg_directory'] == str(keg)
                and package['selected_archive_sha256'] == bottle_sha256
                and package['selected_target'] == 'arm64_tahoe'
                and type(package['selected_bottle_rebuild']) is int and package['selected_bottle_rebuild'] == 0,
                'qualified control Python bottle selection differs')
    attempts = [row for row in package['attempts'] if row['status'] == 'passed'
                and row['archive_sha256'] == package['selected_archive_sha256']
                and row['target'] == package['selected_target']
                and row['bottle_rebuild'] == package['selected_bottle_rebuild']]
    probe._need(len(attempts) == 1, 'one passed control Python bottle attempt required')
    normalization = attempts[0]['normalization']
    probe._need(normalization['status'] == 'passed'
                and normalization['policy'] == 'homebrew-6.0.6-ruby-macho-5.0.0-v1',
                'qualified control normalization differs')
    programs = [row for row in normalization['files'] if row['member'] == member]
    probe._need(len(programs) == 1, 'one normalized Framework control program required')
    program = programs[0]
    probe._need(program['status'] == 'passed' and type(program['codesign_exit_code']) is int
                and program['codesign_exit_code'] == 0
                and type(program['normalized_layout']['bytes']) is int
                and program['normalized_layout']['bytes'] > 0,
                'Framework control normalization did not pass')
    executable = keg / member
    probe._physical(executable)
    before = executable.lstat()
    observed_mode = before.st_mode & 0o7777
    probe._need(probe.stat.S_ISREG(before.st_mode) and not executable.is_symlink()
                and observed_mode & 0o111 != 0 and observed_mode & 0o7000 == 0
                and os.access(executable, os.X_OK), 'Framework control program is not a physical regular executable')
    raw = probe._read(executable)
    after = executable.lstat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mode,
                              info.st_mtime_ns, info.st_ctime_ns)
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    probe._need(identity(before) == identity(after)
                and len(raw) == program['normalized_layout']['bytes'] == before.st_size
                and actual_sha256 == program['normalized_sha256'],
                'current physical Framework program differs from qualified normalization')
    source_name = 'som-rs/scripts/react_output_acceptance/provision_macos.py'
    source = case.result['source']['files'][source_name]
    source_raw = probe._read(case.repository / source_name)
    probe._need(hashlib.sha256(source_raw).hexdigest() == source['sha256'],
                'qualified control mode-provenance source changed')
    provenance = {
        'version': 'som.mac-control-precreation-executable.v1', 'phase': 'before-Popen',
        'launcher': {'public': str(public_launcher), 'physical': str(launcher)},
        'selected_executable': str(executable), 'bytes': len(raw), 'sha256': actual_sha256,
        'observed_mode': observed_mode, 'mode_contract_pin': False,
        'sha256_scope': 'current passed normalization observation; not a historical per-file contract pin',
        'provision': {'path': str(report_path), 'sha256': report_sha256,
                      'package': {key: package[key] for key in ('formula', 'version', 'revision',
                          'expected_keg_directory', 'selected_archive_sha256', 'selected_target',
                          'selected_bottle_rebuild', 'status')},
                      'normalization_policy': normalization['policy'], 'program': program},
        'mode_source': {'path': source_name, **probe._plain(source),
                        'extraction': '321/327 chmod(member.mode & 0o777)',
                        'normalization': '863 captures actual mode; 916 restores same mode in finally'},
        'native_authority': False,
    }
    return executable, actual_sha256, provenance


def _cleanup_captured_direct_child(case, process, pid, owner=None):
    """Emergency cleanup for the finite inherited-group and partial-spawn cases.

    Signal only the captured, still-unreaped direct child. In particular, this
    helper must never signal the inherited parent process group.
    """
    process.pid = pid
    try:
        if owner is not None and owner.terminal is not None:
            # A known actual reap forbids another numeric-PID wait or signal.
            case.assertEqual(owner.terminal[0], pid)
            return
        for kind, seconds in ((signal.SIGTERM, 1), (signal.SIGKILL, 5)):
            try:
                row = _WAITPID(pid, os.WNOHANG)
            except ChildProcessError:
                case.assertIsNotNone(owner, 'missing independent or subject terminal wait')
                case.assertIsNotNone(owner.terminal, 'ECHILD cannot supply a terminal status')
                case.assertEqual(owner.terminal[0], pid)
                return
            if row[0] == pid:
                process.returncode = os.waitstatus_to_exitcode(row[1])
                return
            case.assertEqual(row, (0, 0))
            _KILL(pid, kind)
            deadline = _CLOCK() + seconds
            while _CLOCK() < deadline:
                row = _WAITPID(pid, os.WNOHANG)
                if row[0] == pid:
                    process.returncode = os.waitstatus_to_exitcode(row[1])
                    return
                case.assertEqual(row, (0, 0))
                _SLEEP(0.01)
        raise AssertionError('finite direct-child emergency cleanup did not reap its child')
    finally:
        streams = [process.stdin, process.stdout, process.stderr]
        if owner is not None:
            streams.extend(owner.logs)
        for stream in streams:
            if stream is not None:
                stream.close()


class _Control:
    """Fixture-owned real child, with cleanup separate from subject authority."""

    def __init__(self, case, mode='normal'):
        self.case = case
        self.root = case.new_path('control')
        self.root.mkdir()
        self.assets = self.root / 'assets'
        shutil.copytree(case.output / 'candidate/assets', self.assets)
        self.executable, self.executable_sha256, provenance = _select_control_executable(case)
        self.argv = [str(self.executable), '-I', '-S', '-B', '-c', _CONTROL, mode]
        with (self.root / 'control-executable-before-popen.json').open('x') as stream:
            json.dump({**provenance, 'root': str(self.root), 'argv': self.argv}, stream, indent=2)
            stream.write('\n')
        self.process = subprocess.Popen(
            self.argv, cwd=self.root, env=probe._environment(case.repository),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, close_fds=True, start_new_session=True,
        )
        self.pid = self.process.pid
        self.reaped = None
        self.owner = None
        case.assertIs(type(self.pid), int)
        case.addCleanup(self.close)
        ready = b''
        deadline = _CLOCK() + 10
        while b'\n' not in ready and _CLOCK() < deadline:
            if select.select([self.process.stdout], [], [], 0.1)[0]:
                part = os.read(self.process.stdout.fileno(), 64)
                if not part:
                    break
                ready += part
        case.assertEqual(ready, b'READY\n', 'actual controlled child must complete setup')
        case.assertEqual(_GETPGID(self.pid), self.pid)
        case.assertEqual(_GETSID(self.pid), self.pid)
        spec = {
            'role': 'candidate', 'root': self.root, 'executable': self.executable,
            'assets': self.assets, 'port': 19063, 'binary_sha256': self.executable_sha256,
            'asset_hashes': case.result['build']['packaged_assets'],
            'logs_root': self.root / 'unused-service-logs',
        }
        self.owner = probe._Owned(self.process, spec, self.argv, [], [])
        # This is an actual Mac observation, not an injected native response.
        probe._observe_owned_role(self.owner, listener=(mode == 'listener'))

    def request_exit(self, code):
        self.process.stdin.write((str(code) + '\n').encode('ascii'))
        self.process.stdin.flush()

    def subject_reap(self):
        deadline = _CLOCK() + 10
        while _CLOCK() < deadline:
            terminal = probe._wait_owned_child(self.owner)
            if terminal is not None:
                return terminal
            _SLEEP(0.01)
        raise AssertionError('actual controlled child did not reach a terminal wait')

    def fixture_reap(self):
        deadline = _CLOCK() + 10
        while _CLOCK() < deadline:
            row = _WAITPID(self.pid, os.WNOHANG)
            if row[0] == self.pid:
                self.reaped = row
                self.process.returncode = os.waitstatus_to_exitcode(row[1])
                return row
            self.case.assertEqual(row, (0, 0))
            _SLEEP(0.01)
        raise AssertionError('independent controlled child terminal wait is missing')

    def close(self):
        """Never signal after reap, even if the subject lost its wait record."""
        self.process.pid = self.pid
        try:
            if self.reaped is not None:
                self.case.assertEqual(self.reaped[0], self.pid)
                return
            if self.owner is not None and self.owner.terminal is not None:
                self.case.assertEqual(self.owner.terminal[0], self.pid)
                return
            try:
                row = _WAITPID(self.pid, os.WNOHANG)
            except ChildProcessError:
                terminal = self.owner.terminal if self.owner is not None else None
                if self.reaped is None and not (
                    type(terminal) is tuple and terminal[0] == self.pid
                ):
                    raise AssertionError('fixture cleanup has no actual terminal wait evidence')
                return
            if row[0] == self.pid:
                self.reaped = row
                self.process.returncode = os.waitstatus_to_exitcode(row[1])
                return
            self.case.assertEqual(row, (0, 0))
            self.case.assertEqual(_GETPGID(self.pid), self.pid)
            self.case.assertEqual(_GETSID(self.pid), self.pid)
            for kind, seconds in ((signal.SIGTERM, 1), (signal.SIGKILL, 5)):
                # Each signal requires a fresh independent direct-child wait.
                row = _WAITPID(self.pid, os.WNOHANG)
                if row[0] == self.pid:
                    self.reaped = row
                    self.process.returncode = os.waitstatus_to_exitcode(row[1])
                    return
                self.case.assertEqual(row, (0, 0))
                self.case.assertEqual(_GETPGID(self.pid), self.pid)
                self.case.assertEqual(_GETSID(self.pid), self.pid)
                _KILLPG(self.pid, kind)
                deadline = _CLOCK() + seconds
                while _CLOCK() < deadline:
                    row = _WAITPID(self.pid, os.WNOHANG)
                    if row[0] == self.pid:
                        self.reaped = row
                        self.process.returncode = os.waitstatus_to_exitcode(row[1])
                        return
                    self.case.assertEqual(row, (0, 0))
                    _SLEEP(0.01)
            raise AssertionError('independent fixture cleanup did not reap its child')
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()


class MacPythonRenderOnlyProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # No skip, fake runner, prebuilt candidate, fake HTTP, or expected proof.
        cls.repository = Path(__file__).resolve(strict=True).parents[3]
        runner_temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
        cls.workspace = Path(tempfile.mkdtemp(prefix='som-mac-probe-whole-', dir=runner_temp))
        cls.output = cls.workspace / 'genuine-positive'
        cls.result = probe.run_probe(
            cls.repository, cls.repository / '.local/m6-hosted/baseline', cls.output,
        )
        if cls.result['passed'] is not True:
            raise AssertionError('mandatory genuine compiled source/build/HTTP positive failed')

    def setUp(self):
        # unittest runs cleanups in reverse order. Install the writer first so
        # every registered real cleanup runs before records lose their owners.
        self._method_children, self._method_kernel, self._method_subject = [], [], []
        self._method_errors, self._method_paths, self._recording_errors = [], [], []
        self.addCleanup(self._dump_method_evidence)
        for name in ('_WAITPID', '_KILLPG', '_KILL', '_GETPGID', '_GETSID'):
            original = globals()[name]

            def actual_native(*args, _original=original, _name=name):
                row = {'event': _name, 'arguments': list(args)}
                try:
                    result = _original(*args)
                except BaseException as error:
                    row['error'] = ''.join(traceback.format_exception(error))
                    self._retain_evidence(self._method_kernel, row)
                    raise
                row['result'] = list(result) if type(result) is tuple else result
                self._retain_evidence(self._method_kernel, row)
                return result

            patch = mock.patch.dict(globals(), {name: actual_native})
            patch.start()
            self.addCleanup(patch.stop)
        original_popen_init = subprocess.Popen.__init__

        def actual_popen_init(process, *args, **kwargs):
            original_popen_init(process, *args, **kwargs)
            argv = args[0] if args else kwargs.get('args')
            if type(argv) not in (list, tuple) or not argv:
                return
            control = len(argv) == 7 and argv[1:5] == ['-I', '-S', '-B', '-c'] and argv[5] == _CONTROL
            serve = len(argv) == 6 and argv[1] == 'serve' and argv[2] == '--assets' and argv[4] == '--port'
            build = argv[0] == '/usr/bin/time' and len(argv) > 2 and argv[1] == '-l'
            if control or serve or build:
                self._register_method_child(process, 'control' if control else 'serve' if serve else 'build', argv)

        patch = mock.patch.object(subprocess.Popen, '__init__', new=actual_popen_init)
        patch.start()
        self.addCleanup(patch.stop)
        for name in ('_Owned', '_BuildOwned'):
            owner_class = getattr(probe, name, None)
            if owner_class is None:
                continue
            original_init = owner_class.__init__

            def actual_owner_init(owner, process, *args, _original=original_init, **kwargs):
                _original(owner, process, *args, **kwargs)
                for row in self._method_children:
                    if row['process'] is process:
                        row['owner'] = owner
                        self._retain_evidence(row['owners'], owner)
                        break

            patch = mock.patch.object(owner_class, '__init__', new=actual_owner_init)
            patch.start()
            self.addCleanup(patch.stop)
        # Retain swallowed finite refusals and their original exception objects.
        # No arguments are inspected before the subject's own exact-type gate.
        for name in ('_runner', '_wait_owned_child', '_observe_owned_role', '_signal_owned',
                     '_close_owned_role', '_cleanup_roles', '_spawn_owned_role', '_capture_exchange',
                     '_wait_build_child', '_observe_build_owned', '_signal_build_owned',
                     '_close_build_owned', '_wait_build_until', '_build_absence', '_build_command'):
            original = getattr(probe, name, None)
            if original is None:
                continue

            @functools.wraps(original)
            def actual_subject(*args, _original=original, _name=name, **kwargs):
                self._retain_evidence(self._method_subject, {'function': _name, 'event': 'enter'})
                try:
                    result = _original(*args, **kwargs)
                except BaseException as error:
                    self._retain_evidence(self._method_subject, {'function': _name, 'event': 'error',
                                                                'error_index': len(self._method_errors)})
                    self._retain_evidence(self._method_errors, (error, error.__traceback__))
                    raise
                if _name == '_cleanup_roles':
                    primary = kwargs.get('primary') if 'primary' in kwargs else args[1] if len(args) > 1 else None
                    if primary is not None:
                        # Inspect only after the real cleanup returns. Keep the
                        # original body object and its later secondary notes.
                        self._retain_evidence(self._method_errors, (primary, primary.__traceback__))
                self._retain_evidence(self._method_subject, {'function': _name, 'event': 'return'})
                return result

            patch = mock.patch.object(probe, name, new=actual_subject)
            patch.start()
            self.addCleanup(patch.stop)

    def _retain_evidence(self, rows, value):
        # Recording failure cannot replace an actual body/kernel error or stop
        # real cleanup. The final writer reports it after all cleanup attempts.
        try:
            rows.append(value)
        except BaseException as error:
            self._recording_errors.append(''.join(traceback.format_exception(error)))

    def _register_method_child(self, process, role, argv):
        pid = process.pid
        record = {'process': process, 'pid': pid, 'role': role, 'argv': list(argv),
                  'owner': None, 'owners': [], 'kernel_start': len(self._method_kernel),
                  'independent_cleanup': [], 'absence': []}
        # Register safety cleanup before recording or checking anything else.
        self.addCleanup(self._cleanup_method_child, record)
        self._retain_evidence(self._method_children, record)
        self.assertIs(type(pid), int)
        self.assertGreater(pid, 1)

    def _cleanup_method_child(self, record):
        process, pid = record['process'], record['pid']
        self.assertIs(type(pid), int)
        process.pid = pid
        owner, terminal = record['owner'], None
        try:
            subject_terminal = getattr(owner, 'terminal', None)
            own_terminals = [item['result'] for item in self._method_kernel[record['kernel_start']:]
                             if item['event'] == '_WAITPID' and item.get('arguments') == [pid, os.WNOHANG]
                             and item.get('result', [0])[0] == pid]
            if subject_terminal is not None:
                terminal = list(subject_terminal[:2])
                self._retain_evidence(record['independent_cleanup'], {'event': 'known-actual-subject-terminal',
                                                                      'terminal': list(subject_terminal)})
            elif own_terminals:
                terminal = own_terminals[-1]
                self._retain_evidence(record['independent_cleanup'], {'event': 'known-actual-fixture-terminal',
                                                                      'terminal': terminal})
            for kind, seconds in ((signal.SIGTERM, 1), (signal.SIGKILL, 5)):
                if terminal is not None:
                    break
                try:
                    row = _WAITPID(pid, os.WNOHANG)
                except ChildProcessError:
                    # ECHILD alone says nothing about a missing terminal wait.
                    self._retain_evidence(record['independent_cleanup'], {'event': 'unknown-wait', 'error': 'ECHILD without terminal evidence'})
                    raise AssertionError('fixture ECHILD has no retained actual terminal wait')
                self._retain_evidence(record['independent_cleanup'], {'event': 'waitpid', 'result': list(row)})
                if row[0] == pid:
                    terminal = list(row)
                    process.returncode = os.waitstatus_to_exitcode(row[1])
                    break
                self.assertEqual(row, (0, 0))
                pgid, sid = _GETPGID(pid), _GETSID(pid)
                self._retain_evidence(record['independent_cleanup'], {'event': 'live-identity', 'pgid': pgid, 'sid': sid})
                # These are finite fixture children. An inherited group must
                # receive a PID-only signal, never a parent-group signal.
                if pgid == sid == pid:
                    _KILLPG(pid, kind)
                    target = 'private-child-group'
                else:
                    _KILL(pid, kind)
                    target = 'captured-direct-pid'
                self._retain_evidence(record['independent_cleanup'], {'event': 'signal', 'pid': pid,
                                                                      'signal': int(kind), 'target': target})
                deadline = _CLOCK() + seconds
                while _CLOCK() < deadline:
                    row = _WAITPID(pid, os.WNOHANG)
                    self._retain_evidence(record['independent_cleanup'], {'event': 'waitpid', 'result': list(row)})
                    if row[0] == pid:
                        terminal = list(row)
                        process.returncode = os.waitstatus_to_exitcode(row[1])
                        break
                    self.assertEqual(row, (0, 0))
                    _SLEEP(0.01)
                if terminal is not None:
                    break
            self.assertIsNotNone(terminal, 'independent cleanup terminal wait is missing')
            self._retain_evidence(record['independent_cleanup'], {'event': 'terminal', 'pid': terminal[0],
                                                                  'raw_status': terminal[1],
                                                                  'exit_code': os.waitstatus_to_exitcode(terminal[1])})
            table = probe._table(record['absence'])
            self.assertNotIn(pid, table, 'independent cleanup PID remains or was reused')
            if owner is not None:
                self.assertFalse(set(getattr(owner, 'known', ())) & set(table), 'known child remains')
            self.assertFalse(any(group == pid for _, group in table.values()), 'private child group remains')
            try:
                group = _GETPGID(pid)
            except ProcessLookupError:
                self._retain_evidence(record['absence'], {'event': 'getpgid-after-reap', 'result': 'ESRCH', 'pid': pid})
            else:
                self._retain_evidence(record['absence'], {'event': 'getpgid-after-reap', 'result': group, 'pid': pid})
                self.fail('independent cleanup absence is unknown')
        except BaseException as error:
            self._retain_evidence(record['independent_cleanup'], {'event': 'cleanup-error',
                                                                  'traceback': ''.join(traceback.format_exception(error))})
            raise
        finally:
            for stream in (process.stdin, process.stdout, process.stderr, *getattr(owner, 'logs', ())):
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as error:
                        self._recording_errors.append(''.join(traceback.format_exception(error)))

    def _dump_method_evidence(self):
        children = []
        for record in self._method_children:
            owner = record['owner']
            children.append({key: value for key, value in record.items() if key not in ('process', 'owner', 'owners')}
                            | {'subject_role': getattr(owner, 'role', 'transient-build' if record['role'] == 'build' else None),
                               'subject_terminal': getattr(owner, 'terminal', None),
                               'subject_ledger': getattr(owner, 'ledger', []),
                               'subject_known_pids': sorted(getattr(owner, 'known', ())),
                               'subject_closed': getattr(owner, 'closed', None),
                               'all_real_owner_ledgers': [
                                   {'kind': type(held).__name__, 'terminal': held.terminal, 'ledger': held.ledger}
                                   for held in record['owners']]})
        files = []
        for root in self._method_paths:
            for path in sorted(root.rglob('*')):
                if path.is_symlink():
                    files.append({'path': str(path.relative_to(self.workspace)), 'kind': 'symlink-not-read'})
                elif path.is_file():
                    files.append({'path': str(path.relative_to(self.workspace)), 'bytes': path.stat().st_size,
                                  'sha256': _sha(path)})
        result = self._outcome.result
        errors = [text for test, text in result.errors + result.failures
                  if test is self or getattr(test, 'test_case', None) is self]
        payload = {'version': 'som.mac-probe.per-method-fixture-evidence.v1',
                   'method': self.id(), 'source_head': os.environ['GITHUB_SHA'],
                   'genuine_class_output': str(self.output.relative_to(self.workspace)),
                   'children': children, 'independent_kernel': self._method_kernel,
                   'subject_calls': self._method_subject,
                   'caught_subject_errors': [''.join(traceback.format_exception(type(error), error, caught_trace))
                                             for error, caught_trace in self._method_errors],
                   'test_or_cleanup_errors': errors, 'recording_errors': self._recording_errors,
                   'retained_regular_files': files,
                   'native_authority': False, 'product_acceptance_passed': False}
        destination = self.workspace / 'method-evidence' / self._testMethodName
        destination.mkdir(parents=True, exist_ok=False)
        with (destination / 'evidence.json').open('x') as stream:
            json.dump(probe._plain(payload), stream, indent=2)
            stream.write('\n')
        self.assertEqual(self._recording_errors, [], 'fixture evidence or log close failed')

    def new_path(self, label):
        # Reserve the parent only. Subject APIs require an absent output path.
        parent = Path(tempfile.mkdtemp(prefix=label + '-', dir=self.workspace))
        self._method_paths.append(parent)
        return parent / label

    def role_spec(self, role='candidate'):
        root = self.new_path(role)
        shutil.copytree(self.output / 'candidate', root,
                        ignore=shutil.ignore_patterns('target', 'target-assets'))
        return {
            'role': role, 'root': root, 'executable': root / 'som',
            'assets': root / 'assets', 'port': 19062 if role == 'before' else 19063,
            'binary_sha256': self.result['build']['copied_binary_sha256'],
            'asset_hashes': self.result['build']['packaged_assets'],
            'logs_root': self.new_path(role + '-logs'),
        }

    @contextlib.contextmanager
    def actual_role(self, role='candidate'):
        owner = probe._spawn_owned_role(self.role_spec(role), probe._environment(self.repository), [])
        try:
            yield owner
        finally:
            probe._cleanup_roles([owner])

    def pair_data(self, form='omitted'):
        pair = {'form': form}
        for role in ('before', 'candidate'):
            root = self.output / 'raw' / form / role
            pair[role] = {
                phase + '_' + kind: (root / phase / filename).read_bytes()
                for phase in ('compile', 'assemble')
                for kind, filename in (('request', 'request.raw'), ('response', 'response.raw'),
                                       ('transport', 'transport.json'))
            }
        return pair

    def assert_no_subject_signal(self, owner):
        self.assertEqual([row for row in owner.ledger if row.get('event') == 'signal'], [])

    def _finite_build_control(self, mode='normal'):
        # A private controlled builder-negative only, after genuine class setup.
        # Capture the real executable hash before this child's actual Popen.
        executable, executable_sha256, _ = _select_control_executable(self)
        child = _Control(self, mode)
        self.assertEqual(child.executable, executable)
        probe._need(child.executable_sha256 == executable_sha256,
                    'shared precreation control executable hash changed')
        owner = probe._BuildOwned(child.process, child.argv, child.root, [], [],
                                  executable_sha256=executable_sha256)
        child.owner = owner
        self.assertIs(probe._observe_build_owned(owner), True)
        return child, owner

    def test_genuine_compiled_eight_exchange_positive(self):
        result = self.result
        self.assertIs(result['passed'], True)
        self.assertEqual(result['fresh_exchange_count'], 8)
        self.assertEqual(len(result['canonical_vectors']), 11)
        self.assertEqual({pair['form'] for pair in result['pairs']}, {'omitted', 'python'})
        for pair in result['pairs']:
            self.assertIs(pair['strict_compile_byte_equal'], False)
            self.assertIs(pair['strict_assemble_byte_equal'], True)
            self.assertIs(pair['assemble_request_hashes_differ'], True)
            self.assertIs(pair['prospective_data_comparison_only'], True)
            self.assertEqual(len(pair['independently_derived_digest_pins']), 2)
            self.assertTrue(pair['generated_file_sha256'])
        for flag in ('native_authority', 'SourceSeal', 'genuine_Mac_Context', 'full671', 'full12',
                     'four_receipts_approved', 'product_acceptance_passed',
                     'historical100_wire_recovered', 'signal_causality_is_atomic'):
            self.assertIs(result[flag], False)
        self.assertEqual(result['checkout_before']['head'], os.environ['GITHUB_SHA'])
        self.assertEqual(result['source']['head'], os.environ['GITHUB_SHA'])
        self.assertEqual(result['build']['source_head'], os.environ['GITHUB_SHA'])
        self.assertEqual(len(result['build']['commands']), 4)
        self.assertEqual(set(result['build']['dep_info']), {'som', 'som-compiler-assets'})
        self.assertEqual(result['build']['built_binary_sha256'],
                         result['build']['copied_binary_sha256'])
        self.assertEqual(_sha(self.output / 'candidate/som'), result['build']['copied_binary_sha256'])
        self.assertEqual(_sha(self.repository / '.local/m6-hosted/baseline/som'),
                         '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c')
        for form in ('omitted', 'python'):
            pair = self.pair_data(form)
            self.assertEqual(len(pair['before']['compile_request']), 600 if form == 'omitted' else 618)
            self.assertEqual(pair['before']['compile_request'], pair['candidate']['compile_request'])
            for role in ('before', 'candidate'):
                for phase in ('compile', 'assemble'):
                    transport = json.loads(pair[role][phase + '_transport'])
                    response = pair[role][phase + '_response']
                    self.assertIs(transport['read_complete'], True)
                    self.assertEqual(transport['status'], 200)
                    self.assertEqual(transport['bytes'], len(response))
                    self.assertEqual(transport['sha256'], hashlib.sha256(response).hexdigest())
                    self.assertIs(type(transport['owned_pid']), int)
        with tarfile.open(self.output / 'source.tar', 'r:') as archive:
            self.assertEqual(archive.getnames(), sorted(result['source']['files']))
            for member in archive:
                self.assertTrue(member.isfile())
                self.assertEqual(hashlib.sha256(archive.extractfile(member).read()).hexdigest(),
                                 result['source']['files'][member.name]['sha256'])

    def test_genuine_both_native_cleanup_records(self):
        self.assertEqual({row['role'] for row in self.result['cleanup']}, {'before', 'candidate'})
        for row in self.result['cleanup']:
            self.assertIs(row['absence_verified'], True)
            self.assertIs(row['term_sent'], True)
            self.assertIs(row['unplanned'], False)
            ledger = row['native_ledger']
            signals = [item for item in ledger if item.get('event') == 'signal']
            self.assertTrue(signals)
            self.assertEqual(signals[0]['signal'], signal.SIGTERM)
            self.assertTrue(all(item['succeeded'] is True for item in signals))
            terminals = [i for i, item in enumerate(ledger)
                         if item.get('event') == 'waitpid' and item.get('result', [0])[0] == row['pid']]
            self.assertEqual(len(terminals), 1)
            self.assertFalse(any(item.get('event') == 'signal' for item in ledger[terminals[0] + 1:]))
            self.assertEqual(row['terminal'][0], row['pid'])
            self.assertTrue(any(item.get('event') == 'owner' and item['pgid'] == item['sid'] == row['pid']
                                for item in ledger))

    def test_changed_public_pid_is_rejected_before_hooks_or_native_calls(self):
        child = _Control(self)
        hooks = []

        class HostileInt(int):
            def __eq__(self, other):
                hooks.append('eq')
                raise AssertionError('public PID comparison hook ran')

            def __str__(self):
                hooks.append('str')
                raise AssertionError('public PID format hook ran')

            def __hash__(self):
                hooks.append('hash')
                raise AssertionError('public PID hash hook ran')

        for bad in (True, HostileInt(child.pid)):
            for operation in (probe._wait_owned_child, probe._observe_owned_role, probe._close_owned_role):
                with self.subTest(pid_type=type(bad).__name__, operation=operation.__name__):
                    child.process.pid = bad
                    with contextlib.ExitStack() as stack:
                        spies = [stack.enter_context(mock.patch.object(obj, name, side_effect=AssertionError(name)))
                                 for obj, name in ((probe, '_native'), (probe.os, 'waitpid'),
                                                   (probe.os, 'getpgid'), (probe.os, 'getsid'),
                                                   (probe.os, 'killpg'))]
                        with self.assertRaisesRegex(ValueError, 'current direct Popen PID must be exact int'):
                            operation(child.owner)
                        for spy in spies:
                            spy.assert_not_called()
                    child.process.pid = child.pid
        self.assertEqual(hooks, [])
        self.assert_no_subject_signal(child.owner)

    def test_changed_exact_int_pid_cannot_replace_private_creation_pid(self):
        child = _Control(self)
        child.process.pid = child.pid + 1
        with mock.patch.object(probe.os, 'waitpid', side_effect=AssertionError('wait called')) as wait:
            with self.assertRaisesRegex(ValueError, 'differs from private initial PID'):
                probe._wait_owned_child(child.owner)
            wait.assert_not_called()
        child.process.pid = child.pid
        self.assert_no_subject_signal(child.owner)

    def test_unplanned_actual_exit_zero_and_nonzero_fail_cleanup(self):
        for code in (0, 7):
            with self.subTest(code=code):
                child = _Control(self)
                child.request_exit(code)
                terminal = child.subject_reap()
                self.assertEqual(terminal[0], child.pid)
                self.assertEqual(terminal[2], code)
                self.assertIs(child.owner.unplanned, True)
                with self.assertRaisesRegex(ValueError, 'unplanned serve exit before probe-owned TERM'):
                    probe._close_owned_role(child.owner)
                self.assert_no_subject_signal(child.owner)

    def test_final_pre_term_wait_first_detects_actual_exit(self):
        for code in (0, 7):
            with self.subTest(code=code):
                child = _Control(self)
                original = probe._wait_owned_child
                triggered = []

                def actual_wait(owner):
                    if (not triggered and owner is child.owner and owner.ledger
                            and owner.ledger[-1].get('phase') == 'final-pre-signal-wait'):
                        triggered.append(True)
                        child.request_exit(code)
                        deadline = _CLOCK() + 10
                        while _CLOCK() < deadline:
                            terminal = original(owner)
                            if terminal is not None:
                                return terminal
                            _SLEEP(0.01)
                        raise AssertionError('real final-gate child exit did not complete')
                    return original(owner)

                with mock.patch.object(probe, '_wait_owned_child', side_effect=actual_wait):
                    with self.assertRaisesRegex(ValueError, 'unplanned serve exit at final pre-signal wait gate'):
                        probe._close_owned_role(child.owner)
                self.assertEqual(triggered, [True])
                self.assertEqual(child.owner.terminal[2], code)
                self.assertIs(child.owner.term_sent, False)
                self.assert_no_subject_signal(child.owner)
                self.assertTrue(any(row.get('event') == 'getpgid-after-reap'
                                    and row['result'] == 'ESRCH' for row in child.owner.ledger))

    def test_no_term_or_kill_after_actual_reap(self):
        child = _Control(self)
        child.request_exit(0)
        child.subject_reap()
        for kind in (signal.SIGTERM, signal.SIGKILL):
            with mock.patch.object(probe.os, 'killpg', side_effect=AssertionError('signal after reap')) as kill:
                with self.assertRaisesRegex(ValueError, 'unplanned serve exit before probe-owned TERM'):
                    probe._signal_owned(child.owner, kind)
                kill.assert_not_called()
        self.assert_no_subject_signal(child.owner)

    def test_actual_echild_is_unknown_and_never_authorizes_signal(self):
        child = _Control(self)
        child.request_exit(0)
        child.fixture_reap()
        self.assertIsNone(child.owner.terminal)
        with mock.patch.object(probe.os, 'killpg', side_effect=AssertionError('ECHILD signal')) as kill:
            with self.assertRaises(ChildProcessError):
                probe._close_owned_role(child.owner)
            kill.assert_not_called()
        self.assertIsNone(child.owner.terminal)
        self.assert_no_subject_signal(child.owner)

    def test_missing_terminal_wait_cannot_claim_absence(self):
        child = _Control(self)
        with mock.patch.object(probe, '_table', side_effect=AssertionError('absence observation too soon')) as table:
            with self.assertRaisesRegex(ValueError, 'absence without actual terminal kernel wait is unknown'):
                probe._absence_owned(child.owner)
            table.assert_not_called()
        self.assert_no_subject_signal(child.owner)

    def test_wrong_actual_start_identity_refuses_signal(self):
        child = _Control(self)
        original = child.owner.start
        child.owner.start = 'different actual start expectation'
        try:
            with self.assertRaisesRegex(ValueError, 'actual process start observation differs'):
                probe._signal_owned(child.owner, signal.SIGTERM)
            self.assert_no_subject_signal(child.owner)
        finally:
            child.owner.start = original

    def test_changed_live_owned_assets_refuse_signal(self):
        child = _Control(self)
        path = child.assets / 'manifest.json'
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        try:
            with self.assertRaisesRegex(ValueError, 'live owned asset bytes differ'):
                probe._signal_owned(child.owner, signal.SIGTERM)
            self.assert_no_subject_signal(child.owner)
        finally:
            path.write_bytes(original)

    def test_wrong_executable_hash_refuses_signal(self):
        child = _Control(self)
        original = child.owner.binary_sha256
        child.owner.binary_sha256 = '0' * 64
        try:
            with self.assertRaisesRegex(ValueError, 'actual executable text mapping/path/on-disk hash differs'):
                probe._signal_owned(child.owner, signal.SIGTERM)
            self.assert_no_subject_signal(child.owner)
        finally:
            child.owner.binary_sha256 = original

    def test_real_term_reap_and_absence(self):
        child = _Control(self)
        record = probe._close_owned_role(child.owner)
        self.assertIs(record['absence_verified'], True)
        self.assertIs(record['term_sent'], True)
        self.assertIs(record['unplanned'], False)
        self.assertEqual(record['terminal'][2], -signal.SIGTERM)
        self.assertEqual([row['signal'] for row in child.owner.ledger if row.get('event') == 'signal'],
                         [signal.SIGTERM])
        with mock.patch.object(probe.os, 'killpg', side_effect=AssertionError('KILL after planned reap')) as kill:
            self.assertIs(probe._signal_owned(child.owner, signal.SIGKILL), False)
            kill.assert_not_called()

    def test_real_ignored_term_requires_owned_kill_and_actual_wait(self):
        child = _Control(self, 'ignore-term')
        record = probe._close_owned_role(child.owner)
        self.assertIs(record['absence_verified'], True)
        self.assertEqual(record['terminal'][2], -signal.SIGKILL)
        signals = [row for row in child.owner.ledger if row.get('event') == 'signal']
        self.assertEqual([row['signal'] for row in signals], [signal.SIGTERM, signal.SIGKILL])
        self.assertTrue(all(row['succeeded'] is True for row in signals))
        self.assertFalse(record['unplanned'])

    def test_both_cleanup_attempts_preserve_original_body_exception(self):
        before, candidate = _Control(self), _Control(self)
        before.owner.role = 'before'
        before.owner.port = 19062
        candidate.request_exit(7)
        candidate.subject_reap()
        primary = RuntimeError('original finite body failure')
        try:
            raise primary
        except RuntimeError as caught:
            records = probe._cleanup_roles([before.owner, candidate.owner], primary=caught)
            self.assertIs(caught, primary)
            self.assertIs(sys.exception(), primary)
        self.assertEqual([row['role'] for row in records], ['candidate', 'before'])
        self.assertIs(records[0]['absence_verified'], False)
        self.assertIs(records[1]['absence_verified'], True)
        self.assertIs(before.owner.closed, True)
        self.assertTrue(any('Secondary owned cleanup error:' in note for note in primary.__notes__))
        self.assert_no_subject_signal(candidate.owner)

    def test_both_cleanup_errors_keep_first_error_and_second_note(self):
        before, candidate = _Control(self), _Control(self)
        before.owner.role = 'before'
        before.owner.port = 19062
        for child in (before, candidate):
            child.request_exit(0)
            child.subject_reap()
        errors = []
        original = probe._close_owned_role

        def actual_close(owner):
            try:
                return original(owner)
            except BaseException as error:
                errors.append(error)
                raise

        with mock.patch.object(probe, '_close_owned_role', side_effect=actual_close):
            with self.assertRaises(ValueError) as raised:
                probe._cleanup_roles([before.owner, candidate.owner])
        self.assertEqual(len(errors), 2)
        self.assertIs(raised.exception, errors[0])
        self.assertTrue(any('Additional owned cleanup error:' in note for note in raised.exception.__notes__))
        for child in (before, candidate):
            self.assert_no_subject_signal(child.owner)

    def test_occupied_port_refuses_before_spawning_or_signaling_foreign_listener(self):
        foreign = _Control(self, 'listener')
        ledger = []
        spec = self.role_spec()
        original_popen = subprocess.Popen
        service_spawns = []

        def native_observations_only(argv, *args, **kwargs):
            if argv[0] == str(spec['executable']):
                service_spawns.append(argv)
                raise AssertionError('service spawn on occupied port')
            # subprocess.run uses Popen for the actual lsof observation too.
            return original_popen(argv, *args, **kwargs)

        with mock.patch.object(probe.subprocess, 'Popen', side_effect=native_observations_only):
            with mock.patch.object(probe.os, 'killpg', side_effect=AssertionError('foreign signal')) as kill:
                with self.assertRaisesRegex(ValueError, 'diagnostic port is occupied or its absence is unknown'):
                    probe._spawn_owned_role(spec, probe._environment(self.repository), ledger)
                self.assertEqual(service_spawns, [])
                kill.assert_not_called()
        self.assertIsNone(probe._wait_owned_child(foreign.owner))
        probe._observe_owned_role(foreign.owner, listener=True)
        self.assert_no_subject_signal(foreign.owner)

    def test_real_candidate_restarts_through_creator_and_cleanup(self):
        with self.actual_role() as owner:
            self.assertIs(type(owner.process), subprocess.Popen)
            self.assertEqual(owner.process.pid, owner.pid)
            self.assertIsNone(owner.terminal)
            probe._observe_owned_role(owner)
        self.assertIs(owner.closed, True)
        self.assertIsNotNone(owner.terminal)

    def test_changed_copied_executable_refuses_before_process_creation(self):
        spec = self.role_spec()
        with spec['executable'].open('ab') as stream:
            stream.write(b'changed fixture-owned copied executable')
        with mock.patch.object(probe.subprocess, 'Popen', side_effect=AssertionError('changed binary spawn')) as spawn:
            with self.assertRaisesRegex(ValueError, 'verified role executable differs'):
                probe._spawn_owned_role(spec, probe._environment(self.repository), [])
            spawn.assert_not_called()

    def test_actual_private_compiled_source_change_breaks_source_join(self):
        root = self.output / 'source-worktree'
        name = 'som-rs/scripts/hosted_readiness/test_mac_python_render_only_probe.py'
        path = root / name
        original = path.read_bytes()
        with probe._qualified_modules(self.repository) as (reader, raw, compat):
            before = probe._snapshot(root, reader, self.result['source']['files'])
            self.assertEqual(before, self.result['source'])
            try:
                path.write_bytes(original + b'\n# finite fixture-owned source change\n')
                with self.assertRaisesRegex(ValueError, 'physical selected Git blob/mode differs'):
                    probe._snapshot(root, reader, self.result['source']['files'])
            finally:
                path.write_bytes(original)
            self.assertEqual(probe._snapshot(root, reader, self.result['source']['files']), before)

    def test_fresh_transport_status_and_completeness_are_required(self):
        with probe._qualified_modules(self.repository) as (reader, raw, compat):
            sources = compat._derive_sources(self.repository)
            for field, bad, message in (
                ('status', 503, 'transport status differs'),
                ('read_complete', False, 'transport read is incomplete'),
                ('owned_pid', True, 'retained transport PID or port differs'),
            ):
                with self.subTest(field=field):
                    pair = self.pair_data()
                    transport = json.loads(pair['candidate']['compile_transport'])
                    transport[field] = bad
                    pair['candidate']['compile_transport'] = json.dumps(transport).encode()
                    with self.assertRaisesRegex(ValueError, message):
                        probe._compare_fresh_pairs([pair], sources, compat)

    def test_fresh_assemble_request_must_use_its_own_returned_bundle(self):
        with probe._qualified_modules(self.repository) as (reader, raw, compat):
            pair = self.pair_data()
            pair['candidate']['assemble_request'] = pair['before']['assemble_request']
            with self.assertRaisesRegex(ValueError, 'assembly request is not its complete own returned bundle'):
                probe._compare_fresh_pairs([pair], compat._derive_sources(self.repository), compat)

    def test_fresh_complete_assembly_and_unapproved_raw_bytes_remain_exact(self):
        with probe._qualified_modules(self.repository) as (reader, raw, compat):
            sources = compat._derive_sources(self.repository)
            for key in ('compile_response', 'assemble_response'):
                with self.subTest(key=key):
                    pair = self.pair_data()
                    pair['candidate'][key] += b' '
                    phase = key.split('_')[0]
                    transport = json.loads(pair['candidate'][phase + '_transport'])
                    transport['bytes'] = len(pair['candidate'][key])
                    transport['sha256'] = hashlib.sha256(pair['candidate'][key]).hexdigest()
                    transport['headers'] = [[name, str(transport['bytes']) if name.lower() == 'content-length' else value]
                                            for name, value in transport['headers']]
                    pair['candidate'][phase + '_transport'] = json.dumps(transport).encode()
                    try:
                        probe._compare_fresh_pairs([pair], sources, compat)
                    except ValueError as refused:
                        if key == 'assemble_response':
                            self.assertIn('complete assembly response bytes differ', str(refused))
                        else:
                            frames = []
                            trace = refused.__traceback__
                            while trace is not None:
                                frames.append(trace.tb_frame.f_code.co_name)
                                trace = trace.tb_next
                            self.assertIn('verify_raw', frames)
                    else:
                        self.fail('changed complete raw response was accepted')


    def test_three_exact_paths_refuse_before_comparison_or_path_hooks(self):
        hooks = []

        class HostilePath(type(Path())):
            def __eq__(self, other):
                hooks.append('eq')
                raise AssertionError('untrusted Path comparison ran')

            def __str__(self):
                hooks.append('str')
                raise AssertionError('untrusted Path formatting ran')

            def __fspath__(self):
                hooks.append('fspath')
                raise AssertionError('untrusted filesystem hook ran')

        hostile = HostilePath(str(self.repository))
        good = [self.repository, self.repository / '.local/m6-hosted/baseline', self.new_path('path-gate')]
        for position in range(3):
            for bad in (str(good[position]), hostile):
                with self.subTest(position=position, argument_type=type(bad).__name__):
                    arguments = list(good)
                    arguments[position] = bad
                    with contextlib.ExitStack() as stack:
                        spies = [stack.enter_context(mock.patch.object(probe, name,
                                                                      side_effect=AssertionError(name)))
                                 for name in ('_physical', '_argv', '_git')]
                        with self.assertRaisesRegex(ValueError, 'three exact Path arguments required before any comparison'):
                            probe._runner(*arguments)
                        for spy in spies:
                            spy.assert_not_called()
        self.assertEqual(hooks, [])

    def test_existing_output_and_wrong_runner_refuse_at_outer_gate(self):
        before = self.repository / '.local/m6-hosted/baseline'
        with self.assertRaisesRegex(ValueError, 'new runner temporary output required'):
            probe._runner(self.repository, before, self.output)
        target = self.new_path('wrong-runner')
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            with mock.patch.object(probe, '_git', side_effect=AssertionError('wrong runner Git operation')) as git:
                with self.assertRaisesRegex(ValueError, 'GitHub hosted arm64 Mac26 required'):
                    probe._runner(self.repository, before, target)
                git.assert_not_called()
        self.assertFalse(target.exists())

    def test_actual_compiled_failed_http_status_retains_raw_and_both_owner_checks(self):
        target = self.new_path('actual-http-400')
        with self.actual_role() as owner:
            original_observe = probe._observe_owned_role
            with mock.patch.object(probe, '_observe_owned_role', wraps=original_observe) as observe:
                with self.assertRaisesRegex(ValueError, 'complete bounded successful HTTP response required'):
                    probe._capture_exchange(owner, b'{', 'compile', target)
                self.assertEqual(observe.call_count, 2)
                self.assertEqual(observe.call_args_list, [mock.call(owner), mock.call(owner)])
            self.assertIsNone(probe._wait_owned_child(owner))
            self.assertEqual((target / 'request.raw').read_bytes(), b'{')
            response = (target / 'response.raw').read_bytes()
            transport = json.loads((target / 'transport.json').read_bytes())
            self.assertTrue(response)
            self.assertEqual(transport['status'], 400)
            self.assertIs(transport['read_complete'], False)
            self.assertEqual(transport['bytes'], len(response))
            self.assertEqual(transport['sha256'], hashlib.sha256(response).hexdigest())
            self.assertIn('complete bounded successful HTTP response required',
                          (target / 'capture-error.txt').read_text())

    def test_actual_http_read_first_error_survives_real_post_owner_check_and_secondary(self):
        request = self.pair_data()['candidate']['compile_request']
        for with_secondary in (False, True):
            with self.subTest(secondary=with_secondary):
                target = self.new_path('actual-http-read-error')
                body_error = RuntimeError('original finite actual HTTP body-read error')
                secondary = RuntimeError('finite secondary after real post-HTTP owner check')
                with self.actual_role() as owner:
                    original_getresponse = probe.http.client.HTTPConnection.getresponse
                    original_observe = probe._observe_owned_role
                    reads, observations, responses = [], [], []

                    def actual_getresponse(connection):
                        response = original_getresponse(connection)
                        self.assertEqual(response.status, 200)
                        responses.append(response)
                        original_read = response.read

                        def failed_actual_read(amount):
                            first_byte = original_read(1)
                            self.assertIs(type(first_byte), bytes)
                            self.assertEqual(len(first_byte), 1)
                            reads.append((amount, first_byte))
                            raise body_error

                        response.read = failed_actual_read
                        return response

                    def actual_observe(actual_owner, listener=True):
                        result = original_observe(actual_owner, listener=listener)
                        observations.append(actual_owner)
                        if with_secondary and len(observations) == 2:
                            raise secondary
                        return result

                    with mock.patch.object(probe.http.client.HTTPConnection, 'getresponse', new=actual_getresponse):
                        with mock.patch.object(probe, '_observe_owned_role', side_effect=actual_observe):
                            with self.assertRaises(RuntimeError) as raised:
                                probe._capture_exchange(owner, request, 'compile', target)
                    self.assertIs(raised.exception, body_error)
                    self.assertEqual(observations, [owner, owner])
                    self.assertEqual(len(responses), 1)
                    self.assertEqual(len(reads), 1)
                    self.assertGreater(reads[0][0], 1)
                    self.assertIsNone(probe._wait_owned_child(owner))
                    transport = json.loads((target / 'transport.json').read_bytes())
                    self.assertEqual(transport['status'], 200)
                    self.assertIs(transport['read_complete'], False)
                    self.assertFalse((target / 'response.raw').exists())
                    self.assertIn(str(body_error), (target / 'capture-error.txt').read_text())
                    notes = getattr(body_error, '__notes__', [])
                    if with_secondary:
                        self.assertTrue(any(str(secondary) in note for note in notes))
                    else:
                        self.assertEqual(notes, [])

    def test_public_second_spawn_failure_cleans_first_real_compiled_before_child(self):
        # Explicit finite fault only: reuse the already genuine class build.
        # This call cannot count as another build positive or HTTP positive.
        target = self.new_path('finite-second-spawn-failure')
        failure = RuntimeError('original finite second-role spawn failure')
        original_spawn, original_cleanup = probe._spawn_owned_role, probe._cleanup_roles
        started, attempts, cleaned, cleanup_primaries = [], [], [], []

        def actual_first_spawn_then_fail(spec, env, ledger):
            attempts.append(spec['role'])
            if spec['role'] == 'candidate':
                raise failure
            owner = original_spawn(spec, env, ledger)
            self.addCleanup(_cleanup_captured_direct_child, self, owner.process, owner.pid, owner)
            started.append(owner)
            return owner

        def actual_cleanup(roles, primary=None):
            cleanup_primaries.append(primary)
            records = original_cleanup(roles, primary=primary)
            cleaned.extend(records)
            return records

        reused_negative_build = (self.output / 'candidate', self.result['build'], self.result['source'])
        with mock.patch.object(probe, '_build_candidate_only', return_value=reused_negative_build) as builder:
            with mock.patch.object(probe, '_spawn_owned_role', side_effect=actual_first_spawn_then_fail):
                with mock.patch.object(probe, '_cleanup_roles', side_effect=actual_cleanup):
                    with mock.patch.object(probe, '_capture_owned_pair',
                                           side_effect=AssertionError('HTTP after failed second spawn')) as capture:
                        with self.assertRaises(RuntimeError) as raised:
                            probe.run_probe(self.repository,
                                            self.repository / '.local/m6-hosted/baseline', target)
                        capture.assert_not_called()
        self.assertIs(raised.exception, failure)
        builder.assert_called_once()
        self.assertEqual(attempts, ['before', 'candidate'])
        self.assertEqual(len(started), 1)
        self.assertEqual(cleanup_primaries, [failure])
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]['role'], 'before')
        self.assertIs(cleaned[0]['absence_verified'], True)
        self.assertIs(started[0].closed, True)
        self.assertIsNotNone(started[0].terminal)
        record = json.loads((target / 'result.json').read_bytes())
        self.assertIs(record['passed'], False)
        self.assertNotIn('fresh_exchange_count', record)
        self.assertIn(str(failure), record['body_error'])
        self.assertEqual(record['cleanup'][0]['terminal'][0], started[0].pid)

    def test_actual_inherited_group_and_non_session_group_refuse_native_owner_gate(self):
        for new_group in (False, True):
            with self.subTest(new_group_without_new_session=new_group):
                root = self.new_path('wrong-native-group')
                root.mkdir()
                assets = root / 'assets'
                shutil.copytree(self.output / 'candidate/assets', assets)
                executable = Path(sys.executable).resolve(strict=True)
                argv = [str(executable), '-I', '-S', '-B', '-c', _CONTROL, 'normal']
                options = {'process_group': 0} if new_group else {}
                process = subprocess.Popen(
                    argv, cwd=root, env=probe._environment(self.repository),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    shell=False, close_fds=True, start_new_session=False, **options,
                )
                pid = process.pid
                # Install independent PID-only cleanup before observation/assertion.
                self.addCleanup(_cleanup_captured_direct_child, self, process, pid)
                self.assertIs(type(pid), int)
                self.assertNotEqual(_GETSID(pid), pid)
                if new_group:
                    self.assertEqual(_GETPGID(pid), pid)
                else:
                    self.assertNotEqual(_GETPGID(pid), pid)
                spec = {
                    'role': 'candidate', 'root': root, 'executable': executable,
                    'assets': assets, 'port': 19063, 'binary_sha256': _sha(executable),
                    'asset_hashes': self.result['build']['packaged_assets'],
                    'logs_root': root / 'unused-logs',
                }
                owner = probe._Owned(process, spec, argv, [], [])
                with mock.patch.object(probe, '_native', side_effect=AssertionError('parser before native group gate')) as native:
                    with self.assertRaisesRegex(ValueError, 'native direct process group/session/creator differs'):
                        probe._observe_owned_role(owner, listener=False)
                    native.assert_not_called()
                self.assertIsNone(owner.terminal)
                self.assertEqual(owner.ledger[-1]['event'], 'owner')
                self.assertEqual(owner.ledger[-1]['pgid'], _GETPGID(pid))
                self.assertEqual(owner.ledger[-1]['sid'], _GETSID(pid))
                self.assert_no_subject_signal(owner)

    def test_actual_compiled_cwd_and_listener_parsers_refuse_wrong_expected_identity(self):
        for field in ('root', 'port'):
            with self.subTest(field=field):
                with self.actual_role() as owner:
                    original = getattr(owner, field)
                    if field == 'root':
                        wrong = self.new_path('wrong-expected-cwd')
                        wrong.mkdir()
                        message = 'actual process cwd differs'
                    else:
                        wrong = 19062
                        message = 'required native owner observation failed'
                    start = len(owner.ledger)
                    setattr(owner, field, wrong)
                    try:
                        with self.assertRaisesRegex(ValueError, message):
                            probe._observe_owned_role(owner)
                        observations = [row for row in owner.ledger[start:] if 'argv' in row]
                        intended = (['/usr/sbin/lsof', '-a', '-p', str(owner.pid), '-d', 'cwd', '-Fn']
                                    if field == 'root' else
                                    ['/usr/sbin/lsof', '-nP', '-a', '-iTCP:19062', '-sTCP:LISTEN', '-Fpn'])
                        self.assertEqual(observations[-1]['argv'], intended)
                        self.assertEqual(observations[-1]['exit_code'], 0 if field == 'root' else 1)
                        self.assert_no_subject_signal(owner)
                    finally:
                        setattr(owner, field, original)
                    probe._observe_owned_role(owner)

    def test_live_remembered_pid_predicate_refuses_absence_without_claiming_descendant_discovery(self):
        # Both PIDs come from real independent creators. This is only a finite
        # remembered-PID predicate witness, not a native descendant-discovery proof.
        owner_child, remembered_child = _Control(self), _Control(self)
        self.assertIsNone(probe._wait_owned_child(remembered_child.owner))
        owner_child.owner.known.add(remembered_child.pid)
        with self.assertRaisesRegex(ValueError, 'owned child/group/descendants remain or PID reused'):
            probe._close_owned_role(owner_child.owner)
        self.assertIsNotNone(owner_child.owner.terminal)
        self.assertIs(owner_child.owner.term_sent, True)
        self.assertIs(owner_child.owner.closed, False)
        self.assertIsNone(probe._wait_owned_child(remembered_child.owner))
        self.assert_no_subject_signal(remembered_child.owner)


    def test_transient_actual_timed_exit_zero_needs_terminal_absence_and_no_later_signal(self):
        root = self.new_path('finite-transient-exit-zero')
        root.mkdir()
        command = [str(Path(sys.executable).resolve(strict=True)), '-I', '-S', '-B', '-c', 'pass']
        record = probe._build_command(command, root, probe._environment(self.repository), root, 0)
        self.assertEqual(record['exit_code'], 0)
        self.assertIs(type(record['initial_pid']), int)
        self.assertIs(record['cleanup']['absence_verified'], True)
        self.assertEqual(record['cleanup']['terminal'][2], 0)
        ledger = record['native_ledger']
        terminals = [index for index, item in enumerate(ledger)
                     if item.get('event') == 'build-waitpid'
                     and item.get('result', [0])[0] == record['initial_pid']]
        self.assertEqual(len(terminals), 1)
        self.assertFalse(any(item.get('event') == 'build-signal' for item in ledger[terminals[0] + 1:]))
        self.assertTrue(any(item.get('event') == 'build-getpgid-after-reap'
                            and item['result'] == 'ESRCH' for item in ledger))
        self.assertEqual(json.loads((root / 'build-0.json').read_bytes()), probe._plain(record))
        self.assertEqual((root / 'build-0.stdout').read_bytes(), b'')
        self.assertTrue((root / 'build-0.stderr').read_bytes())

    def test_private_actual_build_timeout_term_and_conditional_kill_keep_kernel_evidence(self):
        for mode in ('normal', 'ignore-term'):
            with self.subTest(mode=mode):
                child, owner = self._finite_build_control(mode)
                with self.assertRaises(subprocess.TimeoutExpired) as timeout:
                    probe._wait_build_until(owner, _CLOCK() - 1, child.argv)
                self.assertEqual(timeout.exception.timeout, 1800)
                self.assertIsNone(owner.terminal)
                record = probe._close_build_owned(owner)
                self.assertIs(record['absence_verified'], True)
                self.assertIs(owner.closed, True)
                signals = [row for row in owner.ledger if row.get('event') == 'build-signal']
                expected = [signal.SIGTERM] if mode == 'normal' else [signal.SIGTERM, signal.SIGKILL]
                self.assertEqual([row['signal'] for row in signals], expected)
                self.assertTrue(all(row['succeeded'] is True for row in signals))
                self.assertEqual(record['terminal'][2], -expected[-1])
                with mock.patch.object(probe.os, 'killpg', side_effect=AssertionError('build signal after reap')) as kill:
                    self.assertIs(probe._signal_build_owned(owner, signal.SIGKILL), False)
                    kill.assert_not_called()

    def test_actual_build_echild_stays_unknown_and_has_no_signal_authority(self):
        child, owner = self._finite_build_control()
        child.request_exit(0)
        actual_fixture_wait = child.fixture_reap()
        self.assertEqual(actual_fixture_wait[0], owner.pid)
        self.assertIsNone(owner.terminal)
        with mock.patch.object(probe.os, 'killpg', side_effect=AssertionError('unknown build wait signal')) as kill:
            with self.assertRaises(ChildProcessError):
                probe._close_build_owned(owner)
            kill.assert_not_called()
        self.assertIsNone(owner.terminal)
        self.assertIs(owner.closed, False)
        self.assertEqual([row for row in owner.ledger if row.get('event') == 'build-signal'], [])
        self.assertTrue(any(row.get('event') == 'build-waitpid' and 'error' in row for row in owner.ledger))

    def test_actual_timed_timeout_keeps_original_error_with_real_cleanup_and_evidence_secondaries(self):
        root = self.new_path('finite-timed-timeout-first-error')
        root.mkdir()
        script = root / 'controlled_build.py'
        script.write_text('import time\nwhile True:\n    time.sleep(0.1)\n')
        command = [str(Path(sys.executable).resolve(strict=True)), '-I', '-S', '-B', str(script)]
        original_wait, original_close, original_write = probe._wait_build_until, probe._close_build_owned, probe._write
        cleanup_error = RuntimeError('finite secondary after actual build cleanup')
        evidence_error = RuntimeError('finite secondary after actual build evidence write')
        originals, cleanups = [], []

        def actual_expired_deadline(owner, deadline, actual_command):
            self.assertIs(probe._observe_build_owned(owner), True)
            try:
                return original_wait(owner, _CLOCK() - 1, actual_command)
            except subprocess.TimeoutExpired as error:
                originals.append(error)
                raise

        def actual_close_then_secondary(owner):
            cleanups.append(original_close(owner))
            raise cleanup_error

        def actual_write_then_secondary(path, value):
            original_write(path, value)
            if path == root / 'build-7.json':
                raise evidence_error

        with mock.patch.object(probe, '_wait_build_until', side_effect=actual_expired_deadline):
            with mock.patch.object(probe, '_close_build_owned', side_effect=actual_close_then_secondary):
                with mock.patch.object(probe, '_write', side_effect=actual_write_then_secondary):
                    with self.assertRaises(subprocess.TimeoutExpired) as raised:
                        probe._build_command(command, root, probe._environment(self.repository), root, 7)
        self.assertEqual(len(originals), 1)
        self.assertIs(raised.exception, originals[0])
        self.assertEqual(raised.exception.timeout, 1800)
        self.assertTrue(any(str(cleanup_error) in note for note in raised.exception.__notes__))
        self.assertTrue(any(str(evidence_error) in note for note in raised.exception.__notes__))
        self.assertEqual(len(cleanups), 1)
        self.assertIs(cleanups[0]['absence_verified'], True)
        self.assertIsNotNone(cleanups[0]['terminal'])
        record = json.loads((root / 'build-7.json').read_bytes())
        self.assertIn('TimeoutExpired', record['error'])
        self.assertIs(record['cleanup']['absence_verified'], False)
        self.assertIn(str(cleanup_error), record['cleanup']['error'])
        self.assertTrue(any(str(cleanup_error) in text for text in record['secondary_errors']))
        self.assertTrue(any(row.get('event') == 'build-getpgid-after-reap'
                            and row['result'] == 'ESRCH' for row in record['native_ledger']))

    def test_actual_post_popen_startup_failure_retains_private_cleanup_in_public_attempt_journal(self):
        # Reused genuine build is finite failed-negative setup only. No second
        # build positive, HTTP positive, returned owner, or native admission exists.
        target = self.new_path('finite-post-popen-startup-failure')
        failure = RuntimeError('original finite failure after actual compiled startup observation')
        original_observe = probe._observe_owned_role
        failed_owners = []

        def actual_startup_then_once_failure(owner, listener=True):
            result = original_observe(owner, listener=listener)
            if owner.role == 'before' and not failed_owners:
                failed_owners.append(owner)
                raise failure
            return result

        reused_negative_build = (self.output / 'candidate', self.result['build'], self.result['source'])
        with mock.patch.object(probe, '_build_candidate_only', return_value=reused_negative_build) as builder:
            with mock.patch.object(probe, '_observe_owned_role', side_effect=actual_startup_then_once_failure):
                with mock.patch.object(probe, '_capture_owned_pair',
                                       side_effect=AssertionError('HTTP after startup failure')) as capture:
                    with self.assertRaises(RuntimeError) as raised:
                        probe.run_probe(self.repository, self.repository / '.local/m6-hosted/baseline', target)
                    capture.assert_not_called()
        self.assertIs(raised.exception, failure)
        builder.assert_called_once()
        self.assertEqual(len(failed_owners), 1)
        owner = failed_owners[0]
        self.assertIsNotNone(owner.terminal)
        self.assertIs(owner.closed, True)
        record = json.loads((target / 'result.json').read_bytes())
        self.assertIs(record['passed'], False)
        self.assertIn(str(failure), record['body_error'])
        self.assertEqual(record['cleanup'], [])
        self.assertEqual(len(record['creation_attempts']), 1)
        attempt = record['creation_attempts'][0]
        self.assertEqual(attempt['role'], 'before')
        ledger = attempt['native_ledger']
        self.assertEqual([row['pid'] for row in ledger if row.get('event') == 'serve-created'], [owner.pid])
        self.assertTrue(any(row.get('event') == 'owner' and row['pid'] == owner.pid for row in ledger))
        snapshots = [row['records'] for row in ledger if row.get('event') == 'failed-start-cleanup']
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(len(snapshots[0]), 1)
        cleanup = snapshots[0][0]
        self.assertIs(cleanup['absence_verified'], True)
        self.assertEqual(cleanup['terminal'], list(owner.terminal))
        self.assertTrue(any(row.get('event') == 'waitpid' and row.get('result', [0])[0] == owner.pid
                            for row in cleanup['native_ledger']))
        self.assertTrue(any(row.get('event') == 'signal' and row['succeeded'] is True
                            for row in cleanup['native_ledger']))
        self.assertTrue((target / 'logs/before/serve.stdout').is_file())
        self.assertTrue((target / 'logs/before/serve.stderr').is_file())
        json.dumps(record)  # Snapshot can be serialized; its own ledger has no cycle.


    def test_actual_exit_during_successful_native_command_view_is_reaped_before_live_parse(self):
        child, owner = self._finite_build_control()
        command_query = ['/bin/ps', '-ww', '-p', str(owner.pid), '-o', 'command=']
        original_native = probe._native
        start = len(owner.ledger)
        returned_views, pre_wait_indices = [], []
        fixture_kernel_start = len(self._method_kernel)

        def actual_successful_command_view_then_exit(argv, ledger):
            if argv != command_query or returned_views:
                return original_native(argv, ledger)
            self.assertIs(ledger, owner.ledger)
            self.assertIsNone(owner.terminal)
            self.assertEqual(ledger[-1]['event'], 'build-waitpid')
            self.assertEqual(ledger[-1]['result'], [0, 0])
            pre_wait_indices.append(len(ledger) - 1)
            before_exit = original_native(argv, ledger)
            self.assertEqual(before_exit[0], 0)
            self.assertEqual(before_exit[1].strip(), ' '.join(owner.argv))
            self.assertEqual(before_exit[2], '')
            child.request_exit(0)
            ledger.append({'event': 'fixture-requested-controlled-exit', 'pid': owner.pid,
                           'requested_exit_code': 0})
            deadline = _CLOCK() + 10
            while _CLOCK() < deadline:
                # These are real ps subprocesses. This wrapper never waits for
                # or reaps the controlled child. Every raw result is retained.
                actual_view = original_native(argv, ledger)
                if (actual_view[0] == 0 and actual_view[1].strip()
                        and actual_view[1].strip() != ' '.join(owner.argv)):
                    self.assertEqual(actual_view[2], '')
                    self.assertIsNone(owner.terminal)
                    returned_views.append(actual_view)
                    return actual_view
                _SLEEP(0.01)
            self.fail('no actual successful changed command view before the bounded deadline')

        with mock.patch.object(probe, '_native', side_effect=actual_successful_command_view_then_exit):
            self.assertIs(probe._observe_build_owned(owner), False)
        self.assertEqual(len(returned_views), 1)
        self.assertEqual(len(pre_wait_indices), 1)
        self.assertGreaterEqual(pre_wait_indices[0], start)
        self.assertFalse(any(row['event'] == '_WAITPID' and row['arguments'][0] == owner.pid
                             for row in self._method_kernel[fixture_kernel_start:]))
        self.assertIsNotNone(owner.terminal)
        self.assertEqual(owner.terminal[0], owner.pid)
        self.assertTrue(os.WIFEXITED(owner.terminal[1]))
        self.assertEqual(owner.terminal[2], 0)
        terminal_indices = [index for index, row in enumerate(owner.ledger)
                            if index >= start and row.get('event') == 'build-waitpid'
                            and row.get('result', [0])[0] == owner.pid]
        self.assertEqual(len(terminal_indices), 1)
        raw_indices = [index for index, row in enumerate(owner.ledger)
                       if index >= start and row.get('argv') == command_query and 'stdout_hex' in row]
        self.assertGreaterEqual(len(raw_indices), 2)
        last_raw = owner.ledger[raw_indices[-1]]
        self.assertEqual(last_raw['exit_code'], 0)
        self.assertEqual(last_raw['stdout_hex'], returned_views[0][1].encode('utf-8').hex())
        self.assertEqual(last_raw['stderr_hex'], '')
        self.assertGreater(terminal_indices[0], raw_indices[-1])
        self.assertTrue(any(row.get('event') == 'build-live-observation-ended-at-terminal'
                            and row['argv'] == command_query for row in owner.ledger[start:]))
        self.assertFalse(any(row.get('event') == 'build-live-identity-complete'
                             for row in owner.ledger[start:]))
        cleanup = probe._close_build_owned(owner)
        self.assertIs(cleanup['absence_verified'], True)
        self.assertIs(owner.closed, True)
        self.assertEqual(cleanup['terminal'][2], 0)
        self.assertTrue(any(row.get('event') == 'build-getpgid-after-reap'
                            and row['result'] == 'ESRCH' for row in owner.ledger))
        with mock.patch.object(probe.os, 'killpg', side_effect=AssertionError('signal after interleaved reap')) as kill:
            for kind in (signal.SIGTERM, signal.SIGKILL):
                self.assertIs(probe._signal_build_owned(owner, kind), False)
            kill.assert_not_called()
        self.assertEqual([row for row in owner.ledger[start:] if row.get('event') == 'build-signal'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
