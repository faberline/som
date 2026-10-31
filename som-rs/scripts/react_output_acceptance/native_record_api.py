"""Same-Core Step12 consumer. Evidence data grants no native or product PASS.

The live original products entry holds its own module-body CodeTypes before
reading inputs. This stable module retains no binding to a suspended entry.
It borrows the collector's existing private4 and never creates a Context/group.
"""
from __future__ import annotations

import dis
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import threading
from types import CodeType, FunctionType, ModuleType

SELF = 'som-rs/scripts/react_output_acceptance/native_record_api.py'
PRODUCTS = 'som-rs/scripts/react_output_acceptance/check_products.py'
SESSION = 'som-rs/scripts/react_output_acceptance/m6_native_entry.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
FACADE = 'som-rs/scripts/react_output_acceptance/native_recorded_subprocess.py'
RUNNER = 'som-rs/tests/react-output-v1/run_api_contract.py'
PROBE = 'som-rs/tests/react-output-v1/api_contract.mjs'
HOOK = 'som-rs/scripts/react_output_acceptance/record_api_fetch.mjs'
MAX_JSON = 32 * 1024 * 1024


def _need(value, message):
    if not value:
        raise ValueError(message)


def _strict_json(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_JSON, 'bounded JSON bytes required')
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result, 'duplicate JSON key refused')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON constant refused: ' + value)
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    _plain(value)
    return value


def _plain(value, depth=0, budget=None):
    if budget is None:
        budget = [1000000]
    budget[0] -= 1
    _need(depth <= 64 and budget[0] >= 0, 'bounded plain JSON tree required')
    kind = type(value)
    if kind in (str, int, bool, type(None)):
        return
    if kind is float:
        _need(value == value and abs(value) != float('inf'), 'finite JSON number required')
        return
    _need(kind in (dict, list), 'plain JSON containers required')
    if kind is dict:
        _need(all(type(key) is str for key in value), 'plain string JSON keys required')
        values = value.values()
    else:
        values = value
    for child in values:
        _plain(child, depth + 1, budget)


def _physical(path, directory):
    _need(type(path) is type(Path()) and path.is_absolute() and '..' not in path.parts
          and not any(part.is_symlink() for part in (path, *path.parents)),
          'physical absolute path without symlinks required')
    info = path.stat()
    _need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
          and (directory or info.st_nlink == 1), 'regular single-link file or directory required')
    return path


def _signature(row):
    _need(type(row) is tuple and len(row) == 10, 'held original invocation required')
    function, code, namespace, builtins, defaults, items, keywords, pairs, closure, cells = row
    _need(type(function) is FunctionType and function.__code__ is code
          and function.__globals__ is namespace and function.__builtins__ is builtins
          and function.__defaults__ is defaults and function.__kwdefaults__ is keywords
          and function.__closure__ is closure and len(defaults or ()) == len(items)
          and all(a is b for a, b in zip(defaults or (), items))
          and len(keywords or {}) == len(pairs)
          and all(key in (keywords or {}) and keywords[key] is value for key, value in pairs)
          and len(closure or ()) == len(cells)
          and all(cell is original and cell.cell_contents is value
                  for cell, (original, value) in zip(closure or (), cells)),
          'original function/code/globals/builtins/defaults/cells changed')
    return function


def _invocation(function):
    _need(type(function) is FunctionType, 'source-owned Python function required')
    return (function, function.__code__, function.__globals__, function.__builtins__,
            function.__defaults__, tuple(function.__defaults__ or ()), function.__kwdefaults__,
            tuple((function.__kwdefaults__ or {}).items()), function.__closure__,
            tuple((cell, cell.cell_contents) for cell in (function.__closure__ or ())))


