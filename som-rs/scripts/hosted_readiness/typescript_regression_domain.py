"""Re-derive recorded TypeScript owner evidence without native execution.

The old audit's raw-domain assertions are reused, never its historical integrity
or acceptance verdict. Any old attestation not yet replaced by a complete raw
replay stays explicitly incomplete. It cannot grant a current gate pass.
"""
from __future__ import annotations
import hashlib
import ast
import copy
import os
import re
import tempfile
import json
from pathlib import Path
import sys
import traceback
from types import ModuleType, SimpleNamespace

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_domain.py'
OWNER = BASE + 'typescript_regression_owner.py'
DIGESTS = BASE + 'typescript_native_digests.py'
REBASE = BASE + 'typescript_regression_rebase.py'
RAW_AUDIT = 'som-rs/scripts/typescript_output_acceptance/audit/audit065.py'
HTTP_REPLAY = BASE + 'typescript_regression_http_replay.py'
MAIN_REPLAY = BASE + 'typescript_regression_main_replay.py'
PRODUCT_PROOF = BASE + 'typescript_regression_product_proof.py'
WHEELS = BASE + 'product_python/produce_wheels.py'
RUST_TOOLS = BASE + 'product_runtime/qualify_rust_tools.py'
CLI_RUST = BASE + 'typescript_regression_cli_rust_proof.py'
PRODUCT_COMMANDS = BASE + 'typescript_regression_product_commands.py'


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


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(reader, repo, name, path):
    require(name not in sys.modules, 'preloaded TypeScript domain verifier refused')
    raw = reader(path); result = ModuleType(name)
    result.__file__, result.__package__, result.__cached__ = str(Path(repo) / path), '', None
    result.__som_raw_sha256__ = sha(raw); sys.modules[name] = result
    try:
        exec(compile(raw, result.__file__, 'exec', dont_inherit=True), result.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return result


class SourceFactories:
    """A data-only source factory reader. It is not a runtime environment."""
    def __init__(self, repo, reader, helper, candidate, admission, data):
        self.repo, self.helper_revision, self.candidate_revision = Path(repo), helper, candidate
        self.qa_mode, self.qa_source_authority, self.data = 'native-qa-v1', admission, data
        self.sources = SimpleNamespace(read=reader)
        self.cache, self.modules, self.names = {}, {}, []

    def module(self, key, path):
        require(path in self.qa_source_authority['admitted_repository_python_sources'],
                'data factory helper is outside exact source admission')
        if key not in self.modules:
            name = '_som_ts_domain_' + key
            self.modules[key] = load(self.sources.read, self.repo, name, path)
            self.names.append(name)
        return self.modules[key]

    def close(self):
        primary = None
        if self.cache.get('typescript_regression_input_binding_closers'):
            try:
                self.module('explicit_input_teardown', BASE + 'typescript_regression_inputs.py').close(self)
            except BaseException as error:
                primary = error
        for name in reversed(self.names):
            sys.modules.pop(name, None)
        if primary is not None:
            raise primary


def command_proof(owner, root, capture, expected):
    require(len(capture['rows']) == len(expected), 'all fifteen original/current commands required')
    outputs = []
    for index, (row, step) in enumerate(zip(capture['rows'], expected), 1):
        require(row['index'] == index and row['step'] == step['name']
                and row['logical_argv'] == [str(v) for v in step['argv']] and row['cwd'] == str(step['cwd']),
                'complete source-derived command order/arguments changed')
        command = row['command']; directory = root / 'commands' / f'{index:02d}'
        require(owner.parse(owner.read(directory / 'command.json')) == command
                and command['exit_code'] == 0 and command['wait_completed'] is True
                and command['owned_group_cleanup_passed'] is True and type(command['pid']) is int and command['pid'] > 0,
                'actual source command/wait/group cleanup is incomplete')
        argv = [capture['context']['observer_python'], '-I', '-S', '-B',
                str(Path(capture['context']['repo']) / (BASE + 'run_full_products.py')),
                '--execute-tool', BASE + 'typescript_regression_checker.py', '--',
                '--tool', row['script'], '--capture-root',
                str(Path(capture['original_run']) / 'raw-checkers' / f'{index:02d}'),
                '--', *row['logical_argv'][2:]]
        require(command['argv'] == argv and command['cwd'] == row['cwd'],
                'actual isolated qualified checker argv differs')
        for name in ('stdout', 'stderr'):
            require(sha(owner.read(directory / (name + '.raw'))) == command[name + '_sha256'],
                    'complete command stream differs')
        post = owner.parse(owner.read(directory / 'postcheck.json'))
        require(post['tool'] == BASE + 'typescript_regression_checker.py' and post['pid'] == command['pid']
                and post['helper_revision'] == post['candidate_revision'] == capture['candidate_revision']
                and post['entry_executed'] is True and post['entry_exit_code'] == 0
                and post['source_runtime_tools_inputs_unchanged'] is True and post['product_acceptance_passed'] is False,
                'actual entry/source/runtime postcheck differs')
        checked = owner.parse(owner.read(root / 'capture/raw-checkers' / f'{index:02d}' / 'checker-postcheck.json'))
        require(checked['tool'] == row['script'] and checked['entry_executed'] is True
                and checked['exit_code'] == 0 and checked['postcheck_complete'] is True
                and checked['source_sha256'] == capture['bound_source_sha256'][row['script']],
                'original checker raw entry execution/postcheck differs')
        passed, judgment = step['judge']({'exit_code': command['exit_code']}, root / 'capture')
        require(passed is True and row['passed'] is True and row['judgment'] == judgment,
                'original full report judge differs')
        outputs.append({'step': row['step'], 'pid': command['pid'],
                        'stdout_sha256': command['stdout_sha256'], 'stderr_sha256': command['stderr_sha256']})
    return outputs


def http_proof(owner, root, steps):
    total = 0
    for index in range(1, len(steps) + 1):
        path = root / 'capture/raw-checkers' / f'{index:02d}'
        document = owner.parse(owner.read(path / 'index.json'))
        require(document['version'] == 'som.typescript-regression-raw-http.v1'
                and document['product_acceptance_passed'] is False, 'complete raw HTTP capture identity differs')
        for n, row in enumerate(document['rows']):
            directory = path / f'{n:06d}'
            require(row['index'] == n and row['complete'] is True
                    and owner.parse(owner.read(directory / 'result.json')) == row
                    and sha(owner.read(directory / 'request.raw')) == row['request_sha256']
                    and sha(owner.read(directory / 'response.raw')) == row['response_sha256'],
                    'complete original HTTP request/response capture differs')
        wanted = {'index.json', 'checker-postcheck.json'} | {f'{n:06d}/{name}'
            for n in range(len(document['rows'])) for name in ('request.raw', 'response.raw', 'result.json')}
        files = set(owner.tree(path))
        extra_roles = {'raw-commands', 'cli-raw', 'product-http'}
        if index == 5: extra_roles.add('schema-raw')
        if index == 12: extra_roles.add('nested-rust-gates')
        require({name for name in files if name.split('/')[0] not in extra_roles} == wanted,
                'checker HTTP raw member universe differs')
        total += len(document['rows'])
    return total


def input_copy(owner, path, expected, ledger):
    """Rebuild opaque input links as metadata. Never instantiate a link."""
    actual = owner.tree(path)
    require(all('type' not in row for row in actual.values()), 'captured input aliases must remain opaque')
    for relative, row in ledger.items():
        if row['type'] == 'file':
            require(actual.get(relative) == {key: row[key] for key in ('sha256', 'size', 'mode')},
                    'complete retained input file differs')
        elif row['type'] == 'opaque-alias':
            require(relative not in actual, 'opaque input alias was instantiated')
            actual[relative] = {'type': 'internal-alias', 'target': row['target'],
                                'mode': row['mode'], 'target_sha256': row['target_sha256']}
        else:
            require(row['type'] == 'directory' and (path / relative).is_dir()
                    and (path / relative).stat().st_mode & 0o7777 == row['mode'],
                    'retained input directory differs')
    require(actual == expected, 'whole captured input regular/opaque-alias universe differs')


def auxiliary_proof(owner, root, steps):
    outputs = []
    for index in range(1, len(steps) + 1):
        path = root / 'capture/raw-checkers' / f'{index:02d}'
        commands = owner.parse(owner.read(path / 'raw-commands/index.json'))
        require(commands['version'] == 'som.typescript-regression-raw-commands.v1'
                and commands['product_acceptance_passed'] is False, 'raw command inventory identity differs')
        members = {'index.json'}
        for n, row in enumerate(commands['rows']):
            directory = path / 'raw-commands' / f'{n:06d}'
            require(row['index'] == n and row['wait_completed'] is True and type(row['pid']) is int
                    and row['pid'] > 0 and type(row['exit_code']) is int
                    and owner.parse(owner.read(directory / 'result.json')) == row,
                    'actual nested command identity/wait differs')
            members.add(f'{n:06d}/result.json')
            for stream in ('stdout', 'stderr', 'input'):
                if stream + '_sha256' in row:
                    require(sha(owner.read(directory / (stream + '.raw'))) == row[stream + '_sha256'],
                            'complete nested command raw stream differs')
                    members.add(f'{n:06d}/{stream}.raw')
            require('failure' not in row and 'cleanup_failure' not in row and 'secondary_failure' not in row
                    and not row.get('cleanup_failures') and not row.get('evidence_failures') and not row.get('secondary_failures')
                    and ('streams_complete' not in row or row['streams_complete'] is True),
                    'nested command failed or had unknown cleanup/evidence/stream completion')
        require(set(owner.tree(path / 'raw-commands')) == members, 'complete nested command universe differs')
        checked = owner.parse(owner.read(path / 'checker-postcheck.json'))
        for label, streams in (('cli', ('stdout', 'stderr', 'source')), ('product_http', ())):
            rows = checked.get(label + '_rows', [])
            directory = path / ('cli-raw' if label == 'cli' else 'product-http')
            expected_members = set()
            for n, row in enumerate(rows):
                require(row['index'] == n and owner.parse(owner.read(directory / f'{n:06d}/result.json')) == row,
                        'full CLI/product raw order differs')
                expected_members.add(f'{n:06d}/result.json')
                for stream in streams:
                    require(sha(owner.read(directory / f'{n:06d}/{stream}.raw')) == row[stream + '_sha256'],
                            'complete original CLI raw stream differs')
                    expected_members.add(f'{n:06d}/{stream}.raw')
                if label == 'cli':
                    require(owner.parse(owner.read(directory / f'checkpoint-{n:06d}.json')) == row,
                            'durable original CLI capture checkpoint differs')
                    expected_members.add(f'checkpoint-{n:06d}.json')
                    for k, request in enumerate(row['requests']):
                        require(sha(owner.read(directory / f'{n:06d}/request-{k:03d}.raw')) == request['sha256'],
                                'complete CLI sent-body bytes differ')
                        expected_members.add(f'{n:06d}/request-{k:03d}.raw')
                else:
                    require(row['complete'] is True, 'product raw HTTP collection incomplete')
                    for stream in ('request', 'response'):
                        if stream == 'request' and row['body_is_null']:
                            require(row['request_sha256'] is None, 'null product request changed')
                            continue
                        require(sha(owner.read(directory / f'{n:06d}/{stream}.raw')) == row[stream + '_sha256'],
                                'complete product raw HTTP bytes differ')
                        expected_members.add(f'{n:06d}/{stream}.raw')
            require((set(owner.tree(directory)) if directory.exists() else set()) == expected_members,
                    'CLI/product complete raw file universe differs')
        outputs.append({'step': steps[index - 1]['name'], 'nested_commands': len(commands['rows']),
                        'cli_calls': len(checked.get('cli_rows', [])),
                        'product_http_calls': len(checked.get('product_http_rows', []))})
    return outputs


def runtime_proof(ctx, owner, capture, data):
    wheels = ctx.module('typescript_data_runtime_authority', WHEELS)
    sources = wheels.Sources(ctx.repo, ctx.helper_revision)
    policy = owner.parse(sources.read(wheels.POLICY))
    # The live Context already owns reuse_runtime's fixed bootstrap namespace.
    # Re-run the exact metadata functions in a fresh source-bound namespace,
    # with independently raw-loaded support. Only the top-level namespace
    # bootstrap is replaced; no runtime qualification assertion is rewritten.
    support = ctx.module('typescript_independent_runtime_metadata_support', wheels.RUNTIME_SUPPORT)
    reuse_path, name = wheels.REUSE, '_som_ts_domain_independent_runtime_reuse'
    raw_reuse = ctx.sources.read(reuse_path); parsed = ast.parse(raw_reuse)
    q_nodes = [node for node in parsed.body if isinstance(node, ast.Assign) and len(node.targets) == 1
               and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'q']
    require(len(q_nodes) == 1 and isinstance(q_nodes[0].value, ast.Call)
            and isinstance(q_nodes[0].value.func, ast.Name)
            and q_nodes[0].value.func.id == 'bootstrap_support', 'unique original runtime support bootstrap required')
    support_pin = [node.value for node in parsed.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'SUPPORT_SHA256' for target in node.targets)]
    require(len(support_pin) == 1 and ast.literal_eval(support_pin[0]) == sha(ctx.sources.read(wheels.RUNTIME_SUPPORT)),
            'original runtime support source SHA differs')
    require(name not in sys.modules, 'fresh independent runtime metadata namespace required')
    reused = ModuleType(name); reused.__file__, reused.__package__, reused.__cached__ = str(ctx.repo / reuse_path), '', None
    reused.__som_raw_sha256__, reused.q = sha(raw_reuse), support
    sys.modules[name] = reused; ctx.names.append(name)
    selected = ast.Module(body=[node for node in parsed.body if node is not q_nodes[0]], type_ignores=[])
    ast.fix_missing_locations(selected)
    exec(compile(selected, reused.__file__, 'exec', dont_inherit=True), reused.__dict__)
    previous_bound = wheels.bound_module
    def fresh_runtime_module(used_sources, requested, path):
        require(wheels.MODULES.get(requested) == path == reuse_path,
                'only the exact original runtime metadata role can use this fresh namespace')
        require(used_sources.read(path) == raw_reuse, 'runtime metadata source differs from current Git')
        return reused
    try:
        wheels.bound_module = fresh_runtime_module
        reused, qualification, inventory, tools, native = wheels.admitted_runtime(
            Path(data['runtime_root']), sources, policy, ctx.helper_revision)
    finally:
        wheels.bound_module = previous_bound
    reused.verify_installed_tree(Path(data['runtime_root']), inventory, qualification)
    for path, pin in sources.pins.items():
        require(sha(ctx.sources.read(path)) == pin['sha256'], 'runtime source role differs from exact current Git')
    python = Path(tools['python']).resolve(strict=True)
    aliases = {str(python): sha(owner.read(python, single_link=False))}
    require(capture['qualified_external_aliases'] == aliases,
            'opaque external Python aliases differ from independently qualified runtime')
    rust_raw = ctx.sources.read(RUST_TOOLS); rust_tree = ast.parse(rust_raw)
    rust_q = [node for node in rust_tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'q']
    rust_pin = [node.value for node in rust_tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'SUPPORT_SHA' for target in node.targets)]
    require(len(rust_q) == len(rust_pin) == 1 and isinstance(rust_q[0].value, ast.Call)
            and isinstance(rust_q[0].value.func, ast.Name) and rust_q[0].value.func.id == 'bootstrap'
            and ast.literal_eval(rust_pin[0]) == sha(ctx.sources.read(wheels.RUNTIME_SUPPORT)),
            'exact original Rust metadata support bootstrap/SHA required')
    rust_name = '_som_ts_domain_independent_rust_tool_metadata'
    require(rust_name not in sys.modules, 'fresh independent Rust metadata namespace required')
    rust = ModuleType(rust_name); rust.__file__, rust.__package__, rust.__cached__ = str(ctx.repo / RUST_TOOLS), '', None
    rust.__som_raw_sha256__, rust.q = sha(rust_raw), support
    sys.modules[rust_name] = rust; ctx.names.append(rust_name)
    selected = ast.Module(body=[node for node in rust_tree.body if node is not rust_q[0]], type_ignores=[])
    ast.fix_missing_locations(selected)
    exec(compile(selected, rust.__file__, 'exec', dont_inherit=True), rust.__dict__)
    profile = rust.profile_data(ctx.sources.read('som-rs/contracts/native-tools-v1/product-rust-tools-v1.json'))
    rust.verify_qualification(Path(data['rust_tools_root']), profile, ctx.helper_revision)
    rust.verify_install_tree(Path(data['rust_tools_root']), profile)
    rust.verify_tools(Path(data['rust_tools_root']), profile)
    return {'runtime_inventory_sha256': sha(owner.canonical(inventory)),
            'qualified_python_sha256': aliases[str(python)], 'external_aliases': aliases,
            'qualified_postgres_sha256': sha(owner.read(Path(data['runtime_root']) / 'install/postgresql/bin/postgres')),
            'source_sha256': dict(sorted(sources.pins.items())), 'rust_profile_sha256': sha(owner.canonical(profile)),
            'product_acceptance_passed': False}


