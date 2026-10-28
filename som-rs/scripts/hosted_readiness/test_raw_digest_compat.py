"""Raw digest compatibility fixtures. Execute on GitHub only."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("raw_digest_compat", Path(__file__).with_name("raw_digest_compat.py"))
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)

A, B, C, D, E, F = (char * 64 for char in "abcdef")
ONE, TWO, THREE, FOUR = (char * 64 for char in "1234")
PINS = [
    ("/assets/backend/sha256", A, B),
    ("/assets/formatter/sha256", C, D),
    ("/assets/target_contract/sha256", E, F),
    ("/hashes/payload_sha256", ONE, TWO),
]


def reply(new=False, noise=None):
    value = {
        "assets": {
            "backend": {"id": "backend.v1", "sha256": B if new else A},
            "formatter": {"id": "formatter.v1", "sha256": D if new else C},
            "target_contract": {"id": "contract.v1", "sha256": F if new else E},
        },
        "hashes": {"payload_sha256": TWO if new else ONE, "prompt_sha256": THREE, "dsl_sha256": FOUR},
        "files": {"main.py": "return 1\n"},
    }
    if noise is not None:
        value["noise"] = noise
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


class RawDigestCompatTests(unittest.TestCase):
    def test_exact_scope_and_original_hash_only_golden(self):
        before, after = reply(), reply(True)
        self.assertTrue(compat.verify_raw(before, after, PINS))
        self.assertEqual(compat.mask_raw(before, PINS, "before"), compat.mask_raw(after, PINS, "after"))
        self.assertEqual(compat.masked_sha256(before, PINS, "before"), compat.masked_sha256(after, PINS, "after"))
        original = hashlib.sha256(before).hexdigest()
        self.assertEqual(compat.rebased_sha256(after, PINS), original)
        self.assertTrue(compat.verify_frozen(after, original, PINS))

    def test_every_non_leaf_byte_stays_exact(self):
        before, after = reply(), reply(True)
        reordered = json.dumps(json.loads(after), sort_keys=True, separators=(",", ":")).encode() + b"\n"
        variants = [
            after + b" ",
            after.replace(b'"assets":', b'"assets" :', 1),
            reordered,
            after.replace(b"backend.v1", b"backend.v2"),
            after.replace(b"backend.v1", b"backend\\u002ev1"),
            after.replace(THREE.encode(), b"5" * 64),
            after.replace(FOUR.encode(), b"6" * 64),
            after.replace(b"return 1", b"return 2"),
        ]
        golden = hashlib.sha256(before).hexdigest()
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaises(compat.RawDigestError):
                    compat.verify_raw(before, value, PINS)
                with self.assertRaises(compat.RawDigestError):
                    compat.verify_frozen(value, golden, PINS)

    def test_fake_pointer_and_key_text_in_strings_is_not_a_leaf(self):
        noise = '{"sha256":"' + A + '","/assets/backend/sha256":"' + A + '"}'
        before, after = reply(noise=noise), reply(True, noise=noise)
        self.assertTrue(compat.verify_raw(before, after, PINS))
        changed_noise = after.replace(A.encode(), B.encode())
        with self.assertRaisesRegex(compat.RawDigestError, "outside"):
            compat.verify_raw(before, changed_noise, PINS)

    def test_arrays_escaped_keys_and_multibyte_offsets(self):
        before = ('{"文档":[{"sha\\u0032\\u0035\\u0036":"' + A + '"}],"a/b":{"~x":"' + C + '"}}').encode()
        after = before.replace(A.encode(), B.encode()).replace(C.encode(), D.encode())
        pins = [("/文档/0/sha256", A, B), ("/a~1b/~0x", C, D)]
        self.assertTrue(compat.verify_raw(before, after, pins))
        self.assertTrue(compat.verify_frozen(after, hashlib.sha256(before).hexdigest(), pins))

    def test_escaped_digest_value_is_refused(self):
        raw = ('{"value":"' + "\\u0061" * 64 + '"}').encode()
        with self.assertRaisesRegex(compat.RawDigestError, "unescaped"):
            compat.verify_raw(raw, raw, [("/value", A, A)])

    def test_duplicate_keys_include_escaped_aliases(self):
        for raw in (
            ('{"value":"' + A + '","value":"' + A + '"}').encode(),
            ('{"value":"' + A + '","va\\u006cue":"' + A + '"}').encode(),
            b'{"nested":{"x":1,"x":1},"value":"' + A.encode() + b'"}',
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(compat.RawDigestError, "duplicate"):
                    compat.verify_raw(raw, raw, [("/value", A, A)])

    def test_malformed_json_is_refused_even_outside_pinned_paths(self):
        values = [b"", b"nullx", b'{"x":01}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1,}', b'{"x":"\\x00"}', b'{"x":"\xff"}', b'["x",]', b'{"x":1']
        for raw in values:
            with self.subTest(raw=raw):
                with self.assertRaises(compat.RawDigestError):
                    compat.verify_raw(raw, raw, [])
        for suffix in (b'"x":NaN}', b'"x":01}', b'"x":"\\x00"}', b'"x":[1,]}'):
            raw = b'{"value":"' + A.encode() + b'",' + suffix
            with self.subTest(suffix=suffix):
                with self.assertRaises(compat.RawDigestError):
                    compat.verify_raw(raw, raw, [("/value", A, A)])

    def test_missing_wrong_type_and_wrong_digest_refused(self):
        for raw in (b"{}", b'{"value":1}', b'{"value":null}', b'{"value":[]}', b'{"value":{}}', b'{"value":"' + b"A" * 64 + b'"}', b'{"value":"' + b"a" * 63 + b'"}', b'{"value":"' + B.encode() + b'"}'):
            with self.subTest(raw=raw):
                with self.assertRaises(compat.RawDigestError):
                    compat.verify_raw(raw, raw, [("/value", A, A)])

    def test_bad_and_duplicate_pointer_pins_are_refused(self):
        invalid = [
            [("/assets/backend/sha256", A, B)] * 2,
            [("assets/backend/sha256", A, B)],
            [("/assets/~2backend", A, B)],
            [("/assets/backend/sha256", "A" * 64, B)],
            [("/assets/backend/sha256", A[:-1], B)],
            [("/assets/backend/sha256", A, None)],
            [("/assets/backend/sha256", A)],
        ]
        for pins in invalid:
            with self.subTest(pins=pins):
                with self.assertRaises(compat.RawDigestError):
                    compat.verify_raw(reply(), reply(True), pins)

    def test_container_and_child_cannot_form_overlapping_allowed_spans(self):
        pins = [("/assets/backend", A, B), ("/assets/backend/sha256", A, B)]
        with self.assertRaises(compat.RawDigestError):
            compat.verify_raw(reply(), reply(True), pins)

    def test_overlap_guard_refuses_a_corrupt_span_index(self):
        class CorruptDocument:
            nodes = {("one",): ("string", 0, 66, A), ("two",): ("string", 0, 66, A)}
        with patch.object(compat, "_Document", return_value=CorruptDocument()):
            with self.assertRaisesRegex(compat.RawDigestError, "overlap"):
                compat.mask_raw(b'"' + A.encode() + b'"', [("/one", A, A), ("/two", A, A)], "before")

    def test_frozen_hash_pin_and_comparison_side_are_checked(self):
        for value in (None, "A" * 64, "0" * 63):
            with self.subTest(value=value):
                with self.assertRaises(compat.RawDigestError):
                    compat.verify_frozen(reply(True), value, PINS)
        with self.assertRaises(compat.RawDigestError):
            compat.mask_raw(reply(), PINS, "unknown")

    def test_no_pins_proves_literal_byte_equality(self):
        self.assertTrue(compat.verify_raw(b' [1, true, null, {"x":"a"}]\n', b' [1, true, null, {"x":"a"}]\n', []))
        with self.assertRaises(compat.RawDigestError):
            compat.verify_raw(b'{"x":1}', b'{"x":1.0}', [])


if __name__ == "__main__":
    unittest.main()
