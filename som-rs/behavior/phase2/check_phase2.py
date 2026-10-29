#!/usr/bin/env python3
"""Run the phase 2 behavior suite on every phase 2 case, on the Python or the Rust products.

usage (under som-rs/runtime/acceptance-m3-venv/bin/python):
  check_phase2.py list
  check_phase2.py run --out DIR [--language python | --language rust --qa URL] [--workers N]
                      [--only ID ...] [--resume]
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

The verifiers are the adapted copies in adapted/ (adapt.py). Each worker process owns one
M3Fixture, because the fixture changes os.environ. A product passes when its checker passes and no
file of the assembled product changed.

--language python (the default, issue #52) runs the accepted Python products as PythonProduct, so
every command is the one the accepted runs used.

--language rust (amendment 003) compiles each typed prompt with target rust on the candidate at
--qa and assembles it. A case whose phase2/case-inventory.json result is a refusal (RS12, RS13)
passes when the compile gives exactly that diagnostic: since amendment 004 the compiler-v1
python_name_collisions refusal with the inventory code, status, detail and pointer (an
UNSUPPORTED_TARGET refusal would be the one tests/rust-output-v1/rust_profile_phase2.py finds, and
it finds none). Every other case must give a product; it runs
as RustProduct with the vendored crates of CARGO_HOME and one cargo target directory per worker,
or on the phase 1 suite as hosts.Host.rust_product runs it. The Python product of the run record
is still read and hash-checked, so both languages run on the same prompts.
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
import threading
import time
import traceback
import urllib.error
import urllib.request
from typing import Any, Callable

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent
for path in (SOM_RS / "tests" / "rust-output-v1", SOM_RS / "scripts" / "compiler_acceptance",
             REPO / "som-core" / "src", REPO / "som-core" / "scripts", HERE / "adapted", HERE):
    sys.path.insert(0, str(path))
import qa_harness as qa  # noqa: E402
import rust_profile  # noqa: E402
import rust_profile_phase2  # noqa: E402

LOCAL = REPO / ".local"
LEGACY = LOCAL / "rust-compiler-goal" / "acceptance" / "legacy544" / "run-005"
PUBLIC = LOCAL / "rust-compiler-goal" / "acceptance" / "public-products-003b"
RS = LOCAL / "rust-output-phase2" / "rs-products-001"
FIXTURES = SOM_RS / "tests" / "compiler-v1" / "cnl-positive-fixtures.json"
INVENTORY = SOM_RS / "contracts" / "rust-output-v1" / "phase2" / "case-inventory.json"
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
    if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
        from full_product_context import phase2_cases
        return phase2_cases(checker)
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


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def post(base: str, body: str, route: str) -> tuple[int, bytes]:
    request = urllib.request.Request(base.rstrip("/") + route, data=body.encode(),
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def rust_files(qa_url: str, case: dict, prompt: dict, row: dict) -> dict[str, str] | None:
    """The Rust product of the typed prompt, or None for the expected refusal. The outcome must be
    the one phase2/case-inventory.json records for the case."""
    expected = {r["id"]: r["phase2_expected"] for r in read(INVENTORY)["rows"]}[case["id"]]
    if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
        from full_product_context import recorded_transport, recorded_files
        if recorded_transport():
            if expected["result"] == "refusal":
                raise RuntimeError("fresh refusal gate needs the same-candidate binary carrier")
            files = recorded_files(prompt, "rust", case["id"])
            row["rust"] = {"transport": "exact-recorded-assembled-input",
                           "files_sha256": qa.sha(files), "fresh_compile_performed": False}
            return files
    status, raw = post(qa_url, dump({"format": "som.prompt-json.v1", "target": "rust", "prompt": prompt}),
                       "/v1/compile")
    record: dict[str, Any] = {"expected": expected, "compile_status": status,
                              "compile_sha256": hashlib.sha256(raw).hexdigest()}
    row["rust"] = record
    if expected["result"] == "refusal":
        if "code" in expected:
            # A compiler-v1 refusal for every target (amendment 004).
            wanted = {"code": expected["code"], "detail": expected["detail"],
                      "prompt_pointer": "/prompt" + expected["pointer"]}
            wanted_status = expected["http_status"]
        else:
            found = rust_profile_phase2.check(prompt)
            if found is None or found["rule"] != expected["rule"]:
                raise RuntimeError("rust_profile_phase2 does not give the inventory refusal")
            wanted = {"code": "UNSUPPORTED_TARGET", "detail": found["cause"],
                      "prompt_pointer": "/prompt" + found["pointer"]}
            wanted_status = 422
        reply = json.loads(raw)
        diagnostics = reply.get("diagnostics") or [{}]
        record["refusal"] = {"code": reply.get("code"), "detail": reply.get("detail"),
                             "prompt_pointer": diagnostics[0].get("prompt_pointer")}
        if status != wanted_status or record["refusal"] != wanted or len(diagnostics) != 1:
            raise RuntimeError(f"the Rust compile did not give the {expected['rule']} refusal")
        return None
    if status != 200:
        raise RuntimeError(f"rust compile returned {status}: {raw[:500]!r}")
    bundle = json.loads(raw)["bundle"]
    status, raw = post(qa_url, dump({"bundle": bundle}), "/v1/assemble")
    if status != 200:
        raise RuntimeError(f"rust assemble returned {status}: {raw[:500]!r}")
    files = json.loads(raw)["files"]
    record.update(bundle_sha256=qa.sha(bundle), files_sha256=qa.sha(files), files=len(files))
    return files


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


def fixture_case(out: pathlib.Path, case: dict, fixture: dict, language: str = "python",
                 qa_url: str | None = None, target_dir: pathlib.Path | None = None) -> dict:
    """One verify_all or flow case under this worker's fixture."""
    from phase2_host import PythonProduct, RustProduct, register, unregister
    started = time.monotonic()
    row: dict[str, Any] = {**public_row(case), "language": language, "passed": False, "problems": []}
    outcome: Any = None
    product = out / "work" / key(case["id"]) / "product"
    try:
        if language == "python" and os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
            from full_product_context import current_refusal
            negative = current_refusal(case["id"], "python")
            if negative is not None:
                row.update(passed=True, outcome_role="current-compiler-refusal-no-product", product_executed=False,
                           current_refusal=negative)
                return finish(out, case, row, negative, started)
        prompt, files = load(case)
        if language == "rust":
            files = rust_files(qa_url, case, prompt, row)
            if files is None:
                row["passed"] = True
                return finish(out, case, row, row["rust"], started)
        if product.parent.exists():
            shutil.rmtree(product.parent)
        product.mkdir(parents=True)
        write_product(product, files)
        host = (PythonProduct(product) if language == "python"
                else RustProduct(product, target_dir, threading.Lock()))
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
    binary = product / "som_app"
    if language == "rust" and binary.is_file() and not binary.is_symlink():
        binary.unlink()
    return finish(out, case, row, outcome, started)


