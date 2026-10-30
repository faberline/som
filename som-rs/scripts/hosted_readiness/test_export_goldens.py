"""Synthetic metadata fixtures only. Run on GitHub; never use private baselines."""
import base64
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("export_goldens", Path(__file__).with_name("export_goldens.py"))
e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.base = self.root / "PRIVATE_LOCAL_SENTINEL/.local/rust-compiler-goal/acceptance"
        self.base.mkdir(parents=True)
        self.local = {}
        self.public = {}
        self.files = {"PRIVATE_PATH_SENTINEL/app.py": "PRIVATE_RESPONSE_CONTENT_SENTINEL\n"}
        self.product_sha = e.value_sha(self.files)
        self.file_shas = {path: e.sha(text.encode()) for path, text in self.files.items()}
        self.raw_response = json.dumps({"status": "ok", "files": self.files}, separators=(",", ":")).encode()
        self.route_path = self.root / "som-m12-baseline-route.json"
        self.out = self.root / "goldens.json"
        self.fixture()
        trusted = patch.object(e, "REVIEWED_ROUTE_SHA256", e.value_sha(self.route))
        trusted.start()
        self.addCleanup(trusted.stop)

    def export(self, route=None):
        route = self.route if route is None else route
        # A synthetic reviewed root permits isolated structural negative fixtures.
        with patch.object(e, "REVIEWED_ROUTE_SHA256", e.value_sha(route)):
            return e.build_export(route, self.repo)

    def local_bytes(self, path, raw):
        file = self.base / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
        self.local[str(file)] = {"path": str(file), "raw_sha256": e.sha(raw), "roles": []}
        return e.sha(raw)

    def local_json(self, path, value):
        return self.local_bytes(path, json.dumps(value, indent=2).encode())

    def public_json(self, path, value):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(value, indent=2).encode()
        file.write_bytes(raw)
        self.public[path] = {"path": path, "revision": e.PUBLIC_REVISION, "raw_sha256": e.sha(raw)}

    def fixture(self, duplicate_phase1=False, prompt_shape=None, checker_scope=None):
        pub_records, pub_routes, fixture_cases, examples = [], [], [], []
        for index in range(127):
            prompt = {"version": "som.prompt.v7", "public_fixture": index}
            cnl = "PUBLIC CNL " + str(index)
            if index < 119:
                identity = "public-" + str(index)
                fixture_cases.append({"id": identity, "expected_prompt": prompt, "cnl": cnl})
                locator = {"path": e.FIXTURES, "prompt_pointer": f"/cases/{index}/expected_prompt", "cnl_pointer": f"/cases/{index}/cnl"}
            else:
                name = "example-" + str(index)
                identity = "PUBLIC_" + name
                examples.append({"id": name, "prompt": name + ".prompt.json", "cnl": name + ".cnl"})
                self.public_json(e.EXAMPLES + name + ".prompt.json", prompt)
                file = self.repo / (e.EXAMPLES + name + ".cnl")
                file.write_text(cnl)
                self.public[file.relative_to(self.repo).as_posix()] = {"path": file.relative_to(self.repo).as_posix(), "revision": e.PUBLIC_REVISION, "raw_sha256": e.sha(file.read_bytes())}
                locator = {"prompt_path": e.EXAMPLES + name + ".prompt.json", "cnl_path": e.EXAMPLES + name + ".cnl"}
            pub_records.append({"id": identity, "passed": True, "product_sha256": self.product_sha})
            pub_routes.append({"id": identity, "prompt_sha256": e.value_sha(prompt), "cnl_sha256": e.sha(cnl.encode()), "reconstruction": locator})
            for label, body in [("compile-json", {"format": "som.prompt-json.v1", "prompt": prompt}), ("compile-cnl", {"format": "som.cnl.v1", "source": cnl})]:
                self.local_bytes(f"public-products-003b/{index:03d}/{label}.request.raw", json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())
            self.local_bytes(f"public-products-003b/{index:03d}/assemble.response.raw.b64", base64.b64encode(self.raw_response))
        self.public_json(e.FIXTURES, {"cases": fixture_cases})
        self.public_json(e.EXAMPLES + "index.json", {"cases": examples})
        public_sha = self.local_json(e.PUBLIC_RESULT, {"fixture_sha256": self.public[e.FIXTURES]["raw_sha256"], "complete": True, "required": 127, "passed": 127, "records": pub_records})
        source_path = "som-core/data/composed/regression.jsonl"
        dataset, legacy_records, legacy_routes, groups = [], [], [], []
        for index in range(544):
            family = "legacy100" if index < 100 else "legacy214" if index < 314 else "quality218" if index < 532 else "supplement12"
            maximum = 442 <= index < 532
            blocked = index >= 533
            prompt_index = index if index < 440 else 428 if index in {440, 441, 532} else 5000 + index if maximum else 10000 + min(index - 533, 9)
            prompt = {"version": "som.prompt.v7", "fixture": prompt_index}
            identity = "legacy-" + str(index)
            variant = "checked" if index in {441, 532, 533} else "standard"
            partition = ("maximum-matrix" if maximum else "supplement12" if family == "supplement12" else
                         "formal114" if 314 <= index < 428 else "dev12" if 428 <= index < 440 else
                         "alternative-plans" if 440 <= index < 442 else None)
            group_name = ("legacy100" if index < 100 else "services14" if index < 114 else "compositions120" if index < 234 else
                          "blind80" if index < 314 else "m2-quality218" if index < 532 else "m2-supplement12")
            record = {"id": identity, "family": family, "group": group_name, "partition": partition, "variant": variant,
                      "prompt_sha256": e.value_sha(prompt), "cnl_sha256": e.sha(identity.encode()), "product_sha256": self.product_sha,
                      "product_file_sha256": self.file_shas, "behavior_expected": partition not in {"maximum-matrix", "alternative-plans"}, "passed": True}
            legacy_records.append(record)
            groups.append({**{key: record[key] for key in ["id", "family", "group", "partition", "variant", "prompt_sha256"]},
                           "behavior": record["behavior_expected"], "gold_product_sha256": self.product_sha})
            route = {"id": identity, "prompt_sha256": record["prompt_sha256"], "route": e.BLOCKED if blocked else e.GENERATED if maximum else e.DIRECT}
            if route["route"] == e.DIRECT:
                dataset.append({"prompt": prompt})
                request = (json.dumps({"format": "som.prompt-json.v1", "prompt": prompt}, ensure_ascii=False, indent=2) + "\n").encode()
                self.local_bytes(f"legacy544/run-005/compile/{record['prompt_sha256']}/compile-json.request.json", request)
                route["reconstruction_sources"] = [{"path": source_path, "line_1based": len(dataset), "member": "/prompt"}]
            if variant == "checked":
                route["checked_dsl_route"] = "BLOCKED: no public plan" if family == "supplement12" else "public reference"
            legacy_routes.append(route)
            self.local_bytes(f"legacy544/run-005/{index:04d}/assemble.response.raw.b64", base64.b64encode(self.raw_response))
        file = self.repo / source_path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("\n".join(json.dumps(row) for row in dataset) + "\n")
        self.public[source_path] = {"path": source_path, "revision": e.PUBLIC_REVISION, "raw_sha256": e.sha(file.read_bytes())}
        for row in legacy_routes:
            for locator in row.get("reconstruction_sources", []):
                locator["source_file_sha256"] = self.public[source_path]["raw_sha256"]
        for path in ["som-core/scripts/probe_flow_maximum_matrix.py", "som-core/scripts/freeze_flow_cases.py"]:
            file = self.repo / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("# public generator fixture; never executed\n")
            self.public[path] = {"path": path, "revision": e.PUBLIC_REVISION, "raw_sha256": e.sha(file.read_bytes())}
        self.local_json(e.INVENTORY, {"groups": groups})
        inventory_sha = e.value_sha({"groups": groups})
        self.local[str(self.base / e.INVENTORY)]["accepted_bindings"] = [{"rule": "canonical JSON sha256", "digest": inventory_sha}]
        legacy_sha = self.local_json(e.LEGACY_RESULT, {"status": "passed", "required_job_count": 544, "passed_job_count": 544, "compile_prompt_count": 540, "inventory_sha256": inventory_sha, "records": legacy_records})
        phase1_ids = ["rust-public-" + str(i) for i in range(15)] + [row["id"] for row in legacy_records[:100]]
        phase1 = []
        for index, identity in enumerate(phase1_ids):
            group = ("public_examples" if index < 5 else "cnl_positive_fixtures:version_positive" if index < 9 else
                     "coverage_cases" if index < 15 else "legacy:legacy100")
            field = "prompt_file_sha256" if index < 5 or 9 <= index < 15 else "prompt_sha256"
            phase1.append({"id": identity, "group": group, "version": "som.prompt.v7", field: "1" * 64, "files_sha256": self.product_sha})
        if prompt_shape == "wrong":
            phase1[5]["prompt_file_sha256"] = phase1[5].pop("prompt_sha256")
        elif prompt_shape == "both":
            phase1[5]["prompt_file_sha256"] = "1" * 64
        elif prompt_shape == "missing":
            del phase1[5]["prompt_sha256"]
        self.public_json(e.P1, {"public_examples": [{"id": x, "rust": "in_scope"} for x in phase1_ids[:5]], "version_positive_fixtures": [{"id": x, "rust": "in_scope"} for x in phase1_ids[5:9]], "coverage_cases": [{"id": x} for x in phase1_ids[9:15]], "legacy_in_scope": [{"id": x} for x in phase1_ids[15:]]})
        frozen2 = []
        for row in legacy_records[100:]:
            frozen2.append({"id": row["id"], "kind": "legacy", "family": row["family"], "group": row["group"],
                            "checker": "verify_all" if row["family"] == "legacy214" else "flow",
                            "standard": row["group"] == "services14" if row["family"] == "legacy214" else True,
                            "prompt_sha256": row["prompt_sha256"], "phase2_expected": {"result": "product"}})
        if checker_scope == "composition":
            frozen2[14]["standard"] = True
        elif checker_scope == "checked_flow":
            frozen2[341]["standard"] = False
        elif checker_scope == "checker":
            frozen2[0]["checker"] = "flow"
        for index in range(140):
            frozen2.append({"id": "rust-extra-" + str(index), "kind": "rs" if index >= 118 else "cnl_fixture", "family": "rs" if index >= 118 else "public", "group": "fixture", "checker": "fixture", "standard": True, "prompt_sha256": "2" * 64,
                            "phase2_expected": {"result": "refusal", "rule": "fixture", "code": "INVALID_VALUE", "http_status": 400} if index >= 138 else {"result": "product"}})
        self.public_json(e.P2, {"rows": frozen2})
        case_hashes = {}
        for row in frozen2:
            raw = {"id": row["id"], "prompt_sha256": row["prompt_sha256"], "passed": True,
                   "rust": {"files_sha256": self.product_sha} if row["phase2_expected"]["result"] == "product" else
                   {"compile_status": 422, "refusal": {"code": "UNSUPPORTED_TARGET"}}}
            key = re.sub(r"[^A-Za-z0-9]+", "_", row["id"]).strip("_")
            case_hashes[row["id"]] = self.local_json(e.RUNS[1] + "/phase2/cases/" + key + ".json", raw)
        phase2_sha = self.local_json(e.RUNS[1] + "/phase2/summary.json", {"case_sha256": case_hashes, "complete": True, "passed": 584, "total": 584, "failed": [], "missing": []})
        rust_anchors = []
        for run in e.RUNS:
            header_sha = self.local_json(run + "/header.json", {"fixture": True})
            report_sha = self.local_json(run + "/rust-products.json", {"cases": phase1 + phase1[:1] if duplicate_phase1 else phase1})
            steps = []
            for index, (name, outputs) in enumerate([("rust-products", {"rust-products.json": report_sha})] + ([("phase2-products", {"phase2/summary.json": phase2_sha})] if run == e.RUNS[1] else []), 1):
                receipt_sha = self.local_json(run + f"/receipts/{index:02d}-{name}.json", {"outputs": outputs})
                steps.append({"step": name, "receipt_sha256": receipt_sha})
            summary_sha = self.local_json(run + "/summary.json", {"status": "passed", "header_sha256": header_sha, "steps": steps})
            rust_anchors.append({"header_sha256": header_sha, "summary_sha256": summary_sha})
        self.public_json(e.ANCHOR16, {"results": {"python_replay": {"inputs": {"public_result_sha256": public_sha, "legacy_result_sha256": legacy_sha}}}, "local_only": rust_anchors[0]})
        self.public_json(e.ANCHOR14, {"gate_receipts": {"public127": {"sha256": public_sha}, "legacy544": {"sha256": legacy_sha}}})
        self.public_json(e.ANCHOR20, {"local_only": rust_anchors[1]})
        specs = {e.ANCHOR16: ["/results/python_replay/inputs/public_result_sha256", "/results/python_replay/inputs/legacy_result_sha256", "/local_only/header_sha256", "/local_only/summary_sha256"], e.ANCHOR14: ["/gate_receipts/public127/sha256", "/gate_receipts/legacy544/sha256"], e.ANCHOR20: ["/local_only/header_sha256", "/local_only/summary_sha256"]}
        anchors = []
        for path, pointers in specs.items():
            doc = json.loads((self.repo / path).read_text())
            anchors.append({**self.public[path], "accepted_digest_pointers": {p: e.pointer(doc, p) for p in pointers}})
        self.route = {"version": "som.m12-baseline-route.private.v1", "classification": e.PRIVATE, "public_repository": "faberline/som", "public_source_revision": e.PUBLIC_REVISION,
                      "public_sources": list(self.public.values()), "local_accepted_sources": list(self.local.values()), "accepted_public_anchors": anchors,
                      "public127_ordered_reconstruction": pub_routes, "legacy544_ordered_reconstruction": legacy_routes,
                      "blocked11": [{"id": row["id"], "prompt_sha256": row["prompt_sha256"]} for row in legacy_routes if row["route"] == e.BLOCKED],
                      "checked_dsl2": [{"id": legacy_routes[i]["id"]} for i in [532, 533]]}

    def test_complete_named_hash_export_keeps_scopes_and_omits_private_values(self):
        result = self.export()
        self.assertEqual({name: len(rows) for name, rows in result["cohorts"].items()}, e.COUNTS)
        self.assertEqual(result["counts"]["legacy_distinct_prompts"], 540)
        self.assertEqual(result["counts"]["legacy_blocked_typed_rows"], 11)
        self.assertEqual(result["counts"]["legacy_generator_rows_unrun"], 90)
        self.assertFalse(result["product_acceptance_passed"])
        self.assertEqual(result["public_source_files"], sorted(self.public.values(), key=lambda row: row["path"]))
        self.assertTrue(all(set(row) == {"path", "revision", "raw_sha256"} for row in result["public_source_files"]))
        raw = json.dumps(result)
        for private in ["PRIVATE_LOCAL_SENTINEL", "PRIVATE_PATH_SENTINEL", "PRIVATE_RESPONSE_CONTENT_SENTINEL", "public_fixture"]:
            self.assertNotIn(private, raw)
        phase1 = result["cohorts"]["rust_phase1"]
        self.assertIsNone(phase1[0]["accepted_per_file_hashes"])
        self.assertEqual(sum(row["prompt_file_sha256"] is not None for row in phase1), 11)
        self.assertEqual(sum(row["prompt_sha256"] is not None for row in phase1), 104)
        self.assertIsNone(phase1[5]["prompt_file_sha256"])
        self.assertEqual(phase1[5]["prompt_sha256"], "1" * 64)
        phase2 = result["cohorts"]["rust_phase2"]
        self.assertIsNotNone(phase2[0]["files_map_sha256"])
        self.assertEqual(sum(row["kind"] == "legacy" and not row["standard"] for row in phase2), 200)
        checked = [row for row in phase2 if row.get("assembly_variant") == "checked"]
        self.assertEqual(len(checked), 3)
        self.assertTrue(all(row["checker"] == "flow" and row["standard"] for row in checked))
        refused = [row for row in result["cohorts"]["rust_phase2"] if row["expected"]["result"] == "refusal"]
        self.assertEqual(len(refused), 2)
        self.assertEqual(refused[0]["expected"]["code"], "INVALID_VALUE")
        self.assertEqual(refused[0]["historical_refusal_observation"]["code"], "UNSUPPORTED_TARGET")
        self.assertIn("/phase2_expected", refused[0]["expected_source"]["pointer"])
        self.assertIn("unrun", result["raw_compile_reply_comparison"])

    def test_changed_source_or_public_anchor_is_refused(self):
        for path in [self.base / e.PUBLIC_RESULT, self.repo / e.ANCHOR16]:
            before = path.read_bytes()
            path.write_bytes(before + b" ")
            with self.assertRaises(e.Refusal):
                e.build_export(self.route, self.repo)
            path.write_bytes(before)

    def test_incomplete_named_cohort_and_blocked_scope_are_refused(self):
        for field in ["public127_ordered_reconstruction", "legacy544_ordered_reconstruction", "blocked11", "checked_dsl2"]:
            route = copy.deepcopy(self.route)
            route[field].pop()
            with self.assertRaises(e.Refusal):
                self.export(route)

    def test_product_tamper_fails_even_with_updated_route_file_hash(self):
        path = self.base / "legacy544/run-005/0000/assemble.response.raw.b64"
        path.write_bytes(base64.b64encode(json.dumps({"status": "ok", "files": {"other.py": "changed"}}).encode()))
        self.local[str(path)]["raw_sha256"] = e.sha(path.read_bytes())
        with self.assertRaisesRegex(e.Refusal, "product-map"):
            self.export()

    def test_raw_inventory_digest_cannot_replace_the_canonical_binding(self):
        row = self.local[str(self.base / e.INVENTORY)]
        row["accepted_bindings"][0]["digest"] = row["raw_sha256"]
        with self.assertRaisesRegex(e.Refusal, "binding"):
            self.export()

    def test_private_review_packet_and_existing_output_are_refused(self):
        self.route_path.write_text(json.dumps({"version": "som.m12-private-fixture-review.v1", "classification": e.PRIVATE}))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(e.main(["--route", str(self.route_path), "--repo", str(self.repo), "--out", str(self.out)]), 2)
        self.assertFalse(self.out.exists())
        self.route_path.write_text(json.dumps(self.route))
        self.out.write_text("preserve")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(e.main(["--route", str(self.route_path), "--repo", str(self.repo), "--out", str(self.out)]), 2)
        self.assertEqual(self.out.read_text(), "preserve")

    def test_modified_route_cannot_repin_changed_public_bytes(self):
        path = self.repo / e.ANCHOR16
        path.write_bytes(path.read_bytes() + b" ")
        route = copy.deepcopy(self.route)
        for row in route["public_sources"] + route["accepted_public_anchors"]:
            if row["path"] == e.ANCHOR16:
                row["raw_sha256"] = e.sha(path.read_bytes())
        with self.assertRaisesRegex(e.Refusal, "reviewed metadata root"):
            e.build_export(route, self.repo)

    def test_extra_fixture_locator_fields_are_never_exported(self):
        route = copy.deepcopy(self.route)
        route["public127_ordered_reconstruction"][0]["reconstruction"]["private"] = "PRIVATE_LOCATOR_SENTINEL"
        self.assertNotIn("PRIVATE_LOCATOR_SENTINEL", json.dumps(self.export(route)))

    def test_duplicate_phase1_report_rows_are_refused(self):
        self.local, self.public = {}, {}
        self.fixture(duplicate_phase1=True)
        with self.assertRaisesRegex(e.Refusal, "duplicate or missing"):
            self.export()

    def test_phase1_prompt_hash_fields_must_match_the_accepted_group_schema(self):
        for shape in ["wrong", "both", "missing"]:
            with self.subTest(shape=shape):
                self.local, self.public = {}, {}
                self.fixture(prompt_shape=shape)
                with self.assertRaisesRegex(e.Refusal, "prompt digest fields"):
                    self.export()

    def test_behavior_checker_scope_is_independent_of_assembly_variant(self):
        for scope in ["composition", "checked_flow", "checker"]:
            with self.subTest(scope=scope):
                self.local, self.public = {}, {}
                self.fixture(checker_scope=scope)
                with self.assertRaisesRegex(e.Refusal, "behavior checker scope"):
                    self.export()

    def test_direct_typed_request_serialization_is_verified(self):
        row = self.route["legacy544_ordered_reconstruction"][0]
        path = self.base / f"legacy544/run-005/compile/{row['prompt_sha256']}/compile-json.request.json"
        path.write_bytes(path.read_bytes().rstrip())
        self.local[str(path)]["raw_sha256"] = e.sha(path.read_bytes())
        with self.assertRaisesRegex(e.Refusal, "typed request serialization"):
            self.export()

    def test_symlink_source_is_refused(self):
        path = self.base / e.PUBLIC_RESULT
        saved = path.with_name("saved.json")
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaisesRegex(e.Refusal, "symlink"):
            e.build_export(self.route, self.repo)


if __name__ == "__main__":
    unittest.main()
