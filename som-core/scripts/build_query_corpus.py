"""Freeze query and pagination tasks with disjoint train and held-out orders."""

from __future__ import annotations

import hashlib
import itertools
import json
import random
from copy import deepcopy
from pathlib import Path

from build_v1_corpus import case
from som_core.v1 import (
    LOGIC_STEPS_V2,
    TEST_CASE_V2,
    assemble,
    load_blocks,
    resolve_prompt_refs,
    validate_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
FIELDS = [
    [{"name": "title", "type": "str", "required": True, "unique": False}],
    [
        {"name": "name", "type": "str", "required": True, "unique": False},
        {"name": "active", "type": "bool", "required": False, "unique": False},
    ],
    [
        {"name": "code", "type": "str", "required": True, "unique": False},
        {"name": "count", "type": "int", "required": True, "unique": False},
    ],
    [
        {"name": "label", "type": "str", "required": True, "unique": False},
        {"name": "score", "type": "float", "required": False, "unique": False},
    ],
]
REFS = {
    "name": "resource.name",
    "table": "resource.table",
    "fields": "resource.fields",
    "path": "resource.path",
    "package": "package",
    "revision": "migration.revision",
    "down_revision": "migration.down_revision",
    "filter_field": "query.filter_field",
    "max_limit": "query.max_limit",
}


def query_case(order: tuple[str, ...], index: int) -> dict:
    row = case(order, index)
    prompt = row["prompt"]
    dsl = row["dsl"]
    fields = deepcopy(FIELDS[index % len(FIELDS)])
    prompt["version"] = "som.prompt.v2"
    prompt["resource"]["fields"] = fields
    prompt["logic"] = [
        LOGIC_STEPS_V2[("create", "get", "list", "patch", "delete").index(op)]
        for op in order
    ]
    prompt["tests"] = [TEST_CASE_V2]
    prompt["query"] = {
        "filter_field": fields[0]["name"],
        "max_limit": (20, 30, 50, 100)[index % 4],
    }
    api = dsl["files"][2]["blocks"]
    list_block = next(block for block in api if block["name"] == "list")
    list_block["id"] = "api.list.query"
    list_block["params"]["filter_field"] = {"$ref": "query.filter_field"}
    list_block["params"]["max_limit"] = {"$ref": "query.max_limit"}
    dsl["files"][3]["blocks"].append(
        {
            "name": "query",
            "id": "test.list.query",
            "refs": ["crud"],
            "params": {
                "path": {"$ref": "resource.path"},
                "fields": {"$ref": "resource.fields"},
                "filter_field": {"$ref": "query.filter_field"},
                "max_limit": {"$ref": "query.max_limit"},
            },
        }
    )
    for file in dsl["files"]:
        for block in file["blocks"]:
            for key in block["params"]:
                block["params"][key] = {"$ref": REFS[key]}
    return row


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        )
    )


def main() -> None:
    operations = ("create", "get", "list", "patch", "delete")
    orders = list(itertools.permutations(operations))
    random.Random(29).shuffle(orders)
    training, heldout = orders[:70], orders[70:120]
    assert not set(training) & set(heldout)
    train_rows = [
        query_case(order, index * 4 + variant)
        for index, order in enumerate(training)
        for variant in range(4)
    ]
    heldout_rows = [
        query_case(order, 1000 + index) for index, order in enumerate(heldout)
    ]
    library = load_blocks(ROOT / "som-code-python/data/blocks/v1")
    for row in (*train_rows, *heldout_rows):
        validate_prompt(row["prompt"])
        assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
    output = ROOT / "som-core/data/query-v2"
    output.mkdir(parents=True, exist_ok=True)
    write_rows(output / "train.jsonl", train_rows)
    write_rows(output / "heldout.jsonl", heldout_rows)
    expected = [
        assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
        for row in heldout_rows
    ]
    write_rows(output / "heldout.expected.jsonl", expected)
    manifest = {
        "train_count": len(train_rows),
        "heldout_count": len(heldout_rows),
        "split_rule": "disjoint five-operation orders, seed 29",
        "train_sha256": hashlib.sha256(
            (output / "train.jsonl").read_bytes()
        ).hexdigest(),
        "heldout_sha256": hashlib.sha256(
            (output / "heldout.jsonl").read_bytes()
        ).hexdigest(),
        "expected_sha256": hashlib.sha256(
            (output / "heldout.expected.jsonl").read_bytes()
        ).hexdigest(),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    combined = ROOT / "som-core/data/crud-query-v2"
    combined.mkdir(parents=True, exist_ok=True)
    old = ROOT / "som-core/data/v1"
    for name in ("train.jsonl", "heldout.jsonl", "heldout.expected.jsonl"):
        (combined / name).write_bytes(
            (old / name).read_bytes() + (output / name).read_bytes()
        )
    (combined / "manifest.json").write_text(
        json.dumps(
            {
                "train_count": 560,
                "heldout_count": 100,
                "train_sha256": hashlib.sha256(
                    (combined / "train.jsonl").read_bytes()
                ).hexdigest(),
                "heldout_sha256": hashlib.sha256(
                    (combined / "heldout.jsonl").read_bytes()
                ).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
