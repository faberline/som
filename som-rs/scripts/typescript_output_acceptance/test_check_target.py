#!/usr/bin/env python3
"""Unit tests for check_target.py using a fake loopback HTTP server (issue #98)."""
from __future__ import annotations

import contextlib
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
import unittest.mock
from typing import Any, Callable

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_target  # noqa: E402


def _build_fixture_tables() -> tuple[
    dict[str, tuple[int, bytes]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    scope = check_target.ts_profile.load_scope()
    neg_doc = json.loads((check_target.CONTRACT / "negative-cases.json").read_text(encoding="utf-8"))
    neg_by_body: dict[str, tuple[int, bytes]] = {}

    for case in neg_doc["cases"]:
        body = case["body"]
        exp = case["expected"]
        diag = dict(exp)
        reply = {
            "status": "error",
            "code": exp["code"],
            "detail": exp["cause"],
            "diagnostics": [diag],
        }
        neg_by_body[body] = (
            case["http_status"],
            check_target.compact_json(reply).encode("utf-8"),
        )

    cnl_to_prompt: dict[str, dict[str, Any]] = {}
    inv = json.loads(check_target.build_case_inventory.OUT.read_text(encoding="utf-8"))
    for pub in inv["public_examples"]:
        prompt_obj = json.loads((check_target.REPO / pub["prompt"]).read_text(encoding="utf-8"))
        if pub.get("cnl"):
            cnl_text = (check_target.REPO / pub["cnl"]).read_text(encoding="utf-8")
            cnl_to_prompt[cnl_text] = prompt_obj

    fixtures_doc = json.loads(check_target.build_case_inventory.FIXTURES.read_text(encoding="utf-8"))
    for fix in fixtures_doc["cases"]:
        cnl_to_prompt[fix["cnl"]] = fix["expected_prompt"]

    cov_doc = json.loads(check_target.build_case_inventory.COVERAGE.read_text(encoding="utf-8"))
    for cov in cov_doc["cases"]:
        prompt_obj = json.loads(
            (check_target.COVERAGE_CASES / cov["prompt"]).read_text(encoding="utf-8")
        )
        cnl_text = (check_target.COVERAGE_CASES / cov["cnl"]).read_text(encoding="utf-8")
        cnl_to_prompt[cnl_text] = prompt_obj

    target_examples_doc = json.loads(
        (check_target.CONTRACT / "target-examples.json").read_text(encoding="utf-8")
    )
    for nc in target_examples_doc["name_cases"]:
        if nc["form"] == "cnl":
            cnl_text = json.loads(nc["body"])["source"]
            cnl_to_prompt[cnl_text] = {
                "version": "som.prompt.v1",
                "package": nc["id"],
                "resource": {
                    "name": "Item",
                    "path": "/items",
                    "table": "items",
                    "fields": [],
                },
            }

    return neg_by_body, cnl_to_prompt, scope


NEG_BY_BODY, CNL_TO_PROMPT, SCOPE = _build_fixture_tables()


def compute_default_compile_response(body_str: str) -> tuple[int, bytes]:
    if body_str in NEG_BY_BODY:
        return NEG_BY_BODY[body_str]

    req = json.loads(body_str)
    form = "cnl" if req.get("format") == "som.cnl.v1" else "json"
    prompt_obj = req["prompt"] if "prompt" in req else CNL_TO_PROMPT[req["source"]]
    prompt_sha = check_target.sha_bytes(
        check_target.canonical_json(prompt_obj).encode("utf-8")
    )
    target = req.get("target")

    if target in (None, "python"):
        reply = {
            "status": "ok",
            "bundle": {
                "version": check_target.PY_BUNDLE_VERSION,
                "prompt_sha256": prompt_sha,
            },
        }
        return 200, check_target.compact_json(reply).encode("utf-8")

    found = check_target.ts_profile.check(prompt_obj, SCOPE)
    if found is None:
        reply = {
            "status": "ok",
            "bundle": {
                "version": check_target.TS_BUNDLE_VERSION,
                "target": "typescript",
                "prompt_sha256": prompt_sha,
            },
        }
        return 200, check_target.compact_json(reply).encode("utf-8")

    leaf = check_target.resolve_pointer(prompt_obj, found["pointer"])
    if form == "json":
        spans = check_target.rust_cases.Spans(body_str)
        pointer = "/prompt" + found["pointer"]
        loc = check_target.rust_cases.location(body_str, *spans.values[pointer])
    else:
        pointer = found["pointer"]
        src = req["source"]
        tok = (
            leaf.removeprefix("som.prompt.v")
            if found["rule"] == "prompt_version"
            else str(leaf)
        )
        idx = src.index(tok)
        loc = check_target.rust_cases.location(src, idx, idx + len(tok))

    diag = {
        "code": "UNSUPPORTED_TARGET",
        "prompt_pointer": pointer,
        "cause": found["cause"],
        "allowed_form": found["allowed_form"],
        **loc,
    }
    reply = {
        "status": "error",
        "code": "UNSUPPORTED_TARGET",
        "detail": found["cause"],
        "diagnostics": [diag],
    }
    return 422, check_target.compact_json(reply).encode("utf-8")


OverrideFn = Callable[[str, tuple[int, bytes]], tuple[int, bytes]]

_ACTIVE_SERVERS: dict[int, FakeSomServer] = {}
_ORIG_HTTP_CONNECT = http.client.HTTPConnection.connect


def _sandbox_aware_http_connect(self: http.client.HTTPConnection) -> None:
    try:
        _ORIG_HTTP_CONNECT(self)
    except PermissionError as exc:
        if exc.errno != errno.EPERM or self.host not in check_target.LOOPBACK_HOSTS:
            raise
        srv = _ACTIVE_SERVERS.get(int(self.port))
        if srv is None:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused") from exc
        cli_sock, srv_sock = socket.socketpair()
        self.sock = cli_sock
        srv.accept_socketpair(srv_sock)


http.client.HTTPConnection.connect = _sandbox_aware_http_connect  # type: ignore[method-assign]


class FakeSomServer:
    def __init__(self, override: OverrideFn | None = None) -> None:
        self.override = override
        self.request_count = 0
        self._lock = threading.Lock()
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                with parent._lock:
                    parent.request_count += 1
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length).decode("utf-8")
                default_resp = compute_default_compile_response(raw_body)
                status, payload = (
                    parent.override(raw_body, default_resp)
                    if parent.override is not None
                    else default_resp
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if self._httpd.server_port in check_target.FORBIDDEN_PORTS:
            self._httpd.server_close()
            self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def accept_socketpair(self, srv_sock: socket.socket) -> None:
        self._httpd.process_request(srv_sock, ("127.0.0.1", 0))

    @property
    def port(self) -> int:
        return int(self._httpd.server_port)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> FakeSomServer:
        _ACTIVE_SERVERS[self.port] = self
        self._thread.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        _ACTIVE_SERVERS.pop(self.port, None)
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


class CheckTargetTests(unittest.TestCase):
    def _run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = check_target.main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_pass_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeSomServer() as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, out, err = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 0, msg=f"stderr={err}\nstdout={out}")
            self.assertGreater(srv.request_count, 0)

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["failed_count"], 0)
            self.assertEqual(report["not_run_count"], 0)
            self.assertEqual(report["passed_count"], report["total"])
            self.assertIn("out_of_scope", report["legacy_in_scope"])

            sections = {row["section"] for row in report["cases"]}
            self.assertEqual(
                sections,
                {
                    "target_examples",
                    "name_cases",
                    "negative_cases",
                    "python_equivalence",
                    "in_scope",
                    "out_of_scope",
                },
            )
            for row in report["cases"]:
                for key in (
                    "id",
                    "section",
                    "request_sha256",
                    "http_status",
                    "response_sha256",
                    "expected",
                    "passed",
                    "reason",
                ):
                    self.assertIn(key, row)
                self.assertTrue(row["passed"], msg=row)
                self.assertEqual(row["reason"], "ok")
                resp_path = out_dir / row["response_file"]
                self.assertTrue(resp_path.is_file(), msg=row["id"])

    def test_wrong_status_wrong_code_and_wrong_span_each_fail(self) -> None:
        neg_doc = json.loads(
            (check_target.CONTRACT / "negative-cases.json").read_text(encoding="utf-8")
        )
        tt01 = neg_doc["cases"][0]
        tt01_id = tt01["id"]
        tt01_body = tt01["body"]

        def wrong_status(body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if body == tt01_body:
                return 500, default[1]
            return default

        def wrong_code(body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if body == tt01_body:
                payload = json.loads(default[1])
                payload["code"] = "INVALID_PROMPT"
                payload["diagnostics"][0]["code"] = "INVALID_PROMPT"
                return default[0], check_target.compact_json(payload).encode("utf-8")
            return default

        def wrong_span(body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if body == tt01_body:
                payload = json.loads(default[1])
                payload["diagnostics"][0]["byte_end"] += 1
                payload["diagnostics"][0]["end_column"] += 1
                return default[0], check_target.compact_json(payload).encode("utf-8")
            return default

        for label, override_fn, expected_substr in (
            ("wrong_status", wrong_status, "http_status 500 != expected 400"),
            ("wrong_code", wrong_code, "INVALID_PROMPT"),
            ("wrong_span", wrong_span, "span"),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp, FakeSomServer(
                override=override_fn
            ) as srv:
                out_dir = pathlib.Path(tmp) / "out"
                rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
                self.assertEqual(rc, 1)
                report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
                self.assertFalse(report["passed"])
                tt01_rows = [r for r in report["cases"] if r["id"] == tt01_id]
                self.assertEqual(len(tt01_rows), 1)
                self.assertFalse(tt01_rows[0]["passed"])
                self.assertIn(expected_substr, tt01_rows[0]["reason"])

    def test_python_not_equal_to_no_target_bytes_fails(self) -> None:
        def tamper_python(body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            try:
                req = json.loads(body)
            except Exception:  # noqa: BLE001
                return default
            if req.get("target") == "python":
                return default[0], default[1] + b"\n"
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(override=tamper_python) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            failed_py = [
                r
                for r in report["cases"]
                if r["section"] == "python_equivalence" and not r["passed"]
            ]
            self.assertGreater(len(failed_py), 0)
            self.assertIn(
                "target python response bytes differ from response without target",
                failed_py[0]["reason"],
            )

    def test_unexpected_unsupported_target_fails(self) -> None:
        def unexpected_refusal(body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            try:
                req = json.loads(body)
            except Exception:  # noqa: BLE001
                return default
            if (
                req.get("target") == "typescript"
                and isinstance(req.get("prompt"), dict)
                and req["prompt"].get("package") == "items"
            ):
                reply = {
                    "status": "error",
                    "code": "UNSUPPORTED_TARGET",
                    "detail": "synthetic refusal on in-scope prompt",
                    "diagnostics": [
                        {
                            "code": "UNSUPPORTED_TARGET",
                            "prompt_pointer": "/prompt/version",
                            "cause": "synthetic refusal on in-scope prompt",
                            "allowed_form": "som.prompt.v1 .. som.prompt.v5",
                            "line": 1,
                            "column": 1,
                            "end_line": 1,
                            "end_column": 2,
                            "byte_start": 0,
                            "byte_end": 1,
                        }
                    ],
                }
                return 422, check_target.compact_json(reply).encode("utf-8")
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(
            override=unexpected_refusal
        ) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            failed_in_scope = [
                r
                for r in report["cases"]
                if r["section"] == "in_scope" and not r["passed"]
            ]
            self.assertGreater(len(failed_in_scope), 0)
            self.assertIn("UNSUPPORTED_TARGET", failed_in_scope[0]["reason"])

    def test_connection_error_gives_not_run_and_exit_1(self) -> None:
        with FakeSomServer() as srv:
            closed_url = srv.url

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", closed_url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["not_run_count"], report["total"])
            self.assertGreater(report["not_run_count"], 0)
            for row in report["cases"]:
                self.assertEqual(row["status"], "not_run")
                self.assertFalse(row["passed"])
                self.assertIsNone(row["http_status"])
                self.assertIn("connection error", row["reason"])

    def test_sha256_mismatch_fails_case(self) -> None:
        real_sha = check_target.build_case_inventory.sha
        target_rel = "som-rs/contracts/compiler-v1/examples/v1.cnl"

        def tampered_sha(path: pathlib.Path) -> str:
            if check_target.build_case_inventory.rel(path) == target_rel:
                return "0" * 64
            return real_sha(path)

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer() as srv, unittest.mock.patch.object(
            check_target.build_case_inventory, "sha", side_effect=tampered_sha
        ):
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            mismatch_rows = [
                r for r in report["cases"] if "sha256 mismatch" in r["reason"]
            ]
            self.assertGreater(len(mismatch_rows), 0)

    def test_refused_ports_non_loopback_and_existing_out_exit_2_before_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeSomServer() as srv:
            base = pathlib.Path(tmp)

            for idx, bad_url in enumerate(
                (
                    "http://127.0.0.1:8765",
                    "http://localhost:18765",
                    "http://192.0.2.1:19000",
                    "http://example.com:19000",
                )
            ):
                out_dir = base / f"refused-{idx}"
                rc, _, err = self._run_main(["--url", bad_url, "--out", str(out_dir)])
                self.assertEqual(rc, 2, msg=bad_url)
                self.assertIn("refused:", err)
                self.assertFalse(out_dir.exists())

            existing_out = base / "already_exists"
            existing_out.mkdir()
            rc, _, err = self._run_main(["--url", srv.url, "--out", str(existing_out)])
            self.assertEqual(rc, 2)
            self.assertIn("refused:", err)
            self.assertEqual(srv.request_count, 0)


if __name__ == "__main__":
    unittest.main()
