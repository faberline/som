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


def create_tiny_legacy_run(
    root: pathlib.Path,
    *,
    in_scope_count_override: int | None = None,
    group_counts_override: list[dict[str, Any]] | None = None,
    omit_second_job_input: bool = False,
) -> tuple[pathlib.Path, pathlib.Path, dict[str, Any], dict[str, Any]]:
    """Create a 2-job fake legacy run (1 in-scope, 1 out-of-scope) and matching fake inventory."""
    legacy_run = root / "legacy-run"
    compile_root = legacy_run / "compile"
    compile_root.mkdir(parents=True, exist_ok=True)

    v3_prompt = json.loads(
        (check_target.EXAMPLES / "v3.prompt.json").read_text(encoding="utf-8")
    )
    in_scope_prompt = {**v3_prompt, "package": "fake_legacy_in_scope"}
    in_scope_sha = check_target.sha_bytes(
        check_target.canonical_json(in_scope_prompt).encode("utf-8")
    )

    v6_prompt = json.loads(
        (check_target.EXAMPLES / "v6.prompt.json").read_text(encoding="utf-8")
    )
    out_of_scope_prompt = {**v6_prompt, "package": "fake_legacy_out_of_scope"}
    out_of_scope_sha = check_target.sha_bytes(
        check_target.canonical_json(out_of_scope_prompt).encode("utf-8")
    )

    jobs = [
        ("legacy100-0000", "legacy100", in_scope_prompt, in_scope_sha, False),
        (
            "services14-0000",
            "services14",
            out_of_scope_prompt,
            out_of_scope_sha,
            omit_second_job_input,
        ),
    ]
    records: list[dict[str, Any]] = []
    for idx, (job_id, group, prompt_obj, prompt_sha, omit_input) in enumerate(jobs):
        job_dir = legacy_run / f"{idx:04d}"
        job_dir.mkdir(parents=True, exist_ok=True)
        comp_dir = compile_root / prompt_sha
        comp_dir.mkdir(parents=True, exist_ok=True)
        if not omit_input:
            req_body = {"format": "som.prompt-json.v1", "prompt": prompt_obj}
            (comp_dir / "compile-json.request.json").write_text(
                json.dumps(req_body, indent=2) + "\n",
                encoding="utf-8",
            )
        records.append(
            {
                "id": job_id,
                "family": "legacy214",
                "group": group,
                "passed": True,
                "prompt_sha256": prompt_sha,
            }
        )

    (legacy_run / "result.json").write_text(
        json.dumps(
            {
                "version": "som.compiler-legacy544-result.v1",
                "status": "passed",
                "required_job_count": len(records),
                "passed_job_count": len(records),
                "compile_prompt_count": len(records),
                "records": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    base_inv = json.loads(
        (check_target.CONTRACT / "case-inventory.json").read_text(encoding="utf-8")
    )
    base_inv["legacy_in_scope"] = [
        {
            "id": "legacy100-0000",
            "group": "legacy100",
            "inventory_index": 0,
            "prompt_version": in_scope_prompt["version"],
            "prompt_sha256": in_scope_sha,
        }
    ]
    base_inv["counts"]["legacy_in_scope"] = (
        1 if in_scope_count_override is None else in_scope_count_override
    )
    base_inv["counts"]["legacy"] = (
        [
            {
                "group": "legacy100",
                "prompt_version": in_scope_prompt["version"],
                "typescript": "in_scope",
                "count": 1,
            },
            {
                "group": "services14",
                "prompt_version": out_of_scope_prompt["version"],
                "typescript": "prompt_version",
                "count": 1,
            },
        ]
        if group_counts_override is None
        else group_counts_override
    )
    inv_path = root / "case-inventory.json"
    inv_path.write_text(json.dumps(base_inv, indent=2) + "\n", encoding="utf-8")
    return legacy_run, inv_path, in_scope_prompt, out_of_scope_prompt


class CheckTargetTests(unittest.TestCase):
    def _run_main(
        self,
        argv: list[str],
        *,
        expected_legacy: int | None = 2,
        expected_legacy_in_scope: int | None = 1,
        patch_legacy: bool = True,
        inv_path: pathlib.Path | None = None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as leg_tmp:
            if "--legacy-run" not in argv:
                leg_run, default_inv, _, _ = create_tiny_legacy_run(pathlib.Path(leg_tmp))
                argv = [*argv, "--legacy-run", str(leg_run)]
                if inv_path is None:
                    inv_path = default_inv
            if patch_legacy and inv_path is not None:
                with (
                    unittest.mock.patch.object(
                        check_target.build_case_inventory, "OUT", inv_path
                    ),
                    unittest.mock.patch.object(
                        check_target,
                        "EXPECTED_LEGACY",
                        2 if expected_legacy is None else expected_legacy,
                    ),
                    unittest.mock.patch.object(
                        check_target,
                        "EXPECTED_LEGACY_IN_SCOPE",
                        1 if expected_legacy_in_scope is None else expected_legacy_in_scope,
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    rc = check_target.main(argv)
            else:
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
            self.assertNotIn("legacy_in_scope", report)

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
                    "legacy",
                },
            )
            legacy_rows = [r for r in report["cases"] if r["section"] == "legacy"]
            self.assertEqual(len(legacy_rows), 2)
            self.assertEqual(report["cases"][-2:], legacy_rows)
            self.assertEqual(legacy_rows[0]["id"], "legacy100-0000")
            self.assertEqual(legacy_rows[0]["inventory_index"], 0)
            self.assertEqual(legacy_rows[0]["group"], "legacy100")
            self.assertTrue(legacy_rows[0]["in_scope"])
            self.assertEqual(legacy_rows[0]["http_status"], 200)
            self.assertEqual(legacy_rows[1]["id"], "services14-0000")
            self.assertEqual(legacy_rows[1]["inventory_index"], 1)
            self.assertEqual(legacy_rows[1]["group"], "services14")
            self.assertFalse(legacy_rows[1]["in_scope"])
            self.assertEqual(legacy_rows[1]["http_status"], 422)

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

    def test_legacy_in_scope_refused_fails(self) -> None:
        def refuse_legacy_in_scope(
            body: str, default: tuple[int, bytes]
        ) -> tuple[int, bytes]:
            try:
                req = json.loads(body)
            except Exception:  # noqa: BLE001
                return default
            if (
                req.get("target") == "typescript"
                and isinstance(req.get("prompt"), dict)
                and req["prompt"].get("package") == "fake_legacy_in_scope"
            ):
                reply = {
                    "status": "error",
                    "code": "UNSUPPORTED_TARGET",
                    "detail": "refused in-scope legacy job",
                    "diagnostics": [
                        {
                            "code": "UNSUPPORTED_TARGET",
                            "prompt_pointer": "/prompt/version",
                            "cause": "refused in-scope legacy job",
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
            override=refuse_legacy_in_scope
        ) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            leg_in = [r for r in report["cases"] if r["id"] == "legacy100-0000"]
            self.assertEqual(len(leg_in), 1)
            self.assertFalse(leg_in[0]["passed"])
            self.assertEqual(leg_in[0]["status"], "failed")
            self.assertIn("UNSUPPORTED_TARGET", leg_in[0]["reason"])

    def test_legacy_out_of_scope_accepted_fails(self) -> None:
        def accept_legacy_out_of_scope(
            body: str, default: tuple[int, bytes]
        ) -> tuple[int, bytes]:
            try:
                req = json.loads(body)
            except Exception:  # noqa: BLE001
                return default
            if (
                req.get("target") == "typescript"
                and isinstance(req.get("prompt"), dict)
                and req["prompt"].get("package") == "fake_legacy_out_of_scope"
            ):
                reply = {
                    "status": "ok",
                    "bundle": {
                        "version": check_target.TS_BUNDLE_VERSION,
                        "target": "typescript",
                    },
                }
                return 200, check_target.compact_json(reply).encode("utf-8")
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(
            override=accept_legacy_out_of_scope
        ) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            leg_out = [r for r in report["cases"] if r["id"] == "services14-0000"]
            self.assertEqual(len(leg_out), 1)
            self.assertFalse(leg_out[0]["passed"])
            self.assertEqual(leg_out[0]["status"], "failed")
            self.assertIn("http_status 200 != expected 422", leg_out[0]["reason"])

    def test_legacy_refusal_wrong_rule_or_pointer_fails(self) -> None:
        def wrong_rule(body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            try:
                req = json.loads(body)
            except Exception:  # noqa: BLE001
                return default
            if (
                req.get("target") == "typescript"
                and isinstance(req.get("prompt"), dict)
                and req["prompt"].get("package") == "fake_legacy_out_of_scope"
            ):
                payload = json.loads(default[1])
                other_ref = SCOPE["refusals"]["table_reserved"]
                cause = other_ref["cause"].format(table="_sqlx_migrations")
                payload["detail"] = cause
                payload["diagnostics"][0]["cause"] = cause
                payload["diagnostics"][0]["allowed_form"] = other_ref["allowed_form"]
                return 422, check_target.compact_json(payload).encode("utf-8")
            return default

        def wrong_pointer(body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            try:
                req = json.loads(body)
            except Exception:  # noqa: BLE001
                return default
            if (
                req.get("target") == "typescript"
                and isinstance(req.get("prompt"), dict)
                and req["prompt"].get("package") == "fake_legacy_out_of_scope"
            ):
                payload = json.loads(default[1])
                payload["diagnostics"][0]["prompt_pointer"] = "/prompt/package"
                return 422, check_target.compact_json(payload).encode("utf-8")
            return default

        for label, override_fn, expected_substr in (
            ("wrong_rule", wrong_rule, "diagnostic.cause"),
            ("wrong_pointer", wrong_pointer, "diagnostic.prompt_pointer"),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp, FakeSomServer(
                override=override_fn
            ) as srv:
                out_dir = pathlib.Path(tmp) / "out"
                rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
                self.assertEqual(rc, 1)
                report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
                self.assertFalse(report["passed"])
                leg_out = [r for r in report["cases"] if r["id"] == "services14-0000"]
                self.assertEqual(len(leg_out), 1)
                self.assertFalse(leg_out[0]["passed"])
                self.assertIn(expected_substr, leg_out[0]["reason"])

    def test_legacy_wrong_counts_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeSomServer() as srv:
            base = pathlib.Path(tmp)
            leg_run, inv_path, _, _ = create_tiny_legacy_run(base / "fx-default")

            # 1. Unpatched 544 requirement fails when legacy run only has 2 jobs
            out_not_544 = base / "out-not-544"
            rc_544, _, _ = self._run_main(
                ["--url", srv.url, "--out", str(out_not_544), "--legacy-run", str(leg_run)],
                patch_legacy=False,
            )
            self.assertEqual(rc_544, 1)
            rep_544 = json.loads((out_not_544 / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(rep_544["passed"])
            self.assertTrue(
                any("legacy job count is 2, expected 544" in p for p in rep_544["problems"]),
                msg=rep_544["problems"],
            )

            # 2. Wrong in-scope count fails
            out_wrong_in_scope = base / "out-wrong-in-scope"
            rc_in, _, _ = self._run_main(
                [
                    "--url",
                    srv.url,
                    "--out",
                    str(out_wrong_in_scope),
                    "--legacy-run",
                    str(leg_run),
                ],
                expected_legacy=2,
                expected_legacy_in_scope=100,
                inv_path=inv_path,
            )
            self.assertEqual(rc_in, 1)
            rep_in = json.loads(
                (out_wrong_in_scope / "report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(rep_in["passed"])
            self.assertTrue(
                any("legacy in-scope job count is 1, expected 100" in p for p in rep_in["problems"]),
                msg=rep_in["problems"],
            )

            # 3. Group count that differs from counts.legacy fails
            bad_group_counts = [
                {
                    "group": "legacy100",
                    "prompt_version": "som.prompt.v3",
                    "typescript": "in_scope",
                    "count": 1,
                },
                {
                    "group": "services14",
                    "prompt_version": "som.prompt.v6",
                    "typescript": "prompt_version",
                    "count": 2,
                },
            ]
            leg_run_grp, inv_path_grp, _, _ = create_tiny_legacy_run(
                base / "fx-group", group_counts_override=bad_group_counts
            )
            out_wrong_grp = base / "out-wrong-group"
            rc_grp, _, _ = self._run_main(
                [
                    "--url",
                    srv.url,
                    "--out",
                    str(out_wrong_grp),
                    "--legacy-run",
                    str(leg_run_grp),
                ],
                expected_legacy=2,
                expected_legacy_in_scope=1,
                inv_path=inv_path_grp,
            )
            self.assertEqual(rc_grp, 1)
            rep_grp = json.loads(
                (out_wrong_grp / "report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(rep_grp["passed"])
            self.assertTrue(
                any("legacy group refusal counts" in p for p in rep_grp["problems"]),
                msg=rep_grp["problems"],
            )

    def test_legacy_missing_job_input_is_not_run_and_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeSomServer() as srv:
            base = pathlib.Path(tmp)
            leg_run, inv_path, _, _ = create_tiny_legacy_run(
                base / "fx-missing", omit_second_job_input=True
            )
            out_dir = base / "out-missing"
            rc, _, _ = self._run_main(
                ["--url", srv.url, "--out", str(out_dir), "--legacy-run", str(leg_run)],
                expected_legacy=2,
                expected_legacy_in_scope=1,
                inv_path=inv_path,
            )
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["not_run_count"], 1)
            leg_rows = {r["id"]: r for r in report["cases"] if r["section"] == "legacy"}
            self.assertTrue(leg_rows["legacy100-0000"]["passed"])
            self.assertEqual(leg_rows["legacy100-0000"]["status"], "passed")
            self.assertFalse(leg_rows["services14-0000"]["passed"])
            self.assertEqual(leg_rows["services14-0000"]["status"], "not_run")


if __name__ == "__main__":
    unittest.main()
