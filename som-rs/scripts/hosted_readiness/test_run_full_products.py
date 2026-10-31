"""GitHub-only source fixtures for complete product cohort admission."""
import ast
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).absolute().parent
if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('Run these fixtures on GitHub only')


def subject(name, path):
    module = ModuleType(name)
    module.__file__, module.__cached__, module.__package__ = str(path), None, ''
    sys.modules[name] = module
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


driver = subject('full_product_driver_under_test', HERE / 'run_full_products.py')
context_module = subject('full_product_context_under_test', HERE / 'product_runtime/full_product_context.py')
cargo = subject('full_product_cargo_under_test', HERE / 'product_runtime/consume_cargo.py')
carrier = subject('full_product_carrier_under_test', HERE / 'product_runtime/candidate_carrier.py')


def native_protocol_fixture(case, *, entry_failure=False, shard=None):
    """Actual Core and driver; synthetic admission/Context/checker, never a product positive."""
    root_repo = HERE.parents[2]
    def source(relative):
        raw = (root_repo / relative).read_bytes()
        case.assertEqual(raw, subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(root_repo),
            'cat-file', 'blob', os.environ['HELPER_REVISION'] + ':' + relative]))
        return raw
    driver_raw = source(driver.SELF)
    case.assertEqual(hashlib.sha256(driver_raw).hexdigest(),
                     'b9fd4184619cd3bcbee6282348243a2089e4b3ecfa0b7e91f83617ede70ca1ac')
    protocol = subject('full_product_core_protocol_fixture', HERE / 'test_native_active_controls.py')
    actual_context = source(driver.CONTEXT)
    context_tree = ast.parse(actual_context)
    kept = {'Refusal', 'require', 'digest', 'canonical', 'read', 'relative', 'SourceBytes'}
    context_parts = '\n\n'.join(ast.get_source_segment(actual_context.decode(), node)
        for node in context_tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in kept)
    context_raw = ('''from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
ENV = 'SOM_FULL_PRODUCT_CONTEXT'
_ACTIVE = None
CONSTRUCTIONS = []
VERIFICATIONS = []
ACTIVATIONS = []
COMMANDS = []
''' + context_parts + '''
class Context:
    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_bytes())
        self.repo = Path(self.data['repo'])
        self.work = Path(self.data['work_root'])
        self.helper_revision = self.data['helper_revision']
        self.candidate_revision = self.data['candidate_revision']
        self.sources = SourceBytes(self.repo, self.helper_revision)
        paths = self.sources.git('ls-tree', '-r', '--name-only', self.helper_revision).decode().splitlines()
        self.qa_source_authority = authority.verify(self.sources.read, 'native-qa-v1', paths,
            self.helper_revision, self.candidate_revision)
        CONSTRUCTIONS.append(self)
        (self.work / 'context-before-entry').write_bytes(b'protocol constructor; not genuine Context admission')
    def verify(self):
        self.sources.verify()
        VERIFICATIONS.append(self)
    def activate_python_paths(self, executable):
        ACTIVATIONS.append(str(executable))
    def child_environment(self):
        return {}
    def command(self, *args, **kwargs):
        COMMANDS.append((args, kwargs))
        return dict(self.data['protocol_command'])
''').encode()
    phase2_path = 'som-rs/behavior/phase2/check_phase2.py'
    phase2_raw = source(phase2_path)
    key_node = next(node for node in ast.parse(phase2_raw).body if isinstance(node, ast.FunctionDef) and node.name == 'key')
    key_source = ast.get_source_segment(phase2_raw.decode(), key_node)
    inventory_path = 'som-rs/contracts/rust-output-v1/phase2/case-inventory.json'
    inventory_raw = source(inventory_path)
    inventory = json.loads(inventory_raw)
    case.assertEqual(len(inventory['rows']), 584)
    case.assertEqual(len({row['id'] for row in inventory['rows']}), 584)
    checker_raw = ('''from __future__ import annotations
import json
import os
from pathlib import Path
import re
def cases():
    repo = Path(json.loads(Path(os.environ['SOM_FULL_PRODUCT_CONTEXT']).read_bytes())['repo'])
    return json.loads((repo / 'som-rs/contracts/rust-output-v1/phase2/case-inventory.json').read_bytes())['rows']
''' + key_source + '''
if __name__ == '__main__':
    data = json.loads(Path(os.environ['SOM_FULL_PRODUCT_CONTEXT']).read_bytes())
    work = Path(data['work_root'])
    if not (work / 'context-before-entry').is_file():
        raise AssertionError('protocol Context must precede the checker body')
    (work / 'entry-observed-context').write_text('actual Core entry; synthetic checker payload')
    if data.get('protocol_entry_failure'):
        raise RuntimeError('exact protocol checker first failure')
    raise SystemExit(0)
''').encode()
    authority_path = 'som-rs/scripts/hosted_readiness/native_qa_authority.py'
    authority_suffix = b'''
# Explicit protocol admission double. Production activation does not use it.
BLOCKED_PATHS = set()
def verify(source_reader, mode, source_paths, helper_revision=None, candidate_revision=None):
    return {'mode': mode, 'source_authority_active': True,
        'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
        'admitted_repository_python_sources': sorted(path for path in source_paths if path.endswith('.py') and path not in BLOCKED_PATHS),
        'manual_source_loads': [], 'product_acceptance_passed': False,
        'compiler_acceptance_passed': False, 'whole_acceptance_complete': False}
'''

    class Fixture(protocol.Fixture):
        def synthetic_seed(self, name, path, raw):
            if path == authority_path:
                raw = (raw.encode() if type(raw) is str else raw) + authority_suffix
            module, code = super().synthetic_seed(name, path, raw)
            module.__file__ = str(self.root / path)
            module.__som_raw_sha256__ = self.core.sha(self.raw[path]) if hasattr(self, 'core') else hashlib.sha256(self.raw[path]).hexdigest()
            return module, code

        def add_role(self, role, path, raw, **kwargs):
            if role == 'bootstrap.authority':
                kwargs['callers'] = [{'role': owner, 'function': 'load_context_module'}
                                     for owner in ('caller', 'full.controller')]
            if role == 'caller':
                path, raw = driver.SELF, driver_raw
                kwargs['bindings'] = {'__som_native_active_controls__': {'kind': 'active-controls'}}
            result = super().add_role(role, path, raw, **kwargs)
            if role == 'caller':
                super().add_role('full.controller', driver.SELF, driver_raw, module='_som_full_protocol_controller',
                    bindings={'__som_native_active_controls__': {'kind': 'active-controls'}})
                super().add_role('runtime.context', driver.CONTEXT, context_raw, module='full_product_context',
                    callers=[{'role': owner, 'function': function} for owner in ('caller', 'full.controller')
                             for function in ('load_context_module', 'phase2_python_results')],
                    bindings={'authority': {'kind': 'role-module', 'role': 'bootstrap.authority'}})
                super().add_role('full.phase2', phase2_path, checker_raw, module='_som_full_protocol_phase2',
                    callers=[{'role': 'full.controller', 'function': 'phase2_python'}])
                super().add_role(driver.NATIVE_QA_ENTRIES[phase2_path], phase2_path, checker_raw,
                    kind='entry', module='__main__', callers=[{'role': 'caller', 'function': 'execute_tool'}])
            return result

    fixture = Fixture(case, entry=True)
    fixture.root.joinpath(inventory_path).parent.mkdir(parents=True, exist_ok=True)
    fixture.root.joinpath(inventory_path).write_bytes(inventory_raw)
    fixture.root.joinpath('unlisted_native_fixture.py').write_text(
        'raise AssertionError("nonfinite checker body must never execute")\n')
    def git(*args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(fixture.root), *args],
            env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                 'GIT_AUTHOR_NAME': 'Protocol Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                 'GIT_COMMITTER_NAME': 'Protocol Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'},
            stderr=subprocess.DEVNULL, timeout=30)
    git('init', '--quiet')
    git('add', '--all')
    git('-c', 'commit.gpgsign=false', 'commit', '--quiet', '--no-verify', '-m', 'Explicit Core protocol fixture')
    revision = git('rev-parse', 'HEAD').decode().strip()
    fixture.transfer.revision = fixture.domain.revision = revision
    fixture.transfer.verify_entry_source = lambda path: case.assertEqual(path, driver.SELF)
    fixture.git = git
    fixture.revision = revision
    fixture.target = phase2_path
    fixture.inventory = inventory
    fixture.work = fixture.root.parent / (fixture.root.name + '-owned-output')
    fixture.work.mkdir()
    fixture.stack.callback(shutil.rmtree, fixture.work)
    fixture.context_path = fixture.work / 'context.json'
    fixture.data = {'repo': str(fixture.root), 'helper_revision': revision, 'candidate_revision': revision,
        'work_root': str(fixture.work), 'stage': 'phase2-python', 'shard': shard or {'index': 0, 'count': 1},
        'qa_mode': 'native-qa-v1', 'observer_python': sys.executable,
        'protocol_entry_failure': entry_failure,
        'protocol_command': {'pid': 12345, 'exit_code': 0, 'wait_completed': True, 'owned_group_cleanup_passed': True}}
    fixture.context_path.write_text(json.dumps(fixture.data))
    fixture.stack.enter_context(mock.patch.dict(os.environ, {'RUNNER_TEMP': str(fixture.root.parent),
        'SOM_FULL_PRODUCT_CONTEXT': str(fixture.context_path)}))
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    fixture.stack.callback(lambda: [signal.signal(number, handler) for number, handler in handlers.items()])
    fixture.run_entry = lambda script=None: fixture.seeds[fixture.core.ENTRY_MODULE]['module']._trusted_run(
        fixture.controls, 'caller', ['--execute-tool', script or fixture.target, '--'])
    fixture.controller = lambda: fixture.domain.load_known('full.controller', fixture.domain.prepare('full.controller'), None)
    return fixture


