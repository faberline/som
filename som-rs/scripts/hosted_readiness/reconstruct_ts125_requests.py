#!/usr/bin/env python3
"""Hash-only reconstruction of accepted TS125 before request recipes. No HTTP."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

import export_ts065_goldens as old

BEFORE_MAP = "som-rs/scripts/hosted_readiness/inputs/ts125-baseline-hashes-v1.json"
BEFORE_MAP_SHA256 = "7009c29b93714dc1a827236b4ebeb255efc99c91c61460bc7a5ded49c902c868"
HEADER_SHA256 = "086d83c57e90ec3c2da85b23d450987bbe2e9b2a6a2bb955ab2cb9d67119355a"
TOOL_REVISION = "bfdb678c7798d40ac067e456ae6bc76b6604b3a4"
INVENTORY = "som-rs/contracts/typescript-output-v1/case-inventory.json"
EXAMPLES = "som-rs/contracts/typescript-output-v1/target-examples.json"
FIXTURES = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
COVERAGE = "som-rs/tests/rust-output-v1/cases/index.json"
WRITERS = {
    "som-rs/scripts/typescript_output_acceptance/check_bundles.py": "8a00d50826f3e0cc74ada9c9ab5d0e93e3bdd591e1f35629e660b613bf619a44",
    "som-rs/scripts/typescript_output_acceptance/check_target.py": "d58aceb3139f714f8659fc7a13de1a4c06b170a23b5ec6aeabb197e9ff666203",
    "som-rs/scripts/typescript_output_acceptance/check_python_baseline.py": "ebe4fbe58d54ac6e89ea48f11a6a2b622ed450589864e289327ecb6fd8f51180",
    "som-rs/scripts/typescript_output_acceptance/run_acceptance.py": "54ee04d9c0e514f1dd3d2df384818aa594a4aadb21616996ba64554dca1f4119",
    "som-rs/tests/typescript-output-v1/common.py": "77b92f00cdf13d66c54c1069e215bc8de479953887bc853e4316275b02a24497",
    "som-rs/tests/rust-output-v1/build_negative_cases.py": "90930bdee4257f0eb4ca7f627547b2314aea0f798d238ecb1aa42d224291c96b",
}
TOOL_POINTERS = ("/before/tools", "/tools_before", "/after/tools", "/tools_after", "/tools")
require, sha, canonical, parse, read = old.require, old.sha, old.canonical, old.parse, old.read


def compact(value):
    """The accepted compact recipe preserves every object member's input order."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def assemble_request(bundle):
    return canonical({"bundle": bundle})


def source_bytes(repo, path):
    require(path in WRITERS, "writer path is not fixed")
    completed = subprocess.run(["git", "--no-pager", "-C", str(repo), "show", TOOL_REVISION + ":" + path], check=True, capture_output=True)
    require(sha(completed.stdout) == WRITERS[path], "accepted writer Git bytes differ")
    return completed.stdout


def writer_bindings(header, repo):
    result = []
    for path, expected in WRITERS.items():
        for pointer in TOOL_POINTERS:
            record = old.pointer(header, pointer)
            require(record["head"] == TOOL_REVISION and record["files"].get(path) == expected, "accepted before/after writer pin differs")
        raw = source_bytes(repo, path)
        if path.endswith("tests/rust-output-v1/build_negative_cases.py"):
            require(b'def compact(obj: Any) -> str:\n    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))' in raw, "accepted compact recipe differs")
        if path.endswith("check_bundles.py"):
            require(b'assemble_body = canonical_json({"bundle": bundle})' in raw and b'return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))' in raw, "accepted assembly recipe differs")
        result.append({"path": path, "revision": TOOL_REVISION, "raw_sha256": expected, "header_raw_sha256": HEADER_SHA256,
                       "header_pointers": [p + "/files/" + old.escaped(path) for p in TOOL_POINTERS], "execution": "not_executed"})
    return result


