"""Frozen date-filter service contract and Python reference checks."""

from __future__ import annotations

import hashlib
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
DATA = ROOT / "som-core/data/date-service-v4"
BLOCKS = ROOT / "som-code-python/data/blocks/v4"


def rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / name).read_text().splitlines()]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_v4_outputs_and_split() -> None:
    manifest = json.loads((DATA / "manifest.json").read_text())
    for name in ("train", "heldout", "expected", "rejections"):
        filename = "heldout.expected.jsonl" if name == "expected" else f"{name}.jsonl"
        assert digest(DATA / filename) == manifest[f"{name}_sha256"]
    assert digest(BLOCKS / "blocks.json") == manifest["blocks_sha256"]
    train, cases, expected = (
        rows("train.jsonl"),
        rows("heldout.jsonl"),
        rows("heldout.expected.jsonl"),
    )
    assert len(train) == 280
    assert len(cases) == len(expected) == 50
    assert not {json.dumps(row["prompt"], sort_keys=True) for row in train} & {
        json.dumps(row["prompt"], sort_keys=True) for row in cases
    }
    library = load_blocks(BLOCKS)
    for row, files in zip(cases, expected, strict=True):
        validate_prompt(row["prompt"])
        assert (
            assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library) == files
        )
    todo = cases[0]
    assert todo["prompt"]["resource"]["name"] == "Todo"
    assert "before: date | None = None" in expected[0]["todos/api.py"]
    assert "sa.Date()" in next(
        value
        for key, value in expected[0].items()
        if key.startswith("alembic/versions/")
    )


def test_frozen_v4_rejection_categories() -> None:
    library = load_blocks(BLOCKS)
    rejected = rows("rejections.jsonl")
    assert len(rejected) == 10
    for row in rejected:
        try:
            validate_prompt(row["prompt"])
            assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
        except V1Error as error:
            assert error.code == row["code"]
        else:
            raise AssertionError("invalid v4 request was accepted")


def test_100_case_regression_set_keeps_previous_tasks() -> None:
    folder = ROOT / "som-core/data/service-regression-v4"
    previous = ROOT / "som-core/data/service-stress-v3"
    current = DATA
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["previous_sha256"] == digest(previous / "heldout.jsonl")
    assert manifest["new_sha256"] == digest(current / "heldout.jsonl")
    for filename, key in (
        ("heldout.jsonl", "heldout_sha256"),
        ("heldout.expected.jsonl", "expected_sha256"),
    ):
        assert digest(folder / filename) == manifest[key]
        assert (folder / filename).read_bytes() == (
            (previous / filename).read_bytes() + (current / filename).read_bytes()
        )
    assert manifest["count"] == 100
