#!/usr/bin/env python3
"""Keep raw HTTP evidence while an unchanged Python checker runs on GitHub.

Usage: record_http.py --out NEW_DIR -- SCRIPT ORIGINAL_ARGUMENTS
"""
from __future__ import annotations

import argparse
import abc
import ast
import _io
import hashlib
import http.client
import json
import os
from pathlib import Path
import runpy
import re
from types import ModuleType, FunctionType, TracebackType, CodeType, GetSetDescriptorType
import stat
import sys
import threading
import tempfile
import traceback
import urllib.parse
import urllib.request
import urllib.error
import urllib.response

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VERSION = "som.m6-http-recording.v1"
REFUSAL = "raw HTTP recording refused"
MAX_BODY = 128 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_CALLS = 20_000
ALLOWED_SCRIPTS = frozenset((
    "som-rs/scripts/typescript_output_acceptance/check_python_baseline.py",
    "som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py",
    "som-rs/tests/react-output-v1/check_python_controls.py",
    "som-rs/tests/rust-output-v1/check_python_controls.py",
    "som-rs/tests/typescript-output-v1/check_python_controls.py",
))
CHILD_ENV = frozenset((
    "HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "CI", "SDKROOT",
    "DEVELOPER_DIR", "CARGO_HOME", "RUSTUP_HOME", "GITHUB_ACTIONS",
    "RUNNER_ENVIRONMENT", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_TEMP",
    "CARGO_NET_OFFLINE", "PYTHONDONTWRITEBYTECODE", "SOM_TS_PRETTIER",
))


SELF = 'som-rs/scripts/react_output_acceptance/record_http.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
NATIVE_TARGETS = {
    'som-rs/scripts/typescript_output_acceptance/check_python_baseline.py': 'entry.m6.python-baseline',
    'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py': 'entry.m6.rust-baseline',
    'som-rs/tests/react-output-v1/check_python_controls.py': 'entry.m6.react-python-controls',
    'som-rs/tests/rust-output-v1/check_python_controls.py': 'entry.m6.rust-python-controls',
    'som-rs/tests/typescript-output-v1/check_python_controls.py': 'entry.m6.ts-python-controls',
}


def _native_need(value, message):
    if not value:
        raise ValueError(message)


def _native_route():
    if '__som_native_active_controls__' in globals():
        return True
    try:
        from _som_native_active_controls_binding import controls as _probe
    except ModuleNotFoundError as error:
        _native_need(error.name == '_som_native_active_controls_binding'
                     and '_som_native_active_controls' not in sys.modules,
                     'native HTTP binding is missing')
        return False
    raise ValueError('native HTTP binding is missing')


def _native_context():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    _native_need(controls.role_sources.get('entry.m6.record-http') == SELF
                 and controls.role_sources.get('m6.record-http.owner') == SELF,
                 'fixed HTTP source roles required')
    _native_need(type(__file__) is str and __file__ == str(controls.root / SELF)
                 and type(__som_raw_sha256__) is str,
                 'actual HTTP entry source required')
    _native_need(type(__som_native_context_module__) is ModuleType,
                 'actual fixed Context module required')
    namespace = ModuleType.__getattribute__(__som_native_context_module__, '__dict__')
    _native_need(namespace.get('Context') is __som_native_Context__
                 and namespace.get('SourceBytes') is __som_native_SourceBytes__
                 and namespace.get('context') is __som_native_context_getter__
                 and namespace.get('read') is __som_native_context_read__
                 and type(namespace.get('__file__')) is str
                 and namespace['__file__'] == str(controls.root / CONTEXT),
                 'fixed Context exports differ')
    ctx = namespace.get('_ACTIVE')
    _native_need(type(ctx) is __som_native_Context__, 'actual active native Context required')
    values = object.__getattribute__(ctx, '__dict__')
    _native_need(type(values) is dict and type(values.get('repo')) is type(controls.root)
                 and values['repo'] == controls.root and type(values.get('helper_revision')) is str
                 and type(values.get('candidate_revision')) is str
                 and values['helper_revision'] == values['candidate_revision'] == controls.helper_revision == controls.candidate_revision
                 and re.fullmatch('[0-9a-f]{40}', values['helper_revision']) is not None
                 and type(values.get('qa_mode')) is str and values['qa_mode'] == 'native-qa-v1',
                 'actual Context root/revisions/mode differ')
    _native_need(type(values.get('path')) is type(controls.root) and values['path'].is_absolute()
                 and type(values.get('raw')) is bytes and __som_native_context_read__(values['path']) == values['raw']
                 and os.environ.get('SOM_FULL_PRODUCT_CONTEXT') == str(values['path']),
                 'actual Context path/raw differs')
    sources = values.get('sources')
    _native_need(type(sources) is __som_native_SourceBytes__, 'actual SourceBytes class required')
    fields = object.__getattribute__(sources, '__dict__')
    _native_need(type(fields) is dict and set(fields) == {'root', 'revision', 'pins'}
                 and type(fields.get('root')) is type(controls.root) and fields['root'] == controls.root
                 and type(fields.get('revision')) is str and fields['revision'] == controls.helper_revision
                 and type(fields.get('pins')) is dict
                 and all(type(path) is str and type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin)
                         for path, pin in fields['pins'].items()), 'actual SourceBytes owner differs')
    lease = values.get('qa_import_controls')
    _native_need(type(lease) is type(controls), 'actual Context source lease required')
    lease.verify_installed()
    _native_need(lease.root == controls.root and lease.helper_revision == controls.helper_revision
                 and lease.candidate_revision == controls.candidate_revision, 'Context source lease differs')
    authority = values.get('qa_source_authority')
    _native_need(type(authority) is dict and authority.get('source_authority_active') is True
                 and type(authority.get('helper_revision')) is str and type(authority.get('candidate_revision')) is str
                 and authority['helper_revision'] == authority['candidate_revision'] == controls.helper_revision
                 and type(authority.get('admitted_repository_python_sources')) is list
                 and all(type(path) is str for path in authority['admitted_repository_python_sources'])
                 and all(authority.get(key) is False for key in ('product_acceptance_passed',
                          'compiler_acceptance_passed', 'whole_acceptance_complete')),
                 'actual native source authority required')
    _native_need(__som_native_context_getter__() is ctx, 'actual fixed Context getter changed owner')
    _native_need(type(__som_native_owner_module__) is ModuleType,
                 'actual completed HTTP source owner required')
    owner = ModuleType.__getattribute__(__som_native_owner_module__, '__dict__')
    _native_need(owner.get('__name__') == '_som_m6_record_http_owner'
                 and owner.get('__file__') == str(controls.root / SELF)
                 and owner.get('__som_raw_sha256__') == __som_raw_sha256__
                 and owner.get('NativeRecorder') is __som_native_Recorder__
                 and owner.get('NativeCall') is __som_native_Call__
                 and owner.get('RecordingError') is __som_native_RecordingError__,
                 'completed HTTP source classes differ')
    _native_need(type(__som_native_finish_call__) is FunctionType
                 and __som_native_finish_call__.__globals__ is owner
                 and __som_native_finish_call__.__code__.co_qualname == 'NativeCall.finish'
                 and __som_native_finish_call__.__code__.co_filename == str(controls.root / SELF),
                 'original HTTP finalizer source required')
    _native_need(type(REPO) is type(controls.root) and REPO == controls.root
                 and REPO.resolve() == REPO and not any(part.is_symlink() for part in (REPO, *REPO.parents)),
                 'same physical HTTP repository required')
    _native_need(sources.read(SELF) == __som_native_read_regular__(controls.root / SELF)
                 and __som_native_digest__(sources.read(SELF)) == __som_raw_sha256__, 'HTTP source differs from actual Git')
    return controls, sources, ctx, authority


