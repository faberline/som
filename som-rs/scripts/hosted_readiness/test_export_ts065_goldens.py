"""Synthetic source fixtures. Execute on GitHub, without private baseline files."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("export_ts065_goldens", Path(__file__).with_name("export_ts065_goldens.py"))
e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.private = self.root / "PRIVATE_HOST_PATH_SENTINEL"
        self.private.mkdir()
        self.route_path = self.root / "route.private.json"
        self.output = self.root / "out.json"
        self.fixture()

    @staticmethod
    def hashed(value):
        return hashlib.sha256(str(value).encode()).hexdigest()

    def local(self, role, value, binary=False):
        path = self.private / ("source-" + str(len(self.sources)))
        raw = value if binary else (json.dumps(value, indent=2) + "\n").encode()
        path.write_bytes(raw)
        self.sources.append({"path": str(path), "raw_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "roles": [role], "accepted_bindings": []})
        self.roles[role] = path
        self.documents[role] = value
        return hashlib.sha256(raw).hexdigest()

    def public(self, path, value):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(value, indent=2) + "\n").encode()
        file.write_bytes(raw)
        row = {"path": path, "revision": e.PUBLIC_REVISION, "raw_sha256": hashlib.sha256(raw).hexdigest()}
        self.public_sources.append(row)
        return row

    def bind(self, role, parent, pointer, rule="raw sha256"):
        expected = self.release if parent == "public release" else self.documents[parent]
        for part in pointer.split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            expected = expected[int(part)] if isinstance(expected, list) else expected[part]
        row = next(row for row in self.sources if role in row["roles"])
        row["accepted_bindings"].append({"source_role": parent, "pointer": pointer, "rule": rule, "digest": expected})

    def fixture(self, bad_status=False, extra_descriptor=False, rebased_shape=None):
        self.sources, self.roles, self.documents, self.public_sources = [], {}, {}, []
        public_ids = ["public-" + str(i) for i in range(127)]
        legacy_ids = ["legacy-" + str(i) for i in range(544)]
        phase1 = ["phase1-" + str(i) for i in range(15)] + legacy_ids[:100]
        phase2 = legacy_ids[100:] + ["phase2-" + str(i) for i in range(140)]
        refusals = set(phase2[-2:])
        p1 = {"public_examples": [{"id": name, "rust": "in_scope"} for name in phase1[:5]],
              "version_positive_fixtures": [{"id": name, "rust": "in_scope"} for name in phase1[5:9]],
              "coverage_cases": [{"id": name, "rust": "in_scope"} for name in phase1[9:15]],
              "legacy_in_scope": [{"id": name} for name in legacy_ids[:100]]}
        p2 = {"rows": [{"id": name, "kind": "legacy" if name in legacy_ids else "rs", "phase2_expected": {"result": "refusal" if name in refusals else "product"}} for name in phase2]}
        p1_pin, p2_pin = self.public(e.P1, p1), self.public(e.P2, p2)
        self.public(e.FIXTURES, {"cases": [{"id": name, "expected_prompt": {"raw": "PRIVATE_PROMPT_BODY_SENTINEL"}, "cnl": "PRIVATE_CNL_BODY_SENTINEL"} for name in public_ids]})
        self.public(e.EXAMPLES + "index.json", {"cases": []})
        for i in range(31):
            self.public("public/source-" + str(i) + ".json", {"public": i})
        py, rs = [], []
        for source, names in [("public", public_ids), ("legacy", legacy_ids)]:
            for name in names:
                for variant in ["none", "python"]:
                    row = {"id": name, "source": source, "variant": variant, "passed": True, "status": "passed", "response": {"body": "PRIVATE_RESPONSE_BODY_SENTINEL"}}
                    row.update({key: self.hashed("python:" + name + ":" + variant + ":" + key.replace("baseline_", "").replace("qa_", "")) for key in e.PY_HASHES})
                    row.update({key: 200 for key in e.PY_STATUSES})
                    py.append(row)
        for phase, names in [("phase1", phase1), ("phase2", phase2)]:
            for name in names:
                checks = ["compile_equal"] if phase == "phase2" and name in refusals else ["compile_equal", "baseline_bundle_refused", "rebased_files"]
                for check in checks:
                    row = {"phase": phase, "id": name, "check": check, "passed": True, "status": "passed", "dsl": "PRIVATE_DSL_BODY_SENTINEL"}
                    row.update({key: self.hashed("rust:" + name + ":" + check + ":" + key) for key in e.RS_HASHES})
                    row.update({"baseline_status": (400 if name in refusals else 200) if check == "compile_equal" else None,
                                "qa_status": 400 if check == "baseline_bundle_refused" or name in refusals else 200})
                    row["qa_request_sha256"] = row["baseline_request_sha256"] = row["request_sha256"]
                    row["response_sha256"] = row["qa_response_sha256"]
                    if check != "compile_equal":
                        row["baseline_response_sha256"] = None
                    elif name in refusals:
                        row["baseline_response_sha256"] = row["qa_response_sha256"]
                    if check == "rebased_files":
                        # The accepted checker records these two fields only for this check.
                        row["files_sha256"] = self.hashed("complete-file-map:" + name)
                        row["recorded_files_sha256"] = row["files_sha256"]
                        if rebased_shape == "missing":
                            del row["recorded_files_sha256"]
                        elif rebased_shape == "mismatch":
                            row["recorded_files_sha256"] = self.hashed("wrong-file-map:" + name)
                    elif rebased_shape == "wrong_check":
                        row["files_sha256"] = row["recorded_files_sha256"] = self.hashed("unexpected-file-map")
                    if rebased_shape == "extra":
                        row["unapproved_sha256"] = self.hashed("unknown-digest-field")
                    row["request_sha256"], row["response_sha256"] = row["qa_request_sha256"], row["qa_response_sha256"]
                    rs.append(row)
        if bad_status:
            rs[0]["qa_status"] = True
        reports = {"python-baseline": {"passed": True, "status": "passed", "rows": py}, "rust-baseline": {"passed": True, "status": "passed", "rows": rs}}
        specs = {name: [name + "/report.json"] for name in e.STEPS}
        specs["target"].append("target/responses")
        specs["rt-diagnostics"].append("rt-diagnostics/responses")
        specs["bundles"].extend(["bundles/extra.json", "bundles/products", "bundles/responses"])
        specs["ts-products"].append("ts-products/extra.json")
        steps, receipts, indexed_receipts = [], {}, []
        for index, name in enumerate(e.STEPS, 1):
            outputs, projected = {}, []
            for path in specs[name]:
                if (name, path) in e.UNCHECKED:
                    outputs[path] = self.hashed("recorded-directory:" + path)
                    projected.append({"relative_path": path, "recorded_directory_digest": outputs[path], "digest_rule": "receipt directory digest; tree not recomputed by this route author"})
                else:
                    role = "accepted output: " + path
                    outputs[path] = self.local(role, reports.get(name, {"private": "PRIVATE_REPORT_BODY_SENTINEL"}))
                    projected.append({"relative_path": path, "raw_sha256": outputs[path], "source_role": role})
            role = "accepted receipt: " + name
            receipts[name] = {"passed": True, "exit_code": 0, "outputs": outputs, "cwd": str(self.private), "argv": ["PRIVATE_COMMAND_SENTINEL"]}
            receipt_sha = self.local(role, receipts[name])
            steps.append({"step": name, "index": index, "passed": True, "exit_code": 0, "receipt_sha256": receipt_sha})
            indexed_receipts.append({"index": index, "step": name, "source_role": role, "raw_sha256": receipt_sha, "passed": True, "exit_code": 0, "outputs": projected})
        archive_sha = self.local("accepted source archive", b"PRIVATE_ARCHIVE_BODY_SENTINEL", binary=True)
        source = {"archive_sha256": archive_sha, "commit": "1" * 40, "files": {"PRIVATE_SOURCE_FILENAME_SENTINEL.py": self.hashed("source")}}
        candidate = {"binary_sha256": self.local("accepted candidate som", b"PRIVATE_BINARY_BODY_SENTINEL", binary=True),
                     "source_json_sha256": self.local("accepted candidate source.json", source),
                     "runtime_receipt_sha256": self.local("accepted candidate runtime.json", {"path": str(self.private)}),
                     "build_receipt_sha256": self.local("accepted candidate build.json", {"command": "PRIVATE_BUILD_COMMAND_SENTINEL"})}
        packaged = {}
        for i in range(6):
            name = "PRIVATE_PRODUCT_FILENAME_SENTINEL-" + str(i)
            packaged[name] = self.local("accepted packaged asset: " + name, {"raw": "PRIVATE_PACKAGED_BODY_SENTINEL"})
        manifest = {"version": "fixture.v1", "files": packaged, "backend": {"version": "fixture.v1", "files": {"PRIVATE_BACKEND_FILENAME_SENTINEL.rs": self.hashed("backend")}}}
        manifest_raw = self.local("accepted candidate asset manifest", manifest)
        health = {key: self.hashed(key) for key in e.HEALTH_HASHES}
        health.update({key: "fixture.v1" for key in e.HEALTH_VERSIONS})
        health["asset_manifest_sha256"] = e.sha(e.canonical(manifest))
        health["backend_sha256"] = e.sha(e.canonical(manifest["backend"]))
        health["rust_target"] = {"assets": {key: {"id": "fixture." + key, "sha256": self.hashed(key)} for key in sorted(e.RUST_ASSETS)}, "bundle_version": "fixture.v2", "dsl_version": "fixture.v1", "formatter_sha256": self.hashed("formatter"), "formatter_version": "fixture.v1"}
        header_sha = self.local("accepted header", {"candidate_before": {"health": health, "asset_manifest_sha256": manifest_raw, "source_tar_sha256": archive_sha}})
        summary_sha = self.local("accepted summary", {"passed": True, "blockers": [], "header_sha256": header_sha, "steps": steps})
        prereq, refs, observations = {}, [], []
        for family, target in e.FAMILIES.items():
            prereq[family] = []
            for ci in range(2):
                case = {"name": target + "-canary-" + str(ci), "steps": []}
                for si in range(5):
                    step = {"name": "compile-json" if si == 0 else "compile-cnl" if si == 1 else "check-" + str(si)}
                    for side in ["request", "response"] if si < 4 else ["response"]:
                        role = f"accepted canary {family}:{ci}:{si}:{side}"
                        body = {"raw": "PRIVATE_CANARY_BODY_SENTINEL"}
                        if side == "response" and si < 2:
                            assets = {"backend": {"id": "fixture.backend", "sha256": self.hashed("backend")}, "formatter": {"id": "fixture.formatter", "sha256": self.hashed("formatter")}, "semantic_contracts": [{"id": "fixture.semantic", "sha256": self.hashed("semantic")}]}
                            if extra_descriptor:
                                assets["backend"]["private_path"] = str(self.private)
                            body = {"bundle": {"assets": assets, "prompt": {"version": "som.prompt.v1", "raw": "PRIVATE_PROMPT_BODY_SENTINEL"}, "dsl": "PRIVATE_DSL_BODY_SENTINEL", "hashes": {key: self.hashed(key) for key in ["dsl_sha256", "prompt_sha256", "payload_sha256"]}, "target": target, "version": "som.dsl-bundle.v2"}}
                        value = self.local(role, body)
                        step[side] = {"path": str(self.roles[role]), "sha256": value}
                        refs.append({"family": family, "case_name": case["name"], "step_name": step["name"], "side": side, "source_role": role, "raw_sha256": value})
                        if side == "response" and si < 2:
                            bundle = body["bundle"]
                            leaves = [{"pointer": "/bundle/assets/backend", "id": "fixture.backend", "sha256": self.hashed("backend")}, {"pointer": "/bundle/assets/formatter", "id": "fixture.formatter", "sha256": self.hashed("formatter")}, {"pointer": "/bundle/assets/semantic_contracts/0", "id": "fixture.semantic", "sha256": self.hashed("semantic")}]
                            observations.append({"family": family, "case_name": case["name"], "step_name": step["name"], "source_role": role, "raw_response_sha256": value, "bundle_version": bundle["version"], "target": target, "prompt_version": "som.prompt.v1", "hashes": bundle["hashes"], "asset_descriptors": leaves})
                    case["steps"].append(step)
                prereq[family].append(case)
        prereq_sha = self.local("accepted prerequisite record", prereq)
        self.release = {"acceptance": {"summary_sha256": summary_sha, "header_sha256": header_sha}, "candidate": candidate, "prerequisites": {"file_sha256": prereq_sha}}
        release_pin = self.public(e.RELEASE, self.release)
        self.release_pin = release_pin
        self.bind("accepted summary", "public release", "/acceptance/summary_sha256")
        self.bind("accepted header", "public release", "/acceptance/header_sha256")
        self.bind("accepted header", "accepted summary", "/header_sha256")
        for pos, name in enumerate(e.STEPS):
            role = "accepted receipt: " + name
            self.bind(role, "accepted summary", f"/steps/{pos}/receipt_sha256")
            for path in receipts[name]["outputs"]:
                if (name, path) not in e.UNCHECKED:
                    self.bind("accepted output: " + path, role, "/outputs/" + path.replace("/", "~1"))
        for name, key in [("som", "binary_sha256"), ("source.json", "source_json_sha256"), ("runtime.json", "runtime_receipt_sha256"), ("build.json", "build_receipt_sha256")]:
            self.bind("accepted candidate " + name, "public release", "/candidate/" + key)
        self.bind("accepted source archive", "accepted candidate source.json", "/archive_sha256")
        self.bind("accepted source archive", "accepted header", "/candidate_before/source_tar_sha256")
        self.bind("accepted candidate asset manifest", "accepted header", "/candidate_before/asset_manifest_sha256")
        self.bind("accepted candidate asset manifest", "accepted header", "/candidate_before/health/asset_manifest_sha256", "canonical JSON sha256")
        for name in packaged:
            self.bind("accepted packaged asset: " + name, "accepted candidate asset manifest", "/files/" + name)
        self.bind("accepted prerequisite record", "public release", "/prerequisites/file_sha256")
        for family in e.FAMILIES:
            for ci, case in enumerate(prereq[family]):
                for si, step in enumerate(case["steps"]):
                    for side in ["request", "response"]:
                        if side in step:
                            self.bind(f"accepted canary {family}:{ci}:{si}:{side}", "accepted prerequisite record", f"/{family}/{ci}/steps/{si}/{side}/sha256")
        def project(rows, identities):
            return [{"row_index": i, **{key: row[key] for key in identities}, "passed": row["passed"], "status": row["status"], **{key: value for key, value in row.items() if key.endswith("_sha256") or key.endswith("_status")}} for i, row in enumerate(rows)]
        self.route = {"version": "som.ts065-baseline-route.private.v1", "classification": e.PRIVATE, "distribution_approval": "none", "policy_approval": "none", "public_repository": "faberline/som", "public_source_revision": e.PUBLIC_REVISION, "counts": dict(e.COUNTS), "public_sources": self.public_sources, "public_release_anchor": release_pin, "public_case_sources": [p1_pin, p2_pin], "local_accepted_sources": self.sources, "accepted_step_receipts": indexed_receipts, "public127_reconstruction_bindings": [{"id": name, "index": i, "reconstruction": {"path": e.FIXTURES, "prompt_pointer": f"/cases/{i}/expected_prompt", "cnl_pointer": f"/cases/{i}/cnl"}} for i, name in enumerate(public_ids)], "python_baseline": {"rows": project(py, ["source", "id", "variant"])}, "rust_baseline": {"rows": project(rs, ["phase", "id", "check"])}, "old_descriptor_health": {"values": health}, "candidate_manifest_metadata": {"raw_file_sha256": manifest_raw, "canonical_json_sha256": e.sha(e.canonical(manifest)), "backend_canonical_json_sha256": e.sha(e.canonical(manifest["backend"])), "backend": manifest["backend"], "packaged_file_hashes": packaged}, "candidate_source_manifest": {"file_hashes": source["files"]}, "accepted_canary_source_references": refs, "accepted_canary_descriptor_observations": observations, "private_notes": "PRIVATE_ROUTE_BODY_SENTINEL"}
        self.route_path.write_text(json.dumps(self.route, indent=2) + "\n")

    def export(self, route=None):
        if route is not None:
            self.route_path.write_text(json.dumps(route, indent=2) + "\n")
        with patch.object(e, "REVIEWED_ROUTE_SHA256", hashlib.sha256(self.route_path.read_bytes()).hexdigest()), patch.object(e, "PUBLIC_RELEASE_SHA256", self.release_pin["raw_sha256"]):
            return e.build_export(self.route_path, self.repo)

    def test_complete_export_retains_authorities_statuses_and_no_bodies(self):
        result = self.export()
        self.assertEqual(len(result["baselines"]["python"]["rows"]), 1342)
        self.assertEqual(len(result["baselines"]["rust"]["rows"]), 2093)
        self.assertEqual(result["verified_local_source_pin_count"], 97)
        self.assertEqual(len(result["accepted_receipts"]), 13)
        self.assertEqual(len(result["public_source_files"]), 36)
        rust = result["baselines"]["rust"]["rows"]
        self.assertNotEqual(rust[0]["qa_response_sha256"], rust[0]["baseline_response_sha256"])
        self.assertEqual(rust[0]["response_sha256"], rust[0]["qa_response_sha256"])
        self.assertEqual(sum(row["qa_status"] == 400 for row in rust), 699)
        self.assertEqual(sum(row["baseline_status"] is None for row in rust), 1394)
        self.assertEqual(sum(row["baseline_response_sha256"] is None for row in rust), 1394)
        self.assertTrue(all(row["qa_request_sha256"] == row["baseline_request_sha256"] == row["request_sha256"] for row in rust))
        self.assertEqual(sum(row["qa_response_sha256"] == row["baseline_response_sha256"] for row in rust), 2)
        rebased = [row for row in rust if row["check"] == "rebased_files"]
        self.assertEqual(len(rebased), 697)
        self.assertTrue(all(row["files_sha256"] == row["recorded_files_sha256"] for row in rebased))
        self.assertTrue(all("files_sha256" not in row and "recorded_files_sha256" not in row for row in rust if row["check"] != "rebased_files"))
        unchecked = [row for receipt in result["accepted_receipts"] for row in receipt["outputs"] if row["verification"] == "unchecked"]
        self.assertEqual(len(unchecked), 4)
        self.assertTrue(all(row["kind"] == "directory" for row in unchecked))
        self.assertFalse(result["product_acceptance_passed"])
        self.assertEqual(result["verification_scope"]["canary_payload_hash_recomputation"], "unrun")
        raw = json.dumps(result)
        for sentinel in ["PRIVATE_", str(self.private), "private_notes", "source_role", "relative_path"]:
            self.assertNotIn(sentinel, raw)
        self.assertNotEqual(result["accepted_descriptors"]["candidate"]["manifest_raw_file_sha256"], result["accepted_descriptors"]["candidate"]["manifest_canonical_json_sha256"])
        semantic = [leaf for row in result["accepted_canary_observations"] for leaf in row["asset_descriptors"] if "/semantic_contracts/" in leaf["pointer"]]
        self.assertEqual(len(semantic), 12)

    def test_exact_raw_route_pin_rejects_whitespace_change(self):
        original = self.route_path.read_bytes()
        self.route_path.write_bytes(original + b" ")
        with patch.object(e, "REVIEWED_ROUTE_SHA256", hashlib.sha256(original).hexdigest()):
            with self.assertRaisesRegex(e.Refusal, "raw pin"):
                e.build_export(self.route_path, self.repo)

    def test_changed_local_or_public_file_is_refused(self):
        for path in [self.roles["accepted candidate som"], self.repo / e.RELEASE]:
            before = path.read_bytes()
            path.write_bytes(before + b" ")
            with self.assertRaises(e.Refusal):
                self.export()
            path.write_bytes(before)

    def test_public_release_anchor_has_its_own_fixed_raw_pin(self):
        with patch.object(e, "REVIEWED_ROUTE_SHA256", hashlib.sha256(self.route_path.read_bytes()).hexdigest()), patch.object(e, "PUBLIC_RELEASE_SHA256", "0" * 64):
            with self.assertRaisesRegex(e.Refusal, "release raw pin"):
                e.build_export(self.route_path, self.repo)

    def test_incomplete_source_or_receipt_index_is_refused(self):
        for key in ["local_accepted_sources", "accepted_step_receipts", "public_sources"]:
            route = copy.deepcopy(self.route)
            route[key].pop()
            with self.assertRaises(e.Refusal):
                self.export(route)

    def test_canonical_manifest_binding_cannot_use_raw_hash(self):
        route = copy.deepcopy(self.route)
        row = next(row for row in route["local_accepted_sources"] if "accepted candidate asset manifest" in row["roles"])
        binding = next(binding for binding in row["accepted_bindings"] if binding["rule"] == "canonical JSON sha256")
        binding["rule"] = "raw sha256"
        with self.assertRaisesRegex(e.Refusal, "binding"):
            self.export(route)

    def test_row_omission_duplication_identity_status_and_hash_swap_are_refused(self):
        for mode in ["omit", "duplicate", "identity", "status", "swap"]:
            route = copy.deepcopy(self.route)
            rows = route["rust_baseline"]["rows"]
            if mode == "omit": rows.pop()
            if mode == "duplicate": rows[-1] = copy.deepcopy(rows[0])
            if mode == "identity": rows[0]["id"] = "renamed-case"
            if mode == "status": rows[0]["qa_status"] = 400
            if mode == "swap": rows[0]["qa_response_sha256"] = rows[0]["baseline_response_sha256"]
            with self.assertRaises(e.Refusal):
                self.export(route)

    def test_unknown_binding_rule_and_unchecked_directory_escalation_are_refused(self):
        route = copy.deepcopy(self.route)
        route["local_accepted_sources"][0]["accepted_bindings"][0]["rule"] = "unchecked"
        with self.assertRaises(e.Refusal):
            self.export(route)
        route = copy.deepcopy(self.route)
        directory = next(row for row in route["accepted_step_receipts"][0]["outputs"] if "recorded_directory_digest" in row)
        directory["digest_rule"] = "verified raw sha256"
        with self.assertRaises(e.Refusal):
            self.export(route)

    def test_boolean_http_status_is_refused_from_an_anchored_source(self):
        self.fixture(bad_status=True)
        with self.assertRaisesRegex(e.Refusal, "HTTP status"):
            self.export()

    def test_rebased_hash_schema_and_complete_map_equality_are_required(self):
        for shape in ["missing", "wrong_check", "extra", "mismatch"]:
            with self.subTest(shape=shape):
                self.fixture(rebased_shape=shape)
                with self.assertRaises(e.Refusal):
                    self.export()

    def test_descriptor_extra_field_is_refused_from_an_anchored_source(self):
        self.fixture(extra_descriptor=True)
        with self.assertRaisesRegex(e.Refusal, "descriptor fields"):
            self.export()

    def test_private_path_in_version_field_is_refused(self):
        with self.assertRaises(e.Refusal):
            e.version(str(self.private))
        with self.assertRaises(e.Refusal):
            e.descriptor({"id": str(self.private), "sha256": "1" * 64})

    def test_symlink_sources_and_public_path_escape_are_refused(self):
        source = self.roles["accepted candidate som"]
        saved = source.with_suffix(".saved")
        source.rename(saved)
        source.symlink_to(saved)
        with self.assertRaisesRegex(e.Refusal, "symlink"):
            self.export()
        source.unlink()
        saved.rename(source)
        route = copy.deepcopy(self.route)
        route["public_sources"][0]["path"] = "../private.json"
        with self.assertRaises(e.Refusal):
            self.export(route)

    def test_duplicate_keys_nonfinite_and_malformed_json_are_refused(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":1,"\\u0078":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":', b'{"x":1} trailing', b'\xff']:
            with self.assertRaises((e.Refusal, ValueError)):
                e.parse(raw)

    def test_cli_does_not_overwrite_existing_output_or_print_private_values(self):
        self.output.write_bytes(b"keep")
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            code = e.main(["--route", str(self.route_path), "--repo", str(self.repo), "--output", str(self.output)])
        self.assertEqual(code, 1)
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assertNotIn(str(self.private), errors.getvalue())


if __name__ == "__main__":
    unittest.main()
