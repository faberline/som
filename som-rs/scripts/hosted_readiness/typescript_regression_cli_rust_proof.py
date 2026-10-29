"""Replay the original CLI and nested Rust bundle/refusal assertions."""
from __future__ import annotations
import traceback
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
from types import SimpleNamespace

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_cli_rust_proof.py'
CLI = 'som-rs/scripts/rust_output_acceptance/check_cli.py'
RUST = 'som-rs/som-targets/qa/check_rust_products.py'
HTTP = BASE + 'typescript_regression_http_replay.py'
MAIN = BASE + 'typescript_regression_main_replay.py'
PRODUCT = BASE + 'typescript_regression_product_proof.py'
INPUTS = BASE + 'typescript_regression_inputs.py'
UNCHANGED = 'som-rs/scripts/rust_output_acceptance/check_rust_unchanged.py'
BASELINE = 'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py'
CURRENT = 'http://127.0.0.1:18978'


PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'


class _InputEnvironment:
    def __init__(self):
        self.environ = {'SOM_FULL_PRODUCT_CONTEXT': 'source-bound-data-replay'}


class _InputMetadata:
    def __init__(self, repo, bindings, profile):
        require(type(bindings) is dict and type(profile) is str, 'exact native input metadata data required')
        self.repo, self.bindings, self.profile = repo, bindings, profile

    def context(self): return self
    @property
    def qa_mode(self): return 'native-qa-v1'
    @property
    def data(self): return {'typescript_regression_inputs': self.bindings}
    @property
    def cache(self): return {'typescript_replay_input_bindings': self.bindings}
    @property
    def sources(self): return self
    def read(self, path):
        require(path == PROFILE, 'unlisted native input metadata source read')
        return self.profile


class _InputTargets:
    def __init__(self, repo): self.repo = repo
    @property
    def REPO(self): return self.repo
    def sha(self, raw):
        _native_controls()
        return _native_original_target_sha(raw)


def _native_controls(ctx=None):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    require(controls.role_sources.get('ctx.full-original-cli-rust-raw-proof') == SELF,
            'fixed native CLI/Rust source role required')
    if ctx is not None:
        require(ctx.qa_mode == 'native-qa-v1' and Path(ctx.repo).absolute() == controls.root
                and ctx.helper_revision == controls.helper_revision == ctx.candidate_revision == controls.candidate_revision,
                'actual native CLI/Rust context identity required')
    return controls


@contextmanager
def _native_input_key(ctx, view, snapshot):
    controls = _native_controls(ctx)
    require(snapshot[RUST] == ctx.sources.read(RUST) and snapshot[PROFILE] == ctx.sources.read(PROFILE),
            'actual native input metadata source bytes differ')
    bindings = view.cache.get('typescript_replay_input_bindings', view.data['typescript_regression_inputs'])
    environment, metadata, targets = (_InputEnvironment(),
        _InputMetadata(controls.root, bindings, snapshot[PROFILE].decode('utf-8', 'strict')),
        _InputTargets(controls.root))
    lease, primary = controls.borrow(), None
    try:
        with lease.load_fragment('ts.cli-rust.input-source-key') as original:
            yield original.input_source_key
    except BaseException as error:
        primary = error
        raise
    finally:
        failure = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
        if primary is not None:
            remember_failure(failure, 'original_native_input_key_assertion', primary)
        for role, action in (('native_input_key_source_postcheck', lease.verify_installed),
                             ('native_input_key_borrow_close', lease.close)):
            try:
                action()
            except BaseException as error:
                remember_failure(failure, role, error)
        if failure['secondary'] or primary is None:
            raise_primary(failure)


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


def read(path):
    path = Path(path).absolute()
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), 'regular replay member required')
    return path.read_bytes()


def parse(raw):
    return json.loads(raw)


def deny(*args, **kwargs):
    raise ValueError('native process/live network forbidden during CLI/Rust raw replay')


