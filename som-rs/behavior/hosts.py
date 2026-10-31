"""The processes the behavior suite owns: one PostgreSQL cluster per run, and each product
started on its own fresh database (contracts/rust-output-v1/behavior-normalization.json hosting).

- The cluster is a fresh initdb of the pinned PostgreSQL 17.7 binaries, checked by sha256,
  with UTF8, UTC and TCP on 127.0.0.1 only.
- A v1 to v4 Python product is its create_router(get_session) mounted on a plain FastAPI()
  under uvicorn. A v5 Python product is its own <package>.app:app. The Python database is
  migrated by the product's Alembic revision through a harness Alembic env.
- A Rust product is built with the pinned cargo and run as the debug som_app binary with
  DATABASE_URL and SOM_APP_ADDR. Its embedded migrator runs at startup.

Every port comes from QA_PORTS, never 8765 or 18765. Every process is stopped on exit.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator

# Native modes use separate fixed source bindings. Names alone grant no authority.
# Any API reserved name selects the API checks, including a partial binding.
_NATIVE_API = any(name.startswith('__som_native_API_') for name in globals())
if _NATIVE_API:
    if not all(name in globals() for name in (
            '__som_native_active_controls__', '__som_native_API_subprocess__',
            '__som_native_API_tools__')):
        raise ValueError('complete fixed native API host bindings required')
    from _som_native_active_controls_binding import controls as _native_controls
    from types import FunctionType as _NativeFunction, ModuleType as _NativeModule
    _native_controls.verify_installed()
    if (_native_controls is not __som_native_active_controls__
            or _native_controls.role_sources.get('m6.api.host.base') != 'som-rs/behavior/hosts.py'
            or __file__ != str(_native_controls.root / 'som-rs/behavior/hosts.py')
            or __name__ != '_som_m6_api_original_base_hosts'
            or type(__som_native_API_subprocess__) is not _NativeModule
            or type(__som_native_API_tools__) is not _NativeFunction):
        raise ValueError('actual fixed native API host/runner bindings required')
    _native_facade = _NativeModule.__getattribute__(__som_native_API_subprocess__, '__dict__')
    if (_native_facade.get('__file__') != str(_native_controls.root /
            _native_controls.role_sources['m6.api.recording.subprocess'])
            or sys.modules.get(_native_facade.get('__name__')) is not __som_native_API_subprocess__
            or __som_native_API_tools__.__globals__.get('__file__') != str(_native_controls.root /
                _native_controls.role_sources['m6.api.case.consumer'])):
        raise ValueError('fixed stable native API facade/getter source differs')
    _NATIVE_API_TOOLS = __som_native_API_tools__()
    _native_keys = ('python', 'node', 'npm_cli', 'cache', 'pg_bin', 'lsof', 'ps', 'site')
    if (type(_NATIVE_API_TOOLS) is not dict or not all(type(key) is str for key in _NATIVE_API_TOOLS)
            or not all(key in _NATIVE_API_TOOLS and type(_NATIVE_API_TOOLS[key]) is str
                       and _NATIVE_API_TOOLS[key] for key in _native_keys)):
        raise ValueError('actual source-owned native API tool data required')
    for _native_key in _native_keys:
        _native_path = pathlib.Path(_NATIVE_API_TOOLS[_native_key])
        if not _native_path.is_absolute() or '..' in _native_path.parts:
            raise ValueError('absolute exact native API tool path required')
    if (_NATIVE_API_TOOLS['lsof'] != '/usr/bin/lsof'
            or _NATIVE_API_TOOLS['ps'] != '/bin/ps'):
        raise ValueError('fixed Linux lsof/ps paths required')
    # Only this module's alias changes. The real stdlib module stays installed.
    subprocess = __som_native_API_subprocess__
    _native_controls.verify_installed()
elif '__som_native_active_controls__' in globals():
    # These existing whole-module callers retain their own source roles.
    # Neither path receives an API facade, tool getter or new Context.
    from _som_native_active_controls_binding import controls as _native_host_controls
    _native_host_controls.verify_installed()
    if (_native_host_controls is not __som_native_active_controls__
            or __file__ != str(_native_host_controls.root / 'som-rs/behavior/hosts.py')):
        raise ValueError('actual fixed native base host binding required')
    if __name__ == 'hosts':
        if _native_host_controls.role_sources.get('full.hosts') != 'som-rs/behavior/hosts.py':
            raise ValueError('fixed full host source role required')
    elif __name__ == '_som_full_typescript_nested_original_hosts':
        if (_native_host_controls.role_sources.get('ctx.typescript-nested-original-hosts')
                != 'som-rs/behavior/hosts.py'):
            raise ValueError('fixed nested TypeScript host source role required')
    else:
        raise ValueError('unlisted native base host namespace')
    _native_host_controls.verify_installed()
else:
    if '_som_native_active_controls' in sys.modules:
        raise ValueError('native API host/runner has no fixed binding')


HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parent
sys.path.insert(0, str(SOM_RS / "som-targets" / "qa"))

import check_rust_products as products  # noqa: E402
import check_targets as targets  # noqa: E402

TOOLCHAIN = json.loads((SOM_RS / "contracts" / "rust-output-v1" / "toolchain.json").read_text())
PG = TOOLCHAIN["postgresql"]
PG_BIN = pathlib.Path(PG["bin_dir"])
if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
    import full_product_context as hosted_products
    PG_BIN = hosted_products.postgres_bin()
if _NATIVE_API:
    if PG_BIN != pathlib.Path(_NATIVE_API_TOOLS['pg_bin']):
        raise ValueError('actual Context/provider PostgreSQL paths differ')
    PG_BIN = pathlib.Path(_NATIVE_API_TOOLS['pg_bin'])
FORBIDDEN_PORTS = frozenset({8765, 18765})
QA_PORTS = range(18919, 19000)
READY_SECONDS = 180
LIVE: set[subprocess.Popen] = set()
LIVE_LOCK = threading.Lock()
STOP = threading.Event()  # set when the run is interrupted; no new process starts after it

HOST_SHIM = '''"""Mounts a v1 to v4 product router on a plain FastAPI app (behavior-normalization.json)."""
import importlib
import os

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

api = importlib.import_module(os.environ["SOM_BEHAVIOR_PACKAGE"] + ".api")
engine = create_engine(os.environ["SOM_BEHAVIOR_DATABASE_URL"])


def get_session():
    with Session(engine) as session:
        yield session


app = FastAPI()
app.include_router(api.create_router(get_session))
'''

ALEMBIC_ENV = '''"""The harness Alembic env: runs the product revision against SOM_BEHAVIOR_DATABASE_URL."""
import os

from alembic import context
from sqlalchemy import create_engine

engine = create_engine(os.environ["SOM_BEHAVIOR_DATABASE_URL"])
try:
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
'''

MIGRATE = '''import sys
from alembic import command
from alembic.config import Config
config = Config()
config.set_main_option("script_location", sys.argv[1])
config.set_main_option("version_locations", sys.argv[2])
command.upgrade(config, "head")
'''


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned_postgres() -> None:
    if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
        hosted_products.postgres_bin()
        return
    for name in ("postgres", "initdb", "pg_ctl"):
        if sha(PG_BIN / name) != PG[name]["sha256"]:
            raise SystemExit(f"{PG_BIN / name} does not match the toolchain.json sha256")


class Ports:
    """Hands out free QA ports, each once per run."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.used: set[int] = set()

    def take(self) -> int:
        with self.lock:
            for port in QA_PORTS:
                if port in self.used or port in FORBIDDEN_PORTS:
                    continue
                with socket.socket() as probe:
                    try:
                        probe.bind(("127.0.0.1", port))
                    except OSError:
                        continue
                self.used.add(port)
                return port
        raise RuntimeError("no free QA port")

    def give(self, port: int) -> None:
        with self.lock:
            self.used.discard(port)


