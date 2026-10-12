"""Measure rules and actual Metal model plans against identical public inputs."""

import argparse
import hashlib
import json
from pathlib import Path
import time

import httpx

from som_core import composed, quality
from som_core.rule_planner import quality_plan
from som_core.v1 import assemble, load_blocks, resolve_prompt_refs

ROOT = Path(__file__).resolve().parents[2]


def sha(value):
    return hashlib.sha256(composed.canonical(value).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    library = load_blocks(ROOT/"som-code-python/data/blocks/composed-quality")
    legacy = load_blocks(ROOT/"som-code-python/data/blocks/composed")
    binary = ROOT/"som-rs/models/composed-quality-candidate/ruff"
    catalog = json.loads(quality.CATALOG.read_text())
    groups = {"services14": "composed/regression.jsonl", "compositions120": "composed/heldout.jsonl",
              "blind80": "composed/blind.jsonl", "legacy100": "service-regression-v4/heldout.jsonl"}
    report = {"version": "som.m1.planner-comparison.v1", "source_sha256": {},
              "results": [], "not_supported": ["runtime_dataflow", "branches", "compensation"],
              "rule_planner_sha256": hashlib.sha256((ROOT/"som-core/src/som_core/rule_planner.py").read_bytes()).hexdigest()}
    with httpx.Client(base_url=args.url, timeout=65) as client:
        report["health"] = client.get("/healthz").json()
        for group, name in groups.items():
            data = ROOT/"som-core/data"/name
            report["source_sha256"][name] = hashlib.sha256(data.read_bytes()).hexdigest()
            for index, line in enumerate(data.read_text().splitlines()):
                row = json.loads(line)
                prompt = row["prompt"]
                start = time.perf_counter()
                rule_dsl = quality_plan(prompt) if group != "legacy100" else row["dsl"]
                rule_files = (quality.reference(prompt, rule_dsl, library, binary) if group != "legacy100"
                              else assemble(resolve_prompt_refs(prompt, rule_dsl), legacy))
                rule_ms = (time.perf_counter()-start)*1000
                trace_start = args.trace.stat().st_size
                start = time.perf_counter()
                response = client.post("/v1/generate", json=prompt)
                model_ms = (time.perf_counter()-start)*1000
                body = response.json()
                with args.trace.open("rb") as source:
                    source.seek(trace_start)
                    logs = [json.loads(line) for line in source.read().decode().splitlines() if line.startswith("{")]
                decoded = [item for item in logs if item.get("stage") == "decoded_plan"]
                model_dsl = composed.decode(decoded[0]["text"]) if group != "legacy100" and len(decoded) == 1 else None
                valid = response.status_code == 200 and body.get("status") == "ok"
                if model_dsl is not None:
                    composed.validate_plan(prompt, model_dsl, catalog)
                for method, output in [("rules", {"status": "ok", "files": rule_files}), ("model", body)]:
                    dest = args.output/method/group/f"{index:04d}.json"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(json.dumps(output)+"\n")
                trace = {"prompt": prompt, "actual_model_input": composed.features(prompt) if group != "legacy100" else prompt,
                         "raw_model_output": decoded, "model_dsl": model_dsl, "rule_dsl": rule_dsl,
                         "request_log": logs, "model_files_sha256": sha(body.get("files", {})),
                         "rule_files_sha256": sha(rule_files)}
                dest = args.output/"traces"/group/f"{index:04d}.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(trace, indent=2)+"\n")
                report["results"].append({"group": group, "index": index, "dsl_valid": valid,
                      "same_final_source": valid and body["files"] == rule_files, "rule_ms": rule_ms,
                      "model_ms": model_ms, "classification": "rules_vs_model" if group != "legacy100" else "compatibility_only"})
            print(json.dumps({"group": group, "count": index+1}), flush=True)
            (args.output/"comparison.json").write_text(json.dumps(report, indent=2)+"\n")
    report["passed"] = all(item["dsl_valid"] and item["same_final_source"] for item in report["results"])
    (args.output/"comparison.json").write_text(json.dumps(report, indent=2)+"\n")
    raise SystemExit(not report["passed"])


if __name__ == "__main__":
    main()
