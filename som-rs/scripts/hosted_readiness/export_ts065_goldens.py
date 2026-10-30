#!/usr/bin/env python3
"""Export accepted TS065 hashes from a local source index; copy no source bodies."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys

REVIEWED_ROUTE_SHA256 = "663632b8985c3dfbfdbc09d30c078c71eaf07f16376ff3658d0dcb665561d422"
PUBLIC_REVISION = "ed56f2cedff497ad9853866c5b4fc10e4b2f8fa2"
PUBLIC_RELEASE_SHA256 = "f8e649ea96bac3f5e62ef01537c4c517a857e0743d05fb38028d013faf08b640"
# This is a historical index-format marker, not evidence of sensitive content.
# Unpublished files and local paths do not require encryption or a private repo.
PRIVATE = "LOCAL PRIVATE REVIEW — DO NOT UPLOAD"
RELEASE = "som-rs/evidence/typescript-output-release-2026-10-22-issue65.json"
P1 = "som-rs/contracts/rust-output-v1/case-inventory.json"
P2 = "som-rs/contracts/rust-output-v1/phase2/case-inventory.json"
FIXTURES = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
EXAMPLES = "som-rs/contracts/compiler-v1/examples/"
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. :+()-]{0,159}\Z")
STEPS = ("target", "rt-diagnostics", "cli", "bundles", "ts-products", "python-baseline",
         "python-replay", "validate-cases", "ts-python-controls", "rust-python-controls",
         "rust-baseline", "rust-products", "rust-unchanged")
UNCHECKED = {("target", "target/responses"), ("rt-diagnostics", "rt-diagnostics/responses"),
             ("bundles", "bundles/products"), ("bundles", "bundles/responses")}
COUNTS = {"python_rows": 1342, "rust_rows": 2093, "public_named_inputs": 127,
          "legacy_named_inputs": 544, "python_variants": 2, "rust_phase1_named_inputs": 115,
          "rust_phase2_named_inputs": 584, "rust_product_named_inputs": 697,
          "rust_refusal_named_inputs": 2, "accepted_receipts": 13,
          "canary_source_files": 54, "canary_descriptor_observations": 12}
PY_HASHES = ("baseline_assemble_request_sha256", "baseline_assemble_response_sha256",
             "baseline_compile_request_sha256", "baseline_compile_response_sha256",
             "compile_request_sha256", "qa_assemble_request_sha256", "qa_assemble_response_sha256",
             "qa_compile_request_sha256", "qa_compile_response_sha256")
PY_STATUSES = ("baseline_assemble_status", "baseline_compile_status", "qa_assemble_status", "qa_compile_status")
RS_HASHES = ("baseline_request_sha256", "baseline_response_sha256", "qa_request_sha256",
             "qa_response_sha256", "request_sha256", "response_sha256")
RS_REBASED_HASHES = ("files_sha256", "recorded_files_sha256")
RS_STATUSES = ("baseline_status", "qa_status")
HEALTH_HASHES = ("asset_manifest_sha256", "backend_sha256", "blocks_sha256", "formatter_sha256",
                 "grammar_sha256", "legacy_blocks_sha256", "quality_blocks_sha256", "selection_policy_sha256")
HEALTH_VERSIONS = ("bundle_version", "compiler_version", "formatter_version", "grammar_version", "selection_policy_version")
ASSETS = {"backend", "block_library", "catalog", "compiler_contract", "formatter", "grammar",
          "literal_policy", "selection_policy", "semantic_contracts", "serializer", "target_contract"}
RUST_ASSETS = {"backend", "block_library", "catalog", "formatter", "target_contract"}
FAMILIES = {"python_canaries": "python", "rust_canaries": "rust", "typescript_canaries": "typescript"}


class Refusal(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def constant(_value):
        raise Refusal("nonfinite JSON number")
    def number(value):
        result = float(value)
        require(math.isfinite(result), "nonfinite JSON number")
        return result
    text = raw.decode("utf-8", "strict") if isinstance(raw, bytes) else raw
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def digest(value):
    require(isinstance(value, str) and SHA.fullmatch(value), "invalid digest")
    return value


def identity(value):
    require(isinstance(value, str) and IDENTITY.fullmatch(value), "invalid identity")
    return value


def version(value):
    require(isinstance(value, str) and VERSION.fullmatch(value), "invalid version metadata")
    return value


def relative(value):
    require(isinstance(value, str) and value and "\\" not in value, "invalid public path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(part not in {"", ".", ".."} for part in value.split("/")), "unsafe public path")
    return value


def no_symlinks(path):
    require(all(not item.is_symlink() for item in (path, *path.parents)), "symlink source refused")


def read(path):
    no_symlinks(path)
    require(path.is_file(), "regular source file required")
    return path.read_bytes()


def pointer(value, path):
    require(isinstance(path, str) and path.startswith("/"), "invalid JSON pointer")
    for part in path.split("/")[1:]:
        require(not re.search(r"~(?![01])", part), "invalid JSON pointer escape")
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            require(re.fullmatch(r"0|[1-9][0-9]*", part) is not None, "invalid array selector")
            value = value[int(part)]
        else:
            value = value[part]
    return value


def escaped(value):
    return value.replace("~", "~0").replace("/", "~1")


class Inputs:
    def __init__(self, route_path, repo):
        raw = read(route_path)
        require(sha(raw) == REVIEWED_ROUTE_SHA256, "private route raw pin differs")
        self.route = parse(raw)
        route = self.route
        require(route["version"] == "som.ts065-baseline-route.private.v1" and route["classification"] == PRIVATE, "wrong private route kind")
        require(route["distribution_approval"] == route["policy_approval"] == "none", "route cannot grant approval")
        require(route["public_repository"] == "faberline/som" and route["public_source_revision"] == PUBLIC_REVISION, "public source authority differs")
        require(route["counts"] == COUNTS, "named scope counts differ")
        self.public, self.local, self.roles = {}, {}, {}
        for row in route["public_sources"]:
            path = relative(row["path"])
            require(path not in self.public and row["revision"] == PUBLIC_REVISION, "duplicate public source or revision differs")
            data = read(repo / path)
            require(sha(data) == digest(row["raw_sha256"]), "public source pin differs")
            self.public[path] = (data, {"path": path, "revision": PUBLIC_REVISION, "raw_sha256": row["raw_sha256"]})
        require(len(self.public) == 36 and {RELEASE, P1, P2, FIXTURES, EXAMPLES + "index.json"} <= self.public.keys(), "public source set incomplete")
        require(self.public[RELEASE][1]["raw_sha256"] == PUBLIC_RELEASE_SHA256, "public release raw pin differs")
        self.release = self.public_doc(RELEASE)
        require(route["public_release_anchor"] == self.public[RELEASE][1], "public release anchor differs")
        require(route["public_case_sources"] == [self.public[P1][1], self.public[P2][1]], "case inventory anchors differ")
        for row in route["local_accepted_sources"]:
            path = Path(row["path"])
            require(path.is_absolute() and ".." not in path.parts and path not in self.local, "invalid private source path")
            data = read(path)
            require(sha(data) == digest(row["raw_sha256"]) and type(row["bytes"]) is int and len(data) == row["bytes"], "local source pin differs")
            require(row["roles"] and len(row["roles"]) == len(set(row["roles"])), "source roles incomplete")
            self.local[path] = (data, row)
            for role in row["roles"]:
                require(isinstance(role, str) and role not in self.roles and role != "public release", "duplicate source role")
                self.roles[role] = path
        require(len(self.local) == 97, "source pin count differs")
        for data, row in self.local.values():
            require(row["accepted_bindings"], "source lacks an acceptance binding")
            for binding in row["accepted_bindings"]:
                require(set(binding) == {"source_role", "pointer", "rule", "digest"}, "binding fields differ")
                source = self.release if binding["source_role"] == "public release" else self.doc(binding["source_role"])
                expected = digest(pointer(source, binding["pointer"]))
                require(expected == digest(binding["digest"]), "binding authority differs")
                require(binding["rule"] in {"raw sha256", "canonical JSON sha256"}, "unknown digest rule")
                actual = sha(data) if binding["rule"] == "raw sha256" else sha(canonical(parse(data)))
                require(actual == expected, "raw or canonical source binding differs")
        def reaches_release(path, pending):
            require(path not in pending, "cyclic acceptance chain")
            for binding in self.local[path][1]["accepted_bindings"]:
                parent = binding["source_role"]
                if parent != "public release":
                    require(parent in self.roles, "missing acceptance source role")
                    reaches_release(self.roles[parent], pending | {path})
        for path in self.local:
            reaches_release(path, set())

    def public_doc(self, path):
        require(path in self.public, "public source not pinned")
        return parse(self.public[path][0])

    def raw(self, role):
        require(role in self.roles, "source role not pinned")
        return self.local[self.roles[role]][0]

    def doc(self, role):
        return parse(self.raw(role))

    def raw_sha(self, role):
        return sha(self.raw(role))


def accepted_receipts(inputs):
    summary, header = inputs.doc("accepted summary"), inputs.doc("accepted header")
    require(summary["passed"] is True and summary["blockers"] == [] and len(summary["steps"]) == 13, "accepted summary is incomplete")
    require(summary["header_sha256"] == inputs.raw_sha("accepted header"), "accepted header binding differs")
    result, indexed, unchecked = [], [], set()
    for pos, step in enumerate(summary["steps"]):
        name, index = STEPS[pos], pos + 1
        require(step["step"] == name and type(step["index"]) is int and step["index"] == index and step["passed"] is True and type(step["exit_code"]) is int and step["exit_code"] == 0, "accepted step status differs")
        role = "accepted receipt: " + name
        receipt = inputs.doc(role)
        require(inputs.raw_sha(role) == step["receipt_sha256"] and receipt["passed"] is True and type(receipt["exit_code"]) is int and receipt["exit_code"] == 0, "receipt status or digest differs")
        output_rows, index_outputs = [], []
        for path, value in receipt["outputs"].items():
            relative(path)
            value = digest(value)
            item = {"output_name_sha256": sha(path.encode()), "digest": value}
            if (name, path) in UNCHECKED:
                unchecked.add((name, path))
                item.update({"kind": "directory", "verification": "unchecked", "digest_rule": "recorded receipt directory digest"})
                index_outputs.append({"relative_path": path, "recorded_directory_digest": value, "digest_rule": "receipt directory digest; tree not recomputed by this route author"})
            else:
                output_role = "accepted output: " + path
                require(inputs.raw_sha(output_role) == value, "receipt output digest differs")
                item.update({"kind": "file", "verification": "verified", "digest_rule": "raw sha256"})
                index_outputs.append({"relative_path": path, "raw_sha256": value, "source_role": output_role})
            output_rows.append(item)
        indexed.append({"index": index, "step": name, "source_role": role, "raw_sha256": step["receipt_sha256"], "passed": True, "exit_code": 0, "outputs": index_outputs})
        result.append({"index": index, "step": name, "raw_sha256": inputs.raw_sha(role), "passed": True, "exit_code": 0, "outputs": output_rows})
    require(indexed == inputs.route["accepted_step_receipts"] and unchecked == UNCHECKED, "receipt index or unchecked scope differs")
    return result, header


def public_ids(inputs):
    rows, result = inputs.route["public127_reconstruction_bindings"], []
    require(len(rows) == 127, "public input identity count differs")
    for index, row in enumerate(rows):
        require(type(row["index"]) is int and row["index"] == index, "public input order differs")
        name, where = identity(row["id"]), row["reconstruction"]
        if set(where) == {"path", "prompt_pointer", "cnl_pointer"}:
            require(where["path"] == FIXTURES, "unexpected public fixture source")
            match = re.fullmatch(r"/cases/(0|[1-9][0-9]*)/expected_prompt", where["prompt_pointer"])
            require(match is not None and where["cnl_pointer"] == f"/cases/{match[1]}/cnl", "public fixture selector differs")
            require(inputs.public_doc(FIXTURES)["cases"][int(match[1])]["id"] == name, "public fixture identity differs")
        else:
            require(set(where) == {"prompt_path", "cnl_path"}, "public example selector fields differ")
            require(where["prompt_path"] in inputs.public and where["cnl_path"] in inputs.public, "example sources not pinned")
            matches = [case for case in inputs.public_doc(EXAMPLES + "index.json")["cases"] if EXAMPLES + case["prompt"] == where["prompt_path"] and EXAMPLES + case["cnl"] == where["cnl_path"]]
            require(len(matches) == 1 and "PUBLIC_" + matches[0]["id"] == name, "public example identity differs")
        result.append(name)
    require(len(set(result)) == 127, "duplicate public identity")
    return set(result)


def report_rows(report, identities, hashes, statuses, hashes_by_check=None):
    require(report["passed"] is True and report["status"] == "passed", "accepted report status differs")
    result = []
    for index, row in enumerate(report["rows"]):
        require(row["passed"] is True and row["status"] == "passed", "accepted row status differs")
        check_hashes = (hashes_by_check or {}).get(row.get("check"), ())
        row_hashes = hashes + check_hashes
        recorded_fields = {key for key in row if key.endswith("_sha256") or key.endswith("_status")}
        require(recorded_fields == set(row_hashes + statuses), "accepted digest/status field schema differs")
        item = {"row_index": index, **{key: identity(row[key]) for key in identities}, "passed": True, "status": row["status"]}
        for key in row_hashes:
            item[key] = None if row[key] is None else digest(row[key])
        if check_hashes == RS_REBASED_HASHES:
            # The owning checker records the actual and expected complete file-map hashes.
            require(item["files_sha256"] is not None and item["files_sha256"] == item["recorded_files_sha256"], "accepted rebased file-map hashes differ")
        for key in statuses:
            value = row[key]
            require(value is None or type(value) is int and 100 <= value <= 599, "invalid HTTP status")
            item[key] = value
        result.append(item)
    return result


def baselines(inputs):
    p1, p2 = inputs.public_doc(P1), inputs.public_doc(P2)
    phase1 = [identity(row["id"]) for group in ["public_examples", "version_positive_fixtures", "coverage_cases", "legacy_in_scope"] for row in p1[group] if row.get("rust", "in_scope") == "in_scope"]
    phase2 = [identity(row["id"]) for row in p2["rows"]]
    refused = {identity(row["id"]) for row in p2["rows"] if row["phase2_expected"]["result"] == "refusal"}
    legacy = [identity(row["id"]) for row in p1["legacy_in_scope"]] + [identity(row["id"]) for row in p2["rows"] if row["kind"] == "legacy"]
    require(len(phase1) == len(set(phase1)) == 115 and len(phase2) == len(set(phase2)) == 584 and len(legacy) == len(set(legacy)) == 544 and len(refused) == 2, "inventory named scope differs")
    py_role, rs_role = "accepted output: python-baseline/report.json", "accepted output: rust-baseline/report.json"
    py = report_rows(inputs.doc(py_role), ["source", "id", "variant"], PY_HASHES, PY_STATUSES)
    rs = report_rows(inputs.doc(rs_role), ["phase", "id", "check"], RS_HASHES, RS_STATUSES, {"rebased_files": RS_REBASED_HASHES})
    require(py == inputs.route["python_baseline"]["rows"] and rs == inputs.route["rust_baseline"]["rows"], "indexed baseline row differs from accepted report")
    py_keys = [(row["source"], row["id"], row["variant"]) for row in py]
    py_expected = {(source, name, variant) for source, names in [("public", public_ids(inputs)), ("legacy", legacy)] for name in names for variant in ["none", "python"]}
    rs_keys = [(row["phase"], row["id"], row["check"]) for row in rs]
    rs_expected = {(phase, name, check) for phase, names in [("phase1", phase1), ("phase2", phase2)] for name in names for check in (["compile_equal"] if phase == "phase2" and name in refused else ["compile_equal", "baseline_bundle_refused", "rebased_files"])}
    require(len(py_keys) == len(set(py_keys)) == 1342 and set(py_keys) == py_expected, "Python named result scope differs")
    require(len(rs_keys) == len(set(rs_keys)) == 2093 and set(rs_keys) == rs_expected, "Rust named result scope differs")
    require(all(row["qa_compile_request_sha256"] == row["baseline_compile_request_sha256"] and row["qa_compile_response_sha256"] == row["baseline_compile_response_sha256"] for row in py), "accepted Python equality differs")
    return {"python": {"report_sha256": inputs.raw_sha(py_role), "rows": py}, "rust": {"report_sha256": inputs.raw_sha(rs_role), "rows": rs}}


def descriptor(value):
    require(isinstance(value, dict) and set(value) == {"id", "sha256"}, "descriptor fields differ")
    return {"id": identity(value["id"]), "sha256": digest(value["sha256"])}


def descriptor_leaves(assets):
    require(isinstance(assets, dict) and assets and set(assets) <= ASSETS, "asset fields differ")
    result = []
    for key, value in assets.items():
        if key == "semantic_contracts":
            require(isinstance(value, list), "semantic contract list required")
            items = enumerate(value)
        else:
            items = [(None, value)]
        for index, item in items:
            result.append({"pointer": "/bundle/assets/" + key + ("" if index is None else "/" + str(index)), **descriptor(item)})
    return result


def accepted_descriptors(inputs, header):
    health = header["candidate_before"]["health"]
    values = {key: digest(health[key]) for key in HEALTH_HASHES}
    values.update({key: version(health[key]) for key in HEALTH_VERSIONS})
    rust = health["rust_target"]
    require(set(rust) == {"assets", "bundle_version", "dsl_version", "formatter_sha256", "formatter_version"} and set(rust["assets"]) == RUST_ASSETS, "Rust health descriptor fields differ")
    values["rust_target"] = {"assets": {key: descriptor(value) for key, value in rust["assets"].items()}, "bundle_version": version(rust["bundle_version"]), "dsl_version": version(rust["dsl_version"]), "formatter_sha256": digest(rust["formatter_sha256"]), "formatter_version": version(rust["formatter_version"])}
    require(values == inputs.route["old_descriptor_health"]["values"], "indexed health descriptors differ")
    manifest = inputs.doc("accepted candidate asset manifest")
    raw_manifest, canonical_manifest = inputs.raw_sha("accepted candidate asset manifest"), sha(canonical(manifest))
    backend = sha(canonical(manifest["backend"]))
    require(canonical_manifest == values["asset_manifest_sha256"] and backend == values["backend_sha256"], "manifest canonical descriptors differ")
    metadata = inputs.route["candidate_manifest_metadata"]
    require(metadata["raw_file_sha256"] == raw_manifest and metadata["canonical_json_sha256"] == canonical_manifest and metadata["backend_canonical_json_sha256"] == backend and metadata["backend"] == manifest["backend"] and metadata["packaged_file_hashes"] == manifest["files"], "indexed manifest metadata differs")
    for name, expected in manifest["files"].items():
        relative(name)
        require(inputs.raw_sha("accepted packaged asset: " + name) == digest(expected), "packaged asset differs")
    source = inputs.doc("accepted candidate source.json")
    require(inputs.raw_sha("accepted source archive") == digest(source["archive_sha256"]), "source archive differs")
    require(inputs.route["candidate_source_manifest"]["file_hashes"] == source["files"], "source manifest file map differs")
    for path, value in source["files"].items():
        relative(path)
        digest(value)
    return {"health": values, "candidate": {"binary_sha256": inputs.raw_sha("accepted candidate som"), "runtime_json_sha256": inputs.raw_sha("accepted candidate runtime.json"), "build_json_sha256": inputs.raw_sha("accepted candidate build.json"), "source_json_sha256": inputs.raw_sha("accepted candidate source.json"), "source_archive_sha256": inputs.raw_sha("accepted source archive"), "source_file_count": len(source["files"]), "source_file_hash_map_sha256": sha(canonical(source["files"])), "manifest_raw_file_sha256": raw_manifest, "manifest_canonical_json_sha256": canonical_manifest, "backend_canonical_json_sha256": backend}}


def canaries(inputs):
    prereq = inputs.doc("accepted prerequisite record")
    references, observations = [], []
    for family, target in FAMILIES.items():
        require(len(prereq[family]) == 2, "canary case count differs")
        for ci, case in enumerate(prereq[family]):
            for si, step in enumerate(case["steps"]):
                for side in ["request", "response"]:
                    ref = step.get(side)
                    if not isinstance(ref, dict) or not {"path", "sha256"} <= ref.keys():
                        continue
                    role = f"accepted canary {family}:{ci}:{si}:{side}"
                    require(inputs.raw_sha(role) == digest(ref["sha256"]), "canary source digest differs")
                    references.append({"family": family, "case_name": case["name"], "step_name": step["name"], "side": side, "source_role": role, "raw_sha256": ref["sha256"]})
                    if side != "response":
                        continue
                    response = inputs.doc(role)
                    bundle = response.get("bundle") if isinstance(response, dict) else None
                    if not isinstance(bundle, dict):
                        continue
                    require(step["name"] in {"compile-json", "compile-cnl"} and bundle.get("target", "python") == target, "canary compile scope differs")
                    hashes = bundle["hashes"]
                    require(set(hashes) == {"dsl_sha256", "prompt_sha256", "payload_sha256"}, "canary hash fields differ")
                    observations.append({"family": family, "case_name": identity(case["name"]), "step_name": step["name"], "source_role": role, "raw_response_sha256": inputs.raw_sha(role), "bundle_version": version(bundle["version"]), "target": target, "prompt_version": version(bundle["prompt"]["version"]), "hashes": {key: digest(value) for key, value in hashes.items()}, "asset_descriptors": descriptor_leaves(bundle["assets"])})
    require(len(references) == 54 and references == inputs.route["accepted_canary_source_references"], "canary source index differs")
    require(len(observations) == 12 and observations == inputs.route["accepted_canary_descriptor_observations"], "canary descriptor index differs")
    return [{key: value for key, value in row.items() if key != "source_role"} for row in observations]


def build_export(route_path, repo):
    inputs = Inputs(route_path, repo)
    receipts, header = accepted_receipts(inputs)
    baselines_result = baselines(inputs)
    return {"version": "som.ts065-accepted-response-hashes.v1", "public_repository": "faberline/som", "public_source_revision": PUBLIC_REVISION, "reviewed_private_route_raw_sha256": REVIEWED_ROUTE_SHA256, "public_release_anchor": inputs.public[RELEASE][1], "public_source_files": sorted((row for _raw, row in inputs.public.values()), key=lambda row: row["path"]), "counts": dict(COUNTS), "verified_local_source_pin_count": len(inputs.local), "accepted_summary_sha256": inputs.raw_sha("accepted summary"), "accepted_header_sha256": inputs.raw_sha("accepted header"), "accepted_prerequisite_sha256": inputs.raw_sha("accepted prerequisite record"), "accepted_receipts": receipts, "baselines": baselines_result, "accepted_descriptors": accepted_descriptors(inputs, header), "accepted_canary_observations": canaries(inputs), "response_hash_authority": {"qa": "latest accepted issue65 candidate replies", "baseline": "older comparison control replies; retain separately", "projection": "none", "raw_success_bodies": "not retained in accepted baseline runs"}, "verification_scope": {"source_file_pins": "verified", "directory_digests": "unchecked", "canary_payload_hash_recomputation": "unrun", "new_compatibility_policy": "unapproved", "current_product_acceptance": "unrun"}, "product_acceptance_passed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        no_symlinks(args.output)
        require(not args.output.exists(), "output already exists")
        result = build_export(args.route, args.repo)
        raw = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        with args.output.open("xb") as output:
            output.write(raw)
        print(json.dumps({"status": "exported", "raw_sha256": sha(raw), "bytes": len(raw), "counts": COUNTS}))
        return 0
    except Refusal as error:
        print("TS065 export refused: " + str(error), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeError):
        print("TS065 export refused: invalid or unavailable source metadata", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
