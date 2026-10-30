#!/usr/bin/env python3
"""Keep raw HTTP evidence while an unchanged Python checker runs on GitHub.

Usage: record_http.py --out NEW_DIR -- SCRIPT ORIGINAL_ARGUMENTS
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import re
from types import ModuleType, FunctionType
import stat
import sys
import threading
import traceback
import urllib.parse
import urllib.request

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
    recorder = __som_native_Recorder__(output, (18978, 18979))
    finish = __som_native_finish_call__
    finish_state = (finish.__code__, finish.__globals__, finish.__defaults__, finish.__kwdefaults__, finish.__closure__)
    call_mro = type.__getattribute__(__som_native_Call__, '__mro__')
    call_namespaces = tuple((base, dict(type.__getattribute__(base, '__dict__'))) for base in call_mro)
    calls, recording_errors = recorder.calls, recorder.errors
    callback_failures, finalizations = recorder.native_failures, recorder.native_finalizations
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
            actual_trace = BaseException.__getattribute__(error, '__traceback__')
            primary, primary_trace = error, actual_trace if actual_trace is not None else original_trace
            evidence['first_error'] = {'phase': phase, **__som_native_safe_error__(error)}
        elif error is not primary and not any(error is previous for previous in secondary):
            secondary.append(error)
            evidence['secondary_errors'].append({'phase': phase, **__som_native_safe_error__(error)})
        code = 1
    consumed_failures = 0
    def callback_errors():
        nonlocal consumed_failures
        while consumed_failures < len(callback_failures):
            phase, actual, actual_trace, extra = callback_failures[consumed_failures]
            consumed_failures += 1
            failure('http-callback:' + phase, actual, actual_trace)
            if extra is not None:
                failure('http-callback-secondary:' + phase, extra)
    try:
        lease.verify_installed()
        recorder.__enter__()
        installed = urllib.request.AbstractHTTPHandler.do_open
        original_open = recorder.original_open
        evidence['execute_entry_attempted'] = True
        code = _native_execute_checker(role, arguments)
        evidence['execute_entry_returned'] = True
        evidence['returned_exit_code'] = code
    except BaseException as error:
        callback_errors()
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
        for call in calls:
            try:
                _native_need(type(call) is __som_native_Call__ and type(finish) is FunctionType
                     and type.__getattribute__(__som_native_Call__, '__mro__') == call_mro
                     and all(len(type.__getattribute__(base, '__dict__')) == len(expected)
                             and all(type.__getattribute__(base, '__dict__').get(key) is value
                                     for key, value in expected.items()) for base, expected in call_namespaces)
                     and all(current is original for current, original in zip(
                         (finish.__code__, finish.__globals__, finish.__defaults__, finish.__kwdefaults__, finish.__closure__), finish_state)),
                     'original HTTP finalizer identity changed')
                finish(call)
            except BaseException as error:
                failure('finish-http-call', error)
        callback_errors()
        evidence['raw_finalizations'] = list(finalizations)
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
             ('controls-postcheck', lease.verify_installed), ('borrow-close', lease.close)):
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
                traceback.print_exception(type(primary), primary, primary_trace, file=sys.stderr)
            except BaseException as error:
                failure('primary-traceback-write', error)
            BaseException.__setattr__(primary, '__som_native_http_secondary__', tuple(secondary))
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


class NativeCall(Call):
    """Keep callback/body errors primary without trusting cleanup after guard failure."""
    def __init__(self, recorder, identity, number):
        _native_callback_guard()
        super().__init__(recorder, identity, number)

    def _finalize(self, complete=False, error=None, *, primary=None, phase='raw-finalize'):
        recorder = self.recorder
        attempted = False
        try:
            _native_callback_guard()
            if self.done:
                return None
            attempted = True
            super().finish(complete, error)
        except BaseException as later:
            recorder._native_finalization(self, False, later, attempted=attempted)
            actual = later if primary is None else primary
            recorder._native_failure(phase, actual, None if actual is later else later)
            if primary is None:
                raise
            return later
        recorder._native_finalization(self, True)
        return None

    def _after_error(self, primary, phase, *, observed_error=None):
        try:
            _native_callback_guard()
            value = safe_error(primary if observed_error is None else observed_error)
        except BaseException as later:
            self.recorder._native_finalization(self, False, later, attempted=False)
            self.recorder._native_failure(phase, primary, later)
            return
        self._finalize(error=value, primary=primary, phase=phase)

    def finish(self, complete=False, error=None):
        return self._finalize(complete, error)

    def attach(self, response):
        _native_callback_guard()
        need(type(response.status) is int)
        self.record['status'] = response.status
        original_read, original_close = response.read, response.close
        # Capture the source-owned in-memory sink while its owner is valid.
        failure_sink = self.recorder._native_failure
        def read(*args, **kwargs):
            try:
                _native_callback_guard()
                raw = original_read(*args, **kwargs)
                self.append(raw)
                amount = args[0] if args else kwargs.get('amt')
                complete = (amount is None or (type(amount) is int and amount < 0)
                            or (amount != 0 and (not raw or getattr(response, 'length', None) == 0)))
                if complete:
                    self.finish(complete=True)
                return raw
            except BaseException as primary:
                self._after_error(primary, 'read-finalize')
                raise
        def close():
            # Capture an actual active body/read error before any fallible guard.
            pending = sys.exception()
            pending_trace = BaseException.__getattribute__(pending, '__traceback__') if pending is not None else None
            try:
                _native_callback_guard()
            except BaseException as guard_error:
                if pending is None:
                    raise
                # No response or raw-finalizer operation runs after this refusal.
                failure_sink('close-source-guard', pending, guard_error)
                raise BaseException.with_traceback(pending, pending_trace)
            try:
                result = original_close()
            except BaseException as close_error:
                primary = close_error if pending is None else pending
                trace = BaseException.__getattribute__(primary, '__traceback__')
                if primary is not close_error:
                    failure_sink('response-close', primary, close_error)
                self._after_error(primary, 'close-finalize', observed_error=close_error)
                raise BaseException.with_traceback(primary, trace)
            if pending is None:
                self.finish()
            else:
                later = self._finalize(primary=pending, phase='close-finalize')
                if later is not None:
                    raise BaseException.with_traceback(pending, pending_trace)
            # A pending but already handled error is not rethrown on success.
            return result
        response.read, response.close = read, close
        return response


class NativeRecorder(Recorder):
    """Native error state is local to this recording instance, never an entry cache."""
    def __init__(self, out, ports=(18917, 18919)):
        _native_callback_guard()
        super().__init__(out, ports)
        self.native_failures = []
        self.native_finalizations = []

    def _native_failure(self, phase, primary, secondary=None):
        if secondary is primary:
            return
        trace = BaseException.__getattribute__(primary, '__traceback__')
        self.native_failures.append((phase, primary, trace, secondary))
        if 'native_finalize_failed' not in self.errors:
            self.errors.append('native_finalize_failed')
        if secondary is not None:
            try:
                previous = BaseException.__getattribute__(primary, '__som_native_http_callback_secondary__')
            except AttributeError:
                previous = ()
            if type(previous) is not tuple:
                previous = ()
            BaseException.__setattr__(primary, '__som_native_http_callback_secondary__', (*previous, secondary))

    def _native_finalization(self, call, saved, error=None, *, attempted=True):
        value = {'call_id': call.record['id'], 'attempted': attempted, 'saved': saved}
        if error is not None:
            value['error'] = safe_error(error)
        self.native_finalizations.append(value)

    def open(self, handler, http_class, request, **kwargs):
        _native_callback_guard()
        try:
            identity = request_identity(request, self.ports)
            with self.lock:
                need(len(self.calls) < MAX_CALLS)
                call = NativeCall(self, identity, len(self.calls) + 1)
                self.calls.append(call)
        except RecordingError:
            with self.lock:
                self.errors.append('request_refused')
            raise
        try:
            response = self.original_open(handler, http_class, request, **kwargs)
        except BaseException as primary:
            call._after_error(primary, 'transport-finalize')
            raise
        try:
            return call.attach(response)
        except BaseException as primary:
            call._after_error(primary, 'attach-finalize')
            try:
                _native_callback_guard()
            except BaseException as secondary:
                self._native_failure('attach-close-source-guard', primary, secondary)
            else:
                try:
                    response.close()
                except BaseException as secondary:
                    self._native_failure('attach-response-close', primary, secondary)
            raise


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
