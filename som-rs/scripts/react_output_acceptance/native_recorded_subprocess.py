"""Finite native subprocess recording. Global subprocess members stay owned by Context."""
from __future__ import annotations

import hashlib as _hashlib
import builtins as _builtins
import contextlib as _contextlib
import io as _io
import json as _json
import math as _math
import os as _os
import posix as _posix
from pathlib import Path as _Path
import re as _re
import signal as _signal
import subprocess as _subprocess
import sys as _sys
import threading as _threading
import _thread as _native_thread
from types import BuiltinFunctionType as _BuiltinFunctionType, CodeType as _CodeType, FunctionType as _FunctionType
from types import MethodType as _MethodType, ModuleType as _ModuleType

PIPE = _subprocess.PIPE
STDOUT = _subprocess.STDOUT
TimeoutExpired = _subprocess.TimeoutExpired

_SELF = 'som-rs/scripts/react_output_acceptance/native_recorded_subprocess.py'
_OWNER = 'som-rs/scripts/react_output_acceptance/native_recording_owner.py'
_CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
_ROLE = 'm6.api.recording.subprocess'
_OWNER_ROLE = 'm6.api.recording.owner'
_ACTIVE = None
_REAL_RUN = _subprocess.run
_COMPLETED = _subprocess.CompletedProcess
_CALLED_ERROR = _subprocess.CalledProcessError
_PATH_TYPE = type(_Path())
_RUN_OPTIONS = frozenset({'capture_output', 'check', 'text', 'timeout', 'cwd', 'env'})
_POPEN_OPTIONS = frozenset({'stdout', 'stderr', 'cwd', 'env', 'start_new_session', 'text'})


def _need(value, message):
    if not value:
        raise ValueError(message)


def _core():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    _need(controls.role_sources.get(_ROLE) == _SELF
          and controls.role_sources.get(_OWNER_ROLE) == _OWNER
          and controls.role_sources.get('runtime.context') == _CONTEXT,
          'fixed native recorder source roles required')
    return controls


def _dict(value):
    return object.__getattribute__(value, '__dict__')


def _fn(cls, name):
    value = type.__getattribute__(cls, '__dict__').get(name)
    _need(type(value) is _FunctionType, 'actual source method required: ' + name)
    return value


def _module(module):
    _need(type(module) is _ModuleType, 'actual fixed recorder module required')
    return _ModuleType.__getattribute__(module, '__dict__')


def _physical(path, directory=False):
    _need(type(path) in {str, _PATH_TYPE}, 'plain recorder path required')
    path = _Path(path).absolute()
    _need('..' not in path.parts and not any(part.is_symlink() for part in (path, *path.parents)),
          'physical recorder path required')
    _need(path.is_dir() if directory else path.is_file() and path.stat().st_nlink == 1,
          'physical recorder file or directory required')
    return path


def _binds():
    """These aliases are policy-declared fixed exports, checked before use."""
    controls = _core()
    owner = _module(__som_native_owner_module__)
    context = _module(__som_native_context_module__)
    for name, value in (('Recorder', __som_native_Recorder__), ('_ScopeState', __som_native_ScopeState__),
                        ('_ChildState', __som_native_ChildState__), ('_RecordingErrors', __som_native_RecordingErrors__),
                        ('_KernelWaitState', __som_native_KernelWaitState__)):
        _need(owner.get(name) is value and type(value) is type, 'fixed recorder class export changed')
    for name, value in (('Context', __som_native_Context__), ('SourceBytes', __som_native_SourceBytes__),
                        ('NativePythonControls', __som_native_NativePythonControls__),
                        ('NativePythonLease', __som_native_NativePythonLease__)):
        _need(context.get(name) is value and type(value) is type, 'fixed Context class export changed')
    _need(context.get('context') is __som_native_context_getter__
          and type(__som_native_context_getter__) is _FunctionType,
          'fixed Context getter export changed')
    return controls, owner, context


def _code_tree(code):
    yield code
    for item in code.co_consts:
        if type(item) is _CodeType:
            yield from _code_tree(item)


def _stdlib_selected_code(module, names):
    """Compile fixed physical stdlib source for comparison only. Never exec it."""
    _need(module is _subprocess and names == ('Popen', '_on_error_fd_closer')
          or module is _contextlib and names == ('contextmanager',), 'fixed stdlib comparison selector required')
    values = _module(module)
    path = _physical(values.get('__file__'))
    _need(path.is_relative_to(_Path(_sys.base_prefix).absolute())
          and path.name == ('subprocess.py' if module is _subprocess else 'contextlib.py'),
          'actual interpreter stdlib physical source required')
    raw = path.read_bytes()
    _need(len(raw) <= 2 * 1024**2, 'bounded actual stdlib source required')
    compiler = compile
    _need(type(compiler) is _BuiltinFunctionType and compiler is _builtins.compile
          and compiler.__self__ is _builtins and compiler.__name__ == 'compile'
          and compiler.__module__ == 'builtins', 'actual comparison-only builtin compiler required')
    code = compiler(raw, str(path), 'exec', dont_inherit=True, optimize=_sys.flags.optimize)
    for name in names:
        matches = [value for value in code.co_consts if type(value) is _CodeType and value.co_name == name]
        _need(len(matches) == 1, 'one exact immediate stdlib CodeType selector required')
        code = matches[0]
    return code, (path, _hashlib.sha256(raw).hexdigest())


def _code_comparison_data(code):
    """Exact same-interpreter code data, without foreign constant hooks."""
    def constant(value):
        kind = type(value)
        if kind is _CodeType:
            return ('code', _code_comparison_data(value))
        if kind in {str, bytes, int, float, complex, bool, type(None), type(Ellipsis)}:
            return (kind.__name__, value)
        if kind is tuple:
            return ('tuple', tuple(constant(item) for item in value))
        if kind is frozenset:
            return ('frozenset', frozenset(constant(item) for item in value))
        raise ValueError('foreign object in comparison-only stdlib code constants')
    _need(type(code) is _CodeType, 'actual comparison-only CodeType required')
    return (code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount, code.co_nlocals,
            code.co_stacksize, code.co_flags, code.co_code, tuple(constant(x) for x in code.co_consts),
            code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars, code.co_filename,
            code.co_name, code.co_qualname, code.co_firstlineno, code.co_linetable, code.co_exceptiontable)


def _decorated_process_method(name, function):
    _need(name == '_on_error_fd_closer', 'unreviewed decorated process method refused')
    contextmanager = _contextlib.contextmanager
    _need(type(contextmanager) is _FunctionType and contextmanager.__globals__ is _module(_contextlib),
          'actual contextmanager function required')
    expected_manager, context_source = _stdlib_selected_code(_contextlib, ('contextmanager',))
    _need(_code_comparison_data(contextmanager.__code__) == _code_comparison_data(expected_manager),
          'whole actual contextmanager source differs')
    helper = [code for code in contextmanager.__code__.co_consts
              if type(code) is _CodeType and code.co_name == 'helper'
              and code.co_qualname == 'contextmanager.<locals>.helper' and code.co_freevars == ('func',)]
    _need(len(helper) == 1 and function.__code__ is helper[0]
          and function.__globals__ is contextmanager.__globals__
          and function.__builtins__ is contextmanager.__builtins__
          and function.__code__.co_freevars == ('func',)
          and type(function.__closure__) is tuple and len(function.__closure__) == 1,
          'exact contextmanager helper/code/globals/builtins/closure required')
    cell = function.__closure__[0]
    underlying = cell.cell_contents
    _need(type(underlying) is _FunctionType and underlying.__globals__ is _module(_subprocess)
          and function.__dict__.get('__wrapped__') is underlying,
          'actual subprocess decorated underlying function required')
    expected_underlying, process_source = _stdlib_selected_code(_subprocess, ('Popen', '_on_error_fd_closer'))
    _need(_code_comparison_data(underlying.__code__) == _code_comparison_data(expected_underlying),
          'whole subprocess fd-closer source differs')
    functions = [(value, value.__code__, value.__globals__, value.__defaults__,
                  value.__kwdefaults__, value.__closure__, value.__builtins__)
                 for value in (function, contextmanager, underlying)]
    return {'wrapper': function, 'cell': cell, 'underlying': underlying, 'functions': functions,
            'sources': [context_source, process_source], 'contextmanager': contextmanager}