def input_origin(ctx, owner, capture, data, root):
    original = owner.parse(owner.read(root / 'input-origin.json'))
    producer = data['typescript_regression_input_producer']
    require(capture['inputs']['producer'] == producer
            and original['producer_catalog_sha256'] == sha(owner.canonical(producer))
            and original['index_sha256'] == producer['index_sha256']
            and original['artifact_id'] == producer['artifact_id']
            and original['artifact_digest'] == producer['artifact_digest']
            and producer['job'] == 'typescript-regression-inputs'
            and producer['head_sha'] == ctx.helper_revision
            and producer['run_id'] == original['run_id'] == os.environ['GITHUB_RUN_ID']
            and producer['run_attempt'] == original['run_attempt'] == os.environ['GITHUB_RUN_ATTEMPT']
            and original['profile_sha256'] == producer['profile_sha256'] == capture['inputs']['profile_sha256']
            and original['roots'] == data['typescript_regression_inputs']['roots']
            and original['siblings'] == data['typescript_regression_inputs']['siblings'],
            'fresh TS input receipt/current source producer output joins differ')
    artifact = capture['inputs']['artifact']
    graph = ctx.module('typescript_data_input_origin_graph', BASE + 'same_candidate_graph.py')
    actual = graph.github_json(os.environ['GITHUB_REPOSITORY'], '/actions/artifacts/' + str(artifact['id']))
    require(actual['id'] == producer['artifact_id'] == artifact['id'] and actual['name'] == artifact['name']
            and actual['digest'] == producer['artifact_digest'] == artifact['digest'] and actual['expired'] is False
            and str(actual['workflow_run']['id']) == producer['run_id']
            and actual['workflow_run']['head_sha'] == producer['head_sha'],
            'live TS input artifact differs from exact producer output')
    return {'producer_catalog_sha256': sha(owner.canonical(producer)),
            'consumer_receipt_sha256': sha(owner.read(root / 'input-origin.json')),
            'artifact': artifact, 'profile_sha256': producer['profile_sha256'],
            'same_run_source_bound_job_output_required': True,
            'full_transport_consumer_verification_complete': False,
            'product_acceptance_passed': False}