def suite_case(out: pathlib.Path, case: dict, host: Any, language: str = "python") -> dict:
    """One v1 to v5 case on the phase 1 suite."""
    import suite
    started = time.monotonic()
    row: dict[str, Any] = {**public_row(case), "language": language, "passed": False, "problems": []}
    outcome: Any = None
    try:
        if language == "python" and os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
            from full_product_context import current_refusal
            negative = current_refusal(case["id"], "python")
            if negative is not None:
                row.update(passed=True, outcome_role="current-compiler-refusal-no-product", product_executed=False,
                           current_refusal=negative)
                return finish(out, case, row, negative, started)
        prompt, files = load(case)
        if language == "rust":
            files = rust_files(host.qa, case, prompt, row)
            if files is None:
                row["passed"] = True
                return finish(out, case, row, row["rust"], started)
        name = ("py_" if language == "python" else "rs_") + key(case["id"]).lower()
        product = host.python_product if language == "python" else host.rust_product
        with product(name, prompt, files) as base:
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


def worker(out: pathlib.Path, index: int, claims: str, ids: list[str], language: str = "python",
           qa_url: str | None = None) -> int:
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
                host = hosts.Host(qa_url or "http://127.0.0.1:1", pg, work / "products", work / "target",
                                  sys.executable)
                host.ports = ports
                for case in todo:
                    if claim(out, claims, case):
                        suite_case(out, case, host, language)
        return 0
    from m3_local_fixture import M3Fixture
    with M3Fixture(out / "fixtures" / f"w{index}-{claims}") as fixture:
        for case in todo:
            if claim(out, claims, case):
                fixture_case(out, case, fixture, language, qa_url, out / "target" / f"w{index}")
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
    if (args.language == "rust") != bool(args.qa):
        raise SystemExit("--qa is required with --language rust, and only then")
    for name in ("cases", "work", "fixtures"):
        (out / name).mkdir(parents=True, exist_ok=True)
    settings = {"language": args.language, "qa": args.qa}
    if (out / "run.json").exists():
        if read(out / "run.json") != settings:
            raise SystemExit("--resume must keep the language and the candidate of the run")
    else:
        (out / "run.json").write_text(json.dumps(settings, indent=1) + "\n")
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
                "--index", str(index), "--claims", claims, "--ids", json.dumps(ids),
                "--language", args.language, *(["--qa", args.qa] if args.qa else [])]
        if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
            from full_product_context import tool_command
            argv = tool_command(pathlib.Path(__file__).resolve(), argv[2:])
        processes.append(subprocess.Popen(argv, stdout=log, stderr=log, env=env))
    codes = [p.wait() for p in processes]
    print("worker exit codes", codes, flush=True)
    if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
        from full_product_context import phase2_result
        summarize(out)
        return phase2_result(out, every, args.only, codes)
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
    settings = read(out / "run.json") if (out / "run.json").exists() else {"language": "python", "qa": None}
    summary = {
        "version": f"som.rust-output-phase2-{settings['language']}-behavior.v1",
        "language": settings["language"], "qa": settings["qa"],
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
    p.add_argument("--language", choices=("python", "rust"), default="python")
    p.add_argument("--qa")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--only", nargs="*")
    p.add_argument("--resume", action="store_true")
    p = sub.add_parser("worker")
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--claims", required=True)
    p.add_argument("--ids", required=True)
    p.add_argument("--language", choices=("python", "rust"), default="python")
    p.add_argument("--qa")
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
    if args.mode in ("run", "worker") and os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
        from full_product_context import observer_python
        if pathlib.Path(sys.executable).absolute() != observer_python():
            raise SystemExit("run with the admitted hosted observer interpreter")
    elif args.mode in ("run", "worker") and pathlib.Path(sys.prefix).resolve() != VENV.resolve():
        raise SystemExit(f"run this with {VENV}/bin/python, the pinned acceptance environment")
    if args.mode == "run":
        return run(args)
    if args.mode == "worker":
        return worker(args.out.resolve(), args.index, args.claims, json.loads(args.ids), args.language,
                      args.qa)
    return summarize(args.out.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
