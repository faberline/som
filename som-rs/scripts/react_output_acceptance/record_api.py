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
import stat
import subprocess
import sys
import types
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNNER = REPO / "som-rs/tests/react-output-v1/run_api_contract.py"
PROBE = RUNNER.with_name("api_contract.mjs")
HOOK = HERE / "record_api_fetch.mjs"
API_BOOTSTRAP_REVIEWED = False
RETAINED_SHA256 = '7ec396988cc2e9704c228c05cb3a7cd7a51c87d0fc6ea26c1ffa1455209d75bc'


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def need(condition: bool) -> None:
    if not condition:
        raise ValueError("API recording refused")


def load_pinned_retained(repo: Path):
    path = repo / 'som-rs/scripts/react_output_acceptance/retained_evidence.py'
    need(repo.is_absolute() and repo.resolve() == repo and not any(part.is_symlink() for part in (path, *path.parents)))
    need('retained_evidence' not in sys.modules)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= 2 * 1024 * 1024)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
    finally:
        os.close(fd)
    need(len(raw) <= 2 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == RETAINED_SHA256)
    module = types.ModuleType('retained_evidence')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    sys.modules['retained_evidence'] = module
    return module


def load_linux_sources(repo: Path, *, bound_retained=None):
    """Load only helper bytes bound by the newest sealed QA authority."""
    need(API_BOOTSTRAP_REVIEWED)
    if bound_retained is None:
        retained_evidence = load_pinned_retained(repo)
    else:
        path = repo / 'som-rs/scripts/react_output_acceptance/retained_evidence.py'
        need(sys.modules.get('retained_evidence') is bound_retained and getattr(bound_retained, '__file__', None) == str(path)
             and not any(part.is_symlink() for part in (path, *path.parents)) and sha(path) == RETAINED_SHA256)
        retained_evidence = bound_retained
    authority_rel = 'som-rs/contracts/react-output-v1/linux-api-qa-authority.json'
    files = retained_evidence.sealed_files(repo)
    authority_path = retained_evidence.physical_repo_path(repo, authority_rel)
    raw = retained_evidence.read_physical_bytes(authority_path, 1024 * 1024)
    need(files.get(authority_rel) == hashlib.sha256(raw).hexdigest())
    authority = retained_evidence.parse_json(raw)
    need(isinstance(authority, dict) and authority.get('version') == 'som.react-linux-api-authority.v1')
    bindings = authority.get('source_sha256')
    need(isinstance(bindings, dict))
    modules = []
    for name in ('linux_api_context', 'linux_api_host', 'linux_api_evidence'):
        rel = 'som-rs/scripts/react_output_acceptance/' + name + '.py'
        path = retained_evidence.physical_repo_path(repo, rel)
        content = retained_evidence.read_physical_bytes(path, 1024 * 1024)
        need(isinstance(bindings.get(rel), str) and bindings[rel] == hashlib.sha256(content).hexdigest())
        need(name not in sys.modules)
        module = types.ModuleType(name)
        module.__file__ = str(path)
        exec(compile(content, str(path), 'exec'), module.__dict__)
        sys.modules[name] = module
        modules.append(module)
    return modules


