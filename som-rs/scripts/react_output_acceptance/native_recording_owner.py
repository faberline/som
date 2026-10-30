"""Completed ordinary owner for the exact c4 Recorder and native live state."""
from __future__ import annotations

import hashlib

import os

import sys

from pathlib import Path

import subprocess

HERE = Path(__file__).resolve().parent

REPO = HERE.parents[2]

RUNNER = REPO / "som-rs/tests/react-output-v1/run_api_contract.py"

PROBE = RUNNER.with_name("api_contract.mjs")

HOOK = HERE / "record_api_fetch.mjs"

def need(condition: bool) -> None:
    if not condition:
        raise ValueError("API recording refused")

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


class _ScopeState:
    """Private live objects; this instance is never a JSON binding."""
    def __init__(self):
        self.closed = False
        self.closing = False
        self.phase = None
        self.refs = {}
        self.children = []
        self.observation_errors = []
        self.secondary = []
        self.failures = []
        self.events = []
        self.startup_failures = []
        self.pre_registration_cleanup = []
        self.kernel_waits = []


class _ChildState:
    def __init__(self, process, requested, actual, cwd, new_session):
        self.process = process
        self.pid = process.pid
        self.requested = requested
        self.actual = actual
        self.cwd = cwd
        self.new_session = new_session
        self.methods = {}
        self.wait_original = None
        self.wait_hook = None
        self.wait_completed = False
        self.exit_code = None
        self.startup_receipt = None
        self.startup_failure = None
        self.failed_before_return = False
        self.streams = []


class _KernelWaitState:
    """Actual per-process kernel observations; never a JSON authority binding."""
    def __init__(self, process, pid, values, lock):
        self.process, self.pid, self.values, self.lock = process, pid, values, lock
        self.hooks = {}
        self.original_private = {}
        self.streams = []
        self.foreign_streams = []
        self.observations = []
        self.claims = []
        self.terminal = False
        self.code = None
        self.status = None
        self.unknown = False
        self.live_observed = False
        self.cleanup_primary = None
        self.installed = False


class _RecordingErrors(list):
    """Keep the old string list and the actual suppressed exception separately."""
    def __init__(self, state, recorder):
        self.state, self.recorder = state, recorder

    def append(self, value):
        state = object.__getattribute__(self, 'state')
        recorder = object.__getattribute__(self, 'recorder')
        if type(state) is not _ScopeState or type(recorder) is not Recorder or type(value) is not str:
            raise ValueError('actual recording error owner required')
        frame = sys._getframe(1)
        methods = type.__getattribute__(Recorder, '__dict__')
        original_handler = (frame.f_globals is globals() and frame.f_locals.get('self') is recorder
            and any(frame.f_code is methods[name].__code__ for name in ('retain', 'observe', 'command')))
        if original_handler:
            error = sys.exception()
            if type(error) is not type(None):
                trace = BaseException.__getattribute__(error, '__traceback__')
                state.observation_errors.append((error, trace))
                state.failures.append(('recording-observation', error, trace))
        # The original entry also appends ordinary markers such as
        # ToolVersionFailed. Those strings are data, never caller authority.
        # Do not attach an unrelated ambient exception to a plain append.
        list.append(self, value)
