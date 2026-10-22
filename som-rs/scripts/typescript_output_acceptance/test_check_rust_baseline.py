#!/usr/bin/env python3
"""Unit tests for check_rust_baseline.py (#104)."""
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
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_rust_baseline as crb  # noqa: E402


_ACTIVE_SERVERS: dict[int, FakeLoopbackServer] = {}


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
        if exc.errno != errno.EPERM or self.host not in crb.LOOPBACK_HOSTS:
            raise
        srv = _lookup_active_server(int(self.port))
        if srv is None:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused") from exc
        cli_sock, srv_sock = socket.socketpair()
        self.sock = cli_sock
        srv.accept_socketpair(srv_sock)


http.client.HTTPConnection.connect = _sandbox_aware_http_connect  # type: ignore[method-assign]


class FakeLoopbackServer:
    """Fake loopback HTTP server supporting real TCP or sandbox in-process fallback."""

    def __init__(
        self,
        *,
        role: str,
        backend_sha: str,
        target_contract_sha: str,
        product_files_by_id: dict[str, dict[str, str]],
        refusal_cases: set[str] | None = None,
        mutate_bundle: Any = None,
        mutate_refusal_bytes: Any = None,
        accept_any_bundle_on_assemble: bool = False,
        mutate_assembled_files: Any = None,
    ) -> None:
        self.role = role
        self.backend_sha = backend_sha
        self.target_contract_sha = target_contract_sha
        self.product_files_by_id = product_files_by_id
        self.refusal_cases = refusal_cases or {"rs/RS12"}
        self.mutate_bundle = mutate_bundle
        self.mutate_refusal_bytes = mutate_refusal_bytes
        self.accept_any_bundle_on_assemble = accept_any_bundle_on_assemble
        self.mutate_assembled_files = mutate_assembled_files

        self.compile_requests: list[dict[str, Any]] = []
        self.assemble_requests: list[dict[str, Any]] = []
        self.request_count = 0
        self._lock = threading.Lock()
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0
        self.base_url: str = ""

    def _build_bundle(self, prompt: dict[str, Any]) -> dict[str, Any]:
        case_id = str(prompt.get("_test_case_id") or prompt.get("service", {}).get("name") or "case")
        bundle: dict[str, Any] = {
            "version": "som.dsl-bundle.v2",
            "target": "rust",
            "prompt": prompt,
            "dsl": {
                "version": "som.dsl.v1",
                "files": [
                    {
                        "path": "src/lib.rs",
                        "blocks": [{"id": "rust.lib", "params": {"case": case_id}}],
                    }
                ],
            },
            "assets": {
                "backend": {"version": "v1", "sha256": self.backend_sha},
                "target_contract": {"version": "v1", "sha256": self.target_contract_sha},
                "catalog": {"version": "v1", "sha256": "c" * 64},
                "block_library": {"version": "v1", "sha256": "d" * 64},
                "formatter": {"version": "v1", "sha256": "e" * 64},
                "compiler_contract": {"version": "v1", "sha256": "f" * 64},
                "selection_policy": {"version": "v1", "sha256": "1" * 64},
            },
            "hashes": {
                "prompt_sha256": crb.canonical_sha256(prompt),
                "dsl_sha256": "2" * 64,
                "payload_sha256": "",
            },
        }
        bundle["hashes"]["payload_sha256"] = crb.compute_bundle_payload_sha256(bundle)
        if self.mutate_bundle is not None:
            self.mutate_bundle(case_id, bundle)
        return bundle

    def start(self) -> str:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def _send_raw(self, status: int, raw: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length)
                req_doc = json.loads(raw_body.decode("utf-8"))

                with outer._lock:
                    outer.request_count += 1
                    if self.path == "/v1/compile":
                        outer.compile_requests.append(req_doc)
                    elif self.path == "/v1/assemble":
                        outer.assemble_requests.append(req_doc)

                if self.path == "/v1/compile":
                    prompt = req_doc.get("prompt", {})
                    case_id = str(prompt.get("_test_case_id") or "")
                    if case_id in outer.refusal_cases:
                        refusal_doc = {
                            "status": "error",
                            "code": "INVALID_VALUE",
                            "detail": "query parameter collision in RS12",
                            "diagnostics": [
                                {
                                    "code": "INVALID_VALUE",
                                    "prompt_pointer": "/prompt/query/filter_field",
                                    "cause": "query parameter collision in RS12",
                                }
                            ],
                        }
                        raw_refusal = crb.compact_json_bytes(refusal_doc)
                        status_code = 400
                        if outer.mutate_refusal_bytes is not None:
                            status_code, raw_refusal = outer.mutate_refusal_bytes(
                                case_id, status_code, raw_refusal
                            )
                        self._send_raw(status_code, raw_refusal)
                        return

                    bundle = outer._build_bundle(prompt)
                    resp_bytes = crb.compact_json_bytes({"status": "ok", "bundle": bundle})
                    self._send_raw(200, resp_bytes)
                    return

                if self.path == "/v1/assemble":
                    bundle = req_doc.get("bundle", {})
                    prompt = bundle.get("prompt", {})
                    case_id = str(prompt.get("_test_case_id") or "")
                    assets = bundle.get("assets", {})
                    backend_ok = (assets.get("backend") or {}).get("sha256") == outer.backend_sha
                    tc_ok = (assets.get("target_contract") or {}).get("sha256") == outer.target_contract_sha
                    expected_payload = crb.compute_bundle_payload_sha256(bundle)
                    actual_payload = (bundle.get("hashes") or {}).get("payload_sha256")

                    if actual_payload != expected_payload:
                        err = {
                            "status": "error",
                            "code": "BUNDLE_HASH_MISMATCH",
                            "detail": "payload_sha256 mismatch",
                        }
                        self._send_raw(400, crb.compact_json_bytes(err))
                        return

                    if not outer.accept_any_bundle_on_assemble and (not backend_ok or not tc_ok):
                        err = {
                            "status": "error",
                            "code": "ASSET_HASH_MISMATCH",
                            "detail": "asset descriptor sha256 mismatch",
                            "diagnostics": [
                                {
                                    "code": "ASSET_HASH_MISMATCH",
                                    "prompt_pointer": "/bundle/assets",
                                }
                            ],
                        }
                        self._send_raw(400, crb.compact_json_bytes(err))
                        return

                    files = copy.deepcopy(
                        outer.product_files_by_id.get(
                            case_id,
                            {"Cargo.toml": "[package]\nname = \"app\"\n", "src/lib.rs": "// default\n"},
                        )
                    )
                    if outer.mutate_assembled_files is not None:
                        files = outer.mutate_assembled_files(case_id, files)
                    resp = {"status": "ok", "files": files}
                    self._send_raw(200, crb.compact_json_bytes(resp))
                    return

                self._send_raw(404, b'{"status":"error","code":"NOT_FOUND"}')

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if self._httpd.server_port in crb.FORBIDDEN_PORTS:
            self._httpd.server_close()
            self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_port)
        _ACTIVE_SERVERS[self.port] = self
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        return self.base_url

    def accept_socketpair(self, srv_sock: socket.socket) -> None:
        assert self._httpd is not None
        self._httpd.process_request(srv_sock, ("127.0.0.1", 0))

    def close(self) -> None:
        _ACTIVE_SERVERS.pop(self.port, None)
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