def native_rows_fixture(case):
    """584 actual inventory names; process and case payloads are explicit unit data."""
    f = native_protocol_fixture(case)
    f.controller_module = f.controller()
    context = f.domain.load_known('runtime.context', f.domain.prepare('runtime.context'), None)
    f.ctx = context.Context(f.context_path)
    case.assertEqual(json.loads(f.ctx.sources.read(
        'som-rs/contracts/rust-output-v1/phase2/case-inventory.json')), f.inventory)
    f.ids = [row['id'] for row in f.inventory['rows']]
    f.output = f.work / 'phase2-python'
    (f.output / 'cases').mkdir(parents=True)
    f.summary = {'version': 'som.full-product-phase2-shard.v1', 'stage': 'phase2-python',
        'helper_revision': f.revision, 'candidate_revision': f.revision, 'shard': f.data['shard'],
        'selected_ids': f.ids, 'whole_inventory_total': 584,
        'whole_inventory_sha256': driver.digest(driver.canonical(f.ids)),
        'whole_inventory_complete': False, 'shard_complete': True, 'missing': [],
        'worker_exit_codes': [0, 0, 0], 'product_acceptance_passed': False, 'rows': []}
    # Use the actual source-owned checker key function after the registered load.
    checker = f.domain.load_known('full.phase2', f.domain.prepare('full.phase2'), None)
    f.key = checker.key
    for identity in f.ids:
        raw = driver.canonical({'id': identity, 'passed': True,
            'full_transcript': ['explicit protocol case data; no product execution']}) + b'\n'
        (f.output / 'cases' / (f.key(identity) + '.json')).write_bytes(raw)
        f.summary['rows'].append({'id': identity, 'passed': True, 'raw_sha256': driver.digest(raw)})
    f.summary_path = f.output / 'hosted-shard-summary.json'
    f.summary_path.write_bytes(driver.canonical(f.summary))
    f.proof = {'version': 'som.full-product-checker-postcheck.v1', 'tool': f.target,
        'pid': f.data['protocol_command']['pid'], 'helper_revision': f.revision, 'candidate_revision': f.revision,
        'stage': f.data['stage'], 'shard': f.data['shard'],
        'source_sha256': {name: driver.digest(f.ctx.sources.read(name)) for name in
                          (driver.SELF, driver.CONTEXT, f.target)},
        'source_runtime_tools_inputs_unchanged': True, 'product_acceptance_passed': False}
    f.proof_path = f.work / 'checker-postchecks' / (str(f.proof['pid']) + '.json')
    f.proof_path.parent.mkdir()
    f.proof_path.write_bytes(driver.canonical(f.proof))
    f.results = lambda: f.controller_module.phase2_python(f.ctx)
    return f


