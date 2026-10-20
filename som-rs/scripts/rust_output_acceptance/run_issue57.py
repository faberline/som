#!/usr/bin/env python3
"""Run the issue #57 and #58 acceptance (acceptance-plan.json issue57, amendment 004) on one candidate.

Each check runs in order as its own process, as run_acceptance.py runs the #55 steps, with the
vendored crate set as CARGO_HOME. Every step keeps its logs and a receipt with its exit code and
output hashes; summary.json maps each plan check to the steps that prove it. The candidate
identity and the default on 8765 are recorded before and after; 8765 gets read-only compile and
assemble calls from check_targets.py only (the reference compiler).

Each step passes on exit code 0, except rust-products (see judge_rust_products).
The independent audit (the last plan check) is not run here: it reads this run's raw outputs.

usage: run_issue57.py --candidate DIR --vendor DIR --out NEW_DIR
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_acceptance as ra  # noqa: E402

RUN_055 = ra.LOCAL / "rust-output-055/run-003"


def steps(candidate: pathlib.Path, run: pathlib.Path, qa: str) -> list[dict]:
    py = str(ra.VENV_PYTHON)
    phase2 = ["--refusals", "phase2"]
    return [
        {"name": "contract", "cwd": ra.REPO, "argv": [py, str(ra.TESTS / "check_contract.py"), "--host"], "outputs": []},
        {"name": "python-replay", "cwd": ra.REPO,
         "argv": [py, str(HERE / "python_replay.py"), "--base-url", qa, "--public-run", str(ra.PUBLIC_RUN),
                  "--legacy-run", str(ra.LEGACY_RUN), "--out", str(run / "python-replay.json"), "--asset-break"],
         "outputs": ["python-replay.json"]},
        {"name": "python-controls", "cwd": ra.REPO,
         "argv": [py, str(ra.TESTS / "check_python_controls.py"), "--base-url", qa,
                  "--out", str(run / "python-controls.json")], "outputs": ["python-controls.json"]},
        {"name": "validate-cases", "cwd": ra.REPO,
         "argv": [py, str(ra.TESTS / "validate_cases.py"), "--base-url", qa, "--out", str(run / "validate-cases.json")],
         "outputs": ["validate-cases.json"]},
        {"name": "targets", "cwd": ra.QA_DIR,
         "argv": [py, "check_targets.py", "--qa", qa, "--reference", ra.DEFAULT, "--out", str(run / "targets.json"),
                  *phase2, "--asset-break"], "outputs": ["targets.json"]},
        {"name": "cli", "cwd": ra.REPO,
         "argv": [py, str(HERE / "check_cli.py"), "--binary", str(candidate / "som"), "--base-url", qa,
                  "--out", str(run / "cli.json"), *phase2], "outputs": ["cli.json"]},
        {"name": "rust-products", "cwd": ra.QA_DIR, "judge": judge_rust_products,
         "argv": [py, str(HERE / "run_acceptance.py"), "cluster-gates", "--qa", qa, "--work", str(run / "products"),
                  "--target-dir", str(run / "target"), "--out", str(run / "rust-products.json"), "--no-gates", *phase2],
         "outputs": ["rust-products.json"]},
        {"name": "rust-unchanged", "cwd": ra.REPO,
         "argv": [py, str(HERE / "check_rust_unchanged.py"), "--qa", qa, "--run", str(RUN_055),
                  "--phase1", str(run / "rust-products.json"), "--out", str(run / "rust-unchanged.json")],
         "outputs": ["rust-unchanged.json"]},
        {"name": "issue57", "cwd": ra.REPO,
         "argv": [py, str(HERE / "check_issue57.py"), "run", "--qa", qa, "--out", str(run / "issue57")],
         "outputs": ["issue57/summary.json"]},
        {"name": "name-collisions", "cwd": ra.REPO,
         "argv": [py, str(HERE / "check_name_collisions.py"), "--qa", qa, "--out", str(run / "name-collisions.json")],
         "outputs": ["name-collisions.json"]},
    ]


PARITY_BLOCKER = "behavior parity: not run, pass --parity with a check_behavior.py report"


def judge_exit(receipt: dict, _run: pathlib.Path) -> tuple[bool, dict]:
    return receipt["exit_code"] == 0, {}


def judge_rust_products(receipt: dict, run: pathlib.Path) -> tuple[bool, dict]:
    """Plan check 5 asks for the bundle checks and assemble_refusals, not the product gates or parity:
    a --no-gates report exits 2 with the parity blocker alone. The products themselves are compared
    with the accepted #55 products by rust-unchanged."""
    report = ra.load(run / "rust-products.json")
    summary = report["summary"]
    refusals = {row["id"]: row for row in report["assemble_refusals"]}
    ok = (receipt["exit_code"] in (0, 2) and not summary["failed"] and summary["blocked"] in ([], [PARITY_BLOCKER])
          and summary["bundle_checks"]["passed"] == summary["bundle_checks"]["total"]
          == summary["acceptance_cases"]["frozen_acceptance_total"]
          and summary["assemble_refusals"]["passed"] == summary["assemble_refusals"]["total"] == len(refusals)
          and all(row["passed"] for row in refusals.values()) and report["refusal_plan"] == "phase2"
          and refusals.get("A6", {}).get("code") == "INVALID_VALUE")
    return ok, {"exit_code": receipt["exit_code"], "bundle_checks": summary["bundle_checks"],
                "assemble_refusals": summary["assemble_refusals"], "blocked": summary["blocked"],
                "A6_amendment_004": refusals.get("A6", {}).get("code")}


