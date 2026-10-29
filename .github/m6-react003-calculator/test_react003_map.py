"""Synthetic draft-metadata fixtures. Execute only on GitHub-hosted runners."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import react003_map as m

def put(root, rel, raw):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path

def document(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode() + b'\n'

class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='react003-map-', dir=os.environ['RUNNER_TEMP'])
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.old = {m.ROOTS[0] + '/contract.json': m.sha(b'old contract'),
                    m.ROOTS[1] + '/run_gates.py': m.sha(b'old runner'),
                    m.ROOTS[2] + '/evidence.json': m.sha(b'old evidence')}
        put(self.repo, m.ROOTS[0] + '/contract.json', b'old contract')
        put(self.repo, m.ROOTS[1] + '/run_gates.py', b'new runner')
        put(self.repo, m.ROOTS[2] + '/evidence.json', b'old evidence')
        self.profile = document({'version': 'som.react-linux-qa-profile.v1', 'host': 'synthetic',
                                 'source': {'commit': 'prior', 'run': 1, 'artifact': 2, 'artifact_zip_sha256': 'old', 'tar_sha256': 'old'},
                                 'node': {'path': 'node', 'sha256': 'x'},
                                 'npm': {'root': 'npm', 'sha256': 'x', 'files': 1, 'symlinks': 0},
                                 'cache': {'path': 'cache', 'sha256': 'x', 'files': 1, 'symlinks': 0},
                                 'prettier': {'sha256': 'x', 'files': 1, 'symlinks': 0},
                                 'reference_formatter_sha256': 'old'})
        put(self.repo, m.PROFILE, self.profile)
        self.previous_target = m.sha(m.canonical(self.old))
        prior = {'amendment': 2, 'status': 'amendment_sealed_after_independent_review', 'files': self.old,
                 'target_contract': {'id': 'som.react-output-contract.v1', 'sha256': self.previous_target}}
        self.prior = put(self.repo, m.PREVIOUS, document(prior))
        self.previous_sha = m.sha(self.prior.read_bytes())
        self.archive_rel = m.ARCHIVE_ROOT + '/run_gates-before-amendment-003.py'
        self.archive = put(self.repo, self.archive_rel, b'old runner')
        self.plan = {'changed_paths': [m.ROOTS[1] + '/run_gates.py'], 'added_paths': [m.PROFILE],
                     'archives': {m.ROOTS[1] + '/run_gates.py': self.archive_rel},
                     'new_sha256': {m.ROOTS[1] + '/run_gates.py': m.sha(b'new runner'), m.PROFILE: m.sha(self.profile)}}
        self.patches = mock.patch.multiple(m, PREVIOUS_SHA=self.previous_sha, PREVIOUS_TARGET=self.previous_target,
                                           PREVIOUS_COUNT=3, PROFILE_SHA=m.sha(self.profile))
        self.patches.start()
    def close(self):
        self.patches.stop()
        self.tmp.cleanup()

class MapFixtures(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
    def test_reviewed_path_order_profile_and_historical_lexical_hash(self):
        raw = Path(__file__).with_name('qa-profile.path-order.draft.json').read_bytes()
        pinned = '5e2e4852b32e8bdc5b60ad60e50fb8e6a235690334848c13fb29ca55cb0d6c00'
        self.assertEqual(m.sha(raw), pinned)
        with mock.patch.object(m, 'PROFILE_SHA', pinned):
            profile = m.validate_profile(raw)
            self.assertEqual(profile['npm']['sha256'], '303f008099574a6edbb698df7d748a897f48f8a0c69cb1f3ee699e2937f50ca9')
            self.assertNotEqual(profile['npm']['sha256'], '69965a8f2a75cad99018e333ff1819bdc8cbf4f17570e77f24ad5f95aa57e558')
            self.assertEqual(profile['cache']['sha256'], '5267851624684b07307609f59d0b13705bd7934a1fd59bde2045463a3ee6ddda')
            self.assertEqual(profile['prettier']['sha256'], 'f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299')
            self.assertEqual(profile['reference_formatter_sha256'], '636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8')
    def test_example_plan_binds_path_order_profile_and_helper(self):
        plan = json.loads(Path(__file__).with_name('plan.example.json').read_bytes())
        pins = plan['new_sha256']
        self.assertEqual(pins[m.PROFILE], '5e2e4852b32e8bdc5b60ad60e50fb8e6a235690334848c13fb29ca55cb0d6c00')
        self.assertEqual(pins['som-rs/tests/react-output-v1/linux_qa_profile.py'], 'e5e1244e27df4717e369fb0e56c1097df59664f2cb2aa19c0766dc13dc2703d7')
    def test_complete_candidate_map_and_old_copy(self):
        result = m.calculate(self.f.repo, self.f.plan)
        self.assertEqual(result['status'], 'DRAFT')
        self.assertEqual(result['review'], 'PENDING')
        self.assertEqual(len(result['files']), 4)
        self.assertEqual(result['changes'][0]['old_sha256'], m.sha(b'old runner'))
        self.assertEqual(result['changes'][0]['archived_copy'], self.f.archive_rel)
        self.assertEqual(result['target_contract']['sha256'], m.sha(m.canonical(result['files'])))
        self.assertNotIn('head', result)
        self.assertNotIn('frozen_at', result)
    def test_previous_bytes_target_and_count_are_fixed(self):
        for field in ('bytes', 'target', 'count'):
            with self.subTest(field=field):
                with mock.patch.object(m, {'bytes': 'PREVIOUS_SHA', 'target': 'PREVIOUS_TARGET', 'count': 'PREVIOUS_COUNT'}[field],
                                       99 if field == 'count' else '0' * 64):
                    with self.assertRaises(m.Refusal):
                        m.calculate(self.f.repo, self.f.plan)
    def test_missing_removed_extra_and_unplanned_changes_fail(self):
        original = self.f.repo / (m.ROOTS[0] + '/contract.json')
        for operation in ('removed', 'changed', 'added'):
            with self.subTest(operation=operation):
                extra = self.f.repo / (m.ROOTS[2] + '/unexpected')
                if operation == 'removed': original.unlink()
                elif operation == 'changed': original.write_bytes(b'unplanned')
                else: extra.write_bytes(b'unplanned')
                with self.assertRaises(m.Refusal):
                    m.calculate(self.f.repo, self.f.plan)
                original.write_bytes(b'old contract')
                if extra.exists(): extra.unlink()
    def test_wrong_archive_and_path_escape_fail(self):
        self.f.archive.write_bytes(b'wrong')
        with self.assertRaises(m.Refusal): m.calculate(self.f.repo, self.f.plan)
        self.f.archive.write_bytes(b'old runner')
        changed = self.f.plan['changed_paths'][0]
        for rel in ('../outside', m.ARCHIVE_ROOT + '/../outside', '/etc/passwd', m.ROOTS[0] + '/contract.json'):
            with self.subTest(rel=rel):
                plan = {**self.f.plan, 'archives': {changed: rel}}
                with self.assertRaises(m.Refusal): m.calculate(self.f.repo, plan)
    def test_links_in_roots_or_archive_fail(self):
        link = self.f.repo / m.ROOTS[2] / 'link'
        link.symlink_to('evidence.json')
        with self.assertRaises(m.Refusal): m.calculate(self.f.repo, self.f.plan)
        link.unlink()
        self.f.archive.unlink()
        self.f.archive.symlink_to(self.f.repo / m.ROOTS[0] / 'contract.json')
        with self.assertRaises(m.Refusal): m.calculate(self.f.repo, self.f.plan)
    def test_profile_bytes_and_forbidden_reference_are_refused(self):
        profile = self.f.repo / m.PROFILE
        changed = json.loads(self.f.profile)
        changed['target_contract_sha256'] = '0' * 64
        raw = document(changed)
        profile.write_bytes(raw)
        self.f.plan['new_sha256'][m.PROFILE] = m.sha(raw)
        with mock.patch.object(m, 'PROFILE_SHA', m.sha(raw)):
            with self.assertRaises(m.Refusal): m.calculate(self.f.repo, self.f.plan)
        profile.write_bytes(self.f.profile + b' ')
        self.f.plan['new_sha256'][m.PROFILE] = m.sha(self.f.profile + b' ')
        with self.assertRaises(m.Refusal): m.calculate(self.f.repo, self.f.plan)
    def test_computed_target_in_mapped_input_is_refused(self):
        marker = b'a' * 64
        raw = b'new runner ' + marker
        (self.f.repo / m.ROOTS[1] / 'run_gates.py').write_bytes(raw)
        self.f.plan['new_sha256'][m.ROOTS[1] + '/run_gates.py'] = m.sha(raw)
        real_sha = m.sha
        def controlled(raw):
            if raw.startswith(b'{"som-rs/') and m.PROFILE.encode() in raw:
                return marker.decode()
            return real_sha(raw)
        with mock.patch.object(m, 'sha', side_effect=controlled):
            with self.assertRaisesRegex(m.Refusal, 'target digest embedded'):
                m.calculate(self.f.repo, self.f.plan)
    def test_new_receipt_reference_in_changed_source_is_refused(self):
        raw = b'react-output-contract-amendment-003.json'
        (self.f.repo / m.ROOTS[1] / 'run_gates.py').write_bytes(raw)
        self.f.plan['new_sha256'][m.ROOTS[1] + '/run_gates.py'] = m.sha(raw)
        with self.assertRaisesRegex(m.Refusal, 'new receipt referenced'):
            m.calculate(self.f.repo, self.f.plan)
    def test_cli_writes_only_new_draft_in_runner_temp(self):
        plan = self.f.root / 'plan.json'
        plan.write_bytes(document(self.f.plan))
        out = self.f.root / 'candidate.json'
        self.assertEqual(m.main(['--repo', str(self.f.repo), '--plan', str(plan), '--out', str(out)]), 0)
        self.assertEqual(json.loads(out.read_bytes())['status'], 'DRAFT')
        self.assertEqual(m.main(['--repo', str(self.f.repo), '--plan', str(plan), '--out', str(out)]), 2)
        self.assertEqual(m.main(['--repo', str(self.f.repo), '--plan', str(plan), '--out', str(self.f.repo / 'output')]), 2)

if __name__ == '__main__':
    unittest.main()
