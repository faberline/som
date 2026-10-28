#!/usr/bin/env python3
"""Export separate, hash-only historical TS125 evidence. Never run a candidate."""
from __future__ import annotations

import argparse
from collections import Counter
import io
import json
from pathlib import Path
import sys
import tarfile

import export_ts065_goldens as old

INVENTORY = "som-rs/contracts/typescript-output-v1/case-inventory.json"
TARGET_EXAMPLES = "som-rs/contracts/typescript-output-v1/target-examples.json"
NEGATIVE_CASES = "som-rs/contracts/typescript-output-v1/negative-cases.json"
BEFORE_NEGATIVE_SHA256 = "c96fb4e6b3b1d86fdd2c1af12724f3e3585878c375c96c96deb057757f771543"
CANDIDATE_BINARY = "2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c"
CANDIDATE_COMMIT = "8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af"
SOURCE_PINS = {
    INVENTORY: "f45572b155c89808bae9153c7a833da779ad043fb4c0a34002de9c9ef26dee0c",
    TARGET_EXAMPLES: "fac94a5cb3295c5cb26c39f541ef395f6fa6ca1a99f25ca8cb5b3ff16f1aa6c5",
    "som-rs/scripts/typescript_output_acceptance/check_target.py": "d58aceb3139f714f8659fc7a13de1a4c06b170a23b5ec6aeabb197e9ff666203",
    "som-rs/scripts/typescript_output_acceptance/check_bundles.py": "8a00d50826f3e0cc74ada9c9ab5d0e93e3bdd591e1f35629e660b613bf619a44",
    "som-rs/scripts/typescript_output_acceptance/run_acceptance.py": "54ee04d9c0e514f1dd3d2df384818aa594a4aadb21616996ba64554dca1f4119",
    "som-rs/som-targets/qa/check_ts_products.py": "fb52e35d465779a31affad4078e9209050f55ade3803e6931881bc523e2271d1",
    "som-rs/behavior/typescript/host.py": "bab520b2874fcb5c770b694ac63a511e18a61a06c25f475ea34da04901c8f310",
}
REPORT_PINS = {
    "target/report.json": "968068b7b78a211149191c1c2f85c0d118aa6b9ff5431113730a668e5fc0b14f",
    "bundles/report.json": "cd7e74587323ac12681d4cde4ae4a2ad3227fffa0b178297b9fc9cd51a25b441",
    "bundles/products/manifest.json": "dd4fee81f904ab65889e90703b013b0886eae928c39c49c1aa9ec1442dee8bdb",
    "ts-products/report.json": "c3f30717e24287836b4ee4c1f8218123289331c27f9c2002014158e1560b58ad",
}
GROUPS = ("public_examples", "version_positive_fixtures", "coverage_cases", "name_cases", "legacy_in_scope")
GATES = ("install", "typecheck", "format_check", "test", "lock_unchanged", "schema_equivalence", "parity", "strict_refusals", "startup")
FORMATS = {"json": "som.prompt-json.v1", "cnl": "som.cnl.v1"}
require, sha, canonical, parse, read = old.require, old.sha, old.canonical, old.parse, old.read


def value_sha(value):
    return sha(canonical(value))


def source_doc(inputs, repo, path):
    raw = read(repo / path)
    require(sha(raw) == SOURCE_PINS[path], "TS public source digest differs")
    source = inputs.doc("accepted candidate source.json")
    if path in {INVENTORY, TARGET_EXAMPLES}:
        require(source["files"].get(path) == sha(raw), "TS frozen input lacks accepted source binding")
    return parse(raw)


def before_negative_doc(inputs):
    # Read one exact regular member. Never extract or execute archive contents.
    with tarfile.open(fileobj=io.BytesIO(inputs.raw("accepted source archive"))) as archive:
        matches = [member for member in archive.getmembers() if member.name == NEGATIVE_CASES]
        require(len(matches) == 1 and matches[0].isfile(), "accepted negative source member differs")
        raw = archive.extractfile(matches[0]).read()
    require(sha(raw) == BEFORE_NEGATIVE_SHA256 == inputs.doc("accepted candidate source.json")["files"].get(NEGATIVE_CASES), "accepted negative source pin differs")
    return parse(raw)