def _decorated_methods(state, source_check=False):
    for record in state.refs['process_decorated'].values():
        _need(_contextlib.contextmanager is record['contextmanager']
              and record['cell'].cell_contents is record['underlying']
              and record['wrapper'].__dict__.get('__wrapped__') is record['underlying'],
              'retained decorated process closure changed')
        for function, code, namespace, defaults, keywords, closure, builtins in record['functions']:
            _need(function.__code__ is code and function.__globals__ is namespace
                  and function.__defaults__ is defaults and function.__kwdefaults__ is keywords
                  and function.__closure__ is closure and function.__builtins__ is builtins,
                  'retained decorated process invocation changed')
        if source_check:
            for path, before in record['sources']:
                _need(_hashlib.sha256(_physical(path).read_bytes()).hexdigest() == before,
                      'retained actual stdlib source changed')


def _retained_process_sources(state):
    refs = state.refs
    namespace = type.__getattribute__(refs['base_popen'], '__dict__')
    _need(len(namespace) == len(refs['base_namespace'])
          and all(namespace.get(key) is value for key, value in refs['base_namespace'].items()),
          'original subprocess class namespace changed')
    for function, code, namespace, defaults, keywords, closure in refs['process_functions'].values():
        _need(function.__code__ is code and function.__globals__ is namespace
              and function.__defaults__ is defaults and function.__kwdefaults__ is keywords
              and function.__closure__ is closure, 'original subprocess method invocation changed')
    _decorated_methods(state)


def _kill_primitive():
    function = _posix.kill
    _need(type(function) is _BuiltinFunctionType and function.__self__ is _posix
          and function.__module__ == 'posix' and function.__name__ == 'kill'
          and _os.kill is function, 'actual POSIX kill primitive required')
    signal = _signal.SIGKILL
    _need(type(signal) is _signal.Signals and int.__index__(signal) == 9,
          'actual Linux SIGKILL required')
    return function, signal


def _retained_kill(state, pid, primary):
    """Reject changed globals, then use only the captured actual builtin."""
    function = state.refs['kill_primitive']
    _need(type(function) is _BuiltinFunctionType and function.__self__ is state.refs['posix_module']
          and function.__module__ == 'posix' and function.__name__ == 'kill'
          and state.refs['kill_number'] == 9, 'retained actual POSIX kill primitive changed')
    if (_os.kill is not function or _posix.kill is not function
            or _signal.SIGKILL is not state.refs['kill_signal']):
        _secondary(primary, 'recorded-cleanup-primitive-owner',
                   ValueError('global kill primitive or SIGKILL changed; captured primitive used'))
    function(pid, 9)


def _kernel_primitives():
    waitpid, decoder = _posix.waitpid, _posix.waitstatus_to_exitcode
    for function, name in ((waitpid, 'waitpid'), (decoder, 'waitstatus_to_exitcode')):
        _need(type(function) is _BuiltinFunctionType and function.__self__ is _posix
              and function.__module__ == 'posix' and function.__name__ == name,
              'actual POSIX kernel wait primitive required')
    _need(_os.waitpid is waitpid and _os.waitstatus_to_exitcode is decoder,
          'actual global kernel wait primitives required')
    lock_type = _native_thread.LockType
    _need(type(lock_type) is type and type.__getattribute__(lock_type, '__module__') == '_thread'
          and type.__getattribute__(lock_type, '__name__') == 'lock', 'actual native wait lock type required')
    namespace = type.__getattribute__(lock_type, '__dict__')
    methods = {name: namespace[name] for name in ('acquire', 'release', 'locked')}
    return waitpid, decoder, lock_type, methods


def _kernel_dependencies(state, cleanup=False):
    # Do not call another mutable helper until all retained facade functions
    # have their original identities, code, globals and invocation bindings.
    for name, signature in state.refs['facade_functions'].items():
        function, code, namespace, defaults, keywords, closure = signature
        if (globals().get(name) is not function or function.__code__ is not code
                or function.__globals__ is not namespace or function.__defaults__ is not defaults
                or function.__kwdefaults__ is not keywords or function.__closure__ is not closure):
            raise ValueError('retained facade kernel operation changed')
    cls, mro, expected = state.refs['kernel_class']
    namespace = type.__getattribute__(cls, '__dict__')
    if (type.__getattribute__(cls, '__mro__') is not mro or len(namespace) != len(expected)
            or not all(namespace.get(name) is value for name, value in expected.items())):
        raise ValueError('kernel record class changed')
    initializer, code, globals_dict, defaults, keywords, closure = state.refs['kernel_initializer']
    if (initializer.__code__ is not code or initializer.__globals__ is not globals_dict
            or initializer.__defaults__ is not defaults or initializer.__kwdefaults__ is not keywords
            or initializer.__closure__ is not closure):
        raise ValueError('kernel record initializer changed')
    lock_type = state.refs['lock_type']
    lock_namespace = type.__getattribute__(lock_type, '__dict__')
    if not all(lock_namespace.get(name) is value for name, value in state.refs['lock_methods'].items()):
        raise ValueError('native wait lock operations changed')
    same = (_os.waitpid is state.refs['waitpid'] and _posix.waitpid is state.refs['waitpid']
            and _os.waitstatus_to_exitcode is state.refs['wait_decoder']
            and _posix.waitstatus_to_exitcode is state.refs['wait_decoder']
            and _native_thread.LockType is lock_type)
    if not same and not cleanup:
        raise ValueError('global kernel wait primitive or lock type changed')
    return same


def _wait_owner(state, process):
    _kernel_dependencies(state, cleanup=True)
    _need(state is _ACTIVE and not state.closed and type(state.kernel_waits) is list,
          'actual live kernel wait scope required')
    cls = state.refs['kernel_class'][0]
    rows = [row for row in state.kernel_waits if type(row) is cls and row.process is process]
    _need(len(rows) == 1, 'actual owned kernel wait record required')
    row = rows[0]
    _need(type(process) is state.refs['guard'] and row.values is _dict(process)
          and row.values.get('pid') == row.pid and row.values.get('_waitpid_lock') is row.lock
          and type(row.lock) is state.refs['lock_type'], 'actual kernel wait process or native lock changed')
    return row


def _capture_wait_owner(state, process):
    _kernel_dependencies(state, cleanup=True)
    _need(type(process) is state.refs['guard'], 'actual guard child required for kernel capture')
    matches = [row for row in state.kernel_waits if row.process is process]
    if matches:
        _need(len(matches) == 1, 'one actual kernel owner required')
        _owned_process_operations(state, process, matches[0].pid)
        row = matches[0]
        if not row.installed:
            for name, hook in row.hooks.items():
                _need(not row.original_private[name][0], 'foreign initial kernel hook remains refused')
                row.values[name] = hook
            row.installed = True
        return row
    values = _dict(process)
    pid = values.get('pid')
    row = state.refs['kernel_class'][0](process, pid, values, values['_waitpid_lock'])
    for name in ('stdout', 'stderr', 'stdin'):
        stream = values.get(name)
        if stream is None or type(stream) in {_io.TextIOWrapper, _io.BufferedReader, _io.BufferedWriter, _io.FileIO}:
            row.streams.append((name, stream))
        else:
            row.foreign_streams.append(name)
    for name, function in (('_internal_poll', _kernel_poll), ('_try_wait', _kernel_try_wait)):
        row.original_private[name] = (name in values, values.get(name))
        row.hooks[name] = _MethodType(function, process)
    state.kernel_waits.append(row)
    _owned_process_operations(state, process, pid)
    for name, hook in row.hooks.items():
        values[name] = hook
    row.installed = True
    return row


def _kernel_read(state, row, flags):
    _need(type(flags) is int and flags in {0, 1}, 'finite actual kernel wait flags required')
    _need(not row.unknown, 'kernel child status remains unknown')
    _need(state.refs['lock_methods']['locked'](row.lock), 'actual native kernel wait lock must be held')
    if not _kernel_dependencies(state, cleanup=True):
        error = ValueError('global wait primitive changed; captured actual kernel primitive used')
        if row.cleanup_primary is None:
            raise error
        _secondary(row.cleanup_primary, 'recorded-cleanup-kernel-primitive-owner', error)
    observation = None
    try:
        found, status = state.refs['waitpid'](row.pid, flags)
        _need(type(found) is int and type(status) is int and found in {0, row.pid},
              'actual owned kernel wait result required')
        observation = {'pid': found, 'status': status, 'state': 'observed'}
        row.observations.append(observation)
        if found == 0:
            row.live_observed = True
            observation['state'] = 'running'
        else:
            code = state.refs['wait_decoder'](status)
            _need(type(code) is int, 'actual decoded terminal kernel status required')
            row.terminal, row.status, row.code, row.live_observed = True, status, code, False
            row.values['returncode'] = code
            observation.update(exit_code=code, state='terminal')
        return found, status
    except BaseException as error:
        row.unknown = True
        row.live_observed = False
        if observation is None:
            observation = {}
            row.observations.append(observation)
        observation.update(state='unknown', error_type=type.__getattribute__(type(error), '__name__'))
        raise


