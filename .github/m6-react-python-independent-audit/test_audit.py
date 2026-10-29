#!/usr/bin/env python3
"""Synthetic checks for GitHub-hosted runners only."""
import copy
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
    raise SystemExit('synthetic tests require a GitHub-hosted runner')

import audit


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def make_tar(items):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as out:
        for name, kind, value in items:
            item = tarfile.TarInfo(name)
            if kind == 'dir':
                item.type = tarfile.DIRTYPE
                out.addfile(item)
            elif kind == 'sym':
                item.type = tarfile.SYMTYPE
                item.linkname = value
                out.addfile(item)
            else:
                item.size = len(value)
                out.addfile(item, io.BytesIO(value))
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode='r:')


def make_zip(items):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        for name, raw in items:
            archive.writestr(name, raw)
    return buf.getvalue()


class ZipChecks(unittest.TestCase):
    def invoke(self, raw, zip_sha=None, size=None, tar_sha=None):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp) / 'input.zip', Path(tmp) / 'out.tar'
            src.write_bytes(raw)
            with mock.patch.object(audit, 'ZIP_SIZE', len(raw) if size is None else size), \
                 mock.patch.object(audit, 'ZIP_SHA', sha(raw) if zip_sha is None else zip_sha), \
                 mock.patch.object(audit, 'TAR_SHA', sha(b'fake tar') if tar_sha is None else tar_sha):
                return audit.unpack(src, dest)

    def test_pin_size_members_and_sidecar(self):
        name = 'react-python-qa-closure.tar'
        items = [(name, b'fake tar'), (name + '.sha256', (sha(b'fake tar') + '  ' + name + '\n').encode())]
        raw = make_zip(items)
        self.assertEqual(self.invoke(raw), sha(b'fake tar'))
        for kwargs in ({'zip_sha': '0' * 64}, {'size': len(raw) + 1}, {'tar_sha': '0' * 64}):
            with self.subTest(kwargs=kwargs), self.assertRaises(audit.AuditError):
                self.invoke(raw, **kwargs)
        for entries in (items + [('extra', b'x')], [items[0], items[0]],
                        [items[0], (name + '.sha256', b'0' * 64 + b'  ' + name.encode() + b'\n')]):
            with self.subTest(entries=entries), self.assertRaises(audit.AuditError):
                self.invoke(make_zip(entries))


class TarChecks(unittest.TestCase):
    def test_cache_links_and_cross_cache(self):
        key = 'a' * 64
        root = audit.QA + '/closures/' + key + '/cache/'
        items = [(root + 'archive-v0/target%02d' % n, 'dir', None) for n in range(80)]
        items += [(root + 'wheels-v5/' + ('index' if n < 40 else 'pypi') + '/item%02d' % n, 'sym',
                   audit.TEMP + '/' + root + 'archive-v0/target%02d' % n) for n in range(80)]
        with mock.patch.object(audit, 'EXPECTED_CACHE_LINKS', 80):
            with make_tar(items) as tar:
                self.assertEqual(sum(m.issym() for m in audit.index_tar(tar).values()), 80)
            for target in (audit.TEMP + '/' + audit.QA + '/closures/' + 'b' * 64 + '/cache/archive-v0/target79', '/etc/passwd'):
                changed = list(items)
                changed[-1] = (changed[-1][0], 'sym', target)
                with self.subTest(target=target), make_tar(changed) as tar, self.assertRaises(audit.AuditError):
                    audit.index_tar(tar)
            with make_tar(items[:-1]) as tar, self.assertRaises(audit.AuditError):
                audit.index_tar(tar)
            changed = list(items)
            changed[-1] = (root + 'wheels-v5/other/item79', 'sym', changed[-1][2])
            with make_tar(changed) as tar, self.assertRaises(audit.AuditError) as caught:
                audit.index_tar(tar)
            detail = json.loads(str(caught.exception).split(': ', 1)[1])
            self.assertEqual(detail, {'name_length':len(changed[-1][0]), 'name_sha256':sha(changed[-1][0].encode()),
                                      'target_length':len(changed[-1][2]), 'target_sha256':sha(changed[-1][2].encode())})
            self.assertNotIn(changed[-1][0], str(caught.exception))
            self.assertNotIn(changed[-1][2], str(caught.exception))
            changed[-1] = (root + 'wheels-v5/other/"' + 'x' * 800, 'sym', changed[-1][2])
            with make_tar(changed) as tar, self.assertRaises(audit.AuditError) as caught:
                audit.index_tar(tar)
            detail = str(caught.exception).split(': ', 1)[1]
            self.assertLess(len(detail), 256)
            self.assertEqual(json.loads(detail)['name_length'], len(changed[-1][0]))
            self.assertNotIn('xxx', detail)
            changed[-1] = (root + 'wheels-v5/pypi/item79', 'sym', audit.TEMP + '/' + root + 'archive-v0/')
            with make_tar(changed) as tar, self.assertRaises(audit.AuditError) as caught:
                audit.index_tar(tar)
            self.assertIn('absolute TAR link leaves pinned cache', str(caught.exception))
            changed[-1] = (root + 'wheels-v5/pypi/item79', 'sym', audit.TEMP + '/' + root + 'archive-v0//')
            with make_tar(changed) as tar, self.assertRaises(audit.AuditError) as caught:
                audit.index_tar(tar)
            self.assertIn('absolute TAR link leaves pinned cache', str(caught.exception))
            changed[-1] = (root + 'wheels-v5/index/item79', 'sym', audit.TEMP + '/' + root + 'archive-v0/target79')
            with make_tar(changed) as tar, self.assertRaises(audit.AuditError):
                audit.index_tar(tar)

    def test_traversal_duplicate_and_link_parent(self):
        cases = [[(audit.QA + '/../escape', 'file', b'x')],
                 [('/' + audit.QA + '/escape', 'file', b'x')],
                 [(audit.QA + '/x', 'file', b'x'), (audit.QA + '/x', 'file', b'y')],
                 [(audit.QA + '/link', 'sym', '../outside'), (audit.QA + '/link/file', 'file', b'x')]]
        with mock.patch.object(audit, 'EXPECTED_CACHE_LINKS', 0):
            for items in cases:
                with self.subTest(items=items), make_tar(items) as tar, self.assertRaises(audit.AuditError):
                    audit.index_tar(tar)


