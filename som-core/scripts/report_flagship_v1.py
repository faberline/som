"""Summarize paired flagship runs and independently verify their product files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "som-code-python" / ".venv" / "bin" / "python"
VERIFY = ROOT / "som-core" / "scripts" / "verify_v1_postgres.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def codex_usage(path: Path) -> dict:
    events = [json.loads(line) for line in path.read_text().splitlines()]
    turns = [
        event["usage"] for event in events if event.get("type") == "turn.completed"
    ]
    if len(turns) != 1:
        raise ValueError(f"{path}: expected one completed Codex turn")
    usage = turns[0]
    return {
        "input_tokens": usage["input_tokens"],
        "cached_input_tokens_included": usage["cached_input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["input_tokens"] + usage["output_tokens"],
    }


def claude_usage(path: Path) -> tuple[str, dict]:
    result = json.loads(path.read_text())
    if result.get("is_error"):
        raise ValueError(f"{path}: Claude run failed")
    usage = result["usage"]
    categories = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
    return next(iter(result["modelUsage"])), {key: usage[key] for key in categories} | {
        "total_tokens": sum(usage[key] for key in categories),
        "turns": result["num_turns"],
    }


def postgres_check(
    directory: Path, index: int, files: dict | None, cases: Path
) -> bool:
    with tempfile.TemporaryDirectory(prefix="som-flagship-check-") as temporary:
        command = [
            str(PYTHON),
            str(VERIFY),
            "--cases",
            str(cases),
            "--heldout-index",
            str(index),
        ]
        if files is None:
            command.extend(["--candidate-dir", str(directory)])
        else:
            file_map = Path(temporary) / "files.json"
            file_map.write_text(json.dumps(files))
            command.extend(["--files-json", str(file_map)])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=os.environ | {"PYTHONPATH": str(ROOT / "som-core" / "src")},
        )
        if result.returncode:
            print(result.stdout + result.stderr)
        return result.returncode == 0


def report(
    root: Path, indices: list[int], verify: bool, cases: Path, model: Path
) -> dict:
    rows = [json.loads(line) for line in cases.read_text().splitlines()]
    versions = {rows[index]["prompt"]["version"] for index in indices}
    if len(versions) != 1:
        raise ValueError("paired tasks must use one prompt version")
    summary = {
        "scope": "paired frozen tasks with the same check protocol",
        "prompt_version": versions.pop(),
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
    for index in indices:
        case = {"index": index, "agents": {}}
        task_bytes = None
        check_bytes = None
        for agent, directory_name in (("codex", "codex"), ("claude_code", "claude")):
            agent_case = {}
            model = "gpt-6-astra"
            for mode in ("direct", "som"):
                directory = root / directory_name / f"case-{index:02d}" / mode
                current_task = (directory / "task.txt").read_bytes()
                if task_bytes is None:
                    task_bytes = current_task
                elif task_bytes != current_task:
                    raise ValueError(f"case {index}: task text differs between runs")
                current_checks = (directory / "run_checks.sh").read_bytes()
                if check_bytes is None:
                    check_bytes = current_checks
                elif check_bytes != current_checks:
                    raise ValueError(f"case {index}: checks differ between runs")
                if agent == "codex":
                    usage = codex_usage(directory / "events.jsonl")
                else:
                    model, usage = claude_usage(directory / "result.json")
                files = None
                if mode == "som":
                    response = json.loads((directory / "som_response.json").read_text())
                    if response.get("status") != "ok":
                        raise ValueError(f"{directory}: SOM did not return success")
                    files = response["files"]
                    if any(
                        (directory / path).read_text() != source
                        for path, source in files.items()
                    ):
                        raise ValueError(
                            f"{directory}: installed source differs from response"
                        )
                    usage["files_match_response"] = True
                usage["independent_postgresql_passed"] = (
                    postgres_check(directory, index, files, cases) if verify else None
                )
                usage["instruction_sha256"] = digest(directory / "instruction.txt")
                agent_case[mode] = usage
                totals[agent][mode] += usage["total_tokens"]
            direct = agent_case["direct"]["total_tokens"]
            som = agent_case["som"]["total_tokens"]
            case["agents"][agent] = {
                "model": model,
                "runs": agent_case,
                "token_reduction_fraction": round(1 - som / direct, 4),
            }
        case["task_sha256"] = hashlib.sha256(task_bytes).hexdigest()
        case["checks_sha256"] = hashlib.sha256(check_bytes).hexdigest()
        summary["cases"].append(case)
    for agent, usage in totals.items():
        summary["agents"][agent] = {
            "direct_total_tokens": usage["direct"],
            "som_total_tokens": usage["som"],
            "token_reduction_fraction": round(1 - usage["som"] / usage["direct"], 4),
        }
    summary["all_postgresql_passed"] = all(
        run["independent_postgresql_passed"] is True
        for case in summary["cases"]
        for agent in case["agents"].values()
        for run in agent["runs"].values()
    )
    summary["thirty_percent_gate_on_this_sample"] = summary[
        "all_postgresql_passed"
    ] and all(
        agent["token_reduction_fraction"] >= 0.30
        for agent in summary["agents"].values()
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--indices", type=int, nargs="+", required=True)
    parser.add_argument("--verify-postgres", action="store_true")
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "som-core/data/v1/heldout.jsonl"
    )
    parser.add_argument("--model", type=Path, default=ROOT / "som-rs/models/crud-v1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = report(
        args.root,
        args.indices,
        args.verify_postgres,
        args.cases.resolve(),
        args.model.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["agents"]))
    if not summary["thirty_percent_gate_on_this_sample"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