def _kernel_claim(state, row):
    claim = row.values.get('returncode')
    if claim is None or row.terminal and type(claim) is int and claim == row.code:
        return
    row.claims.append({'returncode': claim if type(claim) in {int, type(None)} else None,
                       'claim_type': type.__getattribute__(type(claim), '__name__')})
    if not row.terminal:
        _kernel_read(state, row, 1)
    error = ValueError('public returncode has no matching actual kernel wait proof')
    if row.cleanup_primary is None:
        raise error
    _secondary(row.cleanup_primary, 'recorded-cleanup-returncode-claim', error)
    # Only the actual bounded observation above permits this repair. None is
    # still unknown/running; only an actual terminal observation supplies code.
    row.values['returncode'] = row.code if row.terminal else None


def _kernel_poll(process, _deadstate=None):
    state = _ACTIVE
    row = _wait_owner(state, process)
    _need(_deadstate is None or type(_deadstate) is int, 'plain stdlib poll deadstate required')
    _owned_process_operations(state, process, row.pid)
    if not row.terminal:
        row.live_observed = False
    acquire, release = state.refs['lock_methods']['acquire'], state.refs['lock_methods']['release']
    _need(acquire(row.lock, False), 'owned native wait lock is busy; status unknown')
    try:
        _kernel_claim(state, row)
        if not row.terminal:
            _kernel_read(state, row, 1)
        return row.code if row.terminal else None
    finally:
        release(row.lock)


def _kernel_try_wait(process, flags):
    state = _ACTIVE
    row = _wait_owner(state, process)
    _owned_process_operations(state, process, row.pid)
    frame = _sys._getframe(1)
    expected = state.refs['process_functions']['_wait']
    _need(frame.f_code is expected[1] and frame.f_globals is expected[2]
          and state.refs['lock_methods']['locked'](row.lock),
          'actual stdlib wait must own the retained native lock')
    _kernel_claim(state, row)
    return (row.pid, row.status) if row.terminal else _kernel_read(state, row, flags)


def _wait_proof(state, row, code):
    _need(row.terminal and not row.unknown and type(code) is int and row.code == code
          and row.values.get('returncode') == code,
          'terminal wait requires an actual retained kernel observation')


def _restore_kernel_hooks(state, failures):
    _need(type(state.kernel_waits) is list, 'actual kernel wait list required during restore')
    for row in state.kernel_waits:
        if type(row) is not state.refs['kernel_class'][0]:
            failures.append(('recording-kernel-hook-owner', ValueError('foreign kernel record refused'), None))
            continue
        if not row.installed:
            continue
        for name, hook in row.hooks.items():
            try:
                _need(row.values.get(name) is hook, 'kernel hook replaced by later owner')
                function = hook.__func__
                signature = state.refs['facade_functions'][function.__name__]
                _need(signature[0] is function and function.__code__ is signature[1]
                      and function.__globals__ is signature[2], 'owned kernel hook code changed')
                present, value = row.original_private[name]
                if present:
                    row.values[name] = value
                else:
                    del row.values[name]
            except BaseException as error:
                failures.append(('recording-kernel-hook-restore', error,
                                 BaseException.__getattribute__(error, '__traceback__')))
        row.installed = False


def _kernel_ledger(row):
    return {'pid': row.pid, 'terminal_observed': row.terminal, 'raw_status': row.status,
            'exit_code': row.code, 'unknown': row.unknown or not row.terminal,
            'observations': list(row.observations), 'claims': list(row.claims)}


def _guard(ctx, context):
    data = _dict(ctx)
    lease = data.get('python_controls')
    _need(type(lease) is __som_native_NativePythonLease__, 'actual native Python lease required')
    fields = _dict(lease)
    _need(not fields.get('closed') and type(fields.get('owns_controls')) is bool,
          'open native Python lease required')
    actual = fields.get('controls')
    _need(type(actual) is __som_native_NativePythonControls__, 'actual native Python controls required')
    values = _dict(actual)
    _need(values.get('ctx') is ctx and values.get('closed') is False, 'native Python controls owner differs')
    wrapper, original = values.get('wrapper'), values.get('original')
    _need(type(wrapper) is type and type(original) is type and _subprocess.Popen is wrapper,
          'actual global guarded Popen required')
    mro = type.__getattribute__(wrapper, '__mro__')
    _need(mro == (wrapper, original, object), 'native Popen class ancestry changed')
    namespace = type.__getattribute__(wrapper, '__dict__')
    _need(set(namespace) <= {'__module__', '__doc__', '__init__', '__som_native_python_controls__'}
          and namespace.get('__som_native_python_controls__') is actual,
          'native Popen class namespace changed')
    initializer = namespace.get('__init__')
    source = _fn(__som_native_NativePythonControls__, '__init__')
    _need(type(initializer) is _FunctionType and initializer.__globals__ is context
          and initializer.__builtins__ is source.__builtins__
          and any(initializer.__code__ is code for code in _code_tree(source.__code__)),
          'native Popen initializer lost its actual source')
    cells = dict(zip(initializer.__code__.co_freevars, initializer.__closure__ or ()))
    _need('controls' in cells and cells['controls'].cell_contents is actual,
          'native Popen initializer closure differs')
    _need(not {'verify_installed', 'close'} & set(fields)
          and not {'verify_installed', 'close'} & set(values), 'native Python method shadow refused')
    _MethodType(_fn(__som_native_NativePythonLease__, 'verify_installed'), lease)()
    return lease, actual, wrapper, original


def _context(ctx, controls, context):
    _need(type(ctx) is __som_native_Context__, 'actual fixed native Context required')
    data = _dict(ctx)
    _need(not {'python_argv', 'runtime', 'python_payload', '_native_source_controls', 'verify'} & set(data),
          'native Context method shadow refused')
    _need(type(data.get('qa_mode')) is str and type(data.get('repo')) is _PATH_TYPE
          and type(data.get('helper_revision')) is str and type(data.get('candidate_revision')) is str,
          'plain actual Context identity fields required')
    _need(context.get('_ACTIVE') is ctx and data.get('qa_mode') == 'native-qa-v1'
          and data.get('repo') == controls.root
          and data.get('helper_revision') == controls.helper_revision
          == data.get('candidate_revision') == controls.candidate_revision,
          'actual active recorder Context identity differs')
    sources = data.get('sources')
    _need(type(sources) is __som_native_SourceBytes__, 'actual recorder SourceBytes required')
    source_fields = _dict(sources)
    _need(type(source_fields.get('root')) is _PATH_TYPE and type(source_fields.get('revision')) is str,
          'plain SourceBytes identity fields required')
    _need(source_fields.get('root') == controls.root and source_fields.get('revision') == controls.helper_revision
          and not {'read', 'verify', 'git'} & set(source_fields), 'recorder SourceBytes identity or method differs')
    path = _physical(data.get('path'))
    _plain(data.get('data'))
    _need(type(data.get('raw')) is bytes and path.read_bytes() == data['raw']
          and _json.loads(data['raw']) == data.get('data'), 'actual recorder Context bytes changed')
    admission = data.get('qa_source_authority')
    _need(type(admission) is dict and admission.get('source_authority_active') is True,
          'actual native source authority is required')
    getter = __som_native_context_getter__
    _need(getter.__globals__ is context and getter() is ctx, 'actual recorder Context getter changed')
    read = _MethodType(_fn(__som_native_SourceBytes__, 'read'), sources)
    for role, relative in ((_ROLE, _SELF), (_OWNER_ROLE, _OWNER), ('runtime.context', _CONTEXT)):
        _need(controls.role_sources[role] == relative, 'recorder role source changed')
        raw = read(relative)
        _need(raw == _physical(controls.root / relative).read_bytes(), 'recorder committed source bytes differ')
    return data, sources


