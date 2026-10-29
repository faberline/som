"""Synthetic prototype cases. Execute only on a GitHub runner.

The fixture identity, source, product and outputs are invented toy data. Passing
these tests never grants policy authority, product acceptance or deployment.
"""
from dataclasses import FrozenInstanceError
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import strict_adapter
import synthetic_only as toy


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def make_bundle(source):
    prompt = {'name': 'synthetic-case', 'version': 'synthetic.prompt.v1'}
    dsl = {'files': [toy.PRODUCT_PATH], 'version': 'synthetic.dsl.v1'}
    bundle = {'version': toy.BUNDLE_VERSION, 'target': toy.TARGET,
              'prompt': prompt, 'dsl': dsl,
              'assets': {'backend': {'id': toy.BACKEND_ID, 'sha256': digest(source)}}}
    bundle['hashes'] = {'prompt_sha256': digest(canon(prompt)),
                        'dsl_sha256': digest(canon(dsl)),
                        'payload_sha256': digest(canon(bundle))}
    return {'bundle': bundle}


def raw(doc):
    return canon(doc)


def recalc_payload(doc):
    bundle = doc['bundle']
    bundle['hashes']['payload_sha256'] = digest(canon({k: v for k, v in bundle.items() if k != 'hashes'}))
    return raw(doc)


class SyntheticOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A GH workflow must point to the exact checked-out M12 source file.
        # synthetic_only verifies this byte string's SHA before executing it.
        cls.helper = Path(os.environ['SOM_SYNTHETIC_RAW_HELPER']).read_bytes()

    def setUp(self):
        self.left = make_bundle(toy.BEFORE_SOURCE)
        self.right = make_bundle(toy.AFTER_SOURCE)
        self.args = dict(helper_source=self.helper, host=toy.FIXTURE_HOST,
                         role=toy.FIXTURE_ROLE, case=toy.CASE_OK,
                         request=toy.REQUEST_OK, before_status=200, after_status=200,
                         before=raw(self.left), after=raw(self.right),
                         changed_paths=toy.CHANGED_LEAVES,
                         before_products={toy.PRODUCT_PATH: toy.PRODUCT_BYTES},
                         after_products={toy.PRODUCT_PATH: toy.PRODUCT_BYTES})

    def rejects(self, **changes):
        with self.assertRaises(toy.FixtureRefused):
            toy.compare_fixture(**{**self.args, **changes})

    def test_only_positive_toy_fixture_comparison_is_synthetic_not_acceptance(self):
        proof = toy.compare_fixture(**self.args)
        self.assertEqual(proof.label, 'SYNTHETIC / NO ACCEPTANCE')
        self.assertFalse(proof.acceptance_granted)
        self.assertEqual(proof.compared_paths, toy.CHANGED_LEAVES)
        self.assertEqual(proof.product_sha256, digest(toy.PRODUCT_BYTES))
        self.assertEqual(proof.request_sha256, digest(toy.REQUEST_OK))
        self.assertEqual(proof.before_sha256, digest(self.args['before']))
        self.assertEqual(proof.after_sha256, digest(self.args['after']))
        with self.assertRaises(FrozenInstanceError):
            proof.label = 'acceptance'

    def test_wrong_host_role_case_or_request_refused(self):
        for changes in ({'host': 'x86_64-unknown-linux-gnu'},
                        {'role': 'baseline'}, {'case': 'actual-legacy-case'},
                        {'request': b'{"request":"another-case"}'},
                        {'request': bytearray(toy.REQUEST_OK)}):
            with self.subTest(changes=changes):
                self.rejects(**changes)

    def test_unknown_extra_missing_or_duplicated_leaf_refused(self):
        for paths in ((), toy.CHANGED_LEAVES + ('/bundle/prompt/name',),
                      ('/bundle/unknown/sha256', toy.CHANGED_LEAVES[1]),
                      (toy.CHANGED_LEAVES[0], toy.CHANGED_LEAVES[0]),
                      tuple(reversed(toy.CHANGED_LEAVES)), list(toy.CHANGED_LEAVES)):
            with self.subTest(paths=paths):
                self.rejects(changed_paths=paths)

    def test_descriptor_id_or_forged_source_hash_refused_with_valid_payload(self):
        for field, value in [('id', 'wrong.fixture.backend'), ('sha256', '0' * 64)]:
            doc = copy.deepcopy(self.right)
            doc['bundle']['assets']['backend'][field] = value
            with self.subTest(field=field):
                self.rejects(after=recalc_payload(doc))

    def test_payload_prompt_and_dsl_hashes_must_match(self):
        for field in ('payload_sha256', 'prompt_sha256', 'dsl_sha256'):
            doc = copy.deepcopy(self.right)
            doc['bundle']['hashes'][field] = '0' * 64
            with self.subTest(field=field):
                self.rejects(after=raw(doc))

    def test_prompt_and_dsl_content_cannot_be_changed_and_rehashed(self):
        for field in ('prompt', 'dsl'):
            doc = copy.deepcopy(self.right)
            doc['bundle'][field]['version'] = 'synthetic-but-different'
            doc['bundle']['hashes'][field + '_sha256'] = digest(canon(doc['bundle'][field]))
            with self.subTest(field=field):
                self.rejects(after=recalc_payload(doc))

    def test_extra_asset_and_extra_raw_byte_refused(self):
        doc = copy.deepcopy(self.right)
        doc['bundle']['assets']['other'] = {'id': 'extra', 'sha256': '0' * 64}
        self.rejects(after=recalc_payload(doc))
        self.rejects(after=b' ' + raw(self.right))

    def test_duplicate_json_keys_refused(self):
        inner = raw(self.right['bundle'])
        self.rejects(after=b'{"bundle":' + inner + b',"bundle":' + inner + b'}')
        self.rejects(after=b'{"bundle":{"a":1,"a":2}}')

    def test_status_mismatch_or_invalid_type_refused(self):
        for value in (400, True, '200'):
            with self.subTest(value=value):
                self.rejects(after_status=value)

    def test_product_set_and_file_bytes_bound_to_fixed_toy_data(self):
        for value in ({}, {toy.PRODUCT_PATH: b'changed'},
                      {toy.PRODUCT_PATH: toy.PRODUCT_BYTES, 'extra': b'x'},
                      {'some/other.txt': toy.PRODUCT_BYTES}):
            with self.subTest(value=value):
                self.rejects(after_products=value)
        self.rejects(before_products={'same': b'forged'}, after_products={'same': b'forged'})

    def test_non_200_error_requires_identical_bytes_and_no_exceptions(self):
        args = {**self.args, 'case': toy.CASE_ERROR, 'request': toy.REQUEST_ERROR,
                'before_status': 422, 'after_status': 422, 'before': toy.ERROR_RAW,
                'after': toy.ERROR_RAW, 'changed_paths': (),
                'before_products': {}, 'after_products': {}}
        proof = toy.compare_fixture(**args)
        self.assertEqual(proof.label, 'SYNTHETIC / NO ACCEPTANCE')
        self.assertFalse(proof.acceptance_granted)
        for changes in ({'after': b'{"error": "synthetic-only"}'},
                        {'changed_paths': toy.CHANGED_LEAVES},
                        {'after_products': {toy.PRODUCT_PATH: toy.PRODUCT_BYTES}}):
            with self.subTest(changes=changes):
                with self.assertRaises(toy.FixtureRefused):
                    toy.compare_fixture(**{**args, **changes})

    def test_changed_helper_is_refused_before_any_compile_or_exec(self):
        malicious = b'raise RuntimeError("must never execute")\n'
        with patch('builtins.compile', side_effect=AssertionError('must not compile')):
            self.rejects(helper_source=malicious)
        self.rejects(helper_source=bytearray(self.helper))

    def test_prototype_cannot_enable_strict_adapter_native_entry(self):
        with self.assertRaises(strict_adapter.CompatibilityUnavailable):
            strict_adapter.compare_native(before=self.args['before'], after=self.args['after'],
                                          before_status=200, after_status=200,
                                          host=toy.FIXTURE_HOST, role=toy.FIXTURE_ROLE,
                                          policy={'status': 'sealed'}, source_authority=lambda: True,
                                          products=self.args['before_products'])


if __name__ == '__main__':
    unittest.main()