def _native_execute_checker(role, arguments):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    _native_need(type(role) is str and role in NATIVE_TARGETS.values()
                 and type(arguments) is list and all(type(value) is str for value in arguments),
                 'fixed HTTP entry data required')
    return controls.execute_entry(role, arguments)


def _native_run_checker(out, script, arguments, qa_mode):
    controls, sources, ctx, authority = _native_context()
    _native_need(sys.platform == 'linux' and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
                 and os.environ.get('GITHUB_ACTIONS') == 'true'
                 and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', 'isolated GitHub Linux required')
    _native_need(type(qa_mode) is str and qa_mode == 'native-qa-v1', 'explicit native HTTP mode required')
    _native_need(type(out) in {str, type(controls.root)} and type(script) in {str, type(controls.root)}
                 and type(arguments) is list and all(type(value) is str for value in arguments),
                 'HTTP path and argv data required')
    root = controls.root
    target, output = Path(os.path.abspath(script)), Path(os.path.abspath(out))
    _native_need(target.is_relative_to(root) and target.relative_to(root).as_posix() in ALLOWED_SCRIPTS
                 and set(NATIVE_TARGETS) == set(ALLOWED_SCRIPTS), 'HTTP target outside exact original five')
    relative = target.relative_to(root).as_posix()
    role = NATIVE_TARGETS[relative]
    _native_need(controls.role_sources.get(role) == relative, 'HTTP target role/source differs')
    _native_need({SELF, CONTEXT, *NATIVE_TARGETS} <= set(authority['admitted_repository_python_sources']),
                 'complete HTTP source closure is not admitted')
    for path in (SELF, CONTEXT, *NATIVE_TARGETS):
        sources.read(path)
    source = __som_native_read_regular__(target)
    _native_need(source == sources.read(relative), 'HTTP target differs from actual Git')
    _native_need(output.is_relative_to(root / '.local') and not output.exists() and not output.is_symlink(),
                 'fresh owned HTTP output required')
    __som_native_physical__(output.parent)
    before_argv, before_path_object, before_path, before_env = sys.argv, sys.path, sys.path[:], dict(os.environ)
    _native_need(type(before_argv) is list and all(type(value) is str for value in before_argv),
                 'observed wrapper argv must be text')
    context_values = object.__getattribute__(ctx, '__dict__')
    context_path, context_raw = context_values['path'], context_values['raw']
    output.mkdir(mode=0o700)
    (output / 'calls').mkdir(mode=0o700)
    recorder = __som_native_Recorder__(output, (18978, 18979), initial_ctx=ctx)
    operations = __som_native_recording_operations__(recorder)
    finish = __som_native_finish_call__
    finish_state = (finish.__code__, finish.__globals__, finish.__defaults__, finish.__kwdefaults__, finish.__closure__)
    call_mro = type.__getattribute__(__som_native_Call__, '__mro__')
    call_namespaces = tuple((base, dict(type.__getattribute__(base, '__dict__'))) for base in call_mro)
    factory = type.__getattribute__(__som_native_Call__, '__dict__')['__init__']
    finalizer_codes = tuple(value for value in factory.__code__.co_consts
                            if type(value) is CodeType and value.co_name == 'finalize')
    _native_need(len(finalizer_codes) == 1, 'exact source-owned call finalizer code required')
    calls, recording_errors = recorder.calls, recorder.errors
    callback_failures, finalizations, finishers = operations[4:7]
    lease = controls.borrow()
    evidence = {'version': 'som.m6-http-native-source-execution.v1', 'role': role,
                'path': relative, 'sha256': __som_native_digest__(source), 'requested_checker_argv': [str(target), *arguments],
                'current_pid': os.getpid(), 'observed_wrapper_sys_argv': list(before_argv),
                'execute_entry_attempted': False, 'execute_entry_returned': False, 'returned_exit_code': None,
                'source_postcheck': False, 'secondary_errors': [],
                'nested_execution_list_join': 'pending separately reviewed collector verifier',
                'product_acceptance_passed': False}
    primary, primary_trace, code, installed, original_open = None, None, 1, None, None
    secondary = []
    def failure(phase, error, original_trace=None):
        nonlocal primary, primary_trace, code
        if primary is None:
            actual_trace = __som_native_trace__(error)
            primary, primary_trace = error, actual_trace if actual_trace is not None else original_trace
            evidence['first_error'] = {'phase': phase, **__som_native_error__(error)}
        elif error is not primary and not any(error is previous for previous in secondary):
            secondary.append(error)
            evidence['secondary_errors'].append({'phase': phase, **__som_native_error__(error)})
        code = 1
    consumed_failures = 0
    def callback_errors(escaping=None):
        nonlocal consumed_failures
        malformed = []
        while consumed_failures < len(callback_failures):
            row = callback_failures[consumed_failures]
            consumed_failures += 1
            if (type(row) is not tuple or len(row) != 4 or type(row[0]) is not str
                    or row[2] is not None and type(row[2]) is not TracebackType):
                malformed.append(ValueError('original callback ledger row changed'))
                continue
            try:
                __som_native_trace__(row[1])
                if row[3] is not None:
                    __som_native_trace__(row[3])
            except TypeError:
                malformed.append(ValueError('original callback ledger error type changed'))
                continue
            phase, actual, actual_trace, extra = row
            failure('http-callback:' + phase, actual, actual_trace)
            if extra is not None:
                failure('http-callback-secondary:' + phase, extra)
        if malformed and primary is None and escaping is not None:
            failure('checker', escaping)
        for error in malformed:
            failure('http-callback-ledger', error)
    try:
        lease.verify_installed()
        installed = operations[1]()
        original_open = operations[7]
        evidence['execute_entry_attempted'] = True
        code = _native_execute_checker(role, arguments)
        evidence['execute_entry_returned'] = True
        evidence['returned_exit_code'] = code
    except BaseException as error:
        callback_errors(error)
        failure('checker', error)
    finally:
        callback_errors()
        if installed is not None:
            try:
                _native_need(urllib.request.AbstractHTTPHandler.do_open is installed, 'owned HTTP recording hook changed')
                urllib.request.AbstractHTTPHandler.do_open = original_open
            except BaseException as error:
                failure('http-hook-restore', error)
        # Finish every actual recorded call, even if another call cannot be saved.
        for row in finishers:
            try:
                _native_need(type(row) is tuple and len(row) == 3, 'owned finalizer row changed')
                call, captured_finalize, captured_state = row
                _native_need(type(captured_finalize) is FunctionType and type(captured_state) is tuple
                     and len(captured_state) == 5 and captured_finalize.__code__ is finalizer_codes[0]
                     and captured_finalize.__globals__ is factory.__globals__
                     and all(current is original for current, original in zip(
                         (captured_finalize.__code__, captured_finalize.__globals__, captured_finalize.__defaults__,
                          captured_finalize.__kwdefaults__, captured_finalize.__closure__), captured_state)),
                     'captured source-owned finalizer bindings changed')
                _native_need(type(call) is __som_native_Call__ and type(finish) is FunctionType
                     and type.__getattribute__(__som_native_Call__, '__mro__') == call_mro
                     and all(len(type.__getattribute__(base, '__dict__')) == len(expected)
                             and all(type.__getattribute__(base, '__dict__').get(key) is value
                                     for key, value in expected.items()) for base, expected in call_namespaces)
                     and all(current is original for current, original in zip(
                         (finish.__code__, finish.__globals__, finish.__defaults__, finish.__kwdefaults__, finish.__closure__), finish_state)),
                     'original HTTP finalizer identity changed')
                captured_finalize()
            except BaseException as error:
                failure('finish-http-call', error)
        callback_errors()
        evidence['raw_finalizations'] = list(finalizations)
        try:
            operations[2]()
        except BaseException as error:
            failure('response-exit-restore', error)
        evidence['response_exit_scope'] = list(operations[8])
        def process_state():
            _native_need(sys.argv is before_argv and sys.path is before_path_object and sys.path == before_path
                         and dict(os.environ) == before_env, 'nested checker changed wrapper process state')
        def restore_process_state():
            sys.argv = before_argv
            sys.path = before_path_object
            sys.path[:] = before_path
            os.environ.clear()
            os.environ.update(before_env)
        def same_owner():
            actual, actual_sources, actual_ctx, actual_authority = _native_context()
            _native_need(actual is controls and actual_sources is sources and actual_ctx is ctx
                         and actual_authority is authority, 'HTTP native owner changed')
        def target_source():
            controls.verify_installed()
            _native_need(__som_native_read_regular__(target) == source == sources.read(relative), 'HTTP target source changed')
        def context_source():
            controls.verify_installed()
            values = object.__getattribute__(ctx, '__dict__')
            _native_need(values.get('path') == context_path and values.get('raw') == context_raw
                         and __som_native_context_read__(context_path) == context_raw, 'HTTP Context raw changed')
        def source_postcheck():
            controls.verify_installed()
            sources.verify()
        for phase, action in (('process-state', process_state), ('process-state-restore', restore_process_state),
             ('target-source', target_source), ('context-raw', context_source),
             ('source-postcheck', source_postcheck), ('same-owner', same_owner),
             ('controls-postcheck', lease.verify_installed), ('recording-release', operations[3]), ('borrow-close', lease.close)):
            try:
                action()
            except BaseException as error:
                failure(phase, error)
        evidence['source_postcheck'] = primary is None
        if primary is not None:
            recording_errors.append('recording_failed')
        try:
            manifest = {'version': VERSION, 'script': {'path': relative, 'sha256': __som_native_digest__(source)},
                        'argv': [str(target), *arguments], 'exit_code': code,
                        'qa_mode': 'native-qa-v1', 'ports': [18978, 18979], 'source_route': 'checked-git-bytes',
                        'calls': [object.__getattribute__(call, '__dict__')['record']
                                  for call in calls if type(call) is __som_native_Call__], 'recording_errors': recording_errors,
                        'native_source_execution': evidence}
            __som_native_write_new__(output / 'manifest.json', (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode())
        except BaseException as error:
            failure('manifest-write', error)
        if primary is not None:
            try:
                traceback.print_tb(primary_trace, file=sys.stderr)
                print(__som_native_error__(primary)['type'], file=sys.stderr)
            except BaseException as error:
                failure('primary-traceback-write', error)
            __som_native_secondary__(primary, '__som_native_http_secondary__', secondary)
            raise BaseException.with_traceback(primary, primary_trace)
    return code if not recording_errors else 1




class RecordingError(ValueError):
    def __init__(self):
        super().__init__(REFUSAL)


def need(condition):
    if not condition:
        raise RecordingError()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def physical(directory: Path):
    need(directory.is_absolute() and str(directory) == os.path.abspath(directory))
    for item in (*reversed(directory.parents), directory):
        info = item.lstat()
        need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))


