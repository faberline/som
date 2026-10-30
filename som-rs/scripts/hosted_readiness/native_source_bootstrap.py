"""Bind the actual workflow and finite admission helpers before repository exec.

The external anchor is the independently reviewed commit and GitHub workflow.
This module does not grant a QA loader lease or any product acceptance.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import importlib.util
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
from types import CellType, CodeType, FunctionType, MappingProxyType, ModuleType
from urllib.request import HTTPRedirectHandler, Request, build_opener

SELF = 'som-rs/scripts/hosted_readiness/native_source_bootstrap.py'
BASE = 'som-rs/scripts/hosted_readiness/'
GRAPH = BASE + 'same_candidate_graph.py'
AUTHORITY = BASE + 'native_qa_authority.py'
BOUND = BASE + 'bound_source.py'
READINESS = BASE + 'readiness.json'
WORKFLOW = '.github/workflows/rust-hosted-readiness.yml'
CALLED_WORKFLOW = '.github/workflows/rust-same-candidate-products.yml'
INPUT_ACTION = '.github/actions/same-candidate-inputs/action.yml'
REPOSITORY = 'faberline/som'
REPOSITORY_ID = 1390374878
M6_ENTRY = 'som-rs/scripts/react_output_acceptance/m6_native_entry.py'
GRAPH_ENTRY = BASE + 'native_graph_entry.py'
_GRAPH_NAME = '_som_bootstrap_same_candidate_graph'
_AUTHORITY_NAME = '_som_same_candidate_native_authority'
CORE = BASE + 'native_active_controls.py'
_CORE_NAME = '_som_native_active_controls'
_TRUSTED_ENTRY_NAME = '_som_native_trusted_bootstrap_entry'
_CALLED_JOBS = ('bind', 'current-python-companions', 'products', 'product-join',
                'required-callbacks', 'whole-graph-join')
_ROOT_JOBS = ('experimental-native-compile', 'current-product-rust-tools')
_ENTRY_ROLES = MappingProxyType({
    'experimental-native-compile': (BASE + 'product_runtime/current_candidate_layout.py',),
    'current-product-rust-tools': (BASE + 'product_runtime/qualify_rust_tools.py',),
    'bind': (GRAPH_ENTRY,),
    'current-python-companions': (BASE + 'run_current_python_companions.py',),
    'products': (BASE + 'run_full_products.py',),
    'product-join': (BASE + 'run_full_products.py',),
    'required-callbacks': (BASE + 'run_full_products.py', BASE + 'run_candidate_semantic_gates.py',
                           BASE + 'run_current_candidate_gate.py', BASE + 'run_same_candidate_contracts.py', M6_ENTRY,
                           BASE + 'run_typescript_input_consumer.py'),
    'whole-graph-join': (GRAPH_ENTRY,),
})
_ADMISSION_IMPORTS = MappingProxyType({
    SELF: frozenset(('__future__', 'argparse', 'ast', 'builtins', 'hashlib', 'importlib.util',
                     'importlib.abc', 'importlib.machinery', 'json', 'os', 'pathlib', 're',
                     'stat', 'subprocess', 'sys', 'threading', 'types', 'urllib.request')),
    CORE: frozenset(('__future__', 'ast', 'builtins', 'hashlib', 'importlib.abc',
                     'importlib.machinery', 'importlib.util', 'json', 'os', 'pathlib', 're',
                     'sys', 'threading', 'types')),
    GRAPH: frozenset(('__future__', 'argparse', 'hashlib', 'json', 'os', 'pathlib', 're',
                      'stat', 'subprocess', 'sys', 'types', 'urllib.request')),
    AUTHORITY: frozenset(('__future__', 'hashlib', 'ast', 'importlib.abc', 'importlib.machinery',
                          'importlib.util', 'pathlib', 'sys', 'json', 'os', 're')),
})
_MAX_SOURCE = 32 * 1024**2
_LEASE_KEY = object()
_PROCESS_KEYS = ('GITHUB_ACTIONS', 'GITHUB_REPOSITORY', 'GITHUB_REPOSITORY_ID',
                 'GITHUB_SERVER_URL', 'GITHUB_API_URL', 'GITHUB_SHA', 'GITHUB_WORKFLOW_SHA',
                 'GITHUB_EVENT_NAME', 'GITHUB_REF', 'GITHUB_WORKFLOW_REF', 'GITHUB_JOB',
                 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'RUNNER_NAME', 'RUNNER_ENVIRONMENT', 'RUNNER_OS')


def _require(value, message):
    if not value:
        raise ValueError(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _parse(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            _require(key not in result, 'duplicate bootstrap JSON member')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite bootstrap JSON')))


def _relative(value):
    _require(type(value) is str and value and not value.startswith('/') and '\\' not in value
             and all(part not in {'', '.', '..'} for part in value.split('/'))
             and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'unsafe bootstrap source path')
    return value


def _physical_directory(path):
    for item in (path, *path.parents):
        info = item.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'linked or nonphysical source parent')
        _require(info.st_mode & 0o444 and info.st_mode & 0o111 and os.access(item, os.R_OK | os.X_OK),
                 'unreadable source parent')


class _GitSnapshot:
    """A complete Git tree inventory and exact physical bytes for its fixed pins."""
    def __init__(self, repo, revision):
        self.root = Path(repo).absolute()
        _require('..' not in self.root.parts, 'noncanonical repository path')
        _physical_directory(self.root)
        self.revision = revision
        _require(self.git('rev-parse', '--show-toplevel').decode().strip() == str(self.root), 'Git toplevel differs')
        _require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision, 'bootstrap HEAD differs')
        self.tree_raw = self.git('ls-tree', '-r', '-z', revision)
        entries = {}
        for row in self.tree_raw.split(b'\0'):
            if not row:
                continue
            fields, tab, raw_name = row.partition(b'\t')
            parts = fields.split()
            _require(tab and len(parts) == 3, 'invalid complete Git tree entry')
            mode, kind, oid = parts
            name = _relative(raw_name.decode('utf-8', 'strict'))
            _require(name not in entries and re.fullmatch(b'[0-9a-f]{40}', oid), 'duplicate or invalid Git tree entry')
            entries[name] = (mode, kind, oid)
        _require(entries, 'empty complete Git source inventory')
        self.entries = MappingProxyType(entries)
        self.pins = {}

    def git(self, *arguments):
        return subprocess.check_output(
            ['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *arguments],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
            stderr=subprocess.DEVNULL, timeout=30)

    def read(self, name):
        name = _relative(name)
        _require(name in self.entries, 'required bootstrap member is absent from Git')
        mode, kind, oid = self.entries[name]
        _require(mode in {b'100644', b'100755'} and kind == b'blob', 'regular Git bootstrap source required')
        path = self.root / name
        _physical_directory(path.parent)
        before = path.lstat()
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                 and before.st_size <= _MAX_SOURCE, 'bounded unlinked bootstrap source required')
        expected_mode = 0o755 if mode == b'100755' else 0o644
        _require(stat.S_IMODE(before.st_mode) == expected_mode, 'working bootstrap source mode differs from Git')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(fd)
            _require((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode, opened.st_nlink)
                     == (before.st_dev, before.st_ino, before.st_size, before.st_mode, before.st_nlink),
                     'bootstrap source changed before read')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(_MAX_SOURCE + 1)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        final = path.lstat()
        fingerprint = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mode,
                                    info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)
        _require(fingerprint(before) == fingerprint(after) == fingerprint(final)
                 and len(raw) == before.st_size, 'bootstrap source changed during read')
        git_raw = self.git('cat-file', 'blob', oid.decode())
        _require(len(raw) <= _MAX_SOURCE and raw == git_raw
                 and hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == oid.decode(),
                 'working bootstrap bytes differ from exact Git blob')
        if name.endswith('.py'):
            _require(not any(side.exists() or side.is_symlink()
                             for side in (path.with_suffix('.pyc'), path.with_suffix('.pyo'))),
                     'bootstrap adjacent bytecode refused')
            cache = path.parent / '__pycache__'
            if cache.exists() or cache.is_symlink():
                _physical_directory(cache)
                _require(not any(item.name.startswith(path.stem + '.') and item.suffix in {'.pyc', '.pyo'}
                                 for item in cache.iterdir()), 'bootstrap cache bytecode refused')
        pin = (oid.decode(), _sha(raw), expected_mode, fingerprint(before))
        _require(name not in self.pins or self.pins[name] == pin, 'bootstrap initial source pin changed')
        self.pins[name] = pin
        return raw

    def verify(self):
        _require(self.git('rev-parse', '--show-toplevel').decode().strip() == str(self.root)
                 and self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == self.revision,
                 'bootstrap repository identity changed')
        _require(self.git('ls-tree', '-r', '-z', self.revision) == self.tree_raw, 'complete Git inventory changed')
        for name in tuple(self.pins):
            self.read(name)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('bootstrap metadata redirect refused')


def _github_json(suffix):
    _require(re.fullmatch(r'/actions/(?:runs/[1-9][0-9]*(?:/attempts/[1-9][0-9]*/jobs\?per_page=100&page=[1-9][0-9]*)?|workflows/[1-9][0-9]*)', suffix),
             'unlisted bootstrap GitHub API route')
    token = os.environ['SOM_DEPENDENCY_ACTIONS_TOKEN']
    _require(type(token) is str and token and '\n' not in token and '\r' not in token, 'actual Actions token required')
    request = Request('https://api.github.com/repos/' + REPOSITORY + suffix, headers={
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'som-native-source-bootstrap', 'Authorization': 'Bearer ' + token})
    with build_opener(_NoRedirect()).open(request, timeout=30) as response:
        _require(response.status == 200, 'bootstrap metadata HTTP status differs')
        raw = response.read(4 * 1024**2 + 1)
    _require(len(raw) <= 4 * 1024**2, 'bootstrap metadata exceeds bound')
    return _parse(raw)


def _job_display_name(workflow, job):
    """Read one fixed job's literal name from the already checked source."""
    sections = list(re.finditer(r'(?m)^jobs:$', workflow))
    _require(len(sections) == 1, 'one exact workflow jobs section required')
    jobs = workflow[sections[0].end():]
    starts = list(re.finditer(r'(?m)^  ([A-Za-z0-9_-]+):$', jobs))
    selected = [index for index, match in enumerate(starts) if match.group(1) == job]
    _require(len(selected) == 1, 'one exact source job ID required')
    index = selected[0]
    end = starts[index + 1].start() if index + 1 < len(starts) else len(jobs)
    block = jobs[starts[index].end():end]
    names = re.findall(r'(?m)^    name: (.+)$', block)
    _require(len(names) <= 1, 'ambiguous source job display name')
    display = names[0].strip() if names else job
    # Current fixed roles use plain ASCII literal names. Dynamic YAML names,
    # tags, aliases and quoted/multiline variants need a separate source review.
    _require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._()/\-]*', display),
             'unsupported source job display-name expression')
    properties = list(re.finditer(r'(?m)^    ([A-Za-z0-9_-]+):(.*)$', block))
    strategies = [position for position, match in enumerate(properties) if match.group(1) == 'strategy']
    _require(len(strategies) <= 1, 'ambiguous source job strategy')
    strategy = ''
    if strategies:
        position = strategies[0]
        header = properties[position]
        _require(not header.group(2).strip(), 'source job strategy must be an exact block')
        strategy_end = properties[position + 1].start() if position + 1 < len(properties) else len(block)
        strategy = block[header.end():strategy_end]
    matrices = re.findall(r'(?m)^      matrix:.*$', strategy)
    _require(len(matrices) <= 1, 'ambiguous source job matrix')
    return display, bool(matrices)


