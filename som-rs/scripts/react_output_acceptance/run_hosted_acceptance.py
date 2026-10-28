#!/usr/bin/env python3
"""Run the full M6 plan on a disposable GitHub macOS 26 host. Never run locally."""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BASELINE_SHA = "2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c"
STEPS = ("contract", "react-target", "react-python-controls", "python-baseline",
         "rust-baseline", "rust-python-controls", "rt-diagnostics", "ts-target",
         "ts-baseline", "ts-python-controls", "bundles", "products")
BASELINE_PORT, CANDIDATE_PORT = 18917, 18919
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def need(condition: bool) -> None:
    if not condition:
        raise ValueError("hosted acceptance refused")


def load(path: Path) -> dict:
    value = json.loads(path.read_bytes())
    need(type(value) is dict)
    return value


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require_hosted() -> None:
    need(os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
         and os.environ.get("RUNNER_OS") == "macOS"
         and platform.system() == "Darwin" and platform.machine() == "arm64"
         and platform.mac_ver()[0].split(".")[0] == "26")


def output_path(value: Path) -> Path:
    root = REPO / ".local/m6-hosted"
    out = Path(os.path.abspath(REPO / value))
    need(out != root and out.is_relative_to(root) and not out.exists())
    need(not any(out.is_relative_to(root / name) for name in ("baseline", "prettier")))
    for path in (out, *out.parents):
        need(not path.is_symlink())
        if path == REPO:
            break
    return out


def clean_env() -> dict[str, str]:
    # Keep build paths and locale only. Input keys and GitHub tokens never reach children.
    names = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "CI", "SDKROOT",
             "DEVELOPER_DIR", "CARGO_HOME", "RUSTUP_HOME", "GITHUB_ACTIONS",
             "RUNNER_ENVIRONMENT", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_TEMP")
    return {**{key: os.environ[key] for key in names if key in os.environ},
            "CARGO_NET_OFFLINE": "true", "PYTHONDONTWRITEBYTECODE": "1",
            "SOM_TS_PRETTIER": str(REPO / ".local/m6-hosted/prettier")}


def logged(out: Path, name: str, command: list[str], env: dict, *, cwd: Path = REPO,
           timeout: int = 14400) -> int:
    with (out / f"{name}.stdout").open("wb") as stdout, (out / f"{name}.stderr").open("wb") as stderr:
        return subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=stderr, shell=False, timeout=timeout).returncode


def head(out: Path, name: str, env: dict) -> str:
    need(logged(out, name, ["git", "rev-parse", "HEAD"], env, timeout=30) == 0)
    value = (out / f"{name}.stdout").read_text().strip()
    need(re.fullmatch(r"[0-9a-f]{40}", value) is not None)
    return value


def port_free(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return False
    except OSError as exc:
        return exc.errno == errno.ECONNREFUSED


def wait_health(process: subprocess.Popen, expected: dict) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        need(process.poll() is None)
        try:
            with OPENER.open(f"http://127.0.0.1:{BASELINE_PORT}/healthz", timeout=2) as response:
                need(response.status == 200 and json.loads(response.read()) == expected)
                return
        except OSError:
            time.sleep(0.1)
    raise ValueError("baseline health unavailable")


def stop_candidate(out: Path, env: dict) -> dict:
    candidate = out / "candidate"
    record = {"stopped": False, "ownership_verified": False, "binary_sha256": None}
    try:
        if not (candidate / "runtime.json").exists():
            record["stopped"] = port_free(CANDIDATE_PORT)
            return record
        runtime, build = load(candidate / "runtime.json"), load(candidate / "build.json")
        actual = sha(candidate / "som")
        record["binary_sha256"] = actual
        argv = [str(candidate / "som"), "serve", "--assets", str(candidate / "assets"),
                "--port", str(CANDIDATE_PORT)]
        pid = runtime["pid"]
        need(type(pid) is int and pid > 1 and pid != os.getpid())
        need(runtime["argv"] == argv and runtime["port"] == CANDIDATE_PORT
             and actual == runtime["binary_sha256"] == build["binary_sha256"])
        need(logged(out, "candidate-ps", ["ps", "-ww", "-p", str(pid), "-o", "command="], env, timeout=30) == 0)
        need((out / "candidate-ps.stdout").read_text().strip() == " ".join(argv))
        need(logged(out, "candidate-cwd", ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], env, timeout=30) == 0)
        directories = [line[1:] for line in (out / "candidate-cwd.stdout").read_text().splitlines() if line.startswith("n")]
        need(directories == [str(candidate)] and os.getpgid(pid) == pid)
        need(sha(candidate / "som") == actual)
        record["ownership_verified"] = True
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                record["stopped"] = True
                break
            time.sleep(0.1)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        pass
    return record


