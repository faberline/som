"""Compare draft bytes and enforce output rejection; no product pass claim."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from som_core import composed, flow, flow_codec, quality
from som_core.v1 import V1Error, assemble, load_blocks

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    corpus = ROOT / "som-core/data/flow-v7-r10"
    library_path = ROOT / "som-code-python/data/blocks/flow-v7/blocks.json"
    library = load_blocks(library_path.parent)
    binary = ROOT / "som-rs/target/flow-m2/debug/som-flow-verify"
    formatter_model = ROOT / "som-rs/models/composed-quality-candidate"
    cases = []
    for split in ("dev", "formal"):
        for line in (corpus / (split + ".jsonl")).read_text().splitlines():
            row = json.loads(line)
            cases.append({"id": row["id"], "split": split, "prompt": row["prompt"], "dsl": flow.reference(row["prompt"], library)})
    alternate = json.loads((corpus / "dev.jsonl").read_text().splitlines()[0])["prompt"]
    cases.append({"id": "alternate-checked", "split": "alternate", "prompt": alternate, "dsl": flow.reference(alternate, library, "checked")})
    inputs = [{"action": "diagnostic_files", "formatter_model": str(formatter_model), "prompt": row["prompt"], "encoded": flow_codec.encode(row["prompt"], row["dsl"])} for row in cases]
    sent = "".join(composed.canonical(row) + "\n" for row in inputs)
    (args.output / "inputs.jsonl").write_text(sent)
    result = subprocess.run([str(binary), str(library_path)], input=sent, text=True, capture_output=True, check=True)
    (args.output / "rust-diagnostic.jsonl").write_text(result.stdout)
    (args.output / "rust.stderr").write_text(result.stderr)
    rust = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(rust) == len(cases)
    records = []
    normal_inputs = []
    for row, actual in zip(cases, rust, strict=True):
        prompt = row["prompt"]
        draft = {}
        resolved = flow.resolve(prompt, row["dsl"])
        for file in resolved["files"]:
            source = assemble({"version": "som.dsl.v2", "files": [file]}, library)
            for path, text in source.items():
                draft[path] = quality.format_source(path, quality.assemble_imports(text), formatter_model / "ruff", package=prompt["package"]) if path.endswith(".py") else text
        identical = actual.get("status") == "diagnostic" and actual.get("product_acceptance") is False and actual.get("draft_files") == draft
        idempotent = all(not path.endswith(".py") or quality.format_source(path, text, formatter_model / "ruff", package=prompt["package"]) == text for path, text in draft.items())
        entry = {"id": row["id"], "split": row["split"], "draft_byte_parity": identical, "format_idempotent": idempotent,
                 "formatted_bytes": sum(len(text.encode()) for text in draft.values()), "source_sha256": {path: hashlib.sha256(text.encode()).hexdigest() for path, text in draft.items()},
                 "plan_origin": "reference; not model inference", "product_acceptance": False}
        try:
            flow.assemble(prompt, row["dsl"], library, formatter_model / "ruff")
            entry["python_product"] = "accepted"
        except V1Error as error:
            entry["python_product"] = error.code
        normal_inputs.append({"prompt": prompt, "dsl": row["dsl"], "action": "assemble"})
        records.append(entry)
    normal_sent = "".join(composed.canonical(row) + "\n" for row in normal_inputs)
    (args.output / "normal-inputs.jsonl").write_text(normal_sent)
    normal = subprocess.run([str(binary), str(library_path)], input=normal_sent, text=True, capture_output=True, check=True)
    (args.output / "normal-rust.jsonl").write_text(normal.stdout)
    (args.output / "normal-rust.stderr").write_text(normal.stderr)
    replies = [json.loads(line) for line in normal.stdout.splitlines()]
    assert len(replies) == len(records)
    for record, reply in zip(records, replies, strict=True):
        record["rust_product"] = reply.get("code", "accepted")
        record["rejection_parity"] = record["rust_product"] == record["python_product"]
    report = {"stage": "draft_byte_parity_and_budget_rejection", "product_acceptance": False,
              "cases": len(records), "draft_byte_parity_passed": sum(row["draft_byte_parity"] for row in records),
              "format_idempotence_passed": sum(row["format_idempotent"] for row in records),
              "rejection_parity_passed": sum(row["rejection_parity"] for row in records),
              "product_accepted": sum(row["python_product"] == "accepted" for row in records), "records": records,
              "binary_sha256": quality.sha256(binary), "blocks_sha256": quality.sha256(library_path),
              "catalog_sha256": quality.sha256(flow.CATALOG), "contract_sha256": flow.contract_hash(),
              "formatter_sha256": quality.sha256(formatter_model / "ruff"), "inputs_sha256": hashlib.sha256(sent.encode()).hexdigest()}
    report["diagnostic_passed"] = all(row["draft_byte_parity"] and row["format_idempotent"] and row["rejection_parity"] for row in records)
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("cases", "draft_byte_parity_passed", "format_idempotence_passed", "rejection_parity_passed", "product_accepted", "diagnostic_passed")}))
    raise SystemExit(not report["diagnostic_passed"])


if __name__ == "__main__":
    main()