def _workflow_binding(snapshot, candidate, entry):
    env = os.environ
    _require(env.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
             and env.get('RUNNER_ENVIRONMENT') == 'github-hosted' and env.get('RUNNER_OS') == 'Linux',
             'actual GitHub hosted Linux runner required')
    _require(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,
             'isolated no-site no-bytecode Python required')
    _require(env.get('GITHUB_REPOSITORY') == REPOSITORY
             and env.get('GITHUB_REPOSITORY_ID') == str(REPOSITORY_ID)
             and env.get('GITHUB_SERVER_URL') == 'https://github.com'
             and env.get('GITHUB_API_URL') == 'https://api.github.com'
             and env.get('GITHUB_SHA') == env.get('GITHUB_WORKFLOW_SHA') == candidate
             and env.get('GITHUB_EVENT_NAME') in {'push', 'workflow_dispatch'}, 'actual formal source SHA/event differs')
    ref = env['GITHUB_REF']
    _require(re.fullmatch(r'refs/(?:heads|tags)/[^\s@]+', ref)
             and env.get('GITHUB_WORKFLOW_REF') == REPOSITORY + '/' + WORKFLOW + '@' + ref,
             'actual root workflow/ref differs')
    job = env['GITHUB_JOB']
    _require(job in _ROOT_JOBS + _CALLED_JOBS, 'unlisted formal workflow job')
    _require(entry == SELF or entry in _ENTRY_ROLES[job], 'unlisted bootstrap entry/job pair')
    _require(re.fullmatch('[1-9][0-9]*', env['GITHUB_RUN_ID'])
             and re.fullmatch('[1-9][0-9]*', env['GITHUB_RUN_ATTEMPT']), 'actual positive run/attempt required')
    run_id, attempt = int(env['GITHUB_RUN_ID']), int(env['GITHUB_RUN_ATTEMPT'])
    root_workflow = snapshot.read(WORKFLOW).decode('utf-8')
    called_workflow = snapshot.read(CALLED_WORKFLOW).decode('utf-8')
    snapshot.read(INPUT_ACTION)
    _require(re.search(r'(?m)^  same-candidate-full-products:\n', root_workflow)
             and re.search(r'(?m)^    uses: \./' + re.escape(CALLED_WORKFLOW) + r'$', root_workflow),
             'exact reusable workflow call chain is absent')
    selected_workflow = root_workflow if job in _ROOT_JOBS else called_workflow
    _require(re.search(r'(?m)^  ' + re.escape(job) + r':$', selected_workflow), 'actual job absent from exact workflow source')
    record = _github_json('/actions/runs/' + str(run_id))
    _require(type(record['id']) is int and record['id'] == run_id and record['head_sha'] == candidate
             and type(record['run_attempt']) is int and record['run_attempt'] == attempt
             and record['event'] == env['GITHUB_EVENT_NAME'] and record['repository']['full_name'] == REPOSITORY
             and type(record['repository']['id']) is int and record['repository']['id'] == REPOSITORY_ID
             and record['path'] == WORKFLOW and record['head_branch'] == ref.split('/', 2)[2]
             and record['status'] == 'in_progress', 'live run source/workflow/event/ref/attempt differs')
    workflow_id = record['workflow_id']
    _require(type(workflow_id) is int and workflow_id > 0, 'actual workflow id required')
    workflow_record = _github_json('/actions/workflows/' + str(workflow_id))
    _require(workflow_record['id'] == workflow_id and workflow_record['path'] == WORKFLOW,
             'live root workflow identity differs')
    if job in _CALLED_JOBS:
        matches = [row for row in record.get('referenced_workflows', [])
                   if row.get('path', '').split('@', 1)[0] == REPOSITORY + '/' + CALLED_WORKFLOW]
        _require(len(matches) == 1 and matches[0].get('sha') == candidate
                 and ('ref' not in matches[0] or matches[0]['ref'] == ref),
                 'live called workflow source/ref differs')
    observed, total = [], None
    for page in range(1, 101):
        document = _github_json('/actions/runs/' + str(run_id) + '/attempts/' + str(attempt)
                                + '/jobs?per_page=100&page=' + str(page))
        count = document['total_count']
        _require(type(count) is int and 0 < count <= 10000 and (total is None or total == count),
                 'live job pagination count differs')
        total = count
        rows = document['jobs']
        _require(type(rows) is list and len(rows) <= 100, 'bounded live jobs page required')
        observed.extend(rows)
        if page * 100 >= total:
            break
    _require(len(observed) == total
             and all(type(row['id']) is int and row['id'] > 0 for row in observed)
             and len({row['id'] for row in observed}) == total,
             'complete live jobs identity universe differs')
    display, matrix = _job_display_name(selected_workflow, job)
    _require(matrix == (job in ('products', 'required-callbacks')), 'fixed source job matrix shape differs')
    if job in _ROOT_JOBS:
        job_prefix = display
    else:
        caller_display, caller_matrix = _job_display_name(root_workflow, 'same-candidate-full-products')
        _require(not caller_matrix, 'unlisted reusable caller matrix')
        job_prefix = caller_display + ' / ' + display
    runner_name = env['RUNNER_NAME']
    suffix = r'(?: \([^\r\n]+\))?' if matrix else ''
    matches = [row for row in observed if re.fullmatch(re.escape(job_prefix) + suffix, row['name'])
               and row.get('runner_name') == runner_name and row.get('status') == 'in_progress']
    _require(len(matches) == 1 and type(matches[0]['run_id']) is int and matches[0]['run_id'] == run_id
             and matches[0]['head_sha'] == candidate
             and ('run_attempt' not in matches[0] or (type(matches[0]['run_attempt']) is int
                                                      and matches[0]['run_attempt'] == attempt)),
             'actual runner/job is missing, ambiguous or from another source')
    return {'repository': REPOSITORY, 'run_id': run_id, 'run_attempt': attempt, 'root_workflow': WORKFLOW,
            'called_workflow': CALLED_WORKFLOW if job in _CALLED_JOBS else None,
            'github_workflow_ref': env['GITHUB_WORKFLOW_REF'], 'github_workflow_sha': env['GITHUB_WORKFLOW_SHA'],
            'job': job, 'job_display_name': display, 'job_api_name': matches[0]['name'],
            'job_id': matches[0]['id'], 'runner_name': runner_name,
            'process_environment': tuple((key, env[key]) for key in _PROCESS_KEYS),
            'live_run': record, 'live_workflow': workflow_record, 'live_job': matches[0]}


def _code_closure(code):
    result = [code]
    for item in code.co_consts:
        if isinstance(item, CodeType):
            result.extend(_code_closure(item))
    return tuple(result)


def _admission_code(path, raw, filename):
    tree = ast.parse(raw, filename=filename)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module == '_som_native_active_controls_binding':
                _require(path == GRAPH and node.level == 0 and len(node.names) == 1
                         and node.names[0].name == 'controls' and node.names[0].asname is None
                         and any(any(child is node for child in ast.walk(function))
                                 for function in ast.walk(tree)
                                 if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))),
                         'only the fixed deferred Graph function control import is available')
                # The admission-only importer below still refuses this name.
                # The fresh active graph.controller executes this helper later.
                continue
            _require(node.level == 0 and node.module is not None, 'relative admission-helper import refused')
            imports.add(node.module)
    _require(imports == _ADMISSION_IMPORTS[path], 'finite admission-only static import closure differs')
    _require(all(name.split('.')[0] in sys.stdlib_module_names for name in imports), 'repository admission import refused')
    # All these stdlib dependencies were imported by the initial trusted entry.
    # Never consult a repository search path to satisfy a helper's import.
    for name in sorted(imports):
        _require(name in sys.modules, 'admission stdlib dependency was not preloaded by bootstrap')
        module = sys.modules[name]
        origin = getattr(module, '__file__', None)
        if origin is not None:
            _require(Path(origin).absolute().is_relative_to(Path(sys.base_prefix).absolute())
                     and not str(origin).endswith(('.pyc', '.pyo')), 'nonstdlib admission dependency origin')
    return compile(tree, filename, 'exec', dont_inherit=True)


def _admission_builtins(path):
    allowed = _ADMISSION_IMPORTS[path]
    captured = {name: sys.modules[name] for name in allowed}
    captured.update({name.split('.')[0]: sys.modules[name.split('.')[0]] for name in allowed})

    def checked_import(name, globals=None, locals=None, fromlist=(), level=0):
        _require(level == 0 and name in allowed and sys.modules.get(name) is captured[name],
                 'only the captured fixed stdlib import is available to admission helpers')
        result = captured[name if fromlist else name.split('.')[0]]
        _require(sys.modules.get(result.__name__) is result, 'admission stdlib module ownership changed')
        return result

    values = dict(builtins.__dict__)
    values['__import__'] = checked_import
    return MappingProxyType(values)


def _preserve_primary(primary, phase, secondary):
    try:
        existing = BaseException.__getattribute__(primary, '__som_bootstrap_secondary_errors__')
    except AttributeError:
        existing = ()
    notes = list(existing)
    notes.append({'phase': phase, 'exception': secondary})
    BaseException.__setattr__(primary, '__som_bootstrap_secondary_errors__', tuple(notes))


def _namespace_state(namespace, *, _manual_transition=None):
    """Keep actual invocation identities and content without following cycles."""
    _require(all(type(key) is str for key in namespace), 'bootstrap namespace key type changed')
    owner = namespace['__name__']
    _require(type(owner) is str, 'bootstrap namespace owner type changed')
    seen, references = set(), []
    if _manual_transition is not None:
        _manual_transition.verify()

    def state(value):
        kind = type(value)
        if any(kind is accepted for accepted in (str, bytes, int, float, bool, type(None))):
            return ('atom', id(kind), value)
        identity = id(value)
        if identity in seen:
            return ('reference', id(kind), identity)
        seen.add(identity)
        # Keep every object alive. An old binding cannot disappear and have its
        # identity reused while the initial invocation snapshot is authoritative.
        references.append(value)
        if kind is list or kind is tuple:
            return ('sequence', id(kind), identity, tuple(state(item) for item in value))
        if kind is set or kind is frozenset:
            return ('set', id(kind), identity, frozenset(state(item) for item in value))
        if kind is dict:
            return ('dict', identity, tuple((state(key), state(item)) for key, item in value.items()))
        if kind is CellType:
            try:
                contents = value.cell_contents
            except ValueError:
                return ('cell', identity, 'empty')
            return ('cell', identity, state(contents))
        if kind is FunctionType:
            return ('function', identity, id(value.__code__), id(value.__globals__),
                    state(value.__defaults__), state(value.__kwdefaults__), state(value.__closure__))
        if kind is type or kind is type(importlib.abc.MetaPathFinder):
            module_name = type.__getattribute__(value, '__module__')
            _require(type(module_name) is str, 'bootstrap class owner type changed')
            if module_name == owner:
                attributes = type.__getattribute__(value, '__dict__')
                if _manual_transition is not None and value is _manual_transition.owner_class:
                    attributes = dict(attributes)
                    _require(attributes.get('exec_module') is _manual_transition.installed,
                             'actual owned manual-loader method changed')
                    attributes['exec_module'] = _manual_transition.original
                return ('class', identity, tuple(sorted((key, state(item)) for key, item in attributes.items())))
        if kind is classmethod or kind is staticmethod:
            return ('descriptor', id(kind), identity, state(value.__func__))
        if kind is property:
            return ('property', identity, state(value.fget), state(value.fset), state(value.fdel))
        # No equality, repr, iterator, or attribute hook on a foreign object is
        # called while checking bindings. Fingerprints contain built-in data.
        return ('opaque', id(kind), identity)

    return {key: state(value) for key, value in namespace.items()}, tuple(references)


class _ManualTransition:
    """One exact owned classmethod transition, never a blanket fingerprint waiver."""
    __slots__ = ('transfer', 'owner_class', 'original', 'installed', 'domain', 'state', 'references')
    def __init__(self, transfer, installed, domain):
        self.transfer, self.domain = transfer, domain
        self.owner_class = transfer.authority.BoundManualSourceLoader
        self.original = self.owner_class.exec_module
        self.installed = installed
        self.state, self.references = self.binding_state()
    def binding_state(self):
        owner = self.transfer._owner
        record = owner._records[_CORE_NAME]
        _require(type(self.installed) is FunctionType
                 and self.installed.__globals__ is record['globals']
                 and any(self.installed.__code__ is code for code in record['codes']),
                 'manual adapter did not come from actual pre-exec core code/globals')
        return _namespace_state({'__name__': _CORE_NAME, 'adapter': self.installed})
    def verify(self):
        _require(self.owner_class.exec_module is self.installed, 'owned manual-loader adapter was replaced')
        state, _ = self.binding_state()
        _require(state == self.state, 'actual manual adapter invocation bindings changed')


class _ActiveTransfer:
    """Opaque object created by the fresh pre-exec ledger-owned bootstrap source."""
    __slots__ = ('_owner',)
    def __init__(self, owner):
        initial = sys.modules.get('__main__')
        _require(type(initial) is ModuleType and type(owner) is initial.__dict__.get('_BootstrapLease')
                 and owner._initial_main is initial,
                 'actual initial bootstrap owner identity required')
        module = owner._modules.get(_TRUSTED_ENTRY_NAME)
        _require(module is not None and module.__dict__ is globals()
                 and module._ActiveTransfer is type(self) and owner._entry == SELF,
                 'fresh actual bootstrap handoff origin required')
        self._owner = owner
    @property
    def root(self): return self._owner._snapshot.root
    @property
    def revision(self): return self._owner._snapshot.revision
    @property
    def sources(self): return self._owner._admission[0]
    @property
    def authority(self): return self._owner._admission[1]
    @property
    def admission(self): return self._owner._admission[2]
    @property
    def source_paths(self): return tuple(self.sources.paths)
    @property
    def initial_main(self): return self._owner._initial_main
    def read(self, path):
        raw = self._owner._snapshot.read(path)
        _require(raw == self.sources.read(path), 'actual bootstrap/Graph source readers disagree')
        return raw
    def mode(self, path):
        self.read(path)
        return self._owner._snapshot.pins[path][2]
    def seed(self, name):
        owner = self._owner
        record = owner._records[name]
        _require(record['executed'] and owner._modules[name].__dict__ is record['globals'],
                 'actual pre-exec bootstrap seed record missing')
        return {**record, 'module': owner._modules[name]}
    def secondary(self, primary, phase, secondary):
        _preserve_primary(primary, phase, secondary)
    def verify_sources(self):
        self._owner._verify_seed_sources()
        self.sources.verify()
    def verify_entry_source(self, path):
        _require(path in _ENTRY_ROLES[self._owner._binding['job']],
                 'QA entry source is not in the fixed reviewed job entry set')
    def verify_factory_call(self, frame, module):
        owner = self._owner
        method = type(owner).activate
        _require(owner._transfer is self and owner._active is None
                 and frame.f_code is method.__code__ and frame.f_globals is method.__globals__
                 and owner._modules.get(_CORE_NAME) is module
                 and owner._records[_CORE_NAME]['globals'] is module.__dict__,
                 'actual private bootstrap-to-core handoff differs')
    def verify_operational_policy(self, *paths):
        expected = None
        for _, _, leaf, _ in self.authority.AUTHORITY.values():
            policy = _parse(self.read('som-rs/contracts/' + leaf))
            admission = policy['source_admission']
            _require(_sha(self.authority.canonical(admission)) == self.admission['qa_source_admission_sha256'],
                     'actual numbered source admission differs from opaque handoff')
            current = admission['operational_sources']
            _require(expected is None or current == expected, 'numbered operational source maps disagree')
            expected = current
        for path in paths:
            _require(expected.get(path) == _sha(self.read(path)), 'active schema/policy is outside sealed operational sources')
    def install_manual_adapter(self, installed, domain):
        owner = self._owner
        core = owner._modules[_CORE_NAME]
        frame = sys._getframe(1)
        _require(type(domain) is core._Domain and domain.transfer is self
                 and frame.f_globals is core.__dict__ and frame.f_code.co_qualname == '_Domain.install'
                 and any(frame.f_code is code for code in owner._records[_CORE_NAME]['codes']),
                 'only actual source-owned core installation may change the manual adapter')
        _require(owner._manual_transition is None, 'manual adapter transition already active')
        transition = _ManualTransition(self, installed, domain)
        transition.owner_class.exec_module = installed
        owner._manual_transition = transition
        transition.verify()
    def verify_manual_adapter(self, installed, domain):
        transition = self._owner._manual_transition
        _require(transition is not None and transition.installed is installed and transition.domain is domain,
                 'manual adapter transition has a foreign owner')
        transition.verify()
    def restore_manual_adapter(self, installed, domain):
        transition = self._owner._manual_transition
        if transition is None:
            _require(installed is None, 'owned manual adapter transition was lost')
            return
        _require(transition.installed is installed and transition.domain is domain, 'foreign manual adapter close')
        transition.verify()
        transition.owner_class.exec_module = transition.original
        self._owner._manual_transition = None


