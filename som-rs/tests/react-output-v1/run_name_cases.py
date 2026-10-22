#!/usr/bin/env python3
"""Render every React name case and run the phase 1 product gates on it.

Usage: run_name_cases.py <work dir (new)> [--compile-url URL]

For each name case of target-examples.json, the Python control body is compiled
on the live default compiler (read only, 8765 by default) to get the typed
prompt; render_reference.py renders it to <work>/<id>/render and run_gates.py
runs the gates in <work>/<id>/gates. Writes <work>/summary.json and exits 0 when
every case passes. The API contract is not run: a name case has no hosted
Python product.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import urllib.request

from common import CONTRACT, GENERATED, HERE, fixed_files_sha256


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=pathlib.Path)
    parser.add_argument("--compile-url", default="http://127.0.0.1:8765/v1/compile")
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    cases = json.loads((CONTRACT / "target-examples.json").read_text())["name_cases"]
    rows = []
    for case in cases:
        request = urllib.request.Request(args.compile_url, data=case["python_control_body"].encode(),
                                         method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as reply:
            prompt = json.loads(reply.read())["bundle"]["prompt"]
        case_dir = work / case["id"]
        case_dir.mkdir()
        (case_dir / "prompt.json").write_text(json.dumps(prompt, indent=2) + "\n")
        render = subprocess.run([sys.executable, str(HERE / "render_reference.py"), str(case_dir / "prompt.json"),
                                 str(case_dir / "render")], capture_output=True, text=True, check=False)
        gates_run = subprocess.run([sys.executable, str(HERE / "run_gates.py"), str(case_dir / "render"),
                                    str(case_dir / "gates")], capture_output=True, text=True, check=False)
        report_path = case_dir / "gates" / "gates.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        render_dir = case_dir / "render"
        row = {"id": case["id"], "prompt_version": prompt["version"],
               "prompt_file_sha256": hashlib.sha256((case_dir / "prompt.json").read_bytes()).hexdigest(),
               "generated_files": {name: hashlib.sha256((render_dir / name).read_bytes()).hexdigest()
                                   for name in GENERATED if (render_dir / name).is_file()},
               "fixed_files_sha256": fixed_files_sha256(report["product_files"]) if report else None,
               "render_exit": render.returncode, "gates_exit": gates_run.returncode,
               "gates": {g["step"]: g["exit_code"] for g in report.get("gates", [])},
               "builds_identical": report.get("builds_identical"), "lock_unchanged": report.get("lock_unchanged"),
               "product_unchanged": report.get("product_unchanged"),
               "vendored_cache_unchanged": report.get("vendored_cache_tree_sha256_before")
               == report.get("vendored_cache_tree_sha256_after") if report else None,
               "passed": render.returncode == 0 and gates_run.returncode == 0 and report.get("passed") is True}
        rows.append(row)
        print(json.dumps(row), flush=True)
    passed = all(row["passed"] for row in rows)
    (work / "summary.json").write_text(json.dumps({"version": "som.react-output-name-cases.v1", "cases": rows,
                                                   "passed": passed}, indent=2) + "\n")
    print(f"passed={passed}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
