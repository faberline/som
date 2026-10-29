"""Synthetic replay audit tests. Run only on a GitHub-hosted runner."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import audit as a

def raw(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode()

def metadata():
    run = {'id': a.RUN, 'head_sha': a.HEAD, 'status': 'completed', 'conclusion': 'success', 'run_attempt': 1}
    names = ['Run replay synthetic fixtures', 'Check original run and artifact identities',
             'Download and independently recheck retained public evidence', 'Restore exact retained cache and public tools',
             'Observe offline reference install on Ubuntu 22', 'Preserve raw replay results',
             'Run actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02']
    job = {'id': a.JOB, 'name': 'replay', 'head_sha': a.HEAD, 'status': 'completed', 'conclusion': 'success',
           'steps': [{'name': name, 'conclusion': 'success'} for name in names]}
    artifact = {'id': a.ARTIFACT, 'expired': False, 'size_in_bytes': a.ZIP_BYTES, 'digest': 'sha256:' + a.ZIP_SHA,
                'workflow_run': {'id': a.RUN, 'head_sha': a.HEAD}}
    log = ('replay\tSet up job\tTIME ##[group]Operating System\nreplay\tSet up job\tTIME Ubuntu\n'
           'replay\tSet up job\tTIME 22.04.5\nreplay\tSet up job\tTIME Image: ubuntu-22.04\n'
           'replay\tSet up job\tTIME Version: 20261025.340.1\n').encode()
    return run, {'jobs': [job]}, artifact, log

def small_snapshot():
    digest = hashlib.sha256(b'cache').hexdigest()
    doc = {'root_mode': 0o555, 'entries': {'data': {'kind': 'directory', 'mode': 0o555},
           'data/item': {'kind': 'file', 'mode': 0o444, 'sha256': digest}}}
    tree = {'sha256': a.sha(('data/item\0' + digest + '\n').encode()), 'files': 1, 'symlinks': 0}
    return doc, tree

def make_tar(extra=None):
    paths = {'probe/report.json', 'probe/cache-before.json', 'probe/cache-after.json',
             'probe/product/package.json', 'probe/product/package-lock.json',
             'probe/product/node_modules/@rolldown/binding-linux-x64-gnu/package.json',
             'probe/product/node_modules/lightningcss-linux-x64-gnu/package.json'}
    paths.update('probe/' + name + '.' + suffix for name in
                 ('glibc-version', 'node-version', 'npm-version', 'node-elf', 'node-elf-versions', 'npm-ci-offline', 'prettier-version')
                 for suffix in ('stdout', 'stderr'))
    payloads = {name: b'synthetic' for name in paths}
    payloads.update({'probe/source-replay/bin/node': b'node',
                     'probe/source-replay/lib/node_modules/npm/bin/npm-cli.js': b'npm',
                     'probe/product/node_modules/prettier/bin/prettier.cjs': b'prettier'})
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as archive:
        for name, data in sorted(payloads.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if extra is not None:
            member = tarfile.TarInfo(extra)
            member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
    return buf.getvalue(), payloads

class AuditTests(unittest.TestCase):
    def test_runner_diagnostics_identify_field_without_weakening_checks(self):
        expected = {'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64', 'ImageOS': 'ubuntu22',
                    'ImageVersion': '20261026.341.1', 'GITHUB_RUN_ID': str(a.RUN), 'GITHUB_RUN_ATTEMPT': '1'}
        report = {'runner': expected.copy(), 'os_release_sha256': 'a' * 64}
        a.runner_metadata(report)
        report['runner']['ImageVersion'] = 'bad\nversion'
        with self.assertRaises(a.AuditError) as caught:
            a.runner_metadata(report)
        self.assertIn('runner.ImageVersion differs', str(caught.exception))
        self.assertIn('bad\\nversion', str(caught.exception))
        self.assertNotIn('\n', str(caught.exception))
        report['runner']['ImageVersion'] = '20261025.340.1'
        with self.assertRaises(a.AuditError):
            a.runner_metadata(report)
        report['runner'] = expected.copy()
        del report['runner']['ImageOS']
        with self.assertRaisesRegex(a.AuditError, 'missing=.*ImageOS'):
            a.runner_metadata(report)
        report['runner'] = expected.copy()
        report['runner']['unexpected'] = 'fixture'
        with self.assertRaisesRegex(a.AuditError, 'extra_count=1'):
            a.runner_metadata(report)
        report['runner'] = expected.copy()
        report['os_release_sha256'] = 'bad'
        with self.assertRaisesRegex(a.AuditError, 'os_release_sha256 invalid'):
            a.runner_metadata(report)

    def test_metadata_identity_and_missing_step(self):
        run, jobs, artifact, log = metadata()
        self.assertEqual(a.metadata(run, jobs, artifact, log), a.sha(log))
        with self.assertRaises(a.AuditError):
            a.metadata(run, jobs, artifact, log.replace(b'20261025.340.1', b'20261026.341.1'))
        wrong_attempt = dict(run, run_attempt=2)
        with self.assertRaises(a.AuditError):
            a.metadata(wrong_attempt, jobs, artifact, log)
        wrong_artifact = dict(artifact, digest='sha256:' + '0' * 64)
        with self.assertRaises(a.AuditError):
            a.metadata(run, jobs, wrong_artifact, log)
        jobs['jobs'][0]['steps'].pop()
        with self.assertRaises(a.AuditError):
            a.metadata(run, jobs, artifact, log)
        run['head_sha'] = '0' * 40
        with self.assertRaises(a.AuditError):
            a.metadata(run, jobs, artifact, log)

    def test_cache_maps_modes_and_tree(self):
        doc, tree = small_snapshot()
        with mock.patch.object(a, 'CACHE_TREE', tree):
            self.assertEqual(a.cache_snapshot(doc), tree)
            doc['entries']['data/item']['mode'] = 0o644
            with self.assertRaises(a.AuditError):
                a.cache_snapshot(doc)
            doc['entries']['data/item']['mode'] = 0o444
            doc['entries']['data/item']['sha256'] = '0' * 64
            with self.assertRaises(a.AuditError):
                a.cache_snapshot(doc)

    def test_cache_rejects_missing_parent_and_traversal(self):
        doc, tree = small_snapshot()
        with mock.patch.object(a, 'CACHE_TREE', tree):
            del doc['entries']['data']
            with self.assertRaises(a.AuditError):
                a.cache_snapshot(doc)
            doc['entries']['../escape'] = {'kind': 'file', 'mode': 0o444, 'sha256': '0' * 64}
            with self.assertRaises(a.AuditError):
                a.cache_snapshot(doc)

    def test_safe_tar_checks_complete_tool_trees_and_hash(self):
        content, payloads = make_tar()
        npm = {'sha256': a.sha(('bin/npm-cli.js\0' + a.sha(b'npm') + '\n').encode()), 'files': 1, 'symlinks': 0}
        prettier = {'sha256': a.sha(('bin/prettier.cjs\0' + a.sha(b'prettier') + '\n').encode()), 'files': 1, 'symlinks': 0}
        with mock.patch.object(a, 'NODE_SHA', a.sha(b'node')), mock.patch.object(a, 'NPM_TREE', npm), mock.patch.object(a, 'PRETTIER_TREE', prettier):
            got, digest, size = a.inspect_tar(io.BytesIO(content))
            self.assertEqual(digest, a.sha(content))
            self.assertEqual(size, len(content))
            self.assertEqual(got['probe/report.json'], payloads['probe/report.json'])
            invalid, _ = make_tar('../escape')
            with self.assertRaises(a.AuditError):
                a.inspect_tar(io.BytesIO(invalid))

    def test_json_and_paths_fail_closed(self):
        for text in (b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff'):
            with self.subTest(text=text), self.assertRaises(a.AuditError):
                a.json_strict(text)
        for name in ('../escape', '/absolute', 'a//b', 'a/../b', 'a\\b'):
            with self.subTest(name=name), self.assertRaises(a.AuditError):
                a.path_name(name)

    def test_command_receipts_check_all_streams_and_exit_codes(self):
        root = '/home/runner/work/_temp/som-react-linux-qa-closure'
        node, npm = root + '/node/bin/node', root + '/node/lib/node_modules/npm/bin/npm-cli.js'
        samples = [
            ('glibc-version', ['/usr/bin/getconf', 'GNU_LIBC_VERSION'], b'glibc 2.35\n'),
            ('node-version', [node, '--version'], b'v26.5.0\n'),
            ('npm-version', [node, npm, '--version'], b'11.17.0\n'),
            ('node-elf', ['/usr/bin/readelf', '-d', node], b'(NEEDED) libz.so\n'),
            ('node-elf-versions', ['/usr/bin/readelf', '--version-info', node], b'GLIBC_2.35\n'),
            ('npm-ci-offline', [node, npm, 'ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'], b'installed\n'),
            ('prettier-version', [node, root + '/probe/product/node_modules/prettier/bin/prettier.cjs', '--version'], b'3.9.10\n')]
        files, records = {}, []
        for name, argv, stdout in samples:
            files['probe/' + name + '.stdout'] = stdout
            files['probe/' + name + '.stderr'] = b''
            records.append({'name': name, 'argv': argv, 'outcome': 'exited', 'exit_code': 0,
                            'stdout_sha256': a.sha(stdout), 'stderr_sha256': a.sha(b'')})
        report = {'commands': records, 'node_version': 'v26.5.0', 'npm_version': '11.17.0', 'prettier_version': '3.9.10', 'glibc': 'glibc 2.35'}
        self.assertEqual(a.receipts(files, report), 7)
        files['probe/npm-ci-offline.stderr'] = b'changed'
        with self.assertRaises(a.AuditError):
            a.receipts(files, report)
        files['probe/npm-ci-offline.stderr'] = b''
        records[5]['exit_code'] = 1
        with self.assertRaises(a.AuditError):
            a.receipts(files, report)

    def test_zip_receipts_and_tar_binding(self):
        snapshot, cache = small_snapshot()
        producer = {'version': 'som.react-linux-qa-offline-probe.v1', 'state': 'offline-reference-install-observed',
                    'product_acceptance_passed': False, 'cache_tree': cache, 'npm_tree': a.NPM_TREE,
                    'node_version': 'v26.5.0', 'npm_version': '11.17.0', 'prettier_version': '3.9.10'}
        producer_raw = raw(producer)
        source_audit = {'version': 'som.react-linux-independent-audit.v1', 'state': 'partial-evidence-consistent',
                        'product_acceptance_passed': False, 'source_commit': a.PRODUCER, 'tar_sha256': a.SOURCE_TAR,
                        'package_count': 123, 'node_license_count': 152, 'cache_tree': cache,
                        'npm_tree': a.NPM_TREE, 'prettier_tree': a.PRETTIER_TREE}
        restore = {'state': 'public-source-restored', 'producer_head': a.PRODUCER, 'source_tar_sha256': a.SOURCE_TAR,
                   'artifact_id': 12873066929, 'run_id': 40817465924, 'product_acceptance_passed': False}
        producer_run = {'id': 40817465924, 'head_sha': a.PRODUCER, 'conclusion': 'success', 'status': 'completed'}
        audit_run = {'id': 40818025549, 'head_sha': '34aca02762745daa2e6d42b588ae54c80a2c7df8', 'conclusion': 'success', 'status': 'completed'}
        producer_artifact = {'id': 12873066929, 'digest': 'sha256:' + a.SOURCE_ZIP, 'size_in_bytes': 523384472,
                             'expired': False, 'workflow_run': {'id': 40817465924, 'head_sha': a.PRODUCER}}
        formula = {'node': a.NODE_SHA, 'libnode': None, 'prettier_tree_sha256': a.PRETTIER_TREE['sha256']}
        canon = json.dumps(formula, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        report = {'version': 'som.react-linux-qa-ubuntu22-replay.v1', 'state': 'offline-reference-install-observed',
                  'product_acceptance_passed': False, 'producer_head': a.PRODUCER, 'verifier_head': a.HEAD,
                  'source_tar_sha256': a.SOURCE_TAR, 'producer_report_sha256': a.sha(producer_raw),
                  'runner': {'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64', 'ImageOS': 'ubuntu22',
                             'ImageVersion': '20261026.341.1', 'GITHUB_RUN_ID': str(a.RUN), 'GITHUB_RUN_ATTEMPT': '1'},
                  'os_release_sha256': 'f' * 64, 'node_sha256': a.NODE_SHA, 'node_archive_sha256': a.NODE_ARCHIVE,
                  'npm_tree': a.NPM_TREE, 'cache_tree': cache, 'prettier_tree': a.PRETTIER_TREE,
                  'react_bundle_formatter_sha256': a.FORMATTER, 'node_version': producer['node_version'],
                  'npm_version': producer['npm_version'], 'prettier_version': producer['prettier_version'],
                  'qa_formatter_formula': formula, 'qa_formatter_sha256': a.sha(canon)}
        tar_raw = b'synthetic tar payload'
        package, lock = b'synthetic package', b'synthetic lock'
        captured = {'probe/report.json': raw(report), 'probe/cache-before.json': raw(snapshot),
                    'probe/cache-after.json': raw(snapshot), 'probe/product/package.json': package,
                    'probe/product/package-lock.json': lock,
                    'probe/product/node_modules/@rolldown/binding-linux-x64-gnu/package.json': raw({'name': '@rolldown/binding-linux-x64-gnu', 'version': '1.2.15'}),
                    'probe/product/node_modules/lightningcss-linux-x64-gnu/package.json': raw({'name': 'lightningcss-linux-x64-gnu', 'version': '1.33.0'})}
        entries = {a.TAR_NAME: tar_raw, a.TAR_NAME + '.sha256': (a.sha(tar_raw) + '  /tmp/' + a.TAR_NAME + '\n').encode(),
                   'react-ubuntu22-source-audit.json': raw(source_audit),
                   'som-react-linux-qa-closure/restore-receipt.json': raw(restore),
                   'som-react-linux-qa-closure/producer-report.json': producer_raw,
                   'react-producer-run.json': raw(producer_run), 'react-audit-run.json': raw(audit_run),
                   'react-producer-artifact.json': raw(producer_artifact)}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        blob = buf.getvalue()
        root = Path(tempfile.mkdtemp(prefix='som-ubuntu22-audit-', dir=os.environ['RUNNER_TEMP']))
        path = root / 'replay.zip'
        path.write_bytes(blob)
        with mock.patch.object(a, 'ZIP_SHA', a.sha(blob)), mock.patch.object(a, 'ZIP_BYTES', len(blob)), \
             mock.patch.object(a, 'CACHE_TREE', cache), mock.patch.object(a, 'PACKAGE_SHA', a.sha(package)), \
             mock.patch.object(a, 'LOCK_SHA', a.sha(lock)), mock.patch.object(a, 'receipts', return_value=7), \
             mock.patch.object(a, 'inspect_tar', return_value=(captured, a.sha(tar_raw), len(tar_raw))):
            run, jobs, artifact, log = metadata()
            result = a.audit(path, run, jobs, artifact, log)
            self.assertEqual(result['state'], 'partial-evidence-consistent')
            self.assertFalse(result['product_acceptance_passed'])
            self.assertEqual(result['runner_image_evidence'], {'setup_log_version': '20261025.340.1',
                'reported_environment_version': '20261026.341.1', 'mismatch': True, 'cause': 'unknown',
                'actual_image_identity': 'unverified'})
            self.assertEqual(result['source_tar_sha256'], a.SOURCE_TAR)
            path.write_bytes(blob[:-1] + b'x')
            with self.assertRaises(a.AuditError):
                a.audit(path, run, jobs, artifact, log)

if __name__ == '__main__':
    unittest.main()
