"""TypeScript phase 1 product host, pinned toolchain checks and process management (issue #76).

Manages:
- Pinned Node 26.5.0, npm 11.17.0, Homebrew dylibs, vendored npm cache and PostgreSQL 17.7 verification.
- Fresh UTC PostgreSQL 17.7 cluster on a QA port (never 8765 or 18765).
- QA compiler instance on a QA port for compiling/assembling the Python reference product.
- TypeScript product execution (npm ci --offline, node src/main.ts) and clean SIGTERM shutdown.
- PID tracking and lsof -a -p PID -d cwd -Fn verification before signaling any started process.
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
import subprocess
import sys
import time
from typing import Any, Iterator

HERE = pathlib.Path(__file__).resolve().parent
BEHAVIOR_DIR = HERE.parent
SOM_RS = BEHAVIOR_DIR.parent
REPO = SOM_RS.parent
TS_CONTRACT = SOM_RS / "contracts" / "typescript-output-v1"
TS_TESTS = SOM_RS / "tests" / "typescript-output-v1"

for _p in (str(BEHAVIOR_DIR), str(TS_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hosts as base_hosts  # noqa: E402
from tree_sha import tree_sha  # noqa: E402

TOOLCHAIN = json.loads((TS_CONTRACT / "toolchain.json").read_text())
LAYOUT = json.loads((TS_CONTRACT / "product-layout.json").read_text())
NORMALIZATION = json.loads((TS_CONTRACT / "behavior-normalization.json").read_text())
PLAN = json.loads((TS_CONTRACT / "acceptance-plan.json").read_text())

NODE_BIN = TOOLCHAIN["node"]["binary"]
NPM_INVOCATION = TOOLCHAIN["npm"]["invocation"].split()
PG_BIN_DIR = pathlib.Path(TOOLCHAIN["postgresql"]["bin_dir"])

FORBIDDEN_PORTS = {8765, 18765}
BASE_FORBIDDEN_PIDS = {61463}


def find_runtime_root(repo: pathlib.Path = REPO) -> pathlib.Path:
    """Locate the checkout root holding som-rs/runtime/default.json (works inside git worktrees)."""
    for candidate in (repo, *repo.parents):
        if (candidate / "som-rs" / "runtime" / "default.json").is_file():
            return candidate
    return repo


def resolve_local_path(path: pathlib.Path, runtime_root: pathlib.Path | None = None) -> pathlib.Path:
    """Resolve a relative .local path against runtime_root when running from a worktree."""
    if path.is_absolute() or path.exists():
        return path
    root = runtime_root or find_runtime_root()
    candidate = root / path
    if candidate.exists():
        return candidate
    return path


def forbidden_pids() -> set[int]:
    pids = set(BASE_FORBIDDEN_PIDS)
    root = find_runtime_root()
    default_json = root / "som-rs" / "runtime" / "default.json"
    if default_json.is_file():
        try:
            data = json.loads(default_json.read_text())
            if isinstance(data.get("pid"), int):
                pids.add(data["pid"])
            ident_pid = (data.get("identity") or {}).get("pid")
            if isinstance(ident_pid, int):
                pids.add(ident_pid)
        except Exception:  # noqa: BLE001
            pass
    return pids


def check_port_allowed(port: int) -> None:
    if port in FORBIDDEN_PORTS:
        raise RuntimeError(f"port {port} is forbidden (never use 8765 or 18765)")


def check_url_allowed(url: str) -> None:
    clean = url.rstrip("/")
    if clean.endswith((":8765", ":18765")) or ":8765/" in clean or ":18765/" in clean:
        raise RuntimeError(f"URL {url} uses a forbidden port (never use 8765 or 18765)")


def file_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_process_cwd(pid: int, expected_cwd: pathlib.Path) -> tuple[bool, str | None]:
    """Verify via `lsof -a -p PID -d cwd -Fn` that PID is alive and has expected_cwd."""
    if pid in forbidden_pids():
        return False, None
    done = subprocess.run(
        ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    )
    actual = next((line[1:] for line in done.stdout.splitlines() if line.startswith("n")), None)
    if actual is None:
        return False, None
    try:
        matched = pathlib.Path(actual).resolve() == expected_cwd.resolve()
    except OSError:
        matched = actual == str(expected_cwd)
    return matched, actual


class ProcessTracker:
    """Tracks every process started by this tool and verifies cwd via lsof before signaling."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def register(self, pid: int, role: str, cwd: pathlib.Path, command: list[str]) -> dict[str, Any]:
        if pid in forbidden_pids():
            raise RuntimeError(f"refusing to register forbidden PID {pid}")
        matched, actual_cwd = verify_process_cwd(pid, cwd)
        entry: dict[str, Any] = {
            "pid": pid,
            "role": role,
            "expected_cwd": str(cwd.resolve()),
            "initial_cwd": actual_cwd,
            "initial_cwd_verified": matched,
            "command": command,
            "stopped": False,
            "stop_cwd_verified": None,
            "exit_code": None,
        }
        self.records.append(entry)
        return entry

    def stop(
        self,
        proc: subprocess.Popen[Any],
        cwd: pathlib.Path,
        entry: dict[str, Any],
        sig: int = signal.SIGTERM,
        timeout: float = 10.0,
    ) -> int | None:
        pid = proc.pid
        if pid in forbidden_pids():
            raise RuntimeError(f"refusing to signal forbidden PID {pid}")
        if proc.poll() is not None:
            entry["stopped"] = True
            entry["exit_code"] = proc.returncode
            return proc.returncode

        matched, actual_cwd = verify_process_cwd(pid, cwd)
        entry["stop_cwd"] = actual_cwd
        entry["stop_cwd_verified"] = matched
        if not matched:
            raise RuntimeError(
                f"refusing to signal PID {pid} ({entry['role']}): "
                f"lsof cwd {actual_cwd!r} != expected {str(cwd.resolve())!r}"
            )
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            pass
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            matched_kill, _ = verify_process_cwd(pid, cwd)
            if matched_kill:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGKILL)
            code = proc.wait(timeout=5.0)
        entry["stopped"] = True
        entry["exit_code"] = code
        return code


