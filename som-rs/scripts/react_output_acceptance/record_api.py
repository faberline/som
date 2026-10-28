#!/usr/bin/env python3
"""Record raw API and OS evidence around the unchanged frozen API runner. GitHub only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNNER = REPO / "som-rs/tests/react-output-v1/run_api_contract.py"
PROBE = RUNNER.with_name("api_contract.mjs")
HOOK = HERE / "record_api_fetch.mjs"


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def need(condition: bool) -> None:
    if not condition:
        raise ValueError("API recording refused")


class Recorder:
    def __init__(self, out: Path, original_run, original_popen=subprocess.Popen):
        self.out, self.original_run, self.original_popen = out, original_run, original_popen
        self.events: list[dict] = []
        self.errors: list[str] = []
        self.sequence = 0
        self.probe_pid = None

    def popen(self, argv, *args, **kwargs):
        process = self.original_popen(argv, *args, **kwargs)
        if isinstance(argv, (list, tuple)) and len(argv) > 1 and Path(argv[1]) == PROBE:
            self.probe_pid = process.pid
        return process

    def retain(self, name: str, data: bytes | str | None) -> dict:
        raw = data.encode() if isinstance(data, str) else data or b""
        path = self.out / name
        try:
            path.write_bytes(raw)
        except OSError as exc:
            # Recording must never prevent the frozen owner from stopping its process.
            self.errors.append(type(exc).__name__)
        return {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}

    def observe(self, action) -> None:
        try:
            action()
        except Exception as exc:  # Observation must not replace the frozen lifecycle result.
            self.errors.append(type(exc).__name__)

    def postmaster(self, path: Path, phase: str) -> None:
        raw = path.read_bytes()
        self.events.append({"kind": "postmaster", "phase": phase,
                            "raw": self.retain(f"postmaster-{phase}.pid", raw)})
        if phase == "before-stop":
            self.snapshot(int(raw.splitlines()[0]), "before-pg-stop")

    def command(self, argv: list[str], **kwargs) -> dict:
        error = None
        try:
            done = self.original_run(argv, capture_output=True, check=False, timeout=30, **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            error = type(exc).__name__
            self.errors.append(error)
            done = subprocess.CompletedProcess(argv, None, getattr(exc, "stdout", b""), getattr(exc, "stderr", b""))
        self.sequence += 1
        stem = f"os-{self.sequence:04d}"
        return {"argv": argv, "exit_code": done.returncode, "error_type": error,
                "stdout": self.retain(stem + ".stdout", done.stdout),
                "stderr": self.retain(stem + ".stderr", done.stderr)}

    def snapshot(self, pid: int, phase: str, port: int | None = None) -> None:
        event = {"kind": "process", "pid": pid, "phase": phase,
                 "ps": self.command(["/bin/ps", "-ww", "-p", str(pid), "-o", "pid=,ppid=,pgid=,command="]),
                 "table": self.command(["/bin/ps", "-axo", "pid=,ppid=,pgid="]),
                 "cwd": self.command(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])}
        try:
            event["pgid"] = os.getpgid(pid)
            event["pgid_error_errno"] = None
        except OSError as exc:
            event["pgid"], event["pgid_error_errno"] = None, exc.errno
        if port is not None:
            need(1 <= port <= 65535 and port not in (8765, 18765))
            event["port"] = port
            event["listener"] = self.command(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        self.events.append(event)

    def run(self, argv, *args, **kwargs):
        command = [os.fspath(value) for value in argv] if isinstance(argv, (list, tuple)) else []
        node = len(command) > 1 and Path(command[1]) == PROBE
        pg = command and Path(command[0]).name == "pg_ctl"
        initdb = command and Path(command[0]).name == "initdb"
        cwd_probe = command and Path(command[0]).name == "lsof" and "-p" in command and "cwd" in command
        postmaster = None
        if pg and "-D" in command:
            postmaster = Path(command[command.index("-D") + 1]) / "postmaster.pid"
            if command[-1] == "stop":
                self.observe(lambda: self.postmaster(postmaster, "before-stop"))
        if node:
            env = dict(kwargs.get("env") or os.environ)
            # Only this exact Node subprocess gets the hook. No gate or product file changes.
            need(not env.get("NODE_OPTIONS"))
            env.update(NODE_OPTIONS=f"--import={HOOK}", SOM_API_RAW_DIR=str(self.out))
            kwargs["env"] = env
        done = self.original_run(argv, *args, **kwargs)
        if node or pg or cwd_probe or initdb:
            self.sequence += 1
            stem = f"call-{self.sequence:04d}"
            self.events.append({"kind": "probe" if node else "pg_ctl" if pg else "initdb" if initdb else "lsof",
                "argv": command, "exit_code": done.returncode,
                **({"pid": self.probe_pid} if node else {}),
                "stdout": self.retain(stem + ".stdout", done.stdout),
                "stderr": self.retain(stem + ".stderr", done.stderr)})
        if cwd_probe:
            self.observe(lambda: self.snapshot(int(command[command.index("-p") + 1]), "cwd-check"))
        if pg and command[-1] == "start" and postmaster is not None:
            self.observe(lambda: self.postmaster(postmaster, "after-start"))
        return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("product", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument("--raw-out", type=Path)
    args = parser.parse_args(argv)
    need(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
         and os.environ.get("RUNNER_OS") == "macOS")
    case, product, work = (path.resolve() for path in (args.case, args.product, args.work))
    out = (args.raw_out or work.parent / "api-raw").resolve()
    need(not work.exists() and not out.exists() and out.is_relative_to(REPO / ".local"))
    out.mkdir(parents=True)
    recorder = Recorder(out, subprocess.run)
    code = 1
    receipt = {"version": "som.react-api-raw.v1", "events": recorder.events, "recording_errors": recorder.errors,
               "probe_sha256": sha(PROBE), "runner_sha256": sha(RUNNER), "hook_sha256": sha(HOOK),
               "product_api_sha256": sha(product / "src/api.ts"),
               "product_form_sha256": sha(product / "src/form.ts"),
               "product_schema_sha256": sha(product / "src/schema.ts")}
    try:
        with mock.patch.object(sys, "argv", [str(RUNNER), str(case), str(product), str(work)]), \
             mock.patch.object(subprocess, "run", recorder.run), \
             mock.patch.object(subprocess, "Popen", recorder.popen):
            try:
                runpy.run_path(str(RUNNER), run_name="__main__")
                code = 0
            except SystemExit as exc:
                code = exc.code if type(exc.code) is int else 1
    finally:
        report_path = work / "report.json"
        if report_path.is_file():
            receipt["api_report_sha256"] = sha(report_path)
            report = json.loads(report_path.read_bytes())
            for process in report.get("processes", []):
                if type(process.get("pid")) is int and process["pid"] > 1:
                    command = process.get("command", [])
                    words = shlex.split(command[command.index("-o") + 1]) if "-o" in command else command
                    flag = "-p" if process.get("role") == "postgres" else "--port"
                    port = int(words[words.index(flag) + 1]) if flag in words else None
                    recorder.observe(lambda: recorder.snapshot(process["pid"], "after-stop", port))
        receipt["fetch_files"] = [{"file": path.name, "sha256": sha(path)} for path in sorted(out.glob("fetch-*.jsonl"))]
        if recorder.errors:
            code = 1
        receipt["exit_code"] = code
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
