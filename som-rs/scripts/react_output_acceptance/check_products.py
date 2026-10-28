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
With --cleanup-runtime, each completed case keeps its evidence and removes only
its installed node_modules and its PostgreSQL data after a verified stop.

usage: check_products.py --url URL --manifest MANIFEST --out DIR [--cache DIR] [--only ID ...] [--cleanup-runtime]
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

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


def cleanup_root_path(path: pathlib.Path) -> pathlib.Path:
    """Keep the original path so a symlink cannot disappear through resolve()."""
    if ".." in path.parts:
        raise ValueError("runtime cleanup refuses an output path with '..'")
    return path if path.is_absolute() else pathlib.Path.cwd() / path


def _open_cleanup_child(parent_fd: int, name: str) -> int:
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"runtime cleanup refuses symlink directory {name!r}")
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"runtime cleanup requires a directory at {name!r}")
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)


@contextlib.contextmanager
def _cleanup_directory(path: pathlib.Path) -> Iterator[int]:
    """Open every directory without following a symlink, and hold its file descriptor."""
    with contextlib.ExitStack() as stack:
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        for name in path.parts[1:]:
            fd = _open_cleanup_child(fd, name)
            stack.callback(os.close, fd)
        yield fd


def validate_cleanup_root(path: pathlib.Path) -> pathlib.Path:
    """Check existing output ancestors before main creates a new output directory."""
    path = cleanup_root_path(path)
    try:
        with _cleanup_directory(path):
            pass
    except FileNotFoundError:
        pass
    return path


def _postgres_cleanup_proof(api_fd: int, pg_fd: int, pgdata: pathlib.Path) -> dict[str, Any]:
    try:
        os.stat("postmaster.pid", dir_fd=pg_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise ValueError("PostgreSQL cleanup refuses an existing postmaster.pid")

    report_fd = os.open("report.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=api_fd)
    with os.fdopen(report_fd, "rb") as report_file:
        if not stat.S_ISREG(os.fstat(report_file.fileno()).st_mode):
            raise ValueError("PostgreSQL cleanup requires a regular api/report.json")
        raw = report_file.read()
    report = json.loads(raw)
    processes = report.get("processes") if isinstance(report, dict) else None
    if not isinstance(processes, list):
        raise ValueError("PostgreSQL cleanup requires api/report.json process records")
    postgres = [record for record in processes if isinstance(record, dict) and record.get("role") == "postgres"]
    if len(postgres) != 1:
        raise ValueError("PostgreSQL cleanup requires exactly one owned postgres process record")
    record = postgres[0]
    if record.get("expected_cwd") != str(pgdata):
        raise ValueError("PostgreSQL cleanup expected_cwd does not match this case's pgdata")
    if record.get("stopped") is not True:
        raise ValueError("PostgreSQL cleanup requires stopped=True")
    if record.get("stop_cwd_verified") is not True or record.get("stop_cwd") != str(pgdata):
        raise ValueError("PostgreSQL cleanup requires a verified stop_cwd matching this case's pgdata")
    return {"report": "api/report.json", "report_sha256": hashlib.sha256(raw).hexdigest()}


def cleanup_case_runtime(out: pathlib.Path, cid: str) -> dict[str, Any]:
    """Remove only the two owned runtime directories after all case evidence is saved."""
    targets = (pathlib.Path("gates/product/node_modules"), pathlib.Path("api/pgdata"))
    result: dict[str, Any] = {"passed": False, "targets": [], "problems": []}
    try:
        if not isinstance(cid, str) or cid in ("", ".", "..") or "/" in cid or "\\" in cid:
            raise ValueError("runtime cleanup requires a single case directory name")
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            raise ValueError("runtime cleanup requires file-descriptor-safe directory removal")
        root = cleanup_root_path(out)
        with _cleanup_directory(root) as root_fd, contextlib.ExitStack() as case_stack:
            case_fd = _open_cleanup_child(root_fd, cid)
            case_stack.callback(os.close, case_fd)
            for relative in targets:
                target: dict[str, Any] = {"path": (pathlib.Path(cid) / relative).as_posix()}
                result["targets"].append(target)
                target_found = False
                try:
                    with contextlib.ExitStack() as target_stack:
                        parent_fd = case_fd
                        for part in relative.parts[:-1]:
                            parent_fd = _open_cleanup_child(parent_fd, part)
                            target_stack.callback(os.close, parent_fd)
                        target_fd = _open_cleanup_child(parent_fd, relative.name)
                        target_stack.callback(os.close, target_fd)
                        target_found = True
                        if relative == pathlib.Path("api/pgdata"):
                            target["stop_proof"] = _postgres_cleanup_proof(parent_fd, target_fd, root / cid / relative)
                        shutil.rmtree(relative.name, dir_fd=parent_fd)
                        target["status"] = "removed"
                except Exception as exc:  # noqa: BLE001
                    if isinstance(exc, FileNotFoundError) and not target_found:
                        target["status"] = "missing"
                    else:
                        target["status"] = "refused"
                        target["problem"] = f"{type(exc).__name__}: {exc}"
                        result["problems"].append(f"{target['path']}: {target['problem']}")
    except Exception as exc:  # noqa: BLE001
        result["problems"].append(f"{type(exc).__name__}: {exc}")
    result["passed"] = not result["problems"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    toolchain = json.loads((CONTRACT / "toolchain.json").read_text())
    parser.add_argument("--cache", type=pathlib.Path, default=REPO / toolchain["vendored_cache"]["path"])
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--cleanup-runtime", action="store_true",
                        help="Remove only each completed case's node_modules and verified stopped PostgreSQL data")
    args = parser.parse_args()
    port = urllib.parse.urlsplit(args.url).port
    if port in FORBIDDEN_PORTS:
        print(f"refused: port {port} is not a QA port", file=sys.stderr)
        return 2
    if args.cleanup_runtime:
        try:
            out = validate_cleanup_root(args.out)
        except (OSError, ValueError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    else:
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
        if args.cleanup_runtime:
            try:
                cleanup = cleanup_case_runtime(out, entry["id"])
            except Exception as exc:  # noqa: BLE001
                cleanup = {"passed": False, "targets": [], "problems": [f"{type(exc).__name__}: {exc}"]}
            row["cleanup_runtime"] = cleanup
            if cleanup["passed"] is not True:
                row["problems"].extend(f"runtime cleanup: {problem}" for problem in cleanup["problems"])
                row["passed"] = False
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
