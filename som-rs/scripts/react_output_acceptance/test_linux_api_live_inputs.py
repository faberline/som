"""Synthetic live input checks. Execute only on GitHub hosted runners."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import linux_api_live_inputs as live
import linux_api_context as context


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True).encode()


def end_to_end_fixture():
    temp = Path(tempfile.mkdtemp(prefix='som-live-e2e-', dir=os.environ['RUNNER_TEMP'])).resolve()
    repo, runtime, root = temp/'repo', temp/'som-product-runtime-qualification', temp/'som-react-python-closure'
    repo.mkdir()
    (runtime/'install/python/bin').mkdir(parents=True)
    (runtime/'install/uv/bin').mkdir(parents=True)
    python, uv = runtime/'install/python/bin/python3.12', runtime/'install/uv/bin/uv'
    python.write_bytes(b'qualified-python')
    uv.write_bytes(b'qualified-uv-asset')
    runtime_receipt = {'runner': {'GITHUB_RUN_ID': '1234', 'GITHUB_RUN_ATTEMPT': '1'}}
    runtime_raw = json_bytes(runtime_receipt)
    (runtime/'receipt.json').write_bytes(runtime_raw)
    root.mkdir()
    for directory in ('home', 'tmp', 'logs'):
        (root/directory).mkdir()
    closure = root/'closures'/live.KEY
    (closure/'wheels').mkdir(parents=True)
    (closure/'project').mkdir()
    named = root/'environments/react-api-qa'
    (named/'venv/lib/python3.12/site-packages').mkdir(parents=True)
    venv = named/'venv'
    installed = venv/'lib/python3.12/site-packages/fixture.py'
    installed.write_bytes(b'wheel-derived fixture')
    selected = {}
    rows = []
    for n in range(40):
        name, filename = f'pkg{n}', f'pkg{n}-1.0-py3-none-any.whl'
        content = ('wheel ' + name).encode()
        (closure/'wheels'/filename).write_bytes(content)
        metadata_sha = digest(('metadata ' + name).encode())
        selected[name] = {'version': '1.0', 'hashes': [digest(content)]}
        rows.append({'name': name, 'version': '1.0', 'filename': filename, 'sha256': digest(content)})
        audit_dir = closure/'wheel-audits'/name
        audit_dir.mkdir(parents=True)
        (audit_dir/'inventory.json').write_bytes(json_bytes({'metadata_sha256': metadata_sha}))
    lock = ''.join(name + '==1.0 --hash=sha256:' + row['hashes'][0] + '\n' for name,row in selected.items()).encode()
    for name in ('requirements.selected.lock', 'requirements.offline.lock'):
        (closure/name).write_bytes(lock)
    requirements = ''.join(f'pkg{n}==1.0\n' for n in range(39)).encode()
    (closure/'project/pyproject.toml').write_bytes(b'[project]\nname="fixture"\n')
    (closure/'project/uv.lock').write_bytes(b'version = 1\n')
    for name, value in [('requirements.in', requirements), ('pyproject.toml', (closure/'project/pyproject.toml').read_bytes()),
                        ('uv.lock', (closure/'project/uv.lock').read_bytes())]:
        (named/name).write_bytes(value)
    closure_raw = json_bytes({'wheels': rows})
    (closure/'receipt.json').write_bytes(closure_raw)
    (temp/'react39-inputs/source').mkdir(parents=True)
    (temp/'react39-inputs/audit').mkdir(parents=True)
    tar = temp/'react39-inputs/source/react-python-qa-closure.tar'
    tar.write_bytes(b'synthetic archived input')
    tar_sha = digest(tar.read_bytes())
    audit_doc = {'state': 'partial-evidence-consistent', 'product_acceptance_passed': False, 'run_id': 40821432963,
                 'artifact_id': 12874482658, 'source_commit': live.ORIGINAL_HEAD, 'artifact_zip_sha256': live.ZIP_SHA,
                 'artifact_tar_sha256': tar_sha, 'requirements_count': 39, 'wheel_count': 40}
    audit_raw = json_bytes(audit_doc)
    (temp/'react39-inputs/audit/react-python-independent-audit.json').write_bytes(audit_raw)
    probe = root/'probe-installed-metadata.py'
    probe.write_bytes(b'# synthetic no-site probe\n')
    metadata = {'python': '3.12.11', 'executable': str(python), 'site': str(venv/'lib/python3.12/site-packages'),
                'site_disabled': True, 'site_in_sys_path': False,
                'packages': [{'name': name, 'version': '1.0', 'metadata_sha256': digest(('metadata '+name).encode())}
                             for name in selected]}
    metadata_raw = json_bytes(metadata)
    (named/'live-installed-metadata.json').write_bytes(metadata_raw)
    def current_inventory(_venv, _root):
        data = installed.read_bytes()
        return {'lib/python3.12/site-packages/fixture.py': {'type': 'file', 'sha256': digest(data), 'size': len(data), 'mode': 0o644}}
    inventory_raw = json_bytes(current_inventory(venv, root))
    (named/'live-installed-files.json').write_bytes(inventory_raw)
    scope = {'exact_scope_verified': True, 'installed_paths': 1}
    def check_installed(_venv, inventory, _closure, _assets, _python, _project):
        live.need(inventory['lib/python3.12/site-packages/fixture.py']['sha256'] == digest(b'wheel-derived fixture'),
                  'fixture installed bytes differ')
        return scope
    def check_closure(value):
        for row in value['record']['wheels']:
            path = value['wheels']/row['filename']
            live.need(path.is_file() and digest(path.read_bytes()) == row['sha256'], 'fixture wheel differs')
    def parse_lock(raw):
        result = {}
        for line in raw.decode().splitlines():
            first, hash_value = line.split()
            name, version = first.split('==')
            result[name] = {'version': version, 'hashes': [hash_value.removeprefix('--hash=sha256:')]}
        return result
    producer = SimpleNamespace(parse=lambda raw: json.loads(raw), installer_assets=lambda _: {'asset': b'uv-asset'},
                               read=lambda path, *_: Path(path).read_bytes(), MAX_WHEEL=180*1024*1024,
                               locked_requirements=parse_lock, normalize=lambda name: name.lower(), PROBE=probe.read_text(),
                               verify_closure=check_closure, installed_inventory=current_inventory,
                               verify_installed_members=check_installed)
    def verify_runtime(*_):
        live.need(python.read_bytes() == b'qualified-python', 'fixture runtime changed')
    reuse = SimpleNamespace(verify_installed_tree=verify_runtime)
    tools = {'python': python, 'uv': uv}
    native = {'reuse_receipt_sha256': digest(runtime_raw)}
    binding = {'producer': producer, 'reuse': reuse, 'qualification': {}, 'runtime_inventory': {}, 'tools': tools, 'native': native}
    raw_sources = {live.POLICY: b'{}', live.INSTALLER: b'{}', live.REQUIREMENTS: requirements}
    profile = {'runtime_root': str(runtime), 'closure_root': str(root), 'venv': str(venv),
               'live_receipt': str(named/'live-provision-receipt.json')}
    env = {'PATH':str(python.parent)+':'+str(uv.parent)+':/usr/bin:/bin', 'HOME':str(root/'home'),
           'TMPDIR':str(root/'tmp'), 'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8', 'PYTHONDONTWRITEBYTECODE':'1',
           'PYTHONNOUSERSITE':'1', 'UV_PYTHON_DOWNLOADS':'never', 'UV_NO_CONFIG':'1', 'UV_NO_BUILD':'1',
           'UV_KEYRING_PROVIDER':'disabled', 'UV_DEFAULT_INDEX':'https://pypi.org/simple',
           'UV_CACHE_DIR':str(closure/'cache'), 'UV_NO_PROGRESS':'1', 'UV_NO_INSTALLER_METADATA':'1',
           'UV_COMPILE_BYTECODE':'0', 'UV_OFFLINE':'1', 'UV_PROJECT_ENVIRONMENT':str(venv)}
    def command(label, argv, environment, stdout):
        row = {'argv':[str(x) for x in argv], 'cwd':str(named), 'environment':environment, 'status':'completed',
               'pid':1234, 'wait_result':0, 'exit_code':0, 'group_stopped':True, 'elapsed_seconds':1.0}
        for kind, content in [('stdout', stdout), ('stderr', b'')]:
            path = root/'logs'/(label+'.'+kind)
            path.write_bytes(content)
            row.update({kind:str(path), kind+'_bytes':len(content), kind+'_sha256':digest(content)})
        return row
    sync = [uv,'sync','--frozen','--python',python,'--no-managed-python','--no-config',
            '--no-build','--offline','--no-index','--find-links',closure/'wheels','--link-mode','copy']
    first = command('fresh-offline-frozen-sync', sync, env, b'sync complete\n')
    meta_env = dict(env)
    del meta_env['UV_PROJECT_ENVIRONMENT']
    second = command('fresh-offline-no-site-metadata', [python,'-I','-S','-B',probe,venv/'lib/python3.12/site-packages'], meta_env, metadata_raw)
    receipt = {'version':'som.react-live-venv.v1', 'state':'fresh-offline-venv-ready-product-tests-unrun',
               'product_acceptance_passed':False, 'source_run_id':40821432963, 'source_artifact_id':12874482658,
               'source_zip_sha256':live.ZIP_SHA, 'source_tar_sha256':tar_sha, 'independent_audit_run_id':40823392027,
               'independent_audit_artifact_id':12875218806, 'independent_audit_sha256':digest(audit_raw),
               'source_revision':'c'*40, 'closure_receipt_sha256':digest(closure_raw),
               'runtime_receipt_sha256':digest(runtime_raw), 'requirements_sha256':digest(requirements),
               'requirements_count':39, 'wheel_count':40, 'venv':str(venv), 'commands':[first,second],
               'probe_sha256':digest(probe.read_bytes()), 'installed_metadata_sha256':digest(metadata_raw),
               'installed_versions':{name:'1.0' for name in selected}, 'installed_scope':scope,
               'venv_inventory_sha256':digest(inventory_raw)}
    receipt_path = named/'live-provision-receipt.json'
    receipt_path.write_bytes(json_bytes(receipt))
    patches = {'EXPECTED_TEMP':str(temp), 'TAR_SHA':tar_sha, 'AUDIT_SHA':digest(audit_raw),
               'CLOSURE_SHA':digest(closure_raw), 'REQ_SHA':digest(requirements)}
    return temp, repo, profile, binding, raw_sources, patches, receipt_path, receipt, closure, python, installed


class LiveInputTests(unittest.TestCase):
    def test_recorded_github_live_proof_raw_commands(self):
        """Check the retained bytes from run 40825178990 without running the tools."""
        root = Path(__file__).parent / 'fixtures/live-proof'
        expected_hashes = {
            'live-provision-receipt.json': 'a54253a9871ef535927b44c28ee5372e02aa311c3392ce253ae394c12eea3fe7',
            'live-installed-files.json': '8646972fc06038701e4ab57595d123b2dd550a82dfa3af53ece8bc8bceb0523b',
            'live-installed-metadata.json': 'de1f7db2dcf7a5ef7ee0da2b1ff578273eb7cd4f3acbe60b956698961a57cd6e',
            'fresh-offline-frozen-sync.stdout': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            'fresh-offline-frozen-sync.stderr': 'fb728ef1911f2d85047876e105970b1c629e7389161f5a110f9c49111aa75969',
            'fresh-offline-no-site-metadata.stdout': '450156640c9ef553ca597fc754d3e8ab1cdaef80579443da91e18da93356042a',
            'fresh-offline-no-site-metadata.stderr': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
        }
        for name, expected in expected_hashes.items():
            self.assertEqual(digest((root / name).read_bytes()), expected)
        receipt = json.loads((root / 'live-provision-receipt.json').read_bytes())
        self.assertEqual(receipt['source_revision'], '94c5d4cd46fc92b8117bdacdcc0c1ac94b45a28f')
        self.assertEqual((receipt['requirements_count'], receipt['wheel_count']), (39, 40))
        self.assertEqual(receipt['venv_inventory_sha256'], expected_hashes['live-installed-files.json'])
        self.assertEqual(receipt['installed_metadata_sha256'], expected_hashes['live-installed-metadata.json'])
        base = Path('/home/runner/work/_temp')
        closure = base / 'som-react-python-closure'
        named = closure / 'environments/react-api-qa'
        venv = named / 'venv'
        runtime = base / 'som-product-runtime-qualification/install'
        python, uv = runtime / 'python/bin/python3.12', runtime / 'uv/bin/uv'
        wheels = closure / 'closures' / live.KEY / 'wheels'
        cache = closure / 'closures' / live.KEY / 'cache'
        env = {'PATH': str(python.parent) + ':' + str(uv.parent) + ':/usr/bin:/bin',
               'HOME': str(closure / 'home'), 'TMPDIR': str(closure / 'tmp'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'UV_PYTHON_DOWNLOADS': 'never',
               'UV_NO_CONFIG': '1', 'UV_NO_BUILD': '1', 'UV_KEYRING_PROVIDER': 'disabled',
               'UV_DEFAULT_INDEX': 'https://pypi.org/simple', 'UV_CACHE_DIR': str(cache), 'UV_NO_PROGRESS': '1',
               'UV_NO_INSTALLER_METADATA': '1', 'UV_COMPILE_BYTECODE': '0', 'UV_OFFLINE': '1',
               'UV_PROJECT_ENVIRONMENT': str(venv)}
        specs = [([uv, 'sync', '--frozen', '--python', python, '--no-managed-python', '--no-config', '--no-build',
                   '--offline', '--no-index', '--find-links', wheels, '--link-mode', 'copy'], env, 'fresh-offline-frozen-sync'),
                 ([python, '-I', '-S', '-B', closure / 'probe-installed-metadata.py', venv / 'lib/python3.12/site-packages'],
                  {key: value for key, value in env.items() if key != 'UV_PROJECT_ENVIRONMENT'}, 'fresh-offline-no-site-metadata')]
        self.assertEqual(len(receipt['commands']), 2)
        with mock.patch.object(live, 'read', side_effect=lambda path, *args: (root / Path(path).name).read_bytes()):
            for row, (argv, environment, label) in zip(receipt['commands'], specs):
                live.verify_command(row, argv, named, environment, closure / 'logs', label)
                altered = copy.deepcopy(row)
                altered['wait_result'] = None
                with self.assertRaises(live.InputError):
                    live.verify_command(altered, argv, named, environment, closure / 'logs', label)

    def test_sealed_source_inventories_agree(self):
        expected = ({context.CONTEXT_SOURCE, context.NODE_SOURCE, context.LIVE_SOURCE,
                     context.RECORDER_SOURCE, context.HOST_SOURCE, context.EVIDENCE_SOURCE} | context.LIVE_DEPENDENCIES | context.API_CHAIN)
        self.assertEqual(live.SOURCE_SET, expected)

    def test_end_to_end_live_admission_and_missing_or_changed_inputs(self):
        for variant in ('pass', 'missing-wheel', 'changed-wheel', 'runtime', 'venv', 'wrong-wait',
                        'missing-log', 'changed-tar', 'changed-audit'):
            with self.subTest(variant=variant):
                temp, repo, profile, binding, raw_sources, patches, receipt_path, receipt, closure, python, installed = end_to_end_fixture()
                wheel = closure/'wheels/pkg0-1.0-py3-none-any.whl'
                if variant == 'missing-wheel': wheel.unlink()
                if variant == 'changed-wheel': wheel.write_bytes(b'changed')
                if variant == 'runtime': python.write_bytes(b'changed')
                if variant == 'venv': installed.write_bytes(b'changed')
                if variant == 'wrong-wait':
                    receipt['commands'][0]['wait_result'] = None
                    receipt_path.write_bytes(json_bytes(receipt))
                if variant == 'missing-log': Path(receipt['commands'][1]['stdout']).unlink()
                if variant == 'changed-tar': (temp/'react39-inputs/source/react-python-qa-closure.tar').write_bytes(b'changed')
                if variant == 'changed-audit': (temp/'react39-inputs/audit/react-python-independent-audit.json').write_bytes(b'{}')
                environment = {'RUNNER_TEMP': str(temp), 'GITHUB_SHA': 'c'*40,
                               'GITHUB_RUN_ID': '1234', 'GITHUB_RUN_ATTEMPT': '1'}
                sources = SimpleNamespace(verify=mock.Mock())
                source_path = str(repo/'som-rs/scripts/react_output_acceptance/linux_api_live_inputs.py')
                with mock.patch.dict(os.environ, environment), mock.patch.multiple(live, **patches), \
                     mock.patch.object(live, '__file__', source_path), \
                     mock.patch.object(live, 'bound_sources', return_value=(binding, sources, raw_sources)):
                    if variant == 'pass':
                        result = live.verify(profile)
                        self.assertEqual(result['status'], 'verified-live-inputs')
                        self.assertEqual((result['requirements_count'], result['wheel_count']), (39, 40))
                        self.assertEqual(result['live_receipt_sha256'], digest(receipt_path.read_bytes()))
                        self.assertEqual(result['venv_inventory_sha256'], receipt['venv_inventory_sha256'])
                        sources.verify.assert_called_once_with()
                    else:
                        with self.assertRaises((live.InputError, OSError, ValueError)):
                            live.verify(profile)

    def command_fixture(self):
        root = Path(tempfile.mkdtemp(prefix='som-live-input-', dir=os.environ['RUNNER_TEMP'])).resolve()
        logs = root / 'logs'
        logs.mkdir()
        argv, environment = ['/qualified/uv', 'sync', '--offline'], {'UV_OFFLINE': '1'}
        row = {'argv': argv, 'cwd': str(root), 'environment': environment, 'status': 'completed',
               'pid': 1234, 'exit_code': 0, 'wait_result': 0, 'group_stopped': True, 'elapsed_seconds': 1.0}
        for kind, raw in [('stdout', b'output\n'), ('stderr', b'')]:
            path = logs / ('fixture.' + kind)
            path.write_bytes(raw)
            row.update({kind: str(path), kind+'_bytes': len(raw), kind+'_sha256': digest(raw)})
        return root, logs, argv, environment, row

    def test_exact_command_and_wait_and_raw_log_bytes(self):
        root, logs, argv, environment, row = self.command_fixture()
        self.assertEqual(live.verify_command(row, argv, root, environment, logs, 'fixture')['stdout'], b'output\n')
        for field, value in [('argv', []), ('cwd', '/outside'), ('environment', {}), ('status', 'timeout'),
                             ('pid', 1), ('exit_code', 1), ('wait_result', None), ('group_stopped', False),
                             ('stdout_sha256', '0' * 64), ('stderr_bytes', 1), ('elapsed_seconds', float('nan'))]:
            with self.subTest(field=field):
                altered = copy.deepcopy(row)
                altered[field] = value
                with self.assertRaises(live.InputError):
                    live.verify_command(altered, argv, root, environment, logs, 'fixture')
        Path(row['stdout']).write_bytes(b'tampered\n')
        with self.assertRaises(live.InputError):
            live.verify_command(row, argv, root, environment, logs, 'fixture')

    def test_missing_or_linked_evidence_and_duplicate_json_fail(self):
        root, _, _, _, _ = self.command_fixture()
        file = root / 'file'
        file.write_bytes(b'data')
        link = root / 'link'
        link.symlink_to(file)
        for path in (link, root / 'missing'):
            with self.subTest(path=path), self.assertRaises(live.InputError):
                live.read(path)
        with self.assertRaises(live.InputError):
            live.document(b'{"a":1,"a":2}')
        self.assertEqual(live.hash_file(file, 10), digest(b'data'))
        with self.assertRaises(live.InputError):
            live.hash_file(file, 2)

    def test_unknown_profile_refused(self):
        with self.assertRaises(live.InputError):
            live.verify(None)

    def test_runtime_receipt_must_belong_to_current_runner_attempt(self):
        with mock.patch.dict(os.environ, {'GITHUB_RUN_ID': '1234', 'GITHUB_RUN_ATTEMPT': '2'}):
            live.verify_runner_identity(b'{"runner":{"GITHUB_RUN_ID":"1234","GITHUB_RUN_ATTEMPT":"2"}}')
            for raw in (b'{}', b'{"runner":{"GITHUB_RUN_ID":"1235","GITHUB_RUN_ATTEMPT":"2"}}',
                        b'{"runner":{"GITHUB_RUN_ID":"1234","GITHUB_RUN_ATTEMPT":"1"}}'):
                with self.subTest(raw=raw), self.assertRaises(live.InputError):
                    live.verify_runner_identity(raw)

    def test_source_binding_rejects_change_and_preload_before_execution(self):
        root = Path(tempfile.mkdtemp(prefix='som-live-source-', dir=os.environ['RUNNER_TEMP'])).resolve()
        repo = root / 'repo'
        repo.mkdir()
        sources = {}
        for name in live.SOURCE_SET:
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = b'raise AssertionError("untrusted source executed")\n' if name == live.PRODUCER else name.encode()
            path.write_bytes(raw)
            sources[name] = digest(raw)
        authority_path = repo / live.AUTHORITY
        authority_path.parent.mkdir(parents=True, exist_ok=True)
        authority_raw = json.dumps({'source_sha256': sources}, sort_keys=True).encode()
        authority_path.write_bytes(authority_raw)
        receipt = SimpleNamespace(__file__=str(repo/'som-rs/scripts/react_output_acceptance/retained_evidence.py'),
                                  sealed_files=lambda _: {live.AUTHORITY: digest(authority_raw)})
        fixed = {name: sources[path] for name, path in [('PRODUCER_SHA', live.PRODUCER), ('PROVISIONER_SHA', live.PROVISIONER), ('REQ_SHA', live.REQUIREMENTS),
                                                        ('POLICY_SHA', live.POLICY), ('INSTALLER_SHA', live.INSTALLER)]}
        with mock.patch.multiple(live, **fixed), mock.patch.object(live, '_binding', None), \
             mock.patch.dict(sys.modules, {'retained_evidence': receipt, 'som_react_api_verified_wheel_producer': object()}):
            with self.assertRaisesRegex(live.InputError, 'preloaded producer'):
                live.bound_sources(repo, 'a' * 40)
            (repo/live.PROVISIONER).write_bytes(b'tampered')
            with self.assertRaisesRegex(live.InputError, 'source bytes differ'):
                live.bound_sources(repo, 'a' * 40)


if __name__ == '__main__':
    unittest.main()