def read_regular(filename: Path):
    physical(filename.parent)
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BODY)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BODY + 1)
        need(len(raw) == info.st_size and len(raw) <= MAX_BODY)
        return raw
    finally:
        os.close(descriptor)


def write_new(filename: Path, raw: bytes):
    physical(filename.parent)
    descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        os.fchmod(stream.fileno(), 0o600)


def safe_error(error):
    reason = getattr(error, "reason", error)
    number = getattr(reason, "errno", None)
    return {"type": type(error).__name__, "errno": number if type(number) is int else None}


def request_identity(request, ports=(18917, 18919)):
    url = request.full_url
    need(type(url) is str)
    parsed = urllib.parse.urlsplit(url)
    need(parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1", "localhost"))
    need(ports in ((18917, 18919), (18978, 18979))
         and parsed.port in ports and parsed.username is None and parsed.password is None)
    need(not parsed.query and not parsed.fragment)
    method = request.get_method()
    need((method == "POST" and parsed.path in ("/v1/compile", "/v1/assemble"))
         or (method == "GET" and parsed.path == "/healthz"))
    body = request.data
    need(body is None or type(body) is bytes)
    raw = b"" if body is None else body
    need(len(raw) <= MAX_BODY)
    return method, url, raw


class Call:
    def __init__(self, recorder, identity, number):
        method, url, request = identity
        self.recorder = recorder
        self.lock = threading.RLock()
        self.response = bytearray()
        self.done = False
        self.record = {
            "id": number, "method": method, "url": url,
            "request_file": f"calls/{number:06d}.request.raw", "request_sha256": digest(request),
            "status": None, "response_file": f"calls/{number:06d}.response.raw",
            "response_sha256": None, "error": None, "complete": False,
        }
        recorder.account(len(request))
        write_new(recorder.out / self.record["request_file"], request)

    def append(self, chunk):
        need(type(chunk) is bytes)
        with self.lock:
            if self.done:
                need(not chunk)
                return
            need(len(self.response) + len(chunk) <= MAX_BODY)
            self.recorder.account(len(chunk))
            self.response.extend(chunk)

    def finish(self, complete=False, error=None):
        with self.lock:
            if self.done:
                return
            self.done = True
            self.record["complete"] = complete is True
            self.record["error"] = error
            raw = bytes(self.response)
            self.record["response_sha256"] = digest(raw)
            write_new(self.recorder.out / self.record["response_file"], raw)
            write_new(self.recorder.out / f"calls/{self.record['id']:06d}.json",
                      (json.dumps(self.record, indent=2, sort_keys=True) + "\n").encode())

    def attach(self, response):
        """Keep the actual HTTPResponse object, including its HTTPError behavior."""
        need(type(response.status) is int)
        self.record["status"] = response.status
        original_read, original_close = response.read, response.close

        def read(*args, **kwargs):
            try:
                raw = original_read(*args, **kwargs)
                self.append(raw)
                amount = args[0] if args else kwargs.get("amt")
                complete = (amount is None or (type(amount) is int and amount < 0)
                            or (amount != 0 and (not raw or getattr(response, "length", None) == 0)))
                if complete:
                    self.finish(complete=True)
                return raw
            except BaseException as error:
                self.finish(error=safe_error(error))
                raise

        def close():
            try:
                return original_close()
            finally:
                self.finish()

        response.read, response.close = read, close
        return response


class Recorder:
    def __init__(self, out: Path, ports=(18917, 18919)):
        self.out, self.ports = out, ports
        self.lock = threading.RLock()
        self.calls = []
        self.errors = []
        self.total_bytes = 0
        self.original_open = None

    def account(self, size):
        with self.lock:
            need(self.total_bytes + size <= MAX_TOTAL)
            self.total_bytes += size

    def open(self, handler, http_class, request, **kwargs):
        try:
            identity = request_identity(request, self.ports)
            with self.lock:
                need(len(self.calls) < MAX_CALLS)
                call = Call(self, identity, len(self.calls) + 1)
                self.calls.append(call)
        except RecordingError:
            with self.lock:
                self.errors.append("request_refused")
            raise
        try:
            response = self.original_open(handler, http_class, request, **kwargs)
        except BaseException as error:
            call.finish(error=safe_error(error))
            raise
        try:
            return call.attach(response)
        except BaseException as error:
            call.finish(error=safe_error(error))
            response.close()
            raise

    def __enter__(self):
        self.original_open = urllib.request.AbstractHTTPHandler.do_open
        recorder = self

        def recording_open(handler, http_class, request, **kwargs):
            return recorder.open(handler, http_class, request, **kwargs)

        urllib.request.AbstractHTTPHandler.do_open = recording_open
        return self

    def __exit__(self, *_exception):
        urllib.request.AbstractHTTPHandler.do_open = self.original_open
        for call in self.calls:
            call.finish()


def _native_error(error):
    """Do not read an exception instance, reason, errno, or string hook."""
    kind = type(error)
    name = type.__getattribute__(kind, '__name__') if type(kind) is type else 'BaseException'
    return {'type': name if type(name) is str and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', name) else 'BaseException',
            'errno': None}


def _native_trace(error):
    return BaseException.__dict__['__traceback__'].__get__(error, BaseException)


def _native_is_error(value):
    try:
        _native_trace(value)
        return True
    except TypeError:
        return False


def _native_secondary(primary, name, values):
    namespace = BaseException.__dict__['__dict__'].__get__(primary, BaseException)
    dict.__setitem__(namespace, name, tuple(values))


def _native_code_equal(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is CodeType:
        fields = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals', 'co_stacksize',
                  'co_flags', 'co_code', 'co_consts', 'co_names', 'co_varnames', 'co_filename', 'co_name',
                  'co_qualname', 'co_firstlineno', 'co_linetable', 'co_exceptiontable', 'co_freevars', 'co_cellvars')
        return all(_native_code_equal(getattr(left, key), getattr(right, key)) for key in fields)
    if type(left) is tuple:
        return len(left) == len(right) and all(_native_code_equal(a, b) for a, b in zip(left, right))
    if type(left) is frozenset:
        atoms = (str, bytes, int, float, complex, bool, type(None))
        return (all(any(type(item) is accepted for accepted in atoms) for item in left)
                and all(any(type(item) is accepted for accepted in atoms) for item in right) and left == right)
    if any(type(left) is accepted for accepted in (str, bytes, int, float, complex, bool, type(None))):
        return left == right
    return False


def _native_stdlib_class(kind, module, raw, source_path):
    """Compile only two fixed stdlib files for code comparison; execute neither."""
    namespace = ModuleType.__getattribute__(module, '__dict__')
    name = type.__getattribute__(kind, '__name__')
    _native_need((module is http.client and kind is http.client.HTTPResponse and name == 'HTTPResponse')
                 or (module is urllib.response and kind is urllib.response.addbase and name == 'addbase'),
                 'unlisted stdlib class validation')
    tree = ast.parse(raw)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
    _native_need(len(classes) == 1, 'original stdlib class source differs')
    code = compile(raw, str(source_path), 'exec', dont_inherit=True)
    class_codes = [value for value in code.co_consts if type(value) is CodeType and value.co_name == name]
    _native_need(len(class_codes) == 1, 'original stdlib class code differs')
    class_values = type.__getattribute__(kind, '__dict__')
    declared = {node.name for node in classes[0].body if isinstance(node, ast.FunctionDef)}
    for node in classes[0].body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            declared.update(target.id for target in targets if isinstance(target, ast.Name))
    metadata = {'__module__', '__qualname__', '__doc__', '__dict__', '__weakref__', '__abstractmethods__', '_abc_impl'}
    _native_need(all(type(key) is str and key in declared | metadata for key in class_values)
                 and type(class_values.get('__module__')) is str and type(namespace.get('__name__')) is str
                 and class_values['__module__'] == namespace['__name__'],
                 'preloaded foreign response class member refused')
    for key in ('__dict__', '__weakref__'):
        if key in class_values:
            value = class_values[key]
            _native_need(type(value) is GetSetDescriptorType and value.__objclass__ is kind and value.__name__ == key,
                         'original response instance descriptor differs')
    _native_need(_native_code_equal(class_values.get('__doc__'), ast.get_docstring(classes[0], clean=False))
                 and ('__qualname__' not in class_values or type(class_values['__qualname__']) is str
                      and class_values['__qualname__'] == name)
                 and ('__abstractmethods__' not in class_values or type(class_values['__abstractmethods__']) is frozenset
                      and not class_values['__abstractmethods__']), 'original response class metadata differs')
    for node in classes[0].body:
        if not isinstance(node, ast.FunctionDef):
            continue
        _native_need(not node.decorator_list, 'unexpected decorated response method')
        function = class_values.get(node.name)
        originals = [value for value in class_codes[0].co_consts if type(value) is CodeType and value.co_name == node.name]
        _native_need(type(function) is FunctionType and function.__globals__ is namespace
                     and len(originals) == 1 and _native_code_equal(function.__code__, originals[0]),
                     'original stdlib response method code/globals differ')
        defaults = tuple(ast.literal_eval(value) for value in node.args.defaults) or None
        kwdefaults = {arg.arg: ast.literal_eval(value) for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults)
                      if value is not None} or None
        _native_need(_native_code_equal(function.__defaults__, defaults)
                     and (function.__kwdefaults__ is None and kwdefaults is None
                          or type(function.__kwdefaults__) is dict and type(kwdefaults) is dict
                          and all(type(key) is str for key in function.__kwdefaults__)
                          and set(function.__kwdefaults__) == set(kwdefaults)
                          and all(_native_code_equal(function.__kwdefaults__[key], value) for key, value in kwdefaults.items())),
                     'original stdlib response method defaults differ')
        cells = function.__closure__ or ()
        _native_need(len(cells) == len(function.__code__.co_freevars)
                     and all(key == '__class__' and cell.cell_contents is kind
                             for key, cell in zip(function.__code__.co_freevars, cells)),
                     'original stdlib response method closure differs')
    own_exit = any(isinstance(node, ast.FunctionDef) and node.name == '__exit__' for node in classes[0].body)
    _native_need(('__exit__' in class_values) is own_exit, 'preloaded foreign response exit refused')
    if not own_exit:
        inherited = next(type.__getattribute__(base, '__dict__')['__exit__']
                         for base in type.__getattribute__(kind, '__mro__')
                         if '__exit__' in type.__getattribute__(base, '__dict__'))
        _native_need(inherited is _io._IOBase.__dict__['__exit__'], 'original builtin response exit differs')