@contextlib.contextmanager
def cluster(root: pathlib.Path, port: int) -> Iterator[str]:
    """A fresh PostgreSQL cluster. Yields host:port; the cluster is stopped on exit."""
    assert port not in FORBIDDEN_PORTS
    pinned_postgres()
    data = root / "pgdata"
    env = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    subprocess.run([str(PG_BIN / "initdb"), "-D", str(data), "-U", "postgres", "-E", "UTF8", "--locale=C",
                    "--auth=trust", "--no-sync"], check=True, capture_output=True, env=env)
    options = (f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c timezone=UTC "
               f"-c log_timezone=UTC -c max_connections=300 -c fsync=off")
    log = root / "postgres.log"
    try:
        # pg_ctl daemonizes the postmaster, so the stop below also runs when the start fails or is
        # interrupted after the postmaster began.
        if subprocess.run([str(PG_BIN / "pg_ctl"), "-D", str(data), "-l", str(log), "-o", options, "-w",
                           "start"], capture_output=True, env=env).returncode:
            raise RuntimeError(f"PostgreSQL did not start: {log.read_text()[-2000:]}")
        yield f"127.0.0.1:{port}"
    finally:
        subprocess.run([str(PG_BIN / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], capture_output=True,
                       env=env)


def bundle_files(qa: str, prompt: dict, target: str) -> dict[str, str]:
    if os.environ.get("SOM_FULL_PRODUCT_CONTEXT") and hosted_products.recorded_transport():
        return hosted_products.recorded_files(prompt, target)
    body = {"format": "som.prompt-json.v1", "prompt": prompt}
    if target == "rust":
        body["target"] = "rust"
    status, raw = targets.post(qa, targets.dump(body))
    if status != 200:
        raise RuntimeError(f"{target} compile returned {status}")
    status, raw = targets.post(qa, targets.dump({"bundle": json.loads(raw)["bundle"]}), "/v1/assemble")
    if status != 200:
        raise RuntimeError(f"{target} assemble returned {status}")
    return json.loads(raw)["files"]


def package(files: dict[str, str]) -> str:
    names = {path.split("/")[0] for path in files if path.count("/") == 1 and path.endswith("/api.py")}
    if len(names) != 1:
        raise RuntimeError(f"expected one product package, found {sorted(names)}")
    return names.pop()


def _native_process_identity(pid: int) -> tuple[int, int, int, int] | None:
    """Read one same-user Linux process identity; this data grants no ownership."""
    if type(pid) is not int or pid <= 1 or sys.platform != 'linux':
        return None
    root = pathlib.Path('/proc') / str(pid)
    try:
        info = root.stat()
        if info.st_uid != os.geteuid():
            return None
        raw = (root / 'stat').read_text(encoding='ascii')
        head, separator, tail = raw.rpartition(')')
        fields = tail.split()
        if (not separator or not head.startswith(str(pid) + ' (') or len(fields) < 20
                or fields[0] in ('Z', 'X', 'x')
                or not all(value.isdecimal() for value in (fields[1], fields[2], fields[19]))):
            return None
        return int(fields[19]), int(fields[1]), int(fields[2]), info.st_uid
    except (OSError, ValueError, UnicodeError):
        return None


def _native_listening(pid: int, port: int) -> bool:
    """Require this same-user live PID, not a stranger on the requested port."""
    if type(port) is not int or port not in QA_PORTS or port in FORBIDDEN_PORTS:
        return False
    before = _native_process_identity(pid)
    if before is None:
        return False
    done = subprocess.run(
        [_NATIVE_API_TOOLS['lsof'], '-nP', '-a', '-p', str(pid), f'-iTCP:{port}', '-sTCP:LISTEN', '-t'],
        capture_output=True, text=True, check=False,
    )
    if type(done.returncode) is not int or done.returncode != 0 or type(done.stdout) is not str:
        return False
    tokens = done.stdout.split()
    return bool(tokens) and all(token == str(pid) for token in tokens) and before == _native_process_identity(pid)


def listening(pid: int, port: int) -> bool:
    """Whether this process itself listens on the port, so a stranger on it is never taken for it."""
    if _NATIVE_API:
        return _native_listening(pid, port)
    done = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                          capture_output=True, text=True)
    return str(pid) in done.stdout.split()


