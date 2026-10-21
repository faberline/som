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

Test database migration & downgrade judgment (v3–v4 vs v5–v7):
Before running `unittest`, `pg_run.py` always runs `alembic upgrade head` against
`TEST_DATABASE_URL` (recorded as step `alembic_upgrade_test`). In v5–v7 products,
the generated `tests/` suite relies on this migration and leaves the schema in
place after `unittest`, so step `alembic_downgrade` runs `alembic downgrade base`
against both `<package>_dev` and `<package>_test`. In v3 and v4 products,
`ServiceTest` in `tests/` calls `Base.metadata.create_all(engine)` in `setUp()`
(which is a no-op with `checkfirst=True` when `alembic_upgrade_test` has already
migrated the test database) and calls `Base.metadata.drop_all(engine)` in
`tearDown()`, dropping the ORM tables while leaving `alembic_version` at `head`.
Therefore, `tests_drop_schema_in_teardown()` inspects `tests/*.py` for
`Base.metadata.drop_all`: when present (v3/v4), `alembic_downgrade` only runs
`alembic downgrade base` on `<package>_dev` because `tearDown()` already dropped
the `<package>_test` tables; when absent (v5–v7), `alembic_downgrade` runs
`alembic downgrade base` on both `<package>_dev` and `<package>_test`.
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
REDACTED = "<redacted>"

STEP_NAMES = (
    "initdb",
    "pg_start",
    "createdb",
    "som_write",
    "alembic_upgrade",
    "alembic_upgrade_test",
    "unittest",
    "uvicorn_healthz",
    "create",
    "alembic_downgrade",
)

UNSUPPORTED_CAPABILITY_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"^\s*Use\s+database\s+mongodb\s*\.", re.MULTILINE), "database", "mongodb"),
    (re.compile(r"^\s*Use\s+authentication\s+firebase\s*\.", re.MULTILINE), "authentication", "firebase"),
    (
        re.compile(r"^\s*Use\s+authentication\s+google_oauth\s*\.", re.MULTILINE),
        "authentication",
        "google_oauth",
    ),
    (re.compile(r"^\s*Use\s+object\s+storage\s+s3\s*\.", re.MULTILINE), "object storage", "s3"),
    (re.compile(r"^\s*Use\s+object\s+storage\s+gcs\s*\.", re.MULTILINE), "object storage", "gcs"),
)

PLACEHOLDER_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
VALID_PLACEHOLDER_EXPR_RE = re.compile(r"^([1-9][0-9]*)\.([A-Za-z_][A-Za-z0-9_]*)$")

JWT_BOOTSTRAP_SCRIPT = """
import json
import os
from pathlib import Path
import sys
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

auth_dir = Path(sys.argv[1])
issuer = sys.argv[2]
audience = sys.argv[3]
auth_dir.mkdir(parents=True, exist_ok=True)

private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
public_pem = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)

priv_path = auth_dir / "private.pem"
pub_path = auth_dir / "public.pem"
fd = os.open(str(priv_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "wb") as fh:
    fh.write(private_pem)
os.chmod(priv_path, 0o600)
pub_path.write_bytes(public_pem)

now = int(time.time())
claims = {
    "sub": "som-pg-run",
    "iat": now,
    "exp": now + 3600,
    "iss": issuer,
    "aud": audience,
    "role": "editor",
}
token = jwt.encode(claims, private_pem, algorithm="RS256")
if not isinstance(token, str):
    token = token.decode("utf-8")
print(json.dumps({
    "private_key_file": str(priv_path),
    "public_key_file": str(pub_path),
    "issuer": issuer,
    "audience": audience,
    "token": token,
}))
""".strip()


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
        pkg = data.get("package")
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


def check_supported_capabilities(cnl_path: Path) -> str | None:
    try:
        text = cnl_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Refused --cnl {cnl_path}: {exc}"

    for pattern, cap_kind, cap_val in UNSUPPORTED_CAPABILITY_PATTERNS:
        if pattern.search(text):
            return (
                f"Refused --cnl {cnl_path}: unsupported capability "
                f"'Use {cap_kind} {cap_val}.' ({cap_kind}={cap_val})"
            )

    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        caps = data.get("capabilities")
        if isinstance(caps, dict):
            if caps.get("database") == "mongodb":
                return f"Refused --cnl {cnl_path}: unsupported capability database=mongodb"
            if caps.get("auth") in ("firebase", "google_oauth"):
                auth_val = caps["auth"]
                return f"Refused --cnl {cnl_path}: unsupported capability auth={auth_val}"
            if caps.get("storage") in ("s3", "gcs"):
                storage_val = caps["storage"]
                return f"Refused --cnl {cnl_path}: unsupported capability storage={storage_val}"
    return None


