"""Synthetic full companion/baseline fixtures. Execute on GitHub only."""
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_map_rust_inputs as fixtures

SPEC = importlib.util.spec_from_file_location("complete_rust_inputs", Path(__file__).with_name("complete_rust_inputs.py"))
c = importlib.util.module_from_spec(SPEC)
# Reuse the existing complete synthetic mapper fixture, including its pin mocks.
with patch.dict(sys.modules, {"map_rust_inputs": fixtures.m}):
    SPEC.loader.exec_module(c)
m = fixtures.m


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RustMappingTests("test_complete_named_mapping_preserves_forms_refusals_and_variants")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.repo, self.inputs, self.out = f.repo, f.inputs, f.root / "completed"
        self.prepare_sources_and_goldens()
        self.prepare_baseline()
        extras = patch.object(c, "EXTRA_PINS", self.extra_pins)
        extras.start()
        self.addCleanup(extras.stop)
        baseline_pin = patch.object(c, "BASELINE_SHA256", m.sha(self.baseline_path.read_bytes()))
        baseline_pin.start()
        self.addCleanup(baseline_pin.stop)
        @contextmanager
        def fake_serializer(_sources):
            yield lambda prompt, *, statement_map: "Synthetic serialized CNL " + m.value_sha(prompt) + "\n"
        runtime = patch.object(c, "serializer_runtime", fake_serializer)
        runtime.start()
        self.addCleanup(runtime.stop)

    def prepare_sources_and_goldens(self):
        f = self.fixture
        # Eight new public fixture files replace eight metadata-only fillers.
        fillers = [path for path in f.public_files if path.startswith("public-fixture-metadata/")][:8]
        for path in fillers:
            del f.public_files[path]
        bases = {
            "v1": {"version": "som.prompt.v1", "package": "fixture1", "resource": {"table": "items", "path": "/items", "fields": [{"name": "title"}]}},
            "v3": {"version": "som.prompt.v3", "package": "fixture3", "resource": {"table": "items", "path": "/items", "fields": [{"name": "title"}, {"name": "count"}, {"name": "active", "type": "bool"}]}, "query": {"filter_field": "active"}},
            "v4": {"version": "som.prompt.v4", "package": "fixture4", "resource": {"path": "/items", "table": "items", "fields": [{"name": "title"}]}},
            "v5": {"version": "som.prompt.v5", "resources": {"child": {"fields": [{"name": "title"}]}}, "relation": {"foreign_key": "parent_id"}, "package": "fixture5"},
        }
        for name, body in bases.items():
            f.source(c.EXAMPLES + name + ".prompt.json", body, pinned=False)
        # Independent explicit expected values catch edits to the wrong field.
        recipes = {}
        p = deepcopy(bases["v3"]); p["resource"]["fields"][0]["name"] = "self"; recipes[7] = ("v3", p)
        p = deepcopy(bases["v1"]); p["resource"]["path"] = "/items/"; recipes[9] = ("v1", p)
        p = deepcopy(bases["v4"]); p["resource"]["path"] = "/healthz"; recipes[11] = ("v4", p)
        p = deepcopy(bases["v3"]); p["resource"]["fields"][2]["name"] = "limit"; p["query"]["filter_field"] = "limit"; recipes[12] = ("v3", p)
        p = deepcopy(bases["v5"]); p["relation"]["foreign_key"] = "self"; recipes[14] = ("v5", p)
        p = deepcopy(bases["v5"]); p["resources"]["child"]["fields"][0]["name"] = "a__b"; p["relation"]["foreign_key"] = "self"; recipes[16] = ("v5", p)
        p = deepcopy(bases["v1"]); p["resource"]["path"] = "/items/"; p["resource"]["fields"][0]["name"] = "Title"; recipes[17] = ("v1", p)
        p = deepcopy(bases["v5"]); p["resources"]["child"]["fields"][0]["name"] = "a__b"; p["capabilities"] = {"database": "postgresql", "auth": "jwt", "storage": "none", "workflow": "json_export", "value_field": None, "group_field": None}; recipes[20] = ("v5", p)
        p = deepcopy(bases["v1"]); p["resource"]["table"] = "_sqlx_migrations"; recipes[21] = ("v1", p)
        negatives = json.loads((self.repo / m.NEGATIVE).read_text())
        p2 = json.loads((self.repo / m.P2).read_text())
        self.rs_typed = {}
        shared = {1: f.prompts[10], 2: f.prompts[11], 3: f.prompts[10], 4: f.prompts[11],
                  5: {"synthetic_RS": "upper"}, 6: {"synthetic_RS": "upper"},
                  18: {"synthetic_RS": "caps"}, 19: {"synthetic_RS": "caps"}}
        for row in negatives["cases"]:
            number = int(row["id"][2:4])
            if number in recipes:
                base, prompt = recipes[number]
                row["base_example"] = base + ".cnl"
                frozen = next(x for x in p2["rows"] if x["id"] == row["id"])
                golden = next(x for x in f.goldens["cohorts"]["rust_phase2"] if x["id"] == row["id"])
                frozen["prompt_sha256"] = golden["prompt_sha256"] = m.value_sha(prompt)
            else:
                prompt = shared.get(number, {"synthetic_RS": number})
            self.rs_typed[row["id"]] = prompt
        f.source(m.NEGATIVE, negatives)
        f.source(m.P2, p2)
        for path in [c.BUILDER, c.WRITER, c.replay.SERIALIZER]:
            f.source(path, b"# synthetic source pin; not executed\n", pinned=False)
        f.source(c.replay.STATEMENT_MAP, {"synthetic": True}, pinned=False)
        self.extra_pins = {path: f.public_files[path]["raw_sha256"] for path in [c.BUILDER, c.WRITER, c.replay.SERIALIZER, c.replay.STATEMENT_MAP]}
        f.goldens["public_source_files"] = list(f.public_files.values())
        self.assertEqual(len(f.goldens["public_source_files"]), 35)

    def input_prompt(self, cohort, index):
        return json.loads((self.inputs / f"{cohort}/{index:04d}/compile-json.request.raw").read_bytes())["prompt"]

    def prepare_baseline(self):
        f = self.fixture
        p1 = json.loads((self.repo / m.P1).read_text())
        prompts = {}
        public = {row["id"]: row["index"] for row in f.goldens["cohorts"]["python_public"]}
        for group in ["public_examples", "version_positive_fixtures", "coverage_cases", "legacy_in_scope"]:
            for row in p1[group]:
                if group == "public_examples": prompt = json.loads((self.repo / row["prompt"]).read_text())
                elif group == "coverage_cases": prompt = json.loads((self.repo / ("som-rs/tests/rust-output-v1/cases/" + row["id"] + ".prompt.json")).read_text())
                elif group == "legacy_in_scope": prompt = self.input_prompt("python_legacy", row["inventory_index"])
                else: prompt = self.input_prompt("python_public", public[row["id"]])
                prompts[("phase1", row["id"])] = prompt
        p2 = json.loads((self.repo / m.P2).read_text())["rows"]
        for row in p2:
            prompt = self.rs_typed[row["id"]] if row["kind"] == "rs" else self.input_prompt("python_legacy" if row["kind"] == "legacy" else "python_public", row["position"])
            prompts[("phase2", row["id"])] = prompt
        self.baseline_rows = []
        for index, ((phase, identity), prompt) in enumerate(prompts.items()):
            # Select each permitted encoding independently in this frozen fixture.
            if index % 2:
                prompt = json.loads(json.dumps(prompt, ensure_ascii=False, sort_keys=True))
            raw = m.compact({"format": "som.prompt-json.v1", "target": "rust", "prompt": prompt})
            status = 400 if identity in m.REFUSALS else 200
            row = {"id": identity, "phase": phase, "check": "compile_equal", "passed": True, "status": "passed", "row_index": len(self.baseline_rows),
                   "qa_request_sha256": m.sha(raw), "baseline_request_sha256": m.sha(raw), "request_sha256": m.sha(raw),
                   "qa_status": status, "baseline_status": status, "qa_response_sha256": "9" * 64}
            self.baseline_rows.append(row)
            if status == 200:
                self.baseline_rows.extend({"id": identity, "phase": phase, "check": name} for name in ["baseline_bundle_refused", "rebased_files"])
        self.baseline_path = self.repo / c.BASELINE_FILE
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_baseline()

    def write_baseline(self):
        document = {"version": "som.ts065-accepted-response-hashes.v1", "public_repository": "faberline/som",
                    "public_source_revision": m.PUBLIC_REVISION, "product_acceptance_passed": False,
                    "baselines": {"rust": {"rows": self.baseline_rows}}}
        self.baseline_path.write_text(json.dumps(document))

    def run_completion(self):
        return c.complete(self.repo, self.inputs, self.fixture.goldens, self.out, "a" * 40)

    def test_all699_paired_and_baseline_requests_keep_validation_unrun(self):
        result = self.run_completion()
        self.assertEqual(result["counts"]["baseline_full_request_hashes_verified"], 699)
        self.assertEqual(result["counts"]["both_forms_supplied"], 699)
        self.assertEqual(result["counts"]["typed_prompt_deferred"], 0)
        self.assertFalse(result["full_input_set_ready"])
        self.assertFalse(result["product_acceptance_passed"])
        self.assertEqual(set(result["baseline_request_encoding_counts"]), {"preserved-source-object-order", "recursive-sorted-object-keys"})
        phase2 = {row["id"]: row for row in result["cohorts"]["rust_phase2"]}
        native = json.loads((self.repo / m.NEGATIVE).read_text())["cases"]
        for negative in native:
            row = phase2[negative["id"]]
            self.assertEqual((self.out / row["requests"][negative["form"]]["path"]).read_bytes(), negative["body"].encode())
            self.assertEqual((self.out / row["python_control_native"]["path"]).read_bytes(), negative["python_control_body"].encode())
            self.assertEqual(row["cnl_refusal_validation"], "unrun")
            request = (self.out / row["baseline_json_request"]["path"]).read_bytes()
            self.assertEqual(m.sha(request), row["baseline_json_request"]["provenance"]["accepted_qa_request_sha256"])
        collision = phase2["RS12_CNL_V3_FILTER_LIMIT"]
        prompt = json.loads((self.out / collision["requests"]["json"]["path"]).read_bytes())["prompt"]
        self.assertEqual(prompt["resource"]["fields"][2]["name"], "limit")
        self.assertEqual(prompt["resource"]["fields"][0]["name"], "title")
        self.assertEqual(collision["phase2_expected"]["http_status"], 400)
        self.assertNotEqual((self.out / phase2["RS13_JSON_V3_FILTER_OFFSET"]["requests"]["json"]["path"]).read_bytes(),
                            (self.out / phase2["RS13_JSON_V3_FILTER_OFFSET"]["baseline_json_request"]["path"]).read_bytes())
        self.assertEqual(len(list(self.out.rglob("*.raw"))), 2119)

    def test_wrong_RS12_field_edit_cannot_match_frozen_typed_hash(self):
        recipes = deepcopy(c.TYPED_RECIPES)
        recipes["RS12_CNL_V3_FILTER_LIMIT"][1][0] = ("replace", "/resource/fields/0/name", "limit")
        with patch.object(c, "TYPED_RECIPES", recipes), self.assertRaisesRegex(m.Refusal, "constructed typed companion differs"):
            self.run_completion()
        self.assertFalse(self.out.exists())

    def test_third_header_encoding_is_refused_even_with_same_values(self):
        row = next(x for x in self.baseline_rows if x["check"] == "compile_equal")
        p1 = json.loads((self.repo / m.P1).read_text())["public_examples"][0]
        prompt = json.loads((self.repo / p1["prompt"]).read_text())
        raw = m.compact({"format": "som.prompt-json.v1", "prompt": prompt, "target": "rust"})
        for key in ["qa_request_sha256", "baseline_request_sha256", "request_sha256"]: row[key] = m.sha(raw)
        self.write_baseline()
        with patch.object(c, "BASELINE_SHA256", m.sha(self.baseline_path.read_bytes())), self.assertRaisesRegex(m.Refusal, "full Rust baseline request bytes differ"):
            self.run_completion()
        self.assertFalse(self.out.exists())

    def test_baseline_artifact_mutation_cannot_repin_itself(self):
        self.baseline_path.write_bytes(self.baseline_path.read_bytes() + b" ")
        with self.assertRaisesRegex(m.Refusal, "baseline artifact digest"):
            self.run_completion()
        self.assertFalse(self.out.exists())

    def test_duplicate_accepted_case_is_refused_after_fixture_repin(self):
        compiled = [row for row in self.baseline_rows if row["check"] == "compile_equal"]
        compiled[-1]["id"] = compiled[-2]["id"]
        self.write_baseline()
        with patch.object(c, "BASELINE_SHA256", m.sha(self.baseline_path.read_bytes())), self.assertRaisesRegex(m.Refusal, "duplicate or unpassed"):
            self.run_completion()
        self.assertFalse(self.out.exists())

    def test_changed_pinned_serializer_source_fails_before_any_runtime(self):
        path = self.repo / c.replay.SERIALIZER
        path.write_bytes(path.read_bytes() + b"# changed\n")
        with self.assertRaisesRegex(m.Refusal, "source digest"):
            self.run_completion()
        self.assertFalse(self.out.exists())

    def test_existing_output_is_preserved(self):
        self.out.mkdir()
        sentinel = self.out / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(m.Refusal, "output must be new"):
            self.run_completion()
        self.assertEqual(sentinel.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
