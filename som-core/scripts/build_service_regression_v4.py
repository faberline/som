"""Freeze the previous 50 stress tasks with the 50 new date tasks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from som_core.v1 import assemble, load_blocks, resolve_prompt_refs, validate_prompt

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = ROOT / "som-core/data/service-stress-v3"
NEW = ROOT / "som-core/data/date-service-v4"
OUTPUT = ROOT / "som-core/data/service-regression-v4"
BLOCKS = ROOT / "som-code-python/data/blocks/v4"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def main() -> None:
    sources = (PREVIOUS, NEW)
    for source in sources:
        manifest = json.loads((source / "manifest.json").read_text())
        assert sha256(source / "heldout.jsonl") == manifest["heldout_sha256"]
        assert sha256(source / "heldout.expected.jsonl") == manifest["expected_sha256"]
        assert len(rows(source / "heldout.jsonl")) == 50

    cases = [row for source in sources for row in rows(source / "heldout.jsonl")]
    expected = [
        files for source in sources for files in rows(source / "heldout.expected.jsonl")
    ]
    assert len(cases) == len(expected) == 100
    assert [row["prompt"]["version"] for row in cases] == ["som.prompt.v3"] * 50 + [
        "som.prompt.v4"
    ] * 50
    library = load_blocks(BLOCKS)
    for row, files in zip(cases, expected, strict=True):
        validate_prompt(row["prompt"])
        assert (
            assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library) == files
        )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for filename in ("heldout.jsonl", "heldout.expected.jsonl"):
        (OUTPUT / filename).write_bytes(
            b"".join((source / filename).read_bytes() for source in sources)
        )
    manifest = {
        "purpose": "v4 candidate regression: previous 50 v3 stress tasks plus 50 new v4 date tasks",
        "count": 100,
        "previous_count": 50,
        "new_count": 50,
        "previous_sha256": sha256(PREVIOUS / "heldout.jsonl"),
        "new_sha256": sha256(NEW / "heldout.jsonl"),
        "heldout_sha256": sha256(OUTPUT / "heldout.jsonl"),
        "expected_sha256": sha256(OUTPUT / "heldout.expected.jsonl"),
        "blocks_sha256": sha256(BLOCKS / "blocks.json"),
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