def _verify(state):
    # Core/source verification can itself start Git children. Reject changed
    # retained stdlib operations before asking it to perform any such work.
    if type(state) is not __som_native_ScopeState__ or state is not _ACTIVE or state.closed:
        raise ValueError('actual active recording scope required')
    if type(state.refs) is not dict:
        raise ValueError('actual recording owner fields required')
    _kernel_dependencies(state)
    _retained_process_sources(state)
    _need(_os.kill is state.refs['kill_primitive'] and _posix.kill is state.refs['kill_primitive']
          and _signal.SIGKILL is state.refs['kill_signal'], 'recording kill primitive or signal changed')
    _need(_subprocess.Popen is state.refs['guard'] and _subprocess.run is state.refs['real_run'] is _REAL_RUN
          and state.refs['context_namespace'].get('subprocess') is _subprocess,
          'actual recorder subprocess owners changed before source verification')
    controls, owner, context = _binds()
    _need(type(state) is __som_native_ScopeState__ and state is _ACTIVE and not state.closed,
          'actual active recording scope required')
    refs = state.refs
    _need(type(refs) is dict and type(refs.get('pid')) is int and type(refs.get('thread')) is int,
          'actual recording owner fields required')
    _need(refs['pid'] == _os.getpid() and refs['thread'] == _threading.get_ident(),
          'recording scope process or thread differs')
    _need(_subprocess.run is refs['real_run'] is _REAL_RUN, 'global subprocess.run changed')
    _need(_subprocess.CompletedProcess is _COMPLETED and _subprocess.CalledProcessError is _CALLED_ERROR
          and _subprocess.TimeoutExpired is TimeoutExpired
          and _subprocess.PIPE == PIPE and _subprocess.STDOUT == STDOUT,
          'actual stdlib subprocess surface changed')
    _need(_os.kill is refs['kill_primitive'] and _posix.kill is refs['kill_primitive']
          and _signal.SIGKILL is refs['kill_signal'], 'recording kill primitive or signal changed')
    data, sources = _context(refs['ctx'], controls, context)
    lease, actual, wrapper, original = _guard(refs['ctx'], context)
    _need(lease is refs['parent_python'] and actual is refs['python_owner']
          and wrapper is refs['guard'] and original is refs['base_popen'], 'recording Python guard owner changed')
    namespace = type.__getattribute__(original, '__dict__')
    _need(len(namespace) == len(refs['base_namespace'])
          and all(namespace.get(key) is value for key, value in refs['base_namespace'].items()),
          'original subprocess class namespace changed')
    for name, (function, code, namespace, defaults, keyword_defaults, closure) in refs['process_functions'].items():
        _need(function.__code__ is code and function.__globals__ is namespace
              and function.__defaults__ is defaults and function.__kwdefaults__ is keyword_defaults
              and function.__closure__ is closure, 'original subprocess method invocation changed')
    _decorated_methods(state, source_check=True)
    for cls, name, function in refs['source_methods']:
        _need(_fn(cls, name) is function, 'retained recorder source method changed')
    _recorder_methods(state)
    recorder = refs['recorder']
    _need(type(recorder) is __som_native_Recorder__, 'actual Recorder instance required')
    values = _dict(recorder)
    _need(not set(refs['recorder_methods']) & set(values)
          and values.get('original_run') is _bounded_run and type(_bounded_run) is _FunctionType
          and _bounded_run.__globals__ is globals() and values.get('original_popen') is wrapper
          and type(values.get('events')) is list
          and type(values.get('errors')) is __som_native_RecordingErrors__,
          'Recorder method, function, or data owner changed')
    _need(_dict(values['errors']).get('state') is state
          and _dict(values['errors']).get('recorder') is recorder, 'recording error adapter owner differs')
    _need(type(state.children) is list and all(type(row) is __som_native_ChildState__ for row in state.children),
          'actual source-owned child records required')
    _need(type(state.pre_registration_cleanup) is list,
          'actual source-owned pre-registration cleanup records required')
    _need(type(state.kernel_waits) is list,
          'actual source-owned kernel wait records required')
    _kernel_dependencies(state)
    borrow = refs['borrow']
    _need(type(borrow) is type(controls)
          and object.__getattribute__(borrow, '_domain') is object.__getattribute__(controls, '_domain')
          and object.__getattribute__(borrow, '_owns') is False
          and object.__getattribute__(borrow, '_closed') is False, 'actual same-owner Core borrow required')
    _MethodType(_fn(type(controls), 'verify_installed'), borrow)()
    python_borrow_fields = _dict(refs['python_borrow']) if type(refs['python_borrow']) is __som_native_NativePythonLease__ else {}
    _need(python_borrow_fields.get('controls') is actual and python_borrow_fields.get('owns_controls') is False
          and python_borrow_fields.get('closed') is False
          and not {'verify_installed', 'close'} & set(python_borrow_fields), 'actual borrowed Python lease required')
    _MethodType(refs['python_verify'], refs['python_borrow'])()
    return state


def _open(ctx, out, qa=None):
    """Fixed private lifecycle. Caller joins and real API input validation remain separate."""
    global _ACTIVE
    _need(_ACTIVE is None and _sys.platform == 'linux', 'one Linux recording scope required')
    controls, owner, context = _binds()
    data, sources = _context(ctx, controls, context)
    parent, python_owner, wrapper, original = _guard(ctx, context)
    kill_primitive, kill_signal = _kill_primitive()
    waitpid, decoder, lock_type, lock_methods = _kernel_primitives()
    out = _physical(out, True)
    _need(type(data.get('work')) is _PATH_TYPE and out.is_relative_to(data['work']),
          'recording output must belong to actual Context work')
    _need(qa is None or type(qa) is dict, 'plain original QA data required')
    if qa is not None:
        _plain(qa)
        _need(all(type(qa.get(name)) in {str, _PATH_TYPE}
                  for name in ('python', 'node', 'pg_bin', 'lsof', 'ps')), 'complete original QA tool data required')
    state = __som_native_ScopeState__()
    borrow = controls.borrow()
    python_borrow = None
    try:
        python_borrow = __som_native_NativePythonLease__(python_owner, False)
        recorder = __som_native_Recorder__(out, _bounded_run, original_popen=wrapper, qa=qa)
        methods = {name: _fn(__som_native_Recorder__, name)
                   for name in ('__init__', 'popen', 'retain', 'observe', 'postmaster', 'command', 'snapshot', 'run')}
        source_methods = [(__som_native_Recorder__, name, function) for name, function in methods.items()]
        source_methods += [(cls, name, _fn(cls, name)) for cls, name in (
            (__som_native_Context__, 'python_argv'), (__som_native_SourceBytes__, 'read'),
            (__som_native_NativePythonLease__, 'verify_installed'), (__som_native_NativePythonLease__, 'close'))]
        process_functions, process_decorated = {}, {}
        for name, function in type.__getattribute__(original, '__dict__').items():
            if type(function) is not _FunctionType:
                continue
            if function.__globals__ is not _module(_subprocess):
                process_decorated[name] = _decorated_process_method(name, function)
            process_functions[name] = (function, function.__code__, function.__globals__, function.__defaults__,
                                       function.__kwdefaults__, function.__closure__)
        state.refs.update(ctx=ctx, sources=sources, parent_python=parent, python_owner=python_owner,
            guard=wrapper, base_popen=original, real_run=_REAL_RUN, borrow=borrow,
            guard_mro=type.__getattribute__(wrapper, '__mro__'),
            guard_namespace=dict(type.__getattribute__(wrapper, '__dict__')),
            kill_primitive=kill_primitive, kill_signal=kill_signal, kill_number=9, posix_module=_posix,
            waitpid=waitpid, wait_decoder=decoder, lock_type=lock_type, lock_methods=lock_methods,
            kernel_class=(__som_native_KernelWaitState__,
                          type.__getattribute__(__som_native_KernelWaitState__, '__mro__'),
                          dict(type.__getattribute__(__som_native_KernelWaitState__, '__dict__'))),
            python_borrow=python_borrow, python_verify=_fn(__som_native_NativePythonLease__, 'verify_installed'),
            python_close=_fn(__som_native_NativePythonLease__, 'close'), recorder=recorder,
            recorder_methods=methods, source_methods=source_methods, pid=_os.getpid(), thread=_threading.get_ident(),
            python_argv=_MethodType(_fn(__som_native_Context__, 'python_argv'), ctx), out=out,
            recorder_class=__som_native_Recorder__,
            recorder_signatures={name: (function.__code__, function.__globals__, function.__defaults__,
                function.__kwdefaults__, function.__closure__) for name, function in methods.items()},
            recorder_popen_codes=tuple(_code_tree(methods['popen'].__code__)),
            base_namespace=dict(type.__getattribute__(original, '__dict__')), process_functions=process_functions,
            process_decorated=process_decorated,
            source_close=_MethodType(_fn(type(controls), 'close'), borrow),
            source_lease_type=type(borrow), source_domain=object.__getattribute__(borrow, '_domain'),
            source_lease_mro=type.__getattribute__(type(borrow), '__mro__'),
            source_lease_namespace=dict(type.__getattribute__(type(borrow), '__dict__')))
        close = state.refs['source_close'].__func__
        state.refs['source_close_signature'] = (close.__code__, close.__globals__, close.__defaults__,
                                                 close.__kwdefaults__, close.__closure__)
        close = state.refs['python_close']
        state.refs['python_close_signature'] = (close.__code__, close.__globals__, close.__defaults__,
                                                 close.__kwdefaults__, close.__closure__)
        classes = []
        functions = []
        for cls in (__som_native_NativePythonLease__, __som_native_NativePythonControls__):
            namespace = dict(type.__getattribute__(cls, '__dict__'))
            classes.append((cls, type.__getattribute__(cls, '__mro__'), namespace))
            for function in namespace.values():
                if type(function) is _FunctionType:
                    functions.append((function, function.__code__, function.__globals__, function.__defaults__,
                                      function.__kwdefaults__, function.__closure__))
        require = context['require']
        _need(type(require) is _FunctionType, 'actual Context refusal function required')
        functions.append((require, require.__code__, require.__globals__, require.__defaults__,
                          require.__kwdefaults__, require.__closure__))
        state.refs.update(python_close_classes=classes, python_close_functions=functions,
                          context_namespace=context, context_require=require)
        initializer = _fn(__som_native_KernelWaitState__, '__init__')
        state.refs['kernel_initializer'] = (initializer, initializer.__code__, initializer.__globals__,
            initializer.__defaults__, initializer.__kwdefaults__, initializer.__closure__)
        state.refs['facade_functions'] = {name: (function, function.__code__, function.__globals__,
            function.__defaults__, function.__kwdefaults__, function.__closure__)
            for name, function in tuple(globals().items())
            if type(function) is _FunctionType and function.__globals__ is globals()}
        recorder.errors = __som_native_RecordingErrors__(state, recorder)
        _ACTIVE = state
        _verify(state)
        return state
    except BaseException as primary:
        if _ACTIVE is state:
            _ACTIVE = None
        for name, lease in (('python-borrow', python_borrow), ('source-borrow', borrow)):
            if lease is not None:
                try:
                    lease.close()
                except BaseException as error:
                    _secondary(primary, name, error)
        state.closed = True
        state.refs.clear()
        raise


