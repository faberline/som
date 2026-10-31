"""GitHub-only Rust verifier protocol fixtures.

The actual Core, finder, support source and new verifier run on owned synthetic
paths. Admission and source authority are explicit protocol doubles. These
tests do not qualify native tools, a Context factory, or any product gate.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock

# Keep the existing isolated runner. Load the protocol fixture by its exact path.
protocol_path = Path(__file__).absolute().with_name('test_native_active_controls.py')
protocol = ModuleType('_som_rust_tool_active_protocol_fixture')
protocol.__file__, protocol.__cached__, protocol.__package__ = str(protocol_path), None, ''
exec(compile(protocol_path.read_bytes(), str(protocol_path), 'exec', dont_inherit=True), protocol.__dict__)

REPO = protocol.REPO
BASE = 'som-rs/scripts/hosted_readiness/product_runtime/'
LEGACY = BASE + 'qualify_rust_tools.py'
SUPPORT = BASE + 'qualify_runtime.py'
VERIFIER = BASE + 'native_rust_tool_verifier.py'
PROFILE = 'som-rs/contracts/native-tools-v1/product-rust-tools-v1.json'
LIVE = 'native.rust-tools.verifier'
LIVE_SUPPORT = 'native.rust-tools.runtime-support'
DATA = 'test.rust-tools.data-verifier'
DATA_SUPPORT = 'test.rust-tools.data-support'
EXPORTS = ('no_links', 'unique_keys', 'MAX_MEMBER', 'SHA', 'clean_path',
           'MAX_MEMBERS', 'MAX_ARCHIVE', 'sha', 'regular')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class RustFixture(protocol.Fixture):
    """Add the finite roles before the real Core reads its source universe."""
    def __init__(self, case):
        caller = ('def run(controls, data=False):\n'
                  '    if data:\n'
                  '        return controls.load_module("test.rust-tools.data-verifier")\n'
                  '    return controls.load_module("native.rust-tools.verifier")\n')
        super().__init__(case, caller)

    def add_role(self, role, path, raw, **kwargs):
        row = super().add_role(role, path, raw, **kwargs)
        if role == 'm6.api.producer':
            # These are original receipt/profile data, not executable roles.
            for source in (LEGACY, PROFILE):
                body = (REPO / source).read_bytes()
                self.raw[source] = body
                destination = self.root / source
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(body)
            for target, support, suffix in ((LIVE, LIVE_SUPPORT, 'live'),
                                             (DATA, DATA_SUPPORT, 'data')):
                super().add_role(support, SUPPORT, (REPO / SUPPORT).read_bytes(),
                                 module='_som_test_rust_support_' + suffix)
                bindings = {'__som_native_active_controls__': {'kind': 'active-controls'},
                            'q': {'kind': 'role-module', 'role': support}}
                for name in ('require', 'Refusal'):
                    bindings[name] = {'kind': 'role-export', 'role': support, 'attribute': name}
                for name in EXPORTS:
                    bindings['_q_' + name] = {'kind': 'role-export', 'role': support, 'attribute': name}
                super().add_role(target, VERIFIER, (REPO / VERIFIER).read_bytes(),
                                 module='_som_test_rust_verifier_' + suffix,
                                 callers=[{'role': 'caller', 'function': 'run'}], bindings=bindings)
        return row


class ReachedInstalledTree(Exception):
    """A distinct later seam. Reaching it is not full verification success."""


class NativeRustVerifierTests(unittest.TestCase):
    def test_actual_core_keeps_truthful_source_and_independent_support_namespaces(self):
        legacy_name = '_som_rust_product_runtime_support'
        legacy = ModuleType(legacy_name)
        patcher = mock.patch.dict(sys.modules, {legacy_name: legacy})
        patcher.start()
        self.addCleanup(patcher.stop)
        # Fixture cleanup is registered later, so its real Core closes first.
        f = RustFixture(self)
        live = f.run()
        data = f.caller().run(f.controls, True)
        self.assertIsNot(live, data)
        self.assertIsNot(live.q, data.q)
        self.assertIsNot(live.Refusal, data.Refusal)
        self.assertEqual(live.__file__, str(f.root / VERIFIER))
        self.assertEqual(data.__file__, str(f.root / VERIFIER))
        self.assertEqual(live.q.__file__, str(f.root / SUPPORT))
        self.assertIs(f.domain.records[LIVE].module, live)
        self.assertTrue(f.domain.records[LIVE].codes)
        self.assertIs(f.domain.records[LIVE_SUPPORT].module, live.q)
        self.assertIs(sys.modules[legacy_name], legacy)
        self.assertIs(f.run(), live)

    def test_original_receipt_source_guard_uses_old_file_and_rejects_new_or_changed_source(self):
        f = RustFixture(self)
        helper = f.run()
        out = f.root / 'source-seam'
        out.mkdir()
        profile_raw = (REPO / PROFILE).read_bytes()
        (out / 'profile.json').write_bytes(profile_raw)
        profile = helper.profile_data(profile_raw)
        old_path = f.root / LEGACY
        old_raw = old_path.read_bytes()
        new_sha = sha((f.root / VERIFIER).read_bytes())
        self.assertNotEqual(sha(old_raw), new_sha)
        self.assertEqual(helper.__file__, str(f.root / VERIFIER))
        bindings = {LEGACY: {'sha256': sha(old_raw)},
                    SUPPORT: {'sha256': helper.SUPPORT_SHA},
                    PROFILE: {'sha256': sha(profile_raw)}}
        receipt = {'version': 'som.rust-product-tool-qualification.v1',
                   'status': 'qualification_commands_complete_awaiting_independent_review',
                   'source_revision': 'a' * 40, 'product_acceptance_passed': False,
                   'profile_sha256': sha(profile_raw), 'source_bindings': bindings,
                   'source_bindings_after': copy.deepcopy(bindings)}
        reached = []
        def later_tree(*args):
            reached.append('installed-tree')
            raise ReachedInstalledTree('source guard passed; real installed tree remains unrun')
        with mock.patch.object(helper, 'verify_install_tree', later_tree):
            with self.assertRaises(ReachedInstalledTree):
                helper.verify_record(out, profile, 'a' * 40, receipt)
            self.assertEqual(reached, ['installed-tree'])
            reached.clear()
            wrong = copy.deepcopy(receipt)
            wrong['source_bindings'][LEGACY]['sha256'] = new_sha
            wrong['source_bindings_after'][LEGACY]['sha256'] = new_sha
            with self.assertRaisesRegex(helper.Refusal, 'source binding receipt differs'):
                helper.verify_record(out, profile, 'a' * 40, wrong)
            self.assertEqual(reached, [])
            try:
                old_path.write_bytes(old_raw + b'\n# changed original source\n')
                with self.assertRaisesRegex(helper.Refusal, 'source binding receipt differs'):
                    helper.verify_record(out, profile, 'a' * 40, receipt)
                self.assertEqual(reached, [])
            finally:
                old_path.write_bytes(old_raw)
        self.assertEqual(helper.__file__, str(f.root / VERIFIER))

    def test_all_six_actual_file_pins_are_rehashed_before_use(self):
        f = RustFixture(self)
        helper = f.run()
        out = f.root / 'synthetic-tools'
        binary_root = out / 'install/bin'
        binary_root.mkdir(parents=True)
        bodies = {'fixture/cargo/bin/' + name: b'\x7fELF\x02\x01\x01 ' + name.encode()
                  for name in helper.NAMES}
        manifest = ''.join('file:bin/' + name + '\n' for name in sorted(helper.NAMES))
        bodies['fixture/cargo/manifest.in'] = manifest.encode()
        row = {'member_root': 'fixture', 'component_dir': 'cargo',
               'install_manifest_text': manifest,
               'all_regular_files': {name: {'bytes': len(body), 'sha256': sha(body),
                                          'mode': 0o755 if '/bin/' in name else 0o644}
                                     for name, body in bodies.items()}}
        profile = {'components': {'cargo': row},
                   'executables': {name: {'path': 'bin/' + name} for name in helper.NAMES}}
        for name in helper.NAMES:
            path = binary_root / name
            path.write_bytes(bodies['fixture/cargo/bin/' + name])
            path.chmod(0o755)
        tools = helper.verify_tools(out, profile)
        self.assertEqual(set(tools), helper.NAMES)
        self.assertTrue(all(tools[name] == binary_root / name for name in helper.NAMES))
        (binary_root / 'clippy-driver').write_bytes(b'changed')
        with self.assertRaisesRegex(helper.Refusal, 'native tool changed before use'):
            helper.verify_tools(out, profile)

    def test_changed_provider_regex_is_refused_before_foreign_fullmatch(self):
        f = RustFixture(self)
        helper = f.run()
        calls = []
        class ForeignPattern:
            def fullmatch(self, value):
                calls.append(value)
                raise AssertionError('foreign regex hook must not run')
        original = helper.q.SHA
        helper.q.SHA = ForeignPattern()
        try:
            with self.assertRaisesRegex(ValueError, 'fixed role target export changed'):
                f.run()
            self.assertEqual(calls, [])
        finally:
            helper.q.SHA = original


if __name__ == '__main__':
    unittest.main()
