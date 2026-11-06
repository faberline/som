"""Synthetic checked-reference proof fixtures. Run only on GitHub."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import prove_checked_reference as p


class CheckedReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.out = self.root / "proof"
        self.calls = []
        self.change_during_generation = None
        self.validation_error = False
        self.validation_mutation = False
        self.prompt = {"version": "som.prompt.v7", "public_fixture": "kept"}
        self.dsl = {"version": "som.dsl.v3", "files": [], "flow": {"steps": [], "public_fixture": "kept"}}
        self.prompt_sha, self.dsl_sha = p.value_sha(self.prompt), p.value_sha(self.dsl)
        self.fixture()
        for target, name, value in ((p, "RECIPE_SHA256", self.recipe_sha),
                                    (p, "EXPECTED_PROMPT_SHA256", self.prompt_sha), (p, "EXPECTED_DSL_SHA256", self.dsl_sha),
                                    (p.replay, "GOLDENS_SHA256", self.golden_sha), (p.replay, "RUNTIME_PINS", self.module_pins)):
            trusted = patch.object(target, name, value)
            trusted.start()
            self.addCleanup(trusted.stop)

    def write(self, path, raw):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
        return p.sha(raw)

    def write_json(self, path, value):
        return self.write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())

    def fixture(self):
        self.module_pins = {}
        for path in p.MODULE_FILES:
            raw = b"# Synthetic closed source module\n"
            if path.endswith("/flow_compact.py"):
                raw = b"from . import business, composed, related, v1, flow_codec, quality, compact_names, flow\n"
            self.module_pins[path] = self.write(path, raw)
        data_pins = {}
        for path in p.DATA_FILES:
            raw = b"{}\n"
            if path == p.INPUT_FILE:
                raw = (json.dumps({"id": "public-dev-0", "prompt": self.prompt}) + "\n").encode()
            elif path.endswith("/blocks.json"):
                raw = b"[]\n"
            data_pins[path] = self.write(path, raw)
        anchors = []
        for path, pointer in p.ANCHORS.items():
            document = {}
            current = document
            parts = pointer.split("/")[1:]
            for key in parts[:-1]:
                current[key] = {}
                current = current[key]
            current[parts[-1]] = p.replay.LEGACY_RESULT_SHA256
            anchors.append({"path": path, "revision": p.replay.PUBLIC_REVISION, "raw_sha256": self.write_json(path, document),
                            "accepted_result_pointer": pointer, "accepted_result_raw_sha256": p.replay.LEGACY_RESULT_SHA256})
        row = {key: p.COHORT[key] for key in ("id", "index", "family", "group", "partition", "behavior_expected")}
        row.update(variant="checked", prompt_sha256=self.prompt_sha, files_map_sha256="c" * 64,
                   checked_dsl_provenance="public_reference_recipe_unrun")
        rows = [{} for _ in range(532)]
        rows[531] = row
        self.golden = {"version": "som.public-golden-hashes.v1", "public_repository": "faberline/som",
                       "public_source_revision": p.replay.PUBLIC_REVISION, "product_acceptance_passed": False,
                       "cohorts": {"python_legacy": rows}}
        self.golden_sha = self.write_json(p.GOLDENS_FILE, self.golden)
        as_rows = lambda pins: [{"path": path, "revision": p.replay.PUBLIC_REVISION, "raw_sha256": digest}
                                for path, digest in sorted(pins.items())]
        self.recipe = {"version": "som.public-checked-dsl-recipe.v1", "product_acceptance_passed": False, "case_count": 1,
                       "public_repository": "faberline/som", "public_source_revision": p.replay.PUBLIC_REVISION,
                       "cohort": copy.deepcopy(p.COHORT), "public_runtime_module_pins": as_rows(self.module_pins),
                       "public_data_pins": as_rows(data_pins),
                       "input": {"path": p.INPUT_FILE, "line_1based": 1, "pointer": "/prompt",
                                 "raw_sha256": data_pins[p.INPUT_FILE], "prompt_sha256": self.prompt_sha},
                       "recipe": {"call": 'som_core.flow_compact.reference(prompt, blocks, "checked")',
                                  "function_path": "som-core/src/som_core/flow_compact.py", "function": "reference",
                                  "blocks_loader": "som_core.v1.load_blocks", "blocks_directory": p.BLOCKS_DIRECTORY,
                                  "exact_recursive_json_members": ["blocks.json"], "canonical_dsl_sha256": self.dsl_sha,
                                  "validation_call": "som_core.flow.validate_plan(prompt, dsl, som_core.flow_compact.metadata())",
                                  "new_module_dependencies": []},
                       "accepted_hash_chain": {"legacy_result": {"raw_sha256": p.replay.LEGACY_RESULT_SHA256},
                                               "legacy_inventory": {"checked_prevalidation_dsl_sha256": self.dsl_sha},
                                               "accepted_variant_bundle": {"canonical_dsl_sha256": self.dsl_sha,
                                                                           "prompt_sha256": self.prompt_sha},
                                               "public_anchors": anchors,
                                               "sealed_public_goldens": {"path": p.GOLDENS_FILE, "raw_sha256": self.golden_sha,
                                                                         "case_pointer": "/cohorts/python_legacy/531",
                                                                         "files_map_sha256": "c" * 64}}}
        self.recipe_sha = self.write_json(p.RECIPE_FILE, self.recipe)

    @contextmanager
    def fake_runtime(self, sources):
        def load_blocks(path):
            self.assertEqual(path, self.repo / p.BLOCKS_DIRECTORY)
            self.calls.append("load_blocks")
            return {"public_blocks": True}
        def reference(prompt, blocks, variant):
            self.assertEqual(p.value_sha(prompt), self.prompt_sha)
            self.assertEqual(blocks, {"public_blocks": True})
            self.assertEqual(variant, "checked")
            self.calls.append("reference_checked")
            if self.change_during_generation:
                self.change_during_generation()
            return copy.deepcopy(self.dsl)
        def metadata():
            self.calls.append("metadata")
            return {"public_metadata": True}
        def validate_plan(prompt, dsl, catalog):
            self.assertEqual(catalog, {"public_metadata": True})
            self.calls.append("validate_plan")
            if self.validation_error:
                raise ValueError("synthetic semantic refusal")
            if self.validation_mutation:
                dsl["flow"]["public_fixture"] = False
            return dsl
        names = sorted("som_core" if path.endswith("/__init__.py") else path[len(p.MODULE_PREFIX):-3].replace("/", ".")
                       for path in p.MODULE_FILES)
        yield SimpleNamespace(reference=reference, metadata=metadata), SimpleNamespace(validate_plan=validate_plan), SimpleNamespace(load_blocks=load_blocks), lambda: names

    def prove(self):
        with patch.object(p, "checked_runtime", self.fake_runtime):
            return p.prove(self.repo, self.out, "a" * 40)

    def test_one_case_exact_hash_and_semantic_proof_keeps_product_gates_unrun(self):
        report = self.prove()
        self.assertEqual(self.calls, ["load_blocks", "reference_checked", "metadata", "validate_plan"])
        self.assertEqual(report["case_count"], 1)
        self.assertTrue(report["canonical_dsl_equal"])
        self.assertEqual(report["generated_canonical_dsl_sha256"], self.dsl_sha)
        self.assertFalse(report["product_acceptance_passed"])
        self.assertFalse(report["full_input_set_ready"])
        self.assertEqual(report["product_suites"], "unrun")
        self.assertEqual(report["historical_case_checked_dsl_provenance"], "public_reference_recipe_unrun")
        self.assertEqual(len(report["loaded_core_modules"]), 10)
        raw = (self.out / "checked.dsl.json").read_bytes()
        self.assertEqual(p.sha(raw), report["checked_dsl"]["raw_sha256"])
        self.assertEqual(p.value_sha(json.loads(raw)), self.dsl_sha)
        manifest = json.loads((self.out / "manifest.json").read_bytes())
        self.assertNotIn("files", manifest)
        self.assertNotIn("flow", manifest)
        self.assertEqual(manifest["cohort"], p.COHORT)

    def test_changed_data_or_module_refuses_before_runtime(self):
        for path in (p.INPUT_FILE, "som-core/src/som_core/flow.py"):
            file = self.repo / path
            saved = file.read_bytes()
            file.write_bytes(saved + b" ")
            with self.subTest(path=path), self.assertRaisesRegex(p.Refusal, "file digest differs"):
                self.prove()
            file.write_bytes(saved)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.calls, [])

    def test_changed_canonical_dsl_or_scalar_type_cannot_pass(self):
        self.dsl["flow"]["public_fixture"] = False
        with self.assertRaisesRegex(p.Refusal, "accepted canonical digest"):
            self.prove()
        self.assertFalse(self.out.exists())

    def test_semantic_refusal_or_value_mutation_blocks_proof(self):
        self.validation_error = True
        with self.assertRaisesRegex(ValueError, "semantic refusal"):
            self.prove()
        self.validation_error = False
        self.validation_mutation = True
        with self.assertRaisesRegex(p.Refusal, "validation changed a frozen value"):
            self.prove()
        self.assertFalse(self.out.exists())

    def test_new_recursive_json_member_or_symlink_refuses(self):
        extra = self.repo / p.BLOCKS_DIRECTORY / "nested/extra.json"
        extra.parent.mkdir()
        extra.write_bytes(b"[]")
        with self.assertRaisesRegex(p.Refusal, "membership differs"):
            self.prove()
        extra.unlink()
        extra.symlink_to(self.repo / p.BLOCKS_DIRECTORY / "blocks.json")
        with self.assertRaisesRegex(p.Refusal, "symlink"):
            self.prove()
        self.assertFalse(self.out.exists())

    def test_data_change_or_new_member_during_generation_refuses(self):
        file = self.repo / p.BLOCKS_DIRECTORY / "blocks.json"
        self.change_during_generation = lambda: file.write_bytes(b"changed")
        with self.assertRaisesRegex(p.Refusal, "file digest differs"):
            self.prove()
        self.assertFalse(self.out.exists())

    def test_recipe_scope_and_selector_cannot_be_redirected(self):
        for field, value in (("case_count", 2), ("public_repository", "other/repo")):
            recipe = copy.deepcopy(self.recipe)
            recipe[field] = value
            digest = self.write_json(p.RECIPE_FILE, recipe)
            with self.subTest(field=field), patch.object(p, "RECIPE_SHA256", digest), self.assertRaises(p.Refusal):
                self.prove()
        self.assertFalse(self.out.exists())

    def test_public_anchor_value_is_checked_after_raw_pin(self):
        recipe = copy.deepcopy(self.recipe)
        anchor = recipe["accepted_hash_chain"]["public_anchors"][0]
        file = self.repo / anchor["path"]
        raw = file.read_bytes().replace(p.replay.LEGACY_RESULT_SHA256.encode(), b"0" * 64)
        anchor["raw_sha256"] = self.write(anchor["path"], raw)
        digest = self.write_json(p.RECIPE_FILE, recipe)
        with patch.object(p, "RECIPE_SHA256", digest), self.assertRaisesRegex(p.Refusal, "anchor value differs"):
            self.prove()
        self.assertFalse(self.out.exists())

    def test_frozen_named_row_drift_refuses(self):
        self.golden["cohorts"]["python_legacy"][531]["id"] = "another-case"
        digest = self.write_json(p.GOLDENS_FILE, self.golden)
        recipe = copy.deepcopy(self.recipe)
        recipe["accepted_hash_chain"]["sealed_public_goldens"]["raw_sha256"] = digest
        recipe_digest = self.write_json(p.RECIPE_FILE, recipe)
        with patch.object(p.replay, "GOLDENS_SHA256", digest), patch.object(p, "RECIPE_SHA256", recipe_digest), self.assertRaisesRegex(p.Refusal, "named row differs"):
            self.prove()
        self.assertFalse(self.out.exists())

    def test_closed_runtime_reuses_exact_source_loader_and_restores_import_state(self):
        sources = p.ProofSources(self.repo)
        original_meta = sys.meta_path[:]
        original_path = sys.path[:]
        with p.checked_runtime(sources) as (_compact, _flow, _v1, check_loaded):
            self.assertEqual(len(check_loaded()), 10)
            self.assertNotIn("probe_flow_maximum_matrix", sys.modules)
            self.assertNotIn("som_hosted_serializer", sys.modules)
            self.assertIsNone(sys.modules["som_core.flow_compact"].__cached__)
        self.assertEqual(sys.meta_path, original_meta)
        self.assertEqual(sys.path, original_path)
        self.assertNotIn("som_core.flow_compact", sys.modules)

    def test_cli_emits_one_case_proof_and_sanitizes_failures(self):
        output = io.StringIO()
        with patch.object(p, "checked_runtime", self.fake_runtime), redirect_stdout(output):
            code = p.main(["--repo", str(self.repo), "--out", str(self.out), "--tested-sha", "a" * 40])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())["product_acceptance_passed"])
        error = io.StringIO()
        with redirect_stderr(error):
            code = p.main(["--repo", str(self.repo), "--out", str(self.repo / "bad"), "--tested-sha", "bad"])
        self.assertEqual(code, 2)
        self.assertNotIn(str(self.root), error.getvalue())


if __name__ == "__main__":
    unittest.main()
