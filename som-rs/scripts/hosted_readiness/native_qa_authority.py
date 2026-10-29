"""Check exact numbered native QA authority before any substituted QA code runs.

This checks source authority only. It grants no compiler or product acceptance.
All subject execution and fixtures belong on GitHub runners.
"""
from __future__ import annotations

import hashlib
import ast
import importlib.abc
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import json
import os
import re

SELF = 'som-rs/scripts/hosted_readiness/native_qa_authority.py'
MODE = 'native-qa-v1'
REVIEW_SCOPE = 'native_policy_validator_and_exact_qa_source_maps'
AUTHORITY = {
    'compiler': (5, 'rust-compiler', 'compiler-v1/native-tools.json',
                 '2f0c12e6fed43b8364649a0afff207e72b15738179cf56a8ff69036e4534f42c'),
    'rust': (8, 'rust-output', 'rust-output-v1/native-tools-compatibility.json',
             '225b98c2bc01e79c5c6e13ad17f93adda68717498d70fa7577e65930813f9144'),
    'typescript': (4, 'typescript-output', 'typescript-output-v1/native-tools-compatibility.json',
                   '7052daacfc89bf468e399e09f635e6d773187177eb684a0399d46d8ddf650089'),
}
REPLACEMENTS = {'som-rs/behavior/hosts.py', 'som-rs/behavior/phase2/check_phase2.py',
                'som-rs/behavior/phase2/phase2_host.py', 'som-core/scripts/m3_local_fixture.py'}
ORDER = {'version': 'som.native-acceptance-chronology.v1', 'implementation_already_existed': True,
         'prior_work_relabelled_as_preimplementation': False, 'scope': 'native_tools_and_metadata_only',
         'acceptance_rule': 'prospective_full_same_candidate_gates', 'historical_receipt_bytes_changed': False,
         'historical_results_promoted_to_current_acceptance': False}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def parse(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate source authority member')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite source authority member')))


def name(value):
    require(type(value) is str and value and not value.startswith('/') and '\\' not in value
            and all(part not in {'', '.', '..'} for part in value.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'safe exact source name required')
    return value


def source_map(reader, values):
    require(type(values) is dict and values, 'complete source map required')
    for path, pin in values.items():
        require(type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin)
                and sha(reader(name(path))) == pin, 'exact admitted QA source changed: ' + str(path))


def typed_equal(left, right):
    if type(left) is not type(right):
        return False
    if type(right) is dict:
        return set(left) == set(right) and all(typed_equal(left[key], value) for key, value in right.items())
    if type(right) is list:
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def frozen_sources(reader):
    base = 'som-rs/contracts/rust-output-v1/phase2/'
    pins, behavior = parse(reader(base + 'pins.json')), parse(reader(base + 'behavior.json'))
    result = {}
    for table in (pins['suite_files'], behavior['sources'],
                  {row['path']: row['sha256'] for row in pins['emulators']['stand_ins'].values()}):
        for path, pin in table.items():
            require(path not in result or result[path] == pin, 'frozen QA source authorities disagree')
            result[path] = pin
    return result


def chronology(receipt):
    order = receipt.get('implementation_order')
    require(receipt.get('implementation_already_in_progress') is True and type(order) is dict
            and order == ORDER and all(type(order[key]) is type(value) for key, value in ORDER.items()),
            'truthful native chronology required')
    require(receipt.get('acceptance') == {'status': 'not_run', 'product_acceptance_passed': False},
            'source authority may not claim product acceptance')


