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
from types import GeneratorType as _GeneratorType, CellType as _CellType

PIPE = _subprocess.PIPE
STDOUT = _subprocess.STDOUT
TimeoutExpired = _subprocess.TimeoutExpired

_SELF = 'som-rs/scripts/react_output_acceptance/native_recorded_subprocess.py'
_OWNER = 'som-rs/scripts/react_output_acceptance/native_recording_owner.py'
_CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
_ROLE = 'm6.api.recording.subprocess'
_OWNER_ROLE = 'm6.api.recording.owner'
_ACTIVE = None
_CAPTURE = None
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
    _need(any(type(path) is kind for kind in (str, _PATH_TYPE)), 'plain recorder path required')
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
        if any(kind is accepted for accepted in (str, bytes, int, float, complex, bool, type(None), type(Ellipsis))):
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


def _retained_kill(state, process, primary):
    """Signal only the original producer-held live PID with the actual builtin."""
    _kernel_request(state, process, 'kill', primary)


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


def _binding_state(value, seen=None):
    """A comparison value. Never hash or compare a foreign object's type."""
    seen = set() if seen is None else seen
    kind = type(value)
    if any(kind is accepted for accepted in (str, bytes, int, float, complex, bool, type(None))):
        return ('atom', id(kind), value)
    identity = id(value)
    if identity in seen:
        return ('cycle', id(kind), identity)
    seen.add(identity)
    if kind is tuple or kind is list:
        return ('sequence', id(kind), identity, tuple(_binding_state(x, seen) for x in value))
    if kind is dict:
        return ('dict', identity, tuple((_binding_state(k, seen), _binding_state(v, seen))
                                       for k, v in dict.items(value)))
    if kind is _CellType:
        try:
            return ('cell', identity, _binding_state(value.cell_contents, seen))
        except ValueError:
            return ('cell', identity, 'empty')
    return ('opaque', id(kind), identity)


def _invocation(function):
    return (function.__code__, function.__globals__, function.__defaults__, function.__kwdefaults__,
            function.__closure__, function.__builtins__,
            (_binding_state(function.__defaults__), _binding_state(function.__kwdefaults__), _binding_state(function.__closure__)))


def _capture_preflight(functions, classes):
    """Pure builtin checks. This must precede every Core/Git/source spawn."""
    for name, function, signature in functions:
        code, namespace, defaults, keywords, closure, builtins, unused = signature
        if (type(function) is not _FunctionType or function.__code__ is not code
                or function.__globals__ is not namespace or function.__defaults__ is not defaults
                or function.__kwdefaults__ is not keywords or function.__closure__ is not closure
                or function.__builtins__ is not builtins):
            return ('PF001', 'producer-held source invocation changed before source verification')
        if name is not None and dict.get(globals(), name) is not function:
            return ('PF002', 'producer-held facade function changed before source verification')
    for name, function, signature in functions:
        if (_binding_state(function.__defaults__), _binding_state(function.__kwdefaults__), _binding_state(function.__closure__)) != signature[6]:
            return ('PF003', 'producer-held invocation bindings changed before source verification')
    for cls, mro, expected in classes:
        if type(cls) is not type or type.__getattribute__(cls, '__mro__') is not mro:
            return ('PF004', 'actual guarded process class changed before source verification')
        namespace = type.__getattribute__(cls, '__dict__')
        if len(namespace) != len(expected) or not all(namespace.get(name) is value for name, value in expected):
            return ('PF004', 'actual guarded process class changed before source verification')