def inventory_rows(inventory):
    require(inventory["version"] == "som.typescript-output-case-inventory.v1", "TS inventory kind differs")
    result = []
    for group in GROUPS:
        for index, row in enumerate(inventory[group]):
            if row.get("typescript", "in_scope") == "in_scope":
                result.append({"id": old.identity(row["id"]), "group": group, "group_index": index, "source": row})
    require(len(result) == 125 and sum(r["group"] == "legacy_in_scope" for r in result) == 100, "TS125 inventory scope differs")
    require(len({r["id"] for r in result}) == 125, "TS inventory identities repeat")
    return result


def tree_snapshot(root):
    old.no_symlinks(root)
    require(root.is_dir(), "accepted response or product directory missing")
    result = {}
    for path in sorted(root.rglob("*")):
        old.no_symlinks(path)
        if path.is_file():
            result[old.relative(path.relative_to(root).as_posix())] = sha(read(path))
    return result


def tree_digest(files):
    return sha("".join(f"{name}\0{digest}\n" for name, digest in sorted(files.items())).encode())


def response(root, relative, expected):
    old.relative(relative)
    raw = read(root / relative)
    require(sha(raw) == old.digest(expected), "accepted raw response digest differs")
    return raw, parse(raw)


def passed_report(doc, count):
    require(doc["passed"] is True and doc["status"] == "passed" and doc["total"] == count and type(doc["total"]) is int,
            "accepted TS report scope or status differs")
    require(len(doc["cases"]) == count and doc["passed_count"] == count and doc["failed_count"] == doc["not_run_count"] == 0 and doc["problems"] == [], "accepted TS report incomplete")
    for row in doc["cases"]:
        require(row["passed"] is True and row["status"] == "passed" and type(row["http_status"]) is int and row["http_status"] in {200, 400, 422}, "accepted TS row status differs")
        old.identity(row["id"])
        old.digest(row["request_sha256"])
        old.digest(row["response_sha256"])


def target_form(row, names, negatives):
    if row["section"] == "legacy":
        return "json"
    if row["section"] == "name_cases":
        require(row["id"] in names, "target name case not frozen")
        return names[row["id"]]["form"]
    if row["section"] == "negative_cases":
        require(row["id"] in negatives, "target negative case not frozen")
        source = negatives[row["id"]]
        require(sha(source["body"].encode()) == row["request_sha256"], "negative request source association differs")
        return source["form"]
    if row["section"] == "python_equivalence" and row["id"].removesuffix("-python-equivalence") in names:
        return names[row["id"].removesuffix("-python-equivalence")]["form"]
    for form in FORMATS:
        if f"-{form}-" in row["id"]:
            return form
    raise old.Refusal("target form lacks a frozen association")


def target_rows(report, root, names, negatives):
    result = []
    for index, row in enumerate(report["cases"]):
        _raw, body = response(root, row["response_file"], row["response_sha256"])
        form = target_form(row, names, negatives)
        require(form in FORMATS, "target form differs")
        projected = {"row_index": index, "section": old.identity(row["section"]), "id": row["id"],
                     "method": "POST", "endpoint": "/v1/compile", "form": form, "format": FORMATS.get(form),
                     "request_sha256": row["request_sha256"], "request_hash_authority": "recorded_target_report",
                     "raw_request_file_available": False, "http_status": row["http_status"],
                     "response_sha256": row["response_sha256"], "raw_response_file_verified": True}
        if row["http_status"] != 200:
            require(body.get("status") == "error" and isinstance(body.get("code"), str), "target refusal body differs")
            projected["diagnostic_code"] = old.identity(body["code"])
        if "inventory_index" in row:
            require(type(row["inventory_index"]) is int, "legacy target index differs")
            projected["inventory_index"] = row["inventory_index"]
            projected["prompt_sha256"] = old.digest(row["prompt_sha256"])
            require(type(row["in_scope"]) is bool, "legacy target scope differs")
            projected["historical_in_scope"] = row["in_scope"]
        result.append(projected)
    require(Counter(r["http_status"] for r in result) == {200: 190, 400: 12, 422: 589}, "historical target status counts differ")
    return result


