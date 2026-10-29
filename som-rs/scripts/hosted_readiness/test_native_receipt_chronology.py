"""Exercise complete prospective receipt checks. Run only on GitHub runners."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

if os.environ.get("GITHUB_ACTIONS") != "true":
    raise RuntimeError("GitHub tests only")

REPO = Path(__file__).resolve().parents[3]
ORDER = {
    "version": "som.native-acceptance-chronology.v1",
    "implementation_already_existed": True,
    "prior_work_relabelled_as_preimplementation": False,
    "scope": "native_tools_and_metadata_only",
    "acceptance_rule": "prospective_full_same_candidate_gates",
    "historical_receipt_bytes_changed": False,
    "historical_results_promoted_to_current_acceptance": False,
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def raw_json(value):
    return (json.dumps(value, indent=2) + "\n").encode()


class Report:
    def __init__(self):
        self.failed = []

    def check(self, name, passed, detail=""):
        if not passed:
            self.failed.append(name + ": " + detail)


class NativeReceiptChronologyTests(unittest.TestCase):
    def fixture(self, target):
        number = 8 if target == "rust" else 4
        previous_name = f"som-rs/evidence/{target}-output-contract-amendment-{number - 1:03d}.json"
        previous_raw = (REPO / previous_name).read_bytes()
        previous = json.loads(previous_raw)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        checker = f"som-rs/tests/{target}-output-v1/check_contract.py"
        archive = f"som-rs/evidence/{target}-output-contract-archive/check_contract-before-amendment-{number:03d}.py"
        for path, expected in previous["files"].items():
            body = (REPO / (archive if path == checker else path)).read_bytes()
            self.assertEqual(sha(body), expected)
            item = root / path
            item.parent.mkdir(parents=True, exist_ok=True)
            item.write_bytes(body)
        previous_path = root / previous_name
        previous_path.parent.mkdir(parents=True, exist_ok=True)
        previous_path.write_bytes(previous_raw)
        archived = root / archive
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_bytes((REPO / archive).read_bytes())
        # Use the actual reviewed source and added policy bytes, not matching stubs.
        (root / checker).write_bytes((REPO / checker).read_bytes())
        policy = f"som-rs/contracts/{target}-output-v1/native-tools-compatibility.json"
        (root / policy).write_bytes((REPO / policy).read_bytes())
        files = dict(previous["files"])
        files[checker] = sha((root / checker).read_bytes())
        files[policy] = sha((root / policy).read_bytes())
        receipt = {
            "version": f"som.{target}-output-contract-amendment.v1",
            "amendment": number,
            "status": "amendment_sealed_after_independent_review",
            "implementation_already_in_progress": True,
            "implementation_order": copy.deepcopy(ORDER),
            "previous_receipt": previous_name,
            "previous_receipt_sha256": sha(previous_raw),
            "changed_paths": [checker],
            "added_paths": [policy],
            "changes": [{"path": checker, "old_sha256": previous["files"][checker],
                         "new_sha256": files[checker], "archived_copy": archive,
                         "archived_copy_sha256": previous["files"][checker]}],
            "target_contract": {"sha256": sha(canonical(files).encode()),
                                "previous_sha256": previous["target_contract"]["sha256"]},
            "files": files,
            "independent_review": {"final_verdict": "APPROVED",
                                   "scope": "native_policy_validator_and_exact_qa_source_maps",
                                   "receipt_files_sha256": sha(canonical(files).encode())},
            "acceptance": {"status": "not_run", "product_acceptance_passed": False},
        }
        # Load only these actual validator definitions. No checker main or imports run.
        source = (REPO / checker).read_bytes()
        tree = ast.parse(source)
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name in {"native_acceptance_order", "check_receipt"}]
        self.assertEqual({node.name for node in selected}, {"native_acceptance_order", "check_receipt"})
        import pathlib
        scope = {"__name__": "synthetic_native_receipt_subject", "pathlib": pathlib,
                 "hashlib": hashlib, "json": json, "Report": Report, "REPO": root,
                 "CONTRACT": root / f"som-rs/contracts/{target}-output-v1",
                 "HERE": root / f"som-rs/tests/{target}-output-v1",
                 "EVIDENCE": root / f"som-rs/evidence/{target}-output-v1",
                 "load": lambda path: json.loads(path.read_bytes()),
                 "sha": lambda path: sha(path.read_bytes()), "canonical": canonical,
                 "h": lambda value: sha(canonical(value).encode())}
        scope["contract_files"] = lambda: sorted({p for name in ("CONTRACT", "HERE", "EVIDENCE")
                                                  for p in scope[name].rglob("*") if p.is_file()})
        future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
        module = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
        exec(compile(module, str(REPO / checker), "exec", dont_inherit=True), scope)
        receipt_path = root / f"{target}-output-contract-amendment-{number:03d}.json"
        return root, receipt, scope["check_receipt"], receipt_path

    def run_receipt(self, fixture, change=None):
        root, receipt, check, path = fixture
        if change:
            change(root, receipt)
        path.write_bytes(raw_json(receipt))
        report = Report()
        check(report, path)
        return report.failed

    def each(self, change=None, accepted=False):
        for target in ("rust", "typescript"):
            with self.subTest(target=target):
                failures = self.run_receipt(self.fixture(target), change)
                if accepted:
                    self.assertEqual(failures, [])
                else:
                    self.assertTrue(failures)

    def test_truthful_reviewed_native_receipt_passes_full_map_and_chain(self):
        self.each(accepted=True)

    def test_native_implementation_cannot_be_relabelled_before_start(self):
        self.each(lambda _, r: r.update(implementation_already_in_progress=False))

    def test_native_receipt_cannot_claim_an_old_amendment_number(self):
        self.each(lambda _, r: r.update(amendment=1, implementation_already_in_progress=False))

    def test_native_receipt_cannot_omit_amendment_to_claim_initial_freeze(self):
        def change(_, receipt):
            receipt.pop("amendment")
            receipt.update(status="contract_frozen_before_implementation",
                           implementation_started_before_freeze=False)
        self.each(change)

    def test_old_implementation_cannot_be_relabelled_as_preimplementation(self):
        self.each(lambda _, r: r["implementation_order"].update(prior_work_relabelled_as_preimplementation=True))

    def test_existing_implementation_must_be_explicit(self):
        self.each(lambda _, r: r["implementation_order"].update(implementation_already_existed=False))

    def test_integer_cannot_stand_in_for_chronology_boolean(self):
        self.each(lambda _, r: r["implementation_order"].update(implementation_already_existed=1))

    def test_historical_results_cannot_be_promoted(self):
        self.each(lambda _, r: r["implementation_order"].update(historical_results_promoted_to_current_acceptance=True))

    def test_historical_receipt_changes_are_not_approved(self):
        self.each(lambda _, r: r["implementation_order"].update(historical_receipt_bytes_changed=True))

    def test_unreviewed_draft_is_refused(self):
        self.each(lambda _, r: r.update(status="draft_not_sealed_not_active"))

    def test_pending_independent_review_is_refused(self):
        self.each(lambda _, r: r["independent_review"].update(final_verdict="PENDING"))

    def test_review_of_other_policy_bytes_is_refused(self):
        self.each(lambda _, r: r["independent_review"].update(receipt_files_sha256="0" * 64))

    def test_product_acceptance_claim_does_not_come_from_policy(self):
        self.each(lambda _, r: r["acceptance"].update(product_acceptance_passed=True))

    def test_false_previous_receipt_hash_is_refused(self):
        self.each(lambda _, r: r.update(previous_receipt_sha256="0" * 64))

    def test_false_target_map_hash_is_refused(self):
        self.each(lambda _, r: r["target_contract"].update(sha256="0" * 64))

    def test_unlisted_added_file_is_refused(self):
        self.each(lambda root, r: (root / r["added_paths"][0]).with_name("unlisted.txt").write_bytes(b"unexpected"))

    def test_archived_old_validator_change_is_refused(self):
        self.each(lambda root, r: (root / r["changes"][0]["archived_copy"]).write_bytes(b"not the old validator"))

    def test_frozen_semantic_file_change_is_refused(self):
        def change(root, receipt):
            item = next(path for path in receipt["files"] if path.endswith("/CONTRACT.md"))
            (root / item).write_bytes(b"changed semantics")
        self.each(change)


if __name__ == "__main__":
    unittest.main()
