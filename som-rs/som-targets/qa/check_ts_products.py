#!/usr/bin/env python3
"""Issue #76 product gates and behavior parity checker for TypeScript phase 1 products.

Runs all 9 gates in som-rs/contracts/typescript-output-v1/acceptance-plan.json
(product_gates.each_acceptance_case) for one product (--product / --prompt) or a
set of products (--manifest), without sampling:

  1. install            npm ci --offline --ignore-scripts --no-audit --no-fund
  2. typecheck          node node_modules/typescript/bin/tsc -p tsconfig.json
  3. format_check       node node_modules/prettier/bin/prettier.cjs --check --no-config --no-editorconfig ...
  4. test               node --test --test-concurrency=1 test/api.test.ts (with and without TEST_DATABASE_URL)
  5. lock_unchanged     package.json, package-lock.json and all files outside node_modules unchanged
  6. schema_equivalence PostgreSQL catalog match against the Python migration for the same prompt
  7. parity             behavior parity against the Python product (including the 4 TypeScript added_inputs)
  8. strict_refusals    divergences D1-D5 and D7 checked on the TypeScript product alone
  9. startup            missing DATABASE_URL -> exit 2 (1 stderr line), invalid SOM_APP_ADDR -> exit 2 (1 stderr line),
                        SIGTERM on running server -> exit 0
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Iterator
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent
BEHAVIOR_DIR = SOM_RS / "behavior"
TS_BEHAVIOR_DIR = BEHAVIOR_DIR / "typescript"
TS_CONTRACT = SOM_RS / "contracts" / "typescript-output-v1"
TS_REFERENCE = TS_CONTRACT / "reference"

for _p in (str(HERE), str(BEHAVIOR_DIR), str(TS_BEHAVIOR_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import check_rust_products as rust_products  # noqa: E402
import hosts as base_hosts  # noqa: E402
from typescript import host as ts_host  # noqa: E402
from typescript import suite as ts_suite  # noqa: E402

GATES_ORDER: tuple[str, ...] = (
    "install",
    "typecheck",
    "format_check",
    "test",
    "lock_unchanged",
    "schema_equivalence",
    "parity",
    "strict_refusals",
    "startup",
)

EXCLUDED_SCHEMA_TABLES: tuple[str, ...] = ("alembic_version", "_som_ts_migrations")

REFERENCE_FILE_HASHES: dict[str, str] = {
    name: hashlib.sha256((TS_REFERENCE / name).read_bytes()).hexdigest()
    for name in ("package.json", "package-lock.json", "tsconfig.json")
    if (TS_REFERENCE / name).is_file()
}

NODE_TEST_STAT = re.compile(r"^(?:ℹ|#)\s+(tests|suites|pass|fail|cancelled|skipped|todo)\s+(\d+)\s*$", re.MULTILINE)


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def make_gate(
    status: str,
    *,
    command: Any = None,
    exit_code: int | None = None,
    duration_ms: int = 0,
    reason: str = "",
    output_summary: str = "",
    **details: Any,
) -> dict[str, Any]:
    if status not in ("passed", "failed", "not_run"):
        raise ValueError(f"invalid gate status: {status}")
    return {
        "status": status,
        "passed": status == "passed",
        "command": command,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "seconds": round(duration_ms / 1000.0, 3),
        "reason": reason,
        "output_summary": output_summary[-3000:] if output_summary else "",
        **details,
    }


def run_cmd(
    command: list[str],
    cwd: pathlib.Path,
    env: dict[str, str],
    timeout: float = 300.0,
) -> dict[str, Any]:
    started = time.monotonic()
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    duration_ms = round((time.monotonic() - started) * 1000)
    return {
        "command": command,
        "exit_code": done.returncode,
        "stdout": done.stdout,
        "stderr": done.stderr,
        "duration_ms": duration_ms,
    }


def parse_node_test_counts(output: str) -> dict[str, int]:
    return {name: int(count) for name, count in NODE_TEST_STAT.findall(output)}


def evaluate_install(res: dict[str, Any]) -> dict[str, Any]:
    out = (res["stdout"] + res["stderr"]).strip()
    ok = res["exit_code"] == 0
    return make_gate(
        "passed" if ok else "failed",
        command=res["command"],
        exit_code=res["exit_code"],
        duration_ms=res["duration_ms"],
        reason="" if ok else f"npm ci exited with status {res['exit_code']}",
        output_summary=out,
    )


def evaluate_typecheck(res: dict[str, Any]) -> dict[str, Any]:
    out = (res["stdout"] + res["stderr"]).strip()
    ok = res["exit_code"] == 0 and out == ""
    reason = ""
    if res["exit_code"] != 0:
        reason = f"tsc exited with status {res['exit_code']}"
    elif out != "":
        reason = "tsc produced diagnostic output"
    return make_gate(
        "passed" if ok else "failed",
        command=res["command"],
        exit_code=res["exit_code"],
        duration_ms=res["duration_ms"],
        reason=reason,
        output_summary=out,
    )


def evaluate_format_check(res: dict[str, Any]) -> dict[str, Any]:
    out = (res["stdout"] + res["stderr"]).strip()
    ok = res["exit_code"] == 0
    return make_gate(
        "passed" if ok else "failed",
        command=res["command"],
        exit_code=res["exit_code"],
        duration_ms=res["duration_ms"],
        reason="" if ok else f"prettier --check exited with status {res['exit_code']}",
        output_summary=out,
    )


def evaluate_test_gate(with_db: dict[str, Any], without_db: dict[str, Any]) -> dict[str, Any]:
    with_out = (with_db["stdout"] + with_db["stderr"]).strip()
    without_out = (without_db["stdout"] + without_db["stderr"]).strip()
    counts = parse_node_test_counts(with_out)
    without_counts = parse_node_test_counts(without_out)

    problems: list[str] = []
    if with_db["exit_code"] != 0:
        problems.append(f"with TEST_DATABASE_URL exited with {with_db['exit_code']}")
    total_tests = counts.get("tests", 0)
    passed_tests = counts.get("pass", 0)
    skipped_tests = counts.get("skipped", 0) + counts.get("todo", 0) + counts.get("cancelled", 0)
    failed_tests = counts.get("fail", 0)
    if total_tests <= 0 or passed_tests != total_tests:
        problems.append(f"with TEST_DATABASE_URL passed {passed_tests}/{total_tests} tests")
    if skipped_tests > 0 or failed_tests > 0:
        problems.append(f"with TEST_DATABASE_URL had fail={failed_tests}, skipped/todo/cancelled={skipped_tests}")

    if without_db["exit_code"] == 0:
        problems.append("omitting TEST_DATABASE_URL exited with 0 (must fail and never skip)")
    if without_counts.get("skipped", 0) > 0:
        problems.append(f"omitting TEST_DATABASE_URL skipped {without_counts['skipped']} test(s)")

    ok = not problems
    duration_ms = with_db["duration_ms"] + without_db["duration_ms"]
    return make_gate(
        "passed" if ok else "failed",
        command=with_db["command"],
        exit_code=0 if ok else (with_db["exit_code"] or 1),
        duration_ms=duration_ms,
        reason="; ".join(problems),
        output_summary=with_out[-1500:],
        with_test_database_url={
            "exit_code": with_db["exit_code"],
            "counts": counts,
            "duration_ms": with_db["duration_ms"],
        },
        without_test_database_url={
            "exit_code": without_db["exit_code"],
            "counts": without_counts,
            "duration_ms": without_db["duration_ms"],
            "failed_as_required": without_db["exit_code"] != 0 and without_counts.get("skipped", 0) == 0,
        },
    )


def evaluate_lock_unchanged(
    before_files: dict[str, str],
    after_files: dict[str, str],
    reference_hashes: dict[str, str] | None = None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    problems: list[str] = []
    added = sorted(set(after_files) - set(before_files))
    removed = sorted(set(before_files) - set(after_files))
    changed = sorted(k for k in set(before_files) & set(after_files) if before_files[k] != after_files[k])

    for key in ("package.json", "package-lock.json"):
        if before_files.get(key) != after_files.get(key):
            problems.append(f"{key} changed during commands")
    if added:
        problems.append(f"files added outside node_modules: {added}")
    if removed:
        problems.append(f"files removed outside node_modules: {removed}")
    if changed:
        problems.append(f"files modified outside node_modules: {changed}")

    ref = reference_hashes if reference_hashes is not None else REFERENCE_FILE_HASHES
    for key, expected_sha in ref.items():
        if after_files.get(key) != expected_sha:
            problems.append(f"{key} differs from contract reference/{key}")

    ok = not problems
    return make_gate(
        "passed" if ok else "failed",
        command=["sha256-tree-compare", "package.json", "package-lock.json", "outside-node_modules"],
        exit_code=0 if ok else 1,
        duration_ms=duration_ms,
        reason="; ".join(problems),
        output_summary="all files outside node_modules unchanged" if ok else "; ".join(problems),
        package_json_sha256_before=before_files.get("package.json"),
        package_json_sha256_after=after_files.get("package.json"),
        lock_sha256_before=before_files.get("package-lock.json"),
        lock_sha256_after=after_files.get("package-lock.json"),
        added_outside_node_modules=added,
        removed_outside_node_modules=removed,
        changed_outside_node_modules=changed,
    )


def schema_differences(ts_schema: dict[str, Any], py_schema: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for table in sorted(set(ts_schema) | set(py_schema)):
        ts_t, py_t = ts_schema.get(table), py_schema.get(table)
        if isinstance(ts_t, dict) and isinstance(py_t, dict):
            for part in sorted(set(ts_t) | set(py_t)):
                if ts_t.get(part) != py_t.get(part):
                    diffs.append({
                        "table": table,
                        "part": part,
                        "typescript": ts_t.get(part),
                        "python": py_t.get(part),
                    })
        elif ts_t != py_t:
            diffs.append({"table": table, "typescript": ts_t, "python": py_t})
    return diffs


def evaluate_schema_equivalence(
    ts_schema: dict[str, Any],
    py_schema: dict[str, Any],
    command: list[str],
    duration_ms: int,
) -> dict[str, Any]:
    diffs = schema_differences(ts_schema, py_schema)
    problems: list[str] = []
    if not ts_schema:
        problems.append("TypeScript migration produced no public tables")
    if diffs:
        problems.append(f"{len(diffs)} schema difference(s) between TypeScript and Python migrations")
    ok = not problems
    return make_gate(
        "passed" if ok else "failed",
        command=command,
        exit_code=0 if ok else 1,
        duration_ms=duration_ms,
        reason="; ".join(problems),
        output_summary=f"tables={sorted(ts_schema)}" if ok else json.dumps(diffs[:5], ensure_ascii=False),
        excluded_tables=list(EXCLUDED_SCHEMA_TABLES),
        compared_tables=sorted(set(ts_schema) | set(py_schema)),
        differences=diffs,
        typescript_schema=ts_schema,
        python_schema=py_schema,
    )


def evaluate_parity(
    python_run: ts_suite.TsRun,
    ts_run: ts_suite.TsRun,
    diffs: list[dict[str, Any]],
    duration_ms: int,
    command: list[str],
    python_raw_sha256: str = "",
    ts_raw_sha256: str = "",
) -> dict[str, Any]:
    py_500 = [e["step"] for e in python_run.transcript if e["status"] == 500]
    ts_500 = [e["step"] for e in ts_run.transcript if e["status"] == 500]
    problems: list[str] = []
    if python_run.failures:
        problems.append(f"Python oracle had {len(python_run.failures)} failure(s)")
    if ts_run.failures:
        problems.append(
            f"TypeScript oracle had {len(ts_run.failures)} failure(s) "
            f"(first: {ts_run.failures[0]['step']}: {ts_run.failures[0]['problems']})"
        )
    if diffs:
        problems.append(f"{len(diffs)} parity difference(s) between Python and TypeScript")
    if py_500:
        problems.append(f"Python product returned 500 on {py_500}")
    if ts_500:
        problems.append(f"TypeScript product returned 500 on {ts_500}")

    ok = not problems
    return make_gate(
        "passed" if ok else "failed",
        command=command,
        exit_code=0 if ok else 1,
        duration_ms=duration_ms,
        reason="; ".join(problems),
        output_summary=(
            f"{len(ts_run.transcript)} steps matched"
            if ok
            else json.dumps(
                {"ts_failures": ts_run.failures[:5], "differences": diffs[:5]},
                ensure_ascii=False,
            )
        ),
        steps=len(ts_run.transcript),
        python_failures=len(python_run.failures),
        typescript_failures=len(ts_run.failures),
        differences=len(diffs),
        first_python_failures=python_run.failures[:10],
        first_typescript_failures=ts_run.failures[:10],
        first_differences=diffs[:10],
        python_500_steps=py_500,
        typescript_500_steps=ts_500,
        python_raw_sha256=python_raw_sha256,
        typescript_raw_sha256=ts_raw_sha256,
    )


DIVERGENCE_IDS: frozenset[str] = frozenset({"D1", "D2", "D3", "D4", "D5", "D7"})


def evaluate_strict_refusals(
    strict_entries: list[dict[str, Any]],
    strict_failures: list[dict[str, Any]],
    duration_ms: int,
    command: list[str],
) -> dict[str, Any]:
    by_divergence: dict[str, int] = {}
    prototype_members = 0
    for entry in strict_entries:
        cat = entry["category"]
        if cat in DIVERGENCE_IDS:
            by_divergence[cat] = by_divergence.get(cat, 0) + 1
        elif cat == "prototype_members":
            prototype_members += 1
    status_500 = [e["step"] for e in strict_entries if e["status"] == 500]
    problems: list[str] = []
    if not strict_entries:
        problems.append("no strict refusal steps were executed")
    if strict_failures:
        problems.append(
            f"{len(strict_failures)} strict refusal failure(s) "
            f"(first: {strict_failures[0]['step']}: {strict_failures[0]['problems']})"
        )
    if status_500:
        problems.append(f"TypeScript product returned 500 on strict steps {status_500}")

    ok = not problems
    ordered_div = dict(sorted(by_divergence.items()))
    return make_gate(
        "passed" if ok else "failed",
        command=command,
        exit_code=0 if ok else 1,
        duration_ms=duration_ms,
        reason="; ".join(problems),
        output_summary=(
            f"{len(strict_entries)} strict checks passed "
            f"(divergences={ordered_div}, prototype_members={prototype_members})"
            if ok
            else json.dumps(strict_failures[:5], ensure_ascii=False)
        ),
        strict_steps=len(strict_entries),
        by_divergence=ordered_div,
        prototype_members=prototype_members,
        failures=len(strict_failures),
        first_failures=strict_failures[:10],
    )


def evaluate_startup(
    missing_db: dict[str, Any],
    invalid_addr: dict[str, Any],
    sigterm_run: dict[str, Any],
    command: list[str],
    duration_ms: int,
) -> dict[str, Any]:
    problems: list[str] = []

    db_lines = non_empty_lines(missing_db.get("stderr", ""))
    if missing_db.get("exit_code") != 2 or len(db_lines) != 1:
        problems.append(
            f"missing DATABASE_URL exited with {missing_db.get('exit_code')} "
            f"and {len(db_lines)} stderr line(s) (expected exit 2 and 1 line)"
        )

    addr_lines = non_empty_lines(invalid_addr.get("stderr", ""))
    if invalid_addr.get("exit_code") != 2 or len(addr_lines) != 1:
        problems.append(
            f"invalid SOM_APP_ADDR exited with {invalid_addr.get('exit_code')} "
            f"and {len(addr_lines)} stderr line(s) (expected exit 2 and 1 line)"
        )

    if not sigterm_run.get("ready"):
        problems.append(f"server did not become ready before SIGTERM: {sigterm_run.get('error', '')}")
    if not sigterm_run.get("cwd_verified"):
        problems.append("server PID cwd could not be verified via lsof before SIGTERM")
    if sigterm_run.get("exit_code") != 0:
        problems.append(f"SIGTERM exited with {sigterm_run.get('exit_code')} (expected 0)")

    ok = not problems
    return make_gate(
        "passed" if ok else "failed",
        command=command,
        exit_code=0 if ok else 1,
        duration_ms=duration_ms,
        reason="; ".join(problems),
        output_summary=(
            f"missing_db=exit 2 ({db_lines[0] if db_lines else ''}), "
            f"invalid_addr=exit 2 ({addr_lines[0] if addr_lines else ''}), "
            f"sigterm=exit {sigterm_run.get('exit_code')}"
        ),
        missing_database_url={
            "exit_code": missing_db.get("exit_code"),
            "stderr_lines": db_lines,
            "passed": missing_db.get("exit_code") == 2 and len(db_lines) == 1,
        },
        invalid_som_app_addr={
            "exit_code": invalid_addr.get("exit_code"),
            "stderr_lines": addr_lines,
            "passed": invalid_addr.get("exit_code") == 2 and len(addr_lines) == 1,
        },
        sigterm={
            "ready": bool(sigterm_run.get("ready")),
            "cwd_verified": bool(sigterm_run.get("cwd_verified")),
            "exit_code": sigterm_run.get("exit_code"),
            "passed": (
                bool(sigterm_run.get("ready"))
                and bool(sigterm_run.get("cwd_verified"))
                and sigterm_run.get("exit_code") == 0
            ),
        },
    )


def evaluate_product_row(
    case_id: str,
    version: str,
    files_sha256: str,
    gates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ordered_gates: dict[str, dict[str, Any]] = {}
    for name in GATES_ORDER:
        if name not in gates:
            ordered_gates[name] = make_gate(
                "not_run",
                command=None,
                exit_code=None,
                duration_ms=0,
                reason=f"gate {name} was not executed",
                output_summary="not_run",
            )
        else:
            ordered_gates[name] = gates[name]

    passed_gates = [name for name in GATES_ORDER if ordered_gates[name]["status"] == "passed"]
    failed_gates = [name for name in GATES_ORDER if ordered_gates[name]["status"] == "failed"]
    not_run_gates = [
        name for name in GATES_ORDER if ordered_gates[name]["status"] not in ("passed", "failed")
    ]
    all_passed = len(passed_gates) == len(GATES_ORDER) and not failed_gates and not not_run_gates
    return {
        "id": case_id,
        "version": version,
        "files_sha256": files_sha256,
        "passed": all_passed,
        "gate_statuses": {name: ordered_gates[name]["status"] for name in GATES_ORDER},
        "passed_gates": passed_gates,
        "failed_gates": failed_gates,
        "not_run_gates": not_run_gates,
        "gates": ordered_gates,
    }


def build_report(
    rows: list[dict[str, Any]],
    cache_info: dict[str, Any],
    toolchain_info: dict[str, Any],
    process_records: list[dict[str, Any]],
    started_at: float,
) -> tuple[dict[str, Any], int]:
    failed_products = [r["id"] for r in rows if r["failed_gates"]]
    not_run_products = [r["id"] for r in rows if r["not_run_gates"]]
    gate_breakdown: dict[str, dict[str, int]] = {
        gate: {
            "passed": sum(1 for r in rows if r["gates"][gate]["status"] == "passed"),
            "failed": sum(1 for r in rows if r["gates"][gate]["status"] == "failed"),
            "not_run": sum(1 for r in rows if r["gates"][gate]["status"] not in ("passed", "failed")),
        }
        for gate in GATES_ORDER
    }
    failed_gate_details = [
        {
            "product": r["id"],
            "gate": g,
            "status": r["gates"][g]["status"],
            "reason": r["gates"][g]["reason"],
        }
        for r in rows
        for g in GATES_ORDER
        if r["gates"][g]["status"] != "passed"
    ]
    complete = len(rows) > 0 and not failed_products and not not_run_products
    exit_code = 0 if complete else (1 if failed_products else 2)
    summary = {
        "total_products": len(rows),
        "passed_products": sum(1 for r in rows if r["passed"]),
        "failed_products": failed_products,
        "not_run_products": not_run_products,
        "gates_order": list(GATES_ORDER),
        "gate_breakdown": gate_breakdown,
        "non_passing_gates": failed_gate_details,
        "no_sampling_complete": len(rows) > 0 and not not_run_products,
        "all_passed": complete,
        "exit_code": exit_code,
        "seconds": round(time.time() - started_at, 3),
    }
    report = {
        "version": "som.typescript-output-product-checks.v1",
        "issue": 76,
        "recorded_at": utc_now(),
        "toolchain": toolchain_info,
        "cache": cache_info,
        "summary": summary,
        "products": rows,
        "processes": process_records,
    }
    return report, exit_code


def save_raw(out_dir: pathlib.Path, name: str, run: ts_suite.TsRun) -> str:
    data = json.dumps(
        {"raw": run.raw, "transcript": run.transcript, "failures": run.failures},
        indent=1,
        ensure_ascii=False,
    ).encode()
    raw_path = out_dir / "raw" / f"{name}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def leased_port(ports: base_hosts.Ports) -> Iterator[int]:
    port = ports.take()
    try:
        yield port
    finally:
        ports.give(port)


def run_startup_gate(
    product_root: pathlib.Path,
    pg_host: str,
    key: str,
    stamp: str,
    env: dict[str, str],
    ports: base_hosts.Ports,
    tracker: ts_host.ProcessTracker,
) -> dict[str, Any]:
    started = time.monotonic()
    command = [ts_host.NODE_BIN, "src/main.ts"]

    # 1. Missing DATABASE_URL -> exit 2 and 1 stderr line
    env_no_db = dict(env)
    env_no_db.pop("DATABASE_URL", None)
    with leased_port(ports) as no_db_port:
        env_no_db["SOM_APP_ADDR"] = f"127.0.0.1:{no_db_port}"
        missing_db = run_cmd(command, product_root, env_no_db, timeout=30.0)

    # 2. Invalid SOM_APP_ADDR -> exit 2 and 1 stderr line
    startup_db = rust_products.fresh_database(pg_host, f"ts76_start_{key}_{stamp}")
    env_bad_addr = {**env, "DATABASE_URL": startup_db, "SOM_APP_ADDR": "invalid-address"}
    invalid_addr = run_cmd(command, product_root, env_bad_addr, timeout=30.0)

    # 3. SIGTERM on a running server -> exit 0
    with leased_port(ports) as port:
        ts_host.check_port_allowed(port)
        env_run = {**env, "DATABASE_URL": startup_db, "SOM_APP_ADDR": f"127.0.0.1:{port}"}
        log_path = product_root.parent / f"{key}-startup-sigterm-{port}.log"
        proc: subprocess.Popen[Any] | None = None
        record: dict[str, Any] | None = None
        stopped = False
        sigterm_info: dict[str, Any] = {"ready": False, "cwd_verified": False, "exit_code": None}
        try:
            with log_path.open("wb") as log_file:
                proc = subprocess.Popen(
                    command,
                    cwd=product_root,
                    env=env_run,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            record = tracker.register(proc.pid, "typescript_startup_probe", product_root, command)
            try:
                ts_host.wait_http_ready(proc, port, "/healthz", log_path=log_path, timeout=20.0)
                sigterm_info["ready"] = True
                matched, _ = ts_host.verify_process_cwd(proc.pid, product_root)
                sigterm_info["cwd_verified"] = matched
                code = tracker.stop(proc, product_root, record, sig=signal.SIGTERM, timeout=10.0)
                stopped = True
                sigterm_info["exit_code"] = code
            except Exception as err:  # noqa: BLE001
                sigterm_info["error"] = f"{type(err).__name__}: {err}"
                with contextlib.suppress(Exception):
                    tracker.stop(proc, product_root, record, sig=signal.SIGTERM, timeout=5.0)
                    stopped = True
        finally:
            if not stopped and proc is not None and record is not None:
                with contextlib.suppress(Exception):
                    tracker.stop(proc, product_root, record, sig=signal.SIGTERM, timeout=5.0)

    duration_ms = round((time.monotonic() - started) * 1000)
    return evaluate_startup(missing_db, invalid_addr, sigterm_info, command, duration_ms)


def check_one_product(
    case_id: str,
    product_source: pathlib.Path,
    prompt: dict[str, Any],
    cache_dir: pathlib.Path,
    work_dir: pathlib.Path,
    out_dir: pathlib.Path,
    pg_host: str,
    qa_url: str,
    python_exe: str,
    ports: base_hosts.Ports,
    tracker: ts_host.ProcessTracker,
) -> dict[str, Any]:
    key = re.sub(r"[^a-z0-9]+", "_", case_id.lower()).strip("_")
    stamp = f"{int(time.time() * 1000) % 100000000}"
    product_work = work_dir / f"ts_{key}"
    env_scratch = work_dir / f"env_{key}"
    env_scratch.mkdir(parents=True, exist_ok=True)

    before_files = ts_host.copy_product(product_source, product_work)
    files_sha256 = hashlib.sha256(
        json.dumps(before_files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    env = ts_host.toolchain_env(cache_dir, env_scratch)

    gates: dict[str, dict[str, Any]] = {}

    # Gate 1: install
    install_cmd = [*ts_host.NPM_INVOCATION, "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"]
    install_res = run_cmd(install_cmd, product_work, env)
    gates["install"] = evaluate_install(install_res)

    if not gates["install"]["passed"]:
        for skipped_gate in GATES_ORDER[1:]:
            gates[skipped_gate] = make_gate(
                "not_run",
                command=None,
                exit_code=None,
                duration_ms=0,
                reason="install gate failed",
                output_summary="not_run because install failed",
            )
        return evaluate_product_row(case_id, prompt["version"], files_sha256, gates)

    # Gate 2: typecheck
    tsc_cmd = [ts_host.NODE_BIN, "node_modules/typescript/bin/tsc", "-p", "tsconfig.json"]
    gates["typecheck"] = evaluate_typecheck(run_cmd(tsc_cmd, product_work, env))

    # Gate 3: format_check
    ts_files = [
        rel
        for rel in ("src/main.ts", "src/app.ts", "src/schemas.ts", "src/db.ts", "src/migrate.ts", "test/api.test.ts")
        if (product_work / rel).is_file()
    ]
    extra_ts = sorted(
        p.relative_to(product_work).as_posix()
        for p in product_work.rglob("*.ts")
        if "node_modules" not in p.relative_to(product_work).parts
        and p.relative_to(product_work).as_posix() not in ts_files
    )
    prettier_cmd = [
        ts_host.NODE_BIN,
        "node_modules/prettier/bin/prettier.cjs",
        "--check",
        "--no-config",
        "--no-editorconfig",
        *ts_files,
        *extra_ts,
    ]
    gates["format_check"] = evaluate_format_check(run_cmd(prettier_cmd, product_work, env))

    # Gate 4: test (with TEST_DATABASE_URL on a fresh DB, and without TEST_DATABASE_URL)
    test_db_url = rust_products.fresh_database(pg_host, f"ts76_test_{key}_{stamp}")
    test_cmd = [ts_host.NODE_BIN, "--test", "--test-concurrency=1", "test/api.test.ts"]
    with_db_res = run_cmd(test_cmd, product_work, {**env, "TEST_DATABASE_URL": test_db_url})
    without_db_res = run_cmd(test_cmd, product_work, env)
    gates["test"] = evaluate_test_gate(with_db_res, without_db_res)

    # Gate 5: lock_unchanged
    lock_started = time.monotonic()
    after_files = ts_host.snapshot_outside_node_modules(product_work)
    gates["lock_unchanged"] = evaluate_lock_unchanged(
        before_files,
        after_files,
        duration_ms=round((time.monotonic() - lock_started) * 1000),
    )

    # Compile and assemble the Python reference product from the QA compiler
    py_files = base_hosts.bundle_files(qa_url, prompt, "python")

    # Gate 6: schema_equivalence
    schema_started = time.monotonic()
    migrate_cmd = [
        ts_host.NODE_BIN,
        "--input-type=module",
        "-e",
        'import pg from "pg"; import { migrate } from "./src/migrate.ts"; '
        "const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL }); "
        "try { await migrate(pool); } finally { await pool.end(); }",
    ]
    try:
        ts_schema_url = rust_products.fresh_database(pg_host, f"ts76_schema_ts_{key}_{stamp}")
        py_schema_url = rust_products.fresh_database(pg_host, f"ts76_schema_py_{key}_{stamp}")
        mig_res = run_cmd(migrate_cmd, product_work, {**env, "DATABASE_URL": ts_schema_url})
        if mig_res["exit_code"] != 0:
            raise RuntimeError(f"TypeScript migrate failed ({mig_res['exit_code']}): {mig_res['stderr']}")
        py_schema_root = work_dir / f"py_schema_{key}"
        base_hosts.products.write(py_schema_root, py_files)
        ts_host.migrate_python_product(work_dir, py_schema_root, py_schema_url, python_exe)
        ts_schema = rust_products.schema(ts_schema_url, EXCLUDED_SCHEMA_TABLES)
        py_schema = rust_products.schema(py_schema_url, EXCLUDED_SCHEMA_TABLES)
        gates["schema_equivalence"] = evaluate_schema_equivalence(
            ts_schema,
            py_schema,
            migrate_cmd,
            round((time.monotonic() - schema_started) * 1000),
        )
    except Exception as err:  # noqa: BLE001
        gates["schema_equivalence"] = make_gate(
            "failed",
            command=migrate_cmd,
            exit_code=1,
            duration_ms=round((time.monotonic() - schema_started) * 1000),
            reason=f"{type(err).__name__}: {err}",
            output_summary=traceback.format_exc()[-2000:],
        )

    # Gate 7 (parity) & Gate 8 (strict_refusals)
    parity_started = time.monotonic()
    strict_started = parity_started
    run_server_cmd = [ts_host.NODE_BIN, "src/main.ts"]
    try:
        py_parity_url = rust_products.fresh_database(pg_host, f"ts76_parity_py_{key}_{stamp}")
        ts_parity_url = rust_products.fresh_database(pg_host, f"ts76_parity_ts_{key}_{stamp}")
        with (
            leased_port(ports) as py_port,
            ts_host.python_product(
                work_dir,
                f"py_parity_{key}",
                prompt,
                py_files,
                py_parity_url,
                py_port,
                python_exe,
                tracker,
            ) as py_base,
        ):
            python_run = ts_suite.run(prompt, py_base)

        with (
            leased_port(ports) as ts_port,
            ts_host.typescript_product(
                product_work,
                ts_parity_url,
                ts_port,
                env,
                tracker,
            ) as ts_base,
        ):
            ts_run = ts_suite.run(prompt, ts_base)
            parity_duration_ms = round((time.monotonic() - parity_started) * 1000)
            script_steps = len(ts_run.transcript)
            script_failures = len(ts_run.failures)

            strict_started = time.monotonic()
            ts_run.strict()
            strict_duration_ms = round((time.monotonic() - strict_started) * 1000)

        parity_slice = ts_suite.TsRun(ts_run.prof, ts_run.send)
        parity_slice.transcript = ts_run.transcript[:script_steps]
        parity_slice.raw = ts_run.raw[:script_steps]
        parity_slice.failures = ts_run.failures[:script_failures]

        diffs = ts_suite.compare(python_run.transcript, parity_slice.transcript)
        py_raw_sha = save_raw(out_dir, f"py_{key}", python_run)
        ts_raw_sha = save_raw(out_dir, f"ts_{key}", ts_run)

        gates["parity"] = evaluate_parity(
            python_run,
            parity_slice,
            diffs,
            parity_duration_ms,
            run_server_cmd,
            python_raw_sha256=py_raw_sha,
            ts_raw_sha256=ts_raw_sha,
        )
        gates["strict_refusals"] = evaluate_strict_refusals(
            ts_run.transcript[script_steps:],
            ts_run.failures[script_failures:],
            strict_duration_ms,
            run_server_cmd,
        )
    except Exception as err:  # noqa: BLE001
        err_msg = f"{type(err).__name__}: {err}"
        trace = traceback.format_exc()[-2000:]
        if "parity" not in gates:
            gates["parity"] = make_gate(
                "failed",
                command=run_server_cmd,
                exit_code=1,
                duration_ms=round((time.monotonic() - parity_started) * 1000),
                reason=err_msg,
                output_summary=trace,
            )
        if "strict_refusals" not in gates:
            gates["strict_refusals"] = make_gate(
                "failed",
                command=run_server_cmd,
                exit_code=1,
                duration_ms=round((time.monotonic() - strict_started) * 1000),
                reason=err_msg,
                output_summary=trace,
            )

    # Gate 9: startup
    try:
        gates["startup"] = run_startup_gate(product_work, pg_host, key, stamp, env, ports, tracker)
    except Exception as err:  # noqa: BLE001
        gates["startup"] = make_gate(
            "failed",
            command=run_server_cmd,
            exit_code=1,
            duration_ms=0,
            reason=f"{type(err).__name__}: {err}",
            output_summary=traceback.format_exc()[-2000:],
        )

    return evaluate_product_row(case_id, prompt["version"], files_sha256, gates)


def load_prompt_file(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "prompt" in data and isinstance(data["prompt"], dict):
        return data["prompt"]
    return data


def collect_work_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if args.manifest is not None:
        manifest = json.loads(args.manifest.read_text())
        entries = manifest["products"] if isinstance(manifest, dict) and "products" in manifest else manifest
        for entry in entries:
            prompt = (
                entry["prompt"]
                if isinstance(entry["prompt"], dict)
                else load_prompt_file(pathlib.Path(entry["prompt"]))
            )
            items.append({
                "id": entry["id"],
                "product": pathlib.Path(entry["product"]),
                "prompt": prompt,
            })
    elif args.product is not None and args.prompt is not None:
        prompt = load_prompt_file(args.prompt)
        case_id = args.case_id or args.product.name
        items.append({"id": case_id, "product": args.product, "prompt": prompt})
    else:
        raise SystemExit("pass either (--product DIR --prompt FILE) or (--manifest FILE)")
    return items


def prepare_out_paths(out_arg: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path | None]:
    """Return (out_dir, report_path, mirror_dir) ensuring no existing .local data is overwritten."""
    if any(part.startswith(("npm-cache-", "prettier-")) for part in out_arg.parts):
        raise SystemExit(f"refusing to write into protected path: {out_arg}")
    runtime_root = ts_host.find_runtime_root()
    if out_arg.suffix == ".json":
        if out_arg.exists():
            raise SystemExit(f"refusing to overwrite existing output file: {out_arg}")
        out_dir = out_arg.parent / out_arg.stem
        if out_dir.exists() and any(out_dir.iterdir()):
            raise SystemExit(f"refusing to overwrite existing non-empty output directory: {out_dir}")
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir, out_arg, None

    if out_arg.exists() and any(out_arg.iterdir()):
        raise SystemExit(f"refusing to overwrite existing non-empty output directory: {out_arg}")
    mirror_dir: pathlib.Path | None = None
    if not out_arg.is_absolute() and runtime_root != pathlib.Path.cwd():
        candidate = runtime_root / out_arg
        if candidate.exists() and any(candidate.iterdir()):
            raise SystemExit(f"refusing to overwrite existing non-empty output directory: {candidate}")
        mirror_dir = candidate
    out_arg.mkdir(parents=True, exist_ok=True)
    return out_arg, out_arg / "report.json", mirror_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--product", type=pathlib.Path, help="directory of a TypeScript product to check")
    parser.add_argument("--prompt", type=pathlib.Path, help="JSON file with the typed prompt for --product")
    parser.add_argument("--case-id", default=None, help="optional case id for --product")
    parser.add_argument("--manifest", type=pathlib.Path, help="JSON manifest of [{id, product, prompt}, ...]")
    parser.add_argument("--cache", type=pathlib.Path, required=True, help="path to the vendored npm cache")
    parser.add_argument("--out", type=pathlib.Path, required=True, help="new output directory or .json file path")
    parser.add_argument("--qa", default=None, help="optional running QA compiler URL (never 8765 or 18765)")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter for reference products")
    args = parser.parse_args(argv)

    if args.qa is not None:
        ts_host.check_url_allowed(args.qa)

    started_at = time.time()
    cache_dir = ts_host.resolve_local_path(args.cache)
    toolchain_info = ts_host.verify_toolchain(cache_dir)
    cache_info_before = dict(toolchain_info["cache"])
    work_items = collect_work_items(args)
    out_dir, report_path, mirror_dir = prepare_out_paths(args.out)

    tracker = ts_host.ProcessTracker()
    ports = base_hosts.Ports()
    rows: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="som-ts-gates-", ignore_cleanup_errors=True) as scratch:
        work = pathlib.Path(scratch)
        with (
            leased_port(ports) as pg_port,
            ts_host.pg_cluster(work, pg_port, tracker) as pg_host,
        ):
            if args.qa is not None:
                for item in work_items:
                    rows.append(
                        check_one_product(
                            item["id"],
                            item["product"],
                            item["prompt"],
                            cache_dir,
                            work,
                            out_dir,
                            pg_host,
                            args.qa,
                            args.python,
                            ports,
                            tracker,
                        )
                    )
            else:
                with (
                    leased_port(ports) as qa_port,
                    ts_host.qa_compiler(work, qa_port, tracker) as qa_url,
                ):
                    for item in work_items:
                        rows.append(
                            check_one_product(
                                item["id"],
                                item["product"],
                                item["prompt"],
                                cache_dir,
                                work,
                                out_dir,
                                pg_host,
                                qa_url,
                                args.python,
                                ports,
                                tracker,
                            )
                        )

    cache_sha_after, cache_files_after, cache_links_after = ts_host.tree_sha(cache_dir)
    cache_info = {
        **cache_info_before,
        "tree_sha256_after": cache_sha_after,
        "unchanged": (
            cache_sha_after == cache_info_before.get("tree_sha256")
            and cache_files_after == cache_info_before.get("tree_files")
            and cache_links_after == cache_info_before.get("tree_symlinks")
        ),
    }
    if not cache_info["unchanged"]:
        raise RuntimeError(f"vendored npm cache at {cache_dir} was modified during the run")

    report, exit_code = build_report(rows, cache_info, toolchain_info, tracker.records, started_at)
    report_bytes = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode()
    report_path.write_bytes(report_bytes)
    (out_dir / "summary.json").write_text(json.dumps(report["summary"], indent=2, ensure_ascii=False) + "\n")

    if mirror_dir is not None and not mirror_dir.exists():
        mirror_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(out_dir, mirror_dir)

    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