def _capture_request(state, action, process=None, operation=None, argument=None):
    """Fixed source construction and dispatch, anchored by executing code."""
    global _CAPTURE
    if type(action) is not str:
        raise ValueError('exact finite capture action required')
    def _acquire(ctx, out, qa=None):
        """Fixed private lifecycle. Caller joins and real API input validation remain separate."""
        global _ACTIVE, _CAPTURE
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
            _need(all((type(qa.get(name)) is str or type(qa.get(name)) is _PATH_TYPE)
                      for name in ('python', 'node', 'pg_bin', 'lsof', 'ps')), 'complete original QA tool data required')
        state = __som_native_ScopeState__()
        borrow = controls.borrow()
        python_borrow = None
        created_capture = None
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
            created_capture = _producer_capture(state)
            _CAPTURE = created_capture
            next(created_capture)
            _verify(state)
            return state
        except BaseException as primary:
            if created_capture is not None:
                _GeneratorType.close(created_capture)
                if _CAPTURE is created_capture:
                    _CAPTURE = None
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

    def _producer_capture(state):
        """Private CPython3.12.3 fast locals, not a general Python sandbox.
    
        Actual producer frames establish ownership. No channel or kernel result is
        accepted from public rows, refs, caller input or a claimed signature.
        """
        frame = _sys._getframe(1)
        if (frame.f_code is not _acquire_code or frame.f_globals is not globals()
                or frame.f_locals.get('state') is not state or state is not _ACTIVE):
            raise ValueError('actual recording producer frame required')
        acquisition, factory = frame, frame.f_back
        if factory is None or factory.f_code is not _request_code or factory.f_globals is not globals():
            raise ValueError('actual immutable acquisition factory required')
        refs = state.refs
        guard, base, ctx = refs['guard'], refs['base_popen'], refs['ctx']
        owner_pid, owner_thread = _os.getpid(), _threading.get_ident()
        guard_error, marker = ValueError, object()
        generator_type = type(item for item in ())
        generator_namespace = type.__getattribute__(generator_type, '__dict__')
        getframe, send, advance = _sys._getframe, generator_namespace['send'], next
        close_channel = generator_namespace['close']
        if (type(getframe) is not _BuiltinFunctionType or getframe.__self__ is not _sys
                or getframe.__name__ != '_getframe'):
            raise ValueError('actual producer frame primitive required')
        functions = tuple((name, function, _invocation(function))
            for name, function in tuple(globals().items())
            if type(function) is _FunctionType and function.__globals__ is globals())
        source_codes = tuple((name, signature[0]) for name, function, signature in functions)
        source_codes += (('_producer_capture', _producer_code), ('_acquire', _acquire_code))
        methods = tuple((name, function) for name, function in type.__getattribute__(base, '__dict__').items()
                        if type(function) is _FunctionType)
        more = tuple((None, function, _invocation(function)) for unused, function in methods)
        more += tuple((None, item[0], _invocation(item[0]))
            for record in refs['process_decorated'].values() for item in record['functions'])
        more += tuple((None, value, _invocation(value))
            for value in type.__getattribute__(guard, '__dict__').values() if type(value) is _FunctionType)
        more += tuple((None, function, _invocation(function)) for cls, name, function in refs['source_methods']
                      if cls is not refs['recorder_class'] and name != 'close')
        more += tuple((None, _fn(__som_native_NativePythonControls__, 'verify_installed'),
                       _invocation(_fn(__som_native_NativePythonControls__, 'verify_installed')))
                      for unused in (None,))
        context_namespace, context_require = refs['context_namespace'], refs['context_require']
        more += ((None, context_require, _invocation(context_require)),)
        initializer = _fn(__som_native_KernelWaitState__, '__init__')
        more += ((None, initializer, _invocation(initializer)),)
        functions += more
        classes = tuple((cls, type.__getattribute__(cls, '__mro__'),
                         tuple(type.__getattribute__(cls, '__dict__').items()))
                        for cls in (guard, base, type(ctx), type(refs['python_owner']), type(refs['parent_python']),
                                    __som_native_KernelWaitState__))
        decorated_bindings = tuple((record['cell'], record['underlying'], record['wrapper'])
                                   for record in refs['process_decorated'].values())
        preflight = _capture_preflight
        primitive_wait, primitive_decode, lock_type, lock_methods = _kernel_primitives()
        primitive_kill, kill_signal = _kill_primitive()
        primitive_config = (primitive_wait, primitive_decode, primitive_kill, kill_signal,
                            lock_type, tuple(lock_methods.items()), _posix, _os)
        row_type = __som_native_KernelWaitState__
        context_fields = object.__getattribute__(ctx, '__dict__')
        python_fields = object.__getattribute__(refs['python_owner'], '__dict__')
        lease_fields = object.__getattribute__(refs['parent_python'], '__dict__')
        recorder_class, recorder = refs['recorder_class'], refs['recorder']
        recorder_methods = tuple((name, function, _invocation(function))
                                 for name, function in refs['recorder_methods'].items())
        recorder_codes = tuple(refs['recorder_popen_codes'])
        # Private originals remain live through evidence and both real closes.
        source_lease = refs['borrow']
        python_lease, python_owner = refs['python_borrow'], refs['python_owner']
        source_type, python_type, control_type = type(source_lease), type(python_lease), type(python_owner)
        source_domain = object.__getattribute__(source_lease, '_domain')
        source_close = type.__getattribute__(source_type, '__dict__')['close']
        python_close = type.__getattribute__(python_type, '__dict__')['close']
        def class_seal(cls):
            namespace = type.__getattribute__(cls, '__dict__')
            return (cls, type.__getattribute__(cls, '__mro__'), tuple(namespace.items()),
                    tuple((value, _invocation(value)) for value in namespace.values() if type(value) is _FunctionType))
        source_seal = class_seal(source_type)
        python_seals = tuple(class_seal(cls) for cls in (python_type, control_type))
        source_signature, python_signature = _invocation(source_close), _invocation(python_close)
        source_slots = tuple((key, type.__getattribute__(source_type, '__dict__')[key]) for key in ('_domain', '_owns', '_closed'))
        python_values = object.__getattribute__(python_lease, '__dict__')
        owner_values = object.__getattribute__(python_owner, '__dict__')
        subprocess_module = _subprocess
        require_signature = _invocation(context_require)
        recorder_values = object.__getattribute__(recorder, '__dict__')
        recorder_events, recorder_errors = dict.get(recorder_values, 'events'), dict.get(recorder_values, 'errors')
        recorder_seal = class_seal(recorder_class)
        errors_type, child_type = type(recorder_errors), __som_native_ChildState__
        errors_seal, child_seal = class_seal(errors_type), class_seal(child_type)
        errors_values = object.__getattribute__(recorder_errors, '__dict__')
        original_out = refs['out']
        evidence_target = str(original_out / 'native-recorded-processes.json')
        pre_registration = state.pre_registration_cleanup
        decorated = refs['process_decorated']
        decorated_seal = tuple((key, record, record['functions'], record['sources'], tuple(record['functions']), tuple(record['sources']),
            tuple((name, record[name]) for name in ('wrapper', 'cell', 'underlying', 'contextmanager')))
            for key, record in dict.items(decorated))
        decorated_data = tuple((key, record['functions'][0][1].co_qualname, record['functions'][2][1].co_qualname,
            tuple((str(path), before) for path, before in record['sources'])) for key, record in dict.items(decorated))
        child_metadata = ()
        close_code = next(code for name, code in source_codes if name == '_close')
        adopt_code = next(code for name, code in source_codes if name == '_adopt')
        def refuse(tag, reason):
            raise guard_error(marker, ('refusal', tag, reason))
        def signature_ok(function, signature):
            return (type(function) is _FunctionType and function.__code__ is signature[0]
                and function.__globals__ is signature[1] and function.__defaults__ is signature[2]
                and function.__kwdefaults__ is signature[3] and function.__closure__ is signature[4]
                and function.__builtins__ is signature[5]
                and (_binding_state(function.__defaults__), _binding_state(function.__kwdefaults__),
                     _binding_state(function.__closure__)) == signature[6])
        def class_ok(seal):
            cls, mro, pairs, signatures = seal
            if type(cls) is not type or type.__getattribute__(cls, '__mro__') is not mro:
                return False
            namespace = type.__getattribute__(cls, '__dict__')
            return (len(namespace) == len(pairs) and all(namespace.get(key) is value for key, value in pairs)
                and all(signature_ok(function, signature) for function, signature in signatures))
        def dictionary(value, keys=None):
            if type(value) is not dict or not all(type(key) is str for key in dict.keys(value)):
                refuse('E003', 'foreign or changed value in original recording evidence')
            if keys is not None and (len(value) != len(keys) or any(key not in value for key in keys)):
                refuse('E003', 'foreign or changed value in original recording evidence')
            return value
        def scalar(value, kinds):
            if not any(type(value) is kind for kind in kinds):
                refuse('E003', 'foreign or changed value in original recording evidence')
            return value
        def copy_data(value, seen=None):
            kind = type(value)
            if any(kind is accepted for accepted in (str, int, bool, type(None))): return value
            if kind is float and _math.isfinite(value): return value
            if seen is None: seen = set()
            if kind is not dict and kind is not list and kind is not tuple or id(value) in seen:
                refuse('E003', 'foreign or changed value in original recording evidence')
            seen.add(id(value))
            try:
                if kind is dict:
                    dictionary(value)
                    return {key: copy_data(item, seen) for key, item in dict.items(value)}
                return [copy_data(item, seen) for item in (list.__iter__(value) if kind is list else tuple.__iter__(value))]
            finally:
                seen.remove(id(value))
        def frozen_data(value):
            # Called only after copy_data has proved native scalar/container data.
            if type(value) is dict:
                return ('dict', tuple((key, frozen_data(item)) for key, item in dict.items(value)))
            if type(value) is list:
                return ('list', tuple(frozen_data(item) for item in list.__iter__(value)))
            return ('scalar', value)
        def argv_data(value):
            if type(value) is not list or not all(type(item) is str for item in list.__iter__(value)):
                refuse('E003', 'foreign or changed value in original recording evidence')
            return tuple(list.__iter__(value))
        def child_data(row):
            if type(row) is not child_type or not class_ok(child_seal):
                refuse('E003', 'foreign or changed value in original recording evidence')
            values = dictionary(object.__getattribute__(row, '__dict__'))
            return (argv_data(dict.get(values, 'requested')), argv_data(dict.get(values, 'actual')),
                scalar(dict.get(values, 'cwd'), (str,)), scalar(dict.get(values, 'new_session'), (bool,)),
                scalar(dict.get(values, 'failed_before_return'), (bool,)))
        def decorated_ok():
            dictionary(refs)
            if dict.get(refs, 'process_decorated') is not decorated:
                refuse('E003', 'foreign or changed value in original recording evidence')
            dictionary(decorated)
            if len(decorated) != len(decorated_seal): refuse('E003', 'foreign or changed value in original recording evidence')
            for key, record, function_rows, source_rows, signatures, paths, values in decorated_seal:
                if dict.get(decorated, key) is not record: refuse('E003', 'foreign or changed value in original recording evidence')
                dictionary(record, ('wrapper', 'cell', 'underlying', 'functions', 'sources', 'contextmanager'))
                current = dict.get(record, 'functions')
                if current is not function_rows or type(current) is not list or len(current) != len(signatures): refuse('E003', 'foreign or changed value in original recording evidence')
                for actual, expected in zip(list.__iter__(current), signatures):
                    if type(actual) is not tuple or actual is not expected or len(actual) != 7 or not all(a is b for a, b in zip(actual, expected)):
                        refuse('E003', 'foreign or changed value in original recording evidence')
                    function, code, namespace, defaults, keywords, closure, builtins = actual
                    if (type(function) is not _FunctionType or type(code) is not _CodeType or function.__code__ is not code
                            or function.__globals__ is not namespace or function.__defaults__ is not defaults
                            or function.__kwdefaults__ is not keywords or function.__closure__ is not closure or function.__builtins__ is not builtins):
                        refuse('E003', 'foreign or changed value in original recording evidence')
                current = dict.get(record, 'sources')
                if current is not source_rows or type(current) is not list or len(current) != len(paths): refuse('E003', 'foreign or changed value in original recording evidence')
                for actual, expected in zip(list.__iter__(current), paths):
                    if (type(actual) is not tuple or actual is not expected or len(actual) != 2 or type(actual[0]) is not _PATH_TYPE
                            or actual[0] is not expected[0] or type(actual[1]) is not str or actual[1] != expected[1]):
                        refuse('E003', 'foreign or changed value in original recording evidence')
                if any(dict.get(record, name) is not value for name, value in values):
                    refuse('E003', 'foreign or changed value in original recording evidence')
        def retained_file(value):
            dictionary(value, ('file', 'sha256')); scalar(value['file'], (str,)); before = scalar(value['sha256'], (str,))
            if len(before) != 64 or any(ch not in '0123456789abcdef' for ch in before):
                refuse('E003', 'foreign or changed value in original recording evidence')
        def command_data(value):
            dictionary(value, ('argv', 'exit_code', 'error_type', 'stdout', 'stderr'))
            argv_data(value['argv']); scalar(value['exit_code'], (int, type(None))); scalar(value['error_type'], (str, type(None)))
            retained_file(value['stdout']); retained_file(value['stderr'])
        def event_data(event):
            dictionary(event); kind = scalar(dict.get(event, 'kind'), (str,))
            if kind == 'child-start':
                dictionary(event, ('kind', 'pid', 'argv', 'cwd'))
                scalar(event['pid'], (int,)); argv_data(event['argv']); scalar(event['cwd'], (str,))
            elif kind == 'child-wait':
                dictionary(event, ('kind', 'pid', 'state', 'error_type', 'exit_code'))
                scalar(event['pid'], (int,)); state_name = scalar(event['state'], (str,))
                if state_name not in ('completed', 'unverified'): refuse('E003', 'foreign or changed value in original recording evidence')
                scalar(event['error_type'], (str, type(None))); scalar(event['exit_code'], (int, type(None)))
            elif kind == 'postmaster':
                dictionary(event, ('kind', 'phase', 'raw')); scalar(event['phase'], (str,)); retained_file(event['raw'])
            elif kind == 'process':
                keys = ('kind', 'pid', 'phase', 'ps', 'table', 'cwd', 'pgid', 'pgid_error_errno')
                dictionary(event, keys + (('port', 'listener') if 'port' in event or 'listener' in event else ()))
                scalar(event['pid'], (int,)); scalar(event['phase'], (str,))
                scalar(event['pgid'], (int, type(None))); scalar(event['pgid_error_errno'], (int, type(None)))
                for key in ('ps', 'table', 'cwd'): command_data(event[key])
                if 'port' in event: scalar(event['port'], (int,)); command_data(event['listener'])
            elif kind in ('probe', 'pg_ctl', 'initdb', 'listener', 'lsof'):
                dictionary(event, ('kind', 'argv', 'exit_code', 'stdout', 'stderr') + (('pid',) if kind == 'probe' else ()))
                argv_data(event['argv']); scalar(event['exit_code'], (int, type(None)))
                retained_file(event['stdout']); retained_file(event['stderr'])
                if kind == 'probe': scalar(event['pid'], (int, type(None)))
            else: refuse('E003', 'foreign or changed value in original recording evidence')
            return copy_data(event)
        def evidence_data(snapshots):
            dictionary(refs)
            if (dict.get(refs, 'recorder') is not recorder or dict.get(refs, 'out') is not original_out
                    or type(recorder) is not recorder_class or not class_ok(recorder_seal)
                    or object.__getattribute__(recorder, '__dict__') is not recorder_values
                    or type(recorder_errors) is not errors_type or not class_ok(errors_seal)):
                refuse('E002', 'original recording evidence owner or output changed')
            dictionary(recorder_values); dictionary(errors_values)
            if (dict.get(recorder_values, 'events') is not recorder_events or type(recorder_events) is not list
                    or dict.get(recorder_values, 'errors') is not recorder_errors
                    or object.__getattribute__(recorder_errors, '__dict__') is not errors_values
                    or dict.get(errors_values, 'state') is not state or dict.get(errors_values, 'recorder') is not recorder):
                refuse('E002', 'original recording evidence owner or output changed')
            decorated_ok()
            errors = [scalar(item, (str,)) for item in list.__iter__(recorder_errors)]
            events = [event_data(event) for event in list.__iter__(recorder_events)]
            children, ledger = [], []
            for actual in snapshots:
                row = actual['child']
                if row is not None:
                    expected = tuple(item for item in child_metadata if item[0] is row)
                    if len(expected) != 1 or child_data(row) != expected[0][1]: refuse('E003', 'foreign or changed value in original recording evidence')
                    unused, values, receipt = expected[0]
                    current = dictionary(object.__getattribute__(row, '__dict__'))
                    scalar(dict.get(current, 'pid'), (int,))
                    scalar(dict.get(current, 'wait_completed'), (bool,)); scalar(dict.get(current, 'exit_code'), (int, type(None)))
                    if (dict.get(current, 'pid') != actual['pid'] or dict.get(current, 'wait_completed') is not actual['completed']
                            or dict.get(current, 'exit_code') != (actual['code'] if actual['completed'] else None)):
                        refuse('E003', 'foreign or changed value in original recording evidence')
                    current_receipt = dict.get(current, 'startup_receipt'); receipt_path = None
                    if receipt is None:
                        if current_receipt is not None: refuse('E003', 'foreign or changed value in original recording evidence')
                    else:
                        original, path, proof, receipt_path, proof_data = receipt
                        if (type(current_receipt) is not tuple or len(current_receipt) != 2 or current_receipt is not original
                                or type(current_receipt[0]) is not _PATH_TYPE or current_receipt[0] is not path
                                or current_receipt[1] is not proof or frozen_data(copy_data(proof)) != proof_data):
                            refuse('E003', 'foreign or changed value in original recording evidence')
                    children.append({'pid': actual['pid'], 'requested_argv': list(values[0]), 'actual_argv': list(values[1]),
                        'cwd': values[2], 'start_new_session': values[3], 'wait_completed': actual['completed'],
                        'exit_code': actual['code'] if actual['completed'] else None, 'failed_before_return': values[4],
                        'Python_startup_receipt': receipt_path})
                ledger.append({'pid': actual['pid'], 'terminal_observed': actual['terminal'], 'raw_status': actual['status'],
                    'exit_code': actual['code'], 'unknown': actual['unknown'] or not actual['terminal'],
                    'observations': [{'pid': p, 'status': s, 'state': kind, 'exit_code': c, 'error_type': e}
                                     for p, s, kind, c, e in actual['observations']],
                    'claims': [{'returncode': value, 'claim_type': kind} for value, kind in actual['claims']]})
            state_values = dictionary(object.__getattribute__(state, '__dict__'))
            if dict.get(state_values, 'pre_registration_cleanup') is not pre_registration or type(pre_registration) is not list:
                refuse('E003', 'foreign or changed value in original recording evidence')
            pre = []
            for value in list.__iter__(pre_registration):
                dictionary(value, ('branch', 'pid', 'wait_completed', 'exit_code', 'signal_attempted', 'signal_completed', 'streams_closed'))
                branch = scalar(value['branch'], (str,)); pid = scalar(value['pid'], (int,))
                if branch not in ('recorder-callback', 'adoption') or not any(actual['pid'] == pid for actual in snapshots):
                    refuse('E003', 'foreign or changed value in original recording evidence')
                for key in ('wait_completed', 'signal_attempted', 'signal_completed'): scalar(value[key], (bool,))
                scalar(value['exit_code'], (int, type(None))); dictionary(value['streams_closed'])
                captured = next(actual for actual in snapshots if actual['pid'] == pid)
                if (value['wait_completed'] is not captured['completed']
                        or value['exit_code'] != (captured['code'] if captured['completed'] else None)):
                    refuse('E003', 'foreign or changed value in original recording evidence')
                for key, closed in dict.items(value['streams_closed']):
                    if key not in ('stdout', 'stderr', 'stdin'): refuse('E003', 'foreign or changed value in original recording evidence')
                    scalar(closed, (bool,))
                    actual_stream = next((stream for name, stream in captured['streams'] if name == key), None)
                    if actual_stream is None or closed is not (actual_stream.closed is True):
                        refuse('E003', 'foreign or changed value in original recording evidence')
                pre.append(copy_data(value))
            return (evidence_target, {'version': 'som.native-m6-recorded-processes.v1', 'children': children,
                'events': events, 'recording_errors': errors, 'pre_registration_cleanup': pre, 'kernel_waits': copy_data(ledger),
                'stdlib_method_provenance': [{'method': key, 'helper_qualname': helper, 'underlying_qualname': underlying,
                    'sources': [{'path': path, 'sha256': before} for path, before in paths], 'compile_mode': 'exec',
                    'dont_inherit': True, 'optimize': _sys.flags.optimize, 'compiled_code_executed': False}
                    for key, helper, underlying, paths in decorated_data], 'product_acceptance_passed': False})
        # This tuple is a FAST local. No helper closes over it or its mirror.
        # Closure cells alone are not immutable authority on CPython 3.12.
        producer_literal = getframe(0).f_code
        private_helper_seals = ()
        for function in (class_seal, refuse, signature_ok, class_ok, dictionary, scalar,
                         copy_data, frozen_data, argv_data, child_data, decorated_ok, retained_file,
                         command_data, event_data, evidence_data):
            helper_literal = next(code for code in producer_literal.co_consts
                                  if type(code) is _CodeType and code.co_name == function.__name__)
            original_defaults, original_keywords = function.__defaults__, function.__kwdefaults__
            # Only these genuine source literals exist. Do not call a helper
            # or _binding_state to prove its own mutable default binding.
            literal_defaults_owned = (original_keywords is None and
                ((type(original_defaults) is tuple and len(original_defaults) == 1 and original_defaults[0] is None)
                 if helper_literal.co_name in ('dictionary', 'copy_data') else original_defaults is None))
            default_contents = ((original_defaults[0],) if literal_defaults_owned and original_defaults is not None else None)
            keyword_contents = None
            private_helper_seals += ((function, helper_literal, function.__closure__,
                tuple((name, cell, cell.cell_contents)
                      for name, cell in zip(helper_literal.co_freevars, function.__closure__ or ())),
                original_defaults, original_keywords, default_contents, keyword_contents, literal_defaults_owned),)
        metadata_mirror = child_metadata
        captures = ()
        source = globals()
        message = yield None
        while True:
            # Validate literal functions and every closed-over original before
            # using any helper or its captured authority. Never self-seal cells.
            helpers_owned = True
            for (owned_function, owned_code, owned_closure, owned_cells, owned_defaults,
                    owned_keywords, owned_default_contents, owned_keyword_contents, owned_literals_valid) in private_helper_seals:
                if (type(owned_function) is not _FunctionType or owned_function.__code__ is not owned_code
                        or owned_function.__globals__ is not source
                        or owned_function.__builtins__ is not getframe(0).f_builtins
                        or not owned_literals_valid or owned_keywords is not None or owned_keyword_contents is not None
                        or owned_function.__defaults__ is not owned_defaults or owned_function.__kwdefaults__ is not owned_keywords
                        or owned_function.__closure__ is not owned_closure):
                    helpers_owned = False
                    break
                if owned_default_contents is None:
                    if owned_defaults is not None:
                        helpers_owned = False
                        break
                elif (type(owned_defaults) is not tuple or len(owned_defaults) != len(owned_default_contents)
                        or any(actual is not original for actual, original in
                               zip(tuple.__iter__(owned_defaults), tuple.__iter__(owned_default_contents)))):
                    helpers_owned = False
                    break
                for owned_name, owned_cell, owned_value in owned_cells:
                    try:
                        same = (type(owned_cell) is _CellType and owned_cell.cell_contents is
                                (metadata_mirror if owned_name == 'child_metadata' else owned_value))
                    except ValueError:
                        same = False
                    if not same:
                        helpers_owned = False
                        break
            if not helpers_owned:
                message = yield (False, 'refusal', 'P004', 'producer-held operation changed before preflight')
                continue
            try:
                actual_state, action, process, operation, argument = message
                sender = getframe(1)
                request_code = next(code for name, code in source_codes if name == '_capture_request')
                if (sender.f_code is not request_code or sender.f_globals is not source
                        or sender.f_locals.get('state') is not state or actual_state is not state
                        or state is not _ACTIVE or _os.getpid() != owner_pid or _threading.get_ident() != owner_thread):
                    raise guard_error(marker, ('refusal', 'P001', 'actual producer scope and finite request origin required'))
                origin = sender.f_back
                if origin is None or origin.f_globals is not source:
                    raise guard_error(marker, ('refusal', 'P002', 'foreign producer request refused'))
                names = tuple(name for name, code in source_codes if origin.f_code is code)
                if len(names) != 1:
                    raise guard_error(marker, ('refusal', 'P003', 'foreign producer request CodeType refused'))
                name = names[0]
                if action in ('verify', 'children', 'close-python', 'close-source', 'evidence'):
                    if type(refs) is not dict:
                        raise guard_error(marker, ('refusal', 'P001', 'actual producer scope and finite request origin required'))
                    current_pid, current_thread = dict.get(refs, 'pid'), dict.get(refs, 'thread')
                    if (type(current_pid) is not int or type(current_thread) is not int
                            or current_pid != owner_pid or current_thread != owner_thread):
                        raise guard_error(marker, ('refusal', 'P001', 'actual producer scope and finite request origin required'))
                # Function checks always run. Guard checks can fail while read-only
                # snapshots and exact hook restore still unwind the actual owned scope.
                active_functions = (tuple(item for item in functions if item[0] is not None)
                                    if action in ('close-python', 'close-source') else functions)
                for key, function, signature in active_functions:
                    if (function.__code__ is not signature[0] or function.__globals__ is not signature[1]
                            or function.__defaults__ is not signature[2] or function.__kwdefaults__ is not signature[3]
                            or function.__closure__ is not signature[4] or function.__builtins__ is not signature[5]
                            or key is not None and dict.get(source, key) is not function):
                        raise guard_error(marker, ('refusal', 'P004', 'producer-held operation changed before preflight'))
                for cell, underlying, wrapper in (() if action in ('close-python', 'close-source') else decorated_bindings):
                    if (cell.cell_contents is not underlying
                            or dict.get(wrapper.__dict__, '__wrapped__') is not underlying):
                        raise guard_error(marker, ('refusal', 'P005', 'retained decorated process closure changed'))
                refusal = preflight(active_functions, () if action in ('rows', 'children', 'kernel', 'release',
                    'close-python', 'close-source', 'evidence', 'evidence-child') else classes)
                if refusal is not None:
                    raise guard_error(marker, ('refusal', *refusal))
                if action in ('close-python', 'close-source'):
                    if origin.f_code is not close_code or origin.f_globals is not source:
                        refuse('L001', 'unlisted original borrow close preparation caller')
                    if action == 'close-python':
                        if not signature_ok(python_close, python_signature):
                            refuse('L002', 'borrowed Python close invocation changed')
                        if (not all(class_ok(seal) for seal in python_seals)
                                or type(python_lease) is not python_type or type(python_owner) is not control_type
                                or object.__getattribute__(python_lease, '__dict__') is not python_values
                                or object.__getattribute__(python_owner, '__dict__') is not owner_values):
                            refuse('L002', 'original borrowed Python close authority changed')
                        dictionary(python_values); dictionary(owner_values)
                        if (dict.get(python_values, 'controls') is not python_owner
                                or dict.get(python_values, 'owns_controls') is not False
                                or dict.get(python_values, 'closed') is not False
                                or any(key in python_values for key in ('verify_installed', 'close'))
                                or dict.get(owner_values, 'closed') is not False
                                or any(key in owner_values for key in ('verify_installed', 'close'))
                                or dict.get(owner_values, 'ctx') is not ctx or dict.get(owner_values, 'wrapper') is not guard
                                or dict.get(context_namespace, 'require') is not context_require
                                or not signature_ok(context_require, require_signature)
                                or dict.get(context_namespace, 'subprocess') is not subprocess_module
                                or dict.get(_ModuleType.__getattribute__(subprocess_module, '__dict__'), 'Popen') is not guard):
                            refuse('L002', 'original borrowed Python close authority changed')
                        result = _MethodType(python_close, python_lease)
                    else:
                        if (not signature_ok(source_close, source_signature) or not class_ok(source_seal)
                                or type(source_lease) is not source_type):
                            refuse('L003', 'original borrowed source close authority changed')
                        namespace = type.__getattribute__(source_type, '__dict__')
                        if any(namespace.get(key) is not descriptor for key, descriptor in source_slots):
                            refuse('L003', 'original borrowed source close authority changed')
                        if (object.__getattribute__(source_lease, '_domain') is not source_domain
                                or object.__getattribute__(source_lease, '_owns') is not False
                                or object.__getattribute__(source_lease, '_closed') is not False):
                            refuse('L003', 'original borrowed source close authority changed')
                        result = _MethodType(source_close, source_lease)
                elif action in ('evidence', 'evidence-child'):
                    required = close_code if action == 'evidence' else adopt_code
                    if origin.f_code is not required or origin.f_globals is not source:
                        if action == 'evidence': refuse('E001', 'unlisted original recording evidence caller')
                        refuse('E004', 'unlisted original child evidence completion caller')
                    snapshots = ()
                    for unused, actual_process, channel in captures:
                        if (type(channel) is not generator_type or channel.gi_code is not _kernel_code
                                or channel.gi_frame is None or channel.gi_frame.f_globals is not source
                                or channel.gi_frame.f_builtins is not getframe(0).f_builtins
                                or channel.gi_frame.f_locals.get('producer') is not getframe(0)):
                            refuse('P016', 'actual producer-owned kernel channel required before send')
                        reply = send(channel, ('snapshot', getframe(0), None))
                        if not reply[0]: raise guard_error(marker, ('forward', reply))
                        snapshots += (reply[1],)
                    if action == 'evidence':
                        result = evidence_data(snapshots)
                    else:
                        row = argument
                        matches = tuple(actual for actual in snapshots if actual['process'] is process and actual['child'] is row)
                        expected = tuple(item for item in child_metadata if item[0] is row)
                        if (len(matches) != 1 or len(expected) != 1 or origin.f_locals.get('process') is not process
                                or origin.f_locals.get('row') is not row or child_data(row) != expected[0][1]):
                            refuse('E004', 'unlisted original child evidence completion caller')
                        values = dictionary(object.__getattribute__(row, '__dict__'))
                        receipt = dict.get(values, 'startup_receipt')
                        if receipt is not None:
                            if (type(receipt) is not tuple or len(receipt) != 2 or type(receipt[0]) is not _PATH_TYPE
                                    or receipt[0] is not origin.f_locals.get('path') or receipt[1] is not origin.f_locals.get('proof')
                                    or type(origin.f_locals.get('evidence_path')) is not str):
                                refuse('E004', 'unlisted original child evidence completion caller')
                            receipt = (receipt, receipt[0], receipt[1], origin.f_locals['evidence_path'], frozen_data(copy_data(receipt[1])))
                        child_metadata = tuple((item[0], item[1], receipt) if item[0] is row else item for item in child_metadata)
                        metadata_mirror = child_metadata
                        result = None
                elif action == 'verify':
                    decorated_ok()
                    if name not in ('_verify', '_kernel_dependencies'):
                        raise guard_error(marker, ('refusal', 'P006', 'unlisted producer verify caller'))
                    refusal = preflight(functions, classes)
                    if refusal is not None:
                        raise guard_error(marker, ('refusal', *refusal))
                    if (object.__getattribute__(ctx, '__dict__') is not context_fields
                            or context_namespace.get('require') is not context_require
                            or 'python_argv' in context_fields or 'verify_installed' in python_fields
                            or 'close' in python_fields or 'verify_installed' in lease_fields or 'close' in lease_fields):
                        raise guard_error(marker, ('refusal', 'P007', 'actual Context/Python method shadow refused before source verification'))
                    same = (_os.waitpid is primitive_wait and _posix.waitpid is primitive_wait
                            and _os.waitstatus_to_exitcode is primitive_decode and _posix.waitstatus_to_exitcode is primitive_decode
                            and dict.get(refs, 'waitpid') is primitive_wait and dict.get(refs, 'wait_decoder') is primitive_decode
                            and object.__getattribute__(state, '__dict__').get('refs') is refs)
                    if not same and not argument:
                        raise guard_error(marker, ('refusal', 'P008', 'global or evidence kernel primitive alias changed'))
                    result = same
                elif action == 'recorder':
                    if name != '_recorder_methods':
                        raise guard_error(marker, ('refusal', 'P009', 'unlisted original Recorder provenance caller'))
                    for method, function, signature in recorder_methods:
                        if type.__getattribute__(recorder_class, '__dict__').get(method) is not function:
                            raise guard_error(marker, ('refusal', 'P010', 'original Recorder invocation changed'))
                    refusal = preflight(tuple((None, function, signature) for method, function, signature in recorder_methods), ())
                    if refusal is not None:
                        raise guard_error(marker, ('refusal', *refusal))
                    result = (recorder_class, recorder, recorder_methods, recorder_codes)
                elif action == 'base-wait':
                    if name != '_wait_provenance':
                        raise guard_error(marker, ('refusal', 'P011', 'unlisted original wait provenance caller'))
                    result = next(function for method, function in methods if method == 'wait')
                elif action in ('rows', 'children'):
                    if name not in ('_close', '_restore_kernel_hooks', '_wait_owner'):
                        raise guard_error(marker, ('refusal', 'P012', 'unlisted producer enumeration caller'))
                    if action == 'rows':
                        result = tuple(item[0] for item in captures)
                    else:
                        result = ()
                        for unused, child_process, channel in captures:
                            if (type(channel) is not generator_type or channel.gi_code is not _kernel_code
                                    or channel.gi_frame is None or channel.gi_frame.f_globals is not source
                                    or channel.gi_frame.f_builtins is not getframe(0).f_builtins
                                    or channel.gi_frame.f_locals.get('producer') is not getframe(0)):
                                raise guard_error(marker, ('refusal', 'P016', 'actual producer-owned kernel channel required before send'))
                            answer = send(channel, ('snapshot', getframe(0), None))
                            if not answer[0]:
                                raise guard_error(marker, ('forward', answer))
                            if answer[1]['child'] is not None:
                                result += (answer[1]['child'],)
                elif action == 'create':
                    if name != '_capture_wait_owner':
                        raise guard_error(marker, ('refusal', 'P013', 'unlisted kernel creation caller'))
                    parent = origin.f_back
                    if (parent is None or parent.f_globals is not source
                            or not any(parent.f_code is code for key, code in source_codes
                                       if key in ('_adopt', '_cleanup_unregistered'))
                            or parent.f_locals.get('process') is not process):
                        raise guard_error(marker, ('refusal', 'P014', 'actual creation recovery frame required'))
                    producer = parent.f_back
                    popen_code = next(code for key, code in source_codes if key == 'Popen')
                    if (producer is None or producer.f_code is not popen_code or producer.f_globals is not source
                            or not (producer.f_locals.get('process') is process or producer.f_locals.get('child') is process)):
                        raise guard_error(marker, ('refusal', 'P015', 'actual newly-created Recorder child required'))
                    existing = tuple(item for item in captures if item[1] is process)
                    if existing:
                        result = existing[0][0]
                    else:
                        refusal = preflight(functions, classes)
                        if refusal is not None:
                            raise guard_error(marker, ('refusal', *refusal))
                        if type(process) is not guard:
                            raise guard_error(marker, ('refusal', 'P017', 'actual created guard child required'))
                        values = object.__getattribute__(process, '__dict__')
                        pid, lock = dict.get(values, 'pid'), dict.get(values, '_waitpid_lock')
                        if (type(pid) is not int or dict.get(values, '_child_created') is not True
                                or type(lock) is not lock_type
                                or '_internal_poll' in values or '_try_wait' in values):
                            raise guard_error(marker, ('refusal', 'P018', 'actual original child/PID/native lock required before return'))
                        row = row_type(process, pid, values, lock)
                        channel = _kernel_capture(state, process, pid, lock, row, getframe(0),
                            primitive_config, functions, classes, methods, source_codes, guard_error, marker)
                        advance(channel)
                        captures += ((row, process, channel),)
                        if type(state.kernel_waits) is not list:
                            raise guard_error(marker, ('refusal', 'P019', 'plain public kernel evidence projection required'))
                        list.append(state.kernel_waits, row)
                        result = row
                elif action == 'kernel':
                    if name != '_kernel_request':
                        raise guard_error(marker, ('refusal', 'P020', 'unlisted kernel channel caller'))
                    origin = origin.f_back
                    if origin is None or origin.f_globals is not source:
                        raise guard_error(marker, ('refusal', 'P021', 'foreign kernel origin'))
                    matches = ()
                    for actual_row, actual_process, channel in captures:
                        if (type(channel) is not generator_type or channel.gi_code is not _kernel_code
                                or channel.gi_frame is None or channel.gi_frame.f_globals is not source
                                or channel.gi_frame.f_builtins is not getframe(0).f_builtins
                                or channel.gi_frame.f_locals.get('producer') is not getframe(0)):
                            raise guard_error(marker, ('refusal', 'P016', 'actual producer-owned kernel channel required before send'))
                        answer = send(channel, ('snapshot', getframe(0), None))
                        if not answer[0]:
                            raise guard_error(marker, ('forward', answer))
                        if actual_process is process or actual_row is process or answer[1]['child'] is process:
                            matches += ((actual_row, actual_process, channel),)
                    if len(matches) != 1:
                        raise guard_error(marker, ('refusal', 'P022', 'original producer process association required'))
                    channel = matches[0][2]
                    if (type(channel) is not generator_type or channel.gi_code is not _kernel_code
                            or channel.gi_frame is None or channel.gi_frame.f_globals is not source
                            or channel.gi_frame.f_builtins is not getframe(0).f_builtins
                            or channel.gi_frame.f_locals.get('producer') is not getframe(0)):
                        raise guard_error(marker, ('refusal', 'P016', 'actual producer-owned kernel channel required before send'))
                    answer = send(channel, (operation, origin, argument))
                    if not answer[0]:
                        raise guard_error(marker, ('forward', answer))
                    result = answer[1]
                    if operation == 'associate':
                        if origin.f_code is not adopt_code or origin.f_globals is not source:
                            refuse('E004', 'unlisted original child evidence completion caller')
                        child_metadata += ((argument, child_data(argument), None),)
                        metadata_mirror = child_metadata
                elif action == 'release':
                    if name != '_close':
                        raise guard_error(marker, ('refusal', 'P024', 'unlisted producer release caller'))
                    release_failures = ()
                    for row, process, channel in captures:
                        owned_channel = False
                        try:
                            if (type(channel) is not generator_type or channel.gi_code is not _kernel_code
                                    or channel.gi_frame is None or channel.gi_frame.f_globals is not source
                                    or channel.gi_frame.f_builtins is not getframe(0).f_builtins
                                    or channel.gi_frame.f_locals.get('producer') is not getframe(0)):
                                raise guard_error(marker, ('refusal', 'P016', 'actual producer-owned kernel channel required before send'))
                            owned_channel = True
                            reply = send(channel, ('release', origin, None))
                            if not reply[0]:
                                release_failures += (reply,)
                        except BaseException as error:
                            args = BaseException.__getattribute__(error, 'args')
                            if (type(error) is guard_error and type(args) is tuple and len(args) == 2
                                    and args[0] is marker and type(args[1]) is tuple
                                    and len(args[1]) == 3 and args[1][0] == 'refusal'):
                                release_failures += ((False, 'refusal', args[1][1], args[1][2]),)
                            else:
                                release_failures += ((False, 'error', error,
                                    BaseException.__getattribute__(error, '__traceback__')),)
                        finally:
                            if owned_channel:
                                try:
                                    close_channel(channel)
                                except BaseException as error:
                                    release_failures += ((False, 'error', error,
                                        BaseException.__getattribute__(error, '__traceback__')),)
                    captures = ()
                    result = release_failures
                else:
                    raise guard_error(marker, ('refusal', 'P023', 'unlisted finite producer operation'))
                answer = (True, result)
            except BaseException as error:
                args = BaseException.__getattribute__(error, 'args')
                if (type(error) is guard_error and type(args) is tuple and len(args) == 2
                        and args[0] is marker and type(args[1]) is tuple):
                    signal = args[1]
                    if len(signal) == 3 and signal[0] == 'refusal':
                        answer = (False, 'refusal', signal[1], signal[2])
                    elif len(signal) == 2 and signal[0] == 'forward':
                        answer = signal[1]
                    else:
                        answer = (False, 'error', error, BaseException.__getattribute__(error, '__traceback__'))
                else:
                    answer = (False, 'error', error, BaseException.__getattribute__(error, '__traceback__'))
            message = yield answer

    def _kernel_capture(state, process, pid, lock, row, producer, primitives, functions, classes, methods, codes, guard_error, marker):
        """Only this suspended producer frame holds actual kernel authority."""
        if (_sys._getframe(1) is not producer or producer.f_code is not _producer_code
                or producer.f_globals is not globals()):
            raise ValueError('actual kernel producer frame required')
        waitpid, decode, kill, kill_signal, lock_type, lock_pairs, posix, os_module = primitives
        acquire = next(value for name, value in lock_pairs if name == 'acquire')
        release = next(value for name, value in lock_pairs if name == 'release')
        locked = next(value for name, value in lock_pairs if name == 'locked')
        getframe = _sys._getframe
        guard = type(process)
        values = object.__getattribute__(process, '__dict__')
        owner_pid, owner_thread = _os.getpid(), _threading.get_ident()
        streams = tuple((name, dict.get(values, name)) for name in ('stdout', 'stderr', 'stdin')
                        if dict.get(values, name) is None or any(type(dict.get(values, name)) is kind
                            for kind in (_io.TextIOWrapper, _io.BufferedReader, _io.BufferedWriter, _io.FileIO)))
        foreign_streams = tuple(name for name in ('stdout', 'stderr', 'stdin')
                                if not any(name == known for known, unused in streams))
        original_private = tuple((name, name in values, dict.get(values, name))
                                 for name in ('_internal_poll', '_try_wait'))
        hooks = (('_internal_poll', _MethodType(_kernel_poll, process)),
                 ('_try_wait', _MethodType(_kernel_try_wait, process)))
        observations = claims = ()
        terminal = unknown = live_observed = installed = completed = False
        status = code = child = cleanup_primary = None
        message = yield None
        while True:
            try:
                operation, origin, argument = message
                if (getframe(1) is not producer or _os.getpid() != owner_pid or _threading.get_ident() != owner_thread):
                    raise guard_error(marker, ('refusal', 'K001', 'original kernel producer/thread required'))
                origin_names = tuple(name for name, expected in codes
                                     if origin.f_code is expected and origin.f_globals is globals())
                if len(origin_names) != 1:
                    raise guard_error(marker, ('refusal', 'K002', 'actual finite kernel request source required'))
                name = origin_names[0]
                allowed = {
                    'install': ('_capture_wait_owner',), 'snapshot': ('_wait_owner', '_kernel_ledger', '_close', '_producer_capture',
                        '_owned_process_operations', '_cleanup_unregistered', '_stop_and_wait', '_capture_wait_owner', '_adopt', '_child', '_observed_wait', '_bounded_run'),
                    'associate': ('_adopt',), 'poll': ('_kernel_poll',), 'try-wait': ('_kernel_try_wait',),
                    'proof': ('_wait_proof',), 'complete': ('_observed_wait', '_cleanup_unregistered', '_stop_and_wait', 'Popen'),
                    'begin-cleanup': ('_cleanup_unregistered', '_stop_and_wait'),
                    'end-cleanup': ('_cleanup_unregistered', '_stop_and_wait'),
                    'operations': ('_owned_process_operations',), 'kill': ('_retained_kill',),
                    'restore': ('_restore_kernel_hooks',), 'release': ('_close',)}
                if operation not in allowed or name not in allowed[operation]:
                    raise guard_error(marker, ('refusal', 'K003', 'unlisted finite kernel operation/caller'))
                refusal = _capture_preflight(functions, () if operation in ('snapshot', 'restore', 'release', 'begin-cleanup', 'end-cleanup') else classes)
                if refusal is not None:
                    raise guard_error(marker, ('refusal', *refusal))
                # Identity is held here. Public refs are never a dispatch source.
                for function, expected in ((waitpid, 'waitpid'), (decode, 'waitstatus_to_exitcode'), (kill, 'kill')):
                    if (type(function) is not _BuiltinFunctionType or function.__self__ is not posix
                            or function.__module__ != 'posix' or function.__name__ != expected):
                        raise guard_error(marker, ('refusal', 'K004', 'original actual POSIX capture primitive changed'))
                if operation not in ('snapshot', 'restore', 'release', 'begin-cleanup', 'end-cleanup'):
                    public_pid = dict.get(values, 'pid')
                    if (type(process) is not guard or object.__getattribute__(process, '__dict__') is not values
                            or type(public_pid) is not int
                            or public_pid != pid or dict.get(values, '_child_created') is not True
                            or dict.get(values, '_waitpid_lock') is not lock or type(lock) is not lock_type):
                        raise guard_error(marker, ('refusal', 'K005', 'actual captured child/PID/native lock changed'))
                    for method, expected in lock_pairs:
                        if type.__getattribute__(lock_type, '__dict__').get(method) is not expected:
                            raise guard_error(marker, ('refusal', 'K006', 'actual captured native wait lock method changed'))
                if operation == 'install':
                    if not installed:
                        if any(present for unused, present, value in original_private):
                            raise guard_error(marker, ('refusal', 'K007', 'foreign initial private kernel hook refused'))
                        for key, hook in hooks:
                            dict.__setitem__(values, key, hook)
                        installed = True
                    result = row
                elif operation == 'associate':
                    if child is not None or origin.f_locals.get('row') is not argument:
                        raise guard_error(marker, ('refusal', 'K008', 'one actual source-created child record required'))
                    child = argument
                    result = None
                elif operation in ('begin-cleanup', 'end-cleanup'):
                    result = cleanup_primary
                    cleanup_primary = argument
                elif operation in ('poll', 'try-wait'):
                    if unknown:
                        raise guard_error(marker, ('refusal', 'K009', 'kernel child status remains unknown'))
                    same = (_posix.waitpid is waitpid and _os.waitpid is waitpid
                            and _posix.waitstatus_to_exitcode is decode and _os.waitstatus_to_exitcode is decode)
                    if not same:
                        error = ValueError('global wait primitive changed; captured actual kernel primitive used')
                        if cleanup_primary is None:
                            raise guard_error(marker, ('refusal', 'K020', 'global wait primitive changed; captured actual kernel primitive used'))
                        _secondary(cleanup_primary, 'recorded-cleanup-kernel-primitive-owner', error)
                    claim = dict.get(values, 'returncode')
                    false_claim = claim is not None and not (terminal and type(claim) is int and claim == code)
                    if false_claim:
                        claims += ((claim if type(claim) is int else None, type.__getattribute__(type(claim), '__name__')),)
                    taken = operation == 'poll'
                    if taken:
                        if not acquire(lock, False):
                            raise guard_error(marker, ('refusal', 'K010', 'owned native wait lock is busy; status unknown'))
                    else:
                        parent = origin.f_back
                        wait_code = next(function.__code__ for key, function in methods if key == '_wait')
                        if parent is None or parent.f_code is not wait_code or parent.f_globals is not _module(_subprocess) or not locked(lock):
                            raise guard_error(marker, ('refusal', 'K011', 'actual stdlib wait must own retained native lock'))
                    try:
                        if not terminal:
                            flags = 1 if taken else argument
                            if type(flags) is not int or flags not in (0, 1):
                                raise guard_error(marker, ('refusal', 'K013', 'finite actual kernel wait flags required'))
                            try:
                                found, raw = waitpid(pid, flags)
                                if type(found) is not int or type(raw) is not int or found not in (0, pid):
                                    raise guard_error(marker, ('refusal', 'K015', 'actual original PID/raw kernel result required'))
                                if found == pid:
                                    actual_code = decode(raw)
                                    if type(actual_code) is not int:
                                        raise guard_error(marker, ('refusal', 'K019', 'actual decoded terminal kernel status required'))
                                    terminal, status, code, live_observed = True, raw, actual_code, False
                                    dict.__setitem__(values, 'returncode', code)
                                    observations += ((pid, raw, 'terminal', code, None),)
                                else:
                                    live_observed = True
                                    observations += ((0, raw, 'running', None, None),)
                            except BaseException as error:
                                unknown, live_observed = True, False
                                observations += ((None, None, 'unknown', None, type.__getattribute__(type(error), '__name__')),)
                                raise
                        if false_claim:
                            error = ValueError('public returncode has no matching actual kernel wait proof')
                            if cleanup_primary is None:
                                raise guard_error(marker, ('refusal', 'K021', 'public returncode has no matching actual kernel wait proof'))
                            _secondary(cleanup_primary, 'recorded-cleanup-returncode-claim', error)
                            dict.__setitem__(values, 'returncode', code if terminal else None)
                        result = code if taken else (pid, status) if terminal else (0, 0)
                    finally:
                        if taken:
                            release(lock)
                elif operation in ('proof', 'complete'):
                    if not terminal or unknown or type(argument) is not int or argument != code or dict.get(values, 'returncode') != code:
                        raise guard_error(marker, ('refusal', 'K012', 'terminal wait requires an actual producer-held kernel observation'))
                    if operation == 'complete':
                        if origin.f_locals.get('code') is not argument:
                            raise guard_error(marker, ('refusal', 'K014', 'actual original wait return required'))
                        completed = True
                    result = None
                elif operation == 'operations':
                    for key, hook in hooks:
                        if installed and dict.get(values, key) is not hook:
                            raise guard_error(marker, ('refusal', 'K016', 'owned kernel process hook changed'))
                    for key, function in methods:
                        if key.startswith('_') and key not in ('_internal_poll', '_try_wait') and key in values:
                            raise guard_error(marker, ('refusal', 'K017', 'private process operation shadow refused during cleanup'))
                    result = tuple(_MethodType(function, process) for key in ('poll', 'wait')
                                   for method, function in methods if method == key)
                elif operation == 'kill':
                    if not (terminal or live_observed and not unknown):
                        raise guard_error(marker, ('refusal', 'K018', 'actual live or terminal observation required before cleanup signal'))
                    if terminal:
                        result = None
                    else:
                        if _os.kill is not kill or _posix.kill is not kill or _signal.SIGKILL is not kill_signal:
                            _secondary(argument, 'recorded-cleanup-primitive-owner', ValueError('global kill primitive or SIGKILL changed; captured primitive used'))
                        result = kill(pid, 9)
                elif operation == 'restore':
                    failures = argument
                    for key, hook in hooks:
                        try:
                            if installed:
                                if dict.get(values, key) is not hook:
                                    raise ValueError('kernel hook replaced by later owner')
                                original = next((present, value) for name0, present, value in original_private if name0 == key)
                                if original[0]:
                                    dict.__setitem__(values, key, original[1])
                                else:
                                    dict.__delitem__(values, key)
                        except BaseException as error:
                            failures.append(('recording-kernel-hook-restore', error, BaseException.__getattribute__(error, '__traceback__')))
                    installed = False
                    result = None
                elif operation == 'release':
                    result = None
                else:
                    result = {'row': row, 'process': process, 'pid': pid, 'values': values, 'lock': lock,
                        'streams': streams, 'foreign_streams': foreign_streams, 'hooks': hooks,
                        'terminal': terminal, 'unknown': unknown, 'live_observed': live_observed,
                        'status': status, 'code': code, 'completed': completed, 'child': child,
                        'observations': tuple(observations), 'claims': tuple(claims), 'installed': installed}
                # Public rows are a disposable evidence projection, never authority.
                if type(row) is __som_native_KernelWaitState__:
                    projection = object.__getattribute__(row, '__dict__')
                    dict.update(projection, terminal=terminal, unknown=unknown, live_observed=live_observed,
                        status=status, code=code, installed=installed, streams=list(streams), foreign_streams=list(foreign_streams),
                        hooks=dict(hooks), original_private={key:(present,value) for key,present,value in original_private},
                        observations=[{'pid':p,'status':s,'state':kind,'exit_code':c,'error_type':e}
                                      for p,s,kind,c,e in observations],
                        claims=[{'returncode':value,'claim_type':kind} for value,kind in claims])
                answer = (True, result)
            except BaseException as error:
                args = BaseException.__getattribute__(error, 'args')
                if (type(error) is guard_error and type(args) is tuple and len(args) == 2
                        and args[0] is marker and type(args[1]) is tuple):
                    signal = args[1]
                    if len(signal) == 3 and signal[0] == 'refusal':
                        answer = (False, 'refusal', signal[1], signal[2])
                    elif len(signal) == 2 and signal[0] == 'forward':
                        answer = signal[1]
                    else:
                        answer = (False, 'error', error, BaseException.__getattribute__(error, '__traceback__'))
                else:
                    answer = (False, 'error', error, BaseException.__getattribute__(error, '__traceback__'))
            message = yield answer

    request_frame = _sys._getframe(0)
    _request_code = request_frame.f_code
    source = request_frame.f_globals
    code_type = type(_request_code)
    def literal(name):
        matches = tuple(value for value in _request_code.co_consts
                        if type(value) is code_type and value.co_name == name)
        if len(matches) != 1:
            raise ValueError('one immutable source CodeType literal required')
        return matches[0]
    _acquire_code = literal('_acquire')
    _producer_code = literal('_producer_capture')
    _kernel_code = literal('_kernel_capture')
    if action == 'open':
        return _acquire(state, process, operation)
    if operation is not None and type(operation) is not str:
        raise ValueError('exact finite kernel operation required')
    capture = _CAPTURE
    generator_type = type(item for item in ())
    if (type(capture) is not generator_type or capture.gi_code is not _producer_code
            or capture.gi_frame is None or capture.gi_frame.f_globals is not source
            or capture.gi_frame.f_builtins is not request_frame.f_builtins):
        raise ValueError('actual immutable producer source required before send')
    fields = capture.gi_frame.f_locals
    guard_error, marker = fields.get('guard_error'), fields.get('marker')
    if (guard_error is not request_frame.f_builtins.get('ValueError') or type(guard_error) is not type
            or type.__getattribute__(guard_error, '__module__') != 'builtins'
            or type.__getattribute__(guard_error, '__name__') != 'ValueError'
            or type.__getattribute__(guard_error, '__flags__') & (1 << 9)
            or type(marker) is not object):
        raise ValueError('actual native refusal signal and private producer marker required')
    expected = {'_acquire_code': _acquire_code, '_request_code': _request_code,
                '_producer_code': _producer_code, '_kernel_code': _kernel_code}
    for name in _producer_code.co_freevars:
        if name != '_kernel_capture' and (name not in expected or fields.get(name) is not expected[name]):
            raise ValueError('producer literal source binding changed')
    kernel_function = fields.get('_kernel_capture')
    if (type(kernel_function) is not _FunctionType or kernel_function.__code__ is not _kernel_code
            or kernel_function.__globals__ is not source or kernel_function.__builtins__ is not request_frame.f_builtins
            or kernel_function.__defaults__ is not None or kernel_function.__kwdefaults__ is not None):
        raise ValueError('actual nested kernel source function required')
    cells = kernel_function.__closure__ or ()
    if len(cells) != len(_kernel_code.co_freevars):
        raise ValueError('actual nested kernel source bindings required')
    for name, cell in zip(_kernel_code.co_freevars, cells):
        if name not in expected or type(cell) is not _CellType or cell.cell_contents is not expected[name]:
            raise ValueError('nested kernel literal binding changed')
    acquisition, factory = fields.get('acquisition'), fields.get('factory')
    frame_type = type(request_frame)
    if (type(acquisition) is not frame_type or acquisition.f_code is not _acquire_code
            or acquisition.f_globals is not source or acquisition.f_locals.get('state') is not state
            or type(factory) is not frame_type or acquisition.f_back is not factory
            or factory.f_code is not _request_code or factory.f_globals is not source):
        raise ValueError('actual producer acquisition/frame association required')
    send = type.__getattribute__(generator_type, '__dict__')['send']
    try:
        answer = send(capture, (state, action, process, operation, argument))
    finally:
        if action == 'release':
            # The native close is safe only after the literal code and actual
            # acquisition/frame checks above. Never close a foreign locator.
            _GeneratorType.close(capture)
            if _CAPTURE is capture:
                _CAPTURE = None
    if type(answer) is not tuple or not answer or type(answer[0]) is not bool:
        raise ValueError('actual finite producer response required')
    def failure(reply):
        if type(reply) is not tuple or len(reply) != 4 or reply[0] is not False:
            raise ValueError('actual finite producer error response required')
        if reply[1] == 'refusal':
            catalog = (
                ('PF001', 'producer-held source invocation changed before source verification'),
                ('PF002', 'producer-held facade function changed before source verification'),
                ('PF003', 'producer-held invocation bindings changed before source verification'),
                ('PF004', 'actual guarded process class changed before source verification'),
                ('P001', 'actual producer scope and finite request origin required'),
                ('P002', 'foreign producer request refused'),
                ('P003', 'foreign producer request CodeType refused'),
                ('P004', 'producer-held operation changed before preflight'),
                ('P005', 'retained decorated process closure changed'),
                ('P006', 'unlisted producer verify caller'),
                ('P007', 'actual Context/Python method shadow refused before source verification'),
                ('P008', 'global or evidence kernel primitive alias changed'),
                ('P009', 'unlisted original Recorder provenance caller'),
                ('P010', 'original Recorder invocation changed'),
                ('P011', 'unlisted original wait provenance caller'),
                ('P012', 'unlisted producer enumeration caller'),
                ('P013', 'unlisted kernel creation caller'),
                ('P014', 'actual creation recovery frame required'),
                ('P015', 'actual newly-created Recorder child required'),
                ('P016', 'actual producer-owned kernel channel required before send'),
                ('P017', 'actual created guard child required'),
                ('P018', 'actual original child/PID/native lock required before return'),
                ('P019', 'plain public kernel evidence projection required'),
                ('P020', 'unlisted kernel channel caller'),
                ('P021', 'foreign kernel origin'),
                ('P022', 'original producer process association required'),
                ('P023', 'unlisted finite producer operation'),
                ('P024', 'unlisted producer release caller'),
                ('L001', 'unlisted original borrow close preparation caller'),
                ('L002', 'original borrowed Python close authority changed'),
                ('L002', 'borrowed Python close invocation changed'),
                ('L003', 'original borrowed source close authority changed'),
                ('E001', 'unlisted original recording evidence caller'),
                ('E002', 'original recording evidence owner or output changed'),
                ('E003', 'foreign or changed value in original recording evidence'),
                ('E004', 'unlisted original child evidence completion caller'),
                ('K001', 'original kernel producer/thread required'),
                ('K002', 'actual finite kernel request source required'),
                ('K003', 'unlisted finite kernel operation/caller'),
                ('K004', 'original actual POSIX capture primitive changed'),
                ('K005', 'actual captured child/PID/native lock changed'),
                ('K006', 'actual captured native wait lock method changed'),
                ('K007', 'foreign initial private kernel hook refused'),
                ('K008', 'one actual source-created child record required'),
                ('K009', 'kernel child status remains unknown'),
                ('K010', 'owned native wait lock is busy; status unknown'),
                ('K011', 'actual stdlib wait must own retained native lock'),
                ('K012', 'terminal wait requires an actual producer-held kernel observation'),
                ('K013', 'finite actual kernel wait flags required'),
                ('K014', 'actual original wait return required'),
                ('K015', 'actual original PID/raw kernel result required'),
                ('K016', 'owned kernel process hook changed'),
                ('K017', 'private process operation shadow refused during cleanup'),
                ('K018', 'actual live or terminal observation required before cleanup signal'),
                ('K019', 'actual decoded terminal kernel status required'),
                ('K020', 'global wait primitive changed; captured actual kernel primitive used'),
                ('K021', 'public returncode has no matching actual kernel wait proof'),
            )
            if (type(reply[2]) is not str or type(reply[3]) is not str
                    or not any(reply[2] == tag and reply[3] == reason for tag, reason in catalog)):
                raise ValueError('unlisted producer refusal result')
            return ValueError(reply[3])
        if reply[1] != 'error':
            raise ValueError('actual finite producer error response required')
        error, trace = reply[2], reply[3]
        if BaseException.__getattribute__(error, '__traceback__') is not trace:
            raise ValueError('original producer error traceback changed')
        return error
    if not answer[0]:
        # Plain raise keeps a genuine error object and its actual traceback.
        # A finite source guard becomes a real ValueError at this boundary.
        raise failure(answer)
    if action == 'release' and answer[1]:
        first = failure(answer[1][0])
        for reply in answer[1][1:]:
            _secondary(first, 'recording-kernel-release', failure(reply))
        raise first
    return answer[1]






