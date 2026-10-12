"""Prepare matched direct and SOM date-service tasks for both flagship agents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from prepare_flagship_v3 import checks_script
from prepare_flagship_v3 import task_text as service_task_text

ROOT = Path(__file__).resolve().parents[2]
SOM = ROOT / "som-rs/target/release/som"


def task_text(prompt: dict) -> tuple[str, list[str]]:
    task, paths = service_task_text(prompt)
    date_field = prompt["query"]["date_field"]
    task += f"""The optional {date_field} field is a PostgreSQL date and accepts YYYY-MM-DD.
It defaults to null. PATCH may set it to a date or null.
The list route accepts optional before=YYYY-MM-DD. Include only rows with
{date_field} strictly earlier than before. Null dates do not match before.
The date filter can be combined with the boolean filter, then id ordering,
limit, and offset. Invalid dates return 422.
Add a composite index on ({date_field}, id) in the model and migration.
"""
    return task, paths


def som_script(paths: list[str]) -> str:
    allowed = json.dumps(paths)
    return f"""#!/bin/bash
set -euo pipefail
{SOM} generate prompt.json > som_response.json
cat som_response.json
jq -e --argjson allowed '{allowed}' '.status == "ok" and (.files | type == "object") and ((.files | keys) == ($allowed | sort))' som_response.json >/dev/null
while IFS= read -r path; do
    [[ "$path" =~ ^[A-Za-z_0-9/.-]+\\.(py|toml|ini|md)$ ]] || exit 1
    case "$path" in /*|*..*|*//*) exit 1 ;; esac
    mkdir -p "$(dirname "$path")"
    jq -r -j --arg path "$path" '.files[$path]' som_response.json > "$path"
done < <(jq -r '.files | keys[]' som_response.json)
bash run_checks.sh
"""


def prepare(root: Path, indices: list[int], cases: Path) -> None:
    rows = [json.loads(line) for line in cases.read_text().splitlines()]
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"{root}: benchmark directory is not empty")
    for index in indices:
        prompt = rows[index]["prompt"]
        if prompt["version"] != "som.prompt.v4":
            raise ValueError(f"case {index}: expected a date-service task")
        task, paths = task_text(prompt)
        for agent in ("codex", "claude"):
            for mode in ("direct", "som"):
                directory = root / agent / f"case-{index:02d}" / mode
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "task.txt").write_text(task)
                (directory / "run_checks.sh").write_text(checks_script(index, cases))
                intro = (
                    task
                    + "\nWork only in this directory. Do not inspect the SOM repository.\n"
                    + "Do not install dependencies or start a database server.\n"
                )
                if mode == "direct":
                    method = (
                        "Write all 12 product files yourself. Do not use SOM. "
                        "Then run bash run_checks.sh. Fix your source if a check fails.\n"
                    )
                else:
                    (directory / "run_som_case.sh").write_text(som_script(paths))
                    method = (
                        "Write prompt.json yourself. It has exactly these keys: version, package, "
                        "resource, logic, tests, migration, query. Set version to som.prompt.v4. "
                        "Resource has name, table, path, fields. Each field has name, type, "
                        "required, unique, and default only when required=false. "
                        "Use type date for the date field. Logic is the five exact task sentences "
                        'in the requested order. Tests is ["Check standalone CRUD, boolean '
                        "and date-before filtering, page bounds, defaults, timestamps, missing "
                        'id, and migration."]. '
                        f"Query has filter_field={prompt['query']['filter_field']}, "
                        f"date_field={prompt['query']['date_field']}, and "
                        f"max_limit={prompt['query']['max_limit']}. "
                        "Migration has revision and down_revision (null). Do not name block IDs.\n"
                        "Then run bash run_som_case.sh. It calls the Rust CLI, prints the full "
                        "response once, installs its files, and runs the same checks. "
                        "Do not read or edit the prepared script.\n"
                        "Do not write or patch product source yourself. "
                        "If a check fails, change only prompt.json and repeat generation.\n"
                    )
                checks = (
                    "The prepared run_checks.sh runs the same independent PostgreSQL checks "
                    "for both methods. Do not read or edit it or its verifier. "
                    "If a check fails, retry under the method rule above. "
                    "When all checks pass, report the result and stop.\n"
                )
                (directory / "instruction.txt").write_text(intro + method + checks)
    print(root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--indices", type=int, nargs="+", required=True)
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "som-core/data/date-service-v4/heldout.jsonl",
    )
    args = parser.parse_args()
    prepare(args.root, args.indices, args.cases.resolve())


if __name__ == "__main__":
    main()