class LockChecks(unittest.TestCase):
    def test_lock_pins_and_hashes(self):
        h = 'a' * 64
        valid = ('Example_Package==1.2.3 --hash=sha256:' + h + '\n').encode()
        self.assertEqual(audit.lock_requirements(valid), {'example-package': {'version': '1.2.3', 'hashes': [h]}})
        for raw in (b'package>=1 --hash=sha256:' + h.encode(), b'package==1 --hash=sha256:bad',
                    b'package==1', valid + valid,
                    ('package==1 --hash=sha256:' + h + ' --hash=sha256:' + h).encode()):
            with self.subTest(raw=raw), self.assertRaises(audit.AuditError):
                audit.lock_requirements(raw)

    def test_direct_requirements_pin_and_digest(self):
        raw = ''.join('package%d==1.0\n' % n for n in range(39)).encode()
        with mock.patch.object(audit, 'PINS', {**audit.PINS, audit.REQ: sha(raw)}):
            self.assertEqual(len(audit.requirements(raw)), 39)
            with self.assertRaises(audit.AuditError):
                audit.requirements(raw + b'extra==1\n')
        broken = raw.replace(b'package0==1.0', b'package0>=1.0')
        with mock.patch.object(audit, 'PINS', {**audit.PINS, audit.REQ: sha(broken)}), self.assertRaises(audit.AuditError):
            audit.requirements(broken)