def frozen_raw(repo, path, expected):
    old.relative(path)
    raw = read(repo / path)
    require(sha(raw) == old.digest(expected), "frozen input file digest differs")
    return raw


def compile_bundle(raw, expected):
    require(sha(raw) == old.digest(expected), "accepted compile response raw pin differs")
    doc = parse(raw)
    require(set(doc) == {"status", "bundle"} and doc["status"] == "ok", "accepted compile response shape differs")
    bundle = doc["bundle"]
    require(bundle["target"] == "typescript" and bundle["version"] == "som.dsl-bundle.v2", "accepted compile target differs")
    require(bundle["hashes"] == {"prompt_sha256": sha(canonical(bundle["prompt"])), "dsl_sha256": sha(canonical(bundle["dsl"])), "payload_sha256": sha(canonical({k: v for k, v in bundle.items() if k != "hashes"}))}, "accepted bundle canonical proof differs")
    return bundle


def operation(form, before, raw, recipe, bindings):
    recorded = before["request_sha256"]
    reconstructed = sha(raw) if raw is not None else None
    if recorded is not None:
        require(reconstructed == recorded, "reconstructed request differs from direct accepted request hash")
    writers = ["som-rs/scripts/typescript_output_acceptance/check_bundles.py"]
    if not recipe.startswith("canonical_") and recipe != "exact_frozen_name_request_body":
        writers.extend(["som-rs/tests/typescript-output-v1/common.py", "som-rs/tests/rust-output-v1/build_negative_cases.py"])
    if recipe.startswith("legacy_"):
        writers.append("som-rs/scripts/typescript_output_acceptance/check_python_baseline.py")
    return {"form": form, "method": before["method"], "endpoint": before["endpoint"], "format": before["format"],
            "recorded_request_sha256": recorded, "recorded_request_field_changed": False,
            "reconstructed_request_sha256": reconstructed, "reconstructed_request_bytes": len(raw) if raw is not None else None,
            "authority": "reconstructed_not_recorded" if raw is not None else "unrun_unbound_loaded_request_member_order",
            "recipe": recipe, "writer_source_paths": writers, "input_bindings": bindings,
            "direct_recorded_calibration": "matched" if recorded is not None else "not_available",
            "accepted_response_sha256": before["response_sha256"], "accepted_http_status": before["http_status"],
            "accepted_http_status_authority": before["http_status_authority"]}


def standard_request(form, prompt, cnl):
    if form == "json":
        return compact({"format": "som.prompt-json.v1", "prompt": prompt, "target": "typescript"})
    require(form == "cnl" and isinstance(cnl, str), "CNL source is unavailable")
    return compact({"format": "som.cnl.v1", "source": cnl, "target": "typescript"})


