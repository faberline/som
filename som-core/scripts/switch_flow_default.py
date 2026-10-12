"""Verify a retained M2 binary, canary both versions, then switch locally.

This tool changes only the two development links and the verified SOM listener.
It retains a working executable and a complete rollback record before stopping
the original listener. Generated project files are never installed by SOM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time
import traceback

import httpx

ROOT = Path(__file__).resolve().parents[2]
M2 = ROOT / "som-rs/evals/m2-flow-2026-10-04/r31"
MODEL = ROOT / "som-rs/models/flow-m2-candidate/smoke-r31-001"
BLOCKS = ROOT / "som-code-python/data/blocks/flow-v7-compact/blocks.json"
OLD_MODEL = ROOT / "som-rs/models/crud-query-date-service-v4"
OLD_BLOCKS = ROOT / "som-code-python/data/blocks/v4/blocks.json"
MODEL_LINK = ROOT / "som-rs/models/current"
BLOCK_LINK = ROOT / "som-code-python/data/blocks/current"
URL = "http://127.0.0.1:8765"
BINARY_HASH = "14cf0a67fbcc943639c9e0efb661d6aaf0de7b00289c05fc52819f9e97f239d6"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text())


def dump(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def start(binary: Path, model: Path, blocks: Path, directory: Path, port=0):
    directory.mkdir()
    command = [str(binary), "serve", "--model", str(model),
               "--blocks", str(blocks), "--port", str(port)]
    with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout,
                                   stderr=stderr, start_new_session=True)
    dump(directory / "process.json", {"pid": process.pid, "command": command})
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError((directory / "stderr.log").read_text())
            match = re.search(r"SOM listening on 127\.0\.0\.1:(\d+)", (directory / "stderr.log").read_text())
            if match:
                address = "http://127.0.0.1:" + match[1]
                health = httpx.get(address + "/healthz", timeout=5)
                health.raise_for_status()
                dump(directory / "health.json", health.json())
                return process, address
            time.sleep(0.2)
        raise RuntimeError("SOM startup exceeded 60 seconds")
    except BaseException:
        stop(process)
        raise


def stop(process) -> None:
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=30)


def listener() -> int:
    value = subprocess.run(["lsof", "-t", "-nP", "-iTCP:8765", "-sTCP:LISTEN"],
                           capture_output=True, text=True, check=True)
    pids = value.stdout.strip().splitlines()
    if len(pids) != 1 or not pids[0].isdigit():
        raise RuntimeError("expected exactly one default SOM listener")
    return int(pids[0])


def cli(binary: Path, url: str, prompt: dict, case: Path, expected: dict) -> None:
    prompt_file = case / "prompt.json"
    prompt_file.write_text(json.dumps(prompt) + "\n")
    project = case / "empty-project"
    project.mkdir()
    result = subprocess.run([str(binary), "generate", str(prompt_file), "--url", url],
                            cwd=project, capture_output=True, text=True, timeout=120)
    (case / "cli.stdout").write_text(result.stdout)
    (case / "cli.stderr").write_text(result.stderr)
    if result.returncode != 0 or read(case / "cli.stdout")["files"] != expected:
        raise RuntimeError("CLI did not return the complete expected files")
    if list(project.iterdir()):
        raise RuntimeError("CLI wrote project files")


def link(path: Path, target: str) -> None:
    if not path.is_symlink():
        raise RuntimeError(f"refuse to replace a non-link: {path}")
    temporary = path.with_name(path.name + ".m3-switch-tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError("unfinished switch link exists")
    temporary.symlink_to(target)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / "som-rs/evals"):
        raise ValueError("switch evidence must stay inside the repository")
    out.mkdir(parents=True, exist_ok=False)
    old_pid = listener()
    old_command = subprocess.run(["ps", "-p", str(old_pid), "-o", "command="],
                                 text=True, capture_output=True, check=True).stdout.strip()
    if old_command != "som-rs/target/release/som serve":
        raise RuntimeError("default listener no longer matches the inspected command")
    before = httpx.get(URL + "/healthz", timeout=5).json()
    original = {str(MODEL_LINK.relative_to(ROOT)): str(MODEL_LINK.readlink()),
                str(BLOCK_LINK.relative_to(ROOT)): str(BLOCK_LINK.readlink())}
    assert original == {"som-rs/models/current": "crud-query-date-service-v4",
                        "som-code-python/data/blocks/current": "v4"}
    assert before["model_manifest_sha256"] == digest(OLD_MODEL / "manifest.json")
    assert before["blocks_sha256"] == digest(OLD_BLOCKS)
    seal = read(M2 / "source-seal-001/seal.json")
    assert all(digest(ROOT / name) == value for name, value in seal["source_sha256"].items())
    assert read(M2 / "final-audit-002/result.json")["passed"]
    source = M2 / "runtime-assets-001/som"
    assert digest(source) == BINARY_HASH
    saved = ROOT / "som-rs/runtime/versions" / BINARY_HASH / "som"
    saved.parent.mkdir(parents=True, exist_ok=True)
    if not saved.exists():
        shutil.copy2(source, saved)
    assert digest(saved) == BINARY_HASH
    record = {"version": "som.development-switch.v1", "phase": "canary",
              "passed": False, "pid_before": old_pid, "command_before": old_command,
              "health_before": before, "links_before": original,
              "binary": str(saved), "binary_sha256": BINARY_HASH,
              "source_seal_sha256": digest(M2 / "source-seal-001/seal.json"),
              "records": [], "started_at_unix": time.time()}
    dump(out / "result.json", record)
    processes = []
    switched = False
    promoted = None
    try:
        rollback, rollback_url = start(saved, OLD_MODEL, OLD_BLOCKS, out / "rollback-canary")
        processes.append(rollback)
        first = json.loads((ROOT / "som-core/data/service-regression-v4/heldout.jsonl").read_text().splitlines()[0])
        expected = read(M2 / "legacy-generation-002/comparison/model/legacy100/0000.json")["files"]
        response = httpx.post(rollback_url + "/v1/generate", json=first["prompt"], timeout=120)
        assert response.status_code == 200 and response.json()["files"] == expected
        cli(saved, rollback_url, first["prompt"], out / "rollback-canary", expected)
        record["rollback_canary_passed"] = True
        stop(rollback)
        processes.remove(rollback)
        candidate, candidate_url = start(saved, MODEL, BLOCKS, out / "candidate-canary")
        processes.append(candidate)
        trace = out / "candidate-canary/stderr.log"
        health = httpx.get(candidate_url + "/healthz", timeout=5).json()
        assert health["model_manifest_sha256"] == digest(MODEL / "manifest.json")
        assert health["blocks_sha256"] == digest(BLOCKS)
        assert health["flow_model_evidence"] == "seen-development-fixtures-only"
        groups = [("smoke12", "flow-v7-r10/dev.jsonl"),
                  ("services14", "composed/regression.jsonl"),
                  ("compositions120", "composed/heldout.jsonl"),
                  ("blind80", "composed/blind.jsonl"),
                  ("legacy100", "service-regression-v4/heldout.jsonl")]
        with httpx.Client(timeout=120) as client:
            for group, filename in groups:
                rows = [json.loads(line) for line in (ROOT / "som-core/data" / filename).read_text().splitlines()]
                for index, row in enumerate(rows):
                    case = out / "cases" / group / f"{index:04d}"
                    case.mkdir(parents=True)
                    expected = (read(M2 / "training-prerequisite-001" / row["id"] / "response.json")["files"]
                                if group == "smoke12" else
                                read(M2 / "legacy-generation-002/comparison/model" / group / f"{index:04d}.json")["files"])
                    offset = trace.stat().st_size
                    started = time.monotonic()
                    response = client.post(candidate_url + "/v1/generate", json=row["prompt"])
                    body = response.json()
                    dump(case / "response.json", body)
                    assert response.status_code == 200 and body["files"] == expected, (group, index, body.get("code"))
                    with trace.open("rb") as stream:
                        stream.seek(offset)
                        raw = stream.read().decode()
                    (case / "trace.jsonl").write_text(raw)
                    dump(case / "prompt.json", row["prompt"])
                    if group == "smoke12":
                        cli(saved, candidate_url, row["prompt"], case, expected)
                    record["records"].append({"group": group, "index": index,
                        "passed": True, "seconds": time.monotonic()-started,
                        "response_sha256": digest(case / "response.json"),
                        "trace_sha256": digest(case / "trace.jsonl")})
                    dump(out / "result.json", record)
                print(json.dumps({"group": group, "passed": len(rows)}), flush=True)
        assert len(record["records"]) == 326
        stop(candidate)
        processes.remove(candidate)
        assert listener() == old_pid
        assert httpx.get(URL + "/healthz", timeout=5).json() == before
        assert all(digest(ROOT / name) == value for name, value in seal["source_sha256"].items())
        if args.promote:
            record["phase"] = "switching"
            dump(out / "result.json", record)
            # The old process was verified immediately above. Never signal by name.
            os.kill(old_pid, signal.SIGTERM)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                check = subprocess.run(["lsof", "-t", "-nP", "-iTCP:8765", "-sTCP:LISTEN"], capture_output=True, text=True)
                if not check.stdout.strip():
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("old listener did not stop")
            switched = True
            link(MODEL_LINK, "flow-m2-candidate/smoke-r31-001")
            link(BLOCK_LINK, "flow-v7-compact")
            promoted, address = start(saved, MODEL_LINK, BLOCK_LINK / "blocks.json", out / "promoted", 8765)
            assert address == URL
            health = httpx.get(URL + "/healthz", timeout=5).json()
            assert health["model_manifest_sha256"] == digest(MODEL / "manifest.json")
            assert health["blocks_sha256"] == digest(BLOCKS)
            for group, filename in groups[:2] + groups[-1:]:
                row = json.loads((ROOT / "som-core/data" / filename).read_text().splitlines()[0])
                expected = read(out / "cases" / group / "0000/response.json")["files"]
                case = out / ("post-switch-" + group)
                case.mkdir()
                cli(saved, URL, row["prompt"], case, expected)
            record.update(default_pid=promoted.pid, health_after=health,
                          links_after={str(p.relative_to(ROOT)): str(p.readlink()) for p in [MODEL_LINK, BLOCK_LINK]},
                          rollback_command=[str(saved), "serve", "--model", str(OLD_MODEL), "--blocks", str(OLD_BLOCKS), "--port", "8765"],
                          development_default_updated=True)
            dump(ROOT / "som-rs/runtime/default.json", {"pid": promoted.pid,
                 "command": read(out / "promoted/process.json")["command"],
                 "evidence": str(out / "result.json"), "health": health})
        record.update(phase="completed", passed=True)
    except BaseException:
        record.update(phase="failed", passed=False, error=traceback.format_exc())
        if switched:
            if promoted is not None:
                stop(promoted)
            for path in [MODEL_LINK, BLOCK_LINK]:
                link(path, original[str(path.relative_to(ROOT))])
            restored, address = start(saved, OLD_MODEL, OLD_BLOCKS, out / "restored", 8765)
            record.update(rollback_pid=restored.pid, rollback_health=httpx.get(address + "/healthz", timeout=5).json())
            dump(ROOT / "som-rs/runtime/default.json", read(out / "restored/process.json"))
    finally:
        for process in processes:
            stop(process)
        record["seconds"] = time.time()-record["started_at_unix"]
        dump(out / "result.json", record)
    print(json.dumps({k: record.get(k) for k in ["phase", "passed", "default_pid", "error"]}))
    raise SystemExit(not record["passed"])


if __name__ == "__main__":
    main()
