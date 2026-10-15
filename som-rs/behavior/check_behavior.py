"""Run the behavior suite (issues #46 and #49).

usage:
  check_behavior.py python [--only ID ...]   every frozen acceptance case on its Python product
  check_behavior.py faults [--only FAULT ...] seeded faults in Python products must fail the suite
  check_behavior.py parity [--only ID ...]   v1 to v5 Python against Rust, and D1 to D5 on Rust alone

The suite owns its PostgreSQL cluster and every product process, on QA ports only (never 8765
or 18765), and stops them on exit. Bundles come from the QA compiler (--qa). Raw transcripts go
under --out and stay local; the report keeps their sha256.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import pathlib
import re
import signal
import sys
import tempfile
import time
import traceback
from typing import Any

import faults
import hosts
import suite


def cases() -> list[dict]:
    """Every frozen acceptance case: the v1 to v5 cases the Rust profile admits."""
    out = []
    for case in hosts.targets.sources():
        if case.get("rejection") or hosts.targets.rust_profile.check(case["prompt"]) is not None:
            continue
        out.append({"id": case["id"], "version": case["prompt"]["version"], "prompt": case["prompt"]})
    total = hosts.products.CASE_INVENTORY["counts"]["acceptance_total"]
    if len(out) != total:
        raise SystemExit(f"{len(out)} acceptance cases, the frozen plan has {total}")
    return out


def key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def files_sha(files: dict[str, str]) -> str:
    """The product files hash, computed as check_rust_products.py records files_sha256."""
    return hosts.targets.sha(hosts.targets.dump(files).encode())


def keep(out: pathlib.Path, name: str, run: suite.Run) -> str:
    """Write the raw and normalized exchange; return its sha256."""
    data = json.dumps({"raw": run.raw, "transcript": run.transcript, "failures": run.failures},
                      indent=1, ensure_ascii=False).encode()
    path = out / "raw" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def categories(run: suite.Run) -> list[str]:
    return sorted({entry["category"] for entry in run.transcript})


def python_case(host: hosts.Host, out: pathlib.Path, case: dict) -> dict:
    name = f"py_{key(case['id'])}"
    files = hosts.bundle_files(host.qa, case["prompt"], "python")
    with host.python_product(name, case["prompt"], files) as base:
        run = suite.run(case["prompt"], base)
    return {"id": case["id"], "version": case["version"], "files_sha256": files_sha(files),
            "steps": len(run.transcript),
            "categories": categories(run), "failures": len(run.failures), "first_failures": run.failures[:10],
            "raw_sha256": keep(out, name, run), "passed": not run.failures}


def parity_case(host: hosts.Host, out: pathlib.Path, case: dict) -> dict:
    py_name, rs_name = f"py_{key(case['id'])}", f"rs_{key(case['id'])}"
    py_files = hosts.bundle_files(host.qa, case["prompt"], "python")
    rs_files = hosts.bundle_files(host.qa, case["prompt"], "rust")
    with host.python_product(py_name, case["prompt"], py_files) as base:
        python = suite.run(case["prompt"], base)
    with host.rust_product(rs_name, case["prompt"], rs_files) as base:
        rust = suite.run(case["prompt"], base)
        script_steps = len(rust.transcript)
        script_failures = len(rust.failures)
        rust.strict()
    diffs = suite.compare(python.transcript, rust.transcript[:script_steps])
    strict = rust.failures[script_failures:]
    row = {"id": case["id"], "version": case["version"],
           "python_files_sha256": files_sha(py_files), "rust_files_sha256": files_sha(rs_files),
           "steps": script_steps,
           "strict_steps": len(rust.transcript) - script_steps,
           "python_failures": len(python.failures), "rust_failures": script_failures,
           "differences": len(diffs), "first_differences": diffs[:10],
           "strict_failures": len(strict), "first_strict_failures": strict[:10],
           "first_rust_failures": rust.failures[:min(script_failures, 10)],
           "python_raw_sha256": keep(out, py_name, python), "rust_raw_sha256": keep(out, rs_name, rust)}
    row["passed"] = not (python.failures or rust.failures or diffs)
    return row


def fault_case(host: hosts.Host, out: pathlib.Path, fault: dict, prompts: dict) -> dict:
    prompt = prompts[fault["case"]]
    files = hosts.bundle_files(host.qa, prompt, "python")
    seeded = faults.apply(files, fault)
    name = f"fault_{key(fault['id'])}"
    with host.python_product(name, prompt, seeded) as base:
        run = suite.run(prompt, base)
    caught = sorted({failure["category"] for failure in run.failures})
    return {"id": fault["id"], "case": fault["case"], "category": fault["category"],
            "description": fault["description"], "failures": len(run.failures), "caught_by": caught,
            "first_failures": run.failures[:3], "raw_sha256": keep(out, name, run),
            "passed": fault["category"] in caught}


def interrupted(signum: int, _frame: object) -> None:
    """The first SIGTERM, SIGHUP or SIGINT stops the run; later ones only kill again."""
    first = not hosts.STOP.is_set()
    hosts.kill_all()
    if first:
        raise KeyboardInterrupt(f"signal {signum}")


def main() -> int:
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, interrupted)
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("python", "faults", "parity"))
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--qa", default="http://127.0.0.1:18901")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--target-dir", type=pathlib.Path, default=None)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    if "8765" in args.qa:
        raise SystemExit("the QA compiler must not be the default on 8765")
    started = time.time()
    every = cases()
    prompts = {case["id"]: case["prompt"] for case in every}
    if args.mode == "faults":
        work_items = [f for f in faults.FAULTS if args.only is None or f["id"] in args.only]
        unknown = set(args.only or ()) - {f["id"] for f in faults.FAULTS}
    else:
        work_items = [c for c in every if args.only is None or c["id"] in args.only]
        unknown = set(args.only or ()) - {c["id"] for c in every}
    if unknown:
        raise SystemExit(f"unknown ids: {sorted(unknown)}")
    args.out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="som-behavior-", ignore_cleanup_errors=True) as scratch:
        work = pathlib.Path(scratch)
        ports = hosts.Ports()
        pg_port = ports.take()
        with hosts.cluster(work, pg_port) as pg:
            host = hosts.Host(args.qa, pg, work / "products", args.target_dir or work / "target", args.python)
            host.ports = ports

            def one(item: dict) -> dict:
                try:
                    if args.mode == "python":
                        row = python_case(host, args.out, item)
                    elif args.mode == "parity":
                        row = parity_case(host, args.out, item)
                    else:
                        row = fault_case(host, args.out, item, prompts)
                except Exception as error:  # noqa: BLE001
                    row = {"id": item["id"], "passed": False, "error": f"{type(error).__name__}: {error}"[:3000],
                           "trace": traceback.format_exc()[-3000:]}
                print(f"{item['id']}: {'ok' if row['passed'] else 'FAIL'}", file=sys.stderr, flush=True)
                return row

            executor = concurrent.futures.ThreadPoolExecutor(max(1, args.jobs))
            try:
                rows = list(executor.map(one, work_items))
            except KeyboardInterrupt:
                # Every process is killed and no new one starts, so the workers end soon. Wait for
                # them before the cluster and the scratch directory go away.
                hosts.kill_all()
                executor.shutdown(wait=True, cancel_futures=True)
                raise
            executor.shutdown()
    summary: dict[str, Any] = {"mode": args.mode, "total": len(rows), "passed": sum(r["passed"] for r in rows),
                               "failed": [r["id"] for r in rows if not r["passed"]],
                               "seconds": round(time.time() - started)}
    if args.mode == "python":
        summary["by_version"] = {v: sum(1 for r in rows if r.get("version") == v) for v in sorted(
            {r.get("version") for r in rows if r.get("version")})}
        summary["steps"] = sum(r.get("steps", 0) for r in rows)
        summary["categories"] = sorted({c for r in rows for c in r.get("categories", [])})
        summary["complete"] = args.only is None and len(rows) == len(every)
    if args.mode == "parity":
        summary["steps"] = sum(r.get("steps", 0) for r in rows)
        summary["strict_steps"] = sum(r.get("strict_steps", 0) for r in rows)
        summary["strict_failed"] = [r["id"] for r in rows if r.get("strict_failures")]
        summary["complete"] = args.only is None and len(rows) == len(every)
    if args.mode == "faults":
        caught = {r["category"] for r in rows if r["passed"]}
        summary["categories_without_a_caught_fault"] = sorted(set(suite.CATEGORIES) - caught)
        summary["complete"] = args.only is None
    report = {"recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "summary": summary,
              "rows": rows}
    (args.out / f"report-{args.mode}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=1))
    ok = not summary["failed"] and not summary.get("strict_failed") and not summary.get(
        "categories_without_a_caught_fault")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
