"""Freeze v4 date-filter tasks before training the replacement model."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from build_service_corpus import digest, write_rows
from som_core.v1 import (
    LOGIC_STEPS_V3,
    LOGIC_STEPS_V4,
    TEST_CASE_V4,
    V1Error,
    assemble,
    load_blocks,
    resolve_prompt_refs,
    validate_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "som-core/data/date-service-v4"
COMBINED = ROOT / "som-core/data/crud-query-date-service-v4"
OLD = ROOT / "som-core/data/crud-query-service-v3"
V3 = ROOT / "som-core/data/service-v3"
STRESS = ROOT / "som-core/data/service-stress-v3"
BLOCKS = ROOT / "som-code-python/data/blocks/v4/blocks.json"
TRAIN_DATES = ("due_on", "expires_on", "review_on", "scheduled_on")
HELDOUT_DATES = ("deadline_on", "ends_on", "target_on", "cutoff_on", "planned_on")
NEW_IDS = {
    "orm.model.service": "orm.model.service.date",
    "schema.service": "schema.service.date",
    "api.list.boolean": "api.list.boolean.date",
    "test.service": "test.service.date",
    "migration.service": "migration.service.date",
    "service.readme": "service.readme.date",
}


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def upgrade(row: dict, date_name: str, index: int) -> dict:
    result = deepcopy(row)
    prompt, dsl = result["prompt"], result["dsl"]
    prompt["version"] = "som.prompt.v4"
    prompt["logic"] = [
        LOGIC_STEPS_V4[LOGIC_STEPS_V3.index(step)] for step in prompt["logic"]
    ]
    prompt["tests"] = [TEST_CASE_V4]
    fields = prompt["resource"]["fields"]
    if date_name in {field["name"] for field in fields}:
        raise ValueError("date field name collides with another field")
    fields.insert(
        index % (len(fields) + 1),
        {
            "name": date_name,
            "type": "date",
            "required": False,
            "unique": False,
            "default": None,
        },
    )
    prompt["query"]["date_field"] = date_name
    for file in dsl["files"]:
        for entry in file["blocks"]:
            if entry["id"] in NEW_IDS:
                entry["id"] = NEW_IDS[entry["id"]]
                if entry["id"] in {
                    "orm.model.service.date",
                    "api.list.boolean.date",
                    "test.service.date",
                    "migration.service.date",
                }:
                    entry["params"]["date_field"] = {"$ref": "query.date_field"}
    return result


def main() -> None:
    v3_train = rows(V3 / "train.jsonl")
    stress = rows(STRESS / "heldout.jsonl")
    assert len(v3_train) == 280 and len(stress) == 50
    train = [
        upgrade(row, TRAIN_DATES[index % len(TRAIN_DATES)], index)
        for index, row in enumerate(v3_train)
    ]
    heldout_sources = [rows(V3 / "heldout.jsonl")[0], *stress[1:]]
    heldout = [
        upgrade(row, HELDOUT_DATES[index % len(HELDOUT_DATES)], index)
        for index, row in enumerate(heldout_sources)
    ]
    library = load_blocks(BLOCKS.parent)
    expected = []
    train_prompts = {json.dumps(row["prompt"], sort_keys=True) for row in train}
    assert len(train_prompts) == len(train)
    for row in train:
        validate_prompt(row["prompt"])
        files = assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
        assert len(files) == 12
    for row in heldout:
        validate_prompt(row["prompt"])
        files = assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
        assert len(files) == 12
        expected.append(files)
    heldout_prompts = {json.dumps(row["prompt"], sort_keys=True) for row in heldout}
    assert len(heldout_prompts) == len(heldout)
    assert not train_prompts & heldout_prompts
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_rows(OUTPUT / "train.jsonl", train)
    write_rows(OUTPUT / "heldout.jsonl", heldout)
    write_rows(OUTPUT / "heldout.expected.jsonl", expected)

    rejected = []
    changes = (
        lambda row: row["prompt"]["query"].pop("date_field"),
        lambda row: row["prompt"]["query"].update(date_field="missing"),
        lambda row: next(
            field
            for field in row["prompt"]["resource"]["fields"]
            if field["type"] != "date"
        ).update(type="date"),
        lambda row: next(
            field
            for field in row["prompt"]["resource"]["fields"]
            if field["type"] == "date"
        ).update(default=False),
        lambda row: next(
            field
            for field in row["prompt"]["resource"]["fields"]
            if field["type"] == "date"
        ).update(unique=True),
        lambda row: row["prompt"]["logic"].__setitem__(0, "List by date."),
        lambda row: row["dsl"]["files"][2]["blocks"][1].update(id="api.missing"),
        lambda row: row["dsl"]["files"][0].update(path="../escape.py"),
        lambda row: row["dsl"]["files"][2]["blocks"][1].update(source="print(1)"),
        lambda row: row["dsl"]["files"][2]["blocks"][1].update(refs=["missing"]),
    )
    for change in changes:
        row = deepcopy(heldout[0])
        change(row)
        try:
            validate_prompt(row["prompt"])
            assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
        except V1Error as error:
            rejected.append(row | {"code": error.code})
        else:
            raise AssertionError("invalid v4 case was accepted")
    write_rows(OUTPUT / "rejections.jsonl", rejected)
    manifest = {
        "version": "som.prompt.v4",
        "train_count": len(train),
        "heldout_count": len(heldout),
        "rejections_count": len(rejected),
        "split_rule": "v3 service training tasks versus frozen v3 stress tasks; one Todo example",
        "train_sha256": digest(OUTPUT / "train.jsonl"),
        "heldout_sha256": digest(OUTPUT / "heldout.jsonl"),
        "expected_sha256": digest(OUTPUT / "heldout.expected.jsonl"),
        "rejections_sha256": digest(OUTPUT / "rejections.jsonl"),
        "blocks_sha256": digest(BLOCKS),
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    COMBINED.mkdir(parents=True, exist_ok=True)
    for name in ("train.jsonl", "heldout.jsonl", "heldout.expected.jsonl"):
        (COMBINED / name).write_bytes(
            (OLD / name).read_bytes() + (OUTPUT / name).read_bytes()
        )
    (COMBINED / "manifest.json").write_text(
        json.dumps(
            {
                "train_count": 1120,
                "heldout_count": 200,
                "train_sha256": digest(COMBINED / "train.jsonl"),
                "heldout_sha256": digest(COMBINED / "heldout.jsonl"),
                "blocks_sha256": digest(BLOCKS),
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