def _recording(token):
    """Return only this live scope's actual completed ordinary Recorder."""
    state = _verify(token)
    _need(not state.closing, 'recording scope is closing')
    return state.refs['recorder']


def _secondary(primary, stage, error):
    # Use the actual BaseException descriptors. Neither a foreign container nor
    # an exception subclass's attribute hook can run while retaining an error.
    data = BaseException.__dict__['__dict__'].__get__(primary, BaseException)
    key, record_key = '__som_m6_recording_secondary__', '__som_m6_recording_secondary_record__'
    previous = data.get(key)
    # Copy exact builtin containers only. A custom metadata object is preserved
    # by identity in this source-owned plain record, without iterating it.
    rows = list(previous) if type(previous) in {list, tuple} else []
    list.append(rows, (stage, error, BaseException.__dict__['__traceback__'].__get__(error, BaseException)))
    record = {'primary': primary, 'had_previous': key in data, 'previous_value': previous,
              'had_previous_record': record_key in data, 'previous_record': data.get(record_key), 'rows': rows}
    data[record_key], data[key] = record, rows


def _plain(value):
    kind = type(value)
    if kind in {str, bytes, int, bool, type(None), _PATH_TYPE}:
        return
    if kind is float:
        _need(_math.isfinite(value), 'finite recording data required')
        return
    if kind is dict:
        _need(all(type(key) is str for key in value), 'plain recording data keys required')
        for item in value.values():
            _plain(item)
        return
    if kind in {list, tuple}:
        for item in value:
            _plain(item)
        return
    raise ValueError('foreign object in recording data')


def _argv(argv):
    _need(type(argv) in {list, tuple} and argv and all(type(x) in {str, _PATH_TYPE} for x in argv),
          'plain nonempty recorded argv required')
    return [_os.fspath(x) for x in argv]


def _options(options, allowed):
    _need(type(options) is dict and set(options) <= allowed, 'unobserved subprocess option refused')
    for key in ('capture_output', 'check', 'text', 'start_new_session'):
        if key in options:
            _need(type(options[key]) is bool, 'plain boolean subprocess option required')
    if options.get('cwd') is not None:
        _need(type(options['cwd']) in {str, _PATH_TYPE}, 'plain subprocess cwd required')
    if options.get('env') is not None:
        env = options['env']
        _need(type(env) is dict and all(type(k) is str and type(v) is str for k, v in env.items()),
              'plain subprocess environment required')
    if options.get('timeout') is not None:
        value = options['timeout']
        _need(type(value) in {int, float} and _math.isfinite(value) and value >= 0, 'finite timeout required')
    for key in ('stdout', 'stderr'):
        value = options.get(key)
        _need(value is None or type(value) is int and value in ({PIPE, STDOUT} if key == 'stderr' else {PIPE})
              or type(value) in {_io.TextIOWrapper, _io.BufferedWriter, _io.FileIO},
              'plain actual subprocess stream required')


def _recorder_caller(state):
    frame = _sys._getframe(2)
    recorder = state.refs['recorder']
    methods = state.refs['recorder_methods']
    _need(frame.f_locals.get('self') is recorder and any(
        frame.f_code is methods[name].__code__ and frame.f_globals is methods[name].__globals__
        for name in ('run', 'command')), 'bounded run requires the actual Recorder caller')


def _child(state, process, cleanup=False):
    _need(type(process) is state.refs['guard'], 'actual guarded child object required')
    found = [row for row in state.children if row.process is process]
    _need(len(found) == 1, 'actual owned recorded child required')
    row = found[0]
    data = _dict(process)
    _need(type(data.get('pid')) is int and data['pid'] == row.pid, 'actual child PID changed')
    _wait_owner(state, process)
    if not cleanup:
        _need(type(row.actual) is list and all(type(item) is str for item in row.actual)
              and _argv(data.get('args')) == row.actual, 'actual child argv changed')
    for name, method in row.methods.items():
        _need(type(method) is _MethodType and method.__self__ is process
              and method.__func__ is state.refs['process_functions'][name][0]
              and name not in data and getattr(process, name).__func__ is method.__func__,
              'child method shadow refused: ' + name)
    for name, stream in row.streams:
        _need(data.get(name) is stream, 'actual child stream owner changed')
    if not cleanup:
        _need(data.get('wait') is row.wait_hook, 'recorded child wait owner changed')
    _need(type(row.wait_hook) is _MethodType and row.wait_hook.__self__ is process
          and row.wait_hook.__func__ is _observed_wait, 'actual owned wait hook required')
    _wait_provenance(state, process, row.wait_original)
    return row


def _wait_provenance(state, process, wait):
    _recorder_methods(state)
    if type(wait) is _FunctionType:
        original = state.refs['recorder_methods']['popen']
        _need(wait.__globals__ is original.__globals__
              and any(wait.__code__ is c for c in state.refs['recorder_popen_codes']),
              'actual Recorder wait function required')
        cells = dict(zip(wait.__code__.co_freevars, wait.__closure__ or ()))
        _need(set(cells) == {'self', 'process', 'original_wait'}
              and cells['self'].cell_contents is state.refs['recorder']
              and cells['process'].cell_contents is process, 'actual Recorder wait closure owner required')
        underlying = cells['original_wait'].cell_contents
    else:
        underlying = wait
    _need(type(underlying) is _MethodType and underlying.__self__ is process
          and underlying.__func__ is state.refs['process_functions']['wait'][0],
          'actual underlying Popen wait required')
    return wait


