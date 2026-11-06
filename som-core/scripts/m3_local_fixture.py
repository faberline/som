"""Own all local M3 databases and mocks. Never use a host default database."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import threading

from local_business_services import port
from local_flow_services import FlowFixture

PG_BIN = Path("/opt/homebrew/opt/postgresql@17/bin")
if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
    from full_product_context import postgres_bin
    PG_BIN = postgres_bin()


@contextmanager
def interruption():
    """Let a coordinator stop its worker through normal fixture cleanup."""
    def stop_worker(_number, _frame):
        raise KeyboardInterrupt("M3 worker stopped")
    previous = signal.signal(signal.SIGTERM, stop_worker)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def close_native(process) -> None:
    if process is None:
        return
    if process.stdin is not None:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)


class ServerObservation:
    """Sample only the native server started by this worker."""

    def __init__(self, process) -> None:
        self.process = process
        self.stop = threading.Event()
        self.samples: list[int] = []
        self.sample()
        self.thread = threading.Thread(target=self.collect, daemon=True)
        self.thread.start()

    def sample(self) -> None:
        if self.process.poll() is not None:
            return
        value = subprocess.run(["ps", "-o", "rss=", "-p", str(self.process.pid)],
                               capture_output=True, text=True, check=False, timeout=5)
        if value.returncode == 0 and value.stdout.strip().isdigit():
            self.samples.append(int(value.stdout) * 1024)

    def collect(self) -> None:
        while not self.stop.wait(1):
            self.sample()

    def values(self) -> dict:
        return {"idle_resident_bytes": self.samples[0] if self.samples else None,
                "peak_sampled_resident_bytes": max(self.samples) if self.samples else None,
                "sample_period_seconds": 1, "samples": len(self.samples)}

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=6)


class M3Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.port = port()
        self.data = self.root / "postgres"
        self.socket = Path(tempfile.mkdtemp(prefix="som-m3-pg-", dir="/private/tmp"))
        self.flow = None
        self.pg_started = False
        self.old_environment = dict(os.environ)
        self.closed = False

    def run(self, *command: str) -> None:
        result = subprocess.run(command, capture_output=True, text=True,
                                check=False, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def start(self) -> dict:
        if not all((PG_BIN / name).is_file() for name in ("initdb", "pg_ctl", "createdb")):
            raise ValueError("local PostgreSQL 17 binaries are unavailable")
        self.run(str(PG_BIN / "initdb"), "-D", str(self.data),
                 "--username=som_m3_test", "--auth=trust", "--no-locale",
                 "--encoding=UTF8")
        self.run(str(PG_BIN / "pg_ctl"), "-D", str(self.data),
                 "-l", str(self.root / "postgres.log"), "-o",
                 f"-h 127.0.0.1 -p {self.port} -k {self.socket}", "-w", "start")
        self.pg_started = True
        for key in list(os.environ):
            if key.startswith(("PG", "AWS_")) or key == "GOOGLE_APPLICATION_CREDENTIALS":
                os.environ.pop(key)
        os.environ.update(PGHOST="127.0.0.1", PGPORT=str(self.port),
                          PGUSER="som_m3_test", PGDATABASE="postgres",
                          PATH=str(PG_BIN) + os.pathsep + self.old_environment.get("PATH", ""),
                          TEST_DATABASE_URL=f"postgresql+psycopg://som_m3_test@127.0.0.1:{self.port}/postgres")
        self.flow = FlowFixture(self.root / "providers")
        self.flow.start()
        path = self.root / "providers/manifest.json"
        result = json.loads(path.read_text())
        result["m3_postgresql"] = {"root": str(self.data), "port": self.port,
                                   "host": "127.0.0.1", "user": "som_m3_test",
                                   "pid": int((self.data / "postmaster.pid").read_text().splitlines()[0])}
        result["test_only"] = True
        path.write_text(json.dumps(result, indent=2) + "\n")
        return result

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.flow is not None:
                self.flow.close()
        finally:
            try:
                # pg_ctl can start successfully just before an interruption.
                # This PID file belongs to the fresh cluster under this root.
                if self.pg_started or (self.data / "postmaster.pid").is_file():
                    self.run(str(PG_BIN / "pg_ctl"), "-D", str(self.data), "-w", "stop", "-m", "fast")
            finally:
                os.environ.clear()
                os.environ.update(self.old_environment)
                shutil.rmtree(self.socket, ignore_errors=True)

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_exception):
        self.close()
