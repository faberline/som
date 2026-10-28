#!/usr/bin/env python3
"""Inspect hosted-runner prerequisites without running any product or tool."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import re
import sys
from datetime import datetime, timezone


VERSION = "som.hosted-readiness.v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
PROTECTED_PORTS = {8765, 18765}


class Refusal(ValueError):
    """The request is unsafe or the manifest is malformed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Refusal(message)


def relative_path(repo: Path, value: object) -> Path:
    require(isinstance(value, str) and bool(value), "path must be a non-empty string")
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and "\\" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/")),
        f"unsafe repository path: {value}",
    )
    current = repo
    for part in path.parts:
        current /= part
        require(not current.is_symlink(), f"symlink path is refused: {value}")
    return current


def no_symlink_parents(path: Path) -> None:
    for item in (path, *path.parents):
        require(not item.is_symlink(), f"symlink output path is refused: {path}")


def output_path(repo: Path, value: str) -> Path:
    raw = Path(value).expanduser()
    require(".." not in raw.parts, "output traversal is refused")
    out = raw if raw.is_absolute() else repo / raw
    no_symlink_parents(out)
    require(not out.exists(), f"output already exists: {out}")
    try:
        relative = out.relative_to(repo)
    except ValueError:
        return out
    require(
        relative.parts[:2] == (".local", "hosted-readiness")
        and len(relative.parts) > 2,
        "repository output must be under .local/hosted-readiness/",
    )
    return out


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def host_identity() -> str:
    machine = platform.machine().lower()
    machine = {"arm64": "aarch64", "amd64": "x86_64"}.get(machine, machine)
    suffix = {"Darwin": "apple-darwin", "Linux": "unknown-linux-gnu"}.get(
        platform.system(), platform.system().lower()
    )
    return f"{machine}-{suffix}"


def validate_manifest(repo: Path, data: object) -> dict:
    require(isinstance(data, dict), "manifest must be an object")
    require(data.get("version") == VERSION, "unsupported manifest version")
    require(
        isinstance(data.get("source_commit"), str)
        and COMMIT.fullmatch(data["source_commit"]) is not None,
        "source_commit must be a full lowercase Git commit hash",
    )
    sources = data.get("source_files")
    require(isinstance(sources, dict) and bool(sources), "source_files must be non-empty")
    for path, expected in sources.items():
        relative_path(repo, path)
        require(isinstance(expected, str) and SHA256.fullmatch(expected), "invalid source sha256")
    runner = data.get("runner")
    require(
        isinstance(runner, dict)
        and isinstance(runner.get("host"), str)
        and bool(runner["host"]),
        "runner.host must be a non-empty string",
    )
    inventories = data.get("inventories")
    require(isinstance(inventories, list) and bool(inventories), "inventories must be non-empty")
    names = set()
    for item in inventories:
        require(isinstance(item, dict), "inventory must be an object")
        name = item.get("name")
        require(isinstance(name, str) and name and name not in names, "duplicate or invalid inventory name")
        names.add(name)
        relative_path(repo, item.get("path"))
        require(item["path"] in sources, "inventory file must also be bound in source_files")
        pointer = item.get("pointer")
        require(isinstance(pointer, str) and (pointer == "" or pointer.startswith("/")), "invalid inventory pointer")
        require(isinstance(item.get("id_key"), str) and item["id_key"], "inventory id_key is required")
        ids = item.get("ids")
        require(
            isinstance(ids, list)
            and bool(ids)
            and all(isinstance(value, str) and value for value in ids)
            and len(set(ids)) == len(ids),
            "inventory ids must be unique non-empty strings",
        )
    assets = data.get("assets")
    require(isinstance(assets, list), "assets must be a list")
    names = set()
    for asset in assets:
        require(isinstance(asset, dict), "asset must be an object")
        name = asset.get("name")
        require(isinstance(name, str) and name and name not in names, "duplicate or invalid asset name")
        names.add(name)
        if "blocked_reason" in asset:
            require(
                isinstance(asset["blocked_reason"], str)
                and asset["blocked_reason"]
                and "path" not in asset
                and "sha256" not in asset,
                "blocked assets must name a reason and must not name a readable path",
            )
        else:
            relative_path(repo, asset.get("path"))
            expected = asset.get("sha256")
            require(isinstance(expected, str) and SHA256.fullmatch(expected), "invalid asset sha256")
    blockers = data.get("blockers")
    require(
        isinstance(blockers, list)
        and all(isinstance(reason, str) and reason for reason in blockers),
        "blockers must be a list of non-empty strings",
    )
    return data


