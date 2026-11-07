"""GitHub-only raw-byte fixtures. The factory values are finite test data."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

HERE = Path(__file__).absolute().parent


def load(name, path):
    module = ModuleType(name); module.__file__, module.__package__, module.__cached__ = str(path), '', None
    sys.modules[name] = module
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def value_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class NativeDigestTests(unittest.TestCase):
    def setUp(self):
        self.names = ['_som_ts_digest_fixture', '_som_ts_raw_compat_fixture']
        self.helper = load(self.names[0], HERE / 'typescript_native_digests.py')
        self.raw = load(self.names[1], HERE / 'raw_digest_compat.py')
        self.before = {name: digit * 64 for name, digit in
                       (('backend', '1'), ('target_contract', '2'), ('formatter', '3'))}
        self.after = {name: digit * 64 for name, digit in
                      (('backend', '4'), ('target_contract', '5'), ('formatter', '6'))}
        self.ids = {'typescript_' + key: 'som.fixture.' + key for key in self.before}
        self.factory = SimpleNamespace(IDS=self.ids, value_sha=value_sha)
        self.helper.authorities = lambda ctx, target: (self.factory, self.raw, self.before, self.after)
        self.ctx = object()

    def tearDown(self):
        for name in self.names:
            sys.modules.pop(name, None)

    def document(self, pins):
        bundle = {'version': 'som.dsl-bundle.v2', 'target': 'typescript',
                  'prompt': {'version': 'som.prompt.v6', 'text': 'same'}, 'dsl': {'fixed': True},
                  'assets': {name: {'id': self.ids['typescript_' + name], 'sha256': pin} for name, pin in pins.items()}}
        bundle['hashes'] = {'prompt_sha256': value_sha(bundle['prompt']), 'dsl_sha256': value_sha(bundle['dsl']),
                            'payload_sha256': value_sha(bundle)}
        return {'status': 'ok', 'bundle': bundle}

    def pair(self):
        return self.document(self.before), self.document(self.after)

    def test_only_four_owned_sha_leaves_pass(self):
        before, after = self.pair()
        proof = self.helper.compare(self.ctx, 'typescript', compact(before), compact(after))
        self.assertEqual([row[0] for row in proof['exact_digest_pins']], list(self.helper.LEAVES))
        self.assertFalse(proof['product_acceptance_passed'])

    def test_descriptor_id_change_is_refused(self):
        before, after = self.pair(); after['bundle']['assets']['formatter']['id'] = 'other'
        with self.assertRaisesRegex(ValueError, 'descriptor'):
            self.helper.compare(self.ctx, 'typescript', compact(before), compact(after))

    def test_wrong_current_formatter_is_refused(self):
        before, after = self.pair(); after['bundle']['assets']['formatter']['sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'descriptor'):
            self.helper.compare(self.ctx, 'typescript', compact(before), compact(after))

    def test_changed_product_dsl_is_refused(self):
        before, after = self.pair(); after['bundle']['dsl']['fixed'] = False
        after['bundle']['hashes']['dsl_sha256'] = value_sha(after['bundle']['dsl'])
        after['bundle']['hashes']['payload_sha256'] = value_sha({k: v for k, v in after['bundle'].items() if k != 'hashes'})
        with self.assertRaises(ValueError):
            self.helper.compare(self.ctx, 'typescript', compact(before), compact(after))

    def test_invalid_payload_hash_is_refused(self):
        before, after = self.pair(); after['bundle']['hashes']['payload_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'canonical bundle hash'):
            self.helper.compare(self.ctx, 'typescript', compact(before), compact(after))

    def test_response_whitespace_change_is_refused(self):
        before, after = self.pair()
        with self.assertRaises(ValueError):
            self.helper.compare(self.ctx, 'typescript', compact(before), compact(after) + b'\n')

    def test_complete_digest_only_rebase_matches_current_bundle(self):
        before, after = self.pair()
        rebased, _ = self.helper.rebase(self.ctx, 'typescript', compact(before), compact(after))
        self.assertEqual(rebased, after['bundle'])

    def test_pending_real_authority_refuses_before_factory_module(self):
        helper = load('_som_ts_pending_digest_fixture', HERE / 'typescript_native_digests.py')
        self.names.append('_som_ts_pending_digest_fixture')
        calls = []
        policy = {'qa_authority_state': 'PENDING', 'sealing_dependencies': [],
                  'allowed_compile_digest_leaves': [{'path': value} for value in helper.LEAVES]}
        ctx = SimpleNamespace(qa_mode='native-qa-v1', helper_revision='a' * 40, candidate_revision='a' * 40,
            qa_source_authority={'source_authority_active': True,
                                 'admitted_repository_python_sources': [helper.SELF, helper.FACTORY, helper.RAW]},
            cache={}, sources=SimpleNamespace(read=lambda value: json.dumps(policy).encode()),
            module=lambda *values: calls.append(values))
        with self.assertRaisesRegex(ValueError, 'exact sealed'):
            helper.authorities(ctx, 'typescript')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
