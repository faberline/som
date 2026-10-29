"""Independently check retained Linux API run inputs; no product result."""
from pathlib import Path
import re

import linux_install_map as mapping

AUTHORITY = 'som-rs/contracts/react-output-v1/linux-api-qa-authority.json'
INSTALLER = 'som-rs/scripts/hosted_readiness/product_python/uv099-installer-authority.json'
REQUIREMENTS = 'som-rs/scripts/react_output_acceptance/hosted_python_requirements.txt'
REQ_SHA = '217f0d88231ad9d10cca3232a8707cdbb3355bd9a41e19fbddbab0d299c6db35'
AUDIT_SHA = '3fb0075a6795d4f4efcb0845f8ee05dcf16947f7e946954a02a3835677eb8389'
SOURCE_ZIP = 'd568ebe48167bec64b4d067f1e2e246b625e16852fc6201bef3689c13c7850bf'
AUDIT_ZIP = '6aa5750a1b98f0aff12e3145963b73f44cc1f087339905a8ebab8f0e659bbb00'
REQUIRED_SOURCES = ['som-rs/behavior/hosts.py', 'som-rs/behavior/typescript/host.py', 'som-rs/contracts/compiler-v1/compiler-prompt.schema.json', 'som-rs/contracts/rust-output-v1/acceptance-plan.json', 'som-rs/contracts/rust-output-v1/bundle.schema.json', 'som-rs/contracts/rust-output-v1/case-inventory.json', 'som-rs/contracts/rust-output-v1/product-layout.json', 'som-rs/contracts/rust-output-v1/toolchain.json', 'som-rs/contracts/typescript-output-v1/acceptance-plan.json', 'som-rs/contracts/typescript-output-v1/behavior-normalization.json', 'som-rs/contracts/typescript-output-v1/product-layout.json', 'som-rs/contracts/typescript-output-v1/toolchain.json', 'som-rs/scripts/compiler_acceptance/paths.py', 'som-rs/scripts/compiler_acceptance/qa_harness.py', 'som-rs/scripts/hosted_readiness/product_python/produce_wheels.py', 'som-rs/scripts/hosted_readiness/product_python/uv099-installer-authority.json', 'som-rs/scripts/hosted_readiness/product_python/wheel-policy.json', 'som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py', 'som-rs/scripts/hosted_readiness/product_runtime/reuse_runtime.py', 'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-byte-audit.json', 'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-qualification.json', 'som-rs/scripts/hosted_readiness/product_runtime/source-pins.json', 'som-rs/scripts/react_output_acceptance/check_products.py', 'som-rs/scripts/react_output_acceptance/hosted_python_requirements.txt', 'som-rs/scripts/react_output_acceptance/linux_api_context.py', 'som-rs/scripts/react_output_acceptance/linux_api_evidence.py', 'som-rs/scripts/react_output_acceptance/linux_api_host.py', 'som-rs/scripts/react_output_acceptance/linux_api_live_inputs.py', 'som-rs/scripts/react_output_acceptance/provision_live.py', 'som-rs/scripts/react_output_acceptance/record_api.py', 'som-rs/scripts/react_output_acceptance/record_api_fetch.mjs', 'som-rs/scripts/react_output_acceptance/retained_evidence.py', 'som-rs/scripts/react_output_acceptance/run_acceptance.py', 'som-rs/scripts/rust_output_acceptance/python_replay.py', 'som-rs/som-targets/qa/check_rust_products.py', 'som-rs/som-targets/qa/check_targets.py', 'som-rs/tests/react-output-v1/api_contract.mjs', 'som-rs/tests/react-output-v1/check_contract.py', 'som-rs/tests/react-output-v1/common.py', 'som-rs/tests/react-output-v1/linux_qa_profile.py', 'som-rs/tests/react-output-v1/react_profile.py', 'som-rs/tests/react-output-v1/run_api_contract.py', 'som-rs/tests/react-output-v1/tree_sha.py', 'som-rs/tests/rust-output-v1/build_negative_cases.py', 'som-rs/tests/rust-output-v1/rust_profile.py', 'som-rs/tests/rust-output-v1/rust_profile_phase2.py', 'som-rs/tests/typescript-output-v1/tree_sha.py']


class LinuxInputError(ValueError):
    def __init__(self, message, status='failed'):
        super().__init__(message)
        self.status = status


def need(ok, message, status='failed'):
    if not ok:
        raise LinuxInputError(message, status)


def strict(raw):
    try:
        return mapping.strict_json(raw)
    except mapping.InputAuditError as exc:
        raise LinuxInputError(str(exc)) from exc