def case_operations(case, source, prompt, cnl, before_bundles, input_bindings, legacy_body=None):
    require(sha(canonical(prompt)) == case["prompt_sha256"], "frozen prompt canonical hash differs")
    output = []
    for before in case["compile"]:
        form = before["form"]
        bundle = before_bundles[form]
        require(sha(canonical(bundle["prompt"])) == case["prompt_sha256"], "compile input and accepted bundle prompt differ")
        bindings = list(input_bindings)
        bindings.append({"kind": "accepted_compile_response", "raw_sha256": before["response_sha256"], "member": "/bundle/prompt", "canonical_prompt_sha256": case["prompt_sha256"]})
        group = case["inventory_group"]
        if group == "legacy_in_scope":
            if form == "cnl":
                raw, recipe = None, "legacy_loaded_cnl_request_member_order_unbound"
            else:
                require(isinstance(legacy_body, dict) and set(legacy_body) == {"format", "prompt"} and legacy_body["format"] == "som.prompt-json.v1" and sha(canonical(legacy_body["prompt"])) == case["prompt_sha256"], "legacy typed input body differs")
                raw = compact({**legacy_body, "target": "typescript"})
                recipe = "legacy_append_target_to_loaded_typed_request_preserve_member_order"
        elif group == "name_cases":
            if form == source["form"]:
                raw = source["body"].encode()
                require(sha(raw) == source["body_sha256"], "frozen name request body hash differs")
                recipe = "exact_frozen_name_request_body"
            else:
                require(form == "json" and source["form"] == "cnl", "unsupported name form derivation")
                # Read JSON only. Never parse CNL, call a compiler, or serialize a new prompt.
                raw = standard_request("json", before_bundles["cnl"]["prompt"], None)
                recipe = "compact_json_from_accepted_cnl_compile_bundle_prompt"
        else:
            raw = standard_request(form, prompt, cnl)
            recipe = "compact_header_format_prompt_target" if form == "json" else "compact_header_format_source_target"
        output.append(operation(form, before, raw, recipe, bindings))
    assemble_before = case["assemble"]
    first = before_bundles[case["forms"][0]]
    assembly = operation("bundle", assemble_before, assemble_request(first), "canonical_sorted_recursive_bundle_envelope",
                         [{"kind": "accepted_compile_response", "raw_sha256": case["compile"][0]["response_sha256"], "member": "/bundle", "bundle_hashes": case["bundle_hashes"]}])
    return {"row_index": case["row_index"], "id": case["id"], "inventory_group": case["inventory_group"],
            **({"legacy_inventory_index": case["legacy_inventory_index"]} if "legacy_inventory_index" in case else {}),
            "prompt_sha256": case["prompt_sha256"], "forms": case["forms"], "compile": output, "assemble": assembly}


def calibration(rows, canaries):
    indexed = {r["id"]: r for r in rows}
    direct = [op for r in rows for op in r["compile"] if op["recorded_request_sha256"] is not None]
    require(len(direct) == 140 and all(op["direct_recorded_calibration"] == "matched" for op in direct), "full direct compile calibration scope differs")
    result = []
    for canary in canaries:
        if canary["step"] != "assemble":
            continue
        assembly = indexed[canary["case_id"]]["assemble"]
        require(assembly["reconstructed_request_sha256"] == canary["request_sha256"], "accepted canary assemble encoding calibration differs")
        result.append({"case_id": canary["case_id"], "reconstructed_request_sha256": assembly["reconstructed_request_sha256"], "separate_recorded_canary_request_sha256": canary["request_sha256"], "result": "matched", "authority": "separate_accepted_raw_canary_request; not a bundle-run capture"})
    require(len(result) == 2, "canary assemble calibration scope differs")
    return {"direct_compile_request_hashes": 140, "direct_compile_calibrations": "all_matched", "separate_canary_assemble_calibrations": result}


