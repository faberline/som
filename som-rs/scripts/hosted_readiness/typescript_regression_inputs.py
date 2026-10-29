"""Explicit source-profile input reads for the old Rust regression helpers.

No ancestor .local path is a fallback in native mode. The old helpers' normal
historical input selection stays unchanged outside the isolated owner wrapper.
"""
from __future__ import annotations
import hashlib
import ast
import copy
import json
import math
from pathlib import Path
import re
import stat
import sys
from types import ModuleType

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_inputs.py'
PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'
BASELINE = 'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py'
PHASE2 = 'som-rs/behavior/phase2/check_phase2.py'
INVENTORY = 'som-rs/contracts/rust-output-v1/phase2/case-inventory.json'
PHASE2_ROLE = 'ts.inputs.original-phase2'
_NATIVE_VARIANTS = {
    '_som_full_typescript_regression_named_inputs': ('ts.inputs.adapter.live', 'ts.inputs.live'),
    '_som_full_typescript_regression_inputs': ('ts.inputs.adapter.live-teardown', 'ts.inputs.live-teardown'),
    '_som_ts_domain_typescript_http_replay_input_adapter': ('ts.domain.typescript-http-replay-input-adapter', 'ts.inputs.domain-http'),
    '_som_ts_domain_typescript_unchanged_explicit_inputs': ('ts.domain.typescript-unchanged-explicit-inputs', 'ts.inputs.domain-unchanged'),
    '_som_ts_domain_ts_main_replay_3': ('ts.domain.ts-main-replay-3', 'ts.inputs.domain-main'),
    '_som_ts_domain_explicit_input_teardown': ('ts.domain.explicit-input-teardown', 'ts.inputs.domain-teardown'),
}
_TARGETS = {
    ('_som_ts_domain_typescript_http_replay_original_rust', BASELINE): 'ts.domain.typescript-http-replay-original-rust',
    ('_som_ts_domain_typescript_unchanged_original_checker', 'som-rs/scripts/rust_output_acceptance/check_rust_unchanged.py'): 'ts.domain.typescript-unchanged-original-checker',
    ('_som_ts_native_original_rust_baseline', BASELINE): 'ts.checker.original-rust-baseline',
    ('_som_ts_native_original_rust_unchanged', 'som-rs/scripts/rust_output_acceptance/check_rust_unchanged.py'): 'ts.checker.original-rust-unchanged',
}


def _controls():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    return controls


def _roles():
    controls = _controls()
    require(type(__name__) is str and type(_NATIVE_VARIANTS) is dict,
            'fixed native input adapter namespace required')
    selected = _NATIVE_VARIANTS.get(__name__)
    require(type(selected) is tuple and len(selected) == 2
            and all(type(value) is str for value in selected), 'unlisted native input adapter namespace')
    require(controls.role_sources.get(selected[0]) == SELF, 'native input adapter role source differs')
    return controls, selected


def _plain(value):
    kind = type(value)
    if kind in {str, int, bool, type(None)}:
        return value
    if kind is float:
        require(math.isfinite(value), 'finite input metadata number required')
        return value
    if kind is list:
        return [_plain(item) for item in value]
    require(kind is dict and all(type(key) is str for key in value), 'plain input metadata required')
    return {key: _plain(item) for key, item in value.items()}


class _Cache:
    def __init__(self, owner):
        self.owner = owner

    def get(self, key, default=None):
        return json.loads(self.owner.cache_data).get(key, default)

    def __contains__(self, key):
        return key in json.loads(self.owner.cache_data)

    def __getitem__(self, key):
        return json.loads(self.owner.cache_data)[key]

    def __setitem__(self, key, value):
        require(type(key) is str, 'plain input cache key required')
        data = json.loads(self.owner.cache_data)
        data[key] = _plain(value)
        self.owner.cache_data = json.dumps(data, ensure_ascii=False, allow_nan=False)


