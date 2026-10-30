"""GitHub-only real-Core input fragment protocol cases.

Core, RawQaFinder, SourceBytes, input adapter, original baseline/phase2 bodies
and static helper sources are actual reviewed source bytes. The admission and
bootstrap transfer remain explicit protocol doubles. Git is real, on one tiny
temporary source repository. The profile, records, prompts and HTTP replies
are synthetic. No real Context constructor, compiler, service, native checker,
full584 saved corpus, source seal or transport-consumer acceptance runs here.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub tests only')

HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
BASE = 'som-rs/scripts/hosted_readiness/'
INPUT = BASE + 'typescript_regression_inputs.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
BASELINE = 'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py'
PHASE2 = 'som-rs/behavior/phase2/check_phase2.py'
PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'
INVENTORY = 'som-rs/contracts/rust-output-v1/phase2/case-inventory.json'
QA = 'som-rs/scripts/compiler_acceptance/qa_harness.py'
PATHS = 'som-rs/scripts/compiler_acceptance/paths.py'
RUST = 'som-rs/tests/rust-output-v1/rust_profile.py'
RUST_PHASE2 = 'som-rs/tests/rust-output-v1/rust_profile_phase2.py'
ADAPTER = 'ts.domain.typescript-http-replay-input-adapter'
TARGET = 'ts.domain.typescript-http-replay-original-rust'
PHASE2_ROLE = 'ts.inputs.original-phase2'
RECORDS = 'ts.inputs.domain-http.records'
RUST_FILES = 'ts.inputs.domain-http.rust-files'
VIEW_FIELDS = {'repo': 'path', 'helper': 'str', 'candidate': 'str',
               'settings': 'str', 'cache_data': 'str'}
REVIEWED_SHA256 = {
    INPUT: '698e8320aceabbf57e84f09035eab997a13e785cc117a5f8d8faef5b0c7992ac',
    CONTEXT: '7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440',
    BASELINE: '9a4608a53f271697e5279018825f2a045d032595eaacc5748ef8d1637a9c52ce',
    PHASE2: 'aa3d786b2fdf834be212dcca0808a95f12be898e9978285287657fb7dd1097d2',
    QA: 'f0c1cf96e1cf60ee692c7e9c3272757efef92498b52f4ac426e40b54142851a2',
    PATHS: '7fdee8d1a58559d72c05fc9bc4e956d694764c0ab83a7ad7ba7178c0de7dd91f',
    RUST: '62b972b5735ee71fc6b3969a115f57a1a0a53af2e8b0d203321217ab565bf513',
    RUST_PHASE2: '66361c04b8c7069868aa03205dbb33f9d28ede06e55ea262326671b91108316b',
    INVENTORY: 'b2d13ec6ede88ee6175ba12abd7b608453330030abb184c66a37c886d1e8e7e6',
    BASE + 'native_active_controls.py': 'eb0f804a8aa7de0857213d34478adc869fe51f7259c5552439dca109e4e6a038',
    BASE + 'native_source_bootstrap.py': 'd5c8ceb1a5b0b779662134afdf477fd90cfbc5319cca64e7bb7a8b5fe76ae473',
    BASE + 'native_qa_authority.py': '024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1',
    BASE + 'test_native_active_controls.py': '1a5852fb1693bcb921fcb19e9a20fb8281751e85f6f0ea2805b88323d230a201',
}


LOOKUP_SHA256 = {
    "som-rs/contracts/compiler-v1/examples/v1.prompt.json": "8a0269b81d7831c329c04d3114d3feaaa4c2def6df378a76b6569e9bf282463a",
    "som-rs/contracts/compiler-v1/examples/v2.prompt.json": "a5baf60682bb5096eb44d14932de9ff95390f40e02eb5887dddbe0d96206ab3f",
    "som-rs/contracts/compiler-v1/examples/v3.prompt.json": "72361df884e7d4bf28b9a71da5c6dd4c119fde0e19aa78dcb5c4d2a2d45668ed",
    "som-rs/contracts/compiler-v1/examples/v4.prompt.json": "6f123ea4ff72b7e0d6cba50278b10a5da1ecc449dd441b2ae0ff480500b8e43a",
    "som-rs/contracts/compiler-v1/examples/v5.prompt.json": "65101209a650e209be40ee35d969c6f6764d471a5ea4d71f4561dc054d8bbc39",
    "som-rs/contracts/compiler-v1/examples/v6.prompt.json": "33d902b113a2cf203250cc6b5e6b41cea5b02225890ad53e96019114a2bf3722",
    "som-rs/contracts/compiler-v1/examples/v7.prompt.json": "2d4a92302f99c30723f49f2ccc642f2c7bf58daf25c8f991002df93746cb5db7",
    "som-rs/contracts/compiler-v1/examples/v7-branch-null.prompt.json": "4e3c80e264d13e83c894418f2d011f4dc3b26f94fb5dc094efb719b1b503e522",
    "som-rs/tests/compiler-v1/cnl-positive-fixtures.json": "0e8aefb530ce299aa7a445d249efdfa8047104bbb94a0c7913bdd162025e4475",
    "som-rs/tests/rust-output-v1/cases/index.json": "b49af2a7ba842e5dd810d8d010fcf93436474df7f49f1c569367bed1bc94ed1b",
    "som-rs/tests/rust-output-v1/cases/cov-v1-types.prompt.json": "e3a960561e6315900af067cbe3e62d2736efb96419d58cfaa1ca1a968347237c",
    "som-rs/tests/rust-output-v1/cases/cov-v2-types.prompt.json": "1ac4b8c37b1c19930ffdd6bbdf236a8c50b858541d44c4d7a7dba09e8cebac53",
    "som-rs/tests/rust-output-v1/cases/cov-v3-types.prompt.json": "931db289eea1da333b9b39fb27349c526b967e91ad3457fa6ccb967e5dbfff23",
    "som-rs/tests/rust-output-v1/cases/cov-v4-types.prompt.json": "4516fc930150a1c4cec7650b2d6c687d1065843e06cea40b3704124eb4ee124e",
    "som-rs/tests/rust-output-v1/cases/cov-v5-min.prompt.json": "6c169522307102d28d73ef0a292cd17ce68b0f11dac367ca96b5d9cb273cb347",
    "som-rs/tests/rust-output-v1/cases/cov-v5-max.prompt.json": "47657a7899b56c99420d281f04bf9aa56568ab228206de614dfd85a24b939f4d",
}
REVIEWED_SHA256.update(LOOKUP_SHA256)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def source_bytes(path):
    raw = (REPO / path).read_bytes()
    if path in REVIEWED_SHA256 and sha(raw) != REVIEWED_SHA256[path]:
        raise AssertionError('fixture dependency differs from reviewed same-head bytes: ' + path)
    return raw


# -I removes the script directory. This exact fixture dependency is loaded
# without sys.path changes. It is never a production source-admission grant.
for reviewed_path in REVIEWED_SHA256:
    source_bytes(reviewed_path)
protocol_path = BASE + 'test_native_active_controls.py'
protocol = ModuleType('_som_ts_input_actual_core_protocol')
protocol.__file__, protocol.__package__ = str(REPO / protocol_path), ''
sys.modules[protocol.__name__] = protocol
exec(compile(source_bytes(protocol_path), protocol.__file__, 'exec', dont_inherit=True), protocol.__dict__)


def selector(raw, names):
    """Use the actual GitHub interpreter and Core's exact original-node order."""
    tree = ast.parse(raw)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in names]
    if len(nodes) != len(names) or {node.name for node in nodes} != set(names):
        raise AssertionError('whole original selector missing or ambiguous')
    original = ast.Module(body=nodes, type_ignores=[])
    return {'kind': 'top-level-functions', 'names': list(names),
            'ast_sha256': sha(ast.dump(original, include_attributes=False).encode())}


