#!/usr/bin/env python3
"""Run the TypeScript phase 1 acceptance and rust-output-v1 amendment 005 checks on one candidate.

Runs all 13 acceptance steps in order, each as its own child process with
som-rs/runtime/acceptance-m3-venv/bin/python, writing only under --out (which
must be a new directory under .local/typescript-output/acceptance/).

Starts a temporary read-only baseline som server from --baseline on
--baseline-port with cwd under --out, waits for /healthz, and stops only that
PID on completion, error, or Ctrl-C.

usage: run_acceptance.py --candidate DIR --out NEW_DIR [--baseline DIR] [--baseline-port N] [--workers N]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, NoReturn

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent
RUST_ACCEPTANCE = SOM_RS / "scripts" / "rust_output_acceptance"
QA_DIR = SOM_RS / "som-targets" / "qa"
TS_TESTS = SOM_RS / "tests" / "typescript-output-v1"
RUST_TESTS = SOM_RS / "tests" / "rust-output-v1"
TS_CONTRACT = SOM_RS / "contracts" / "typescript-output-v1"
RUST_CONTRACT = SOM_RS / "contracts" / "rust-output-v1"
VENV_PYTHON = SOM_RS / "runtime" / "acceptance-m3-venv" / "bin" / "python"

LOCAL_TS = REPO / ".local" / "typescript-output"
ACCEPTANCE_ROOT = LOCAL_TS / "acceptance"
NPM_CACHE = LOCAL_TS / "npm-cache-001"
DEFAULT_BASELINE = REPO / ".local" / "rust-compiler-goal" / "rust-output-candidate-006"
PUBLIC_RUN = REPO / ".local" / "rust-compiler-goal" / "acceptance" / "public-products-003b"
LEGACY_RUN = REPO / ".local" / "rust-compiler-goal" / "acceptance" / "legacy544" / "run-005"
RUN_003 = REPO / ".local" / "rust-compiler-goal" / "acceptance" / "rust-output-055" / "run-003"
COVERAGE_FROZEN = SOM_RS / "evidence" / "rust-output-v1" / "coverage-cases-python-2026-10-15.json"

DEFAULT_URL = "http://127.0.0.1:8765"
FORBIDDEN_PORTS = {8765, 18765}
EXPECTED_BASELINE_BINARY_SHA256 = "52658e13af1ac08659ef1dda7e333634f4ae3893731304e09edec6ca5f9b87c2"

TOOL_ROOTS = [
    "som-rs/scripts/typescript_output_acceptance",
    "som-rs/scripts/rust_output_acceptance",
    "som-rs/som-targets/qa",
    "som-rs/tests/typescript-output-v1",
    "som-rs/tests/rust-output-v1",
    "som-rs/contracts/typescript-output-v1",
    "som-rs/contracts/rust-output-v1",
]


def _load_rust_driver() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_som_rust_run_acceptance",
        RUST_ACCEPTANCE / "run_acceptance.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {RUST_ACCEPTANCE / 'run_acceptance.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rust_driver = _load_rust_driver()

NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        print(f"refused: {message}", file=sys.stderr)
        raise SystemExit(2)


def refuse(message: str) -> NoReturn:
    print(f"refused: {message}", file=sys.stderr)
    raise SystemExit(2)


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def sha_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha(root: pathlib.Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink()):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f"{rel}\0symlink:{os.readlink(path)}\n".encode())
        else:
            digest.update(f"{rel}\0{sha_file(path)}\n".encode())
    return digest.hexdigest()


def output_sha(path: pathlib.Path) -> str | None:
    if path.is_file():
        return sha_file(path)
    if path.is_dir():
        return tree_sha(path)
    return None


def load(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def get(url: str, timeout: float = 10.0) -> tuple[int | None, bytes]:
    try:
        with NO_PROXY_OPENER.open(url, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()
    except OSError as error:
        return None, str(error).encode("utf-8")


def listener(port: int) -> list[str]:
    done = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(done.stdout.split())


def process_command(pid: int) -> str:
    return subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def git(*args: str, cwd: pathlib.Path | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd or REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def candidate_identity(candidate: pathlib.Path) -> dict[str, Any]:
    """Reuse candidate_identity from the Rust run_acceptance.py, catching missing/invalid files."""
    if not candidate.is_dir():
        return {"candidate": str(candidate), "port": None, "pid": None, "problems": ["candidate_not_a_directory"]}
    required = ("build.json", "runtime.json", "source.json", "som", "source.tar", "assets/manifest.json")
    missing = [name for name in required if not (candidate / name).is_file()]
    if missing:
        return {"candidate": str(candidate), "port": None, "pid": None, "problems": [f"missing:{m}" for m in missing]}
    orig_repo = rust_driver.REPO
    orig_get = rust_driver.get
    orig_listener = rust_driver.listener
    orig_proc_cmd = rust_driver.process_command
    try:
        rust_driver.REPO = REPO
        rust_driver.get = get
        rust_driver.listener = listener
        rust_driver.process_command = process_command
        info = rust_driver.candidate_identity(candidate)
    except Exception as error:  # noqa: BLE001
        return {
            "candidate": str(candidate),
            "port": None,
            "pid": None,
            "problems": [f"{type(error).__name__}: {error}"],
        }
    finally:
        rust_driver.REPO = orig_repo
        rust_driver.get = orig_get
        rust_driver.listener = orig_listener
        rust_driver.process_command = orig_proc_cmd
    return info


def default_state() -> dict[str, Any]:
    """Read the state of 8765 only through GET /healthz."""
    status, raw = get(DEFAULT_URL + "/healthz")
    health = None
    if status == 200:
        try:
            health = json.loads(raw)
        except ValueError:
            health = None
    return {
        "url": DEFAULT_URL + "/healthz",
        "healthz_status": status,
        "healthz_sha256": hashlib.sha256(raw).hexdigest() if status == 200 else None,
        "health": health,
    }


def tools_state() -> dict[str, Any]:
    files = sorted(f for f in git("ls-files", "-z", "--", *TOOL_ROOTS).split("\0") if f)
    return {
        "head": git("rev-parse", "HEAD").strip(),
        "porcelain": git("status", "--porcelain", "--untracked-files=all", "--", *TOOL_ROOTS),
        "roots": list(TOOL_ROOTS),
        "files": {f: sha_file(REPO / f) for f in files if (REPO / f).is_file()},
    }


def plans_state() -> dict[str, Any]:
    ts_plan = TS_CONTRACT / "acceptance-plan.json"
    rust_plan = RUST_CONTRACT / "acceptance-plan.json"
    return {
        "typescript_output_v1": {
            "path": str(ts_plan.relative_to(REPO)) if ts_plan.is_relative_to(REPO) else str(ts_plan),
            "sha256": sha_file(ts_plan),
        },
        "rust_output_v1": {
            "path": str(rust_plan.relative_to(REPO)) if rust_plan.is_relative_to(REPO) else str(rust_plan),
            "sha256": sha_file(rust_plan),
        },
    }


def check_baseline_dir(baseline: pathlib.Path) -> str:
    """Validate the baseline directory and return its binary sha256, or refuse with exit 2."""
    binary = baseline / "som"
    assets = baseline / "assets"
    if not baseline.is_dir() or not binary.is_file():
        refuse(f"baseline som binary not found: {binary}")
    if not assets.is_dir():
        refuse(f"baseline assets directory not found: {assets}")
    digest = sha_file(binary)
    if digest != EXPECTED_BASELINE_BINARY_SHA256:
        refuse(
            f"baseline som binary sha256 {digest} != expected {EXPECTED_BASELINE_BINARY_SHA256}"
        )
    return digest


def wait_baseline_health(
    port: int,
    process: subprocess.Popen[bytes],
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    last_err = "timed out waiting for /healthz"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"baseline server exited early with status {process.returncode}")
        status, raw = get(url, timeout=2.0)
        if status == 200:
            try:
                body = json.loads(raw)
                if isinstance(body, dict):
                    return body
                last_err = "healthz returned non-object JSON"
            except ValueError as error:
                last_err = f"healthz invalid JSON: {error}"
        else:
            last_err = f"status={status}"
        time.sleep(0.05)
    raise RuntimeError(f"baseline server on port {port} did not become healthy: {last_err}")


def start_baseline(
    baseline: pathlib.Path,
    port: int,
    run: pathlib.Path,
    binary_sha256: str,
) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    baseline_cwd = run / "baseline"
    baseline_cwd.mkdir(parents=True, exist_ok=False)
    log_path = run / "logs" / "baseline.log"
    argv = [
        str(baseline / "som"),
        "serve",
        "--assets",
        str(baseline / "assets"),
        "--port",
        str(port),
    ]
    with log_path.open("wb") as log_file:
        proc = subprocess.Popen(
            argv,
            cwd=baseline_cwd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    try:
        health = wait_baseline_health(port, proc)
    except Exception:
        stop_baseline(proc)
        raise
    identity = {
        "baseline": str(baseline),
        "port": port,
        "pid": proc.pid,
        "argv": argv,
        "cwd": str(baseline_cwd),
        "binary_sha256": binary_sha256,
        "assets_tree_sha256": tree_sha(baseline / "assets"),
        "health": health,
        "running": proc.poll() is None,
        "problems": [],
    }
    return proc, identity


def baseline_after_state(
    baseline: pathlib.Path,
    port: int,
    proc: subprocess.Popen[bytes],
    before: dict[str, Any],
) -> dict[str, Any]:
    problems: list[str] = []
    running = proc.poll() is None
    if not running:
        problems.append(f"baseline process {proc.pid} exited with {proc.returncode}")
    status, raw = get(f"http://127.0.0.1:{port}/healthz", timeout=5.0)
    health = None
    if status == 200:
        try:
            health = json.loads(raw)
        except ValueError:
            problems.append("baseline /healthz returned invalid JSON")
    else:
        problems.append(f"baseline /healthz status {status}")
    binary_sha = sha_file(baseline / "som") if (baseline / "som").is_file() else None
    if binary_sha != before["binary_sha256"]:
        problems.append("baseline binary_sha256 changed")
    if health != before["health"]:
        problems.append("baseline health changed")
    return {
        "baseline": str(baseline),
        "port": port,
        "pid": proc.pid,
        "argv": before["argv"],
        "cwd": before["cwd"],
        "binary_sha256": binary_sha,
        "assets_tree_sha256": tree_sha(baseline / "assets") if (baseline / "assets").is_dir() else None,
        "health": health,
        "running": running,
        "problems": problems,
    }


def stop_baseline(proc: subprocess.Popen[bytes] | None) -> int | None:
    """Stop only the baseline server PID we started."""
    if proc is None:
        return None
    if proc.poll() is None:
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5.0)
    return proc.returncode


# ---------------------------------------------------------------- judgments

def _row_state(row: dict[str, Any]) -> str:
    """Classify a single report row as 'passed', 'failed', or 'not_run'."""
    status = row.get("status")
    if status in ("not_run", "unrun", "not run", "skipped"):
        return "not_run"
    if status == "failed":
        return "failed"
    if row.get("not_run_gates"):
        return "not_run"
    if row.get("failed_gates"):
        return "failed"
    gates = row.get("gates")
    if isinstance(gates, dict) and gates:
        gate_states = []
        for gate in gates.values():
            if isinstance(gate, dict):
                g_status = gate.get("status")
                if g_status in ("not_run", "unrun", "not run", "skipped"):
                    gate_states.append("not_run")
                elif g_status == "failed" or gate.get("passed") is False:
                    gate_states.append("failed")
                elif g_status == "passed" or gate.get("passed") is True:
                    gate_states.append("passed")
                else:
                    gate_states.append("failed")
        if "not_run" in gate_states:
            return "not_run"
        if "failed" in gate_states:
            return "failed"
    parity = row.get("parity")
    if isinstance(parity, dict) and parity.get("status") in ("not_run", "unrun", "not run"):
        return "not_run"
    if isinstance(parity, dict) and parity.get("status") == "failed":
        return "failed"
    exchanges = row.get("exchanges")
    if isinstance(exchanges, list) and exchanges:
        ex_states = [_row_state(ex) for ex in exchanges if isinstance(ex, dict)]
        if "not_run" in ex_states:
            return "not_run"
        if "failed" in ex_states:
            return "failed"
    exchange = row.get("exchange")
    if isinstance(exchange, dict):
        ex_state = _row_state(exchange)
        if ex_state != "passed":
            return ex_state
    if "passed" in row:
        return "passed" if row["passed"] is True else "failed"
    if status == "passed":
        return "passed"
    if "same_bundle" in row or "assemble_status" in row:
        ok = (
            row.get("json_status") == 200
            and row.get("cnl_status") == 200
            and row.get("same_bundle") is True
            and row.get("assemble_status") == 200
        )
        return "passed" if ok else "failed"
    return "failed"


def _collect_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(report.get("products"), list):
        rows.extend(r for r in report["products"] if isinstance(r, dict))
    if isinstance(report.get("assemble_refusals"), list):
        rows.extend(r for r in report["assemble_refusals"] if isinstance(r, dict))
    if isinstance(report.get("public_rows"), list) or isinstance(report.get("legacy_rows"), list):
        for key in ("public_rows", "legacy_compile_rows", "legacy_rows"):
            if isinstance(report.get(key), list):
                rows.extend(r for r in report[key] if isinstance(r, dict))
    if isinstance(report.get("phase1"), dict) and isinstance(report.get("phase2"), dict):
        for phase_key in ("phase1", "phase2"):
            phase_cases = report[phase_key].get("cases")
            if isinstance(phase_cases, list):
                rows.extend(r for r in phase_cases if isinstance(r, dict))
    if not rows:
        for key in ("rows", "cases", "results"):
            if isinstance(report.get(key), list):
                rows.extend(r for r in report[key] if isinstance(r, dict))
                break
    elif isinstance(report.get("cases"), list) and isinstance(report.get("assemble_refusals"), list):
        rows.extend(r for r in report["cases"] if isinstance(r, dict))
    return rows


def evaluate_report(report: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Verify that every row in report passed, with 0 failed and 0 not_run."""
    rows = _collect_rows(report)
    states = [_row_state(r) for r in rows]
    passed_rows = sum(1 for s in states if s == "passed")
    failed_rows = sum(1 for s in states if s == "failed")
    not_run_rows = sum(1 for s in states if s == "not_run")

    problems: list[str] = []
    if not rows:
        problems.append("no rows in report")
    if failed_rows > 0:
        problems.append(f"{failed_rows} row(s) failed")
    if not_run_rows > 0:
        problems.append(f"{not_run_rows} row(s) not_run")

    if report.get("status") in ("failed", "not_run", "unrun"):
        problems.append(f"report status is {report.get('status')!r}")
    if report.get("passed") is False:
        problems.append("report passed is False")
    for count_key in ("failed_count", "not_run_count", "failed_rows", "not_run_rows"):
        val = report.get(count_key)
        if isinstance(val, int) and not isinstance(val, bool) and val > 0:
            problems.append(f"{count_key}={val}")
    for list_key in ("failed", "problems", "inventory_problems", "blocked"):
        val = report.get(list_key)
        if isinstance(val, list) and val:
            problems.append(f"{list_key} non-empty")
    total_val = report.get("total")
    if isinstance(total_val, int) and not isinstance(total_val, bool):
        passed_val = report.get("passed")
        if isinstance(passed_val, int) and not isinstance(passed_val, bool) and passed_val != total_val:
            problems.append(f"passed {passed_val} != total {total_val}")
        passed_cnt = report.get("passed_count")
        if isinstance(passed_cnt, int) and not isinstance(passed_cnt, bool) and passed_cnt != total_val:
            problems.append(f"passed_count {passed_cnt} != total {total_val}")
        if total_val <= 0:
            problems.append(f"total={total_val}")

    summary_obj = report.get("summary")
    if isinstance(summary_obj, dict):
        if summary_obj.get("all_passed") is False:
            problems.append("summary.all_passed is False")
        if summary_obj.get("no_sampling_complete") is False:
            problems.append("summary.no_sampling_complete is False")
        for list_key in ("failed", "blocked", "failed_products", "not_run_products", "non_passing_gates"):
            val = summary_obj.get(list_key)
            if isinstance(val, list) and val:
                problems.append(f"summary.{list_key} non-empty")
        for sub_key in ("bundle_checks", "assemble_refusals"):
            sub = summary_obj.get(sub_key)
            if isinstance(sub, dict):
                if sub.get("passed") != sub.get("total") or sub.get("total", 0) <= 0:
                    problems.append(f"summary.{sub_key} {sub.get('passed')}/{sub.get('total')}")
        if isinstance(summary_obj.get("total_products"), int):
            if summary_obj.get("passed_products") != summary_obj["total_products"] or summary_obj["total_products"] <= 0:
                problems.append(
                    f"summary.passed_products {summary_obj.get('passed_products')}/{summary_obj['total_products']}"
                )

    for phase_key in ("phase1", "phase2", "public", "legacy", "exchanges"):
        sub = report.get(phase_key)
        if isinstance(sub, dict) and "total" in sub and "passed" in sub:
            if sub["passed"] != sub["total"] or sub["total"] <= 0:
                problems.append(f"{phase_key} {sub['passed']}/{sub['total']}")
            if isinstance(sub.get("failed"), list) and sub["failed"]:
                problems.append(f"{phase_key}.failed non-empty")

    ok = not problems and len(rows) > 0 and passed_rows == len(rows) and failed_rows == 0 and not_run_rows == 0
    judgment = {
        "rows_total": len(rows),
        "rows_passed": passed_rows,
        "rows_failed": failed_rows,
        "rows_not_run": not_run_rows,
        "problems": problems,
    }
    return ok, judgment


