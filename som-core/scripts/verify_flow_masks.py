"""Compare every Python/Rust legal token set without reading unseen formal data."""

from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

from som_core import composed, flow, flow_codec, flow_compact
from som_core.flow_constraints import State, policy
from som_core.v1 import V1Error, load_blocks

ROOT = Path(__file__).resolve().parents[2]


def tokens(prompt: dict, plan: dict, reverse: bool = False) -> list[int]:
    state = State(prompt)
    result = []

    def emit(value):
        if isinstance(value, dict):
            result.append(2)
            items = sorted(value.items(), reverse=reverse)
            for key, entry in items:
                emit(key)
                emit(entry)
            result.append(3)
        elif isinstance(value, list):
            result.append(4)
            for entry in value:
                emit(entry)
            result.append(5)
        else:
            result.append(state.ids[composed.canonical(value)])

    emit(plan)
    return [*result, 1]


def trace(prompt: dict, selected: list[int]) -> dict:
    state = State(prompt)
    result = []
    for position, token in enumerate(selected):
        allowed = state.allowed()
        if token not in allowed:
            return {"trace": result, "code": "INVALID_DSL", "position": position,
                    "allowed": allowed}
        result.append({"allowed": allowed})
        state.advance(token)
    return {"trace": result, "allowed": state.allowed(),
            "complete": state.complete, "root": state.root}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--data", type=Path, action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = args.data or [ROOT / "som-core/data/flow-v7-r10" / (name + ".jsonl")
                         for name in ("dev", "formal")]
    data = [path.resolve() for path in data]
    for path in data:
        if "flow-m3" in str(path) and path.name == "formal.jsonl":
            raise ValueError("pre-training parity cannot inspect M3 formal data")
    rows = [json.loads(line) for path in data for line in path.read_text().splitlines()]
    library = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    cases = []
    for row in rows:
        plan = row.get("dsl") or flow_compact.reference(row["prompt"], library)
        cases.append((row["id"], row["prompt"], tokens(row["prompt"], plan), True))
    first = rows[0]
    for variant in ("standard", "checked"):
        plan = flow_compact.reference(first["prompt"], library, variant)
        cases.append(("alternative-" + variant, first["prompt"],
                      tokens(first["prompt"], plan, reverse=True), True))
    plan = flow_compact.reference(first["prompt"], library)
    bad = deepcopy(plan)
    bad["flow"]["steps"] = bad["flow"]["steps"][:1]
    cases.append(("missing-required-operations", first["prompt"],
                  tokens(first["prompt"], bad), False))
    bad = deepcopy(plan)
    bad["flow"]["steps"] = bad["flow"]["steps"][:1]
    bad["flow"]["result"] = {}
    try:
        flow.validate_plan(first["prompt"], bad, flow_compact.metadata())
    except V1Error:
        pass
    else:
        raise ValueError("business-wrong plan was accepted by final requirements")
    cases.append(("business-wrong-valid-prefix-no-repair", first["prompt"],
                  tokens(first["prompt"], bad), True))
    # Mutations are sent through the token grammar. They cannot use an answer
    # comparison to repair an invalid block, reference, inverse or scope.
    def walk(nodes):
        for node in nodes:
            yield node
            for key in ("steps", "then", "else"):
                yield from walk(node.get(key, []))
    mutation_row = next(row for row in rows if any(
        node.get("undo") for node in walk(
            (row.get("dsl") or flow_compact.reference(row["prompt"], library))["flow"]["steps"])))
    mutation_plan = mutation_row.get("dsl") or flow_compact.reference(mutation_row["prompt"], library)
    for mutation in ("missing-permission", "missing-undo", "wrong-capability",
                     "forward-reference", "wrong-reference-type"):
        bad = deepcopy(mutation_plan)
        nodes = list(walk(bad["flow"]["steps"]))
        if mutation == "missing-permission":
            bad["flow"]["steps"] = bad["flow"]["steps"][1:]
        elif mutation == "missing-undo":
            next(node for node in nodes if node.get("undo"))["undo"] = None
        elif mutation == "wrong-capability":
            catalog = flow_compact.metadata()["flow_blocks"]
            node = next(node for node in nodes if catalog[node["id"]]["operation"] == "authorize")
            node["id"] = next(identity for identity, entry in catalog.items()
                              if entry.get("operation") == "authorize" and identity != node["id"])
        else:
            node = next(node for node in nodes if any("input" in ref for ref in node.get("args", {}).values()))
            key = next(key for key, ref in node["args"].items() if "input" in ref)
            node["args"][key] = {"step": nodes[-1]["name"], "field": ""} if mutation == "forward-reference" else {"literal": True, "type": "boolean"}
        cases.append((mutation, mutation_row["prompt"], tokens(mutation_row["prompt"], bad), False))
    args.output.mkdir(parents=True, exist_ok=False)
    process = subprocess.Popen([str(args.binary.resolve())], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    records = []
    try:
        with gzip.open(args.output / "allowed-sets.jsonl.gz", "wt") as evidence:
            for identity, prompt, selected, valid in cases:
                request = composed.canonical({"prompt": prompt, "tokens": selected})
                process.stdin.write(request + "\n")
                process.stdin.flush()
                rust = json.loads(process.stdout.readline())
                python = trace(prompt, selected)
                compared = deepcopy(rust)
                for step in compared.get("trace", []):
                    step.pop("context", None)
                passed = compared == python and (rust.get("complete") is True if valid else rust.get("code") == "INVALID_DSL")
                item = {"id": identity, "valid": valid, "passed": passed,
                        "prefixes": len(python["trace"]), "python": python, "rust": rust}
                evidence.write(composed.canonical(item) + "\n")
                records.append({key: item[key] for key in ("id", "valid", "passed", "prefixes")})
                if not passed:
                    (args.output / "first-difference.json").write_text(json.dumps(item, indent=2) + "\n")
                    break
                if len(records) % 25 == 0:
                    print(composed.canonical({"stage": "mask-parity", "cases": len(records)}), flush=True)
    finally:
        process.stdin.close()
        process.wait(timeout=10)
    report = {"version": "som.flow.mask-parity.v1", "required": len(cases),
              "passed": len(records) == len(cases) and all(row["passed"] for row in records),
              "records": records, "policy": policy(),
              "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
              "data_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in data}}
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(composed.canonical({"required": len(cases), "attempted": len(records), "passed": report["passed"]}))
    raise SystemExit(not report["passed"])


if __name__ == "__main__":
    main()
