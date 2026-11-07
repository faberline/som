"""GitHub-only Domain metadata protocol tests; no native Context factory.

The actual Core, SourceBytes class and metadata helpers run on a tiny committed
synthetic Git tree. Source/admission authority remain protocol doubles. The
SourceFactories constructor is bypassed. No runtime qualification, compiler,
service, product, live-before gate or input transport consumer runs here.
"""
from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub tests only')

# -I removes the script directory from sys.path. Load this explicit fixture
# dependency without adding a repository search path or granting it authority.
HERE = Path(__file__).absolute().parent
protocol_path = HERE / 'test_native_active_controls.py'
protocol = ModuleType('_som_ts_domain_protocol_fixture')
protocol.__file__, protocol.__package__ = str(protocol_path), ''
sys.modules[protocol.__name__] = protocol
exec(compile(protocol_path.read_bytes(), str(protocol_path), 'exec', dont_inherit=True), protocol.__dict__)

REPO = protocol.REPO
BASE = 'som-rs/scripts/hosted_readiness/'
DOMAIN_PATH = BASE + 'typescript_regression_domain.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
WHEELS = BASE + 'product_python/produce_wheels.py'
RUNTIME = BASE + 'product_runtime/native_runtime_verifier.py'
SUPPORT = BASE + 'product_runtime/qualify_runtime.py'
RAW_PATH = BASE + 'raw_digest_compat.py'
DOMAIN = 'ts.owner.domain-verifier'
TARGET = 'ts.domain.typescript-regression-raw-digest'
Q = 'ts.domain.typescript-independent-runtime-metadata-support'
FRAGMENT = 'ts.domain.runtime-verifier'
KEY = 'typescript_regression_raw_digest'
RUNTIME_NAMES = ['digest', 'parse', 'safe_read', 'qualification_data', 'validate_inventory',
                 'read_inventory', 'collect_files', 'expected_directories', 'verify_installed_tree']


class GitTransfer(protocol.ProtocolTransfer):
    """Same explicit protocol authority double with an actual fixture revision."""
    def __init__(self, fixture):
        super().__init__(fixture)
        self.revision = fixture.revision
        self.admission.update(helper_revision=self.revision, candidate_revision=self.revision)


class DomainFixture(protocol.Fixture):
    def __init__(self, case):
        caller = ('def run(controls, target="domain"):\n'
                  '    if target == "runtime":\n'
                  '        return controls.load_fragment("' + FRAGMENT + '")\n'
                  '    return controls.load_module("' + DOMAIN + '")\n')
        with mock.patch.object(protocol, 'ProtocolTransfer', GitTransfer):
            super().__init__(case, caller)

    def add_role(self, role, path, raw, **kwargs):
        row = super().add_role(role, path, raw, **kwargs)
        if role != 'm6.api.producer':
            return row
        super().add_role('runtime.context', CONTEXT, (REPO / CONTEXT).read_bytes(),
                         module='_som_ts_domain_fixture_context',
                         bindings={'__som_native_active_controls__': {'kind': 'active-controls'}})
        wheels_role = 'ts.domain.typescript-data-runtime-authority'
        super().add_role(wheels_role, WHEELS, (REPO / WHEELS).read_bytes(),
                         module='_som_ts_domain_typescript_data_runtime_authority')
        bindings = {'__som_native_active_controls__': {'kind': 'active-controls'},
                    '__som_native_SourceBytes__': {'kind': 'role-export', 'role': 'runtime.context',
                                                 'attribute': 'SourceBytes'},
                    '__som_native_WheelSources__': {'kind': 'role-export', 'role': wheels_role,
                                                  'attribute': 'Sources'}}
        super().add_role(DOMAIN, DOMAIN_PATH, (REPO / DOMAIN_PATH).read_bytes(),
                         module='_som_typescript_owner_domain_verifier', bindings=bindings,
                         callers=[{'role': 'caller', 'function': 'run'}])
        super().add_role('test.domain.factory-type', BASE + 'synthetic_domain_type_consumer.py',
                         'VALUE = 1\n', bindings={'SourceFactories': {'kind': 'role-export',
                            'role': DOMAIN, 'attribute': 'SourceFactories'}})
        super().add_role(TARGET, RAW_PATH, (REPO / RAW_PATH).read_bytes(),
                         module='_som_ts_domain_' + KEY,
                         callers=[{'role': DOMAIN, 'function': 'SourceFactories.module'}])
        super().add_role(Q, SUPPORT, (REPO / SUPPORT).read_bytes(),
                         module='_som_ts_domain_typescript_independent_runtime_metadata_support')
        runtime_bindings = {'q': {'kind': 'role-module', 'role': Q}}
        for name in ('Refusal', 'require'):
            runtime_bindings[name] = {'kind': 'role-export', 'role': Q, 'attribute': name}
        for name in ('SHA', 'unique_keys', 'manifest_data', 'clean_path', 'no_links', 'sha'):
            runtime_bindings['_q_' + name] = {'kind': 'role-export', 'role': Q, 'attribute': name}
        for name, module, attribute in (('hashlib', 'hashlib', None), ('json', 'json', None),
                ('os', 'os', None), ('re', 're', None), ('stat', 'stat', None),
                ('Path', 'pathlib', 'Path'), ('PurePosixPath', 'pathlib', 'PurePosixPath')):
            runtime_bindings[name] = {'kind': 'stdlib', 'module': module}
            if attribute:
                runtime_bindings[name]['attribute'] = attribute
        for name in ('AUDIT', 'META_FILES', 'MAX_META', 'MAX_FILE', 'MAX_TOTAL'):
            runtime_bindings[name] = {'kind': 'source-constant', 'source': DOMAIN_PATH,
                                      'name': '_DOMAIN_RUNTIME_' + name}
        raw_runtime = (REPO / RUNTIME).read_bytes()
        nodes = [n for n in ast.parse(raw_runtime).body
                 if isinstance(n, ast.FunctionDef) and n.name in RUNTIME_NAMES]
        case_ast = ast.Module(body=nodes, type_ignores=[])
        record = super().add_role(FRAGMENT, RUNTIME, raw_runtime, kind='fragment',
                                  module='_som_ts_domain_independent_runtime_reuse',
                                  bindings=runtime_bindings,
                                  callers=[{'role': 'caller', 'function': 'run'}])
        record['selector'] = {'kind': 'top-level-functions', 'names': RUNTIME_NAMES,
            'ast_sha256': hashlib.sha256(ast.dump(case_ast, include_attributes=False).encode()).hexdigest()}
        # Actual Git operations happen only on GitHub and only inside this
        # fixture's temporary tree. No repository worktree is changed.
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        def git(*args):
            return subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
                                  env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        git('init', '--quiet')
        git('add', '--', '.')
        git('-c', 'user.name=Domain protocol fixture', '-c', 'user.email=fixture@example.invalid',
            '-c', 'core.hooksPath=/dev/null', 'commit', '--quiet', '-m', 'synthetic source metadata fixture')
        self.revision = git('rev-parse', 'HEAD').decode().strip()
        return row

    def factory(self):
        domain = self.run()
        sources = domain.__som_native_SourceBytes__(self.root, self.revision)
        result = object.__new__(domain.SourceFactories)
        result.repo, result.helper_revision, result.candidate_revision = self.root, self.revision, self.revision
        result.qa_mode, result.qa_source_authority = 'native-qa-v1', self.transfer.admission
        result.data, result.sources = {}, sources
        result.cache, result.modules, result.names = {}, {}, []
        result.fragments, result.closed, result.lease = [], False, self.controls.borrow()
        self.case.addCleanup(result.close)
        return domain, result


