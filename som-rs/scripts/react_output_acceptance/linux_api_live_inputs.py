"""Unsealed draft: verify fresh React QA venv and runtime without trusting its receipt.

The caller authenticates and executes this source from the sealed API source
map. The source map and the live provisioner are not yet sealed; this module
must never be interpreted as a qualification or product acceptance result.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from types import ModuleType

BASE = 'som-rs/scripts/hosted_readiness/'
PRODUCER = BASE + 'product_python/produce_wheels.py'
PROVISIONER = 'som-rs/scripts/react_output_acceptance/provision_live.py'
REQUIREMENTS = 'som-rs/scripts/react_output_acceptance/hosted_python_requirements.txt'
POLICY = BASE + 'product_python/wheel-policy.json'
INSTALLER = BASE + 'product_python/uv099-installer-authority.json'
RUNTIME = BASE + 'product_runtime/'
NATIVE_PATHS = (RUNTIME+'reuse_runtime.py', RUNTIME+'qualify_runtime.py',
                RUNTIME+'runtime-bf3-qualification.json', RUNTIME+'runtime-bf3-byte-audit.json',
                RUNTIME+'source-pins.json')
AUTHORITY = 'som-rs/contracts/react-output-v1/linux-api-qa-authority.json'
CONTEXT_SOURCE = 'som-rs/scripts/react_output_acceptance/linux_api_context.py'
LIVE_SOURCE = 'som-rs/scripts/react_output_acceptance/linux_api_live_inputs.py'
NODE_SOURCE = 'som-rs/tests/react-output-v1/linux_qa_profile.py'
RECORDER_SOURCE = 'som-rs/scripts/react_output_acceptance/record_api.py'
HOST_SOURCE = 'som-rs/scripts/react_output_acceptance/linux_api_host.py'
EVIDENCE_SOURCE = 'som-rs/scripts/react_output_acceptance/linux_api_evidence.py'
SOURCE_SET = frozenset((CONTEXT_SOURCE, LIVE_SOURCE, NODE_SOURCE, RECORDER_SOURCE, HOST_SOURCE, EVIDENCE_SOURCE,
                        PROVISIONER, REQUIREMENTS, PRODUCER, POLICY, INSTALLER, *NATIVE_PATHS,
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
                        'som-rs/contracts/compiler-v1/compiler-prompt.schema.json'))
KEY = '4669d1f312255b4d628224faad1326423741eac16521c3d59677eb994cd2f90b'
PRODUCER_SHA = 'c9151e7b09b0d7fcadcc203f72b83bf1135f2da260becf2034f4228b26299a39'
PROVISIONER_SHA = 'd912a1c4c88c84dd2c29af64713985b367fcf70dbfc173b8ca95c7b2d7539ef1'
REQ_SHA = '217f0d88231ad9d10cca3232a8707cdbb3355bd9a41e19fbddbab0d299c6db35'
POLICY_SHA = '7a7532ed476f2e983fedfdf4304f8034765efb51df89caac604ad89c953442d7'
INSTALLER_SHA = 'e166303c1376a77d6e92ae19d9ff0eddcdd24c3adbde8381ff835274bb850358'
TAR_SHA = '8a1ead27a72911acb9af76c622311c5924ad91073b3fc9a221a44db6a0dd4e3b'
AUDIT_SHA = '3fb0075a6795d4f4efcb0845f8ee05dcf16947f7e946954a02a3835677eb8389'
CLOSURE_SHA = 'c6669bc6820eea75d1f0cc596ed4d41a62936a1854026ecf20bee9510d2eb200'
ZIP_SHA = 'd568ebe48167bec64b4d067f1e2e246b625e16852fc6201bef3689c13c7850bf'
ORIGINAL_HEAD = '071c55b0f02e2325a6f0993636d5330005b6a4fe'
EXPECTED_TEMP = '/home/runner/work/_temp'
PIN = re.compile(r'([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
_binding = None


class InputError(ValueError):
    pass


def need(value, message):
    if not value:
        raise InputError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit=180 * 1024 * 1024):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and not any(x.is_symlink() for x in (path, *path.parents)),
         'live source path refused')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit, 'live source file refused')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(limit + 1)
            need(len(raw) <= limit, 'live source file oversized')
            return raw
        finally:
            os.close(fd)
    except OSError as exc:
        raise InputError('live source file unavailable') from exc


def hash_file(path, limit):
    path = Path(path)
    need(path.is_absolute() and not any(x.is_symlink() for x in (path, *path.parents)), 'live source path refused')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit, 'live source file refused')
            digest, count = hashlib.sha256(), 0
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    count += len(chunk)
                    need(count <= limit, 'live source file oversized')
                    digest.update(chunk)
            return digest.hexdigest()
        finally:
            os.close(fd)
    except OSError as exc:
        raise InputError('live source file unavailable') from exc


def document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate live JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(InputError('invalid JSON value')))
    except (UnicodeError, ValueError) as exc:
        raise InputError('invalid live JSON') from exc
    need(isinstance(value, dict), 'live JSON object required')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def bound_sources(repo, revision):
    """Bind all executable dependencies before executing the pinned producer."""
    global _binding
    receipt = sys.modules.get('retained_evidence')
    need(receipt is not None and getattr(receipt, '__file__', None) == str(repo/'som-rs/scripts/react_output_acceptance/retained_evidence.py')
         and callable(getattr(receipt, 'sealed_files', None)), 'bound receipt verifier unavailable')
    files = receipt.sealed_files(repo)
    auth_raw = read(repo/AUTHORITY, 1024 * 1024)
    need(files.get(AUTHORITY) == sha(auth_raw), 'live source authority not sealed')
    authority = document(auth_raw)
    source_shas = authority.get('source_sha256')
    need(isinstance(source_shas, dict) and set(source_shas) == SOURCE_SET
         and all(isinstance(value, str) and HEX.fullmatch(value) is not None for value in source_shas.values()),
         'live source inventory differs')
    raw_sources = {name:read(repo/name, 32 * 1024 * 1024) for name in SOURCE_SET}
    need(all(sha(raw_sources[name]) == source_shas[name] for name in SOURCE_SET), 'live source bytes differ')
    for name, fixed in ((PRODUCER, PRODUCER_SHA), (PROVISIONER, PROVISIONER_SHA),
                        (REQUIREMENTS, REQ_SHA), (POLICY, POLICY_SHA), (INSTALLER, INSTALLER_SHA)):
        need(source_shas[name] == fixed, 'pinned producer source differs')
    # The caller supplies no verifier, digest, or path as authority. The
    # receipt-bound producer is executed only after every dependency is bound.
    if _binding is None:
        name = 'som_react_api_verified_wheel_producer'
        need(name not in sys.modules, 'preloaded producer refused')
        producer = ModuleType(name)
        producer.__file__, producer.__package__ = str(repo/PRODUCER), ''
        exec(compile(raw_sources[PRODUCER], producer.__file__, 'exec', dont_inherit=True), producer.__dict__)
        sys.modules[name] = producer
        _binding = {'repo':repo, 'revision':revision, 'source_shas':dict(source_shas), 'producer':producer,
                    'reuse':None, 'native':None, 'qualification':None, 'runtime_inventory':None, 'tools':None}
    else:
        need(_binding['repo'] == repo and _binding['revision'] == revision
             and _binding['source_shas'] == source_shas
             and sys.modules.get('som_react_api_verified_wheel_producer') is _binding['producer'],
             'bound live verifier identity changed')
    sources = _binding['producer'].Sources(repo, revision)
    for name in SOURCE_SET:
        need(sources.read(name) == raw_sources[name], 'live source is not committed working source')
    return _binding, sources, raw_sources


def verify_command(row, argv, cwd, env, logs, label):
    need(isinstance(row, dict) and row.get('argv') == [str(x) for x in argv] and row.get('cwd') == str(cwd)
         and row.get('environment') == env and row.get('status') == 'completed'
         and type(row.get('pid')) is int and row['pid'] > 1
         and type(row.get('exit_code')) is int and row['exit_code'] == 0
         and type(row.get('wait_result')) is int and row['wait_result'] == 0
         and row.get('group_stopped') is True, 'live command or wait differs')
    elapsed = row.get('elapsed_seconds')
    need(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0, 'live command timing differs')
    result = {}
    for kind in ('stdout', 'stderr'):
        path = logs / (label + '.' + kind)
        need(row.get(kind) == str(path), 'live command log path differs')
        raw = read(path, 32 * 1024 * 1024)
        need(type(row.get(kind+'_bytes')) is int and row[kind+'_bytes'] == len(raw)
             and row.get(kind+'_sha256') == sha(raw), 'live command log differs')
        result[kind] = raw
    return result


def verify_runner_identity(runtime_raw):
    runner = document(runtime_raw).get('runner')
    need(isinstance(runner, dict) and isinstance(os.environ.get('GITHUB_RUN_ID'), str)
         and os.environ['GITHUB_RUN_ID'].isdigit() and runner.get('GITHUB_RUN_ID') == os.environ['GITHUB_RUN_ID']
         and isinstance(os.environ.get('GITHUB_RUN_ATTEMPT'), str) and os.environ['GITHUB_RUN_ATTEMPT'].isdigit()
         and runner.get('GITHUB_RUN_ATTEMPT') == os.environ['GITHUB_RUN_ATTEMPT'], 'runtime run identity differs')


def verify(profile):
    """Recompute live runtime, wheel, venv and two command receipts each call."""
    need(isinstance(profile, dict), 'live API profile missing')
    repo = Path(__file__).absolute().parents[3]
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    need(str(temp) == EXPECTED_TEMP, 'runner temp differs')
    root, runtime = temp/'som-react-python-closure', temp/'som-product-runtime-qualification'
    named, venv = root/'environments/react-api-qa', root/'environments/react-api-qa/venv'
    need(profile.get('runtime_root') == str(runtime) and profile.get('closure_root') == str(root)
         and profile.get('venv') == str(venv) and profile.get('live_receipt') == str(named/'live-provision-receipt.json'),
         'live API paths differ')
    revision = os.environ.get('GITHUB_SHA', '')
    need(re.fullmatch(r'[0-9a-f]{40}', revision) is not None, 'live source revision missing')
    binding, sources, raw_sources = bound_sources(repo, revision)
    producer = binding['producer']
    policy_raw, installer_raw = raw_sources[POLICY], raw_sources[INSTALLER]
    policy, assets = producer.parse(policy_raw), producer.installer_assets(installer_raw)
    if binding['reuse'] is None:
        reuse, qualification, inventory, tools, native = producer.admitted_runtime(runtime, sources, policy, revision)
        binding.update(reuse=reuse, qualification=qualification, runtime_inventory=inventory, tools=tools, native=native)
    reuse, qualification, inventory, tools, native = (binding[k] for k in ('reuse', 'qualification', 'runtime_inventory', 'tools', 'native'))
    runtime_raw = read(runtime/'receipt.json', 32 * 1024 * 1024)
    need(sha(runtime_raw) == native['reuse_receipt_sha256'], 'runtime receipt changed')
    verify_runner_identity(runtime_raw)
    reuse.verify_installed_tree(runtime, inventory, qualification)
    need(all(value in producer.read(tools['uv'], producer.MAX_WHEEL) for value in assets.values()), 'uv installer assets differ')
    audit_raw = read(temp/'react39-inputs/audit/react-python-independent-audit.json', 1024 * 1024)
    audit = document(audit_raw)
    need(sha(audit_raw) == AUDIT_SHA and audit.get('state') == 'partial-evidence-consistent'
         and audit.get('product_acceptance_passed') is False and audit.get('run_id') == 40821432963
         and audit.get('artifact_id') == 12874482658 and audit.get('source_commit') == ORIGINAL_HEAD
         and audit.get('artifact_zip_sha256') == ZIP_SHA and audit.get('artifact_tar_sha256') == TAR_SHA
         and audit.get('requirements_count') == 39 and audit.get('wheel_count') == 40, 'independent audit differs')
    # The artifact TAR is immutable input; hash from a bounded stream, not a caller receipt.
    tar_path = temp/'react39-inputs/source/react-python-qa-closure.tar'
    need(hash_file(tar_path, 2 * 1024**3) == TAR_SHA, 'audited closure TAR differs')
    closure_root = root/'closures'/KEY
    closure_raw = read(closure_root/'receipt.json', 32 * 1024 * 1024)
    need(sha(closure_raw) == CLOSURE_SHA, 'frozen closure receipt differs')
    closure_record = document(closure_raw)
    selected = producer.locked_requirements(producer.read(closure_root/'requirements.selected.lock'))
    offline = producer.locked_requirements(producer.read(closure_root/'requirements.offline.lock'))
    direct = {}
    for line in raw_sources[REQUIREMENTS].decode('utf-8', 'strict').splitlines():
        if not line or line.startswith('#'):
            continue
        match = PIN.fullmatch(line)
        need(match is not None, 'direct requirement differs')
        name = producer.normalize(match[1])
        need(name not in direct, 'direct requirement duplicated')
        direct[name] = match[2]
    need(len(direct) == 39 and len(selected) == 40 and selected == offline
         and all(name in selected and selected[name]['version'] == value for name, value in direct.items()),
         'direct or offline lock differs')
    rows = closure_record.get('wheels')
    need(isinstance(rows, list) and len(rows) == 40 and all(isinstance(row, dict) for row in rows), 'wheel inventory differs')
    names = [row.get('name') for row in rows]
    need(len(set(names)) == 40 and set(names) == set(selected), 'wheel name inventory differs')
    need(all(row.get('version') == selected[row['name']]['version'] and selected[row['name']]['hashes'] == [row.get('sha256')]
             for row in rows), 'wheel lock binding differs')
    audits = {name:document(read(closure_root/'wheel-audits'/name/'inventory.json', 32*1024*1024)) for name in names}
    closure = {'root':closure_root, 'cache':closure_root/'cache', 'wheels':closure_root/'wheels',
               'pins':selected, 'record':closure_record, 'audits':audits}
    producer.verify_closure(closure)
    raw = read(named/'live-provision-receipt.json', 16*1024*1024)
    receipt = document(raw)
    expected = {'version':'som.react-live-venv.v1', 'state':'fresh-offline-venv-ready-product-tests-unrun',
                'product_acceptance_passed':False, 'source_run_id':40821432963, 'source_artifact_id':12874482658,
                'source_zip_sha256':ZIP_SHA, 'source_tar_sha256':TAR_SHA, 'independent_audit_run_id':40823392027,
                'independent_audit_artifact_id':12875218806, 'independent_audit_sha256':AUDIT_SHA,
                'source_revision':revision, 'closure_receipt_sha256':CLOSURE_SHA,
                'runtime_receipt_sha256':sha(runtime_raw), 'requirements_sha256':REQ_SHA,
                'requirements_count':39, 'wheel_count':40, 'venv':str(venv)}
    need(all(receipt.get(k) == v and type(receipt.get(k)) is type(v) for k,v in expected.items()), 'live receipt identity differs')
    need(venv.is_dir() and not venv.is_symlink(), 'fresh live venv absent')
    env = {'PATH':str(tools['python'].parent)+':'+str(tools['uv'].parent)+':/usr/bin:/bin',
           'HOME':str(root/'home'), 'TMPDIR':str(root/'tmp'), 'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONNOUSERSITE':'1', 'UV_PYTHON_DOWNLOADS':'never', 'UV_NO_CONFIG':'1',
           'UV_NO_BUILD':'1', 'UV_KEYRING_PROVIDER':'disabled', 'UV_DEFAULT_INDEX':'https://pypi.org/simple',
           'UV_CACHE_DIR':str(closure['cache']), 'UV_NO_PROGRESS':'1', 'UV_NO_INSTALLER_METADATA':'1',
           'UV_COMPILE_BYTECODE':'0', 'UV_OFFLINE':'1', 'UV_PROJECT_ENVIRONMENT':str(venv)}
    commands = receipt.get('commands')
    need(isinstance(commands, list) and len(commands) == 2, 'live command inventory differs')
    command = [tools['uv'],'sync','--frozen','--python',tools['python'],'--no-managed-python','--no-config',
               '--no-build','--offline','--no-index','--find-links',closure['wheels'],'--link-mode','copy']
    verify_command(commands[0], command, named, env, root/'logs', 'fresh-offline-frozen-sync')
    probe = root/'probe-installed-metadata.py'
    need(read(probe, 1024*1024) == producer.PROBE.encode() and receipt.get('probe_sha256') == sha(producer.PROBE.encode()),
         'live metadata probe differs')
    metadata_env = dict(env)
    del metadata_env['UV_PROJECT_ENVIRONMENT']
    logs = verify_command(commands[1], [tools['python'],'-I','-S','-B',probe,venv/'lib/python3.12/site-packages'],
                          named, metadata_env, root/'logs', 'fresh-offline-no-site-metadata')
    metadata_raw = read(named/'live-installed-metadata.json', 32*1024*1024)
    need(receipt.get('installed_metadata_sha256') == sha(metadata_raw) and canonical(document(logs['stdout'])) == canonical(document(metadata_raw)),
         'live metadata output differs')
    observation = document(metadata_raw)
    need(observation.get('python') == '3.12.11' and observation.get('executable') == str(tools['python'])
         and observation.get('site') == str(venv/'lib/python3.12/site-packages')
         and observation.get('site_disabled') is True and observation.get('site_in_sys_path') is False, 'live metadata context differs')
    observed = {}
    for item in observation.get('packages', []):
        name = producer.normalize(item['name'])
        need(name not in observed and name in audits and item.get('version') == selected[name]['version']
             and item.get('metadata_sha256') == audits[name]['metadata_sha256'], 'live installed metadata differs')
        observed[name] = item['version']
    need(set(observed) == set(selected) and receipt.get('installed_versions') == observed, 'live package set differs')
    inventory_raw = read(named/'live-installed-files.json', 32*1024*1024)
    need(receipt.get('venv_inventory_sha256') == sha(inventory_raw), 'live inventory binding differs')
    saved_inventory = document(inventory_raw)
    current = producer.installed_inventory(venv, root)
    need(canonical(saved_inventory) == canonical(current), 'live venv bytes or modes changed')
    scope = producer.verify_installed_members(venv, current, closure, assets, tools['python'], 'som-react-api-qa')
    need(canonical(scope) == canonical(receipt.get('installed_scope')), 'live source-derived installed scope differs')
    for filename, original in [('requirements.in', raw_sources[REQUIREMENTS]),
                               ('pyproject.toml', read(closure_root/'project/pyproject.toml', 32*1024*1024)),
                               ('uv.lock', read(closure_root/'project/uv.lock', 32*1024*1024))]:
        need(read(named/filename, 32*1024*1024) == original, 'live project source changed')
    sources.verify()
    # This state means the live bytes and recorded command data were checked.
    # The independent auditor must separately join the GitHub workflow step
    # and its observed completion; the outer wait is not observed here.
    return {'status':'verified-live-inputs', 'python':str(venv/'bin/python'),
            'pg_bin':str(runtime/'install/postgresql/bin'), 'live_receipt_sha256':sha(raw),
            'runtime_receipt_sha256':sha(runtime_raw), 'closure_receipt_sha256':sha(closure_raw),
            'venv_inventory_sha256':sha(inventory_raw), 'requirements_count':39, 'wheel_count':40}
