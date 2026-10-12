"""Create a fresh M3 dataset seal. This is not training admission."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from itertools import product
import json
from pathlib import Path

from som_core import composed, flow_codec, flow_compact
from som_core.flow_m3_data import COUNTS, make_rows

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if not destination.is_relative_to(ROOT / "som-core/data") or destination.exists():
        raise ValueError("use a new repository dataset directory")
    caps = list(product(("postgresql", "mongodb"), ("jwt", "firebase", "google_oauth"),
                        ("none", "s3", "gcs"),
                        ("json_export", "gzip_export", "csv", "decimal_summary", "statistics")))
    source_files = [ROOT / "som-core/src/som_core/flow_m3_data.py", Path(__file__),
                    ROOT / "som-core/src/som_core/flow_codec.py", ROOT / "som-core/src/som_core/flow.py", flow_compact.CATALOG,
                    ROOT / "som-rs/contracts/m3-input-bindings.v3.json"]
    source_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in source_files}
    rows, audit = make_rows(caps)
    if source_hashes != {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in source_files}:
        raise ValueError("dataset source changed during generation")
    destination.mkdir(parents=True)
    files = {}
    counts = {}
    for split, cases in rows.items():
        path = destination / (split + ".jsonl")
        path.write_text("".join(composed.canonical(row) + "\n" for row in cases))
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        atoms = Counter()
        def walk(nodes):
            for node in nodes:
                atoms[node["op"]] += 1
                if node["op"] == "transaction": walk(node["steps"])
                elif node["op"] == "if": walk(node["then"]); walk(node["else"])
        for row in cases: walk(row["prompt"]["flow"]["steps"])
        counts[split] = {"examples": len(cases), "apps": dict(Counter(row["app"] for row in cases)),
                        "operation_nodes": sum(atoms.values()), "atoms": dict(atoms),
                        "unique": {key: len({row["fingerprints"][key] for row in cases}) for key in ("input", "structure", "graph")},
                        "capability_tuples": len({tuple(row["prompt"]["capabilities"][key] for key in ("database", "auth", "storage", "workflow")) for row in cases}),
                        "max_input_tokens": max(row["input_tokens"] for row in cases),
                        "max_output_tokens": max(row["output_tokens"] for row in cases)}
    manifest = {"version": "som.flow.m3-dataset.v1", "seed": 17, "counts": COUNTS,
                "statistics": counts, "files": files, "audit": audit,
                "cross_split_overlap": {key: 0 for key in ("input", "structure", "graph")},
                "training_exclusions": ["Experiment", "Observation", "Document", "Review"],
                "codec_version": flow_codec.COMPACT_VERSION,
                "input_policy_version": flow_codec.M3_BOUND_INPUT_VERSION,
                "source_sha256": source_hashes,
                "frozen_before_training": True, "training_admission": False,
                "formal_use": "only after earliest perfect development checkpoint is fixed"}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