def _native_callback_guard():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    _native_need(__name__ == '_som_m6_record_http_owner'
                 and controls.role_sources.get('m6.record-http.owner') == SELF
                 and __file__ == str(controls.root / SELF), 'actual completed HTTP callback owner required')
    _native_need(type(__som_native_context_module__) is ModuleType, 'fixed callback Context module required')
    namespace = ModuleType.__getattribute__(__som_native_context_module__, '__dict__')
    _native_need(namespace.get('Context') is __som_native_Context__
                 and namespace.get('SourceBytes') is __som_native_SourceBytes__
                 and namespace.get('context') is __som_native_context_getter__
                 and namespace.get('read') is __som_native_context_read__, 'callback Context exports differ')
    ctx = namespace.get('_ACTIVE')
    _native_need(type(ctx) is __som_native_Context__, 'actual callback Context required')
    values = object.__getattribute__(ctx, '__dict__')
    _native_need(type(values) is dict and type(values.get('repo')) is type(controls.root)
                 and values['repo'] == controls.root and values.get('qa_mode') == 'native-qa-v1'
                 and type(values.get('helper_revision')) is str and type(values.get('candidate_revision')) is str
                 and values['helper_revision'] == values['candidate_revision'] == controls.helper_revision == controls.candidate_revision
                 and type(values.get('sources')) is __som_native_SourceBytes__
                 and __som_native_context_getter__() is ctx, 'callback active owner differs')
    lease = values.get('qa_import_controls')
    _native_need(type(lease) is type(controls), 'actual callback Context source lease required')
    lease.verify_installed()
    _native_need(lease.root == controls.root and lease.helper_revision == controls.helper_revision
                 and lease.candidate_revision == controls.candidate_revision, 'callback source lease differs')
    source_values = object.__getattribute__(values['sources'], '__dict__')
    _native_need(type(source_values) is dict and type(source_values.get('root')) is type(controls.root)
                 and source_values['root'] == controls.root and type(source_values.get('revision')) is str
                 and source_values['revision'] == controls.helper_revision and type(source_values.get('pins')) is dict
                 and all(type(name) is str and type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin)
                         for name, pin in source_values['pins'].items()), 'callback SourceBytes owner differs')
    path, raw, authority = values.get('path'), values.get('raw'), values.get('qa_source_authority')
    _native_need(type(path) is type(controls.root) and type(raw) is bytes
                 and __som_native_context_read__(path) == raw
                 and os.environ.get('SOM_FULL_PRODUCT_CONTEXT') == str(path)
                 and type(authority) is dict and authority.get('source_authority_active') is True
                 and authority.get('helper_revision') == authority.get('candidate_revision') == controls.helper_revision
                 and all(authority.get(key) is False for key in ('product_acceptance_passed',
                     'compiler_acceptance_passed', 'whole_acceptance_complete')),
                 'callback actual Context bytes or source authority differ')
    return controls, ctx, values['sources'], lease, authority, path, raw


