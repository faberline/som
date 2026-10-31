"""GitHub-only host fragment/process protocol; no native factory or products.

Core, RawQaFinder, Context/SourceBytes classes, full TS host/checker sources,
three original function selectors and Linux listener helpers are actual source.
Bootstrap/source admission and Context constructors are explicit protocol
doubles. The real-child case builds acquisition data without configure. The
partial case runs actual configure with explicit artifact/profile/PG doubles
and intentionally refuses the third scope before tools or children execute.
Its readiness/tracker/base/Rust/package/suite support are explicit runtime
doubles. A real Python interpreter runs a tiny HTTP fixture as the Node double.
No Node/npm/PG tool admission, real product, SQL, original plan or native Context
qualification is claimed. Selectors are recomputed by the GitHub interpreter.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import ModuleType
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub tests only')

HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
BASE = 'som-rs/scripts/hosted_readiness/'
ADAPTER_SOURCE = BASE + 'typescript_native_host.py'
HOST_SOURCE = 'som-rs/behavior/typescript/host.py'
CHECKER_SOURCE = 'som-rs/som-targets/qa/check_ts_products.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
LISTENERS = BASE + 'typescript_regression_nested_rust.py'
OWNER_SOURCE = BASE + 'typescript_regression_owner.py'
ADAPTER = 'ctx.typescript-regression-native-host'
HOST = 'ts.checker.original-typescript-host'
CHECKER = 'ts.checker.original-ts-products'
CONSTRUCTORS = {
    'typescript_product': ADAPTER + '.typescript-product',
    'python_product': ADAPTER + '.python-product',
    'run_startup_gate': ADAPTER + '.startup-gate'}
FIELDS = {'repo': 'path', 'helper': 'str', 'candidate': 'str', 'role': 'str', 'serial': 'int'}
REVIEWED_SHA256 = {
    "som-rs/scripts/hosted_readiness/typescript_native_host.py": "74811b731ca0d7c26f72b5e914742f16c4488ce571370caaa37e07f89d05cbb2",
    "som-rs/scripts/hosted_readiness/test_native_active_controls.py": "f76335be9269618bcec26750895202a896bbe759cfc6ea490d7b8dffddc99e0d",
    "som-rs/scripts/hosted_readiness/native_active_controls.py": "eead8e03186f8f55812566a11d5aa837fb5cdb542d2c3de9435da9bad6bb5b32",
    "som-rs/scripts/hosted_readiness/native_qa_authority.py": "024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1",
    "som-rs/scripts/hosted_readiness/native_source_bootstrap.py": "d5c8ceb1a5b0b779662134afdf477fd90cfbc5319cca64e7bb7a8b5fe76ae473",
    "som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py": "7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440",
    "som-rs/scripts/hosted_readiness/typescript_regression_nested_rust.py": "e0b0f1c17f5337aac8ba071d68ae7475fd13617844944cd72f436b0237ab55a6",
    "som-rs/scripts/hosted_readiness/typescript_regression_owner.py": "db90a5f459763359d3438e179dcd7301655d335ef08fcf1d0e194e2ce6d7c493",
    "som-rs/behavior/typescript/host.py": "9a30912b4185fdd5c64dcf0df39c0b6c2cc52a666f0f9405ba23c2c9211c6aef",
    "som-rs/som-targets/qa/check_ts_products.py": "fb52e35d465779a31affad4078e9209050f55ade3803e6931881bc523e2271d1",
    "som-rs/tests/typescript-output-v1/tree_sha.py": "6c9bccd1293aa8629f18539ea89fa0b5bb284dc70a579b13bc86f8d0324fa9a2",
    "som-rs/contracts/typescript-output-v1/toolchain.json": "0875aeb7e60aa72a44bc59c9aef89b971dffc5fc5304813e0b04ec5dc26da618",
    "som-rs/contracts/typescript-output-v1/product-layout.json": "eec61af43adb02583b86ded51478edb57459098f0899062a69c7591591f7ce47",
    "som-rs/contracts/typescript-output-v1/behavior-normalization.json": "cc068fe361cfbcb960a299be67143eab06e7a02930a981c122251ff6afb4e8b0",
    "som-rs/contracts/typescript-output-v1/acceptance-plan.json": "17478469d02bf362e6d394ef9081ff4e842eeab239fbfc89550fba92951d35f5",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def actual(path):
    raw = (REPO / path).read_bytes()
    if path not in REVIEWED_SHA256 or sha(raw) != REVIEWED_SHA256[path]:
        raise AssertionError('same-head host fixture dependency differs: ' + path)
    return raw


# -I needs an exact file dependency. No repository search path is added.
for reviewed_path in REVIEWED_SHA256:
    actual(reviewed_path)
protocol_path = BASE + 'test_native_active_controls.py'
protocol = ModuleType('_som_ts_host_actual_core_protocol')
protocol.__file__, protocol.__package__ = str(REPO / protocol_path), ''
sys.modules[protocol.__name__] = protocol
exec(compile(actual(protocol_path), protocol.__file__, 'exec', dont_inherit=True), protocol.__dict__)


def selector(raw, names):
    nodes = [node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef)
             and node.name in names]
    if len(nodes) != len(names) or {node.name for node in nodes} != set(names):
        raise AssertionError('whole original constructor selector missing or ambiguous')
    return {'kind': 'top-level-functions', 'names': list(names),
            'ast_sha256': sha(ast.dump(ast.Module(body=nodes, type_ignores=[]),
                                      include_attributes=False).encode())}


def bindings(name):
    def view(kind, local):
        return {'kind': 'local-instance', 'local': local, 'type_role': ADAPTER,
                'type_name': kind, 'fields': copy.deepcopy(FIELDS)}
    def export(role, attribute):
        return {'kind': 'role-export', 'role': role, 'attribute': attribute}
    result = {'subprocess': view('_ProcessView', 'process_view'),
              'contextlib': {'kind': 'stdlib', 'module': 'contextlib'},
              'pathlib': {'kind': 'stdlib', 'module': 'pathlib'},
              'signal': {'kind': 'stdlib', 'module': 'signal'},
              '_verify_view_guard': view('_VerifyView', 'verify_view')}
    for key in ('_HostState', '_OwnedMap', '_OwnedList', '_OwnedSet'):
        result['_guard' + key] = export(ADAPTER, key)
    for key in ('configure', 'close'):
        result['_guard_' + key] = export(ADAPTER, key)
    if name in {'typescript_product', 'python_product'}:
        result.update(check_port_allowed=export(HOST, 'check_port_allowed'),
                      wait_http_ready=view('_ReadyView', 'ready_view'),
                      base_hosts=export(HOST, 'base_hosts'))
    if name == 'typescript_product':
        result['NODE_BIN'] = {'kind': 'local-data', 'local': 'native_node', 'type': 'str'}
    elif name == 'python_product':
        result.update(migrate_python_product=view('_MigrateView', 'migrate_view'),
                      _python_path=export(HOST, '_python_path'),
                      os={'kind': 'stdlib', 'module': 'os'})
    else:
        result['ts_host'] = view('_HostView', 'host_view')
        result['time'] = {'kind': 'stdlib', 'module': 'time'}
        for key in ('leased_port', 'run_cmd', 'rust_products', 'evaluate_startup'):
            result[key] = export(CHECKER, key)
    return copy.deepcopy(result)


DRIVER_SOURCE = '''from pathlib import Path
from types import CodeType, FunctionType
import os
import signal
import threading
import time
OWNERS = None
ADMISSION, RUNTIME_ROOT = None, None
def ready_double(proc, port, path='/healthz', log_path=None, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError('toy child exited before its actual owned listener')
        try:
            OWNERS.owned_socket(proc.pid, port)
            return
        except ValueError:
            time.sleep(0.01)
    raise RuntimeError('toy child owned listener timed out')
def cell(value):
    def captured(): return value
    return captured.__closure__[0]
def source_callback(adapter, name, values, defaults=None):
    codes = [code for code in adapter.configure.__code__.co_consts
             if type(code) is CodeType and code.co_name == name]
    if len(codes) != 1:
        raise AssertionError('one actual registered nested callback CodeType required')
    code = codes[0]
    return FunctionType(code, adapter.__dict__, name, defaults,
                        tuple(cell(values[key]) for key in code.co_freevars))
def load_adapter(controls):
    return controls.load_module('ctx.typescript-regression-native-host')
def pg_double():
    return RUNTIME_ROOT / 'pg'
def context_double(controls):
    checker = controls.load_module('ts.checker.original-ts-products')
    context = controls.load_module('runtime.context')
    ctx = object.__new__(context.Context)
    ctx.repo, ctx.helper_revision, ctx.candidate_revision = controls.root, controls.helper_revision, controls.candidate_revision
    ctx.qa_mode, ctx.qa_source_authority = 'native-qa-v1', ADMISSION
    ctx.cache, ctx.modules, ctx.module_lock = {}, {}, threading.RLock()
    ctx.sources = context.SourceBytes(controls.root, controls.helper_revision)
    ctx.work = controls.root / 'work'
    ctx.work.mkdir()
    ctx.data = {'typescript_regression_inputs': {'roots': {
        'node_tools': str(RUNTIME_ROOT / 'node_tools'), 'npm_cache': str(RUNTIME_ROOT / 'npm_cache')}}}
    ctx.verify_postgres = pg_double
    return ctx, checker
def make(controls, python):
    adapter = controls.load_module('ctx.typescript-regression-native-host')
    sockets = controls.load_module('ctx.typescript-native-owned-listeners')
    ctx, checker = context_double(controls)
    host = checker.ts_host
    # Explicit constructor boundary doubles. These are not Context or host
    # configure acceptance, and production exposes no such constructor route.
    state = object.__new__(adapter._HostState)
    state.ctx, state.host, state.checker = ctx, host, checker
    state.stamps, state.roles, state.created, state.entries = [adapter._OwnedMap() for unused in range(4)]
    state.retained, state.scopes, state.pointers = adapter._OwnedSet(), adapter._OwnedList(), adapter._OwnedList()
    state.lease, state.closed, state.active = controls.borrow(), False, False
    state.serial, state.absent, state.fragment_type, state.constructor_exports = 1, object(), None, ()
    state.original_ready, state.original_migrate, state.sockets = ready_double, host.migrate_python_product, sockets
    state.migrate = None
    state.cwd_verified = source_callback(adapter, 'cwd_verified', {'host': host, 'stamps': state.stamps})
    state.verify_process = source_callback(adapter, 'verify_process', {'cwd_verified': state.cwd_verified, 'stamps': state.stamps})
    state.ready = source_callback(adapter, 'wait_http_ready', {'ctx': ctx, 'entries': state.entries,
        'original_ready': ready_double, 'verify_process': state.verify_process, 'sockets': sockets},
        ('/healthz', None, 20.0))
    global OWNERS
    OWNERS = sockets
    ctx.cache[adapter.STATE_CACHE] = [state]
    adapter._STATES[state.serial] = state
    original = host.NODE_BIN
    host.NODE_BIN = python
    state.pointers.append((host, 'NODE_BIN', original, python))
    return adapter, state
def scopes(controls, adapter, state):
    header = (controls.root, controls.helper_revision, controls.candidate_revision,
              'ctx.typescript-regression-native-host', state.serial)
    process_view, ready_view = adapter._ProcessView(*header), adapter._ReadyView(*header)
    migrate_view, host_view, verify_view = adapter._MigrateView(*header), adapter._HostView(*header), adapter._VerifyView(*header)
    native_node = state.host.NODE_BIN
    ts = controls.load_fragment('ctx.typescript-regression-native-host.typescript-product')
    state.scopes.append(ts); state.fragment_type = type(ts)
    py = controls.load_fragment('ctx.typescript-regression-native-host.python-product')
    state.scopes.append(py)
    startup = controls.load_fragment('ctx.typescript-regression-native-host.startup-gate')
    state.scopes.append(startup)
    state.constructor_exports = (('typescript_product', ts, ts.typescript_product),
        ('python_product', py, py.python_product), ('run_startup_gate', startup, startup.run_startup_gate))
    state.active = True
    return ts, py, startup, process_view
class Tracker:
    def __init__(self, adapter, state):
        self.adapter, self.state = adapter, state
    def register(self, pid, role, cwd, command):
        stamp = self.adapter.process_stamp(pid)
        if stamp['pgrp'] != pid or stamp['session'] != pid or stamp['cwd'] != str(cwd):
            raise AssertionError('actual toy new-session/cwd differs')
        self.state.stamps[pid] = stamp
        self.state.roles[pid] = role
        row = {'pid': pid, 'role': role, 'command': command, 'native_owned_start': stamp}
        self.state.entries[pid] = row
        return row
    def stop(self, proc, cwd, record, sig=signal.SIGTERM, timeout=10.0):
        actual = self.adapter.process_stamp(proc.pid)
        before = self.state.stamps[proc.pid]
        if any(actual[key] != before[key] for key in ('pid', 'pgrp', 'session', 'start_ticks')):
            raise AssertionError('toy process ownership changed before actual stop')
        os.killpg(proc.pid, sig)
        code = proc.wait(timeout=timeout)
        record.update(wait_completed=True, wait_exit_code=code)
        return code
'''


class GitTransfer(protocol.ProtocolTransfer):
    def __init__(self, fixture):
        super().__init__(fixture)
        self.revision = fixture.revision


class HostFixture(protocol.Fixture):
    def __init__(self, case, *, partial=False):
        self.partial = partial
        self.path_before = list(sys.path)
        with mock.patch.object(protocol, 'ProtocolTransfer', GitTransfer):
            super().__init__(case, DRIVER_SOURCE)
        self.policy['imports'] = []
        def imported(caller, name, result, names=(), children=None):
            self.policy['imports'].append({'caller': caller, 'name': name, 'level': 0,
                'fromlist': list(names), 'result': result, 'from_roles': children or {}})
        imported(HOST, 'hosts', 'test.host.base')
        imported(HOST, 'tree_sha', 'test.host.tree', ['tree_sha'])
        imported(CHECKER, 'hosts', 'test.host.base')
        imported(CHECKER, 'check_rust_products', 'test.host.rust')
        imported(CHECKER, 'typescript', 'test.host.package', ['host'], {'host': HOST})
        imported(CHECKER, 'typescript', 'test.host.package', ['suite'], {'suite': 'test.host.suite'})
        self.policy['bridges'] = [{'kind': 'context-module', 'role': 'runtime.context', 'function': 'Context.module'}]
        self.policy['context_keys'] = {'typescript_regression_input_inventory': 'ctx.typescript-regression-input-inventory',
                                       'typescript_native_owned_listeners': 'ctx.typescript-native-owned-listeners'}
        self.caller().ADMISSION, self.caller().RUNTIME_ROOT = self.transfer.admission, self.runtime_root
        if partial:
            self.adapter = self.caller().load_adapter(self.controls)
            self.ctx, self.target = self.caller().context_double(self.controls)
            self.state = None
            return
        self.adapter, self.state = self.caller().make(self.controls, str(Path(sys.executable).resolve()))
        self.ts, self.py, self.startup, self.process_view = self.caller().scopes(self.controls, self.adapter, self.state)
        self.stack.callback(self.close_state)

    def add_role(self, role, path, raw, **kwargs):
        row = super().add_role(role, path, raw, **kwargs)
        if role != 'm6.api.producer':
            return row
        self.stack.callback(lambda: sys.path.__setitem__(slice(None), self.path_before))
        super().add_role('runtime.context', CONTEXT, actual(CONTEXT), module='_som_host_fixture_context',
            bindings={'__som_native_active_controls__': {'kind': 'active-controls'}},
            callers=[{'role': 'caller', 'function': 'make'}, {'role': 'caller', 'function': 'context_double'}])
        super().add_role(ADAPTER, ADAPTER_SOURCE, actual(ADAPTER_SOURCE),
            module='_som_full_typescript_regression_native_host',
            bindings={'__som_native_active_controls__': {'kind': 'active-controls'},
                '__som_native_Context__': {'kind': 'role-export', 'role': 'runtime.context', 'attribute': 'Context'},
                '__som_native_SourceBytes__': {'kind': 'role-export', 'role': 'runtime.context', 'attribute': 'SourceBytes'}},
            callers=[{'role': 'caller', 'function': 'make'}, {'role': 'caller', 'function': 'load_adapter'}])
        for target, source, name in ((HOST, HOST_SOURCE, '_som_ts_native_original_typescript_host'),
                (CHECKER, CHECKER_SOURCE, '_som_ts_native_original_ts_products')):
            super().add_role(target, source, actual(source), module=name,
                bindings={'__som_native_active_controls__': {'kind': 'active-controls'}}
                    if target == HOST else None,
                callers=[{'role': 'caller', 'function': 'make'}, {'role': ADAPTER, 'function': '_module'},
                         {'role': 'caller', 'function': 'context_double'},
                         {'role': CHECKER, 'function': '<module>'}])
        super().add_role('ctx.typescript-native-owned-listeners', LISTENERS, actual(LISTENERS),
            module='_som_full_typescript_native_owned_listeners', callers=[{'role': 'caller', 'function': 'make'},
                {'role': ADAPTER, 'function': 'configure'}])
        super().add_role('ctx.typescript-regression-input-inventory', OWNER_SOURCE, actual(OWNER_SOURCE),
            module='_som_full_typescript_regression_input_inventory',
            callers=[{'role': ADAPTER, 'function': 'configure'}])
        super().add_role('test.host.base', BASE + 'synthetic_host_base.py',
            'def listening(pid,port):\n    return bool(owners.owned_socket(pid,port))\n', module='hosts',
            bindings={'owners': {'kind': 'role-module', 'role': 'ctx.typescript-native-owned-listeners'}},
            callers=[{'role': HOST, 'function': '<module>'}, {'role': CHECKER, 'function': '<module>'}])
        super().add_role('test.host.rust', BASE + 'synthetic_host_rust.py', 'VALUE=1\n',
            module='check_rust_products', callers=[{'role': CHECKER, 'function': '<module>'}])
        super().add_role('test.host.package', BASE + 'synthetic_host_typescript/__init__.py', '',
            module='typescript')
        self.rows['test.host.package']['is_package'] = True
        self.rows['test.host.package']['callers'] = [{'role': CHECKER, 'function': '<module>'}]
        super().add_role('test.host.suite', BASE + 'synthetic_host_suite.py', 'VALUE=1\n',
            module='_som_host_fixture_suite', callers=[{'role': CHECKER, 'function': '<module>'}])
        tree_path = 'som-rs/tests/typescript-output-v1/tree_sha.py'
        super().add_role('test.host.tree', tree_path, actual(tree_path), module='tree_sha',
            callers=[{'role': HOST, 'function': '<module>'}])
        for name, target in CONSTRUCTORS.items():
            source = CHECKER_SOURCE if name == 'run_startup_gate' else HOST_SOURCE
            record = super().add_role(target, source, actual(source), kind='fragment',
                module='_som_host_fixture_' + name,
                callers=[{'role': 'caller', 'function': 'scopes'}, {'role': ADAPTER, 'function': 'configure'}],
                bindings=copy.deepcopy(bindings(name)))
            record['selector'] = selector(actual(source), [name])
            if self.partial and name == 'run_startup_gate':
                record['kind'] = 'module'  # Intended third-fragment API refusal, before body execution.
        for leaf in ('toolchain.json', 'product-layout.json', 'behavior-normalization.json', 'acceptance-plan.json'):
            source = 'som-rs/contracts/typescript-output-v1/' + leaf
            target = self.root / source; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(actual(source))
        # Explicit fixture metadata authority and runtime artifacts. This copied
        # Python ELF is not Node26.5 and no tool version command runs here.
        self.runtime_root = self.root / 'fixture-runtime'
        node_root, cache_root = self.runtime_root / 'node_tools', self.runtime_root / 'npm_cache'
        node = node_root / 'bin/node'; cli = node_root / 'lib/node_modules/npm/bin/npm-cli.js'
        node.parent.mkdir(parents=True); cli.parent.mkdir(parents=True); cache_root.mkdir()
        (self.runtime_root / 'pg').mkdir()
        node.write_bytes(Path(sys.executable).resolve().read_bytes()); node.chmod(0o755)
        cli.write_bytes(b'// synthetic npm entry; not executed\n'); cli.chmod(0o644)
        profile_files = {path.relative_to(node_root).as_posix(): {'sha256': sha(path.read_bytes()),
                         'size': path.stat().st_size, 'mode': path.stat().st_mode & 0o777}
                         for path in (node, cli)}
        profile = {'native_typescript_tools': {'node': 'bin/node',
            'npm_cli': 'lib/node_modules/npm/bin/npm-cli.js', 'npm_root': 'lib/node_modules/npm'},
            'roles': {'node_tools': {'files': profile_files}, 'npm_cache': {'files': {}}}}
        artifacts = {'tools': [{'tool': 'node', 'host': 'x86_64-unknown-linux-gnu', 'version': '26.5.0',
                               'executable_sha256': sha(node.read_bytes())}]}
        for path, document in ((BASE + 'inputs/typescript-regression-inputs-v1.json', profile),
                ('som-rs/contracts/native-tools-v1/artifacts.json', artifacts)):
            target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(document, sort_keys=True))
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        def git(*args):
            return subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
                check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        git('init', '--quiet'); git('add', '--', '.')
        git('-c', 'user.name=Host source protocol', '-c', 'user.email=fixture@example.invalid',
            '-c', 'core.hooksPath=/dev/null', 'commit', '--quiet', '-m', 'synthetic native host source protocol')
        self.revision = git('rev-parse', 'HEAD').decode().strip()
        return row

    def close_state(self):
        if not self.state.closed:
            self.adapter.close(self.state.ctx, self.state.checker)
        for row in self.state.created.values():
            proc = row['process']
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)

    def toy(self):
        root = self.state.ctx.work / 'product'; (root / 'src').mkdir(parents=True)
        (root / 'src/main.ts').write_text(
            'import os\nfrom http.server import BaseHTTPRequestHandler,HTTPServer\n'
            'class Handler(BaseHTTPRequestHandler):\n'
            ' def do_GET(self):\n'
            '  self.send_response(200);self.end_headers();self.wfile.write(b"fixture")\n'
            ' def log_message(self,*args):pass\n'
            'host,port=os.environ["SOM_APP_ADDR"].rsplit(":",1)\n'
            'HTTPServer((host,int(port)),Handler).serve_forever()\n')
        with socket.socket() as held:
            held.bind(('127.0.0.1', 0)); port = held.getsockname()[1]
        return root, port


class NativeHostFragmentTests(unittest.TestCase):
    def test_actual_partial_configure_keeps_first_error_and_closes_two_acquired_scopes(self):
        f = HostFixture(self, partial=True)
        observed = []
        previous = sys.gettrace()
        def first_error(frame, event, data):
            if (event == 'exception' and frame.f_code is f.core.require.__code__
                    and type(data[1]) is ValueError and str(data[1]) == 'wrong finite role API'):
                observed.append((data[1], data[2]))
            return first_error
        # Observer only: no code/role registration or source authority. This
        # records the actual Core refusal before configure performs its unwind.
        sys.settrace(first_error)
        try:
            try:
                f.adapter.configure(f.ctx, f.target)
            except ValueError as primary:
                self.assertEqual(str(primary), 'wrong finite role API')
                self.assertTrue(observed)
                self.assertIs(primary, observed[0][0])
                current, found = primary.__traceback__, False
                while current is not None:
                    found |= current is observed[0][1]; current = current.tb_next
                self.assertTrue(found)
            else:
                self.fail('the third fixed fragment API must refuse')
        finally:
            sys.settrace(previous)
        states = f.ctx.cache[f.adapter.STATE_CACHE]
        self.assertEqual(len(states), 1)
        state = states[0]
        self.assertEqual(len(state.scopes), 2)
        self.assertTrue(state.closed); self.assertTrue(state.lease._closed)
        self.assertTrue(all(scope._closed for scope in state.scopes))
        self.assertEqual(state.pointers, [])
        self.assertEqual(state.created, {})
        self.assertEqual(f.adapter._STATES, {})
        self.assertIsNone(f.adapter.close(f.ctx, f.target))
        self.assertFalse(f.domain.closed)
        f.controls.verify_installed()

    def test_actual_whole_fragments_real_popen_owned_http_wait_and_borrow_lifetime(self):
        f = HostFixture(self)
        self.assertIsNone(f.domain.records['runtime.context'].module._ACTIVE)
        for name, scope, function in f.state.constructor_exports:
            record = next(value for value in f.domain.records.values() if value.role == CONSTRUCTORS[name])
            self.assertEqual(record.row['selector'], selector(actual(record.row['source']), [name]))
            body = function.__closure__[0].cell_contents if name != 'run_startup_gate' else function
            self.assertIs(body.__globals__, record.namespace)
            self.assertTrue(any(body.__code__ is code for code in record.codes.values()))
            self.assertFalse(hasattr(scope, 'main'))
        with self.assertRaisesRegex(ValueError, 'actual originating native constructor fragment'):
            f.process_view.Popen([sys.executable, '-c', 'pass'], cwd=f.state.ctx.work,
                start_new_session=True, stdout=subprocess.DEVNULL)
        self.assertEqual(f.state.created, {})
        root, port = f.toy()
        tracker = f.caller().Tracker(f.adapter, f.state)
        with f.ts.typescript_product(root, 'postgresql://postgres@127.0.0.1:31119/fixture', port,
                                     {'PATH': '/usr/bin:/bin'}, tracker) as url:
            self.assertEqual(url, f'http://127.0.0.1:{port}')
            self.assertFalse(f.ts._closed); self.assertFalse(f.state.lease._closed)
            pid, held = next(iter(f.state.created.items()))
            self.assertIs(held['process'], f.state.created[pid]['process'])
            self.assertEqual(held['requested_argv'], [str(Path(sys.executable).resolve()), 'src/main.ts'])
            self.assertIs(held['actual_argv'], held['process'].args)
            self.assertEqual(held['cwd'], str(root))
            self.assertTrue(held['new_session'])
            self.assertTrue(held['constructor_io']['stdout'].closed)
            self.assertEqual(f.state.entries[pid]['native_readiness']['response_sha256'], sha(b'fixture'))
            f.controls.verify_installed()  # Mutable owned records are data, not changed callback bindings.
        proc = held['process']
        self.assertIsInstance(proc.returncode, int)
        self.assertTrue(f.state.entries[pid]['wait_completed'])
        self.assertEqual(f.state.entries[pid]['wait_exit_code'], proc.returncode)
        self.assertEqual(f.adapter.port_listeners(port), [])
        f.controls.verify_installed()
        f.adapter.close(f.state.ctx, f.state.checker)
        self.assertTrue(f.state.closed); self.assertTrue(f.state.lease._closed)
        self.assertTrue(all(scope._closed for scope in (f.ts, f.py, f.startup)))
        self.assertFalse(f.domain.closed)
        f.controls.verify_installed()

    def test_safe_no_acquisition_and_foreign_provider_hooks_refuse_before_execution(self):
        f = HostFixture(self)
        calls = []
        class Fake:
            def __getattribute__(self, key):
                calls.append(key)
                raise AssertionError('foreign provider hook must not run')
        with self.assertRaisesRegex(ValueError, 'source-owned native TypeScript provider'):
            f.adapter._provider(Fake())
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(ValueError, 'only actual configure'):
            f.adapter._HostState(f.state.ctx, f.state.checker, f.state.host,
                f.state.stamps, f.state.roles, f.state.created, f.state.entries, f.state.retained)
        f.adapter.close(f.state.ctx, f.state.checker)
        self.assertIsNone(f.adapter.close(f.state.ctx, f.state.checker))
        f.controls.verify_installed()

    def test_later_owner_and_failed_actual_scope_close_keep_primary_trace_and_secondary(self):
        f = HostFixture(self)
        original_node = f.state.pointers[0][2]
        later = object()
        f.state.host.NODE_BIN = later
        record = next(value for value in f.domain.records.values()
                      if value.role == CONSTRUCTORS['typescript_product'])
        foreign = ModuleType('foreign-later-scope-owner')
        sys.modules[record.name] = foreign
        try:
            try:
                raise ValueError('original host body failure')
            except ValueError as primary:
                trace = primary.__traceback__
                try:
                    try:
                        raise primary
                    finally:
                        f.adapter.close(f.state.ctx, f.state.checker)
                except ValueError as actual_error:
                    self.assertIs(actual_error, primary)
                    current, found = actual_error.__traceback__, False
                    while current is not None:
                        found |= current is trace; current = current.tb_next
                    self.assertTrue(found)
                    failures = actual_error.__som_native_host_secondary__
                    pending, messages, seen = list(failures), [], set()
                    while pending:
                        error = pending.pop(0)
                        if id(error) in seen:
                            continue
                        seen.add(id(error)); messages.append(str(error))
                        pending.extend(getattr(error, '__som_native_host_secondary__', ()))
                    text = '\n'.join(messages)
                    self.assertIn('namespace changed', text)
                    self.assertIn('later owner: NODE_BIN', text)
                    self.assertIn('foreign module replacement kept', text)
                else:
                    self.fail('the original primary must propagate')
            self.assertIs(f.state.host.NODE_BIN, later)
            self.assertIs(sys.modules[record.name], foreign)
            self.assertTrue(f.state.closed); self.assertTrue(f.state.lease._closed)
            self.assertFalse(any(value.role in set(CONSTRUCTORS.values()) for value in f.domain.records.values()))
        finally:
            f.state.host.NODE_BIN = original_node
            if sys.modules.get(record.name) is foreign:
                del sys.modules[record.name]


if __name__ == '__main__':
    unittest.main()