def object_(value, label):
    need(isinstance(value, dict), label + ' missing', 'unrun')
    return value


def saved(reader, root, record, expected_path):
    record = object_(record, 'retained file record')
    need(set(record) == {'path', 'sha256', 'size'} and record['path'] == expected_path
         and type(record['size']) is int and record['size'] >= 0
         and isinstance(record['sha256'], str) and re.fullmatch('[0-9a-f]{64}', record['sha256']),
         'retained file record differs')
    path = reader.path(root / expected_path)
    need(path.is_file(), 'retained file missing', 'unrun')
    need(path.stat().st_size == record['size'] and reader.hash(path) == record['sha256'],
         'retained file bytes differ')
    return path


def metadata(run_doc, art_doc, run, head, artifact, digest):
    run_doc = object_(run_doc, 'GitHub source run')
    art_doc = object_(art_doc, 'GitHub source artifact')
    need(run_doc.get('id') == run and run_doc.get('head_sha') == head and run_doc.get('status') == 'completed'
         and run_doc.get('conclusion') == 'success' and run_doc.get('repository', {}).get('id') == 1390374878,
         'GitHub source run identity differs')
    ref = object_(art_doc.get('workflow_run'), 'artifact source run')
    need(art_doc.get('id') == artifact and art_doc.get('expired') is False and art_doc.get('digest') == 'sha256:' + digest
         and ref.get('id') == run and ref.get('head_sha') == head and ref.get('repository_id') == 1390374878,
         'GitHub artifact identity differs')


