#!/usr/bin/env python3
"""Map every frozen Rust case to public requests. Run on GitHub; never accept products.

CNL is copied, never parsed here. A matching frozen prompt hash does not prove
that a CNL body parses to that prompt. Missing companion forms remain explicit.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

from export_goldens import Refusal, case_id, digest_text, no_symlinks, parse, relative, require, sha, value_sha
from reconstruct_public_inputs import SUPPLEMENT_REQUEST_PINS

GOLDENS_SHA256 = "ea573d3c842d367b32538ad8b9fec01608b4f05f676a07a80d9b9acf81a69a8b"
PUBLIC_REVISION = "ed56f2cedff497ad9853866c5b4fc10e4b2f8fa2"
P1 = "som-rs/contracts/rust-output-v1/case-inventory.json"
P2 = "som-rs/contracts/rust-output-v1/phase2/case-inventory.json"
NEGATIVE = "som-rs/contracts/rust-output-v1/negative-cases.json"
COVERAGE = "som-rs/tests/rust-output-v1/cases/index.json"
COUNTS = {"rust_phase1": 115, "rust_phase2": 584}
REFUSALS = {"RS12_CNL_V3_FILTER_LIMIT", "RS13_JSON_V3_FILTER_OFFSET"}
# Sealed public inputs, including all six coverage prompt/CNL pairs.
SOURCE_PINS = {
    "som-rs/contracts/rust-output-v1/case-inventory.json": "fc6fbec822623cb9bdc0a6e11c2a72b6f0dfac3c41e8b7c1e35830090eb7d2a5",
    "som-rs/contracts/rust-output-v1/phase2/case-inventory.json": "b2d13ec6ede88ee6175ba12abd7b608453330030abb184c66a37c886d1e8e7e6",
    "som-rs/contracts/rust-output-v1/negative-cases.json": "9bb36d689ad4be26e608ac8be779a1a8bb8db043d37788b5819c9fe1bf402e22",
    "som-rs/tests/rust-output-v1/cases/index.json": "b49af2a7ba842e5dd810d8d010fcf93436474df7f49f1c569367bed1bc94ed1b",
    "som-rs/tests/rust-output-v1/cases/cov-v1-types.prompt.json": "e3a960561e6315900af067cbe3e62d2736efb96419d58cfaa1ca1a968347237c",
    "som-rs/tests/rust-output-v1/cases/cov-v1-types.cnl": "22e6cb0aacd219cb47478dca4ff5f244b63ae605de399f08a16bb62093ec9ce7",
    "som-rs/tests/rust-output-v1/cases/cov-v2-types.prompt.json": "1ac4b8c37b1c19930ffdd6bbdf236a8c50b858541d44c4d7a7dba09e8cebac53",
    "som-rs/tests/rust-output-v1/cases/cov-v2-types.cnl": "a2512cdbbea998a8f051160166e3c1d2f4b43ae5d7bbcbe5e6a0cd211a9b7c1d",
    "som-rs/tests/rust-output-v1/cases/cov-v3-types.prompt.json": "931db289eea1da333b9b39fb27349c526b967e91ad3457fa6ccb967e5dbfff23",
    "som-rs/tests/rust-output-v1/cases/cov-v3-types.cnl": "554eb27bbd5c894fa7ccd473b45734a33753b1ec32395ccb5f73133b0a71a12c",
    "som-rs/tests/rust-output-v1/cases/cov-v4-types.prompt.json": "4516fc930150a1c4cec7650b2d6c687d1065843e06cea40b3704124eb4ee124e",
    "som-rs/tests/rust-output-v1/cases/cov-v4-types.cnl": "cb7f5fb994d6c7bfbdff549e71286211cd1d3f7177b91bdbeeb34337229b7724",
    "som-rs/tests/rust-output-v1/cases/cov-v5-min.prompt.json": "6c169522307102d28d73ef0a292cd17ce68b0f11dac367ca96b5d9cb273cb347",
    "som-rs/tests/rust-output-v1/cases/cov-v5-min.cnl": "627623f91944b13158487086efec5653948be6c2e77a341e0d0ba9410d291e5d",
    "som-rs/tests/rust-output-v1/cases/cov-v5-max.prompt.json": "47657a7899b56c99420d281f04bf9aa56568ab228206de614dfd85a24b939f4d",
    "som-rs/tests/rust-output-v1/cases/cov-v5-max.cnl": "97cb49f506fb16fb9da2ea203b5a114ea68766ec46ebb69cdb4d63f012deb16c"
}


def compact(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def named(rows, count):
    require(len(rows) == len({case_id(row["id"]) for row in rows}) == count, "named case scope differs")
    return {row["id"]: row for row in rows}


class Public:
    def __init__(self, repo, goldens):
        self.repo, self.pins = repo, dict(SOURCE_PINS)
        rows = goldens["public_source_files"]
        require(len(rows) == len({row["path"] for row in rows}) == 35, "sealed public source scope differs")
        for row in rows:
            path = str(relative(row["path"]))
            require(row["revision"] == PUBLIC_REVISION, "sealed public source revision differs")
            digest = digest_text(row["raw_sha256"])
            require(path not in self.pins or self.pins[path] == digest, "public source pins conflict")
            self.pins[path] = digest
        self.verify()

    def raw(self, path):
        relative(path)
        require(path in self.pins, "Rust source is not pinned")
        file = self.repo / path
        no_symlinks(file)
        raw = file.read_bytes()
        require(sha(raw) == self.pins[path], "Rust source digest differs")
        return raw

    def document(self, path):
        return parse(self.raw(path))

    def verify(self):
        for path in self.pins:
            self.raw(path)


def load_goldens(path):
    no_symlinks(path)
    raw = path.read_bytes()
    require(sha(raw) == GOLDENS_SHA256, "golden export is not sealed")
    doc = parse(raw)
    require(doc["version"] == "som.public-golden-hashes.v1" and doc["public_repository"] == "faberline/som"
            and doc["public_source_revision"] == PUBLIC_REVISION and doc["product_acceptance_passed"] is False,
            "golden identity differs")
    return doc


class Reused:
    """Recheck actual input bytes against the sealed Python golden identities."""
    def __init__(self, root, goldens):
        self.root, self.by_cohort, self.typed, self.cnl = root, {}, {}, {}
        self.checked_files = {}
        self.known_request_counts = {"python_public": 0, "python_legacy": 0, "supplement_subset": 0}
        no_symlinks(root / "manifest.json")
        raw = (root / "manifest.json").read_bytes()
        self.manifest_sha256 = sha(raw)
        report = parse(raw)
        require(report["version"] == "som.public-input-reconstruction.v2" and report["typed_input_set_ready"] is True
                and report["golden_export_sha256"] == GOLDENS_SHA256 and report["source_revision"] == PUBLIC_REVISION
                and report["product_acceptance_passed"] is False, "Python input manifest identity differs")
        for cohort, count in [("python_public", 127), ("python_legacy", 544)]:
            rows, expected = report["cohorts"][cohort], goldens["cohorts"][cohort]
            named(rows, count)
            named(expected, count)
            loaded = {}
            for index, (row, golden) in enumerate(zip(rows, expected)):
                require(row["id"] == golden["id"] and row["index"] == golden["index"] == index
                        and row["state"] == "reconstructed", "Python input order differs")
                bodies, locators = {}, {}
                for label, form in [("compile-json", "som.prompt-json.v1"), ("compile-cnl", "som.cnl.v1")]:
                    location = row["requests"][label]
                    path = f"{cohort}/{index:04d}/{label}.request.raw"
                    require(location["path"] == path, "Python request locator differs")
                    raw = self.read(path, digest_text(location["sha256"]))
                    # The runtime manifest is mutable. Recheck every recorded
                    # request byte hash against an independent committed pin.
                    recorded = []
                    if cohort == "python_public":
                        require(set(golden["request_sha256"]) == {"compile-json", "compile-cnl"},
                                "known public request byte scope differs")
                        recorded.append(golden["request_sha256"][label])
                    elif label == "compile-json":
                        if "typed_request_sha256" in golden:
                            recorded.append(golden["typed_request_sha256"])
                        if golden["id"] in SUPPLEMENT_REQUEST_PINS:
                            prompt_pin, request_pin = SUPPLEMENT_REQUEST_PINS[golden["id"]]
                            require(digest_text(prompt_pin) == golden["prompt_sha256"],
                                    "known supplement request prompt binding differs")
                            recorded.append(request_pin)
                            self.known_request_counts["supplement_subset"] += 1
                    if recorded:
                        require(all(sha(raw) == digest_text(expected) for expected in recorded),
                                "known frozen Python request bytes differ")
                        self.known_request_counts[cohort] += 1
                    body = parse(raw)
                    field = "prompt" if label == "compile-json" else "source"
                    require(set(body) == {"format", field} and body["format"] == form, "Python request envelope differs")
                    bodies[label] = body
                    locators[label] = {"cohort": cohort, "id": row["id"], "index": index,
                                       "path": path, "raw_sha256": sha(raw)}
                prompt, cnl = bodies["compile-json"]["prompt"], bodies["compile-cnl"]["source"]
                require(isinstance(prompt, dict) and isinstance(cnl, str)
                        and value_sha(prompt) == row["prompt_sha256"] == golden["prompt_sha256"]
                        and sha(cnl.encode()) == row["cnl_sha256"] == golden["cnl_sha256"],
                        "Python request prompt or CNL digest differs")
                entry = {"prompt": prompt, "cnl": cnl, "golden": golden, "locators": locators}
                loaded[row["id"]] = entry
                self.typed.setdefault(golden["prompt_sha256"], (prompt, locators["compile-json"]))
                self.cnl.setdefault(golden["prompt_sha256"], (cnl, locators["compile-cnl"]))
            self.by_cohort[cohort] = loaded
        require(self.known_request_counts == {"python_public": 254, "python_legacy": 454, "supplement_subset": 11},
                "known frozen Python request byte scope differs")
        self.verify()

    def read(self, path, expected):
        relative(path)
        file = self.root / path
        no_symlinks(file)
        raw = file.read_bytes()
        require(sha(raw) == expected, "Python request bytes differ")
        self.checked_files[path] = expected
        return raw

    def verify(self):
        no_symlinks(self.root / "manifest.json")
        require(sha((self.root / "manifest.json").read_bytes()) == self.manifest_sha256, "Python manifest changed")
        for path, expected in list(self.checked_files.items()):
            self.read(path, expected)


def source_locator(path, pointer, *, raw_sha256=None):
    return {"path": path, "source_file_sha256": SOURCE_PINS[path], "pointer": pointer,
            **({"value_raw_sha256": raw_sha256} if raw_sha256 is not None else {})}


def write_request(directory, label, raw, provenance):
    file = directory / (label + ".request.raw")
    with file.open("xb") as stream:
        stream.write(raw)
    return {"path": file.name, "raw_sha256": sha(raw), "provenance": provenance}


def map_inputs(repo, inputs, goldens, out, tested_sha):
    require(re.fullmatch(r"[0-9a-f]{40}", tested_sha) is not None, "tested revision must be an exact Git SHA")
    require(not out.exists() and out != repo and repo not in out.parents and out != inputs and inputs not in out.parents,
            "mapping output must be new and outside source/input trees")
    no_symlinks(out)
    sources, reused = Public(repo, goldens), Reused(inputs, goldens)
    p1, p2 = sources.document(P1), sources.document(P2)["rows"]
    selected = [(group, row) for group in ["public_examples", "version_positive_fixtures", "coverage_cases", "legacy_in_scope"]
                for row in p1[group] if row.get("rust", "in_scope") == "in_scope"]
    require(dict(Counter(group for group, _ in selected)) == {"public_examples": 5, "version_positive_fixtures": 4,
                                                              "coverage_cases": 6, "legacy_in_scope": 100}, "phase1 group scope differs")
    phase1_gold = named(goldens["cohorts"]["rust_phase1"], 115)
    phase2_gold = named(goldens["cohorts"]["rust_phase2"], 584)
    require(set(phase1_gold) == set(named([row for _, row in selected], 115))
            and set(phase2_gold) == set(named(p2, 584)), "frozen Rust named scope differs")
    require(dict(Counter(row["kind"] for row in p2)) == {"legacy": 444, "cnl_fixture": 115, "public_example": 3, "rs": 22},
            "phase2 kind scope differs")
    require({row["id"] for row in p2 if row["phase2_expected"]["result"] == "refusal"} == REFUSALS
            and sum(row["phase2_expected"]["result"] == "product" for row in p2) == 582,
            "phase2 product/refusal scope differs")
    coverage = named(sources.document(COVERAGE)["cases"], 6)
    negatives = sources.document(NEGATIVE)["cases"]
    negative_rows = [(index, row) for index, row in enumerate(negatives) if row["id"].startswith("RS")]
    rs_frozen = {row["id"]: row for row in p2 if row["kind"] == "rs"}
    require(len(negative_rows) == 22 and set(named([row for _, row in negative_rows], 22)) == set(rs_frozen)
            and dict(Counter(row["form"] for _, row in negative_rows)) == {"cnl": 13, "json": 9}, "RS named/form scope differs")
    originals = {}
    for index, row in negative_rows:
        identity, form = row["id"], row["form"]
        body, control = parse(row["body"]), parse(row["python_control_body"])
        field, format_name = ("prompt", "som.prompt-json.v1") if form == "json" else ("source", "som.cnl.v1")
        require(set(body) == {"format", field, "target"} and body["target"] == "rust"
                and body["format"] == format_name and control == {key: value for key, value in body.items() if key != "target"},
                "RS native/control envelope differs")
        expected_hash = digest_text(rs_frozen[identity]["prompt_sha256"])
        location = source_locator(NEGATIVE, f"/cases/{index}/body", raw_sha256=sha(row["body"].encode()))
        if form == "json":
            require(isinstance(body["prompt"], dict) and value_sha(body["prompt"]) == expected_hash, "RS typed prompt digest differs")
            reused.typed.setdefault(expected_hash, (body["prompt"], location))
        else:
            require(isinstance(body["source"], str), "RS CNL source is not text")
            # This is a frozen expected association, not a fresh CNL parse proof.
            reused.cnl.setdefault(expected_hash, (body["source"], location))
        originals[identity] = (index, row)
    out.mkdir(parents=True, exist_ok=False)
    result = {name: [] for name in COUNTS}

    def emit(cohort, index, identity, prompt_hash, prompt, cnl, origins, golden, context, native=None):
        directory = out / cohort / f"{index:04d}"
        directory.mkdir(parents=True, exist_ok=False)
        requests = {}
        for label, value, field, format_name in [("json", prompt, "prompt", "som.prompt-json.v1"), ("cnl", cnl, "source", "som.cnl.v1")]:
            if value is None:
                requests[label] = None
                continue
            raw = compact({"format": format_name, field: value, "target": "rust"})
            if native is not None and label == native[1]["form"]:
                raw = native[1]["body"].encode()
            item = write_request(directory, label, raw, origins[label])
            item["path"] = (directory / item["path"]).relative_to(out).as_posix()
            requests[label] = item
        entry = {"id": identity, "index": index, "expected_prompt_sha256": prompt_hash,
                 "canonical_typed_prompt_sha256": value_sha(prompt) if prompt is not None else None,
                 "typed_prompt_proof": "public typed value digest matched frozen prompt" if prompt is not None else
                                       "deferred; only raw public CNL is independently available",
                 "cnl_source_sha256": sha(cnl.encode()) if cnl is not None else None,
                 "cnl_semantic_prompt_proof": "unrun; owned GitHub compiler must prove CNL/typed equivalence",
                 "requests": requests, "expected_files_map_sha256": golden["files_map_sha256"], **context}
        if native is not None:
            nindex, negative = native
            control = write_request(directory, "python-control-native", negative["python_control_body"].encode(),
                                    source_locator(NEGATIVE, f"/cases/{nindex}/python_control_body"))
            control["path"] = (directory / control["path"]).relative_to(out).as_posix()
            entry["python_control_native"] = control
            entry["python_control_expected"] = negative["python_control_expected"]
            entry["native_form"] = negative["form"]
            entry["frozen_negative_diagnostic"] = {"http_status": negative["http_status"], "expected": negative["expected"],
                                                   "current_phase2_authority": identity in REFUSALS,
                                                   "source": source_locator(NEGATIVE, f"/cases/{nindex}/expected")}
        result[cohort].append(entry)

    for index, (group, frozen) in enumerate(selected):
        identity, golden = frozen["id"], phase1_gold[frozen["id"]]
        if group == "coverage_cases":
            row = coverage[identity]
            base = Path(COVERAGE).parent
            prompt_path, cnl_path = (base / row["prompt"]).as_posix(), (base / row["cnl"]).as_posix()
            raw_prompt, raw_cnl = sources.raw(prompt_path), sources.raw(cnl_path)
            require(sha(raw_prompt) == row["prompt_file_sha256"] == frozen["prompt_file_sha256"] == golden["prompt_file_sha256"]
                    and sha(raw_cnl) == row["cnl_sha256"] == frozen["cnl_sha256"], "coverage source binding differs")
            prompt, cnl = parse(raw_prompt), raw_cnl.decode("utf-8")
            prompt_hash = value_sha(prompt)
            origins = {"json": source_locator(prompt_path, ""), "cnl": source_locator(cnl_path, "")}
        else:
            if group == "public_examples":
                # The frozen phase1 golden stores a raw prompt-file hash, not a canonical hash.
                path = frozen["prompt"]
                raw = sources.raw(path)
                require(sha(raw) == frozen["prompt_file_sha256"] == golden["prompt_file_sha256"], "example raw prompt binding differs")
                prompt_hash = value_sha(parse(raw))
            else:
                prompt_hash = frozen["prompt_sha256"]
                require(golden["prompt_sha256"] == prompt_hash, "phase1 canonical prompt binding differs")
            if group == "legacy_in_scope":
                source = reused.by_cohort["python_legacy"][identity]
                require(source["golden"]["index"] == frozen["inventory_index"], "phase1 legacy position differs")
            else:
                source = next((entry for entry in reused.by_cohort["python_public"].values()
                               if value_sha(entry["prompt"]) == prompt_hash and sha(entry["cnl"].encode()) == frozen["cnl_sha256"]), None)
                require(source is not None, "phase1 public counterpart missing")
            prompt, cnl, origins = source["prompt"], source["cnl"], {"json": source["locators"]["compile-json"], "cnl": source["locators"]["compile-cnl"]}
            require(value_sha(prompt) == prompt_hash, "phase1 reused prompt differs")
        emit("rust_phase1", index, identity, prompt_hash, prompt, cnl, origins, golden,
             {"group": golden["group"], "inventory_group": group, "expected": {"result": "product", "http_status": 200},
              "assembly_variant": "standard", "expected_source": source_locator(P1, f"/{group}/{p1[group].index(frozen)}")})
    for index, frozen in enumerate(p2):
        identity, golden, kind = frozen["id"], phase2_gold[frozen["id"]], frozen["kind"]
        for key in ["kind", "family", "group", "checker", "standard", "prompt_sha256"]:
            require(golden[key] == frozen[key], "phase2 frozen/golden scope differs")
        expected = frozen["phase2_expected"]
        require(golden["expected"] == {key: value for key, value in expected.items() if key in {"result", "rule", "code", "http_status"}},
                "phase2 current expectation differs from sealed golden")
        context = {key: frozen[key] for key in ["kind", "family", "group", "checker", "standard"]}
        context.update(expected={**expected, "http_status": 200 if expected["result"] == "product" else expected["http_status"]},
                       expected_source=source_locator(P2, f"/rows/{index}/phase2_expected"), assembly_variant=golden.get("assembly_variant", "standard"))
        prompt_hash, native = frozen["prompt_sha256"], None
        if kind == "rs":
            native = originals[identity]
            require((identity in REFUSALS) == (expected["result"] == "refusal"), "RS refusal set differs")
            if identity in REFUSALS:
                negative = native[1]
                pointer = negative["expected"]["prompt_pointer"].removeprefix("/prompt") if negative["form"] == "json" else negative["expected"]["prompt_pointer"]
                require(expected["code"] == negative["expected"]["code"] == "INVALID_VALUE"
                        and expected["http_status"] == negative["http_status"] == 400
                        and expected["detail"] == negative["expected"]["cause"] and expected["pointer"] == pointer,
                        "current name-collision refusal differs")
            typed, text = reused.typed.get(prompt_hash), reused.cnl.get(prompt_hash)
            prompt, cnl = typed[0] if typed else None, text[0] if text else None
            origins = {"json": typed[1] if typed else None, "cnl": text[1] if text else None}
            # Keep the native raw body, even when another matching case supplies a companion.
            if native[1]["form"] == "json":
                prompt = parse(native[1]["body"])["prompt"]
            else:
                cnl = parse(native[1]["body"])["source"]
            origins[native[1]["form"]] = source_locator(NEGATIVE, f"/cases/{native[0]}/body", raw_sha256=sha(native[1]["body"].encode()))
        else:
            cohort = "python_legacy" if kind == "legacy" else "python_public"
            source = reused.by_cohort[cohort][identity]
            require(value_sha(source["prompt"]) == prompt_hash and source["golden"]["index"] == frozen["position"], "phase2 reused identity differs")
            prompt, cnl = source["prompt"], source["cnl"]
            origins = {"json": source["locators"]["compile-json"], "cnl": source["locators"]["compile-cnl"]}
            if kind == "legacy":
                require(context["assembly_variant"] == source["golden"]["variant"], "phase2 assembly variant differs")
        emit("rust_phase2", index, identity, prompt_hash, prompt, cnl, origins, golden, context, native)
    sources.verify()
    reused.verify()
    require(all(len(result[name]) == count for name, count in COUNTS.items()), "Rust mapping is incomplete")
    rows = result["rust_phase1"] + result["rust_phase2"]
    missing_json = [row["id"] for row in rows if row["requests"]["json"] is None]
    missing_cnl = [row["id"] for row in rows if row["requests"]["cnl"] is None]
    require(len(missing_json) == 9 and len(missing_cnl) == 5
            and all(any(row["requests"].values()) for row in rows), "RS companion-form scope differs")
    report = {"version": "som.public-rust-input-mapping.v1", "tested_sha": tested_sha,
              "golden_export_sha256": GOLDENS_SHA256, "python_input_manifest_sha256": reused.manifest_sha256,
              "known_reused_python_request_byte_checks": reused.known_request_counts,
              "public_source_revision": PUBLIC_REVISION, "public_source_pins": sources.pins,
              "request_mapping_complete": True, "full_input_set_ready": False, "product_acceptance_passed": False,
              "state": "all699 named original requests mapped;14 companion forms and compiler semantic proofs remain unrun",
              "compiler_semantic_checks": "unrun", "product_suites": "unrun", "behavior_gates": "unrun",
              "counts": {**COUNTS, "named_total": 699, "rs_native_cnl": 13, "rs_native_json": 9,
                         "canonical_typed_prompt_rows": 690, "typed_prompt_deferred": 9, "cnl_companion_deferred": 5,
                         "both_forms_supplied": 685, "requests_supplied": 1384, "phase2_products": 582, "phase2_refusals": 2},
              "deferred": {"typed_prompt_from_cnl": missing_json, "cnl_from_typed_prompt": missing_cnl,
                           "rule": "Use owned GitHub compiler/serializer checks, retain exact native requests and frozen refusal authority; do not infer semantic proof from this mapper."},
              "cohorts": result}
    with (out / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--goldens", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tested-sha", required=True)
    args = parser.parse_args(argv)
    try:
        require(".." not in args.out.parts, "mapping output traversal refused")
        report = map_inputs(args.repo.resolve(strict=True), args.inputs.resolve(strict=True), load_goldens(args.goldens), args.out.absolute(), args.tested_sha)
        print(json.dumps({"state": report["state"], "counts": report["counts"], "product_acceptance_passed": False}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, IndexError, StopIteration) as error:
        print("refused: " + (str(error) if isinstance(error, Refusal) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
