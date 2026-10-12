"""Freeze reusable standalone CRUD service tasks with disjoint operation orders."""

from __future__ import annotations

import hashlib
import itertools
import json
import random
from copy import deepcopy
from pathlib import Path

from som_core.v1 import (
    LOGIC_STEPS_V3,
    TEST_CASE_V3,
    V1Error,
    assemble,
    load_blocks,
    resolve_prompt_refs,
    validate_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ("create", "get", "list", "patch", "delete")
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
VARIANTS = [
    (
        "Todo",
        "todos",
        "completed",
        [
            {"name": "title", "type": "str", "required": True, "unique": False},
            {
                "name": "description",
                "type": "str",
                "required": False,
                "unique": False,
                "default": None,
            },
            {
                "name": "completed",
                "type": "bool",
                "required": False,
                "unique": False,
                "default": False,
            },
        ],
    ),
    (
        "Note",
        "notes",
        "archived",
        [
            {"name": "name", "type": "str", "required": True, "unique": False},
            {
                "name": "body",
                "type": "str",
                "required": False,
                "unique": False,
                "default": None,
            },
            {
                "name": "archived",
                "type": "bool",
                "required": False,
                "unique": False,
                "default": False,
            },
        ],
    ),
    (
        "Ticket",
        "tickets",
        "resolved",
        [
            {"name": "code", "type": "str", "required": True, "unique": True},
            {"name": "count", "type": "int", "required": True, "unique": False},
            {
                "name": "resolved",
                "type": "bool",
                "required": False,
                "unique": False,
                "default": False,
            },
        ],
    ),
    (
        "Job",
        "jobs",
        "enabled",
        [
            {"name": "label", "type": "str", "required": True, "unique": False},
            {
                "name": "score",
                "type": "float",
                "required": False,
                "unique": False,
                "default": None,
            },
            {
                "name": "enabled",
                "type": "bool",
                "required": False,
                "unique": False,
                "default": False,
            },
        ],
    ),
]


def entry(
    local_name: str, block_id: str, refs: list[str] | None = None, **params: str
) -> dict:
    return {
        "name": local_name,
        "id": block_id,
        "params": {key: {"$ref": REFS[value]} for key, value in params.items()},
        "refs": refs or [],
    }


def document() -> dict:
    return {
        "version": "som.dsl.v2",
        "files": [
            {
                "path": "{package}/models.py",
                "blocks": [
                    entry("base", "orm.base"),
                    entry(
                        "model",
                        "orm.model.service",
                        ["base"],
                        name="name",
                        table="table",
                        filter_field="filter_field",
                        fields="fields",
                    ),
                ],
            },
            {
                "path": "{package}/schemas.py",
                "blocks": [
                    entry("schemas", "schema.service", name="name", fields="fields"),
                ],
            },
            {
                "path": "{package}/api.py",
                "blocks": [
                    entry("head", "api.head"),
                    entry("create", "api.create", ["head"], name="name", path="path"),
                    entry("get", "api.get", ["head"], name="name", path="path"),
                    entry(
                        "list",
                        "api.list.boolean",
                        ["head"],
                        name="name",
                        path="path",
                        filter_field="filter_field",
                        max_limit="max_limit",
                    ),
                    entry("patch", "api.patch", ["head"], name="name", path="path"),
                    entry("delete", "api.delete", ["head"], name="name", path="path"),
                    entry("tail", "api.tail", ["head"]),
                ],
            },
            {
                "path": "tests/test_{package}.py",
                "blocks": [
                    entry(
                        "test",
                        "test.service",
                        package="package",
                        path="path",
                        filter_field="filter_field",
                        max_limit="max_limit",
                        fields="fields",
                    ),
                ],
            },
            {
                "path": "alembic/versions/{migration.revision}_{package}.py",
                "blocks": [
                    entry(
                        "migration",
                        "migration.service",
                        revision="revision",
                        down_revision="down_revision",
                        table="table",
                        filter_field="filter_field",
                        fields="fields",
                    ),
                ],
            },
            {"path": "{package}/app.py", "blocks": [entry("app", "service.app")]},
            {
                "path": "{package}/database.py",
                "blocks": [entry("database", "service.database")],
            },
            {
                "path": "{package}/__init__.py",
                "blocks": [entry("init", "service.init")],
            },
            {
                "path": "alembic/env.py",
                "blocks": [entry("env", "service.alembic.env", package="package")],
            },
            {"path": "alembic.ini", "blocks": [entry("config", "service.alembic.ini")]},
            {
                "path": "pyproject.toml",
                "blocks": [entry("project", "service.project", package="package")],
            },
            {
                "path": "README.md",
                "blocks": [entry("readme", "service.readme", package="package")],
            },
        ],
    }


def case(order: tuple[str, ...], index: int, variant: int) -> dict:
    name, package, filter_field, fields = VARIANTS[variant]
    revision = f"service{index:04d}"
    prompt = {
        "version": "som.prompt.v3",
        "package": package,
        "resource": {
            "name": name,
            "table": package,
            "path": f"/{package}",
            "fields": deepcopy(fields),
        },
        "logic": [LOGIC_STEPS_V3[OPERATIONS.index(op)] for op in order],
        "tests": [TEST_CASE_V3],
        "migration": {"revision": revision, "down_revision": None},
        "query": {
            "filter_field": filter_field,
            "max_limit": (20, 30, 50, 100)[variant],
        },
    }
    dsl = document()
    api = dsl["files"][2]["blocks"]
    by_name = {item["name"]: item for item in api}
    dsl["files"][2]["blocks"] = [
        by_name["head"],
        *(by_name[op] for op in order),
        by_name["tail"],
    ]
    return {"prompt": prompt, "dsl": dsl}


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        )
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    orders = list(itertools.permutations(OPERATIONS))
    random.Random(43).shuffle(orders)
    train_orders, heldout_orders = orders[:70], orders[70:120]
    assert not set(train_orders) & set(heldout_orders)
    train = [
        case(order, index * 4 + variant, variant)
        for index, order in enumerate(train_orders)
        for variant in range(4)
    ]
    heldout = [
        case(order, 1000 + index, index % 4)
        for index, order in enumerate(heldout_orders)
    ]
    # The first held-out task is the public Todo example: default 20, max 100.
    # Training keeps its separate Todo limit values and operation orders.
    heldout[0]["prompt"]["query"]["max_limit"] = 100
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/v3")
    for row in (*train, *heldout):
        validate_prompt(row["prompt"])
        assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), blocks)
    output = ROOT / "som-core/data/service-v3"
    output.mkdir(parents=True, exist_ok=True)
    write_rows(output / "train.jsonl", train)
    write_rows(output / "heldout.jsonl", heldout)
    write_rows(
        output / "heldout.expected.jsonl",
        [
            assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), blocks)
            for row in heldout
        ],
    )
    manifest = {
        "train_count": len(train),
        "heldout_count": len(heldout),
        "split_rule": "disjoint five-operation orders, seed 43",
        "train_sha256": digest(output / "train.jsonl"),
        "heldout_sha256": digest(output / "heldout.jsonl"),
        "expected_sha256": digest(output / "heldout.expected.jsonl"),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rejected = []
    changes = (
        lambda row: row["prompt"]["resource"]["fields"][2].update(default=True),
        lambda row: row["prompt"]["query"].update(filter_field="title"),
        lambda row: row["prompt"]["migration"].update(down_revision="old"),
        lambda row: row["prompt"]["logic"].__setitem__(0, "Create anything."),
        lambda row: row["dsl"].update(version="som.dsl.v9"),
        lambda row: row["dsl"]["files"][0].update(path="../escape.py"),
        lambda row: row["dsl"]["files"][0].update(path="unsafe.sh"),
        lambda row: row["dsl"]["files"][0]["blocks"][0].update(source="print(1)"),
        lambda row: row["dsl"]["files"][0]["blocks"][0].update(id="missing.block"),
        lambda row: row["dsl"]["files"][0]["blocks"][0].update(refs=["missing"]),
        lambda row: row["dsl"]["files"].append(deepcopy(row["dsl"]["files"][0])),
        lambda row: row["prompt"]["resource"].update(name="A" * 8000),
    )
    for change in changes:
        row = deepcopy(heldout[0])
        change(row)
        try:
            validate_prompt(row["prompt"])
            assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), blocks)
        except V1Error as error:
            rejected.append(row | {"code": error.code})
        else:
            raise AssertionError("invalid service case was accepted")
    write_rows(output / "rejections.jsonl", rejected)
    manifest["rejections_count"] = len(rejected)
    manifest["rejections_sha256"] = digest(output / "rejections.jsonl")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    combined = ROOT / "som-core/data/crud-query-service-v3"
    combined.mkdir(parents=True, exist_ok=True)
    old = ROOT / "som-core/data/crud-query-v2"
    for name in ("train.jsonl", "heldout.jsonl", "heldout.expected.jsonl"):
        (combined / name).write_bytes(
            (old / name).read_bytes() + (output / name).read_bytes()
        )
    (combined / "manifest.json").write_text(
        json.dumps(
            {
                "train_count": 840,
                "heldout_count": 150,
                "train_sha256": digest(combined / "train.jsonl"),
                "heldout_sha256": digest(combined / "heldout.jsonl"),
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