class _View:
    def __init__(self, repo, helper, candidate, settings, cache_data='{}'):
        self.repo, self.helper, self.candidate = repo, helper, candidate
        self.settings, self.cache_data = settings, cache_data

    @property
    def sources(self):
        controls = _controls()
        require(type(self.repo) is type(controls.root) and self.repo == controls.root
                and self.helper == controls.helper_revision and self.candidate == controls.candidate_revision,
                'actual input source root/revision required')
        return __som_native_SourceBytes__(self.repo, self.helper)

    @property
    def data(self):
        return {'typescript_regression_inputs': json.loads(self.settings)['bindings']}

    @property
    def cache(self):
        return _Cache(self)

    @property
    def qa_mode(self):
        return 'native-qa-v1'

    @property
    def qa_source_authority(self):
        return json.loads(self.settings)['admission']

    def read_path(self, value):
        value = Path(value).absolute()
        repo = Path(self.repo).absolute()
        if repo in value.parents:
            return json.loads(self.sources.read(value.relative_to(repo).as_posix()))
        data = json.loads(self.settings)['bindings']
        for role, root in {**data['roots'], **data['siblings']}.items():
            root = Path(root).absolute()
            if root in value.parents:
                return read_input(self, role, value.relative_to(root).as_posix())
        raise ValueError('phase2 attempted an undeclared ancestor input')

    def __call__(self, value):
        return self.read_path(value)


class _Records(_View):
    @property
    def cases(self):
        return self

    def __call__(self):
        raise ValueError('fresh Rust gate requires every explicit saved phase2 record')

    def read(self, value):
        return self.read_path(value)

    def key(self, text):
        return re.sub(r'[^A-Za-z0-9]+', '_', text).strip('_')


class _Lookup(_View):
    def __call__(self, case_id):
        return lookup_prompt(self, case_id)


class _Cases(_View):
    def __call__(self):
        return fresh_cases(self, None, None)


class _Environment:
    @property
    def environ(self):
        return {}


class _Qa:
    def sha(self, value):
        return sha(value)


class _Post:
    def __init__(self, repo, helper, candidate, role):
        self.repo, self.helper, self.candidate, self.role = repo, helper, candidate, role

    def __call__(self, *args, **kwargs):
        controls = _controls()
        require(self.repo == controls.root and self.helper == controls.helper_revision
                and self.candidate == controls.candidate_revision and self.role == PHASE2_ROLE
                and controls.role_sources.get(self.role) == PHASE2,
                'fixed current phase2 callback source required')
        module = controls.load_module(PHASE2_ROLE)
        # Core checks the current original or source-owned replay callback.
        return module.post(*args, **kwargs)


def _view(ctx, kind):
    controls, unused = _roles()
    if type(ctx) in {_View, _Records, _Lookup, _Cases}:
        return kind(ctx.repo, ctx.helper, ctx.candidate, ctx.settings, ctx.cache_data)
    require(Path(ctx.repo).absolute() == controls.root
            and ctx.helper_revision == controls.helper_revision
            and ctx.candidate_revision == controls.candidate_revision, 'actual input Context root/revision required')
    data = ctx.cache.get('typescript_replay_input_bindings', ctx.data['typescript_regression_inputs'])
    settings = json.dumps(_plain({'bindings': data, 'admission': ctx.qa_source_authority}),
                          ensure_ascii=False, allow_nan=False)
    return kind(controls.root, controls.helper_revision, controls.candidate_revision, settings)


class _Acquisition:
    def __init__(self, originals, installed, absent, scope, lease):
        self.originals, self.installed, self.absent = originals, installed, absent
        self.scope, self.lease, self.closed = scope, lease, False

    def __call__(self):
        if self.closed:
            return
        failures, errors = [], []
        for (owner, key, original), (_, _, selected) in zip(reversed(self.originals), reversed(self.installed)):
            try:
                current = getattr(owner, key, self.absent)
                if current is not selected:
                    failures.append(key)
                    continue
                if original is self.absent:
                    if current is not self.absent:
                        delattr(owner, key)
                else:
                    setattr(owner, key, original)
            except BaseException as error:
                failures.append(key)
                errors.append((error, error.__traceback__))
        try:
            require(not failures, 'owned explicit input binding replaced: ' + ','.join(failures))
        except BaseException as error:
            errors.append((error, error.__traceback__))
        for action in (self.scope.close, self.lease.close):
            try:
                action()
            except BaseException as error:
                errors.append((error, error.__traceback__))
        self.closed = True
        if errors:
            primary, trace = errors[0]
            _secondary(primary, '__som_input_cleanup_secondary__',
                       tuple(error for error, unused in errors[1:]))
            raise primary.with_traceback(trace)


