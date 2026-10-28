"""Fake CLI streams and forwarding only. Run on GitHub; no compiler is executed."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("react_cli_raw_fixture", Path(__file__).with_name("cli_raw.py"))
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class CliRawTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.scratch = self.out / "cli-inputs"
        self.scratch.mkdir()
        self.source = "Project café.\n"
        self.body = encoded({"format": "som.cnl.v1", "source": self.source, "target": "react"})
        self.bundle = {"version": "som.dsl-bundle.v2", "target": "react", "files": []}
        self.reply = b" \n" + encoded({"status": "ok", "bundle": self.bundle}) + b"\n"
        self.stdout = json.dumps(self.bundle, indent=2).encode() + b"\n"

    def check(self, *, label="case", code=0, stdout=None, stderr=b"", status=200, reply=None, captured=None, expect=None):
        done = subprocess.CompletedProcess(["never-executed"], code, self.stdout if stdout is None else stdout, stderr)
        helper = SimpleNamespace(_run_cli_with_request_capture=mock.Mock(return_value=(done,
            [("/v1/compile", self.body)] if captured is None else captured)),
            post=mock.Mock(return_value=(status, self.reply if reply is None else reply)))
        row = cli.check_case(helper, "/fake/som", "http://127.0.0.1:18919", self.scratch, label,
                             self.source, "react", expect=expect)
        return row, helper

    def test_success_keeps_all_exact_streams_and_forwarding_bytes(self):
        row, helper = self.check()
        self.assertTrue(row["passed"])
        self.assertEqual(row["capture_count"], 1)
        for name, expected in (("stdout", self.stdout), ("stderr", b""), ("http_response", self.reply), ("request", self.body)):
            path = self.out / row[name + "_file"]
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(row[name + "_sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(row["request_sha256"], row["expected_request_sha256"])
        args = helper._run_cli_with_request_capture.call_args.args
        self.assertEqual(args[:2], ("/fake/som", "http://127.0.0.1:18919"))
        self.assertEqual(args[2].read_text(), self.source)
        self.assertEqual(args[3], "react")
        helper.post.assert_called_once_with("http://127.0.0.1:18919", self.body)

    def test_refusal_keeps_raw_stderr_and_requires_exit_two(self):
        response = {"status": "error", "code": "INVALID_SCHEMA", "detail": "target refused", "diagnostics": []}
        stderr = json.dumps(response, indent=2).encode() + b"\n"
        row, _ = self.check(code=2, stdout=b"", stderr=stderr, status=422, reply=encoded(response), expect="INVALID_SCHEMA")
        self.assertTrue(row["passed"])
        self.assertEqual((self.out / row["stderr_file"]).read_bytes(), stderr)
        for label, code, expected in (("bad-exit", 1, "INVALID_SCHEMA"), ("bad-code", 2, "OTHER")):
            with self.subTest(label=label):
                row, _ = self.check(label=label, code=code, stdout=b"", stderr=stderr, status=422, reply=encoded(response), expect=expected)
                self.assertFalse(row["passed"])

    def test_zero_or_duplicate_capture_blocks_acceptance(self):
        for count in (0, 2):
            with self.subTest(count=count):
                row, _ = self.check(label=str(count), captured=[("/v1/compile", self.body)] * count)
                self.assertFalse(row["passed"])
                self.assertEqual(row["capture_count"], count)
                self.assertNotIn("request_file", row)
                self.assertTrue((self.out / row["stdout_file"]).is_file())

    def test_forwarding_route_or_body_drift_is_retained_and_refused(self):
        changed = encoded({"format": "som.cnl.v1", "source": self.source, "target": "python"})
        for label, capture in (("route", ("/wrong", self.body)), ("body", ("/v1/compile", changed))):
            with self.subTest(label=label):
                row, _ = self.check(label=label, captured=[capture])
                self.assertFalse(row["passed"])
                self.assertEqual((self.out / row["request_file"]).read_bytes(), capture[1])
                self.assertIn("CLI forwarding request differs", row["problems"])

    def test_invalid_cli_or_http_json_cannot_pass(self):
        for label, options in (("stdout", {"stdout": b"{broken"}), ("http", {"reply": b"{broken"}),
                               ("stderr", {"code": 2, "status": 400, "stdout": b"", "stderr": b"not-json", "reply": b'{"code":"BAD"}'})):
            with self.subTest(label=label):
                row, _ = self.check(label=label, **options)
                self.assertFalse(row["passed"])
                self.assertIn("CLI or HTTP output is invalid JSON", row["problems"])

    def test_success_output_must_match_its_http_bundle(self):
        row, _ = self.check(stdout=encoded({**self.bundle, "target": "rust"}))
        self.assertFalse(row["passed"])
        self.assertIn("CLI success output differs from HTTP bundle", row["problems"])

    def test_existing_raw_stream_is_not_overwritten(self):
        row, _ = self.check()
        original = (self.out / row["stdout_file"]).read_bytes()
        with self.assertRaises(FileExistsError):
            self.check(stdout=b"different")
        self.assertEqual((self.out / row["stdout_file"]).read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