def receipt(reader, paths, target):
    number, prefix, policy_leaf, old_sha = AUTHORITY[target]
    path = f'som-rs/evidence/{prefix}-contract-amendment-{number:03d}.json'
    previous_path = f'som-rs/evidence/{prefix}-contract-amendment-{number - 1:03d}.json'
    pattern = re.escape('som-rs/evidence/' + prefix + '-contract-amendment-') + r'[0-9]{3}\.json'
    latest = max((item for item in paths if re.fullmatch(pattern, item)), default=None)
    require(latest == path, 'exact latest numbered source authority required')
    raw, previous_raw = reader(path), reader(previous_path)
    current, previous = parse(raw), parse(previous_raw)
    require(type(current.get('amendment')) is int and current['amendment'] == number
            and current.get('status') == 'amendment_sealed_after_independent_review'
            and current.get('previous_receipt') == previous_path
            and current.get('previous_receipt_sha256') == sha(previous_raw) == old_sha,
            'numbered authority is pending, stale or has a false chain')
    require(current.get('version') == ('som.compiler-contract-amendment.v1' if target == 'compiler'
            else f'som.{target}-output-contract-amendment.v1'), 'numbered receipt version differs')
    chronology(current)
    source_map(reader, current.get('files'))
    root_name = 'compiler-v1' if target == 'compiler' else target + '-output-v1'
    prefixes = tuple('som-rs/' + part + '/' + root_name + '/' for part in ('contracts', 'tests', 'evidence'))
    present = {item for item in paths if item.startswith(prefixes)}
    require(present == set(current['files']), 'complete sealed contract/test/evidence universe differs')
    files_hash = sha(canonical(current['files']))
    review = current.get('independent_review', {})
    require(review.get('final_verdict') == 'APPROVED' and review.get('scope') == REVIEW_SCOPE
            and review.get('receipt_files_sha256') == files_hash, 'fresh review of the exact complete H(files) required')
    if target != 'compiler':
        require(current.get('target_contract', {}).get('sha256') == files_hash
                and current['target_contract']['previous_sha256'] == previous['target_contract']['sha256'],
                'target authority must use its own complete H(files)')
        owner = reader('som-rs/som-targets/src/' + target + '/mod.rs').decode('utf-8', 'strict')
        constants = re.findall(r'pub\s+const\s+TARGET_CONTRACT_SHA256\s*:\s*&str\s*=\s*"([0-9a-f]{64})"\s*;', owner)
        require(constants == [files_hash], 'latest sealed H(files) and owning target constant differ')
    old_files = previous['files']
    require(set(old_files) <= set(current['files']), 'old frozen files cannot be removed')
    changed = sorted(path for path in old_files if current['files'][path] != old_files[path])
    added = sorted(set(current['files']) - set(old_files))
    checker = f'som-rs/tests/{target}-output-v1/check_contract.py'
    policy_path = 'som-rs/contracts/' + policy_leaf
    require(changed == ([] if target == 'compiler' else [checker])
            and added == [policy_path] and current.get('changed_paths') == changed
            and current.get('added_paths') == added, 'native authority changed an unapproved semantic file')
    for change in current.get('changes', []):
        require(change['path'] in changed and change['old_sha256'] == old_files[change['path']]
                == change['archived_copy_sha256'] == sha(reader(name(change['archived_copy'])))
                and change['new_sha256'] == current['files'][change['path']], 'old checker archive or complete change map differs')
    require([row['path'] for row in current.get('changes', [])] == changed, 'complete changed source archives required')
    policy = parse(reader(policy_path))
    require(policy.get('target') == ('python' if target == 'compiler' else target) and policy.get('amendment') == number
            and policy.get('previous_receipt') == {'path': previous_path, 'sha256': old_sha}
            and policy.get('product_acceptance_passed') is False, 'numbered policy role differs')
    leaves = [row.get('path') for row in policy.get('allowed_compile_digest_leaves', [])]
    expected_leaves = {'/bundle/assets/backend/sha256', '/bundle/assets/formatter/sha256', '/bundle/hashes/payload_sha256'}
    if target != 'compiler':
        expected_leaves.add('/bundle/assets/target_contract/sha256')
    require(len(leaves) == len(expected_leaves) and set(leaves) == expected_leaves,
            'only exact eligible metadata digest leaves may differ')
    return current, policy, {'path': path, 'raw_sha256': sha(raw), 'files_map_sha256': files_hash}


