"""Native Rust tools use the original complete data verification closure.

The active source owner injects the real support module and fixed exports.
This module has no raw bootstrap, build, download, install or CLI entry.
Source checks do not grant product acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from types import ModuleType

BASE = "som-rs/scripts/hosted_readiness/product_runtime/"
SCRIPT = BASE + "qualify_rust_tools.py"
SUPPORT = BASE + "qualify_runtime.py"
PROFILE = "som-rs/contracts/native-tools-v1/product-rust-tools-v1.json"
SUPPORT_SHA = "f3c6f69e922ab0a8f27426a5f597c03f213d40be0fb7fc324ac33f1b48ae66f3"
HOST = "x86_64-unknown-linux-gnu"
NAMES = {"rustc", "cargo", "rustfmt", "cargo-fmt", "cargo-clippy", "clippy-driver"}
COMPONENTS = {"rustc", "cargo", "rust-std", "rustfmt-preview", "clippy-preview"}
SOURCE_LIMIT = 1024 * 1024

NATIVE_SCRIPT = BASE + "native_rust_tool_verifier.py"


def _verify_native_bindings():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    if Path(__file__).absolute() != controls.root / NATIVE_SCRIPT:
        raise RuntimeError("fixed native Rust verifier source required")
    if type(q) is not ModuleType or Path(q.__file__).absolute() != controls.root / SUPPORT:
        raise RuntimeError("fixed native Rust support source required")
    if require is not q.require or Refusal is not q.Refusal:
        raise RuntimeError("fixed native Rust support aliases differ")
    if not (_q_no_links is q.no_links and _q_unique_keys is q.unique_keys
            and _q_MAX_MEMBER is q.MAX_MEMBER and _q_SHA is q.SHA
            and _q_clean_path is q.clean_path and _q_MAX_MEMBERS is q.MAX_MEMBERS
            and _q_MAX_ARCHIVE is q.MAX_ARCHIVE and _q_sha is q.sha
            and _q_regular is q.regular):
        raise RuntimeError("fixed native Rust support exports differ")
    path = controls.root / SUPPORT
    q.no_links(path)
    info = path.lstat()
    if not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= SOURCE_LIMIT):
        raise RuntimeError("regular bounded native Rust support source required")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RuntimeError("native Rust support source changed type")
        raw = stream.read(SOURCE_LIMIT + 1)
    if len(raw) != info.st_size or hashlib.sha256(raw).hexdigest() != SUPPORT_SHA:
        raise RuntimeError("native Rust support source SHA differs")


_verify_native_bindings()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def raw_json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def bounded_read(path, limit=SOURCE_LIMIT):
    q.no_links(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
            "regular single-link bounded file required")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1, "opened input changed type")
        raw = stream.read(limit + 1)
    require(len(raw) <= limit and len(raw) == info.st_size, "input size changed or exceeds limit")
    return raw


def parse(raw):
    def reject_constant(value):
        raise Refusal("nonfinite JSON constant refused")
    return json.loads(raw, object_pairs_hook=q.unique_keys, parse_constant=reject_constant)


def record_pin(value):
    require(type(value) is dict and set(value) == {"bytes", "mode", "sha256"}, "exact file pin required")
    require(type(value["bytes"]) is int and 0 <= value["bytes"] <= q.MAX_MEMBER,
            "file byte bound differs")
    require(value["mode"] in {0o644, 0o755} and type(value["mode"]) is int, "unsupported file mode")
    require(isinstance(value["sha256"], str) and q.SHA.fullmatch(value["sha256"]), "file SHA missing")


def expected_install(profile):
    result = {}
    for component, row in profile["components"].items():
        prefix = row["member_root"] + "/" + row["component_dir"] + "/"
        lines = row["install_manifest_text"].splitlines()
        require(lines and all(line.startswith("file:") for line in lines), "only exact file install entries admitted")
        relative = [q.clean_path(line[5:]) for line in lines]
        require(len(set(relative)) == len(relative), "duplicate component install entry")
        actual = {name[len(prefix):] for name in row["all_regular_files"]
                  if name.startswith(prefix) and name != prefix + "manifest.in"}
        require(set(relative) == actual, "component manifest does not cover complete install files")
        manifest = row["all_regular_files"][prefix + "manifest.in"]
        require(digest(row["install_manifest_text"].encode()) == manifest["sha256"]
                and len(row["install_manifest_text"].encode()) == manifest["bytes"], "manifest.in bytes differ")
        for name in relative:
            value = row["all_regular_files"][prefix + name]
            require(name not in result or {k: result[name][k] for k in ("bytes", "mode", "sha256")} == value,
                    "conflicting component install overlap")
            if name not in result:
                result[name] = {**value, "components": []}
            result[name]["components"].append(component)
    for value in result.values():
        value["components"].sort()
    require(len(result) <= q.MAX_MEMBERS, "installed file count exceeds bound")
    return dict(sorted(result.items()))


def profile_data(raw):
    require(type(raw) is bytes and len(raw) <= SOURCE_LIMIT, "bound profile bytes required")
    value = parse(raw)
    require(value.get("version") == "som.native-rust-product-tools-profile.v1"
            and value.get("host") == HOST and value.get("rust_release") == "1.96.1"
            and value.get("os") == {"id": "ubuntu", "version_id": "22.04"}, "fixed native product profile required")
    require(set(value.get("components", {})) == COMPONENTS and set(value.get("executables", {})) == NAMES,
            "all five components and six tools required")
    for component, row in value["components"].items():
        root = q.clean_path(row["member_root"])
        require("/" not in root and row["component_dir"] == ("rust-std-" + HOST if component == "rust-std" else component),
                "component root differs")
        require(row.get("all_aliases") == {}, "this profile admits no archive aliases")
        require(row["url"] == "https://static.rust-lang.org/dist/2026-06-30/" + root + ".tar.xz", "publisher URL differs")
        require(q.SHA.fullmatch(row["publisher_sha256"]) and type(row["archive_bytes"]) is int
                and 0 < row["archive_bytes"] <= q.MAX_ARCHIVE, "archive pin differs")
        files, directories = row["all_regular_files"], row["directories"]
        require(type(files) is dict and files and len(files) <= q.MAX_MEMBERS
                and type(directories) is dict and root in directories, "complete archive scope required")
        for name, pin in files.items():
            q.clean_path(name)
            require(name.startswith(root + "/"), "file outside component archive")
            record_pin(pin)
        for name, mode in directories.items():
            q.clean_path(name)
            require((name == root or name.startswith(root + "/")) and mode == 0o755,
                    "directory scope or mode differs")
        require(set(row["licenses"]) <= set(files) and row["licenses"], "license scope missing")
        for name, pin in row["required_binary_members"].items():
            require(name in files and files[name] == pin and pin["mode"] == 0o755, "tool member pin differs")
    installed = expected_install(value)
    for name, pin in value["executables"].items():
        require(pin["path"] == "bin/" + name and pin["component"] in COMPONENTS,
                "fixed tool path required")
        row = value["components"][pin["component"]]
        require(pin["archive_member"] in row["required_binary_members"]
                and pin["archive_member"] in row["all_regular_files"]
                and row["required_binary_members"][pin["archive_member"]] == row["all_regular_files"][pin["archive_member"]],
                "tool archive member differs")
        require(installed[pin["path"]]["sha256"] == pin["sha256"]
                and installed[pin["path"]]["bytes"] == pin["bytes"]
                and isinstance(pin["version_prefix"], str) and pin["version_prefix"], "tool install or version pin differs")
    require(value["executables"]["rustfmt"]["sha256"] == "dbb30c6037c51a2bacb94d46e2d6435d9487531c06cf82330fdee0bda507db0d",
            "compiler native rustfmt and product rustfmt must agree")
    return value


def verify_file_tree(root, expected):
    actual, directories = {}, set()
    q.no_links(root)
    require(root.is_dir(), "owned file tree missing")
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in dirs:
            path = Path(base) / name
            q.no_links(path)
            require(path.is_dir(), "nonregular installed directory")
            directories.add(path.relative_to(root).as_posix())
        for name in names:
            path = Path(base) / name
            q.no_links(path)
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= q.MAX_MEMBER,
                    "nonregular, linked or oversized installed file")
            actual[relative] = {"sha256": q.sha(path), "bytes": info.st_size, "mode": stat.S_IMODE(info.st_mode)}
    require(set(actual) == set(expected), "installed file set differs")
    expected_dirs = {p.as_posix() for name in expected for p in PurePosixPath(name).parents if p.as_posix() != "."}
    require(directories == expected_dirs, "installed directory set differs")
    for name, record in actual.items():
        require(record == {key: expected[name][key] for key in ("sha256", "bytes", "mode")}, "installed file bytes or mode differs")
    return actual


def verify_install_tree(out, profile):
    expected = expected_install(profile)
    verify_file_tree(out / "install", expected)
    return {"version": "som.rust-product-tool-installed-files.v1", "files": expected}


def verify_preserved_inputs(out, profile):
    scopes = {"publisher-records": {}, "licenses": {}, "downloads": {}, "publisher-metadata": {}}
    for name, row in profile["components"].items():
        prefix = row["member_root"] + "/" + row["component_dir"] + "/"
        scopes["downloads"][name + ".tar.xz"] = {"sha256": row["publisher_sha256"], "bytes": row["archive_bytes"], "mode": 0o644}
        for path, pin in row["all_regular_files"].items():
            preserved = {**pin, "mode": 0o644}
            if not path.startswith(prefix) or path == prefix + "manifest.in":
                scopes["publisher-records"][name + "/" + path] = preserved
            if path in row["licenses"]:
                scopes["licenses"][name + "/" + path] = preserved
    publisher = profile["publisher_manifest"]
    for path, checksum in (("channel.toml", False), ("channel.toml.sha256", True)):
        scopes["publisher-metadata"][path] = {"sha256": publisher["checksum_sha256" if checksum else "sha256"],
                                             "bytes": publisher["checksum_bytes" if checksum else "bytes"], "mode": 0o644}
    return {name: verify_file_tree(out / name, expected) for name, expected in scopes.items()}


def version_matches(text, pin):
    require(type(text) is str and text and all(c in "\r\n\t" or 32 <= ord(c) < 127 for c in text), "invalid version text")
    lines = text.splitlines()
    prefix = pin["version_prefix"]
    return bool(lines) and (lines[0] == prefix or lines[0].startswith(prefix + " "))


def verify_tools(out, profile):
    require(set(profile["executables"]) == NAMES, "six fixed tool pins required")
    result = {}
    expected = expected_install(profile)
    for name in sorted(NAMES):
        require(profile["executables"][name]["path"] == "bin/" + name, "fixed tool path required")
        path, pin = out / "install/bin" / name, expected["bin/" + name]
        q.no_links(path)
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == pin["bytes"]
                and stat.S_IMODE(info.st_mode) == pin["mode"] == 0o755
                and q.sha(path) == pin["sha256"], "native tool changed before use")
        result[name] = path
    return result


def verify_qualification(out, profile, source_revision):
    return verify_record(out, profile, source_revision, parse(bounded_read(out / "receipt.json", 16 * SOURCE_LIMIT)))


def verify_record(out, profile, source_revision, receipt):
    require(receipt.get("version") == "som.rust-product-tool-qualification.v1"
            and receipt.get("status") == "qualification_commands_complete_awaiting_independent_review"
            and receipt.get("source_revision") == source_revision and receipt.get("product_acceptance_passed") is False,
            "qualification receipt incomplete or wrong source")
    require(receipt.get("profile_sha256") == digest(bounded_read(out / "profile.json")), "profile receipt SHA differs")
    require(profile == profile_data(bounded_read(out / "profile.json")), "qualified profile differs from bound profile")
    require(set(receipt.get("source_bindings", {})) == {SCRIPT, SUPPORT, PROFILE}
            and receipt["source_bindings"] == receipt.get("source_bindings_after")
            and receipt["source_bindings"][SUPPORT]["sha256"] == SUPPORT_SHA
            and receipt["source_bindings"][PROFILE]["sha256"] == receipt["profile_sha256"]
            and receipt["source_bindings"][SCRIPT]["sha256"] == digest(bounded_read(Path(__file__).absolute().with_name("qualify_rust_tools.py"))),
            "source binding receipt differs")
    inventory = verify_install_tree(out, profile)
    require(raw_json(inventory) == bounded_read(out / "installed-files.json")
            and digest(raw_json(inventory)) == receipt["installed_inventory_sha256"], "installed inventory receipt differs")
    require(set(receipt.get("tools", {})) == NAMES, "six tool probe receipts required")
    require(verify_preserved_inputs(out, profile) == receipt.get("preserved_input_inventory"), "publisher or license evidence changed")
    require(receipt.get("commands"), "owned command receipts missing")
    for command in receipt["commands"]:
        require(command["exit_code"] == 0 and command.get("wait_complete") is True
                and command.get("cleanup", {}).get("group_gone") is True, "command wait or cleanup is incomplete")
        for stream in ("stdout", "stderr"):
            leaf = Path(command[stream])
            require(leaf.parent == out / "logs" and digest(bounded_read(leaf)) == command[stream + "_sha256"],
                    "owned log path or hash differs")
    result = {}
    for name in sorted(NAMES):
        pin, row = profile["executables"][name], receipt["tools"][name]
        path = out / "install" / pin["path"]
        require(row["relative_path"] == "install/" + pin["path"] and row["sha256"] == pin["sha256"]
                and q.regular(path) == pin["sha256"], "qualified tool identity differs")
        index = row["command_index"]
        require(type(index) is int and 0 <= index < len(receipt["commands"]), "version command index invalid")
        command = receipt["commands"][index]
        require(command["argv"] == [str(path), "--version"] and command["exit_code"] == 0
                and command.get("wait_complete") is True and command.get("cleanup", {}).get("group_gone") is True,
                "version command or wait proof differs")
        for stream in ("stdout", "stderr"):
            leaf = Path(command[stream])
            require(leaf.parent == out / "logs" and digest(bounded_read(leaf)) == command[stream + "_sha256"],
                    "version log path or hash differs")
        version_text = bounded_read(Path(command["stdout"])).decode()
        require(row["version_text"] == version_text and version_matches(version_text, pin), "qualified tool version differs")
        result[name] = path
    require(verify_tools(out, profile) == result, "tool launch identities differ")
    return result

