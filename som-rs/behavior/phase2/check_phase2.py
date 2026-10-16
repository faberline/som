#!/usr/bin/env python3
"""Run the phase 2 behavior suite on the accepted Python products of every phase 2 case (issue #52).

usage (under som-rs/runtime/acceptance-m3-venv/bin/python):
  check_phase2.py list
  check_phase2.py run --out DIR [--workers N] [--only ID ...] [--resume]
  check_phase2.py summarize --out DIR

A phase 2 case is a case the phase 1 Rust profile refuses: 444 legacy jobs of run-005 (legacy100
is excluded), the refused public products of public-products-003b and the RS01 to RS22 products of
rs-products-001. Every product is read from its frozen run output, its prompt and product hashes
are checked against the record of that run, and it is written to a new directory under --out.

Dispatch follows the accepted runs:
- legacy214: verify_composed_services.verify_all, standard when the group is services14;
- quality218 and supplement12: verify_flow_service.verify, passing on full_m2_acceptance;
- other som.prompt.v7: verify_flow_service.verify;
- other som.prompt.v6 and som.prompt.v5 with capabilities: verify_all, standard;
- other v1 to v5: the phase 1 HTTP suite (som-rs/behavior/suite.py).

The verifiers are the adapted copies in adapted/ (adapt.py); the products run as PythonProduct, so
every command is the one the accepted runs used. Each worker process owns one M3Fixture, because
the fixture changes os.environ. A product passes when its checker passes and no file of the
assembled product changed.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Callable

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent
for path in (SOM_RS / "tests" / "rust-output-v1", SOM_RS / "scripts" / "compiler_acceptance",
             REPO / "som-core" / "src", REPO / "som-core" / "scripts", HERE / "adapted", HERE):
    sys.path.insert(0, str(path))
import qa_harness as qa  # noqa: E402
import rust_profile  # noqa: E402

LOCAL = REPO / ".local"
LEGACY = LOCAL / "rust-compiler-goal" / "acceptance" / "legacy544" / "run-005"
PUBLIC = LOCAL / "rust-compiler-goal" / "acceptance" / "public-products-003b"
RS = LOCAL / "rust-output-phase2" / "rs-products-001"
FIXTURES = SOM_RS / "tests" / "compiler-v1" / "cnl-positive-fixtures.json"
SKIP_DIRS = {".venv", "check-results", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXPECTED = {"legacy": 444, "public": 118, "rs": 22}
# PostgreSQL 17 on macOS refuses to start without a valid LC_ALL.
VENV = SOM_RS / "runtime" / "acceptance-m3-venv"
WORKER_ENV = {"UV_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "en_US.UTF-8"}


def key(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def read(path: pathlib.Path) -> Any:
    return json.loads(path.read_text())


def checker(case: dict) -> tuple[str, bool]:
    family, version, prompt_caps = case["family"], case["prompt_version"], case["capabilities"]
    if family == "legacy214":
        return "verify_all", case["group"] == "services14"
    if family in ("quality218", "supplement12") or version == "som.prompt.v7":
        return "flow", True
    if version == "som.prompt.v6" or (version == "som.prompt.v5" and prompt_caps):
        return "verify_all", True
    return "phase1_suite", True


def cases() -> list[dict]:
    """Every phase 2 case, with loaders for its prompt and files."""
    out: list[dict] = []

    def add(**row: Any) -> None:
        prompt = row["load_prompt"]()
        row.update(prompt_version=prompt["version"], capabilities=bool(prompt.get("capabilities")))
        row["checker"], row["standard"] = checker(row)
        out.append(row)

    records = read(LEGACY / "result.json")["records"]
    for index, record in enumerate(records):
        if record["family"] == "legacy100":
            continue
        job = LEGACY / f"{index:04d}"
        add(id=record["id"], source="legacy544/run-005", position=index, family=record["family"],
            group=record["group"], prompt_sha256=record["prompt_sha256"],
            product_sha256=record["product_sha256"],
            load_prompt=lambda job=job: read(job / "variant.bundle.json")["prompt"],
            load_files=lambda job=job: read(job / "assemble.response.json")["body"]["files"])

    fixtures = read(FIXTURES)["cases"]
    for directory in sorted(PUBLIC.glob("[0-9][0-9][0-9]")):
        prompt = read(directory / "prompt.json")
        if rust_profile.check(prompt) is None:
            continue
        result = read(directory / "result.json")
        position = int(directory.name)
        if position < len(fixtures):
            fixture = fixtures[position]
            if fixture["id"] != result["id"] or fixture["prompt_sha256"] != qa.sha(prompt):
                raise SystemExit(f"public {directory.name}: differs from the CNL fixture list")
            group = fixture["category"]
        else:
            group = "public_example"
        add(id=result["id"], source="public-products-003b", position=position, family="public",
            group=group, prompt_sha256=qa.sha(prompt), product_sha256=result["product_sha256"],
            load_prompt=lambda d=directory: read(d / "prompt.json"),
            load_files=lambda d=directory: read(d / "assemble.response.json")["body"]["files"])

    for row in read(RS / "result.json")["cases"]:
        directory = RS / row["id"]
        add(id=row["id"], source="rust-output-phase2/rs-products-001", position=int(row["id"][2:4]),
            family="rs", group=row["form"], prompt_sha256=row["prompt_sha256"],
            product_sha256=row["product_sha256"],
            load_prompt=lambda d=directory: read(d / "prompt.json"),
            load_files=lambda d=directory: read(d / "files.json"))

    counts = {"legacy": sum(c["family"] not in ("public", "rs") for c in out),
              "public": sum(c["family"] == "public" for c in out),
              "rs": sum(c["family"] == "rs" for c in out)}
    if counts != EXPECTED:
        raise SystemExit(f"phase 2 case counts {counts}, expected {EXPECTED}")
    ids = [c["id"] for c in out]
    if len(set(map(key, ids))) != len(ids):
        raise SystemExit("phase 2 case ids collide")
    return out


def public_row(case: dict) -> dict:
    return {k: v for k, v in case.items() if not callable(v)}


def write_product(root: pathlib.Path, files: dict[str, str]) -> None:
    """The run-005 writer: safe relative paths, no collisions."""
    resolved = root.resolve()
    written: set[pathlib.Path] = set()
    for name, source in files.items():
        relative = pathlib.Path(name)
        if (not relative.parts or relative.is_absolute() or "\\" in name
                or any(part in {"", ".", ".."} for part in relative.parts)):
            raise ValueError(f"unsafe product path: {name!r}")
        target = (resolved / relative).resolve()
        if not target.is_relative_to(resolved) or target in written:
            raise ValueError(f"unsafe or colliding product path: {name!r}")
        written.add(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")


def product_files(root: pathlib.Path) -> set[str]:
    out = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_file() and not SKIP_DIRS & set(relative.parts) and not relative.parts[0].endswith(".egg-info"):
            out.add(relative.as_posix())
    return out


def load(case: dict) -> tuple[dict, dict[str, str]]:
    prompt, files = case["load_prompt"](), case["load_files"]()
    if qa.sha(prompt) != case["prompt_sha256"]:
        raise RuntimeError("prompt hash differs from the run record")
    if qa.sha(files) != case["product_sha256"]:
        raise RuntimeError("product hash differs from the run record")
    if rust_profile.check(prompt) is None:
        raise RuntimeError("the phase 1 profile admits this prompt")
    return prompt, files


def finish(out: pathlib.Path, case: dict, row: dict, outcome: Any, started: float) -> dict:
    row["seconds"] = round(time.monotonic() - started, 1)
    row["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    name = key(case["id"])
    data = json.dumps(outcome, indent=1, default=str, ensure_ascii=False).encode()
    (out / "cases" / f"{name}.outcome.json").write_bytes(data)
    row["outcome_sha256"] = hashlib.sha256(data).hexdigest()
    (out / "cases" / f"{name}.json").write_text(json.dumps(row, indent=1, default=str) + "\n")
    print(f"{case['id']}: {'ok' if row['passed'] else 'FAIL'} {row['seconds']}s", file=sys.stderr, flush=True)
    return row


def fixture_case(out: pathlib.Path, case: dict, fixture: dict) -> dict:
    """One verify_all or flow case under this worker's fixture."""
    from phase2_host import PythonProduct, register, unregister
    started = time.monotonic()
    row: dict[str, Any] = {**public_row(case), "passed": False, "problems": []}
    outcome: Any = None
    product = out / "work" / key(case["id"]) / "product"
    try:
        prompt, files = load(case)
        if product.parent.exists():
            shutil.rmtree(product.parent)
        product.mkdir(parents=True)
        write_product(product, files)
        host = PythonProduct(product)
        register(product, host)
        try:
            if case["checker"] == "verify_all":
                from verify_composed_services import verify_all
                outcome = verify_all(prompt, product, fixture, standard=case["standard"])
                row["checker_passed"] = outcome.get("status") == "passed"
            else:
                from verify_flow_service import verify
                outcome = verify(prompt, product, fixture)
                row["checker_passed"] = bool(outcome.get("full_m2_acceptance"))
        finally:
            unregister(product)
        row["surface_checks"] = host.surface_checks
        changed = sorted(n for n in files if (product / n).read_text(encoding="utf-8") != files[n])
        row["changed_files"] = changed
        row["extra_files"] = sorted(product_files(product) - set(files))
        if not row["checker_passed"]:
            row["problems"].append("checker did not pass")
        if changed:
            row["problems"].append("assembled files changed")
    except Exception as error:  # noqa: BLE001
        row["problems"].append(f"{type(error).__name__}: {error}"[:3000])
        row["traceback"] = traceback.format_exc()[-6000:]
    row["passed"] = not row["problems"]
    venv = product / ".venv"
    if venv.is_dir() and not venv.is_symlink():
        shutil.rmtree(venv)
    return finish(out, case, row, outcome, started)