QA_SUPPORT_SEARCH_ROOTS = (
    'som-rs/som-targets/qa', 'som-rs/behavior', 'som-rs/behavior/phase2',
    'som-rs/behavior/phase2/adapted', 'som-rs/tests/rust-output-v1',
    'som-rs/tests/typescript-output-v1', 'som-rs/tests/react-output-v1',
    'som-rs/scripts/compiler_acceptance', 'som-rs/scripts/rust_output_acceptance',
    'som-rs/scripts/typescript_output_acceptance', 'som-rs/scripts/react_output_acceptance',
    'som-core/scripts', 'som-core/src', 'som-rs/scripts/hosted_readiness',
    'som-rs/scripts/hosted_readiness/product_runtime', 'som-rs/scripts/hosted_readiness/product_python',
)
MIN_QA_SUPPORT_ROOTS = {
    'som-rs/som-targets/qa/check_rust_products.py', 'som-rs/som-targets/qa/check_targets.py',
    'som-rs/som-targets/qa/check_phase2_products.py', 'som-rs/behavior/hosts.py',
    'som-rs/behavior/check_behavior.py', 'som-rs/behavior/phase2/check_phase2.py',
    'som-rs/behavior/phase2/phase2_host.py', 'som-core/scripts/m3_local_fixture.py',
    'som-core/scripts/verify_python_quality.py', 'som-core/scripts/verify_service_v3.py',
    'som-rs/scripts/rust_output_acceptance/check_cli.py',
    'som-rs/scripts/rust_output_acceptance/python_replay.py',
    'som-rs/scripts/rust_output_acceptance/check_rust_unchanged.py',
    'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py',
    'som-rs/scripts/typescript_output_acceptance/check_python_baseline.py',
    'som-rs/scripts/typescript_output_acceptance/check_rt_diagnostics.py',
}
NATIVE_PYTHON_PHASE2_OUTCOMES = {
    'mode': 'native-qa-v1', 'named_rows': 584, 'real_product_gate_rows': 582,
    'current_compiler_refusal_rows': ['RS12_CNL_V3_FILTER_LIMIT', 'RS13_JSON_V3_FILTER_OFFSET'],
    'refusal_status': 400, 'refusal_code': 'INVALID_VALUE', 'raw_refusal_forms': 4,
    'refusal_product_role': 'No product exists. Record exact current compiler-refusal/no-product; no product pass or historical product substitution.',
    'diagnostic_rule': 'Compare the complete source-derived diagnostic and actual raw request/reply forms from the new same candidate. Never relabel omitted-target bytes as explicit-python bytes.',
    'source_rule': 'Compiler-v1 Amendment004 and exact current phase2_expected. No generic400 shortcut or unsupported-positive waiver.',
    'whole_scope_rule': 'Keep every584 named row, exact summary, workers, real waits, exits and cleanup. Run every applicable gate on all582 real products.',
    'repaired_current_ids': ['RS07_CNL_FIELD_SELF', 'RS14_CNL_V5_FOREIGN_KEY_SELF', 'RS16_CNL_V5_FIRST_REFUSAL'],
    'repaired_product_role': 'Use only fresh verified current files for the current role. Keep each old product_sha256 separately as unchanged historical provenance.',
    'python_quality_named_rows': 671,
    'python_quality_rule': 'Current28 lookup fallback does not add or remove any of the671 frozen Python quality rows.',
    'historical_hashes_rewritten': False,
}

MANUAL_SOURCE_CALLERS = (
    'som-rs/tests/react-output-v1/common.py',
    'som-rs/tests/typescript-output-v1/common.py',
)
MANUAL_SOURCE_MODULE = 'rust_build_negative_cases'
MANUAL_SOURCE_PATH = 'som-rs/tests/rust-output-v1/build_negative_cases.py'



