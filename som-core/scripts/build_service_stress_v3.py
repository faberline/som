"""Freeze new v3 field combinations before running the current model."""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

from build_service_corpus import OPERATIONS, digest, document, write_rows
from som_core.v1 import (
    LOGIC_STEPS_V3,
    TEST_CASE_V3,
    assemble,
    load_blocks,
    resolve_prompt_refs,
    validate_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "som-core/data/service-stress-v3"
RESOURCES = (
    ("Asset", "assets", "active"),
    ("Journal", "journals", "archived"),
    ("Milestone", "milestones", "resolved"),
    ("Ledger", "ledgers", "visible"),
    ("Bundle", "bundles", "ready"),
    ("Plan", "plans", "approved"),
    ("Card", "cards", "published"),
    ("Report", "reports", "closed"),
    ("Entry", "entries", "selected"),
    ("Schedule", "schedules", "enabled"),
)
PRIMARY = (
    ("title", "str"),
    ("quantity", "int"),
    ("ratio", "float"),
    ("verified", "bool"),
)
EXTRAS = (
    ("description", "str", False, False, None),
    ("rank", "int", True, False, None),
    ("score", "float", False, False, None),
    ("reviewed", "bool", False, False, None),
    ("tag", "str", True, True, None),
    ("weight", "float", True, False, None),
    ("level", "int", False, False, None),
    ("flagged", "bool", False, False, False),
)
PAGE_LIMITS = (20, 25, 30, 40, 50, 60, 75, 90, 100, 35)


def field(name: str, ty: str, required: bool, unique: bool, default: object) -> dict:
    result = {"name": name, "type": ty, "required": required, "unique": unique}
    if not required:
        result["default"] = default
    return result


def make_case(index: int, order: tuple[str, ...]) -> dict:
    shape, variant = divmod(index, len(RESOURCES))
    name, package, filter_name = RESOURCES[variant]
    primary_name, primary_type = PRIMARY[(index * 3 + shape) % len(PRIMARY)]
    primary = field(
        primary_name,
        primary_type,
        True,
        index % 3 == 0 and primary_type in {"str", "int"},
        None,
    )
    filter_field = field(filter_name, "bool", False, False, False)
    extras = []
    for offset in range(shape):
        spec = EXTRAS[(variant + offset * 3 + shape) % len(EXTRAS)]
        extras.append(field(*spec))
    fields = [primary, filter_field, *extras]
    random.Random(9000 + index).shuffle(fields)
    route = f"/api/{package}" if variant % 2 else f"/{package}"
    prompt = {
        "version": "som.prompt.v3",
        "package": package,
        "resource": {
            "name": name,
            "table": package,
            "path": route,
            "fields": fields,
        },
        "logic": [LOGIC_STEPS_V3[OPERATIONS.index(op)] for op in order],
        "tests": [TEST_CASE_V3],
        "migration": {"revision": f"stress{2000 + index}", "down_revision": None},
        "query": {"filter_field": filter_name, "max_limit": PAGE_LIMITS[variant]},
    }
    dsl = document()
    api = dsl["files"][2]["blocks"]
    by_name = {block["name"]: block for block in api}
    dsl["files"][2]["blocks"] = [
        by_name["head"],
        *(by_name[op] for op in order),
        by_name["tail"],
    ]
    return {"prompt": prompt, "dsl": dsl}


def main() -> None:
    orders = list(itertools.permutations(OPERATIONS))
    random.Random(9031).shuffle(orders)
    rows = [make_case(index, orders[index]) for index in range(50)]
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/v3")
    old_train = ROOT / "som-core/data/crud-query-service-v3/train.jsonl"
    old_heldout = ROOT / "som-core/data/crud-query-service-v3/heldout.jsonl"
    old_prompts = {
        json.dumps(json.loads(line)["prompt"], sort_keys=True)
        for path in (old_train, old_heldout)
        for line in path.read_text().splitlines()
    }
    expected = []
    signatures = set()
    for row in rows:
        prompt = row["prompt"]
        validate_prompt(prompt)
        assert json.dumps(prompt, sort_keys=True) not in old_prompts
        signature = (
            tuple(prompt["logic"]),
            tuple(
                (f["type"], f["required"], f["unique"], f.get("default"))
                for f in prompt["resource"]["fields"]
            ),
        )
        assert signature not in signatures
        signatures.add(signature)
        files = assemble(resolve_prompt_refs(prompt, row["dsl"]), blocks)
        assert len(files) == 12
        expected.append(files)
    assert len(rows) == len(signatures) == 50
    assert {len(row["prompt"]["resource"]["fields"]) for row in rows} == {2, 3, 4, 5, 6}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_rows(OUTPUT / "heldout.jsonl", rows)
    write_rows(OUTPUT / "heldout.expected.jsonl", expected)
    manifest = {
        "version": "som.prompt.v3",
        "purpose": "untrained structural stress evaluation",
        "count": 50,
        "seed": 9031,
        "field_counts": [2, 3, 4, 5, 6],
        "resource_count": len(RESOURCES),
        "training_overlap": 0,
        "training_sha256": digest(old_train),
        "prior_heldout_sha256": digest(old_heldout),
        "blocks_sha256": digest(ROOT / "som-code-python/data/blocks/v3/blocks.json"),
        "heldout_sha256": digest(OUTPUT / "heldout.jsonl"),
        "expected_sha256": digest(OUTPUT / "heldout.expected.jsonl"),
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
