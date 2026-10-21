#!/usr/bin/env python3
"""Unit tests for som-rs/scripts/python_quickstart/pg_run.py."""

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


class FakeHarness:
    """Creates fake PostgreSQL, Python, and SOM executables in a temp directory."""

    def __init__(
        self,
        root: Path,
        *,
        package: str = "todos",
        unittest_exit_code: int = 0,
        create_status_code: int = 201,
        uvicorn_exit_after_healthz: bool = False,
    ) -> None:
        self.root = root
        self.package = package
        self.unittest_exit_code = unittest_exit_code
        self.create_status_code = create_status_code
        self.uvicorn_exit_after_healthz = uvicorn_exit_after_healthz
        self.pg_bin = root / "pg_bin"
        self.py_bin = root / "py_bin"
        self.pg_bin.mkdir(parents=True, exist_ok=True)
        self.py_bin.mkdir(parents=True, exist_ok=True)
        self.event_log = root / "events.jsonl"
        self.uvicorn_pid_file = root / "uvicorn.pid"
        self.cnl_file = root / "sample.cnl"
        self.cnl_file.write_text(
            f"Use SOM CNL version 1.\nUse prompt version 3.\nUse package {package}.\nEnd request.\n",
            encoding="utf-8",
        )
        self.som_bin = self._create_fake_som()
        self._create_pg_tools()
        self._create_py_tools(
            unittest_exit_code=unittest_exit_code,
            create_status_code=create_status_code,
            uvicorn_exit_after_healthz=uvicorn_exit_after_healthz,
        )

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
            '        "DATABASE_URL": os.environ.get("DATABASE_URL"),',
            '        "TEST_DATABASE_URL": os.environ.get("TEST_DATABASE_URL"),',
            "    }",
            "    if extra:",
            "        payload.update(extra)",
            '    with EVENT_LOG.open("a", encoding="utf-8") as fh:',
            '        fh.write(json.dumps(payload, sort_keys=True) + "\\n")',
        ]

    def _create_fake_som(self) -> Path:
        return write_executable(
            self.root / "fake_som",
            [
                *self._header_lines(),
                "argv = sys.argv[1:]",
                'record_event("som")',
                'if len(argv) != 6 or argv[0] != "write" or argv[2] != "--out" or argv[4] != "--url":',
                '    sys.stderr.write(f"unexpected som args: {argv!r}\\n")',
                "    sys.exit(2)",
                "out_dir = Path(argv[3])",
                "out_dir.mkdir(parents=True, exist_ok=True)",
                "readme = (",
                f'    "# {self.package} service\\n\\n"',
                '    "```sh\\n"',
                f'    \'export DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{self.package}_dev"\\n\'',
                f'    \'export TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{self.package}_test"\\n\'',
                '    "uv run alembic upgrade head\\n"',
                f'    "uv run uvicorn {self.package}.app:app\\n"',
                '    "uv run python -m unittest discover -s tests -v\\n"',
                '    "```\\n"',
                ")",
                '(out_dir / "README.md").write_text(readme, encoding="utf-8")',
                f'(out_dir / "pyproject.toml").write_text(\'[project]\\nname = "{self.package}"\\n\', encoding="utf-8")',
                'tests_dir = out_dir / "tests"',
                "tests_dir.mkdir(parents=True, exist_ok=True)",
                '(tests_dir / "test_api.py").write_text("import unittest\\n", encoding="utf-8")',
                'print(json.dumps({"status": "ok", "files": 3, "out": str(out_dir), "target": "python"}))',
            ],
        )

    def _create_pg_tools(self) -> None:
        write_executable(
            self.pg_bin / "initdb",
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
            self.pg_bin / "pg_ctl",
            [
                *self._header_lines(),
                "argv = sys.argv[1:]",
                'action = argv[-1] if argv else "unknown"',
                'record_event(f"pg_ctl_{action}")',
                'print(f"pg_ctl {action} ok")',
            ],
        )
        write_executable(
            self.pg_bin / "createdb",
            [
                *self._header_lines(),
                "argv = sys.argv[1:]",
                'db_name = argv[-1] if argv else ""',
                'record_event("createdb", {"db": db_name})',
                'print(f"createdb {db_name} ok")',
            ],
        )

    def _create_py_tools(
        self,
        *,
        unittest_exit_code: int,
        create_status_code: int,
        uvicorn_exit_after_healthz: bool,
    ) -> None:
        write_executable(
            self.py_bin / "alembic",
            [
                *self._header_lines(),
                "argv = sys.argv[1:]",
                'subcmd = argv[0] if argv else "unknown"',
                'record_event(f"alembic_{subcmd}")',
                'print(f"alembic {\' \'.join(argv)} ok")',
            ],
        )
        write_executable(
            self.py_bin / "python",
            [
                *self._header_lines(),
                'record_event("python_unittest")',
                'print("ran fake unittest suite")',
                f"sys.exit({unittest_exit_code})",
            ],
        )
        write_executable(
            self.py_bin / "uvicorn",
            [
                *self._header_lines(),
                "from http.server import BaseHTTPRequestHandler, HTTPServer",
                "import signal",
                "argv = sys.argv[1:]",
                'port = int(argv[argv.index("--port") + 1])',
                f"EXIT_AFTER_HEALTHZ = {uvicorn_exit_after_healthz!r}",
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
                '        length = int(self.headers.get("Content-Length", "0"))',
                '        raw = self.rfile.read(length).decode("utf-8", errors="replace")',
                '        record_event("uvicorn_post", {"path": self.path, "body": raw})',
                '        resp_payload = json.dumps({"path": self.path, "received": json.loads(raw)}).encode("utf-8")',
                f"        self.send_response({create_status_code})",
                '        self.send_header("Content-Type", "application/json")',
                '        self.send_header("Content-Length", str(len(resp_payload)))',
                "        self.end_headers()",
                "        self.wfile.write(resp_payload)",
                'server = HTTPServer(("127.0.0.1", port), Handler)',
                f"PID_FILE = Path({str(self.uvicorn_pid_file)!r})",
                'PID_FILE.write_text(str(os.getpid()), encoding="utf-8")',
                'record_event("uvicorn_start", {"pid": os.getpid()})',
                "def handle_sigterm(signum, frame):",
                '    record_event("uvicorn_sigterm", {"pid": os.getpid()})',
                "    sys.exit(0)",
                "signal.signal(signal.SIGTERM, handle_sigterm)",
                "server.serve_forever()",
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

    def wait_for_uvicorn_ready(self, timeout: float = 5.0) -> int | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.uvicorn_pid_file.is_file():
                raw = self.uvicorn_pid_file.read_text(encoding="utf-8").strip()
                if raw.isdigit():
                    pid = int(raw)
                    try:
                        os.kill(pid, 0)
                        return pid
                    except OSError:
                        pass
            time.sleep(0.05)
        return None


class PgRunTest(unittest.TestCase):
    def invoke_pg_run(
        self,
        harness: FakeHarness,
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
            pid = harness.wait_for_uvicorn_ready(timeout=2.0)
            if pid is None:
                return None, payload
            parsed = urllib.parse.urlparse(url)
            if method == "GET" and parsed.path == "/healthz":
                if harness.uvicorn_exit_after_healthz:
                    with contextlib.suppress(OSError):
                        os.kill(pid, signal.SIGKILL)
                return 200, '{"status":"ok"}'
            if method == "POST":
                raw_text = (body or b"").decode("utf-8", errors="replace")
                with harness.event_log.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "tool": "uvicorn_post",
                                "argv": [],
                                "cwd": str(harness.root),
                                "DATABASE_URL": None,
                                "TEST_DATABASE_URL": None,
                                "path": parsed.path,
                                "body": raw_text,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                return harness.create_status_code, json.dumps(
                    {"path": parsed.path, "received": json.loads(raw_text)}
                )
            return 404, "not found"

        buf = io.StringIO()
        patched_env = dict(os.environ)
        patched_env["PATH"] = f"{harness.pg_bin}{os.pathsep}{patched_env.get('PATH', '')}"
        with (
            mock.patch.dict(os.environ, patched_env, clear=True),
            mock.patch.object(pg_run, "http_request", side_effect=sandbox_aware_http_request),
            contextlib.redirect_stdout(buf),
        ):
            code = pg_run.main(args)
        return code, buf.getvalue()

    def test_port_refusals_and_existing_out_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeHarness(root)
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
                            "--python-bin",
                            str(harness.py_bin),
                            "--pg-port",
                            str(bad_port),
                            "--app-port",
                            "18081",
                            "--create",
                            '/todos={"title":"a"}',
                        ],
                    )
                    self.assertEqual(code, 2)
                    summary = json.loads(stdout.strip())
                    self.assertFalse(summary["passed"])
                    self.assertIn("--pg-port", summary["error"])
                    self.assertFalse(valid_out.exists())

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
                            "--python-bin",
                            str(harness.py_bin),
                            "--pg-port",
                            "55432",
                            "--app-port",
                            str(bad_port),
                            "--create",
                            '/todos={"title":"a"}',
                        ],
                    )
                    self.assertEqual(code, 2)
                    summary = json.loads(stdout.strip())
                    self.assertFalse(summary["passed"])
                    self.assertIn("--app-port", summary["error"])
                    self.assertFalse(valid_out.exists())

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
                            "--python-bin",
                            str(harness.py_bin),
                            "--pg-port",
                            "55432",
                            "--app-port",
                            "18081",
                            "--create",
                            '/todos={"title":"a"}',
                        ],
                    )
                    self.assertEqual(code, 2)
                    summary = json.loads(stdout.strip())
                    self.assertFalse(summary["passed"])
                    self.assertIn("--url", summary["error"])
                    self.assertFalse(valid_out.exists())

            # 1d. Existing --out directory
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
                    "--python-bin",
                    str(harness.py_bin),
                    "--pg-port",
                    "55432",
                    "--app-port",
                    "18081",
                    "--create",
                    '/todos={"title":"a"}',
                ],
            )
            self.assertEqual(code_dir, 2)
            summary_dir = json.loads(stdout_dir.strip())
            self.assertFalse(summary_dir["passed"])
            self.assertIn("--out", summary_dir["error"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched\n")
            self.assertEqual(harness.read_events(), [])

            # 1e. Occupied --app-port is refused with exit 2 before initdb
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
                squatter.bind(("127.0.0.1", 0))
                squatter.listen(1)
                busy_app_port = int(squatter.getsockname()[1])
                free_pg_port = pick_free_port(exclude={busy_app_port})

                code_app_busy, stdout_app_busy = self.invoke_pg_run(
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
                        "--python-bin",
                        str(harness.py_bin),
                        "--pg-port",
                        str(free_pg_port),
                        "--app-port",
                        str(busy_app_port),
                        "--create",
                        '/todos={"title":"a"}',
                    ],
                )
                self.assertEqual(code_app_busy, 2)
                summary_app_busy = json.loads(stdout_app_busy.strip())
                self.assertEqual(summary_app_busy["status"], "refused")
                self.assertFalse(summary_app_busy["passed"])
                self.assertIn("--app-port", summary_app_busy["error"])
                self.assertFalse(valid_out.exists())
                self.assertEqual(harness.read_events(), [])

            # 1f. Occupied --pg-port is refused with exit 2 before initdb
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
                squatter.bind(("127.0.0.1", 0))
                squatter.listen(1)
                busy_pg_port = int(squatter.getsockname()[1])
                free_app_port = pick_free_port(exclude={busy_pg_port})

                code_pg_busy, stdout_pg_busy = self.invoke_pg_run(
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
                        "--python-bin",
                        str(harness.py_bin),
                        "--pg-port",
                        str(busy_pg_port),
                        "--app-port",
                        str(free_app_port),
                        "--create",
                        '/todos={"title":"a"}',
                    ],
                )
                self.assertEqual(code_pg_busy, 2)
                summary_pg_busy = json.loads(stdout_pg_busy.strip())
                self.assertEqual(summary_pg_busy["status"], "refused")
                self.assertFalse(summary_pg_busy["passed"])
                self.assertIn("--pg-port", summary_pg_busy["error"])
                self.assertFalse(valid_out.exists())
                self.assertEqual(harness.read_events(), [])

    def test_step_order_env_vars_result_json_and_app_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeHarness(root, package="todos")
            out_dir = root / "run_ok"
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
                    "--python-bin",
                    str(harness.py_bin),
                    "--pg-port",
                    str(pg_port),
                    "--app-port",
                    str(app_port),
                    "--create",
                    '/todos={"title":"first"}',
                    '/todos={"title":"second"}',
                ],
            )
            self.assertEqual(code, 0, f"stdout={stdout}")
            stdout_lines = [line for line in stdout.splitlines() if line.strip()]
            self.assertEqual(len(stdout_lines), 1)
            summary = json.loads(stdout_lines[0])
            self.assertTrue(summary["passed"])
            self.assertEqual(summary["status"], "ok")

            # Verify wall-clock tool invocation order
            events = harness.read_events()
            tool_order = [ev["tool"] for ev in events]
            self.assertEqual(
                tool_order,
                [
                    "initdb",
                    "pg_ctl_start",
                    "createdb",
                    "createdb",
                    "som",
                    "alembic_upgrade",
                    "python_unittest",
                    "uvicorn_start",
                    "uvicorn_post",
                    "uvicorn_post",
                    "alembic_downgrade",
                    "uvicorn_sigterm",
                    "pg_ctl_stop",
                ],
            )

            # Verify createdb databases
            created_dbs = [ev["db"] for ev in events if ev["tool"] == "createdb"]
            self.assertEqual(created_dbs, ["todos_dev", "todos_test"])

            # Verify DATABASE_URL and TEST_DATABASE_URL passed to alembic and unittest
            expected_dev_url = f"postgresql+psycopg://postgres@127.0.0.1:{pg_port}/todos_dev"
            expected_test_url = f"postgresql+psycopg://postgres@127.0.0.1:{pg_port}/todos_test"
            for tool_name in ("alembic_upgrade", "python_unittest", "alembic_downgrade"):
                matching = [ev for ev in events if ev["tool"] == tool_name]
                self.assertEqual(len(matching), 1)
                self.assertEqual(matching[0]["DATABASE_URL"], expected_dev_url)
                self.assertEqual(matching[0]["TEST_DATABASE_URL"], expected_test_url)

            # Verify <out>/result.json structure and steps
            result_path = out_dir / "result.json"
            self.assertTrue(result_path.is_file())
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertTrue(result["passed"])
            step_names = [s["name"] for s in result["steps"]]
            self.assertEqual(
                step_names,
                [
                    "initdb",
                    "pg_start",
                    "createdb",
                    "som_write",
                    "alembic_upgrade",
                    "unittest",
                    "uvicorn_healthz",
                    "create",
                    "alembic_downgrade",
                ],
            )
            for step in result["steps"]:
                self.assertEqual(step["status"], "passed")
                self.assertTrue(step["passed"])
                self.assertEqual(step["exit_code"], 0)
                self.assertIsNotNone(step["argv"])
                self.assertIsInstance(step["output_tail"], str)

            # Verify the app process was stopped at the end
            self.assertTrue(result["cleanup"]["app_stop"]["signaled"])
            uvicorn_pid = int(harness.uvicorn_pid_file.read_text(encoding="utf-8").strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(uvicorn_pid, 0)

    def test_failing_unittest_marks_later_steps_not_run_and_stops_pg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeHarness(root, package="todos", unittest_exit_code=1)
            out_dir = root / "run_unittest_fail"
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
                    "--python-bin",
                    str(harness.py_bin),
                    "--pg-port",
                    str(pg_port),
                    "--app-port",
                    str(app_port),
                    "--create",
                    '/todos={"title":"first"}',
                ],
            )
            self.assertEqual(code, 1)
            summary = json.loads(stdout.strip())
            self.assertFalse(summary["passed"])
            self.assertEqual(summary["status"], "error")

            result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
            self.assertFalse(result["passed"])
            steps_by_name = {s["name"]: s for s in result["steps"]}

            for passed_step in ("initdb", "pg_start", "createdb", "som_write", "alembic_upgrade"):
                self.assertEqual(steps_by_name[passed_step]["status"], "passed", steps_by_name)
                self.assertTrue(steps_by_name[passed_step]["passed"])

            self.assertEqual(steps_by_name["unittest"]["status"], "failed")
            self.assertFalse(steps_by_name["unittest"]["passed"])
            self.assertEqual(steps_by_name["unittest"]["exit_code"], 1)

            for later_step in ("uvicorn_healthz", "create", "alembic_downgrade"):
                self.assertEqual(steps_by_name[later_step]["status"], "not run")
                self.assertFalse(steps_by_name[later_step]["passed"])
                self.assertIsNone(steps_by_name[later_step]["exit_code"])

            # Verify pg_ctl stop still ran
            self.assertEqual(result["cleanup"]["pg_stop"]["status"], "passed")
            self.assertEqual(result["cleanup"]["pg_stop"]["exit_code"], 0)
            events = harness.read_events()
            self.assertEqual(events[-1]["tool"], "pg_ctl_stop")

    def test_create_non_201_fails_and_cleans_up_app_and_pg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeHarness(root, package="todos", create_status_code=422)
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
                    "--python-bin",
                    str(harness.py_bin),
                    "--pg-port",
                    str(pg_port),
                    "--app-port",
                    str(app_port),
                    "--create",
                    '/todos={"title":"bad"}',
                ],
            )
            self.assertEqual(code, 1)
            summary = json.loads(stdout.strip())
            self.assertFalse(summary["passed"])

            result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
            self.assertFalse(result["passed"])
            steps_by_name = {s["name"]: s for s in result["steps"]}

            self.assertEqual(steps_by_name["uvicorn_healthz"]["status"], "passed", steps_by_name)
            self.assertEqual(steps_by_name["create"]["status"], "failed")
            self.assertFalse(steps_by_name["create"]["passed"])
            self.assertEqual(steps_by_name["create"]["exit_code"], 1)
            self.assertEqual(steps_by_name["alembic_downgrade"]["status"], "not run")
            self.assertFalse(steps_by_name["alembic_downgrade"]["passed"])

            # Both uvicorn and pg_ctl stop must have run in cleanup
            self.assertTrue(result["cleanup"]["app_stop"]["signaled"])
            self.assertEqual(result["cleanup"]["pg_stop"]["status"], "passed")
            uvicorn_pid = int(harness.uvicorn_pid_file.read_text(encoding="utf-8").strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(uvicorn_pid, 0)

    def test_uvicorn_exiting_immediately_after_first_poll_fails_healthz_and_stops_pg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = FakeHarness(root, package="todos", uvicorn_exit_after_healthz=True)
            out_dir = root / "run_uvicorn_early_exit"
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
                    "--python-bin",
                    str(harness.py_bin),
                    "--pg-port",
                    str(pg_port),
                    "--app-port",
                    str(app_port),
                    "--create",
                    '/todos={"title":"first"}',
                ],
            )
            self.assertEqual(code, 1)
            summary = json.loads(stdout.strip())
            self.assertFalse(summary["passed"])
            self.assertEqual(summary["status"], "error")

            result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
            self.assertFalse(result["passed"])
            steps_by_name = {s["name"]: s for s in result["steps"]}

            self.assertEqual(steps_by_name["uvicorn_healthz"]["status"], "failed", steps_by_name)
            self.assertFalse(steps_by_name["uvicorn_healthz"]["passed"])
            self.assertEqual(steps_by_name["create"]["status"], "not run")
            self.assertFalse(steps_by_name["create"]["passed"])
            self.assertEqual(steps_by_name["alembic_downgrade"]["status"], "not run")
            self.assertFalse(steps_by_name["alembic_downgrade"]["passed"])

            self.assertEqual(result["cleanup"]["pg_stop"]["status"], "passed")
            events = harness.read_events()
            self.assertEqual(events[-1]["tool"], "pg_ctl_stop")


if __name__ == "__main__":
    unittest.main()
