#!/usr/bin/env python3
"""Write the React output evidence files from an acceptance run and a name-case run.

Usage: make_evidence.py --public-run <run_products.py work dir> --name-run <run_name_cases.py work dir>
                        --date YYYY-MM-DD

Writes, under som-rs/evidence/react-output-v1:
- public-products-<date>.json: the run_products.py summary;
- reference-verification-<date>.json: the gates and API contract of public product 003;
- name-cases-<date>.json: the run_name_cases.py summary.
Every absolute path under the repository is written relative to it. Exits 1, writing
nothing, unless both runs passed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from common import EVIDENCE, REPO

PUBLIC_PRODUCTS = ".local/rust-compiler-goal/acceptance/public-products-003b"
PYTHON = "som-rs/runtime/acceptance-m3-venv/bin/python"
REFERENCE_PRODUCT = "003"


def relative(value: Any) -> Any:
    """value with every string path under the repository made repository-relative."""
    if isinstance(value, dict):
        return {k: relative(v) for k, v in value.items()}
    if isinstance(value, list):
        return [relative(v) for v in value]
    if isinstance(value, str):
        return value.replace(str(REPO) + "/", "")
    return value


def rel(path: pathlib.Path) -> str:
    return path.resolve().relative_to(REPO).as_posix()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-run", type=pathlib.Path, required=True)
    parser.add_argument("--name-run", type=pathlib.Path, required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    public = json.loads((args.public_run / "summary.json").read_text())
    names = json.loads((args.name_run / "summary.json").read_text())
    if public.get("passed") is not True or names.get("passed") is not True:
        print("a run did not pass", file=sys.stderr)
        return 1
    ports = "QA ports from behavior/hosts.py Ports; never 8765 or 18765"
    out: dict[str, Any] = {}

    public_ev = relative(public)
    public_ev["run"] = {"date": args.date, "work_dir": rel(args.public_run),
                        "command": f"{PYTHON} som-rs/tests/react-output-v1/run_products.py {PUBLIC_PRODUCTS} "
                                   f"{rel(args.public_run)}", "ports": ports}
    out[f"public-products-{args.date}.json"] = public_ev

    case = args.public_run / REFERENCE_PRODUCT
    gates = json.loads((case / "gates" / "gates.json").read_text())
    api = json.loads((case / "api" / "report.json").read_text())
    prompt = pathlib.Path(PUBLIC_PRODUCTS) / REFERENCE_PRODUCT
    prompt_sha = next(c["prompt_file_sha256"] for c in public["cases"] if c["index"] == REFERENCE_PRODUCT)
    # A step whose when condition is false is recorded with ok null; check_contract checks which ones.
    api_ok = (int(api["exit_code"]) == 0 and api["result"]["failed"] is False
              and all(step["ok"] is not False for step in api["result"]["steps"]))
    stopped = bool(api["processes"]) and all(p.get("stopped") is True for p in api["processes"])
    reference = {"version": "som.react-output-reference-verification.v1", "date": args.date,
                 "product": f"public product {REFERENCE_PRODUCT} (som.prompt.v4, resource Item, fields "
                            "str/bool/date), rendered by render_reference.py",
                 "public_product": prompt.as_posix(), "prompt_file_sha256": prompt_sha,
                 "product_files": gates["product_files"], "product_files_after": gates["product_files_after"],
                 "product_unchanged": gates["product_unchanged"], "environment": relative(gates["environment"]),
                 "gates": gates["gates"], "builds_identical": gates["builds_identical"],
                 "dist_files": gates["dist_files"], "lock_unchanged": gates["lock_unchanged"],
                 "vendored_cache_tree_sha256_before": gates["vendored_cache_tree_sha256_before"],
                 "vendored_cache_tree_sha256_after": gates["vendored_cache_tree_sha256_after"],
                 "prettier_tree_sha256": gates["prettier_tree_sha256"],
                 "api_contract": {"exit_code": int(api["exit_code"]), "facts": api["result"]["facts"],
                                  "steps": api["result"]["steps"],
                                  "processes_stopped": stopped},
                 "run": {"work_dir": rel(case), "command": public_ev["run"]["command"]}}
    reference["passed"] = gates["passed"] is True and api_ok and stopped
    if not reference["passed"]:
        print("reference product did not pass", file=sys.stderr)
        return 1
    out[f"reference-verification-{args.date}.json"] = reference

    name_ev = relative(names)
    name_ev["api_contract"] = ("not run: a name case has no hosted Python product. The API contract runs against "
                               "the public products and, after the React target lands, in the M6 acceptance.")
    name_ev["run"] = {"date": args.date, "work_dir": rel(args.name_run),
                      "command": f"python3 som-rs/tests/react-output-v1/run_name_cases.py {rel(args.name_run)}",
                      "prompts": "each case's python_control_body compiled read-only on 127.0.0.1:8765"}
    out[f"name-cases-{args.date}.json"] = name_ev

    texts = {name: json.dumps(relative(data), indent=2, ensure_ascii=False) + "\n" for name, data in out.items()}
    for name, text in texts.items():
        if str(REPO) in text:
            print(f"{name} holds an absolute path", file=sys.stderr)
            return 1
    for name, text in texts.items():
        (EVIDENCE / name).write_text(text)
        print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