def _kernel_request(state, process, operation, argument=None):
    return _capture_request(state, 'kernel', process, operation, argument)


def _kernel_dependencies(state, cleanup=False):
    return _capture_request(state, 'verify', argument=cleanup)


def _wait_owner(state, process):
    rows = _capture_request(state, 'rows')
    matches = [row for row in rows if _kernel_request(state, row, 'snapshot')['process'] is process]
    _need(len(matches) == 1, 'actual producer-held kernel owner required')
    return matches[0]


def _capture_wait_owner(state, process):
    row = _capture_request(state, 'create', process)
    _kernel_request(state, process, 'install')
    return row






def _kernel_poll(process, _deadstate=None):
    _need(_deadstate is None or type(_deadstate) is int, 'plain stdlib poll deadstate required')
    return _kernel_request(_ACTIVE, process, 'poll')


def _kernel_try_wait(process, flags):
    return _kernel_request(_ACTIVE, process, 'try-wait', flags)


def _wait_proof(state, row, code):
    _kernel_request(state, row, 'proof', code)


def _restore_kernel_hooks(state, failures):
    for row in _capture_request(state, 'rows'):
        try:
            _kernel_request(state, row, 'restore', failures)
        except BaseException as error:
            failures.append(('recording-kernel-hook-restore', error,
                             BaseException.__getattribute__(error, '__traceback__')))


