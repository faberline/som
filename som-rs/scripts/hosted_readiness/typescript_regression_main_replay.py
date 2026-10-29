"""Re-run exact original pure HTTP checker mains on immutable saved exchanges.

The caller first rederives native source authority, original commands, complete
input/capture inventories and shared post-close proof. This helper discharges
only the explicitly named old raw-audit gaps. It grants no whole-domain pass.
"""
from __future__ import annotations
import traceback

import builtins
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
from email.message import Message
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
from types import SimpleNamespace
import urllib.error
import urllib.request

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_main_replay.py'
OWNER = BASE + 'typescript_regression_owner.py'
COLLECTOR = 'som-rs/scripts/typescript_output_acceptance/run_acceptance.py'
CHECKER = BASE + 'typescript_regression_checker.py'
DIGESTS = BASE + 'typescript_native_digests.py'
INPUTS = BASE + 'typescript_regression_inputs.py'
RAW_JSON = BASE + 'raw_digest_compat.py'
CURRENT = 'http://127.0.0.1:18978'
BEFORE = 'http://127.0.0.1:18979'
SPECS = (
    ('target', 1, 'som-rs/scripts/typescript_output_acceptance/check_target.py', 'target', True),
    ('rt-diagnostics', 2, 'som-rs/scripts/typescript_output_acceptance/check_rt_diagnostics.py', 'rt-diagnostics', True),
    ('bundles', 4, 'som-rs/scripts/typescript_output_acceptance/check_bundles.py', 'bundles', True),
    ('python-replay', 7, 'som-rs/scripts/rust_output_acceptance/python_replay.py', 'python-replay.json', False),
    ('validate-cases', 8, 'som-rs/tests/rust-output-v1/validate_cases.py', 'validate-cases.json', False),
    ('ts-python-controls', 9, 'som-rs/tests/typescript-output-v1/check_python_controls.py', 'ts-python-controls.json', False),
    ('rust-python-controls', 10, 'som-rs/tests/rust-output-v1/check_python_controls.py', 'rust-python-controls.json', False),
)


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


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def parse(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'duplicate main-replay JSON member')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite main-replay JSON')))


def physical(path, *, exists=True):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
            'physical main-replay path required')
    if exists:
        require(path.exists(), 'main-replay member missing')
    return path


def regular(path, limit=64 * 1024**2):
    path = physical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
                'bounded regular main-replay file required')
        parts, count = [], 0
        while True:
            block = os.read(fd, min(1024**2, limit + 1 - count))
            if not block:
                break
            parts.append(block); count += len(block)
            require(count <= limit, 'main-replay file exceeds bound')
        after = os.fstat(fd)
    finally:
        os.close(fd)
    require(count == before.st_size and (before.st_dev, before.st_ino, before.st_size, before.st_mode,
        before.st_mtime_ns, before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size,
        after.st_mode, after.st_mtime_ns, after.st_ctime_ns), 'main-replay file changed during read')
    return b''.join(parts)


class Response(io.BytesIO):
    def __init__(self, raw, row):
        super().__init__(raw)
        self.status = self.code = row['status']
        self.url, self.reason = row['url'], http.client.responses.get(row['status'], 'saved response')
        self.headers = Message()
        for key, value in row.get('response_headers', []):
            self.headers.add_header(key, value)

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def info(self):
        return self.headers


