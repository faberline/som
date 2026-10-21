#!/usr/bin/env python3
"""Unit tests for som-rs/scripts/rust_quickstart/pg_run.py."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.parse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import pg_run  # noqa: E402

TEST_URL = "http://127.0.0.1:19876"


def pick_free_port(exclude: set[int] | None = None) -> int:
    forbidden = {8765, 18765} | (exclude or set())
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in forbidden:
            return port
    raise RuntimeError("Could not allocate an allowed free TCP port")


def write_executable(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class FakeRustHarness:
    """Creates fake SOM, Cargo, PostgreSQL, and vendor bundle fixtures in a temp directory."""

    def __init__(
        self,
        root: Path,
        *,
        package: str = "items",
        shape: str = "resource",
        cargo_build_exit_code: int = 0,
        cargo_test_exit_code: int = 0,
        create_status_code: int = 201,
        app_exit_after_healthz: bool = False,
    ) -> None:
        self.root = root
        self.package = package
        self.shape = shape
        self.cargo_build_exit_code = cargo_build_exit_code
        self.cargo_test_exit_code = cargo_test_exit_code
        self.create_status_code = create_status_code
        self.app_exit_after_healthz = app_exit_after_healthz
        self.post_counter = 0

        self.bin_dir = root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.vendor_dir = root / "vendor_bundle"
        (self.vendor_dir / "cargo-home").mkdir(parents=True, exist_ok=True)
        (self.vendor_dir / "vendor").mkdir(parents=True, exist_ok=True)
        (self.vendor_dir / "cargo-home" / "config.toml").write_text(
            '[source.crates-io]\nreplace-with = "vendored-sources"\n\n'
            f'[source.vendored-sources]\ndirectory = "{self.vendor_dir / "vendor"}"\n\n'
            "[net]\noffline = true\n",
            encoding="utf-8",
        )

        self.event_log = root / "events.jsonl"
        self.app_pid_file = root / "som_app.pid"
        self.cnl_file = root / "sample.cnl"
        self.cnl_file.write_text(
            f"Use SOM CNL version 1.\nUse prompt version 1.\nUse package {package}.\nEnd request.\n",
            encoding="utf-8",
        )
        self.som_bin = self._create_fake_som()
        self._create_pg_tools()
        self._create_fake_cargo()

    def _header_lines(self) -> list[str]:
        return [
            f"#!{sys.executable}",
            "import json",
            "import os",
            "from pathlib import Path",
            "import sys",
            f"EVENT_LOG = Path({str(self.event_log)!r})",
            "def record_event(tool, extra=None):",
            "    payload = {",
            '        "tool": tool,',
            '        "argv": sys.argv[1:],',
            '        "cwd": os.getcwd(),',
            '        "CARGO_HOME": os.environ.get("CARGO_HOME"),',
            '        "CARGO_TARGET_DIR": os.environ.get("CARGO_TARGET_DIR"),',
            '        "CARGO_NET_OFFLINE": os.environ.get("CARGO_NET_OFFLINE"),',
            '        "DATABASE_URL": os.environ.get("DATABASE_URL"),',
            '        "TEST_DATABASE_URL": os.environ.get("TEST_DATABASE_URL"),',
            '        "SOM_APP_ADDR": os.environ.get("SOM_APP_ADDR"),',
            "    }",
            "    if extra:",
            "        payload.update(extra)",
            '    with EVENT_LOG.open("a", encoding="utf-8") as fh:',
            '        fh.write(json.dumps(payload, sort_keys=True) + "\\n")',
        ]

    def _create_fake_som(self) -> Path:
        extra_files_lines: list[str] = []
        if self.shape == "capability_postgresql":
            extra_files_lines.append(
                '(out_dir / "src" / "integrations.rs").write_text("pub struct Settings;\\n", encoding="utf-8")'
            )
        elif self.shape == "flow_postgresql":
            extra_files_lines.extend(
                [
                    '(out_dir / "src" / "integrations.rs").write_text("pub struct Settings;\\n", encoding="utf-8")',
                    '(out_dir / "src" / "flows.rs").write_text("pub struct Flow;\\n", encoding="utf-8")',
                ]
            )
        return write_executable(
            self.root / "fake_som",
            [
                *self._header_lines(),
                "argv = sys.argv[1:]",
                'record_event("som")',
                "if (",
                "    len(argv) != 8",
                '    or argv[0] != "write"',
                '    or argv[2] != "--target"',
                '    or argv[3] != "rust"',
                '    or argv[4] != "--out"',
                '    or argv[6] != "--url"',
                "):",
                '    sys.stderr.write(f"unexpected som args: {argv!r}\\n")',
                "    sys.exit(2)",
                "out_dir = Path(argv[5])",
                '(out_dir / "src").mkdir(parents=True, exist_ok=True)',
                '(out_dir / "migrations").mkdir(parents=True, exist_ok=True)',
                '(out_dir / "tests").mkdir(parents=True, exist_ok=True)',
                '(out_dir / "Cargo.toml").write_text(\'[package]\\nname = "som_app"\\nversion = "0.1.0"\\nedition = "2024"\\n\', encoding="utf-8")',
                '(out_dir / "Cargo.lock").write_text(\'version = 4\\n\', encoding="utf-8")',
                f'(out_dir / "README.md").write_text("# {self.package}\\n", encoding="utf-8")',
                '(out_dir / "src" / "main.rs").write_text("fn main() {}\\n", encoding="utf-8")',
                '(out_dir / "src" / "lib.rs").write_text("pub fn router() {}\\n", encoding="utf-8")',
                '(out_dir / "src" / "models.rs").write_text("pub struct Row;\\n", encoding="utf-8")',
                '(out_dir / "src" / "schemas.rs").write_text("pub struct Create;\\n", encoding="utf-8")',
                '(out_dir / "src" / "api.rs").write_text("pub fn healthz() {}\\n", encoding="utf-8")',
                '(out_dir / "src" / "db.rs").write_text("pub fn query() {}\\n", encoding="utf-8")',
                '(out_dir / "migrations" / "0001_rev0000.sql").write_text("-- revision: rev0000, down_revision: none\\n", encoding="utf-8")',
                '(out_dir / "tests" / "api.rs").write_text("#[test] fn smoke() {}\\n", encoding="utf-8")',
                *extra_files_lines,
                'print(json.dumps({"status": "ok", "files": 11, "out": str(out_dir), "target": "rust"}))',
            ],
        )

    def _create_pg_tools(self) -> None:
        write_executable(
            self.bin_dir / "initdb",
            [
                *self._header_lines(),
                'record_event("initdb")',
                "argv = sys.argv[1:]",
                'if "-D" in argv:',
                '    pgdata = Path(argv[argv.index("-D") + 1])',
                "    pgdata.mkdir(parents=True, exist_ok=True)",
                'print("initdb ok")',
            ],
        )
        write_executable(
            self.bin_dir / "pg_ctl",
            [
                *self._header_lines(),
                "argv = sys.argv[1:]",
                'action = argv[-1] if argv else "unknown"',
                'record_event(f"pg_ctl_{action}")',
                'print(f"pg_ctl {action} ok")',
            ],
        )
        write_executable(
            self.bin_dir / "createdb",
            [
                *self._header_lines(),
                "argv = sys.argv[1:]",
                'db_name = argv[-1] if argv else ""',
                'record_event("createdb", {"db": db_name})',
                'print(f"createdb {db_name} ok")',
            ],
        )

    def _create_fake_cargo(self) -> None:
        app_script_lines = [
            *self._header_lines(),
            "from http.server import BaseHTTPRequestHandler, HTTPServer",
            "import signal",
            'addr = os.environ.get("SOM_APP_ADDR", "127.0.0.1:8000")',
            'host, port_str = addr.rsplit(":", 1)',
            "port = int(port_str)",
            f"EXIT_AFTER_HEALTHZ = {self.app_exit_after_healthz!r}",
            "post_seq = 0",
            "class Handler(BaseHTTPRequestHandler):",
            "    def log_message(self, fmt, *args):",
            "        return",
            "    def do_GET(self):",
            '        if self.path == "/healthz":',
            '            body = b\'{"status":"ok"}\'',
            "            self.send_response(200)",
            '            self.send_header("Content-Type", "application/json")',
            '            self.send_header("Content-Length", str(len(body)))',
            "            self.end_headers()",
            "            self.wfile.write(body)",
            "            self.wfile.flush()",
            "            if EXIT_AFTER_HEALTHZ:",
            "                os._exit(1)",
            "        else:",
            "            self.send_response(404)",
            "            self.end_headers()",
            "    def do_POST(self):",
            "        global post_seq",
            "        post_seq += 1",
            '        length = int(self.headers.get("Content-Length", "0"))',
            '        raw = self.rfile.read(length).decode("utf-8", errors="replace")',
            '        record_event("som_app_post", {"path": self.path, "body": raw})',
            '        resp_payload = json.dumps({"id": 41 + post_seq, "path": self.path, "received": json.loads(raw)}).encode("utf-8")',
            f"        self.send_response({self.create_status_code})",
            '        self.send_header("Content-Type", "application/json")',
            '        self.send_header("Content-Length", str(len(resp_payload)))',
            "        self.end_headers()",
            "        self.wfile.write(resp_payload)",
            "server = HTTPServer((host, port), Handler)",
            f"PID_FILE = Path({str(self.app_pid_file)!r})",
            'PID_FILE.write_text(str(os.getpid()), encoding="utf-8")',
            'record_event("som_app_start", {"pid": os.getpid()})',
            "def handle_sigterm(signum, frame):",
            '    record_event("som_app_sigterm", {"pid": os.getpid()})',
            "    sys.exit(0)",
            "signal.signal(signal.SIGTERM, handle_sigterm)",
            "server.serve_forever()",
        ]
        app_script_content = "\n".join(app_script_lines) + "\n"

        write_executable(
            self.bin_dir / "cargo",
            [
                *self._header_lines(),
                "import stat",
                "argv = sys.argv[1:]",
                'subcmd = argv[0] if argv else "unknown"',
                'record_event(f"cargo_{subcmd}")',
                'if subcmd == "build":',
                f"    if {self.cargo_build_exit_code} != 0:",
                f"        sys.exit({self.cargo_build_exit_code})",
                '    target_dir = Path(os.environ["CARGO_TARGET_DIR"])',
                '    debug_dir = target_dir / "debug"',
                "    debug_dir.mkdir(parents=True, exist_ok=True)",
                '    bin_path = debug_dir / "som_app"',
                f"    bin_path.write_text({app_script_content!r}, encoding='utf-8')",
                "    bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)",
                '    print("cargo build ok")',
                "    sys.exit(0)",
                'if subcmd == "test":',
                '    print("cargo test ok")',
                f"    sys.exit({self.cargo_test_exit_code})",
                'sys.stderr.write(f"unexpected cargo subcommand: {argv!r}\\n")',
                "sys.exit(2)",
            ],
        )

    def read_events(self) -> list[dict]:
        if not self.event_log.is_file():
            return []
        return [
            json.loads(line)
            for line in self.event_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def wait_for_app_ready(self, timeout: float = 5.0) -> int | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.app_pid_file.is_file():
                raw = self.app_pid_file.read_text(encoding="utf-8").strip()
                if raw.isdigit():
                    pid = int(raw)
                    try:
                        os.kill(pid, 0)
                        return pid
                    except OSError:
                        pass
            time.sleep(0.05)
        return None


class RustPgRunTest(unittest.TestCase):
    def invoke_pg_run(
        self,
        harness: FakeRustHarness,
        args: list[str],
    ) -> tuple[int, str]:
        orig_http = pg_run.http_request

        def sandbox_aware_http_request(
            method: str,
            url: str,
            body: bytes | None = None,
            headers: dict[str, str] | None = None,
            timeout: float = 2.0,
        ) -> tuple[int | None, str]:
            status, payload = orig_http(method, url, body=body, headers=headers, timeout=timeout)
            if status is not None or "Operation not permitted" not in payload:
                return status, payload
            # Fallback used only inside restricted Seatbelt sandboxes that block loopback connect().
            pid = harness.wait_for_app_ready(timeout=2.0)
            if pid is None:
                return None, payload
            parsed = urllib.parse.urlparse(url)
            if method == "GET" and parsed.path == "/healthz":
                if harness.app_exit_after_healthz:
                    with contextlib.suppress(OSError):
                        os.kill(pid, signal.SIGKILL)
                return 200, '{"status":"ok"}'
            if method == "POST":
                harness.post_counter += 1
                raw_text = (body or b"").decode("utf-8", errors="replace")
                with harness.event_log.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "tool": "som_app_post",
                                "argv": [],
                                "cwd": str(harness.root),
                                "path": parsed.path,
                                "body": raw_text,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                return harness.create_status_code, json.dumps(
                    {"id": 41 + harness.post_counter, "path": parsed.path, "received": json.loads(raw_text)}
                )
            return 404, "not found"

        buf = io.StringIO()
        patched_env = dict(os.environ)
        patched_env["PATH"] = f"{harness.bin_dir}{os.pathsep}{patched_env.get('PATH', '')}"
        with (
            mock.patch.dict(os.environ, patched_env, clear=True),
            mock.patch.object(pg_run, "http_request", side_effect=sandbox_aware_http_request),
            contextlib.redirect_stdout(buf),
        ):
            code = pg_run.main(args)
        return code, buf.getvalue()

    def test_preflight_refusals_exit_2_without_calling_initdb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeRustHarness(root)
            valid_out = root / "out_never_created"

            # 1a. Forbidden --pg-port (8765 and 18765)
            for bad_port in (8765, 18765):
                with self.subTest(flag="--pg-port", port=bad_port):
                    code, stdout = self.invoke_pg_run(
                        harness,
                        [
                            "--som",
                            str(harness.som_bin),
                            "--url",
                            TEST_URL,
                            "--cnl",
                            str(harness.cnl_file),
                            "--out",
                            str(valid_out),
                            "--vendor",
                            str(harness.vendor_dir),
                            "--pg-port",
                            str(bad_port),
                            "--app-port",
                            "18081",
                            "--create",
                            '/items={"title":"a"}',
                        ],
                    )
                    self.assertEqual(code, 2)
                    summary = json.loads(stdout.strip())
                    self.assertEqual(summary["status"], "refused")
                    self.assertFalse(summary["passed"])
                    self.assertIn("--pg-port", summary["error"])
                    self.assertFalse(valid_out.exists())
                    self.assertEqual(harness.read_events(), [])

            # 1b. Forbidden --app-port (8765 and 18765)
            for bad_port in (8765, 18765):
                with self.subTest(flag="--app-port", port=bad_port):
                    code, stdout = self.invoke_pg_run(
                        harness,
                        [
                            "--som",
                            str(harness.som_bin),
                            "--url",
                            TEST_URL,
                            "--cnl",
                            str(harness.cnl_file),
                            "--out",
                            str(valid_out),
                            "--vendor",
                            str(harness.vendor_dir),
                            "--pg-port",
                            "55432",
                            "--app-port",
                            str(bad_port),
                            "--create",
                            '/items={"title":"a"}',
                        ],
                    )
                    self.assertEqual(code, 2)
                    summary = json.loads(stdout.strip())
                    self.assertEqual(summary["status"], "refused")
                    self.assertFalse(summary["passed"])
                    self.assertIn("--app-port", summary["error"])
                    self.assertFalse(valid_out.exists())
                    self.assertEqual(harness.read_events(), [])

            # 1c. Forbidden --url port (8765 and 18765)
            for bad_port in (8765, 18765):
                with self.subTest(flag="--url", port=bad_port):
                    code, stdout = self.invoke_pg_run(
                        harness,
                        [
                            "--som",
                            str(harness.som_bin),
                            "--url",
                            f"http://127.0.0.1:{bad_port}",
                            "--cnl",
                            str(harness.cnl_file),
                            "--out",
                            str(valid_out),
                            "--vendor",
                            str(harness.vendor_dir),
                            "--pg-port",
                            "55432",
                            "--app-port",
                            "18081",
                            "--create",
                            '/items={"title":"a"}',
                        ],
                    )
                    self.assertEqual(code, 2)
                    summary = json.loads(stdout.strip())
                    self.assertEqual(summary["status"], "refused")
                    self.assertFalse(summary["passed"])
                    self.assertIn("--url", summary["error"])
                    self.assertFalse(valid_out.exists())
                    self.assertEqual(harness.read_events(), [])

            # 1d. --pg-port == --app-port
            same_port = pick_free_port()
            code_same, stdout_same = self.invoke_pg_run(
                harness,
                [
                    "--som",
                    str(harness.som_bin),
                    "--url",
                    TEST_URL,
                    "--cnl",
                    str(harness.cnl_file),
                    "--out",
                    str(valid_out),
                    "--vendor",
                    str(harness.vendor_dir),
                    "--pg-port",
                    str(same_port),
                    "--app-port",
                    str(same_port),
                    "--create",
                    '/items={"title":"a"}',
                ],
            )
            self.assertEqual(code_same, 2)
            summary_same = json.loads(stdout_same.strip())
            self.assertEqual(summary_same["status"], "refused")
            self.assertFalse(summary_same["passed"])
            self.assertFalse(valid_out.exists())
            self.assertEqual(harness.read_events(), [])

            # 1e. Existing --out directory
            existing_dir = root / "already_exists"
            existing_dir.mkdir()
            sentinel = existing_dir / "keep.txt"
            sentinel.write_text("untouched\n", encoding="utf-8")

            code_dir, stdout_dir = self.invoke_pg_run(
                harness,
                [
                    "--som",
                    str(harness.som_bin),
                    "--url",
                    TEST_URL,
                    "--cnl",
                    str(harness.cnl_file),
                    "--out",
                    str(existing_dir),
                    "--vendor",
                    str(harness.vendor_dir),
                    "--pg-port",
                    "55432",
                    "--app-port",
                    "18081",
                    "--create",
                    '/items={"title":"a"}',
                ],
            )
            self.assertEqual(code_dir, 2)
            summary_dir = json.loads(stdout_dir.strip())
            self.assertEqual(summary_dir["status"], "refused")
            self.assertFalse(summary_dir["passed"])
            self.assertIn("--out", summary_dir["error"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched\n")
            self.assertEqual(harness.read_events(), [])

            # 1f. Missing --cnl file
            missing_cnl = root / "nonexistent.cnl"
            code_cnl, stdout_cnl = self.invoke_pg_run(
                harness,
                [
                    "--som",
                    str(harness.som_bin),
                    "--url",
                    TEST_URL,
                    "--cnl",
                    str(missing_cnl),
                    "--out",
                    str(valid_out),
                    "--vendor",
                    str(harness.vendor_dir),
                    "--pg-port",
                    "55432",
                    "--app-port",
                    "18081",
                    "--create",
                    '/items={"title":"a"}',
                ],
            )
            self.assertEqual(code_cnl, 2)
            summary_cnl = json.loads(stdout_cnl.strip())
            self.assertEqual(summary_cnl["status"], "refused")
            self.assertFalse(summary_cnl["passed"])
            self.assertIn("--cnl", summary_cnl["error"])
            self.assertFalse(valid_out.exists())
            self.assertEqual(harness.read_events(), [])

            # 1g. Invalid --vendor directories (missing directory, missing config.toml, missing vendor/)
            vendor_no_config = root / "vendor_no_config"
            (vendor_no_config / "cargo-home").mkdir(parents=True)
            (vendor_no_config / "vendor").mkdir(parents=True)

            vendor_no_crates = root / "vendor_no_crates"
            (vendor_no_crates / "cargo-home").mkdir(parents=True)
            (vendor_no_crates / "cargo-home" / "config.toml").write_text("[net]\noffline = true\n", encoding="utf-8")

            for bad_vendor in (root / "vendor_missing", vendor_no_config, vendor_no_crates):
                with self.subTest(vendor=str(bad_vendor)):
                    code_v, stdout_v = self.invoke_pg_run(
                        harness,
                        [
                            "--som",
                            str(harness.som_bin),
                            "--url",
                            TEST_URL,
                            "--cnl",
                            str(harness.cnl_file),
                            "--out",
                            str(valid_out),
                            "--vendor",
                            str(bad_vendor),
                            "--pg-port",
                            "55432",
                            "--app-port",
                            "18081",
                            "--create",
                            '/items={"title":"a"}',
                        ],
                    )
                    self.assertEqual(code_v, 2)
                    summary_v = json.loads(stdout_v.strip())
                    self.assertEqual(summary_v["status"], "refused")
                    self.assertFalse(summary_v["passed"])
                    self.assertIn("--vendor", summary_v["error"])
                    self.assertFalse(valid_out.exists())
                    self.assertEqual(harness.read_events(), [])

            # 1h. Invalid --create formats
            for bad_create in ("/items", 'items={"title":"a"}', '/items={not-json}'):
                with self.subTest(create=bad_create):
                    code_c, stdout_c = self.invoke_pg_run(
                        harness,
                        [
                            "--som",
                            str(harness.som_bin),
                            "--url",
                            TEST_URL,
                            "--cnl",
                            str(harness.cnl_file),
                            "--out",
                            str(valid_out),
                            "--vendor",
                            str(harness.vendor_dir),
                            "--pg-port",
                            "55432",
                            "--app-port",
                            "18081",
                            "--create",
                            bad_create,
                        ],
                    )
                    self.assertEqual(code_c, 2)
                    summary_c = json.loads(stdout_c.strip())
                    self.assertEqual(summary_c["status"], "refused")
                    self.assertFalse(summary_c["passed"])
                    self.assertIn("--create", summary_c["error"])
                    self.assertFalse(valid_out.exists())
                    self.assertEqual(harness.read_events(), [])

            # 1i. Occupied --app-port or --pg-port refused with exit 2 before initdb
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
                squatter.bind(("127.0.0.1", 0))
                squatter.listen(1)
                busy_port = int(squatter.getsockname()[1])
                free_port = pick_free_port(exclude={busy_port})

                for pg_p, app_p, expected_flag in (
                    (free_port, busy_port, "--app-port"),
                    (busy_port, free_port, "--pg-port"),
                ):
                    with self.subTest(expected_flag=expected_flag):
                        code_b, stdout_b = self.invoke_pg_run(
                            harness,
                            [
                                "--som",
                                str(harness.som_bin),
                                "--url",
                                TEST_URL,
                                "--cnl",
                                str(harness.cnl_file),
                                "--out",
                                str(valid_out),
                                "--vendor",
                                str(harness.vendor_dir),
                                "--pg-port",
                                str(pg_p),
                                "--app-port",
                                str(app_p),
                                "--create",
                                '/items={"title":"a"}',
                            ],
                        )
                        self.assertEqual(code_b, 2)
                        summary_b = json.loads(stdout_b.strip())
                        self.assertEqual(summary_b["status"], "refused")
                        self.assertFalse(summary_b["passed"])
                        self.assertIn(expected_flag, summary_b["error"])
                        self.assertFalse(valid_out.exists())
                        self.assertEqual(harness.read_events(), [])

    def test_non_resource_shape_refused_with_exit_2_without_calling_initdb(self) -> None:
        for shape in ("capability_postgresql", "flow_postgresql"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                harness = FakeRustHarness(root, package="tasks", shape=shape)
                out_dir = root / f"out_{shape}"
                pg_port = pick_free_port()
                app_port = pick_free_port(exclude={pg_port})

                code, stdout = self.invoke_pg_run(
                    harness,
                    [
                        "--som",
                        str(harness.som_bin),
                        "--url",
                        TEST_URL,
                        "--cnl",
                        str(harness.cnl_file),
                        "--out",
                        str(out_dir),
                        "--vendor",
                        str(harness.vendor_dir),
                        "--pg-port",
                        str(pg_port),
                        "--app-port",
                        str(app_port),
                        "--create",
                        '/projects={"name":"p"}',
                    ],
                )
                self.assertEqual(code, 2, f"stdout={stdout}")
                summary = json.loads(stdout.strip())
                self.assertEqual(summary["status"], "refused")
                self.assertFalse(summary["passed"])
                self.assertIn("resource", summary["error"])
                self.assertIn("v1–v5", summary["error"])

                # Verify initdb was never called
                events = harness.read_events()
                self.assertEqual([ev["tool"] for ev in events], ["som"])

                result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(result["status"], "refused")
                self.assertFalse(result["passed"])
                self.assertEqual(result["steps_by_name"]["som_write"]["status"], "passed")
                self.assertEqual(result["steps_by_name"]["shape_check"]["status"], "refused")
                self.assertEqual(result["steps_by_name"]["initdb"]["status"], "not run")

    def test_normal_path_step_order_cargo_flags_env_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeRustHarness(root, package="items")
            out_dir = root / "run_ok"
            shared_target = root / "shared_target"
            pg_port = pick_free_port()
            app_port = pick_free_port(exclude={pg_port})

            code, stdout = self.invoke_pg_run(
                harness,
                [
                    "--som",
                    str(harness.som_bin),
                    "--url",
                    TEST_URL,
                    "--cnl",
                    str(harness.cnl_file),
                    "--out",
                    str(out_dir),
                    "--vendor",
                    str(harness.vendor_dir),
                    "--target-dir",
                    str(shared_target),
                    "--pg-port",
                    str(pg_port),
                    "--app-port",
                    str(app_port),
                    "--create",
                    '/items={"title":"first"}',
                    '/items={"title":"second"}',
                ],
            )
            self.assertEqual(code, 0, f"stdout={stdout}")
            stdout_lines = [line for line in stdout.splitlines() if line.strip()]
            self.assertEqual(len(stdout_lines), 1)
            summary = json.loads(stdout_lines[0])
            self.assertTrue(summary["passed"])
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["package"], "items")

            events = harness.read_events()
            tool_order = [ev["tool"] for ev in events]
            self.assertEqual(
                tool_order,
                [
                    "som",
                    "initdb",
                    "pg_ctl_start",
                    "createdb",
                    "createdb",
                    "cargo_build",
                    "cargo_test",
                    "som_app_start",
                    "som_app_post",
                    "som_app_post",
                    "som_app_sigterm",
                    "pg_ctl_stop",
                ],
            )

            created_dbs = [ev["db"] for ev in events if ev["tool"] == "createdb"]
            self.assertEqual(created_dbs, ["items_dev", "items_test"])

            expected_cargo_home = str((harness.vendor_dir / "cargo-home").resolve())
            expected_target_dir = str(shared_target.resolve())
            expected_dev_url = f"postgres://postgres@127.0.0.1:{pg_port}/items_dev"
            expected_test_url = f"postgres://postgres@127.0.0.1:{pg_port}/items_test"

            build_ev = next(ev for ev in events if ev["tool"] == "cargo_build")
            self.assertEqual(build_ev["argv"], ["build", "--locked", "--offline"])
            self.assertEqual(build_ev["CARGO_HOME"], expected_cargo_home)
            self.assertEqual(build_ev["CARGO_TARGET_DIR"], expected_target_dir)
            self.assertEqual(build_ev["CARGO_NET_OFFLINE"], "true")

            test_ev = next(ev for ev in events if ev["tool"] == "cargo_test")
            self.assertEqual(
                test_ev["argv"],
                ["test", "--locked", "--offline", "--", "--test-threads=1"],
            )
            self.assertEqual(test_ev["CARGO_HOME"], expected_cargo_home)
            self.assertEqual(test_ev["CARGO_TARGET_DIR"], expected_target_dir)
            self.assertEqual(test_ev["CARGO_NET_OFFLINE"], "true")
            self.assertEqual(test_ev["TEST_DATABASE_URL"], expected_test_url)

            app_ev = next(ev for ev in events if ev["tool"] == "som_app_start")
            self.assertEqual(app_ev["DATABASE_URL"], expected_dev_url)
            self.assertEqual(app_ev["SOM_APP_ADDR"], f"127.0.0.1:{app_port}")

            result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
            self.assertTrue(result["passed"])
            step_names = [s["name"] for s in result["steps"]]
            self.assertEqual(
                step_names,
                [
                    "som_write",
                    "shape_check",
                    "initdb",
                    "pg_start",
                    "createdb",
                    "cargo_build",
                    "cargo_test",
                    "app_healthz",
                    "create",
                ],
            )
            for step in result["steps"]:
                self.assertEqual(step["status"], "passed")
                self.assertTrue(step["passed"])
                self.assertEqual(step["exit_code"], 0)

            self.assertTrue(result["cleanup"]["app_stop"]["signaled"])
            self.assertEqual(result["cleanup"]["pg_stop"]["status"], "passed")
            app_pid = int(harness.app_pid_file.read_text(encoding="utf-8").strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(app_pid, 0)

    def test_binary_exiting_immediately_after_first_poll_fails_healthz_and_stops_pg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeRustHarness(root, package="items", app_exit_after_healthz=True)
            out_dir = root / "run_early_exit"
            pg_port = pick_free_port()
            app_port = pick_free_port(exclude={pg_port})

            code, stdout = self.invoke_pg_run(
                harness,
                [
                    "--som",
                    str(harness.som_bin),
                    "--url",
                    TEST_URL,
                    "--cnl",
                    str(harness.cnl_file),
                    "--out",
                    str(out_dir),
                    "--vendor",
                    str(harness.vendor_dir),
                    "--pg-port",
                    str(pg_port),
                    "--app-port",
                    str(app_port),
                    "--create",
                    '/items={"title":"first"}',
                ],
            )
            self.assertEqual(code, 1)
            summary = json.loads(stdout.strip())
            self.assertFalse(summary["passed"])
            self.assertEqual(summary["status"], "error")

            result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
            self.assertFalse(result["passed"])
            steps_by_name = result["steps_by_name"]
            self.assertEqual(steps_by_name["app_healthz"]["status"], "failed")
            self.assertFalse(steps_by_name["app_healthz"]["passed"])
            self.assertEqual(steps_by_name["create"]["status"], "not run")
            self.assertFalse(steps_by_name["create"]["passed"])

            self.assertEqual(result["cleanup"]["pg_stop"]["status"], "passed")
            events = harness.read_events()
            self.assertEqual(events[-1]["tool"], "pg_ctl_stop")

    def test_failing_cargo_test_marks_later_steps_not_run_and_stops_pg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeRustHarness(root, package="items", cargo_test_exit_code=1)
            out_dir = root / "run_cargo_test_fail"
            pg_port = pick_free_port()
            app_port = pick_free_port(exclude={pg_port})

            code, stdout = self.invoke_pg_run(
                harness,
                [
                    "--som",
                    str(harness.som_bin),
                    "--url",
                    TEST_URL,
                    "--cnl",
                    str(harness.cnl_file),
                    "--out",
                    str(out_dir),
                    "--vendor",
                    str(harness.vendor_dir),
                    "--pg-port",
                    str(pg_port),
                    "--app-port",
                    str(app_port),
                    "--create",
                    '/items={"title":"first"}',
                ],
            )
            self.assertEqual(code, 1)
            summary = json.loads(stdout.strip())
            self.assertFalse(summary["passed"])

            result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
            steps_by_name = result["steps_by_name"]
            for passed_step in ("som_write", "shape_check", "initdb", "pg_start", "createdb", "cargo_build"):
                self.assertEqual(steps_by_name[passed_step]["status"], "passed")
            self.assertEqual(steps_by_name["cargo_test"]["status"], "failed")
            for later_step in ("app_healthz", "create"):
                self.assertEqual(steps_by_name[later_step]["status"], "not run")
            self.assertEqual(result["cleanup"]["pg_stop"]["status"], "passed")

    def test_create_non_201_fails_and_cleans_up_app_and_pg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeRustHarness(root, package="items", create_status_code=422)
            out_dir = root / "run_create_fail"
            pg_port = pick_free_port()
            app_port = pick_free_port(exclude={pg_port})

            code, stdout = self.invoke_pg_run(
                harness,
                [
                    "--som",
                    str(harness.som_bin),
                    "--url",
                    TEST_URL,
                    "--cnl",
                    str(harness.cnl_file),
                    "--out",
                    str(out_dir),
                    "--vendor",
                    str(harness.vendor_dir),
                    "--pg-port",
                    str(pg_port),
                    "--app-port",
                    str(app_port),
                    "--create",
                    '/items={"title":"bad"}',
                ],
            )
            self.assertEqual(code, 1)
            summary = json.loads(stdout.strip())
            self.assertFalse(summary["passed"])

            result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["steps_by_name"]["app_healthz"]["status"], "passed")
            self.assertEqual(result["steps_by_name"]["create"]["status"], "failed")
            self.assertTrue(result["cleanup"]["app_stop"]["signaled"])
            self.assertEqual(result["cleanup"]["pg_stop"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
