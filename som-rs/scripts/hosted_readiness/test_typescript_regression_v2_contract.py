"""GitHub-only V2 fixtures. These definitions do not grant a gate pass."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest

HERE = Path(__file__).absolute().parent


def load(relative):
    path = HERE / relative
    module = ModuleType('_som_ts_v2_contract_' + path.stem)
    module.__file__, module.__package__, module.__cached__ = str(path), '', None
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


class VerificationContractTests(unittest.TestCase):
    def setUp(self):
        self.owner = load('typescript_regression_owner.py')
        self.domain = load('typescript_regression_domain.py')
        self.revision = 'a' * 40
        self.proofs = {key: '/unread/' + key for key in self.owner.PROOF_ROLES}

    def test_missing_evidence_root_refuses_before_capture_or_source_read(self):
        calls = []
        with self.assertRaisesRegex(ValueError, 'explicit fresh verification evidence'):
            self.owner.verify_result('/unread/capture', lambda path: calls.append(path),
                self.revision, self.revision, self.owner.GATE, repo='/unread/repo', proof_roots=self.proofs)
        self.assertEqual(calls, [])

    def test_unadmitted_archive_view_refuses_before_capture_or_source_read(self):
        calls = []
        with self.assertRaisesRegex(ValueError, 'reviewed archive-view API'):
            self.owner.verify_result('/unread/capture', lambda path: calls.append(path),
                self.revision, self.revision, self.owner.GATE, repo='/unread/repo', proof_roots=self.proofs,
                verification_evidence_root='/unread/evidence', archive_view={'root': '/unread/capture'})
        self.assertEqual(calls, [])

    def profile(self, document):
        return self.owner.baseline_role(lambda path: json.dumps(document).encode(), 'profile.json')

    def test_recorded_before_is_explicitly_supplemental(self):
        result = self.profile({'version': 'som.linux-qa-recorded-before.v1',
            'role': 'recorded-same-request-before', 'source_compiler': {
                'binary_sha256': self.owner.LATEST_BINARY, 'source_revision': self.owner.LATEST_SOURCE}})
        self.assertEqual(result, 'recorded-before-supplemental')

    def test_recorded_before_cannot_use_current_or_f59_source_role(self):
        with self.assertRaisesRegex(ValueError, 'frozen live2bce authority'):
            self.profile({'version': 'som.linux-qa-recorded-before.v1',
                'role': 'recorded-same-request-before', 'source_compiler': {
                    'binary_sha256': self.owner.LATEST_BINARY, 'source_revision': self.owner.F59}})

    def test_live_before_requires_the_exact_frozen_identity(self):
        self.assertEqual(self.profile({'version': 'som.linux-qa-immutable-baseline.v1',
            'role': 'historical-immutable-baseline', 'source_revision': self.owner.LATEST_SOURCE,
            'binary': {'sha256': self.owner.LATEST_BINARY}}), 'live-before-profile')

    def test_other_live_binary_is_not_a_before_substitute(self):
        with self.assertRaisesRegex(ValueError, 'exact frozen live2bce'):
            self.profile({'version': 'som.linux-qa-immutable-baseline.v1',
                'role': 'historical-immutable-baseline', 'source_revision': self.owner.LATEST_SOURCE,
                'binary': {'sha256': 'e' * 64}})

    def test_only_exact_two_native_python_hash_checks_can_be_replaced(self):
        row = {'id': 'ts.python_unchanged.0'}
        original = SimpleNamespace(unrun=[], post=[], failed=[
            '1342 rows with compile qa_response_sha256 != baseline_response_sha256',
            '[ts.python_unchanged.0] 1342 rows with assemble qa_request_sha256 != baseline_request_sha256'])
        self.assertTrue(self.domain.discharged(row, original, {row['id']: {'actual_raw_replay': True}}))

    def test_unknown_failed_assertion_stays_failed(self):
        row = {'id': 'ts.python_unchanged.0'}
        original = SimpleNamespace(unrun=[], post=[], failed=['1342 rows with product files differ'])
        self.assertFalse(self.domain.discharged(row, original, {row['id']: {'actual_raw_replay': True}}))

    def test_other_row_cannot_clear_a_failed_assertion(self):
        row = {'id': 'ts.product_gates.0'}
        original = SimpleNamespace(unrun=[], post=[], failed=[
            '1342 rows with compile qa_response_sha256 != baseline_response_sha256'])
        self.assertFalse(self.domain.discharged(row, original, {row['id']: {'actual_raw_replay': True}}))

    def test_unrun_or_postcondition_failure_cannot_be_replaced(self):
        row = {'id': 'ts.python_unchanged.0'}
        for key in ('unrun', 'post'):
            original = SimpleNamespace(unrun=[], post=[], failed=[])
            setattr(original, key, ['required proof missing'])
            self.assertFalse(self.domain.discharged(row, original, {row['id']: {'actual_raw_replay': True}}))


class InputBindingLifecycleTests(unittest.TestCase):
    def fixture(self):
        adapter = load('typescript_regression_inputs.py')
        phase2 = SimpleNamespace(PUBLIC='old-public', LEGACY='old-legacy', RS='old-rs',
            cases=lambda: [], rust_files=lambda: [])
        original = dict(vars(phase2))
        module = SimpleNamespace(phase2=phase2, _lookup_prompt_by_id=lambda value: None,
                                 _ORIG_PHASE2_CASES=phase2.cases)
        ctx = SimpleNamespace(qa_mode='native-qa-v1', repo=Path('/unread/repo'), cache={},
            qa_source_authority={'admitted_repository_python_sources': [adapter.SELF,
                'som-rs/behavior/phase2/check_phase2.py']},
            data={'typescript_regression_inputs': {'roots': {'public_run': '/input/public',
                'legacy_run': '/input/legacy'}, 'siblings': {'rs_prompts': '/input/rs'}}},
            sources=SimpleNamespace(read=lambda path: b'def rust_files(*args):\n    return []\n'))
        return adapter, ctx, module, original

    def test_owned_input_binding_cleanup_restores_original_objects(self):
        adapter, ctx, module, original = self.fixture()
        adapter.configure(ctx, module)
        self.assertEqual(module.phase2.PUBLIC, Path('/input/public'))
        adapter.close(ctx)
        for key, value in original.items():
            self.assertIs(getattr(module.phase2, key), value)
        self.assertEqual(ctx.cache['typescript_regression_input_binding_closers']
                         if 'typescript_regression_input_binding_closers' in ctx.cache else [], [])

    def test_input_cleanup_preserves_a_new_owner_and_refuses(self):
        adapter, ctx, module, original = self.fixture()
        adapter.configure(ctx, module)
        replacement = lambda: ['new owner']
        module.phase2.cases = replacement
        with self.assertRaisesRegex(ValueError, 'input binding cleanup failed'):
            adapter.close(ctx)
        self.assertIs(module.phase2.cases, replacement)
        for key in ('PUBLIC', 'LEGACY', 'RS', 'rust_files'):
            self.assertIs(getattr(module.phase2, key), original[key])


if __name__ == '__main__':
    unittest.main()
