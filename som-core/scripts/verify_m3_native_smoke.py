"""Run the new native mask on retained M2 weights, without new training."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import time

from som_core import composed, flow_codec, flow_compact
from som_core.flow_m3_evidence import ROOT, digest
from som_core.train_flow import BLOCKS, LEGACY
from som_core.v1 import load_blocks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--assembler", type=Path, required=True)
    parser.add_argument("--model", type=Path,
                        default=ROOT / "som-rs/models/flow-m2-candidate/smoke-r31-001")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    model = args.model.resolve()
    manifest = json.loads((model / "manifest.json").read_text())
    if manifest.get("purpose") != "m2-development-fixture-smoke":
        raise ValueError("pre-training smoke requires seen-fixture test weights")
    config = json.loads((model / "config.json").read_text())
    data = ROOT / "som-core/data/flow-v7-r10/dev.jsonl"
    rows = [json.loads(line) for line in data.read_text().splitlines()]
    if len(rows) != 12:
        raise ValueError("M2 development fixture set changed")
    library = load_blocks(BLOCKS.parent)
    records = []
    for row in rows:
        directory = args.output / row["id"]
        directory.mkdir()
        prompt_path = directory / "prompt.json"
        prompt_path.write_text(composed.canonical(row["prompt"]) + "\n")
        started = time.monotonic()
        result = subprocess.run([str(args.binary.resolve()), "--model", str(model),
                                 "--prompt", str(prompt_path.resolve()), "--constrained"],
                                text=True, capture_output=True, timeout=180, check=False)
        (directory / "response.json").write_text(result.stdout)
        (directory / "native-trace.jsonl").write_text(result.stderr)
        record = {"id": row["id"], "passed": False, "returncode": result.returncode,
                  "seconds": time.monotonic() - started}
        if result.returncode == 0:
            response = json.loads(result.stdout)
            plan = response["dsl"]
            trace = next(json.loads(line) for line in result.stderr.splitlines()
                         if line.startswith("{") and json.loads(line).get("stage") == "flow_decoded_plan")
            if flow_codec.decode(row["prompt"], trace["raw_output"], flow_compact.metadata()) != plan:
                raise ValueError("native output and actual DSL differ")
            files = flow_compact.assemble(row["prompt"], plan, library, LEGACY / "ruff")
            request = composed.canonical({"prompt": row["prompt"], "dsl": plan,
                                          "formatter_model": str(LEGACY)})
            assembled = subprocess.run([str(args.assembler.resolve()), str(BLOCKS)],
                input=request + "\n", text=True, capture_output=True, timeout=180, check=True)
            rust = json.loads(assembled.stdout)
            record["passed"] = rust.get("status") == "ok" and rust.get("files") == files
            record["choices"] = dict(Counter(choice["context"].get("choice", "structure_or_binding") for choice in trace["choices"]))
            record["contract_forced"] = sum(choice["forced"] for choice in trace["choices"])
            record["model_selected"] = len(trace["choices"]) - record["contract_forced"]
            (directory / "files.json").write_text(composed.canonical(rust) + "\n")
        records.append(record)
        print(composed.canonical(record), flush=True)
    report = {"version": "som.flow.m3-native-path-smoke.v1", "required": 12,
              "passed": len(records) == 12 and all(row["passed"] for row in records),
              "model_purpose": "m2-development-fixture-smoke", "m3_accuracy": None,
              "input_policy": config.get("flow_input_policy", "archived legacy input order"),
              "path_probe": manifest.get("path_probe"),
              "weights_created": False, "records": records,
              "source_sha256": {str(path.relative_to(ROOT)): digest(path) for path in
                                (args.binary.resolve(), args.assembler.resolve(), data, model / "manifest.json")}}
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(not report["passed"])


if __name__ == "__main__":
    main()