def make_step_judge(report_rel: str, extra_check: Any = None):
    def judge(receipt: dict[str, Any], run: pathlib.Path) -> tuple[bool, dict[str, Any]]:
        report_path = run / report_rel
        report = load(report_path)
        report_ok, judgment = evaluate_report(report)
        if extra_check is not None:
            extra_problems = extra_check(report)
            if extra_problems:
                judgment["problems"].extend(extra_problems)
                report_ok = False
        judgment["report"] = report_rel
        judgment["exit_code"] = receipt["exit_code"]
        ok = receipt["exit_code"] == 0 and report_ok
        return ok, judgment

    return judge


def _check_cli_extra(report: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "refusals" in report or "refusals_exit_2" in report:
        if report.get("refusals", 0) <= 0 or report.get("refusals") != report.get("refusals_exit_2"):
            problems.append(
                f"refusals={report.get('refusals')}, refusals_exit_2={report.get('refusals_exit_2')}"
            )
    ts = report.get("typescript")
    if isinstance(ts, dict) and ts.get("passed") is not True:
        problems.append("typescript CLI case did not pass")
    return problems


def _check_validate_extra(report: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    cases = report.get("cases")
    if isinstance(cases, list) and COVERAGE_FROZEN.is_file():
        frozen = {c["id"]: c.get("python_files") for c in load(COVERAGE_FROZEN).get("cases", [])}
        for row in cases:
            if isinstance(row, dict) and "python_files" in row and row.get("id") in frozen:
                if row["python_files"] != frozen[row["id"]]:
                    problems.append(f"{row['id']}: python_files differ from frozen evidence")
    return problems


def _check_rust_products_extra(report: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "refusal_plan" in report and report.get("refusal_plan") != "phase2":
        problems.append(f"refusal_plan={report.get('refusal_plan')!r} != 'phase2'")
    refusals = report.get("assemble_refusals")
    if isinstance(refusals, list):
        by_id = {r.get("id"): r for r in refusals if isinstance(r, dict)}
        a6 = by_id.get("A6")
        if isinstance(a6, dict) and "code" in a6 and a6["code"] != "INVALID_VALUE":
            problems.append(f"A6 code={a6.get('code')!r} != 'INVALID_VALUE'")
        a4 = by_id.get("A4")
        if isinstance(a4, dict) and "code" in a4 and a4["code"] != "INVALID_SCHEMA":
            problems.append(f"A4 code={a4.get('code')!r} != 'INVALID_SCHEMA'")
    return problems


# ---------------------------------------------------------------- steps

def steps(
    candidate: pathlib.Path,
    run: pathlib.Path,
    qa: str,
    base: str,
    workers: int = 4,
) -> list[dict[str, Any]]:
    py = str(VENV_PYTHON)
    bundles_out = run / "bundles"
    rust_products_report = run / "rust-products.json"
    return [
        {
            "name": "target",
            "cwd": REPO,
            "argv": [py, str(HERE / "check_target.py"), "--url", qa, "--out", str(run / "target")],
            "outputs": ["target/report.json", "target/responses"],
            "judge": make_step_judge("target/report.json"),
        },
        {
            "name": "rt-diagnostics",
            "cwd": REPO,
            "argv": [py, str(HERE / "check_rt_diagnostics.py"), "--url", qa, "--out", str(run / "rt-diagnostics")],
            "outputs": ["rt-diagnostics/report.json", "rt-diagnostics/responses"],
            "judge": make_step_judge("rt-diagnostics/report.json"),
        },
        {
            "name": "cli",
            "cwd": REPO,
            "argv": [
                py,
                str(RUST_ACCEPTANCE / "check_cli.py"),
                "--binary",
                str(candidate / "som"),
                "--base-url",
                qa,
                "--out",
                str(run / "cli.json"),
                "--refusals",
                "phase2",
            ],
            "outputs": ["cli.json"],
            "judge": make_step_judge("cli.json", _check_cli_extra),
        },
        {
            "name": "bundles",
            "cwd": REPO,
            "argv": [py, str(HERE / "check_bundles.py"), "--url", qa, "--out", str(bundles_out)],
            "outputs": ["bundles/report.json", "bundles/products/manifest.json", "bundles/products", "bundles/responses"],
            "judge": make_step_judge("bundles/report.json"),
        },
        {
            "name": "ts-products",
            "cwd": QA_DIR,
            "argv": [
                py,
                str(QA_DIR / "check_ts_products.py"),
                "--manifest",
                str(bundles_out / "products" / "manifest.json"),
                "--cache",
                str(NPM_CACHE),
                "--qa",
                qa,
                "--out",
                str(run / "ts-products"),
            ],
            "outputs": ["ts-products/report.json", "ts-products/summary.json"],
            "judge": make_step_judge("ts-products/report.json"),
        },
        {
            "name": "python-baseline",
            "cwd": REPO,
            "argv": [
                py,
                str(HERE / "check_python_baseline.py"),
                "--qa",
                qa,
                "--baseline",
                base,
                "--out",
                str(run / "python-baseline"),
                "--workers",
                str(workers),
            ],
            "outputs": ["python-baseline/report.json"],
            "judge": make_step_judge("python-baseline/report.json"),
        },
        {
            "name": "python-replay",
            "cwd": REPO,
            "argv": [
                py,
                str(RUST_ACCEPTANCE / "python_replay.py"),
                "--asset-break",
                "--base-url",
                qa,
                "--public-run",
                str(PUBLIC_RUN),
                "--legacy-run",
                str(LEGACY_RUN),
                "--out",
                str(run / "python-replay.json"),
            ],
            "outputs": ["python-replay.json"],
            "judge": make_step_judge("python-replay.json"),
        },
        {
            "name": "validate-cases",
            "cwd": REPO,
            "argv": [
                py,
                str(RUST_TESTS / "validate_cases.py"),
                "--base-url",
                qa,
                "--out",
                str(run / "validate-cases.json"),
            ],
            "outputs": ["validate-cases.json"],
            "judge": make_step_judge("validate-cases.json", _check_validate_extra),
        },
        {
            "name": "ts-python-controls",
            "cwd": REPO,
            "argv": [
                py,
                str(TS_TESTS / "check_python_controls.py"),
                "--url",
                qa,
                "--evidence",
                str(run / "ts-python-controls.json"),
            ],
            "outputs": ["ts-python-controls.json"],
            "judge": make_step_judge("ts-python-controls.json"),
        },
        {
            "name": "rust-python-controls",
            "cwd": REPO,
            "argv": [
                py,
                str(RUST_TESTS / "check_python_controls.py"),
                "--base-url",
                qa,
                "--out",
                str(run / "rust-python-controls.json"),
            ],
            "outputs": ["rust-python-controls.json"],
            "judge": make_step_judge("rust-python-controls.json"),
        },
        {
            "name": "rust-baseline",
            "cwd": REPO,
            "argv": [
                py,
                str(HERE / "check_rust_baseline.py"),
                "--qa",
                qa,
                "--baseline",
                base,
                "--out",
                str(run / "rust-baseline"),
                "--workers",
                str(workers),
            ],
            "outputs": ["rust-baseline/report.json"],
            "judge": make_step_judge("rust-baseline/report.json"),
        },
        {
            "name": "rust-products",
            "cwd": QA_DIR,
            "argv": [
                py,
                str(RUST_ACCEPTANCE / "run_acceptance.py"),
                "cluster-gates",
                "--qa",
                qa,
                "--work",
                str(run / "rust-products-work"),
                "--target-dir",
                str(run / "rust-target"),
                "--out",
                str(rust_products_report),
                "--no-gates",
                "--refusals",
                "phase2",
                "--parity",
                str(RUN_003 / "behavior" / "parity" / "report-parity.json"),
            ],
            "outputs": ["rust-products.json"],
            "judge": make_step_judge("rust-products.json", _check_rust_products_extra),
        },
        {
            "name": "rust-unchanged",
            "cwd": REPO,
            "argv": [
                py,
                str(RUST_ACCEPTANCE / "check_rust_unchanged.py"),
                "--qa",
                qa,
                "--run",
                str(RUN_003),
                "--phase1",
                str(rust_products_report),
                "--out",
                str(run / "rust-unchanged.json"),
                "--workers",
                str(workers),
            ],
            "outputs": ["rust-unchanged.json"],
            "judge": make_step_judge("rust-unchanged.json"),
        },
    ]


# ---------------------------------------------------------------- plan mapping

TS_PLAN_MAP: dict[str, list[list[str] | None]] = {
    "python_unchanged": [
        ["python-baseline"],
        ["python-replay", "validate-cases"],
        ["ts-python-controls"],
        ["rust-python-controls"],
    ],
    "target_selection_checks": [
        ["target"],
        ["target"],
        ["rt-diagnostics"],
        ["target"],
        ["cli"],
    ],
    "scope_checks": [
        ["target"],
        ["target"],
        ["target"],
        ["target", "bundles"],
    ],
    "bundle_checks": [
        ["bundles"],
        ["bundles"],
        ["bundles"],
        ["bundles"],
        ["bundles"],
    ],
    "assemble_refusals": [["bundles"]] * 12,
    "product_gates": [["ts-products"]] * 9,
    "independent_audit": [None],
}

AMENDMENT005_MAP: list[list[str] | None] = [
    ["rt-diagnostics"],
    ["rust-python-controls"],
    ["python-baseline"],
    ["python-replay", "validate-cases"],
    ["rust-baseline"],
    ["rust-products", "rust-unchanged"],
    ["rust-baseline"],
    ["rust-products"],
    ["cli"],
    None,
]


def _map_check(
    plan_name: str,
    section: str,
    index: int,
    check_id: str,
    text: str,
    step_names: list[str] | None,
    results: dict[str, bool],
) -> dict[str, Any]:
    if step_names is None:
        return {
            "plan": plan_name,
            "section": section,
            "index": index,
            "id": check_id,
            "check": text,
            "steps": [],
            "status": "not_run",
            "state": "not_run",
            "passed": False,
            "audit": True,
            "reason": "independent audit",
        }
    if not step_names or any(name not in results for name in step_names):
        return {
            "plan": plan_name,
            "section": section,
            "index": index,
            "id": check_id,
            "check": text,
            "steps": list(step_names),
            "status": "not_run",
            "state": "not_run",
            "passed": False,
            "audit": False,
            "reason": "step not run",
        }
    passed = all(results[name] for name in step_names)
    row: dict[str, Any] = {
        "plan": plan_name,
        "section": section,
        "index": index,
        "id": check_id,
        "check": text,
        "steps": list(step_names),
        "status": "passed" if passed else "failed",
        "state": "passed" if passed else "failed",
        "passed": passed,
        "audit": False,
    }
    if not passed:
        failed_steps = [name for name in step_names if not results[name]]
        row["reason"] = "step failed: " + ", ".join(failed_steps)
    return row


def plan_coverage(results: dict[str, bool]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[str]]:
    ts_plan = load(TS_CONTRACT / "acceptance-plan.json")
    rust_plan = load(RUST_CONTRACT / "acceptance-plan.json")

    ts_sections: dict[str, list[tuple[str, str]]] = {
        "python_unchanged": [
            (f"python_unchanged[{i}]", text)
            for i, text in enumerate(ts_plan["python_unchanged"]["checks"])
        ],
        "target_selection_checks": [
            (f"target_selection_checks[{i}]", text)
            for i, text in enumerate(ts_plan["target_selection_checks"])
        ],
        "scope_checks": [
            (f"scope_checks[{i}]", text)
            for i, text in enumerate(ts_plan["scope_checks"])
        ],
        "bundle_checks": [
            (f"bundle_checks[{i}]", text)
            for i, text in enumerate(ts_plan["bundle_checks"])
        ],
        "assemble_refusals": [
            (recipe["id"], f"{recipe['id']}: {recipe['change']} -> {recipe['code']} {recipe['http_status']} {recipe['prompt_pointer']}")
            for recipe in ts_plan["assemble_refusals"]["recipes"]
        ],
        "product_gates": [
            (f"product_gates[{i}]", text)
            for i, text in enumerate(ts_plan["product_gates"]["each_acceptance_case"])
        ],
        "independent_audit": [
            ("independent_audit[0]", ts_plan["issue_gates"]["phase1-acceptance"]),
        ],
    }

    all_rows: list[dict[str, Any]] = []
    ts_grouped: dict[str, list[dict[str, Any]]] = {}
    problems: list[str] = []

    for section, items in ts_sections.items():
        mapped = TS_PLAN_MAP[section]
        if len(mapped) != len(items):
            problems.append(f"typescript-output-v1 {section}: {len(items)} checks, {len(mapped)} mapped")
        sec_rows: list[dict[str, Any]] = []
        for index, (cid, text) in enumerate(items):
            step_names = mapped[index] if index < len(mapped) else []
            row = _map_check("typescript-output-v1", section, index, cid, text, step_names, results)
            sec_rows.append(row)
            all_rows.append(row)
        ts_grouped[section] = sec_rows

    a005_items = rust_plan["amendment005"]["checks"]
    if len(AMENDMENT005_MAP) != len(a005_items):
        problems.append(f"rust-output-v1 amendment005: {len(a005_items)} checks, {len(AMENDMENT005_MAP)} mapped")
    a005_rows: list[dict[str, Any]] = []
    for index, text in enumerate(a005_items):
        step_names = AMENDMENT005_MAP[index] if index < len(AMENDMENT005_MAP) else []
        row = _map_check(
            "rust-output-v1",
            "amendment005",
            index,
            f"amendment005[{index}]",
            text,
            step_names,
            results,
        )
        a005_rows.append(row)
        all_rows.append(row)

    return all_rows, ts_grouped, a005_rows, problems


def run_step(
    index: int,
    step: dict[str, Any],
    run: pathlib.Path,
    env_delta: dict[str, str],
) -> dict[str, Any]:
    name = f"{index:02d}-{step['name']}"
    stdout = run / "logs" / f"{name}.stdout"
    stderr = run / "logs" / f"{name}.stderr"
    receipt: dict[str, Any] = {
        "step": step["name"],
        "index": index,
        "argv": step["argv"],
        "cwd": str(step["cwd"]),
        "env_delta": env_delta,
        "started_at": now(),
        "stdout": str(stdout.relative_to(run)),
        "stderr": str(stderr.relative_to(run)),
    }
    started = time.monotonic()
    with stdout.open("wb") as out_fh, stderr.open("wb") as err_fh:
        code = subprocess.run(
            step["argv"],
            cwd=step["cwd"],
            env={**os.environ, **env_delta},
            stdin=subprocess.DEVNULL,
            stdout=out_fh,
            stderr=err_fh,
            check=False,
        ).returncode
    receipt.update(
        ended_at=now(),
        seconds=time.monotonic() - started,
        exit_code=code,
        stdout_sha256=sha_file(stdout),
        stderr_sha256=sha_file(stderr),
        outputs={o: output_sha(run / o) for o in step["outputs"]},
    )
    try:
        receipt["passed"], receipt["summary"] = step["judge"](receipt, run)
    except Exception as error:  # noqa: BLE001
        receipt["passed"] = False
        receipt["summary"] = {"judge_error": f"{type(error).__name__}: {error}"}
    receipt["judgment"] = receipt["summary"]
    (run / "receipts" / f"{name}.json").write_text(
        json.dumps(receipt, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{name}: {'passed' if receipt['passed'] else 'FAILED'} ({receipt['seconds']:.0f}s)", flush=True)
    return receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _ArgumentParser(
        description="Run every typescript-output-v1 phase 1 and rust-output-v1 amendment005 check on one candidate."
    )
    parser.add_argument("--candidate", type=pathlib.Path, required=True, help="Candidate directory from prepare_candidate.py --prettier")
    parser.add_argument("--out", type=pathlib.Path, required=True, help="New output directory under .local/typescript-output/acceptance/")
    parser.add_argument(
        "--baseline",
        type=pathlib.Path,
        default=DEFAULT_BASELINE,
        help="Baseline candidate directory (default: %(default)s)",
    )
    parser.add_argument(
        "--baseline-port",
        type=int,
        default=18958,
        help="Loopback QA port for the temporary baseline server (default: %(default)s)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent workers for baseline and unchanged steps (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    if args.workers < 1:
        refuse("--workers must be >= 1")
    if args.baseline_port in FORBIDDEN_PORTS or not (1 <= args.baseline_port <= 65535):
        refuse(f"--baseline-port must not be {args.baseline_port}")

    if args.out.exists() or args.out.is_symlink():
        refuse(f"preserve existing output: {args.out}")
    run = args.out.resolve()
    acceptance_root = ACCEPTANCE_ROOT.resolve()
    if run.exists() or run.is_symlink():
        refuse(f"preserve existing output: {run}")
    if not run.is_relative_to(acceptance_root) or run == acceptance_root:
        refuse(f"--out must be a new directory under {acceptance_root}")

    candidate = args.candidate.resolve()
    baseline = args.baseline.resolve()

    tools_before = tools_state()
    if tools_before["porcelain"]:
        refuse("commit the acceptance tools first:\n" + tools_before["porcelain"])

    baseline_sha = check_baseline_dir(baseline)

    candidate_before = candidate_identity(candidate)
    if candidate_before["problems"]:
        refuse(f"candidate identity problems: {candidate_before['problems']}")

    if args.baseline_port == candidate_before["port"]:
        refuse(f"--baseline-port {args.baseline_port} equals candidate port")
    if listener(args.baseline_port):
        refuse(f"--baseline-port {args.baseline_port} is already listening")

    run.mkdir(parents=True, exist_ok=False)
    (run / "logs").mkdir()
    (run / "receipts").mkdir()

    qa_url = f"http://127.0.0.1:{candidate_before['port']}"
    base_url = f"http://127.0.0.1:{args.baseline_port}"
    env_delta = {"CARGO_NET_OFFLINE": "true", "PYTHONDONTWRITEBYTECODE": "1"}

    baseline_proc: subprocess.Popen[bytes] | None = None
    try:
        baseline_proc, baseline_before = start_baseline(
            baseline, args.baseline_port, run, baseline_sha
        )
        started_at = now()
        default_before = default_state()
        plans_before = plans_state()

        before_snapshot = {
            "recorded_at": started_at,
            "candidate": candidate_before,
            "baseline": baseline_before,
            "default_8765": default_before,
            "tools": tools_before,
            "acceptance_plans": plans_before,
        }
        header: dict[str, Any] = {
            "version": "som.typescript-output-acceptance-header.v1",
            "started_at": started_at,
            "ended_at": None,
            "workers": args.workers,
            "baseline_port": args.baseline_port,
            "env_delta": env_delta,
            "candidate_before": candidate_before,
            "baseline_before": baseline_before,
            "default_before": default_before,
            "tools_before": tools_before,
            "tools": tools_before,
            "acceptance_plans_before": plans_before,
            "acceptance_plans": plans_before,
            "before": before_snapshot,
            "after": None,
        }
        (run / "header.json").write_text(
            json.dumps(header, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        planned_steps = steps(candidate, run, qa_url, base_url, args.workers)
        receipts = [
            run_step(index, step, run, env_delta)
            for index, step in enumerate(planned_steps, 1)
        ]

        ended_at = now()
        candidate_after = candidate_identity(candidate)
        baseline_after = baseline_after_state(
            baseline, args.baseline_port, baseline_proc, baseline_before
        )
        default_after = default_state()
        tools_after = tools_state()
        plans_after = plans_state()
    finally:
        stop_baseline(baseline_proc)

    after_snapshot = {
        "recorded_at": ended_at,
        "candidate": candidate_after,
        "baseline": baseline_after,
        "default_8765": default_after,
        "tools": tools_after,
        "acceptance_plans": plans_after,
    }
    header.update(
        ended_at=ended_at,
        candidate_after=candidate_after,
        baseline_after=baseline_after,
        default_after=default_after,
        tools_after=tools_after,
        acceptance_plans_after=plans_after,
        after=after_snapshot,
    )
    (run / "header.json").write_text(
        json.dumps(header, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    step_results = {r["step"]: bool(r["passed"]) for r in receipts}
    all_checks, ts_grouped, a005_checks, coverage_problems = plan_coverage(step_results)
    steps_passed = len(receipts) == len(planned_steps) and all(r["passed"] for r in receipts)

    non_audit_unpassed = [
        f"plan check {c['section']}[{c['index']}] {c['status']}"
        for c in all_checks
        if not c["audit"] and c["status"] != "passed"
    ]
    candidate_changed = (
        {k: v for k, v in candidate_after.items() if k != "checks"}
        != {k: v for k, v in candidate_before.items() if k != "checks"}
    )
    blockers = (
        [f"step {r['step']} failed" for r in receipts if not r["passed"]]
        + non_audit_unpassed
        + coverage_problems
        + [f"candidate after: {p}" for p in candidate_after["problems"]]
        + (["candidate identity changed during the run"] if candidate_changed else [])
        + [f"baseline after: {p}" for p in baseline_after["problems"]]
        + (["default on 8765 changed during the run"] if default_after != default_before else [])
        + (["tools changed during the run"] if tools_after != tools_before else [])
        + (["acceptance plans changed during the run"] if plans_after != plans_before else [])
    )

    overall_passed = steps_passed and not blockers
    summary = {
        "version": "som.typescript-output-acceptance-run.v1",
        "issue": 109,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": "passed" if overall_passed else "failed",
        "passed": overall_passed,
        "steps_passed": steps_passed,
        "blockers": blockers,
        "header_sha256": sha_file(run / "header.json"),
        "candidate_after": candidate_after,
        "baseline_after": baseline_after,
        "default_after": default_after,
        "steps": [
            {
                "step": r["step"],
                "index": r["index"],
                "passed": r["passed"],
                "exit_code": r["exit_code"],
                "seconds": r["seconds"],
                "receipt_sha256": sha_file(run / "receipts" / f"{i:02d}-{r['step']}.json"),
                "summary": r["summary"],
            }
            for i, r in enumerate(receipts, 1)
        ],
        "typescript_plan": ts_grouped,
        "amendment005": a005_checks,
        "plan_checks": all_checks,
        "acceptance_plans_sha256": {
            name: info["sha256"] for name, info in plans_after.items()
        },
    }
    (run / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": summary["status"], "steps_passed": steps_passed, "blockers": blockers}))
    return 0 if overall_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
