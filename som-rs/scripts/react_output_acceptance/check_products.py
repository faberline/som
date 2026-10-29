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

usage: check_products.py --url URL --manifest MANIFEST --out DIR [--cache DIR | --qa-profile FILE] [--only ID ...] [--cleanup-runtime]
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import re
import signal
import shutil
import stat
import subprocess
import sys
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REACT_TESTS = SOM_RS / "tests" / "react-output-v1"
if str(REACT_TESTS) not in sys.path:
    sys.path.insert(0, str(REACT_TESTS))

def load_pinned_retained(repo: pathlib.Path):
    path = repo / 'som-rs/scripts/react_output_acceptance/retained_evidence.py'
    if (not repo.is_absolute() or repo.resolve() != repo or any(part.is_symlink() for part in (path, *path.parents))
            or 'retained_evidence' in sys.modules):
        raise ValueError('retained verifier path or preload differs')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise ValueError('retained verifier source differs')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
    finally:
        os.close(fd)
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != '7ec396988cc2e9704c228c05cb3a7cd7a51c87d0fc6ea26c1ffa1455209d75bc':
        raise ValueError('retained verifier source differs')
    module = types.ModuleType('retained_evidence')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    sys.modules['retained_evidence'] = module
    return module

LINUX_QA_CLI = any(arg in ('--qa-profile', '--api-profile') or arg.startswith(('--qa-profile=', '--api-profile=')) for arg in sys.argv[1:])
if LINUX_QA_CLI:
    REPO = SOM_RS.parent
    CONTRACT = SOM_RS / 'contracts/react-output-v1'
    retained_evidence = load_pinned_retained(REPO)
    check_contract = None
else:
    import check_contract  # type: ignore[import-not-found]  # noqa: E402
    from common import CONTRACT, REPO  # type: ignore[import-not-found]  # noqa: E402
    import retained_evidence

PYTHON = str(SOM_RS / "runtime" / "acceptance-m3-venv" / "bin" / "python")
FORBIDDEN_PORTS = (8765, 18765)
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EXPECTED_PRODUCTS = 129
# Activation remains blocked until the shared receipt verifier is authenticated
# before import and the live-input evidence and independent audit are reviewed.
API_BOOTSTRAP_REVIEWED = False


