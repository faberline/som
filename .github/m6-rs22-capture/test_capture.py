"""Synthetic RS22 capture checks; execute only on a GitHub runner."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import capture


class CaptureFixtureTests(unittest.TestCase):
    def test_checked_in_canonical_vectors_include_signed_zero(self):
        repo = Path(os.environ["GITHUB_WORKSPACE"])
        raw = (repo / "som-rs/contracts/compiler-v1/canonical-vectors.json").read_bytes()
        capture.qualify_vectors(raw)
        self.assertEqual(capture.canonical(-0.0), "-0.0")
        self.assertNotEqual(capture.value_sha(-0.0), capture.value_sha(0.0))

    def test_duplicate_or_nonfinite_json_is_refused(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(capture.Refusal):
                capture.parse(raw)

    def test_typed_removal_keeps_order_unicode_and_negative_zero(self):
        typed = capture.parse('{"format":"som.prompt-json.v1","prompt":{"name":"例","n":-0.0},"target":"rust"}'.encode())
        body = capture.compact({"format": typed["format"], "prompt": typed["prompt"]})
        self.assertEqual(body, '{"format":"som.prompt-json.v1","prompt":{"name":"例","n":-0.0}}'.encode())

    def test_typed_prompt_identity_is_bound_even_for_refusal_cases(self):
        prompt = {"name": "refusal-fixture", "n": -0.0}
        typed = capture.compact({"format": "som.prompt-json.v1", "prompt": prompt, "target": "rust"})
        expected = capture.compact({"format": "som.prompt-json.v1", "prompt": prompt})
        result = capture.typed_python_request(typed, capture.sha(typed), capture.value_sha(prompt), capture.sha(expected))
        self.assertEqual(result, expected)
        with self.assertRaises(capture.Refusal):
            capture.typed_python_request(typed, capture.sha(typed), "0" * 64, capture.sha(expected))

    def test_safe_file_rejects_unsafe_paths_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok").write_bytes(b"safe")
            (root / "link").symlink_to(root / "ok")
            self.assertEqual(capture.physical_file(root, "ok"), b"safe")
            for value in ("../ok", "/ok", "a//b", "link", "a\\b", ""):
                with self.subTest(value=value), self.assertRaises(capture.Refusal):
                    capture.physical_file(root, value)

    def test_product_capture_records_full_bytes_and_file_hashes(self):
        prompt = {"name": "synthetic", "n": -0.0}
        bundle = {"version": "som.dsl-bundle.v1", "prompt": prompt}
        files = {"src/a.py": "print('ok')\n", "README.md": "fixture\n"}
        responses = [(200, capture.compact({"bundle": bundle})), (200, capture.compact({"files": files}))]
        row = {"expected_control_status": 200, "frozen_prompt_sha256": capture.value_sha(prompt)}
        with tempfile.TemporaryDirectory() as tmp, patch.object(capture, "http", side_effect=responses):
            directory = Path(tmp) / "RS03_JSON_V6"
            directory.mkdir()
            result = capture.execute_case(directory, row, {}, "native", b'{"request":"fixture"}')
            self.assertEqual(result["status"], "captured_product")
            self.assertEqual(result["files_map_sha256"], capture.value_sha(files))
            self.assertEqual(result["files_sha256"]["src/a.py"], capture.sha(files["src/a.py"].encode()))
            self.assertEqual(result["assemble_response"]["path"], "run/RS03_JSON_V6/native.assemble.response.raw")
            self.assertEqual((directory / "native.assemble.response.raw").read_bytes(), responses[1][1])

    def test_changed_prompt_or_assemble_status_fails_closed(self):
        prompt = {"name": "case"}
        bundle = capture.compact({"bundle": {"version": "som.dsl-bundle.v1", "prompt": prompt}})
        with tempfile.TemporaryDirectory() as tmp:
            for index, (row, replies) in enumerate([
                ({"expected_control_status": 200, "frozen_prompt_sha256": "0" * 64}, [(200, bundle)]),
                ({"expected_control_status": 200, "frozen_prompt_sha256": capture.value_sha(prompt)}, [(200, bundle), (500, b'{}')]),
            ]):
                directory = Path(tmp) / str(index)
                directory.mkdir()
                result = {"status": "unrun"}
                with self.subTest(index=index), patch.object(capture, "http", side_effect=replies), self.assertRaises(capture.Refusal):
                    capture.execute_case(directory, row, {}, "typed", b'{}', result=result)
                self.assertEqual(result["status"], "failed")
                self.assertIn("compile_request", result)
                self.assertIn("compile_response", result)

    def test_refusal_has_no_product_and_native_span_checked(self):
        expected = {"code": "INVALID_VALUE", "cause": "bad name", "prompt_pointer": "/prompt/name"}
        case = {"expected": expected, "python_control_expected": "the same diagnostic, located in python_control_body",
                "span_text": '"offset"'}
        request = b'{"name":"offset"}'
        start = request.index(b'"offset"')
        diag = {**expected, "byte_start": start, "byte_end": start + len(b'"offset"')}
        response = capture.compact({"code": "INVALID_VALUE", "diagnostics": [diag]})
        row = {"expected_control_status": 400}
        with tempfile.TemporaryDirectory() as tmp, patch.object(capture, "http", return_value=(400, response)):
            directory = Path(tmp) / "RS13_JSON_V3_FILTER_OFFSET"
            directory.mkdir()
            result = capture.execute_case(directory, row, case, "native", request)
            self.assertEqual(result["products"], "not_applicable_refusal")
            self.assertFalse(any("assemble" in path.name for path in directory.iterdir()))
            with self.assertRaises(capture.Refusal):
                capture.expected_error(400, response, case, b'{}', True)

    def test_wrong_refusal_code_is_rejected(self):
        case = {"expected": {"code": "INVALID_VALUE"}}
        with self.assertRaises(capture.Refusal):
            capture.expected_error(400, b'{"code":"OTHER","diagnostics":[{}]}', case, b'{}', False)

    def test_port_probe_refuses_listener_and_unknown_outcome(self):
        class Result:
            returncode = 0
            stdout = b'p123\n'
            stderr = b''
        with patch.object(capture.subprocess, "run", return_value=Result()):
            with self.assertRaises(capture.Refusal):
                capture.no_listener(18917)
        Result.returncode, Result.stdout, Result.stderr = 1, b'', b'problem'
        with patch.object(capture.subprocess, "run", return_value=Result()):
            with self.assertRaises(capture.Refusal):
                capture.no_listener(18917)


if __name__ == "__main__":
    unittest.main()