def _trusted_run(owner, role, argv):
    """This body runs only from the SELF module registered before its exec."""
    _require(owner._modules.get(_TRUSTED_ENTRY_NAME).__dict__ is globals(), 'fresh trusted entry namespace required')
    graph = owner.load_admission_module('same-candidate-graph')
    sources, authority, admission = graph.admit_source(owner._snapshot.root, owner._snapshot.revision, owner._snapshot.revision)
    controls = owner.activate(sources, admission)
    return controls.execute_entry(role, argv)


class _AdmissionGraph:
    """Expose source admission only; never expose Graph's general raw_module."""
    __slots__ = ('_owner',)

    def __init__(self, owner, key):
        _require(key is _LEASE_KEY, 'checked bootstrap graph construction required')
        self._owner = owner

    def admit_source(self, repo, helper_revision, candidate_revision):
        owner = self._owner
        owner.verify()
        _require(Path(repo).absolute() == owner._snapshot.root
                 and helper_revision == candidate_revision == owner._snapshot.revision,
                 'admission cannot replace bootstrap source identity')
        primary = None
        try:
            result = owner._modules[_GRAPH_NAME].admit_source(repo, helper_revision, candidate_revision)
            sources, authority, admission = result
            _require(authority is owner._modules[_AUTHORITY_NAME], 'Graph replaced the prechecked authority module')
            owner._admission = (sources, authority, admission)
            return result
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                owner.verify()
            except BaseException as secondary:
                if primary is None:
                    raise
                _preserve_primary(primary, 'post-admission-source-check', secondary)


class _BootstrapLease:
    __slots__ = ('_snapshot', '_binding', '_entry', '_meta_path', '_spec', '_modules',
                 '_records', '_graph', '_admission', '_closed', '_active', '_transfer',
                 '_manual_transition', '_initial_main')

    def __init__(self, snapshot, binding, entry, controls, key):
        _require(key is _LEASE_KEY, 'checked bootstrap construction required')
        self._snapshot, self._binding, self._entry = snapshot, binding, entry
        self._meta_path, self._spec = controls
        self._modules, self._records = {}, {}
        self._graph, self._admission, self._closed = None, None, False
        self._active, self._transfer, self._manual_transition = None, None, None
        self._initial_main = sys.modules['__main__']

    def verify(self):
        self._verify_seed_sources()
        if self._active is None:
            self._verify_initial_controls()
        else:
            self._active._domain.check_guard()

    def _verify_initial_controls(self):
        _require(len(sys.meta_path) == len(self._meta_path)
                 and all(actual is expected for actual, expected in zip(sys.meta_path, self._meta_path))
                 and importlib.util.spec_from_file_location is self._spec, 'existing finder/spec ownership changed')

    def _verify_seed_sources(self):
        _require(not self._closed, 'bootstrap lease is closed')
        _require(tuple((key, os.environ.get(key)) for key in _PROCESS_KEYS) == self._binding['process_environment']
                 and sys.platform == 'linux' and sys.flags.isolated and sys.flags.no_site
                 and sys.flags.dont_write_bytecode, 'bootstrap initial process identity changed')
        self._snapshot.verify()
        for name, record in self._records.items():
            module = self._modules[name]
            _require(sys.modules.get(name) is module and module.__dict__ is record['globals'],
                     'bootstrap module namespace ownership changed')
            if record['executed']:
                current_state, _ = _namespace_state(module.__dict__, _manual_transition=(
                    self._manual_transition if record['path'] == AUTHORITY else None))
                _require(current_state == record['namespace_state'],
                         'bootstrap source-owned namespace state changed')
                for key, expected in record['callables'].items():
                    value = module.__dict__.get(key)
                    _require(value is expected and value.__globals__ is record['globals']
                             and any(value.__code__ is code for code in record['codes']),
                             'bootstrap function code/globals ownership changed')

    def load_admission_module(self, role):
        _require(type(role) is str and role == 'same-candidate-graph', 'only the fixed source-admission role is available')
        self.verify()
        if self._graph is not None:
            return self._graph
        # Read and compile the whole finite group before the first helper exec.
        compiled = []
        for name, path in ((_AUTHORITY_NAME, AUTHORITY), (_GRAPH_NAME, GRAPH)):
            _require(name not in sys.modules, 'preloaded admission helper refused')
            raw = self._snapshot.read(path)
            compiled.append((name, path, raw, _admission_code(path, raw, str(self._snapshot.root / path))))
        self.verify()
        try:
            for name, path, raw, code in compiled:
                module = ModuleType(name)
                module.__file__, module.__cached__, module.__package__ = str(self._snapshot.root / path), None, ''
                module.__builtins__ = _admission_builtins(path)
                # This compatibility marker is never used as bootstrap proof.
                module.__som_raw_sha256__ = _sha(raw)
                record = {'code': code, 'codes': _code_closure(code), 'globals': module.__dict__,
                          'path': path, 'executed': False, 'callables': {}}
                self._modules[name], self._records[name] = module, record
                sys.modules[name] = module
                # The real CodeType and exact module globals are registered now.
                exec(code, module.__dict__)
                record['executed'] = True
                record['callables'] = {key: value for key, value in module.__dict__.items()
                                       if isinstance(value, FunctionType) and value.__globals__ is module.__dict__}
                record['namespace_state'], record['invocation_references'] = _namespace_state(module.__dict__)
                self.verify()
            self._graph = _AdmissionGraph(self, _LEASE_KEY)
            return self._graph
        except BaseException as primary:
            try:
                self._remove_owned_modules()
            except BaseException as secondary:
                _preserve_primary(primary, 'admission-module-failure-cleanup', secondary)
            raise

    def activate(self, sources, admission):
        self.verify()
        _require(self._admission is not None and sources is self._admission[0] and admission is self._admission[2],
                 'only this bootstrap actual source-admission result can reach the bridge')
        _require(self._entry == SELF and _TRUSTED_ENTRY_NAME in self._records,
                 'active controls need a fresh ledger-owned trusted entry before QA')
        if self._active is not None:
            return self._active.borrow()
        sources.verify()
        authority = self._admission[1]
        actual = authority.verify(sources.read, mode='native-qa-v1', source_paths=sources.paths,
            helper_revision=self._snapshot.revision, candidate_revision=self._snapshot.revision)
        _require(authority.typed_equal(actual, admission) and actual['source_authority_active'] is True
                 and all(actual[field] is False for field in ('product_acceptance_passed',
                    'compiler_acceptance_passed', 'whole_acceptance_complete')),
                 'actual native source authority changed before active handoff')
        module = self._load_activation_module()
        self._transfer = self._modules[_TRUSTED_ENTRY_NAME]._ActiveTransfer(self)
        self._active = module._activate_from_bootstrap(self._transfer)
        self.verify()
        return self._active

    def _load_activation_module(self):
        if _CORE_NAME in self._modules:
            self.verify()
            return self._modules[_CORE_NAME]
        _require(_CORE_NAME not in sys.modules, 'preloaded active core refused')
        raw = self._snapshot.read(CORE)
        _require(raw == self._admission[0].read(CORE), 'active core source readers disagree')
        code = _admission_code(CORE, raw, str(self._snapshot.root / CORE))
        module = ModuleType(_CORE_NAME)
        module.__file__, module.__package__, module.__cached__ = str(self._snapshot.root / CORE), '', None
        module.__builtins__, module.__som_raw_sha256__ = _admission_builtins(CORE), _sha(raw)
        record = {'code': code, 'codes': _code_closure(code), 'globals': module.__dict__,
                  'path': CORE, 'executed': False, 'callables': {}}
        self._modules[_CORE_NAME], self._records[_CORE_NAME] = module, record
        sys.modules[_CORE_NAME] = module
        try:
            exec(code, module.__dict__)
            record['executed'] = True
            record['callables'] = {name: value for name, value in module.__dict__.items()
                                   if type(value) is FunctionType and value.__globals__ is module.__dict__}
            record['namespace_state'], record['invocation_references'] = _namespace_state(module.__dict__)
            self.verify()
            return module
        except BaseException:
            if sys.modules.get(_CORE_NAME) is module:
                del sys.modules[_CORE_NAME]
            self._modules.pop(_CORE_NAME, None); self._records.pop(_CORE_NAME, None)
            raise

    def execute_entry(self, role, argv):
        """Seed only: fresh SELF body enters the ledger before the QA entry."""
        self.verify()
        _require(self._entry == SELF and _TRUSTED_ENTRY_NAME not in self._modules
                 and _TRUSTED_ENTRY_NAME not in sys.modules, 'retired or repeated trusted initial entry')
        raw = self._snapshot.read(SELF)
        code = _admission_code(SELF, raw, str(self._snapshot.root / SELF))
        module = ModuleType(_TRUSTED_ENTRY_NAME)
        module.__file__, module.__package__, module.__cached__ = str(self._snapshot.root / SELF), '', None
        module.__builtins__, module.__som_raw_sha256__ = _admission_builtins(SELF), _sha(raw)
        record = {'code': code, 'codes': _code_closure(code), 'globals': module.__dict__,
                  'path': SELF, 'executed': False, 'callables': {}}
        self._modules[_TRUSTED_ENTRY_NAME], self._records[_TRUSTED_ENTRY_NAME] = module, record
        sys.modules[_TRUSTED_ENTRY_NAME] = module
        exec(code, module.__dict__)
        record['executed'] = True
        record['callables'] = {name: value for name, value in module.__dict__.items()
                               if type(value) is FunctionType and value.__globals__ is module.__dict__}
        record['namespace_state'], record['invocation_references'] = _namespace_state(module.__dict__)
        self.verify()
        return module._trusted_run(self, role, argv)

    def _remove_owned_modules(self):
        primary = None
        for name, module in reversed(tuple(self._modules.items())):
            if sys.modules.get(name) is not module:
                error = ValueError('foreign module replaced owned bootstrap namespace')
                if primary is None:
                    primary = error
                else:
                    _preserve_primary(primary, 'owned-namespace-cleanup', error)
            else:
                del sys.modules[name]
        self._modules.clear()
        self._records.clear()
        self._graph = None
        if primary is not None:
            raise primary

    def close(self):
        if self._closed:
            return
        primary = None
        if self._active is not None:
            try:
                self._active.close()
            except BaseException as error:
                primary = error
            self._active = None
        try:
            self.verify()
        except BaseException as error:
            if primary is None:
                primary = error
            else:
                _preserve_primary(primary, 'bootstrap-source-postcheck', error)
        try:
            self._remove_owned_modules()
        except BaseException as secondary:
            if primary is None:
                primary = secondary
            else:
                _preserve_primary(primary, 'bootstrap-close-owned-cleanup', secondary)
        finally:
            self._closed = True
        if primary is not None:
            raise primary

    def __enter__(self):
        self.verify()
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.close()
        except BaseException as secondary:
            if value is None:
                raise
            _preserve_primary(value, 'bootstrap-context-close', secondary)
        return False


def checked_bootstrap(repo, helper_revision, candidate_revision):
    """Read actual Git/process/GitHub state; no caller policy or byte overrides."""
    _require(type(helper_revision) is str and type(candidate_revision) is str
             and re.fullmatch('[0-9a-f]{40}', helper_revision)
             and helper_revision == candidate_revision
             and candidate_revision != 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa',
             'one new full helper/candidate revision required')
    controls = (tuple(sys.meta_path), importlib.util.spec_from_file_location)
    snapshot = _GitSnapshot(repo, helper_revision)
    _require(Path(__file__).absolute() == snapshot.root / SELF, 'bootstrap must execute its exact source path')
    main = sys.modules['__main__']
    entry_path = Path(main.__file__).absolute()
    _require(entry_path.is_relative_to(snapshot.root), 'bootstrap entry is outside current repository')
    entry = _relative(entry_path.relative_to(snapshot.root).as_posix())
    _require(entry != GRAPH and entry != AUTHORITY and entry != BOUND,
             'admission helper body already executed before bootstrap')
    # The full ls-tree inventory fixes blobs and modes. These are the complete
    # admission-only executable closure plus the actual entry/workflow anchors.
    for path in dict.fromkeys((SELF, BOUND, GRAPH, AUTHORITY, READINESS, WORKFLOW,
                               CALLED_WORKFLOW, INPUT_ACTION, entry)):
        snapshot.read(path)
    readiness = _parse(snapshot.read(READINESS))
    _require(readiness.get('product_acceptance_enabled') is False, 'readiness cannot grant bootstrap product acceptance')
    for name, module in tuple(sys.modules.items()):
        origin = getattr(module, '__file__', None)
        if isinstance(origin, str) and Path(origin).absolute().is_relative_to(snapshot.root):
            _require(module is main or module.__dict__ is globals(), 'repository helper preloaded before source bootstrap')
    binding = _workflow_binding(snapshot, candidate_revision, entry)
    snapshot.verify()
    lease = _BootstrapLease(snapshot, binding, entry, controls, _LEASE_KEY)
    lease.verify()
    return lease


