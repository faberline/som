"""Open the new formal set only after a fixed, perfect raw-dev checkpoint.

The constrained path uses real Rust Metal HTTP and CLI. The control uses
identical weights without masks. No reference DSL supplies a product answer.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import traceback

import httpx

from composed_evidence import failure_details
from m3_artifact_checks import check
from m3_local_fixture import M3Fixture, ServerObservation, close_native, interruption
from som_core import composed, flow, flow_codec, flow_compact
from som_core.flow_m3_evidence import ROOT, digest, hashes, verify_runtime, verify_seal
from som_core.train_flow import BLOCKS
from som_core.train_flow_m3 import CONFIG, PURPOSE, SIDECARS
from som_core.v1 import V1Error, load_blocks
from switch_flow_default import cli, start, stop


def structure(prompt: dict, text: str):
    """Read only compact syntax. Requirement checks are a separate result."""
    pool = flow_codec.pool(prompt, metadata=flow_compact.metadata())
    selected = re.findall(r"<[^<>\s]+>", text)
    if not selected or selected[-1] != "<eos>" or "".join(selected) != text or len(selected) > 2048:
        raise ValueError("raw compact sequence is incomplete")
    index = 0
    def read(depth=0):
        nonlocal index
        if depth > 32 or index >= len(selected) - 1:
            raise ValueError("raw compact sequence is truncated or too deep")
        token = selected[index]
        index += 1
        if token in {"<map>", "<list>"}:
            end = "</map>" if token == "<map>" else "</list>"
            value = {} if token == "<map>" else []
            while index < len(selected) - 1 and selected[index] != end:
                key = read(depth + 1)
                if isinstance(value, dict):
                    if not isinstance(key, str) or key in value:
                        raise ValueError("duplicate or non-string raw key")
                    value[key] = read(depth + 1)
                else:
                    value.append(key)
            if index >= len(selected) - 1:
                raise ValueError("unclosed raw container")
            index += 1
            return value
        matched = re.fullmatch(r"<v:(0|[1-9][0-9]*)>", token)
        if matched is None or int(matched[1]) >= len(pool):
            raise ValueError("unknown raw token or binding")
        return pool[int(matched[1])]
    plan = read()
    if index != len(selected) - 1:
        raise ValueError("extra tokens after raw plan")
    return plan


def dsl_valid(prompt: dict, plan: dict) -> bool:
    """Check selected typed operations separately from requested operations."""
    shadow = deepcopy(prompt)
    metadata = flow_compact.metadata()
    catalog = metadata["flow_blocks"]
    def nodes(items):
        result = []
        for node in items:
            value = deepcopy(node)
            selected = catalog[value.pop("id")]
            value["op"] = selected["operation"]
            if value["op"] == "transaction": value["steps"] = nodes(value["steps"])
            elif value["op"] == "if":
                value["then"] = nodes(value["then"])
                value["else"] = nodes(value["else"])
            elif value["undo"] is not None: value["undo"] = catalog[value["undo"]]["inverse"]
            result.append(value)
        return result
    try:
        shadow["operations"] = [
            {"scope": operation[0], "action": operation[1]}
            for file in plan["files"] for block in file["blocks"]
            if (operation := metadata["blocks"][block["id"]].get("operation"))
        ]
        shadow["flow"] = {**deepcopy(plan["flow"]), "inputs": deepcopy(prompt["flow"]["inputs"])}
        shadow["flow"]["steps"] = nodes(plan["flow"]["steps"])
        flow.validate_plan(shadow, plan, metadata)
        return True
    except (V1Error, KeyError, ValueError, TypeError):
        return False


def candidate_hashes(model: Path) -> dict[str, str]:
    """Bind the files used by this checkpoint, including frozen legacy."""
    manifest = json.loads((model / "manifest.json").read_text())
    required = {"tokenizer.json", "config.json", "model.safetensors", *SIDECARS}
    if set(manifest.get("files", {})) != required:
        raise ValueError("M3 checkpoint file inventory is incomplete")
    for name, wanted in manifest["files"].items():
        target = model / name
        if not target.resolve().is_relative_to(model) or digest(target) != wanted:
            raise ValueError("M3 checkpoint bytes differ from its manifest")
    if digest(model / "legacy/manifest.json") != manifest.get("legacy_manifest_sha256"):
        raise ValueError("M3 frozen legacy differs from its checkpoint")
    paths = [model / "manifest.json", *(model / name for name in required)]
    paths.extend(path for path in (model / "legacy").rglob("*") if path.is_file())
    return {str(path.relative_to(model)): digest(path) for path in sorted(paths)}


def checkpoint(model: Path, dataset: Path, seal: Path) -> dict:
    verify_seal(seal, dataset)
    manifest = json.loads((model / "manifest.json").read_text())
    dataset_hash = digest(dataset / "manifest.json")
    admission = json.loads((model / "admission.json").read_text())
    checks = manifest.get("development_checks", [])
    candidate_hashes(model)
    step = manifest.get("training_steps")
    if (manifest.get("purpose") != PURPOSE or manifest.get("development_perfect") is not True
            or json.loads((model / "config.json").read_text()) != CONFIG
            or manifest.get("formal_training_examples") != 0
            or manifest.get("dataset_sha256") != dataset_hash
            or admission.get("dataset_sha256") != dataset_hash
            or admission.get("source_seal_sha256") != digest(seal)
            or type(step) is not int or step <= 0 or step % 1000
            or len(checks) != step // 1000
            or any(row.get("step") != (index + 1) * 1000
                   or row.get("total") != 300
                   or len(row.get("results", [])) != 300
                   or any(item.get("id") != f"m3-dev-{position:04d}"
                          or type(item.get("correct")) is not bool
                          for position, item in enumerate(row["results"]))
                   or row.get("correct") != sum(item["correct"] for item in row["results"])
                   for index, row in enumerate(checks))
            or not checks or checks[-1].get("correct") != 300
            or checks[-1].get("step") != manifest.get("training_steps")
            or any(row.get("correct") == 300 for row in checks[:-1])):
        raise ValueError("formal data cannot open before the earliest perfect raw-dev checkpoint")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--source-seal", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--trace-binary", type=Path, required=True)
    parser.add_argument("--assembler", type=Path, required=True)
    parser.add_argument("--mask-binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    args = parser.parse_args()
    args.data, args.model, args.output = args.data.resolve(), args.model.resolve(), args.output.resolve()
    if not 1 <= args.shards <= 4 or not 0 <= args.shard_index < args.shards:
        raise ValueError("invalid bounded formal shard")
    seal = verify_seal(args.source_seal, args.data)
    verify_runtime(seal, {"som": args.binary, "som-flow-model-trace": args.trace_binary,
                          "som-flow-compact-verify": args.assembler,
                          "som-flow-mask": args.mask_binary})
    manifest = checkpoint(args.model, args.data, args.source_seal)
    fixed_model = candidate_hashes(args.model)
    before = hashes(args.data)
    args.output.mkdir(parents=True, exist_ok=False)
    formal = args.data / "formal.jsonl"
    data_manifest = json.loads((args.data / "manifest.json").read_text())
    if digest(formal) != data_manifest["files"][formal.name]:
        raise ValueError("sealed formal bytes changed")
    library = load_blocks(BLOCKS.parent)
    records = []
    server = assembler = mask = observation = None
    log = args.output / "server/stderr.log"
    report = {}
    try:
        server, address = start(args.binary.resolve(), args.model, BLOCKS, args.output / "server")
        # Native startup verifies the complete model before any formal case
        # or answer is opened for inference.
        if candidate_hashes(args.model) != fixed_model:
            raise ValueError("M3 checkpoint changed during native startup")
        rows = [json.loads(line) for line in formal.read_text().splitlines()]
        if len(rows) != 300:
            raise ValueError("formal scope differs from 300 cases")
        rows = [row for index, row in enumerate(rows) if index % args.shards == args.shard_index]
        (args.output / "opened.json").write_text(json.dumps({"formal_sha256": digest(formal),
            "model_sha256": fixed_model, "training_step": manifest["training_steps"],
            "model_fixed_before_opening": True, "native_startup_verified": True}, indent=2) + "\n")
        observation = ServerObservation(server)
        assembler = subprocess.Popen([str(args.assembler.resolve()), str(BLOCKS)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        mask = subprocess.Popen([str(args.mask_binary.resolve())], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with M3Fixture(args.output / "fixture") as fixture:
            for mode in ("constrained", "unconstrained"):
                (args.output / mode).mkdir()
                for row in rows:
                    directory = args.output / mode / row["id"]
                    directory.mkdir()
                    prompt = row["prompt"]
                    prompt_file = directory / "prompt.json"
                    prompt_file.write_text(composed.canonical(prompt) + "\n")
                    record = {"id": row["id"], "app": row["app"], "mode": mode,
                              "passed": False, "dsl_valid": False, "requirements_fit": False,
                              "grammar_valid": False, "checkpoint_sha256": digest(args.model / "model.safetensors")}
                    started = time.monotonic()
                    try:
                        if mode == "constrained":
                            offset = log.stat().st_size
                            response = httpx.post(address + "/v1/generate", json=prompt, timeout=120)
                            (directory / "http-response.json").write_bytes(response.content)
                            with log.open("rb") as stream:
                                stream.seek(offset)
                                trace_text = stream.read().decode("utf-8")
                            native = response.json()
                            files = native.get("files")
                        else:
                            result = subprocess.run([str(args.trace_binary.resolve()), "--model", str(args.model),
                                                     "--prompt", str(prompt_file)],
                                capture_output=True, text=True, check=False, timeout=180)
                            (directory / "raw-response.json").write_text(result.stdout)
                            trace_text = result.stderr
                            native = json.loads(result.stdout)
                            files = None
                        (directory / "native-trace.jsonl").write_text(trace_text)
                        record["generation_ms"] = (time.monotonic() - started) * 1000
                        record["native_refusal"] = native.get("code")
                        generated = next(json.loads(line) for line in trace_text.splitlines()
                                         if line.startswith("{") and json.loads(line).get("stage") == "flow_decoded_plan")
                        record["inference_ms"] = generated["inference_ms"]
                        record["timings"] = [json.loads(line) for line in trace_text.splitlines()
                            if line.startswith("{") and json.loads(line).get("stage") == "request"]
                        actual = structure(prompt, generated["raw_output"])
                        record["grammar_valid"] = True
                        record["dsl_valid"] = dsl_valid(prompt, actual)
                        flow.validate_plan(prompt, actual, flow_compact.metadata())
                        record["requirements_fit"] = True
                        if native.get("status") != "ok":
                            raise ValueError("native actual generation rejected the selected plan")
                        if mode == "unconstrained":
                            files = flow_compact.assemble(prompt, actual, library,
                                ROOT / "som-rs/models/composed-quality-candidate/ruff")
                        artifact = check(prompt, actual, files, directory, fixture, library, assembler, mask,
                            actual_output_ids=generated["output_ids"], actual_input=generated["actual_input"])
                        record.update(artifact)
                        if mode == "constrained":
                            cli(args.binary.resolve(), address, prompt, directory, files)
                            choices = generated["choices"]
                            record["choices"] = {kind: {"model": sum(not item["forced"] for item in choices if item["context"].get("choice") == kind),
                                "contract": sum(item["forced"] for item in choices if item["context"].get("choice") == kind)}
                                for kind in ("block", "order", "reference", "structure_or_binding")}
                    except Exception as error:
                        record.update(passed=False, rejection=getattr(error, "code", None) or record.get("native_refusal"),
                                      error=traceback.format_exc(), failure=failure_details(error))
                    record["seconds"] = time.monotonic() - started
                    (directory / "result.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
                    records.append(record)
                    unchanged = hashes(args.data) == before
                    model_unchanged = candidate_hashes(args.model) == fixed_model
                    summaries = {method: {"attempted": sum(item["mode"] == method for item in records),
                        **{metric: sum(item.get(metric) is True for item in records if item["mode"] == method)
                           for metric in ("grammar_valid", "dsl_valid", "requirements_fit", "passed")},
                        "rejections": dict(Counter(str(item.get("rejection")) for item in records if item["mode"] == method and not item["passed"]))}
                        for method in ("constrained", "unconstrained")}
                    report = {"version": "som.flow.m3-formal-model-acceptance.v1", "required": 300,
                              "formal_opened": True, "source_sha256": before,
                              "model_sha256": fixed_model, "model_unchanged": model_unchanged,
                              "shards": args.shards, "shard_index": args.shard_index,
                              "shard_required": len(rows),
                              "observations": {**observation.values(),
                                  "model_directory_bytes": sum(path.stat().st_size for path in args.model.rglob('*') if path.is_file())},
                              "shard_passed": unchanged and model_unchanged and summaries["constrained"]["passed"] == len(rows)
                                        and all(summaries[method]["attempted"] == len(rows) for method in summaries),
                              "source_unchanged": unchanged, "records": records, "summaries": summaries,
                              "passed": unchanged and model_unchanged and summaries["constrained"]["passed"] == 300
                                        and all(summaries[method]["attempted"] == 300 for method in summaries)}
                    pending = args.output / "result.pending.json"
                    pending.write_text(json.dumps(report, indent=2, default=str) + "\n")
                    pending.replace(args.output / "result.json")
                    print(composed.canonical({key: record.get(key) for key in
                        ("id", "mode", "passed", "seconds", "rejection", "failure")}), flush=True)
                    for disposable in (directory / "product/.venv", directory / "quality/mypy-cache", directory / "quality/pylint-cache"):
                        if disposable.is_dir(): shutil.rmtree(disposable)
                    if not unchanged or not model_unchanged:
                        raise ValueError("formal source changed; preserve failures and freeze a new round")
    finally:
        if observation is not None:
            observation.close()
        if server is not None:
            stop(server)
        for process in (assembler, mask):
            close_native(process)
    raise SystemExit(not report.get("shard_passed"))


if __name__ == "__main__":
    with interruption():
        main()
