"""GitHub-only fixtures. Git, physical files and the bootstrap are real.

GitHub environment/API records and the initial main-entry namespace are explicit
test doubles. These fixtures cannot qualify an actual hosted bootstrap or QA.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock
import uuid

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BOOTSTRAP_PATH = 'som-rs/scripts/hosted_readiness/native_source_bootstrap.py'
BASE = 'som-rs/scripts/hosted_readiness/'
GRAPH = BASE + 'same_candidate_graph.py'
AUTHORITY = BASE + 'native_qa_authority.py'
M6_ENTRY = 'som-rs/scripts/react_output_acceptance/m6_native_entry.py'
ROOT_WORKFLOW = '.github/workflows/rust-hosted-readiness.yml'
CALLED_WORKFLOW = '.github/workflows/rust-same-candidate-products.yml'
INPUT_ACTION = '.github/actions/same-candidate-inputs/action.yml'
GRAPH_NAME = '_som_bootstrap_same_candidate_graph'
AUTHORITY_NAME = '_som_same_candidate_native_authority'
_FIXED_FILES = (BOOTSTRAP_PATH, BASE + 'bound_source.py', GRAPH, AUTHORITY,
                BASE + 'readiness.json', ROOT_WORKFLOW, CALLED_WORKFLOW, INPUT_ACTION)


class Fixture:
    """Run this only inside the isolated GitHub fixture job."""
    def __init__(self, case, graph_suffix=''):
        self.case = case
        self.stack = ExitStack()
        case.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='som-bootstrap-source-fixture-')))
        for name in _FIXED_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
            target.chmod(0o644)
        if graph_suffix:
            with (self.root / GRAPH).open('a') as stream:
                stream.write('\n' + graph_suffix + '\n')
        entry = self.root / M6_ENTRY
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text('"""Synthetic initial entry; no product code or authority."""\n')
        entry.chmod(0o644)
        self.git('init', '-b', 'main')
        self.git('add', '.')
        self.git('commit', '-m', 'Synthetic bootstrap source fixture')
        self.revision = self.git('rev-parse', 'HEAD').strip()
        self.env = {
            'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux',
            'RUNNER_NAME': 'synthetic-source-fixture-runner', 'GITHUB_REPOSITORY': 'faberline/som',
            'GITHUB_REPOSITORY_ID': '1390374878', 'GITHUB_SERVER_URL': 'https://github.com',
            'GITHUB_API_URL': 'https://api.github.com',
            'GITHUB_SHA': self.revision, 'GITHUB_WORKFLOW_SHA': self.revision,
            'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_WORKFLOW_REF': 'faberline/som/' + ROOT_WORKFLOW + '@refs/heads/main',
            'GITHUB_JOB': 'required-callbacks', 'GITHUB_RUN_ID': '101', 'GITHUB_RUN_ATTEMPT': '2',
            'SOM_DEPENDENCY_ACTIONS_TOKEN': 'synthetic-api-double-no-network',
        }
        self.run = {
            'id': 101, 'head_sha': self.revision, 'run_attempt': 2, 'event': 'push',
            'repository': {'full_name': 'faberline/som', 'id': 1390374878}, 'path': ROOT_WORKFLOW,
            'head_branch': 'main', 'status': 'in_progress', 'workflow_id': 303,
            'referenced_workflows': [{'path': 'faberline/som/' + CALLED_WORKFLOW + '@refs/heads/main',
                                      'sha': self.revision, 'ref': 'refs/heads/main'}],
        }
        self.workflow = {'id': 303, 'path': ROOT_WORKFLOW}
        self.job = {
            'id': 202, 'run_id': 101, 'head_sha': self.revision,
            'name': 'same-candidate-full-products / required-callbacks (m6-react003)',
            'runner_name': self.env['RUNNER_NAME'], 'status': 'in_progress',
        }
        self.jobs = {'total_count': 1, 'jobs': [self.job]}
        self.requests = []
        self.module_name = '_synthetic_bootstrap_fixture_' + uuid.uuid4().hex
        self.module = ModuleType(self.module_name)
        self.module.__file__ = str(self.root / BOOTSTRAP_PATH)
        self.module.__package__ = ''
        sys.modules[self.module_name] = self.module
        self.stack.callback(lambda: sys.modules.pop(self.module_name, None))
        exec(compile((self.root / BOOTSTRAP_PATH).read_bytes(), self.module.__file__, 'exec', dont_inherit=True),
             self.module.__dict__)
        self.main = ModuleType('__main__')
        self.main.__file__ = str(entry)
        self.stack.enter_context(mock.patch.dict(sys.modules, {'__main__': self.main}))
        self.stack.enter_context(mock.patch.dict(os.environ, self.env))
        self.stack.enter_context(mock.patch.object(self.module, '_github_json', side_effect=self.api))
        # Cleanup restores only these synthetic module names. Real finder/spec
        # ownership is tested separately and is never removed by the bootstrap.
        self.stack.callback(lambda: sys.modules.pop(GRAPH_NAME, None))
        self.stack.callback(lambda: sys.modules.pop(AUTHORITY_NAME, None))

    def git(self, *args):
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_AUTHOR_NAME': 'Synthetic fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
               'GIT_COMMITTER_NAME': 'Synthetic fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
                                       env=env, stderr=subprocess.DEVNULL, timeout=30).decode()

    def api(self, suffix):
        self.requests.append(suffix)
        records = {'/actions/runs/101': self.run, '/actions/workflows/303': self.workflow,
                   '/actions/runs/101/attempts/2/jobs?per_page=100&page=1': self.jobs}
        self.case.assertIn(suffix, records)
        return json.loads(json.dumps(records[suffix]))

    def bootstrap(self):
        return self.module.checked_bootstrap(self.root, self.revision, self.revision)

    def select_job(self, job):
        os.environ['GITHUB_JOB'] = job
        direct_names = {'experimental-native-compile': 'Full Python and Rust compile and assembly (unsealed experiment)',
                        'current-product-rust-tools': 'current-product-rust-tools'}
        prefix = direct_names[job] if job in self.module._ROOT_JOBS else 'same-candidate-full-products / ' + job
        self.job['name'] = prefix + (' (phase1, 0, 1)' if job == 'products' else '')
        self.main.__file__ = str(self.root / BOOTSTRAP_PATH)


class SourceStrategyParentTests(unittest.TestCase):
    def test_fixed_deferred_graph_import_compiles_but_admission_importer_refuses_it(self):
        f = Fixture(self, graph_suffix='def active_controller_handle():\n    from _som_native_active_controls_binding import controls\n    return controls')
        with f.bootstrap() as bootstrap:
            bootstrap.load_admission_module('same-candidate-graph')
            with self.assertRaisesRegex(ValueError, 'captured fixed stdlib import'):
                sys.modules[GRAPH_NAME].active_controller_handle()

    def test_fixed_active_binding_import_at_graph_module_scope_is_refused_before_exec(self):
        f = Fixture(self, graph_suffix='from _som_native_active_controls_binding import controls')
        with f.bootstrap() as bootstrap:
            with self.assertRaisesRegex(ValueError, 'fixed deferred Graph function'):
                bootstrap.load_admission_module('same-candidate-graph')
        self.assertNotIn(GRAPH_NAME, sys.modules)

    def test_fresh_actual_self_entry_is_registered_before_first_admission_call(self):
        f = Fixture(self, graph_suffix='def admit_source(repo, helper, candidate):\n    raise RuntimeError("explicit protocol stop before real admission and QA")')
        f.select_job('experimental-native-compile')
        name = f.module._TRUSTED_ENTRY_NAME
        with f.bootstrap() as bootstrap:
            with self.assertRaisesRegex(RuntimeError, 'explicit protocol stop'):
                bootstrap.execute_entry('not-yet-admitted-qa', [])
            record = bootstrap._records[name]
            module = bootstrap._modules[name]
            self.assertTrue(record['executed'])
            self.assertIs(record['globals'], module.__dict__)
            self.assertTrue(any(module._trusted_run.__code__ is code for code in record['codes']))
            self.assertIs(sys.modules[name], module)
        self.assertNotIn(name, sys.modules)

    def test_actual_outputs_matrix_is_not_a_strategy_or_a_job_name_suffix_grant(self):
        f = Fixture(self)
        workflow = (f.root / CALLED_WORKFLOW).read_text()
        self.assertIn('    outputs:\n      matrix:', workflow)
        for job in f.module._CALLED_JOBS:
            display, matrix = f.module._job_display_name(workflow, job)
            self.assertEqual(display, job)
            self.assertEqual(matrix, job in {'products', 'required-callbacks'})
        f.select_job('bind')
        with f.bootstrap() as bootstrap:
            self.assertEqual(bootstrap._binding['job'], 'bind')
        f.job['name'] += ' (unlisted-output-matrix-suffix)'
        with self.assertRaises(ValueError):
            f.bootstrap()

    def test_duplicate_or_inline_strategy_cannot_hide_in_an_outputs_block(self):
        f = Fixture(self)
        workflow = (f.root / CALLED_WORKFLOW).read_text()
        inline = workflow.replace('  bind:\n', '  bind:\n    strategy: {matrix: {unlisted: [x]}}\n', 1)
        with self.assertRaisesRegex(ValueError, 'source job strategy must be an exact block'):
            f.module._job_display_name(inline, 'bind')
        duplicate = workflow.replace('  bind:\n',
            '  bind:\n    strategy:\n      matrix:\n        first: [x]\n'
            '    strategy:\n      matrix:\n        second: [y]\n', 1)
        with self.assertRaisesRegex(ValueError, 'ambiguous source job strategy'):
            f.module._job_display_name(duplicate, 'bind')


class NativeSourceBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ.get('GITHUB_ACTIONS'), 'true', 'fixtures must run on GitHub')
        self.assertEqual(sys.platform, 'linux')
        self.assertTrue(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)

    def test_real_git_and_fixed_group_register_code_and_globals_before_each_exec(self):
        f = Fixture(self)
        with f.bootstrap() as bootstrap:
            self.assertNotIn(GRAPH_NAME, sys.modules)
            self.assertNotIn(AUTHORITY_NAME, sys.modules)
            calls = []
            old_profile = sys.getprofile()
            def profile(frame, event, argument):
                if event == 'call' and frame.f_code.co_name == '<module>':
                    for name, record in bootstrap._records.items():
                        if frame.f_code is record['code']:
                            self.assertIs(frame.f_globals, record['globals'])
                            self.assertFalse(record['executed'])
                            self.assertIs(sys.modules[name].__dict__, frame.f_globals)
                            calls.append(name)
            sys.setprofile(profile)
            try:
                graph = bootstrap.load_admission_module('same-candidate-graph')
            finally:
                sys.setprofile(old_profile)
            self.assertEqual(calls, [AUTHORITY_NAME, GRAPH_NAME])
            self.assertIs(graph, bootstrap.load_admission_module('same-candidate-graph'))
            self.assertFalse(hasattr(graph, 'raw_module'))
            self.assertFalse(hasattr(graph, 'restore_stage_set'))
            bootstrap.verify()
            self.assertEqual(len(bootstrap._snapshot.entries), len(f.git('ls-tree', '-r', '--name-only', f.revision).splitlines()))
        self.assertNotIn(GRAPH_NAME, sys.modules)
        self.assertNotIn(AUTHORITY_NAME, sys.modules)

    def test_each_current_reusable_job_keeps_root_and_called_workflow_identity(self):
        f = Fixture(self)
        for job in f.module._CALLED_JOBS:
            with self.subTest(job=job):
                f.select_job(job)
                with f.bootstrap() as bootstrap:
                    self.assertEqual(bootstrap._binding['root_workflow'], ROOT_WORKFLOW)
                    self.assertEqual(bootstrap._binding['called_workflow'], CALLED_WORKFLOW)
                    self.assertEqual(bootstrap._binding['job'], job)
                    self.assertEqual(bootstrap._binding['github_workflow_sha'], f.revision)

    def test_both_current_direct_admission_jobs_keep_the_root_workflow(self):
        f = Fixture(self)
        for job in f.module._ROOT_JOBS:
            with self.subTest(job=job):
                f.select_job(job)
                with f.bootstrap() as bootstrap:
                    self.assertIsNone(bootstrap._binding['called_workflow'])

    def test_changed_source_bytes_mode_entry_and_workflow_refuse_before_helper_exec(self):
        for path, mutation in ((GRAPH, 'bytes'), (AUTHORITY, 'mode'), (M6_ENTRY, 'bytes'),
                               (ROOT_WORKFLOW, 'bytes'), (CALLED_WORKFLOW, 'bytes'), (INPUT_ACTION, 'bytes')):
            with self.subTest(path=path, mutation=mutation):
                with ExitStack() as scope:
                    f = Fixture(self)
                    path = f.root / path
                    if mutation == 'bytes':
                        with path.open('ab') as stream:
                            stream.write(b'\n# changed physical bytes\n')
                    else:
                        path.chmod(0o755)
                    with self.assertRaisesRegex(ValueError, 'differ'):
                        f.bootstrap()
                    self.assertNotIn(GRAPH_NAME, sys.modules)
                    self.assertNotIn(AUTHORITY_NAME, sys.modules)
                    # Complete this fixture before the next shared namespace.
                    f.stack.close()

    def test_actual_head_change_cannot_reuse_the_original_bootstrap_pins(self):
        f = Fixture(self)
        bootstrap = f.bootstrap()
        f.git('commit', '--allow-empty', '-m', 'Different actual HEAD')
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            bootstrap.load_admission_module('same-candidate-graph')
        self.assertNotIn(GRAPH_NAME, sys.modules)

    def test_missing_transitive_authority_is_refused_before_any_group_body_exec(self):
        f = Fixture(self)
        bootstrap = f.bootstrap()
        (f.root / AUTHORITY).unlink()
        with self.assertRaises(FileNotFoundError):
            bootstrap.load_admission_module('same-candidate-graph')
        self.assertEqual(bootstrap._records, {})
        self.assertNotIn(GRAPH_NAME, sys.modules)
        self.assertNotIn(AUTHORITY_NAME, sys.modules)

    def test_new_repository_import_in_the_fixed_group_is_refused_before_any_helper_exec(self):
        f = Fixture(self, graph_suffix='import native_hidden_fixture_helper')
        with f.bootstrap() as bootstrap:
            with self.assertRaisesRegex(ValueError, 'static import closure differs'):
                bootstrap.load_admission_module('same-candidate-graph')
            self.assertEqual(bootstrap._records, {})
            self.assertNotIn(GRAPH_NAME, sys.modules)
            self.assertNotIn(AUTHORITY_NAME, sys.modules)

    def test_matching_timestamp_pyc_and_dangling_adjacent_pyc_are_refused(self):
        for mode in ('timestamp', 'dangling'):
            with self.subTest(mode=mode):
                f = Fixture(self)
                graph = f.root / GRAPH
                if mode == 'timestamp':
                    cached = Path(py_compile.compile(str(graph), doraise=True,
                        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
                    self.assertTrue(cached.is_file())
                else:
                    graph.with_suffix('.pyc').symlink_to('missing-fixture-bytecode')
                with self.assertRaisesRegex(ValueError, 'bytecode refused'):
                    f.bootstrap()
                f.stack.close()

    def test_link_and_hardlink_source_aliases_are_refused(self):
        for alias in ('symlink', 'hardlink'):
            with self.subTest(alias=alias):
                f = Fixture(self)
                graph = f.root / GRAPH
                copied = f.root / 'opaque-fixture-graph-copy'
                graph.rename(copied)
                if alias == 'symlink':
                    graph.symlink_to(copied)
                else:
                    os.link(copied, graph)
                with self.assertRaisesRegex(ValueError, 'unlinked bootstrap source required'):
                    f.bootstrap()
                f.stack.close()

    def test_actual_environment_fields_cannot_be_replaced_by_call_arguments(self):
        f = Fixture(self)
        changes = {'GITHUB_SHA': '0' * 40, 'GITHUB_WORKFLOW_SHA': '0' * 40,
                   'GITHUB_WORKFLOW_REF': 'faberline/som/' + CALLED_WORKFLOW + '@refs/heads/main',
                   'GITHUB_REPOSITORY': 'other/repository', 'GITHUB_EVENT_NAME': 'pull_request',
                   'GITHUB_REPOSITORY_ID': '1', 'GITHUB_SERVER_URL': 'https://foreign.invalid',
                   'GITHUB_API_URL': 'https://foreign.invalid',
                   'GITHUB_RUN_ID': '0', 'GITHUB_RUN_ATTEMPT': '0', 'GITHUB_JOB': 'foreign-job',
                   'RUNNER_ENVIRONMENT': 'self-hosted', 'RUNNER_OS': 'Windows',
                   'GITHUB_REF': 'refs/heads/foreign'}
        for key, value in changes.items():
            with self.subTest(field=key), mock.patch.dict(os.environ, {key: value}):
                with self.assertRaises((ValueError, AssertionError)):
                    f.bootstrap()
                self.assertNotIn(GRAPH_NAME, sys.modules)

    def test_checked_process_identity_cannot_change_between_bootstrap_and_first_helper_exec(self):
        f = Fixture(self)
        bootstrap = f.bootstrap()
        with mock.patch.dict(os.environ, {'GITHUB_JOB': 'products'}):
            with self.assertRaisesRegex(ValueError, 'initial process identity changed'):
                bootstrap.load_admission_module('same-candidate-graph')
        self.assertNotIn(GRAPH_NAME, sys.modules)
        bootstrap.close()

    def test_old_graph_entry_and_unlisted_entry_path_cannot_claim_preexecution_bootstrap(self):
        f = Fixture(self)
        for entry in (GRAPH, AUTHORITY, BASE + 'bound_source.py', BASE + 'readiness.json'):
            with self.subTest(entry=entry):
                f.main.__file__ = str(f.root / entry)
                with self.assertRaisesRegex(ValueError, 'already executed|unlisted bootstrap entry'):
                    f.bootstrap()

    def test_live_run_workflow_called_source_and_runner_job_are_independently_joined(self):
        f = Fixture(self)
        changes = ((f.run, 'head_sha', '0' * 40), (f.run, 'run_attempt', 1),
                   (f.run, 'path', CALLED_WORKFLOW), (f.run, 'head_branch', 'other'),
                   (f.workflow, 'path', CALLED_WORKFLOW), (f.job, 'head_sha', '0' * 40),
                   (f.job, 'run_id', 999), (f.job, 'runner_name', 'other-runner'),
                   (f.job, 'status', 'completed'), (f.job, 'name', 'foreign / required-callbacks'),
                   (f.run['referenced_workflows'][0], 'sha', '0' * 40),
                   (f.run['referenced_workflows'][0], 'ref', 'refs/heads/other'))
        for record, key, replacement in changes:
            with self.subTest(field=key, replacement=replacement):
                original = record[key]
                record[key] = replacement
                try:
                    with self.assertRaisesRegex(ValueError, 'differs|missing'):
                        f.bootstrap()
                finally:
                    record[key] = original

    def test_incomplete_duplicate_or_ambiguous_live_job_pages_are_refused(self):
        f = Fixture(self)
        original = json.loads(json.dumps(f.jobs))
        for rows, total in (([f.job], 2), ([f.job, f.job], 2),
                            ([f.job, dict(f.job, id=204)], 2)):
            with self.subTest(total=total, rows=len(rows)):
                f.jobs.update(total_count=total, jobs=rows)
                with self.assertRaisesRegex(ValueError, 'universe differs|ambiguous'):
                    f.bootstrap()
        f.jobs.update(original)

    def test_forged_preload_sha_marker_never_grants_admission_module_ownership(self):
        f = Fixture(self)
        foreign = ModuleType(AUTHORITY_NAME)
        foreign.__file__ = str(f.root / AUTHORITY)
        foreign.__som_raw_sha256__ = hashlib.sha256((f.root / AUTHORITY).read_bytes()).hexdigest()
        with mock.patch.dict(sys.modules, {AUTHORITY_NAME: foreign}):
            with self.assertRaisesRegex(ValueError, 'preloaded'):
                f.bootstrap()
        bootstrap = f.bootstrap()
        with mock.patch.dict(sys.modules, {AUTHORITY_NAME: foreign}):
            with self.assertRaisesRegex(ValueError, 'preloaded'):
                bootstrap.load_admission_module('same-candidate-graph')
            self.assertIs(sys.modules[AUTHORITY_NAME], foreign)

    def test_unlisted_role_path_raw_code_and_generic_module_name_are_unavailable(self):
        f = Fixture(self)
        with f.bootstrap() as bootstrap:
            for role in ('collector', 'reuse-runtime', AUTHORITY, 'full_product_context', '', None):
                with self.subTest(role=role), self.assertRaises(ValueError):
                    bootstrap.load_admission_module(role)
            with self.assertRaises(TypeError):
                bootstrap.load_admission_module('same-candidate-graph', raw=b'pass')
            self.assertNotIn(GRAPH_NAME, sys.modules)

    def test_real_existing_raw_finder_and_checked_spec_stay_the_same_objects(self):
        f = Fixture(self)
        authority = ModuleType('_synthetic_external_authority_control')
        authority.__file__ = str(REPO / AUTHORITY)
        exec(compile((REPO / AUTHORITY).read_bytes(), authority.__file__, 'exec', dont_inherit=True), authority.__dict__)
        finder = authority.RawQaFinder(lambda path: (f.root / path).read_bytes(), f.root, [], []).install()
        try:
            checked_spec = importlib.util.spec_from_file_location
            with f.bootstrap() as bootstrap:
                bootstrap.load_admission_module('same-candidate-graph')
                self.assertIs(importlib.util.spec_from_file_location, checked_spec)
                self.assertIn(finder, sys.meta_path)
            self.assertIs(importlib.util.spec_from_file_location, checked_spec)
            self.assertIn(finder, sys.meta_path)
            finder.verify_installed()
        finally:
            finder.close()

    def test_spec_ownership_change_during_live_api_check_is_refused_before_exec(self):
        f = Fixture(self)
        original_spec = importlib.util.spec_from_file_location
        original_api = f.api
        def changed(suffix):
            importlib.util.spec_from_file_location = lambda *args, **kwargs: None
            return original_api(suffix)
        try:
            with mock.patch.object(f.module, '_github_json', side_effect=changed):
                with self.assertRaisesRegex(ValueError, 'finder/spec ownership changed'):
                    f.bootstrap()
            self.assertNotIn(GRAPH_NAME, sys.modules)
        finally:
            importlib.util.spec_from_file_location = original_spec

    def test_function_identity_cannot_be_replaced_with_a_matching_mutable_sha_marker(self):
        f = Fixture(self)
        bootstrap = f.bootstrap()
        bootstrap.load_admission_module('same-candidate-graph')
        graph = sys.modules[GRAPH_NAME]
        original = graph.admit_source
        graph.admit_source = lambda *args: None
        try:
            with self.assertRaisesRegex(ValueError, 'namespace state changed|code/globals ownership changed'):
                bootstrap.verify()
        finally:
            graph.admit_source = original
            bootstrap.close()

    def test_original_admission_error_survives_a_secondary_ownership_failure(self):
        suffix = "_fixture_primary = RuntimeError('injected source admission failure; no acceptance')\ndef admit_source(repo, helper, candidate):\n    global admit_source\n    admit_source = lambda *args: None\n    raise _fixture_primary"
        f = Fixture(self, graph_suffix=suffix)
        bootstrap = f.bootstrap()
        facade = bootstrap.load_admission_module('same-candidate-graph')
        graph = sys.modules[GRAPH_NAME]
        original = graph.admit_source
        primary = graph._fixture_primary
        try:
            caught = None
            try:
                facade.admit_source(f.root, f.revision, f.revision)
            except RuntimeError as error:
                caught = error
                self.assertIs(error, primary)
                self.assertIsNotNone(error.__traceback__)
            self.assertIs(caught, primary)
            secondary = primary.__som_bootstrap_secondary_errors__
            self.assertEqual(secondary[0]['phase'], 'post-admission-source-check')
            self.assertIsInstance(secondary[0]['exception'], ValueError)
            self.assertIsNotNone(secondary[0]['exception'].__traceback__)
        finally:
            graph.admit_source = original
            bootstrap.close()

    def test_source_owned_constant_and_class_method_mutation_cannot_keep_the_code_ledger_valid(self):
        f = Fixture(self)
        bootstrap = f.bootstrap()
        bootstrap.load_admission_module('same-candidate-graph')
        authority = sys.modules[AUTHORITY_NAME]
        old = authority.ORDER['implementation_already_existed']
        authority.ORDER['implementation_already_existed'] = False
        try:
            with self.assertRaisesRegex(ValueError, 'namespace state changed'):
                bootstrap.verify()
        finally:
            authority.ORDER['implementation_already_existed'] = old
        original = authority.RawQaFinder.verify_installed
        authority.RawQaFinder.verify_installed = lambda self: None
        try:
            with self.assertRaisesRegex(ValueError, 'namespace state changed'):
                bootstrap.verify()
        finally:
            authority.RawQaFinder.verify_installed = original
            bootstrap.close()

    def test_actual_helper_body_failure_keeps_first_error_and_only_removes_owned_namespaces(self):
        suffix = "sys.modules['_som_same_candidate_native_authority'] = ModuleType('foreign-fixture-owner')\nraise RuntimeError('actual synthetic helper body first failure')"
        f = Fixture(self, graph_suffix=suffix)
        bootstrap = f.bootstrap()
        with self.assertRaisesRegex(RuntimeError, 'actual synthetic helper body first failure') as caught:
            bootstrap.load_admission_module('same-candidate-graph')
        self.assertNotIn(GRAPH_NAME, sys.modules)
        self.assertEqual(sys.modules[AUTHORITY_NAME].__name__, 'foreign-fixture-owner')
        secondary = caught.exception.__som_bootstrap_secondary_errors__
        self.assertEqual(secondary[0]['phase'], 'admission-module-failure-cleanup')
        self.assertIsInstance(secondary[0]['exception'], ValueError)

    def test_retired_entry_cannot_activate_even_when_fixture_result_identities_match(self):
        f = Fixture(self)
        with f.bootstrap() as bootstrap:
            facade = bootstrap.load_admission_module('same-candidate-graph')
            with self.assertRaisesRegex(ValueError, 'actual source-admission result'):
                bootstrap.activate(object(), {'source_authority_active': True})
            # The old initial-entry double cannot become the fresh bootstrap
            # entry. This is not native admission and never opens QA code.
            sources, authority, admission = object(), object(), {'product_acceptance_passed': False}
            bootstrap._admission = (sources, authority, admission)
            with self.assertRaisesRegex(ValueError, 'fresh ledger-owned trusted entry'):
                bootstrap.activate(sources, admission)
            self.assertFalse(hasattr(facade, 'load_module'))

    def test_context_exit_keeps_body_failure_and_still_removes_owned_modules_after_source_refusal(self):
        f = Fixture(self)
        primary = RuntimeError('actual fixture body failure before source postcheck')
        with self.assertRaises(RuntimeError) as caught:
            with f.bootstrap() as bootstrap:
                bootstrap.load_admission_module('same-candidate-graph')
                with (f.root / M6_ENTRY).open('ab') as stream:
                    stream.write(b'\n# actual changed entry bytes\n')
                raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.__som_bootstrap_secondary_errors__[0]['phase'], 'bootstrap-context-close')
        self.assertNotIn(GRAPH_NAME, sys.modules)
        self.assertNotIn(AUTHORITY_NAME, sys.modules)

    def test_real_graph_default_reader_mutation_refuses_before_foreign_reader_runs(self):
        f = Fixture(self)
        bootstrap = f.bootstrap()
        facade = bootstrap.load_admission_module('same-candidate-graph')
        function = sys.modules[GRAPH_NAME].source_workflow_binding
        original_defaults, original_code, original_globals = function.__defaults__, function.__code__, function.__globals__
        calls = []
        def foreign_reader(*args):
            calls.append(args)
            raise AssertionError('foreign reader must never execute')
        function.__defaults__ = (None, foreign_reader)
        try:
            self.assertIs(function.__code__, original_code)
            self.assertIs(function.__globals__, original_globals)
            with self.assertRaisesRegex(ValueError, 'namespace state changed'):
                facade.admit_source(f.root, f.revision, f.revision)
            self.assertEqual(calls, [])
        finally:
            function.__defaults__ = original_defaults
            bootstrap.close()

    def test_actual_keyword_default_binding_refuses_mutation_without_foreign_object_hooks(self):
        suffix = "def _fixture_keyword(*, reader=None):\n    return reader() if reader is not None else None"
        f = Fixture(self, graph_suffix=suffix)
        bootstrap = f.bootstrap()
        facade = bootstrap.load_admission_module('same-candidate-graph')
        function = sys.modules[GRAPH_NAME]._fixture_keyword
        original = function.__kwdefaults__
        calls = []
        class ForeignMeta(type):
            def __hash__(cls):
                calls.append('foreign metaclass hash')
                return 1
            def __eq__(cls, other):
                calls.append('foreign metaclass equality')
                return False
        class ForeignReader(metaclass=ForeignMeta):
            def __call__(self):
                calls.append('foreign reader call')
            def __getattribute__(self, name):
                calls.append('foreign object attribute')
                return object.__getattribute__(self, name)
        foreign = ForeignReader()
        original['reader'] = foreign
        try:
            with self.assertRaisesRegex(ValueError, 'namespace state changed'):
                facade.admit_source(f.root, f.revision, f.revision)
            self.assertEqual(calls, [])
        finally:
            original['reader'] = None
            function.__kwdefaults__ = original
            bootstrap.close()

    def test_actual_closure_cell_identity_and_cyclic_content_are_checked_before_admission(self):
        suffix = "def _fixture_factory():\n    binding = {'reader': None}\n    binding['cycle'] = binding\n    def bound():\n        return binding['reader']() if binding['reader'] is not None else None\n    return bound\n_fixture_bound = _fixture_factory()"
        f = Fixture(self, graph_suffix=suffix)
        bootstrap = f.bootstrap()
        facade = bootstrap.load_admission_module('same-candidate-graph')
        function = sys.modules[GRAPH_NAME]._fixture_bound
        cell = function.__closure__[0]
        original = cell.cell_contents
        bootstrap.verify()  # The unchanged cyclic binding must be supported.
        calls = []
        def foreign_reader():
            calls.append('foreign closure reader')
        try:
            original['reader'] = foreign_reader
            with self.assertRaisesRegex(ValueError, 'namespace state changed'):
                facade.admit_source(f.root, f.revision, f.revision)
            self.assertEqual(calls, [])
            original['reader'] = None
            bootstrap.verify()
            replacement = {'reader': None}
            replacement['cycle'] = replacement
            cell.cell_contents = replacement
            with self.assertRaisesRegex(ValueError, 'namespace state changed'):
                facade.admit_source(f.root, f.revision, f.revision)
            self.assertEqual(calls, [])
        finally:
            original['reader'] = None
            cell.cell_contents = original
            bootstrap.close()

    def test_actual_explicit_source_display_name_keeps_its_distinct_environment_job_id(self):
        f = Fixture(self)
        f.select_job('experimental-native-compile')
        expected = 'Full Python and Rust compile and assembly (unsealed experiment)'
        self.assertEqual(f.job['name'], expected)
        with f.bootstrap() as bootstrap:
            self.assertEqual(bootstrap._binding['job'], 'experimental-native-compile')
            self.assertEqual(bootstrap._binding['job_display_name'], expected)
            self.assertEqual(bootstrap._binding['job_api_name'], expected)
            self.assertEqual(bootstrap._binding['root_workflow'], ROOT_WORKFLOW)
            self.assertIsNone(bootstrap._binding['called_workflow'])

    def test_wrong_or_duplicate_explicit_name_runner_job_remains_refused(self):
        f = Fixture(self)
        f.select_job('experimental-native-compile')
        original = dict(f.job)
        for wrong in ('experimental-native-compile', 'Full Python and Rust compile and assembly',
                      'other / Full Python and Rust compile and assembly (unsealed experiment)',
                      'Full Python and Rust compile and assembly (unsealed experiment) (foreign)'):
            with self.subTest(name=wrong):
                f.job['name'] = wrong
                with self.assertRaisesRegex(ValueError, 'missing, ambiguous'):
                    f.bootstrap()
        f.job.update(original)
        f.jobs['jobs'] = [f.job, dict(f.job, id=204)]
        f.jobs['total_count'] = 2
        with self.assertRaisesRegex(ValueError, 'missing, ambiguous'):
            f.bootstrap()


if __name__ == '__main__':
    unittest.main()
