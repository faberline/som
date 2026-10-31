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
from types import ModuleType, FunctionType, MethodType, TracebackType, CodeType, GetSetDescriptorType
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
    # Held only after the unchanged healthy Core/fixed-owner handoff.
    _native_frame_get = __som_native_HTTP_FRAME_GET__
    _native_object_new = __som_native_HTTP_OBJECT_NEW__
    _native_gen_send, _native_gen_close = __som_native_HTTP_GEN_SEND__, __som_native_HTTP_GEN_CLOSE__
    _native_rlock_new, _native_rlock_owned = __som_native_HTTP_RLOCK_NEW__, __som_native_HTTP_RLOCK_OWNED__
    _native_rlock_acquire, _native_rlock_release = __som_native_HTTP_RLOCK_ACQUIRE__, __som_native_HTTP_RLOCK_RELEASE__
    _native_entry_signature = _native_invocation(_native_run_checker)
    _native_constructor_kind = __som_native_Recorder__
    _native_constructor_snapshot = _native_class_capture(_native_constructor_kind)
    _native_constructor_wrapper = type.__getattribute__(_native_constructor_kind, '__dict__').get('__new__')
    _native_need(type(_native_constructor_wrapper) is staticmethod, 'original static HTTP constructor required')
    _native_constructor_signature = _native_invocation(object.__getattribute__(_native_constructor_wrapper, '__func__'))
    _native_constructor_init_signature = _native_invocation(type.__getattribute__(_native_constructor_kind, '__dict__')['__init__'])
    _native_constructor_classes = (__som_native_fixed__NativeIssuer__, __som_native_fixed__NativeCallbackSeal__,
        __som_native_fixed__NativeExitFrame__, __som_native_fixed__NativeResponseState__,
        __som_native_fixed__NativeCallState__, __som_native_fixed__NativeExitDescriptor__,
        __som_native_fixed__NativeRecordingState__, __som_native_fixed__NativeExitState__,
        __som_native_Call__, __som_native_Recorder__)
    _native_constructor_snapshots = tuple(_native_class_capture(kind) for kind in _native_constructor_classes)
    _native_constructor_issuer = __som_native_HTTP_issuer__
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
    _native_class_check(_native_constructor_snapshot)
    _native_need(type.__getattribute__(_native_constructor_kind, '__dict__').get('__new__') is _native_constructor_wrapper,
                 'original static HTTP constructor wrapper changed')
    _native_check_invocation(_native_constructor_signature)
    recorder = _native_check_invocation(_native_constructor_signature)(_native_constructor_kind, output, (18978, 18979), initial_ctx=ctx)
    _native_original_channel = dict.get(object.__getattribute__(recorder, '__dict__'), '_native_origin')
    _native_acquisition_codes = tuple(item for item in _native_constructor_signature[3].co_consts
                                      if type(item) is CodeType and item.co_name == 'acquire')
    _native_need(len(_native_acquisition_codes) == 1, 'one literal HTTP acquisition code required')
    _native_producer_codes = tuple(item for item in _native_acquisition_codes[0].co_consts
                                  if type(item) is CodeType and item.co_name == 'produce')
    _native_need(len(_native_producer_codes) == 1, 'one literal HTTP producer code required')
    _native_origin_code = _native_producer_codes[0]
    try:
        _native_need(type(_native_original_channel) is type((item for item in ()))
                     and _native_original_channel.gi_code is _native_origin_code
                     and _native_original_channel.gi_frame.f_globals is _native_constructor_signature[4],
                     'fresh original HTTP producer source required')
        _native_class_check(_native_constructor_snapshot)
        _native_check_invocation(_native_constructor_init_signature)(recorder, output, (18978, 18979), initial_ctx=ctx)
    except BaseException as primary:
        try:
            _native_gen_close(_native_original_channel)
        except BaseException as secondary:
            __som_native_secondary__(primary, '__som_native_http_constructor_close_secondary__', (secondary,))
        raise
    operations = __som_native_recording_operations__(recorder)
    def held_operation(method):
        _native_need(type(method) is MethodType and type(method.__func__) is FunctionType,
                     'actual constructor-issued operation required')
        function = method.__func__
        return (method, method.__self__, function, function.__code__, function.__globals__, function.__builtins__,
                function.__defaults__, function.__kwdefaults__, function.__closure__,
                tuple((cell, cell.cell_contents) for cell in function.__closure__ or ()),
                tuple(function.__kwdefaults__.items()) if type(function.__kwdefaults__) is dict else None)
    def invoke_held(signature, *args):
        method, owner, function, code, namespace, builtins, defaults, keywords, closure, cells, pairs = signature
        _native_need(type(method) is MethodType and method.__self__ is owner and method.__func__ is function
                     and type(function) is FunctionType and function.__code__ is code and function.__globals__ is namespace
                     and function.__builtins__ is builtins and function.__defaults__ is defaults
                     and function.__kwdefaults__ is keywords and function.__closure__ is closure
                     and all(cell.cell_contents is value for cell, value in cells)
                     and (pairs is None or type(keywords) is dict and len(keywords) == len(pairs)
                          and all(type(key) is str for key in keywords)
                          and all(dict.get(keywords, key) is value for key, value in pairs)),
                     'original captured operation invocation changed')
        return method(*args)
    held = {index: held_operation(operations[index]) for index in (0, 1, 2, 3, 9, 10)}
    def operation(index, *args):
        _native_need(type(index) is int and index in (0, 1, 2, 3, 9, 10), 'finite original operation required')
        return invoke_held(held[index], *args)
    calls, recording_errors = recorder.calls, recorder.errors
    callback_failures, finalizations, finishers = operations[4:7]
    lease = controls.borrow()
    borrow_verify, borrow_close = operation(10, lease, controls)
    held_borrow_verify, held_borrow_close = held_operation(borrow_verify), held_operation(borrow_close)
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
        invoke_held(held_borrow_verify)
        installed = operation(1)
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
        try:
            operation(9)
        except BaseException as error:
            failure('finish-http-call', error)
        callback_errors()
        evidence['raw_finalizations'] = list(finalizations)
        try:
            operation(2)
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
            operation(0)
            actual, actual_sources, actual_ctx, actual_authority = _native_context()
            _native_need(actual is controls and actual_sources is sources and actual_ctx is ctx
                         and actual_authority is authority, 'HTTP native owner changed')
        def target_source():
            operation(0)
            controls.verify_installed()
            _native_need(__som_native_read_regular__(target) == source == sources.read(relative), 'HTTP target source changed')
        def context_source():
            operation(0)
            controls.verify_installed()
            values = object.__getattribute__(ctx, '__dict__')
            _native_need(values.get('path') == context_path and values.get('raw') == context_raw
                         and __som_native_context_read__(context_path) == context_raw, 'HTTP Context raw changed')
        def source_postcheck():
            operation(0)
            controls.verify_installed()
            sources.verify()
        for phase, action in (('process-state', process_state), ('process-state-restore', restore_process_state),
             ('target-source', target_source), ('context-raw', context_source),
             ('source-postcheck', source_postcheck), ('same-owner', same_owner),
             ('controls-postcheck', lambda: invoke_held(held_borrow_verify)), ('recording-release', lambda: operation(3)), ('borrow-close', lambda: invoke_held(held_borrow_close))):
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
        reported_primary = primary is not None
        if reported_primary:
            try:
                traceback.print_tb(primary_trace, file=sys.stderr)
                print(__som_native_error__(primary)['type'], file=sys.stderr)
            except BaseException as error:
                failure('primary-traceback-write', error)
        # Close the exact channel held before __init__, after all evidence,
        # descriptor/process restoration and the independent nonowner close.
        # A changed public locator cannot select a different channel here.
        try:
            _native_need(_native_original_channel.gi_code is _native_origin_code,
                         'held original HTTP producer code changed')
            _native_gen_close(_native_original_channel)
        except BaseException as error:
            failure('native-producer-close', error)
        if primary is not None:
            if not reported_primary:
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


def _native_invocation(function):
    if type(function) is MethodType:
        owner, actual = function.__self__, function.__func__
    else:
        owner, actual = None, function
    _native_need(type(actual) is FunctionType, 'actual source function required')
    cells = actual.__closure__ or ()
    return (function, owner, actual, actual.__code__, actual.__globals__, actual.__builtins__,
            actual.__defaults__, actual.__kwdefaults__, actual.__closure__,
            tuple((cell, cell.cell_contents) for cell in cells),
            tuple(actual.__kwdefaults__.items()) if type(actual.__kwdefaults__) is dict else None)


def _native_check_invocation(signature):
    function, owner, actual, code, namespace, builtins, defaults, keywords, closure, cells, pairs = signature
    _native_need(type(actual) is FunctionType and actual.__code__ is code and actual.__globals__ is namespace
                 and actual.__builtins__ is builtins and actual.__defaults__ is defaults
                 and actual.__kwdefaults__ is keywords and actual.__closure__ is closure
                 and all(cell.cell_contents is value for cell, value in cells)
                 and (pairs is None or type(keywords) is dict and len(keywords) == len(pairs)
                      and all(type(key) is str for key in keywords)
                      and all(dict.get(keywords, key) is value for key, value in pairs)),
                 'constructor-issued callback invocation changed')
    if owner is not None:
        _native_need(type(function) is MethodType and function.__self__ is owner and function.__func__ is actual,
                     'original bound callback changed')
    else:
        _native_need(function is actual, 'original callback pointer changed')
    return function


def _native_class_capture(kind):
    mro = type.__getattribute__(kind, '__mro__')
    namespaces, functions = [], []
    for base in mro:
        namespace = type.__getattribute__(base, '__dict__')
        _native_need(all(type(key) is str for key in namespace), 'original source class keys differ')
        namespaces.append((base, tuple(namespace.items())))
        for value in namespace.values():
            if type(value) is FunctionType:
                functions.append(_native_invocation(value))
            elif type(value) is staticmethod or type(value) is classmethod:
                functions.append(_native_invocation(object.__getattribute__(value, '__func__')))
            elif type(value) is property:
                functions.extend(_native_invocation(item) for item in (value.fget, value.fset, value.fdel)
                                 if item is not None)
    return kind, mro, tuple(namespaces), tuple(functions)


def _native_class_check(snapshot):
    kind, mro, namespaces, functions = snapshot
    _native_need(type.__getattribute__(kind, '__mro__') is mro, 'original source class MRO changed')
    for base, expected in namespaces:
        current = type.__getattribute__(base, '__dict__')
        _native_need(len(current) == len(expected) and all(type(key) is str for key in current)
                     and all(current.get(key) is value for key, value in expected),
                     'original source class namespace changed')
    for signature in functions:
        _native_check_invocation(signature)