def suite_case(out: pathlib.Path, case: dict, host: Any) -> dict:
    """One v1 to v5 case on the phase 1 suite."""
    import suite
    started = time.monotonic()
    row: dict[str, Any] = {**public_row(case), "passed": False, "problems": []}
    outcome: Any = None
    try:
        prompt, files = load(case)
        name = "py_" + key(case["id"]).lower()
        with host.python_product(name, prompt, files) as base:
            run = suite.run(prompt, base)
        outcome = {"raw": run.raw, "transcript": run.transcript, "failures": run.failures}
        root = host.work / name
        changed = sorted(n for n in files if (root / n).read_text(encoding="utf-8") != files[n])
        row.update(checker_passed=not run.failures, steps=len(run.transcript),
                   categories=sorted({e["category"] for e in run.transcript}),
                   failures=len(run.failures), first_failures=run.failures[:10], changed_files=changed)
        if run.failures:
            row["problems"].append("suite oracle mismatches")
        if changed:
            row["problems"].append("assembled files changed")
    except Exception as error:  # noqa: BLE001
        row["problems"].append(f"{type(error).__name__}: {error}"[:3000])
        row["traceback"] = traceback.format_exc()[-6000:]
    row["passed"] = not row["problems"]
    return finish(out, case, row, outcome, started)