# Each acceptance-plan.json issue57 check and the steps whose exit codes prove it.
PLAN = [["python-replay"], ["python-controls"], ["validate-cases"], ["targets", "cli"], ["rust-products"],
        ["targets", "rust-unchanged"], ["rust-unchanged"], ["issue57"], ["name-collisions"], None]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=pathlib.Path, required=True)
    parser.add_argument("--vendor", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    candidate, vendor, run = args.candidate.resolve(), args.vendor.resolve(), args.out.resolve()
    if run.exists():
        raise SystemExit(f"preserve existing output: {run}")
    if not run.is_relative_to(ra.LOCAL):
        raise SystemExit(f"write under {ra.LOCAL}")
    checks = ra.load(ra.CONTRACT / "acceptance-plan.json")["issue57"]["checks"]
    if len(checks) != len(PLAN):
        raise SystemExit(f"acceptance-plan.json issue57 has {len(checks)} checks, this runner maps {len(PLAN)}")
    tools = ra.tools_state()
    if tools["porcelain"]:
        raise SystemExit("commit the acceptance tools first:\n" + tools["porcelain"])
    before = ra.candidate_identity(candidate)
    if before["problems"]:
        raise SystemExit(f"candidate identity problems: {before['problems']}")
    vendor_receipt = ra.load(vendor / "vendor.json")
    vendor_tree = ra.tree_sha(vendor / "vendor")
    if vendor_tree != vendor_receipt["vendor_tree_sha256"]:
        raise SystemExit("vendored crates differ from vendor.json")
    run.mkdir(parents=True)
    (run / "logs").mkdir()
    (run / "receipts").mkdir()
    qa = f"http://127.0.0.1:{before['port']}"
    env_delta = {"CARGO_HOME": str(vendor / "cargo-home"), "CARGO_NET_OFFLINE": "true", "PYTHONDONTWRITEBYTECODE": "1"}
    header = {"started_at": ra.now(), "candidate_before": before, "default_before": ra.default_state(),
              "tools": tools, "vendor": {"vendor_json_sha256": ra.sha_file(vendor / "vendor.json"),
                                         "vendor_tree_sha256": vendor_tree},
              "venv": ra.venv_packages(), "env_delta": env_delta, "plan_checks": checks}
    (run / "header.json").write_text(json.dumps(header, indent=1, sort_keys=True) + "\n")

    receipts = []
    for index, step in enumerate(steps(candidate, run, qa), 1):
        step.setdefault("judge", judge_exit)
        receipts.append(ra.run_step(index, step, run, env_delta))
    passed = {r["step"]: r["passed"] for r in receipts}
    coverage = [{"index": i, "check": check, "steps": names,
                 "state": "not run" if names is None else ("passed" if all(passed[n] for n in names) else "failed")}
                for i, (check, names) in enumerate(zip(checks, PLAN))]
    after = ra.candidate_identity(candidate)
    default_after = ra.default_state()
    blockers = ([f"step {r['step']} failed" for r in receipts if not r["passed"]]
                + [f"candidate after: {p}" for p in after["problems"]]
                + (["candidate identity changed during the run"]
                   if {k: before[k] for k in ("binary_sha256", "assets_tree_sha256", "health")}
                   != {k: after[k] for k in ("binary_sha256", "assets_tree_sha256", "health")} else [])
                + (["the default on 8765 changed during the run"] if default_after != header["default_before"] else []))
    summary = {"version": "som.rust-output-issue57-run.v1", "issues": [57, 58], "ended_at": ra.now(),
               "steps": passed, "plan_coverage": coverage, "candidate_after": after, "default_after": default_after,
               "blockers": blockers, "passed": not blockers,
               "audit": "not run: the independent audit reads this run's raw outputs"}
    (run / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": summary["passed"], "steps": passed, "blockers": blockers}))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