class _NativeIssuer:
    """Private constructor ledger. No receipt or public callback supplies it."""
    def __init__(self):
        self.recordings, self.calls = {}, {}
        self.helpers = ()


def _native_origin_types(frame_get, object_new, gen_send, gen_close,
                         rlock_new, rlock_owned, rlock_acquire, rlock_release):
    """Check the finite layouts of the eight already fixed native values.

    Their trusted base is the qualified startup/source chain and early Core
    fixed handoff. These structural checks do not create native provenance.
    No current _thread/threading class lookup supplies the expected owner.
    """
    native_function = type(frame_get)
    descriptor = type(gen_send)
    _native_need(native_function is not FunctionType
                 and type(object_new) is native_function and type(rlock_new) is native_function
                 and descriptor is type(gen_close),
                 'fixed HTTP native primitive kinds differ')
    for value in (rlock_owned, rlock_acquire, rlock_release):
        _native_need(type(value) is descriptor, 'fixed HTTP native descriptor kind differs')
    generator_kind = object.__getattribute__(gen_send, '__objclass__')
    _native_need(type(generator_kind) is type
                 and object.__getattribute__(gen_close, '__objclass__') is generator_kind,
                 'original native generator descriptor owners differ')
    rlock_kind = object.__getattribute__(rlock_new, '__self__')
    _native_need(type(rlock_kind) is type
                 and type.__getattribute__(rlock_kind, '__mro__') == (rlock_kind, object)
                 and type.__getattribute__(rlock_kind, '__flags__') & (1 << 8),
                 'original immutable native RLock owner differs')
    for value in (rlock_owned, rlock_acquire, rlock_release):
        _native_need(object.__getattribute__(value, '__objclass__') is rlock_kind,
                     'original native RLock descriptor owner differs')
    namespace = type.__getattribute__(rlock_kind, '__dict__')
    original_new = namespace.get('__new__')
    _native_need(original_new is rlock_new
                 or type(original_new) is staticmethod
                 and object.__getattribute__(original_new, '__func__') is rlock_new,
                 'original native RLock constructor association differs')
    _native_need('__init__' not in namespace
                 and namespace.get('_is_owned') is rlock_owned
                 and namespace.get('acquire') is rlock_acquire
                 and namespace.get('release') is rlock_release,
                 'native RLock no-argument constructor or method slots differ')
    return generator_kind, rlock_kind


def _native_transaction(subject, operation, candidate=None):
    """One finite private check. No long operation runs under this lock."""
    frame_get = __som_native_HTTP_FRAME_GET__
    object_new = __som_native_HTTP_OBJECT_NEW__
    gen_send, gen_close = __som_native_HTTP_GEN_SEND__, __som_native_HTTP_GEN_CLOSE__
    rlock_new, rlock_owned = __som_native_HTTP_RLOCK_NEW__, __som_native_HTTP_RLOCK_OWNED__
    rlock_acquire, rlock_release = __som_native_HTTP_RLOCK_ACQUIRE__, __som_native_HTTP_RLOCK_RELEASE__
    if not (type(operation) is str and operation in (
        'preflight', 'begin-recording', 'recording-init', 'recording-active', 'recording-failed',
        'recording-check', 'safe-recording', 'safe-call', 'recording-release', 'new-call', 'begin-call', 'call-init', 'call-check',
        'response-init', 'exit-init', 'borrow-init', 'open-init', 'open-constructor-check')):
        raise ValueError('unlisted private HTTP operation')
    # The qualified VM creates this kind directly. Do not inspect a changed
    # candidate native descriptor before comparing it with private originals.
    generator_kind = type((item for item in ()))
    if not any(type(subject) is kind for kind in (NativeRecorder, NativeCall, _NativeRecordingState, _NativeCallState)):
        raise ValueError('foreign private HTTP transaction receiver')
    fields = object.__getattribute__(subject, '__dict__')
    if not (type(fields) is dict and all(type(key) is str for key in fields)):
        raise ValueError('native HTTP locator fields differ')
    channel = dict.get(fields, '_native_origin')
    if type(channel) is not generator_kind:
        raise ValueError('actual private HTTP producer required')
    frame = object.__getattribute__(channel, 'gi_frame')
    if frame is None:
        raise ValueError('private HTTP producer ended before cleanup')
    # f_locals is refreshed from the actual native frame each time. The view
    # dictionary is never retained or accepted as expected-state authority.
    values = frame.f_locals
    if not (type(values) is dict and frame.f_globals is globals()
            and frame.f_code is dict.get(values, 'producer_code')):
        raise ValueError('private HTTP producer source differs')
    pure_functions = dict.get(values, 'pure_functions')
    if type(pure_functions) is not tuple:
        raise ValueError('private HTTP producer has no original helper facts')
    # Do not invoke a mutable global helper before its private original has
    # been checked. All stored signatures are immutable tuple facts.
    for name, original in pure_functions:
        function = original[2]
        if (globals().get(name) is not original[0] or type(function) is not FunctionType
                or function.__code__ is not original[3] or function.__globals__ is not original[4]
                or function.__builtins__ is not original[5] or function.__defaults__ is not original[6]
                or function.__kwdefaults__ is not original[7] or function.__closure__ is not original[8]):
            raise ValueError('original private HTTP pure helper changed')
    check_invocation = next(signature[0] for name, signature in pure_functions if name == '_native_check_invocation')
    need = next(signature[0] for name, signature in pure_functions if name == '_native_need')
    types_check = next(signature[0] for name, signature in pure_functions if name == '_native_origin_types')
    for name, signature in pure_functions:
        check_invocation(signature)
    creator = dict.get(values, 'creator_frame')
    constructor = dict.get(values, 'constructor_frame')
    producer = creator.f_locals.get('producer') if creator is not None else None
    acquisition = constructor.f_locals.get('acquisition') if constructor is not None else None
    need(type(producer) is FunctionType and producer.__code__ is frame.f_code
                 and producer.__globals__ is frame.f_globals
                 and type(acquisition) is FunctionType and acquisition.__code__ is creator.f_code
                 and acquisition.__globals__ is frame.f_globals
                 and any(item is creator.f_code for item in constructor.f_code.co_consts)
                 and any(item is frame.f_code for item in creator.f_code.co_consts),
                 'private HTTP producer creator association differs')
    for name, value in (
        ('frame_get', frame_get), ('object_new', object_new), ('gen_send', gen_send), ('gen_close', gen_close),
        ('rlock_new', rlock_new), ('rlock_owned', rlock_owned),
        ('rlock_acquire', rlock_acquire), ('rlock_release', rlock_release)):
        need(dict.get(values, name) is value,
                     'private HTTP native values lost the early fixed handoff')
    generator_kind, rlock_kind = types_check(frame_get, object_new, gen_send, gen_close,
        rlock_new, rlock_owned, rlock_acquire, rlock_release)
    check_invocation(constructor.f_locals['acquisition_signature'])
    check_invocation(creator.f_locals['producer_signature'])
    recorder, state = dict.get(values, 'recorder'), dict.get(values, 'state')
    calls = dict.get(values, 'calls')
    need(subject is recorder or subject is state or type(calls) is tuple
                 and any(subject is row[0][0] or subject is row[0][4] for row in calls)
                 or operation == 'begin-call' and type(dict.get(values, 'allocated_calls')) is tuple
                 and any(subject is row[0] for row in values['allocated_calls']),
                 'private HTTP producer belongs to another recording')
    lock = dict.get(values, 'lock')
    need(type(lock) is rlock_kind, 'original private HTTP lock receiver differs')
    if rlock_owned(lock):
        raise ValueError('same-thread private HTTP short transaction reentry')
    acquired = rlock_acquire(lock, True)
    need(acquired is True, 'private HTTP lock did not acquire')
    try:
        result = gen_send(channel, (operation, subject, candidate))
    finally:
        rlock_release(lock)
    need(type(result) is tuple and len(result) == 2 and type(result[0]) is bool,
                 'private HTTP producer result differs')
    if not result[0]:
        raise result[1]
    return result[1]


def _native_origin_view(state, *, cleanup=False):
    fields = object.__getattribute__(state, '__dict__')
    row = dict.get(fields, '_native_record')
    _native_need(type(row) is dict, 'original HTTP recording data view is missing')
    _native_transaction(state, 'safe-recording' if cleanup else 'recording-check', row)
    return row



def _native_runtime_preflight(subject):
    _native_transaction(subject, 'preflight')


def _native_recording_issue(state, *, cleanup=False):
    _native_need(type(state) is _NativeRecordingState, 'actual constructor-issued recording required')
    return _native_origin_view(state, cleanup=cleanup)


def _native_call_issue(state, *, cleanup=False):
    _native_need(type(state) is _NativeCallState, 'actual constructor-issued call required')
    _native_need(cleanup is False or cleanup is True, 'literal call cleanup selector required')
    if cleanup is True:
        # Derive the real issue inside the producer. No public candidate tuple
        # or callback is accepted as expected cleanup authority.
        return _native_transaction(state, 'safe-call')
    row = dict.get(object.__getattribute__(state, '__dict__'), '_native_call_record')
    _native_need(type(row) is tuple and row[0] is state, 'call lacks original constructor issue')
    _native_transaction(state, 'call-check', row)
    return row


def _native_safe_failure(state, phase, primary, secondary=None):
    record = _native_recording_issue(state, cleanup=True)
    _native_check_invocation(record['sinks']['failure'])(state, phase, primary, secondary)


def _native_safe_finalization(state, number, saved, error=None, attempted=True):
    record = _native_recording_issue(state, cleanup=True)
    _native_check_invocation(record['sinks']['finalization'])(state, number, saved, error, attempted)


def _native_after_error(state, primary, phase, *, observed_error=None):
    """Keep the real primary while deriving cleanup data from the producer."""
    owner = number = None
    try:
        issue = _native_call_issue(state, cleanup=True)
        owner, number = issue[5], issue[6][0]
        record = _native_recording_issue(owner, cleanup=True)
        function = _native_check_invocation(record['call_after_error'])
        # Cleanup data does not bypass normal dispatch authorization.
        _native_runtime_preflight(state)
        function(state, primary, phase, observed_error=observed_error)
    except BaseException as later:
        try:
            if owner is None:
                namespace = BaseException.__dict__['__dict__'].__get__(primary, BaseException)
                previous = dict.get(namespace, '__som_native_http_after_error_secondary__', ())
                dict.__setitem__(namespace, '__som_native_http_after_error_secondary__',
                                 (*previous, later) if type(previous) is tuple else (later,))
            else:
                _native_safe_finalization(owner, number, False, later, False)
                _native_safe_failure(owner, phase, primary, later)
        except BaseException as cleanup_error:
            namespace = BaseException.__dict__['__dict__'].__get__(primary, BaseException)
            previous = dict.get(namespace, '__som_native_http_after_error_secondary__', ())
            dict.__setitem__(namespace, '__som_native_http_after_error_secondary__',
                             (*previous, later, cleanup_error) if type(previous) is tuple else (later, cleanup_error))