def claim(out: pathlib.Path, claims: str, case: dict) -> bool:
    path = out / claims / key(case["id"])
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        return False
    return True


def worker(out: pathlib.Path, index: int, claims: str, ids: list[str]) -> int:
    wanted = set(ids)
    todo = [c for c in cases() if c["id"] in wanted]
    if index == 0:
        sys.path.insert(0, str(HERE.parent))
        import hosts
        with tempfile.TemporaryDirectory(prefix="som-phase2-", dir=out / "fixtures",
                                         ignore_cleanup_errors=True) as scratch:
            work = pathlib.Path(scratch)
            ports = hosts.Ports()
            with hosts.cluster(work, ports.take()) as pg:
                host = hosts.Host("http://127.0.0.1:1", pg, work / "products", work / "target", sys.executable)
                host.ports = ports
                for case in todo:
                    if claim(out, claims, case):
                        suite_case(out, case, host)
        return 0
    from m3_local_fixture import M3Fixture
    with M3Fixture(out / "fixtures" / f"w{index}-{claims}") as fixture:
        for case in todo:
            if claim(out, claims, case):
                fixture_case(out, case, fixture)
    return 0


def run(args: argparse.Namespace) -> int:
    out = args.out.resolve()
    every = cases()
    if args.only:
        unknown = set(args.only) - {c["id"] for c in every}
        if unknown:
            raise SystemExit(f"unknown ids: {sorted(unknown)}")
    if out.exists() and not args.resume:
        raise SystemExit(f"{out} exists; use a new directory or --resume")
    for name in ("cases", "work", "fixtures"):
        (out / name).mkdir(parents=True, exist_ok=True)
    done = {p.stem for p in (out / "cases").glob("*.json") if not p.name.endswith(".outcome.json")}
    todo = [c for c in every if (not args.only or c["id"] in args.only) and key(c["id"]) not in done]
    claims = "claims-" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    (out / claims).mkdir()
    suite_ids = [c["id"] for c in todo if c["checker"] == "phase1_suite"]
    fixture_ids = [c["id"] for c in todo if c["checker"] != "phase1_suite"]
    env = {**os.environ, **WORKER_ENV}
    plan = []
    if suite_ids:
        plan.append((0, suite_ids))
    for index in range(1, min(args.workers, len(fixture_ids)) + 1):
        plan.append((index, fixture_ids))
    print(f"{len(todo)} cases: {len(suite_ids)} on the phase 1 suite, {len(fixture_ids)} on "
          f"{len(plan) - bool(suite_ids)} fixture workers", flush=True)
    processes = []
    for index, ids in plan:
        log = (out / f"worker-{index}-{claims}.log").open("w")
        argv = [sys.executable, str(pathlib.Path(__file__).resolve()), "worker", "--out", str(out),
                "--index", str(index), "--claims", claims, "--ids", json.dumps(ids)]
        processes.append(subprocess.Popen(argv, stdout=log, stderr=log, env=env))
    codes = [p.wait() for p in processes]
    print("worker exit codes", codes, flush=True)
    return summarize(out) or max(codes, default=0)