def _callsite(frame, name):
    # f_lasti points at CALL or its inline cache while the real callee is live.
    instructions = tuple(dis.get_instructions(frame.f_code, show_caches=True))
    positions = [i for i, item in enumerate(instructions) if item.offset == frame.f_lasti]
    _need(len(positions) == 1, 'actual caller instruction offset required')
    index = positions[0]
    while index >= 0 and instructions[index].opname == 'CACHE':
        index -= 1
    _need(index >= 0 and instructions[index].opname == 'CALL', 'actual source CALL instruction required')
    index -= 1
    while index >= 0 and instructions[index].opname in ('CACHE', 'PRECALL', 'PUSH_NULL'):
        index -= 1
    _need(index >= 0 and instructions[index].opname == 'LOAD_FAST'
          and instructions[index].argval == name, 'one exact no-argument source callsite required')


def _base():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    _need(controls.role_sources.get('m6.api.case.consumer') == SELF
          and controls.role_sources.get('m6.collector.session') == SESSION
          and type(__som_native_collector_session__) is ModuleType,
          'fixed stable consumer and collector source roles required')
    session = ModuleType.__getattribute__(__som_native_collector_session__, '__dict__')
    _need(session.get('_native_products_origin') is __som_native_products_origin__
          and session.get('_NativeCollectorScope') is __som_native_CollectorScope__
          and session.get('__file__') == str(controls.root / SESSION),
          'actual stable collector exports required')
    scope, ctx, live_values, manifest_raw = __som_native_products_origin__()
    _need(type(scope) is __som_native_CollectorScope__ and type(ctx) is __som_native_Context__
          and type(__som_native_context_module__) is ModuleType
          and ModuleType.__getattribute__(__som_native_context_module__, '__dict__').get('_ACTIVE') is ctx
          and __som_native_context_getter__() is ctx
          and scope.ctx is ctx and type(scope.controls) is type(controls)
          and scope.controls.root == controls.root
          and scope.controls.helper_revision == controls.helper_revision,
          'same actual Context, Core lease and collector owner required')
    _need(ctx.path.read_bytes() == ctx.raw and os.environ.get('SOM_FULL_PRODUCT_CONTEXT') == str(ctx.path)
          and _strict_json(ctx.raw) == ctx.data
          and ctx.repo == controls.root
          and ctx.helper_revision == ctx.candidate_revision == controls.helper_revision == controls.candidate_revision,
          'actual raw Context and same candidate required')
    data = ctx.data.get('m6_react_inputs')
    _need(type(data) is dict and type(data.get('collector_out')) is str,
          'raw Context original collector C required')
    c = _physical(Path(data['collector_out']), True)
    _need(c == scope.out and controls.root / '.local' in c.parents,
          'same original repository/.local C required')
    _need(type(live_values) is tuple and len(live_values) == 4 and scope.live_values is live_values,
          'actual existing provider private4 case validation required')
    qa = live_values[1]
    _need(type(qa) is dict and qa['full_before_path'] == c / 'api-qa-evidence/full-before.json'
          and qa['full_before_sha256'] == scope.before['sha256'],
          'actual first-load full-before producer must match case validation')
    ctx.sources.verify()
    return controls, scope, ctx, live_values, manifest_raw, c


def _products_main():
    frame = __som_native_API_FRAME_GET__(1)
    found = []
    while frame is not None:
        if frame.f_code.co_name == 'main' and frame.f_globals.get('__file__', '').endswith('/' + PRODUCTS):
            found.append(frame)
        frame = frame.f_back
    _need(len(found) == 1, 'one actual original products main required')
    main = found[0]
    handoff = main.f_locals.get('_native_source_handoff')
    _need(type(handoff) is tuple and len(handoff) == 7, 'early original products handoff required')
    module, wrapper, namespace, builtins, rows, pid, thread = handoff
    _need(main.f_back is wrapper and wrapper.f_back is module
          and module.f_code.co_name == '<module>' and module.f_globals is namespace
          and main.f_globals is wrapper.f_globals is namespace
          and main.f_builtins is wrapper.f_builtins is module.f_builtins is builtins
          and wrapper.f_locals.get('_native_source_handoff') is handoff
          and pid == os.getpid() and thread == threading.get_ident(),
          'actual module-body/wrapper/main process/thread ancestry required')
    functions = main.f_locals.get('_native_functions')
    _need(type(functions) is dict and type(rows) is tuple and len(functions) == len(rows),
          'original products invocation set required')
    for name, row in rows:
        _need(functions.get(name) is row and _signature(row).__globals__ is namespace
              and namespace.get(name) is row[0]
              and any(code is row[1] for code in module.f_code.co_consts),
              'products original CodeType is not its actual module-body constant')
    _need(main.f_code is functions['main'][1]
          and wrapper.f_code is functions['_native_products_entry'][1],
          'held original products entry/main CodeType required')
    return main, functions


