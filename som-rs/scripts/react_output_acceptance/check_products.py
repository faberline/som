#!/usr/bin/env python3
"""Run the React product gates for every assembled acceptance product (issue #129).

react-output-v1 acceptance-plan.json product_gates, for each entry of the
products/manifest.json that check_bundles.py wrote:
- tests/react-output-v1/run_gates.py on the React product with the vendored npm
  cache: install offline with --ignore-scripts, tsc, prettier --check, vitest,
  two identical builds, lock, cache tree and product files unchanged;
- the Python product the candidate assembles for the same request without target
  (POST <url>/v1/compile, then POST <url>/v1/assemble), written with its prompt
  to <out>/<case-id>/python;
- tests/react-output-v1/run_api_contract.py with the product's own src/api.ts
  against that Python product on QA ports with a fresh PostgreSQL cluster. All
  20 api-contract.json steps are recorded, none fails, the skipped steps are
  exactly those whose when condition is false for the prompt, and every process
  is stopped.

No sampling: every manifest entry runs every gate, one case at a time.
Writes <out>/report.json and exits 0 when every case passes.

usage: check_products.py --url URL --manifest MANIFEST --out DIR [--cache DIR] [--only ID ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REACT_TESTS = SOM_RS / "tests" / "react-output-v1"
if str(REACT_TESTS) not in sys.path:
    sys.path.insert(0, str(REACT_TESTS))

import check_contract  # type: ignore[import-not-found]  # noqa: E402
from common import CONTRACT, REPO  # type: ignore[import-not-found]  # noqa: E402

PYTHON = str(SOM_RS / "runtime" / "acceptance-m3-venv" / "bin" / "python")
FORBIDDEN_PORTS = (8765, 18765)
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EXPECTED_PRODUCTS = 129


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def post(base_url: str, route: str, body: bytes) -> tuple[int, bytes]:
    req = urllib.request.Request(base_url.rstrip("/") + route, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with NO_PROXY_OPENER.open(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        with err:
            return err.code, err.read()


def python_product(base_url: str, entry: dict[str, Any], case_dir: pathlib.Path) -> str | None:
    """Writes prompt.json and product/ for the Python product of the request without target."""
    request = json.loads(entry["python_request"])
    if "target" in request:
        return "python_request has a target"
    status, raw = post(base_url, "/v1/compile", entry["python_request"].encode("utf-8"))
    (case_dir / "compile.response.json").write_bytes(raw)
    if status != 200:
        return f"python compile returned {status}"
    bundle = json.loads(raw)["bundle"]
    if bundle.get("target", "python") != "python":
        return f"python compile gave target {bundle.get('target')}"
    if bundle["prompt"] != entry["prompt"]:
        return "python bundle prompt differs from the React bundle prompt"
    body = json.dumps({"bundle": bundle}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    status, raw = post(base_url, "/v1/assemble", body.encode("utf-8"))
    (case_dir / "assemble.response.json").write_bytes(raw)
    if status != 200:
        return f"python assemble returned {status}"
    files = json.loads(raw)["files"]
    (case_dir / "prompt.json").write_text(json.dumps(entry["prompt"], ensure_ascii=False, indent=2) + "\n")
    for rel, content in files.items():
        target = case_dir / "product" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return None


def step(argv: list[str], log: pathlib.Path) -> int:
    with log.open("w") as out:
        return subprocess.run([PYTHON, *argv], stdout=out, stderr=subprocess.STDOUT, timeout=3600,
                              check=False).returncode


def check_case(base_url: str, entry: dict[str, Any], out: pathlib.Path, cache: pathlib.Path,
               api: dict[str, Any]) -> dict[str, Any]:
    cid = entry["id"]
    work = out / cid
    work.mkdir()
    problems: list[str] = []
    product = pathlib.Path(entry["product"])
    codes: dict[str, int | None] = {"gates": None, "api_contract": None}
    codes["gates"] = step([str(REACT_TESTS / "run_gates.py"), str(product), str(work / "gates"),
                           "--cache", str(cache)], work / "gates.log")
    gates_path = work / "gates" / "gates.json"
    gates = json.loads(gates_path.read_text()) if gates_path.is_file() else None
    if codes["gates"] != 0 or gates is None or gates.get("passed") is not True:
        problems.append(f"run_gates.py exit {codes['gates']}")

    python_dir = work / "python"
    python_dir.mkdir()
    python_problem = python_product(base_url, entry, python_dir)
    report = None
    if python_problem is not None:
        problems.append(python_problem)
    else:
        codes["api_contract"] = step([str(REACT_TESTS / "run_api_contract.py"), str(python_dir), str(product),
                                      str(work / "api")], work / "api.log")
        report_path = work / "api" / "report.json"
        report = json.loads(report_path.read_text()) if report_path.is_file() else None
    result = (report or {}).get("result") or {}
    steps = result.get("steps") or []
    facts = result.get("facts")
    stopped = None if report is None else all(p["stopped"] for p in report["processes"])
    if python_problem is None:
        if codes["api_contract"] != 0:
            problems.append(f"run_api_contract.py exit {codes['api_contract']}")
        if len(steps) != 20:
            problems.append(f"{len(steps)} API steps, expected 20")
        expected_facts = check_contract.spec_facts(entry["prompt"])
        if facts != expected_facts:
            problems.append(f"API facts {facts} != prompt facts {expected_facts}")
        skip_problem = check_contract.api_steps_ok(steps, expected_facts, api)
        if skip_problem is not None:
            problems.append(f"API steps: {skip_problem}")
        if stopped is not True:
            problems.append("a process run_api_contract.py started is not stopped")
    row = {
        "id": cid,
        "group": entry["group"],
        "prompt_version": entry["prompt"]["version"],
        "exit_codes": codes,
        "gates": None if gates is None else {g["step"]: g["exit_code"] for g in gates["gates"]},
        "builds_identical": None if gates is None else gates["builds_identical"],
        "lock_unchanged": None if gates is None else gates["lock_unchanged"],
        "product_unchanged": None if gates is None else gates["product_unchanged"],
        "vendored_cache_unchanged": None if gates is None else
        gates["vendored_cache_tree_sha256_before"] == gates["vendored_cache_tree_sha256_after"],
        "product_files": None if gates is None else gates["product_files"],
        "dist_files_sha256": None if gates is None else hashlib.sha256(
            json.dumps(gates["dist_files"], sort_keys=True).encode()).hexdigest(),
        "python_product_files": None if python_problem is not None else {
            p.relative_to(python_dir / "product").as_posix(): sha(p)
            for p in sorted((python_dir / "product").rglob("*")) if p.is_file()},
        "api_facts": facts,
        "api_steps": [{k: v for k, v in s.items() if k in ("name", "ok", "skipped")} for s in steps],
        "processes_stopped": stopped,
        "problems": problems,
        "passed": not problems,
    }
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    toolchain = json.loads((CONTRACT / "toolchain.json").read_text())
    parser.add_argument("--cache", type=pathlib.Path, default=REPO / toolchain["vendored_cache"]["path"])
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()
    port = urllib.parse.urlsplit(args.url).port
    if port in FORBIDDEN_PORTS:
        print(f"refused: port {port} is not a QA port", file=sys.stderr)
        return 2
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    entries = manifest["products"] if isinstance(manifest, dict) else manifest
    if args.only:
        entries = [e for e in entries if e["id"] in args.only]
    api = json.loads((CONTRACT / "api-contract.json").read_text())
    rows = []
    started = time.time()
    for index, entry in enumerate(entries, 1):
        t0 = time.time()
        row = check_case(args.url, entry, out, args.cache.resolve(), api)
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        print(f"[{index}/{len(entries)}] {row['id']} passed={row['passed']} {row['seconds']}s "
              f"{'; '.join(row['problems'])}", flush=True)
        (out / "progress.json").write_text(json.dumps({"done": index, "total": len(entries)}) + "\n")
    complete = args.only is None and len(rows) == EXPECTED_PRODUCTS
    report = {
        "version": "som.react-output-product-gates.v1",
        "issue": 129,
        "url": args.url,
        "manifest": str(manifest_path),
        "manifest_sha256": sha(manifest_path),
        "vendored_cache": str(args.cache.resolve()),
        "case_count": len(rows),
        "complete": complete,
        "failed": [r["id"] for r in rows if not r["passed"]],
        "seconds": round(time.time() - started, 1),
        "cases": rows,
    }
    report["passed"] = complete and not report["failed"]
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"cases={len(rows)} failed={len(report['failed'])} complete={complete} passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
