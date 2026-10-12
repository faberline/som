"""Close out local M1 evidence without changing models or prior receipts."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "som-rs/evals/m1-closeout-2026-10-02/r1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    destination = EVAL / "closeout-002"
    destination.mkdir(exist_ok=False)
    baseline = read(EVAL / "baseline/baseline.json")
    assert digest(EVAL / "baseline/sources.tar.gz") == baseline["archive_sha256"]
    comparison = read(EVAL / "comparison-002/comparison.json")
    assert comparison["passed"] and len(comparison["results"]) == 314
    rows = [r for r in comparison["results"] if r["classification"] == "rules_vs_model"]
    assert len(rows) == 214
    for row in comparison["results"]:
        name = Path(row["group"]) / f'{row["index"]:04d}.json'
        a = read(EVAL / "comparison-002/model" / name)
        b = read(EVAL / "comparison-002/rules" / name)
        assert row["dsl_valid"] and a["files"] == b["files"]
    receipts = [EVAL / f"full-services-002/shard-{i}/result.json" for i in range(2)]
    receipts += [EVAL / "legacy100/result.json", EVAL / "legacy-chain-receipt.json"]
    assert all(read(path)["passed"] for path in receipts)
    assert sum(sum(r["count"] for r in read(path)["results"]) for path in receipts[:2]) == 214
    assert all(read(path)["assets_unchanged"] for path in receipts[:2])
    assert "34 passed" in (EVAL / "compatibility.log").read_text()
    supplement = destination / "supplement"
    supplement.mkdir()
    binary = ROOT / "som-rs/target/composed-quality/release/som"
    assert digest(binary) == "c248c3431358c1273b224b7376e1b7eff507d5cfcc0d9a921e952f53fc7f42a1"
    (supplement / "som").write_bytes(binary.read_bytes())
    (supplement / "som").chmod(0o755)
    original_paths = [ROOT / name for name in baseline["files"] if any(name.startswith(prefix) for prefix in (
        "som-rs/models/composed-quality-candidate/", "som-code-python/data/blocks/", "som-code-python/data/block-contracts/composed", "som-rs/contracts/python-quality-v1/",
        "som-rs/evals/direct-baselines/", "som-rs/evals/fixed-baseline-som-"))]
    assert all(digest(path) == baseline["files"][str(path.relative_to(ROOT))] for path in original_paths)
    with tarfile.open(EVAL / "baseline/sources.tar.gz") as archive:
        archived_model = archive.extractfile("som-rs/src/model.rs").read().decode()
    current_model = (ROOT / "som-rs/src/model.rs").read_text()
    prefix = current_model.split("\nimpl Decoder {\n    /// Offline evidence only.", 1)[0]
    tail = current_model[current_model.index("fn model_error("):]
    assert archived_model == prefix + "\n" + tail
    environment = {}
    for label, executable in [("fixtures", "/private/tmp/som-business-eval-venv/bin/python"),
                              ("quality", "/private/tmp/som-python-quality-tools-2026-10-01/bin/python")]:
        code = "import importlib.metadata as m,json,platform,sys;print(json.dumps({'python':sys.version,'platform':platform.platform(),'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))"
        environment[label] = json.loads(subprocess.check_output([executable, "-c", code], text=True))
    (supplement / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    (supplement / "tracked-wip.patch").write_bytes(subprocess.check_output(["git", "diff", "--binary"], cwd=ROOT))
    (supplement / "git-status.txt").write_bytes(subprocess.check_output(["git", "status", "--short"], cwd=ROOT))
    codec = read(ROOT / "som-rs/models/composed-quality-candidate/codec.json")
    inputs = []
    for row in rows:
        trace_path = EVAL / f'comparison-002/traces/{row["group"]}/{row["index"]:04d}.json'
        trace = read(trace_path)
        ids = [codec["token_ids"][token] for token in re.findall(r"<[^<>]+>", trace["actual_model_input"])]
        model = next(item for item in trace["request_log"] if item.get("stage") == "model")
        assert len(ids) == model["input_tokens"]
        assert len(trace["raw_model_output"]) == 1 and trace["model_dsl"] is not None
        inputs.append({"case": str(trace_path.relative_to(ROOT)), "trace_sha256": digest(trace_path), "actual_input_token_ids": ids})
    (supplement / "v6-input-token-ids.json").write_text(json.dumps(inputs, indent=2) + "\n")
    with tarfile.open(destination / "supplement.tar.gz", "w:gz") as archive:
        for path in supplement.iterdir():
            archive.add(path, arcname=path.name, recursive=False)
    frozen = ROOT / "som-core/data/flow-v7-r7/acceptance-seal-003/seal.json"
    acceptance = read(frozen)
    assert acceptance["frozen_before_test_weights"] and acceptance["counts"]["formal"] == 114
    assert acceptance["actual_input_overlap"] == acceptance["structural_overlap"] == 0
    groups = {}
    for group in ("services14", "compositions120", "blind80"):
        selected = [r for r in rows if r["group"] == group]
        def timing(key):
            values = sorted(r[key] for r in selected)
            return {"p50_ms": values[len(values)//2], "p95_ms": values[int(0.95*(len(values)-1))], "max_ms": max(values)}
        groups[group] = {"cases": len(selected), "rules": {"dsl_valid": len(selected), "requirements_met": len(selected), "function_passed": len(selected), "rejected": 0},
                         "model": {"dsl_valid": len(selected), "requirements_met": len(selected), "function_passed": len(selected), "rejected": 0},
                         "rule_time": timing("rule_ms"), "model_http_time": timing("model_ms")}
    record = {"version": "som.m1.closeout.v1", "passed": True, "groups": groups, "legacy_compatibility_cases": 100,
              "format_checks": 34, "complete_chains": {"v6": 214, "legacy": 100},
              "baseline_archive_sha256": baseline["archive_sha256"], "supplement_sha256": digest(destination / "supplement.tar.gz"),
              "binary_sha256": digest(binary), "old_production_decoder_unchanged": True,
              "same_final_sources": 314, "shared_function_check_reason": "independently checked source hashes are byte-identical for both methods",
              "not_proven": ["model superiority to general rules", "runtime dataflow", "branches", "compensation"],
              "memory_this_comparison": None, "model_directory_bytes": sum(p.stat().st_size for p in (ROOT / "som-rs/models/composed-quality-candidate").rglob("*") if p.is_file()),
              "defaults_unchanged": str((ROOT / "som-rs/models/current").resolve()) == baseline["default_model"] and str((ROOT / "som-code-python/data/blocks/current").resolve()) == baseline["default_blocks"],
              "m2_acceptance_sha256": digest(frozen), "m2_specification_sha256": acceptance["archive_sha256"],
              "receipts": {str(path.relative_to(ROOT)): digest(path) for path in receipts + [EVAL / "compatibility.log", EVAL / "comparison-002/comparison.json", frozen]},
              "paid_agent_runs": 0, "claude_experiment_comparison": "pending M5; failed direct baseline retained"}
    assert record["defaults_unchanged"]
    (destination / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ("passed", "same_final_sources", "complete_chains", "supplement_sha256")}))


if __name__ == "__main__":
    main()
