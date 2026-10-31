"""GitHub-only fixtures for the new owner. No product acceptance is granted."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest

HERE = Path(__file__).absolute().parent


def load(name, relative):
    raw = (HERE / relative).read_bytes()
    module = ModuleType(name)
    module.__file__, module.__package__, module.__cached__ = str(HERE / relative), '', None
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


class OwnerInputTests(unittest.TestCase):
    def setUp(self):
        self.name = '_som_ts_owner_fixture'
        self.owner = load(self.name, 'typescript_regression_owner.py')
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup(); sys.modules.pop(self.name, None)

    def test_duplicate_json_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.owner.parse(b'{"a":1,"a":2}')

    def test_nonfinite_json_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            self.owner.parse(b'{"a":NaN}')

    def test_complete_input_tree_binds_mode_size_and_hash(self):
        (self.root / 'tool').write_bytes(b'fixed tool'); (self.root / 'tool').chmod(0o755)
        self.assertEqual(self.owner.tree(self.root), {'tool': {'sha256': hashlib.sha256(b'fixed tool').hexdigest(),
                                                             'size': 10, 'mode': 0o755}})

    def test_internal_npm_alias_stays_opaque_in_capture(self):
        source, target = self.root / 'source', self.root / 'capture'; source.mkdir()
        (source / 'npm-cli.js').write_bytes(b'fixed npm'); (source / 'npm').symlink_to('npm-cli.js')
        inventory = self.owner.tree(source)
        ledger = self.owner.copy_capture(source, target)
        self.assertEqual(inventory['npm']['type'], 'internal-alias')
        self.assertEqual(ledger['npm']['type'], 'opaque-alias')
        self.assertFalse((target / 'npm').exists())
        self.assertEqual((target / 'npm-cli.js').read_bytes(), b'fixed npm')

    def test_input_external_alias_is_refused(self):
        source = self.root / 'source'; source.mkdir()
        (self.root / 'outside').write_bytes(b'outside'); (source / 'link').symlink_to('../outside')
        with self.assertRaisesRegex(ValueError, 'internal'):
            self.owner.tree(source)

    def test_capture_unknown_external_alias_is_refused(self):
        source = self.root / 'source'; source.mkdir()
        (self.root / 'outside').write_bytes(b'outside'); (source / 'link').symlink_to('../outside')
        with self.assertRaisesRegex(ValueError, 'unqualified external'):
            self.owner.copy_capture(source, self.root / 'capture')

    def test_capture_known_python_alias_is_only_metadata(self):
        source = self.root / 'source'; source.mkdir()
        target = self.root / 'python'; target.write_bytes(b'qualified Python')
        (source / 'python').symlink_to(target)
        proof = self.owner.copy_capture(source, self.root / 'capture',
                                        {str(target): hashlib.sha256(target.read_bytes()).hexdigest()})
        self.assertEqual(proof['python']['target_role'], 'qualified-runtime-python')
        self.assertFalse((self.root / 'capture/python').exists())

    def test_internal_directory_alias_does_not_duplicate_tree(self):
        source = self.root / 'source'; (source / 'lib').mkdir(parents=True)
        (source / 'lib/a').write_bytes(b'payload'); (source / 'lib64').symlink_to('lib', target_is_directory=True)
        proof = self.owner.copy_capture(source, self.root / 'capture')
        self.assertEqual(proof['lib64']['target_role'], 'internal-directory')
        self.assertFalse((self.root / 'capture/lib64').exists())
        self.assertEqual((self.root / 'capture/lib/a').read_bytes(), b'payload')

    def test_pending_input_failure_keeps_original_error_and_capture(self):
        owner_dir = self.root / 'owner'; owner_dir.mkdir()
        work = self.root / 'work'; work.mkdir()
        ctx = SimpleNamespace(helper_revision='a' * 40, candidate_revision='a' * 40,
                              work=work, sources=SimpleNamespace(pins={}), runtime=lambda: {
                                  'tools': {'python': Path(sys.executable).resolve()}})
        self.owner.admitted_context = lambda unused: object()
        self.owner.plan_scope = lambda unused: {'fixture': 'scope'}
        ctx.sources.read = lambda unused: b'fixture'
        original = RuntimeError('original pending profile failure')
        def pending(unused):
            raise original
        self.owner.admit_inputs = pending
        with self.assertRaises(RuntimeError) as caught:
            self.owner.run_gate(ctx, self.root, object(), owner_dir, self.owner.GATE)
        self.assertIs(caught.exception, original)
        raw = (owner_dir / 'result.json').read_bytes(); capture = self.owner.parse(raw)
        self.assertFalse(capture['owned_waits_complete']); self.assertFalse(capture['owned_cleanup_complete'])
        self.assertFalse(capture['complete']); self.assertFalse(capture['product_acceptance_passed'])
        self.assertEqual(capture['primary_failure']['reason'], str(original))
        self.assertFalse((owner_dir / 'verified-result.json').exists())
        with self.assertRaises(FileExistsError):
            self.owner.save(owner_dir / 'result.json', capture)
        self.assertEqual((owner_dir / 'result.json').read_bytes(), raw)

    def test_old_f59_identity_fails_before_source_read(self):
        calls = []
        with self.assertRaisesRegex(ValueError, 'new exact source'):
            self.owner.verify_result(self.root, lambda value: calls.append(value), self.owner.F59,
                                     self.owner.F59, self.owner.GATE, repo=self.root, proof_roots={})
        self.assertEqual(calls, [])


class NamedInputTests(unittest.TestCase):
    def setUp(self):
        self.name = '_som_ts_named_input_fixture'
        self.module = load(self.name, 'typescript_regression_inputs.py')
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup(); sys.modules.pop(self.name, None)

    def fixture(self):
        target = self.root / 'RS01_CNL_V6/prompt.json'; target.parent.mkdir()
        target.write_bytes(b'{"version":"som.prompt.v6"}')
        row = {'mode': target.stat().st_mode & 0o777, 'size': len(target.read_bytes()),
               'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
        profile = {'siblings': {'rs_prompts': {'files': {'RS01_CNL_V6/prompt.json': row}}}}
        return SimpleNamespace(data={'typescript_regression_inputs': {'siblings': {'rs_prompts': str(self.root)}}},
                               sources=SimpleNamespace(read=lambda unused: json.dumps(profile).encode()))

    def test_exact_explicit_sibling_prompt(self):
        ctx = self.fixture()
        self.assertEqual(self.module.read_input(ctx, 'rs_prompts', 'RS01_CNL_V6/prompt.json'),
                         {'version': 'som.prompt.v6'})

    def test_sibling_source_byte_change_is_refused(self):
        ctx = self.fixture(); (self.root / 'RS01_CNL_V6/prompt.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'complete source profile'):
            self.module.read_input(ctx, 'rs_prompts', 'RS01_CNL_V6/prompt.json')

    def test_sibling_ancestor_escape_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'safe named'):
            self.module.read_input(object(), 'rs_prompts', '../prompt.json')


class FailClosedInventoryTests(unittest.TestCase):
    def test_unreadable_extra_directory_is_never_silently_omitted(self):
        name = '_som_ts_unreadable_inventory_fixture'
        owner = load(name, 'typescript_regression_owner.py')
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); (root / 'known').write_bytes(b'fixed')
                hidden = root / 'unexpected'; hidden.mkdir(); (hidden / 'hidden').write_bytes(b'unknown')
                hidden.chmod(0)
                try:
                    with self.assertRaisesRegex(ValueError, 'unreadable'):
                        owner.tree(root)
                    with self.assertRaisesRegex(ValueError, 'unreadable'):
                        owner.copy_capture(root, root.parent / (root.name + '-copy'))
                finally:
                    hidden.chmod(0o700)
                    import shutil
                    shutil.rmtree(root.parent / (root.name + '-copy'), ignore_errors=True)
        finally:
            sys.modules.pop(name, None)


if __name__ == '__main__':
    unittest.main()
