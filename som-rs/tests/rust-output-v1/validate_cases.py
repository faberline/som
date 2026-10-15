#!/usr/bin/env python3
"""Compile the Rust coverage cases in both input forms on a running compiler.

Only /v1/compile and /v1/assemble are called. The CNL and JSON forms of each
case must give the same Python bundle, and that bundle must assemble. The
report holds hashes and file names only.
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
CASES = HERE / "cases"


def post(base, path, obj):
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    index = json.loads((CASES / "index.json").read_text())
    rows = []
    failures = []
    for case in index["cases"]:
        prompt = json.loads((CASES / case["prompt"]).read_text())
        cnl = (CASES / case["cnl"]).read_text()
        s_json, r_json = post(args.base_url, "/v1/compile", {"format": "som.prompt-json.v1", "prompt": prompt})
        s_cnl, r_cnl = post(args.base_url, "/v1/compile", {"format": "som.cnl.v1", "source": cnl})
        row = {"id": case["id"], "json_status": s_json, "cnl_status": s_cnl}
        if s_json != 200 or s_cnl != 200:
            row["json_error"] = r_json.get("code") if s_json != 200 else None
            row["cnl_error"] = r_cnl.get("code") if s_cnl != 200 else None
            row["diagnostic"] = (r_json if s_json != 200 else r_cnl).get("diagnostics")
            failures.append(case["id"])
            rows.append(row)
            continue
        bundle = r_json["bundle"]
        row["same_bundle"] = bundle == r_cnl["bundle"]
        row["payload_sha256"] = bundle["hashes"]["payload_sha256"]
        row["prompt_sha256"] = bundle["hashes"]["prompt_sha256"]
        s_asm, files = post(args.base_url, "/v1/assemble", {"bundle": bundle})
        row["assemble_status"] = s_asm
        if s_asm == 200:
            row["python_files"] = {name: hashlib.sha256(text.encode()).hexdigest()
                                   for name, text in sorted(files["files"].items())}
        if not row["same_bundle"] or s_asm != 200:
            failures.append(case["id"])
        rows.append(row)
    report = {"version": "som.rust-output-case-validation.v1", "cases": rows,
              "passed": len(rows) - len(failures), "failed": failures}
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        pathlib.Path(args.out).write_text(text)
    print(json.dumps({"passed": report["passed"], "failed": failures,
                      "errors": [r.get("diagnostic") for r in rows if "diagnostic" in r]}, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
