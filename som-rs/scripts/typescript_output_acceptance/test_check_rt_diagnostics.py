#!/usr/bin/env python3
"""Unit tests for check_rt_diagnostics.py (#105)."""
from __future__ import annotations

import contextlib
import copy
import errno
import http.client
import http.server
import io
import json
import pathlib
import socket
import sys
import tempfile
import threading
import unittest
from typing import Any, Callable

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_rt_diagnostics as crtd  # noqa: E402


_ACTIVE_SERVERS: dict[int, FakeRTServer] = {}


def _find_true_http_connect() -> Any:
    current = http.client.HTTPConnection.connect
    for mod in list(sys.modules.values()):
        orig = getattr(mod, "_ORIG_HTTP_CONNECT", None)
        if orig is not None and getattr(orig, "__name__", "") == "connect":
            return orig
    return current


_ORIG_HTTP_CONNECT = _find_true_http_connect()


def _lookup_active_server(port: int) -> Any:
    if port in _ACTIVE_SERVERS:
        return _ACTIVE_SERVERS[port]
    for mod in list(sys.modules.values()):
        table = getattr(mod, "_ACTIVE_SERVERS", None)
        if isinstance(table, dict) and port in table:
            return table[port]
    return None


def _sandbox_aware_http_connect(self: http.client.HTTPConnection) -> None:
    try:
        _ORIG_HTTP_CONNECT(self)
    except PermissionError as exc:
        if exc.errno != errno.EPERM or self.host not in crtd.LOOPBACK_HOSTS:
            raise
        srv = _lookup_active_server(int(self.port))
        if srv is None:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused") from exc
        cli_sock, srv_sock = socket.socketpair()
        self.sock = cli_sock
        if hasattr(srv, "accept_socketpair"):
            srv.accept_socketpair(srv_sock)
        else:
            srv.process_request(srv_sock, ("127.0.0.1", 0))
            srv_sock.close()


http.client.HTTPConnection.connect = _sandbox_aware_http_connect  # type: ignore[method-assign]


RT_CASES, _, _ = crtd.load_rt_cases()
RT_BY_BODY_BYTES: dict[bytes, dict[str, Any]] = {
    case["body"].encode("utf-8"): case for case in RT_CASES
}

MutateFn = Callable[[dict[str, Any], int, dict[str, Any]], tuple[int, dict[str, Any]]]


class FakeRTServer:
    """Fake loopback server returning expected RT01-RT07 diagnostics."""

    def __init__(self, mutate: MutateFn | None = None) -> None:
        self.mutate = mutate
        self.received_bodies: list[bytes] = []
        self.received_content_types: list[str] = []
        self.request_count = 0
        self._lock = threading.Lock()
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length)
                ctype = self.headers.get("Content-Type", "")
                with parent._lock:
                    parent.request_count += 1
                    parent.received_bodies.append(raw_body)
                    parent.received_content_types.append(ctype)

                case = RT_BY_BODY_BYTES.get(raw_body)
                if case is None:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b'{"status":"error","code":"UNKNOWN_TEST_BODY"}')
                    return

                http_status = int(case["http_status"])
                diag = copy.deepcopy(case["expected"])
                reply: dict[str, Any] = {
                    "status": "error",
                    "code": diag["code"],
                    "detail": diag["cause"],
                    "diagnostics": [diag],
                }
                if parent.mutate is not None:
                    http_status, reply = parent.mutate(case, http_status, reply)

                payload = json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(http_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if self._httpd.server_port in crtd.FORBIDDEN_PORTS:
            self._httpd.server_close()
            self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_port)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def accept_socketpair(self, srv_sock: socket.socket) -> None:
        self._httpd.process_request(srv_sock, ("127.0.0.1", 0))

    def __enter__(self) -> FakeRTServer:
        _ACTIVE_SERVERS[self.port] = self
        self._thread.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        _ACTIVE_SERVERS.pop(self.port, None)
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2.0)


