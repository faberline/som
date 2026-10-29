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
                           BASE + 'run_current_candidate_gate.py', BASE + 'run_same_candidate_contracts.py', M6_ENTRY),
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
        return bootstrap.execute_entry(args.entry_role, values)


if __name__ == '__main__':
    raise SystemExit(main())
