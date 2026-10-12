"""Seal the compact source and input ledger without admitting training.

This snapshot can retain a failed candidate. It cannot create weights or
turn diagnostic drafts into accepted products.
"""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile

from som_core import composed, flow, flow_codec, flow_compact, quality
from som_core.v1 import load_blocks
from som_core.train_flow import BUDGET_POLICY, LIMITS

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "som-core/data/flow-v7-r10"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def sources():
    paths = set((ROOT / "som-core/src/som_core").glob("*.py"))
    paths.update((ROOT / "som-core/scripts").glob("*.py"))
    paths.update((ROOT / "som-core/tests").glob("test_*flow*.py"))
    paths.update((ROOT / "som-core/tests").glob("test_fixture*.py"))
    paths.update((ROOT / "som-core/block_templates/flow_compact").glob("*.tpl"))
    paths.update((ROOT / "som-rs/src").rglob("*.rs"))
    paths.update(path for path in (ROOT / "som-rs/contracts").rglob("*") if path.is_file())
    paths.update(CORPUS / name for name in ("dev.jsonl", "formal.jsonl", "manifest.json"))
    paths.update(CORPUS / 'acceptance-seal-002' / name for name in
                 ('requirements.json', 'rejections.jsonl', 'seal.json'))
    paths.update({flow_compact.CATALOG,
                  ROOT / "som-code-python/data/blocks/flow-v7-compact/blocks.json",
                  ROOT / "som-rs/Cargo.toml", ROOT / "som-rs/Cargo.lock"})
    model = ROOT / "som-rs/models/composed-quality-candidate"
    paths.update(model / name for name in ("manifest.json", "ruff", "quality.json", "pyproject.toml"))
    return sorted(paths)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    library = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    catalog = flow_compact.metadata()
    paths = sources()
    before = {str(path.relative_to(ROOT)): quality.sha256(path) for path in paths}
    sets = {}
    for split, expected in (("dev", 12), ("formal", 114)):
        rows = [json.loads(line) for line in (CORPUS / (split + ".jsonl")).read_text().splitlines()]
        if len(rows) != expected:
            raise ValueError("sealed corpus count differs")
        records = []
        for row in rows:
            prompt = row["prompt"]
            actual = flow_codec.features(prompt, catalog)
            plan = flow_compact.reference(prompt, library)
            output = flow_codec.encode(prompt, plan, catalog)
            inputs = actual.count("<")
            outputs = output.count("<")
            if max(inputs, outputs) > 2048:
                raise ValueError("model token budget exceeded")
            records.append({"id": row["id"], "prompt_sha256": digest(composed.canonical(prompt).encode()),
                            "actual_model_input": actual, "input_sha256": digest(actual.encode()),
                            "reference_output": output, "input_tokens": inputs, "output_tokens": outputs,
                            "structure_sha256": digest(composed.canonical(flow.semantic_view(prompt)).encode())})
        (args.output / (split + "-inputs.jsonl")).write_text(
            "".join(composed.canonical(record) + "\n" for record in records))
        sets[split] = records
    for key in ("input_sha256", "structure_sha256"):
        groups = {split: {row[key] for row in rows} for split, rows in sets.items()}
        if any(len(groups[split]) != len(sets[split]) for split in sets):
            raise ValueError("duplicate fixture input or structure")
        if groups["dev"] & groups["formal"]:
            raise ValueError("development and formal fixtures overlap")
    with tarfile.open(args.output / "sources.tar.gz", "w:gz") as archive:
        for path in paths:
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
    after = {str(path.relative_to(ROOT)): quality.sha256(path) for path in paths}
    if after != before:
        raise ValueError("candidate source changed during the snapshot")
    seal = {"version": "som.flow.compact-source-seal.v1", "training_admission": False,
            "reason": "source freeze only; scope, quality and full behavior require acceptance",
            "counts": {key: len(rows) for key, rows in sets.items()},
            "actual_input_overlap": 0, "structural_overlap": 0,
            "limits": LIMITS,
            "output_budget_sha256": quality.sha256(BUDGET_POLICY),
            "codec_version": flow_codec.COMPACT_VERSION, "source_sha256": before,
            "source_unchanged": True, "archive_sha256": quality.sha256(args.output / "sources.tar.gz"),
            "input_ledger_sha256": {split: quality.sha256(args.output / (split + "-inputs.jsonl")) for split in sets},
            "formal_use": "block and service acceptance only; never training or checkpoint selection",
            "smoke_scope": "twelve development fixtures only; not generalization",
            "pending": ["output budgets", "full fault matrix", "Metal flow weights and HTTP/CLI", "M2 closure"]}
    (args.output / "seal.json").write_text(json.dumps(seal, indent=2) + "\n")
    print(json.dumps({key: seal[key] for key in ("counts", "training_admission", "source_unchanged", "archive_sha256")}))


if __name__ == "__main__":
    main()