class NativeTsDomainTests(unittest.TestCase):
    def test_actual_git_sourcebytes_and_core_module_identity_without_context_constructor(self):
        f = DomainFixture(self)
        domain, factory = f.factory()
        module = factory.module(KEY, RAW_PATH)
        self.assertIs(module, f.domain.records[TARGET].module)
        self.assertIs(factory.module(KEY, RAW_PATH), module)
        self.assertEqual(factory.sources.read(DOMAIN_PATH), (REPO / DOMAIN_PATH).read_bytes())
        self.assertIs(type(factory.sources), domain.__som_native_SourceBytes__)
        self.assertNotIn('reader', factory.__dict__)
        self.assertIsNone(f.domain.records['runtime.context'].module._ACTIVE)
        self.assertFalse(f.domain.closed)

    def test_fake_facade_refuses_before_attribute_hooks_or_target_execution(self):
        f = DomainFixture(self)
        domain = f.run()
        calls = []
        class FakeFactory:
            def __getattribute__(self, name):
                calls.append(name)
                raise AssertionError('foreign facade hook must not run')
        with self.assertRaisesRegex(ValueError, 'actual open Domain SourceFactories required'):
            domain.SourceFactories.module(FakeFactory(), KEY, RAW_PATH)
        self.assertEqual(calls, [])
        self.assertNotIn(TARGET, f.domain.records)

    def test_wrong_fixed_path_and_foreign_cached_pointer_refuse(self):
        f = DomainFixture(self)
        _, factory = f.factory()
        actual = factory.module(KEY, RAW_PATH)
        with self.assertRaisesRegex(ValueError, 'unlisted fixed Domain module key/path'):
            factory.module(KEY, WHEELS)
        factory.modules[KEY] = ModuleType('foreign-pointer')
        try:
            with self.assertRaisesRegex(ValueError, 'Domain cached module ownership differs'):
                factory.module(KEY, RAW_PATH)
            self.assertIs(f.domain.records[TARGET].module, actual)
        finally:
            factory.modules[KEY] = actual
        calls = []
        class ForeignReader:
            def read(self, path):
                calls.append(path)
                raise AssertionError('foreign source callback must not run')
        original_sources = factory.sources
        factory.sources = ForeignReader()
        try:
            with self.assertRaisesRegex(ValueError, 'Domain module source owner differs'):
                factory.module(KEY, RAW_PATH)
            self.assertEqual(calls, [])
        finally:
            factory.sources = original_sources

    def test_nine_function_scope_uses_own_q_and_keeps_borrowed_owner_live(self):
        f = DomainFixture(self)
        _, factory = f.factory()
        scope = f.caller().run(f.controls, 'runtime')
        factory.fragments.append(scope)
        self.assertEqual(scope.parse(b'{"value":1}'), {'value': 1})
        self.assertIs(scope.q, f.domain.records[Q].module)
        self.assertNotIn('runtime.qualifier', f.domain.records)
        self.assertFalse(hasattr(scope, '_native_support'))
        self.assertFalse(hasattr(scope, 'main'))
        record = next(r for r in f.domain.records.values() if r.role == FRAGMENT)
        self.assertIs(scope.parse.__globals__, record.namespace)
        self.assertTrue(any(scope.parse.__code__ is code for code in record.codes.values()))
        finder, spec = f.domain.finder, f.domain.spec
        factory.close()
        self.assertFalse(f.domain.closed)
        self.assertIs(f.domain.finder, finder)
        self.assertIs(f.domain.spec, spec)
        with self.assertRaisesRegex(ValueError, 'fragment scope is closed'):
            scope.parse(b'{}')


if __name__ == '__main__':
    unittest.main()
