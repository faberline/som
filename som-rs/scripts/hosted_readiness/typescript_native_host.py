"""Narrow native-only execution binding for the unchanged TypeScript product gates.

No frozen Mac host rule is changed. This adapter lives only in the new isolated
TypeScript owner child after exact native source and input-profile admission.
"""
from __future__ import annotations
from contextlib import contextmanager
import contextlib
import base64
import ast
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from types import CodeType, FunctionType, ModuleType, SimpleNamespace
from urllib.parse import urlsplit

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_native_host.py'
PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'
OWNER = BASE + 'typescript_regression_owner.py'
ARTIFACTS = 'som-rs/contracts/native-tools-v1/artifacts.json'
SOCKETS = BASE + 'typescript_regression_nested_rust.py'
HOST_SOURCE = 'som-rs/behavior/typescript/host.py'
CHECKER_SOURCE = 'som-rs/som-targets/qa/check_ts_products.py'
ADAPTER_ROLE = 'ctx.typescript-regression-native-host'
HOST_ROLE = 'ts.checker.original-typescript-host'
CHECKER_ROLE = 'ts.checker.original-ts-products'
STATE_CACHE = 'typescript_native_host_acquisitions'
_STATES = {}
_SERIAL = 0


class _OwnedMap(dict):
    """Source-owned mutable business records, not callback bindings."""


class _OwnedList(list):
    """Source-owned data lifetime, with no callable behavior."""


class _OwnedSet(set):
    """Source-owned retained product paths, with no callable behavior."""


def _bound_controls():
    from _som_native_active_controls_binding import controls
    require(type(ADAPTER_ROLE) is str and type(SELF) is str,
            'fixed native TypeScript role/source data required')
    require(controls.role_sources.get(ADAPTER_ROLE) == SELF,
            'fixed native TypeScript adapter source required')
    require(__name__ == '_som_full_typescript_regression_native_host',
            'fixed native TypeScript adapter namespace required')
    return controls


def _controls():
    controls = _bound_controls()
    controls.verify_installed()
    return controls


def _module(role):
    controls = _controls()
    require(type(role) is str and role in {CHECKER_ROLE, HOST_ROLE},
            'unlisted native TypeScript module role')
    if role == CHECKER_ROLE:
        path, name = CHECKER_SOURCE, '_som_ts_native_original_ts_products'
    else:
        path, name = HOST_SOURCE, '_som_ts_native_original_typescript_host'
    require(controls.role_sources.get(role) == path, 'fixed native TypeScript role source differs')
    module = controls.load_module(role)
    require(type(module) is ModuleType and module.__file__ == str(controls.root / path)
            and module.__name__ == name, 'actual native TypeScript module owner differs')
    return module


def _native_owner(ctx, checker):
    controls = _controls()
    require(type(ctx) is __som_native_Context__, 'actual source-owned Context class required')
    data = object.__getattribute__(ctx, '__dict__')
    require(type(data) is dict and type(data.get('cache')) is dict
            and type(data.get('repo')) is type(controls.root) and data['repo'] == controls.root
            and type(data.get('helper_revision')) is str
            and data['helper_revision'] == controls.helper_revision
            and type(data.get('candidate_revision')) is str
            and data['candidate_revision'] == controls.candidate_revision,
            'actual native TypeScript Context source owner differs')
    sources = data.get('sources')
    require(type(sources) is __som_native_SourceBytes__
            and type(sources.root) is type(controls.root) and sources.root == controls.root
            and type(sources.revision) is str and sources.revision == controls.helper_revision
            and type(sources.pins) is dict, 'actual native TypeScript SourceBytes required')
    held = __som_native_Context__._native_source_controls(ctx)
    require(type(held) is type(controls) and held.root == controls.root
            and held.helper_revision == controls.helper_revision
            and held.candidate_revision == controls.candidate_revision,
            'actual native TypeScript Context lease differs')
    held.verify_installed()
    target = _module(CHECKER_ROLE)
    require(checker is target, 'actual Core-owned TypeScript checker required')
    host = _module(HOST_ROLE)
    require(target.ts_host is host, 'actual Core-owned TypeScript host import required')
    return controls, host


def _secondary(primary, errors):
    try:
        previous = BaseException.__getattribute__(primary, '__som_native_host_secondary__')
    except AttributeError:
        previous = ()
    if type(previous) is not tuple:
        previous = (previous,)
    BaseException.__setattr__(primary, '__som_native_host_secondary__', previous + tuple(errors))