def _recorder_methods(state):
    """Same FunctionType identity cannot grant changed code or bindings."""
    for name, function in state.refs['recorder_methods'].items():
        code, namespace, defaults, keyword_defaults, closure = state.refs['recorder_signatures'][name]
        _need(_fn(state.refs['recorder_class'], name) is function
              and function.__code__ is code and function.__globals__ is namespace
              and function.__defaults__ is defaults and function.__kwdefaults__ is keyword_defaults
              and function.__closure__ is closure, 'original Recorder invocation changed')


def _observed_wait(process, timeout=None):
    state = _verify(_ACTIVE)
    row = _child(state, process)
    kernel = _wait_owner(state, process)
    if kernel.values.get('returncode') is not None:
        _kernel_poll(process)
    try:
        code = row.wait_original(timeout=timeout)
    except BaseException:
        state.events.append({'kind': 'wait-error', 'pid': process.pid})
        raise
    _wait_proof(state, kernel, code)
    row.wait_completed, row.exit_code = True, code
    state.events.append({'kind': 'wait', 'pid': process.pid, 'exit_code': code})
    return code


def _adopt(state, process, requested, options, failed=False, startup_failure=None):
    _need(type(process) is state.refs['guard'], 'actual guarded child required before recording')
    kernel = _capture_wait_owner(state, process)
    actual = _argv(_dict(process).get('args'))
    row = __som_native_ChildState__(process, list(requested), actual,
        str(_Path(options.get('cwd') or _Path.cwd()).absolute()), options.get('start_new_session', False))
    _need(row.pid == kernel.pid and _dict(process).get('pid') == kernel.pid,
          'actual captured child PID changed during adoption')
    for name in ('communicate', 'kill', 'terminate', 'poll'):
        _need(name not in _dict(process), 'foreign child instance method refused')
        method = getattr(process, name)
        _need(type(method) is _MethodType and method.__self__ is process,
              'actual bound child method required')
        row.methods[name] = method
    wait = _wait_provenance(state, process, getattr(process, 'wait'))
    row.wait_original = wait
    row.wait_hook = _MethodType(_observed_wait, process)
    row.failed_before_return = failed
    row.streams = list(kernel.streams)
    _need(not kernel.foreign_streams
          and all(_dict(process).get(name) is stream for name, stream in kernel.streams),
          'actual child pipe object required')
    process.wait = row.wait_hook
    state.children.append(row)
    expected = state.refs['python_argv'](requested, cwd=options.get('cwd'))
    _need(actual == expected, 'actual recorded child argv differs from Context rewrite')
    python = bool(_re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?', _Path(requested[0]).name))
    receipt = _dict(process).get('__som_native_startup_receipt__')
    if python and receipt is not None:
        _need(type(receipt) is tuple and len(receipt) == 2 and type(receipt[1]) is dict,
              'actual Python startup receipt required')
        path, proof = receipt
        path = _physical(path)
        ctx = _dict(state.refs['ctx'])
        _need(path.parent == ctx['work'] / 'python-child-startups'
              and path.read_bytes() == (_json.dumps(proof, ensure_ascii=False, sort_keys=True,
                    separators=(',', ':'), allow_nan=False) + '\n').encode()
              and proof['pid'] == process.pid and proof['actual_argv'] == actual
              and proof['original_requested_argv'] == requested
              and proof['helper_revision'] == ctx['helper_revision'] == proof['candidate_revision'],
              'actual Python startup PID/argv/source receipt differs')
        row.startup_receipt = (path, proof)
    elif python:
        _need(failed and type(startup_failure) is dict
              and any(startup_failure is item for item in _dict(state.refs['python_owner'])['startup_failures']),
              'actual Context startup failure evidence required')
        row.startup_failure = startup_failure
    elif not python:
        _need(receipt is None, 'nonPython child cannot carry a Python startup receipt')
    return row


def Popen(argv, *args, **kwargs):
    state = _verify(_ACTIVE)
    _need(not state.closing, 'recording scope is closing')
    _need(not args, 'recorded Popen accepts only named finite options')
    requested = _argv(argv)
    _options(kwargs, _POPEN_OPTIONS)
    previous = state.phase
    if previous is not None:
        frame = _sys._getframe(1)
        _need(previous == 'bounded' and frame.f_code is _bounded_run.__code__
              and frame.f_globals is globals(), 'recorded Popen reentry refused')
    state.phase = 'popen'
    try:
        method = state.refs['recorder_methods']['popen']
        try:
            process = method(state.refs['recorder'], requested, **kwargs)
        except BaseException as primary:
            child = BaseException.__getattribute__(primary, '__dict__').get('__som_native_startup_child__')
            proof = BaseException.__getattribute__(primary, '__dict__').get('__som_native_startup_failure__')
            if child is None:
                trace = BaseException.__getattribute__(primary, '__traceback__')
                while trace is not None:
                    frame = trace.tb_frame
                    if (frame.f_code is method.__code__ and frame.f_globals is method.__globals__
                            and frame.f_locals.get('self') is state.refs['recorder']
                            and frame.f_locals.get('argv') is requested):
                        child = frame.f_locals.get('process')
                        break
                    trace = trace.tb_next
            if child is not None:
                if proof is not None:
                    state.startup_failures.append((primary, proof))
                try:
                    row = _adopt(state, child, requested, kwargs, failed=True, startup_failure=proof)
                    if proof and proof.get('wait_completed'):
                        _owned_process_operations(state, child, row.pid)
                        code = row.wait_original(timeout=10)
                        _wait_proof(state, _wait_owner(state, child), code)
                        row.wait_completed, row.exit_code = True, code
                    else:
                        _stop_and_wait(state, row, primary)
                except BaseException as error:
                    _secondary(primary, 'record-startup-failure', error)
                    existing = [row for row in state.children if row.process is child]
                    if len(existing) == 1:
                        _stop_and_wait(state, existing[0], primary)
                    else:
                        _cleanup_unregistered(state, child, primary, 'recorder-callback')
            raise
        try:
            _adopt(state, process, requested, kwargs)
        except BaseException as primary:
            existing = [row for row in state.children if row.process is process]
            if len(existing) == 1:
                _stop_and_wait(state, existing[0], primary)
            else:
                _cleanup_unregistered(state, process, primary, 'adoption')
            raise
        return process
    finally:
        state.phase = previous


def _owned_process_operations(state, process, pid):
    """Return only captured actual operations on the proved created child."""
    _need(type(process) is state.refs['guard'] and type(pid) is int,
          'actual created child/PID required for cleanup')
    _need(state.refs['pid'] == _os.getpid() and state.refs['thread'] == _threading.get_ident(),
              'actual recording cleanup owner required')
    guard = state.refs['guard']
    guard_namespace = type.__getattribute__(guard, '__dict__')
    _need(type.__getattribute__(guard, '__mro__') is state.refs['guard_mro']
          and len(guard_namespace) == len(state.refs['guard_namespace'])
          and all(guard_namespace.get(name) is value for name, value in state.refs['guard_namespace'].items()),
          'actual guarded process class changed before cleanup')
    _need(_dict(process).get('pid') == pid and _dict(process).get('_child_created') is True,
          'actual created child/PID required for cleanup')
    _kernel_dependencies(state, cleanup=True)
    _decorated_methods(state)
    matches = [item for item in state.kernel_waits if item.process is process]
    kernel = matches[0] if len(matches) == 1 else None
    lock = _dict(process).get('_waitpid_lock')
    _need(type(lock) is state.refs['lock_type'] and (kernel is None or lock is kernel.lock),
          'actual retained native waitpid lock required')
    namespace = type.__getattribute__(state.refs['base_popen'], '__dict__')
    _need(len(namespace) == len(state.refs['base_namespace'])
              and all(namespace.get(name) is value for name, value in state.refs['base_namespace'].items()),
              'original process class changed before cleanup')
    for name, signature in state.refs['process_functions'].items():
        function, code, globals_dict, defaults, keyword_defaults, closure = signature
        _need(function.__code__ is code and function.__globals__ is globals_dict
                  and function.__defaults__ is defaults and function.__kwdefaults__ is keyword_defaults
                  and function.__closure__ is closure, 'original process operation changed before cleanup')
        if name.startswith('_'):
            if kernel is not None and name in {'_internal_poll', '_try_wait'}:
                hook = kernel.hooks[name]
                expected = _kernel_poll if name == '_internal_poll' else _kernel_try_wait
                if kernel.installed:
                    _need(_dict(process).get(name) is hook and type(hook) is _MethodType
                          and hook.__self__ is process and hook.__func__ is expected,
                          'owned kernel process hook changed')
                else:
                    _need(name not in _dict(process), 'foreign private kernel hook before installation')
            else:
                _need(name not in _dict(process), 'private process operation shadow refused during cleanup')
    return (_MethodType(state.refs['process_functions']['poll'][0], process),
            _MethodType(state.refs['process_functions']['wait'][0], process))


def _cleanup_unregistered(state, process, primary, branch):
    """Both post-create/pre-registration failures use this owned cleanup.

    This records actual cleanup attempts, not a fabricated returned child row.
    Streams close in finally even when another owned cleanup operation fails.
    """
    streams = []
    proof = kernel = None
    previous_primary = None
    try:
        _need(type(process) is state.refs['guard'], 'actual guarded unregistered child required')
        values = _dict(process)
        pid = values.get('pid')
        _need(type(pid) is int and values.get('_child_created') is True,
              'actual created unregistered child PID required')
        proof = {'branch': branch, 'pid': pid, 'wait_completed': False, 'exit_code': None,
                 'signal_attempted': False, 'signal_completed': False, 'streams_closed': {}}
        state.pre_registration_cleanup.append(proof)
        for name in ('stdout', 'stderr', 'stdin'):
            stream = values.get(name)
            if stream is None:
                continue
            if type(stream) in {_io.TextIOWrapper, _io.BufferedReader, _io.BufferedWriter, _io.FileIO}:
                streams.append((name, stream))
                proof['streams_closed'][name] = False
            else:
                _secondary(primary, 'record-unregistered-stream-owner',
                           ValueError('foreign unregistered child stream refused'))
        kernel = _capture_wait_owner(state, process)
        for name, stream in kernel.streams:
            if stream is not None and not any(known is stream for unused, known in streams):
                streams.append((name, stream))
                proof['streams_closed'][name] = False
        previous_primary, kernel.cleanup_primary = kernel.cleanup_primary, primary
        poll, wait = _owned_process_operations(state, process, pid)
        selected = values.get('wait', wait)
        try:
            wait = _wait_provenance(state, process, selected)
        except BaseException as error:
            # Keep a foreign public wait untouched. The captured real base wait
            # remains safe after all private operation guards above succeeded.
            _secondary(primary, 'record-unregistered-wait-owner', error)
        code = None
        try:
            code = poll()
        except BaseException as error:
            _secondary(primary, 'record-unregistered-poll', error)
        _need(kernel.terminal or kernel.live_observed and not kernel.unknown,
              'unregistered cleanup requires an actual live or terminal kernel observation')
        if code is None:
            proof['signal_attempted'] = True
            try:
                _retained_kill(state, pid, primary)
                proof['signal_completed'] = True
            except BaseException as error:
                _secondary(primary, 'record-unregistered-kill', error)
        try:
            code = wait(timeout=10)
            _wait_proof(state, kernel, code)
            proof['wait_completed'], proof['exit_code'] = True, code
        except BaseException as error:
            _secondary(primary, 'record-unregistered-wait', error)
    except BaseException as error:
        _secondary(primary, 'record-unregistered-owner', error)
    finally:
        if kernel is not None:
            kernel.cleanup_primary = previous_primary
        for name, stream in streams:
            try:
                stream.close()
                proof['streams_closed'][name] = stream.closed is True
            except BaseException as error:
                _secondary(primary, 'record-unregistered-stream-close', error)


def _stop_and_wait(state, row, primary):
    """Use captured real operations even if a public instance method was shadowed.

    A live unreaped child still owns its PID. A terminal poll skips signaling.
    Private method shadows make the retained stdlib wait unsafe and are refused.
    """
    process = row.process
    kernel = None
    previous_primary = None
    try:
        kernel = _wait_owner(state, process)
        previous_primary, kernel.cleanup_primary = kernel.cleanup_primary, primary
        poll, wait = _owned_process_operations(state, process, row.pid)
    except BaseException as error:
        _secondary(primary, 'recorded-cleanup-owner', error)
        if kernel is not None:
            kernel.cleanup_primary = previous_primary
        return
    try:
        wait = _wait_provenance(state, process, row.wait_original)
    except BaseException as error:
        # The observer is refused. The held original base wait and fixed owned
        # kernel hooks can still reap this child without executing that code.
        _secondary(primary, 'recorded-cleanup-observer-owner', error)
    code = None
    try:
        code = poll()
    except BaseException as error:
        _secondary(primary, 'recorded-cleanup-poll', error)
        kernel.cleanup_primary = previous_primary
        return
    if not (kernel.terminal or kernel.live_observed and not kernel.unknown):
        _secondary(primary, 'recorded-cleanup-kernel-unknown',
                   ValueError('no actual live or terminal kernel observation for cleanup'))
        kernel.cleanup_primary = previous_primary
        return
    if code is None:
        try:
            _retained_kill(state, row.pid, primary)
        except BaseException as error:
            _secondary(primary, 'recorded-cleanup-kill', error)
    try:
        code = wait(timeout=10)
        _wait_proof(state, kernel, code)
        row.wait_completed, row.exit_code = True, code
    except BaseException as error:
        _secondary(primary, 'recorded-cleanup-wait', error)
    finally:
        kernel.cleanup_primary = previous_primary


def _bounded_run(argv, *args, **kwargs):
    # Linux subprocess.run kills and waits after TimeoutExpired. POSIX
    # communicate already put partial bytes on that same exception. A second
    # communicate would change that behavior. Close captured pipes in finally.
    state = _verify(_ACTIVE)
    _recorder_caller(state)
    _need(not args and state.phase in {None, 'run'}, 'bounded run reentry or positional options refused')
    _options(kwargs, _RUN_OPTIONS)
    options = dict(kwargs)
    capture = options.pop('capture_output', False)
    check = options.pop('check', False)
    timeout = options.pop('timeout', None)
    if capture:
        options.update(stdout=PIPE, stderr=PIPE)
    previous, state.phase = state.phase, 'bounded'
    process = row = None
    primary = None
    try:
        process = Popen(argv, **options)
        row = _child(state, process)
        try:
            stdout, stderr = row.methods['communicate'](timeout=timeout)
        except BaseException as error:
            primary = error
            _stop_and_wait(state, row, error)
            raise
        code = process.returncode
        _need(row.wait_completed and type(code) is int and row.exit_code == code, 'run requires a real terminal wait')
        if check and code:
            raise _CALLED_ERROR(code, process.args, output=stdout, stderr=stderr)
        return _COMPLETED(process.args, code, stdout, stderr)
    except BaseException as error:
        primary = error
        raise
    finally:
        if process is not None:
            for name, stream in (row.streams if row is not None else []):
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as error:
                        if primary is not None:
                            _secondary(primary, 'recorded-stream-close', error)
                        else:
                            failure = ('recorded-stream-close', error,
                                       BaseException.__getattribute__(error, '__traceback__'))
                            state.secondary.append(failure)
                            state.failures.append(failure)
        state.phase = previous


def run(argv, *args, **kwargs):
    state = _verify(_ACTIVE)
    _need(not state.closing, 'recording scope is closing')
    _need(not args and state.phase is None, 'recorded run reentry or positional options refused')
    requested = _argv(argv)
    _options(kwargs, _RUN_OPTIONS)
    state.phase = 'run'
    try:
        method = state.refs['recorder_methods']['run']
        return method(state.refs['recorder'], requested, **kwargs)
    finally:
        state.phase = None


def _close(token, primary=None):
    """Preserve an explicit or active body exception; return without replacing it."""
    global _ACTIVE
    primary = _sys.exception() if primary is None else primary
    _need(primary is None or any(base is BaseException for base in
          type.__getattribute__(type(primary), '__mro__')), 'actual primary exception required')
    failures = []
    try:
        _need(type(token) is __som_native_ScopeState__ and token is _ACTIVE and not token.closed,
              'actual owned recording scope required for close')
        _need(type(token.refs) is dict and type(token.refs.get('thread')) is int
              and token.refs['thread'] == _threading.get_ident()
              and token.refs.get('pid') == _os.getpid(), 'recording close owner thread differs')
    except BaseException as error:
        if primary is not None:
            _secondary(primary, 'recording-close-owner', error)
            return
        raise
    state, refs = token, token.refs
    failures.extend(state.failures)
    state.closing = True
    try:
        try:
            _verify(state)
        except BaseException as error:
            failures.append(('recording-postcheck', error, error.__traceback__))
        children = state.children
        if type(children) is not list:
            error = ValueError('actual recording child list required during close')
            failures.append(('recording-children-owner', error, None))
            children = []
        for row in children:
            if type(row) is not __som_native_ChildState__:
                error = ValueError('foreign child record refused during close')
                failures.append(('recording-child-owner', error, None))
                continue
            try:
                _child(state, row.process)
                _need(type(row.wait_completed) is bool
                      and (not row.wait_completed or type(row.exit_code) is int
                           and _dict(row.process).get('returncode') == row.exit_code),
                      'actual recorded terminal wait changed')
                if row.wait_completed:
                    _wait_proof(state, _wait_owner(state, row.process), row.exit_code)
            except BaseException as error:
                failures.append(('recording-child-postcheck', error,
                                 BaseException.__getattribute__(error, '__traceback__')))
            if not row.wait_completed:
                error = RuntimeError('recorded child lacked its original terminal wait')
                failures.append(('recording-missing-wait', error, error.__traceback__))
                _stop_and_wait(state, row, error)
            for name, stream in row.streams:
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as error:
                        failures.append(('recording-stream-close', error,
                                         BaseException.__getattribute__(error, '__traceback__')))
            try:
                _need(_dict(row.process).get('wait') is row.wait_hook,
                      'recorded wait replaced by a later owner')
                _wait_provenance(state, row.process, row.wait_original)
                row.process.wait = row.wait_original
            except BaseException as error:
                failures.append(('recording-wait-restore', error, error.__traceback__))
        try:
            payload = {'version': 'som.native-m6-recorded-processes.v1',
                'children': [{'pid': row.pid, 'requested_argv': row.requested,
                    'actual_argv': row.actual, 'cwd': row.cwd, 'start_new_session': row.new_session,
                    'wait_completed': row.wait_completed, 'exit_code': row.exit_code,
                    'failed_before_return': row.failed_before_return,
                    'Python_startup_receipt': None if row.startup_receipt is None else str(row.startup_receipt[0])}
                    for row in children if type(row) is __som_native_ChildState__],
                'events': refs['recorder'].events, 'recording_errors': list(refs['recorder'].errors),
                'pre_registration_cleanup': state.pre_registration_cleanup,
                'kernel_waits': [_kernel_ledger(row) for row in state.kernel_waits],
                'stdlib_method_provenance': [{'method': name,
                    'helper_qualname': record['functions'][0][1].co_qualname,
                    'underlying_qualname': record['functions'][2][1].co_qualname,
                    'sources': [{'path': str(path), 'sha256': before} for path, before in record['sources']],
                    'compile_mode': 'exec', 'dont_inherit': True, 'optimize': _sys.flags.optimize,
                    'compiled_code_executed': False}
                    for name, record in refs['process_decorated'].items()],
                'product_acceptance_passed': False}
            with (refs['out'] / 'native-recorded-processes.json').open('xb') as stream:
                stream.write((_json.dumps(payload, sort_keys=True, separators=(',', ':')) + '\n').encode())
        except BaseException as error:
            failures.append(('recording-evidence', error, error.__traceback__))
    finally:
        try:
            _restore_kernel_hooks(state, failures)
        except BaseException as error:
            failures.append(('recording-kernel-hook-restore', error,
                             BaseException.__getattribute__(error, '__traceback__')))
        if _ACTIVE is state:
            _ACTIVE = None
        else:
            error = RuntimeError('recording scope replaced by a later owner')
            failures.append(('recording-state-owner', error, error.__traceback__))
        state.closed = True
        if type(state.children) is list:
            list.clear(state.children)
        if type(state.pre_registration_cleanup) is list:
            list.clear(state.pre_registration_cleanup)
        if type(state.kernel_waits) is list:
            list.clear(state.kernel_waits)
        owned_closes = []
        try:
            for cls, expected_mro, expected in refs['python_close_classes']:
                namespace = type.__getattribute__(cls, '__dict__')
                _need(type.__getattribute__(cls, '__mro__') is expected_mro
                      and len(namespace) == len(expected)
                      and all(namespace.get(name) is value for name, value in expected.items()),
                      'borrowed Python close class changed')
            for function, code, globals_dict, defaults, keyword_defaults, closure in refs['python_close_functions']:
                _need(function.__code__ is code and function.__globals__ is globals_dict
                      and function.__defaults__ is defaults and function.__kwdefaults__ is keyword_defaults
                      and function.__closure__ is closure, 'borrowed Python close invocation changed')
            lease = refs['python_borrow']
            python_type, control_type = (row[0] for row in refs['python_close_classes'])
            _need(type(lease) is python_type
                  and _dict(lease).get('controls') is refs['python_owner']
                  and _dict(lease).get('owns_controls') is False
                  and type(_dict(lease).get('closed')) is bool
                  and not {'verify_installed', 'close'} & set(_dict(lease)),
                  'actual owned borrowed Python lease required during close')
            owner = refs['python_owner']
            _need(type(owner) is control_type and _dict(owner).get('closed') is False
                  and not {'verify_installed', 'close'} & set(_dict(owner))
                  and _dict(owner).get('ctx') is refs['ctx'] and _dict(owner).get('wrapper') is refs['guard']
                  and _subprocess.Popen is refs['guard']
                  and refs['context_namespace'].get('subprocess') is _subprocess
                  and refs['context_namespace'].get('require') is refs['context_require'],
                  'borrowed Python close verifier owner changed')
            function = _fn(python_type, 'close')
            _need(function is refs['python_close'], 'borrowed Python close source changed')
            code, globals_dict, defaults, keyword_defaults, closure = refs['python_close_signature']
            _need(function.__code__ is code and function.__globals__ is globals_dict
                  and function.__defaults__ is defaults and function.__kwdefaults__ is keyword_defaults
                  and function.__closure__ is closure, 'borrowed Python close invocation changed')
            owned_closes.append(('recording-python-borrow', _MethodType(function, lease)))
        except BaseException as error:
            failures.append(('recording-python-borrow-owner', error,
                             BaseException.__getattribute__(error, '__traceback__')))
        try:
            lease = refs['borrow']
            cls = refs['source_lease_type']
            _need(type(lease) is cls
                  and object.__getattribute__(lease, '_domain') is refs['source_domain']
                  and object.__getattribute__(lease, '_owns') is False,
                  'actual owned borrowed source lease required during close')
            namespace = type.__getattribute__(cls, '__dict__')
            _need(type.__getattribute__(cls, '__mro__') is refs['source_lease_mro']
                  and len(namespace) == len(refs['source_lease_namespace'])
                  and all(namespace.get(name) is value for name, value in refs['source_lease_namespace'].items()),
                  'borrowed source lease class changed before close')
            retained = refs['source_close']
            _need(type(retained) is _MethodType and retained.__self__ is lease
                  and retained.__func__ is namespace.get('close'),
                  'borrowed source close pointer changed')
            function = retained.__func__
            code, globals_dict, defaults, keyword_defaults, closure = refs['source_close_signature']
            _need(function.__code__ is code and function.__globals__ is globals_dict
                  and function.__defaults__ is defaults and function.__kwdefaults__ is keyword_defaults
                  and function.__closure__ is closure, 'borrowed source close invocation changed')
            owned_closes.append(('recording-source-borrow', retained))
        except BaseException as error:
            failures.append(('recording-source-borrow-owner', error,
                             BaseException.__getattribute__(error, '__traceback__')))
        refs.clear()
        for stage, operation in owned_closes:
            try:
                operation()
            except BaseException as error:
                failures.append((stage, error, error.__traceback__))
    if primary is not None:
        for stage, error, trace in failures:
            _secondary(primary, stage, error)
        return
    if failures:
        stage, first, trace = failures[0]
        for later_stage, error, unused in failures[1:]:
            _secondary(first, later_stage, error)
        raise BaseException.with_traceback(first, trace)