def qa_support_closure(reader, paths, base_sources, roots):
    """Resolve explicit static QA imports inside the fixed source search roots."""
    path_set, base_set = set(paths), set(base_sources)
    require(type(roots) is list and len(roots) == len(set(roots))
            and MIN_QA_SUPPORT_ROOTS <= set(roots)
            and all(name(path).endswith('.py') and path in path_set for path in roots),
            'complete explicit QA gate/entry roots required')
    pending, seen, edges = set(roots) | {p for p in base_set if p.endswith('.py')}, set(), set()
    def candidates(parts, local=None):
        result = set()
        prefixes = (local,) if local is not None else QA_SUPPORT_SEARCH_ROOTS
        for prefix in prefixes:
            stem = prefix + ('/' + '/'.join(parts) if parts else '')
            for possible in (stem + '.py', stem + '/__init__.py'):
                if possible in path_set:
                    result.add(possible)
                    # Package initializers run before an imported submodule.
                    for count in range(1, len(parts)):
                        package = prefix + '/' + '/'.join(parts[:count]) + '/__init__.py'
                        if package in path_set:
                            result.add(package)
        return result
    while pending:
        path = min(pending)
        pending.remove(path)
        if path in seen:
            continue
        require(path in path_set, 'QA closure source is absent from exact Git inventory')
        seen.add(path)
        tree = ast.parse(reader(path), filename=path)
        for node in ast.walk(tree):
            imports = []
            if isinstance(node, ast.Import):
                imports = [(item.name, item.name.split('.'), None) for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                local = None
                if node.level:
                    pieces = path.split('/')[:-1]
                    require(node.level <= len(pieces), 'relative QA import escapes its source root')
                    local = '/'.join(pieces[:len(pieces) - node.level + 1])
                parts = node.module.split('.') if node.module else []
                imports = [(('.' * node.level) + (node.module or ''), parts, local)]
                imports += [(('.' * node.level) + '.'.join(parts + [item.name]), parts + [item.name], local)
                            for item in node.names if item.name != '*']
            for spelling, parts, local in imports:
                for found in candidates(parts, local):
                    edges.add((path, spelling, found))
                    if found not in seen:
                        pending.add(found)
    return {'roots': sorted(roots),
            'sources': {path: sha(reader(path)) for path in sorted(seen - base_set)},
            'edges': [{'from': owner, 'import': spelling, 'source': found}
                      for owner, spelling, found in sorted(edges)]}


def manual_source_rules(reader, rules, admitted):
    """Derive only the two unchanged common.py manual loads from source AST."""
    require(type(rules) is list and len(rules) == len(MANUAL_SOURCE_CALLERS),
            'exact TypeScript and React shared-source loads required')
    expected = []
    for caller in MANUAL_SOURCE_CALLERS:
        raw = reader(caller)
        tree = ast.parse(raw, filename=caller)
        bindings = {'REPO': ''}
        def path_value(node):
            if isinstance(node, ast.Name) and node.id in bindings:
                return bindings[node.id]
            if isinstance(node, ast.Constant) and type(node.value) is str:
                return name(node.value)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                left, right = path_value(node.left), path_value(node.right)
                return name(left + '/' + right if left else right)
            raise ValueError('manual source path is not a fixed literal binding')
        for key in ('SOM_RS', 'RUST_TOOLS'):
            values = [node.value for node in tree.body if isinstance(node, ast.Assign)
                      and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                      and node.targets[0].id == key]
            require(len(values) == 1, 'one exact manual source root binding required')
            bindings[key] = path_value(values[0])
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == 'spec_from_file_location'
                 and ast.dump(node.func, include_attributes=False) == ast.dump(
                     ast.parse('importlib.util.spec_from_file_location', mode='eval').body, include_attributes=False)]
        require(len(calls) == 1 and len(calls[0].args) == 2 and not calls[0].keywords
                and isinstance(calls[0].args[0], ast.Constant)
                and calls[0].args[0].value == MANUAL_SOURCE_MODULE
                and path_value(calls[0].args[1]) == MANUAL_SOURCE_PATH
                and caller in admitted and MANUAL_SOURCE_PATH in admitted,
                'manual caller/module/target differs from the frozen shared-source call')
        expected.append({'caller': caller, 'module': MANUAL_SOURCE_MODULE, 'path': MANUAL_SOURCE_PATH,
                         'caller_sha256': sha(raw), 'sha256': sha(reader(MANUAL_SOURCE_PATH))})
    require(typed_equal(rules, expected), 'manual source paths, callers or exact checked raw bytes differ')
    return rules