class _NativeCallbackSeal:
    """A constructor-issued binding, never an expected signature from a public row."""
    def __init__(self, controller, state, function):
        self.controller, self.state, self.signature = controller, state, _native_invocation(function)

    def checked(self, controller, state):
        _native_need(type(self) is _NativeCallbackSeal and self.controller is controller and self.state is state,
                     'original callback seal owner changed')
        return _native_check_invocation(self.signature)


class _NativeExitFrame:
    def __init__(self, response, thread, kind, value, trace):
        self.response, self.thread, self.kind, self.value, self.trace = response, thread, kind, value, trace


class _NativeResponseState:
    def __init__(self, call, response, original_read, original_close):
        self.call, self.response, self.kind = call, response, type(response)
        self.original_read, self.original_close = _native_invocation(original_read), _native_invocation(original_close)
        self.read_seal = self.close_seal = None
        self.exit_frame = None
        self.exit_lock = threading.RLock()

    def check_fields(self):
        _native_need(type(self) is _NativeResponseState and type(self.call) is _NativeCallState,
                     'original response state required')
        matches = [row for row in _native_recording_issue(_native_call_issue(self.call)[5])['responses'] if row[0] is self]
        _native_need(len(matches) == 1, 'response was not issued by original call constructor')
        issue = matches[0]
        _native_need(self.response is issue[1] and self.kind is issue[2] and type(self.response) is issue[2]
                     and self.read_seal is issue[3] and self.read_seal.signature is issue[4]
                     and self.close_seal is issue[5] and self.close_seal.signature is issue[6]
                     and self.original_read is issue[11] and self.original_close is issue[12]
                     and self.exit_lock is issue[7] and type(self.exit_lock) is issue[8],
                     'registered HTTP response type or issued bindings changed')
        fields = object.__getattribute__(self.response, '__dict__')
        _native_need(type(fields) is dict and dict.get(fields, 'read') is _native_check_invocation(issue[4])
                     and dict.get(fields, 'close') is _native_check_invocation(issue[6]),
                     'registered HTTP response callbacks changed')
        return issue

    def check(self):
        _native_need(type(self) is _NativeResponseState, 'actual registered response state required')
        _NativeCallState.check(self.call)
        issue = _NativeResponseState.check_fields(self)
        if self.exit_frame is not None:
            _native_need(type(self.exit_frame) is _NativeExitFrame
                         and self.exit_frame.response is self.response
                         and self.exit_frame.thread == threading.get_ident(), 'same response exit belongs to another thread')
        return issue


class _NativeCallState:
    def __init__(self, owner, call, number):
        self.owner, self.call, self.number = owner, call, number
        self._native_origin = object.__getattribute__(owner, '_native_origin')
        self.expected = {name: object.__getattribute__(call, '__dict__')[name]
                         for name in ('recorder', 'lock', 'response', 'record', '_native_origin')}
        self.response = None
        self.pending_close = None
        self.refused = False
        state = self
        def finalize(complete=False, error=None, *, primary=None, phase='raw-finalize'):
            _native_runtime_preflight(state)
            _native_need(type(state) is _NativeCallState, 'actual call state required')
            return _NativeCallState.finalize(state, complete, error, primary=primary, phase=phase)
        def after_error(primary, phase, *, observed_error=None):
            _native_need(type(state) is _NativeCallState, 'actual call state required')
            return _native_after_error(state, primary, phase, observed_error=observed_error)
        def attach(response):
            _native_runtime_preflight(state)
            _native_need(type(state) is _NativeCallState, 'actual call state required')
            return _NativeCallState.attach(state, response)
        self.callbacks = (finalize, after_error, attach)
        self.seals = tuple(_NativeCallbackSeal(owner, self, function) for function in self.callbacks)

    def check(self):
        _native_need(type(self) is _NativeCallState and type(self.owner) is _NativeRecordingState,
                     'original native call controller required')
        _native_need(not self.refused, 'original HTTP call ownership was refused')
        try:
            _NativeRecordingState.guard(self.owner)
        except BaseException:
            self.refused = True
            raise
        issue = _native_call_issue(self)
        _native_need(self.owner is issue[5], 'original call controller changed')
        _native_need(self.callbacks is issue[1] and self.seals is issue[2] and self.call is issue[4],
                     'original call callback registry changed')
        fields = object.__getattribute__(self.call, '__dict__')
        _native_need(type(self.call) is NativeCall and type(fields) is dict
                     and all(type(key) is str for key in fields)
                     and set(fields) == set(self.expected) | {'done', '_native_callbacks', '_native_state'}
                     and all(dict.get(fields, key) is value for key, value in self.expected.items())
                     and type(dict.get(fields, 'done')) is bool and dict.get(fields, '_native_state') is self
                     and dict.get(fields, '_native_callbacks') is self.callbacks, 'original HTTP call fields changed')
        record = dict.get(fields, 'record')
        _native_need(type(record) is dict and all(type(key) is str for key in record)
                     and set(record) == {'id', 'method', 'url', 'request_file', 'request_sha256', 'status',
                         'response_file', 'response_sha256', 'error', 'complete'},
                     'original HTTP record shape changed')
        original_id, original_response_file = issue[6]
        _native_need(type(dict.get(record, 'id')) is int and dict.get(record, 'id') == original_id
                     and type(dict.get(record, 'response_file')) is str
                     and dict.get(record, 'response_file') == original_response_file,
                     'original HTTP record value or identity changed')
        error = dict.get(record, 'error')
        _native_need(all(type(dict.get(record, name)) is str for name in ('method', 'url', 'request_file'))
                     and type(dict.get(record, 'request_sha256')) is str
                     and re.fullmatch('[0-9a-f]{64}', dict.get(record, 'request_sha256'))
                     and (dict.get(record, 'status') is None or type(dict.get(record, 'status')) is int)
                     and (dict.get(record, 'response_sha256') is None
                          or type(dict.get(record, 'response_sha256')) is str
                          and re.fullmatch('[0-9a-f]{64}', dict.get(record, 'response_sha256')))
                     and type(dict.get(record, 'complete')) is bool
                     and (error is None or type(error) is dict and all(type(key) is str for key in error)
                          and set(error) == {'type', 'errno'} and type(dict.get(error, 'type')) is str
                          and dict.get(error, 'errno') is None),
                     'original HTTP record data schema changed')
        for seal, signature in zip(self.seals, issue[3]):
            _native_need(type(seal) is _NativeCallbackSeal and seal.signature is signature,
                         'original independent callback signature changed')
            seal.checked(self.owner, self)
        if self.response is not None:
            try:
                _NativeResponseState.check_fields(self.response)
            except BaseException:
                self.refused = True
                raise

    def finalize(self, complete=False, error=None, *, primary=None, phase='raw-finalize'):
        attempted = False
        try:
            _NativeCallState.check(self)
            if self.call.done:
                return None
            attempted = True
            Call.finish(self.call, complete, error)
        except BaseException as later:
            if not attempted:
                self.refused = True
            owner = _native_call_issue(self)[5]
            _native_safe_finalization(owner, self.number, False, later, attempted)
            actual = later if primary is None else primary
            _native_safe_failure(owner, phase, actual, None if actual is later else later)
            if primary is None:
                raise
            return later
        _native_safe_finalization(_native_call_issue(self)[5], self.number, True, None, True)
        return None

    def after_error(self, primary, phase, *, observed_error=None):
        try:
            _NativeCallState.check(self)
            metadata = _native_error(primary if observed_error is None else observed_error)
        except BaseException as later:
            self.refused = True
            owner = _native_call_issue(self)[5]
            _native_safe_finalization(owner, self.number, False, later, False)
            _native_safe_failure(owner, phase, primary, later)
            return
        _NativeCallState.finalize(self, error=metadata, primary=primary, phase=phase)

    def attach(self, response):
        _NativeCallState.check(self)
        original_close = object.__getattribute__(response, 'close')
        self.pending_close = _native_invocation(original_close)
        need(type(response.status) is int)
        self.call.record['status'] = response.status
        original_read = object.__getattribute__(response, 'read')
        row = _NativeResponseState(self, response, original_read, original_close)
        state = self
        def read(*args, **kwargs):
            _native_need(type(state) is _NativeCallState, 'actual response call state required')
            row = state.response
            try:
                _native_runtime_preflight(state)
                issue = _NativeResponseState.check(row)
                original = _native_check_invocation(issue[11])
                raw = original(*args, **kwargs)
                _NativeResponseState.check(row)
                Call.append(state.call, raw)
                amount = args[0] if args else kwargs.get('amt')
                length = dict.get(object.__getattribute__(row.response, '__dict__'), 'length')
                complete = (amount is None or (type(amount) is int and amount < 0)
                            or (amount != 0 and (not raw or length == 0)))
                if complete:
                    _NativeCallState.finalize(state, complete=True)
                return raw
            except BaseException as primary:
                _native_after_error(state, primary, 'read-finalize')
                raise
        def close():
            _native_runtime_preflight(state)
            _native_need(type(state) is _NativeCallState, 'actual response call state required')
            row = state.response
            _native_need(type(row) is _NativeResponseState, 'actual registered response state required')
            frame = row.exit_frame
            pending = frame.value if type(frame) is _NativeExitFrame and frame.thread == threading.get_ident() else None
            pending_trace = frame.trace if pending is not None else None
            try:
                issue = _NativeResponseState.check(row)
            except BaseException as later:
                if pending is None:
                    raise
                _native_safe_failure(_native_call_issue(state)[5], 'close-source-guard', pending, later)
                raise BaseException.with_traceback(pending, pending_trace)
            try:
                result = _native_check_invocation(issue[12])()
            except BaseException as later:
                primary = later if pending is None else pending
                trace = _native_trace(primary)
                if primary is not later:
                    _native_safe_failure(_native_call_issue(state)[5], 'response-close', primary, later)
                _native_after_error(state, primary, 'close-finalize', observed_error=later)
                raise BaseException.with_traceback(primary, trace)
            if pending is None:
                _NativeCallState.finalize(state)
            else:
                later = _NativeCallState.finalize(state, primary=pending, phase='close-finalize')
                if later is not None:
                    raise BaseException.with_traceback(pending, pending_trace)
            return result
        row.read_seal, row.close_seal = (_NativeCallbackSeal(self.owner, self, read), _NativeCallbackSeal(self.owner, self, close))
        lock_kind = type(row.exit_lock)
        lock_namespace = type.__getattribute__(lock_kind, '__dict__')
        response_issue = (row, response, row.kind, row.read_seal, row.read_seal.signature,
                    row.close_seal, row.close_seal.signature, row.exit_lock, lock_kind,
                    __som_native_HTTP_RLOCK_ACQUIRE__, __som_native_HTTP_RLOCK_RELEASE__, row.original_read, row.original_close)
        list.append(_native_recording_issue(self.owner)['responses'], response_issue)
        _native_transaction(self, 'response-init', response_issue)
        self.response = row
        list.append(self.owner.registered, row)
        response.read, response.close = read, close
        return response