_NATIVE_LAUNCH_ROOT = '/home/runner/work/_temp/som-product-runtime-qualification'
_NATIVE_LAUNCH_PROFILE = BASE + 'native_first_launch_profile_v1.json'
_NATIVE_LAUNCH_PROFILE_SHA = '0937e44ad864c497e04f56fd07f6e048c7a538e072f27bdfdd58708921d198bb'
_NATIVE_LAUNCH_PYTHON_SHA = '8d0f57448252a3a8f8065b889648de2f1ca8959ea3f3fa029a94204bb9325c10'
_NATIVE_LAUNCH_INVENTORY_SHA = 'ac9df6b242d0a5016dd32317af27475e64d652dd38ccc8663743b0537f9cd63c'
_NATIVE_LAUNCH_EVIDENCE = '/home/runner/work/_temp/som-native-origin-before-node-evidence'
_NATIVE_LAUNCH_TMP = '/home/runner/work/_temp/som-native-origin-before-node-tmp'


def _native_before_node_first_launch(owner):
    """Check one initial source-owned launch; never grant a loader authority."""
    caller = sys._getframe(1)
    _require(type(owner) is _BootstrapLease and not owner._closed
             and owner._initial_main is sys.modules.get('__main__')
             and type(owner._initial_main) is ModuleType
             and caller.f_code is main.__code__ and caller.f_globals is globals()
             and globals() is owner._initial_main.__dict__
             and caller.f_locals.get('bootstrap') is owner
             and owner._entry == SELF and owner._active is None and owner._admission is None
             and owner._graph is None and owner._modules == {} and owner._records == {},
             'native first launch needs its actual initial bootstrap')
    _require(owner._binding['job'] == 'required-callbacks'
             and sys.version_info[:3] == (3, 12, 11) and sys.flags.optimize == 0
             and sys.flags.isolated == 1 and sys.flags.no_site == 1
             and sys.flags.dont_write_bytecode == 1 and sys.pycache_prefix is None,
             'native first launch interpreter or startup differs')
    owner.verify()
    evidence_identity, phase = None, 'evidence-directory'
    public = {'version': 'som.native-before-node-first-launch-join.v1', 'status': 'FAILED',
              'scope': 'required-callbacks/typescript-regression/entry.ts-input-consumer/before-node only',
              'acceptance': {'SourceSeal': False, 'compiler_acceptance_passed': False,
                             'product_acceptance_passed': False, 'whole_acceptance_complete': False},
              'sequence': []}
    try:
        temporary = Path('/home/runner/work/_temp')
        _require(os.environ.get('RUNNER_TEMP') == str(temporary), 'fixed native runner temporary root required')
        _physical_directory(temporary)
        os.mkdir(_NATIVE_LAUNCH_EVIDENCE, 0o700)
        info = os.lstat(_NATIVE_LAUNCH_EVIDENCE)
        _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700,
                 'new owned native launch evidence required')
        evidence_identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
        public['sequence'].append('source_workflow_bound')
        phase = 'runtime-source'
        files = _native_launch_files(owner)
        profile, installed = files['profile'], files['inventory']
        public['source'] = files['source']
        public['runtime'] = files['runtime']
        public['github'] = {key: owner._binding[key] for key in (
            'repository', 'run_id', 'run_attempt', 'job', 'job_id', 'runner_name')}
        public['github'].update(workflow_ref=owner._binding['github_workflow_ref'],
                                workflow_sha=owner._binding['github_workflow_sha'])
        public['sequence'].append('fixed_runtime_and_profile_joined')
        phase = 'process'
        process = _native_launch_stat(_native_launch_read('proc-stat', owner)['raw'])
        boot_id = _native_launch_read('boot-id', owner)['raw'].decode('ascii').strip()
        _require(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id),
                 'native process boot identity differs')
        _require(process['pid'] == os.getpid() and process['ppid'] == os.getppid()
                 and process['one_task'] is True
                 and sorted(os.listdir('/proc/' + str(process['pid']) + '/task')) == [str(process['pid'])],
                 'native process task ownership differs')
        argv = _native_launch_cmdline(_native_launch_read('proc-cmdline', owner)['raw'], owner)
        actual_exe = _native_launch_read('proc-exe', owner)
        expected_exe = _native_launch_read(('installed', 'install/python/bin/python3.12'), owner)
        _require(actual_exe['sha256'] == expected_exe['sha256'] == _NATIVE_LAUNCH_PYTHON_SHA
                 and actual_exe['identity'] == expected_exe['identity']
                 and actual_exe['path'] == _NATIVE_LAUNCH_ROOT + '/install/python/bin/python3.12'
                 and stat.S_IMODE(actual_exe['identity'][2]) == 0o755,
                 'actual kernel executable is not the qualified regular Python')
        process.update(boot_id=boot_id, exe_path=actual_exe['path'],
                       exe_dev=actual_exe['identity'][0], exe_inode=actual_exe['identity'][1],
                       exe_size=actual_exe['size'], exe_mode=0o755, exe_sha256=actual_exe['sha256'],
                       complete_public_argv=argv, cwd=os.getcwd(),
                       cmdline_sha256=_sha(_native_launch_read('proc-cmdline', owner)['raw']),
                       flags={'isolated': sys.flags.isolated, 'no_site': sys.flags.no_site,
                              'dont_write_bytecode': sys.flags.dont_write_bytecode, 'optimize': sys.flags.optimize})
        _require(Path(os.getcwd()) == owner._snapshot.root
                 and sys.executable == actual_exe['path']
                 and sys.base_prefix == _NATIVE_LAUNCH_ROOT + '/install/python',
                 'native launch cwd or interpreter prefix differs')
        public['process'] = process
        public['sequence'].append('kernel_process_joined')
        phase = 'environment'
        public['environment'] = _native_launch_environment(owner)
        public['sequence'].append('private_environment_checked')
        phase = 'module-loader-host'
        before_raw = _native_launch_read('proc-maps', owner)['raw']
        before = _native_launch_maps(before_raw)
        modules = _native_launch_modules(owner, installed, profile)
        host = _native_launch_host_closure(before, modules['native_files'])
        public['startup'] = {'qualified_module_rows': modules['modules'], 'fixed_alias_rows': modules['aliases'],
                             'source_cache_frozen_code_rows': modules['code'], 'native_ELF_graph': host['ELF_graph']}
        public['host'] = host['host']
        public['sequence'].append('module_loader_and_host_graph_joined')
        phase = 'stable-readback'
        owner.verify()
        again = _native_launch_files(owner)
        _require(again == files, 'qualified native launch source or installed bytes changed')
        final_process = _native_launch_stat(_native_launch_read('proc-stat', owner)['raw'])
        _require(all(final_process[key] == process[key] for key in ('pid', 'ppid', 'pgrp', 'session', 'starttime', 'one_task')),
                 'native process changed during initial join')
        after_raw = _native_launch_read('proc-maps', owner)['raw']
        after = _native_launch_maps(after_raw)
        executable = lambda rows: [tuple(row[key] for key in ('start', 'end', 'permissions', 'offset', 'dev', 'inode', 'path'))
                                   for row in rows if 'x' in row['permissions']]
        _require(executable(before) == executable(after), 'native executable mapping graph changed')
        public['startup'].update(map_snapshot_sha256_before_after=[_sha(before_raw), _sha(after_raw)],
                                 executable_graph_stable=True)
        public['sequence'].append('stable_readback')
        public['status'] = 'INITIAL_SOURCE_DATA_JOIN_COMPLETE'
        phase = 'report'
        _native_launch_report(owner, 'complete', public)
    except BaseException as primary:
        if evidence_identity is not None:
            kind = type(primary)
            public = {'version': 'som.native-before-node-first-launch-join.v1', 'status': 'FAILED',
                      'scope': 'required-callbacks/typescript-regression/entry.ts-input-consumer/before-node only',
                      'acceptance': {'SourceSeal': False, 'compiler_acceptance_passed': False,
                                     'product_acceptance_passed': False, 'whole_acceptance_complete': False},
                      'sequence': list(public['sequence']),
                      'github': {key: owner._binding[key] for key in ('repository', 'run_id', 'run_attempt', 'job_id')},
                      'source': {'candidate_revision': owner._snapshot.revision},
                      'process': {'pid': os.getpid()},
                      'failure': {'fixed_phase': phase, 'fixed_failure_code': 'NATIVE_FIRST_LAUNCH_REJECTED',
                                  'safe_exception_module': type.__getattribute__(kind, '__module__'),
                                  'safe_exception_type': type.__getattribute__(kind, '__name__')}}
            try:
                _native_launch_report(owner, 'failure', public)
            except BaseException as secondary:
                _preserve_primary(primary, 'native-first-launch-failure-evidence', secondary)
        raise


def _native_launch_read(role, owner):
    """Read only one private fixed data role, retaining no open descriptor."""
    fixed = {
        'profile': (str(owner._snapshot.root / _NATIVE_LAUNCH_PROFILE), 524288),
        'capsule': ('/home/runner/work/_temp/som-current-inputs/runtime/capsule.json', 16777216),
        'archive': ('/home/runner/work/_temp/som-current-inputs/runtime/runtime.tar', 2167963648),
        'restore': ('/home/runner/work/_temp/som-runtime-capsule-restore-evidence/restore.json', 16777216),
        'origin': ('/home/runner/work/_temp/som-runtime-capsule-restore-evidence/artifact-origin.json', 16777216),
        'inventory': (_NATIVE_LAUNCH_ROOT + '/qualification-evidence/installed-files.json', 8388608),
        'reuse': (_NATIVE_LAUNCH_ROOT + '/receipt.json', 8388608),
        'original': (_NATIVE_LAUNCH_ROOT + '/qualification-evidence/receipt.json', 8388608),
        'boot-id': ('/proc/sys/kernel/random/boot_id', 128),
        'os-release': ('/usr/lib/os-release', 65536),
        'dpkg-status': ('/var/lib/dpkg/status', 33554432),
        'nss': ('/etc/nsswitch.conf', 65536),
        'openssl': ('/etc/ssl/openssl.cnf', 1048576),
        'ca-file': ('/etc/ssl/certs/ca-certificates.crt', 16777216),
        'ld-preload': ('/etc/ld.so.preload', 65536),
    }
    proc = role in ('proc-stat', 'proc-cmdline', 'proc-maps', 'proc-exe')
    if proc:
        pid = str(os.getpid())
        _require(os.readlink('/proc/self') == pid, 'native proc self identity differs')
        leaf, limit = {'proc-stat': ('stat', 4096), 'proc-cmdline': ('cmdline', 65536),
                       'proc-maps': ('maps', 2097152), 'proc-exe': ('exe', 268435456)}[role]
        path = '/proc/' + pid + '/' + leaf
    elif type(role) is str and role in fixed:
        path, limit = fixed[role]
    else:
        _require(type(role) is tuple and len(role) == 2 and all(type(value) is str for value in role),
                 'unlisted native launch data role')
        tag, name = role
        if tag == 'repository':
            _require(name in {BASE + 'product_runtime/' + leaf for leaf in (
                'runtime_capsule.py', 'reuse_runtime.py', 'qualify_runtime.py', 'runtime-bf3-qualification.json',
                'runtime-bf3-byte-audit.json', 'source-pins.json')} | {BASE + 'product_python/wheel-policy.json'},
                'unlisted native launch repository data')
            raw = owner._snapshot.read(name)
            return {'raw': raw, 'size': len(raw), 'sha256': _sha(raw), 'path': str(owner._snapshot.root / name)}
        if tag == 'installed':
            name = _relative(name)
            _require(name.startswith('install/'), 'native installed data escapes fixed tree')
            path, limit = _NATIVE_LAUNCH_ROOT + '/' + name, 268435456
        elif tag == 'host-elf':
            _require(str(Path(name).parent) == '/usr/lib/x86_64-linux-gnu'
                     and str(Path(name)) == name and '..' not in Path(name).parts,
                     'unlisted native host ELF path')
            path, limit = name, 268435456
        elif tag in ('package-list', 'package-md5'):
            suffix = '.list' if tag == 'package-list' else '.md5sums'
            _require(re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?' + re.escape(suffix), name),
                     'invalid native host package data name')
            path, limit = '/var/lib/dpkg/info/' + name, 4194304
        elif tag == 'locale-data':
            allowed = {'locale-archive'} | {'C.utf8/' + leaf for leaf in (
                'LC_CTYPE', 'LC_NUMERIC', 'LC_TIME', 'LC_COLLATE', 'LC_MONETARY', 'LC_MESSAGES/SYS_LC_MESSAGES',
                'LC_PAPER', 'LC_NAME', 'LC_ADDRESS', 'LC_TELEPHONE', 'LC_MEASUREMENT', 'LC_IDENTIFICATION')}
            _require(name in allowed, 'unlisted native host locale data')
            path, limit = '/usr/lib/locale/' + name, 268435456
        else:
            raise ValueError('unlisted native launch data role')
    host_role = (role in ('os-release', 'dpkg-status', 'nss', 'openssl', 'ca-file', 'ld-preload')
                 or type(role) is tuple and role[0] in ('host-elf', 'package-list', 'package-md5', 'locale-data'))
    special_exe = role == 'proc-exe'
    if not special_exe:
        _physical_directory(Path(path).parent)
        before = os.lstat(path)
        _require(stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode),
                 'native launch data must be regular')
        if host_role:
            _require(before.st_uid == 0 and before.st_mode & 0o022 == 0,
                     'native fixed host data is not protected root-owned data')
            for parent in Path(path).parents:
                ancestor = os.lstat(parent)
                _require(stat.S_ISDIR(ancestor.st_mode) and not stat.S_ISLNK(ancestor.st_mode)
                         and ancestor.st_uid == 0 and ancestor.st_mode & 0o022 == 0,
                         'native fixed host ancestor is not protected root-owned data')
    else:
        before = None
    fd, primary = None, None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | (0 if special_exe else os.O_NOFOLLOW | os.O_NONBLOCK))
        initial = os.fstat(fd)
        _require(stat.S_ISREG(initial.st_mode), 'native launch opened data changed type')
        identity = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
                                  value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if before is not None:
            _require(identity(before) == identity(initial), 'native launch data changed before read')
        _require(proc or role == 'boot-id' or initial.st_size <= limit, 'native launch data exceeds limit')
        chunks, size, digest = [], 0, hashlib.sha256()
        while True:
            block = os.read(fd, min(2097152, limit + 1 - size))
            if not block:
                break
            size += len(block)
            _require(size <= limit, 'native launch data exceeds limit')
            digest.update(block)
            if role != 'archive':
                chunks.append(block)
        final = os.fstat(fd)
        _require(identity(initial) == identity(final), 'native launch data changed during read')
        if not proc and role != 'boot-id':
            _require(size == initial.st_size and identity(os.lstat(path)) == identity(initial),
                     'native launch data size or path changed')
        actual_path = os.readlink(path) if special_exe else path
        _require(not actual_path.endswith(' (deleted)'), 'deleted native executable refused')
        result = {'path': actual_path, 'size': size, 'sha256': digest.hexdigest(), 'identity': identity(initial)}
        if role != 'archive':
            result['raw'] = b''.join(chunks)
        return result
    except BaseException as error:
        primary = error
        raise
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as secondary:
                if primary is None:
                    raise
                _preserve_primary(primary, 'native-launch-read-close', secondary)


