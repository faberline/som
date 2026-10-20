#!/usr/bin/env python3
"""Check that `som compile --target` forwards the target unchanged and exits 2 on a refusal.

This is the last target_selection_checks row of acceptance-plan.json. For each
input the script runs the candidate CLI against a QA compiler and posts the
same request body the CLI sends: {"format":"som.cnl.v1","source":...,"target":...}
in compact form. The CLI result must equal the HTTP result:

- a 200 answer prints the same bundle and exits 0;
- any other answer prints the same error document on stderr and exits 2.

Inputs: every coverage case CNL without a target, with target python and with
target rust (python must print the same bytes as no target); every CNL negative
case whose target is a string; and a few unknown target strings.

With --refusals phase2 (acceptance-plan.json phase2.refusals), an RS negative case
expects UNSUPPORTED_TARGET only where tests/rust-output-v1/rust_profile_phase2.py
refuses its compiled prompt (none since amendment 004), the frozen compiler-v1 code
where compiler-v1 refuses the prompt without target (RS12), and a bundle otherwise.

usage: check_cli.py --binary SOM --base-url http://127.0.0.1:PORT --out NEW.json [--refusals phase2]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[3]
CONTRACT = REPO / "som-rs/contracts/rust-output-v1"
CASES = REPO / "som-rs/tests/rust-output-v1/cases"
sys.path.insert(0, str(REPO / "som-rs/tests/rust-output-v1"))
import rust_profile_phase2  # type: ignore[import-not-found]  # noqa: E402

FORBIDDEN_PORTS = {8765, 18765}
UNKNOWN_TARGETS = ["RUST", "", "go", " rust", "python3"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def post(base_url: str, data: bytes) -> tuple[int, bytes]:
    request = urllib.request.Request(base_url + "/v1/compile", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def cli_body(source: str, target: str | None) -> bytes:
    """The bytes the CLI sends: serde_json compact form, keys in this order."""
    body = {"format": "som.cnl.v1", "source": source}
    if target is not None:
        body["target"] = target
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()


def check(binary: str, base_url: str, scratch: pathlib.Path, label: str, source: str,
          target: str | None, expect: str | None = None) -> dict:
    path = scratch / f"{sha(label.encode())[:16]}.cnl"
    path.write_text(source, encoding="utf-8")
    argv = [binary, "compile", str(path), "--url", base_url] + ([] if target is None else ["--target", target])
    done = subprocess.run(argv, capture_output=True, timeout=120)
    status, raw = post(base_url, cli_body(source, target))
    expected = json.loads(raw)
    row = {"input": label, "target": target, "http_status": status, "exit_code": done.returncode,
           "stdout_sha256": sha(done.stdout), "stderr_sha256": sha(done.stderr)}
    problems = []
    try:
        if status == 200:
            if done.returncode != 0:
                problems.append("CLI did not exit 0 on a 200 answer")
            elif json.loads(done.stdout) != expected.get("bundle"):
                problems.append("CLI bundle differs from the HTTP bundle")
            row["bundle_version"] = expected.get("bundle", {}).get("version")
        else:
            row["code"] = expected.get("code")
            if done.returncode != 2:
                problems.append("CLI did not exit 2 on a refusal")
            elif json.loads(done.stderr) != expected:
                problems.append("CLI error document differs from the HTTP answer")
    except ValueError as error:
        problems.append(f"CLI output is not JSON: {error}")
    if expect is not None and row.get("code") != expect:
        problems.append(f"expected code {expect}, got {row.get('code')}")
    row.update(problems=problems, passed=not problems)
    row["_stdout"] = done.stdout
    return row


def phase2_code(base_url: str, body: dict, case: dict) -> str | None:
    """UNSUPPORTED_TARGET where the phase 2 profile refuses the compiled prompt, the frozen code where
    compiler-v1 refuses the prompt for every target, None for a bundle."""
    plain = {k: v for k, v in body.items() if k != "target"}
    status, raw = post(base_url, json.dumps(plain).encode())
    if status != 200:
        if case["python_control_expected"].startswith("the same diagnostic") \
                and json.loads(raw).get("code") == case["expected"]["code"]:
            return case["expected"]["code"]
        return f"compile without target gave {status}"
    return "UNSUPPORTED_TARGET" if rust_profile_phase2.check(json.loads(raw)["bundle"]["prompt"]) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--refusals", choices=["phase1", "phase2"], default="phase1")
    args = parser.parse_args()
    split = urllib.parse.urlsplit(args.base_url)
    if split.hostname not in {"127.0.0.1", "localhost", "::1"} or split.port in FORBIDDEN_PORTS:
        raise SystemExit("use a loopback QA port, not 8765 or 18765")
    if args.out.exists():
        raise SystemExit(f"preserve existing output: {args.out}")
    base_url = args.base_url.rstrip("/")
    rows = []
    with tempfile.TemporaryDirectory(prefix="som-cli-") as scratch_name:
        scratch = pathlib.Path(scratch_name)
        for case in json.loads((CASES / "index.json").read_text())["cases"]:
            source = (CASES / case["cnl"]).read_text(encoding="utf-8")
            plain = check(args.binary, base_url, scratch, case["id"], source, None)
            python = check(args.binary, base_url, scratch, case["id"], source, "python")
            if python["passed"] and python["_stdout"] != plain["_stdout"]:
                python["problems"].append("target python prints other bytes than no target")
                python["passed"] = False
            rust = check(args.binary, base_url, scratch, case["id"], source, "rust")
            rows += [plain, python, rust]
        for case in json.loads((CONTRACT / "negative-cases.json").read_text())["cases"]:
            body = json.loads(case["body"])
            if case["form"] != "cnl" or not isinstance(body.get("target"), str) or not isinstance(body.get("source"), str):
                continue
            expect = case["expected"]["code"]
            if args.refusals == "phase2" and case["id"].startswith("RS"):
                expect = phase2_code(base_url, body, case)
            row = check(args.binary, base_url, scratch, case["id"], body["source"], body["target"], expect)
            if expect is None and row["http_status"] != 200:
                row["problems"].append(f"expected a bundle, got {row['http_status']}")
                row["passed"] = False
            rows.append(row)
        first = json.loads((CASES / "index.json").read_text())["cases"][0]
        source = (CASES / first["cnl"]).read_text(encoding="utf-8")
        for target in UNKNOWN_TARGETS:
            rows.append(check(args.binary, base_url, scratch, f"{first['id']} unknown {target!r}", source, target))
    for row in rows:
        row.pop("_stdout")
    refusals = [r for r in rows if r["http_status"] != 200]
    report = {"version": "som.rust-output-cli-target.v1", "base_url": base_url, "refusals_profile": args.refusals,
              "binary_sha256": sha(pathlib.Path(args.binary).read_bytes()),
              "inputs": {"negative_cases_sha256": sha((CONTRACT / "negative-cases.json").read_bytes()),
                         "coverage_index_sha256": sha((CASES / "index.json").read_bytes())},
              "total": len(rows), "passed": sum(r["passed"] for r in rows),
              "refusals": len(refusals), "refusals_exit_2": sum(r["exit_code"] == 2 for r in refusals),
              "failed": [f"{r['input']} target={r['target']!r}" for r in rows if not r["passed"]], "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({key: report[key] for key in ("total", "passed", "refusals", "refusals_exit_2", "failed")}))
    return 0 if not report["failed"] and report["refusals"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