class _NativeExitDescriptor:
    """Exactly two issued non-callable descriptors. Never a response proxy."""
    def __init__(self, controller, patch):
        self.controller, self.patch = controller, patch

    def __get__(self, response, owner=None):
        _native_runtime_preflight(object.__getattribute__(self, 'controller'))
        _native_need(type(self) is _NativeExitDescriptor, 'actual owned exit descriptor required')
        controller = object.__getattribute__(self, 'controller')
        _native_need(type(controller) is _NativeRecordingState, 'actual exit controller required')
        _NativeRecordingState.pure_owner(controller)
        patch = object.__getattribute__(self, 'patch')
        issue = _NativeRecordingState.descriptor_check(controller, self, patch)
        function = _native_check_invocation(issue[3])
        return function if response is None else MethodType(function, response)


class NativeCall(Call):
    @staticmethod
    def __new__(kind, recorder, identity, number, *, recording):
        _native_need(kind is NativeCall and type(recording) is _NativeRecordingState
                     and recording.recorder is recorder, 'original native call allocation owner required')
        return _native_transaction(recording, 'new-call', (kind, identity, number))

    def __init__(self, recorder, identity, number, *, recording):
        _native_transaction(self, 'begin-call')
        _native_need(type(recording) is _NativeRecordingState, 'actual call constructor owner required')
        _NativeRecordingState.guard(recording)
        super().__init__(recorder, identity, number)
        state = _NativeCallState(recording, self, number)
        self._native_state, self._native_callbacks = state, state.callbacks
        list.append(recording.issued_calls, state)
        issue = (state, state.callbacks, state.seals, tuple(seal.signature for seal in state.seals), self, recording,
                 (number, f'calls/{number:06d}.response.raw'))
        list.append(_native_recording_issue(recording)['calls'], issue)
        state._native_call_record = issue
        _native_transaction(recording, 'call-init', issue)
        dict.__setitem__(object.__getattribute__(_NATIVE_ISSUER, 'calls'), id(state), issue)

    def _finalize(self, complete=False, error=None, *, primary=None, phase='raw-finalize'):
        state = _native_call_state(self)
        return state.seals[0].checked(state.owner, state)(complete, error, primary=primary, phase=phase)

    def _after_error(self, primary, phase, *, observed_error=None):
        state = _native_call_state(self)
        return state.seals[1].checked(state.owner, state)(primary, phase, observed_error=observed_error)

    def finish(self, complete=False, error=None):
        return self._finalize(complete, error)

    def attach(self, response):
        state = _native_call_state(self)
        return state.seals[2].checked(state.owner, state)(response)


def _native_call_state(call):
    _native_need(type(call) is NativeCall, 'actual source-owned call required')
    state = dict.get(object.__getattribute__(call, '__dict__'), '_native_state')
    _native_need(type(state) is _NativeCallState and state.call is call, 'actual constructor-issued call state required')
    _NativeCallState.check(state)
    return state


def _native_call_callbacks(call):
    state = _native_call_state(call)
    return tuple(seal.checked(state.owner, state) for seal in state.seals)


def _native_recording_operations(recorder):
    _native_need(type(recorder) is NativeRecorder, 'actual source-owned recorder required')
    state = dict.get(object.__getattribute__(recorder, '__dict__'), '_native_state')
    _native_need(type(state) is _NativeRecordingState and state.recorder is recorder, 'actual constructor-issued recorder state required')
    _native_runtime_preflight(state)
    _NativeRecordingState.guard(state)
    return state.operations