def _kernel_ledger(row):
    actual = _kernel_request(_ACTIVE, row, 'snapshot')
    return {'pid': actual['pid'], 'terminal_observed': actual['terminal'], 'raw_status': actual['status'],
            'exit_code': actual['code'], 'unknown': actual['unknown'] or not actual['terminal'],
            'observations': [{'pid': p, 'status': s, 'state': kind, 'exit_code': c, 'error_type': e}
                             for p, s, kind, c, e in actual['observations']],
            'claims': [{'returncode': value, 'claim_type': kind} for value, kind in actual['claims']]}


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
    recorder_class, recorder, methods, original_codes = _recorder_methods(state)
    _need(refs.get('recorder') is recorder and refs.get('recorder_class') is recorder_class
          and type(recorder) is __som_native_Recorder__, 'actual Recorder instance required')
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
    """Fixed acquisition; no callback/channel/code input is accepted."""
    return _capture_request(ctx, 'open', out, qa)


def _recording(token):
    """Return only this live scope's actual completed ordinary Recorder."""
    state = _verify(token)
    _need(not state.closing, 'recording scope is closing')
    return _recorder_methods(state)[1]


def _secondary(primary, stage, error):
    # Use the actual BaseException descriptors. Neither a foreign container nor
    # an exception subclass's attribute hook can run while retaining an error.
    data = BaseException.__dict__['__dict__'].__get__(primary, BaseException)
    key, record_key = '__som_m6_recording_secondary__', '__som_m6_recording_secondary_record__'
    previous = data.get(key)
    # Copy exact builtin containers only. A custom metadata object is preserved
    # by identity in this source-owned plain record, without iterating it.
    rows = list(previous) if (type(previous) is list or type(previous) is tuple) else []
    list.append(rows, (stage, error, BaseException.__dict__['__traceback__'].__get__(error, BaseException)))
    record = {'primary': primary, 'had_previous': key in data, 'previous_value': previous,
              'had_previous_record': record_key in data, 'previous_record': data.get(record_key), 'rows': rows}
    data[record_key], data[key] = record, rows


