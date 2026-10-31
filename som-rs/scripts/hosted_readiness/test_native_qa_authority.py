"""Synthetic exact native QA authority fixtures. Run only on GitHub."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise RuntimeError('GitHub Linux fixtures only')
REPO = Path(__file__).resolve().parents[3]
SUBJECT = 'som-rs/scripts/hosted_readiness/native_qa_authority.py'
revision = subprocess.check_output(['/usr/bin/git', '-C', str(REPO), 'rev-parse', 'HEAD']).decode().strip()
raw = (REPO / SUBJECT).read_bytes()
if raw != subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO), 'cat-file', 'blob', revision + ':' + SUBJECT]):
    raise RuntimeError('fixture subject differs from raw Git source')
c = ModuleType('_native_qa_authority_fixture_subject')
c.__file__, c.__package__, c.__cached__ = str(REPO / SUBJECT), '', None
exec(compile(raw, c.__file__, 'exec', dont_inherit=True), c.__dict__)


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


class NativeQaSourceTests(unittest.TestCase):
    def fixture(self, activate=True):
        values, receipts, policies = {}, {}, {}
        paths = set(subprocess.check_output(['/usr/bin/git', '-C', str(REPO), 'ls-tree', '-r', '--name-only', revision]).decode().splitlines())
        for target, (number, prefix, leaf, _) in c.AUTHORITY.items():
            policy_path = 'som-rs/contracts/' + leaf
            policy = json.loads((REPO / policy_path).read_bytes())
            if activate:
                policy['qa_authority_state'], policy['sealing_dependencies'] = 'SEALED_AUTHORITY', []
            policies[target] = policy
            previous_path = f'som-rs/evidence/{prefix}-contract-amendment-{number-1:03d}.json'
            previous_raw = (REPO / previous_path).read_bytes()
            previous = json.loads(previous_raw)
            checker = f'som-rs/tests/{target}-output-v1/check_contract.py'
            files = dict(previous['files'])
            changes = []
            if target != 'compiler':
                archive = f'som-rs/evidence/{target}-output-contract-archive/check_contract-before-amendment-{number:03d}.py'
                self.assertEqual(c.sha((REPO / archive).read_bytes()), files[checker])
                files[checker] = c.sha((REPO / checker).read_bytes())
                changes.append({'path': checker, 'old_sha256': previous['files'][checker], 'new_sha256': files[checker],
                                'archived_copy': archive, 'archived_copy_sha256': previous['files'][checker]})
            receipt = {'version': 'som.compiler-contract-amendment.v1' if target == 'compiler' else f'som.{target}-output-contract-amendment.v1',
                       'amendment': number, 'status': 'amendment_sealed_after_independent_review',
                       'previous_receipt': previous_path, 'previous_receipt_sha256': c.sha(previous_raw),
                       'implementation_already_in_progress': True, 'implementation_order': copy.deepcopy(c.ORDER),
                       'files': files, 'changed_paths': [] if target == 'compiler' else [checker],
                       'added_paths': [policy_path], 'changes': changes,
                       'independent_review': {'final_verdict': 'APPROVED', 'scope': c.REVIEW_SCOPE},
                       'acceptance': {'status': 'not_run', 'product_acceptance_passed': False}}
            if target != 'compiler':
                receipt['target_contract'] = {'previous_sha256': previous['target_contract']['sha256']}
            receipts[target] = receipt
            paths.add(f'som-rs/evidence/{prefix}-contract-amendment-{number:03d}.json')
        self.rebind(values, receipts, policies)
        return values, receipts, policies, paths

    def rebind(self, values, receipts, policies, fresh_review=True):
        for target, (number, prefix, leaf, _) in c.AUTHORITY.items():
            policy_path = 'som-rs/contracts/' + leaf
            values[policy_path] = encoded(policies[target])
            receipt = receipts[target]
            receipt['files'][policy_path] = c.sha(values[policy_path])
            current_hash = c.sha(c.canonical(receipt['files']))
            if target != 'compiler':
                receipt['target_contract']['sha256'] = current_hash
                owner = f'som-rs/som-targets/src/{target}/mod.rs'
                original = (REPO / owner).read_text()
                changed, count = re.subn(r'(pub\s+const\s+TARGET_CONTRACT_SHA256\s*:\s*&str\s*=\s*")[0-9a-f]{64}("\s*;)',
                                         lambda match: match[1] + current_hash + match[2], original)
                self.assertEqual(count, 1)
                values[owner] = changed.encode()
            if fresh_review: receipt['independent_review']['receipt_files_sha256'] = current_hash
            values[f'som-rs/evidence/{prefix}-contract-amendment-{number:03d}.json'] = encoded(receipt)

    def reader(self, values):
        return lambda name: values[name] if name in values else (REPO / name).read_bytes()

    def verify(self, fixture):
        values, _, _, paths = fixture
        return c.verify(self.reader(values), c.MODE, paths)

    def change_admission(self, fixture, change):
        values, receipts, policies, _ = fixture
        for policy in policies.values(): change(policy['qa_source_admission'])
        self.rebind(values, receipts, policies)

    def test_exact_synthetic_authority_admits_source_and_grants_no_product_result(self):
        result = self.verify(self.fixture())
        self.assertTrue(result['source_authority_active'])
        self.assertEqual(set(result['numbered_receipts']), {'compiler', 'rust', 'typescript'})
        self.assertIs(result['product_acceptance_passed'], False)
        self.assertIs(result['compiler_acceptance_passed'], False)
        self.assertIs(result['whole_acceptance_complete'], False)

    def test_draft_pending_policy_cannot_be_activated_by_a_receipt_flag(self):
        with self.assertRaisesRegex(ValueError, 'remains pending'): self.verify(self.fixture(activate=False))

    def test_unknown_mode_stops_before_any_source_read(self):
        reader = mock.Mock(side_effect=AssertionError('must not read source'))
        with self.assertRaisesRegex(ValueError, 'unknown QA'): c.verify(reader, 'native-from-env', [])
        reader.assert_not_called()

    def test_f59_or_other_helper_cannot_supply_final_native_QA_source(self):
        reader = mock.Mock(side_effect=AssertionError('must reject identity before reading source'))
        for helper, candidate in [('f59e28d910ff4adb16e3becfdc82bd2978ca84fa', 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'),
                                  ('1' * 40, '2' * 40), ('short', 'short')]:
            with self.subTest(helper=helper), self.assertRaises(ValueError):
                c.verify(reader, c.MODE, [], helper_revision=helper, candidate_revision=candidate)
        reader.assert_not_called()

    def test_missing_unchanged_frozen_member_is_refused_even_after_rebinding_review(self):
        fixture = self.fixture()
        def change(admission):
            key = next(name for name in admission['native_sources'] if name not in c.REPLACEMENTS)
            admission['native_sources'].pop(key)
        self.change_admission(fixture, change)
        with self.assertRaisesRegex(ValueError, 'cannot omit unchanged'): self.verify(fixture)

    def test_missing_or_extra_native_replacement_is_refused(self):
        fixture = self.fixture()
        self.change_admission(fixture, lambda a: a['replacements'].pop(next(iter(c.REPLACEMENTS))))
        with self.assertRaisesRegex(ValueError, 'exact four'): self.verify(fixture)

    def test_changed_adapter_bytes_cannot_reuse_source_authority(self):
        fixture = self.fixture()
        name = 'som-rs/behavior/phase2/check_phase2.py'
        fixture[0][name] = (REPO / name).read_bytes() + b'\n# changed\n'
        with self.assertRaisesRegex(ValueError, 'QA source changed'): self.verify(fixture)

    def test_missing64_fixture_or_cache_helper_pin_is_refused(self):
        fixture = self.fixture()
        self.change_admission(fixture, lambda a: a['operational_sources'].pop('som-rs/scripts/hosted_readiness/product_python/test_produce_wheels.py'))
        with self.assertRaisesRegex(ValueError, 'complete operational'): self.verify(fixture)

    def test_stale_approved_Hfiles_cannot_authorize_new_policy_bytes(self):
        fixture = self.fixture()
        values, receipts, policies, _ = fixture
        policies['rust']['scope'] += ' changed'
        self.rebind(values, receipts, policies, fresh_review=False)
        with self.assertRaisesRegex(ValueError, 'fresh review'): self.verify(fixture)

    def test_old_review_scope_cannot_authorize_expanded_QA_sources(self):
        fixture = self.fixture()
        fixture[1]['rust']['independent_review']['scope'] = 'native_policy_and_validator_source_only'
        self.rebind(*fixture[:3])
        with self.assertRaisesRegex(ValueError, 'fresh review'): self.verify(fixture)

    def test_prompt_or_whole_object_digest_exception_is_refused(self):
        fixture = self.fixture()
        for policy in fixture[2].values(): policy['allowed_compile_digest_leaves'].append({'path': '/bundle/prompt', 'authority': 'invalid'})
        self.rebind(*fixture[:3])
        with self.assertRaisesRegex(ValueError, 'exact eligible metadata'): self.verify(fixture)

    def test_old_checker_archive_bytes_remain_exact(self):
        fixture = self.fixture()
        archive = fixture[1]['rust']['changes'][0]['archived_copy']
        fixture[0][archive] = b'changed old checker'
        with self.assertRaisesRegex(ValueError, 'old checker archive'): self.verify(fixture)

    def test_current_profile_bytes_are_checked_without_running_a_tool(self):
        fixture = self.fixture()
        name = fixture[2]['rust']['qa_source_admission']['profiles']['rust_product']['path']
        fixture[0][name] = (REPO / name).read_bytes() + b' '
        with self.assertRaisesRegex(ValueError, 'QA source changed'): self.verify(fixture)

    def test_no_environment_flag_can_replace_numbered_authority(self):
        fixture = self.fixture()
        fixture[1]['rust']['status'] = 'draft_not_sealed_not_active'
        self.rebind(*fixture[:3])
        with mock.patch.dict(os.environ, {'SOM_NATIVE_QA_APPROVED': 'true'}):
            with self.assertRaisesRegex(ValueError, 'pending, stale'): self.verify(fixture)

    def test_newer_receipt_cannot_be_hidden_by_selecting008(self):
        fixture = self.fixture()
        fixture[3].add('som-rs/evidence/rust-output-contract-amendment-009.json')
        with self.assertRaisesRegex(ValueError, 'exact latest'): self.verify(fixture)

    def test_incomplete_frozen_Cargo_scope_cannot_be_approved(self):
        fixture = self.fixture()
        self.change_admission(fixture, lambda a: a['cargo_scope'].update(registry_union_packages=351))
        with self.assertRaisesRegex(ValueError, 'Cargo scope'): self.verify(fixture)

    def test_repository_PYC_is_refused_before_subject_execution(self):
        reader = mock.Mock(side_effect=AssertionError('no source should execute'))
        finder = c.RawQaFinder(reader, REPO, {'example.py'})
        found = SimpleNamespace(origin=str(REPO / 'example.pyc'), submodule_search_locations=None)
        with mock.patch.object(c.importlib.machinery.PathFinder, 'find_spec', return_value=found):
            with self.assertRaisesRegex(ValueError, 'repository PYC refused'):
                finder.find_spec('example')
        reader.assert_not_called()

    def test_preloaded_unbound_QA_helper_is_refused(self):
        fixture = self.fixture()
        reader = self.reader(fixture[0])
        authority = c.verify(reader, c.MODE, fixture[3])
        stale = ModuleType('_stale_native_qa_fixture')
        stale.__file__ = str(REPO / 'som-rs/scripts/hosted_readiness/product_python/produce_wheels.py')
        with mock.patch.dict(sys.modules, {stale.__name__: stale}):
            with self.assertRaisesRegex(ValueError, 'preloaded repository QA'):
                c.install_raw_finder(reader, REPO, authority)

    def test_final68_capture_output_is_not_a_sealing_input(self):
        fixture = self.fixture()
        reads = []
        reader = self.reader(fixture[0])
        def observed(name):
            reads.append(name)
            return reader(name)
        self.assertTrue(c.verify(observed, c.MODE, fixture[3])['source_authority_active'])
        self.assertFalse(any(name.endswith('/manifest.json') and 'current-python' in name for name in reads))


if __name__ == '__main__':
    unittest.main()