class _NativeRecordingState:
    def __init__(self, recorder, initial):
        self.recorder, self.initial = recorder, initial
        self.active, self.installed, self.thread = True, False, threading.get_ident()
        self.failures, self.finalizations, self.finishers, self.registered = [], [], [], []
        self.patches, self.exit_events, self.wrapper_snapshots, self.issued_calls = [], [], [], []
        self.calls, self.errors = recorder.calls, recorder.errors
        self.original_open = urllib.request.AbstractHTTPHandler.do_open
        self.http_error_kind, self.closer_kind = urllib.error.HTTPError, tempfile._TemporaryFileCloser
        self.source_snapshot = _native_class_capture(__som_native_SourceBytes__)
        self.lease_snapshot = _native_class_capture(type(initial[3]))
        self.borrow = None
        recorder.native_failures, recorder.native_finalizations = self.failures, self.finalizations
        self.expected = {name: object.__getattribute__(recorder, '__dict__')[name] for name in
                         ('out', 'ports', 'lock', 'calls', 'errors', 'native_failures', 'native_finalizations', '_native_origin')}
        self.operations = (self.guard, self.enter, self.restore_exits, self.release, self.failures, self.finalizations,
                           self.finishers, self.original_open, self.exit_events, self.finish_all, self.capture_borrow)
        namespace = type.__getattribute__(_NativeRecordingState, '__dict__')
        record = {'state': self, 'calls': [], 'exits': [], 'responses': [],
                  'source_snapshot': self.source_snapshot, 'lease_snapshot': self.lease_snapshot, 'borrow': None,
                  'core_handles': tuple((handle, object.__getattribute__(handle, '_domain'),
                      object.__getattribute__(handle, '_owns')) for handle in (initial[0], initial[3])),
                  'operation_signatures': tuple(_native_invocation(self.operations[index]) for index in (0, 1, 2, 3, 9, 10)),
                  'sinks': {name: _native_invocation(namespace[name]) for name in ('failure', 'finalization')},
                  'call_after_error': _native_invocation(type.__getattribute__(_NativeCallState, '__dict__')['after_error']),
                  'failures': self.failures, 'finalizations': self.finalizations, 'errors': self.errors}
        self._native_record = record
        _native_transaction(self, 'recording-init', record)
        dict.__setitem__(object.__getattribute__(_NATIVE_ISSUER, 'recordings'), id(self), record)

    def pure_owner(self):
        _native_need(type(self) is _NativeRecordingState, 'actual recording state required')
        controls, ctx, sources, lease, authority, path, raw = self.initial
        record = _native_recording_issue(self)
        _native_class_check(record['lease_snapshot'])
        for handle, domain, owns in record['core_handles']:
            _native_need(object.__getattribute__(handle, '_domain') is domain
                         and object.__getattribute__(handle, '_owns') is owns
                         and object.__getattribute__(handle, '_closed') is False,
                         'original HTTP Core handle owner changed')
        namespace = ModuleType.__getattribute__(__som_native_context_module__, '__dict__')
        values = object.__getattribute__(ctx, '__dict__')
        _native_need(all(type(key) is str for key in namespace)
                     and namespace.get('_ACTIVE') is ctx and type(ctx) is __som_native_Context__
                     and type(values) is dict and all(type(key) is str for key in values)
                     and dict.get(values, 'sources') is sources
                     and dict.get(values, 'qa_import_controls') is lease and dict.get(values, 'qa_source_authority') is authority
                     and dict.get(values, 'path') is path and dict.get(values, 'raw') is raw,
                     'original HTTP owner pointer changed')
        _native_class_check(record['source_snapshot'])
        fields = object.__getattribute__(sources, '__dict__')
        _native_need(type(sources) is __som_native_SourceBytes__ and type(fields) is dict
                     and all(type(key) is str for key in fields)
                     and set(fields) == {'root', 'revision', 'pins'}
                     and type(fields['root']) is type(controls.root) and fields['root'] == controls.root
                     and type(fields['revision']) is str and fields['revision'] == controls.helper_revision
                     and type(fields['pins']) is dict and all(type(key) is str and type(value) is str
                         and re.fullmatch('[0-9a-f]{64}', value) for key, value in fields['pins'].items()),
                     'original SourceBytes instance dispatch changed')

    def guard(self):
        _NativeRecordingState.pure_owner(self)
        current = _native_callback_guard()
        _native_need(all(left is right for left, right in zip(current[:5], self.initial[:5]))
                     and current[5] == self.initial[5] and current[6] is self.initial[6], 'original HTTP owner pointer changed')
        values = object.__getattribute__(self.recorder, '__dict__')
        _native_need(self.active and type(values) is dict and all(type(key) is str for key in values)
                     and set(values) == set(self.expected) | {'total_bytes', 'original_open', '_native_operations', '_native_state'}
                     and all(values.get(key) is value for key, value in self.expected.items())
                     and type(values.get('total_bytes')) is int and values.get('_native_state') is self
                     and values.get('_native_operations') is self.operations
                     and (values.get('original_open') is None or values.get('original_open') is self.original_open),
                     'original HTTP recorder fields changed')
        for signature in _native_recording_issue(self)['operation_signatures']:
            _native_check_invocation(signature)
        if self.installed:
            _NativeRecordingState.verify_exits(self)

    def failure(self, phase, primary, secondary=None):
        if secondary is primary:
            return
        record = _native_recording_issue(self, cleanup=True)
        list.append(record['failures'], (phase, primary, _native_trace(primary), secondary))
        list.append(record['errors'], 'native_finalize_failed')
        if secondary is not None:
            namespace = BaseException.__dict__['__dict__'].__get__(primary, BaseException)
            previous = dict.get(namespace, '__som_native_http_callback_secondary__', ())
            _native_secondary(primary, '__som_native_http_callback_secondary__',
                              (*previous, secondary) if type(previous) is tuple else (secondary,))

    def finalization(self, number, saved, error=None, attempted=True):
        row = {'call_id': number, 'attempted': attempted, 'saved': saved}
        if error is not None:
            row['error'] = _native_error(error)
        list.append(_native_recording_issue(self, cleanup=True)['finalizations'], row)

    def capture_borrow(self, lease, controls):
        _native_need(self.borrow is None and type(lease) is type(controls), 'one actual nonowning borrow required')
        _native_need(object.__getattribute__(lease, '_domain') is object.__getattribute__(controls, '_domain')
                     and object.__getattribute__(lease, '_owns') is False and object.__getattribute__(lease, '_closed') is False,
                     'actual same-owner nonowning borrow required')
        cls = type(lease)
        namespace = type.__getattribute__(cls, '__dict__')
        self.borrow = (lease, _native_class_capture(cls), object.__getattribute__(lease, '_domain'),
                       _native_invocation(MethodType(namespace['verify_installed'], lease)),
                       _native_invocation(MethodType(namespace['close'], lease)))
        _native_recording_issue(self)['borrow'] = self.borrow
        _native_transaction(self, 'borrow-init', self.borrow)
        return self.verify_borrow, self.close_borrow

    def verify_borrow(self):
        original = _native_recording_issue(self, cleanup=True)['borrow']
        _native_need(self.borrow is original and type(original) is tuple, 'original nonowning borrow capture changed')
        lease, snapshot, domain, verify, close = original
        _native_class_check(snapshot)
        _native_need(object.__getattribute__(lease, '_domain') is domain and object.__getattribute__(lease, '_owns') is False
                     and object.__getattribute__(lease, '_closed') is False, 'original nonowning borrow changed')
        _native_check_invocation(verify)()

    def close_borrow(self):
        original = _native_recording_issue(self, cleanup=True)['borrow']
        _native_need(self.borrow is original and type(original) is tuple, 'original nonowning borrow capture changed')
        lease, snapshot, domain, verify, close = original
        _native_class_check(snapshot)
        _native_need(object.__getattribute__(lease, '_domain') is domain and object.__getattribute__(lease, '_owns') is False
                     and type(object.__getattribute__(lease, '_closed')) is bool, 'original nonowning borrow changed')
        _native_check_invocation(close)()

    def finish_all(self):
        problems = []
        for issue in tuple(_native_recording_issue(self, cleanup=True)['calls']):
            reported_before = len(_native_recording_issue(self, cleanup=True)['finalizations'])
            try:
                state = issue[0]
                _native_need(type(state) is _NativeCallState and state.owner is self, 'original finalizer owner changed')
                _native_need(state.callbacks is issue[1] and state.seals is issue[2]
                             and state.seals[0].signature is issue[3][0], 'original independent finalizer signature changed')
                _native_check_invocation(issue[3][0])()
            except BaseException as error:
                # The actual finalizer records its own attempted write failure.
                # Record an outer refusal only when no finalization was reached.
                if len(_native_recording_issue(self, cleanup=True)['finalizations']) == reported_before:
                    _native_safe_finalization(self, state.number, False, error, False)
                _native_safe_failure(self, 'finish-http-call', error)
                problems.append(error)
        if problems:
            _native_secondary(problems[0], '__som_native_http_finish_secondary__', problems[1:])
            raise problems[0]

    def class_snapshot(self, kind):
        _native_need(type(kind) is type or type(kind) is abc.ABCMeta, 'response class metaclass differs')
        return _native_class_capture(kind)

    def verify_snapshot(self, kind, snapshot, installed=None):
        original, mro, namespaces, functions = snapshot
        _native_need(kind is original and type.__getattribute__(kind, '__mro__') is mro, 'response class MRO changed')
        for base, expected in namespaces:
            current = type.__getattribute__(base, '__dict__')
            expected = dict(expected)
            expected = {**expected, '__exit__': installed} if base is kind and installed is not None else expected
            _native_need(len(current) == len(expected) and all(current.get(key) is value for key, value in expected.items()),
                         'response class namespace changed')
        for signature in functions:
            _native_check_invocation(signature)

    def descriptor_check(self, descriptor, patch):
        matches = [row for row in _native_recording_issue(self)['exits'] if row[0] is descriptor]
        _native_need(len(matches) == 1, 'exit descriptor was not issued by original constructor')
        issue = matches[0]
        _native_need(type(descriptor) is _NativeExitDescriptor and type(patch) is dict
                     and patch is issue[1] and all(type(key) is str for key in patch)
                     and len(patch) == len(issue[4])
                     and all(dict.get(patch, key) is value for key, value in issue[4])
                     and patch.get('descriptor') is descriptor
                     and object.__getattribute__(descriptor, 'controller') is self
                     and object.__getattribute__(descriptor, 'patch') is patch,
                     'original constructor-issued exit descriptor changed')
        _native_class_check(patch['descriptor_class'])
        _native_need(type(issue[2]) is _NativeCallbackSeal and issue[2].signature is issue[3],
                     'independent exit callback signature changed')
        _native_check_invocation(issue[3])
        _native_need(type.__getattribute__(patch['kind'], '__dict__').get('__exit__') is descriptor,
                     'owned response exit field changed')
        return issue

    def verify_exits(self):
        for patch in self.patches:
            _NativeRecordingState.descriptor_check(self, patch['descriptor'], patch)
            _NativeRecordingState.verify_snapshot(self, patch['kind'], patch['snapshot'], patch['descriptor'])
            module_values = ModuleType.__getattribute__(patch['module'], '__dict__')
            _native_need(module_values.get(type.__getattribute__(patch['kind'], '__name__')) is patch['kind']
                         and module_values.get('__file__') == str(patch['path'])
                         and read_regular(patch['path']) == patch['raw'], 'original response stdlib source changed')
        for kind, snapshot, module, path, raw in self.wrapper_snapshots:
            _NativeRecordingState.verify_snapshot(self, kind, snapshot)
            namespace = ModuleType.__getattribute__(module, '__dict__')
            _native_need(namespace.get(type.__getattribute__(kind, '__name__')) is kind
                         and namespace.get('__file__') == str(path) and read_regular(path) == raw,
                         'HTTPError wrapper stdlib source changed')

    def response_row(self, response):
        for row in self.registered:
            if row.response is response:
                _NativeResponseState.check(row)
                return row
        if type(response) is self.http_error_kind:
            namespace = BaseException.__dict__['__dict__'].__get__(response, BaseException)
            inner, closer = dict.get(namespace, 'fp'), dict.get(namespace, '_closer')
            _native_need(dict.get(namespace, 'file') is inner and 'close' not in namespace
                         and type(closer) is self.closer_kind, 'HTTPError real file fields differ')
            values = object.__getattribute__(closer, '__dict__')
            _native_need(type(values) is dict and all(type(key) is str and key in {
                'file', 'name', 'delete', 'delete_on_close', 'cleanup_called', 'close_called'} for key in values)
                and values.get('file') is inner and values.get('delete') is False
                and values.get('name') == '<urllib response>' and type(values.get('delete_on_close')) is bool
                and all(type(values[key]) is bool for key in ('cleanup_called', 'close_called') if key in values)
                and type.__getattribute__(self.closer_kind, '__dict__').get('cleanup_called') is False
                and type.__getattribute__(self.closer_kind, '__dict__').get('close_called') is False,
                'HTTPError real closer file differs')
            for row in self.registered:
                if row.response is inner:
                    _NativeResponseState.check(row)
                    return row
        return None

    def install_exits(self):
        _NativeRecordingState.guard(self)
        _native_need(threading.get_ident() == self.thread and not self.installed and not self.patches,
                     'owned response exit installation reentry')
        for kind, module in ((http.client.HTTPResponse, http.client), (urllib.response.addbase, urllib.response)):
            source_path = Path(ModuleType.__getattribute__(module, '__dict__')['__file__'])
            _native_need(source_path.is_absolute() and source_path.is_relative_to(Path(sys.base_prefix)),
                         'response class source is outside actual stdlib')
            raw = read_regular(source_path)
            _native_stdlib_class(kind, module, raw, source_path)
            namespace = dict(type.__getattribute__(kind, '__dict__'))
            original = next(type.__getattribute__(base, '__dict__')['__exit__']
                            for base in type.__getattribute__(kind, '__mro__')
                            if '__exit__' in type.__getattribute__(base, '__dict__'))
            patch = {'kind': kind, 'original': original, 'namespace': tuple(namespace.items()),
                     'snapshot': _NativeRecordingState.class_snapshot(self, kind),
                     'module': module, 'path': source_path, 'raw': raw}
            state = self
            # This closure holds one opaque constructor-issued state object.
            # The distinct patch object is retained by its fixed descriptor.
            exit_state = _NativeExitState(self, patch)
            def make_exit(state):
                def owned_exit(response, et, value, trace):
                    try:
                        _native_runtime_preflight(state.owner)
                        _native_need(type(state) is _NativeExitState, 'actual exit state required')
                        return _NativeExitState.exit(state, response, et, value, trace)
                    except BaseException as later:
                        if value is None:
                            raise
                        try:
                            _native_safe_failure(state.owner, 'response-exit-dispatch', value, later)
                        except BaseException as sink_error:
                            _native_secondary(value, '__som_native_http_exit_sink_secondary__', (later, sink_error))
                        _native_secondary(value, '__som_native_http_exit_dispatch_secondary__', (later,))
                        raise BaseException.with_traceback(value, trace)
                return owned_exit
            function = make_exit(exit_state)
            seal = _NativeCallbackSeal(self, patch, function)
            descriptor = _NativeExitDescriptor(self, patch)
            patch['descriptor'], patch['descriptor_class'] = descriptor, _native_class_capture(_NativeExitDescriptor)
            list.append(self.patches, patch)
            exit_issue = (descriptor, patch, seal, seal.signature, tuple(patch.items()), exit_state, original)
            list.append(_native_recording_issue(self)['exits'], exit_issue)
            _native_transaction(self, 'exit-init', exit_issue)
            event = {'class': type.__getattribute__(kind, '__module__') + '.' + type.__getattribute__(kind, '__name__'),
                'field': '__exit__', 'original_own_field': '__exit__' in namespace,
                'stdlib_source': str(source_path), 'stdlib_source_sha256': digest(raw),
                'installed': False, 'restored': False, 'postcheck_passed': False, 'foreign_field_preserved': False}
            list.append(self.exit_events, event)
            type.__setattr__(kind, '__exit__', descriptor)
            event['installed'] = type.__getattribute__(kind, '__dict__').get('__exit__') is descriptor
        self.installed = True
        for kind, module in ((self.http_error_kind, urllib.error), (self.closer_kind, tempfile)):
            path = Path(ModuleType.__getattribute__(module, '__dict__')['__file__'])
            _native_need(path.is_absolute() and path.is_relative_to(Path(sys.base_prefix)), 'HTTPError wrapper source outside stdlib')
            self.wrapper_snapshots.append((kind, _NativeRecordingState.class_snapshot(self, kind), module, path, read_regular(path)))
        _NativeRecordingState.verify_exits(self)

    def restore_exits(self):
        problems = []
        _native_need(threading.get_ident() == self.thread, 'owned response restoration thread differs')
        for row in self.registered:
            if row.exit_frame is not None:
                problems.append(ValueError('owned response exit remains active'))
        issued = _native_recording_issue(self, cleanup=True)['exits']
        for index in reversed(range(len(issued))):
            issue, event = issued[index], self.exit_events[index]
            patch = dict(issue[4])
            try:
                current = type.__getattribute__(patch['kind'], '__dict__')
                event['foreign_field_preserved'] = current.get('__exit__') is not patch['descriptor']
                _native_need(not event['foreign_field_preserved'], 'foreign response exit field kept')
                original_namespace = dict(patch['namespace'])
                if '__exit__' in original_namespace:
                    type.__setattr__(patch['kind'], '__exit__', original_namespace['__exit__'])
                else:
                    type.__delattr__(patch['kind'], '__exit__')
                event['restored'] = True
                _NativeRecordingState.verify_snapshot(self, patch['kind'], patch['snapshot'])
                _native_need(read_regular(patch['path']) == patch['raw'], 'response stdlib source changed at close')
                event['postcheck_passed'] = True
            except BaseException as error:
                problems.append(error)
        self.installed = False
        if problems:
            _native_secondary(problems[0], '__som_native_http_exit_secondary__', problems[1:])
            raise problems[0]

    def enter(self):
        _NativeRecordingState.guard(self)
        _NativeRecordingState.install_exits(self)
        self.recorder.original_open = self.original_open
        state = self
        def recording_open(handler, http_class, request, **kwargs):
            _native_runtime_preflight(state)
            _native_need(type(state) is _NativeRecordingState, 'actual original recording state required')
            return _NativeRecordingState.opened(state, handler, http_class, request, **kwargs)
        self.open_seal = _NativeCallbackSeal(self, self, recording_open)
        open_issue = (self.open_seal, self.open_seal.signature)
        _native_recording_issue(self)['open'] = open_issue
        _native_transaction(self, 'open-init', open_issue)
        urllib.request.AbstractHTTPHandler.do_open = recording_open
        return recording_open

    def opened(self, handler, http_class, request, **kwargs):
        _NativeRecordingState.guard(self)
        issued_seal, signature = _native_recording_issue(self)['open']
        _native_need(self.open_seal is issued_seal and issued_seal.signature is signature, 'original recording open seal changed')
        _native_check_invocation(signature)
        try:
            identity = request_identity(request, self.recorder.ports)
            recorder = self.recorder
            recorder_lock = dict.get(object.__getattribute__(recorder, '__dict__'), 'lock')
            call_kind = NativeCall
            call_namespace = type.__getattribute__(call_kind, '__dict__')
            call_wrapper = call_namespace.get('__new__')
            _native_need(type(call_wrapper) is staticmethod, 'original static call constructor required')
            new_signature = _native_invocation(object.__getattribute__(call_wrapper, '__func__'))
            init_signature = _native_invocation(call_namespace['__init__'])
            _native_transaction(self, 'open-constructor-check',
                (recorder, recorder_lock, call_kind, call_wrapper, new_signature, init_signature))
            with recorder_lock:
                need(len(self.calls) < MAX_CALLS)
                call = _native_check_invocation(new_signature)(call_kind, recorder, identity, len(self.calls) + 1, recording=self)
                _native_check_invocation(init_signature)(call, recorder, identity, len(self.calls) + 1, recording=self)
                state = object.__getattribute__(call, '__dict__')['_native_state']
                finalize = state.seals[0].checked(self, state)
                signature = (finalize.__code__, finalize.__globals__, finalize.__defaults__, finalize.__kwdefaults__, finalize.__closure__)
                list.append(self.calls, call)
                list.append(self.finishers, (call, finalize, signature))
        except RecordingError:
            list.append(self.errors, 'request_refused')
            raise
        try:
            response = self.original_open(handler, http_class, request, **kwargs)
        except BaseException as primary:
            _native_after_error(state, primary, 'transport-finalize')
            raise
        try:
            return state.seals[2].checked(self, state)(response)
        except BaseException as primary:
            _native_after_error(state, primary, 'attach-finalize')
            try:
                _NativeRecordingState.guard(self)
                if state.pending_close is not None:
                    _native_check_invocation(state.pending_close)()
            except BaseException as secondary:
                _native_safe_failure(self, 'attach-response-close', primary, secondary)
            raise

    def release(self):
        _native_transaction(self, 'recording-release')
        self.active = False


