#!/usr/bin/env python3
"""Post each negative case's python_control_body to a compiler and compare.

A control without target must compile to an unchanged som.dsl-bundle.v1
bundle, or, for RP cases, give exactly the frozen diagnostic. RT07 also checks
that the duplicate target member is invalid JSON today. Use a QA compiler
port, or the default compiler for read-only calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rust_profile  # type: ignore[import-not-found]  # noqa: E402

CASES = HERE.parent.parent / "contracts" / "rust-output-v1" / "negative-cases.json"


def post(base_url: str, body: str) -> tuple[int, dict]:
    req = urllib.request.Request(base_url.rstrip("/") + "/v1/compile", data=body.encode(),
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def same_diagnostic(status: int, response: dict, case: dict) -> bool:
    expected = case["expected"]
    if status != case["http_status"] or response.get("code") != expected["code"]:
        return False
    diagnostics = response.get("diagnostics", [])
    return len(diagnostics) == 1 and all(diagnostics[0].get(k) == v for k, v in expected.items())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    data = CASES.read_bytes()
    results = []
    for case in json.loads(data)["cases"]:
        status, response = post(args.base_url, case["python_control_body"])
        if case["id"].startswith("RP"):
            ok = same_diagnostic(status, response, case)
        else:
            ok = status == 200 and response.get("bundle", {}).get("version") == "som.dsl-bundle.v1"
        row = {"id": case["id"], "control_status": status, "passed": ok}
        if ok and status == 200:
            row["payload_sha256"] = response["bundle"]["hashes"]["payload_sha256"]
        if ok and case["id"].startswith("RS") and case["form"] == "cnl":
            # The compiled prompt of a CNL scope case must give the frozen refusal.
            got = rust_profile.check(response["bundle"]["prompt"])
            expected = case["expected"]
            row["rust_profile_matches"] = got is not None and got["pointer"] == expected["prompt_pointer"] \
                and got["cause"] == expected["cause"] and got["allowed_form"] == expected["allowed_form"]
            row["passed"] = row["rust_profile_matches"]
        if case["expected"]["code"] == "INVALID_JSON":
            dup_status, dup_response = post(args.base_url, case["body"])
            row["case_body_status"] = dup_status
            row["case_body_matches_today"] = same_diagnostic(dup_status, dup_response, case)
            row["passed"] = ok and row["case_body_matches_today"]
        results.append(row)
    failed = [r["id"] for r in results if not r["passed"]]
    report = {"version": "som.rust-output-python-controls.v1", "negative_cases_sha256": hashlib.sha256(data).hexdigest(),
              "base_url": args.base_url, "passed": len(results) - len(failed), "failed": failed, "results": results}
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    print(f"passed {report['passed']}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