def _native_call_callbacks(call):
    _native_callback_guard()
    _native_need(type(call) is NativeCall, 'actual source-owned call required')
    values = object.__getattribute__(call, '__dict__')
    callbacks = dict.get(values, '_native_callbacks')
    factory = type.__getattribute__(NativeCall, '__dict__')['__init__']
    _native_need(type(callbacks) is tuple and len(callbacks) == 3, 'actual call callback tuple differs')
    for function, name in zip(callbacks, ('finalize', 'after_error', 'attach')):
        originals = tuple(value for value in factory.__code__.co_consts if type(value) is CodeType and value.co_name == name)
        _native_need(type(function) is FunctionType and len(originals) == 1
                     and function.__code__ is originals[0] and function.__globals__ is factory.__globals__,
                     'actual call callback code/globals differ')
    return callbacks


class NativeCall(Call):
    """Callbacks retain source-owned operations before any external operation."""
    def __init__(self, recorder, identity, number, *, recording):
        guard, failure, finalization, register = recording
        guard()
        super().__init__(recorder, identity, number)
        fields = object.__getattribute__(self, '__dict__')
        expected = {name: fields[name] for name in ('recorder', 'lock', 'response', 'record')}
        callbacks = None
        exits = []
        def call_guard():
            guard()
            current = object.__getattribute__(self, '__dict__')
            _native_need(type(current) is dict and all(type(key) is str for key in current)
                         and set(current) == set(expected) | {'done', '_native_callbacks'}
                         and all(current.get(key) is value for key, value in expected.items())
                         and type(current.get('done')) is bool and current.get('_native_callbacks') is callbacks,
                         'original HTTP call fields changed')
        def finalize(complete=False, error=None, *, primary=None, phase='raw-finalize'):
            attempted = False
            try:
                call_guard()
                if self.done:
                    return None
                attempted = True
                super(NativeCall, self).finish(complete, error)
            except BaseException as later:
                finalization(number, False, later, attempted)
                actual = later if primary is None else primary
                failure(phase, actual, None if actual is later else later)
                if primary is None:
                    raise
                return later
            finalization(number, True, None, True)
            return None
        def after_error(primary, phase, *, observed_error=None):
            try:
                call_guard()
                metadata = _native_error(primary if observed_error is None else observed_error)
            except BaseException as later:
                finalization(number, False, later, False)
                failure(phase, primary, later)
                return
            finalize(error=metadata, primary=primary, phase=phase)
        def attach(response):
            call_guard()
            need(type(response.status) is int)
            self.record['status'] = response.status
            original_read, original_close = response.read, response.close
            def read(*args, **kwargs):
                try:
                    call_guard()
                    raw = original_read(*args, **kwargs)
                    call_guard()
                    self.append(raw)
                    amount = args[0] if args else kwargs.get('amt')
                    complete = (amount is None or (type(amount) is int and amount < 0)
                                or (amount != 0 and (not raw or getattr(response, 'length', None) == 0)))
                    if complete:
                        finalize(complete=True)
                    return raw
                except BaseException as primary:
                    after_error(primary, 'read-finalize')
                    raise
            def close():
                # Only the explicit owned __exit__ scope supplies a body error.
                token = exits[-1] if exits and exits[-1][0] == threading.get_ident() else None
                pending, pending_trace = (token[1], token[2]) if token is not None else (None, None)
                try:
                    call_guard()
                except BaseException as guard_error:
                    if pending is None:
                        raise
                    failure('close-source-guard', pending, guard_error)
                    raise BaseException.with_traceback(pending, pending_trace)
                try:
                    result = original_close()
                except BaseException as close_error:
                    primary = close_error if pending is None else pending
                    trace = _native_trace(primary)
                    if primary is not close_error:
                        failure('response-close', primary, close_error)
                    after_error(primary, 'close-finalize', observed_error=close_error)
                    raise BaseException.with_traceback(primary, trace)
                if pending is None:
                    finalize()
                else:
                    later = finalize(primary=pending, phase='close-finalize')
                    if later is not None:
                        raise BaseException.with_traceback(pending, pending_trace)
                return result
            register(response, call_guard, exits)
            response.read, response.close = read, close
            return response
        callbacks = (finalize, after_error, attach)
        self._native_callbacks = callbacks

    def _finalize(self, complete=False, error=None, *, primary=None, phase='raw-finalize'):
        return _native_call_callbacks(self)[0](complete, error, primary=primary, phase=phase)

    def _after_error(self, primary, phase, *, observed_error=None):
        return _native_call_callbacks(self)[1](primary, phase, observed_error=observed_error)

    def finish(self, complete=False, error=None):
        return _native_call_callbacks(self)[0](complete, error)

    def attach(self, response):
        return _native_call_callbacks(self)[2](response)


