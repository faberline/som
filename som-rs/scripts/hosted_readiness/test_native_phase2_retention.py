"""GitHub-only native retention fixtures, with explicit business/runtime doubles.

Real lifecycle methods and inventories operate on physical synthetic files.
Selected real checker functions are compiled from exact Git source. No fixture
qualifies a product, process wait, database, wheel closure or source authority.
"""
from __future__ import annotations
import ast
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise RuntimeError('GitHub Linux source fixtures only')
REPO = Path(__file__).absolute().parents[3]
BASE = 'som-rs/scripts/hosted_readiness/'
PHASE2 = 'som-rs/behavior/phase2/check_phase2.py'
GATES = 'som-rs/som-targets/qa/check_phase2_products.py'


def raw_source(relative):
    raw = (REPO / relative).read_bytes()
    assert raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
        'cat-file', 'blob', os.environ['HELPER_REVISION'] + ':' + relative])
    return raw


def subject(name, relative):
    module = ModuleType(name); module.__file__ = str(REPO / relative)
    exec(compile(raw_source(relative), module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return module


def functions(relative, names, namespace):
    tree = ast.parse(raw_source(relative))
    chosen = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in chosen} == set(names)
    exec(compile(ast.Module(body=chosen, type_ignores=[]), str(REPO / relative), 'exec'), namespace)


c = subject('phase2_retention_context', BASE + 'product_runtime/full_product_context.py')
w = subject('phase2_retention_inventory', BASE + 'product_python/produce_wheels.py')


class NativePhase2Retention(unittest.TestCase):
    def context(self, root):
        ctx = c.Context.__new__(c.Context)
        ctx.work = root / 'work'; ctx.work.mkdir()
        ctx.qa_mode = 'native-qa-v1'
        ctx.qa_import_controls = SimpleNamespace(verify_installed=lambda: None)
        ctx.helper_revision = ctx.candidate_revision = '1' * 40
        ctx.cache = {}; ctx.checked = []
        ctx.module = lambda *args: w
        ctx.python_payload = lambda executable: ctx.checked.append(str(executable))
        ctx.runtime = lambda: {'tools': {'python': Path(sys.executable).resolve()}}
        return ctx

    def environment(self, ctx, product):
        venv = product / '.venv'; (venv / 'bin').mkdir(parents=True)
        (venv / 'bin/python').symlink_to(Path(sys.executable).resolve())
        site = venv / 'lib/python3.12/site-packages'; site.mkdir(parents=True)
        (site / 'payload.py').write_bytes(b'VALUE = 42\n')
        (site / 'ordinary.dist-info').mkdir()
        (site / 'ordinary.dist-info/INSTALLER').write_bytes(b'uv')
        key = c.digest(str(product).encode())
        cache = ctx.work / 'product-caches' / key; cache.mkdir(parents=True)
        (cache / 'original.raw').write_bytes(b'original synthetic cache bytes')
        evidence = ctx.work / 'product-environments' / key; evidence.mkdir(parents=True)
        before = c.canonical(w.installed_inventory(venv, ctx.work)) + b'\n'
        (evidence / 'installed-before.json').write_bytes(before)
        ctx.cache.setdefault('product_environments', {})[str(product)] = {
            'python': str(venv / 'bin/python'), 'environment': {'UV_CACHE_DIR': str(cache)}}
        return SimpleNamespace(venv=venv, site=site, cache=cache, evidence=evidence, before=before)

    def phase2(self, ctx):
        namespace = {'os': os, 'tempfile': tempfile}
        functions(PHASE2, ['retained_scratch', 'current_product_path'], namespace)
        namespace['native_retention_context'] = lambda: ctx
        return SimpleNamespace(**namespace)

    def test_actual_snapshot_keeps_original_before_after_cache_and_unknown_business_outcome(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); product = ctx.work / 'product'; product.mkdir()
            env = self.environment(ctx, product)
            result = ctx.retain_native_environments(product, 'phase2-fixture')
            record = json.loads(Path(result['path']).read_bytes())
            self.assertTrue(record['complete']); self.assertIsNone(record['product_gates_passed'])
            self.assertFalse(record['product_acceptance_passed'])
            self.assertEqual(record['ordinary_installer_scope_independent_audit'], 'unrun')
            row = record['environments'][0]; saved = Path(result['path']).parent / row['snapshot']
            self.assertEqual((saved / 'installed-before.json').read_bytes(), env.before)
            self.assertEqual((saved / 'installed-after.json').read_bytes(), env.before)
            self.assertEqual((env.evidence / 'installed-before.json').read_bytes(), env.before)
            self.assertEqual((env.cache / 'original.raw').read_bytes(), b'original synthetic cache bytes')
            self.assertTrue(env.venv.is_dir()); self.assertEqual(len(ctx.checked), 1)

    def test_changed_actual_installed_bytes_are_saved_then_refused_without_removal(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); product = ctx.work / 'product'; product.mkdir()
            env = self.environment(ctx, product); (env.site / 'payload.py').write_bytes(b'changed\n')
            with self.assertRaisesRegex(c.Refusal, 'changed before retention'):
                ctx.retain_native_environments(product, 'phase2-fixture')
            records = list((ctx.work / 'native-retention').glob('*/retention.json'))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_bytes()); self.assertFalse(record['complete'])
            self.assertFalse(record['environments'][0]['same_installed_files_before_after'])
            self.assertTrue(env.venv.is_dir()); self.assertTrue(env.cache.is_dir())

    def test_repeat_snapshots_append_without_rewriting_any_earlier_raw_record(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); product = ctx.work / 'product'; product.mkdir()
            self.environment(ctx, product)
            first = ctx.retain_native_environments(product, 'phase2-fixture')
            original = {str(p): p.read_bytes() for p in Path(first['path']).parent.rglob('*') if p.is_file()}
            second = ctx.retain_native_environments(product, 'phase2-fixture')
            self.assertNotEqual(first['path'], second['path'])
            self.assertTrue(all(Path(name).read_bytes() == raw for name, raw in original.items()))

    def test_partial_unregistered_environment_keeps_actual_after_bytes_and_refuses_missing_before(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); product = ctx.work / 'partial'; product.mkdir()
            venv = product / '.venv'; venv.mkdir(); (venv / 'partial.raw').write_bytes(b'original partial install')
            with self.assertRaisesRegex(c.Refusal, 'lacks original installed-before'):
                ctx.retain_native_environments(product, 'phase2-fixture')
            path = next((ctx.work / 'native-retention').glob('*/retention.json'))
            record = json.loads(path.read_bytes()); self.assertFalse(record['complete'])
            row = record['unregistered_environments'][0]; self.assertIsNone(row['original_before'])
            after = json.loads((path.parent / row['snapshot'] / 'installed-after.json').read_bytes())
            self.assertEqual(after, w.installed_inventory(venv, ctx.work))
            self.assertEqual((venv / 'partial.raw').read_bytes(), b'original partial install')

    def test_repeated_root_gets_new_attempt_and_keeps_valid_old_environment_and_cache(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); nominal = ctx.work / 'gate/python'
            first = ctx.new_native_product_root(nominal, 'phase2-storage'); first.mkdir()
            env = self.environment(ctx, first); (first / 'original.py').write_bytes(b'original')
            second = ctx.new_native_product_root(nominal, 'phase2-storage')
            self.assertNotEqual(first, second); self.assertFalse(second.exists())
            self.assertEqual(ctx.native_product_path(nominal), second)
            self.assertEqual((first / 'original.py').read_bytes(), b'original')
            self.assertTrue(env.venv.is_dir()); self.assertTrue(env.cache.is_dir())
            self.assertIn(str(first), ctx.cache['product_environments'])
            self.assertNotIn(str(second), ctx.cache['product_environments'])
            self.assertNotEqual(c.digest(str(first).encode()), c.digest(str(second).encode()))
            entries = [json.loads(p.read_bytes()) for p in (ctx.work / 'native-product-roots').glob('*/root.json')]
            self.assertEqual(len(entries), 2)
            self.assertTrue(all(not entry['previous_tree_removed'] and not entry['original_paths_rewritten'] for entry in entries))

    def test_missing_prior_cached_root_cannot_be_reused_as_fresh_authority(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); nominal = ctx.work / 'missing'
            ctx.cache['product_environments'] = {str(nominal): {'python': 'unrun'}}
            with self.assertRaisesRegex(c.Refusal, 'missing retained environment'):
                ctx.new_native_product_root(nominal, 'phase2-storage')
            self.assertFalse(nominal.exists())

    def test_foreign_scope_and_linked_cache_parent_are_refused_without_following_alias(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); ctx = self.context(root)
            outside = root / 'outside'; outside.mkdir(); (outside / 'protected').write_bytes(b'keep')
            with self.assertRaises(c.Refusal): ctx.native_owned_directory(outside)
            link = ctx.work / 'linked'; link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(c.Refusal): ctx.native_owned_directory(link / 'child', allow_missing=True)
            self.assertEqual((outside / 'protected').read_bytes(), b'keep')

    def test_native_suite_scratch_retains_data_after_inner_cleanup_and_original_error(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); parent = ctx.work / 'fixtures'; parent.mkdir()
            events = []; original = RuntimeError('synthetic checker failure')
            @contextmanager
            def closed_cluster():
                try: yield
                finally: events.append('cluster-closed')
            with self.assertRaises(RuntimeError) as caught:
                with ctx.native_phase2_scratch(parent) as scratch:
                    product = Path(scratch) / 'products/one'; product.mkdir(parents=True)
                    env = self.environment(ctx, product)
                    ctx.python_payload = lambda executable: self.assertEqual(events, ['cluster-closed'])
                    with closed_cluster(): raise original
            self.assertIs(caught.exception, original)
            self.assertTrue(product.is_dir()); self.assertTrue(env.venv.is_dir()); self.assertTrue(env.cache.is_dir())
            self.assertTrue(list((ctx.work / 'native-retention').glob('*/retention.json')))

    def test_ordinary_scratch_still_uses_real_temporary_directory_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            parent = Path(d).resolve(); phase2 = self.phase2(None)
            with phase2.retained_scratch(parent) as scratch:
                root = Path(scratch); (root / 'ordinary.raw').write_bytes(b'ordinary')
            self.assertFalse(root.exists())

    def test_real_fixture_case_retains_native_product_and_binary_but_ordinary_cleanup_remains(self):
        for native in (True, False):
            with self.subTest(native=native), tempfile.TemporaryDirectory() as d:
                ctx = self.context(Path(d).resolve()); events = []
                class Host:
                    def __init__(self, root, *args): self.root = root; self.surface_checks = []
                host_module = ModuleType('phase2_host')
                host_module.PythonProduct = host_module.RustProduct = Host
                host_module.register = lambda *args: events.append('registered')
                host_module.unregister = lambda *args: events.append('unregistered')
                checker_module = ModuleType('verify_composed_services')
                def verify(prompt, product, fixture, standard):
                    self.environment(ctx, product); (product / 'som_app').write_bytes(b'actual synthetic binary data')
                    return {'status': 'passed'}
                checker_module.verify_all = verify
                namespace = {'pathlib': __import__('pathlib'), 'time': time, 'threading': threading, 'os': os,
                    'shutil': shutil, 'traceback': traceback, 'native_retention_context': lambda: ctx if native else None,
                    'key': lambda name: name, 'public_row': lambda case: {'id': case['id']},
                    'load': lambda case: ({}, {'service.py': 'original'}),
                    'rust_files': lambda url, case, prompt, row: {'service.py': 'original'},
                    'write_product': lambda root, files: [(root / name).write_text(body) for name, body in files.items()],
                    'product_files': lambda root: {'service.py'}, 'finish': lambda out, case, row, outcome, started: row}
                functions(PHASE2, ['fixture_case'], namespace)
                with mock.patch.dict(sys.modules, {'phase2_host': host_module, 'verify_composed_services': checker_module}):
                    row = namespace['fixture_case'](ctx.work / 'out', {'id': 'one', 'checker': 'verify_all', 'standard': True}, {}, 'rust')
                product = Path(row.get('native_product_root', ctx.work / 'out/work/one/product'))
                self.assertTrue(row['passed']); self.assertEqual(events, ['registered', 'unregistered'])
                self.assertEqual((product / '.venv').exists(), native)
                self.assertEqual((product / 'som_app').exists(), native)

    def test_real_storage_method_keeps_both_database_closes_and_fresh_repeated_python_roots(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); work = ctx.work / 'gate'; work.mkdir()
            events = []; roots = []
            class Backend:
                def __init__(self, prompt, name, fixture): self.name = name; self.env = {}; self.db = None
                def create(self, run): events.append(('create', self.name))
                def close(self, run): events.append(('close', self.name))
            class Product:
                def __init__(self, root): self.root = root
                def prepare(self, env): roots.append(self.root); self.outer.environment(ctx, self.root)
                def migration_command(self, *args): return ['synthetic-migration-not-executed']
            Product.outer = self
            modules = {name: ModuleType(name) for name in ('business_database_checks', 'phase2_host', 'verify_related')}
            modules['business_database_checks'].Mongo = modules['business_database_checks'].Postgres = Backend
            modules['phase2_host'].PythonProduct = Product
            modules['verify_related'].run = lambda *args, **kwargs: None
            tree = ast.parse(raw_source(GATES)); gate = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Gate')
            storage = next(n for n in gate.body if isinstance(n, ast.FunctionDef) and n.name == 'storage')
            phase2 = SimpleNamespace(native_retention_context=lambda: ctx,
                write_product=lambda root, files: [(root / name).write_text(body) for name, body in files.items()])
            namespace = {'uuid': __import__('uuid'), 'phase2': phase2, 'products': SimpleNamespace(schema=lambda *args: {'table': []}),
                'RUST_RECORDS': (), 'PYTHON_RECORDS': (), 'hashlib': __import__('hashlib'), 'json': json, 'shutil': shutil}
            exec(compile(ast.Module(body=[storage], type_ignores=[]), str(REPO / GATES), 'exec'), namespace)
            owner = SimpleNamespace(work=work, prompt={'capabilities': {'database': 'postgresql'}},
                fixture={}, python_files={'service.py': 'original'})
            with mock.patch.dict(sys.modules, modules):
                for _ in range(2):
                    self.assertTrue(namespace['storage'](owner, SimpleNamespace(binary='not-executed', root=work), {})['passed'])
            self.assertEqual(len(roots), 2); self.assertNotEqual(*roots)
            self.assertTrue(all((root / '.venv').is_dir() for root in roots))
            self.assertEqual(sum(kind == 'close' for kind, _ in events), 4)
            self.assertEqual(ctx.native_product_path(work / 'python'), roots[-1])

    def test_gate_finish_reads_mapped_actual_product_without_rewriting_the_old_lock(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); nominal = ctx.work / 'product'
            first = ctx.new_native_product_root(nominal, 'phase2-fixture'); first.mkdir()
            (first / 'Cargo.lock').write_text('old original lock')
            actual = ctx.new_native_product_root(nominal, 'phase2-fixture'); actual.mkdir()
            (actual / 'Cargo.lock').write_text('new original lock')
            tree = ast.parse(raw_source(GATES)); gate = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Gate')
            finish = next(n for n in gate.body if isinstance(n, ast.FunctionDef) and n.name == 'finish')
            namespace = {'phase2': self.phase2(ctx), 'json': json}
            exec(compile(ast.Module(body=[finish], type_ignores=[]), str(REPO / GATES), 'exec'), namespace)
            owner = SimpleNamespace(test_logs=[], gates={})
            namespace['finish'](owner, nominal, {'Cargo.lock': 'new original lock'})
            self.assertTrue(owner.gates['lock_unchanged']['passed'])
            self.assertFalse(owner.gates['cargo_test']['passed'])  # No business tests ran in this fixture.
            self.assertEqual((first / 'Cargo.lock').read_text(), 'old original lock')

    def test_current_refusal_returns_before_any_product_directory_or_retention_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve())
            host = ModuleType('phase2_host')
            for name in ('PythonProduct', 'RustProduct', 'register', 'unregister'):
                setattr(host, name, lambda *args: self.fail('refusal cannot create or register a product'))
            full = ModuleType('full_product_context')
            full.current_refusal = lambda *args: {'product_executed': False, 'fixture_role': 'synthetic refusal routing only'}
            namespace = {'pathlib': __import__('pathlib'), 'time': time, 'os': os,
                'public_row': lambda case: {'id': case['id']}, 'key': lambda name: name,
                'finish': lambda out, case, row, outcome, started: row,
                'load': lambda case: self.fail('refusal cannot load a product')}
            functions(PHASE2, ['fixture_case'], namespace)
            with mock.patch.dict(sys.modules, {'phase2_host': host, 'full_product_context': full}), \
                    mock.patch.dict(os.environ, {'SOM_FULL_PRODUCT_CONTEXT': 'synthetic-not-a-qualified-context'}):
                row = namespace['fixture_case'](ctx.work / 'out', {'id': 'synthetic-refusal'}, {}, 'python')
            self.assertFalse(row['product_executed'])
            self.assertFalse((ctx.work / 'out').exists())

    def test_retention_failure_cannot_replace_original_suite_error_or_claim_complete(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); parent = ctx.work / 'fixtures'; parent.mkdir()
            original = RuntimeError('original synthetic suite error')
            with self.assertRaises(RuntimeError) as caught:
                with ctx.native_phase2_scratch(parent) as scratch:
                    product = Path(scratch) / 'product'; product.mkdir()
                    env = self.environment(ctx, product)
                    (env.site / 'payload.py').write_bytes(b'changed actual bytes')
                    raise original
            self.assertIs(caught.exception, original)
            self.assertEqual(original.__som_native_retention_error__['type'], 'Refusal')
            path = next((ctx.work / 'native-retention').glob('*/retention.json'))
            self.assertFalse(json.loads(path.read_bytes())['complete'])
            self.assertTrue(env.venv.is_dir()); self.assertTrue(env.cache.is_dir())


    def test_unreadable_parent_hiding_partial_environment_refuses_incomplete_discovery(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); scope = ctx.work / 'scope'; scope.mkdir()
            blocked = scope / 'unreadable-parent'; venv = blocked / 'product/.venv'
            venv.mkdir(parents=True); payload = venv / 'partial.raw'; payload.write_bytes(b'preserve unreadable partial data')
            original_mode = blocked.stat().st_mode & 0o777
            blocked.chmod(0)
            try:
                self.assertFalse(os.access(blocked, os.R_OK | os.X_OK), 'permission negative control requires an unprivileged runner')
                with self.assertRaises(PermissionError): list(blocked.iterdir())
                with self.assertRaisesRegex(c.Refusal, 'not readable/searchable'):
                    ctx.retain_native_environments(scope, 'phase2-fixture')
                path = next((ctx.work / 'native-retention').glob('*/retention.json'))
                record = json.loads(path.read_bytes())
                self.assertFalse(record['complete']); self.assertFalse(record['discovery_complete'])
                self.assertEqual(record['first_error']['type'], 'Refusal')
                self.assertIn(str(blocked), record['first_error']['message'])
                self.assertIn('not readable/searchable', record['first_error']['traceback'])
            finally:
                blocked.chmod(original_mode)
            self.assertEqual(payload.read_bytes(), b'preserve unreadable partial data')
            self.assertTrue(venv.is_dir())

    def test_actual_walk_scandir_error_is_raised_and_retained_instead_of_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); scope = ctx.work / 'scope'; scope.mkdir()
            nested = scope / 'nested'; (nested / 'product/.venv').mkdir(parents=True)
            (nested / 'product/.venv/partial.raw').write_bytes(b'original')
            original = PermissionError(13, 'synthetic scan race after readable-directory check', str(nested))
            real_scandir = os.scandir
            def failing_scandir(path):
                if Path(path) == nested: raise original
                return real_scandir(path)
            with mock.patch.object(c.os, 'scandir', side_effect=failing_scandir), self.assertRaises(PermissionError) as caught:
                ctx.retain_native_environments(scope, 'phase2-fixture')
            self.assertIs(caught.exception, original)
            path = next((ctx.work / 'native-retention').glob('*/retention.json'))
            record = json.loads(path.read_bytes())
            self.assertFalse(record['complete']); self.assertFalse(record['discovery_complete'])
            self.assertEqual(record['first_error']['type'], 'PermissionError')
            self.assertEqual(record['first_error']['message'], str(original))
            self.assertEqual((nested / 'product/.venv/partial.raw').read_bytes(), b'original')

    def test_census_keeps_exact_internal_uv_lib64_alias_opaque_and_refuses_other_directory_alias(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self.context(Path(d).resolve()); product = ctx.work / 'product'; product.mkdir()
            env = self.environment(ctx, product)
            (env.venv / 'lib64').symlink_to('lib', target_is_directory=True)
            (env.evidence / 'installed-before.json').write_bytes(c.canonical(w.installed_inventory(env.venv, ctx.work)) + b'\n')
            result = ctx.retain_native_environments(product, 'phase2-fixture')
            record = json.loads(Path(result['path']).read_bytes())
            self.assertTrue(record['complete']); self.assertTrue(record['discovery_complete'])
            self.assertEqual(record['discovery_opaque_directory_aliases'], [{
                'path': str(env.venv / 'lib64'), 'target': 'lib', 'physical_target': str(env.venv / 'lib'),
                'followed': False, 'execution_authority_granted': False}])
            (product / 'foreign-directory-alias').symlink_to(env.venv / 'lib', target_is_directory=True)
            with self.assertRaisesRegex(c.Refusal, 'not physical'):
                ctx.retain_native_environments(product, 'phase2-fixture')
            self.assertEqual(os.readlink(env.venv / 'lib64'), 'lib')

    def test_real_storage_and_owner_capture_keep_primary_setup_migration_and_retention_errors_distinct(self):
        for fail in ('prepare', 'migration', 'no-primary'):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as d:
                ctx = self.context(Path(d).resolve()); work = ctx.work / 'gate'; work.mkdir()
                original = RuntimeError('exact original synthetic storage failure at ' + fail)
                events, roots, caught_errors = [], [], []
                class Backend:
                    def __init__(self, prompt, name, fixture): self.name = name; self.env = {}; self.db = None
                    def create(self, run): events.append(('create', self.name))
                    def close(self, run): events.append(('close', self.name))
                class Product:
                    def __init__(self, root): self.root = root; roots.append(root)
                    def prepare(self, env):
                        partial = self.root / '.venv'; partial.mkdir()
                        (partial / 'partial.raw').write_bytes(b'actual synthetic partial install')
                        if fail == 'prepare': raise original
                    def migration_command(self, *args): return ['synthetic-python-migration']
                def run(command, *args, **kwargs):
                    if fail == 'migration' and command == ['synthetic-python-migration']: raise original
                modules = {name: ModuleType(name) for name in ('business_database_checks', 'phase2_host', 'verify_related')}
                modules['business_database_checks'].Mongo = modules['business_database_checks'].Postgres = Backend
                modules['phase2_host'].PythonProduct = Product; modules['verify_related'].run = run
                tree = ast.parse(raw_source(GATES)); gate = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Gate')
                methods = [n for n in gate.body if isinstance(n, ast.FunctionDef) and n.name in {'storage', 'after_build'}]
                namespace = {'uuid': __import__('uuid'), 'traceback': traceback, 'pathlib': __import__('pathlib'),
                    'phase2': SimpleNamespace(native_retention_context=lambda: ctx,
                        write_product=lambda root, files: [(root / name).write_text(body) for name, body in files.items()]),
                    'products': SimpleNamespace(schema=lambda *args: {'table': []}), 'RUST_RECORDS': (), 'PYTHON_RECORDS': (),
                    'hashlib': __import__('hashlib'), 'json': json, 'shutil': shutil}
                functions(GATES, ['outcome'], namespace)
                exec(compile(ast.Module(body=methods, type_ignores=[]), str(REPO / GATES), 'exec'), namespace)
                owner = SimpleNamespace(work=work, prompt={'capabilities': {'database': 'postgresql'}}, fixture={},
                    python_files={'service.py': 'original'}, gates={},
                    missing_setting=lambda *args: {'passed': True, 'fixture_role': 'business outcome double only'})
                def actual_storage(host, env):
                    try: return namespace['storage'](owner, host, env)
                    except BaseException as error:
                        caught_errors.append(error)
                        raise
                owner.storage = actual_storage
                with mock.patch.dict(sys.modules, modules):
                    namespace['after_build'](owner, SimpleNamespace(binary='not-executed', root=work), {})
                self.assertEqual(len(caught_errors), 1)
                self.assertEqual(sum(kind == 'close' for kind, _ in events), 2)
                result = owner.gates['schema_equivalence']; self.assertFalse(result['passed'])
                if fail == 'no-primary':
                    self.assertIsInstance(caught_errors[0], c.Refusal)
                    self.assertNotIn('native_storage_failure', result)
                else:
                    self.assertIs(caught_errors[0], original)
                    evidence = result['native_storage_failure']
                    self.assertEqual(evidence['primary']['type'], 'RuntimeError')
                    self.assertEqual(evidence['primary']['message'], str(original))
                    self.assertIn('RuntimeError: ' + str(original), evidence['primary']['traceback'])
                    self.assertEqual(evidence['retention_secondary']['type'], 'Refusal')
                    self.assertIn('lacks original installed-before', evidence['retention_secondary']['message'])
                    self.assertIn('Refusal:', evidence['retention_secondary']['traceback'])
                path = next((ctx.work / 'native-retention').glob('*/retention.json'))
                record = json.loads(path.read_bytes()); self.assertFalse(record['complete'])
                self.assertIsNone(record['unregistered_environments'][0]['original_before'])
                self.assertEqual((roots[0] / '.venv/partial.raw').read_bytes(), b'actual synthetic partial install')


if __name__ == '__main__': unittest.main(verbosity=2)
