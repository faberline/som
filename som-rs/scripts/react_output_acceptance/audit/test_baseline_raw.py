"""Synthetic raw exchanges. Run these fixtures only on GitHub runners."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("baseline_raw", Path(__file__).with_name("baseline_raw.py"))
raw_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(raw_audit)

QA, BASE = "http://127.0.0.1:18919", "http://127.0.0.1:18917"


class Reader:
    def __init__(self, repo):
        self.repo = repo
        self.reads = []

    def path(self, value, base=None):
        path = Path(value)
        if not path.is_absolute():
            path = (base or self.repo) / path
        if ".." in path.parts or not path.is_relative_to(self.repo):
            raise raw_audit.BaselineRawError("unsafe fixture path")
        return path

    def bytes(self, value, base=None):
        path = self.path(value, base)
        self.reads.append(path)
        if not path.is_file():
            raise raw_audit.BaselineRawError("missing fixture raw file", "unrun")
        return path.read_bytes()

    def hash(self, value):
        return raw_audit.sha(self.bytes(value))


class Fixture:
    def __init__(self, root, script):
        self.repo = root / "repo"
        self.repo.mkdir()
        self.reader = Reader(self.repo)
        self.directory = self.repo / ".local/raw"
        self.directory.mkdir(parents=True)
        self.script = script
        self.write(script, b"# Fake collector bytes. Never imported or executed.\n")
        self.manifest = {"version": "som.m6-http-recording.v1", "script": {"path": script, "sha256": self.reader.hash(script)},
                         "argv": [str(self.repo / script)], "exit_code": 0, "calls": [], "recording_errors": []}

    def write(self, name, raw):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def json(self, name, value):
        return self.write(name, raw_audit.canonical(value) + b"\n")

    def exchange(self, url, route, request, status, response):
        identity = len(self.manifest["calls"]) + 1
        names = {kind: f"calls/{identity:06d}.{kind}.raw" for kind in ("request", "response")}
        for kind, data in (("request", request), ("response", response)):
            target = self.directory / names[kind]
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
        call = {"id": identity, "method": "POST", "url": url + route,
                "request_file": names["request"], "request_sha256": raw_audit.sha(request), "status": status,
                "response_file": names["response"], "response_sha256": raw_audit.sha(response), "error": None, "complete": True}
        self.manifest["calls"].append(call)
        return call

    def save(self):
        (self.directory / "manifest.json").write_bytes(raw_audit.canonical(self.manifest))

    def change(self, call, kind, data):
        (self.directory / call[f"{kind}_file"]).write_bytes(data)
        call[f"{kind}_sha256"] = raw_audit.sha(data)


def prompt(label="fake"):
    return {"version": "som.prompt.v1", "package": label, "resource": {"name": "Fake", "path": "/fake", "table": "fake",
            "fields": [{"name": "title", "type": "str", "required": True, "unique": False}]}}


def bundle(value, target="python", revision="old"):
    result = {"version": "som.dsl-bundle.v1" if target == "python" else "som.dsl-bundle.v2", "prompt": copy.deepcopy(value),
              "dsl": {"version": "fake.dsl", "files": []}, "assets": {"backend": {"id": revision}, "target_contract": {"id": revision}, "fixed": "fixed"}}
    if target != "python":
        result["target"] = target
    result["hashes"] = {"prompt_sha256": raw_audit.sha(raw_audit.canonical(result["prompt"])),
                        "dsl_sha256": raw_audit.sha(raw_audit.canonical(result["dsl"])),
                        "payload_sha256": raw_audit.sha(raw_audit.canonical(result))}
    return result


def compile_reply(value):
    return raw_audit.canonical({"status": "ok", "bundle": value}) + b"\n"


class BaselineRawTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="som-baseline-raw-fake-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def python_fixture(self):
        data = Fixture(self.root, "som-rs/scripts/typescript_output_acceptance/check_python_baseline.py")
        pub, leg = prompt("public"), prompt("legacy")
        public = [{"id": "PUBLIC_FAKE", "prompt": pub}]
        legacy = [{"id": "legacy-fake", "prompt_sha256": raw_audit.sha(raw_audit.canonical(leg))}]
        public_run = ".local/rust-compiler-goal/acceptance/public-products-003b"
        legacy_run = raw_audit.LEGACY + "/run-005"
        data.json(public_run + "/result.json", {"records": [{"id": "PUBLIC_FAKE"}]})
        data.json(legacy_run + "/result.json", {"records": legacy})
        rows = []
        for source, identity, value, directory in (("public", "PUBLIC_FAKE", pub, public_run + "/000"),
                                                   ("legacy", "legacy-fake", leg, legacy_run + "/compile/" + legacy[0]["prompt_sha256"])):
            body = {"format": "som.prompt-json.v1", "prompt": value}
            data.json(directory + "/compile-json.request.json", body)
            original = json.dumps(body, ensure_ascii=False, indent=2).encode() + b"\n"
            data.write(directory + "/compile-json.request.raw", original)
            for variant in ("none", "python"):
                request = original if variant == "none" else raw_audit.compact({**body, "target": "python"})
                row = {"source": source, "id": identity, "variant": variant, "passed": False}
                compiled = bundle(value)
                reply = compile_reply(compiled)
                assembled_request = raw_audit.compact({"bundle": compiled})
                assembled = raw_audit.canonical({"files": {"main.py": "fake source bytes\n"}})
                for role, url in (("qa", QA), ("baseline", BASE)):
                    data.exchange(url, "/v1/compile", request, 200, reply)
                    data.exchange(url, "/v1/assemble", assembled_request, 200, assembled)
                    for route, sent, returned in (("compile", request, reply), ("assemble", assembled_request, assembled)):
                        row[f"{role}_{route}_request_sha256"] = raw_audit.sha(sent)
                        row[f"{role}_{route}_response_sha256"] = raw_audit.sha(returned)
                        row[f"{role}_{route}_status"] = 200
                rows.append(row)
        report = {"qa_url": QA, "baseline_url": BASE, "public_run": public_run, "legacy_run": legacy_run, "rows": rows}
        data.save()
        return data, report, (public, legacy, legacy_run)

    def test_python_compares_actual_bytes_even_when_all_pass_booleans_are_false(self):
        data, report, sources = self.python_fixture()
        with mock.patch.object(raw_audit, "source_inputs", return_value=sources):
            result = raw_audit.validate_python(data.reader, data.directory, report)
        self.assertEqual(result, {"cases": 2, "rows": 4, "exchanges": 16})

    def test_python_equal_reported_hashes_do_not_hide_different_raw_responses(self):
        data, report, sources = self.python_fixture()
        call = data.manifest["calls"][0]
        changed = compile_reply(bundle(prompt("wrong")))
        data.change(call, "response", changed)
        report["rows"][0]["qa_compile_response_sha256"] = raw_audit.sha(changed)
        report["rows"][0]["baseline_compile_response_sha256"] = raw_audit.sha(changed)
        data.save()
        with mock.patch.object(raw_audit, "source_inputs", return_value=sources):
            with self.assertRaisesRegex(raw_audit.BaselineRawError, "compile bytes or status changed"):
                raw_audit.validate_python(data.reader, data.directory, report)

    def test_missing_exchange_and_incomplete_body_block_acceptance(self):
        data, report, sources = self.python_fixture()
        final = data.manifest["calls"].pop()
        data.save()
        with mock.patch.object(raw_audit, "source_inputs", return_value=sources):
            with self.assertRaises(raw_audit.BaselineRawError) as caught:
                raw_audit.validate_python(data.reader, data.directory, report)
            self.assertEqual(caught.exception.status, "unrun")
        data.manifest["calls"].append(final)
        final["complete"] = False
        data.save()
        with mock.patch.object(raw_audit, "source_inputs", return_value=sources):
            with self.assertRaises(raw_audit.BaselineRawError) as caught:
                raw_audit.validate_python(data.reader, data.directory, report)
            self.assertEqual(caught.exception.status, "unrun")

    def test_raw_hash_and_collector_identity_must_match(self):
        data, report, sources = self.python_fixture()
        data.manifest["calls"][0]["response_sha256"] = "0" * 64
        data.save()
        with mock.patch.object(raw_audit, "source_inputs", return_value=sources):
            with self.assertRaisesRegex(raw_audit.BaselineRawError, "response bytes differ"):
                raw_audit.validate_python(data.reader, data.directory, report)
        data.manifest["script"]["sha256"] = "0" * 64
        data.save()
        with self.assertRaisesRegex(raw_audit.BaselineRawError, "collector source hash differs"):
            raw_audit.Exchanges(data.reader, data.directory, data.script)

    def test_changed_assemble_input_cannot_be_bound_to_another_compile(self):
        data, report, sources = self.python_fixture()
        call = data.manifest["calls"][1]
        data.change(call, "request", raw_audit.compact({"bundle": bundle(prompt("other"))}))
        data.save()
        with mock.patch.object(raw_audit, "source_inputs", return_value=sources):
            with self.assertRaises(raw_audit.BaselineRawError) as caught:
                raw_audit.validate_python(data.reader, data.directory, report)
        self.assertEqual(caught.exception.status, "failed")

    def rust_fixture(self):
        data = Fixture(self.root, "som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py")
        value = prompt()
        refused_prompt = prompt("refused")
        refused_prompt["query"] = {"filter_field": "offset"}
        files = {"Cargo.toml": "fake product\n", "src/main.rs": "fn main() {}\n"}
        specs = {("phase1", "fake-product"): {"prompt": value, "files_sha256": raw_audit.sha(raw_audit.canonical(files)), "refusal": False},
                 ("phase2", "RS12_fake"): {"prompt": refused_prompt, "files_sha256": None, "refusal": True,
                     "expected": {"code": "INVALID_VALUE", "http_status": 400, "detail": "fake frozen collision", "pointer": "/query/filter_field"}}}
        rows = []
        for (phase, cid), spec in specs.items():
            request = raw_audit.compact({"format": "som.prompt-json.v1", "target": "rust", "prompt": spec["prompt"]})
            qa_bundle = bundle(spec["prompt"], "rust", "new")
            base_bundle = bundle(spec["prompt"], "rust", "old")
            qa_raw, base_raw = compile_reply(qa_bundle), compile_reply(base_bundle)
            status = 200
            if spec["refusal"]:
                start = request.index(b'"offset"')
                expected = {"code": "INVALID_VALUE", "cause": "fake frozen collision", "prompt_pointer": "/prompt/query/filter_field",
                            "byte_start": start, "byte_end": start + 8, "line": 1, "column": start + 1,
                            "end_line": 1, "end_column": start + 9}
                status = 400
                qa_raw = base_raw = raw_audit.canonical({"code": "INVALID_VALUE", "diagnostics": [expected]})
            data.exchange(QA, "/v1/compile", request, status, qa_raw)
            data.exchange(BASE, "/v1/compile", request, status, base_raw)
            rows.append({"phase": phase, "id": cid, "check": "compile_equal", "request_sha256": raw_audit.sha(request),
                         "qa_request_sha256": raw_audit.sha(request), "baseline_request_sha256": raw_audit.sha(request),
                         "qa_status": status, "baseline_status": status, "qa_response_sha256": raw_audit.sha(qa_raw),
                         "baseline_response_sha256": raw_audit.sha(base_raw), "passed": False})
            if spec["refusal"]:
                continue
            old_request = raw_audit.compact({"bundle": base_bundle})
            old_raw = b'{"code":"ASSET_HASH_MISMATCH"}'
            data.exchange(QA, "/v1/assemble", old_request, 400, old_raw)
            rows.append({"phase": phase, "id": cid, "check": "baseline_bundle_refused", "request_sha256": raw_audit.sha(old_request),
                         "qa_status": 400, "qa_response_sha256": raw_audit.sha(old_raw), "passed": False})
            rebased = copy.deepcopy(base_bundle)
            rebased["assets"]["backend"] = qa_bundle["assets"]["backend"]
            rebased["assets"]["target_contract"] = qa_bundle["assets"]["target_contract"]
            rebased["hashes"]["payload_sha256"] = raw_audit.sha(raw_audit.canonical({key: value for key, value in rebased.items() if key != "hashes"}))
            rebased_request = raw_audit.compact({"bundle": rebased})
            response = raw_audit.canonical({"files": files})
            data.exchange(QA, "/v1/assemble", rebased_request, 200, response)
            rows.append({"phase": phase, "id": cid, "check": "rebased_files", "request_sha256": raw_audit.sha(rebased_request),
                         "qa_status": 200, "qa_response_sha256": raw_audit.sha(response), "files_sha256": spec["files_sha256"],
                         "recorded_files_sha256": spec["files_sha256"], "passed": False})
        report = {"qa_url": QA, "baseline_url": BASE, "rows": rows}
        data.save()
        return data, report, specs

    def test_rust_rebase_refusal_and_raw_files_are_independently_checked(self):
        data, report, specs = self.rust_fixture()
        with mock.patch.object(raw_audit, "rust_sources", return_value=specs):
            result = raw_audit.validate_rust(data.reader, data.directory, report)
        self.assertEqual(result, {"cases": 2, "rows": 4, "exchanges": 6})

    def test_rust_product_digest_claim_cannot_hide_changed_product_bytes(self):
        data, report, specs = self.rust_fixture()
        call = data.manifest["calls"][3]
        changed = raw_audit.canonical({"files": {"Cargo.toml": "changed"}})
        data.change(call, "response", changed)
        report["rows"][2]["qa_response_sha256"] = raw_audit.sha(changed)
        data.save()
        with mock.patch.object(raw_audit, "rust_sources", return_value=specs):
            with self.assertRaisesRegex(raw_audit.BaselineRawError, "product bytes differ"):
                raw_audit.validate_rust(data.reader, data.directory, report)

    def test_rust_normalization_does_not_allow_dsl_or_json_type_changes(self):
        original = {"status": "ok", "bundle": bundle(prompt(), "rust")}
        changed = copy.deepcopy(original)
        changed["bundle"]["assets"]["backend"] = {"id": "allowed"}
        changed["bundle"]["hashes"]["payload_sha256"] = "0" * 64
        self.assertEqual(raw_audit.normalized(original), raw_audit.normalized(changed))
        changed["bundle"]["dsl"]["extra"] = True
        self.assertNotEqual(raw_audit.normalized(original), raw_audit.normalized(changed))
        changed["bundle"]["dsl"]["extra"] = 1
        self.assertNotEqual(raw_audit.canonical({"extra": True}), raw_audit.canonical({"extra": 1}))

    def controls_fixture(self, language="react"):
        contract = {"react": "react-output-v1", "ts": "typescript-output-v1", "rust": "rust-output-v1"}[language]
        data = Fixture(self.root, f"som-rs/tests/{contract}/check_python_controls.py")
        value = prompt()
        success = {"id": "RN01_FAKE" if language == "react" else "RT01_FAKE", "python_control_body": raw_audit.compact({"format": "som.prompt-json.v1", "prompt": value}).decode(),
                   "python_control_expected": "HTTP 200", "expected": {"code": "UNSUPPORTED_TARGET"}}
        expected = {"code": "INVALID_SCHEMA", "cause": "fake error", "allowed_form": "fake object", "byte_start": 0, "byte_end": 2,
                    "line": 1, "column": 1, "end_line": 1, "end_column": 3, "prompt_pointer": ""}
        refusal = {"id": "RP01_FAKE", "python_control_body": "[]", "python_control_expected": "the same diagnostic", "http_status": 400, "expected": expected}
        data.json(f"som-rs/contracts/{contract}/negative-cases.json", {"cases": [refusal] if language == "react" else [success, refusal]})
        if language != "rust":
            data.json(f"som-rs/contracts/{contract}/target-examples.json", {"name_cases": [success] if language == "react" else []})
        if language == "react":
            data.json(f"som-rs/contracts/{contract}/scope.json", {"prompt_versions": {"in_scope": ["som.prompt.v1"]}, "identifiers": {"reserved_names": ["__proto__"]}})
        elif language == "rust":
            data.json(f"som-rs/contracts/{contract}/scope.json", {})
        compiled = bundle(value)
        reply = compile_reply(compiled)
        refusal_reply = raw_audit.canonical({"code": expected["code"], "diagnostics": [expected]})
        ordered = [refusal, success] if language == "react" else [success, refusal]
        rows = []
        for case in ordered:
            status, response = (200, reply) if case is success else (400, refusal_reply)
            data.exchange(QA, "/v1/compile", case["python_control_body"].encode(), status, response)
            row = {"id": case["id"], "control_status": status, "passed": False}
            if status == 200:
                row["payload_sha256"] = compiled["hashes"]["payload_sha256"]
            rows.append(row)
        evidence_path, receipt_path = raw_audit.CONTROL_EVIDENCE[language]
        evidence = data.json(evidence_path, {"results": rows})
        data.json(receipt_path, {"files": {evidence_path: raw_audit.sha(evidence.read_bytes())}})
        data.save()
        return data, {"base_url": QA, "results": rows}

    def test_all_control_contracts_read_full_raw_successes_and_refusals(self):
        for language in ("react", "rust", "ts"):
            with self.subTest(language=language):
                self.root = self.root / language
                self.root.mkdir()
                data, report = self.controls_fixture(language)
                self.assertEqual(raw_audit.validate_controls(data.reader, data.directory, report, language), {"cases": 2, "exchanges": 2})

    def test_control_status_payload_claims_cannot_hide_wrong_diagnostic(self):
        data, report = self.controls_fixture()
        call = data.manifest["calls"][0]
        document = raw_audit.parse((data.directory / call["response_file"]).read_bytes())
        document["diagnostics"][0]["cause"] = "wrong"
        data.change(call, "response", raw_audit.canonical(document))
        data.save()
        with self.assertRaisesRegex(raw_audit.BaselineRawError, "member changed: cause"):
            raw_audit.validate_controls(data.reader, data.directory, report, "react")

    def test_rust_duplicate_member_exchange_is_required_and_checked_from_bytes(self):
        data, report = self.controls_fixture("rust")
        cases_path = "som-rs/contracts/rust-output-v1/negative-cases.json"
        cases = raw_audit.parse(data.reader.bytes(cases_path))
        duplicate = '{"target":"rust","target":"rust"}'
        start = duplicate.rindex('"target"')
        expected = {"code": "INVALID_JSON", "cause": "Duplicate JSON member.", "allowed_form": "One JSON value with unique keys.",
                    "prompt_pointer": "/target", "byte_start": start, "byte_end": start + 8,
                    "line": 1, "column": start + 1, "end_line": 1, "end_column": start + 9}
        cases["cases"][0].update(body=duplicate, http_status=400, expected=expected)
        data.json(cases_path, cases)
        with self.assertRaises(raw_audit.BaselineRawError) as caught:
            raw_audit.validate_controls(data.reader, data.directory, report, "rust")
        self.assertEqual(caught.exception.status, "unrun")
        call = data.exchange(QA, "/v1/compile", duplicate.encode(), 400,
                             raw_audit.canonical({"code": "INVALID_JSON", "diagnostics": [expected]}))
        data.save()
        self.assertEqual(raw_audit.validate_controls(data.reader, data.directory, report, "rust"), {"cases": 2, "exchanges": 3})
        wrong = {**expected, "cause": "Wrong duplicate diagnostic"}
        data.change(call, "response", raw_audit.canonical({"code": "INVALID_JSON", "diagnostics": [wrong]}))
        data.save()
        with self.assertRaisesRegex(raw_audit.BaselineRawError, "member changed: cause"):
            raw_audit.validate_controls(data.reader, data.directory, report, "rust")

    def test_rust_cnl_control_compiled_prompt_must_give_frozen_profile_refusal(self):
        data = Fixture(self.root, "som-rs/tests/rust-output-v1/check_python_controls.py")
        source = raw_audit.compact({"format": "som.cnl.v1", "source": "Fake public CNL."})
        scope = {"prompt_versions": {"in_scope": ["som.prompt.v1"], "out_of_scope": {"som.prompt.v6": "fake newer family"}},
                 "refusals": {"prompt_version": {"cause": "Version {version} uses {family}.", "allowed_form": "Use version 1."}},
                 "routes": {"fixed_top_level_paths": []}, "identifiers": {"reserved_tables": [], "not_raw_identifiers": []}}
        case = {"id": "RS01_CNL_FAKE", "form": "cnl", "python_control_body": source.decode(), "python_control_expected": "HTTP 200",
                "expected": {"code": "UNSUPPORTED_TARGET", "prompt_pointer": "/version", "cause": "Version 6 uses fake newer family.", "allowed_form": "Use version 1."}}
        data.json("som-rs/contracts/rust-output-v1/negative-cases.json", {"cases": [case]})
        data.json("som-rs/contracts/rust-output-v1/scope.json", scope)
        compiled = bundle({"version": "som.prompt.v6"})
        call = data.exchange(QA, "/v1/compile", source, 200, compile_reply(compiled))
        row = {"id": case["id"], "control_status": 200, "payload_sha256": compiled["hashes"]["payload_sha256"], "passed": False}
        evidence_path, receipt_path = raw_audit.CONTROL_EVIDENCE["rust"]
        evidence = data.json(evidence_path, {"results": [row]})
        data.json(receipt_path, {"files": {evidence_path: raw_audit.sha(evidence.read_bytes())}})
        data.save()
        report = {"base_url": QA, "results": [row]}
        self.assertEqual(raw_audit.validate_controls(data.reader, data.directory, report, "rust"), {"cases": 1, "exchanges": 1})
        wrong = bundle(prompt())
        data.change(call, "response", compile_reply(wrong))
        row["payload_sha256"] = wrong["hashes"]["payload_sha256"]
        evidence = data.json(evidence_path, {"results": [row]})
        data.json(receipt_path, {"files": {evidence_path: raw_audit.sha(evidence.read_bytes())}})
        data.save()
        with self.assertRaisesRegex(raw_audit.BaselineRawError, "profile refusal"):
            raw_audit.validate_controls(data.reader, data.directory, report, "rust")

    def test_sealed_control_payload_missing_or_changed_never_implicitly_passes(self):
        data, report = self.controls_fixture("ts")
        evidence_path, receipt_path = raw_audit.CONTROL_EVIDENCE["ts"]
        evidence = raw_audit.parse(data.reader.bytes(evidence_path))
        del evidence["results"][0]["payload_sha256"]
        filename = data.json(evidence_path, evidence)
        data.json(receipt_path, {"files": {evidence_path: raw_audit.sha(filename.read_bytes())}})
        with self.assertRaises(raw_audit.BaselineRawError) as caught:
            raw_audit.validate_controls(data.reader, data.directory, report, "ts")
        self.assertEqual(caught.exception.status, "unrun")
        evidence["results"][0]["payload_sha256"] = "0" * 64
        filename = data.json(evidence_path, evidence)
        data.json(receipt_path, {"files": {evidence_path: raw_audit.sha(filename.read_bytes())}})
        with self.assertRaisesRegex(raw_audit.BaselineRawError, "sealed historical baseline"):
            raw_audit.validate_controls(data.reader, data.directory, report, "ts")
        filename.write_bytes(filename.read_bytes() + b" ")
        with self.assertRaisesRegex(raw_audit.BaselineRawError, "frozen hash"):
            raw_audit.validate_controls(data.reader, data.directory, report, "ts")

    def test_duplicate_json_type_and_endpoint_refusals(self):
        for raw in (b'{"same":1,"same":2}', b'{"value":NaN}'):
            with self.assertRaises(raw_audit.BaselineRawError):
                raw_audit.parse(raw)
        for url in ("http://127.0.0.1:8765", "http://127.0.0.1:18765", "http://example.invalid:18919", QA + "/unexpected"):
            with self.assertRaises(raw_audit.BaselineRawError):
                raw_audit.endpoint(url)
        self.assertEqual((raw_audit.PUBLIC_COUNT, raw_audit.LEGACY_COUNT, raw_audit.RUST_PHASE1_COUNT, raw_audit.RUST_PHASE2_COUNT), (127, 544, 115, 584))

    def test_json_pointer_location_handles_unicode_and_repeated_values(self):
        raw = b'{"first":"same","nested":{"value":"same","unicode":"\\u00e9"}}'
        start = raw.index(b'"same"', raw.index(b'"nested"'))
        diagnostic = {"prompt_pointer": "/nested/value", "byte_start": start, "byte_end": start + 6,
                      "line": 1, "column": start + 1, "end_line": 1, "end_column": start + 7}
        raw_audit.verify_location(raw, diagnostic)
        diagnostic["byte_start"] = raw.index(b'"same"')
        with self.assertRaisesRegex(raw_audit.BaselineRawError, "pointer value"):
            raw_audit.verify_location(raw, diagnostic)

    def test_source_loader_binds_frozen_inputs_and_original_rust_file_records(self):
        data = Fixture(self.root, "som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py")
        public_prompt, legacy_prompt = prompt("public"), prompt("legacy")
        fixtures_path = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
        fixtures_file = data.json(fixtures_path, {"cases": [{"id": "PUBLIC_FAKE", "expected_prompt": public_prompt,
            "prompt_sha256": raw_audit.sha(raw_audit.canonical(public_prompt))}]})
        examples_index = "som-rs/contracts/compiler-v1/examples/index.json"
        examples_file = data.json(examples_index, {"cases": []})
        legacy_path = raw_audit.LEGACY + "/inventory-003.json"
        legacy = [{"id": "legacy-fake", "prompt_sha256": raw_audit.sha(raw_audit.canonical(legacy_prompt))}]
        legacy_file = data.json(legacy_path, {"groups": legacy})
        legacy_run = raw_audit.LEGACY + "/run-005"
        data.json(legacy_run + "/compile/" + legacy[0]["prompt_sha256"] + "/compile-json.request.json",
                  {"format": "som.prompt-json.v1", "prompt": legacy_prompt})
        data.json("som-rs/contracts/react-output-v1/case-inventory.json", {"sources": {
            "cnl_positive_fixtures": {fixtures_path: raw_audit.sha(fixtures_file.read_bytes())},
            "examples": {examples_index: raw_audit.sha(examples_file.read_bytes())},
            "legacy_inventory": {legacy_path: raw_audit.sha(legacy_file.read_bytes()), "run": legacy_run}}})
        prompt_file = data.json("som-rs/contracts/compiler-v1/examples/fake.prompt.json", public_prompt)
        coverage_path = "som-rs/tests/rust-output-v1/cases/index.json"
        coverage_file = data.json(coverage_path, {"cases": []})
        data.json("som-rs/contracts/rust-output-v1/case-inventory.json", {
            "sources": {"coverage_index": {coverage_path: raw_audit.sha(coverage_file.read_bytes())}},
            "public_examples": [{"id": "example-fake", "rust": "in_scope", "prompt": prompt_file.relative_to(data.repo).as_posix(),
                                 "prompt_file_sha256": raw_audit.sha(prompt_file.read_bytes())}],
            "version_positive_fixtures": [], "coverage_cases": [], "legacy_in_scope": []})
        data.json(raw_audit.RUST_ACCEPTED + "/rust-products.json", {"cases": [{"id": "example-fake", "files_sha256": "f" * 64}]})
        phase2_case = {"id": "legacy-fake", "prompt_sha256": legacy[0]["prompt_sha256"], "phase2_expected": {"result": "product"}}
        data.json("som-rs/contracts/rust-output-v1/phase2/case-inventory.json", {"rows": [phase2_case]})
        record_file = data.json(raw_audit.RUST_ACCEPTED + "/phase2/cases/legacy_fake.json", {
            "id": "legacy-fake", "prompt_sha256": legacy[0]["prompt_sha256"], "rust": {"files_sha256": "e" * 64}})
        data.json(raw_audit.RUST_ACCEPTED + "/phase2/summary.json", {"case_sha256": {"legacy-fake": raw_audit.sha(record_file.read_bytes())}})
        with mock.patch.multiple(raw_audit, PUBLIC_COUNT=1, LEGACY_COUNT=1, RUST_PHASE1_COUNT=1, RUST_PHASE2_COUNT=1):
            specs = raw_audit.rust_sources(data.reader)
            self.assertEqual(set(specs), {("phase1", "example-fake"), ("phase2", "legacy-fake")})
            self.assertEqual(specs[("phase2", "legacy-fake")]["prompt"], legacy_prompt)
            record_file.write_bytes(record_file.read_bytes() + b" ")
            with self.assertRaisesRegex(raw_audit.BaselineRawError, "source JSON differs"):
                raw_audit.rust_sources(data.reader)


if __name__ == "__main__":
    unittest.main()