def bundle_doc(raw):
    doc = parse(raw)
    require(set(doc) == {"status", "bundle"} and doc["status"] == "ok", "compile response shape differs")
    bundle = doc["bundle"]
    require(set(bundle) == {"version", "target", "prompt", "dsl", "assets", "hashes"} and bundle["version"] == "som.dsl-bundle.v2" and bundle["target"] == "typescript", "TS bundle shape differs")
    hashes = {"prompt_sha256": value_sha(bundle["prompt"]), "dsl_sha256": value_sha(bundle["dsl"]),
              "payload_sha256": value_sha({k: v for k, v in bundle.items() if k != "hashes"})}
    require(bundle["hashes"] == hashes, "accepted TS bundle canonical hash differs")
    return bundle


def direct_compile_sources(case, form, bundle_index, bundle_row, target):
    sources = []
    if form == bundle_row["forms"][0]:
        sources.append({"report": "bundles/report.json", "row_index": bundle_index, "request_sha256": bundle_row["request_sha256"]})
    for row in target:
        same = (case["group"] == "legacy_in_scope" and row["section"] == "legacy" and row["id"] == case["id"] and row.get("inventory_index") == case["source"]["inventory_index"])
        same |= (case["group"] == "name_cases" and row["section"] == "name_cases" and row["id"] == case["id"])
        same |= (case["group"] not in {"legacy_in_scope", "name_cases"} and row["section"] in {"target_examples", "in_scope"} and row["id"] == case["id"] + f"-{form}-typescript")
        if same and row["form"] == form:
            require(row["http_status"] == 200, "applicable target row did not succeed")
            sources.append({"report": "target/report.json", "row_index": row["row_index"], "request_sha256": row["request_sha256"], "response_sha256": row["response_sha256"]})
    hashes = {r["request_sha256"] for r in sources}
    require(len(hashes) <= 1, "accepted direct compile request hashes disagree")
    return (next(iter(hashes)) if hashes else None), sources