def readme_requires_jwt(readme_text: str) -> bool:
    return "## Authentication" in readme_text and "JWT_PUBLIC_KEY_FILE" in readme_text


def tests_drop_schema_in_teardown(product_dir: Path) -> bool:
    tests_dir = product_dir / "tests"
    if not tests_dir.is_dir():
        return False
    for py_file in sorted(tests_dir.rglob("*.py")):
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if "Base.metadata.drop_all" in content:
            return True
    return False


def generate_jwt_materials(
    python_exe: str,
    auth_dir: Path,
    package_name: str,
) -> tuple[dict[str, str] | None, str | None]:
    issuer = "som-pg-run"
    audience = package_name
    try:
        proc = subprocess.run(
            [python_exe, "-c", JWT_BOOTSTRAP_SCRIPT, str(auth_dir), issuer, audience],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, f"Failed to run {python_exe} for JWT key generation: {exc}"
    if proc.returncode != 0:
        return None, f"JWT key generation exited with {proc.returncode}: {tail_text(proc.stdout, proc.stderr)}"
    try:
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        payload = json.loads(lines[-1]) if lines else {}
    except ValueError as exc:
        return None, f"Invalid JSON from JWT key generation: {exc}"
    if not isinstance(payload, dict) or not payload.get("token") or not payload.get("public_key_file"):
        return None, "JWT key generation did not return token and public_key_file"
    priv_file = payload.get("private_key_file")
    if isinstance(priv_file, str) and Path(priv_file).is_file():
        os.chmod(priv_file, 0o600)
    return {
        "private_key_file": str(payload.get("private_key_file", "")),
        "public_key_file": str(payload["public_key_file"]),
        "issuer": str(payload.get("issuer", issuer)),
        "audience": str(payload.get("audience", audience)),
        "token": str(payload["token"]),
    }, None


def redact_secrets(value: Any, secrets: list[str]) -> Any:
    active_secrets = [s for s in secrets if s]
    if not active_secrets:
        return value
    if isinstance(value, str):
        out = value
        for secret in active_secrets:
            if secret in out:
                out = out.replace(secret, REDACTED)
        return out
    if isinstance(value, list):
        return [redact_secrets(item, active_secrets) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item, active_secrets) for item in value]
    if isinstance(value, dict):
        return {k: redact_secrets(v, active_secrets) for k, v in value.items()}
    return value


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

    cap_err = check_supported_capabilities(cnl_path)
    if cap_err is not None:
        return 2, {"status": "refused", "passed": False, "error": cap_err}

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
    secrets_to_redact: list[str] = []

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

        readme_text = readme_path.read_text(encoding="utf-8")
        readme_pkg = parse_package_from_readme(readme_text)
        if readme_pkg:
            package_name = readme_pkg
        if not package_name:
            raise RuntimeError("could not determine package name")

        dev_url = f"postgresql+psycopg://postgres@127.0.0.1:{pg_port}/{package_name}_dev"
        test_url = f"postgresql+psycopg://postgres@127.0.0.1:{pg_port}/{package_name}_test"
        product_env = dict(os.environ)
        for jwt_var in (
            "JWT_PUBLIC_KEY_FILE",
            "JWT_ISSUER",
            "JWT_AUDIENCE",
            "TEST_AUTH_HEADERS",
            "TEST_AUTH_COOKIES",
        ):
            product_env.pop(jwt_var, None)
        product_env["DATABASE_URL"] = dev_url
        product_env["TEST_DATABASE_URL"] = test_url
        pythonpath_parts = (
            [str(product_dir / "src"), str(product_dir)]
            if (product_dir / "src").is_dir()
            else [str(product_dir)]
        )
        if product_env.get("PYTHONPATH"):
            pythonpath_parts.append(product_env["PYTHONPATH"])
        product_env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

        jwt_token: str | None = None
        if readme_requires_jwt(readme_text):
            auth_dir = out_dir / "auth"
            jwt_info, jwt_err = generate_jwt_materials(python_exe, auth_dir, package_name)
            if jwt_err is not None or jwt_info is None:
                record_step(
                    steps_map,
                    "alembic_upgrade",
                    argv=[],
                    exit_code=1,
                    output_tail=jwt_err or "JWT key generation failed",
                )
                raise RuntimeError("jwt setup failed")
            jwt_token = jwt_info["token"]
            secrets_to_redact.append(jwt_token)
            priv_key_path = Path(jwt_info["private_key_file"])
            if priv_key_path.is_file():
                priv_text = priv_key_path.read_text(encoding="utf-8", errors="replace").strip()
                if priv_text:
                    secrets_to_redact.append(priv_text)
                for line in priv_text.splitlines():
                    stripped_line = line.strip()
                    if stripped_line and not stripped_line.startswith("-----"):
                        secrets_to_redact.append(stripped_line)
            product_env["JWT_PUBLIC_KEY_FILE"] = jwt_info["public_key_file"]
            product_env["JWT_ISSUER"] = jwt_info["issuer"]
            product_env["JWT_AUDIENCE"] = jwt_info["audience"]
            product_env["TEST_AUTH_HEADERS"] = json.dumps({"Authorization": f"Bearer {jwt_token}"})
            product_env["TEST_AUTH_COOKIES"] = "{}"

        # Step 5: alembic upgrade head (dev DB)
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

        # Step 6: alembic upgrade head (test DB)
        test_migrate_env = {**product_env, "DATABASE_URL": test_url}
        argv_upgrade_test = [alembic_bin, "upgrade", "head"]
        code_test_up, tail_test_up = run_cmd(argv_upgrade_test, cwd=product_dir, env=test_migrate_env)
        if not record_step(
            steps_map,
            "alembic_upgrade_test",
            argv=argv_upgrade_test,
            exit_code=code_test_up,
            output_tail=tail_test_up,
        ):
            raise RuntimeError("alembic upgrade test failed")

        # Step 7: python -m unittest discover -s tests -v
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

        # Step 8: Start uvicorn <package>.app:app --port <app-port> and poll GET /healthz up to 10s
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

        # Step 9: --create requests
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
                    f"POST {resolved_path} aborted: uvicorn exited early (exit_code={app_proc.returncode})"
                )
                break
            req_headers: dict[str, str] = {"Content-Type": "application/json"}
            if jwt_token is not None:
                req_headers["Authorization"] = f"Bearer {jwt_token}"
            status_code, resp_body = http_request(
                "POST",
                target_url,
                body=raw_json.encode("utf-8"),
                headers=req_headers,
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

        # Step 10: alembic downgrade base (dev DB, and test DB when tests did not drop_all)
        argv_downgrade = [alembic_bin, "downgrade", "base"]
        code_down_dev, tail_down_dev = run_cmd(argv_downgrade, cwd=product_dir, env=product_env)
        if code_down_dev != 0:
            record_step(
                steps_map,
                "alembic_downgrade",
                argv=argv_downgrade,
                exit_code=code_down_dev,
                output_tail=tail_down_dev,
            )
            raise RuntimeError("alembic downgrade failed")

        if tests_drop_schema_in_teardown(product_dir):
            record_step(
                steps_map,
                "alembic_downgrade",
                argv=argv_downgrade,
                exit_code=0,
                output_tail=tail_down_dev,
                extra={"databases": [f"{package_name}_dev"]},
            )
        else:
            code_down_test, tail_down_test = run_cmd(
                argv_downgrade,
                cwd=product_dir,
                env={**product_env, "DATABASE_URL": test_url},
            )
            if not record_step(
                steps_map,
                "alembic_downgrade",
                argv=[argv_downgrade, argv_downgrade],
                exit_code=code_down_test,
                output_tail=tail_text(tail_down_dev, tail_down_test),
                extra={"databases": [f"{package_name}_dev", f"{package_name}_test"]},
            ):
                raise RuntimeError("alembic downgrade test failed")

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

    result_payload = redact_secrets(
        {
            "passed": overall_passed,
            "status": "ok" if overall_passed else "error",
            "package": package_name,
            "out": str(out_dir),
            "steps": ordered_steps,
            "steps_by_name": steps_map,
            "cleanup": cleanup_info,
        },
        secrets_to_redact,
    )
    result_path = out_dir / "result.json"
    result_path.write_text(json.dumps(result_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = redact_secrets(
        {
            "status": "ok" if overall_passed else "error",
            "passed": overall_passed,
            "package": package_name,
            "out": str(out_dir),
            "result": str(result_path),
            "steps": {name: steps_map[name]["status"] for name in STEP_NAMES},
            "pg_stop": cleanup_info["pg_stop"]["status"],
        },
        secrets_to_redact,
    )
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
