"""Keep the untrained v3 stress set distinct from earlier data."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STRESS = ROOT / "som-core/data/service-stress-v3"
OLD = ROOT / "som-core/data/crud-query-service-v3"


def test_frozen_structural_stress_set() -> None:
    manifest = json.loads((STRESS / "manifest.json").read_text())
    cases = [
        json.loads(line) for line in (STRESS / "heldout.jsonl").read_text().splitlines()
    ]
    expected = (STRESS / "heldout.expected.jsonl").read_text().splitlines()
    assert len(cases) == len(expected) == manifest["count"] == 50
    assert Counter(len(row["prompt"]["resource"]["fields"]) for row in cases) == {
        2: 10,
        3: 10,
        4: 10,
        5: 10,
        6: 10,
    }
    previous = {
        json.dumps(json.loads(line)["prompt"], sort_keys=True)
        for name in ("train.jsonl", "heldout.jsonl")
        for line in (OLD / name).read_text().splitlines()
    }
    assert all(
        json.dumps(row["prompt"], sort_keys=True) not in previous for row in cases
    )
    for filename, key in (
        ("heldout.jsonl", "heldout_sha256"),
        ("heldout.expected.jsonl", "expected_sha256"),
    ):
        assert (
            hashlib.sha256((STRESS / filename).read_bytes()).hexdigest()
            == manifest[key]
        )
