"""GitHub-only output-boundary fixtures for the actual receipt container schema."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub fixtures only')
HERE = Path(__file__).absolute().parent


class ConsumerContainerTests(unittest.TestCase):
    def setUp(self):
        path = HERE / 'run_current_candidate_gate.py'
        self.common = ModuleType('_som_caller_receipt_container_fixture')
        self.common.__file__, self.common.__package__, self.common.__cached__ = str(path), '', None
        exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), self.common.__dict__)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        env = mock.patch.dict(os.environ, {'RUNNER_TEMP': str(self.base)})
        env.start(); self.addCleanup(env.stop)
        def directory(name):
            result = self.base / name; result.mkdir(parents=True); return result
        self.capture = directory('raw/gate')
        self.repo = directory('repo')
        self.proofs = {name: directory('proofs/' + name) for name in self.common.ROLES}
        self.container = directory('transport/som-typescript-input-consumer')
        for name, body in {'receipt.json': b'{"fixture":"consumer"}\n',
                           'evidence/check.json': b'{"evidence":1}',
                           'authenticated/member': b'authenticated fixture data',
                           'cipher/member.sealed': b'opaque fixture ciphertext',
                           'proof/origin.json': b'{"proof":1}'}.items():
            path = self.container / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
        self.receipt = self.container / 'receipt.json'
        self.data = {'typescript_regression_inputs': {
            'roots': {name: str(directory('inputs/' + name)) for name in
                      ('public_run','legacy_run','rust_run','npm_cache','node_tools')},
            'siblings': {'rs_prompts': str(directory('inputs/rs_prompts'))},
            'receipt': str(self.receipt), 'artifact': {'id': 1,
                'name': 'same-candidate-typescript-regression-inputs', 'digest': 'sha256:'+'f'*64,
                'receipt_sha256': hashlib.sha256(self.receipt.read_bytes()).hexdigest()}}}
        self.data.update({name: str(directory('runtime/' + name)) for name in
                          ('runtime_root','wheel_root','rust_tools_root','cargo_root')})
        self.output = self.base / 'raw/verification-evidence/new-check'

    def keyword(self):
        return {'capture_root': self.capture, 'repo': self.repo, 'proof_roots': self.proofs, 'data': self.data}

    def test_receipt_file_container_descendants_and_ancestors_refuse_before_reads_or_mkdir(self):
        before = self.common.tree(self.container)
        invalid = [self.container / 'evidence/new-check', self.container / 'authenticated/new-check',
                   self.container / 'new-sibling-output', self.container, self.container.parent, self.receipt]
        for output in invalid:
            with self.subTest(output=output), \
                 mock.patch.object(self.common, 'read', side_effect=AssertionError('must not read source or payload')), \
                 mock.patch.object(self.common.os, 'mkdir', side_effect=AssertionError('must not create output')):
                with self.assertRaisesRegex(ValueError, 'whole TypeScript consumer container'):
                    self.common.prepare_verification_evidence_root(output, **self.keyword())
        self.assertEqual(self.common.tree(self.container), before)
        self.assertFalse((self.container / 'new-sibling-output').exists())

    def test_normal_disjoint_output_is_allowed_without_reading_or_changing_consumer_bytes(self):
        before = self.common.tree(self.container)
        with mock.patch.object(self.common, 'read', side_effect=AssertionError('must not read source or payload')):
            result = self.common.prepare_verification_evidence_root(self.output, **self.keyword())
        self.assertEqual(result, self.output); self.assertEqual(list(result.iterdir()), [])
        self.assertEqual(result.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.common.tree(self.container), before)


if __name__ == '__main__':
    unittest.main()