def _plain(value):
    kind = type(value)
    if any(kind is accepted for accepted in (str, bytes, int, bool, type(None), _PATH_TYPE)):
        return
    if kind is float:
        _need(_math.isfinite(value), 'finite recording data required')
        return
    if kind is dict:
        _need(all(type(key) is str for key in value), 'plain recording data keys required')
        for item in value.values():
            _plain(item)
        return
    if (kind is list or kind is tuple):
        for item in value:
            _plain(item)
        return
    raise ValueError('foreign object in recording data')


def _argv(argv):
    _need((type(argv) is list or type(argv) is tuple) and argv and all((type(x) is str or type(x) is _PATH_TYPE) for x in argv),
          'plain nonempty recorded argv required')
    return [_os.fspath(x) for x in argv]


def _options(options, allowed):
    _need(type(options) is dict and set(options) <= allowed, 'unobserved subprocess option refused')
    for key in ('capture_output', 'check', 'text', 'start_new_session'):
        if key in options:
            _need(type(options[key]) is bool, 'plain boolean subprocess option required')
    if options.get('cwd') is not None:
        _need((type(options['cwd']) is str or type(options['cwd']) is _PATH_TYPE), 'plain subprocess cwd required')
    if options.get('env') is not None:
        env = options['env']
        _need(type(env) is dict and all(type(k) is str and type(v) is str for k, v in env.items()),
              'plain subprocess environment required')
    if options.get('timeout') is not None:
        value = options['timeout']
        _need((type(value) is int or type(value) is float) and _math.isfinite(value) and value >= 0, 'finite timeout required')
    for key in ('stdout', 'stderr'):
        value = options.get(key)
        _need(value is None or type(value) is int and value in ({PIPE, STDOUT} if key == 'stderr' else {PIPE})
              or any(type(value) is kind for kind in (_io.TextIOWrapper, _io.BufferedWriter, _io.FileIO)),
              'plain actual subprocess stream required')