def case_rows(inventory, bundle_report, manifest, products, target, root, trees):
    inventory = inventory_rows(inventory)
    bundle_rows = [(i, r) for i, r in enumerate(bundle_report["cases"]) if r["section"] == "bundle_checks"]
    names = [r["id"] for r in inventory]
    require(names == [r["id"] for _, r in bundle_rows] == [r["id"] for r in manifest["products"]] == [r["id"] for r in products["products"]], "TS125 exact ordered identities differ")
    require(products["summary"]["no_sampling_complete"] is True and products["summary"]["all_passed"] is True and products["summary"]["total_products"] == products["summary"]["passed_products"] == 125, "historical TS product scope incomplete")
    result, bundles = [], {}
    for index, (case, (report_index, row), item, product) in enumerate(zip(inventory, bundle_rows, manifest["products"], products["products"])):
        require(row["forms"] in (["json", "cnl"], ["cnl", "json"], ["json"]), "accepted compile forms differ")
        source = case["source"]
        if case["group"] == "legacy_in_scope":
            require(row["inventory_index"] == source["inventory_index"], "TS legacy inventory index differs")
        require((row["single_form"] is None) == (len(row["forms"]) == 2), "accepted single form declaration differs")
        require(row["single_form"] in {None, "only json form exists in target-examples.json name_cases"}, "single form metadata differs")
        operations, compiled = [], []
        for form in row["forms"]:
            expected_files = [f"responses/bundle_checks__{case['id']}__compile_{form}_{n}.json" for n in (1, 2)]
            raws = [read(root / file) for file in expected_files]
            require(all(trees["bundles/responses"].get(file.removeprefix("responses/")) == sha(raw) for file, raw in zip(expected_files, raws)), "compile response lacks receipt tree binding")
            require(raws[0] == raws[1], "accepted repeat compile bytes differ")
            bundle = bundle_doc(raws[0])
            compiled.append(raws[0])
            bundles[case["id"]] = bundle
            request_sha, bindings = direct_compile_sources(case, form, report_index, row, target)
            require(all(b.get("response_sha256", sha(raws[0])) == sha(raws[0]) for b in bindings), "target and bundle response association differs")
            operations.append({"form": form, "format": FORMATS[form], "method": "POST", "endpoint": "/v1/compile",
                               "request_sha256": request_sha, "request_hash_authority": "recorded_report" if bindings else "unavailable",
                               "request_bindings": bindings, "raw_request_file_available": False,
                               "source_reconstructed_request_sha256": None, "source_reconstruction": "unrun_before_writer_not_in_accepted_archive",
                               "http_status": 200, "http_status_authority": "passed_bundle_check_for_all_declared_compile_forms", "response_sha256": sha(raws[0]), "raw_response_file_verified": True, "repeat_response_sha256": [sha(r) for r in raws]})
        require(len(set(compiled)) == 1, "accepted CNL and JSON bundles differ")
        prompt_sha = value_sha(item["prompt"])
        require(value_sha(bundle["prompt"]) == prompt_sha, "product manifest prompt association differs")
        if "prompt_sha256" in source:
            require(source["prompt_sha256"] == prompt_sha, "frozen TS prompt identity differs")
        require(item["prompt"]["version"] == product["version"], "historical product prompt version differs")
        a_raw, assembled = response(root, row["response_file"], row["response_sha256"])
        require(row["response_file"] == f"responses/bundle_checks__{case['id']}__assemble_1.json" and row["http_status"] == 200, "bundle report response is not assemble")
        require(read(root / f"responses/bundle_checks__{case['id']}__assemble_2.json") == a_raw, "accepted repeat assembly differs")
        require(set(assembled) == {"status", "files"} and assembled["status"] == "ok" and len(assembled["files"]) == 11 and all(isinstance(v, str) for v in assembled["files"].values()), "historical assembled file map differs")
        file_hashes = {old.relative(k): sha(v.encode()) for k, v in assembled["files"].items()}
        require(sha(json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode()) == product["files_sha256"], "historical product complete file hash map differs")
        prefix = case["id"] + "/"
        actual = {k[len(prefix):]: v for k, v in trees["bundles/products"].items() if k.startswith(prefix)}
        require(actual == file_hashes, "retained product files differ from accepted assembly")
        require(Path(item["product"]) == root / "products" / case["id"], "manifest product locator differs")
        require(product["passed"] is True and product["failed_gates"] == product["not_run_gates"] == [] and product["gate_statuses"] == {gate: "passed" for gate in GATES}, "historical TS product gates incomplete")
        result.append({"row_index": index, "id": case["id"], "inventory_group": case["group"], "inventory_group_index": case["group_index"],
                       **({"legacy_inventory_index": source["inventory_index"]} if case["group"] == "legacy_in_scope" else {}),
                       "prompt_sha256": prompt_sha, "prompt_version": old.version(product["version"]), "forms": row["forms"], "single_form": row["single_form"],
                       "compile": operations, "bundle_hashes": bundle["hashes"], "bundle_assets": {k: [old.descriptor(d) for d in v] if isinstance(v, list) else old.descriptor(v) for k, v in bundle["assets"].items()},
                       "assemble": {"method": "POST", "endpoint": "/v1/assemble", "format": "som.dsl-bundle.v2", "request_sha256": None,
                                    "request_hash_authority": "unavailable_not_recorded_by_bundle_writer", "raw_request_file_available": False,
                                    "source_reconstructed_request_sha256": None, "source_reconstruction": "unrun_before_writer_not_in_accepted_archive",
                                    "http_status": 200, "http_status_authority": "recorded_bundle_report", "response_sha256": sha(a_raw), "raw_response_file_verified": True, "repeat_response_sha256": [sha(a_raw), sha(a_raw)]},
                       "product": {"files": file_hashes, "files_sha256": product["files_sha256"], "file_count": 11, "raw_files_verified": True, "historical_gate_statuses": product["gate_statuses"]},
                       "report_bindings": {"bundle_row_index": report_index, "manifest_row_index": index, "product_row_index": index}})
    return result, bundles


