"""Real native QA import and timestamp-PYC fixtures. Run only on GitHub."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import marshal
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise RuntimeError('GitHub Linux fixtures only')
REPO = Path(__file__).resolve().parents[3]
BASE_TEST = 'som-rs/scripts/hosted_readiness/test_native_qa_authority.py'
revision = subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO), 'rev-parse', 'HEAD']).decode().strip()
raw = (REPO / BASE_TEST).read_bytes()
if raw != subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO), 'cat-file', 'blob', revision + ':' + BASE_TEST]):
    raise RuntimeError('base fixture differs from exact raw Git bytes')
base = ModuleType('_native_qa_import_base_fixture')
base.__file__, base.__cached__, base.__package__ = str(REPO / BASE_TEST), None, ''
base.__som_raw_sha256__ = hashlib.sha256(raw).hexdigest()
sys.modules[base.__name__] = base
exec(compile(raw, base.__file__, 'exec', dont_inherit=True), base.__dict__)
c = base.c


class NativeQaRealImportTests(unittest.TestCase):
    def setUp(self):
        self.subject = base.NativeQaSourceTests('test_exact_synthetic_authority_admits_source_and_grants_no_product_result')
        self.fixture = self.subject.fixture()
        self.reader = self.subject.reader(self.fixture[0])
        self.authority = c.verify(self.reader, c.MODE, self.fixture[3])
        self.before_modules = dict(sys.modules)
        self.before_path = list(sys.path)
        self.controls = None
        self.addCleanup(self.restore)

    def restore(self):
        if self.controls is not None:
            self.controls.close()
        for name, module in list(sys.modules.items()):
            file = getattr(module, '__file__', None)
            if isinstance(file, str) and REPO in Path(file).absolute().parents:
                if name in self.before_modules:
                    sys.modules[name] = self.before_modules[name]
                else:
                    sys.modules.pop(name, None)
        sys.path[:] = self.before_path

    def install(self):
        self.controls = c.install_raw_finder(self.reader, REPO, self.authority)
        return self.controls

    def rebind_admission(self, change):
        for policy in self.fixture[2].values():
            change(policy['qa_source_admission'])
        self.subject.rebind(*self.fixture[:3])

    def test_real_hosts_product_targets_and_replay_import_graph_loads_checked_source(self):
        self.install()
        sys.path.insert(0, str(REPO / 'som-rs/behavior'))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SOM_FULL_PRODUCT_CONTEXT', None)
            hosts = importlib.import_module('hosts')
        self.assertTrue(callable(hosts.products.product_gates))
        for module_name, path in {
            'hosts': 'som-rs/behavior/hosts.py',
            'check_rust_products': 'som-rs/som-targets/qa/check_rust_products.py',
            'check_targets': 'som-rs/som-targets/qa/check_targets.py',
            'python_replay': 'som-rs/scripts/rust_output_acceptance/python_replay.py',
            'qa_harness': 'som-rs/scripts/compiler_acceptance/qa_harness.py',
        }.items():
            module = sys.modules[module_name]
            self.assertEqual(module.__file__, str(REPO / path))
            self.assertEqual(module.__som_raw_sha256__, c.sha(self.reader(path)))
            self.assertIsNone(module.__cached__)
        self.controls.verify_installed()

    def test_missing_required_support_source_is_refused_before_import(self):
        missing = 'som-rs/som-targets/qa/check_rust_products.py'
        self.rebind_admission(lambda admission: admission['qa_support_sources'].pop(missing))
        with self.assertRaisesRegex(ValueError, 'transitive QA support'):
            c.verify(self.reader, c.MODE, self.fixture[3])
        self.assertNotIn('check_rust_products', sys.modules)

    def test_changed_support_raw_bytes_are_refused_before_import(self):
        path = 'som-rs/scripts/rust_output_acceptance/python_replay.py'
        self.fixture[0][path] = self.reader(path) + b'\n# changed support\n'
        with self.assertRaisesRegex(ValueError, 'transitive QA support'):
            c.verify(self.reader, c.MODE, self.fixture[3])
        self.assertNotIn('python_replay', sys.modules)

    def test_omitted_transitive_edge_is_refused_after_rebinding_review(self):
        self.rebind_admission(lambda admission: admission['qa_support_edges'].pop())
        with self.assertRaisesRegex(ValueError, 'transitive QA support'):
            c.verify(self.reader, c.MODE, self.fixture[3])

    def assert_real_common_manual_loader_ignores_timestamp_PYC(self, target_name):
        target = REPO / 'som-rs/tests/rust-output-v1/build_negative_cases.py'
        checked_raw = target.read_bytes()
        cache = Path(importlib.util.cache_from_source(str(target)))
        previous = cache.read_bytes() if cache.is_file() else None
        previous_mode = cache.stat().st_mode & 0o777 if cache.exists() else None
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temporary:
            sentinel = Path(temporary) / 'PYC-SENTINEL'
            poison = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('PYC executed')\nPYC_SENTINEL = True\n"
            code = compile(poison, str(target), 'exec', dont_inherit=True)
            info = target.stat()
            header = importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, int(info.st_mtime) & 0xFFFFFFFF, info.st_size & 0xFFFFFFFF)
            cache.write_bytes(header + marshal.dumps(code))
            try:
                # Control: the ordinary real SourceFileLoader accepts this PYC.
                spec = importlib.util.spec_from_file_location('native_qa_poison_control', target)
                control = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(control)
                self.assertTrue(control.PYC_SENTINEL)
                self.assertEqual(sentinel.read_text(), 'PYC executed')
                self.assertEqual(cache.read_bytes()[:16], header)
                sentinel.unlink()
                # Subject: unchanged frozen common.py uses its actual manual path.
                self.install()
                caller_path = REPO / f'som-rs/tests/{target_name}-output-v1/common.py'
                sys.path.insert(0, str(caller_path.parent))
                common = importlib.import_module('common')
                self.assertEqual(common.__file__, str(caller_path))
                self.assertEqual(common.TARGET, '"target":"' + target_name + '"')
                self.assertFalse(sentinel.exists())
                self.assertFalse(hasattr(common.rust_cases, 'PYC_SENTINEL'))
                self.assertEqual(common.rust_cases.__file__, str(target))
                self.assertEqual(common.rust_cases.__som_raw_sha256__, c.sha(checked_raw))
                self.assertEqual(common.rust_cases.__som_manual_raw_source__, 'som-rs/tests/rust-output-v1/build_negative_cases.py')
                self.assertIsNone(common.rust_cases.__cached__)
                self.assertTrue(callable(common.rust_cases.compact))
                self.assertEqual(target.read_bytes(), checked_raw)
                self.controls.verify_installed()
            finally:
                if previous is None:
                    cache.unlink(missing_ok=True)
                else:
                    cache.write_bytes(previous)
                    cache.chmod(previous_mode)

    def test_real_TypeScript_common_ignores_valid_matching_timestamp_PYC(self):
        self.assert_real_common_manual_loader_ignores_timestamp_PYC('typescript')

    def test_real_React_common_ignores_valid_matching_timestamp_PYC(self):
        self.assert_real_common_manual_loader_ignores_timestamp_PYC('react')

    def test_unlisted_real_manual_source_path_is_refused(self):
        self.install()
        target = REPO / 'som-rs/tests/rust-output-v1/rust_profile.py'
        with self.assertRaisesRegex(ValueError, 'unlisted repository manual source'):
            importlib.util.spec_from_file_location('rust_build_negative_cases', target)

    def test_filename_only_manual_caller_cannot_replace_checked_raw_metadata(self):
        self.install()
        sys.path.insert(0, str(REPO / 'som-rs/tests/typescript-output-v1'))
        path = 'som-rs/tests/typescript-output-v1/common.py'
        scope = {'__name__': '_unbound_common_fixture', '__file__': str(REPO / path)}
        with self.assertRaisesRegex(ValueError, 'caller did not execute its checked raw source'):
            exec(compile(self.reader(path), str(REPO / path), 'exec', dont_inherit=True), scope)

    def test_import_control_tamper_is_a_postcheck_failure(self):
        self.install()
        controlled = importlib.util.spec_from_file_location
        importlib.util.spec_from_file_location = self.controls.original_spec
        try:
            with self.assertRaisesRegex(ValueError, 'controls changed during QA'):
                self.controls.verify_installed()
        finally:
            importlib.util.spec_from_file_location = controlled

    def test_borrowed_Context_lease_never_deactivates_its_owner(self):
        self.install()
        owner = self.controls
        self.assertIs(owner.owns_controls, True)
        context_module = ModuleType('full_product_context')
        context_module.__file__ = str(REPO / 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py')
        context_module.__som_raw_sha256__ = c.sha(self.reader('som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'))
        context_module._ACTIVE = SimpleNamespace(qa_mode=c.MODE, repo=REPO,
            qa_source_authority=self.authority, qa_import_controls=owner)
        with mock.patch.dict(sys.modules, {'full_product_context': context_module}):
            borrowed = c.install_raw_finder(self.reader, REPO, self.authority)
            self.assertIs(borrowed.owns_controls, False)
            self.assertIsNot(borrowed, owner)
            self.assertIs(borrowed.controls, owner.controls)
            original_guard = importlib.util.spec_from_file_location
            borrowed.verify_installed()
            borrowed.close()
            self.assertIs(importlib.util.spec_from_file_location, original_guard)
            owner.verify_installed()
            self.assertIn(owner.controls, sys.meta_path)

    def test_only_owned_close_restores_both_original_import_guards(self):
        self.install()
        lease = self.controls
        original_spec, finder = lease.original_spec, lease.controls
        lease.verify_installed()
        lease.close()
        lease.close()
        self.assertIs(importlib.util.spec_from_file_location, original_spec)
        self.assertNotIn(finder, sys.meta_path)

    def test_two_refusals_cannot_become_a_general400_or_quality_scope_exception(self):
        self.rebind_admission(lambda admission: admission['native_python_phase2_outcomes'].update(real_product_gate_rows=581))
        with self.assertRaisesRegex(ValueError, '584/582/two-refusal'):
            c.verify(self.reader, c.MODE, self.fixture[3])


if __name__ == '__main__':
    unittest.main()
