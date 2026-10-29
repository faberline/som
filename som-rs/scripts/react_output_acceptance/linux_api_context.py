"""Unsealed draft: admit explicit Linux API inputs only through React authority.

The caller MUST verify and load this file before execution against the latest
sealed receipt and its QA source authority. No current receipt activates this
draft. The separately reviewed live-input verifier is not yet available.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
from types import ModuleType

AUTHORITY = 'som-rs/contracts/react-output-v1/linux-api-qa-authority.json'
CONTEXT_SOURCE = 'som-rs/scripts/react_output_acceptance/linux_api_context.py'
NODE_SOURCE = 'som-rs/tests/react-output-v1/linux_qa_profile.py'
LIVE_SOURCE = 'som-rs/scripts/react_output_acceptance/linux_api_live_inputs.py'
RECORDER_SOURCE = 'som-rs/scripts/react_output_acceptance/record_api.py'
HOST_SOURCE = 'som-rs/scripts/react_output_acceptance/linux_api_host.py'
EVIDENCE_SOURCE = 'som-rs/scripts/react_output_acceptance/linux_api_evidence.py'
API_CHAIN = frozenset({
    'som-rs/tests/react-output-v1/run_api_contract.py',
    'som-rs/tests/react-output-v1/api_contract.mjs',
    'som-rs/behavior/typescript/host.py', 'som-rs/behavior/hosts.py',
    'som-rs/som-targets/qa/check_rust_products.py',
    'som-rs/scripts/react_output_acceptance/record_api_fetch.mjs',
    'som-rs/scripts/react_output_acceptance/check_products.py',
    'som-rs/scripts/react_output_acceptance/run_acceptance.py',
    'som-rs/scripts/react_output_acceptance/retained_evidence.py',
    'som-rs/tests/typescript-output-v1/tree_sha.py',
    'som-rs/som-targets/qa/check_targets.py',
    'som-rs/tests/rust-output-v1/rust_profile.py',
    'som-rs/tests/rust-output-v1/rust_profile_phase2.py',
    'som-rs/tests/rust-output-v1/build_negative_cases.py',
    'som-rs/scripts/rust_output_acceptance/python_replay.py',
    'som-rs/scripts/compiler_acceptance/qa_harness.py',
    'som-rs/scripts/compiler_acceptance/paths.py',
    'som-rs/tests/react-output-v1/check_contract.py',
    'som-rs/tests/react-output-v1/common.py',
    'som-rs/tests/react-output-v1/react_profile.py',
    'som-rs/tests/react-output-v1/tree_sha.py',
    'som-rs/contracts/typescript-output-v1/toolchain.json',
    'som-rs/contracts/typescript-output-v1/product-layout.json',
    'som-rs/contracts/typescript-output-v1/behavior-normalization.json',
    'som-rs/contracts/typescript-output-v1/acceptance-plan.json',
    'som-rs/contracts/rust-output-v1/toolchain.json',
    'som-rs/contracts/rust-output-v1/case-inventory.json',
    'som-rs/contracts/rust-output-v1/product-layout.json',
    'som-rs/contracts/rust-output-v1/bundle.schema.json',
    'som-rs/contracts/rust-output-v1/acceptance-plan.json',
    'som-rs/contracts/compiler-v1/compiler-prompt.schema.json',
})
LIVE_DEPENDENCIES = frozenset({
    'som-rs/scripts/react_output_acceptance/provision_live.py',
    'som-rs/scripts/react_output_acceptance/hosted_python_requirements.txt',
    'som-rs/scripts/hosted_readiness/product_python/produce_wheels.py',
    'som-rs/scripts/hosted_readiness/product_python/wheel-policy.json',
    'som-rs/scripts/hosted_readiness/product_python/uv099-installer-authority.json',
    'som-rs/scripts/hosted_readiness/product_runtime/reuse_runtime.py',
    'som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py',
    'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-qualification.json',
    'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-byte-audit.json',
    'som-rs/scripts/hosted_readiness/product_runtime/source-pins.json',
})
NPM_TREE = ('303f008099574a6edbb698df7d748a897f48f8a0c69cb1f3ee699e2937f50ca9', 1921, 0)
HEX = re.compile(r'[0-9a-f]{64}\Z')


class ContextError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise ContextError(message)


def physical(path, *, allow_missing=False):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'absolute physical path required')
    need(not any(item.is_symlink() for item in (path, *path.parents)), 'linked input refused')
    if not allow_missing:
        need(path.exists() and path.resolve() == path, 'input missing or nonphysical')
    return path


def read(path, limit):
    physical(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'input missing or oversized')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(limit + 1)
            need(len(raw) <= limit, 'input oversized')
            return raw
        finally:
            os.close(fd)
    except OSError as exc:
        raise ContextError('input unreadable') from exc


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate JSON member')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ContextError('invalid JSON value')))
    except (ValueError, UnicodeError) as exc:
        raise ContextError('invalid JSON') from exc


def receipt_files(repo):
    """Use the already bound collector receipt-chain verifier as trust anchor."""
    module = sys.modules.get('retained_evidence')
    path = physical(repo / 'som-rs/scripts/react_output_acceptance/retained_evidence.py')
    need(module is not None and getattr(module, '__file__', None) == str(path)
         and callable(getattr(module, 'sealed_files', None)), 'bound receipt verifier unavailable')
    # The entry-point's source bootstrap must authenticate retained_evidence
    # before importing it. This check is not a substitute for that bootstrap.
    files = module.sealed_files(repo)
    need(isinstance(files, dict), 'sealed receipt unavailable')
    return files


def source_bytes(repo, relative, expected):
    need(isinstance(expected, str) and HEX.fullmatch(expected) is not None, 'source SHA missing')
    raw = read(repo / relative, 1024 * 1024)
    need(sha(raw) == expected, 'sealed source differs')
    return raw


def load_bound(repo, relative, expected, name):
    raw = source_bytes(repo, relative, expected)
    need(name not in sys.modules, 'preloaded helper refused')
    module = ModuleType(name)
    module.__file__ = str(repo / relative)
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    sys.modules[name] = module
    return module


def authority(repo):
    files = receipt_files(repo)
    expected = files.get(AUTHORITY)
    need(isinstance(expected, str) and HEX.fullmatch(expected) is not None, 'Linux API authority not sealed')
    raw = read(repo / AUTHORITY, 1024 * 1024)
    need(sha(raw) == expected, 'Linux API authority differs')
    doc = parse(raw)
    need(isinstance(doc, dict) and set(doc) == {'version', 'api_profile_sha256', 'node_profile_sha256', 'source_sha256'}
         and doc['version'] == 'som.react-linux-api-authority.v1', 'Linux API authority format differs')
    sources = doc['source_sha256']
    need(isinstance(sources, dict) and set(sources) == {CONTEXT_SOURCE, NODE_SOURCE, LIVE_SOURCE, RECORDER_SOURCE, HOST_SOURCE, EVIDENCE_SOURCE} | LIVE_DEPENDENCIES | API_CHAIN
         and all(isinstance(value, str) and HEX.fullmatch(value) is not None for value in sources.values())
         and all(isinstance(doc[key], str) and HEX.fullmatch(doc[key]) is not None
                 for key in ('api_profile_sha256', 'node_profile_sha256')), 'Linux API authority inventory differs')
    need(files.get(NODE_SOURCE) == sources[NODE_SOURCE], 'node helper not in sealed React map')
    for frozen in ('som-rs/tests/react-output-v1/run_api_contract.py', 'som-rs/tests/react-output-v1/api_contract.mjs'):
        need(files.get(frozen) == sources[frozen], 'API frozen source not in sealed React map')
    for source, expected in sources.items():
        source_bytes(repo, source, expected)
    return doc, sha(raw)


def host():
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'GitHub Linux host required')
    fields = {}
    for line in Path('/etc/os-release').read_text(encoding='utf-8').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            need(key not in fields, 'duplicate host release field')
            fields[key] = value.strip('"')
    need(fields.get('ID') == 'ubuntu' and fields.get('VERSION_ID') == '22.04', 'Ubuntu 22.04 required')


def tool(path):
    """Record OS tool identity; authority does not invent a frozen tool hash."""
    try:
        resolved = Path(path).resolve(strict=True)
        raw = read(resolved, 64 * 1024 * 1024)
        info = resolved.stat()
        need(stat.S_ISREG(info.st_mode) and bool(info.st_mode & 0o111) and os.access(path, os.X_OK),
             'OS observation tool not executable')
        return {'path': path, 'resolved_path': str(resolved), 'sha256': sha(raw),
                'mode': stat.S_IMODE(info.st_mode)}
    except OSError as exc:
        raise ContextError('OS observation tool unavailable') from exc


def inputs(profile_path, repo):
    host()
    repo = physical(repo)
    temp = physical(Path(os.environ['RUNNER_TEMP']))
    profile_path = physical(profile_path)
    need(profile_path == temp / 'som-react-api-context.json', 'API profile path differs')
    auth, auth_sha = authority(repo)
    raw = read(profile_path, 4096)
    need(sha(raw) == auth['api_profile_sha256'], 'API profile differs from authority')
    profile = parse(raw)
    closure = temp / 'som-react-python-closure'
    expected = {'version': 'som.react-linux-api-profile.v1', 'host': 'ubuntu-22.04-x86_64',
                'node_profile': str(temp / 'som-react-linux-qa-profile.json'),
                'runtime_root': str(temp / 'som-product-runtime-qualification'),
                'closure_root': str(closure), 'venv': str(closure / 'environments/react-api-qa/venv'),
                'live_receipt': str(closure / 'environments/react-api-qa/live-provision-receipt.json')}
    need(profile == expected, 'API profile contents differ')
    # Validate the exact public profile and receipt bytes before helpers execute.
    node_raw = read(Path(profile['node_profile']), 4096)
    need(sha(node_raw) == auth['node_profile_sha256'], 'node profile differs from authority')
    physical(Path(profile['runtime_root']))
    physical(Path(profile['closure_root']))
    physical(Path(profile['venv']))
    receipt_raw = read(Path(profile['live_receipt']), 16 * 1024 * 1024)
    return repo, profile_path, profile, auth, auth_sha, sha(raw), sha(receipt_raw)


def verify(repo, profile_path, profile, auth, auth_sha, profile_sha, receipt_sha, node_module, live_module):
    need(getattr(node_module, 'NPM_TREE', None) == NPM_TREE, 'node helper uses unreviewed npm ordering')
    node_path = Path(profile['node_profile'])
    node = node_module.validate_profile(node_path)
    need(isinstance(node, dict) and node.get('profile_path') == node_path
         and node.get('profile_sha256') == auth['node_profile_sha256'], 'node profile admission differs')
    node_module.recheck(node)
    verifier = getattr(live_module, 'verify', None)
    need(callable(verifier), 'live input verifier unavailable')
    live = verifier(profile)
    keys = {'status', 'python', 'pg_bin', 'live_receipt_sha256', 'runtime_receipt_sha256',
            'closure_receipt_sha256', 'venv_inventory_sha256', 'requirements_count', 'wheel_count'}
    need(isinstance(live, dict) and set(live) == keys and live['status'] == 'verified-live-inputs'
         and type(live['requirements_count']) is int and live['requirements_count'] == 39
         and type(live['wheel_count']) is int and live['wheel_count'] == 40
         and all(isinstance(live[key], str) and HEX.fullmatch(live[key]) is not None for key in
                 ('live_receipt_sha256', 'runtime_receipt_sha256', 'closure_receipt_sha256', 'venv_inventory_sha256'))
         and live['live_receipt_sha256'] == receipt_sha, 'live input verification differs')
    need(live['python'] == profile['venv'] + '/bin/python'
         and live['pg_bin'] == profile['runtime_root'] + '/install/postgresql/bin', 'live executable paths differ')
    # The source-bound verifier must check the permitted venv interpreter link,
    # every wheel/installed byte and the complete admitted runtime tree.
    result = {'node': node['node'], 'npm_cli': node['npm_cli'], 'cache': node['cache'],
            'node_profile_path': node_path, 'profile_sha256': node['profile_sha256'],
            'python': live['python'], 'pg_bin': live['pg_bin'], 'lsof': '/usr/bin/lsof', 'ps': '/bin/ps',
            'os_tools': {'lsof': tool('/usr/bin/lsof'), 'ps': tool('/bin/ps')},
            'context_path': profile_path, 'context_sha256': profile_sha, 'authority_sha256': auth_sha,
            'live_receipt_sha256': receipt_sha, 'runtime_receipt_sha256': live['runtime_receipt_sha256'],
            'closure_receipt_sha256': live['closure_receipt_sha256'],
            'venv_inventory_sha256': live['venv_inventory_sha256'],
            '_repo': repo, '_profile_path': profile_path, '_source_shas': dict(auth['source_sha256']),
            '_node_module': node_module, '_live_module': live_module}
    result['_case_baseline'] = case_evidence(result)
    return result


def executable(path, expected_resolved=None):
    """Hash a selected executable; permit only the verified venv Python link."""
    path = Path(path)
    physical(path.parent)
    resolved = path.resolve(strict=True)
    if expected_resolved is not None:
        need(resolved == Path(expected_resolved), 'venv Python link differs')
    else:
        need(resolved == path, 'executable path linked')
    raw = read(resolved, 180 * 1024 * 1024)
    mode = stat.S_IMODE(resolved.stat().st_mode)
    need(mode & 0o111 and os.access(path, os.X_OK), 'selected executable is not executable')
    return {'path': str(path), 'resolved_path': str(resolved), 'sha256': sha(raw), 'mode': mode}


def case_evidence(context):
    repo, path, profile, auth, auth_sha, profile_sha, receipt_sha = inputs(context['_profile_path'], context['_repo'])
    need(auth['source_sha256'] == context['_source_shas'], 'source authority changed')
    node_root = Path(profile['node_profile']).parent/'som-react-linux-qa-closure'
    node, npm, cache = node_root/'node/bin/node', node_root/'node/lib/node_modules/npm/bin/npm-cli.js', node_root/'npm-cache-new'
    python = Path(profile['venv'])/'bin/python'
    runtime = Path(profile['runtime_root'])
    pg_bin = runtime/'install/postgresql/bin'
    closure = Path(profile['closure_root'])/'closures'/'4669d1f312255b4d628224faad1326423741eac16521c3d59677eb994cd2f90b'
    runtime_sha = sha(read(runtime/'receipt.json', 32*1024*1024))
    closure_sha = sha(read(closure/'receipt.json', 32*1024*1024))
    inventory_sha = sha(read(Path(profile['venv']).parent/'live-installed-files.json', 32*1024*1024))
    actual = {'version':'som.react-linux-api-full-validation.v1',
              'run_id':os.environ.get('GITHUB_RUN_ID'), 'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),
              'context_path':str(path), 'context_sha256':profile_sha, 'authority_sha256':auth_sha,
              'node_profile_path':profile['node_profile'], 'node_profile_sha256':auth['node_profile_sha256'],
              'node':str(node), 'npm_cli':str(npm), 'cache':str(cache), 'python':str(python), 'pg_bin':str(pg_bin),
              'live_receipt_sha256':receipt_sha, 'runtime_receipt_sha256':runtime_sha,
              'closure_receipt_sha256':closure_sha, 'venv_inventory_sha256':inventory_sha,
              'executables':{'node':executable(node), 'python':executable(python, runtime/'install/python/bin/python3.12'),
                             **{name:executable(pg_bin/name) for name in ('initdb','postgres','pg_ctl')}},
              'os_tools':{'lsof':tool('/usr/bin/lsof'), 'ps':tool('/bin/ps')}}
    need(isinstance(actual['run_id'], str) and actual['run_id'].isdigit()
         and isinstance(actual['run_attempt'], str) and actual['run_attempt'].isdigit(), 'runner identity missing')
    for key in ('node','npm_cli','python','pg_bin'):
        need(str(context[key]) == actual[key], 'case executable path differs')
    need(str(context['cache']) == actual['cache'] and context['profile_sha256'] == actual['node_profile_sha256']
         and context['context_sha256'] == profile_sha and context['authority_sha256'] == auth_sha
         and context['live_receipt_sha256'] == receipt_sha and context['runtime_receipt_sha256'] == runtime_sha
         and context['closure_receipt_sha256'] == closure_sha and context['venv_inventory_sha256'] == inventory_sha,
         'case identity differs')
    return actual


def full_record(context: dict) -> dict:
    """Return run-before evidence only after this process completed full verify."""
    need(context.get('_node_module') is not None and context.get('_live_module') is not None,
         'full context required')
    actual = case_evidence(context)
    need(actual == context['_case_baseline'], 'case identity changed after full verification')
    return actual


def recheck_case(context: dict) -> None:
    """Check selected executables and receipts, not every venv byte."""
    need(case_evidence(context) == context['_case_baseline'], 'per-case identity changed')


def validate_case(profile_path: Path, repo: Path, full_before: Path) -> dict:
    """Join one case to the source-bound collector's physical full-before record.

    This does not establish the record's producer or prove every venv file
    unchanged during a case; the independent auditor must check both roles.
    """
    repo, path, profile, auth, auth_sha, profile_sha, receipt_sha = inputs(Path(profile_path), Path(repo))
    before = physical(full_before)
    need(before.name == 'full-before.json' and before.parent.name == 'api-qa-evidence'
         and before.is_relative_to(repo) and before.parent.parent != repo, 'full-before path differs')
    before_raw = read(before, 1024*1024)
    baseline = parse(before_raw)
    need(isinstance(baseline, dict) and baseline.get('version') == 'som.react-linux-api-full-validation.v1',
         'full-before record differs')
    context = {'node':baseline.get('node'), 'npm_cli':baseline.get('npm_cli'), 'cache':baseline.get('cache'),
               'node_profile_path':Path(profile['node_profile']), 'profile_sha256':auth['node_profile_sha256'],
               'python':baseline.get('python'), 'pg_bin':baseline.get('pg_bin'), 'lsof':'/usr/bin/lsof', 'ps':'/bin/ps',
               'os_tools':baseline.get('os_tools'), 'context_path':path, 'context_sha256':profile_sha,
               'authority_sha256':auth_sha, 'live_receipt_sha256':receipt_sha,
               'runtime_receipt_sha256':baseline.get('runtime_receipt_sha256'),
               'closure_receipt_sha256':baseline.get('closure_receipt_sha256'),
               'venv_inventory_sha256':baseline.get('venv_inventory_sha256'), '_repo':repo, '_profile_path':path,
               '_source_shas':dict(auth['source_sha256']), '_case_baseline':baseline,
               'full_before_path':before, 'full_before_sha256':sha(before_raw)}
    recheck_case(context)
    return context


def validate(profile_path: Path, repo: Path, *, bound_node_module=None) -> dict:
    """Admit inputs. Reuse a node module only when the caller bound it first.

    A caller passing bound_node_module must already have authenticated that
    exact module's source bytes before execution using the same receipt.
    """
    repo, path, profile, auth, auth_sha, profile_sha, receipt_sha = inputs(Path(profile_path), Path(repo))
    if bound_node_module is None:
        node_module = load_bound(repo, NODE_SOURCE, auth['source_sha256'][NODE_SOURCE], 'linux_qa_profile')
    else:
        source_bytes(repo, NODE_SOURCE, auth['source_sha256'][NODE_SOURCE])
        need(sys.modules.get('linux_qa_profile') is bound_node_module
             and getattr(bound_node_module, '__file__', None) == str(repo / NODE_SOURCE),
             'caller-bound node helper differs')
        node_module = bound_node_module
    live_module = load_bound(repo, LIVE_SOURCE, auth['source_sha256'][LIVE_SOURCE], 'linux_api_live_inputs')
    return verify(repo, path, profile, auth, auth_sha, profile_sha, receipt_sha, node_module, live_module)


def recheck(context: dict) -> None:
    repo, path, profile, auth, auth_sha, profile_sha, receipt_sha = inputs(context['_profile_path'], context['_repo'])
    need(auth['source_sha256'] == context['_source_shas'], 'source authority changed')
    for source, expected in auth['source_sha256'].items():
        source_bytes(repo, source, expected)
    after = verify(repo, path, profile, auth, auth_sha, profile_sha, receipt_sha,
                   context['_node_module'], context['_live_module'])
    keys = set(after) - {'_node_module', '_live_module'}
    need(all(after[key] == context.get(key) for key in keys), 'API inputs changed')
