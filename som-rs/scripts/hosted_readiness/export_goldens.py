#!/usr/bin/env python3
"""Export hashes from an indexed private baseline route. Never export its bodies."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys

COUNTS = {"python_public": 127, "python_legacy": 544, "rust_phase1": 115, "rust_phase2": 584}
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
PRIVATE = "LOCAL PRIVATE REVIEW — DO NOT UPLOAD"
# This reviewed metadata root binds the private index and its public revision.
# A new route needs independent review and a new committed pin, never an env override.
REVIEWED_ROUTE_SHA256 = "7d6a7ee92f0f574d2df05d75a93ab2e6df79bb3bee23ceb513d6bb4c91adacd7"
PUBLIC_REVISION = "ed56f2cedff497ad9853866c5b4fc10e4b2f8fa2"
DIRECT = "tracked JSONL input; prompt digest and typed request serialization verified"
GENERATED = "public generator; execution and accepted prompt-digest proof unrun"
BLOCKED = "BLOCKED: no verified public reconstruction source"
PUBLIC_RESULT = "public-products-003b/result.json"
LEGACY_RESULT = "legacy544/run-005/result.json"
INVENTORY = "legacy544/inventory-003.json"
RUNS = ("rust-output-050/run-001", "rust-output-055/run-003")
P1 = "som-rs/contracts/rust-output-v1/case-inventory.json"
P2 = "som-rs/contracts/rust-output-v1/phase2/case-inventory.json"
FIXTURES = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
EXAMPLES = "som-rs/contracts/compiler-v1/examples/"
ANCHOR16 = "som-rs/evidence/rust-output-acceptance-2026-10-16.json"
ANCHOR14 = "som-rs/evidence/rust-compiler-acceptance-2026-10-14.json"
ANCHOR20 = "som-rs/evidence/rust-output-acceptance-2026-10-20-phase2.json"
ANCHOR_POINTERS = {
    ANCHOR16: {"/results/python_replay/inputs/public_result_sha256", "/results/python_replay/inputs/legacy_result_sha256",
               "/local_only/header_sha256", "/local_only/summary_sha256"},
    ANCHOR14: {"/gate_receipts/public127/sha256", "/gate_receipts/legacy544/sha256"},
    ANCHOR20: {"/local_only/header_sha256", "/local_only/summary_sha256"},
}
FAMILY_COUNTS = {"legacy100": 100, "legacy214": 214, "quality218": 218, "supplement12": 12}
PHASE1_GROUP_COUNTS = {"public_examples": 5, "cnl_positive_fixtures:version_positive": 4,
                       "coverage_cases": 6, "legacy:legacy100": 100}
PHASE1_PROMPT_FIELDS = {"public_examples": "prompt_file_sha256", "coverage_cases": "prompt_file_sha256",
                        "cnl_positive_fixtures:version_positive": "prompt_sha256", "legacy:legacy100": "prompt_sha256"}
GROUP_COUNTS = {"legacy100": 100, "services14": 14, "compositions120": 120, "blind80": 80,
                "m2-quality218": 218, "m2-supplement12": 12}
PARTITION_COUNTS = {None: 314, "formal114": 114, "dev12": 12, "maximum-matrix": 90,
                    "alternative-plans": 2, "supplement12": 12}
DIRECT_PATHS = {
    "som-core/data/composed/regression.jsonl", "som-core/data/composed/heldout.jsonl",
    "som-core/data/composed/blind.jsonl", "som-core/data/service-regression-v4/heldout.jsonl",
    "som-core/data/flow-v7-r10/formal.jsonl", "som-core/data/flow-v7-r10/dev.jsonl",
}


class Refusal(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    """The frozen Python JSON primitive rule; do not import a project module."""
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        require(math.isfinite(value), "nonfinite JSON number")
        text = repr(value)
        if "e" in text:
            first, exponent = text.split("e", 1)
            sign = "-" if exponent.startswith("-") else "+" if exponent.startswith("+") else ""
            digits = exponent.lstrip("+-")
            text = first + "e" + sign + (digits.zfill(2) if sign == "-" else digits)
        return text
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is list:
        return "[" + ",".join(canonical(item) for item in value) + "]"
    require(type(value) is dict and all(type(key) is str for key in value), "unsupported JSON primitive")
    return "{" + ",".join(canonical(key) + ":" + canonical(value[key]) for key in sorted(value)) + "}"


def value_sha(value):
    return sha(canonical(value).encode())


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON member")
            result[key] = value
        return result
    def constant(_value):
        raise Refusal("nonfinite JSON number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def pointer(value, path):
    for part in path.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def relative(value):
    require(isinstance(value, str) and value and "\\" not in value, "invalid public path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(part not in {"", ".", ".."} for part in value.split("/")), "unsafe public path")
    return path


def no_symlinks(path):
    require(all(not item.is_symlink() for item in (path, *path.parents)), "symlink path refused")


def digest_text(value):
    require(isinstance(value, str) and SHA.fullmatch(value), "invalid SHA-256")
    return value


def case_id(value):
    require(isinstance(value, str) and IDENTITY.fullmatch(value), "invalid case identity")
    return value


class Inputs:
    def __init__(self, route, repo):
        require(route.get("version") == "som.m12-baseline-route.private.v1" and route.get("classification") == PRIVATE,
                "only the private baseline route is accepted; the private review packet is prohibited")
        require(value_sha(route) == REVIEWED_ROUTE_SHA256, "route is not the independently reviewed metadata root")
        require(route.get("public_repository") == "faberline/som", "wrong public source repository")
        revision = route.get("public_source_revision")
        require(revision == PUBLIC_REVISION, "public revision differs from the reviewed source pin")
        self.route, self.repo, self.revision = route, repo, revision
        self.public = {}
        for row in route["public_sources"]:
            path = str(relative(row["path"]))
            require(path not in self.public and row["revision"] == revision, "duplicate or inconsistent public source")
            self.public[path] = digest_text(row["raw_sha256"])
        self.local = {}
        for row in route["local_accepted_sources"]:
            path = Path(row["path"])
            require(path.is_absolute() and ".." not in path.parts and path.name != "som-m12-private-fixture-review.json", "unsafe private source")
            require(path not in self.local, "duplicate private source")
            digest_text(row["raw_sha256"])
            self.local[path] = row
        matches = [path for path in self.local if path.as_posix().endswith("/" + PUBLIC_RESULT)]
        require(len(matches) == 1, "public result source is ambiguous")
        self.root = matches[0].parents[1]
        require(all(self.root in path.parents for path in self.local), "indexed source is outside the accepted baseline")
        self.verify_all()

    def public_bytes(self, path):
        relative(path)
        require(path in self.public, "public source is not indexed")
        file = self.repo / path
        no_symlinks(file)
        raw = file.read_bytes()
        require(sha(raw) == self.public[path], "public source digest mismatch")
        return raw

    def public_json(self, path):
        return parse(self.public_bytes(path))

    def source_bytes(self, relative_path):
        relative(relative_path)
        path = self.root / relative_path
        require(path in self.local, "accepted source is not indexed")
        no_symlinks(path)
        raw = path.read_bytes()
        require(sha(raw) == self.local[path]["raw_sha256"], "accepted source digest mismatch")
        return raw

    def source(self, path):
        return parse(self.source_bytes(path))

    def source_sha(self, path):
        return sha(self.source_bytes(path))

    def verify_all(self):
        for path in self.public:
            self.public_bytes(path)
        for path, row in self.local.items():
            raw = self.source_bytes(path.relative_to(self.root).as_posix())
            for binding in row.get("accepted_bindings", []):
                rule = binding["rule"]
                require(rule in {"raw sha256", "canonical JSON sha256"}, "unknown accepted digest rule")
                actual = sha(raw) if rule == "raw sha256" else value_sha(parse(raw))
                require(actual == digest_text(binding["digest"]), "accepted source binding mismatch")

    def anchored(self, local_path, public_path, public_pointer):
        expected = pointer(self.public_json(public_path), public_pointer)
        require(self.source_sha(local_path) == digest_text(expected), "public acceptance anchor mismatch")

    def anchor_chain(self):
        result = []
        require(len(self.route["accepted_public_anchors"]) == 3, "complete public anchor chain required")
        for row in self.route["accepted_public_anchors"]:
            path = row["path"]
            require(path in ANCHOR_POINTERS, "unexpected public acceptance anchor")
            require(set(row["accepted_digest_pointers"]) == ANCHOR_POINTERS[path], "unexpected acceptance anchor pointers")
            require(row["raw_sha256"] == self.public[path], "anchor source digest differs")
            document = self.public_json(path)
            values = {}
            for key, expected in row["accepted_digest_pointers"].items():
                require(pointer(document, key) == digest_text(expected), "anchor pointer differs")
                values[key] = expected
            result.append({"path": path, "revision": self.revision, "raw_sha256": self.public[path], "accepted_digest_pointers": values})
        require({row["path"] for row in result} == {ANCHOR14, ANCHOR16, ANCHOR20}, "duplicate or missing acceptance anchor")
        return result


def file_hashes(inputs, directory, expected, old_per_file=None):
    raw = base64.b64decode(b"".join(inputs.source_bytes(directory + "/assemble.response.raw.b64").split()), validate=True)
    response = parse(raw)
    require(response.get("status") == "ok" and isinstance(response.get("files"), dict), "accepted assembly response is malformed")
    files = response["files"]
    require(files and all(isinstance(path, str) and isinstance(text, str) for path, text in files.items()), "accepted product is malformed")
    require(value_sha(files) == digest_text(expected), "accepted complete product-map hash mismatch")
    by_path = {path: sha(text.encode()) for path, text in files.items()}
    if old_per_file is not None:
        require(by_path == old_per_file, "accepted per-file hash inventory differs")
    rows = [{"path_sha256": sha(path.encode()), "content_sha256": content} for path, content in by_path.items()]
    rows.sort(key=lambda row: row["path_sha256"])
    return {"files_map_sha256": expected, "raw_assemble_response_sha256": sha(raw), "file_count": len(rows),
            "accepted_per_file_hashes": rows, "per_file_evidence": "derived from accepted raw assembly; complete map hash verified"}


def public_input(inputs, row):
    where = row["reconstruction"]
    if "prompt_pointer" in where:
        require(where["path"] == FIXTURES, "unexpected public fixture source")
        match = re.fullmatch(r"/cases/(\d+)/expected_prompt", where["prompt_pointer"])
        require(match is not None and where["cnl_pointer"] == f"/cases/{match[1]}/cnl", "unexpected fixture selector")
        case = inputs.public_json(FIXTURES)["cases"][int(match[1])]
        require(case["id"] == row["id"], "public fixture identity differs")
        return case["expected_prompt"], case["cnl"], {key: where[key] for key in ["path", "prompt_pointer", "cnl_pointer"]}
    prompt_path, cnl_path = where["prompt_path"], where["cnl_path"]
    matches = [case for case in inputs.public_json(EXAMPLES + "index.json")["cases"]
               if EXAMPLES + case["prompt"] == prompt_path and EXAMPLES + case["cnl"] == cnl_path]
    require(len(matches) == 1 and "PUBLIC_" + matches[0]["id"] == row["id"], "public example identity differs")
    return inputs.public_json(prompt_path), inputs.public_bytes(cnl_path).decode(), {"prompt_path": prompt_path, "cnl_path": cnl_path}


def phase1_prompt_hashes(row):
    group = row["group"]
    require(group in PHASE1_PROMPT_FIELDS, "unexpected Rust phase1 group")
    expected = PHASE1_PROMPT_FIELDS[group]
    fields = {"prompt_file_sha256", "prompt_sha256"}
    require(fields.intersection(row) == {expected}, "Rust phase1 prompt digest fields differ from the group schema")
    return {name: digest_text(row[name]) if name == expected else None for name in sorted(fields)}


def rust_history(inputs):
    reports, summaries = {}, {}
    for run, anchor in zip(RUNS, [ANCHOR16, ANCHOR20]):
        inputs.anchored(run + "/summary.json", anchor, "/local_only/summary_sha256")
        inputs.anchored(run + "/header.json", anchor, "/local_only/header_sha256")
        summary = inputs.source(run + "/summary.json")
        require(summary["status"] == "passed" and inputs.source_sha(run + "/header.json") == summary["header_sha256"], "accepted Rust run binding differs")
        receipts = {}
        for index, step in enumerate(summary["steps"], 1):
            receipt_path = run + f"/receipts/{index:02d}-{step['step']}.json"
            require(inputs.source_sha(receipt_path) == step["receipt_sha256"], "accepted Rust step receipt differs")
            receipts[step["step"]] = inputs.source(receipt_path)
        report_path = run + "/rust-products.json"
        require(receipts["rust-products"]["outputs"]["rust-products.json"] == inputs.source_sha(report_path), "Rust phase1 golden report binding differs")
        reports[run] = inputs.source(report_path)["cases"]
        require(len(reports[run]) == len({row["id"] for row in reports[run]}) == COUNTS["rust_phase1"],
                "Rust phase1 named golden set has duplicate or missing rows")
        require(dict(Counter(row["group"] for row in reports[run])) == PHASE1_GROUP_COUNTS,
                "Rust phase1 group scope differs")
        for row in reports[run]:
            phase1_prompt_hashes(row)
        summaries[run] = receipts
    old = {row["id"]: row["files_sha256"] for row in reports[RUNS[0]]}
    new = {row["id"]: row["files_sha256"] for row in reports[RUNS[1]]}
    require(old == new and len(new) == COUNTS["rust_phase1"], "Rust phase1 named golden set differs")
    return reports[RUNS[1]], summaries[RUNS[1]]


def build_export(route, repo):
    inputs = Inputs(route, repo)
    anchors = inputs.anchor_chain()
    for local, pointer16, pointer14 in [
        (PUBLIC_RESULT, "/results/python_replay/inputs/public_result_sha256", "/gate_receipts/public127/sha256"),
        (LEGACY_RESULT, "/results/python_replay/inputs/legacy_result_sha256", "/gate_receipts/legacy544/sha256"),
    ]:
        inputs.anchored(local, ANCHOR16, pointer16)
        inputs.anchored(local, ANCHOR14, pointer14)
    public = inputs.source(PUBLIC_RESULT)
    legacy = inputs.source(LEGACY_RESULT)
    inventory = inputs.source(INVENTORY)
    require(value_sha(inventory) == legacy["inventory_sha256"], "legacy canonical inventory binding differs")
    require(public["fixture_sha256"] == inputs.public[FIXTURES], "accepted public fixture source binding differs")
    require(public["complete"] is True and public["required"] == public["passed"] == 127, "public acceptance metadata is incomplete")
    require(legacy["status"] == "passed" and legacy["required_job_count"] == legacy["passed_job_count"] == 544, "legacy acceptance metadata is incomplete")
    require(len(route["public127_ordered_reconstruction"]) == len(public["records"]) == COUNTS["python_public"], "public named cohort is incomplete")
    require(len(route["legacy544_ordered_reconstruction"]) == len(legacy["records"]) == len(inventory["groups"]) == COUNTS["python_legacy"], "legacy named cohort is incomplete")
    public_rows, legacy_rows = [], []
    for index, (row, accepted) in enumerate(zip(route["public127_ordered_reconstruction"], public["records"])):
        require(row["id"] == accepted["id"] and accepted["passed"] is True, "public ordered identity differs")
        prompt, cnl, location = public_input(inputs, row)
        require(value_sha(prompt) == row["prompt_sha256"] and sha(cnl.encode()) == row["cnl_sha256"], "public reconstruction digest differs")
        requests = {}
        for label, body in [("compile-json", {"format": "som.prompt-json.v1", "prompt": prompt}), ("compile-cnl", {"format": "som.cnl.v1", "source": cnl})]:
            expected = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            require(inputs.source_bytes(f"public-products-003b/{index:03d}/{label}.request.raw") == expected, "public raw request reconstruction differs")
            requests[label] = sha(expected)
        public_rows.append({"id": case_id(row["id"]), "index": index, "prompt_sha256": row["prompt_sha256"], "cnl_sha256": row["cnl_sha256"],
                            "input_provenance": "verified tracked public source", "public_source": location, "request_sha256": requests,
                            **file_hashes(inputs, f"public-products-003b/{index:03d}", accepted["product_sha256"])})
    dispositions = {DIRECT: "verified_tracked", GENERATED: "public_generator_unrun", BLOCKED: "blocked_no_public_source"}
    checked_blocked, checked_unrun = [], []
    for index, (row, accepted, group) in enumerate(zip(route["legacy544_ordered_reconstruction"], legacy["records"], inventory["groups"])):
        require(row["id"] == accepted["id"] == group["id"] and accepted["passed"] is True, "legacy ordered identity differs")
        require(row["prompt_sha256"] == accepted["prompt_sha256"] == group["prompt_sha256"], "legacy prompt identity differs")
        require(group["gold_product_sha256"] == accepted["product_sha256"], "independent historical golden differs")
        require(row["route"] in dispositions and type(accepted["behavior_expected"]) is bool, "unknown legacy scope or route")
        require(all(accepted[name] == group[name] for name in ["family", "group", "partition", "variant"])
                and accepted["behavior_expected"] == group["behavior"], "legacy inventory scope differs")
        entry = {key: accepted[key] for key in ["family", "group", "partition", "variant", "prompt_sha256", "cnl_sha256", "behavior_expected"]}
        require(entry["family"] in {"legacy100", "legacy214", "quality218", "supplement12"} and entry["variant"] in {"standard", "checked"}, "unexpected legacy scope")
        entry.update(id=case_id(row["id"]), index=index, input_provenance=dispositions[row["route"]])
        if row["route"] == DIRECT:
            sources = []
            for locator in row["reconstruction_sources"]:
                path = locator["path"]
                require(path in DIRECT_PATHS and locator["member"] == "/prompt" and type(locator["line_1based"]) is int and locator["line_1based"] > 0, "invalid tracked legacy locator")
                require(locator["source_file_sha256"] == inputs.public[path], "tracked source binding differs")
                prompt = parse(inputs.public_bytes(path).decode().splitlines()[locator["line_1based"] - 1])["prompt"]
                require(value_sha(prompt) == accepted["prompt_sha256"], "tracked legacy prompt digest differs")
                request = inputs.source_bytes(f"legacy544/run-005/compile/{accepted['prompt_sha256']}/compile-json.request.json")
                expected_request = (json.dumps({"format": "som.prompt-json.v1", "prompt": prompt}, ensure_ascii=False,
                                               indent=2) + "\n").encode()
                require(request == expected_request, "tracked legacy typed request serialization differs")
                entry["typed_request_sha256"] = sha(request)
                sources.append({"path": path, "line_1based": locator["line_1based"], "pointer": "/prompt"})
            require(sources, "direct legacy source is missing")
            entry["public_sources"] = sources
        elif row["route"] == GENERATED:
            require(accepted["family"] == "quality218" and accepted["partition"] == "maximum-matrix", "unexpected generator scope")
            inputs.public_bytes("som-core/scripts/probe_flow_maximum_matrix.py")
            inputs.public_bytes("som-core/scripts/freeze_flow_cases.py")
            entry["public_recipe"] = {"path": "som-core/scripts/probe_flow_maximum_matrix.py", "function": "maximum_matrix", "id_prefix": "m2-",
                                      "source_sha256": inputs.public["som-core/scripts/probe_flow_maximum_matrix.py"],
                                      "constructor": {"path": "som-core/scripts/freeze_flow_cases.py", "function": "requirement",
                                                      "source_sha256": inputs.public["som-core/scripts/freeze_flow_cases.py"]}, "state": "unrun"}
        if accepted["variant"] == "checked":
            if row.get("checked_dsl_route", "").startswith("BLOCKED:"):
                entry["checked_dsl_provenance"] = "blocked_no_public_source"
                checked_blocked.append(entry["id"])
            else:
                require(accepted["family"] == "quality218", "missing checked supplement provenance")
                entry["checked_dsl_provenance"] = "public_reference_recipe_unrun"
                entry["checked_public_recipe"] = {"module": "som_core.flow_compact", "function": "reference", "variant": "checked",
                                                  "state": "unrun; module source hash not indexed in this export"}
                checked_unrun.append(entry["id"])
        entry.update(file_hashes(inputs, f"legacy544/run-005/{index:04d}", accepted["product_sha256"], accepted["product_file_sha256"]))
        legacy_rows.append(entry)
    require(dict(Counter(row["family"] for row in legacy_rows)) == FAMILY_COUNTS
            and dict(Counter(row["group"] for row in legacy_rows)) == GROUP_COUNTS
            and dict(Counter(row["partition"] for row in legacy_rows)) == PARTITION_COUNTS
            and dict(Counter(row["variant"] for row in legacy_rows)) == {"standard": 541, "checked": 3}
            and dict(Counter(row["behavior_expected"] for row in legacy_rows)) == {True: 452, False: 92},
            "legacy family, group, partition, variant or behavior scope differs")
    blocked = [row for row in legacy_rows if row["input_provenance"] == "blocked_no_public_source"]
    require(len(blocked) == 11 and len({row["prompt_sha256"] for row in blocked}) == 10 and len(checked_blocked) == 2, "private blocked scope differs")
    require(len([row for row in legacy_rows if row["input_provenance"] == "public_generator_unrun"]) == 90, "maximum generator scope differs")
    require(len({row["prompt_sha256"] for row in legacy_rows}) == legacy["compile_prompt_count"] == 540, "legacy duplicate/prompt scope differs")
    require({(row["id"], row["prompt_sha256"]) for row in blocked} == {(row["id"], row["prompt_sha256"]) for row in route["blocked11"]}, "blocked input inventory differs")
    require(set(checked_blocked) == {row["id"] for row in route["checked_dsl2"]}, "checked plan inventory differs")
    phase1, receipts = rust_history(inputs)
    frozen1 = inputs.public_json(P1)
    expected1 = [row["id"] for name in ["public_examples", "version_positive_fixtures", "coverage_cases", "legacy_in_scope"]
                 for row in frozen1[name] if row.get("rust", "in_scope") == "in_scope"]
    require(len(expected1) == len(set(expected1)) == COUNTS["rust_phase1"] and set(expected1) == {row["id"] for row in phase1}, "frozen phase1 named scope differs")
    rust1 = [{"id": case_id(row["id"]), "group": row["group"], "version": row["version"], **phase1_prompt_hashes(row),
              "files_map_sha256": digest_text(row["files_sha256"]), "accepted_per_file_hashes": None,
              "per_file_evidence": "not stored in indexed accepted metadata; complete map hash remains authoritative"} for row in phase1]
    phase2_path = RUNS[1] + "/phase2/summary.json"
    require(inputs.source_sha(phase2_path) == receipts["phase2-products"]["outputs"]["phase2/summary.json"], "phase2 summary binding differs")
    phase2_summary = inputs.source(phase2_path)
    require(phase2_summary["complete"] is True and phase2_summary["passed"] == phase2_summary["total"] == 584
            and phase2_summary["failed"] == phase2_summary["missing"] == [], "accepted phase2 product scope is incomplete")
    frozen2 = inputs.public_json(P2)["rows"]
    require(len(frozen2) == len(phase2_summary["case_sha256"]) == COUNTS["rust_phase2"], "phase2 named scope is incomplete")
    require(len({row["id"] for row in frozen2}) == len(frozen2), "duplicate phase2 identity")
    legacy_by_id = {row["id"]: row for row in legacy_rows}
    legacy1 = {row["id"] for row in frozen1["legacy_in_scope"] if row.get("rust", "in_scope") == "in_scope"}
    legacy2 = {row["id"] for row in frozen2 if row["kind"] == "legacy"}
    require(len(legacy1) == 100 and len(legacy2) == 444 and not legacy1 & legacy2
            and legacy1 | legacy2 == set(legacy_by_id), "legacy identities differ from the public frozen named scope")
    rust2 = []
    for frozen_index, frozen in enumerate(frozen2):
        identity = case_id(frozen["id"])
        key = re.sub(r"[^A-Za-z0-9]+", "_", identity).strip("_")
        source_path = RUNS[1] + "/phase2/cases/" + key + ".json"
        record_sha = inputs.source_sha(source_path)
        require(record_sha == phase2_summary["case_sha256"].get(identity, phase2_summary["case_sha256"].get(key)), "phase2 raw record binding differs")
        record = inputs.source(source_path)
        require(record["id"] == identity and record["prompt_sha256"] == frozen["prompt_sha256"] and record["passed"] is True,
                "phase2 accepted prompt identity or recorded product gate differs")
        entry = {name: frozen[name] for name in ["kind", "family", "group", "checker", "standard", "prompt_sha256"]}
        require(type(entry["standard"]) is bool, "invalid phase2 standard scope")
        entry.update(id=identity, expected_source={"path": P2, "raw_sha256": inputs.public[P2], "pointer": f"/rows/{frozen_index}/phase2_expected"},
                     expected={name: value for name, value in frozen["phase2_expected"].items() if name in {"result", "rule", "code", "http_status"}},
                     files_map_sha256=record.get("rust", {}).get("files_sha256"), accepted_record_sha256=record_sha,
                     accepted_per_file_hashes=None, per_file_evidence="not stored in indexed accepted metadata; complete map hash remains authoritative")
        if entry["expected"].get("result") == "product":
            digest_text(entry["files_map_sha256"])
        else:
            require(entry["expected"].get("result") == "refusal" and entry["files_map_sha256"] is None, "invalid phase2 refusal evidence")
            historical = record["rust"]
            require(type(historical["compile_status"]) is int and 400 <= historical["compile_status"] <= 599,
                    "invalid historical refusal status")
            code = historical["refusal"]["code"]
            require(isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code), "invalid historical refusal code")
            entry["historical_refusal_observation"] = {"http_status": historical["compile_status"], "code": code,
                                                       "authority": "historical observation only; current expected rule comes from expected_source"}
        if identity in legacy_by_id:
            source = legacy_by_id[identity]
            require(entry["family"] == source["family"] and entry["group"] == source["group"]
                    and entry["prompt_sha256"] == source["prompt_sha256"], "phase2 legacy scope differs")
            # Frozen check_phase2.checker: standard selects behavior checks.
            # Assembly variants describe a separate DSL choice.
            if source["family"] == "legacy214":
                require(entry["checker"] == "verify_all" and entry["standard"] == (source["group"] == "services14"),
                        "phase2 legacy214 behavior checker scope differs")
            else:
                require(source["family"] in {"quality218", "supplement12"}
                        and entry["checker"] == "flow" and entry["standard"] is True,
                        "phase2 flow behavior checker scope differs")
            entry["assembly_variant"] = source["variant"]
            entry["input_provenance"] = source["input_provenance"]
            if "checked_dsl_provenance" in source:
                entry["checked_dsl_provenance"] = source["checked_dsl_provenance"]
        else:
            entry["input_provenance"] = "tracked frozen public case; future GitHub reconstruction proof required"
        rust2.append(entry)
    require(sum(row["expected"]["result"] == "product" for row in rust2) == 582 and sum(row["expected"]["result"] == "refusal" for row in rust2) == 2, "phase2 product/refusal scope differs")
    require(len({row["id"] for row in public_rows}) == len(public_rows) and len(legacy_by_id) == len(legacy_rows), "duplicate named Python job")
    inputs.verify_all()
    return {"version": "som.public-golden-hashes.v1", "state": "historical golden evidence only; current product acceptance not run",
            "product_acceptance_passed": False, "behavior_gates": "unrun", "raw_compile_reply_comparison": "unrun; no projected compile hash is emitted", "native_metadata_compatibility": "requires a separate numbered policy; no metadata masking performed",
            "public_repository": "faberline/som", "public_source_revision": inputs.revision, "accepted_public_anchors": anchors,
            "public_source_files": [{"path": path, "revision": inputs.revision, "raw_sha256": digest}
                                    for path, digest in sorted(inputs.public.items())],
            "counts": {**COUNTS, "legacy_distinct_prompts": 540, "legacy_blocked_typed_rows": 11, "legacy_blocked_distinct_prompts": 10,
                       "legacy_generator_rows_unrun": 90, "checked_dsl_rows_blocked": 2, "checked_public_recipe_rows_unrun": len(checked_unrun),
                       "rust_phase2_products": 582, "rust_phase2_refusals": 2},
            "cohorts": {"python_public": public_rows, "python_legacy": legacy_rows, "rust_phase1": rust1, "rust_phase2": rust2}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", type=Path, required=True, help="local private baseline route; never use the private fixture review packet")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--out", type=Path, required=True, help="new hash-only JSON file")
    args = parser.parse_args(argv)
    try:
        require(args.route.name != "som-m12-private-fixture-review.json", "private review packet is prohibited")
        no_symlinks(args.route)
        require(".." not in args.out.parts and not args.out.exists(), "output must be new and must not traverse")
        no_symlinks(args.out)
        route = parse(args.route.read_bytes())
        result = build_export(route, args.repo.resolve(strict=True))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({"state": result["state"], "counts": result["counts"], "product_acceptance_passed": False}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        print("refused: " + (str(error) if isinstance(error, Refusal) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