def _native_launch_stat(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= 4096 and raw.endswith(b'\n')
             and b'\0' not in raw, 'invalid native proc stat')
    opening, closing = raw.find(b'('), raw.rfind(b')')
    _require(opening > 1 and closing > opening and raw[:opening].strip().isdigit(), 'invalid native proc stat delimiters')
    pid, comm = int(raw[:opening].strip()), raw[opening + 1:closing]
    tail = raw[closing + 1:].split()
    _require(len(tail) == 50 and tail[0] in (b'R', b'S', b'D') and comm == b'python3.12',
             'native proc stat field count or state differs')
    signed = {4, 5, 6, 7, 8, 16, 17, 18, 19, 20, 38, 39, 44, 52}
    fields = {1: pid}
    for number, value in enumerate(tail[1:], 4):
        _require(re.fullmatch(b'-?[0-9]+' if number in signed else b'[0-9]+', value),
                 'native proc stat numeric field differs')
        parsed = int(value)
        _require(-(1 << 63) <= parsed < (1 << 63) if number in signed else 0 <= parsed < (1 << 64),
                 'native proc stat numeric field overflow')
        fields[number] = parsed
    _require(0 < pid < (1 << 64) and all(fields[number] > 0 for number in (1, 4, 5, 6, 22)) and fields[20] == 1,
             'native proc stat process identity differs')
    return {'pid': pid, 'ppid': fields[4], 'pgrp': fields[5], 'session': fields[6],
            'starttime': fields[22], 'one_task': True}


def _native_launch_cmdline(raw, owner):
    _require(type(raw) is bytes and 0 < len(raw) <= 65536 and raw.endswith(b'\0'),
             'native kernel argv is not bounded NUL data')
    expected = [_NATIVE_LAUNCH_ROOT + '/install/python/bin/python3.12', '-I', '-S', '-B',
                str(owner._snapshot.root / SELF), '--repo', str(owner._snapshot.root),
                '--helper-revision', owner._snapshot.revision, '--candidate-revision', owner._snapshot.revision,
                '--entry-role', 'entry.ts-input-consumer', '--', '--repo', str(owner._snapshot.root),
                '--helper-revision', owner._snapshot.revision, '--candidate-revision', owner._snapshot.revision,
                'before-node']
    _require(raw == b'\0'.join(os.fsencode(value) for value in expected) + b'\0'
             and sys.orig_argv == expected and sys.argv == expected[4:],
             'native kernel argv differs from the one selected original launch')
    return expected


def _native_launch_environment(owner):
    expected = dict(owner._binding['process_environment'])
    _require(type(os.environ.get('HOME')) is str and os.environ['HOME'] and '\n' not in os.environ['HOME'],
             'native launch actual HOME required')
    home = Path(os.environ['HOME'])
    _require(home.is_absolute() and len(str(home)) <= 4096, 'native launch HOME is not bounded absolute data')
    _physical_directory(home)
    _physical_directory(Path(_NATIVE_LAUNCH_TMP))
    _require(stat.S_IMODE(os.lstat(_NATIVE_LAUNCH_TMP).st_mode) == 0o700
             and not os.listdir(_NATIVE_LAUNCH_TMP), 'new native task TMPDIR required')
    expected.update(PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8', HOME=str(home),
                    TMPDIR=_NATIVE_LAUNCH_TMP, RUNNER_TEMP='/home/runner/work/_temp')
    private_name = 'SOM_DEPENDENCY_ACTIONS_TOKEN'
    allowed = set(expected) | {private_name}
    _require(set(os.environ) == allowed and all(type(key) is str for key in os.environ),
             'native launch environment names differ')
    fd, primary, raw, pieces, value, current = None, None, bytearray(), [], None, None
    try:
        fd = os.open('/proc/' + str(os.getpid()) + '/environ', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        while True:
            block = os.read(fd, 4096)
            if not block:
                break
            raw.extend(block)
            _require(len(raw) <= 65536, 'native initial environment exceeds bound')
        _require(raw and raw[-1] == 0, 'native initial environment lacks terminator')
        pieces = raw.split(b'\0')
        observed, public = set(), {}
        for piece in pieces[:-1]:
            name, separator, value = piece.partition(b'=')
            _require(separator and name and all(65 <= item <= 90 or 97 <= item <= 122
                     or 48 <= item <= 57 or item == 95 for item in name), 'native environment entry rejected')
            key = name.decode('ascii')
            _require(key in allowed and key not in observed, 'native environment entry rejected')
            observed.add(key)
            if key == private_name:
                current = os.environ[private_name]
                _require(type(current) is str and current and '\n' not in current and '\r' not in current
                         and value == os.fsencode(current), 'native controlled private binding rejected')
            else:
                _require(value == os.fsencode(expected[key]) and os.environ[key] == expected[key],
                         'native public environment value differs')
                public[key] = expected[key]
        _require(observed == allowed, 'native environment key universe differs')
        return {'exact_allowed_names': sorted(allowed), 'all_nonsecret_values': public,
                'private_names_only': [private_name], 'private_required_present': True,
                'private_internal_equality': True, 'extra_names_absent': True, 'raw_environment_saved_false': True}
    except BaseException as error:
        primary = error
        raise
    finally:
        current = value = None
        raw.clear()
        for piece in pieces:
            piece.clear()
        pieces.clear()
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as secondary:
                if primary is None:
                    raise
                _preserve_primary(primary, 'native-launch-private-environment-close', secondary)


def _native_launch_maps(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= 2097152 and raw.endswith(b'\n') and b'\0' not in raw,
             'invalid native maps data')
    lines = raw.splitlines()
    _require(len(lines) <= 8192, 'native maps row count exceeds bound')
    rows, previous = [], 0
    for line in lines:
        match = re.fullmatch(rb'([0-9a-f]+)-([0-9a-f]+) ([r-][w-][x-][ps]) ([0-9a-f]+) ([0-9a-f]+):([0-9a-f]+) ([0-9]+)(?: +(.*))?', line)
        _require(match is not None, 'native maps row grammar differs')
        start, end, permissions, offset, major, minor, inode, name = match.groups()
        start, end, offset, major, minor, inode = (int(start, 16), int(end, 16), int(offset, 16),
                                                 int(major, 16), int(minor, 16), int(inode))
        permissions = permissions.decode('ascii')
        name = '' if name is None else os.fsdecode(name)
        _require(0 <= previous <= start < end < (1 << 64) and offset < (1 << 64)
                 and len(name) <= 4096 and not any(ord(char) < 32 or ord(char) == 127 for char in name)
                 and not name.endswith(' (deleted)') and not ('w' in permissions and 'x' in permissions),
                 'native mapping range, path or permissions rejected')
        previous = end
        if not name or name in ('[heap]', '[stack]'):
            _require(inode == major == minor == offset == 0 and permissions.endswith('p')
                     and 'x' not in permissions and (not name or permissions == 'rw-p'),
                     'native anonymous mapping rejected')
            category = 'private-anonymous'
        elif name in ('[vdso]', '[vvar]', '[vvar_vclock]', '[vsyscall]'):
            _require(inode == major == minor == offset == 0, 'native kernel mapping metadata differs')
            if name == '[vsyscall]':
                _require((start, end, permissions) == (0xffffffffff600000, 0xffffffffff601000, '--xp'),
                         'native vsyscall mapping differs')
            else:
                _require(end - start <= 65536 and permissions == ('r-xp' if name == '[vdso]' else 'r--p'),
                         'native kernel mapping layout differs')
            category = 'kernel'
        else:
            _require(name.startswith('/') and inode > 0 and permissions.endswith('p')
                     and not name.startswith(('/dev/', '/memfd:')), 'native file mapping rejected')
            category = 'file'
        rows.append({'start': start, 'end': end, 'permissions': permissions, 'offset': offset,
                     'dev': [major, minor], 'inode': inode, 'path': name, 'category': category})
    return rows


def _native_launch_files(owner):
    roles = ('profile', 'capsule', 'restore', 'origin', 'inventory', 'reuse', 'original')
    raw = {role: _native_launch_read(role, owner) for role in roles}
    _require(raw['profile']['raw'] == owner._snapshot.read(_NATIVE_LAUNCH_PROFILE)
             and raw['profile']['sha256'] == _NATIVE_LAUNCH_PROFILE_SHA,
             'native startup profile differs from fixed source')
    data = {role: _parse(row['raw']) for role, row in raw.items()}
    _require(all(type(row) is dict for row in data.values()), 'native runtime metadata objects required')
    base = BASE + 'product_runtime/'
    names = ('runtime_capsule.py', 'reuse_runtime.py', 'qualify_runtime.py', 'runtime-bf3-qualification.json',
             'runtime-bf3-byte-audit.json', 'source-pins.json')
    source_raw = {base + name: _native_launch_read(('repository', base + name), owner)['raw'] for name in names}
    q, audit, pins = (_parse(source_raw[base + name]) for name in (
        'runtime-bf3-qualification.json', 'runtime-bf3-byte-audit.json', 'source-pins.json'))
    policy_raw = _native_launch_read(('repository', BASE + 'product_python/wheel-policy.json'), owner)['raw']
    policy = _parse(policy_raw)
    _require(_sha(source_raw[base + 'runtime-bf3-byte-audit.json']) == '7a2c04a65866474bd773b9722274c09c36646951fa7848d62e4b20b2cb94e75f'
             and _sha(source_raw[base + 'source-pins.json']) == 'a4208c342bc9e923509bc5ba7ce6a9a1adfe3143d55b6215f0a313b4833550b2'
             and q.get('version') == 'som.product-runtime-artifact-qualification.v1'
             and q.get('qualified_for') == 'later_GitHub_tests_only' and q.get('product_runtime_ready') is False
             and q.get('product_acceptance_passed') is False and q.get('install_prefix') == _NATIVE_LAUNCH_ROOT
             and q['transport_files']['installed-files.json'] == raw['inventory']['sha256'] == _NATIVE_LAUNCH_INVENTORY_SHA
             and audit.get('qualification_evidence_verified') is True
             and policy.get('python') == data['profile'].get('python') == '3.12.11'
             and policy['native_sha256']['python'] == data['profile']['python_ELF_sha256'] == _NATIVE_LAUNCH_PYTHON_SHA,
             'native runtime original qualification differs')
    python_rows = [row for row in pins['archives'] if row['name'] == 'python']
    _require(len(python_rows) == 1 and python_rows[0]['version'] == '3.12.11'
             and python_rows[0]['sha256'] == data['profile']['release_archive_sha256']
             == 'c30bb24b7f1e9a19b11b55a546434f74e739bb4c271a3e3a80ff4380d49f7adb',
             'native runtime qualified Python source differs')
    capsule, restore, origin = data['capsule'], data['restore'], data['origin']
    identity = {'repository': owner._binding['repository'], 'run_id': owner._binding['run_id'],
                'run_attempt': owner._binding['run_attempt']}
    source_pins = {path: _sha(body) for path, body in source_raw.items()}
    for row in (capsule, restore):
        _require(row.get('github') == identity and row.get('helper_revision') == row.get('candidate_revision')
                 == owner._snapshot.revision and row.get('source_sha256') == source_pins
                 and row.get('product_runtime_ready') is False and row.get('product_acceptance_passed') is False,
                 'native capsule actual run/source differs')
    _require(capsule.get('version') == 'som.same-run-qualified-runtime-capsule.v1'
             and capsule.get('role') == 'same-run-qualified-runtime-transport'
             and capsule.get('runtime_prefix') == _NATIVE_LAUNCH_ROOT
             and capsule.get('installed_code_executed_by_capsule') is False
             and restore.get('version') == 'som.same-run-qualified-runtime-capsule-restore.v1'
             and restore.get('status') == 'same_run_qualified_runtime_restored_without_execution'
             and restore.get('installed_code_executed') is False and restore.get('complete_runtime_tree_verified') is True
             and restore.get('runtime_root') == _NATIVE_LAUNCH_ROOT
             and restore.get('manifest_sha256') == raw['capsule']['sha256']
             and restore.get('qualification_profile') == capsule.get('profile')
             and restore.get('artifact_origin_sha256') == raw['origin']['sha256'],
             'native restored capsule is incomplete or from another source')
    _require(type(restore.get('artifact_id')) is int and restore['artifact_id'] > 0
             and type(restore.get('artifact_digest')) is str and re.fullmatch('[0-9a-f]{64}', restore['artifact_digest'])
             and origin.get('id') == restore['artifact_id'] and origin.get('name') == 'same-run-qualified-runtime-capsule'
             and origin.get('expired') is False and origin.get('digest') == 'sha256:' + restore['artifact_digest']
             and origin.get('workflow_run', {}).get('id') == identity['run_id']
             and origin.get('workflow_run', {}).get('head_sha') == owner._snapshot.revision
             and origin.get('url') == 'https://api.github.com/repos/faberline/som/actions/artifacts/' + str(restore['artifact_id']),
             'native capsule artifact origin differs')
    archive = _native_launch_read('archive', owner)
    _require(type(capsule.get('archive')) is dict and set(capsule['archive']) == {'name', 'size', 'sha256'}
             and capsule['archive']['name'] == 'runtime.tar' and capsule['archive']['size'] == archive['size']
             and capsule['archive']['sha256'] == restore.get('archive_sha256') == archive['sha256'],
             'native capsule archive changed')
    reuse, original = data['reuse'], data['original']
    _require(reuse.get('version') == 'som.product-runtime-reuse.v1'
             and reuse.get('status') == 'audited_runtime_reinstalled_and_rechecked_for_hosted_tests'
             and reuse.get('tested_sha') == owner._snapshot.revision
             and reuse.get('installed_tree_verified_before') is True and reuse.get('installed_tree_verified_after') is True
             and reuse.get('qualification_evidence_verified_after') is True
             and reuse.get('product_runtime_ready') is False and reuse.get('product_acceptance_passed') is False
             and raw['original']['sha256'] == q['transport_files']['receipt.json']
             and original.get('source_revision') == q['source_revision']
             and capsule['profile']['inventory_sha256'] == _NATIVE_LAUNCH_INVENTORY_SHA
             and capsule['profile']['reuse_receipt_sha256'] == raw['reuse']['sha256'],
             'native original/reuse runtime evidence differs')
    inventory, entries = data['inventory'], capsule['entries']
    _require(type(entries) is dict and 0 < len(entries) <= 20000
             and q['counts']['regular_installed_files'] == 10102 and q['counts']['aliases'] == 11,
             'native installed universe or counts differ')
    files, directories, total = set(), {'install'}, 0
    for parent, dirs, leaves in os.walk(_NATIVE_LAUNCH_ROOT + '/install', followlinks=False):
        for leaf in dirs:
            path = Path(parent) / leaf
            info = os.lstat(path)
            _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'native installed directory alias refused')
            directories.add(path.relative_to(_NATIVE_LAUNCH_ROOT).as_posix())
        for leaf in leaves:
            files.add((Path(parent) / leaf).relative_to(_NATIVE_LAUNCH_ROOT).as_posix())
    _require(files == set(inventory), 'complete native installed file set differs')
    expected_directories = {'/'.join(name.split('/')[:index]) for name in inventory
                            for index in range(1, len(name.split('/')))}
    _require(directories == expected_directories, 'complete native installed directory set differs')
    fingerprints, aliases, regular = {}, {}, 0
    for name, row in sorted(inventory.items()):
        _relative(name)
        _require(name.startswith('install/') and type(row) is dict
                 and type(row.get('sha256')) is str and re.fullmatch('[0-9a-f]{64}', row['sha256']),
                 'invalid native installed inventory row')
        path = Path(_NATIVE_LAUNCH_ROOT) / name
        if row.get('kind') == 'regular_file':
            _require(set(row) == {'kind', 'sha256', 'executable_bit'} and type(row['executable_bit']) is bool,
                     'native regular inventory row differs')
            actual = _native_launch_read(('installed', name), owner)
            mode = 0o755 if row['executable_bit'] else 0o644
            _require(actual['sha256'] == row['sha256'] and actual['identity'][3] == 1
                     and stat.S_IMODE(actual['identity'][2]) == mode,
                     'native installed bytes, links or mode differ')
            transport = entries.get(name)
            _require(type(transport) is dict and transport == {'kind': 'file', 'mode': mode,
                     'sha256': row['sha256'], 'size': actual['size']}, 'native capsule installed row differs')
            fingerprints[name] = actual['identity']
            total += actual['size']
            _require(total <= 2147483648, 'native installed total exceeds bound')
            regular += 1
        else:
            _require(set(row) == {'kind', 'target', 'sha256', 'qualification_probe_executed', 'runtime_admitted'}
                     and row.get('kind') == 'alias_not_admitted' and type(row.get('target')) is str
                     and row['qualification_probe_executed'] is False and row['runtime_admitted'] is False
                     and row['target'] not in ('.', '..') and '/' not in row['target']
                     and row['sha256'] == _sha(row['target'].encode())
                     and os.path.islink(path) and os.readlink(path) == row['target']
                     and entries.get(name) == {'kind': 'alias', 'target': row['target'], 'mode': 0o777},
                     'native installed alias differs')
            target = name.rsplit('/', 1)[0] + '/' + row['target']
            _require(inventory.get(target, {}).get('kind') == 'regular_file', 'native alias target is not an original regular file')
            aliases[name] = row['target']
    _require(regular == 10102 and len(aliases) == 11 and aliases == q['aliases'], 'native installed counts/aliases differ')
    bootstrap_raw = owner._snapshot.read(SELF)
    return {'profile': data['profile'], 'inventory': inventory, 'installed_fingerprints': fingerprints,
            'source': {'helper_revision': owner._snapshot.revision, 'candidate_revision': owner._snapshot.revision,
                       'bootstrap_git_blob_oid': owner._snapshot.entries[SELF][2].decode(), 'bootstrap_sha256': _sha(bootstrap_raw),
                       'profile_sha256': _NATIVE_LAUNCH_PROFILE_SHA,
                       'workflow_sha256': _sha(owner._snapshot.read(WORKFLOW)),
                       'called_workflow_sha256': _sha(owner._snapshot.read(CALLED_WORKFLOW)),
                       'fixed_entry_leaf': BASE + 'run_typescript_input_consumer.py'},
            'runtime': {'qualification_sha256': _sha(source_raw[base + 'runtime-bf3-qualification.json']),
                        'controller_audit_sha256': _sha(source_raw[base + 'runtime-bf3-byte-audit.json']),
                        'inventory_sha256': raw['inventory']['sha256'], 'capsule_manifest_sha256': raw['capsule']['sha256'],
                        'archive_sha256': archive['sha256'], 'restore_sha256': raw['restore']['sha256'],
                        'artifact_origin_sha256': raw['origin']['sha256'], 'artifact_id': restore['artifact_id'],
                        'artifact_digest': restore['artifact_digest'], 'python_ELF_sha256': _NATIVE_LAUNCH_PYTHON_SHA,
                        'installed_regular_count': regular, 'alias_count': len(aliases)}}