def _recorder_caller(state):
    frame = _sys._getframe(2)
    recorder_class, recorder, methods, original_codes = _recorder_methods(state)
    _need(frame.f_locals.get('self') is recorder and any(
        frame.f_code is function.__code__ and frame.f_globals is function.__globals__
        for name, function, signature in methods if name in ('run', 'command')),
        'bounded run requires the actual Recorder caller')


def _child(state, process, cleanup=False):
    _need(type(process) is state.refs['guard'], 'actual guarded child object required')
    actual = _kernel_request(state, process, 'snapshot')
    row = actual['child']
    _need(type(row) is __som_native_ChildState__ and type(state.children) is list
          and sum(public is row for public in state.children) == 1,
          'actual owned recorded child required')
    data = _dict(process)
    public_pid, row_pid = data.get('pid'), row.pid
    _need(type(public_pid) is int and type(row_pid) is int
          and public_pid == actual['pid'] and row_pid == actual['pid'], 'actual child PID changed')
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
    recorder_class, recorder, methods, original_codes = _recorder_methods(state)
    if type(wait) is _FunctionType:
        original = next(function for name, function, signature in methods if name == 'popen')
        _need(wait.__globals__ is original.__globals__
              and any(wait.__code__ is c for c in original_codes),
              'actual Recorder wait function required')
        cells = dict(zip(wait.__code__.co_freevars, wait.__closure__ or ()))
        _need(set(cells) == {'self', 'process', 'original_wait'}
              and cells['self'].cell_contents is recorder
              and cells['process'].cell_contents is process, 'actual Recorder wait closure owner required')
        underlying = cells['original_wait'].cell_contents
    else:
        underlying = wait
    _need(type(underlying) is _MethodType and underlying.__self__ is process
          and underlying.__func__ is _capture_request(state, 'base-wait'),
          'actual underlying Popen wait required')
    return wait