class _HostState:
    """Owned live objects stay here, never in Core's typed JSON bindings."""
    def __init__(self, ctx, checker, host, stamps, roles, created, entries, retained):
        frame = sys._getframe(1)
        require(frame.f_code is configure.__code__ and frame.f_globals is globals(),
                'only actual configure may acquire native TypeScript state')
        controls, actual_host = _native_owner(ctx, checker)
        require(host is actual_host and all(type(value) is _OwnedMap for value in
                (stamps, roles, created, entries)) and type(retained) is _OwnedSet,
                'actual native TypeScript process registry required')
        global _SERIAL
        require(type(_STATES) is dict and type(_SERIAL) is int,
                'owned native TypeScript state registry differs')
        require(not any(type(value) is _HostState and value.ctx is ctx and not value.closed
                        for value in _STATES.values()), 'native TypeScript acquisition already active')
        previous = ctx.cache.setdefault(STATE_CACHE, [])
        require(type(previous) is list, 'owned native TypeScript acquisition list required')
        self.ctx, self.checker, self.host = ctx, checker, host
        self.stamps, self.roles, self.created, self.entries = stamps, roles, created, entries
        self.retained = retained
        self.lease, self.scopes = controls.borrow(), _OwnedList()
        self.pointers, self.closed, self.active = _OwnedList(), False, False
        self.fragment_type = None
        self.constructor_exports = ()
        self.absent = object()
        _SERIAL += 1
        self.serial = _SERIAL
        self.ready = self.migrate = self.cwd_verified = self.verify_process = None
        self.original_ready = self.original_migrate = self.sockets = None
        _STATES[self.serial] = self
        previous.append(self)

    def verify(self, ctx=None, checker=None, *, check_pointers=True):
        controls = _controls()
        require(type(self) is _HostState and type(_STATES) is dict
                and type(self.serial) is int and type(self.closed) is bool
                and _STATES.get(self.serial) is self and not self.closed,
                'actual open native TypeScript acquisition required')
        require(ctx is None or self.ctx is ctx, 'native TypeScript Context pointer changed')
        require(checker is None or self.checker is checker, 'native TypeScript checker pointer changed')
        actual, host = _native_owner(self.ctx, self.checker)
        require(host is self.host and type(self.lease) is type(actual)
                and self.lease.root == actual.root
                and self.lease.helper_revision == actual.helper_revision
                and self.lease.candidate_revision == actual.candidate_revision,
                'native TypeScript borrowed lease owner changed')
        self.lease.verify_installed()
        cache = self.ctx.cache.get(STATE_CACHE)
        require(type(cache) is list and any(value is self for value in cache),
                'native TypeScript acquisition pointer changed')
        require(all(type(value) is _OwnedMap for value in
                (self.stamps, self.roles, self.created, self.entries))
                and type(self.retained) is _OwnedSet and type(self.scopes) is _OwnedList
                and type(self.pointers) is _OwnedList,
                'native TypeScript source-owned registry types changed')
        require(type(check_pointers) is bool, 'native pointer check mode differs')
        if check_pointers:
            for pointer in self.pointers:
                require(type(pointer) is tuple and len(pointer) == 4,
                        'native TypeScript owned pointer record differs')
                owner, key, unused, installed = pointer
                require(type(owner) is ModuleType and (owner is self.host or owner is self.checker)
                        and type(key) is str, 'native TypeScript owned attribute source differs')
                require(vars(owner).get(key, self.absent) is installed,
                        'native TypeScript attribute replaced by a later owner: ' + key)
        return controls

    def install(self, owner, key, value):
        frame = sys._getframe(1)
        require(frame.f_code is configure.__code__ and frame.f_globals is globals(),
                'only actual configure may install native TypeScript attributes')
        require(type(owner) is ModuleType and (owner is self.host or owner is self.checker),
                'native TypeScript attribute has a foreign module owner')
        allowed = ({'NODE_BIN', 'NPM_INVOCATION', 'PG_BIN_DIR', 'verify_toolchain',
                    'verify_process_cwd', 'ProcessTracker', 'migrate_python_product',
                    'python_product', 'pg_cluster', 'find_runtime_root', 'wait_http_ready',
                    'typescript_product'} if owner is self.host else
                   {'__som_native_process_stamps__', '__som_native_process_roles__',
                    '__som_native_verify_process__', 'tempfile', 'run_startup_gate'})
        require(type(key) is str and key in allowed, 'unlisted native TypeScript attribute')
        require(not any(item[0] is owner and item[1] == key for item in self.pointers),
                'native TypeScript attribute installed twice')
        original = vars(owner).get(key, self.absent)
        setattr(owner, key, value)
        self.pointers.append((owner, key, original, value))

    def callback(self, name):
        # These are fixed source callbacks captured by configure. No caller can
        # supply a callback, namespace, code object or control handle here.
        require(name in {'wait_http_ready', 'migrate', 'cwd_verified', 'verify_process'},
                'unlisted native TypeScript callback')
        value = {'wait_http_ready': self.ready, 'migrate': self.migrate,
                 'cwd_verified': self.cwd_verified, 'verify_process': self.verify_process}[name]
        codes = [code for code in configure.__code__.co_consts
                 if type(code) is CodeType and code.co_name == name]
        require(len(codes) == 1 and type(value) is FunctionType
                and value.__code__ is codes[0] and value.__globals__ is globals()
                and value.__builtins__ is configure.__builtins__ and value.__kwdefaults__ is None,
                'actual native TypeScript callback code/globals required')
        defaults = value.__defaults__
        if name == 'wait_http_ready':
            require(type(defaults) is tuple and len(defaults) == 3
                    and type(defaults[0]) is str and defaults[0] == '/healthz'
                    and defaults[1] is None and type(defaults[2]) is float and defaults[2] == 20.0,
                    'native TypeScript readiness invocation defaults changed')
        else:
            require(defaults is None, 'native TypeScript callback invocation defaults changed')
        expected = {'ctx': self.ctx, 'host': self.host, 'stamps': self.stamps,
                    'entries': self.entries, 'retained_python': self.retained,
                    'original_ready': self.original_ready, 'original_migrate': self.original_migrate,
                    'sockets': self.sockets, 'verify_process': self.verify_process,
                    'cwd_verified': self.cwd_verified}
        cells = value.__closure__ or ()
        require(len(cells) == len(value.__code__.co_freevars), 'native callback closure differs')
        for key, cell in zip(value.__code__.co_freevars, cells):
            require(key in expected and cell.cell_contents is expected[key],
                    'native TypeScript callback closure owner changed')
        return value

    def fragment_caller(self, frame, names):
        require(type(self.constructor_exports) is tuple, 'owned native constructor exports required')
        for name, scope, original in self.constructor_exports:
            require(type(name) is str and type(scope) is self.fragment_type
                    and type(original) is FunctionType,
                    'actual native constructor scope/export pointer required')
            if name not in names:
                continue
            current = getattr(scope, name)
            require(current is original, 'native constructor export replaced')
            body = current
            if name in {'typescript_product', 'python_product'}:
                require(current.__globals__ is contextlib.__dict__
                        and current.__code__.co_freevars == ('func',)
                        and type(current.__closure__) is tuple and len(current.__closure__) == 1,
                        'actual contextmanager constructor wrapper required')
                body = current.__closure__[0].cell_contents
                require(type(body) is FunctionType and current.__wrapped__ is body,
                        'native contextmanager constructor body changed')
            if (frame.f_code is body.__code__ and frame.f_globals is body.__globals__
                    and frame.f_builtins is body.__builtins__):
                return
        raise ValueError('actual originating native constructor fragment required')

    def close(self):
        # The reserved import still proves the actual current source, opaque
        # handle and unchanged finder/spec. It grants no new role execution.
        # A foreign later export can make global verify fail; that failure must
        # not skip restoration of other still-owned attributes or held scopes.
        controls = _bound_controls()
        require(type(self) is _HostState and type(self.serial) is int
                and type(self.closed) is bool and type(self.active) is bool,
                'actual native TypeScript cleanup state required')
        if self.closed:
            return
        require(type(_STATES) is dict and _STATES.get(self.serial) is self
                and type(self.ctx) is __som_native_Context__
                and type(self.checker) is ModuleType and type(self.host) is ModuleType
                and self.checker.__file__ == str(controls.root / CHECKER_SOURCE)
                and self.checker.__name__ == '_som_ts_native_original_ts_products'
                and self.host.__file__ == str(controls.root / HOST_SOURCE)
                and self.host.__name__ == '_som_ts_native_original_typescript_host'
                and type(self.lease) is type(controls)
                and self.lease.root == controls.root
                and self.lease.helper_revision == controls.helper_revision
                and self.lease.candidate_revision == controls.candidate_revision
                and type(self.pointers) is _OwnedList and type(self.scopes) is _OwnedList,
                'native TypeScript cleanup ownership changed')
        errors = []
        try:
            self.verify(check_pointers=False)
        except BaseException as error:
            errors.append((error, error.__traceback__))
        for pointer in reversed(self.pointers):
            try:
                require(type(pointer) is tuple and len(pointer) == 4,
                        'native TypeScript owned pointer record differs')
                owner, key, original, installed = pointer
                require(type(owner) is ModuleType and (owner is self.host or owner is self.checker)
                        and type(key) is str, 'native TypeScript owned attribute source differs')
                current = vars(owner).get(key, self.absent)
                require(current is installed, 'native TypeScript attribute replaced by a later owner: ' + key)
                if original is self.absent:
                    delattr(owner, key)
                else:
                    setattr(owner, key, original)
            except BaseException as error:
                errors.append((error, error.__traceback__))
        # Fixed bindings can refer to the adapters just restored. Remove every
        # fragment directly, before the next installed-source verification.
        for scope in reversed(self.scopes):
            try:
                require(type(scope) is self.fragment_type,
                        'native TypeScript fragment pointer replaced before cleanup')
                scope.close()
            except BaseException as error:
                errors.append((error, error.__traceback__))
        try:
            self.lease.close()
        except BaseException as error:
            errors.append((error, error.__traceback__))
        self.closed, self.active = True, False
        if type(_STATES) is dict and _STATES.get(self.serial) is self:
            del _STATES[self.serial]
        # ctx.cache retains the actual closed state and each real child object
        # for later evidence. No old record or Popen.args value is rewritten.
        if errors:
            primary, trace = errors[0]
            _secondary(primary, [error for error, unused in errors[1:]])
            raise primary.with_traceback(trace)


