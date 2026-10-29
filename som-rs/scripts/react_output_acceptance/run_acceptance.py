#!/usr/bin/env python3
"""Run all twelve React phase 1 acceptance checks on one candidate compiler (issue #136).

Runs every check of som-rs/contracts/react-output-v1/acceptance-plan.json in order
against one candidate compiler (from prepare_candidate.py) and one baseline compiler,
records a per-step receipt under <out>/receipts/, records candidate / baseline / 8765 /
tool identity before and after in <out>/identity.json, and writes <out>/run.json.

An isolated hosted VM can use --protected-default absent. Both read-only GETs
must get the OS connection-refused error. This mode does not prove default-server health.

usage:
  run_acceptance.py --candidate DIR --out NEW_DIR [--baseline URL] [--legacy-run DIR]
                    [--rust-baseline-run DIR] [--python PATH] [--protected-default healthy|absent]
                    [--record-raw-http] [--cleanup-products-runtime]
"""
from __future__ import annotations

import argparse
import datetime
import errno
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
import retained_evidence

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent

DEFAULT_BASELINE = "http://127.0.0.1:18960"
DEFAULT_8765_URL = "http://127.0.0.1:8765"
DEFAULT_LEGACY_RUN_REL = pathlib.Path(".local/rust-compiler-goal/acceptance/legacy544/run-005")
DEFAULT_PYTHON_REL = pathlib.Path("som-rs/runtime/acceptance-m3-venv/bin/python")
INITIAL_CONTRACT_RECEIPT_REL = pathlib.Path("som-rs/evidence/react-output-contract-freeze-2026-10-22.json")
REACT_PYTHON_CONTROLS_EXPECT_REL = pathlib.Path("som-rs/evidence/react-output-v1/python-controls-2026-10-22.json")

FORBIDDEN_PORTS = {8765, 18765}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
CLEAN_TREE_PATHS = (
    "som-rs/scripts/react_output_acceptance/",
    "som-rs/tests/react-output-v1/",
    "som-rs/contracts/react-output-v1/",
)
AMENDMENT_STATUS_SEALED = "amendment_sealed_after_independent_review"
AMENDMENT_FILENAME_RE = re.compile(r"^react-output-contract-amendment-(\d+)\.json$")
CONTRACT_SUMMARY_RE = re.compile(r"(?m)^(\d+)\s+passed,\s+(\d+)\s+failed\s*$")

NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(f"refused: {message}", file=sys.stderr)
        raise SystemExit(2)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def git_output(repo: pathlib.Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def resolve_repo_path(path: pathlib.Path, repo: pathlib.Path) -> pathlib.Path:
    if path.is_absolute():
        return path
    return repo / path


def parse_and_validate_url(url: str, flag_name: str) -> tuple[str, int]:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise ValueError(f"invalid {flag_name} URL {url!r}: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{flag_name} URL must use http or https: {url!r}")
    if parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError(f"{flag_name} URL must use a loopback host: {url!r}")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{flag_name} URL must not contain credentials, a path, a query or a fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid port in {flag_name} URL {url!r}: {exc}") from exc
    if port is None or not (1 <= port <= 65535):
        raise ValueError(f"{flag_name} URL must specify an explicit port: {url!r}")
    if port in FORBIDDEN_PORTS:
        raise ValueError(f"{flag_name} port {port} is forbidden (never use 8765 or 18765)")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    normalized = f"{parsed.scheme}://{host}:{port}"
    return normalized, port


def find_latest_contract_receipt(repo: pathlib.Path = REPO) -> pathlib.Path:
    """Pick highest-numbered sealed amendment receipt, or initial freeze receipt if none exists."""
    evidence_dir = repo / "som-rs" / "evidence"
    sealed: list[tuple[int, str, pathlib.Path]] = []
    if evidence_dir.is_dir():
        for entry in sorted(evidence_dir.iterdir()):
            if not entry.is_file():
                continue
            match = AMENDMENT_FILENAME_RE.match(entry.name)
            if match is None:
                continue
            try:
                doc = load_json(entry)
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if isinstance(doc, dict) and doc.get("status") == AMENDMENT_STATUS_SEALED:
                sealed.append((int(match.group(1)), entry.name, entry))
    if sealed:
        sealed.sort(key=lambda item: (item[0], item[1]))
        return sealed[-1][2]
    return repo / INITIAL_CONTRACT_RECEIPT_REL


def format_repo_arg(path: pathlib.Path, repo: pathlib.Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def react_acceptance_files_sha256(repo: pathlib.Path = REPO) -> dict[str, str]:
    script_root = repo / "som-rs" / "scripts" / "react_output_acceptance"
    result: dict[str, str] = {}
    if not script_root.is_dir():
        return result
    for path in sorted(script_root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(repo).as_posix()
        result[rel] = sha_file(path)
    return result


def fetch_healthz(base_or_healthz_url: str) -> dict[str, Any]:
    url = base_or_healthz_url.rstrip("/")
    if not url.endswith("/healthz"):
        url = f"{url}/healthz"
    req = urllib.request.Request(url, method="GET")
    try:
        with NO_PROXY_OPENER.open(req, timeout=10) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as err:
        with err:
            status = err.code
            raw = err.read()
    except OSError as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if isinstance(reason, OSError) and reason.errno == errno.ECONNREFUSED:
            error_kind = "connection_refused"
        elif isinstance(reason, TimeoutError) or (
            isinstance(reason, OSError) and reason.errno == errno.ETIMEDOUT
        ):
            error_kind = "timeout"
        elif isinstance(reason, OSError):
            error_kind = "network_error"
        else:
            error_kind = "unknown_error"
        return {
            "url": url,
            "status": None,
            "body": None,
            "raw": None,
            "sha256": None,
            "error": f"{type(exc).__name__}: {exc}",
            "error_kind": error_kind,
            "error_type": type(reason).__name__,
            "error_errno": reason.errno if isinstance(reason, OSError) else None,
        }

    text = raw.decode("utf-8", errors="replace")
    try:
        parsed_body: Any = json.loads(text)
    except ValueError:
        parsed_body = text
    return {
        "url": url,
        "status": status,
        "body": parsed_body,
        "raw": text,
        "sha256": sha_bytes(raw),
        "error": None,
    }


def healthz_connection_refused(record: dict[str, Any]) -> bool:
    """Accept absence only from a GET that failed with the OS connection-refused error."""
    return (
        record.get("error_kind") == "connection_refused"
        and record.get("error_type") in ("ConnectionRefusedError", "OSError")
        and type(record.get("error_errno")) is int
        and record["error_errno"] == errno.ECONNREFUSED
        and isinstance(record.get("error"), str)
        and bool(record["error"])
        and all(key in record and record[key] is None for key in ("status", "body", "raw", "sha256"))
    )


def validate_preconditions(
    candidate_dir: pathlib.Path,
    out_dir: pathlib.Path,
    baseline_url: str,
    repo: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, Any], str, int, str, int]:
    """Validate all refusal conditions before creating --out or running any step.

    Raises ValueError on any refusal (caller prints to stderr and exits 2).
    """
    if out_dir.exists() or out_dir.is_symlink():
        raise ValueError(f"--out already exists: {out_dir}")

    normalized_baseline, baseline_port = parse_and_validate_url(baseline_url, "--baseline")

    build_path = candidate_dir / "build.json"
    runtime_path = candidate_dir / "runtime.json"
    binary_path = candidate_dir / "som"
    for req_path, label in (
        (build_path, "build.json"),
        (runtime_path, "runtime.json"),
        (binary_path, "som binary"),
    ):
        if not req_path.is_file():
            raise ValueError(f"candidate missing {label}: {req_path}")

    try:
        build_doc = load_json(build_path)
        runtime_doc = load_json(runtime_path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"cannot parse candidate metadata: {exc}") from exc

    if not isinstance(build_doc, dict) or not isinstance(runtime_doc, dict):
        raise ValueError("candidate build.json and runtime.json must be JSON objects")

    raw_port = runtime_doc.get("port")
    if isinstance(raw_port, bool) or not isinstance(raw_port, int) or not (1 <= raw_port <= 65535):
        raise ValueError(f"invalid candidate port in runtime.json: {raw_port!r}")
    candidate_port = raw_port
    if candidate_port in FORBIDDEN_PORTS:
        raise ValueError(f"candidate port {candidate_port} is forbidden (never use 8765 or 18765)")
    if candidate_port == baseline_port:
        raise ValueError(f"candidate port and baseline port must differ (both are {candidate_port})")

    if build_doc.get("complete") is not True:
        raise ValueError(f"candidate build.json complete is {build_doc.get('complete')!r}, expected True")

    tree_before = build_doc.get("tree_before")
    if not isinstance(tree_before, dict):
        raise ValueError("candidate build.json missing tree_before object")
    porcelain_before = tree_before.get("porcelain")
    if porcelain_before != "":
        raise ValueError(f"candidate build.json tree_before.porcelain is not empty: {porcelain_before!r}")

    try:
        current_head = git_output(repo, "rev-parse", "HEAD").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"git rev-parse HEAD failed: {exc}") from exc

    head_before = tree_before.get("head")
    if not isinstance(head_before, str) or head_before.strip() != current_head:
        raise ValueError(
            f"candidate build.json tree_before.head ({head_before!r}) differs from HEAD ({current_head!r})"
        )

    try:
        dirty_status = git_output(
            repo,
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            *CLEAN_TREE_PATHS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"git status failed: {exc}") from exc

    if dirty_status.strip() != "":
        raise ValueError(
            "dirty working tree under react-output acceptance/tests/contracts:\n" + dirty_status.rstrip()
        )

    actual_binary_sha = sha_file(binary_path)
    build_binary_sha = build_doc.get("binary_sha256")
    runtime_binary_sha = runtime_doc.get("binary_sha256")
    if build_binary_sha != actual_binary_sha:
        raise ValueError(
            f"build.json binary_sha256 ({build_binary_sha!r}) differs from som binary ({actual_binary_sha!r})"
        )
    if runtime_binary_sha != actual_binary_sha:
        raise ValueError(
            f"runtime.json binary_sha256 ({runtime_binary_sha!r}) differs from som binary ({actual_binary_sha!r})"
        )

    candidate_url = f"http://127.0.0.1:{candidate_port}"
    return build_doc, runtime_doc, candidate_url, candidate_port, normalized_baseline, baseline_port


def build_steps(
    *,
    python_bin: str,
    candidate_dir: pathlib.Path,
    candidate_url: str,
    baseline_url: str,
    legacy_run: str,
    out_dir: pathlib.Path,
    contract_receipt: str,
    cleanup_products_runtime: bool = False,
    rust_baseline_run: str | None = None,
    record_raw_http: bool = False,
    qa_profile: str | None = None,
) -> list[dict[str, Any]]:
    """Construct the 12 ordered step definitions."""
    som_binary = str(candidate_dir / "som")
    bundles_manifest = out_dir / "bundles" / "products" / "manifest.json"
    steps = [
        {
            "index": 1,
            "step": "contract",
            "argv": [
                python_bin,
                "som-rs/tests/react-output-v1/check_contract.py",
                "--require-prompts",
                "--receipt",
                contract_receipt,
            ],
            "report": out_dir / "logs" / "01-contract.stdout",
            "kind": "contract",
        },
        {
            "index": 2,
            "step": "react-target",
            "argv": [
                python_bin,
                "som-rs/scripts/react_output_acceptance/check_target.py",
                "--url",
                candidate_url,
                "--out",
                str(out_dir / "react-target"),
                "--binary",
                som_binary,
                "--legacy-run",
                legacy_run,
            ],
            "report": out_dir / "react-target" / "report.json",
            "kind": "report",
        },
        {
            "index": 3,
            "step": "react-python-controls",
            "argv": [
                python_bin,
                "som-rs/tests/react-output-v1/check_python_controls.py",
                "--url",
                candidate_url,
                "--expect",
                str(REACT_PYTHON_CONTROLS_EXPECT_REL),
                "--evidence",
                str(out_dir / "react-python-controls.json"),
            ],
            "report": out_dir / "react-python-controls.json",
            "kind": "controls",
        },
        {
            "index": 4,
            "step": "python-baseline",
            "argv": [
                python_bin,
                "som-rs/scripts/typescript_output_acceptance/check_python_baseline.py",
                "--qa",
                candidate_url,
                "--baseline",
                baseline_url,
                "--out",
                str(out_dir / "python-baseline"),
                "--legacy-run",
                legacy_run,
            ],
            "report": out_dir / "python-baseline" / "report.json",
            "kind": "report",
        },
        {
            "index": 5,
            "step": "rust-baseline",
            "argv": [
                python_bin,
                "som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py",
                "--qa",
                candidate_url,
                "--baseline",
                baseline_url,
                "--out",
                str(out_dir / "rust-baseline"),
                *(["--run", rust_baseline_run] if rust_baseline_run is not None else []),
            ],
            "report": out_dir / "rust-baseline" / "report.json",
            "kind": "report",
        },
        {
            "index": 6,
            "step": "rust-python-controls",
            "argv": [
                python_bin,
                "som-rs/tests/rust-output-v1/check_python_controls.py",
                "--base-url",
                candidate_url,
                "--out",
                str(out_dir / "rust-python-controls.json"),
            ],
            "report": out_dir / "rust-python-controls.json",
            "kind": "controls",
        },
        {
            "index": 7,
            "step": "rt-diagnostics",
            "argv": [
                python_bin,
                "som-rs/scripts/typescript_output_acceptance/check_rt_diagnostics.py",
                "--url",
                candidate_url,
                "--out",
                str(out_dir / "rt-diagnostics"),
            ],
            "report": out_dir / "rt-diagnostics" / "report.json",
            "kind": "report",
        },
        {
            "index": 8,
            "step": "ts-target",
            "argv": [
                python_bin,
                "som-rs/scripts/typescript_output_acceptance/check_target.py",
                "--url",
                candidate_url,
                "--out",
                str(out_dir / "ts-target"),
                "--legacy-run",
                legacy_run,
            ],
            "report": out_dir / "ts-target" / "report.json",
            "kind": "report",
        },
        {
            "index": 9,
            "step": "ts-baseline",
            "argv": [
                python_bin,
                "som-rs/scripts/react_output_acceptance/check_typescript_baseline.py",
                "--qa",
                candidate_url,
                "--baseline",
                baseline_url,
                "--out",
                str(out_dir / "ts-baseline"),
                "--legacy-run",
                legacy_run,
            ],
            "report": out_dir / "ts-baseline" / "report.json",
            "kind": "report",
        },
        {
            "index": 10,
            "step": "ts-python-controls",
            "argv": [
                python_bin,
                "som-rs/tests/typescript-output-v1/check_python_controls.py",
                "--url",
                candidate_url,
                "--evidence",
                str(out_dir / "ts-python-controls.json"),
            ],
            "report": out_dir / "ts-python-controls.json",
            "kind": "controls",
        },
        {
            "index": 11,
            "step": "bundles",
            "argv": [
                python_bin,
                "som-rs/scripts/react_output_acceptance/check_bundles.py",
                "--url",
                candidate_url,
                "--out",
                str(out_dir / "bundles"),
                "--legacy-run",
                legacy_run,
            ],
            "report": out_dir / "bundles" / "report.json",
            "kind": "report",
        },
        {
            "index": 12,
            "step": "products",
            "argv": [
                python_bin,
                "som-rs/scripts/react_output_acceptance/check_products.py",
                "--url",
                candidate_url,
                "--manifest",
                str(bundles_manifest),
                "--out",
                str(out_dir / "products"),
                *(["--cleanup-runtime"] if cleanup_products_runtime else []),
                *(["--qa-profile", qa_profile] if qa_profile is not None else []),
            ],
            "report": out_dir / "products" / "report.json",
            "kind": "report",
            "requires_manifest": bundles_manifest,
        },
    ]
    if record_raw_http:
        for step in steps:
            if step["step"] in {"react-python-controls", "python-baseline", "rust-baseline", "rust-python-controls", "ts-python-controls"}:
                step["argv"] = [python_bin, str(HERE / "record_http.py"), "--out", str(out_dir / f"{step['step']}-raw"),
                                "--", *step["argv"][1:]]
    return steps


def judge_contract_stdout(exit_code: int, stdout_path: pathlib.Path) -> tuple[str, str]:
    if not stdout_path.is_file():
        return "failed", f"missing stdout log: {stdout_path}"
    text = stdout_path.read_text(encoding="utf-8", errors="replace")
    fail_lines = [line for line in text.splitlines() if line.startswith("FAIL ")]
    matches = list(CONTRACT_SUMMARY_RE.finditer(text))
    if not matches:
        return "failed", "missing 'N passed, 0 failed' summary line in stdout"
    last_match = matches[-1]
    passed_count = int(last_match.group(1))
    failed_count = int(last_match.group(2))
    if fail_lines:
        return "failed", f"contract stdout reported FAIL lines ({len(fail_lines)})"
    if failed_count != 0 or passed_count <= 0:
        return "failed", f"contract summary reported {passed_count} passed, {failed_count} failed"
    if exit_code != 0:
        return "failed", f"contract exited with code {exit_code}"
    return "passed", f"{passed_count} passed, 0 failed"


def judge_json_report(exit_code: int, report_path: pathlib.Path, kind: str = "report") -> tuple[str, str]:
    if not report_path.is_file():
        return "failed", f"missing report file: {report_path}"
    try:
        doc = load_json(report_path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return "failed", f"invalid JSON in {report_path}: {exc}"
    if not isinstance(doc, dict):
        return "failed", f"report is not a JSON object: {report_path}"

    passed_val = doc.get("passed")
    if kind == "controls" and (isinstance(passed_val, bool) or not isinstance(passed_val, int)):
        return "failed", "controls report passed must be a positive integer"
    if isinstance(passed_val, bool):
        if not passed_val:
            return "failed", "report passed is false"
        if doc.get("status") in ("failed", "not_run"):
            return "failed", f"report status is {doc.get('status')!r}"
        if doc.get("complete", True) is not True:
            return "failed", "report complete is not true"
        failed_field = doc.get("failed")
        if isinstance(failed_field, list) and len(failed_field) > 0:
            return "failed", f"report lists {len(failed_field)} failed item(s)"
        if isinstance(failed_field, int) and not isinstance(failed_field, bool) and failed_field > 0:
            return "failed", f"report failed={failed_field}"
        if doc.get("failed_count", 0) != 0:
            return "failed", f"report failed_count={doc.get('failed_count')}"
        if doc.get("not_run_count", 0) != 0:
            return "failed", f"report not_run_count={doc.get('not_run_count')}"
        problems = doc.get("problems")
        if isinstance(problems, list) and len(problems) > 0:
            return "failed", f"report has problems: {problems[0]}"
        if exit_code != 0:
            return "failed", f"process exited with code {exit_code}"
        return "passed", "ok"

    if kind == "controls" and isinstance(passed_val, int):
        failed_field = doc.get("failed")
        if not isinstance(failed_field, list) or failed_field:
            return "failed", "controls report failed must be an empty list"
        if passed_val <= 0:
            return "failed", f"controls report passed={passed_val}"
        results_field = doc.get("results")
        if not isinstance(results_field, list):
            return "failed", "controls report results must be a list"
        if len(results_field) != passed_val:
            return "failed", f"controls report results count ({len(results_field)}) != passed ({passed_val})"
        if exit_code != 0:
            return "failed", f"process exited with code {exit_code}"
        return "passed", f"{passed_val} passed, 0 failed"

    return "failed", f"report missing valid 'passed' field: {passed_val!r}"


def run_single_step(
    step_def: dict[str, Any],
    out_dir: pathlib.Path,
    repo: pathlib.Path,
) -> dict[str, Any]:
    index = step_def["index"]
    step_name = step_def["step"]
    prefix = f"{index:02d}-{step_name}"
    stdout_path = out_dir / "logs" / f"{prefix}.stdout"
    stderr_path = out_dir / "logs" / f"{prefix}.stderr"
    receipt_path = out_dir / "receipts" / f"{prefix}.json"
    report_path: pathlib.Path = step_def["report"]

    requires_manifest: pathlib.Path | None = step_def.get("requires_manifest")
    if requires_manifest is not None and not requires_manifest.is_file():
        started_iso = now_iso()
        stdout_path.write_bytes(b"")
        stderr_path.write_bytes(b"")
        ended_iso = now_iso()
        reason = f"missing required manifest: {requires_manifest}"
        receipt = {
            "step": step_name,
            "index": index,
            "argv": step_def["argv"],
            "cwd": str(repo),
            "started_at": started_iso,
            "ended_at": ended_iso,
            "seconds": 0.0,
            "exit_code": None,
            "stdout": str(stdout_path),
            "stdout_sha256": sha_file(stdout_path),
            "stderr": str(stderr_path),
            "stderr_sha256": sha_file(stderr_path),
            "report": str(report_path),
            "report_sha256": sha_file(report_path) if report_path.is_file() else None,
            "judgment": "not_run",
            "passed": False,
            "reason": reason,
        }
        write_json(receipt_path, receipt)
        return receipt

    started_iso = now_iso()
    t0 = time.monotonic()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    launch_error = None
    with stdout_path.open("wb") as out_f, stderr_path.open("wb") as err_f:
        try:
            proc = subprocess.run(
                step_def["argv"],
                cwd=repo,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                check=False,
            )
            exit_code = proc.returncode
        except OSError as exc:
            exit_code = 127
            launch_error = f"cannot start step: {type(exc).__name__}: {exc}"
            err_f.write((launch_error + "\n").encode("utf-8"))
    duration = round(time.monotonic() - t0, 3)
    ended_iso = now_iso()

    if launch_error is not None:
        judgment, reason = "failed", launch_error
    elif step_def["kind"] == "contract":
        judgment, reason = judge_contract_stdout(exit_code, stdout_path)
    else:
        judgment, reason = judge_json_report(exit_code, report_path, step_def["kind"])

    report_sha = sha_file(report_path) if report_path.is_file() else None
    receipt = {
        "step": step_name,
        "index": index,
        "argv": step_def["argv"],
        "cwd": str(repo),
        "started_at": started_iso,
        "ended_at": ended_iso,
        "seconds": duration,
        "exit_code": exit_code,
        "stdout": str(stdout_path),
        "stdout_sha256": sha_file(stdout_path),
        "stderr": str(stderr_path),
        "stderr_sha256": sha_file(stderr_path),
        "report": str(report_path),
        "report_sha256": report_sha,
        "judgment": judgment,
        "passed": judgment == "passed",
        "reason": reason,
    }
    write_json(receipt_path, receipt)
    return receipt


def capture_identity_snapshot(
    candidate_dir: pathlib.Path,
    candidate_url: str,
    baseline_url: str,
    port_8765_url: str,
    repo: pathlib.Path,
) -> dict[str, Any]:
    return {
        "captured_at": now_iso(),
        "candidate_binary_sha256": sha_file(candidate_dir / "som"),
        "candidate_healthz": fetch_healthz(candidate_url),
        "baseline_healthz": fetch_healthz(baseline_url),
        "port_8765_healthz": fetch_healthz(port_8765_url),
        "git_head": git_output(repo, "rev-parse", "HEAD").strip(),
        "react_output_acceptance_files": react_acceptance_files_sha256(repo),
    }


def evaluate_identity(
    candidate_dir: pathlib.Path,
    candidate_url: str,
    candidate_port: int,
    build_doc: dict[str, Any],
    runtime_doc: dict[str, Any],
    baseline_url: str,
    port_8765_url: str,
    before: dict[str, Any],
    after: dict[str, Any],
    protected_default: str = "healthy",
) -> dict[str, Any]:
    problems: list[str] = []

    cand_before = before["candidate_healthz"]
    cand_after = after["candidate_healthz"]
    if cand_before["status"] != 200:
        problems.append(f"candidate /healthz before returned status {cand_before['status']}")
    if cand_after["status"] != 200:
        problems.append(f"candidate /healthz after returned status {cand_after['status']}")
    if cand_before["sha256"] != cand_after["sha256"] or cand_before["raw"] != cand_after["raw"]:
        problems.append("candidate /healthz body changed between before and after")
    if before["candidate_binary_sha256"] != after["candidate_binary_sha256"]:
        problems.append("candidate binary_sha256 changed between before and after")

    base_before = before["baseline_healthz"]
    base_after = after["baseline_healthz"]
    if base_before["status"] != 200:
        problems.append(f"baseline /healthz before returned status {base_before['status']}")
    if base_after["status"] != 200:
        problems.append(f"baseline /healthz after returned status {base_after['status']}")
    if base_before["sha256"] != base_after["sha256"] or base_before["raw"] != base_after["raw"]:
        problems.append("baseline /healthz body changed between before and after")

    p8765_before = before["port_8765_healthz"]
    p8765_after = after["port_8765_healthz"]
    if protected_default == "absent":
        for label, record in (("before", p8765_before), ("after", p8765_after)):
            if not healthz_connection_refused(record):
                problems.append(f"8765 /healthz {label} did not return an OS connection-refused error")
        protected_unchanged = healthz_connection_refused(p8765_before) and healthz_connection_refused(p8765_after)
    else:
        if p8765_before["status"] != 200:
            problems.append(f"8765 /healthz before returned status {p8765_before['status']}")
        if p8765_after["status"] != 200:
            problems.append(f"8765 /healthz after returned status {p8765_after['status']}")
        if p8765_before["sha256"] != p8765_after["sha256"] or p8765_before["raw"] != p8765_after["raw"]:
            problems.append("8765 /healthz body changed between before and after")
        protected_unchanged = (
            p8765_before["status"] == 200
            and p8765_after["status"] == 200
            and p8765_before["sha256"] == p8765_after["sha256"]
        )

    protected_identity: dict[str, Any] = {
        "url": port_8765_url,
        "healthz": {
            "before": p8765_before,
            "after": p8765_after,
            "unchanged": protected_unchanged,
        },
    }
    if protected_default == "absent":
        protected_identity["expected_state"] = "absent"

    if before["git_head"] != after["git_head"]:
        problems.append("git HEAD changed between before and after")
    if before["react_output_acceptance_files"] != after["react_output_acceptance_files"]:
        problems.append("files under som-rs/scripts/react_output_acceptance/ changed during run")

    return {
        "candidate": {
            "dir": str(candidate_dir),
            "url": candidate_url,
            "port": candidate_port,
            "build": build_doc,
            "build_sha256": sha_file(candidate_dir / "build.json"),
            "runtime": runtime_doc,
            "runtime_sha256": sha_file(candidate_dir / "runtime.json"),
            "binary_sha256": before["candidate_binary_sha256"],
            "binary_sha256_after": after["candidate_binary_sha256"],
            "healthz": {
                "before": cand_before,
                "after": cand_after,
                "unchanged": (
                    cand_before["status"] == 200
                    and cand_after["status"] == 200
                    and cand_before["sha256"] == cand_after["sha256"]
                ),
            },
        },
        "baseline": {
            "url": baseline_url,
            "healthz": {
                "before": base_before,
                "after": base_after,
                "unchanged": (
                    base_before["status"] == 200
                    and base_after["status"] == 200
                    and base_before["sha256"] == base_after["sha256"]
                ),
            },
        },
        "8765": protected_identity,
        "git_head": {
            "before": before["git_head"],
            "after": after["git_head"],
            "unchanged": before["git_head"] == after["git_head"],
        },
        "react_output_acceptance_files": {
            "before": before["react_output_acceptance_files"],
            "after": after["react_output_acceptance_files"],
            "unchanged": before["react_output_acceptance_files"] == after["react_output_acceptance_files"],
        },
        "problems": problems,
        "passed": len(problems) == 0,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--candidate",
        type=pathlib.Path,
        required=True,
        help="Candidate directory written by prepare_candidate.py (build.json, runtime.json, som)",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        required=True,
        help="New output directory for receipts/, logs/, identity.json, and run.json",
    )
    parser.add_argument(
        "--baseline",
        default=DEFAULT_BASELINE,
        help=f"Baseline compiler URL (default: {DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--legacy-run",
        type=pathlib.Path,
        default=None,
        help=f"Recorded legacy544 run directory (default: {DEFAULT_LEGACY_RUN_REL})",
    )
    parser.add_argument(
        "--rust-baseline-run",
        type=pathlib.Path,
        default=None,
        help="Recorded Rust baseline run directory (default: check_rust_baseline.py's existing default).",
    )
    parser.add_argument(
        "--python",
        type=pathlib.Path,
        default=None,
        help=f"Python interpreter for step subprocesses (default: {DEFAULT_PYTHON_REL})",
    )
    parser.add_argument(
        "--protected-default",
        choices=("healthy", "absent"),
        default="healthy",
        help=(
            "Expected state of protected port 8765 (default: healthy). "
            "Use absent only on an isolated hosted VM; both read-only GETs must get connection refused."
        ),
    )
    parser.add_argument(
        "--cleanup-products-runtime", action="store_true",
        help="Remove owned node_modules and stopped PostgreSQL scratch data after each complete product check.",
    )
    parser.add_argument("--record-raw-http", action="store_true",
                        help="Retain every baseline and Python control HTTP exchange for independent acceptance.")
    parser.add_argument("--qa-profile", type=pathlib.Path, default=None,
                        help="Optional sealed Linux React QA profile for every product gate.")
    return parser.parse_args(argv)


def qa_profile_snapshot(path: pathlib.Path) -> dict[str, str]:
    """Record profile bytes without following a linked file or ancestor."""
    if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("QA profile must be an absolute physical file")
    if not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("QA profile missing or oversized")
    return {"path": str(path), "sha256": sha_file(path)}


def validate_qa_profile(path: pathlib.Path, repo: pathlib.Path) -> dict[str, str]:
    """Require the same sealed profile accepted by each product gate."""
    before = qa_profile_snapshot(path)
    linux_qa_profile = retained_evidence.sealed_profile_helper(repo)
    admitted = linux_qa_profile.validate_profile(path)
    if (not isinstance(admitted, dict) or admitted.get("profile_path") != path or admitted.get("profile_sha256") != before["sha256"]
            or qa_profile_snapshot(path) != before):
        raise ValueError("QA profile identity differs or changed during validation")
    return before


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = REPO
    candidate_dir = resolve_repo_path(args.candidate, repo).resolve()
    out_dir = resolve_repo_path(args.out, repo)

    legacy_run_path = (
        resolve_repo_path(args.legacy_run, repo)
        if args.legacy_run is not None
        else (repo / DEFAULT_LEGACY_RUN_REL)
    )
    python_path = (
        resolve_repo_path(args.python, repo)
        if args.python is not None
        else (repo / DEFAULT_PYTHON_REL)
    )

    try:
        if args.qa_profile is not None:
            out_dir = retained_evidence.physical_repo_path(repo, args.out)
        qa_before = validate_qa_profile(args.qa_profile, repo) if args.qa_profile is not None else None
        (
            build_doc,
            runtime_doc,
            candidate_url,
            candidate_port,
            baseline_url,
            _baseline_port,
        ) = validate_preconditions(candidate_dir, out_dir, args.baseline, repo)
    except (ImportError, OSError, ValueError, KeyError, TypeError) as err:
        print(f"refused: {err}", file=sys.stderr)
        return 2

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "logs").mkdir(parents=True, exist_ok=False)
    (out_dir / "receipts").mkdir(parents=True, exist_ok=False)
    qa_evidence_root = out_dir / "qa-profile-evidence"
    qa_evidence_state = qa_evidence_runtime = qa_evidence_module = None
    if qa_before is not None:
        try:
            qa_evidence_module = sys.modules.get("linux_qa_profile")
            expected_module = repo / "som-rs/tests/react-output-v1/linux_qa_profile.py"
            if (qa_evidence_module is None or not hasattr(qa_evidence_module, "__file__")
                    or pathlib.Path(qa_evidence_module.__file__).resolve() != expected_module.resolve()):
                raise ValueError("QA helper source path differs")
            qa_evidence_state, qa_evidence_runtime = retained_evidence.begin(
                qa_evidence_root, repo, args.qa_profile, qa_evidence_module)
            if qa_evidence_state["profile"]["before"]["sha256"] != qa_before["sha256"]:
                raise ValueError("retained QA profile differs")
        except (OSError, ValueError, KeyError, TypeError) as err:
            print(f"refused: retained QA evidence: {err}", file=sys.stderr)
            return 2

    latest_receipt_path = find_latest_contract_receipt(repo)
    contract_receipt_arg = format_repo_arg(latest_receipt_path, repo)
    legacy_run_arg = format_repo_arg(legacy_run_path, repo) if args.legacy_run is None else str(legacy_run_path)
    rust_baseline_run_arg = (
        str(resolve_repo_path(args.rust_baseline_run, repo))
        if args.rust_baseline_run is not None else None
    )
    python_arg = str(python_path)

    started_at = now_iso()
    t0 = time.monotonic()

    before_snapshot = capture_identity_snapshot(
        candidate_dir=candidate_dir,
        candidate_url=candidate_url,
        baseline_url=baseline_url,
        port_8765_url=DEFAULT_8765_URL,
        repo=repo,
    )
    initial_identity = {
        "candidate": {
            "dir": str(candidate_dir),
            "url": candidate_url,
            "port": candidate_port,
            "build": build_doc,
            "build_sha256": sha_file(candidate_dir / "build.json"),
            "runtime": runtime_doc,
            "runtime_sha256": sha_file(candidate_dir / "runtime.json"),
            "binary_sha256": before_snapshot["candidate_binary_sha256"],
            "healthz": {"before": before_snapshot["candidate_healthz"], "after": None, "unchanged": None},
        },
        "baseline": {
            "url": baseline_url,
            "healthz": {"before": before_snapshot["baseline_healthz"], "after": None, "unchanged": None},
        },
        "8765": {
            "url": DEFAULT_8765_URL,
            "healthz": {"before": before_snapshot["port_8765_healthz"], "after": None, "unchanged": None},
        },
        "git_head": {"before": before_snapshot["git_head"], "after": None, "unchanged": None},
        "react_output_acceptance_files": {
            "before": before_snapshot["react_output_acceptance_files"],
            "after": None,
            "unchanged": None,
        },
        "problems": [],
        "passed": None,
    }
    if args.protected_default == "absent":
        initial_identity["8765"]["expected_state"] = "absent"
    if qa_before is not None:
        initial_identity["qa_profile"] = {"before": qa_before, "after": None, "unchanged": None}
    identity_path = out_dir / "identity.json"
    write_json(identity_path, initial_identity)

    step_defs = build_steps(
        python_bin=python_arg,
        candidate_dir=candidate_dir,
        candidate_url=candidate_url,
        baseline_url=baseline_url,
        legacy_run=legacy_run_arg,
        out_dir=out_dir,
        contract_receipt=contract_receipt_arg,
        cleanup_products_runtime=args.cleanup_products_runtime,
        rust_baseline_run=rust_baseline_run_arg,
        record_raw_http=args.record_raw_http,
        qa_profile=qa_before["path"] if qa_before is not None else None,
    )

    receipts: list[dict[str, Any]] = []
    for step_def in step_defs:
        receipt = run_single_step(step_def, out_dir, repo)
        receipts.append(receipt)

    after_snapshot = capture_identity_snapshot(
        candidate_dir=candidate_dir,
        candidate_url=candidate_url,
        baseline_url=baseline_url,
        port_8765_url=DEFAULT_8765_URL,
        repo=repo,
    )
    final_identity = evaluate_identity(
        candidate_dir=candidate_dir,
        candidate_url=candidate_url,
        candidate_port=candidate_port,
        build_doc=build_doc,
        runtime_doc=runtime_doc,
        baseline_url=baseline_url,
        port_8765_url=DEFAULT_8765_URL,
        before=before_snapshot,
        after=after_snapshot,
        protected_default=args.protected_default,
    )
    if qa_before is not None:
        try:
            qa_after = qa_profile_snapshot(args.qa_profile)
        except (OSError, ValueError):
            qa_after = None
        unchanged = qa_after == qa_before
        final_identity["qa_profile"] = {"before": qa_before, "after": qa_after, "unchanged": unchanged}
        if not unchanged:
            final_identity["problems"].append("QA profile missing or changed during acceptance")
            final_identity["passed"] = False
        evidence_passed = retained_evidence.finish(qa_evidence_root, repo, args.qa_profile, qa_evidence_module,
                                                    qa_evidence_state, qa_evidence_runtime)
        final_identity["qa_profile_evidence"] = {"path": "qa-profile-evidence/identity.json",
                                                  "sha256": sha_file(qa_evidence_root / "identity.json"),
                                                  "passed": evidence_passed}
        if not evidence_passed:
            final_identity["problems"].append("QA retained evidence missing or changed during acceptance")
            final_identity["passed"] = False
    write_json(identity_path, final_identity)

    summary_steps: list[dict[str, Any]] = []
    for receipt in receipts:
        idx = receipt["index"]
        sname = receipt["step"]
        rpath = out_dir / "receipts" / f"{idx:02d}-{sname}.json"
        summary_steps.append(
            {
                "index": idx,
                "step": sname,
                "judgment": receipt["judgment"],
                "passed": receipt["judgment"] == "passed",
                "reason": receipt["reason"],
                "exit_code": receipt["exit_code"],
                "seconds": receipt["seconds"],
                "receipt": str(rpath),
                "receipt_sha256": sha_file(rpath),
                "report": receipt["report"],
                "report_sha256": receipt["report_sha256"],
            }
        )

    passed_steps = [s["step"] for s in summary_steps if s["judgment"] == "passed"]
    failed_steps = [s["step"] for s in summary_steps if s["judgment"] == "failed"]
    not_run_steps = [s["step"] for s in summary_steps if s["judgment"] == "not_run"]

    problems: list[str] = []
    for s in summary_steps:
        if s["judgment"] != "passed":
            problems.append(f"step {s['step']} {s['judgment']}: {s['reason']}")
    problems.extend(final_identity["problems"])

    overall_passed = (
        len(summary_steps) == 12
        and all(s["judgment"] == "passed" for s in summary_steps)
        and len(not_run_steps) == 0
        and final_identity["passed"] is True
    )

    ended_at = now_iso()
    total_seconds = round(time.monotonic() - t0, 3)
    run_summary = {
        "version": "som.react-output-acceptance-run.v1",
        "issue": 136,
        "started_at": started_at,
        "ended_at": ended_at,
        "seconds": total_seconds,
        "candidate": str(candidate_dir),
        "candidate_url": candidate_url,
        "baseline_url": baseline_url,
        "legacy_run": legacy_run_arg,
        "record_raw_http": args.record_raw_http,
        **({"qa_profile": qa_before} if qa_before is not None else {}),
        **({"rust_baseline_run": rust_baseline_run_arg} if rust_baseline_run_arg is not None else {}),
        "python": python_arg,
        "contract_receipt": contract_receipt_arg,
        "identity": str(identity_path),
        "identity_sha256": sha_file(identity_path),
        "identity_passed": final_identity["passed"],
        "steps": summary_steps,
        "passed_steps": passed_steps,
        "failed": failed_steps,
        "not_run": not_run_steps,
        "problems": problems,
        "passed": overall_passed,
        "status": "passed" if overall_passed else "failed",
    }
    write_json(out_dir / "run.json", run_summary)
    return 0 if overall_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
