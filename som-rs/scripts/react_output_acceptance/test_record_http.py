"""Synthetic HTTPResponse fixtures. No compiler, service or network is used."""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.request

SPEC = importlib.util.spec_from_file_location("m6_record_http", Path(__file__).with_name("record_http.py"))
recorder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recorder)


class FakeSocket:
    def __init__(self, raw):
        self.raw = raw

    def makefile(self, _mode):
        return io.BytesIO(self.raw)


def response(status=200, body=b'{"fake":"wire bytes"}'):
    wire = f"HTTP/1.1 {status} Fake\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
    value = http.client.HTTPResponse(FakeSocket(wire))
    value.begin()
    value.code, value.msg = value.status, value.reason
    value.url = "http://127.0.0.1:18919/v1/compile"
    return value


class RecordingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="som-m6-http-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.output = self.repo / ".local/raw"
        self.output.parent.mkdir()
        self.script = self.repo / "fake_checker.py"
        self.status = 200
        self.body = b'{"fake":"wire bytes","unicode":"\xc3\xa9"}'
        self.requests = []

    def open(self, handler, http_class, request, **kwargs):
        self.requests.append(request)
        return response(self.status, self.body)

    def script_text(self, text):
        self.script.write_text(text)

    @contextlib.contextmanager
    def patches(self):
        with mock.patch.multiple(recorder, REPO=self.repo, ALLOWED_SCRIPTS=frozenset({"fake_checker.py"})), \
             mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "M6_INPUTS_KEYRING": "fake key", "GITHUB_TOKEN": "fake token"}, clear=False), \
             mock.patch.object(urllib.request.AbstractHTTPHandler, "do_open", new=self.open):
            yield

    def invoke(self, arguments=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.patches(), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = recorder.run_checker(self.output, self.script, [] if arguments is None else arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def manifest(self):
        return json.loads((self.output / "manifest.json").read_bytes())

    def test_exact_raw_request_response_and_actual_response_object_survive(self):
        self.script_text('''import http.client, json, urllib.request
request = urllib.request.Request("http://127.0.0.1:18919/v1/compile", data=b'{"prompt":"fake input"}', method="POST")
with urllib.request.urlopen(request) as value:
    assert isinstance(value, http.client.HTTPResponse)
    assert value.status == 200
    data = value.read()
    assert value.read() == b""
    print(data.decode())
''')
        code, out, err = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out.strip().encode(), self.body)
        record = self.manifest()
        self.assertEqual(record["version"], "som.m6-http-recording.v1")
        self.assertEqual(record["script"], {"path": "fake_checker.py", "sha256": hashlib.sha256(self.script.read_bytes()).hexdigest()})
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(record["recording_errors"], [])
        call = record["calls"][0]
        self.assertTrue(call["complete"])
        self.assertIsNone(call["error"])
        self.assertEqual((call["id"], call["method"], call["status"]), (1, "POST", 200))
        self.assertEqual((self.output / call["request_file"]).read_bytes(), b'{"prompt":"fake input"}')
        self.assertEqual((self.output / call["response_file"]).read_bytes(), self.body)
        for kind in ("request", "response"):
            self.assertEqual(call[f"{kind}_sha256"], hashlib.sha256((self.output / call[f"{kind}_file"]).read_bytes()).hexdigest())
        self.assertEqual(json.loads((self.output / "calls/000001.json").read_bytes()), call)
        for path in self.output.rglob("*"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_http_error_keeps_its_type_and_full_raw_body(self):
        self.status = 422
        self.script_text('''import urllib.error, urllib.request
try:
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18919/v1/compile", data=b"fake", method="POST"))
except urllib.error.HTTPError as error:
    assert error.code == 422
    assert error.read()
    raise SystemExit(2)
''')
        code, _out, err = self.invoke()
        self.assertEqual((code, err), (2, ""))
        call = self.manifest()["calls"][0]
        self.assertEqual(call["status"], 422)
        self.assertTrue(call["complete"])
        self.assertEqual((self.output / call["response_file"]).read_bytes(), self.body)

    def test_chunked_reads_and_zero_read_do_not_change_bytes(self):
        self.script_text('''import urllib.request
with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18919/v1/compile", data=b"fake")) as value:
    assert value.read(0) == b""
    data = value.read(3) + value.read(8) + value.read()
    print(data.decode())
''')
        code, out, err = self.invoke()
        self.assertEqual((code, err, out.strip().encode()), (0, "", self.body))
        self.assertEqual((self.output / self.manifest()["calls"][0]["response_file"]).read_bytes(), self.body)

    def test_partial_read_is_preserved_and_cannot_claim_complete(self):
        self.script_text('''import urllib.request
with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18919/v1/compile", data=b"fake")) as value:
    assert value.read(3) == b'{"f'
''')
        code, _, _ = self.invoke()
        self.assertEqual(code, 0)
        call = self.manifest()["calls"][0]
        self.assertFalse(call["complete"])
        self.assertEqual((self.output / call["response_file"]).read_bytes(), b'{"f')

    def test_parallel_duplicate_requests_keep_all_multiplicities(self):
        self.script_text('''import concurrent.futures, urllib.request
def one(_):
    with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18919/v1/compile", data=b"same request")) as value:
        return value.read()
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    assert len(list(pool.map(one, range(16)))) == 16
''')
        code, _, _ = self.invoke()
        self.assertEqual(code, 0)
        calls = self.manifest()["calls"]
        self.assertEqual([call["id"] for call in calls], list(range(1, 17)))
        self.assertEqual(len({call["request_file"] for call in calls}), 16)
        self.assertTrue(all(call["complete"] and (self.output / call["response_file"]).read_bytes() == self.body for call in calls))

    def test_transport_error_is_retained_without_exception_text(self):
        self.script_text('''import urllib.error, urllib.request
try:
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18919/v1/compile", data=b"fake"))
except urllib.error.URLError:
    pass
''')
        with self.patches():
            # The error is injected at the original transport. No socket opens.
            def failed(*args, **kwargs):
                import urllib.error
                raise urllib.error.URLError(ConnectionRefusedError(61, "fake sensitive error"))
            with mock.patch.object(urllib.request.AbstractHTTPHandler, "do_open", new=failed):
                code = recorder.run_checker(self.output, self.script, [])
        self.assertEqual(code, 0)
        call = self.manifest()["calls"][0]
        self.assertEqual(call["error"], {"type": "URLError", "errno": 61})
        self.assertFalse(call["complete"])
        self.assertIsNone(call["status"])
        self.assertNotIn("fake sensitive error", (self.output / "manifest.json").read_text())

    def test_network_boundaries_block_before_the_transport(self):
        self.script_text('''import urllib.request
for url in ("http://127.0.0.1:8765/v1/compile", "http://127.0.0.1:18765/v1/compile", "https://127.0.0.1:18919/v1/compile", "http://example.invalid:18919/v1/compile", "http://127.0.0.1:18919/other", "http://user:password@127.0.0.1:18919/v1/compile"):
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=b"fake"))
    except ValueError:
        pass
''')
        code, _, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.manifest()["recording_errors"], ["request_refused"] * 6)

    def test_original_arguments_file_name_path_and_exit_are_preserved(self):
        self.script_text('''import os, pathlib, sys
assert __name__ == "__main__"
assert pathlib.Path(__file__).name == "fake_checker.py"
assert sys.argv[1:] == ["--fake", "a b", "$literal"]
assert pathlib.Path(sys.path[0]).name == "repo"
assert os.environ.get("M6_INPUTS_KEYRING") is None
assert os.environ.get("GITHUB_TOKEN") is None
assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
raise SystemExit(7)
''')
        previous_path = list(__import__("sys").path)
        previous_environment = dict(os.environ)
        code, _, _ = self.invoke(["--fake", "a b", "$literal"])
        self.assertEqual(code, 7)
        self.assertEqual(self.manifest()["exit_code"], 7)
        self.assertEqual(self.manifest()["calls"], [])
        self.assertEqual(__import__("sys").path, previous_path)
        self.assertEqual(dict(os.environ), previous_environment)

    def test_guards_keep_existing_outputs_and_nonadmitted_script(self):
        self.script_text("raise SystemExit(0)\n")
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_bytes(b"keep")
        with self.patches(), self.assertRaises(recorder.RecordingError):
            recorder.run_checker(self.output, self.script, [])
        self.assertEqual(sentinel.read_bytes(), b"keep")
        self.output = self.repo / ".local/new-raw"
        with self.patches(), mock.patch.object(recorder, "ALLOWED_SCRIPTS", frozenset()), self.assertRaises(recorder.RecordingError):
            recorder.run_checker(self.output, self.script, [])
        self.assertFalse(self.output.exists())
        with self.patches(), mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), self.assertRaises(recorder.RecordingError):
            recorder.run_checker(self.output, self.script, [])

    def test_exception_keeps_completed_wire_evidence_and_restores_the_hook(self):
        self.script_text('''import urllib.request
with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18919/v1/compile", data=b"fake")) as value:
    assert value.read()
raise RuntimeError("synthetic checker failure")
''')
        original = urllib.request.AbstractHTTPHandler.do_open
        code, _, err = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError: synthetic checker failure", err)
        self.assertIs(urllib.request.AbstractHTTPHandler.do_open, original)
        self.assertEqual(self.manifest()["exit_code"], 1)
        self.assertTrue(self.manifest()["calls"][0]["complete"])

    def test_cli_forwards_original_arguments_without_a_shell(self):
        with mock.patch.object(recorder, "run_checker", return_value=7) as run:
            code = recorder.main(["--out", str(self.output), "--", str(self.script), "--fake", "$literal", "a b"])
        self.assertEqual(code, 7)
        run.assert_called_once_with(str(self.output), str(self.script), ["--fake", "$literal", "a b"])


if __name__ == "__main__":
    unittest.main()
