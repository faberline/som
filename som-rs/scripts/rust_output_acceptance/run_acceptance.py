#!/usr/bin/env python3
"""Run every check of the Rust output acceptance plan on one recorded candidate (#50).

The candidate comes from prepare_candidate.py and serves on a QA port. Every step
runs in order as its own process, with the vendored crate set as CARGO_HOME and
one shared new target directory. Each step leaves a receipt with its argv, cwd,
environment changes, times, exit code, log hashes and output hashes, and a
judgment read from its own report. A step that fails, cannot be judged or does
not run blocks acceptance. Nothing is skipped and no step has a sample.

The candidate identity, the default compiler on 8765 (read-only) and the tool
files are recorded before and after. The tools must be committed and the
candidate source must equal the current tree.

usage: run_acceptance.py --candidate DIR --vendor DIR --out NEW_DIR
       run_acceptance.py cluster-gates <check_rust_products.py arguments without --pg>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[3]
SOM_RS = REPO / "som-rs"
HERE = pathlib.Path(__file__).resolve().parent
QA_DIR = SOM_RS / "som-targets/qa"
BEHAVIOR = SOM_RS / "behavior"
TESTS = SOM_RS / "tests/rust-output-v1"
CONTRACT = SOM_RS / "contracts/rust-output-v1"
ACCEPTANCE = SOM_RS / "scripts/compiler_acceptance"
VENV_PYTHON = SOM_RS / "runtime/acceptance-m3-venv/bin/python"
LOCAL = REPO / ".local/rust-compiler-goal/acceptance"
PUBLIC_RUN = LOCAL / "public-products-003b"
LEGACY_RUN = LOCAL / "legacy544/run-005"
ASSET_INDEX = LOCAL / "per-version-asset-index-003.json"
DEFAULT = "http://127.0.0.1:8765"
FORBIDDEN_PORTS = {8765, 18765}
TOOL_ROOTS = ["som-rs/scripts/rust_output_acceptance", "som-rs/scripts/compiler_acceptance", "som-rs/som-targets/qa",
              "som-rs/behavior", "som-rs/tests/rust-output-v1", "som-rs/contracts/rust-output-v1"]
PYPI = ["fastapi", "uvicorn", "sqlalchemy", "psycopg", "psycopg-binary", "alembic", "pydantic", "jsonschema", "httpx"]


def sha_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha(root: pathlib.Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(f"{path.relative_to(root)}\0{sha_file(path)}\n".encode())
    return digest.hexdigest()


def output_sha(path: pathlib.Path) -> str | None:
    if path.is_file():
        return sha_file(path)
    if path.is_dir():
        return "tree:" + tree_sha(path)
    return None


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def get(url: str) -> tuple[int | None, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except OSError as error:
        return None, str(error).encode()


def listener(port: int) -> list[str]:
    done = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return sorted(done.stdout.split())


def process_command(pid: int) -> str:
    return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------- identity

def candidate_identity(candidate: pathlib.Path) -> dict:
    build, runtime, source = (load(candidate / name) for name in ("build.json", "runtime.json", "source.json"))
    port, pid = runtime["port"], runtime["pid"]
    status, raw = get(f"http://127.0.0.1:{port}/healthz")
    health = json.loads(raw) if status == 200 else None
    binary = candidate / "som"
    current = {relative: sha_file(REPO / relative) if (REPO / relative).is_file() else None
               for relative in source["files"]}
    problems = []
    checks = {
        "build_complete": build["complete"] is True,
        "build_source_unchanged": build["source_unchanged"] is True,
        "binary_matches_build": sha_file(binary) == build["binary_sha256"] == runtime["binary_sha256"],
        "source_matches_build": sha_file(candidate / "source.json") == build["source_sha256"] == runtime["source_sha256"],
        "source_archive_matches": sha_file(candidate / "source.tar") == source["archive_sha256"],
        "source_equals_current_tree": current == source["files"],
        "assets_match_build": sha_file(candidate / "assets/manifest.json") == build["asset_manifest_sha256"],
        "process_is_the_binary": process_command(pid).startswith(str(binary.resolve()) + " serve"),
        "process_listens_on_port": listener(port) == [str(pid)],
        "health_matches_runtime": health == runtime["health"],
        "qa_port": port not in FORBIDDEN_PORTS,
    }
    problems = [name for name, ok in checks.items() if not ok]
    return {"candidate": str(candidate), "port": port, "pid": pid, "checks": checks, "problems": problems,
            "build_json_sha256": sha_file(candidate / "build.json"),
            "runtime_json_sha256": sha_file(candidate / "runtime.json"),
            "source_json_sha256": sha_file(candidate / "source.json"),
            "source_tar_sha256": sha_file(candidate / "source.tar"),
            "binary_sha256": sha_file(binary), "binary_bytes": binary.stat().st_size,
            "asset_manifest_sha256": sha_file(candidate / "assets/manifest.json"),
            "assets_tree_sha256": tree_sha(candidate / "assets"),
            "commit": source["commit"], "health": health}


def default_state() -> dict:
    status, raw = get(DEFAULT + "/healthz")
    return {"listeners": listener(8765), "healthz_status": status,
            "healthz_sha256": hashlib.sha256(raw).hexdigest() if status == 200 else None}


def tools_state() -> dict:
    files = sorted(f for f in git("ls-files", "-z", "--", *TOOL_ROOTS).split("\0") if f)
    return {"head": git("rev-parse", "HEAD").strip(),
            "porcelain": git("status", "--porcelain", "--untracked-files=all", "--", *TOOL_ROOTS),
            "files": {f: sha_file(REPO / f) for f in files}}


def venv_packages() -> dict:
    code = ("import importlib.metadata as m, json, sys; "
            "print(json.dumps({'python': sys.version.split()[0], 'packages': "
            "{d.metadata['Name'].lower(): d.version for d in m.distributions()}}))")
    data = json.loads(subprocess.run([str(VENV_PYTHON), "-c", code], capture_output=True, text=True, check=True).stdout)
    return {"python": data["python"], "pypi": {name: data["packages"].get(name) for name in PYPI}}


# ---------------------------------------------------------------- judgments

def judge_contract(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    stdout = json.loads((run / step["stdout"]).read_text())
    return step["exit_code"] == 0 and not stdout["failed"] and stdout["passed"] > 0, stdout


def judge_controls(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    report = load(run / "python-controls.json")
    total = len(load(CONTRACT / "negative-cases.json")["cases"])
    summary = {"passed": report["passed"], "total": len(report["results"]), "frozen_cases": total,
               "failed": report["failed"]}
    return step["exit_code"] == 0 and not report["failed"] and report["passed"] == total == len(report["results"]), summary


def judge_validate(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    report = load(run / "validate-cases.json")
    total = len(load(TESTS / "cases/index.json")["cases"])
    summary = {"passed": report["passed"], "total": len(report["cases"]), "frozen_cases": total, "failed": report["failed"]}
    return step["exit_code"] == 0 and not report["failed"] and report["passed"] == total, summary


def judge_targets(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    summary = load(run / "targets.json")["summary"]
    negatives = len(load(CONTRACT / "negative-cases.json")["cases"])
    examples = len(load(CONTRACT / "target-examples.json")["examples"])
    ok = (step["exit_code"] == 0 and not summary["failed"] and not summary["inventory_problems"]
          and summary["negative_cases"]["passed"] == summary["negative_cases"]["total"] == negatives
          and summary["target_examples"]["passed"] == summary["target_examples"]["total"] == examples
          and summary["source_forms"]["passed"] == summary["source_forms"]["total"] > 0
          and summary["compiler_v1_precedence"]["passed"] == summary["compiler_v1_precedence"]["total"])
    return ok, summary


def judge_cli(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    report = load(run / "cli.json")
    summary = {key: report[key] for key in ("total", "passed", "refusals", "refusals_exit_2", "failed")}
    return (step["exit_code"] == 0 and not report["failed"] and report["passed"] == report["total"]
            and report["refusals"] == report["refusals_exit_2"] > 0), summary


def judge_replay(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    report = load(run / "python-replay.json")
    summary = {"public": {k: report["public"][k] for k in ("required", "total", "passed", "failed")},
               "legacy": {k: report["legacy"][k] for k in ("required", "total", "passed", "families",
                                                           "compile_prompts", "compile_prompts_passed", "failed")},
               "exchanges": report["exchanges"], "inputs": report["inputs"]}
    return step["exit_code"] == 0 and report["passed"] is True, summary


def judge_asset_index(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    generated = run / "asset-index.json"
    provenance = load(run / "asset-index-provenance.json")
    equal = generated.is_file() and load(generated) == load(ASSET_INDEX)
    summary = {"equal_to": str(ASSET_INDEX.relative_to(REPO)), "accepted_sha256": sha_file(ASSET_INDEX),
               "generated_sha256": sha_file(generated), "equal": equal,
               "package_manifest_matches_independent_sources": provenance["package_manifest_matches_independent_sources"],
               "freeze_chain": provenance["receipt_chain"]["status"]}
    return (step["exit_code"] == 0 and equal and summary["package_manifest_matches_independent_sources"] is True
            and summary["freeze_chain"] == "passed"), summary


def judge_runtime(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    result = load(run / "runtime-acceptance/result.json")
    checks = result["checks"]
    summary = {"status": result["status"], "checks": len(checks), "passed": sum(r.get("passed") is True for r in checks),
               "unrun": result.get("unrun", [])}
    return (step["exit_code"] == 0 and result["passed"] is True and not summary["unrun"]
            and summary["passed"] == summary["checks"] > 0), summary


def judge_behavior(mode: str):
    def judge(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
        summary = load(run / "behavior" / mode / f"report-{mode}.json")["summary"]
        ok = (step["exit_code"] == 0 and summary["complete"] is True and not summary["failed"]
              and summary["passed"] == summary["total"] > 0 and not summary.get("strict_failed")
              and not summary.get("categories_without_a_caught_fault"))
        return ok, summary
    return judge


def judge_products(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    report = load(run / "rust-products.json")
    summary = report["summary"]
    total = summary["acceptance_cases"]["frozen_acceptance_total"]
    gates = {}
    for case in report["cases"]:
        for name, gate in case.get("product_gates", {}).get("gates", {}).items():
            gates.setdefault(name, [0, 0])
            gates[name][0] += gate["passed"] is True
            gates[name][1] += 1
    summary = {**summary, "gate_counts": {name: {"passed": p, "total": t} for name, (p, t) in sorted(gates.items())},
               "parity_report_sha256_expected": sha_file(run / "behavior/parity/report-parity.json")}
    ok = (step["exit_code"] == 0 and not summary["failed"] and not summary["blocked"]
          and summary["acceptance_cases"]["checked"] == summary["acceptance_cases"]["total"] == total
          and summary["bundle_checks"]["passed"] == summary["bundle_checks"]["total"] == total
          and summary["assemble_refusals"]["passed"] == summary["assemble_refusals"]["total"]
          == len(load(CONTRACT / "acceptance-plan.json")["assemble_refusals"]["recipes"])
          and summary["product_gates"]["passed"] == summary["product_gates"]["total"] == total
          and summary["parity"].get("status") == "passed"
          and summary["parity"].get("report_sha256") == summary["parity_report_sha256_expected"]
          and all(p == t == total for p, t in gates.values()))
    return ok, summary


def judge_perf(step: dict, run: pathlib.Path) -> tuple[bool, dict]:
    report = load(run / "perf/perf.json")
    return step["exit_code"] == 0 and report["complete"] is True, {"complete": report["complete"],
                                                                     "missing": report["missing"],
                                                                     "summary": report["summary"]}


# ---------------------------------------------------------------- steps

def steps(candidate: pathlib.Path, run: pathlib.Path, qa: str) -> list[dict]:
    py = str(VENV_PYTHON)
    target = str(run / "target")
    behavior = [{"name": f"behavior-{mode}", "cwd": BEHAVIOR,
                 "argv": [py, "check_behavior.py", mode, "--qa", qa, "--out", str(run / "behavior" / mode),
                          "--target-dir", target, "--python", py],
                 "outputs": [f"behavior/{mode}"], "judge": judge_behavior(mode)}
                for mode in ("python", "faults", "parity")]
    return [
        {"name": "contract", "cwd": REPO, "argv": [py, str(TESTS / "check_contract.py"), "--host"],
         "outputs": [], "judge": judge_contract},
        {"name": "python-controls", "cwd": REPO,
         "argv": [py, str(TESTS / "check_python_controls.py"), "--base-url", qa, "--out", str(run / "python-controls.json")],
         "outputs": ["python-controls.json"], "judge": judge_controls},
        {"name": "validate-cases", "cwd": REPO,
         "argv": [py, str(TESTS / "validate_cases.py"), "--base-url", qa, "--out", str(run / "validate-cases.json")],
         "outputs": ["validate-cases.json"], "judge": judge_validate},
        {"name": "targets", "cwd": QA_DIR,
         "argv": [py, "check_targets.py", "--qa", qa, "--reference", DEFAULT, "--out", str(run / "targets.json")],
         "outputs": ["targets.json"], "judge": judge_targets},
        {"name": "cli", "cwd": REPO,
         "argv": [py, str(HERE / "check_cli.py"), "--binary", str(candidate / "som"), "--base-url", qa,
                  "--out", str(run / "cli.json")],
         "outputs": ["cli.json"], "judge": judge_cli},
        {"name": "python-replay", "cwd": REPO,
         "argv": [py, str(HERE / "python_replay.py"), "--base-url", qa, "--public-run", str(PUBLIC_RUN),
                  "--legacy-run", str(LEGACY_RUN), "--out", str(run / "python-replay.json")],
         "outputs": ["python-replay.json"], "judge": judge_replay},
        {"name": "asset-index", "cwd": REPO,
         "argv": [py, str(ACCEPTANCE / "asset_index_generator.py"), "--package-assets", str(candidate / "assets"),
                  "--formatter", str(candidate / "assets/ruff"), "--output", str(run / "asset-index.json"),
                  "--provenance", str(run / "asset-index-provenance.json")],
         "outputs": ["asset-index.json", "asset-index-provenance.json"], "judge": judge_asset_index},
        {"name": "runtime-acceptance", "cwd": REPO,
         "argv": [py, str(ACCEPTANCE / "runtime_acceptance.py"), "--binary", str(candidate / "som"),
                  "--assets", str(candidate / "assets"), "--asset-index", str(run / "asset-index.json"),
                  "--out", str(run / "runtime-acceptance")],
         "outputs": ["runtime-acceptance"], "judge": judge_runtime},
        *behavior,
        {"name": "rust-products", "cwd": QA_DIR,
         "argv": [py, str(HERE / "run_acceptance.py"), "cluster-gates", "--qa", qa, "--work", str(run / "products"),
                  "--target-dir", target, "--out", str(run / "rust-products.json"),
                  "--parity", str(run / "behavior/parity/report-parity.json")],
         "outputs": ["rust-products.json"], "judge": judge_products},
        {"name": "perf", "cwd": REPO,
         "argv": [py, str(HERE / "perf_observe.py"), "--base-url", qa, "--products", str(run / "products"),
                  "--target-dir", target, "--candidate", str(candidate), "--out", str(run / "perf")],
         "outputs": ["perf"], "judge": judge_perf},
    ]


# Each acceptance-plan.json check and the steps whose judgments prove it.
PLAN = {
    "python_unchanged": [["python-replay"], ["python-replay"], ["python-replay", "targets"], ["python-controls"],
                         ["validate-cases"]],
    "target_selection_checks": [["targets"], ["targets"], ["targets"], ["cli"]],
    "scope_checks": [["targets"], ["targets"], ["targets", "python-controls"]],
    "bundle_checks": [["rust-products"]] * 4,
    "assemble_refusals": [["rust-products"]] * 9,
    "product_gates": [["rust-products"]] * 6 + [["behavior-parity", "rust-products"], ["rust-products"]],
}


def plan_coverage(results: dict[str, bool]) -> tuple[list[dict], list[str]]:
    plan = load(CONTRACT / "acceptance-plan.json")
    texts = {"python_unchanged": plan["python_unchanged"]["checks"],
             "target_selection_checks": plan["target_selection_checks"],
             "scope_checks": plan["scope_checks"], "bundle_checks": plan["bundle_checks"],
             "assemble_refusals": [r["id"] for r in plan["assemble_refusals"]["recipes"]],
             "product_gates": plan["product_gates"]["each_acceptance_case"]}
    rows, problems = [], []
    for section, items in texts.items():
        mapped = PLAN[section]
        if len(mapped) != len(items):
            problems.append(f"{section}: {len(items)} plan checks, {len(mapped)} mapped")
        for index, text in enumerate(items):
            names = mapped[index] if index < len(mapped) else []
            state = ("unrun" if not names or any(n not in results for n in names)
                     else "passed" if all(results[n] for n in names) else "failed")
            rows.append({"section": section, "index": index, "check": text, "steps": names, "state": state})
    return rows, problems


def run_step(index: int, step: dict, run: pathlib.Path, env_delta: dict) -> dict:
    name = f"{index:02d}-{step['name']}"
    stdout, stderr = run / "logs" / f"{name}.stdout", run / "logs" / f"{name}.stderr"
    receipt = {"step": step["name"], "argv": step["argv"], "cwd": str(step["cwd"]), "env_delta": env_delta,
               "started_at": now(), "stdout": str(stdout.relative_to(run)), "stderr": str(stderr.relative_to(run))}
    started = time.monotonic()
    with stdout.open("wb") as out, stderr.open("wb") as err:
        code = subprocess.run(step["argv"], cwd=step["cwd"], env={**os.environ, **env_delta},
                              stdin=subprocess.DEVNULL, stdout=out, stderr=err).returncode
    receipt.update(ended_at=now(), seconds=time.monotonic() - started, exit_code=code,
                   stdout_sha256=sha_file(stdout), stderr_sha256=sha_file(stderr),
                   outputs={o: output_sha(run / o) for o in step["outputs"]})
    try:
        receipt["passed"], receipt["summary"] = step["judge"](receipt, run)
    except Exception as error:  # noqa: BLE001
        receipt["passed"], receipt["summary"] = False, {"judge_error": f"{type(error).__name__}: {error}"}
    (run / "receipts" / f"{name}.json").write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(f"{name}: {'passed' if receipt['passed'] else 'FAILED'} ({receipt['seconds']:.0f}s)", flush=True)
    return receipt


def cluster_gates(argv: list[str]) -> int:
    sys.path.insert(0, str(BEHAVIOR))
    import hosts  # type: ignore[import-not-found]
    with tempfile.TemporaryDirectory(prefix="som-a50-") as scratch:
        with hosts.cluster(pathlib.Path(scratch), hosts.Ports().take()) as pg:
            return subprocess.run([sys.executable, str(QA_DIR / "check_rust_products.py"), "--pg", pg, *argv],
                                  cwd=QA_DIR).returncode


def main() -> int:
    if sys.argv[1:2] == ["cluster-gates"]:
        return cluster_gates(sys.argv[2:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=pathlib.Path, required=True)
    parser.add_argument("--vendor", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    candidate, vendor, run = args.candidate.resolve(), args.vendor.resolve(), args.out.resolve()
    if run.exists():
        raise SystemExit(f"preserve existing output: {run}")
    if not run.is_relative_to(LOCAL):
        raise SystemExit(f"write under {LOCAL}")
    tools = tools_state()
    if tools["porcelain"]:
        raise SystemExit("commit the acceptance tools first:\n" + tools["porcelain"])
    before = candidate_identity(candidate)
    if before["problems"]:
        raise SystemExit(f"candidate identity problems: {before['problems']}")
    vendor_receipt = load(vendor / "vendor.json")
    vendor_tree = tree_sha(vendor / "vendor")
    if vendor_tree != vendor_receipt["vendor_tree_sha256"]:
        raise SystemExit("vendored crates differ from vendor.json")
    run.mkdir(parents=True)
    (run / "logs").mkdir()
    (run / "receipts").mkdir()
    qa = f"http://127.0.0.1:{before['port']}"
    env_delta = {"CARGO_HOME": str(vendor / "cargo-home"), "CARGO_NET_OFFLINE": "true", "PYTHONDONTWRITEBYTECODE": "1"}
    started_at, default_before = now(), default_state()
    header = {"started_at": started_at, "candidate_before": before, "default_before": default_before,
              "tools": tools, "vendor": {"vendor_json_sha256": sha_file(vendor / "vendor.json"),
                                         "vendor_tree_sha256": vendor_tree, "crates": vendor_receipt["crates"],
                                         "stubs": len(vendor_receipt["stubs"]),
                                         "cargo_config_sha256": sha_file(vendor / "cargo-home/config.toml")},
              "venv": venv_packages(), "env_delta": env_delta}
    (run / "header.json").write_text(json.dumps(header, indent=1, sort_keys=True) + "\n")

    receipts = [run_step(index, step, run, env_delta) for index, step in enumerate(steps(candidate, run, qa), 1)]
    after = candidate_identity(candidate)
    default_after = default_state()
    results = {r["step"]: r["passed"] for r in receipts}
    coverage, coverage_problems = plan_coverage(results)
    blockers = ([f"step {r['step']} failed" for r in receipts if not r["passed"]]
                + [f"plan check {c['section']}[{c['index']}] {c['state']}" for c in coverage if c["state"] != "passed"]
                + coverage_problems
                + [f"candidate after: {p}" for p in after["problems"]]
                + (["candidate identity changed during the run"]
                   if {k: v for k, v in after.items() if k != "checks"} != {k: v for k, v in before.items() if k != "checks"}
                   else [])
                + (["default on 8765 changed during the run"] if default_after != default_before else [])
                + (["tools changed during the run"] if tools_state() != tools else []))
    summary = {
        "version": "som.rust-output-acceptance-run.v1",
        "issue": 50,
        "started_at": started_at, "ended_at": now(),
        "status": "passed" if not blockers else "failed",
        "blockers": blockers,
        "header_sha256": sha_file(run / "header.json"),
        "candidate_after": after, "default_after": default_after,
        "steps": [{"step": r["step"], "passed": r["passed"], "exit_code": r["exit_code"], "seconds": r["seconds"],
                   "receipt_sha256": sha_file(run / "receipts" / f"{i:02d}-{r['step']}.json")}
                  for i, r in enumerate(receipts, 1)],
        "plan_checks": coverage,
        "plan_sha256": sha_file(CONTRACT / "acceptance-plan.json"),
    }
    (run / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"status": summary["status"], "blockers": blockers}))
    return 0 if not blockers else 1


if __name__ == "__main__":
    sys.exit(main())
