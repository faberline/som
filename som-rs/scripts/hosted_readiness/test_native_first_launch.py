"""GitHub-only control fixtures for the exact adjacent V3 bootstrap.

Every kernel/source/FD/owner record below is an explicit test double.
No fixture proves the first native launch, original native values, a constructor,
an operational role policy, Context/factory, HTTP, SourceSeal or product gates.
Do not run this source from a scratch packet or use a primary old bootstrap.
"""
from __future__ import annotations

import _imp
import ast
from contextlib import ExitStack
import copy
import errno
import hashlib
import importlib.machinery
import json
import marshal
import os
from pathlib import Path, PurePosixPath
import shlex
import signal
import stat
import struct
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'native_source_bootstrap.py'
SOURCE_SHA = 'd69f36f0c94b32a3ad50c2d6d67805af71782155d62cdf79d3d7aa32a0647266'
PROFILE_SHA = '0937e44ad864c497e04f56fd07f6e048c7a538e072f27bdfdd58708921d198bb'
WORKFLOW_SHA = '9b9d16b06cec79b72a74b734c5e114686c2db97e5936048242b5778d02c6ef4c'
RUNTIME = '/home/runner/work/_temp/som-product-runtime-qualification'
PYTHON = RUNTIME + '/install/python/bin/python3.12'
TEMP = '/home/runner/work/_temp'
EVIDENCE = TEMP + '/som-native-origin-before-node-evidence'
TASK_TMP = TEMP + '/som-native-origin-before-node-tmp'
REVISION = 'a' * 40
DEV = os.makedev(8, 1)
SEQUENCE = ['source_workflow_bound', 'fixed_runtime_and_profile_joined',
            'kernel_process_joined', 'private_environment_checked',
            'module_loader_and_host_graph_joined', 'stable_readback']


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def assembly_bytes():
    """Read only the exact future same-directory assembly; no alternate path."""
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('GitHub Linux Python3.12 control fixture only')
    paths = (HERE / 'native_source_bootstrap.py', HERE / 'native_first_launch_profile_v1.json',
             REPO / '.github/workflows/rust-same-candidate-products.yml')
    result = []
    for path, expected, size in zip(paths, (SOURCE_SHA, PROFILE_SHA, WORKFLOW_SHA), (128059, 85991, 40974)):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise RuntimeError('physical future assembly source required')
        initial = path.lstat()
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1 or stat.S_IMODE(initial.st_mode) != 0o644 or initial.st_size != size:
            raise RuntimeError('exact regular future assembly file/mode/size required')
        raw = path.read_bytes()
        final = path.lstat()
        if digest(raw) != expected or (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns):
            raise RuntimeError('exact adjacent V3 source/profile/workflow required; no old-source fallback')
        result.append(raw)
    return tuple(result)


def info(*, mode=stat.S_IFREG | 0o644, uid=0, gid=0, ino=17, size=3, nlink=1, dev=DEV):
    return SimpleNamespace(st_dev=dev, st_ino=ino, st_mode=mode, st_nlink=nlink,
                           st_uid=uid, st_gid=gid, st_size=size,
                           st_mtime_ns=1000000000, st_ctime_ns=1000000000)