def typed(name, local, fields):
    return {'kind': 'local-instance', 'local': local, 'type_role': ADAPTER,
            'type_name': name, 'fields': copy.deepcopy(fields)}


def fragment_bindings():
    """Every fixture receives new nested dictionaries; mutations cannot leak."""
    guards = {name: {'kind': 'role-export', 'role': ADAPTER, 'attribute': attribute}
              for name, attribute in (('_cache_type', '_Cache'),
                                      ('_acquisition_type', '_Acquisition'))}
    records = {
        'phase2': typed('_Records', 'phase2_view', VIEW_FIELDS),
        '_ORIG_PHASE2_CASES': typed('_Records', 'phase2_view', VIEW_FIELDS),
        '_lookup_prompt_by_id': typed('_Lookup', 'lookup_view', VIEW_FIELDS),
        'qa': typed('_Qa', 'qa_view', {}),
        'DEFAULT_RUN': {'kind': 'local-data', 'local': 'default_run', 'type': 'path'},
        'pathlib': {'kind': 'stdlib', 'module': 'pathlib'}, **copy.deepcopy(guards)}
    rust_files = {
        'read': typed('_View', 'read_view', VIEW_FIELDS),
        'os': typed('_Environment', 'environment_view', {}),
        'qa': typed('_Qa', 'qa_view', {}),
        'post': typed('_Post', 'post_view', {'repo': 'path', 'helper': 'str',
                                          'candidate': 'str', 'role': 'str'}),
        '_case_type_guard': typed('_Cases', 'case_view', VIEW_FIELDS),
        'INVENTORY': {'kind': 'local-data', 'local': 'inventory_path', 'type': 'path'},
        'rust_profile_phase2': {'kind': 'role-module', 'role': 'test.inputs.rust-profile-phase2'},
        'dump': {'kind': 'role-export', 'role': ADAPTER, 'attribute': 'dump'},
        'json': {'kind': 'stdlib', 'module': 'json'},
        'hashlib': {'kind': 'stdlib', 'module': 'hashlib'}, **copy.deepcopy(guards)}
    return copy.deepcopy(records), copy.deepcopy(rust_files)