def discharged(row, original, replacements):
    if row['id'] not in replacements or original.unrun or original.post:
        return False
    if not original.failed:
        return True
    # These old pre-native equalities are explicitly replaced by the exact full
    # native3-leaf replay. Other failed checks are never cleared by a new label.
    if row['id'] not in {'ts.python_unchanged.0', 'rust.amendment005.2'}:
        return False
    prefix = r'(?:\[ts\.python_unchanged\.0\] )?'
    wanted = prefix + r'\d+ rows with (?:compile qa_response_sha256 != baseline_response_sha256|assemble qa_request_sha256 != baseline_request_sha256)'
    return all(re.fullmatch(wanted, message) is not None for message in original.failed)


def required_unrun_obligations(before_role):
    require(before_role in {'recorded-before-supplemental', 'live-before-profile'},
            'exact declared before role required')
    # A profile's identity does not establish strict original live assertions.
    # The native-three/four-leaf comparisons are separate supplemental proofs.
    return [{'id': name, 'status': 'unrun', 'reason': reason} for name, reason in (
        ('ts.python_unchanged.strict_live_before',
         'original strict Python whole-request/response live2bce equality has no independent replay adapter'),
        ('ts.amendment002.live_before',
         'original frozen TS002 three-member live2bce comparison is not proved by native four-leaf replay'),
        ('rust.amendment006.live_before',
         'original frozen Rust006 three-member live2bce comparison is not proved by native four-leaf replay'),
        ('ts.inputs.full_transport_consumer',
         'source-admitted same-run complete ZIP/index/cipher/manifest/AEAD/ordered-chunk/five-role/RS22 consumer API is not integrated'))]