class NativeRoleProtocolTests(unittest.TestCase):
    def proof(self, f):
        paths = list((f.work / 'checker-postchecks').iterdir())
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_bytes())

    def assert_failed_complete(self, f, rows):
        self.assertEqual([row['id'] for row in rows], f.ids)
        self.assertEqual(len(rows), 584)
        self.assertFalse(any(row['passed'] for row in rows))
        self.assertTrue(all(row['case_result']['full_transcript'] ==
            ['explicit protocol case data; no product execution'] for row in rows))

    def test_registered_fixed_entry_constructs_context_before_body_and_rechecks_sources(self):
        f = native_protocol_fixture(self)
        old_main, old_argv = sys.modules['__main__'], sys.argv
        self.assertEqual(f.run_entry(), 0)
        self.assertIs(sys.modules['__main__'], old_main)
        self.assertIs(sys.argv, old_argv)
        context = f.domain.records['runtime.context'].module
        self.assertEqual(len(context.CONSTRUCTIONS), 1)
        self.assertEqual(len(context.VERIFICATIONS), 1)
        self.assertIs(context.VERIFICATIONS[0], context.CONSTRUCTIONS[0])
        self.assertTrue((f.work / 'entry-observed-context').is_file())
        report = self.proof(f)
        self.assertTrue(report['entry_executed'])
        self.assertTrue(report['source_runtime_tools_inputs_unchanged'])
        self.assertEqual(report['entry_exit_code'], 0)
        self.assertFalse(report['product_acceptance_passed'])
        self.assertEqual(report['source_sha256'][driver.SELF],
                         hashlib.sha256(f.git('show', f.revision + ':' + driver.SELF)).hexdigest())

    def test_checker_first_failure_retains_exact_error_and_restores_main_and_argv(self):
        f = native_protocol_fixture(self, entry_failure=True)
        old_main, old_argv = sys.modules['__main__'], sys.argv
        # The original driver's __main__ catches its error and exits 1.
        self.assertEqual(f.run_entry(), 1)
        self.assertIs(sys.modules['__main__'], old_main)
        self.assertIs(sys.argv, old_argv)
        report = self.proof(f)
        self.assertEqual(report['error'], 'RuntimeError: exact protocol checker first failure')
        self.assertTrue(report['entry_executed'])
        self.assertTrue(report['source_runtime_tools_inputs_unchanged'])
        self.assertNotIn(driver.NATIVE_QA_ENTRIES[f.target], f.domain.records)
        self.assertNotIn('caller', f.domain.records)
        self.assertEqual(len(f.domain.records['runtime.context'].module.VERIFICATIONS), 1)

    def test_source_admitted_but_nonfinite_entry_refuses_before_body(self):
        f = native_protocol_fixture(self)
        self.assertEqual(f.run_entry('unlisted_native_fixture.py'), 1)
        report = self.proof(f)
        self.assertEqual(report['error'], 'ValueError: repository checker has no fixed native entry role')
        self.assertFalse(report['entry_executed'])
        self.assertTrue(report['source_runtime_tools_inputs_unchanged'])
        self.assertIn('unlisted_native_fixture.py', report['source_sha256'])

    def test_missing_reserved_binding_refuses_before_context_or_checker_execution(self):
        f = native_protocol_fixture(self)
        f.controls.close()
        with self.assertRaises(ModuleNotFoundError) as failure:
            driver.load_context_module(f.root, f.revision, 'native-qa-v1', f.revision)
        self.assertEqual(failure.exception.name, '_som_native_active_controls_binding')
        self.assertFalse((f.work / 'context-before-entry').exists())
        self.assertFalse((f.work / 'entry-observed-context').exists())

    def test_native_phase2_registered_roles_keep_all_584_names_and_original_source_keys(self):
        f = native_rows_fixture(self)
        ids, rows = f.results()
        self.assertEqual(ids, f.ids)
        self.assertEqual(len(rows), 584)
        self.assertTrue(all(row['passed'] for row in rows))
        self.assertEqual(set(row['id'] for row in rows), set(f.ids))
        self.assertEqual(len(f.domain.records['runtime.context'].module.COMMANDS), 1)
        argv = f.domain.records['runtime.context'].module.COMMANDS[0][0][0]
        self.assertIn('--execute-tool', argv)
        self.assertIn(f.target, argv)
        self.assertIs(f.domain.records['full.phase2'].module.key, f.key)
        self.assertTrue(all(gate['passed'] for gate in rows[0]['gates'].values()))

    def test_native_phase2_rejects_wrong_role_source_before_loading_checker(self):
        f = native_protocol_fixture(self)
        controller = f.controller()
        context = f.domain.load_known('runtime.context', f.domain.prepare('runtime.context'), None)
        ctx = context.Context(f.context_path)
        original = f.rows['full.phase2']['source']
        f.rows['full.phase2']['source'] = driver.SELF
        try:
            with self.assertRaisesRegex(ValueError, '^fixed phase2 source role differs$'):
                controller.phase2_python(ctx)
            self.assertNotIn('full.phase2', f.domain.records)
            self.assertEqual(context.COMMANDS, [])
        finally:
            f.rows['full.phase2']['source'] = original

    def test_native_phase2_actual_git_and_working_source_must_stay_equal(self):
        f = native_rows_fixture(self)
        path = f.root / f.target
        original = path.read_bytes()
        path.write_bytes(original + b'\n# changed physical checker\n')
        try:
            ids, rows = f.results()
            self.assertEqual(ids, f.ids)
            self.assert_failed_complete(f, rows)
            self.assertIn('source differs from committed Git bytes',
                          rows[0]['gates']['driver_exit_wait_cleanup']['failure']['error'])
            self.assertFalse(rows[0]['gates']['checker_postcheck']['passed'])
        finally:
            path.write_bytes(original)

    def test_native_phase2_rows_cannot_hide_exit_signal_wait_or_cleanup_failure(self):
        f = native_rows_fixture(self)
        original = dict(f.ctx.data['protocol_command'])
        for field, value in (('exit_code', 1), ('exit_code', -9), ('exit_code', None),
                             ('wait_completed', False), ('owned_group_cleanup_passed', False)):
            with self.subTest(field=field, value=value):
                f.ctx.data['protocol_command'] = {**original, field: value}
                _, rows = f.results()
                self.assert_failed_complete(f, rows)

    def test_native_phase2_worker_and_raw_summary_gates_remain_required(self):
        f = native_rows_fixture(self)
        for changes in ({'worker_exit_codes': [0, 0]}, {'worker_exit_codes': [0, 1, 0]},
                        {'worker_exit_codes': [0, None, 0]}, {'worker_exit_codes': [0, False, 0]},
                        {'rows': f.summary['rows'][:-1]}, {'selected_ids': list(reversed(f.ids))}):
            with self.subTest(changes=list(changes)):
                f.summary_path.write_bytes(driver.canonical({**f.summary, **changes}))
                _, rows = f.results()
                self.assert_failed_complete(f, rows)