def _native_launch_elf(raw):
    _require(type(raw) is bytes and 64 <= len(raw) <= 268435456
             and raw[:7] == b'\x7fELF\x02\x01\x01', 'native ELF64LE required')
    integer = lambda offset, size: int.from_bytes(raw[offset:offset + size], 'little')
    _require(integer(16, 2) in (2, 3) and integer(18, 2) == 62 and integer(52, 2) == 64,
             'native ELF target/header differs')
    phoff, phsize, count = integer(32, 8), integer(54, 2), integer(56, 2)
    _require(phsize == 56 and 0 < count <= 1024 and phoff <= len(raw) - count * phsize,
             'native ELF program table is not bounded')
    loads, dynamics, interpreters = [], [], []
    for index in range(count):
        start = phoff + index * phsize
        kind, flags = integer(start, 4), integer(start + 4, 4)
        offset, address, size, memory = (integer(start + 8, 8), integer(start + 16, 8),
                                        integer(start + 32, 8), integer(start + 40, 8))
        _require(offset <= len(raw) and size <= len(raw) - offset and size <= memory
                 and address + memory < (1 << 64), 'native ELF segment range differs')
        if kind == 1:
            loads.append({'offset': offset, 'address': address, 'size': size, 'memory': memory, 'flags': flags})
        elif kind == 2:
            dynamics.append((offset, size))
        elif kind == 3:
            _require(1 < size <= 4096, 'native ELF interpreter is not bounded')
            body = raw[offset:offset + size]
            _require(body.endswith(b'\0') and b'\0' not in body[:-1], 'native ELF interpreter is not one path')
            interpreters.append(body[:-1].decode('ascii'))
    _require(loads and len(dynamics) <= 1 and len(interpreters) <= 1, 'native ELF segment cardinality differs')
    values, needed = {}, []
    if dynamics:
        offset, size = dynamics[0]
        _require(0 < size <= 65536 and size % 16 == 0, 'native ELF dynamic table is not bounded')
        terminal = False
        for at in range(offset, offset + size, 16):
            tag, value = integer(at, 8), integer(at + 8, 8)
            if tag == 0:
                terminal = True
                break
            if tag == 1:
                needed.append(value)
            elif tag in (5, 10, 14, 15, 29):
                _require(tag not in values, 'duplicate native ELF string metadata')
                values[tag] = value
        _require(terminal and 5 in values and 10 in values and 0 < values[10] <= 16777216,
                 'native ELF dynamic string table missing')
        matches = [row['offset'] + values[5] - row['address'] for row in loads
                   if row['address'] <= values[5] and values[10] <= row['size'] - (values[5] - row['address'])]
        _require(len(matches) == 1, 'native ELF string table is not uniquely file backed')
        strings = raw[matches[0]:matches[0] + values[10]]
        def text(at):
            _require(type(at) is int and 0 <= at < len(strings), 'native ELF string index differs')
            end = strings.find(b'\0', at)
            _require(at < end <= at + 4096, 'native ELF string termination differs')
            result = strings[at:end].decode('ascii')
            _require(not any(ord(char) < 32 or ord(char) == 127 for char in result), 'native ELF string control refused')
            return result
        names = [text(value) for value in needed]
        soname = text(values[14]) if 14 in values else None
        for value in [*names, *([] if soname is None else [soname])]:
            _require(re.fullmatch(r'[A-Za-z0-9_.+-]{1,128}', value), 'native ELF dependency is not a basename')
        paths = [text(values[tag]) for tag in (15, 29) if tag in values]
    else:
        names, soname, paths = [], None, []
    return {'needed': names, 'soname': soname, 'search_paths': paths,
            'interpreter': interpreters[0] if interpreters else None, 'loads': loads}