def preserving_evidence(primary, actions):
    failure = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
    if primary is not None:
        remember_failure(failure, 'original_assertion', primary)
    for role, action in actions:
        try:
            action()
        except BaseException as error:
            remember_failure(failure, role, error)
    raise_primary(failure)


def cli(ctx, root, capture, out):
    module = ctx.module('typescript_cli_original_assertions', CLI)
    http = ctx.module('typescript_cli_strict_http', HTTP)
    products = ctx.module('typescript_cli_raw_commands', PRODUCT)
    directory = root / 'capture/raw-checkers/03'
    tape = http.Tape(directory)
    post = parse(read(directory / 'checker-postcheck.json')); rows = post['cli_rows']
    commands = products.commands(directory / 'raw-commands')
    cursor = 0
    binary = str(Path(capture['services']['current']['candidate_dir']) / 'som')
    def recorded(binary_arg, base, source, target):
        nonlocal cursor
        require(cursor < len(rows) and base == CURRENT and binary_arg == binary, 'missing or wrong owned CLI row')
        index = cursor; cursor += 1
        row = rows[index]; saved = directory / 'cli-raw' / f'{index:06d}'
        require(row['index'] == index and row['binary'] == binary and row['base_url'] == base
                and row['target'] == target and read(source) == read(saved / 'source.raw')
                and sha(read(source)) == row['source_sha256'] and len(row['requests']) == 1,
                'source-derived original CLI input/target differs')
        argv = row['argv']; tail = [] if target is None else ['--target', target]
        require(argv[:2] == [binary, 'compile'] and argv[3] == '--url' and argv[5:] == tail,
                'actual original CLI argv differs')
        matched = [value for value in commands if value['argv'] == argv
                   and value['exit_code'] == row['exit_code']
                   and value.get('stdout_sha256') == row['stdout_sha256']
                   and value.get('stderr_sha256') == row['stderr_sha256']]
        require(len(matched) == 1, 'CLI streams do not join one actual child PID/wait')
        request = read(saved / 'request-000.raw')
        require(row['requests'][0] == {'route': '/v1/compile', 'sha256': sha(request)}, 'CLI sent-body raw binding differs')
        tape(base, '/v1/compile', request)  # Actual proxy-forwarded request.
        return subprocess.CompletedProcess(argv, row['exit_code'], read(saved / 'stdout.raw'), read(saved / 'stderr.raw')), [('/v1/compile', request)]
    previous = {name: getattr(module, name) for name in
                ('check', '_run_cli_with_request_capture', 'post', 'subprocess', 'NO_PROXY_OPENER', 'tempfile')}
    original_check = module.check
    def complete_check(binary_arg, base, scratch, label, source, target, expect=None, verify_request=False):
        return original_check(binary_arg, base, scratch, label, source, target, expect, verify_request=True)
    stdout, stderr = io.StringIO(), io.StringIO()
    installed = {}
    class RetainedReplayScratch:
        def __init__(self, *, prefix):
            require(prefix == 'som-cli-', 'original CLI scratch role changed')
            self.name = tempfile.mkdtemp(prefix=prefix, dir=out)
        def __enter__(self): return self.name
        def __exit__(self, *args): return False
    try:
        module.check, module._run_cli_with_request_capture = complete_check, recorded
        module.post = lambda base, data: tape(base, '/v1/compile', data)
        module.subprocess, module.NO_PROXY_OPENER = SimpleNamespace(run=deny), SimpleNamespace(open=deny)
        module.tempfile = SimpleNamespace(TemporaryDirectory=RetainedReplayScratch)
        installed = {name: getattr(module, name) for name in previous}
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = module.main(['--binary', binary, '--base-url', CURRENT,
                                '--out', str(out / 'cli.json'), '--refusals', 'phase2'])
        saved = parse(read(root / 'capture/cli.json')); result = parse(read(out / 'cli.json'))
        require(code == 0 and result == saved and result['failed'] == []
                and result['passed'] == result['total'] == cursor == len(rows),
                'complete original CLI assertion report differs')
        require(len(commands) == len(rows), 'extra original CLI native command')
        return {'original_cli_calls': cursor, **tape.finish(), 'report_sha256': sha(read(root / 'capture/cli.json')),
                'replaces': ['ts.target_selection.4', 'rust.amendment005.8'], 'product_acceptance_passed': False}
    finally:
        def restore(name, value):
            require(getattr(module, name) is installed[name], 'CLI replay attribute replaced by later owner: ' + name)
            setattr(module, name, value)
        actions = [('cli_attribute_restore_' + name, lambda name=name, value=value: restore(name, value))
                   for name, value in previous.items() if name in installed]
        actions += [('cli_stdout_evidence', lambda: (out / 'cli-replay.stdout.raw').write_bytes(stdout.getvalue().encode())),
                    ('cli_stderr_evidence', lambda: (out / 'cli-replay.stderr.raw').write_bytes(stderr.getvalue().encode()))]
        preserving_evidence(sys.exc_info()[1], actions)


