"""Freeze distinct CRUD block orders for training and held-out evaluation."""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import sys
from copy import deepcopy
from pathlib import Path

from som_core.v1 import (
    LOGIC_STEPS,
    TEST_CASE,
    V1Error,
    assemble,
    load_blocks,
    resolve_prompt_refs,
    validate_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "som-core" / "tests"))
from test_v1 import dsl

OPERATION_TEXT = dict(
    zip(("create", "get", "list", "patch", "delete"), LOGIC_STEPS, strict=True)
)
RESOURCES = [("Item", "items"), ("Note", "notes"), ("Task", "tasks"), ("Job", "jobs")]
FIELD_SETS = [
    [{"name": "title", "type": "str", "required": True, "unique": True}],
    [
        {"name": "name", "type": "str", "required": True, "unique": False},
        {"name": "active", "type": "bool", "required": False, "unique": False},
    ],
    [
        {"name": "code", "type": "str", "required": True, "unique": True},
        {"name": "count", "type": "int", "required": True, "unique": False},
    ],
    [
        {"name": "score", "type": "float", "required": False, "unique": False},
        {"name": "enabled", "type": "bool", "required": True, "unique": False},
    ],
]


def case(order: tuple[str, ...], index: int) -> dict:
    name, package = RESOURCES[index % len(RESOURCES)]
    fields = deepcopy(FIELD_SETS[index % len(FIELD_SETS)])
    revision = f"rev{index:04d}"
    document = dsl()
    document["files"][0]["path"] = f"{package}/models.py"
    document["files"][1]["path"] = f"{package}/schemas.py"
    document["files"][2]["path"] = f"{package}/api.py"
    document["files"][3]["path"] = f"tests/test_{package}.py"
    document["files"][4]["path"] = f"alembic/versions/{revision}_{package}.py"
    for file in document["files"]:
        for block in file["blocks"]:
            params = block["params"]
            if "name" in params:
                params["name"] = name
            if "fields" in params:
                params["fields"] = fields
            if "table" in params:
                params["table"] = package
            if "path" in params:
                params["path"] = f"/{package}"
            if "package" in params:
                params["package"] = package
            if "revision" in params:
                params["revision"] = revision
    api = document["files"][2]["blocks"]
    by_name = {block["name"]: block for block in api}
    document["files"][2]["blocks"] = [
        by_name["head"],
        *(by_name[op] for op in order),
        by_name["tail"],
    ]
    prompt = {
        "version": "som.prompt.v1",
        "package": package,
        "resource": {
            "name": name,
            "table": package,
            "path": f"/{package}",
            "fields": fields,
        },
        "logic": [OPERATION_TEXT[op] for op in order],
        "tests": [TEST_CASE],
        "migration": {"revision": revision, "down_revision": None},
    }
    document["files"][0]["path"] = "{package}/models.py"
    document["files"][1]["path"] = "{package}/schemas.py"
    document["files"][2]["path"] = "{package}/api.py"
    document["files"][3]["path"] = "tests/test_{package}.py"
    document["files"][4]["path"] = "alembic/versions/{migration.revision}_{package}.py"
    param_refs = {
        "name": "resource.name",
        "table": "resource.table",
        "fields": "resource.fields",
        "path": "resource.path",
        "package": "package",
        "revision": "migration.revision",
        "down_revision": "migration.down_revision",
    }
    for file in document["files"]:
        for block in file["blocks"]:
            for key in block["params"]:
                block["params"][key] = {"$ref": param_refs[key]}
    return {"prompt": prompt, "dsl": document}


def main() -> None:
    orders = list(itertools.permutations(OPERATION_TEXT))
    random.Random(17).shuffle(orders)
    train_orders, holdout_orders = orders[:70], orders[70:120]
    assert len(set(train_orders) & set(holdout_orders)) == 0
    library = load_blocks(ROOT / "som-code-python" / "data" / "blocks" / "v1")
    output = ROOT / "som-core" / "data" / "v1"
    output.mkdir(parents=True, exist_ok=True)
    heldout_rows = [
        case(order, 1000 + index) for index, order in enumerate(holdout_orders)
    ]
    for name, rows in (
        (
            "train",
            [
                case(order, index * 4 + variant)
                for index, order in enumerate(train_orders)
                for variant in range(4)
            ],
        ),
        ("heldout", heldout_rows),
    ):
        for row in rows:
            validate_prompt(row["prompt"])
            assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
        path = output / f"{name}.jsonl"
        path.write_text(
            "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
    expected = output / "heldout.expected.jsonl"
    expected.write_text(
        "".join(
            json.dumps(
                assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in heldout_rows
        ),
        encoding="utf-8",
    )
    baseline = heldout_rows[0]["dsl"]
    rejected = []
    for change in (
        lambda d: d.update(version="som.dsl.v2"),
        lambda d: d["files"][0]["blocks"][0].update(source="print('x')"),
        lambda d: d["files"][0]["blocks"][0].update(id="missing.block"),
        lambda d: d["files"][0].update(path="../escape.py"),
        lambda d: d["files"][0].update(path="{unknown}/models.py"),
        lambda d: d["files"][0]["blocks"][0].update(refs=["missing"]),
        lambda d: d["files"][0]["blocks"][1]["params"].update(name={"$ref": "unknown"}),
        lambda d: d["files"][0]["blocks"][1]["params"].update(name="bad;name"),
        lambda d: d["files"][0]["blocks"][1]["params"].update(extra="raw"),
        lambda d: d["files"][0]["blocks"][1]["params"].update(
            fields=[{"bad": "field"}]
        ),
        lambda d: d.update(
            files=[{"path": f"x{i}.py", "blocks": []} for i in range(9)]
        ),
        lambda d: d["files"][0].update(
            blocks=[deepcopy(d["files"][0]["blocks"][0]) for _ in range(65)]
        ),
    ):
        document = deepcopy(baseline)
        change(document)
        try:
            assemble(resolve_prompt_refs(heldout_rows[0]["prompt"], document), library)
        except V1Error as error:
            rejected.append(
                {
                    "prompt": heldout_rows[0]["prompt"],
                    "dsl": document,
                    "code": error.code,
                }
            )
        else:
            raise AssertionError("invalid case was accepted")
    rejection_path = output / "rejections.jsonl"
    rejection_path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rejected
        ),
        encoding="utf-8",
    )
    manifest = {
        "train_count": 280,
        "heldout_count": 50,
        "split_rule": (
            "disjoint permutations of the five CRUD operation blocks, seed 17"
        ),
        "train_sha256": hashlib.sha256(
            (output / "train.jsonl").read_bytes()
        ).hexdigest(),
        "heldout_sha256": hashlib.sha256(
            (output / "heldout.jsonl").read_bytes()
        ).hexdigest(),
        "heldout_expected_sha256": hashlib.sha256(expected.read_bytes()).hexdigest(),
        "rejections_count": len(rejected),
        "rejections_sha256": hashlib.sha256(rejection_path.read_bytes()).hexdigest(),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