def refusal_rows(report, root):
    rows = [(i, r) for i, r in enumerate(report["cases"]) if r["section"] == "assemble_refusals"]
    require([r["id"] for _, r in rows] == [f"A{i}" for i in range(1, 13)], "assemble refusal ordered scope differs")
    result = []
    for index, row in rows:
        _raw, body = response(root, row["response_file"], row["response_sha256"])
        require(body["status"] == "error" and body["code"] == row["expected"]["code"] and row["http_status"] == row["expected"]["http_status"], "assemble refusal status differs")
        result.append({"report_row_index": index, "id": row["id"], "method": "POST", "endpoint": "/v1/assemble", "format": "som.dsl-bundle.v2",
                       "request_sha256": row["request_sha256"], "request_hash_authority": "recorded_assemble_refusal_report", "raw_request_file_available": False,
                       "http_status": row["http_status"], "response_sha256": row["response_sha256"], "raw_response_file_verified": True,
                       "diagnostic_code": old.identity(body["code"]), "prompt_pointer": row["expected"]["prompt_pointer"]})
    return result


def canary_rows(inputs, bundles):
    result = []
    prereq = inputs.doc("accepted prerequisite record")
    for index, case in enumerate(prereq["typescript_canaries"]):
        require(case["name"] in bundles, "TS canary is outside frozen125")
        for step_index, step in enumerate(case["steps"]):
            if step["name"] not in {"compile-json", "compile-cnl", "assemble"}:
                continue
            raw = inputs.raw(f"accepted canary typescript_canaries:{index}:{step_index}:request")
            reply = inputs.raw(f"accepted canary typescript_canaries:{index}:{step_index}:response")
            require(sha(raw) == step["request"]["sha256"] and sha(reply) == step["response"]["sha256"], "TS raw canary pin differs")
            body = parse(raw)
            name = step["name"]
            endpoint = "/v1/assemble" if name == "assemble" else "/v1/compile"
            require(step["kind"] == "http" and step["path"] == endpoint, "TS canary HTTP route differs")
            if name == "assemble":
                require(set(body) == {"bundle"} and body["bundle"] == bundles[case["name"]], "TS canary assemble bundle association differs")
            else:
                form = name.removeprefix("compile-")
                key = "prompt" if form == "json" else "source"
                require(set(body) == {"format", key, "target"} and body["format"] == FORMATS[form] and body["target"] == "typescript", "TS canary request format differs")
                require(body[key] == bundles[case["name"]]["prompt"] if form == "json" else isinstance(body[key], str), "TS canary input association differs")
                require(bundle_doc(reply) == bundles[case["name"]], "TS canary compile bundle association differs")
            result.append({"case_id": case["name"], "step": name, "method": "POST", "endpoint": endpoint,
                           "format": "som.dsl-bundle.v2" if name == "assemble" else FORMATS[name.removeprefix("compile-")],
                           "request_sha256": sha(raw), "request_hash_authority": "accepted_prerequisite_record_raw_file", "raw_request_file_verified": True,
                           "response_sha256": sha(reply), "raw_response_file_verified": True, "http_status": None, "http_status_authority": "not_recorded_in_prerequisite",
                           "prerequisite_case_index": index, "prerequisite_step_index": step_index})
    require(len(result) == 6, "TS canary operation scope differs")
    return result


