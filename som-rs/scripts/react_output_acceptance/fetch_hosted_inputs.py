#!/usr/bin/env python3
"""Restore pinned encrypted inputs in a GitHub runner. This tool uses no network.

The workflow must fetch the config's commit first. Keys come only from
M6_INPUTS_KEYRING. A successful provision is not a product acceptance result.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any

CONFIG_VERSION = "som.m6-hosted-inputs.v1"
INDEX_VERSION = "som.m6-input-index.v1"
MANIFEST_VERSION = "som.m6-sealed-inputs-manifest.v1"
PROVISION_VERSION = "som.m6-input-provision.v1"
MAX_FILES = 20_000
MAX_UNCOMPRESSED = 256 * 1024 * 1024
MAX_ARCHIVE = MAX_UNCOMPRESSED + 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024 * 1024
REFUSAL = "hosted input provisioning refused"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
PACKAGE = re.compile(r"package-([0-9]{3})\.sealed\Z")


class ProvisionError(Exception):
    def __init__(self) -> None:
        super().__init__(REFUSAL)


def need(condition: bool) -> None:
    if not condition:
        raise ProvisionError()


def exact_keys(value: Any, keys: set[str]) -> None:
    need(type(value) is dict and set(value) == keys)


def integer(value: Any, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_json(data: bytes | str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            need(key not in result)
            result[key] = value
        return result

    def constant(_value: str) -> None:
        raise ProvisionError()

    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="strict")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def canonical_absolute(value: str | Path) -> Path:
    text = os.fspath(value)
    need(type(text) is str and os.path.isabs(text))
    need(os.path.abspath(text) == text and str(Path(text)) == text)
    need("\x00" not in text and not text.startswith("//"))
    return Path(text)


def safe_directory(directory: Path) -> None:
    directory = canonical_absolute(directory)
    for candidate in [*reversed(directory.parents), directory]:
        info = candidate.lstat()
        need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))


def read_regular(filename: Path, maximum: int) -> bytes:
    filename = canonical_absolute(filename)
    safe_directory(filename.parent)
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= maximum)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(maximum + 1)
        need(len(data) <= maximum and len(data) == info.st_size)
        return data
    finally:
        os.close(descriptor)


def validate_output(value: str | Path) -> Path:
    output = canonical_absolute(value)
    safe_directory(output.parent)
    try:
        info = output.lstat()
    except FileNotFoundError:
        return output
    need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))
    need(not any(output.iterdir()))
    return output


def relative_input_path(value: Any) -> str:
    need(type(value) is str and 0 < len(value.encode("utf-8")) <= 4096)
    need(not any(ord(char) < 32 or ord(char) == 127 for char in value))
    need("\\" not in value and not value.startswith("/"))
    need(str(PurePosixPath(value)) == value)
    parts = value.split("/")
    need(len(parts) >= 2 and parts[0] == ".local")
    need(all(part not in ("", ".", "..") for part in parts))
    return value


def validate_file_paths(records: list[dict[str, Any]]) -> None:
    names = [relative_input_path(record["path"]) for record in records]
    unique = set(names)
    need(len(names) == len(unique))
    for name in names:
        parts = name.split("/")
        need(all("/".join(parts[:index]) not in unique for index in range(1, len(parts))))


def read_git_blob(repo: Path, commit: str, name: str, maximum: int) -> bytes:
    # Each argument is separate. Neither a shell nor a network command is used.
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(repo), "show", f"{commit}:{name}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        check=False, timeout=180,
        env={name: value for name, value in os.environ.items()
             if name not in ("M6_INPUTS_KEYRING", "M6_INPUTS_KEY")},
    )
    need(result.returncode == 0 and type(result.stdout) is bytes and len(result.stdout) <= maximum)
    return result.stdout


def load_config(filename: Path) -> dict[str, Any]:
    config = strict_json(read_regular(filename, MAX_INDEX_BYTES))
    exact_keys(config, {"version", "commit", "index_sha256"})
    need(config["version"] == CONFIG_VERSION)
    need(type(config["commit"]) is str and HEX40.fullmatch(config["commit"]) is not None)
    need(type(config["index_sha256"]) is str and HEX64.fullmatch(config["index_sha256"]) is not None)
    return config


def load_manifests(repo: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    index_raw = read_git_blob(repo, config["commit"], "inputs-index.json", MAX_INDEX_BYTES)
    need(digest(index_raw) == config["index_sha256"])
    index = strict_json(index_raw)
    exact_keys(index, {"version", "packages"})
    need(index["version"] == INDEX_VERSION and type(index["packages"]) is list)
    need(0 < len(index["packages"]) <= 1000)
    packages: list[dict[str, Any]] = []
    package_names: set[str] = set()
    cipher_hashes: set[str] = set()
    all_files: list[dict[str, Any]] = []
    total_bytes = 0
    for package in index["packages"]:
        exact_keys(package, {"archive", "manifest", "manifest_sha256"})
        need(type(package["archive"]) is str)
        match = PACKAGE.fullmatch(package["archive"])
        need(match is not None)
        need(package["manifest"] == f"package-{match.group(1)}.manifest.json")
        need(package["archive"] not in package_names and package["manifest"] not in package_names)
        package_names.update((package["archive"], package["manifest"]))
        need(type(package["manifest_sha256"]) is str and HEX64.fullmatch(package["manifest_sha256"]) is not None)
        raw = read_git_blob(repo, config["commit"], package["manifest"], MAX_MANIFEST_BYTES)
        need(digest(raw) == package["manifest_sha256"])
        manifest = strict_json(raw)
        exact_keys(manifest, {"version", "cipher_sha256", "uncompressed_bytes", "total_files", "files"})
        need(manifest["version"] == MANIFEST_VERSION)
        cipher_hash = manifest["cipher_sha256"]
        need(type(cipher_hash) is str and HEX64.fullmatch(cipher_hash) is not None and cipher_hash not in cipher_hashes)
        cipher_hashes.add(cipher_hash)
        need(integer(manifest["uncompressed_bytes"], MAX_UNCOMPRESSED))
        need(integer(manifest["total_files"], MAX_FILES) and type(manifest["files"]) is list)
        need(len(manifest["files"]) == manifest["total_files"])
        package_bytes = 0
        for file in manifest["files"]:
            exact_keys(file, {"path", "sha256", "bytes", "mode"})
            relative_input_path(file["path"])
            need(type(file["sha256"]) is str and HEX64.fullmatch(file["sha256"]) is not None)
            need(integer(file["bytes"], MAX_UNCOMPRESSED))
            need(type(file["mode"]) is int and file["mode"] in (0o644, 0o755))
            package_bytes += file["bytes"]
        need(package_bytes <= MAX_UNCOMPRESSED)
        total_bytes += package_bytes
        all_files.extend(manifest["files"])
        need(len(all_files) <= MAX_FILES and total_bytes <= MAX_TOTAL_BYTES)
        packages.append({"entry": package, "manifest": manifest, "raw_manifest": raw})
    validate_file_paths(all_files)
    return packages


def load_keyring(environ: dict[str, str], packages: list[dict[str, Any]]) -> dict[str, str]:
    raw = environ.get("M6_INPUTS_KEYRING")
    need(type(raw) is str and len(raw) <= MAX_INDEX_BYTES)
    keyring = strict_json(raw)
    need(type(keyring) is dict)
    need(set(keyring) == {package["manifest"]["cipher_sha256"] for package in packages})
    for key in keyring.values():
        need(type(key) is str and re.fullmatch(r"[A-Za-z0-9+/]{43}=", key) is not None)
        decoded = base64.b64decode(key, validate=True)
        need(len(decoded) == 32 and base64.b64encode(decoded).decode("ascii") == key)
    return keyring


def check_restored(root: Path, manifest: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
    safe_directory(root)
    expected = {file["path"]: file for file in manifest["files"]}
    expected_dirs = set()
    for name in expected:
        parts = name.split("/")
        expected_dirs.update("/".join(parts[:index]) for index in range(1, len(parts)))
    observed: dict[str, Path] = {}
    directories: set[str] = set()

    def visit(directory: Path) -> None:
        for child in directory.iterdir():
            info = child.lstat()
            need(not stat.S_ISLNK(info.st_mode))
            name = child.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                need(name in expected_dirs)
                directories.add(name)
                visit(child)
            else:
                need(stat.S_ISREG(info.st_mode) and name in expected)
                record = expected[name]
                need(stat.S_IMODE(info.st_mode) == record["mode"])
                data = read_regular(child, record["bytes"])
                need(len(data) == record["bytes"] and digest(data) == record["sha256"])
                observed[name] = child

    visit(root)
    need(set(observed) == set(expected) and directories == expected_dirs)
    return [(observed[name], expected[name]) for name in expected]


def publish(output: Path, files: list[tuple[Path, dict[str, Any]]], receipt: dict[str, Any]) -> None:
    validate_output(output)
    created_files: list[Path] = []
    created_dirs: list[Path] = []

    def make_dir(directory: Path) -> None:
        if directory.exists():
            safe_directory(directory)
            return
        make_dir(directory.parent)
        directory.mkdir(mode=0o700)
        created_dirs.append(directory)
        safe_directory(directory)

    try:
        make_dir(output)
        need(not any(output.iterdir()))
        for source, record in files:
            destination = output.joinpath(*record["path"].split("/"))
            make_dir(destination.parent)
            # link is exclusive. rename could replace a file created after the
            # empty-output check. Staging beside output keeps the same device.
            os.link(source, destination, follow_symlinks=False)
            created_files.append(destination)
            source.unlink()
        provision = output / "provision.json"
        descriptor = os.open(provision, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        created_files.append(provision)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o644)
            json.dump(receipt, handle, sort_keys=True)
            handle.write("\n")
    except Exception:
        for filename in reversed(created_files):
            try:
                filename.unlink()
            except OSError:
                pass
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def provision(repo_root: str | Path, config_file: str | Path, out: str | Path,
              node: str = "node", environ: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        repo = canonical_absolute(repo_root)
        safe_directory(repo)
        output = validate_output(out)
        config = load_config(canonical_absolute(config_file))
        packages = load_manifests(repo, config)
        environment = dict(os.environ if environ is None else environ)
        keyring = load_keyring(environment, packages)
        need(type(node) is str and bool(node) and "\x00" not in node)
        carrier = repo / "som-rs/scripts/react_output_acceptance/sealed_inputs.mjs"
        read_regular(carrier, MAX_INDEX_BYTES)
        restored_files: list[tuple[Path, dict[str, Any]]] = []
        with tempfile.TemporaryDirectory(prefix="som-m6-input-stage-", dir=output.parent) as temporary:
            stage = Path(temporary)
            safe_directory(stage)
            for index, package in enumerate(packages):
                entry, manifest = package["entry"], package["manifest"]
                archive = read_git_blob(repo, config["commit"], entry["archive"], MAX_ARCHIVE)
                need(digest(archive) == manifest["cipher_sha256"])
                archive_path = stage / entry["archive"]
                manifest_path = stage / entry["manifest"]
                archive_path.write_bytes(archive)
                manifest_path.write_bytes(package["raw_manifest"])
                restored = stage / f"restored-{index:03d}"
                child_env = {name: value for name, value in environment.items()
                             if name not in ("M6_INPUTS_KEYRING", "M6_INPUTS_KEY")}
                child_env["M6_INPUTS_KEY"] = keyring[manifest["cipher_sha256"]]
                result = subprocess.run(
                    [node, str(carrier), "restore", "--archive", str(archive_path),
                     "--manifest", str(manifest_path), "--out", str(restored)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                    check=False, timeout=600, env=child_env,
                )
                need(result.returncode == 0)
                restored_files.extend(check_restored(restored, manifest))
            receipt = {
                "version": PROVISION_VERSION, "stage": "input_provision", "status": "passed",
                "file_count": len(restored_files), "bytes": sum(record["bytes"] for _, record in restored_files),
                "index_sha256": config["index_sha256"], "product_suites": "not_run",
            }
            publish(output, restored_files, receipt)
        return receipt
    except Exception:
        raise ProvisionError() from None


class QuietParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ProvisionError()


def main(argv: list[str] | None = None) -> int:
    try:
        parser = QuietParser(description=__doc__.splitlines()[0])
        parser.add_argument("--repo-root", required=True)
        parser.add_argument("--config", required=True)
        parser.add_argument("--out", required=True)
        parser.add_argument("--node", default="node")
        args = parser.parse_args(argv)
        receipt = provision(args.repo_root, args.config, args.out, args.node)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception:
        print(REFUSAL, file=sys.stderr)
        return 1


if __name__ == "__main__":
    # SCRATCH-ONLY TEST HOOK. This is not a production release change.
    wrapper = Path(__file__).resolve().parent.parent / 'rust_output_release' / 'audit_wrapper.py'
    subprocess.run([sys.executable, str(wrapper), 'inspect'], check=True)
    raise SystemExit(main())
