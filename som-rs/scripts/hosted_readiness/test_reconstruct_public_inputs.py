"""Synthetic public reconstruction fixtures. Execute only on GitHub runners."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
import io
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import py_compile
import sys
import tempfile
import unittest
from unittest.mock import patch

import reconstruct_public_inputs as r


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.out = self.root / "output"
        self.goldens = self.root / "goldens.json"
        self.sources = {}
        self.generated = []
        self.prompt_cnl = {}
        self.fixture()

    def source(self, path, raw):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
        self.sources[path] = {"path": path, "revision": r.PUBLIC_REVISION, "raw_sha256": r.sha(raw)}

    def fixture(self):
        fixtures, public, legacy, direct = [], [], [], []
        for index in range(127):
            identity = "public-" + str(index)
            prompt, cnl = {"version": "som.prompt.v7", "fixture": index}, "PUBLIC CNL " + str(index)
            fixtures.append({"id": identity, "expected_prompt": prompt, "cnl": cnl})
            public.append({"id": identity, "index": index, "prompt_sha256": r.value_sha(prompt),
                           "cnl_sha256": r.sha(cnl.encode()), "public_source": {"path": r.FIXTURES,
                           "prompt_pointer": f"/cases/{index}/expected_prompt", "cnl_pointer": f"/cases/{index}/cnl"},
                           "request_sha256": {"compile-json": r.sha(r.request({"format": "som.prompt-json.v1", "prompt": prompt}, public=True)),
                                              "compile-cnl": r.sha(r.request({"format": "som.cnl.v1", "source": cnl}, public=True))}})
        self.source(r.FIXTURES, json.dumps({"cases": fixtures}).encode())
        direct_path = "som-core/data/composed/regression.jsonl"
        for index in range(544):
            ready_direct, generated = index < 443, 443 <= index < 533
            key = index if index < 440 else index - 440 if ready_direct else index if generated else 6000 + min(index - 533, 9)
            prompt = {"version": "som.prompt.v7", "fixture": key}
            cnl = "PUBLIC LEGACY CNL " + str(key)
            self.prompt_cnl[r.value_sha(prompt)] = cnl
            identity = "m2-maximum-" + str(index) if generated else "legacy-" + str(index)
            row = {"id": identity, "index": index, "prompt_sha256": r.value_sha(prompt), "cnl_sha256": r.sha(cnl.encode()),
                   "input_provenance": "verified_tracked" if ready_direct else "public_generator_unrun" if generated else "blocked_no_public_source"}
            if ready_direct:
                direct.append(json.dumps({"prompt": prompt}))
                row["public_sources"] = [{"path": direct_path, "line_1based": len(direct), "pointer": "/prompt"}]
                row["typed_request_sha256"] = r.sha(r.request({"format": "som.prompt-json.v1", "prompt": prompt}, public=False))
            elif generated:
                self.generated.append((identity.removeprefix("m2-"), prompt))
                row["public_recipe"] = {"path": r.PROBE, "function": "maximum_matrix", "id_prefix": "m2-",
                                        "source_sha256": r.RUNTIME_PINS[r.PROBE],
                                        "constructor": {"path": r.FREEZE, "function": "requirement",
                                                        "source_sha256": r.RUNTIME_PINS[r.FREEZE]}}
            if index == 0:
                row["checked_dsl_provenance"] = "public_reference_recipe_unrun"
            elif index in {533, 534}:
                row["checked_dsl_provenance"] = "blocked_no_public_source"
            legacy.append(row)
        self.source(direct_path, ("\n".join(direct) + "\n").encode())
        while len(self.sources) < r.SOURCE_COUNT:
            self.source("public/padding-" + str(len(self.sources)) + ".txt", b"public metadata\n")
        self.document = {"version": "som.public-golden-hashes.v1", "public_repository": "faberline/som",
                         "public_source_revision": r.PUBLIC_REVISION, "product_acceptance_passed": False,
                         "public_source_files": list(self.sources.values()),
                         "cohorts": {"python_public": public, "python_legacy": legacy}}
        raw = (json.dumps(self.document, indent=2) + "\n").encode()
        self.goldens.write_bytes(raw)
        self.sealed = r.sha(raw)

    @contextmanager
    def fake_runtime(self, sources):
        def serialize(prompt, *, contract_dir):
            self.assertEqual(contract_dir, self.repo / "som-rs/contracts/compiler-v1")
            return self.prompt_cnl[r.value_sha(prompt)]
        yield lambda: iter(self.generated), serialize

    def reconstruct(self, document=None):
        with patch.object(r, "runtime", self.fake_runtime):
            return r.reconstruct(self.document if document is None else document, self.repo, self.out)

    def test_complete_named_manifest_keeps_blocked_rows_and_duplicate_prompts(self):
        report = self.reconstruct()
        self.assertFalse(report["product_acceptance_passed"])
        self.assertFalse(report["full_input_set_ready"])
        self.assertEqual(report["product_suites"], "unrun")
        self.assertEqual(report["counts"]["legacy_reconstructed"], 533)
        self.assertEqual(report["counts"]["legacy_blocked"], 11)
        rows = report["cohorts"]["python_legacy"]
        self.assertEqual([row["id"] for row in rows], [row["id"] for row in self.document["cohorts"]["python_legacy"]])
        self.assertEqual(rows[0]["prompt_sha256"], rows[440]["prompt_sha256"])
        self.assertEqual(rows[0]["requests"]["compile-json"]["sha256"], rows[440]["requests"]["compile-json"]["sha256"])
        self.assertNotEqual(rows[0]["requests"]["compile-json"]["path"], rows[440]["requests"]["compile-json"]["path"])
        self.assertNotIn("requests", rows[533])
        self.assertEqual(len(report["blocked_checked_dsl_ids"]), 2)
        self.assertEqual(len(report["unrun_checked_dsl_ids"]), 1)
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(manifest["counts"], report["counts"])

    def test_public_request_is_compact_and_legacy_request_keeps_member_order(self):
        self.reconstruct()
        public = (self.out / "python_public/0000/compile-json.request.raw").read_bytes()
        legacy = (self.out / "python_legacy/0000/compile-json.request.raw").read_bytes()
        self.assertEqual(public, b'{"format":"som.prompt-json.v1","prompt":{"version":"som.prompt.v7","fixture":0}}')
        self.assertTrue(legacy.endswith(b"\n"))
        self.assertLess(legacy.index(b'"version"'), legacy.index(b'"fixture"'))

    def test_public_file_change_refuses_before_output(self):
        (self.repo / r.FIXTURES).write_bytes(b"{}")
        with self.assertRaisesRegex(r.Refusal, "source digest"):
            self.reconstruct()
        self.assertFalse(self.out.exists())

    def test_golden_export_needs_committed_pin_and_exact_bytes(self):
        with patch.object(r, "GOLDENS_SHA256", self.sealed):
            self.assertEqual(r.load_goldens(self.goldens), self.document)
            self.goldens.write_bytes(self.goldens.read_bytes() + b" ")
            with self.assertRaisesRegex(r.Refusal, "not sealed"):
                r.load_goldens(self.goldens)
        with patch.object(r, "GOLDENS_SHA256", "UNSEALED"):
            with self.assertRaisesRegex(r.Refusal, "SHA-256"):
                r.load_goldens(self.goldens)

    def test_removed_reordered_or_duplicated_case_refuses(self):
        for action in ("remove", "reorder", "duplicate"):
            doc = copy.deepcopy(self.document)
            rows = doc["cohorts"]["python_legacy"]
            if action == "remove":
                rows.pop()
            elif action == "reorder":
                rows[0], rows[1] = rows[1], rows[0]
            else:
                rows[1] = copy.deepcopy(rows[0])
            with self.subTest(action=action), self.assertRaises(r.Refusal):
                self.reconstruct(doc)
        self.assertFalse(self.out.exists())

    def test_generated_identity_missing_or_duplicate_refuses(self):
        self.generated[-1] = self.generated[0]
        with self.assertRaisesRegex(r.Refusal, "duplicate generated identity"):
            self.reconstruct()
        self.assertFalse((self.out / "manifest.json").exists())

    def test_generated_prompt_or_cnl_drift_refuses(self):
        doc = copy.deepcopy(self.document)
        doc["cohorts"]["python_legacy"][443]["cnl_sha256"] = "f" * 64
        with self.assertRaisesRegex(r.Refusal, "CNL differs"):
            self.reconstruct(doc)
        self.assertFalse((self.out / "manifest.json").exists())

    def test_direct_request_key_order_is_part_of_frozen_bytes(self):
        doc = copy.deepcopy(self.document)
        doc["cohorts"]["python_legacy"][0]["typed_request_sha256"] = "0" * 64
        with self.assertRaisesRegex(r.Refusal, "request bytes differ"):
            self.reconstruct(doc)

    def test_unbound_recipe_dependency_refuses_before_import(self):
        sources = r.Sources(self.repo, self.document)
        with self.assertRaises(OSError):
            with r.runtime(sources):
                self.fail("unbound code must not execute")

    def recipe_fixture(self, *, unbound=False):
        modules = {
            "som-core/src/som_core/__init__.py": b"SOURCE_MARKER = 'source_package'\n",
            "som-core/src/som_core/allowed.py": b"SOURCE_MARKER = 'source_allowed'\n",
            r.FREEZE: b"SOURCE_MARKER = 'source_freeze'\n",
            r.PROBE: (b"from som_core import allowed\nfrom freeze_flow_cases import SOURCE_MARKER\n"
                      b"def maximum_matrix():\n    return [(allowed.SOURCE_MARKER, SOURCE_MARKER)]\n"),
            r.SERIALIZER: b"def serialize_prompt(prompt, *, contract_dir):\n    return 'source_serializer'\n",
        }
        if unbound:
            modules[r.PROBE] = b"from som_core import unbound\n"
        for path, raw in modules.items():
            file = self.repo / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(raw)
        return modules, {path: r.sha(raw) for path, raw in modules.items()}

    def test_runtime_executes_exact_source_despite_cached_pyc_and_extension_shadows(self):
        modules, pins = self.recipe_fixture()
        probe_file = self.repo / r.PROBE
        # A normal source loader accepts this unchecked-hash cache even after
        # restoring the separately pinned source bytes. It must never execute.
        probe_file.write_bytes(b"raise AssertionError('cached bytecode executed')\n")
        py_compile.compile(str(probe_file), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        probe_file.write_bytes(modules[r.PROBE])
        cache = Path(importlib.util.cache_from_source(str(probe_file)))
        cache_before = cache.read_bytes()
        shadows = []
        for stem in (probe_file.with_suffix(""), self.repo / "som-core/src/som_core/allowed"):
            shadow = Path(str(stem) + importlib.machinery.EXTENSION_SUFFIXES[0])
            shadow.write_bytes(b"invalid extension: this file must never be loaded")
            shadows.append(shadow)
        sources = r.Sources(self.repo, self.document)
        ambient_path = [str(self.repo / "som-core/scripts"), str(self.repo / "som-core/src"), *sys.path]
        original_meta = sys.meta_path[:]
        with patch.object(r, "RUNTIME_PINS", pins), patch.object(sys, "path", ambient_path):
            with r.runtime(sources) as (maximum_matrix, serialize_prompt):
                self.assertEqual(maximum_matrix(), [("source_allowed", "source_freeze")])
                self.assertEqual(serialize_prompt({}, contract_dir=self.repo), "source_serializer")
                self.assertIsNone(sys.modules["probe_flow_maximum_matrix"].__cached__)
                self.assertEqual(sys.path, ambient_path)
        self.assertEqual(sys.meta_path, original_meta)
        self.assertEqual(cache.read_bytes(), cache_before)
        self.assertTrue(all(path.read_bytes().startswith(b"invalid extension") for path in shadows))
        self.assertNotIn("probe_flow_maximum_matrix", sys.modules)
        self.assertNotIn("som_core.allowed", sys.modules)

    def test_unbound_som_core_source_refuses_before_execution(self):
        _modules, pins = self.recipe_fixture(unbound=True)
        sentinel = self.root / "unbound-executed"
        file = self.repo / "som-core/src/som_core/unbound.py"
        file.write_text("from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text('bad')\n")
        sources = r.Sources(self.repo, self.document)
        ambient_path = [str(self.repo / "som-core/src"), *sys.path]
        with patch.object(r, "RUNTIME_PINS", pins), patch.object(sys, "path", ambient_path):
            with self.assertRaisesRegex(r.Refusal, "refused before execution"):
                with r.runtime(sources):
                    self.fail("unbound module must not load")
        self.assertFalse(sentinel.exists())
        self.assertNotIn("som_core.unbound", sys.modules)

    def test_changed_pinned_source_refuses_at_exec_before_body(self):
        _modules, pins = self.recipe_fixture()
        sources = r.Sources(self.repo, self.document)
        loader = r.PinnedSourceModules(sources)
        spec = loader.find_spec("probe_flow_maximum_matrix")
        module = importlib.util.module_from_spec(spec)
        sentinel = self.root / "changed-source-executed"
        (self.repo / r.PROBE).write_text("from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text('bad')\n")
        with patch.object(r, "RUNTIME_PINS", pins):
            with self.assertRaisesRegex(r.Refusal, "digest differs before execution"):
                loader.exec_module(module)
        self.assertFalse(sentinel.exists())

    def test_output_must_be_new_and_outside_repository(self):
        for out in (self.repo / "generated", self.repo, self.root):
            with self.subTest(out=out.name), self.assertRaises(r.Refusal):
                with patch.object(r, "runtime", self.fake_runtime):
                    r.reconstruct(self.document, self.repo, out)

    def test_public_symlink_refuses(self):
        path = self.repo / r.FIXTURES
        raw = path.read_bytes()
        path.unlink()
        replacement = self.root / "replacement.json"
        replacement.write_bytes(raw)
        path.symlink_to(replacement)
        with self.assertRaisesRegex(r.Refusal, "symlink"):
            self.reconstruct()

    def test_private_packet_is_not_an_accepted_golden(self):
        raw = b'{"classification":"LOCAL PRIVATE REVIEW","secret":"PRIVATE_SENTINEL"}'
        self.goldens.write_bytes(raw)
        output = io.StringIO()
        with patch.object(r, "GOLDENS_SHA256", self.sealed), redirect_stderr(output):
            code = r.main(["--repo", str(self.repo), "--goldens", str(self.goldens), "--out", str(self.out)])
        self.assertEqual(code, 2)
        self.assertNotIn("PRIVATE_SENTINEL", output.getvalue())
        self.assertFalse(self.out.exists())

    def test_cli_reports_partial_scope_without_accepting_products(self):
        output = io.StringIO()
        with patch.object(r, "GOLDENS_SHA256", self.sealed), patch.object(r, "runtime", self.fake_runtime), redirect_stdout(output):
            code = r.main(["--repo", str(self.repo), "--goldens", str(self.goldens), "--out", str(self.out)])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["product_acceptance_passed"])
        self.assertEqual(report["counts"]["legacy_blocked"], 11)


if __name__ == "__main__":
    unittest.main()
