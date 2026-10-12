"""Freeze requirements and rejection cases before any M2 test weights exist."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile

from som_core import composed, flow

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "som-core/data/flow-v7-r10"

CHECKS = {
    "dataflow": ["observe typed outputs", "reject forward, cross-arm and cyclic references"],
    "branches": ["observe both arms and all three nested paths", "accept matching typed joins", "reject leaking results"],
    "transactions": ["observe one session and commit per scope", "observe committed writes before later failure", "reject missing and nested transactions"],
    "database_reverse": ["reverse committed groups in one transaction", "restore only changed fields", "preserve concurrent revisions", "reject absent or wrong inverses"],
    "provider_reverse": ["observe exact S3 versions and GCS generations", "retain preexisting identical objects", "prove lost replies using provider observations", "leave unknown outcomes pending"],
    "recovery": ["interrupt and restart a process", "recover only pending reverse work", "observe one concurrent claimant", "fence an expired epoch", "use a new run for a new forward execution"],
    "identity": ["owner isolation", "editor before effects", "OAuth CSRF", "expiry, revocation and replay"],
    "legacy": ["old 100 tasks", "14 services plus 120 and 80 compositions", "old-format byte identity and rejection classes"],
    "artifact": ["Python/Rust final-byte identity", "strict quality tools", "format idempotence", "12 files, 64 blocks, 65536 bytes, 2048 input/output tokens"],
    "startup": ["missing settings", "provider failure and recovery", "persistent rows after restart", "migration upgrade, downgrade, upgrade"],
    "model": ["Metal test-weight full decode", "preserve every emitted choice", "accept two legal plans", "reject incorrect choices without repair"],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    specification = importlib.util.spec_from_file_location("flow_negative", ROOT / "som-core/tests/test_flow_contract.py")
    tests = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(tests)
    cases = []
    for change, code in tests.REJECTIONS.items():
        prompt = tests.prompt_failure(tests.row(), change)
        if change == "nonfinite":
            prompt["flow"]["steps"][2]["condition"]["right"]["literal"] = "Infinity"
        cases.append({"id": change, "prompt": prompt, "expected_code": code})
    (args.output / "rejections.jsonl").write_text("".join(composed.canonical(case) + "\n" for case in cases))
    (args.output / "requirements.json").write_text(json.dumps({
        "version": "som.flow.acceptance.v1", "checks": CHECKS,
        "behavior_oracle": "public requirement plus database/provider observations; no expected source or unique block sequence",
        "formal_use": "acceptance only; never training, tuning or checkpoint selection",
        "test_weights": "development smoke only; not a correctness or generalization claim",
        "scope": {"database": ["postgresql", "mongodb"], "auth": ["jwt", "firebase", "google_oauth"],
                  "storage": ["none", "s3", "gcs"], "workflow": ["json_export", "gzip_export", "csv", "decimal_summary", "statistics"]},
        "exclusions": ["Experiment", "Observation", "Document", "Review"],
        "limits": {"actions": 8, "branches": 2, "depth": 2, "transactions": 2, "files": 12, "blocks": 64,
                   "bytes": 65536, "input_tokens": 2048, "output_tokens": 2048},
        "passed_required": "all applicable checks; no supported rejection counted as success",
        "pending_later": ["M3 full training", "M5 Claude Experiment gap", "M5 real cloud", "M5 current switch", "M5 token adoption"],
    }, indent=2) + "\n")
    paths = [CORPUS / name for name in ("dev.jsonl", "formal.jsonl", "manifest.json")]
    paths += [flow.CONTRACT_PATH, ROOT / "som-core/src/som_core/flow.py", ROOT / "som-core/src/som_core/flow_codec.py",
              ROOT / "som-core/tests/test_flow_contract.py", Path(__file__).resolve()]
    paths += [ROOT / "som-core/tests/test_flow_provider_boundary.py"]
    paths += [ROOT / "som-rs/contracts/som.prompt.v7.schema.json", ROOT / "som-rs/contracts/som.dsl.v3.schema.json",
              ROOT / "som-rs/src/flow.rs", ROOT / "som-rs/src/flow_codec.rs", ROOT / "som-rs/src/model.rs",
              ROOT / "som-rs/src/bin/som-flow-verify.rs", ROOT / "som-rs/src/lib.rs",
              ROOT / "som-code-python/data/blocks/flow-v7/blocks.json", flow.CATALOG]
    paths += [ROOT / "som-core/scripts" / name for name in (
        "build_flow_blocks.py", "build_flow_schemas.py", "check_flow_schemas.py",
        "check_flow_parity.py", "check_flow_artifacts.py", "check_flow_scalar_parity.py", "probe_flow_budget.py")]
    paths += sorted((ROOT / "som-core/block_templates/flow").glob("*.tpl"))
    paths += [args.output / "rejections.jsonl", args.output / "requirements.json"]
    with tarfile.open(args.output / "specification.tar.gz", "w:gz") as archive:
        for path in paths:
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
    corpus = json.loads((CORPUS / "manifest.json").read_text())
    from som_core import flow_codec
    inputs = {}
    structures = {}
    for split in ("dev", "formal"):
        rows = [json.loads(line) for line in (CORPUS / (split + ".jsonl")).read_text().splitlines()]
        inputs[split] = [hashlib.sha256(flow_codec.features(row["prompt"]).encode()).hexdigest() for row in rows]
        structures[split] = [hashlib.sha256(composed.canonical(flow.semantic_view(row["prompt"])).encode()).hexdigest() for row in rows]
    assert inputs == corpus["actual_model_input_sha256"]
    assert structures == corpus["normalized_input_sha256"]
    assert corpus["block_catalog_sha256"] == hashlib.sha256(flow.CATALOG.read_bytes()).hexdigest()
    assert corpus["block_library_sha256"] == hashlib.sha256((ROOT / "som-code-python/data/blocks/flow-v7/blocks.json").read_bytes()).hexdigest()
    assert not set(inputs["dev"]) & set(inputs["formal"])
    assert not set(structures["dev"]) & set(structures["formal"])
    assert corpus["split_overlap"] == corpus["actual_input_overlap"] == 0
    seal = {"version": "som.flow.acceptance-seal.v1", "frozen_before_test_weights": True,
            "counts": corpus["counts"], "rejections": len(cases), "actual_input_overlap": 0, "structural_overlap": 0,
            "contract_sha256": flow.contract_hash(),
            "files": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
            "archive_sha256": hashlib.sha256((args.output / "specification.tar.gz").read_bytes()).hexdigest()}
    (args.output / "seal.json").write_text(json.dumps(seal, indent=2) + "\n")
    print(json.dumps({key: seal[key] for key in ("counts", "rejections", "archive_sha256")}))


if __name__ == "__main__":
    main()