def audit(reader, run, latest, qa, tar_path):
    """Return case identity only after raw inputs pass source reconstruction.

    The outer aggregate must separately bind the GitHub workflow, head, step,
    and result. This function cannot derive that transport from a receipt.
    """
    need(qa is not None, 'Node QA input audit missing', 'unrun')
    need(tar_path is not None, 'qualified closure TAR not supplied', 'unrun')
    evidence = run / 'api-qa-evidence'
    retained = object_(strict(reader.bytes(evidence / 'retained.json')), 'API retained evidence')
    need(retained.get('version') == 'som.react-linux-api-retained-evidence.v1'
         and retained.get('status') == 'passed' and retained.get('problems') == [],
         'API retained evidence incomplete', 'unrun')
    files = object_(latest.get('files'), 'sealed contract map')
    authority_raw = reader.bytes(AUTHORITY)
    authority = strict(authority_raw)
    need(files.get(AUTHORITY) == mapping.sha(authority_raw)
         and set(authority) == {'version', 'api_profile_sha256', 'node_profile_sha256', 'source_sha256'}
         and authority.get('version') == 'som.react-linux-api-authority.v1', 'API authority is not sealed')
    shas = object_(authority.get('source_sha256'), 'API source authority')
    need(set(shas) == set(REQUIRED_SOURCES), 'API source authority closure differs')
    for rel, expected_sha in {
        'som-rs/scripts/hosted_readiness/product_python/produce_wheels.py': 'c9151e7b09b0d7fcadcc203f72b83bf1135f2da260becf2034f4228b26299a39',
        INSTALLER: mapping.INSTALLER_SHA,
        REQUIREMENTS: REQ_SHA,
    }.items():
        need(shas.get(rel) == expected_sha, 'fixed Python source authority differs')
    sources = object_(retained.get('sources'), 'retained API sources')
    need(set(sources) == set(shas), 'retained API source inventory differs')
    for rel, expected_sha in shas.items():
        need(isinstance(expected_sha, str) and re.fullmatch('[0-9a-f]{64}', expected_sha)
             and reader.hash(rel) == expected_sha, 'API source differs from sealed authority')
        if rel.startswith(('som-rs/contracts/react-output-v1/', 'som-rs/tests/react-output-v1/')):
            need(files.get(rel) == expected_sha, 'API contract/test source absent from sealed map')
        records = object_(sources[rel], 'retained API source phases')
        need(set(records) == {'before', 'after'}, 'API source phases missing', 'unrun')
        for phase in ('before', 'after'):
            path = saved(reader, evidence, records[phase], 'source-' + phase + '/' + rel)
            need(reader.hash(path) == expected_sha, 'retained API source differs')
    profiles = []
    for key in ('profile', 'authority'):
        phases = object_(retained.get(key), 'retained ' + key)
        need(set(phases) == {'before', 'after'}, 'retained phases missing', 'unrun')
        values = []
        for phase in ('before', 'after'):
            path = saved(reader, evidence, phases[phase], key + '-' + phase + '.json')
            values.append(reader.bytes(path))
        need(values[0] == values[1], 'retained ' + key + ' changed')
        if key == 'authority':
            need(values[0] == authority_raw, 'retained API authority differs')
        else:
            profiles = values
    profile_raw = profiles[0]
    need(mapping.sha(profile_raw) == authority.get('api_profile_sha256'), 'API profile SHA differs')
    profile = object_(strict(profile_raw), 'API profile')
    temp = '/home/runner/work/_temp'
    root = temp + '/som-react-python-closure'
    runtime = temp + '/som-product-runtime-qualification'
    venv = root + '/environments/react-api-qa/venv'
    api_path = temp + '/som-react-api-context.json'
    expect = {'version': 'som.react-linux-api-profile.v1', 'host': 'ubuntu-22.04-x86_64',
              'node_profile': qa['profile_path'], 'runtime_root': runtime, 'closure_root': root, 'venv': venv,
              'live_receipt': root + '/environments/react-api-qa/live-provision-receipt.json'}
    need(mapping.equal(profile, expect) and authority.get('node_profile_sha256') == qa['profile_sha256'],
         'API and Node profile differ')
    identity = object_(strict(reader.bytes(run / 'identity.json')), 'run identity')
    run_doc = object_(strict(reader.bytes(run / 'run.json')), 'run record')
    need(run_doc.get('api_qa_profile') == {'path': api_path, 'sha256': mapping.sha(profile_raw)},
         'run API profile differs')
    binding = object_(identity.get('api_qa_profile'), 'run API identity')
    need(binding.get('sha256') == mapping.sha(profile_raw) and binding.get('recheck_passed') is True
         and binding.get('before') == 'api-qa-evidence/full-before.json'
         and binding.get('after') == 'api-qa-evidence/full-after.json'
         and binding.get('retained') == 'api-qa-evidence/retained.json' and binding.get('retained_passed') is True
         and reader.hash(evidence / 'retained.json') == binding.get('retained_sha256'), 'run API identity differs')
    before_raw = reader.bytes(evidence / 'full-before.json')
    after_raw = reader.bytes(evidence / 'full-after.json')
    need(mapping.sha(before_raw) == binding.get('before_sha256') and mapping.sha(after_raw) == binding.get('after_sha256'),
         'API full record hash differs')
    before, after = strict(before_raw), strict(after_raw)
    need(mapping.equal(before, after) and before.get('version') == 'som.react-linux-api-full-validation.v1',
         'API full records differ')
    need(before.get('context_path') == api_path and before.get('context_sha256') == mapping.sha(profile_raw)
         and before.get('authority_sha256') == mapping.sha(authority_raw)
         and before.get('node_profile_path') == qa['profile_path'] and before.get('node_profile_sha256') == qa['profile_sha256']
         and before.get('node') == qa['node'] and before.get('npm_cli') == qa['npm_cli'] and before.get('cache') == qa['cache']
         and before.get('python') == venv + '/bin/python' and before.get('pg_bin') == runtime + '/install/postgresql/bin',
         'API full input identity differs')
    for key in ('run_id', 'run_attempt'):
        need(isinstance(before.get(key), str) and before[key].isdigit(), 'runner identity missing', 'unrun')
    snapshots = {}
    for label in ('runtime', 'venv'):
        phases = object_(object_(retained.get('inventories'), 'tree inventories').get(label), 'tree phases')
        need(set(phases) == {'before', 'after'}, 'tree phases missing', 'unrun')
        docs = []
        for phase in ('before', 'after'):
            entry = object_(phases[phase], 'tree phase')
            path = evidence / (label + '-' + phase + '.json')
            need(entry == {'path': label + '-' + phase + '.json', 'sha256': reader.hash(path)},
                 'tree snapshot binding differs')
            docs.append(strict(reader.bytes(path)))
        need(mapping.equal(docs[0], docs[1]), 'tree changed between observations')
        snapshots[label] = docs[0]
    inputs = object_(retained.get('inputs'), 'retained API inputs')

    def get(name):
        path = saved(reader, evidence, inputs.get(name), 'inputs/' + name)
        return None if path.suffix == '.py' else reader.bytes(path)

    runtime_raw = get('runtime-receipt.json')
    inventory_raw = get('runtime-installed-files.json')
    closure_raw = get('closure-receipt.json')
    live_raw = get('live-provision-receipt.json')
    installed_raw = get('live-installed-files.json')
    metadata_raw = get('live-installed-metadata.json')
    for name in ('runtime-artifact-origin.json', 'runtime-qualification-receipt.json', 'runtime-source-pins.json',
                 'runtime-build-intermediates.json', 'selected.lock', 'offline.lock', 'pyproject.toml', 'uv.lock', 'requirements.in'):
        get(name)
    probe_path = saved(reader, evidence, inputs.get('probe-installed-metadata.py'), 'inputs/probe-installed-metadata.py')
    need(reader.hash(probe_path) == strict(live_raw).get('probe_sha256')
         == '8692a2ace0edb8fe6a4f8f003ee7a4896d2c55d801a5500133ef50cb03c2169c',
         'metadata probe source differs')
    need(mapping.sha(closure_raw) == mapping.CLOSURE_SHA and mapping.sha(runtime_raw) == before.get('runtime_receipt_sha256')
         and mapping.sha(live_raw) == before.get('live_receipt_sha256')
         and mapping.sha(installed_raw) == before.get('venv_inventory_sha256')
         and mapping.sha(closure_raw) == before.get('closure_receipt_sha256'), 'source receipt binding differs')
    need(mapping.sha(get('requirements.in')) == REQ_SHA == shas.get(REQUIREMENTS), 'requirements source differs')
    closure = object_(strict(closure_raw), 'closure')
    for name, key in [('selected.lock', 'requirements_selected_sha256'), ('offline.lock', 'requirements_offline_sha256'),
                      ('pyproject.toml', 'pyproject_sha256'), ('uv.lock', 'uv_lock_sha256')]:
        need(mapping.sha(get(name)) == closure.get(key), 'locked project source differs')
    runtime_doc = object_(strict(runtime_raw), 'runtime receipt')
    runner = object_(runtime_doc.get('runner'), 'runtime runner')
    need(runner.get('GITHUB_RUN_ID') == before['run_id'] and runner.get('GITHUB_RUN_ATTEMPT') == before['run_attempt'],
         'runtime was not established in this run')
    live = object_(strict(live_raw), 'fresh venv receipt')
    expected_live = {'version': 'som.react-live-venv.v1', 'state': 'fresh-offline-venv-ready-product-tests-unrun',
        'product_acceptance_passed': False, 'source_run_id': 40821432963, 'source_artifact_id': 12874482658,
        'source_zip_sha256': SOURCE_ZIP, 'source_tar_sha256': mapping.TAR_SHA,
        'independent_audit_run_id': 40823392027, 'independent_audit_artifact_id': 12875218806,
        'independent_audit_sha256': AUDIT_SHA, 'closure_receipt_sha256': mapping.CLOSURE_SHA,
        'runtime_receipt_sha256': mapping.sha(runtime_raw), 'requirements_sha256': REQ_SHA,
        'requirements_count': 39, 'wheel_count': 40, 'venv': venv,
        'venv_inventory_sha256': mapping.sha(installed_raw), 'installed_metadata_sha256': mapping.sha(metadata_raw)}
    need(all(type(live.get(k)) is type(v) and live.get(k) == v for k, v in expected_live.items()),
         'fresh venv receipt differs')
    head = object_(identity.get('git_head'), 'run head')
    need(isinstance(head.get('before'), str) and head.get('before') == head.get('after') == live.get('source_revision'),
         'fresh venv source revision differs from candidate')
    metadata(strict(get('provenance/source-run.json')), strict(get('provenance/source-artifact.json')),
             40821432963, '071c55b0f02e2325a6f0993636d5330005b6a4fe', 12874482658, SOURCE_ZIP)
    metadata(strict(get('provenance/audit-run.json')), strict(get('provenance/audit-artifact.json')),
             40823392027, '980fb40bde910d467bfcf125bbaade5b6009d033', 12875218806, AUDIT_ZIP)
    need(mapping.sha(get('provenance/react-python-independent-audit.json')) == AUDIT_SHA,
         'independent wheel audit bytes differ')
    commands = live.get('commands')
    need(isinstance(commands, list) and len(commands) == 2, 'fresh installation commands missing', 'unrun')
    labels = ('fresh-offline-frozen-sync', 'fresh-offline-no-site-metadata')
    python = runtime + '/install/python/bin/python3.12'
    uv = runtime + '/install/uv/bin/uv'
    named = root + '/environments/react-api-qa'
    closure_root = root + '/closures/' + mapping.KEY
    expected_commands = ([uv, 'sync', '--frozen', '--python', python, '--no-managed-python', '--no-config', '--no-build',
                          '--offline', '--no-index', '--find-links', closure_root + '/wheels', '--link-mode', 'copy'],
                         [python, '-I', '-S', '-B', root + '/probe-installed-metadata.py', venv + '/lib/python3.12/site-packages'])
    environment = {'PATH': runtime + '/install/python/bin:' + runtime + '/install/uv/bin:/usr/bin:/bin',
        'HOME': root + '/home', 'TMPDIR': root + '/tmp', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'UV_PYTHON_DOWNLOADS': 'never',
        'UV_NO_CONFIG': '1', 'UV_NO_BUILD': '1', 'UV_KEYRING_PROVIDER': 'disabled',
        'UV_DEFAULT_INDEX': 'https://pypi.org/simple', 'UV_CACHE_DIR': closure_root + '/cache',
        'UV_NO_PROGRESS': '1', 'UV_NO_INSTALLER_METADATA': '1', 'UV_COMPILE_BYTECODE': '0',
        'UV_OFFLINE': '1', 'UV_PROJECT_ENVIRONMENT': venv}
    metadata_env = dict(environment)
    del metadata_env['UV_PROJECT_ENVIRONMENT']
    for row, label, argv, env in zip(commands, labels, expected_commands, (environment, metadata_env)):
        need(isinstance(row, dict) and row.get('argv') == argv and row.get('cwd') == named and row.get('status') == 'completed'
             and mapping.equal(row.get('environment'), env)
             and type(row.get('pid')) is int and row['pid'] > 1 and type(row.get('exit_code')) is int and row['exit_code'] == 0
             and type(row.get('wait_result')) is int and row['wait_result'] == 0 and row.get('group_stopped') is True,
             'fresh installation command or wait differs')
        for stream in ('stdout', 'stderr'):
            raw = get('logs/' + label + '.' + stream)
            need(row.get(stream) == root + '/logs/' + label + '.' + stream and row.get(stream + '_sha256') == mapping.sha(raw)
                 and type(row.get(stream + '_bytes')) is int and row[stream + '_bytes'] == len(raw), 'fresh command log differs')
    need(mapping.equal(strict(get('logs/fresh-offline-no-site-metadata.stdout')), strict(metadata_raw)),
         'metadata stdout differs from retained metadata')
    wheels = closure.get('wheels')
    need(isinstance(wheels, list) and len(wheels) == 40, 'wheel inventory missing', 'unrun')
    inventories = {}
    for row in wheels:
        name = row.get('name')
        need(isinstance(name, str) and name not in inventories, 'wheel name duplicated')
        inventories[name] = get('wheel-audits/' + name + '/inventory.json')
    try:
        runtime_count = mapping.runtime_check(snapshots['runtime'], inventory_raw)
        expected, dirs = mapping.derive(tar_path, closure_raw, inventories, reader.bytes(INSTALLER), venv, python)
        venv_count = mapping.venv_check(snapshots['venv'], installed_raw, expected, dirs)
    except mapping.InputAuditError as exc:
        raise LinuxInputError(str(exc)) from exc
    executables = object_(before.get('executables'), 'selected executables')
    runtime_entries = snapshots['runtime']['entries']
    for name in ('initdb', 'postgres', 'pg_ctl'):
        path = 'postgresql/bin/' + name
        row = object_(executables.get(name), 'PostgreSQL executable')
        source = runtime_entries[path]
        need(row.get('path') == runtime + '/install/' + path and row.get('resolved_path') == row['path']
             and row.get('sha256') == source['sha256'] and row.get('mode') == source['mode'] and row['mode'] & 0o111,
             'PostgreSQL executable identity differs')
    node = object_(executables.get('node'), 'Node executable')
    need(node.get('path') == qa['node'] and node.get('sha256') == qa['profile']['node']['sha256'],
         'Node executable differs from separately audited profile')
    py = object_(executables.get('python'), 'Python executable')
    actual_python = runtime_entries['python/bin/python3.12']
    need(py.get('path') == venv + '/bin/python' and py.get('resolved_path') == python
         and py.get('sha256') == actual_python['sha256'] and py.get('mode') == actual_python['mode'] and py['mode'] & 0o111,
         'Python executable identity differs')
    os_tools = object_(before.get('os_tools'), 'OS tool observations')
    return {'api_profile_sha256': mapping.sha(profile_raw), 'node_profile_sha256': qa['profile_sha256'],
            'node': qa['node'], 'python': venv + '/bin/python', 'pg_bin': runtime + '/install/postgresql/bin',
            'lsof': '/usr/bin/lsof', 'ps': '/bin/ps', 'os_tools': os_tools,
            'api_profile_path': api_path, 'node_profile_path': qa['profile_path'],
            'full_before_path': str(evidence / 'full-before.json'),
            'runtime_paths': runtime_count, 'venv_paths': venv_count}