class CheckRTDiagnosticsTests(unittest.TestCase):
    def _run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = crtd.main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_pass_path_and_received_body_bytes_equal_case_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeRTServer() as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, out, err = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 0, msg=f"stderr={err}\nstdout={out}")

            expected_bodies = [case["body"].encode("utf-8") for case in RT_CASES]
            self.assertEqual(len(expected_bodies), 7)
            self.assertEqual(srv.received_bodies, expected_bodies)
            self.assertTrue(all(ct == "application/json" for ct in srv.received_content_types))

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["total"], 7)
            self.assertEqual(report["passed_count"], 7)
            self.assertEqual(report["failed_count"], 0)
            self.assertEqual(report["not_run_count"], 0)
            self.assertEqual(len(report["rows"]), 7)
            for row, case in zip(report["rows"], RT_CASES):
                self.assertEqual(row["id"], case["id"])
                self.assertEqual(row["request_sha256"], crtd.sha_bytes(case["body"].encode("utf-8")))
                self.assertIsNotNone(row["response_sha256"])
                self.assertTrue(row["passed"])
                self.assertEqual(row["status"], "passed")
                self.assertEqual(row["reason"], "ok")
                self.assertTrue((out_dir / row["response_file"]).is_file())

    def test_wrong_status_wrong_code_wrong_cause_and_wrong_span_each_fail(self) -> None:
        mutations: list[tuple[str, MutateFn, str]] = [
            (
                "wrong_status",
                lambda case, st, rep: (422 if case["id"].startswith("RT01") else st, rep),
                "http_status 422 != expected 400",
            ),
            (
                "wrong_code",
                lambda case, st, rep: (
                    st,
                    {
                        **rep,
                        "code": "INVALID_PROMPT",
                        "diagnostics": [{**rep["diagnostics"][0], "code": "INVALID_PROMPT"}],
                    }
                    if case["id"].startswith("RT02")
                    else rep,
                ),
                "INVALID_PROMPT",
            ),
            (
                "wrong_cause",
                lambda case, st, rep: (
                    st,
                    {
                        **rep,
                        "detail": "Wrong cause string.",
                        "diagnostics": [{**rep["diagnostics"][0], "cause": "Wrong cause string."}],
                    }
                    if case["id"].startswith("RT03")
                    else rep,
                ),
                "Wrong cause string.",
            ),
            (
                "wrong_span",
                lambda case, st, rep: (
                    st,
                    {
                        **rep,
                        "diagnostics": [
                            {
                                **rep["diagnostics"][0],
                                "byte_start": 0,
                                "byte_end": 8,
                                "column": 1,
                                "end_column": 9,
                            }
                        ],
                    }
                    if case["id"].startswith("RT04")
                    else rep,
                ),
                "span",
            ),
        ]

        for label, mutate_fn, expected_substr in mutations:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp, FakeRTServer(mutate=mutate_fn) as srv:
                out_dir = pathlib.Path(tmp) / "out"
                rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
                self.assertEqual(rc, 1)
                report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
                self.assertFalse(report["passed"])
                self.assertEqual(report["failed_count"], 1)
                failed_rows = [r for r in report["rows"] if not r["passed"]]
                self.assertEqual(len(failed_rows), 1)
                self.assertIn(expected_substr, failed_rows[0]["reason"])

    def test_connection_error_is_not_run_with_exit_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with FakeRTServer() as srv:
                closed_url = srv.url
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", closed_url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["status"], "not_run")
            self.assertEqual(report["not_run_count"], 7)
            self.assertTrue(all(r["status"] == "not_run" and not r["passed"] for r in report["rows"]))

    def test_refused_ports_non_loopback_and_existing_out_exit_2_before_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeRTServer() as srv:
            existing_out = pathlib.Path(tmp) / "existing"
            existing_out.mkdir()

            bad_Cases = [
                ["--url", "http://127.0.0.1:8765", "--out", str(pathlib.Path(tmp) / "o1")],
                ["--url", "http://127.0.0.1:18765", "--out", str(pathlib.Path(tmp) / "o2")],
                ["--url", "http://10.0.0.1:9000", "--out", str(pathlib.Path(tmp) / "o3")],
                ["--url", "http://example.com:9000", "--out", str(pathlib.Path(tmp) / "o4")],
                ["--url", srv.url, "--out", str(existing_out)],
            ]
            for argv in bad_Cases:
                with self.subTest(argv=argv):
                    rc, _, err = self._run_main(argv)
                    self.assertEqual(rc, 2)
                    self.assertIn("refused:", err)

            self.assertEqual(srv.request_count, 0)


if __name__ == "__main__":
    unittest.main()