class _Provider:
    def __init__(self, repo, helper, candidate, role, serial):
        self.repo, self.helper, self.candidate = repo, helper, candidate
        self.role, self.serial = role, serial


def _provider(value):
    controls = _controls()
    require(type(value) in {_ProcessView, _ReadyView, _MigrateView, _HostView, _VerifyView},
            'actual source-owned native TypeScript provider required')
    fields = object.__getattribute__(value, '__dict__')
    require(type(fields) is dict and set(fields) == {'repo', 'helper', 'candidate', 'role', 'serial'}
            and type(fields['repo']) is type(controls.root) and fields['repo'] == controls.root
            and type(fields['helper']) is str and fields['helper'] == controls.helper_revision
            and type(fields['candidate']) is str and fields['candidate'] == controls.candidate_revision
            and type(fields['role']) is str and fields['role'] == ADAPTER_ROLE
            and type(fields['serial']) is int and type(_STATES) is dict,
            'native TypeScript provider data owner changed')
    state = _STATES.get(fields['serial'])
    require(type(state) is _HostState, 'actual source-owned native TypeScript state required')
    state.verify()
    require(state.active, 'native TypeScript constructors are not active')
    return state


def _registered_child(state, *args, **kwargs):
    _controls()
    require(type(state) is _HostState, 'actual native TypeScript child state required')
    frame = sys._getframe(1)
    require(frame.f_code is _ProcessView.Popen.__code__ and frame.f_globals is globals()
            and type(frame.f_locals.get('self')) is _ProcessView,
            'actual fixed native process provider required')
    state.verify()
    ctx, created = state.ctx, state.created
    require(len(args) == 1 and isinstance(args[0], (list, tuple))
            and kwargs.get('start_new_session') is True and not kwargs.get('shell')
            and kwargs.get('executable') is None and ctx.work in Path(kwargs['cwd']).absolute().parents,
            'only exact owned product child constructors may be registered')
    proc = subprocess.Popen(*args, **kwargs)
    created[proc.pid] = {'process': proc, 'requested_argv': [str(v) for v in args[0]],
                         'cwd': str(Path(kwargs['cwd']).absolute()), 'new_session': True}
    created[proc.pid]['actual_argv'] = proc.args
    created[proc.pid]['constructor_io'] = {key: kwargs.get(key) for key in ('stdin', 'stdout', 'stderr')}
    return proc