def verify(source_reader, mode, source_paths, helper_revision=None, candidate_revision=None):
    reader, paths = source_reader, source_paths
    require(mode in {'historical', MODE}, 'unknown QA source mode')
    if helper_revision is not None or candidate_revision is not None:
        require(type(helper_revision) is str and type(candidate_revision) is str
                and re.fullmatch('[0-9a-f]{40}', helper_revision)
                and helper_revision == candidate_revision, 'one exact final helper/candidate source required')
        if mode == MODE:
            require(candidate_revision != 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa',
                    'recorded f59 preparation cannot satisfy final native QA source authority')
    frozen = frozen_sources(reader)
    if mode == 'historical':
        source_map(reader, frozen)
        return {'mode': mode, 'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
                'source_authority_active': False, 'product_acceptance_passed': False}
    require(type(paths) in {list, tuple, set} and all(type(path) is str for path in paths), 'complete Git source inventory required')
    policies, receipts, admissions, approved_sources = [], {}, [], set()
    for target in AUTHORITY:
        current, policy, binding = receipt(reader, paths, target)
        require(policy.get('qa_authority_state') == 'SEALED_AUTHORITY' and policy.get('sealing_dependencies') == [],
                'native QA source policy remains pending')
        approved_sources.update(current['files'])
        policies.append(policy)
        receipts[target] = binding
        admissions.append(policy['qa_source_admission'])
    admission = admissions[0]
    require(all(item == admission for item in admissions) and admission.get('mode') == MODE,
            'all numbered policies must bind one exact native QA source authority')
    require(admission.get('frozen_sources') == frozen, 'complete frozen suite, behavior and stand-in map required')
    replacements = admission.get('replacements', {})
    require(set(replacements) == REPLACEMENTS, 'only the exact four reviewed QA adapters may differ')
    expected = dict(frozen)
    for path, row in replacements.items():
        require(set(row) == {'old_sha256', 'new_sha256', 'obligation'} and row['old_sha256'] == frozen[path]
                and row['new_sha256'] != frozen[path] and type(row['obligation']) is str and row['obligation'],
                'source substitution lost its old hash or unchanged semantic obligation')
        expected[path] = row['new_sha256']
    require(admission.get('native_sources') == expected, 'complete native source map cannot omit unchanged frozen members')
    source_map(reader, expected)
    operational = admission.get('operational_sources', {})
    expected_operational = {path for path in paths if path.startswith('som-rs/scripts/hosted_readiness/')
                            and path.endswith(('.py', '.json'))}
    require(set(operational) == expected_operational and SELF in operational
            and 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py' in operational
            and 'som-rs/scripts/hosted_readiness/product_python/test_produce_wheels.py' in operational,
            'complete operational source and64-fixture authority required')
    source_map(reader, operational)
    support = qa_support_closure(reader, paths, approved_sources | set(expected) | set(operational),
                                 admission.get('qa_support_roots'))
    require(admission.get('qa_support_search_roots') == list(QA_SUPPORT_SEARCH_ROOTS)
            and admission.get('qa_support_sources') == support['sources']
            and admission.get('qa_support_edges') == support['edges'],
            'complete exact transitive QA support source map or edges differ')
    source_map(reader, support['sources'])
    approved_sources.update(support['sources'])
    approved_sources.update(expected)
    approved_sources.update(operational)
    manual = manual_source_rules(reader, admission.get('manual_source_loads'), approved_sources)
    require(typed_equal(admission.get('native_python_phase2_outcomes'), NATIVE_PYTHON_PHASE2_OUTCOMES),
            'exact584/582/two-refusal authority and671-quality scope required')
    profiles = admission['profiles']
    source_map(reader, {row['path']: row['sha256'] for row in profiles.values()})
    rust = parse(reader(profiles['rust_product']['path']))
    require(rust['host'] == 'x86_64-unknown-linux-gnu' and rust['os'] == {'id': 'ubuntu', 'version_id': '22.04'}
            and set(rust['executables']) == {'rustc', 'cargo', 'rustfmt', 'cargo-fmt', 'cargo-clippy', 'clippy-driver'}
            and set(rust['components']) == {'rustc', 'cargo', 'rust-std', 'rustfmt-preview', 'clippy-preview'},
            'complete six-tool and std/runtime profile required')
    cargo = admission['cargo_scope']
    require(cargo['phase1_registry_packages'] == 203 and cargo['phase2_registry_packages'] == 334
            and cargo['registry_union_packages'] == 352 and cargo['license_files'] == 647,
            'complete frozen Cargo scope must remain exact')
    require(admission['runtime_rule'] == 'Recheck exact qualified native bytes, versions, inventories, waits and group cleanup before use; no source-only product acceptance.',
            'native runtime proof requirements differ')
    approved_sources.update(expected)
    approved_sources.update(operational)
    return {'mode': mode, 'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
            'source_authority_active': True, 'numbered_receipts': receipts,
            'source_inventory_sha256': sha(canonical(sorted(paths))), 'source_file_count': len(paths),
            'admitted_repository_python_sources': sorted(name for name in approved_sources if name.endswith('.py')),
            'qa_source_admission_sha256': sha(canonical(admission)), 'manual_source_loads': manual,
            'qa_support_closure_sha256': sha(canonical(support)), 'product_acceptance_passed': False,
            'compiler_acceptance_passed': False, 'whole_acceptance_complete': False}