def command_fixture():
    key = 'a' * 64
    out = audit.TEMP + '/' + audit.QA
    base = out + '/closures/' + key
    project, cache, wheels = base + '/project', base + '/cache', base + '/wheels'
    envroot = out + '/environments/react-api-qa'
    named, seed = envroot + '/venv', base + '/cache-preparation/venv'
    common = ['--python', audit.PYTHON, '--no-managed-python', '--no-config', '--no-build']
    target = ['--python', audit.PYTHON, '--python-version', '3.12.11', '--python-platform', 'x86_64-manylinux_2_35', '--no-managed-python', '--no-config', '--only-binary', ':all:', '--generate-hashes', '--no-header', '--no-annotate', '--format', 'requirements.txt']
    label = 'closure-' + key[:20]
    specs = [
        ([audit.UV, 'lock', *common, '--default-index', 'https://pypi.org/simple'], project, None, False, label + '-universal-lock'),
        ([audit.UV, 'export', '--frozen', '--offline', '--no-config', '--no-emit-project', '--format', 'requirements.txt', '--output-file', base + '/requirements.universal.lock'], project, None, True, label + '-frozen-export'),
        ([audit.UV, 'pip', 'compile', base + '/requirements.universal.lock', *target, '--default-index', 'https://pypi.org/simple', '--output-file', base + '/requirements.target.lock'], project, None, False, label + '-target-lock'),
        ([audit.UV, 'pip', 'compile', base + '/requirements.in', '--constraint', base + '/requirements.selected.lock', *target, '--offline', '--no-index', '--find-links', wheels, '--output-file', base + '/requirements.offline.lock'], project, None, True, label + '-offline-original-closure'),
        ([audit.UV, 'sync', '--frozen', *common, '--no-index', '--find-links', wheels, '--link-mode', 'copy'], project, seed, False, 'cache-' + key + '-frozen-url-cache'),
        ([audit.UV, 'lock', *common, '--offline'], envroot, named, True, 'react-api-qa-offline-original-lock'),
        ([audit.UV, 'sync', '--frozen', *common, '--offline', '--no-index', '--find-links', wheels, '--link-mode', 'copy'], envroot, named, True, 'react-api-qa-offline-frozen-sync'),
        ([audit.PYTHON, '-I', '-S', '-B', out + '/probe-installed-metadata.py', named + '/lib/python3.12/site-packages'], envroot, None, True, 'react-api-qa-offline-no-site-metadata')]
    files, rows = {}, []
    for n, (argv, cwd, venv, offline, name) in enumerate(specs):
        environment = {'PATH': '/home/runner/work/_temp/som-product-runtime-qualification/install/python/bin:/home/runner/work/_temp/som-product-runtime-qualification/install/uv/bin:/usr/bin:/bin',
                       'HOME': '/home/runner/work/_temp/som-react-python-closure/home',
                       'TMPDIR': '/home/runner/work/_temp/som-react-python-closure/tmp',
                       'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
                       'PYTHONNOUSERSITE': '1', 'UV_PYTHON_DOWNLOADS': 'never', 'UV_NO_CONFIG': '1',
                       'UV_NO_BUILD': '1', 'UV_KEYRING_PROVIDER': 'disabled',
                       'UV_DEFAULT_INDEX': 'https://pypi.org/simple', 'UV_CACHE_DIR': cache,
                       'UV_NO_PROGRESS': '1', 'UV_NO_INSTALLER_METADATA': '1', 'UV_COMPILE_BYTECODE': '0'}
        if venv is not None:
            environment['UV_PROJECT_ENVIRONMENT'] = venv
        if offline:
            environment['UV_OFFLINE'] = '1'
        row = {'argv': argv, 'cwd': cwd, 'environment': environment,
               'exit_code': 0, 'owned_pid': 100 + n, 'owned_parent_stopped': True,
               'owned_group_stopped': True, 'elapsed_seconds': 1, 'started_at': 'start', 'finished_at': 'finish'}
        for stream in ('stdout', 'stderr'):
            path = 'logs/' + name + '.' + stream
            raw = ('%s %d\n' % (stream, n)).encode()
            files[audit.QA + '/' + path] = raw
            row[stream] = path
            row[stream + '_bytes'] = len(raw)
            row[stream + '_sha256'] = sha(raw)
        rows.append(row)
    return key, {'commands': rows}, files


