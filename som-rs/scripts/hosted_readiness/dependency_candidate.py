#!/usr/bin/env python3
"""Admit one reviewed compiler artifact for dependency helper iteration only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.request import HTTPRedirectHandler, Request, build_opener

SELF = "som-rs/scripts/hosted_readiness/dependency_candidate.py"
RECIPE = "som-rs/scripts/hosted_readiness/inputs/dependency-iteration-f59-v1.json"
RECIPE_SHA256 = "cc2611be2ccaab1d6a71887903cc3a69b7e2550d48e7bf8ae6785d725325339f"
CANDIDATE = "f59e28d910ff4adb16e3becfdc82bd2978ca84fa"
PROTECTED_PREFIXES = ("som-rs/src/", "som-rs/som-targets/src/", "som-rs/contracts/", "som-rs/examples/",
                      "som-rs/.cargo/", "som-rs/som-targets/.cargo/", ".cargo/",
                      "som-core/src/", "som-core/data/", "som-core/block_templates/",
                      "som-code-python/data/blocks/", "som-code-python/data/block-contracts/")
PROTECTED_EXACT = frozenset(("Cargo.toml", "Cargo.lock", "rust-toolchain", "rust-toolchain.toml", ".gitmodules",
                             "som-rs/Cargo.toml", "som-rs/Cargo.lock", "som-rs/build.rs",
                             "som-rs/rust-toolchain", "som-rs/rust-toolchain.toml",
                             "som-rs/som-targets/Cargo.toml", "som-rs/som-targets/Cargo.lock",
                             "som-rs/som-targets/build.rs", "som-core/pyproject.toml", "som-core/uv.lock"))
HISTORICAL_ALIASES = {"som-code-python/data/blocks/current": 'flow-v7-compact'}
UNREFERENCED_PREPARATION_FILES = frozenset(("som-rs/contracts/native-tools-v1/product-rust-tools-v1.json",))
MAX_FILE, MAX_TOTAL, MAX_FILES = 512 * 1024 * 1024, 4 * 1024 * 1024 * 1024, 100000


class Refusal(ValueError):
    pass


class SourceChanged(Refusal):
    """A real candidate input change requires a new full candidate run."""


def require(ok, reason):
    if not ok:
        raise Refusal(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_sha(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate metadata key")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refusal("nonfinite metadata")))


def relative(name):
    require(isinstance(name, str) and name and not name.startswith("/") and "\\" not in name
            and all(part not in {"", ".", ".."} for part in name.split("/"))
            and not any(ord(c) < 32 or ord(c) == 127 for c in name), "safe relative path required")
    return name


def read(path, maximum=MAX_FILE):
    path = Path(path).absolute()
    require(".." not in path.parts, "path traversal refused")
    for part in (path, *path.parents):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode), "linked path refused")
        require(stat.S_ISREG(info.st_mode) if part == path else stat.S_ISDIR(info.st_mode), "regular path required")
        if part == path:
            require(info.st_nlink == 1 and info.st_size <= maximum, "linked or oversized file refused")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum, "file changed type or size")
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, "file exceeds byte bound")
    return raw


class GitSource:
    def __init__(self, root, helper_revision):
        self.root, self.revision, self.cache = Path(root).absolute(), helper_revision, {}
        require(re.fullmatch(r"[a-f0-9]{40}", helper_revision) is not None, "exact helper revision required")
        require(self.git("rev-parse", "--verify", "HEAD^{commit}").decode().strip() == helper_revision, "helper HEAD differs")
        require(self.git("rev-parse", "--show-toplevel").strip() == os.fsencode(self.root), "helper repository root differs")
        require(Path(__file__).absolute() == self.root / SELF, "fixed source guard path required")
        self.blob(helper_revision, SELF, working=True)

    def git(self, *args):
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        try:
            raw = subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-C", str(self.root), *args],
                                          env=env, stderr=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            raise Refusal("required immutable Git object is unavailable") from error
        require(len(raw) <= 64 * 1024 * 1024, "Git metadata output exceeds bound")
        return raw

    def blob(self, revision, name, working=False):
        relative(name)
        key = revision, name
        if key not in self.cache:
            tree = self.git("ls-tree", "-z", revision, "--", name)
            require(tree.endswith(b"\0") and tree.count(b"\0") == 1, "source Git entry is absent or ambiguous")
            header, actual = tree[:-1].split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            require(mode in {"100644", "100755"} and kind == "blob" and actual.decode() == name, "regular Git source blob required")
            size = self.git("cat-file", "-s", oid).decode().strip()
            require(size.isdecimal() and int(size) <= MAX_FILE, "Git source blob exceeds bound")
            raw = self.git("cat-file", "blob", oid)
            require(len(raw) == int(size) and hashlib.sha1(b"blob " + size.encode() + b"\0" + raw).hexdigest() == oid,
                    "Git blob bytes differ")
            self.cache[key] = raw
        raw = self.cache[key]
        if working:
            require(read(self.root / name) == raw, "working source differs from helper Git blob")
        return raw

    def tree(self, revision):
        result = {}
        for entry in self.git("ls-tree", "-r", "-z", "--full-tree", revision).split(b"\0"):
            if not entry:
                continue
            header, raw_name = entry.split(b"\t", 1)
            name = relative(raw_name.decode("utf-8"))
            mode, kind, oid = header.decode().split()
            if (name in PROTECTED_EXACT or name.startswith(PROTECTED_PREFIXES)) and name not in UNREFERENCED_PREPARATION_FILES:
                if kind != "blob" or (mode not in {"100644", "100755"} and not (mode == "120000" and name in HISTORICAL_ALIASES)):
                    raise SourceChanged("protected build input is not a regular Git blob: " + name)
                result[name] = {"mode": mode, "oid": oid}
        require(result and len(result) <= MAX_FILES, "complete protected build source tree required")
        return result


def recipe(source):
    raw = source.blob(source.revision, RECIPE, working=True)
    require(sha(raw) == RECIPE_SHA256, "fixed reviewed reuse recipe bytes differ")
    value = parse(raw)
    require(value.get("version") == "som.dependency-preparation-candidate-reuse.v1"
            and value.get("candidate_revision") == CANDIDATE and value.get("repository") == "faberline/som"
            and value.get("product_acceptance_passed") is False, "reuse recipe role differs")
    require(len(value.get("factory_source_pins", {})) == 139 and len(value.get("compile_assembly_source_pins", {})) == 168,
            "complete recorded source vectors required")
    audit = value["approved_byte_audit"]
    audit_raw = source.blob(source.revision, audit["path"], working=True)
    require(sha(audit_raw) == audit["sha256"], "independent approved byte audit changed")
    checked = parse(audit_raw)
    require(checked.get("audit_passed") is True and checked.get("verdict") == audit["verdict"]
            and checked.get("tested_sha") == CANDIDATE and checked.get("artifact_id") == value["artifact"]["artifact_id"]
            and checked.get("artifact_github_digest") == value["artifact"]["github_zip_digest"]
            and checked.get("product_acceptance_passed") is False, "bounded independent audit authority differs")
    for saved, wanted in (("compile_manifest_sha256", "compile_manifest_sha256"),
                          ("assembly_manifest_sha256", "assembly_manifest_sha256"),
                          ("complete_relative_file_hash_inventory_sha256", "complete_file_inventory_canonical_sha256")):
        require(checked["artifact_bindings"][saved] == value["artifact"][wanted], "audit and recipe artifact pins differ")
    return value


def same_sources(source, value):
    before, current = source.tree(CANDIDATE), source.tree(source.revision)
    if before != current:
        changed = sorted(name for name in before.keys() | current.keys() if before.get(name) != current.get(name))
        raise SourceChanged("candidate build input changed; run a full candidate: " + ", ".join(changed[:8]))
    total = 0
    for name, item in current.items():
        if item["mode"] == "120000":
            path = source.root / name
            require(name in HISTORICAL_ALIASES and path.is_symlink(), "recorded historical alias required")
            target = os.readlink(path)
            if target != HISTORICAL_ALIASES[name]:
                raise SourceChanged("historical source alias changed: " + name)
            require(target and "/" not in target and "\\" not in target and target not in {".", ".."}, "historical alias target is unsafe")
            resolved = path.resolve(strict=True)
            expected_parent = path.parent.resolve(strict=True)
            require(resolved.parent == expected_parent and resolved.is_dir()
                    and all(not parent.is_symlink() for parent in (path.parent, *path.parent.parents)), "historical alias escapes its protected tree")
            raw = target.encode("utf-8")
        else:
            raw = read(source.root / name)
        total += len(raw)
        require(total <= MAX_TOTAL, "protected working source exceeds bound")
        oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if oid != item["oid"]:
            raise SourceChanged("protected working source changed: " + name)
    merged, helper_changes = {}, {}
    for table in (value["factory_source_pins"], value["compile_assembly_source_pins"]):
        for name, expected in table.items():
            relative(name)
            require(re.fullmatch(r"[a-f0-9]{64}", expected) is not None
                    and (name not in merged or merged[name] == expected), "recorded source maps disagree")
            before_raw = source.blob(CANDIDATE, name)
            current_raw = source.blob(source.revision, name, working=True)
            require(sha(before_raw) == expected, "original candidate source evidence differs")
            if before_raw != current_raw:
                allowed = value.get("admitted_dependency_helper_changes", {}).get(name, {}).get("allowed_helper_sha256", [])
                if name in value["factory_source_pins"] or sha(current_raw) not in allowed:
                    raise SourceChanged("recorded factory/input source changed: " + name)
                helper_changes[name] = {"candidate_sha256": expected, "helper_sha256": sha(current_raw),
                                        "role": "reviewed preparation helper; original candidate evidence preserved"}
            merged[name] = expected
    return {"factory_source_pins": len(value["factory_source_pins"]),
            "compile_assembly_source_pins": len(value["compile_assembly_source_pins"]),
            "separate_helper_source_changes": helper_changes,
            "all_listed_source_pins": len(merged), "protected_tracked_files": len(current),
            "protected_tree_canonical_sha256": canonical_sha(current), "listed_source_map_canonical_sha256": canonical_sha(merged)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Refusal("artifact origin redirects are refused")


def check_origin(data, value):
    expected = value["artifact"]
    require(data.get("id") == expected["artifact_id"] and data.get("name") == expected["name"]
            and data.get("size_in_bytes") == expected["compressed_bytes"] and data.get("digest") == expected["github_zip_digest"]
            and data.get("expired") is False and data.get("expires_at") == expected["expires_at"], "pinned artifact origin or retention differs")
    run = data.get("workflow_run", {})
    require(run.get("id") == expected["run_id"] and run.get("head_sha") == CANDIDATE
            and run.get("repository_id") == value["repository_id"] and run.get("head_repository_id") == value["repository_id"],
            "pinned artifact source/run/repository differs")
    require(datetime.now(timezone.utc) < datetime.fromisoformat(expected["expires_at"].replace("Z", "+00:00")),
            "reviewed artifact retention expired; a new full candidate is required")
    return {"repository": value["repository"], "run_id": expected["run_id"], "artifact_id": expected["artifact_id"],
            "github_zip_digest": expected["github_zip_digest"], "expires_at": expected["expires_at"],
            "zip_digest_locally_recomputed": False}


def artifact_origin(value):
    require(os.environ.get("GITHUB_ACTIONS") == "true", "artifact origin admission runs on GitHub only")
    url = "https://api.github.com/repos/faberline/som/actions/artifacts/" + str(value["artifact"]["artifact_id"])
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "som-dependency-candidate-admission"}
    token = os.environ.get("SOM_DEPENDENCY_ACTIONS_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=30) as response:
            require(response.status == 200, "artifact origin HTTP status differs")
            raw = response.read(1024 * 1024 + 1)
            require(len(raw) <= 1024 * 1024, "artifact origin metadata exceeds bound")
    except Refusal:
        raise
    except Exception as error:
        raise Refusal("pinned GitHub artifact metadata is unavailable") from error
    return check_origin(parse(raw), value)


def artifact_inventory(root, value):
    root = Path(root).absolute()
    require(root.is_dir() and not root.is_symlink(), "complete compiler artifact root required")
    files, total = {}, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs:
            info = (Path(directory) / name).lstat()
            require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), "artifact directory is linked or special")
        for name in names:
            path = Path(directory) / name
            relative_name = relative(path.relative_to(root).as_posix())
            body = read(path, 128 * 1024 * 1024)
            total += len(body)
            require(total <= MAX_TOTAL and len(files) < MAX_FILES, "compiler artifact exceeds inventory bounds")
            files[relative_name] = sha(body)
    expected = value["artifact"]
    require(len(files) == expected["complete_file_count"]
            and canonical_sha(files) == expected["complete_file_inventory_canonical_sha256"], "complete reviewed compiler artifact bytes differ")
    require(files.get("manifest.json") == expected["compile_manifest_sha256"]
            and files.get("assembly/manifest.json") == expected["assembly_manifest_sha256"], "recorded compile/assembly manifests differ")
    compile_record, assembly = parse(read(root / "manifest.json")), parse(read(root / "assembly/manifest.json"))
    require(compile_record.get("tested_sha") == assembly.get("tested_sha") == CANDIDATE
            and compile_record.get("experiment_complete") is True and assembly.get("assembly_experiment_complete") is True
            and compile_record.get("product_acceptance_passed") is assembly.get("product_acceptance_passed") is False,
            "recorded compiler artifact identity or bounded role differs")
    require(compile_record["initial_identities"]["binary"]["sha256"] == value["candidate_binary_saved_sha256"]
            and compile_record["asset_manifest_canonical_sha256"] == value["asset_manifest_canonical_sha256"], "saved candidate identities differ")
    return {"compile_manifest_sha256": files["manifest.json"], "assembly_manifest_sha256": files["assembly/manifest.json"],
            "complete_file_count": len(files), "complete_file_inventory_canonical_sha256": canonical_sha(files)}


def verify_candidate_inputs(repo, helper_revision, candidate_revision=None, assembly_root=None):
    """Keep the compiler candidate distinct from the helper that reads its files."""
    candidate_revision = candidate_revision or helper_revision
    require(re.fullmatch(r"[a-f0-9]{40}", candidate_revision) is not None, "exact candidate revision required")
    source = GitSource(repo, helper_revision)
    result = {"mode": "same-candidate", "helper_revision": helper_revision, "candidate_revision": candidate_revision,
              "product_acceptance_passed": False, "recipe": None, "artifact": None, "source_equivalence": None}
    if candidate_revision == helper_revision:
        return result
    require(candidate_revision == CANDIDATE and assembly_root is not None, "only the fixed reviewed f59 artifact may cross helper revisions")
    value = recipe(source)
    equivalent = same_sources(source, value)
    origin = artifact_origin(value)
    payload = artifact_inventory(assembly_root, value)
    result.update(mode="pinned-f59-dependency-reuse", recipe={"path": RECIPE, "sha256": RECIPE_SHA256},
                  artifact={**origin, **payload}, source_equivalence=equivalent,
                  scope="dependency preparation only; compiler identity and prior results remain f59")
    return result


def classify(repo, helper_revision, force_full=False):
    source = GitSource(repo, helper_revision)
    if force_full or helper_revision == CANDIDATE:
        return {"run_full_candidate": "true", "candidate_revision": helper_revision, "helper_revision": helper_revision,
                "mode": "explicit-full-candidate", "product_acceptance_passed": False}
    value = recipe(source)
    try:
        equivalent = same_sources(source, value)
    except SourceChanged as error:
        return {"run_full_candidate": "true", "candidate_revision": helper_revision, "helper_revision": helper_revision,
                "mode": "changed-candidate-inputs", "reason": str(error), "product_acceptance_passed": False}
    origin = artifact_origin(value)
    return {"run_full_candidate": "false", "candidate_revision": CANDIDATE, "helper_revision": helper_revision,
            "mode": "dependency-helper-iteration", "recipe_sha256": RECIPE_SHA256, "source_equivalence": equivalent,
            "artifact_origin": origin, "product_acceptance_passed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("classify", "verify"))
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--helper-revision", required=True)
    parser.add_argument("--candidate-revision")
    parser.add_argument("--assembly-root", type=Path)
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--dependency-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(os.environ.get("GITHUB_ACTIONS") == "true", "GitHub execution only")
        if args.operation == "classify":
            require(args.candidate_revision is None and args.assembly_root is None, "classification has no caller-selected candidate or artifact")
            require(not (args.force_full and args.dependency_only), "choose one execution mode")
            value = classify(args.repo, args.helper_revision, args.force_full)
            if args.dependency_only:
                require(value["run_full_candidate"] == "false", "candidate inputs changed; select explicit full candidate mode")
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                    for key in ("run_full_candidate", "candidate_revision", "helper_revision"):
                        stream.write(key + "=" + value[key] + "\n")
        else:
            require(not args.force_full and not args.dependency_only, "artifact verification has no execution-mode override")
            value = verify_candidate_inputs(args.repo, args.helper_revision, args.candidate_revision, args.assembly_root)
        print(json.dumps(value, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({"mode": "refused", "product_acceptance_passed": False,
                          "reason": str(error) if isinstance(error, Refusal) else "candidate admission metadata failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