def _recorder_methods(state):
    """Same FunctionType identity cannot grant changed code or bindings."""
    return _capture_request(state, 'recorder')


def _observed_wait(process, timeout=None):
    state = _verify(_ACTIVE)
    row = _child(state, process)
    kernel = _wait_owner(state, process)
    if _kernel_request(state, process, 'snapshot')['values'].get('returncode') is not None:
        _kernel_poll(process)
    try:
        code = row.wait_original(timeout=timeout)
    except BaseException:
        state.events.append({'kind': 'wait-error', 'pid': process.pid})
        raise
    _wait_proof(state, kernel, code)
    _kernel_request(state, process, 'complete', code)
    row.wait_completed, row.exit_code = True, code
    state.events.append({'kind': 'wait', 'pid': process.pid, 'exit_code': code})
    return code


def _adopt(state, process, requested, options, failed=False, startup_failure=None):
    _need(type(process) is state.refs['guard'], 'actual guarded child required before recording')
    kernel = _capture_wait_owner(state, process)
    captured = _kernel_request(state, process, 'snapshot')
    actual = _argv(_dict(process).get('args'))
    row = __som_native_ChildState__(process, list(requested), actual,
        str(_Path(options.get('cwd') or _Path.cwd()).absolute()), options.get('start_new_session', False))
    public_pid, row_pid = _dict(process).get('pid'), row.pid
    _need(type(row_pid) is int and type(public_pid) is int
          and row_pid == captured['pid'] and public_pid == captured['pid'],
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
    row.streams = list(captured['streams'])
    _need(not captured['foreign_streams']
          and all(_dict(process).get(name) is stream for name, stream in captured['streams']),
          'actual child pipe object required')
    process.wait = row.wait_hook
    state.children.append(row)
    _kernel_request(state, process, 'associate', row)
    expected = state.refs['python_argv'](requested, cwd=options.get('cwd'))
    _need(actual == expected, 'actual recorded child argv differs from Context rewrite')
    evidence_path = None
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
        evidence_path = str(path)
    elif python:
        _need(failed and type(startup_failure) is dict
              and any(startup_failure is item for item in _dict(state.refs['python_owner'])['startup_failures']),
              'actual Context startup failure evidence required')
        row.startup_failure = startup_failure
    elif not python:
        _need(receipt is None, 'nonPython child cannot carry a Python startup receipt')
    _capture_request(state, 'evidence-child', process, argument=row)
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
        recorder_class, recorder, methods, original_codes = _recorder_methods(state)
        method = next(function for name, function, signature in methods if name == 'popen')
        try:
            process = method(recorder, requested, **kwargs)
        except BaseException as primary:
            child = BaseException.__getattribute__(primary, '__dict__').get('__som_native_startup_child__')
            proof = BaseException.__getattribute__(primary, '__dict__').get('__som_native_startup_failure__')
            if child is None:
                trace = BaseException.__getattribute__(primary, '__traceback__')
                while trace is not None:
                    frame = trace.tb_frame
                    if (frame.f_code is method.__code__ and frame.f_globals is method.__globals__
                            and frame.f_locals.get('self') is recorder
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
                        _kernel_request(state, child, 'complete', code)
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
    actual = _kernel_request(state, process, 'snapshot')
    _need(type(pid) is int and actual['pid'] == pid, 'actual captured cleanup PID required')
    return _kernel_request(state, process, 'operations')


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
            if any(type(stream) is kind for kind in (_io.TextIOWrapper, _io.BufferedReader, _io.BufferedWriter, _io.FileIO)):
                streams.append((name, stream))
                proof['streams_closed'][name] = False
            else:
                _secondary(primary, 'record-unregistered-stream-owner',
                           ValueError('foreign unregistered child stream refused'))
        kernel = _capture_wait_owner(state, process)
        for name, stream in _kernel_request(state, process, 'snapshot')['streams']:
            if stream is not None and not any(known is stream for unused, known in streams):
                streams.append((name, stream))
                proof['streams_closed'][name] = False
        previous_primary = _kernel_request(state, process, 'begin-cleanup', primary)
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
        actual = _kernel_request(state, process, 'snapshot')
        _need(actual['terminal'] or actual['live_observed'] and not actual['unknown'],
              'unregistered cleanup requires an actual live or terminal kernel observation')
        if code is None:
            proof['signal_attempted'] = True
            try:
                _retained_kill(state, process, primary)
                proof['signal_completed'] = True
            except BaseException as error:
                _secondary(primary, 'record-unregistered-kill', error)
        try:
            code = wait(timeout=10)
            _wait_proof(state, kernel, code)
            _kernel_request(state, process, 'complete', code)
            proof['wait_completed'], proof['exit_code'] = True, code
        except BaseException as error:
            _secondary(primary, 'record-unregistered-wait', error)
    except BaseException as error:
        _secondary(primary, 'record-unregistered-owner', error)
    finally:
        if kernel is not None:
            try:
                _kernel_request(state, process, 'end-cleanup', previous_primary)
            except BaseException as error:
                _secondary(primary, 'recorded-cleanup-end-capture', error)
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
    actual = _kernel_request(state, row, 'snapshot')
    process = actual['process']
    kernel = None
    previous_primary = None
    try:
        kernel = _wait_owner(state, process)
        previous_primary = _kernel_request(state, process, 'begin-cleanup', primary)
        poll, wait = _owned_process_operations(state, process, actual['pid'])
    except BaseException as error:
        _secondary(primary, 'recorded-cleanup-owner', error)
        if kernel is not None:
            try:
                _kernel_request(state, process, 'end-cleanup', previous_primary)
            except BaseException as error:
                _secondary(primary, 'recorded-cleanup-end-capture', error)
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
        try:
            _kernel_request(state, process, 'end-cleanup', previous_primary)
        except BaseException as error:
            _secondary(primary, 'recorded-cleanup-end-capture', error)
        return
    actual = _kernel_request(state, process, 'snapshot')
    if not (actual['terminal'] or actual['live_observed'] and not actual['unknown']):
        _secondary(primary, 'recorded-cleanup-kernel-unknown',
                   ValueError('no actual live or terminal kernel observation for cleanup'))
        try:
            _kernel_request(state, process, 'end-cleanup', previous_primary)
        except BaseException as error:
            _secondary(primary, 'recorded-cleanup-end-capture', error)
        return
    if code is None:
        try:
            _retained_kill(state, process, primary)
        except BaseException as error:
            _secondary(primary, 'recorded-cleanup-kill', error)
    try:
        code = wait(timeout=10)
        _wait_proof(state, kernel, code)
        _kernel_request(state, process, 'complete', code)
        row.wait_completed, row.exit_code = True, code
    except BaseException as error:
        _secondary(primary, 'recorded-cleanup-wait', error)
    finally:
        try:
            _kernel_request(state, process, 'end-cleanup', previous_primary)
        except BaseException as error:
            _secondary(primary, 'recorded-cleanup-end-capture', error)


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
        actual = _kernel_request(state, process, 'snapshot')
        _need(actual['completed'] and type(code) is int and actual['code'] == code,
              'run requires a real terminal wait')
        if check and code:
            raise _CALLED_ERROR(code, process.args, output=stdout, stderr=stderr)
        return _COMPLETED(process.args, code, stdout, stderr)
    except BaseException as error:
        primary = error
        raise
    finally:
        if process is not None:
            try:
                streams = _kernel_request(state, process, 'snapshot')['streams']
            except BaseException as error:
                streams = ()
                if primary is not None:
                    _secondary(primary, 'recorded-stream-capture', error)
                else:
                    failure = ('recorded-stream-capture', error,
                               BaseException.__getattribute__(error, '__traceback__'))
                    state.secondary.append(failure)
                    state.failures.append(failure)
            for name, stream in streams:
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
        recorder_class, recorder, methods, original_codes = _recorder_methods(state)
        method = next(function for name, function, signature in methods if name == 'run')
        return method(recorder, requested, **kwargs)
    finally:
        state.phase = None


def _close(token, primary=None):
    """Preserve an explicit or active body exception; return without replacing it."""
    global _ACTIVE, _CAPTURE
    primary = _sys.exception() if primary is None else primary
    _need(primary is None or any(base is BaseException for base in
          type.__getattribute__(type(primary), '__mro__')), 'actual primary exception required')
    failures = []
    try:
        _need(type(token) is __som_native_ScopeState__ and token is _ACTIVE and not token.closed,
              'actual owned recording scope required for close')
        _need(type(token.refs) is dict, 'recording close owner thread differs')
        close_pid, close_thread = token.refs.get('pid'), token.refs.get('thread')
        _need(type(close_pid) is int and type(close_thread) is int,
              'recording close owner thread differs')
        _need(close_thread == _threading.get_ident() and close_pid == _os.getpid(),
              'recording close owner thread differs')
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
        try:
            children = _capture_request(state, 'children')
        except BaseException as error:
            failures.append(('recording-child-capture-unresolved', error,
                             BaseException.__getattribute__(error, '__traceback__')))
            children = ()
        public_children = state.children
        if (type(public_children) is not list or len(public_children) != len(children)
                or not all(actual is public for actual, public in zip(children, public_children))):
            failures.append(('recording-children-owner', ValueError('actual recorded child projection changed'), None))
        for row in children:
            try:
                actual = _kernel_request(state, row, 'snapshot')
            except BaseException as error:
                failures.append(('recording-child-capture-unresolved', error,
                                 BaseException.__getattribute__(error, '__traceback__')))
                continue
            process, pid = actual['process'], actual['pid']
            if type(row) is not __som_native_ChildState__:
                failures.append(('recording-child-owner', ValueError('foreign child record refused'), None))
            else:
                try:
                    _child(state, process)
                    _need(type(row.wait_completed) is bool and row.wait_completed is actual['completed']
                          and (not actual['completed'] or row.exit_code == actual['code']),
                          'actual recorded terminal wait changed')
                    if actual['completed']:
                        _wait_proof(state, _wait_owner(state, process), actual['code'])
                except BaseException as error:
                    failures.append(('recording-child-postcheck', error,
                                     BaseException.__getattribute__(error, '__traceback__')))
            if not actual['completed']:
                error = RuntimeError('recorded child lacked its original terminal wait')
                failures.append(('recording-missing-wait', error, error.__traceback__))
                try:
                    _stop_and_wait(state, row, error)
                except BaseException as later:
                    _secondary(error, 'recorded-cleanup-capture', later)
            try:
                actual = _kernel_request(state, row, 'snapshot')
            except BaseException as error:
                failures.append(('recording-child-capture-unresolved', error,
                                 BaseException.__getattribute__(error, '__traceback__')))
                # The previous actual snapshot still selects owned pipes. It
                # grants no new wait credit after this refusal.
                if type(row) is __som_native_ChildState__:
                    row.wait_completed, row.exit_code = False, None
            else:
                if type(row) is __som_native_ChildState__:
                    row.wait_completed, row.exit_code = actual['completed'], actual['code'] if actual['completed'] else None
            for name, stream in actual['streams']:
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as error:
                        failures.append(('recording-stream-close', error,
                                         BaseException.__getattribute__(error, '__traceback__')))
            try:
                _need(type(row) is __som_native_ChildState__ and _dict(process).get('wait') is row.wait_hook,
                      'recorded wait replaced by a later owner')
                _wait_provenance(state, process, row.wait_original)
                process.wait = row.wait_original
            except BaseException as error:
                failures.append(('recording-wait-restore', error, error.__traceback__))
        try:
            evidence_target, payload = _capture_request(state, 'evidence')
            with _Path(evidence_target).open('xb') as stream:
                stream.write((_json.dumps(payload, sort_keys=True, separators=(',', ':')) + '\n').encode())
        except BaseException as error:
            failures.append(('recording-evidence', error, error.__traceback__))
    finally:
        try:
            _restore_kernel_hooks(state, failures)
        except BaseException as error:
            failures.append(('recording-kernel-hook-restore', error,
                             BaseException.__getattribute__(error, '__traceback__')))
        # Each preparation happens immediately before its own real call.
        # The first original close cannot authorize a later changed function.
        for action, owner_stage, call_stage in (
                ('close-python', 'recording-python-borrow-owner', 'recording-python-borrow'),
                ('close-source', 'recording-source-borrow-owner', 'recording-source-borrow')):
            try:
                operation = _capture_request(state, action)
            except BaseException as error:
                failures.append((owner_stage, error, BaseException.__getattribute__(error, '__traceback__')))
                continue
            try:
                operation()
            except BaseException as error:
                failures.append((call_stage, error, BaseException.__getattribute__(error, '__traceback__')))
        try:
            _capture_request(state, 'release')
        except BaseException as error:
            failures.append(('recording-producer-release', error, BaseException.__getattribute__(error, '__traceback__')))
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
        refs.clear()
    if primary is not None:
        for stage, error, trace in failures:
            _secondary(primary, stage, error)
        return
    if failures:
        stage, first, trace = failures[0]
        for later_stage, error, unused in failures[1:]:
            _secondary(first, later_stage, error)
        raise BaseException.with_traceback(first, trace)