class CommandChecks(unittest.TestCase):
    def run_commands(self, report, files, key):
        def read(_tar, _entries, path, limit):
            if path not in files:
                raise audit.Missing(path)
            return files[path]
        with mock.patch.object(audit, 'data', side_effect=read):
            return audit.commands(None, {}, report, key)

    def test_eight_commands_and_order(self):
        key, report, files = command_fixture()
        self.assertEqual(len(self.run_commands(report, files, key)[0]), 8)
        report['commands'][4], report['commands'][5] = report['commands'][5], report['commands'][4]
        with self.assertRaises(audit.AuditError):
            self.run_commands(report, files, key)

    def test_environment_rejects_extra_and_missing_keys(self):
        key, report, files = command_fixture()
        for n in range(8):
            for mutation in ('missing', 'extra'):
                altered = copy.deepcopy(report)
                env = altered['commands'][n]['environment']
                if mutation == 'missing':
                    del env['UV_CACHE_DIR']
                else:
                    env['UNEXPECTED'] = '1'
                with self.subTest(command=n, mutation=mutation), self.assertRaises(audit.AuditError):
                    self.run_commands(altered, files, key)
        altered = copy.deepcopy(report)
        altered['commands'][7]['environment']['UV_PROJECT_ENVIRONMENT'] = 'unexpected'
        with self.assertRaises(audit.AuditError):
            self.run_commands(altered, files, key)

    def test_each_command_rejects_bad_and_missing_receipt(self):
        key, report, files = command_fixture()
        changes = [('exit_code', 1), ('owned_pid', 1), ('owned_parent_stopped', False),
                   ('owned_group_stopped', False), ('stdout_sha256', '0' * 64), ('stderr_bytes', -1),
                   ('stdout', 'logs/wrong'), ('cwd', '/wrong'), ('argv', []), ('environment', {})]
        for n in range(8):
            for field, value in changes:
                altered = copy.deepcopy(report)
                altered['commands'][n][field] = value
                with self.subTest(command=n, field=field), self.assertRaises(audit.AuditError):
                    self.run_commands(altered, files, key)
            for field in ('exit_code', 'owned_parent_stopped', 'owned_group_stopped'):
                altered = copy.deepcopy(report)
                del altered['commands'][n][field]
                with self.subTest(command=n, missing=field), self.assertRaises(audit.AuditError):
                    self.run_commands(altered, files, key)
            altered_files = dict(files)
            altered_files[audit.QA + '/' + report['commands'][n]['stderr']] += b'tampered'
            with self.subTest(command=n, field='raw stderr'), self.assertRaises(audit.AuditError):
                self.run_commands(report, altered_files, key)
            absent = dict(files)
            del absent[audit.QA + '/' + report['commands'][n]['stdout']]
            with self.subTest(command=n, field='missing stdout'), self.assertRaises(audit.Missing):
                self.run_commands(report, absent, key)


class SourceChecks(unittest.TestCase):
    def test_source_digest_and_wait_flow(self):
        producer = (b'child.wait(timeout=1200)\nowned_parent_stopped=child.poll() is not None\n'
                    b'owned_group_stopped=stop_owned_descendants(child.pid)\n'
                    b'require(record["exit_code"] == 0 and record["owned_group_stopped"]\n')
        payloads = {audit.PRODUCER: producer, 'other': b'other'}
        pins = {key: sha(value) for key, value in payloads.items()}
        def show(argv, **kwargs):
            return payloads[argv[-1].split(':', 1)[1]]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(audit, 'PINS', pins), \
             mock.patch.object(audit.subprocess, 'check_output', side_effect=show):
            self.assertEqual(audit.sources(Path(tmp)), payloads)
            payloads[audit.PRODUCER] = producer.replace(b'child.wait(timeout=1200)', b'child.poll()')
            with self.assertRaises(audit.AuditError):
                audit.sources(Path(tmp))
            pins[audit.PRODUCER] = sha(payloads[audit.PRODUCER])
            with self.assertRaises(audit.AuditError):
                audit.sources(Path(tmp))