def _tools_values(ctx, live_values):
    proof = __som_native_Context__.python_payload(ctx, sys.executable)
    _need(type(proof) is dict and proof.get('role') == 'react-api-qa'
          and proof.get('requirements_count') == 39 and proof.get('wheel_count') == 40
          and proof.get('pth_executed') is False and proof.get('product_acceptance_passed') is False,
          'actual admitted API39/40 Python payload required')
    qa = live_values[1]
    values = {name: str(qa[name]) for name in ('python', 'node', 'npm_cli', 'cache', 'pg_bin', 'lsof', 'ps')}
    values['site'] = proof['site']
    _need(all(type(value) is str and value and '\0' not in value for value in values.values())
          and Path(values['python']) == Path(sys.executable)
          and Path(values['site']) == live_values[2], 'same real interpreter/site and finite tool data required')
    return values


def _main_inputs():
    controls, scope, ctx, live_values, raw, c = _base()
    main, functions = _products_main()
    _need(__som_native_API_FRAME_GET__(1) is main and main.f_locals.get('_native_consumer') is sys.modules[__name__],
          'only actual original main initial source handoff required')
    return _tools_values(ctx, live_values)


def _manifest_rows(raw):
    manifest = _strict_json(raw)
    entries = manifest.get('products') if type(manifest) is dict else manifest
    _need(type(entries) is list and len(entries) == 129, 'all original129 manifest products required')
    ids = set()
    for entry in entries:
        _need(type(entry) is dict and type(entry.get('id')) is str
              and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', entry['id']) is not None
              and entry['id'] not in ('.', '..') and entry['id'] not in ids,
              'unique safe original manifest case id required')
        ids.add(entry['id'])
        _need(type(entry.get('product')) is str and type(entry.get('python_request')) is str
              and type(entry.get('prompt')) is dict, 'complete original manifest case fields required')
    return entries


def _case_origin(completed):
    controls, scope, ctx, live_values, raw, c = _base()
    main, functions = _products_main()
    _need(main.f_globals.get('__file__') == str(controls.root / PRODUCTS)
          and main.f_globals.get('__som_raw_sha256__') == hashlib.sha256(ctx.sources.read(PRODUCTS)).hexdigest(),
          'actual original products source bytes required')
    args = main.f_locals.get('args')
    _need(args is not None and args.only is None
          and main.f_locals.get('manifest_path') == c / 'bundles/products/manifest.json'
          and main.f_locals.get('out') == c / 'products'
          and Path(args.manifest) == c / 'bundles/products/manifest.json'
          and Path(args.out) == c / 'products', 'entire original same-C products loop required')
    entries = main.f_locals.get('entries')
    observed = _manifest_rows(raw)
    _need(type(entries) is list and entries == observed
          and main.f_locals.get('manifest') == _strict_json(raw),
          'actual full unfiltered parsed original manifest changed')
    case = __som_native_API_FRAME_GET__(1)
    while case is not None and case.f_back is not main:
        case = case.f_back
    _need(case is not None and case.f_code is functions['check_case'][1]
          and case.f_globals is main.f_globals and case.f_builtins is main.f_builtins,
          'direct original main to check_case frame required')
    entry = case.f_locals.get('entry')
    _need(type(entry) is dict and entry is main.f_locals.get('entry')
          and sum(item is entry for item in entries) == 1
          and type(main.f_locals.get('index')) is int
          and entries[main.f_locals['index'] - 1] is entry
          and case.f_locals.get('base_url') == args.url
          and args.url == scope.main.f_locals['candidate_url'],
          'same actual original case object and candidate service required')
    cid = entry['id']
    _need(entry == next(item for item in observed if item['id'] == cid)
          and entry['product'] == str(c / 'bundles/products' / cid)
          and case.f_locals.get('out') == c / 'products'
          and case.f_locals.get('work') == c / 'products' / cid
          and case.f_locals.get('product') == c / 'bundles/products' / cid
          and case.f_locals.get('cache') == Path(live_values[1]['cache']),
          'exact original case product/work/cache equations required')
    _physical(c / 'bundles/products' / cid, True)
    if completed:
        _need(case.f_locals.get('python_problem') is None
              and case.f_locals.get('_native_python') is _signature(functions['python_product'])
              and case.f_locals.get('python_dir') == c / 'products' / cid / 'python'
              and case.f_locals.get('_native_consume') is _signature(main.f_locals['_native_consume_row']),
              'held original Python producer must have returned successfully')
        _callsite(case, '_native_consume')
        _python_bytes(entry, c / 'products' / cid / 'python')
    return controls, scope, ctx, live_values, main, case, entry, c