class BoundManualSourceLoader(importlib.abc.Loader):
    def __init__(self, reader, root, module_name, path, raw):
        self.reader, self.root, self.module_name, self.path, self.raw = reader, root, module_name, path, raw

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        require(module.__name__ == self.module_name and module.__spec__.loader is self
                and self.reader(self.path) == self.raw, 'manual raw source identity changed before execution')
        filename = str(self.root / self.path)
        module.__file__, module.__cached__, module.__som_raw_sha256__ = filename, None, sha(self.raw)
        module.__som_manual_raw_source__ = self.path
        exec(compile(self.raw, filename, 'exec', dont_inherit=True), module.__dict__)


class RawQaFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, reader, root, allowed, rules=None):
        self.reader, self.root, self.allowed, self.pending = reader, Path(root).absolute(), set(allowed), {}
        self.rules, self.original_spec, self.raw_spec = list(rules or []), None, None

    def find_spec(self, fullname, path=None, target=None):
        found = importlib.machinery.PathFinder.find_spec(fullname, path)
        if found is None or not found.origin or found.origin in {'built-in', 'frozen'}:
            return None
        origin = Path(found.origin).absolute()
        if self.root not in origin.parents:
            return None
        name = origin.relative_to(self.root).as_posix()
        require(origin.suffix == '.py' and name in self.allowed, 'unadmitted QA source or repository PYC refused')
        require(target is None, 'QA source reload refused')
        self.pending[fullname] = origin, self.reader(name)
        spec = importlib.machinery.ModuleSpec(fullname, self, origin=str(origin),
                                               is_package=found.submodule_search_locations is not None)
        if found.submodule_search_locations is not None:
            spec.submodule_search_locations = [str(origin.parent)]
        spec.has_location = True
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        origin, raw = self.pending.pop(module.__name__)
        module.__file__, module.__cached__, module.__som_raw_sha256__ = str(origin), None, sha(raw)
        exec(compile(raw, str(origin), 'exec', dont_inherit=True), module.__dict__)

    def install(self):
        require(self.original_spec is None, 'raw import controls already installed')
        self.original_spec = importlib.util.spec_from_file_location
        def checked_spec(*args, **kwargs):
            fullname = args[0] if args else kwargs.get('name')
            location = args[1] if len(args) > 1 else kwargs.get('location')
            try:
                origin = Path(os.fsdecode(os.fspath(location))).absolute() if location is not None else None
            except TypeError:
                origin = None
            if origin is None or self.root not in origin.parents:
                return self.original_spec(*args, **kwargs)
            require(len(args) <= 2 and set(kwargs) <= {'name', 'location', 'loader', 'submodule_search_locations'}
                    and kwargs.get('loader') is None and kwargs.get('submodule_search_locations') is None,
                    'unlisted repository manual loader options refused')
            path = name(origin.relative_to(self.root).as_posix())
            frame = sys._getframe(1)
            caller_file = frame.f_globals.get('__file__')
            require(type(caller_file) is str and self.root in Path(caller_file).absolute().parents,
                    'manual loader caller is outside admitted QA source')
            caller = Path(caller_file).absolute().relative_to(self.root).as_posix()
            matches = [rule for rule in self.rules if rule['caller'] == caller
                       and rule['module'] == fullname and rule['path'] == path]
            require(len(matches) == 1 and caller in self.allowed and path in self.allowed,
                    'unlisted repository manual source path or caller refused')
            rule = matches[0]
            require(frame.f_globals.get('__som_raw_sha256__') == rule['caller_sha256'] == sha(self.reader(caller)),
                    'manual caller did not execute its checked raw source')
            raw = self.reader(path)
            require(sha(raw) == rule['sha256'], 'manual source bytes differ from sealed authority')
            loader = BoundManualSourceLoader(self.reader, self.root, fullname, path, raw)
            spec = importlib.machinery.ModuleSpec(fullname, loader, origin=str(origin), is_package=False)
            spec.has_location = True
            return spec
        self.raw_spec = checked_spec
        sys.meta_path.insert(0, self)
        importlib.util.spec_from_file_location = checked_spec
        return self

    def verify_installed(self):
        require(self in sys.meta_path and self.raw_spec is not None
                and importlib.util.spec_from_file_location is self.raw_spec,
                'checked raw import/manual source controls changed during QA')

    def close(self):
        self.verify_installed()
        importlib.util.spec_from_file_location = self.original_spec
        sys.meta_path.remove(self)


