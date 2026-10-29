"""GitHub-only caller protocol fixtures. No fixture grants a product pass."""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub fixtures only')
HERE = Path(__file__).absolute().parent


def load(name):
    path = HERE / (name + '.py')
    module = ModuleType('_som_owner_evidence_fixture_' + name)
    module.__file__, module.__package__, module.__cached__ = str(path), '', None
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


class EvidenceRootTests(unittest.TestCase):
    def setUp(self):
        self.common = load('run_current_candidate_gate')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.env = mock.patch.dict(os.environ, {'RUNNER_TEMP': str(self.base)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.capture = self.base / 'raw/gate'; (self.capture / 'owner').mkdir(parents=True)
        (self.capture / 'owner/result.json').write_bytes(b'{"immutable":"capture"}\n')
        (self.capture / 'result.json').write_bytes(b'{"immutable":"shared"}\n')
        self.repo = self.directory('repo')
        self.proofs = {name: self.directory('proofs/' + name) for name in self.common.ROLES}
        inputs = {name: str(self.directory('inputs/' + name)) for name in
                  ('public_run', 'legacy_run', 'rust_run', 'npm_cache', 'node_tools')}
        self.data = {'typescript_regression_inputs': {'roots': inputs,
            'siblings': {'rs_prompts': str(self.directory('inputs/rs_prompts'))}}}
        self.data.update({name: str(self.directory('runtime/' + name)) for name in
                          ('runtime_root', 'wheel_root', 'rust_tools_root', 'cargo_root')})
        consumer = self.directory('input-transport/som-typescript-input-consumer')
        receipt = consumer / 'receipt.json'; receipt.write_bytes(b'{"fixture":"consumer-receipt"}\n')
        self.data['typescript_regression_inputs'].update(receipt=str(receipt), artifact={
            'id': 1, 'name': 'same-candidate-typescript-regression-inputs',
            'digest': 'sha256:' + 'f' * 64, 'receipt_sha256': self.common.sha(receipt.read_bytes())})
        self.evidence = self.base / 'raw/verification-evidence/same-job'

    def directory(self, relative):
        result = self.base / relative; result.mkdir(parents=True); return result

    def keyword(self):
        return {'capture_root': self.capture, 'repo': self.repo, 'proof_roots': self.proofs, 'data': self.data}

    def test_check_is_read_only_and_requires_a_fresh_explicit_root(self):
        with self.assertRaisesRegex(ValueError, 'explicit fresh'):
            self.common.verification_evidence_root_checked(None, **self.keyword())
        self.assertEqual(self.common.verification_evidence_root_checked(self.evidence, **self.keyword()), self.evidence)
        self.assertFalse(self.evidence.parent.exists())

    def test_prepare_creates_only_owned_empty_role_directories_and_keeps_capture_bytes(self):
        before = self.common.tree(self.capture)
        result = self.common.prepare_verification_evidence_root(self.evidence, **self.keyword())
        self.assertEqual(result, self.evidence); self.assertEqual(list(result.iterdir()), [])
        self.assertEqual(result.stat().st_mode & 0o777, 0o700)
        self.assertEqual(result.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.common.tree(self.capture), before)

    def test_a_sibling_inside_whole_gate_is_rejected_before_any_mkdir(self):
        bad = self.capture / 'outside-owner-but-inside-gate/new-evidence'
        with mock.patch.object(self.common.os, 'mkdir', side_effect=AssertionError('must not create')):
            with self.assertRaisesRegex(ValueError, 'overlaps whole gate'):
                self.common.prepare_verification_evidence_root(bad, **self.keyword())
        self.assertFalse(bad.parent.exists())

    def test_capture_ancestor_is_not_an_evidence_root(self):
        with self.assertRaisesRegex(ValueError, 'overlaps whole gate'):
            self.common.verification_evidence_root_checked(self.capture.parent, **self.keyword())

    def test_every_trusted_proof_input_runtime_and_repository_root_is_protected(self):
        protected = [self.repo, *self.proofs.values(),
            *[Path(path) for path in self.data['typescript_regression_inputs']['roots'].values()],
            *[Path(path) for path in self.data['typescript_regression_inputs']['siblings'].values()],
            *[Path(self.data[name]) for name in ('runtime_root', 'wheel_root', 'rust_tools_root', 'cargo_root')]]
        for path in protected:
            with self.subTest(path=path):
                bad = path / 'new-verification'
                with self.assertRaisesRegex(ValueError, 'overlaps whole gate'):
                    self.common.prepare_verification_evidence_root(bad, **self.keyword())
                self.assertFalse(bad.exists())

    def test_all_baseline_roots_and_their_ancestors_refuse_before_any_mkdir(self):
        roots = [self.directory('baseline-inputs/one'), self.directory('baseline-inputs/two')]
        self.data['baseline_inputs'] = {'source-profile-one': str(roots[0]), 'source-profile-two': str(roots[1])}
        for bad in [*(path / 'new-evidence' for path in roots), roots[0].parent]:
            with self.subTest(path=bad), mock.patch.object(self.common.os, 'mkdir',
                    side_effect=AssertionError('must not create before baseline guard')):
                with self.assertRaisesRegex(ValueError, 'overlaps whole gate'):
                    self.common.prepare_verification_evidence_root(bad, **self.keyword())
            if bad.name == 'new-evidence':
                self.assertFalse(bad.exists())

    def test_known_build_provenance_and_current_companion_roots_refuse_before_mkdir(self):
        for field in ('build_provenance_root', 'current_companions_root'):
            path = self.directory('known-inputs/' + field)
            self.data[field] = str(path)
        for field in ('build_provenance_root', 'current_companions_root'):
            root = Path(self.data[field])
            for bad in (root / 'new-evidence', root.parent):
                with self.subTest(field=field, path=bad), mock.patch.object(self.common.os, 'mkdir',
                        side_effect=AssertionError('must not create before all known input guards')):
                    with self.assertRaisesRegex(ValueError, 'overlaps whole gate'):
                        self.common.prepare_verification_evidence_root(bad, **self.keyword())
                if bad.name == 'new-evidence':
                    self.assertFalse(bad.exists())

    def test_existing_nonempty_evidence_is_never_reused(self):
        self.evidence.mkdir(parents=True); (self.evidence / 'first-failure.raw').write_bytes(b'first')
        with self.assertRaisesRegex(ValueError, 'fresh and empty'):
            self.common.prepare_verification_evidence_root(self.evidence, **self.keyword())
        self.assertEqual((self.evidence / 'first-failure.raw').read_bytes(), b'first')

    def test_existing_owned_empty_root_is_valid(self):
        self.evidence.mkdir(parents=True, mode=0o700)
        self.assertEqual(self.common.prepare_verification_evidence_root(self.evidence, **self.keyword()), self.evidence)

    def test_a_symlinked_parent_is_refused_without_writing_its_target(self):
        outside = self.directory('outside'); parent = self.capture.parent / 'verification-evidence'
        parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'physical verification'):
            self.common.prepare_verification_evidence_root(self.evidence, **self.keyword())
        self.assertEqual(list(outside.iterdir()), [])

    def test_group_writable_existing_parent_refuses_before_child_creation(self):
        self.evidence.parent.mkdir(); self.evidence.parent.chmod(0o770)
        try:
            with self.assertRaisesRegex(ValueError, 'owned physical'):
                self.common.prepare_verification_evidence_root(self.evidence, **self.keyword())
            self.assertFalse(self.evidence.exists())
        finally:
            self.evidence.parent.chmod(0o700)

    def test_outside_runner_temp_is_not_an_evidence_role(self):
        with self.assertRaisesRegex(ValueError, 'below RUNNER_TEMP'):
            self.common.prepare_verification_evidence_root(self.base.parent / 'unowned-evidence', **self.keyword())

    def owner(self, *, legacy=False):
        calls = []
        def run_gate(ctx, candidate_dir, services, out, gate):
            raise AssertionError('collector must not run during a verifier fixture')
        if legacy:
            def verify_result(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots):
                calls.append({'repo': repo, 'proof_roots': proof_roots})
                return {'fixture_protocol_only': True}
        else:
            def verify_result(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots,
                              verification_evidence_root=None, archive_view=None):
                calls.append({'repo': repo, 'proof_roots': proof_roots,
                    'verification_evidence_root': verification_evidence_root, 'archive_view': archive_view})
                (verification_evidence_root / 'failure.stdout.raw').write_bytes(b'complete audit output')
                return {'fixture_protocol_only': True}
        return SimpleNamespace(run_gate=run_gate, verify_result=verify_result), calls

    def invoke(self, owner, gate='typescript-regression', **extra):
        return self.common.verify_owner_domain(owner, self.capture / 'owner', lambda path: b'',
            'a' * 40, 'a' * 40, gate, repo=self.repo, proof_roots=self.proofs, data=self.data, **extra)

    def test_ts_gets_exact_new_keywords_and_retains_external_evidence(self):
        owner, calls = self.owner(); before = self.common.tree(self.capture)
        self.invoke(owner, verification_evidence_root=self.evidence)
        self.assertEqual(calls, [{'repo': self.repo, 'proof_roots': self.proofs,
            'verification_evidence_root': self.evidence, 'archive_view': None}])
        self.assertEqual((self.evidence / 'failure.stdout.raw').read_bytes(), b'complete audit output')
        self.assertEqual(self.common.tree(self.capture), before)

    def test_m6_keeps_old_keyword_api_and_creates_no_evidence_directory(self):
        owner, calls = self.owner(legacy=True)
        self.invoke(owner, gate='m6-react003', verification_evidence_root=self.evidence)
        self.assertEqual(calls, [{'repo': self.repo, 'proof_roots': self.proofs}])
        self.assertFalse(self.evidence.parent.exists())

    def test_ts_refuses_old_owner_api_before_creating_directories(self):
        owner, calls = self.owner(legacy=True)
        with self.assertRaisesRegex(ValueError, 'exact optional evidence/archive'):
            self.invoke(owner, verification_evidence_root=self.evidence)
        self.assertEqual(calls, []); self.assertFalse(self.evidence.parent.exists())

    def test_any_non_none_archive_view_refuses_before_owner_or_directory_work(self):
        owner, calls = self.owner()
        with self.assertRaisesRegex(ValueError, 'reviewed archive-view API'):
            self.invoke(owner, verification_evidence_root=self.evidence, archive_view={})
        self.assertEqual(calls, []); self.assertFalse(self.evidence.parent.exists())
        with self.assertRaisesRegex(ValueError, 'reviewed archive-view API'):
            self.common.verify_result('/unread', lambda path: self.fail('must not read'),
                'a' * 40, 'a' * 40, 'm6-react003', repo='/unread', proof_roots={}, archive_view='view')

    def test_owner_failure_keeps_first_error_and_raw_evidence_outside_capture(self):
        owner, calls = self.owner(); first = RuntimeError('first owner replay failure')
        original = owner.verify_result
        def verify_result(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots,
                          verification_evidence_root=None, archive_view=None):
            original(root, source_reader, helper_revision, candidate_revision, gate, repo=repo, proof_roots=proof_roots,
                     verification_evidence_root=verification_evidence_root, archive_view=archive_view)
            raise first
        owner.verify_result = verify_result; before = self.common.tree(self.capture)
        with self.assertRaises(RuntimeError) as caught:
            self.invoke(owner, verification_evidence_root=self.evidence)
        self.assertIs(caught.exception, first)
        self.assertEqual((self.evidence / 'failure.stdout.raw').read_bytes(), b'complete audit output')
        self.assertEqual(self.common.tree(self.capture), before)


class GraphEvidenceForwardingTests(unittest.TestCase):
    def setUp(self):
        self.graph = load('same_candidate_graph')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'gate'; self.root.mkdir(); (self.root / 'result.json').write_bytes(b'{}')
        self.sources = SimpleNamespace(root=self.base / 'repo', read=lambda path: b'', pins={}, verify=lambda: None)
        self.proofs = {name: self.base / name for name in
                       ('assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root')}
        self.evidence = self.base / 'verification-evidence/graph-postclose'

    def test_graph_forwards_exact_ts_only_keywords(self):
        calls = []
        def verify_result(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots,
                          verification_evidence_root=None, archive_view=None):
            calls.append({'root': root, 'repo': repo, 'proof_roots': proof_roots,
                'verification_evidence_root': verification_evidence_root, 'archive_view': archive_view})
            return {'fixture_protocol_only': True}
        # Finite fixed-role double; the original keyword judge is unchanged.
        controls = SimpleNamespace(
            role_sources={'controller.current-gate': 'som-rs/scripts/hosted_readiness/run_current_candidate_gate.py'},
            load_module=mock.Mock(return_value=SimpleNamespace(verify_result=verify_result)))
        with mock.patch.object(self.graph, 'verify_compile_proof', return_value={}), \
             mock.patch.object(self.graph, '_native_controls', return_value=controls) as acquire, \
             mock.patch.object(self.graph, 'raw_module', side_effect=AssertionError('legacy raw loader must not execute')):
            self.graph.verify_callback(self.sources, self.root, 'typescript-regression', 'a'*40, 'a'*40,
                self.proofs, verification_evidence_root=self.evidence)
        acquire.assert_called_once_with(self.sources.root, 'a'*40, 'a'*40)
        controls.load_module.assert_called_once_with('controller.current-gate')
        self.assertEqual(calls, [{'root': self.root, 'repo': self.sources.root, 'proof_roots': self.proofs,
            'verification_evidence_root': self.evidence, 'archive_view': None}])

    def test_graph_m6_keeps_old_common_call_keywords(self):
        calls = []
        def verify_result(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots):
            calls.append({'repo': repo, 'proof_roots': proof_roots}); return {'fixture_protocol_only': True}
        # This strict callee still accepts only the original M6 keywords.
        controls = SimpleNamespace(
            role_sources={'controller.current-gate': 'som-rs/scripts/hosted_readiness/run_current_candidate_gate.py'},
            load_module=mock.Mock(return_value=SimpleNamespace(verify_result=verify_result)))
        with mock.patch.object(self.graph, 'verify_compile_proof', return_value={}), \
             mock.patch.object(self.graph, '_native_controls', return_value=controls) as acquire, \
             mock.patch.object(self.graph, 'raw_module', side_effect=AssertionError('legacy raw loader must not execute')):
            self.graph.verify_callback(self.sources, self.root, 'm6-react003', 'a'*40, 'a'*40, self.proofs,
                verification_evidence_root=self.evidence)
        acquire.assert_called_once_with(self.sources.root, 'a'*40, 'a'*40)
        controls.load_module.assert_called_once_with('controller.current-gate')
        self.assertEqual(calls, [{'repo': self.sources.root, 'proof_roots': self.proofs}])

    def test_missing_ts_evidence_and_any_archive_view_refuse_before_proof_loading(self):
        with mock.patch.object(self.graph, 'verify_compile_proof', side_effect=AssertionError('must not load')):
            with self.assertRaisesRegex(ValueError, 'explicit TypeScript verification'):
                self.graph.verify_callback(self.sources, self.root, 'typescript-regression', 'a'*40, 'a'*40, self.proofs)
            with self.assertRaisesRegex(ValueError, 'reviewed archive-view API'):
                self.graph.verify_callback(self.sources, self.root, 'm6-react003', 'a'*40, 'a'*40,
                    self.proofs, archive_view='view')

    def test_final_join_selects_a_fresh_ts_stage_child_and_keeps_other_apis(self):
        expected = self.graph.required_stage_artifacts('callbacks')
        callbacks = self.base / 'callbacks'; callbacks.mkdir()
        for name in expected: (callbacks / name).mkdir()
        joined = {'version': 'som.full-product-preparation-join.v1', 'identities': {
            'helper_revision': 'a'*40, 'candidate_revision': 'a'*40, 'qa_mode': 'native-qa-v1'},
            'whole_named_product_cohorts_joined': True, 'all_preparation_gates_passed': True,
            'product_acceptance_passed': False, 'whole_acceptance_complete': False,
            'cohorts': {stage: {'total': count, 'run': count, 'passed': count, 'failed': []}
                        for stage, unused, count in self.graph.STAGES}}
        product = self.base / 'product-join.json'; product.write_text(json.dumps(joined))
        calls = []
        def callback(sources, root, stage, helper, candidate, proof_roots, **keyword):
            calls.append((stage, keyword)); return {'fixture_protocol_only': stage}
        evidence = self.base / 'whole-graph-raw/verification-evidence'
        with mock.patch.object(self.graph, 'verify_callback', side_effect=callback):
            self.graph.join_graph(self.sources, callbacks, self.base / 'current', product,
                'a'*40, 'a'*40, self.proofs, verification_evidence_root=evidence)
        self.assertEqual(dict(calls)['typescript-regression'], {
            'verification_evidence_root': evidence / 'typescript-regression', 'archive_view': None})
        self.assertTrue(all(not keyword for stage, keyword in calls if stage != 'typescript-regression'))
        self.assertFalse(evidence.exists())

    def test_common_and_graph_cli_forward_the_explicit_keyword_arguments(self):
        common = ast.parse((HERE / 'run_current_candidate_gate.py').read_bytes())
        graph = ast.parse((HERE / 'same_candidate_graph.py').read_bytes())
        cm = next(node for node in common.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        gm = next(node for node in graph.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        calls = [node for node in ast.walk(cm) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'verify_owner_domain']
        self.assertEqual(len(calls), 1)
        self.assertTrue({'verification_evidence_root','archive_view'} <= {key.arg for key in calls[0].keywords})
        for name in ('verify_callback', 'join_graph'):
            calls = [node for node in ast.walk(gm) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == name]
            self.assertEqual(len(calls), 1)
            self.assertTrue({'verification_evidence_root','archive_view'} <= {key.arg for key in calls[0].keywords})

    def test_workflow_evidence_roots_stay_inside_always_sealed_outer_raw_roots(self):
        repo = HERE.parents[2]
        raw = (repo / '.github/workflows/rust-same-candidate-products.yml').read_text()
        self.assertIn('--verification-evidence-root "$RUNNER_TEMP/som-callback-raw/verification-evidence/same-job"', raw)
        self.assertIn('--verification-evidence-root "$RUNNER_TEMP/som-callback-raw/verification-evidence/graph-postclose"', raw)
        self.assertIn('--verification-evidence-root "$RUNNER_TEMP/som-whole-graph-raw/verification-evidence"', raw)
        self.assertIn('--raw-root "$RUNNER_TEMP/som-callback-raw"', raw)
        self.assertIn('--raw-root "$RUNNER_TEMP/som-whole-graph-raw"', raw)
        for role in ('som-callback-raw', 'som-whole-graph-raw'):
            position = raw.index('--raw-root "$RUNNER_TEMP/' + role + '"')
            self.assertIn('if: always()', raw[max(0, position-1200):position])


if __name__ == '__main__':
    unittest.main()
