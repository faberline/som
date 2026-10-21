#!/usr/bin/env python3
"""Smoke-check `som write --target rust` across all compiler-v1 CNL and JSON prompt examples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

SOM_RS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_EXAMPLES_DIR = SOM_RS_DIR / "contracts" / "compiler-v1" / "examples"
RUST_CONTRACT_DIR = SOM_RS_DIR / "contracts" / "rust-output-v1"
REF_PHASE1_TOML = RUST_CONTRACT_DIR / "reference" / "Cargo.toml"
REF_PHASE1_LOCK = RUST_CONTRACT_DIR / "reference" / "Cargo.lock"
REF_PHASE2_TOML = RUST_CONTRACT_DIR / "reference-phase2" / "Cargo.toml"
REF_PHASE2_LOCK = RUST_CONTRACT_DIR / "reference-phase2" / "Cargo.lock"

REQUIRED_FILES = (
    "Cargo.toml",
    "Cargo.lock",
    "README.md",
    "src/main.rs",
    "src/lib.rs",
)


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


def check_generated_dir(target_dir: Path) -> list[str]:
    missing: list[str] = []
    if not target_dir.is_dir():
        return ["<output directory>"]

    for rel_file in REQUIRED_FILES:
        if not (target_dir / rel_file).is_file():
            missing.append(rel_file)

    return missing


def is_query_parameter_collision_refusal(stderr: str) -> bool:
    text = stderr.strip()
    if not text:
        return False
    if "query_parameter_collision" in text:
        return True
    if "because a list parameter has the same name" in text:
        return True
    if "prompt.query.filter_field" in text:
        return True
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("rule") == "query_parameter_collision":
        return True
    detail = payload.get("detail")
    if isinstance(detail, str) and (
        "query_parameter_collision" in detail
        or "prompt.query.filter_field" in detail
        or "because a list parameter has the same name" in detail
    ):
        return True
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, list):
        for diag in diagnostics:
            if not isinstance(diag, dict):
                continue
            pointer = diag.get("prompt_pointer", "")
            cause = diag.get("cause", "")
            rule = diag.get("rule", "")
            if (
                rule == "query_parameter_collision"
                or (
                    isinstance(pointer, str)
                    and pointer.endswith("/query/filter_field")
                    and isinstance(cause, str)
                    and (
                        "collides with a generated Python identifier" in cause
                        or "because a list parameter has the same name" in cause
                        or "query_parameter_collision" in cause
                    )
                )
            ):
                return True
    return False


def _load_reference_cargo_pairs() -> tuple[tuple[bytes, bytes], tuple[bytes, bytes]] | None:
    try:
        p1 = (REF_PHASE1_TOML.read_bytes(), REF_PHASE1_LOCK.read_bytes())
        p2 = (REF_PHASE2_TOML.read_bytes(), REF_PHASE2_LOCK.read_bytes())
        return p1, p2
    except OSError:
        return None


def check_cargo_byte_identity(
    generated: list[tuple[str, Path, Path]]
) -> list[dict[str, Any]]:
    if len(generated) <= 1:
        return []

    first_name, _, first_dir = generated[0]
    baseline_toml = (first_dir / "Cargo.toml").read_bytes()
    baseline_lock = (first_dir / "Cargo.lock").read_bytes()

    ref_pairs = _load_reference_cargo_pairs()
    is_live_reference_baseline = (
        ref_pairs is not None
        and (baseline_toml, baseline_lock) in ref_pairs
    )

    mismatches: list[dict[str, Any]] = []
    for name, source, target_dir in generated[1:]:
        toml_bytes = (target_dir / "Cargo.toml").read_bytes()
        lock_bytes = (target_dir / "Cargo.lock").read_bytes()

        if toml_bytes == baseline_toml and lock_bytes == baseline_lock:
            continue

        # In product-layout.json, `resource` shapes emit reference/Cargo.{toml,lock}
        # and phase 2 capability/flow shapes (with src/integrations.rs) emit
        # reference-phase2/Cargo.{toml,lock}.
        if (
            is_live_reference_baseline
            and ref_pairs is not None
            and (target_dir / "src" / "integrations.rs").is_file()
            and (toml_bytes, lock_bytes) == ref_pairs[1]
        ):
            continue

        differing: list[str] = []
        if toml_bytes != baseline_toml:
            differing.append("Cargo.toml")
        if lock_bytes != baseline_lock:
            differing.append("Cargo.lock")
        mismatches.append(
            {
                "name": name,
                "source": str(source),
                "baseline": first_name,
                "differing_files": differing,
            }
        )

    return mismatches


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
            "refused": 0,
            "failed": 0,
            "refusals": [],
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
            "refused": 0,
            "failed": 0,
            "refusals": [],
            "failures": [],
        }
        return 1, summary

    out_dir.mkdir(parents=True, exist_ok=True)

    generated: list[tuple[str, Path, Path]] = []
    refusals: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for source in sources:
        name = case_name_for(source)
        target_dir = out_dir / name
        cmd = [
            str(som_bin),
            "write",
            str(source),
            "--target",
            "rust",
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
            stderr_text = proc.stderr.strip()
            if is_query_parameter_collision_refusal(stderr_text):
                refusals.append(
                    {
                        "name": name,
                        "source": str(source),
                        "rule": "query_parameter_collision",
                        "returncode": proc.returncode,
                        "stderr": stderr_text,
                    }
                )
            else:
                failures.append(
                    {
                        "name": name,
                        "source": str(source),
                        "returncode": proc.returncode,
                        "stderr": stderr_text,
                    }
                )
            continue

        missing = check_generated_dir(target_dir)
        if missing:
            failures.append(
                {
                    "name": name,
                    "source": str(source),
                    "missing": missing,
                }
            )
            continue

        generated.append((name, source, target_dir))

    mismatches = check_cargo_byte_identity(generated)
    mismatch_names = {item["name"] for item in mismatches}
    failures.extend(mismatches)

    passed = sum(1 for name, _, _ in generated if name not in mismatch_names)
    total = len(sources)
    refused = len(refusals)
    failed = len(failures)
    status = "ok" if failed == 0 else "error"
    summary = {
        "status": status,
        "out": str(out_dir),
        "total": total,
        "passed": passed,
        "refused": refused,
        "failed": failed,
        "refusals": refusals,
        "failures": failures,
    }
    return (0 if failed == 0 else 1), summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run `som write --target rust` across compiler-v1 CNL and JSON prompt examples and verify each generated project layout."
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
