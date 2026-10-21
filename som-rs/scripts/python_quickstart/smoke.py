#!/usr/bin/env python3
"""Smoke-check `som write` across all compiler-v1 CNL and JSON prompt examples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

DEFAULT_EXAMPLES_DIR = (
    Path(__file__).resolve().parents[2] / "contracts" / "compiler-v1" / "examples"
)
REQUIRED_FILES = ("pyproject.toml", "README.md")
REQUIRED_DIRS = ("tests/",)
# In compiler-v1 selection-policy.json, prompts v1 and v2 compile to som.dsl.v1,
# which emits 5 router/migration/test files without pyproject.toml or README.md.
DSL_V1_STEMS = {"v1", "v2"}
DSL_V1_ROUTER_FILES = ("items/models.py", "items/schemas.py", "items/api.py")


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


def check_generated_dir(target_dir: Path, source: Path) -> list[str]:
    missing: list[str] = []
    if not target_dir.is_dir():
        return ["<output directory>"]

    stem = (
        source.name[: -len(".prompt.json")]
        if source.name.endswith(".prompt.json")
        else source.stem
    )
    has_live_dsl_v1_router = (
        stem in DSL_V1_STEMS
        and not (target_dir / "pyproject.toml").exists()
        and not (target_dir / "README.md").exists()
        and all((target_dir / rel).is_file() for rel in DSL_V1_ROUTER_FILES)
        and (target_dir / "alembic" / "versions").is_dir()
        and any((target_dir / "alembic" / "versions").iterdir())
    )

    if not has_live_dsl_v1_router:
        for rel_file in REQUIRED_FILES:
            if not (target_dir / rel_file).is_file():
                missing.append(rel_file)

    for rel_dir in REQUIRED_DIRS:
        dir_name = rel_dir.rstrip("/")
        if not (target_dir / dir_name).is_dir():
            missing.append(rel_dir)

    return missing


def run_smoke(
    som_bin: Path,
    url: str,
    out_dir: Path,
    examples_dir: Path = DEFAULT_EXAMPLES_DIR,
) -> tuple[int, dict[str, Any]]:
    out_error = validate_out_dir(out_dir)
    if out_error is not None:
        summary: dict[str, Any] = {
            "status": "error",
            "error": out_error,
            "out": str(out_dir),
            "total": 0,
            "passed": 0,
            "failed": 0,
            "failures": [],
        }
        return 1, summary

    sources = example_sources(examples_dir)
    if not sources:
        summary = {
            "status": "error",
            "error": f"No *.cnl or *.prompt.json examples found in {examples_dir}",
            "out": str(out_dir),
            "total": 0,
            "passed": 0,
            "failed": 0,
            "failures": [],
        }
        return 1, summary

    out_dir.mkdir(parents=True, exist_ok=True)

    passed = 0
    failures: list[dict[str, Any]] = []

    for source in sources:
        name = case_name_for(source)
        target_dir = out_dir / name
        cmd = [
            str(som_bin),
            "write",
            str(source),
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
            failures.append(
                {
                    "name": name,
                    "source": str(source),
                    "reason": f"failed to execute {som_bin}: {exc}",
                }
            )
            continue

        if proc.returncode != 0:
            failures.append(
                {
                    "name": name,
                    "source": str(source),
                    "returncode": proc.returncode,
                    "stderr": proc.stderr.strip(),
                }
            )
            continue

        missing = check_generated_dir(target_dir, source)
        if missing:
            failures.append(
                {
                    "name": name,
                    "source": str(source),
                    "missing": missing,
                }
            )
            continue

        passed += 1

    total = len(sources)
    failed = len(failures)
    status = "ok" if failed == 0 else "error"
    summary = {
        "status": status,
        "out": str(out_dir),
        "total": total,
        "passed": passed,
        "failed": failed,
        "failures": failures,
    }
    return (0 if failed == 0 else 1), summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run `som write` across compiler-v1 CNL and JSON prompt examples and verify each generated project layout."
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
        help="Base URL of the local SOM HTTP service.",
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
