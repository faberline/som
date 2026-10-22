#!/usr/bin/env python3
"""Render, gate and API-check the public v1 to v4 React products against their Python products.

Usage: run_products.py <public products dir> <work dir (new)>

For each public product index in PRODUCTS: render_reference.py renders the prompt
to <work>/<idx>/render, run_gates.py runs the gates in <work>/<idx>/gates, and
run_api_contract.py drives the rendered api.ts against the hosted Python product
in <work>/<idx>/api. Writes <work>/summary.json and exits 0 when every case passes.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

from common import GENERATED, fixed_files_sha256

HERE = pathlib.Path(__file__).resolve().parent
# run_api_contract.py hosts the Python product, which needs the acceptance venv (psycopg).
PYTHON = str(HERE.parent.parent / "runtime" / "acceptance-m3-venv" / "bin" / "python")
PRODUCTS = ["000", "119", "001", "120", "002", "121", "003", "122"]


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def step(argv: list[str], log: pathlib.Path) -> int:
    with log.open("w") as out:
        return subprocess.run([PYTHON, *argv], stdout=out, stderr=subprocess.STDOUT, timeout=1800,
                              check=False).returncode


def main() -> int:
    public, work = (pathlib.Path(arg).resolve() for arg in sys.argv[1:3])
    work.mkdir(parents=True, exist_ok=False)
    cases = []
    for idx in PRODUCTS:
        case = public / idx
        out = work / idx
        out.mkdir()
        prompt = json.loads((case / "prompt.json").read_text())
        render = out / "render"
        codes = {"render": step([str(HERE / "render_reference.py"), str(case / "prompt.json"), str(render)],
                                out / "render.log")}
        codes["gates"] = step([str(HERE / "run_gates.py"), str(render), str(out / "gates")], out / "gates.log")
        codes["api_contract"] = step([str(HERE / "run_api_contract.py"), str(case), str(render), str(out / "api")],
                                     out / "api.log")
        gates = json.loads((out / "gates" / "gates.json").read_text()) if (out / "gates" / "gates.json").is_file() \
            else None
        api = json.loads((out / "api" / "report.json").read_text()) if (out / "api" / "report.json").is_file() \
            else None
        steps = (api or {}).get("result") or {}
        row = {
            "index": idx,
            "prompt_version": prompt["version"],
            "resource": prompt["resource"]["name"],
            "prompt_file_sha256": sha(case / "prompt.json"),
            "generated_files": {name: sha(render / name) for name in GENERATED if (render / name).is_file()},
            "fixed_files_sha256": None if gates is None else fixed_files_sha256(gates["product_files"]),
            "exit_codes": codes,
            "gates": None if gates is None else {g["step"]: g["exit_code"] for g in gates["gates"]},
            "builds_identical": None if gates is None else gates["builds_identical"],
            "lock_unchanged": None if gates is None else gates["lock_unchanged"],
            "product_unchanged": None if gates is None else gates["product_unchanged"],
            "vendored_cache_unchanged": None if gates is None else
            gates["vendored_cache_tree_sha256_before"] == gates["vendored_cache_tree_sha256_after"],
            "dist_files": None if gates is None else gates["dist_files"],
            "api_facts": steps.get("facts"),
            "api_steps": [{k: v for k, v in s.items() if k in ("name", "ok", "skipped")}
                          for s in steps.get("steps", [])],
            "processes_stopped": None if api is None else all(p["stopped"] for p in api["processes"]),
        }
        row["passed"] = all(code == 0 for code in codes.values()) and bool(row["processes_stopped"])
        print(f"{idx} {prompt['version']} {codes} passed={row['passed']}", flush=True)
        cases.append(row)
    summary = {"version": "som.react-output-public-products.v1", "public_products": str(public),
               "cases": cases, "passed": all(c["passed"] for c in cases)}
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"passed={summary['passed']}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
