#!/usr/bin/env python3
"""Send every python_control_body of the TypeScript contract to a compiler and check the result.

Each negative case and each name case carries a request without target and
the result the current Python default must give for it. The compile endpoint
is read only, so this may run against the live default on 8765. An expected
"HTTP 200" result needs a som.dsl-bundle.v1 bundle; any other expected result
needs the same status and the same first diagnostic (code, cause, allowed
form, byte span, line and column). --evidence writes the results as a JSON
evidence file.
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

from common import CONTRACT

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
    args = parser.parse_args()
    if args.evidence and args.evidence.exists():
        print(f"refusing to overwrite {args.evidence}", file=sys.stderr)
        return 2
    sources = {name: (CONTRACT / name).read_bytes() for name in ("negative-cases.json", "target-examples.json")}
    cases = json.loads(sources["negative-cases.json"])["cases"]
    cases += json.loads(sources["target-examples.json"])["name_cases"]
    failed = 0
    results = []
    for case in cases:
        status, reply = post(args.url, case["python_control_body"])
        problem = check(case, status, reply)
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
            "version": "som.typescript-output-python-controls.v1",
            "sources_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()},
            "base_url": args.url,
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
