"""Whole Git data-reader fixtures. Run only on a GitHub runner.

The workflow must supply the fixed old commit before this file runs.
Missing history is an error. It is never a skipped positive test.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zlib

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SOURCE = HERE / 'python_backend_source_data.py'
SOURCE_SHA = '144c662a236b51e953827de8a0ca4ea4078c2eab57d2d5d9c037bc5d572a961f'
BEFORE = '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af'
OLD_PATHS = {
    'Cargo.lock': 'som-rs/Cargo.lock',
    'Cargo.toml': 'som-rs/Cargo.toml',
    'block-contracts/composed-quality.json': 'som-code-python/data/block-contracts/composed-quality.json',
    'block-contracts/composed.json': 'som-code-python/data/block-contracts/composed.json',
    'block-contracts/flow-v7-compact.json': 'som-code-python/data/block-contracts/flow-v7-compact.json',
    'block-contracts/flow-v7.json': 'som-code-python/data/block-contracts/flow-v7.json',
    'contracts/python-literal-encoding.v1.json': 'som-rs/contracts/python-literal-encoding.v1.json',
    'contracts/python-quality-v1/pyproject.toml': 'som-rs/contracts/python-quality-v1/pyproject.toml',
    'contracts/related-business.v1.md': 'som-rs/contracts/related-business.v1.md',
    'contracts/related-flow.v1.md': 'som-rs/contracts/related-flow.v1.md',
    'flow_compact/engine.py.tpl': 'som-core/block_templates/flow_compact/engine.py.tpl',
    'flow_compact/forward_support.py.tpl': 'som-core/block_templates/flow_compact/forward_support.py.tpl',
    'src/business.rs': 'som-rs/src/business.rs',
    'src/cnl.rs': 'som-rs/src/cnl.rs',
    'src/cnl_semantics.rs': 'som-rs/src/cnl_semantics.rs',
    'src/compiler.rs': 'som-rs/src/compiler.rs',
    'src/composed.rs': 'som-rs/src/composed.rs',
    'src/flow.rs': 'som-rs/src/flow.rs',
    'src/flow_compact.rs': 'som-rs/src/flow_compact.rs',
    'src/json_diagnostics.rs': 'som-rs/src/json_diagnostics.rs',
    'src/lib.rs': 'som-rs/src/lib.rs',
    'src/main.rs': 'som-rs/src/main.rs',
    'src/planner.rs': 'som-rs/src/planner.rs',
    'src/python_names.rs': 'som-rs/src/python_names.rs',
    'src/quality.rs': 'som-rs/src/quality.rs',
    'src/related.rs': 'som-rs/src/related.rs',
    'src/strict_json.rs': 'som-rs/src/strict_json.rs',
}
CURRENT_PATHS = {**OLD_PATHS,
    'contracts/native-tools-v1/CONTRACT.md': 'som-rs/contracts/native-tools-v1/CONTRACT.md',
    'contracts/native-tools-v1/artifacts.json': 'som-rs/contracts/native-tools-v1/artifacts.json'}
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
       'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
       'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}


def git(root, *args, data=None, check=True):
    return subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(root), *args],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV,
        timeout=30, check=check)


def commit(root):
    git(root, '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Source data fixture',
        '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '--allow-empty',
        '-m', 'owned temporary source-data fixture')


class BackendSourceDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
            raise RuntimeError('this whole fixture must run on a GitHub-hosted runner')
        raw = SOURCE.read_bytes()
        if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
            raise RuntimeError('whole source reader fixture pin differs')
        spec = importlib.util.spec_from_file_location('_som_backend_source_data_fixture', SOURCE)
        cls.reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.reader)
        # A missing old object must fail setup, never hide a positive test as skipped.
        git(REPO, 'cat-file', '-e', BEFORE + '^{commit}')
        cls.head = git(REPO, 'rev-parse', '--verify', 'HEAD').stdout.decode().strip()

    def clone(self):
        temp = tempfile.TemporaryDirectory(prefix='som-backend-data-')
        self.addCleanup(temp.cleanup)
        parent = Path(temp.name)
        root = parent / 'checkout'
        git(parent, 'clone', '--shared', '--no-checkout', '--', str(REPO), str(root))
        git(root, 'read-tree', 'HEAD')
        git(root, 'checkout-index', '--', *sorted(set(CURRENT_PATHS.values())))
        return root

    def change(self, root, name, raw):
        path = root / name
        path.write_bytes(raw)
        git(root, 'add', '--', name)
        commit(root)

    def oracle(self, root, result, paths):
        self.assertEqual(len(result['entries']), len(paths))
        by_name = {row['logical']: row for row in result['entries']}
        self.assertEqual(set(by_name), set(paths))
        hashes = {}
        for logical, name in paths.items():
            entry = git(root, 'ls-tree', '-z', result['revision'], '--', name).stdout
            header, actual = entry[:-1].split(b'\t')
            mode, kind, oid = header.decode().split()
            self.assertEqual((mode, kind, actual), ('100644', 'blob', name.encode()))
            raw = git(root, 'cat-file', 'blob', oid).stdout
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual(dict(by_name[logical]), {'logical': logical, 'path': name,
                'blob_oid': oid, 'mode': mode, 'bytes': len(raw), 'sha256': digest})
            hashes[logical] = digest
        manifest = json.dumps({'version': 'som.compiler-backend.v1', 'files': hashes},
                              sort_keys=True, separators=(',', ':')).encode()
        self.assertEqual(result['manifest_bytes'], manifest)
        self.assertEqual(result['backend_sha256'], hashlib.sha256(manifest).hexdigest())
        tree = git(root, 'rev-parse', result['revision'] + '^{tree}').stdout.decode().strip()
        self.assertEqual(result['tree_oid'], tree)

    def test_complete_fixed_old27_tree_blobs_and_independent_manifest(self):
        result = self.reader.read_before_backend(REPO)
        self.assertEqual(result['revision'], BEFORE)
        self.oracle(REPO, result, OLD_PATHS)
        self.assertEqual(result['backend_sha256'], '581ce38f29702c300342cdefe8fe5f1735cefcd1869d6485b44f30063a866711')
        self.assertFalse(result['physical_current_sources_checked'])

    def test_complete_actual_head29_and_physical_sources(self):
        result = self.reader.read_current_backend(REPO, self.head)
        self.assertEqual(result['revision'], self.head)
        self.oracle(REPO, result, CURRENT_PATHS)
        self.assertTrue(result['physical_current_sources_checked'])

    def test_current_selection_must_be_full_actual_head(self):
        for revision in (BEFORE, self.head[:8], self.head.upper(), True, '0' * 40):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.reader.read_current_backend(REPO, revision)

    def test_current_digest_is_recomputed_after_real_committed_source_change(self):
        root = self.clone()
        before = self.reader.read_current_backend(root)
        name = 'som-rs/src/python_names.rs'
        self.change(root, name, (root / name).read_bytes() + b'\n// source-data fixture change\n')
        after = self.reader.read_current_backend(root)
        self.oracle(root, after, CURRENT_PATHS)
        self.assertNotEqual(before['revision'], after['revision'])
        self.assertNotEqual(before['backend_sha256'], after['backend_sha256'])
        self.assertEqual(before['factory_region_sha256'], after['factory_region_sha256'])

    def test_dirty_physical_bytes_are_refused(self):
        root = self.clone(); path = root / 'som-rs/src/cnl.rs'
        path.write_bytes(path.read_bytes() + b'\n// dirty\n')
        with self.assertRaisesRegex(ValueError, 'physical source differs'):
            self.reader.read_current_backend(root)

    def test_wrong_physical_mode_is_refused(self):
        root = self.clone(); (root / 'som-rs/src/cnl.rs').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'mode/type/size'):
            self.reader.read_current_backend(root)

    def test_symbolic_source_and_parent_are_refused(self):
        for parent in (False, True):
            with self.subTest(parent=parent):
                root = self.clone()
                path = root / ('som-rs/src' if parent else 'som-rs/src/cnl.rs')
                saved = path.with_name(path.name + '-original')
                path.rename(saved); path.symlink_to(saved, target_is_directory=parent)
                with self.assertRaisesRegex(ValueError, 'symbolic link'):
                    self.reader.read_current_backend(root)

    def test_symbolic_repository_root_is_refused(self):
        root = self.clone(); link = root.parent / 'alias'; link.symlink_to(root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            self.reader.read_before_backend(link)

    def test_missing_fixed_old_objects_refuses_without_fetch_or_skip(self):
        temp = tempfile.TemporaryDirectory(prefix='som-no-old-object-'); self.addCleanup(temp.cleanup)
        root = Path(temp.name); git(root, 'init', '--quiet'); commit(root)
        with self.assertRaisesRegex(ValueError, 'immutable Git data is unavailable'):
            self.reader.read_before_backend(root)
        self.assertFalse((root / '.git/FETCH_HEAD').exists())
        self.assertNotEqual(git(root, 'cat-file', '-e', BEFORE, check=False).returncode, 0)

    def test_invalid_utf8_in_a_real_committed_blob_refuses(self):
        root = self.clone()
        self.change(root, 'som-rs/src/cnl.rs', b'\xff\xfe')
        with self.assertRaises(UnicodeDecodeError):
            self.reader.read_current_backend(root)

    def test_git_symlink_and_submodule_entries_refuse(self):
        for mode in ('120000', '160000'):
            with self.subTest(mode=mode):
                root = self.clone(); name = 'som-rs/src/cnl.rs'
                oid = self.head if mode == '160000' else git(root, 'hash-object', '-w', '--stdin', data=b'outside.rs').stdout.decode().strip()
                git(root, 'update-index', '--cacheinfo', mode + ',' + oid + ',' + name); commit(root)
                with self.assertRaisesRegex(ValueError, 'regular source Git blob'):
                    self.reader.read_current_backend(root)

    def test_changed_owning_factory_refuses(self):
        root = self.clone(); name = 'som-rs/src/compiler.rs'
        raw = (root / name).read_bytes().replace(b'let files = [', b'let files = /* changed factory */ [', 1)
        self.change(root, name, raw)
        with self.assertRaisesRegex(ValueError, 'backend factory changed'):
            self.reader.read_current_backend(root)

    def test_missing_complete_vector_source_refuses(self):
        root = self.clone()
        git(root, 'rm', '--cached', '--', 'som-rs/src/cnl.rs'); commit(root)
        with self.assertRaisesRegex(ValueError, 'tree entry is missing'):
            self.reader.read_current_backend(root)

    def test_computed_alias_in_committed_owner_refuses(self):
        root = self.clone(); name = 'som-rs/src/quality.rs'; raw = (root / name).read_bytes()
        old = b'pub const PROFILE_TEXT: &str = include_str!("../contracts/python-quality-v1/pyproject.toml");'
        self.assertEqual(raw.count(old), 1)
        self.change(root, name, raw.replace(old, b'pub const PROFILE_TEXT: &str = concat!("not", "a literal include");'))
        with self.assertRaisesRegex(ValueError, 'literal original include alias'):
            self.reader.read_current_backend(root)

    def test_alias_lexer_ignores_comments_strings_and_raw_strings(self):
        source = b'''/* pub const PROFILE_TEXT: &str = include_str!("wrong"); */
const UNUSED: &str = r###"pub const PROFILE_TEXT: &str = include_str!("wrong");"###;
pub const PROFILE_TEXT: &str = include_str!("../contracts/python-quality-v1/pyproject.toml");
'''
        self.assertEqual(self.reader._resolve_alias('quality', 'PROFILE_TEXT', source),
                         'som-rs/contracts/python-quality-v1/pyproject.toml')
        computed = source.replace(b'pub const PROFILE_TEXT: &str = include_str!("../contracts/python-quality-v1/pyproject.toml");',
                                  b'pub const PROFILE_TEXT: &str = compute();')
        with self.assertRaisesRegex(ValueError, 'literal original include alias'):
            self.reader._resolve_alias('quality', 'PROFILE_TEXT', computed)

    def test_duplicate_escaped_and_unknown_aliases_refuse(self):
        original = b'pub const PROFILE_TEXT: &str = include_str!("../contracts/python-quality-v1/pyproject.toml");\n'
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
            self.reader._resolve_alias('quality', 'PROFILE_TEXT', original * 2)
        with self.assertRaisesRegex(ValueError, 'escaped or computed'):
            self.reader._resolve_alias('quality', 'PROFILE_TEXT', original.replace(b'pyproject', b'pypro\\x6aect'))
        with self.assertRaisesRegex(ValueError, 'unreviewed backend alias'):
            self.reader._resolve_alias('unknown', 'PROFILE_TEXT', original)

    def test_include_paths_cannot_escape_repository(self):
        for include in ('../../../../outside', '/tmp/outside', '../bad\\name', '../a//b'):
            with self.subTest(include=include), self.assertRaises(ValueError):
                self.reader._resolve_include('som-rs/src/compiler.rs', include)

    def test_source_data_is_deeply_immutable_and_grants_no_runtime_authority(self):
        result = self.reader.read_before_backend(REPO)
        with self.assertRaises(TypeError): result['revision'] = '0' * 40
        with self.assertRaises(TypeError): result['entries'][0]['sha256'] = '0' * 64
        self.assertIs(type(result['entries']), tuple)
        self.assertIs(type(result['manifest_bytes']), bytes)
        for name in ('native_authority', 'SourceSeal', 'product_acceptance_passed'):
            self.assertIs(result[name], False)
        with self.assertRaises(TypeError):
            self.reader.read_current_backend(REPO, expected_digest='0' * 64)

    def test_corrupt_owned_intermediate_tree_refuses_even_when_worktree_matches(self):
        root = self.clone()
        tree_oid = git(root, 'rev-parse', 'HEAD:som-rs/src').stdout.decode().strip()
        tree = git(root, 'cat-file', 'tree', tree_oid).stdout
        original = git(root, 'rev-parse', 'HEAD:som-rs/src/cnl.rs').stdout.decode().strip()
        other = git(root, 'rev-parse', 'HEAD:som-rs/src/python_names.rs').stdout.decode().strip()
        marker = b'100644 cnl.rs\0' + bytes.fromhex(original)
        self.assertEqual(tree.count(marker), 1)
        corrupt = tree.replace(marker, b'100644 cnl.rs\0' + bytes.fromhex(other))
        # Override only this owned clone's loose object. Shared original objects stay read-only.
        target = root / '.git/objects' / tree_oid[:2] / tree_oid[2:]
        self.assertFalse(target.exists())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zlib.compress(b'tree ' + str(len(corrupt)).encode() + b'\0' + corrupt))
        (root / 'som-rs/src/cnl.rs').write_bytes(git(root, 'cat-file', 'blob', other).stdout)
        with self.assertRaises(ValueError):
            self.reader.read_current_backend(root)


if __name__ == '__main__':
    unittest.main()