class HttpTape:
    def __init__(self, root):
        self.root = physical(root)
        self.raw = regular(self.root / 'index.json')
        document = parse(self.raw)
        require(document.get('version') == 'som.typescript-regression-raw-http.v1'
                and document.get('product_acceptance_passed') is False and type(document.get('rows')) is list,
                'exact original complete HTTP capture required')
        self.rows, self.position = document['rows'], 0
        expected = {'index.json', 'checker-postcheck.json', 'raw-commands/index.json'} | {
            f'{i:06d}/{name}' for i in range(len(self.rows))
            for name in ('request.raw', 'response.raw', 'result.json')}
        actual = set()
        for path in self.root.rglob('*'):
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'pure HTTP capture links refused')
            if stat.S_ISREG(info.st_mode):
                actual.add(path.relative_to(self.root).as_posix())
        require(actual == expected, 'pure HTTP capture exact member universe differs')
        commands = parse(regular(self.root / 'raw-commands/index.json'))
        require(commands == {'version': 'som.typescript-regression-raw-commands.v1', 'rows': [],
                             'product_acceptance_passed': False}, 'pure checker must have no native command rows')
        for index, row in enumerate(self.rows):
            prefix = self.root / f'{index:06d}'
            require(type(row) is dict and row.get('index') == index and row.get('complete') is True
                    and row.get('method') == 'POST' and type(row.get('status')) is int
                    and 200 <= row['status'] <= 599
                    and row.get('url') in {base + route for base in (CURRENT, BEFORE)
                                          for route in ('/v1/compile', '/v1/assemble')}
                    and parse(regular(prefix / 'result.json')) == row
                    and sha(regular(prefix / 'request.raw')) == row.get('request_sha256')
                    and sha(regular(prefix / 'response.raw')) == row.get('response_sha256'),
                    'complete original HTTP capture slot differs')

    def open(self, opener, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        request = (fullurl if isinstance(fullurl, urllib.request.Request) else
                   urllib.request.Request(fullurl, data=data))
        body = request.data if request.data is not None else data
        require(type(body) is bytes and self.position < len(self.rows), 'missing complete replay HTTP slot')
        row = self.rows[self.position]
        prefix = self.root / f'{self.position:06d}'
        require(request.get_method() == row['method'] and request.full_url == row['url']
                and regular(prefix / 'request.raw') == body and sha(body) == row['request_sha256'],
                'original main source request/order differs')
        raw = regular(prefix / 'response.raw')
        require(sha(raw) == row['response_sha256'], 'replay response changed after admission')
        self.position += 1
        response = Response(raw, row)
        if row['status'] >= 400:
            raise urllib.error.HTTPError(row['url'], row['status'], response.reason, response.headers, response)
        return response

    def finish(self):
        require(self.position == len(self.rows) and regular(self.root / 'index.json') == self.raw,
                'unconsumed/changed original pure HTTP tape')
        return {'http_exchanges': self.position, 'http_index_sha256': sha(self.raw)}


class FrozenContext:
    """Only exact preloaded modules and source bytes are visible during replay."""
    def __init__(self, ctx, root, source_bytes, modules):
        self.repo, self.helper_revision, self.candidate_revision = ctx.repo, ctx.helper_revision, ctx.candidate_revision
        self.qa_mode, self.qa_source_authority = ctx.qa_mode, ctx.qa_source_authority
        self.cache, self.modules = dict(ctx.cache), modules
        self.cache.pop('typescript_regression_input_binding_closers', None)
        self.sources = SimpleNamespace(read=self.read_source)
        self.source_bytes = source_bytes
        self.data = copy.deepcopy(ctx.data)
        bindings = self.data['typescript_regression_inputs']
        bindings['roots'] = {role: str(root / 'inputs' / role) for role in bindings['roots']}
        bindings['siblings'] = {role: str(root / 'inputs/siblings' / role) for role in bindings['siblings']}

    def read_source(self, path):
        require(path in self.source_bytes, 'undeclared source read inside pure HTTP replay')
        return self.source_bytes[path]

    def module(self, key, path):
        require(path in self.modules, 'new repository module load inside pure HTTP replay refused')
        return self.modules[path]


class MainGuards:
    """Deny live processes/network and confine original-main writes to new output."""
    def __init__(self, ctx, root, capture, out, tape):
        self.ctx, self.root, self.out, self.tape = ctx, root, out, tape
        self.expected = capture['saved_files']
        self.originals, self.installed, self.cleanup_errors = [], [], []
        self.process_attempts, self.network_attempts = [], []
        self.original_builtin_open, self.original_io_open = builtins.open, io.open

    def replace(self, owner, key, value):
        self.originals.append((owner, key, getattr(owner, key)))
        setattr(owner, key, value)
        self.installed.append((owner, key, value))

    def denied_process(self, *args, **kwargs):
        self.process_attempts.append({'api': 'process', 'argv': repr(args)[:2048]})
        raise ValueError('native command forbidden during pure HTTP replay')

    def denied_network(self, *args, **kwargs):
        self.network_attempts.append({'api': 'network'})
        raise ValueError('live network forbidden during pure HTTP replay')

    def checked_open(self, file, mode='r', buffering=-1, encoding=None, errors=None,
                     newline=None, closefd=True, opener=None):
        require(not isinstance(file, int) and opener is None and closefd is True,
                'unbound file descriptor/custom opener in pure replay refused')
        path = physical(file, exists=False)
        write = any(letter in mode for letter in 'wax+')
        if write:
            require(self.out in path.parents and '+' not in mode, 'pure main writes outside fresh audit output refused')
            return self.original_io_open(path, mode, buffering, encoding, errors, newline, closefd,
                opener=lambda name, flags: os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o666))
        require(mode in {'r', 'rt', 'rb'}, 'unsupported pure main file mode')
        if Path(self.ctx.repo) in path.parents:
            relative = path.relative_to(self.ctx.repo).as_posix()
            require(relative in self.ctx.source_bytes, 'original main read an undeclared repository file')
            raw = regular(path)
            require(raw == self.ctx.source_bytes[relative], 'original main source/data changed from exact Git')
        elif self.root in path.parents:
            relative = path.relative_to(self.root).as_posix()
            wanted = self.expected.get(relative)
            require(type(wanted) is dict and {'sha256', 'size', 'mode'} <= set(wanted),
                    'original main read an unlisted captured input')
            raw = regular(path)
            require({'sha256': sha(raw), 'size': len(raw), 'mode': stat.S_IMODE(path.stat().st_mode)} == wanted,
                    'original main input changed from complete immutable capture')
        else:
            require(self.out in path.parents, 'original main attempted an undeclared external read')
            raw = regular(path)
        stream = io.BytesIO(raw)
        return stream if 'b' in mode else io.TextIOWrapper(stream, encoding=encoding, errors=errors, newline=newline)

    def __enter__(self):
        self.replace(builtins, 'open', self.checked_open)
        self.replace(io, 'open', self.checked_open)
        for key in ('Popen', 'run', 'call', 'check_call', 'check_output'):
            self.replace(subprocess, key, self.denied_process)
        for key in ('system', 'popen', 'fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'execv', 'execve',
                    'execvp', 'execvpe', 'execl', 'execle', 'execlp', 'execlpe'):
            if hasattr(os, key):
                self.replace(os, key, self.denied_process)
        self.replace(socket, 'socket', self.denied_network)
        self.replace(socket, 'create_connection', self.denied_network)
        self.replace(http.client.HTTPConnection, 'connect', self.denied_network)
        self.replace(http.client.HTTPSConnection, 'connect', self.denied_network)
        tape = self.tape
        def replay_open(opener, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
            return tape.open(opener, fullurl, data, timeout)
        self.replace(urllib.request.OpenerDirector, 'open', replay_open)
        return self

    def __exit__(self, kind, primary, traceback):
        for (owner, key, original), (_, _, installed) in zip(reversed(self.originals), reversed(self.installed)):
            if getattr(owner, key) is not installed:
                self.cleanup_errors.append({'attribute': key, 'reason': 'owned replay guard was replaced'})
                continue
            setattr(owner, key, original)
        if primary is None:
            require(not self.cleanup_errors and not self.process_attempts and not self.network_attempts,
                    'pure main attempted execution/network or changed an owned guard')
        return False


def source_snapshot(ctx, needed):
    paths = set(ctx.qa_source_authority['admitted_repository_python_sources']) | set(needed)
    for receipt in ctx.qa_source_authority['numbered_receipts'].values():
        raw = ctx.sources.read(receipt['path'])
        require(sha(raw) == receipt['raw_sha256'], 'numbered source receipt differs before replay')
        document = parse(raw)
        paths.add(receipt['path'])
        require(type(document.get('files')) is dict, 'complete numbered source file map required')
        paths.update(document['files'])
    return {path: ctx.sources.read(path) for path in sorted(paths)}


def arguments(owner, collector, ctx, capture, out):
    roots = {key: Path(value) for key, value in capture['input_roots'].items()}
    steps = owner.collector_steps(collector, ctx, Path(capture['services']['current']['candidate_dir']),
        Path(capture['original_run']), roots, CURRENT, BEFORE)
    require([row['step'] for row in capture['rows']] == [step['name'] for step in steps],
            'complete original/current step sequence differs before replay')
    return {step['name']: step for step in steps}


def replay_argv(step, capture, root, out):
    original_run = Path(capture['original_run'])
    root_map = {str(Path(value)): root / 'inputs' / role for role, value in capture['input_roots'].items()}
    result, index = [], 0
    values = [str(value) for value in step['argv'][2:]]
    while index < len(values):
        flag = values[index]; result.append(flag); index += 1
        if flag in {'--out', '--evidence'}:
            require(index < len(values), 'original output argument missing')
            value = Path(values[index]); require(original_run in value.parents, 'original output is outside Context run')
            result.append(str(out / value.relative_to(original_run))); index += 1
        elif flag in {'--public-run', '--legacy-run'}:
            require(index < len(values), 'explicit original input argument missing')
            value = Path(values[index])
            matches = [(Path(old), new) for old, new in root_map.items() if value == Path(old) or Path(old) in value.parents]
            require(len(matches) == 1, 'original input locator is not one exact declared role')
            old, new = matches[0]; result.append(str(new / value.relative_to(old))); index += 1
    return result


def locator(value, mappings):
    require(type(value) is str, 'explicit source-owned locator must be text')
    for path, token in sorted(mappings, key=lambda pair: -len(pair[0])):
        if value == path or value.startswith(path + '/'):
            return token + value[len(path):]
    raise ValueError('known report locator did not join its exact original/replay root')


def normalized(step, relative, value, mappings):
    value = copy.deepcopy(value)
    if step == 'python-replay':
        require(relative == 'python-replay.json' and type(value.get('seconds')) in {int, float}
                and math.isfinite(value['seconds']) and value['seconds'] >= 0, 'original elapsed-time field differs')
        value['seconds'] = '<elapsed-seconds>'
        for key in ('public_run', 'legacy_run'):
            value['inputs'][key] = locator(value['inputs'][key], mappings)
    elif step in {'target', 'bundles'} and relative == 'report.json':
        value['legacy_run'] = locator(value['legacy_run'], mappings)
    elif step == 'bundles' and relative == 'products/manifest.json':
        for item in value['products']:
            item['product'] = locator(item['product'], mappings)
    elif step == 'rt-diagnostics' and relative == 'report.json':
        value['negative_cases_path'] = locator(value['negative_cases_path'], mappings)
    return value


def report_raw(step, relative, raw, mappings, json_spans):
    value = parse(raw)
    adjusted = normalized(step, relative, value, mappings)
    pointers = []
    if step == 'python-replay':
        pointers = [('seconds',), ('inputs', 'public_run'), ('inputs', 'legacy_run')]
    elif step in {'target', 'bundles'} and relative == 'report.json':
        pointers = [('legacy_run',)]
    elif step == 'bundles' and relative == 'products/manifest.json':
        pointers = [('products', str(index), 'product') for index in range(len(value['products']))]
    elif step == 'rt-diagnostics' and relative == 'report.json':
        pointers = [('negative_cases_path',)]
    nodes = json_spans._Document(raw).nodes
    spans = []
    for pointer in pointers:
        require(pointer in nodes and nodes[pointer][0] in {'scalar', 'string'},
                'known report locator/time value token is missing')
        current = adjusted
        for key in pointer:
            current = current[int(key)] if type(current) is list else current[key]
        _, start, end, _ = nodes[pointer]
        spans.append((start, end, canonical(current)))
    result, previous = [], 0
    for start, end, replacement in sorted(spans):
        require(previous <= start < end <= len(raw), 'report path/time value spans overlap')
        result += [raw[previous:start], replacement]
        previous = end
    result.append(raw[previous:])
    return b''.join(result)


def compare_outputs(step, root, out, capture, repo, json_spans):
    _, _, _, relative, directory = step
    original = root / 'capture' / relative
    reproduced = out / relative
    def inventory(path):
        values = {}
        if not directory:
            return {relative: {'type': 'file', 'mode': stat.S_IMODE(path.stat().st_mode)}}
        require(path.is_dir() and not path.is_symlink(), 'complete regenerated output directory required')
        for member in path.rglob('*'):
            info = member.lstat()
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'output alias/special member refused')
            values[member.relative_to(path).as_posix()] = {
                'type': 'file' if stat.S_ISREG(info.st_mode) else 'directory', 'mode': stat.S_IMODE(info.st_mode)}
        return values
    original_inventory, replay_inventory = inventory(original), inventory(reproduced)
    require(original_inventory == replay_inventory, 'complete regenerated output names/types/modes differ')
    original_repo = str(capture['context']['repo'])
    mappings = [(str(Path(capture['original_run'])), '<run>'), (str(out), '<run>'),
                (original_repo, '<repo>'), (str(repo), '<repo>')]
    mappings += [(str(Path(old)), '<input:' + role + '>') for role, old in capture['input_roots'].items()]
    mappings += [(str(root / 'inputs' / role), '<input:' + role + '>') for role in capture['input_roots']]
    result = {}
    for member, info in original_inventory.items():
        if info['type'] != 'file':
            continue
        before = regular(original / member if directory else original)
        after = regular(reproduced / member if directory else reproduced)
        metadata = ((directory and member == 'report.json') or
                    (step[0] == 'bundles' and member == 'products/manifest.json') or not directory)
        if metadata:
            first = report_raw(step[0], member, before, mappings, json_spans)
            second = report_raw(step[0], member, after, mappings, json_spans)
            require(first == second, 'complete original assertion report differs: ' + step[0] + '/' + member)
            result[member] = {'comparison': 'exact-raw-bytes-except-explicit-path-or-time-value-spans',
                              'normalized_raw_sha256': sha(first), 'mode': info['mode']}
        else:
            require(before == after, 'complete original raw response/product bytes differ: ' + member)
            result[member] = {'comparison': 'exact-raw-bytes', 'sha256': sha(before),
                              'size': len(before), 'mode': info['mode']}
    return {'files': result, 'complete_node_inventory_sha256': sha(canonical(original_inventory)),
            'file_count': len(result)}