def verify_toolchain(cache_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """Verify pinned Node, libnode, Homebrew dylibs, npm tree, PostgreSQL binaries and vendored cache."""
    problems: list[str] = []
    node_cfg = TOOLCHAIN["node"]
    node_path = pathlib.Path(node_cfg["binary"])
    if not node_path.is_file() or file_sha256(node_path) != node_cfg["sha256"]:
        problems.append(f"node binary hash mismatch at {node_path}")

    libnode_path = pathlib.Path(node_cfg["libnode"]["path"])
    if not libnode_path.is_file() or file_sha256(libnode_path) != node_cfg["libnode"]["sha256"]:
        problems.append(f"libnode hash mismatch at {libnode_path}")

    for dylib, spec in node_cfg["homebrew_libraries"].items():
        resolved = pathlib.Path(spec["resolved"])
        if not resolved.is_file() or file_sha256(resolved) != spec["sha256"]:
            problems.append(f"Homebrew dylib hash mismatch for {dylib} ({resolved})")

    npm_cfg = TOOLCHAIN["npm"]
    npm_root = pathlib.Path(npm_cfg["root"])
    if not npm_root.is_dir():
        problems.append(f"npm root missing at {npm_root}")
    else:
        npm_sha, npm_files, npm_links = tree_sha(npm_root)
        if (npm_sha, npm_files, npm_links) != (
            npm_cfg["tree_sha256"],
            npm_cfg["tree_files"],
            npm_cfg["tree_symlinks"],
        ):
            problems.append(
                f"npm tree mismatch: got {(npm_sha, npm_files, npm_links)}, "
                f"expected {(npm_cfg['tree_sha256'], npm_cfg['tree_files'], npm_cfg['tree_symlinks'])}"
            )

    pg_cfg = TOOLCHAIN["postgresql"]
    for name in ("postgres", "initdb", "pg_ctl"):
        binary = PG_BIN_DIR / name
        if not binary.is_file() or file_sha256(binary) != pg_cfg[name]["sha256"]:
            problems.append(f"PostgreSQL {name} hash mismatch at {binary}")

    cache_info: dict[str, Any] = {}
    if cache_dir is not None:
        if not cache_dir.is_dir():
            problems.append(f"vendored npm cache directory does not exist: {cache_dir}")
        else:
            c_sha, c_files, c_links = tree_sha(cache_dir)
            cache_info = {
                "path": str(cache_dir),
                "tree_sha256": c_sha,
                "tree_files": c_files,
                "tree_symlinks": c_links,
            }
            vc = TOOLCHAIN["vendored_cache"]
            if cache_dir.name == pathlib.Path(vc["path"]).name:
                if (c_sha, c_files, c_links) != (vc["tree_sha256"], vc["tree_files"], vc["tree_symlinks"]):
                    problems.append(
                        f"vendored cache tree mismatch: got {(c_sha, c_files, c_links)}, "
                        f"expected {(vc['tree_sha256'], vc['tree_files'], vc['tree_symlinks'])}"
                    )

    if problems:
        raise RuntimeError("toolchain verification failed: " + "; ".join(problems))
    return {
        "node_version": node_cfg["version"],
        "node_sha256": node_cfg["sha256"],
        "libnode_sha256": node_cfg["libnode"]["sha256"],
        "npm_version": npm_cfg["version"],
        "npm_tree_sha256": npm_cfg["tree_sha256"],
        "postgresql_version": pg_cfg["version"],
        "cache": cache_info,
    }


def toolchain_env(
    cache_dir: pathlib.Path,
    scratch_dir: pathlib.Path,
    extra: dict[str, str] | None = None,
    strip_keys: tuple[str, ...] = (),
) -> dict[str, str]:
    """Build the offline environment for npm and node commands per toolchain.json."""
    logs_dir = scratch_dir / "npm-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    empty_npmrc = scratch_dir / "empty-npmrc"
    if not empty_npmrc.exists():
        empty_npmrc.write_text("")

    env = dict(os.environ)
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "SOM_APP_ADDR", *strip_keys):
        env.pop(key, None)

    node_bin_dir = str(pathlib.Path(NODE_BIN).parent)
    env.update(
        {
            "PATH": f"{node_bin_dir}:/usr/bin:/bin",
            "npm_config_cache": str(cache_dir.resolve()),
            "npm_config_offline": "true",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
            "npm_config_logs_dir": str(logs_dir.resolve()),
            "npm_config_userconfig": str(empty_npmrc.resolve()),
            "TZ": "UTC",
            "LC_ALL": "C",
        }
    )
    if extra:
        env.update(extra)
    return env


