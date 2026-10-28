#!/usr/bin/env python3
"""Prepare the complete frozen Rust registry closure on GitHub. Never product acceptance."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import signal
import sys
import tarfile
import tomllib
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HERE = Path(__file__).absolute().parent
spec = importlib.util.spec_from_file_location("_som_runtime", HERE / "qualify_runtime.py")
q = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = q
spec.loader.exec_module(q)
require, Refusal = q.require, q.Refusal
SCRIPT = "som-rs/scripts/hosted_readiness/product_runtime/provision_cargo.py"
SUPPORT = "som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py"
BASE = "som-rs/contracts/rust-output-v1/"
GOLDENS = "som-rs/scripts/hosted_readiness/inputs/public-golden-hashes-v1.json"
EXPERIMENT = "som-rs/scripts/hosted_readiness/experimental_native_compile.py"
RAW = "som-rs/scripts/hosted_readiness/raw_digest_compat.py"
TS065 = "som-rs/scripts/hosted_readiness/inputs/ts065-baseline-hashes-v1.json"
LOCKS = {
    "phase1": ("reference", "c56ff78ed4a4f6bd0418b3d027cbf8efec2d088e81f407bcbc25d0eceeb85f20", 203,
               "8b708c38686b77e6740171e24c28793c4a40b54a8e9807f8e147a9ef3b44591d"),
    "phase2": ("reference-phase2", "0483374722515e3daa7f8509708abb9c551f1bc1815f9110e6662b893db443ad", 334,
               "d06a474e62ab474465c73f4ad99ddcf5d0e33a750684e3a8cfa83714949cd430"),
}
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
IDENTITIES = {"phase1": "example-v1", "phase2": "services14-0000"}
MAX_BLOB = 16 * 1024 * 1024
MAX_CRATE = 50 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_TREE = 2 * 1024 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def bad(_):
        raise Refusal("nonfinite JSON value")
    return json.loads(raw, object_pairs_hook=q.unique_keys, parse_constant=bad)


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def safe_read(path, maximum=MAX_BLOB):
    q.no_links(path)
    require(path.is_file() and path.stat().st_size <= maximum, "missing, linked, or oversized input")
    return path.read_bytes()


def bind_sources(runner, repo, revision):
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "full candidate revision required")
    require(Path(__file__).absolute() == repo / SCRIPT and Path(q.__file__).absolute() == repo / SUPPORT,
            "fixed provisioning source paths required")
    files = [SCRIPT, SUPPORT, GOLDENS, TS065, EXPERIMENT, RAW, BASE + "toolchain.json"]
    files += [BASE + item[0] + "/" + leaf for item in LOCKS.values() for leaf in ("Cargo.toml", "Cargo.lock")]
    git = Path("/usr/bin/git").resolve(strict=True)
    command = [git, "--no-pager", "--no-replace-objects", "-C", repo]
    report = {"revision": revision, "git_sha256": q.regular(git), "files": {}}
    runner.report["source_bindings"] = report
    require(runner.run_bytes(command + ["rev-parse", "--verify", "HEAD^{commit}"], "source-head", 128).strip() == revision.encode(),
            "candidate HEAD differs")
    require(runner.run_bytes(command + ["rev-parse", "--show-toplevel"], "source-root", 4096).strip() == os.fsencode(repo),
            "repository root differs")
    blobs = {}
    for index, relative in enumerate(files):
        path = repo / relative
        working = safe_read(path)
        raw = runner.run_bytes(command + ["ls-tree", "-z", revision, "--", relative], f"tree-{index}", 4096)
        require(raw.endswith(b"\0") and raw.count(b"\0") == 1, "source path missing from candidate tree")
        header, _, name = raw[:-1].partition(b"\t")
        fields = header.split(b" ")
        require(len(fields) == 3 and fields[0] in {b"100644", b"100755"} and fields[1] == b"blob"
                and name == relative.encode() and re.fullmatch(rb"[0-9a-f]{40}", fields[2]), "source is not a regular Git blob")
        oid = fields[2].decode()
        size = runner.run_bytes(command + ["cat-file", "-s", oid], f"size-{index}", 128).strip()
        require(size.isdigit() and int(size) <= MAX_BLOB, "Git source blob exceeds limit")
        blob = runner.run_bytes(command + ["cat-file", "blob", oid], f"blob-{index}", MAX_BLOB)
        require(len(blob) == int(size) and hashlib.sha1(b"blob " + size + b"\0" + blob).hexdigest() == oid, "Git blob identity differs")
        report["files"][relative] = {"git_blob_oid": oid, "sha256": digest(blob), "working_sha256": digest(working),
                                     "bytes": len(blob), "working_bytes_match": working == blob}
        require(working == blob, "working source differs from candidate Git blob: " + relative)
        blobs[relative] = blob
    return blobs


def load_bound(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def lock_union(blobs):
    union, counts = {}, {}
    for phase, (directory, lock_sha, count, manifest_sha) in LOCKS.items():
        lock = blobs[BASE + directory + "/Cargo.lock"]
        manifest = blobs[BASE + directory + "/Cargo.toml"]
        require(digest(lock) == lock_sha and digest(manifest) == manifest_sha, "frozen Cargo bytes changed")
        data = tomllib.loads(lock.decode())
        require(data.get("version") == 4 and isinstance(data.get("package"), list), "unsupported frozen Cargo lock")
        local, seen = 0, set()
        for package in data["package"]:
            name, version = package.get("name"), package.get("version")
            require(isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name)
                    and isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z.+-]+", version), "unsafe package identity")
            key = (name, version)
            require(key not in seen, "duplicate locked package")
            seen.add(key)
            if "source" not in package:
                require(name == "som_app" and version == "0.1.0" and "checksum" not in package, "unexpected local package")
                local += 1
                continue
            require(package["source"] == REGISTRY and isinstance(package.get("checksum"), str)
                    and q.SHA.fullmatch(package["checksum"]), "non-registry or unpinned dependency")
            record = (name, version, REGISTRY, package["checksum"])
            require(key not in union or union[key] == record, "frozen locks disagree on registry package")
            union[key] = record
        counts[phase] = len(seen) - local
        require(local == 1 and counts[phase] == count, "frozen lock package count differs")
    require(len(union) == 352, "full 352-package registry union required")
    return dict(sorted(union.items())), counts


def tree_files(root):
    q.no_links(root)
    require(root.is_dir(), "product or vendor input directory missing")
    result, total = {}, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            q.no_links(path)
            require(path.is_dir() or path.is_file(), "special tree member refused")
        for name in names:
            path = Path(directory) / name
            relative = q.clean_path(path.relative_to(root).as_posix())
            total += path.stat().st_size
            require(total <= MAX_TREE and len(result) < 100000, "tree exceeds inventory limit")
            result[relative] = path
    return dict(sorted(result.items()))


def checked_receipt(path, revision):
    raw = safe_read(path)
    value = parse(raw)
    require(isinstance(value, dict) and value.get("tested_sha") == revision
            and value.get("state") == "experimental-unsealed"
            and value.get("product_acceptance_passed") is False, "receipt candidate or scope differs")
    return value, digest(raw)


def select_products(artifact_root, roots, blobs, revision, canonical_sha, checker):
    """Use real normal-target products only. The complete historical text maps remain authority."""
    assembly, assembly_sha = checked_receipt(artifact_root / "assembly/manifest.json", revision)
    compile_receipt, compile_sha = checked_receipt(artifact_root / "manifest.json", revision)
    require(assembly.get("version") == "som.native-assembly-experiment.v1", "assembly receipt schema differs")
    require(compile_receipt.get("experiment_complete") is True and compile_receipt.get("passed") == 2041
            and compile_receipt.get("identity_stability_passed") is True
            and compile_receipt.get("cleanup", {}).get("cleanup_passed") is True, "whole compile experiment is not complete")
    require(assembly.get("source_pin_record", {}).get("commit") == revision
            and compile_receipt.get("independent_source_record", {}).get("commit") == revision, "source receipt commit differs")
    require(assembly.get("input_receipts", {}).get("goldens_raw_sha256") == digest(blobs[GOLDENS])
            and assembly.get("input_receipts", {}).get("ts065_raw_sha256") == digest(blobs[TS065]), "frozen input receipt differs")
    baseline = parse(blobs[TS065])
    expected_compile = [("python", row["source"], row["id"], row["variant"]) for row in baseline["baselines"]["python"]["rows"]]
    expected_compile += [("rust", row["phase"], row["id"]) for row in baseline["baselines"]["rust"]["rows"] if row["check"] == "compile_equal"]
    actual_compile = [(row["target"], *(row["identity"][key] for key in (("source", "id", "variant") if row["target"] == "python" else ("phase", "id"))))
                      for row in compile_receipt["rows"]]
    require(len(expected_compile) == 2041 and actual_compile == expected_compile
            and all(row.get("state") == "passed" and row.get("index") == index for index, row in enumerate(compile_receipt["rows"])),
            "whole compile receipt scope or order differs")
    products = assembly.get("products", {}).get("rust")
    require(isinstance(products, list) and len(products) == 697
            and len({(row.get("phase"), row.get("id")) for row in products}) == 697
            and sum(row.get("phase") == "phase1" for row in products) == 115
            and sum(row.get("phase") == "phase2" for row in products) == 582, "complete Rust assembly inventory required")
    goldens = parse(blobs[GOLDENS])
    require(goldens.get("version") == "som.public-golden-hashes.v1", "frozen golden schema differs")
    expected_products = [(phase, item["id"]) for phase in ("phase1", "phase2")
                         for item in goldens["cohorts"]["rust_" + phase] if item.get("files_map_sha256") is not None]
    require(len(expected_products) == 697 and [(row["phase"], row["id"]) for row in products] == expected_products,
            "whole Rust assembly named inventory or order differs")
    selected = {}
    for phase, identity in IDENTITIES.items():
        rows = [row for row in products if row["phase"] == phase and row["id"] == identity]
        require(len(rows) == 1, "named reference product missing")
        row = rows[0]
        require(row.get("state") == "passed" and row.get("rust_assembly_mode") == "normal-target-plan", "reference assembly is not passed normal target plan")
        locator = q.clean_path(row["product_dir"])
        expected_root = artifact_root / locator
        require(roots[phase] == expected_root, "explicit product root differs from assembly receipt")
        actual = tree_files(expected_root)
        require("src/main.rs" in actual and "Cargo.toml" in actual and "Cargo.lock" in actual, "real generated product target missing")
        text = {name: safe_read(path, 32 * 1024 * 1024).decode("utf-8") for name, path in actual.items()}
        golden = [item for item in goldens["cohorts"]["rust_" + phase] if item["id"] == identity]
        require(len(golden) == 1, "named frozen full file map missing")
        expected = golden[0]["files_map_sha256"]
        require(q.SHA.fullmatch(expected) and canonical_sha(text) == expected
                and row.get("expected_files_map_sha256") == expected and row.get("actual_files_map_sha256") == expected,
                "complete generated text file map differs from frozen authority")
        files = [{"path": name, "sha256": digest(text[name].encode()), "size": len(text[name].encode())} for name in actual]
        require(sorted(row["files"], key=lambda item: item["path"]) == files, "assembly per-file inventory differs")
        directory = LOCKS[phase][0]
        for leaf in ("Cargo.toml", "Cargo.lock"):
            require(actual[leaf].read_bytes() == blobs[BASE + directory + "/" + leaf], "generated Cargo bytes differ from frozen reference")
        request_path = artifact_root / q.clean_path(row["request_path"])
        response_path = artifact_root / q.clean_path(row["response_path"])
        request_raw, response_raw = safe_read(request_path), safe_read(response_path)
        require(digest(request_raw) == row["request_sha256"] and digest(response_raw) == row["response_sha256"], "assembly raw receipt differs")
        request, response = parse(request_raw), parse(response_raw)
        require(response.get("status") == "ok" and response.get("files") == text, "assembly response does not bind complete product files")
        compile_rows = [item for item in compile_receipt["rows"] if item.get("target") == "rust"
                        and item.get("identity") == {"phase": phase, "id": identity}]
        require(len(compile_rows) == 1 and compile_rows[0].get("state") == "passed" and compile_rows[0].get("http_status") == 200, "named source compile receipt missing")
        compile_row = compile_rows[0]
        index = compile_row["index"]
        require(type(index) is int and 0 <= index < 2041, "compile row index differs")
        compile_raw = safe_read(artifact_root / "rows" / f"{index:04d}" / "response.raw")
        check_raw = safe_read(artifact_root / "rows" / f"{index:04d}" / "checker.json")
        check = parse(check_raw)
        require(digest(compile_raw) == compile_row["current_response_sha256"] == check.get("current_raw_sha256")
                and check.get("byte_compatibility_passed") is True and check.get("product_acceptance") is False
                and check.get("http_status") == 200 and check.get("target") == "rust"
                and check.get("identity") == {"phase": phase, "id": identity}
                and check.get("source_pin_record", {}).get("commit") == revision, "source compile proof differs")
        verified = checker.verify_response("rust", {"phase": phase, "id": identity}, 200, compile_raw)
        require(verified.report.get("byte_compatibility_passed") is True
                and verified.report.get("current_raw_sha256") == digest(compile_raw), "independent raw compile QA differs")
        compile_body = parse(compile_raw)
        require(request == {"bundle": compile_body.get("bundle")}, "assembly request differs from current compiled bundle")
        bundle = request["bundle"]
        require(bundle.get("target") == "rust" and canonical_sha({key: value for key, value in bundle.items() if key != "hashes"})
                == bundle["hashes"]["payload_sha256"], "compiled bundle canonical payload differs")
        selected[phase] = {"id": identity, "root": expected_root, "files": files, "source_files": actual,
                           "files_map_sha256": expected, "compile_response_sha256": digest(compile_raw),
                           "compile_checker_sha256": digest(check_raw), "independent_compile_report": verified.report,
                           "assemble_request_sha256": digest(request_raw),
                           "assemble_response_sha256": digest(response_raw)}
    return selected, {"assembly_manifest_sha256": assembly_sha, "compile_manifest_sha256": compile_sha,
                      "assembly_source_pin_record_sha256": canonical_sha(assembly["source_pin_record"]),
                      "compile_source_pin_record_sha256": canonical_sha(compile_receipt["independent_source_record"])}


def public_url(value):
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname in {"crates.io", "static.crates.io"}
            and not parsed.username and not parsed.password and parsed.port is None, "nonpublic crate URL refused")
    return value


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        public_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def fetch_crate(record, path):
    name, version, _, checksum = record
    url = f"https://crates.io/api/v1/crates/{name}/{version}/download"
    total, sha = 0, hashlib.sha256()
    with build_opener(Redirect()).open(Request(public_url(url), headers={"User-Agent": "SOM-Cargo-closure/1"}), timeout=45) as response, path.open("xb") as output:
        public_url(response.geturl())
        while block := response.read(2 * 1024 * 1024):
            total += len(block)
            require(total <= MAX_CRATE, "crate download exceeds limit")
            sha.update(block)
            output.write(block)
    require(sha.hexdigest() == checksum, "crate archive differs from Cargo.lock checksum")
    return total


def inspect_crate(record, path, out):
    name, version, _, checksum = record
    require(q.sha(path) == checksum and path.stat().st_size <= MAX_CRATE, "crate changed before archive inspection")
    prefix = name + "-" + version
    files, notices, total = {}, {}, 0
    with q.checked_tar(path, {"name": prefix}, out) as (archive, expanded):
        entries = []
        for member in archive:
            member_name = q.clean_path(member.name)
            require(member_name == prefix or member_name.startswith(prefix + "/"), "crate member outside package root")
            require(member.type in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}, "crate links and special entries refused")
            require(0 <= member.size <= q.MAX_MEMBER and len(entries) < q.MAX_MEMBERS, "crate member exceeds limit")
            total += member.size
            require(total <= q.MAX_EXPANDED, "crate expanded size exceeds limit")
            entries.append((member_name, member))
        require(len({name for name, _ in entries}) == len(entries), "duplicate crate archive member")
        raw = archive.fileobj
        raw.seek(archive.offset)
        while raw.tell() < raw.size:
            require(not any(raw.read(min(2 * 1024 * 1024, raw.size - raw.tell()))), "crate tar trailing data")
        bodies = {}
        for member_name, member in entries:
            if member.isdir():
                continue
            relative = member_name[len(prefix) + 1:]
            require(relative, "crate source root is a file")
            stream = archive.extractfile(member)
            require(stream is not None, "unreadable crate member")
            with stream:
                body = stream.read(q.MAX_MEMBER + 1)
            require(len(body) == member.size, "truncated crate member")
            files[relative] = digest(body)
            if relative == "Cargo.toml":
                bodies[relative] = body
            first = Path(relative).name.lower()
            if first.startswith(("license", "licence", "copying", "copyright", "notice", "unlicense")):
                notices[relative] = body
        require("Cargo.toml" in bodies, "crate manifest missing")
        package = tomllib.loads(bodies["Cargo.toml"].decode()).get("package", {})
        require(package.get("name") == name and package.get("version") == version, "published package identity differs")
        license_file = package.get("license-file")
        if license_file is not None:
            license_file = q.clean_path(license_file)
            require(license_file in files, "declared license file missing")
            if license_file not in notices:
                member = next(member for member_name, member in entries if member_name == prefix + "/" + license_file)
                with archive.extractfile(member) as stream:
                    notices[license_file] = stream.read(q.MAX_MEMBER + 1)
        for relative, body in notices.items():
            q.write_new(out / "licenses" / prefix / relative, body)
    return {"archive_sha256": checksum, "expanded_tar": expanded, "files": files,
            "license_expression": package.get("license"), "license_files": {name: digest(body) for name, body in notices.items()}}


def cargo_omission(relative):
    # Cargo's own source copy omits these metadata and patch backup files.
    return relative in {".gitattributes", ".gitignore", ".git", ".cargo-ok"} or relative.endswith((".orig", ".rej"))


def verify_vendor(vendor, locked, published, changes):
    require(set(path.name for path in vendor.iterdir()) == {name + "-" + version for name, version in locked}, "vendor is not the exact 352-package set")
    rows = []
    for key, record in locked.items():
        name, version, source, checksum = record
        directory = vendor / (name + "-" + version)
        paths = tree_files(directory)
        require(".cargo-checksum.json" in paths, "Cargo-generated checksum file missing")
        raw = safe_read(paths[".cargo-checksum.json"])
        data = parse(raw)
        actual = {relative: q.sha(path) for relative, path in paths.items() if relative != ".cargo-checksum.json"}
        require(set(data) == {"files", "package"} and data["package"] == checksum and data["files"] == actual, "full Cargo checksum metadata differs")
        origin = published[key]["files"]
        require(set(actual) <= set(origin), "vendor contains files absent from publisher archive")
        differences = {relative: {"archive": origin[relative], "vendor": value}
                       for relative, value in actual.items() if origin[relative] != value}
        omitted = sorted(set(origin) - set(actual))
        changes[name + "-" + version] = {"changed": differences, "publisher_files_not_vendored": omitted}
        require(all(cargo_omission(relative) for relative in omitted), "Cargo omitted publisher source files: " + name + "-" + version)
        require(not differences, "Cargo transformed publisher bytes; independent review required: " + name + "-" + version + " " + ",".join(sorted(differences)))
        require("Cargo.toml" in actual and actual, "vendor package is incomplete")
        rows.append({"name": name, "version": version, "source": source, "checksum": checksum,
                     "files": actual, "cargo_checksum_sha256": digest(raw),
                     "publisher_files_not_vendored": sorted(set(origin) - set(actual)),
                     "licenses": published[key]["license_files"]})
    return rows


def stage_products(selected, out):
    roots = {}
    for phase, item in selected.items():
        target = out / "projects" / phase
        expected = {row["path"]: row for row in item["files"]}
        for relative, source in item["source_files"].items():
            body = safe_read(source, 32 * 1024 * 1024)
            require(digest(body) == expected[relative]["sha256"] and len(body) == expected[relative]["size"], "product changed before staging")
            q.write_new(target / relative, body)
        roots[phase] = target
    return roots


def reject_cargo_configs(root):
    for parent in (root, *root.parents):
        for name in ("config", "config.toml"):
            require(not (parent / ".cargo" / name).exists() and not (parent / ".cargo" / name).is_symlink(),
                    "inherited Cargo configuration refused")


def check_project_bytes(roots, blobs):
    for phase, root in roots.items():
        for leaf in ("Cargo.toml", "Cargo.lock"):
            require(safe_read(root / leaf) == blobs[BASE + LOCKS[phase][0] + "/" + leaf], "Cargo changed frozen project bytes")


def package_candidate(out):
    destination = out / "cargo-closure.tar.gz"
    q.no_links(destination)
    members = {}
    for top in ("vendor", "licenses", "downloads"):
        for relative, path in tree_files(out / top).items():
            members[top + "/" + relative] = path
    members["cargo-home/config.toml"] = out / "cargo-home/config.toml"
    with tarfile.open(destination, "x:gz") as archive:
        for relative, path in sorted(members.items()):
            q.clean_path(relative)
            q.no_links(path)
            require(path.is_file(), "candidate archive member is not regular")
            header = tarfile.TarInfo(relative)
            header.size, header.mode = path.stat().st_size, path.stat().st_mode & 0o777
            with path.open("rb") as stream:
                archive.addfile(header, stream)
    return q.sha(destination)


@contextmanager
def fixed_git_environment(environment):
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(environment)
    os.environ.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_NO_REPLACE_OBJECTS="1")
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def provision(args):
    require(os.environ.get("GITHUB_ACTIONS") == "true" and platform.system() == "Linux"
            and platform.machine().lower() == "x86_64", "GitHub Linux x86_64 execution only")
    repo, out = args.repo.resolve(strict=True), args.out
    q.checked_output(out, repo)
    for path in (args.assembly_root, args.phase1_product_root, args.phase2_product_root):
        temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
        require(path.is_absolute() and ".." not in path.parts and temporary in path.parents
                and path != out and path not in out.parents, "absolute consumer inputs in RUNNER_TEMP outside output required")
        q.no_links(path)
        require(path.is_dir(), "same-candidate assembly consumer inputs are missing")
    out.mkdir(mode=0o700)
    os.chdir(out)
    report = {"version": "som.product-cargo-closure.v1", "tested_sha": args.source_revision,
              "status": "failed", "product_acceptance_passed": False, "product_builds": "unrun",
              "services": "not_started", "toolchain_binary_authority": "observed_hashes_not_independently_admitted",
              "bootstrap_python": {"path": str(Path(sys.executable).resolve()), "version": sys.version,
                                   "sha256": q.regular(Path(sys.executable).resolve()), "role": "metadata_bootstrap_only"},
              "runner": {key: os.environ.get(key) for key in ("RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")},
              "commands": [], "packages": []}
    try:
        runner = q.Runner(out, report)
        blobs = bind_sources(runner, repo, args.source_revision)
        locked, counts = lock_union(blobs)
        report["lock_counts"], report["registry_union_count"] = counts, len(locked)
        load_bound("raw_digest_compat", repo / RAW)
        experiment = load_bound("_som_cargo_experiment", repo / EXPERIMENT)
        with fixed_git_environment(runner.environment):
            checker = experiment.ExperimentalChecker(repo)
            require(checker.repo.commit == args.source_revision, "independent checker source revision differs")
            selected, inputs = select_products(args.assembly_root, {"phase1": args.phase1_product_root, "phase2": args.phase2_product_root},
                                               blobs, args.source_revision, experiment.value_sha, checker)
        report["input_receipts"] = inputs
        report["reference_products"] = {phase: {key: value for key, value in item.items() if key not in {"source_files", "root"}}
                                        for phase, item in selected.items()}
        roots = stage_products(selected, out)
        for root in roots.values():
            reject_cargo_configs(root)
        cargo, rustc = args.cargo, args.rustc
        report["toolchain"] = {}
        for name, path in (("cargo", cargo), ("rustc", rustc)):
            require(path.is_absolute(), "absolute native tool path required")
            before = q.regular(path)
            with path.open("rb") as stream:
                require(stream.read(4) == b"\x7fELF" and path.stat().st_mode & 0o111, "native ELF tool required")
            version = runner.run([path, "-vV"], "version-" + name)
            require(re.search(rf"^{name} 1\.96\.1(?:\s|$)", version) is not None, "exact Rust/Cargo 1.96.1 required")
            require("host: x86_64-unknown-linux-gnu" in version, "GNU Linux x86_64 toolchain required")
            if name == "rustc":
                commit = parse(blobs[BASE + "toolchain.json"])["rust"]["rustc"]["commit_hash"]
                require("commit-hash: " + commit in version, "frozen rustc commit differs")
            require(q.regular(path) == before, "tool changed during version command")
            report["toolchain"][name] = {"path": str(path), "sha256": before, "version_stdout": version}
        runner.environment.update(CARGO_HOME=str(out / "fetch-home"), CARGO_TARGET_DIR=str(out / "target"),
                                  RUSTC=str(rustc), CARGO_NET_OFFLINE="false", CARGO_REGISTRIES_CRATES_IO_PROTOCOL="sparse",
                                  CARGO_BUILD_JOBS="2")
        (out / "downloads").mkdir()
        published, total = {}, 0
        report["published_inventory"] = {}
        for key, record in locked.items():
            path = out / "downloads" / (record[0] + "-" + record[1] + ".crate")
            report["current_package"] = record[0] + "-" + record[1]
            total += fetch_crate(record, path)
            report["registry_download_bytes"] = total
            require(total <= MAX_TOTAL, "total crate downloads exceed limit")
            published[key] = inspect_crate(record, path, out)
            report["published_inventory"][report["current_package"]] = published[key]
        report["current_package"] = None
        vendor = out / "vendor"
        runner.run([cargo, "vendor", "--locked", "--versioned-dirs", "--manifest-path", roots["phase1"] / "Cargo.toml",
                    "--sync", roots["phase2"] / "Cargo.toml", vendor], "cargo-vendor", timeout=1200)
        config = Path(report["commands"][-1]["stdout"]).read_bytes()
        parsed = tomllib.loads(config.decode())
        require(parsed == {"source": {"crates-io": {"replace-with": "vendored-sources"},
                                     "vendored-sources": {"directory": str(vendor)}}}, "Cargo vendor emitted unexpected source configuration")
        report["vendor_differences"] = {}
        report["packages"] = verify_vendor(vendor, locked, published, report["vendor_differences"])
        cache = out / "fetch-home/registry/cache"
        cached = [path for path in cache.rglob("*.crate")] if cache.exists() else []
        require(len(cached) == 352, "Cargo cache did not retain the full registry archive closure")
        expected = {name + "-" + version + ".crate": record[3] for (name, version), record in locked.items()}
        require({path.name: q.regular(path) for path in cached} == expected, "Cargo fetched archive set differs from frozen checksums")
        home = out / "cargo-home"
        home.mkdir()
        q.write_new(home / "config.toml", config + b"\n[net]\noffline = true\n")
        runner.environment.update(CARGO_HOME=str(home), CARGO_NET_OFFLINE="true")
        for phase, root in roots.items():
            runner.run([cargo, "metadata", "--locked", "--offline", "--format-version", "1", "--manifest-path", root / "Cargo.toml"],
                       "offline-metadata-" + phase, timeout=120)
        check_project_bytes(roots, blobs)
        for phase, root in roots.items():
            actual = {name: q.sha(path) for name, path in tree_files(root).items()}
            require(actual == {row["path"]: row["sha256"] for row in selected[phase]["files"]}, "staged product source changed")
        report.update(status="registry_closure_verified_product_gates_unrun", cargo_config_sha256=q.sha(home / "config.toml"),
                      vendor_package_count=352, product_builds="unrun",
                      candidate_archive_sha256=package_candidate(out))
        return report
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        q.no_links(out / "receipt.json")
        (out / "receipt.json").write_bytes(json_bytes(report))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cargo", type=Path, required=True)
    parser.add_argument("--rustc", type=Path, required=True)
    parser.add_argument("--assembly-root", type=Path, required=True)
    parser.add_argument("--phase1-product-root", type=Path, required=True)
    parser.add_argument("--phase2-product-root", type=Path, required=True)
    args = parser.parse_args(argv)
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, q.interrupted)
    try:
        result = provision(args)
        print(json.dumps({"status": result["status"], "receipt": str(args.out / "receipt.json"), "product_acceptance_passed": False}))
        return 0
    except BaseException as error:
        if q.LIVE is not None:
            q.stop_owned(q.LIVE)
        print(f"Cargo closure failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