def runtime_fixture():
    def encoded(value):
        return json.dumps(value, sort_keys=True).encode()
    root = audit.TEMP + '/' + audit.RT
    paths = ['som-rs/scripts/hosted_readiness/product_runtime/reuse_runtime.py',
             'som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py', audit.QUAL, audit.CONTROL, audit.SOURCE_PINS]
    admitted_paths = ['install/python/bin/python3.12', 'install/uv/bin/uv'] + [
        'install/tools/bin/tool%02d' % n for n in range(39)]
    inventory = {name: {'sha256': sha(name.encode())} for name in admitted_paths}
    invraw = encoded(inventory)
    license_rows = {'LICENSE%02d' % n: sha(('license%02d' % n).encode()) for n in range(13)}
    old_receipt = encoded({'python_modules': 'fixture modules\n'})
    transport = {'installed-files.json': sha(invraw), 'receipt.json': sha(old_receipt), 'one': sha(b'one'), 'two': sha(b'two')}
    qualification = {'transport_files': transport, 'admitted_paths': admitted_paths,
                     'counts': {'license_files': 13, 'admitted_executables': 41},
                     'artifact_id': 12, 'artifact_name': 'runtime', 'artifact_sha256': 'a' * 64,
                     'run_id': 42, 'source_revision': 'b' * 40}
    source = {paths[0]: b'reuse source', paths[1]: b'qualify source', audit.QUAL: encoded(qualification),
              audit.CONTROL: encoded({'installed_inventory_sha256': sha(invraw)}),
              audit.SOURCE_PINS: encoded({'archives': [{'name': 'fixture', 'licenses': license_rows}]}),
              audit.POLICY: encoded({'native_sha256': {'python': inventory[admitted_paths[0]]['sha256'],
                                                        'uv': inventory[admitted_paths[1]]['sha256']}})}
    pins = {path: sha(raw) for path, raw in source.items()}
    files = {audit.RT + '/qualification-evidence/installed-files.json': invraw}
    files[audit.RT + '/qualification-evidence/receipt.json'] = old_receipt
    for name in ('one', 'two'):
        files[audit.RT + '/qualification-evidence/' + name] = name.encode()
    entries = {}
    for number in range(13):
        path = audit.RT + '/licenses/fixture/LICENSE%02d' % number
        files[path] = ('license%02d' % number).encode()
        entries[path] = tarfile.TarInfo(path)
    origin = {'id': 12, 'name': 'runtime', 'digest': 'sha256:' + 'a' * 64, 'expired': False,
              'workflow_run': {'id': 42, 'head_sha': 'b' * 40}}
    originraw = encoded(origin)
    files[audit.RT + '/artifact-origin.json'] = originraw
    commands = []
    bindings = {}
    git = ['/usr/bin/git', '--no-pager', '--no-replace-objects', '-C', '/home/runner/work/som/som']
    def command(index, label, argv, stdout):
        row = {'argv': argv, 'cwd': root, 'pid': 100 + index, 'exit_code': 0}
        for stream, raw in [('stdout', stdout), ('stderr', b'')]:
            relative = 'logs/%03d-%s.%s' % (index, label, stream)
            row[stream] = root + '/' + relative
            row[stream + '_sha256'] = sha(raw)
            files[audit.RT + '/' + relative] = raw
        commands.append(row)
    command(0, 'source-head', git + ['rev-parse', '--verify', 'HEAD^{commit}'], (audit.REVISION + '\n').encode())
    command(1, 'source-root', git + ['rev-parse', '--show-toplevel'], b'/home/runner/work/som/som\n')
    for number, path in enumerate(paths):
        raw = source[path]
        oid = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        bindings[path] = {'git_blob_oid': oid, 'sha256': sha(raw), 'working_sha256': sha(raw), 'working_bytes_match': True}
        for label, argv, output in [
                ('tree', git + ['ls-tree', '-z', audit.REVISION, '--', path], b'100644 blob ' + oid.encode() + b'\t' + path.encode() + b'\0'),
                ('size', git + ['cat-file', '-s', oid], str(len(raw)).encode() + b'\n'),
                ('blob', git + ['cat-file', 'blob', oid], raw)]:
            command(len(commands), label + '-' + str(number), argv, output)
    admitted = {}
    for name in admitted_paths:
        index = len(commands)
        component = name.split('/')[1]
        path = root + '/' + name
        stdout = ('version ' + name + '\n').encode()
        argv = [path, '-B', '--version'] if component == 'python' else [path, '--version']
        command(index, 'recheck-' + component + '-' + name.rsplit('/', 1)[-1], argv, stdout)
        admitted[name] = {'sha256': inventory[name]['sha256'], 'status': 'hash_and_version_rechecked',
                          'version_exit_code': 0, 'version_command_index': index, 'path': path,
                          'component': component, 'version_stdout': stdout.decode()}
    module_probe = "import ssl,zlib,bz2,lzma,sqlite3,ctypes,venv,ensurepip,json; print(json.dumps({'openssl':ssl.OPENSSL_VERSION,'zlib':zlib.ZLIB_RUNTIME_VERSION,'sqlite':sqlite3.sqlite_version}))"
    command(58, 'recheck-python-core-modules', [root + '/install/python/bin/python3.12', '-I', '-B', '-c', module_probe], b'fixture modules\n')
    receipt = {'version': 'som.product-runtime-reuse.v1', 'tested_sha': audit.REVISION,
               'status': 'audited_runtime_reinstalled_and_rechecked_for_hosted_tests',
               'product_runtime_ready': False, 'product_acceptance_passed': False,
               'runner': {'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64', 'ImageOS': 'ubuntu22',
                          'ImageVersion': 'fixture', 'GITHUB_RUN_ID': str(audit.RUN), 'GITHUB_RUN_ATTEMPT': '1'},
               'origin': {'metadata_sha256': sha(originraw)}, 'source_bindings': {'revision': audit.REVISION, 'files': bindings},
               'admitted_executables': admitted, 'commands': commands,
               'python_modules': {'stdout': 'fixture modules\n', 'exit_code': 0, 'command_index': 58}}
    receipt_raw = encoded(receipt)
    files[audit.RT + '/receipt.json'] = receipt_raw
    native = {'reuse_receipt_sha256': sha(receipt_raw), 'qualification_sha256': pins[audit.QUAL],
              'inventory_sha256': sha(invraw), 'python_sha256': inventory[admitted_paths[0]]['sha256'],
              'uv_sha256': inventory[admitted_paths[1]]['sha256']}
    return source, pins, files, entries, {'native_runtime': native}, receipt