def _sample_prompt(case_id: str) -> dict[str, Any]:
    return {
        "version": "som.prompt.v1",
        "_test_case_id": case_id,
        "service": {"name": f"svc_{case_id.replace('/', '_').replace('-', '_')}"},
        "resource": {
            "name": "item",
            "fields": [{"name": "title", "type": "str", "required": True}],
        },
    }


class CheckRustBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._tmp.name)
        self.run_dir = self.tmp_path / "run-003"
        self.out_dir = self.tmp_path / "out"

        self.p1_id = "example-v1"
        self.p2_id = "L214-001"
        self.refusal_id = "rs/RS12"

        self.p1_files = {
            "Cargo.toml": '[package]\nname = "p1_app"\nversion = "0.1.0"\n',
            "src/main.rs": "fn main() { println!(\"p1\"); }\n",
        }
        self.p2_files = {
            "Cargo.toml": '[package]\nname = "p2_app"\nversion = "0.1.0"\n',
            "src/main.rs": "fn main() { println!(\"p2\"); }\n",
        }
        self.product_files_by_id = {
            self.p1_id: self.p1_files,
            self.p2_id: self.p2_files,
        }
        self._write_fake_run003()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_fake_run003(self, *, omit_phase2_record: bool = False) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        p1_prompt = _sample_prompt(self.p1_id)
        p2_prompt = _sample_prompt(self.p2_id)
        rs12_prompt = _sample_prompt(self.refusal_id)

        rust_products = {
            "cases": [
                {
                    "id": self.p1_id,
                    "prompt": p1_prompt,
                    "bundle_payload_sha256": "0" * 64,
                    "files_sha256": crb.canonical_sha256(self.p1_files),
                    "passed": True,
                }
            ]
        }
        (self.run_dir / "rust-products.json").write_text(
            json.dumps(rust_products, indent=2) + "\n", encoding="utf-8"
        )

        cases_dir = self.run_dir / "phase2" / "cases"
        cases_dir.mkdir(parents=True, exist_ok=True)

        summary_doc = {
            "case_sha256": {
                self.p2_id: "1" * 64,
                self.refusal_id: "2" * 64,
            }
        }
        (self.run_dir / "phase2" / "summary.json").write_text(
            json.dumps(summary_doc, indent=2) + "\n", encoding="utf-8"
        )

        if not omit_phase2_record:
            p2_rec = {
                "id": self.p2_id,
                "prompt": p2_prompt,
                "prompt_sha256": crb.canonical_sha256(p2_prompt),
                "rust": {
                    "compile_status": 200,
                    "expected": "product",
                    "files_sha256": crb.canonical_sha256(self.p2_files),
                    "pass": True,
                },
            }
            (cases_dir / f"{crb.phase2.key(self.p2_id)}.json").write_text(
                json.dumps(p2_rec, indent=2) + "\n", encoding="utf-8"
            )

        rs12_rec = {
            "id": self.refusal_id,
            "prompt": rs12_prompt,
            "prompt_sha256": crb.canonical_sha256(rs12_prompt),
            "rust": {
                "compile_status": 400,
                "expected": "refusal",
                "pass": True,
                "refusal": {"code": "INVALID_VALUE"},
            },
        }
        (cases_dir / f"{crb.phase2.key(self.refusal_id)}.json").write_text(
            json.dumps(rs12_rec, indent=2) + "\n", encoding="utf-8"
        )

    def _start_pair(
        self,
        *,
        qa_kwargs: dict[str, Any] | None = None,
        base_kwargs: dict[str, Any] | None = None,
    ) -> tuple[FakeLoopbackServer, FakeLoopbackServer]:
        qa_srv = FakeLoopbackServer(
            role="qa",
            backend_sha="a1" * 32,
            target_contract_sha="a2" * 32,
            product_files_by_id=self.product_files_by_id,
            **(qa_kwargs or {}),
        )
        base_srv = FakeLoopbackServer(
            role="baseline",
            backend_sha="b1" * 32,
            target_contract_sha="b2" * 32,
            product_files_by_id=self.product_files_by_id,
            **(base_kwargs or {}),
        )
        qa_srv.start()
        base_srv.start()
        self.addCleanup(qa_srv.close)
        self.addCleanup(base_srv.close)
        return qa_srv, base_srv

    def test_pass_path_and_rs12_refusal_passes(self) -> None:
        qa_srv, base_srv = self._start_pair()
        code = crb.main(
            [
                "--qa",
                qa_srv.base_url,
                "--baseline",
                base_srv.base_url,
                "--run",
                str(self.run_dir),
                "--out",
                str(self.out_dir),
                "--workers",
                "2",
            ]
        )
        self.assertEqual(code, 0)
        report = json.loads((self.out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertTrue(report["passed"])
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["failed_rows"], 0)
        self.assertEqual(report["not_run_rows"], 0)
        self.assertIn(self.refusal_id, report["refusal_cases"])
        self.assertIsNotNone(report["rust_products_sha256"])
        self.assertIn(self.p2_id, report["records_sha256"]["phase2_case_files"])
        # 2 product cases * 3 checks + 1 refusal case * 1 check = 7 rows
        self.assertEqual(len(report["rows"]), 7)
        self.assertTrue(all(r["passed"] and r["status"] == "passed" for r in report["rows"]))
        self.assertFalse((self.out_dir / "responses").exists())

    def test_fails_on_difference_outside_three_allowed_fields(self) -> None:
        def mutate_bundle(case_id: str, bundle: dict[str, Any]) -> None:
            if case_id == self.p1_id:
                bundle["assets"]["catalog"] = {"version": "v1", "sha256": "9" * 64}
                bundle["hashes"]["payload_sha256"] = crb.compute_bundle_payload_sha256(bundle)

        qa_srv, base_srv = self._start_pair(qa_kwargs={"mutate_bundle": mutate_bundle})
        code = crb.main(
            [
                "--qa",
                qa_srv.base_url,
                "--baseline",
                base_srv.base_url,
                "--run",
                str(self.run_dir),
                "--out",
                str(self.out_dir),
            ]
        )
        self.assertEqual(code, 1)
        report = json.loads((self.out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        failed = [
            r
            for r in report["rows"]
            if r["id"] == self.p1_id and r["check"] == "compile_equal"
        ]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0]["passed"])
        self.assertIn("catalog", failed[0]["reason"])
        self.assertTrue((self.out_dir / "responses").is_dir())

    def test_fails_on_wrong_candidate_payload_sha256(self) -> None:
        def mutate_bundle(case_id: str, bundle: dict[str, Any]) -> None:
            if case_id == self.p1_id:
                bundle["hashes"]["payload_sha256"] = "0" * 64

        qa_srv, base_srv = self._start_pair(qa_kwargs={"mutate_bundle": mutate_bundle})
        code = crb.main(
            [
                "--qa",
                qa_srv.base_url,
                "--baseline",
                base_srv.base_url,
                "--run",
                str(self.run_dir),
                "--out",
                str(self.out_dir),
            ]
        )
        self.assertEqual(code, 1)
        report = json.loads((self.out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        failed = [
            r
            for r in report["rows"]
            if r["id"] == self.p1_id and r["check"] == "compile_equal"
        ]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0]["passed"])
        self.assertIn("payload_sha256", failed[0]["reason"])

    def test_fails_when_baseline_bundle_accepted_by_candidate(self) -> None:
        qa_srv, base_srv = self._start_pair(qa_kwargs={"accept_any_bundle_on_assemble": True})
        code = crb.main(
            [
                "--qa",
                qa_srv.base_url,
                "--baseline",
                base_srv.base_url,
                "--run",
                str(self.run_dir),
                "--out",
                str(self.out_dir),
            ]
        )
        self.assertEqual(code, 1)
        report = json.loads((self.out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        refuse_rows = [r for r in report["rows"] if r["check"] == "baseline_bundle_refused"]
        self.assertTrue(all(not r["passed"] and r["status"] == "failed" for r in refuse_rows))
        self.assertTrue((self.out_dir / "responses").is_dir())

    def test_fails_when_rebased_files_differ_from_files_sha256(self) -> None:
        def mutate_files(case_id: str, files: dict[str, str]) -> dict[str, str]:
            if case_id == self.p2_id:
                files["src/main.rs"] = "fn main() { println!(\"tampered\"); }\n"
            return files

        qa_srv, base_srv = self._start_pair(qa_kwargs={"mutate_assembled_files": mutate_files})
        code = crb.main(
            [
                "--qa",
                qa_srv.base_url,
                "--baseline",
                base_srv.base_url,
                "--run",
                str(self.run_dir),
                "--out",
                str(self.out_dir),
            ]
        )
        self.assertEqual(code, 1)
        report = json.loads((self.out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        reb_rows = [
            r for r in report["rows"] if r["id"] == self.p2_id and r["check"] == "rebased_files"
        ]
        self.assertEqual(len(reb_rows), 1)
        self.assertFalse(reb_rows[0]["passed"])
        self.assertIn("files_sha256", reb_rows[0]["reason"])
        self.assertTrue((self.out_dir / "responses").is_dir())

    def test_fails_on_different_refusal_byte(self) -> None:
        def mutate_refusal(case_id: str, status: int, raw: bytes) -> tuple[int, bytes]:
            return status, raw + b" "

        qa_srv, base_srv = self._start_pair(qa_kwargs={"mutate_refusal_bytes": mutate_refusal})
        code = crb.main(
            [
                "--qa",
                qa_srv.base_url,
                "--baseline",
                base_srv.base_url,
                "--run",
                str(self.run_dir),
                "--out",
                str(self.out_dir),
            ]
        )
        self.assertEqual(code, 1)
        report = json.loads((self.out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        ref_rows = [r for r in report["rows"] if r["id"] == self.refusal_id]
        self.assertEqual(len(ref_rows), 1)
        self.assertFalse(ref_rows[0]["passed"])
        self.assertIn("bytes differ", ref_rows[0]["reason"])
        self.assertTrue((self.out_dir / "responses").is_dir())

    def test_missing_record_is_not_run_and_exits_1(self) -> None:
        # Remove the phase 2 record file for self.p2_id while keeping it in summary.json
        missing_path = self.run_dir / "phase2" / "cases" / f"{crb.phase2.key(self.p2_id)}.json"
        missing_path.unlink()

        qa_srv, base_srv = self._start_pair()
        code = crb.main(
            [
                "--qa",
                qa_srv.base_url,
                "--baseline",
                base_srv.base_url,
                "--run",
                str(self.run_dir),
                "--out",
                str(self.out_dir),
            ]
        )
        self.assertEqual(code, 1)
        report = json.loads((self.out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertGreater(report["not_run_rows"], 0)
        p2_rows = [r for r in report["rows"] if r["id"] == self.p2_id]
        self.assertTrue(all(r["status"] == "not_run" and not r["passed"] for r in p2_rows))

    def test_cli_validation_refuses_forbidden_ports_non_loopback_same_url_and_existing_out(
        self,
    ) -> None:
        qa_srv, base_srv = self._start_pair()
        existing_out = self.tmp_path / "existing-out"
        existing_out.mkdir()

        invalid_invocations = [
            # Forbidden port 8765 on --qa
            ["--qa", "http://127.0.0.1:8765", "--baseline", base_srv.base_url, "--run", str(self.run_dir), "--out", str(self.tmp_path / "o1")],
            # Forbidden port 18765 on --baseline
            ["--qa", qa_srv.base_url, "--baseline", "http://127.0.0.1:18765", "--run", str(self.run_dir), "--out", str(self.tmp_path / "o2")],
            # Non-loopback host on --qa
            ["--qa", "http://192.168.1.10:9000", "--baseline", base_srv.base_url, "--run", str(self.run_dir), "--out", str(self.tmp_path / "o3")],
            # Non-loopback host on --baseline
            ["--qa", qa_srv.base_url, "--baseline", "http://example.com:9000", "--run", str(self.run_dir), "--out", str(self.tmp_path / "o4")],
            # Identical URLs
            ["--qa", qa_srv.base_url, "--baseline", qa_srv.base_url, "--run", str(self.run_dir), "--out", str(self.tmp_path / "o5")],
            # Existing --out
            ["--qa", qa_srv.base_url, "--baseline", base_srv.base_url, "--run", str(self.run_dir), "--out", str(existing_out)],
        ]

        for argv in invalid_invocations:
            with self.subTest(argv=argv):
                err_buf = io.StringIO()
                with contextlib.redirect_stderr(err_buf):
                    code = crb.main(argv)
                self.assertEqual(code, 2)
                self.assertIn("error:", err_buf.getvalue())

        self.assertEqual(qa_srv.request_count, 0)
        self.assertEqual(base_srv.request_count, 0)


if __name__ == "__main__":
    unittest.main()
