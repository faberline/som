"""Admit M3 weights only after all 2,700 train/dev products pass actual checks."""

from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import traceback

from audit_business_transactions import source_hashes
from composed_evidence import failure_details
from m3_local_fixture import M3Fixture, close_native, interruption
from som_core import composed, flow_codec, flow_compact, flow_m3_target, quality
from som_core.flow_constraints import State, policy
from som_core.flow_m3_data import COUNTS
from som_core.flow_m3_evidence import ROOT, case_evidence, digest, hashes, verify_artifact_record, verify_runtime, verify_seal
from som_core.train_flow import BLOCKS, LEGACY, LIMITS
from som_core.train_flow_m3 import ADMISSION_VERSION
from som_core.v1 import load_blocks
from verify_flow_masks import trace
from verify_flow_service import verify as verify_behavior
from verify_python_quality import verify as verify_quality


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--source-seal", type=Path)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--mask-binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-ids")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    args = parser.parse_args()
    args.data = args.data.resolve()
    args.output = args.output.resolve()
    if not 1 <= args.shards <= 4 or not 0 <= args.shard_index < args.shards:
        raise ValueError("invalid bounded M3 shard assignment")
    if args.diagnostic_ids and args.shards != 1:
        raise ValueError("diagnostics cannot supply a sharded admission")
    if not args.output.is_relative_to(ROOT / "som-rs/evals"):
        raise ValueError("M3 evidence must stay in its repository eval directory")
    if args.diagnostic_ids is None and args.source_seal is None:
        raise ValueError("a complete admission requires a pre-training source seal")
    if args.source_seal:
        seal = verify_seal(args.source_seal, args.data)
        verify_runtime(seal, {"som-flow-compact-verify": args.binary,
                              "som-flow-mask": args.mask_binary})
    before = hashes(args.data)
    rows = [json.loads(line) for split in ("train", "dev")
            for line in (args.data / (split + ".jsonl")).read_text().splitlines()]
    if len(rows) != 2700:
        raise ValueError("M3 admission needs exactly 2,400 train and 300 dev labels")
    rows = [row for index, row in enumerate(rows) if index % args.shards == args.shard_index]
    if args.diagnostic_ids:
        selected = set(args.diagnostic_ids.split(","))
        rows = [row for row in rows if row["id"] in selected]
        if selected != {row["id"] for row in rows}:
            raise ValueError("unknown diagnostic id")
    records = []
    if args.resume:
        old = json.loads((args.output / "result.json").read_text())
        if (old["source_sha256"] != before or old.get("diagnostic_ids") != args.diagnostic_ids
                or old.get("shards", 1) != args.shards or old.get("shard_index", 0) != args.shard_index):
            raise ValueError("changed-source evidence cannot resume")
        records = old["records"]
        for record in records:
            verify_artifact_record(record)
            if digest(args.output / record["id"] / "result.json") != old["record_sha256"][record["id"]]:
                raise ValueError("saved case receipt changed")
    else:
        args.output.mkdir(parents=True, exist_ok=False)
    library = load_blocks(BLOCKS.parent)
    report = {}

    def persist():
        nonlocal report
        unchanged = hashes(args.data) == before
        complete = len(records) == 2700 and args.diagnostic_ids is None
        report = {"version": "som.flow.m3-training-artifacts.v1", "required": 2700,
                  "attempted": len(records), "records": records,
                  "source_sha256": before, "source_unchanged": unchanged,
                  "dataset_sha256": digest(args.data / "manifest.json"),
                  "diagnostic_ids": args.diagnostic_ids,
                  "shards": args.shards, "shard_index": args.shard_index,
                  "shard_required": len(rows),
                  "shard_passed": args.diagnostic_ids is None and len(records) == len(rows)
                                  and unchanged and all(row["passed"] for row in records),
                  "passed": complete and unchanged and all(row["passed"] for row in records),
                  "record_sha256": {row["id"]: digest(args.output / row["id"] / "result.json") for row in records}}
        temporary = args.output / "result.pending.json"
        temporary.write_text(json.dumps(report, indent=2, default=str) + "\n")
        temporary.replace(args.output / "result.json")

    remaining = [row for row in rows if row["id"] not in {item["id"] for item in records}]
    fixture_root = args.output / ("fixture-" + str(time.time_ns()))
    assembler = mask = None
    try:
        assembler = subprocess.Popen([str(args.binary.resolve()), str(BLOCKS)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        mask = subprocess.Popen([str(args.mask_binary.resolve())], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with M3Fixture(fixture_root) as fixture:
            for row in remaining:
                directory = args.output / row["id"]
                directory.mkdir()
                started = time.monotonic()
                prompt, plan = row["prompt"], row["dsl"]
                record = {"id": row["id"], "passed": False,
                          "evidence_directory": str(directory.relative_to(ROOT)),
                          "plan_origin": "reference label; not model inference",
                          "fixture": str(fixture_root.relative_to(ROOT)),
                          "prompt_sha256": hashlib.sha256(composed.canonical(prompt).encode()).hexdigest()}
                try:
                    (directory / "prompt.json").write_text(composed.canonical(prompt) + "\n")
                    (directory / "dsl.json").write_text(composed.canonical(plan) + "\n")
                    features = flow_codec.features_m3_bound(prompt, flow_compact.metadata())
                    encoded = flow_m3_target.encode(prompt, plan, flow_compact.metadata())
                    record["target_policy"] = flow_m3_target.VERSION
                    (directory / "input.txt").write_text(features)
                    (directory / "target.txt").write_text(encoded)
                    files = flow_compact.assemble(prompt, plan, library, LEGACY / "ruff")
                    request = composed.canonical({"prompt": prompt, "dsl": plan, "formatter_model": str(LEGACY)})
                    assembler.stdin.write(request + "\n")
                    assembler.stdin.flush()
                    native = json.loads(assembler.stdout.readline())
                    (directory / "response.json").write_text(composed.canonical(native) + "\n")
                    if native.get("status") != "ok" or native.get("files") != files:
                        raise ValueError("normal Python/Rust final byte parity failed")
                    record.update(byte_parity=True, normal_accepted=True,
                        formatted_bytes=sum(len(value.encode()) for value in files.values()),
                        files=len(files), blocks=sum(len(file["blocks"]) for file in plan["files"]),
                        input_tokens=features.count("<"), output_tokens=encoded.count("<"))
                    if record["files"] > 12 or record["blocks"] > 64 or max(record["input_tokens"], record["output_tokens"]) > 2048:
                        raise ValueError("normal product exceeds a fixed budget")
                    selected = flow_m3_target.token_ids(encoded)
                    mask.stdin.write(composed.canonical({"prompt": prompt, "tokens": selected}) + "\n")
                    mask.stdin.flush()
                    rust = json.loads(mask.stdout.readline())
                    compared = deepcopy(rust)
                    for choice in compared.get("trace", []): choice.pop("context", None)
                    python = trace(prompt, selected)
                    record["mask_parity"] = compared == python and rust.get("complete") is True
                    with gzip.open(directory / "allowed-sets.json.gz", "wt") as evidence:
                        evidence.write(composed.canonical({"rust": rust, "python": python}))
                    if not record["mask_parity"]:
                        raise ValueError("training label violates native/Python prefix masks")
                    product = directory / "product"
                    product.mkdir()
                    for name, source in files.items():
                        target = product / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(source)
                    record["source_sha256"] = source_hashes(product)
                    record["format_idempotent"] = all(not name.endswith(".py") or
                        quality.format_source(name, source, LEGACY / "ruff", package=prompt["package"]) == source
                        for name, source in files.items())
                    record["quality"] = verify_quality(product, directory / "quality")
                    if not record["quality"]["passed"] or not record["format_idempotent"]:
                        raise ValueError("strict product quality failed")
                    record["behavior"] = verify_behavior(prompt, product, fixture)
                    record["source_unchanged"] = record["source_sha256"] == source_hashes(product)
                    record["passed"] = True
                    verify_artifact_record(record)
                except Exception as error:
                    record.update(passed=False, error=traceback.format_exc(),
                                  failure=failure_details(error), rejection=getattr(error, "code", None))
                record["seconds"] = time.monotonic() - started
                (directory / "result.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
                records.append(record)
                persist()
                print(composed.canonical({key: record.get(key) for key in
                      ("id", "passed", "seconds", "formatted_bytes", "rejection", "failure")}), flush=True)
                # Installed environments and caches are disposable. Preserve
                # source, lockfile, logs, observed behavior and tool receipts.
                for disposable in (directory / "product/.venv", directory / "quality/mypy-cache", directory / "quality/pylint-cache"):
                    if disposable.is_dir(): shutil.rmtree(disposable)
                if not record["passed"] or not report["source_unchanged"]:
                    break
    finally:
        for process in (assembler, mask):
            close_native(process)
    persist()
    if report["passed"]:
        evidence = [args.output / "result.json", args.source_seal]
        for record in records:
            directory = args.output / record["id"]
            evidence.extend(case_evidence(directory))
        admission = {"version": ADMISSION_VERSION, "counts": COUNTS, "limits": LIMITS,
                     "quality_behavior_passed": True,
                     "dataset_sha256": digest(args.data / "manifest.json"),
                     "source_seal_sha256": digest(args.source_seal),
                     "blocks_sha256": digest(BLOCKS), "decode_policy": policy(),
                     "target_policy": flow_m3_target.policy(),
                     "quality_behavior_report": str((args.output / "result.json").relative_to(ROOT)),
                     "evidence_sha256": {str(path.resolve().relative_to(ROOT)): digest(path) for path in evidence},
                     "formal_training_examples": 0}
        (args.output / "admission.json").write_text(json.dumps(admission, indent=2) + "\n")
    print(composed.canonical({"attempted": len(records), "passed": report["passed"], "weights_created": False}))
    raise SystemExit(not (report["passed"] or report["shard_passed"]
                          or args.diagnostic_ids and len(records) == len(rows) and all(row["passed"] for row in records)))


if __name__ == "__main__":
    with interruption():
        main()