def wait_ready(process: subprocess.Popen, port: int, log: pathlib.Path) -> None:
    deadline = time.monotonic() + READY_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"the product exited with {process.returncode}: {log.read_text()[-2000:]}")
        if not listening(process.pid, port):
            time.sleep(0.2)
            continue
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", "/__som_behavior_ready__")
            connection.getresponse().read()
            return
        except OSError:
            time.sleep(0.2)
        finally:
            connection.close()
    raise RuntimeError(f"the product did not answer on {port}: {log.read_text()[-2000:]}")


def start(command: list[str], cwd: pathlib.Path, env: dict, **kw: object) -> subprocess.Popen:
    """Start a tracked process in its own process group. Refused once the run is interrupted, and
    checked under the lock, so kill_all either sees the process or the process never starts."""
    with LIVE_LOCK:
        if STOP.is_set():
            raise RuntimeError("the run was interrupted")
        process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True, **kw)
        LIVE.add(process)
    return process


def signal_group(process: subprocess.Popen, signum: int) -> None:
    """Signal the group of a process not yet reaped, so a reused pid is never signalled."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signum)


def stop(process: subprocess.Popen) -> None:
    """Stop the process group, then forget the process."""
    signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        signal_group(process, signal.SIGKILL)
        process.wait()
    with LIVE_LOCK:
        LIVE.discard(process)


def run_tracked(command: list[str], cwd: pathlib.Path, env: dict, timeout: int = 1800) -> tuple[int, str]:
    """products.run with a tracked process: the exit code and the whole output."""
    process = start(command, cwd, env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        signal_group(process, signal.SIGKILL)
        output, _ = process.communicate()
        output += f"\ntimed out after {timeout} s"
    finally:
        stop(process)
    return process.returncode, output


@contextlib.contextmanager
def serve(command: list[str], cwd: pathlib.Path, env: dict, port: int, log: pathlib.Path) -> Iterator[str]:
    assert port not in FORBIDDEN_PORTS
    with log.open("wb") as sink:
        process = start(command, cwd, env, stdout=sink, stderr=subprocess.STDOUT)
        try:
            wait_ready(process, port, log)
            yield f"http://127.0.0.1:{port}"
            if process.poll() is not None or not listening(process.pid, port):
                raise RuntimeError(f"the product stopped listening during the run: {log.read_text()[-2000:]}")
        finally:
            stop(process)


def kill_all() -> None:
    """For an interrupted run: refuse new processes, and kill the process group of every product,
    migration and build still running."""
    with LIVE_LOCK:
        STOP.set()
        for process in list(LIVE):
            signal_group(process, signal.SIGKILL)


def force_rebuild(target_dir: pathlib.Path) -> None:
    """Every product is the som_app package at its workspace root, so cargo gives all of them one
    fingerprint and judges freshness by modification times. Removing the som_app units makes each
    build compile the files just written; the dependencies stay cached."""
    debug = target_dir / "debug"
    for path in (debug / ".fingerprint").glob("som_app-*"):
        shutil.rmtree(path)
    for path in [*(debug / "deps").glob("som_app-*"), *(debug / "deps").glob("libsom_app-*"),
                 debug / "som_app", debug / "libsom_app.rlib"]:
        if path.is_file():
            path.unlink()


def database(pg: str, name: str) -> str:
    return products.fresh_database(pg, name)


class Host:
    """Builds and starts products. One Host per run; Rust builds are serialized."""

    def __init__(self, qa: str, pg: str, work: pathlib.Path, target_dir: pathlib.Path, python: str) -> None:
        self.qa = qa
        self.pg = pg
        self.work = work
        self.target_dir = target_dir
        self.python = python
        self.ports = Ports()
        self.build_lock = threading.Lock()
        self.shim = work / "_shim"
        self.shim.mkdir(parents=True, exist_ok=True)
        (self.shim / "som_behavior_host.py").write_text(HOST_SHIM)
        self.alembic = work / "_alembic"
        self.alembic.mkdir(parents=True, exist_ok=True)
        (self.alembic / "env.py").write_text(ALEMBIC_ENV)
        (self.alembic / "migrate.py").write_text(MIGRATE)

    @contextlib.contextmanager
    def python_product(self, key: str, prompt: dict, files: dict[str, str] | None = None) -> Iterator[str]:
        files = files if files is not None else bundle_files(self.qa, prompt, "python")
        root = self.work / key
        products.write(root, files)
        url = database(self.pg, key).replace("postgresql://", "postgresql+psycopg://")
        env = {key_: value for key_, value in os.environ.items() if not key_.startswith("PYTHON")}
        env.update({"PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1",
                    "SOM_BEHAVIOR_DATABASE_URL": url, "DATABASE_URL": url})
        product_python = (str(hosted_products.prepare_python(root, files))
                          if os.environ.get("SOM_FULL_PRODUCT_CONTEXT") else self.python)
        env = (hosted_products.product_environment(root, env)
               if os.environ.get("SOM_FULL_PRODUCT_CONTEXT") else env)
        code, output = run_tracked([product_python, str(self.alembic / "migrate.py"), str(self.alembic),
                                    str(root / "alembic" / "versions")], root, env, timeout=300)
        if code:
            raise RuntimeError(f"migration failed: {output[-2000:]}")
        name = package(files)
        port = self.ports.take()
        if prompt["version"] == "som.prompt.v5":
            app, extra = f"{name}.app:app", []
        else:
            app, extra = "som_behavior_host:app", ["--app-dir", str(self.shim)]
            env["SOM_BEHAVIOR_PACKAGE"] = name
        command = [product_python, "-m", "uvicorn", app, *extra, "--host", "127.0.0.1", "--port", str(port),
                   "--no-access-log", "--log-level", "warning"]
        try:
            with serve(command, root, env, port, root / "server.log") as base:
                yield base
        finally:
            self.ports.give(port)

    @contextlib.contextmanager
    def rust_product(self, key: str, prompt: dict, files: dict[str, str] | None = None) -> Iterator[str]:
        files = files if files is not None else bundle_files(self.qa, prompt, "rust")
        root = self.work / key
        products.write(root, files)
        env = {**os.environ, **products.pinned_toolchain(), "CARGO_TARGET_DIR": str(self.target_dir)}
        with self.build_lock:
            if STOP.is_set():
                raise RuntimeError("the run was interrupted")
            force_rebuild(self.target_dir)
            code, output = run_tracked([str(products.BIN_DIR / "cargo"), "build", "--locked", "--offline", "-q"],
                                       root, env)
            if code:
                raise RuntimeError(f"cargo build failed: {output[-3000:]}")
            shutil.copy2(self.target_dir / "debug" / "som_app", root / "som_app")
        url = database(self.pg, key)
        port = self.ports.take()
        run_env = {"PATH": os.environ["PATH"], "DATABASE_URL": url, "SOM_APP_ADDR": f"127.0.0.1:{port}"}
        try:
            with serve([str(root / "som_app")], root, run_env, port, root / "server.log") as base:
                yield base
        finally:
            self.ports.give(port)
