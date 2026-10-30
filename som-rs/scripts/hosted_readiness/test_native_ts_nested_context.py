"""GitHub-only nested Context identity tests with actual Core and SourceBytes.

The Context constructor is bypassed. Admission/transfer and Context data are
synthetic. No PostgreSQL, compiler, product child or genuine factory runs here.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub tests only')
BASE = 'som-rs/scripts/hosted_readiness/'
REPO = Path(__file__).absolute().parents[3]
NESTED = BASE + 'typescript_regression_nested_rust.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
ROLE = 'ctx.typescript-regression-nested-rust'
PINS = {
    NESTED: 'e0b0f1c17f5337aac8ba071d68ae7475fd13617844944cd72f436b0237ab55a6',
    CONTEXT: '7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440',
    BASE + 'native_active_controls.py': 'eb0f804a8aa7de0857213d34478adc869fe51f7259c5552439dca109e4e6a038',
    BASE + 'native_source_bootstrap.py': 'd5c8ceb1a5b0b779662134afdf477fd90cfbc5319cca64e7bb7a8b5fe76ae473',
    BASE + 'native_qa_authority.py': '024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1',
    BASE + 'test_native_active_controls.py': '1a5852fb1693bcb921fcb19e9a20fb8281751e85f6f0ea2805b88323d230a201',
}

def source(path):
    raw = (REPO / path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINS[path]:
        raise AssertionError('reviewed same-head dependency differs: ' + path)
    return raw

for path in PINS:
    source(path)
protocol = ModuleType('_som_nested_context_protocol')
protocol.__file__, protocol.__package__ = str(REPO / BASE / 'test_native_active_controls.py'), ''
sys.modules[protocol.__name__] = protocol
exec(compile(source(BASE + 'test_native_active_controls.py'), protocol.__file__, 'exec', dont_inherit=True), protocol.__dict__)

class GitTransfer(protocol.ProtocolTransfer):
    def __init__(self, fixture):
        super().__init__(fixture)
        self.revision = fixture.revision
        self.admission.update(helper_revision=self.revision, candidate_revision=self.revision)

class NestedFixture(protocol.Fixture):
    def __init__(self, case):
        caller = ('def run(controls, ctx=None):\n'
                  '    target = controls.load_module("' + ROLE + '")\n'
                  '    if ctx is None:\n        return target\n'
                  '    return target._native_context(ctx)\n')
        with mock.patch.object(protocol, 'ProtocolTransfer', GitTransfer):
            super().__init__(case, caller)

    def add_role(self, role, path, raw, **kwargs):
        row = super().add_role(role, path, raw, **kwargs)
        if role != 'm6.api.producer':
            return row
        super().add_role('runtime.context', CONTEXT, source(CONTEXT),
            module='_som_full_product_context',
            bindings={'__som_native_active_controls__': {'kind': 'active-controls'}},
            callers=[{'role': ROLE, 'function': '_native_context'}])
        super().add_role(ROLE, NESTED, source(NESTED),
            module='_som_full_typescript_regression_nested_rust',
            callers=[{'role': 'caller', 'function': 'run'}],
            bindings={'__som_native_active_controls__': {'kind': 'active-controls'},
                      '__som_native_Context__': {'kind': 'role-export', 'role': 'runtime.context', 'attribute': 'Context'},
                      '__som_native_SourceBytes__': {'kind': 'role-export', 'role': 'runtime.context', 'attribute': 'SourceBytes'}})
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        def git(*args):
            return subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
                env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        git('init', '--quiet'); git('add', '--', '.')
        git('-c', 'user.name=Nested identity fixture', '-c', 'user.email=fixture@example.invalid',
            '-c', 'core.hooksPath=/dev/null', 'commit', '--quiet', '-m', 'synthetic nested identity source')
        self.revision = git('rev-parse', 'HEAD').decode().strip()
        return row

    def context(self):
        nested = self.run()
        hosted = self.domain.records['runtime.context'].module
        ctx = object.__new__(hosted.Context)
        ctx.repo, ctx.helper_revision, ctx.candidate_revision = self.root, self.revision, self.revision
        ctx.qa_mode, ctx.qa_source_authority = 'native-qa-v1', self.transfer.admission
        ctx.sources = hosted.SourceBytes(self.root, self.revision)
        ctx.path = self.root / 'synthetic-context.json'
        ctx.raw = json.dumps({'scope': 'synthetic constructor-bypass identity test'}).encode()
        ctx.path.write_bytes(ctx.raw)
        hosted._ACTIVE = ctx
        self.stack.enter_context(mock.patch.dict(os.environ, {hosted.ENV: str(ctx.path)}))
        return nested, hosted, ctx

class NativeNestedContextTests(unittest.TestCase):
    def test_actual_core_context_getter_ignores_foreign_generic_alias_and_retains_owner(self):
        f = NestedFixture(self); nested, hosted, ctx = f.context()
        calls = []
        foreign = ModuleType('full_product_context')
        def trap():
            calls.append('foreign getter'); raise AssertionError('foreign alias must not run')
        foreign.context = trap
        with mock.patch.dict(sys.modules, {'full_product_context': foreign}):
            self.assertIs(f.caller().run(f.controls, ctx), hosted)
            self.assertIs(sys.modules['full_product_context'], foreign)
        self.assertEqual(calls, [])
        self.assertIs(hosted._ACTIVE, ctx)
        self.assertIs(type(ctx.sources), nested.__som_native_SourceBytes__)
        self.assertEqual(ctx.sources.read(NESTED), source(NESTED))
        self.assertFalse(f.domain.closed)

    def test_foreign_context_refuses_before_attribute_hooks(self):
        f = NestedFixture(self); _, _, _ = f.context(); calls = []
        class Foreign:
            def __getattribute__(self, key):
                calls.append(key); raise AssertionError('foreign Context hook must not run')
        with self.assertRaisesRegex(ValueError, 'actual native nested Context object required'):
            f.caller().run(f.controls, Foreign())
        self.assertEqual(calls, [])

    def test_inactive_owner_and_changed_context_data_refuse_at_original_getter(self):
        f = NestedFixture(self); _, hosted, ctx = f.context()
        hosted._ACTIVE = None
        try:
            with self.assertRaisesRegex(ValueError, 'actual active nested Context owner required'):
                f.caller().run(f.controls, ctx)
            self.assertIsNone(hosted._ACTIVE)
        finally:
            hosted._ACTIVE = ctx
        ctx.path.write_bytes(b'{"changed":true}')
        with self.assertRaisesRegex(ValueError, 'hosted context identity changed'):
            f.caller().run(f.controls, ctx)
        self.assertIs(hosted._ACTIVE, ctx)

    def test_foreign_source_hook_revision_and_changed_git_bytes_refuse(self):
        f = NestedFixture(self); _, _, ctx = f.context(); original = ctx.sources; calls = []
        class ForeignSource:
            def __getattribute__(self, key):
                calls.append(key); raise AssertionError('foreign source hook must not run')
        ctx.sources = ForeignSource()
        try:
            with self.assertRaisesRegex(ValueError, 'actual native nested SourceBytes owner required'):
                f.caller().run(f.controls, ctx)
        finally:
            ctx.sources = original
        self.assertEqual(calls, [])
        ctx.candidate_revision = 'b' * 40
        try:
            with self.assertRaisesRegex(ValueError, 'actual native nested Context source identities differ'):
                f.caller().run(f.controls, ctx)
        finally:
            ctx.candidate_revision = f.revision
        path = f.root / CONTEXT; raw = path.read_bytes(); path.write_bytes(raw + b'\n')
        try:
            with self.assertRaisesRegex(ValueError, 'source differs from committed Git bytes'):
                f.caller().run(f.controls, ctx)
        finally:
            path.write_bytes(raw)

if __name__ == '__main__':
    unittest.main()
