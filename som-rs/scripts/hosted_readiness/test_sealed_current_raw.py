"""Whole-member M6 transport fixtures. Run only on GitHub Linux."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import ModuleType
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise RuntimeError('GitHub Linux fixtures only')
HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
REVISION = os.environ['HELPER_REVISION']
SELF = 'som-rs/scripts/hosted_readiness/sealed_current_raw.py'


def raw_source(path):
    raw = (REPO / path).read_bytes()
    assert raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
                                          'cat-file', 'blob', REVISION + ':' + path])
    return raw


s = ModuleType('sealed_raw_under_test')
s.__file__, s.__cached__, s.__package__ = str(REPO / SELF), None, ''
exec(compile(raw_source(SELF), s.__file__, 'exec', dont_inherit=True), s.__dict__)


class RawTransportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.raw = self.root / 'raw'; self.raw.mkdir(mode=0o700)
        self.archive = self.root / 'raw.tar'

    def files(self):
        for name, body, mode in [('wire/request.json', b'{"exact":"request"}', 0o600),
                                 ('wire/response.bin', bytes(range(256)), 0o644),
                                 ('native/prettier/node_modules/prettier/index.mjs', b'raw native formatter bytes', 0o755),
                                 ('failure/__pycache__/failed.raw', b'first failure raw bytes', 0o600),
                                 ('native/licenses/' + 'long-' * 19 + '/LICENSE', b'full long license bytes', 0o644)]:
            path = self.raw / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body); path.chmod(mode)
        (self.raw / 'empty').mkdir(mode=0o700, exist_ok=True)

    def test_whole_tree_roundtrip_keeps_modes_long_names_excluded_m6_directory_names_and_empty_dirs(self):
        self.files(); record = s.pack_raw(self.raw, self.archive)
        restored = self.root / 'restored'; s.unpack_raw(self.archive, record, restored)
        self.assertEqual(s.inventory(restored)[0], record['inventory'])
        self.assertEqual((restored / 'failure/__pycache__/failed.raw').read_bytes(), b'first failure raw bytes')
        self.assertTrue((restored / 'native/prettier/node_modules/prettier/index.mjs').exists())
        with tarfile.open(self.archive) as tar: self.assertTrue(all(not m.pax_headers for m in tar.getmembers()))

    def test_symlink_and_hardlink_are_refused_without_following_or_dropping_members(self):
        target = self.root / 'outside'; target.write_bytes(b'protected outside fixture')
        (self.raw / 'alias').symlink_to(target)
        with self.assertRaises(ValueError): s.pack_raw(self.raw, self.archive)
        (self.raw / 'alias').unlink(); os.link(target, self.raw / 'alias')
        with self.assertRaises(ValueError): s.pack_raw(self.raw, self.archive)
        self.assertEqual(target.read_bytes(), b'protected outside fixture')

    def test_path_beyond_ustar_limit_refuses_instead_of_writing_pax(self):
        path = self.raw / ('a' * 120) / ('b' * 120) / ('c' * 120)
        path.parent.mkdir(parents=True); path.write_bytes(b'bounded but unrepresentable name')
        with self.assertRaises(ValueError): s.pack_raw(self.raw, self.archive)

    def test_product_python_alias_is_retained_as_opaque_metadata_and_never_instantiated(self):
        self.files()
        external = self.root / 'qualified-python'; external.write_bytes(b'qualified runtime fixture bytes')
        alias = self.raw / 'observer/.venv/bin/python'
        alias.parent.mkdir(parents=True); alias.symlink_to(external)
        pin = hashlib.sha256(external.read_bytes()).hexdigest()
        record = s.pack_raw(self.raw, self.archive, {str(external): pin})
        saved = [row for row in record['inventory'] if row['kind'] == 'opaque-alias']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['target_sha256'], pin)
        self.assertEqual(saved[0]['target'], str(external))
        self.assertEqual(record['omission_ledger'], [])
        external.unlink()  # Neither archive nor restore can dereference the external target.
        out = self.root / 'restored'; s.unpack_raw(self.archive, record, out)
        self.assertFalse((out / 'observer/.venv/bin/python').exists())
        self.assertFalse((out / 'observer/.venv/bin/python').is_symlink())
        self.assertEqual(s.inventory(out)[0], [row for row in record['inventory'] if row['kind'] != 'opaque-alias'])

    def test_unknown_external_alias_refuses_even_with_registered_product_alias_mode(self):
        alias = self.raw / 'python'; alias.symlink_to('/unregistered/target')
        with self.assertRaisesRegex(ValueError, 'unregistered external'):
            s.pack_raw(self.raw, self.archive, {'/different/qualified/python': '1' * 64})

    def test_duplicate_transport_json_keys_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            s.parse(b'{"archive_sha256":"one","archive_sha256":"two"}')

    def test_actual_product_envelope_contains_exact_context_work_child_and_driver_streams(self):
        work = self.raw / 'som-full-product-run'; work.mkdir()
        context_path = work / 'context.json'
        data = {'work_root': str(work), 'qa_mode': 'native-qa-v1', 'helper_revision': '1' * 40,
                'candidate_revision': '1' * 40, 'stage': 'phase2-python', 'shard': {'index': 0, 'count': 8}}
        context_path.write_text(json.dumps(data)); (self.raw / 'driver.stdout').write_bytes(b'full driver bytes')
        plan = [{'stage': 'phase2-python', 'index': 0, 'count': 8}]
        s.product_scope(self.raw, context_path, json.loads(context_path.read_bytes()),
                        '1' * 40, '1' * 40, 'full-product-phase2-python-0', plan)
        record = s.pack_raw(self.raw, self.archive, {})
        self.assertIn('driver.stdout', [row['path'] for row in record['inventory']])
        self.assertIn('som-full-product-run/context.json', [row['path'] for row in record['inventory']])
        for changed in ('outside', 'wrong-child', 'wrong-shard'):
            bad = {**data, 'shard': dict(data['shard'])}
            if changed == 'outside': bad['work_root'] = str(self.root / 'other')
            if changed == 'wrong-child': bad['work_root'] = str(self.raw / 'other-child')
            if changed == 'wrong-shard': bad['shard']['index'] = 1
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                s.product_scope(self.raw, context_path, bad, '1' * 40, '1' * 40, 'full-product-phase2-python-0', plan)

    def test_product_and_whole_joins_seal_and_restore_as_regular_only_stages(self):
        self.files(); record = s.pack_raw(self.raw, self.archive)
        for stage in ('full-product-join', 'whole-graph-join'):
            with self.subTest(stage=stage):
                self.assertFalse(s.product_shard(stage))
                s.stage_policy(record, stage)
                out = self.root / stage
                s.unpack_raw(self.archive, record, out)
                self.assertEqual(s.inventory(out)[0], record['inventory'])
                with self.assertRaises(ValueError): s.stage_policy({**record, 'opaque_aliases_allowed': True}, stage)
        self.assertTrue(s.product_shard('full-product-phase2-rust-15'))
        with self.assertRaises(ValueError): s.stage_policy(record, 'full-product-phase2-rust-15')

    def test_product_regular_cache_hardlinks_keep_all_bytes_and_original_link_metadata_without_recreating_links(self):
        cache = self.raw / 'product-caches/cache/package.py'
        cache.parent.mkdir(parents=True); cache.write_bytes(b'original ordinary installed regular bytes')
        installed = self.raw / 'som-full-product-run/.venv/lib/package.py'
        installed.parent.mkdir(parents=True); os.link(cache, installed)
        record = s.pack_raw(self.raw, self.archive, {})
        self.assertEqual(record['original_identities']['product-caches/cache/package.py'][3], 2)
        self.assertEqual(record['original_identities']['som-full-product-run/.venv/lib/package.py'][3], 2)
        out = self.root / 'product-restored'; s.unpack_raw(self.archive, record, out)
        for name in ('product-caches/cache/package.py', 'som-full-product-run/.venv/lib/package.py'):
            self.assertEqual((out / name).read_bytes(), cache.read_bytes())
            self.assertEqual((out / name).stat().st_nlink, 1)
        self.assertEqual(s.inventory(out)[0], record['inventory'])
        with self.assertRaises(ValueError): s.hash_file(cache)  # Native/source/packet guards remain single-link.

    def test_mutation_after_member_copy_is_rejected_by_complete_post_inventory(self):
        self.files(); original = tarfile.TarFile.addfile
        def mutate(tar, info, stream=None):
            result = original(tar, info, stream)
            if info.name == 'wire/response.bin': (self.raw / info.name).write_bytes(b'changed')
            return result
        with mock.patch.object(tarfile.TarFile, 'addfile', mutate), self.assertRaisesRegex(ValueError, 'changed'):
            s.pack_raw(self.raw, self.archive)

    def chunk_fixture(self):
        self.files(); record = s.pack_raw(self.raw, self.archive)
        chunks = self.root / 'chunks'
        with mock.patch.object(s, 'CHUNK', 4096): rows = s.split_archive(self.archive, chunks)
        return record, rows, chunks

    def test_exact_chunks_rejoin_whole_archive_and_reject_missing_extra_reorder_and_changed_bytes(self):
        for mutation in ('none', 'missing', 'extra', 'reorder', 'bytes'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                record, rows, chunks = self.chunk_fixture()
                if mutation == 'missing': (chunks / rows[0]['name']).unlink()
                if mutation == 'extra': (chunks / 'unlisted.chunk').write_bytes(b'extra')
                if mutation == 'reorder': rows.reverse()
                if mutation == 'bytes': (chunks / rows[0]['name']).write_bytes(b'changed')
                with mock.patch.object(s, 'CHUNK', 4096):
                    if mutation == 'none':
                        out = Path(temp) / 'joined.tar'
                        s.rejoin_archive(chunks, rows, out, record['archive_bytes'], record['archive_sha256'])
                        self.assertEqual(out.read_bytes(), self.archive.read_bytes())
                    else:
                        with self.assertRaises(ValueError):
                            s.rejoin_archive(chunks, rows, Path(temp) / 'joined.tar', record['archive_bytes'], record['archive_sha256'])
                shutil.rmtree(chunks); self.archive.unlink()

    def test_changed_inventory_or_whole_archive_hash_cannot_be_admitted(self):
        record, rows, chunks = self.chunk_fixture()
        with mock.patch.object(s, 'CHUNK', 4096), self.assertRaises(ValueError):
            s.rejoin_archive(chunks, rows, self.root / 'bad.tar', record['archive_bytes'], '0' * 64)
        record['inventory'][1]['mode'] ^= 1
        with self.assertRaises(ValueError): s.unpack_raw(self.archive, record, self.root / 'bad-tree')

    def test_real_m6_seal_restore_sees_every_chunk_and_keeps_inner_native_prettier_and_failure_bytes(self):
        self.files()
        huge = self.raw / 'native/bin/node-fixture'; huge.parent.mkdir(parents=True)
        with huge.open('wb') as stream: stream.truncate(129 * 1024**2 + 17)
        huge.chmod(0o755)  # Data only. This synthetic fixture executable never runs.
        record = s.pack_raw(self.raw, self.archive)
        hosted = self.root / 'hosted'; transport = hosted / 'run/transport'; transport.mkdir(parents=True)
        record['chunks'] = s.split_archive(self.archive, transport / 'chunks')
        self.assertGreater(record['archive_bytes'], 128 * 1024**2)
        self.assertGreaterEqual(len(record['chunks']), 5)
        self.assertTrue(all(row['bytes'] == 32 * 1024**2 for row in record['chunks'][:-1]))
        (transport / 'transport.json').write_bytes(s.canonical(record))
        node = shutil.which('node'); self.assertIsNotNone(node, 'GitHub fixture Node is required')
        for name in s.SCRIPTS: raw_source(name)
        # The GitHub fixture Node runs only the transport fixture. The production entry still checks the committed Node pin.
        key = bytes(range(32)); ring = {hashlib.sha256(key).hexdigest(): base64.b64encode(key).decode()}
        environment = {**os.environ, 'M6_INPUTS_KEYRING': json.dumps(ring)}
        sealed, restored = self.root / 'sealed', self.root / 'restored-hosted'
        subprocess.run([node, str(REPO / s.SCRIPTS[0]), '--root', str(hosted), '--out', str(sealed)],
                       env=environment, check=True, capture_output=True, timeout=120)
        index_raw = (sealed / 'evidence-index.json').read_bytes()
        index = json.loads(index_raw)
        names = [row['path'] for package in index['packages']
                 for row in json.loads((sealed / package['manifest']).read_bytes())['files']]
        self.assertEqual(set(names), {p.relative_to(hosted).as_posix() for p in hosted.rglob('*') if p.is_file()})
        subprocess.run([node, str(REPO / s.SCRIPTS[1]), '--root', str(sealed), '--out', str(restored),
                        '--index-sha256', hashlib.sha256(index_raw).hexdigest()],
                       env=environment, check=True, capture_output=True, timeout=120)
        restored_transport = restored / 'run/transport'
        unpacked_record = json.loads((restored_transport / 'transport.json').read_bytes())
        joined = self.root / 'joined.tar'
        s.rejoin_archive(restored_transport / 'chunks', unpacked_record['chunks'], joined,
                         record['archive_bytes'], record['archive_sha256'])
        s.unpack_raw(joined, unpacked_record, self.root / 'final')
        self.assertEqual(s.inventory(self.root / 'final')[0], record['inventory'])


    def test_restored_descriptor_keeps_original_root_and_inode_metadata_without_capture_rewrite(self):
        self.files(); record = s.pack_raw(self.raw, self.archive)
        original_raw = s.canonical(record) + b'\n'
        before = {row['path']: row for row in record['inventory']}
        restored = self.root / 'moved-restored'; s.unpack_raw(self.archive, record, restored)
        evidence = self.root / 'descriptor-evidence'; evidence.mkdir()
        descriptor = s.retain_transport_manifest(original_raw, record, restored, evidence)
        self.assertEqual((evidence / 'transport.json').read_bytes(), original_raw)
        self.assertEqual(descriptor['original_raw_root'], str(self.raw))
        self.assertEqual(descriptor['restored_raw_root'], str(restored))
        self.assertFalse(descriptor['archived_read_authority_granted'])
        self.assertEqual(s.inventory(restored)[0], record['inventory'])
        self.assertEqual(s.parse((evidence / 'transport.json').read_bytes())['original_identities'],
                         s.parse(original_raw)['original_identities'])
        self.assertEqual({row['path']: row for row in s.inventory(self.raw)[0]}, before)

    def test_descriptor_refuses_changed_manifest_and_payload_overlap(self):
        self.files(); record = s.pack_raw(self.raw, self.archive)
        raw = s.canonical(record) + b'\n'
        restored = self.root / 'restored'; s.unpack_raw(self.archive, record, restored)
        evidence = self.root / 'descriptor-evidence'; evidence.mkdir()
        changed = dict(record); changed['original_raw_root'] = '/changed/original'
        with self.assertRaisesRegex(ValueError, 'manifest differs'):
            s.retain_transport_manifest(raw, changed, restored, evidence)
        self.assertFalse((evidence / 'transport.json').exists())
        with self.assertRaisesRegex(ValueError, 'outside restored payload'):
            s.retain_transport_manifest(raw, record, restored, restored)
        self.assertEqual(s.inventory(restored)[0], record['inventory'])


if __name__ == '__main__': unittest.main(verbosity=2)
