"""Synthetic external candidate restrictions. GitHub runner only."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import audit130


class OwnedCandidateAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.run = self.repo / 'run'; self.run.mkdir()
        self.candidate = self.root / 'external' / 'candidate'; self.candidate.mkdir(parents=True)
        self.binary = b'fixture binary'
        (self.candidate / 'som').write_bytes(self.binary)
        digest = hashlib.sha256(self.binary).hexdigest()
        self.documents = {}
        self.identity = {'candidate': {'dir': str(self.candidate), 'binary_sha256': digest,
                         'binary_sha256_after': digest}}
        for name in ('build', 'runtime'):
            value = {'binary_sha256': digest, 'fixture': name}
            raw = json.dumps(value).encode()
            (self.candidate / (name + '.json')).write_bytes(raw)
            self.identity['candidate'][name] = value
            self.identity['candidate'][name + '_sha256'] = hashlib.sha256(raw).hexdigest()

    def candidate_row(self, actual_dir=None):
        auditor = audit130.Audit(self.repo, self.run, owned_candidate_root=actual_dir or self.candidate)
        captured = {}
        def execute_only_candidate(name, action, **kwargs):
            if name == 'integrity/candidate':
                action()
                captured['files'] = dict(auditor.reader.files)
        with mock.patch.object(auditor.reader, 'json', return_value=self.identity), \
             mock.patch.object(auditor, 'row', side_effect=execute_only_candidate):
            auditor.identities()
        return captured

    def test_exact_candidate_and_real_file_hashes(self):
        result = self.candidate_row()
        self.assertEqual(set(result['files']), {'@owned-candidate/som', '@owned-candidate/build.json',
                                                '@owned-candidate/runtime.json'})
        self.assertEqual(result['files']['@owned-candidate/som'], hashlib.sha256(self.binary).hexdigest())

    def test_candidate_must_be_actual_identity_path(self):
        other = self.root / 'other'; other.mkdir()
        with self.assertRaisesRegex(audit130.EvidenceError, 'directory differ'):
            self.candidate_row(other)

    def test_different_bytes_and_symlink_are_refused(self):
        (self.candidate / 'som').write_bytes(b'not the candidate')
        with self.assertRaisesRegex(audit130.EvidenceError, 'binary hash differs'):
            self.candidate_row()
        (self.candidate / 'som').unlink()
        (self.candidate / 'som').symlink_to('/bin/sh')
        with self.assertRaises(audit130.EvidenceError):
            self.candidate_row()

    def test_no_other_external_member_or_general_escape(self):
        reader = audit130.Reader(self.repo)
        with self.assertRaisesRegex(audit130.EvidenceError, 'member refused'):
            reader.owned_candidate_bytes(self.candidate, 'secret')
        with self.assertRaisesRegex(audit130.EvidenceError, 'escapes repository'):
            reader.path(self.candidate / 'som')

    def test_external_mode_is_explicitly_unverified(self):
        fake = mock.Mock()
        fake.run_audit.return_value = {'limitations': [], 'totals': {'failed': 0, 'unrun': 0}}
        with mock.patch.object(audit130, 'Audit', return_value=fake):
            result = audit130.run_audit(self.repo, self.run, owned_candidate_root=self.candidate)
        self.assertEqual(result['owned_candidate_process_binding'], 'unverified')
        self.assertIn('process ownership', result['limitations'][-1])

    def test_external_cli_cannot_claim_acceptance(self):
        out = self.repo / 'external-audit.json'
        report = {'totals': {'failed': 0, 'unrun': 0}, 'owned_candidate_process_binding': 'unverified'}
        args = ['--repo', str(self.repo), '--run', str(self.run), '--out', str(out),
                '--owned-candidate-root', str(self.candidate)]
        with mock.patch.object(audit130, 'run_audit', return_value=report):
            self.assertEqual(audit130.main(args), 1)
        self.assertEqual(json.loads(out.read_bytes()), report)


if __name__ == '__main__':
    unittest.main()