class RuntimeChecks(unittest.TestCase):
    def invoke(self, source, pins, files, entries, report):
        def read(_tar, _entries, path, limit):
            if path not in files:
                raise audit.Missing(path)
            return files[path]
        with mock.patch.object(audit, 'PINS', pins), mock.patch.object(audit, 'data', side_effect=read):
            return audit.runtime_identity(None, entries, report, source)

    def test_retained_runtime_identity_and_tampering(self):
        source, pins, files, entries, report, receipt = runtime_fixture()
        self.assertEqual(self.invoke(source, pins, files, entries, report), report['native_runtime'])
        altered = copy.deepcopy(receipt)
        altered['commands'].append({})
        self.assert_receipt_rejected(source, pins, files, entries, report, altered)
        altered = copy.deepcopy(receipt)
        altered['admitted_executables']['install/python/bin/python3.12']['version_command_index'] = 18
        self.assert_receipt_rejected(source, pins, files, entries, report, altered)
        for field in ('RUNNER_OS', 'RUNNER_ARCH', 'ImageOS', 'GITHUB_RUN_ID'):
            altered = copy.deepcopy(receipt)
            altered['runner'][field] = 'wrong'
            self.assert_receipt_rejected(source, pins, files, entries, report, altered)
        for field in ('git_blob_oid', 'sha256', 'working_sha256', 'working_bytes_match'):
            altered = copy.deepcopy(receipt)
            first = next(iter(altered['source_bindings']['files']))
            altered['source_bindings']['files'][first][field] = False
            self.assert_receipt_rejected(source, pins, files, entries, report, altered)
        for index in (0, 1, 2, 3, 4, 17, 58):
            for field, value in [('argv', []), ('exit_code', 1), ('pid', None)]:
                altered = copy.deepcopy(receipt)
                altered['commands'][index][field] = value
                with self.subTest(index=index, field=field):
                    self.assert_receipt_rejected(source, pins, files, entries, report, altered)
            altered = copy.deepcopy(receipt)
            relative = altered['commands'][index]['stdout'].removeprefix(audit.TEMP + '/' + audit.RT + '/')
            path = audit.RT + '/' + relative
            tampered = dict(files)
            tampered[path] += b'tamper'
            altered['commands'][index]['stdout_sha256'] = sha(tampered[path])
            with self.subTest(index=index, field='rehashed stdout'):
                self.assert_receipt_rejected(source, pins, tampered, entries, report, altered)
        for index in range(59):
            command = receipt['commands'][index]
            for stream in ('stdout', 'stderr'):
                path = audit.RT + '/' + command[stream].removeprefix(audit.TEMP + '/' + audit.RT + '/')
                tampered = dict(files)
                tampered[path] += b'tamper'
                with self.subTest(index=index, stream=stream), self.assertRaises(audit.AuditError):
                    self.invoke(source, pins, tampered, entries, report)
                missing = dict(files)
                del missing[path]
                with self.subTest(index=index, missing=stream), self.assertRaises(audit.Missing):
                    self.invoke(source, pins, missing, entries, report)

    def assert_receipt_rejected(self, source, pins, files, entries, report, receipt):
        raw = json.dumps(receipt, sort_keys=True).encode()
        altered_files = dict(files)
        altered_files[audit.RT + '/receipt.json'] = raw
        altered_report = copy.deepcopy(report)
        altered_report['native_runtime']['reuse_receipt_sha256'] = sha(raw)
        with self.assertRaises(audit.AuditError):
            self.invoke(source, pins, altered_files, entries, altered_report)


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise SystemExit('synthetic tests require a GitHub-hosted runner')
    unittest.main()