def restore_owned_attribute(owner, key, original, installed):
    require(getattr(owner, key) is installed, 'replay attribute replaced by later owner: ' + key)
    setattr(owner, key, original)


def verify(ctx, root, capture, *, out):
    needed = {SELF, OWNER, COLLECTOR, CHECKER, DIGESTS, INPUTS, RAW_JSON} | {row[2] for row in SPECS}
    require(ctx.qa_mode == 'native-qa-v1' and ctx.helper_revision == ctx.candidate_revision
            and ctx.qa_source_authority.get('source_authority_active') is True
            and needed <= set(ctx.qa_source_authority['admitted_repository_python_sources'])
            and capture.get('helper_revision') == capture.get('candidate_revision') == ctx.helper_revision,
            'exact native original-main replay source admission required')
    root, out = physical(root), physical(out, exists=False)
    runner_temp = physical(os.environ['RUNNER_TEMP'])
    require(runner_temp in out.parents and not out.exists() and root not in out.parents
            and out not in root.parents and Path(ctx.repo) not in out.parents,
            'fresh disjoint GitHub temporary replay output required')
    # Source/Git factory inspection occurs before native-command denial. Every
    # main sees only these already checked bytes/modules, never a lazy Git call.
    modules = {path: ctx.module('ts_main_replay_' + str(index), path)
               for index, path in enumerate(sorted(needed - {SELF}))}
    digest = modules[DIGESTS]
    for target in ('python', 'rust', 'typescript'):
        digest.authorities(ctx, target)
    source_bytes = source_snapshot(ctx, needed)
    view = FrozenContext(ctx, root, source_bytes, modules)
    expected = arguments(modules[OWNER], modules[COLLECTOR], ctx, capture, out)
    out.mkdir(mode=0o700)
    result = []
    failure = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
    try:
        for spec in SPECS:
            name, index, path, _, _ = spec
            step, recorded = expected[name], capture['rows'][index - 1]
            require(recorded['step'] == name and recorded['script'] == path
                    and recorded['logical_argv'] == [str(v) for v in step['argv']]
                    and Path(step['argv'][1]).as_posix() == (Path(ctx.repo) / path).as_posix(),
                    'original main/source/command binding differs')
            module, tape = modules[path], HttpTape(root / 'capture/raw-checkers' / f'{index:02d}')
            saved_argv = sys.argv
            owned_attributes, bundle_adapter_owned = [], False
            selected_argv = None
            stdout, stderr = io.StringIO(), io.StringIO()
            guards = MainGuards(view, root, capture, out, tape)
            try:
                if name == 'bundles':
                    modules[CHECKER].native_bundle_expectations(view, module)
                    bundle_adapter_owned = True
                elif name == 'python-replay':
                    old_difference, old_assets = module.asset_only_difference, module.NEW_ASSETS
                    module.NEW_ASSETS = {}
                    owned_attributes.append((module, 'NEW_ASSETS', old_assets, module.NEW_ASSETS))
                    def current_difference(before, after):
                        problem, _ = digest.historical_python_replay(view, before, after, old_difference)
                        return problem
                    module.asset_only_difference = current_difference
                    owned_attributes.append((module, 'asset_only_difference', old_difference, current_difference))
                selected_argv = [str(Path(ctx.repo) / path), *replay_argv(step, capture, root, out)]
                sys.argv = selected_argv
                with guards, redirect_stdout(stdout), redirect_stderr(stderr):
                    code = module.main()
                require(type(code) is int and code == 0, 'original pure HTTP checker main refused: ' + name)
                tape_proof = tape.finish()
                outputs = compare_outputs(spec, root, out, capture, ctx.repo, modules[RAW_JSON])
                result.append({'step': name, 'source_sha256': sha(source_bytes[path]),
                               'exit_code': code, **tape_proof, **outputs})
            except BaseException as error:
                remember_failure(failure, 'original_main_' + name, error)
            finally:
                actions = []
                if selected_argv is not None:
                    actions.append(('argv_restore', lambda: restore_owned_attribute(sys, 'argv', saved_argv, selected_argv)))
                if bundle_adapter_owned:
                    actions.append(('bundle_descriptor_restore', lambda: modules[CHECKER].close_native_bundle_expectations(module)))
                for owner, key, original, installed in reversed(owned_attributes):
                    actions.append(('attribute_restore_' + key, lambda owner=owner, key=key, original=original, installed=installed:
                                    restore_owned_attribute(owner, key, original, installed)))
                streams = out / 'replay-streams'
                actions += [('stream_directory', lambda: streams.mkdir(exist_ok=True)),
                    ('stdout_evidence', lambda: (streams / (name + '.stdout.raw')).write_bytes(stdout.getvalue().encode())),
                    ('stderr_evidence', lambda: (streams / (name + '.stderr.raw')).write_bytes(stderr.getvalue().encode())),
                    ('guards_evidence', lambda: (streams / (name + '.guards.json')).write_bytes(canonical({
                        'process_attempts': guards.process_attempts, 'network_attempts': guards.network_attempts,
                        'cleanup_errors': guards.cleanup_errors}) + b'\n'))]
                for role, action in actions:
                    try:
                        action()
                    except BaseException as error:
                        remember_failure(failure, name + '_' + role, error)
            raise_primary(failure)
    except BaseException as error:
        if failure['primary'] is not error:
            remember_failure(failure, 'original_main_replay', error)
        try:
            (out / 'first-failure.json').write_bytes(canonical({**failure['failure'],
                'secondary_failures': failure['secondary'], 'completed_steps': [row['step'] for row in result],
                'product_acceptance_passed': False}) + b'\n')
        except BaseException as evidence:
            remember_failure(failure, 'first_failure_evidence', evidence)
    finally:
        changed = []
        for path, raw in source_bytes.items():
            try:
                require(ctx.sources.read(path) == raw, 'source changed after original-main replay')
            except BaseException as error:
                changed.append({'path': path, 'error': type(error).__name__, 'reason': str(error)})
                remember_failure(failure, 'source_postcheck', error)
        try:
            (out / 'source-postcheck.json').write_bytes(canonical({'changed': changed,
                'source_sha256': {path: sha(raw) for path, raw in sorted(source_bytes.items())},
                'secondary_failures': failure['secondary']}) + b'\n')
        except BaseException as error:
            remember_failure(failure, 'source_postcheck_evidence', error)
    raise_primary(failure)
    replacements = ['ts.python_unchanged.1', 'ts.python_unchanged.2', 'ts.python_unchanged.3',
        'rust.amendment005.1', 'rust.amendment005.3', 'rust.python_unchanged.amendment_004',
        'ts.target_selection.3', 'ts.bundle_checks.0',
        *('ts.assemble_refusals.A' + str(index) for index in range(1, 13))]
    proof = {'version': 'som.typescript-regression-pure-original-main-replay.v1',
        'helper_revision': ctx.helper_revision, 'candidate_revision': ctx.candidate_revision,
        'source_snapshot_sha256': sha(canonical({path: sha(raw) for path, raw in sorted(source_bytes.items())})),
        'original_main_steps': result, 'replaces': replacements,
        'only_named_replacements_complete': True, 'whole_domain_complete': False,
        'not_covered_by_this_helper': ['native-rust-unchanged-exact-http-transport-adapter',
                                     'nested-rust-products-complete-raw-capture-and-replay'],
        'product_acceptance_passed': False}
    (out / 'proof.json').write_bytes(canonical(proof) + b'\n')
    return proof
