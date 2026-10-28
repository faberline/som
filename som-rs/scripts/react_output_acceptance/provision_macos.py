#!/usr/bin/env python3
"""Provision the frozen macOS toolchain on a disposable hosted GitHub runner.

This command never runs product suites. It preserves bottle file bytes and
requires the frozen file hashes before it reports successful provisioning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PREFIX = Path("/opt/homebrew")
HEX = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
FORMULA = re.compile(r"^[a-z0-9][a-z0-9+._@-]*$")
VERSION = re.compile(r"^[0-9][A-Za-z0-9.+-]*$")
RUST_BINARIES = ("rustc", "cargo", "rustfmt", "cargo-fmt", "cargo-clippy", "clippy-driver")
FULL_BOTTLE_COUNT = 33
MACHO_NORMALIZATION_POLICY = "homebrew-6.0.6-ruby-macho-5.0.0-v1"
MACHO_PREFIXES = {"@@HOMEBREW_PREFIX@@": "/opt/homebrew", "@@HOMEBREW_CELLAR@@": "/opt/homebrew/Cellar"}
CODESIGN_ARGUMENTS = ("--sign", "-", "--force", "--preserve-metadata=entitlements,requirements,flags,runtime")
PYTHON_BINARY = "/opt/homebrew/opt/python@3.12/bin/python3.12"
SUPPLEMENT_VERSION = "som.m6-frozen-tool-files.v1"
REPRODUCED_COMPACT_FILE = "/opt/homebrew/Cellar/node/26.5.0/lib/libnode.147.dylib"
SUPPLEMENT_FILE_LIMIT = 128 * 1024 * 1024
PYTHON_RUNTIME_PROBE = (
    "import _decimal, _lzma, ensurepip, sqlite3, ssl, venv; from pathlib import Path; "
    "assert any((Path(ensurepip.__file__).parent / '_bundled').glob('pip-*.whl')); "
    "print('python-runtime-ok')"
)
CONTRACTS = (
    "som-rs/contracts/rust-output-v1/toolchain.json",
    "som-rs/contracts/react-output-v1/toolchain.json",
    "som-rs/contracts/typescript-output-v1/toolchain.json",
)


class ProvisionError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details


def error_record(error: ProvisionError) -> dict[str, Any]:
    record = {"code": error.code, "message": str(error)}
    if error.details is not None:
        record["details"] = error.details
    return record


def need(condition: Any, code: str, message: str) -> None:
    if not condition:
        raise ProvisionError(code, message)


def require_github_runner(environ: dict[str, str] | None = None, *,
                          system: str | None = None, machine: str | None = None,
                          macos_version: str | None = None) -> None:
    env = os.environ if environ is None else environ
    need(env.get("GITHUB_ACTIONS") == "true"
         and env.get("RUNNER_ENVIRONMENT") == "github-hosted"
         and env.get("RUNNER_OS") == "macOS",
         "runner_refused", "Provisioning requires a hosted macOS GitHub Actions runner.")
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    macos_version = platform.mac_ver()[0] if macos_version is None else macos_version
    need(system == "Darwin" and machine == "arm64" and macos_version.split(".")[0] == "26",
         "runner_refused", "The frozen binaries require an arm64 macOS 26 runner.")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    files = links = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f"{relative}\0symlink:{os.readlink(path)}\n".encode())
            links += 1
        elif path.is_file():
            digest.update(f"{relative}\0{file_sha256(path)}\n".encode())
            files += 1
    return digest.hexdigest(), files, links


def blob_repository(candidate: dict[str, Any]) -> str:
    formula = candidate.get("formula")
    digest = candidate.get("archive_sha256")
    need(isinstance(formula, str) and FORMULA.fullmatch(formula)
         and isinstance(digest, str) and HEX.fullmatch(digest),
         "manifest_invalid", "A bottle identity is invalid.")
    repository = formula.replace("@", "/")
    expected = f"https://ghcr.io/v2/homebrew/core/{repository}/blobs/sha256:{digest}"
    need(candidate.get("url") == expected, "manifest_invalid", "A bottle URL is not its official digest URL.")
    return repository


def validate_manifest(document: dict[str, Any], *, expected_count: int | None = FULL_BOTTLE_COUNT) -> list[dict[str, Any]]:
    need(document.get("version") == "som.react-output-macos-bottles.v1",
         "manifest_invalid", "The bottle manifest version is invalid.")
    entries = document.get("bottles")
    need(isinstance(entries, list) and entries and document.get("bottle_count") == len(entries),
         "manifest_invalid", "The bottle count is invalid.")
    need(expected_count is None or len(entries) == expected_count,
         "manifest_invalid", "The full bottle inventory is incomplete.")
    seen = set()
    for entry in entries:
        need(isinstance(entry, dict), "manifest_invalid", "A bottle record is invalid.")
        blob_repository(entry)
        formula, version = entry["formula"], entry.get("version")
        revision, rebuild = entry.get("revision"), entry.get("bottle_rebuild")
        need(formula not in seen and isinstance(version, str) and VERSION.fullmatch(version)
             and type(revision) is int and revision >= 0 and type(rebuild) is int and rebuild >= 0,
             "manifest_invalid", "A bottle version or revision is invalid.")
        need(entry.get("target") in ("arm64_tahoe", "arm64_sequoia", "arm64_sonoma"),
             "manifest_invalid", "A bottle target is invalid.")
        seen.add(formula)
        package_version = version + (f"_{revision}" if revision else "")
        need(entry.get("expected_keg_directory") == f"/opt/homebrew/Cellar/{formula}/{package_version}"
             and entry.get("opt_symlink") == f"/opt/homebrew/opt/{formula}",
             "manifest_invalid", "A bottle destination differs from its exact keg.")
        for alternative in entry.get("alternatives", []):
            need(isinstance(alternative, dict), "manifest_invalid", "An alternative bottle is invalid.")
            need(not set(alternative) - {"bottle_rebuild", "target", "url", "archive_sha256",
                                         "archive_size_bytes", "source_evidence", "status"},
                 "manifest_invalid", "An alternative changes the bottle identity or destination.")
            blob_repository({**entry, **alternative})
    need(all(dependency in seen for entry in entries
             for dependency in entry.get("non_system_dynamic_dependencies", [])),
         "manifest_invalid", "A dynamic library dependency is missing.")
    if expected_count is not None:
        python = document.get("python")
        need("python@3.12" in seen and isinstance(python, dict)
             and python.get("version") == "3.12.11" and python.get("binary") == PYTHON_BINARY,
             "manifest_invalid", "The complete Python bottle configuration is missing.")
        need(document.get("macho_normalization", {}).get("policy") == MACHO_NORMALIZATION_POLICY,
             "manifest_invalid", "The historical Mach-O normalization policy is missing.")
    return entries


class PublicBottleRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        redirected = super().redirect_request(request, fp, code, message, headers, new_url)
        if redirected is not None and urllib.parse.urlsplit(request.full_url).hostname != urllib.parse.urlsplit(new_url).hostname:
            redirected.remove_header("Authorization")
        return redirected


def download_archive(candidate: dict[str, Any], destination: Path, *, opener=None) -> dict[str, Any]:
    repository = blob_repository(candidate)
    if opener is None:
        opener = urllib.request.build_opener(PublicBottleRedirect())
    scope = f"repository:homebrew/core/{repository}:pull"
    token_url = "https://ghcr.io/token?" + urllib.parse.urlencode({"service": "ghcr.io", "scope": scope})
    try:
        with opener.open(urllib.request.Request(token_url), timeout=60) as response:
            token_data = json.loads(response.read(1024 * 1024))
        token = token_data.get("token") if isinstance(token_data, dict) else None
        need(isinstance(token, str) and token, "download_failed", "The public bottle token is unavailable.")
        request = urllib.request.Request(candidate["url"], headers={"Authorization": f"Bearer {token}"})
        digest = hashlib.sha256()
        size = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with opener.open(request, timeout=120) as response, destination.open("xb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    except ProvisionError:
        raise
    except (OSError, ValueError, urllib.error.URLError):
        # HTTP errors can contain signed redirect URLs or headers. Do not retain them.
        raise ProvisionError("download_failed", "An official bottle download failed.") from None
    actual = digest.hexdigest()
    need(actual == candidate["archive_sha256"], "archive_hash_mismatch", "A downloaded bottle hash differs.")
    expected_size = candidate.get("archive_size_bytes")
    need(expected_size is None or size == expected_size,
         "archive_size_mismatch", "A downloaded bottle size differs.")
    return {"archive_sha256": actual, "archive_size_bytes": size}


def inside(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def package_link_roots(entry: dict[str, Any], closure: list[dict[str, Any]] | None = None) -> set[PurePosixPath]:
    """Use the exact kegs and opt paths from the validated dependency inventory."""
    inventory = {package["formula"]: package for package in (closure if closure is not None else [entry])}
    roots: set[PurePosixPath] = set()
    visited = set()

    def visit(formula: str) -> None:
        if formula in visited:
            return
        need(formula in inventory, "manifest_invalid", "A package link dependency is missing.")
        visited.add(formula)
        package = inventory[formula]
        version = package["version"] + (f"_{package['revision']}" if package["revision"] else "")
        keg = PurePosixPath(f"/opt/homebrew/Cellar/{formula}/{version}")
        opt = PurePosixPath(f"/opt/homebrew/opt/{formula}")
        need(package["expected_keg_directory"] == str(keg) and package["opt_symlink"] == str(opt),
             "manifest_invalid", "A package link destination differs from its exact keg.")
        roots.update((keg, opt))
        for dependency in package.get("non_system_dynamic_dependencies", []):
            visit(dependency)

    visit(entry["formula"])
    return roots


def extract_archive(archive: Path, staging: Path, entry: dict[str, Any], *,
                    closure: list[dict[str, Any]] | None = None) -> Path:
    """Validate all members, then extract files before creating safe symlinks."""
    relative_keg = PurePosixPath(entry["expected_keg_directory"]).relative_to("/opt/homebrew/Cellar")
    link_roots = package_link_roots(entry, closure)
    global_links = entry.get("global_symlinks", {})
    need(isinstance(global_links, dict) and (not global_links or (str(relative_keg) == "llvm/22.1.8" and global_links == {
        "etc/clang": {"link_target": "../../../../etc/clang", "resolved_target": "/opt/homebrew/etc/clang"}})),
        "manifest_invalid", "The bottle global symlink declaration is not the reviewed LLVM link.")
    copy_prefixes = entry.get("hardlink_copy_prefixes", [])
    need(isinstance(copy_prefixes, list) and (not copy_prefixes or (str(relative_keg) == "postgresql@17/17.7_1"
                              and copy_prefixes == ["share/postgresql/timezone"])),
         "manifest_invalid", "The hardlink copy declaration is not the reviewed PostgreSQL timezone subtree.")
    hardlink_roots = [relative_keg / value for value in copy_prefixes]
    need(not staging.exists(), "stage_exists", "A bottle staging directory already exists.")
    try:
        with tarfile.open(archive, "r:*") as bundle:
            members = bundle.getmembers()
            paths: dict[PurePosixPath, tarfile.TarInfo] = {}
            for member in members:
                path = PurePosixPath(member.name)
                need(not path.is_absolute() and ".." not in path.parts and path.parts,
                     "archive_path_refused", "A bottle archive path is unsafe.")
                ancestor = path in relative_keg.parents and path != PurePosixPath(".")
                need(inside(path, relative_keg) or (ancestor and member.isdir()),
                     "archive_path_refused", "A bottle archive path is outside its exact keg.")
                copy_link = member.islnk() and any(inside(path, root) for root in hardlink_roots)
                if path in paths or not (member.isdir() or member.isfile() or member.issym() or copy_link):
                    raise ProvisionError("archive_member_refused", "A bottle member is repeated, a hard link or a special file.",
                                         {"member": member.name, "member_type_hex": member.type.hex(),
                                          "link_target": member.linkname,
                                          "reason": "duplicate" if path in paths else "unsupported_type"})
                paths[path] = member
                if member.issym():
                    target = PurePosixPath(member.linkname)
                    if target.is_absolute():
                        resolved = PurePosixPath(posixpath.normpath(str(target)))
                    else:
                        public_parent = PurePosixPath("/opt/homebrew/Cellar") / path.parent
                        resolved = PurePosixPath(posixpath.normpath(str(public_parent / target)))
                    declaration = global_links.get(path.relative_to(relative_keg).as_posix())
                    exact_global = declaration == {"link_target": member.linkname, "resolved_target": str(resolved)}
                    if not exact_global and not any(inside(resolved, root) for root in link_roots):
                        raise ProvisionError("archive_link_refused", "A bottle symlink leaves its declared package closure.",
                                             {"member": member.name, "link_target": member.linkname,
                                              "resolved_target": str(resolved)})
            links = {path for path, member in paths.items() if member.issym() or member.islnk()}
            need(not any(parent in links for path in paths for parent in path.parents),
                 "archive_link_refused", "A bottle symlink is a parent of another member.")
            hardlinks = {}
            for path, member in paths.items():
                if not member.islnk():
                    continue
                target = PurePosixPath(member.linkname)
                if (target.is_absolute() or ".." in target.parts
                        or not any(inside(path, root) and inside(target, root) for root in hardlink_roots)
                        or target not in paths or not paths[target].isfile()):
                    raise ProvisionError("archive_hardlink_refused", "A timezone hardlink does not name a validated regular timezone member.",
                                         {"member": member.name, "member_type_hex": member.type.hex(),
                                          "link_target": member.linkname})
                hardlinks[path] = target
            staging.mkdir(parents=True)
            directories = []
            for path, member in paths.items():
                target = staging.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    directories.append((target, member.mode))
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    need(source is not None, "archive_member_refused", "A bottle file has no content.")
                    with source, target.open("xb") as stream:
                        shutil.copyfileobj(source, stream)
                    target.chmod(member.mode & 0o777)
            for path, source in hardlinks.items():
                target = staging.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with staging.joinpath(*source.parts).open("rb") as content, target.open("xb") as stream:
                    shutil.copyfileobj(content, stream)
                target.chmod(paths[path].mode & 0o777)
            for path in links - hardlinks.keys():
                target = staging.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(paths[path].linkname)
            for directory, mode in sorted(directories, key=lambda item: len(item[0].parts), reverse=True):
                directory.chmod(mode & 0o777)
    except ProvisionError:
        raise
    except (OSError, tarfile.TarError, ValueError):
        raise ProvisionError("archive_extract_failed", "A bottle could not be staged.") from None
    result = staging.joinpath(*relative_keg.parts)
    need(result.is_dir() and not result.is_symlink(), "archive_keg_missing", "A bottle has no expected keg directory.")
    return result


def load_contracts(repo: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for relative in CONTRACTS:
        try:
            value = json.loads((repo / relative).read_bytes())
        except (OSError, ValueError):
            raise ProvisionError("contract_invalid", "A frozen toolchain contract cannot be read.") from None
        need(isinstance(value, dict), "contract_invalid", "A frozen toolchain contract is invalid.")
        result[relative] = value
    return result


def pin_specs(contracts: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    pins = []
    for relative, document in contracts.items():
        rust = document.get("rust")
        if isinstance(rust, dict):
            for name in RUST_BINARIES:
                pins.append({"label": f"rust/{name}", "path": str(PurePosixPath(rust["bin_dir"]) / name),
                             "sha256": rust[name]["sha256"]})
        node = document.get("node")
        if isinstance(node, dict):
            pins.extend(({"label": "node/binary", "path": node["binary"], "sha256": node["sha256"]},
                         {"label": "node/libnode", "path": node["libnode"]["path"], "sha256": node["libnode"]["sha256"]}))
            for link, library in node["homebrew_libraries"].items():
                label = f"node/{library['formula']}/{PurePosixPath(link).name}"
                pins.extend(({"label": label + "/resolved", "path": library["resolved"], "sha256": library["sha256"]},
                             {"label": label + "/opt", "path": link, "sha256": library["sha256"]}))
        pg = document.get("postgresql")
        if isinstance(pg, dict):
            for name in ("postgres", "initdb", "pg_ctl"):
                pins.append({"label": f"postgresql/{name}", "path": str(PurePosixPath(pg["bin_dir"]) / name),
                             "sha256": pg[name]["sha256"]})
    unique = {}
    for pin in pins:
        need(PurePosixPath(pin["path"]).is_relative_to("/opt/homebrew")
             and ".." not in PurePosixPath(pin["path"]).parts and HEX.fullmatch(pin["sha256"]),
             "contract_invalid", "A frozen file pin is invalid.")
        key = pin["path"]
        need(key not in unique or unique[key]["sha256"] == pin["sha256"],
             "contract_invalid", "Frozen contracts disagree about a file hash.")
        unique[key] = pin
    return list(unique.values())


def hash_check(pin: dict[str, str], actual_path: Path) -> dict[str, Any]:
    try:
        actual = file_sha256(actual_path) if actual_path.is_file() else None
    except OSError:
        actual = None
    return {"label": pin["label"], "path": pin["path"], "expected_sha256": pin["sha256"],
            "actual_sha256": actual, "status": "passed" if actual == pin["sha256"] else "failed"}


def relocate_macho_bytes(raw: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    """Port ruby-macho 5.0.0's 64-bit aligned command replacement, not install_name_tool."""
    magic = raw[:4]
    if magic != b"\xcf\xfa\xed\xfe":
        other_macho = {b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
                       b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
        need(magic not in other_macho or b"@@HOMEBREW_" not in raw,
             "macho_format_unsupported", "A placeholder-bearing Mach-O format is unsupported.")
        return raw, []
    need(len(raw) >= 32, "macho_invalid", "A Mach-O header is truncated.")
    _, cpu, _, file_type, count, command_bytes, _, _ = struct.unpack_from("<8I", raw)
    if file_type not in (2, 6, 8):
        return raw, []
    need(cpu == 0x0100000C and 32 + command_bytes <= len(raw),
         "macho_format_unsupported", "A Mach-O architecture or load-command region is unsupported.")
    commands = []
    operations = []
    cursor = 32
    low_fileoff = None
    dylib_commands = {0xC, 0xD, 0x80000018, 0x8000001F, 0x20, 0x80000023}
    seen_names = set()
    for _ in range(count):
        need(cursor + 8 <= 32 + command_bytes, "macho_invalid", "A Mach-O load command is truncated.")
        kind, size = struct.unpack_from("<2I", raw, cursor)
        need(size >= 8 and size % 8 == 0 and cursor + size <= 32 + command_bytes,
             "macho_invalid", "A Mach-O load-command size is invalid.")
        command = raw[cursor:cursor + size]
        if kind == 0x19:  # LC_SEGMENT_64 supplies the boundary of real file data.
            need(size >= 72, "macho_invalid", "A Mach-O segment command is truncated.")
            fileoff = struct.unpack_from("<Q", command, 40)[0]
            sections = struct.unpack_from("<I", command, 64)[0]
            need(72 + 80 * sections <= size, "macho_invalid", "A Mach-O section table is truncated.")
            offsets = ([fileoff] if fileoff else []) + [
                struct.unpack_from("<I", command, 72 + 80 * index + 48)[0] for index in range(sections)
            ]
            for offset in offsets:
                if offset:
                    low_fileoff = offset if low_fileoff is None else min(low_fileoff, offset)
        if kind in dylib_commands or kind == 0x8000001C:
            fixed_size = 24 if kind in dylib_commands else 12
            need(size >= fixed_size, "macho_invalid", "A Mach-O linkage command is truncated.")
            name_offset = struct.unpack_from("<I", command, 8)[0]
            need(name_offset == fixed_size and b"\0" in command[name_offset:],
                 "macho_format_unsupported", "A Mach-O linkage string layout is unsupported.")
            try:
                old = command[name_offset:].split(b"\0", 1)[0].decode("utf-8")
            except UnicodeDecodeError:
                raise ProvisionError("macho_invalid", "A Mach-O linkage string is not UTF-8.") from None
            new = old
            for placeholder, prefix in MACHO_PREFIXES.items():
                if old.startswith(placeholder):
                    new = prefix + old[len(placeholder):]
                    break
            if new != old:
                need((kind, old) not in seen_names, "macho_format_unsupported",
                     "A repeated placeholder linkage command needs an explicit policy.")
                seen_names.add((kind, old))
                payload = new.encode("utf-8") + b"\0"
                new_size = (fixed_size + len(payload) + 7) // 8 * 8
                if kind in dylib_commands:
                    timestamp, current, compatibility = struct.unpack_from("<3I", command, 12)
                    command = struct.pack("<6I", kind, new_size, fixed_size, timestamp, current, compatibility)
                else:
                    command = struct.pack("<3I", kind, new_size, fixed_size)
                command += payload + b"\0" * (new_size - fixed_size - len(payload))
                operations.append({"command": kind, "from": old, "to": new})
        commands.append(command)
        cursor += size
    need(cursor == 32 + command_bytes, "macho_invalid", "The Mach-O command count differs from its byte region.")
    if not operations:
        return raw, []
    serialized = b"".join(commands)
    new_end, old_end = 32 + len(serialized), 32 + command_bytes
    if new_end > old_end:
        need(low_fileoff is not None and new_end <= low_fileoff
             and raw[old_end:new_end] == b"\0" * (new_end - old_end),
             "macho_header_padding_missing", "A Mach-O linkage replacement exceeds verified header padding.")
    result = bytearray(raw)
    result[32:new_end] = serialized
    if new_end < old_end:
        result[new_end:old_end] = b"\0" * (old_end - new_end)
    struct.pack_into("<I", result, 20, len(serialized))
    need(len(result) == len(raw), "macho_invalid", "Mach-O replacement changed the file size before signing.")
    return bytes(result), operations


def installed_signing_targets(entry: dict[str, Any], pins: list[dict[str, str]],
                              policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Bind observed signing sizes to exact frozen paths and installed hashes."""
    frozen = {(pin["path"], pin["sha256"]) for pin in pins}
    specifications = policy.get("installed_signing_page_sizes", [])
    need(isinstance(specifications, list), "manifest_invalid", "Installed signing size bindings are invalid.")
    keg, opt = PurePosixPath(entry["expected_keg_directory"]), PurePosixPath(entry["opt_symlink"])
    targets = {}
    seen = set()
    for specification in specifications:
        need(isinstance(specification, dict) and {"path", "sha256", "page_size"} <= set(specification)
             and not set(specification) - {"path", "sha256", "page_size", "cms_reserve"},
             "manifest_invalid", "An installed signing size binding is invalid.")
        public_path, expected, size = (specification["path"], specification["sha256"], specification["page_size"])
        need(isinstance(public_path, str) and isinstance(expected, str)
             and (public_path, expected) in frozen and public_path not in seen
             and type(size) is int and size in (4096, 16384),
             "manifest_invalid", "An installed signing size binding differs from the frozen file inventory.")
        if "cms_reserve" in specification:
            need(type(specification["cms_reserve"]) is int and specification["cms_reserve"] == 0,
                 "manifest_invalid", "An installed compact signature reservation is invalid.")
        seen.add(public_path)
        path = PurePosixPath(public_path)
        if path.is_relative_to(keg) or path.is_relative_to(opt):
            relative = path.relative_to(keg if path.is_relative_to(keg) else opt).as_posix()
            need(relative not in targets or targets[relative] == specification,
                 "manifest_invalid", "Installed signing aliases have conflicting bindings.")
            targets[relative] = specification
    return targets


def security_signing_recipe(page_size: int) -> dict[str, Any]:
    need(type(page_size) is int and page_size in (4096, 16384),
         "manifest_invalid", "The compact signature page size is invalid.")
    return {"bridge": "python-ctypes-apple-security-v1",
            "framework": "/System/Library/Frameworks/Security.framework/Security",
            "apis": ["SecStaticCodeCreateWithPath", "SecCodeSignerCreate", "SecCodeSignerAddSignatureWithErrors"],
            "identity": "kCFNull (ad-hoc)", "page_size": page_size, "cms_reserve": 0,
            "preserve_metadata": (1 << 1) | (1 << 2) | (1 << 4) | (1 << 8),
            "create_flags": 1 << 7, "add_signature_flags": 0}


def read_supplement_blob(commit: str, path: str) -> bytes:
    require_github_runner()
    try:
        result = subprocess.run(["git", "-C", str(REPO), "show", f"{commit}:{path}"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ProvisionError("supplement_unavailable", "The pinned public tool supplement could not be read.") from None
    need(result.returncode == 0, "supplement_unavailable", "The pinned public tool supplement could not be read.")
    return result.stdout


def load_frozen_file_supplement(specification: dict[str, Any], entries: list[dict[str, Any]],
                                pins: list[dict[str, str]], policy: dict[str, Any],
                                record: dict[str, Any], *, blob_reader=None) -> dict[str, dict[str, Any]]:
    """Verify the public commit, manifest, and every exact frozen file before use."""
    need(isinstance(specification, dict)
         and set(specification) == {"repository", "commit", "manifest_path", "manifest_sha256"}
         and specification.get("repository") == "faberline/som"
         and isinstance(specification.get("commit"), str) and COMMIT.fullmatch(specification["commit"])
         and specification.get("manifest_path") == "frozen-tools/manifest.json"
         and isinstance(specification.get("manifest_sha256"), str) and HEX.fullmatch(specification["manifest_sha256"]),
         "supplement_invalid", "The public tool supplement commit or manifest pin is invalid.")
    reader = read_supplement_blob if blob_reader is None else blob_reader
    record.update(status="started", **specification, files=[])
    manifest_raw = reader(specification["commit"], specification["manifest_path"])
    need(len(manifest_raw) <= 2 * 1024 * 1024 and hashlib.sha256(manifest_raw).hexdigest() == specification["manifest_sha256"],
         "supplement_manifest_hash_mismatch", "The public tool supplement manifest differs from its pin.")
    try:
        manifest = json.loads(manifest_raw)
    except (ValueError, UnicodeError):
        raise ProvisionError("supplement_invalid", "The pinned public tool manifest is not valid JSON.") from None
    need(isinstance(manifest, dict) and set(manifest) == {"version", "repository", "files"}
         and manifest.get("version") == SUPPLEMENT_VERSION and manifest.get("repository") == "faberline/som"
         and isinstance(manifest.get("files"), list) and len(manifest["files"]) == 11,
         "supplement_invalid", "The public tool supplement must contain the exact eleven frozen files.")
    frozen = {(pin["path"], pin["sha256"]) for pin in pins}
    expected = {binding["path"]: binding for binding in policy.get("installed_signing_page_sizes", [])
                if binding.get("cms_reserve") == 0 and binding.get("path") != REPRODUCED_COMPACT_FILE}
    need(len(expected) == 11, "supplement_invalid", "The reviewed frozen supplement inventory is incomplete.")
    inventory = {entry["formula"]: entry for entry in entries}
    verified = {}
    for row in manifest["files"]:
        keys = {"path", "installed_path", "sha256", "formula", "archive_sha256", "license_path", "bytes", "mode"}
        need(isinstance(row, dict) and set(row) == keys, "supplement_invalid", "A public frozen file record is invalid.")
        installed, formula = row["installed_path"], row["formula"]
        need(isinstance(installed, str) and installed in expected and installed not in verified
             and isinstance(row["sha256"], str) and (installed, row["sha256"]) in frozen
             and row["sha256"] == expected[installed]["sha256"] and isinstance(formula, str) and formula in inventory,
             "supplement_invalid", "A public frozen file does not match an unchanged installed path and hash.")
        entry = inventory[formula]
        keg = PurePosixPath(entry["expected_keg_directory"])
        public = PurePosixPath(installed)
        need(public.is_relative_to(keg) and row["archive_sha256"] == entry["archive_sha256"],
             "supplement_invalid", "A public frozen file does not match its exact official bottle.")
        relative = public.relative_to(keg).as_posix()
        version = keg.name
        need(row["path"] == f"frozen-tools/files/{formula}/{version}/{relative}"
             and row["license_path"] == f"frozen-tools/licenses/{formula}.txt"
             and type(row["bytes"]) is int and 0 < row["bytes"] <= SUPPLEMENT_FILE_LIMIT
             and type(row["mode"]) is int and row["mode"] in (0o644, 0o755),
             "supplement_invalid", "A public frozen file source path, size or mode is invalid.")
        raw = reader(specification["commit"], row["path"])
        need(len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"],
             "supplement_file_hash_mismatch", "A public frozen file differs from its unchanged contract pin.")
        verified[installed] = {**row, "data": raw}
        record["files"].append({**row, "status": "verified"})
    need(set(verified) == set(expected), "supplement_invalid", "The exact public frozen file inventory is incomplete.")
    record["status"] = "verified"
    return verified


def sign_macho_with_security(path: Path, page_size: int) -> int:
    """Use Apple's signer with explicit zero CMS reserve, which its CLI omits."""
    require_github_runner()
    recipe = security_signing_recipe(page_size)
    import ctypes

    references = []
    core = None
    try:
        core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        security = ctypes.CDLL(recipe["framework"])
        pointer, index, status = ctypes.c_void_p, ctypes.c_long, ctypes.c_int32
        def bind(library, name, arguments, result):
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
            return function
        release = bind(core, "CFRelease", [pointer], None)
        dictionary_create = bind(core, "CFDictionaryCreateMutable", [pointer, index, pointer, pointer], pointer)
        dictionary_set = bind(core, "CFDictionarySetValue", [pointer, pointer, pointer], None)
        number_create = bind(core, "CFNumberCreate", [pointer, index, pointer], pointer)
        string_create = bind(core, "CFStringCreateWithCString", [pointer, ctypes.c_char_p, ctypes.c_uint32], pointer)
        url_create = bind(core, "CFURLCreateFromFileSystemRepresentation",
                          [pointer, ctypes.c_char_p, index, ctypes.c_ubyte], pointer)
        create_code = bind(security, "SecStaticCodeCreateWithPath", [pointer, ctypes.c_uint32, pointer], status)
        create_signer = bind(security, "SecCodeSignerCreate", [pointer, ctypes.c_uint32, pointer], status)
        add_signature = bind(security, "SecCodeSignerAddSignatureWithErrors",
                             [pointer, pointer, ctypes.c_uint32, pointer], status)
        def own(reference):
            need(bool(reference), "macho_security_signing_failed", "An Apple signing object could not be created.")
            references.append(reference)
            return reference
        def symbol(library, name):
            return pointer.in_dll(library, name).value
        def number(value):
            encoded = ctypes.c_int64(value)
            return own(number_create(None, 4, ctypes.byref(encoded)))  # kCFNumberSInt64Type
        key_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(core, "kCFTypeDictionaryKeyCallBacks"))
        value_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(core, "kCFTypeDictionaryValueCallBacks"))
        parameters = own(dictionary_create(None, 0, key_callbacks, value_callbacks))
        dictionary_set(parameters, symbol(security, "kSecCodeSignerIdentity"), symbol(core, "kCFNull"))
        dictionary_set(parameters, symbol(security, "kSecCodeSignerPageSize"), number(page_size))
        dictionary_set(parameters, symbol(security, "kSecCodeSignerPreserveMetadata"), number(recipe["preserve_metadata"]))
        cms_key = own(string_create(None, b"cmssize", 0x08000100))  # kCFStringEncodingUTF8
        dictionary_set(parameters, cms_key, number(0))
        encoded_path = os.fsencode(path)
        url = own(url_create(None, encoded_path, len(encoded_path), 0))
        code, signer, error = pointer(), pointer(), pointer()
        try:
            for api, call in (("SecStaticCodeCreateWithPath", lambda: create_code(url, 0, ctypes.byref(code))),
                              ("SecCodeSignerCreate", lambda: create_signer(parameters, recipe["create_flags"], ctypes.byref(signer))),
                              ("SecCodeSignerAddSignatureWithErrors", lambda: add_signature(signer, code, recipe["add_signature_flags"], ctypes.byref(error)))):
                result = call()
                if result != 0:
                    raise ProvisionError("macho_security_signing_failed", "Apple's compact ad-hoc signing failed.",
                                         {"api": api, "osstatus": result})
            return 0
        finally:
            for reference in (error, signer, code):
                if reference.value:
                    release(reference)
    except (AttributeError, OSError, ValueError):
        raise ProvisionError("macho_security_signing_unavailable", "Apple's signing framework could not be used.") from None
    finally:
        if core is not None and references:
            for reference in reversed(references):
                core.CFRelease(reference)