def verify(root, source_reader, helper, candidate, *, repo, admission, shared_proof, verification_evidence_root):
    require(admission.get('source_authority_active') is True
            and admission.get('helper_revision') == admission.get('candidate_revision') == helper == candidate,
            'actual native source authority before domain helper execution required')
    require({SELF, OWNER, DIGESTS, REBASE, RAW_AUDIT, HTTP_REPLAY, MAIN_REPLAY, PRODUCT_PROOF, WHEELS, RUST_TOOLS,
             CLI_RUST, PRODUCT_COMMANDS}
            <= set(admission['admitted_repository_python_sources']),
            'complete TypeScript raw-domain source admission required')
    owner_name, audit_name = '_som_ts_domain_owner_contract', '_som_ts_domain_original_raw_audit'
    initial_modules = dict(sys.modules)
    owner = load(source_reader, repo, owner_name, OWNER)
    ctx = None
    try:
        raw = owner.read(root / 'result.json'); capture = owner.parse(raw)
        data = owner.parse(owner.read(root / 'context.input.json'))
        require(sha(owner.read(root / 'context.input.json')) == shared_proof['context_sha256']
                and data['helper_revision'] == data['candidate_revision'] == candidate
                and data['qa_mode'] == 'native-qa-v1', 'original checked Context copy differs')
        capture['context'] = data  # Local data view only. Never rewrite capture/result.json.
        profile_raw = source_reader(owner.INPUT_PROFILE)
        require(owner.read(root / 'input-profile.json') == profile_raw, 'captured immutable input profile differs')
        profile = owner.parse(profile_raw)
        require(sha(owner.read(root / 'input-origin.json')) == capture['inputs']['receipt_sha256'], 'input receipt differs')
        for role in owner.INPUT_ROLES:
            require(profile['roles'][role]['files'] == capture['inputs']['files'][role],
                    'complete independently source-pinned input role differs: ' + role)
            input_copy(owner, root / 'inputs' / role, profile['roles'][role]['files'],
                       capture['input_capture_inventory'][role])
        require(set(profile['siblings']) == set(capture['sibling_roots']) == {'rs_prompts'},
                'exact independent RS sibling role required')
        input_copy(owner, root / 'inputs/siblings/rs_prompts', profile['siblings']['rs_prompts']['files'],
                   capture['sibling_capture_inventory']['rs_prompts'])
        require(Path(capture['original_run']) == Path(data['work_root']) / 'typescript-regression',
                'original products were outside their checked Context work root')
        replay_bindings = copy.deepcopy(data['typescript_regression_inputs'])
        replay_bindings['roots'] = {
            role: str(root / 'inputs' / role) for role in owner.INPUT_ROLES}
        replay_bindings['siblings'] = {
            'rs_prompts': str(root / 'inputs/siblings/rs_prompts')}
        ctx = SourceFactories(repo, source_reader, helper, candidate, admission, data)
        ctx.cache['typescript_replay_input_bindings'] = replay_bindings
        runtime = runtime_proof(ctx, owner, capture, data)
        origin = input_origin(ctx, owner, capture, data, root)
        collector = ctx.module('original_step_constructor', owner.COLLECTOR)
        roots = {name: Path(value) for name, value in capture['input_roots'].items()}
        expected = owner.collector_steps(collector, ctx, Path(capture['services']['current']['candidate_dir']),
            Path(capture['original_run']), roots, 'http://127.0.0.1:18978', 'http://127.0.0.1:18979')
        commands = command_proof(owner, root, capture, expected)
        http = http_proof(owner, root, expected)
        auxiliary = auxiliary_proof(owner, root, expected)
        baseline_replay = ctx.module('full_original_baseline_http_replay', HTTP_REPLAY).verify(ctx, root, capture)
        main = ctx.module('full_original_pure_main_replay', MAIN_REPLAY)
        evidence_parent = Path(verification_evidence_root).absolute()
        require(evidence_parent.is_dir() and not any(evidence_parent.iterdir())
                and not any(path.is_symlink() for path in (evidence_parent, *evidence_parent.parents)),
                'explicit fresh physical verification-evidence parent required')
        replay_root = Path(tempfile.mkdtemp(prefix='invocation-', dir=evidence_parent))
        (replay_root / 'invocation.json').write_bytes(owner.canonical({
            'gate': owner.GATE, 'helper_revision': helper, 'candidate_revision': candidate,
            'capture_result_sha256': sha(raw), 'run_id': os.environ['GITHUB_RUN_ID'],
            'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'job': os.environ['GITHUB_JOB'],
            'product_acceptance_passed': False}) + b'\n')
        # The replay helper itself requires a new output root. Keep all of its
        # raw audit streams outside immutable capture, including first failure.
        main_replay = main.verify(ctx, root, capture, out=replay_root / 'original-mains')
        products = ctx.module('full_typescript_product_raw_proof', PRODUCT_PROOF).verify(ctx, root, capture)
        cli_rust = ctx.module('full_original_cli_rust_raw_proof', CLI_RUST).verify(
            ctx, root, capture, out=replay_root / 'cli-rust')
        product_commands = ctx.module('full_original_product_command_proof', PRODUCT_COMMANDS).verify(
            ctx, root, capture, runtime)
        # The new full raw125 proof is independently replayed, including exact
        # factory-owned four SHA leaves, old-bundle diagnostics/spans and files.
        rebase = ctx.module('current_typescript_refusal_rebase', REBASE)
        raw_rebase = rebase.verify_capture(ctx, root / 'capture/typescript002-refusal-rebase',
                                          root / 'inputs/legacy_run/run-005')
        audit = load(source_reader, repo, audit_name, RAW_AUDIT)
        original_reader = audit.Reader
        mappings = [
            (str(Path(capture['original_run'])), root / 'capture'),
            ('.local/rust-compiler-goal/acceptance/public-products-003b', root / 'inputs/public_run'),
            ('.local/rust-compiler-goal/acceptance/legacy544', root / 'inputs/legacy_run'),
            ('typescript_regression_inputs/legacy_run', root / 'inputs/legacy_run'),
            ('.local/rust-compiler-goal/acceptance/rust-output-055/run-003', root / 'inputs/rust_run'),
            ('.local/typescript-output/candidate-001', root.parent / 'candidate')]
        mappings += [(old, root / 'inputs' / role) for role, old in capture['input_roots'].items()]
        class BoundReader(original_reader):
            def key(self, value):
                value = str(Path(value).absolute())
                for old, mapped in sorted(mappings, key=lambda row: -len(str(row[1]))):
                    mapped = str(mapped)
                    if value == mapped or value.startswith(mapped + '/'):
                        return old + value[len(mapped):]
                return super().key(value)

            def p(self, value):
                text = str(value)
                for old, mapped in sorted(mappings, key=lambda row: -len(row[0])):
                    if text == old or text.startswith(old + '/'):
                        return str(mapped) + text[len(old):]
                    absolute_old = str(Path(repo) / old) if not Path(old).is_absolute() else None
                    if absolute_old and (text == absolute_old or text.startswith(absolute_old + '/')):
                        return str(mapped) + text[len(absolute_old):]
                path = Path(text) if Path(text).is_absolute() else Path(repo) / text
                require(Path(repo) in path.parents or root in path.parents or root.parent / 'candidate' in path.parents,
                        'legacy raw audit read an undeclared external path')
                return str(path)

            def bytes(self, value):
                body = super().bytes(value)
                path = Path(self.p(value))
                if Path(repo) in path.parents:
                    require(source_reader(path.relative_to(repo).as_posix()) == body,
                            'raw audit contract/data differs from exact current Git')
                return body
        audit.Reader = BoundReader
        # Explicit current-mode source expectations. The archived auditor and its
        # historical constants remain byte exact in Git; no old acceptance runs.
        digest = ctx.module('native_digest_source_factory', DIGESTS)
        factory, _, _, ts_after = digest.authorities(ctx, 'typescript')
        _, _, _, rust_after = digest.authorities(ctx, 'rust')
        audit.TS_TC_SHA, audit.RS_TC_SHA = ts_after['target_contract'], rust_after['target_contract']
        audit.CAND_REL = str(root.parent / 'candidate')
        original_assets = audit.Audit.expected_ts_assets
        def current_assets(instance, row):
            result = original_assets(instance, row)
            result['target_contract']['sha256'] = ts_after['target_contract']
            result['formatter']['sha256'] = ts_after['formatter']
            require(result['backend']['sha256'] == ts_after['backend'], 'native TypeScript backend source vector differs')
            return result
        audit.Audit.expected_ts_assets = current_assets
        check = audit.Audit(repo, root / 'capture')
        families = ('python_unchanged', 'target_selection', 'scope_checks', 'bundle_checks',
                    'assemble_refusals', 'product_gates', 'rust', 'rust_extras')
        for family in families:
            getattr(check, family)()
        rows = [row.to_json() for row in check.rows]
        # Replacement proof is keyed to its exact original row. Keep the old
        # archived audit rows unchanged and never clear an unknown attestation.
        replacements = dict(baseline_replay['replacements'])
        replacements.update({name: main_replay for name in main_replay['replaces']})
        replacements.update(products['replacement_rows'])
        replacements.update({name: cli_rust for name in cli_rust['replaces']})
        replacements.update({name: product_commands for name in product_commands['replaces']})
        incomplete = [{'id': row['id'], 'status': row['status'], 'reason': row['reason']}
                      for row, original in zip(rows, check.rows) if row['status'] != 'passed'
                      and not discharged(row, original, replacements)]
        before_role = owner.baseline_role(source_reader, profile['baseline_profile'])
        require(before_role == capture['inputs']['before_comparison_role'], 'captured before role changed')
        incomplete.extend(required_unrun_obligations(before_role))
        # Old attested hash-only statements are explicitly NOT promoted to raw
        # native proof. Their replacement is remaining author work, not a flag.
        return {'version': 'som.typescript-regression-current-domain-proof.v1',
                'gate': owner.GATE, 'helper_revision': helper, 'candidate_revision': candidate,
                'source_scope': owner.plan_scope(source_reader), 'commands': commands,
                'raw_http_exchanges': http, 'auxiliary_raw_captures': auxiliary,
                'full_original_baseline_replay': baseline_replay,
                'qualified_runtime_tools': runtime, 'input_origin': origin,
                'full_original_pure_main_replay': main_replay, 'full_typescript_product_raw_proof': products,
                'full_original_cli_rust_raw_proof': cli_rust,
                'full_original_product_command_proof': product_commands,
                'verification_evidence_role': 'typescript-regression-verification-evidence',
                'before_comparison_role': before_role,
                'frozen_live_before_checks_complete': False,
                'native_replay_role': 'supplemental-only',
                'full_transport_consumer_verification_complete': False,
                'typescript125_refusal_rebase': raw_rebase,
                'original_domain_families': list(families), 'original_raw_audit_rows': rows,
                'remaining_raw_assertions': incomplete,
                'complete': not incomplete, 'all_required_passed': not incomplete,
                'product_acceptance_passed': False}
    finally:
        primary = sys.exc_info()[1]
        failure = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
        if primary is not None:
            remember_failure(failure, 'original_domain_assertion', primary)
        if ctx is not None:
            try:
                ctx.close()
            except BaseException as error:
                remember_failure(failure, 'domain_source_cleanup', error)
                evidence_parent = Path(verification_evidence_root)
                try:
                    if evidence_parent.is_dir():
                        with (evidence_parent / 'source-cleanup-failure.json').open('xb') as stream:
                            stream.write(owner.canonical({**failure_record(error),
                                'product_acceptance_passed': False}) + b'\n')
                except BaseException as evidence:
                    remember_failure(failure, 'domain_source_cleanup_evidence', evidence)
        sys.modules.pop(audit_name, None)
        sys.modules.pop(owner_name, None)
        for name, module in list(sys.modules.items()):
            if name in initial_modules:
                continue
            value = getattr(module, '__file__', None)
            if value is not None and Path(repo) in Path(value).parents:
                relative = Path(value).relative_to(repo).as_posix()
                if relative in admission['admitted_repository_python_sources']:
                    try:
                        require(getattr(module, '__som_raw_sha256__', None) == sha(source_reader(relative)),
                                'unbound new source module cannot be silently removed')
                        sys.modules.pop(name, None)
                    except BaseException as error:
                        remember_failure(failure, 'domain_source_module_postcheck_' + name, error)
        if failure['primary'] is not None:
            try:
                with (Path(verification_evidence_root) / 'domain-first-failure.json').open('xb') as stream:
                    stream.write(owner.canonical({**failure['failure'], 'secondary_failures': failure['secondary'],
                        'product_acceptance_passed': False}) + b'\n')
            except BaseException as evidence:
                remember_failure(failure, 'domain_first_failure_evidence', evidence)
            raise_primary(failure)
