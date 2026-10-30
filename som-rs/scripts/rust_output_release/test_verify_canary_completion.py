#!/usr/bin/env python3
"""Synthetic role completion fixtures; execute on GitHub-hosted runners only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

if not (os.environ.get('GITHUB_ACTIONS') == 'true'
        and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
        and os.environ.get('GITHUB_REPOSITORY') == 'faberline/som'):
    raise RuntimeError('execute these fixtures only on a GitHub-hosted runner')

import verify_canary_completion as subject


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class CompletionFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.runner = {'run_id': '1', 'run_attempt': '1', 'source_commit': 'a' * 40}
        self.expected = {'cases': {}, 'compiler_hashes': {
            'candidate': {'binary': 'a' * 64}, 'previous': {'binary': 'b' * 64}}}
        for role, families in subject.client.FAMILIES.items():
            self.expected['cases'][role] = {}
            for family in families:
                self.expected['cases'][role][family] = [
                    {'name': role + '-' + family + '-' + str(number), 'steps': [
                        {'kind': 'http'}, {'kind': 'http'}, {'kind': 'cli', 'binaries': ['fixture']}]}
                    for number in range(2)]
            self.create_role(role)
        self.projection = mock.patch.object(subject.client, 'inventory', return_value=self.expected)
        self.projection.start()
        self.addCleanup(self.projection.stop)

    def create_role(self, role):
        output = self.root / 'run' / role
        output.mkdir(parents=True)
        cases = [case for family in subject.client.FAMILIES[role]
                 for case in self.expected['cases'][role][family]]
        names = [case['name'] for case in cases]
        journal = [{'stage': 'canary_passed', 'name': name, 'port': 18919,
                    'label': 'hosted-' + role} for name in names]
        (output / 'journal.json').write_text(json.dumps(journal))
        for name in subject.expected_raw_names(cases) - {'journal.json'}:
            (output / name).write_bytes(b'synthetic')
        raw = [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())}
               for p in sorted(output.iterdir())]
        receipt = {'version': 'som.m6.hosted-canary-result.v1', 'passed': True, 'role': role,
                   'runner': self.runner, 'port': 18919, 'inputs_postchecked': True,
                   'production_host_proven': False, 'production_acceptance_passed': False,
                   'check_default_replaced': False, 'seal_required_before_retention': True,
                   'step_outcome_required': True, 'evidence_failures': [],
                   'client_sha256': subject.rc.digest(subject.client.__file__),
                   'common_sha256': subject.rc.digest(subject.rc.__file__),
                   'candidate_binary_sha256': 'a' * 64, 'previous_binary_sha256': 'b' * 64,
                   'expected_case_names': names, 'raw_files': raw}
        (output / 'hosted-canary-result.json').write_text(json.dumps(receipt))

    def verify(self, outcome='success'):
        return subject.verify({}, {}, self.root, self.runner, outcome)

    def receipt(self, role):
        return self.root / 'run' / role / 'hosted-canary-result.json'

    def change_receipt(self, role, key, value):
        path = self.receipt(role)
        data = json.loads(path.read_bytes())
        data[key] = value
        path.write_text(json.dumps(data))

    def test_complete_roles_are_not_production_acceptance(self):
        result = self.verify()
        self.assertTrue(result['complete'])
        self.assertFalse(result['production_acceptance_passed'])
        self.assertFalse(result['github_step_outcome_independently_verified'])

    def test_failed_cancelled_and_missing_outcomes_are_refused(self):
        for outcome in ('failure', 'cancelled', 'skipped', '', 'unknown'):
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                self.verify(outcome)

    def test_missing_role_or_receipt_is_refused(self):
        self.receipt('previous').unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_partial_json_is_refused(self):
        self.receipt('candidate').write_text('{"passed":')
        with self.assertRaises(ValueError):
            self.verify()

    def test_complete_receipt_cannot_override_failed_step(self):
        # Models a write or fsync failure after valid passed=true bytes reached disk.
        self.assertTrue(json.loads(self.receipt('candidate').read_bytes())['passed'])
        with self.assertRaises(ValueError):
            self.verify('failure')

    def test_false_receipt_is_refused(self):
        self.change_receipt('candidate', 'passed', False)
        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_case_count_or_order_is_refused(self):
        path = self.receipt('candidate')
        data = json.loads(path.read_bytes())
        data['expected_case_names'].reverse()
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_journal_is_refused(self):
        (self.root / 'run/candidate/journal.json').unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_reordered_journal_is_refused_even_if_raw_hash_is_updated(self):
        path = self.root / 'run/candidate/journal.json'
        journal = json.loads(path.read_bytes())
        journal.reverse()
        path.write_text(json.dumps(journal))
        receipt_path = self.receipt('candidate')
        receipt = json.loads(receipt_path.read_bytes())
        item = next(row for row in receipt['raw_files'] if row['name'] == 'journal.json')
        item['bytes'] = path.stat().st_size
        item['sha256'] = sha(path.read_bytes())
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_raw_bytes_are_refused(self):
        (self.root / 'run/candidate/0001-request.raw').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_workflow_identity_is_refused(self):
        self.change_receipt('previous', 'runner', {'run_id': '2'})
        with self.assertRaises(ValueError):
            self.verify()

    def test_single_file_coverage_cannot_prove_complete_cases(self):
        lone = Path(self.temp.name).resolve() / 'lone'
        (lone / 'run/candidate').mkdir(parents=True)
        (lone / 'run/candidate/0001-request.raw').write_bytes(b'synthetic')
        with self.assertRaises(ValueError):
            subject.verify({}, {}, lone, self.runner, 'success')


if __name__ == '__main__':
    unittest.main()
