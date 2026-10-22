#!/usr/bin/env python3
"""Smoke-check `som write --target typescript` across all compiler-v1 CNL and JSON prompt examples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import urllib.parse

SOM_RS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_EXAMPLES_DIR = SOM_RS_DIR / "contracts" / "compiler-v1" / "examples"
TS_CONTRACT_DIR = SOM_RS_DIR / "contracts" / "typescript-output-v1"
DEFAULT_REFERENCE_DIR = TS_CONTRACT_DIR / "reference"

FORBIDDEN_PORTS = {8765, 18765}
IN_SCOPE_VERSIONS = {1, 2, 3, 4, 5}
OUT_OF_SCOPE_VERSIONS = {6, 7}

REQUIRED_FIXED_FILES = (
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "src/main.ts",
    "src/app.ts",
    "src/schemas.ts",
    "src/db.ts",
    "src/migrate.ts",
    "test/api.test.ts",
    "README.md",
)
MIGRATION_GLOB = "migrations/0001_*.sql"
REFERENCE_FILES = (
    "package.json",
    "package-lock.json",
    "tsconfig.json",
)

CNL_VERSION_RE = re.compile(r"^\s*Use\s+prompt\s+version\s+([0-9]+)\s*\.", re.MULTILINE)
JSON_VERSION_RE = re.compile(r"^som\.prompt\.v([0-9]+)$")
STEM_VERSION_RE = re.compile(r"^v([0-9]+)(?:$|[-_])")


def example_sources(examples_dir: Path) -> list[Path]:
    cnl_files = sorted(examples_dir.glob("*.cnl"))
    prompt_files = sorted(examples_dir.glob("*.prompt.json"))
    return cnl_files + prompt_files


def case_name_for(source: Path) -> str:
    if source.name.endswith(".prompt.json"):
        return f"{source.name[:-len('.prompt.json')]}-prompt"
    if source.name.endswith(".cnl"):
        return f"{source.name[:-len('.cnl')]}-cnl"
    return source.name.replace(".", "-")


def detect_prompt_version(source: Path) -> int | None:
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        text = ""

    if source.name.endswith(".cnl") and text:
        match = CNL_VERSION_RE.search(text)
        if match is not None:
            return int(match.group(1))
    elif source.name.endswith(".json") and text:
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            raw_ver = doc.get("version")
            if not isinstance(raw_ver, str) and isinstance(doc.get("prompt"), dict):
                raw_ver = doc["prompt"].get("version")
            if isinstance(raw_ver, str):
                ver_match = JSON_VERSION_RE.match(raw_ver)
                if ver_match is not None:
                    return int(ver_match.group(1))

    stem = (
        source.name[: -len(".prompt.json")]
        if source.name.endswith(".prompt.json")
        else source.stem
    )
    stem_match = STEM_VERSION_RE.match(stem)
    if stem_match is not None:
        return int(stem_match.group(1))
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


def validate_out_dir(out: Path) -> str | None:
    if out.is_symlink():
        return f"Refused --out {out}: symlinks are not allowed"
    if out.exists():
        if not out.is_dir():
            return f"Refused --out {out}: path exists and is not a directory"
        try:
            if any(out.iterdir()):
                return f"Refused --out {out}: directory is not empty"
        except OSError as exc:
            return f"Refused --out {out}: {exc}"
    return None


def check_generated_dir(target_dir: Path) -> list[str]:
    missing: list[str] = []
    if not target_dir.is_dir():
        return ["<output directory>"]

    for rel_file in REQUIRED_FIXED_FILES:
        candidate = target_dir / rel_file
        if candidate.is_symlink() or not candidate.is_file():
            missing.append(rel_file)

    migrations_dir = target_dir / "migrations"
    migration_files = (
        [
            p
            for p in sorted(target_dir.glob(MIGRATION_GLOB))
            if not p.is_symlink() and p.is_file()
        ]
        if migrations_dir.is_dir() and not migrations_dir.is_symlink()
        else []
    )
    if len(migration_files) != 1:
        missing.append(MIGRATION_GLOB)

    return missing


def check_reference_byte_identity(
    target_dir: Path,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
) -> list[str]:
    differing: list[str] = []
    for rel_name in REFERENCE_FILES:
        gen_path = target_dir / rel_name
        ref_path = reference_dir / rel_name
        try:
            gen_bytes = gen_path.read_bytes()
            ref_bytes = ref_path.read_bytes()
        except OSError:
            differing.append(rel_name)
            continue
        if gen_bytes != ref_bytes:
            differing.append(rel_name)
    return differing


def is_unsupported_target_refusal(returncode: int, stderr: str) -> bool:
    if returncode != 2:
        return False
    text = stderr.strip()
    if not text:
        return False
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("code") != "UNSUPPORTED_TARGET":
        return False
    diagnostics = payload.get("diagnostics")
    if diagnostics is not None:
        if not isinstance(diagnostics, list) or not diagnostics:
            return False
        for diag in diagnostics:
            if not isinstance(diag, dict) or diag.get("code") != "UNSUPPORTED_TARGET":
                return False
    return True


def _make_refusal_summary(out_dir: Path, error: str) -> dict[str, Any]:
    return {
        "status": "refused",
        "error": error,
        "out": str(out_dir),
        "total": 0,
        "written": 0,
        "passed": 0,
        "refused": 0,
        "failed": 0,
        "examples": [],
        "rows": [],
        "refusals": [],
        "failures": [],
    }


def run_smoke(
    som_bin: Path,
    url: str,
    out_dir: Path,
    examples_dir: Path = DEFAULT_EXAMPLES_DIR,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
) -> tuple[int, dict[str, Any]]:
    url_error = validate_url(url)
    if url_error is not None:
        return 2, _make_refusal_summary(out_dir, url_error)

    out_error = validate_out_dir(out_dir)
    if out_error is not None:
        return 2, _make_refusal_summary(out_dir, out_error)

    sources = example_sources(examples_dir)
    if not sources:
        return 2, _make_refusal_summary(
            out_dir,
            f"No *.cnl or *.prompt.json examples found in {examples_dir}",
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    written = 0

    for source in sources:
        name = case_name_for(source)
        version = detect_prompt_version(source)
        target_dir = out_dir / name
        cmd = [
            str(som_bin),
            "write",
            str(source),
            "--target",
            "typescript",
            "--out",
            str(target_dir),
            "--url",
            url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            row = {
                "name": name,
                "source": str(source),
                "version": version,
                "status": "failed",
                "returncode": None,
                "reason": f"failed to execute {som_bin}: {exc}",
            }
            rows.append(row)
            failures.append(row)
            continue

        stderr_text = proc.stderr.strip()

        if version in OUT_OF_SCOPE_VERSIONS:
            if is_unsupported_target_refusal(proc.returncode, stderr_text):
                row = {
                    "name": name,
                    "source": str(source),
                    "version": version,
                    "status": "refused",
                    "returncode": proc.returncode,
                    "code": "UNSUPPORTED_TARGET",
                }
                rows.append(row)
                refusals.append(row)
            else:
                row = {
                    "name": name,
                    "source": str(source),
                    "version": version,
                    "status": "failed",
                    "returncode": proc.returncode,
                    "reason": "expected exit 2 with UNSUPPORTED_TARGET for out-of-scope version",
                    "stderr": stderr_text,
                }
                rows.append(row)
                failures.append(row)
            continue

        if version not in IN_SCOPE_VERSIONS:
            row = {
                "name": name,
                "source": str(source),
                "version": version,
                "status": "failed",
                "returncode": proc.returncode,
                "reason": f"unknown prompt version {version!r}",
                "stderr": stderr_text,
            }
            rows.append(row)
            failures.append(row)
            continue

        if proc.returncode != 0:
            row = {
                "name": name,
                "source": str(source),
                "version": version,
                "status": "failed",
                "returncode": proc.returncode,
                "stderr": stderr_text,
            }
            rows.append(row)
            failures.append(row)
            continue

        missing = check_generated_dir(target_dir)
        if missing:
            row = {
                "name": name,
                "source": str(source),
                "version": version,
                "status": "failed",
                "returncode": proc.returncode,
                "missing": missing,
            }
            rows.append(row)
            failures.append(row)
            continue

        differing = check_reference_byte_identity(target_dir, reference_dir)
        if differing:
            row = {
                "name": name,
                "source": str(source),
                "version": version,
                "status": "failed",
                "returncode": proc.returncode,
                "differing_files": differing,
            }
            rows.append(row)
            failures.append(row)
            continue

        written += 1
        row = {
            "name": name,
            "source": str(source),
            "version": version,
            "status": "written",
            "returncode": proc.returncode,
        }
        rows.append(row)

    total = len(sources)
    refused = len(refusals)
    failed = len(failures)
    status = "ok" if failed == 0 else "error"
    summary: dict[str, Any] = {
        "status": status,
        "out": str(out_dir),
        "total": total,
        "written": written,
        "passed": written,
        "refused": refused,
        "failed": failed,
        "examples": rows,
        "rows": rows,
        "refusals": refusals,
        "failures": failures,
    }
    return (0 if failed == 0 else 1), summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run `som write --target typescript` across compiler-v1 CNL and JSON prompt examples "
            "and verify each v1-v5 product layout and v6-v7 UNSUPPORTED_TARGET refusal."
        )
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
        help="Base URL of the SOM HTTP service (ports 8765 and 18765 are refused).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New or empty directory where generated projects will be written.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    code, summary = run_smoke(args.som, args.url, args.out)
    print(json.dumps(summary, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
