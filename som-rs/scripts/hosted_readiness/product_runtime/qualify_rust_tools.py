#!/usr/bin/env python3
"""Qualify complete official Rust product tools on GitHub Ubuntu22. No product gate passes here."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import time
from types import ModuleType
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = "som-rs/scripts/hosted_readiness/product_runtime/"
SCRIPT = BASE + "qualify_rust_tools.py"
SUPPORT = BASE + "qualify_runtime.py"
PROFILE = "som-rs/contracts/native-tools-v1/product-rust-tools-v1.json"
SUPPORT_SHA = "f3c6f69e922ab0a8f27426a5f597c03f213d40be0fb7fc324ac33f1b48ae66f3"
HOST = "x86_64-unknown-linux-gnu"
NAMES = {"rustc", "cargo", "rustfmt", "cargo-fmt", "cargo-clippy", "clippy-driver"}
COMPONENTS = {"rustc", "cargo", "rust-std", "rustfmt-preview", "clippy-preview"}
SOURCE_LIMIT = 1024 * 1024


def bootstrap(path):
    path = Path(path).absolute()
    if path.parts[-len(Path(SUPPORT).parts):] != Path(SUPPORT).parts or ".." in path.parts:
        raise RuntimeError("fixed support source path required")
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or (item != path and not stat.S_ISDIR(info.st_mode)):
            raise RuntimeError("linked or nonregular support path")
        if item == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > SOURCE_LIMIT):
            raise RuntimeError("linked or oversized support source")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("opened support source is nonregular or linked")
        raw = stream.read(SOURCE_LIMIT + 1)
    if len(raw) > SOURCE_LIMIT or hashlib.sha256(raw).hexdigest() != SUPPORT_SHA:
        raise RuntimeError("support source SHA differs")
    name = "_som_rust_product_runtime_support"
    if name in sys.modules:
        raise RuntimeError("support namespace already loaded")
    module = ModuleType(name)
    module.__file__, module.__cached__, module.__package__ = str(path), None, ""
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


q = bootstrap(Path(__file__).with_name("qualify_runtime.py"))
require, Refusal = q.require, q.Refusal


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


class Runner(q.Runner):
    def run(self, argv, label, cwd=None, timeout=60):
        cwd = cwd or self.out
        require(cwd == self.out or self.out in cwd.parents, "child cwd outside owned output")
        q.no_links(cwd)
        index = len(self.report["commands"])
        stdout, stderr = [self.out / "logs" / f"{index:03d}-{label}.{name}" for name in ("stdout", "stderr")]
        row = {"argv": list(map(str, argv)), "cwd": str(cwd), "environment": dict(self.environment),
               "stdout": str(stdout), "stderr": str(stderr), "pid": None, "exit_code": None,
               "wait_complete": False, "cleanup": {"group_gone": False}}
        self.report["commands"].append(row)
        process, started = None, time.monotonic()
        try:
            with stdout.open("xb") as sink, stderr.open("xb") as errors:
                process = subprocess.Popen(row["argv"], cwd=cwd, env=self.environment, stdin=subprocess.DEVNULL,
                                           stdout=sink, stderr=errors, start_new_session=True)
                q.LIVE = process
                row["pid"] = process.pid
                process.wait(timeout=timeout)
                row["wait_complete"] = True
        except BaseException as error:
            row["error"] = type(error).__name__
            raise
        finally:
            if process is not None:
                row["cleanup"] = q.stop_owned(process)
                row["exit_code"] = process.poll()
                row["wait_complete"] = row["exit_code"] is not None
            q.LIVE = None
            row["elapsed_seconds"] = time.monotonic() - started
            for name, path in (("stdout", stdout), ("stderr", stderr)):
                if path.is_file():
                    require(path.stat().st_size <= SOURCE_LIMIT, "command log exceeds bound")
                    row[name + "_sha256"] = q.sha(path)
        require(row["exit_code"] == 0 and row["wait_complete"] and row["cleanup"]["group_gone"],
                "owned command failed or cleanup is unconfirmed")
        return bounded_read(stdout).decode("utf-8", "strict")


def source_bindings(runner, repo, revision):
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "declared commit required")
    require(Path(__file__).absolute() == repo / SCRIPT and ".." not in repo.parts, "fixed qualifier path required")
    git = Path("/usr/bin/git")
    q.regular(git)
    argv = [git, "--no-replace-objects", "-C", repo]
    require(runner.run_bytes(argv + ["rev-parse", "HEAD^{commit}"], "source-head", 128).strip() == revision.encode(),
            "checkout differs from declared source")
    require(runner.run_bytes(argv + ["rev-parse", "--show-toplevel"], "source-root", 4096).strip() == os.fsencode(repo),
            "repository root differs")
    blobs, bindings = {}, {}
    for i, name in enumerate((SCRIPT, SUPPORT, PROFILE)):
        tree = runner.run_bytes(argv + ["ls-tree", "-z", revision, "--", name], f"source-tree-{i}", 4096)
        fields, separator, path = tree.rstrip(b"\0").partition(b"\t")
        mode, kind, oid = fields.split()
        require(tree.count(b"\0") == 1 and tree.endswith(b"\0") and separator and path == name.encode()
                and mode in {b"100644", b"100755"} and kind == b"blob" and re.fullmatch(rb"[0-9a-f]{40}", oid),
                "fixed source is not a regular Git blob")
        size = runner.run_bytes(argv + ["cat-file", "-s", oid.decode()], f"source-size-{i}", 128).strip()
        require(size.isdigit() and int(size) <= SOURCE_LIMIT, "source blob exceeds bound")
        raw = runner.run_bytes(argv + ["cat-file", "blob", oid.decode()], f"source-blob-{i}", SOURCE_LIMIT)
        require(len(raw) == int(size) and hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == oid.decode(),
                "source blob identity differs")
        require(bounded_read(repo / name) == raw, "working source differs from committed source")
        blobs[name], bindings[name] = raw, {"git_blob_oid": oid.decode(), "sha256": digest(raw), "bytes": len(raw)}
    require(digest(blobs[SUPPORT]) == SUPPORT_SHA, "Git-bound support differs")
    return blobs, bindings


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Refusal("publisher redirect refused")


def download(url, path, expected, size):
    require(url.startswith("https://static.rust-lang.org/dist/") and "?" not in url and "#" not in url
            and q.SHA.fullmatch(expected) and type(size) is int and 0 < size <= q.MAX_ARCHIVE, "publisher download pin invalid")
    opener = build_opener(NoRedirect())
    digestor, total = hashlib.sha256(), 0
    with opener.open(Request(url, headers={"User-Agent": "som-rust-product-tool-qualification/1"}), timeout=60) as response, path.open("xb") as stream:
        require(response.status == 200 and response.geturl() == url, "publisher response differs")
        while block := response.read(2 * 1024 * 1024):
            total += len(block)
            require(total <= size, "publisher body exceeds declared size")
            digestor.update(block)
            stream.write(block)
    require(total == size and digestor.hexdigest() == expected, "publisher archive bytes or SHA differ")
    return {"url": url, "sha256": expected, "bytes": total}


def admit_component(name, row, archive_path, out, merged):
    require(q.sha(archive_path) == row["publisher_sha256"] and archive_path.stat().st_size == row["archive_bytes"],
            "archive changed before admission")
    wrapper = {"name": name}
    with q.checked_tar(archive_path, wrapper, out) as (archive, expanded):
        entries, total = {}, 0
        for member in archive:
            path = q.clean_path(member.name)
            require(path not in entries and len(entries) < q.MAX_MEMBERS, "duplicate or too many archive members")
            require(member.isdir() or member.isfile(), "archive links or special members refused")
            require(0 <= member.size <= q.MAX_MEMBER, "archive member exceeds bound")
            entries[path] = member
            total += member.size
            require(total <= q.MAX_EXPANDED, "archive scope exceeds bound")
        raw = archive.fileobj
        raw.seek(archive.offset)
        while raw.tell() < raw.size:
            require(not any(raw.read(min(2 * 1024 * 1024, raw.size - raw.tell()))), "nonzero tar trailer")
        regular = {path: value for path, value in entries.items() if value.isfile()}
        directories = {path: value for path, value in entries.items() if value.isdir()}
        require(set(regular) == set(row["all_regular_files"]) and set(directories) == set(row["directories"]),
                "complete archive file or directory inventory differs")
        for path, item in directories.items():
            require(item.mode == row["directories"][path], "archive directory mode differs")
        prefix = row["member_root"] + "/" + row["component_dir"] + "/"
        observed = {}
        for path, member in regular.items():
            pin = row["all_regular_files"][path]
            require(member.size == pin["bytes"] and member.mode == pin["mode"], "archive file size or mode differs")
            stream = archive.extractfile(member)
            require(stream is not None, "archive file unreadable")
            with stream:
                body = stream.read(q.MAX_MEMBER + 1)
            require(len(body) == pin["bytes"] and digest(body) == pin["sha256"], "archive file hash differs")
            if path.startswith(prefix) and path != prefix + "manifest.in":
                relative = q.clean_path(path[len(prefix):])
                expected = merged[relative]
                require(name in expected["components"] and expected["sha256"] == pin["sha256"], "install ownership differs")
                destination = out / "install" / relative
                if destination.exists() or destination.is_symlink():
                    require(bounded_read(destination, q.MAX_MEMBER) == body and stat.S_IMODE(destination.stat().st_mode) == pin["mode"],
                            "existing install merge is not identical owned data")
                else:
                    q.write_new(destination, body, pin["mode"])
            else:
                q.write_new(out / "publisher-records" / name / path, body)
            if path in row["licenses"]:
                q.write_new(out / "licenses" / name / path, body)
            observed[path] = {"sha256": digest(body), "bytes": len(body), "mode": member.mode}
    return {"expanded_tar": expanded, "regular_files": observed, "directories": row["directories"],
            "aliases": {}, "install_manifest_sha256": digest(row["install_manifest_text"].encode()), "licenses": row["licenses"]}


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
            and receipt["source_bindings"][SCRIPT]["sha256"] == digest(bounded_read(Path(__file__).absolute())),
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    q.host()
    repo, out = args.repo.absolute(), args.out.absolute()
    require(out == Path(os.environ["RUNNER_TEMP"]).resolve() / "som-rust-product-tools-qualification", "fixed owned Rust output required")
    q.checked_output(out, repo)
    out.mkdir(mode=0o700)
    for name in ("downloads", "publisher-metadata", "publisher-records", "licenses", "install", "home", "cargo-home", "rustup-home"):
        (out / name).mkdir()
    report = {"version": "som.rust-product-tool-qualification.v1", "source_revision": args.source_revision,
              "status": "incomplete", "commands": [], "components": {}, "tools": {}, "product_acceptance_passed": False,
              "product_runtime_ready": False, "full_product_gates": "unrun"}
    runner = Runner(out, report)
    runner.environment.update(HOME=str(out / "home"), PATH=str(out / "install/bin") + ":/usr/bin:/bin",
                              CARGO_HOME=str(out / "cargo-home"), RUSTUP_HOME=str(out / "rustup-home"),
                              CARGO_NET_OFFLINE="true", RUSTC=str(out / "install/bin/rustc"),
                              CARGO=str(out / "install/bin/cargo"), RUSTFMT=str(out / "install/bin/rustfmt"))
    def cancel(signum, frame):
        raise InterruptedError("owned qualification interrupted")
    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    code = 0
    try:
        blobs, report["source_bindings"] = source_bindings(runner, repo, args.source_revision)
        profile = profile_data(blobs[PROFILE])
        report["profile_sha256"] = digest(blobs[PROFILE])
        q.write_new(out / "profile.json", blobs[PROFILE])
        publisher = profile["publisher_manifest"]
        report["publisher_manifest"] = download(publisher["url"], out / "publisher-metadata/channel.toml", publisher["sha256"], publisher["bytes"])
        report["publisher_checksum"] = download(publisher["checksum_url"], out / "publisher-metadata/channel.toml.sha256", publisher["checksum_sha256"], publisher["checksum_bytes"])
        require(bounded_read(out / "publisher-metadata/channel.toml.sha256").split()[0].decode() == publisher["sha256"], "publisher manifest checksum differs")
        merged = expected_install(profile)
        for name, row in profile["components"].items():
            archive = out / "downloads" / (name + ".tar.xz")
            report["components"][name] = {"download": download(row["url"], archive, row["publisher_sha256"], row["archive_bytes"])}
            report["components"][name]["admission"] = admit_component(name, row, archive, out, merged)
        installed = verify_install_tree(out, profile)
        preserved = verify_preserved_inputs(out, profile)
        q.write_new(out / "installed-files.json", raw_json(installed))
        report["installed_inventory_sha256"] = digest(raw_json(installed))
        for name in sorted(NAMES):
            pin = profile["executables"][name]
            tool = out / "install" / pin["path"]
            require(q.regular(tool) == pin["sha256"] and bounded_read(tool, q.MAX_MEMBER)[:7] == b"\x7fELF\x02\x01\x01", "native regular tool header or hash differs")
            index = len(report["commands"])
            text = runner.run([tool, "--version"], "version-" + name)
            require(version_matches(text, pin) and q.regular(tool) == pin["sha256"], "tool version or after hash differs")
            report["tools"][name] = {"relative_path": "install/" + pin["path"], "sha256": pin["sha256"], "version_text": text, "command_index": index}
        require(verify_install_tree(out, profile) == installed, "installed tree changed during probes")
        require(verify_preserved_inputs(out, profile) == preserved, "publisher or license bytes changed during probes")
        report["preserved_input_inventory"] = preserved
        again, report["source_bindings_after"] = source_bindings(runner, repo, args.source_revision)
        require(again == blobs, "source changed during qualification")
        report["status"] = "qualification_commands_complete_awaiting_independent_review"
        verify_record(out, profile, args.source_revision, report)
        q.write_new(out / "github.env", ("SOM_PRODUCT_RUST_BIN=" + str(out / "install/bin") + "\nCARGO=" + str(out / "install/bin/cargo") + "\nRUSTC=" + str(out / "install/bin/rustc") + "\nRUSTFMT=" + str(out / "install/bin/rustfmt") + "\n").encode())
    except BaseException as error:
        code = 130 if isinstance(error, (InterruptedError, KeyboardInterrupt)) else 1
        report.update(status="interrupted" if code == 130 else "failed", error=type(error).__name__,
                      reason=str(error) if isinstance(error, (Refusal, InterruptedError)) else "see preserved source/download/command evidence")
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        q.write_new(out / "receipt.json", raw_json(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