def nested_rust(ctx, root, capture, out):
    module = ctx.module('typescript_nested_original_rust_assertions', RUST)
    http = ctx.module('typescript_nested_strict_http', HTTP)
    main = ctx.module('typescript_nested_write_guards', MAIN)
    products = ctx.module('typescript_nested_raw_command_proof', PRODUCT)
    directory = root / 'capture/raw-checkers/12'
    post = parse(read(directory / 'checker-postcheck.json'))
    state = post['nested_rust_capture']
    work = root / 'capture/nested-rust-cluster'
    require(parse(read(work / 'nested-rust-result.json')) == state and state['original_cluster_scope'] is True,
            'actual nested Rust cluster capture differs')
    pg = state['postgres']; cleanup = parse(read(work / 'pg-cleanup.json'))
    require(cleanup == state['cleanup'] and cleanup['wait_attempted'] is True and cleanup['wait_completed'] is True
            and type(cleanup['exit_code']) is int and cleanup['cleanup_errors'] == []
            and cleanup['remaining_listeners'] == [],
            'actual nested PostgreSQL wait/cleanup incomplete')
    first = {key: value for key, value in pg.items() if key != 'final_owned_socket'}
    require(parse(read(work / 'pg-start.json')) == first and pg['start']['pid'] == pg['pid']
            and pg['start']['pgrp'] == pg['start']['session'] == pg['pid']
            and pg['start']['cwd'] == pg['cwd'] and pg['owned_socket'] == pg['final_owned_socket'],
            'actual nested PostgreSQL owner/socket changed')
    command = state['child_command']; row = capture['rows'][11]
    child = directory / 'nested-rust-gates'
    checked = parse(read(child / 'checker-postcheck.json'))
    require(checked['tool'] == RUST and checked['entry_executed'] is True and checked['exit_code'] == 0
            and checked['postcheck_complete'] is True and checked['source_sha256'] == sha(ctx.sources.read(RUST))
            and command['wait_completed'] is True and command['exit_code'] == 0
            and command['owned_group_cleanup_passed'] is True, 'actual nested source entry/child wait differs')
    require(row['logical_argv'][2] == 'cluster-gates' and row['logical_argv'][-1].endswith('/behavior/parity/report-parity.json'),
            'original nested Rust scope/explicit parity input differs')
    expected_tail = ['--tool', RUST, '--capture-root', str(Path(capture['original_run']) / 'raw-checkers/12/nested-rust-gates'),
                     '--', '--pg', '127.0.0.1:' + str(pg['owned_socket']['port']), *row['logical_argv'][3:]]
    require(command['argv'] == [capture['context']['observer_python'], '-I', '-S', '-B',
        str(Path(capture['context']['repo']) / (BASE + 'run_full_products.py')), '--execute-tool',
        BASE + 'typescript_regression_checker.py', '--', *expected_tail], 'actual nested original command differs')
    original_context = root / 'original-context/environment-commands'
    for stream in ('stdout', 'stderr'):
        path = original_context / Path(command[stream]).name
        require(sha(read(path)) == command[stream + '_sha256'], 'complete nested original stream differs')
    nested_commands = products.commands(child / 'raw-commands')
    require(all(value['argv'][0] == '/usr/bin/git' and value['exit_code'] == 0 for value in nested_commands),
            'no-gates Rust assertion child ran an unexpected native command')
    tape = http.Tape(child)
    previous_post, previous_legacy = module.targets.post, module.targets.LEGACY
    previous_pinned, previous_hosted = module.pinned_toolchain, getattr(module, 'hosted_products', None)
    previous_key = module.input_source_key
    argv_before = sys.argv
    profile_path = BASE + 'inputs/typescript-regression-inputs-v1.json'
    snapshot = main.source_snapshot(ctx, {SELF, RUST, profile_path})
    view = main.FrozenContext(ctx, root, snapshot, {})
    output = out / 'rust-products.json'
    replay_args = ['--qa', CURRENT, '--pg', '127.0.0.1:' + str(pg['owned_socket']['port']),
        '--work', str(out / 'rust-products-work'), '--target-dir', str(out / 'rust-target'), '--out', str(output),
        '--no-gates', '--refusals', 'phase2', '--parity', str(root / 'inputs/rust_run/behavior/parity/report-parity.json')]
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        module.targets.post = lambda base, body, route='/v1/compile': tape(base, route, body.encode() if isinstance(body, str) else body)
        module.targets.LEGACY = root / 'inputs/legacy_run'
        module.pinned_toolchain = lambda: {}  # Tools are separately rederived, and --no-gates executes none.
        module.hosted_products = SimpleNamespace(context=lambda: view)
        definitions = [node for node in ast.parse(snapshot[RUST]).body if isinstance(node, ast.FunctionDef)
                       and node.name == 'input_source_key']
        require(len(definitions) == 1, 'exact source-owned native input metadata key adapter required')
        with _native_input_key(ctx, view, snapshot) as input_key:
            module.input_source_key = input_key
            try:
                sys.argv = [str(Path(ctx.repo) / RUST), *replay_args]
                with main.MainGuards(view, root, capture, out, tape), redirect_stdout(stdout), redirect_stderr(stderr):
                    code = module.main()
                saved = parse(read(root / 'capture/rust-products.json')); result = parse(read(output))
                require(code == 0 and result == saved and saved['summary']['failed'] == []
                        and saved['summary']['blocked'] == []
                        and saved['summary']['bundle_checks'] == {'passed': 115, 'total': 115}
                        and saved['summary']['assemble_refusals'] == {'passed': 15, 'total': 15},
                        'original full115 Rust bundle/fifteen-recipe assertions differ')
                return {'original_bundle_cases': 115, 'original_phase2_recipes': 15, **tape.finish(),
                    'report_sha256': sha(read(root / 'capture/rust-products.json')),
                    'replaces': ['rust.amendment005.7', 'rust.phase2.A6_amendment_004',
                                 'rust.phase2.refusals_amendment_004'], 'product_acceptance_passed': False}
            finally:
                module.input_source_key = previous_key
    finally:
        module.targets.post, module.targets.LEGACY = previous_post, previous_legacy
        module.pinned_toolchain, module.input_source_key = previous_pinned, previous_key
        if previous_hosted is None:
            del module.hosted_products
        else:
            module.hosted_products = previous_hosted
        sys.argv = argv_before
        preserving_evidence(sys.exc_info()[1], [
            ('rust_stdout_evidence', lambda: (out / 'rust-replay.stdout.raw').write_bytes(stdout.getvalue().encode())),
            ('rust_stderr_evidence', lambda: (out / 'rust-replay.stderr.raw').write_bytes(stderr.getvalue().encode()))])