class RawImportLease:
    """Give each caller an explicit ownership handle for shared import guards."""
    def __init__(self, controls, owns_controls):
        require(type(owns_controls) is bool, 'explicit import-control ownership required')
        self.controls, self.owns_controls, self.closed = controls, owns_controls, False

    @property
    def original_spec(self):
        return self.controls.original_spec

    def verify_installed(self):
        require(not self.closed, 'import-control lease is already closed')
        self.controls.verify_installed()

    def close(self):
        if self.closed:
            return
        self.verify_installed()
        if self.owns_controls:
            self.controls.close()
        self.closed = True


def install_raw_finder(reader, root, authority):
    require(authority.get('mode') == MODE and authority.get('source_authority_active') is True,
            'active source authority required before QA imports')
    root = Path(root).absolute()
    current = sys.modules.get('full_product_context')
    context = getattr(current, '_ACTIVE', None)
    if context is not None and getattr(context, 'qa_mode', None) == MODE:
        require(getattr(current, '__som_raw_sha256__', None) == sha(reader('som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'))
                and context.repo == root and context.qa_source_authority == authority,
                'existing checked Context source authority differs')
        context.qa_import_controls.verify_installed()
        return RawImportLease(context.qa_import_controls.controls, False)
    finder = RawQaFinder(reader, root, authority['admitted_repository_python_sources'],
                         authority['manual_source_loads'])
    for module in list(sys.modules.values()):
        file = getattr(module, '__file__', None)
        if not isinstance(file, str) or finder.root not in Path(file).absolute().parents:
            continue
        path = Path(file).absolute().relative_to(finder.root).as_posix()
        require(path in finder.allowed and (getattr(module, '__name__', '') == '__main__'
                or getattr(module, '__som_raw_sha256__', None) == sha(reader(path))),
                'preloaded repository QA source is unbound or unadmitted')
    return RawImportLease(finder.install(), True)