CALLER_SOURCE = '''from pathlib import Path
import json
RESPONSES, REQUESTS = [], []
def run(controls):
    return controls.load_module("ts.domain.typescript-http-replay-input-adapter")
def target(controls):
    return controls.load_module("ts.domain.typescript-http-replay-original-rust")
def post(base, body, route):
    REQUESTS.append((base, body, route))
    return RESPONSES.pop(0)
def later_cases():
    return ['new owner']
def records_scope(controls, adapter, view):
    phase2_view = adapter._view(view, adapter._Records)
    lookup_view = adapter._view(view, adapter._Lookup)
    qa_view = adapter._Qa()
    default_run = controls.root / '.local/rust-compiler-goal/acceptance/rust-output-055/run-003'
    return controls.load_fragment("ts.inputs.domain-http.records")
class CloseProbe:
    def __init__(self, actual, pointers, events, name, error):
        self.actual, self.pointers = actual, pointers
        self.events, self.name, self.error = events, name, error
    def close(self):
        self.events.append((self.name, all(getattr(owner, key) is value
                                         for owner, key, value in self.pointers)))
        self.actual.close()
        if self.error is not None:
            raise self.error
def probes(acquisition, phase2, originals, events, scope_error=None, lease_error=None,
           target=None, target_originals=None):
    pointers = [(phase2, key, value) for key, value in originals.items()]
    if target is not None:
        pointers += [(target, key, value) for key, value in target_originals.items()]
    acquisition.scope = CloseProbe(acquisition.scope, pointers, events, 'scope', scope_error)
    acquisition.lease = CloseProbe(acquisition.lease, pointers, events, 'lease', lease_error)
def fail_then_close(adapter, ctx, primary):
    try:
        raise primary
    finally:
        adapter.close(ctx)
'''


class GitTransfer(protocol.ProtocolTransfer):
    """Protocol authority double; SourceBytes still checks real Git and files."""
    def __init__(self, fixture):
        super().__init__(fixture)
        self.revision = fixture.revision
        self.admission.update(helper_revision=self.revision, candidate_revision=self.revision)


