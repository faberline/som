"""Probe typed scalar distinctions and finite float serialization."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from som_core import composed, flow, flow_codec
from som_core.v1 import V1Error, load_blocks

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    p = json.loads((ROOT / "som-core/data/flow-v7-r10/dev.jsonl").read_text().splitlines()[0])["prompt"]
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7")
    cases = []
    for value in (0.0, -0.0, 0.1, 0.3, 1e-7, -1e-7, 1e-6, 1e-5, 1e-4, 1.23456789e-5,
                  1.234567891234567e-5, 0.84551240822557006, 2.2250738585072014e-308,
                  1e16, 1e20, 5e-324, 1.7976931348623157e308):
        prompt = deepcopy(p)
        prompt["flow"]["steps"].append({"name": "compare", "op": "if",
                                      "condition": {"op": "eq", "left": {"literal": value, "type": "float"},
                                                    "right": {"literal": 0.0, "type": "float"}},
                                      "then": [], "else": [], "exports": {}})
        cases.append({"id": repr(value), "prompt": prompt, "dsl": flow.reference(prompt, blocks), "action": "resolve"})
    prompt = deepcopy(p)
    prompt["flow"]["result"]["flag"] = {"literal": True, "type": "boolean"}
    dsl = flow.reference(prompt, blocks)
    dsl["flow"]["result"]["flag"]["literal"] = 1
    cases.append({"id": "integer-is-not-boolean", "prompt": prompt, "dsl": dsl, "action": "resolve"})
    for name, expected, actual, kind in (("float-is-not-integer", 1, 1.0, "integer"), ("signed-zero", 0.0, -0.0, "float")):
        prompt = deepcopy(p)
        prompt["flow"]["result"]["value"] = {"literal": expected, "type": kind}
        dsl = flow.reference(prompt, blocks)
        dsl["flow"]["result"]["value"]["literal"] = actual
        cases.append({"id": name, "prompt": prompt, "dsl": dsl, "action": "resolve"})
    sent = "".join(composed.canonical(case) + "\n" for case in cases)
    (args.output / "inputs.jsonl").write_text(sent)
    binary = ROOT / "som-rs/target/flow-m2/debug/som-flow-verify"
    response = subprocess.run([str(binary), str(ROOT / "som-code-python/data/blocks/flow-v7/blocks.json")], input=sent, text=True, capture_output=True, check=True)
    (args.output / "rust.stdout").write_text(response.stdout)
    replies = [json.loads(line) for line in response.stdout.splitlines()]
    records = []
    for case, rust in zip(cases, replies, strict=True):
        try:
            expected = {"status": "ok", "features": flow_codec.features(case["prompt"]),
                        "dsl": case["dsl"], "resolved": flow.resolve(case["prompt"], case["dsl"]),
                        "encoded": flow_codec.encode(case["prompt"], case["dsl"])}
            record = {"id": case["id"], "passed": expected == rust,
                      "different_keys": [key for key in set(expected) | set(rust) if expected.get(key) != rust.get(key)]}
        except V1Error as error:
            record = {"id": case["id"], "passed": rust.get("code") == error.code,
                      "python": error.code, "rust": rust.get("code")}
        records.append(record)
    result = {"stage": "scalar_semantics_and_lowering_only", "product_acceptance": False,
              "cases": len(records), "passed": all(row["passed"] for row in records), "records": records}
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    raise SystemExit(not result["passed"])


if __name__ == "__main__":
    main()
