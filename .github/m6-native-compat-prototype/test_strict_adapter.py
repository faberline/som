"""Synthetic tests for the unused strict adapter; run only on a GH runner.

The native cases assert universal refusal. They do not establish that any
future sealed authority, digest-leaf rule, or payload verifier is implemented.
"""

from dataclasses import FrozenInstanceError
import hashlib
import unittest

import strict_adapter as adapter


class StrictRawTests(unittest.TestCase):
    def test_exact_response_and_status_produce_only_an_equality_proof(self):
        raw = b'{"status":"ok","bundle":{"value":"a"}}'
        proof = adapter.compare_raw(before=raw, after=raw,
                                    before_status=200, after_status=200)
        self.assertEqual(proof.before_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(proof.after_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(proof.byte_length, len(raw))
        self.assertFalse(proof.acceptance_granted)
        with self.assertRaises(FrozenInstanceError):
            proof.byte_length = 0

    def test_same_json_with_different_spacing_is_not_equal(self):
        with self.assertRaises(adapter.ComparisonRefused):
            adapter.compare_raw(before=b'{"x":1}', after=b'{"x": 1}',
                                before_status=200, after_status=200)

    def test_changed_descriptor_id_is_not_equal(self):
        left = b'{"assets":{"backend":{"id":"old","sha256":"' + b'a' * 64 + b'"}}}'
        right = left.replace(b'"old"', b'"new"')
        with self.assertRaises(adapter.ComparisonRefused):
            adapter.compare_raw(before=left, after=right,
                                before_status=200, after_status=200)

    def test_payload_digest_and_unlisted_byte_drift_are_not_equal(self):
        left = b'{"payload_sha256":"' + b'a' * 64 + b'","mode":"safe"}'
        for changed in (left.replace(b'a' * 64, b'b' * 64),
                        left.replace(b'"safe"', b'"else"')):
            with self.subTest(changed=changed):
                with self.assertRaises(adapter.ComparisonRefused):
                    adapter.compare_raw(before=left, after=changed,
                                        before_status=200, after_status=200)

    def test_status_difference_or_invalid_type_is_refused(self):
        for before_status, after_status in [(200, 400), (True, 1), (None, 200),
                                            (200, 600), ("200", 200)]:
            with self.subTest(before_status=before_status, after_status=after_status):
                with self.assertRaises(adapter.ComparisonRefused):
                    adapter.compare_raw(before=b'{}', after=b'{}',
                                        before_status=before_status,
                                        after_status=after_status)

    def test_equal_errors_are_only_byte_equality_not_gate_acceptance(self):
        proof = adapter.compare_raw(before=b'{"error":"bad"}',
                                    after=b'{"error":"bad"}',
                                    before_status=422, after_status=422)
        self.assertFalse(proof.acceptance_granted)
        self.assertEqual(proof.before_status, 422)

    def test_mutable_or_non_bytes_input_is_refused(self):
        for value in (bytearray(b'{}'), memoryview(b'{}'), '{}', None):
            with self.subTest(type=type(value)):
                with self.assertRaises(adapter.ComparisonRefused):
                    adapter.compare_raw(before=value, after=b'{}',
                                        before_status=200, after_status=200)

    def test_proof_constructor_cannot_assert_acceptance(self):
        with self.assertRaises(TypeError):
            adapter.RawEqualityProof(200, 200, 'a' * 64, 'a' * 64, 0,
                                     acceptance_granted=True)


class StrictProductTests(unittest.TestCase):
    def test_complete_equal_maps_are_immutable_evidence(self):
        old = {'z.txt': b'z', 'a.txt': b'alpha'}
        new = {'a.txt': b'alpha', 'z.txt': b'z'}
        proof = adapter.compare_products(before_files=old, after_files=new)
        old['a.txt'] = b'mutated afterwards'
        self.assertEqual(tuple(item.path for item in proof.files), ('a.txt', 'z.txt'))
        self.assertEqual(proof.files[0].before_sha256, hashlib.sha256(b'alpha').hexdigest())
        self.assertFalse(proof.acceptance_granted)
        with self.assertRaises(FrozenInstanceError):
            proof.files[0].path = 'different'

    def test_mutated_product_or_file_set_is_refused(self):
        old = {'src/a.py': b'pass\n'}
        for new in ({'src/a.py': b'fail\n'}, {},
                    {'src/a.py': b'pass\n', 'extra': b''},
                    {'src/b.py': b'pass\n'}):
            with self.subTest(new=new):
                with self.assertRaises(adapter.ComparisonRefused):
                    adapter.compare_products(before_files=old, after_files=new)

    def test_unsafe_paths_and_mutable_file_contents_are_refused(self):
        invalid = [('/etc/a', b'x'), ('../a', b'x'), ('a//b', b'x'),
                   ('a/./b', b'x'), ('a/../b', b'x'), ('a\\b', b'x'),
                   ('a\x00b', b'x'), ('a', bytearray(b'x')), ('a', 'x')]
        for path, value in invalid:
            with self.subTest(path=path, value_type=type(value)):
                with self.assertRaises(adapter.ComparisonRefused):
                    adapter.compare_products(before_files={path: value},
                                             after_files={path: value})

    def test_empty_file_maps_are_not_accepted_as_product_evidence(self):
        with self.assertRaises(adapter.ComparisonRefused):
            adapter.compare_products(before_files={}, after_files={})

    def test_proof_constructor_cannot_assert_product_acceptance(self):
        with self.assertRaises(TypeError):
            adapter.ProductEqualityProof((), acceptance_granted=True)


class DisabledNativeTests(unittest.TestCase):
    def test_every_native_request_is_refused_without_calling_untrusted_objects(self):
        class TrappedObject:
            def __call__(self, *args, **kwargs):
                raise AssertionError('untrusted callback must not run')

            def __getattribute__(self, name):
                if name.startswith('__'):
                    return object.__getattribute__(self, name)
                raise AssertionError('untrusted attribute must not be accessed')

        fake = TrappedObject()
        base = dict(before=b'{}', after=b'{}', before_status=200, after_status=200,
                    host='linux-x86_64', role='baseline',
                    policy={'status': 'sealed', 'source_sha256': 'a' * 64},
                    source_authority=fake, products={'src/a.py': b'pass\n'})
        scenarios = {
            'draft or pending policy': {'policy': {'status': 'PENDING'}},
            'pretend sealed arbitrary policy': {},
            'wrong host': {'host': 'unknown-host'},
            'wrong role': {'role': 'unknown-role'},
            'unknown leaf': {'policy': {'leaves': ['/bundle/prompt_sha256']}},
            'extra leaf': {'policy': {'leaves': ['/bundle/assets/backend/sha256', '/extra']}},
            'descriptor ID changed': {'after': b'{"id":"different"}'},
            'forged hash': {'policy': {'expected': '0' * 64}},
            'payload hash mismatch': {'after': b'{"payload_sha256":"invalid"}'},
            'unlisted byte drift': {'after': b'{"semantic_change":true}'},
            'changed product bytes': {'products': {'old': b'a', 'new': b'b'}},
        }
        for name, changes in scenarios.items():
            with self.subTest(name=name):
                request = {**base, **changes}
                with self.assertRaises(adapter.CompatibilityUnavailable):
                    adapter.compare_native(**request)

    def test_unavailable_is_a_refusal_not_success(self):
        self.assertTrue(issubclass(adapter.CompatibilityUnavailable, adapter.ComparisonRefused))
        with self.assertRaises(adapter.CompatibilityUnavailable):
            adapter.compare_native(before=None, after=None, before_status=None,
                                   after_status=None, host=None, role=None,
                                   policy=None, source_authority=None, products=None)


if __name__ == '__main__':
    unittest.main()