def frozen_acceptance_groups(repo: pathlib.Path) -> dict[str, str]:
    """Read named cases and their groups from the sealed contract inventory."""
    rel = "som-rs/contracts/react-output-v1/case-inventory.json"
    files = retained_evidence.sealed_files(repo)
    expected = files.get(rel)
    retained_evidence.need(isinstance(expected, str), "QA inventory is not sealed")
    path = retained_evidence.physical_repo_path(repo, rel)
    raw = retained_evidence.read_physical_bytes(path, 8 * 1024 * 1024)
    retained_evidence.need(hashlib.sha256(raw).hexdigest() == expected, "QA inventory differs from receipt")
    inventory = retained_evidence.parse_json(raw)
    retained_evidence.need(isinstance(inventory, dict) and inventory.get("version") == "som.react-output-case-inventory.v1", "QA inventory format differs")
    groups = ("public_examples", "version_positive_fixtures", "coverage_cases", "name_cases",
              "public_products_in_scope", "legacy_in_scope")
    cases: dict[str, str] = {}
    for group in groups:
        rows = inventory.get(group)
        retained_evidence.need(isinstance(rows, list), "QA inventory group missing")
        for row in rows:
            retained_evidence.need(isinstance(row, dict), "QA inventory row invalid")
            if group in groups[:3]:
                retained_evidence.need(isinstance(row.get("react"), str), "QA inventory scope missing")
                if row["react"] != "in_scope":
                    continue
            cid = row.get("id")
            retained_evidence.need(isinstance(cid, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", cid) is not None
                                   and cid not in (".", ".."), "QA inventory case id invalid")
            retained_evidence.need(cid not in cases, "QA frozen inventory repeats a case")
            cases[cid] = group
    retained_evidence.need(len(cases) == EXPECTED_PRODUCTS,
                           "QA frozen inventory must contain 129 unique cases")
    return cases


def validate_qa_manifest(entries: Any, expected: dict[str, str]) -> None:
    retained_evidence.need(isinstance(entries, list) and all(isinstance(row, dict) for row in entries),
                           "QA manifest products invalid")
    ids = [row.get("id") for row in entries]
    retained_evidence.need(all(isinstance(cid, str) for cid in ids), "QA manifest case id invalid")
    retained_evidence.need(len(ids) == EXPECTED_PRODUCTS and len(set(ids)) == EXPECTED_PRODUCTS
                           and set(ids) == set(expected), "QA manifest differs from frozen 129 named cases")
    retained_evidence.need(all(row.get("group") == expected[row["id"]] for row in entries),
                           "QA manifest case group differs from frozen inventory")


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_api_recorder(repo: pathlib.Path):
    """Authenticate the recorder before executing any Linux QA helper."""
    retained_evidence.need(API_BOOTSTRAP_REVIEWED, 'Linux API bootstrap is not reviewed')
    authority_rel = 'som-rs/contracts/react-output-v1/linux-api-qa-authority.json'
    files = retained_evidence.sealed_files(repo)
    path = retained_evidence.physical_repo_path(repo, authority_rel)
    raw = retained_evidence.read_physical_bytes(path, 1024 * 1024)
    retained_evidence.need(files.get(authority_rel) == hashlib.sha256(raw).hexdigest(), 'API authority differs')
    authority = retained_evidence.parse_json(raw)
    retained_evidence.need(isinstance(authority, dict) and authority.get('version') == 'som.react-linux-api-authority.v1'
                           and isinstance(authority.get('source_sha256'), dict), 'API authority format differs')
    rel = 'som-rs/scripts/react_output_acceptance/record_api.py'
    path = retained_evidence.physical_repo_path(repo, rel)
    source = retained_evidence.read_physical_bytes(path, 1024 * 1024)
    retained_evidence.need(authority['source_sha256'].get(rel) == hashlib.sha256(source).hexdigest(), 'API recorder source differs')
    retained_evidence.need('record_api' not in sys.modules, 'preloaded API recorder refused')
    module = types.ModuleType('record_api')
    module.__file__ = str(path)
    exec(compile(source, str(path), 'exec'), module.__dict__)
    sys.modules['record_api'] = module
    return module


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


def step(argv: list[str], log: pathlib.Path, python: str | None = None) -> int:
    with log.open("w") as out:
        return subprocess.run([python or PYTHON, *argv], stdout=out, stderr=subprocess.STDOUT, timeout=3600,
                              check=False).returncode


def gate_invocation(argv: list[str], log: pathlib.Path, destination: pathlib.Path, python: str | None = None) -> dict[str, Any]:
    """Retain the actual direct child invocation; unknown wait cannot pass."""
    actual = [python or PYTHON, *argv]
    cwd = str(pathlib.Path.cwd().resolve())
    outcome, code, failure = "unverified", None, None
    with log.open("w") as output:
        try:
            result = subprocess.run(actual, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=3600, check=False)
            if type(result.returncode) is int:
                outcome, code = "exited", result.returncode
            else:
                failure = "missing-exit-code"
        except subprocess.TimeoutExpired:
            outcome, failure = "timed-out", "timeout"
        except OSError as exc:
            outcome, failure = "process-error", type(exc).__name__
    record = {"version": "som.react-gates-invocation.v1", "argv": actual, "cwd": cwd,
              "outcome": outcome, "exit_code": code,
              "wait": {"state": "completed" if outcome == "exited" else "unverified", "exit_code": code},
              "log": log.name, "log_sha256": retained_evidence.digest(log), "failure": failure}
    retained_evidence.write_json(destination, record)
    return record


def api_invocation(argv: list[str], log: pathlib.Path, destination: pathlib.Path, python: str,
                   timeout: float = 3600, grace: float = 60) -> dict[str, Any]:
    actual = [python, *argv]
    cwd = str(pathlib.Path.cwd().resolve())
    outcome, code, failure = 'unverified', None, None
    cleanup = None
    with log.open('w') as output:
        try:
            child = subprocess.Popen(actual, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                result = child.wait(timeout=timeout)
                if type(result) is int:
                    outcome, code = 'exited', result
                else:
                    failure = 'missing-exit-code'
            except subprocess.TimeoutExpired:
                # Interrupt only the owned recorder, so its existing finally
                # blocks can stop its verified Python and PostgreSQL owners.
                outcome, failure = 'timed-out', 'timeout'
                try:
                    child.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
                try:
                    code = child.wait(timeout=grace)
                    cleanup = {'method': 'interrupt-recorder', 'recorder_wait': 'completed',
                               'exit_code': code, 'owned_services': 'requires-raw-proof'}
                except subprocess.TimeoutExpired:
                    child.kill()
                    try:
                        code = child.wait(timeout=10)
                        cleanup = {'method': 'interrupt-then-kill-recorder', 'recorder_wait': 'completed',
                                   'exit_code': code, 'owned_services': 'unverified'}
                    except subprocess.TimeoutExpired:
                        cleanup = {'method': 'interrupt-then-kill-recorder', 'recorder_wait': 'unverified',
                                   'exit_code': None, 'owned_services': 'unverified'}
        except subprocess.TimeoutExpired:
            outcome, failure = 'timed-out', 'timeout'
        except OSError as exc:
            outcome, failure = 'process-error', type(exc).__name__
    record = {'version': 'som.react-api-invocation.v1', 'argv': actual, 'cwd': cwd,
              'outcome': outcome, 'exit_code': code,
              'wait': {'state': 'completed' if type(code) is int else 'unverified', 'exit_code': code},
              'log': log.name, 'log_sha256': retained_evidence.digest(log), 'failure': failure}
    if cleanup is not None:
        record['timeout_cleanup'] = cleanup
    retained_evidence.write_json(destination, record)
    return record


def cache_snapshot(cache: pathlib.Path, destination: pathlib.Path) -> dict[str, Any]:
    """Retain a per-file snapshot around the unchanged frozen product gates."""
    record: dict[str, Any] = {"version": "som.m6-cache-snapshot.v1", "status": "failed", "files": {}}
    try:
        if cache.is_symlink() or not cache.is_dir():
            raise ValueError("cache must be a physical directory")
        files = {}
        for path in sorted(cache.rglob("*")):
            if path.is_symlink():
                raise ValueError("the frozen cache admits no symlinks")
            if path.is_file():
                files[path.relative_to(cache).as_posix()] = sha(path)
            elif not path.is_dir():
                raise ValueError("cache has a special file")
        digest = hashlib.sha256()
        for name, value in files.items():
            digest.update(f"{name}\0{value}\n".encode())
        record.update(status="passed", files=files, tree_sha256=digest.hexdigest(), file_count=len(files), symlink_count=0)
    except (OSError, ValueError) as error:
        record["error_type"] = type(error).__name__
    with destination.open("x") as stream:
        json.dump(record, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return record


def gate_argv(product: pathlib.Path, work: pathlib.Path, cache: pathlib.Path,
              qa_profile: pathlib.Path | None = None) -> list[str]:
    """Use the sealed QA profile or the legacy explicit cache, never both."""
    argv = [str(REACT_TESTS / "run_gates.py"), str(product), str(work)]
    return argv + (["--qa-profile", str(qa_profile)] if qa_profile is not None else ["--cache", str(cache)])


def check_case(base_url: str, entry: dict[str, Any], out: pathlib.Path, cache: pathlib.Path,
               api: dict[str, Any], qa: dict[str, Any] | None = None,
               api_qa: dict[str, Any] | None = None) -> dict[str, Any]:
    qa_profile = qa["profile_path"] if qa is not None else None
    cid = entry["id"]
    if qa is not None:
        retained_evidence.need(isinstance(cid, str) and cid not in ('', '.', '..')
                               and pathlib.Path(cid).name == cid and '/' not in cid and '\\' not in cid,
                               "QA case identifier is unsafe")
    work = out / cid
    if qa is not None:
        retained_evidence.physical_repo_path(out, work)
    work.mkdir()
    problems: list[str] = []
    product = pathlib.Path(entry["product"])
    codes: dict[str, int | None] = {"gates": None, "api_contract": None}
    expected_cache = None
    if qa_profile is not None:
        expected_path = out.parent / "qa-profile-evidence" / "cache-inventory.json"
        expected_cache = retained_evidence.read_json(expected_path)
        full_before = retained_evidence.tree_inventory(cache)
        retained_evidence.write_json(work / "cache-evidence-before.json", full_before)
        retained_evidence.need(retained_evidence.same_value(full_before, expected_cache), "QA cache before differs from retained inventory")
    cache_before = cache_snapshot(cache, work / "cache-before.json")
    if qa_profile is None:
        codes["gates"] = step(gate_argv(product, work / "gates", cache, qa_profile), work / "gates.log")
        invocation = None
    else:
        invocation = gate_invocation(gate_argv(product, work / "gates", cache, qa_profile),
                                     work / "gates.log", work / "gates-invocation.json", str(api_qa['python']))
        codes["gates"] = invocation["exit_code"]
    cache_after = cache_snapshot(cache, work / "cache-after.json")
    if qa_profile is not None:
        full_after = retained_evidence.tree_inventory(cache)
        retained_evidence.write_json(work / "cache-evidence-after.json", full_after)
        retained_evidence.need(retained_evidence.same_value(full_after, expected_cache)
                               and retained_evidence.same_value(full_after, full_before), "QA cache after differs")
        retained_evidence.need(invocation is not None and invocation["outcome"] == "exited"
                               and invocation["wait"]["state"] == "completed", "QA gate wait is unverified")
    if cache_before.get("status") != "passed" or cache_after.get("status") != "passed" or cache_before != cache_after:
        problems.append("raw cache file snapshots differ or are missing")
    gates_path = work / "gates" / "gates.json"
    if qa is None:
        gates = json.loads(gates_path.read_text()) if gates_path.is_file() else None
    else:
        retained_evidence.physical_repo_path(out, gates_path)
        gates = retained_evidence.read_json(gates_path)
        retained_evidence.need(isinstance(gates, dict), "QA gate report is not an object")
        expected_identity = {"sha256": qa["profile_sha256"], "node": str(qa["node"]),
                             "npm_cli": str(qa["npm_cli"]), "cache": str(qa["cache"])}
        reported = gates.get("qa_profile")
        retained_evidence.need(isinstance(reported, dict) and all(reported.get(k) == v for k, v in expected_identity.items())
                               and reported.get("recheck_passed") is True and "failure_reason" in reported
                               and reported["failure_reason"] is None, "QA gate profile identity differs")
        expected_argv = [str(api_qa['python']), *gate_argv(product, work / "gates", cache, qa_profile)]
        retained_evidence.need(invocation["argv"] == expected_argv and invocation["cwd"] == str(pathlib.Path.cwd().resolve())
                               and invocation["exit_code"] == codes["gates"] and invocation["wait"]["exit_code"] == codes["gates"], "QA gate invocation differs")
    if codes["gates"] != 0 or gates is None or gates.get("passed") is not True:
        problems.append(f"run_gates.py exit {codes['gates']}")

    python_dir = work / "python"
    python_dir.mkdir()
    python_problem = python_product(base_url, entry, python_dir)
    report = None
    if python_problem is not None:
        problems.append(python_problem)
    else:
        api_args = [str(HERE / "record_api.py"), str(python_dir), str(product),
                    str(work / "api"), "--raw-out", str(work / "api-raw")]
        if api_qa is not None:
            api_args += ['--api-profile', str(api_qa['context_path']), '--qa-profile', str(qa_profile),
                         '--full-validation', str(out.parent / 'api-qa-evidence/full-before.json')]
        if api_qa is None:
            codes["api_contract"] = step(api_args, work / "api.log")
        else:
            api_call = api_invocation(api_args, work / 'api.log', work / 'api-invocation.json', str(api_qa['python']))
            retained_evidence.need(api_call['outcome'] == 'exited' and api_call['wait']['state'] == 'completed',
                                   'API recorder wait is unverified')
            codes["api_contract"] = api_call['exit_code']
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
        **({"qa_profile_sha256": qa["profile_sha256"]} if qa is not None else {}),
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
    parser.add_argument("--cache", type=pathlib.Path, default=None)
    parser.add_argument("--qa-profile", type=pathlib.Path, default=None,
                        help="Use a sealed Linux QA profile for every product gate.")
    parser.add_argument("--api-profile", type=pathlib.Path, default=None,
                        help="Use the sealed live Linux API QA context.")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--cleanup-runtime", action="store_true",
                        help="Remove only each completed case's node_modules and verified stopped PostgreSQL data")
    args = parser.parse_args()
    qa = None
    api_qa = api_context_module = None
    qa_out = None
    if args.qa_profile is not None:
        if args.cache is not None or args.api_profile is None:
            parser.error("Linux QA requires --api-profile and forbids --cache")
        try:
            qa_out = retained_evidence.physical_repo_path(REPO, args.out)
            linux_qa_profile = retained_evidence.sealed_profile_helper(REPO)
            qa = linux_qa_profile.validate_profile(args.qa_profile)
            cache = qa["cache"]
            recorder = load_api_recorder(REPO)
            api_context_module, _, _ = recorder.load_linux_sources(REPO, bound_retained=retained_evidence)
            api_qa = api_context_module.validate(args.api_profile, REPO, bound_node_module=linux_qa_profile)
            retained_evidence.need(api_qa['node_profile_path'] == qa['profile_path']
                                   and api_qa['profile_sha256'] == qa['profile_sha256']
                                   and str(api_qa['node']) == str(qa['node'])
                                   and str(api_qa['npm_cli']) == str(qa['npm_cli'])
                                   and pathlib.Path(api_qa['cache']) == qa['cache'], 'API and gate QA identities differ')
        except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
            print(f"refused: Linux QA profile: {exc}", file=sys.stderr)
            return 2
    else:
        if args.api_profile is not None:
            parser.error('--api-profile requires --qa-profile')
        cache = args.cache if args.cache is not None else REPO / toolchain["vendored_cache"]["path"]
    port = urllib.parse.urlsplit(args.url).port
    if port in FORBIDDEN_PORTS:
        print(f"refused: port {port} is not a QA port", file=sys.stderr)
        return 2
    manifest_path = args.manifest.resolve()
    expected_groups = None
    if qa is not None:
        try:
            manifest = retained_evidence.read_json(manifest_path)
            entries = manifest.get("products") if isinstance(manifest, dict) else manifest
            expected_groups = frozen_acceptance_groups(REPO)
            validate_qa_manifest(entries, expected_groups)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"refused: QA product inventory: {exc}", file=sys.stderr)
            return 2
    if args.cleanup_runtime:
        try:
            out = validate_cleanup_root(qa_out if qa_out is not None else args.out)
        except (OSError, ValueError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    else:
        out = qa_out if qa_out is not None else args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    if qa is None:
        manifest = json.loads(manifest_path.read_text())
        entries = manifest["products"] if isinstance(manifest, dict) else manifest
    if args.only:
        entries = [e for e in entries if e["id"] in args.only]
    api = json.loads((CONTRACT / "api-contract.json").read_text())
    rows = []
    started = time.time()
    for index, entry in enumerate(entries, 1):
        t0 = time.time()
        if qa is None:
            row = check_case(args.url, entry, out, cache.resolve(), api)
        else:
            api_context_module.recheck_case(api_qa)
            row = check_case(args.url, entry, out, cache.resolve(), api, qa, api_qa)
            api_context_module.recheck_case(api_qa)
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
    qa_recheck_problem = None
    if qa is not None:
        try:
            linux_qa_profile.recheck(qa)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            qa_recheck_problem = f"{type(exc).__name__}: {exc}"
    complete = args.only is None and len(rows) == EXPECTED_PRODUCTS
    if qa is not None:
        row_ids = [row.get("id") for row in rows]
        complete = (complete and all(isinstance(cid, str) for cid in row_ids)
                    and len(set(row_ids)) == EXPECTED_PRODUCTS and set(row_ids) == set(expected_groups)
                    and all(row.get("group") == expected_groups[row["id"]] for row in rows))
    report = {
        "version": "som.react-output-product-gates.v1",
        "issue": 129,
        "url": args.url,
        "manifest": str(manifest_path),
        "manifest_sha256": sha(manifest_path),
        "vendored_cache": str(cache.resolve()),
        **({"qa_profile": {"path": str(qa["profile_path"]), "sha256": qa["profile_sha256"]}} if qa is not None else {}),
        **({"qa_profile_final_recheck": {"passed": qa_recheck_problem is None,
                                          "failure_reason": qa_recheck_problem}} if qa is not None else {}),
        "case_count": len(rows),
        "complete": complete,
        "failed": [r["id"] for r in rows if not r["passed"]],
        "seconds": round(time.time() - started, 1),
        "cases": rows,
    }
    report["passed"] = complete and not report["failed"] and qa_recheck_problem is None
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"cases={len(rows)} failed={len(report['failed'])} complete={complete} passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