def build_export(route_path, repo):
    inputs = old.Inputs(route_path, repo)
    old.accepted_receipts(inputs)
    require(inputs.raw_sha("accepted header") == HEADER_SHA256, "accepted header raw pin differs")
    header = inputs.doc("accepted header")
    writers = writer_bindings(header, repo)
    before_raw = read(repo / BEFORE_MAP)
    require(sha(before_raw) == BEFORE_MAP_SHA256, "immutable TS125 before artifact differs")
    before = parse(before_raw)
    require(before["version"] == "som.ts125-accepted-before-hashes.v1" and before["product_acceptance_passed"] is False and len(before["cases"]) == 125, "before map scope differs")
    source_files = inputs.doc("accepted candidate source.json")["files"]
    inv_raw = frozen_raw(repo, INVENTORY, source_files[INVENTORY])
    examples_raw = frozen_raw(repo, EXAMPLES, source_files[EXAMPLES])
    inv, examples = parse(inv_raw), parse(examples_raw)
    named = {r["id"]: r for r in examples["name_cases"]}
    frozen = {r["id"]: r for group in ("public_examples", "version_positive_fixtures", "coverage_cases", "name_cases", "legacy_in_scope") for r in inv[group]}
    fixtures = {r["id"]: r for r in parse(read(repo / FIXTURES))["cases"]}
    coverage_raw = frozen_raw(repo, COVERAGE, header["tools_before"]["files"][COVERAGE])
    coverage = {r["id"]: r for r in parse(coverage_raw)["cases"]}
    baseline = inputs.doc("accepted output: python-baseline/report.json")
    legacy_run = Path(baseline["legacy_run"])
    require(legacy_run.is_absolute() and ".." not in legacy_run.parts, "legacy source locator differs")
    legacy_raw = read(legacy_run / "result.json")
    require(sha(legacy_raw) == baseline["legacy_result_sha256"] == baseline["inputs"]["legacy_result_sha256"], "accepted legacy result binding differs")
    legacy = parse(legacy_raw)
    report_root = inputs.roles["accepted output: bundles/report.json"].parent
    response_tree = before["verified_directory_receipts"][1]
    require(response_tree["name"] == "bundles/responses", "before response tree identity differs")
    # Read/hash the complete directory as data; preserve the accepted receipt rule.
    tree_files = {p.relative_to(report_root / "responses").as_posix(): sha(read(p)) for p in sorted((report_root / "responses").rglob("*")) if p.is_file()}
    require(sha("".join(f"{p}\0{v}\n" for p, v in sorted(tree_files.items())).encode()) == response_tree["raw_tree_sha256"], "accepted raw compile directory differs")
    rows = []
    for case in before["cases"]:
        src = frozen[case["id"]]
        bindings = [{"kind": "frozen_inventory", "raw_sha256": sha(inv_raw), "group": case["inventory_group"], "group_index": case["inventory_group_index"]}]
        bundles = {}
        for op in case["compile"]:
            path = report_root / "responses" / f"bundle_checks__{case['id']}__compile_{op['form']}_1.json"
            bundles[op["form"]] = compile_bundle(read(path), op["response_sha256"])
        group, prompt, cnl, legacy_body = case["inventory_group"], None, None, None
        if group == "public_examples":
            p_raw = frozen_raw(repo, src["prompt"], src["prompt_file_sha256"])
            c_raw = frozen_raw(repo, src["cnl"], src["cnl_sha256"])
            prompt, cnl = parse(p_raw), c_raw.decode("utf-8")
            bindings.extend([{"kind": "frozen_prompt_file", "path": src["prompt"], "raw_sha256": sha(p_raw)}, {"kind": "frozen_cnl_file", "path": src["cnl"], "raw_sha256": sha(c_raw)}])
        elif group == "version_positive_fixtures":
            fixture = fixtures[case["id"]]
            prompt, cnl = fixture["expected_prompt"], fixture["cnl"]
            require(sha(canonical(prompt)) == src["prompt_sha256"] and sha(cnl.encode()) == src["cnl_sha256"], "frozen fixture prompt or CNL differs")
            bindings.append({"kind": "frozen_inventory_value_hashes", "canonical_prompt_sha256": src["prompt_sha256"], "cnl_text_sha256": src["cnl_sha256"], "prompt_member_order_authority": "direct_complete_request_hash_calibration"})
        elif group == "coverage_cases":
            cov = coverage[case["id"]]
            parent = Path(COVERAGE).parent
            p_path, c_path = (parent / cov["prompt"]).as_posix(), (parent / cov["cnl"]).as_posix()
            p_raw = frozen_raw(repo, p_path, src["prompt_file_sha256"])
            c_raw = frozen_raw(repo, c_path, src["cnl_sha256"])
            prompt, cnl = parse(p_raw), c_raw.decode("utf-8")
            bindings.extend([{"kind": "header_bound_coverage_index", "path": COVERAGE, "raw_sha256": sha(coverage_raw)}, {"kind": "frozen_prompt_file", "path": p_path, "raw_sha256": sha(p_raw)}, {"kind": "frozen_cnl_file", "path": c_path, "raw_sha256": sha(c_raw)}])
        elif group == "name_cases":
            src = {**named[case["id"]], "body_sha256": src["body_sha256"]}
            prompt = bundles[case["forms"][0]]["prompt"]
            require(sha(src["body"].encode()) == src["body_sha256"], "frozen name source body differs")
            bindings.append({"kind": "accepted_source_manifest_target_examples", "path": EXAMPLES, "raw_sha256": sha(examples_raw), "body_sha256": src["body_sha256"]})
        else:
            require(group == "legacy_in_scope", "unexpected TS125 group")
            index = case["legacy_inventory_index"]
            record = legacy["records"][index]
            require(record["id"] == case["id"] and record["prompt_sha256"] == case["prompt_sha256"] == src["prompt_sha256"] and record["passed"] is True, "accepted legacy index or prompt differs")
            compile_dir = legacy_run / "compile" / record["prompt_sha256"]
            request_path = compile_dir / "compile-json.request.json"
            if not request_path.is_file():
                request_path = compile_dir / "compile.request.json"
            typed_raw = read(request_path)
            legacy_body = parse(typed_raw)
            prompt = legacy_body.get("prompt")
            c_raw = read(compile_dir / "source.cnl")
            require(sha(c_raw) == record["cnl_sha256"], "accepted legacy CNL source differs")
            bindings.append({"kind": "accepted_legacy_result", "raw_sha256": sha(legacy_raw), "record_index": index, "canonical_prompt_sha256": record["prompt_sha256"], "cnl_text_sha256": record["cnl_sha256"], "result_binding_report": "python-baseline/report.json", "result_binding_pointer": "/legacy_result_sha256"})
            bindings.append({"kind": "loaded_legacy_typed_request", "observed_raw_sha256": sha(typed_raw), "member_order_authority": "direct_complete_TS_request_hash_calibration", "cnl_request_member_order": "unbound"})
        rows.append(case_operations(case, src, prompt, cnl, bundles, bindings, legacy_body))
    calibration_result = calibration(rows, before["separate_accepted_canary_operations"])
    counts = Counter(op["authority"] for row in rows for op in row["compile"])
    require(len(rows) == 125 and sum(len(row["compile"]) for row in rows) == 246 and counts == {"reconstructed_not_recorded": 146, "unrun_unbound_loaded_request_member_order": 100}, "reconstruction named scope differs")
    return {"version": "som.ts125-source-reconstructed-before-requests.v1", "public_repository": "faberline/som", "immutable_before_artifact": {"path": BEFORE_MAP, "raw_sha256": BEFORE_MAP_SHA256},
            "accepted_header_raw_sha256": HEADER_SHA256, "accepted_before_candidate": before["accepted_before"], "writer_source_bindings": writers,
            "counts": {"named_cases": 125, "compile_forms": 246, "compile_reconstructed": 146, "compile_unrun": 100, "assemble_reconstructed": 125, "direct_compile_calibrations": 140, "canary_assemble_calibrations": 2},
            "calibration": calibration_result, "cases": rows,
            "boundaries": {"request_authority": "reconstructed_not_recorded; distinct from recorded fields", "legacy_cnl_gap": "accepted source text hash does not bind the member order of the loaded compile-cnl.request.json object", "before_writer_execution": "unrun", "CNL_parser_or_serializer_execution": "unrun", "current_candidate_inputs_or_outputs": "unused", "current_product_checker": "unused; its current bytes differ from the accepted before checker"},
            "policy_state": "unsealed", "current_product_acceptance": "unrun", "product_acceptance_passed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        old.no_symlinks(args.output)
        require(not args.output.exists(), "output already exists")
        result = build_export(args.route, args.repo)
        raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        with args.output.open("xb") as output:
            output.write(raw)
        print(json.dumps({"raw_sha256": sha(raw), "bytes": len(raw), "counts": result["counts"]}))
        return 0
    except old.Refusal as error:
        print("TS125 reconstruction refused: " + str(error), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeError, subprocess.CalledProcessError):
        print("TS125 reconstruction refused: invalid or unavailable source metadata", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
