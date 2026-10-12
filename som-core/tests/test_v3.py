"""Python reference checks for the frozen standalone-service family."""

from __future__ import annotations

import json
from pathlib import Path

from som_core.v1 import (
    V1Error,
    assemble,
    load_blocks,
    resolve_prompt_refs,
    validate_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "som-core/data/service-v3"
BLOCKS = ROOT / "som-code-python/data/blocks/v3"


def rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (CORPUS / name).read_text().splitlines()]


def test_frozen_python_outputs_and_todo_files() -> None:
    library = load_blocks(BLOCKS)
    cases = rows("heldout.jsonl")
    expected = rows("heldout.expected.jsonl")
    assert len(cases) == len(expected) == 50
    for row, files in zip(cases, expected, strict=True):
        validate_prompt(row["prompt"])
        assert (
            assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library) == files
        )
    todo = expected[0]
    assert cases[0]["prompt"]["query"]["max_limit"] == 100
    assert len(todo) == 12
    assert "todos/app.py" in todo
    assert "alembic.ini" in todo
    assert "pyproject.toml" in todo
    assert "completed: bool = False" in todo["todos/schemas.py"]
    assert "DATABASE_URL" in todo["todos/database.py"]


def test_frozen_rejections_keep_their_error_categories() -> None:
    library = load_blocks(BLOCKS)
    cases = rows("rejections.jsonl")
    assert len(cases) == 12
    for row in cases:
        try:
            validate_prompt(row["prompt"])
            assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
        except V1Error as error:
            assert error.code == row["code"]
        else:
            raise AssertionError("invalid standalone-service task was accepted")
