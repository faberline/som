#!/usr/bin/env python3
"""Send every python_control_body of the React contract to a compiler and check the result.

Each negative case and each name case carries a request without target and
the result the current Python default must give for it. The compile endpoint
is read only, so this may run against the live default on 8765. An expected
"HTTP 200" result needs a som.dsl-bundle.v1 bundle; any other expected result
needs the same status and the same first diagnostic (code, cause, allowed
form, byte span, line and column). The typed prompt of each name case's Python
bundle must also pass react_profile.check, so the CNL edits are in scope.
--expect <evidence> also needs every HTTP 200 control to give the payload_sha256
that evidence file recorded, and fails one that file has no payload_sha256 for, so a candidate's Python bundles are byte-compared
with the default's. --evidence writes the results as a JSON evidence file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request
from typing import Any

from common import CONTRACT, react_profile

KEYS = ("code", "cause", "allowed_form", "byte_start", "byte_end", "line", "column")


def post(url: str, body: str) -> tuple[int, Any]:
    request = urllib.request.Request(f"{url}/v1/compile", data=body.encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def check(case: dict[str, Any], status: int, reply: Any) -> str | None:
    if case["python_control_expected"].startswith("HTTP 200"):
        if status == 200 and reply.get("bundle", {}).get("version") == "som.dsl-bundle.v1":
            if case["id"].startswith("RN"):
                found = react_profile.check(reply["bundle"]["prompt"])
                if found is not None:
                    return f"the Python bundle prompt is out of React scope: {found['cause']}"
            return None
        return f"status {status}, {json.dumps(reply)[:300]}"
    if status != case["http_status"]:
        return f"status {status}, expected {case['http_status']}"
    got = reply["diagnostics"][0]
    expected = case["expected"]
    wrong = [key for key in KEYS if got.get(key) != expected.get(key)]
    return f"differs in {wrong}: {json.dumps(got)[:300]}" if wrong else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--evidence", type=pathlib.Path, help="write the results to this new JSON file")
    parser.add_argument("--expect", type=pathlib.Path, help="an earlier evidence file whose payload_sha256 must match")
    args = parser.parse_args()
    if args.evidence and args.evidence.exists():
        print(f"refusing to overwrite {args.evidence}", file=sys.stderr)
        return 2
    sources = {name: (CONTRACT / name).read_bytes() for name in ("negative-cases.json", "target-examples.json")}
    cases = json.loads(sources["negative-cases.json"])["cases"]
    cases += json.loads(sources["target-examples.json"])["name_cases"]
    expected_payloads = {}
    if args.expect:
        expected_payloads = {r["id"]: r["payload_sha256"] for r in json.loads(args.expect.read_text())["results"]
                             if "payload_sha256" in r}
    failed = 0
    results = []
    for case in cases:
        status, reply = post(args.url, case["python_control_body"])
        problem = check(case, status, reply)
        if problem is None and args.expect and status == 200 and case["id"] not in expected_payloads:
            problem = f"no payload_sha256 for {case['id']} in {args.expect.name}"
        if problem is None and case["id"] in expected_payloads:
            payload = reply.get("bundle", {}).get("hashes", {}).get("payload_sha256")
            if payload != expected_payloads[case["id"]]:
                problem = f"payload_sha256 {payload} differs from {args.expect.name}"
        failed += problem is not None
        result = {"id": case["id"], "control_status": status, "passed": problem is None}
        if status == 200:
            result["payload_sha256"] = reply.get("bundle", {}).get("hashes", {}).get("payload_sha256")
        else:
            result["diagnostic"] = {key: reply["diagnostics"][0].get(key) for key in KEYS}
        if problem:
            result["problem"] = problem
        results.append(result)
        print(f"{'ok  ' if problem is None else 'FAIL'} {case['id']}" + (f": {problem}" if problem else ""))
    print(f"{len(cases) - failed} passed, {failed} failed")
    if args.evidence:
        evidence = {
            "version": "som.react-output-python-controls.v1",
            "sources_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()},
            "base_url": args.url,
            "expect": None if args.expect is None else str(args.expect),
            "passed": len(cases) - failed,
            "failed": [r["id"] for r in results if not r["passed"]],
            "results": results,
        }
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        with args.evidence.open("x") as out:
            out.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
