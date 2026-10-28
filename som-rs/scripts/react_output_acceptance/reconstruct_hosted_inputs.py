#!/usr/bin/env python3
"""Rebuild public name prompts and copy recorded Rust inputs on a GitHub runner.

Never synthesize a historical assemble request. Never use a candidate compiler.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

VERSION = "som.m6-hosted-reconstruction.v1"
REFUSAL = "hosted input reconstruction refused"
BASELINE_URL = "http://127.0.0.1:18917"
BASELINE_SHA = "2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c"
NAME_COUNT, PHASE1_COUNT, PHASE2_COUNT, RUST_FILE_COUNT, LEGACY_COUNT = 9, 115, 584, 1170, 544
MAX_FILE_BYTES, MAX_TREE_BYTES = 128 * 1024 * 1024, 1024 * 1024 * 1024
NAME_ROOT = ".local/react-output/review134-names-001"
RUST_SOURCE = ".local/rust-compiler-goal/acceptance/rust-output-055/run-003"
LEGACY_ROOT = ".local/rust-compiler-goal/acceptance/legacy544/run-005"
HEX = re.compile(r"[a-f0-9]{64}\Z")


class ReconstructionError(Exception):
    def __init__(self) -> None:
        super().__init__(REFUSAL)


def need(condition: bool) -> None:
    if not condition:
        raise ReconstructionError()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def strict_json(raw: bytes | str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result)
            result[key] = value
        return result

    def reject_constant(_value):
        raise ReconstructionError()

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="strict")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)


def absolute(value: str | Path) -> Path:
    text = os.fspath(value)
    need(type(text) is str and os.path.isabs(text) and os.path.abspath(text) == text)
    need(str(Path(text)) == text and not text.startswith("//") and "\x00" not in text)
    return Path(text)


def physical(directory: Path) -> None:
    directory = absolute(directory)
    for item in [*reversed(directory.parents), directory]:
        info = item.lstat()
        need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))


def physical_or_missing(directory: Path) -> None:
    directory = absolute(directory)
    for item in [*reversed(directory.parents), directory]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))


def relative(value: str) -> str:
    need(type(value) is str and value and len(value.encode("utf-8")) <= 4096)
    need("\\" not in value and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    need(not value.startswith("/") and str(PurePosixPath(value)) == value)
    need(all(part not in ("", ".", "..") for part in value.split("/")))
    return value


def read_file(filename: Path, maximum: int = MAX_FILE_BYTES) -> bytes:
    filename = absolute(filename)
    physical(filename.parent)
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        need(stat.S_ISREG(before.st_mode) and before.st_size <= maximum)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(maximum + 1)
        after = os.fstat(descriptor)
        need(len(raw) == before.st_size and len(raw) <= maximum)
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
        return raw
    finally:
        os.close(descriptor)


def write_new(filename: Path, raw: bytes, mode: int = 0o600) -> None:
    physical(filename.parent)
    descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        os.fchmod(handle.fileno(), mode)


def make_dirs(directory: Path) -> None:
    try:
        directory.lstat()
    except FileNotFoundError:
        make_dirs(directory.parent)
        directory.mkdir(mode=0o700)
    physical(directory)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ReconstructionError()


def baseline_call(route: str, body: bytes | None = None) -> tuple[int, bytes]:
    need(route in ("/healthz", "/v1/compile"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(BASELINE_URL + route, data=body,
                                    headers={"Content-Type": "application/json"} if body is not None else {},
                                    method="POST" if body is not None else "GET")
    try:
        with opener.open(request, timeout=120) as response:
            raw = response.read(MAX_FILE_BYTES + 1)
            need(len(raw) <= MAX_FILE_BYTES)
            return response.status, raw
    except urllib.error.HTTPError as error:
        with error:
            raw = error.read(MAX_FILE_BYTES + 1)
            need(len(raw) <= MAX_FILE_BYTES)
            return error.code, raw


def git_blob(repo: Path, commit: str, filename: str) -> bytes:
    result = subprocess.run(["git", "--no-pager", "-C", str(repo), "show", f"{commit}:{filename}"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                            check=False, timeout=120,
                            env={key: value for key, value in os.environ.items()
                                 if key not in ("M6_INPUTS_KEYRING", "M6_INPUTS_KEY")})
    need(result.returncode == 0 and type(result.stdout) is bytes and len(result.stdout) <= MAX_FILE_BYTES)
    return result.stdout


def verify_original_requests(repo: Path) -> dict[str, Any]:
    """Bind the existing originals to the approved encrypted package manifests."""
    config = strict_json(read_file(repo / "som-rs/scripts/react_output_acceptance/hosted_inputs.json"))
    need(type(config) is dict and set(config) == {"version", "commit", "index_sha256"})
    need(config["version"] == "som.m6-hosted-inputs.v1")
    need(type(config["commit"]) is str and re.fullmatch(r"[a-f0-9]{40}", config["commit"]) is not None)
    raw = git_blob(repo, config["commit"], "inputs-index.json")
    need(sha(raw) == config["index_sha256"])
    index = strict_json(raw)
    need(type(index) is dict and set(index) == {"version", "packages"}
         and index["version"] == "som.m6-input-index.v1" and type(index["packages"]) is list)
    need(0 < len(index["packages"]) <= 1000)
    expected = {f"{LEGACY_ROOT}/{position:04d}/assemble.request.json" for position in range(LEGACY_COUNT)}
    found: dict[str, str] = {}
    package_names = set()
    for package in index["packages"]:
        need(type(package) is dict and set(package) == {"archive", "manifest", "manifest_sha256"})
        need(type(package["archive"]) is str and re.fullmatch(r"package-[0-9]{3}\.sealed", package["archive"]) is not None)
        need(package["manifest"] == package["archive"][:-7] + ".manifest.json" and package["manifest"] not in package_names)
        package_names.add(package["manifest"])
        manifest_raw = git_blob(repo, config["commit"], package["manifest"])
        need(sha(manifest_raw) == package["manifest_sha256"])
        manifest = strict_json(manifest_raw)
        need(type(manifest) is dict and manifest.get("version") == "som.m6-sealed-inputs-manifest.v1"
             and type(manifest.get("files")) is list)
        for file in manifest["files"]:
            need(type(file) is dict and type(file.get("path")) is str)
            if file["path"] in expected:
                need(file["path"] not in found and type(file.get("sha256")) is str
                     and HEX.fullmatch(file["sha256"]) is not None)
                found[file["path"]] = file["sha256"]
    need(set(found) == expected)
    for filename, expected_sha in found.items():
        need(sha(read_file(repo / filename)) == expected_sha)
    return {"count": len(found), "input_commit": config["commit"], "index_sha256": config["index_sha256"],
            "files_tree_sha256": sha(canonical(found))}


def name_plan(repo: Path) -> list[dict[str, Any]]:
    contract = repo / "som-rs/contracts/react-output-v1"
    toolchain = strict_json(read_file(contract / "toolchain.json"))
    evidence_path = relative(toolchain["reference_verification"]["name_cases_evidence"])
    need(evidence_path.startswith("som-rs/evidence/react-output-v1/name-cases-") and evidence_path.endswith(".json"))
    evidence = strict_json(read_file(repo / evidence_path))
    need(evidence["run"]["work_dir"] == NAME_ROOT)
    cases = strict_json(read_file(contract / "target-examples.json"))["name_cases"]
    expected = {case["id"]: case for case in evidence["cases"]}
    need(len(cases) == len(evidence["cases"]) == len(expected) == NAME_COUNT)
    need({case["id"] for case in cases} == set(expected))
    plan = []
    for case in cases:
        identity = case["id"]
        need(type(identity) is str and re.fullmatch(r"RN[0-9]{2}_[A-Z0-9_]+", identity) is not None)
        need(type(case["python_control_body"]) is str)
        raw = case["python_control_body"].encode("utf-8")
        body = strict_json(raw)
        need(type(body) is dict and "target" not in body)
        need((body.get("format") == "som.cnl.v1" and set(body) == {"format", "source"} and type(body["source"]) is str)
             or (body.get("format") == "som.prompt-json.v1" and set(body) == {"format", "prompt"} and type(body["prompt"]) is dict))
        frozen_sha = expected[identity]["prompt_file_sha256"]
        need(type(frozen_sha) is str and HEX.fullmatch(frozen_sha) is not None)
        destination = repo / NAME_ROOT / identity / "prompt.json"
        physical_or_missing(destination.parent)
        if destination.exists() or destination.is_symlink():
            need(sha(read_file(destination)) == frozen_sha)
        plan.append({"id": identity, "request": raw, "expected_sha256": frozen_sha,
                     "destination": destination})
    return plan


def rust_plan(repo: Path) -> tuple[list[dict[str, Any]], int, int]:
    """Plan a physical copy of the exact 1170 files in the approved source set."""
    source = repo / RUST_SOURCE
    physical(source)
    rows = strict_json(read_file(repo / "som-rs/contracts/rust-output-v1/phase2/case-inventory.json"))["rows"]
    need(type(rows) is list and len(rows) == PHASE2_COUNT)
    ids = [row["id"] for row in rows]
    need(all(type(identity) is str for identity in ids) and len(set(ids)) == PHASE2_COUNT)
    case_keys = {identity: re.sub(r"[^A-Za-z0-9]+", "_", identity).strip("_") for identity in ids}
    need(len(set(case_keys.values())) == PHASE2_COUNT)
    expected = {"rust-products.json", "phase2/summary.json"}
    expected.update(f"phase2/cases/{key}{suffix}" for key in case_keys.values() for suffix in (".json", ".outcome.json"))
    need(len(expected) == RUST_FILE_COUNT)
    observed: dict[str, Path] = {}

    def visit(directory: Path) -> None:
        physical(directory)
        for child in directory.iterdir():
            info = child.lstat()
            need(not stat.S_ISLNK(info.st_mode))
            if stat.S_ISDIR(info.st_mode):
                visit(child)
            else:
                need(stat.S_ISREG(info.st_mode))
                name = child.relative_to(source).as_posix()
                need(name in expected)
                observed[name] = child

    visit(source)
    need(set(observed) == expected)
    files = []
    total_bytes = 0
    for name in sorted(observed):
        raw = read_file(observed[name])
        total_bytes += len(raw)
        need(total_bytes <= MAX_TREE_BYTES)
        file_mode = stat.S_IMODE(observed[name].lstat().st_mode)
        need(file_mode in (0o644, 0o755))
        files.append({"path": name, "source": observed[name], "sha256": sha(raw), "bytes": len(raw), "mode": file_mode})
    phase1 = strict_json(read_file(source / "rust-products.json"))["cases"]
    need(type(phase1) is list and len(phase1) == PHASE1_COUNT)
    need(all(type(case) is dict and type(case.get("id")) is str for case in phase1))
    need(len({case["id"] for case in phase1}) == PHASE1_COUNT)
    summary = strict_json(read_file(source / "phase2/summary.json"))
    need(type(summary.get("case_sha256")) is dict and set(summary["case_sha256"]) == set(ids))
    hashes = {file["path"]: file["sha256"] for file in files}
    need(all(summary["case_sha256"][identity] == hashes[f"phase2/cases/{key}.json"] for identity, key in case_keys.items()))
    return files, len(phase1), len(ids)


def reconstruct(repo: str | Path, baseline: str, out: str | Path) -> dict[str, Any]:
    output = None
    stage = "host"
    receipt: dict[str, Any] = {"version": VERSION, "stage": "input_reconstruction", "status": "failed", "passed": False,
                               "product_suites": "not_run", "legacy_assemble_requests": "not_reconstructed"}
    try:
        need(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted")
        need(baseline == BASELINE_URL)
        repository, requested = absolute(repo), absolute(out)
        physical(repository)
        physical(requested.parent)
        need(requested.is_relative_to(repository / ".local/m6-hosted") and not requested.exists() and not requested.is_symlink())
        need(not any(requested.is_relative_to(repository / f".local/m6-hosted/{name}") for name in ("baseline", "prettier")))
        stage = "source_inputs"
        originals = verify_original_requests(repository)
        names = name_plan(repository)
        files, phase1_count, phase2_count = rust_plan(repository)
        need(sha(read_file(repository / ".local/m6-hosted/baseline/som")) == BASELINE_SHA)
        expected_health = strict_json(read_file(repository / ".local/m6-hosted/baseline/runtime.json"))["health"]
        need(type(expected_health) is dict)
        requested.mkdir(mode=0o700)
        output = requested
        stage = "baseline_health"
        status, before = baseline_call("/healthz")
        write_new(output / "baseline-health-before.json", before)
        need(status == 200 and strict_json(before) == expected_health)
        name_receipts, reconstructed = [], []
        stage = "name_prompts"
        for case in names:
            directory = output / "name-cases" / case["id"]
            make_dirs(directory)
            request_file = directory / "compile.request.json"
            reply_file = directory / "compile.response.json"
            write_new(request_file, case["request"])
            status, reply = baseline_call("/v1/compile", case["request"])
            write_new(reply_file, reply)
            body = strict_json(reply)
            need(status == 200 and type(body) is dict and body.get("status") == "ok"
                 and type(body.get("bundle")) is dict and type(body["bundle"].get("prompt")) is dict)
            prompt = body["bundle"]["prompt"]
            control = strict_json(case["request"])
            if control["format"] == "som.prompt-json.v1":
                need(canonical(prompt) == canonical(control["prompt"]))
            # This is the exact writer used by the frozen run_name_cases.py:
            # default ensure_ascii=True, insertion order, two spaces, newline.
            raw = (json.dumps(prompt, indent=2) + "\n").encode("utf-8")
            need(sha(raw) == case["expected_sha256"])
            reconstructed.append((case["destination"], raw))
            name_receipts.append({"id": case["id"], "path": case["destination"].relative_to(repository).as_posix(),
                                  "sha256": sha(raw), "bytes": len(raw),
                                  "request_file": request_file.relative_to(output).as_posix(), "request_sha256": sha(case["request"]),
                                  "response_file": reply_file.relative_to(output).as_posix(), "response_sha256": sha(reply)})
        status, after = baseline_call("/healthz")
        write_new(output / "baseline-health-after.json", after)
        need(status == 200 and before == after and strict_json(after) == expected_health)
        stage = "rust_copy"
        copied = output / "rust-baseline"
        copied.mkdir(mode=0o700)
        copy_receipts = []
        for file in files:
            raw = read_file(file["source"])
            need(sha(raw) == file["sha256"] and len(raw) == file["bytes"])
            destination = copied.joinpath(*file["path"].split("/"))
            make_dirs(destination.parent)
            write_new(destination, raw, file["mode"])
            copied_info, source_info = destination.lstat(), file["source"].lstat()
            need((copied_info.st_dev, copied_info.st_ino) != (source_info.st_dev, source_info.st_ino)
                 and sha(read_file(destination)) == file["sha256"])
            copy_receipts.append({key: file[key] for key in ("path", "sha256", "bytes", "mode")})
        source_hashes = {file["path"]: file["sha256"] for file in files}
        copy_hashes = {file["path"]: sha(read_file(copied / file["path"])) for file in files}
        need(source_hashes == copy_hashes and copied.resolve() != (repository / RUST_SOURCE).resolve())
        need(sha(read_file(repository / ".local/m6-hosted/baseline/som")) == BASELINE_SHA)
        stage = "publish_names"
        # All nine frozen prompt hashes and every Rust copy pass before any
        # compatibility prompt is added to the input tree. Never replace one.
        for destination, raw in reconstructed:
            physical_or_missing(destination.parent)
            if destination.exists() or destination.is_symlink():
                need(read_file(destination) == raw)
        for destination, raw in reconstructed:
            if not destination.exists():
                make_dirs(destination.parent)
                write_new(destination, raw, 0o644)
        receipt.update(status="passed", passed=True, name_prompt_count=len(name_receipts), rust_file_count=len(files),
                       rust_phase1_case_count=phase1_count, rust_phase2_case_count=phase2_count,
                       legacy_original_request_count=originals["count"], legacy_originals=originals,
                       baseline={"binary_sha256": BASELINE_SHA, "health_before_sha256": sha(before),
                                 "health_after_sha256": sha(after), "health_unchanged": True},
                       name_prompts=name_receipts,
                       rust_reference={"source": RUST_SOURCE, "destination": "rust-baseline",
                                       "source_tree_sha256": sha(canonical(source_hashes)),
                                       "copy_tree_sha256": sha(canonical(copy_hashes)), "files": copy_receipts})
        write_new(output / "receipt.json", json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n")
        return receipt
    except Exception:
        if output is not None:
            try:
                receipt.update(status="failed", passed=False, failed_stage=stage)
                write_new(output / "receipt.json", json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n")
            except OSError:
                pass
        raise ReconstructionError() from None


class QuietParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ReconstructionError()


def main(argv: list[str] | None = None) -> int:
    try:
        parser = QuietParser(description=__doc__.splitlines()[0])
        parser.add_argument("--repo", required=True)
        parser.add_argument("--baseline", required=True)
        parser.add_argument("--out", required=True)
        args = parser.parse_args(argv)
        receipt = reconstruct(args.repo, args.baseline, args.out)
        print(json.dumps({key: receipt[key] for key in ("version", "stage", "status", "passed", "name_prompt_count",
                                                       "rust_file_count", "rust_phase1_case_count", "rust_phase2_case_count",
                                                       "product_suites", "legacy_assemble_requests")}, sort_keys=True))
        return 0
    except Exception:
        print(REFUSAL, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
