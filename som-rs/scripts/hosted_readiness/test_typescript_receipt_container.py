"""GitHub-only TS verifier placement checks; no valid gate result is supplied."""
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


class TypeScriptReceiptContainerTests(unittest.TestCase):
    def setUp(self):
        path = HERE / 'typescript_regression_owner.py'
        self.owner = ModuleType('_som_ts_receipt_container_fixture')
        self.owner.__file__, self.owner.__package__, self.owner.__cached__ = str(path), '', None
        exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), self.owner.__dict__)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        env = mock.patch.dict(os.environ, {'RUNNER_TEMP': str(self.base)})
        env.start(); self.addCleanup(env.stop)
        def directory(name):
            result = self.base / name; result.mkdir(parents=True); return result
        self.capture = directory('raw/gate/owner'); (self.capture / 'result.json').write_bytes(b'{}')
        self.repo = directory('repo')
        self.proofs = {name: directory('proofs/' + name) for name in self.owner.PROOF_ROLES}
        self.container = directory('transport/som-typescript-input-consumer')
        for name, body in {'receipt.json': b'{"fixture":"consumer"}\n',
                           'evidence/check.json': b'{"evidence":1}',
                           'authenticated/member': b'authenticated fixture data',
                           'cipher/member.sealed': b'opaque fixture ciphertext',
                           'proof/origin.json': b'{"proof":1}'}.items():
            path = self.container / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
        self.receipt = self.container / 'receipt.json'
        self.data = {'typescript_regression_inputs': {
            'roots': {name: str(directory('inputs/' + name)) for name in self.owner.INPUT_ROLES},
            'siblings': {'rs_prompts': str(directory('inputs/rs_prompts'))},
            'receipt': str(self.receipt), 'artifact': {'id': 1,
                'name': 'same-candidate-typescript-regression-inputs', 'digest': 'sha256:'+'f'*64,
                'receipt_sha256': hashlib.sha256(self.receipt.read_bytes()).hexdigest()}}}
        self.data.update({name: str(directory('runtime/' + name)) for name in
                          ('runtime_root','wheel_root','rust_tools_root','cargo_root')})
        (self.capture / 'context.input.json').write_text(json.dumps(self.data))
        self.output = self.base / 'raw/verification-evidence/disjoint'
        self.output.parent.mkdir(parents=True)

    def invoke(self, output, reader):
        return self.owner.verify_result(self.capture, reader, 'a'*40, 'a'*40, self.owner.GATE,
            repo=self.repo, proof_roots=self.proofs, verification_evidence_root=output)

    def test_all_consumer_container_output_witnesses_refuse_before_source_or_mkdir(self):
        before = self.owner.tree(self.container)
        reader = mock.Mock(side_effect=AssertionError('must not read project source'))
        invalid = [self.container / 'evidence/new-check', self.container / 'authenticated/new-check',
                   self.container / 'new-sibling-output', self.container, self.container.parent, self.receipt]
        for output in invalid:
            with self.subTest(output=output), \
                 mock.patch.object(self.owner, 'raw_module', side_effect=AssertionError('must not load source')), \
                 mock.patch.object(self.owner.Path, 'mkdir', side_effect=AssertionError('must not create output')):
                with self.assertRaisesRegex(ValueError, 'whole TypeScript consumer container'):
                    self.invoke(output, reader)
        reader.assert_not_called(); self.assertEqual(self.owner.tree(self.container), before)
        self.assertFalse((self.container / 'new-sibling-output').exists())

    def test_disjoint_output_passes_placement_guard_then_refuses_incomplete_capture_identity(self):
        before = self.owner.tree(self.container)
        reader = mock.Mock(side_effect=AssertionError('must not read project source'))
        # The fixture deliberately has no accepted result.version. This reaches
        # the existing identity refusal after placement; it grants no gate pass.
        with mock.patch.object(self.owner, 'raw_module', side_effect=AssertionError('must not load source')):
            with self.assertRaisesRegex(KeyError, 'version'):
                self.invoke(self.output, reader)
        reader.assert_not_called(); self.assertTrue(self.output.is_dir())
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.owner.tree(self.container), before)


if __name__ == '__main__':
    unittest.main()
