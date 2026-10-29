#!/usr/bin/env python3
"""Compare all paired CNL/native forms with one current typed candidate on GitHub.

This experiment supplies semantic evidence only. Policy, products, quality,
behavior, TypeScript and React acceptance stay separate and unrun.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from types import MappingProxyType, ModuleType

BASE = "som-rs/scripts/hosted_readiness/"
SELF = BASE + "run_experimental_native_cnl.py"
MODULES = {name: BASE + name + ".py" for name in (
    "export_goldens", "raw_digest_compat", "reconstruct_public_inputs",
    "map_rust_inputs", "complete_rust_inputs", "experimental_native_compile",
    "run_experimental_native_compile")}
POLICY = "som-rs/contracts/compiler-v1/selection-policy.json"
NEGATIVE = "som-rs/contracts/rust-output-v1/negative-cases.json"
PHASE2 = "som-rs/contracts/rust-output-v1/phase2/case-inventory.json"
REFUSALS = {"RS12_CNL_V3_FILTER_LIMIT": "limit", "RS13_JSON_V3_FILTER_OFFSET": "offset"}
EXPECTED = {"python_cnl": 1342, "rust_cnl": 699, "rust_native_json": 9}
MAX_SOURCE, MAX_RAW, MAX_METADATA = 8 * 1024 * 1024, 16 * 1024 * 1024, 64 * 1024 * 1024


class Refusal(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def no_links(path):
    path = Path(path).absolute()
    require(".." not in path.parts, "path traversal refused")
    for item in (path, *path.parents):
        require(not item.is_symlink(), "linked input path refused")


def regular_bytes(path, maximum=MAX_RAW):
    path = Path(path).absolute()
    require(".." not in path.parts, "path traversal refused")
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode), "linked path refused")
        require(stat.S_ISREG(info.st_mode) if item == path else stat.S_ISDIR(info.st_mode), "nonregular path refused")
        if item == path:
            require(info.st_nlink == 1 and info.st_size <= maximum, "linked or oversized file refused")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum, "file changed type or size")
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, "file exceeds byte bound")
    return raw


class Sources:
    """Bind all helper bytes before any helper code can execute."""
    def __init__(self, repo, revision):
        no_links(repo)
        self.root, self.revision, self.raw, self.pins = Path(repo).resolve(strict=True), revision, {}, {}
        require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "exact source revision required")
        require(self.git("rev-parse", "--verify", "HEAD").decode().strip() == revision, "source HEAD differs")
        require(Path(self.git("rev-parse", "--show-toplevel").decode().strip()).resolve() == self.root, "source root differs")
        for path in [SELF, *MODULES.values()]:
            self.read(path)

    def git(self, *args):
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        return subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-C", str(self.root), *args],
                                       env=env, stderr=subprocess.DEVNULL, timeout=30)

    def read(self, path):
        require(path in {SELF, *MODULES.values()}, "helper source is outside the fixed closure")
        if path not in self.raw:
            tree = self.git("ls-tree", "-z", self.revision, "--", path)
            require(tree.endswith(b"\0") and tree.count(b"\0") == 1, "source Git entry is missing or ambiguous")
            header, name = tree[:-1].split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            require(mode in {"100644", "100755"} and kind == "blob" and name.decode() == path, "regular source Git blob required")
            size = self.git("cat-file", "-s", oid).decode().strip()
            require(size.isdecimal() and int(size) <= MAX_SOURCE, "source Git blob exceeds bound")
            raw = self.git("cat-file", "blob", oid)
            require(len(raw) == int(size), "source Git blob size differs")
            require(regular_bytes(self.root / path, MAX_SOURCE) == raw, "working source differs from Git bytes")
            self.raw[path], self.pins[path] = raw, digest(raw)
        return self.raw[path]

    def verify(self):
        require(self.git("rev-parse", "--verify", "HEAD").decode().strip() == self.revision, "source HEAD changed")
        for path, raw in self.raw.items():
            require(regular_bytes(self.root / path, MAX_SOURCE) == raw, "bound helper source changed")


class BoundModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, sources):
        self.sources = sources
        self.bound = MappingProxyType({name: sources.read(path) for name, path in MODULES.items()})
        self.loaded = set()

    def find_spec(self, fullname, path=None, target=None):
        if fullname in MODULES:
            require(target is None, "bound helper reload refused")
            return importlib.util.spec_from_loader(fullname, self, origin=str(self.sources.root / MODULES[fullname]))
        return None

    def create_module(self, spec):
        return ModuleType(spec.name)

    def exec_module(self, module):
        name = module.__name__
        require(name in self.bound and name not in self.loaded, "bound helper name differs or reloaded")
        path = str(self.sources.root / MODULES[name])
        module.__file__, module.__cached__, module.__package__ = path, None, ""
        self.loaded.add(name)
        exec(compile(self.bound[name], path, "exec", dont_inherit=True), module.__dict__)

    @contextmanager
    def installed(self):
        require(not (set(MODULES) & set(sys.modules)), "bound helper namespace is already loaded")
        old = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        sys.meta_path.insert(0, self)
        try:
            for name in MODULES:
                importlib.import_module(name)
            yield self
            require(set(MODULES) == self.loaded, "bound helper import closure differs")
            self.sources.verify()
        finally:
            sys.meta_path.remove(self)
            sys.dont_write_bytecode = old
            for name in self.loaded:
                sys.modules.pop(name, None)


def tree_hashes(root):
    result = {}
    require(root.is_dir() and not root.is_symlink(), "input tree required")
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink() and (path.is_file() or path.is_dir()), "linked or special input node refused")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(regular_bytes(path, MAX_METADATA))
    return result


def frozen_inputs(driver, complete, checker, repo, python_inputs, rust_inputs, out, revision):
    document, rows, keys = driver.baseline(repo)
    typed_jobs, input_record = driver.prepare_jobs(repo, python_inputs, rust_inputs, rows, revision)
    require(all("input_error" not in job for job in typed_jobs), "one or more exact typed inputs are unavailable")
    require(set(checker.rows) == set(keys), "complete typed checker scope differs")
    goldens = driver.mapper.load_goldens(repo / (BASE + "inputs/public-golden-hashes-v1.json"))
    reused = driver.mapper.Reused(python_inputs, goldens)
    regenerated = out / "source-reconstructed-rust-inputs"
    completed = complete.complete(repo, python_inputs, goldens, regenerated, revision)
    expected_tree, actual_tree = tree_hashes(regenerated), tree_hashes(rust_inputs)
    require(len(expected_tree) == 2120 and actual_tree == expected_tree,
            "complete Rust requests differ from independent frozen-source reconstruction")
    require(digest(regular_bytes(rust_inputs / "manifest.json", MAX_METADATA)) == input_record["rust_input_manifest_sha256"],
            "Rust input manifest changed during independent reconstruction")
    return typed_jobs, reused, completed, input_record, expected_tree


def key(target, identity):
    fields = ("source", "id", "variant") if target == "python" else ("phase", "id")
    return (target, *(identity[name] for name in fields))


class TypedProof:
    """Recheck each saved typed request and reply with the independent checker."""
    def __init__(self, root, jobs, checker, driver, revision, input_record):
        self.root, self.hashes, self.rows = root, {}, {}
        raw = self.read("manifest.json", MAX_METADATA)
        value = driver.experiment.parse(raw)
        require(value.get("tested_sha") == revision and value.get("state") == "experimental-unsealed"
                and value.get("product_acceptance_passed") is False and value.get("policy_state") == "unsealed"
                and value.get("experiment_complete") is True and value.get("identity_stability_passed") is True
                and value.get("cleanup", {}).get("cleanup_passed") is True and value.get("inputs") == input_record,
                "typed proof candidate, input or completion scope differs")
        require(value["initial_identities"] == value["final_identities"], "typed proof identities changed")
        require(value.get("actual_rows") == driver.EXPECTED and value.get("passed") == 2041
                and len(value["rows"]) == len(jobs) == 2041, "whole typed proof inventory differs")
        actual_paths = set(tree_hashes(root / "rows"))
        expected_paths = {f"{index:04d}/{name}" for index in range(2041)
                          for name in ("request.raw", "response.raw", "result.json", "checker.json")}
        require(actual_paths == expected_paths, "typed proof raw row file universe differs")
        for index, (job, row) in enumerate(zip(jobs, value["rows"])):
            identity = key(job["target"], job["identity"])
            require(identity not in self.rows and row.get("index") == index and row.get("state") == "passed"
                    and key(row["target"], row["identity"]) == identity, "typed proof row identity or order differs")
            prefix = f"rows/{index:04d}/"
            request, response = self.read(prefix + "request.raw"), self.read(prefix + "response.raw")
            require(request == job["raw"] and digest(request) == row["expected_request_sha256"] == job["expected_request_sha256"],
                    "typed proof request differs from independent accepted bytes")
            require(digest(response) == row["current_response_sha256"] and row["http_status"] == job["expected_http_status"],
                    "typed proof reply hash or status differs")
            require(driver.experiment.parse(self.read(prefix + "result.json")) == row, "typed proof per-row result differs")
            saved_check = driver.experiment.parse(self.read(prefix + "checker.json"))
            require(saved_check["current_raw_sha256"] == digest(response), "typed proof checker raw pin differs")
            # The saved state and checker flag cannot authorize their own response.
            checked = checker.verify_response(job["target"], job["identity"], row["http_status"], response)
            require(checked.report["byte_compatibility_passed"] is True, "typed raw reply did not pass independent verification")
            self.rows[identity] = {"path": prefix + "response.raw", "sha256": digest(response),
                                   "status": row["http_status"], "index": index}
        self.receipt, self.manifest_sha256 = value, digest(raw)

    def read(self, path, maximum=MAX_RAW):
        require(not path.startswith("/") and all(part not in {"", ".", ".."} for part in path.split("/")), "unsafe typed proof path")
        raw = regular_bytes(self.root / path, maximum)
        observed = digest(raw)
        require(path not in self.hashes or self.hashes[path] == observed, "typed proof bytes changed")
        self.hashes[path] = observed
        return raw

    def reference(self, target, identity):
        return dict(self.rows[key(target, identity)])

    def verify(self):
        for path, wanted in self.hashes.items():
            require(digest(regular_bytes(self.root / path, MAX_METADATA if path == "manifest.json" else MAX_RAW)) == wanted,
                    "typed proof bytes changed after CNL checks")


def plan_jobs(driver, typed_jobs, reused, completed, rust_inputs, proof):
    rust_rows = {(phase, row["id"]): row for cohort, phase in (("rust_phase1", "phase1"), ("rust_phase2", "phase2"))
                 for row in completed["cohorts"][cohort]}
    require(len(rust_rows) == 699, "Rust paired input identities differ")
    jobs = []
    for typed in typed_jobs:
        target, identity = typed["target"], typed["identity"]
        if target == "python":
            source = reused.by_cohort["python_public" if identity["source"] == "public" else "python_legacy"][identity["id"]]
            locator = source["locators"]["compile-cnl"]
            original = reused.read(locator["path"], locator["raw_sha256"])
            body = driver.parse(original)
            require(body == {"format": "som.cnl.v1", "source": source["cnl"]}, "Python CNL source envelope differs")
            raw = original if identity["variant"] == "none" else driver.compact({**body, "target": "python"})
            group, origin = "python_cnl", {"input_path": locator["path"], "source_sha256": digest(source["cnl"].encode()),
                                          "input_raw_sha256": locator["raw_sha256"], "explicit_target_added": identity["variant"] == "python"}
        else:
            row = rust_rows[(identity["phase"], identity["id"])]
            locator = row["requests"]["cnl"]
            raw = driver.read_request(rust_inputs, locator, locator["raw_sha256"])
            body = driver.parse(raw)
            require(set(body) == {"format", "source", "target"} and body["format"] == "som.cnl.v1"
                    and body["target"] == "rust" and digest(body["source"].encode()) == row["cnl_source_sha256"],
                    "Rust CNL source envelope differs")
            group, origin = "rust_cnl", {"input_path": locator["path"], "source_sha256": row["cnl_source_sha256"],
                                       "input_raw_sha256": locator["raw_sha256"], "native_form": row.get("native_form")}
        jobs.append({"group": group, "target": target, "identity": dict(identity), "form": "cnl", "raw": raw,
                     "request_sha256": digest(raw), "input": origin, "typed_reference": proof.reference(target, identity)})
    originals = [row for row in completed["cohorts"]["rust_phase2"] if row.get("native_form") == "json"]
    require(len(originals) == 9 and len({row["id"] for row in originals}) == 9, "original native JSON scope differs")
    for row in originals:
        identity, locator = {"phase": "phase2", "id": row["id"]}, row["requests"]["json"]
        raw = driver.read_request(rust_inputs, locator, locator["raw_sha256"])
        body = driver.parse(raw)
        require(body["format"] == "som.prompt-json.v1" and body["target"] == "rust"
                and driver.value_sha(body["prompt"]) == row["expected_prompt_sha256"], "native JSON prompt differs")
        jobs.append({"group": "rust_native_json", "target": "rust", "identity": identity, "form": "json", "raw": raw,
                     "request_sha256": digest(raw), "input": {"input_path": locator["path"], "input_raw_sha256": locator["raw_sha256"], "native_form": "json"},
                     "typed_reference": proof.reference("rust", identity)})
    require(dict(Counter(job["group"] for job in jobs)) == EXPECTED, "complete companion inventory differs")
    identities = [(job["group"], key(job["target"], job["identity"])) for job in jobs]
    require(len(identities) == len(set(identities)), "companion identity is duplicated")
    return jobs


def location(text, start, end):
    def point(offset):
        before = text[:offset]
        return before.count("\n") + 1, offset - before.rfind("\n")
    line, column = point(start)
    end_line, end_column = point(end)
    return {"line": line, "column": column, "end_line": end_line, "end_column": end_column,
            "byte_start": len(text[:start].encode()), "byte_end": len(text[:end].encode())}


class RefusalOracle:
    """Derive only the two compiler-v1 collisions, never old Rust scope errors."""
    def __init__(self, checker, driver):
        self.driver = driver
        self.policy = driver.experiment.parse(checker.repo.read(POLICY))["python_name_collisions"]
        negatives = driver.experiment.parse(checker.repo.read(NEGATIVE))["cases"]
        self.negative = {row["id"]: row for row in negatives if row["id"] in REFUSALS}
        rows = driver.experiment.parse(checker.repo.read(PHASE2))["rows"]
        phase2 = {row["id"]: row["phase2_expected"] for row in rows if row["id"] in REFUSALS}
        require(set(self.negative) == set(phase2) == set(REFUSALS), "current collision authority scope differs")
        for identity, name in REFUSALS.items():
            expected, negative = phase2[identity], self.negative[identity]
            detail = self.policy["error"]["detail"].replace("<path>", "query.filter_field")
            require(expected == {"result": "refusal", "rule": "python_name_collisions", "code": "INVALID_VALUE",
                                 "detail": detail, "pointer": "/query/filter_field", "http_status": 400},
                    "current phase2 collision rule differs")
            require(negative["http_status"] == 400 and negative["expected"]["code"] == self.policy["error"]["code"] == "INVALID_VALUE"
                    and negative["expected"]["cause"] == detail, "native collision diagnostic authority differs")
            require(any(name in row["names"] and set(row["roles"]) <= {"field", "filter_field"}
                        for row in self.policy["versions"]["som.prompt.v3"]["postgresql"]), "collision name is not selected by pinned policy")

    def expected(self, job):
        identity, raw, form = job["identity"]["id"], job["raw"], job["form"]
        require(job["target"] == "rust" and identity in REFUSALS, "independent refusal oracle has no authority for this case")
        name, body = REFUSALS[identity], self.driver.parse(raw)
        negative = self.negative[identity]
        cause = self.policy["error"]["detail"].replace("<path>", "query.filter_field")
        if form == "cnl":
            source = body["source"]
            statement = "Filter exactly on field " + name + "."
            matches, offset = [], 0
            for line in source.splitlines(keepends=True):
                if line.rstrip("\r\n") == statement:
                    start = offset + len("Filter exactly on field ")
                    matches.append((start, start + len(name)))
                offset += len(line)
            require(len(matches) == 1, "collision CNL statement is absent or ambiguous")
            span = location(source, *matches[0])
            pointer = "/query/filter_field"
            allowed = self.negative["RS12_CNL_V3_FILTER_LIMIT"]["expected"]["allowed_form"]
        else:
            require(form == "json" and body["prompt"]["version"] == "som.prompt.v3"
                    and body["prompt"]["query"]["filter_field"] == name, "collision typed prompt differs")
            node = self.driver.experiment.raw_compat._Document(raw).nodes[("prompt", "query", "filter_field")]
            require(node[0] == "string" and node[3] == name, "collision JSON pointer token differs")
            text = raw.decode("utf-8")
            start = len(raw[:node[1]].decode("utf-8"))
            end = len(raw[:node[2]].decode("utf-8"))
            span = location(text, start, end)
            pointer = "/prompt/query/filter_field"
            allowed = self.negative["RS13_JSON_V3_FILTER_OFFSET"]["expected"]["allowed_form"]
        diagnostic = {"code": "INVALID_VALUE", **span, "prompt_pointer": pointer, "cause": cause, "allowed_form": allowed}
        if negative["form"] == form:
            require(raw == negative["body"].encode(), "native original request bytes differ")
            require(diagnostic == negative["expected"], "derived native span differs from frozen complete diagnostic")
        expected = {"status": "error", "code": "INVALID_VALUE", "detail": cause, "diagnostics": [diagnostic]}
        return 400, self.driver.compact(self.driver.sorted_objects(expected)), diagnostic


def run_rows(jobs, proof, oracle, service, out, driver, *, start_index=0):
    records = []
    for index, job in enumerate(jobs, start_index):
        directory = out / "rows" / f"{index:04d}"
        started = time.monotonic()
        record = {name: value for name, value in job.items() if name != "raw"}
        record.update(index=index, state="unrun", http_status=None, started_at=driver.utc())
        try:
            directory.mkdir(parents=True)
            try:
                require(digest(job["raw"]) == job["request_sha256"], "companion request changed")
                (directory / "request.raw").write_bytes(job["raw"])
                require(service is not None, "owned companion service unavailable")
                status, raw, ownership = service.call("/v1/compile", job["raw"])
                (directory / "response.raw").write_bytes(raw)
                record.update(state="failed", http_status=status, response_sha256=digest(raw), owned_socket=ownership)
                if job["target"] == "rust" and job["identity"]["id"] in REFUSALS:
                    expected_status, expected_raw, diagnostic = oracle.expected(job)
                    require(status == expected_status and raw == expected_raw, "current400 complete refusal bytes or spans differ")
                    record.update(comparison="independent frozen compiler collision rule and raw span", expected_status=expected_status,
                                  expected_response_sha256=digest(expected_raw), expected_diagnostic=diagnostic)
                else:
                    reference = job["typed_reference"]
                    expected_raw = proof.read(reference["path"])
                    require(digest(expected_raw) == reference["sha256"] and reference["status"] == 200,
                            "verified typed positive reference changed")
                    require(status == 200 and raw == expected_raw, "companion response differs from exact same-candidate typed bytes")
                    record.update(comparison="exact complete current typed reply bytes", expected_status=200,
                                  expected_response_sha256=reference["sha256"])
                record["state"] = "passed"
            except InterruptedError:
                raise
            except Exception as error:
                record.update(error=type(error).__name__, reason=str(error) if isinstance(error, (Refusal, driver.Refusal, driver.experiment.Refusal)) else "see saved raw proof")
                if service is not None and record["state"] == "unrun":
                    record["state"] = "failed_transport_or_ownership"
            record.update(finished_at=driver.utc(), elapsed_seconds=time.monotonic() - started)
            driver.dump(directory / "result.json", record)
            records.append(record)
        except BaseException as error:
            record.update(state="interrupted" if isinstance(error, (InterruptedError, KeyboardInterrupt)) else "recording_failed",
                          error=type(error).__name__, finished_at=driver.utc(), elapsed_seconds=time.monotonic() - started)
            records.append(record)
            raise driver.RowsStopped(records, error) from error
    return records


def identities(driver, checker, binary, assets, tools):
    return {"source": driver.source_snapshot(checker), "binary": driver.file_identity(binary), "assets": driver.tree_identity(assets),
            "tools": {name: driver.tree_identity(path) if name == "SOM_TS_PRETTIER" else driver.file_identity(path)
                      for name, path in tools.items()}}


def close_service(service):
    if service is None:
        return {"owned_pid": None, "cleanup_passed": True}
    try:
        return service.close()
    except BaseException as error:
        return {"owned_pid": None, "cleanup_passed": False, "error": type(error).__name__}


def portable_identity(value):
    if type(value) is dict and {"sha256", "size", "mode"} <= set(value):
        return {key: value[key] for key in ("sha256", "size", "mode")}
    require(type(value) is dict, "complete portable identity object required")
    return {key: portable_identity(item) for key, item in value.items()}


def typed_identity_binding(current, prior, carrier_root, proof_sha256, revision):
    names = ("binary", "assets", "tools")
    if carrier_root is None:
        require(all(current[name] == prior[name] for name in names),
                "companion binary, assets or tools differ from the complete typed build")
        return {"mode": "original-same-job-identities"}
    root = Path(carrier_root)
    raw = regular_bytes(root / "carrier.json", MAX_METADATA)
    receipt = json.loads(raw)
    expected = portable_identity({name: prior[name] for name in names})
    require(receipt.get("version") == "som.tested-candidate-carrier.v1"
            and receipt.get("candidate_revision") == revision
            and receipt.get("compile_manifest_sha256") == proof_sha256
            and digest(regular_bytes(root / "compile-manifest.json", MAX_METADATA)) == proof_sha256
            and receipt.get("tested_identities") == expected
            and portable_identity({name: current[name] for name in names}) == expected,
            "restored carrier is not bound to the same complete tested candidate")
    require(type(receipt.get("compiler_rebuilt")) is bool and receipt.get("origin_role") ==
            ("new build compared byte-for-byte with the admitted old candidate" if receipt["compiler_rebuilt"]
             else "original already tested candidate bytes"), "carrier origin differs")
    archive_sha = digest(regular_bytes(root / "candidate.tar.gz", 512 * 1024**2))
    require(archive_sha == receipt.get("archive_sha256"), "carrier archive changed")
    return {"mode": "verified-same-candidate-portable-carrier-identities", "carrier_sha256": digest(raw),
            "archive_sha256": archive_sha, "compile_manifest_sha256": proof_sha256,
            "candidate_revision": revision, "compiler_rebuilt": receipt["compiler_rebuilt"]}


def run(args, sources, driver, complete):
    for path in (args.repo, args.python_inputs, args.rust_inputs, args.typed_proof, args.binary, args.assets, args.ruff, args.rustfmt, args.node, args.prettier):
        no_links(path)
    repo, python_inputs, rust_inputs, proof_root = (path.resolve(strict=True) for path in (args.repo, args.python_inputs, args.rust_inputs, args.typed_proof))
    binary, assets, out = args.binary.absolute(), args.assets.absolute(), args.out.absolute()
    tools = {name: getattr(args, field).absolute() for name, field in driver.TOOLS.items()}
    require(args.port == 18977, "the dedicated companion port18977 is required")
    require(".." not in out.parts and not out.exists() and Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) in out.parents,
            "new output under RUNNER_TEMP required")
    protected = [repo, python_inputs, rust_inputs, proof_root, binary, assets, *tools.values()]
    require(all(out != path and path not in out.parents and out not in path.parents for path in protected), "output overlaps a protected input")
    driver.no_symlinks(out)
    out.mkdir()
    report = {"version": "som.native-cnl-companion-experiment.v1", "state": "experimental-unsealed", "policy_state": "unsealed",
              "tested_sha": args.tested_sha, "product_acceptance_passed": False, "product_runtime_ready": False,
              "full_input_set_ready": False, "expected_rows": EXPECTED, "started_at": driver.utc(), "rows": [],
              "remaining": {"numbered_native_compatibility": "unsealed", "product_quality_and_behavior": "unrun",
                            "typescript_react_full_acceptance": "unrun"}}
    checker = service = proof = reused = None
    jobs, records = [], []
    initial = rust_hashes = None
    started = time.monotonic()
    with driver.cancellation_handlers() as cancellation:
        try:
            checker = driver.experiment.ExperimentalChecker(repo)
            require(checker.repo.commit == args.tested_sha, "checker source candidate differs")
            for path in [SELF, *MODULES.values()]:
                require(checker.repo.read(path) == sources.read(path), "helper bytes differ from checker Git bindings")
            typed_jobs, reused, completed, input_record, rust_hashes = frozen_inputs(driver, complete, checker, repo, python_inputs, rust_inputs, out, args.tested_sha)
            proof = TypedProof(proof_root, typed_jobs, checker, driver, args.tested_sha, input_record)
            jobs = plan_jobs(driver, typed_jobs, reused, completed, rust_inputs, proof)
            oracle = RefusalOracle(checker, driver)
            for job in jobs:
                if job["target"] == "rust" and job["identity"]["id"] in REFUSALS:
                    oracle.expected(job)
            initial = identities(driver, checker, binary, assets, tools)
            prior = proof.receipt["initial_identities"]
            transport = typed_identity_binding(initial, prior, getattr(args, "carrier_root", None),
                                               proof.manifest_sha256, args.tested_sha)
            report["typed_identity_transport"] = transport
            for path, wanted in prior["source"].items():
                require(digest(checker.repo.read(path)) == wanted, "typed proof owning source differs from current Git")
            initial = identities(driver, checker, binary, assets, tools)
            report.update(inputs=input_record, independent_rust_request_tree_sha256=digest(driver.compact(driver.sorted_objects(rust_hashes))),
                          independent_rust_request_files=len(rust_hashes), typed_proof_manifest_sha256=proof.manifest_sha256,
                          typed_raw_rows_independently_reverified=2041, initial_identities=initial,
                          asset_manifest_canonical_sha256=driver.asset_manifest_canonical_sha(assets), source_pin_record=checker.source_record())
            service = driver.OwnedService(binary, assets, tools, args.port, out)
            report["launch"] = {"argv": service.argv, "environment": service.environment}
            report["owned_start"] = service.start()
            status, raw, owner = service.call("/healthz")
            (out / "health.response.raw").write_bytes(raw)
            health = driver.experiment.parse(raw)
            require(status == 200 and health["status"] == "ok" and health["model_loaded"] is False and health["blocks_loaded"] is True
                    and health["backend_sha256"] == checker.after["python_backend"]
                    and health["asset_manifest_sha256"] == report["asset_manifest_canonical_sha256"]
                    and health["formatter_sha256"] == initial["tools"]["SOM_RUFF"]["sha256"], "owned health differs from independent factory and typed identities")
            report["health_consistency"] = {"status": status, "raw_sha256": digest(raw), "owned_socket": owner}
            records = run_rows(jobs, proof, oracle, service, out, driver)
        except BaseException as error:
            if isinstance(error, driver.RowsStopped):
                records, error = error.records, error.cause
            report.update(setup_error=type(error).__name__, setup_reason=str(error) if isinstance(error, (Refusal, driver.Refusal, driver.experiment.Refusal)) else "see preserved input, source and raw proof")
        finally:
            cancellation["cleanup"] = True
            report["cleanup"] = close_service(service)
            if len(records) < len(jobs):
                try:
                    records += run_rows(jobs[len(records):], proof, None, None, out, driver, start_index=len(records))
                except driver.RowsStopped as error:
                    records += error.records
                    report["row_recording_error"] = type(error.cause).__name__
            try:
                sources.verify()
                if proof is not None:
                    proof.verify()
                if reused is not None:
                    reused.verify()
                if rust_hashes is not None:
                    require(tree_hashes(rust_inputs) == rust_hashes == tree_hashes(out / "source-reconstructed-rust-inputs"), "paired inputs changed after checks")
                if initial is not None:
                    final = identities(driver, checker, binary, assets, tools)
                    report["final_identities"] = final
                    require(initial == final, "source, binary, assets or tools changed")
                    require(typed_identity_binding(final, proof.receipt["initial_identities"],
                                getattr(args, "carrier_root", None), proof.manifest_sha256,
                                args.tested_sha) == report["typed_identity_transport"], "carrier transport changed")
                    report["identity_stability_passed"] = True
                    report["input_and_typed_proof_stability_passed"] = True
            except BaseException as error:
                report.update(identity_stability_passed=False, input_and_typed_proof_stability_passed=False, final_verification_error=type(error).__name__)
        report.update(rows=records, actual_rows=dict(Counter(row["group"] for row in records)), passed=sum(row["state"] == "passed" for row in records),
                      interrupted_signal=cancellation["signal"], finished_at=driver.utc(), elapsed_seconds=time.monotonic() - started)
        report["experiment_complete"] = (len(records) == sum(EXPECTED.values()) and report["actual_rows"] == EXPECTED
                                         and report["passed"] == sum(EXPECTED.values()) and report.get("identity_stability_passed") is True
                                         and report.get("input_and_typed_proof_stability_passed") is True and report["cleanup"]["cleanup_passed"] is True
                                         and "setup_error" not in report and "row_recording_error" not in report and cancellation["signal"] is None)
        driver.dump(out / "manifest.json", report)
    print(json.dumps({name: report[name] for name in ("state", "product_acceptance_passed", "actual_rows", "passed", "experiment_complete")}))
    return 0 if report["experiment_complete"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "python-inputs", "rust-inputs", "typed-proof", "binary", "assets", "out", "ruff", "rustfmt", "node", "prettier"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--tested-sha", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--carrier-root", type=Path)
    args = parser.parse_args(argv)
    try:
        require(os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "linux", "GitHub Linux runner only")
        require(Path(__file__).absolute() == args.repo.absolute() / SELF, "fixed source path required")
        sources = Sources(args.repo, args.tested_sha)
        with BoundModules(sources).installed():
            driver = importlib.import_module("run_experimental_native_compile")
            complete = importlib.import_module("complete_rust_inputs")
            return run(args, sources, driver, complete)
    except Exception as error:
        print(json.dumps({"state": "experimental-unsealed", "product_acceptance_passed": False, "experiment_complete": False,
                          "error": type(error).__name__, "reason": str(error) if isinstance(error, Refusal) else "see preserved evidence"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
