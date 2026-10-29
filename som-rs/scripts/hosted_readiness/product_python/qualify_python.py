#!/usr/bin/env python3
"""Inventory real Python environments on GitHub. Never accept products.

No resolver, package, compiler, server or generated Python code runs here.
The next stage must solve each emitted environment separately on GitHub.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tomllib

HERE = "som-rs/scripts/hosted_readiness/product_python/"
SCRIPT = HERE + "qualify_python.py"
PLAN = HERE + "environment-plan.json"
GOLDENS = "som-rs/scripts/hosted_readiness/inputs/public-golden-hashes-v1.json"
TS065 = "som-rs/scripts/hosted_readiness/inputs/ts065-baseline-hashes-v1.json"
SHA = re.compile(r"[a-f0-9]{64}\Z")
PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[([A-Za-z0-9_,.-]+)\])?==([A-Za-z0-9][A-Za-z0-9.!+_-]*)\Z")
LIMIT = 256 * 1024 * 1024
FORBIDDEN = {"mlx", "mlx-lm", "mlx-metal", "transformers", "tokenizers", "safetensors", "sentencepiece", "huggingface-hub", "hf-xet"}


class Refusal(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Refusal(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def relative(value):
    require(isinstance(value, str) and value and "\\" not in value and not value.startswith("/")
            and all(part not in {"", ".", ".."} for part in value.split("/"))
            and not any(ord(c) < 32 or ord(c) == 127 for c in value), "unsafe relative path")
    return value


def read(path, limit=LIMIT):
    require(all(not p.is_symlink() for p in (path, *path.parents)), "symlink input refused")
    require(path.is_file() and path.stat().st_size <= limit, "regular bounded input required")
    return path.read_bytes()


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


class Sources:
    """Bind each authority file to the declared regular Git blob."""
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
        require(len(bits) == 3 and bits[0] in {b"100644", b"100755"} and bits[1] == b"blob", "source is not a regular Git blob")
        size = self.git("cat-file", "-s", bits[2].decode()).strip()
        require(size.isdigit() and int(size) <= LIMIT, "source blob exceeds size bound")
        raw = self.git("cat-file", "blob", bits[2].decode())
        require(len(raw) == int(size) and raw == read(self.root / name), "source differs from committed blob")
        require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == bits[2].decode(), "Git blob identity differs")
        self.pins[name] = {"sha256": sha(raw), "git_blob_oid": bits[2].decode()}
        return raw


def requirements(raw, template=False):
    """Keep every source constraint, extra, order, and dependency group."""
    config = tomllib.loads(raw.decode("utf-8"))
    project = config.get("project", {})
    require(set(project) <= {"name", "version", "requires-python", "dependencies"}, "unreviewed project configuration")
    require(project.get("requires-python") == ">=3.12,<3.13", "Python constraint differs")
    require(config.get("tool", {}).get("uv") == {"package": False}, "only unpackaged products admitted")
    require("build-system" not in config, "build backend refused")
    groups = config.get("dependency-groups", {})
    require(set(groups) == {"dev"}, "dependency groups differ")
    runtime, dev = project.get("dependencies"), groups["dev"]
    require(isinstance(runtime, list) and runtime and isinstance(dev, list), "dependency arrays required")
    for group in (runtime, dev):
        require(all(isinstance(item, str) for item in group), "string requirement required")
        require(len(set(group)) == len(group), "duplicate direct requirement")
        for item in group:
            match = PIN.fullmatch(item)
            require(match is not None and normalize(match[1]) not in FORBIDDEN, "unreviewed direct requirement")
    require(not template or project.get("name") == "{{package}}", "template package placeholder differs")
    quality = config.get("tool", {}).get("som", {}).get("python-quality")
    checker = None
    if quality is not None:
        tools = {"ruff": "0.16.6", "mypy": "2.3.1", "pylint": "4.0.9", "isort": "9.0.2"}
        require(quality.get("contract") == "som.python-quality.v1" and quality.get("tools") == tools,
                "quality tool requirements differ from the owning checker")
        require(all(name + "==" + version in dev for name, version in tools.items()),
                "quality tool is not declared by its owning product")
        checker = {"source": "som-core/scripts/verify_python_quality.py", "tool_versions": tools,
                   "interpreter": "this-environment/venv/bin/python",
                   "argv_after_interpreter": ["som-core/scripts/verify_python_quality.py", "PRODUCT", "--evidence", "NEW_EVIDENCE"],
                   "observation": "unchanged checker records tool_versions and requires exact pins before invoking every tool"}
    return {"requires_python": project["requires-python"], "runtime": runtime, "development": dev,
            "quality_checker": checker}


def inventory(sources, plan):
    require(plan.get("version") == "som.hosted-python-environment-plan.v1", "environment plan version differs")
    require(plan.get("python") == "3.12.11" and plan.get("uv") == "0.9.9", "runtime versions differ")
    for name, expected in plan["source_sha256"].items():
        require(SHA.fullmatch(expected) and sha(sources.read(name)) == expected, "owning source changed; review required: " + name)
    records = []
    for name in plan["template_sources"]:
        require(name in plan["source_sha256"], "template source has no reviewed pin")
        blocks = parse(sources.read(name))
        ids = set()
        for block in blocks:
            require(block["id"] not in ids, "duplicate block ID")
            ids.add(block["id"])
            text = block.get("template", "")
            if text.startswith("[project]\n"):
                records.append({"source": name, "block_id": block["id"], "template_sha256": sha(text.encode()),
                                **requirements(text.encode(), template=True)})
    require(len(records) == 8, "template environment inventory differs")
    observer = plan["observer_requirements"]
    require(isinstance(observer, list) and observer and len(set(observer)) == len(observer), "observer requirements missing/duplicated")
    for item in observer:
        match = PIN.fullmatch(item)
        require(match is not None and normalize(match[1]) not in FORBIDDEN, "invalid observer requirement")
    require("requests==2.34.2" in observer and "moto[server,s3]==5.2.3" in observer, "observer requests/Moto pins differ")
    require("requests==2.32.5" not in observer and any("requests==2.32.5" in row["runtime"] for row in records), "observer and product requests must stay separate")
    return {"templates": records, "observer_requirements": observer,
            "direct_runtime_spellings": sorted({item for row in records for item in row["runtime"]}),
            "direct_development_spellings": sorted({item for row in records for item in row["development"]}),
            "warning": "Direct requirements only. These are not a solved transitive wheel closure."}


def files_digest(files):
    # All product map values are strings. This is the accepted qa.sha map rule.
    return sha(json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def actual_files(artifact, variant, golden):
    require(variant.get("state") == "passed", "assembly variant did not pass")
    directory = artifact / relative(variant["product_dir"])
    require(re.fullmatch(r"assembly/observations/[0-9]{4}/product", variant["product_dir"]) is not None, "product directory differs")
    listing = variant.get("files")
    require(isinstance(listing, list) and len(listing) == golden["file_count"], "product file count differs")
    files, per_file = {}, []
    for row in listing:
        name = relative(row["path"])
        require(name not in files, "duplicate product path")
        raw = read(directory / name)
        require(type(row["size"]) is int and len(raw) == row["size"] and sha(raw) == row["sha256"], "product file bytes differ")
        files[name] = raw.decode("utf-8", "strict")
        per_file.append({"path_sha256": sha(name.encode()), "content_sha256": sha(raw)})
    require(directory.is_dir() and all(not p.is_symlink() for p in (directory, *directory.parents)), "regular product directory required")
    actual = []
    for member in directory.rglob("*"):
        require(not member.is_symlink() and (member.is_dir() or member.is_file()), "nonregular product member")
        if member.is_file():
            actual.append(member.relative_to(directory).as_posix())
    actual.sort()
    require(actual == sorted(files), "unlisted product file or link")
    require(sorted(per_file, key=lambda row: row["path_sha256"]) == sorted(golden["accepted_per_file_hashes"], key=lambda row: row["path_sha256"]), "accepted per-file hashes differ")
    require(files_digest(files) == golden["files_map_sha256"] == variant["actual_files_map_sha256"], "accepted complete product map differs")
    return files


def products(artifact, document, goldens, revision, golden_sha, ts065_sha):
    require(document.get("version") == "som.native-assembly-experiment.v1" and document.get("tested_sha") == revision, "assembly identity differs")
    require(document.get("state") == "experimental-unsealed" and document.get("product_acceptance_passed") is False, "assembly is not the expected unsealed input")
    require(document.get("assembly_experiment_complete") is True, "owned assembly experiment incomplete")
    receipts = document.get("input_receipts", {})
    require(receipts.get("goldens_raw_sha256") == golden_sha and receipts.get("ts065_raw_sha256") == ts065_sha, "assembly source receipts differ")
    expected = [(cohort, row) for cohort in ("python_public", "python_legacy") for row in goldens["cohorts"][cohort]]
    entries = document["products"]["python"]
    require(len(entries) == len(expected), "whole Python product scope differs")
    result, used, physical = [], set(), set()
    observations = document.get("observations", [])
    for entry, (cohort, golden) in zip(entries, expected):
        require((entry["cohort"], entry["id"], entry["index"]) == (cohort, golden["id"], golden["index"]), "frozen product key/order differs")
        require(entry["state"] == "passed" and entry["prompt_sha256"] == golden["prompt_sha256"], "product prompt/state differs")
        require(entry["expected_files_map_sha256"] == golden["files_map_sha256"], "product map authority differs")
        variants = entry.get("compile_variants", [])
        require(len(variants) == 2 and [row["compile_variant"] for row in variants] == ["none", "python"], "both Python variants required")
        selected = None
        for variant in variants:
            index = variant["observation_index"]
            require(type(index) is int and 0 <= index < len(observations) and index not in used, "assembly observation missing or reused")
            used.add(index)
            observed = observations[index]
            expected_source = "public" if cohort == "python_public" else "legacy"
            expected_mode = "checked-dsl" if golden.get("variant", "standard") == "checked" else "compile-default"
            require((observed.get("target"), observed.get("cohort"), observed.get("source"), observed.get("id"),
                     observed.get("index"), observed.get("compile_variant"), observed.get("mode"), observed.get("observation_index"))
                    == ("python", cohort, expected_source, golden["id"], golden["index"], variant["compile_variant"], expected_mode, index),
                    "assembly observation named identity or mode differs")
            prefix = f"assembly/observations/{index:04d}"
            require(prefix not in physical and variant["product_dir"] == prefix + "/product", "assembly physical proof reused or index differs")
            physical.add(prefix)
            exchange = {}
            for field in ("request_path", "response_path"):
                label = field.removesuffix("_path")
                key = label + "_sha256"
                require(variant[field] == prefix + "/assemble." + label + ".raw"
                        and observed.get(field) == variant[field] and observed.get(key) == variant[key], "assembly observation reference differs")
                exchange[label] = read(artifact / relative(variant[field]))
                require(sha(exchange[label]) == variant[key], "assembly exchange bytes differ")
            require(observed.get("state") == "passed" and observed.get("product_dir") == variant["product_dir"], "assembly observation did not pass")
            require(sha(exchange["response"]) == golden["raw_assemble_response_sha256"], "frozen raw product reply differs")
            reply = parse(exchange["response"])
            require(type(reply) is dict and set(reply) == {"status", "files"} and reply["status"] == "ok"
                    and type(reply["files"]) is dict and all(type(k) is str and type(v) is str for k, v in reply["files"].items()),
                    "product reply schema differs")
            files = actual_files(artifact, variant, golden)
            require(reply["files"] == files, "raw reply differs from saved complete product map")
            if variant["compile_variant"] == "none":
                selected = files
                require(entry["product_dir"] == variant["product_dir"], "selected product differs")
        require(selected is not None, "selected product missing")
        needs = requirements(selected["pyproject.toml"].encode()) if "pyproject.toml" in selected else None
        result.append({"cohort": cohort, "id": entry["id"], "files_map_sha256": golden["files_map_sha256"],
                       "pyproject_bytes": selected.get("pyproject.toml"), "requirements": needs})
    return result


def write_environment(root, name, needs, pyproject=None):
    directory = root / name
    directory.mkdir()
    # Each environment keeps its exact direct constraints. No union or overrides.
    raw = ("\n".join(needs["runtime"] + needs["development"]) + "\n").encode()
    (directory / "requirements.in").write_bytes(raw)
    if pyproject is not None:
        (directory / "pyproject.toml").write_bytes(pyproject.encode())
    return {"id": name, "requires_python": needs["requires_python"], "requirements": needs,
            "requirements_in_sha256": sha(raw), "pyproject_sha256": sha(pyproject.encode()) if pyproject is not None else None,
            "cache": name + "/cache", "venv": name + "/venv", "wheelhouse": name + "/wheels",
            "uv_lock": name + "/uv.lock", "requirements_lock": name + "/requirements.lock",
            "transitive_resolution": "unrun", "offline_provision": "unrun", "license_notice_audit": "unrun"}



def checked_output(repo, out, assembly, temporary):
    require(".." not in out.parts and temporary in out.parents and repo not in out.parents
            and not out.exists() and out.parent.is_dir(), "new output below RUNNER_TEMP required")
    require(all(not p.is_symlink() for p in (out, *out.parents)), "symlink output refused")
    if assembly is not None:
        require(assembly.is_absolute() and ".." not in assembly.parts and assembly.is_dir()
                and all(not p.is_symlink() for p in (assembly, *assembly.parents)), "regular assembly input directory required")
        require(out != assembly and assembly not in out.parents, "output must be outside assembly input tree")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--assembly", type=Path)
    args = parser.parse_args()
    require(os.environ.get("GITHUB_ACTIONS") == "true" and platform.system() == "Linux" and platform.machine() == "x86_64", "GitHub Linux x86_64 only")
    repo, out = args.repo.absolute(), args.out.absolute()
    require(Path(__file__).absolute() == repo / SCRIPT, "fixed helper source path required")
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    checked_output(repo, out, args.assembly.absolute() if args.assembly is not None else None, temporary)
    out.mkdir()
    report = {"version": "som.hosted-python-environment-inventory.v1", "source_revision": args.source_revision,
              "state": "source-plan-only", "product_runtime_ready": False, "product_acceptance_passed": False,
              "closure_qualification_complete": False, "environment_input_set_complete": False,
              "environments": [], "missing_inputs": []}
    exit_code = 0
    sources = None
    try:
        sources = Sources(repo, args.source_revision)
        sources.read(SCRIPT)
        plan = parse(sources.read(PLAN))
        report["inventory"] = inventory(sources, plan)
        golden_raw, ts_raw = sources.read(GOLDENS), sources.read(TS065)
        goldens = parse(golden_raw)
        require([len(goldens["cohorts"][key]) for key in ("python_public", "python_legacy")] == [127, 544], "frozen Python scope differs")
        report["expected_products"] = 671
        environment_root = out / "environments"
        environment_root.mkdir()
        observer = {"requires_python": ">=3.12,<3.13", "runtime": plan["observer_requirements"], "development": []}
        report["environments"].append(write_environment(environment_root, "observer", observer))
        if args.assembly is None:
            report["missing_inputs"].append("complete owned 671-product assembly artifact")
        else:
            artifact = args.assembly.absolute()
            assembly_raw = read(artifact / "assembly/manifest.json")
            report["assembly_manifest_sha256"] = sha(assembly_raw)
            rows = products(artifact, parse(assembly_raw), goldens, args.source_revision, sha(golden_raw), sha(ts_raw))
            report["observed_products"] = len(rows)
            report["product_environment_counts"] = dict(Counter("own-project" if row["requirements"] else "observer-runtime" for row in rows))
            report["observer_runtime_products"] = [{key: row[key] for key in ("cohort", "id", "files_map_sha256")} for row in rows if row["requirements"] is None]
            for index, row in enumerate(rows):
                if row["requirements"] is not None:
                    record = write_environment(environment_root, f"product-{index:04d}", row["requirements"], row["pyproject_bytes"])
                    record.update(cohort=row["cohort"], product_id=row["id"],
                                  files_map_sha256=row["files_map_sha256"])
                    report["environments"].append(record)
            report.update(state="complete-product-environment-inputs", environment_input_set_complete=True)
        report["missing_inputs"] += plan["remaining_required_inputs"]
    except Exception as error:
        exit_code = 1
        report.update(state="failed", error=type(error).__name__, reason=str(error) if isinstance(error, Refusal) else "hosted input parsing failed")
    finally:
        report["source_bindings"] = sources.pins if sources is not None else {}
        dump(out / "receipt.json", report)
    print(json.dumps({key: report[key] for key in ("state", "product_runtime_ready", "product_acceptance_passed", "environment_input_set_complete", "closure_qualification_complete", "missing_inputs")}))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