def verify(ctx, root, capture, *, out):
    require(ctx.qa_mode == 'native-qa-v1' and ctx.qa_source_authority['source_authority_active'] is True
            and {SELF, CLI, RUST, HTTP, MAIN, PRODUCT, INPUTS, UNCHANGED, BASELINE} <=
                set(ctx.qa_source_authority['admitted_repository_python_sources']),
            'exact CLI/Rust original assertion source admission required')
    root, out = Path(root).absolute(), Path(out).absolute()
    require(Path(os.environ['RUNNER_TEMP']) in out.parents and not out.exists() and root not in out.parents,
            'fresh disjoint CLI/Rust raw replay output required')
    out.mkdir(mode=0o700)
    cli_proof = cli(ctx, root, capture, out)
    rust_proof = nested_rust(ctx, root, capture, out)
    unchanged_proof = rust_unchanged(ctx, root, capture, out)
    return {'version': 'som.typescript-regression-cli-rust-raw-proof.v1',
            'cli': cli_proof, 'rust': rust_proof, 'unchanged': unchanged_proof,
            'replaces': [*cli_proof['replaces'], *rust_proof['replaces'], *unchanged_proof['replaces']],
            'product_acceptance_passed': False}


def rust_unchanged(ctx, root, capture, out):
    main = ctx.module('typescript_unchanged_main_guards', MAIN)
    http = ctx.module('typescript_unchanged_strict_tape', HTTP)
    products = ctx.module('typescript_unchanged_actual_commands', PRODUCT)
    module = ctx.module('typescript_unchanged_original_checker', UNCHANGED)
    inputs = ctx.module('typescript_unchanged_explicit_inputs', INPUTS)
    baseline = ctx.module('typescript_fresh_case_record_loader', BASELINE)
    needed = {SELF, INPUTS, UNCHANGED, BASELINE}
    snapshot = main.source_snapshot(ctx, needed)
    view = main.FrozenContext(ctx, root, snapshot, {BASELINE: baseline})
    tape_root = root / 'capture/raw-checkers/13'
    tape = http.Tape(tape_root)
    commands = products.commands(tape_root / 'raw-commands')
    require(all(row['argv'][0] == '/usr/bin/git' and row['exit_code'] == 0 for row in commands),
            'fresh Rust unchanged checker ran an unexpected native command')
    previous_post = module.phase2.post
    previous_argv = sys.argv
    output = out / 'rust-unchanged.json'
    try:
        module.phase2.post = lambda base, body, route='/v1/compile': tape(base, route,
            body.encode() if isinstance(body, str) else body)
        inputs.configure(view, module)
        sys.argv = [str(Path(ctx.repo) / UNCHANGED), '--qa', CURRENT, '--run', str(root / 'inputs/rust_run'),
            '--phase1', str(root / 'capture/rust-products.json'), '--out', str(output), '--workers', '1']
        with main.MainGuards(view, root, capture, out, tape), redirect_stdout(io.StringIO()):
            code = module.main()
        saved = parse(read(root / 'capture/rust-unchanged.json')); result = parse(read(output))
        original = copy.deepcopy(saved)
        original['run'] = str(root / 'inputs/rust_run')
        original['phase1']['record']['path'] = str(root / 'inputs/rust_run/rust-products.json')
        original['phase1']['report']['path'] = str(root / 'capture/rust-products.json')
        require(code == 0 and result == original and result['passed'] is True
                and result['phase1']['total'] == result['phase1']['passed'] == 115
                and result['phase1']['failed'] == []
                and result['phase2']['total'] == result['phase2']['passed'] == 584
                and result['phase2']['files_equal'] == 582 and result['phase2']['failed'] == []
                and len(result['phase2']['refusals']) == 2,
                'complete original115+584 Rust file/refusal assertions differ')
        return {'original_phase1_cases': 115, 'original_phase2_cases': 584, 'phase2_products': 582,
            'compiler_refusals': result['phase2']['refusals'], **tape.finish(),
            'explicit_path_fields_only': ['run', 'phase1.record.path', 'phase1.report.path'],
            'report_sha256': sha(read(root / 'capture/rust-unchanged.json')),
            'replaces': ['rust.amendment005.5'], 'product_acceptance_passed': False}
    finally:
        primary = sys.exc_info()[1]
        module.phase2.post = previous_post
        sys.argv = previous_argv
        preserving_evidence(primary, [('rust_unchanged_input_cleanup', lambda: inputs.close(view))])
