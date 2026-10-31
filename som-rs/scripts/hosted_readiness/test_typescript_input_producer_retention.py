#!/usr/bin/env python3
"""GitHub-only fixtures for exact TS producer retention source.

Run with -I -S -B. Raw source loads are fixed by whole-file hashes below.
These fixtures do not qualify Node, encryption, the native Context, or products.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import traceback
import types
import unittest
from unittest import mock

if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and sys.platform == 'linux'):
    raise RuntimeError('this fixture requires actual GitHub Linux -I -S -B startup')

BASE = 'som-rs/scripts/hosted_readiness/'
DEPENDENCIES = {
    BASE + 'typescript_input_producer.py': '675098eee2967e0ba71a62331a9958ba710b7147f212ec2b5370c70dd5c77c53',
    BASE + 'typescript_input_consumer.py': '0ccdb2731974619ea448c50b93429cedd6b6f27b41f7c07598750eb32e10ed18',
}
ROOT = Path(__file__).absolute().parents[3]
CANARY = 'PRIVATE_FIXTURE_KEY_TOKEN_ERROR_76ecff22'
LABELS = {
    'producer': 'ts-producer-evidence', 'original19': 'ts-original19',
    'wire-packet': 'packet', 'current-packet': 'ts-current-packet',
    'semantic-sealed': 'ts-role-sealed', 'approved-inputs': 'approved-inputs',
}


def exact_module(case, name, relative):
    path = ROOT / relative
    case.assertFalse(any(p.is_symlink() for p in (path, *path.parents)))
    info = path.lstat()
    case.assertTrue(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    raw = path.read_bytes()
    case.assertEqual(hashlib.sha256(raw).hexdigest(), DEPENDENCIES[relative])
    case.assertNotIn(name, sys.modules)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ''
    sys.modules[name] = module
    case.addCleanup(sys.modules.pop, name, None)
    try:
        exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


class FixtureSources:
    """Finite authority seam. Verify real fixture files against saved bytes.

    These two inert JS files are never executed. This is not SourceBytes,
    a SourceSeal, an admitted Context, or an authorization stub for main().
    """
    def __init__(self, root, producer, data):
        self.root, self.revision = root, 'a' * 40
        self.data = data
        self.names = (producer.SEAL, producer.M6 + 'sealed_inputs.mjs')
        self.expected = {name: ('// inert fixture source: ' + name + '\n').encode() for name in self.names}
        for name, raw in self.expected.items():
            data.write(root / name, raw)
        self.reads, self.verifications = [], 0
        self.changed = RuntimeError('fixture pinned source changed: ' + CANARY)

    def read(self, name):
        if name not in self.expected:
            raise AssertionError('undeclared fixture source read')
        self.reads.append(name)
        raw = self.data.raw_file(self.root / name)
        if raw != self.expected[name]:
            raise self.changed
        return raw

    def verify(self):
        self.verifications += 1
        for name, raw in self.expected.items():
            if self.data.raw_file(self.root / name) != raw:
                raise self.changed


class ProducerRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='som-ts-retention-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = exact_module(self, '_som_fixture_retention_consumer', BASE + 'typescript_input_consumer.py')
        self.producer = exact_module(self, '_som_fixture_retention_producer', BASE + 'typescript_input_producer.py')
        self.sources = FixtureSources(self.root / 'source', self.producer, self.data)
        self.native_node, self.native_runner = self.data._node, self.data._run_owned
        self.node = self.root / 'ts-node-tools/bin/node'
        self.data.write(self.node, b'inert fixture Node boundary; never executed\n', 0o700)

    def tearDown(self):
        self.assertIs(self.data._node, self.native_node)
        self.assertIs(self.data._run_owned, self.native_runner)
        for relative, pin in DEPENDENCIES.items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), pin)

    def node_boundary(self, sources, node):
        self.assertIs(sources, self.sources)
        self.assertEqual(Path(node), self.node)
        return self.node_record(node)

    def node_record(self, node):
        path = self.data.physical(node)
        info = path.stat()
        pin, size = self.data.hash_file(path)
        return {'path': str(path), 'sha256': pin, 'bytes': size,
                'mode': stat.S_IMODE(info.st_mode), 'identity': list(self.data.file_identity(info)),
                'executable_inode': {'device': info.st_dev, 'inode': info.st_ino, 'size': info.st_size}}

    @contextlib.contextmanager
    def boundaries(self, runner, node=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(self.data, '_node', node or self.node_boundary))
            stack.enter_context(mock.patch.object(self.data, '_run_owned', runner))
            stack.enter_context(mock.patch.dict(os.environ, {
                'M6_INPUTS_KEYRING': 'fixture-only-keyring-' + CANARY,
                'TS_PRODUCER_PRIOR_STATUS': 'failure',
            }))
            yield

    def raw_boundary(self, evidence, primary):
        evidence.mkdir(mode=0o700)
        self.data.write(evidence / 'stdout.raw', b'fixture seal boundary stdout\n')
        self.data.write(evidence / 'stderr.raw', ('fixture private failure: ' + CANARY).encode())
        self.data.dump(evidence / 'command.json', {
            'boundary': 'not executed; explicit crypto boundary fixture',
            'first_error': {'type': type(primary).__name__, 'message': str(primary)},
            'environment': {'M6_INPUTS_KEYRING': CANARY},
        })

    def caught(self, expected, call, frame):
        try:
            call()
        except BaseException as error:
            self.assertIs(error, expected)
            self.assertIn(frame, [row.name for row in traceback.extract_tb(error.__traceback__)])
            return error
        self.fail('the original exception must propagate')

    def abort_retention_at_crypto(self, mutate=None):
        primary = RuntimeError('original private seal failure: ' + CANARY)
        def abort_seal(argv, cwd, evidence, environment, executable, **kwargs):
            self.assertEqual(list(map(str, argv)), list(map(str, [self.node, '--no-addons',
                self.sources.root / self.producer.SEAL, '--root', self.root / 'som-ts-producer-retention/packet',
                '--out', self.root / 'som-ts-producer-retained-upload/sealed'])))
            self.assertEqual(cwd, evidence.parent)
            self.assertEqual(environment['M6_INPUTS_KEYRING'], 'fixture-only-keyring-' + CANARY)
            self.raw_boundary(evidence, primary)
            if mutate is not None:
                mutate(evidence)
            raise primary
        with self.boundaries(abort_seal):
            self.caught(primary, lambda: self.producer.retain_producer_raw(self.sources, self.data, self.root), 'abort_seal')
        return primary

    def original_roots(self):
        roots = {key: self.root / relative for key, relative in LABELS.items()}
        for root in roots.values():
            root.mkdir(mode=0o700)
        return roots

    def archive_view(self):
        work = self.root / 'som-ts-producer-retention'
        transport = self.data.parse(self.data.raw_file(work / 'packet/run/transport.json'))
        pieces, joined = [], bytearray()
        for number, row in enumerate(transport['ordered_chunks']):
            self.assertEqual(row['name'], f'{number:06d}.part')
            raw = self.data.raw_file(work / 'packet/run/chunks' / row['name'])
            self.assertEqual((hashlib.sha256(raw).hexdigest(), len(raw)), (row['sha256'], row['bytes']))
            pieces.append(row['name']); joined.extend(raw)
        self.assertEqual(set(pieces), {p.name for p in (work / 'packet/run/chunks').iterdir()})
        self.assertEqual((hashlib.sha256(joined).hexdigest(), len(joined)),
                         (transport['archive_sha256'], transport['archive_bytes']))
        self.assertEqual(bytes(joined), self.data.raw_file(work / 'archive.tar'))
        self.assertFalse(transport['alias_instantiation'])
        self.assertFalse(transport['product_acceptance_passed'])
        with tarfile.open(fileobj=io.BytesIO(joined), mode='r:') as archive:
            members = archive.getmembers()
            self.assertTrue(all(m.isfile() and not m.pax_headers and m.mode == 0o600 and m.mtime == 0 for m in members))
            self.assertEqual(len({m.name for m in members}), len(members))
            raw = {m.name: archive.extractfile(m).read() for m in members}
        manifest = self.data.parse(raw.pop('manifest.json'))
        self.assertEqual(set(raw), set(manifest['file_slots'].values()))
        self.assertEqual(transport['source_inventory_sha256'], self.data.sha(self.data.canonical(manifest['nodes'])))
        self.assertFalse(manifest['authenticated_archive_view_admitted'])
        return manifest, raw, transport

    def actual_closed_exporter(self):
        """Actual short Python child; fixture postcheck, no Node/crypto claim."""
        work = self.root / 'exporter-work'; work.mkdir()
        upload = self.root / 'exporter-upload'; upload.mkdir()
        executable = Path(sys.executable).resolve()
        info = executable.stat()
        inode = {'device': info.st_dev, 'inode': info.st_ino, 'size': info.st_size}
        program = "import os,sys,time; print('actual short child stdout'); sys.stderr.write('private stderr '+os.environ['FIXTURE_PRIVATE_CANARY']+'\\n'); time.sleep(0.25)"
        argv = [executable, '-I', '-S', '-B', '-c', program]
        record = self.data._run_owned(argv, work, work / 'seal-command',
            {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'FIXTURE_PRIVATE_CANARY': CANARY}, inode, timeout=15)
        self.assertTrue(record['wait_recorded'] and record['owned_group_gone'])
        self.assertEqual(record['exit_code'], 0)
        post = {'complete': True, 'source_postcheck_passed': True,
                'before': {'fixture': 'generic owned Python child, not Node qualification', 'executable_inode': inode}}
        post['after'] = copy.deepcopy(post['before'])
        self.data.dump(work / 'seal-command/postcheck.json', post)
        return work, upload, argv, record, post

    def exported(self, work, upload, argv, completed):
        with mock.patch.dict(os.environ, {'TS_PRODUCER_PRIOR_STATUS': 'failure'}):
            self.producer.exporter_control(self.sources, self.data, self.root, work, upload, argv, completed)
        return self.data.parse(self.data.raw_file(upload / 'exporter-control.json'))

    def test_late_origin_all_six_roots_full_bytes_modes_and_ordered_chunks(self):
        roots = self.original_roots()
        files = {
            'producer/receipt.json': b'{"late":"receipt"}\n', 'producer/origin.json': b'{"late":"origin"}\n',
            'producer/uploaded.zip': b'whole inert ZIP fixture bytes\x00\xff',
            'producer/restore/package/command.json': b'{"actual_saved_fixture":"wait"}\n',
            'producer/restore/package/stdout.raw': b'original restore stdout\n',
            'producer/restore/package/stderr.raw': ('original private stderr ' + CANARY).encode(),
            'producer/seal/postcheck.json': b'{"original":"postcheck"}\n',
            'original19/evidence/package-018/command.json': b'{"ordinal":18}\n',
            'wire-packet/run/raw.bin': b'wire\x00', 'current-packet/run/raw.bin': b'current\xff',
            'semantic-sealed/evidence-index.json': b'{"cipher":"inert"}\n',
            'approved-inputs/package/LICENSE': b'original license bytes\n',
        }
        for name, raw in files.items():
            label, relative = name.split('/', 1)
            self.data.write(roots[label] / relative, raw, 0o640 if name.endswith('stderr.raw') else 0o600)
        large = roots['producer'] / 'large-raw.bin'
        with large.open('xb') as stream:
            for _ in range(32):
                stream.write(b'0123456789abcdef' * (1024**2 // 16))
            stream.write(b'last original bytes\n')
        before = self.producer.retention_inventory(roots, self.data)
        primary = self.abort_retention_at_crypto()
        manifest, raw, transport = self.archive_view()
        self.assertGreaterEqual(len(transport['ordered_chunks']), 2)
        self.assertEqual(manifest['nodes'], before)
        self.assertEqual(self.producer.retention_inventory(roots, self.data), before)
        for name, expected in files.items():
            self.assertEqual(raw[manifest['file_slots'][name]], expected)
        for name, slot in manifest['file_slots'].items():
            self.assertEqual((self.data.sha(raw[slot]), len(raw[slot])),
                             (before[name]['sha256'], before[name]['size']))
        self.assertEqual(before['producer/restore/package/stderr.raw']['mode'], 0o640)
        self.assertFalse(any('seal-command' in name or 'som-ts-producer-retention' in name for name in manifest['nodes']))
        control_raw = self.data.raw_file(self.root / 'som-ts-producer-retained-upload/exporter-control.json')
        control = self.data.parse(control_raw)
        self.assertFalse(control['retention_complete'])
        self.assertFalse(control['private_archive_seal_completed'])
        self.assertNotIn(CANARY.encode(), control_raw)
        self.assertEqual(str(primary), 'original private seal failure: ' + CANARY)

    def test_aliases_are_only_opaque_metadata_and_never_external_bytes(self):
        roots = self.original_roots()
        outside = self.root / 'outside'; outside.mkdir()
        self.data.write(outside / 'secret.raw', ('EXTERNAL_NOT_ARCHIVED_' + CANARY).encode())
        (roots['producer'] / 'file-alias').symlink_to(outside / 'secret.raw')
        (roots['producer'] / 'directory-alias').symlink_to(outside, target_is_directory=True)
        (roots['producer'] / 'dangling-alias').symlink_to(self.root / 'missing-target')
        self.data.write(roots['producer'] / 'kept.raw', b'kept original')
        self.abort_retention_at_crypto()
        manifest, raw, _ = self.archive_view()
        for name in ('file-alias', 'directory-alias', 'dangling-alias'):
            key = 'producer/' + name
            self.assertEqual(manifest['nodes'][key]['type'], 'opaque-alias-data')
            self.assertEqual(manifest['nodes'][key]['target'], os.readlink(roots['producer'] / name))
            self.assertNotIn(key, manifest['file_slots'])
        self.assertFalse(any('directory-alias/' in key for key in manifest['nodes']))
        self.assertFalse(any(b'EXTERNAL_NOT_ARCHIVED_' in value for value in raw.values()))
        self.assertEqual(self.data.raw_file(outside / 'secret.raw'), ('EXTERNAL_NOT_ARCHIVED_' + CANARY).encode())

    def test_fifo_hardlink_and_alias_root_fail_before_crypto_without_fake_complete(self):
        for kind in ('fifo', 'hardlink', 'root-alias'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as directory:
                temporary = Path(directory)
                source = temporary / 'ts-producer-evidence'; source.mkdir()
                if kind == 'fifo':
                    os.mkfifo(source / 'special')
                elif kind == 'hardlink':
                    self.data.write(source / 'original', b'not single-link')
                    os.link(source / 'original', source / 'second')
                else:
                    source.rmdir(); source.symlink_to(self.root / 'source', target_is_directory=True)
                local_node = temporary / 'ts-node-tools/bin/node'
                self.data.write(local_node, b'inert local Node boundary, never executed', 0o700)
                def local_node_boundary(sources, node):
                    self.assertIs(sources, self.sources)
                    self.assertEqual(Path(node), local_node)
                    return self.node_record(node)
                runner = mock.Mock(side_effect=AssertionError('crypto must not start'))
                with self.boundaries(runner, local_node_boundary):
                    with self.assertRaises(ValueError):
                        self.producer.retain_producer_raw(self.sources, self.data, temporary)
                runner.assert_not_called()
                control = self.data.parse(self.data.raw_file(temporary / 'som-ts-producer-retained-upload/exporter-control.json'))
                self.assertFalse(control['retention_complete'])
                self.assertFalse(control['private_archive_seal_completed'])
                self.assertFalse((temporary / 'som-ts-producer-retained-upload/sealed').exists())

    def test_seal_body_primary_survives_real_node_change_and_receipt_collision(self):
        evidence = self.root / 'seal-case'
        primary = RuntimeError('body first ' + CANARY)
        def body(argv, cwd, target, environment, executable):
            self.raw_boundary(target, primary)
            self.node.write_bytes(b'changed inert fixture node')
            self.data.write(target / 'postcheck.json', b'actual earlier colliding receipt')
            raise primary
        with self.boundaries(body):
            self.caught(primary, lambda: self.producer.seal_packet(self.sources, self.data, self.node,
                self.root / 'packet', self.root / 'sealed', evidence), 'body')
        self.assertEqual(primary.__som_ts_seal_postcheck_error__, 'ValueError')
        self.assertEqual(primary.__som_ts_seal_postcheck_receipt_error__, 'FileExistsError')
        self.assertIn(CANARY.encode(), self.data.raw_file(evidence / 'stderr.raw'))
        self.assertEqual(self.data.raw_file(evidence / 'postcheck.json'), b'actual earlier colliding receipt')

    def test_seal_source_failure_stays_first_when_receipt_also_fails(self):
        evidence = self.root / 'seal-case'
        def body(argv, cwd, target, environment, executable):
            target.mkdir()
            self.data.write(target / 'stdout.raw', b'closed fixture stdout')
            self.data.write(target / 'stderr.raw', b'closed fixture stderr')
            (self.sources.root / self.producer.SEAL).write_bytes(b'changed pinned source')
            self.data.write(target / 'postcheck.json', b'actual colliding receipt')
        with self.boundaries(body):
            self.caught(self.sources.changed, lambda: self.producer.seal_packet(self.sources, self.data, self.node,
                self.root / 'packet', self.root / 'sealed', evidence), 'verify')
        self.assertGreater(self.sources.verifications, 0)
        self.assertEqual(self.sources.changed.__som_ts_seal_postcheck_receipt_error__, 'FileExistsError')
        self.assertEqual(self.sources.reads, list(self.sources.names))

    def test_seal_success_retains_actual_boundary_call_and_final_postcheck(self):
        evidence = self.root / 'seal-case'; calls = []
        before = self.node_boundary(self.sources, self.node)
        def body(argv, cwd, target, environment, executable):
            calls.append((list(argv), cwd, target, dict(environment), dict(executable)))
            target.mkdir()
            self.data.write(target / 'stdout.raw', b'boundary success, no crypto executed')
            self.data.write(target / 'stderr.raw', b'')
        with self.boundaries(body):
            self.producer.seal_packet(self.sources, self.data, self.node, self.root / 'packet', self.root / 'sealed', evidence)
        self.assertEqual(len(calls), 1)
        argv, cwd, target, environment, inode = calls[0]
        self.assertEqual(argv, [self.node, '--no-addons', self.sources.root / self.producer.SEAL,
                                '--root', self.root / 'packet', '--out', self.root / 'sealed'])
        self.assertEqual((cwd, target, inode), (evidence.parent, evidence, before['executable_inode']))
        self.assertEqual(set(environment) - {'HOME', 'TMPDIR'}, {'PATH', 'LANG', 'LC_ALL', 'M6_INPUTS_KEYRING'})
        post = self.data.parse(self.data.raw_file(evidence / 'postcheck.json'))
        self.assertEqual(post['before'], before)
        self.assertEqual(post['after'], before)
        self.assertTrue(post['complete'] and post['source_postcheck_passed'])
        self.assertFalse(post['product_acceptance_passed'])
        self.assertEqual(self.sources.verifications, 1)

    def test_actual_closed_child_control_has_waits_and_unarchived_raw_hashes(self):
        work, upload, argv, record, _ = self.actual_closed_exporter()
        control = self.exported(work, upload, argv, True)
        self.assertTrue(control['exporter_evidence_verified'] and control['retention_complete'])
        self.assertEqual(control['actual_argv'], list(map(str, argv)))
        self.assertEqual(control['owned_start']['parent_pid'], os.getpid())
        self.assertEqual(control['owned_start']['pid'], record['pid'])
        self.assertEqual(control['pid'], record['owned_start']['pgrp'])
        self.assertEqual(control['pid'], record['owned_start']['session'])
        self.assertEqual(control['wait_attempts'], record['wait_attempts'])
        self.assertFalse(Path('/proc', str(record['pid'])).exists())
        for name, row in control['raw_exporter_observations'].items():
            self.assertEqual(row['publication'], 'UNARCHIVED')
            self.assertEqual((row['sha256'], row['bytes']), self.data.hash_file(work / 'seal-command' / name))
        self.assertEqual({p.name for p in upload.iterdir()}, {'exporter-control.json'})
        self.assertIn(CANARY.encode(), self.data.raw_file(work / 'seal-command/stderr.raw'))
        self.assertNotIn(CANARY.encode(), self.data.raw_file(upload / 'exporter-control.json'))

    def test_control_rejects_actual_record_field_and_raw_stream_mutations(self):
        work, _, argv, original, post = self.actual_closed_exporter()
        mutations = {
            'argv': lambda r: r.update(argv=['foreign', CANARY]),
            'parent': lambda r: r['owned_start'].update(parent_pid=os.getpid() + 1),
            'group': lambda r: r['owned_start'].update(pgrp=r['pid'] + 1),
            'session': lambda r: r['owned_start'].update(session=r['pid'] + 1),
            'inode': lambda r: r['owned_start']['executable_inode'].update(inode=0),
            'wait': lambda r: r.update(wait_recorded=False),
            'wait-attempt': lambda r: r.update(wait_attempts=[{'stage': 'foreign', 'completed': True, 'exit_code': 0}]),
            'group-gone': lambda r: r.update(owned_group_gone=False),
            'cleanup': lambda r: r.update(cleanup_errors=[{'message': CANARY}]),
            'exit': lambda r: r.update(exit_code=9),
            'stdout-hash': lambda r: r.update(stdout_sha256='0' * 64),
            'stderr-size': lambda r: r.update(stderr_bytes=r['stderr_bytes'] + 1),
            'cwd': lambda r: r.update(cwd=str(work / 'foreign')),
            'malformed-start': lambda r: r['owned_start'].update(executable_inode={'secret': CANARY}),
        }
        for label, change in mutations.items():
            with self.subTest(label=label):
                record = copy.deepcopy(original)
                record['first_error'] = {'message': CANARY}
                record['environment'] = {'M6_INPUTS_KEYRING': CANARY}
                change(record)
                (work / 'seal-command/command.json').write_bytes(self.data.canonical(record))
                upload = self.root / ('negative-' + label); upload.mkdir()
                with self.assertRaisesRegex(ValueError, 'complete actual exporter control observations required'):
                    self.exported(work, upload, argv, True)
                raw = self.data.raw_file(upload / 'exporter-control.json')
                control = self.data.parse(raw)
                self.assertFalse(control['retention_complete'])
                self.assertFalse(control['exporter_evidence_verified'])
                self.assertNotIn(CANARY.encode(), raw)
        (work / 'seal-command/command.json').write_bytes(self.data.canonical(original))
        for label in ('source', 'after'):
            changed = copy.deepcopy(post)
            if label == 'source': changed['source_postcheck_passed'] = False
            else: changed['after']['executable_inode']['inode'] += 1
            (work / 'seal-command/postcheck.json').write_bytes(self.data.canonical(changed))
            upload = self.root / ('negative-post-' + label); upload.mkdir()
            with self.assertRaises(ValueError): self.exported(work, upload, argv, True)
            self.assertFalse(self.data.parse(self.data.raw_file(upload / 'exporter-control.json'))['retention_complete'])

    def test_completed_false_never_claims_complete_even_with_real_waited_child(self):
        work, upload, argv, _, _ = self.actual_closed_exporter()
        control = self.exported(work, upload, argv, False)
        self.assertTrue(control['exporter_evidence_verified'])
        self.assertFalse(control['private_archive_seal_completed'])
        self.assertFalse(control['retention_complete'])
        self.assertEqual(control['producer_job_prior_status'], 'failure')
        self.assertFalse(control['authenticated_archive_view_admitted'])
        self.assertFalse(control['product_acceptance_passed'])

    def test_malformed_secret_command_stays_private_and_missing_proof_fails_closed(self):
        work = self.root / 'malformed'; (work / 'seal-command').mkdir(parents=True)
        (work / 'seal-command/postcheck.json').write_bytes(('{"keyring":"' + CANARY + '", bad JSON').encode())
        expected = [self.node, '--fixture-only-never-executed']
        for label, command in (
                ('malformed', ('{"keyring":"' + CANARY + '", bad JSON').encode()),
                ('list', self.data.canonical([CANARY, {'environment': CANARY}])),
                ('fields', self.data.canonical({'argv': [CANARY], 'pid': CANARY,
                    'owned_start': {'pid': CANARY}, 'cleanup_errors': [{'message': CANARY}],
                    'first_error': {'message': CANARY}, 'environment': {'TOKEN': CANARY}}))):
            (work / 'seal-command/command.json').write_bytes(command)
            upload = self.root / ('public-' + label); upload.mkdir()
            control = self.exported(work, upload, expected, False)
            raw = self.data.raw_file(upload / 'exporter-control.json')
            self.assertNotIn(CANARY.encode(), raw)
            self.assertIsNone(control['actual_argv'])
            self.assertIsNone(control['owned_start'])
            self.assertFalse(control['retention_complete'])
            self.assertFalse(control['postcheck_passed'])
            self.assertEqual(control['raw_exporter_observations']['command.json']['publication'], 'UNARCHIVED')
            self.assertEqual(control['raw_exporter_observations']['command.json']['sha256'], self.data.sha(command))
        upload = self.root / 'missing-must-refuse'; upload.mkdir()
        with self.assertRaises(ValueError): self.exported(work, upload, expected, True)
        self.assertFalse(self.data.parse(self.data.raw_file(upload / 'exporter-control.json'))['retention_complete'])

    def test_no_node_keeps_original_exception_and_no_fake_archive_or_crypto(self):
        temporary = self.root / 'pre-node'; temporary.mkdir()
        errors = []
        def missing_node(sources, path):
            self.assertIs(sources, self.sources)
            try:
                self.data.physical(path)
            except FileNotFoundError as error:
                errors.append(error)
                raise
            self.fail('the actual node path must be absent')
        runner = mock.Mock(side_effect=AssertionError('no crypto before a qualified node'))
        with self.boundaries(runner, missing_node):
            try:
                self.producer.retain_producer_raw(self.sources, self.data, temporary)
            except FileNotFoundError as error:
                self.assertIs(error, errors[0])
                self.assertIn('missing_node', [r.name for r in traceback.extract_tb(error.__traceback__)])
            else:
                self.fail('missing node must remain a failure')
        runner.assert_not_called()
        control = self.data.parse(self.data.raw_file(temporary / 'som-ts-producer-retained-upload/exporter-control.json'))
        self.assertFalse(control['actual_command_record_observed'])
        self.assertFalse(control['retention_complete'])
        self.assertFalse((temporary / 'som-ts-producer-retention/archive.tar').exists())
        self.assertFalse((temporary / 'som-ts-producer-retained-upload/sealed').exists())
        self.assertFalse(control['raw_exporter_observations']['stderr.raw']['observed'])

    def test_original_failure_survives_secondary_public_control_write_failure(self):
        roots = self.original_roots()
        self.data.write(roots['producer'] / 'failure.stderr.raw', ('first private failure ' + CANARY).encode())
        def collision(evidence):
            self.data.write(self.root / 'som-ts-producer-retained-upload/exporter-control.json', b'existing private collision')
        primary = self.abort_retention_at_crypto(collision)
        self.assertEqual(primary.__som_ts_retention_control_error__, 'FileExistsError')
        manifest, raw, _ = self.archive_view()
        self.assertEqual(raw[manifest['file_slots']['producer/failure.stderr.raw']], ('first private failure ' + CANARY).encode())
        self.assertIn(CANARY.encode(), self.data.raw_file(self.root / 'som-ts-producer-retention/seal-command/stderr.raw'))
        self.assertEqual(self.data.raw_file(self.root / 'som-ts-producer-retained-upload/exporter-control.json'), b'existing private collision')


if __name__ == '__main__':
    unittest.main()
