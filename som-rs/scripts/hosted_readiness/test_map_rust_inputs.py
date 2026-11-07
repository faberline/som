"""Synthetic complete Rust request-mapping fixtures. Execute on GitHub only."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("map_rust_inputs", Path(__file__).with_name("map_rust_inputs.py"))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

RS_NAMES = ["CNL_V6", "CNL_V7", "JSON_V6", "JSON_V7", "CNL_FIELD_UPPERCASE", "JSON_FIELD_UPPERCASE",
            "CNL_FIELD_SELF", "JSON_CHILD_FIELD_DOUBLE_UNDERSCORE", "CNL_PATH_TRAILING_SLASH", "JSON_PATH_DOUBLE_SLASH",
            "CNL_PATH_HEALTHZ", "CNL_V3_FILTER_LIMIT", "JSON_V3_FILTER_OFFSET", "CNL_V5_FOREIGN_KEY_SELF",
            "JSON_V5_FOREIGN_KEY_CRATE", "CNL_V5_FIRST_REFUSAL", "CNL_PATH_BEFORE_FIELD", "CNL_V5_CAPABILITIES",
            "JSON_V5_CAPABILITIES", "CNL_V5_CAPABILITIES_FIRST", "CNL_TABLE_SQLX_MIGRATIONS", "JSON_TABLE_BEFORE_FIELD"]


class RustMappingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repo, self.inputs, self.out = [self.root / name for name in ["repo", "inputs", "mapped"]]
        self.repo.mkdir()
        self.inputs.mkdir()
        self.pins, self.public_files, self.supplement_pins = {}, {}, {}
        self.fixture()
        self.pin_context = patch.object(m, "SOURCE_PINS", self.pins)
        self.pin_context.start()
        self.addCleanup(self.pin_context.stop)
        requests = patch.object(m, "SUPPLEMENT_REQUEST_PINS", self.supplement_pins)
        requests.start()
        self.addCleanup(requests.stop)

    def source(self, path, value, *, pinned=True):
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode() if not isinstance(value, bytes) else value
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
        digest = m.sha(raw)
        if pinned:
            self.pins[path] = digest
        self.public_files[path] = {"path": path, "revision": m.PUBLIC_REVISION, "raw_sha256": digest}
        return digest

    def input_case(self, cohort, index, identity, prompt, cnl):
        requests = {}
        for label, body in [("compile-json", {"format": "som.prompt-json.v1", "prompt": prompt}),
                            ("compile-cnl", {"format": "som.cnl.v1", "source": cnl})]:
            path = f"{cohort}/{index:04d}/{label}.request.raw"
            file = self.inputs / path
            file.parent.mkdir(parents=True, exist_ok=True)
            raw = m.compact(body)
            file.write_bytes(raw)
            requests[label] = {"path": path, "sha256": m.sha(raw)}
        return {"id": identity, "index": index, "state": "reconstructed", "prompt_sha256": m.value_sha(prompt),
                "cnl_sha256": m.sha(cnl.encode()), "requests": requests}

    def fixture(self):
        self.prompts = [{"version": "som.prompt.v7", "synthetic_public": i} for i in range(127)]
        public_rows, public_golds, legacy_rows, legacy_golds = [], [], [], []
        for i, prompt in enumerate(self.prompts):
            row = self.input_case("python_public", i, "pub-" + str(i), prompt, "Public CNL " + str(i) + "\n")
            public_rows.append(row)
            public_golds.append({key: row[key] for key in ["id", "index", "prompt_sha256", "cnl_sha256"]})
            public_golds[-1]["request_sha256"] = {name: item["sha256"] for name, item in row["requests"].items()}
        for i in range(544):
            duplicate = 100 if i == 101 else 200 if i == 201 else 300 if i in {301, 302} else i
            prompt = {"version": "som.prompt.v7", "synthetic_legacy": duplicate}
            row = self.input_case("python_legacy", i, "legacy-" + str(i), prompt, "Legacy CNL " + str(duplicate) + "\n")
            legacy_rows.append(row)
            family = "legacy100" if i < 100 else "legacy214" if i < 314 else "quality218" if i < 532 else "supplement12"
            group = ("legacy100" if i < 100 else "services14" if i < 114 else "compositions120" if i < 234 else
                     "blind80" if i < 314 else "m2-quality218" if i < 532 else "m2-supplement12")
            variant = "checked" if i in {441, 532, 533} else "standard"
            legacy_golds.append({**{key: row[key] for key in ["id", "index", "prompt_sha256", "cnl_sha256"]},
                                 "family": family, "group": group, "variant": variant})
            if i < 443:
                legacy_golds[-1]["typed_request_sha256"] = row["requests"]["compile-json"]["sha256"]
            elif i >= 533:
                self.supplement_pins[row["id"]] = (row["prompt_sha256"], row["requests"]["compile-json"]["sha256"])
        manifest = {"version": "som.public-input-reconstruction.v2", "typed_input_set_ready": True,
                    "golden_export_sha256": m.GOLDENS_SHA256, "source_revision": m.PUBLIC_REVISION,
                    "product_acceptance_passed": False, "cohorts": {"python_public": public_rows, "python_legacy": legacy_rows}}
        (self.inputs / "manifest.json").write_text(json.dumps(manifest))
        selected, phase1 = {key: [] for key in ["public_examples", "version_positive_fixtures", "coverage_cases", "legacy_in_scope"]}, []
        for i in range(5):
            path = f"som-rs/contracts/compiler-v1/examples/fake-{i}.prompt.json"
            raw_hash = self.source(path, self.prompts[i], pinned=False)
            row = {"id": "phase1-example-" + str(i), "rust": "in_scope", "prompt": path,
                   "prompt_file_sha256": raw_hash, "cnl_sha256": public_rows[i]["cnl_sha256"]}
            selected["public_examples"].append(row)
            phase1.append({"id": row["id"], "group": "public_examples", "prompt_file_sha256": raw_hash,
                           "prompt_sha256": None, "files_map_sha256": "1" * 64})
        for i in range(5, 9):
            row = {"id": public_rows[i]["id"], "rust": "in_scope", "prompt_sha256": public_rows[i]["prompt_sha256"],
                   "cnl_sha256": public_rows[i]["cnl_sha256"]}
            selected["version_positive_fixtures"].append(row)
            phase1.append({"id": row["id"], "group": "cnl_positive_fixtures:version_positive", "prompt_sha256": row["prompt_sha256"],
                           "prompt_file_sha256": None, "files_map_sha256": "1" * 64})
        coverage = []
        for i in range(6):
            base = Path(m.COVERAGE).parent
            raw_hash = self.source((base / f"cover-{i}.prompt.json").as_posix(), {"version": "som.prompt.v1", "synthetic_coverage": i})
            cnl_hash = self.source((base / f"cover-{i}.cnl").as_posix(), ("Coverage CNL " + str(i) + "\n").encode())
            row = {"id": "cover-" + str(i), "prompt": f"cover-{i}.prompt.json", "cnl": f"cover-{i}.cnl",
                   "prompt_file_sha256": raw_hash, "cnl_sha256": cnl_hash}
            coverage.append(row)
            selected["coverage_cases"].append({"id": row["id"], "rust": "in_scope", "prompt_file_sha256": raw_hash, "cnl_sha256": cnl_hash})
            phase1.append({"id": row["id"], "group": "coverage_cases", "prompt_file_sha256": raw_hash,
                           "prompt_sha256": None, "files_map_sha256": "1" * 64})
        for i in range(100):
            row = legacy_rows[i]
            selected["legacy_in_scope"].append({"id": row["id"], "inventory_index": i, "prompt_sha256": row["prompt_sha256"]})
            phase1.append({"id": row["id"], "group": "legacy:legacy100", "prompt_sha256": row["prompt_sha256"],
                           "prompt_file_sha256": None, "files_map_sha256": "1" * 64})
        self.source(m.P1, selected)
        self.source(m.COVERAGE, {"cases": coverage})
        frozen2, phase2 = [], []
        for i in range(100, 544):
            g = legacy_golds[i]
            frozen2.append({"id": g["id"], "kind": "legacy", "family": g["family"], "group": g["group"],
                            "checker": "verify_all" if g["family"] == "legacy214" else "flow",
                            "standard": g["group"] == "services14" if g["family"] == "legacy214" else True,
                            "prompt_sha256": g["prompt_sha256"], "position": i, "phase2_expected": {"result": "product"}})
        for i in range(9, 127):
            frozen2.append({"id": public_rows[i]["id"], "kind": "cnl_fixture" if i < 124 else "public_example",
                            "family": "public", "group": "fixture", "checker": "flow", "standard": True,
                            "prompt_sha256": public_rows[i]["prompt_sha256"], "position": i, "phase2_expected": {"result": "product"}})
        negatives = []
        shared = {1: self.prompts[10], 2: self.prompts[11], 3: self.prompts[10], 4: self.prompts[11],
                  5: {"synthetic_RS": "upper"}, 6: {"synthetic_RS": "upper"},
                  18: {"synthetic_RS": "caps"}, 19: {"synthetic_RS": "caps"}}
        for number, name in enumerate(RS_NAMES, 1):
            identity = f"RS{number:02d}_" + name
            form = "json" if name.startswith("JSON") else "cnl"
            prompt = shared.get(number, {"synthetic_RS": number})
            body = {"format": "som.prompt-json.v1", "prompt": prompt} if form == "json" else {"format": "som.cnl.v1", "source": "Frozen RS CNL " + str(number) + "\n"}
            control = m.compact(body).decode()
            body["target"] = "rust"
            # Noncompact spacing proves the mapper copies native request bytes.
            raw = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
            collision = number in {12, 13}
            expected = {"code": "INVALID_VALUE" if collision else "UNSUPPORTED_TARGET", "cause": "fixture collision" if collision else "phase1 only",
                        "prompt_pointer": "/prompt/query/filter_field" if form == "json" else "/query/filter_field",
                        "line": 1, "column": 2, "end_line": 1, "end_column": 3, "byte_start": 1, "byte_end": 2, "allowed_form": "rename"}
            negatives.append({"id": identity, "form": form, "body": raw, "python_control_body": control,
                              "python_control_expected": "the same diagnostic" if collision else "HTTP200 product", "http_status": 400 if collision else 422,
                              "expected": expected})
            phase_expected = {"result": "refusal", "rule": "python_name_collisions", "code": "INVALID_VALUE", "http_status": 400,
                              "detail": "fixture collision", "pointer": "/query/filter_field"} if collision else {"result": "product"}
            frozen2.append({"id": identity, "kind": "rs", "family": "rs", "group": form, "checker": "phase1_suite", "standard": True,
                            "prompt_sha256": m.value_sha(prompt), "position": number, "phase2_expected": phase_expected})
        self.source(m.NEGATIVE, {"cases": negatives})
        self.source(m.P2, {"rows": frozen2})
        for row in frozen2:
            g = {key: row[key] for key in ["id", "kind", "family", "group", "checker", "standard", "prompt_sha256"]}
            g["expected"] = {key: value for key, value in row["phase2_expected"].items() if key in {"result", "rule", "code", "http_status"}}
            g["files_map_sha256"] = None if row["phase2_expected"]["result"] == "refusal" else "2" * 64
            if row["kind"] == "legacy":
                g["assembly_variant"] = legacy_golds[row["position"]]["variant"]
            phase2.append(g)
        while len(self.public_files) < 35:
            self.source(f"public-fixture-metadata/{len(self.public_files)}.json", {"fixture": True}, pinned=False)
        self.goldens = {"version": "som.public-golden-hashes.v1", "public_repository": "faberline/som",
                        "public_source_revision": m.PUBLIC_REVISION, "product_acceptance_passed": False,
                        "public_source_files": list(self.public_files.values()),
                        "cohorts": {"python_public": public_golds, "python_legacy": legacy_golds, "rust_phase1": phase1, "rust_phase2": phase2}}

    def run_mapping(self):
        return m.map_inputs(self.repo, self.inputs, self.goldens, self.out, "a" * 40)

    def test_complete_named_mapping_preserves_forms_refusals_and_variants(self):
        result = self.run_mapping()
        self.assertEqual({name: len(rows) for name, rows in result["cohorts"].items()}, m.COUNTS)
        self.assertFalse(result["full_input_set_ready"])
        self.assertFalse(result["product_acceptance_passed"])
        self.assertEqual(result["known_reused_python_request_byte_checks"],
                         {"python_public": 254, "python_legacy": 454, "supplement_subset": 11})
        rows = result["cohorts"]["rust_phase2"]
        rs = {row["id"]: row for row in rows if row["kind"] == "rs"}
        originals = json.loads((self.repo / m.NEGATIVE).read_text())["cases"]
        for source in originals:
            row = rs[source["id"]]
            self.assertEqual((self.out / row["requests"][source["form"]]["path"]).read_bytes(), source["body"].encode())
            self.assertEqual((self.out / row["python_control_native"]["path"]).read_bytes(), source["python_control_body"].encode())
        deferred = [row for row in rows if row["requests"]["json"] is None]
        self.assertEqual(len(deferred), 9)
        self.assertTrue(all(row["canonical_typed_prompt_sha256"] is None and "deferred" in row["typed_prompt_proof"] for row in deferred))
        self.assertEqual(sum(row["requests"]["cnl"] is None for row in rows), 5)
        self.assertEqual(sum(row["assembly_variant"] == "checked" for row in rows), 3)
        self.assertEqual(sum(row["expected"]["result"] == "refusal" for row in rows), 2)
        for identity in m.REFUSALS:
            self.assertEqual(rs[identity]["expected"]["http_status"], 400)
            self.assertEqual(rs[identity]["expected"]["code"], "INVALID_VALUE")
            self.assertTrue(rs[identity]["frozen_negative_diagnostic"]["current_phase2_authority"])
        self.assertEqual(len([row for row in rows if row["id"] in {"legacy-100", "legacy-101"}]), 2)

    def test_changed_public_source_or_coverage_is_refused(self):
        for path in [m.P2, "som-rs/tests/rust-output-v1/cases/cover-0.prompt.json"]:
            file = self.repo / path
            before = file.read_bytes()
            file.write_bytes(before + b" ")
            with self.assertRaisesRegex(m.Refusal, "source digest"):
                self.run_mapping()
            file.write_bytes(before)

    def test_forged_input_body_fails_even_if_manifest_request_sha_is_updated(self):
        # This generator row has no recorded raw request hash. Its canonical
        # prompt must still bind independently of the mutable runtime receipt.
        file = self.inputs / "python_legacy/0450/compile-json.request.raw"
        file.write_bytes(m.compact({"format": "som.prompt-json.v1", "prompt": {"changed": True}}))
        manifest_path = self.inputs / "manifest.json"
        report = json.loads(manifest_path.read_text())
        report["cohorts"]["python_legacy"][450]["requests"]["compile-json"]["sha256"] = m.sha(file.read_bytes())
        manifest_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(m.Refusal, "prompt or CNL digest"):
            self.run_mapping()

    def test_same_values_with_changed_request_bytes_fail_after_mutable_manifest_rehash(self):
        for cohort, index, label in [("python_public", 0, "compile-json"), ("python_public", 0, "compile-cnl"),
                                     ("python_legacy", 0, "compile-json"), ("python_legacy", 533, "compile-json")]:
            file = self.inputs / f"{cohort}/{index:04d}/{label}.request.raw"
            before = file.read_bytes()
            # Preserve every JSON value, including the exact CNL string. Only
            # object spacing changes. The mutable receipt is re-signed too.
            changed = (json.dumps(json.loads(before), ensure_ascii=False, indent=2) + "\n").encode()
            self.assertNotEqual(changed, before)
            self.assertEqual(json.loads(changed), json.loads(before))
            file.write_bytes(changed)
            manifest = self.inputs / "manifest.json"
            original_manifest = manifest.read_bytes()
            report = json.loads(original_manifest)
            report["cohorts"][cohort][index]["requests"][label]["sha256"] = m.sha(changed)
            manifest.write_text(json.dumps(report))
            with self.subTest(cohort=cohort, index=index, label=label), self.assertRaisesRegex(m.Refusal, "known frozen Python request bytes differ"):
                self.run_mapping()
            self.assertFalse(self.out.exists())
            file.write_bytes(before)
            manifest.write_bytes(original_manifest)

    def test_missing_python_named_row_is_refused(self):
        manifest_path = self.inputs / "manifest.json"
        report = json.loads(manifest_path.read_text())
        report["cohorts"]["python_legacy"].pop()
        manifest_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(m.Refusal, "named case scope"):
            self.run_mapping()

    def test_duplicate_rust_name_is_refused_after_source_repin(self):
        p2 = json.loads((self.repo / m.P2).read_text())
        p2["rows"][-1]["id"] = p2["rows"][-2]["id"]
        self.source(m.P2, p2)
        self.goldens["public_source_files"] = list(self.public_files.values())
        with self.assertRaisesRegex(m.Refusal, "named case scope"):
            self.run_mapping()

    def test_old422_refusal_cannot_replace_current400_rule(self):
        p2 = json.loads((self.repo / m.P2).read_text())
        row = next(row for row in p2["rows"] if row["id"] == "RS13_JSON_V3_FILTER_OFFSET")
        row["phase2_expected"]["http_status"] = 422
        self.source(m.P2, p2)
        self.goldens["public_source_files"] = list(self.public_files.values())
        next(row for row in self.goldens["cohorts"]["rust_phase2"] if row["id"] == "RS13_JSON_V3_FILTER_OFFSET")["expected"]["http_status"] = 422
        with self.assertRaisesRegex(m.Refusal, "name-collision refusal"):
            self.run_mapping()

    def test_native_rs_envelope_cannot_be_silently_retargeted(self):
        negatives = json.loads((self.repo / m.NEGATIVE).read_text())
        body = json.loads(negatives["cases"][0]["body"])
        body["target"] = "python"
        negatives["cases"][0]["body"] = json.dumps(body)
        self.source(m.NEGATIVE, negatives)
        self.goldens["public_source_files"] = list(self.public_files.values())
        with self.assertRaisesRegex(m.Refusal, "native/control envelope"):
            self.run_mapping()

    def test_symlink_input_is_refused(self):
        file = self.inputs / "python_public/0000/compile-json.request.raw"
        saved = file.with_name("saved.raw")
        file.rename(saved)
        file.symlink_to(saved)
        with self.assertRaisesRegex(m.Refusal, "symlink"):
            self.run_mapping()

    def test_existing_output_is_preserved(self):
        self.out.mkdir()
        sentinel = self.out / "preserve.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(m.Refusal, "output must be new"):
            self.run_mapping()
        self.assertEqual(sentinel.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
