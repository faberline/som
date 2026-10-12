"""Seal the M3 source, data, decoder rules and pre-training native proofs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tarfile

from audit_m3_flow_data import audit
from som_core import flow_codec, flow_m3_target
from som_core.flow_constraints import policy
from som_core.flow_m3_evidence import (
    ROOT, RUNTIME_EXECUTABLES, SEAL_VERSION, digest, hashes, sources,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--mask-receipt", type=Path, required=True)
    parser.add_argument("--native-receipt", type=Path, required=True)
    parser.add_argument("--input-receipt", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = args.data.resolve()
    before = hashes(data)
    mask = json.loads(args.mask_receipt.read_text())
    native = json.loads(args.native_receipt.read_text())
    input_proof = json.loads(args.input_receipt.read_text())
    runtime = args.runtime.resolve()
    identity = json.loads((runtime / "runtime.json").read_text())
    if (identity.get("purpose") != "m3-development-candidate-runtime"
            or set(identity.get("sha256", {})) != set(RUNTIME_EXECUTABLES)
            or any(digest(runtime / name) != identity["sha256"][name]
                   for name in RUNTIME_EXECUTABLES)):
        raise ValueError("M3 retained runtime inventory changed")
    if (not runtime.is_relative_to(ROOT / 'som-rs/runtime/versions')
            or mask.get('binary_sha256') != digest(runtime / 'som-flow-mask')
            or native.get('source_sha256', {}).get(str((runtime / 'som-flow-model-trace').relative_to(ROOT))) != digest(runtime / 'som-flow-model-trace')
            or native.get('source_sha256', {}).get(str((runtime / 'som-flow-compact-verify').relative_to(ROOT))) != digest(runtime / 'som-flow-compact-verify')
            or mask.get('policy') != policy()):
        raise ValueError('M3 prerequisite proofs do not bind the retained runtime and decode policy')
    if (input_proof.get("version") != "som.flow.m3-new-input-parity.v1"
            or input_proof.get("input_policy_version") != flow_codec.M3_BOUND_INPUT_VERSION
            or input_proof.get("required") != 2700
            or input_proof.get("matched") != 2700
            or input_proof.get("source_unchanged") is not True
            or input_proof.get("source_sha256") != before
            or input_proof.get("dataset_sha256") != digest(data / "manifest.json")
            or input_proof.get("binary_sha256") != digest(runtime / "som-flow-codec")
            or input_proof.get("target_policy") != flow_m3_target.policy()
            or input_proof.get("target_roundtrips") != 2700
            or input_proof.get("target_native_decodes") != 2700
            or input_proof.get("target_mask_matches") != 2700
            or input_proof.get("formal_opened_for_model_evaluation") is not False
            or input_proof.get("optimizer_updates") != 0):
        raise ValueError("M3 needs complete unchanged-source parity for the new input policy")
    for receipt in (args.mask_receipt, args.native_receipt, args.input_receipt):
        if json.loads(receipt.read_text()).get("passed") is not True:
            raise ValueError("M3 needs complete native and mask prerequisites")
    report = audit(data)
    args.output.mkdir(parents=True, exist_ok=False)
    report_path = args.output / "data-audit.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    with tarfile.open(args.output / "source.tar.gz", "w:gz") as archive:
        for path in sources(data):
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
    if hashes(data) != before:
        raise ValueError("M3 source changed while sealing")
    seal = {"version": SEAL_VERSION, "source_unchanged": True,
            "source_sha256": before, "dataset_sha256": digest(data / "manifest.json"),
            "runtime_sha256": {str(path.relative_to(ROOT)): digest(path) for path in
                (runtime / name for name in (*RUNTIME_EXECUTABLES, "runtime.json"))},
            "decode_policy": policy(), "target_policy": flow_m3_target.policy(),
            "training_admission": False,
            "formal_opened_for_model_evaluation": False,
            "evidence_sha256": {str(path.resolve().relative_to(ROOT)): digest(path)
                                for path in (report_path, args.mask_receipt, args.native_receipt,
                                             args.input_receipt)},
            "archive_sha256": digest(args.output / "source.tar.gz")}
    (args.output / "seal.json").write_text(json.dumps(seal, indent=2) + "\n")
    print(json.dumps({"source_unchanged": True, "training_admission": False,
                      "dataset_sha256": seal["dataset_sha256"]}))


if __name__ == "__main__":
    main()