def _python_bytes(entry, root):
    request = _strict_json(entry['python_request'].encode('utf-8'))
    _need(type(request) is dict and 'target' not in request
          and request.get('prompt') == entry['prompt'], 'original target-free Python request/prompt required')
    compiled = _strict_json(_physical(root / 'compile.response.json', False).read_bytes())
    assembled = _strict_json(_physical(root / 'assemble.response.json', False).read_bytes())
    bundle = compiled.get('bundle') if type(compiled) is dict else None
    _need(type(bundle) is dict and bundle.get('target', 'python') == 'python'
          and bundle.get('prompt') == entry['prompt'], 'actual compile response prompt/target differs')
    _need((root / 'prompt.json').read_bytes() ==
          (json.dumps(entry['prompt'], ensure_ascii=False, indent=2) + '\n').encode('utf-8'),
          'actual original prompt bytes differ')
    files = assembled.get('files') if type(assembled) is dict else None
    _need(type(files) is dict and files and all(type(k) is str and type(v) is str for k, v in files.items()),
          'actual assembled text product files required')
    expected = {}
    for name, content in files.items():
        path = Path(name)
        _need(name and not path.is_absolute() and '..' not in path.parts and path.as_posix() == name,
              'safe exact assembled product path required')
        target = _physical(root / 'product' / path, False)
        _need(target.read_bytes() == content.encode('utf-8'), 'actual assembled product bytes differ')
        expected[name] = content.encode('utf-8')
    actual = {}
    for path in (root / 'product').rglob('*'):
        _need(not path.is_symlink(), 'product symlink refused')
        if path.is_file():
            _physical(path, False)
            actual[path.relative_to(root / 'product').as_posix()] = path.read_bytes()
    _need(actual == expected, 'complete original Python product inventory differs')
    return expected



def _check_gates(argv, log):
    controls, scope, ctx, live_values, main, case, entry, c = _case_origin(False)
    native_step = __som_native_API_FRAME_GET__(1)
    step = native_step.f_back
    functions = main.f_locals['_native_functions']
    _need(native_step.f_code is functions['_native_step'][1]
          and step.f_code is functions['step'][1] and step.f_back is case
          and native_step.f_globals is step.f_globals is case.f_globals
          and native_step.f_builtins is step.f_builtins is case.f_builtins
          and _signature(main.f_locals['_native_gate_row']).__code__ is __som_native_API_FRAME_GET__().f_code,
          'actual held original nested gates caller chain required')
    expected = [str(ctx.repo / 'som-rs/tests/react-output-v1/run_gates.py'),
                str(c / 'bundles/products' / entry['id']),
                str(c / 'products' / entry['id'] / 'gates'), '--cache',
                str(live_values[1]['cache'])]
    _need(type(argv) is list and all(type(value) is str for value in argv)
          and argv == expected and log == c / 'products' / entry['id'] / 'gates.log',
          'exact actual original case gates argv/log required')

