"""Native-only copy of the original complete installed-runtime data verifiers.

The original nine function ASTs and constants stay exact. The qualifier comes
from the active source owner. No legacy bootstrap body or bytecode is executed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

BASE = "som-rs/scripts/hosted_readiness/product_runtime/"
SCRIPT, SUPPORT = BASE + "reuse_runtime.py", BASE + "qualify_runtime.py"
SUPPORT_SHA256 = "f3c6f69e922ab0a8f27426a5f597c03f213d40be0fb7fc324ac33f1b48ae66f3"


def _native_support():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    if controls.role_sources.get('runtime.qualifier') != SUPPORT:
        raise RuntimeError("fixed native runtime qualifier source required")
    path = controls.root / SUPPORT
    path = Path(path).absolute()
    suffix = Path(SUPPORT).parts
    if ".." in path.parts or path.parts[-len(suffix):] != suffix:
        raise RuntimeError("fixed bootstrap support source path required")
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or (item != path and not stat.S_ISDIR(info.st_mode)):
            raise RuntimeError("bootstrap support source or parent is linked or nonregular")
        if item == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8 * 1024 * 1024):
            raise RuntimeError("bootstrap support source is linked, nonregular, or oversized")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("bootstrap support source changed type")
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != SUPPORT_SHA256:
        raise RuntimeError("bootstrap support SHA differs")
    return controls.load_module('runtime.qualifier')


q = _native_support()
Refusal, require = q.Refusal, q.require
QUALIFICATION, AUDIT = BASE + "runtime-bf3-qualification.json", BASE + "runtime-bf3-byte-audit.json"
PINS = BASE + "source-pins.json"
META_FILES = {"receipt.json", "installed-files.json", "source-pins.json", "build-intermediates.json"}
MAX_META, MAX_FILE, MAX_TOTAL = 8 * 1024 * 1024, 256 * 1024 * 1024, 2 * 1024 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def bad(_):
        raise Refusal("nonfinite JSON value")
    try:
        return json.loads(raw, object_pairs_hook=q.unique_keys, parse_constant=bad)
    except Refusal:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise Refusal("invalid JSON metadata") from error


def safe_read(path):
    q.no_links(path)
    require(path.is_file() and path.stat().st_size <= MAX_META, "missing or oversized metadata")
    return path.read_bytes()


def qualification_data(raw_qualification, raw_audit, raw_source_pins):
    value, audit = parse(raw_qualification), parse(raw_audit)
    require(isinstance(value, dict) and isinstance(audit, dict), "qualification and audit objects required")
    require(value.get("version") == "som.product-runtime-artifact-qualification.v1"
            and value.get("qualified_for") == "later_GitHub_tests_only"
            and value.get("product_runtime_ready") is False and value.get("product_acceptance_passed") is False, "qualification scope differs")
    require(value.get("repository") == "faberline/som" and type(value.get("run_id")) is int and type(value.get("artifact_id")) is int
            and value["run_id"] > 0 and value["artifact_id"] > 0
            and value.get("artifact_name") == "product-runtime-first-build-qualification"
            and re.fullmatch(r"[0-9a-f]{40}", value.get("source_revision", "")), "artifact identity differs")
    require(isinstance(value.get("audit"), dict) and value["audit"].get("path") == AUDIT and digest(raw_audit) == value["audit"].get("sha256"), "controller audit bytes differ")
    require(audit.get("version") == "som.product-runtime-first-build-saved-byte-audit.v1"
            and audit.get("qualification_evidence_verified") is True and audit.get("product_runtime_ready") is False
            and audit.get("product_acceptance_passed") is False and audit.get("tested_sha") == value["source_revision"]
            and audit.get("artifact_id") == value["artifact_id"] and audit.get("artifact_github_digest") == value.get("artifact_sha256")
            and audit.get("run_url") == f"https://github.com/{value['repository']}/actions/runs/{value['run_id']}", "controller audit does not qualify this artifact")
    files = value.get("transport_files")
    require(isinstance(files, dict) and set(files) == META_FILES and all(isinstance(item, str) and q.SHA.fullmatch(item) for item in files.values()), "fixed transport metadata hashes required")
    require(files["receipt.json"] == audit.get("receipt_sha256")
            and files["installed-files.json"] == audit.get("installed_inventory_sha256")
            and files["source-pins.json"] == digest(raw_source_pins), "audited metadata bindings differ")
    pins = q.manifest_data(raw_source_pins)
    counts = value.get("counts", {})
    source_counts = {"regular_installed_files": "regular_installed_files", "aliases": "transport_dereferenced_aliases",
                     "admitted_executables": "admitted_elf_version_observations", "commands": "commands_zero_exit_and_log_hashes_verified",
                     "license_files": "license_files"}
    require(isinstance(counts, dict) and set(counts) == set(source_counts) and all(type(counts[key]) is int and counts[key] > 0
            and type(audit.get(source)) is int and counts[key] == audit.get(source) for key, source in source_counts.items()), "audited inventory counts differ")
    require(len(pins["archives"]) == audit.get("source_archive_records") == 6, "source archive inventory differs")
    aliases, admitted = value.get("aliases"), value.get("admitted_paths")
    require(isinstance(aliases, dict) and len(aliases) == counts["aliases"] and isinstance(admitted, list)
            and len(admitted) == len(set(admitted)) == counts["admitted_executables"] and not set(admitted) & set(aliases), "alias or admitted path inventory differs")
    for path in [*aliases, *admitted]:
        require(q.clean_path(path).startswith("install/"), "path outside runtime install tree")
    for target in aliases.values():
        require(isinstance(target, str) and target not in {".", ".."} and q.clean_path(target) == target and "/" not in target, "only recorded internal basename aliases allowed")
    prefix = value.get("install_prefix")
    require(isinstance(prefix, str) and Path(prefix).is_absolute() and ".." not in Path(prefix).parts
            and Path(prefix).name == "som-product-runtime-qualification"
            and not any(ord(char) < 32 or ord(char) == 127 for char in prefix), "fixed runtime prefix required")
    require(isinstance(value.get("artifact_sha256"), str) and q.SHA.fullmatch(value["artifact_sha256"]), "artifact digest pin missing")
    return value


def validate_inventory(inventory, qualification):
    require(isinstance(inventory, dict), "installed inventory must be an object")
    regular, aliases = 0, {}
    for relative, row in inventory.items():
        require(q.clean_path(relative).startswith("install/") and isinstance(row, dict), "unsafe installed inventory path")
        require(isinstance(row.get("sha256"), str) and q.SHA.fullmatch(row["sha256"]), "installed hash missing")
        if row.get("kind") == "regular_file":
            require(set(row) == {"kind", "sha256", "executable_bit"} and type(row["executable_bit"]) is bool, "unknown installed file mode")
            regular += 1
        else:
            require(set(row) == {"kind", "target", "sha256", "qualification_probe_executed", "runtime_admitted"}
                    and row.get("kind") == "alias_not_admitted" and isinstance(row.get("target"), str) and row.get("qualification_probe_executed") is False
                    and row.get("runtime_admitted") is False and qualification["aliases"].get(relative) == row.get("target")
                    and digest(row["target"].encode()) == row["sha256"], "unknown or admitted alias refused")
            target = str(PurePosixPath(relative).parent / row["target"])
            require(isinstance(inventory.get(target), dict) and inventory[target].get("kind") == "regular_file", "alias target is not a recorded regular file")
            aliases[relative] = row["target"]
    require(regular == qualification["counts"]["regular_installed_files"] and aliases == qualification["aliases"], "complete installed inventory count differs")
    require(all(path in inventory and inventory[path]["kind"] == "regular_file" and inventory[path]["executable_bit"] is True
                for path in qualification["admitted_paths"]), "admitted executable is an alias or has no executable mode")
    return inventory


def read_inventory(path, qualification):
    raw = safe_read(path)
    require(digest(raw) == qualification["transport_files"]["installed-files.json"], "installed inventory bytes differ")
    return validate_inventory(parse(raw), qualification)


def collect_files(root, allow_file_links=False):
    q.no_links(root)
    require(root.is_dir(), "artifact or installed tree missing")
    files, directories, total = {}, set(), 0
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in dirs:
            path = Path(base) / name
            q.no_links(path)
            directories.add(q.clean_path(path.relative_to(root).as_posix()))
        for name in names:
            path = Path(base) / name
            q.no_links(path.parent)
            relative = q.clean_path(path.relative_to(root).as_posix())
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                require(allow_file_links, "transport symlink refused")
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_FILE, "nonregular, linked, or oversized runtime file")
                total += info.st_size
                require(total <= MAX_TOTAL, "runtime tree exceeds size limit")
            files[relative] = path
    return files, directories


def expected_directories(paths):
    return {parent.as_posix() for name in paths for parent in PurePosixPath(name).parents if parent.as_posix() != "."}


def verify_installed_tree(root, inventory, qualification):
    validate_inventory(inventory, qualification)
    files, directories = collect_files(root / "install", allow_file_links=True)
    files = {"install/" + relative: path for relative, path in files.items()}
    expected = set(inventory)
    require(set(files) == expected and {"install/" + name for name in directories} | {"install"} == expected_directories(expected), "installed file set or directories differ")
    regular, aliases = 0, 0
    for relative, row in inventory.items():
        path = files[relative]
        if row["kind"] == "regular_file":
            q.no_links(path)
            require(q.sha(path) == row["sha256"] and stat.S_IMODE(path.stat().st_mode) == (0o755 if row["executable_bit"] else 0o644), "installed hash or restored mode differs")
            regular += 1
        else:
            require(path.is_symlink() and os.readlink(path) == row["target"], "installed alias differs")
            target = path.resolve(strict=True)
            require(root / "install" in target.parents and not target.is_symlink(), "installed alias escapes runtime")
            aliases += 1
    return {"regular_files": regular, "aliases": aliases,
            "installed_inventory_sha256": qualification["transport_files"]["installed-files.json"]}
