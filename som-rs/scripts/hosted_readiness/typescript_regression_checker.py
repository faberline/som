"""Execute one exact original checker and retain its complete QA HTTP bytes."""
from __future__ import annotations
import argparse
import copy
import hashlib
import io
import json
import socket
from pathlib import Path
import sys
import traceback
import threading
from contextlib import contextmanager
from types import FunctionType, MethodType, ModuleType
import urllib.error
import urllib.parse
import urllib.request

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_checker.py'
DIGESTS = BASE + 'typescript_native_digests.py'
RAW_CAPTURE = BASE + 'typescript_regression_raw_capture.py'
INPUT_ADAPTER = BASE + 'typescript_regression_inputs.py'
SCHEMA_CAPTURE = BASE + 'typescript_regression_schema_capture.py'
NESTED_RUST = BASE + 'typescript_regression_nested_rust.py'


CONTEXT = BASE + 'product_runtime/full_product_context.py'
NATIVE_HOST = BASE + 'typescript_native_host.py'
_TARGETS = {'som-rs/scripts/hosted_readiness/typescript_regression_rebase.py': {'module': '_som_ts_native_original_typescript002_refusal_rebase',
                                                                     'role': 'ts.checker.original-typescript002-refusal-rebase'},
 'som-rs/scripts/react_output_acceptance/check_typescript_baseline.py': {'module': '_som_ts_native_original_typescript002_baseline',
                                                                         'role': 'ts.checker.original-typescript002-baseline'},
 'som-rs/scripts/rust_output_acceptance/check_cli.py': {'module': '_som_ts_native_original_cli',
                                                        'role': 'ts.checker.original-cli'},
 'som-rs/scripts/rust_output_acceptance/check_rust_unchanged.py': {'module': '_som_ts_native_original_rust_unchanged',
                                                                   'role': 'ts.checker.original-rust-unchanged'},
 'som-rs/scripts/rust_output_acceptance/python_replay.py': {'module': '_som_ts_native_original_python_replay',
                                                            'role': 'ts.checker.original-python-replay'},
 'som-rs/scripts/rust_output_acceptance/run_acceptance.py': {'module': '_som_ts_native_original_rust_acceptance',
                                                             'role': 'ts.checker.original-rust-acceptance'},
 'som-rs/scripts/typescript_output_acceptance/check_bundles.py': {'module': '_som_ts_native_original_bundles',
                                                                  'role': 'ts.checker.original-bundles'},
 'som-rs/scripts/typescript_output_acceptance/check_python_baseline.py': {'module': '_som_ts_native_original_python_baseline',
                                                                          'role': 'ts.checker.original-python-baseline'},
 'som-rs/scripts/typescript_output_acceptance/check_rt_diagnostics.py': {'module': '_som_ts_native_original_rt_diagnostics',
                                                                         'role': 'ts.checker.original-rt-diagnostics'},
 'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py': {'module': '_som_ts_native_original_rust_baseline',
                                                                        'role': 'ts.checker.original-rust-baseline'},
 'som-rs/scripts/typescript_output_acceptance/check_target.py': {'module': '_som_ts_native_original_target',
                                                                 'role': 'ts.checker.original-target'},
 'som-rs/som-targets/qa/check_rust_products.py': {'module': '_som_ts_native_original_rust_products',
                                                  'role': 'ts.checker.original-rust-products'},
 'som-rs/som-targets/qa/check_ts_products.py': {'module': '_som_ts_native_original_ts_products',
                                                'role': 'ts.checker.original-ts-products'},
 'som-rs/tests/rust-output-v1/check_python_controls.py': {'module': '_som_ts_native_original_rust_python_controls',
                                                          'role': 'ts.checker.original-rust-python-controls'},
 'som-rs/tests/rust-output-v1/validate_cases.py': {'module': '_som_ts_native_original_validate_cases',
                                                   'role': 'ts.checker.original-validate-cases'},
 'som-rs/tests/typescript-output-v1/check_python_controls.py': {'module': '_som_ts_native_original_ts_python_controls',
                                                                'role': 'ts.checker.original-ts-python-controls'}}
_ABSENT = object()
_ADAPTER_NAMES = frozenset(('cluster_gates', 'LEGACY', '_run_cli_with_request_capture', 'check',
    'asset_only_difference', 'http_send', 'psycopg', 'schema'))