class CallbackBaseContextTests(unittest.TestCase):
    """Actual prepare and Git reader; explicit Graph/Context/runtime doubles."""
    PROFILE = 'som-rs/scripts/hosted_readiness/inputs/callback-protocol-profile.json'
    SECOND = 'som-rs/scripts/hosted_readiness/inputs/callback-second-profile.json'
    GRAPH = 'som-rs/scripts/hosted_readiness/same_candidate_graph.py'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='som-callback-base-protocol-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.calls = []
        self.profile_raw = b'{"version":"explicit-protocol-profile","admission_complete":false}\n'
        for relative in (driver.SELF, self.GRAPH, driver.CALLBACK_ADAPTERS['typescript-regression']):
            raw = (HERE.parents[2] / relative).read_bytes()
            self.assertEqual(raw, subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(HERE.parents[2]),
                'cat-file', 'blob', os.environ['HELPER_REVISION'] + ':' + relative]))
            target = self.repo / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        for relative in (self.PROFILE, self.SECOND):
            target = self.repo / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(self.profile_raw)
        def git(*arguments):
            return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.repo), *arguments],
                env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                     'GIT_AUTHOR_NAME': 'Callback protocol fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                     'GIT_COMMITTER_NAME': 'Callback protocol fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'},
                stderr=subprocess.DEVNULL, timeout=30)
        git('init', '--quiet'); git('add', '--all')
        git('-c', 'commit.gpgsign=false', 'commit', '--quiet', '--no-verify', '-m', 'Explicit callback protocol source')
        revision = git('rev-parse', 'HEAD').decode().strip()
        self.sources = context_module.SourceBytes(self.repo, revision)
        self.sources.workflow_binding = {'explicit_protocol_workflow': True, 'product_acceptance_passed': False}
        self.baseline = self.root / 'baseline-data'; self.baseline.mkdir()
        self.other_baseline = self.root / 'other-baseline-data'; self.other_baseline.mkdir()
        assembly = self.root / 'inputs/_temp/som-unsealed-native-compile'
        build = assembly.parent / 'som-current-build-provenance'
        context_parent = self.root / 'context-parent'; context_parent.mkdir()
        self.args = SimpleNamespace(mode='sealed-candidate-products', repo=self.repo,
            helper_revision=revision, candidate_revision=revision, stage='phase1', shard_index=0, shard_count=1,
            prepare_context_only=True, callback_gate='typescript-regression',
            baseline_input=[[self.PROFILE, str(self.baseline)]], out=context_parent / 'context-out',
            assembly_root=assembly, build_provenance_root=build)
        for name in ('wheel_root', 'runtime_root', 'rust_tools_root', 'cargo_root', 'carrier_root',
                     'python_inputs_root', 'rust_inputs_root', 'current_companions_root'):
            setattr(self.args, name, self.root / 'inputs' / name)
        for name in ('assembly_root', 'build_provenance_root', 'wheel_root', 'runtime_root', 'rust_tools_root',
                     'cargo_root', 'carrier_root', 'python_inputs_root', 'rust_inputs_root', 'current_companions_root'):
            getattr(self.args, name).mkdir(parents=True, exist_ok=True)
        self.args.artifact_inputs = self.root / 'inputs/catalog/artifact-inputs.json'
        self.args.artifact_inputs.parent.mkdir(); self.args.artifact_inputs.write_text('{"explicit_catalog_double":true}')
        self.admission = {'source_authority_active': True, 'mode': 'native-qa-v1',
            'admitted_repository_python_sources': [driver.CALLBACK_ADAPTERS['typescript-regression']],
            'product_acceptance_passed': False}
        fixture = self
        class ContextProtocol:
            def __init__(self, path):
                self.path = Path(path); self.data = json.loads(self.path.read_bytes())
                self.repo, self.work, self.sources = fixture.repo, fixture.args.out, fixture.sources
                # Retain exactly what was on disk at constructor entry.
                fixture.calls.append(('constructor', json.loads(self.path.read_bytes())))
            def python_rows(self): fixture.calls.append(('python_rows',)); return []
            def rust_rows(self): fixture.calls.append(('rust_rows',)); return []
            def module(self, key, path):
                fixture.assertEqual((key, path), ('cargo_consumer', driver.CARGO))
                return SimpleNamespace(prepare=lambda ctx: fixture.calls.append(('cargo_prepare',)))
            def prepare_python(self, root, files):
                fixture.assertEqual((root, files), (fixture.args.out / 'observer', {}))
                fixture.calls.append(('observer_prepare',))
            def verify(self): fixture.sources.verify(); fixture.calls.append(('verify',))
            def command(self, *args, **kwargs): raise AssertionError('prepare-only must not run a product worker')
        self.module = SimpleNamespace(Context=ContextProtocol, __som_native_source_authority__=self.admission)
        def restore(*args):
            self.calls.append(('carrier_restore',))
            return {'explicit_protocol_carrier': True, 'product_acceptance_passed': False}
        self.graph = SimpleNamespace(_admitted_controller_sources=lambda *args: (self.sources, None, self.admission),
            restore_current_carrier=restore)
        self.controls = SimpleNamespace(role_sources={'full.graph': self.GRAPH}, load_module=lambda role: self.graph)
        self.environment = mock.patch.dict(os.environ, {'RUNNER_TEMP': str(self.root)})
        self.environment.start(); self.addCleanup(self.environment.stop)

    def run_prepare(self):
        with mock.patch.object(driver, '_native_controls', return_value=self.controls), \
             mock.patch.object(driver, 'load_context_module', return_value=self.module):
            return driver.prepare(self.args)

    def assert_before_output(self):
        self.assertFalse(self.args.out.exists())
        self.assertFalse(any(call[0] in {'constructor', 'carrier_restore', 'observer_prepare'} for call in self.calls))

    def test_actual_prepare_stores_exact_fixed_bindings_before_constructor(self):
        self.assertEqual(self.run_prepare(), 0)
        snapshot = next(value for name, *values in self.calls if name == 'constructor' for value in values)
        self.assertEqual(snapshot['baseline_inputs'], {self.PROFILE: str(self.baseline)})
        adapter = driver.CALLBACK_ADAPTERS['typescript-regression']
        self.assertEqual(snapshot['gate_adapters'], {'typescript-regression': {
            'path': adapter, 'sha256': hashlib.sha256(self.sources.read(adapter)).hexdigest()}})
        self.assertEqual(json.loads((self.args.out / 'context.json').read_bytes()), snapshot)
        self.assertEqual(self.sources.read(self.PROFILE), self.profile_raw)
        self.assertFalse(json.loads(self.sources.read(self.PROFILE))['admission_complete'])
        prepared = json.loads((self.args.out / 'context-prepared.json').read_bytes())
        self.assertFalse(prepared['product_acceptance_passed']); self.assertFalse(prepared['product_rows_run'])
        self.assertEqual([item[0] for item in self.calls],
                         ['carrier_restore', 'constructor', 'python_rows', 'rust_rows', 'cargo_prepare', 'observer_prepare', 'verify'])

    def test_absent_options_preserve_original_base_context_shape(self):
        self.args.callback_gate = None; self.args.baseline_input = None
        self.assertEqual(self.run_prepare(), 0)
        data = json.loads((self.args.out / 'context.json').read_bytes())
        self.assertNotIn('baseline_inputs', data); self.assertNotIn('gate_adapters', data)
        self.assertTrue((self.args.out / 'context-prepared.json').is_file())

    def test_cli_passes_explicit_fixed_gate_and_repeated_two_value_bindings(self):
        arguments = ['--mode', 'sealed-candidate-products', '--stage', 'phase1',
            '--helper-revision', self.args.helper_revision, '--candidate-revision', self.args.candidate_revision,
            '--prepare-context-only', '--callback-gate', 'typescript-regression',
            '--baseline-input', self.PROFILE, str(self.baseline),
            '--baseline-input', self.SECOND, str(self.other_baseline)]
        for name in ('repo', 'assembly_root', 'wheel_root', 'runtime_root', 'rust_tools_root', 'cargo_root', 'out',
                     'carrier_root', 'artifact_inputs', 'current_companions_root', 'python_inputs_root',
                     'rust_inputs_root', 'build_provenance_root'):
            arguments += ['--' + name.replace('_', '-'), str(getattr(self.args, name))]
        handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        try:
            with mock.patch.object(driver, 'prepare', return_value=0) as prepared:
                self.assertEqual(driver.main(arguments), 0)
                prepared.assert_called_once()
                parsed = prepared.call_args.args[0]
                self.assertEqual(parsed.callback_gate, 'typescript-regression')
                self.assertTrue(parsed.prepare_context_only)
                self.assertEqual(parsed.baseline_input,
                    [[self.PROFILE, str(self.baseline)], [self.SECOND, str(self.other_baseline)]])
                prepared.reset_mock()
                arguments[arguments.index('typescript-regression')] = 'm6-react003'
                with self.assertRaises(SystemExit) as refused: driver.main(arguments)
                self.assertEqual(refused.exception.code, 2)
                prepared.assert_not_called()
        finally:
            for number, handler in handlers.items(): signal.signal(number, handler)

    def test_bindings_require_sealed_prepare_only_and_explicit_gate_and_nonempty_map(self):
        for changes, message in (({'mode': 'experimental-preparation'}, 'sealed prepare-context-only'),
                ({'prepare_context_only': False}, 'sealed prepare-context-only'),
                ({'callback_gate': None}, 'fixed source adapter'),
                ({'baseline_input': []}, 'one to 32')):
            original = vars(self.args).copy()
            vars(self.args).update(changes)
            try:
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message): self.run_prepare()
                self.assert_before_output()
            finally:
                vars(self.args).clear(); vars(self.args).update(original)

    def test_duplicate_profiles_and_more_than32_bindings_refuse_before_output(self):
        for pairs, message in (([[self.PROFILE, str(self.baseline)], [self.PROFILE, str(self.other_baseline)]], 'duplicate baseline profile'),
                               ([[self.PROFILE, str(self.baseline)]] * 33, 'one to 32')):
            self.args.baseline_input = pairs
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message): self.run_prepare()
            self.assert_before_output()

    def test_untracked_missing_or_nonoperational_profile_cannot_bind(self):
        for profile in ('/absolute/profile.json', '../profile.json', 'som-rs/contracts/profile.json',
                        'som-rs/scripts/hosted_readiness/../profile.json', 'som-rs/scripts/hosted_readiness/profile.py'):
            self.args.baseline_input = [[profile, str(self.baseline)]]
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, 'clean tracked operational JSON'): self.run_prepare()
            self.assert_before_output()
        missing = 'som-rs/scripts/hosted_readiness/inputs/not-tracked.json'
        self.args.baseline_input = [[missing, str(self.baseline)]]
        with self.assertRaisesRegex(ValueError, 'fixed regular Git source required'): self.run_prepare()
        self.assert_before_output()
        (self.repo / missing).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'fixed regular Git source required'): self.run_prepare()
        self.assert_before_output()

    def test_link_and_traversal_and_relative_roots_refuse_before_output(self):
        alias = self.root / 'baseline-alias'; alias.symlink_to(self.baseline, target_is_directory=True)
        regular = self.root / 'baseline-file'; regular.write_bytes(b'not a directory')
        for locator in (str(alias), str(regular), str(self.baseline) + '/../baseline-data', 'relative-data', str(self.baseline) + '/'):
            self.args.baseline_input = [[self.PROFILE, locator]]
            with self.subTest(locator=locator), self.assertRaisesRegex(ValueError, 'physical directory without traversal or links'): self.run_prepare()
            self.assert_before_output()

    def test_baselines_exclude_every_protected_scope_and_each_other(self):
        protected = [self.repo, self.args.out.parent, self.args.artifact_inputs.parent,
                     *(getattr(self.args, name) for name in ('assembly_root', 'wheel_root', 'runtime_root', 'rust_tools_root',
                       'cargo_root', 'carrier_root', 'python_inputs_root', 'rust_inputs_root', 'current_companions_root', 'build_provenance_root'))]
        for root in protected:
            self.args.baseline_input = [[self.PROFILE, str(root)]]
            with self.subTest(root=root), self.assertRaisesRegex(ValueError, 'overlaps protected'): self.run_prepare()
            self.assert_before_output()
        nested = self.baseline / 'nested'; nested.mkdir()
        for other in (self.baseline, nested):
            self.args.baseline_input = [[self.PROFILE, str(self.baseline)], [self.SECOND, str(other)]]
            with self.subTest(other=other), self.assertRaisesRegex(ValueError, 'overlaps protected'): self.run_prepare()
            self.assert_before_output()

    def test_only_the_admitted_fixed_typescript_adapter_can_be_selected(self):
        for gate in ('m6-react003', 'arbitrary.module:callable'):
            self.args.callback_gate = gate
            message = 'all eight explicit M6' if gate == 'm6-react003' else 'no finalized fixed source adapter'
            with self.subTest(gate=gate), self.assertRaisesRegex(ValueError, message): self.run_prepare()
            self.assert_before_output()
        self.args.callback_gate = 'typescript-regression'
        self.admission['admitted_repository_python_sources'] = []
        with self.assertRaisesRegex(ValueError, 'not in actual native source admission'): self.run_prepare()
        self.assert_before_output()
        adapter = driver.CALLBACK_ADAPTERS['typescript-regression']
        self.admission['admitted_repository_python_sources'] = [adapter]
        path = self.repo / adapter
        path.rename(path.with_suffix('.unavailable'))
        with self.assertRaises(FileNotFoundError): self.run_prepare()
        self.assert_before_output()