class Recorder:
    def __init__(self, out: Path, original_run, original_popen=subprocess.Popen, qa=None):
        self.out, self.original_run, self.original_popen = out, original_run, original_popen
        self.qa = qa
        self.events: list[dict] = []
        self.errors: list[str] = []
        self.sequence = 0
        self.probe_pid = None

    def popen(self, argv, *args, **kwargs):
        process = self.original_popen(argv, *args, **kwargs)
        if isinstance(argv, (list, tuple)) and len(argv) > 1 and Path(argv[1]) == PROBE:
            self.probe_pid = process.pid
        if self.qa is not None and isinstance(argv, (list, tuple)) and argv:
            command = [os.fspath(item) for item in argv]
            executables = {str(self.qa['python']), str(self.qa['node']),
                           *(str(Path(self.qa['pg_bin']) / item) for item in ('initdb', 'pg_ctl'))}
            if command[0] in executables:
                self.events.append({'kind': 'child-start', 'pid': process.pid, 'argv': command,
                                    'cwd': os.fspath(kwargs.get('cwd') or Path.cwd())})
                original_wait = process.wait
                def observed_wait(*wait_args, **wait_kwargs):
                    try:
                        code = original_wait(*wait_args, **wait_kwargs)
                    except BaseException as exc:
                        self.events.append({'kind': 'child-wait', 'pid': process.pid,
                                            'state': 'unverified', 'error_type': type(exc).__name__,
                                            'exit_code': None})
                        raise
                    self.events.append({'kind': 'child-wait', 'pid': process.pid,
                                        'state': 'completed', 'error_type': None,
                                        'exit_code': code if type(code) is int else None})
                    return code
                process.wait = observed_wait
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
                 "cwd": self.command([str(self.qa['lsof']) if self.qa is not None else "/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])}
        try:
            event["pgid"] = os.getpgid(pid)
            event["pgid_error_errno"] = None
        except OSError as exc:
            event["pgid"], event["pgid_error_errno"] = None, exc.errno
        if port is not None:
            need(1 <= port <= 65535 and port not in (8765, 18765))
            event["port"] = port
            event["listener"] = self.command([str(self.qa['lsof']) if self.qa is not None else "/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        self.events.append(event)

    def run(self, argv, *args, **kwargs):
        command = [os.fspath(value) for value in argv] if isinstance(argv, (list, tuple)) else []
        node = len(command) > 1 and Path(command[1]) == PROBE
        pg = command and Path(command[0]).name == "pg_ctl"
        initdb = command and Path(command[0]).name == "initdb"
        cwd_probe = command and Path(command[0]).name == "lsof" and "-p" in command and "cwd" in command
        live_listener = (self.qa is not None and command and Path(command[0]).name == 'lsof'
                         and '-p' in command and any(word.startswith('-iTCP:') for word in command))
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
        if node or pg or cwd_probe or initdb or live_listener:
            self.sequence += 1
            stem = f"call-{self.sequence:04d}"
            self.events.append({"kind": "probe" if node else "pg_ctl" if pg else "initdb" if initdb else "listener" if live_listener else "lsof",
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
    parser.add_argument("--api-profile", type=Path)
    parser.add_argument("--qa-profile", type=Path)
    parser.add_argument("--full-validation", type=Path)
    args = parser.parse_args(argv)
    need(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted")
    qa = context_module = adapter = None
    if args.api_profile is None:
        need(args.qa_profile is None and args.full_validation is None and os.environ.get("RUNNER_OS") == "macOS")
    else:
        need(args.qa_profile is not None and args.full_validation is not None and os.environ.get("RUNNER_OS") == "Linux")
        context_module, adapter, _ = load_linux_sources(REPO)
        import retained_evidence
        work_path = retained_evidence.physical_repo_path(REPO, args.work)
        need(work_path.name == 'api' and work_path.parent.parent.name == 'products')
        full_before = retained_evidence.physical_repo_path(REPO, args.full_validation)
        need(full_before == work_path.parents[2] / 'api-qa-evidence/full-before.json')
        qa = context_module.validate_case(args.api_profile, REPO, full_before)
        context_module.bind_repository_imports(REPO, qa['_source_shas'], 'host')
        runner_raw = context_module.source_bytes(REPO, 'som-rs/tests/react-output-v1/run_api_contract.py',
                                                 qa['_source_shas'].get('som-rs/tests/react-output-v1/run_api_contract.py'))
        need(qa['node_profile_path'] == args.qa_profile and qa['profile_sha256'] == sha(args.qa_profile))
        adapter.ADMITTED_CONTEXT = qa
    case, product, work = (path.resolve() for path in (args.case, args.product, args.work))
    out = (args.raw_out or work.parent / "api-raw").resolve()
    need(not work.exists() and not out.exists() and out.is_relative_to(REPO / ".local"))
    out.mkdir(parents=True)
    recorder = Recorder(out, subprocess.run, qa=qa)
    code = 1
    receipt = {"version": "som.react-api-raw.v1", "events": recorder.events, "recording_errors": recorder.errors,
               "probe_sha256": sha(PROBE), "runner_sha256": sha(RUNNER), "hook_sha256": sha(HOOK),
               "product_api_sha256": sha(product / "src/api.ts"),
               "product_form_sha256": sha(product / "src/form.ts"),
               "product_schema_sha256": sha(product / "src/schema.ts")}
    if qa is not None:
        receipt['linux_qa'] = {'api_profile_sha256': qa['context_sha256'], 'node_profile_sha256': qa['profile_sha256'],
                               'node': str(qa['node']), 'python': str(qa['python']),
                               'pg_bin': str(qa['pg_bin']), 'lsof': str(qa['lsof']), 'ps': str(qa['ps']),
                               'os_tools': qa['os_tools']}
        for name, executable, option in (('lsof', qa['lsof'], '-v'), ('ps', qa['ps'], '--version')):
            observation = recorder.command([str(executable), option])
            recorder.events.append({'kind': 'os-tool-version', 'tool': name, **observation})
            if observation['exit_code'] != 0:
                recorder.errors.append('ToolVersionFailed')
    try:
        runner_argv = [str(RUNNER), str(case), str(product), str(work)] + (['--linux-qa'] if qa is not None else [])
        with mock.patch.object(sys, "argv", runner_argv), \
             mock.patch.object(subprocess, "run", recorder.run), \
             mock.patch.object(subprocess, "Popen", recorder.popen):
            try:
                if qa is None:
                    runpy.run_path(str(RUNNER), run_name="__main__")
                else:
                    exec(compile(runner_raw, str(RUNNER), "exec", dont_inherit=True),
                         {"__name__": "__main__", "__file__": str(RUNNER), "__package__": ""})
                code = 0
            except SystemExit as exc:
                code = exc.code if type(exc.code) is int else 1
    finally:
        report_path = work / "report.json"
        if report_path.is_file():
            receipt["api_report_sha256"] = sha(report_path)
            report = json.loads(report_path.read_bytes())
            if qa is not None:
                processes = report.get('processes')
                if (not isinstance(processes, list) or len(processes) != 2
                        or not all(isinstance(item, dict) for item in processes)
                        or {item.get('role') for item in processes} != {'postgres', 'python_product'}
                        or not all(item.get('stopped') is True for item in processes)):
                    code = 1
            reported_processes = report.get('processes', [])
            if qa is not None and not isinstance(reported_processes, list):
                reported_processes = []
            for process in reported_processes:
                if qa is not None and not isinstance(process, dict):
                    code = 1
                    continue
                if type(process.get("pid")) is int and process["pid"] > 1:
                    command = process.get("command", [])
                    words = shlex.split(command[command.index("-o") + 1]) if "-o" in command else command
                    flag = "-p" if process.get("role") == "postgres" else "--port"
                    port = int(words[words.index(flag) + 1]) if flag in words else None
                    recorder.observe(lambda: recorder.snapshot(process["pid"], "after-stop", port))
        elif qa is not None:
            code = 1
        receipt["fetch_files"] = [{"file": path.name, "sha256": sha(path)} for path in sorted(out.glob("fetch-*.jsonl"))]
        if recorder.errors:
            code = 1
        if qa is not None:
            try:
                context_module.recheck_case(qa)
                receipt['linux_qa']['recheck_passed'] = True
            except Exception as exc:
                receipt['linux_qa']['recheck_passed'] = False
                receipt['linux_qa']['recheck_error'] = type(exc).__name__
                code = 1
            starts = [event for event in recorder.events if event.get('kind') == 'child-start']
            waits = [event for event in recorder.events if event.get('kind') == 'child-wait']
            if not starts or any(not any(w['pid'] == event['pid'] and w['state'] == 'completed'
                                         and type(w.get('exit_code')) is int for w in waits) for event in starts) \
                    or any(w['state'] != 'completed' for w in waits):
                code = 1
        receipt["exit_code"] = code
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
