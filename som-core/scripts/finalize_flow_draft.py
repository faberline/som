"""Seal partial M2 evidence without treating diagnostic passes as acceptance."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "som-rs/evals/m2-flow-2026-10-02/r1"
M1 = ROOT / "som-rs/evals/m1-closeout-2026-10-02/r1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    baseline = read(M1 / "baseline/baseline.json")
    protected = {name: value for name, value in baseline["files"].items()
                 if name.startswith(("som-rs/models/", "som-code-python/data/blocks/",
                                     "som-code-python/data/block-contracts/composed", "som-rs/contracts/python-quality-v1/",
                                     "som-rs/evals/direct-baselines/", "som-rs/evals/fixed-baseline-som-"))}
    changed = [name for name, value in protected.items() if digest(ROOT / name) != value]
    assert not changed, changed
    defaults = {name: str((ROOT / name).resolve()) for name in (
        "som-rs/models/current", "som-code-python/data/blocks/current")}
    assert defaults["som-rs/models/current"] == baseline["default_model"]
    assert defaults["som-code-python/data/blocks/current"] == baseline["default_blocks"]
    legacy = read(EVAL / "legacy-new-binary-002/comparison.json")
    assert legacy["passed"] and len(legacy["results"]) == 314
    equal_old = 0
    for row in legacy["results"]:
        name = Path(row["group"]) / f'{row["index"]:04d}.json'
        assert read(EVAL / "legacy-new-binary-002/model" / name) == read(M1 / "comparison-002/model" / name)
        equal_old += 1
    semantics = read(EVAL / "semantic-parity-008/result.json")
    artifacts = read(EVAL / "artifact-parity-005/result.json")
    schemas = read(EVAL / "schemas-004/result.json")
    assert semantics["passed"] and artifacts["diagnostic_passed"] and schemas["passed"]
    assert artifacts["blocks_sha256"] == digest(ROOT / "som-code-python/data/blocks/flow-v7/blocks.json")
    assert artifacts["catalog_sha256"] == digest(ROOT / "som-code-python/data/block-contracts/flow-v7.json")
    formal = [row for row in artifacts["records"] if row["split"] == "formal"]
    assert len(formal) == 114
    assert all(row["python_product"] == row["rust_product"] == "SCOPE_TOO_LARGE" for row in formal)
    quality_paths = [EVAL / name / "result.json" for name in (
        "quality-diagnostic-postgresql-005", "quality-diagnostic-mongodb-006", "quality-diagnostic-gcs-002")]
    assert all(read(path)["passed"] for path in quality_paths)
    seal_path = ROOT / "som-core/data/flow-v7-r10/acceptance-seal-002/seal.json"
    seal = read(seal_path)
    assert seal["frozen_before_test_weights"] and seal["counts"] == {"dev": 12, "formal": 114, "capability_tuples": 90, "semantic_cases": 24}
    assert seal["actual_input_overlap"] == seal["structural_overlap"] == 0
    for name, value in seal["files"].items():
        assert digest(ROOT / name) == value, name
    assert "90 passed" in (EVAL / "contract-tests-012.log").read_text()
    rust_log = (EVAL / "rust-tests-004.log").read_text()
    rust_counts = [tuple(map(int, match)) for match in re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored", rust_log)]
    assert rust_counts and sum(row[1] for row in rust_counts) == 0
    gate_path = EVAL / "generation-gate-002/result.json"
    gate = read(gate_path)
    assert gate["passed"] and gate["binary_sha256"] == digest(ROOT / "som-rs/target/flow-m2/debug/som")
    scalar_path = EVAL / "scalar-parity-004/result.json"
    scalar = read(scalar_path)
    assert scalar["passed"] and scalar["cases"] == 20
    paths = [ROOT / name for name in seal["files"]]
    paths += [ROOT / name for name in (
        "som-rs/src/main.rs", "som-rs/Cargo.toml", "som-rs/Cargo.lock",
        "som-rs/src/quality.rs", "som-core/src/som_core/composed.py", "som-core/src/som_core/quality.py",
        "som-rs/target/flow-m2/debug/som", "som-rs/target/flow-m2/debug/som-flow-verify",
        "som-core/scripts/freeze_flow_cases.py", "som-core/scripts/check_flow_generation_gate.py")]
    paths.append(Path(__file__).resolve())
    with tarfile.open(args.output / "source-supplement.tar.gz", "w:gz") as archive:
        for path in sorted(set(paths)):
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
    (args.output / "tracked-work.patch").write_bytes(subprocess.check_output(["git", "diff", "--binary"], cwd=ROOT))
    (args.output / "git-status.txt").write_bytes(subprocess.check_output(["git", "status", "--short"], cwd=ROOT))
    receipts = [EVAL / name for name in (
        "legacy-new-binary-002/comparison.json", "semantic-parity-008/result.json",
        "artifact-parity-005/result.json", "schemas-004/result.json", "contract-tests-012.log",
        "provider-boundary-001.log", "rust-tests-004.log", "budget-010/result.json")]
    receipts += quality_paths + [seal_path, gate_path, scalar_path]
    maximum = read(EVAL / "budget-010/result.json")["records"][:2]
    # The public prompt cases are unchanged. Only their codec binding ledger
    # changed when the separate v7 database block was added.
    unchanged_rows = {split: (ROOT / "som-core/data/flow-v7-r9" / (split + ".jsonl")).read_bytes()
                     == (ROOT / "som-core/data/flow-v7-r10" / (split + ".jsonl")).read_bytes()
                     for split in ("dev", "formal")}
    assert all(unchanged_rows.values())
    result = {
        "version": "som.m2.draft-snapshot.v1", "m1_passed": True, "m2_passed": False,
        "stage": "output_budget_failed; candidate generation remains gated",
        "legacy_new_binary": {"cases": 314, "same_archived_final_source": equal_old,
                              "metal_output_mode": "archived v6 plus legacy; not new flow_plan",
                              "behavior_receipts": "unchanged bytes reuse the independently verified M1 behavior; no new M2 runtime proof"},
        "semantic_parity": {key: semantics[key] for key in ("cases", "valid", "rejected", "passed")},
        "draft_parity": {key: artifacts[key] for key in ("cases", "draft_byte_parity_passed", "format_idempotence_passed", "rejection_parity_passed", "product_accepted")},
        "structural_schema_cases": schemas["cases"], "python_tests_passed": 90,
        "scalar_parity_cases": scalar["cases"],
        "rust_tests_passed": sum(row[0] for row in rust_counts), "rust_tests_ignored": sum(row[2] for row in rust_counts),
        "quality_diagnostic_samples_passed": 3, "quality_full_formal_matrix_run": False,
        "formal_artifact_budget": {"cases": 114, "accepted": 0, "rejected": 114,
                                  "rejection_code": "SCOPE_TOO_LARGE", "limit_bytes": 65536,
                                  "min_bytes": min(row["formatted_bytes"] for row in formal),
                                  "max_bytes": max(row["formatted_bytes"] for row in formal)},
        "maximum_field_probes": [{key: row[key] for key in ("id", "files", "blocks", "formatted_bytes", "input_tokens", "output_tokens", "ruff_violations", "budget_passed")} for row in maximum],
        "defaults": defaults, "protected_assets": {"count": len(protected), "changed": changed},
        "protected_asset_sha256": protected, "source_sha256": {str(path.relative_to(ROOT)): digest(path) for path in paths},
        "receipt_sha256": {str(path.relative_to(ROOT)): digest(path) for path in receipts},
        "source_archive_sha256": digest(args.output / "source-supplement.tar.gz"),
        "development_and_formal_overlap": 0, "test_weights_created": False, "new_flow_metal_acceptance": False,
        "public_cases_same_as_r9": unchanged_rows,
        "new_flow_behavior_matrix_run": False, "paid_agent_runs": 0,
        "observations": {"new_flow_model_directory_bytes": None, "new_flow_resident_memory_bytes": None,
                         "new_flow_generation_ms": None, "reason": "no accepted flow candidate or new weights"},
        "required_next": ["shrink shared runtime without relaxing fixed budgets",
                          "recheck full final artifact quality and valid-byte/rejection parity",
                          "development-only test weights and Metal end-to-end proof",
                          "independent local full-flow database/provider/recovery/migration matrix"],
        "pending_m5": ["Claude Experiment comparison gap", "real cloud", "token adoption", "current switch"],
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("m1_passed", "m2_passed", "stage", "formal_artifact_budget", "source_archive_sha256")}))


if __name__ == "__main__":
    main()
