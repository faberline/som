"""The strict SOM v1 contract and Python reference renderer."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
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
BLOCKS = ROOT / "som-code-python" / "data" / "blocks" / "v1"
FIELDS = [
    {"name": "title", "type": "str", "required": True, "unique": True},
    {"name": "active", "type": "bool", "required": False, "unique": False},
]


def entry(
    block_name: str, block_id: str, refs: list[str] | None = None, **params: object
) -> dict:
    return {"name": block_name, "id": block_id, "params": params, "refs": refs or []}


def dsl() -> dict:
    model = "Item"
    path = "/items"
    return {
        "version": "som.dsl.v1",
        "files": [
            {
                "path": "items/models.py",
                "blocks": [
                    entry("base", "orm.base"),
                    entry(
                        "model",
                        "orm.model",
                        ["base"],
                        name=model,
                        table="items",
                        fields=FIELDS,
                    ),
                ],
            },
            {
                "path": "items/schemas.py",
                "blocks": [
                    entry("create", "schema.create", name=model, fields=FIELDS),
                    entry("read", "schema.read", name=model, fields=FIELDS),
                    entry("patch", "schema.patch", name=model, fields=FIELDS),
                ],
            },
            {
                "path": "items/api.py",
                "blocks": [
                    entry("head", "api.head"),
                    entry("create", "api.create", ["head"], name=model, path=path),
                    entry("get", "api.get", ["head"], name=model, path=path),
                    entry("list", "api.list", ["head"], name=model, path=path),
                    entry("patch", "api.patch", ["head"], name=model, path=path),
                    entry("delete", "api.delete", ["head"], name=model, path=path),
                    entry("tail", "api.tail", ["head"]),
                ],
            },
            {
                "path": "tests/test_items.py",
                "blocks": [
                    entry(
                        "crud", "test.crud", package="items", path=path, fields=FIELDS
                    ),
                ],
            },
            {
                "path": "alembic/versions/rev1_items.py",
                "blocks": [
                    entry(
                        "migration",
                        "migration.table",
                        revision="rev1",
                        down_revision=None,
                        table="items",
                        fields=FIELDS,
                    ),
                ],
            },
        ],
    }


def test_full_crud_bundle_has_only_parseable_python() -> None:
    files = assemble(dsl(), load_blocks(BLOCKS))
    assert len(files) == 5
    for text in files.values():
        ast.parse(text)
    assert "nullable=False" in files["items/models.py"]
    assert 'revision = "rev1"' in files["alembic/versions/rev1_items.py"]


def test_unknown_block_and_raw_source_are_refused() -> None:
    document = dsl()
    document["files"][0]["blocks"][0]["id"] = "unknown"
    with pytest.raises(V1Error) as error:
        assemble(document, load_blocks(BLOCKS))
    assert error.value.code == "UNSUPPORTED_BLOCK"
    document = dsl()
    document["files"][0]["blocks"][0]["source"] = "print('injected')"
    with pytest.raises(V1Error) as error:
        assemble(document, load_blocks(BLOCKS))
    assert error.value.code == "INVALID_SCHEMA"


def test_code_cannot_hide_inside_identifier() -> None:
    document = dsl()
    document["files"][0]["blocks"][1]["params"]["name"] = "Item; __import__('os')"
    with pytest.raises(V1Error) as error:
        assemble(document, load_blocks(BLOCKS))
    assert error.value.code == "INVALID_VALUE"


def test_prompt_requires_explicit_logic_and_tests() -> None:
    prompt = {
        "version": "som.prompt.v1",
        "package": "items",
        "resource": {
            "name": "Item",
            "table": "items",
            "path": "/items",
            "fields": FIELDS,
        },
        "logic": list(LOGIC_STEPS),
        "tests": [TEST_CASE],
        "migration": {"revision": "rev1", "down_revision": None},
    }
    assert validate_prompt(prompt) == prompt
    document = dsl()
    document["files"][0]["path"] = "{package}/models.py"
    document["files"][0]["blocks"][1]["params"]["name"] = {"$ref": "resource.name"}
    resolved = resolve_prompt_refs(prompt, document)
    assert resolved["files"][0]["path"] == "items/models.py"
    assert resolved["files"][0]["blocks"][1]["params"]["name"] == "Item"
    prompt["logic"] = []
    with pytest.raises(V1Error):
        validate_prompt(prompt)


def test_unsupported_prompt_wording_is_rejected() -> None:
    prompt = {
        "version": "som.prompt.v1",
        "package": "items",
        "resource": {
            "name": "Item",
            "table": "items",
            "path": "/items",
            "fields": FIELDS,
        },
        "logic": list(LOGIC_STEPS),
        "tests": [TEST_CASE],
        "migration": {"revision": "rev1", "down_revision": None},
    }
    prompt["logic"][0] = "Create an item."
    with pytest.raises(V1Error) as error:
        validate_prompt(prompt)
    assert error.value.code == "UNSUPPORTED_LOGIC"
    prompt["logic"] = list(LOGIC_STEPS)
    prompt["tests"] = ["Check the API."]
    with pytest.raises(V1Error) as error:
        validate_prompt(prompt)
    assert error.value.code == "UNSUPPORTED_TEST"


def test_query_prompt_and_assembled_code() -> None:
    import json

    row = json.loads(
        (ROOT / "som-core/data/query-v2/heldout.jsonl").read_text().splitlines()[0]
    )
    prompt = row["prompt"]
    assert validate_prompt(prompt) == prompt
    files = assemble(resolve_prompt_refs(prompt, row["dsl"]), load_blocks(BLOCKS))
    for source in files.values():
        ast.parse(source)
    api = files[f"{prompt['package']}/api.py"]
    assert ".where(" in api
    assert ".limit(limit).offset(offset)" in api
    prompt["query"]["filter_field"] = "unknown"
    with pytest.raises(V1Error) as error:
        validate_prompt(prompt)
    assert error.value.code == "INVALID_VALUE"
    prompt["query"]["filter_field"] = row["prompt"]["resource"]["fields"][0]["name"]
    prompt["query"]["max_limit"] = 101
    with pytest.raises(V1Error) as error:
        validate_prompt(prompt)
    assert error.value.code == "INVALID_VALUE"
