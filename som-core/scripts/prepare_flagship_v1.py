"""Prepare isolated, paired flagship benchmark tasks from the frozen holdout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "som-code-python" / ".venv" / "bin" / "python"
SOM = ROOT / "som-rs" / "target" / "release" / "som"


def task_text(prompt: dict) -> tuple[str, list[str]]:
    package = prompt["package"]
    resource = prompt["resource"]
    revision = prompt["migration"]["revision"]
    paths = [
        f"{package}/models.py",
        f"{package}/schemas.py",
        f"{package}/api.py",
        f"tests/test_{package}.py",
        f"alembic/versions/{revision}_{package}.py",
    ]
    fields = "\n".join(
        f"- {field['name']}: {field['type']}; required={field['required']}; unique={field['unique']}"
        for field in resource["fields"]
    )
    steps = "\n".join(
        f"{index}. {step}" for index, step in enumerate(prompt["logic"], 1)
    )
    task = f"""Build a complete five-file FastAPI CRUD package.
Resource class: {resource["name"]}. Package: {package}. PostgreSQL table: {resource["table"]}.
Route base: {resource["path"]}. Row route parameter: item_id.
Fields:
{fields}
Write exactly these product files:
{chr(10).join(f"- {path}" for path in paths)}
Use SQLAlchemy ORM, Pydantic schemas, FastAPI, unittest, and an Alembic migration.
Expose models.Base and api.create_router(get_session), which returns an APIRouter.
The caller supplies get_session. The generated unittest reads TEST_DATABASE_URL.
Migration revision: {revision}. Down revision: null.
Define the five routes in this order:
{steps}
List rows in id order. Patch only supplied fields. Return 404 for missing rows.
Return 409 for a unique value conflict. Alembic upgrade creates the table.
Alembic downgrade removes the table.
"""
    if prompt["version"] == "som.prompt.v2":
        query = prompt["query"]
        task += (
            f"The list route accepts optional filter_value, a string exact match on "
            f"{query['filter_field']}. It accepts integer limit with default 20 and "
            f"range 1..{query['max_limit']}, and integer offset with default 0 and "
            "minimum 0. Reject invalid bounds with 422. Apply the filter, id order, "
            "limit, and offset in the SQL query before returning rows.\n"
            "The generated unittest must check exact filtering, page bounds, "
            "missing id, and unique conflicts when supported.\n"
        )
    return task, paths


def checks_script(paths: list[str], index: int, cases: Path) -> str:
    return f"""#!/bin/bash
set -euo pipefail
{PYTHON} -m py_compile {" ".join(paths)}
TEST_DATABASE_URL=sqlite:///bench.db {PYTHON} -m unittest discover -s tests -v
PYTHONPATH={ROOT / "som-core" / "src"} {PYTHON} {ROOT / "som-core" / "scripts" / "verify_v1_postgres.py"} --cases {cases} --heldout-index {index} --sqlite --candidate-dir .
"""


def som_script(paths: list[str]) -> str:
    allowed = json.dumps(paths)
    return f"""#!/bin/bash
set -euo pipefail
{SOM} generate prompt.json > som_response.json
cat som_response.json
jq -e --argjson allowed '{allowed}' '.status == "ok" and (.files | type == "object") and ((.files | keys) == ($allowed | sort))' som_response.json >/dev/null
while IFS= read -r path; do
    [[ "$path" =~ ^[A-Za-z_0-9/.-]+\\.py$ ]] || exit 1
    case "$path" in /*|*..*|*//*) exit 1 ;; esac
    mkdir -p "$(dirname "$path")"
    jq -r -j --arg path "$path" '.files[$path]' som_response.json > "$path"
done < <(jq -r '.files | keys[]' som_response.json)
bash run_checks.sh
"""


def prepare(root: Path, indices: list[int], cases: Path) -> None:
    rows = [json.loads(line) for line in cases.read_text().splitlines()]
    for index in indices:
        prompt = rows[index]["prompt"]
        task, paths = task_text(prompt)
        for agent in ("codex", "claude"):
            for mode in ("direct", "som"):
                directory = root / agent / f"case-{index:02d}" / mode
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "task.txt").write_text(task)
                (directory / "run_checks.sh").write_text(
                    checks_script(paths, index, cases)
                )
                intro = (
                    task
                    + "\nWork only in this directory. Do not inspect the SOM repository.\n"
                    + "Do not install dependencies or start a database server.\n"
                )
                checks = (
                    "The prepared run_checks.sh runs the same syntax, unittest, and "
                    "independent SQLite checks for both methods.\n"
                    "Do not read or edit that script or its verifier. "
                    "If a check fails, retry under the method rule below.\n"
                    "When all checks pass, report the result and stop.\n"
                )
                if mode == "direct":
                    method = (
                        "Write the five product files yourself. Do not use SOM. "
                        "Then run bash run_checks.sh. Fix your source if a check fails.\n"
                    )
                else:
                    (directory / "run_som_case.sh").write_text(som_script(paths))
                    is_v2 = prompt["version"] == "som.prompt.v2"
                    keys = "version, package, resource, logic, tests, migration"
                    if is_v2:
                        keys += ", query"
                    test_sentence = (
                        "Check CRUD, exact filtering, page bounds, missing id, "
                        "and unique conflict when supported."
                        if is_v2
                        else "Check CRUD, missing id, and unique conflict when supported."
                    )
                    query_instruction = (
                        f"Query has filter_field={prompt['query']['filter_field']} and "
                        f"max_limit={prompt['query']['max_limit']}. "
                        if is_v2
                        else ""
                    )
                    method = (
                        f"Write prompt.json yourself with exactly these keys: {keys}. "
                        f"Set version to {prompt['version']}. "
                        "Resource has name, table, path, fields. Each field has name, type, "
                        "required, unique. Logic is the five exact task sentences in the "
                        f'requested order. Tests is the one-element array ["{test_sentence}"]. '
                        f"{query_instruction}"
                        "Migration has revision and down_revision (null). "
                        "Do not name block IDs.\n"
                        "Then run bash run_som_case.sh. It calls the Rust CLI, prints the full "
                        "response once, installs its files, and runs the same checks. "
                        "Do not read or edit the prepared script.\n"
                        "Do not write or patch product source yourself. "
                        "If a check fails, change only prompt.json and repeat generation.\n"
                    )
                (directory / "instruction.txt").write_text(intro + method + checks)
    print(root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--indices", type=int, nargs="+", required=True)
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "som-core/data/v1/heldout.jsonl"
    )
    args = parser.parse_args()
    prepare(args.root, args.indices, args.cases.resolve())


if __name__ == "__main__":
    main()