def _secondary(primary, name, errors):
    """Keep nested cleanup evidence when this layer adds more errors."""
    try:
        previous = BaseException.__getattribute__(primary, name)
    except AttributeError:
        previous = ()
    if type(previous) is not tuple:
        previous = (previous,)
    BaseException.__setattr__(primary, name, previous + errors)


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value: Any) -> str:
    """Implement the frozen canonical JSON rules for fixture/hash checking."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite JSON number")
        s = repr(value)
        if "e" in s:
            a, b = s.split("e", 1)
            sign = "-" if b.startswith("-") else "+" if b.startswith("+") else ""
            digits = b.lstrip("+-")
            if sign == "-" and len(digits) < 2:
                digits = digits.zfill(2)
            s = a + "e" + sign + digits
        return s
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is list:
        return "[" + ",".join(canonical(x) for x in value) + "]"
    if type(value) is dict:
        if any(type(k) is not str for k in value):
            raise TypeError("JSON object keys must be strings")
        return "{" + ",".join(canonical(k) + ":" + canonical(value[k]) for k in sorted(value)) + "}"
    raise TypeError(f"unsupported JSON type: {type(value).__name__}")


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_input(ctx, role, relative):
    require(type(relative) is str and relative and not relative.startswith('/')
            and all(part not in {'', '.', '..'} for part in relative.split('/')), 'safe named input member required')
    profile = json.loads(ctx.sources.read(PROFILE))
    bindings = getattr(ctx, 'cache', {}).get('typescript_replay_input_bindings', ctx.data['typescript_regression_inputs'])
    sibling = role == 'rs_prompts'
    root = Path(bindings['siblings' if sibling else 'roots'][role]).absolute()
    expected = profile['siblings' if sibling else 'roles'][role]['files'][relative]
    path = root / relative
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'typed prompt input must be a regular file')
    info = path.stat(); raw = path.read_bytes()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
            {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw), 'mode': stat.S_IMODE(info.st_mode)} == expected,
            'typed prompt input differs from its complete source profile')
    return json.loads(raw)


def lookup_prompt(ctx, case_id):
    require(ctx.qa_mode == 'native-qa-v1' and SELF in ctx.qa_source_authority['admitted_repository_python_sources'],
            'exact native prompt adapter source admission required')
    key = 'typescript_regression_static_prompt_map'
    if key not in ctx.cache:
        values = {}
        prefix = 'som-rs/contracts/compiler-v1/examples/'
        for name in ('v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v7-branch-null'):
            values['example-' + name] = json.loads(ctx.sources.read(prefix + name + '.prompt.json'))
        fixtures = json.loads(ctx.sources.read('som-rs/tests/compiler-v1/cnl-positive-fixtures.json'))
        values.update({row['id']: row['expected_prompt'] for row in fixtures['cases']})
        prefix = 'som-rs/tests/rust-output-v1/cases/'
        for row in json.loads(ctx.sources.read(prefix + 'index.json'))['cases']:
            values[row['id']] = json.loads(ctx.sources.read(prefix + row['prompt']))
        ctx.cache[key] = values
    static = ctx.cache[key]
    if case_id in static:
        return static[case_id]
    short = case_id.split('/')[-1] if case_id.startswith('rs/') else case_id
    if short.startswith('RS'):
        return read_input(ctx, 'rs_prompts', short + '/prompt.json')
    if case_id.startswith('PUBLIC_'):
        return static.get('example-' + case_id[len('PUBLIC_'):].replace('_', '-'))
    groups = read_input(ctx, 'legacy_run', 'inventory-003.json')['groups']
    for index, group in enumerate(groups):
        if group['id'] != case_id:
            continue
        files = json.loads(ctx.sources.read(PROFILE))['roles']['legacy_run']['files']
        candidates = []
        if group.get('prompt_sha256'):
            candidates.append(('run-005/compile/' + group['prompt_sha256'] + '/compile-json.request.json', 'prompt'))
        candidates += [(f'run-005/{index:04d}/assemble.request.json', 'bundle'),
                       (f'run-005/{index:04d}/variant.bundle.json', None)]
        for relative, field in candidates:
            if relative not in files:
                continue
            document = read_input(ctx, 'legacy_run', relative)
            return document['prompt'] if field == 'prompt' else (
                document['bundle']['prompt'] if field == 'bundle' else document['prompt'])
        return None
    return None


def fresh_cases(ctx, phase2, checked_read):
    """Use the original record-spec builder for all584 explicit saved records."""
    controls, variant = _roles()
    require(BASELINE in ctx.qa_source_authority['admitted_repository_python_sources'],
            'original Rust baseline record loader must be admitted')
    reader = _view(ctx, _View)
    raw = reader.sources.read(BASELINE); tree = ast.parse(raw)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name == '_discover_phase2_case_specs']
    require(len(nodes) == 1 and not nodes[0].decorator_list, 'exact original record-spec builder required')
    phase2_view, lookup_view, qa_view = _view(ctx, _Records), _view(ctx, _Lookup), _Qa()
    # No ancestor selection runs. Equality with this original repository-local
    # default reaches the explicit no-fallback refusal in the unchanged body.
    default_run = controls.root / '.local/rust-compiler-goal/acceptance/rust-output-055/run-003'
    bindings = ctx.cache.get('typescript_replay_input_bindings', ctx.data['typescript_regression_inputs'])
    run = Path(bindings['roots']['rust_run'])
    with controls.load_fragment(variant[1] + '.records') as baseline:
        specs = baseline._discover_phase2_case_specs(run)
        inventory = json.loads(reader.sources.read('som-rs/contracts/rust-output-v1/phase2/case-inventory.json'))['rows']
        require(len(specs) == len(inventory) == 584 and len({row['id'] for row in specs}) == 584
                and {row['id'] for row in specs} == {row['id'] for row in inventory},
                'all584 named original Rust phase2 records required')
        for spec in specs:
            prompt = spec['load_prompt']()
            require(type(prompt) is dict and baseline.canonical_sha256(prompt) == spec['prompt_sha256'],
                    'every original named typed Rust prompt must match its recorded source hash')
            spec['load_prompt'] = lambda prompt=copy.deepcopy(prompt): copy.deepcopy(prompt)
        return specs


def _target(module):
    controls = _controls()
    require(type(module) is ModuleType and type(module.__file__) is str
            and type(module.__name__) is str, 'actual Core-owned Rust checker module required')
    file = Path(module.__file__).absolute()
    require(controls.root in file.parents, 'Rust checker source is outside actual repository')
    path = file.relative_to(controls.root).as_posix()
    role = _TARGETS.get((module.__name__, path))
    require(type(role) is str and controls.role_sources.get(role) == path,
            'unlisted fixed Rust checker source/namespace')
    require(controls.load_module(role) is module, 'Rust checker is not the actual Core-owned module')
    phase2 = controls.load_module(PHASE2_ROLE)
    require(controls.role_sources.get(PHASE2_ROLE) == PHASE2 and module.phase2 is phase2,
            'actual fixed phase2 module required')
    return phase2


def configure(ctx, module):
    controls, variant = _roles()
    require(ctx.qa_mode == 'native-qa-v1' and SELF in ctx.qa_source_authority['admitted_repository_python_sources'],
            'exact native input adapter source admission required')
    data = ctx.cache.get('typescript_replay_input_bindings', ctx.data['typescript_regression_inputs'])
    phase2 = _target(module)
    absent = object()
    originals = [(phase2, key, getattr(phase2, key, absent))
                 for key in ('PUBLIC', 'LEGACY', 'RS', 'cases', 'rust_files')]
    originals += [(module, key, getattr(module, key, absent))
                  for key in ('_lookup_prompt_by_id', '_ORIG_PHASE2_CASES')]
    path = 'som-rs/behavior/phase2/check_phase2.py'
    require(path in ctx.qa_source_authority['admitted_repository_python_sources'],
            'original phase2 assertion source must be admitted')
    reader = _view(ctx, _View)
    raw = reader.sources.read(path); tree = ast.parse(raw)
    wanted = {'rust_files'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    require(len(nodes) == 1 and {node.name for node in nodes} == wanted
            and all(not node.decorator_list for node in nodes), 'exact phase2 assertion functions required')
    read_view, lookup_view, case_view = _view(ctx, _View), _view(ctx, _Lookup), _view(ctx, _Cases)
    environment_view, qa_view = _Environment(), _Qa()
    post_view = _Post(controls.root, controls.helper_revision, controls.candidate_revision, PHASE2_ROLE)
    inventory_path = controls.root / INVENTORY
    public = Path(data['roots']['public_run'])
    legacy = Path(data['roots']['legacy_run']) / 'run-005'
    rs = Path(data['siblings']['rs_prompts'])
    lease, scope, acquisition = controls.borrow(), None, None
    installed, changed_originals = [], []
    def install(owner, key, value):
        original = next(row[2] for row in originals if row[0] is owner and row[1] == key)
        setattr(owner, key, value)
        changed_originals.append((owner, key, original))
        installed.append((owner, key, value))
    try:
        scope = lease.load_fragment(variant[1] + '.rust-files')
        acquisition = _Acquisition(changed_originals, installed, absent, scope, lease)
        install(phase2, 'PUBLIC', public)
        install(phase2, 'LEGACY', legacy)
        install(phase2, 'RS', rs)
        if hasattr(module, '_lookup_prompt_by_id'):
            install(module, '_lookup_prompt_by_id', lookup_view.__call__)
        install(phase2, 'cases', case_view.__call__)
        install(phase2, 'rust_files', scope.rust_files)
        if hasattr(module, '_ORIG_PHASE2_CASES'):
            install(module, '_ORIG_PHASE2_CASES', phase2.cases)
        ctx.cache.setdefault('typescript_regression_input_binding_closers', []).append(acquisition)
        return {'mode': 'native-qa-v1', 'ancestor_fallback': False,
                'public_run': str(phase2.PUBLIC), 'legacy_run': str(phase2.LEGACY),
                'rs_prompts': str(phase2.RS), 'original_assertion_source_sha256': hashlib.sha256(raw).hexdigest(),
                'original_assertion_AST_sha256': {node.name: hashlib.sha256(ast.dump(node,
                    include_attributes=False).encode()).hexdigest() for node in nodes},
                'fresh_current_http': True, 'real_environment_changed': False, 'product_acceptance_passed': False}
    except BaseException as primary:
        cleanup_errors = []
        if acquisition is not None:
            try:
                acquisition()
            except BaseException as secondary:
                cleanup_errors.append(secondary)
        else:
            if scope is not None:
                try:
                    scope.close()
                except BaseException as secondary:
                    cleanup_errors.append(secondary)
            try:
                lease.close()
            except BaseException as secondary:
                cleanup_errors.append(secondary)
        if cleanup_errors:
            _secondary(primary, '__som_input_acquire_cleanup__', tuple(cleanup_errors))
        raise


def close(ctx):
    """Restore only bindings still owned by this acquisition, in reverse order."""
    incoming = sys.exc_info()[1]
    incoming_trace = incoming.__traceback__ if incoming is not None else None
    try:
        controls = _controls()
    except BaseException as error:
        if incoming is not None:
            _secondary(incoming, '__som_input_cleanup_secondary__', (error,))
            raise incoming.with_traceback(incoming_trace)
        raise
    failures, errors = [], []
    for callback in reversed(ctx.cache.pop('typescript_regression_input_binding_closers', [])):
        try:
            cls = type(callback)
            require(type(cls) is type, 'source-owned input acquisition type required')
            name = type.__getattribute__(cls, '__dict__').get('__module__')
            require(type(_NATIVE_VARIANTS) is dict and type(name) is str and name in _NATIVE_VARIANTS,
                    'unlisted input acquisition source namespace')
            selected = _NATIVE_VARIANTS[name]
            require(type(selected) is tuple and len(selected) == 2
                    and all(type(value) is str for value in selected), 'fixed input acquisition type role required')
            role = selected[0]
            require(controls.role_sources.get(role) == SELF, 'input acquisition role source differs')
            owner = controls.load_module(role)
            require(type(callback) is owner._Acquisition, 'actual Core-owned input acquisition required')
            callback()
        except BaseException as error:
            failures.append({'error': type(error).__name__, 'reason': str(error)})
            errors.append((error, error.__traceback__))
    try:
        require(not failures, 'explicit input binding cleanup failed: ' + json.dumps(failures, sort_keys=True))
    except BaseException as summary:
        if incoming is not None:
            _secondary(incoming, '__som_input_cleanup_secondary__',
                       tuple(error for error, unused in errors))
            BaseException.__setattr__(incoming, '__som_input_cleanup_summary__', str(summary))
            raise incoming.with_traceback(incoming_trace)
        if errors:
            primary, trace = errors[0]
            _secondary(primary, '__som_input_cleanup_secondary__',
                       tuple(error for error, unused in errors[1:]))
            BaseException.__setattr__(primary, '__som_input_cleanup_summary__', str(summary))
            raise primary.with_traceback(trace)
        raise
