"""GitHub-only checks for the non-acceptance historical source experiment."""
from dataclasses import FrozenInstanceError
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import react_source_snapshot as proof


class SourceSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The owner workflow checks out its exact push SHA with fetch-depth >= 4.
        # These tests are intentionally not run on the local authoring host.
        cls.repo = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
        cls.owner = proof._git(cls.repo, "show", f"{proof.BASE}:{proof.OWNER}")

    def test_current_source_subset_proof_is_not_acceptance(self):
        result = proof.inspect_snapshot(self.repo)
        self.assertEqual(result.label, "HISTORICAL SOURCE SUBSET / NO ACCEPTANCE")
        self.assertEqual(result.base_source, proof.BASE)
        self.assertEqual(result.checkout_head, os.environ["GITHUB_SHA"])
        self.assertEqual(result.backend_sha256, proof.BACKEND_SHA)
        self.assertEqual(result.target_sha256, proof.TARGET_SHA)
        self.assertEqual(result.formatter_sha256, proof.FORMATTER_SHA)
        self.assertEqual(result.receipt_file_count, 54)
        self.assertFalse(result.acceptance_granted)
        with self.assertRaises(FrozenInstanceError):
            result.acceptance_granted = True
        with self.assertRaises(TypeError):
            proof.SnapshotProof("x", "x", "x", "x", "x", "x", 0,
                                acceptance_granted=True)

    def test_wrong_runner_host_or_repository_is_refused(self):
        for changes in ({"RUNNER_OS": "macOS"}, {"RUNNER_ARCH": "ARM64"},
                        {"GITHUB_REPOSITORY": "example/other"},
                        {"GITHUB_ACTIONS": "false"}, {"RUNNER_ENVIRONMENT": "self-hosted"}):
            with self.subTest(changes=changes), patch.dict(os.environ, changes):
                with self.assertRaises(proof.SnapshotRefused):
                    proof.inspect_snapshot(self.repo)

    def test_untrusted_head_is_refused(self):
        with patch.dict(os.environ, {"GITHUB_SHA": "0" * 40}):
            with self.assertRaisesRegex(proof.SnapshotRefused, "head differs"):
                proof.inspect_snapshot(self.repo)
        with patch.dict(os.environ, {"GITHUB_SHA": "not-a-commit"}):
            with self.assertRaisesRegex(proof.SnapshotRefused, "head missing"):
                proof.inspect_snapshot(self.repo)

    def test_factory_parser_binds_all_seven_exact_aliases(self):
        self.assertEqual(proof._factory_aliases(self.owner), proof.ALIASES)
        altered = self.owner.replace(b'"../profile.rs"', b'"../not-profile.rs"', 1)
        self.assertNotEqual(altered, self.owner)
        # Altering the fixed constant is confined to this test process. It
        # forces the parser to inspect a synthetic source whose SHA matches.
        with patch.object(proof, "OWNER_SHA", hashlib.sha256(altered).hexdigest()):
            with self.assertRaisesRegex(proof.SnapshotRefused, "aliases differ"):
                proof._factory_aliases(altered)
        with self.assertRaisesRegex(proof.SnapshotRefused, "owner source differs"):
            proof._factory_aliases(altered)

    def test_duplicate_keys_and_unsafe_paths_fail_closed(self):
        with self.assertRaises(proof.SnapshotRefused):
            proof._json(b'{"files":{},"files":{}}')
        for path in ("../source", "/absolute", "a/../b", "a//b", "a\\b", "a\0b"):
            with self.subTest(path=path), self.assertRaises(proof.SnapshotRefused):
                proof._path(path)

    def test_missing_or_modified_receipt_fails(self):
        real_read = proof._bound_file
        target = proof.RECEIPTS[-1][0]
        def altered(repo, path):
            raw = real_read(repo, path)
            return raw + b" " if path == target else raw
        with patch.object(proof, "_bound_file", side_effect=altered):
            with self.assertRaisesRegex(proof.SnapshotRefused, "receipt raw hash differs"):
                proof.inspect_snapshot(self.repo)

    def test_newer_or_missing_amendment_receipt_fails(self):
        known = {path for path, _ in proof.RECEIPTS[1:]}
        for receipts in (known | {"som-rs/evidence/react-output-contract-amendment-003.json"}, set()):
            with self.subTest(receipts=receipts), patch.object(proof, "_receipt_files", return_value=receipts):
                with self.assertRaisesRegex(proof.SnapshotRefused, "amendment receipt set differs"):
                    proof.inspect_snapshot(self.repo)

    def test_modified_frozen_member_fails(self):
        real_read = proof._bound_file
        target = proof.TOOLCHAIN
        def altered(repo, path):
            raw = real_read(repo, path)
            return raw + b" " if path == target else raw
        with patch.object(proof, "_bound_file", side_effect=altered):
            with self.assertRaisesRegex(proof.SnapshotRefused, "receipt member differs"):
                proof.inspect_snapshot(self.repo)

    def test_extra_or_missing_receipt_scope_file_fails(self):
        actual = proof._scope_files(self.repo)
        for altered in (actual | {"som-rs/contracts/react-output-v1/unexpected"}, set()):
            with self.subTest(size=len(altered)), patch.object(proof, "_scope_files", return_value=altered):
                with self.assertRaisesRegex(proof.SnapshotRefused, "receipt file set differs"):
                    proof.inspect_snapshot(self.repo)

    def test_ignored_python_cache_in_contract_scope_fails(self):
        root = self.repo / proof.CONTRACT_ROOTS[0]
        with patch.object(proof.os, "walk", return_value=[(str(root), ["__pycache__"], [])]):
            with self.assertRaisesRegex(proof.SnapshotRefused, "cached files"):
                proof._scope_files(self.repo)

    def test_extra_unknown_factory_tuple_is_refused(self):
        marker = b'const BACKEND_FILES: &[(&str, &str)] = &['
        altered = self.owner.replace(marker, marker + b' ("unexpected", include_str!("mod.rs")),', 1)
        with patch.object(proof, "OWNER_SHA", hashlib.sha256(altered).hexdigest()):
            with self.assertRaisesRegex(proof.SnapshotRefused, "aliases differ"):
                proof._factory_aliases(altered)

    def test_wrong_expected_backend_or_target_digest_is_refused(self):
        with patch.object(proof, "BACKEND_SHA", "0" * 64):
            with self.assertRaisesRegex(proof.SnapshotRefused, "backend derivation differs"):
                proof.inspect_snapshot(self.repo)
        with patch.object(proof, "TARGET_SHA", "0" * 64):
            with self.assertRaisesRegex(proof.SnapshotRefused, "target map differs"):
                proof.inspect_snapshot(self.repo)

    def test_untracked_or_dirty_protected_paths_fail(self):
        original = proof._git
        def git(repo, *args):
            if args and args[0] == "status":
                return b"?? som-rs/contracts/react-output-v1/unknown\n"
            return original(repo, *args)
        with patch.object(proof, "_git", side_effect=git):
            with self.assertRaisesRegex(proof.SnapshotRefused, "protected source"):
                proof.inspect_snapshot(self.repo)

    def test_head_change_after_observation_fails(self):
        original = proof._git
        head_reads = 0
        def git(repo, *args):
            nonlocal head_reads
            if args == ("rev-parse", "HEAD"):
                head_reads += 1
                if head_reads == 2:
                    return ("0" * 40 + "\n").encode()
            return original(repo, *args)
        with patch.object(proof, "_git", side_effect=git):
            with self.assertRaisesRegex(proof.SnapshotRefused, "head changed"):
                proof.inspect_snapshot(self.repo)

    def test_current_formatter_value_is_mac_reference_not_linux_identity(self):
        # The contract records a macOS reference, even on this Linux runner.
        tool = proof._json(proof._bound_file(self.repo, proof.TOOLCHAIN))
        self.assertEqual(tool["host"], "aarch64-apple-darwin")
        self.assertEqual(tool["formatter"]["prettier_tree_sha256"], proof.PRETTIER_SHA)
        result = proof.inspect_snapshot(self.repo)
        self.assertEqual(result.formatter_sha256, proof.FORMATTER_SHA)
        self.assertFalse(result.acceptance_granted)

    def test_formatter_formula_rejects_wrong_host_or_inputs(self):
        tool = proof._json(proof._bound_file(self.repo, proof.TOOLCHAIN))
        self.assertEqual(proof._formatter(tool), proof.FORMATTER_SHA)
        for key in ("node", "libnode", "prettier", "host", "id"):
            candidate = copy.deepcopy(tool)
            if key == "node":
                candidate["node"]["sha256"] = "0" * 64
            elif key == "libnode":
                candidate["node"]["libnode"]["sha256"] = "0" * 64
            elif key == "prettier":
                candidate["formatter"]["prettier_tree_sha256"] = "0" * 64
            elif key == "host":
                candidate["host"] = "x86_64-unknown-linux-gnu"
            else:
                candidate["formatter"]["id"] = "other"
            with self.subTest(key=key), self.assertRaises(proof.SnapshotRefused):
                proof._formatter(candidate)


if __name__ == "__main__":
    unittest.main()