def _tools():
    # Tool strings are data, not permission. Gate initialization precedes Python
    # production. API host initialization occurs during the original _consume.
    controls, scope, ctx, live_values, main, case, entry, c = _case_origin(False)
    frame = __som_native_API_FRAME_GET__(1)
    allowed = {
        'som-rs/tests/react-output-v1/run_gates.py', RUNNER,
        'som-rs/behavior/typescript/host.py', 'som-rs/behavior/hosts.py',
    }
    _need(frame.f_code.co_name == '<module>'
          and frame.f_globals.get('__file__') in {str(controls.root / path) for path in allowed}
          and frame.f_globals.get('__som_native_API_tools__') is _tools,
          'fixed source module-body tool data use required')
    relative = Path(frame.f_globals['__file__']).relative_to(controls.root).as_posix()
    _need(frame.f_globals.get('__som_raw_sha256__') == hashlib.sha256(ctx.sources.read(relative)).hexdigest(),
          'tool consumer source bytes differ')
    return _tools_values(ctx, live_values)


def _check_case_output(ctx, out):
    controls, scope, actual, live_values, main, case, entry, c = _case_origin(True)
    _need(ctx is actual, 'the actual case Context is required')
    _exact_case_output(c, entry['id'], out)
    consume = __som_native_API_FRAME_GET__(1)
    while consume is not None and consume.f_code is not main.f_locals['_native_consume_row'][1]:
        consume = consume.f_back
    _need(consume is not None and consume.f_globals is globals()
          and consume.f_builtins is main.f_locals['_native_consume_row'][3]
          and consume.f_locals.get('ctx') is ctx and consume.f_locals.get('raw_out') == out,
          'live original stable consumer acquisition required')
    rows = consume.f_locals.get('_facade_functions')
    _need(type(rows) is dict and all(_signature(row) for row in rows.values()),
          'held original facade functions required')
    acquire, request = __som_native_API_FRAME_GET__(1), __som_native_API_FRAME_GET__(2)
    _need(request.f_code is rows['_capture_request'][1]
          and acquire.f_globals is request.f_globals is rows['_capture_request'][2]
          and any(code is acquire.f_code for code in request.f_code.co_consts if type(code) is CodeType)
          and acquire.f_locals.get('ctx') is ctx and acquire.f_locals.get('out') == out,
          'actual original facade acquisition caller required')
    _physical(out, True)


def _exact_case_output(c, cid, out):
    # C and I come only from the live original case proof above. This helper
    # rejects path data. It cannot establish that proof or authorize dispatch.
    _need(type(c) is type(Path()) and type(out) is type(Path()) and type(cid) is str
          and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', cid) is not None
          and cid not in ('.', '..') and out == c / 'products' / cid / 'api-raw',
          'only this actual case raw output is allowed')
    _physical(out, True)