class CohortScopeTests(unittest.TestCase):
    def test_stride_join_preserves_all_584_named_slots(self):
        ids = ['case-' + str(index) for index in range(584)]
        chunks = [driver.scoped(ids, {'index': index, 'count': 16}) for index in range(16)]
        joined = [identity for index in range(584) for identity in chunks[index % 16][index // 16:index // 16 + 1]]
        self.assertEqual(ids, joined)
        self.assertEqual(sum(map(len, chunks)), 584)

    def test_no_overlap_between_671_product_shards(self):
        ids = ['python/' + str(index) for index in range(671)]
        chunks = [set(driver.scoped(ids, {'index': index, 'count': 16})) for index in range(16)]
        self.assertEqual(set.union(*chunks), set(ids))
        for index, chunk in enumerate(chunks):
            for other in chunks[index + 1:]:
                self.assertFalse(chunk & other)

    def test_repeated_ids_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            driver.scoped(['A', 'A'], {'index': 0, 'count': 1})

    def test_boolean_shard_index_is_refused(self):
        with self.assertRaises(ValueError):
            driver.scoped(['A'], {'index': False, 'count': 1})

    def test_out_of_range_shard_is_refused(self):
        with self.assertRaises(ValueError):
            driver.scoped(['A'], {'index': 16, 'count': 16})

    def test_zero_shard_count_is_refused(self):
        with self.assertRaises(ValueError):
            driver.scoped(['A'], {'index': 0, 'count': 0})

    def test_each_named_duplicate_prompt_slot_is_kept(self):
        ids = ['PUBLIC_v6', 'RS01_CNL_V6', 'RS03_JSON_V6']
        self.assertEqual(driver.scoped(ids, {'index': 0, 'count': 1}), ids)

    def test_syntax_is_not_used_as_file_parity(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = {'app.py': 'value = 1\n'}
            (root / 'app.py').write_text('value=1\n')
            self.assertFalse(driver.unchanged(root, original))

    def test_acceptance_mode_refuses_before_loading_runtime(self):
        args = type('Args', (), {'mode': 'acceptance'})()
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), mock.patch.object(driver.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(ValueError, 'unsealed'):
                driver.prepare(args)

    def test_pending_gates_still_name_fresh_compiler_and_independent_scope(self):
        self.assertIn('fresh compile and assemble determinism and refusals', driver.PENDING)
        self.assertIn('ordinary-mode installer file scope independent audit', driver.PENDING)


class JoinRefusalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_TEMP': str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.temporary.cleanup)

    def row(self, name, **changes):
        value = {'version': 'som.full-product-preparation-shard.v1', 'stage': 'phase2-rust',
                 'shard': {'index': 0, 'count': 1}, 'identities': {'helper_revision': '1' * 40, 'candidate_revision': '2' * 40},
                 'product_acceptance_passed': False, 'whole_acceptance_complete': False,
                 'shard_complete': True, 'source_runtime_tools_unchanged': True}
        value.update(changes)
        path = self.root / name
        path.write_text(json.dumps(value))
        return path

    def test_partial_receipt_is_not_joined(self):
        path = self.row('partial.json', shard_complete=False)
        with self.assertRaisesRegex(ValueError, 'partial shard'):
            driver.join(type('Args', (), {'receipts': [path], 'out': self.root / 'join.json'})())

    def test_missing_tool_postcheck_is_refused(self):
        path = self.row('tools.json', source_runtime_tools_unchanged=False)
        with self.assertRaisesRegex(ValueError, 'postcheck'):
            driver.join(type('Args', (), {'receipts': [path], 'out': self.root / 'join.json'})())

    def test_false_preparation_acceptance_is_refused(self):
        path = self.row('accepted.json', product_acceptance_passed=True)
        with self.assertRaisesRegex(ValueError, 'acceptance claim'):
            driver.join(type('Args', (), {'receipts': [path], 'out': self.root / 'join.json'})())

    def test_mixed_candidate_binaries_are_refused(self):
        first = self.row('one.json')
        second = self.row('two.json', identities={'helper_revision': '1' * 40, 'candidate_revision': '3' * 40})
        with self.assertRaisesRegex(ValueError, 'mixed candidate'):
            driver.join(type('Args', (), {'receipts': [first, second], 'out': self.root / 'join.json'})())


class DependencyIdentityTests(unittest.TestCase):
    def setUp(self):
        self.helper, self.candidate = '1' * 40, '2' * 40
        self.admission = {'helper_revision': self.helper, 'candidate_revision': self.candidate,
                          'product_acceptance_passed': False, 'recipe': {'sha256': 'a' * 64},
                          'artifact': {'compile_manifest_sha256': 'b' * 64}}
        self.document = {'source_revision': self.helper, 'helper_revision': self.helper,
                         'candidate_revision': self.candidate, 'candidate_admission': self.admission}

    def test_dependency_proof_keeps_distinct_helper_and_candidate_roles(self):
        context_module.dependency_identity(self.document, self.helper, self.candidate, self.admission)

    def test_dependency_roles_are_explicit_and_cannot_default(self):
        for field in ('source_revision', 'helper_revision', 'candidate_revision'):
            for value in (None, '3' * 40):
                with self.subTest(field=field, value=value):
                    changed = {**self.document, field: value}
                    with self.assertRaisesRegex(context_module.Refusal, 'explicit dependency'):
                        context_module.dependency_identity(changed, self.helper, self.candidate, self.admission)

    def test_dependency_admission_binds_actual_recipe_and_artifact_proof(self):
        for admission in (None, {**self.admission, 'candidate_revision': '3' * 40},
                          {**self.admission, 'artifact': {'compile_manifest_sha256': 'c' * 64}}):
            with self.subTest(admission=admission):
                with self.assertRaisesRegex(context_module.Refusal, 'admission proof'):
                    context_module.dependency_identity({**self.document, 'candidate_admission': admission},
                                                       self.helper, self.candidate, self.admission)

    def test_cargo_tested_sha_names_candidate_and_still_reaches_full_content_checks(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            producer = SimpleNamespace(BASE='contracts/', LOCKS={'phase': ('reference',)},
                                       lock_union=mock.Mock(side_effect=RuntimeError('full frozen lock check reached')))
            ctx = SimpleNamespace(helper_revision=self.helper, candidate_revision=self.candidate,
                                  candidate_admission=self.admission, data={'cargo_root': str(root)},
                                  module=mock.Mock(return_value=producer), sources=SimpleNamespace(read=lambda _: b'frozen'))
            report = {**self.document, 'tested_sha': self.candidate, 'version': 'som.product-cargo-closure.v1',
                      'status': 'registry_closure_verified_product_gates_unrun', 'vendor_package_count': 352}
            with mock.patch.dict(sys.modules, {'full_product_context': context_module}):
                (root / 'receipt.json').write_text(json.dumps(report))
                with self.assertRaisesRegex(RuntimeError, 'full frozen lock check reached'):
                    cargo.prepare(ctx)
                producer.lock_union.assert_called_once()
                for field, value in (('tested_sha', self.helper), ('source_revision', self.candidate),
                                     ('helper_revision', None), ('candidate_revision', None)):
                    with self.subTest(field=field):
                        producer.lock_union.reset_mock()
                        (root / 'receipt.json').write_text(json.dumps({**report, field: value}))
                        with self.assertRaises(context_module.Refusal):
                            cargo.prepare(ctx)
                        producer.lock_union.assert_not_called()

    def test_wheel_receipt_checks_candidate_roles_before_environment_use(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            ctx = object.__new__(context_module.Context)
            ctx.helper_revision, ctx.candidate_revision, ctx.candidate_admission = self.helper, self.candidate, self.admission
            ctx.data = {'wheel_root': str(root)}
            report = {**self.document, 'version': 'som.hosted-python-wheel-closure-qualification.v1',
                      'wheel_closure_qualification_complete': True, 'all_named_environment_checks_complete': True}
            for field, value in (('helper_revision', None), ('candidate_revision', '3' * 40),
                                 ('candidate_admission', {**self.admission, 'recipe': {'sha256': 'd' * 64}})):
                with self.subTest(field=field):
                    (root / 'receipt.json').write_text(json.dumps({**report, field: value}))
                    with self.assertRaises(context_module.Refusal):
                        ctx.closure('observer')


class CarrierProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'})
        environment.start()
        self.addCleanup(environment.stop)
        paths = {'binary': b'fake compiler fixture; never executed', 'assets/manifest.json': b'fake asset',
                 'native/bin/ruff': b'fake ruff', 'native/bin/node': b'fake node',
                 'native/prettier/package.json': b'fake prettier', 'compiler-rustfmt': b'fake rustfmt'}
        for name, body in paths.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            path.chmod(0o755 if name in {'binary', 'native/bin/ruff', 'native/bin/node', 'compiler-rustfmt'} else 0o644)
        expected = carrier.identities(self.root)
        self.proof = {'tested_sha': '2' * 40, 'experiment_complete': True, 'identity_stability_passed': True,
                      'passed': 2041, 'initial_identities': expected, 'final_identities': expected,
                      'cleanup': {'cleanup_passed': True}}
        self.manifest = self.root / 'compile-proof.json'
        self.manifest.write_bytes(carrier.canonical(self.proof))
        self.args = SimpleNamespace(binary=self.root / 'binary', assets=self.root / 'assets',
                                    native=self.root / 'native', rustfmt=self.root / 'compiler-rustfmt',
                                    compile_manifest=self.manifest, candidate_revision='2' * 40,
                                    out=self.root / 'carrier-out')

    def test_original_carrier_defaults_to_no_rebuild_and_preserves_proof(self):
        record = carrier.pack(self.args)
        self.assertIs(record['compiler_rebuilt'], False)
        self.assertEqual(record['origin_role'], 'original already tested candidate bytes')
        self.assertEqual((self.args.out / 'compile-manifest.json').read_bytes(), self.manifest.read_bytes())

    def test_byte_identical_rebuild_records_new_origin_and_restores_same_identities(self):
        self.args.byte_identical_rebuild = True
        record = carrier.pack(self.args)
        self.assertIs(record['compiler_rebuilt'], True)
        self.assertEqual(record['origin_role'], 'new build compared byte-for-byte with the admitted old candidate')
        restored = carrier.restore(self.args.out, self.root / 'restored', self.manifest, self.args.candidate_revision)
        self.assertEqual(restored['identity'], self.proof['initial_identities'])
        self.assertEqual((self.args.out / 'compile-manifest.json').read_bytes(), self.manifest.read_bytes())

    def test_rebuild_flag_cannot_waive_binary_asset_or_tool_bytes_and_mode(self):
        self.args.byte_identical_rebuild = True
        for name in ('binary', 'assets/manifest.json', 'native/bin/ruff', 'compiler-rustfmt'):
            path = self.root / name
            original, mode = path.read_bytes(), path.stat().st_mode & 0o777
            for change in ('bytes', 'mode'):
                with self.subTest(name=name, change=change):
                    path.write_bytes(original + b'changed') if change == 'bytes' else path.chmod(mode ^ 0o100)
                    with self.assertRaisesRegex(ValueError, 'bytes changed before packing'):
                        carrier.pack(self.args)
                    self.assertFalse(self.args.out.exists())
                    path.write_bytes(original)
                    path.chmod(mode)

    def test_long_license_paths_roundtrip_without_pax_headers(self):
        path = self.root / 'native/licenses/node' / ('component-' + 'x' * 80) / 'LICENSE'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'Synthetic public license; never executed.\n')
        self.assertGreater(len(path.relative_to(self.root).as_posix()), 100)
        record = carrier.pack(self.args)
        with carrier.tarfile.open(self.args.out / 'candidate.tar.gz', 'r:gz') as archive:
            self.assertTrue(all(not member.pax_headers for member in archive))
        restored = carrier.restore(self.args.out, self.root / 'restored', self.manifest, self.args.candidate_revision)
        self.assertEqual(restored['identity'], self.proof['initial_identities'])
        self.assertEqual((self.root / 'restored' / path.relative_to(self.root)).read_bytes(), path.read_bytes())
        self.assertEqual(record['files'][path.relative_to(self.root).as_posix()]['sha256'], carrier.digest(path.read_bytes()))

    def test_too_long_paths_fail_instead_of_selecting_extended_headers(self):
        path = self.root / 'native/licenses' / ('a' * 100) / ('b' * 100) / ('c' * 100) / 'LICENSE'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'Synthetic public license.\n')
        with self.assertRaises(ValueError):
            carrier.pack(self.args)
        self.assertFalse((self.args.out / 'carrier.json').exists())


class Phase2ResultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / 'phase2-python'
        (self.output / 'cases').mkdir(parents=True)
        (self.root / 'checker-postchecks').mkdir()
        self.ids, self.selected = ['A', 'B', 'C'], ['A', 'C']
        self.sources = {name: ('exact helper source ' + name).encode() for name in
                        (driver.SELF, driver.CONTEXT, 'som-rs/behavior/phase2/check_phase2.py')}
        self.ctx = SimpleNamespace(repo=self.root, work=self.root, helper_revision='1' * 40,
                                   candidate_revision='2' * 40, sources=SimpleNamespace(read=self.sources.__getitem__),
                                   data={'stage': 'phase2-python', 'shard': {'index': 0, 'count': 2}})
        self.command = {'exit_code': 0, 'wait_completed': True, 'owned_group_cleanup_passed': True, 'pid': 12345}
        self.summary = {'version': 'som.full-product-phase2-shard.v1', **self.ctx.data,
                        'helper_revision': self.ctx.helper_revision, 'candidate_revision': self.ctx.candidate_revision,
                        'selected_ids': self.selected, 'whole_inventory_total': len(self.ids),
                        'whole_inventory_sha256': driver.digest(driver.canonical(self.ids)),
                        'whole_inventory_complete': False, 'shard_complete': True, 'missing': [],
                        'worker_exit_codes': [0, 0], 'product_acceptance_passed': False, 'rows': []}
        for identity in self.selected:
            raw = driver.canonical({'id': identity, 'passed': True, 'full_transcript': ['original case evidence']}) + b'\n'
            (self.output / 'cases' / (identity + '.json')).write_bytes(raw)
            self.summary['rows'].append({'id': identity, 'passed': True, 'raw_sha256': driver.digest(raw)})
        self.summary_path = self.output / 'hosted-shard-summary.json'
        self.summary_path.write_bytes(driver.canonical(self.summary))
        self.proof = {'version': 'som.full-product-checker-postcheck.v1', 'tool': 'som-rs/behavior/phase2/check_phase2.py',
                      'pid': self.command['pid'], 'helper_revision': self.ctx.helper_revision,
                      'candidate_revision': self.ctx.candidate_revision, **self.ctx.data,
                      'source_sha256': {name: driver.digest(raw) for name, raw in self.sources.items()},
                      'source_runtime_tools_inputs_unchanged': True, 'product_acceptance_passed': False}
        self.proof_path = self.root / 'checker-postchecks/12345.json'
        self.proof_path.write_bytes(driver.canonical(self.proof))
        alias = mock.patch.dict(sys.modules, {'full_product_context': context_module})
        alias.start()
        self.addCleanup(alias.stop)

    def results(self, command=None, error=None):
        return driver.phase2_python_results(self.ctx, self.ids, self.selected, self.output, lambda value: value,
                                            2, self.command if command is None else command, error)

    def assert_failed_scope(self, rows):
        self.assertEqual([row['id'] for row in rows], self.selected)
        self.assertFalse(any(row['passed'] for row in rows))
        self.assertTrue(all(row['case_result']['full_transcript'] == ['original case evidence'] for row in rows))

    def test_success_binds_every_raw_row_summary_and_process_postcheck(self):
        rows = self.results()
        self.assertEqual([row['id'] for row in rows], self.selected)
        self.assertTrue(all(row['passed'] for row in rows))
        self.assertTrue(all(row['gates']['hosted_shard_summary']['raw_sha256'] for row in rows))

    def test_all_true_rows_cannot_hide_nonzero_driver_or_missing_wait_cleanup(self):
        for field, value in (('exit_code', 1), ('exit_code', -9), ('exit_code', None),
                             ('wait_completed', False), ('owned_group_cleanup_passed', False)):
            with self.subTest(field=field, value=value):
                self.assert_failed_scope(self.results({**self.command, field: value}))

    def test_tool_cleanup_exception_keeps_full_rows_but_fails_them(self):
        self.assert_failed_scope(self.results(error={'error': 'owned group cleanup failed'}))

    def test_phase2_entry_point_consumes_failed_tool_return(self):
        checker = SimpleNamespace(cases=lambda: [{'id': identity, 'checker': 'fixture'} for identity in self.ids],
                                  key=lambda value: value)
        with mock.patch.dict(sys.modules, {'check_phase2': checker}), \
             mock.patch.object(driver, 'tool', return_value={**self.command, 'exit_code': 1}):
            ids, rows = driver.phase2_python(self.ctx)
        self.assertEqual(ids, self.ids)
        self.assert_failed_scope(rows)

    def test_phase2_entry_point_preserves_rows_after_tool_exception(self):
        checker = SimpleNamespace(cases=lambda: [{'id': identity, 'checker': 'fixture'} for identity in self.ids],
                                  key=lambda value: value)
        with mock.patch.dict(sys.modules, {'check_phase2': checker}), \
             mock.patch.object(driver, 'tool', side_effect=ValueError('postcheck failed')):
            ids, rows = driver.phase2_python(self.ctx)
        self.assertEqual(ids, self.ids)
        self.assert_failed_scope(rows)

    def test_summary_binds_order_scope_shard_candidate_and_complete_raw_rows(self):
        for field, value in (('selected_ids', list(reversed(self.selected))), ('shard', {'index': 1, 'count': 2}),
                             ('helper_revision', '3' * 40), ('candidate_revision', '3' * 40),
                             ('whole_inventory_sha256', '0' * 64), ('rows', self.summary['rows'][:1]),
                             ('missing', ['C']), ('shard_complete', False)):
            with self.subTest(field=field):
                self.summary_path.write_bytes(driver.canonical({**self.summary, field: value}))
                self.assert_failed_scope(self.results())

    def test_nonzero_or_missing_worker_exit_cannot_pass_with_true_rows(self):
        for codes in ([0, 1], [0], [0, None], [0, False]):
            with self.subTest(codes=codes):
                self.summary_path.write_bytes(driver.canonical({**self.summary, 'worker_exit_codes': codes}))
                self.assert_failed_scope(self.results())

    def test_rehashed_named_case_cannot_replace_summary_raw_hash(self):
        path = self.output / 'cases/A.json'
        body = json.loads(path.read_bytes())
        body['full_transcript'].append('changed evidence')
        path.write_bytes(driver.canonical(body))
        rows = self.results()
        self.assertEqual([row['id'] for row in rows], self.selected)
        self.assertFalse(any(row['passed'] for row in rows))

    def test_extra_saved_case_cannot_expand_the_admitted_shard(self):
        (self.output / 'cases/B.json').write_bytes(driver.canonical({'id': 'B', 'passed': True}))
        self.assert_failed_scope(self.results())

    def test_missing_or_changed_driver_postcheck_fails_complete_rows(self):
        self.proof_path.rename(self.proof_path.with_suffix('.missing'))
        self.assert_failed_scope(self.results())
        for field, value in (('pid', 111), ('candidate_revision', '3' * 40),
                             ('source_runtime_tools_inputs_unchanged', False), ('source_sha256', {})):
            with self.subTest(field=field):
                self.proof_path.write_bytes(driver.canonical({**self.proof, field: value}))
                self.assert_failed_scope(self.results())


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Run these fixtures on GitHub only')
    unittest.main()
