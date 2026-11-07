"""Audit retained baseline HTTP bytes. No acceptance tool is loaded or run."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

PUBLIC_COUNT, LEGACY_COUNT, RUST_PHASE1_COUNT, RUST_PHASE2_COUNT = 127, 544, 115, 584
ROOT = "som-rs"
LEGACY = ".local/rust-compiler-goal/acceptance/legacy544"
RUST_ACCEPTED = ".local/rust-compiler-goal/acceptance/rust-output-055/run-003"
HEX = re.compile(r"[0-9a-f]{64}\Z")
LOCATION = {"line", "column", "end_line", "end_column", "byte_start", "byte_end"}
CONTROL_EVIDENCE = {
    "react": ("som-rs/evidence/react-output-v1/python-controls-2026-10-22.json", "som-rs/evidence/react-output-contract-amendment-002.json"),
    "rust": ("som-rs/evidence/rust-output-v1/negative-python-controls-2026-10-23.json", "som-rs/evidence/rust-output-contract-amendment-006.json"),
    "ts": ("som-rs/evidence/typescript-output-v1/python-controls-2026-10-23.json", "som-rs/evidence/typescript-output-contract-amendment-002.json"),
}


class BaselineRawError(ValueError):
    def __init__(self, message, status="failed"):
        super().__init__(message)
        self.status = status


def need(condition, message, status="failed"):
    if not condition:
        raise BaselineRawError(message, status)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "raw JSON has duplicate members")
            result[key] = value
        return result

    def bad_constant(value):
        raise BaselineRawError("raw JSON has a non-finite number")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (UnicodeDecodeError, ValueError) as error:
        if isinstance(error, BaselineRawError):
            raise
        raise BaselineRawError("raw HTTP JSON is malformed") from None


def obj(value, label):
    need(type(value) is dict, f"{label} is not an object")
    return value


def arr(value, label):
    need(type(value) is list, f"{label} is not an array")
    return value


def read_json(reader, path):
    return parse(reader.bytes(path))


def pinned_json(reader, path, expected):
    raw = reader.bytes(path)
    need(type(expected) is str and HEX.fullmatch(expected), "source JSON hash is missing", "unrun")
    need(sha(raw) == expected, "source JSON differs from its frozen hash")
    return parse(raw)


def endpoint(value, ports=(18917, 18919)):
    need(ports in ((18917, 18919), (18978, 18979)), "unsupported fixed QA ports")
    need(type(value) is str, "recorded endpoint is missing", "unrun")
    url = urlsplit(value)
    need(url.scheme == "http" and url.hostname == "127.0.0.1" and url.port in ports
         and not url.username and not url.password and not url.query and not url.fragment
         and url.path in ("", "/"), "baseline raw evidence endpoint is not a fixed hosted QA endpoint")
    return value.rstrip("/")


class Exchanges:
    def __init__(self, reader, directory, expected_script, ports=(18917, 18919)):
        self.reader, self.directory = reader, reader.path(directory)
        manifest = obj(read_json(reader, self.directory / "manifest.json"), "raw HTTP manifest")
        need(manifest.get("version") == "som.m6-http-recording.v1", "raw HTTP recording version differs")
        if ports == (18978, 18979):
            need(manifest.get("qa_mode") == "native-qa-v1" and manifest.get("ports") == list(ports)
                 and manifest.get("source_route") == "checked-git-bytes",
                 "native HTTP recorder evidence missing", "unrun")
        need(type(manifest.get("exit_code")) is int and manifest["exit_code"] == 0,
             "recorded baseline collector did not exit successfully")
        need(manifest.get("recording_errors") == [], "HTTP recorder did not retain complete evidence", "unrun")
        identity = obj(manifest.get("script"), "recorded script identity")
        need(identity.get("path") == expected_script, "HTTP recorder ran another script")
        need(identity.get("sha256") == reader.hash(expected_script), "recorded collector source hash differs")
        self.calls, self.used = arr(manifest.get("calls"), "recorded HTTP calls"), set()
        self.full_count_known = False
        self.by_request = {}
        ids = set()
        for position, call in enumerate(self.calls):
            obj(call, "raw HTTP call")
            identity = call.get("id")
            need(type(identity) is int and identity >= 0 and identity not in ids, "raw HTTP call ID is invalid or repeated")
            ids.add(identity)
            need(call.get("method") == "POST", "baseline recorder has an unexpected HTTP method")
            split = urlsplit(call.get("url", ""))
            origin = endpoint(f"{split.scheme}://{split.netloc}", ports)
            need(split.path in ("/v1/compile", "/v1/assemble") and not split.query and not split.fragment,
                 "raw baseline request route differs")
            request = self.file(call, "request")
            self.by_request.setdefault((origin + split.path, sha(request)), []).append((position, request))

    def file(self, call, kind):
        name = call.get(f"{kind}_file")
        need(type(name) is str and name and not Path(name).is_absolute(), "raw HTTP byte path missing", "unrun")
        path = self.reader.path(name, self.directory)
        need(path.is_relative_to(self.directory), "raw HTTP bytes leave this recording")
        raw = self.reader.bytes(path)
        need(call.get(f"{kind}_sha256") == sha(raw), f"raw {kind} bytes differ from their hash")
        return raw

    def take(self, base, route, request):
        candidates = self.by_request.get((base + route, sha(request)), [])
        available = [(position, raw) for position, raw in candidates if position not in self.used]
        need(available, "required raw HTTP exchange is missing or its request changed", "failed" if self.full_count_known else "unrun")
        position, actual = available[0]
        need(actual == request, "raw HTTP request bytes differ")
        self.used.add(position)
        call = self.calls[position]
        need(call.get("complete") is True and call.get("error") is None,
             "raw HTTP exchange is incomplete or failed", "unrun")
        status = call.get("status")
        need(type(status) is int and 100 <= status <= 599, "raw HTTP status is missing", "unrun")
        return status, self.file(call, "response"), call

    def finish(self):
        need(len(self.used) == len(self.calls), "raw recording has unexpected or unbound HTTP exchanges")

    def require_count(self, expected):
        need(len(self.calls) == expected, "raw HTTP exchange inventory differs", "unrun" if len(self.calls) < expected else "failed")
        self.full_count_known = True


def verify_bundle(bundle, target):
    obj(bundle, "compile bundle")
    need(bundle.get("version") == ("som.dsl-bundle.v1" if target == "python" else "som.dsl-bundle.v2"),
         "compile bundle version differs")
    if target == "python":
        need(bundle.get("target", "python") == "python", "Python reply has another target")
    else:
        need(bundle.get("target") == target, "compile bundle target differs")
    hashes = obj(bundle.get("hashes"), "bundle hashes")
    need(hashes.get("prompt_sha256") == sha(canonical(bundle["prompt"])), "raw bundle prompt hash differs")
    need(hashes.get("dsl_sha256") == sha(canonical(bundle["dsl"])), "raw bundle DSL hash differs")
    need(hashes.get("payload_sha256") == sha(canonical({key: value for key, value in bundle.items() if key != "hashes"})),
         "raw bundle payload hash differs")
    return bundle


def compile_bundle(status, raw, target, prompt=None):
    need(status == 200, "required compile was refused")
    document = obj(parse(raw), "compile reply")
    need(document.get("status") == "ok", "compile success envelope differs")
    bundle = verify_bundle(document.get("bundle"), target)
    if prompt is not None:
        need(canonical(bundle["prompt"]) == canonical(prompt), "raw compiled prompt differs from source input")
    return document, bundle


def source_inputs(reader):
    inventory = obj(read_json(reader, f"{ROOT}/contracts/react-output-v1/case-inventory.json"), "React inventory")
    sources = obj(inventory.get("sources"), "frozen input sources")
    fixtures_path = f"{ROOT}/tests/compiler-v1/cnl-positive-fixtures.json"
    fixtures = pinned_json(reader, fixtures_path, sources["cnl_positive_fixtures"][fixtures_path])
    public = []
    for case in arr(fixtures.get("cases"), "public fixture cases"):
        prompt = obj(case.get("expected_prompt"), "public fixture prompt")
        need(sha(canonical(prompt)) == case.get("prompt_sha256"), "public fixture prompt hash differs")
        public.append({"id": case["id"], "prompt": prompt})
    examples = f"{ROOT}/contracts/compiler-v1/examples"
    index = pinned_json(reader, f"{examples}/index.json", sources["examples"][f"{examples}/index.json"])
    for case in index["cases"]:
        path = f"{examples}/{case['prompt']}"
        public.append({"id": "PUBLIC_" + case["id"], "prompt": pinned_json(reader, path, sources["examples"][path])})
    need(len(public) == PUBLIC_COUNT and len({case["id"] for case in public}) == PUBLIC_COUNT,
         "immutable public input inventory is incomplete", "unrun")
    legacy_source = sources["legacy_inventory"]
    path = f"{LEGACY}/inventory-003.json"
    legacy = arr(pinned_json(reader, path, legacy_source[path]).get("groups"), "legacy inventory groups")
    need(len(legacy) == LEGACY_COUNT and len({case["id"] for case in legacy}) == LEGACY_COUNT,
         "immutable legacy input inventory is incomplete", "unrun")
    return public, legacy, legacy_source["run"]


def input_request(reader, directory):
    directory = reader.path(directory)
    chosen = next((directory / name for name in ("compile-json.request.json", "compile.request.json")
                   if reader.path(directory / name).is_file()), None)
    need(chosen is not None, "accepted compile request is missing", "unrun")
    body = obj(read_json(reader, chosen), "accepted compile request")
    need(set(body) == {"format", "prompt"} and body["format"] == "som.prompt-json.v1", "accepted typed request envelope differs")
    raw_path = chosen.with_suffix(".raw")
    raw = reader.bytes(raw_path) if reader.path(raw_path).is_file() else compact(body)
    try:
        if canonical(parse(raw)) != canonical(body):
            raw = compact(body)
    except BaselineRawError:
        raw = compact(body)
    return body, raw


def legacy_prompt(reader, run, index, case):
    directories = [Path(run) / "compile" / case["prompt_sha256"], Path(run) / f"{index:04d}"]
    directory = next((path for path in directories if reader.path(path).is_dir()), None)
    need(directory is not None, "legacy input directory is missing", "unrun")
    body, raw = input_request(reader, directory)
    need(case["prompt_sha256"] in (sha(canonical(body["prompt"])), sha(compact(body["prompt"]))),
         "legacy input prompt differs from frozen identity")
    return body, raw


def validate_python(reader, raw_dir, report, ports=(18917, 18919)):
    recording = Exchanges(reader, raw_dir, f"{ROOT}/scripts/typescript_output_acceptance/check_python_baseline.py", ports)
    qa, base = endpoint(report.get("qa_url", report.get("qa")), ports), endpoint(report.get("baseline_url", report.get("baseline")), ports)
    need(qa != base, "candidate and baseline endpoints are the same")
    public, legacy, legacy_run = source_inputs(reader)
    public_run = f".local/rust-compiler-goal/acceptance/public-products-003b"
    need(reader.path(report.get("public_run")) == reader.path(public_run)
         and reader.path(report.get("legacy_run")) == reader.path(legacy_run), "Python baseline source directories differ")
    expected_inputs = []
    for source, cases, run, width in (("public", public, public_run, 3), ("legacy", legacy, legacy_run, 4)):
        records = arr(read_json(reader, Path(run) / "result.json").get("records"), "accepted source records")
        need([row.get("id") for row in records] == [row["id"] for row in cases], "accepted source IDs/order differ")
        for index, case in enumerate(cases):
            if source == "public":
                body, raw = input_request(reader, Path(run) / f"{index:0{width}d}")
                need(canonical(body["prompt"]) == canonical(case["prompt"]), "public baseline request differs from immutable input")
            else:
                need(records[index].get("prompt_sha256") == case["prompt_sha256"], "legacy source record prompt identity differs")
                body, raw = legacy_prompt(reader, run, index, case)
            expected_inputs.extend((source, case["id"], variant, body, raw if variant == "none" else compact({**body, "target": "python"}))
                                   for variant in ("none", "python"))
    rows = arr(report.get("rows"), "Python baseline rows")
    actual = {(row.get("source"), row.get("id"), row.get("variant")): row for row in rows}
    need(len(actual) == len(rows) == len(expected_inputs)
         and set(actual) == {(source, cid, variant) for source, cid, variant, _, _ in expected_inputs},
         "Python baseline named inventory differs", "unrun")
    recording.require_count(len(expected_inputs) * 4)
    for source, cid, variant, body, request in expected_inputs:
        row = actual[(source, cid, variant)]
        results = [recording.take(url, "/v1/compile", request) for url in (qa, base)]
        need(results[0][:2] == results[1][:2], "Python raw compile bytes or status changed")
        bundles = []
        for role, (status, raw, _) in zip(("qa", "baseline"), results):
            _, bundle = compile_bundle(status, raw, "python", body["prompt"])
            need(row.get(f"{role}_compile_request_sha256") == sha(request)
                 and row.get(f"{role}_compile_response_sha256") == sha(raw)
                 and row.get(f"{role}_compile_status") == status, "Python compile receipt differs from raw exchange")
            bundles.append(bundle)
        assembled = []
        for role, url, bundle in zip(("qa", "baseline"), (qa, base), bundles):
            assemble_request = compact({"bundle": bundle})
            status, raw, _ = recording.take(url, "/v1/assemble", assemble_request)
            need(status == 200 and type(parse(raw).get("files")) is dict, "Python assembly did not return product files")
            need(row.get(f"{role}_assemble_request_sha256") == sha(assemble_request)
                 and row.get(f"{role}_assemble_response_sha256") == sha(raw)
                 and row.get(f"{role}_assemble_status") == status, "Python assembly receipt differs from its own compile bundle")
            assembled.append(raw)
        need(assembled[0] == assembled[1], "Python raw product response bytes changed")
    recording.finish()
    return {"cases": len(public) + len(legacy), "rows": len(expected_inputs), "exchanges": len(recording.calls)}


def rust_sources(reader):
    inventory = obj(read_json(reader, f"{ROOT}/contracts/rust-output-v1/case-inventory.json"), "Rust phase1 inventory")
    phase2 = obj(read_json(reader, f"{ROOT}/contracts/rust-output-v1/phase2/case-inventory.json"), "Rust phase2 inventory")
    public, legacy, legacy_run = source_inputs(reader)
    static = {case["id"]: case["prompt"] for case in public}
    for case in inventory["public_examples"]:
        static[case["id"]] = pinned_json(reader, case["prompt"], case["prompt_file_sha256"])
    coverage_root = Path(f"{ROOT}/tests/rust-output-v1/cases")
    coverage_path = (coverage_root / "index.json").as_posix()
    coverage = pinned_json(reader, coverage_path, inventory["sources"]["coverage_index"][coverage_path])
    coverage_pins = {case["id"]: case for case in inventory["coverage_cases"]}
    for case in coverage["cases"]:
        static[case["id"]] = pinned_json(reader, coverage_root / case["prompt"], coverage_pins[case["id"]]["prompt_file_sha256"])
    for index, case in enumerate(legacy):
        static[case["id"]] = legacy_prompt(reader, legacy_run, index, case)[0]["prompt"]
    phase1_rows = [case for group in ("public_examples", "version_positive_fixtures", "coverage_cases", "legacy_in_scope")
                   for case in inventory[group] if group == "legacy_in_scope" or case.get("rust") == "in_scope"]
    need(len(phase1_rows) == RUST_PHASE1_COUNT and len({case["id"] for case in phase1_rows}) == RUST_PHASE1_COUNT,
         "frozen Rust phase1 source inventory differs", "unrun")
    accepted_phase1 = arr(read_json(reader, f"{RUST_ACCEPTED}/rust-products.json").get("cases"), "accepted Rust phase1 cases")
    need({case["id"] for case in accepted_phase1} == {case["id"] for case in phase1_rows}
         and len(accepted_phase1) == RUST_PHASE1_COUNT, "accepted Rust phase1 IDs differ")
    phase1_hashes = {case["id"]: case.get("files_sha256") for case in accepted_phase1}
    specs = {}
    for case in phase1_rows:
        prompt = obj(static.get(case["id"]), "source-bound Rust phase1 prompt")
        if case.get("prompt_sha256"):
            need(case["prompt_sha256"] in (sha(canonical(prompt)), sha(compact(prompt))), "Rust phase1 prompt identity differs")
        need(type(phase1_hashes[case["id"]]) is str and HEX.fullmatch(phase1_hashes[case["id"]]), "accepted Rust file hash is missing", "unrun")
        specs[("phase1", case["id"])] = {"prompt": prompt, "files_sha256": phase1_hashes[case["id"]], "refusal": False}
    rows = arr(phase2.get("rows"), "frozen Rust phase2 cases")
    need(len(rows) == RUST_PHASE2_COUNT and len({case["id"] for case in rows}) == RUST_PHASE2_COUNT,
         "frozen Rust phase2 inventory differs", "unrun")
    summary = obj(read_json(reader, f"{RUST_ACCEPTED}/phase2/summary.json"), "accepted Rust summary")
    need(set(summary.get("case_sha256", {})) == {case["id"] for case in rows}, "accepted Rust summary inventory differs")
    for case in rows:
        cid = case["id"]
        key = re.sub(r"[^A-Za-z0-9]+", "_", cid).strip("_")
        path = f"{RUST_ACCEPTED}/phase2/cases/{key}.json"
        record = obj(pinned_json(reader, path, summary["case_sha256"][cid]), "accepted Rust phase2 record")
        prompt = record.get("prompt", static.get(cid))
        if prompt is None and cid.startswith("PUBLIC_"):
            suffix = cid.removeprefix("PUBLIC_").replace("_", "-")
            prompt = static.get(f"example-{suffix}")
        if prompt is None and cid.startswith(("RS", "rs/")):
            prompt = read_json(reader, f".local/rust-output-phase2/rs-products-001/{cid.split('/')[-1]}/prompt.json")
        obj(prompt, "source-bound Rust phase2 prompt")
        need(sha(canonical(prompt)) == case.get("prompt_sha256"), "Rust phase2 prompt differs from immutable source identity")
        if record.get("prompt_sha256") is not None:
            need(record["prompt_sha256"] == case["prompt_sha256"], "accepted phase2 prompt identity differs")
        original = obj(record.get("rust"), "accepted Rust product record")
        refusal = case.get("phase2_expected", {}).get("result") == "refusal"
        files_sha = original.get("files_sha256")
        if not refusal:
            need(type(files_sha) is str and HEX.fullmatch(files_sha), "accepted Rust phase2 file hash missing", "unrun")
        specs[("phase2", cid)] = {"prompt": prompt, "files_sha256": files_sha, "refusal": refusal,
                                   "expected": case["phase2_expected"]}
    return specs


def normalized(document):
    out = copy.deepcopy(document)
    bundle = obj(out.get("bundle"), "Rust compile bundle")
    assets, hashes = obj(bundle.get("assets"), "Rust assets"), obj(bundle.get("hashes"), "Rust hashes")
    for member in ("backend", "target_contract"):
        need(member in assets, "allowed Rust descriptor is missing")
        del assets[member]
    need("payload_sha256" in hashes, "Rust payload hash is missing")
    del hashes["payload_sha256"]
    return canonical(out)


def validate_rust(reader, raw_dir, report, ports=(18917, 18919)):
    recording = Exchanges(reader, raw_dir, f"{ROOT}/scripts/typescript_output_acceptance/check_rust_baseline.py", ports)
    qa, base = endpoint(report.get("qa_url", report.get("qa")), ports), endpoint(report.get("baseline_url", report.get("baseline")), ports)
    need(qa != base, "candidate and baseline endpoints are the same")
    specs = rust_sources(reader)
    rows = arr(report.get("rows"), "Rust baseline rows")
    indexed = {(row.get("phase"), row.get("id"), row.get("check")): row for row in rows}
    expected = {(phase, cid, name) for (phase, cid), spec in specs.items()
                for name in (("compile_equal",) if spec["refusal"] else ("compile_equal", "baseline_bundle_refused", "rebased_files"))}
    need(len(indexed) == len(rows) and set(indexed) == expected, "Rust baseline named result inventory differs", "unrun")
    recording.require_count(sum(2 if spec["refusal"] else 4 for spec in specs.values()))
    for (phase, cid), spec in specs.items():
        request = compact({"format": "som.prompt-json.v1", "target": "rust", "prompt": spec["prompt"]})
        qa_result, base_result = (recording.take(url, "/v1/compile", request) for url in (qa, base))
        row = indexed[(phase, cid, "compile_equal")]
        need(row.get("request_sha256") == row.get("qa_request_sha256") == row.get("baseline_request_sha256") == sha(request),
             "Rust compile receipt request differs from source-bound input")
        for role, result in (("qa", qa_result), ("baseline", base_result)):
            need(row.get(f"{role}_status") == result[0] and row.get(f"{role}_response_sha256") == sha(result[1]),
                 "Rust compile receipt differs from retained raw bytes")
        if spec["refusal"]:
            need(qa_result[:2] == base_result[:2] and qa_result[0] != 200, "Rust compiler refusal bytes changed")
            expected_refusal = obj(spec.get("expected"), "frozen Rust compiler refusal")
            actual_refusal = obj(parse(qa_result[1]), "raw Rust compiler refusal")
            need(qa_result[0] == expected_refusal.get("http_status") and actual_refusal.get("code") == expected_refusal.get("code"),
                 "Rust compiler refusal differs from frozen status or code")
            first = arr(actual_refusal.get("diagnostics"), "raw Rust compiler diagnostics")
            need(len(first) == 1 and first[0].get("cause") == expected_refusal.get("detail")
                 and first[0].get("prompt_pointer") == "/prompt" + expected_refusal["pointer"],
                 "Rust compiler refusal differs from the frozen first diagnostic")
            verify_location(request, first[0])
            continue
        qa_doc, qa_bundle = compile_bundle(*qa_result[:2], "rust", spec["prompt"])
        base_doc, base_bundle = compile_bundle(*base_result[:2], "rust", spec["prompt"])
        need(normalized(qa_doc) == normalized(base_doc), "Rust compile changed outside the three allowed members")
        refusal_request = compact({"bundle": base_bundle})
        status, raw, _ = recording.take(qa, "/v1/assemble", refusal_request)
        refusal = obj(parse(raw), "baseline bundle refusal")
        need(status == 400 and refusal.get("code") == "ASSET_HASH_MISMATCH", "candidate accepted an old Rust bundle")
        refusal_row = indexed[(phase, cid, "baseline_bundle_refused")]
        need(refusal_row.get("request_sha256") == sha(refusal_request)
             and refusal_row.get("qa_response_sha256") == sha(raw) and refusal_row.get("qa_status") == status,
             "Rust refusal receipt differs from raw exchange")
        rebased = copy.deepcopy(base_bundle)
        for member in ("backend", "target_contract"):
            rebased["assets"][member] = copy.deepcopy(qa_bundle["assets"][member])
        rebased["hashes"]["payload_sha256"] = sha(canonical({key: value for key, value in rebased.items() if key != "hashes"}))
        rebased_request = compact({"bundle": rebased})
        status, raw, _ = recording.take(qa, "/v1/assemble", rebased_request)
        response = obj(parse(raw), "rebased Rust assembly")
        files = obj(response.get("files"), "assembled Rust files")
        need(status == 200 and files and all(type(path) is str and type(content) is str for path, content in files.items()),
             "rebased Rust bundle did not assemble product bytes")
        actual_files = sha(canonical(files))
        need(actual_files == spec["files_sha256"], "Rust product bytes differ from accepted reference")
        assembled_row = indexed[(phase, cid, "rebased_files")]
        need(assembled_row.get("request_sha256") == sha(rebased_request)
             and assembled_row.get("qa_response_sha256") == sha(raw) and assembled_row.get("qa_status") == status
             and assembled_row.get("files_sha256") == assembled_row.get("recorded_files_sha256") == actual_files,
             "Rust product receipt differs from raw product bytes and accepted reference")
    recording.finish()
    return {"cases": len(specs), "rows": len(rows), "exchanges": len(recording.calls)}


def profile(prompt, scope, target):
    version = prompt["version"]
    def refusal(rule, pointer, **values):
        entry = scope["refusals"][rule]
        return {"pointer": pointer, "cause": entry["cause"].format(**values), "allowed_form": entry["allowed_form"]}
    if version not in scope["prompt_versions"]["in_scope"]:
        return refusal("prompt_version", "/version", version=version.removeprefix("som.prompt.v"), family=scope["prompt_versions"]["out_of_scope"][version])
    if target == "react":
        if "" in prompt["resource"]["path"].split("/")[1:]:
            return refusal("path_empty_segment", "/resource/path", path=prompt["resource"]["path"])
        for index, field in enumerate(prompt["resource"]["fields"]):
            if field["name"] in scope["identifiers"]["reserved_names"]:
                return refusal("identifier_reserved", f"/resource/fields/{index}/name", name=field["name"])
        return None
    if version == "som.prompt.v5" and "capabilities" in prompt:
        return refusal("v5_capabilities", "/capabilities/auth", auth=prompt["capabilities"]["auth"])
    resources = [(prompt["resources"][role], f"/resources/{role}", role == "parent") for role in ("parent", "child")] if version == "som.prompt.v5" else [(prompt["resource"], "/resource", True)]
    def identifier(name, pointer):
        if name in scope["identifiers"]["not_raw_identifiers"]:
            return refusal("identifier_not_raw", pointer, name=name)
        core = name.strip("_")
        if not core or any(char.isupper() for char in core) or "__" in core:
            return refusal("identifier_not_snake_case", pointer, name=name)
        return None
    for resource, pointer, top in resources:
        if "" in resource["path"].split("/")[1:]:
            return refusal("path_empty_segment", pointer + "/path", path=resource["path"])
        if top and resource["path"] in scope["routes"]["fixed_top_level_paths"]:
            return refusal("path_reserved", pointer + "/path", path=resource["path"])
        if resource["table"] in scope["identifiers"]["reserved_tables"]:
            return refusal("table_reserved", pointer + "/table", table=resource["table"])
        for index, field in enumerate(resource["fields"]):
            found = identifier(field["name"], pointer + f"/fields/{index}/name")
            if found:
                return found
    if version == "som.prompt.v5":
        return identifier(prompt["relation"]["foreign_key"], "/relation/foreign_key")
    query = prompt.get("query")
    if query is not None and version in scope["query_parameters"]:
        settings = scope["query_parameters"][version]
        if settings["filter_parameter"] == "field_name" and query["filter_field"] in settings["fixed"]:
            return refusal("query_parameter_collision", "/query/filter_field", name=query["filter_field"])
    return None


def diagnostic(status, raw, case, body, *, moved=False):
    document = obj(parse(raw), "raw control refusal")
    expected = obj(case.get("expected"), "frozen control diagnostic")
    need(status == case["http_status"] and document.get("code") == expected["code"], "control refusal status or code changed")
    diagnostics = arr(document.get("diagnostics"), "control diagnostics")
    need(len(diagnostics) == 1, "control first-diagnostic inventory differs")
    actual = obj(diagnostics[0], "control first diagnostic")
    for key, value in expected.items():
        if not moved or key not in LOCATION:
            need(type(actual.get(key)) is type(value) and actual.get(key) == value, f"control diagnostic member changed: {key}")
    if moved:
        start, end = actual.get("byte_start"), actual.get("byte_end")
        need(type(start) is int and type(end) is int and 0 <= start <= end, "moved diagnostic byte range is invalid")
        parsed = parse(body)
        source = parsed.get("source") if parsed.get("format") == "som.cnl.v1" else None
        located = source.encode() if source is not None else body
        need(end <= len(located) and located[start:end].decode() == case["span_text"], "moved diagnostic span differs")
        for position, prefix in ((start, ""), (end, "end_")):
            before = located[:position].decode()
            need(actual.get(prefix + "line") == before.count("\n") + 1
                 and actual.get(prefix + "column") == len(before.rsplit("\n", 1)[-1]) + 1,
                 "moved diagnostic line or column differs")
        if source is None:
            verify_location(body, actual)


def verify_location(raw, diagnostic):
    """Locate a typed JSON pointer without using an acceptance parser."""
    text = raw.decode("utf-8")
    decoder, spans = json.JSONDecoder(), {}
    def whitespace(position):
        while position < len(text) and text[position].isspace():
            position += 1
        return position
    def visit(position, pointer):
        position = whitespace(position)
        start = position
        if text[position] == "{":
            position = whitespace(position + 1)
            if text[position] != "}":
                while True:
                    key, end = decoder.raw_decode(text, position)
                    need(type(key) is str, "raw pointer object key is not a string")
                    position = whitespace(end)
                    need(text[position] == ":", "raw pointer object is malformed")
                    escaped = key.replace("~", "~0").replace("/", "~1")
                    position = whitespace(visit(position + 1, pointer + "/" + escaped))
                    if text[position] == "}":
                        break
                    need(text[position] == ",", "raw pointer object delimiter differs")
                    position = whitespace(position + 1)
            position += 1
        elif text[position] == "[":
            position = whitespace(position + 1)
            index = 0
            if text[position] != "]":
                while True:
                    position = whitespace(visit(position, pointer + "/" + str(index)))
                    index += 1
                    if text[position] == "]":
                        break
                    need(text[position] == ",", "raw pointer array delimiter differs")
                    position = whitespace(position + 1)
            position += 1
        else:
            _, position = decoder.raw_decode(text, position)
        spans[pointer] = (len(text[:start].encode()), len(text[:position].encode()))
        return position
    parse(raw)
    end = visit(0, "")
    need(whitespace(end) == len(text), "raw pointer input has trailing bytes")
    pointer = diagnostic.get("prompt_pointer")
    need(pointer in spans, "diagnostic pointer is absent from raw JSON")
    start, end = spans[pointer]
    need((diagnostic.get("byte_start"), diagnostic.get("byte_end")) == (start, end), "diagnostic span is not its raw JSON pointer value")
    for position, prefix in ((start, ""), (end, "end_")):
        before = raw[:position].decode()
        need(diagnostic.get(prefix + "line") == before.count("\n") + 1
             and diagnostic.get(prefix + "column") == len(before.rsplit("\n", 1)[-1]) + 1,
             "diagnostic JSON pointer line or column differs")


def validate_controls(reader, raw_dir, report, language, ports=(18917, 18919)):
    need(language in ("react", "rust", "ts"), "unknown Python control contract")
    contract = {"react": "react-output-v1", "rust": "rust-output-v1", "ts": "typescript-output-v1"}[language]
    recording = Exchanges(reader, raw_dir, f"{ROOT}/tests/{contract}/check_python_controls.py", ports)
    base = endpoint(report.get("base_url"), ports)
    cases = arr(read_json(reader, f"{ROOT}/contracts/{contract}/negative-cases.json").get("cases"), "frozen controls")
    if language != "rust":
        cases += arr(read_json(reader, f"{ROOT}/contracts/{contract}/target-examples.json").get("name_cases"), "frozen name controls")
    rows = arr(report.get("results"), "Python control results")
    actual = {row.get("id"): row for row in rows}
    need(len(actual) == len(rows) == len(cases) and set(actual) == {case["id"] for case in cases},
         "Python controls named inventory differs", "unrun")
    evidence_path, receipt_path = CONTROL_EVIDENCE[language]
    receipt = obj(read_json(reader, receipt_path), "sealed control receipt")
    frozen_record = pinned_json(reader, evidence_path, obj(receipt.get("files"), "sealed control files").get(evidence_path))
    frozen_rows = arr(frozen_record.get("results"), "frozen Python control rows")
    frozen = {row["id"]: row for row in frozen_rows}
    need(len(frozen) == len(frozen_rows) and set(frozen) == {case["id"] for case in cases}, "sealed Python controls inventory differs")
    scope = read_json(reader, f"{ROOT}/contracts/{contract}/scope.json") if language in ("react", "rust") else None
    recording.require_count(len(cases) + sum(language == "rust" and case["expected"]["code"] == "INVALID_JSON" for case in cases))
    for case in cases:
        body = case["python_control_body"].encode()
        status, raw, _ = recording.take(base, "/v1/compile", body)
        row = actual[case["id"]]
        need(row.get("control_status") == status, "Python control receipt status differs from raw exchange")
        expected = case.get("python_control_expected", "")
        refuses = case["id"].startswith("RP") or expected.startswith("the same diagnostic") if language == "rust" else not expected.startswith("HTTP 200")
        if refuses:
            diagnostic(status, raw, case, body, moved=expected.startswith("the same diagnostic, located in python_control_body"))
        else:
            _, bundle = compile_bundle(status, raw, "python")
            need(row.get("payload_sha256") == bundle["hashes"]["payload_sha256"], "control payload receipt differs from raw bundle")
            frozen_payload = frozen[case["id"]].get("payload_sha256")
            need(type(frozen_payload) is str and HEX.fullmatch(frozen_payload), "successful control has no sealed historical payload pin", "unrun")
            need(frozen_payload == bundle["hashes"]["payload_sha256"], "Python control payload changed from sealed historical baseline")
            if language == "react":
                if case["id"].startswith("RN"):
                    need(profile(bundle["prompt"], scope, "react") is None, "name control compiled outside React scope")
            if language == "rust" and case["id"].startswith("RS") and case["form"] == "cnl":
                found = profile(bundle["prompt"], scope, "rust")
                need(found is not None and all(found[key] == case["expected"]["prompt_pointer" if key == "pointer" else key]
                                               for key in ("pointer", "cause", "allowed_form")),
                     "Rust CNL control prompt does not give its frozen profile refusal")
        if language == "rust" and case["expected"]["code"] == "INVALID_JSON":
            duplicate_status, duplicate_raw, _ = recording.take(base, "/v1/compile", case["body"].encode())
            diagnostic(duplicate_status, duplicate_raw, case, case["body"].encode())
    recording.finish()
    return {"cases": len(cases), "exchanges": len(recording.calls)}