def _consume():
    controls, scope, ctx, live_values, main, case, entry, c = _case_origin(True)
    _need(__som_native_API_FRAME_GET__(1) is case
          and _signature(main.f_locals['_native_consume_row']).__code__ is __som_native_API_FRAME_GET__().f_code,
          'actual original no-argument consumer call required')
    python_case = c / 'products' / entry['id'] / 'python'
    react_product = c / 'bundles/products' / entry['id']
    api_work = c / 'products' / entry['id'] / 'api'
    raw_out = c / 'products' / entry['id'] / 'api-raw'
    raw_out.mkdir(mode=0o700, exist_ok=False)
    _physical(raw_out, True)
    # The stable consumer is now complete. Loading the facade here lets its
    # persistent fixed binding target this stable validator, never __main__.
    _original_path, _original_path_items = sys.path, tuple(sys.path)
    token, recorder = None, None
    primary, trace, secondary = None, None, []
    code = 1
    receipt = {'version': 'som.react-api-raw.v1', 'case': str(python_case),
               'react': str(react_product), 'work': str(api_work),
               'runner_sha256': hashlib.sha256(ctx.sources.read(RUNNER)).hexdigest(),
               'probe_sha256': hashlib.sha256(ctx.sources.read(PROBE)).hexdigest(),
               'hook_sha256': hashlib.sha256(ctx.sources.read(HOOK)).hexdigest(),
               'product_api_sha256': hashlib.sha256((react_product / 'src/api.ts').read_bytes()).hexdigest(),
               'product_form_sha256': hashlib.sha256((react_product / 'src/form.ts').read_bytes()).hexdigest(),
               'product_schema_sha256': hashlib.sha256((react_product / 'src/schema.ts').read_bytes()).hexdigest(),
               'execution': 'same-Core-inprocess-entry', 'child_wait_claimed_for_entry': False,
               'native_consumer_sha256': hashlib.sha256(ctx.sources.read(SELF)).hexdigest(),
               'logical_record_api_argv': [str(ctx.repo / 'som-rs/scripts/react_output_acceptance/record_api.py'),
                   str(python_case), str(react_product), str(api_work), '--raw-out', str(raw_out)]}
    try:
        facade = controls.load_module('m6.api.recording.subprocess')
        values = ModuleType.__getattribute__(facade, '__dict__')
        _need(values.get('__file__') == str(ctx.repo / FACADE)
              and values.get('__som_raw_sha256__') == hashlib.sha256(ctx.sources.read(FACADE)).hexdigest(),
              'actual original facade source required')
        _facade_functions = {name: _invocation(values[name]) for name in
                             ('_open', '_recording', '_close', '_capture_request')}
        token = _signature(_facade_functions['_open'])(ctx, raw_out, live_values[1])
        recorder = _signature(_facade_functions['_recording'])(token)
        _need(recorder.out == raw_out, 'actual original recording output differs')
        code = controls.execute_entry('entry.m6.api-contract',
                                      [str(python_case), str(react_product), str(api_work)])
        _need(type(code) is int, 'original API entry must complete with integer exit')
    except BaseException as error:
        primary, trace = error, error.__traceback__
    finally:
        # Evidence and real post-stop observations run before owner release.
        # A later evidence/close error never replaces the original exception/TB.
        try:
            if recorder is not None:
                report = api_work / 'report.json'
                if report.is_file():
                    receipt['api_report_sha256'] = hashlib.sha256(report.read_bytes()).hexdigest()
                    for process in _strict_json(report.read_bytes()).get('processes', []):
                        if type(process.get('pid')) is int and process['pid'] > 1:
                            command = process.get('command', [])
                            words = shlex.split(command[command.index('-o') + 1]) if '-o' in command else command
                            flag = '-p' if process.get('role') == 'postgres' else '--port'
                            port = int(words[words.index(flag) + 1]) if flag in words else None
                            recorder.observe(lambda: recorder.snapshot(process['pid'], 'after-stop', port))
                receipt['events'] = recorder.events
                receipt['recording_errors'] = recorder.errors
                receipt['fetch_files'] = [{'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in sorted(raw_out.glob('fetch-*.jsonl'))]
                if recorder.errors:
                    code = 1
            _case_origin(True)
            controls.verify_installed()
        except BaseException as error:
            if primary is None:
                primary, trace = error, error.__traceback__
            elif error is not primary:
                secondary.append(error)
        if token is not None:
            try:
                _signature(_facade_functions['_close'])(token, primary)
            except BaseException as error:
                if primary is None:
                    primary, trace = error, error.__traceback__
                elif error is not primary:
                    secondary.append(error)
        try:
            if sys.path is not _original_path:
                raise ValueError('native API runner replaced the original sys.path object')
        except BaseException as error:
            if primary is None:
                primary, trace = error, error.__traceback__
            elif error is not primary:
                secondary.append(error)
        finally:
            sys.path = _original_path
            _original_path[:] = _original_path_items
        receipt['exit_code'] = code
        receipt['original_error_type'] = None if primary is None else type(primary).__name__
        try:
            with (raw_out / 'receipt.json').open('x') as stream:
                json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                stream.write('\n')
        except BaseException as error:
            if primary is None:
                primary, trace = error, error.__traceback__
            elif error is not primary:
                secondary.append(error)
    if primary is not None:
        BaseException.__setattr__(primary, '__som_native_api_secondary__', tuple(secondary))
        raise BaseException.with_traceback(primary, trace)
    return code
