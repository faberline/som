#!/usr/bin/env python3
"""Unit tests for check_python_baseline.py using two fake loopback HTTP servers (issue #103)."""
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
from typing import Any, Callable
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_python_baseline  # noqa: E402

_ACTIVE_SERVERS: dict[int, http.server.ThreadingHTTPServer] = {}


def _find_true_http_connect() -> Callable[[http.client.HTTPConnection], None]:
    conn_fn = http.client.HTTPConnection.connect
    for mod in list(sys.modules.values()):
        orig = getattr(mod, "_ORIG_HTTP_CONNECT", None)
        if callable(orig) and getattr(orig, "__name__", "") == "connect":
            return orig
    return conn_fn


_ORIG_HTTP_CONNECT = _find_true_http_connect()


def _sandbox_aware_http_connect(self: http.client.HTTPConnection) -> None:
    try:
        _ORIG_HTTP_CONNECT(self)
    except PermissionError as exc:
        if exc.errno != errno.EPERM:
            raise
        port = int(self.port)
        server = _ACTIVE_SERVERS.get(port)
        if server is not None:
            client_sock, server_sock = socket.socketpair()
            if self.timeout is not None:
                client_sock.settimeout(self.timeout)
            self.sock = client_sock
            threading.Thread(
                target=server.process_request,
                args=(server_sock, ("127.0.0.1", 0)),
                daemon=True,
            ).start()
            return
        for mod in list(sys.modules.values()):
            active = getattr(mod, "_ACTIVE_SERVERS", None)
            if isinstance(active, dict) and active is not _ACTIVE_SERVERS:
                other_srv = active.get(port)
                if other_srv is not None and hasattr(other_srv, "accept_socketpair"):
                    client_sock, server_sock = socket.socketpair()
                    if self.timeout is not None:
                        client_sock.settimeout(self.timeout)
                    self.sock = client_sock
                    other_srv.accept_socketpair(server_sock)
                    return
        raise


http.client.HTTPConnection.connect = _sandbox_aware_http_connect  # type: ignore[method-assign]


