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

Writes <out>/report.json and the exact HTTP request and response bytes.
Exit 0 when every case passes.

usage: check_typescript_baseline.py --qa URL --baseline URL --out NEW_DIR [--legacy-run DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
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


def retain(out: pathlib.Path, relative: str, raw: bytes) -> str:
    path = out / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return relative


def check_request(qa: str, baseline: str, body: str, out: pathlib.Path,
                  stem: str, form: str) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    record: dict[str, Any] = {
        "form": form,
        "body_sha256": sha(body.encode("utf-8")),
        "request_file": retain(out, f"requests/{stem}.compile.json", body.encode("utf-8")),
    }
    if json.loads(body).get("target") != "typescript":
        return ["the request has no target typescript"], record

    def exchange(side: str, url: str, operation: str, request_body: str) -> tuple[int, bytes] | None:
        prefix = side if operation == "compile" else f"{side}_assemble"
        if operation == "assemble":
            record[f"{prefix}_request_file"] = retain(
                out, f"requests/{stem}.{side}.assemble.json", request_body.encode("utf-8"))
            record[f"{prefix}_request_sha256"] = sha(request_body.encode("utf-8"))
        try:
            status, raw = post(url, f"/v1/{operation}", request_body)
        except (OSError, TimeoutError) as error:
            message = f"{side} {operation}: {type(error).__name__}: {error}"
            problems.append(message)
            record.setdefault("not_run_errors", []).append(message)
            return None
        record[f"{prefix}_status"] = status
        record[f"{prefix}_sha256"] = sha(raw)
        record[f"{prefix}_response_file"] = retain(
            out, f"responses/{stem}.{side}.{operation}.json", raw)
        return status, raw

    qa_compile = exchange("qa", qa, "compile", body)
    base_compile = exchange("baseline", baseline, "compile", body)
    if qa_compile is None or base_compile is None:
        return problems, record
    qa_status, qa_raw = qa_compile
    base_status, base_raw = base_compile
    if qa_status != base_status:
        problems.append(f"compile status {qa_status} != baseline {base_status}")
    try:
        qa_reply, base_reply = json.loads(qa_raw), json.loads(base_raw)
    except (ValueError, UnicodeDecodeError) as error:
        problems.append(f"invalid compile JSON: {error}")
        return problems, record
    if compact(without_allowed(qa_reply)) != compact(without_allowed(base_reply)):
        problems.append("compile replies differ outside the allowed members")
    if qa_status != 200 or base_status != 200:
        problems.append("compile did not return a bundle")
        return problems, record
    if any(not isinstance(reply, dict) or reply.get("status") != "ok"
           or not isinstance(reply.get("bundle"), dict) for reply in (qa_reply, base_reply)):
        problems.append("compile response has no successful bundle")
        return problems, record
    if any(not isinstance(reply["bundle"].get(section), dict)
           for reply in (qa_reply, base_reply) for section in ("prompt", "assets", "hashes")):
        problems.append("compile bundle has no prompt, assets or hashes object")
        return problems, record
    differs = [f"{section}.{key}" for section, key in ALLOWED
               if qa_reply["bundle"].get(section, {}).get(key)
               != base_reply["bundle"].get(section, {}).get(key)]
    record["differing_members"] = differs
    assembled: dict[str, dict[str, str]] = {}
    for side, url, reply in (("qa", qa, qa_reply), ("baseline", baseline, base_reply)):
        result = exchange(side, url, "assemble", compact({"bundle": reply["bundle"]}))
        if result is None:
            continue
        status, raw = result
        if status != 200:
            problems.append(f"{side} assemble status {status} != 200")
            continue
        try:
            document = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            problems.append(f"invalid {side} assemble JSON: {error}")
            continue
        if not isinstance(document, dict) or document.get("status") != "ok":
            problems.append(f"{side} assemble response has no successful status")
            continue
        files = document.get("files")
        if (not isinstance(files, dict) or not files
                or not all(isinstance(path, str) and isinstance(text, str) for path, text in files.items())):
            problems.append(f"{side} assemble response files is not a nonempty object of strings")
            continue
        assembled[side] = files
        hashes = {path: sha(text.encode("utf-8")) for path, text in files.items()}
        record["files_sha256" if side == "qa" else "baseline_files_sha256"] = hashes
    if len(assembled) == 2 and assembled["qa"] != assembled["baseline"]:
        qa_files = record["files_sha256"]
        base_files = record["baseline_files_sha256"]
        changed = sorted(set(qa_files.items()) ^ set(base_files.items()))
        problems.append(f"product files differ: {sorted({path for path, _ in changed})}")
    record["bundle"] = qa_reply["bundle"]
    return problems, record


def check_case(qa: str, baseline: str, case: dict[str, Any], out: pathlib.Path) -> dict[str, Any]:
    row: dict[str, Any] = {"id": case["id"], "group": case["group"], "requests": []}
    problems = list(case.get("file_problems") or [])
    if case.get("not_run_error"):
        row.update(status="not_run", passed=False, problems=[case["not_run_error"]])
        return row
    bundles = []
    requests = list(case["requests"])
    if not requests:
        row.update(status="not_run", passed=False, problems=[*problems, "case has no compile requests"])
        return row
    case_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", case["id"])
    not_run = False
    for index, (form, body) in enumerate(requests):
        stem = f"{case_stem}/{index}-{form}"
        found, record = check_request(qa, baseline, body, out, stem, form)
        not_run = not_run or bool(record.get("not_run_errors"))
        bundle = record.pop("bundle", None)
        if bundle is not None:
            bundles.append(bundle)
            if case.get("derive_json_from_cnl_bundle") and index == 0 and len(requests) == 1:
                requests.append(("json", compact({"format": "som.prompt-json.v1", "prompt": bundle["prompt"],
                                                 "target": "typescript"})))
        problems.extend(f"request {index}: {problem}" for problem in found)
        row["requests"].append(record)
    if len({compact(bundle) for bundle in bundles}) > 1:
        problems.append("the request forms gave different bundles")
    row.update(status="not_run" if not_run else "failed" if problems else "passed",
               passed=not problems, problems=problems)
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
    rows = [check_case(qa, baseline, case, args.out) for case in cases]
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
