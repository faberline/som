#!/usr/bin/env python3
"""GitHub-only wheel closure qualification. Never product acceptance.

Bind every owning source and the reused native tree before starting a child.
Resolve each exact dependency input once. Validate every named project offline.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import csv
import configparser
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import tomllib
from types import ModuleType
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

HERE = "som-rs/scripts/hosted_readiness/product_python/"
SCRIPT = HERE + "produce_wheels.py"
POLICY = HERE + "wheel-policy.json"
CONSUMER = HERE + "qualify_python.py"
ENV_PLAN = HERE + "environment-plan.json"
CANDIDATE_GUARD = "som-rs/scripts/hosted_readiness/dependency_candidate.py"
REUSE = "som-rs/scripts/hosted_readiness/product_runtime/reuse_runtime.py"
RUNTIME_SUPPORT = "som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py"
QUALIFICATION = "som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-qualification.json"
BYTE_AUDIT = "som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-byte-audit.json"
SOURCE_PINS = "som-rs/scripts/hosted_readiness/product_runtime/source-pins.json"
INSTALLER_AUTHORITY = HERE + "uv099-installer-authority.json"
MODULES = {"som_python_environment_consumer_for_wheels": CONSUMER, "som_native_reuse_for_wheels": REUSE,
           "som_dependency_candidate_for_wheels": CANDIDATE_GUARD}
SHA = re.compile(r"[a-f0-9]{64}\Z")
PIN = re.compile(r"([a-zA-Z0-9][a-zA-Z0-9._-]*)==([a-zA-Z0-9][a-zA-Z0-9.!+_-]*)\Z")
MAX_SOURCE = 32 * 1024 * 1024
MAX_METADATA = 16 * 1024 * 1024
MAX_WHEEL = 150 * 1024 * 1024
MAX_MEMBER = 128 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_MEMBERS = 100000
MAX_PACKAGES = 1000
HOST = "x86_64-manylinux_2_35"
PYTHON = "3.12.11"
FORBIDDEN = {"mlx", "mlx-lm", "mlx-metal", "transformers", "tokenizers", "safetensors", "sentencepiece", "huggingface-hub", "hf-xet"}


class Refusal(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def normalize(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def relative(value):
    require(isinstance(value, str) and value and not value.startswith("/") and "\\" not in value
            and all(part not in {"", ".", ".."} for part in value.split("/"))
            and not any(ord(c) < 32 or ord(c) == 127 for c in value), "unsafe relative path")
    return value


def no_links(path):
    require(all(not p.is_symlink() for p in (path, *path.parents)), "symlink path refused")


def read(path, limit=MAX_SOURCE):
    no_links(path)
    require(path.is_file() and 0 <= path.stat().st_size <= limit, "regular bounded input required")
    return path.read_bytes()


def file_sha(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(2 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def constant(_):
        raise Refusal("nonfinite JSON number")
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs, parse_constant=constant)


def dump(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def now():
    return datetime.now(timezone.utc).isoformat()


class Sources:
    """Only system Git metadata may run before source and runtime admission."""
    def __init__(self, root, revision):
        self.root, self.revision, self.pins = root, revision, {}
        require(re.fullmatch(r"[a-f0-9]{40}", revision) is not None, "invalid source revision")
        require(self.git("rev-parse", "--verify", "HEAD^{commit}").strip().decode() == revision, "HEAD differs")
        require(self.git("rev-parse", "--show-toplevel").strip() == os.fsencode(root), "repository root differs")

    def git(self, *args):
        return subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-C", str(self.root), *args], stderr=subprocess.DEVNULL)

    def read(self, name):
        relative(name)
        tree = self.git("ls-tree", "-z", self.revision, "--", name)
        fields, separator, actual = tree.removesuffix(b"\0").partition(b"\t")
        require(tree.count(b"\0") == 1 and separator and actual == name.encode(), "source Git path differs")
        bits = fields.split()
        require(len(bits) == 3 and bits[0] in {b"100644", b"100755"} and bits[1] == b"blob", "regular source Git blob required")
        size = self.git("cat-file", "-s", bits[2].decode()).strip()
        require(size.isdigit() and int(size) <= MAX_SOURCE, "source blob exceeds bound")
        raw = self.git("cat-file", "blob", bits[2].decode())
        require(len(raw) == int(size) and raw == read(self.root / name), "source differs from committed blob")
        require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == bits[2].decode(), "Git object identity differs")
        self.pins[name] = {"sha256": sha(raw), "git_blob_oid": bits[2].decode()}
        return raw

    def verify(self):
        previous = dict(self.pins)
        require(self.git("rev-parse", "--verify", "HEAD^{commit}").strip().decode() == self.revision, "HEAD changed during qualification")
        for name, expected in previous.items():
            require(sha(self.read(name)) == expected["sha256"], "source changed during qualification")


def bound_module(sources, name, path):
    require(MODULES.get(name) == path and name not in sys.modules, "fixed fresh source module required")
    raw = sources.read(path)
    module = ModuleType(name)
    module.__file__ = str(sources.root / path)
    module.__cached__, module.__package__ = None, ""
    sys.modules[name] = module
    try:
        # Execute exactly the bound Git bytes. Never consult a file loader or PYC.
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
        return module
    except BaseException:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise


def checked_paths(repo, out, inputs, assembly, runtime, temporary):
    all_inputs = (repo, inputs, assembly, runtime)
    for path in all_inputs:
        require(path.is_absolute() and ".." not in path.parts and path.is_dir(), "absolute existing input directory required")
        no_links(path)
    require(out.is_absolute() and ".." not in out.parts and temporary in out.parents
            and not out.exists() and out.parent.is_dir(), "new output below RUNNER_TEMP required")
    no_links(out)
    require(all(out != root and root not in out.parents and out not in root.parents for root in all_inputs), "output overlaps an input tree")


def admitted_runtime(root, sources, policy, revision):
    # reuse_runtime imports this shared helper. Bind it before that import.
    sources.read(RUNTIME_SUPPORT)
    qualification_raw, audit_raw = sources.read(QUALIFICATION), sources.read(BYTE_AUDIT)
    source_pins_raw = sources.read(SOURCE_PINS)
    reuse = bound_module(sources, "som_native_reuse_for_wheels", REUSE)
    require(sha(audit_raw) == policy["native_authority"]["controller_byte_audit_sha256"], "controller native byte audit differs")
    qualification = reuse.qualification_data(qualification_raw, audit_raw, source_pins_raw)
    require(str(root) == qualification["install_prefix"], "native Python root differs from its qualified build prefix")
    require(qualification["transport_files"]["installed-files.json"] == policy["native_authority"]["qualified_inventory_sha256"], "qualified native inventory authority differs")
    raw = read(root / "receipt.json")
    receipt = parse(raw)
    require(receipt.get("version") == "som.product-runtime-reuse.v1"
            and receipt.get("status") == "audited_runtime_reinstalled_and_rechecked_for_hosted_tests"
            and receipt.get("tested_sha") == revision and receipt.get("qualified_for") == "later_GitHub_tests_only"
            and receipt.get("installed_tree_verified_before") is True and receipt.get("installed_tree_verified_after") is True
            and receipt.get("product_runtime_ready") is False and receipt.get("product_acceptance_passed") is False,
            "verified native reuse receipt required")
    binding = receipt.get("qualification", {})
    require(binding.get("manifest_sha256") == sha(qualification_raw) and binding.get("audit_sha256") == sha(audit_raw)
            and binding.get("installed_inventory_sha256") == policy["native_authority"]["qualified_inventory_sha256"],
            "reuse receipt qualification source bindings differ")
    inventory = reuse.read_inventory(root / "qualification-evidence/installed-files.json", qualification)
    verified = reuse.verify_installed_tree(root, inventory, qualification)
    admitted = receipt.get("admitted_executables", {})
    require(set(admitted) == set(qualification["admitted_paths"]) and len(admitted) == 41, "complete native binary recheck required")
    for name, row in admitted.items():
        require(row.get("status") == "hash_and_version_rechecked" and row.get("version_exit_code") == 0
                and row.get("qualified_for") == "later_GitHub_tests_only"
                and row.get("authority") == "controller_audited_saved_artifact"
                and row.get("sha256") == inventory[name]["sha256"], "one of the complete native tool rechecks failed")
    tools = {}
    for name, relative_path in (("python", "install/python/bin/python3.12"), ("uv", "install/uv/bin/uv")):
        row = admitted[relative_path]
        path = root / relative_path
        no_links(path)
        require(path.is_file() and path.stat().st_mode & 0o111, "qualified executable is not regular executable")
        require(row.get("status") == "hash_and_version_rechecked" and row.get("version_exit_code") == 0
                and row.get("qualified_for") == "later_GitHub_tests_only"
                and row.get("authority") == "controller_audited_saved_artifact"
                and row.get("sha256") == policy["native_sha256"][name] == file_sha(path), "native tool admission differs")
        text = row.get("version_stdout", "")
        require(isinstance(text, str) and (text.strip() == "Python " + PYTHON if name == "python"
                                         else re.match(r"uv 0\.9\.9(?:\s|$)", text) is not None), "native version differs")
        tools[name] = path
    record = {"reuse_receipt_sha256": sha(raw), "tree": verified,
              "qualification_sha256": sources.pins[QUALIFICATION]["sha256"],
              "inventory_sha256": qualification["transport_files"]["installed-files.json"],
              "python_sha256": policy["native_sha256"]["python"], "uv_sha256": policy["native_sha256"]["uv"],
              "qualification_scope": "temporary saved artifact reuse for GitHub qualification; not permanent readiness"}
    return reuse, qualification, inventory, tools, record


def environment_inputs(q, sources, root, assembly, revision, candidate_revision=None):
    candidate_revision = candidate_revision or revision
    receipt_raw = read(root / "receipt.json")
    receipt = parse(receipt_raw)
    require(receipt.get("version") == "som.hosted-python-environment-inventory.v1"
            and receipt.get("source_revision") == revision and receipt.get("helper_revision", revision) == revision
            and receipt.get("candidate_revision", revision) == candidate_revision and receipt.get("state") == "complete-product-environment-inputs"
            and receipt.get("environment_input_set_complete") is True and receipt.get("observed_products") == 671
            and receipt.get("expected_products") == 671 and receipt.get("product_runtime_ready") is False
            and receipt.get("product_acceptance_passed") is False, "complete same-source environment input receipt required")
    plan = parse(sources.read(ENV_PLAN))
    q.inventory(sources, plan)
    golden_raw, ts_raw = sources.read(q.GOLDENS), sources.read(q.TS065)
    goldens = parse(golden_raw)
    require([len(goldens["cohorts"][key]) for key in ("python_public", "python_legacy")] == [127, 544], "whole frozen Python scope differs")
    assembly_raw = read(assembly / "assembly/manifest.json")
    require(receipt.get("assembly_manifest_sha256") == sha(assembly_raw), "environment source assembly changed")
    products = q.products(assembly, parse(assembly_raw), goldens, candidate_revision, sha(golden_raw), sha(ts_raw))
    expected = [{"id": "observer", "requirements": {"requires_python": ">=3.12,<3.13", "runtime": plan["observer_requirements"], "development": []}, "pyproject_bytes": None}]
    observer_products = []
    for index, product in enumerate(products):
        if product["requirements"] is None:
            observer_products.append({key: product[key] for key in ("cohort", "id", "files_map_sha256")})
        else:
            expected.append({"id": f"product-{index:04d}", "requirements": product["requirements"],
                             "pyproject_bytes": product["pyproject_bytes"],
                             **{key if key != "id" else "product_id": product[key] for key in ("cohort", "id", "files_map_sha256")}})
    require(receipt.get("observer_runtime_products") == observer_products, "old-product observer bindings differ")
    supplied = receipt.get("environments", [])
    require(len(supplied) == len(expected) and [row["id"] for row in supplied] == [row["id"] for row in expected], "whole named environment scope differs")
    result = []
    for actual, row in zip(supplied, expected):
        require(actual.get("requirements") == row["requirements"], "direct requirement groups differ")
        for key in ("cohort", "product_id", "files_map_sha256"):
            require(actual.get(key) == row.get(key), "named product environment binding differs")
        directory = root / "environments" / row["id"]
        no_links(directory)
        raw = read(directory / "requirements.in")
        expected_raw = ("\n".join(row["requirements"]["runtime"] + row["requirements"]["development"]) + "\n").encode()
        require(raw == expected_raw and sha(raw) == actual.get("requirements_in_sha256"), "direct requirements bytes differ")
        if row["pyproject_bytes"] is not None:
            project_raw = read(directory / "pyproject.toml")
            require(project_raw == row["pyproject_bytes"].encode() and sha(project_raw) == actual.get("pyproject_sha256"), "original project bytes differ")
        else:
            require(actual.get("pyproject_sha256") is None, "observer project unexpectedly supplied")
        declared = {"requirements.in", "pyproject.toml"} if row["pyproject_bytes"] is not None else {"requirements.in"}
        require({path.name for path in directory.iterdir()} == declared, "unexpected environment input entry")
        result.append({**row, "requirements_raw": raw})
    for path, binding in receipt.get("source_bindings", {}).items():
        require(sha(sources.read(path)) == binding.get("sha256"), "environment source binding differs")
    require(CONSUMER in receipt.get("source_bindings", {}) and ENV_PLAN in receipt["source_bindings"], "environment consumer source bindings missing")
    return result, observer_products, {"environment_receipt_sha256": sha(receipt_raw), "assembly_manifest_sha256": sha(assembly_raw)}


def dependency_key(raw, native):
    identity = {"requirements_in_sha256": sha(raw), "python_sha256": native["python_sha256"],
                "uv_sha256": native["uv_sha256"], "installed_inventory_sha256": native["inventory_sha256"],
                "python": PYTHON, "host": HOST}
    return sha(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())


def locked_requirements(raw):
    """Parse only target-specific, exact, hash-locked uv output."""
    text = raw.decode("utf-8", "strict").replace("\\\n", " ")
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        match = PIN.fullmatch(words[0])
        require(match is not None and len(words) > 1, "unsupported or unhashed lock entry")
        name, version = normalize(match[1]), match[2]
        require(name not in result and name not in FORBIDDEN, "duplicate/forbidden locked package")
        hashes = []
        for word in words[1:]:
            require(word.startswith("--hash=sha256:") and SHA.fullmatch(word.removeprefix("--hash=sha256:")), "only SHA256 wheel lock hashes admitted")
            hashes.append(word.removeprefix("--hash=sha256:"))
        require(len(set(hashes)) == len(hashes), "duplicate lock hash")
        result[name] = {"version": version, "hashes": hashes}
    require(0 < len(result) <= MAX_PACKAGES, "empty or oversized package closure")
    return result


def checked_url(value, metadata=False):
    require(isinstance(value, str), "URL string required")
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname == ("pypi.org" if metadata else "files.pythonhosted.org")
            and parsed.username is None and parsed.password is None and parsed.port is None
            and not parsed.fragment and not parsed.query, "only official PyPI/Pythonhosted HTTPS URLs admitted")
    return value


class Redirect(HTTPRedirectHandler):
    def __init__(self, metadata):
        self.metadata = metadata

    def redirect_request(self, request, fp, code, message, headers, newurl):
        checked_url(newurl, self.metadata)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(url, destination, limit, expected_sha=None, expected_size=None, metadata=False):
    checked_url(url, metadata)
    request = Request(url, headers={"User-Agent": "SOM-wheel-closure-qualification/1", "Accept": "application/json" if metadata else "application/octet-stream"})
    result, total = hashlib.sha256(), 0
    with build_opener(Redirect(metadata)).open(request, timeout=60) as response, destination.open("xb") as stream:
        checked_url(response.geturl(), metadata)
        while block := response.read(2 * 1024 * 1024):
            total += len(block)
            require(total <= limit, "download exceeds bound")
            result.update(block)
            stream.write(block)
    require(expected_size is None or total == expected_size, "download size differs from publisher metadata")
    require(expected_sha is None or result.hexdigest() == expected_sha, "download SHA differs from frozen lock/publisher")
    return {"url": url, "sha256": result.hexdigest(), "size": total}


def wheel_rank(filename):
    """Admit only CPython3.12/abi3/pure wheels on this exact Linux x86_64 host.

    This intentionally narrow PEP425/600 rule refuses unreviewed tag families.
    It selects one deterministic compatible wheel. Offline uv must also accept it.
    """
    require(isinstance(filename, str) and re.fullmatch(r"[A-Za-z0-9_.+]+(?:-[A-Za-z0-9_.+]+){4,5}\.whl", filename) is not None, "wheel filename required")
    parts = filename[:-4].split("-")
    require(len(parts) in {5, 6}, "unsupported wheel filename")
    python, abi, platforms = parts[-3:]
    priorities = []
    for py, ab, host in itertools.product(python.split("."), abi.split("."), platforms.split(".")):
        if host == "any":
            host_rank = 100
        elif host == "linux_x86_64":
            host_rank = 90
        elif host in {"manylinux1_x86_64", "manylinux2010_x86_64", "manylinux2014_x86_64"}:
            host_rank = 35 - {"manylinux1_x86_64": 5, "manylinux2010_x86_64": 12, "manylinux2014_x86_64": 17}[host]
        else:
            match = re.fullmatch(r"manylinux_2_([0-9]+)_x86_64", host)
            if match is None or not 5 <= int(match[1]) <= 35:
                continue
            host_rank = 35 - int(match[1])
        if py == "cp312" and ab == "cp312" and host != "any":
            py_rank = 0
        elif py.startswith("cp3") and py[3:].isdigit() and 2 <= int(py[3:]) <= 12 and ab == "abi3" and host != "any":
            py_rank = 1 + 12 - int(py[3:])
        elif py == "cp312" and ab == "none":
            py_rank = 20
        elif py in {"py3", "py312"} and ab == "none":
            py_rank = 30 if py == "py312" else 31
        else:
            continue
        priorities.append((py_rank, host_rank))
    return min(priorities) if priorities else None


def selected_wheel(metadata, name, pin):
    require(normalize(metadata.get("info", {}).get("name", "")) == name and metadata["info"].get("version") == pin["version"], "publisher package identity differs")
    candidates = []
    for row in metadata.get("urls", []):
        if row.get("packagetype") != "bdist_wheel" or row.get("yanked"):
            continue
        rank = wheel_rank(row.get("filename", ""))
        if rank is None:
            continue
        digest = row.get("digests", {}).get("sha256")
        require(isinstance(digest, str) and SHA.fullmatch(digest), "publisher wheel SHA missing")
        if digest not in pin["hashes"]:
            continue
        checked_url(row.get("url"))
        require(type(row.get("size")) is int and 0 < row["size"] <= MAX_WHEEL, "publisher wheel size invalid")
        require(unquote(urlsplit(row["url"]).path.rsplit("/", 1)[-1]) == row["filename"], "publisher URL/filename differ")
        candidates.append((rank, row["filename"], row))
    require(candidates, "no locked compatible publisher wheel: " + name)
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def audit_wheel(path, name, version, notice_root):
    """Read all wheel bytes, verify RECORD, and preserve metadata/notices."""
    no_links(path)
    require(path.is_file() and path.stat().st_size <= MAX_WHEEL and wheel_rank(path.name) is not None, "compatible bounded wheel required")
    files, members, notices, directories = {}, [], [], []
    with zipfile.ZipFile(path) as wheel:
        infos = wheel.infolist()
        require(0 < len(infos) <= MAX_MEMBERS and len({row.filename for row in infos}) == len(infos), "wheel duplicate/member bound")
        normalized_paths = [row.filename.removesuffix("/") for row in infos]
        require(len(set(normalized_paths)) == len(normalized_paths), "wheel file/directory path collision")
        file_paths = {row.filename for row in infos if not row.is_dir()}
        require(all(not any("/".join(row.filename.split("/")[:index]) in file_paths
                            for index in range(1, len(row.filename.split("/")))) for row in infos), "wheel parent path is a file")
        require(sum(row.file_size for row in infos) <= MAX_EXPANDED, "wheel expansion exceeds bound")
        for info in infos:
            relative(info.filename.removesuffix("/"))
            kind = (info.external_attr >> 16) & 0o170000
            require(kind in {0, stat.S_IFREG, stat.S_IFDIR} and not (info.flag_bits & 1)
                    and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, "wheel link, special member, encryption or compression refused")
            require(info.file_size <= MAX_MEMBER, "wheel member exceeds bound")
            if info.is_dir():
                directories.append(info.filename.removesuffix("/"))
                continue
            data = wheel.read(info)
            require(len(data) == info.file_size, "wheel member size differs")
            files[info.filename] = {"sha256": sha(data), "size": len(data)}
        metadata_names = []
        for key in files:
            if key.count("/") != 1 or not key.endswith(".dist-info/METADATA"):
                continue
            base = key.split("/", 1)[0].removesuffix(".dist-info")
            distribution, separator, wheel_version = base.rpartition("-")
            if separator and normalize(distribution) == name and wheel_version == version:
                metadata_names.append(key)
        require(len(metadata_names) == 1, "one exact top-level wheel METADATA root required")
        prefix = metadata_names[0].removesuffix("METADATA")
        data_dir = prefix.removesuffix(".dist-info/") + ".data"
        metadata_raw = wheel.read(metadata_names[0])
        require(len(metadata_raw) <= MAX_METADATA, "wheel METADATA too large")
        metadata = BytesParser(policy=default).parsebytes(metadata_raw)
        require(len(metadata.get_all("Name", [])) == len(metadata.get_all("Version", [])) == 1
                and normalize(metadata["Name"]) == name and metadata["Version"] == version and name not in FORBIDDEN,
                "wheel METADATA identity differs")
        wheel_raw = wheel.read(prefix + "WHEEL")
        wheel_meta = BytesParser(policy=default).parsebytes(wheel_raw)
        require(wheel_meta["Wheel-Version"] == "1.0" and wheel_meta.get_all("Tag"), "unsupported WHEEL metadata")
        wheel_tags = set(wheel_meta.get_all("Tag"))
        parts = path.name[:-4].split("-")
        require(normalize(parts[0]) == name and parts[1] == version, "wheel archive filename identity differs")
        filename_tags = {"-".join(tags) for tags in itertools.product(*(part.split(".") for part in parts[-3:]))}
        require(wheel_tags == filename_tags, "WHEEL tags differ from archive name")
        record_name = prefix + "RECORD"
        record_raw = wheel.read(record_name)
        require(len(record_raw) <= MAX_METADATA, "wheel RECORD too large")
        record = {}
        for row in csv.reader(io.StringIO(record_raw.decode("utf-8", "strict"), newline="")):
            require(len(row) == 3, "RECORD columns differ")
            member, digest, size = row
            relative(member)
            require(member not in record and member in files, "RECORD duplicate or unknown member")
            if member == record_name:
                require(digest == size == "", "RECORD self hash must be empty")
            else:
                algorithm, separator, encoded = digest.partition("=")
                require(separator and algorithm in {"sha256", "sha384", "sha512"} and size.isdigit(), "RECORD strong hash/size required")
                digest_bytes = bytes.fromhex(files[member]["sha256"]) if algorithm == "sha256" else hashlib.new(algorithm, wheel.read(member)).digest()
                expected = base64.urlsafe_b64encode(digest_bytes).rstrip(b"=").decode()
                require(encoded == expected and int(size) == files[member]["size"], "RECORD hash or size differs")
            record[member] = row
        require(record_name in record, "RECORD self entry missing")
        for member, info in sorted(files.items()):
            signature = member in {prefix + "RECORD.jws", prefix + "RECORD.p7s"}
            require(member in record or signature, "wheel file omitted from RECORD")
            members.append({"path": member, **info, "record_binding": "external-signature" if signature and member not in record else "self" if member == record_name else "sha256"})
            basename = member.rsplit("/", 1)[-1].lower()
            if ".dist-info/licenses/" in member or basename.startswith(("license", "licence", "copying", "notice")):
                body = wheel.read(member)
                destination = notice_root / (sha(member.encode()) + ".txt")
                destination.write_bytes(body)
                notices.append({"path": member, **info, "preserved": str(destination.name)})
        for member, body in (("METADATA", metadata_raw), ("WHEEL", wheel_raw), ("RECORD", record_raw)):
            (notice_root / member).write_bytes(body)
    return {"filename": path.name, "sha256": file_sha(path), "size": path.stat().st_size,
            "name": name, "version": version, "metadata_sha256": sha(metadata_raw), "record_sha256": sha(record_raw),
            "dist_info_dir": prefix.removesuffix("/"), "data_dir": data_dir, "directories": directories,
            "record_entries": list(record.values()),
            "wheel_metadata_sha256": sha(wheel_raw), "requires_dist": metadata.get_all("Requires-Dist", []),
            "requires_python": metadata["Requires-Python"], "license_expression": metadata["License-Expression"],
            "license_label": metadata["License"], "license_files_declared": metadata.get_all("License-File", []),
            "license_notices": notices, "notice_status": "present" if notices else "none_in_publisher_wheel; independent legal review unresolved",
            "members": members, "record_verified": True, "independent_metadata_license_audit": "unrun"}


@contextmanager
def cancellation():
    state = {"raised": False}
    def interrupt(number, _frame):
        if state["raised"]:
            return
        state["raised"] = True
        raise InterruptedError("wheel producer interrupted by signal " + str(number))
    watched = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    previous = {number: signal.getsignal(number) for number in watched}
    for number in watched:
        signal.signal(number, interrupt)
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def group_exists(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_owned_descendants(pid):
    if not group_exists(pid):
        return True
    for number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, number)
        except ProcessLookupError:
            return True
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not group_exists(pid):
                return True
            time.sleep(0.1)
    return not group_exists(pid)


class Runner:
    def __init__(self, out, report, tools, native, assets=None):
        self.out, self.report, self.tools, self.native = out, report, tools, native
        self.installer_assets = assets

    def run(self, args, cwd, cache, label, offline=False, venv=None):
        require(re.fullmatch(r"[a-z0-9-]+", label) is not None, "unsafe command label")
        for name, path in self.tools.items():
            no_links(path)
            require(path.is_file() and file_sha(path) == self.native[name + "_sha256"], "native tool changed before child")
        require(Path(args[0]) in self.tools.values(), "only admitted native child entrypoints allowed")
        if Path(args[0]) == self.tools["python"]:
            require([str(value) for value in args[1:4]] == ["-I", "-S", "-B"], "base Python children must disable site and bytecode")
        environment = {"PATH": str(self.tools["python"].parent) + ":" + str(self.tools["uv"].parent) + ":/usr/bin:/bin",
                       "HOME": str(self.out / "home"), "TMPDIR": str(self.out / "tmp"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "UV_PYTHON_DOWNLOADS": "never",
                       "UV_NO_CONFIG": "1", "UV_NO_BUILD": "1", "UV_KEYRING_PROVIDER": "disabled",
                       "UV_DEFAULT_INDEX": "https://pypi.org/simple", "UV_CACHE_DIR": str(cache), "UV_NO_PROGRESS": "1"}
        # Source-proven qualification mode. Never export this through GITHUB_ENV.
        # No INSTALLER/REQUESTED/direct_url/cache/build metadata is generated.
        environment["UV_NO_INSTALLER_METADATA"] = "1"
        environment["UV_COMPILE_BYTECODE"] = "0"
        if offline:
            environment["UV_OFFLINE"] = "1"
        if venv is not None:
            environment["UV_PROJECT_ENVIRONMENT"] = str(venv)
        stdout, stderr = self.out / "logs" / (label + ".stdout"), self.out / "logs" / (label + ".stderr")
        record = {"argv": [str(arg) for arg in args], "cwd": str(cwd), "environment": environment,
                  "started_at": now(), "stdout": str(stdout.relative_to(self.out)), "stderr": str(stderr.relative_to(self.out)),
                  "exit_code": None, "owned_group_stopped": False}
        self.report["commands"].append(record)
        started = time.monotonic()
        try:
            with stdout.open("xb") as output, stderr.open("xb") as errors:
                child = subprocess.Popen(record["argv"], cwd=cwd, env=environment, stdout=output, stderr=errors, start_new_session=True)
                record["owned_pid"] = child.pid
                try:
                    record["exit_code"] = child.wait(timeout=1200)
                except BaseException:
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        child.wait()
                    raise
                finally:
                    record.update(exit_code=child.poll(), owned_parent_stopped=child.poll() is not None,
                                  owned_group_stopped=stop_owned_descendants(child.pid))
        finally:
            record.update(finished_at=now(), elapsed_seconds=time.monotonic() - started)
            for kind, path in (("stdout", stdout), ("stderr", stderr)):
                if path.is_file():
                    record[kind + "_sha256"] = file_sha(path)
                    record[kind + "_bytes"] = path.stat().st_size
        require(record["exit_code"] == 0 and record["owned_group_stopped"], "native command failed: " + label)
        return read(stdout, MAX_METADATA)


def uv_lock_inventory(raw):
    data = tomllib.loads(raw.decode("utf-8"))
    python = data.get("requires-python")
    # Pinned uv 0.9.9 normalizes this project's exact minor range to a prefix.
    python_range = type(python) is str and python.replace(" ", "") in {">=3.12,<3.13", "==3.12.*"}
    require(type(data.get("version")) is int and data["version"] == 1
            and python_range, "unreviewed uv lock version/Python constraint")
    packages = data.get("package", [])
    require(isinstance(packages, list) and 0 < len(packages) <= MAX_PACKAGES, "uv package inventory missing or oversized")
    root_count = 0
    for package in packages:
        name = normalize(package["name"])
        require(name not in FORBIDDEN, "forbidden package in universal lock")
        source = package.get("source", {})
        if source == {"virtual": "."}:
            root_count += 1
            continue
        require(source == {"registry": "https://pypi.org/simple"}, "uv lock source is not official PyPI")
        artifacts = package.get("wheels", []) + ([package["sdist"]] if "sdist" in package else [])
        for row in artifacts:
            checked_url(row.get("url"))
            require(isinstance(row.get("hash"), str) and row["hash"].startswith("sha256:") and SHA.fullmatch(row["hash"][7:]), "uv artifact SHA missing")
    require(root_count == 1, "one original unpackaged project root required")
    return {"package_records": len(packages), "source_policy": "one virtual original project and official PyPI only",
            "sdist_records": "metadata retained only; never selected, downloaded by this tool, built or installed"}


def observer_project(row):
    require(row["id"] == "observer" and row["pyproject_bytes"] is None, "only declared observer can receive an observer project")
    return ('[project]\nname = "som-hosted-observer"\nversion = "0.0.0"\nrequires-python = ">=3.12,<3.13"\ndependencies = '
            + json.dumps(row["requirements"]["runtime"]) + '\n[dependency-groups]\ndev = []\n[tool.uv]\npackage = false\n').encode()


def resolve_set(runner, row, key, record):
    root = runner.out / "closures" / key
    root.mkdir()
    project, cache, wheels = root / "project", root / "cache", root / "wheels"
    for path in (project, cache, wheels, root / "publisher-metadata", root / "wheel-audits"):
        path.mkdir()
    project_raw = row["pyproject_bytes"].encode() if row["pyproject_bytes"] is not None else observer_project(row)
    (project / "pyproject.toml").write_bytes(project_raw)
    (root / "requirements.in").write_bytes(row["requirements_raw"])
    uv, python = runner.tools["uv"], runner.tools["python"]
    common = ["--python", python, "--no-managed-python", "--no-config", "--no-build"]
    label = "closure-" + key[:20]
    record.update(root=str(root.relative_to(runner.out)), representative_environment=row["id"],
                  requirements_in_sha256=sha(row["requirements_raw"]), pyproject_sha256=sha(project_raw), state="resolving", wheels=[])
    try:
        runner.run([uv, "lock", *common, "--default-index", "https://pypi.org/simple"], project, cache, label + "-universal-lock")
        universal_raw = read(project / "uv.lock")
        record["universal_lock_inventory"] = uv_lock_inventory(universal_raw)
        record["uv_lock_sha256"] = sha(universal_raw)
        exported = root / "requirements.universal.lock"
        runner.run([uv, "export", "--frozen", "--offline", "--no-config", "--no-emit-project", "--format", "requirements.txt", "--output-file", exported],
                   project, cache, label + "-frozen-export", offline=True)
        record["universal_requirements_sha256"] = sha(read(exported))
        locked = root / "requirements.target.lock"
        target = ["--python", python, "--python-version", PYTHON, "--python-platform", HOST,
                  "--no-managed-python", "--no-config", "--only-binary", ":all:", "--generate-hashes", "--no-header", "--no-annotate", "--format", "requirements.txt"]
        runner.run([uv, "pip", "compile", exported, *target, "--default-index", "https://pypi.org/simple", "--output-file", locked],
                   project, cache, label + "-target-lock")
        locked_raw = read(locked)
        pins = locked_requirements(locked_raw)
        universal_artifacts = {}
        for package in tomllib.loads(universal_raw.decode())["package"]:
            if package.get("source") == {"virtual": "."}:
                continue
            universal_artifacts[(normalize(package["name"]), package["version"])] = {row["hash"][7:] for row in package.get("wheels", [])}
        require(all((name, pin["version"]) in universal_artifacts for name, pin in pins.items()),
                "target package version is outside the original frozen universal lock")
        record["target_requirements_sha256"] = sha(locked_raw)
        selected_lines, audits = [], {}
        total_bytes = 0
        for name, pin in sorted(pins.items()):
            metadata_path = root / "publisher-metadata" / (name + ".json")
            record["active_download"] = {"package": name, "version": pin["version"], "stage": "official publisher metadata"}
            metadata_record = download("https://pypi.org/pypi/" + quote(name, safe="") + "/" + quote(pin["version"], safe="") + "/json",
                                       metadata_path, MAX_METADATA, metadata=True)
            candidate = selected_wheel(parse(read(metadata_path, MAX_METADATA)), name, pin)
            digest = candidate["digests"]["sha256"]
            require(digest in universal_artifacts[(name, pin["version"])], "selected wheel SHA is outside the original frozen universal lock")
            wheel = wheels / candidate["filename"]
            download_record = download(candidate["url"], wheel, MAX_WHEEL, digest, candidate["size"])
            total_bytes += candidate["size"]
            require(total_bytes <= 3 * 1024 * 1024 * 1024, "closure wheel bytes exceed bound")
            notice_root = root / "wheel-audits" / name
            notice_root.mkdir()
            audit = audit_wheel(wheel, name, pin["version"], notice_root)
            audit.update(publisher_metadata=metadata_record, download=download_record)
            dump(notice_root / "inventory.json", audit)
            audits[name] = audit
            record["wheels"].append({"name": name, "version": pin["version"], "filename": wheel.name, "sha256": digest,
                                     "metadata_sha256": audit["metadata_sha256"], "record_sha256": audit["record_sha256"],
                                     "audit_sha256": file_sha(notice_root / "inventory.json"),
                                     "publisher_metadata_sha256": metadata_record["sha256"], "audit": str((notice_root / "inventory.json").relative_to(runner.out))})
            selected_lines.append(name + "==" + pin["version"] + " --hash=sha256:" + digest)
        selected = root / "requirements.selected.lock"
        selected.write_text("\n".join(selected_lines) + "\n")
        offline = root / "requirements.offline.lock"
        # These additive pins retain every original requirement and extra. They
        # cannot replace or weaken an original requirement as an override would.
        runner.run([uv, "pip", "compile", root / "requirements.in", "--constraint", selected, *target,
                    "--offline", "--no-index", "--find-links", wheels, "--output-file", offline],
                   project, cache, label + "-offline-original-closure", offline=True)
        offline_pins = locked_requirements(read(offline))
        selected_pins = locked_requirements(read(selected))
        require(offline_pins == selected_pins, "offline original requirement closure differs from selected frozen wheels")
        require(read(project / "pyproject.toml") == project_raw and read(project / "uv.lock") == universal_raw,
                "closure command changed original requirements or universal lock")
        record.update(state="wheel-closure-qualified", requirements_selected_sha256=sha(read(selected)),
                      requirements_offline_sha256=sha(read(offline)), wheels_bytes=total_bytes,
                      offline_original_closure_verified=True, independent_metadata_license_audit="unrun")
        record.pop("active_download", None)
        return {"root": root, "cache": cache, "wheels": wheels, "pins": selected_pins, "record": record, "audits": audits}
    except BaseException as error:
        record.update(state="interrupted" if isinstance(error, (InterruptedError, KeyboardInterrupt)) else "failed",
                      error=type(error).__name__, reason=str(error) if isinstance(error, (Refusal, InterruptedError)) else "see preserved closure evidence")
        raise
    finally:
        dump(root / "receipt.json", record)


PROBE = '''import hashlib,importlib.metadata as m,json,sys
from pathlib import Path
site=Path(sys.argv[1])
if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise RuntimeError("isolated no-site no-bytecode probe required")
if str(site) in sys.path:
    raise RuntimeError("installed site must never enter sys.path")
rows=[]
for distribution in m.distributions(path=[str(site)]):
    raw=distribution.read_text("METADATA")
    rows.append({"name":distribution.metadata["Name"],"version":distribution.version,"metadata_sha256":hashlib.sha256(raw.encode()).hexdigest()})
print(json.dumps({"python":sys.version.split()[0],"executable":sys.executable,"site":str(site),"site_disabled":True,"site_in_sys_path":False,"packages":rows},sort_keys=True))
'''


def installed_inventory(venv, out):
    require(venv.is_dir() and not venv.is_symlink() and out in venv.parents, "owned regular venv required")
    records = {}
    for path in sorted(venv.rglob("*")):
        relative_path = path.relative_to(venv).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            records[relative_path] = {"type": "symlink", "target": target}
        elif stat.S_ISREG(mode):
            records[relative_path] = {"type": "file", "sha256": file_sha(path), "size": path.stat().st_size, "mode": stat.S_IMODE(mode)}
        else:
            require(stat.S_ISDIR(mode), "special installed venv entry refused")
    return records


def verify_closure(closure):
    root, record = closure["root"], closure["record"]
    for path, field in (("requirements.in", "requirements_in_sha256"), ("project/pyproject.toml", "pyproject_sha256"),
                        ("project/uv.lock", "uv_lock_sha256"), ("requirements.universal.lock", "universal_requirements_sha256"),
                        ("requirements.target.lock", "target_requirements_sha256"), ("requirements.selected.lock", "requirements_selected_sha256"),
                        ("requirements.offline.lock", "requirements_offline_sha256")):
        require(sha(read(root / path)) == record[field], "frozen closure input changed: " + path)
    require({path.name for path in closure["wheels"].iterdir()} == {row["filename"] for row in record["wheels"]}, "frozen wheelhouse entries differ")
    for row in record["wheels"]:
        wheel = closure["wheels"] / row["filename"]
        no_links(wheel)
        require(wheel.is_file() and file_sha(wheel) == row["sha256"], "frozen wheel bytes changed")
        audit_root = root / "wheel-audits" / row["name"]
        require(sha(read(audit_root / "inventory.json")) == row["audit_sha256"], "wheel member audit changed")
        audit = closure["audits"][row["name"]]
        require(sha(read(root / "publisher-metadata" / (row["name"] + ".json"), MAX_METADATA)) == row["publisher_metadata_sha256"], "publisher metadata changed")
        for path, expected in (("METADATA", audit["metadata_sha256"]), ("WHEEL", audit["wheel_metadata_sha256"]), ("RECORD", audit["record_sha256"])):
            require(sha(read(audit_root / path, MAX_METADATA)) == expected, "preserved wheel metadata changed")
        for notice in audit["license_notices"]:
            require(sha(read(audit_root / notice["preserved"], MAX_MEMBER)) == notice["sha256"], "preserved license/notice changed")


def installer_assets(raw):
    authority = parse(raw)
    require(authority.get("version") == "som.uv099-installer-source-authority.v1" and authority.get("uv") == "0.9.9", "installer source authority differs")
    expected = {"activate", "activate.csh", "activate.fish", "activate.nu", "activate.ps1", "activate.bat", "deactivate.bat", "pydoc.bat", "activate_this.py", "_virtualenv.py"}
    require(set(authority.get("assets", {})) == expected, "exact uv seed source inventory required")
    endings = authority.get("checkout_line_endings", {})
    require(endings.get("url") == "https://github.com/astral-sh/uv/blob/0.9.9/crates/uv-virtualenv/src/activator/.gitattributes"
            and endings.get("text") == "*.bat text eol=crlf\n" and sha(endings["text"].encode()) == endings.get("sha256"), "uv source checkout line-ending authority differs")
    result = {}
    for name, row in authority["assets"].items():
        source = "crates/uv-virtualenv/src/" + (name if name == "_virtualenv.py" else "activator/" + name)
        require(row.get("url") == "https://github.com/astral-sh/uv/blob/0.9.9/" + source
                and isinstance(row.get("text"), str) and sha(row["text"].encode()) == row.get("sha256"), "uv seed source bytes differ")
        value = row["text"].encode()
        result[name] = value.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n") if name.endswith(".bat") else value
    return result


def sha_record(raw):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()


def uv_shebang(executable):
    text = str(executable)
    if len(os.fsencode(text)) + 3 > 127 or " " in text:
        escaped = text.replace("'", "'\"'\"'")
        return "#!/bin/sh\n'''exec' '" + escaped + "' \"$0\" \"$@\"\n' '''"
    return "#!" + text


def console_entrypoints(raw):
    parser = configparser.RawConfigParser(interpolation=None, strict=True, delimiters=("=",))
    parser.optionxform = str
    parser.read_string(raw.decode("utf-8", "strict"))
    require(not parser.defaults(), "unreviewed entrypoint DEFAULT section")
    result = []
    for section in ("console_scripts", "gui_scripts"):
        if not parser.has_section(section):
            continue
        for name, value in parser.items(section, raw=True):
            require(re.fullmatch(r"[A-Za-z0-9_.-]+", name) is not None and name not in {"python", "pythonw", "python3"}
                    and not re.fullmatch(r"python3\.[0-9]+", name), "unsafe or reserved console script name")
            match = re.fullmatch(r"([\w\d_\-.]+)\s*:\s*([\w\d_\-.]+)(?:\s*\[\s*[^\]]+\])?\s*", value)
            require(match is not None, "unreviewed console script entry point")
            if name.startswith("pip3.") and name[5:].isdigit():
                continue
            result.append((name, match[1], match[2]))
            if name == "pip3":
                result.append(("pip3.12", match[1], match[2]))
    require(len({row[0] for row in result}) == len(result), "duplicate console/gui entrypoint")
    return result


def console_bytes(venv, module, function):
    return (uv_shebang(venv / "bin/python") + "\n# -*- coding: utf-8 -*-\nimport sys\nfrom " + module + " import " + function.split(".", 1)[0]
            + '\nif __name__ == "__main__":\n    if sys.argv[0].endswith("-script.pyw"):\n        sys.argv[0] = sys.argv[0][:-11]\n'
            + '    elif sys.argv[0].endswith(".exe"):\n        sys.argv[0] = sys.argv[0][:-4]\n    sys.exit(' + function + '())\n').encode()


def installed_scope(venv, closure, assets, base_python, project_name):
    """Derive every allowed file from pinned uv source and verified wheels.

    No installed bytes are used to select or widen the expected set.
    UV_NO_INSTALLER_METADATA=1 suppresses all optional installer metadata.
    """
    site = "lib/python3.12/site-packages"
    expected, contents, origins, expected_dirs = {}, {}, {}, set()
    total_bytes = 0
    proof = {"source_wheel_members": 0, "generated_console_scripts": 0, "regenerated_record_files": 0,
             "uv_seed_files": 0, "suppressed_installer_metadata": True, "skipped_legacy_entrypoint_stubs": []}
    def parents(name):
        for parent in Path(name).parents:
            if str(parent) != ".":
                expected_dirs.add(parent.as_posix())
    def put(name, raw, origin):
        nonlocal total_bytes
        relative(name)
        row = {"type": "file", "sha256": sha(raw), "size": len(raw)}
        require(name not in expected or expected[name] == row, "conflicting installed file sources: " + name)
        if name not in expected:
            total_bytes += len(raw)
        require(total_bytes <= MAX_EXPANDED and len(expected) <= MAX_MEMBERS, "complete installed scope exceeds bound")
        expected[name], contents[name], origins[name] = row, raw, origin
        parents(name)
    def link(name, target):
        require(name not in expected, "installed link source collision")
        expected[name] = {"type": "symlink", "target": target}
        origins[name] = "uv0.9.9 virtualenv.rs"
        parents(name)
    put("CACHEDIR.TAG", b"Signature: 8a477f597d28d172789f06886806bc55", "uv0.9.9 cachedir.rs HEADER")
    put(".gitignore", b"*", "uv0.9.9 virtualenv.rs")
    put(".lock", b"", "uv0.9.9 uv-python interpreter.rs and uv-fs LockedFile::create")
    prompt = normalize(project_name)
    cfg = f"home = {base_python.parent}\nimplementation = CPython\nuv = 0.9.9\nversion_info = 3.12.11\ninclude-system-site-packages = false\nprompt = {prompt}\n"
    put("pyvenv.cfg", cfg.encode(), "uv0.9.9 virtualenv.rs ordered write_cfg")
    link("bin/python", str(base_python))
    link("bin/python3", "python")
    link("bin/python3.12", "python")
    link("lib64", "lib")
    for name, raw in assets.items():
        if name == "_virtualenv.py":
            put(site + "/_virtualenv.py", raw, "uv0.9.9 exact _virtualenv.py asset")
        else:
            text = raw.decode().replace("{{ VIRTUAL_ENV_DIR }}", str(venv).replace("'", "'\"'\"'"))
            text = text.replace("{{ BIN_NAME }}", "bin").replace("{{ VIRTUAL_PROMPT }}", prompt)
            text = text.replace("{{ PATH_SEP }}", ":").replace("{{ RELATIVE_SITE_PACKAGES }}", "../" + site)
            put("bin/" + name, text.encode(), "uv0.9.9 exact activation template/substitution")
        proof["uv_seed_files"] += 1
    put(site + "/_virtualenv.pth", b"import _virtualenv", "uv0.9.9 literal virtualenv.rs")
    for audit in closure["audits"].values():
        archive_path = closure["wheels"] / audit["filename"]
        prefix, data = audit["dist_info_dir"], audit["data_dir"]
        source_record = prefix + "/RECORD"
        record_rows = {row[0]: list(row) for row in audit["record_entries"]}
        require(len(record_rows) == len(audit["record_entries"]), "source RECORD duplicate")
        with zipfile.ZipFile(archive_path) as archive:
            entry_path = prefix + "/entry_points.txt"
            scripts = console_entrypoints(archive.read(entry_path)) if entry_path in archive.namelist() else []
            script_names = {row[0] for row in scripts}
            generated = []
            for script_name, module, function in scripts:
                target, raw = "bin/" + script_name, console_bytes(venv, module, function)
                put(target, raw, "uv0.9.9 wrapper from verified " + entry_path)
                generated.append([os.path.relpath(target, site), sha_record(raw), str(len(raw))])
                proof["generated_console_scripts"] += 1
            for member in audit["members"]:
                name = member["path"]
                if member["record_binding"] == "self":
                    require(name == source_record, "wrong source RECORD self binding")
                    continue
                raw = archive.read(name)
                require(sha(raw) == member["sha256"] and len(raw) == member["size"], "wheel changed during expected scope construction")
                target, changed = site + "/" + name, False
                if name.startswith(data + "/"):
                    kind, separator, tail = name[len(data) + 1:].partition("/")
                    require(separator, "wheel data placement missing")
                    relative(tail)
                    if kind in {"purelib", "platlib"}:
                        target = site + "/" + tail
                    elif kind == "data":
                        target = tail
                    elif kind == "headers":
                        target = "include/site/python3.12/" + normalize(audit["name"]) + "/" + tail
                    elif kind == "scripts":
                        require("/" not in tail, "uv data/scripts requires regular immediate files")
                        match_name = re.sub(r"(?:\.exe|-script\.py|\.pya)$", "", tail)
                        if match_name in script_names:
                            proof["skipped_legacy_entrypoint_stubs"].append({"path": name, "sha256": member["sha256"]})
                            continue  # uv retains this original RECORD row, then removes .data.
                        target = "bin/" + tail
                        if raw.startswith(b"#!python"):
                            offset = 8
                            while offset < len(raw) and raw[offset] not in (10, 13):
                                offset += 1
                            raw = (uv_shebang(venv / "bin/python") + "\n").encode() + raw[min(offset + 1, len(raw)):]
                            changed = True
                    else:
                        raise Refusal("unknown uv wheel data directory")
                    require(name in record_rows, "relocated member absent from source RECORD")
                    record_rows[name][0] = os.path.relpath(target, site)
                    if changed:
                        record_rows[name][1:] = [sha_record(raw), str(len(raw))]
                require(target not in {"bin/python", "bin/python3", "bin/python3.12", "lib64"}, "wheel overwrites admitted venv links")
                put(target, raw, "verified publisher wheel " + audit["filename"] + ":" + name)
                proof["source_wheel_members"] += 1
            # Explicit empty wheel directories are installed too, except .data,
            # which uv removes after moving its declared payload files.
            for directory in audit.get("directories", []):
                if directory == data:
                    continue
                if directory.startswith(data + "/"):
                    kind, _separator, tail = directory[len(data) + 1:].partition("/")
                    if kind in {"purelib", "platlib"}:
                        target_dir = site + ("/" + tail if tail else "")
                    elif kind == "data":
                        target_dir = tail
                    elif kind == "headers":
                        target_dir = "include/site/python3.12/" + normalize(audit["name"]) + ("/" + tail if tail else "")
                    elif kind == "scripts":
                        require(not tail, "uv data/scripts cannot contain subdirectories")
                        target_dir = "bin"
                    else:
                        raise Refusal("unknown uv data directory")
                else:
                    target_dir = site + "/" + directory
                if target_dir:
                    expected_dirs.add(target_dir)
                    parents(target_dir)
            rows = list(record_rows.values()) + generated
            require(not any(row[0] in {prefix + "/" + key for key in ("INSTALLER", "REQUESTED", "direct_url.json", "uv_cache.json", "uv_build.json")}
                            for row in generated), "unexpected installer metadata generation")
            output = io.StringIO(newline="")
            writer = csv.writer(output, lineterminator="\n")
            writer.writerows(sorted(rows, key=lambda row: (row[0], row[1], row[2])))
            put(site + "/" + source_record, output.getvalue().encode(), "uv0.9.9 exact sorted RECORD serialization")
            proof["regenerated_record_files"] += 1
    return expected, contents, origins, expected_dirs, proof


def verify_installed_members(venv, inventory, closure, assets, base_python, project_name):
    expected, _contents, origins, expected_dirs, proof = installed_scope(venv, closure, assets, base_python, project_name)
    require(set(inventory) == set(expected), "installed scope has missing or unexpected files/links")
    for name, row in expected.items():
        actual = inventory[name]
        require(all(actual.get(key) == value for key, value in row.items()), "installed source-derived bytes/type differ: " + name)
    directories = {path.relative_to(venv).as_posix() for path in venv.rglob("*") if path.is_dir() and not path.is_symlink()}
    require(directories == expected_dirs, "installed directory scope differs from source-derived scope")
    proof.update(exact_scope_verified=True, installed_paths=len(expected), installed_directories=len(expected_dirs),
                 expected_scope_sha256=sha(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()),
                 source_rule_count=len(set(origins.values())), source_rule_bindings_sha256=sha(json.dumps(origins, sort_keys=True, separators=(",", ":")).encode()),
                 uv_installer_source_authority=INSTALLER_AUTHORITY)
    return proof


def cleanup_successful_venv(venv, out, successful, inventory_sha, qualification_path):
    require(successful is True and isinstance(inventory_sha, str) and SHA.fullmatch(inventory_sha), "successful inventory receipt required before cleanup")
    require(venv.name == "venv" and venv.parent.parent == out / "environments" and not venv.is_symlink(), "only this owned successful venv may be removed")
    no_links(venv.parent)
    require(qualification_path == venv.parent / "qualification-before-cleanup.json", "fixed pre-cleanup receipt path required")
    checkpoint_raw = read(qualification_path)
    checkpoint = parse(checkpoint_raw)
    require(checkpoint.get("state") == "offline-environment-qualified" and checkpoint.get("installed_inventory_sha256") == inventory_sha
            and file_sha(venv.parent / "installed-files.json") == inventory_sha
            and checkpoint.get("installed_metadata_sha256") == file_sha(venv.parent / "installed-metadata.json")
            and checkpoint.get("uv_lock_sha256") == file_sha(venv.parent / "uv.lock"), "durable qualified hash receipts required before cleanup")
    shutil.rmtree(venv)
    require(not venv.exists(), "owned successful venv cleanup incomplete")
    return {"path": str(venv.relative_to(out)), "removed": True, "authority": "this new run's successful owned virtual environment only",
            "installed_inventory_sha256": inventory_sha, "product_tests": "unrun"}


def provision_named(runner, row, closure, probe, record):
    verify_closure(closure)
    command_start = len(runner.report["commands"])
    root = runner.out / "environments" / row["id"]
    root.mkdir()
    venv = root / "venv"
    original = row["pyproject_bytes"].encode() if row["pyproject_bytes"] is not None else observer_project(row)
    (root / "pyproject.toml").write_bytes(original)
    (root / "requirements.in").write_bytes(row["requirements_raw"])
    record.update(state="offline-provision-started", project_inputs_sha256=sha(original),
                  requirements_in_sha256=sha(row["requirements_raw"]), venv=str(venv.relative_to(runner.out)),
                  dependency_set_cache=str(closure["cache"].relative_to(runner.out)), product_tests="unrun")
    uv, python = runner.tools["uv"], runner.tools["python"]
    common = ["--python", python, "--no-managed-python", "--no-config", "--no-build", "--offline"]
    project_name = tomllib.loads(original.decode())["project"]["name"]
    require(runner.installer_assets is not None, "bound uv installer source assets required")
    record["qualification_installer_mode"] = {"UV_NO_INSTALLER_METADATA": "1", "UV_COMPILE_BYTECODE": "0", "scope": "qualification only; full product uv mode remains unrun"}
    try:
        # The universal metadata cache belongs only to this exact dependency set.
        # Original product names/groups remain untouched and get their own lock.
        runner.run([uv, "lock", *common], root, closure["cache"], row["id"] + "-offline-original-lock", offline=True, venv=venv)
        lock_raw = read(root / "uv.lock")
        record["uv_lock_inventory"] = uv_lock_inventory(lock_raw)
        record["uv_lock_sha256"] = sha(lock_raw)
        runner.run([uv, "sync", "--frozen", *common, "--no-index", "--find-links", closure["wheels"], "--link-mode", "copy"],
                   root, closure["cache"], row["id"] + "-offline-frozen-sync", offline=True, venv=venv)
        # No uv command follows installation: uv's interpreter query uses -I -B
        # without -S. The following base-Python probe never starts the venv.
        inventory = installed_inventory(venv, runner.out)
        record["installed_wheel_member_bindings"] = verify_installed_members(venv, inventory, closure, runner.installer_assets, python, project_name)
        raw = runner.run([python, "-I", "-S", "-B", probe, venv / "lib/python3.12/site-packages"],
                         root, closure["cache"], row["id"] + "-offline-no-site-metadata", offline=True)
        observation = parse(raw)
        require(observation.get("python") == PYTHON and observation.get("executable") == str(python)
                and observation.get("site") == str(venv / "lib/python3.12/site-packages")
                and observation.get("site_disabled") is True and observation.get("site_in_sys_path") is False, "no-site base Python probe identity differs")
        require(installed_inventory(venv, runner.out) == inventory, "no-site metadata probe changed installed bytes or scope")
        observed = {}
        for package in observation.get("packages", []):
            name = normalize(package["name"])
            require(name not in observed and name not in FORBIDDEN, "duplicate/forbidden installed package")
            observed[name] = package["version"]
        require(observed == {name: pin["version"] for name, pin in closure["pins"].items()}, "installed distribution closure differs from frozen wheel selection")
        checker = row["requirements"].get("quality_checker")
        if checker is not None:
            require(all(observed.get(name) == version for name, version in checker["tool_versions"].items()), "owning quality tool versions differ")
        require(read(root / "pyproject.toml") == original and read(root / "uv.lock") == lock_raw,
                "offline sync/run changed original requirements or frozen lock")
        dump(root / "installed-metadata.json", observation)
        actual_metadata = sorted(value["sha256"] for name, value in inventory.items()
                                 if name.startswith("lib/python3.12/site-packages/")
                                 and name[len("lib/python3.12/site-packages/"):].count("/") == 1
                                 and name.endswith(".dist-info/METADATA") and value["type"] == "file")
        require(actual_metadata == sorted(row["metadata_sha256"] for row in closure["record"]["wheels"]),
                "installed METADATA bytes differ from the audited wheel closure")
        dump(root / "installed-files.json", inventory)
        inventory_sha = file_sha(root / "installed-files.json")
        record.update(state="offline-environment-qualified", installed_metadata_sha256=file_sha(root / "installed-metadata.json"),
                      installed_inventory_sha256=inventory_sha, installed_entries=len(inventory), exact_installed_versions=observed,
                      frozen_sync_passed=True, frozen_run_passed=False, frozen_run="unrun; full product gate must use its actual declared uv mode",
                      no_site_metadata_probe_passed=True, original_project_unchanged=True,
                      uv_commands_after_sync=0, dependency_check="offline original requirement graph, before installation",
                      quality_checker=checker, quality_checker_execution="unrun; use this product venv in the full product gate")
        record["command_receipts"] = runner.report["commands"][command_start:]
        checkpoint = root / "qualification-before-cleanup.json"
        dump(checkpoint, record)
        record["qualification_before_cleanup_sha256"] = file_sha(checkpoint)
        # Keep the complete inventory and all commands. A later product gate must
        # create its own venv from this frozen wheelhouse and run every product.
        record["cleanup"] = cleanup_successful_venv(venv, runner.out, True, inventory_sha, checkpoint)
        return record
    except BaseException as error:
        record.update(state="interrupted" if isinstance(error, (InterruptedError, KeyboardInterrupt)) else "failed",
                      error=type(error).__name__, reason=str(error) if isinstance(error, (Refusal, InterruptedError)) else "see preserved environment evidence")
        raise
    finally:
        dump(root / "receipt.json", record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-revision", required=True, help="helper Git HEAD")
    parser.add_argument("--candidate-revision", help="recorded compiler candidate; defaults to helper HEAD")
    parser.add_argument("--environments", type=Path, required=True)
    parser.add_argument("--assembly", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    candidate_revision = args.candidate_revision or args.source_revision
    require(os.environ.get("GITHUB_ACTIONS") == "true" and platform.system() == "Linux" and platform.machine() == "x86_64", "GitHub Linux x86_64 execution only")
    os_release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    require(os_release.get("ID", "").strip('"') == "ubuntu" and os_release.get("VERSION_ID", "").strip('"') == "22.04", "qualified Ubuntu22.04 runtime host required")
    repo, out = args.repo.absolute(), args.out.absolute()
    inputs, assembly, runtime = args.environments.absolute(), args.assembly.absolute(), args.runtime.absolute()
    require(Path(__file__).absolute() == repo / SCRIPT, "fixed producer source path required")
    checked_paths(repo, out, inputs, assembly, runtime, Path(os.environ["RUNNER_TEMP"]).resolve(strict=True))
    out.mkdir()
    for name in ("home", "tmp", "logs", "closures", "environments"):
        (out / name).mkdir()
    started = time.monotonic()
    report = {"version": "som.hosted-python-wheel-closure-qualification.v1", "source_revision": args.source_revision,
              "helper_revision": args.source_revision, "candidate_revision": candidate_revision,
              "started_at": now(), "state": "incomplete", "product_runtime_ready": False, "product_acceptance_passed": False,
              "wheel_closure_qualification_complete": False, "all_named_environment_checks_complete": False,
              "product_tests": "unrun", "independent_metadata_license_audit": "unrun", "commands": [], "closures": [],
              "environments": [], "observer_runtime_products": [], "failures": []}
    exit_code, sources, verifier = 0, None, None
    try:
        with cancellation():
            sources = Sources(repo, args.source_revision)
            sources.read(SCRIPT)
            candidate_guard = bound_module(sources, "som_dependency_candidate_for_wheels", CANDIDATE_GUARD)
            report["candidate_admission"] = candidate_guard.verify_candidate_inputs(repo, args.source_revision, candidate_revision, assembly)
            policy = parse(sources.read(POLICY))
            require(policy.get("version") == "som.hosted-python-wheel-policy.v1" and policy.get("python") == PYTHON
                    and policy.get("uv") == "0.9.9" and policy.get("host") == HOST, "reviewed wheel policy differs")
            asset_raw = sources.read(INSTALLER_AUTHORITY)
            assets = installer_assets(asset_raw)
            report["installer_source_authority_sha256"] = sha(asset_raw)
            q = bound_module(sources, "som_python_environment_consumer_for_wheels", CONSUMER)
            rows, old_products, input_record = environment_inputs(q, sources, inputs, assembly, args.source_revision, candidate_revision)
            report.update(input_receipts=input_record, expected_products=671, observer_runtime_products=old_products,
                          expected_named_environments=len(rows))
            reuse, qualification, inventory, tools, native = admitted_runtime(runtime, sources, policy, args.source_revision)
            verifier = lambda: reuse.verify_installed_tree(runtime, inventory, qualification)
            report["native_runtime"] = native
            qualified_uv_bytes = read(tools["uv"], MAX_WHEEL)
            require(sha(qualified_uv_bytes) == native["uv_sha256"] and all(value in qualified_uv_bytes for value in assets.values()),
                    "source-derived uv seed assets differ from the exact qualified uv binary")
            report["uv_seed_asset_bytes_verified_in_qualified_binary"] = True
            del qualified_uv_bytes
            runner = Runner(out, report, tools, native, assets)
            uv_version = runner.run([tools["uv"], "--version"], out, out / "closures", "qualified-uv-version").decode().strip()
            python_version = runner.run([tools["python"], "-I", "-S", "-B", "--version"], out, out / "closures", "qualified-python-version").decode().strip()
            require(re.match(r"uv 0\.9\.9(?:\s|$)", uv_version) is not None and python_version == "Python " + PYTHON, "runtime versions changed")
            probe = out / "probe-installed-metadata.py"
            probe.write_text(PROBE)
            report["probe_sha256"] = file_sha(probe)
            sets, bindings = {}, []
            for row in rows:
                key = dependency_key(row["requirements_raw"], native)
                sets.setdefault(key, row)
                bindings.append({"id": row["id"], "dependency_set": key, "state": "unrun",
                                 **{name: row[name] for name in ("cohort", "product_id", "files_map_sha256") if name in row}})
            report["environments"] = bindings
            resolved = {}
            for key, row in sets.items():
                record = {"id": key, "state": "unrun", "native_identity": native}
                report["closures"].append(record)
                try:
                    resolved[key] = resolve_set(runner, row, key, record)
                except (InterruptedError, KeyboardInterrupt):
                    raise
                except Exception as error:
                    record.update(state="failed", error=type(error).__name__, reason=str(error) if isinstance(error, Refusal) else "see preserved source/command/download evidence")
                    report["failures"].append({"dependency_set": key, "error": record["error"], "reason": record["reason"]})
            report["wheel_closure_qualification_complete"] = len(resolved) == len(sets)
            if report["wheel_closure_qualification_complete"]:
                for row, binding in zip(rows, bindings):
                    try:
                        provision_named(runner, row, resolved[binding["dependency_set"]], probe, binding)
                    except (InterruptedError, KeyboardInterrupt):
                        raise
                    except Exception as error:
                        binding.update(state="failed", error=type(error).__name__, reason=str(error) if isinstance(error, Refusal) else "see preserved failed environment and command evidence")
                        report["failures"].append({"environment": row["id"], "error": binding["error"], "reason": binding["reason"]})
                        break
            else:
                for binding in bindings:
                    binding["state"] = "unrun; one or more dependency closures failed"
            report["all_named_environment_checks_complete"] = all(row["state"] == "offline-environment-qualified" for row in bindings)
            verifier()
            report["native_runtime_verified_after"] = True
            sources.verify()
            require(candidate_guard.verify_candidate_inputs(repo, args.source_revision, candidate_revision, assembly) == report["candidate_admission"],
                    "candidate admission changed during dependency qualification")
            _, _, after_inputs = environment_inputs(q, sources, inputs, assembly, args.source_revision, candidate_revision)
            require(after_inputs == input_record, "original environment/assembly receipt bytes changed")
            for closure in resolved.values():
                verify_closure(closure)
            report["original_input_bytes_verified_after"] = True
            if report["wheel_closure_qualification_complete"] and report["all_named_environment_checks_complete"]:
                report["state"] = "all-environment-closures-qualified; product-and-independent-audit-gates-unrun"
            else:
                exit_code = 1
                report["state"] = "failed; preserved partial qualification evidence"
    except BaseException as error:
        exit_code = 130 if isinstance(error, (InterruptedError, KeyboardInterrupt)) else 1
        report.update(state="interrupted" if exit_code == 130 else "failed", error=type(error).__name__,
                      reason=str(error) if isinstance(error, (Refusal, InterruptedError)) else "see preserved input and command evidence")
    finally:
        if verifier is not None and not report.get("native_runtime_verified_after"):
            try:
                verifier()
                report["native_runtime_verified_after"] = True
            except Exception as error:
                report["native_runtime_verified_after"] = False
                report["native_runtime_after_error"] = type(error).__name__
        report.update(finished_at=now(), elapsed_seconds=time.monotonic() - started,
                      source_bindings=sources.pins if sources is not None else {})
        dump(out / "receipt.json", report)
    print(json.dumps({key: report[key] for key in ("state", "product_runtime_ready", "product_acceptance_passed", "wheel_closure_qualification_complete", "all_named_environment_checks_complete")}))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
