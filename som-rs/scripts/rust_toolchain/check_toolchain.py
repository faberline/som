#!/usr/bin/env python3
"""Read-only check that pinned Rust toolchain binaries exist, are executable, and match SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

DEFAULT_TOOLCHAIN = (
    Path(__file__).resolve().parents[2] / "contracts/rust-output-v1/toolchain.json"
)
REQUIRED_BINARIES = (
    "rustc",
    "cargo",
    "rustfmt",
    "cargo-fmt",
    "cargo-clippy",
    "clippy-driver",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def load_toolchain(path: Path) -> tuple[Path, list[tuple[str, str]], str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot read toolchain file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("toolchain JSON root must be an object")

    rust = raw.get("rust")
    if not isinstance(rust, dict):
        raise ValueError("missing or invalid 'rust' object")

    bin_dir_raw = rust.get("bin_dir")
    if not isinstance(bin_dir_raw, str) or not bin_dir_raw or "\x00" in bin_dir_raw:
        raise ValueError("missing or invalid 'rust.bin_dir'")

    for required in REQUIRED_BINARIES:
        entry = rust.get(required)
        if not isinstance(entry, dict):
            raise ValueError(f"missing or invalid 'rust.{required}' object")
        sha = entry.get("sha256")
        if not isinstance(sha, str) or not sha:
            raise ValueError(f"missing or invalid 'rust.{required}.sha256'")

    binaries: list[tuple[str, str]] = []
    for name, value in rust.items():
        if isinstance(value, dict) and "sha256" in value:
            expected = value.get("sha256")
            if not isinstance(expected, str) or not expected:
                raise ValueError(f"invalid sha256 for rust.{name}")
            binaries.append((name, expected))

    rustfmt_sha = rust["rustfmt"]["sha256"]

    formatter = raw.get("formatter")
    if not isinstance(formatter, dict):
        raise ValueError("missing or invalid 'formatter' object")
    formatter_sha = formatter.get("sha256")
    if not isinstance(formatter_sha, str) or not formatter_sha:
        raise ValueError("missing or invalid 'formatter.sha256'")

    return Path(bin_dir_raw), binaries, rustfmt_sha, formatter_sha


def check_toolchain(path: Path) -> tuple[bool, list[dict[str, Any]], list[str]]:
    bin_dir, binaries, rustfmt_sha, formatter_sha = load_toolchain(path)
    items: list[dict[str, Any]] = []
    lines: list[str] = []
    all_ok = True

    for name, expected in binaries:
        binary_path = bin_dir / name
        try:
            usable = binary_path.is_file() and os.access(binary_path, os.X_OK)
        except OSError:
            usable = False

        if not usable:
            all_ok = False
            items.append(
                {
                    "name": name,
                    "status": "missing",
                    "path": str(binary_path),
                    "expected": expected,
                }
            )
            lines.append(f"missing {name} {binary_path}")
            continue

        try:
            actual = sha256_file(binary_path)
        except OSError:
            all_ok = False
            items.append(
                {
                    "name": name,
                    "status": "missing",
                    "path": str(binary_path),
                    "expected": expected,
                }
            )
            lines.append(f"missing {name} {binary_path}")
            continue

        if actual != expected:
            all_ok = False
            items.append(
                {
                    "name": name,
                    "status": "mismatch",
                    "path": str(binary_path),
                    "expected": expected,
                    "actual": actual,
                }
            )
            lines.append(f"mismatch {name} {expected} {actual}")
        else:
            items.append(
                {
                    "name": name,
                    "status": "ok",
                    "path": str(binary_path),
                    "sha256": actual,
                }
            )
            lines.append(f"ok {name}")

    if formatter_sha != rustfmt_sha:
        all_ok = False
        items.append(
            {
                "name": "formatter",
                "status": "mismatch",
                "expected": rustfmt_sha,
                "actual": formatter_sha,
            }
        )
        lines.append(f"mismatch formatter {rustfmt_sha} {formatter_sha}")
    else:
        items.append(
            {
                "name": "formatter",
                "status": "ok",
                "sha256": formatter_sha,
            }
        )
        lines.append("ok formatter")

    return all_ok, items, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify pinned Rust binaries and SHA-256 hashes from toolchain.json."
    )
    parser.add_argument(
        "--toolchain",
        type=Path,
        default=DEFAULT_TOOLCHAIN,
        help="Path to toolchain.json (default: som-rs/contracts/rust-output-v1/toolchain.json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output a JSON object with 'ok' and 'items'",
    )
    args = parser.parse_args(argv)

    try:
        all_ok, items, lines = check_toolchain(args.toolchain)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps({"ok": all_ok, "items": items}))
    else:
        for line in lines:
            print(line)

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
