"""Prepare paired standalone-service tasks for Codex and Claude Code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "som-code-python/.venv/bin/python"
VERIFY = ROOT / "som-core/scripts/verify_service_v3.py"
SOM = ROOT / "som-rs/target/release/som"


def task_text(prompt: dict) -> tuple[str, list[str]]:
    package = prompt["package"]
    resource = prompt["resource"]
    revision = prompt["migration"]["revision"]
    fields = "\n".join(
        f"- {f['name']}: {f['type']}; required={f['required']}; "
        f"unique={f['unique']}; default={f.get('default', 'none')}"
        for f in resource["fields"]
    )
    paths = [
        f"{package}/models.py",
        f"{package}/schemas.py",
        f"{package}/api.py",
        f"tests/test_{package}.py",
        f"alembic/versions/{revision}_{package}.py",
        f"{package}/app.py",
        f"{package}/database.py",
        f"{package}/__init__.py",
        "alembic/env.py",
        "alembic.ini",
        "pyproject.toml",
        "README.md",
    ]
    steps = "\n".join(f"{i}. {step}" for i, step in enumerate(prompt["logic"], 1))
    task = f"""Build a standalone FastAPI CRUD service for {resource["name"]}.
Package: {package}. PostgreSQL table: {resource["table"]}. Route: {resource["path"]}.
Fields:
{fields}
The service also has read-only created_at and updated_at timezone-aware timestamps.
Write exactly these 12 product files:
{chr(10).join(f"- {path}" for path in paths)}
Use Python 3.12, FastAPI, SQLAlchemy, Pydantic, psycopg, Alembic, uv, and unittest.
The pinned dependencies are FastAPI 0.118.0, Uvicorn 0.53.0, SQLAlchemy 2.0.44,
psycopg 3.3.6, Alembic 1.20.0, Pydantic 2.11.9, and HTTPX 0.28.1.
The files must support uv sync, alembic upgrade head, alembic downgrade base,
and uvicorn {package}.app:app from a clean directory.
Read DATABASE_URL from the environment. Require a postgresql+psycopg URL.
Read TEST_DATABASE_URL for unittest. Do not use a SQLite fallback.
GET /healthz runs a database SELECT 1. Return 200 when healthy and 503 on DB failure.
Expose the five resource routes in this exact order:
{steps}
POST returns 201, DELETE returns 204, missing IDs return 404, invalid input returns 422,
and unique conflicts return 409. PATCH changes only submitted fields.
Reject null for nonnullable fields, including {prompt["query"]["filter_field"]}.
Create defaults are stored in PostgreSQL. created_at stays fixed; updated_at changes on writes.
List accepts optional {prompt["query"]["filter_field"]} boolean filter, limit default 20
in 1..{prompt["query"]["max_limit"]}, and offset default 0 with minimum 0.
Apply filter, id order, limit, and offset in SQL. Add a composite index on
({prompt["query"]["filter_field"]}, id).
Migration revision is {revision} and down_revision is null.
The generated unittest checks CRUD, defaults, filtering, invalid bounds, and missing IDs.
"""
    return task, paths


def checks_script(index: int, cases: Path) -> str:
    return f"""#!/bin/bash
set -euo pipefail
PYTHONPATH={ROOT / "som-core/src"} {PYTHON} {VERIFY} --cases {cases} --heldout-index {index} --candidate-dir .
"""


def som_script(paths: list[str]) -> str:
    allowed = json.dumps(paths)
    return f"""#!/bin/bash
set -euo pipefail
{SOM} generate prompt.json --url http://127.0.0.1:8766 > som_response.json
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
    for index in indices:
        prompt = rows[index]["prompt"]
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
                        "resource, logic, tests, migration, query. Set version to som.prompt.v3. "
                        "Resource has name, table, path, fields. Each field has name, type, "
                        "required, unique, and default only when required=false. "
                        "Logic is the five exact task sentences in the requested order. "
                        'Tests is ["Check standalone CRUD, boolean filtering, page bounds, '
                        'defaults, timestamps, missing id, and migration."]. '
                        f"Query has filter_field={prompt['query']['filter_field']} and "
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
        "--cases", type=Path, default=ROOT / "som-core/data/service-v3/heldout.jsonl"
    )
    args = parser.parse_args()
    prepare(args.root, args.indices, args.cases.resolve())


if __name__ == "__main__":
    main()
