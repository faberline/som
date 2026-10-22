#!/usr/bin/env python3
"""Compare TypeScript compile and assemble responses between a React candidate and the baseline (#128).

react-output-v1 acceptance-plan.json typescript_unchanged, first check: every
in-scope case of typescript-output-v1 case-inventory.json (the 25 non-legacy
cases and the 100 legacy_in_scope jobs), compiled with target: "typescript" on
the candidate and on the baseline, gives responses that differ only in
bundle.assets.backend, bundle.assets.target_contract and
bundle.hashes.payload_sha256. Assembling the candidate bundle on the candidate
and the baseline bundle on the baseline gives the same product files.

The case collectors are those of
som-rs/scripts/typescript_output_acceptance/check_bundles.py. A case that has
only a CNL request also sends the typed JSON request built from the bundle
prompt, as check_bundles.py does.

Writes <out>/report.json (ids and hashes only). Exit 0 when every case passes.

usage: check_typescript_baseline.py --qa URL --baseline URL --out NEW_DIR [--legacy-run DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
TS_ACCEPTANCE = SOM_RS / "scripts" / "typescript_output_acceptance"
if str(TS_ACCEPTANCE) not in sys.path:
    sys.path.insert(0, str(TS_ACCEPTANCE))

import check_bundles  # noqa: E402

ALLOWED = (("assets", "backend"), ("assets", "target_contract"), ("hashes", "payload_sha256"))
EXPECTED_CASES = 125
FORBIDDEN_PORTS = {8765, 18765}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def post(url: str, route: str, body: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url.rstrip("/") + route, data=body.encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()


def without_allowed(reply: Any) -> Any:
    """The reply with the three members the amendment allows to differ blanked."""
    value = json.loads(json.dumps(reply))
    bundle = value.get("bundle") if isinstance(value, dict) else None
    if isinstance(bundle, dict):
        for section, key in ALLOWED:
            if isinstance(bundle.get(section), dict) and key in bundle[section]:
                bundle[section][key] = None
    return value


def files_of(url: str, bundle: Any) -> tuple[int, dict[str, str] | None]:
    status, raw = post(url, "/v1/assemble", compact({"bundle": bundle}))
    if status != 200:
        return status, None
    files = json.loads(raw).get("files")
    if not isinstance(files, dict):
        return status, None
    return status, {path: sha(text.encode()) for path, text in files.items()}


def check_request(qa: str, baseline: str, body: str) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    record: dict[str, Any] = {"body_sha256": sha(body.encode())}
    if json.loads(body).get("target") != "typescript":
        return ["the request has no target typescript"], record
    qa_status, qa_raw = post(qa, "/v1/compile", body)
    base_status, base_raw = post(baseline, "/v1/compile", body)
    record.update(qa_status=qa_status, baseline_status=base_status,
                  qa_sha256=sha(qa_raw), baseline_sha256=sha(base_raw))
    if qa_status != base_status:
        problems.append(f"compile status {qa_status} != baseline {base_status}")
    qa_reply, base_reply = json.loads(qa_raw), json.loads(base_raw)
    if compact(without_allowed(qa_reply)) != compact(without_allowed(base_reply)):
        problems.append("compile replies differ outside the allowed members")
    if qa_status != 200 or base_status != 200:
        problems.append("compile did not return a bundle")
        return problems, record
    differs = [f"{section}.{key}" for section, key in ALLOWED
               if qa_reply["bundle"][section].get(key) != base_reply["bundle"][section].get(key)]
    record["differing_members"] = differs
    qa_assemble, qa_files = files_of(qa, qa_reply["bundle"])
    base_assemble, base_files = files_of(baseline, base_reply["bundle"])
    record.update(qa_assemble_status=qa_assemble, baseline_assemble_status=base_assemble,
                  files_sha256=qa_files)
    if qa_files is None or base_files is None:
        problems.append(f"assemble status {qa_assemble} / baseline {base_assemble}")
    elif qa_files != base_files:
        changed = sorted(set(qa_files.items()) ^ set(base_files.items()))
        problems.append(f"product files differ: {sorted({path for path, _ in changed})}")
    record["bundle"] = qa_reply["bundle"]
    return problems, record


def check_case(qa: str, baseline: str, case: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"id": case["id"], "group": case["group"], "requests": []}
    problems = list(case.get("file_problems") or [])
    if case.get("not_run_error"):
        row.update(status="not_run", problems=[case["not_run_error"]])
        return row
    bundles = []
    bodies = [body for _, body in case["requests"]]
    for index, body in enumerate(bodies):
        found, record = check_request(qa, baseline, body)
        bundle = record.pop("bundle", None)
        if bundle is not None:
            bundles.append(bundle)
            if case.get("derive_json_from_cnl_bundle") and index == 0 and len(bodies) == 1:
                bodies.append(compact({"format": "som.prompt-json.v1", "prompt": bundle["prompt"],
                                       "target": "typescript"}))
        problems.extend(f"request {index}: {problem}" for problem in found)
        row["requests"].append(record)
    if len({compact(bundle) for bundle in bundles}) > 1:
        problems.append("the request forms gave different bundles")
    row.update(status="failed" if problems else "passed", problems=problems)
    return row


def checked_url(url: str) -> str:
    port = urllib.parse.urlsplit(url).port
    if port in FORBIDDEN_PORTS:
        raise SystemExit(f"{url}: QA never uses port {port}")
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qa", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--legacy-run", type=pathlib.Path, default=check_bundles.DEFAULT_LEGACY_RUN)
    args = parser.parse_args()
    qa, baseline = checked_url(args.qa), checked_url(args.baseline)
    args.out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()

    inventory = json.loads((check_bundles.CONTRACT / "case-inventory.json").read_text())
    examples = json.loads((check_bundles.CONTRACT / "target-examples.json").read_text())
    cases = check_bundles.collect_in_scope_cases(inventory, examples)
    legacy, legacy_problems = check_bundles.collect_legacy_in_scope_cases(
        inventory, args.legacy_run, check_bundles.EXPECTED_LEGACY_IN_SCOPE)
    cases.extend(legacy)
    rows = [check_case(qa, baseline, case) for case in cases]
    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("passed", "failed", "not_run")}
    problems = list(legacy_problems)
    if len(rows) != EXPECTED_CASES:
        problems.append(f"{len(rows)} cases, expected {EXPECTED_CASES}")
    passed = not problems and counts["passed"] == len(rows)
    report = {
        "version": "som.react-output-typescript-baseline.v1",
        "issue": 128,
        "qa": qa,
        "baseline": baseline,
        "legacy_run": str(args.legacy_run),
        "allowed_differences": [f"bundle.{section}.{key}" for section, key in ALLOWED],
        "total": len(rows),
        "passed_count": counts["passed"],
        "failed_count": counts["failed"],
        "not_run_count": counts["not_run"],
        "problems": problems,
        "passed": passed,
        "status": "passed" if passed else "failed",
        "seconds": round(time.monotonic() - started, 3),
        "cases": rows,
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("status", "total", "passed_count", "failed_count", "not_run_count", "problems")}))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
