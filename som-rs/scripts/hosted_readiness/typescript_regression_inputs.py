"""Explicit source-profile input reads for the old Rust regression helpers.

No ancestor .local path is a fallback in native mode. The old helpers' normal
historical input selection stays unchanged outside the isolated owner wrapper.
"""
from __future__ import annotations
import hashlib
import ast
import copy
import json
from pathlib import Path
import stat
from types import SimpleNamespace

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_inputs.py'
PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'
BASELINE = 'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py'


def require(value, message):
    if not value:
        raise ValueError(message)


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
    require(BASELINE in ctx.qa_source_authority['admitted_repository_python_sources'],
            'original Rust baseline record loader must be admitted')
    baseline = ctx.module('typescript_fresh_case_record_loader', BASELINE)
    raw = ctx.sources.read(BASELINE); tree = ast.parse(raw)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name == '_discover_phase2_case_specs']
    require(len(nodes) == 1 and not nodes[0].decorator_list, 'exact original record-spec builder required')
    def no_fallback():
        raise ValueError('fresh Rust gate requires every explicit saved phase2 record')
    proxy = SimpleNamespace(**{key: value for key, value in vars(phase2).items()
                               if key not in {'cases', 'read'}}, cases=no_fallback, read=checked_read)
    scope = dict(vars(baseline)); scope.update(phase2=proxy, _ORIG_PHASE2_CASES=no_fallback,
                                               _lookup_prompt_by_id=lambda cid: lookup_prompt(ctx, cid))
    selected = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                                *nodes], type_ignores=[]); ast.fix_missing_locations(selected)
    exec(compile(selected, str(Path(ctx.repo) / BASELINE), 'exec', dont_inherit=True), scope)
    bindings = ctx.cache.get('typescript_replay_input_bindings', ctx.data['typescript_regression_inputs'])
    run = Path(bindings['roots']['rust_run'])
    specs = scope['_discover_phase2_case_specs'](run)
    inventory = json.loads(ctx.sources.read('som-rs/contracts/rust-output-v1/phase2/case-inventory.json'))['rows']
    require(len(specs) == len(inventory) == 584 and len({row['id'] for row in specs}) == 584
            and {row['id'] for row in specs} == {row['id'] for row in inventory},
            'all584 named original Rust phase2 records required')
    for spec in specs:
        prompt = spec['load_prompt']()
        require(type(prompt) is dict and baseline.canonical_sha256(prompt) == spec['prompt_sha256'],
                'every original named typed Rust prompt must match its recorded source hash')
        spec['load_prompt'] = lambda prompt=copy.deepcopy(prompt): copy.deepcopy(prompt)
    return specs


def configure(ctx, module):
    require(ctx.qa_mode == 'native-qa-v1' and SELF in ctx.qa_source_authority['admitted_repository_python_sources'],
            'exact native input adapter source admission required')
    data = ctx.cache.get('typescript_replay_input_bindings', ctx.data['typescript_regression_inputs'])
    phase2 = module.phase2
    absent = object()
    originals = [(phase2, key, getattr(phase2, key, absent))
                 for key in ('PUBLIC', 'LEGACY', 'RS', 'cases', 'rust_files')]
    originals += [(module, key, getattr(module, key, absent))
                  for key in ('_lookup_prompt_by_id', '_ORIG_PHASE2_CASES')]
    # The owner checks a live current compiler. Preserve the exact original
    # assertion AST while selecting its HTTP/data branch in an explicit fresh
    # namespace. The real environment and the checked Context stay unchanged.
    path = 'som-rs/behavior/phase2/check_phase2.py'
    require(path in ctx.qa_source_authority['admitted_repository_python_sources'],
            'original phase2 assertion source must be admitted')
    raw = ctx.sources.read(path); tree = ast.parse(raw)
    wanted = {'rust_files'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    require(len(nodes) == 1 and {node.name for node in nodes} == wanted
            and all(not node.decorator_list for node in nodes), 'exact phase2 assertion functions required')
    scope = dict(vars(phase2)); scope['os'] = SimpleNamespace(environ={})
    scope.update(PUBLIC=Path(data['roots']['public_run']),
                 LEGACY=Path(data['roots']['legacy_run']) / 'run-005',
                 RS=Path(data['siblings']['rs_prompts']))
    def checked_read(value):
        value = Path(value).absolute()
        repo = Path(ctx.repo).absolute()
        if repo in value.parents:
            return json.loads(ctx.sources.read(value.relative_to(repo).as_posix()))
        for role, root in {**data['roots'], **data['siblings']}.items():
            root = Path(root).absolute()
            if root in value.parents:
                return read_input(ctx, role, value.relative_to(root).as_posix())
        raise ValueError('phase2 attempted an undeclared ancestor input')
    scope['read'] = checked_read
    selected = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                                *nodes], type_ignores=[])
    ast.fix_missing_locations(selected)
    exec(compile(selected, str(Path(ctx.repo) / path), 'exec', dont_inherit=True), scope)
    phase2.PUBLIC, phase2.LEGACY, phase2.RS = scope['PUBLIC'], scope['LEGACY'], scope['RS']
    if hasattr(module, '_lookup_prompt_by_id'):
        module._lookup_prompt_by_id = lambda case_id: lookup_prompt(ctx, case_id)
    phase2.cases, phase2.rust_files = lambda: fresh_cases(ctx, phase2, checked_read), scope['rust_files']
    if hasattr(module, '_ORIG_PHASE2_CASES'):
        module._ORIG_PHASE2_CASES = phase2.cases
    installed = [(owner, key, getattr(owner, key, absent)) for owner, key, unused in originals]
    def close_bindings():
        failures = []
        for (owner, key, original), (_, _, selected) in zip(reversed(originals), reversed(installed)):
            current = getattr(owner, key, absent)
            if current is not selected:
                failures.append(key)
                continue
            if original is absent:
                if current is not absent:
                    delattr(owner, key)
            else:
                setattr(owner, key, original)
        require(not failures, 'owned explicit input binding replaced: ' + ','.join(failures))
    ctx.cache.setdefault('typescript_regression_input_binding_closers', []).append(close_bindings)
    return {'mode': 'native-qa-v1', 'ancestor_fallback': False,
            'public_run': str(phase2.PUBLIC), 'legacy_run': str(phase2.LEGACY),
            'rs_prompts': str(phase2.RS), 'original_assertion_source_sha256': hashlib.sha256(raw).hexdigest(),
            'original_assertion_AST_sha256': {node.name: hashlib.sha256(ast.dump(node,
                include_attributes=False).encode()).hexdigest() for node in nodes},
            'fresh_current_http': True, 'real_environment_changed': False, 'product_acceptance_passed': False}


def close(ctx):
    """Restore only bindings still owned by this acquisition, in reverse order."""
    failures = []
    for callback in reversed(ctx.cache.pop('typescript_regression_input_binding_closers', [])):
        try:
            callback()
        except BaseException as error:
            failures.append({'error': type(error).__name__, 'reason': str(error)})
    require(not failures, 'explicit input binding cleanup failed: ' + json.dumps(failures, sort_keys=True))