def copy_product(source_dir: pathlib.Path, dest_dir: pathlib.Path) -> dict[str, str]:
    """Copy product files (excluding node_modules) to a fresh working directory and return path->sha256."""
    dest_dir.mkdir(parents=True, exist_ok=False)
    hashes: dict[str, str] = {}
    for path in sorted(source_dir.rglob("*")):
        rel = path.relative_to(source_dir)
        if "node_modules" in rel.parts or ".git" in rel.parts:
            continue
        target = dest_dir / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            target.write_bytes(data)
            hashes[rel.as_posix()] = hashlib.sha256(data).hexdigest()
    return hashes


def snapshot_outside_node_modules(root: pathlib.Path) -> dict[str, str]:
    """Return relative_posix_path -> sha256 for every file under root outside node_modules."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if "node_modules" in rel.parts:
            continue
        if path.is_file():
            out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def wait_http_ready(
    proc: subprocess.Popen[Any],
    port: int,
    path: str = "/healthz",
    log_path: pathlib.Path | None = None,
    timeout: float = 20.0,
) -> None:
    """Wait until proc (or its child) listens on port and answers HTTP 200 on path."""
    check_port_allowed(port)
    deadline = time.monotonic() + timeout
    last_error = "did not listen"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(errors="replace")[-2000:] if log_path and log_path.is_file() else ""
            raise RuntimeError(f"process {proc.pid} exited with {proc.returncode} before ready:\n{tail}")
        if base_hosts.listening(proc.pid, port):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            try:
                conn.request("GET", path)
                resp = conn.getresponse()
                resp.read()
                if resp.status < 500:
                    return
                last_error = f"HTTP {resp.status} on {path}"
            except Exception as err:  # noqa: BLE001
                last_error = str(err)
            finally:
                conn.close()
        time.sleep(0.05)
    tail = log_path.read_text(errors="replace")[-2000:] if log_path and log_path.is_file() else ""
    raise RuntimeError(f"process {proc.pid} on port {port} not ready after {timeout}s ({last_error}):\n{tail}")


@contextlib.contextmanager
def pg_cluster(work: pathlib.Path, port: int, tracker: ProcessTracker) -> Iterator[str]:
    """Start a fresh UTC PostgreSQL 17.7 cluster on port and stop it after verifying postmaster cwd."""
    check_port_allowed(port)
    data = work / "pgdata"
    log = work / "postgres.log"
    env = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    init_cmd = [
        str(PG_BIN_DIR / "initdb"),
        "-D",
        str(data),
        "-U",
        "postgres",
        "-A",
        "trust",
        "--encoding=UTF8",
        "--locale=C",
        "--no-instructions",
    ]
    done = subprocess.run(init_cmd, env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"initdb failed ({done.returncode}): {done.stdout}\n{done.stderr}")

    opts = f"-F -h 127.0.0.1 -p {port} -k '' -c timezone=UTC"
    start_cmd = [str(PG_BIN_DIR / "pg_ctl"), "-D", str(data), "-l", str(log), "-w", "-o", opts, "start"]
    record: dict[str, Any] | None = None
    try:
        started = subprocess.run(start_cmd, env=env, capture_output=True, text=True, check=False)
        if started.returncode != 0:
            raise RuntimeError(f"pg_ctl start failed ({started.returncode}): {started.stdout}\n{started.stderr}")
        pid_file = data / "postmaster.pid"
        pg_pid = int(pid_file.read_text().splitlines()[0].strip())
        record = tracker.register(pg_pid, "postgres", data, start_cmd)
        yield f"127.0.0.1:{port}"
    finally:
        if (data / "postmaster.pid").is_file():
            pg_pid = int((data / "postmaster.pid").read_text().splitlines()[0].strip())
            if pg_pid in forbidden_pids():
                raise RuntimeError(f"refusing to stop PostgreSQL with forbidden PID {pg_pid}")
            matched, actual_cwd = verify_process_cwd(pg_pid, data)
            if record is not None:
                record["stop_cwd"] = actual_cwd
                record["stop_cwd_verified"] = matched
            if matched:
                stop_done = subprocess.run(
                    [str(PG_BIN_DIR / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if record is not None:
                    record["stopped"] = stop_done.returncode == 0
                    record["exit_code"] = stop_done.returncode


@contextlib.contextmanager
def qa_compiler(work: pathlib.Path, port: int, tracker: ProcessTracker) -> Iterator[str]:
    """Start the current default compiler binary on a QA port (never 8765 or 18765) and stop it on exit."""
    check_port_allowed(port)
    runtime_root = find_runtime_root()
    default_cfg = json.loads((runtime_root / "som-rs" / "runtime" / "default.json").read_text())
    cmd_list = default_cfg["command"]
    som_bin = pathlib.Path(cmd_list[0])
    assets_idx = cmd_list.index("--assets") + 1
    assets_dir = pathlib.Path(cmd_list[assets_idx])

    cwd = work / "qa-compiler"
    cwd.mkdir(parents=True, exist_ok=True)
    log_path = cwd / "qa-compiler.log"
    command = [str(som_bin), "serve", "--assets", str(assets_dir), "--port", str(port)]
    with log_path.open("wb") as log_file:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    record = tracker.register(proc.pid, "qa_compiler", cwd, command)
    try:
        wait_http_ready(proc, port, "/healthz", log_path=log_path, timeout=20.0)
        yield f"http://127.0.0.1:{port}"
    finally:
        tracker.stop(proc, cwd, record, sig=signal.SIGTERM)


@contextlib.contextmanager
def typescript_product(
    root: pathlib.Path,
    db_url: str,
    port: int,
    env: dict[str, str],
    tracker: ProcessTracker,
) -> Iterator[str]:
    """Run `node src/main.ts` on a QA port with DATABASE_URL and SOM_APP_ADDR, stopping on exit."""
    check_port_allowed(port)
    run_env = {**env, "DATABASE_URL": db_url, "SOM_APP_ADDR": f"127.0.0.1:{port}"}
    log_path = root.parent / f"{root.name}-ts-server-{port}.log"
    command = [NODE_BIN, "src/main.ts"]
    with log_path.open("wb") as log_file:
        proc = subprocess.Popen(
            command,
            cwd=root,
            env=run_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    record = tracker.register(proc.pid, "typescript_product", root, command)
    try:
        wait_http_ready(proc, port, "/healthz", log_path=log_path, timeout=20.0)
        yield f"http://127.0.0.1:{port}"
        if not base_hosts.listening(proc.pid, port):
            tail = log_path.read_text(errors="replace")[-2000:] if log_path.is_file() else ""
            raise RuntimeError(f"TypeScript product stopped listening on {port} before suite finished:\n{tail}")
    finally:
        tracker.stop(proc, root, record, sig=signal.SIGTERM)


def _python_path(root: pathlib.Path) -> str:
    venv_site = (
        find_runtime_root()
        / "som-rs"
        / "runtime"
        / "acceptance-m3-venv"
        / "lib"
        / "python3.12"
        / "site-packages"
    )
    if venv_site.is_dir():
        return f"{root}:{venv_site}"
    return str(root)


def migrate_python_product(
    work: pathlib.Path,
    root: pathlib.Path,
    db_url: str,
    python_exe: str,
) -> None:
    """Run the Python product's Alembic migration against db_url."""
    alembic = work / "_alembic"
    alembic.mkdir(parents=True, exist_ok=True)
    (alembic / "env.py").write_text(base_hosts.ALEMBIC_ENV)
    (alembic / "migrate.py").write_text(base_hosts.MIGRATE)
    url = db_url.replace("postgresql://", "postgresql+psycopg://")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.update(
        {
            "PYTHONPATH": _python_path(root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOM_BEHAVIOR_DATABASE_URL": url,
            "DATABASE_URL": url,
            "TZ": "UTC",
            "LC_ALL": "C",
        }
    )
    done = subprocess.run(
        [python_exe, str(alembic / "migrate.py"), str(alembic), str(root / "alembic" / "versions")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"Python migration failed ({done.returncode}): {(done.stdout + done.stderr)[-2000:]}")


@contextlib.contextmanager
def python_product(
    work: pathlib.Path,
    name: str,
    prompt: dict[str, Any],
    files: dict[str, str],
    db_url: str,
    port: int,
    python_exe: str,
    tracker: ProcessTracker,
) -> Iterator[str]:
    """Migrate and run a Python reference product on a QA port, verifying cwd via lsof before stopping."""
    check_port_allowed(port)
    root = work / name
    base_hosts.products.write(root, files)
    migrate_python_product(work, root, db_url, python_exe)

    shim = work / "_shim"
    shim.mkdir(parents=True, exist_ok=True)
    (shim / "som_behavior_host.py").write_text(base_hosts.HOST_SHIM)

    url = db_url.replace("postgresql://", "postgresql+psycopg://")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.update(
        {
            "PYTHONPATH": _python_path(root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOM_BEHAVIOR_DATABASE_URL": url,
            "DATABASE_URL": url,
            "TZ": "UTC",
            "LC_ALL": "C",
        }
    )
    pkg_name = base_hosts.package(files)
    if prompt["version"] == "som.prompt.v5":
        app, extra_args = f"{pkg_name}.app:app", []
    else:
        app, extra_args = "som_behavior_host:app", ["--app-dir", str(shim)]
        env["SOM_BEHAVIOR_PACKAGE"] = pkg_name
    command = [
        python_exe,
        "-m",
        "uvicorn",
        app,
        *extra_args,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
        "--log-level",
        "warning",
    ]
    log_path = work / f"{name}-py-server-{port}.log"
    with log_path.open("wb") as log_file:
        proc = subprocess.Popen(
            command,
            cwd=root,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    record = tracker.register(proc.pid, "python_product", root, command)
    path = "/healthz" if prompt["version"] == "som.prompt.v5" else prompt["resource"]["path"]
    try:
        wait_http_ready(proc, port, path, log_path=log_path, timeout=20.0)
        yield f"http://127.0.0.1:{port}"
        if not base_hosts.listening(proc.pid, port):
            tail = log_path.read_text(errors="replace")[-2000:] if log_path.is_file() else ""
            raise RuntimeError(f"Python product {name} stopped listening on {port} before suite finished:\n{tail}")
    finally:
        tracker.stop(proc, root, record, sig=signal.SIGTERM)

