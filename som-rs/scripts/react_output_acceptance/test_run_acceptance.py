#!/usr/bin/env python3
"""Unit tests for som-rs/scripts/react_output_acceptance/run_acceptance.py (issue #136)."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import http.server
import io
import json
import os
import pathlib
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from typing import Any
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_acceptance  # noqa: E402


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _HealthzStubServer:
    """Local ephemeral HTTP server serving /healthz with configurable response payload."""

    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload = dict(payload)
        self.status = status
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/healthz":
                    body = json.dumps(parent.payload, sort_keys=True).encode("utf-8")
                    self.send_response(parent.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)


class RunAcceptanceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="som-react-run-acc-")
        self.root = pathlib.Path(self._tmp.name).resolve()
        self.som_rs = self.root / "som-rs"

        self.cand_server = _HealthzStubServer({"ok": True, "role": "candidate", "version": "1"})
        self.base_server = _HealthzStubServer({"ok": True, "role": "baseline", "version": "1"})
        self.p8765_server = _HealthzStubServer({"ok": True, "role": "default-8765", "version": "1"})

        self._init_fake_repo()
        self._init_candidate()

    def tearDown(self) -> None:
        self.cand_server.close()
        self.base_server.close()
        self.p8765_server.close()
        self._tmp.cleanup()

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout

    def _write_executable(self, path: pathlib.Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _init_fake_repo(self) -> None:
        self.react_acc = self.som_rs / "scripts" / "react_output_acceptance"
        self.ts_acc = self.som_rs / "scripts" / "typescript_output_acceptance"
        self.react_tests = self.som_rs / "tests" / "react-output-v1"
        self.rust_tests = self.som_rs / "tests" / "rust-output-v1"
        self.ts_tests = self.som_rs / "tests" / "typescript-output-v1"
        self.react_contract = self.som_rs / "contracts" / "react-output-v1"
        self.evidence_dir = self.som_rs / "evidence"
        self.legacy_run = self.root / ".local" / "rust-compiler-goal" / "acceptance" / "legacy544" / "run-005"
        self.venv_python = self.som_rs / "runtime" / "acceptance-m3-venv" / "bin" / "python"

        for d in (
            self.react_acc,
            self.ts_acc,
            self.react_tests,
            self.rust_tests,
            self.ts_tests,
            self.react_contract,
            self.evidence_dir / "react-output-v1",
            self.legacy_run,
            self.venv_python.parent,
        ):
            d.mkdir(parents=True, exist_ok=True)

        # Wrapper for --python that delegates to sys.executable
        self._write_executable(
            self.venv_python,
            f"#!/bin/sh\nexec {json.dumps(sys.executable)} \"$@\"\n",
        )

        # Contract & evidence files
        (self.react_contract / "acceptance-plan.json").write_text(
            json.dumps({"version": "1.0.0"}, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.evidence_dir / "react-output-v1" / "python-controls-2026-10-22.json").write_text(
            json.dumps({"passed": 32, "failed": []}, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.evidence_dir / "react-output-contract-freeze-2026-10-22.json").write_text(
            json.dumps({"status": "contract_frozen_before_implementation"}, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.evidence_dir / "react-output-contract-amendment-001.json").write_text(
            json.dumps({"amendment": 1, "status": "amendment_sealed_after_independent_review"}, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.evidence_dir / "react-output-contract-amendment-002.json").write_text(
            json.dumps({"amendment": 2, "status": "amendment_sealed_after_independent_review"}, indent=2) + "\n",
            encoding="utf-8",
        )

        # Fake step scripts driven by FAKE_STEP_CONFIG_JSON env or file in self.root / ".step_config.json"
        driver_code = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

STEP_NAME = __STEP_NAME__
ROOT = pathlib.Path(__file__).resolve()
while not (ROOT / ".git").exists() and ROOT.parent != ROOT:
    ROOT = ROOT.parent

cfg_path = ROOT / ".local" / "step_config.json"
cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
step_cfg = cfg.get("steps", {}).get(STEP_NAME, {})

log_path = ROOT / ".local" / "invocations.jsonl"
log_path.parent.mkdir(parents=True, exist_ok=True)

# Check that identity.json exists before step 1 and previous receipts exist
args = sys.argv[1:]
out_val = None
for flag in ("--out", "--evidence"):
    if flag in args:
        out_val = args[args.index(flag) + 1]
        break

record = {
    "step": STEP_NAME,
    "argv": sys.argv,
    "cwd": os.getcwd(),
}
with log_path.open("a", encoding="utf-8") as f:
    f.write(json.dumps(record) + "\\n")

if STEP_NAME == "contract":
    stdout_text = step_cfg.get("stdout", "512 passed, 0 failed\\n")
    sys.stdout.write(stdout_text)
    raise SystemExit(int(step_cfg.get("exit_code", 0)))

assert out_val is not None, f"missing --out/--evidence in {sys.argv}"
out_path = pathlib.Path(out_val)

if STEP_NAME in ("react-python-controls", "rust-python-controls", "ts-python-controls"):
    report_payload = step_cfg.get("report", {"passed": 32, "failed": [], "results": [{"id": i} for i in range(32)]})
    if step_cfg.get("write_report", True):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(step_cfg.get("raw_report", json.dumps(report_payload, indent=2) + "\\n"), encoding="utf-8")
else:
    out_path.mkdir(parents=True, exist_ok=True)
    report_payload = step_cfg.get("report", {"passed": True, "status": "passed", "failed": [], "problems": []})
    if step_cfg.get("write_report", True):
        (out_path / "report.json").write_text(step_cfg.get("raw_report", json.dumps(report_payload, indent=2) + "\\n"), encoding="utf-8")
    if STEP_NAME == "bundles" and step_cfg.get("write_manifest", True):
        manifest_dir = out_path / "products"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps({"products": [{"id": "example-01"}]}, indent=2) + "\\n",
            encoding="utf-8",
        )

sys.stdout.write(json.dumps({"step": STEP_NAME, "ok": True}) + "\\n")
raise SystemExit(int(step_cfg.get("exit_code", 0)))
"""

        script_map = {
            self.react_tests / "check_contract.py": "contract",
            self.react_acc / "check_target.py": "react-target",
            self.react_tests / "check_python_controls.py": "react-python-controls",
            self.ts_acc / "check_python_baseline.py": "python-baseline",
            self.ts_acc / "check_rust_baseline.py": "rust-baseline",
            self.rust_tests / "check_python_controls.py": "rust-python-controls",
            self.ts_acc / "check_rt_diagnostics.py": "rt-diagnostics",
            self.ts_acc / "check_target.py": "ts-target",
            self.react_acc / "check_typescript_baseline.py": "ts-baseline",
            self.ts_tests / "check_python_controls.py": "ts-python-controls",
            self.react_acc / "check_bundles.py": "bundles",
            self.react_acc / "check_products.py": "products",
        }
        for path, step_name in script_map.items():
            self._write_executable(path, driver_code.replace("__STEP_NAME__", json.dumps(step_name)))

        self._git("init")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self._git("add", "som-rs")
        self._git("commit", "-m", "initial fake repo")
        self.head = self._git("rev-parse", "HEAD").strip()

    def _init_candidate(self) -> None:
        self.candidate_dir = self.root / ".local" / "candidate-001"
        self.candidate_dir.mkdir(parents=True, exist_ok=True)
        som_bin = self.candidate_dir / "som"
        som_bin.write_bytes(b"#!/bin/sh\necho fake-som\n")
        som_bin.chmod(0o755)
        self.binary_sha256 = _sha_bytes(som_bin.read_bytes())

        self.build_doc: dict[str, Any] = {
            "candidate": str(self.candidate_dir),
            "complete": True,
            "binary_sha256": self.binary_sha256,
            "tree_before": {
                "head": self.head,
                "porcelain": "",
            },
        }
        self.runtime_doc: dict[str, Any] = {
            "candidate": str(self.candidate_dir),
            "port": self.cand_server.port,
            "pid": os.getpid(),
            "binary_sha256": self.binary_sha256,
            "health": {"ok": True, "role": "candidate", "version": "1"},
        }
        self._write_candidate_docs()

    def _write_candidate_docs(self) -> None:
        (self.candidate_dir / "build.json").write_text(
            json.dumps(self.build_doc, indent=2) + "\n", encoding="utf-8"
        )
        (self.candidate_dir / "runtime.json").write_text(
            json.dumps(self.runtime_doc, indent=2) + "\n", encoding="utf-8"
        )

    def _set_step_config(self, steps_cfg: dict[str, Any]) -> None:
        cfg_path = self.root / ".local" / "step_config.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps({"steps": steps_cfg}, indent=2) + "\n", encoding="utf-8")

    def _read_invocations(self) -> list[dict[str, Any]]:
        log_path = self.root / ".local" / "invocations.jsonl"
        if not log_path.is_file():
            return []
        return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with (
            mock.patch.object(run_acceptance, "REPO", self.root),
            mock.patch.object(run_acceptance, "SOM_RS", self.som_rs),
            mock.patch.object(run_acceptance, "HERE", self.react_acc),
            mock.patch.object(run_acceptance, "DEFAULT_8765_URL", self.p8765_server.url),
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            try:
                rc = run_acceptance.main(argv)
            except SystemExit as exc:
                rc = int(exc.code) if isinstance(exc.code, int) else 2
        return rc, stdout_buf.getvalue(), stderr_buf.getvalue()

    def _run_main_with_protected_errors(
        self,
        argv: list[str],
        errors: list[OSError | None],
    ) -> tuple[int, int]:
        original_open = run_acceptance.NO_PROXY_OPENER.open
        probes = iter(errors)
        protected_requests = []

        def probe(request: Any, *args: Any, **kwargs: Any) -> Any:
            if request.full_url == f"{self.p8765_server.url}/healthz":
                protected_requests.append(request)
                error = next(probes)
                if error is not None:
                    raise error
            return original_open(request, *args, **kwargs)

        with mock.patch.object(run_acceptance.NO_PROXY_OPENER, "open", side_effect=probe):
            rc, _out, _err = self._run_main(argv)
        self.assertTrue(all(request.get_method() == "GET" for request in protected_requests))
        return rc, len(protected_requests)

    def test_1_happy_path_all_12_steps(self) -> None:
        out_dir = self.root / ".local" / "run-happy"
        rc, _out, err = self._run_main(
            [
                "--candidate",
                str(self.candidate_dir),
                "--out",
                str(out_dir),
                "--baseline",
                self.base_server.url,
            ]
        )
        self.assertEqual(rc, 0, msg=f"stderr: {err}")

        invocations = self._read_invocations()
        expected_step_order = [
            "contract",
            "react-target",
            "react-python-controls",
            "python-baseline",
            "rust-baseline",
            "rust-python-controls",
            "rt-diagnostics",
            "ts-target",
            "ts-baseline",
            "ts-python-controls",
            "bundles",
            "products",
        ]
        self.assertEqual([item["step"] for item in invocations], expected_step_order)
        for item in invocations:
            self.assertEqual(pathlib.Path(item["cwd"]).resolve(), self.root)

        # Verify expected argv for all 12 steps
        expected_python = str(self.venv_python)
        expected_u = self.cand_server.url
        expected_b = self.base_server.url
        expected_l = ".local/rust-compiler-goal/acceptance/legacy544/run-005"

        self.assertEqual(
            invocations[0]["argv"][1:],
            [
                "--require-prompts",
                "--receipt",
                "som-rs/evidence/react-output-contract-amendment-002.json",
            ],
        )
        self.assertEqual(
            invocations[1]["argv"][1:],
            [
                "--url",
                expected_u,
                "--out",
                str(out_dir / "react-target"),
                "--binary",
                str(self.candidate_dir / "som"),
                "--legacy-run",
                expected_l,
            ],
        )
        self.assertEqual(
            invocations[2]["argv"][1:],
            [
                "--url",
                expected_u,
                "--expect",
                "som-rs/evidence/react-output-v1/python-controls-2026-10-22.json",
                "--evidence",
                str(out_dir / "react-python-controls.json"),
            ],
        )
        self.assertEqual(
            invocations[3]["argv"][1:],
            [
                "--qa",
                expected_u,
                "--baseline",
                expected_b,
                "--out",
                str(out_dir / "python-baseline"),
                "--legacy-run",
                expected_l,
            ],
        )
        self.assertEqual(
            invocations[4]["argv"][1:],
            [
                "--qa",
                expected_u,
                "--baseline",
                expected_b,
                "--out",
                str(out_dir / "rust-baseline"),
            ],
        )
        self.assertEqual(
            invocations[5]["argv"][1:],
            [
                "--base-url",
                expected_u,
                "--out",
                str(out_dir / "rust-python-controls.json"),
            ],
        )
        self.assertEqual(
            invocations[6]["argv"][1:],
            [
                "--url",
                expected_u,
                "--out",
                str(out_dir / "rt-diagnostics"),
            ],
        )
        self.assertEqual(
            invocations[7]["argv"][1:],
            [
                "--url",
                expected_u,
                "--out",
                str(out_dir / "ts-target"),
                "--legacy-run",
                expected_l,
            ],
        )
        self.assertEqual(
            invocations[8]["argv"][1:],
            [
                "--qa",
                expected_u,
                "--baseline",
                expected_b,
                "--out",
                str(out_dir / "ts-baseline"),
                "--legacy-run",
                expected_l,
            ],
        )
        self.assertEqual(
            invocations[9]["argv"][1:],
            [
                "--url",
                expected_u,
                "--evidence",
                str(out_dir / "ts-python-controls.json"),
            ],
        )
        self.assertEqual(
            invocations[10]["argv"][1:],
            [
                "--url",
                expected_u,
                "--out",
                str(out_dir / "bundles"),
                "--legacy-run",
                expected_l,
            ],
        )
        self.assertEqual(
            invocations[11]["argv"][1:],
            [
                "--url",
                expected_u,
                "--manifest",
                str(out_dir / "bundles" / "products" / "manifest.json"),
                "--out",
                str(out_dir / "products"),
            ],
        )

        # Verify receipts, logs, identity.json, and run.json
        run_doc = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(run_doc["passed"])
        self.assertEqual(run_doc["status"], "passed")
        self.assertEqual(run_doc["not_run"], [])
        self.assertEqual(run_doc["failed"], [])
        self.assertEqual(run_doc["python"], expected_python)
        self.assertEqual(len(run_doc["steps"]), 12)

        for idx, step_name in enumerate(expected_step_order, 1):
            receipt_file = out_dir / "receipts" / f"{idx:02d}-{step_name}.json"
            self.assertTrue(receipt_file.is_file(), f"missing {receipt_file}")
            receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(receipt["step"], step_name)
            self.assertEqual(receipt["index"], idx)
            self.assertEqual(receipt["judgment"], "passed")
            self.assertEqual(receipt["exit_code"], 0)
            for field in ("stdout", "stderr", "report"):
                self.assertEqual(
                    receipt[f"{field}_sha256"],
                    _sha_bytes(pathlib.Path(receipt[field]).read_bytes()),
                )
            self.assertEqual(receipt["argv"][0], expected_python)

        identity_doc = json.loads((out_dir / "identity.json").read_text(encoding="utf-8"))
        self.assertTrue(identity_doc["passed"])
        self.assertTrue(identity_doc["candidate"]["healthz"]["unchanged"])
        self.assertTrue(identity_doc["baseline"]["healthz"]["unchanged"])
        self.assertTrue(identity_doc["8765"]["healthz"]["unchanged"])
        self.assertTrue(identity_doc["git_head"]["unchanged"])
        self.assertTrue(identity_doc["react_output_acceptance_files"]["unchanged"])

    def test_2_latest_contract_receipt_selection(self) -> None:
        # With freeze, amendment-001 (sealed), and amendment-002 (sealed) -> picks amendment-002
        picked = run_acceptance.find_latest_contract_receipt(self.root)
        self.assertEqual(picked.name, "react-output-contract-amendment-002.json")

        # Add an unsealed draft amendment-003 -> ignored, still picks amendment-002
        (self.evidence_dir / "react-output-contract-amendment-003.json").write_text(
            json.dumps({"amendment": 3, "status": "draft_pending_review"}, indent=2) + "\n",
            encoding="utf-8",
        )
        picked_with_draft = run_acceptance.find_latest_contract_receipt(self.root)
        self.assertEqual(picked_with_draft.name, "react-output-contract-amendment-002.json")

        # Mark amendment-001 and amendment-002 as unsealed -> falls back to initial freeze receipt
        for name in ("react-output-contract-amendment-001.json", "react-output-contract-amendment-002.json"):
            (self.evidence_dir / name).write_text(
                json.dumps({"status": "draft"}, indent=2) + "\n",
                encoding="utf-8",
            )
        fallback = run_acceptance.find_latest_contract_receipt(self.root)
        self.assertEqual(fallback.name, "react-output-contract-freeze-2026-10-22.json")

    def test_3_refusals_exit_2_before_step_1(self) -> None:
        # 3a. Existing --out
        existing_out = self.root / ".local" / "existing-out"
        existing_out.mkdir(parents=True)
        rc, _, err = self._run_main(
            ["--candidate", str(self.candidate_dir), "--out", str(existing_out), "--baseline", self.base_server.url]
        )
        self.assertEqual(rc, 2)
        self.assertIn("--out already exists", err)
        self.assertEqual(self._read_invocations(), [])

        # 3b. Forbidden baseline port 8765 and 18765
        for forbidden_port in (8765, 18765):
            out_dir = self.root / ".local" / f"out-base-{forbidden_port}"
            rc, _, err = self._run_main(
                [
                    "--candidate",
                    str(self.candidate_dir),
                    "--out",
                    str(out_dir),
                    "--baseline",
                    f"http://127.0.0.1:{forbidden_port}",
                ]
            )
            self.assertEqual(rc, 2)
            self.assertIn(str(forbidden_port), err)
            self.assertFalse(out_dir.exists())
            self.assertEqual(self._read_invocations(), [])

        # 3c. Forbidden candidate port 8765 and 18765
        for forbidden_port in (8765, 18765):
            self.runtime_doc["port"] = forbidden_port
            self._write_candidate_docs()
            out_dir = self.root / ".local" / f"out-cand-{forbidden_port}"
            rc, _, err = self._run_main(
                ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
            )
            self.assertEqual(rc, 2)
            self.assertIn(str(forbidden_port), err)
            self.assertFalse(out_dir.exists())
            self.assertEqual(self._read_invocations(), [])
        self.runtime_doc["port"] = self.cand_server.port
        self._write_candidate_docs()

        # 3d. build.json complete != True
        self.build_doc["complete"] = False
        self._write_candidate_docs()
        out_dir = self.root / ".local" / "out-incomplete"
        rc, _, err = self._run_main(
            ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
        )
        self.assertEqual(rc, 2)
        self.assertIn("complete", err)
        self.assertEqual(self._read_invocations(), [])
        self.build_doc["complete"] = True
        self._write_candidate_docs()

        # 3e. Dirty tree_before.porcelain
        self.build_doc["tree_before"]["porcelain"] = " M som-rs/Cargo.toml\n"
        self._write_candidate_docs()
        out_dir = self.root / ".local" / "out-dirty-porcelain"
        rc, _, err = self._run_main(
            ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
        )
        self.assertEqual(rc, 2)
        self.assertIn("porcelain", err)
        self.assertEqual(self._read_invocations(), [])
        self.build_doc["tree_before"]["porcelain"] = ""
        self._write_candidate_docs()

        # 3f. Mismatched tree_before.head vs git rev-parse HEAD
        self.build_doc["tree_before"]["head"] = "0" * 40
        self._write_candidate_docs()
        out_dir = self.root / ".local" / "out-bad-head"
        rc, _, err = self._run_main(
            ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
        )
        self.assertEqual(rc, 2)
        self.assertIn("tree_before.head", err)
        self.assertEqual(self._read_invocations(), [])
        self.build_doc["tree_before"]["head"] = self.head
        self._write_candidate_docs()

        # 3g. Dirty acceptance script tree
        untracked = self.react_acc / "dirty_untracked.py"
        untracked.write_text("# dirty\n", encoding="utf-8")
        out_dir = self.root / ".local" / "out-dirty-scripts"
        rc, _, err = self._run_main(
            ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
        )
        self.assertEqual(rc, 2)
        self.assertIn("dirty working tree", err)
        self.assertEqual(self._read_invocations(), [])
        # Restore clean git status by staging and resetting untracked file via git clean
        self._git("clean", "-fd", "--", "som-rs/scripts/react_output_acceptance")

        # 3h. Mismatched binary_sha256 in build.json and runtime.json
        self.runtime_doc["binary_sha256"] = "f" * 64
        self._write_candidate_docs()
        out_dir = self.root / ".local" / "out-bad-bin-sha"
        rc, _, err = self._run_main(
            ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
        )
        self.assertEqual(rc, 2)
        self.assertIn("binary_sha256", err)
        self.assertEqual(self._read_invocations(), [])
        self.runtime_doc["binary_sha256"] = self.binary_sha256
        self._write_candidate_docs()

        self.build_doc["binary_sha256"] = "f" * 64
        self._write_candidate_docs()
        out_dir = self.root / ".local" / "out-bad-build-bin-sha"
        rc, _, err = self._run_main(
            ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
        )
        self.assertEqual(rc, 2)
        self.assertIn("build.json binary_sha256", err)
        self.assertFalse(out_dir.exists())
        self.assertEqual(self._read_invocations(), [])
        self.build_doc["binary_sha256"] = self.binary_sha256
        self._write_candidate_docs()

    def test_4_step_failure_judged_from_report_not_exit_code_alone(self) -> None:
        # rt-diagnostics exits 0, but writes passed: false in report.json
        self._set_step_config(
            {
                "rt-diagnostics": {
                    "exit_code": 0,
                    "report": {"passed": False, "status": "failed", "problems": ["RT03 mismatch"]},
                }
            }
        )
        out_dir = self.root / ".local" / "run-step-fail"
        rc, _, _ = self._run_main(
            ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
        )
        self.assertEqual(rc, 1)

        receipt = json.loads((out_dir / "receipts" / "07-rt-diagnostics.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["judgment"], "failed")
        self.assertFalse(receipt["passed"])

        run_doc = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
        self.assertFalse(run_doc["passed"])
        self.assertEqual(run_doc["failed"], ["rt-diagnostics"])
        # Subsequent independent steps still ran
        self.assertEqual(len(self._read_invocations()), 12)

    def test_5_missing_bundles_manifest_records_products_not_run(self) -> None:
        self._set_step_config(
            {
                "bundles": {
                    "exit_code": 1,
                    "report": {"passed": False, "status": "failed"},
                    "write_manifest": False,
                }
            }
        )
        out_dir = self.root / ".local" / "run-no-manifest"
        rc, _, _ = self._run_main(
            ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
        )
        self.assertEqual(rc, 1)

        invocations = self._read_invocations()
        self.assertNotIn("products", [item["step"] for item in invocations])

        prod_receipt = json.loads((out_dir / "receipts" / "12-products.json").read_text(encoding="utf-8"))
        self.assertEqual(prod_receipt["judgment"], "not_run")
        self.assertIsNone(prod_receipt["exit_code"])
        self.assertIn("manifest", prod_receipt["reason"])

        run_doc = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
        self.assertFalse(run_doc["passed"])
        self.assertEqual(run_doc["failed"], ["bundles"])
        self.assertEqual(run_doc["not_run"], ["products"])

    def test_6_identity_drift_fails_run(self) -> None:
        for target_name, server in (
            ("candidate", self.cand_server),
            ("baseline", self.base_server),
            ("8765", self.p8765_server),
        ):
            with self.subTest(endpoint=target_name):
                orig_version = server.payload["version"]
                orig_run_single_step = run_acceptance.run_single_step

                def _mutate_on_last_step(
                    step_def: dict[str, Any],
                    out_dir_arg: pathlib.Path,
                    repo_arg: pathlib.Path,
                    _srv: _HealthzStubServer = server,
                ) -> dict[str, Any]:
                    res = orig_run_single_step(step_def, out_dir_arg, repo_arg)
                    if step_def["index"] == 12:
                        _srv.payload["version"] = "drifted-during-run"
                    return res

                out_dir = self.root / ".local" / f"run-drift-{target_name}"
                with mock.patch.object(run_acceptance, "run_single_step", side_effect=_mutate_on_last_step):
                    rc, _, _ = self._run_main(
                        [
                            "--candidate",
                            str(self.candidate_dir),
                            "--out",
                            str(out_dir),
                            "--baseline",
                            self.base_server.url,
                        ]
                    )
                server.payload["version"] = orig_version

                self.assertEqual(rc, 1)
                identity_doc = json.loads((out_dir / "identity.json").read_text(encoding="utf-8"))
                self.assertFalse(identity_doc["passed"])
                self.assertFalse(identity_doc[target_name]["healthz"]["unchanged"])
                run_doc = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
                self.assertFalse(run_doc["passed"])
                self.assertFalse(run_doc["identity_passed"])

    def test_7_missing_and_unparseable_reports_fail_without_stopping_steps(self) -> None:
        cases = (
            ("react-target", 2, {"write_report": False}, "missing report"),
            ("python-baseline", 4, {"raw_report": "{broken"}, "invalid JSON"),
            ("react-python-controls", 3, {"write_report": False}, "missing report"),
            ("rust-python-controls", 6, {"raw_report": "{broken"}, "invalid JSON"),
        )
        for step_name, index, config, reason in cases:
            with self.subTest(step=step_name):
                self._set_step_config({step_name: config})
                before = len(self._read_invocations())
                out_dir = self.root / ".local" / f"run-bad-report-{step_name}"
                rc, _, _ = self._run_main(
                    ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
                )
                self.assertEqual(rc, 1)
                self.assertEqual(len(self._read_invocations()) - before, 12)
                receipt = json.loads((out_dir / "receipts" / f"{index:02d}-{step_name}.json").read_text())
                self.assertEqual(receipt["judgment"], "failed")
                self.assertEqual(receipt["exit_code"], 0)
                self.assertIn(reason, receipt["reason"])
                for field in ("stdout", "stderr"):
                    self.assertEqual(receipt[f"{field}_sha256"], _sha_bytes(pathlib.Path(receipt[field]).read_bytes()))
                report = pathlib.Path(receipt["report"])
                self.assertEqual(receipt["report_sha256"], _sha_bytes(report.read_bytes()) if report.is_file() else None)
                run_doc = json.loads((out_dir / "run.json").read_text())
                self.assertEqual(run_doc["failed"], [step_name])
                self.assertEqual(run_doc["not_run"], [])

    def test_8_launch_failure_is_recorded_and_later_steps_continue(self) -> None:
        original_run = subprocess.run
        for step_name, index, script in (
            ("react-target", 2, "som-rs/scripts/react_output_acceptance/check_target.py"),
            ("bundles", 11, "som-rs/scripts/react_output_acceptance/check_bundles.py"),
        ):
            with self.subTest(step=step_name):
                def fail_launch(argv: list[str], *args: Any, **kwargs: Any) -> Any:
                    if len(argv) > 1 and argv[1] == script:
                        raise FileNotFoundError(2, "fixture launch failed", argv[0])
                    return original_run(argv, *args, **kwargs)

                before = len(self._read_invocations())
                out_dir = self.root / ".local" / f"run-launch-fail-{step_name}"
                with mock.patch.object(run_acceptance.subprocess, "run", side_effect=fail_launch):
                    rc, _, _ = self._run_main(
                        ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url]
                    )
                self.assertEqual(rc, 1)
                receipt = json.loads((out_dir / "receipts" / f"{index:02d}-{step_name}.json").read_text())
                self.assertEqual(receipt["judgment"], "failed")
                self.assertEqual(receipt["exit_code"], 127)
                self.assertIn("cannot start step", receipt["reason"])
                self.assertIn("FileNotFoundError", pathlib.Path(receipt["stderr"]).read_text())
                for field in ("stdout", "stderr"):
                    self.assertEqual(receipt[f"{field}_sha256"], _sha_bytes(pathlib.Path(receipt[field]).read_bytes()))
                run_doc = json.loads((out_dir / "run.json").read_text())
                self.assertEqual(len(run_doc["steps"]), 12)
                self.assertEqual(run_doc["failed"], [step_name])
                expected_not_run = ["products"] if step_name == "bundles" else []
                self.assertEqual(run_doc["not_run"], expected_not_run)
                invoked = self._read_invocations()[before:]
                self.assertEqual(len(invoked), 10 if step_name == "bundles" else 11)
                self.assertEqual(invoked[-1]["step"], "ts-python-controls" if step_name == "bundles" else "products")

    def test_9_controls_require_complete_counts_and_lists(self) -> None:
        report = self.root / ".local" / "controls-report.json"
        invalid = (
            {"passed": True, "failed": [], "results": [{}]},
            {"passed": 1, "results": [{}]},
            {"passed": 1, "failed": 0, "results": [{}]},
            {"passed": 1, "failed": [], "results": None},
            {"passed": 1, "failed": []},
            {"passed": 0, "failed": [], "results": []},
            {"passed": 1, "failed": [], "results": []},
            {"passed": 1, "failed": ["failed case"], "results": [{}]},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                report.write_text(json.dumps(payload))
                self.assertEqual(run_acceptance.judge_json_report(0, report, "controls")[0], "failed")
        report.write_text(json.dumps({"passed": 1, "failed": [], "results": [{"id": "case"}]}))
        self.assertEqual(run_acceptance.judge_json_report(0, report, "controls")[0], "passed")
        self.assertEqual(run_acceptance.judge_json_report(0, report)[0], "failed")
        report.write_text(json.dumps({"passed": True}))
        self.assertEqual(run_acceptance.judge_json_report(0, report)[0], "passed")

    def test_product_cleanup_option_preserves_all_twelve_gate_commands(self) -> None:
        options = {
            "python_bin": "python3", "candidate_dir": self.candidate_dir,
            "candidate_url": "http://127.0.0.1:18919",
            "baseline_url": "http://127.0.0.1:18917",
            "legacy_run": str(self.legacy_run), "out_dir": self.root / ".local/run",
            "contract_receipt": "receipt.json",
        }
        original = run_acceptance.build_steps(**options)
        cleaned = run_acceptance.build_steps(**options, cleanup_products_runtime=True)
        self.assertEqual(len(cleaned), 12)
        self.assertEqual(cleaned[:-1], original[:-1])
        self.assertEqual(cleaned[-1]["step"], "products")
        self.assertEqual(cleaned[-1]["argv"], [*original[-1]["argv"], "--cleanup-runtime"])
        parsed = run_acceptance.parse_args([
            "--candidate", str(self.candidate_dir), "--out", str(self.root / ".local/run"),
            "--cleanup-products-runtime",
        ])
        self.assertTrue(parsed.cleanup_products_runtime)

    def test_rust_baseline_run_override_changes_only_its_gate(self) -> None:
        options = {
            "python_bin": "python3", "candidate_dir": self.candidate_dir,
            "candidate_url": "http://127.0.0.1:18919", "baseline_url": "http://127.0.0.1:18917",
            "legacy_run": str(self.legacy_run), "out_dir": self.root / ".local/run",
            "contract_receipt": "receipt.json",
        }
        alternate = self.root / ".local/m6-hosted/restored-rust-baseline"
        original = run_acceptance.build_steps(**options)
        changed = run_acceptance.build_steps(**options, rust_baseline_run=str(alternate))
        self.assertEqual(len(changed), 12)
        for before, after in zip(original, changed):
            if before["step"] == "rust-baseline":
                self.assertEqual(after, {**before, "argv": [*before["argv"], "--run", str(alternate)]})
            else:
                self.assertEqual(before, after)
        base_args = ["--candidate", str(self.candidate_dir), "--out", str(self.root / ".local/run")]
        self.assertIsNone(run_acceptance.parse_args(base_args).rust_baseline_run)
        self.assertEqual(run_acceptance.parse_args([*base_args, "--rust-baseline-run", str(alternate)]).rust_baseline_run, alternate)

        out_dir = self.root / ".local/run-rust-override"
        rc, _out, err = self._run_main([
            "--candidate", str(self.candidate_dir), "--out", str(out_dir),
            "--baseline", self.base_server.url, "--rust-baseline-run", str(alternate),
        ])
        self.assertEqual(rc, 0, msg=err)
        invocations = self._read_invocations()
        self.assertEqual(len(invocations), 12)
        self.assertEqual(invocations[4]["argv"][-2:], ["--run", str(alternate)])
        self.assertFalse(any("--run" in item["argv"] for item in invocations if item["step"] != "rust-baseline"))
        self.assertEqual(json.loads((out_dir / "run.json").read_text())["rust_baseline_run"], str(alternate))

    def test_raw_recording_wraps_only_five_frozen_http_collectors(self) -> None:
        options = {"python_bin": "python3", "candidate_dir": self.candidate_dir,
                   "candidate_url": "http://127.0.0.1:18919", "baseline_url": "http://127.0.0.1:18917",
                   "legacy_run": str(self.legacy_run), "out_dir": self.root / ".local/run",
                   "contract_receipt": "receipt.json"}
        original = run_acceptance.build_steps(**options)
        recorded = run_acceptance.build_steps(**options, record_raw_http=True)
        chosen = {"react-python-controls", "python-baseline", "rust-baseline", "rust-python-controls", "ts-python-controls"}
        self.assertEqual([step["step"] for step in recorded], [step["step"] for step in original])
        for before, after in zip(original, recorded):
            with self.subTest(step=before["step"]):
                if before["step"] in chosen:
                    self.assertEqual(after["argv"], ["python3", str(run_acceptance.HERE / "record_http.py"),
                        "--out", str(options["out_dir"] / f"{before['step']}-raw"), "--", *before["argv"][1:]])
                    self.assertEqual({key: value for key, value in after.items() if key != "argv"},
                                     {key: value for key, value in before.items() if key != "argv"})
                else:
                    self.assertEqual(before, after)

    def test_10_loopback_url_validation_preserves_ipv6(self) -> None:
        self.assertEqual(
            run_acceptance.parse_and_validate_url("http://[::1]:18960", "--baseline"),
            ("http://[::1]:18960", 18960),
        )
        for url in (
            "http://user@127.0.0.1:18960",
            "http://127.0.0.1:18960/other",
            "http://127.0.0.1:18960?query=value",
            "http://127.0.0.1:18960#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                run_acceptance.parse_and_validate_url(url, "--baseline")

    def test_11_explicit_absent_mode_runs_all_12_steps_and_records_refused_gets(self) -> None:
        out_dir = self.root / ".local" / "run-protected-absent"
        refused = urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"))
        original_write_json = run_acceptance.write_json
        with mock.patch.object(run_acceptance, "write_json", wraps=original_write_json) as write_json:
            rc, protected_gets = self._run_main_with_protected_errors(
                [
                    "--candidate", str(self.candidate_dir), "--out", str(out_dir),
                    "--baseline", self.base_server.url, "--protected-default", "absent",
                ],
                [refused, refused],
            )
        self.assertEqual(rc, 0)
        self.assertEqual(protected_gets, 2)
        self.assertEqual(len(self._read_invocations()), 12)
        initial_identity = write_json.call_args_list[0].args[1]
        self.assertEqual(initial_identity["8765"]["expected_state"], "absent")
        self.assertIsNone(initial_identity["8765"]["healthz"]["after"])
        identity_doc = json.loads((out_dir / "identity.json").read_text())
        self.assertTrue(identity_doc["passed"])
        self.assertEqual(identity_doc["8765"]["expected_state"], "absent")
        self.assertTrue(identity_doc["8765"]["healthz"]["unchanged"])
        for label in ("before", "after"):
            record = identity_doc["8765"]["healthz"][label]
            self.assertEqual(record["url"], f"{self.p8765_server.url}/healthz")
            self.assertEqual(record["error_kind"], "connection_refused")
            self.assertEqual(record["error_type"], "ConnectionRefusedError")
            self.assertEqual(record["error_errno"], errno.ECONNREFUSED)
            self.assertTrue(record["error"])
            for field in ("status", "body", "raw", "sha256"):
                self.assertIsNone(record[field])
        for role in ("candidate", "baseline"):
            self.assertTrue(identity_doc[role]["healthz"]["unchanged"])
            self.assertNotIn("expected_state", identity_doc[role])
        run_doc = json.loads((out_dir / "run.json").read_text())
        self.assertTrue(run_doc["passed"])
        self.assertEqual(len(run_doc["steps"]), 12)
        self.assertEqual(run_doc["failed"], [])
        self.assertEqual(run_doc["not_run"], [])

    def test_12_default_healthy_mode_fails_when_protected_default_is_absent(self) -> None:
        out_dir = self.root / ".local" / "run-protected-unexpected-absence"
        refused = urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"))
        rc, protected_gets = self._run_main_with_protected_errors(
            ["--candidate", str(self.candidate_dir), "--out", str(out_dir), "--baseline", self.base_server.url],
            [refused, refused],
        )
        self.assertEqual(rc, 1)
        self.assertEqual(protected_gets, 2)
        self.assertEqual(len(self._read_invocations()), 12)
        identity_doc = json.loads((out_dir / "identity.json").read_text())
        self.assertFalse(identity_doc["passed"])
        self.assertFalse(identity_doc["8765"]["healthz"]["unchanged"])
        self.assertNotIn("expected_state", identity_doc["8765"])
        self.assertIn("8765 /healthz before returned status None", identity_doc["problems"])
        self.assertIn("8765 /healthz after returned status None", identity_doc["problems"])

    def test_13_absent_mode_fails_on_alive_transition_or_timeout(self) -> None:
        refused = urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"))
        timed_out = urllib.error.URLError(TimeoutError("timed out"))
        for name, errors in (
            ("absent-then-healthy", [refused, None]),
            ("healthy-then-absent", [None, refused]),
            ("healthy", [None, None]),
            ("timeout", [timed_out, timed_out]),
        ):
            with self.subTest(state=name):
                before_invocations = len(self._read_invocations())
                out_dir = self.root / ".local" / f"run-protected-{name}"
                rc, protected_gets = self._run_main_with_protected_errors(
                    [
                        "--candidate", str(self.candidate_dir), "--out", str(out_dir),
                        "--baseline", self.base_server.url, "--protected-default", "absent",
                    ],
                    errors,
                )
                self.assertEqual(rc, 1)
                self.assertEqual(protected_gets, 2)
                self.assertEqual(len(self._read_invocations()) - before_invocations, 12)
                identity_doc = json.loads((out_dir / "identity.json").read_text())
                self.assertFalse(identity_doc["passed"])
                self.assertFalse(identity_doc["8765"]["healthz"]["unchanged"])
                self.assertEqual(identity_doc["8765"]["expected_state"], "absent")
                self.assertTrue(any("OS connection-refused" in item for item in identity_doc["problems"]))
                run_doc = json.loads((out_dir / "run.json").read_text())
                self.assertFalse(run_doc["passed"])
                self.assertEqual(run_doc["failed"], [])
                self.assertEqual(run_doc["not_run"], [])


class FetchHealthzErrorTestCase(unittest.TestCase):
    def test_only_os_econnrefused_can_record_absence(self) -> None:
        for error in (
            ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
            urllib.error.URLError(OSError(errno.ECONNREFUSED, "Connection refused")),
        ):
            with self.subTest(error=error):
                with mock.patch.object(run_acceptance.NO_PROXY_OPENER, "open", side_effect=error) as open_url:
                    record = run_acceptance.fetch_healthz("http://127.0.0.1:8765")
                request = open_url.call_args.args[0]
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(request.full_url, "http://127.0.0.1:8765/healthz")
                self.assertEqual(record["error_kind"], "connection_refused")
                self.assertEqual(record["error_errno"], errno.ECONNREFUSED)
                self.assertTrue(run_acceptance.healthz_connection_refused(record))

    def test_timeout_dns_and_unknown_errors_do_not_record_absence(self) -> None:
        for error, expected_kind in (
            (TimeoutError("timed out"), "timeout"),
            (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
            (urllib.error.URLError(OSError(errno.ETIMEDOUT, "Connection timed out")), "timeout"),
            (urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "Name lookup failed")), "network_error"),
            (urllib.error.URLError("Connection refused"), "unknown_error"),
            (OSError("Unknown connection error"), "network_error"),
        ):
            with self.subTest(error=error):
                with mock.patch.object(run_acceptance.NO_PROXY_OPENER, "open", side_effect=error):
                    record = run_acceptance.fetch_healthz("http://127.0.0.1:8765")
                self.assertEqual(record["error_kind"], expected_kind)
                self.assertFalse(run_acceptance.healthz_connection_refused(record))

    def test_http_error_does_not_record_absence(self) -> None:
        error = urllib.error.HTTPError(
            "http://127.0.0.1:8765/healthz", 503, "Service unavailable", None, io.BytesIO(b'{"ok": false}')
        )
        with mock.patch.object(run_acceptance.NO_PROXY_OPENER, "open", side_effect=error):
            record = run_acceptance.fetch_healthz("http://127.0.0.1:8765")
        self.assertEqual(record["status"], 503)
        self.assertEqual(record["body"], {"ok": False})
        self.assertEqual(record["raw"], '{"ok": false}')
        self.assertEqual(record["sha256"], _sha_bytes(b'{"ok": false}'))
        self.assertIsNone(record["error"])
        self.assertNotIn("error_kind", record)
        self.assertFalse(run_acceptance.healthz_connection_refused(record))

    def test_healthy_response_keeps_existing_record_fields(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b'{"ok": true}'
        response.__enter__.return_value = response
        with mock.patch.object(run_acceptance.NO_PROXY_OPENER, "open", return_value=response):
            record = run_acceptance.fetch_healthz("http://127.0.0.1:8765")
        self.assertEqual(
            record,
            {
                "url": "http://127.0.0.1:8765/healthz", "status": 200, "body": {"ok": True},
                "raw": '{"ok": true}', "sha256": _sha_bytes(b'{"ok": true}'), "error": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