def _native_recording_operations(recorder):
    _native_callback_guard()
    _native_need(type(recorder) is NativeRecorder, 'actual source-owned recorder required')
    values = object.__getattribute__(recorder, '__dict__')
    operations = dict.get(values, '_native_operations')
    factory = type.__getattribute__(NativeRecorder, '__dict__')['__init__']
    _native_need(type(operations) is tuple and len(operations) == 9, 'actual recorder operation tuple differs')
    for function, name in zip(operations[:4], ('guard', 'enter', 'restore_exits', 'release')):
        originals = tuple(value for value in factory.__code__.co_consts if type(value) is CodeType and value.co_name == name)
        _native_need(type(function) is FunctionType and len(originals) == 1
                     and function.__code__ is originals[0] and function.__globals__ is factory.__globals__,
                     'actual recorder operation code/globals differ')
    return operations


class NativeRecorder(Recorder):
    """All captured ledgers and functions remain local to this recording."""
    def __init__(self, out, ports=(18917, 18919), *, initial_ctx):
        initial = _native_callback_guard()
        _native_need(initial[1] is initial_ctx, 'original HTTP Context pointer differs')
        super().__init__(out, ports)
        failures, finalizations, finishers, registered, patches, exit_events, wrapper_snapshots = [], [], [], [], [], [], []
        self.native_failures, self.native_finalizations = failures, finalizations
        fields = object.__getattribute__(self, '__dict__')
        expected = {name: fields[name] for name in ('out', 'ports', 'lock', 'calls', 'errors', 'native_failures', 'native_finalizations')}
        calls, errors = self.calls, self.errors
        original_open = urllib.request.AbstractHTTPHandler.do_open
        http_error_kind, closer_kind = urllib.error.HTTPError, tempfile._TemporaryFileCloser
        installing_thread, active, installed = threading.get_ident(), True, False
        operations = None
        def guard():
            current = _native_callback_guard()
            _native_need(all(left is right for left, right in zip(current[:5], initial[:5]))
                         and current[5] == initial[5] and current[6] is initial[6], 'original HTTP owner pointer changed')
            values = object.__getattribute__(self, '__dict__')
            _native_need(active and type(values) is dict and all(type(key) is str for key in values)
                         and set(values) == set(expected) | {'total_bytes', 'original_open', '_native_operations'}
                         and all(values.get(key) is value for key, value in expected.items())
                         and type(values.get('total_bytes')) is int
                         and (values.get('original_open') is None or values.get('original_open') is original_open)
                         and values.get('_native_operations') is operations,
                         'original HTTP recorder fields changed')
            _native_need(all(type(value) is list for value in (failures, finalizations, finishers, registered, patches, calls, errors)),
                         'original HTTP ledger type differs')
            if installed:
                verify_exits()
        def failure(phase, primary, secondary=None):
            if secondary is primary:
                return
            list.append(failures, (phase, primary, _native_trace(primary), secondary))
            list.append(errors, 'native_finalize_failed')
            if secondary is not None:
                namespace = BaseException.__dict__['__dict__'].__get__(primary, BaseException)
                previous = dict.get(namespace, '__som_native_http_callback_secondary__', ())
                _native_secondary(primary, '__som_native_http_callback_secondary__',
                                  (*previous, secondary) if type(previous) is tuple else (secondary,))
        def finalization(number, saved, error=None, attempted=True):
            value = {'call_id': number, 'attempted': attempted, 'saved': saved}
            if error is not None:
                value['error'] = _native_error(error)
            list.append(finalizations, value)
        def register(response, call_guard, exits):
            guard()
            _native_need(not any(row[0] is response for row in registered), 'HTTP response registered twice')
            list.append(registered, (response, type(response), call_guard, exits))
        def original_descriptor(kind):
            for base in type.__getattribute__(kind, '__mro__'):
                namespace = type.__getattribute__(base, '__dict__')
                if '__exit__' in namespace:
                    return namespace['__exit__']
            raise ValueError('original response exit descriptor is missing')
        def class_snapshot(kind):
            _native_need(type(kind) is type or type(kind) is abc.ABCMeta, 'response class metaclass differs')
            mro = type.__getattribute__(kind, '__mro__')
            namespaces, functions = [], []
            for base in mro:
                _native_need(type(base) is type or type(base) is abc.ABCMeta, 'response base metaclass differs')
                namespace = dict(type.__getattribute__(base, '__dict__'))
                _native_need(all(type(name) is str for name in namespace), 'response class key differs')
                list.append(namespaces, (base, namespace))
                for value in namespace.values():
                    function = value.__func__ if type(value) in (staticmethod, classmethod) else value
                    if type(function) is FunctionType:
                        list.append(functions, (function, (function.__code__, function.__globals__, function.__defaults__,
                                                          function.__kwdefaults__, function.__closure__)))
            return type(kind), mro, namespaces, functions
        def verify_snapshot(kind, snapshot, wrapper=None):
            metaclass, mro, namespaces, functions = snapshot
            actual_mro = type.__getattribute__(kind, '__mro__')
            _native_need(type(kind) is metaclass and len(actual_mro) == len(mro)
                         and all(left is right for left, right in zip(actual_mro, mro)), 'response class MRO changed')
            for base, expected_namespace in namespaces:
                current = type.__getattribute__(base, '__dict__')
                expected_namespace = {**expected_namespace, '__exit__': wrapper} if base is kind and wrapper is not None else expected_namespace
                _native_need(len(current) == len(expected_namespace)
                             and all(current.get(name) is value for name, value in expected_namespace.items()),
                             'response class namespace changed')
            for function, original in functions:
                actual = (function.__code__, function.__globals__, function.__defaults__, function.__kwdefaults__, function.__closure__)
                _native_need(all(left is right for left, right in zip(actual, original)), 'response method code or bindings changed')
        def response_row(response):
            for row in registered:
                if row[0] is response:
                    _native_need(type(response) is row[1], 'registered HTTP response type changed')
                    return row
            if type(response) is http_error_kind:
                namespace = BaseException.__dict__['__dict__'].__get__(response, BaseException)
                inner = dict.get(namespace, 'fp')
                _native_need(dict.get(namespace, 'file') is inner and 'close' not in namespace,
                             'HTTPError real file fields differ')
                closer = dict.get(namespace, '_closer')
                _native_need(type(closer) is closer_kind, 'HTTPError real closer differs')
                values = object.__getattribute__(closer, '__dict__')
                _native_need(type(values) is dict and all(type(key) is str and key in {
                        'file', 'name', 'delete', 'delete_on_close', 'cleanup_called', 'close_called'} for key in values)
                             and values.get('file') is inner and values.get('delete') is False
                             and type(values.get('name')) is str and values['name'] == '<urllib response>'
                             and type(values.get('delete_on_close')) is bool
                             and all(type(values[key]) is bool for key in ('cleanup_called', 'close_called') if key in values)
                             and type.__getattribute__(closer_kind, '__dict__').get('cleanup_called') is False
                             and type.__getattribute__(closer_kind, '__dict__').get('close_called') is False,
                             'HTTPError real closer file differs')
                for row in registered:
                    if row[0] is inner:
                        _native_need(type(inner) is row[1], 'HTTPError registered inner type changed')
                        return row
            return None
        def verify_exits():
            for kind, old, namespace, snapshot, wrapper, present, module, source_path, source_raw in patches:
                verify_snapshot(kind, snapshot, wrapper)
                module_values = ModuleType.__getattribute__(module, '__dict__')
                _native_need(module_values.get(type.__getattribute__(kind, '__name__')) is kind
                             and module_values.get('__file__') == str(source_path)
                             and read_regular(source_path) == source_raw, 'original response stdlib source changed')
            for kind, snapshot, module, source_path, source_raw in wrapper_snapshots:
                verify_snapshot(kind, snapshot)
                module_values = ModuleType.__getattribute__(module, '__dict__')
                _native_need(module_values.get(type.__getattribute__(kind, '__name__')) is kind
                             and module_values.get('__file__') == str(source_path)
                             and read_regular(source_path) == source_raw, 'HTTPError wrapper stdlib source changed')
        def install_exits():
            nonlocal installed
            guard()
            _native_need(threading.get_ident() == installing_thread and not installed and not patches,
                         'owned response exit installation reentry')
            for kind, module in ((http.client.HTTPResponse, http.client), (urllib.response.addbase, urllib.response)):
                _native_need((type(kind) is type or type(kind) is abc.ABCMeta) and type(module) is ModuleType
                             and ModuleType.__getattribute__(module, '__dict__').get(type.__getattribute__(kind, '__name__')) is kind,
                             'actual stdlib response class required')
                source_path = Path(ModuleType.__getattribute__(module, '__dict__')['__file__'])
                _native_need(source_path.is_absolute() and source_path.is_relative_to(Path(sys.base_prefix)),
                             'response class source is outside actual stdlib')
                source_raw = read_regular(source_path)
                _native_stdlib_class(kind, module, source_raw, source_path)
                namespace = dict(type.__getattribute__(kind, '__dict__'))
                snapshot = class_snapshot(kind)
                old = original_descriptor(kind)
                def make_exit(original):
                    def owned_exit(response, et, value, trace):
                        row = None
                        try:
                            _native_need(value is None and et is None and trace is None
                                         or _native_is_error(value) and et is type(value)
                                         and (trace is None or type(trace) is TracebackType), 'explicit response exit arguments differ')
                            guard(); verify_exits()
                            row = response_row(response)
                            if row is not None:
                                row[2]()
                                _native_need(not any(token[0] == threading.get_ident() for token in row[3]),
                                             'owned response exit reentry')
                        except BaseException as later:
                            if value is None:
                                raise
                            failure('close-source-guard', value, later)
                            raise BaseException.with_traceback(value, trace)
                        token = (threading.get_ident(), value, trace)
                        if row is not None:
                            list.append(row[3], token)
                        primary = None
                        try:
                            return original.__get__(response, type(response))(et, value, trace)
                        except BaseException as later:
                            primary = later if value is None else value
                            if primary is not later:
                                failure('response-exit', primary, later)
                            raise BaseException.with_traceback(primary, _native_trace(later) if value is None else trace)
                        finally:
                            if row is not None:
                                if row[3] and row[3][-1] is token:
                                    list.pop(row[3])
                                else:
                                    error = ValueError('owned response exit token changed')
                                    if primary is not None or value is not None:
                                        failure('response-exit-token', primary if primary is not None else value, error)
                                    else:
                                        raise error
                    return owned_exit
                wrapper = make_exit(old)
                list.append(patches, (kind, old, namespace, snapshot, wrapper, '__exit__' in namespace, module, source_path, source_raw))
                event = {'class': type.__getattribute__(kind, '__module__') + '.' + type.__getattribute__(kind, '__name__'),
                         'field': '__exit__', 'original_own_field': '__exit__' in namespace,
                         'stdlib_source': str(source_path), 'stdlib_source_sha256': digest(source_raw),
                         'installed': False, 'restored': False, 'postcheck_passed': False,
                         'foreign_field_preserved': False}
                list.append(exit_events, event)
                type.__setattr__(kind, '__exit__', wrapper)
                event['installed'] = type.__getattribute__(kind, '__dict__').get('__exit__') is wrapper
            installed = True
            for kind, module in ((http_error_kind, urllib.error), (closer_kind, tempfile)):
                source_path = Path(ModuleType.__getattribute__(module, '__dict__')['__file__'])
                _native_need(source_path.is_absolute() and source_path.is_relative_to(Path(sys.base_prefix)),
                             'HTTPError wrapper source is outside actual stdlib')
                list.append(wrapper_snapshots, (kind, class_snapshot(kind), module, source_path, read_regular(source_path)))
            verify_exits()
        def restore_exits():
            nonlocal installed
            problems = []
            _native_need(threading.get_ident() == installing_thread, 'owned response restoration thread differs')
            for row in registered:
                if row[3]:list.append(problems, ValueError('owned response exit remains active'))
            for index in reversed(range(len(patches))):
                kind, old, namespace, snapshot, wrapper, present, module, source_path, source_raw = patches[index]
                try:
                    current = type.__getattribute__(kind, '__dict__')
                    exit_events[index]['foreign_field_preserved'] = current.get('__exit__') is not wrapper
                    _native_need(current.get('__exit__') is wrapper, 'foreign response exit field kept')
                    if present:type.__setattr__(kind, '__exit__', namespace['__exit__'])
                    else:type.__delattr__(kind, '__exit__')
                    restored_namespace = type.__getattribute__(kind, '__dict__')
                    exit_events[index]['restored'] = (restored_namespace.get('__exit__') is namespace['__exit__']
                        if present else '__exit__' not in restored_namespace)
                    verify_snapshot(kind, snapshot)
                    _native_need(read_regular(source_path) == source_raw, 'response stdlib source changed at close')
                    exit_events[index]['postcheck_passed'] = True
                except BaseException as error:list.append(problems, error)
            installed = False
            if problems:
                _native_secondary(problems[0], '__som_native_http_exit_secondary__', problems[1:])
                raise problems[0]
        def enter():
            guard(); install_exits()
            self.original_open = original_open
            def recording_open(handler, http_class, request, **kwargs):
                return opened(handler, http_class, request, **kwargs)
            urllib.request.AbstractHTTPHandler.do_open = recording_open
            return recording_open
        def opened(handler, http_class, request, **kwargs):
            guard(); verify_exits()
            try:
                identity = request_identity(request, ports)
                with self.lock:
                    need(len(calls) < MAX_CALLS)
                    call = NativeCall(self, identity, len(calls) + 1,
                                      recording=(guard, failure, finalization, register))
                    callbacks = object.__getattribute__(call, '__dict__')['_native_callbacks']
                    finalize = callbacks[0]
                    state = (finalize.__code__, finalize.__globals__, finalize.__defaults__,
                             finalize.__kwdefaults__, finalize.__closure__)
                    list.append(calls, call);list.append(finishers, (call, finalize, state))
            except RecordingError:
                list.append(errors, 'request_refused')
                raise
            try:
                response = original_open(handler, http_class, request, **kwargs)
            except BaseException as primary:
                callbacks[1](primary, 'transport-finalize')
                raise
            try:
                return callbacks[2](response)
            except BaseException as primary:
                callbacks[1](primary, 'attach-finalize')
                try:
                    guard(); verify_exits()
                except BaseException as secondary:
                    failure('attach-close-source-guard', primary, secondary)
                else:
                    try:response.close()
                    except BaseException as secondary:failure('attach-response-close', primary, secondary)
                raise
        def release():
            nonlocal active
            active = False
        operations = (guard, enter, restore_exits, release, failures, finalizations, finishers, original_open, exit_events)
        self._native_operations = operations

    def open(self, handler, http_class, request, **kwargs):
        raise ValueError('native HTTP uses only its captured source-owned open operation')

    def __enter__(self):
        _native_recording_operations(self)[1]()
        return self

    def __exit__(self, *_exception):
        raise ValueError('native HTTP cleanup belongs to its captured outer scope')