class InputFixture(protocol.Fixture):
    def __init__(self, case, *, mutation=None):
        self.mutation = mutation
        self.original_path = list(sys.path)
        with mock.patch.object(protocol, 'ProtocolTransfer', GitTransfer):
            super().__init__(case, CALLER_SOURCE)
        # These are fixture-only joins for the actual complete legacy modules.
        # They do not adopt Root's still-pending production role map.
        self.policy['imports'] = [
            {'caller': caller, 'name': name, 'level': 0, 'fromlist': names,
             'result': target, 'from_roles': {}}
            for caller, name, names, target in (
                (TARGET, 'check_phase2', [], PHASE2_ROLE),
                (TARGET, 'qa_harness', [], 'test.inputs.qa'),
                (TARGET, 'rust_profile', [], 'test.inputs.rust-profile'),
                (PHASE2_ROLE, 'qa_harness', [], 'test.inputs.qa'),
                (PHASE2_ROLE, 'rust_profile', [], 'test.inputs.rust-profile'),
                (PHASE2_ROLE, 'rust_profile_phase2', [], 'test.inputs.rust-profile-phase2'),
                ('test.inputs.qa', 'paths', ['default_output_root', 'find_repo', 'require_new_output'],
                 'test.inputs.paths'))]
        # from_roles maps package submodules, not ordinary function exports.
        # Python reads these exact source-owned functions from the target.
        self.adapter = self.run()
        self.module = self.caller().target(self.controls)
        self.phase2 = self.module.phase2
        self.sources = self.adapter.__som_native_SourceBytes__(self.root, self.revision)
        settings = json.dumps({'bindings': self.bindings, 'admission': self.transfer.admission})
        self.view = self.adapter._View(self.root, self.revision, self.revision, settings)
        self.ctx = SimpleNamespace(repo=self.root, helper_revision=self.revision,
            candidate_revision=self.revision, qa_mode='native-qa-v1',
            qa_source_authority=self.transfer.admission, sources=self.sources, cache={},
            data={'typescript_regression_inputs': copy.deepcopy(self.bindings)})
        self.stack.callback(self.close_inputs)

    def add_role(self, role, path, raw, **kwargs):
        row = super().add_role(role, path, raw, **kwargs)
        if role != 'm6.api.producer':
            return row
        # Register before base Fixture installs Core. Its finish callback then
        # runs after close_inputs and before path/module/temp-tree cleanup.
        self.stack.callback(lambda: sys.path.__setitem__(slice(None), self.original_path))
        self.stack.enter_context(mock.patch.dict(os.environ, {'SOM_REPO': str(self.root)}))
        (self.root / 'som-core').mkdir()
        (self.root / '.local/rust-compiler-goal/acceptance').mkdir(parents=True)
        super().add_role('runtime.context', CONTEXT, source_bytes(CONTEXT),
            module='_som_ts_inputs_fixture_actual_context')
        super().add_role(ADAPTER, INPUT, source_bytes(INPUT),
            module='_som_ts_domain_typescript_http_replay_input_adapter',
            callers=[{'role': 'caller', 'function': 'run'}, {'role': ADAPTER, 'function': 'close'}],
            bindings={'__som_native_active_controls__': {'kind': 'active-controls'},
                      '__som_native_SourceBytes__': {'kind': 'role-export', 'role': 'runtime.context',
                                                   'attribute': 'SourceBytes'}})
        super().add_role(TARGET, BASELINE, source_bytes(BASELINE),
            module='_som_ts_domain_typescript_http_replay_original_rust',
            callers=[{'role': 'caller', 'function': 'target'}, {'role': ADAPTER, 'function': '_target'}])
        super().add_role(PHASE2_ROLE, PHASE2, source_bytes(PHASE2), module='check_phase2',
            callers=[{'role': TARGET, 'function': '<module>'}, {'role': ADAPTER, 'function': '_target'},
                     {'role': ADAPTER, 'function': '_Post.__call__'}])
        helper_rows = (
            ('test.inputs.qa', QA, 'qa_harness', [TARGET, PHASE2_ROLE]),
            ('test.inputs.rust-profile', RUST, 'rust_profile', [TARGET, PHASE2_ROLE]),
            ('test.inputs.rust-profile-phase2', RUST_PHASE2, 'rust_profile_phase2', [PHASE2_ROLE]),
            ('test.inputs.paths', PATHS, 'paths', ['test.inputs.qa']))
        for target, source, name, callers in helper_rows:
            super().add_role(target, source, source_bytes(source), module=name,
                callers=[{'role': caller, 'function': '<module>'} for caller in callers])
        records, rust_files = fragment_bindings()
        for target, source, names, bindings, callers in (
            (RECORDS, BASELINE, ['_discover_phase2_case_specs', 'canonical_sha256'], records,
             [{'role': ADAPTER, 'function': 'fresh_cases'}, {'role': 'caller', 'function': 'records_scope'}]),
            (RUST_FILES, PHASE2, ['rust_files'], rust_files, [{'role': ADAPTER, 'function': 'configure'}])):
            fragment = super().add_role(target, source, source_bytes(source), kind='fragment',
                module='_som_fixture_' + target.replace('.', '_'), bindings=copy.deepcopy(bindings), callers=callers)
            fragment['selector'] = selector(source_bytes(source), names)
        self.make_inputs()
        self.commit_sources()
        return row

    def make_inputs(self):
        self.input_root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='som-ts-input-data-')))
        self.bindings = {'roots': {role: str(self.input_root / role)
                                  for role in ('rust_run', 'public_run', 'legacy_run')},
                         'siblings': {'rs_prompts': str(self.input_root / 'rs_prompts')}}
        for value in (*self.bindings['roots'].values(), *self.bindings['siblings'].values()):
            Path(value).mkdir()
        (Path(self.bindings['roots']['legacy_run']) / 'run-005').mkdir()
        inventory = json.loads(source_bytes(INVENTORY))
        self.ids = [row['id'] for row in inventory['rows']]
        self.case.assertEqual(len(self.ids), 584)
        self.case.assertEqual(len(set(self.ids)), 584)
        profile = {'roles': {role: {'files': {}} for role in self.bindings['roots']},
                   'siblings': {'rs_prompts': {'files': {}}}}
        def member(role, relative, document):
            sibling = role == 'rs_prompts'
            root = Path(self.bindings['siblings' if sibling else 'roots'][role])
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = json_bytes(document)
            path.write_bytes(raw); path.chmod(0o644)
            profile['siblings' if sibling else 'roles'][role]['files'][relative] = {
                'sha256': sha(raw), 'size': len(raw), 'mode': 0o644}
        for index, name in enumerate(self.ids):
            prompt = {'fixture': name, 'finite': 1.25}
            record = {'id': name, 'prompt': prompt, 'prompt_sha256': sha(json_bytes(prompt))}
            if index == 0 and self.mutation == 'prompt-hash':
                record['prompt_sha256'] = '0' * 64
            if index == len(self.ids) - 1 and self.mutation == 'duplicate-name':
                record['id'] = self.ids[0]
            if index == len(self.ids) - 1 and self.mutation == 'wrong-name':
                record['id'] = 'unlisted-synthetic-record'
            member('rust_run', 'phase2/cases/' + re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_') + '.json',
                   record)
        member('rs_prompts', 'RS01/prompt.json', {'fixture': 'named-sibling', 'finite': 1.25})
        for relative, raw in ((PROFILE, json_bytes(profile)), (INVENTORY, source_bytes(INVENTORY))):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw); target.chmod(0o644)
        for relative in LOOKUP_SHA256:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_bytes(relative)); target.chmod(0o644)

    def commit_sources(self):
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        def git(*args):
            return subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
                env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        git('init', '--quiet'); git('add', '--', '.')
        git('-c', 'user.name=Input fragment protocol fixture', '-c', 'user.email=fixture@example.invalid',
            '-c', 'core.hooksPath=/dev/null', 'commit', '--quiet', '-m', 'synthetic input fragment sources')
        self.revision = git('rev-parse', 'HEAD').decode().strip()

    def configure(self):
        self.adapter.configure(self.ctx, self.module)
        acquisition = self.ctx.cache['typescript_regression_input_binding_closers'][-1]
        self.case.assertIs(type(acquisition), self.adapter._Acquisition)
        return acquisition

    def close_inputs(self):
        if self.ctx.cache.get('typescript_regression_input_binding_closers'):
            self.adapter.close(self.ctx)
        self.sources.verify()