def summarize(out: pathlib.Path) -> int:
    every = cases()
    rows = {}
    for case in every:
        path = out / "cases" / f"{key(case['id'])}.json"
        if path.exists():
            rows[case["id"]] = read(path)
    def tally(group: Callable[[dict], str]) -> dict:
        result: dict[str, dict[str, int]] = {}
        for case in every:
            item = result.setdefault(group(case), {"total": 0, "run": 0, "passed": 0})
            item["total"] += 1
            if case["id"] in rows:
                item["run"] += 1
                item["passed"] += bool(rows[case["id"]]["passed"])
        return result
    summary = {
        "version": "som.rust-output-phase2-python-behavior.v1",
        "total": len(every), "run": len(rows), "passed": sum(r["passed"] for r in rows.values()),
        "complete": len(rows) == len(every),
        "failed": sorted(i for i, r in rows.items() if not r["passed"]),
        "missing": sorted(c["id"] for c in every if c["id"] not in rows),
        "by_source": tally(lambda c: c["source"]),
        "by_checker": tally(lambda c: c["checker"] + ("" if c["standard"] else "_subset")),
        "by_version": tally(lambda c: c["prompt_version"]),
        "case_sha256": {i: hashlib.sha256((out / "cases" / f"{key(i)}.json").read_bytes()).hexdigest()
                        for i in sorted(rows)},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("total", "run", "passed", "complete", "failed", "by_checker")},
                     indent=1))
    return 0 if summary["complete"] and summary["passed"] == summary["total"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("list")
    p = sub.add_parser("run")
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--only", nargs="*")
    p.add_argument("--resume", action="store_true")
    p = sub.add_parser("worker")
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--claims", required=True)
    p.add_argument("--ids", required=True)
    p = sub.add_parser("summarize")
    p.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.mode == "list":
        every = cases()
        for case in every:
            print(case["id"], case["source"], case["group"], case["prompt_version"], case["checker"],
                  "" if case["standard"] else "subset")
        print(len(every), "cases", file=sys.stderr)
        return 0
    if args.mode in ("run", "worker") and pathlib.Path(sys.prefix).resolve() != VENV.resolve():
        raise SystemExit(f"run this with {VENV}/bin/python, the pinned acceptance environment")
    if args.mode == "run":
        return run(args)
    if args.mode == "worker":
        return worker(args.out.resolve(), args.index, args.claims, json.loads(args.ids))
    return summarize(args.out.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
