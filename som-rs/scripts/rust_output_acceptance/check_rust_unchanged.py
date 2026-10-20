#!/usr/bin/env python3
"""Check that a candidate assembles every Rust product of the accepted #55 run to the same files.

acceptance-plan.json issue57 (amendment 004): every phase 1 and phase 2 Rust product of
rust-output-055 run-003 assembles to byte-identical files on the candidate.

- phase 1: the cases of a check_rust_products.py report made on the candidate (--phase1) against
  run-003 rust-products.json, by case id and files_sha256;
- phase 2: every case of check_phase2.py, compiled with target rust from its typed prompt and
  assembled on the candidate, against the run-003 phase2/cases record. A case whose current
  inventory outcome is a refusal must give that refusal (RS12, RS13: compiler-v1, amendment 004).

usage: check_rust_unchanged.py --qa URL --run RUN_DIR --phase1 REPORT.json --out OUT.json [--workers N]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
sys.path.insert(0, str(SOM_RS / "behavior" / "phase2"))
import check_phase2 as phase2  # noqa: E402

FORBIDDEN = (":8765", ":18765")


def file_sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def phase1(run: pathlib.Path, report_path: pathlib.Path) -> dict:
    old = {c["id"]: c for c in phase2.read(run / "rust-products.json")["cases"]}
    new = {c["id"]: c for c in phase2.read(report_path)["cases"]}
    rows = []
    for case_id in sorted(old):
        before = old[case_id].get("files_sha256")
        after = new.get(case_id, {}).get("files_sha256")
        rows.append({"id": case_id, "files_sha256": [before, after], "passed": before is not None and before == after})
    extra = sorted(set(new) - set(old))
    failed = [r["id"] for r in rows if not r["passed"]] + [f"{i}: not in the run" for i in extra]
    return {"record": {"path": str(run / "rust-products.json"), "sha256": file_sha(run / "rust-products.json")},
            "report": {"path": str(report_path), "sha256": file_sha(report_path)},
            "total": len(rows), "passed": sum(r["passed"] for r in rows), "failed": failed, "cases": rows}


def phase2_case(qa_url: str, run: pathlib.Path, case: dict) -> dict:
    record = phase2.read(run / "phase2" / "cases" / f"{phase2.key(case['id'])}.json")["rust"]
    row: dict = {"id": case["id"]}
    try:
        prompt = case["load_prompt"]()
        if phase2.qa.sha(prompt) != case["prompt_sha256"]:
            raise RuntimeError("prompt hash differs from the run record")
        files = phase2.rust_files(qa_url, case, prompt, row)
    except Exception as error:  # noqa: BLE001 - recorded as the case failure
        row.update(passed=False, problem=f"{type(error).__name__}: {error}")
        return row
    rust = row["rust"]
    if files is None:
        row.update(passed=True, moved_refusal=rust["refusal"], recorded=record["expected"])
    else:
        row.update(files_sha256=[record.get("files_sha256"), rust["files_sha256"]],
                   passed=record.get("files_sha256") == rust["files_sha256"])
        if not row["passed"]:
            row["problem"] = "the assembled files differ from run-003"
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qa", required=True)
    parser.add_argument("--run", type=pathlib.Path, required=True)
    parser.add_argument("--phase1", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.qa.rstrip("/").endswith(FORBIDDEN):
        parser.error("QA checks never use ports 8765 or 18765")
    if args.out.exists():
        parser.error(f"{args.out} exists")
    cases = phase2.cases()
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(lambda case: phase2_case(args.qa, args.run, case), cases))
    for row in rows:
        if not row["passed"]:
            print(f"{row['id']}: FAIL {row.get('problem')}", file=sys.stderr)
    two = {"total": len(rows), "passed": sum(r["passed"] for r in rows),
           "files_equal": sum(1 for r in rows if "files_sha256" in r and r["passed"]),
           "refusals": sorted(r["id"] for r in rows if "moved_refusal" in r),
           "failed": [r["id"] for r in rows if not r["passed"]], "cases": rows}
    one = phase1(args.run, args.phase1)
    report = {"version": "som.rust-output-unchanged.v1", "issue": 57, "qa": args.qa,
              "run": str(args.run), "phase1": one, "phase2": two,
              "passed": not one["failed"] and not two["failed"]}
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({"passed": report["passed"],
                      "phase1": {k: one[k] for k in ("total", "passed", "failed")},
                      "phase2": {k: two[k] for k in ("total", "passed", "files_equal", "refusals", "failed")}}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
