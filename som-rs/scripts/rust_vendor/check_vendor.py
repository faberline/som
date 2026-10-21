#!/usr/bin/env python3
"""Read-only verifier for phase 2 vendored crates against frozen Cargo.lock files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tomllib
from typing import Any

REPO = Path(__file__).resolve().parents[3]
CONTRACT = REPO / "som-rs/contracts/rust-output-v1"
DEFAULT_REFERENCES = [CONTRACT / "reference", CONTRACT / "reference-phase2"]


def _default_vendor_dir() -> Path:
    rel = Path(".local/rust-output-phase2/vendor-002")
    direct = REPO / rel
    if direct.exists():
        return direct
    dot_git = REPO / ".git"
    if dot_git.is_file():
        try:
            text = dot_git.read_text(encoding="utf-8").strip()
        except OSError:
            return direct
        if text.startswith("gitdir:"):
            gitdir = (REPO / text.removeprefix("gitdir:").strip()).resolve()
            if gitdir.parent.name == "worktrees":
                main_repo = gitdir.parents[2]
                fallback = main_repo / rel
                if fallback.exists():
                    return fallback
    return direct


DEFAULT_VENDOR = _default_vendor_dir()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(f"{path.relative_to(root)}\0{sha_file(path)}\n".encode("utf-8"))
    return digest.hexdigest()


def load_locks(references: list[Path]) -> tuple[dict[tuple[str, str], str], list[tuple[Path, str]]]:
    if not references:
        raise ValueError("at least one --reference directory is required")
    locked: dict[tuple[str, str], str] = {}
    ref_shas: list[tuple[Path, str]] = []
    for ref_dir in references:
        lock_path = ref_dir / "Cargo.lock"
        try:
            raw_bytes = lock_path.read_bytes()
            parsed = tomllib.loads(raw_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"cannot read Cargo.lock at {lock_path}: {exc}") from exc

        ref_shas.append((ref_dir, sha_bytes(raw_bytes)))
        packages = parsed.get("package")
        if not isinstance(packages, list):
            raise ValueError(f"missing [[package]] list in {lock_path}")

        for pkg in packages:
            if not isinstance(pkg, dict):
                raise ValueError(f"invalid package entry in {lock_path}")
            checksum = pkg.get("checksum")
            if checksum is None:
                continue
            name = pkg.get("name")
            version = pkg.get("version")
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(version, str)
                or not version
                or not isinstance(checksum, str)
                or not checksum
            ):
                raise ValueError(f"malformed package entry in {lock_path}")
            existing = locked.setdefault((name, version), checksum)
            if existing != checksum:
                raise ValueError(
                    f"Cargo.lock files disagree on {name} {version}: {existing} vs {checksum}"
                )

    if not locked:
        raise ValueError("no crates.io packages with checksum found in Cargo.lock")

    return locked, ref_shas


def load_vendor_receipt(
    vendor_json: Path,
) -> tuple[dict[tuple[str, str], str], int, list[tuple[str | None, str]], str]:
    try:
        data = json.loads(vendor_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read vendor.json at {vendor_json}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("vendor.json root must be an object")

    lock_packages = data.get("lock_packages")
    if not isinstance(lock_packages, int):
        raise ValueError("vendor.json missing integer 'lock_packages'")

    packages = data.get("packages")
    if not isinstance(packages, list):
        raise ValueError("vendor.json missing 'packages' list")

    tree_map: dict[tuple[str, str], str] = {}
    for row in packages:
        if not isinstance(row, dict):
            raise ValueError("vendor.json package entry must be an object")
        name = row.get("name")
        version = row.get("version")
        t_sha = row.get("tree_sha256")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(version, str)
            or not version
            or not isinstance(t_sha, str)
            or not t_sha
        ):
            raise ValueError("vendor.json package entry missing name, version, or tree_sha256")
        tree_map[(name, version)] = t_sha

    references = data.get("references")
    if not isinstance(references, list):
        raise ValueError("vendor.json missing 'references' list")
    receipt_refs: list[tuple[str | None, str]] = []
    for ref in references:
        if not isinstance(ref, dict) or not isinstance(ref.get("lock_sha256"), str):
            raise ValueError("vendor.json reference entry missing 'lock_sha256'")
        ref_path = ref.get("path")
        receipt_refs.append((ref_path if isinstance(ref_path, str) else None, ref["lock_sha256"]))

    cargo_config_sha = data.get("cargo_config_sha256")
    if not isinstance(cargo_config_sha, str) or not cargo_config_sha:
        raise ValueError("vendor.json missing 'cargo_config_sha256'")

    return tree_map, lock_packages, receipt_refs, cargo_config_sha


def _references_match(
    ref_shas: list[tuple[Path, str]], receipt_refs: list[tuple[str | None, str]]
) -> bool:
    actual_by_resolved: dict[Path, str] = {}
    for ref_dir, sha in ref_shas:
        resolved = ref_dir.resolve()
        if resolved in actual_by_resolved and actual_by_resolved[resolved] != sha:
            return False
        actual_by_resolved[resolved] = sha

    if len(actual_by_resolved) != len(receipt_refs):
        return False

    receipt_shas = [sha for _, sha in receipt_refs]
    actual_shas = list(actual_by_resolved.values())
    if sorted(actual_shas) != sorted(receipt_shas):
        return False

    for raw_path, expected_sha in receipt_refs:
        if raw_path is None:
            continue
        p = Path(raw_path)
        candidates = [p.resolve()] if p.is_absolute() else [(REPO / p).resolve(), p.resolve()]
        for cand in candidates:
            if cand in actual_by_resolved:
                if actual_by_resolved[cand] != expected_sha:
                    return False
                break

    return True


def check_vendor(
    vendor_dir: Path, references: list[Path]
) -> tuple[bool, list[dict[str, Any]], list[str], dict[str, int]]:
    locked, ref_shas = load_locks(references)

    if not vendor_dir.is_dir():
        raise ValueError(f"vendor directory does not exist: {vendor_dir}")
    vendor_root = vendor_dir / "vendor"
    if not vendor_root.is_dir():
        raise ValueError(f"vendor subdirectory does not exist: {vendor_root}")

    config_path = vendor_dir / "cargo-home/config.toml"
    try:
        config_bytes = config_path.read_bytes()
        config_data = tomllib.loads(config_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read cargo config at {config_path}: {exc}") from exc

    vendor_json = vendor_dir / "vendor.json"
    has_receipt = vendor_json.exists()
    tree_map: dict[tuple[str, str], str] = {}
    receipt_lock_packages = 0
    receipt_refs: list[tuple[str | None, str]] = []
    receipt_config_sha = ""
    if has_receipt:
        if not vendor_json.is_file():
            raise ValueError(f"vendor.json is not a regular file: {vendor_json}")
        tree_map, receipt_lock_packages, receipt_refs, receipt_config_sha = (
            load_vendor_receipt(vendor_json)
        )

    items: list[dict[str, Any]] = []
    lines: list[str] = []
    ok_count = 0
    problem_count = 0
    receipt_tree_mismatches = 0

    for (name, version), checksum in sorted(locked.items()):
        pkg_id = f"{name}-{version}"
        pkg_dir = vendor_root / pkg_id
        pkg_problems = 0

        if not pkg_dir.is_dir():
            lines.append(f"missing {pkg_id} {pkg_dir}")
            items.append(
                {
                    "name": pkg_id,
                    "status": "missing",
                    "path": str(pkg_dir),
                }
            )
            problem_count += 1
            continue

        checksum_path = pkg_dir / ".cargo-checksum.json"
        if not checksum_path.is_file():
            lines.append(f"missing {pkg_id} {checksum_path}")
            items.append(
                {
                    "name": pkg_id,
                    "status": "missing",
                    "path": str(checksum_path),
                }
            )
            problem_count += 1
            continue

        try:
            checksum_data = json.loads(checksum_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            reason = f"invalid_json:{type(exc).__name__}"
            lines.append(f"mismatch {pkg_id} .cargo-checksum.json {reason}")
            items.append(
                {
                    "name": pkg_id,
                    "status": "mismatch",
                    "what": ".cargo-checksum.json",
                    "reason": str(exc),
                }
            )
            problem_count += 1
            continue

        if not isinstance(checksum_data, dict) or not isinstance(
            checksum_data.get("files"), dict
        ):
            reason = "malformed_structure"
            lines.append(f"mismatch {pkg_id} .cargo-checksum.json {reason}")
            items.append(
                {
                    "name": pkg_id,
                    "status": "mismatch",
                    "what": ".cargo-checksum.json",
                    "reason": reason,
                }
            )
            problem_count += 1
            continue

        recorded_pkg = checksum_data.get("package")
        if recorded_pkg != checksum:
            lines.append(f"mismatch {pkg_id} package {checksum} {recorded_pkg}")
            items.append(
                {
                    "name": pkg_id,
                    "status": "mismatch",
                    "what": "package",
                    "expected": checksum,
                    "actual": recorded_pkg,
                }
            )
            pkg_problems += 1

        files_map: dict[str, Any] = checksum_data["files"]
        for relpath, expected_sha in sorted(
            files_map.items(), key=lambda kv: str(kv[0])
        ):
            if not isinstance(relpath, str) or not isinstance(expected_sha, str):
                reason = f"invalid_files_entry:{relpath}"
                lines.append(f"mismatch {pkg_id} .cargo-checksum.json {reason}")
                items.append(
                    {
                        "name": pkg_id,
                        "status": "mismatch",
                        "what": ".cargo-checksum.json",
                        "reason": reason,
                    }
                )
                pkg_problems += 1
                continue
            file_path = pkg_dir / relpath
            if not file_path.is_file():
                lines.append(f"missing {pkg_id} {file_path}")
                items.append(
                    {
                        "name": pkg_id,
                        "status": "missing",
                        "path": str(file_path),
                    }
                )
                pkg_problems += 1
            else:
                try:
                    actual_sha = sha_file(file_path)
                except OSError:
                    actual_sha = "unreadable"
                if actual_sha != expected_sha:
                    lines.append(f"mismatch {pkg_id} {relpath} {expected_sha} {actual_sha}")
                    items.append(
                        {
                            "name": pkg_id,
                            "status": "mismatch",
                            "what": relpath,
                            "expected": expected_sha,
                            "actual": actual_sha,
                        }
                    )
                    pkg_problems += 1

        for path in sorted(pkg_dir.rglob("*")):
            if path.is_file() and path != checksum_path:
                rel = str(path.relative_to(pkg_dir))
                if rel not in files_map:
                    lines.append(f"extra {path}")
                    items.append(
                        {
                            "name": pkg_id,
                            "status": "extra",
                            "path": str(path),
                        }
                    )
                    pkg_problems += 1

        if has_receipt:
            expected_tree = tree_map.get((name, version))
            if expected_tree is None:
                lines.append(f"missing {pkg_id} {vendor_json}")
                items.append(
                    {
                        "name": pkg_id,
                        "status": "missing",
                        "path": str(vendor_json),
                    }
                )
                pkg_problems += 1
                receipt_tree_mismatches += 1
            else:
                try:
                    actual_tree = tree_sha(pkg_dir)
                except OSError:
                    actual_tree = "unreadable"
                if actual_tree != expected_tree:
                    lines.append(
                        f"mismatch {pkg_id} tree_sha256 {expected_tree} {actual_tree}"
                    )
                    items.append(
                        {
                            "name": pkg_id,
                            "status": "mismatch",
                            "what": "tree_sha256",
                            "expected": expected_tree,
                            "actual": actual_tree,
                        }
                    )
                    pkg_problems += 1
                    receipt_tree_mismatches += 1

        if pkg_problems == 0:
            lines.append(f"ok {pkg_id}")
            items.append({"name": pkg_id, "status": "ok"})
            ok_count += 1
        else:
            problem_count += pkg_problems

    expected_dirs = {f"{name}-{version}" for name, version in locked}
    for entry in sorted(vendor_root.iterdir()):
        if entry.name not in expected_dirs:
            lines.append(f"extra {entry}")
            items.append({"name": entry.name, "status": "extra", "path": str(entry)})
            problem_count += 1

    if has_receipt:
        receipt_problems = 0
        if receipt_lock_packages != len(locked):
            lines.append(
                f"mismatch vendor.json lock_packages {len(locked)} {receipt_lock_packages}"
            )
            items.append(
                {
                    "name": "vendor.json",
                    "status": "mismatch",
                    "what": "lock_packages",
                    "expected": len(locked),
                    "actual": receipt_lock_packages,
                }
            )
            receipt_problems += 1

        actual_ref_shas = [sha for _, sha in ref_shas]
        receipt_ref_shas = [sha for _, sha in receipt_refs]
        if not _references_match(ref_shas, receipt_refs):
            expected_joined = ",".join(actual_ref_shas)
            actual_joined = ",".join(receipt_ref_shas)
            lines.append(
                f"mismatch vendor.json lock_sha256 {expected_joined} {actual_joined}"
            )
            items.append(
                {
                    "name": "vendor.json",
                    "status": "mismatch",
                    "what": "lock_sha256",
                    "expected": actual_ref_shas,
                    "actual": receipt_ref_shas,
                }
            )
            receipt_problems += 1

        actual_cfg_sha = sha_bytes(config_bytes)
        if receipt_config_sha != actual_cfg_sha:
            lines.append(
                f"mismatch vendor.json cargo_config_sha256 {receipt_config_sha} {actual_cfg_sha}"
            )
            items.append(
                {
                    "name": "vendor.json",
                    "status": "mismatch",
                    "what": "cargo_config_sha256",
                    "expected": receipt_config_sha,
                    "actual": actual_cfg_sha,
                }
            )
            receipt_problems += 1

        problem_count += receipt_problems
        if receipt_problems == 0 and receipt_tree_mismatches == 0:
            lines.append("ok vendor.json")
            items.append({"name": "vendor.json", "status": "ok"})

    config_problems = 0
    source_table = config_data.get("source")
    vendored_source = (
        source_table.get("vendored-sources")
        if isinstance(source_table, dict)
        else None
    )
    configured_dir = (
        vendored_source.get("directory") if isinstance(vendored_source, dict) else None
    )
    expected_dir_resolved = vendor_root.resolve()
    try:
        configured_resolved = (
            Path(configured_dir).resolve()
            if isinstance(configured_dir, str) and configured_dir
            else None
        )
    except OSError:
        configured_resolved = None
    if configured_resolved != expected_dir_resolved:
        lines.append(
            f"mismatch config.toml directory {vendor_root} {configured_dir}"
        )
        items.append(
            {
                "name": "config.toml",
                "status": "mismatch",
                "what": "directory",
                "expected": str(vendor_root),
                "actual": configured_dir,
            }
        )
        config_problems += 1

    net_table = config_data.get("net")
    offline_val = net_table.get("offline") if isinstance(net_table, dict) else None
    if offline_val is not True:
        lines.append(f"mismatch config.toml offline True {offline_val}")
        items.append(
            {
                "name": "config.toml",
                "status": "mismatch",
                "what": "offline",
                "expected": True,
                "actual": offline_val,
            }
        )
        config_problems += 1

    problem_count += config_problems
    if config_problems == 0:
        lines.append("ok config.toml")
        items.append({"name": "config.toml", "status": "ok"})

    summary = {"ok": ok_count, "problems": problem_count}
    lines.append(f"summary ok={ok_count} problems={problem_count}")
    return problem_count == 0, items, lines, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify phase 2 vendored crates against frozen Cargo.lock files."
    )
    parser.add_argument(
        "--vendor",
        type=Path,
        default=DEFAULT_VENDOR,
        help="Path to the vendor bundle directory (default: .local/rust-output-phase2/vendor-002)",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        action="append",
        dest="references",
        help="Reference directory containing Cargo.lock (may be passed multiple times)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output a JSON object with 'ok', 'items', and 'summary'",
    )
    args = parser.parse_args(argv)
    references = args.references if args.references else DEFAULT_REFERENCES

    try:
        all_ok, items, lines, summary = check_vendor(args.vendor, references)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps({"ok": all_ok, "items": items, "summary": summary}))
    else:
        for line in lines:
            print(line)

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