def file_row(repo: Path, name: str, path: str, expected: str) -> dict:
    row = {"name": name, "path": path, "expected_sha256": expected, "status": "blocked"}
    try:
        file = relative_path(repo, path)
        if not file.is_file():
            row["reason"] = "required file is missing or is not a regular file"
        else:
            row["actual_sha256"] = digest(file)
            if row["actual_sha256"] != expected:
                row["reason"] = "sha256 mismatch"
            else:
                row["status"] = "verified"
    except OSError as exc:
        row["reason"] = f"cannot read required file: {exc.__class__.__name__}"
    return row


def pointer_value(value: object, pointer: str) -> object:
    for token in pointer.split("/")[1:] if pointer else []:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value[token]
        elif isinstance(value, list) and token.isdecimal():
            value = value[int(token)]
        else:
            raise ValueError("inventory pointer does not select a value")
    return value


def inspect(repo: Path, data: dict, port: int) -> dict:
    sources = [file_row(repo, path, path, expected) for path, expected in data["source_files"].items()]
    inventories = []
    for item in data["inventories"]:
        row = {"name": item["name"], "path": item["path"], "expected_count": len(item["ids"]), "status": "blocked"}
        try:
            records = pointer_value(load_json(relative_path(repo, item["path"])), item["pointer"])
            if not isinstance(records, list):
                raise ValueError("inventory does not select an array")
            ids = [record[item["id_key"]] for record in records]
            row["actual_count"] = len(ids)
            if ids != item["ids"]:
                row["reason"] = "inventory ids or order differ from the complete named inventory"
            else:
                row["status"] = "verified"
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            row["reason"] = f"inventory unavailable or malformed: {exc.__class__.__name__}"
        inventories.append(row)
    assets = []
    for asset in data["assets"]:
        if "blocked_reason" in asset:
            assets.append({"name": asset["name"], "status": "blocked", "reason": asset["blocked_reason"]})
        else:
            assets.append(file_row(repo, asset["name"], asset["path"], asset["sha256"]))
    actual_host = host_identity()
    runner = {"expected_host": data["runner"]["host"], "actual_host": actual_host, "status": "verified"}
    if actual_host != runner["expected_host"]:
        runner.update(status="blocked", reason="unsupported platform for the frozen host pin")
    blocked = bool(data["blockers"]) or any(row["status"] == "blocked" for row in [*sources, *inventories, *assets, runner])
    return {
        "version": "som.hosted-readiness-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": data["source_commit"],
        "source_commit_role": "declared inventory baseline; source_files hashes are verified",
        "status": "blocked" if blocked else "ready",
        "acceptance_passed": False,
        "product_suites": "not_run",
        "qa_port": port,
        "source_files": sources,
        "inventories": inventories,
        "runner": runner,
        "assets": assets,
        "blockers": data["blockers"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--manifest", required=True, help="manifest path within the repository")
    parser.add_argument("--out", required=True, help="new JSON report path; inside repo use .local/hosted-readiness/")
    parser.add_argument("--port", type=int, default=18970)
    args = parser.parse_args(argv)
    try:
        require(1 <= args.port <= 65535 and args.port not in PROTECTED_PORTS, "unsafe QA port")
        repo = args.repo.resolve(strict=True)
        require(repo.is_dir(), "repository must be a directory")
        manifest = Path(args.manifest)
        if manifest.is_absolute():
            try:
                manifest = manifest.relative_to(repo)
            except ValueError as exc:
                raise Refusal("manifest must be inside the repository") from exc
        manifest = relative_path(repo, manifest.as_posix())
        out = output_path(repo, args.out)
        data = validate_manifest(repo, load_json(manifest))
        report = inspect(repo, data, args.port)
        report["manifest_sha256"] = digest(manifest)
        report["tool_sha256"] = digest(Path(__file__))
        out.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also protects against another writer creating the report.
        with out.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        print(json.dumps({"status": report["status"], "report": str(out), "acceptance_passed": False, "product_suites": "not_run"}))
        return 1 if report["status"] == "blocked" else 0
    except (Refusal, OSError, ValueError, TypeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
