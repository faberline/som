"""GitHub-only fixtures for real child cleanup and schema capture fields."""
from __future__ import annotations
import ast
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest

HERE = Path(__file__).absolute().parent


def load(relative):
    path = HERE / relative; result = ModuleType('_som_ts_lifecycle_' + path.stem)
    result.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), result.__dict__)
    return result


def original_nested(name, scope):
    path = HERE / 'typescript_native_host.py'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != '74811b731ca0d7c26f72b5e914742f16c4488ce571370caaa37e07f89d05cbb2':
        raise AssertionError('exact reviewed native host source required')
    tree = ast.parse(raw)
    configure = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'configure')
    wanted = ('_RegistrationBase', 'NativeTracker') if name == 'NativeTracker' else (name,)
    def lexical(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    yield node
                # Do not enter a different function or class scope.
            else:
                for key, value in ast.iter_fields(node):
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, ast.stmt):
                                yield from lexical([item])
                            elif isinstance(item, ast.ExceptHandler):
                                yield from lexical(item.body)
    nodes = [node for node in lexical(configure.body) if node.name in wanted]
    if len(nodes) != len(wanted) or {node.name for node in nodes} != set(wanted):
        raise AssertionError('exact complete current lifecycle nodes required')
    selected = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                                *nodes], type_ignores=[])
    ast.fix_missing_locations(selected)
    exec(compile(selected, str(path), 'exec', dont_inherit=True), scope)
    return scope[name]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.native = load('typescript_native_host.py')

    def tearDown(self):
        self.tmp.cleanup()

    def scope(self, created=None):
        class Tracker:
            def stop(self, *args, **kwargs):
                raise RuntimeError('first owner error')
        return {'original_tracker': Tracker, 'host': SimpleNamespace(forbidden_pids=lambda: set()),
            'ctx': SimpleNamespace(work=self.root), 'created': created or {}, 'stamps': {}, 'roles': {},
            'Path': Path, 'os': os, 'signal': signal, 'subprocess': subprocess, 'json': json,
            'base64': base64, 'require': self.native.require, 'process_stamp': self.native.process_stamp,
            'pg': self.root, 'node': Path(sys.executable).resolve(), 'file_sha': self.native.file_sha,
            'cwd_verified': lambda *args: (False, None)}

    def test_real_new_session_is_waited_when_registration_fails(self):
        command = [sys.executable, '-I', '-S', '-B', '-c', 'import time;time.sleep(30)']
        proc = subprocess.Popen(command, cwd=self.root, start_new_session=True,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        primary = RuntimeError('first stamp failure')
        scope = self.scope({proc.pid: {'process': proc, 'requested_argv': command,
                                      'cwd': str(self.root), 'new_session': True}})
        def failed(pid):
            raise primary
        scope['process_stamp'] = failed
        tracker = original_nested('NativeTracker', scope)()
        try:
            with self.assertRaises(RuntimeError) as caught:
                tracker.register(proc.pid, 'typescript_product', self.root, command)
            self.assertIs(caught.exception, primary)
            proof = primary.__som_typescript_registration_cleanup__
            self.assertTrue(proof['wait_attempted']); self.assertTrue(proof['wait_completed'])
            self.assertEqual(proof['signals'], ['SIGTERM']); self.assertIsNotNone(proc.returncode)
            self.assertEqual(json.loads((self.root / 'typescript-regression/native-registration-failures' /
                                         (str(proc.pid) + '.json')).read_bytes()), proof)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close(); proc.stderr.close()

    def test_tracker_always_attempts_wait_and_keeps_first_owner_error(self):
        scope = self.scope(); calls = []
        proc = SimpleNamespace(wait=lambda **kwargs: calls.append(kwargs) or -15)
        entry = {}
        with self.assertRaisesRegex(RuntimeError, 'first owner error'):
            original_nested('NativeTracker', scope)().stop(proc, self.root, entry)
        self.assertEqual(len(calls), 1); self.assertTrue(entry['wait_completed'])

    def test_secondary_wait_error_does_not_replace_owner_error(self):
        scope = self.scope(); entry = {}
        def failed(**kwargs):
            raise ValueError('secondary wait failure')
        with self.assertRaisesRegex(RuntimeError, 'first owner error'):
            original_nested('NativeTracker', scope)().stop(SimpleNamespace(wait=failed), self.root, entry)
        self.assertFalse(entry['wait_completed'])
        self.assertEqual(entry['native_cleanup_failures'][0]['reason'], 'secondary wait failure')

    def test_pg_stamp_failure_still_attempts_final_wait(self):
        data = self.root / 'pgdata'; data.mkdir()
        primary, waits = RuntimeError('first registration failure'), []
        proc = SimpleNamespace(pid=12345, returncode=None, wait=lambda **kwargs: waits.append(kwargs) or -15)
        def register(*args): raise primary
        def failed_stamp(pid): raise ValueError('secondary stamp failure')
        scope = {'contextmanager': contextmanager, 'host': SimpleNamespace(check_port_allowed=lambda value: None),
            'Path': Path, 'os': os, 'subprocess': SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=0, stderr=''),
                Popen=lambda *a, **k: proc, DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT),
            'registered_popen': lambda *a, **k: proc, 'pg': self.root,
            'require': self.native.require, 'process_stamp': failed_stamp, 'json': json,
            'time': time, 'signal': signal}
        cluster = original_nested('pg_cluster', scope)
        with self.assertRaises(RuntimeError) as caught:
            with cluster(self.root, 34567, SimpleNamespace(register=register)):
                self.fail('registration must refuse before yielding')
        self.assertIs(caught.exception, primary); self.assertEqual(len(waits), 1)
        saved = json.loads((self.root / 'native-postgres-cleanup.json').read_bytes())
        self.assertTrue(saved['wait_completed'])
        self.assertEqual(saved['cleanup_errors'][0]['reason'], 'secondary stamp failure')


class SchemaCaptureTests(unittest.TestCase):
    def test_original_schema_sees_each_real_query_result_once(self):
        helper = load('typescript_regression_schema_capture.py')
        queries = []
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, params=None):
                queries.append((sql, params))
                return SimpleNamespace(fetchall=lambda: [('items', None, 7, True, ['a'])])
        rust = SimpleNamespace(psycopg=SimpleNamespace(connect=lambda url: Connection()))
        def original(url, excluded):
            with rust.psycopg.connect(url) as conn:
                return {'rows': list(conn.execute('SELECT original', ('items',)))}
        rust.schema = original
        checker = SimpleNamespace(rust_products=rust, __som_native_process_roles__={12345: 'postgres'},
                                  __som_native_verify_process__=lambda pid: {'pid': pid})
        with tempfile.TemporaryDirectory() as temporary:
            rows = helper.install(checker, Path(temporary))
            result = rust.schema('postgresql://postgres@127.0.0.1:34567/items', ())
            self.assertEqual(queries, [('SELECT original', ('items',))])
            self.assertEqual(result, {'rows': [('items', None, 7, True, ['a'])]})
            self.assertTrue(rows[0]['complete']); self.assertEqual(rows[0]['pg_owner_pid'], 12345)
            self.assertEqual(rows[0]['queries'][0]['params'],
                             {'type': 'tuple', 'value': [{'type': 'str', 'value': 'items'}]})
            self.assertEqual(rows[0]['queries'][0]['rows']['type'], 'list')

    def test_unknown_sql_type_is_refused(self):
        helper = load('typescript_regression_schema_capture.py')
        with self.assertRaisesRegex(ValueError, 'unadmitted SQL type'):
            helper.typed(1.5)


if __name__ == '__main__':
    unittest.main()
