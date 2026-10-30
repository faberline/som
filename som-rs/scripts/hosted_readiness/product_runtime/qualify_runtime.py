#!/usr/bin/env python3
"""First-build qualification only. Execute on a GitHub Ubuntu22.04 runner."""

from __future__ import annotations

import argparse
import bz2
from contextlib import contextmanager
import gzip
from datetime import datetime, timezone
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

EXPECTED = {"postgresql": "17.7", "python": "3.12.11", "redis": "8.4.0",
            "mongodb": "7.0.28", "uv": "0.9.9", "uv-licenses": "0.9.9"}
PUBLISHERS = {"ftp.postgresql.org", "www.python.org", "download.redis.io",
              "fastdl.mongodb.org", "github.com", "release-assets.githubusercontent.com",
              "objects.githubusercontent.com"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARCHIVE = 150 * 1024 * 1024
MAX_MEMBER = 256 * 1024 * 1024
MAX_EXPANDED = 800 * 1024 * 1024
MAX_MEMBERS = 100000
SCRIPT_REL = "som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py"
PINS_REL = "som-rs/scripts/hosted_readiness/product_runtime/source-pins.json"
MAX_SOURCE_BLOB = 512 * 1024
LIVE = None


class Refusal(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Refusal(message)


def sha(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(2 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def clean_path(value):
    require(isinstance(value, str) and value, "empty path")
    value = value.removesuffix("/")
    require(value and not PurePosixPath(value).is_absolute() and "\\" not in value
            and all(part not in {"", ".", ".."} for part in value.split("/"))
            and not any(ord(c) < 32 or ord(c) == 127 for c in value), "unsafe relative path")
    return value


def no_links(path):
    require(all(not item.is_symlink() for item in (path, *path.parents)), "symlink path refused")


def regular(path):
    no_links(path)
    require(path.is_file(), "admitted executable must be a regular file")
    return sha(path)


def host():
    require(os.environ.get("GITHUB_ACTIONS") == "true", "GitHub execution only")
    require(platform.system() == "Linux" and platform.machine().lower() == "x86_64", "wrong host")
    values = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    require(values.get("ID", "").strip('"') == "ubuntu" and values.get("VERSION_ID", "").strip('"') == "22.04", "Ubuntu22.04 required")


def checked_output(out, repo):
    require(out.is_absolute() and ".." not in out.parts and not any(ord(c) < 32 or ord(c) == 127 for c in str(out)), "unsafe output")
    no_links(out)
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(temporary in out.parents and repo not in out.parents and not out.exists()
            and out.parent.is_dir(), "output must be new and inside RUNNER_TEMP")


def checked_url(value):
    require(isinstance(value, str), "URL must be a string")
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname in PUBLISHERS
            and not parsed.username and not parsed.password and parsed.port is None
            and not parsed.fragment, "unapproved source publisher")
    return value


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate manifest key")
        result[key] = value
    return result


def manifest_data(raw):
    require(isinstance(raw, bytes), "manifest must be bound Git blob bytes")
    data = json.loads(raw, object_pairs_hook=unique_keys)
    require(isinstance(data, dict), "manifest must be an object")
    require(data.get("version") == "som.native-product-runtime-source-pins.v1", "wrong pin manifest")
    rows = data.get("archives")
    require(isinstance(rows, list) and len(rows) == len(EXPECTED), "incomplete archive inventory")
    found = set()
    for row in rows:
        require(isinstance(row, dict), "invalid archive row")
        name = row.get("name")
        require(isinstance(name, str) and name in EXPECTED and name not in found and row.get("version") == EXPECTED[name], "wrong or duplicate component")
        found.add(name)
        require(row.get("kind") == ("source" if name in {"postgresql", "python", "redis"} else "licenses" if name == "uv-licenses" else "binary"), "wrong archive role")
        require(isinstance(row.get("sha256"), str) and SHA.fullmatch(row["sha256"]), "invalid source pin")
        checked_url(row.get("url"))
        if "archive_bytes" in row:
            require(type(row["archive_bytes"]) is int and 0 < row["archive_bytes"] <= MAX_ARCHIVE, "invalid pinned archive size")
        clean_path(row.get("root"))
        require("/" not in row["root"], "archive root must be one directory")
        licenses = row.get("licenses")
        require(isinstance(licenses, dict) and (licenses or name == "uv"), "license inventory missing")
        for member, expected in licenses.items():
            clean_path(member)
            require(member.startswith(row["root"] + "/") and isinstance(expected, str) and SHA.fullmatch(expected), "invalid license pin")
        executables = row.get("executables", {})
        require(isinstance(executables, dict) and bool(executables) == (row["kind"] == "binary"), "executable member inventory differs")
        if row["kind"] == "binary":
            executable = "mongod" if name == "mongodb" else "uv"
            require(set(executables) == {row["root"] + ("/bin/" if name == "mongodb" else "/") + executable},
                    "wrong admitted executable member")
        destinations = set()
        for member, record in executables.items():
            clean_path(member)
            require(member.startswith(row["root"] + "/") and isinstance(record, dict), "invalid binary member")
            clean_path(record.get("path"))
            require(record["path"] == "bin/" + executable and isinstance(record.get("sha256"), str)
                    and SHA.fullmatch(record["sha256"]) and record["path"] not in destinations, "invalid executable pin")
            destinations.add(record["path"])
        require(not (set(licenses) & set(executables)), "license and executable member overlap")
    return data


def checked_source_paths(repo, manifest):
    require(manifest == repo / PINS_REL and ".." not in manifest.parts,
            "only the fixed committed source-pins.json path is allowed")
    script = Path(__file__).absolute()
    require(script == repo / SCRIPT_REL and ".." not in script.parts,
            "only the fixed committed qualify_runtime.py path is allowed")
    for path in (script, manifest):
        no_links(path)
        require(path.is_file() and path.stat().st_size <= MAX_SOURCE_BLOB, "source authority file is missing or too large")
    return script


def committed_sources(runner, repo, revision, script, manifest):
    """Bind fixed working bytes to the declared HEAD before using archive pins."""
    require(script == repo / SCRIPT_REL and manifest == repo / PINS_REL,
            "source binding requires the two fixed repository paths")
    git = Path("/usr/bin/git").resolve(strict=True)
    command = [git, "--no-replace-objects", "-C", repo]
    authority = {"authority": "declared_head_git_blobs", "declared_revision": revision,
                 "git": {"path": str(git), "sha256": regular(git)}, "files": {}}
    runner.report["source_bindings"] = authority
    head = runner.run_bytes(command + ["rev-parse", "--verify", "HEAD^{commit}"], "binding-head", 128).strip()
    require(head == revision.encode("ascii"), "checkout differs from declared source revision")
    authority["observed_head"] = head.decode("ascii")
    top = runner.run_bytes(command + ["rev-parse", "--show-toplevel"], "binding-root", 4096).strip()
    require(top == os.fsencode(repo), "repo must be the Git worktree root")
    authority["repository_root"] = os.fsdecode(top)
    result = {}
    for relative, path in ((SCRIPT_REL, script), (PINS_REL, manifest)):
        tree = runner.run_bytes(command + ["ls-tree", "-z", revision, "--", relative],
                                "binding-tree-" + path.stem, 4096)
        require(tree.endswith(b"\0") and tree.count(b"\0") == 1, "fixed source path missing from Git tree")
        fields, separator, name = tree[:-1].partition(b"\t")
        fields = fields.split(b" ")
        require(separator and len(fields) == 3 and name == relative.encode("ascii")
                and fields[0] in {b"100644", b"100755"} and fields[1] == b"blob"
                and re.fullmatch(rb"[0-9a-f]{40}", fields[2]), "fixed source Git entry is not a regular blob")
        oid = fields[2].decode("ascii")
        size = runner.run_bytes(command + ["cat-file", "-s", oid], "binding-size-" + path.stem, 128).strip()
        require(size.isdigit() and int(size) <= MAX_SOURCE_BLOB, "committed source blob exceeds limit")
        blob = runner.run_bytes(command + ["cat-file", "blob", oid], "binding-blob-" + path.stem, MAX_SOURCE_BLOB)
        require(len(blob) == int(size), "committed source blob size differs")
        require(hashlib.sha1(b"blob " + str(len(blob)).encode("ascii") + b"\0" + blob).hexdigest() == oid,
                "committed source blob identity differs")
        no_links(path)
        require(path.is_file() and path.stat().st_size <= MAX_SOURCE_BLOB, "working authority file changed type or size")
        working = path.read_bytes()
        binding = {"git_blob_oid": oid, "git_mode": fields[0].decode("ascii"), "bytes": len(blob),
                   "committed_sha256": hashlib.sha256(blob).hexdigest(),
                   "working_sha256": hashlib.sha256(working).hexdigest(), "working_bytes_match": working == blob}
        authority["files"][relative] = binding
        require(working == blob, "working source differs from committed Git blob: " + relative)
        result[relative] = blob
    return result


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(row, destination):
    digest, total = hashlib.sha256(), 0
    request = Request(checked_url(row["url"]), headers={"User-Agent": "SOM-runtime-qualification/1"})
    try:
        with build_opener(Redirect()).open(request, timeout=45) as response, destination.open("xb") as output:
            checked_url(response.geturl())
            while block := response.read(2 * 1024 * 1024):
                total += len(block)
                require(total <= MAX_ARCHIVE, "archive download exceeds limit")
                digest.update(block)
                output.write(block)
    except Refusal:
        raise
    except Exception as error:
        raise Refusal(f"download failed for {row['name']}: {type(error).__name__}") from error
    require(total == row.get("archive_bytes", total), "archive byte count differs from pin")
    require(digest.hexdigest() == row["sha256"], f"{row['name']} archive hash mismatch")
    return {"url": row["url"], "sha256": digest.hexdigest(), "bytes": total}


def write_new(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    path.chmod(mode)


class TarReader:
    """Bound parser reads to the already bounded plain tar file."""

    def __init__(self, stream, size):
        self.stream, self.size = stream, size
        self.name = stream.name

    def read(self, size=-1):
        remaining = self.size - self.stream.tell()
        size = remaining if size < 0 else size
        require(0 <= size <= remaining, "tar parser read exceeds bounded file")
        return self.stream.read(size)

    def tell(self):
        return self.stream.tell()

    def seek(self, offset, whence=0):
        target = offset if whence == 0 else self.tell() + offset if whence == 1 else self.size + offset
        require(0 <= target <= self.size, "tar parser seek exceeds bounded file")
        return self.stream.seek(target)


@contextmanager
def checked_tar(archive_path, row, out):
    temporary = out / "admission" / (row["name"] + ".tar")
    temporary.parent.mkdir(exist_ok=True)
    no_links(temporary)
    require(not temporary.exists(), "existing tar admission output refused")
    created = False
    digest, total = hashlib.sha256(), 0
    with archive_path.open("rb") as stream:
        magic = stream.read(6)
    opener = gzip.open if magic.startswith(b"\x1f\x8b") else bz2.open if magic.startswith(b"BZh") else lzma.open if magic == b"\xfd7zXZ\x00" else None
    require(opener is not None, "unsupported archive compression")
    try:
        with opener(archive_path, "rb") as source, temporary.open("xb") as destination:
            created = True
            while block := source.read(2 * 1024 * 1024):
                total += len(block)
                require(total <= MAX_EXPANDED, "expanded tar stream exceeds limit")
                digest.update(block)
                destination.write(block)
        with temporary.open("rb") as raw:
            with tarfile.open(fileobj=TarReader(raw, total), mode="r:") as archive:
                yield archive, {"bytes": total, "sha256": digest.hexdigest()}
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def admit_archive(row, archive_path, out):
    require(0 < archive_path.stat().st_size <= MAX_ARCHIVE, "archive size exceeds limit")
    require(sha(archive_path) == row["sha256"], "archive changed before admission")
    with checked_tar(archive_path, row, out) as (archive, expanded):
        entries, total = {}, 0
        for member in archive:
            name = clean_path(member.name)
            require(name == row["root"] or name.startswith(row["root"] + "/"), "member outside expected source root")
            require(name not in entries, "duplicate archive member")
            require(0 <= member.size <= MAX_MEMBER, "archive member exceeds limit")
            total += member.size
            require(total <= MAX_EXPANDED, "expanded archive exceeds limit")
            entries[name] = member
            require(len(entries) <= MAX_MEMBERS, "archive member count exceeds limit")
        raw = archive.fileobj
        raw.seek(archive.offset)
        while raw.tell() < raw.size:
            block = raw.read(min(2 * 1024 * 1024, raw.size - raw.tell()))
            require(not any(block), "nonzero data after tar archive end")
        selected = set(row["licenses"]) | set(row.get("executables", {}))
        require(selected <= set(entries), "pinned member missing")
        links = []
        # Validate all selected members and all build sources before any extraction.
        for name, member in entries.items():
            wanted = row["kind"] == "source" or name in selected
            if not wanted:
                continue
            if member.issym() and row["kind"] == "source" and name not in selected:
                target = member.linkname
                require(not PurePosixPath(target).is_absolute() and "\\" not in target
                        and not any(c in target for c in "\r\n\0"), "unsafe source symlink")
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
                require(resolved == row["root"] or resolved.startswith(row["root"] + "/"), "source symlink escapes root")
                require(not any(other.startswith(name + "/") for other in entries), "source symlink is an extraction parent")
                links.append((name, target))
            else:
                require(member.isdir() or member.isfile(), "selected links or special archive members refused")
                require(name not in selected or member.isfile(), "pinned member is not a regular file")
        observed = {}
        for name, member in entries.items():
            if member.isdir() or member.issym():
                continue
            wanted = row["kind"] == "source" or name in selected
            if not wanted:
                continue
            stream = archive.extractfile(member)
            require(stream is not None, "archive member unreadable")
            with stream:
                data = stream.read(MAX_MEMBER + 1)
            require(len(data) == member.size, "archive member size mismatch")
            actual = hashlib.sha256(data).hexdigest()
            if name in row["licenses"]:
                require(actual == row["licenses"][name], "license hash mismatch")
                write_new(out / "licenses" / row["name"] / name, data)
            if name in row.get("executables", {}):
                pin = row["executables"][name]
                require(actual == pin["sha256"], "binary member hash mismatch")
                write_new(out / "install" / row["name"] / pin["path"], data, 0o755)
                observed[pin["path"]] = actual
            if row["kind"] == "source":
                relative = name[len(row["root"]) + 1:]
                require(relative, "source file has no relative path")
                write_new(out / "source" / row["name"] / relative, data, 0o755 if member.mode & 0o111 else 0o644)
        for name, target in links:
            path = out / "source" / row["name"] / name[len(row["root"]) + 1:]
            path.parent.mkdir(parents=True, exist_ok=True)
            require(not path.exists() and not path.is_symlink(), "source link destination already exists")
            path.symlink_to(target)
        source_root = out / "source" / row["name"]
        for name, _ in links:
            try:
                resolved = (source_root / name[len(row["root"]) + 1:]).resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise Refusal("source symlink target is missing or cyclic") from error
            require(resolved == source_root or source_root in resolved.parents, "resolved source symlink escapes root")
    return {"expanded_tar": expanded, "licenses": row["licenses"], "trusted_binary_members": observed}


def stop_owned(process):
    require(isinstance(process.pid, int) and process.pid > 1, "invalid owned process group")
    sent = []
    try:
        os.killpg(process.pid, signal.SIGTERM)
        sent.append(int(signal.SIGTERM))
    except ProcessLookupError:
        return {"signals": sent, "group_gone": True}
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return {"signals": sent, "group_gone": True}
    # The leader can exit while a child still owns this original process group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
        sent.append(int(signal.SIGKILL))
    except ProcessLookupError:
        return {"signals": sent, "group_gone": True}
    process.wait(timeout=10)
    deadline = time.monotonic() + 10
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return {"signals": sent, "group_gone": True}
        require(time.monotonic() < deadline, "owned process group cleanup is unconfirmed")
        time.sleep(0.05)


class Runner:
    def __init__(self, out, report):
        self.out, self.report = out, report
        self.environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                            "TMPDIR": str(out / "tmp"), "XDG_CACHE_HOME": str(out / "cache"),
                            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(out / "pycache"),
                            "PIP_NO_INDEX": "1", "PIP_NO_CACHE_DIR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                            "PIP_CONFIG_FILE": "/dev/null", "UV_PYTHON_DOWNLOADS": "never"}
        for key in ("tmp", "cache", "pycache", "logs", "build"):
            (out / key).mkdir(exist_ok=True)

    def run_bytes(self, argv, label, limit):
        self.run(argv, label)
        stdout = Path(self.report["commands"][-1]["stdout"])
        require(stdout.stat().st_size <= limit, "Git metadata output exceeds limit")
        return stdout.read_bytes()

    def run(self, argv, label, cwd=None, timeout=30):
        global LIVE
        cwd = cwd or self.out
        require(cwd == self.out or self.out in cwd.parents, "process cwd outside owned run")
        no_links(cwd)
        number = len(self.report["commands"])
        stdout, stderr = self.out / "logs" / f"{number:03d}-{label}.stdout", self.out / "logs" / f"{number:03d}-{label}.stderr"
        row = {"argv": [str(x) for x in argv], "cwd": str(cwd), "environment": dict(self.environment),
               "started_at": datetime.now(timezone.utc).isoformat(), "stdout": str(stdout), "stderr": str(stderr),
               "pid": None, "exit_code": None}
        self.report["commands"].append(row)
        started = time.monotonic()
        try:
            with stdout.open("xb") as sink, stderr.open("xb") as errors:
                process = subprocess.Popen(row["argv"], cwd=cwd, env=self.environment, stdout=sink, stderr=errors, start_new_session=True)
                LIVE = process
                row["pid"] = process.pid
                try:
                    process.wait(timeout=timeout)
                except BaseException as error:
                    row["interrupted"] = type(error).__name__
                    row["cleanup"] = {"group_gone": False}
                    row["cleanup"] = stop_owned(process)
                    raise
                finally:
                    LIVE = None if process.poll() is not None else process
                    row["exit_code"] = process.returncode
        finally:
            row.update(elapsed_seconds=time.monotonic() - started, finished_at=datetime.now(timezone.utc).isoformat())
            if stdout.is_file():
                row["stdout_sha256"] = sha(stdout)
            if stderr.is_file():
                row["stderr_sha256"] = sha(stderr)
        require(row["exit_code"] == 0, f"command failed: {label}; see owned logs")
        return stdout.read_text(errors="replace") if stdout.stat().st_size <= 65536 else ""


def inventory(root, out):
    rows = {}
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(out).as_posix()
        if path.is_symlink():
            target = path.resolve(strict=False)
            require(target == out or out in target.parents, "installed alias escapes owned run")
            rows[rel] = {"kind": "alias_not_admitted", "target": os.readlink(path),
                         "sha256": hashlib.sha256(os.readlink(path).encode()).hexdigest(),
                         "qualification_probe_executed": False, "runtime_admitted": False}
        elif path.is_file():
            rows[rel] = {"kind": "regular_file", "sha256": sha(path),
                         "executable_bit": bool(path.stat().st_mode & 0o111)}
    return rows


def observe_builders(runner):
    records = {}
    for name in ("cc", "c++", "make", "ld", "ar", "ranlib", "pkg-config"):
        selected = shutil.which(name, path="/usr/bin:/bin")
        require(selected is not None, f"builder missing: {name}")
        path = Path(selected).resolve(strict=True)
        records[name] = {"path": str(path), "sha256": regular(path),
                         "version_output": runner.run([path, "--version"], f"builder-{name}")}
    runner.environment.update(CC=records["cc"]["path"], CXX=records["c++"]["path"],
                              AR=records["ar"]["path"], RANLIB=records["ranlib"]["path"], LD=records["ld"]["path"])
    runner.run(["/usr/bin/dpkg-query", "-W"], "system-packages")
    return records


def build_sources(runner):
    out = runner.out
    prefixes = {name: out / "install" / name for name in ("python", "postgresql", "redis")}
    runner.report["source_entrypoints"] = {}
    for name in ("python", "postgresql"):
        build = out / "build" / name
        build.mkdir()
        configure = out / "source" / name / "configure"
        runner.report["source_entrypoints"][name] = {"configure_sha256": regular(configure)}
        command = [configure, f"--prefix={prefixes[name]}"]
        command += ["--with-ensurepip=install"] if name == "python" else ["--with-openssl"]
        runner.run(command, f"{name}-configure", build, 300)
        runner.run(["/usr/bin/make", "-j2"], f"{name}-make", build, 2400)
        runner.run(["/usr/bin/make", "altinstall" if name == "python" else "install"],
                   f"{name}-install", build, 900)
    redis = out / "source/redis"
    runner.report["source_entrypoints"]["redis"] = {"makefile_sha256": regular(redis / "Makefile"),
                                                    "src_makefile_sha256": regular(redis / "src/Makefile")}
    runner.run(["/usr/bin/make", "-j2", "MALLOC=libc"], "redis-make", redis, 1200)
    runner.run(["/usr/bin/make", "MALLOC=libc", f"PREFIX={prefixes['redis']}", "install"], "redis-install", redis, 300)


def probe(runner, path, component, expected_hash=None):
    before = regular(path)
    with path.open("rb") as stream:
        require(stream.read(4) == b"\x7fELF" and path.stat().st_mode & 0o111, "admitted tool must be an executable ELF file")
    require(expected_hash is None or before == expected_hash, "admitted binary differs from trusted member pin")
    record = {"path": str(path), "sha256": before, "status": "version_unrun",
              "authority": "trusted_archive_member" if expected_hash else "first_source_build_hash_awaiting_independent_qualification",
              "admitted_for": "qualification_commands_only"}
    runner.report["admitted_executables"][path.relative_to(runner.out).as_posix()] = record
    record["version_command_index"] = len(runner.report["commands"])
    text = runner.run([path, "--version"], f"version-{component}-{path.name}")
    record["version_stdout"] = text
    record["version_exit_code"] = runner.report["commands"][-1]["exit_code"]
    wanted = EXPECTED[component]
    if component == "python":
        matches = text.strip() == f"Python {wanted}"
    elif component == "postgresql":
        matches = "PostgreSQL" in text and re.search(rf"(?<![0-9A-Za-z_.]){re.escape(wanted)}(?![0-9A-Za-z_.])", text) is not None
    elif component == "mongodb":
        matches = re.search(rf"^db version v{re.escape(wanted)}\s*$", text, re.MULTILINE) is not None
    elif component == "uv":
        matches = re.match(rf"uv {re.escape(wanted)}(?:\s|$)", text) is not None
    elif path.name == "redis-server":
        matches = re.search(rf"(?:^|\s)v={re.escape(wanted)}(?:\s|$)", text) is not None
        matches = matches and re.search(r"(?:^|\s)malloc=libc(?:\s|$)", text) and re.search(r"(?:^|\s)bits=64(?:\s|$)", text)
    else:
        matches = text.strip() == f"{path.name} {wanted}"
    require(matches, f"wrong observed version: {component}/{path.name}")
    require(regular(path) == before, "binary changed during version probe")
    runner.run(["/usr/bin/ldd", path], f"libraries-{component}-{path.name}")
    record["status"] = "qualification_commands_passed_not_independently_accepted"
    return record


def qualify(repo, manifest, out, revision):
    host()
    require(isinstance(revision, str) and re.fullmatch("[0-9a-f]{40}", revision), "full source revision required")
    checked_output(out, repo)
    script = checked_source_paths(repo, manifest)
    out.mkdir(mode=0o700)
    os.chdir(out)
    (out / "archives").mkdir()
    report = {"version": "som.product-runtime-qualification.v1", "status": "failed",
              "source_revision": revision, "manifest_sha256": sha(manifest), "script_sha256": sha(script),
              "bootstrap": {"python": str(Path(sys.executable).resolve()), "sha256": regular(Path(sys.executable).resolve()),
                            "version": sys.version, "os_release_sha256": sha(Path("/etc/os-release"))},
              "runner": {k: os.environ.get(k) for k in ("RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")},
              "trusted_sources": {}, "commands": [], "admitted_executables": {}, "first_build_binary_hashes_trusted": False,
              "services": "not_started", "moto": "unrun", "python_wheel_closure": "unrun", "product_acceptance": "unrun",
              "product_runtime_ready": False}
    try:
        runner = Runner(out, report)
        blobs = committed_sources(runner, repo, revision, script, manifest)
        data = manifest_data(blobs[PINS_REL])
        write_new(out / "source-pins.json", blobs[PINS_REL])
        for row in data["archives"]:
            path = out / "archives" / (row["name"] + ".tar")
            report["trusted_sources"][row["name"]] = download(row, path)
            report["trusted_sources"][row["name"]]["admission"] = admit_archive(row, path, out)
        report["builders"] = observe_builders(runner)
        build_sources(runner)
        paths = [(out / "install/python/bin/python3.12", "python", None)]
        pg = out / "install/postgresql/bin"
        for name in ("postgres", "initdb", "pg_ctl", "createdb"):
            regular(pg / name)
        for path in sorted(pg.iterdir()):
            if not path.is_symlink() and path.is_file():
                with path.open("rb") as stream:
                    if stream.read(4) == b"\x7fELF":
                        paths.append((path, "postgresql", None))
        for name in ("redis-server", "redis-cli", "redis-benchmark"):
            paths.append((out / "install/redis/bin" / name, "redis", None))
        for row in data["archives"]:
            for member, pin in row.get("executables", {}).items():
                paths.append((out / "install" / row["name"] / pin["path"], row["name"], pin["sha256"]))
        for path, component, expected in paths:
            report["admitted_executables"][path.relative_to(out).as_posix()] = probe(runner, path, component, expected)
        python = out / "install/python/bin/python3.12"
        report["python_modules"] = runner.run([python, "-I", "-c",
            "import ssl,zlib,bz2,lzma,sqlite3,ctypes,venv,ensurepip,json; print(json.dumps({'openssl':ssl.OPENSSL_VERSION,'zlib':zlib.ZLIB_RUNTIME_VERSION,'sqlite':sqlite3.sqlite_version}))"], "python-required-modules")
        write_new(out / "installed-files.json", (json.dumps(inventory(out / "install", out), indent=2) + "\n").encode())
        intermediates = {**inventory(out / "source", out), **inventory(out / "build", out)}
        write_new(out / "build-intermediates.json", (json.dumps(intermediates, indent=2) + "\n").encode())
        report["status"] = "qualification_commands_completed_awaiting_independent_review"
        return report
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        no_links(out / "receipt.json")
        (out / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")


def interrupted(signum, frame):
    raise InterruptedError(f"qualification interrupted by signal {signum}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--manifest", type=Path, default=Path(PINS_REL), help="fixed committed path only")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args(argv)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        repo = args.repo.resolve(strict=True)
        path = args.manifest if args.manifest.is_absolute() else repo / args.manifest
        result = qualify(repo, path, args.out, args.source_revision)
        print(json.dumps({"status": result["status"], "receipt": str(args.out / "receipt.json"), "product_runtime_ready": False}))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        if LIVE is not None:
            stop_owned(LIVE)
        print(f"qualification failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
