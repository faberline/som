"""Verify a selected M3 model on frozen development prompts before formal data."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import gzip
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time

import httpx
from tokenizers import Tokenizer

from m3_artifact_checks import check
from m3_local_fixture import M3Fixture, close_native, interruption
from run_m3_flow_acceptance import candidate_hashes, checkpoint, structure
from som_core import composed, flow, flow_codec, flow_compact, flow_m3_target, quality
from som_core.flow_m3_evidence import ROOT, case_evidence, digest, hashes, verify_runtime, verify_seal
from som_core.train_flow import BLOCKS
from som_core.train_flow_m3 import BUDGET, CONFIG, PURPOSE, TIME_LIMIT, training_budget
from som_core.v1 import load_blocks
from switch_flow_default import cli, start, stop


def replace_json(path: Path, value) -> None:
    """Replace the private clone file. Never modify a shared hard link."""
    pending = path.with_name(path.name + ".negative-pending")
    pending.write_text(composed.canonical(value) + "\n")
    pending.replace(path)


def loader_refused(stdout: str, stderr: str, exit_code: int) -> bool:
    if exit_code == 0:
        return False
    for line in (stdout + "\n" + stderr).splitlines():
        if line.startswith("MODEL_UNAVAILABLE: "):
            return True
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("status") == "error" and value.get("code") == "MODEL_UNAVAILABLE":
            return True
    return False


def loader_controls(model: Path, runtime: Path, directory: Path) -> list[dict]:
    directory.mkdir()
    manifest = json.loads((model / "manifest.json").read_text())
    controls = [("hash-" + name.replace(".", "-"), "hash", name) for name in sorted(manifest["files"])]
    controls += [("version-" + key.replace("_", "-"), "config", key) for key in
                 ("flow_output_policy", "flow_input_policy", "flow_decode_policy", "training_profile")]
    controls += [("codec-version", "sidecar", "codec.json"),
                 ("target-policy-content", "sidecar", "target-policy.json"),
                 ("wrong-purpose", "manifest", "purpose"),
                 ("formal-training", "manifest", "formal_training_examples"),
                 ("missing-development", "manifest", "development_perfect"),
                 ("legacy-manifest-hash", "manifest", "legacy_manifest_sha256"),
                 ("block-library-hash", "manifest", "blocks_sha256")]
    records = []
    for identity, kind, key in controls:
        case = directory / identity
        case.mkdir()
        clone = case / "model"
        shutil.copytree(model, clone, copy_function=os.link,
                        ignore=shutil.ignore_patterns("checkpoints"))
        altered = deepcopy(manifest)
        if kind == "hash":
            path = clone / key
            pending = path.with_name(path.name + ".negative-pending")
            pending.write_bytes(path.read_bytes() + b"\n")
            pending.replace(path)
        elif kind == "config":
            value = json.loads((clone / "config.json").read_text())
            value[key] = "unknown-negative-control"
            replace_json(clone / "config.json", value)
            altered["files"]["config.json"] = digest(clone / "config.json")
        elif kind == "sidecar":
            value = json.loads((clone / key).read_text())
            value["version"] = "unknown-negative-control"
            replace_json(clone / key, value)
            altered["files"][key] = digest(clone / key)
        else:
            altered[key] = (300 if key == "formal_training_examples" else
                            False if key == "development_perfect" else "unknown-negative-control")
        replace_json(clone / "manifest.json", altered)
        command = [str(runtime / "som"), "serve", "--model", str(clone),
                   "--blocks", str(BLOCKS), "--port", "0"]
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = process.communicate(timeout=30)
        except BaseException:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=15)
            raise
        (case / "stdout.log").write_text(stdout)
        (case / "stderr.log").write_text(stderr)
        passed = loader_refused(stdout, stderr, process.returncode)
        record = {"id": identity, "kind": kind, "changed_field_or_file": key, "command": command, "exit_code": process.returncode,
                  "passed": passed, "manifest_sha256": digest(clone / "manifest.json"),
                  "stdout_sha256": digest(case / "stdout.log"), "stderr_sha256": digest(case / "stderr.log")}
        (case / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        records.append(record)
        if not passed:
            raise ValueError("native loader accepted an invalid export: " + identity)
    return records


def verify_trace(prompt: dict, plan: dict, generated: dict, directory: Path, codec: Tokenizer) -> dict:
    if generated.get("decode_mode") != "constrained":
        raise ValueError("selected development probe did not use constrained native inference")
    actual_input = flow_codec.features_m3_bound(prompt, flow_compact.metadata())
    if (generated["actual_input"] != actual_input
            or generated["input_ids"] != codec.encode(actual_input, add_special_tokens=False).ids
            or not 0 < len(generated["input_ids"]) <= 2048
            or not 0 < len(generated["output_ids"]) <= 2048
            or flow_m3_target.token_ids(generated["raw_output"]) != generated["output_ids"]
            or structure(prompt, generated["raw_output"]) != plan):
        raise ValueError("native development input or selected output differs")
    with gzip.open(directory / "allowed-sets.json.gz", "rt") as stream:
        evidence = json.load(stream)
    allowed = evidence["rust"]["trace"]
    choices = generated["choices"]
    if len(choices) != len(allowed) or len(choices) != len(generated["output_ids"]):
        raise ValueError("native development prefix evidence is incomplete")
    for index, (choice, expected) in enumerate(zip(choices, allowed, strict=True)):
        if (choice["position"] != index or choice["selected"] != generated["output_ids"][index]
                or choice["allowed"] != expected["allowed"] or choice["selected"] not in choice["allowed"]
                or choice["forced"] != (len(choice["allowed"]) == 1)
                or type(choice["raw_top1"]) is not int or not 0 <= choice["raw_top1"] < CONFIG["vocab_size"]):
            raise ValueError("actual development choice differs from its complete legal set")
    forced = sum(choice["forced"] for choice in choices)
    return {"contract_forced": forced, "model_selected": len(choices) - forced,
            "choices": dict(Counter(choice["context"].get("choice", "structure_or_binding") for choice in choices))}


def native_health_contract(manifest_sha256: str, blocks_sha256: str, training_steps: int) -> dict:
    return {"status": "ok", "model_loaded": True, "blocks_loaded": True,
            "model_manifest_sha256": manifest_sha256, "blocks_sha256": blocks_sha256,
            "training_steps": training_steps, "flow_model_purpose": PURPOSE,
            "flow_model_evidence": "disjoint-full-training-candidate",
            "flow_decode_policy": CONFIG["flow_decode_policy"], "flow_input_policy": CONFIG["flow_input_policy"],
            "flow_codec_version": flow_codec.COMPACT_VERSION, "formatter_version": quality.RUFF_VERSION,
            "quality_contract": quality.VERSION, "flow_prompt_version": flow.VERSION,
            "dsl_version": flow.DSL_VERSION}


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("data", "source-seal", "model", "runtime", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.data, args.model, args.runtime, args.output = [getattr(args, name).resolve() for name in
                                                       ("data", "model", "runtime", "output")]
    if not args.output.is_relative_to(ROOT / "som-rs/evals"):
        raise ValueError("selected development evidence must stay local")
    seal = verify_seal(args.source_seal, args.data)
    verify_runtime(seal, {name: args.runtime / name for name in
                         ("som", "som-flow-compact-verify", "som-flow-mask")})
    manifest = checkpoint(args.model, args.data, args.source_seal)
    fixed = candidate_hashes(args.model)
    before = hashes(args.data)
    ledger = json.loads(BUDGET.read_text())
    if (ledger.get("limit_seconds") != TIME_LIMIT or any(row.get("elapsed_seconds") is None for row in ledger["attempts"])
            or any(not isinstance(row["elapsed_seconds"], (int, float)) or isinstance(row["elapsed_seconds"], bool)
                   or not math.isfinite(row["elapsed_seconds"]) or row["elapsed_seconds"] < 0 for row in ledger["attempts"])
            or sum(row["elapsed_seconds"] for row in ledger["attempts"]) > TIME_LIMIT):
        raise ValueError("selected checkpoint lacks a closed shared time ledger within one week")
    rows = [json.loads(line) for line in (args.data / "dev.jsonl").read_text().splitlines()]
    if len(rows) != 300 or [row["id"] for row in rows] != [f"m3-dev-{index:04d}" for index in range(300)]:
        raise ValueError("selected development smoke requires the frozen development set")
    rows = rows[:12]
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "selection.json").write_text(json.dumps({"development_sha256": digest(args.data / "dev.jsonl"),
        "case_ids": [row["id"] for row in rows], "selection": "first twelve frozen development cases; fixed before formal",
        "candidate_sha256": fixed, "source_seal_sha256": digest(args.source_seal),
        "shared_budget_sha256": digest(BUDGET), "training_steps": manifest["training_steps"],
        "formal_opened": False}, indent=2) + "\n")
    with training_budget(3600):
        controls = loader_controls(args.model, args.runtime, args.output / "loader-controls")
        library = load_blocks(BLOCKS.parent)
        codec = Tokenizer.from_file(str(args.model / "tokenizer.json"))
        server = assembler = mask = None
        records = []
        try:
            server, address = start(args.runtime / "som", args.model, BLOCKS, args.output / "server")
            health = httpx.get(address + "/healthz", timeout=5).json()
            expected_health = native_health_contract(fixed["manifest.json"], digest(BLOCKS), manifest["training_steps"])
            if any(health.get(key) != value for key, value in expected_health.items()):
                raise ValueError("selected native health differs from its checkpoint and sidecars")
            assembler = subprocess.Popen([str(args.runtime / "som-flow-compact-verify"), str(BLOCKS)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            mask = subprocess.Popen([str(args.runtime / "som-flow-mask")], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            log = args.output / "server/stderr.log"
            with M3Fixture(args.output / "fixture") as fixture:
                for row in rows:
                    directory = args.output / row["id"]
                    directory.mkdir()
                    prompt = row["prompt"]
                    offset = log.stat().st_size
                    started = time.monotonic()
                    response = httpx.post(address + "/v1/generate", json=prompt, timeout=120)
                    (directory / "http-response.json").write_bytes(response.content)
                    with log.open("rb") as stream:
                        stream.seek(offset)
                        trace_text = stream.read().decode("utf-8")
                    (directory / "native-trace.jsonl").write_text(trace_text)
                    generated = [json.loads(line) for line in trace_text.splitlines() if line.startswith("{")
                                 and json.loads(line).get("stage") == "flow_decoded_plan"]
                    if len(generated) != 1 or response.status_code != 200 or response.json().get("status") != "ok":
                        raise ValueError("selected development native HTTP generation failed")
                    generated = generated[0]
                    plan = structure(prompt, generated["raw_output"])
                    flow.validate_plan(prompt, plan, flow_compact.metadata())
                    files = response.json()["files"]
                    record = {"id": row["id"], "mode": "constrained", "generation_ms": (time.monotonic() - started) * 1000,
                              "checkpoint_sha256": fixed["model.safetensors"]}
                    record.update(check(prompt, plan, files, directory, fixture, library, assembler, mask,
                        actual_output_ids=generated["output_ids"], actual_input=generated["actual_input"]))
                    record.update(verify_trace(prompt, plan, generated, directory, codec))
                    cli(args.runtime / "som", address, prompt, directory, files)
                    record["evidence_directory"] = str(directory.relative_to(ROOT))
                    (directory / "result.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
                    records.append(record)
                    print(composed.canonical({"id": row["id"], "passed": record["passed"]}), flush=True)
                    for disposable in (directory / "product/.venv", directory / "quality/mypy-cache", directory / "quality/pylint-cache"):
                        if disposable.is_dir():
                            shutil.rmtree(disposable)
                    if candidate_hashes(args.model) != fixed or hashes(args.data) != before:
                        raise ValueError("selected checkpoint or source changed during development smoke")
        finally:
            if server is not None:
                stop(server)
            for process in (assembler, mask):
                close_native(process)
    ledger = json.loads(BUDGET.read_text())
    if any(row["elapsed_seconds"] is None for row in ledger["attempts"]) or sum(row["elapsed_seconds"] for row in ledger["attempts"]) > TIME_LIMIT:
        raise ValueError("native development checks exceeded the shared week-long budget")
    evidence = [path for path in case_evidence(args.output) if "loader-controls" not in path.parts]
    for control in controls:
        case = args.output / "loader-controls" / control["id"]
        evidence.extend(case / name for name in ("stdout.log", "stderr.log", "result.json", "model/manifest.json"))
    report = {"version": "som.flow.m3-selected-native-path.v1", "passed": len(records) == 12,
        "required": 12, "records": records, "loader_controls": controls,
        "source_sha256": before, "source_unchanged": hashes(args.data) == before,
        "model_sha256": fixed, "model_unchanged": candidate_hashes(args.model) == fixed,
        "health": health, "source_seal_sha256": digest(args.source_seal),
        "shared_budget_sha256": digest(BUDGET), "shared_budget": ledger,
        "development_budget_attempt_id": ledger["attempts"][-1]["id"],
        "development_budget_scope": "native development model inference plus its complete quality and behavior probes; charged conservatively",
        "formal_opened": False, "native_backend": "Rust Candle Metal",
        "completed_unix": time.time(), "selected_path_checker_sha256": digest(Path(__file__)),
        "evidence_sha256": {str(path.relative_to(ROOT)): digest(path) for path in evidence}}
    if not report["source_unchanged"] or not report["model_unchanged"] or not all(row["passed"] for row in records):
        raise ValueError("selected development path is incomplete")
    (args.output / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n")



if __name__ == "__main__":
    with interruption():
        main()