def _native_context(hosted):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    require(type(hosted) is ModuleType and hosted is __som_native_ContextModule__
            and hosted.context is __som_native_context_getter__
            and hosted.Context is __som_native_Context__ and hosted.SourceBytes is __som_native_SourceBytes__
            and controls.role_sources.get('entry.qa.typescript-wrapper') == SELF
            and controls.role_sources.get('runtime.context') == CONTEXT,
            'actual fixed native Context module/getter/classes required')
    ctx = hosted.context()
    require(type(ctx) is __som_native_Context__, 'actual source-owned native Context instance required')
    require(type(ctx.repo) is type(controls.root) and ctx.repo == controls.root
            and ctx.helper_revision == controls.helper_revision == ctx.candidate_revision == controls.candidate_revision,
            'native checker Context root/revision differs')
    require(type(ctx.sources) is __som_native_SourceBytes__ and ctx.sources.root == controls.root
            and ctx.sources.revision == controls.helper_revision, 'actual checker SourceBytes owner required')
    for owner, name, cls in ((ctx, 'module', __som_native_Context__), (ctx, 'verify', __som_native_Context__),
                             (ctx.sources, 'read', __som_native_SourceBytes__),
                             (ctx.sources, 'verify', __som_native_SourceBytes__)):
        method = getattr(owner, name)
        require(type(method) is MethodType and method.__self__ is owner and method.__func__ is getattr(cls, name),
                'native checker bound source method replaced: ' + name)
    return ctx, controls


def _target(lease, ctx, tool, raw):
    require(type(tool) is str and tool in _TARGETS, 'unlisted native original checker source')
    item = _TARGETS[tool]
    require(lease.role_sources.get(item['role']) == tool, 'fixed native checker target role source differs')
    name = item['module']
    require(name not in sys.modules, 'preloaded original TypeScript checker refused')
    target = lease.load_module(item['role'])
    require(type(target) is ModuleType and target.__name__ == name and sys.modules.get(name) is target
            and target.__file__ == str(ctx.repo / tool) and target.__cached__ is None
            and target.__som_raw_sha256__ == sha(raw), 'actual Core original checker identity differs')
    return target


def _checked_native_host(ctx, controls, host):
    controls.verify_installed()
    require(type(host) is ModuleType
            and controls.context_keys.get('typescript_regression_native_host') == 'ctx.typescript-regression-native-host'
            and controls.role_sources.get('ctx.typescript-regression-native-host') == NATIVE_HOST
            and host.__name__ == '_som_full_typescript_regression_native_host'
            and host.__file__ == str(ctx.repo / NATIVE_HOST) and host.__cached__ is None
            and sys.modules.get(host.__name__) is host
            and host.__som_raw_sha256__ == sha(ctx.sources.read(NATIVE_HOST)),
            'actual fixed native TypeScript host source/module required')
    for name in ('configure', 'close'):
        function = host.__dict__.get(name)
        require(type(function) is FunctionType and function.__globals__ is host.__dict__
                and function.__code__.co_filename == str(ctx.repo / NATIVE_HOST),
                'actual fixed native TypeScript host callable required: ' + name)
    return host


@contextmanager
def _owned_adapter(changes, owner, names):
    require(type(owner) is ModuleType and type(names) is tuple and set(names) <= _ADAPTER_NAMES,
            'fixed source-owned checker adapter fields required')
    namespace = ModuleType.__getattribute__(owner, '__dict__')
    rows = [(owner, name, namespace.get(name, _ABSENT)) for name in names]
    try:
        yield
    finally:
        for module, name, before in rows:
            changes.append((module, name, before, ModuleType.__getattribute__(module, '__dict__').get(name, _ABSENT)))


def _close_owned_adapters(changes):
    state = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
    for owner, name, original, installed in reversed(changes):
        try:
            namespace = ModuleType.__getattribute__(owner, '__dict__')
            require(namespace.get(name, _ABSENT) is installed, 'checker adapter replaced by later owner: ' + name)
            if original is _ABSENT:
                namespace.pop(name, None)
            else:
                namespace[name] = original
        except BaseException as error:
            remember_failure(state, 'checker_adapter_restore_' + name, error)
    changes.clear()
    raise_primary(state)


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


