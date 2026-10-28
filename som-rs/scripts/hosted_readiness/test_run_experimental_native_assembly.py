"""Full-scope synthetic assembly fixtures. Run only on GitHub. No real server."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_experimental_native_assembly as a


def bundle(prompt, target, dsl):
    value = {"version": "som.dsl-bundle.v2" if target == "rust" else "som.dsl-bundle.v1",
             "prompt": prompt, "dsl": dsl, "assets": {"backend": {"id": "synthetic", "sha256": "a" * 64}}}
    if target == "rust":
        value["target"] = "rust"
    value["hashes"] = {"prompt_sha256": a.value_sha(prompt), "dsl_sha256": a.value_sha(dsl), "payload_sha256": a.value_sha(value)}
    return value


def assembled(value):
    target = value.get("target", "python")
    content = target + ":" + value["prompt"]["case"] + ":" + value["dsl"]["mode"] + "\n"
    return {"src/main.rs" if target == "rust" else "app.py": content}


def reply(files):
    return a.driver.compact({"status": "ok", "files": files})


def golden(identity, index, prompt, files, variant="standard"):
    return {"id": identity, "index": index, "prompt_sha256": a.value_sha(prompt), "variant": variant,
            "files_map_sha256": a.value_sha(files), "raw_assemble_response_sha256": a.sha(reply(files)), "file_count": len(files),
            "accepted_per_file_hashes": [{"path_sha256": a.sha(path.encode()), "content_sha256": a.sha(text.encode())} for path, text in sorted(files.items())]}


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.out = self.root / "results"
        self.out.mkdir()
        self.ext = a.AssemblyExperiment(self.repo, self.root / "python", self.root / "rust", self.root / "proof", "b" * 40)

    def fixture(self):
        goldens = {"cohorts": {name: [] for name in ["python_public", "python_legacy", "rust_phase1", "rust_phase2"]}}
        document = {"baselines": {"python": {"rows": []}, "rust": {"rows": []}}}
        jobs, records, current = [], [], {}
        checked = sorted(a.CHECKED_IDS)
        for cohort, source, count in [("python_public", "public", 127), ("python_legacy", "legacy", 544)]:
            for index in range(count):
                identity = checked[index] if source == "legacy" and index < 3 else source + "-" + str(index)
                mode = "checked" if source == "legacy" and index < 3 else "standard"
                prompt = {"version": "som.prompt.v7", "case": identity}
                default = bundle(prompt, "python", {"mode": "standard"})
                expected = assembled(bundle(prompt, "python", {"mode": mode}))
                g = golden(identity, index, prompt, expected, mode)
                goldens["cohorts"][cohort].append(g)
                if mode == "checked":
                    self.ext.checked[identity] = {"dsl": {"mode": "checked"}, "dsl_sha256": a.value_sha({"mode": "checked"}),
                                                   "prompt_sha256": a.value_sha(prompt), "source": "synthetic fixed checked fixture"}
                for variant in ["none", "python"]:
                    ident = {"source": source, "id": identity, "variant": variant}
                    raw = a.driver.compact({"status": "ok", "bundle": default})
                    request = a.driver.compact({"bundle": default})
                    accepted = {"source": source, "id": identity, "variant": variant, "qa_compile_status": 200,
                                "qa_assemble_status": 200, "baseline_assemble_status": 200,
                                "qa_assemble_request_sha256": a.sha(request), "baseline_assemble_request_sha256": a.sha(request),
                                "qa_assemble_response_sha256": a.sha(reply(assembled(default))),
                                "baseline_assemble_response_sha256": a.sha(reply(assembled(default)))}
                    document["baselines"]["python"]["rows"].append(accepted)
                    jobs.append({"target": "python", "identity": ident, "expected_http_status": 200})
                    records.append({"state": "passed", "current_response_sha256": a.sha(raw), "http_status": 200})
                    current[len(jobs) - 1] = raw
        refusals = sorted(a.driver.mapper.REFUSALS)
        for cohort, phase, count in [("rust_phase1", "phase1", 115), ("rust_phase2", "phase2", 584)]:
            for index in range(count):
                refused = phase == "phase2" and index >= 582
                identity = refusals[index - 582] if refused else phase + "-" + str(index)
                prompt = {"version": "som.prompt.v5", "case": identity}
                current_bundle = bundle(prompt, "rust", {"mode": "standard"})
                files = assembled(current_bundle)
                g = golden(identity, index, prompt, files)
                g["assembly_variant"] = "checked" if phase == "phase2" and index < 3 else "standard"
                g["standard"] = False if index % 2 else True
                if refused:
                    g.update(expected={"result": "refusal"}, files_map_sha256=None)
                else:
                    document["baselines"]["rust"]["rows"].append({"row_index": len(document["baselines"]["rust"]["rows"]), "phase": phase, "id": identity,
                        "check": "rebased_files", "files_sha256": a.value_sha(files), "recorded_files_sha256": a.value_sha(files),
                        "passed": True, "status": "passed", "qa_status": 200})
                goldens["cohorts"][cohort].append(g)
                jobs.append({"target": "rust", "identity": {"phase": phase, "id": identity}, "expected_http_status": 400 if refused else 200})
                raw = a.driver.compact({"status": "error", "code": "INVALID_VALUE"}) if refused else a.driver.compact({"status": "ok", "bundle": current_bundle})
                records.append({"state": "passed", "current_response_sha256": a.sha(raw), "http_status": 400 if refused else 200})
                current[len(jobs) - 1] = raw
        self.ext.plan_jobs(goldens, document, jobs)
        self.ext.report["preparation_passed"] = True
        for index, raw in current.items():
            directory = self.out / "rows" / f"{index:04d}"
            directory.mkdir(parents=True)
            (directory / "response.raw").write_bytes(raw)
        def verify(target, identity, status, raw):
            self.assertEqual(status, 200)
            return SimpleNamespace(original_bundle=a.driver.experiment.parse(raw)["bundle"])
        checker = SimpleNamespace(verify_response=verify)
        service = SimpleNamespace(process=SimpleNamespace(pid=4242), stamp={"start_ticks": 123})
        return goldens, document, jobs, records, checker, service

    def call(self, service, raw):
        value = a.driver.experiment.parse(raw)["bundle"]
        return 200, reply(assembled(value)), {"pid": 4242, "start_ticks": 123}

    def run_fixture(self):
        fixture = self.fixture()
        with patch.object(a, "post_assemble", side_effect=self.call):
            self.ext.run(fixture[4], fixture[5], fixture[2], fixture[3], self.out)
        return fixture

    def finalize(self, cleanup=True, stability=True):
        return self.ext.finalize({"experiment_complete": True, "identity_stability_passed": stability,
                                  "cleanup": {"cleanup_passed": cleanup}}, self.out)

    def test_all2041_compile_rows_then2045_assemblies_and_all1368_named_products(self):
        self.run_fixture()
        summary = self.finalize()
        self.assertTrue(summary["assembly_experiment_complete"])
        self.assertEqual(summary["actual_observations"], a.OBSERVATIONS)
        self.assertEqual(summary["actual_products"], {"python": 671, "rust": 697})
        self.assertFalse(self.ext.report["product_acceptance_passed"])
        self.assertFalse(self.ext.report["product_runtime_ready"])
        self.assertEqual(len(self.ext.report["compile_refusals_without_assembly"]), 2)
        py = self.ext.report["products"]["python"]
        checked = [row for row in py if row["assembly_variant"] == "checked"]
        self.assertEqual(len(checked), 3)
        self.assertTrue(all(len(row["compile_variants"]) == 2 for row in py))
        self.assertTrue(all(row["selected_compile_variant"] == "none" for row in py))
        rust = self.ext.report["products"]["rust"]
        self.assertTrue(all(row["rust_assembly_mode"] == "normal-target-plan" for row in rust))
        self.assertEqual(sum(row["python_assembly_variant_provenance"] == "checked" for row in rust), 3)
        self.assertTrue(all(not Path(row["product_dir"]).is_absolute() for row in py + rust))
        self.assertEqual(self.ext.report["remaining"]["old_bundle_rejection_and_rebase"], "unrun")

    def test_no_assembly_when_one_compile_proof_failed(self):
        fixture = self.fixture()
        fixture[3][1000]["state"] = "failed"
        with patch.object(a, "post_assemble") as transport, self.assertRaisesRegex(a.Refusal, "every exact compile"):
            self.ext.run(fixture[4], fixture[5], fixture[2], fixture[3], self.out)
        transport.assert_not_called()

    def test_missing_named_product_or_wrong_current_refusal_cannot_shrink_scope(self):
        goldens, document, jobs, *_ = self.fixture()
        for mutation in ["omit", "status"]:
            candidate = deepcopy(jobs)
            if mutation == "omit": candidate.pop(0)
            else: candidate[-1]["expected_http_status"] = 200
            ext = a.AssemblyExperiment(self.repo, self.root / "p", self.root / "r", self.root / "c", "b" * 40)
            with self.subTest(mutation=mutation), self.assertRaises((a.Refusal, KeyError)):
                ext.plan_jobs(goldens, document, candidate)

    def test_recorded_rust_map_must_equal_rebased_and_frozen_map(self):
        goldens, document, jobs, *_ = self.fixture()
        document["baselines"]["rust"]["rows"][0]["recorded_files_sha256"] = "0" * 64
        ext = a.AssemblyExperiment(self.repo, self.root / "p", self.root / "r", self.root / "c", "b" * 40)
        with self.assertRaisesRegex(a.Refusal, "map authorities disagree"):
            ext.plan_jobs(goldens, document, jobs)

    def test_checked_adapter_keeps_current_assets_and_rehashes_complete_payload(self):
        prompt = {"version": "som.prompt.v7", "case": "checked"}
        original = bundle(prompt, "python", {"mode": "standard"})
        checked = {"mode": "checked"}
        result = a.checked_bundle(original, checked, a.value_sha(prompt), a.value_sha(checked))
        self.assertEqual(result["assets"], original["assets"])
        self.assertEqual(result["hashes"]["payload_sha256"], a.value_sha({k: v for k, v in result.items() if k != "hashes"}))
        self.assertEqual(original["dsl"]["mode"], "standard")
        with self.assertRaisesRegex(a.Refusal, "Python bundles only"):
            a.checked_bundle(bundle(prompt, "rust", {"mode": "standard"}), checked, a.value_sha(prompt), a.value_sha(checked))

    def test_same_file_map_but_changed_raw_python_reply_fails(self):
        fixture = self.fixture()
        slot = self.ext.plan[0]
        def changed(service, raw):
            status, response, ownership = self.call(service, raw)
            return status, response + b"\n", ownership
        with patch.object(a, "post_assemble", side_effect=changed):
            row = self.ext.observe(slot, 0, fixture[4], fixture[5], fixture[3][0], self.out)
        self.assertEqual(row["state"], "failed")
        self.assertIn("exact TS065 QA", row["reason"])

    def test_accepted_per_file_hash_set_is_complete(self):
        files = {"x.py": "x\n", "y.py": "y\n"}
        g = golden("fixture", 0, {}, files)
        g["accepted_per_file_hashes"].pop()
        with self.assertRaisesRegex(a.Refusal, "complete accepted per-file"):
            a.verify_product_files(files, g, "python")

    def test_unsafe_file_paths_or_conflicts_fail_before_product_write(self):
        for files in [{"../escape": "x"}, {"/absolute": "x"}, {"a": "x", "a/b": "y"}, {"a\\b": "x"}, {"a:b": "x"}]:
            with self.subTest(files=files), self.assertRaises(a.Refusal):
                a.files_from_response(200, reply(files))
        self.assertFalse((self.root / "escape").exists())

    def test_saved_product_mutation_or_cleanup_failure_prevents_complete(self):
        self.run_fixture()
        first = self.ext.report["observations"][0]
        file = self.out / first["product_dir"] / first["files"][0]["path"]
        file.write_bytes(file.read_bytes() + b"changed")
        summary = self.finalize()
        self.assertFalse(summary["assembly_experiment_complete"])
        self.assertIn("saved complete assembly product changed", self.ext.report["finalization_reason"])

    def test_owned_cleanup_failure_prevents_complete(self):
        self.run_fixture()
        summary = self.finalize(cleanup=False)
        self.assertFalse(summary["assembly_experiment_complete"])

    def test_current_compile_response_mutation_is_refused_before_transport(self):
        fixture = self.fixture()
        file = self.out / "rows" / "0000" / "response.raw"
        file.write_bytes(file.read_bytes() + b" ")
        with patch.object(a, "post_assemble") as transport:
            row = self.ext.observe(self.ext.plan[0], 0, fixture[4], fixture[5], fixture[3][0], self.out)
        self.assertEqual(row["state"], "failed")
        self.assertIn("compile proof receipt", row["reason"])
        transport.assert_not_called()

    def test_checked_proof_must_name_same_candidate_and_fixed_canonical_dsl(self):
        supplement_rows, legacy, pins = [], [], {}
        for index, identity in enumerate(a.replay.CHECKED_DSL_SHA256):
            dsl = {"synthetic": identity}
            digest = a.value_sha(dsl)
            pins[identity] = digest
            supplement_rows.append({"id": identity, "legacy_index": index, "variant": "checked",
                                    "prompt_sha256": "a" * 64, "checked_dsl_input": dsl, "dsl_sha256": digest})
            legacy.append({"id": identity, "index": index, "prompt_sha256": "a" * 64})
        supplement = a.driver.compact({"checked_dsl_inputs": supplement_rows})
        dsl = {"synthetic": "public quality checked plan"}
        dsl_raw = a.driver.compact(dsl)
        dsl_pin = a.value_sha(dsl)
        recipe = b'{"synthetic":"fixed recipe"}'
        recipe_pin = a.sha(recipe)
        sources = SimpleNamespace(pins={}, anchors={}, members=lambda: [])
        checker = SimpleNamespace(repo=SimpleNamespace(read=lambda path: supplement if path == a.replay.SUPPLEMENT_FILE else recipe))
        modules = sorted("som_core" if path.endswith("/__init__.py") else path[len(a.proof.MODULE_PREFIX):-3].replace("/", ".") for path in a.proof.MODULE_FILES)
        manifest = {"version": "som.checked-reference-proof.v1", "tested_sha": "b" * 40, "cohort": a.proof.COHORT,
                    "case_count": 1, "recipe": {"path": a.proof.RECIPE_FILE, "raw_sha256": recipe_pin},
                    "public_source_revision": a.replay.PUBLIC_REVISION, "module_and_data_pins": {}, "public_anchor_pins": {},
                    "recursive_blocks_json_members": [], "source_checks_before_and_after": "passed", "semantic_validation": "passed",
                    "canonical_dsl_equal": True, "product_acceptance_passed": False, "prompt_sha256": a.proof.EXPECTED_PROMPT_SHA256,
                    "accepted_canonical_dsl_sha256": dsl_pin, "generated_canonical_dsl_sha256": dsl_pin,
                    "loaded_core_modules": modules, "checked_dsl": {"path": "checked.dsl.json", "raw_sha256": a.sha(dsl_raw), "canonical_sha256": dsl_pin}}
        directory = self.root / "proof"
        directory.mkdir()
        for mutation in ["candidate", "dsl"]:
            changed = deepcopy(manifest)
            raw = dsl_raw
            if mutation == "candidate":
                changed["tested_sha"] = "c" * 40
            else:
                raw = a.driver.compact({"synthetic": "changed plan"})
                changed["checked_dsl"]["raw_sha256"] = a.sha(raw)
            (directory / "manifest.json").write_bytes(a.driver.compact(changed))
            (directory / "checked.dsl.json").write_bytes(raw)
            ext = a.AssemblyExperiment(self.repo, self.root / "python", self.root / "rust", directory, "b" * 40)
            with self.subTest(mutation=mutation), patch.object(a.replay, "CHECKED_DSL_SHA256", pins), \
                 patch.object(a.replay, "SUPPLEMENT_SHA256", a.sha(supplement)), patch.object(a.proof, "ProofSources", return_value=sources), \
                 patch.object(a.proof, "RECIPE_SHA256", recipe_pin), patch.object(a.proof, "EXPECTED_DSL_SHA256", dsl_pin), self.assertRaises(a.Refusal):
                ext.load_checked(checker, {"cohorts": {"python_legacy": legacy}})

    def test_driver_runs_extension_before_owned_cleanup_and_finalizes_after(self):
        import test_run_experimental_native_compile as compile_fixtures
        fixture = compile_fixtures.DriverTests("test_signal_cancellation_closes_owned_service_and_keeps_manifest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        stages = []
        class Extension:
            def prepare(self, checker, document, jobs):
                stages.append("prepare")
            def run(self, checker, service, jobs, records, out):
                self.service = service
                self.count = len(records)
                self.cleanup_before_assembly = service.close.call_count
                stages.append("assemble")
                return {"assembly_experiment_complete": False}
            def finalize(self, report, out):
                self.cleanup_after_assembly = self.service.close.call_count
                self.cleanup_receipt = report["cleanup"]
                stages.append("finalize")
                return {"assembly_experiment_complete": False, "product_acceptance_passed": False}
        extension = Extension()
        original = a.driver.run
        def run(*args, **kwargs):
            return original(*args, extension=extension, **kwargs)
        with patch.object(a.driver, "run", side_effect=run):
            report = fixture.lifecycle_fixture()
        self.assertEqual(stages, ["prepare", "assemble", "finalize"])
        self.assertEqual(extension.count, 1)
        self.assertEqual(extension.cleanup_before_assembly, 0)
        self.assertEqual(extension.cleanup_after_assembly, 1)
        self.assertTrue(extension.cleanup_receipt["cleanup_passed"])
        self.assertIn("unsealed assembly_extension", report["remaining"]["assembly"])


if __name__ == "__main__":
    unittest.main()