def identity(value):
    return tuple(getattr(value, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_nlink',
                 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def data_row(raw=b'abc', *, path='/synthetic/data', sha=None, mode=0o644, ino=17):
    return {'raw': raw, 'path': path, 'size': len(raw), 'sha256': sha or digest(raw),
            'identity': identity(info(mode=stat.S_IFREG | mode, ino=ino, size=len(raw)))}


def stat_bytes(**changes):
    fields = {number: 0 for number in range(4, 53)}
    fields.update({4: 41, 5: 40, 6: 39, 8: -1, 20: 1, 22: 999})
    fields.update(changes.get('fields', {}))
    values = [changes.get('state', 'R'), *(str(fields[number]) for number in range(4, 53))]
    return (str(changes.get('pid', 42)) + ' (' + changes.get('comm', 'python3.12') + ') '
            + ' '.join(values) + '\n').encode()


def argv(root=REPO, revision=REVISION):
    return [PYTHON, '-I', '-S', '-B', str(root / SELF), '--repo', str(root),
            '--helper-revision', revision, '--candidate-revision', revision,
            '--entry-role', 'entry.ts-input-consumer', '--', '--repo', str(root),
            '--helper-revision', revision, '--candidate-revision', revision, 'before-node']


def command_bytes(values):
    return b'\0'.join(os.fsencode(value) for value in values) + b'\0'


def _traceback_rows(traceback):
    while traceback:
        yield traceback
        traceback = traceback.tb_next


def _raise_fixed_fault(error):
    """Retain the real first raise nodes once, without tracing or fake frames."""
    try:
        raise error
    except BaseException:
        try:
            BaseException.__getattribute__(error, '__fixture_first_throw_nodes__')
        except AttributeError:
            BaseException.__setattr__(error, '__fixture_first_throw_nodes__', tuple(_traceback_rows(error.__traceback__)))
        raise


class FixedForeignExpectedLease:
    """One inert expected-class data fault; never an owner or authority."""
    pass


def map_bytes(rows):
    return ''.join(f"{row['start']:x}-{row['end']:x} {row['permissions']} {row['offset']:x} "
                   f"{row['dev'][0]:02x}:{row['dev'][1]:02x} {row['inode']}"
                   + (' ' + row['path'] if row['path'] else '') + '\n' for row in rows).encode()


def map_row(path='', *, start=0x1000, permissions='rw-p', inode=0, dev=(0, 0), size=4096):
    return {'start': start, 'end': start + size, 'permissions': permissions, 'offset': 0,
            'dev': list(dev), 'inode': inode, 'path': path, 'category': 'file' if path.startswith('/') else 'private-anonymous'}


def elf_bytes(*, needed=(), soname=None, search=None, interpreter=None):
    """Build bounded inert ELF test data; never write/load/execute this ELF."""
    strings = bytearray(b'\0')
    indexes = {}
    for value in (*needed, *(() if soname is None else (soname,)), *(() if search is None else (search,))):
        if value not in indexes:
            indexes[value] = len(strings)
            strings.extend(value.encode() + b'\0')
    dynamic = [(5, 0x300), (10, len(strings)), *((1, indexes[name]) for name in needed)]
    if soname is not None:
        dynamic.append((14, indexes[soname]))
    if search is not None:
        dynamic.append((29, indexes[search]))
    dynamic.append((0, 0))
    raw = bytearray(max(0x400, 0x300 + len(strings)))
    raw[:7] = b'\x7fELF\x02\x01\x01'
    struct.pack_into('<HH', raw, 16, 2 if interpreter else 3, 62)
    struct.pack_into('<Q', raw, 32, 64)
    struct.pack_into('<HHH', raw, 52, 64, 56, 3 if interpreter else 2)
    struct.pack_into('<IIQQQQQQ', raw, 64, 1, 5, 0, 0, 0, len(raw), 4096 + len(raw), 4096)
    struct.pack_into('<IIQQQQQQ', raw, 120, 2, 4, 0x200, 0x200, 0, len(dynamic) * 16, len(dynamic) * 16, 8)
    if interpreter:
        body = interpreter.encode() + b'\0'
        struct.pack_into('<IIQQQQQQ', raw, 176, 3, 4, 0x180, 0x180, 0, len(body), len(body), 1)
        raw[0x180:0x180 + len(body)] = body
    for index, pair in enumerate(dynamic):
        struct.pack_into('<QQ', raw, 0x200 + index * 16, *pair)
    raw[0x300:0x300 + len(strings)] = strings
    return bytes(raw)


class OSData:
    """Process-local OS/source-FD doubles; never operate on the host paths."""
    def __init__(self):
        self.events, self.stats, self.data, self.links = [], {}, {}, {}
        self.read_chunks, self.fstat_rows = None, None
        self.open_error = self.read_error = self.fstat_error = None
        self.close_errors, self.offsets = {}, {}
        self.report_files, self.current_leaf = {}, None
        self.write_error = self.fsync_error = None
        self.write_limit, self.write_zero = 17, False
        self.evidence_exists = False
        self.directory_error = self.report_open_error = None
        self.report_stat_change = None
        self.listdir_rows = {}
        # Only these two fixed symlinks can appear at the opened-name boundary.
        # lstat/physical-directory preflight describes the earlier directory.
        self.directory_symlink = self.report_symlink = False
        self.directory_followed = self.report_followed = False
        self.directory_sentinel = bytearray(b'keep fixed directory target')
        self.report_sentinel = bytearray(b'keep fixed report target')
        self.directory_symlink_error = OSError(errno.ENOTDIR, 'fixed directory symlink rejected by O_NOFOLLOW/O_DIRECTORY')
        self.report_symlink_error = FileExistsError(errno.EEXIST, 'fixed report symlink rejected by O_CREAT/O_EXCL')
        self.report_nofollow_error = OSError(errno.ELOOP, 'fixed report symlink rejected by O_NOFOLLOW')

    def lstat(self, path):
        path = str(path)
        if path in self.stats:
            return self.stats[path]
        return info(mode=stat.S_IFDIR | (0o700 if path in (EVIDENCE, TASK_TMP) else 0o755), ino=500, size=0)

    def put(self, path, raw, **changes):
        self.data[str(path)] = raw
        self.stats[str(path)] = info(size=len(raw), **changes)

    def open(self, path, flags, mode=None, *, dir_fd=None):
        path = str(path)
        self.events.append(('open', path, flags, mode, dir_fd))
        if self.open_error:
            _raise_fixed_fault(self.open_error)
        if path == EVIDENCE:
            if self.directory_error:
                _raise_fixed_fault(self.directory_error)
            if self.directory_symlink:
                if flags & os.O_NOFOLLOW:
                    self.events.append(('symlink-refused', 'directory', flags, dir_fd))
                    _raise_fixed_fault(self.directory_symlink_error)
                self.directory_followed = True
                self.events.append(('symlink-followed', 'directory', flags, dir_fd))
            return 51
        if dir_fd is not None:
            if self.report_open_error:
                _raise_fixed_fault(self.report_open_error)
            if path == 'initial-launch.json' and self.report_symlink:
                if flags & os.O_CREAT and flags & os.O_EXCL:
                    self.events.append(('symlink-refused', 'report-excl', flags, dir_fd))
                    _raise_fixed_fault(self.report_symlink_error)
                if flags & os.O_NOFOLLOW:
                    self.events.append(('symlink-refused', 'report-nofollow', flags, dir_fd))
                    _raise_fixed_fault(self.report_nofollow_error)
                self.report_followed = True
                self.events.append(('symlink-followed', 'report', flags, dir_fd))
                self.current_leaf = '__fixed_report_symlink_target__'
                self.report_files[self.current_leaf] = self.report_sentinel
                return 52
            if path in self.report_files:
                raise FileExistsError('fixed fixture existing report')
            self.current_leaf = path
            self.report_files[path] = bytearray()
            return 52
        self.current_read = path
        self.offsets[17] = 0
        return 17

    def fstat(self, fd):
        self.events.append(('fstat', fd))
        if self.fstat_error:
            _raise_fixed_fault(self.fstat_error)
        if fd == 51:
            if self.directory_followed:
                return info(mode=stat.S_IFDIR | 0o755, ino=777, size=0)
            return self.lstat(EVIDENCE)
        if fd == 52:
            row = info(mode=stat.S_IFREG | 0o600, ino=501, size=len(self.report_files[self.current_leaf]))
            if self.report_stat_change:
                row = self.report_stat_change(row)
            return row
        if self.fstat_rows:
            return self.fstat_rows.pop(0)
        return self.lstat(self.current_read)

    def read(self, fd, count):
        self.events.append(('read', fd, count))
        if self.read_error:
            _raise_fixed_fault(self.read_error)
        if self.read_chunks is not None:
            return self.read_chunks.pop(0) if self.read_chunks else b''
        raw = self.data[self.current_read]
        at = self.offsets[fd]
        self.offsets[fd] += min(count, len(raw) - at)
        return raw[at:at + count]

    def close(self, fd):
        self.events.append(('close', fd))
        if fd in self.close_errors:
            _raise_fixed_fault(self.close_errors[fd])

    def write(self, fd, raw):
        self.events.append(('write', fd))
        if self.write_error:
            _raise_fixed_fault(self.write_error)
        if self.write_zero:
            return 0
        count = min(self.write_limit, len(raw))
        self.report_files[self.current_leaf].extend(raw[:count])
        return count

    def fsync(self, fd):
        self.events.append(('fsync', fd))
        if self.fsync_error:
            _raise_fixed_fault(self.fsync_error)

    def mkdir(self, path, mode):
        self.events.append(('mkdir', str(path), mode))
        if self.evidence_exists:
            raise FileExistsError('fixed fixture existing directory')
        self.evidence_exists = True

    def listdir(self, path):
        path = str(path)
        if path.endswith('/task'):
            return ['42']
        return self.listdir_rows.get(path, [])

    def readlink(self, path):
        return self.links.get(str(path), '42' if str(path) == '/proc/self' else PYTHON)

    def path_type(self):
        model = self
        class DataPath(PurePosixPath):
            def lstat(self): return model.lstat(self)
            def is_symlink(self): return str(self) in model.links
            def exists(self): return str(self) in model.data or str(self) in model.stats
        return DataPath


class SnapshotData:
    def __init__(self, raw):
        self.root, self.revision = REPO, REVISION
        self.raw, self.calls = raw, []
        self.workflows = {}
        for name, expected, size in (
                ('.github/workflows/rust-hosted-readiness.yml', '65f136f9579330e3bf87a151c6e28f063dfd4c1a53d6a579794480cb98baf977', 64432),
                ('.github/workflows/rust-same-candidate-products.yml', WORKFLOW_SHA, 40974)):
            path = self.root / name
            if any(item.is_symlink() for item in (path, *path.parents)):
                raise AssertionError('linked fixed synthetic workflow input')
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o644 or before.st_nlink != 1 or before.st_size != size:
                raise AssertionError('exact fixed synthetic workflow file identity required')
            body = path.read_bytes(); after = path.lstat()
            if digest(body) != expected or identity(before) != identity(after):
                raise AssertionError('exact reviewed future workflow bytes required')
            self.workflows[name] = body
        oid = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        self.entries = {SELF: (b'100644', b'blob', oid.encode())}
    def read(self, path):
        if path not in (SELF, BASE + 'native_first_launch_profile_v1.json', BASE + 'product_runtime/source-pins.json',
                '.github/workflows/rust-hosted-readiness.yml', '.github/workflows/rust-same-candidate-products.yml'):
            raise AssertionError('unselected synthetic snapshot data path')
        self.calls.append(('read', path))
        if path in self.workflows:
            return self.workflows[path]
        return self.raw if path == SELF else (REPO / path).read_bytes()
    def verify(self): self.calls.append(('verify',))


class OwnerAcquisitionData:
    """Only three fixed post-enter synthetic owner faults for F19."""
    def __init__(self, owner, fault):
        self.owner, self.fault = owner, fault
    def __enter__(self):
        owner = self.owner.__enter__()
        self.original = (owner._initial_main, owner._active, owner._admission)
        if self.fault == 'foreign-main': owner._initial_main = ModuleType('fixed-foreign-main')
        elif self.fault == 'preloaded-active': owner._active = object()
        elif self.fault == 'preloaded-admission': owner._admission = object()
        else: raise AssertionError('unselected synthetic owner fault')
        return owner
    def __exit__(self, kind, value, traceback):
        try:
            return self.owner.__exit__(kind, value, traceback)
        finally:
            self.owner._initial_main, self.owner._active, self.owner._admission = self.original


class Fixture:
    """Only this synthetic module, OS object and synthetic lease are changed."""
    def __init__(self, case):
        self.case, self.stack = case, ExitStack()
        case.addCleanup(self.stack.close)
        self.raw, self.profile_raw, self.workflow_raw = assembly_bytes()
        self.module = ModuleType('_synthetic_first_launch_control')
        self.module.__file__, self.module.__package__ = str(HERE / 'native_source_bootstrap.py'), ''
        exec(compile(self.raw, self.module.__file__, 'exec', dont_inherit=True), self.module.__dict__)
        original_namespace = dict(vars(self.module))
        def restore_owned_namespace():
            vars(self.module).clear()
            vars(self.module).update(original_namespace)
        self.stack.callback(restore_owned_namespace)
        self.actual = {name: value for name, value in vars(self.module).items() if name.startswith('_native_')}
        self.osdata = OSData()
        self.os = SimpleNamespace(**vars(os))
        self.os.path = SimpleNamespace(**vars(os.path))
        for name in ('open', 'fstat', 'read', 'close', 'write', 'fsync', 'mkdir', 'listdir', 'readlink', 'lstat'):
            setattr(self.os, name, getattr(self.osdata, name))
        self.os.access = lambda path, flags: True
        self.os.getpid, self.os.getppid, self.os.getcwd = lambda: 42, lambda: 41, lambda: str(REPO)
        self.os.uname = lambda: os.uname_result(('Linux', 'fixture', '6.8', 'fixed', 'x86_64'))
        self.os.path.lexists = lambda path: str(path) in self.osdata.data or str(path) in self.osdata.links
        self.os.path.islink = lambda path: str(path) in self.osdata.links
        self.os.path.realpath = lambda path: {'/usr/lib/ssl/openssl.cnf': '/etc/ssl/openssl.cnf',
            '/usr/lib/ssl/cert.pem': '/etc/ssl/certs/ca-certificates.crt',
            '/usr/lib64/ld-linux-x86-64.so.2': '/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'}.get(str(path), str(path))
        self.sys = SimpleNamespace(**vars(sys))
        self.sys.flags = SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1, optimize=0)
        self.sys.version_info, self.sys.platform, self.sys.pycache_prefix = (3, 12, 11), 'linux', None
        self.sys.executable, self.sys.base_prefix = PYTHON, RUNTIME + '/install/python'
        self.sys.implementation = SimpleNamespace(cache_tag='cpython-312')
        self.sys.modules = {'__main__': self.module, '_imp': _imp, 'marshal': marshal}
        self.sys.gettrace, self.sys.getprofile = lambda: None, lambda: None
        self.sys.monitoring = SimpleNamespace(get_tool=lambda number: None)
        self.sys.orig_argv, self.sys.argv = argv(), argv()[4:]
        self.os.environ = {key: 'fixture-' + key for key in self.module._PROCESS_KEYS}
        self.os.environ.update(GITHUB_JOB='required-callbacks', RUNNER_TEMP=TEMP, HOME='/home/runner',
            PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8', TMPDIR=TASK_TMP,
            SOM_DEPENDENCY_ACTIONS_TOKEN='fixed-synthetic-noncredential')
        self.module.os, self.module.sys, self.module.Path = self.os, self.sys, self.osdata.path_type()
        self.snapshot = SnapshotData(self.raw)
        self.events, self.markers, self.read_rows = [], [], {}
        self.print_error = None
        self.binding = {'job': 'required-callbacks', 'repository': 'faberline/som', 'run_id': 101,
            'run_attempt': 2, 'job_id': 202, 'runner_name': 'fixture-runner',
            'github_workflow_ref': 'fixed-source-ref', 'github_workflow_sha': REVISION,
            'process_environment': tuple((key, self.os.environ.get(key)) for key in self.module._PROCESS_KEYS)}

    def owner(self):
        return self.module._BootstrapLease(self.snapshot, dict(self.binding), SELF,
            (tuple(self.sys.meta_path), self.module.importlib.util.spec_from_file_location), self.module._LEASE_KEY)

    def source_read(self, role, owner):
        self.events.append(('role', role))
        row = self.read_rows[role]
        if isinstance(row, BaseException):
            _raise_fixed_fault(row)
        return copy.deepcopy(row)

    def output(self, *values, **options):
        text = ' '.join(str(value) for value in values)
        self.events.append(('marker', text, options))
        self.osdata.events.append(('print-flush', text, options))
        if self.print_error:
            _raise_fixed_fault(self.print_error)
        self.markers.append(text)

    def drive(self, *, active=(), changes=None, role='entry.ts-input-consumer', operation='before-node', job='required-callbacks', owner_fault=None):
        m = self.module
        self.binding['job'] = job
        self.os.environ['GITHUB_JOB'] = job
        self.binding['process_environment'] = tuple((key, self.os.environ.get(key)) for key in m._PROCESS_KEYS)
        owner = self.owner()
        self.last_owner = owner
        executable = data_row(b'fixture-ELF', path=PYTHON, sha=m._NATIVE_LAUNCH_PYTHON_SHA, mode=0o755)
        self.read_rows.setdefault('proc-stat', data_row(stat_bytes()))
        self.read_rows.setdefault('boot-id', data_row(b'00000000-0000-0000-0000-000000000001\n'))
        self.read_rows.setdefault('proc-cmdline', data_row(command_bytes(argv())))
        self.read_rows.setdefault('proc-exe', executable)
        self.read_rows.setdefault(('installed', 'install/python/bin/python3.12'), executable)
        self.read_rows.setdefault('proc-maps', data_row(map_bytes([map_row()])))
        files = {'profile': {}, 'inventory': {}, 'source': {'candidate_revision': REVISION}, 'runtime': {}}
        selected = {
            '_native_launch_read': self.source_read,
            '_native_launch_files': lambda owner: files,
            '_native_launch_environment': lambda owner: {'private_names_only': ['SOM_DEPENDENCY_ACTIONS_TOKEN']},
            '_native_launch_modules': lambda owner, installed, profile: {'modules': [], 'aliases': [], 'code': [], 'native_files': {}},
            '_native_launch_host_closure': lambda maps, native_rows: {'ELF_graph': {}, 'host': {}},
            '_native_launch_report': lambda owner, phase, row: self.events.append(('report', phase, copy.deepcopy(row))),
        }
        if set(changes or {}) - set(selected) or set(active) - set(selected):
            raise AssertionError('unselected synthetic acquisition boundary')
        selected.update(changes or {})
        with ExitStack() as stack:
            for name, value in selected.items():
                stack.enter_context(mock.patch.object(m, name, self.actual[name] if name in active else value))
            acquisition = owner if owner_fault is None else OwnerAcquisitionData(owner, owner_fault)
            stack.enter_context(mock.patch.object(m, 'checked_bootstrap', return_value=acquisition))
            stack.enter_context(mock.patch.object(m._BootstrapLease, 'execute_entry',
                lambda lease, selected_role, values: self.events.append(('target', selected_role, list(values)))))
            stack.enter_context(mock.patch.object(m, 'print', self.output, create=True))
            values = ['--repo', str(REPO), '--helper-revision', REVISION, '--candidate-revision', REVISION,
                      '--entry-role', role, '--', '--repo', str(REPO), '--helper-revision', REVISION,
                      '--candidate-revision', REVISION, operation]
            result = m.main(values)
        return result


class FileGraph:
    """Synthetic complete inventory and metadata acquisition rows for F09/F10.

    The inventory/original receipt reader rows explicitly declare saved pins.
    Their synthetic bytes do not have those saved hashes. This is the selected
    reader boundary double, not a byte audit or a runtime identity assertion.
    Real reader hashing is tested separately in F05/F06.
    """
    def __init__(self, f):
        self.f, self.m = f, f.module
        names = ('runtime_capsule.py', 'reuse_runtime.py', 'qualify_runtime.py',
                 'runtime-bf3-qualification.json', 'runtime-bf3-byte-audit.json', 'source-pins.json')
        self.repo = {BASE + 'product_runtime/' + name: (HERE / 'product_runtime' / name).read_bytes() for name in names}
        self.repo[BASE + 'product_python/wheel-policy.json'] = (HERE / 'product_python/wheel-policy.json').read_bytes()
        self.q = json.loads(self.repo[BASE + 'product_runtime/runtime-bf3-qualification.json'])
        targets = {name.rsplit('/', 1)[0] + '/' + target for name, target in self.q['aliases'].items()}
        cache_dir = 'install/python/lib/python3.12/__pycache__/'
        self.caches = [cache_dir + 'fixture.cpython-312' + suffix + '.pyc' for suffix in ('', '.opt-1', '.opt-2')]
        regular = sorted({'install/python/bin/python3.12', *targets, *self.caches})
        regular += ['install/fixture/f' + str(index).zfill(5) for index in range(10102 - len(regular))]
        self.inventory, self.transport, self.installed = {}, {}, {}
        for index, name in enumerate(regular):
            raw, executable = name.encode(), name == 'install/python/bin/python3.12'
            mode = 0o755 if executable else 0o644
            self.inventory[name] = {'kind': 'regular_file', 'sha256': digest(raw), 'executable_bit': executable}
            self.transport[name] = {'kind': 'file', 'mode': mode, 'size': len(raw), 'sha256': digest(raw)}
            self.installed[name] = data_row(raw, path=RUNTIME + '/' + name, mode=mode, ino=1000 + index)
        for name, target in self.q['aliases'].items():
            self.inventory[name] = {'kind': 'alias_not_admitted', 'target': target, 'sha256': digest(target.encode()),
                'qualification_probe_executed': False, 'runtime_admitted': False}
            self.transport[name] = {'kind': 'alias', 'target': target, 'mode': 0o777}
            f.osdata.links[RUNTIME + '/' + name] = target
        self.directory_names = {'install'}
        for name in self.inventory:
            self.directory_names.update('/'.join(name.split('/')[:index]) for index in range(1, len(name.split('/'))))
        self.walk_rows = []
        for directory in sorted(self.directory_names):
            prefix = directory + '/'
            dirs = [name[len(prefix):] for name in sorted(self.directory_names)
                    if name.startswith(prefix) and '/' not in name[len(prefix):]]
            leaves = [name[len(prefix):] for name in sorted(self.inventory)
                      if name.startswith(prefix) and '/' not in name[len(prefix):]]
            self.walk_rows.append((RUNTIME + '/' + directory, dirs, leaves))
        self.archive = data_row(b'fixed synthetic archive', path=TEMP + '/som-current-inputs/runtime/runtime.tar')
        github = {key: f.binding[key] for key in ('repository', 'run_id', 'run_attempt')}
        sources = {name: digest(raw) for name, raw in self.repo.items() if name != BASE + 'product_python/wheel-policy.json'}
        self.reuse = {'version': 'som.product-runtime-reuse.v1', 'status': 'audited_runtime_reinstalled_and_rechecked_for_hosted_tests',
            'tested_sha': REVISION, 'installed_tree_verified_before': True, 'installed_tree_verified_after': True,
            'qualification_evidence_verified_after': True, 'product_runtime_ready': False, 'product_acceptance_passed': False}
        self.original = {'source_revision': self.q['source_revision']}
        self.origin = {'id': 303, 'name': 'same-run-qualified-runtime-capsule', 'expired': False,
            'digest': 'sha256:' + 'b' * 64, 'workflow_run': {'id': 101, 'head_sha': REVISION},
            'url': 'https://api.github.com/repos/faberline/som/actions/artifacts/303'}
        self.capsule = {'version': 'som.same-run-qualified-runtime-capsule.v1', 'role': 'same-run-qualified-runtime-transport',
            'github': github, 'helper_revision': REVISION, 'candidate_revision': REVISION, 'source_sha256': sources,
            'product_runtime_ready': False, 'product_acceptance_passed': False, 'runtime_prefix': RUNTIME,
            'installed_code_executed_by_capsule': False, 'archive': {'name': 'runtime.tar', 'size': self.archive['size'], 'sha256': self.archive['sha256']},
            'profile': {'inventory_sha256': self.m._NATIVE_LAUNCH_INVENTORY_SHA, 'reuse_receipt_sha256': digest(json_bytes(self.reuse))},
            'entries': self.transport}
        self.restore = {'version': 'som.same-run-qualified-runtime-capsule-restore.v1',
            'status': 'same_run_qualified_runtime_restored_without_execution', 'github': github,
            'helper_revision': REVISION, 'candidate_revision': REVISION, 'source_sha256': sources,
            'product_runtime_ready': False, 'product_acceptance_passed': False, 'installed_code_executed': False,
            'complete_runtime_tree_verified': True, 'runtime_root': RUNTIME, 'manifest_sha256': digest(json_bytes(self.capsule)),
            'qualification_profile': copy.deepcopy(self.capsule['profile']), 'artifact_origin_sha256': digest(json_bytes(self.origin)),
            'artifact_id': 303, 'artifact_digest': 'b' * 64, 'archive_sha256': self.archive['sha256']}

    def read(self, role, owner):
        if type(role) is tuple:
            tag, name = role
            if tag == 'repository': return data_row(self.repo[name], path=str(REPO / name))
            if tag == 'installed': return copy.deepcopy(self.installed[name])
            raise AssertionError('unselected synthetic file-graph role')
        if role == 'profile': return data_row(self.f.profile_raw)
        if role == 'inventory': return data_row(json_bytes(self.inventory), sha=self.m._NATIVE_LAUNCH_INVENTORY_SHA)
        if role == 'original': return data_row(json_bytes(self.original), sha=self.q['transport_files']['receipt.json'])
        if role == 'archive': return copy.deepcopy(self.archive)
        return data_row(json_bytes(getattr(self, role)))

    def install(self):
        self.f.stack.enter_context(mock.patch.object(self.f.module, '_native_launch_read', self.read))
        self.f.os.walk = lambda root, followlinks=False: copy.deepcopy(self.walk_rows)


class ModuleGraph:
    """Only synthetic module/spec/source/cache inputs; no module body executes."""
    def __init__(self, f):
        self.f, self.m = f, f.module
        self.profile = {'forbidden_actual_modules': [], 'source_modules': {}, 'fixed_module_aliases': {},
            'frozen_names': [], 'native_module_names': ['_imp', 'marshal'], 'platform_only_rejected_in_actual_Linux_prefix': []}
        self.installed, self.rows = {}, {}

    def read(self, role, owner):
        return copy.deepcopy(self.rows[role[1]])

    def add_source(self, name='fixture_source', raw=b'def value():\n    return 1\n', *, frozen_original=None):
        member = name.replace('.', '/') + '.py'
        relative = 'install/python/lib/python3.12/' + member
        filename = RUNTIME + '/' + relative
        self.profile['source_modules'][name] = {'member': 'Synthetic-Control/Lib/' + member, 'sha256': digest(raw), 'bytes': len(raw)}
        self.installed[relative] = {'kind': 'regular_file', 'sha256': digest(raw)}
        self.rows[relative] = data_row(raw, path=filename)
        module = ModuleType(frozen_original or name)
        if frozen_original:
            loader = importlib.machinery.FrozenImporter
            spec = importlib.machinery.ModuleSpec(frozen_original, loader, origin='frozen')
            spec._cached, spec._set_fileattr = None, False
            module.__cached__ = None
            self.profile['frozen_names'].append(frozen_original)
            self.profile['fixed_module_aliases'][frozen_original] = name
            self.f.sys.modules[frozen_original] = module
        else:
            loader = importlib.machinery.SourceFileLoader(name, filename)
            spec = importlib.machinery.ModuleSpec(name, loader, origin=filename)
            cache = str(PurePosixPath(filename).parent / '__pycache__' / (PurePosixPath(filename).stem + '.cpython-312.pyc'))
            spec._cached, spec._set_fileattr, module.__cached__ = cache, True, cache
        module.__loader__, module.__spec__, module.__file__ = loader, spec, filename
        self.f.sys.modules[name] = module
        return module, relative

    def add_cache(self, name, *, flags=0, header_valid=True, code=None):
        module = self.f.sys.modules[name]
        source_relative = str(PurePosixPath(module.__file__).relative_to(RUNTIME))
        raw = self.rows[source_relative]['raw']
        code = compile(raw, module.__file__, 'exec', dont_inherit=True, optimize=0) if code is None else code
        magic = b'\xcb\r\r\n'
        tail = (_imp.source_hash(int.from_bytes(magic, 'little'), raw) if flags & 1
                else (1).to_bytes(4, 'little') + len(raw).to_bytes(4, 'little'))
        if not header_valid: tail = b'\xff' * 8
        payload = magic + flags.to_bytes(4, 'little') + tail + marshal.dumps(code)
        relative = str(PurePosixPath(module.__cached__).relative_to(RUNTIME))
        self.rows[relative] = data_row(payload, path=module.__cached__)
        self.installed[relative] = {'kind': 'regular_file', 'sha256': digest(payload)}
        return relative

    def add_extension(self):
        name = '_fixture_native'
        relative = 'install/python/lib/python3.12/lib-dynload/' + name + '.cpython-312-x86_64-linux-gnu.so'
        path, raw = RUNTIME + '/' + relative, elf_bytes(needed=('libfixture.so',))
        module = ModuleType(name); loader = importlib.machinery.ExtensionFileLoader(name, path)
        module.__spec__, module.__loader__, module.__file__ = importlib.machinery.ModuleSpec(name, loader, origin=path), loader, path
        self.f.sys.modules[name] = module; self.profile['native_module_names'].append(name)
        self.rows[relative] = data_row(raw, path=path, mode=0o755, ino=99)
        self.installed[relative] = {'kind': 'regular_file', 'sha256': digest(raw)}
        return module, relative

    def run(self):
        with mock.patch.object(self.m, '_native_launch_read', self.read):
            return self.m._native_launch_modules(self.f.owner(), self.installed, self.profile)


class HostGraph:
    """Inert ELF/config/package rows driven through the actual orchestrator.

    The toy Python reader row declares the saved expected ELF hash to isolate
    graph checks. Its generated ELF bytes are not that qualified binary.
    Reader hashing and the real process origin remain separate gates.
    """
    def __init__(self, f, *, extension=False):
        self.f, self.m, self.rows = f, f.module, {}
        self.loader = '/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
        self.library = '/usr/lib/x86_64-linux-gnu/libfixture.so'
        self.locale = '/usr/lib/locale/C.utf8/LC_CTYPE'
        self.paths = {
            PYTHON: data_row(elf_bytes(needed=('libfixture.so',), interpreter='/lib64/ld-linux-x86-64.so.2'),
                path=PYTHON, sha=self.m._NATIVE_LAUNCH_PYTHON_SHA, mode=0o755, ino=80),
            self.loader: data_row(elf_bytes(soname='ld-linux-x86-64.so.2'), path=self.loader, mode=0o755, ino=81),
            self.library: data_row(elf_bytes(soname='libfixture.so'), path=self.library, mode=0o755, ino=82),
            self.locale: data_row(b'fixed locale', path=self.locale, ino=83),
        }
        self.native = {}
        if extension:
            path = RUNTIME + '/install/python/lib/python3.12/lib-dynload/_fixture_native.cpython-312-x86_64-linux-gnu.so'
            self.paths[path] = data_row(elf_bytes(needed=('libfixture.so',)), path=path, mode=0o755, ino=99)
            self.native[path] = {'sha256': self.paths[path]['sha256'], 'identity': self.paths[path]['identity'], 'module': '_fixture_native'}
        for path, row in self.paths.items():
            tag = ('installed', str(PurePosixPath(path).relative_to(RUNTIME))) if path.startswith(RUNTIME) else (
                ('locale-data', path.removeprefix('/usr/lib/locale/')) if path == self.locale else ('host-elf', path))
            self.rows[tag] = row
        configs = {'os-release': b'ID=ubuntu\nVERSION_ID="22.04"\n', 'nss': b'hosts: files dns # fixed\n',
            'ld-preload': b'# no active preload\n', 'openssl': b'# no external provider\n', 'ca-file': b'fixed public CA',
            'dpkg-status': b'Package: libfixture\nVersion: 1\nArchitecture: amd64\nStatus: install ok installed\n\n'}
        for name, raw in configs.items():
            self.rows[name] = data_row(raw, path='/etc/ssl/certs/ca-certificates.crt' if name == 'ca-file' else '/synthetic/' + name)
        required = [self.loader, self.library, self.locale]
        self.rows[('package-list', 'libfixture:amd64.list')] = data_row(('\n'.join(required) + '\n').encode())
        self.rows[('package-md5', 'libfixture:amd64.md5sums')] = data_row(''.join(
            hashlib.md5(self.paths[path]['raw'], usedforsecurity=False).hexdigest() + '  ' + path.removeprefix('/') + '\n' for path in required).encode())
        f.osdata.listdir_rows['/var/lib/dpkg/info'] = ['libfixture:amd64.list']
        f.osdata.links.update({'/lib': 'usr/lib', '/lib64': 'usr/lib64', '/usr/lib64/ld-linux-x86-64.so.2': self.loader})
        self.maps = []
        for index, (path, row) in enumerate(self.paths.items()):
            self.maps.append(map_row(path, start=0x1000 + index * 0x2000,
                permissions='r--p' if path == self.locale else 'r-xp', inode=row['identity'][1], dev=(8, 1)))

    def read(self, role, owner):
        if role in self.rows:
            row = self.rows[role]
            if isinstance(row, BaseException): _raise_fixed_fault(row)
            return copy.deepcopy(row)
        return self.f.source_read(role, owner)

    def run(self):
        self.f.read_rows['proc-exe'] = copy.deepcopy(self.paths[PYTHON])
        self.f.read_rows['proc-maps'] = data_row(map_bytes(self.maps))
        return self.f.drive(active=('_native_launch_host_closure',), changes={
            '_native_launch_read': self.read,
            '_native_launch_modules': lambda owner, installed, profile: {'modules': [], 'aliases': [], 'code': [], 'native_files': self.native}})


class FirstLaunchControlTests(unittest.TestCase):
    def fresh(self):
        previous = getattr(self, '_owned_fixture', None)
        if previous is not None:
            previous.stack.close()
        self._owned_fixture = Fixture(self)
        return self._owned_fixture

    def reject(self, call, message):
        try:
            call()
        except ValueError as error:
            self.assertRegex(str(error), message)
            return error
        self.fail('the actual helper did not refuse the fixed negative case')

    def assert_primary(self, call, expected, code, *, secondary_chain=()):
        try:
            call()
        except BaseException as actual:
            self.assertIs(actual, expected)
            propagated = tuple(_traceback_rows(actual.__traceback__))
            codes = [node.tb_frame.f_code for node in propagated]
            self.assertIn(code, codes)
            original = BaseException.__getattribute__(expected, '__fixture_first_throw_nodes__')
            self.assertIs(type(original), tuple)
            self.assertTrue(original, 'first fixed fault did not retain a real traceback')
            first = next((index for index, node in enumerate(propagated) if node is original[0]), None)
            self.assertIsNotNone(first, 'original first-throw traceback node was lost')
            self.assertEqual(len(propagated[first:first + len(original)]), len(original))
            for actual_node, original_node in zip(propagated[first:first + len(original)], original):
                self.assertIs(actual_node, original_node, 'original first-throw traceback chain changed')
            for index, node in enumerate(original):
                self.assertIs(node.tb_next, original[index + 1] if index + 1 < len(original) else None)
            notes = getattr(actual, '__som_bootstrap_secondary_errors__', ())
            self.assertEqual(len(notes), len(secondary_chain), 'unexpected or missing ordered secondary error')
            for note, (phase, error) in zip(notes, secondary_chain):
                self.assertEqual(note['phase'], phase)
                self.assertIs(note['exception'], error)
            return actual
        self.fail('the real fixed fixture primary exception was not raised')

    def test_stat_exact_52_fields_signed_values_and_one_task(self):
        f = self.fresh()
        result = f.module._native_launch_stat(stat_bytes())
        self.assertEqual(result, {'pid': 42, 'ppid': 41, 'pgrp': 40, 'session': 39, 'starttime': 999, 'one_task': True})
        cases = {
            '49-tail': stat_bytes().rsplit(b' ', 1)[0] + b'\n', '51-tail': stat_bytes()[:-1] + b' 0\n',
            'delimiter': b'42 python3.12) R 0\n', 'comm': stat_bytes(comm='other'),
            'state': stat_bytes(state='T'), 'NUL': stat_bytes() + b'\0', 'no-LF': stat_bytes()[:-1],
            'unsigned-negative': stat_bytes(fields={9: -1}), 'signed-overflow': stat_bytes(fields={8: 1 << 63}),
            'unsigned-overflow': stat_bytes(fields={9: 1 << 64}), 'zero-pid': stat_bytes(pid=0),
            'zero-start': stat_bytes(fields={22: 0}), 'two-tasks': stat_bytes(fields={20: 2}),
        }
        for label, raw in cases.items():
            with self.subTest(case=label): self.reject(lambda: f.module._native_launch_stat(raw), 'native proc stat')

    def test_complete_kernel_argv_and_original_sys_arrays(self):
        f = self.fresh(); owner = f.owner(); expected = argv()
        self.assertEqual(f.module._native_launch_cmdline(command_bytes(expected), owner), expected)
        cases = {'missing-NUL': command_bytes(expected)[:-1], 'empty-extra': command_bytes(expected) + b'\0'}
        for label, index, value in [('executable', 0, '/usr/bin/python3'), ('entry', 4, '/tmp/other.py'),
                ('revision', 8, 'b' * 40), ('operation', 20, 'other'), ('c', 1, '-c'), ('m', 1, '-m'),
                ('O', 1, '-O'), ('X', 1, '-X'), ('order', 1, '-S')]:
            values = list(expected); values[index] = value; cases[label] = command_bytes(values)
        cases['extra-argument'] = command_bytes([*expected, 'extra'])
        for label, raw in cases.items():
            with self.subTest(case=label): self.reject(lambda: f.module._native_launch_cmdline(raw, owner), 'native kernel argv')
        for name in ('orig_argv', 'argv'):
            with self.subTest(case=name):
                old = getattr(f.sys, name); setattr(f.sys, name, ['foreign'])
                self.reject(lambda: f.module._native_launch_cmdline(command_bytes(expected), owner), 'native kernel argv')
                setattr(f.sys, name, old)

    def test_maps_fixed_categories_bounds_and_executable_refusal(self):
        f = self.fresh()
        rows = [map_row(), map_row('[heap]', start=0x3000), map_row('[stack]', start=0x5000),
                map_row('[vvar]', start=0x7000, permissions='r--p'), map_row('[vvar_vclock]', start=0x9000, permissions='r--p'),
                map_row('[vdso]', start=0xb000, permissions='r-xp'),
                map_row('/qualified/file with space', start=0xd000, permissions='r-xp', inode=17, dev=(8, 1)),
                map_row('[vsyscall]', start=0xffffffffff600000, permissions='--xp')]
        self.assertEqual(len(f.module._native_launch_maps(map_bytes(rows))), len(rows))
        cases = {'overlap': [map_row(), map_row(start=0x1001)], 'unsorted': [map_row(start=0x3000), map_row()],
                 'WX': [map_row(permissions='rwxp')], 'anonymous-X': [map_row(permissions='r-xp')],
                 'shared': [map_row(permissions='rw-s')], 'unknown': [map_row('[foreign]')],
                 'deleted': [map_row('/x (deleted)', inode=17, dev=(8, 1))],
                 'memfd': [map_row('/memfd:foreign', inode=17, dev=(8, 1))],
                 'device': [map_row('/dev/foreign', inode=17, dev=(8, 1))],
                 'zero': [map_row(size=0)], 'overflow': [map_row(start=1 << 64)],
                 'kernel-metadata': [map_row('[vdso]', permissions='r-xp', inode=1)]}
        for label, values in cases.items():
            with self.subTest(case=label): self.reject(lambda: f.module._native_launch_maps(map_bytes(values)), 'native ')
        for label, raw in {'permissions': b'1-2 z--p 0 00:00 0\n', 'dev': b'1-2 r--p 0 zz:00 1 /a\n',
                'inode': b'1-2 r--p 0 00:00 -1 /a\n', 'offset': b'1-2 r--p 10000000000000000 00:00 1 /a\n',
                'NUL': b'1-2 rw-p 0 00:00 0\0\n', 'no-LF': b'1-2 rw-p 0 00:00 0',
                'path-bound': map_bytes([map_row('/' + 'a' * 4096, inode=1)]),
                'row-bound': b'1-2 rw-p 0 00:00 0\n' * 8193}.items():
            with self.subTest(case=label): self.reject(lambda: f.module._native_launch_maps(raw), 'native ')

    def test_elf_header_load_dynamic_and_string_ranges(self):
        f = self.fresh()
        valid = elf_bytes(needed=('libfixture.so',), soname='fixture.so', search='$ORIGIN', interpreter='/lib64/ld-linux-x86-64.so.2')
        result = f.module._native_launch_elf(valid)
        self.assertEqual(result['needed'], ['libfixture.so']); self.assertEqual(result['soname'], 'fixture.so')
        self.assertIsNone(f.module._native_launch_elf(elf_bytes())['interpreter'])
        mutations = {'short': valid[:63]}
        for label, at, raw in [('class', 4, b'\x01'), ('endian', 5, b'\x02'), ('machine', 18, b'\x03\0'),
                ('type', 16, b'\x01\0'), ('header', 52, b'\x3f\0'), ('phsize', 54, b'\x01\0'),
                ('phcount', 56, b'\x01\x04'), ('phoff', 32, b'\xff' * 8),
                ('load-range', 64 + 32, b'\xff' * 8), ('missing-null', 0x200 + 5 * 16, struct.pack('<QQ', 7, 1)),
                ('unbacked-strings', 0x208, struct.pack('<Q', 1 << 63)),
                ('string-index', 0x200 + 2 * 16 + 8, struct.pack('<Q', 1 << 63))]:
            changed = bytearray(valid); changed[at:at + len(raw)] = raw; mutations[label] = bytes(changed)
        mutations.update({'needed-path': elf_bytes(needed=('../bad',)), 'soname-bound': elf_bytes(soname='x' * 129)})
        excessive = bytearray(valid); excessive.extend(b'\0' * (0x200 + 65552 - len(excessive)))
        struct.pack_into('<QQ', excessive, 64 + 32, len(excessive), len(excessive) + 4096)
        struct.pack_into('<QQ', excessive, 120 + 32, 65552, 65552)
        mutations['dynamic-limit'] = bytes(excessive)
        # Duplicate segment and dynamic-string metadata use inert table bytes.
        duplicate = bytearray(valid); struct.pack_into('<I', duplicate, 176, 2); mutations['duplicate-dynamic'] = bytes(duplicate)
        duplicate_interpreter = bytearray(valid); duplicate_interpreter[120:176] = valid[176:232]
        mutations['duplicate-interpreter'] = bytes(duplicate_interpreter)
        duplicate_tag = bytearray(valid); struct.pack_into('<QQ', duplicate_tag, 0x200 + 2 * 16, 5, 0x300); mutations['duplicate-string-tag'] = bytes(duplicate_tag)
        oversized_strings = bytearray(valid); struct.pack_into('<Q', oversized_strings, 0x200 + 16 + 8, 16777217)
        mutations['string-table-bound'] = bytes(oversized_strings)
        unterminated = bytearray(valid); unterminated[0x300:] = b'x' * (len(valid) - 0x300); mutations['unterminated-string'] = bytes(unterminated)
        for label, raw in mutations.items():
            with self.subTest(case=label): self.reject(lambda: f.module._native_launch_elf(raw), 'native ')

    def test_fixed_reader_roles_host_leaf_and_every_physical_ancestor(self):
        roles = {'os-release': '/usr/lib/os-release', 'dpkg-status': '/var/lib/dpkg/status', 'nss': '/etc/nsswitch.conf',
                 'openssl': '/etc/ssl/openssl.cnf', 'ca-file': '/etc/ssl/certs/ca-certificates.crt', 'ld-preload': '/etc/ld.so.preload',
                 ('host-elf', '/usr/lib/x86_64-linux-gnu/fixture.so'): '/usr/lib/x86_64-linux-gnu/fixture.so',
                 ('package-list', 'fixture:amd64.list'): '/var/lib/dpkg/info/fixture:amd64.list',
                 ('package-md5', 'fixture:amd64.md5sums'): '/var/lib/dpkg/info/fixture:amd64.md5sums',
                 ('locale-data', 'C.utf8/LC_CTYPE'): '/usr/lib/locale/C.utf8/LC_CTYPE'}
        for role, path in roles.items():
            for label in ('valid', 'leaf-uid', 'leaf-write', 'leaf-link', 'ancestor-uid', 'ancestor-write', 'ancestor-link', 'ancestor-nondirectory'):
                with self.subTest(role=role, case=label):
                    f = self.fresh(); f.osdata.put(path, b'abc')
                    if label == 'leaf-uid': f.osdata.stats[path].st_uid = 1001
                    if label == 'leaf-write': f.osdata.stats[path].st_mode |= 0o020
                    if label == 'leaf-link': f.osdata.stats[path].st_mode = stat.S_IFLNK | 0o777
                    parent = str(PurePosixPath(path).parent)
                    if label.startswith('ancestor'):
                        row = info(mode=stat.S_IFDIR | 0o755, size=0)
                        if label.endswith('uid'): row.st_uid = 1001
                        if label.endswith('write'): row.st_mode |= 0o002
                        if label.endswith('link'): row.st_mode = stat.S_IFLNK | 0o777
                        if label.endswith('nondirectory'): row.st_mode = stat.S_IFREG | 0o644
                        f.osdata.stats[parent] = row
                    if label == 'valid': self.assertEqual(f.module._native_launch_read(role, f.owner())['raw'], b'abc')
                    else: self.reject(lambda: f.module._native_launch_read(role, f.owner()), 'native |source parent')
        for role, path in [('boot-id', '/proc/sys/kernel/random/boot_id'), ('proc-stat', '/proc/42/stat'),
                ('capsule', TEMP + '/som-current-inputs/runtime/capsule.json'),
                ('profile', str(REPO / BASE / 'native_first_launch_profile_v1.json')),
                (('installed', 'install/fixture'), RUNTIME + '/install/fixture')]:
            with self.subTest(case='separate', role=role):
                f = self.fresh(); f.osdata.put(path, b'abc', uid=1001, mode=stat.S_IFREG | 0o666)
                self.assertEqual(f.module._native_launch_read(role, f.owner())['raw'], b'abc')
        f = self.fresh(); self.assertEqual(f.module._native_launch_read(('repository', BASE + 'product_runtime/source-pins.json'), f.owner())['raw'], (REPO / BASE / 'product_runtime/source-pins.json').read_bytes())
        for role in (('host-elf', '/tmp/other.so'), ('locale-data', '../x'), ('installed', '../x'), ('unknown', 'x')):
            with self.subTest(case='unlisted', role=role): self.reject(lambda: f.module._native_launch_read(role, f.owner()), 'native |bootstrap source path')
        f = self.fresh(); f.osdata.put('/proc/42/exe', b'abc'); f.module._native_launch_read('proc-exe', f.owner())
        flags = next(row[2] for row in f.osdata.events if row[0] == 'open')
        self.assertFalse(flags & os.O_NOFOLLOW)

    def test_reader_stable_bounds_primary_traceback_and_once_close(self):
        path = RUNTIME + '/install/fixture'; role = ('installed', 'install/fixture')
        for operation in ('open', 'read', 'fstat', 'close', 'read-and-close'):
            with self.subTest(case=operation):
                f = self.fresh(); f.osdata.put(path, b'abc'); first = RuntimeError('fixed reader primary'); second = RuntimeError('fixed close secondary')
                if operation == 'open': f.osdata.open_error = first
                if operation.startswith('read'): f.osdata.read_error = first
                if operation == 'fstat': f.osdata.fstat_error = first
                if operation == 'close': f.osdata.close_errors[17] = first
                if operation == 'read-and-close': f.osdata.close_errors[17] = second
                chain = (('native-launch-read-close', second),) if operation == 'read-and-close' else ()
                self.assert_primary(lambda: f.module._native_launch_read(role, f.owner()), first, f.module._native_launch_read.__code__, secondary_chain=chain)
                self.assertEqual(sum(row == ('close', 17) for row in f.osdata.events), 0 if operation == 'open' else 1)
                if operation == 'read-and-close': self.assertIs(first.__som_bootstrap_secondary_errors__[0]['exception'], second)
        for label, key, value in [('inode', 'st_ino', 88), ('mode', 'st_mode', stat.S_IFREG | 0o600), ('path', 'st_nlink', 2)]:
            with self.subTest(case=label):
                f = self.fresh(); f.osdata.put(path, b'abc'); changed = copy.copy(f.osdata.stats[path]); setattr(changed, key, value)
                f.osdata.fstat_rows = [f.osdata.stats[path], changed]
                self.reject(lambda: f.module._native_launch_read(role, f.owner()), 'native launch data changed')
        f = self.fresh(); f.osdata.put(path, b'abc')
        f.osdata.fstat_rows = [info(mode=stat.S_IFDIR | 0o755, ino=17, size=3)]
        error = self.reject(lambda: f.module._native_launch_read(role, f.owner()), '^native launch opened data changed type$')
        self.assertIn(f.module._native_launch_read.__code__, [node.tb_frame.f_code for node in _traceback_rows(error.__traceback__)])
        self.assertEqual([row for row in f.osdata.events if row[0] == 'close'], [('close', 17)])
        self.assertEqual(sum(row[0] == 'open' for row in f.osdata.events), 1)
        self.assertFalse(any(row[0] == 'read' for row in f.osdata.events))
        f = self.fresh(); f.osdata.put(path, b'abc'); f.osdata.read_chunks = [b'a', b'bc', b'']
        self.assertEqual(f.module._native_launch_read(role, f.owner())['raw'], b'abc')
        f = self.fresh(); f.osdata.put(path, b'abc'); original_lstat = f.os.lstat
        changed_path = copy.copy(f.osdata.stats[path]); changed_path.st_ino = 999
        f.os.lstat = lambda value: changed_path if str(value) == path and any(row[0] == 'read' for row in f.osdata.events) else original_lstat(value)
        self.reject(lambda: f.module._native_launch_read(role, f.owner()), 'size or path changed')
        f = self.fresh(); f.osdata.put('/usr/lib/os-release', b'abc'); f.osdata.read_chunks = [b'x' * 65537]
        self.reject(lambda: f.module._native_launch_read('os-release', f.owner()), 'exceeds limit')
        f = self.fresh(); archive = TEMP + '/som-current-inputs/runtime/runtime.tar'; f.osdata.put(archive, b'archive')
        row = f.module._native_launch_read('archive', f.owner()); self.assertNotIn('raw', row)
        f.osdata.stats[archive].st_size = 2167963649
        self.reject(lambda: f.module._native_launch_read('archive', f.owner()), 'exceeds limit')
        with self.subTest(case='lost-original-traceback-oracle'):
            original = RuntimeError('fixed oracle counterexample')
            try:
                _raise_fixed_fault(original)
            except RuntimeError as caught:
                self.assertIs(caught, original)
            saved = original.__fixture_first_throw_nodes__
            self.assertTrue(saved)
            def lost_original():
                # Only a fixture oracle counterexample erases its own error TB.
                # No subject function, frame or native-origin claim is involved.
                raise original.with_traceback(None)
            with self.assertRaisesRegex(AssertionError, 'original first-throw traceback node was lost'):
                self.assert_primary(lost_original, original, lost_original.__code__)

    def test_private_environment_exact_names_values_and_no_secret_output(self):
        def prepare():
            f = self.fresh(); owner = f.owner()
            raw = b'\0'.join(os.fsencode(key + '=' + value) for key, value in f.os.environ.items()) + b'\0'
            f.osdata.put('/proc/42/environ', raw)
            return f, owner, raw
        f, owner, raw = prepare(); result = f.module._native_launch_environment(owner)
        self.assertNotIn('fixed-synthetic-noncredential', json.dumps(result)); self.assertTrue(result['extra_names_absent'])
        self.assertEqual(result['all_nonsecret_values']['HOME'], '/home/runner'); self.assertEqual(result['private_names_only'], ['SOM_DEPENDENCY_ACTIONS_TOKEN'])
        for label in ('duplicate', 'missing', 'extra-LD', 'extra-python', 'bad-name', 'missing-NUL', 'bound', 'private-empty', 'private-CRLF', 'public-changed', 'private-changed', 'HOME-relative', 'TMP-mode', 'TMP-notempty'):
            with self.subTest(case=label):
                f, owner, raw = prepare()
                if label == 'duplicate': raw += b'PATH=/usr/bin:/bin\0'
                elif label == 'missing': raw = raw.replace(b'PATH=/usr/bin:/bin\0', b'')
                elif label == 'extra-LD': raw += b'LD_PRELOAD=fixture-only-never-loaded\0'
                elif label == 'extra-python': raw += b'PYTHONPATH=fixture-only\0'
                elif label == 'bad-name': raw += b'BAD-NAME=x\0'
                elif label == 'missing-NUL': raw = raw[:-1]
                elif label == 'bound': raw = b'x' * 65537
                elif label == 'private-empty': f.os.environ['SOM_DEPENDENCY_ACTIONS_TOKEN'] = ''
                elif label == 'private-CRLF': f.os.environ['SOM_DEPENDENCY_ACTIONS_TOKEN'] = 'fixed\r\n'
                elif label == 'public-changed': f.os.environ['PATH'] = '/foreign'
                elif label == 'private-changed': f.os.environ['SOM_DEPENDENCY_ACTIONS_TOKEN'] = 'different-fixed-synthetic'
                elif label == 'HOME-relative': f.os.environ['HOME'] = 'relative'
                elif label == 'TMP-mode': f.osdata.stats[TASK_TMP] = info(mode=stat.S_IFDIR | 0o755)
                elif label == 'TMP-notempty': f.osdata.listdir_rows[TASK_TMP] = ['existing']
                f.osdata.put('/proc/42/environ', raw)
                error = self.reject(lambda: f.module._native_launch_environment(owner), 'native ')
                self.assertNotIn('fixed-synthetic-noncredential', str(error))

    def test_private_environment_cleanup_keeps_first_error(self):
        for operation in ('open', 'read', 'parser', 'read-and-close', 'parser-and-close', 'success-close'):
            with self.subTest(case=operation):
                f = self.fresh(); owner = f.owner(); first = RuntimeError('fixed environment primary'); second = RuntimeError('fixed close secondary')
                raw = b'\0'.join(os.fsencode(key + '=' + value) for key, value in f.os.environ.items()) + b'\0'
                f.osdata.put('/proc/42/environ', b'broken' if operation.startswith('parser') else raw)
                if operation == 'open': f.osdata.open_error = first
                if operation.startswith('read'): f.osdata.read_error = first
                if operation.endswith('close'): f.osdata.close_errors[17] = first if operation == 'success-close' else second
                if operation.startswith('parser'):
                    error = self.reject(lambda: f.module._native_launch_environment(owner), 'native initial environment')
                else:
                    chain = (('native-launch-private-environment-close', second),) if operation == 'read-and-close' else ()
                    error = self.assert_primary(lambda: f.module._native_launch_environment(owner), first, f.module._native_launch_environment.__code__, secondary_chain=chain)
                self.assertEqual(sum(row == ('close', 17) for row in f.osdata.events), 0 if operation == 'open' else 1)
                if operation in ('read-and-close', 'parser-and-close'): self.assertIs(error.__som_bootstrap_secondary_errors__[0]['exception'], second)
                tb = error.__traceback__
                while tb:
                    if tb.tb_frame.f_code is f.module._native_launch_environment.__code__:
                        values = tb.tb_frame.f_locals
                        self.assertIsNone(values['current']); self.assertIsNone(values['value'])
                        self.assertEqual(values['raw'], bytearray()); self.assertEqual(values['pieces'], [])
                    tb = tb.tb_next

    def test_runtime_source_capsule_restore_origin_and_archive_join(self):
        f = self.fresh(); graph = FileGraph(f); graph.install()
        result = f.module._native_launch_files(f.owner())
        self.assertEqual(result['runtime']['installed_regular_count'], 10102)
        self.assertEqual(result['runtime']['alias_count'], 11)
        for name, field in (('.github/workflows/rust-hosted-readiness.yml', 'workflow_sha256'),
                ('.github/workflows/rust-same-candidate-products.yml', 'called_workflow_sha256')):
            self.assertEqual(f.snapshot.calls.count(('read', name)), 1)
            self.assertEqual(result['source'][field], digest(f.snapshot.workflows[name]))
        for label in ('profile', 'audit', 'source-pins', 'policy-python', 'policy-ELF', 'python-source',
                'run', 'attempt', 'helper', 'candidate', 'source-map', 'artifact-id', 'artifact-name',
                'artifact-digest', 'artifact-head', 'artifact-expired', 'archive-size', 'archive-SHA',
                'restore-manifest', 'restore-origin', 'original', 'reuse'):
            with self.subTest(case=label):
                f = self.fresh(); graph = FileGraph(f); graph.install()
                if label == 'profile':
                    original = graph.read
                    f.module._native_launch_read = lambda role, owner: data_row(b'{}') if role == 'profile' else original(role, owner)
                elif label in ('audit', 'source-pins'):
                    name = 'runtime-bf3-byte-audit.json' if label == 'audit' else 'source-pins.json'
                    graph.repo[BASE + 'product_runtime/' + name] += b' '
                elif label.startswith('policy'):
                    name = BASE + 'product_python/wheel-policy.json'; value = json.loads(graph.repo[name])
                    if label == 'policy-python': value['python'] = '3.12.3'
                    else: value['native_sha256']['python'] = '0' * 64
                    graph.repo[name] = json_bytes(value)
                elif label == 'python-source':
                    # The profile reader is a data boundary; the exact source pin stays unmodified.
                    changed = json.loads(f.profile_raw); changed['release_archive_sha256'] = '0' * 64
                    original = graph.read
                    f.module._native_launch_read = lambda role, owner: data_row(json_bytes(changed), sha=PROFILE_SHA) if role == 'profile' else original(role, owner)
                    original_snapshot_read = f.snapshot.read
                    f.stack.enter_context(mock.patch.object(f.snapshot, 'read',
                        lambda path: json_bytes(changed) if path == f.module._NATIVE_LAUNCH_PROFILE else original_snapshot_read(path)))
                elif label == 'run': graph.capsule['github']['run_id'] = 999
                elif label == 'attempt': graph.capsule['github']['run_attempt'] = 999
                elif label == 'helper': graph.capsule['helper_revision'] = 'b' * 40
                elif label == 'candidate': graph.capsule['candidate_revision'] = 'b' * 40
                elif label == 'source-map': graph.capsule['source_sha256'] = {}
                elif label == 'artifact-id': graph.origin['id'] = 999
                elif label == 'artifact-name': graph.origin['name'] = 'other'
                elif label == 'artifact-digest': graph.restore['artifact_digest'] = '0' * 64
                elif label == 'artifact-head': graph.origin['workflow_run']['head_sha'] = 'b' * 40
                elif label == 'artifact-expired': graph.origin['expired'] = True
                elif label == 'archive-size': graph.archive['size'] += 1
                elif label == 'archive-SHA': graph.archive['sha256'] = '0' * 64
                elif label == 'restore-manifest': graph.restore['manifest_sha256'] = '0' * 64
                elif label == 'restore-origin': graph.restore['artifact_origin_sha256'] = '0' * 64
                elif label == 'original': graph.original['source_revision'] = 'b' * 40
                elif label == 'reuse': graph.reuse['tested_sha'] = 'b' * 40
                if label in ('artifact-id', 'artifact-name', 'artifact-head', 'artifact-expired'):
                    graph.restore['artifact_origin_sha256'] = digest(json_bytes(graph.origin))
                self.reject(lambda: f.module._native_launch_files(f.owner()), 'native ')

    def test_complete_inventory_modes_aliases_and_all_cache_bytes(self):
        f = self.fresh(); graph = FileGraph(f); graph.install()
        result = f.module._native_launch_files(f.owner())
        self.assertEqual(len(result['installed_fingerprints']), 10102)
        for name, field in (('.github/workflows/rust-hosted-readiness.yml', 'workflow_sha256'),
                ('.github/workflows/rust-same-candidate-products.yml', 'called_workflow_sha256')):
            self.assertEqual(f.snapshot.calls.count(('read', name)), 1)
            self.assertEqual(result['source'][field], digest(f.snapshot.workflows[name]))
        for label in ('extra-file', 'missing-file', 'extra-directory', 'missing-directory', 'directory-link',
                'regular-mode', 'regular-links', 'regular-size', 'regular-bytes',
                'alias-target', 'alias-mode', 'alias-hash', 'alias-row', 'alias-flags',
                'normal-cache-byte', 'normal-cache-mode', 'opt1-cache-byte', 'opt1-cache-mode', 'opt2-cache-byte', 'opt2-cache-mode'):
            with self.subTest(case=label):
                f = self.fresh(); graph = FileGraph(f); graph.install()
                regular = next(iter(graph.installed)); alias = next(iter(graph.q['aliases']))
                if label == 'extra-file': graph.walk_rows[0][2].append('unowned')
                elif label == 'missing-file':
                    next(row[2] for row in graph.walk_rows if row[2]).pop()
                elif label == 'extra-directory': graph.walk_rows[0][1].append('unowned')
                elif label == 'missing-directory':
                    next(row[1] for row in graph.walk_rows if row[1]).pop()
                elif label == 'directory-link':
                    directory = next(name for name in graph.directory_names if name != 'install')
                    f.osdata.stats[RUNTIME + '/' + directory] = info(mode=stat.S_IFLNK | 0o777)
                elif label.startswith('regular'):
                    row = graph.installed[regular]
                    if label == 'regular-mode': row['identity'] = (*row['identity'][:2], stat.S_IFREG | 0o600, *row['identity'][3:])
                    if label == 'regular-links': row['identity'] = (*row['identity'][:3], 2, *row['identity'][4:])
                    if label == 'regular-size': row['size'] += 1
                    if label == 'regular-bytes': row['sha256'] = digest(b'changed')
                elif label == 'alias-target': f.osdata.links[RUNTIME + '/' + alias] = 'other'
                elif label == 'alias-mode': graph.transport[alias]['mode'] = 0o755
                elif label == 'alias-hash': graph.inventory[alias]['sha256'] = '0' * 64
                elif label == 'alias-row': graph.inventory[alias]['extra'] = True
                elif label == 'alias-flags': graph.inventory[alias]['runtime_admitted'] = True
                else:
                    cache = graph.caches[0 if label.startswith('normal') else 1 if label.startswith('opt1') else 2]
                    row = graph.installed[cache]
                    if label.endswith('byte'):
                        row['raw'] += b'x'; row['size'] += 1; row['sha256'] = digest(row['raw'])
                    else: row['identity'] = (*row['identity'][:2], stat.S_IFREG | 0o600, *row['identity'][3:])
                if label == 'alias-mode': graph.restore['manifest_sha256'] = digest(json_bytes(graph.capsule))
                self.reject(lambda: f.module._native_launch_files(f.owner()), 'native ')

    def test_module_native_namespaces_and_only_pinned_aliases(self):
        def frozen_graph():
            f = self.fresh(); graph = ModuleGraph(f)
            # This is the fixture runner's own stdlib data, never target-origin authority.
            stdlib = Path(sys._stdlib_dir)
            for original, canonical, leaf in (('_frozen_importlib', 'importlib._bootstrap', '_bootstrap.py'),
                    ('_frozen_importlib_external', 'importlib._bootstrap_external', '_bootstrap_external.py')):
                raw = (stdlib / 'importlib' / leaf).read_bytes()
                graph.add_source(canonical, raw, frozen_original=original)
            return f, graph
        f, graph = frozen_graph(); result = graph.run()
        self.assertEqual(len([row for row in result['code'] if row['kind'] == 'frozen']), 4)
        for label in ('missing-original', 'missing-canonical', 'distinct-pointer', 'wrong-spec', 'wrong-frozen-name'):
            with self.subTest(case=label):
                f, graph = frozen_graph()
                if label == 'missing-original': del f.sys.modules['_frozen_importlib']
                if label == 'missing-canonical': del f.sys.modules['importlib._bootstrap']
                if label == 'distinct-pointer': f.sys.modules['importlib._bootstrap'] = ModuleType('foreign')
                if label == 'wrong-spec': f.sys.modules['_frozen_importlib'].__spec__.name = 'foreign'
                if label == 'wrong-frozen-name': graph.profile['frozen_names'].remove('_frozen_importlib')
                self.reject(graph.run, 'native |frozen')
        for label in ('valid', 'foreign-key', 'wrong-export', 'wrong-metaclass', 'module-key', 'spec-key', 'arbitrary-alias'):
            with self.subTest(case=label):
                f = self.fresh(); graph = ModuleGraph(f); typing_module, relative = graph.add_source('typing', b'pass\n')
                deprecated = type('_DeprecatedType', (type,), {})
                hooks = []
                class ForeignKey:
                    armed = False
                    def __hash__(self):
                        if self.armed: hooks.append('hash')
                        return 1
                    def __eq__(self, other):
                        if self.armed: hooks.append('eq')
                        return self is other
                key = ForeignKey(); namespace = {'__module__': 'typing'}
                if label == 'foreign-key': namespace[key] = 'inert'
                alias = deprecated('io', (), namespace)
                typing_module._DeprecatedType, typing_module.io = deprecated, alias
                f.sys.modules['typing.io'] = alias
                key.armed = True; hooks.clear()
                if label == 'wrong-export': typing_module.io = object()
                if label == 'wrong-metaclass': typing_module._DeprecatedType = type
                if label == 'module-key': vars(typing_module)[42] = None
                if label == 'spec-key': vars(typing_module.__spec__)[42] = None
                if label == 'arbitrary-alias':
                    # A same-pointer module without a pinned alias is still unlisted.
                    f.sys.modules['unlisted.alias'] = typing_module
                if label == 'valid': self.assertEqual(graph.run()['aliases'], [{'name': 'typing.io', 'kind': 'qualified-typing-class-alias'}])
                else: self.reject(graph.run, 'native |typing')
                self.assertEqual(hooks, [])

    def test_source_frozen_normal_cache_association_and_complete_code_tree(self):
        for flags, header in ((0, True), (0, False), (1, True), (1, False), (3, True), (3, False)):
            with self.subTest(case='header', flags=flags, valid=header):
                f = self.fresh(); graph = ModuleGraph(f); graph.add_source(); graph.add_cache('fixture_source', flags=flags, header_valid=header)
                row = graph.run()['code'][0]; self.assertTrue(row['whole_code_tree_equal'])
                self.assertIn('normal-cache-', row['cache_status'])
        for label in ('loader', 'spec', 'source', 'cache-association', 'magic', 'flags2', 'flags4', 'short-header',
                'code-filename', 'code-constant-type', 'nested-code', 'line-table', 'foreign-cache'):
            with self.subTest(case=label):
                f = self.fresh(); graph = ModuleGraph(f); module, source = graph.add_source(); cache = graph.add_cache('fixture_source')
                if label == 'loader': module.__loader__ = object()
                elif label == 'spec': module.__spec__.name = 'foreign'
                elif label == 'source': graph.rows[source]['sha256'] = '0' * 64
                elif label == 'cache-association': module.__cached__ = '/foreign/cache.pyc'
                elif label in ('magic', 'flags2', 'flags4', 'short-header'):
                    raw = graph.rows[cache]['raw']
                    if label == 'magic': raw = b'xxxx' + raw[4:]
                    elif label.startswith('flags'): raw = raw[:4] + (2 if label == 'flags2' else 4).to_bytes(4, 'little') + raw[8:]
                    else: raw = raw[:15]
                    graph.rows[cache] = data_row(raw); graph.installed[cache]['sha256'] = digest(raw)
                elif label == 'foreign-cache':
                    del graph.installed[cache]; f.osdata.data[module.__cached__] = b'foreign'
                else:
                    expected = compile(graph.rows[source]['raw'], module.__file__, 'exec', dont_inherit=True, optimize=0)
                    if label == 'code-filename': changed = expected.replace(co_filename='/foreign.py')
                    elif label == 'line-table': changed = expected.replace(co_linetable=expected.co_linetable + b'\0')
                    else:
                        nested = next(value for value in expected.co_consts if isinstance(value, type(expected)))
                        changed_nested = nested.replace(co_consts=tuple(True if type(value) is int and value == 1 else value for value in nested.co_consts))
                        if label == 'nested-code': changed_nested = nested.replace(co_name='foreign')
                        changed = expected.replace(co_consts=tuple(changed_nested if value is nested else value for value in expected.co_consts))
                    graph.add_cache('fixture_source', code=changed)
                self.reject(graph.run, 'native ')
        # Frozen filename and full code comparison are also exercised by F11's two live runner-frozen pairs.
        self.assertEqual(assembly_bytes()[0], self.fresh().raw)

    def test_native_module_file_graph_instrumentation_and_no_wheel_startup(self):
        f = self.fresh(); graph = ModuleGraph(f); module, relative = graph.add_extension()
        result = graph.run(); self.assertEqual(result['native_files'][module.__file__]['module'], '_fixture_native')
        for label in ('native-name', 'builtin-loader', 'extension-loader', 'extension-path', 'extension-file',
                'site-package', 'wheel', 'repository', 'trace', 'profile', 'monitoring', 'module-bound'):
            with self.subTest(case=label):
                f = self.fresh(); graph = ModuleGraph(f); module, relative = graph.add_extension()
                if label == 'native-name': graph.profile['native_module_names'].remove('_fixture_native')
                elif label == 'builtin-loader':
                    other = ModuleType('posix'); loader = object(); other.__loader__ = loader
                    other.__spec__ = importlib.machinery.ModuleSpec('posix', loader, origin='built-in')
                    f.sys.modules['posix'] = other; graph.profile['native_module_names'].append('posix')
                elif label == 'extension-loader': module.__loader__ = object()
                elif label == 'extension-path': module.__file__ = '/foreign/native.so'
                elif label == 'extension-file': graph.rows[relative]['sha256'] = '0' * 64
                elif label in ('site-package', 'wheel', 'repository'):
                    name = 'foreign_' + label
                    path = {'site-package': '/usr/lib/python3.12/site-packages/foreign.py',
                        'wheel': '/synthetic/wheel/foreign.py', 'repository': str(REPO / BASE / 'foreign_fixture.py')}[label]
                    foreign = ModuleType(name); loader = importlib.machinery.SourceFileLoader(name, path)
                    foreign.__loader__, foreign.__file__ = loader, path
                    foreign.__spec__ = importlib.machinery.ModuleSpec(name, loader, origin=path)
                    f.sys.modules[name] = foreign
                elif label == 'trace': f.sys.gettrace = lambda: object()
                elif label == 'profile': f.sys.getprofile = lambda: object()
                elif label == 'monitoring': f.sys.monitoring.get_tool = lambda number: 'fixed-tool' if number == 0 else None
                elif label == 'module-bound': f.sys.modules.update({f'foreign{index}': None for index in range(513)})
                self.reject(graph.run, '^unlisted native startup module$' if label in ('site-package', 'wheel', 'repository') else 'native ')
        f = self.fresh(); graph = ModuleGraph(f); graph.add_extension(); host = HostGraph(f, extension=True)
        host.maps = [row for row in host.maps if row['path'] not in host.native]
        f.read_rows['proc-exe'] = copy.deepcopy(host.paths[PYTHON]); f.read_rows['proc-maps'] = data_row(map_bytes(host.maps))
        files = {'profile': graph.profile, 'inventory': graph.installed, 'source': {}, 'runtime': {}}
        self.reject(lambda: f.drive(active=('_native_launch_modules', '_native_launch_host_closure'),
            changes={'_native_launch_files': lambda owner: files, '_native_launch_read': host.read}),
            'qualified native ELF has no actual file mapping')

    def test_host_dependency_config_package_locale_and_loader_graph(self):
        f = self.fresh(); host = HostGraph(f, extension=True)
        listing = host.rows[('package-list', 'libfixture:amd64.list')]
        checksums = host.rows[('package-md5', 'libfixture:amd64.md5sums')]
        for index, name in enumerate(('libnss_files.so.2', 'libnss_dns.so.2')):
            path = '/usr/lib/x86_64-linux-gnu/' + name; raw = elf_bytes(soname=name, needed=('libfixture.so',))
            host.rows[('host-elf', path)] = data_row(raw, path=path, mode=0o755, ino=90 + index)
            host.maps.append(map_row(path, start=0x19000 + index * 0x2000, permissions='r-xp', inode=90 + index, dev=(8, 1)))
            listing['raw'] += path.encode() + b'\n'; listing['size'] = len(listing['raw'])
            checksums['raw'] += (hashlib.md5(raw, usedforsecurity=False).hexdigest() + '  ' + path.removeprefix('/') + '\n').encode()
        host.run()
        self.assertTrue(any(row[0] == 'target' for row in f.events))
        for label in ('missing-dependency', 'ambiguous-SONAME', 'extra-ELF', 'loader-alias', 'usr-merge',
                'NSS', 'preload', 'openssl-include', 'openssl-provider', 'openssl-engine', 'openssl-module',
                'package-absent', 'package-not-installed', 'MD5', 'locale-permissions'):
            with self.subTest(case=label):
                f = self.fresh(); host = HostGraph(f)
                if label == 'missing-dependency':
                    host.rows[('host-elf', host.library)]['raw'] = elf_bytes(soname='different.so')
                elif label == 'ambiguous-SONAME':
                    host.rows[('host-elf', host.loader)]['raw'] = elf_bytes(soname='libfixture.so')
                elif label == 'extra-ELF':
                    path = '/usr/lib/x86_64-linux-gnu/unexplained.so'; row = data_row(elf_bytes(soname='unexplained.so'), path=path, ino=88)
                    host.rows[('host-elf', path)] = row; host.maps.append(map_row(path, start=0x19000, permissions='r-xp', inode=88, dev=(8, 1)))
                elif label == 'loader-alias': f.osdata.links['/usr/lib64/ld-linux-x86-64.so.2'] = '/foreign/loader'
                elif label == 'usr-merge': f.osdata.links['/lib'] = 'foreign'
                elif label == 'NSS': host.rows['nss']['raw'] = b'hosts: files resolve dns\n'
                elif label == 'preload': host.rows['ld-preload']['raw'] = b'/foreign.so\n'
                elif label.startswith('openssl'):
                    host.rows['openssl']['raw'] = {'openssl-include': b'.include other\n', 'openssl-provider': b'providers = external\n',
                        'openssl-engine': b'engines = external\n', 'openssl-module': b'module = external\n'}[label]
                elif label == 'package-absent': f.osdata.listdir_rows['/var/lib/dpkg/info'] = []
                elif label == 'package-not-installed': host.rows['dpkg-status']['raw'] = b''
                elif label == 'MD5': host.rows[('package-md5', 'libfixture:amd64.md5sums')]['raw'] = b'0' * 32 + b'  usr/lib/x86_64-linux-gnu/libfixture.so\n'
                elif label == 'locale-permissions': next(row for row in host.maps if row['path'] == host.locale)['permissions'] = 'rw-p'
                self.reject(host.run, 'native ')
                self.assertFalse(any(row[0] == 'target' for row in f.events))

    def test_origin_single_token_static_suffix_and_all_tail_variable_refusals(self):
        for search in ('$ORIGIN', '$ORIGIN/../lib', '/usr/lib/x86_64-linux-gnu', '/lib/x86_64-linux-gnu'):
            with self.subTest(case='valid', search=search):
                f = self.fresh(); host = HostGraph(f)
                host.rows[('installed', 'install/python/bin/python3.12')]['raw'] = elf_bytes(
                    needed=('libfixture.so',), search=search, interpreter='/lib64/ld-linux-x86-64.so.2')
                host.run(); self.assertTrue(any(row[0] == 'target' for row in f.events))
        for search in ('$ORIGINevil', '${ORIGIN}/lib', '$ORIGIN/$UNKNOWN', '$ORIGIN/$LIB', '$ORIGIN/$ORIGIN',
                '$ORIGIN/lib$ANY', '$ORIGIN/../../../../../../outside', '', '.', '/arbitrary'):
            with self.subTest(case='invalid', search=search):
                f = self.fresh(); host = HostGraph(f)
                host.rows[('installed', 'install/python/bin/python3.12')]['raw'] = elf_bytes(
                    needed=('libfixture.so',), search=search, interpreter='/lib64/ld-linux-x86-64.so.2')
                error = self.reject(host.run, 'native ')
                if '$' in search[len('$ORIGIN'):] and search.startswith('$ORIGIN/'):
                    self.assertEqual(str(error), 'native ELF origin search suffix contains a variable')
                self.assertFalse(any(row[0] == 'target' for row in f.events))

    def test_report_completed_schema_bounded_data_and_exclusive_files(self):
        f = self.fresh(); f.drive(active=('_native_launch_report',))
        report = json.loads(f.osdata.report_files['initial-launch.json'])
        self.assertEqual(report['sequence'], SEQUENCE); self.assertEqual(report['acceptance']['SourceSeal'], False)
        self.assertEqual(len(f.markers), 1)
        for bad_stage in ('FDs_closed', 'initial_marker_flushed_before_target'):
            with self.subTest(case='premature-stage', stage=bad_stage):
                f = self.fresh(); acquisitions = [0]
                files = {'profile': {}, 'inventory': {}, 'source': {}, 'runtime': {}}
                def fixed_first_acquisition(owner):
                    # Root fbce619b selects precisely these two test-local data faults.
                    # This uses the real frame and grants no native frame/origin authority.
                    frame = sys._getframe(1)
                    self.assertIs(frame.f_code, f.actual['_native_before_node_first_launch'].__code__)
                    self.assertIs(frame.f_globals, vars(f.module))
                    acquisitions[0] += 1
                    if acquisitions[0] == 1:
                        self.assertEqual(frame.f_locals['phase'], 'runtime-source')
                        public = frame.f_locals['public']
                        self.assertIs(type(public), dict)
                        self.assertIs(type(public['sequence']), list)
                        self.assertEqual(public['sequence'], ['source_workflow_bound'])
                        public['sequence'].append(bad_stage)
                    return files
                error = self.reject(lambda: f.drive(active=('_native_launch_report',),
                    changes={'_native_launch_files': fixed_first_acquisition}),
                    '^native evidence may describe only completed pre-report stages$')
                self.assertEqual(acquisitions[0], 2)
                self.assertIn(f.actual['_native_launch_report'].__code__,
                    [tb.tb_frame.f_code for tb in _traceback_rows(error.__traceback__)])
                self.assertFalse(any(row[0] == 'target' for row in f.events))
                self.assertFalse(any('INITIAL_SOURCE_DATA_JOIN_COMPLETE' in row for row in f.markers))
        for label, bad in [('object', object()), ('float', 1.5), ('key', {42: 'inert'}),
                ('private', {'token': 'fixed-synthetic-only'}), ('raw', {'raw': b'inert'}),
                ('exception-data', {'exception_repr': 'fixed'}), ('string-bound', 'x' * 4097),
                ('array-bound', [None] * 8193), ('NUL-string', 'x\0')]:
            with self.subTest(case=label):
                f = self.fresh(); files = {'profile': {}, 'inventory': {}, 'source': {'fixed_bad_data': bad}, 'runtime': {}}
                self.reject(lambda: f.drive(active=('_native_launch_report',), changes={'_native_launch_files': lambda owner: files}), 'native evidence|private or raw')
                self.assertFalse(any(row[0] == 'target' for row in f.events))
        f = self.fresh(); huge = {str(index): 'x' * 4096 for index in range(4100)}
        files = {'profile': {}, 'inventory': {}, 'source': huge, 'runtime': {}}
        self.reject(lambda: f.drive(active=('_native_launch_report',), changes={'_native_launch_files': lambda owner: files}), 'report exceeds bound')
        for label in ('existing-report', 'existing-directory', 'directory-identity', 'report-mode', 'report-links'):
            with self.subTest(case=label):
                f = self.fresh()
                if label == 'existing-report': f.osdata.report_files['initial-launch.json'] = bytearray(b'keep original')
                if label == 'existing-directory': f.osdata.evidence_exists = True
                if label == 'directory-identity':
                    original = f.osdata.fstat
                    f.os.fstat = lambda fd: info(mode=stat.S_IFDIR | 0o700, ino=999, size=0) if fd == 51 else original(fd)
                if label == 'report-mode': f.osdata.report_stat_change = lambda row: info(mode=stat.S_IFREG | 0o644, ino=501, size=row.st_size)
                if label == 'report-links': f.osdata.report_stat_change = lambda row: info(mode=stat.S_IFREG | 0o600, ino=501, size=row.st_size, nlink=2)
                if label.startswith('existing'):
                    with self.assertRaises(FileExistsError): f.drive(active=('_native_launch_report',))
                else: self.reject(lambda: f.drive(active=('_native_launch_report',)), 'native evidence')
                self.assertFalse(any(row[0] == 'target' for row in f.events))
                if label == 'existing-report': self.assertEqual(f.osdata.report_files['initial-launch.json'], b'keep original')
        for label in ('directory-symlink-at-open', 'report-leaf-symlink'):
            with self.subTest(case=label):
                f = self.fresh()
                if label == 'directory-symlink-at-open':
                    f.osdata.directory_symlink = True
                    first = f.osdata.directory_symlink_error
                    chain = (('native-first-launch-failure-evidence', first),)
                else:
                    f.osdata.report_symlink = True
                    first = f.osdata.report_symlink_error
                    chain = ()
                directory_before = bytes(f.osdata.directory_sentinel)
                report_before = bytes(f.osdata.report_sentinel)
                error = self.assert_primary(lambda: f.drive(active=('_native_launch_report',)), first,
                    f.actual['_native_launch_report'].__code__, secondary_chain=chain)
                codes = [node.tb_frame.f_code for node in _traceback_rows(error.__traceback__)]
                self.assertIn(OSData.open.__code__, codes)
                directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                report_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
                opens = [row for row in f.osdata.events if row[0] == 'open']
                self.assertTrue(opens)
                for row in opens:
                    if row[1] == EVIDENCE:
                        self.assertEqual(row[2:], (directory_flags, None, None))
                    else:
                        self.assertIn(row[1], ('initial-launch.json', 'failure.json'))
                        self.assertEqual(row[2:], (report_flags, 0o600, 51))
                refusals = [row for row in f.osdata.events if row[0] == 'symlink-refused']
                self.assertEqual(refusals[0][1], 'directory' if label == 'directory-symlink-at-open' else 'report-excl')
                self.assertEqual(error.errno, errno.ENOTDIR if label == 'directory-symlink-at-open' else errno.EEXIST)
                self.assertFalse(f.osdata.directory_followed or f.osdata.report_followed)
                self.assertEqual(bytes(f.osdata.directory_sentinel), directory_before)
                self.assertEqual(bytes(f.osdata.report_sentinel), report_before)
                self.assertNotIn('initial-launch.json', f.osdata.report_files)
                self.assertFalse(any('INITIAL_SOURCE_DATA_JOIN_COMPLETE' in row for row in f.markers))
                self.assertFalse(any(row[0] == 'target' for row in f.events))
                closes = [row[1] for row in f.osdata.events if row[0] == 'close']
                self.assertEqual(closes, [] if label == 'directory-symlink-at-open' else [51, 52, 51])
                # Model sensitivity only: absent protection follows the fixed target.
                # These are not source/kernel/native-origin observations.
                model = OSData()
                if label == 'directory-symlink-at-open':
                    model.directory_symlink = True
                    self.assertEqual(model.open(EVIDENCE, directory_flags & ~os.O_NOFOLLOW), 51)
                    self.assertTrue(model.directory_followed); model.close(51)
                    self.assertEqual(bytes(model.directory_sentinel), directory_before)
                else:
                    model.report_symlink = True
                    with self.assertRaises(OSError) as caught:
                        model.open('initial-launch.json', report_flags & ~os.O_EXCL, 0o600, dir_fd=51)
                    self.assertEqual(caught.exception.errno, errno.ELOOP)
                    self.assertEqual(model.open('initial-launch.json', report_flags & ~(os.O_EXCL | os.O_NOFOLLOW), 0o600, dir_fd=51), 52)
                    self.assertTrue(model.report_followed); model.close(52)
                    self.assertEqual(bytes(model.report_sentinel), report_before)
        with self.subTest(case='expected-owner-class-at-second-acquisition'):
            f = self.fresh(); original_class = f.module._BootstrapLease
            files = {'profile': {}, 'inventory': {}, 'source': {}, 'runtime': {}}
            acquisitions = [0]
            def fixed_second_acquisition(owner):
                frame = sys._getframe(1)
                self.assertIs(frame.f_code, f.actual['_native_before_node_first_launch'].__code__)
                self.assertIs(frame.f_globals, vars(f.module))
                self.assertIs(type(owner), original_class)
                acquisitions[0] += 1
                if acquisitions[0] == 1:
                    self.assertEqual(frame.f_locals['phase'], 'runtime-source')
                elif acquisitions[0] == 2:
                    self.assertEqual(frame.f_locals['phase'], 'stable-readback')
                    self.assertIs(f.module._BootstrapLease, original_class)
                    f.module._BootstrapLease = FixedForeignExpectedLease
                else:
                    self.fail('unexpected owner-fault acquisition')
                return files
            try:
                error = self.reject(lambda: f.drive(active=('_native_launch_report',),
                    changes={'_native_launch_files': fixed_second_acquisition}),
                    '^native evidence needs its original source caller$')
                self.assertEqual(acquisitions[0], 2)
                self.assertIs(f.module._BootstrapLease, FixedForeignExpectedLease)
                frames = [node.tb_frame for node in _traceback_rows(error.__traceback__)
                          if node.tb_frame.f_code is f.actual['_native_launch_report'].__code__]
                self.assertEqual(len(frames), 1)
                report_frame = frames[0]; caller = report_frame.f_locals['caller']
                self.assertIs(report_frame.f_globals, vars(f.module))
                self.assertIs(caller.f_code, f.actual['_native_before_node_first_launch'].__code__)
                self.assertIs(caller.f_globals, vars(f.module))
                self.assertIs(caller.f_locals['owner'], f.last_owner)
                self.assertIs(report_frame.f_locals['owner'], f.last_owner)
                self.assertIs(type(f.last_owner), original_class)
                self.assertEqual(report_frame.f_locals['phase'], 'complete')
                self.assertNotIn('expected_directory', report_frame.f_locals)
                notes = error.__som_bootstrap_secondary_errors__
                self.assertEqual(len(notes), 1)
                self.assertEqual(notes[0]['phase'], 'native-first-launch-failure-evidence')
                self.assertIsNot(notes[0]['exception'], error)
                secondary_frames = [node.tb_frame for node in _traceback_rows(notes[0]['exception'].__traceback__)
                    if node.tb_frame.f_code is f.actual['_native_launch_report'].__code__]
                self.assertEqual(len(secondary_frames), 1)
                self.assertEqual(secondary_frames[0].f_locals['phase'], 'failure')
                self.assertFalse(any(row[0] == 'target' for row in f.events))
                self.assertEqual(f.markers, [])
            finally:
                f.module._BootstrapLease = original_class
            self.assertIs(f.module._BootstrapLease, original_class)
        with self.subTest(case='foreign-caller-only'):
            f = self.fresh()
            self.reject(lambda: f.module._native_launch_report(f.owner(), 'foreign', {}),
                '^native evidence needs its original source caller$')
        with self.subTest(case='phase-source-AST-only'):
            source_tree = ast.parse(f.raw)
            orchestrator = next(node for node in source_tree.body if isinstance(node, ast.FunctionDef)
                                and node.name == '_native_before_node_first_launch')
            calls = sorted((node for node in ast.walk(orchestrator) if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name) and node.func.id == '_native_launch_report'), key=lambda node: node.lineno)
            self.assertEqual(len(calls), 2)
            self.assertEqual([ast.literal_eval(node.args[1]) for node in calls], ['complete', 'failure'])
            for node in calls:
                self.assertEqual(len(node.args), 3); self.assertEqual(node.keywords, [])
                self.assertEqual(ast.dump(node.args[0]), ast.dump(ast.Name(id='owner', ctx=ast.Load())))
                self.assertEqual(ast.dump(node.args[2]), ast.dump(ast.Name(id='public', ctx=ast.Load())))
            report = next(node for node in source_tree.body if isinstance(node, ast.FunctionDef) and node.name == '_native_launch_report')
            expected = ast.dump(ast.parse("phase in ('complete', 'failure')", mode='eval').body)
            predicates = [node for node in ast.walk(report) if isinstance(node, ast.Compare) and ast.dump(node) == expected]
            self.assertEqual(len(predicates), 1)
            print('F16 isolated invalid-phase runtime: NOT_SUPPLIED/UNREACHABLE_UNDER_SELECTED_CALLER_INTERFACE; exact source AST only', flush=True)

    def test_report_partial_write_fsync_reverse_close_and_primary_error(self):
        f = self.fresh(); f.drive(active=('_native_launch_report',))
        operations = [row[0] for row in f.osdata.events]
        self.assertGreater(operations.count('write'), 1)
        self.assertEqual([row for row in f.osdata.events if row[0] == 'close'], [('close', 52), ('close', 51)])
        for label in ('write', 'fstat', 'fsync', 'directory-open', 'report-open', 'first-close', 'both-close'):
            with self.subTest(case=label):
                f = self.fresh(); first = RuntimeError('fixed report primary'); second = RuntimeError('fixed directory secondary')
                if label == 'write': f.osdata.write_error = first
                elif label == 'fstat': f.osdata.fstat_error = first
                elif label == 'fsync': f.osdata.fsync_error = first
                elif label == 'directory-open': f.osdata.directory_error = first
                elif label == 'report-open': f.osdata.report_open_error = first
                else: f.osdata.close_errors[52] = first
                if label in ('write', 'fsync', 'both-close'): f.osdata.close_errors[51] = second
                chain = ((('native-launch-report-close', second),) * 2 if label in ('write', 'fsync', 'both-close') else ())
                chain += (('native-first-launch-failure-evidence', first),)
                error = self.assert_primary(lambda: f.drive(active=('_native_launch_report',)), first, f.actual['_native_launch_report'].__code__, secondary_chain=chain)
                closes = [row[1] for row in f.osdata.events if row[0] == 'close']
                if label == 'directory-open': self.assertEqual(closes, [])
                elif label in ('report-open', 'fstat'): self.assertEqual(closes, [51, 51])
                else:
                    self.assertEqual(closes, [52, 51, 52, 51])
                    if label in ('write', 'fsync', 'both-close'):
                        self.assertIs(error.__som_bootstrap_secondary_errors__[0]['exception'], second)
                self.assertFalse(any(row[0] == 'target' for row in f.events))
        f = self.fresh(); f.osdata.write_zero = True
        self.reject(lambda: f.drive(active=('_native_launch_report',)), 'write made no progress')

    def test_marker_is_flushed_only_after_all_owned_report_fds_close(self):
        f = self.fresh(); f.drive(active=('_native_launch_report',))
        events = f.osdata.events; print_index = next(index for index, row in enumerate(events) if row[0] == 'print-flush')
        self.assertEqual([row for row in events[:print_index] if row[0] == 'close'], [('close', 52), ('close', 51)])
        self.assertEqual(events[print_index][2], {'flush': True})
        self.assertFalse(any(row == ('close', 1) for row in events))
        marker = json.loads(f.markers[0].split(' ', 1)[1]); raw = bytes(f.osdata.report_files['initial-launch.json'])
        self.assertEqual(marker['report_size'], len(raw)); self.assertEqual(marker['report_sha256'], digest(raw))
        self.assertEqual(set(marker), {'version', 'status', 'repository', 'run_id', 'attempt', 'job_id',
            'candidate', 'pid', 'report_relative_leaf', 'report_size', 'report_sha256', 'starttime'})
        self.assertLess(next(index for index, row in enumerate(f.events) if row[0] == 'marker'), next(index for index, row in enumerate(f.events) if row[0] == 'target'))
        for fd in (52, 51):
            with self.subTest(case='close-failure', fd=fd):
                f = self.fresh(); first = RuntimeError('fixed close failure'); f.osdata.close_errors[fd] = first
                self.assert_primary(lambda: f.drive(active=('_native_launch_report',)), first, f.actual['_native_launch_report'].__code__,
                    secondary_chain=(('native-first-launch-failure-evidence', first),))
                self.assertEqual(f.markers, []); self.assertFalse(any(row[0] == 'target' for row in f.events))
        f = self.fresh(); primary = RuntimeError('fixed stdout print or flush failure'); f.print_error = primary
        error = self.assert_primary(lambda: f.drive(active=('_native_launch_report',)), primary, f.actual['_native_launch_report'].__code__,
            secondary_chain=(('native-first-launch-failure-evidence', primary),))
        self.assertTrue(any(row['phase'] == 'native-first-launch-failure-evidence' for row in error.__som_bootstrap_secondary_errors__))
        self.assertEqual(f.markers, []); self.assertFalse(any(row[0] == 'target' for row in f.events))

    def test_orchestrator_phase_failures_keep_primary_and_never_enter_target(self):
        for phase in ('directory', 'files', 'process', 'environment', 'modules', 'host', 'stable-readback', 'report'):
            with self.subTest(case=phase):
                f = self.fresh(); first = RuntimeError('fixed first phase failure'); changes = {}
                def fail(*unused): _raise_fixed_fault(first)
                if phase == 'directory': f.osdata.mkdir = fail; f.os.mkdir = fail
                elif phase == 'process': f.read_rows['proc-stat'] = first
                elif phase == 'stable-readback':
                    count = [0]
                    def fixed_files(owner):
                        count[0] += 1
                        if count[0] == 2: _raise_fixed_fault(first)
                        return {'profile': {}, 'inventory': {}, 'source': {}, 'runtime': {}}
                    changes['_native_launch_files'] = fixed_files
                else:
                    changes[{'files': '_native_launch_files', 'environment': '_native_launch_environment',
                        'modules': '_native_launch_modules', 'host': '_native_launch_host_closure', 'report': '_native_launch_report'}[phase]] = fail
                chain = (('native-first-launch-failure-evidence', first),) if phase == 'report' else ()
                self.assert_primary(lambda: f.drive(changes=changes), first, f.actual['_native_before_node_first_launch'].__code__, secondary_chain=chain)
                self.assertFalse(any(row[0] == 'target' for row in f.events))
                failures = [row for row in f.events if row[0] == 'report' and row[1] == 'failure']
                if phase != 'directory' and phase != 'report':
                    self.assertEqual(len(failures), 1)
                    self.assertEqual(failures[0][2]['failure']['fixed_failure_code'], 'NATIVE_FIRST_LAUNCH_REJECTED')
                    self.assertNotIn('exception_message', json.dumps(failures[0][2]))
        for label in ('files-changed', 'process-changed', 'maps-changed', 'failure-report-error'):
            with self.subTest(case=label):
                f = self.fresh(); counts = {}; first = RuntimeError('fixed body primary'); second = RuntimeError('fixed failure report secondary')
                def reads(role, owner):
                    counts[role] = counts.get(role, 0) + 1
                    row = f.source_read(role, owner)
                    if label == 'process-changed' and role == 'proc-stat' and counts[role] == 2: row['raw'] = stat_bytes(fields={22: 1000})
                    if label == 'maps-changed' and role == 'proc-maps' and counts[role] == 2:
                        row['raw'] = map_bytes([map_row('/changed/executable', permissions='r-xp', inode=1, dev=(8, 1))])
                    return row
                changes = {'_native_launch_read': reads}
                if label == 'files-changed':
                    count = [0]
                    def files(owner):
                        count[0] += 1
                        return {'profile': {}, 'inventory': {}, 'source': {'count': count[0]}, 'runtime': {}}
                    changes['_native_launch_files'] = files
                if label == 'failure-report-error':
                    def body(owner): _raise_fixed_fault(first)
                    def report(owner, phase, row): _raise_fixed_fault(second)
                    changes.update(_native_launch_files=body, _native_launch_report=report)
                    error = self.assert_primary(lambda: f.drive(changes=changes), first, f.actual['_native_before_node_first_launch'].__code__,
                        secondary_chain=(('native-first-launch-failure-evidence', second),))
                    self.assertIs(error.__som_bootstrap_secondary_errors__[0]['exception'], second)
                else: self.reject(lambda: f.drive(changes=changes), 'changed')
                self.assertFalse(any(row[0] == 'target' for row in f.events))
        for label in ('foreign-caller', 'foreign-main', 'preloaded-active', 'preloaded-admission'):
            with self.subTest(case=label):
                f = self.fresh(); owner = f.owner()
                if label == 'foreign-caller':
                    self.reject(lambda: f.module._native_before_node_first_launch(owner), 'actual initial bootstrap')
                else:
                    self.reject(lambda: f.drive(owner_fault=label), 'actual initial bootstrap')
                self.assertEqual(f.osdata.events, [])

    def test_original_main_calls_once_before_target_and_other_calls_are_silent(self):
        f = self.fresh(); f.drive()
        self.assertEqual([row[0] for row in f.events if row[0] in ('report', 'target')], ['report', 'target'])
        self.assertEqual(sum(row[0] == 'target' for row in f.events), 1)
        for label, options in [('job', {'job': 'products'}), ('role', {'role': 'entry.other'}), ('operation', {'operation': 'postclose'})]:
            with self.subTest(case=label):
                f = self.fresh(); f.drive(**options)
                self.assertEqual([row[0] for row in f.events], ['target'])
                self.assertEqual(f.osdata.events, [])
        f = self.fresh(); first = RuntimeError('fixed initial helper failure')
        def fail(owner): _raise_fixed_fault(first)
        self.assert_primary(lambda: f.drive(changes={'_native_launch_files': fail}), first, f.module.main.__code__)
        self.assertFalse(any(row[0] == 'target' for row in f.events))

    def test_original_workflow_pipeline_retains_both_real_exit_statuses(self):
        _, _, raw = assembly_bytes()
        text = raw.decode()
        anchor = '      - name: Qualify actual runtime and derive official crypto Node before baseline staging\n'
        end = '      - name: Preserve nonsecret initial before-node launch evidence\n'
        self.assertEqual(text.count(anchor), 1); self.assertEqual(text.count(end), 1)
        selected = text.split(anchor, 1)[1].split(end, 1)[0].split('        run: |\n', 1)[1]
        original = ''.join(line[10:] for line in selected.splitlines(True))
        self.assertEqual(len(original.encode()), 1908)
        self.assertEqual(digest(original.encode()), '6bb00e7d1aaf7adafb1075caf31310504a20a49bfe17a3fbb19966b14beff0eb')
        script_operand = '"$GITHUB_WORKSPACE/som-rs/scripts/hosted_readiness/native_source_bootstrap.py"'
        output_operand = '"$RUNNER_TEMP/som-ts-consumer-wrapper-raw/before-node.stdout"'
        self.assertEqual(original.count(script_operand), 1); self.assertEqual(original.count(output_operand), 1)
        for label, child_exit, full, waiting in [('zero-zero', 0, False, False), ('child-seven', 7, False, False),
                ('tee-failure', 0, True, False), ('both-fail', 7, True, False), ('interrupted-wait', 0, False, True)]:
            with self.subTest(case=label), tempfile.TemporaryDirectory(prefix='som-f21-owned-') as directory:
                root = Path(directory); child = root / 'fixed_child.py'
                body = ("import sys,time\nsys.stdout.write('F21_WAITING\\n');sys.stdout.flush()\n"
                        "sys.stderr.write('fixed fixture stderr\\n');sys.stderr.flush()\ntime.sleep(30)\n" if waiting else
                        "import sys\nsys.stdout.write('F21_CONTROL\\n');sys.stdout.flush()\n"
                        "sys.stderr.write('fixed fixture stderr\\n');sys.stderr.flush()\nraise SystemExit(" + str(child_exit) + ")\n")
                child.write_text(body); child.chmod(0o600)
                replacement = shlex.quote(str(child)); controlled = original.replace(script_operand, replacement, 1)
                if full: controlled = controlled.replace(output_operand, '/dev/full', 1)
                inverse = controlled.replace('/dev/full', output_operand, 1) if full else controlled
                self.assertEqual(inverse.replace(replacement, script_operand, 1), original)
                environment = {key: 'fixed-synthetic-' + key for key in ('GITHUB_ACTIONS', 'GITHUB_REPOSITORY',
                    'GITHUB_REPOSITORY_ID', 'GITHUB_SERVER_URL', 'GITHUB_API_URL', 'GITHUB_SHA', 'GITHUB_WORKFLOW_SHA',
                    'GITHUB_EVENT_NAME', 'GITHUB_REF', 'GITHUB_WORKFLOW_REF', 'GITHUB_JOB', 'GITHUB_RUN_ID',
                    'GITHUB_RUN_ATTEMPT', 'RUNNER_NAME', 'RUNNER_ENVIRONMENT', 'RUNNER_OS')}
                environment.update(PATH='/usr/bin:/bin', HOME=str(root), RUNNER_TEMP=str(root), GITHUB_WORKSPACE=str(REPO),
                    HELPER_REVISION=REVISION, CANDIDATE_REVISION=REVISION, SOM_PRODUCT_PYTHON=sys.executable,
                    SOM_DEPENDENCY_ACTIONS_TOKEN='fixed-synthetic-noncredential')
                process = subprocess.Popen(['/bin/bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-s'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, start_new_session=True)
                status_path = root / 'som-ts-consumer-wrapper-raw/before-node.pipeline-exits'
                try:
                    if waiting:
                        with self.assertRaises(subprocess.TimeoutExpired) as caught:
                            process.communicate(controlled.encode(), timeout=5)
                        self.assertIn(b'F21_WAITING', caught.exception.output or b'')
                        self.assertFalse(status_path.exists()); self.assertIsNone(process.poll())
                        self.assertEqual(os.getpgid(process.pid), process.pid)
                        os.killpg(process.pid, signal.SIGTERM)
                        stdout, stderr = process.communicate(timeout=5)
                        self.assertIsNotNone(process.returncode)
                        self.assertNotEqual(process.returncode, 0)
                        self.assertFalse(status_path.exists())
                        print('F21 interrupted owned wait: no completed pipeline status exists', flush=True)
                    else:
                        stdout, stderr = process.communicate(controlled.encode(), timeout=10)
                        statuses = status_path.read_bytes().splitlines()
                        self.assertEqual(len(statuses), 2); self.assertTrue(all(value.isdigit() for value in statuses))
                        actual = [int(value) for value in statuses]
                        self.assertEqual(actual[0], child_exit)
                        self.assertEqual(actual[1] == 0, not full)
                        self.assertEqual(process.returncode == 0, child_exit == 0 and not full)
                        self.assertIn(b'F21_CONTROL', stdout)
                        self.assertEqual((root / 'som-ts-consumer-wrapper-raw/before-node.stderr').read_bytes(), b'fixed fixture stderr\n')
                        print('F21 ' + label + ' actual pipeline exits=' + repr(actual), flush=True)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate(timeout=5)
                    self.assertIsNotNone(process.returncode)

    def test_frozen_build_expected_nested_literal_oracle_and_unchanged_compile_data(self):
        f = self.fresh()
        source_a = ('def outer():\n'
                    '    def inner(value):\n'
                    '        return value in {3, 2}\n'
                    '    return inner\n')
        source_b = ('def outer():\n'
                    '    def inner(value):\n'
                    '        return value in (2, 3)\n'
                    '    return inner\n')
        filename = '<frozen independent_nested_literal_oracle>'
        original = compile(source_a, filename, 'exec', dont_inherit=True, optimize=0)
        oracle = compile(source_b, filename, 'exec', dont_inherit=True, optimize=0)
        fields = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals', 'co_stacksize',
                  'co_flags', 'co_code', 'co_consts', 'co_names', 'co_varnames', 'co_filename', 'co_name',
                  'co_qualname', 'co_firstlineno', 'co_linetable', 'co_exceptiontable', 'co_freevars', 'co_cellvars')
        code_type = type(original)
        def typed_snapshot(value):
            visited, scalar_units = 0, 0
            def validate(item, depth):
                nonlocal visited, scalar_units
                visited += 1
                if visited > 10000 or depth > 64:
                    raise ValueError('literal snapshot graph exceeds bound')
                kind = type(item)
                if kind is code_type:
                    for key in fields:
                        validate(object.__getattribute__(item, key), depth + 1)
                elif kind is tuple or kind is frozenset:
                    for child in item:
                        validate(child, depth + 1)
                else:
                    if not any(kind is scalar for scalar in
                               (str, bytes, int, float, complex, bool, type(None), type(Ellipsis))):
                        raise ValueError('literal snapshot constant type differs')
                    if kind is str or kind is bytes:
                        units = len(item)
                        if units > 65536:
                            raise ValueError('literal snapshot scalar exceeds bound')
                    elif kind is int:
                        bits = int.bit_length(item)
                        if bits > 4096:
                            raise ValueError('literal snapshot integer exceeds bound')
                        units = (bits + 7) // 8
                    else:
                        units = 1
                    scalar_units += units
                    if scalar_units > 262144:
                        raise ValueError('literal snapshot scalar total exceeds bound')
            # Validate the complete closed graph before hashing or comparison.
            validate(value, 0)
            def encode(item):
                kind = type(item)
                if kind is code_type:
                    return ('code',) + tuple((key, encode(object.__getattribute__(item, key))) for key in fields)
                if kind is tuple:
                    # Rebuild typed tuple data; compare values, not sharing.
                    return ('tuple',) + tuple(encode(child) for child in item)
                if kind is frozenset:
                    if not item:
                        return ('frozenset',)
                    return ('frozenset', frozenset(encode(child) for child in item))
                if kind is str: return ('str', item)
                if kind is bytes: return ('bytes', item)
                if kind is int: return ('int', item)
                if kind is bool: return ('bool', item)
                if kind is float: return ('float', struct.pack('>d', item))
                if kind is complex: return ('complex', struct.pack('>dd', item.real, item.imag))
                if kind is type(None): return ('none',)
                if kind is type(Ellipsis): return ('ellipsis',)
                raise ValueError('literal snapshot constant type differs')
            return encode(value)
        # Detached all18-field data and independent SourceB are the primary checks.
        before_data = typed_snapshot(original)
        # Selected 3.12.11 format2 avoids >=3 reference flags; this is supplementary.
        before = marshal.dumps(original, 2)
        snapshots, pending = [], [original]
        while pending:
            item = pending.pop()
            if type(item) is type(original):
                snapshots.append((item, item.co_consts))
                pending.extend(item.co_consts)
        built = f.module._native_frozen_build_expected(original)
        code_count, frozen_count = 0, 0
        pending = [(original, built, oracle)]
        while pending:
            old, actual, expected = pending.pop()
            self.assertIs(type(actual), type(expected))
            if type(old) is type(original):
                code_count += 1
                self.assertIs(type(actual), type(original))
                self.assertIsNot(actual, old)
                for key in fields:
                    if key != 'co_consts':
                        self.assertEqual(object.__getattribute__(actual, key), object.__getattribute__(old, key))
                    pending.append((object.__getattribute__(old, key), object.__getattribute__(actual, key),
                                    object.__getattribute__(expected, key)))
            elif type(old) is tuple:
                self.assertIs(type(actual), tuple)
                self.assertEqual(len(actual), len(old))
                self.assertEqual(len(actual), len(expected))
                pending.extend(zip(old, actual, expected))
            elif type(old) is frozenset:
                frozen_count += 1
                self.assertEqual(old, frozenset((2, 3)))
                self.assertIs(type(actual), tuple)
                self.assertEqual(actual, (2, 3))
                self.assertEqual(expected, (2, 3))
                self.assertTrue(all(type(value) is int for value in actual))
            else:
                self.assertIs(type(old), type(actual))
                self.assertEqual(actual, expected)
                self.assertEqual(actual, old)
        self.assertEqual(code_count, 3)
        self.assertEqual(frozen_count, 1)
        after_data = typed_snapshot(original)
        self.assertEqual(after_data, before_data)
        self.assertEqual(marshal.dumps(original, 2), before)
        for old, constants in snapshots:
            self.assertIs(old.co_consts, constants)
        inner = next(value for value in snapshots[-1][1] if type(value) is frozenset)
        self.assertIs(type(inner), frozenset)
        self.assertEqual(inner, frozenset((2, 3)))

    def test_frozen_build_expected_builtin_sort_and_repr_fallback_literal_goldens(self):
        f = self.fresh()
        seed = compile('pass\n', '<frozen independent_builtin_goldens>', 'exec', dont_inherit=True, optimize=0)
        fields = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals', 'co_stacksize',
                  'co_flags', 'co_code', 'co_consts', 'co_names', 'co_varnames', 'co_filename', 'co_name',
                  'co_qualname', 'co_firstlineno', 'co_linetable', 'co_exceptiontable', 'co_freevars', 'co_cellvars')
        cases = (
            ('int-order', frozenset((3, 2)), (2, 3)),
            ('str-order', frozenset(('z', 'a')), ('a', 'z')),
            ('mixed-fallback', frozenset((2, 'a', b'b')), ('a', 2, b'b')),
            ('tuple-fallback', frozenset(((2,), ('a',))), (('a',), (2,))),
            ('tuple-recursion', ((frozenset((3, 2)),),), (((2, 3),),)),
            ('builtin-leaves', (None, Ellipsis, True, False, 7, 1.25, 1 + 2j, 'x', b'x'),
                              (None, Ellipsis, True, False, 7, 1.25, 1 + 2j, 'x', b'x')),
            ('all-mixed-fallback', frozenset((None, Ellipsis, True, 3, 2.5, 1 + 2j, b'z', 'a', (4, 5))),
                                   ('a', 1 + 2j, (4, 5), 2.5, 3, Ellipsis, None, True, b'z')),
        )
        for label, value, golden in cases:
            with self.subTest(case=label):
                original = seed.replace(co_consts=(value, None))
                constants, before = original.co_consts, marshal.dumps(original)
                built = f.module._native_frozen_build_expected(original)
                # The expected value is a fixed literal above, never builder output.
                self.assertEqual(built.co_consts, (golden, None))
                self.assertIs(type(built.co_consts), tuple)
                pending = [(built.co_consts[0], golden)]
                while pending:
                    actual, expected = pending.pop()
                    self.assertIs(type(actual), type(expected))
                    if type(expected) is tuple:
                        self.assertEqual(len(actual), len(expected))
                        pending.extend(zip(actual, expected))
                    else:
                        self.assertEqual(actual, expected)
                for key in fields:
                    if key != 'co_consts':
                        self.assertEqual(object.__getattribute__(built, key), object.__getattribute__(original, key))
                self.assertIs(original.co_consts, constants)
                self.assertIs(original.co_consts[0], value)
                self.assertEqual(marshal.dumps(original), before)

    def test_frozen_build_expected_refuses_foreign_subclasses_hooks_and_finite_bounds(self):
        f = self.fresh()
        seed = compile('pass\n', '<frozen independent_rejection_data>', 'exec', dont_inherit=True, optimize=0)
        hooks = []
        def foreign_repr(self): hooks.append('repr'); return 'foreign'
        def foreign_compare(self, other): hooks.append('compare'); return False
        def foreign_hash(self): hooks.append('hash'); return 23
        def foreign_iter(self): hooks.append('iter'); return iter(())
        def foreign_len(self): hooks.append('len'); return 0
        def foreign_getitem(self, key): hooks.append('getitem'); raise IndexError('foreign')
        class ForeignMeta(type):
            def __eq__(self, other): hooks.append('metaclass-eq'); return False
            def __hash__(self): hooks.append('metaclass-hash'); return 29
            def __getattribute__(self, name):
                hooks.append('metaclass-getattribute')
                return type.__getattribute__(self, name)
        class Foreign(metaclass=ForeignMeta):
            __repr__, __lt__, __eq__, __hash__ = foreign_repr, foreign_compare, foreign_compare, foreign_hash
        foreign = Foreign()
        attributes = {'__repr__': foreign_repr, '__lt__': foreign_compare, '__eq__': foreign_compare,
                      '__hash__': foreign_hash, '__iter__': foreign_iter, '__len__': foreign_len,
                      '__getitem__': foreign_getitem}
        cases = [('unknown', foreign), ('unknown-later', (frozenset((2, 'a')), foreign)),
                 ('unknown-in-sort', frozenset((foreign, 2))), ('nested-unknown-in-sort', frozenset(((foreign,), (2,)))),
                 ('list', []), ('dict', {}), ('set', set()), ('bytearray', bytearray(b'x'))]
        for base, value in ((str, 'foreign'), (bytes, b'foreign'), (int, 7), (float, 1.25),
                            (complex, 1 + 2j), (tuple, (2, 3)), (frozenset, (2, 3))):
            kind = type('ForeignBuiltin', (base,), attributes)
            cases.append(('subclass-' + base.__name__, base.__new__(kind, value)))
        for label, value in cases:
            with self.subTest(case=label):
                candidate = seed.replace(co_consts=(value,))
                hooks.clear()
                self.reject(lambda: f.module._native_frozen_build_expected(candidate), 'native frozen-build expected constant type')
                self.assertEqual(hooks, [])
        hooks.clear()
        self.reject(lambda: f.module._native_frozen_build_expected(foreign), 'native frozen-build expected root')
        self.assertEqual(hooks, [])
        deep = None
        for _ in range(129): deep = (deep,)
        wide = (None,) * 500001
        for label, constants, message in (
                ('depth', (deep,), 'graph exceeds bound'),
                ('nodes', (wide, wide), 'graph exceeds bound'),
                ('container', (None,) * 1000001, 'container exceeds bound'),
                ('scalar', ('x' * 2097153,), 'scalar exceeds bound'),
                ('integer', (1 << 4096,), 'integer exceeds bound'),
                ('scalar-total', (b'x' * 2097152,) * 9, 'scalar total exceeds bound')):
            with self.subTest(case=label):
                candidate = seed.replace(co_consts=constants)
                hooks.clear()
                self.reject(lambda: f.module._native_frozen_build_expected(candidate), 'native frozen-build expected ' + message)
                self.assertEqual(hooks, [])
        for version in ((3, 12, 10), (3, 13, 0)):
            with self.subTest(case='version', value=version), mock.patch.object(f.sys, 'version_info', version):
                self.reject(lambda: f.module._native_frozen_build_expected(seed), 'native frozen-build expected version')
        for key, value in (('cache_tag', 'cpython-313'), ('optimize', 1)):
            target = f.sys.implementation if key == 'cache_tag' else f.sys.flags
            with self.subTest(case=key), mock.patch.object(target, key, value):
                self.reject(lambda: f.module._native_frozen_build_expected(seed), 'native frozen-build expected version')



if __name__ == '__main__':
    unittest.main(verbosity=2)
