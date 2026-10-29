"""Synthetic fixtures only. Run exclusively on a GitHub-hosted runner."""
import hashlib
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest import mock
from types import SimpleNamespace

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

sys.path.insert(0, str(Path(__file__).absolute().parent))
import qualify_react_python as q


def synthetic_requirements():
    return ('# synthetic only\n' + ''.join(f'pkg-{i}==1.2.{i}\n' for i in range(39))).encode()


class ReactPythonClosureFixtures(unittest.TestCase):
    def parse_fixture(self, raw):
        with mock.patch.object(q, 'REQUIREMENTS_SHA', q.sha(raw)):
            return q.requirements_row(raw)

    def test_exact_39_pins_bind_new_qa_project_without_changing_bytes(self):
        raw = synthetic_requirements()
        row = self.parse_fixture(raw)
        project = tomllib.loads(row['pyproject_bytes'])
        self.assertEqual(row['id'], 'react-api-qa')
        self.assertEqual(row['requirements_raw'], raw)
        self.assertEqual(len(row['requirements']['runtime']), 39)
        self.assertEqual(project['project']['dependencies'], row['requirements']['runtime'])
        self.assertEqual(project['project']['requires-python'], '>=3.12,<3.13')
        self.assertEqual(project['project']['name'], 'som-react-api-qa')
        self.assertEqual(project['dependency-groups']['dev'], [])
        self.assertIs(project['tool']['uv']['package'], False)

    def test_rejects_missing_extra_duplicate_alias_or_non_exact_pins(self):
        original = synthetic_requirements().decode().splitlines()
        cases = [original[:-1], original + ['extra==1'],
                 original[:-1] + ['pkg_0==1.2.0'],
                 original[:-1] + ['pkg-38>=1.0'],
                 original[:-1] + ['pkg-38==1.0 ; python_version > "3"'],
                 original[:-1] + ['pkg-38 @ https://example.invalid/archive']]
        for lines in cases:
            raw = ('\n'.join(lines) + '\n').encode()
            with self.subTest(lines=lines[-1]), self.assertRaises(q.Refusal):
                self.parse_fixture(raw)

    def test_requirement_hash_mismatch_refuses(self):
        with mock.patch.object(q, 'REQUIREMENTS_SHA', '0' * 64), self.assertRaises(q.Refusal):
            q.requirements_row(synthetic_requirements())

    def test_git_blob_and_worktree_bytes_must_both_match(self):
        root = Path(tempfile.mkdtemp(prefix='som-react-python-source-fixture-'))
        relative = 'source/fixture.py'
        path = root / relative
        path.parent.mkdir()
        raw = b'# reviewed synthetic source\n'
        path.write_bytes(raw)
        oid = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        revision = 'a' * 40

        def fake_git(argv, **kwargs):
            self.assertEqual(argv[:5], ['/usr/bin/git', '--no-replace-objects', '-C', str(root), argv[4]])
            command = argv[4:]
            if command == ['rev-parse', '--verify', 'HEAD^{commit}']:
                return (revision + '\n').encode()
            if command == ['rev-parse', '--show-toplevel']:
                return os.fsencode(root) + b'\n'
            if command == ['ls-tree', '-z', revision, '--', relative]:
                return ('100644 blob ' + oid + '\t' + relative + '\0').encode()
            if command == ['cat-file', 'blob', oid]:
                return raw
            raise AssertionError(command)

        with mock.patch.object(q.subprocess, 'check_output', side_effect=fake_git):
            got, binding = q.reviewed_source(root, revision, relative, q.sha(raw))
            self.assertEqual(got, raw)
            self.assertEqual(binding, {'path': relative, 'git_blob_oid': oid, 'sha256': q.sha(raw)})
            with self.assertRaises(q.Refusal):
                q.reviewed_source(root, revision, relative, '0' * 64)
            path.write_bytes(b'# changed synthetic source\n')
            with self.assertRaises(q.Refusal):
                q.reviewed_source(root, revision, relative, q.sha(raw))

    def test_source_path_traversal_refused_before_git(self):
        root = Path(tempfile.mkdtemp(prefix='som-react-python-source-fixture-'))
        with mock.patch.object(q.subprocess, 'check_output') as git:
            with self.assertRaises(q.Refusal):
                q.reviewed_source(root, 'a' * 40, '../other')
            git.assert_not_called()

    def test_prepare_rejects_bad_producer_policy_and_runtime_before_provision(self):
        for failure in ('producer', 'policy', 'runtime'):
            with self.subTest(failure=failure):
                temp = Path(tempfile.mkdtemp(prefix='som-react-prepare-fixture-')).resolve()
                repo, runtime = temp / 'repo', temp / 'runtime'
                repo.mkdir()
                runtime.mkdir()
                producer_path = repo / q.PRODUCER
                producer_path.parent.mkdir(parents=True)
                producer_path.write_bytes(b'synthetic producer')
                executable = runtime / 'install/python/bin/python3.12'
                executable.parent.mkdir(parents=True)
                executable.write_bytes(b'synthetic python')
                output = temp / 'som-react-python-closure'
                req = synthetic_requirements()
                own, producer_raw, policy, installer, native_source = (b'wrapper', b'synthetic producer',
                                                                         b'policy', b'installer', b'native source')
                policy_path, installer_path, native_path = 'policy.json', 'installer.json', 'native.py'
                values = {q.WRAPPER: own, q.PRODUCER: producer_raw, q.REQUIREMENTS: req,
                          policy_path: policy, installer_path: installer, native_path: native_source}
                sources = mock.Mock()
                sources.read.side_effect = lambda path: values[path]
                producer = mock.Mock()
                producer.Sources.return_value = sources
                producer.POLICY, producer.INSTALLER_AUTHORITY = policy_path, installer_path
                producer.parse.return_value = {'version': 'som.hosted-python-wheel-policy.v1',
                                               'python': '3.12.11', 'uv': '0.9.9', 'host': 'x86_64-manylinux_2_35'}
                producer.installer_assets.return_value = []
                if failure == 'runtime':
                    producer.admitted_runtime.side_effect = q.Refusal('runtime differs')

                def reviewed(repo_arg, revision_arg, name, expected=None):
                    self.assertEqual(repo_arg, repo)
                    return values[name], {'path': name}

                def load(repo_arg, revision_arg):
                    if failure == 'producer':
                        raise q.Refusal('producer differs')
                    return producer, {'path': q.PRODUCER}

                normal_read_text = Path.read_text
                def read_text(path, *args, **kwargs):
                    if path == Path('/etc/os-release'):
                        return 'ID=ubuntu\nVERSION_ID="22.04"\n'
                    return normal_read_text(path, *args, **kwargs)

                revision = 'a' * 40
                environment = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                               'GITHUB_SHA': revision, 'RUNNER_TEMP': str(temp)}
                expected_policy = '0' * 64 if failure == 'policy' else q.sha(policy)
                flags = SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.dict(os.environ, environment))
                    stack.enter_context(mock.patch.object(q.platform, 'system', return_value='Linux'))
                    stack.enter_context(mock.patch.object(q.platform, 'machine', return_value='x86_64'))
                    stack.enter_context(mock.patch.object(Path, 'read_text', read_text))
                    stack.enter_context(mock.patch.object(q.sys, 'flags', flags))
                    stack.enter_context(mock.patch.object(q.sys, 'executable', str(executable)))
                    stack.enter_context(mock.patch.object(q, '__file__', str(repo / q.WRAPPER)))
                    stack.enter_context(mock.patch.object(q, 'file_sha', return_value=q.PYTHON_SHA))
                    stack.enter_context(mock.patch.object(q, 'REQUIREMENTS_SHA', q.sha(req)))
                    stack.enter_context(mock.patch.object(q, 'POLICY_SHA', expected_policy))
                    stack.enter_context(mock.patch.object(q, 'INSTALLER_SHA', q.sha(installer)))
                    stack.enter_context(mock.patch.object(q, 'NATIVE_SOURCE_SHAS', {native_path: q.sha(native_source)}))
                    stack.enter_context(mock.patch.object(q, 'reviewed_source', side_effect=reviewed))
                    stack.enter_context(mock.patch.object(q, 'load_producer', side_effect=load))
                    with self.assertRaises(q.Refusal):
                        q.prepare(repo, revision, runtime, output)
                producer.provision_named.assert_not_called()
                if failure in ('producer', 'policy'):
                    producer.admitted_runtime.assert_not_called()
                if failure == 'runtime':
                    producer.admitted_runtime.assert_called_once()

    def test_main_refuses_seed_pin_offline_cleanup_and_final_identity_failures(self):
        failures = ('none', 'pin', 'seed_error', 'seed_state', 'seed_unclean', 'seed_lock', 'seed_receipt',
                    'seed_offline', 'seed_not_frozen', 'seed_log_path', 'seed_log_hash', 'seed_wait',
                    'named_offline', 'named_not_frozen', 'named_unclean', 'named_order', 'named_missing', 'named_extra', 'named_probe', 'named_probe_venv',
                    'named_log_size', 'runtime', 'source')
        for failure in failures:
            with self.subTest(failure=failure):
                temp = Path(tempfile.mkdtemp(prefix='som-react-main-fixture-'))
                repo, runtime, output = temp / 'repo', temp / 'runtime', temp / 'result'
                repo.mkdir()
                runtime.mkdir()
                req = synthetic_requirements()
                source_path = repo / q.REQUIREMENTS
                source_path.parent.mkdir(parents=True)
                source_path.write_bytes(req)
                row = self.parse_fixture(req)
                pins = {f'pkg-{i}': {'version': f'1.2.{i}'} for i in range(39)}
                if failure == 'pin':
                    pins['pkg-7']['version'] = '9.9.9'
                tools = {'python': Path('/synthetic/python'), 'uv': Path('/synthetic/uv')}
                producer = mock.Mock()
                producer.PROBE = '# synthetic metadata probe'
                producer.Runner.return_value = object()
                producer.file_sha.side_effect = q.file_sha
                producer.read.side_effect = lambda path, *args: path.read_bytes()
                producer.parse.side_effect = lambda raw: json.loads(raw)
                producer.dependency_key.return_value = 'synthetic-key'
                producer.cancellation.side_effect = contextlib.nullcontext
                events = []
                def resolve(runner, row_arg, key, record):
                    events.append('resolve')
                    root = output / 'closures' / key
                    project, cache, wheels = root / 'project', root / 'cache', root / 'wheels'
                    project.mkdir(parents=True)
                    cache.mkdir()
                    wheels.mkdir()
                    (project / 'pyproject.toml').write_bytes(b'synthetic project')
                    (project / 'uv.lock').write_bytes(b'synthetic lock')
                    return {'pins': pins, 'root': root, 'cache': cache, 'wheels': wheels}
                producer.resolve_set.side_effect = resolve
                def command(argv, cwd, venv, offline, cache, label):
                    env = {'PATH': str(tools['python'].parent) + ':' + str(tools['uv'].parent) + ':/usr/bin:/bin',
                           'HOME': str(output / 'home'), 'TMPDIR': str(output / 'tmp'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'UV_PYTHON_DOWNLOADS': 'never',
                           'UV_NO_CONFIG': '1', 'UV_NO_BUILD': '1', 'UV_KEYRING_PROVIDER': 'disabled',
                           'UV_DEFAULT_INDEX': 'https://pypi.org/simple', 'UV_CACHE_DIR': str(cache), 'UV_NO_PROGRESS': '1',
                           'UV_NO_INSTALLER_METADATA': '1', 'UV_COMPILE_BYTECODE': '0'}
                    if venv is not None:
                        env['UV_PROJECT_ENVIRONMENT'] = str(venv)
                    if offline:
                        env['UV_OFFLINE'] = '1'
                    record = {'argv': [str(x) for x in argv], 'cwd': str(cwd), 'environment': env,
                              'owned_pid': 12345, 'exit_code': 0, 'owned_parent_stopped': True, 'owned_group_stopped': True}
                    for stream in ('stdout', 'stderr'):
                        relative = 'logs/' + label + '.' + stream
                        raw = ('synthetic ' + label + ' ' + stream).encode()
                        (output / relative).write_bytes(raw)
                        record[stream] = relative
                        record[stream + '_bytes'] = len(raw)
                        record[stream + '_sha256'] = q.sha(raw)
                    return record
                def seed(runner, closure, project, cache, evidence, label):
                    events.append('seed')
                    self.assertEqual(project, closure['root'] / 'project')
                    self.assertEqual(cache, closure['cache'])
                    self.assertEqual(evidence, closure['root'] / 'cache-preparation')
                    self.assertEqual(label, 'cache-synthetic-key')
                    evidence.mkdir()
                    if failure == 'seed_error':
                        (evidence / 'receipt.json').write_text('{"state":"failed"}')
                        raise ValueError('seed failed')
                    venv = evidence / 'venv'
                    if failure == 'seed_unclean':
                        venv.mkdir()
                    inventory = evidence / 'installed-files.json'
                    inventory.write_bytes(b'synthetic installed inventory')
                    argv = [tools['uv'], 'sync', '--frozen', '--python', tools['python'], '--no-managed-python',
                            '--no-config', '--no-build', '--no-index', '--find-links', closure['wheels'], '--link-mode', 'copy']
                    if failure == 'seed_offline':
                        argv.append('--offline')
                    if failure == 'seed_not_frozen':
                        argv.remove('--frozen')
                    seed_command = command(argv, project, venv, failure == 'seed_offline', cache,
                                           label + '-frozen-url-cache')
                    if failure == 'seed_log_path':
                        seed_command['stdout'] = 'logs/../outside.stdout'
                    if failure == 'seed_log_hash':
                        seed_command['stdout_sha256'] = '0' * 64
                    if failure == 'seed_wait':
                        seed_command['owned_parent_stopped'] = False
                    record = {'version': 'som.frozen-url-cache-preparation.v1',
                              'state': 'wrong' if failure == 'seed_state' else 'frozen-url-cache-prepared',
                              'product_acceptance_passed': False, 'original_inputs_unchanged': True,
                              'installed_code_executed': False, 'offline_product_sync': 'required separately',
                              'network_scope': 'preparation using the unchanged frozen official publisher URLs',
                              'installer_mode': 'qualification seed only; ordinary product mode is unchanged',
                              'cache': str(cache), 'venv': str(venv), 'project_sha256': q.sha((project / 'pyproject.toml').read_bytes()),
                              'uv_lock_sha256': q.sha((project / 'uv.lock').read_bytes()),
                              'installed_inventory_sha256': q.file_sha(inventory),
                              'commands': [seed_command]}
                    (evidence / 'before-cleanup.json').write_text(json.dumps(record))
                    record['owned_seed_venv_removed'] = True
                    (evidence / 'receipt.json').write_text(json.dumps({'state': 'tampered'} if failure == 'seed_receipt' else record))
                    if failure == 'seed_lock':
                        (project / 'uv.lock').write_bytes(b'changed lock')
                    return record
                producer.prepare_frozen_url_cache.side_effect = seed
                def provision(runner, row_arg, closure, probe, record):
                    events.append('named')
                    root = output / 'environments' / 'react-api-qa'
                    root.mkdir()
                    venv = root / 'venv'
                    if failure == 'named_unclean':
                        venv.mkdir()
                    offline = failure != 'named_offline'
                    argv = [tools['uv'], 'sync', '--frozen', '--python', tools['python'], '--no-managed-python',
                            '--no-config', '--no-build']
                    if failure == 'named_not_frozen':
                        argv.remove('--frozen')
                    if offline:
                        argv.append('--offline')
                    argv += ['--no-index', '--find-links', closure['wheels'], '--link-mode', 'copy']
                    common = ['--python', tools['python'], '--no-managed-python', '--no-config', '--no-build', '--offline']
                    lock_argv = [tools['uv'], 'lock', *common]
                    probe_argv = [tools['python'], '-I', '-S', '-B', output / 'probe-installed-metadata.py',
                                  venv / 'lib/python3.12/site-packages']
                    if failure == 'named_probe':
                        probe_argv.remove('-S')
                    commands = [command(lock_argv, root, venv, True, closure['cache'], 'react-api-qa-offline-original-lock'),
                                command(argv, root, venv, offline, closure['cache'], 'react-api-qa-offline-frozen-sync'),
                                command(probe_argv, root, None, True, closure['cache'], 'react-api-qa-offline-no-site-metadata')]
                    if failure == 'named_probe_venv':
                        commands[2]['environment']['UV_PROJECT_ENVIRONMENT'] = str(venv)
                    if failure == 'named_order':
                        commands[0], commands[1] = commands[1], commands[0]
                    if failure == 'named_missing':
                        commands.pop()
                    if failure == 'named_extra':
                        commands.append(commands[0])
                    if failure == 'named_log_size':
                        commands[1]['stderr_bytes'] += 1
                    record.update(state='offline-environment-qualified', frozen_sync_passed=True,
                                  original_project_unchanged=True, command_receipts=commands)
                    checkpoint = json.dumps(record).encode()
                    (root / 'qualification-before-cleanup.json').write_bytes(checkpoint)
                    record['qualification_before_cleanup_sha256'] = q.sha(checkpoint)
                    record['cleanup'] = {'removed': True}
                    (root / 'receipt.json').write_text(json.dumps(record))
                    return record
                producer.provision_named.side_effect = provision
                producer.verify_closure.side_effect = lambda closure: events.append('closure')
                sources = mock.Mock()
                reuse = mock.Mock()
                if failure == 'runtime':
                    reuse.verify_installed_tree.side_effect = ValueError('runtime changed')
                if failure == 'source':
                    sources.verify.side_effect = ValueError('source changed')
                prepared = (producer, sources, row, tools, {}, {}, reuse, {}, {}, {})
                argv = ['qualify_react_python.py', '--repo', str(repo), '--source-revision', 'a' * 40,
                        '--runtime', str(runtime), '--out', str(output)]
                with mock.patch.object(q, 'prepare', return_value=prepared), mock.patch.object(sys, 'argv', argv):
                    code = q.main()
                report = json.loads((output / 'receipt.json').read_text())
                self.assertIs(report['product_acceptance_passed'], False)
                self.assertEqual(report['independent_wheel_audit'], 'unrun')
                if failure == 'none':
                    self.assertEqual(code, 0)
                    self.assertEqual(report['state'], 'react-qa-closure-qualified-independent-audit-unrun')
                    self.assertEqual(events, ['resolve', 'seed', 'named', 'closure'])
                    self.assertEqual(report['frozen_url_cache']['state'], 'frozen-url-cache-prepared')
                    producer.provision_named.assert_called_once()
                    reuse.verify_installed_tree.assert_called_once()
                    sources.verify.assert_called_once()
                else:
                    self.assertEqual(code, 1)
                    self.assertEqual(report['state'], 'failed')
                    self.assertNotIn('wheel_closure_qualified', report)
                    if failure in ('pin', 'seed_error', 'seed_state', 'seed_unclean', 'seed_lock', 'seed_receipt',
                                   'seed_offline', 'seed_not_frozen', 'seed_log_path', 'seed_log_hash', 'seed_wait'):
                        producer.provision_named.assert_not_called()
                    else:
                        producer.provision_named.assert_called_once()
                    if failure == 'pin':
                        producer.prepare_frozen_url_cache.assert_not_called()
                    if failure == 'seed_error':
                        self.assertEqual(report['frozen_url_cache']['state'], 'failed-or-unverified')
                        self.assertTrue((output / 'closures/synthetic-key/cache-preparation/receipt.json').is_file())


if __name__ == '__main__':
    unittest.main()