class _ProcessView(_Provider):
    @property
    def STDOUT(self):
        _provider(self).fragment_caller(sys._getframe(1),
            {'typescript_product', 'python_product', 'run_startup_gate'})
        return subprocess.STDOUT

    def Popen(self, *args, **kwargs):
        state = _provider(self)
        state.fragment_caller(sys._getframe(1),
            {'typescript_product', 'python_product', 'run_startup_gate'})
        return _registered_child(state, *args, **kwargs)


class _ReadyView(_Provider):
    def __call__(self, *args, **kwargs):
        state = _provider(self)
        state.fragment_caller(sys._getframe(1), {'typescript_product', 'python_product'})
        return state.callback('wait_http_ready')(*args, **kwargs)


class _MigrateView(_Provider):
    def __call__(self, *args, **kwargs):
        state = _provider(self)
        state.fragment_caller(sys._getframe(1), {'python_product'})
        return state.callback('migrate')(*args, **kwargs)


class _HostView(_Provider):
    @property
    def NODE_BIN(self):
        state = _provider(self)
        state.fragment_caller(sys._getframe(1), {'run_startup_gate'})
        require(type(state.host.NODE_BIN) is str, 'native TypeScript node path changed')
        return state.host.NODE_BIN

    def check_port_allowed(self, *args, **kwargs):
        state = _provider(self)
        state.fragment_caller(sys._getframe(1), {'run_startup_gate'})
        return state.host.check_port_allowed(*args, **kwargs)

    def wait_http_ready(self, *args, **kwargs):
        state = _provider(self)
        state.fragment_caller(sys._getframe(1), {'run_startup_gate'})
        return state.callback('wait_http_ready')(*args, **kwargs)

    def verify_process_cwd(self, *args, **kwargs):
        state = _provider(self)
        state.fragment_caller(sys._getframe(1), {'run_startup_gate'})
        return state.callback('cwd_verified')(*args, **kwargs)


class _VerifyView(_Provider):
    def __call__(self, *args, **kwargs):
        return _provider(self).callback('verify_process')(*args, **kwargs)


def require(value, message):
    if not value:
        raise ValueError(message)


def file_sha(path):
    path = Path(path)
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), 'regular native tool required')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root):
    values = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'native TypeScript input link/special member')
        if stat.S_ISREG(info.st_mode):
            values[path.relative_to(root).as_posix()] = {'sha256': file_sha(path), 'size': info.st_size,
                                                      'mode': stat.S_IMODE(info.st_mode)}
    return values


def process_stamp(pid):
    root = Path('/proc') / str(pid)
    raw = (root / 'stat').read_bytes()
    fields = raw.rsplit(b')', 1)[1].split()
    exe = (root / 'exe').stat()
    cmdline = (root / 'cmdline').read_bytes()
    require(cmdline.endswith(b'\0') and not cmdline.startswith(b'\0'), 'complete actual process argv required')
    environment = {}
    for item in (root / 'environ').read_bytes().split(b'\0'):
        name, separator, value = item.partition(b'=')
        if separator and name in {b'SOM_APP_ADDR', b'DATABASE_URL', b'SOM_BEHAVIOR_DATABASE_URL', b'TEST_DATABASE_URL'}:
            key, value = name.decode(), value.decode('utf-8', 'strict')
            require(key not in environment, 'duplicate actual process environment key')
            if key != 'SOM_APP_ADDR':
                parsed = urlsplit(value.replace('postgresql+psycopg://', 'postgresql://', 1))
                require(parsed.scheme == 'postgresql' and parsed.hostname == '127.0.0.1'
                        and parsed.port is not None and parsed.port not in {8765, 18765, 18978, 18979}
                        and parsed.username == 'postgres' and parsed.password is None,
                        'only owned password-free loopback database environment may be retained')
            environment[key] = value
    return {'pid': pid, 'state': fields[0].decode(), 'pgrp': int(fields[2]), 'session': int(fields[3]),
            'start_ticks': int(fields[19]), 'executable_device': exe.st_dev, 'executable_inode': exe.st_ino,
            'cwd': os.readlink(root / 'cwd'), 'proc_stat_raw': raw.decode('utf-8', 'strict'),
            'cmdline_raw_b64': base64.b64encode(cmdline).decode(), 'environment': environment}


def port_listeners(port):
    result = []
    for protocol in ('tcp', 'tcp6'):
        for raw in (Path('/proc/net') / protocol).read_text().splitlines()[1:]:
            fields = raw.split()
            if fields[3] == '0A' and int(fields[1].split(':')[1], 16) == port:
                result.append({'protocol': protocol, 'raw': raw})
    return result


