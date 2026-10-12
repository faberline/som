"""Summarize paired standalone-service token runs and PostgreSQL correctness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from report_flagship_v1 import claude_usage, codex_usage, digest

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "som-code-python/.venv/bin/python"
VERIFY = ROOT / "som-core/scripts/verify_service_v3.py"


def postgres_check(directory: Path, index: int, cases: Path) -> bool:
    result = subprocess.run(
        [
            str(PYTHON),
            str(VERIFY),
            "--cases",
            str(cases),
            "--heldout-index",
            str(index),
            "--candidate-dir",
            str(directory),
        ],
        capture_output=True,
        text=True,
        env=os.environ | {"PYTHONPATH": str(ROOT / "som-core/src")},
        timeout=120,
    )
    if result.returncode:
        print(f"{directory}: {result.stdout}{result.stderr}"[-2500:])
    return result.returncode == 0


def report(root: Path, indices: list[int], cases: Path, model: Path) -> dict:
    summary = {
        "scope": "paired standalone CRUD services with identical tasks and checks",
        "prompt_version": "som.prompt.v3",
        "indices": indices,
        "model_manifest_sha256": digest(model / "manifest.json"),
        "heldout_sha256": digest(cases),
        "count_rule": {
            "codex": "input_tokens includes cached_input_tokens; add output_tokens once",
            "claude": "add input, cache creation, cache read, and output tokens",
        },
        "cases": [],
        "agents": {},
    }
    totals = {agent: {"direct": 0, "som": 0} for agent in ("codex", "claude_code")}
    checks = []
    for index in indices:
        case = {"index": index, "agents": {}}
        task_bytes = None
        check_bytes = None
        for agent, folder in (("codex", "codex"), ("claude_code", "claude")):
            runs = {}
            model_name = "gpt-6-astra"
            for mode in ("direct", "som"):
                directory = root / folder / f"case-{index:02d}" / mode
                task = (directory / "task.txt").read_bytes()
                script = (directory / "run_checks.sh").read_bytes()
                if task_bytes is None:
                    task_bytes, check_bytes = task, script
                elif task != task_bytes or script != check_bytes:
                    raise ValueError(f"case {index}: task or checks differ")
                if agent == "codex":
                    usage = codex_usage(directory / "events.jsonl")
                else:
                    model_name, usage = claude_usage(directory / "result.json")
                if mode == "som":
                    response = json.loads((directory / "som_response.json").read_text())
                    if response.get("status") != "ok":
                        raise ValueError(f"{directory}: SOM did not succeed")
                    if any(
                        (directory / path).read_text() != source
                        for path, source in response["files"].items()
                    ):
                        raise ValueError(
                            f"{directory}: installed source differs from response"
                        )
                    usage["files_match_response"] = True
                usage["instruction_sha256"] = digest(directory / "instruction.txt")
                runs[mode] = usage
                totals[agent][mode] += usage["total_tokens"]
                checks.append((usage, directory, index))
            direct = runs["direct"]["total_tokens"]
            som = runs["som"]["total_tokens"]
            case["agents"][agent] = {
                "model": model_name,
                "runs": runs,
                "token_reduction_fraction": round(1 - som / direct, 4),
            }
        case["task_sha256"] = hashlib.sha256(task_bytes).hexdigest()
        case["checks_sha256"] = hashlib.sha256(check_bytes).hexdigest()
        summary["cases"].append(case)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(postgres_check, directory, index, cases)
            for _, directory, index in checks
        ]
        for (usage, _, _), future in zip(checks, futures, strict=True):
            usage["independent_postgresql_passed"] = future.result()
    for agent, usage in totals.items():
        summary["agents"][agent] = {
            "direct_total_tokens": usage["direct"],
            "som_total_tokens": usage["som"],
            "token_reduction_fraction": round(1 - usage["som"] / usage["direct"], 4),
        }
    summary["all_postgresql_passed"] = all(
        usage["independent_postgresql_passed"] for usage, _, _ in checks
    )
    summary["thirty_percent_gate_on_this_sample"] = summary[
        "all_postgresql_passed"
    ] and all(
        value["token_reduction_fraction"] >= 0.30
        for value in summary["agents"].values()
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--indices", type=int, nargs="+", required=True)
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "som-core/data/service-v3/heldout.jsonl"
    )
    parser.add_argument(
        "--model", type=Path, default=ROOT / "som-rs/models/crud-query-service-v3"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = report(
        args.root, args.indices, args.cases.resolve(), args.model.resolve()
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["agents"]))
    if not summary["thirty_percent_gate_on_this_sample"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