def run_checker(out: str | Path, script: str | Path, arguments: list[str], qa_mode=None):
    if _native_route():
        return _native_run_checker(out, script, arguments, qa_mode)
    if qa_mode is not None:
        raise ValueError('native HTTP mode requires actual Core handoff')
    need(os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted")
    root = Path(os.path.abspath(REPO))
    target = Path(os.path.abspath(script))
    output = Path(os.path.abspath(out))
    need(target.is_relative_to(root) and target.relative_to(root).as_posix() in ALLOWED_SCRIPTS)
    need(output.is_relative_to(root / ".local") and not output.exists() and not output.is_symlink())
    physical(output.parent)
    source = read_regular(target)
    native_router = None
    if qa_mode == 'native-qa-v1':
        holder = sys.modules.get('_som_m6_checked_router')
        need(holder is not None and getattr(holder, 'qa_mode', None) == 'native-qa-v1'
             and callable(getattr(getattr(holder, 'router', None), 'execute', None)))
        native_router = holder.router
        need(source == native_router.sources.read(target.relative_to(root).as_posix()))
    else:
        need(qa_mode is None)
    output.mkdir(mode=0o700)
    (output / "calls").mkdir(mode=0o700)
    before_argv, before_path, before_env = sys.argv, sys.path[:], dict(os.environ)
    recorder = Recorder(output, (18978, 18979) if native_router is not None else (18917, 18919))
    code = 1
    failed_recording = False
    try:
        sys.argv = [str(target), *arguments]
        sys.path[:] = [str(target.parent), *before_path]
        os.environ.clear()
        allowed = CHILD_ENV | (frozenset(('SOM_FULL_PRODUCT_CONTEXT', 'GITHUB_SHA', 'GITHUB_RUN_ID',
                                           'GITHUB_RUN_ATTEMPT')) if native_router is not None else frozenset())
        os.environ.update({key: value for key, value in before_env.items() if key in allowed})
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        with recorder:
            try:
                if native_router is None:
                    runpy.run_path(str(target), run_name="__main__")
                    code = 0
                else:
                    code = native_router.execute(str(target), arguments)
            except SystemExit as error:
                if error.code is None:
                    code = 0
                elif isinstance(error.code, int):
                    code = int(error.code)
                else:
                    print(error.code, file=sys.stderr)
                    code = 1
            except Exception:
                traceback.print_exc()
                code = 1
    except Exception:
        failed_recording = True
        code = 1
    finally:
        sys.argv, sys.path[:] = before_argv, before_path
        os.environ.clear()
        os.environ.update(before_env)
    need(read_regular(target) == source)
    if failed_recording:
        recorder.errors.append("recording_failed")
    manifest = {
        "version": VERSION, "script": {"path": target.relative_to(root).as_posix(), "sha256": digest(source)},
        "argv": [str(target), *arguments], "exit_code": code,
        **({"qa_mode": "native-qa-v1", "ports": [18978, 18979],
            "source_route": "checked-git-bytes"} if native_router is not None else {}),
        "calls": [call.record for call in recorder.calls], "recording_errors": recorder.errors,
    }
    write_new(output / "manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return code if not recorder.errors else 1


class QuietParser(argparse.ArgumentParser):
    def error(self, _message):
        raise RecordingError()


def main(argv=None):
    try:
        parser = QuietParser(description=__doc__.splitlines()[0])
        parser.add_argument("--out", required=True)
        parser.add_argument("--qa-mode", choices=('native-qa-v1',), default=None)
        parser.add_argument("checker", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv)
        command = args.checker[1:] if args.checker[:1] == ["--"] else args.checker
        need(command and all(type(value) is str for value in command))
        if args.qa_mode is None:
            return run_checker(args.out, command[0], command[1:])
        return run_checker(args.out, command[0], command[1:], args.qa_mode)
    except Exception:
        print(REFUSAL, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
