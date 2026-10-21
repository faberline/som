#!/usr/bin/env python3
"""Run one generated Python backend product end to end on a local PostgreSQL cluster.

Package discovery:
Reads `<package>` from the generated product's `README.md` by matching the
`uvicorn <package>.app:app` launch command (present in every generated Python
product `README.md` for prompts v3–v7) or the first-line `# <package>` /
`# <package> service` heading, with a fallback to a `DATABASE_URL` example URL
(`postgresql+psycopg://.../<package>_dev`) if present in `README.md`. When
`createdb` runs before `som write` has materialized `<out>/product/README.md`,
the initial package name is read from `Use package <package>.` in `--cnl` and
then verified against `<out>/product/README.md` immediately after `som write`
(or, if `--cnl` does not contain a `Use package` clause, `som write` runs first
so `<package>` can be read directly from `<out>/product/README.md`).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

FORBIDDEN_PORTS = {8765, 18765}
DEFAULT_HOMEBREW_PG_BIN = Path("/opt/homebrew/opt/postgresql@17/bin")
OUTPUT_TAIL_MAX_CHARS = 2000

STEP_NAMES = (
    "initdb",
    "pg_start",
    "createdb",
    "som_write",
    "alembic_upgrade",
    "unittest",
    "uvicorn_healthz",
    "create",
    "alembic_downgrade",
)


def tail_text(stdout: str, stderr: str, limit: int = OUTPUT_TAIL_MAX_CHARS) -> str:
    combined = "\n".join(part.strip() for part in (stdout, stderr) if part and part.strip())
    if len(combined) <= limit:
        return combined
    return combined[-limit:]


def parse_package_from_readme(readme_text: str) -> str | None:
    uvicorn_match = re.search(r"\buvicorn\s+([A-Za-z_][A-Za-z0-9_]*)\.app:app\b", readme_text)
    if uvicorn_match:
        return uvicorn_match.group(1)

    db_url_match = re.search(
        r"postgresql\+psycopg://[^\s\"'`]+/([A-Za-z_][A-Za-z0-9_]*?)(?:_dev|_test)?\b",
        readme_text,
    )
    if db_url_match:
        return db_url_match.group(1)

    for line in readme_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading_match = re.match(r"^#\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+service)?\s*$", stripped)
        if heading_match:
            return heading_match.group(1)
        break
    return None


def parse_package_from_cnl(cnl_path: Path) -> str | None:
    try:
        text = cnl_path.read_text(encoding="utf-8")
    except OSError:
        return None
    cnl_match = re.search(r"^\s*Use\s+package\s+([A-Za-z_][A-Za-z0-9_]*)\s*\.", text, re.MULTILINE)
    if cnl_match:
        return cnl_match.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict):
        pkg = data.get("package") or (
            data.get("prompt", {}).get("package") if isinstance(data.get("prompt"), dict) else None
        )
        if isinstance(pkg, str) and pkg:
            return pkg
    return None


def validate_port(port: int, flag_name: str) -> str | None:
    if port in FORBIDDEN_PORTS:
        return f"Refused {flag_name} {port}: ports 8765 and 18765 are forbidden"
    if port <= 0 or port > 65535:
        return f"Refused {flag_name} {port}: port must be between 1 and 65535"
    return None


def check_port_free(port: int, flag_name: str) -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            return f"Refused {flag_name} {port}: port is already in use on 127.0.0.1 ({exc})"
    return None


def validate_url(url: str) -> str | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        return f"Refused --url {url!r}: {exc}"
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return f"Refused --url {url!r}: must be an http:// or https:// URL"
    try:
        port = parsed.port
    except ValueError as exc:
        return f"Refused --url {url!r}: {exc}"
    if port in FORBIDDEN_PORTS:
        return f"Refused --url {url!r}: ports 8765 and 18765 are forbidden"
    return None


def parse_create_pairs(raw_pairs: list[str]) -> tuple[list[tuple[str, str]] | None, str | None]:
    if not raw_pairs:
        return None, "Refused --create: at least one <path>=<json body> pair is required"
    parsed: list[tuple[str, str]] = []
    for item in raw_pairs:
        if "=" not in item:
            return None, f"Refused --create {item!r}: expected <path>=<json body>"
        path, body = item.split("=", 1)
        path = path.strip()
        if not path.startswith("/"):
            return None, f"Refused --create {item!r}: path must start with '/'"
        try:
            json.loads(body)
        except ValueError as exc:
            return None, f"Refused --create {item!r}: invalid JSON body ({exc})"
        parsed.append((path, body))
    return parsed, None


def resolve_pg_tool(name: str, env_path: str) -> str:
    found = shutil.which(name, path=env_path)
    if found:
        return found
    candidate = DEFAULT_HOMEBREW_PG_BIN / name
    if candidate.is_file():
        return str(candidate)
    return name


def resolve_python_tool(python_bin_dir: Path, name: str) -> str:
    direct = python_bin_dir / name
    if direct.is_file():
        return str(direct)
    if name == "python":
        py3 = python_bin_dir / "python3"
        if py3.is_file():
            return str(py3)
    return str(direct)


def run_cmd(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode, tail_text(proc.stdout, proc.stderr)
    except OSError as exc:
        return 127, str(exc)


def http_request(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 2.0,
) -> tuple[int | None, str]:
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            return resp.status, payload
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read().decode("utf-8", errors="replace")
        except Exception:
            payload = str(exc)
        return exc.code, payload
    except Exception as exc:
        return None, str(exc)


def make_not_run_steps() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "name": name,
            "status": "not run",
            "passed": False,
            "argv": None,
            "exit_code": None,
            "output_tail": "",
        }
        for name in STEP_NAMES
    }


def record_step(
    steps_map: dict[str, dict[str, Any]],
    name: str,
    *,
    argv: Any,
    exit_code: int,
    output_tail: str,
    extra: dict[str, Any] | None = None,
) -> bool:
    passed = exit_code == 0
    entry: dict[str, Any] = {
        "name": name,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "argv": argv,
        "exit_code": exit_code,
        "output_tail": output_tail,
    }
    if extra:
        entry.update(extra)
    steps_map[name] = entry
    return passed


def run_pg_check(
    *,
    som_bin: Path,
    url: str,
    cnl_path: Path,
    out_dir: Path,
    python_bin_dir: Path,
    pg_port: int,
    app_port: int,
    create_Specs: list[str],
) -> tuple[int, dict[str, Any]]:
    for port, flag in ((pg_port, "--pg-port"), (app_port, "--app-port")):
        err = validate_port(port, flag)
        if err is not None:
            return 2, {"status": "refused", "passed": False, "error": err}
    if pg_port == app_port:
        return 2, {
            "status": "refused",
            "passed": False,
            "error": f"Refused --pg-port and --app-port: both are {pg_port}",
        }

    url_err = validate_url(url)
    if url_err is not None:
        return 2, {"status": "refused", "passed": False, "error": url_err}

    if out_dir.exists() or out_dir.is_symlink():
        return 2, {
            "status": "refused",
            "passed": False,
            "error": f"Refused --out {out_dir}: path already exists",
        }

    if not cnl_path.is_file():
        return 2, {
            "status": "refused",
            "passed": False,
            "error": f"Refused --cnl {cnl_path}: file does not exist",
        }

    create_pairs, create_err = parse_create_pairs(create_Specs)
    if create_err is not None or create_pairs is None:
        return 2, {"status": "refused", "passed": False, "error": create_err or "Invalid --create"}

    for port, flag in ((pg_port, "--pg-port"), (app_port, "--app-port")):
        busy_err = check_port_free(port, flag)
        if busy_err is not None:
            return 2, {"status": "refused", "passed": False, "error": busy_err}

    out_dir = out_dir.resolve()
    som_bin = som_bin.resolve()
    cnl_path = cnl_path.resolve()
    python_bin_dir = python_bin_dir.resolve()

    out_dir.mkdir(parents=True, exist_ok=False)
    pgdata_dir = out_dir / "pgdata"
    pg_log = out_dir / "pg.log"
    product_dir = out_dir / "product"
    app_stdout_log = out_dir / "uvicorn.stdout.log"
    app_stderr_log = out_dir / "uvicorn.stderr.log"

    env_path = os.environ.get("PATH", "")
    initdb_bin = resolve_pg_tool("initdb", env_path)
    pg_ctl_bin = resolve_pg_tool("pg_ctl", env_path)
    createdb_bin = resolve_pg_tool("createdb", env_path)

    pg_env = dict(os.environ)
    pg_env.setdefault("LC_ALL", "en_US.UTF-8")
    pg_env.setdefault("LANG", "en_US.UTF-8")

    python_exe = resolve_python_tool(python_bin_dir, "python")
    alembic_bin = resolve_python_tool(python_bin_dir, "alembic")
    uvicorn_bin = resolve_python_tool(python_bin_dir, "uvicorn")

    steps_map = make_not_run_steps()
    cleanup_info: dict[str, Any] = {
        "app_stop": {"signaled": False, "exit_code": None},
        "pg_stop": {"status": "not run", "argv": None, "exit_code": None, "output_tail": ""},
    }

    pg_started = False
    app_proc: subprocess.Popen[str] | None = None
    app_stdout_handle = None
    app_stderr_handle = None
    package_name: str | None = parse_package_from_cnl(cnl_path)

    def run_createdb_step(pkg: str) -> bool:
        dev_db = f"{pkg}_dev"
        test_db = f"{pkg}_test"
        argv_dev = [
            createdb_bin,
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_port),
            "-U",
            "postgres",
            dev_db,
        ]
        argv_test = [
            createdb_bin,
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_port),
            "-U",
            "postgres",
            test_db,
        ]
        code1, tail1 = run_cmd(argv_dev, env=pg_env)
        if code1 != 0:
            return record_step(
                steps_map,
                "createdb",
                argv=[argv_dev, argv_test],
                exit_code=code1,
                output_tail=tail1,
                extra={"databases": [dev_db, test_db]},
            )
        code2, tail2 = run_cmd(argv_test, env=pg_env)
        combined_tail = tail_text(tail1, tail2)
        return record_step(
            steps_map,
            "createdb",
            argv=[argv_dev, argv_test],
            exit_code=code2,
            output_tail=combined_tail,
            extra={"databases": [dev_db, test_db]},
        )

    def run_som_write_step() -> bool:
        argv_write = [
            str(som_bin),
            "write",
            str(cnl_path),
            "--out",
            str(product_dir),
            "--url",
            url,
        ]
        code, tail = run_cmd(argv_write)
        return record_step(
            steps_map,
            "som_write",
            argv=argv_write,
            exit_code=code,
            output_tail=tail,
        )

    try:
        # Step 1: initdb
        argv_initdb = [
            initdb_bin,
            "-D",
            str(pgdata_dir),
            "-U",
            "postgres",
            "--auth=trust",
            "-E",
            "UTF8",
        ]
        code, tail = run_cmd(argv_initdb, env=pg_env)
        if not record_step(steps_map, "initdb", argv=argv_initdb, exit_code=code, output_tail=tail):
            raise RuntimeError("initdb failed")

        # Step 2: pg_ctl start
        argv_pg_start = [
            pg_ctl_bin,
            "-D",
            str(pgdata_dir),
            "-l",
            str(pg_log),
            "-o",
            f"-p {pg_port} -k {out_dir} -c listen_addresses=127.0.0.1",
            "-w",
            "start",
        ]
        code, tail = run_cmd(argv_pg_start, env=pg_env)
        if not record_step(steps_map, "pg_start", argv=argv_pg_start, exit_code=code, output_tail=tail):
            raise RuntimeError("pg_start failed")
        pg_started = True

        # Steps 3 & 4: createdb (<package>_dev, <package>_test) and som write.
        # If `--cnl` declares `Use package <package>.`, run step 3 (createdb) then step 4 (som write)
        # in strict wall-clock order and verify `<package>` against `<out>/product/README.md`.
        # If `--cnl` has no package declaration (e.g. a synthetic test input), run `som write` first
        # so `<package>` can be read from `<out>/product/README.md`, then run `createdb`.
        if package_name is not None:
            if not run_createdb_step(package_name):
                raise RuntimeError("createdb failed")
            if not run_som_write_step():
                raise RuntimeError("som_write failed")
            readme_path = product_dir / "README.md"
            if readme_path.is_file():
                readme_pkg = parse_package_from_readme(readme_path.read_text(encoding="utf-8"))
                if readme_pkg is not None and readme_pkg != package_name:
                    package_name = readme_pkg
                    if not run_createdb_step(package_name):
                        raise RuntimeError("createdb failed")
        else:
            if not run_som_write_step():
                raise RuntimeError("som_write failed")
            readme_path = product_dir / "README.md"
            if readme_path.is_file():
                package_name = parse_package_from_readme(readme_path.read_text(encoding="utf-8"))
            if not package_name:
                record_step(
                    steps_map,
                    "createdb",
                    argv=[],
                    exit_code=1,
                    output_tail=f"Could not determine <package> from {readme_path}",
                )
                raise RuntimeError("package discovery failed")
            if not run_createdb_step(package_name):
                raise RuntimeError("createdb failed")

        readme_path = product_dir / "README.md"
        if not readme_path.is_file():
            steps_map["som_write"]["status"] = "failed"
            steps_map["som_write"]["passed"] = False
            steps_map["som_write"]["exit_code"] = 1
            steps_map["som_write"]["output_tail"] = f"Missing {readme_path}"
            raise RuntimeError("missing README.md")

        readme_pkg = parse_package_from_readme(readme_path.read_text(encoding="utf-8"))
        if readme_pkg:
            package_name = readme_pkg
        if not package_name:
            raise RuntimeError("could not determine package name")

        dev_url = f"postgresql+psycopg://postgres@127.0.0.1:{pg_port}/{package_name}_dev"
        test_url = f"postgresql+psycopg://postgres@127.0.0.1:{pg_port}/{package_name}_test"
        product_env = dict(os.environ)
        product_env["DATABASE_URL"] = dev_url
        product_env["TEST_DATABASE_URL"] = test_url
        pythonpath_parts = [str(product_dir / "src"), str(product_dir)] if (product_dir / "src").is_dir() else [str(product_dir)]
        if product_env.get("PYTHONPATH"):
            pythonpath_parts.append(product_env["PYTHONPATH"])
        product_env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

        # Step 5: alembic upgrade head
        argv_upgrade = [alembic_bin, "upgrade", "head"]
        code, tail = run_cmd(argv_upgrade, cwd=product_dir, env=product_env)
        if not record_step(
            steps_map,
            "alembic_upgrade",
            argv=argv_upgrade,
            exit_code=code,
            output_tail=tail,
        ):
            raise RuntimeError("alembic upgrade failed")

        # Step 6: python -m unittest discover -s tests -v
        argv_unittest = [python_exe, "-m", "unittest", "discover", "-s", "tests", "-v"]
        code, tail = run_cmd(argv_unittest, cwd=product_dir, env=product_env)
        if not record_step(
            steps_map,
            "unittest",
            argv=argv_unittest,
            exit_code=code,
            output_tail=tail,
        ):
            raise RuntimeError("unittest failed")

        # Step 7: Start uvicorn <package>.app:app --port <app-port> and poll GET /healthz up to 10s
        argv_uvicorn = [uvicorn_bin, f"{package_name}.app:app", "--port", str(app_port)]
        app_stdout_handle = app_stdout_log.open("w", encoding="utf-8")
        app_stderr_handle = app_stderr_log.open("w", encoding="utf-8")
        try:
            app_proc = subprocess.Popen(
                argv_uvicorn,
                cwd=str(product_dir),
                env=product_env,
                stdout=app_stdout_handle,
                stderr=app_stderr_handle,
                text=True,
            )
        except OSError as exc:
            record_step(
                steps_map,
                "uvicorn_healthz",
                argv=argv_uvicorn,
                exit_code=127,
                output_tail=str(exc),
            )
            raise RuntimeError("uvicorn spawn failed") from exc

        healthz_url = f"http://127.0.0.1:{app_port}/healthz"
        deadline = time.monotonic() + 10.0
        health_ok = False
        last_status: int | None = None
        last_body = ""
        while time.monotonic() < deadline:
            if app_proc.poll() is not None:
                break
            status_code, body = http_request("GET", healthz_url, timeout=1.0)
            last_status = status_code
            last_body = body
            if status_code == 200:
                time.sleep(0.1)
                if app_proc.poll() is None:
                    health_ok = True
                break
            time.sleep(0.1)

        if not health_ok or app_proc.poll() is not None:
            proc_code = app_proc.poll()
            exit_val = proc_code if (proc_code is not None and proc_code != 0) else 1
            if app_stdout_handle is not None:
                app_stdout_handle.flush()
            if app_stderr_handle is not None:
                app_stderr_handle.flush()
            log_tail = tail_text(
                app_stdout_log.read_text(encoding="utf-8", errors="replace") if app_stdout_log.is_file() else "",
                app_stderr_log.read_text(encoding="utf-8", errors="replace") if app_stderr_log.is_file() else "",
            )
            combined_health_tail = tail_text(
                f"GET /healthz status={last_status} body={last_body}",
                log_tail,
            )
            record_step(
                steps_map,
                "uvicorn_healthz",
                argv=argv_uvicorn,
                exit_code=exit_val,
                output_tail=combined_health_tail,
            )
            raise RuntimeError("uvicorn healthz failed")

        record_step(
            steps_map,
            "uvicorn_healthz",
            argv=argv_uvicorn,
            exit_code=0,
            output_tail=last_body[:OUTPUT_TAIL_MAX_CHARS],
            extra={"healthz_status": last_status},
        )

        # Step 8: --create requests
        create_results: list[dict[str, Any]] = []
        create_argv: list[str] = []
        create_failed = False
        create_tails: list[str] = []
        for path, raw_json in create_pairs:
            target_url = f"http://127.0.0.1:{app_port}{path}"
            create_argv.append(f"POST {target_url} {raw_json}")
            if app_proc.poll() is not None:
                create_failed = True
                create_tails.append(
                    f"POST {path} aborted: uvicorn exited early (exit_code={app_proc.returncode})"
                )
                break
            status_code, resp_body = http_request(
                "POST",
                target_url,
                body=raw_json.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            create_results.append(
                {
                    "path": path,
                    "status": status_code,
                    "body": resp_body,
                }
            )
            create_tails.append(f"POST {path} -> {status_code}: {resp_body}")
            if status_code != 201 or app_proc.poll() is not None:
                create_failed = True
                break

        if not record_step(
            steps_map,
            "create",
            argv=create_argv,
            exit_code=1 if create_failed else 0,
            output_tail="\n".join(create_tails)[:OUTPUT_TAIL_MAX_CHARS],
            extra={"requests": create_results},
        ):
            raise RuntimeError("create step failed")

        # Step 9: alembic downgrade base
        argv_downgrade = [alembic_bin, "downgrade", "base"]
        code, tail = run_cmd(argv_downgrade, cwd=product_dir, env=product_env)
        if not record_step(
            steps_map,
            "alembic_downgrade",
            argv=argv_downgrade,
            exit_code=code,
            output_tail=tail,
        ):
            raise RuntimeError("alembic downgrade failed")

    except RuntimeError:
        pass
    finally:
        if app_proc is not None:
            if app_proc.poll() is None:
                app_proc.send_signal(signal.SIGTERM)
                try:
                    app_proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    app_proc.kill()
                    app_proc.wait(timeout=2.0)
            cleanup_info["app_stop"] = {
                "signaled": True,
                "pid": app_proc.pid,
                "exit_code": app_proc.returncode,
            }
        if app_stdout_handle is not None:
            app_stdout_handle.close()
        if app_stderr_handle is not None:
            app_stderr_handle.close()

        if pg_started:
            argv_pg_stop = [
                pg_ctl_bin,
                "-D",
                str(pgdata_dir),
                "-m",
                "fast",
                "-w",
                "stop",
            ]
            stop_code, stop_tail = run_cmd(argv_pg_stop, env=pg_env)
            cleanup_info["pg_stop"] = {
                "status": "passed" if stop_code == 0 else "failed",
                "argv": argv_pg_stop,
                "exit_code": stop_code,
                "output_tail": stop_tail,
            }

    ordered_steps = [steps_map[name] for name in STEP_NAMES]
    overall_passed = all(step["status"] == "passed" for step in ordered_steps)
    if pg_started and cleanup_info["pg_stop"]["exit_code"] not in (0, None):
        overall_passed = False

    result_payload: dict[str, Any] = {
        "passed": overall_passed,
        "status": "ok" if overall_passed else "error",
        "package": package_name,
        "out": str(out_dir),
        "steps": ordered_steps,
        "steps_by_name": steps_map,
        "cleanup": cleanup_info,
    }
    result_path = out_dir / "result.json"
    result_path.write_text(json.dumps(result_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary: dict[str, Any] = {
        "status": "ok" if overall_passed else "error",
        "passed": overall_passed,
        "package": package_name,
        "out": str(out_dir),
        "result": str(result_path),
        "steps": {name: steps_map[name]["status"] for name in STEP_NAMES},
        "pg_stop": cleanup_info["pg_stop"]["status"],
    }
    return (0 if overall_passed else 1), summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a generated Python backend product end to end on a local PostgreSQL cluster."
    )
    parser.add_argument(
        "--som",
        type=Path,
        required=True,
        help="Path to the `som` CLI binary.",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Base URL of the SOM compiler HTTP service (ports 8765 and 18765 are refused).",
    )
    parser.add_argument(
        "--cnl",
        type=Path,
        required=True,
        help="Path to the `.cnl` source file to compile and run.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New output directory (refused if it already exists).",
    )
    parser.add_argument(
        "--python-bin",
        type=Path,
        default=Path(sys.executable).resolve().parent,
        help="Directory holding `python`, `alembic`, and `uvicorn` (defaults to the directory of sys.executable).",
    )
    parser.add_argument(
        "--pg-port",
        type=int,
        required=True,
        help="Local PostgreSQL port (ports 8765 and 18765 are refused).",
    )
    parser.add_argument(
        "--app-port",
        type=int,
        required=True,
        help="Local uvicorn port (ports 8765 and 18765 are refused).",
    )
    parser.add_argument(
        "--create",
        action="extend",
        nargs="+",
        required=True,
        metavar="PATH=JSON",
        help="One or more `<path>=<json body>` pairs to POST after startup (expects HTTP 201).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return code

    code, summary = run_pg_check(
        som_bin=args.som,
        url=args.url,
        cnl_path=args.cnl,
        out_dir=args.out,
        python_bin_dir=args.python_bin,
        pg_port=args.pg_port,
        app_port=args.app_port,
        create_Specs=args.create,
    )
    print(json.dumps(summary, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
