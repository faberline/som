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
from types import ModuleType
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
    ctx = hosted.context()
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
    raw = ctx.sources.read(args.tool)
    root = args.capture_root.absolute()
    require(ctx.work in root.parents and not root.exists()
            and not any(p.is_symlink() for p in root.parents), 'new Context-owned checker capture root required')
    root.mkdir(parents=True)
    name = '_som_typescript_original_checker_entry'
    require(name not in sys.modules, 'preloaded original TypeScript checker refused')
    target = ModuleType(name)
    target.__file__, target.__cached__, target.__package__ = str(ctx.repo / args.tool), None, ''
    target.__som_raw_sha256__ = sha(raw)
    old_main, old_argv, old_path = sys.modules['__main__'], sys.argv, list(sys.path)
    require(RAW_CAPTURE in allowed, 'complete command/CLI/product raw capture source must be admitted')
    raw_capture = ctx.module('typescript_regression_raw_capture', RAW_CAPTURE)
    runs = raw_capture.RawRuns(root / 'raw-commands')
    http = HttpCapture(root); outcome = {'tool': args.tool, 'source_sha256': sha(raw),
        'entry_executed': False, 'exit_code': None, 'postcheck_complete': False, 'product_acceptance_passed': False}
    original = None
    try:
        http.install()
        sys.modules[name] = target
        sys.argv = [target.__file__, *(args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments)]
        sys.path.insert(0, str((ctx.repo / args.tool).parent))
        outcome['entry_executed'] = True
        try:
            exec(compile(raw, target.__file__, 'exec', dont_inherit=True), target.__dict__)
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
                outcome['nested_rust_capture'] = nested.install(ctx, target, root)
            if args.tool == 'som-rs/som-targets/qa/check_rust_products.py':
                target.targets.LEGACY = Path(ctx.data['typescript_regression_inputs']['roots']['legacy_run'])
            if args.tool == 'som-rs/som-targets/qa/check_ts_products.py':
                host_path = BASE + 'typescript_native_host.py'
                require(host_path in allowed, 'native TypeScript product host must be source-admitted')
                host = ctx.module('typescript_regression_native_host', host_path)
                outcome['native_typescript_tools'] = host.configure(ctx, target)
                outcome['product_http_rows'] = raw_capture.install_product_http(target, root / 'product-http')
                require(SCHEMA_CAPTURE in allowed, 'actual schema capture source must be admitted')
                schema = ctx.module('typescript_regression_schema_capture', SCHEMA_CAPTURE)
                outcome['schema_rows'] = schema.install(target, root / 'schema-raw')
            if args.tool == 'som-rs/scripts/rust_output_acceptance/check_cli.py':
                outcome['cli_rows'] = raw_capture.install_cli(target, root / 'cli-raw')
                original_check = target.check
                def checked_request(*values, **keywords):
                    if len(values) > 7:
                        values = (*values[:7], True, *values[8:])
                        keywords.pop('verify_request', None)
                    else:
                        keywords['verify_request'] = True
                    return original_check(*values, **keywords)
                target.check = checked_request
            if args.tool == 'som-rs/scripts/rust_output_acceptance/python_replay.py':
                digest = ctx.module('typescript_regression_native_replay_digests', DIGESTS)
                original_difference = target.asset_only_difference
                outcome['native_python_replay_proofs'] = []
                def current_difference(expected, current):
                    problem, proof = digest.historical_python_replay(ctx, expected, current, original_difference)
                    outcome['native_python_replay_proofs'].append(proof)
                    return problem
                target.asset_only_difference = current_difference
            require(callable(getattr(target, 'main', None)), 'original checker main entry required')
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
            if target.__dict__.get('__som_native_bundle_expectation_leases__'):
                close_native_bundle_expectations(target)
        for label, action in (('raw_commands', runs.close), ('raw_http', http.close),
                              ('input_bindings', close_input_bindings), ('bundle_adapter', close_bundle_adapter),
                              ('context', ctx.verify)):
            try:
                action()
            except BaseException as error:
                cleanup_errors.append(remember_failure(state, label, error))
        outcome['postcheck_complete'] = not cleanup_errors
        if cleanup_errors:
            outcome['postcheck_failures'] = cleanup_errors
        sys.modules['__main__'], sys.argv, sys.path = old_main, old_argv, old_path
        sys.modules.pop(name, None)
        try:
            outcome['bound_source_sha256'] = dict(sorted(ctx.sources.pins.items()))
        except BaseException as error:
            cleanup_errors.append(remember_failure(state, 'checker_source_inventory', error))
            outcome['postcheck_complete'] = False
        if state['primary'] is not None:
            outcome['primary_failure'] = state['failure']
        outcome['secondary_failures'] = state['secondary']
        try:
            save(root / 'checker-postcheck.json', outcome)
        except BaseException as error:
            cleanup_errors.append(remember_failure(state, 'checker_postcheck_evidence', error))
            outcome['postcheck_complete'] = False
    raise_primary(state)
    return outcome['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