class FakeCompilerServer:
    """Fake loopback HTTP server handling POST /v1/compile and POST /v1/assemble."""

    def __init__(
        self,
        *,
        compile_override: Callable[[dict[str, Any], bytes], tuple[int, bytes] | None] | None = None,
        assemble_override: Callable[[dict[str, Any], bytes], tuple[int, bytes] | None] | None = None,
    ) -> None:
        self.compile_override = compile_override
        self.assemble_override = assemble_override
        self.requests: list[tuple[str, bytes]] = []
        self._lock = threading.Lock()

        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length)
                with outer._lock:
                    outer.requests.append((self.path, raw_body))

                status, payload = outer._dispatch(self.path, raw_body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_port
        while self.port in check_python_baseline.FORBIDDEN_PORTS:
            self._httpd.server_close()
            self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self.port = self._httpd.server_port

        self.base_url = f"http://127.0.0.1:{self.port}"
        _ACTIVE_SERVERS[self.port] = self._httpd
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def _dispatch(self, path: str, raw_body: bytes) -> tuple[int, bytes]:
        req = json.loads(raw_body.decode("utf-8"))
        if path == "/v1/compile":
            if self.compile_override is not None:
                overridden = self.compile_override(req, raw_body)
                if overridden is not None:
                    return overridden
            prompt = req.get("prompt", {})
            pkg = prompt.get("package", "unknown_pkg")
            prompt_sha = check_python_baseline.sha_bytes(
                check_python_baseline.compact_json_bytes(prompt)
            )
            bundle = {
                "version": "som.dsl-bundle.v1",
                "prompt": prompt,
                "dsl": {
                    "version": "som.dsl.v1",
                    "files": [
                        {
                            "path": f"{pkg}/main.py",
                            "blocks": [{"id": "app.main", "name": "main", "params": {}, "refs": []}],
                        }
                    ],
                },
                "assets": {"backend": {"sha256": "0" * 64}},
                "hashes": {
                    "prompt_sha256": prompt_sha,
                    "dsl_sha256": "1" * 64,
                    "payload_sha256": "2" * 64,
                },
            }
            return 200, check_python_baseline.compact_json_bytes({"status": "ok", "bundle": bundle})

        if path == "/v1/assemble":
            if self.assemble_override is not None:
                overridden = self.assemble_override(req, raw_body)
                if overridden is not None:
                    return overridden
            bundle = req.get("bundle", {})
            pkg = bundle.get("prompt", {}).get("package", "unknown_pkg")
            files = {
                "README.md": f"# {pkg}\n",
                f"{pkg}/main.py": f"# generated for {pkg}\n",
            }
            return 200, check_python_baseline.compact_json_bytes({"status": "ok", "files": files})

        return 404, b'{"status":"error","code":"NOT_FOUND"}'

    def close(self) -> None:
        _ACTIVE_SERVERS.pop(self.port, None)
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def make_prompt(pkg: str) -> dict[str, Any]:
    return {
        "version": "som.prompt.v1",
        "package": pkg,
        "resource": {
            "name": "Item",
            "table": "items",
            "path": "/items",
            "fields": [{"name": "title", "type": "str", "required": True, "unique": True}],
        },
        "migration": {"revision": "items_0001"},
    }


def create_fake_runs(
    root: pathlib.Path,
    *,
    public_ids: tuple[str, ...] = ("PUB_CASE_1", "PUB_CASE_2"),
    legacy_ids: tuple[str, ...] = ("leg-0000", "leg-0001"),
) -> tuple[pathlib.Path, pathlib.Path]:
    """Create tiny fake public-products and legacy544 runs matching python_replay.py layout."""
    public_run = root / "public-run"
    legacy_run = root / "legacy-run"
    public_run.mkdir(parents=True)
    legacy_run.mkdir(parents=True)

    public_records: list[dict[str, Any]] = []
    for index, case_id in enumerate(public_ids):
        case_dir = public_run / f"{index:03d}"
        case_dir.mkdir(parents=True)
        prompt = make_prompt(f"pub_{index}_{case_id.lower()}")
        req_body = {"format": "som.prompt-json.v1", "prompt": prompt}
        (case_dir / "compile-json.request.json").write_text(
            json.dumps(req_body, indent=2) + "\n",
            encoding="utf-8",
        )
        (case_dir / "compile-json.request.raw").write_bytes(
            check_python_baseline.compact_json_bytes(req_body)
        )
        (case_dir / "result.json").write_text(
            json.dumps({"id": case_id, "passed": True}) + "\n",
            encoding="utf-8",
        )
        public_records.append({"id": case_id, "passed": True})

    (public_run / "result.json").write_text(
        json.dumps(
            {
                "version": "som.compiler-public-result.v1",
                "required": len(public_records),
                "passed": len(public_records),
                "complete": True,
                "records": public_records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    legacy_records: list[dict[str, Any]] = []
    compile_root = legacy_run / "compile"
    compile_root.mkdir(parents=True)
    for index, job_id in enumerate(legacy_ids):
        prompt = make_prompt(f"leg_{index}_{job_id.replace('-', '_')}")
        prompt_sha = check_python_baseline.sha_bytes(
            check_python_baseline.compact_json_bytes(prompt)
        )
        comp_dir = compile_root / prompt_sha
        comp_dir.mkdir(parents=True, exist_ok=True)
        req_body = {"format": "som.prompt-json.v1", "prompt": prompt}
        (comp_dir / "compile-json.request.json").write_text(
            json.dumps(req_body, indent=2) + "\n",
            encoding="utf-8",
        )
        job_dir = legacy_run / f"{index:04d}"
        job_dir.mkdir(parents=True)
        legacy_records.append(
            {
                "id": job_id,
                "family": "legacy214",
                "group": "services14",
                "passed": True,
                "prompt_sha256": prompt_sha,
            }
        )

    (legacy_run / "result.json").write_text(
        json.dumps(
            {
                "version": "som.compiler-legacy544-result.v1",
                "status": "passed",
                "required_job_count": len(legacy_records),
                "passed_job_count": len(legacy_records),
                "compile_prompt_count": len(legacy_records),
                "records": legacy_records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return public_run, legacy_run


class CheckPythonBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.public_run, self.legacy_run = create_fake_runs(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_main(
        self,
        qa_url: str,
        baseline_url: str,
        out_dir: pathlib.Path,
        *,
        public_run: pathlib.Path | None = None,
        legacy_run: pathlib.Path | None = None,
        expected_public: int | None = 2,
        expected_legacy: int | None = 2,
        patch_defaults: bool = True,
    ) -> tuple[int, str]:
        pub = public_run or self.public_run
        leg = legacy_run or self.legacy_run
        argv = [
            "--qa",
            qa_url,
            "--baseline",
            baseline_url,
            "--out",
            str(out_dir),
            "--public-run",
            str(pub),
            "--legacy-run",
            str(leg),
            "--workers",
            "2",
        ]
        stderr_buf = io.StringIO()
        if patch_defaults:
            with (
                mock.patch.object(
                    check_python_baseline,
                    "EXPECTED_PUBLIC",
                    2 if expected_public is None else expected_public,
                ),
                mock.patch.object(
                    check_python_baseline,
                    "EXPECTED_LEGACY",
                    2 if expected_legacy is None else expected_legacy,
                ),
                contextlib.redirect_stderr(stderr_buf),
            ):
                rc = check_python_baseline.main(argv)
        else:
            with contextlib.redirect_stderr(stderr_buf):
                rc = check_python_baseline.main(argv)
        return rc, stderr_buf.getvalue()

    def test_pass_path(self) -> None:
        qa_srv = FakeCompilerServer()
        base_srv = FakeCompilerServer()
        try:
            out_dir = self.tmp / "out-pass"
            rc, stderr = self._run_main(qa_srv.base_url, base_srv.base_url, out_dir)
            self.assertEqual(rc, 0, msg=stderr)

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["row_count_matches"])
            self.assertEqual(report["expected_rows"], 8)
            self.assertEqual(report["total_rows"], 8)
            self.assertEqual(report["passed_rows"], 8)
            self.assertEqual(report["failed_rows"], 0)
            self.assertEqual(report["not_run_rows"], 0)
            self.assertEqual(
                report["public_result_sha256"],
                check_python_baseline.sha_file(self.public_run / "result.json"),
            )
            self.assertEqual(
                report["legacy_result_sha256"],
                check_python_baseline.sha_file(self.legacy_run / "result.json"),
            )

            for row in report["rows"]:
                self.assertTrue(row["passed"])
                self.assertEqual(row["status"], "passed")
                self.assertIsNone(row["reason"])
                self.assertIn(row["source"], ("public", "legacy"))
                self.assertIn(row["variant"], ("none", "python"))
                self.assertIsNotNone(row["qa_compile_request_sha256"])
                self.assertEqual(
                    row["qa_compile_request_sha256"],
                    row["baseline_compile_request_sha256"],
                )
                self.assertIsNotNone(row["qa_compile_response_sha256"])
                self.assertEqual(
                    row["qa_compile_response_sha256"],
                    row["baseline_compile_response_sha256"],
                )
                self.assertIsNotNone(row["qa_assemble_request_sha256"])
                self.assertEqual(
                    row["qa_assemble_request_sha256"],
                    row["baseline_assemble_request_sha256"],
                )
                self.assertIsNotNone(row["qa_assemble_response_sha256"])
                self.assertEqual(
                    row["qa_assemble_response_sha256"],
                    row["baseline_assemble_response_sha256"],
                )
                self.assertNotIn("saved_responses", row)

            responses_dir = out_dir / "responses"
            self.assertEqual(list(responses_dir.glob("*")), [])
        finally:
            qa_srv.close()
            base_srv.close()

    def test_compile_byte_diff_fails(self) -> None:
        def tamper_compile(req: dict[str, Any], _raw: bytes) -> tuple[int, bytes] | None:
            pkg = req.get("prompt", {}).get("package", "")
            if pkg.startswith("pub_0_"):
                prompt = req["prompt"]
                prompt_sha = check_python_baseline.sha_bytes(
                    check_python_baseline.compact_json_bytes(prompt)
                )
                bundle = {
                    "version": "som.dsl-bundle.v1",
                    "prompt": prompt,
                    "dsl": {
                        "version": "som.dsl.v1",
                        "files": [
                            {
                                "path": f"{pkg}/main.py",
                                "blocks": [{"id": "app.main", "name": "main", "params": {}, "refs": []}],
                            }
                        ],
                    },
                    "assets": {"backend": {"sha256": "1" + "0" * 63}},
                    "hashes": {
                        "prompt_sha256": prompt_sha,
                        "dsl_sha256": "1" * 64,
                        "payload_sha256": "2" * 64,
                    },
                }
                return 200, check_python_baseline.compact_json_bytes({"status": "ok", "bundle": bundle})
            return None

        qa_srv = FakeCompilerServer(compile_override=tamper_compile)
        base_srv = FakeCompilerServer()
        try:
            out_dir = self.tmp / "out-compile-diff"
            rc, _ = self._run_main(qa_srv.base_url, base_srv.base_url, out_dir)
            self.assertEqual(rc, 1)

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["failed_rows"], 2)
            self.assertEqual(report["passed_rows"], 6)

            failed = [r for r in report["rows"] if not r["passed"]]
            self.assertEqual(len(failed), 2)
            for row in failed:
                self.assertEqual(row["id"], "PUB_CASE_1")
                self.assertEqual(row["status"], "failed")
                self.assertIn("compile response bytes differ", row["reason"])
                self.assertNotEqual(
                    row["qa_compile_response_sha256"],
                    row["baseline_compile_response_sha256"],
                )
                self.assertIn("saved_responses", row)
                for rel_path in row["saved_responses"].values():
                    self.assertTrue((out_dir / rel_path).is_file())

            passed = [r for r in report["rows"] if r["passed"]]
            for row in passed:
                self.assertNotIn("saved_responses", row)
        finally:
            qa_srv.close()
            base_srv.close()

    def test_status_difference_fails(self) -> None:
        def tamper_status(req: dict[str, Any], _raw: bytes) -> tuple[int, bytes] | None:
            pkg = req.get("prompt", {}).get("package", "")
            if pkg.startswith("pub_0_"):
                return 422, b'{"status":"error","code":"UNSUPPORTED_TARGET"}'
            return None

        qa_srv = FakeCompilerServer(compile_override=tamper_status)
        base_srv = FakeCompilerServer()
        try:
            out_dir = self.tmp / "out-status-diff"
            rc, _ = self._run_main(qa_srv.base_url, base_srv.base_url, out_dir)
            self.assertEqual(rc, 1)

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["failed_rows"], 2)
            self.assertEqual(report["passed_rows"], 6)

            failed = [r for r in report["rows"] if not r["passed"]]
            self.assertEqual(len(failed), 2)
            for row in failed:
                self.assertEqual(row["id"], "PUB_CASE_1")
                self.assertEqual(row["status"], "failed")
                self.assertEqual(row["qa_compile_status"], 422)
                self.assertEqual(row["baseline_compile_status"], 200)
                self.assertIn("compile HTTP status differs", row["reason"])
                self.assertIn("saved_responses", row)
        finally:
            qa_srv.close()
            base_srv.close()

    def test_assemble_byte_diff_fails(self) -> None:
        def tamper_assemble(req: dict[str, Any], _raw: bytes) -> tuple[int, bytes] | None:
            pkg = req.get("bundle", {}).get("prompt", {}).get("package", "")
            if pkg.startswith("leg_1_"):
                files = {"README.md": "# different bytes on qa\n"}
                return 200, check_python_baseline.compact_json_bytes({"status": "ok", "files": files})
            return None

        qa_srv = FakeCompilerServer(assemble_override=tamper_assemble)
        base_srv = FakeCompilerServer()
        try:
            out_dir = self.tmp / "out-assemble-diff"
            rc, _ = self._run_main(qa_srv.base_url, base_srv.base_url, out_dir)
            self.assertEqual(rc, 1)

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["failed_rows"], 2)
            self.assertEqual(report["passed_rows"], 6)

            failed = [r for r in report["rows"] if not r["passed"]]
            self.assertEqual(len(failed), 2)
            for row in failed:
                self.assertEqual(row["id"], "leg-0001")
                self.assertEqual(row["status"], "failed")
                self.assertIn("assemble response bytes differ", row["reason"])
                self.assertEqual(
                    row["qa_compile_response_sha256"],
                    row["baseline_compile_response_sha256"],
                )
                self.assertNotEqual(
                    row["qa_assemble_response_sha256"],
                    row["baseline_assemble_response_sha256"],
                )
                self.assertIn("saved_responses", row)
                self.assertTrue((out_dir / row["saved_responses"]["qa.assemble.raw"]).is_file())
                self.assertTrue((out_dir / row["saved_responses"]["baseline.assemble.raw"]).is_file())
        finally:
            qa_srv.close()
            base_srv.close()

    def test_diff_only_on_single_variant_fails(self) -> None:
        def tamper_python_variant_only(req: dict[str, Any], _raw: bytes) -> tuple[int, bytes] | None:
            pkg = req.get("prompt", {}).get("package", "")
            if pkg.startswith("pub_1_") and req.get("target") == "python":
                return 200, b'{"status":"ok","bundle":{"tampered":true}}'
            return None

        qa_srv = FakeCompilerServer(compile_override=tamper_python_variant_only)
        base_srv = FakeCompilerServer()
        try:
            out_dir = self.tmp / "out-variant-python-diff"
            rc, _ = self._run_main(qa_srv.base_url, base_srv.base_url, out_dir)
            self.assertEqual(rc, 1)

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["failed_rows"], 1)
            self.assertEqual(report["passed_rows"], 7)

            by_key = {(r["id"], r["variant"]): r for r in report["rows"]}
            self.assertTrue(by_key[("PUB_CASE_2", "none")]["passed"])
            self.assertEqual(by_key[("PUB_CASE_2", "none")]["status"], "passed")
            self.assertFalse(by_key[("PUB_CASE_2", "python")]["passed"])
            self.assertEqual(by_key[("PUB_CASE_2", "python")]["status"], "failed")
        finally:
            qa_srv.close()
            base_srv.close()

        def tamper_none_variant_only(req: dict[str, Any], _raw: bytes) -> tuple[int, bytes] | None:
            pkg = req.get("prompt", {}).get("package", "")
            if pkg.startswith("leg_0_") and "target" not in req:
                return 200, b'{"status":"ok","bundle":{"tampered_none":true}}'
            return None

        qa_srv2 = FakeCompilerServer(compile_override=tamper_none_variant_only)
        base_srv2 = FakeCompilerServer()
        try:
            out_dir2 = self.tmp / "out-variant-none-diff"
            rc2, _ = self._run_main(qa_srv2.base_url, base_srv2.base_url, out_dir2)
            self.assertEqual(rc2, 1)

            report2 = json.loads((out_dir2 / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report2["passed"])
            self.assertEqual(report2["failed_rows"], 1)
            self.assertEqual(report2["passed_rows"], 7)

            by_key2 = {(r["id"], r["variant"]): r for r in report2["rows"]}
            self.assertFalse(by_key2[("leg-0000", "none")]["passed"])
            self.assertEqual(by_key2[("leg-0000", "none")]["status"], "failed")
            self.assertTrue(by_key2[("leg-0000", "python")]["passed"])
            self.assertEqual(by_key2[("leg-0000", "python")]["status"], "passed")
        finally:
            qa_srv2.close()
            base_srv2.close()

    def test_recorded_request_already_containing_target_fails(self) -> None:
        req_file = self.public_run / "000" / "compile-json.request.json"
        body = json.loads(req_file.read_text(encoding="utf-8"))
        body["target"] = "python"
        req_file.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

        qa_srv = FakeCompilerServer()
        base_srv = FakeCompilerServer()
        try:
            out_dir = self.tmp / "out-has-target"
            rc, _ = self._run_main(qa_srv.base_url, base_srv.base_url, out_dir)
            self.assertEqual(rc, 1)

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["failed_rows"], 2)

            pub1_rows = [r for r in report["rows"] if r["id"] == "PUB_CASE_1"]
            self.assertEqual(len(pub1_rows), 2)
            for row in pub1_rows:
                self.assertFalse(row["passed"])
                self.assertEqual(row["status"], "failed")
                self.assertIn("target", row["reason"])
        finally:
            qa_srv.close()
            base_srv.close()

    def test_missing_recorded_request_marks_row_not_run_and_exits_1(self) -> None:
        req_file = self.public_run / "001" / "compile-json.request.json"
        req_file.unlink()
        raw_file = self.public_run / "001" / "compile-json.request.raw"
        if raw_file.exists():
            raw_file.unlink()

        qa_srv = FakeCompilerServer()
        base_srv = FakeCompilerServer()
        try:
            out_dir = self.tmp / "out-missing-request"
            rc, _ = self._run_main(qa_srv.base_url, base_srv.base_url, out_dir)
            self.assertEqual(rc, 1)

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["not_run_rows"], 2)
            self.assertEqual(report["passed_rows"], 6)

            pub2_rows = [r for r in report["rows"] if r["id"] == "PUB_CASE_2"]
            self.assertEqual(len(pub2_rows), 2)
            for row in pub2_rows:
                self.assertFalse(row["passed"])
                self.assertEqual(row["status"], "not_run")
                self.assertTrue(row["reason"])
        finally:
            qa_srv.close()
            base_srv.close()

    def test_short_row_count_fails(self) -> None:
        qa_srv = FakeCompilerServer()
        base_srv = FakeCompilerServer()
        try:
            # 1. Unpatched default expects (127 + 544) * 2 = 1342 rows; tiny fake run has 8 rows.
            out_default = self.tmp / "out-short-default"
            rc1, _ = self._run_main(
                qa_srv.base_url,
                base_srv.base_url,
                out_default,
                patch_defaults=False,
            )
            self.assertEqual(rc1, 1)
            report1 = json.loads((out_default / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report1["passed"])
            self.assertFalse(report1["row_count_matches"])
            self.assertEqual(report1["expected_rows"], (127 + 544) * 2)
            self.assertEqual(report1["total_rows"], 8)
            self.assertTrue(report1["problems"])

            # 2. Patched expected count (3 public + 2 legacy = 10 rows) when only 2 public exist (8 rows).
            out_patched = self.tmp / "out-short-patched"
            rc2, _ = self._run_main(
                qa_srv.base_url,
                base_srv.base_url,
                out_patched,
                expected_public=3,
                expected_legacy=2,
                patch_defaults=True,
            )
            self.assertEqual(rc2, 1)
            report2 = json.loads((out_patched / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report2["passed"])
            self.assertFalse(report2["row_count_matches"])
            self.assertEqual(report2["expected_rows"], 10)
            self.assertEqual(report2["total_rows"], 8)
        finally:
            qa_srv.close()
            base_srv.close()

    def test_refusals_exit_2_before_any_request(self) -> None:
        qa_srv = FakeCompilerServer()
        base_srv = FakeCompilerServer()
        try:
            # Forbidden ports 8765 and 18765 on either --qa or --baseline
            for bad_port in (8765, 18765):
                out_qa_bad = self.tmp / f"out-qa-{bad_port}"
                rc, _ = self._run_main(
                    f"http://127.0.0.1:{bad_port}",
                    base_srv.base_url,
                    out_qa_bad,
                )
                self.assertEqual(rc, 2)
                self.assertFalse(out_qa_bad.exists())

                out_base_bad = self.tmp / f"out-base-{bad_port}"
                rc, _ = self._run_main(
                    qa_srv.base_url,
                    f"http://127.0.0.1:{bad_port}",
                    out_base_bad,
                )
                self.assertEqual(rc, 2)
                self.assertFalse(out_base_bad.exists())

            # Non-loopback hosts on --qa or --baseline
            for bad_url in ("http://192.168.1.10:19001", "http://example.com:19001", "http://0.0.0.0:19001"):
                out_host_qa = self.tmp / "out-bad-host-qa"
                rc, _ = self._run_main(bad_url, base_srv.base_url, out_host_qa)
                self.assertEqual(rc, 2)
                self.assertFalse(out_host_qa.exists())

                out_host_base = self.tmp / "out-bad-host-base"
                rc, _ = self._run_main(qa_srv.base_url, bad_url, out_host_base)
                self.assertEqual(rc, 2)
                self.assertFalse(out_host_base.exists())

            # --qa == --baseline (exact match, trailing slash, or localhost vs 127.0.0.1 on same port)
            for same_base in (
                qa_srv.base_url,
                qa_srv.base_url + "/",
                f"http://localhost:{qa_srv.port}",
            ):
                out_same = self.tmp / "out-same-url"
                rc, _ = self._run_main(qa_srv.base_url, same_base, out_same)
                self.assertEqual(rc, 2)
                self.assertFalse(out_same.exists())

            # Existing --out directory or file
            existing_dir = self.tmp / "existing-out-dir"
            existing_dir.mkdir()
            sentinel = existing_dir / "keep.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            rc, _ = self._run_main(qa_srv.base_url, base_srv.base_url, existing_dir)
            self.assertEqual(rc, 2)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((existing_dir / "report.json").exists())

            # Verify zero HTTP requests were sent across all refusal cases
            self.assertEqual(qa_srv.requests, [])
            self.assertEqual(base_srv.requests, [])
        finally:
            qa_srv.close()
            base_srv.close()


if __name__ == "__main__":
    unittest.main()
