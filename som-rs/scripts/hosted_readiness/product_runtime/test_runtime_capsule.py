"""GitHub-only synthetic capsule tests. No installed program runs."""
from __future__ import annotations
import copy
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise SystemExit('Run capsule fixtures only on GitHub Linux')
HERE = Path(__file__).absolute().parent
REPO = HERE.parents[3]
NAME = 'som-rs/scripts/hosted_readiness/product_runtime/runtime_capsule.py'
REVISION = os.environ['HELPER_REVISION']
RAW = (REPO / NAME).read_bytes()
assert RAW == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
    'cat-file', 'blob', REVISION + ':' + NAME])
c = ModuleType('runtime_capsule_under_test')
c.__file__, c.__cached__, c.__package__ = str(REPO / NAME), None, ''
sys.modules[c.__name__] = c
exec(compile(RAW, c.__file__, 'exec', dont_inherit=True), c.__dict__)


class CapsuleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'qualified'
        self.root.mkdir(mode=0o700)
        (self.root / 'install').mkdir(mode=0o755)
        (self.root / 'install/bin').mkdir(mode=0o755)
        (self.root / 'install/bin/tool').write_bytes(b'\x7fELF never executed')
        (self.root / 'install/bin/tool').chmod(0o755)
        (self.root / 'install/bin/alias').symlink_to('tool')
        (self.root / 'receipt.json').write_bytes(b'{"immutable":"qualified"}\n')
        (self.root / 'github.env').write_bytes(b'SOM_PRODUCT_UV=/fixed/uv\n')
        self.aliases = {'install/bin/alias': 'tool'}
        self.entries = c.scan_tree(self.root, self.aliases)
        self.archive = self.base / 'runtime.tar'
        c.pack_archive(self.root, self.archive, self.entries)
        self.identity = {'repository': 'faberline/som', 'run_id': 1234, 'run_attempt': 2}
        self.revision = 'a' * 40
        self.profile = {'fixture_profile': True, 'aliases': self.aliases}
        self.sources = SimpleNamespace(repo=self.base / 'repo', revision=self.revision,
            pins={name: 'b' * 64 for name in c.SOURCE_PATHS}, raw={name: b'{}' for name in c.SOURCE_PATHS},
            verify=mock.Mock(), validator=mock.Mock(return_value=SimpleNamespace(
                qualification_data=lambda *_: {'install_prefix': str(self.root), 'aliases': self.aliases})))
        self.sources.repo.mkdir()
        self.env = mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'faberline/som',
            'GITHUB_RUN_ID': '1234', 'GITHUB_RUN_ATTEMPT': '2', 'RUNNER_TEMP': str(self.base)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_duplicate_and_nonfinite_metadata_are_refused(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): c.parse(raw)

    def test_unsafe_paths_are_refused(self):
        for name in ('../x', '/x', 'a//b', 'a/./b', 'a\\b', 'a\n'):
            with self.subTest(name=name), self.assertRaises(ValueError): c.relative(name)

    def test_symlink_hardlink_and_fifo_reads_are_refused(self):
        source = self.root / 'install/bin/tool'
        with self.assertRaises(ValueError): c.read_file(self.root / 'install/bin/alias')
        os.link(source, self.base / 'hardlink')
        with self.assertRaises(ValueError): c.read_file(source)
        fifo = self.base / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError): c.read_file(fifo)

    def test_regular_file_bounds_are_enforced(self):
        with self.assertRaises(ValueError): c.read_file(self.root / 'receipt.json', 2)

    def test_unknown_alias_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'alias'): c.scan_tree(self.root, {})

    def test_escaping_alias_is_refused(self):
        path = self.root / 'install/bin/alias'
        path.unlink()
        path.symlink_to('../../../outside')
        (self.base / 'outside').write_bytes(b'outside')
        with self.assertRaises(ValueError): c.scan_tree(self.root, {'install/bin/alias': '../../../outside'})

    def test_linked_directory_is_refused(self):
        (self.root / 'linked-directory').symlink_to(self.root / 'install', target_is_directory=True)
        with self.assertRaises(ValueError): c.scan_tree(self.root, self.aliases)

    def test_changed_modes_and_root_mode_are_refused(self):
        (self.root / 'install/bin/tool').chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'mode'): c.scan_tree(self.root, self.aliases)
        self.root.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'root mode'): c.scan_tree(self.root, self.aliases)

    def test_full_inventory_detects_extra_and_missing_file(self):
        (self.root / 'extra').write_bytes(b'new')
        self.assertNotEqual(c.scan_tree(self.root, self.aliases), self.entries)
        (self.root / 'extra').unlink()
        (self.root / 'receipt.json').unlink()
        self.assertNotEqual(c.scan_tree(self.root, self.aliases), self.entries)

    def test_entry_schema_refuses_unknown_fields_and_boolean_integers(self):
        for mutate in ({'unexpected': 1}, {'mode': True}, {'size': True}):
            bad = copy.deepcopy(self.entries)
            bad['install/bin/tool'].update(mutate)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): c.validate_entries(bad)

    def test_alias_requires_regular_sibling_and_directory_parents(self):
        for action in ('target', 'parent'):
            bad = copy.deepcopy(self.entries)
            if action == 'target': bad['install/bin/alias']['target'] = 'missing'
            else: bad.pop('install/bin')
            with self.subTest(action=action), self.assertRaises(ValueError): c.validate_entries(bad)

    def test_archive_roundtrip_preserves_all_bytes_modes_and_exact_alias(self):
        c.validate_archive(self.archive, self.entries)
        restored = self.base / 'restored'
        c.unpack_archive(self.archive, restored, self.entries)
        self.assertEqual(c.scan_tree(restored, self.aliases), self.entries)
        self.assertTrue((restored / 'install/bin/alias').is_symlink())
        self.assertEqual(os.readlink(restored / 'install/bin/alias'), 'tool')
        self.assertEqual((restored / 'receipt.json').read_bytes(), (self.root / 'receipt.json').read_bytes())

    def test_long_license_path_uses_USTAR_without_PAX(self):
        name = 'licenses/' + 'publisher/' * 14 + 'LICENSE'
        header = c.tar_header(name, {'kind': 'file', 'mode': 0o644, 'size': 1, 'sha256': c.digest(b'x')})
        raw = header.tobuf(format=tarfile.USTAR_FORMAT, encoding='utf-8', errors='strict')
        parsed = tarfile.TarInfo.frombuf(raw, 'utf-8', 'strict')
        self.assertEqual(parsed.name, name)
        self.assertEqual(parsed.pax_headers, {})

    def test_unencodable_path_fails_closed_without_fallback(self):
        with self.assertRaises(ValueError): c.tar_header('x' * 256, {'kind': 'directory', 'mode': 0o755})

    def rewrite_archive(self, mutate):
        headers = []
        with tarfile.open(self.archive, 'r:') as archive:
            for header in archive:
                headers.append((header, archive.extractfile(header).read() if header.isfile() else None))
        replacement = self.base / 'changed.tar'
        with tarfile.open(replacement, 'w:', format=tarfile.PAX_FORMAT) as archive:
            for header, body in mutate(headers):
                archive.addfile(header, io.BytesIO(body) if body is not None else None)
        return replacement

    def test_archive_rejects_changed_file_bytes(self):
        changed = self.rewrite_archive(lambda rows: [(header, b'Z' * len(body) if body else body) for header, body in rows])
        with self.assertRaises(ValueError): c.validate_archive(changed, self.entries)

    def test_archive_rejects_missing_extra_and_duplicate_members(self):
        extra = tarfile.TarInfo('extra')
        extra.mode = 0o644
        for mutate in (lambda rows: rows[:-1], lambda rows: rows + [(extra, b'')], lambda rows: rows + rows[:1]):
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                c.validate_archive(self.rewrite_archive(mutate), self.entries)

    def test_archive_rejects_PAX_headers(self):
        def mutate(rows):
            rows[0][0].pax_headers = {'path': rows[0][0].name}
            return rows
        with self.assertRaises(ValueError): c.validate_archive(self.rewrite_archive(mutate), self.entries)

    def test_archive_rejects_hardlink_and_wrong_alias_target(self):
        for kind in ('hardlink', 'target'):
            def mutate(rows):
                for header, _ in rows:
                    if header.issym():
                        if kind == 'hardlink': header.type = tarfile.LNKTYPE
                        else: header.linkname = 'other'
                return rows
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                c.validate_archive(self.rewrite_archive(mutate), self.entries)

    def test_archive_rejects_mode_and_owner_changes(self):
        for field, value in (('mode', 0o777), ('uid', 123), ('mtime', 1)):
            def mutate(rows):
                setattr(rows[0][0], field, value)
                return rows
            with self.subTest(field=field), self.assertRaises(ValueError):
                c.validate_archive(self.rewrite_archive(mutate), self.entries)

    def test_owned_outputs_refuse_existing_overlap_and_escape(self):
        for path in (self.root, self.root / 'nested', self.base / 'repo/out', Path('/tmp/outside-capsule')):
            with self.subTest(path=path), self.assertRaises(ValueError): c.owned_new(path, self.sources.repo, (self.root,))

    def test_actual_run_identity_refuses_other_repository_or_missing_attempt(self):
        for fields in ({'GITHUB_REPOSITORY': 'other/repo'}, {'GITHUB_RUN_ATTEMPT': ''}, {'GITHUB_RUN_ID': '0'}):
            with mock.patch.dict(os.environ, fields), self.assertRaises(ValueError): c.run_identity()

    def test_raw_command_logs_require_actual_zero_wait_and_pid(self):
        (self.root / 'logs').mkdir()
        (self.root / 'logs/out').write_bytes(b'ok')
        (self.root / 'logs/err').write_bytes(b'')
        command = {'pid': 123, 'exit_code': 0, 'stdout': str(self.root / 'logs/out'), 'stderr': str(self.root / 'logs/err'),
                   'stdout_sha256': c.digest(b'ok'), 'stderr_sha256': c.digest(b'')}
        self.assertEqual(len(c.command_logs(self.root, [command])), 2)
        for bad in ({'exit_code': None}, {'exit_code': 1}, {'pid': None}, {'interrupted': 'signal'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): c.command_logs(self.root, [{**command, **bad}])

    def test_raw_command_log_tamper_and_escape_are_refused(self):
        command = {'pid': 123, 'exit_code': 0, 'stdout': str(self.root / 'receipt.json'), 'stderr': str(self.root / 'github.env'),
                   'stdout_sha256': '0' * 64, 'stderr_sha256': '0' * 64}
        with self.assertRaises(ValueError): c.command_logs(self.root, [command])
        command['stdout'] = '/tmp/escaped'
        with self.assertRaises(ValueError): c.command_logs(self.root, [command])

    def test_artifact_API_requires_exact_same_run_upload_metadata(self):
        url = 'https://api.github.com/repos/faberline/som/actions/artifacts/77'
        value = {'id': 77, 'name': c.ARTIFACT_NAME, 'expired': False, 'digest': 'sha256:' + 'c' * 64,
                 'workflow_run': {'id': 1234, 'head_sha': 'merge-context-is-observation'}, 'url': url}
        def invoke(data):
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = c.encoded(data)
            opener = SimpleNamespace(open=lambda *_args, **_kwargs: response)
            with mock.patch.object(c, 'build_opener', return_value=opener):
                return c.verify_artifact(77, 'c' * 64, self.identity)
        self.assertEqual(c.parse(invoke(value)), value)
        for field, replacement in (('id', 78), ('name', 'wrong'), ('expired', True), ('digest', 'sha256:' + 'd' * 64),
                                   ('workflow_run', {'id': 1233})):
            with self.subTest(field=field), self.assertRaises(ValueError): invoke({**value, field: replacement})

    def transport(self):
        capsule = self.base / 'capsule'
        capsule.mkdir()
        (capsule / 'runtime.tar').write_bytes(self.archive.read_bytes())
        manifest = {'version': c.VERSION, 'role': 'same-run-qualified-runtime-transport', 'github': self.identity,
            'helper_revision': self.revision, 'candidate_revision': self.revision, 'source_sha256': self.sources.pins,
            'runtime_prefix': str(self.root), 'profile': self.profile, 'entries': self.entries,
            'archive': {'name': 'runtime.tar', 'sha256': c.digest(self.archive.read_bytes()), 'size': self.archive.stat().st_size},
            'installed_code_executed_by_capsule': False, 'product_runtime_ready': False, 'product_acceptance_passed': False}
        (capsule / 'capsule.json').write_bytes(c.encoded(manifest))
        return capsule, manifest

    def restore(self, capsule, manifest_hash):
        return c.restore_capsule(self.sources.repo, self.revision, self.revision, capsule, self.root,
            self.base / 'evidence', 77, 'c' * 64, manifest_hash)

    def mocks(self):
        return mock.patch.object(c, 'Sources', return_value=self.sources), mock.patch.object(c, 'verify_artifact', return_value=b'{}\n')

    def test_restore_verifies_transport_without_native_execution(self):
        capsule, manifest = self.transport()
        shutil.rmtree(self.root)
        a, b = self.mocks()
        with a, b, mock.patch.object(c, 'validate_runtime', return_value=(self.profile, self.entries)), \
                mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('native child forbidden')):
            result = self.restore(capsule, c.digest(c.encoded(manifest)))
        self.assertEqual(result['status'], 'same_run_qualified_runtime_restored_without_execution')
        self.assertEqual(c.scan_tree(self.root, self.aliases), self.entries)
        self.assertFalse(result['installed_code_executed'])
        self.assertEqual((self.root / 'receipt.json').read_bytes(), b'{"immutable":"qualified"}\n')
        self.assertTrue((self.base / 'evidence/restore.json').is_file())
        self.assertTrue((self.base / 'evidence/artifact-origin.json').is_file())

    def test_bad_manifest_hash_stops_before_unpack(self):
        capsule, _ = self.transport()
        shutil.rmtree(self.root)
        a, b = self.mocks()
        with a, b, mock.patch.object(c, 'unpack_archive') as unpack:
            with self.assertRaises(ValueError): self.restore(capsule, '0' * 64)
            unpack.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_stale_candidate_attempt_and_acceptance_claim_are_refused(self):
        capsule, manifest = self.transport()
        shutil.rmtree(self.root)
        for field, value in (('candidate_revision', 'd' * 40), ('github', {**self.identity, 'run_attempt': 1}),
                             ('product_acceptance_passed', True)):
            altered = {**manifest, field: value}
            (capsule / 'capsule.json').write_bytes(c.encoded(altered))
            a, b = self.mocks()
            with a, b, mock.patch.object(c, 'unpack_archive') as unpack:
                with self.assertRaises(ValueError): self.restore(capsule, c.digest(c.encoded(altered)))
                unpack.assert_not_called()
            shutil.rmtree(self.base / 'evidence')

    def test_changed_archive_stops_before_unpack(self):
        capsule, manifest = self.transport()
        shutil.rmtree(self.root)
        (capsule / 'runtime.tar').write_bytes(self.archive.read_bytes() + b'changed')
        a, b = self.mocks()
        with a, b, mock.patch.object(c, 'unpack_archive') as unpack:
            with self.assertRaises(ValueError): self.restore(capsule, c.digest(c.encoded(manifest)))
            unpack.assert_not_called()

    def test_extra_capsule_file_and_changed_alias_are_refused(self):
        capsule, manifest = self.transport()
        shutil.rmtree(self.root)
        (capsule / 'extra').write_bytes(b'x')
        a, b = self.mocks()
        with a, b, self.assertRaises(ValueError): self.restore(capsule, c.digest(c.encoded(manifest)))
        self.assertFalse((self.base / 'evidence').exists())
        (capsule / 'extra').unlink()
        manifest['entries']['install/bin/alias']['target'] = 'other'
        (capsule / 'capsule.json').write_bytes(c.encoded(manifest))
        a, b = self.mocks()
        with a, b, mock.patch.object(c, 'unpack_archive') as unpack:
            with self.assertRaises(ValueError): self.restore(capsule, c.digest(c.encoded(manifest)))
            unpack.assert_not_called()

    def test_export_source_change_and_cross_source_candidate_fail_closed(self):
        out = self.base / 'export'
        with mock.patch.object(c, 'Sources', return_value=self.sources), \
                mock.patch.object(c, 'validate_runtime', side_effect=[(self.profile, self.entries), ({}, self.entries)]):
            with self.assertRaises(ValueError): c.export_capsule(self.sources.repo, self.revision, self.revision, self.root, out)
        self.assertFalse((out / 'capsule.json').exists())
        with self.assertRaisesRegex(ValueError, 'same-source'):
            c.export_capsule(self.sources.repo, self.revision, 'd' * 40, self.root, self.base / 'cross-source')

    def test_failed_qualified_receipt_blocks_before_inventory_use(self):
        reuse = SimpleNamespace(qualification_data=lambda *_: {'install_prefix': str(self.root)}, read_inventory=mock.Mock())
        with self.assertRaisesRegex(ValueError, 'reuse receipt'):
            c.validate_runtime(self.root, self.sources, reuse)
        reuse.read_inventory.assert_not_called()

    def test_preloaded_validator_is_refused_before_execution(self):
        sources = object.__new__(c.Sources)
        with mock.patch.dict(sys.modules, {'_som_reuse_support': ModuleType('preloaded')}), self.assertRaises(ValueError):
            sources.validator()

    def test_redirect_and_cross_source_restore_are_refused(self):
        with self.assertRaises(ValueError): c.NoRedirect().redirect_request()
        with self.assertRaisesRegex(ValueError, 'same-source'):
            c.restore_capsule(self.sources.repo, self.revision, 'd' * 40, self.base / 'capsule', self.root,
                self.base / 'evidence', 77, 'c' * 64, 'b' * 64)


if __name__ == '__main__':
    unittest.main()
