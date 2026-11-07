"""Synthetic dependency reuse admission tests. Run only on GitHub."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

if os.environ.get("GITHUB_ACTIONS") != "true":
    raise RuntimeError("GitHub tests only")
SUBJECT = Path(__file__).with_name("dependency_candidate.py")
p = ModuleType("dependency_candidate_tests_subject")
p.__file__, p.__cached__, p.__package__ = str(SUBJECT), None, ""
sys.modules[p.__name__] = p
exec(compile(SUBJECT.read_bytes(), str(SUBJECT), "exec", dont_inherit=True), p.__dict__)
HELPER = "a" * 40


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class FakeSource:
    def __init__(self, root):
        self.root, self.revision = root, HELPER
        self.before = {"som-rs/src/compiler.rs": b"same owning factory\n",
                       "som-core/data/public.json": b'{"fixed":true}\n'}
        self.current = dict(self.before)
        for name, raw in self.current.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.extra = {}

    def tree(self, revision):
        entries = self.before if revision == p.CANDIDATE else self.current
        return {name: {"mode": "120000" if name in p.HISTORICAL_ALIASES else "100644", "oid": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
                for name, raw in entries.items() if name in p.PROTECTED_EXACT or name.startswith(p.PROTECTED_PREFIXES)}

    def blob(self, revision, name, working=False):
        if name in self.extra:
            return self.extra[name]
        raw = (self.before if revision == p.CANDIDATE else self.current)[name]
        if working and (self.root / name).read_bytes() != raw:
            raise p.Refusal("working source differs")
        return raw


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = FakeSource(self.root)
        self.value = {"factory_source_pins": {name: p.sha(raw) for name, raw in self.source.before.items()},
                      "compile_assembly_source_pins": {name: p.sha(raw) for name, raw in self.source.before.items()}}

    def test_whole_protected_tree_and_every_listed_source_are_equal(self):
        record = p.same_sources(self.source, self.value)
        self.assertEqual(record["all_listed_source_pins"], 2)
        self.assertEqual(record["protected_tracked_files"], 2)
        self.assertEqual(record["listed_source_map_canonical_sha256"], p.canonical_sha(self.value["factory_source_pins"]))

    def test_unchanged_historical_alias_keeps_its_exact_target(self):
        name, target = next(iter(p.HISTORICAL_ALIASES.items()))
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.parent / target).mkdir()
        path.symlink_to(target)
        self.source.before[name] = self.source.current[name] = target.encode()
        self.assertEqual(p.same_sources(self.source, self.value)["protected_tracked_files"], 3)
        path.unlink()
        path.symlink_to("different-target")
        with self.assertRaisesRegex(p.SourceChanged, "historical source alias"):
            p.same_sources(self.source, self.value)

    def test_reviewed_helper_bootstrap_change_keeps_old_candidate_provenance(self):
        name = "som-rs/scripts/hosted_readiness/experimental_native_compile.py"
        old, new = b"old comparison helper", b"reviewed source bootstrap helper"
        self.source.before[name], self.source.current[name] = old, new
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new)
        self.value["compile_assembly_source_pins"][name] = p.sha(old)
        self.value["admitted_dependency_helper_changes"] = {name: {"allowed_helper_sha256": [p.sha(new)]}}
        result = p.same_sources(self.source, self.value)
        self.assertEqual(result["separate_helper_source_changes"][name]["candidate_sha256"], p.sha(old))
        self.assertEqual(result["separate_helper_source_changes"][name]["helper_sha256"], p.sha(new))
        self.value["admitted_dependency_helper_changes"][name]["allowed_helper_sha256"] = []
        with self.assertRaises(p.SourceChanged):
            p.same_sources(self.source, self.value)

    def test_added_or_deleted_build_input_requires_full_candidate(self):
        self.source.current["som-rs/src/new_module.rs"] = b"new candidate code"
        with self.assertRaisesRegex(p.SourceChanged, "full candidate"):
            p.same_sources(self.source, self.value)
        self.source.current = dict(self.source.before)
        del self.source.current["som-rs/src/compiler.rs"]
        with self.assertRaises(p.SourceChanged):
            p.same_sources(self.source, self.value)

    def test_only_the_named_unreferenced_profile_is_outside_the_build_tree(self):
        source = object.__new__(p.GitSource)
        base = b"100644 blob " + b"1" * 40 + b"\tsom-rs/src/compiler.rs\0"
        extra = b"100644 blob " + b"2" * 40 + b"\tsom-rs/contracts/native-tools-v1/product-rust-tools-v1.json\0"
        source.git = lambda *args: base if args[-1] == p.CANDIDATE else base + extra
        self.assertEqual(source.tree(p.CANDIDATE), source.tree(HELPER))
        unknown = b"120000 blob " + b"3" * 40 + b"\tsom-rs/src/unknown-alias\0"
        source.git = lambda *args: base + unknown
        with self.assertRaisesRegex(p.SourceChanged, "not a regular Git blob"):
            source.tree(HELPER)

    def test_owning_alias_or_contract_change_cannot_use_old_candidate(self):
        for path in ("som-rs/src/compiler.rs", "som-core/data/public.json"):
            with self.subTest(path=path):
                self.source.current = dict(self.source.before)
                self.source.current[path] = b"changed input bytes"
                with self.assertRaises(p.SourceChanged):
                    p.same_sources(self.source, self.value)

    def test_dirty_working_bytes_fail_even_with_equal_git_trees(self):
        path = self.root / "som-rs/src/compiler.rs"
        path.write_bytes(b"dirty source bytes")
        with self.assertRaisesRegex(p.SourceChanged, "working source"):
            p.same_sources(self.source, self.value)

    def test_conflicting_or_wrong_independent_source_pin_fails(self):
        self.value["compile_assembly_source_pins"]["som-rs/src/compiler.rs"] = "f" * 64
        with self.assertRaisesRegex(p.Refusal, "source maps disagree"):
            p.same_sources(self.source, self.value)

    def test_linked_source_and_oversized_metadata_are_refused_before_read(self):
        path = self.root / "linked"
        path.symlink_to(self.root / "som-rs/src/compiler.rs")
        with self.assertRaisesRegex(p.Refusal, "linked"):
            p.read(path)
        path.unlink()
        with path.open("wb") as stream:
            stream.truncate(1025)
        with patch.object(Path, "open", side_effect=AssertionError("oversized input read")):
            with self.assertRaisesRegex(p.Refusal, "oversized"):
                p.read(path, 1024)

    def origin_fixture(self):
        value = {"repository": "faberline/som", "repository_id": 7,
                 "artifact": {"artifact_id": 11, "run_id": 22, "name": "recorded-data", "compressed_bytes": 123,
                              "github_zip_digest": "sha256:" + "b" * 64, "expires_at": "2099-01-01T00:00:00Z"}}
        origin = {"id": 11, "name": "recorded-data", "size_in_bytes": 123, "digest": "sha256:" + "b" * 64,
                  "expired": False, "expires_at": "2099-01-01T00:00:00Z",
                  "workflow_run": {"id": 22, "head_sha": p.CANDIDATE, "repository_id": 7, "head_repository_id": 7}}
        return value, origin

    def test_origin_binds_exact_candidate_run_repository_digest_and_retention(self):
        value, origin = self.origin_fixture()
        record = p.check_origin(origin, value)
        self.assertEqual(record["artifact_id"], 11)
        self.assertFalse(record["zip_digest_locally_recomputed"])
        for key, bad in (("id", 12), ("digest", "sha256:" + "c" * 64), ("expired", True), ("size_in_bytes", 124)):
            changed = copy.deepcopy(origin)
            changed[key] = bad
            with self.subTest(key=key), self.assertRaises(p.Refusal):
                p.check_origin(changed, value)
        for key, bad in (("id", 23), ("head_sha", HELPER), ("repository_id", 8), ("head_repository_id", 8)):
            changed = copy.deepcopy(origin)
            changed["workflow_run"][key] = bad
            with self.subTest(key=key), self.assertRaises(p.Refusal):
                p.check_origin(changed, value)

    def test_expired_reviewed_artifact_is_not_implicitly_replaced(self):
        value, origin = self.origin_fixture()
        value["artifact"]["expires_at"] = origin["expires_at"] = "2020-01-01T00:00:00Z"
        with self.assertRaisesRegex(p.Refusal, "new full candidate"):
            p.check_origin(origin, value)

    def artifact_fixture(self):
        root = self.root / "artifact"
        (root / "assembly").mkdir(parents=True)
        compile_record = {"tested_sha": p.CANDIDATE, "experiment_complete": True, "product_acceptance_passed": False,
                          "initial_identities": {"binary": {"sha256": "d" * 64}}, "asset_manifest_canonical_sha256": "e" * 64}
        assembly = {"tested_sha": p.CANDIDATE, "assembly_experiment_complete": True, "product_acceptance_passed": False}
        (root / "manifest.json").write_bytes(encoded(compile_record))
        (root / "assembly/manifest.json").write_bytes(encoded(assembly))
        (root / "raw.data").write_bytes(b"every saved file is bound\n")
        files = {path.relative_to(root).as_posix(): p.sha(path.read_bytes()) for path in root.rglob("*") if path.is_file()}
        value = {"candidate_binary_saved_sha256": "d" * 64, "asset_manifest_canonical_sha256": "e" * 64,
                 "artifact": {"complete_file_count": 3, "complete_file_inventory_canonical_sha256": p.canonical_sha(files),
                              "compile_manifest_sha256": files["manifest.json"], "assembly_manifest_sha256": files["assembly/manifest.json"]}}
        return root, value

    def test_complete_saved_file_inventory_is_rehashed_not_only_flags(self):
        root, value = self.artifact_fixture()
        record = p.artifact_inventory(root, value)
        self.assertEqual(record["complete_file_count"], 3)
        (root / "raw.data").write_bytes(b"changed but manifest success remains true")
        with self.assertRaisesRegex(p.Refusal, "complete reviewed"):
            p.artifact_inventory(root, value)

    def test_extra_missing_or_linked_saved_file_refuses_reuse(self):
        root, value = self.artifact_fixture()
        extra = root / "unknown.data"
        extra.write_bytes(b"extra")
        with self.assertRaises(p.Refusal):
            p.artifact_inventory(root, value)
        extra.unlink()
        original = root / "raw.data"
        original.unlink()
        with self.assertRaises(p.Refusal):
            p.artifact_inventory(root, value)
        original.symlink_to(self.root / "som-rs/src/compiler.rs")
        with self.assertRaisesRegex(p.Refusal, "linked"):
            p.artifact_inventory(root, value)

    def test_cross_revision_requires_fixed_candidate_and_full_artifact(self):
        with patch.object(p, "GitSource", return_value=self.source):
            for candidate, artifact in (("b" * 40, self.root), (p.CANDIDATE, None)):
                with self.subTest(candidate=candidate), self.assertRaisesRegex(p.Refusal, "fixed reviewed"):
                    p.verify_candidate_inputs(self.root, HELPER, candidate, artifact)

    def test_helper_and_candidate_roles_are_not_relabelled(self):
        root, value = self.artifact_fixture()
        value.update(self.value)
        value.update(approved_byte_audit={}, purpose="synthetic data only")
        with patch.object(p, "GitSource", return_value=self.source), patch.object(p, "recipe", return_value=value), \
             patch.object(p, "artifact_origin", return_value={"artifact_id": 11}):
            record = p.verify_candidate_inputs(self.root, HELPER, p.CANDIDATE, root)
        self.assertEqual(record["helper_revision"], HELPER)
        self.assertEqual(record["candidate_revision"], p.CANDIDATE)
        self.assertFalse(record["product_acceptance_passed"])
        self.assertEqual(record["mode"], "pinned-f59-dependency-reuse")

    def test_manual_full_mode_bypasses_reuse_and_keeps_current_identity(self):
        with patch.object(p, "GitSource", return_value=self.source), \
             patch.object(p, "recipe", side_effect=AssertionError("old artifact should not be required")):
            record = p.classify(self.root, HELPER, force_full=True)
        self.assertEqual(record["run_full_candidate"], "true")
        self.assertEqual(record["candidate_revision"], HELPER)

    def test_genuine_source_change_selects_full_not_old_candidate(self):
        self.source.current["som-rs/src/compiler.rs"] = b"new compiler"
        with patch.object(p, "GitSource", return_value=self.source), patch.object(p, "recipe", return_value=self.value):
            record = p.classify(self.root, HELPER)
        self.assertEqual(record["run_full_candidate"], "true")
        self.assertEqual(record["candidate_revision"], HELPER)

    def test_origin_unavailable_does_not_silently_start_a_large_build(self):
        with patch.object(p, "GitSource", return_value=self.source), patch.object(p, "recipe", return_value=self.value), \
             patch.object(p, "artifact_origin", side_effect=p.Refusal("origin unavailable")):
            with self.assertRaisesRegex(p.Refusal, "origin unavailable"):
                p.classify(self.root, HELPER)

    def test_recipe_or_review_cannot_be_replaced_by_caller_hash(self):
        self.source.extra[p.RECIPE] = b'{"version":"forged-but-passed"}'
        with self.assertRaisesRegex(p.Refusal, "fixed reviewed reuse recipe"):
            p.recipe(self.source)

    def test_duplicate_metadata_and_traversal_are_refused(self):
        with self.assertRaisesRegex(p.Refusal, "duplicate"):
            p.parse(b'{"source":1,"source":2}')
        for path in ("../outside", "/outside", "a//b", "a\\b", "a\nb"):
            with self.subTest(path=path), self.assertRaises(p.Refusal):
                p.relative(path)


if __name__ == "__main__":
    unittest.main()
