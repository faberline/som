#!/usr/bin/env python3
"""Build the Rust output phase 2 tool and emulator pins (issue #52).

usage:
  build_phase2_pins.py           write contracts/rust-output-v1/phase2/pins.json
  build_phase2_pins.py --check   fail unless the committed pins are exactly what this host has

The Rust toolchain, the crates and PostgreSQL stay the phase 1 pins of toolchain.json; this file
only names that file by hash. Phase 2 adds the services the behavior suite runs: mongod, redis,
moto (S3), the in-repo GCS, Firebase and OIDC stand-ins, uv, the acceptance venv and its packages,
and every repository file the suite imports. A binary is pinned by its resolved path, its version
output and its sha256; a repository file by its sha256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parent.parent
REPO = SOM_RS.parent
CONTRACT = SOM_RS / "contracts" / "rust-output-v1"
OUT = CONTRACT / "phase2" / "pins.json"
TOOLCHAIN = CONTRACT / "toolchain.json"
VENV = SOM_RS / "runtime" / "acceptance-m3-venv"
BEHAVIOR = SOM_RS / "behavior"
SCRIPTS = REPO / "som-core" / "scripts"

# Every repository file the phase 2 suite imports or launches, outside the venv.
SUITE_FILES = (
    [BEHAVIOR / name for name in ("hosts.py", "suite.py", "faults.py", "check_behavior.py")]
    + [BEHAVIOR / "phase2" / name for name in ("adapt.py", "check_phase2.py", "phase2_host.py",
                                                "make_rs_products.py")]
    + [BEHAVIOR / "phase2" / "adapted" / "MANIFEST.json"]
    + sorted((BEHAVIOR / "phase2" / "adapted").glob("*.py"))
    + [HERE / "rust_profile.py", SOM_RS / "scripts" / "compiler_acceptance" / "qa_harness.py",
       SOM_RS / "scripts" / "compiler_acceptance" / "paths.py"]
    + sorted((REPO / "som-core" / "src" / "som_core").glob("*.py"))
)
# The stand-ins are repository code; the suite pins them like any other suite file.
EMULATOR_SCRIPTS = {
    "postgresql_cluster": SCRIPTS / "m3_local_fixture.py",
    "redis_mongo_s3_firebase_oidc": SCRIPTS / "local_business_services.py",
    "flow_services": SCRIPTS / "local_flow_services.py",
    "gcs": SCRIPTS / "flow_gcs_fixture.py",
}
SUPPORT_SCRIPTS = ("business_database_checks.py", "business_test_support.py", "composed_evidence.py",
                   "fixture_guard.py", "flow_row_history.py", "flow_test_backends.py",
                   "verify_composed_services.py")


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: pathlib.Path) -> str:
    return str(path.relative_to(REPO))


def first_line(command: list[str]) -> str:
    done = subprocess.run(command, capture_output=True, text=True, check=True)
    return (done.stdout or done.stderr).strip().splitlines()[0]


def binary(name: str, version_args: list[str], path: pathlib.Path | None = None) -> dict[str, str]:
    found = path or pathlib.Path(shutil.which(name) or "")
    if not found.is_file():
        raise SystemExit(f"{name} is not on PATH")
    real = found.resolve()
    return {"path": str(real), "version": first_line([str(real), *version_args]), "sha256": sha(real)}


def uv() -> dict[str, Any]:
    """uv resolves through a pyenv shim on this host; pin the shim and the binary it runs."""
    shim = pathlib.Path(shutil.which("uv") or "")
    pin = binary("uv", ["--version"])
    if "/.pyenv/shims/" in str(shim):
        real = pathlib.Path(subprocess.run(["pyenv", "which", "uv"], capture_output=True, text=True,
                                           check=True).stdout.strip()).resolve()
        pin = {"shim": pin, "path": str(real), "version": first_line([str(real), "--version"]), "sha256": sha(real)}
    return pin


def packages() -> dict[str, str]:
    code = ("import importlib.metadata as m, json; print(json.dumps(sorted("
            "(d.metadata['Name'].lower(), d.version) for d in m.distributions())))")
    done = subprocess.run([str(VENV / "bin" / "python"), "-I", "-c", code], capture_output=True, text=True,
                          check=True)
    return {name: version for name, version in json.loads(done.stdout)}


def build() -> dict[str, Any]:
    toolchain = json.loads(TOOLCHAIN.read_text())
    pg_bin = pathlib.Path(toolchain["postgresql"]["bin_dir"])
    for name in ("postgres", "initdb", "pg_ctl"):
        if sha(pg_bin / name) != toolchain["postgresql"][name]["sha256"]:
            raise SystemExit(f"{pg_bin / name} does not match toolchain.json")
    python = VENV / "bin" / "python"
    return {
        "version": "som.rust-output-phase2-pins.v1",
        "issue": 52,
        "host": toolchain["host"],
        "rules": [
            "The Rust toolchain, rustfmt, the crate registry rules and PostgreSQL 17.7 are the phase 1 pins of "
            "toolchain.json, unchanged; crate pins for the phase 2 capabilities belong to #53.",
            "A phase 2 acceptance run uses exactly these binaries, packages and repository files. A run on any "
            "other version or hash is not phase 2 evidence.",
            "No cloud service is used: S3 is moto, GCS, Firebase and OIDC are the in-repo stand-ins, MongoDB "
            "and Redis are local processes on loopback QA ports other than 8765 and 18765.",
            "Python products install their locked dependencies with uv sync --python 3.12 under UV_OFFLINE=1.",
        ],
        "toolchain": {"path": rel(TOOLCHAIN), "sha256": sha(TOOLCHAIN)},
        "emulators": {
            "postgresql": {"version": toolchain["postgresql"]["version"], "bin_dir": str(pg_bin),
                           "pinned_by": rel(TOOLCHAIN),
                           "createdb": {"sha256": sha(pg_bin / "createdb")}},
            "mongod": binary("mongod", ["--version"]) | {"replica_set": "som"},
            "redis-server": binary("redis-server", ["--version"]),
            "moto_server": {"path": rel(VENV / "bin" / "moto_server"), "sha256": sha(VENV / "bin" / "moto_server"),
                            "package": "moto", "version": packages()["moto"]},
            "stand_ins": {role: {"path": rel(path), "sha256": sha(path)}
                          for role, path in sorted(EMULATOR_SCRIPTS.items())},
        },
        "uv": uv(),
        "python": {"venv": rel(VENV), "interpreter": str(python.resolve()),
                   "version": first_line([str(python), "--version"]), "sha256": sha(python.resolve()),
                   "packages": packages()},
        "suite_files": {rel(path): sha(path) for path in
                        sorted(SUITE_FILES + [SCRIPTS / name for name in SUPPORT_SCRIPTS])},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{rel(OUT)} differs from what this host has", file=sys.stderr)
            return 1
        print(f"{rel(OUT)} is current")
        return 0
    OUT.write_text(text)
    print(f"wrote {rel(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
