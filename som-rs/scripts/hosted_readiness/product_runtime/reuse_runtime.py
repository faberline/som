#!/usr/bin/env python3
"""Restore controller-audited runtime bytes for later GitHub tests. Product readiness stays false."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys
from types import ModuleType
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = "som-rs/scripts/hosted_readiness/product_runtime/"
SCRIPT, SUPPORT = BASE + "reuse_runtime.py", BASE + "qualify_runtime.py"
SUPPORT_MODULE = "_som_reuse_support"
SUPPORT_SHA256 = "f3c6f69e922ab0a8f27426a5f597c03f213d40be0fb7fc324ac33f1b48ae66f3"


def bootstrap_support(path):
    """Compile the exact reviewed support bytes. Never read a bytecode cache."""
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
    if SUPPORT_MODULE in sys.modules:
        raise RuntimeError("bootstrap support module name already loaded")
    module = ModuleType(SUPPORT_MODULE)
    module.__file__, module.__cached__, module.__package__ = str(path), None, ""
    sys.modules[SUPPORT_MODULE] = module
    try:
        exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(SUPPORT_MODULE, None)
        raise
    return module


q = bootstrap_support(Path(__file__).absolute().with_name("qualify_runtime.py"))
Refusal, require = q.Refusal, q.require
QUALIFICATION, AUDIT = BASE + "runtime-bf3-qualification.json", BASE + "runtime-bf3-byte-audit.json"
PINS = BASE + "source-pins.json"
META_FILES = {"receipt.json", "installed-files.json", "source-pins.json", "build-intermediates.json"}
MAX_META, MAX_FILE, MAX_TOTAL = 8 * 1024 * 1024, 256 * 1024 * 1024, 2 * 1024 * 1024 * 1024
MODULE_PROBE = "import ssl,zlib,bz2,lzma,sqlite3,ctypes,venv,ensurepip,json; print(json.dumps({'openssl':ssl.OPENSSL_VERSION,'zlib':zlib.ZLIB_RUNTIME_VERSION,'sqlite':sqlite3.sqlite_version}))"


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


def bind_sources(runner, repo, revision):
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "full candidate revision required")
    require(Path(__file__).absolute() == repo / SCRIPT and Path(q.__file__).absolute() == repo / SUPPORT, "fixed installer source paths required")
    git = Path("/usr/bin/git").resolve(strict=True)
    command = [git, "--no-pager", "--no-replace-objects", "-C", repo]
    report = {"revision": revision, "git_sha256": q.regular(git), "files": {}}
    runner.report["source_bindings"] = report
    require(runner.run_bytes(command + ["rev-parse", "--verify", "HEAD^{commit}"], "source-head", 128).strip() == revision.encode(), "candidate HEAD differs")
    require(runner.run_bytes(command + ["rev-parse", "--show-toplevel"], "source-root", 4096).strip() == os.fsencode(repo), "repository root differs")
    blobs = {}
    for index, relative in enumerate((SCRIPT, SUPPORT, QUALIFICATION, AUDIT, PINS)):
        working = safe_read(repo / relative)
        tree = runner.run_bytes(command + ["ls-tree", "-z", revision, "--", relative], f"tree-{index}", 4096)
        require(tree.endswith(b"\0") and tree.count(b"\0") == 1, "fixed source path missing")
        header, _, name = tree[:-1].partition(b"\t")
        fields = header.split(b" ")
        require(len(fields) == 3 and fields[0] in {b"100644", b"100755"} and fields[1] == b"blob"
                and name == relative.encode() and re.fullmatch(rb"[0-9a-f]{40}", fields[2]), "regular source Git blob required")
        oid = fields[2].decode()
        size = runner.run_bytes(command + ["cat-file", "-s", oid], f"size-{index}", 128).strip()
        require(size.isdigit() and int(size) <= MAX_META, "source Git blob exceeds limit")
        blob = runner.run_bytes(command + ["cat-file", "blob", oid], f"blob-{index}", MAX_META)
        require(len(blob) == int(size) and hashlib.sha1(b"blob " + size + b"\0" + blob).hexdigest() == oid, "source Git blob identity differs")
        report["files"][relative] = {"git_blob_oid": oid, "sha256": digest(blob), "working_sha256": digest(working), "working_bytes_match": working == blob}
        require(working == blob, "working source differs from committed authority: " + relative)
        blobs[relative] = blob
    return blobs


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


def license_map(pins):
    result = {}
    for row in pins["archives"]:
        for member, sha in row["licenses"].items():
            relative = q.clean_path("licenses/" + row["name"] + "/" + member)
            require(relative not in result, "duplicate license destination")
            result[relative] = sha
    return result


def log_relative(path, prefix):
    require(isinstance(path, str) and path.startswith(prefix + "/"), "qualification log prefix differs")
    relative = q.clean_path(path[len(prefix) + 1:])
    require(relative.startswith("logs/"), "qualification log outside log directory")
    return relative


def transport_expectations(receipt, inventory, pins, qualification):
    require(receipt.get("version") == "som.product-runtime-qualification.v1"
            and receipt.get("status") == "qualification_commands_completed_awaiting_independent_review"
            and receipt.get("source_revision") == qualification["source_revision"]
            and receipt.get("manifest_sha256") == qualification["transport_files"]["source-pins.json"]
            and receipt.get("product_runtime_ready") is False and receipt.get("services") == "not_started", "first qualification receipt differs")
    commands, admitted = receipt.get("commands"), receipt.get("admitted_executables")
    require(isinstance(commands, list) and len(commands) == qualification["counts"]["commands"]
            and isinstance(admitted, dict) and set(admitted) == set(qualification["admitted_paths"]), "complete qualification command inventory required")
    expected = dict(qualification["transport_files"])
    for relative, row in inventory.items():
        target = str(PurePosixPath(relative).parent / row["target"]) if row["kind"] == "alias_not_admitted" else relative
        expected[relative] = inventory[target]["sha256"]
    for command in commands:
        require(type(command.get("exit_code")) is int and command["exit_code"] == 0, "failed source qualification command")
        for stream in ("stdout", "stderr"):
            relative = log_relative(command[stream], qualification["install_prefix"])
            require(relative not in expected and q.SHA.fullmatch(command[stream + "_sha256"]), "duplicate or invalid qualification log")
            expected[relative] = command[stream + "_sha256"]
    for relative, row in admitted.items():
        require(inventory[relative]["kind"] == "regular_file" and row.get("sha256") == inventory[relative]["sha256"]
                and row.get("path") == qualification["install_prefix"] + "/" + relative
                and row.get("status") == "qualification_commands_passed_not_independently_accepted"
                and row.get("admitted_for") == "qualification_commands_only" and type(row.get("version_exit_code")) is int and row["version_exit_code"] == 0
                and isinstance(row.get("version_stdout"), str), "admitted first-build tool binding differs")
        index = row.get("version_command_index")
        require(type(index) is int and 0 <= index < len(commands)
                and commands[index].get("argv") == [row["path"], "--version"]
                and commands[index]["stdout_sha256"] == digest(row["version_stdout"].encode()), "admitted version log differs")
    require(set(receipt.get("trusted_sources", {})) == {row["name"] for row in pins["archives"]}, "six source archive records required")
    for row in pins["archives"]:
        observed = receipt["trusted_sources"][row["name"]]
        require(observed.get("sha256") == row["sha256"] and observed.get("bytes") == row["archive_bytes"]
                and observed.get("admission", {}).get("licenses") == row["licenses"], "first-build source or license evidence differs")
    notices = license_map(pins)
    require(len(notices) == qualification["counts"]["license_files"], "license inventory count differs")
    expected.update(notices)
    return expected


def verify_transport(root, inventory, receipt, pins, qualification):
    expected = transport_expectations(receipt, inventory, pins, qualification)
    files, directories = collect_files(root)
    require(set(files) == set(expected) and directories == expected_directories(expected), "transport contains missing or extra paths")
    for relative, sha in expected.items():
        require(q.sha(files[relative]) == sha, "transport file hash differs: " + relative)
    return files


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise Refusal("artifact metadata redirect refused")


def verify_origin(qualification, out):
    url = f"https://api.github.com/repos/{qualification['repository']}/actions/artifacts/{qualification['artifact_id']}"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "SOM-runtime-reuse/1"}
    token = os.environ.get("SOM_RUNTIME_ACTIONS_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024, "artifact metadata exceeds limit")
    except Refusal:
        raise
    except Exception as error:
        raise Refusal("artifact origin lookup failed: " + type(error).__name__) from error
    data = parse(raw)
    require(isinstance(data, dict) and isinstance(data.get("workflow_run"), dict)
            and type(data.get("id")) is int and data.get("id") == qualification["artifact_id"] and data.get("name") == qualification["artifact_name"]
            and data.get("expired") is False and data.get("digest") == "sha256:" + qualification["artifact_sha256"]
            and type(data["workflow_run"].get("id")) is int and data["workflow_run"]["id"] == qualification["run_id"]
            and data.get("workflow_run", {}).get("head_sha") == qualification["source_revision"]
            and data.get("url") == url, "GitHub artifact metadata differs from committed qualification")
    q.write_new(out / "artifact-origin.json", raw)
    return {"metadata_sha256": digest(raw), "artifact_id": qualification["artifact_id"], "run_id": qualification["run_id"],
            "artifact_sha256": qualification["artifact_sha256"]}


def copy_checked(source, destination, expected, mode=0o644):
    q.no_links(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    q.no_links(destination)
    sha = hashlib.sha256()
    with source.open("rb") as stream, destination.open("xb") as output:
        for block in iter(lambda: stream.read(2 * 1024 * 1024), b""):
            sha.update(block)
            output.write(block)
    require(sha.hexdigest() == expected, "transport changed during copy")
    destination.chmod(mode)


def install_files(files, inventory, pins, qualification, out):
    expected = transport_expectations(parse(safe_read(files["receipt.json"])), inventory, pins, qualification)
    for relative, source in files.items():
        if relative.startswith("install/"):
            row = inventory[relative]
            if row["kind"] == "alias_not_admitted":
                continue
            destination, mode = out / relative, 0o755 if row["executable_bit"] else 0o644
        elif relative.startswith("licenses/"):
            destination, mode = out / relative, 0o644
        else:
            destination, mode = out / "qualification-evidence" / relative, 0o644
        copy_checked(source, destination, expected[relative], mode)
    for relative, target in qualification["aliases"].items():
        path = out / relative
        require(not path.exists() and not path.is_symlink(), "alias output already exists")
        path.symlink_to(target)


def verify_evidence(out, expected):
    for top in ("licenses", "qualification-evidence"):
        files, directories = collect_files(out / top)
        selected = {name: sha for name, sha in expected.items()
                    if (name.startswith("licenses/") if top == "licenses" else not name.startswith(("install/", "licenses/")))}
        relative = {name[len("licenses/"):] if top == "licenses" else name: sha for name, sha in selected.items()}
        require(set(files) == set(relative) and directories == expected_directories(relative), "retained qualification evidence paths differ")
        require(all(q.sha(files[name]) == sha for name, sha in relative.items()), "retained qualification evidence hash differs")


def recheck_tools(runner, old_receipt, qualification):
    for relative in qualification["admitted_paths"]:
        old, path = old_receipt["admitted_executables"][relative], runner.out / relative
        before = q.regular(path)
        with path.open("rb") as stream:
            require(stream.read(4) == b"\x7fELF" and path.stat().st_mode & 0o111 and before == old["sha256"], "qualified ELF hash differs")
        record = {"path": str(path), "sha256": before, "component": PurePosixPath(relative).parts[1],
                  "status": "version_unrun", "authority": "controller_audited_saved_artifact",
                  "qualified_for": "later_GitHub_tests_only", "version_command_index": len(runner.report["commands"])}
        runner.report["admitted_executables"][relative] = record
        argv = [path, "-B", "--version"] if record["component"] == "python" else [path, "--version"]
        stdout = runner.run(argv, "recheck-" + record["component"] + "-" + path.name)
        require(stdout == old["version_stdout"] and q.regular(path) == before
                and runner.report["commands"][-1].get("stderr_sha256") == old_receipt["commands"][old["version_command_index"]]["stderr_sha256"],
                "qualified tool version or bytes differ")
        record.update(status="hash_and_version_rechecked", version_stdout=stdout, version_exit_code=0)
    python = runner.out / "install/python/bin/python3.12"
    index = len(runner.report["commands"])
    stdout = runner.run([python, "-I", "-B", "-c", MODULE_PROBE], "recheck-python-core-modules")
    require(stdout == old_receipt["python_modules"] and runner.report["commands"][-1].get("stderr_sha256") == digest(b""), "qualified Python core module versions differ")
    runner.report["python_modules"] = {"stdout": stdout, "exit_code": 0, "command_index": index}


def reuse(repo, artifact, out, revision):
    q.host()
    q.checked_output(out, repo)
    require(out == Path(os.environ["RUNNER_TEMP"]).resolve() / "som-product-runtime-qualification", "fixed runtime output path required")
    require(artifact.is_absolute() and ".." not in artifact.parts and artifact != out
            and artifact not in out.parents and Path(os.environ["RUNNER_TEMP"]).resolve() in artifact.parents, "owned artifact transport outside output required")
    q.no_links(artifact)
    require(artifact.is_dir(), "artifact transport missing")
    out.mkdir(mode=0o700)
    os.chdir(out)
    report = {"version": "som.product-runtime-reuse.v1", "tested_sha": revision, "status": "failed",
              "qualified_for": "later_GitHub_tests_only", "product_runtime_ready": False, "product_acceptance_passed": False,
              "services": "not_started", "moto": "unrun", "python_wheel_closure": "unrun",
              "installed_tree_verified_before": False, "installed_tree_verified_after": False,
              "installed_inventory_path": "qualification-evidence/installed-files.json",
              "commands": [], "admitted_executables": {},
              "bootstrap": {"python": str(Path(sys.executable).resolve()), "version": sys.version,
                            "sha256": q.regular(Path(sys.executable).resolve())},
              "runner": {key: os.environ.get(key) for key in ("RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")}}
    try:
        runner = q.Runner(out, report)
        blobs = bind_sources(runner, repo, revision)
        qualification = qualification_data(blobs[QUALIFICATION], blobs[AUDIT], blobs[PINS])
        require(out == Path(os.environ["RUNNER_TEMP"]).resolve() / "som-product-runtime-qualification"
                and str(out) == qualification["install_prefix"], "runner path does not match qualified Python prefix")
        report["qualification"] = {"manifest_path": QUALIFICATION, "manifest_sha256": digest(blobs[QUALIFICATION]),
                                   "audit_sha256": digest(blobs[AUDIT]), "artifact_id": qualification["artifact_id"],
                                   "run_id": qualification["run_id"], "artifact_sha256": qualification["artifact_sha256"],
                                   "receipt_sha256": qualification["transport_files"]["receipt.json"],
                                   "installed_inventory_sha256": qualification["transport_files"]["installed-files.json"],
                                   "source_pins_sha256": qualification["transport_files"]["source-pins.json"]}
        report["origin"] = verify_origin(qualification, out)
        for name, expected in qualification["transport_files"].items():
            require(digest(safe_read(artifact / name)) == expected, "qualified transport metadata differs: " + name)
        inventory = read_inventory(artifact / "installed-files.json", qualification)
        receipt = parse(safe_read(artifact / "receipt.json"))
        pins = q.manifest_data(blobs[PINS])
        files = verify_transport(artifact, inventory, receipt, pins, qualification)
        install_files(files, inventory, pins, qualification, out)
        report["installed_tree_before"] = verify_installed_tree(out, inventory, qualification)
        report["installed_tree_verified_before"] = True
        recheck_tools(runner, receipt, qualification)
        report["installed_tree_after"] = verify_installed_tree(out, inventory, qualification)
        verify_evidence(out, transport_expectations(receipt, inventory, pins, qualification))
        report["installed_tree_verified_after"] = True
        report["qualification_evidence_verified_after"] = True
        exports = {"SOM_PRODUCT_PYTHON": out / "install/python/bin/python3.12", "SOM_PRODUCT_UV": out / "install/uv/bin/uv",
                   "SOM_PRODUCT_PG_BIN": out / "install/postgresql/bin", "SOM_PRODUCT_MONGOD": out / "install/mongodb/bin/mongod",
                   "SOM_PRODUCT_REDIS_SERVER": out / "install/redis/bin/redis-server"}
        q.write_new(out / "github.env", "".join(key + "=" + str(value) + "\n" for key, value in exports.items()).encode())
        report["status"] = "audited_runtime_reinstalled_and_rechecked_for_hosted_tests"
        return report
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        q.no_links(out / "receipt.json")
        (out / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, q.interrupted)
    try:
        result = reuse(args.repo.resolve(strict=True), args.artifact_root, args.out, args.source_revision)
        print(json.dumps({"status": result["status"], "receipt": str(args.out / "receipt.json"), "product_runtime_ready": False}))
        return 0
    except BaseException as error:
        if q.LIVE is not None:
            q.stop_owned(q.LIVE)
        print(f"runtime reuse failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