def configure(ctx, checker):
    controls, actual_host = _native_owner(ctx, checker)
    require(ctx.qa_mode == 'native-qa-v1' and ctx.qa_source_authority['source_authority_active'] is True
            and {SELF, OWNER} <= set(ctx.qa_source_authority['admitted_repository_python_sources']),
            'exact native TypeScript host source admission required')
    ctx.sources.read(SELF)
    profile = json.loads(ctx.sources.read(PROFILE))
    roots = ctx.data['typescript_regression_inputs']['roots']
    native_root, cache = Path(roots['node_tools']), Path(roots['npm_cache'])
    native = profile['native_typescript_tools']
    owner = ctx.module('typescript_regression_input_inventory', OWNER)
    require(set(native) == {'node', 'npm_cli', 'npm_root'}, 'exact native Node/npm source paths required')
    for value in native.values():
        require(type(value) is str and value and not value.startswith('/')
                and all(p not in {'', '.', '..'} for p in value.split('/')), 'safe native tool member required')
    node, npm_cli, npm_root = (native_root / native[name] for name in ('node', 'npm_cli', 'npm_root'))
    artifacts = json.loads(ctx.sources.read(ARTIFACTS))
    admitted_nodes = [row for row in artifacts['tools'] if row['tool'] == 'node'
                      and row['host'] == 'x86_64-unknown-linux-gnu' and row['version'] == '26.5.0']
    require(len(admitted_nodes) == 1 and file_sha(node) == admitted_nodes[0]['executable_sha256']
            and node.read_bytes()[:4] == b'\x7fELF' and os.access(node, os.X_OK),
            'native Node differs from the exact committed artifact authority')
    require(file_sha(npm_cli) == profile['roles']['node_tools']['files'][native['npm_cli']]['sha256'],
            'native npm entry differs from the complete source-profile tree')
    host = checker.ts_host
    require(host.__file__.endswith('/som-rs/behavior/typescript/host.py'), 'original TypeScript host module required')
    pg = ctx.verify_postgres()
    original_python = host.python_product
    original_migrate = host.migrate_python_product
    retained_python = _OwnedSet()
    stamps = _OwnedMap()
    roles = _OwnedMap()
    created = _OwnedMap()
    entries = _OwnedMap()
    require(host is actual_host, 'actual native TypeScript host pointer changed')
    state = _HostState(ctx, checker, host, stamps, roles, created, entries, retained_python)
    try:

        def verify_inputs():
            require(owner.tree(native_root) == profile['roles']['node_tools']['files']
                    and owner.tree(cache) == profile['roles']['npm_cache']['files'], 'complete native TypeScript tool/cache bytes changed')
            require(host.tree_sha(npm_root) == (host.TOOLCHAIN['npm']['tree_sha256'],
                    host.TOOLCHAIN['npm']['tree_files'], host.TOOLCHAIN['npm']['tree_symlinks']),
                    'native npm JavaScript differs from the frozen TypeScript npm release')
            require(host.tree_sha(cache) == (host.TOOLCHAIN['vendored_cache']['tree_sha256'],
                    host.TOOLCHAIN['vendored_cache']['tree_files'], host.TOOLCHAIN['vendored_cache']['tree_symlinks']),
                    'the TypeScript cache differs from its own original frozen authority')
            ctx.verify_postgres()

        def native_toolchain(cache_dir=None):
            require(cache_dir is not None and Path(cache_dir) == cache, 'only the admitted TypeScript cache is allowed')
            verify_inputs()
            versions = []
            for command in ([str(node), '--version'], [str(node), str(npm_cli), '--version'],
                            [str(pg / 'postgres'), '--version']):
                result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
                require(result.returncode == 0 and not result.stderr, 'qualified native tool version command failed')
                versions.append(result.stdout.strip())
            require(versions[0] == host.TOOLCHAIN['node']['version']
                    and versions[1] == host.TOOLCHAIN['npm']['version']
                    and versions[2].startswith('postgres (PostgreSQL) ' + host.TOOLCHAIN['postgresql']['version']),
                    'actual native tools changed a frozen TypeScript tool version')
            return {'mode': 'native-qa-v1', 'node_version': versions[0],
                    'node_sha256': file_sha(node), 'libnode_sha256': None,
                    'npm_version': versions[1], 'npm_tree_sha256': host.tree_sha(npm_root)[0],
                    'postgresql_version': versions[2],
                    'cache': {'path': str(cache), 'tree_sha256': host.tree_sha(cache)[0],
                              'tree_files': host.tree_sha(cache)[1], 'tree_symlinks': 0}}

        def cwd_verified(pid, expected):
            try:
                if pid in host.forbidden_pids():
                    return False, None
                actual = process_stamp(pid)
                wanted = stamps.get(pid)
                same = (wanted is not None and all(actual[key] == wanted[key] for key in
                    ('pid', 'pgrp', 'session', 'start_ticks', 'executable_device', 'executable_inode'))
                    and actual['state'] != 'Z' and Path(actual['cwd']).resolve() == Path(expected).resolve())
                return same, actual['cwd']
            except (OSError, ValueError, IndexError):
                return False, None

        def verify_process(pid):
            require(pid in stamps and cwd_verified(pid, stamps[pid]['cwd'])[0],
                    'actual owned process identity changed')
            return process_stamp(pid)

        original_tracker = host.ProcessTracker
        class _RegistrationBase(original_tracker):
            def register_checked(self, pid, role, cwd, command):
                require(pid not in host.forbidden_pids(), 'protected process cannot enter the native tracker')
                stamp = process_stamp(pid)
                require(stamp['pgrp'] == stamp['session'] == pid and stamp['state'] != 'Z'
                        and Path(stamp['cwd']).resolve() == Path(cwd).resolve(), 'new owned native process group required')
                if role == 'postgres':
                    expected = pg / 'postgres'
                elif role in {'typescript_product', 'typescript_startup_probe'}:
                    expected = node
                elif role == 'python_product':
                    expected = Path(ctx.runtime()['tools']['python'])
                else:
                    raise ValueError('unadmitted native TypeScript process role: ' + role)
                info = expected.stat()
                require((stamp['executable_device'], stamp['executable_inode']) == (info.st_dev, info.st_ino),
                        'actual owned process executable differs from its qualified role')
                stamps[pid] = stamp
                roles[pid] = role
                record = super().register(pid, role, cwd, command)
                record['native_owned_start'] = stamp
                record['native_actual_argv'] = [part.decode('utf-8', 'strict') for part in
                    base64.b64decode(stamp['cmdline_raw_b64'], validate=True)[:-1].split(b'\0')]
                record['native_executable_sha256'] = file_sha(expected)
                entries[pid] = record
                return record

        class NativeTracker(_RegistrationBase):
            def __init__(self):
                original_tracker.__init__(self)

            def register(self, pid, role, cwd, command):
                try:
                    return self.register_checked(pid, role, cwd, command)
                except BaseException as primary:
                    held = created.get(pid)
                    cleanup = {'pid': pid, 'role': role, 'primary_error': type(primary).__name__,
                               'primary_reason': str(primary), 'wait_attempted': False, 'wait_completed': False,
                               'signals': [], 'cleanup_errors': []}
                    if held is not None:
                        proc = held['process']
                        try:
                            require(held['new_session'] is True and held['cwd'] == str(Path(cwd).absolute())
                                    and held['requested_argv'] == [str(v) for v in command]
                                    and proc.pid == pid and proc.returncode is None,
                                    'failed registration has no unreaped owned new-session handle')
                            # Before the first wait/poll, the direct child's PID is
                            # still reserved. Its constructor created this session.
                            # No unrelated process group can reuse that ID here.
                            try:
                                os.killpg(pid, signal.SIGTERM); cleanup['signals'].append('SIGTERM')
                            except ProcessLookupError:
                                pass
                        except BaseException as error:
                            cleanup['cleanup_errors'].append({'stage': 'owned-group-stop',
                                'error': type(error).__name__, 'reason': str(error)})
                        cleanup['wait_attempted'] = True
                        try:
                            code = proc.wait(timeout=10)
                            cleanup.update(wait_completed=True, exit_code=code)
                        except subprocess.TimeoutExpired:
                            try:
                                require(proc.returncode is None, 'owned child was reaped before escalation')
                                os.killpg(pid, signal.SIGKILL); cleanup['signals'].append('SIGKILL')
                            except BaseException as error:
                                cleanup['cleanup_errors'].append({'stage': 'owned-group-escalation',
                                    'error': type(error).__name__, 'reason': str(error)})
                            try:
                                cleanup.update(wait_completed=True, exit_code=proc.wait(timeout=10))
                            except BaseException as error:
                                cleanup['cleanup_errors'].append({'stage': 'final-child-wait',
                                    'error': type(error).__name__, 'reason': str(error)})
                        except BaseException as error:
                            cleanup['cleanup_errors'].append({'stage': 'actual-child-wait',
                                'error': type(error).__name__, 'reason': str(error)})
                    directory = ctx.work / 'typescript-regression/native-registration-failures'
                    try:
                        directory.mkdir(parents=True, exist_ok=True)
                        (directory / (str(pid) + '.json')).write_text(json.dumps(cleanup, sort_keys=True) + '\n')
                    except BaseException as error:
                        cleanup['cleanup_errors'].append({'stage': 'failure-evidence',
                            'error': type(error).__name__, 'reason': str(error)})
                    primary.__som_typescript_registration_cleanup__ = cleanup
                    raise

            def stop(self, proc, cwd, entry, sig=signal.SIGTERM, timeout=10.0):
                primary, code, waited = None, None, None
                try:
                    code = super().stop(proc, cwd, entry, sig=sig, timeout=timeout)
                except BaseException as error:
                    primary = error
                try:
                    waited = proc.wait(timeout=10)
                    entry['wait_completed'], entry['wait_exit_code'] = True, waited
                except BaseException as error:
                    entry['wait_completed'] = False
                    entry.setdefault('native_cleanup_failures', []).append(
                        {'stage': 'actual-child-wait', 'error': type(error).__name__, 'reason': str(error)})
                    if primary is None:
                        primary = error
                try:
                    require('native_owned_start' in entry and entry.get('command'), 'actual native start metadata required')
                    command = entry['command']
                    if entry['role'].startswith('typescript_'):
                        port = urlsplit('http://' + entry['native_owned_start']['environment']['SOM_APP_ADDR']).port
                    else:
                        flag = '-p' if entry['role'] == 'postgres' else '--port'
                        port = int(command[command.index(flag) + 1])
                    require(type(port) is int and port not in {8765, 18765, 18978, 18979}, 'owned terminal port required')
                    remaining = port_listeners(port)
                    entry['native_terminal'] = {'pid': proc.pid, 'port': port, 'wait_exit_code': waited,
                                                 'remaining_listeners': remaining}
                    require(waited is not None and not remaining, 'actual owned product port remains after wait')
                except BaseException as error:
                    entry.setdefault('native_cleanup_failures', []).append(
                        {'stage': 'terminal-listener-check', 'error': type(error).__name__, 'reason': str(error)})
                    if primary is None:
                        primary = error
                if primary is not None:
                    raise primary
                require(type(waited) is int and waited == code, 'actual owned native child wait differs')
                return waited

        @contextmanager
        def pg_cluster(work, port, tracker):
            """Keep the same fresh PG settings, with an actual owned child handle."""
            host.check_port_allowed(port)
            data, log = Path(work) / 'pgdata', Path(work) / 'postgres.log'
            env = {**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC'}
            command = [str(pg / 'initdb'), '-D', str(data), '-U', 'postgres', '-A', 'trust',
                       '--encoding=UTF8', '--locale=C', '--no-instructions']
            initialized = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
            require(initialized.returncode == 0, 'qualified PostgreSQL initdb failed: ' + initialized.stderr)
            command = [str(pg / 'postgres'), '-D', str(data), '-F', '-h', '127.0.0.1',
                       '-p', str(port), '-k', '', '-c', 'timezone=UTC']
            with log.open('xb') as stream:
                proc = registered_popen(command, cwd=data, env=env, stdin=subprocess.DEVNULL,
                                        stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            record, primary = None, None
            try:
                record = tracker.register(proc.pid, 'postgres', data, command)
                deadline, last = time.monotonic() + 30, ''
                while True:
                    require(proc.poll() is None, 'owned PostgreSQL exited before readiness')
                    try:
                        with checker.rust_products.psycopg.connect(
                                f'postgresql://postgres@127.0.0.1:{port}/postgres', connect_timeout=1):
                            pass
                        require(checker.base_hosts.listening(proc.pid, port),
                                'PostgreSQL readiness socket is outside the owned child')
                        break
                    except checker.rust_products.psycopg.OperationalError as error:
                        last = str(error)
                        require(time.monotonic() < deadline, 'owned PostgreSQL readiness timed out: ' + last)
                        time.sleep(0.05)
                yield f'127.0.0.1:{port}'
            except BaseException as error:
                primary = error
                raise
            finally:
                cleanup_errors = []
                try:
                    if proc.returncode is not None:
                        pass
                    elif record is not None:
                        tracker.stop(proc, data, record)
                    else:
                        # Register failed. Kill only the new Popen handle after its
                        # exact process/executable/CWD stamp is rechecked.
                        stamp = process_stamp(proc.pid)
                        expected = (pg / 'postgres').stat()
                        require(stamp['pgrp'] == stamp['session'] == proc.pid
                                and Path(stamp['cwd']).resolve() == data.resolve()
                                and (stamp['executable_device'], stamp['executable_inode']) ==
                                    (expected.st_dev, expected.st_ino),
                                'failed PostgreSQL registration has no owned cleanup proof')
                        proc.terminate(); proc.wait(timeout=10)
                except BaseException as cleanup:
                    cleanup_errors.append({'stage': 'owned-stop', 'error': type(cleanup).__name__, 'reason': str(cleanup)})
                    if primary is None:
                        primary = cleanup
                # A stamp or signaling failure must never skip the actual handle's
                # final wait attempt. Unknown ownership still forbids a blind kill.
                terminal = {'wait_attempted': True, 'wait_completed': False, 'exit_code': None}
                try:
                    terminal.update(wait_completed=True, exit_code=proc.wait(timeout=10))
                except BaseException as cleanup:
                    cleanup_errors.append({'stage': 'final-child-wait', 'error': type(cleanup).__name__, 'reason': str(cleanup)})
                    if primary is None:
                        primary = cleanup
                terminal['cleanup_errors'] = cleanup_errors
                try:
                    (Path(work) / 'native-postgres-cleanup.json').write_text(
                        json.dumps(terminal, sort_keys=True, separators=(',', ':')) + '\n')
                except BaseException as cleanup:
                    cleanup_errors.append({'stage': 'cleanup-evidence', 'error': type(cleanup).__name__, 'reason': str(cleanup)})
                    if primary is None:
                        primary = cleanup
                if primary is not None and cleanup_errors:
                    primary.__som_typescript_pg_cleanup__ = terminal
                    raise primary

        def migrate(work, root, db_url, python_exe):
            require(ctx.work in Path(root).absolute().parents, 'TypeScript reference Python root must belong to Context')
            python = ctx.prepare_python(root)
            passed = False
            try:
                original_migrate(work, root, db_url, str(python)); passed = True
            finally:
                if str(Path(root).absolute()) not in retained_python:
                    ctx.release_python(root, passed)

        @contextmanager
        def python_product(work, name, prompt, files, db_url, port, python_exe, tracker):
            root = Path(work) / name
            retained_python.add(str(root.absolute()))
            passed = False
            try:
                with original_python(work, name, prompt, files, db_url, port,
                                     str(root / '.venv/bin/python'), tracker) as url:
                    yield url
                passed = True
            finally:
                retained_python.discard(str(root.absolute()))
                if root.is_dir():
                    ctx.release_python(root, passed)

        require(SOCKETS in ctx.qa_source_authority['admitted_repository_python_sources'],
                'owned listener source must be admitted')
        sockets = ctx.module('typescript_native_owned_listeners', SOCKETS)
        original_ready = host.wait_http_ready
        def wait_http_ready(proc, port, path='/healthz', log_path=None, timeout=20.0):
            original_ready(proc, port, path, log_path=log_path, timeout=timeout)
            verify_process(proc.pid)
            owned = sockets.owned_socket(proc.pid, port)
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                connection.request('GET', path)
                response = connection.getresponse(); raw = response.read(64 * 1024**2 + 1)
                require(len(raw) <= 64 * 1024**2 and response.status < 500,
                        'actual owned product readiness response refused')
                row = {'pid': proc.pid, 'method': 'GET', 'path': path,
                       'url': f'http://127.0.0.1:{port}' + path, 'status': response.status,
                       'response_headers': list(response.getheaders()),
                       'response_sha256': hashlib.sha256(raw).hexdigest(), 'owned_socket': owned}
            finally:
                connection.close()
            require(sockets.owned_socket(proc.pid, port) == owned, 'owned readiness listener changed')
            directory = ctx.work / 'typescript-regression/native-readiness' / str(proc.pid)
            directory.mkdir(parents=True)
            (directory / 'response.raw').write_bytes(raw)
            (directory / 'result.json').write_text(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
            entries[proc.pid]['native_readiness'] = row
        # The old checker deletes its TemporaryDirectory. Native evidence must keep
        # all product bytes and failed database state below the checked work root.
        temporary_roots = _OwnedList()
        class RetainedTemporaryDirectory:
            def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=False, **kwargs):
                require(dir is None and not kwargs, 'only the fixed native product work root is allowed')
                parent = ctx.work / 'typescript-regression/native-products'
                parent.mkdir(parents=True, exist_ok=True)
                self.name = tempfile.mkdtemp(suffix=suffix or '', prefix=prefix or 'tmp', dir=parent)
                temporary_roots.append(self.name)
            def __enter__(self):
                return self.name
            def __exit__(self, *exc):
                return False
        retained_tempfile = SimpleNamespace(**{key: value for key, value in vars(tempfile).items()
                                             if key != 'TemporaryDirectory'},
                                           TemporaryDirectory=RetainedTemporaryDirectory)
        def registered_popen(*args, **kwargs):
            require(len(args) == 1 and isinstance(args[0], (list, tuple))
                    and kwargs.get('start_new_session') is True and not kwargs.get('shell')
                    and kwargs.get('executable') is None and ctx.work in Path(kwargs['cwd']).absolute().parents,
                    'only exact owned product child constructors may be registered')
            proc = subprocess.Popen(*args, **kwargs)
            created[proc.pid] = {'process': proc, 'requested_argv': [str(v) for v in args[0]],
                                 'cwd': str(Path(kwargs['cwd']).absolute()), 'new_session': True}
            created[proc.pid]['actual_argv'] = proc.args
            created[proc.pid]['constructor_io'] = {key: kwargs.get(key) for key in ('stdin', 'stdout', 'stderr')}
            return proc
        for path, name in ((HOST_SOURCE, 'typescript_product'), (HOST_SOURCE, 'python_product'),
                           (CHECKER_SOURCE, 'run_startup_gate')):
            require(path in ctx.qa_source_authority['admitted_repository_python_sources'],
                    'original product constructor source must be admitted')
            raw = ctx.sources.read(path); tree = ast.parse(raw)
            nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
            require(len(nodes) == 1, 'exact original owned product constructor required')
        state.ready, state.migrate = wait_http_ready, migrate
        state.cwd_verified, state.verify_process = cwd_verified, verify_process
        state.original_ready, state.original_migrate, state.sockets = original_ready, original_migrate, sockets
        header = (controls.root, controls.helper_revision, controls.candidate_revision, ADAPTER_ROLE, state.serial)
        process_view, ready_view = _ProcessView(*header), _ReadyView(*header)
        migrate_view, host_view, verify_view = _MigrateView(*header), _HostView(*header), _VerifyView(*header)
        native_node = str(node)
        ts_scope = state.lease.load_fragment('ctx.typescript-regression-native-host.typescript-product')
        state.scopes.append(ts_scope)
        state.fragment_type = type(ts_scope)
        python_scope = state.lease.load_fragment('ctx.typescript-regression-native-host.python-product')
        state.scopes.append(python_scope)
        require(type(python_scope) is state.fragment_type, 'actual Python constructor fragment type differs')
        startup_scope = state.lease.load_fragment('ctx.typescript-regression-native-host.startup-gate')
        state.scopes.append(startup_scope)
        require(type(startup_scope) is state.fragment_type, 'actual startup constructor fragment type differs')
        original_python = python_scope.python_product
        state.constructor_exports = (
            ('typescript_product', ts_scope, ts_scope.typescript_product),
            ('python_product', python_scope, original_python),
            ('run_startup_gate', startup_scope, startup_scope.run_startup_gate))
        state.install(checker, '__som_native_process_stamps__', stamps)
        state.install(checker, '__som_native_process_roles__', roles)
        state.install(checker, '__som_native_verify_process__', verify_view.__call__)
        state.install(host, 'NODE_BIN', native_node)
        state.install(host, 'NPM_INVOCATION', [str(node), str(npm_cli)])
        state.install(host, 'PG_BIN_DIR', pg)
        state.install(host, 'verify_toolchain', native_toolchain)
        state.install(host, 'verify_process_cwd', cwd_verified)
        state.install(host, 'ProcessTracker', NativeTracker)
        state.install(host, 'migrate_python_product', migrate)
        state.install(host, 'python_product', python_product)
        state.install(host, 'pg_cluster', pg_cluster)
        state.install(host, 'find_runtime_root', lambda: ctx.repo)
        state.install(host, 'wait_http_ready', wait_http_ready)
        state.install(host, 'typescript_product', ts_scope.typescript_product)
        state.install(checker, 'tempfile', retained_tempfile)
        state.install(checker, 'run_startup_gate', startup_scope.run_startup_gate)
        state.active = True
        verify_inputs()
        return {'profile_sha256': hashlib.sha256(ctx.sources.read(PROFILE)).hexdigest(),
                'node_sha256': file_sha(node), 'npm_tree_sha256': host.tree_sha(npm_root)[0],
                'typescript_cache_sha256': host.tree_sha(cache)[0], 'retained_temporary_roots': temporary_roots,
                'postgres_launch_role': 'qualified-owned-direct-child', 'product_acceptance_passed': False}
    except BaseException as primary:
        try:
            state.close()
        except BaseException as secondary:
            _secondary(primary, (secondary,))
        raise


def close(ctx, checker):
    """Owned unwind after original Context verification; safe before acquire."""
    incoming = sys.exc_info()[1]
    incoming_trace = incoming.__traceback__ if incoming is not None else None
    try:
        _bound_controls()
    except BaseException as error:
        if incoming is not None:
            _secondary(incoming, (error,))
            raise incoming.with_traceback(incoming_trace)
        raise
    require(type(_STATES) is dict, 'owned native TypeScript state registry differs')
    selected = []
    for state in tuple(_STATES.values()):
        require(type(state) is _HostState, 'foreign native TypeScript acquisition retained')
        if state.ctx is ctx and state.checker is checker:
            selected.append(state)
    # The caller sets its attempted flag before configure. It may reach here
    # before Context/target validation or before a single scope was acquired.
    if not selected:
        return None
    errors = []
    for state in reversed(selected):
        try:
            state.close()
        except BaseException as error:
            errors.append((error, error.__traceback__))
    if errors:
        if incoming is not None:
            _secondary(incoming, [error for error, unused in errors])
            raise incoming.with_traceback(incoming_trace)
        primary, trace = errors[0]
        _secondary(primary, [error for error, unused in errors[1:]])
        raise primary.with_traceback(trace)
    return None
