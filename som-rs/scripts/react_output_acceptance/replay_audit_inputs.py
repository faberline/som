#!/usr/bin/env python3
"""Hydrate recorded inputs and summarize a GitHub audit replay. No product is run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SOURCE_HEAD = "40cffc13b3bff35e3ea5804b9f43a49cdef7b3c0"
CANDIDATE_SHA = "59dab78a0f401a86e028c186285a888cfdf304b2d077e167d46b20d1131a3308"
BASELINE_SHA = "2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c"
RECONSTRUCTION_SHA = "e0987d9215d148309c087e43dfc7b46bf7c8228c825f8ae70825415938775517"
NAME_ROOT = ".local/react-output/review134-names-001"
STEPS = ("contract", "react-target", "react-python-controls", "python-baseline", "rust-baseline",
         "rust-python-controls", "rt-diagnostics", "ts-target", "ts-baseline", "ts-python-controls", "bundles", "products")
ATTESTED = {"contract/sealed", "issue_gates/phase1-contract", "issue_gates/phase1-target-amendments", "issue_gates/phase1-acceptance"}


def need(condition, message="retained audit replay refused"):
    if not condition:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def physical(path: Path) -> Path:
    path = Path(path)
    need(path.is_absolute() and ".." not in path.parts)
    for part in (*reversed(path.parents), path):
        need(not part.is_symlink(), "symlink path refused")
        if part != path and part.exists():
            need(part.is_dir())
    return path


def read(path: Path) -> bytes:
    physical(path)
    need(path.is_file() and path.stat().st_size <= 128 * 1024 * 1024, "raw file missing or too large")
    return path.read_bytes()


def parse(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate JSON member")
            result[key] = value
        return result
    def bad_constant(_value):
        raise ValueError("non-finite JSON number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)


def load(path: Path):
    return parse(read(path))


def relative(value: str) -> Path:
    need(type(value) is str and value and "\\" not in value and not any(ord(char) < 32 for char in value))
    path = Path(value)
    need(not path.is_absolute() and ".." not in path.parts and path.as_posix() == value)
    return path


def new_output(out: Path, raw_root: Path):
    physical(out)
    need(not out.exists() and not out.is_relative_to(raw_root), "preserve existing or original output")


def write_new(path: Path, raw: bytes):
    physical(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def write_json(path: Path, value):
    write_new(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def original(repo: Path, raw_root: Path) -> dict:
    physical(repo)
    physical(raw_root)
    need(raw_root == repo / ".local/m6-hosted/acceptance", "unexpected original raw directory")
    summary, run = load(raw_root / "summary.json"), load(raw_root / "run/run.json")
    need(summary.get("version") == "som.m6-hosted-acceptance.v1" and summary.get("failed_stage") == "audit"
         and summary.get("passed") is False and summary.get("status") == "failed")
    need(type(summary.get("acceptance_exit_code")) is int and summary["acceptance_exit_code"] == 0
         and type(summary.get("audit_exit_code")) is int and summary["audit_exit_code"] == 1)
    need(summary.get("head_before") == summary.get("head_after") == SOURCE_HEAD
         and summary.get("candidate_binary_sha256") == CANDIDATE_SHA
         and summary.get("baseline_binary_sha256") == BASELINE_SHA
         and summary.get("step_count") == 12 and summary.get("product_suites") == "recorded"
         and summary.get("candidate_assets_match_bootstrap") is True)
    cleanup = summary["cleanup"]
    need(cleanup.get("baseline_stopped") is True and cleanup["candidate"].get("stopped") is True
         and cleanup["candidate"].get("ownership_verified") is True
         and cleanup["candidate"].get("binary_sha256") == CANDIDATE_SHA)
    need(run.get("version") == "som.react-output-acceptance-run.v1" and run.get("passed") is True
         and run.get("identity_passed") is True and run.get("status") == "passed"
         and run.get("failed") == run.get("problems") == run.get("not_run") == [])
    need([step.get("step") for step in run["steps"]] == list(STEPS))
    need(all(type(step.get("index")) is int and step["index"] == index
             and step.get("judgment") == "passed" and step.get("passed") is True
             and type(step.get("exit_code")) is int and step["exit_code"] == 0
             for index, step in enumerate(run["steps"], 1)))
    identity_path = raw_root / "run/identity.json"
    need(run.get("identity") == str(identity_path) and run.get("candidate") == str(raw_root / "candidate")
         and sha(read(identity_path)) == run.get("identity_sha256"))
    identity = load(identity_path)
    need(identity["git_head"].get("before") == identity["git_head"].get("after") == SOURCE_HEAD)
    candidate = identity["candidate"]
    need(candidate.get("dir") == str(raw_root / "candidate")
         and candidate.get("binary_sha256") == candidate.get("binary_sha256_after") == CANDIDATE_SHA
         and sha(read(raw_root / "candidate/som")) == CANDIDATE_SHA)
    for name in ("build", "runtime"):
        path = raw_root / f"candidate/{name}.json"
        need(sha(read(path)) == candidate.get(name + "_sha256") and load(path) == candidate.get(name)
             and candidate[name].get("binary_sha256") == CANDIDATE_SHA)
    build, runtime = candidate["build"], candidate["runtime"]
    need(build.get("complete") is True and build.get("source_unchanged") is True and runtime.get("accepted") is False)
    need(all(build[name].get("head") == SOURCE_HEAD and build[name].get("porcelain") == ""
             for name in ("tree_before", "tree_after")))
    source = raw_root / "candidate/source.json"
    need(build.get("source_sha256") == runtime.get("source_sha256") == sha(read(source))
         and load(source).get("commit") == SOURCE_HEAD)
    receipt = raw_root / "reconstructed-inputs/receipt.json"
    need(summary.get("reconstructed_inputs_receipt_sha256") == sha(read(receipt)) == RECONSTRUCTION_SHA)
    return {"source_head": SOURCE_HEAD, "candidate_binary_sha256": CANDIDATE_SHA,
            "baseline_binary_sha256": BASELINE_SHA, "original_summary_sha256": sha(read(raw_root / "summary.json")),
            "raw_run_sha256": sha(read(raw_root / "run/run.json")), "identity_sha256": run["identity_sha256"],
            "candidate_build_sha256": candidate["build_sha256"], "candidate_runtime_sha256": candidate["runtime_sha256"],
            "candidate_source_sha256": sha(read(source)), "reconstructed_inputs_receipt_sha256": RECONSTRUCTION_SHA}


def prepare(repo: Path, raw_root: Path, out: Path) -> dict:
    new_output(out, raw_root)
    binding = original(repo, raw_root)
    reconstructed = raw_root / "reconstructed-inputs"
    receipt = load(reconstructed / "receipt.json")
    need(receipt.get("version") == "som.m6-hosted-reconstruction.v1" and receipt.get("status") == "passed"
         and receipt.get("passed") is True and receipt.get("name_prompt_count") == 9
         and receipt["baseline"].get("binary_sha256") == BASELINE_SHA)
    toolchain = load(repo / "som-rs/contracts/react-output-v1/toolchain.json")
    evidence_path = relative(toolchain["reference_verification"]["name_cases_evidence"])
    need(evidence_path.as_posix().startswith("som-rs/evidence/react-output-v1/name-cases-") and evidence_path.suffix == ".json")
    evidence = load(repo / evidence_path)
    need(evidence["run"].get("work_dir") == NAME_ROOT)
    cases = load(repo / "som-rs/contracts/react-output-v1/target-examples.json")["name_cases"]
    expected = {row["id"]: row for row in evidence["cases"]}
    recorded = {row["id"]: row for row in receipt["name_prompts"]}
    need(len(cases) == len(expected) == len(recorded) == len(evidence["cases"]) == len(receipt["name_prompts"]) == 9)
    need({case["id"] for case in cases} == set(expected) == set(recorded))
    plan = []
    for case in cases:
        name, row = case["id"], recorded[case["id"]]
        need(type(name) is str and re.fullmatch(r"RN[0-9]{2}_[A-Z0-9_]+", name))
        destination = repo / NAME_ROOT / name / "prompt.json"
        physical(destination)
        need(row.get("path") == destination.relative_to(repo).as_posix())
        request_path, response_path = (f"name-cases/{name}/compile.{kind}.json" for kind in ("request", "response"))
        need(row.get("request_file") == request_path and row.get("response_file") == response_path)
        request, response = read(reconstructed / request_path), read(reconstructed / response_path)
        need(sha(request) == row.get("request_sha256") and sha(response) == row.get("response_sha256")
             and request == case["python_control_body"].encode())
        body = parse(response)
        need(body.get("status") == "ok" and type(body["bundle"].get("prompt")) is dict)
        prompt = body["bundle"]["prompt"]
        control = parse(request)
        if control.get("format") == "som.prompt-json.v1":
            need(prompt == control["prompt"])
        raw = (json.dumps(prompt, indent=2) + "\n").encode()
        need(sha(raw) == row.get("sha256") == expected[name].get("prompt_file_sha256") and len(raw) == row.get("bytes"))
        if destination.exists():
            need(read(destination) == raw, "existing name input differs")
        plan.append((destination, raw))
    # All nine requests, responses, frozen hashes, destinations and output pass first.
    created = []
    for destination, raw in plan:
        if not destination.exists():
            write_new(destination, raw)
            created.append(destination.relative_to(repo).as_posix())
    result = {"version": "som.m6-retained-input-hydration.v1", "status": "passed", "product_suites": "not_run",
              "method": "original_recorded_compile_response", "name_prompt_count": 9, "created": created,
              "files": {path.relative_to(repo).as_posix(): sha(raw) for path, raw in plan}, **binding}
    write_json(out, result)
    return result


def finalize(repo: Path, raw_root: Path, audit_path: Path, auditor_source: Path, out: Path, audit_exit_code: int) -> dict:
    new_output(out, raw_root)
    need(not audit_path.is_relative_to(raw_root), "new audit must be outside original raw evidence")
    binding = original(repo, raw_root)
    audit_raw, source_raw = read(audit_path), read(auditor_source)
    audit, source = parse(audit_raw), parse(source_raw)
    need(source.get("version") == "som.m6-auditor-source.v1"
         and type(source.get("commit")) is str and re.fullmatch(r"[0-9a-f]{40}", source["commit"]))
    files = source.get("files")
    need(type(files) is dict and files and "audit/audit130.py" in files)
    for name, expected in files.items():
        need(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected)
             and sha(read(auditor_source.parent / relative(name))) == expected, "auditor source hash differs")
    rows, totals = audit.get("rows"), audit.get("totals")
    passed = (type(audit_exit_code) is int and audit_exit_code == 0
              and audit.get("version") == "som.react-output-audit-130.v1"
              and audit.get("run") == (raw_root / "run").relative_to(repo).as_posix()
              and type(rows) is list and bool(rows) and type(totals) is dict
              and all(type(totals.get(name)) is int and totals[name] == 0 for name in ("failed", "unrun"))
              and all(type(row) is dict and row.get("status") in ("passed", "attested", "post_acceptance")
                      and (row["status"] != "attested" or row.get("id") in ATTESTED) for row in rows))
    result = {"version": "som.m6-retained-audit-summary.v1", "passed": bool(passed),
              "status": "passed" if passed else "failed", "acceptance_mode": "retained_raw_audit_replay",
              "product_suites": "original_recorded_run", "new_product_suites": "not_run", "audit_exit_code": audit_exit_code,
              "audit_sha256": sha(audit_raw), "audit_totals": totals,
              "auditor_source_sha256": sha(source_raw), "auditor_source": source, **binding}
    write_json(out, result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "finalize"):
        command = commands.add_parser(name)
        for flag in ("repo", "raw-root", "out"):
            command.add_argument("--" + flag, required=True, type=Path)
        if name == "finalize":
            command.add_argument("--audit", required=True, type=Path)
            command.add_argument("--auditor-source", required=True, type=Path)
            command.add_argument("--audit-exit-code", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        need(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
             and os.environ.get("RUNNER_OS") == "macOS", "GitHub hosted macOS is required")
        head = subprocess.run(["git", "-C", str(args.repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        need(head.stdout.strip() == SOURCE_HEAD, "replay checkout differs from original source")
        result = (prepare(args.repo, args.raw_root, args.out) if args.command == "prepare" else
                  finalize(args.repo, args.raw_root, args.audit, args.auditor_source, args.out, args.audit_exit_code))
        print(json.dumps({key: result[key] for key in ("version", "status")}))
        return 0 if result["status"] == "passed" else 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        print("retained audit replay refused", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
