"""Same-candidate source/artifact refusal fixtures. Execute only on GitHub."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise RuntimeError('GitHub Linux fixtures only')
HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
HELPER = os.environ['HELPER_REVISION']


def module(name, path):
    raw = path.read_bytes()
    relative = path.relative_to(REPO).as_posix()
    assert raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
                                          'cat-file', 'blob', HELPER + ':' + relative])
    result = ModuleType(name)
    result.__file__, result.__cached__, result.__package__ = str(path), None, ''
    exec(compile(raw, str(path), 'exec', dont_inherit=True), result.__dict__)
    return result


g = module('graph_under_test', HERE / 'same_candidate_graph.py')
s = module('stage_under_test', HERE / 'product_runtime/stage_candidate_carrier.py')
d = module('products_under_test', HERE / 'run_full_products.py')


class GraphTests(unittest.TestCase):
    def test_plan_covers_every_required_stage_with_no_duplicate_shard(self):
        rows = g.matrix()['include']
        self.assertEqual(len(rows), 33)
        self.assertEqual(len({(row['stage'], row['index']) for row in rows}), 33)
        for stage, count in [('python-quality', 8), ('phase1', 1), ('phase2-python', 8), ('phase2-rust', 16)]:
            self.assertEqual([row['index'] for row in rows if row['stage'] == stage], list(range(count)))

    def test_final_source_pair_refuses_old_candidate_mixed_and_short_ids(self):
        for helper, candidate in [(g.F59, g.F59), ('1' * 40, '2' * 40), ('1' * 39, '1' * 39)]:
            with self.subTest(helper=helper), self.assertRaises(ValueError): g.pair(helper, candidate)

    def workflow_fixture(self):
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_SHA': '1' * 40,
               'GITHUB_WORKFLOW_SHA': '1' * 40, 'GITHUB_RUN_ID': '77', 'GITHUB_RUN_ATTEMPT': '2',
               'GITHUB_REPOSITORY': 'faberline/som'}
        record = {'id': 77, 'head_sha': '1' * 40, 'run_attempt': 2, 'event': 'workflow_dispatch',
                  'repository': {'full_name': 'faberline/som'}, 'workflow_id': 123,
                  'path': '.github/workflows/rust-hosted-readiness.yml'}
        return env, record

    def test_actual_dispatch_workflow_source_is_checked_against_live_run_before_native_qa(self):
        env, record = self.workflow_fixture()
        reader = mock.Mock(return_value=record)
        result = g.source_workflow_binding('1' * 40, env, reader)
        reader.assert_called_once_with('faberline/som', '/actions/runs/77')
        self.assertEqual(result['github_sha'], '1' * 40)

    def test_pr_merge_or_changed_actual_workflow_identity_refuses_without_changing_environment(self):
        for mutation in ('pr', 'merge-sha', 'workflow-sha', 'live-head', 'live-attempt', 'live-event', 'live-path'):
            env, record = self.workflow_fixture()
            if mutation == 'pr': env['GITHUB_EVENT_NAME'] = 'pull_request'
            if mutation == 'merge-sha': env['GITHUB_SHA'] = '2' * 40
            if mutation == 'workflow-sha': env['GITHUB_WORKFLOW_SHA'] = '2' * 40
            if mutation == 'live-head': record['head_sha'] = '2' * 40
            if mutation == 'live-attempt': record['run_attempt'] = 1
            if mutation == 'live-event': record['event'] = 'pull_request'
            if mutation == 'live-path': record['path'] = '.github/workflows/other.yml'
            original = dict(env)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                g.source_workflow_binding('1' * 40, env, lambda repository, suffix: record)
            self.assertEqual(env, original)

    def artifact_fixture(self):
        spec = {role: {'id': index + 1, 'name': role, 'digest': 'sha256:' + str(index) * 64}
                for index, role in enumerate(sorted(g.ARTIFACTS))}
        documents = {row['id']: {**row, 'expired': False,
                     'workflow_run': {'id': 11, 'head_sha': '1' * 40}} for row in spec.values()}
        return spec, documents

    def test_artifact_inputs_require_actual_origin_records_for_all_seven_outputs(self):
        spec, docs = self.artifact_fixture()
        with mock.patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}):
            report = g.validate_artifacts(spec, '1' * 40, '1' * 40, 11, 'faberline/som', lambda repo, identity: docs[identity])
        self.assertEqual(set(report['github_metadata']), g.ARTIFACTS)
        self.assertFalse(report['product_acceptance_passed'])

    def test_artifact_origin_refuses_foreign_run_source_zip_name_and_expiry(self):
        for field in ('run', 'source', 'zip', 'name', 'expired'):
            spec, docs = self.artifact_fixture()
            selected = next(iter(docs.values()))
            if field == 'run': selected['workflow_run']['id'] = 12
            if field == 'source': selected['workflow_run']['head_sha'] = '2' * 40
            if field == 'zip': selected['digest'] = 'sha256:' + 'f' * 64
            if field == 'name': selected['name'] = 'historical-saved-candidate'
            if field == 'expired': selected['expired'] = True
            with self.subTest(field=field), mock.patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}), self.assertRaises(ValueError):
                g.validate_artifacts(spec, '1' * 40, '1' * 40, 11, 'faberline/som', lambda repo, identity: docs[identity])

    def test_artifact_set_refuses_missing_role_and_reused_id(self):
        spec, docs = self.artifact_fixture()
        spec.pop('runtime')
        with self.assertRaises(ValueError): g.validate_artifacts(spec, '1' * 40, '1' * 40, 11, 'faberline/som')
        spec, docs = self.artifact_fixture()
        spec['runtime']['id'] = spec['carrier']['id']
        with self.assertRaises(ValueError): g.validate_artifacts(spec, '1' * 40, '1' * 40, 11, 'faberline/som')

    def test_missing_named_shard_cannot_pass_receipt_path_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError): g.receipt_paths(Path(temp))

    def test_all33_artifact_names_are_joined_to_their_exact_stage_index_and_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for expected in g.matrix()['include']:
                name = 'full-product-' + expected['stage'] + '-' + str(expected['index'])
                path = root / name / 'som-full-product-run/result.json'; path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'stage': expected['stage'], 'shard': {key: expected[key] for key in ('index', 'count')}}))
            self.assertEqual(len(g.receipt_paths(root)), 33)
            path = root / 'full-product-python-quality-7/som-full-product-run/result.json'
            row = json.loads(path.read_bytes()); row['shard']['count'] = 7; path.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError, 'exact shared shard role'): g.receipt_paths(root)

    def test_encrypted_stage_sets_keep_all33_shards_and_all_five_other_required_callbacks(self):
        products = g.required_stage_artifacts('products')
        self.assertEqual(len(products), 33)
        self.assertEqual(len(set(products.values())), 33)
        callbacks = g.required_stage_artifacts('callbacks')
        self.assertEqual(set(callbacks.values()), {'cnl', 'cli-refusals-rebase', 'named-contract-acceptance',
                                                'typescript-regression', 'm6-react003'})
        self.assertEqual(g.required_stage_artifacts('product-join'), {'same-candidate-product-join': 'full-product-join'})

    def stage_documents(self):
        return [{'id': index + 1, 'name': name, 'digest': 'sha256:' + '2' * 64, 'expired': False,
                 'workflow_run': {'id': 77, 'head_sha': '1' * 40}}
                for index, name in enumerate(g.required_stage_artifacts('products'))]

    def test_encrypted_stage_origin_requires_every_actual_same_run_job_output(self):
        rows = self.stage_documents()
        result = g.stage_artifact_origins('products', 'faberline/som', 77, '1' * 40,
                                         lambda repository, suffix: {'total_count': len(rows), 'artifacts': rows})
        self.assertEqual(set(result), set(g.required_stage_artifacts('products')))

    def test_encrypted_stage_origin_refuses_missing_duplicate_foreign_source_run_and_expiry(self):
        for mutation in ('missing', 'duplicate', 'source', 'run', 'expiry'):
            rows = self.stage_documents()
            if mutation == 'missing': rows.pop()
            if mutation == 'duplicate': rows.append(dict(rows[0]))
            if mutation == 'source': rows[0]['workflow_run']['head_sha'] = '3' * 40
            if mutation == 'run': rows[0]['workflow_run']['id'] = 78
            if mutation == 'expiry': rows[0]['expired'] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                g.stage_artifact_origins('products', 'faberline/som', 77, '1' * 40,
                                         lambda repository, suffix: {'total_count': len(rows), 'artifacts': rows})

    def test_semantic_verifier_receives_graph_roots_instead_of_callback_declared_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'result.json').write_bytes(b'checked raw report fixture')
            sources = mock.Mock(root=root / 'repo', pins={})
            roots = {name: root / name for name in ('assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root')}
            owning = mock.Mock(); owning.verify_result.return_value = {'verified': 'unit fixture'}
            # Finite loader protocol double; this does not admit native source.
            controls = mock.Mock(spec=['role_sources', 'load_module'])
            controls.role_sources = {'graph.semantic': 'som-rs/scripts/hosted_readiness/run_candidate_semantic_gates.py'}
            controls.load_module.return_value = owning
            with mock.patch.object(g, 'verify_compile_proof'), \
                 mock.patch.object(g, '_native_controls', return_value=controls) as acquire, \
                 mock.patch.object(g, 'raw_module', side_effect=AssertionError('legacy raw loader must not execute')):
                result = g.verify_callback(sources, root, 'cnl', '1' * 40, '1' * 40, roots)
            acquire.assert_called_once_with(sources.root, '1' * 40, '1' * 40)
            controls.load_module.assert_called_once_with('graph.semantic')
            owning.verify_result.assert_called_once_with(root, sources.read, '1' * 40, '1' * 40, 'cnl',
                                                         repo=sources.root, proof_roots=roots)
            self.assertFalse(result['product_acceptance_passed'])
            self.assertEqual(result['raw_manifest_sha256'], hashlib.sha256((root / 'result.json').read_bytes()).hexdigest())


class WorkflowClosureTests(unittest.TestCase):
    """Check the actual workflow callers and raw current callee source."""
    MAIN = '.github/workflows/rust-hosted-readiness.yml'
    PREPARATION = '.github/workflows/rust-dependency-preparation.yml'
    FORMAL = '.github/workflows/rust-same-candidate-products.yml'

    def source(self, relative):
        raw = (REPO / relative).read_bytes()
        self.assertEqual(raw, subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
            'cat-file', 'blob', HELPER + ':' + relative]))
        return raw.decode('utf-8', 'strict')

    def job(self, text, name):
        found = re.search(r'(?ms)^  ' + re.escape(name) + r':\n.*?(?=^  [A-Za-z_][A-Za-z0-9_-]*:\n|\Z)', text)
        self.assertIsNotNone(found, 'actual workflow job is required: ' + name)
        return found.group()

    def step(self, job, name):
        found = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n.*?(?=^      - (?:name:|uses:)|\Z)', job)
        self.assertIsNotNone(found, 'actual workflow step is required: ' + name)
        return found.group()

    def fields(self, text, key, indent):
        lines = text.splitlines()
        starts = [i for i, line in enumerate(lines) if line == ' ' * indent + key + ':']
        self.assertLessEqual(len(starts), 1, 'ambiguous direct YAML mapping: ' + key)
        result = {}
        if not starts:
            return result
        for line in lines[starts[0] + 1:]:
            if not line.strip():
                continue
            depth = len(line) - len(line.lstrip(' '))
            if depth <= indent:
                break
            if depth == indent + 2:
                name, separator, value = line.strip().partition(':')
                self.assertTrue(separator)
                self.assertNotIn(name, result)
                result[name] = value.strip()
        return result

    def live_api_closure(self, text):
        for name, step_name in (
                ('experimental-native-compile', 'Bind the actual formal workflow source before native QA helpers'),
                ('current-product-rust-tools', 'Verify sealed native authority before product native tool qualification')):
            job = self.job(text, name)
            permissions = self.fields(job, 'permissions', 4)
            self.assertEqual(permissions.get('contents'), 'read')
            self.assertEqual(permissions.get('actions'), 'read')
            step = self.step(job, step_name)
            self.assertIn('--entry som-rs/scripts/hosted_readiness/same_candidate_graph.py -- admit', step)
            self.assertEqual(self.fields(step, 'env', 8).get('SOM_DEPENDENCY_ACTIONS_TOKEN'), '${{ github.token }}')
            self.assertNotIn('SOM_DEPENDENCY_ACTIONS_TOKEN', self.fields(job, 'env', 4))
        self.assertNotIn('SOM_DEPENDENCY_ACTIONS_TOKEN', self.fields(text, 'env', 0))

    def test_actual_formal_live_API_callers_have_step_token_and_actions_read(self):
        text = self.source(self.MAIN)
        self.live_api_closure(text)
        for name in ('experimental-native-compile', 'current-product-rust-tools'):
            actual = self.job(text, name)
            for mutation, replacement in (
                    ('missing-token', actual.replace('          SOM_DEPENDENCY_ACTIONS_TOKEN: ${{ github.token }}\n', '')),
                    ('missing-actions-read', actual.replace('      actions: read\n', ''))):
                with self.subTest(job=name, mutation=mutation), self.assertRaises(AssertionError):
                    self.live_api_closure(text.replace(actual, replacement))

    def required_cli_flags(self, raw):
        source = ast.parse(raw)
        required = set()
        def is_required(call):
            return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == 'add_argument' and call.args
                    and any(item.arg == 'required' and isinstance(item.value, ast.Constant)
                            and item.value.value is True for item in call.keywords))
        for node in ast.walk(source):
            if is_required(node) and isinstance(node.args[0], ast.Constant):
                required.add(node.args[0].value)
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name) or not isinstance(node.iter, (ast.Tuple, ast.List)):
                continue
            for call in ast.walk(node):
                if not is_required(call):
                    continue
                option = call.args[0]
                if (isinstance(option, ast.BinOp) and isinstance(option.op, ast.Add)
                        and isinstance(option.left, ast.Constant) and option.left.value == '--'
                        and isinstance(option.right, ast.Name) and option.right.id == node.target.id):
                    self.assertTrue(all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.iter.elts))
                    required.update('--' + item.value for item in node.iter.elts)
        self.assertTrue({'--assembly-root', '--carrier-root', '--product-context', '--qa-mode', '--port'} <= required)
        return required

    def current_gate_arguments(self, text, required):
        lines = [line for line in text.splitlines() if '--entry som-rs/scripts/hosted_readiness/run_current_candidate_gate.py -- ' in line]
        self.assertEqual(len(lines), 1)
        argv = shlex.split(lines[0].split(' -- ', 1)[1])
        self.assertTrue(required <= set(argv), 'actual launcher required flags must all be passed')
        expected = {'--assembly-root': '$RUNNER_TEMP/som-current-inputs/assembly/_temp/som-unsealed-native-compile',
                    '--carrier-root': '$RUNNER_TEMP/som-current-inputs/carrier'}
        for key, value in expected.items():
            self.assertEqual(argv.count(key), 1)
            self.assertEqual(argv[argv.index(key) + 1], value)

    def test_TS_M6_actual_launcher_parser_and_trusted_proof_roots_match(self):
        text = self.source(self.FORMAL)
        required = self.required_cli_flags(self.source('som-rs/scripts/hosted_readiness/run_current_candidate_gate.py'))
        self.current_gate_arguments(text, required)
        for field, value in (
                ('--assembly-root', '$RUNNER_TEMP/som-current-inputs/assembly/_temp/som-unsealed-native-compile'),
                ('--carrier-root', '$RUNNER_TEMP/som-current-inputs/carrier')):
            changed = text.replace(field + ' "' + value + '" --product-context', '--product-context')
            if changed == text:
                line = next(line for line in text.splitlines() if '--entry som-rs/scripts/hosted_readiness/run_current_candidate_gate.py -- ' in line)
                changed = text.replace(line, line.replace(field + ' "' + value + '" ', ''))
            with self.subTest(flag=field), self.assertRaises(AssertionError):
                self.current_gate_arguments(changed, required)

    def preparation_helper_closure(self, text):
        job = self.job(text, 'full-product-preparation-join')
        revision = self.fields(job, 'env', 4).get('HELPER_REVISION')
        self.assertEqual(revision, '${{ inputs.helper_revision || github.event.pull_request.head.sha || github.sha }}')
        self.assertIn('          ref: ' + revision, job)
        self.assertIn('--revision "$HELPER_REVISION" --entry som-rs/scripts/hosted_readiness/run_full_products.py --', job)
        self.assertNotRegex(job, r'(?m)^\s*GITHUB_SHA:')

    def test_preparation_join_uses_its_actual_checked_helper_revision(self):
        text = self.source(self.PREPARATION)
        self.preparation_helper_closure(text)
        actual = self.job(text, 'full-product-preparation-join')
        changed = actual.replace('      HELPER_REVISION: ${{ inputs.helper_revision || github.event.pull_request.head.sha || github.sha }}\n', '')
        with self.assertRaises(AssertionError):
            self.preparation_helper_closure(text.replace(actual, changed))

    def main_fixture_closure(self, text):
        job = self.job(text, 'fixture-tests')
        self.assertNotIn('unittest discover', job)
        step = self.step(job, 'Run every source fixture file in a fresh checked Python process')
        argv_line = next(line for line in step.splitlines() if '--entry som-rs/scripts/hosted_readiness/run_source_fixtures.py -- ' in line)
        self.assertIn('python3 -I -S -B ', argv_line)
        argv = shlex.split(argv_line.split(' -- ', 1)[1])
        self.assertEqual(argv[:6], ['--repo', '$GITHUB_WORKSPACE', '--helper-revision', '$HELPER_REVISION', '--out', '$RUNNER_TEMP/som-main-source-fixtures'])
        current = self.step(job, 'Keep all 34 current input source fixtures required')
        self.assertIn('        if: always()', current)
        self.assertIn('--entry som-rs/scripts/hosted_readiness/product_runtime/test_current_input_context.py -- -v', current)
        saved = self.step(job, 'Save fixture test logs')
        self.assertIn('        if: always()', saved)
        for path in ('unit-tests.log', 'current-context-fixtures.log', '${{ runner.temp }}/som-main-source-fixtures'):
            self.assertIn(path, saved)

    def test_main_fixture_calls_complete_isolated_runner_and_keeps_current34_and_all_logs(self):
        text = self.source(self.MAIN)
        self.main_fixture_closure(text)
        for marker in ('--entry som-rs/scripts/hosted_readiness/run_source_fixtures.py -- ',
                       '--entry som-rs/scripts/hosted_readiness/product_runtime/test_current_input_context.py -- -v',
                       '${{ runner.temp }}/som-main-source-fixtures'):
            with self.subTest(missing=marker), self.assertRaises((AssertionError, StopIteration)):
                self.main_fixture_closure(text.replace(marker, 'MISSING_REQUIRED_SOURCE_FIXTURE'))


class StagingTests(unittest.TestCase):
    def test_hardlinked_current_binary_copies_to_single_link_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            binary, rustfmt = root / 'binary', root / 'rustfmt'
            binary.write_bytes(b'synthetic compiler; never executed')
            rustfmt.write_bytes(b'synthetic formatter; never executed')
            for path in (binary, rustfmt): path.chmod(0o755)
            os.link(binary, root / 'cargo-alias')
            identity = lambda p: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': p.stat().st_size,
                                  'mode': stat.S_IMODE(p.stat().st_mode)}
            values = {'binary': identity(binary), 'tools': {'SOM_RUSTFMT': identity(rustfmt)}}
            proof = {'tested_sha': '1' * 40, 'experiment_complete': True, 'passed': 2041,
                     'identity_stability_passed': True, 'initial_identities': values, 'final_identities': values,
                     'cleanup': {'cleanup_passed': True}, 'product_acceptance_passed': False}
            path = root / 'proof.json'; path.write_text(json.dumps(proof))
            with mock.patch.dict(os.environ, {'RUNNER_TEMP': str(root)}):
                record = s.stage(binary, rustfmt, path, '1' * 40, root / 'staged')
            self.assertEqual(binary.stat().st_nlink, 2)
            self.assertEqual((root / 'staged/binary').stat().st_nlink, 1)
            self.assertEqual(record['files']['binary']['source_nlink'], 2)
            self.assertEqual((root / 'staged/binary').read_bytes(), binary.read_bytes())
            self.assertFalse(record['product_acceptance_passed'])


class EntryAdmissionTests(unittest.TestCase):
    def setup_context(self, root, allowed):
        work = root / 'work'; work.mkdir()
        context = root / 'context.json'
        data = {'repo': str(root), 'helper_revision': '1' * 40, 'candidate_revision': '1' * 40,
                'work_root': str(work), 'stage': 'phase1', 'shard': {'index': 0, 'count': 1}, 'qa_mode': 'native-qa-v1'}
        context.write_text(json.dumps(data))
        authority = {'source_authority_active': True, 'admitted_repository_python_sources': allowed}
        sources = mock.Mock(root=root, pins={})
        ctx = mock.Mock(sources=sources, qa_source_authority=authority)
        mod = mock.Mock(Context=mock.Mock(return_value=ctx), __som_native_source_authority__=authority)
        mod.relative = lambda name: name
        return work, context, sources, ctx, mod

    def test_unadmitted_entry_sentinel_is_never_read_or_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            work, context, sources, ctx, mod = self.setup_context(root, ['allowed.py'])
            sources.read.return_value = b'raise AssertionError("entry sentinel executed")'
            with mock.patch.dict(os.environ, {'RUNNER_TEMP': str(root), 'SOM_FULL_PRODUCT_CONTEXT': str(context)}), \
                 mock.patch.object(d, 'load_context_module', return_value=mod), self.assertRaisesRegex(ValueError, 'entry itself'):
                d.execute_tool(['sentinel.py'])
            self.assertNotIn(mock.call('sentinel.py'), sources.read.call_args_list)
            ctx.verify.assert_called_once()
            report = json.loads(next((work / 'checker-postchecks').iterdir()).read_bytes())
            self.assertFalse(report['entry_executed'])

    def test_entry_without_context_import_still_has_prior_context_and_postchecks(self):
        protocol = module('graph_full_product_protocol_fixture', HERE / 'test_run_full_products.py')
        fixture = protocol.native_protocol_fixture(self)
        self.assertEqual(fixture.run_entry(), 0)
        context = fixture.domain.records['runtime.context'].module
        self.assertEqual(len(context.CONSTRUCTIONS), 1)
        self.assertEqual(len(context.VERIFICATIONS), 1)
        self.assertIs(context.CONSTRUCTIONS[0], context.VERIFICATIONS[0])
        self.assertTrue((fixture.work / 'entry-observed-context').is_file())
        # The checker payload has no full_product_context import. The actual
        # driver/actual Core must construct and recheck the protocol Context.
        self.assertNotIn(b'import full_product_context', fixture.raw[fixture.target])
        report = json.loads(next((fixture.work / 'checker-postchecks').iterdir()).read_bytes())
        self.assertTrue(report['entry_executed'])
        self.assertTrue(report['source_runtime_tools_inputs_unchanged'])
        self.assertFalse(report['product_acceptance_passed'])

    def test_context_admission_failure_keeps_first_error_and_writes_failure_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            work, context, sources, ctx, mod = self.setup_context(root, ['allowed.py'])
            mod.Context.side_effect = ValueError('first sealed admission refusal')
            with mock.patch.dict(os.environ, {'RUNNER_TEMP': str(root), 'SOM_FULL_PRODUCT_CONTEXT': str(context)}), \
                 mock.patch.object(d, 'load_context_module', return_value=mod), self.assertRaisesRegex(ValueError, 'first sealed'):
                d.execute_tool(['allowed.py'])
            sources.read.assert_not_called()
            report = json.loads(next((work / 'checker-postchecks').iterdir()).read_bytes())
            self.assertFalse(report['entry_executed'])
            self.assertFalse(report['source_runtime_tools_inputs_unchanged'])


if __name__ == '__main__': unittest.main(verbosity=2)