class _NativeExitState:
    def __init__(self, owner, patch):
        self.owner, self.patch, self.original = owner, patch, patch['original']

    def exit(self, response, et, value, trace):
        row = frame = None
        acquired, escaping = False, None
        failure_phase = 'response-exit'
        try:
            _native_need(type(self) is _NativeExitState and type(self.owner) is _NativeRecordingState,
                         'actual constructor-issued exit state required')
            _native_need(value is None and et is None and trace is None or _native_is_error(value)
                         and et is type(value) and (trace is None or type(trace) is TracebackType),
                         'explicit response exit arguments differ')
            failure_phase = 'close-source-guard'
            _NativeRecordingState.guard(self.owner)
            failure_phase = 'response-exit'
            issue = _NativeRecordingState.descriptor_check(self.owner, self.patch['descriptor'], self.patch)
            _native_need(issue[5] is self and self.original is issue[6], 'original exit state or delegate changed')
            row = _NativeRecordingState.response_row(self.owner, response)
            if row is not None:
                lock_issue = next(item for item in _native_recording_issue(self.owner)['responses'] if item[0] is row)
                acquired = lock_issue[9](lock_issue[7], False)
                _native_need(acquired and row.exit_frame is None, 'owned response exit reentry or another thread')
                frame = _NativeExitFrame(row.response, threading.get_ident(), et, value, trace)
                row.exit_frame = frame
            original = self.original
            # All descriptor binding and delegation stay inside this protected try.
            if type(original) is FunctionType:
                delegate = FunctionType.__get__(original, response, type(response))
            else:
                _native_need(original is _io._IOBase.__dict__['__exit__'], 'original builtin response exit changed')
                delegate = type(original).__get__(original, response, type(response))
            return delegate(et, value, trace)
        except BaseException as later:
            primary = later if value is None else value
            escaping = primary
            if primary is not later:
                _native_safe_failure(self.owner, failure_phase, primary, later)
            raise BaseException.with_traceback(primary, _native_trace(later) if value is None else trace)
        finally:
            if row is not None and acquired:
                try:
                    if frame is not None:
                        _native_need(row.exit_frame is frame, 'owned response exit token changed')
                        row.exit_frame = None
                except BaseException as later:
                    primary = escaping if escaping is not None else value
                    if primary is None:
                        raise
                    _native_safe_failure(self.owner, 'response-exit-token', primary, later)
                finally:
                    lock_issue[10](lock_issue[7])


