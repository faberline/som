#!/usr/bin/env python3
"""Run one generated Rust `resource` backend product end to end offline on a local PostgreSQL cluster.

Supports `resource` shape products (prompts v1–v5 without capabilities, 11 files).
Runs `som write --target rust`, verifies the generated product is a `resource` shape
(`src/integrations.rs` and `src/flows.rs` are absent), initializes and starts a local
PostgreSQL cluster on `--pg-port` (`listen_addresses=127.0.0.1`), creates dev and test
databases, runs `cargo build --locked --offline` and
`cargo test --locked --offline -- --test-threads=1` against the offline `--vendor`
bundle (`CARGO_HOME=<vendor>/cargo-home`), launches the compiled `som_app` binary
directly (`DATABASE_URL`, `SOM_APP_ADDR=127.0.0.1:<app-port>`), polls `GET /healthz`,
sends `--create` POST requests, and stops both `som_app` and PostgreSQL in `finally`.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

FORBIDDEN_PORTS = {8765, 18765}
DEFAULT_HOMEBREW_PG_BIN = Path("/opt/homebrew/opt/postgresql@17/bin")
DEFAULT_HOMEBREW_RUST_BIN = Path("/opt/homebrew/Cellar/rust/1.96.1/bin")
OUTPUT_TAIL_MAX_CHARS = 2000

STEP_NAMES = (
    "som_write",
    "shape_check",
    "initdb",
    "pg_start",
    "createdb",
    "cargo_build",
    "cargo_test",
    "app_healthz",
    "create",
)

REQUIRED_RESOURCE_FILES = (
    "Cargo.toml",
    "Cargo.lock",
    "README.md",
    "src/main.rs",
    "src/lib.rs",
)

NON_RESOURCE_FILES = (
    "src/integrations.rs",
    "src/flows.rs",
)

PLACEHOLDER_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
VALID_PLACEHOLDER_EXPR_RE = re.compile(r"^([1-9][0-9]*)\.([A-Za-z_][A-Za-z0-9_]*)$")


def tail_text(stdout: str, stderr: str, limit: int = OUTPUT_TAIL_MAX_CHARS) -> str:
    combined = "\n".join(part.strip() for part in (stdout, stderr) if part and part.strip())
    if len(combined) <= limit:
        return combined
    return combined[-limit:]


def parse_package_from_readme(readme_text: str) -> str | None:
    for line in readme_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading_match = re.match(r"^#\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+service)?\s*$", stripped)
        if heading_match:
            pkg = heading_match.group(1)
            if pkg != "som_app":
                return pkg
        pkg_bullet = re.search(r"\bpackage[:\s]+`?([A-Za-z_][A-Za-z0-9_]*)`?", stripped, re.IGNORECASE)
        if pkg_bullet:
            return pkg_bullet.group(1)
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
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            return f"Refused {flag_name} {port}: port is already in use on 127.0.0.1 ({exc})"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn:
        conn.settimeout(0.2)
        rc = conn.connect_ex(("127.0.0.1", port))
    if rc == 0:
        return f"Refused {flag_name} {port}: port is already in use on 127.0.0.1 (listener active)"
    if rc == errno.EPERM:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as wildcard_probe:
            wildcard_probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                wildcard_probe.bind(("0.0.0.0", port))
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


def validate_vendor_dir(vendor_dir: Path) -> str | None:
    if not vendor_dir.is_dir():
        return f"Refused --vendor {vendor_dir}: directory does not exist"
    config_toml = vendor_dir / "cargo-home" / "config.toml"
    if not config_toml.is_file():
        return f"Refused --vendor {vendor_dir}: missing cargo-home/config.toml ({config_toml})"
    vendor_subdir = vendor_dir / "vendor"
    if not vendor_subdir.is_dir():
        return f"Refused --vendor {vendor_dir}: missing vendor/ directory ({vendor_subdir})"
    return None


def parse_create_pairs(raw_pairs: list[str]) -> tuple[list[tuple[str, str]] | None, str | None]:
    if not raw_pairs:
        return None, "Refused --create: at least one <path>=<json body> pair is required"
    parsed: list[tuple[str, str]] = []
    for idx, item in enumerate(raw_pairs, start=1):
        if "=" not in item:
            return None, f"Refused --create {item!r}: expected <path>=<json body>"
        path, body = item.split("=", 1)
        path = path.strip()
        if not path.startswith("/"):
            return None, f"Refused --create {item!r}: path must start with '/'"
        if "{" in path or "}" in path:
            stripped = PLACEHOLDER_TOKEN_RE.sub("", path)
            if "{" in stripped or "}" in stripped:
                return None, f"Refused --create {item!r}: unmatched '{{' or '}}' in path {path!r}"
            for match in PLACEHOLDER_TOKEN_RE.finditer(path):
                expr = match.group(1)
                valid_match = VALID_PLACEHOLDER_EXPR_RE.match(expr)
                if valid_match is None:
                    return (
                        None,
                        f"Refused --create {item!r}: invalid placeholder '{{{expr}}}' "
                        "(expected {<N>.<field>} with 1-based integer N)",
                    )
                ref_idx = int(valid_match.group(1))
                if ref_idx >= idx:
                    return (
                        None,
                        f"Refused --create {item!r}: placeholder '{{{expr}}}' references "
                        f"create #{ref_idx}, which does not precede create #{idx}",
                    )
        try:
            json.loads(body)
        except ValueError as exc:
            return None, f"Refused --create {item!r}: invalid JSON body ({exc})"
        parsed.append((path, body))
    return parsed, None


def resolve_create_path(
    raw_path: str,
    prior_responses: dict[int, dict[str, Any]],
) -> tuple[str | None, str | None]:
    if "{" not in raw_path:
        return raw_path, None

    missing_err: str | None = None

    def _replace(match: re.Match[str]) -> str:
        nonlocal missing_err
        expr = match.group(1)
        valid_match = VALID_PLACEHOLDER_EXPR_RE.match(expr)
        if valid_match is None:
            missing_err = f"Invalid placeholder '{{{expr}}}' in {raw_path!r}"
            return match.group(0)
        ref_idx = int(valid_match.group(1))
        field_name = valid_match.group(2)
        resp_obj = prior_responses.get(ref_idx)
        if not isinstance(resp_obj, dict) or field_name not in resp_obj:
            missing_err = (
                f"Placeholder '{{{expr}}}' in {raw_path!r}: create #{ref_idx} response "
                f"has no field {field_name!r}"
            )
            return match.group(0)
        val = resp_obj[field_name]
        if val is None or isinstance(val, (dict, list)):
            missing_err = (
                f"Placeholder '{{{expr}}}' in {raw_path!r}: create #{ref_idx} field "
                f"{field_name!r} is not a scalar value"
            )
            return match.group(0)
        return str(val)

    resolved = PLACEHOLDER_TOKEN_RE.sub(_replace, raw_path)
    if missing_err is not None:
        return None, missing_err
    return resolved, None


def check_resource_shape(product_dir: Path) -> tuple[bool, str]:
    found_non_resource = [
        rel for rel in NON_RESOURCE_FILES if (product_dir / rel).exists()
    ]
    if found_non_resource:
        return (
            False,
            f"Refused non-resource product in {product_dir} (found {', '.join(found_non_resource)}): "
            "this tool currently only supports resource shape products (prompts v1–v5 without capabilities)",
        )
    readme_path = product_dir / "README.md"
    if readme_path.is_file():
        readme_text = readme_path.read_text(encoding="utf-8", errors="replace")
        for non_res_shape in (
            "capability_postgresql",
            "capability_mongodb",
            "flow_postgresql",
            "flow_mongodb",
        ):
            if non_res_shape in readme_text:
                return (
                    False,
                    f"Refused non-resource product shape {non_res_shape!r} in {product_dir}: "
                    "this tool currently only supports resource shape products (prompts v1–v5 without capabilities)",
                )
    missing = [rel for rel in REQUIRED_RESOURCE_FILES if not (product_dir / rel).is_file()]
    if missing:
        return (
            False,
            f"Refused product in {product_dir} (missing {', '.join(missing)}): "
            "this tool currently only supports resource shape products (prompts v1–v5 without capabilities)",
        )
    return True, "product shape is resource (v1–v5 without capabilities)"


def resolve_pg_tool(name: str, env_path: str) -> str:
    found = shutil.which(name, path=env_path)
    if found:
        return found
    candidate = DEFAULT_HOMEBREW_PG_BIN / name
    if candidate.is_file():
        return str(candidate)
    return name


def resolve_rust_tool(name: str, env_path: str) -> str:
    found = shutil.which(name, path=env_path)
    if found:
        return found
    candidate = DEFAULT_HOMEBREW_RUST_BIN / name
    if candidate.is_file():
        return str(candidate)
    return name


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
    status_override: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    passed = exit_code == 0 and status_override is None
    status = status_override if status_override is not None else ("passed" if passed else "failed")
    entry: dict[str, Any] = {
        "name": name,
        "status": status,
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
    vendor_dir: Path,
    pg_port: int,
    app_port: int,
    create_specs: list[str],
    target_dir: Path | None = None,
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

    if target_dir is not None and (
        target_dir.is_symlink() or (target_dir.exists() and not target_dir.is_dir())
    ):
        return 2, {
            "status": "refused",
            "passed": False,
            "error": f"Refused --target-dir {target_dir}: must be a directory path, not a file or symlink",
        }

    if not cnl_path.is_file():
        return 2, {
            "status": "refused",
            "passed": False,
            "error": f"Refused --cnl {cnl_path}: file does not exist",
        }

    vendor_err = validate_vendor_dir(vendor_dir)
    if vendor_err is not None:
        return 2, {"status": "refused", "passed": False, "error": vendor_err}

    create_pairs, create_err = parse_create_pairs(create_specs)
    if create_err is not None or create_pairs is None:
        return 2, {"status": "refused", "passed": False, "error": create_err or "Invalid --create"}

    for port, flag in ((pg_port, "--pg-port"), (app_port, "--app-port")):
        busy_err = check_port_free(port, flag)
        if busy_err is not None:
            return 2, {"status": "refused", "passed": False, "error": busy_err}

    out_dir = out_dir.resolve()
    som_bin = som_bin.resolve()
    cnl_path = cnl_path.resolve()
    vendor_dir = vendor_dir.resolve()
    cargo_home = vendor_dir / "cargo-home"
    resolved_target_dir = target_dir.resolve() if target_dir is not None else (out_dir / "target")

    out_dir.mkdir(parents=True, exist_ok=False)
    resolved_target_dir.mkdir(parents=True, exist_ok=True)
    pgdata_dir = out_dir / "pgdata"
    pg_log = out_dir / "pg.log"
    product_dir = out_dir / "product"
    app_stdout_log = out_dir / "app.stdout.log"
    app_stderr_log = out_dir / "app.stderr.log"

    env_path = os.environ.get("PATH", "")
    initdb_bin = resolve_pg_tool("initdb", env_path)
    pg_ctl_bin = resolve_pg_tool("pg_ctl", env_path)
    createdb_bin = resolve_pg_tool("createdb", env_path)
    cargo_bin = resolve_rust_tool("cargo", env_path)

    pg_env = dict(os.environ)
    pg_env.setdefault("LC_ALL", "en_US.UTF-8")
    pg_env.setdefault("LANG", "en_US.UTF-8")

    cargo_env = dict(os.environ)
    if DEFAULT_HOMEBREW_RUST_BIN.is_dir():
        cargo_env["PATH"] = (
            f"{env_path}{os.pathsep}{DEFAULT_HOMEBREW_RUST_BIN}"
            if env_path
            else str(DEFAULT_HOMEBREW_RUST_BIN)
        )
    cargo_env["CARGO_HOME"] = str(cargo_home)
    cargo_env["CARGO_TARGET_DIR"] = str(resolved_target_dir)
    cargo_env["CARGO_NET_OFFLINE"] = "true"

    steps_map = make_not_run_steps()
    cleanup_info: dict[str, Any] = {
        "app_stop": {"signaled": False, "exit_code": None},
        "pg_stop": {"status": "not run", "argv": None, "exit_code": None, "output_tail": ""},
    }

    pg_started = False
    app_proc: subprocess.Popen[str] | None = None
    app_stdout_handle = None
    app_stderr_handle = None
    package_name: str = parse_package_from_cnl(cnl_path) or "som_app"
    refused_reason: str | None = None

    try:
        # Step 1: som write <cnl> --target rust --out <product_dir> --url <url>
        argv_write = [
            str(som_bin),
            "write",
            str(cnl_path),
            "--target",
            "rust",
            "--out",
            str(product_dir),
            "--url",
            url,
        ]
        code, tail = run_cmd(argv_write)
        if not record_step(
            steps_map,
            "som_write",
            argv=argv_write,
            exit_code=code,
            output_tail=tail,
        ):
            raise RuntimeError("som_write failed")

        readme_path = product_dir / "README.md"
        if readme_path.is_file():
            readme_pkg = parse_package_from_readme(readme_path.read_text(encoding="utf-8", errors="replace"))
            if readme_pkg:
                package_name = readme_pkg

        # Step 2: verify product is `resource` shape (before initdb)
        is_resource, shape_msg = check_resource_shape(product_dir)
        if not is_resource:
            refused_reason = shape_msg
            record_step(
                steps_map,
                "shape_check",
                argv=["check_resource_shape", str(product_dir)],
                exit_code=2,
                output_tail=shape_msg,
                status_override="refused",
            )
            raise RuntimeError("shape_check refused")
        record_step(
            steps_map,
            "shape_check",
            argv=["check_resource_shape", str(product_dir)],
            exit_code=0,
            output_tail=shape_msg,
        )

        # Step 3a: initdb
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

        # Step 3b: pg_ctl start (--pg-port, listen_addresses=127.0.0.1)
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

        # Step 3c: createdb (<package>_dev and <package>_test)
        dev_db = f"{package_name}_dev"
        test_db = f"{package_name}_test"
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
            record_step(
                steps_map,
                "createdb",
                argv=[argv_dev, argv_test],
                exit_code=code1,
                output_tail=tail1,
                extra={"databases": [dev_db, test_db]},
            )
            raise RuntimeError("createdb dev failed")
        code2, tail2 = run_cmd(argv_test, env=pg_env)
        if not record_step(
            steps_map,
            "createdb",
            argv=[argv_dev, argv_test],
            exit_code=code2,
            output_tail=tail_text(tail1, tail2),
            extra={"databases": [dev_db, test_db]},
        ):
            raise RuntimeError("createdb test failed")

        dev_url = f"postgres://postgres@127.0.0.1:{pg_port}/{dev_db}"
        test_url = f"postgres://postgres@127.0.0.1:{pg_port}/{test_db}"

        # Step 4: cargo build --locked --offline
        argv_cargo_build = [cargo_bin, "build", "--locked", "--offline"]
        code, tail = run_cmd(argv_cargo_build, cwd=product_dir, env=cargo_env)
        if not record_step(
            steps_map,
            "cargo_build",
            argv=argv_cargo_build,
            exit_code=code,
            output_tail=tail,
        ):
            raise RuntimeError("cargo_build failed")

        # Step 5: TEST_DATABASE_URL=... cargo test --locked --offline -- --test-threads=1
        cargo_test_env = {
            **cargo_env,
            "DATABASE_URL": dev_url,
            "TEST_DATABASE_URL": test_url,
        }
        argv_cargo_test = [
            cargo_bin,
            "test",
            "--locked",
            "--offline",
            "--",
            "--test-threads=1",
        ]
        code, tail = run_cmd(argv_cargo_test, cwd=product_dir, env=cargo_test_env)
        if not record_step(
            steps_map,
            "cargo_test",
            argv=argv_cargo_test,
            exit_code=code,
            output_tail=tail,
        ):
            raise RuntimeError("cargo_test failed")

        # Step 6: Launch compiled binary (<target-dir>/debug/som_app) with DATABASE_URL & SOM_APP_ADDR
        app_bin = resolved_target_dir / "debug" / "som_app"
        argv_app = [str(app_bin)]
        if not app_bin.is_file() or not os.access(app_bin, os.X_OK):
            record_step(
                steps_map,
                "app_healthz",
                argv=argv_app,
                exit_code=127,
                output_tail=f"Compiled binary not found or not executable: {app_bin}",
            )
            raise RuntimeError("compiled binary missing")

        app_env = dict(os.environ)
        app_env["DATABASE_URL"] = dev_url
        app_env["SOM_APP_ADDR"] = f"127.0.0.1:{app_port}"
        app_stdout_handle = app_stdout_log.open("w", encoding="utf-8")
        app_stderr_handle = app_stderr_log.open("w", encoding="utf-8")
        try:
            app_proc = subprocess.Popen(
                argv_app,
                cwd=str(product_dir),
                env=app_env,
                stdout=app_stdout_handle,
                stderr=app_stderr_handle,
                text=True,
            )
        except OSError as exc:
            record_step(
                steps_map,
                "app_healthz",
                argv=argv_app,
                exit_code=127,
                output_tail=str(exc),
            )
            raise RuntimeError("som_app spawn failed") from exc

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
                "app_healthz",
                argv=argv_app,
                exit_code=exit_val,
                output_tail=combined_health_tail,
            )
            raise RuntimeError("app_healthz failed")

        record_step(
            steps_map,
            "app_healthz",
            argv=argv_app,
            exit_code=0,
            output_tail=last_body[:OUTPUT_TAIL_MAX_CHARS],
            extra={"healthz_status": last_status},
        )

        # Step 7: --create requests
        create_results: list[dict[str, Any]] = []
        create_argv: list[str] = []
        create_failed = False
        create_tails: list[str] = []
        prior_create_responses: dict[int, dict[str, Any]] = {}
        for idx, (raw_path, raw_json) in enumerate(create_pairs, start=1):
            resolved_path, resolve_err = resolve_create_path(raw_path, prior_create_responses)
            if resolve_err is not None or resolved_path is None:
                create_failed = True
                create_argv.append(f"POST http://127.0.0.1:{app_port}{raw_path} {raw_json}")
                create_tails.append(resolve_err or f"POST {raw_path} failed to resolve placeholders")
                break
            target_url = f"http://127.0.0.1:{app_port}{resolved_path}"
            create_argv.append(f"POST {target_url} {raw_json}")
            if app_proc.poll() is not None:
                create_failed = True
                create_tails.append(
                    f"POST {resolved_path} aborted: som_app exited early (exit_code={app_proc.returncode})"
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
                    "path": resolved_path,
                    "status": status_code,
                    "body": resp_body,
                }
            )
            create_tails.append(f"POST {resolved_path} -> {status_code}: {resp_body}")
            if status_code != 201 or app_proc.poll() is not None:
                create_failed = True
                break
            try:
                parsed_resp = json.loads(resp_body)
            except ValueError:
                parsed_resp = None
            if isinstance(parsed_resp, dict):
                prior_create_responses[idx] = parsed_resp

        if not record_step(
            steps_map,
            "create",
            argv=create_argv,
            exit_code=1 if create_failed else 0,
            output_tail="\n".join(create_tails)[:OUTPUT_TAIL_MAX_CHARS],
            extra={"requests": create_results},
        ):
            raise RuntimeError("create step failed")

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
    if refused_reason is not None:
        result_payload: dict[str, Any] = {
            "passed": False,
            "status": "refused",
            "error": refused_reason,
            "package": package_name,
            "out": str(out_dir),
            "target_dir": str(resolved_target_dir),
            "steps": ordered_steps,
            "steps_by_name": steps_map,
            "cleanup": cleanup_info,
        }
        result_path = out_dir / "result.json"
        result_path.write_text(json.dumps(result_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary = {
            "status": "refused",
            "passed": False,
            "error": refused_reason,
            "package": package_name,
            "out": str(out_dir),
            "result": str(result_path),
            "steps": {name: steps_map[name]["status"] for name in STEP_NAMES},
            "pg_stop": cleanup_info["pg_stop"]["status"],
        }
        return 2, summary

    overall_passed = all(step["status"] == "passed" for step in ordered_steps)
    if pg_started and cleanup_info["pg_stop"]["exit_code"] not in (0, None):
        overall_passed = False

    result_payload = {
        "passed": overall_passed,
        "status": "ok" if overall_passed else "error",
        "package": package_name,
        "out": str(out_dir),
        "target_dir": str(resolved_target_dir),
        "steps": ordered_steps,
        "steps_by_name": steps_map,
        "cleanup": cleanup_info,
    }
    result_path = out_dir / "result.json"
    result_path.write_text(json.dumps(result_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = {
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
        description="Run a generated Rust `resource` backend product end to end offline on a local PostgreSQL cluster."
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
        help="Path to the `.cnl` or `.prompt.json` source file to compile and run.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New output directory (refused if it already exists).",
    )
    parser.add_argument(
        "--vendor",
        type=Path,
        required=True,
        help="Offline vendor bundle directory containing `cargo-home/config.toml` and `vendor/`.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="Optional shared `CARGO_TARGET_DIR` (defaults to `<out>/target`).",
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
        help="Local `som_app` HTTP port (ports 8765 and 18765 are refused).",
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
        vendor_dir=args.vendor,
        pg_port=args.pg_port,
        app_port=args.app_port,
        create_specs=args.create,
        target_dir=args.target_dir,
    )
    print(json.dumps(summary, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
