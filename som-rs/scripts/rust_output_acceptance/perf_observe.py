#!/usr/bin/env python3
"""Record build time, memory and output bytes for the Rust products. Observations only.

No measurement has a limit. A measurement that cannot be taken is reported as
missing and makes the report incomplete.

- Compile and assemble latency and response bytes on the QA compiler, for every
  acceptance case with target rust and without a target.
- Files and bytes of every product check_rust_products.py wrote.
- A rebuild of the app crate of every product on the shared target directory,
  with its dependencies already built: seconds and max RSS.
- Cold debug and release builds of example-v1 and example-v5 in new target
  directories.
- The candidate build timings from its build.json.

usage: perf_observe.py --base-url URL --products DIR --target-dir DIR --candidate DIR --out NEW_DIR
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[3]
QA = REPO / "som-rs/som-targets/qa"
sys.path.insert(0, str(QA))
import check_rust_products as products  # type: ignore[import-not-found]  # noqa: E402
import check_targets as targets  # type: ignore[import-not-found]  # noqa: E402

FORBIDDEN_PORTS = {8765, 18765}
COLD_CASES = ("example-v1", "example-v5")


def post(base_url: str, path: str, data: bytes) -> tuple[int, bytes, float]:
    request = urllib.request.Request(base_url + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    return status, raw, time.perf_counter() - started


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {"n": len(ordered), "min": ordered[0], "median": statistics.median(ordered),
            "p95": ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))], "max": ordered[-1],
            "sum": sum(ordered)}


def timed(command: list[str], cwd: pathlib.Path, env: dict, log: pathlib.Path) -> dict:
    started = time.monotonic()
    with log.open("wb") as handle:
        code = subprocess.run(["/usr/bin/time", "-l", *command], cwd=cwd, env=env,
                              stdout=handle, stderr=subprocess.STDOUT).returncode
    seconds = time.monotonic() - started
    rss = re.search(r"(\d+)\s+maximum resident set size", log.read_text(errors="replace"))
    return {"exit_code": code, "seconds": seconds, "max_rss_bytes": int(rss.group(1)) if rss else None}


def key(case_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", case_id.lower())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--products", type=pathlib.Path, required=True)
    parser.add_argument("--target-dir", type=pathlib.Path, required=True)
    parser.add_argument("--candidate", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    split = urllib.parse.urlsplit(args.base_url)
    if split.hostname not in {"127.0.0.1", "localhost", "::1"} or split.port in FORBIDDEN_PORTS:
        raise SystemExit("use a loopback QA port, not 8765 or 18765")
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    logs = out / "logs"
    logs.mkdir()
    base_url = args.base_url.rstrip("/")
    missing: list[str] = []
    cases = products.acceptance_cases()

    # Service latency and response bytes.
    service = []
    for case in cases:
        row: dict = {"id": case["id"], "version": case["prompt"]["version"]}
        rust_body = products.compile_body(case, "json").encode()
        status, raw, seconds = post(base_url, "/v1/compile", rust_body)
        row["rust_compile"] = {"status": status, "seconds": seconds, "response_bytes": len(raw)}
        if status == 200:
            bundle = json.loads(raw)["bundle"]
            data = targets.dump({"bundle": bundle}).encode()
            status, raw, seconds = post(base_url, "/v1/assemble", data)
            row["rust_assemble"] = {"status": status, "seconds": seconds, "request_bytes": len(data),
                                    "response_bytes": len(raw)}
        python_body = targets.dump({"format": "som.prompt-json.v1", "prompt": case["prompt"]}).encode()
        status, raw, seconds = post(base_url, "/v1/compile", python_body)
        row["python_compile"] = {"status": status, "seconds": seconds, "response_bytes": len(raw)}
        if status == 200:
            data = targets.dump({"bundle": json.loads(raw)["bundle"]}).encode()
            status, raw, seconds = post(base_url, "/v1/assemble", data)
            row["python_assemble"] = {"status": status, "seconds": seconds, "response_bytes": len(raw)}
        for name in ("rust_compile", "rust_assemble", "python_compile", "python_assemble"):
            if row.get(name, {}).get("status") != 200:
                missing.append(f"{case['id']} {name}")
        service.append(row)

    # Product files and bytes.
    files = []
    for case in cases:
        root = args.products / key(case["id"])
        if not (root / "Cargo.toml").is_file():
            missing.append(f"{case['id']} product directory")
            continue
        paths = [p for p in root.rglob("*") if p.is_file()]
        rs = [p for p in paths if p.suffix == ".rs"]
        files.append({"id": case["id"], "files": len(paths), "bytes": sum(p.stat().st_size for p in paths),
                      "rs_files": len(rs), "rs_bytes": sum(p.stat().st_size for p in rs),
                      "rs_lines": sum(p.read_text().count("\n") for p in rs)})

    # App crate rebuilds on the shared target directory.
    env = {**os.environ, **products.pinned_toolchain(), "CARGO_TARGET_DIR": str(args.target_dir)}
    cargo = str(products.BIN_DIR / "cargo")
    rebuilds = []
    for case in cases:
        root = args.products / key(case["id"])
        if not root.is_dir():
            continue
        clean = subprocess.run([cargo, "clean", "--offline", "-p", "som_app"], cwd=root, env=env,
                               capture_output=True)
        row = {"id": case["id"], "clean_exit_code": clean.returncode,
               **timed([cargo, "build", "--locked", "--offline", "-q"], root, env, logs / f"rebuild-{key(case['id'])}.log")}
        if row["clean_exit_code"] != 0 or row["exit_code"] != 0 or row["max_rss_bytes"] is None:
            missing.append(f"{case['id']} rebuild")
        rebuilds.append(row)

    # Cold builds in new target directories, removed afterwards.
    cold = []
    for case_id in COLD_CASES:
        root = args.products / key(case_id)
        for profile in ("debug", "release"):
            target = out / f"cold-target-{key(case_id)}-{profile}"
            command = [cargo, "build", "--locked", "--offline", "-q"] + (["--release"] if profile == "release" else [])
            row = {"id": case_id, "profile": profile,
                   **timed(command, root, {**env, "CARGO_TARGET_DIR": str(target)},
                           logs / f"cold-{key(case_id)}-{profile}.log")}
            binary = target / profile / "som_app"
            row["binary_bytes"] = binary.stat().st_size if binary.is_file() else None
            if row["exit_code"] != 0 or row["max_rss_bytes"] is None or row["binary_bytes"] is None:
                missing.append(f"{case_id} cold {profile} build")
            shutil.rmtree(target, ignore_errors=True)
            cold.append(row)

    build = json.loads((args.candidate / "build.json").read_text())
    report = {
        "version": "som.rust-output-perf-observations.v1",
        "note": "Observations only. No measurement has a limit. max_rss_bytes is the largest single process "
                "/usr/bin/time -l saw (rusage of the children), not a sum over the process tree.",
        "base_url": base_url,
        "machine": {"uname": " ".join(os.uname()), "cpus": os.cpu_count()},
        "candidate_build": {"commands": [{k: row[k] for k in ("command", "exit_code", "seconds", "max_rss_bytes")}
                                         for row in build["commands"]],
                            "binary_bytes": build.get("binary_bytes")},
        "summary": {
            "cases": len(cases),
            "rust_compile_seconds": stats([r["rust_compile"]["seconds"] for r in service]),
            "rust_assemble_seconds": stats([r["rust_assemble"]["seconds"] for r in service if "rust_assemble" in r]),
            "python_compile_seconds": stats([r["python_compile"]["seconds"] for r in service]),
            "python_assemble_seconds": stats([r["python_assemble"]["seconds"] for r in service if "python_assemble" in r]),
            "rust_bundle_response_bytes": stats([r["rust_compile"]["response_bytes"] for r in service]),
            "rust_product_response_bytes": stats([r["rust_assemble"]["response_bytes"] for r in service if "rust_assemble" in r]),
            "product_files": stats([r["files"] for r in files]),
            "product_bytes": stats([r["bytes"] for r in files]),
            "product_rs_bytes": stats([r["rs_bytes"] for r in files]),
            "product_rs_lines": stats([r["rs_lines"] for r in files]),
            "app_rebuild_seconds": stats([r["seconds"] for r in rebuilds if r["exit_code"] == 0]),
            "app_rebuild_max_rss_bytes": stats([r["max_rss_bytes"] for r in rebuilds if r["max_rss_bytes"]]),
            "cold_builds": cold,
        },
        "missing": missing,
        "complete": not missing and len(files) == len(rebuilds) == len(service) == len(cases),
        "service": service, "products": files, "rebuilds": rebuilds,
    }
    (out / "perf.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({"complete": report["complete"], "missing": missing[:10], "cases": len(cases),
                      "cold": [(r["id"], r["profile"], round(r["seconds"], 1), r["max_rss_bytes"]) for r in cold]}))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
