#!/usr/bin/env python3
"""Unit tests for som-rs/scripts/typescript_output_acceptance/run_acceptance.py."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import http.client
import http.server
import json
import os
import pathlib
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from typing import Any
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REAL_REPO = SOM_RS.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_acceptance  # noqa: E402


def _install_unified_sandbox_http_connect() -> None:
    """Ensure all test_*.py fake loopback servers work together under unittest discover in the sandbox."""
    true_connect = http.client.HTTPConnection.connect
    for mod in list(sys.modules.values()):
        orig = getattr(mod, "_ORIG_HTTP_CONNECT", None)
        if callable(orig) and getattr(orig, "__name__", "") == "connect":
            true_connect = orig
            break

    def unified_connect(conn: http.client.HTTPConnection) -> None:
        try:
            true_connect(conn)
        except PermissionError as exc:
            if exc.errno != errno.EPERM:
                raise
            port = int(conn.port)
            for mod in list(sys.modules.values()):
                table = getattr(mod, "_ACTIVE_SERVERS", None)
                if isinstance(table, dict) and port in table:
                    srv = table[port]
                    cli_sock, srv_sock = socket.socketpair()
                    if conn.timeout is not None:
                        cli_sock.settimeout(conn.timeout)
                    conn.sock = cli_sock
                    if hasattr(srv, "accept_socketpair"):
                        srv.accept_socketpair(srv_sock)
                    else:
                        threading.Thread(
                            target=srv.process_request,
                            args=(srv_sock, ("127.0.0.1", 0)),
                            daemon=True,
                        ).start()
                    return
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused") from exc

    http.client.HTTPConnection.connect = unified_connect  # type: ignore[method-assign]
    for mod in list(sys.modules.values()):
        if hasattr(mod, "_ORIG_HTTP_CONNECT"):
            setattr(mod, "_ORIG_HTTP_CONNECT", unified_connect)


_install_unified_sandbox_http_connect()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    return sha_bytes(path.read_bytes())


def reserve_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


STUB_SCRIPT_TEMPLATE = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json
    import os
    import pathlib
    import sys

    step_name = {step_name!r}
    config_path = pathlib.Path({config_path!r})
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {{}}
    step_cfg = (config.get("steps") or {{}}).get(step_name, {{}})
    behavior = step_cfg.get("behavior", "pass")

    argv = sys.argv[1:]
    out_val = None
    for flag in ("--out", "--evidence"):
        if flag in argv:
            idx = argv.index(flag)
            if idx + 1 < len(argv):
                out_val = pathlib.Path(argv[idx + 1])
                break

    # Record invocation in tracker file
    log_file = pathlib.Path({tracker_path!r})
    records = json.loads(log_file.read_text(encoding="utf-8")) if log_file.is_file() else []
    header_before_seen = False
    if out_val is not None:
        for parent in [out_val, *out_val.parents]:
            h_file = parent / "header.json"
            if h_file.is_file():
                h_doc = json.loads(h_file.read_text(encoding="utf-8"))
                header_before_seen = h_doc.get("before") is not None and h_doc.get("after") is None
                break
    records.append({{
        "step": step_name,
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "header_before_seen": header_before_seen,
    }})
    log_file.write_text(json.dumps(records, indent=2) + "\\n", encoding="utf-8")

    if behavior == "missing_report":
        sys.exit(step_cfg.get("exit_code", 0))

    row_status = "passed"
    row_passed = True
    if behavior == "failing_report":
        row_status = "failed"
        row_passed = False
    elif behavior == "not_run_row":
        row_status = "not_run"
        row_passed = False

    def make_rows():
        rows = [
            {{"id": "case-1", "status": "passed", "passed": True}},
            {{"id": "case-2", "status": row_status, "passed": row_passed}},
        ]
        return rows

    rows = make_rows()
    passed_count = sum(1 for r in rows if r["status"] == "passed" and r["passed"])
    failed_count = sum(1 for r in rows if r["status"] == "failed")
    not_run_count = sum(1 for r in rows if r["status"] == "not_run")
    all_ok = failed_count == 0 and not_run_count == 0 and passed_count == len(rows)
    overall_status = "passed" if all_ok else ("not_run" if not_run_count > 0 and failed_count == 0 else "failed")

    if step_name in ("target", "rt-diagnostics", "bundles", "python-baseline", "rust-baseline"):
        out_val.mkdir(parents=True, exist_ok=True)
        (out_val / "responses").mkdir(exist_ok=True)
        if step_name == "bundles":
            prod_dir = out_val / "products"
            prod_dir.mkdir(exist_ok=True)
            (prod_dir / "manifest.json").write_text(json.dumps({{"products": []}}) + "\\n", encoding="utf-8")
        report = {{
            "status": overall_status,
            "passed": all_ok,
            "total": len(rows),
            "passed_count": passed_count,
            "failed_count": failed_count,
            "not_run_count": not_run_count,
            "problems": [] if all_ok else (["row failed"] if failed_count else []),
            "rows": rows,
            "cases": rows,
        }}
        (out_val / "report.json").write_text(json.dumps(report, indent=2) + "\\n", encoding="utf-8")
    elif step_name == "ts-products":
        out_val.mkdir(parents=True, exist_ok=True)
        gate_entry = {{"status": row_status, "passed": row_passed}}
        prod_row = {{
            "id": "example-v1",
            "passed": all_ok,
            "status": overall_status,
            "failed_gates": ["install"] if failed_count else [],
            "not_run_gates": ["install"] if not_run_count else [],
            "gates": {{"install": gate_entry}},
        }}
        summary = {{
            "total_products": 1,
            "passed_products": 1 if all_ok else 0,
            "failed_products": ["example-v1"] if failed_count else [],
            "not_run_products": ["example-v1"] if not_run_count else [],
            "non_passing_gates": [] if all_ok else [{{"product": "example-v1", "gate": "install", "status": row_status}}],
            "no_sampling_complete": not_run_count == 0,
            "all_passed": all_ok,
        }}
        report = {{"summary": summary, "products": [prod_row]}}
        (out_val / "report.json").write_text(json.dumps(report, indent=2) + "\\n", encoding="utf-8")
        (out_val / "summary.json").write_text(json.dumps(summary, indent=2) + "\\n", encoding="utf-8")
    elif step_name == "cli":
        out_val.parent.mkdir(parents=True, exist_ok=True)
        report = {{
            "total": len(rows),
            "passed": passed_count,
            "refusals": 5,
            "refusals_exit_2": 5 if all_ok else 4,
            "typescript": {{"passed": all_ok, "status": overall_status}},
            "failed": [] if all_ok else ["case-2"],
            "rows": rows,
        }}
        out_val.write_text(json.dumps(report, indent=2) + "\\n", encoding="utf-8")
    elif step_name == "python-replay":
        out_val.parent.mkdir(parents=True, exist_ok=True)
        report = {{
            "passed": all_ok,
            "public": {{"required": 2, "total": len(rows), "passed": passed_count, "failed": [] if all_ok else ["case-2"]}},
            "legacy": {{"required": 2, "total": len(rows), "passed": passed_count, "failed": [] if all_ok else ["case-2"]}},
            "exchanges": {{"total": len(rows), "passed": passed_count}},
            "public_rows": rows,
            "legacy_compile_rows": rows,
            "legacy_rows": rows,
        }}
        out_val.write_text(json.dumps(report, indent=2) + "\\n", encoding="utf-8")
    elif step_name == "validate-cases":
        out_val.parent.mkdir(parents=True, exist_ok=True)
        vc_rows = [
            {{
                "id": "cov-1",
                "status": "passed",
                "passed": True,
                "json_status": 200,
                "cnl_status": 200,
                "same_bundle": True,
                "assemble_status": 200,
            }},
            {{
                "id": "cov-2",
                "status": row_status,
                "passed": row_passed,
                "json_status": 200 if row_passed else 500,
                "cnl_status": 200,
                "same_bundle": row_passed,
                "assemble_status": 200 if row_passed else 500,
            }},
        ]
        report = {{
            "cases": vc_rows,
            "passed": passed_count,
            "failed": [] if all_ok else ["cov-2"],
        }}
        out_val.write_text(json.dumps(report, indent=2) + "\\n", encoding="utf-8")
    elif step_name in ("ts-python-controls", "rust-python-controls"):
        out_val.parent.mkdir(parents=True, exist_ok=True)
        report = {{
            "passed": passed_count,
            "failed": [] if all_ok else ["case-2"],
            "results": rows,
        }}
        out_val.write_text(json.dumps(report, indent=2) + "\\n", encoding="utf-8")
    elif step_name == "rust-products":
        out_val.parent.mkdir(parents=True, exist_ok=True)
        refusal_rows = [
            {{"id": "A4", "passed": True, "status": "passed", "code": "INVALID_SCHEMA", "prompt_pointer": "/bundle/target"}},
            {{"id": "A6", "passed": row_passed, "status": row_status, "code": "INVALID_VALUE"}},
        ]
        case_rows = [
            {{"id": "example-v1", "passed": row_passed, "status": row_status, "bundle_checks": {{"passed": row_passed}}, "parity": {{"status": row_status}}}},
        ]
        report = {{
            "refusal_plan": "phase2",
            "summary": {{
                "acceptance_cases": {{"total": 1, "checked": 1, "frozen_acceptance_total": 1}},
                "bundle_checks": {{"passed": 1 if all_ok else 0, "total": 1}},
                "assemble_refusals": {{"passed": 2 if all_ok else 1, "total": 2}},
                "failed": [] if all_ok else ["example-v1"],
                "blocked": [],
            }},
            "assemble_refusals": refusal_rows,
            "cases": case_rows,
        }}
        out_val.write_text(json.dumps(report, indent=2) + "\\n", encoding="utf-8")
    elif step_name == "rust-unchanged":
        out_val.parent.mkdir(parents=True, exist_ok=True)
        report = {{
            "passed": all_ok,
            "phase1": {{"total": len(rows), "passed": passed_count, "failed": [] if all_ok else ["case-2"], "cases": rows}},
            "phase2": {{"total": len(rows), "passed": passed_count, "failed": [] if all_ok else ["case-2"], "cases": rows}},
        }}
        out_val.write_text(json.dumps(report, indent=2) + "\\n", encoding="utf-8")

    exit_code = step_cfg.get("exit_code", 0 if behavior != "nonzero_exit" else 1)
    sys.exit(exit_code)
    """
)