class NativeInputFragmentTests(unittest.TestCase):
    def test_whole_record_selector_actual_code_exports_and_all584_synthetic_prompts(self):
        f = InputFixture(self)
        self.assertIs(type(f.sources), f.adapter.__som_native_SourceBytes__)
        self.assertEqual(f.sources.read(INPUT), source_bytes(INPUT))
        self.assertIsNone(f.domain.records['runtime.context'].module._ACTIVE)
        with f.caller().records_scope(f.controls, f.adapter, f.view) as scope:
            self.assertIs(scope.phase2.cases, scope._ORIG_PHASE2_CASES)
            record = next(r for r in f.domain.records.values() if r.role == RECORDS)
            function = scope._discover_phase2_case_specs
            self.assertIs(function.__globals__, record.namespace)
            self.assertTrue(any(function.__code__ is code for code in record.codes.values()))
            self.assertEqual(record.row['selector'], selector(source_bytes(BASELINE),
                ['_discover_phase2_case_specs', 'canonical_sha256']))
            self.assertFalse(hasattr(scope, 'main'))
            records = function(Path(f.bindings['roots']['rust_run']))
            self.assertEqual({row['id'] for row in records}, set(f.ids))
            for row in records:
                self.assertEqual(scope.canonical_sha256(row['load_prompt']()), row['prompt_sha256'])
            self.assertFalse(scope._closed)
        with self.assertRaisesRegex(ValueError, 'fragment scope is closed'):
            scope.canonical_sha256({})
        calls = []
        def foreign_reader(path):
            calls.append(path)
            raise AssertionError('public callback must not be used')
        specs = f.adapter.fresh_cases(f.ctx, object(), foreign_reader)
        self.assertEqual(len(specs), 584)
        self.assertEqual(calls, [])
        self.assertFalse(any(r.role == RECORDS for r in f.domain.records.values()))
        owner = f.domain.records[ADAPTER]
        for row in specs:
            self.assertIs(row['load_prompt'].__globals__, owner.namespace)
            self.assertTrue(any(row['load_prompt'].__code__ is code for code in owner.codes.values()))
            self.assertEqual(f.adapter.sha(row['load_prompt']()), row['prompt_sha256'])
            self.assertIsNot(row['load_prompt'](), row['load_prompt']())

    def test_actual_git_profile_named_input_hash_mode_link_and_ancestor_guards(self):
        f = InputFixture(self)
        path = Path(f.bindings['siblings']['rs_prompts']) / 'RS01/prompt.json'
        raw = path.read_bytes()
        self.assertEqual(f.adapter.read_input(f.view, 'rs_prompts', 'RS01/prompt.json'),
                         {'fixture': 'named-sibling', 'finite': 1.25})
        lookup = f.adapter._Lookup(f.root, f.revision, f.revision, f.view.settings)
        self.assertEqual(lookup('RS01'), {'fixture': 'named-sibling', 'finite': 1.25})
        self.assertEqual(lookup('example-v1'), json.loads(source_bytes(
            'som-rs/contracts/compiler-v1/examples/v1.prompt.json')))
        try:
            path.write_bytes(raw + b' ')
            with self.assertRaisesRegex(ValueError, 'complete source profile'):
                f.adapter.read_input(f.view, 'rs_prompts', 'RS01/prompt.json')
            path.write_bytes(raw); path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'complete source profile'):
                f.adapter.read_input(f.view, 'rs_prompts', 'RS01/prompt.json')
        finally:
            path.write_bytes(raw); path.chmod(0o644)
        alias = path.with_name('external-alias.json')
        try:
            os.link(path, alias)
            with self.assertRaisesRegex(ValueError, 'complete source profile'):
                f.adapter.read_input(f.view, 'rs_prompts', 'RS01/prompt.json')
        finally:
            alias.unlink(missing_ok=True)
        held = path.with_name('held-prompt.json')
        try:
            path.rename(held)
            path.symlink_to(held)
            with self.assertRaisesRegex(ValueError, 'regular file'):
                f.adapter.read_input(f.view, 'rs_prompts', 'RS01/prompt.json')
        finally:
            if path.is_symlink():
                path.unlink()
            if held.exists():
                held.rename(path)
        with self.assertRaisesRegex(ValueError, 'safe named input'):
            f.adapter.read_input(f.view, 'rs_prompts', '../prompt.json')
        with self.assertRaisesRegex(ValueError, 'undeclared ancestor'):
            f.view.read_path(f.input_root.parent / 'undeclared-input.json')
        profile = f.root / PROFILE
        original = profile.read_bytes()
        try:
            profile.write_bytes(original + b' ')
            with self.assertRaisesRegex(ValueError, 'committed Git bytes'):
                f.adapter.read_input(f.view, 'rs_prompts', 'RS01/prompt.json')
        finally:
            profile.write_bytes(original)

    def test_original584_count_names_prompt_hash_and_no_fallback_refuse_at_intended_guards(self):
        f = InputFixture(self)
        root = Path(f.bindings['roots']['rust_run'])
        path = sorted((root / 'phase2/cases').glob('*.json'))[0]
        raw = path.read_bytes()
        hidden = path.with_suffix('.held')
        try:
            path.rename(hidden)
            with self.assertRaisesRegex(ValueError, 'all584 named original Rust phase2 records required'):
                f.adapter.fresh_cases(f.ctx, None, None)
        finally:
            hidden.rename(path)
        # A profile-preserving data view changes only the selected root. The
        # unchanged original body reaches its explicit no-fallback callback.
        saved = f.ctx.cache.get('typescript_replay_input_bindings')
        f.ctx.cache['typescript_replay_input_bindings'] = copy.deepcopy(f.bindings)
        f.ctx.cache['typescript_replay_input_bindings']['roots']['rust_run'] = str(f.input_root / 'absent-run')
        try:
            with self.assertRaisesRegex(ValueError, 'every explicit saved phase2 record'):
                f.adapter.fresh_cases(f.ctx, None, None)
        finally:
            if saved is None:
                f.ctx.cache.pop('typescript_replay_input_bindings')
            else:
                f.ctx.cache['typescript_replay_input_bindings'] = saved
        f.stack.close()
        # Fresh committed profiles match each mutated fixture's physical bytes.
        # The original name/hash assertions, rather than an earlier profile or
        # source refusal, must reject each case. No live revision is rewritten.
        for mutation, message in (
                ('prompt-hash', 'every original named typed Rust prompt'),
                ('duplicate-name', 'all584 named original Rust phase2 records required'),
                ('wrong-name', 'all584 named original Rust phase2 records required')):
            with self.subTest(mutation=mutation):
                negative = InputFixture(self, mutation=mutation)
                try:
                    with self.assertRaisesRegex(ValueError, message):
                        negative.adapter.fresh_cases(negative.ctx, None, None)
                    self.assertFalse(any(r.role == RECORDS for r in negative.domain.records.values()))
                finally:
                    negative.stack.close()

    def test_original_rust_http_body_and_callback_lifetime_then_owned_restore(self):
        f = InputFixture(self)
        originals = {key: getattr(f.phase2, key) for key in ('PUBLIC', 'LEGACY', 'RS', 'cases', 'rust_files')}
        target_originals = {key: getattr(f.module, key) for key in ('_lookup_prompt_by_id', '_ORIG_PHASE2_CASES')}
        original_post = f.phase2.post
        f.phase2.post = f.caller().post
        acquisition = f.configure()
        scope, lease = acquisition.scope, acquisition.lease
        fragment = next(r for r in f.domain.records.values() if r.role == RUST_FILES)
        self.assertIs(f.phase2.rust_files, scope.rust_files)
        self.assertIs(scope.rust_files.__globals__, fragment.namespace)
        self.assertTrue(any(scope.rust_files.__code__ is code for code in fragment.codes.values()))
        self.assertEqual(fragment.row['selector'], selector(source_bytes(PHASE2), ['rust_files']))
        case = next(row for row in json.loads(source_bytes(INVENTORY))['rows']
                    if row['phase2_expected']['result'] != 'refusal')
        prompt, bundle, files = {'fixture': 'http'}, {'fixture': 'bundle'}, {'src/lib.rs': '// synthetic\n'}
        f.caller().RESPONSES[:] = [(200, json_bytes({'bundle': bundle})), (200, json_bytes({'files': files}))]
        row = {}
        self.assertEqual(f.phase2.rust_files('http://protocol.invalid', case, prompt, row), files)
        self.assertEqual([request[2] for request in f.caller().REQUESTS], ['/v1/compile', '/v1/assemble'])
        self.assertEqual(json.loads(f.caller().REQUESTS[0][1]),
                         {'format': 'som.prompt-json.v1', 'target': 'rust', 'prompt': prompt})
        self.assertEqual(row['rust']['files_sha256'], f.adapter.sha(files))
        refusal = next(row for row in json.loads(source_bytes(INVENTORY))['rows']
                       if row['phase2_expected']['result'] == 'refusal')
        expected = refusal['phase2_expected']
        response = {'code': expected['code'], 'detail': expected['detail'],
                    'diagnostics': [{'prompt_pointer': '/prompt' + expected['pointer']}]}
        f.caller().RESPONSES[:] = [(expected['http_status'], json_bytes(response))]
        refusal_row = {}
        self.assertIsNone(f.phase2.rust_files('http://protocol.invalid', refusal, prompt, refusal_row))
        self.assertEqual(refusal_row['rust']['refusal'], {'code': expected['code'],
            'detail': expected['detail'], 'prompt_pointer': '/prompt' + expected['pointer']})
        response['diagnostics'][0]['prompt_pointer'] = '/wrong-pointer'
        f.caller().RESPONSES[:] = [(expected['http_status'], json_bytes(response))]
        with self.assertRaisesRegex(RuntimeError, 'did not give the ' + expected['rule'] + ' refusal'):
            f.phase2.rust_files('http://protocol.invalid', refusal, prompt, {})
        self.assertFalse(scope._closed); self.assertFalse(lease._closed)
        # Restore a permitted source-owned post transition before input close.
        f.phase2.post = original_post
        events = []
        f.caller().probes(acquisition, f.phase2, originals, events,
                          target=f.module, target_originals=target_originals)
        f.adapter.close(f.ctx)
        self.assertEqual(events, [('scope', True), ('lease', True)])
        self.assertTrue(scope._closed); self.assertTrue(lease._closed)
        self.assertFalse(f.domain.closed)
        for key, value in originals.items():
            self.assertIs(getattr(f.phase2, key), value)
        for key, value in target_originals.items():
            self.assertIs(getattr(f.module, key), value)
        with self.assertRaisesRegex(ValueError, 'fragment scope is closed'):
            scope.rust_files

    def test_foreign_typed_hook_fake_target_and_callback_refuse_before_hook(self):
        f = InputFixture(self)
        calls = []
        original = f.adapter._Records
        class ForeignRecords(original):
            def __getattribute__(self, name):
                calls.append(name)
                raise AssertionError('foreign typed hook must not run')
        f.adapter._Records = ForeignRecords
        try:
            with self.assertRaisesRegex(ValueError, 'typed class export changed'):
                f.adapter.fresh_cases(f.ctx, None, None)
            self.assertEqual(calls, [])
        finally:
            f.adapter._Records = original
        class ForeignTarget:
            def __getattribute__(self, name):
                calls.append(name)
                raise AssertionError('foreign target hook must not run')
        with self.assertRaisesRegex(ValueError, 'actual Core-owned Rust checker module'):
            f.adapter.configure(f.ctx, ForeignTarget())
        self.assertEqual(calls, [])
        foreign = lambda: calls.append('cache callback')
        f.ctx.cache['typescript_regression_input_binding_closers'] = [foreign]
        with self.assertRaisesRegex(ValueError, 'unlisted input acquisition source namespace'):
            f.adapter.close(f.ctx)
        self.assertEqual(calls, [])

    def test_later_owner_kept_and_first_exception_trace_all_cleanup_secondary_retained(self):
        f = InputFixture(self)
        originals = {key: getattr(f.phase2, key) for key in ('PUBLIC', 'LEGACY', 'RS', 'cases', 'rust_files')}
        target_originals = {key: getattr(f.module, key) for key in ('_lookup_prompt_by_id', '_ORIG_PHASE2_CASES')}
        acquisition = f.configure()
        actual_scope, actual_lease = acquisition.scope, acquisition.lease
        events = []
        scope_error, lease_error = ValueError('injected after real fragment close'), ValueError('injected after real lease close')
        # Fixed source-owned probes call the actual closers, then inject errors.
        # They are lifecycle doubles only, never source-authority callbacks.
        still_owned = {key: value for key, value in originals.items() if key != 'cases'}
        f.caller().probes(acquisition, f.phase2, still_owned, events, scope_error, lease_error,
                          target=f.module, target_originals=target_originals)
        later = f.caller().post
        f.phase2.cases = later
        try:
            raise ValueError('original business failure')
        except ValueError as primary:
            trace = primary.__traceback__
            try:
                f.caller().fail_then_close(f.adapter, f.ctx, primary)
            except ValueError as observed:
                # assertRaises clears exception tracebacks. Inspect the actual
                # retained traceback directly before a test helper can clear it.
                self.assertIs(observed, primary)
                current, found = observed.__traceback__, False
                while current is not None:
                    found |= current is trace
                    current = current.tb_next
                self.assertTrue(found)
                ownership, = observed.__som_input_cleanup_secondary__
                self.assertIn('owned explicit input binding replaced: cases', str(ownership))
                self.assertEqual(ownership.__som_input_cleanup_secondary__, (scope_error, lease_error))
                self.assertIn('explicit input binding cleanup failed', observed.__som_input_cleanup_summary__)
            else:
                self.fail('the original business failure must propagate')
        self.assertIs(f.phase2.cases, later)
        for key in ('PUBLIC', 'LEGACY', 'RS', 'rust_files'):
            self.assertIs(getattr(f.phase2, key), originals[key])
        for key, value in target_originals.items():
            self.assertIs(getattr(f.module, key), value)
        self.assertEqual(events, [('scope', True), ('lease', True)])
        self.assertTrue(actual_scope._closed); self.assertTrue(actual_lease._closed)
        self.assertFalse(f.domain.closed)
        # Restore the fixture's own intentional later pointer before Core closes.
        f.phase2.cases = originals['cases']


if __name__ == '__main__':
    unittest.main()