def native_bundle_expectations(ctx, target):
    """Derive only new native TC/formatter values from the owning source factory."""
    require(DIGESTS in ctx.qa_source_authority['admitted_repository_python_sources'],
            'native bundle expectation source is outside exact admission')
    digest = ctx.module('typescript_regression_native_bundle_digests', DIGESTS)
    _, _, _, expected = digest.authorities(ctx, 'typescript')
    original = target.compute_expected_ts_specific_descriptors
    def source_expected():
        values = copy.deepcopy(original())
        require(values['backend']['sha256'] == expected['backend'],
                'original TypeScript backend source vector differs from current owning factory')
        values['target_contract']['sha256'] = expected['target_contract']
        values['formatter']['sha256'] = expected['formatter']
        return values
    target.compute_expected_ts_specific_descriptors = source_expected
    target.__dict__.setdefault('__som_native_bundle_expectation_leases__', []).append((original, source_expected))
    return {'mode': 'native-qa-v1', 'target_contract_sha256': expected['target_contract'],
            'formatter_sha256': expected['formatter'], 'backend_sha256': expected['backend'],
            'product_acceptance_passed': False}


def close_native_bundle_expectations(target):
    leases = target.__dict__.get('__som_native_bundle_expectation_leases__', [])
    require(leases, 'owned native descriptor adapter lease required')
    original, installed = leases.pop()
    require(target.compute_expected_ts_specific_descriptors is installed,
            'native descriptor adapter replaced by a later owner')
    target.compute_expected_ts_specific_descriptors = original
    if not leases:
        target.__dict__.pop('__som_native_bundle_expectation_leases__', None)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write((json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode())


class Response(io.BytesIO):
    def __init__(self, raw, original):
        super().__init__(raw)
        self.status = self.code = original.getcode()
        self.headers, self.reason, self.url = original.headers, original.reason, original.geturl()

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def info(self):
        return self.headers


class HttpCapture:
    def __init__(self, root):
        self.root, self.rows, self.lock = root, [], threading.Lock()
        self.original = urllib.request.OpenerDirector.open
        self.pending, self.completed = 0, False

    def install(self):
        owner = self
        def capture(opener, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
            request = fullurl if isinstance(fullurl, urllib.request.Request) else urllib.request.Request(fullurl, data=data)
            url = request.full_url
            split = urllib.parse.urlsplit(url)
            require(split.scheme == 'http' and split.hostname in {'127.0.0.1', 'localhost', '::1'}
                    and split.port is not None and split.port not in {8765, 18765}
                    and not split.username and not split.password and not split.fragment,
                    'TypeScript checker HTTP must use an explicit owned QA loopback endpoint')
            body = request.data if request.data is not None else data
            require(body is None or isinstance(body, bytes), 'complete raw byte request required')
            body = body or b''
            with owner.lock:
                index = len(owner.rows); owner.rows.append(None); owner.pending += 1
            record = {'index': index, 'url': url, 'method': request.get_method(),
                      'request_sha256': sha(body), 'status': None, 'complete': False}
            directory = owner.root / f'{index:06d}'; directory.mkdir()
            (directory / 'request.raw').write_bytes(body)
            try:
                try:
                    original = owner.original(opener, fullurl, data=data, timeout=timeout)
                except urllib.error.HTTPError as error:
                    original = error
                raw = original.read(64 * 1024**2 + 1)
                require(len(raw) <= 64 * 1024**2, 'bounded complete checker HTTP response required')
                record.update(status=original.getcode(), response_sha256=sha(raw),
                              response_headers=list(original.headers.items()), complete=True)
                (directory / 'response.raw').write_bytes(raw)
                if isinstance(original, urllib.error.HTTPError):
                    replacement = urllib.error.HTTPError(original.geturl(), original.code, original.reason,
                                                         original.headers, io.BytesIO(raw))
                    original.close()
                    raise replacement
                replacement = Response(raw, original); original.close()
                return replacement
            except BaseException as error:
                if not record['complete']:
                    record['error'], record['reason'] = type(error).__name__, str(error)
                raise
            finally:
                with owner.lock:
                    owner.rows[index] = record; owner.pending -= 1
                save(directory / 'result.json', record)
        self.installed = capture
        urllib.request.OpenerDirector.open = capture

    def close(self):
        owned = urllib.request.OpenerDirector.open is self.installed
        if owned:
            urllib.request.OpenerDirector.open = self.original
        save(self.root / 'index.json', {'version': 'som.typescript-regression-raw-http.v1',
            'rows': self.rows, 'product_acceptance_passed': False})
        require(owned and self.pending == 0 and all(type(row) is dict for row in self.rows),
                'checker HTTP capture changed or worker did not finish')


def main(argv=None):
    import full_product_context as hosted
    ctx, controls = _native_context(hosted)
    require(ctx.qa_mode == 'native-qa-v1' and ctx.qa_source_authority['source_authority_active'] is True,
            'checked native Context before checker source required')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', required=True)
    parser.add_argument('--capture-root', type=Path, required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    allowed = ctx.qa_source_authority['admitted_repository_python_sources']
    require(args.tool in allowed and SELF in allowed and args.tool.endswith('.py')
            and all(part not in {'', '.', '..'} for part in args.tool.split('/')),
            'exact original checker entry must be admitted')
    require(args.tool in _TARGETS, 'unlisted native original checker source')
    raw = ctx.sources.read(args.tool)
    root = args.capture_root.absolute()
    require(ctx.work in root.parents and not root.exists()
            and not any(p.is_symlink() for p in root.parents), 'new Context-owned checker capture root required')
    root.mkdir(parents=True)
    target, target_lease, changes = None, None, []
    host, host_configure_attempted = None, False
    old_main, old_argv, old_path = sys.modules['__main__'], sys.argv, list(sys.path)
    require(RAW_CAPTURE in allowed, 'complete command/CLI/product raw capture source must be admitted')
    raw_capture = ctx.module('typescript_regression_raw_capture', RAW_CAPTURE)
    runs = raw_capture.RawRuns(root / 'raw-commands')
    require(raw_capture.CaptureRows is __som_native_CaptureRows__
            and raw_capture.CaptureOutcome is __som_native_CaptureOutcome__,
            'actual completed RawCapture JSON data classes required')
    http = HttpCapture(root); outcome = raw_capture.CaptureOutcome({'tool': args.tool, 'source_sha256': sha(raw),
        'entry_executed': False, 'exit_code': None, 'postcheck_complete': False, 'product_acceptance_passed': False})
    original = None
    try:
        http.install()
        sys.argv = [str(ctx.repo / args.tool), *(args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments)]
        outcome['entry_executed'] = True
        try:
            target_lease = controls.borrow()
            target = _target(target_lease, ctx, args.tool, raw)
            if args.tool == 'som-rs/scripts/typescript_output_acceptance/check_bundles.py':
                outcome['native_bundle_expectations'] = native_bundle_expectations(ctx, target)
            if args.tool in {'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py',
                             'som-rs/scripts/rust_output_acceptance/check_rust_unchanged.py'}:
                require(INPUT_ADAPTER in allowed, 'explicit Rust input adapter source must be admitted')
                inputs = ctx.module('typescript_regression_named_inputs', INPUT_ADAPTER)
                outcome['native_input_roots'] = inputs.configure(ctx, target)
            if args.tool == 'som-rs/scripts/rust_output_acceptance/run_acceptance.py':
                require(NESTED_RUST in allowed, 'nested original Rust capture source must be admitted')
                nested = ctx.module('typescript_regression_nested_rust', NESTED_RUST)
                with _owned_adapter(changes, target, ('cluster_gates',)):
                    outcome['nested_rust_capture'] = nested.install(ctx, target, root)
            if args.tool == 'som-rs/som-targets/qa/check_rust_products.py':
                with _owned_adapter(changes, target.targets, ('LEGACY',)):
                    target.targets.LEGACY = Path(ctx.data['typescript_regression_inputs']['roots']['legacy_run'])
            if args.tool == 'som-rs/som-targets/qa/check_ts_products.py':
                host_path = NATIVE_HOST
                require(host_path in allowed, 'native TypeScript product host must be source-admitted')
                host = ctx.module('typescript_regression_native_host', host_path)
                host = _checked_native_host(ctx, controls, host)
                host_configure_attempted = True
                outcome['native_typescript_tools'] = host.configure(ctx, target)
                with _owned_adapter(changes, target.ts_suite, ('http_send',)):
                    outcome['product_http_rows'] = raw_capture.install_product_http(target, root / 'product-http')
                require(SCHEMA_CAPTURE in allowed, 'actual schema capture source must be admitted')
                schema = ctx.module('typescript_regression_schema_capture', SCHEMA_CAPTURE)
                with _owned_adapter(changes, target.rust_products, ('psycopg', 'schema')):
                    outcome['schema_rows'] = schema.install(target, root / 'schema-raw')
            if args.tool == 'som-rs/scripts/rust_output_acceptance/check_cli.py':
                with _owned_adapter(changes, target, ('_run_cli_with_request_capture',)):
                    outcome['cli_rows'] = raw_capture.install_cli(target, root / 'cli-raw')
                original_check = target.check
                def checked_request(*values, **keywords):
                    if len(values) > 7:
                        values = (*values[:7], True, *values[8:])
                        keywords.pop('verify_request', None)
                    else:
                        keywords['verify_request'] = True
                    return original_check(*values, **keywords)
                with _owned_adapter(changes, target, ('check',)):
                    target.check = checked_request
            if args.tool == 'som-rs/scripts/rust_output_acceptance/python_replay.py':
                replay_digest = ctx.module('typescript_regression_native_replay_digests', DIGESTS)
                original_difference = target.asset_only_difference
                outcome['native_python_replay_proofs'] = []
                def current_difference(expected, current):
                    problem, proof = replay_digest.historical_python_replay(ctx, expected, current, original_difference)
                    outcome['native_python_replay_proofs'].append(proof)
                    return problem
                with _owned_adapter(changes, target, ('asset_only_difference',)):
                    target.asset_only_difference = current_difference
            require(callable(getattr(target, 'main', None)), 'original checker main entry required')
            require(type(target.main) is FunctionType and target.main.__globals__ is target.__dict__
                    and target.main.__code__.co_filename == str(ctx.repo / args.tool),
                    'original checker main code/globals differ')
            target_lease.verify_installed()
            # Source/bootstrap Git reads are admitted first. Native command
            # capture starts at the actual checker main, after all owning
            # digest factories are checked and cached in this Context.
            digest = ctx.module('typescript_regression_preloaded_digests', DIGESTS)
            for selected_target in ('python', 'rust', 'typescript'):
                digest.authorities(ctx, selected_target)
            runs.install()
            result = target.main()
            outcome['exit_code'] = 0 if result is None else result
            require(type(outcome['exit_code']) is int, 'original checker terminal code must be an integer')
            if outcome['exit_code'] != 0:
                original = SystemExit(outcome['exit_code'])
        except SystemExit as error:
            outcome['exit_code'] = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
            if outcome['exit_code'] != 0:
                original = error
        except BaseException as error:
            original = error; outcome['error'], outcome['reason'] = type(error).__name__, str(error)
    except BaseException as error:
        if original is None:
            original = error
    finally:
        state = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
        if original is not None:
            remember_failure(state, 'original_checker', original)
        cleanup_errors = []
        def close_input_bindings():
            if ctx.cache.get('typescript_regression_input_binding_closers'):
                ctx.module('typescript_regression_inputs', INPUT_ADAPTER).close(ctx)
        def close_bundle_adapter():
            if target is not None and target.__dict__.get('__som_native_bundle_expectation_leases__'):
                close_native_bundle_expectations(target)
        def close_native_host():
            if host_configure_attempted:
                require(args.tool == 'som-rs/som-targets/qa/check_ts_products.py' and target is not None,
                        'only the actual configured TypeScript product host may close')
                _checked_native_host(ctx, controls, host).close(ctx, target)
        for label, action in (('raw_commands', runs.close), ('raw_http', http.close),
                              ('input_bindings', close_input_bindings), ('bundle_adapter', close_bundle_adapter),
                              ('owned_adapters', lambda: _close_owned_adapters(changes)),
                              ('context', ctx.verify),
                              ('native_host', close_native_host),
                              ('native_target_ledger', lambda: target_lease.verify_installed() if target_lease is not None else None),
                              ('native_target_borrow', lambda: target_lease.close() if target_lease is not None else None)):
            try:
                action()
            except BaseException as error:
                cleanup_errors.append(remember_failure(state, label, error))
        outcome['postcheck_complete'] = not cleanup_errors
        if cleanup_errors:
            outcome['postcheck_failures'] = cleanup_errors
        sys.modules['__main__'], sys.argv, sys.path = old_main, old_argv, old_path
        try:
            outcome['bound_source_sha256'] = dict(sorted(ctx.sources.pins.items()))
        except BaseException as error:
            cleanup_errors.append(remember_failure(state, 'checker_source_inventory', error))
            outcome['postcheck_complete'] = False
        if state['primary'] is not None:
            outcome['primary_failure'] = state['failure']
        outcome['secondary_failures'] = state['secondary']
        try:
            save(root / 'checker-postcheck.json', outcome.value)
        except BaseException as error:
            cleanup_errors.append(remember_failure(state, 'checker_postcheck_evidence', error))
            outcome['postcheck_complete'] = False
    raise_primary(state)
    return outcome['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
