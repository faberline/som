"""Collect the complete TypeScript regression scope on the shared current build.

This additive owner keeps the original thirteen commands and their judges.
The historical TypeScript phase-one main and its52658 baseline stay unchanged.
The current regression uses the explicitly frozen TypeScript002/Rust006 role.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import traceback
from types import ModuleType

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_owner.py'
COLLECTOR = 'som-rs/scripts/typescript_output_acceptance/run_acceptance.py'
CURRENT_TS = 'som-rs/scripts/react_output_acceptance/check_typescript_baseline.py'
CURRENT_REBASE = BASE + 'typescript_regression_rebase.py'
DIGESTS = BASE + 'typescript_native_digests.py'
CHECKER = BASE + 'typescript_regression_checker.py'
NATIVE_HOST = BASE + 'typescript_native_host.py'
RAW_CAPTURE = BASE + 'typescript_regression_raw_capture.py'
INPUT_ADAPTER = BASE + 'typescript_regression_inputs.py'
HTTP_REPLAY = BASE + 'typescript_regression_http_replay.py'
MAIN_REPLAY = BASE + 'typescript_regression_main_replay.py'
PRODUCT_PROOF = BASE + 'typescript_regression_product_proof.py'
SCHEMA_CAPTURE = BASE + 'typescript_regression_schema_capture.py'
NESTED_RUST = BASE + 'typescript_regression_nested_rust.py'
CLI_RUST = BASE + 'typescript_regression_cli_rust_proof.py'
PRODUCT_COMMANDS = BASE + 'typescript_regression_product_commands.py'
DOMAIN = BASE + 'typescript_regression_domain.py'
LEGACY_AUDITOR = 'som-rs/scripts/typescript_output_acceptance/audit/audit065.py'
SHARED = BASE + 'run_current_candidate_gate.py'
INPUT_PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'
GATE = 'typescript-regression'
VERSION = 'som.typescript-regression-owner-capture.v1'
VERIFIED = 'som.typescript-regression-owner-verified.v1'
LATEST_BINARY = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
LATEST_SOURCE = '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af'
F59 = 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'
PLANS = {'typescript': 'som-rs/contracts/typescript-output-v1/acceptance-plan.json',
         'rust': 'som-rs/contracts/rust-output-v1/acceptance-plan.json',
         'react': 'som-rs/contracts/react-output-v1/acceptance-plan.json'}
ORIGINAL_STEPS = ('target', 'rt-diagnostics', 'cli', 'bundles', 'ts-products',
                  'python-baseline', 'python-replay', 'validate-cases',
                  'ts-python-controls', 'rust-python-controls', 'rust-baseline',
                  'rust-products', 'rust-unchanged')
INPUT_ROLES = {'public_run', 'legacy_run', 'rust_run', 'npm_cache', 'node_tools'}
PROOF_ROLES = {'assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root'}


def require(value, message):
    if not value:
        raise ValueError(message)


def failure_record(error):
    return {'error': type(error).__name__, 'reason': str(error),
            'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__))}


def remember_failure(state, role, error):
    record = {'role': role, **failure_record(error)}
    if state['primary'] is None:
        state.update(primary=error, traceback=error.__traceback__, failure=record)
        error.__som_typescript_failure_record__ = record
    else:
        state['secondary'].append(record)
    state['primary'].__som_typescript_secondary_failures__ = state['secondary']
    return record


def raise_primary(state):
    if state['primary'] is None:
        return
    try:
        sys.stderr.write(json.dumps({'typescript_primary_failure': state['failure'],
            'secondary_failures': state['secondary']}, ensure_ascii=False, sort_keys=True) + '\n')
    except BaseException as error:
        remember_failure(state, 'primary_traceback_stderr', error)
    raise state['primary'].with_traceback(state['traceback'])


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate owner JSON member')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite owner JSON')))


def physical(value, *, exists=True):
    path = Path(value).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
            'physical owner path required')
    if exists:
        require(path.exists(), 'owner input missing')
    return path


def read(path, *, limit=320 * 1024**2, single_link=True):
    path = physical(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit
                and (not single_link or info.st_nlink == 1), 'bounded regular owner file required')
        raw = stream.read(limit + 1)
    require(len(raw) == info.st_size, 'owner input changed size')
    return raw


def identity(path, *, single_link=True):
    raw = read(path, single_link=single_link)
    info = Path(path).stat()
    return {'sha256': sha(raw), 'size': len(raw), 'mode': stat.S_IMODE(info.st_mode)}


def members(root):
    root = physical(root)
    require(root.is_dir() and root.stat().st_mode & 0o500 == 0o500,
            'readable searchable owner root required')
    def failed(error):
        raise error
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=failed):
        require(Path(current).stat().st_mode & 0o500 == 0o500,
                'unreadable owner directory refused')
        dirs.sort(); files.sort()
        for name in sorted([*dirs, *files]):
            path = Path(current) / name; info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                require(info.st_mode & 0o500 == 0o500, 'unreadable owner directory refused')
            yield path, info


def tree(root):
    root = physical(root)
    require(root.is_dir(), 'owner tree directory required')
    values = {}
    for path, info in members(root):
        require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode),
                'owner tree special files refused')
        if stat.S_ISREG(info.st_mode):
            values[path.relative_to(root).as_posix()] = identity(path)
        elif stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            resolved = path.resolve(strict=True)
            require(not os.path.isabs(target) and root in resolved.parents and resolved.is_file(),
                    'only exact internal input file aliases are admitted')
            values[path.relative_to(root).as_posix()] = {'type': 'internal-alias', 'target': target,
                'mode': stat.S_IMODE(info.st_mode), 'target_sha256': sha(read(resolved, single_link=False))}
    return values


def directory_modes(root):
    root = physical(root)
    return {'.': stat.S_IMODE(root.stat().st_mode), **{
        path.relative_to(root).as_posix(): stat.S_IMODE(info.st_mode)
        for path, info in members(root) if stat.S_ISDIR(info.st_mode)}}


def input_directory_universe(root, files, specification, role):
    wanted = {'.'}
    empty = specification.get('empty_directories', [])
    require(type(empty) is list and all(type(value) is str and value and not value.startswith('/')
            and all(part not in {'', '.', '..'} for part in value.split('/')) for value in empty),
            'explicit safe source-profile empty directories required')
    names = [*files, *empty, *(['_logs'] if role == 'npm_cache' else [])]
    for value in names:
        path = Path(value)
        if value in empty or (role == 'npm_cache' and value == '_logs'):
            wanted.add(value)
        wanted.update(parent.as_posix() for parent in path.parents)
    actual = directory_modes(root)
    require(set(actual) == wanted, 'complete source-profile input directory universe differs: ' + role)
    if role == 'npm_cache':
        logs = Path(root) / '_logs'
        require(logs.is_dir() and not any(logs.iterdir()) and all(mode == 0o555 for mode in actual.values()),
                'frozen TypeScript cache directories must remain read-only with empty _logs')
    return actual


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def raw_module(reader, repo, name, path):
    require(name not in sys.modules, 'preloaded TypeScript owner helper refused')
    raw = reader(path)
    module = ModuleType(name)
    module.__file__, module.__package__, module.__cached__ = str(Path(repo) / path), '', None
    module.__som_raw_sha256__ = sha(raw)
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def plan_scope(reader):
    data = {name: parse(reader(path)) for name, path in PLANS.items()}
    ts, rust = data['typescript'], data['rust']
    for section in (ts['amendment_002'], rust['amendment006']):
        require(LATEST_BINARY in section['baseline'] and LATEST_SOURCE in section['baseline'],
                'current TypeScript002/Rust006 baseline role must be the recorded #65 build')
    require(ts['amendment_002']['status'] == 'sealed'
            and len(ts['amendment_002']['checks']) == 6 and len(rust['amendment006']['checks']) == 10,
            'complete frozen current amendment scope required')
    require(len(ts['assemble_refusals']['recipes']) == 12
            and len(ts['product_gates']['each_acceptance_case']) == 9,
            'all TypeScript refusal and product gates required')
    return {'plan_sha256': {name: sha(reader(path)) for name, path in PLANS.items()},
            'original_steps': list(ORIGINAL_STEPS),
            'current_typescript_checks': ts['amendment_002']['checks'],
            'current_rust_checks': rust['amendment006']['checks'],
            'typescript_refusal_ids': [row['id'] for row in ts['assemble_refusals']['recipes']],
            'product_gates': ts['product_gates']['each_acceptance_case'],
            'baseline_binary_sha256': LATEST_BINARY, 'baseline_source_revision': LATEST_SOURCE}


def baseline_role(reader, path):
    profile = parse(reader(path))
    if profile.get('version') == 'som.linux-qa-recorded-before.v1':
        require(profile.get('role') == 'recorded-same-request-before'
                and profile.get('source_compiler') == {'binary_sha256': LATEST_BINARY,
                                                       'source_revision': LATEST_SOURCE},
                'recorded before source compiler differs from frozen live2bce authority')
        return 'recorded-before-supplemental'
    require(profile.get('version') == 'som.linux-qa-immutable-baseline.v1'
            and profile.get('role') == 'historical-immutable-baseline'
            and profile.get('source_revision') == LATEST_SOURCE
            and profile.get('binary', {}).get('sha256') == LATEST_BINARY,
            'exact frozen live2bce baseline profile required; no other historical/current binary may substitute')
    return 'live-before-profile'


def admitted_context(ctx):
    require(ctx.qa_mode == 'native-qa-v1' and re.fullmatch('[0-9a-f]{40}', ctx.helper_revision or '')
            and ctx.helper_revision == ctx.candidate_revision != F59, 'one new native source identity required')
    authority = ctx.qa_source_authority
    require(authority.get('source_authority_active') is True
            and authority.get('helper_revision') == authority.get('candidate_revision') == ctx.helper_revision,
            'checked native source authority required')
    require(all(path in authority['admitted_repository_python_sources'] for path in
                (SELF, COLLECTOR, CURRENT_TS, CURRENT_REBASE, DIGESTS, CHECKER, NATIVE_HOST, DOMAIN,
                 LEGACY_AUDITOR, RAW_CAPTURE, INPUT_ADAPTER, HTTP_REPLAY, MAIN_REPLAY,
                 PRODUCT_PROOF, SCHEMA_CAPTURE, NESTED_RUST, CLI_RUST, PRODUCT_COMMANDS)),
            'complete TypeScript owner source admission required')
    require(callable(getattr(ctx, 'python_argv', None)), 'actual isolated qualified Python startup API required')
    ctx.sources.read(SELF)
    ctx.verify()
    module = sys.modules.get('full_product_context')
    require(module is not None and module.context() is ctx and callable(getattr(module, 'tool_command', None)),
            'actual active Context/tool_command required')
    return module


def require_full_input_transport_consumer():
    # Matching payloads/catalogs/API metadata do not establish ZIP, cipher,
    # AEAD, manifest, aliases or ordered-chunk consumer verification. No such
    # source-admitted API is implemented here, so no product may start yet.
    raise NotImplementedError('required source-admitted same-run full ZIP/index/cipher/manifest/AEAD/'
                              'ordered-chunk/five-role/RS22 consumer verifier is not integrated')


def admit_inputs(ctx):
    """The data producer must restore one current-run source-profile-bound packet."""
    data = ctx.data.get('typescript_regression_inputs')
    require(type(data) is dict and set(data) == {'roots', 'siblings', 'receipt', 'artifact'},
            'explicit full TypeScript regression input binding required')
    require(type(data['roots']) is dict and set(data['roots']) == INPUT_ROLES,
            'all original Python/Rust/npm/Node input roles required')
    profile_raw = ctx.sources.read(INPUT_PROFILE)
    profile = parse(profile_raw)
    require(profile.get('version') == 'som.typescript-regression-input-profile.v1'
            and profile.get('scope') == 'typescript002-rust006-native-regression'
            and profile.get('baseline_binary_sha256') == LATEST_BINARY
            and profile.get('baseline_source_revision') == LATEST_SOURCE
            and profile.get('pending') == []
            and set(profile['roles']) == INPUT_ROLES,
            'exact TypeScript-specific source profile required; no React cache or f59 role substitution')
    require(type(profile['baseline_profile']) is str and profile['baseline_profile'].startswith(BASE + 'inputs/'),
            'explicit current recorded-before source profile required')
    ctx.sources.read(profile['baseline_profile'])
    before_role = baseline_role(ctx.sources.read, profile['baseline_profile'])
    roots = {name: physical(value) for name, value in data['roots'].items()}
    require(type(data['siblings']) is dict and set(data['siblings']) == {'rs_prompts'}
            and set(profile['siblings']) == {'rs_prompts'}
            and len(profile['siblings']['rs_prompts']['files']) == 22,
            'the complete explicit twenty-two RS prompt sibling inputs are required')
    siblings = {name: physical(path) for name, path in data['siblings'].items()}
    all_roots = [*roots.values(), *siblings.values()]
    require(all(a != b and a not in b.parents and b not in a.parents
                for i, a in enumerate(all_roots) for b in all_roots[i + 1:]),
            'input roles must be disjoint')
    require(all(path != ctx.repo and ctx.repo not in path.parents and path not in ctx.repo.parents
                and path != ctx.work and ctx.work not in path.parents and path not in ctx.work.parents
                for path in all_roots), 'declared input roles overlap repository or product output')
    receipt_raw = read(data['receipt'])
    receipt = parse(receipt_raw)
    require(receipt.get('version') == 'som.typescript-regression-restored-inputs.v1'
            and receipt.get('helper_revision') == receipt.get('candidate_revision') == ctx.helper_revision
            and receipt.get('run_id') == os.environ['GITHUB_RUN_ID']
            and receipt.get('run_attempt') == os.environ['GITHUB_RUN_ATTEMPT']
            and receipt.get('profile_sha256') == sha(profile_raw)
            and receipt.get('roots') == {name: str(value) for name, value in roots.items()},
            'current-run restored input receipt differs')
    inventories = {name: tree(path) for name, path in roots.items()}
    require(inventories == receipt['files'] == {name: profile['roles'][name]['files'] for name in INPUT_ROLES},
            'complete retained source/tool/npm input universes differ from exact profile')
    dirs = {name: input_directory_universe(path, inventories[name], profile['roles'][name], name)
            for name, path in roots.items()}
    sibling_files = {name: tree(path) for name, path in siblings.items()}
    require(receipt.get('siblings') == {name: str(path) for name, path in siblings.items()}
            and sibling_files == receipt['sibling_files'] == {
                name: profile['siblings'][name]['files'] for name in siblings},
            'complete explicit RS prompt sibling bindings differ')
    sibling_dirs = {name: input_directory_universe(path, sibling_files[name], profile['siblings'][name], name)
                    for name, path in siblings.items()}
    artifact = data['artifact']
    require(type(artifact) is dict and set(artifact) == {'id', 'name', 'digest', 'receipt_sha256'}
            and type(artifact['id']) is int and artifact['id'] > 0
            and artifact['name'] == 'same-candidate-typescript-regression-inputs'
            and artifact['receipt_sha256'] == sha(receipt_raw), 'exact TypeScript input artifact binding required')
    producer = ctx.data.get('typescript_regression_input_producer')
    require(type(producer) is dict and set(producer) == {
        'version', 'job', 'run_id', 'run_attempt', 'head_sha', 'artifact_id', 'artifact_digest',
        'index_sha256', 'receipt_sha256', 'profile_sha256'}
        and producer['version'] == 'som.ts-input-producer-catalog-draft.v1'
        and producer['job'] == 'typescript-regression-inputs'
        and producer['run_id'] == os.environ['GITHUB_RUN_ID']
        and producer['run_attempt'] == os.environ['GITHUB_RUN_ATTEMPT']
        and producer['head_sha'] == ctx.helper_revision
        and producer['artifact_id'] == artifact['id'] and producer['artifact_digest'] == artifact['digest']
        and producer['profile_sha256'] == sha(profile_raw)
        and re.fullmatch('[0-9a-f]{64}', producer['index_sha256'])
        and re.fullmatch('[0-9a-f]{64}', producer['receipt_sha256']),
        'exact source-workflow producer job output/catalog required')
    require(receipt.get('producer_catalog_sha256') == sha(canonical(producer))
            and receipt.get('artifact_id') == artifact['id']
            and receipt.get('artifact_digest') == artifact['digest']
            and receipt.get('index_sha256') == producer['index_sha256'],
            'fresh consuming receipt does not bind the exact producer output')
    graph = ctx.module('typescript_input_origin_graph', BASE + 'same_candidate_graph.py')
    actual = graph.github_json(os.environ['GITHUB_REPOSITORY'], '/actions/artifacts/' + str(artifact['id']))
    require(actual['id'] == artifact['id'] and actual['name'] == artifact['name']
            and actual['digest'] == artifact['digest'] and actual['expired'] is False
            and str(actual['workflow_run']['id']) == os.environ['GITHUB_RUN_ID']
            and actual['workflow_run']['head_sha'] == ctx.helper_revision,
            'TypeScript input artifact is not this exact run/source output')
    require_full_input_transport_consumer()
    # The concrete future verifier must return independently rederived complete
    # transport proof before this source branch may expose runtime input roots.
    return roots, profile, {'profile_sha256': sha(profile_raw), 'receipt_sha256': sha(receipt_raw),
                            'artifact': artifact, 'producer': producer, 'files': inventories,
                            'sibling_files': sibling_files, 'directory_modes': dirs,
                            'sibling_directory_modes': sibling_dirs, 'before_comparison_role': before_role}


def collector_steps(module, ctx, candidate, run, roots, current, baseline):
    """Use the actual original constructor; change only explicit runtime paths."""
    names = ('VENV_PYTHON', 'PUBLIC_RUN', 'LEGACY_RUN', 'RUN_003', 'NPM_CACHE')
    saved = {name: getattr(module, name) for name in names}
    legacy = roots['legacy_run'] / 'run-005'
    values = {'VENV_PYTHON': Path(ctx.data['observer_python']), 'PUBLIC_RUN': roots['public_run'],
              'LEGACY_RUN': legacy, 'RUN_003': roots['rust_run'], 'NPM_CACHE': roots['npm_cache']}
    try:
        for key, value in values.items():
            setattr(module, key, value)
        result = module.steps(candidate, run, current, baseline, workers=1)
    finally:
        for key, value in saved.items():
            setattr(module, key, value)
    require(tuple(row['name'] for row in result) == ORIGINAL_STEPS, 'original complete thirteen-command scope changed')
    for row in result:
        row['argv'] = list(row['argv'])
        if row['name'] in {'target', 'bundles'}:
            row['argv'] += ['--legacy-run', str(legacy)]
        if row['name'] == 'python-baseline':
            row['argv'] += ['--public-run', str(roots['public_run']), '--legacy-run', str(legacy)]
        if row['name'] == 'rust-baseline':
            row['argv'] += ['--run', str(roots['rust_run'])]
    result.append({'name': 'typescript002-baseline', 'cwd': ctx.repo,
        'argv': [ctx.data['observer_python'], str(ctx.repo / CURRENT_TS), '--qa', current,
                 '--baseline', baseline, '--legacy-run', str(legacy),
                 '--out', str(run / 'typescript002-baseline')],
        'outputs': ['typescript002-baseline/report.json'],
        'judge': module.make_step_judge('typescript002-baseline/report.json')})
    result.append({'name': 'typescript002-refusal-rebase', 'cwd': ctx.repo,
        'argv': [ctx.data['observer_python'], str(ctx.repo / CURRENT_REBASE),
                 '--legacy-run', str(legacy), '--out', str(run / 'typescript002-refusal-rebase')],
        'outputs': ['typescript002-refusal-rebase/result.json'],
        'judge': module.make_step_judge('typescript002-refusal-rebase/result.json')})
    return result


def command_row(ctx, context_module, index, step, run, out):
    script = Path(step['argv'][1]).relative_to(ctx.repo).as_posix()
    require(script in ctx.qa_source_authority['admitted_repository_python_sources'],
            'original checker entry is outside exact current QA source admission')
    ctx.sources.read(script)
    tape = run / 'raw-checkers' / f'{index:02d}'
    argv = context_module.tool_command(ctx.repo / CHECKER,
        ['--tool', script, '--capture-root', str(tape), '--', *step['argv'][2:]])
    command = ctx.command(argv, step['cwd'], ctx.child_environment(),
        'typescript-regression-' + step['name'], timeout=18000, check=False)
    row = {'index': index, 'step': step['name'], 'script': script,
           'logical_argv': [str(v) for v in step['argv']], 'cwd': str(step['cwd']),
           'command': command, 'passed': False}
    directory = out / 'commands' / f'{index:02d}'; directory.mkdir(parents=True)
    for kind in ('stdout', 'stderr'):
        body = read(ctx.work / command[kind]); (directory / (kind + '.raw')).write_bytes(body)
        require(sha(body) == command[kind + '_sha256'], 'original command stream changed')
    record = ctx.work / Path(command['stdout']).with_suffix('.json')
    require(parse(read(record)) == command, 'original Context command record differs')
    (directory / 'command.json').write_bytes(read(record))
    post = ctx.work / 'checker-postchecks' / (str(command['pid']) + '.json')
    (directory / 'postcheck.json').write_bytes(read(post))
    judge_receipt = {'exit_code': command['exit_code']}
    row['passed'], row['judgment'] = step['judge'](judge_receipt, run)
    save(directory / 'result.json', row)
    return row


def copy_capture(source, destination, external_aliases=None):
    """Retain every regular byte; keep aliases as opaque data, never follow them."""
    source = physical(source); destination.mkdir(mode=0o700, parents=True)
    inventory = {'.': {'type': 'directory', 'mode': stat.S_IMODE(source.stat().st_mode)}}
    for path, info in members(source):
        relative = path.relative_to(source).as_posix()
        if stat.S_ISDIR(info.st_mode):
            (destination / relative).mkdir(mode=0o700, exist_ok=True)
            inventory[relative] = {'type': 'directory', 'mode': stat.S_IMODE(info.st_mode)}
        elif stat.S_ISLNK(info.st_mode):
            resolved = path.resolve(strict=True)
            internal = source in resolved.parents
            row = {'type': 'opaque-alias', 'mode': stat.S_IMODE(info.st_mode), 'target': os.readlink(path)}
            if resolved.is_file():
                row['target_sha256'] = sha(read(resolved, single_link=False))
                require(internal or (external_aliases or {}).get(str(resolved)) == row['target_sha256'],
                        'unqualified external collection alias target refused')
                row['target_role'] = 'internal-file' if internal else 'qualified-runtime-python'
            else:
                require(internal and resolved.is_dir(), 'only internal directory aliases may be retained')
                row['target_role'], row['target_tree_sha256'] = 'internal-directory', sha(canonical(tree(resolved)))
            inventory[relative] = row
        else:
            require(stat.S_ISREG(info.st_mode), 'special collection output refused')
            raw = read(path, single_link=False); target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw); target.chmod(stat.S_IMODE(info.st_mode))
            after = path.lstat()
            require(read(path, single_link=False) == raw
                    and (after.st_dev, after.st_ino, after.st_size, stat.S_IMODE(after.st_mode), after.st_nlink)
                    == (info.st_dev, info.st_ino, info.st_size, stat.S_IMODE(info.st_mode), info.st_nlink),
                    'collection source changed during capture')
            inventory[relative] = {'type': 'file', **identity(target), 'source_nlink': info.st_nlink}
    for relative, row in sorted(inventory.items(), key=lambda item: -len(Path(item[0]).parts)):
        if row['type'] == 'directory':
            (destination / relative).chmod(row['mode'])
    return inventory


def run_gate(ctx, candidate_dir, services, out, gate):
    require(gate == GATE, 'exact TypeScript gate required')
    context_module = admitted_context(ctx)
    out, candidate_dir = physical(out), physical(candidate_dir)
    require(out.name == 'owner' and out.is_dir() and not any(out.iterdir()), 'fresh shared owner output required')
    scope = plan_scope(ctx.sources.read)
    report = {'version': VERSION, 'gate': gate, 'helper_revision': ctx.helper_revision,
        'candidate_revision': ctx.candidate_revision, 'scope': scope, 'rows': [],
        'complete': False, 'all_required_passed': False, 'owned_waits_complete': False,
        'owned_cleanup_complete': False, 'raw_logs_complete': False, 'product_acceptance_passed': False}
    run = None
    state = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
    external_aliases = {}
    try:
        runtime_python = Path(ctx.runtime()['tools']['python']).resolve(strict=True)
        external_aliases = {str(runtime_python): sha(read(runtime_python, single_link=False))}
        report['qualified_external_aliases'] = external_aliases
        roots, profile, inputs = admit_inputs(ctx); report['inputs'] = inputs
        report['input_roots'] = {name: str(path) for name, path in roots.items()}
        (out / 'context.input.json').write_bytes(read(ctx.path))
        (out / 'input-profile.json').write_bytes(ctx.sources.read(INPUT_PROFILE))
        (out / 'input-origin.json').write_bytes(read(ctx.data['typescript_regression_inputs']['receipt']))
        for role, path in roots.items():
            report.setdefault('input_capture_inventory', {})[role] = copy_capture(path, out / 'inputs' / role)
        report['sibling_roots'] = dict(ctx.data['typescript_regression_inputs']['siblings'])
        for role, path in report['sibling_roots'].items():
            report.setdefault('sibling_capture_inventory', {})[role] = copy_capture(
                physical(path), out / 'inputs/siblings' / role)
        current = services.start_current()
        require(current['url'] == 'http://127.0.0.1:18978' and Path(current['candidate_dir']) == candidate_dir,
                'shared current build identity differs')
        before = services.start_baseline(profile['baseline_profile'])
        require(before['url'] == 'http://127.0.0.1:18979', 'explicit recorded-before oracle port required')
        report['services'] = {'current': current, 'baseline': before}
        run = physical(ctx.work / 'typescript-regression', exists=False)
        require(not run.exists() and ctx.work in run.parents, 'new product roots must belong to the checked Context')
        run.mkdir(mode=0o700)
        collector = ctx.module('typescript_regression_original_steps', COLLECTOR)
        steps = collector_steps(collector, ctx, candidate_dir, run, roots, current['url'], before['url'])
        report['original_run'] = str(run)
        for index, step in enumerate(steps, 1):
            try:
                report['rows'].append(command_row(ctx, context_module, index, step, run, out))
            except Exception as error:
                report['rows'].append({'index': index, 'step': step['name'], 'passed': False,
                                      'error': type(error).__name__, 'reason': str(error)})
            ctx.verify(); services.verify_all()
            require({role: tree(path) for role, path in roots.items()} == inputs['files']
                    and {role: tree(path) for role, path in report['sibling_roots'].items()} == inputs['sibling_files']
                    and {role: directory_modes(path) for role, path in roots.items()} == inputs['directory_modes']
                    and {role: directory_modes(path) for role, path in report['sibling_roots'].items()} == inputs['sibling_directory_modes'],
                    'complete TypeScript input roles changed during original checker execution')
        report['collector_complete'] = (len(report['rows']) == len(ORIGINAL_STEPS) + 2
                                        and all(row['passed'] for row in report['rows']))
        # The shared waits and cleanup are pending. The immutable capture stays false.
    except BaseException as error:
        report.setdefault('primary_failure', remember_failure(state, 'owner_collector', error))
    finally:
        if run is not None and run.is_dir():
            try:
                report['capture_inventory'] = copy_capture(run, out / 'capture', external_aliases)
            except BaseException as error:
                report['capture_failure'] = remember_failure(state, 'owner_product_capture', error)
        for name in ('environment-commands', 'checker-postchecks', 'product-environments'):
            directory = ctx.work / name
            if directory.is_dir():
                try:
                    report.setdefault('original_context_inventory', {})[name] = copy_capture(
                        directory, out / 'original-context' / name, external_aliases)
                except BaseException as error:
                    report.setdefault('context_capture_failures', []).append(
                        remember_failure(state, 'owner_context_capture_' + name, error))
        for label, action in (
                ('bound_source_sha256', lambda: dict(sorted(ctx.sources.pins.items()))),
                ('saved_files', lambda: tree(out)),
                ('saved_directory_modes', lambda: directory_modes(out))):
            try:
                report[label] = action()
            except BaseException as error:
                report.setdefault('evidence_failures', []).append(remember_failure(state, label, error))
        if state['primary'] is not None:
            report.setdefault('primary_failure', state['failure'])
        report['secondary_failures'] = state['secondary']
        try:
            save(out / 'result.json', report)
        except BaseException as error:
            report.setdefault('evidence_failures', []).append(remember_failure(state, 'owner_result_evidence', error))
    raise_primary(state)
    return report


def verify_result(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots,
                  verification_evidence_root=None, archive_view=None):
    """Keep shared and domain proofs separate. Never promote capture booleans."""
    require(gate == GATE and helper_revision == candidate_revision != F59
            and re.fullmatch('[0-9a-f]{40}', helper_revision or ''), 'new exact source gate identity required')
    require(set(proof_roots) == PROOF_ROLES, 'all graph-trusted proof roots required')
    require(archive_view is None, 'a concrete reviewed archive-view API is required before archived inputs can be selected')
    require(verification_evidence_root is not None, 'explicit fresh verification evidence root required')
    root = physical(root); capture_raw = read(root / 'result.json'); capture = parse(capture_raw)
    evidence = physical(verification_evidence_root, exists=False)
    temporary = physical(os.environ['RUNNER_TEMP'])
    data = parse(read(root / 'context.input.json'))
    protected = [root.parent, physical(repo), *[physical(path) for path in proof_roots.values()],
                 *[physical(path) for path in data['typescript_regression_inputs']['roots'].values()],
                 *[physical(path) for path in data['typescript_regression_inputs']['siblings'].values()],
                 *[physical(data[name]) for name in ('runtime_root', 'wheel_root', 'rust_tools_root', 'cargo_root')],
                 *[physical(path) for path in data.get('baseline_inputs', {}).values()],
                 *[physical(data[name]) for name in ('build_provenance_root', 'current_companions_root') if name in data]]
    require(temporary in evidence.parents and all(evidence != path and evidence not in path.parents
                and path not in evidence.parents for path in protected),
            'verification evidence must be disjoint from capture, repository and all input roots')
    receipt_value = data['typescript_regression_inputs'].get('receipt')
    require(type(receipt_value) is str and receipt_value and Path(receipt_value).is_absolute(),
            'explicit physical TypeScript consumer receipt path required')
    receipt = physical(receipt_value)
    receipt_parent = physical(receipt.parent)
    receipt_info = receipt.lstat()
    require(receipt_parent.is_dir() and stat.S_ISREG(receipt_info.st_mode) and receipt_info.st_nlink == 1,
            'single-link regular TypeScript consumer receipt and physical container required')
    require(all(evidence != path and evidence not in path.parents and path not in evidence.parents
                for path in (receipt, receipt_parent)),
            'verification evidence overlaps receipt or whole TypeScript consumer container')
    if evidence.exists():
        require(evidence.is_dir() and not any(evidence.iterdir()), 'fresh empty verification evidence directory required')
    else:
        evidence.mkdir(mode=0o700)
    require(evidence.stat().st_uid == os.getuid() and evidence.stat().st_mode & 0o022 == 0,
            'owned verification evidence root required')
    require(capture['version'] == VERSION and capture['gate'] == gate
            and capture['helper_revision'] == capture['candidate_revision'] == candidate_revision
            and capture['owned_waits_complete'] is capture['owned_cleanup_complete'] is False
            and capture['product_acceptance_passed'] is False, 'immutable pending capture identity differs')
    files = tree(root)
    for excluded in ('result.json', 'verified-result.json'):
        files.pop(excluded, None)
    require(files == capture['saved_files'], 'complete owner capture file universe changed')
    require(directory_modes(root) == capture['saved_directory_modes'], 'complete owner directory universe/modes changed')
    require(capture['scope'] == plan_scope(source_reader)
            and all(sha(source_reader(path)) == pin for path, pin in capture['bound_source_sha256'].items()),
            'captured plans/owning source changed')
    # The source-bound common function rederives real source/catalog/layout/owner
    # process/health/wait/cleanup proof. It grants no domain checker pass.
    name = '_som_typescript_owner_shared_postclose'
    shared = raw_module(source_reader, repo, name, SHARED)
    try:
        proof = shared.verify_owner_postclose(root, source_reader, helper_revision, candidate_revision,
            gate, repo=repo, proof_roots=proof_roots)
        require(proof['capture_result_sha256'] == sha(capture_raw), 'common proof does not own the immutable capture')
        with shared.admitted_sources(repo, helper_revision, source_reader,
                                     namespace='_som_owner_postclose_authority') as (_, _, admission):
            domain = verify_current_domain(root, source_reader, helper_revision, candidate_revision,
                                           repo=repo, admission=admission, shared_proof=proof,
                                           verification_evidence_root=evidence)
    finally:
        sys.modules.pop(name, None)
    # A historical audit065 result cannot establish current002/006 native scope.
    # This must be a separately source-bound current raw-domain audit, derived
    # from every original assertion plus the current TS125 refusal/rebase proof.
    # No pass object or manifest boolean can stand in for that actual verifier.
    require(domain['complete'] is True and domain['all_required_passed'] is True,
            'complete current raw TypeScript domain audit required')
    require(read(root / 'result.json') == capture_raw, 'capture changed during postclose verification')
    final_files = tree(root)
    for excluded in ('result.json', 'verified-result.json'):
        final_files.pop(excluded, None)
    require(final_files == capture['saved_files'], 'whole owner capture changed during postclose verification')
    require(directory_modes(root) == capture['saved_directory_modes'], 'whole owner directories changed during verification')
    return {'version': VERIFIED, 'gate': gate, 'helper_revision': helper_revision,
        'candidate_revision': candidate_revision, 'capture_result_sha256': sha(capture_raw),
        'shared_postclose_proof': proof, 'domain_proof': domain, 'complete': True,
        'all_required_passed': True, 'owned_waits_complete': True, 'owned_cleanup_complete': True,
        'raw_logs_complete': True, 'product_acceptance_passed': False}


def verify_current_domain(root, source_reader, helper_revision, candidate_revision, *, repo, admission, shared_proof,
                          verification_evidence_root):
    require(DOMAIN in admission['admitted_repository_python_sources'],
            'current raw TypeScript domain verifier is outside exact source admission')
    name = '_som_typescript_owner_domain_verifier'
    module = raw_module(source_reader, repo, name, DOMAIN)
    try:
        return module.verify(root, source_reader, helper_revision, candidate_revision,
                             repo=repo, admission=admission, shared_proof=shared_proof,
                             verification_evidence_root=verification_evidence_root)
    finally:
        sys.modules.pop(name, None)