class NativeRecorder(Recorder):
    @staticmethod
    def __new__(kind, out, ports=(18917, 18919), *, initial_ctx):
        from _som_native_active_controls_binding import controls
        controls.verify_installed()
        frame_get = __som_native_HTTP_FRAME_GET__
        object_new = __som_native_HTTP_OBJECT_NEW__
        gen_send, gen_close = __som_native_HTTP_GEN_SEND__, __som_native_HTTP_GEN_CLOSE__
        rlock_new, rlock_owned = __som_native_HTTP_RLOCK_NEW__, __som_native_HTTP_RLOCK_OWNED__
        rlock_acquire, rlock_release = __som_native_HTTP_RLOCK_ACQUIRE__, __som_native_HTTP_RLOCK_RELEASE__
        constructor_frame = frame_get(0)
        entry = constructor_frame.f_back
        anchors = entry.f_locals
        signature = anchors.get('_native_constructor_signature')
        wrapper = anchors.get('_native_constructor_wrapper')
        _native_need(kind is __class__ and kind is NativeRecorder and kind is anchors.get('_native_constructor_kind')
                     and type(wrapper) is staticmethod and type(signature) is tuple
                     and signature[0] is object.__getattribute__(wrapper, '__func__')
                     and signature[3] is constructor_frame.f_code
                     and signature[4] is constructor_frame.f_globals is globals()
                     and signature[5] is constructor_frame.f_builtins,
                     'native recorder needs its original held constructor')
        _native_check_invocation(signature)
        entry_signature = anchors.get('_native_entry_signature')
        _native_need(type(entry_signature) is tuple and entry_signature[3] is entry.f_code
                     and entry_signature[4] is entry.f_globals and entry_signature[5] is entry.f_builtins
                     and entry.f_code.co_name == '_native_run_checker'
                     and entry.f_globals.get('__name__') in ('__main__', '_som_m6_fixture_http_call'),
                     'native recorder needs the actual entry or direct fixture caller')
        _native_check_invocation(entry_signature)
        _native_class_check(anchors['_native_constructor_snapshot'])
        classes = anchors['_native_constructor_classes']
        snapshots = anchors['_native_constructor_snapshots']
        compatibility_issuer = anchors['_native_constructor_issuer']
        _native_need(type(classes) is tuple and type(snapshots) is tuple
                     and len(classes) == len(snapshots) == 10
                     and all(snapshot[0] is cls for cls, snapshot in zip(classes, snapshots)),
                     'original HTTP source class anchors differ')
        for name, value in (('_native_frame_get', frame_get), ('_native_object_new', object_new),
            ('_native_gen_send', gen_send), ('_native_gen_close', gen_close), ('_native_rlock_new', rlock_new),
            ('_native_rlock_owned', rlock_owned), ('_native_rlock_acquire', rlock_acquire),
            ('_native_rlock_release', rlock_release)):
            _native_need(anchors.get(name) is value, 'entry and owner fixed native values differ')

        def acquire(constructor_frame, kind, out, ports, initial_ctx, compatibility_issuer, classes, snapshots,
                    frame_get, object_new, gen_send, gen_close, rlock_new, rlock_owned, rlock_acquire, rlock_release):
            from _som_native_active_controls_binding import controls
            controls.verify_installed()
            for name, value in (('__som_native_HTTP_FRAME_GET__', frame_get),
                ('__som_native_HTTP_OBJECT_NEW__', object_new), ('__som_native_HTTP_GEN_SEND__', gen_send),
                ('__som_native_HTTP_GEN_CLOSE__', gen_close), ('__som_native_HTTP_RLOCK_NEW__', rlock_new),
                ('__som_native_HTTP_RLOCK_OWNED__', rlock_owned), ('__som_native_HTTP_RLOCK_ACQUIRE__', rlock_acquire),
                ('__som_native_HTTP_RLOCK_RELEASE__', rlock_release)):
                _native_need(globals().get(name) is value, 'acquisition native argument differs from the fixed handoff')
            creator_frame = frame_get(0)
            acquisition = constructor_frame.f_locals.get('acquisition')
            _native_need(creator_frame.f_back is constructor_frame
                         and type(acquisition) is FunctionType and acquisition.__code__ is creator_frame.f_code
                         and acquisition.__globals__ is creator_frame.f_globals is globals()
                         and sum(item is creator_frame.f_code for item in constructor_frame.f_code.co_consts) == 1,
                         'literal HTTP acquisition needs its actual creator')
            _native_check_invocation(constructor_frame.f_locals['acquisition_signature'])

            def produce(creator_frame, constructor_frame, kind, out, ports, initial_ctx,
                        compatibility_issuer, classes, snapshots, frame_get, object_new, gen_send, gen_close,
                        rlock_new, rlock_owned, rlock_acquire, rlock_release):
                capture_count, send_count = 0, 0
                phase = 'unprimed'
                # This first resume precedes private-lock allocation. Existing
                # public Core verification checks the early source/bound
                # identities before a supplied native argument can be called.
                from _som_native_active_controls_binding import controls
                controls.verify_installed()
                for name, value in (('__som_native_HTTP_FRAME_GET__', frame_get),
                    ('__som_native_HTTP_OBJECT_NEW__', object_new), ('__som_native_HTTP_GEN_SEND__', gen_send),
                    ('__som_native_HTTP_GEN_CLOSE__', gen_close), ('__som_native_HTTP_RLOCK_NEW__', rlock_new),
                    ('__som_native_HTTP_RLOCK_OWNED__', rlock_owned), ('__som_native_HTTP_RLOCK_ACQUIRE__', rlock_acquire),
                    ('__som_native_HTTP_RLOCK_RELEASE__', rlock_release)):
                    _native_need(globals().get(name) is value, 'producer native argument differs from the fixed handoff')
                own_frame = frame_get(0)
                producer_code = own_frame.f_code
                producer = creator_frame.f_locals.get('producer')
                _native_need(own_frame.f_back is creator_frame
                             and type(producer) is FunctionType and producer.__code__ is producer_code
                             and producer.__globals__ is own_frame.f_globals is globals()
                             and sum(item is producer_code for item in creator_frame.f_code.co_consts) == 1,
                             'literal HTTP producer needs its actual first sender')
                _native_check_invocation(creator_frame.f_locals['producer_signature'])
                for snapshot in snapshots:
                    _native_class_check(snapshot)
                generator_kind, rlock_kind = _native_origin_types(frame_get, object_new, gen_send, gen_close,
                    rlock_new, rlock_owned, rlock_acquire, rlock_release)
                state_kind = next(cls for cls in classes if type.__getattribute__(cls, '__name__') == '_NativeRecordingState')
                issuer_kind = next(cls for cls in classes if type.__getattribute__(cls, '__name__') == '_NativeIssuer')
                call_kind = next(cls for cls in classes if type.__getattribute__(cls, '__name__') == 'NativeCall')
                recorder, state, issuer = object_new(kind), object_new(state_kind), object_new(issuer_kind)
                lock = rlock_new(rlock_kind)
                _native_need(type(lock) is rlock_kind, 'native RLock construction returned another kind')
                object.__setattr__(recorder, '_native_state', state)
                object.__setattr__(state, 'recorder', recorder)
                pure_names = ('_native_need', '_native_invocation', '_native_check_invocation',
                              '_native_class_capture', '_native_class_check', '_native_origin_types')
                pure_functions = tuple((name, _native_invocation(globals()[name])) for name in pure_names)
                source_functions = tuple(_native_invocation(globals()[name]) for name in (
                    '_native_safe_failure', '_native_safe_finalization', '_native_after_error',
                    '_native_recording_operations', '_native_call_state', '_native_transaction'))
                transaction_signature = source_functions[-1]
                recording = None
                recording_fields, recording_lists = (), ()
                recorder_fields, operations, initial_facts = (), None, None
                calls, responses, exits, allocated_calls = (), (), (), ()
                capture_count, phase = 1, 'allocated'
                try:
                    request = yield recorder
                    while True:
                        try:
                            send_count += 1
                            operation, subject, candidate = request
                            sender = frame_get(0).f_back
                            _native_need(sender is not None and sender.f_globals is globals()
                                         and sender.f_code is transaction_signature[3]
                                         and sender.f_globals is transaction_signature[4]
                                         and sender.f_locals.get('subject') is subject
                                         and sender.f_locals.get('operation') is operation,
                                         'private HTTP producer sender differs')
                            source_frame = sender.f_back
                            for unused in range(4):
                                if source_frame.f_code.co_name not in ('_native_origin_view', '_native_recording_issue',
                                                                      '_native_call_issue', '_native_runtime_preflight'):
                                    break
                                source_frame = source_frame.f_back
                            # Check original function objects before invoking their
                            # pure source bodies. No current global is resealed.
                            for name, original in pure_functions:
                                function = original[2]
                                if (globals().get(name) is not original[0]
                                        or type(function) is not FunctionType
                                        or function.__code__ is not original[3]
                                        or function.__globals__ is not original[4]
                                        or function.__builtins__ is not original[5]
                                        or function.__defaults__ is not original[6]
                                        or function.__kwdefaults__ is not original[7]
                                        or function.__closure__ is not original[8]):
                                    raise ValueError('original private HTTP pure helper changed')
                                _native_check_invocation(original)
                            for cls, snapshot in zip(classes, snapshots):
                                _native_need(globals().get(type.__getattribute__(cls, '__name__')) is cls,
                                             'original HTTP source class export changed')
                                _native_class_check(snapshot)
                            signatures = (tuple(signature for snapshot in snapshots for signature in snapshot[3])
                                          + source_functions
                                          + tuple(signature for issue, fields in calls for signature in issue[3])
                                          + tuple(signature for issue in responses for signature in (issue[4], issue[6]))
                                          + tuple(issue[3] for issue in exits)
                                          + tuple(value[1] for key, value in recording_fields if key == 'open'))
                            _native_need(source_frame is not None and source_frame.f_globals is globals()
                                         and any(signature[3] is source_frame.f_code and signature[4] is source_frame.f_globals
                                                 for signature in signatures),
                                         'private HTTP operation lacks its original source caller')
                            cleanup = operation in ('safe-recording', 'safe-call', 'recording-failed', 'recording-release')
                            if not cleanup:
                                _native_need(_NATIVE_ISSUER is compatibility_issuer,
                                             'original HTTP compatibility issuer was replaced')
                            result = None
                            if operation == 'begin-recording':
                                _native_need(subject is recorder and phase == 'allocated'
                                             and source_frame.f_code.co_qualname == 'NativeRecorder.__init__',
                                             'native recorder initialization is not fresh')
                                phase = 'initializing'
                            elif operation == 'recording-failed':
                                _native_need(subject is recorder, 'native recorder failure receiver differs')
                                phase = 'failed'
                            elif operation == 'recording-init':
                                _native_need(subject is state and phase == 'initializing' and recording is None
                                             and source_frame.f_code.co_qualname == '_NativeRecordingState.__init__'
                                             and source_frame.f_locals.get('record') is candidate,
                                             'native recording facts lack their actual constructor')
                                _native_need(type(candidate) is dict and candidate.get('state') is state,
                                             'fresh native recording data view differs')
                                recording = candidate
                                initial_facts = object.__getattribute__(state, 'initial')
                                _native_need(type(initial_facts) is tuple and len(initial_facts) == 7
                                             and initial_facts[1] is initial_ctx,
                                             'native recording did not retain its original Context')
                                expected_recorder = object.__getattribute__(state, 'expected')
                                _native_need(type(expected_recorder) is dict
                                             and all(type(key) is str for key in expected_recorder),
                                             'fresh native recorder fields differ')
                                recorder_fields = tuple(expected_recorder.items())
                                operations = object.__getattribute__(state, 'operations')
                                recording_fields = tuple((key, tuple(value.items()) if key == 'sinks' else value)
                                                         for key, value in candidate.items()
                                                         if key not in ('calls', 'responses', 'exits'))
                                recording_lists = tuple((key, candidate[key]) for key in ('calls', 'responses', 'exits'))
                                _native_need(all(type(value) is list and not value for key, value in recording_lists),
                                             'new native recording view is not empty')
                            else:
                                _native_need(recording is not None and phase in ('initializing', 'active', 'released', 'failed'),
                                             'native recording has no original private issue')
                                if not cleanup:
                                    _native_need(object.__getattribute__(state, 'initial') is initial_facts,
                                                 'original native Context tuple was replaced')
                                    expected_recorder = object.__getattribute__(state, 'expected')
                                    actual_recorder = object.__getattribute__(recorder, '__dict__')
                                    _native_need(type(expected_recorder) is dict
                                                 and len(expected_recorder) == len(recorder_fields)
                                                 and all(type(key) is str for key in expected_recorder)
                                                 and all(dict.get(expected_recorder, key) is value for key, value in recorder_fields)
                                                 and type(actual_recorder) is dict
                                                 and all(type(key) is str for key in actual_recorder)
                                                 and all(dict.get(actual_recorder, key) is value for key, value in recorder_fields)
                                                 and dict.get(actual_recorder, '_native_state') is state,
                                                 'original HTTP recorder field candidates changed')
                                    if phase in ('active', 'released') or operation == 'recording-active':
                                        _native_need(object.__getattribute__(state, 'operations') is operations
                                                     and dict.get(actual_recorder, '_native_operations') is operations,
                                                     'original HTTP operation view changed')
                                changing = 'borrow' if operation == 'borrow-init' else 'open' if operation == 'open-init' else None
                                expected_keys = {key for key, value in recording_fields} | {'calls', 'responses', 'exits'}
                                _native_need(type(recording) is dict and all(type(key) is str for key in recording)
                                             and set(recording) == expected_keys | ({changing} if changing else set()),
                                             'private recording data view shape changed')
                                for key, expected in recording_fields:
                                    if key == changing:
                                        continue
                                    current = dict.get(recording, key)
                                    if key == 'sinks':
                                        _native_need(type(current) is dict and len(current) == len(expected)
                                                     and all(type(key) is str for key in current)
                                                     and all(dict.get(current, name) is value for name, value in expected),
                                                     'original HTTP safe sink candidates changed')
                                    else:
                                        _native_need(current is expected, 'original HTTP recording candidate changed')
                                for key, view in recording_lists:
                                    expected_rows = tuple(row[0] for row in calls) if key == 'calls' else responses if key == 'responses' else exits
                                    appended = (operation == 'call-init' and key == 'calls'
                                                or operation == 'response-init' and key == 'responses'
                                                or operation == 'exit-init' and key == 'exits')
                                    _native_need(recording.get(key) is view and type(view) is list
                                                 and len(view) == len(expected_rows) + (1 if appended else 0)
                                                 and all(left is right for left, right in zip(view, expected_rows))
                                                 and (not appended or view[-1] is candidate),
                                                 'original HTTP issued candidate list changed')
                                for issue, expected_fields in calls:
                                    if subject is not issue[0] and subject is not issue[4]:
                                        continue
                                    call_state = issue[0]
                                    _native_need(object.__getattribute__(call_state, 'owner') is state
                                                 and object.__getattribute__(call_state, 'call') is issue[4]
                                                 and type(object.__getattribute__(call_state, 'number')) is int
                                                 and object.__getattribute__(call_state, 'number') == issue[6][0],
                                                 'original HTTP call receiver or number changed')
                                    fields = object.__getattribute__(call_state, 'expected')
                                    _native_need(type(fields) is dict and len(fields) == len(expected_fields)
                                                 and all(type(key) is str for key in fields)
                                                 and all(dict.get(fields, key) is value for key, value in expected_fields),
                                                 'original HTTP call expected fields were replaced')
                                if operation == 'recording-active':
                                    _native_need(subject is recorder and phase == 'initializing'
                                                 and source_frame.f_code.co_qualname == 'NativeRecorder.__init__',
                                                 'native recorder cannot reactivate')
                                    phase = 'active'
                                elif operation == 'recording-release':
                                    _native_need(subject is state
                                                 and source_frame.f_code.co_qualname == '_NativeRecordingState.release',
                                                 'native recorder release needs its original source')
                                    phase = 'released'
                                elif operation == 'open-constructor-check':
                                    _native_need(subject is state and phase == 'active'
                                                 and source_frame.f_code.co_qualname == '_NativeRecordingState.opened'
                                                 and type(candidate) is tuple and len(candidate) == 6
                                                 and candidate[0] is recorder
                                                 and any(key == 'lock' and value is candidate[1] for key, value in recorder_fields)
                                                 and candidate[2] is call_kind,
                                                 'original HTTP call constructor candidates differ')
                                    call_namespace = type.__getattribute__(call_kind, '__dict__')
                                    _native_need(candidate[3] is call_namespace.get('__new__')
                                                 and type(candidate[3]) is staticmethod
                                                 and candidate[4][0] is object.__getattribute__(candidate[3], '__func__')
                                                 and candidate[5][0] is call_namespace.get('__init__'),
                                                 'original HTTP call constructor wrappers differ')
                                    _native_check_invocation(candidate[4])
                                    _native_check_invocation(candidate[5])
                                elif operation == 'new-call':
                                    opened_codes = tuple(signature[3] for signature in signatures
                                                         if signature[3].co_qualname == '_NativeRecordingState.opened')
                                    parent_frame = source_frame.f_back
                                    _native_need(subject is state and phase == 'active'
                                                 and source_frame.f_code.co_qualname == 'NativeCall.__new__'
                                                 and len(opened_codes) == 1 and parent_frame is not None
                                                 and parent_frame.f_code is opened_codes[0]
                                                 and parent_frame.f_globals is globals()
                                                 and parent_frame.f_locals.get('self') is state
                                                 and type(candidate) is tuple and len(candidate) == 3
                                                 and candidate[0] is call_kind
                                                 and candidate[1] is parent_frame.f_locals.get('identity')
                                                 and type(candidate[2]) is int and candidate[2] == len(allocated_calls) + 1,
                                                 'native call needs a fresh original allocation')
                                    result = object_new(call_kind)
                                    object.__setattr__(result, '_native_origin', sender.f_locals['channel'])
                                    allocated_calls += ((result, candidate[2], 'allocated', parent_frame),)
                                elif operation == 'begin-call':
                                    matches = tuple(row for row in allocated_calls if row[0] is subject)
                                    _native_need(len(matches) == 1 and matches[0][2] == 'allocated'
                                                 and source_frame.f_back is matches[0][3]
                                                 and source_frame.f_code.co_qualname == 'NativeCall.__init__',
                                                 'native call initialization is not fresh')
                                    allocated_calls = tuple((row[0], row[1], 'initializing', row[3]) if row[0] is subject else row
                                                            for row in allocated_calls)
                                elif operation == 'call-init':
                                    _native_need(source_frame.f_code.co_qualname == 'NativeCall.__init__'
                                                 and source_frame.f_locals.get('issue') is candidate
                                                 and type(candidate) is tuple and len(candidate) == 7
                                                 and candidate[5] is state and candidate[0].owner is state
                                                 and any(row[0] is candidate[4] and row[1] == candidate[6][0]
                                                         and row[2] == 'initializing' for row in allocated_calls),
                                                 'native call issue lacks its original construction')
                                    expected_fields = object.__getattribute__(candidate[0], 'expected')
                                    _native_need(type(expected_fields) is dict and all(type(key) is str for key in expected_fields),
                                                 'native call constructor fields differ')
                                    calls += ((candidate, tuple(expected_fields.items())),)
                                    allocated_calls = tuple((row[0], row[1], 'active', row[3]) if row[0] is candidate[4] else row
                                                            for row in allocated_calls)
                                elif operation == 'call-check':
                                    _native_need(any(issue[0] is subject and candidate is issue for issue, fields in calls),
                                                 'native call candidate is not its private original')
                                elif operation == 'safe-call':
                                    # The normal call-check above is unchanged.
                                    # This finite cleanup read has no supplied
                                    # expected row and performs no finalization.
                                    matches = tuple(issue for issue, fields in calls if issue[0] is subject)
                                    _native_need(candidate is None and len(matches) == 1
                                                 and source_frame.f_code.co_qualname == '_native_after_error',
                                                 'safe call cleanup needs its original error source and receiver')
                                    result = matches[0]
                                elif operation == 'response-init':
                                    _native_need(source_frame.f_code.co_qualname == '_NativeCallState.attach'
                                                 and source_frame.f_locals.get('response_issue') is candidate
                                                 and type(candidate) is tuple and len(candidate) == 13
                                                 and any(issue[0] is subject for issue, fields in calls)
                                                 and candidate[0].call is subject and type(candidate[7]) is rlock_kind
                                                 and candidate[9] is rlock_acquire and candidate[10] is rlock_release,
                                                 'native response issue lacks its original receiver')
                                    responses += (candidate,)
                                elif operation == 'exit-init':
                                    _native_need(subject is state
                                                 and source_frame.f_code.co_qualname == '_NativeRecordingState.install_exits'
                                                 and source_frame.f_locals.get('exit_issue') is candidate
                                                 and type(candidate) is tuple and len(candidate) == 7,
                                                 'native exit issue lacks its original constructor')
                                    exits += (candidate,)
                                elif operation in ('borrow-init', 'open-init'):
                                    required = '_NativeRecordingState.capture_borrow' if changing == 'borrow' else '_NativeRecordingState.enter'
                                    _native_need(subject is state and source_frame.f_code.co_qualname == required
                                                 and dict.get(recording, changing) is candidate,
                                                 'native recording update lacks its original source')
                                    _native_need(not any(key == changing and value is not None for key, value in recording_fields),
                                                 'native recording update cannot be repeated')
                                    recording_fields = tuple((key, value) for key, value in recording_fields if key != changing) + ((changing, candidate),)
                                elif operation in ('preflight', 'recording-check', 'safe-recording'):
                                    _native_need(subject is recorder or subject is state
                                                 or any(issue[0] is subject or issue[4] is subject for issue, fields in calls),
                                                 'native private check receiver differs')
                                    if operation != 'preflight':
                                        _native_need(subject is state and candidate is recording,
                                                     'recording view is not its private original candidate')
                                else:
                                    raise ValueError('unlisted private HTTP producer request')
                            reply = (True, result)
                        except BaseException as refusal:
                            reply = (False, refusal)
                        request = yield reply
                finally:
                    creator_frame = constructor_frame = None

            producer = produce
            producer_signature = _native_invocation(producer)
            _native_need(producer.__closure__ is None and producer.__defaults__ is None
                         and sum(item is producer.__code__ for item in creator_frame.f_code.co_consts) == 1,
                         'literal HTTP producer cannot accept foreign cells or defaults')
            channel = producer(creator_frame, constructor_frame, kind, out, ports, initial_ctx,
                compatibility_issuer, classes, snapshots, frame_get, object_new, gen_send, gen_close,
                rlock_new, rlock_owned, rlock_acquire, rlock_release)
            generator_kind, unused_kind = _native_origin_types(frame_get, object_new, gen_send, gen_close,
                rlock_new, rlock_owned, rlock_acquire, rlock_release)
            _native_need(type(channel) is generator_kind and channel.gi_code is producer.__code__
                         and channel.gi_frame.f_globals is globals(), 'literal HTTP channel differs')
            try:
                recorder = gen_send(channel, None)
            except BaseException as primary:
                try:
                    gen_close(channel)
                except BaseException as secondary:
                    _native_secondary(primary, '__som_native_http_acquisition_close_secondary__', (secondary,))
                raise
            _native_need(type(recorder) is kind, 'private HTTP producer did not allocate the recorder')
            state = channel.gi_frame.f_locals['state']
            object.__setattr__(recorder, '_native_origin', channel)
            object.__setattr__(state, '_native_origin', channel)
            return recorder

        acquisition = acquire
        acquisition_signature = _native_invocation(acquisition)
        _native_need(acquisition.__closure__ is None and acquisition.__defaults__ is None
                     and sum(item is acquisition.__code__ for item in constructor_frame.f_code.co_consts) == 1,
                     'literal HTTP acquisition cannot accept foreign cells or defaults')
        return acquisition(constructor_frame, kind, out, ports, initial_ctx, compatibility_issuer, classes, snapshots,
            frame_get, object_new, gen_send, gen_close, rlock_new, rlock_owned, rlock_acquire, rlock_release)


    def __init__(self, out, ports=(18917, 18919), *, initial_ctx):
        _native_transaction(self, 'begin-recording')
        try:
            initial = _native_callback_guard()
            _native_need(initial[1] is initial_ctx, 'original HTTP Context pointer differs')
            super().__init__(out, ports)
            state = object.__getattribute__(self, '_native_state')
            _NativeRecordingState.__init__(state, self, initial)
            self._native_operations = state.operations
            _native_transaction(self, 'recording-active')
        except BaseException as primary:
            try:
                _native_transaction(self, 'recording-failed')
            except BaseException as secondary:
                _native_secondary(primary, '__som_native_http_constructor_secondary__', (secondary,))
            raise

    def open(self, handler, http_class, request, **kwargs):
        raise ValueError('native HTTP uses only its captured source-owned open operation')

    def __enter__(self):
        _native_recording_operations(self)[1]()
        return self

    def __exit__(self, *_exception):
        raise ValueError('native HTTP cleanup belongs to its captured outer scope')


_NATIVE_ISSUER = _NativeIssuer()
_NATIVE_ISSUER.helpers = tuple(_native_class_capture(kind) for kind in (
    _NativeIssuer, _NativeCallbackSeal, _NativeExitFrame, _NativeResponseState,
    _NativeCallState, _NativeExitDescriptor, _NativeRecordingState, _NativeExitState,
    NativeCall, NativeRecorder))



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
