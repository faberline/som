#!/usr/bin/env python3
"""DRAFT: React QA Python wheel closure on GitHub; never product acceptance.

The source-bound, already qualified producer remains unchanged. This wrapper
does not use its 671-product environment consumer or invent an observer receipt.
Run only with the previously admitted Python using -I -S -B.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
from types import ModuleType

WRAPPER = '.github/m6-react-python-closure/qualify_react_python.py'
PRODUCER = 'som-rs/scripts/hosted_readiness/product_python/produce_wheels.py'
REQUIREMENTS = 'som-rs/scripts/react_output_acceptance/hosted_python_requirements.txt'
PRODUCER_SHA = 'c9151e7b09b0d7fcadcc203f72b83bf1135f2da260becf2034f4228b26299a39'
REQUIREMENTS_SHA = '217f0d88231ad9d10cca3232a8707cdbb3355bd9a41e19fbddbab0d299c6db35'
POLICY_SHA = '7a7532ed476f2e983fedfdf4304f8034765efb51df89caac604ad89c953442d7'
INSTALLER_SHA = 'e166303c1376a77d6e92ae19d9ff0eddcdd24c3adbde8381ff835274bb850358'
PYTHON_SHA = '8d0f57448252a3a8f8065b889648de2f1ca8959ea3f3fa029a94204bb9325c10'
NATIVE_SOURCE_SHAS = {
    'som-rs/scripts/hosted_readiness/product_runtime/reuse_runtime.py': '2a92e2df47fb05e76677fbb84e9cbb7f12d07d90eb8019bd816b5fe6b5588598',
    'som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py': 'f3c6f69e922ab0a8f27426a5f597c03f213d40be0fb7fc324ac33f1b48ae66f3',
    'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-qualification.json': '4aaca5ef284440fd64bd50723619425201417937b4d635bb3c5d2d5835226b68',
    'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-byte-audit.json': '7a2c04a65866474bd773b9722274c09c36646951fa7848d62e4b20b2cb94e75f',
    'som-rs/scripts/hosted_readiness/product_runtime/source-pins.json': 'a4208c342bc9e923509bc5ba7ce6a9a1adfe3143d55b6215f0a313b4833550b2',
}
MODULE = '_som_react_reviewed_wheel_producer'
PIN = re.compile(r'([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)\Z')


class Refusal(ValueError):
    pass


def need(value, reason):
    if not value:
        raise Refusal(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(2 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def physical(path):
    need(path.is_absolute() and '..' not in path.parts, 'absolute physical path required')
    for item in (path, *path.parents):
        need(not item.is_symlink(), 'linked path refused')


def reviewed_source(repo, revision, path, expected=None):
    """Bind current regular Git blob and working bytes without importing it."""
    need(re.fullmatch(r'[a-f0-9]{40}', revision) is not None, 'source revision invalid')
    need(not path.startswith('/') and all(x not in ('', '.', '..') for x in path.split('/')), 'source path invalid')
    working = repo / path
    physical(working)
    info = working.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 32 * 1024 * 1024, 'source file refused')
    def git(*args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args], stderr=subprocess.DEVNULL)
    need(git('rev-parse', '--verify', 'HEAD^{commit}').strip().decode() == revision, 'HEAD differs')
    need(git('rev-parse', '--show-toplevel').strip() == os.fsencode(repo), 'repository root differs')
    tree = git('ls-tree', '-z', revision, '--', path)
    fields, separator, name = tree.removesuffix(b'\0').partition(b'\t')
    values = fields.split()
    need(tree.count(b'\0') == 1 and separator and name == path.encode() and len(values) == 3
         and values[0] in (b'100644', b'100755') and values[1] == b'blob'
         and re.fullmatch(rb'[0-9a-f]{40}', values[2]) is not None, 'source Git tree differs')
    raw = git('cat-file', 'blob', values[2].decode())
    need(len(raw) == info.st_size and raw == working.read_bytes()
         and hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == values[2].decode(),
         'source bytes differ from Git')
    if expected is not None:
        need(sha(raw) == expected, 'reviewed source SHA differs')
    return raw, {'path': path, 'git_blob_oid': values[2].decode(), 'sha256': sha(raw)}


def requirements_row(raw):
    need(sha(raw) == REQUIREMENTS_SHA, 'React requirements source differs')
    lines = raw.decode('utf-8', 'strict').splitlines()
    pins, seen = [], set()
    for line in lines:
        if line.startswith('#') or not line:
            continue
        match = PIN.fullmatch(line)
        need(match is not None, 'requirement must be an exact pin')
        name = re.sub(r'[-_.]+', '-', match.group(1)).lower()
        need(name not in seen, 'duplicate requirement')
        seen.add(name)
        pins.append(line)
    need(len(pins) == 39, 'React requirement count differs')
    project = ('[project]\nname = "som-react-api-qa"\nversion = "0.0.0"\n'
               'requires-python = ">=3.12,<3.13"\ndependencies = ' + json.dumps(pins)
               + '\n[dependency-groups]\ndev = []\n[tool.uv]\npackage = false\n')
    return {'id': 'react-api-qa', 'requirements_raw': raw,
            'requirements': {'requires_python': '>=3.12,<3.13', 'runtime': pins, 'development': []},
            'pyproject_bytes': project}


def load_producer(repo, revision):
    raw, binding = reviewed_source(repo, revision, PRODUCER, PRODUCER_SHA)
    need(MODULE not in sys.modules, 'producer module already loaded')
    module = ModuleType(MODULE)
    module.__file__, module.__cached__, module.__package__ = str(repo / PRODUCER), None, ''
    sys.modules[MODULE] = module
    try:
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(MODULE, None)
        raise
    return module, binding


def verify_command(record, argv, cwd, venv, offline, out, cache, tools, label):
    need(isinstance(record, dict) and record.get('argv') == [str(x) for x in argv]
         and record.get('cwd') == str(cwd) and type(record.get('exit_code')) is int and record['exit_code'] == 0
         and type(record.get('owned_pid')) is int and record['owned_pid'] > 1
         and record.get('owned_parent_stopped') is True and record.get('owned_group_stopped') is True,
         'qualification command identity or exit differs')
    environment = record.get('environment')
    expected_env = {'PATH': str(tools['python'].parent) + ':' + str(tools['uv'].parent) + ':/usr/bin:/bin',
                    'HOME': str(out / 'home'), 'TMPDIR': str(out / 'tmp'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                    'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'UV_PYTHON_DOWNLOADS': 'never',
                    'UV_NO_CONFIG': '1', 'UV_NO_BUILD': '1', 'UV_KEYRING_PROVIDER': 'disabled',
                    'UV_DEFAULT_INDEX': 'https://pypi.org/simple', 'UV_CACHE_DIR': str(cache), 'UV_NO_PROGRESS': '1',
                    'UV_NO_INSTALLER_METADATA': '1', 'UV_COMPILE_BYTECODE': '0'}
    if venv is not None:
        expected_env['UV_PROJECT_ENVIRONMENT'] = str(venv)
    if offline:
        expected_env['UV_OFFLINE'] = '1'
    need(environment == expected_env, 'qualification command environment or offline mode differs')
    need(re.fullmatch(r'[a-z0-9-]+', label) is not None, 'qualification log label invalid')
    for stream in ('stdout', 'stderr'):
        relative = 'logs/' + label + '.' + stream
        need(record.get(stream) == relative, 'qualification raw log path differs')
        path = out / relative
        physical(path)
        need(path.is_file() and path.stat().st_nlink == 1 and type(record.get(stream + '_bytes')) is int
             and record[stream + '_bytes'] == path.stat().st_size
             and isinstance(record.get(stream + '_sha256'), str)
             and record[stream + '_sha256'] == file_sha(path), 'qualification raw log bytes differ')


def verify_seed(producer, seed, closure, tools, out, key):
    root, cache, wheels = closure['root'], closure['cache'], closure['wheels']
    project, evidence = root / 'project', root / 'cache-preparation'
    venv = evidence / 'venv'
    need(isinstance(seed, dict) and seed.get('version') == 'som.frozen-url-cache-preparation.v1'
         and seed.get('state') == 'frozen-url-cache-prepared' and seed.get('product_acceptance_passed') is False
         and seed.get('original_inputs_unchanged') is True and seed.get('installed_code_executed') is False
         and seed.get('owned_seed_venv_removed') is True and seed.get('offline_product_sync') == 'required separately'
         and seed.get('network_scope') == 'preparation using the unchanged frozen official publisher URLs'
         and seed.get('installer_mode') == 'qualification seed only; ordinary product mode is unchanged'
         and seed.get('cache') == str(cache) and seed.get('venv') == str(venv), 'cache seed receipt differs')
    need(not venv.exists() and not venv.is_symlink(), 'cache seed virtual environment not removed')
    need(sha(producer.read(project / 'pyproject.toml')) == seed.get('project_sha256')
         and sha(producer.read(project / 'uv.lock')) == seed.get('uv_lock_sha256'), 'cache seed project or lock differs')
    need(producer.file_sha(evidence / 'installed-files.json') == seed.get('installed_inventory_sha256'),
         'cache seed inventory differs')
    saved = producer.parse(producer.read(evidence / 'receipt.json'))
    checkpoint = producer.parse(producer.read(evidence / 'before-cleanup.json'))
    prior = dict(seed)
    prior.pop('owned_seed_venv_removed')
    need(saved == seed and checkpoint == prior, 'cache seed durable receipts differ')
    commands = seed.get('commands')
    need(isinstance(commands, list) and len(commands) == 1, 'cache seed command count differs')
    argv = [tools['uv'], 'sync', '--frozen', '--python', tools['python'], '--no-managed-python', '--no-config',
            '--no-build', '--no-index', '--find-links', wheels, '--link-mode', 'copy']
    verify_command(commands[0], argv, project, venv, False, out, cache, tools, 'cache-' + key + '-frozen-url-cache')


def verify_named(producer, record, closure, tools, out):
    root = out / 'environments' / 'react-api-qa'
    venv = root / 'venv'
    need(isinstance(record, dict) and record.get('state') == 'offline-environment-qualified'
         and record.get('frozen_sync_passed') is True and record.get('original_project_unchanged') is True
         and record.get('cleanup', {}).get('removed') is True and not venv.exists() and not venv.is_symlink(),
         'named offline environment or cleanup differs')
    saved = producer.parse(producer.read(root / 'receipt.json'))
    checkpoint_raw = producer.read(root / 'qualification-before-cleanup.json')
    before_cleanup = dict(record)
    before_cleanup.pop('cleanup')
    before_cleanup.pop('qualification_before_cleanup_sha256')
    need(saved == record and sha(checkpoint_raw) == record.get('qualification_before_cleanup_sha256')
         and producer.parse(checkpoint_raw) == before_cleanup, 'named durable receipts differ')
    commands = record.get('command_receipts')
    need(isinstance(commands, list), 'named offline commands missing')
    need(len(commands) == 3, 'named offline command count differs')
    common = ['--python', tools['python'], '--no-managed-python', '--no-config', '--no-build', '--offline']
    expected = [
        ([tools['uv'], 'lock', *common], 'react-api-qa-offline-original-lock', venv),
        ([tools['uv'], 'sync', '--frozen', *common, '--no-index', '--find-links', closure['wheels'], '--link-mode', 'copy'],
         'react-api-qa-offline-frozen-sync', venv),
        ([tools['python'], '-I', '-S', '-B', out / 'probe-installed-metadata.py', venv / 'lib/python3.12/site-packages'],
         'react-api-qa-offline-no-site-metadata', None),
    ]
    probe = out / 'probe-installed-metadata.py'
    physical(probe)
    need(probe.is_file() and probe.stat().st_nlink == 1 and probe.read_bytes() == producer.PROBE.encode(),
         'metadata probe source differs')
    for command, (argv, label, command_venv) in zip(commands, expected):
        verify_command(command, argv, root, command_venv, True, out, closure['cache'], tools, label)


def prepare(repo, revision, runtime, out):
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'GitHub hosted Linux required')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    need(release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '22.04',
         'qualified Ubuntu 22.04 required')
    need(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode, 'use Python -I -S -B')
    need(revision == os.environ.get('GITHUB_SHA'), 'workflow and source revisions differ')
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    for path in (repo, runtime, out):
        physical(path)
    need(repo.is_dir() and runtime.is_dir() and out == temp / 'som-react-python-closure'
         and out.parent == temp and not out.exists(), 'input or new output path refused')
    need(not (out == runtime or out in runtime.parents or runtime in out.parents), 'runtime/output overlap')
    need(Path(__file__).absolute() == repo / WRAPPER, 'fixed wrapper source path required')
    need(Path(sys.executable).absolute() == runtime / 'install/python/bin/python3.12'
         and file_sha(Path(sys.executable)) == PYTHON_SHA, 'wrapper interpreter differs from qualified Python')
    own_raw, own_binding = reviewed_source(repo, revision, WRAPPER)
    requirements_raw, requirements_binding = reviewed_source(repo, revision, REQUIREMENTS, REQUIREMENTS_SHA)
    row = requirements_row(requirements_raw)
    producer, producer_binding = load_producer(repo, revision)
    sources = producer.Sources(repo, revision)
    need(sources.read(WRAPPER) == own_raw and sources.read(PRODUCER) == (repo / PRODUCER).read_bytes()
         and sources.read(REQUIREMENTS) == requirements_raw, 'source binding changed')
    policy_raw = sources.read(producer.POLICY)
    installer_raw = sources.read(producer.INSTALLER_AUTHORITY)
    need(sha(policy_raw) == POLICY_SHA and sha(installer_raw) == INSTALLER_SHA, 'wheel or installer policy differs')
    for path, expected in NATIVE_SOURCE_SHAS.items():
        need(sha(sources.read(path)) == expected, 'reviewed native runtime source differs')
    policy = producer.parse(policy_raw)
    need(policy.get('version') == 'som.hosted-python-wheel-policy.v1' and policy.get('python') == '3.12.11'
         and policy.get('uv') == '0.9.9' and policy.get('host') == 'x86_64-manylinux_2_35', 'wheel policy identity differs')
    assets = producer.installer_assets(installer_raw)
    reuse, qualification, inventory, tools, native = producer.admitted_runtime(runtime, sources, policy, revision)
    need(tools['python'] == Path(sys.executable).absolute(), 'admitted Python identity differs')
    uv_bytes = producer.read(tools['uv'], producer.MAX_WHEEL)
    need(sha(uv_bytes) == native['uv_sha256'] and all(value in uv_bytes for value in assets.values()),
         'qualified uv installer assets differ')
    return producer, sources, row, tools, native, assets, reuse, qualification, inventory, {
        'wrapper': own_binding, 'producer': producer_binding, 'requirements': requirements_binding,
        'wheel_policy_sha256': sha(policy_raw), 'installer_authority_sha256': sha(installer_raw)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = {'version': 'som.react-api-python-wheel-closure-draft.v1', 'state': 'incomplete',
              'product_runtime_ready': False, 'product_acceptance_passed': False,
              'independent_wheel_audit': 'unrun', 'product_tests': 'unrun',
              'compressed_wheel_tag_fix': 'source-bound producer 9763; independent actual closure unverified',
              'commands': [], 'closures': [], 'environments': []}
    created = False
    try:
        producer, sources, row, tools, native, assets, reuse, qualification, inventory, bindings = prepare(
            args.repo, args.source_revision, args.runtime, args.out)
        args.out.mkdir()
        created = True
        for name in ('home', 'tmp', 'logs', 'closures', 'environments'):
            (args.out / name).mkdir()
        report.update(source_revision=args.source_revision, source_bindings=bindings, native_runtime=native,
                      requirements_count=39, project_role='new React API QA environment; not a generated product')
        runner = producer.Runner(args.out, report, tools, native, assets)
        probe = args.out / 'probe-installed-metadata.py'
        probe.write_text(producer.PROBE)
        report['probe_sha256'] = producer.file_sha(probe)
        key = producer.dependency_key(row['requirements_raw'], native)
        closure_record = {'id': key, 'state': 'unrun', 'native_identity': native}
        report['closures'].append(closure_record)
        env_record = {'id': row['id'], 'dependency_set': key, 'state': 'unrun'}
        report['environments'].append(env_record)
        with producer.cancellation():
            closure = producer.resolve_set(runner, row, key, closure_record)
            for direct in row['requirements']['runtime']:
                match = PIN.fullmatch(direct)
                normalized = re.sub(r'[-_.]+', '-', match.group(1)).lower()
                need(closure['pins'].get(normalized, {}).get('version') == match.group(2),
                     'direct React requirement differs from closure')
            evidence = closure['root'] / 'cache-preparation'
            report['frozen_url_cache'] = {'state': 'started', 'receipt': str(evidence / 'receipt.json')}
            seed = producer.prepare_frozen_url_cache(runner, closure, closure['root'] / 'project',
                                                     closure['cache'], evidence, 'cache-' + key)
            verify_seed(producer, seed, closure, tools, args.out, key)
            report['frozen_url_cache'] = seed
            named = producer.provision_named(runner, row, closure, probe, env_record)
            verify_named(producer, named, closure, tools, args.out)
            producer.verify_closure(closure)
            reuse.verify_installed_tree(args.runtime, inventory, qualification)
            sources.verify()
            need((args.repo / REQUIREMENTS).read_bytes() == row['requirements_raw'], 'React requirements changed')
        report.update(state='react-qa-closure-qualified-independent-audit-unrun',
                      runtime_tree_verified_after=True, source_bytes_verified_after=True,
                      wheel_closure_qualified=True, offline_environment_qualified=True,
                      all_direct_versions_verified=True)
    except BaseException as exc:
        report.update(state='failed', error=type(exc).__name__)
        if report.get('frozen_url_cache', {}).get('state') == 'started':
            report['frozen_url_cache']['state'] = 'failed-or-unverified'
        if isinstance(exc, (Refusal, ValueError, InterruptedError)):
            report['reason'] = str(exc)
    if created:
        with (args.out / 'receipt.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write('\n')
    print(json.dumps({'state': report['state'], 'product_acceptance_passed': False,
                      'independent_wheel_audit': report['independent_wheel_audit'],
                      'error': report.get('error'), 'reason': report.get('reason')}))
    return 0 if report['state'] == 'react-qa-closure-qualified-independent-audit-unrun' else 1


if __name__ == '__main__':
    raise SystemExit(main())