def verdict(out: Path, summary: dict) -> bool:
    build, runtime = load(out / "candidate/build.json"), load(out / "candidate/runtime.json")
    run, audit = load(out / "run/run.json"), load(out / "audit.json")
    actual = sha(out / "candidate/som")
    summary["candidate_binary_sha256"] = actual
    summary["step_count"] = len(run["steps"])
    summary["audit_totals"] = audit["totals"]
    return (build.get("complete") is True and runtime.get("accepted") is False
            and build.get("source_unchanged") is True
            and actual == build.get("binary_sha256") == runtime.get("binary_sha256")
            and build.get("tree_before", {}).get("head") == summary["head_before"]
            and run.get("version") == "som.react-output-acceptance-run.v1"
            and run.get("passed") is True and run.get("identity_passed") is True
            and run.get("failed") == run.get("problems") == run.get("not_run") == []
            and [step.get("step") for step in run["steps"]] == list(STEPS)
            and all(step.get("judgment") == "passed" for step in run["steps"])
            and audit.get("version") == "som.react-output-audit-130.v1"
            and audit.get("run") == (out / "run").relative_to(REPO).as_posix()
            and type(audit.get("rows")) is list and bool(audit["rows"])
            and all(type(row) is dict and (row.get("status") != "attested" or row.get("id") in {
                "contract/sealed", "issue_gates/phase1-contract", "issue_gates/phase1-target-amendments",
                "issue_gates/phase1-acceptance"}) for row in audit["rows"])
            and all(type(audit["totals"].get(name)) is int and audit["totals"][name] == 0
                    for name in ("failed", "unrun"))
            and summary["acceptance_exit_code"] == summary["audit_exit_code"] == 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=REPO / "som-rs/runtime/acceptance-m3-venv/bin/python")
    args = parser.parse_args(argv)
    stage, out, baseline = "runner", None, None
    summary = {"version": "som.m6-hosted-acceptance.v1", "passed": False, "status": "failed",
               "failed_stage": None, "product_suites": "not_run", "head_before": None,
               "head_after": None, "candidate_binary_sha256": None, "step_count": 0,
               "acceptance_exit_code": None, "audit_exit_code": None}
    env = clean_env()
    try:
        require_hosted()
        stage = "output"
        new_out = output_path(args.out)
        new_out.mkdir(parents=True)
        out = new_out
        stage = "inputs"
        frozen = REPO / ".local/m6-hosted/baseline"
        need(sha(frozen / "som") == BASELINE_SHA)
        need(BASELINE_SHA in load(REPO / "som-rs/contracts/react-output-v1/acceptance-plan.json")["baseline"])
        expected = load(frozen / "runtime.json")["health"]
        need(type(expected) is dict and (REPO / ".local/m6-hosted/prettier").is_dir())
        summary["baseline_binary_sha256"] = BASELINE_SHA
        summary["head_before"] = head(out, "head-before", env)
        stage = "ports"
        need(port_free(BASELINE_PORT) and port_free(CANDIDATE_PORT))
        stage = "baseline"
        with (out / "baseline.stdout").open("wb") as stdout, (out / "baseline.stderr").open("wb") as stderr:
            baseline = subprocess.Popen([str(frozen / "som"), "serve", "--assets", str(frozen / "assets"),
                                         "--port", str(BASELINE_PORT)], cwd=frozen, env=env,
                                        stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, shell=False)
        wait_health(baseline, expected)
        stage = "reconstruct-inputs"
        reconstructed = out / "reconstructed-inputs"
        need(logged(out, "reconstruct", [sys.executable, str(HERE / "reconstruct_hosted_inputs.py"),
                     "--repo", str(REPO), "--baseline", f"http://127.0.0.1:{BASELINE_PORT}",
                     "--out", str(reconstructed)], env) == 0)
        need((reconstructed / "rust-baseline").is_dir())
        receipt = load(reconstructed / "receipt.json")
        need(receipt.get("version") == "som.m6-hosted-reconstruction.v1"
             and receipt.get("stage") == "input_reconstruction" and receipt.get("status") == "passed"
             and receipt.get("passed") is True and receipt.get("product_suites") == "not_run"
             and receipt.get("legacy_assemble_requests") == "not_reconstructed")
        need(all(type(receipt.get(name)) is int and receipt[name] == expected for name, expected in (
            ("name_prompt_count", 9), ("rust_file_count", 1170),
            ("rust_phase1_case_count", 115), ("rust_phase2_case_count", 584))))
        reference = receipt["rust_reference"]
        need(reference["destination"] == "rust-baseline"
             and re.fullmatch(r"[0-9a-f]{64}", reference["source_tree_sha256"]) is not None
             and reference["source_tree_sha256"] == reference["copy_tree_sha256"])
        summary["reconstructed_inputs_receipt_sha256"] = sha(reconstructed / "receipt.json")
        stage = "candidate"
        need(logged(out, "prepare", [sys.executable, str(HERE.parent / "rust_output_acceptance/prepare_candidate.py"),
                     "--out", str(out / "candidate"), "--port", str(CANDIDATE_PORT), "--package-assets",
                     str(frozen / "assets/ruff"), "--prettier", env["SOM_TS_PRETTIER"]], env) == 0)
        stage = "acceptance"
        try:
            summary["acceptance_exit_code"] = logged(out, "acceptance", [sys.executable, str(HERE / "run_acceptance.py"),
                "--candidate", str(out / "candidate"), "--out", str(out / "run"), "--baseline",
                f"http://127.0.0.1:{BASELINE_PORT}", "--python", str(args.python),
                "--rust-baseline-run", str(reconstructed / "rust-baseline"),
                "--protected-default", "absent", "--cleanup-products-runtime", "--record-raw-http"], env)
        except (OSError, subprocess.SubprocessError):
            pass
        if summary["acceptance_exit_code"] != 0:
            summary["failed_stage"] = "acceptance"
        try:
            raw_run = load(out / "run/run.json")
            products = next((step for step in raw_run.get("steps", []) if step.get("step") == "products"), {})
            if products.get("report") and Path(products["report"]).is_file():
                summary["product_suites"] = "recorded"
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        # A failed runner can still leave useful raw evidence. Audit it before cleanup.
        if (out / "run").is_dir():
            stage = "audit"
            summary["audit_exit_code"] = logged(out, "audit", [sys.executable, str(HERE / "audit/audit130.py"),
                "--repo", str(REPO), "--run", str(out / "run"), "--out", str(out / "audit.json")], env)
            if summary["audit_exit_code"] != 0:
                summary["failed_stage"] = summary["failed_stage"] or "audit"
        stage = "verdict"
        need(verdict(out, summary))
        summary["passed"] = True
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        summary["failed_stage"] = summary["failed_stage"] or stage
    finally:
        if out is not None and out.is_dir():
            cleanup = {"baseline_stopped": baseline is None, "candidate": stop_candidate(out, env)}
            if baseline is not None:
                try:
                    baseline.terminate()
                    baseline.wait(timeout=10)
                    cleanup["baseline_stopped"] = True
                except (OSError, subprocess.SubprocessError):
                    pass
            summary["cleanup"] = cleanup
            try:
                if summary["head_before"] is not None:
                    summary["head_after"] = head(out, "head-after", env)
                need(summary["head_before"] is not None and summary["head_before"] == summary["head_after"]
                     and cleanup["baseline_stopped"] and cleanup["candidate"]["stopped"])
            except (OSError, ValueError, subprocess.SubprocessError):
                summary["passed"] = False
                summary["failed_stage"] = summary["failed_stage"] or "cleanup-or-head"
            summary["status"] = "passed" if summary["passed"] else "failed"
            try:
                (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
            except OSError:
                summary.update(passed=False, status="failed", failed_stage="summary")
    print(json.dumps({key: summary[key] for key in ("passed", "failed_stage", "step_count", "head_before", "candidate_binary_sha256")}))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