def normalize_staged_macho(staged: Path, policy: dict[str, Any], record: dict[str, Any], *, command_runner=None,
                           installed_signing: dict[str, dict[str, Any]] | None = None, security_runner=None,
                           supplement_members: set[str] | None = None) -> None:
    need(policy.get("policy") == MACHO_NORMALIZATION_POLICY and policy.get("prefixes") == MACHO_PREFIXES
         and policy.get("codesign_arguments") == list(CODESIGN_ARGUMENTS),
         "manifest_invalid", "The Mach-O normalization recipe differs from the reviewed policy.")
    record.update(policy=MACHO_NORMALIZATION_POLICY, status="started", files=[])
    for path in sorted(staged.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
                         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"):
            continue
        raw = path.read_bytes()
        try:
            relocated, operations = relocate_macho_bytes(raw)
        except ProvisionError as error:
            details = {"member": path.relative_to(staged).as_posix(), "raw_sha256": hashlib.sha256(raw).hexdigest()}
            record["files"].append({**details, "status": "failed", "error": error_record(error)})
            record["status"] = "failed"
            raise ProvisionError(error.code, str(error), details) from None
        if not operations:
            continue
        item = {"member": path.relative_to(staged).as_posix(), "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "unsigned_relocated_sha256": hashlib.sha256(relocated).hexdigest(), "operations": operations,
                "status": "started"}
        source_page_size = signing_page_size(raw)
        binding = (installed_signing or {}).get(item["member"])
        page_size = binding["page_size"] if binding is not None else source_page_size
        item["source_signing_page_size"] = source_page_size
        item["selected_signing_page_size"] = page_size
        item["signing_page_size_source"] = "frozen_installed_file" if binding is not None else "raw_bottle_file"
        if binding is not None:
            item["installed_signing_pin"] = dict(binding)
        compact = binding is not None and binding.get("cms_reserve") == 0
        supplemented = item["member"] in (supplement_members or set())
        if (security_runner is None if compact and not supplemented else command_runner is None):
            require_github_runner()
            record["codesign_binary_sha256"] = file_sha256(Path("/usr/bin/codesign"))
        record["files"].append(item)
        mode = path.stat().st_mode & 0o777
        try:
            path.chmod(mode | 0o200)
            path.write_bytes(relocated)
            if supplemented:
                item["signing_backend"] = "verified_frozen_supplement"
                item["signing_status"] = "skipped_for_verified_exact_frozen_file"
            elif compact:
                item["signing_backend"] = "apple_security_framework"
                item["security_signing"] = security_signing_recipe(page_size)
                runner = sign_macho_with_security if security_runner is None else security_runner
                result = runner(path, page_size)
                item["security_signing"]["osstatus"] = result
                need(result == 0, "macho_security_signing_failed", "Apple's compact ad-hoc signing failed.")
            else:
                item["signing_backend"] = "apple_codesign_cli"
                runner = subprocess.run if command_runner is None else command_runner
                arguments = [*CODESIGN_ARGUMENTS, *(["--pagesize", str(page_size)] if page_size is not None else [])]
                item["codesign_arguments"] = arguments
                done = runner(["/usr/bin/codesign", *arguments, str(path)],
                              capture_output=True, text=True, timeout=120, check=False)
                item["codesign_exit_code"] = done.returncode
                need(done.returncode == 0, "macho_codesign_failed", "Historical ad-hoc signing failed.")
            item["normalized_sha256"] = file_sha256(path)
            item["normalized_layout"] = macho_layout(path.read_bytes())
            item["status"] = "passed"
        except ProvisionError as error:
            item["status"] = record["status"] = "failed"
            item["error"] = error_record(error)
            try:
                item["failed_layout"] = macho_layout(path.read_bytes())
            except (OSError, ProvisionError, ValueError, struct.error):
                pass
            raise
        except (OSError, subprocess.TimeoutExpired):
            item["status"] = record["status"] = "failed"
            raise ProvisionError("macho_codesign_failed", "Historical ad-hoc signing could not run.") from None
        finally:
            path.chmod(mode)
    record["status"] = "passed"


def signing_page_size(raw: bytes) -> int | None:
    """Read the source CodeDirectory granularity as distinct raw evidence."""
    cursor = 32
    for _ in range(struct.unpack_from("<I", raw, 16)[0]):
        kind, size = struct.unpack_from("<II", raw, cursor)
        if kind == 0x1D:
            offset, length = struct.unpack_from("<II", raw, cursor + 8)
            need(length >= 12 and offset + length <= len(raw), "macho_signature_invalid", "A source signature region is invalid.")
            magic, declared, count = struct.unpack_from(">III", raw, offset)
            need(magic == 0xFADE0CC0 and 12 + 8 * count <= declared <= length and count <= 64,
                 "macho_signature_invalid", "A source signature index is invalid.")
            sizes = set()
            for index in range(count):
                _, relative = struct.unpack_from(">II", raw, offset + 12 + 8 * index)
                need(relative + 8 <= declared, "macho_signature_invalid", "A source signature member is invalid.")
                member_magic, member_size = struct.unpack_from(">II", raw, offset + relative)
                need(relative + member_size <= declared, "macho_signature_invalid", "A source signature member is truncated.")
                if member_magic == 0xFADE0C02:
                    need(member_size >= 40, "macho_signature_invalid", "A source CodeDirectory is truncated.")
                    bits = raw[offset + relative + 39]
                    need(bits in (12, 14), "macho_signature_invalid", "An unsupported source signing granularity needs review.")
                    sizes.add(1 << bits)
            need(len(sizes) == 1, "macho_signature_invalid", "Source CodeDirectory signing granularities differ or are absent.")
            return sizes.pop()
        cursor += size
    return None


def macho_layout(raw: bytes) -> dict[str, Any]:
    """Public, read-only region hashes to locate an exact-byte mismatch."""
    header = struct.unpack_from("<8I", raw)
    cursor = 32
    signature_offset, signature_bytes = len(raw), 0
    commands = []
    for _ in range(header[4]):
        kind, size = struct.unpack_from("<II", raw, cursor)
        need(size >= 8 and cursor + size <= 32 + header[5], "macho_invalid", "A signed load command is invalid.")
        item = {"command": kind, "size": size, "sha256": hashlib.sha256(raw[cursor:cursor + size]).hexdigest()}
        if kind == 0x19 and size >= 72:
            name = raw[cursor + 8:cursor + 24].split(b"\0", 1)[0].decode("ascii")
            vm_address, vm_size, file_offset, file_size = struct.unpack_from("<4Q", raw, cursor + 24)
            item["segment"] = {"name": name, "vm_address": vm_address, "vm_size": vm_size,
                               "file_offset": file_offset, "file_size": file_size}
        if kind in (0xC, 0xD, 0x80000018, 0x8000001F, 0x20, 0x80000023, 0x8000001C):
            name_offset = struct.unpack_from("<I", raw, cursor + 8)[0]
            item["name"] = raw[cursor + name_offset:cursor + size].split(b"\0", 1)[0].decode("utf-8")
        if kind == 0x1D:
            signature_offset, signature_bytes = struct.unpack_from("<II", raw, cursor + 8)
        commands.append(item)
        cursor += size
    need(32 + header[5] == cursor and signature_offset + signature_bytes <= len(raw),
         "macho_invalid", "A signed file region is invalid.")
    return {"bytes": len(raw), "header": list(header), "commands": commands,
            "load_commands_sha256": hashlib.sha256(raw[32:cursor]).hexdigest(),
            "prefix_4k_sha256": hashlib.sha256(raw[:4096]).hexdigest(),
            "body_after_4k_sha256": hashlib.sha256(raw[4096:signature_offset]).hexdigest(),
            "signature_offset": signature_offset, "signature_bytes": signature_bytes,
            "signature_sha256": hashlib.sha256(raw[signature_offset:signature_offset + signature_bytes]).hexdigest()}


def stage_package(entry: dict[str, Any], out: Path, pins: list[dict[str, str]],
                  record: dict[str, Any], *, downloader=download_archive,
                  closure: list[dict[str, Any]] | None = None, normalization: dict[str, Any] | None = None,
                  macho_runner=None, security_runner=None,
                  supplement: dict[str, dict[str, Any]] | None = None) -> tuple[dict[str, Any], Path]:
    keg = PurePosixPath(entry["expected_keg_directory"])
    relevant = [pin for pin in pins if PurePosixPath(pin["path"]).is_relative_to(keg)]
    # PostgreSQL's frozen paths go through opt; map them to the staged keg too.
    opt = PurePosixPath(entry["opt_symlink"])
    relevant += [pin for pin in pins if PurePosixPath(pin["path"]).is_relative_to(opt)]
    signing_targets = installed_signing_targets(entry, pins, normalization) if normalization is not None else {}
    selected_supplement = {path: row for path, row in (supplement or {}).items() if PurePosixPath(path).is_relative_to(keg)}
    supplement_members = {PurePosixPath(path).relative_to(keg).as_posix() for path in selected_supplement}
    candidates = [entry, *({**entry, **alternative} for alternative in entry.get("alternatives", []))]
    for index, candidate in enumerate(candidates):
        attempt = {"archive_sha256": candidate["archive_sha256"], "target": candidate["target"],
                   "bottle_rebuild": candidate["bottle_rebuild"], "status": "started", "hash_checks": []}
        record["attempts"].append(attempt)
        archive = out / "archives" / f"{entry['formula']}-{index}.tar.gz"
        try:
            attempt["download"] = downloader(candidate, archive)
            # An injected downloader must not bypass the trust boundary.
            need(file_sha256(archive) == candidate["archive_sha256"],
                 "archive_hash_mismatch", "A bottle hash differs before extraction.")
            staged = extract_archive(archive, out / "stage" / f"{entry['formula']}-{index}", entry, closure=closure)
            for relative, expected in entry.get("raw_file_sha256", {}).items():
                path = PurePosixPath(relative)
                need(not path.is_absolute() and ".." not in path.parts and HEX.fullmatch(expected),
                     "manifest_invalid", "A recorded raw bottle file pin is invalid.")
                need(file_sha256(staged.joinpath(*path.parts)) == expected,
                     "raw_bottle_file_mismatch", "An extracted bottle file differs from recorded raw evidence.")
            attempt["raw_hash_checks"] = []
            for pin in relevant:
                path = PurePosixPath(pin["path"])
                relative = path.relative_to(keg if path.is_relative_to(keg) else opt)
                attempt["raw_hash_checks"].append(hash_check(pin, staged.joinpath(*relative.parts)))
            if normalization is not None:
                attempt["normalization"] = {}
                normalize_staged_macho(staged, normalization, attempt["normalization"], command_runner=macho_runner,
                                       installed_signing=signing_targets, security_runner=security_runner,
                                       supplement_members=supplement_members)
            attempt["supplement_files"] = []
            for installed, row in selected_supplement.items():
                relative = PurePosixPath(installed).relative_to(keg)
                path = staged.joinpath(*relative.parts)
                need(path.is_file() and not path.is_symlink(), "supplement_target_refused", "A frozen supplement target is not a regular bottle file.")
                prior = file_sha256(path)
                need((installed, row["sha256"]) in {(pin["path"], pin["sha256"]) for pin in relevant}
                     and hashlib.sha256(row["data"]).hexdigest() == row["sha256"],
                     "supplement_file_hash_mismatch", "A verified public file lost its exact frozen binding.")
                item = {key: row[key] for key in ("path", "installed_path", "sha256", "formula", "archive_sha256", "bytes", "mode")}
                item["pre_supplement_sha256"] = prior
                if prior != row["sha256"]:
                    path.chmod(0o600)
                    path.write_bytes(row["data"])
                    path.chmod(row["mode"])
                    item["status"] = "applied_after_installed_hash_mismatch"
                else:
                    item["status"] = "already_matched"
                item["supplemented_sha256"] = file_sha256(path)
                need(item["supplemented_sha256"] == row["sha256"], "supplement_file_hash_mismatch", "A supplemented file differs from its frozen hash.")
                attempt["supplement_files"].append(item)
            for pin in relevant:
                path = PurePosixPath(pin["path"])
                relative = path.relative_to(keg if path.is_relative_to(keg) else opt)
                attempt["hash_checks"].append(hash_check(pin, staged.joinpath(*relative.parts)))
        except ProvisionError as error:
            attempt.update(status="failed", error=error_record(error))
            raise
        if all(check["status"] == "passed" for check in attempt["hash_checks"]):
            attempt["status"] = "passed"
            record["selected_archive_sha256"] = candidate["archive_sha256"]
            record["selected_target"] = candidate["target"]
            record["selected_bottle_rebuild"] = candidate["bottle_rebuild"]
            return candidate, staged
        attempt["status"] = "frozen_hash_mismatch"
    raise ProvisionError("frozen_bottle_bytes_mismatch",
                         "Staged installed bytes differ from frozen files after the declared normalization policy.")


def remove_exact(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def replace_link(link: Path, target: Path, *, allow_directory: bool = False) -> None:
    need(allow_directory or link.is_symlink() or not link.exists(),
         "link_path_refused", "An opt link path is an existing directory.")
    remove_exact(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)


def install_closure(selections: list[tuple[dict[str, Any], Path]], prefix: Path = PREFIX) -> None:
    if prefix == PREFIX:
        require_github_runner()
    for entry, staged in selections:
        relative = PurePosixPath(entry["expected_keg_directory"]).relative_to("/opt/homebrew")
        destination = prefix.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        remove_exact(destination)
        shutil.copytree(staged, destination, symlinks=True)
        replace_link(prefix / "opt" / entry["formula"], destination)
    node = next((entry for entry, _ in selections if entry["formula"] == "node"), None)
    if node is not None:
        keg = prefix / "Cellar" / "node" / (node["version"] + (f"_{node['revision']}" if node["revision"] else ""))
        source, destination = keg / "libexec/lib/node_modules/npm", prefix / "lib/node_modules/npm"
        need(source.is_dir(), "npm_missing", "The Node bottle has no npm package.")
        remove_exact(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=True)
        (destination / "npmrc").write_bytes(b"prefix = /opt/homebrew\n")
    pg = next((entry for entry, _ in selections if entry["formula"] == "postgresql@17"), None)
    if pg is not None:
        keg = prefix / "Cellar" / "postgresql@17" / (pg["version"] + (f"_{pg['revision']}" if pg["revision"] else ""))
        for relative in ("share/postgresql", "lib/postgresql"):
            target = keg / relative
            need(target.is_dir(), "postgresql_data_missing", "The PostgreSQL bottle lacks required data or libraries.")
            parent = "share" if relative.startswith("share/") else "lib"
            replace_link(prefix / parent / "postgresql@17", target, allow_directory=True)
    python = next((entry for entry, _ in selections if entry["formula"] == "python@3.12"), None)
    if python is not None:
        package = python["version"] + (f"_{python['revision']}" if python["revision"] else "")
        framework = prefix / "Cellar/python@3.12" / package / "Frameworks/Python.framework/Versions/3.12"
        need(framework.is_dir(), "python_framework_missing", "The Python bottle has no expected framework.")
        replace_link(prefix / "Frameworks/Python.framework/Versions/3.12", framework, allow_directory=True)


def verify_python(spec: dict[str, Any], contracts: dict[str, dict[str, Any]],
                  prefix: Path = PREFIX, *, command_runner=None) -> dict[str, Any]:
    """Run only on GitHub; fake runners and a temporary prefix support unit fixtures."""
    if prefix == PREFIX:
        require_github_runner()
    versions = {document["python"]["version"] for document in contracts.values()
                if isinstance(document.get("python"), dict)}
    need(spec.get("binary") == PYTHON_BINARY and spec.get("version") == "3.12.11"
         and versions == {"3.12.11"}, "contract_invalid", "The Python version pin is invalid.")
    binary = prefix / "opt/python@3.12/bin/python3.12"
    need(binary.is_file(), "python_binary_missing", "The installed Python executable is missing.")
    command_runner = subprocess.run if command_runner is None else command_runner
    result = {"status": "failed", "binary": PYTHON_BINARY, "expected_version": "Python 3.12.11",
              "binary_sha256": None, "venv_creation": "not_run", "checks": []}
    commands = (("version", [str(binary), "--version"], "Python 3.12.11"),
                ("stdlib_and_bundled_ensurepip", [str(binary), "-I", "-c", PYTHON_RUNTIME_PROBE], "python-runtime-ok"))
    for label, command, expected in commands:
        try:
            done = command_runner(command, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            result["checks"].append({"label": label, "status": "failed", "reason": "Python check could not run."})
            return result
        output = (done.stdout or done.stderr or "").strip()
        passed = done.returncode == 0 and output == expected
        check = {"label": label, "status": "passed" if passed else "failed", "exit_code": done.returncode}
        if label == "version" and re.fullmatch(r"Python [0-9]+\.[0-9]+\.[0-9]+", output):
            check["actual_version"] = output
        result["checks"].append(check)
        if not passed:
            return result
    result["status"] = "passed"
    return result


def verify_installed(contracts: dict[str, dict[str, Any]], pins: list[dict[str, str]],
                     prefix: Path = PREFIX) -> list[dict[str, Any]]:
    checks = [hash_check(pin, prefix.joinpath(*PurePosixPath(pin["path"]).relative_to("/opt/homebrew").parts))
              for pin in pins]
    for document in contracts.values():
        npm = document.get("npm")
        if isinstance(npm, dict):
            root = prefix.joinpath(*PurePosixPath(npm["root"]).relative_to("/opt/homebrew").parts)
            actual = tree_sha256(root) if root.is_dir() else None
            expected = (npm["tree_sha256"], npm["tree_files"], npm["tree_symlinks"])
            checks.append({"label": "npm/tree", "path": npm["root"], "expected": list(expected),
                           "actual": list(actual) if actual is not None else None,
                           "status": "passed" if actual == expected else "failed"})
    return checks


def cleanup_provision_staging(out: Path) -> dict[str, Any]:
    """Remove only this successful run's duplicate archives and extracted trees."""
    roots = [out / "archives", out / "stage"]
    need(all(not root.is_symlink() and (not root.exists() or root.is_dir()) for root in roots),
         "cleanup_refused", "A provisioning temporary directory is not a normal directory.")
    file_bytes = sum(path.stat().st_size for root in roots if root.exists()
                     for path in root.rglob("*") if not path.is_symlink() and path.is_file())
    available_before = shutil.disk_usage(out).free
    removed = []
    for root in roots:
        if root.exists():
            shutil.rmtree(root)
            removed.append(root.name)
    available_after = shutil.disk_usage(out).free
    return {"status": "passed", "removed_directories": removed, "file_bytes_removed": file_bytes,
            "available_bytes_before": available_before, "available_bytes_after": available_after,
            "observed_available_bytes_delta": available_after - available_before,
            "measurement": "File bytes are logical sizes. The free-space change can include other runner activity."}


def provision(document: dict[str, Any], contracts: dict[str, dict[str, Any]], out: Path,
              report: dict[str, Any], *, prefix: Path = PREFIX, downloader=download_archive,
              expected_count: int | None = FULL_BOTTLE_COUNT, python_runner=None, macho_runner=None,
              security_runner=None, supplement_reader=None) -> None:
    if prefix == PREFIX:
        require_github_runner()
    entries = validate_manifest(document, expected_count=expected_count)
    pins = pin_specs(contracts)
    supplement = {}
    if "frozen_file_supplement" in document:
        report["stage"] = "frozen_file_supplement"
        report["frozen_file_supplement"] = {}
        supplement = load_frozen_file_supplement(document["frozen_file_supplement"], entries, pins,
                                                document.get("macho_normalization", {}), report["frozen_file_supplement"],
                                                blob_reader=supplement_reader)
    selections = []
    failures = []
    report["stage"] = "download_and_stage"
    for entry in entries:
        record = {key: entry[key] for key in ("formula", "version", "revision", "expected_keg_directory")}
        record["attempts"] = []
        report["packages"].append(record)
        try:
            selections.append(stage_package(entry, out, pins, record, downloader=downloader, closure=entries,
                                            normalization=document.get("macho_normalization"), macho_runner=macho_runner,
                                            security_runner=security_runner, supplement=supplement))
            record["status"] = "passed"
        except ProvisionError as error:
            record.update(status="failed", error=error_record(error))
            failures.append({"formula": entry["formula"], "error": error_record(error)})
    if failures:
        raise ProvisionError("package_staging_failed", "One or more bottles failed. No package was installed.",
                             {"failed_packages": failures})
    report["stage"] = "install"
    install_closure(selections, prefix)
    report["stage"] = "frozen_hashes"
    report["hash_checks"] = verify_installed(contracts, pins, prefix)
    need(report["hash_checks"] and all(check["status"] == "passed" for check in report["hash_checks"]),
         "installed_frozen_hash_mismatch", "Installed files differ from the frozen toolchain.")
    if isinstance(document.get("python"), dict):
        report["stage"] = "python_runtime"
        report["python"] = verify_python(document["python"], contracts, prefix, command_runner=python_runner)
        need(report["python"]["status"] == "passed", "python_runtime_mismatch",
             "The installed Python version or bundled standard library check failed.")
    report["stage"] = "cleanup"
    report["cleanup"] = cleanup_provision_staging(out)
    report.update(status="passed", stage="complete")


def new_report() -> dict[str, Any]:
    return {"version": "som.react-output-macos-provision.v1", "status": "failed",
            "product_suites": "not_run", "stage": "guard", "packages": [], "hash_checks": [],
            "python": {"status": "not_run", "venv_creation": "not_run"},
            "cleanup": {"status": "not_run", "reason": "Temporary evidence stays until all provisioning checks pass."},
            "relocation": "Only the reviewed Homebrew placeholder load-command normalization and ad-hoc signing are allowed. Raw and normalized hashes remain separate. Frozen installed file hashes decide success."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="A new provisioning log directory")
    parser.add_argument("--manifest", type=Path, default=HERE / "macos_bottles.json")
    args = parser.parse_args(argv)
    report = new_report()
    output = None
    code = 1
    try:
        require_github_runner()
        need(not args.out.exists() and not args.out.is_symlink(),
             "output_exists", "The provisioning output directory already exists.")
        args.out.mkdir(parents=True)
        output = args.out / "report.json"
        document = json.loads(args.manifest.read_bytes())
        need(isinstance(document, dict), "manifest_invalid", "The bottle manifest is invalid.")
        contracts = load_contracts(REPO)
        report["manifest_sha256"] = file_sha256(args.manifest)
        report["contract_sha256"] = {relative: file_sha256(REPO / relative) for relative in CONTRACTS}
        provision(document, contracts, args.out, report)
        code = 0
    except ProvisionError as error:
        report["error"] = error_record(error)
    except Exception as error:
        report["error"] = {"code": "provision_failed", "message": "Provisioning failed. No product suite ran.",
                           "exception_type": type(error).__name__}
    finally:
        if output is not None:
            try:
                output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            except OSError:
                code = 1
                report.update(status="failed", error={"code": "report_write_failed",
                                                      "message": "The provisioning report could not be written."})
    print(json.dumps({"status": report["status"], "stage": report["stage"],
                      "code": report.get("error", {}).get("code"),
                      "packages": len(report["packages"]), "product_suites": report["product_suites"]}))
    return code


if __name__ == "__main__":
    sys.exit(main())