def _native_launch_modules(owner, installed, profile):
    _require(type(sys.modules) is dict and len(sys.modules) <= 512
             and all(type(name) is str for name in sys.modules)
             and sys.implementation.cache_tag == 'cpython-312' and sys.pycache_prefix is None
             and sys.flags.optimize == 0, 'native startup module/cache policy differs')
    snapshot = dict(sys.modules)
    _require(not (set(snapshot) & set(profile['forbidden_actual_modules']))
             and sys.gettrace() is None and sys.getprofile() is None
             and all(sys.monitoring.get_tool(number) is None for number in range(6)),
             'native startup instrumentation or extra module rejected')
    source_rows = profile['source_modules']
    aliases = profile['fixed_module_aliases']
    frozen_names, native_names = set(profile['frozen_names']), set(profile['native_module_names'])
    frozen_loader = importlib.machinery.FrozenImporter
    builtin_loader = importlib.machinery.BuiltinImporter
    source_loader, extension_loader = importlib.machinery.SourceFileLoader, importlib.machinery.ExtensionFileLoader
    machinery = sys.modules.get('_imp')
    marshal_module = sys.modules.get('marshal')
    _require(type(machinery) is ModuleType and type(marshal_module) is ModuleType,
             'native frozen/cache machinery must already exist')
    _require(ModuleType.__getattribute__(machinery, '__dict__').get('check_hash_based_pycs') == 'default',
             'native source-loader hash-cache policy differs')
    get_frozen = ModuleType.__getattribute__(machinery, '__dict__').get('get_frozen_object')
    marshal_loads = ModuleType.__getattribute__(marshal_module, '__dict__').get('loads')
    _require(type(get_frozen) is type(sys._getframe) and get_frozen.__self__ is machinery
             and get_frozen.__name__ == 'get_frozen_object'
             and type(marshal_loads) is type(sys._getframe) and marshal_loads.__self__ is marshal_module
             and marshal_loads.__name__ == 'loads', 'native preloaded code-data reader differs')
    code_fields = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals', 'co_stacksize',
                   'co_flags', 'co_code', 'co_consts', 'co_names', 'co_varnames', 'co_filename', 'co_name',
                   'co_qualname', 'co_firstlineno', 'co_linetable', 'co_exceptiontable', 'co_freevars', 'co_cellvars')
    def equal_code(actual, expected):
        pending, visited = [(actual, expected)], 0
        while pending:
            left, right = pending.pop()
            visited += 1
            _require(visited <= 1000000 and type(left) is type(right), 'native cache code constant type differs')
            kind = type(left)
            if kind is CodeType:
                pending.extend((object.__getattribute__(left, key), object.__getattribute__(right, key))
                               for key in code_fields)
            elif kind is tuple:
                _require(len(left) == len(right), 'native cache code tuple differs')
                pending.extend(zip(left, right))
            elif kind is frozenset:
                allowed = (str, bytes, int, float, complex, bool, type(None), tuple)
                _require(all(type(value) in allowed for value in left | right) and left == right,
                         'native cache code frozenset differs')
            else:
                _require(kind in (str, bytes, int, float, complex, bool, type(None), type(Ellipsis))
                         and left == right, 'native cache code field differs')
    modules, alias_rows, code_rows, native_files = [], [], [], {}
    for name, module in sorted(snapshot.items()):
        _require(0 < len(name) <= 256, 'native module name exceeds bound')
        if name in ('typing.io', 'typing.re'):
            typing_module = snapshot.get('typing')
            _require(type(typing_module) is ModuleType, 'qualified typing namespace is missing')
            namespace = ModuleType.__getattribute__(typing_module, '__dict__')
            leaf = name.rsplit('.', 1)[1]
            _require(module is dict.get(namespace, leaf) and type(module) is dict.get(namespace, '_DeprecatedType'),
                     'fixed typing namespace alias differs')
            class_namespace = type.__getattribute__(module, '__dict__')
            _require(type(class_namespace) is MappingProxyType
                     and all(type(key) is str for key in class_namespace),
                     'fixed typing class namespace key type differs')
            _require(class_namespace.get('__module__') == 'typing', 'fixed typing namespace owner differs')
            alias_rows.append({'name': name, 'kind': 'qualified-typing-class-alias'})
            continue
        _require(type(module) is ModuleType, 'nonmodule native startup namespace refused')
        namespace = ModuleType.__getattribute__(module, '__dict__')
        _require(type(namespace) is dict and all(type(key) is str for key in namespace),
                 'native module namespace key type differs')
        if name == '__main__':
            _require(module is owner._initial_main and namespace is globals()
                     and namespace.get('__file__') == str(owner._snapshot.root / SELF),
                     'actual original native main differs')
            modules.append({'name': name, 'kind': 'original-source-main', 'source_sha256': _sha(owner._snapshot.read(SELF))})
            continue
        canonical = aliases.get(name, name)
        if canonical != name:
            _require(snapshot.get(canonical) is module, 'fixed native module alias differs')
            alias_rows.append({'name': name, 'canonical': canonical})
        spec = namespace.get('__spec__')
        _require(type(spec) is importlib.machinery.ModuleSpec, 'native module spec class differs')
        spec_data = object.__getattribute__(spec, '__dict__')
        _require(type(spec_data) is dict and all(type(key) is str for key in spec_data),
                 'native module spec namespace differs')
        loader, origin, spec_name = spec_data.get('loader'), spec_data.get('origin'), spec_data.get('name')
        _require(namespace.get('__loader__') is loader, 'native module loader association differs')
        if canonical in source_rows:
            source = source_rows[canonical]
            relative = 'install/python/lib/python3.12/' + source['member'].split('/Lib/', 1)[1]
            _require(relative in installed and installed[relative]['kind'] == 'regular_file',
                     'native source module outside qualified installed inventory')
            actual = _native_launch_read(('installed', relative), owner)
            _require(actual['sha256'] == source['sha256'] == installed[relative]['sha256']
                     and actual['size'] == source['bytes'], 'qualified native stdlib source differs')
            filename = _NATIVE_LAUNCH_ROOT + '/' + relative
            if origin == 'frozen':
                importlib_original = {'importlib._bootstrap': '_frozen_importlib',
                                      'importlib._bootstrap_external': '_frozen_importlib_external'}.get(canonical)
                if importlib_original is not None:
                    _require(name in (importlib_original, canonical)
                             and aliases.get(importlib_original) == canonical
                             and snapshot.get(importlib_original) is module
                             and snapshot.get(canonical) is module
                             and spec_name == importlib_original,
                             'fixed original importlib frozen alias association differs')
                frozen_name = name if importlib_original is None else importlib_original
                _require(frozen_name in frozen_names and loader is frozen_loader
                         and spec_name in frozen_names
                         and spec_name in (name, canonical, '_frozen_importlib', '_frozen_importlib_external')
                         and namespace.get('__file__') in (None, filename)
                         and namespace.get('__cached__') is None and spec_data.get('_cached') is None
                         and spec_data.get('_set_fileattr') is False,
                         'unlisted native frozen module')
                expected = compile(actual['raw'], '<frozen ' + canonical + '>', 'exec', dont_inherit=True, optimize=0)
                loaded = get_frozen(spec_name)
                _require(type(loaded) is CodeType, 'native frozen code object differs')
                equal_code(loaded, expected)
                code_rows.append({'name': name, 'kind': 'frozen', 'filename': expected.co_filename,
                                  'source_sha256': actual['sha256'], 'whole_code_tree_equal': True})
                kind = 'qualified-frozen'
            else:
                _require(type(loader) is source_loader and origin == filename and spec_name == name
                         and namespace.get('__file__') == filename,
                         'native source loader/spec/origin differs')
                loader_data = object.__getattribute__(loader, '__dict__')
                _require(type(loader_data) is dict and loader_data.get('name') == name
                         and loader_data.get('path') == filename, 'original native source-loader fields differ')
                # This is the pinned original cache_from_source rule for the
                # selected cache tag, pycache_prefix=None and optimization=''.
                source_path = Path(filename)
                cache = str(source_path.parent / '__pycache__' / (source_path.stem + '.cpython-312.pyc'))
                _require(namespace.get('__cached__') == cache and spec_data.get('_cached') == cache,
                         'native normal-cache association differs from the fixed loader rule')
                relative_cache = Path(cache).relative_to(_NATIVE_LAUNCH_ROOT).as_posix()
                expected = compile(actual['raw'], filename, 'exec', dont_inherit=True, optimize=0)
                cache_status = 'normal-cache-absent'
                if relative_cache in installed:
                    cache_data = _native_launch_read(('installed', relative_cache), owner)
                    _require(installed[relative_cache]['kind'] == 'regular_file'
                             and cache_data['sha256'] == installed[relative_cache]['sha256']
                             and len(cache_data['raw']) >= 16 and cache_data['raw'][:4] == b'\xcb\r\r\n',
                             'native normal-cache qualified bytes/header differ')
                    flags = int.from_bytes(cache_data['raw'][4:8], 'little')
                    _require(flags in (0, 1, 3), 'native normal-cache header flags differ')
                    if flags & 1:
                        source_hash = ModuleType.__getattribute__(machinery, '__dict__').get('source_hash')
                        _require(type(source_hash) is type(sys._getframe) and source_hash.__self__ is machinery
                                 and source_hash.__name__ == 'source_hash', 'native cache source-hash reader differs')
                        header_valid = cache_data['raw'][8:16] == source_hash(int.from_bytes(b'\xcb\r\r\n', 'little'), actual['raw'])
                    else:
                        header_valid = (int.from_bytes(cache_data['raw'][8:12], 'little')
                                        == (actual['identity'][7] // 1000000000 & 0xffffffff)
                                        and int.from_bytes(cache_data['raw'][12:16], 'little') == (actual['size'] & 0xffffffff))
                    loaded = marshal_loads(cache_data['raw'][16:])
                    _require(type(loaded) is CodeType, 'native normal-cache payload is not code')
                    equal_code(loaded, expected)
                    cache_status = ('normal-cache-qualified-header-and-code-equal' if header_valid else
                                    'normal-cache-unchecked-hash-source-equivalent' if flags == 1 else
                                    'normal-cache-ignored-invalid-header-but-qualified-code-equal')
                else:
                    _require(not os.path.lexists(cache), 'unlisted native normal cache refused')
                code_rows.append({'name': name, 'kind': 'source-normal-cache', 'filename': filename,
                                  'source_sha256': actual['sha256'], 'cache_path': cache,
                                  'cache_status': cache_status, 'whole_code_tree_equal': True})
                kind = 'qualified-source'
            modules.append({'name': name, 'kind': kind, 'source_path': filename, 'source_sha256': actual['sha256']})
        else:
            _require(name in native_names and name not in profile['platform_only_rejected_in_actual_Linux_prefix'],
                     'unlisted native startup module')
            if origin == 'built-in':
                _require(loader is builtin_loader and spec_name == name and name in sys.builtin_module_names,
                         'native builtin module loader differs')
                modules.append({'name': name, 'kind': 'qualified-ELF-builtin', 'ELF_sha256': _NATIVE_LAUNCH_PYTHON_SHA})
            else:
                _require(type(loader) is extension_loader and type(origin) is str and spec_name == name
                         and namespace.get('__file__') == origin
                         and str(Path(origin).parent) == _NATIVE_LAUNCH_ROOT + '/install/python/lib/python3.12/lib-dynload',
                         'native extension loader/origin differs')
                relative = Path(origin).relative_to(_NATIVE_LAUNCH_ROOT).as_posix()
                _require(relative in installed and installed[relative]['kind'] == 'regular_file',
                         'native extension outside qualified inventory')
                actual = _native_launch_read(('installed', relative), owner)
                _require(actual['sha256'] == installed[relative]['sha256'], 'native extension bytes differ')
                _native_launch_elf(actual['raw'])
                native_files[origin] = {'sha256': actual['sha256'], 'identity': actual['identity'], 'module': name}
                modules.append({'name': name, 'kind': 'qualified-native-extension',
                                'file': origin, 'sha256': actual['sha256']})
    _require(len(sys.modules) == len(snapshot) and all(sys.modules.get(name) is module for name, module in snapshot.items()),
             'native startup module dictionary changed during data check')
    return {'modules': modules, 'aliases': alias_rows, 'code': code_rows, 'native_files': native_files}


def _native_launch_host_closure(maps, native_rows):
    caller = sys._getframe(1)
    owner = caller.f_locals.get('owner')
    _require(caller.f_code is _native_before_node_first_launch.__code__ and caller.f_globals is globals()
             and type(owner) is _BootstrapLease, 'native host data needs the initial source caller')
    os_release = _native_launch_read('os-release', owner)
    os_values = {}
    for line in os_release['raw'].decode('utf-8').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            os_values[key] = value.strip('"')
    _require(os_values.get('ID') == 'ubuntu' and os_values.get('VERSION_ID') == '22.04'
             and os.uname().machine == 'x86_64', 'native admitted host platform differs')
    nss = _native_launch_read('nss', owner)
    host_lines = [line.split('#', 1)[0].strip() for line in nss['raw'].decode('ascii').splitlines()]
    host_lines = [match.group(1).split() for line in host_lines
                  if (match := re.fullmatch(r'hosts\s*:(.*)', line)) is not None]
    _require(host_lines == [['files', 'dns']], 'native resolver is outside the finite files/dns profile')
    try:
        preload = _native_launch_read('ld-preload', owner)
    except FileNotFoundError:
        preload = None
    if preload is not None:
        _require(not any(line.split('#', 1)[0].strip() for line in preload['raw'].decode('ascii').splitlines()),
                 'native host preload configuration refused')
    openssl = _native_launch_read('openssl', owner)
    _require(os.path.realpath('/usr/lib/ssl/openssl.cnf') == '/etc/ssl/openssl.cnf',
             'native default TLS configuration path differs')
    for line in openssl['raw'].decode('utf-8').splitlines():
        active = line.split('#', 1)[0].strip()
        _require(not re.match(r'(?i)(?:\.include\b|(?:providers|engines|module|dynamic_path)\s*=)', active),
                 'native external TLS provider configuration refused')
    ca = _native_launch_read('ca-file', owner)
    _require(os.path.realpath('/usr/lib/ssl/cert.pem') == ca['path'], 'native default CA path differs')
    for actual in (os_release, nss, openssl, ca, *([] if preload is None else [preload])):
        _require(actual['identity'][4] == 0 and actual['identity'][2] & 0o022 == 0,
                 'native host configuration is not protected root-owned data')
    paths = {}
    for row in maps:
        if row['category'] == 'file':
            paths.setdefault(row['path'], []).append(row)
    executable_path = _NATIVE_LAUNCH_ROOT + '/install/python/bin/python3.12'
    _require(executable_path in paths and all(path in paths for path in native_rows),
             'qualified native ELF has no actual file mapping')
    nodes, locales = {}, {}
    root_names = {executable_path, *native_rows}
    for path, rows in sorted(paths.items()):
        if path == executable_path or path in native_rows:
            relative = Path(path).relative_to(_NATIVE_LAUNCH_ROOT).as_posix()
            actual = _native_launch_read(('installed', relative), owner)
            expected_sha = _NATIVE_LAUNCH_PYTHON_SHA if path == executable_path else native_rows[path]['sha256']
            _require(actual['sha256'] == expected_sha, 'qualified mapped ELF bytes differ')
            category = 'qualified-ELF'
        elif str(Path(path).parent) == '/usr/lib/x86_64-linux-gnu':
            actual = _native_launch_read(('host-elf', path), owner)
            _require(actual['identity'][4] == 0 and actual['identity'][2] & 0o022 == 0,
                     'native host ELF is not root-owned protected data')
            category = 'host-ELF'
        elif path.startswith('/usr/lib/locale/'):
            actual = _native_launch_read(('locale-data', path.removeprefix('/usr/lib/locale/')), owner)
            _require(actual['identity'][4] == 0 and actual['identity'][2] & 0o022 == 0
                     and all('x' not in row['permissions'] and 'w' not in row['permissions']
                             and row['inode'] == actual['identity'][1]
                             and row['dev'] == [os.major(actual['identity'][0]), os.minor(actual['identity'][0])]
                             for row in rows),
                     'native host locale mapping differs')
            locales[path] = actual
            continue
        else:
            raise ValueError('unlisted native mapped file origin')
        for parent in Path(path).parents:
            parent_info = os.lstat(parent)
            if category == 'host-ELF':
                _require(stat.S_ISDIR(parent_info.st_mode) and parent_info.st_uid == 0
                         and parent_info.st_mode & 0o022 == 0,
                         'native host ELF ancestor is not protected root-owned data')
        info = actual['identity']
        _require(all(row['inode'] == info[1] and row['dev'] == [os.major(info[0]), os.minor(info[0])]
                     for row in rows), 'native mapped file inode/device differs')
        parsed = _native_launch_elf(actual['raw'])
        for row in rows:
            if 'x' in row['permissions']:
                _require(any(segment['flags'] & 1
                             and segment['offset'] // 4096 * 4096 <= row['offset']
                             and row['offset'] + row['end'] - row['start']
                             <= (segment['offset'] + segment['size'] + 4095) // 4096 * 4096
                             for segment in parsed['loads']), 'native executable map is outside its ELF load segment')
        for search in parsed['search_paths']:
            for item in search.split(':'):
                if item == '$ORIGIN' or item.startswith('$ORIGIN/'):
                    _require('$' not in item[len('$ORIGIN'):],
                             'native ELF origin search suffix contains a variable')
                    expanded = os.path.normpath(str(Path(path).parent) + item[len('$ORIGIN'):])
                    _require(expanded == _NATIVE_LAUNCH_ROOT + '/install/python'
                             or expanded.startswith(_NATIVE_LAUNCH_ROOT + '/install/python/'),
                             'native ELF origin search escapes qualified Python')
                else:
                    _require(item in ('/usr/lib/x86_64-linux-gnu', '/lib/x86_64-linux-gnu'),
                             'unlisted native ELF loader search path')
        nodes[path] = {'path': path, 'category': category, 'sha256': actual['sha256'],
                       'identity': info, 'ELF': parsed}
    _require(len(nodes) <= 128, 'native ELF graph exceeds bound')
    system_dir = '/usr/lib/x86_64-linux-gnu/'
    loader_path = system_dir + 'ld-linux-x86-64.so.2'
    _require(nodes[executable_path]['ELF']['interpreter'] == '/lib64/ld-linux-x86-64.so.2'
             and loader_path in nodes and os.readlink('/lib') == 'usr/lib'
             and os.readlink('/lib64') == 'usr/lib64', 'native original loader or fixed host aliases differ')
    loader_link = '/usr/lib64/ld-linux-x86-64.so.2'
    link_text = os.readlink(loader_link)
    _require(link_text in ('/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2', loader_path,
                          '../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2')
             and os.path.realpath(loader_link) == loader_path, 'native loader leaf alias differs')
    sonames = {}
    for path, row in nodes.items():
        name = row['ELF']['soname']
        if name is not None:
            _require(name not in sonames, 'native mapped SONAME is ambiguous')
            sonames[name] = path
    roots = root_names | {loader_path}
    for name in ('libnss_files.so.2', 'libnss_dns.so.2'):
        if name in sonames:
            roots.add(sonames[name])
    pending, seen, edges = list(roots), set(), []
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        _require(path in nodes, 'native ELF dependency root is missing')
        seen.add(path)
        for name in nodes[path]['ELF']['needed']:
            _require(name in sonames, 'native mapped dependency has no unique SONAME')
            target = sonames[name]
            edges.append({'from': path, 'needed': name, 'to': target})
            pending.append(target)
    _require(seen == set(nodes), 'native file graph contains an unexplained loaded ELF')
    # Attribute current host files to this admitted host package database only.
    # No package, ELF loader or external metadata program executes here.
    status = _native_launch_read('dpkg-status', owner)
    packages = {}
    for block in status['raw'].decode('utf-8').split('\n\n'):
        fields = {}
        for line in block.splitlines():
            if line.startswith(('Package: ', 'Version: ', 'Architecture: ', 'Status: ')):
                key, value = line.split(': ', 1)
                _require(key not in fields, 'duplicate native host package field')
                fields[key] = value
        if fields.get('Status') == 'install ok installed':
            _require(re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', fields.get('Package', ''))
                     and re.fullmatch(r'[a-z0-9][a-z0-9-]*', fields.get('Architecture', ''))
                     and 0 < len(fields.get('Version', '')) <= 512,
                     'invalid native installed package identity')
            packages[fields['Package'] + ':' + fields['Architecture']] = fields
    required = {path for path, row in nodes.items() if row['category'] == 'host-ELF'} | set(locales)
    owners, package_rows, metadata_bytes = {path: [] for path in required}, {}, 0
    list_names = sorted(name for name in os.listdir('/var/lib/dpkg/info') if name.endswith('.list'))
    _require(len(list_names) <= 20000, 'native package metadata file count exceeds bound')
    for name in list_names:
        listing = _native_launch_read(('package-list', name), owner)
        _require(listing['identity'][4] == 0 and listing['identity'][2] & 0o022 == 0,
                 'native package list is not protected host data')
        metadata_bytes += listing['size']
        _require(metadata_bytes <= 268435456, 'native package metadata exceeds total bound')
        matched = {}
        for item in listing['raw'].decode('utf-8').splitlines():
            if item.startswith('/lib/'):
                physical = '/usr' + item
            elif item.startswith('/lib64/'):
                physical = '/usr' + item
            else:
                physical = item
            if physical in required:
                matched[physical] = item
        if not matched:
            continue
        package = name[:-5]
        candidates = [key for key in packages if key == package or key.rsplit(':', 1)[0] == package]
        _require(len(candidates) == 1, 'native mapped file package owner is not uniquely installed')
        key = candidates[0]
        md5_name = package + '.md5sums'
        try:
            md5 = _native_launch_read(('package-md5', md5_name), owner)
        except FileNotFoundError:
            md5 = None
        checksums = {}
        if md5 is not None:
            _require(md5['identity'][4] == 0 and md5['identity'][2] & 0o022 == 0,
                     'native package checksum data is not protected')
            for line in md5['raw'].decode('ascii').splitlines():
                match = re.fullmatch(r'([0-9a-f]{32})  (.+)', line)
                _require(match is not None and match.group(2) not in checksums,
                         'native package checksum row differs')
                checksums[match.group(2)] = match.group(1)
        for path, listed in matched.items():
            actual = (locales[path] if path in locales else _native_launch_read(('host-elf', path), owner))
            expected_md5 = checksums.get(listed.removeprefix('/'))
            if expected_md5 is not None:
                _require(hashlib.md5(actual['raw'], usedforsecurity=False).hexdigest() == expected_md5,
                         'native host package file checksum differs')
            owners[path].append({'package': key, 'version': packages[key]['Version'],
                                 'package_list_sha256': listing['sha256'],
                                 'package_md5_metadata_sha256': None if md5 is None else md5['sha256'],
                                 'available_file_MD5_checked': expected_md5 is not None,
                                 'actual_file_sha256': actual['sha256']})
        package_rows[key] = {'version': packages[key]['Version'], 'list_sha256': listing['sha256'],
                             'md5_metadata_sha256': None if md5 is None else md5['sha256']}
    _require(all(rows for rows in owners.values()), 'native host mapped file has no installed package attribution')
    _require(sys.gettrace() is None and sys.getprofile() is None
             and all(sys.monitoring.get_tool(number) is None for number in range(6)),
             'native source-prefix instrumentation changed')
    public_nodes = [{key: row[key] for key in ('path', 'category', 'sha256', 'identity')}
                    | {'needed': row['ELF']['needed'], 'soname': row['ELF']['soname']}
                    for path, row in sorted(nodes.items())]
    return {'ELF_graph': {'nodes': public_nodes, 'edges': edges, 'fixed_loader_alias': link_text},
            'host': {'os_release_sha256': os_release['sha256'],
                     'kernel_public_identity': list(os.uname()),
                     'fixed_base_trust_label': 'existing GitHub-hosted Ubuntu22.04 x86_64 base; current observations only',
                     'system_files_and_package_metadata_sha256': {'status': status['sha256'],
                                                                  'packages': package_rows, 'files': owners},
                     'NSS_config_sha256': nss['sha256'],
                     'TLS_config_public_hashes': {'openssl_config': openssl['sha256'], 'CA_bundle': ca['sha256'],
                                                  'loader_preload': None if preload is None else preload['sha256']},
                     'host_files_are_not_capsule_pins': True}}


def _native_launch_report(owner, phase, public_row):
    caller = sys._getframe(1)
    _require(caller.f_code is _native_before_node_first_launch.__code__ and caller.f_globals is globals()
             and caller.f_locals.get('owner') is owner and type(owner) is _BootstrapLease
             and phase in ('complete', 'failure'), 'native evidence needs its original source caller')
    expected_directory = caller.f_locals.get('evidence_identity')
    _require(type(expected_directory) is tuple and len(expected_directory) == 5,
             'actual newly created native evidence directory required')
    allowed_top = {'version', 'status', 'scope', 'acceptance', 'source', 'github', 'runtime',
                   'process', 'environment', 'startup', 'host', 'sequence', 'failure'}
    _require(type(public_row) is dict and set(public_row) <= allowed_top
             and public_row.get('version') == 'som.native-before-node-first-launch-join.v1'
             and public_row.get('status') == ('INITIAL_SOURCE_DATA_JOIN_COMPLETE' if phase == 'complete' else 'FAILED')
             and public_row.get('acceptance') == {'SourceSeal': False, 'compiler_acceptance_passed': False,
                                                'product_acceptance_passed': False, 'whole_acceptance_complete': False},
             'native nonsecret evidence schema differs')
    if phase == 'complete':
        _require(set(public_row) == allowed_top - {'failure'} and public_row['sequence'] == [
            'source_workflow_bound', 'fixed_runtime_and_profile_joined', 'kernel_process_joined',
            'private_environment_checked', 'module_loader_and_host_graph_joined', 'stable_readback'],
            'native evidence may describe only completed pre-report stages')
    pending, count = [public_row], 0
    while pending:
        value = pending.pop()
        count += 1
        _require(count <= 250000, 'native nonsecret evidence structure exceeds bound')
        kind = type(value)
        if kind is dict:
            _require(all(type(key) is str and 0 < len(key) <= 4096 for key in value),
                     'native evidence key type or size differs')
            _require(not (set(value) & {'raw', 'Authorization', 'token', 'token_sha256', 'raw_environment',
                                        'exception_message', 'exception_repr', 'frame_locals'}),
                     'private or raw native evidence key refused')
            pending.extend(value.values())
        elif kind is list or kind is tuple:
            _require(len(value) <= 8192, 'native evidence array exceeds bound')
            pending.extend(value)
        elif kind is str:
            _require(len(value) <= 4096 and '\0' not in value, 'native evidence string exceeds bound')
        else:
            _require(kind in (int, bool, type(None)), 'native evidence has a nondata value')
    raw = (json.dumps(public_row, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode()
    _require(len(raw) <= 16777216, 'native nonsecret report exceeds bound')
    leaf = 'initial-launch.json' if phase == 'complete' else 'failure.json'
    directory_fd, report_fd, primary = None, None, None
    try:
        _physical_directory(Path(_NATIVE_LAUNCH_EVIDENCE))
        directory_fd = os.open(_NATIVE_LAUNCH_EVIDENCE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(directory_fd)
        _require((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) == expected_directory,
                 'native evidence directory ownership changed')
        report_fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                            0o600, dir_fd=directory_fd)
        initial = os.fstat(report_fd)
        _require(stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1
                 and stat.S_IMODE(initial.st_mode) == 0o600, 'native evidence report ownership differs')
        offset = 0
        while offset < len(raw):
            written = os.write(report_fd, raw[offset:])
            _require(type(written) is int and written > 0, 'native evidence write made no progress')
            offset += written
        os.fsync(report_fd)
        final = os.fstat(report_fd)
        _require((initial.st_dev, initial.st_ino, initial.st_mode, initial.st_nlink)
                 == (final.st_dev, final.st_ino, final.st_mode, final.st_nlink)
                 and final.st_size == len(raw), 'native evidence report changed while writing')
    except BaseException as error:
        primary = error
        raise
    finally:
        close_error = None
        for fd in (report_fd, directory_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException as secondary:
                    if primary is not None:
                        _preserve_primary(primary, 'native-launch-report-close', secondary)
                    elif close_error is None:
                        close_error = secondary
                    else:
                        _preserve_primary(close_error, 'native-launch-report-close', secondary)
        if primary is None and close_error is not None:
            raise close_error
    # The report deliberately makes no earlier claim about this close/flush.
    # The actual complete official job marker and its report digest bind order.
    marker = {'version': public_row['version'], 'status': public_row['status'],
              'repository': owner._binding['repository'], 'run_id': owner._binding['run_id'],
              'attempt': owner._binding['run_attempt'], 'job_id': owner._binding['job_id'],
              'candidate': owner._snapshot.revision, 'pid': os.getpid(),
              'report_relative_leaf': leaf, 'report_size': len(raw), 'report_sha256': _sha(raw)}
    if phase == 'complete':
        marker['starttime'] = public_row['process']['starttime']
    print('SOM_NATIVE_BEFORE_NODE_INITIAL_JOIN ' + json.dumps(marker, sort_keys=True, separators=(',', ':')), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--helper-revision', required=True)
    parser.add_argument('--candidate-revision', required=True)
    parser.add_argument('--entry-role', required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    values = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    with checked_bootstrap(args.repo, args.helper_revision, args.candidate_revision) as bootstrap:
        if (bootstrap._binding['job'] == 'required-callbacks' and args.entry_role == 'entry.ts-input-consumer'
                and values[-1:] == ['before-node']):
            _native_before_node_first_launch(bootstrap)
        return bootstrap.execute_entry(args.entry_role, values)


if __name__ == '__main__':
    raise SystemExit(main())
