"""Synthetic request-proof fixtures. Run on GitHub; never start a service."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("reconstruct_ts125_requests", HERE / "reconstruct_ts125_requests.py")
e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e)


def bundle(prompt):
    value = {"version": "som.dsl-bundle.v2", "target": "typescript", "prompt": prompt, "dsl": {"version": "fixture", "files": []}, "assets": {}}
    value["hashes"] = {"prompt_sha256": e.sha(e.canonical(prompt)), "dsl_sha256": e.sha(e.canonical(value["dsl"])), "payload_sha256": e.sha(e.canonical(value))}
    return value


def before_op(form, request=None):
    return {"form": form, "method": "POST", "endpoint": "/v1/assemble" if form == "bundle" else "/v1/compile", "format": "som.dsl-bundle.v2" if form == "bundle" else "som.prompt-json.v1" if form == "json" else "som.cnl.v1", "request_sha256": request, "response_sha256": "a" * 64, "http_status": 200, "http_status_authority": "synthetic_recorded_report"}


class RequestProofTests(unittest.TestCase):
    def case(self, group="public_examples", forms=("json", "cnl"), index=0):
        prompt = {"version": "som.prompt.v1", "value": "snow 雪", "items": [2, 1]}
        value = {"row_index": index, "id": f"case-{index}", "inventory_group": group, "prompt_sha256": e.sha(e.canonical(prompt)), "forms": list(forms), "compile": [before_op(f) for f in forms], "assemble": before_op("bundle"), "bundle_hashes": bundle(prompt)["hashes"]}
        if group == "legacy_in_scope":
            value["legacy_inventory_index"] = index + 100
        return value, prompt, {f: bundle(prompt) for f in forms}

    def test_01_compact_exact_bytes_preserve_unicode_header_and_nested_order(self):
        prompt = {"z": "雪", "a": [2, 1], "n": 1.5}
        expected = '{"format":"som.prompt-json.v1","prompt":{"z":"雪","a":[2,1],"n":1.5},"target":"typescript"}'.encode()
        self.assertEqual(e.standard_request("json", prompt, None), expected)
        self.assertEqual(e.standard_request("cnl", None, "one\ntwo"), b'{"format":"som.cnl.v1","source":"one\\ntwo","target":"typescript"}')

    def test_02_same_values_different_member_order_cannot_match_frozen_request(self):
        expected = b'{"format":"som.prompt-json.v1","prompt":{"z":1,"a":2},"target":"typescript"}'
        before = before_op("json", e.sha(expected))
        candidate = e.standard_request("json", {"a": 2, "z": 1}, None)
        self.assertEqual(json.loads(expected), json.loads(candidate))
        with self.assertRaisesRegex(e.old.Refusal, "direct accepted request hash"):
            e.operation("json", before, candidate, "compact_header_format_prompt_target", [])

    def test_03_assembly_canonicalizes_members_but_keeps_list_order_and_values(self):
        first = {"z": [2, 1], "a": "雪"}
        second = {"a": "雪", "z": [2, 1]}
        self.assertEqual(e.assemble_request(first), e.assemble_request(second))
        self.assertEqual(e.assemble_request(first), '{"bundle":{"a":"雪","z":[2,1]}}'.encode())
        self.assertNotEqual(e.assemble_request(first), e.assemble_request({"a": "雪", "z": [1, 2]}))

    def test_04_recorded_null_stays_null_while_reconstruction_has_separate_authority(self):
        result = e.operation("bundle", before_op("bundle"), b'{"bundle":{}}', "canonical_sorted_recursive_bundle_envelope", [])
        self.assertIsNone(result["recorded_request_sha256"])
        self.assertEqual(result["authority"], "reconstructed_not_recorded")
        self.assertFalse(result["recorded_request_field_changed"])
        self.assertEqual(result["reconstructed_request_sha256"], e.sha(b'{"bundle":{}}'))

    def test_05_legacy_cnl_order_gap_never_calls_a_cnl_parser_or_fills_a_hash(self):
        case, prompt, bundles = self.case("legacy_in_scope")
        body = {"format": "som.prompt-json.v1", "prompt": prompt}
        with patch.object(e, "standard_request", side_effect=AssertionError("must not infer legacy CNL bytes")):
            row = e.case_operations(case, {}, prompt, "known bound source", bundles, [{"cnl_text_sha256": "b" * 64}], body)
        self.assertIsNone(row["compile"][1]["reconstructed_request_sha256"])
        self.assertIn("unbound_loaded_request_member_order", row["compile"][1]["authority"])
        self.assertIsNotNone(row["compile"][0]["reconstructed_request_sha256"])
        self.assertIsNotNone(row["assemble"]["reconstructed_request_sha256"])

    def test_06_legacy_typed_request_preserves_loaded_header_and_prompt_order(self):
        case, prompt, bundles = self.case("legacy_in_scope", ("json",))
        body = {"prompt": prompt, "format": "som.prompt-json.v1"}
        expected = json.dumps({"prompt": prompt, "format": "som.prompt-json.v1", "target": "typescript"}, ensure_ascii=False, separators=(",", ":")).encode()
        case["compile"][0]["request_sha256"] = e.sha(expected)
        row = e.case_operations(case, {}, prompt, None, bundles, [], body)
        self.assertEqual(row["compile"][0]["reconstructed_request_sha256"], e.sha(expected))
        body["prompt"] = {"version": "som.prompt.v1", "value": "different"}
        with self.assertRaisesRegex(e.old.Refusal, "typed input body differs"):
            e.case_operations(case, {}, prompt, None, bundles, [], body)

    def test_07_name_json_uses_exact_frozen_raw_body_including_spaces(self):
        case, prompt, bundles = self.case("name_cases", ("json",))
        raw = json.dumps({"format": "som.prompt-json.v1", "prompt": prompt, "target": "typescript"}, ensure_ascii=False, indent=2).encode()
        source = {"form": "json", "body": raw.decode(), "body_sha256": e.sha(raw)}
        case["compile"][0]["request_sha256"] = e.sha(raw)
        row = e.case_operations(case, source, prompt, None, bundles, [])
        self.assertEqual(row["compile"][0]["reconstructed_request_sha256"], e.sha(raw))
        source["body"] += " "
        with self.assertRaisesRegex(e.old.Refusal, "name request body hash"):
            e.case_operations(case, source, prompt, None, bundles, [])

    def test_08_name_derived_json_uses_old_cnl_response_prompt_member_order(self):
        case, prompt, bundles = self.case("name_cases", ("cnl", "json"))
        cnl_prompt = {"items": prompt["items"], "value": prompt["value"], "version": prompt["version"]}
        bundles["cnl"] = bundle(cnl_prompt)
        source = {"form": "cnl", "body": "known CNL request bytes", "body_sha256": e.sha(b"known CNL request bytes")}
        row = e.case_operations(case, source, prompt, None, bundles, [])
        expected = json.dumps({"format": "som.prompt-json.v1", "prompt": cnl_prompt, "target": "typescript"}, ensure_ascii=False, separators=(",", ":")).encode()
        self.assertEqual(row["compile"][1]["reconstructed_request_sha256"], e.sha(expected))
        self.assertIsNone(row["compile"][1]["recorded_request_sha256"])

    def test_09_accepted_response_needs_raw_and_canonical_integrity(self):
        prompt = {"version": "som.prompt.v1"}
        doc = {"status": "ok", "bundle": bundle(prompt)}
        raw = json.dumps(doc).encode()
        self.assertEqual(e.compile_bundle(raw, e.sha(raw)), doc["bundle"])
        with self.assertRaisesRegex(e.old.Refusal, "raw pin"):
            e.compile_bundle(raw + b" ", e.sha(raw))
        doc["bundle"]["prompt"]["value"] = "tampered"
        changed = json.dumps(doc).encode()
        with self.assertRaisesRegex(e.old.Refusal, "canonical proof"):
            e.compile_bundle(changed, e.sha(changed))

    def test_10_fixture_or_bundle_prompt_change_blocks_source_association(self):
        case, prompt, bundles = self.case()
        bundles["json"] = bundle({"version": "som.prompt.v1", "different": True})
        with self.assertRaisesRegex(e.old.Refusal, "accepted bundle prompt differ"):
            e.case_operations(case, {}, prompt, "source", bundles, [])

    def test_11_complete_125_scope_keeps_duplicates_and_all_100_cnl_gaps(self):
        rows = []
        for i in range(125):
            group = "legacy_in_scope" if i >= 25 else "name_cases" if i >= 15 else "public_examples"
            forms = ("json",) if 21 <= i < 25 else ("cnl", "json") if 15 <= i < 21 else ("json", "cnl")
            case, prompt, bundles = self.case(group, forms, i)
            source = {"form": forms[0], "body": "frozen body", "body_sha256": e.sha(b"frozen body")} if group == "name_cases" else {}
            legacy = {"format": "som.prompt-json.v1", "prompt": prompt} if group == "legacy_in_scope" else None
            rows.append(e.case_operations(case, source, prompt, "bound CNL", bundles, [], legacy))
        self.assertEqual(len(rows), 125)
        self.assertEqual(sum(len(r["compile"]) for r in rows), 246)
        self.assertEqual(sum(op["reconstructed_request_sha256"] is not None for r in rows for op in r["compile"]), 146)
        self.assertEqual(sum(r["assemble"]["reconstructed_request_sha256"] is not None for r in rows), 125)
        self.assertEqual(len({r["id"] for r in rows}), 125)
        self.assertEqual(len({r["prompt_sha256"] for r in rows}), 1)

    def calibration_rows(self):
        return [{"id": "case-0", "compile": [{"recorded_request_sha256": "a" * 64, "direct_recorded_calibration": "matched"} for _ in range(140)], "assemble": {"reconstructed_request_sha256": "b" * 64}}, {"id": "case-1", "compile": [], "assemble": {"reconstructed_request_sha256": "c" * 64}}]

    def test_12_full_140_calibrations_required_not_a_sample(self):
        rows = self.calibration_rows()
        canaries = [{"case_id": "case-0", "step": "assemble", "request_sha256": "b" * 64}, {"case_id": "case-1", "step": "assemble", "request_sha256": "c" * 64}]
        self.assertEqual(e.calibration(rows, canaries)["direct_compile_request_hashes"], 140)
        rows[0]["compile"].pop()
        with self.assertRaisesRegex(e.old.Refusal, "full direct compile"):
            e.calibration(rows, canaries)

    def test_13_both_separate_canary_assembly_encodings_must_match(self):
        rows = self.calibration_rows()
        canaries = [{"case_id": "case-0", "step": "assemble", "request_sha256": "b" * 64}, {"case_id": "case-1", "step": "assemble", "request_sha256": "d" * 64}]
        with self.assertRaisesRegex(e.old.Refusal, "encoding calibration"):
            e.calibration(rows, canaries)
        self.assertNotIn("recorded_request_sha256", rows[0]["assemble"])

    def test_14_writer_pin_must_match_all_before_and_after_header_maps(self):
        path = "som-rs/scripts/typescript_output_acceptance/check_target.py"
        raw = b"synthetic pinned writer, never executed"
        record = {"head": e.TOOL_REVISION, "files": {path: e.sha(raw)}}
        header = {"before": {"tools": copy.deepcopy(record)}, "after": {"tools": copy.deepcopy(record)}, "tools_before": copy.deepcopy(record), "tools_after": copy.deepcopy(record), "tools": copy.deepcopy(record)}
        with patch.dict(e.WRITERS, {path: e.sha(raw)}, clear=True), patch.object(e, "source_bytes", return_value=raw):
            self.assertEqual(len(e.writer_bindings(header, Path("unused"))), 1)
            header["after"]["tools"]["files"][path] = "f" * 64
            with self.assertRaisesRegex(e.old.Refusal, "before/after writer pin"):
                e.writer_bindings(header, Path("unused"))

    def test_15_writer_lookup_reads_one_fixed_git_blob_without_shell_or_module_import(self):
        path = "som-rs/scripts/typescript_output_acceptance/check_target.py"
        raw = b"fixed source bytes"
        with patch.dict(e.WRITERS, {path: e.sha(raw)}, clear=True), patch.object(e.subprocess, "run", return_value=SimpleNamespace(stdout=raw)) as run:
            self.assertEqual(e.source_bytes(Path("/repo"), path), raw)
            args, kwargs = run.call_args
            self.assertEqual(args[0], ["git", "--no-pager", "-C", "/repo", "show", e.TOOL_REVISION + ":" + path])
            self.assertNotIn("shell", kwargs)
            with self.assertRaisesRegex(e.old.Refusal, "not fixed"):
                e.source_bytes(Path("/repo"), "unbound.py")
            self.assertEqual(run.call_count, 1)

    def test_16_input_file_digest_and_json_scalar_types_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = b'{"value":1}'
            (root / "input.json").write_bytes(raw)
            self.assertEqual(e.frozen_raw(root, "input.json", e.sha(raw)), raw)
            (root / "input.json").write_bytes(b'{"value":1.0}')
            with self.assertRaisesRegex(e.old.Refusal, "digest differs"):
                e.frozen_raw(root, "input.json", e.sha(raw))
        self.assertNotEqual(e.compact({"value": 1}), e.compact({"value": 1.0}))
        with self.assertRaises(ValueError):
            e.compact({"value": float("nan")})


if __name__ == "__main__":
    unittest.main()