def build_export(route_path, repo):
    inputs = old.Inputs(route_path, repo)
    receipts, header = old.accepted_receipts(inputs)
    descriptors = old.accepted_descriptors(inputs, header)
    require(descriptors["candidate"]["binary_sha256"] == CANDIDATE_BINARY and inputs.doc("accepted candidate source.json")["commit"] == CANDIDATE_COMMIT, "accepted before candidate differs")
    for path, expected in SOURCE_PINS.items():
        require(sha(read(repo / path)) == expected, "review source pin differs")
    inventory = source_doc(inputs, repo, INVENTORY)
    names = {r["id"]: r for r in source_doc(inputs, repo, TARGET_EXAMPLES)["name_cases"]}
    negatives = {r["id"]: r for r in before_negative_doc(inputs)["cases"]}
    docs = {}
    for name, expected in REPORT_PINS.items():
        role = "accepted output: " + name
        require(inputs.raw_sha(role) == expected, "TS report raw pin differs")
        docs[name] = inputs.doc(role)
    target, bundle = docs["target/report.json"], docs["bundles/report.json"]
    passed_report(target, 791)
    passed_report(bundle, 137)
    roots = {"target": inputs.roles["accepted output: target/report.json"].parent, "bundles": inputs.roles["accepted output: bundles/report.json"].parent}
    trees, tree_receipts = {}, []
    for step, leaf in (("target", "responses"), ("bundles", "responses"), ("bundles", "products")):
        name = step + "/" + leaf
        files = tree_snapshot(roots[step] / leaf)
        expected = inputs.doc("accepted receipt: " + step)["outputs"][name]
        require(tree_digest(files) == expected, "accepted TS directory digest differs")
        trees[name] = files
        tree_receipts.append({"name": name, "raw_tree_sha256": expected, "file_count": len(files), "verification": "verified", "rule": "sorted_relative_path_NUL_raw_sha256_LF"})
    history = target_rows(target, roots["target"], names, negatives)
    rows, bundles = case_rows(inventory, bundle, docs["bundles/products/manifest.json"], docs["ts-products/report.json"], history, roots["bundles"], trees)
    refusals, canaries = refusal_rows(bundle, roots["bundles"]), canary_rows(inputs, bundles)
    for name, files in trees.items():
        step, leaf = name.split("/")
        require(tree_snapshot(roots[step] / leaf) == files, "accepted TS directory changed during export")
    direct = sum(op["request_sha256"] is not None for row in rows for op in row["compile"])
    require(direct == 140 and sum(len(r["compile"]) for r in rows) == 246, "direct TS compile request authority scope differs")
    return {"version": "som.ts125-accepted-before-hashes.v1", "public_repository": "faberline/som", "public_source_revision": old.PUBLIC_REVISION,
            "reviewed_source_index_raw_sha256": old.REVIEWED_ROUTE_SHA256, "public_release_anchor": inputs.public[old.RELEASE][1],
            "accepted_summary_sha256": inputs.raw_sha("accepted summary"), "accepted_header_sha256": inputs.raw_sha("accepted header"),
            "accepted_receipts": receipts, "accepted_before": {"commit": CANDIDATE_COMMIT, **descriptors["candidate"]},
            "review_source_files": [{"path": p, "revision": old.PUBLIC_REVISION, "raw_sha256": h, "accepted_candidate_source_manifest_bound": p in {INVENTORY, TARGET_EXAMPLES}} for p, h in SOURCE_PINS.items()],
            "accepted_archive_source_files": [{"path": NEGATIVE_CASES, "raw_sha256": BEFORE_NEGATIVE_SHA256, "accepted_candidate_source_manifest_bound": True}],
            "accepted_report_files": [{"name": p, "raw_sha256": h} for p, h in REPORT_PINS.items()], "verified_directory_receipts": tree_receipts,
            "counts": {"named_cases": 125, "nonlegacy_cases": 25, "legacy_cases": 100, "compile_forms": 246, "directly_recorded_compile_request_hashes": direct,
                       "compile_request_hashes_unavailable": 246-direct, "bundle_assemble_request_hashes_unavailable": 125, "product_files": 1375,
                       "target_history_rows": 791, "assemble_refusals": 12, "separate_raw_canary_operations": 6,
                       "duplicate_prompt_rows_preserved": sum(n-1 for n in Counter(r["prompt_sha256"] for r in rows).values())},
            "cases": rows, "historical_target_rows": history, "assemble_refusals": refusals, "separate_accepted_canary_operations": canaries,
            "verification_scope": {"accepted_raw_responses": "verified", "complete_product_file_hash_maps": "verified", "ordered_frozen125_associations": "verified",
                                   "raw_request_file_availability_scope": "that_check_output; separate accepted canary files are listed independently",
                                   "missing_request_hashes": "unavailable", "before_request_reconstruction": "unrun", "current_candidate_execution": "unrun", "current_product_acceptance": "unrun",
                                   "current_react_scope": "not_derived_from_historical_typescript_refusals", "compatibility_policy": "unsealed"},
            "product_acceptance_passed": False}


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
        print(json.dumps({"status": "exported", "raw_sha256": sha(raw), "bytes": len(raw), "counts": result["counts"]}))
        return 0
    except old.Refusal as error:
        print("TS125 export refused: " + str(error), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeError):
        print("TS125 export refused: invalid or unavailable source metadata", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
