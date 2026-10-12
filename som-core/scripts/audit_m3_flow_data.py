"""Recompute the M3 split audit. This checks structure, never model accuracy."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path

from som_core import composed, flow, flow_codec, flow_compact
from som_core.flow_m3_data import COUNTS, composition_view, fingerprints, sha
from som_core.flow_m3_evidence import ROOT, digest


def audit(dataset: Path) -> dict:
    manifest = json.loads((dataset / "manifest.json").read_text())
    if (manifest.get("version") != "som.flow.m3-dataset.v1" or manifest.get("counts") != COUNTS
            or manifest.get("input_policy_version") not in {
                flow_codec.M3_INPUT_VERSION, flow_codec.M3_SEMANTIC_INPUT_VERSION, flow_codec.M3_BOUND_INPUT_VERSION}):
        raise ValueError("M3 dataset scope differs")
    input_policy = manifest["input_policy_version"]
    marks = {}
    statistics = {}
    for split, count in COUNTS.items():
        path = dataset / (split + ".jsonl")
        if digest(path) != manifest["files"].get(path.name):
            raise ValueError("sealed split bytes changed")
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if len(rows) != count or len({row["id"] for row in rows}) != count:
            raise ValueError("M3 split has wrong or duplicate cases")
        marks[split] = {key: set() for key in ("input", "structure", "graph")}
        capability_flows = defaultdict(set)
        capability_counts = Counter()
        apps = Counter()
        nodes = Counter()
        for row in rows:
            prompt = row["prompt"]
            current = fingerprints(prompt, input_policy=input_policy)
            if current != row["fingerprints"]:
                raise ValueError("actual input or composition fingerprint changed")
            for key, value in current.items():
                marks[split][key].add(value)
            if split != "formal" and row["app"] in {"experiment", "documents"}:
                raise ValueError("held-out App entered train or development")
            flow.validate_plan(prompt, row["dsl"], flow_compact.metadata())
            flow_codec.encode(prompt, row["dsl"], flow_compact.metadata())
            cap = tuple(prompt["capabilities"][key] for key in ("database", "auth", "storage", "workflow"))
            # API order, query options and resource renaming cannot supply the
            # required twenty different flow structures for one capability.
            normalized = composition_view(prompt)["flow"]
            capability_flows[cap].add(sha(composed.canonical(normalized)))
            capability_counts[cap] += 1
            apps[row["app"]] += 1
            def walk(items):
                for node in items:
                    nodes[node["op"]] += 1
                    for key in ("steps", "then", "else"):
                        walk(node.get(key, []))
            walk(prompt["flow"]["steps"])
        minimum = 20 if split == "train" else 3
        if (len(capability_counts) != 90 or min(capability_counts.values()) < minimum
                or split == "train" and min(map(len, capability_flows.values())) < 20
                or any(len(values) != count for values in marks[split].values())):
            raise ValueError("M3 lacks distinct compositions or capability coverage")
        statistics[split] = {"cases": count, "apps": dict(apps), "atoms": dict(nodes),
            "unique": {key: len(values) for key, values in marks[split].items()},
            "capability_tuples": len(capability_counts),
            "min_capability_cases": min(capability_counts.values()),
            "min_distinct_flow_structures": min(map(len, capability_flows.values()))}
    overlap = {key: sum(len(marks[left][key] & marks[right][key])
                        for left, right in (("train", "dev"), ("train", "formal"), ("dev", "formal")))
               for key in ("input", "structure", "graph")}
    if any(overlap.values()):
        raise ValueError("complete inputs or compositions overlap between splits")
    # Inspect historical requirements only for exclusion. None is a model
    # accuracy answer. The current dataset must not exclude itself.
    previous = [path for prefix in ("flow-v7*", "flow-m3-*")
                for directory in (ROOT / "som-core/data").glob(prefix)
                if directory.resolve() != dataset
                for path in directory.glob("*.jsonl")]
    old_prompts = []
    for path in previous:
        old_prompts.extend(json.loads(line)["prompt"] for line in path.read_text().splitlines())
    for directory in ("training-prerequisite-001", "supplementary-acceptance-003"):
        for path in (ROOT / "som-rs/evals/m2-flow-2026-10-04/r31" / directory).glob("*/prompt.json"):
            value = json.loads(path.read_text())
            old_prompts.append(value.get("prompt", value))
    known = {key: set() for key in overlap}
    for original in old_prompts:
        prompt = deepcopy(original)
        prompt["contract"] = {"id": flow.CONTRACT, "sha256": flow.contract_hash()}
        for key, value in fingerprints(prompt, input_policy=input_policy).items(): known[key].add(value)
    seen_overlap = {key: len(known[key] & marks["formal"][key]) for key in overlap}
    if any(seen_overlap.values()):
        raise ValueError("new formal compositions repeat a historical requirement")
    return {"version": "som.flow.m3-data-audit.v1", "passed": True,
            "dataset_sha256": digest(dataset / "manifest.json"),
            "statistics": statistics, "overlap": overlap,
            "previously_seen_overlap": seen_overlap,
            "historical_data_sha256": {str(path.relative_to(ROOT)): digest(path) for path in previous},
            "formal_used_for_accuracy": False, "checkpoint_selected": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.data.resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(composed.canonical(report))


if __name__ == "__main__":
    main()
