"""One frozen round, four isolated workers, one complete M3 gate receipt."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

from som_core.flow_m3_data import COUNTS
from som_core.flow_m3_evidence import ROOT, RUNTIME_EXECUTABLES, case_evidence, digest, hashes, verify_artifact_record, verify_runtime, verify_seal
from som_core.train_flow import BLOCKS, LIMITS
from som_core.train_flow_m3 import ADMISSION_VERSION
from som_core.flow_constraints import policy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("admission", "formal", "protection"), required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--source-seal", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.data, args.output, args.runtime = args.data.resolve(), args.output.resolve(), args.runtime.resolve()
    if not 1 <= args.workers <= 4 or not args.output.is_relative_to(ROOT / "som-rs/evals"):
        raise ValueError("invalid M3 gate worker count or destination")
    seal = verify_seal(args.source_seal, args.data)
    verify_runtime(seal, {name: args.runtime / name for name in RUNTIME_EXECUTABLES})
    before = hashes(args.data)
    fixed_model = None
    if args.stage in {"formal", "protection"}:
        if args.model is None:
            raise ValueError("formal generation requires the fixed candidate")
        from run_m3_flow_acceptance import candidate_hashes, checkpoint
        checkpoint(args.model.resolve(), args.data, args.source_seal)
        fixed_model = candidate_hashes(args.model.resolve())
    if args.resume:
        original = json.loads((args.output / "started.json").read_text())
        if original["source_sha256"] != before or original["workers"] != args.workers or args.stage != "admission":
            raise ValueError("only unchanged-source partial admission can resume")
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "started.json").write_text(json.dumps({"stage": args.stage,
            "workers": args.workers, "source_sha256": before,
            "source_seal_sha256": digest(args.source_seal), "started_unix": time.time(),
            "model": str(args.model) if args.model else None}, indent=2) + "\n")
    processes = []
    logs = []
    def interrupted(_number, _frame):
        raise KeyboardInterrupt("M3 gate interrupted")
    old_signal = signal.signal(signal.SIGTERM, interrupted)
    try:
        for index in range(args.workers):
            directory = args.output / f"worker-{index:02d}"
            script = {'admission': 'prepare_m3_flow_training.py', 'formal': 'run_m3_flow_acceptance.py',
                      'protection': 'run_m3_protection.py'}[args.stage]
            command = [sys.executable, str(Path(__file__).with_name(script)),
                       "--data", str(args.data), "--source-seal", str(args.source_seal.resolve()),
                       "--output", str(directory), "--shards", str(args.workers),
                       "--shard-index", str(index)]
            if args.stage != 'protection':
                command.extend(['--mask-binary', str(args.runtime / 'som-flow-mask')])
            if args.stage == "admission":
                command.extend(["--binary", str(args.runtime / "som-flow-compact-verify")])
                if args.resume and directory.exists(): command.append("--resume")
            elif args.stage == 'formal':
                command.extend(["--model", str(args.model.resolve()), "--binary", str(args.runtime / "som"),
                    "--assembler", str(args.runtime / "som-flow-compact-verify"),
                    "--trace-binary", str(args.runtime / "som-flow-model-trace")])
            else:
                command.extend(['--model', str(args.model.resolve()), '--runtime', str(args.runtime)])
            log = (args.output / f"worker-{index:02d}.log").open("a")
            logs.append(log)
            processes.append(subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log))
        (args.output / "processes.json").write_text(json.dumps([
            {"pid": process.pid, "worker": index} for index, process in enumerate(processes)], indent=2) + "\n")
        while any(process.poll() is None for process in processes):
            progress = []
            for index, process in enumerate(processes):
                receipt = args.output / f"worker-{index:02d}/result.json"
                try:
                    value = json.loads(receipt.read_text()) if receipt.exists() else {}
                except json.JSONDecodeError:
                    value = {}
                progress.append({"worker": index, "attempted": value.get("attempted", len(value.get("records", []))),
                                 "exit_code": process.poll()})
            print(json.dumps({"stage": args.stage, "progress": progress}), flush=True)
            # This is a worker wait. The controlling tool yields each minute.
            time.sleep(30)
        receipts = []
        for index in range(args.workers):
            path = args.output / f"worker-{index:02d}/result.json"
            try:
                receipt = json.loads(path.read_text())
            except (FileNotFoundError, json.JSONDecodeError) as error:
                receipt = {"records": [], "shard_passed": False,
                           "failure": str(error), "exit_code": processes[index].returncode}
            receipts.append(receipt)
        records = [row for receipt in receipts for row in receipt["records"]]
        required = {'admission': 2700, 'formal': 600, 'protection': 544}[args.stage]
        expected = ({f"m3-{split}-{index:04d}" for split in ("train", "dev") for index in range(COUNTS[split])}
                    if args.stage == "admission" else
                    {(f"m3-formal-{index:04d}", mode) for index in range(300) for mode in ("constrained", "unconstrained")})
        if args.stage == 'protection':
            from run_m3_protection import jobs
            expected = {row['id'] for row in jobs()}
        actual = ({row["id"] for row in records} if args.stage != "formal"
                  else {(row["id"], row["mode"]) for row in records})
        unchanged = hashes(args.data) == before
        complete = len(records) == required and actual == expected
        passed = complete and unchanged and all(process.returncode == 0 for process in processes)
        for index, receipt in enumerate(receipts):
            passed = passed and receipt.get("source_sha256") == before and receipt.get("shard_passed") is True
            if args.stage in {'formal', 'protection'}:
                passed = passed and receipt.get('model_unchanged') is True
                passed = passed and receipt.get('model_sha256') == fixed_model
            passed = passed and receipt.get("shards") == args.workers and receipt.get("shard_index") == index
            for row in receipt["records"]:
                row["evidence_directory"] = str((args.output / f"worker-{index:02d}" /
                    (Path(row["mode"]) if args.stage == "formal" else Path()) / row["id"]).relative_to(ROOT))
        report = {"version": "som.flow.m3-complete-gate.v1", "stage": args.stage,
                  "required": required, "attempted": len(records), "records": records,
                  "complete": complete, "source_sha256": before,
                  "source_unchanged": unchanged, "passed": passed,
                  "dataset_sha256": digest(args.data / "manifest.json"),
                  "workers": args.workers, "worker_receipts": {
                      str((args.output / f"worker-{index:02d}/result.json").relative_to(ROOT)):
                      digest(args.output / f"worker-{index:02d}/result.json") for index in range(args.workers)
                      if (args.output / f"worker-{index:02d}/result.json").is_file()},
                  "worker_failures": [receipt.get("failure") for receipt in receipts if receipt.get("failure")]}
        if args.stage == "admission":
            if passed:
                for row in records: verify_artifact_record(row)
        elif args.stage == 'formal':
            report["summaries"] = {mode: {
                **{metric: sum(row.get(metric) is True for row in records if row["mode"] == mode)
                   for metric in ("grammar_valid", "dsl_valid", "requirements_fit", "passed")},
                "rejections": dict(Counter(str(row.get("rejection")) for row in records if row["mode"] == mode and not row["passed"]))}
                for mode in ("constrained", "unconstrained")}
            report["passed"] = passed and report["summaries"]["constrained"]["passed"] == 300
            memories = [receipt.get('observations', {}).get('peak_sampled_resident_bytes') for receipt in receipts]
            report['observations'] = {
                'model_directory_bytes': receipts[0].get('observations', {}).get('model_directory_bytes'),
                'peak_sampled_resident_bytes': max((value for value in memories if value is not None), default=None),
                'worker_observations': [receipt.get('observations') for receipt in receipts],
                'generation_ms': {mode: [row.get('generation_ms') for row in records if row['mode'] == mode]
                                  for mode in ('constrained', 'unconstrained')},
                'thresholds': 'size, latency and resident memory are observations only'}
        else:
            report['groups'] = {group: {'attempted': sum(row['group'] == group for row in records),
                'passed': sum(row['group'] == group and row['passed'] is True for row in records)}
                for group in {row['group'] for row in records}}
            report['passed'] = passed and all(row['passed'] is True for row in records)
        result = args.output / "result.json"
        result.write_text(json.dumps(report, indent=2, default=str) + "\n")
        if args.stage == "admission" and report["passed"]:
            evidence = [result, args.source_seal]
            for row in records:
                directory = ROOT / row["evidence_directory"]
                evidence.extend(case_evidence(directory))
            evidence.extend(ROOT / name for name in report["worker_receipts"])
            admission = {"version": ADMISSION_VERSION, "counts": COUNTS, "limits": LIMITS,
                "quality_behavior_passed": True, "dataset_sha256": report["dataset_sha256"],
                "source_seal_sha256": digest(args.source_seal), "blocks_sha256": digest(BLOCKS),
                "decode_policy": policy(), "formal_training_examples": 0,
                "quality_behavior_report": str(result.relative_to(ROOT)),
                "evidence_sha256": {str(path.resolve().relative_to(ROOT)): digest(path) for path in evidence}}
            (args.output / "admission.json").write_text(json.dumps(admission, indent=2) + "\n")
        print(json.dumps({"stage": args.stage, "attempted": len(records), "passed": report["passed"]}), flush=True)
        raise SystemExit(not report["passed"])
    finally:
        for process in processes:
            if process.poll() is None: process.terminate()
        for process in processes:
            try: process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs: log.close()
        signal.signal(signal.SIGTERM, old_signal)


if __name__ == "__main__":
    main()