FAKE_BASELINE_SOM = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import http.server
    import json
    import pathlib
    import signal
    import sys

    args = sys.argv[1:]
    if not args or args[0] != "serve":
        sys.exit(2)
    port = int(args[args.index("--port") + 1])
    health_payload = json.dumps({
        "status": "ok",
        "role": "baseline",
        "backend_sha256": "581ce38f29702c300342cdefe8fe5f1735cefcd1869d6485b44f30063a866711",
        "asset_manifest_sha256": "d95d93c764d95373480b580cfa672de91c55f8f06a5446a786bff61395aeccaa",
    }).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(health_payload)))
                self.end_headers()
                self.wfile.write(health_payload)
            else:
                self.send_response(404)
                self.end_headers()
        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    pathlib.Path("healthz.json").write_bytes(health_payload)

    def handle_sigterm(signum, frame):
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    server.serve_forever()
    """
)


class RunAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="som-ts-run-acc-")
        self.root = pathlib.Path(self._tmp.name).resolve()
        self.som_rs = self.root / "som-rs"
        self.ts_acc = self.som_rs / "scripts" / "typescript_output_acceptance"
        self.rust_acc = self.som_rs / "scripts" / "rust_output_acceptance"
        self.qa_dir = self.som_rs / "som-targets" / "qa"
        self.ts_tests = self.som_rs / "tests" / "typescript-output-v1"
        self.rust_tests = self.som_rs / "tests" / "rust-output-v1"
        self.ts_contract = self.som_rs / "contracts" / "typescript-output-v1"
        self.rust_contract = self.som_rs / "contracts" / "rust-output-v1"
        self.venv_bin = self.som_rs / "runtime" / "acceptance-m3-venv" / "bin"

        for d in (
            self.ts_acc,
            self.rust_acc,
            self.qa_dir,
            self.ts_tests,
            self.rust_tests,
            self.ts_contract,
            self.rust_contract,
            self.venv_bin,
        ):
            d.mkdir(parents=True, exist_ok=True)

        # Symlink venv python to current interpreter
        venv_py = self.venv_bin / "python"
        venv_py.symlink_to(pathlib.Path(sys.executable))

        # Copy both real acceptance-plan.json files
        shutil.copy2(
            REAL_REPO / "som-rs" / "contracts" / "typescript-output-v1" / "acceptance-plan.json",
            self.ts_contract / "acceptance-plan.json",
        )
        shutil.copy2(
            REAL_REPO / "som-rs" / "contracts" / "rust-output-v1" / "acceptance-plan.json",
            self.rust_contract / "acceptance-plan.json",
        )

        # State files outside the 7 tool roots
        self.state_dir = self.root / ".local" / "stub-state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "config.json"
        self.tracker_path = self.state_dir / "tracker.json"
        self.config_path.write_text("{}\n", encoding="utf-8")

        # Write stub tool scripts for all 13 steps
        stub_scripts = {
            self.ts_acc / "check_target.py": "target",
            self.ts_acc / "check_rt_diagnostics.py": "rt-diagnostics",
            self.rust_acc / "check_cli.py": "cli",
            self.ts_acc / "check_bundles.py": "bundles",
            self.qa_dir / "check_ts_products.py": "ts-products",
            self.ts_acc / "check_python_baseline.py": "python-baseline",
            self.rust_acc / "python_replay.py": "python-replay",
            self.rust_tests / "validate_cases.py": "validate-cases",
            self.ts_tests / "check_python_controls.py": "ts-python-controls",
            self.rust_tests / "check_python_controls.py": "rust-python-controls",
            self.ts_acc / "check_rust_baseline.py": "rust-baseline",
            self.rust_acc / "run_acceptance.py": "rust-products",
            self.rust_acc / "check_rust_unchanged.py": "rust-unchanged",
        }
        for path, step_name in stub_scripts.items():
            path.write_text(
                STUB_SCRIPT_TEMPLATE.format(
                    step_name=step_name,
                    config_path=str(self.config_path),
                    tracker_path=str(self.tracker_path),
                ),
                encoding="utf-8",
            )
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

        # Initialize git repo and commit all tool roots
        subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "add", "som-rs"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-m", "init tools"], check=True, capture_output=True)
        self.head_commit = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Prepare fake baseline directory
        self.baseline_dir = self.root / ".local" / "rust-compiler-goal" / "rust-output-candidate-006"
        (self.baseline_dir / "assets").mkdir(parents=True, exist_ok=True)
        (self.baseline_dir / "assets" / "manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
        self.baseline_som = self.baseline_dir / "som"
        self.baseline_som.write_text(FAKE_BASELINE_SOM, encoding="utf-8")
        self.baseline_som.chmod(self.baseline_som.stat().st_mode | stat.S_IXUSR)
        self.fake_baseline_sha = sha_file(self.baseline_som)

        # Prepare fake candidate directory
        self.candidate_port = reserve_free_port()
        self.baseline_port = reserve_free_port()
        while self.baseline_port == self.candidate_port:
            self.baseline_port = reserve_free_port()

        self.candidate_dir = self.root / ".local" / "typescript-output" / "candidate-001"
        (self.candidate_dir / "assets").mkdir(parents=True, exist_ok=True)
        manifest_path = self.candidate_dir / "assets" / "manifest.json"
        manifest_path.write_text('{"version": 1}\n', encoding="utf-8")
        cand_som = self.candidate_dir / "som"
        cand_som.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cand_som.chmod(cand_som.stat().st_mode | stat.S_IXUSR)
        source_tar = self.candidate_dir / "source.tar"
        source_tar.write_bytes(b"fake-tar-bytes")

        tracked_rel = "som-rs/contracts/typescript-output-v1/acceptance-plan.json"
        source_doc = {
            "version": "som.compiler-candidate-source.v1",
            "commit": self.head_commit,
            "files": {tracked_rel: sha_file(self.root / tracked_rel)},
            "archive_sha256": sha_file(source_tar),
        }
        source_json = self.candidate_dir / "source.json"
        source_json.write_text(json.dumps(source_doc) + "\n", encoding="utf-8")

        self.cand_health = {"status": "ok", "role": "candidate"}
        self.cand_pid = os.getpid()
        build_doc = {
            "complete": True,
            "source_unchanged": True,
            "binary_sha256": sha_file(cand_som),
            "source_sha256": sha_file(source_json),
            "asset_manifest_sha256": sha_file(manifest_path),
        }
        (self.candidate_dir / "build.json").write_text(json.dumps(build_doc) + "\n", encoding="utf-8")
        runtime_doc = {
            "pid": self.cand_pid,
            "port": self.candidate_port,
            "binary_sha256": sha_file(cand_som),
            "source_sha256": sha_file(source_json),
            "health": self.cand_health,
        }
        (self.candidate_dir / "runtime.json").write_text(json.dumps(runtime_doc) + "\n", encoding="utf-8")

        self.acceptance_root = self.root / ".local" / "typescript-output" / "acceptance"
        self.npm_cache = self.root / ".local" / "typescript-output" / "npm-cache-001"
        self.npm_cache.mkdir(parents=True, exist_ok=True)
        self.run_003 = self.root / ".local" / "rust-compiler-goal" / "acceptance" / "rust-output-055" / "run-003"
        (self.run_003 / "behavior" / "parity").mkdir(parents=True, exist_ok=True)
        (self.run_003 / "behavior" / "parity" / "report-parity.json").write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @contextlib.contextmanager
    def _patched_env(self, *, expected_baseline_sha: str | None = None):
        exp_sha = self.fake_baseline_sha if expected_baseline_sha is None else expected_baseline_sha
        real_listener = run_acceptance.listener
        real_get = run_acceptance.get

        def fake_listener(port: int) -> list[str]:
            if port == self.candidate_port:
                return [str(self.cand_pid)]
            return real_listener(port)

        def fake_proc_cmd(pid: int) -> str:
            if pid == self.cand_pid:
                return f"{(self.candidate_dir / 'som').resolve()} serve --port {self.candidate_port}"
            return ""

        def fake_get(url: str, timeout: float = 10.0) -> tuple[int | None, bytes]:
            if url == f"http://127.0.0.1:{self.candidate_port}/healthz":
                return 200, json.dumps(self.cand_health).encode("utf-8")
            if url == "http://127.0.0.1:8765/healthz":
                return 200, json.dumps({"status": "ok", "port": 8765}).encode("utf-8")
            if url == f"http://127.0.0.1:{self.baseline_port}/healthz":
                status, raw = real_get(url, timeout=0.5)
                if status == 200:
                    return status, raw
                # In the macOS Seatbelt sandbox, socket.connect raises EPERM even when the child HTTPServer is listening.
                if real_listener(self.baseline_port):
                    return 200, json.dumps({
                        "status": "ok",
                        "role": "baseline",
                        "backend_sha256": "581ce38f29702c300342cdefe8fe5f1735cefcd1869d6485b44f30063a866711",
                        "asset_manifest_sha256": "d95d93c764d95373480b580cfa672de91c55f8f06a5446a786bff61395aeccaa",
                    }).encode("utf-8")
                return None, b"baseline not listening yet"
            return real_get(url, timeout=timeout)

        with (
            mock.patch.object(run_acceptance, "REPO", self.root),
            mock.patch.object(run_acceptance, "SOM_RS", self.som_rs),
            mock.patch.object(run_acceptance, "HERE", self.ts_acc),
            mock.patch.object(run_acceptance, "RUST_ACCEPTANCE", self.rust_acc),
            mock.patch.object(run_acceptance, "QA_DIR", self.qa_dir),
            mock.patch.object(run_acceptance, "TS_TESTS", self.ts_tests),
            mock.patch.object(run_acceptance, "RUST_TESTS", self.rust_tests),
            mock.patch.object(run_acceptance, "TS_CONTRACT", self.ts_contract),
            mock.patch.object(run_acceptance, "RUST_CONTRACT", self.rust_contract),
            mock.patch.object(run_acceptance, "VENV_PYTHON", self.venv_bin / "python"),
            mock.patch.object(run_acceptance, "LOCAL_TS", self.root / ".local" / "typescript-output"),
            mock.patch.object(run_acceptance, "ACCEPTANCE_ROOT", self.acceptance_root),
            mock.patch.object(run_acceptance, "NPM_CACHE", self.npm_cache),
            mock.patch.object(run_acceptance, "DEFAULT_BASELINE", self.baseline_dir),
            mock.patch.object(run_acceptance, "RUN_003", self.run_003),
            mock.patch.object(run_acceptance, "COVERAGE_FROZEN", self.som_rs / "evidence" / "nonexistent.json"),
            mock.patch.object(run_acceptance, "EXPECTED_BASELINE_BINARY_SHA256", exp_sha),
            mock.patch.object(run_acceptance, "listener", side_effect=fake_listener),
            mock.patch.object(run_acceptance, "process_command", side_effect=fake_proc_cmd),
            mock.patch.object(run_acceptance, "get", side_effect=fake_get),
        ):
            yield

    def _run_driver(self, out_dir: pathlib.Path, extra_args: list[str] | None = None, **patch_kwargs: Any) -> int:
        argv = [
            "--candidate",
            str(self.candidate_dir),
            "--out",
            str(out_dir),
            "--baseline",
            str(self.baseline_dir),
            "--baseline-port",
            str(self.baseline_port),
            "--workers",
            "2",
        ]
        if extra_args:
            argv = extra_args
        with self._patched_env(**patch_kwargs):
            try:
                return run_acceptance.main(argv)
            except SystemExit as exc:
                return int(exc.code) if isinstance(exc.code, int) else 2

    def _ran_steps(self) -> list[dict[str, Any]]:
        if not self.tracker_path.is_file():
            return []
        return json.loads(self.tracker_path.read_text(encoding="utf-8"))

    def test_pass_path_runs_all_13_steps_maps_all_checks_and_stops_baseline(self) -> None:
        out_dir = self.acceptance_root / "run-pass"
        code = self._run_driver(out_dir)
        self.assertEqual(code, 0)

        ran = self._ran_steps()
        expected_order = [
            "target",
            "rt-diagnostics",
            "cli",
            "bundles",
            "ts-products",
            "python-baseline",
            "python-replay",
            "validate-cases",
            "ts-python-controls",
            "rust-python-controls",
            "rust-baseline",
            "rust-products",
            "rust-unchanged",
        ]
        self.assertEqual([r["step"] for r in ran], expected_order)
        self.assertTrue(all(r["header_before_seen"] for r in ran))

        # Check receipts
        for idx, step_name in enumerate(expected_order, 1):
            receipt_file = out_dir / "receipts" / f"{idx:02d}-{step_name}.json"
            self.assertTrue(receipt_file.is_file(), f"missing receipt {receipt_file}")
            rec = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(rec["step"], step_name)
            self.assertEqual(rec["exit_code"], 0)
            self.assertTrue(rec["passed"])
            self.assertIn("argv", rec)
            self.assertIn("cwd", rec)
            self.assertIn("env_delta", rec)
            self.assertIn("stdout_sha256", rec)
            self.assertIn("stderr_sha256", rec)
            self.assertIn("outputs", rec)
            self.assertIn("summary", rec)

        # Check header.json before and after
        header = json.loads((out_dir / "header.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(header["before"])
        self.assertIsNotNone(header["after"])
        for snap_key in ("before", "after"):
            snap = header[snap_key]
            self.assertIn("candidate", snap)
            self.assertIn("baseline", snap)
            self.assertIn("default_8765", snap)
            self.assertIn("tools", snap)
            self.assertIn("acceptance_plans", snap)
            self.assertEqual(snap["tools"]["head"], self.head_commit)

        baseline_pid = header["baseline_before"]["pid"]
        self.assertFalse(is_pid_alive(baseline_pid), f"baseline PID {baseline_pid} should be stopped")
        self.assertEqual(run_acceptance.listener(self.baseline_port), [])

        # Check summary.json and plan mappings
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["steps_passed"])
        self.assertTrue(summary["passed"])
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["blockers"], [])

        ts_plan = summary["typescript_plan"]
        self.assertEqual(len(ts_plan["python_unchanged"]), 4)
        self.assertEqual(len(ts_plan["target_selection_checks"]), 5)
        self.assertEqual(len(ts_plan["scope_checks"]), 4)
        self.assertEqual(len(ts_plan["bundle_checks"]), 5)
        self.assertEqual(len(ts_plan["assemble_refusals"]), 12)
        self.assertEqual(len(ts_plan["product_gates"]), 9)
        self.assertEqual(len(ts_plan["independent_audit"]), 1)
        self.assertEqual(len(summary["amendment005"]), 10)

        audit_checks = [c for c in summary["plan_checks"] if c.get("audit")]
        self.assertEqual(len(audit_checks), 2)
        for ac in audit_checks:
            self.assertEqual(ac["status"], "not_run")
            self.assertEqual(ac["reason"], "independent audit")

        non_audit_checks = [c for c in summary["plan_checks"] if not c.get("audit")]
        self.assertEqual(len(non_audit_checks), 48)
        self.assertTrue(all(c["status"] == "passed" and c["steps"] for c in non_audit_checks))

    def test_step_failures_still_run_later_steps_and_stop_baseline(self) -> None:
        scenarios = [
            ("nonzero_exit", "target", {"behavior": "nonzero_exit", "exit_code": 1}),
            ("failing_report", "bundles", {"behavior": "failing_report", "exit_code": 0}),
            ("missing_report", "python-baseline", {"behavior": "missing_report", "exit_code": 0}),
            ("not_run_row", "rust-baseline", {"behavior": "not_run_row", "exit_code": 0}),
        ]
        for label, target_step, step_cfg in scenarios:
            with self.subTest(scenario=label):
                if self.tracker_path.is_file():
                    self.tracker_path.unlink()
                self.config_path.write_text(
                    json.dumps({"steps": {target_step: step_cfg}}) + "\n",
                    encoding="utf-8",
                )
                out_dir = self.acceptance_root / f"run-{label}"
                code = self._run_driver(out_dir)
                self.assertEqual(code, 1)

                ran = self._ran_steps()
                self.assertEqual(len(ran), 13, f"all 13 steps must still run on {label}")

                summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
                self.assertFalse(summary["steps_passed"])
                self.assertFalse(summary["passed"])
                self.assertEqual(summary["status"], "failed")

                steps_by_name = {s["step"]: s for s in summary["steps"]}
                self.assertFalse(steps_by_name[target_step]["passed"])
                for other_name, other_step in steps_by_name.items():
                    if other_name != target_step:
                        self.assertTrue(other_step["passed"], f"step {other_name} should pass in {label}")

                mapped_to_target = [
                    c for c in summary["plan_checks"] if target_step in c.get("steps", [])
                ]
                self.assertTrue(mapped_to_target)
                self.assertTrue(all(c["status"] == "failed" for c in mapped_to_target))

                header = json.loads((out_dir / "header.json").read_text(encoding="utf-8"))
                baseline_pid = header["baseline_before"]["pid"]
                self.assertFalse(is_pid_alive(baseline_pid))

    def test_refusals_exit_2_before_creating_out_or_running_steps(self) -> None:
        # 1. --out already exists
        existing_out = self.acceptance_root / "existing-run"
        existing_out.mkdir(parents=True, exist_ok=True)
        code = self._run_driver(existing_out)
        self.assertEqual(code, 2)
        self.assertEqual(self._ran_steps(), [])

        # 2. --out outside .local/typescript-output/acceptance/
        outside_out = self.root / ".local" / "typescript-output" / "outside-run"
        code = self._run_driver(outside_out)
        self.assertEqual(code, 2)
        self.assertFalse(outside_out.exists())
        self.assertEqual(self._ran_steps(), [])

        # 3. --baseline-port is 8765 or 18765 or candidate's port
        for bad_port in (8765, 18765, self.candidate_port):
            out_dir = self.acceptance_root / f"refuse-port-{bad_port}"
            argv = [
                "--candidate",
                str(self.candidate_dir),
                "--out",
                str(out_dir),
                "--baseline",
                str(self.baseline_dir),
                "--baseline-port",
                str(bad_port),
            ]
            code = self._run_driver(out_dir, extra_args=argv)
            self.assertEqual(code, 2)
            self.assertFalse(out_dir.exists())
            self.assertEqual(self._ran_steps(), [])

        # 4. --baseline-port already listening
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy_sock:
            busy_sock.bind(("127.0.0.1", 0))
            busy_sock.listen(1)
            busy_port = int(busy_sock.getsockname()[1])
            out_dir = self.acceptance_root / "refuse-listening"
            argv = [
                "--candidate",
                str(self.candidate_dir),
                "--out",
                str(out_dir),
                "--baseline",
                str(self.baseline_dir),
                "--baseline-port",
                str(busy_port),
            ]
            code = self._run_driver(out_dir, extra_args=argv)
            self.assertEqual(code, 2)
            self.assertFalse(out_dir.exists())
            self.assertEqual(self._ran_steps(), [])

        # 5. Baseline binary sha256 mismatch
        out_dir = self.acceptance_root / "refuse-baseline-sha"
        code = self._run_driver(
            out_dir,
            expected_baseline_sha="52658e13af1ac08659ef1dda7e333634f4ae3893731304e09edec6ca5f9b87c2",
        )
        self.assertEqual(code, 2)
        self.assertFalse(out_dir.exists())
        self.assertEqual(self._ran_steps(), [])

        # 6. Candidate identity problem
        bad_cand = self.root / ".local" / "typescript-output" / "bad-candidate"
        shutil.copytree(self.candidate_dir, bad_cand)
        (bad_cand / "som").write_text("#!/bin/sh\necho tampered\n", encoding="utf-8")
        out_dir = self.acceptance_root / "refuse-candidate-id"
        argv = [
            "--candidate",
            str(bad_cand),
            "--out",
            str(out_dir),
            "--baseline",
            str(self.baseline_dir),
            "--baseline-port",
            str(self.baseline_port),
        ]
        code = self._run_driver(out_dir, extra_args=argv)
        self.assertEqual(code, 2)
        self.assertFalse(out_dir.exists())
        self.assertEqual(self._ran_steps(), [])

        # Dirty tool roots (untracked file in one of the 7 tool roots)
        dirty_file = self.ts_acc / "uncommitted.py"
        dirty_file.write_text("# dirty\n", encoding="utf-8")
        try:
            out_dir = self.acceptance_root / "refuse-dirty-tools"
            code = self._run_driver(out_dir)
            self.assertEqual(code, 2)
            self.assertFalse(out_dir.exists())
            self.assertEqual(self._ran_steps(), [])
        finally:
            dirty_file.unlink()

    def test_judge_exception_fails_step_and_continues(self) -> None:
        out_dir = self.acceptance_root / "run-judge-exc"
        orig_steps = run_acceptance.steps

        def bad_judge(_receipt: dict[str, Any], _run: pathlib.Path) -> tuple[bool, dict[str, Any]]:
            raise RuntimeError("boom in judge")

        def patched_steps(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            step_list = orig_steps(*args, **kwargs)
            step_list[1]["judge"] = bad_judge
            return step_list

        with mock.patch.object(run_acceptance, "steps", side_effect=patched_steps):
            code = self._run_driver(out_dir)
        self.assertEqual(code, 1)
        self.assertEqual(len(self._ran_steps()), 13)
        rec = json.loads((out_dir / "receipts" / "02-rt-diagnostics.json").read_text(encoding="utf-8"))
        self.assertFalse(rec["passed"])
        self.assertIn("judge_error", rec["summary"])

    def test_keyboard_interrupt_during_steps_stops_baseline_pid_only(self) -> None:
        out_dir = self.acceptance_root / "run-ctrl-c"
        captured_pid: list[int] = []
        orig_run_step = run_acceptance.run_step

        def interrupt_on_step_3(index: int, step: dict[str, Any], run: pathlib.Path, env_delta: dict[str, str]) -> dict[str, Any]:
            if index == 3:
                h_doc = json.loads((run / "header.json").read_text(encoding="utf-8"))
                captured_pid.append(h_doc["baseline_before"]["pid"])
                raise KeyboardInterrupt("simulated Ctrl-C")
            return orig_run_step(index, step, run, env_delta)

        with (
            self._patched_env(),
            mock.patch.object(run_acceptance, "run_step", side_effect=interrupt_on_step_3),
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_acceptance.main([
                    "--candidate",
                    str(self.candidate_dir),
                    "--out",
                    str(out_dir),
                    "--baseline",
                    str(self.baseline_dir),
                    "--baseline-port",
                    str(self.baseline_port),
                ])

        self.assertEqual(len(captured_pid), 1)
        self.assertFalse(is_pid_alive(captured_pid[0]))
        self.assertTrue(is_pid_alive(self.cand_pid))


if __name__ == "__main__":
    unittest.main()
